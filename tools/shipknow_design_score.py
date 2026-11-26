"""設計タスク D の採点器と、非 LLM ベースライン。

    # 予測の採点（test.jsonl は正解つき、scenarios.jsonl は正解なし = JSON・要求・validator だけ採点）
    .venv/bin/python tools/shipknow_design_score.py score --test data/navspec/shipknow_design_v0/test.jsonl --pred preds.jsonl [--out result.json]
    # ベースライン（近傍だけから作る設計）を予測 JSONL に書き出して同じ採点にかける
    .venv/bin/python tools/shipknow_design_score.py baselines --test data/navspec/shipknow_design_v0/test.jsonl --out-dir <dir> [--score]

予測ファイル（JSONL、1 行 1 例）: {"id": "c00004:D:0", "text": "<モデルの生出力>"} または {"id": ..., "output": {設計 dict}}。
text は ``` や <think> を剥がして最初の JSON オブジェクトを取る（shipknow_score.extract_json）。無い id は JSON 不正として数える。

指標
  json_invalid_rate     欠落 / JSON でない / オブジェクトでない
  schema_invalid_rate   JSON だが validator の schema 検査に落ちる（艦種ラベル・kind・値域・必須キー）
  req_satisfied         要求充足率（排水量上限・最低速力・必須装備・艦種一致・乗員・航続のすべて）と、項目別の充足率
  validator_pass        全検査（schema + 要求 + 物理 + 砲 + 年代 + 艦種）を通った率 / 要求を除いた検査（物理・年代・艦種）だけの合格率
  valid_and_ok          JSON 有効かつ validator 合格（分母は全例）
  closeness（正解がある場合。JSON が有効な例だけ）
    ship_type_acc / ship_type_fine_acc
    数値: 項目別 median 相対誤差 / MAPE / ±10% 以内率（正解と予測の両方にある項目。欠けた項目は missing 率で別に）
    weapons: item micro P/R/F1（shipknow_score.match_items: 同 kind・口径 ±5%）、例ごとの平均 F1、主砲口径一致率
    sensors: 種別集合の F1 / Jaccard、propulsion: 集合の完全一致率
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402
import shipknow_design_common as C  # noqa: E402
import shipknow_design_validator as V  # noqa: E402
import shipknow_score as S  # noqa: E402

NUM_FIELDS = ["displacement_normal_t", "displacement_full_t", "displacement_standard_t", "length_m", "beam_m", "draught_m", "power_hp", "speed_kn", "endurance_nm", "complement"]
ALL_LAYERS = ("schema", "req", "ratio", "gun", "era", "type")
PHYS_LAYERS = ("schema", "ratio", "gun", "era", "type")


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        m = r["messages"]
        r["req"] = json.loads(m[1]["content"])["req"]
        r["nbr"] = json.loads(m[1]["content"]).get("nbr", [])
        r["gold"] = json.loads(m[2]["content"]) if len(m) > 2 else None
        rows.append(r)
    return rows


def load_preds(path: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            p = json.loads(line)
            out[p["id"]] = p
    return out


def _rel(g: float, p: float) -> float:
    return abs(p - g) / abs(g) if g else float("nan")


def _f1(tp: int, fp: int, fn: int) -> float:
    return S.prf(tp, fp, fn)[2]


def score(rows: list[dict[str, Any]], preds: dict[str, dict[str, Any]], thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
    th = thresholds if thresholds is not None else V.load_thresholds()
    n = len(rows)
    n_json_bad = n_schema_bad = 0
    req_ok = full_ok = phys_ok = both_ok = 0
    req_parts: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    viol_codes: collections.Counter = collections.Counter()
    warn_n = 0
    has_gold = all(r.get("gold") for r in rows)
    type_ok = fine_ok = n_cmp = 0
    num_err: dict[str, list[float]] = collections.defaultdict(list)
    num_missing: collections.Counter = collections.Counter()
    num_present_gold: collections.Counter = collections.Counter()
    tp = fp = fn = 0
    ex_f1: list[float] = []
    mc_total = mc_ok = 0
    s_tp = s_fp = s_fn = 0
    s_jac: list[float] = []
    prop_exact = prop_n = 0
    for r in rows:
        pr = preds.get(r["id"])
        obj = S.parse_output(pr) if pr is not None else None
        if not isinstance(obj, dict):
            n_json_bad += 1
            continue
        sch = V._schema(obj)
        if sch:
            n_schema_bad += 1
        req = r["req"]
        res_all = V.validate(req, obj, th, layers=ALL_LAYERS)
        res_req = V.validate(req, obj, th, layers=("req",))
        res_phys = V.validate(None, {**obj, "year": req["year"]}, th, layers=PHYS_LAYERS)
        req_ok += res_req["ok"]
        full_ok += res_all["ok"]
        phys_ok += res_phys["ok"]
        both_ok += res_all["ok"]
        warn_n += bool(res_all["warnings"])
        for x in res_all["violations"]:
            viol_codes[x["code"]] += 1
        for part, pref in (("displacement", "req_displacement"), ("speed", "req_speed"), ("equipment", "req_equipment"), ("type", "req_type"), ("complement", "req_complement"),
                           ("endurance", "req_endurance")):
            applies = {"displacement": "max_displacement_t" in req, "speed": "min_speed_kn" in req, "equipment": bool(req.get("required")), "type": True,
                       "complement": "max_complement" in req, "endurance": "min_endurance_nm" in req}[part]
            if applies:
                req_parts[part][0] += 1
                req_parts[part][1] += not any(x["code"].startswith(pref) for x in res_req["violations"])
        if not has_gold:
            continue
        g = r["gold"]
        n_cmp += 1
        type_ok += obj.get("ship_type") == g.get("ship_type")
        fine_ok += obj.get("ship_type_fine") == g.get("ship_type_fine")
        for f in NUM_FIELDS:
            if f in g:
                num_present_gold[f] += 1
                if S.is_num(obj.get(f)):
                    num_err[f].append(_rel(g[f], obj[f]))
                else:
                    num_missing[f] += 1
        gw = g.get("weapons") or []
        pw = [w for w in (obj.get("weapons") or []) if isinstance(w, dict) and "kind" in w]
        a, b, c = S.match_items(gw, pw)
        tp, fp, fn = tp + a, fp + b, fn + c
        ex_f1.append(1.0 if not gw and not pw else _f1(a, b, c))
        gm = S.main_gun(gw)
        if gm:
            mc_total += 1
            pm = S.main_gun(pw)
            mc_ok += bool(pm and abs(pm[0] - gm[0]) / gm[0] <= S.CAL_TOL)
        gs, ps = set(g.get("sensors") or []), set(x for x in (obj.get("sensors") or []) if isinstance(x, str))
        s_tp, s_fp, s_fn = s_tp + len(gs & ps), s_fp + len(ps - gs), s_fn + len(gs - ps)
        s_jac.append(1.0 if not gs and not ps else len(gs & ps) / len(gs | ps))
        if g.get("propulsion"):
            prop_n += 1
            prop_exact += set(g["propulsion"]) == set(obj.get("propulsion") or [])
    valid_n = n - n_json_bad
    res: dict[str, Any] = {
        "n": n, "json_invalid_rate": round(n_json_bad / n, 4), "schema_invalid_rate": round(n_schema_bad / n, 4),
        "req_satisfied": round(req_ok / n, 4), "validator_pass": round(full_ok / n, 4), "validator_pass_without_requirements": round(phys_ok / n, 4),
        "valid_and_ok": round(both_ok / n, 4), "any_warning_rate_among_valid": round(warn_n / valid_n, 4) if valid_n else None,
        "req_satisfied_by_part": {k: {"n": a, "rate": round(b / a, 4)} for k, (a, b) in req_parts.items()},
        "violation_codes": dict(viol_codes.most_common(12)),
    }
    if has_gold and n_cmp:
        p_, r_, f_ = S.prf(tp, fp, fn)
        res["closeness"] = {
            "n": n_cmp, "ship_type_acc": round(type_ok / n_cmp, 4), "ship_type_fine_acc": round(fine_ok / n_cmp, 4),
            "numeric": {f: {"n": num_present_gold[f], "missing_rate": round(num_missing[f] / num_present_gold[f], 4),
                            "median_rel_err": round(statistics.median(num_err[f]), 4) if num_err[f] else None,
                            "mape": round(statistics.mean(num_err[f]), 4) if num_err[f] else None,
                            "within_10pct": round(sum(e <= 0.10 for e in num_err[f]) / len(num_err[f]), 4) if num_err[f] else None}
                        for f in NUM_FIELDS if num_present_gold[f]},
            "weapons": {"precision": round(p_, 4), "recall": round(r_, 4), "f1": round(f_, 4), "example_mean_f1": round(statistics.mean(ex_f1), 4), "main_caliber_acc": round(mc_ok / mc_total, 4) if mc_total else None},
            "sensors": {"f1": round(_f1(s_tp, s_fp, s_fn), 4), "mean_jaccard": round(statistics.mean(s_jac), 4)},
            "propulsion_exact": round(prop_exact / prop_n, 4) if prop_n else None,
        }
        nums = [x for f in NUM_FIELDS for x in num_err[f]]
        res["closeness"]["numeric_overall"] = {"median_rel_err": round(statistics.median(nums), 4), "mape": round(statistics.mean(nums), 4),
                                               "within_10pct": round(sum(e <= 0.10 for e in nums) / len(nums), 4)}
    return res


def print_summary(name: str, res: dict[str, Any]) -> None:
    c = res.get("closeness", {})
    no = c.get("numeric_overall", {})
    print(f"{name:<28} n={res['n']} json_bad={res['json_invalid_rate']:.3f} schema_bad={res['schema_invalid_rate']:.3f} req_ok={res['req_satisfied']:.3f} "
          f"valid_pass={res['validator_pass']:.3f} (no-req {res['validator_pass_without_requirements']:.3f})"
          + (f" | type={c['ship_type_acc']:.3f} fine={c['ship_type_fine_acc']:.3f} num_medRE={no.get('median_rel_err')} num_±10%={no.get('within_10pct')} "
             f"wF1={c['weapons']['f1']:.3f} sensF1={c['sensors']['f1']:.3f}" if c else ""))


# --------------------------------------------------------------------------------------------------
# non-LLM baselines (use only what the model sees: the request and the neighbour payloads)
# --------------------------------------------------------------------------------------------------
def _med(xs: list[float]) -> float:
    return float(statistics.median(xs))


def _item_key(w: dict[str, Any]) -> tuple[str, float | None]:
    return w["kind"], w.get("cal")


def baseline_top1(row: dict[str, Any], copy_req_type: bool = False) -> dict[str, Any]:
    d = C.payload_to_design(row["nbr"][0]) if row["nbr"] else {}
    if copy_req_type:
        d["ship_type"] = row["req"]["ship_type"]
        if row["req"].get("ship_type_fine"):
            d["ship_type_fine"] = row["req"]["ship_type_fine"]
    return d


def baseline_median_vote(row: dict[str, Any], copy_req_type: bool = False) -> dict[str, Any]:
    """近傍 3 隻: 数値は中央値、艦種・推進・センサー・air・weapons の項目は多数決（過半数の近傍にある項目だけ。n / per は中央値）。"""
    ds = [C.payload_to_design(p) for p in row["nbr"]]
    if not ds:
        return {}
    k = len(ds)
    need = k // 2 + 1
    out: dict[str, Any] = {}
    types = collections.Counter(d.get("ship_type") for d in ds)
    out["ship_type"] = types.most_common(1)[0][0] if types.most_common(1)[0][1] > 1 else ds[0].get("ship_type")
    fines = collections.Counter(d.get("ship_type_fine") for d in ds)
    out["ship_type_fine"] = fines.most_common(1)[0][0] if fines.most_common(1)[0][1] > 1 else ds[0].get("ship_type_fine")
    if copy_req_type:
        out["ship_type"] = row["req"]["ship_type"]
        if row["req"].get("ship_type_fine"):
            out["ship_type_fine"] = row["req"]["ship_type_fine"]
    for f in NUM_FIELDS + ["shafts"]:
        vals = [d[f] for d in ds if S.is_num(d.get(f))]
        if len(vals) >= need:
            out[f] = B.rnd(f, _med(vals)) if f != "shafts" else int(round(_med(vals)))
    props = collections.Counter(p for d in ds for p in set(d.get("propulsion") or []))
    pv = sorted(p for p, c in props.items() if c >= need)
    if pv:
        out["propulsion"] = pv
    arm: dict[str, int] = {}
    for part in ("belt", "deck", "turret"):
        vals = [d["armour"][part] for d in ds if part in (d.get("armour") or {})]
        if len(vals) >= need:
            arm[part] = int(round(_med(vals)))
    if arm:
        out["armour"] = arm
    # weapons: group items across neighbours by (kind, caliber within 5%)
    groups: list[list[dict[str, Any]]] = []
    for d in ds:
        for w in d.get("weapons") or []:
            for g in groups:
                g0 = g[0]
                if g0["kind"] == w["kind"] and ((not g0.get("cal") and not w.get("cal")) or (g0.get("cal") and w.get("cal") and abs(g0["cal"] - w["cal"]) / g0["cal"] <= S.CAL_TOL)):
                    g.append(w)
                    break
            else:
                groups.append([w])
    weapons = []
    for g in groups:
        if len(g) >= need:
            item: dict[str, Any] = {"kind": g[0]["kind"]}
            n_med = int(round(_med([x.get("n", 0) for x in g])))
            if n_med:
                item["n"] = n_med
            per = int(round(_med([x.get("per", 1) for x in g])))
            if per > 1:
                item["per"] = per
            if g[0].get("cal"):
                item["cal"] = g[0]["cal"]
            weapons.append((B.KIND_ORDER.index(item["kind"]), -(item.get("cal") or 0), item))
    weapons.sort(key=lambda t: (t[0], t[1]))
    out["weapons"] = [w for _, _, w in weapons]
    sc = collections.Counter(s for d in ds for s in set(d.get("sensors") or []))
    out["sensors"] = sorted(s for s, c in sc.items() if c >= need)
    air = collections.Counter(a for d in ds for a in set(d.get("air") or []))
    av = sorted(a for a, c in air.items() if c >= need)
    if av:
        out["air"] = av
    return {k_: out[k_] for k_ in C.DESIGN_KEY_ORDER if k_ in out}


def cmd_score(args: argparse.Namespace) -> None:
    rows = load_rows(Path(args.test))
    res = score(rows, load_preds(Path(args.pred)), V.load_thresholds(Path(args.thresholds)) if args.thresholds else None)
    print_summary(Path(args.pred).stem, res)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_baselines(args: argparse.Namespace) -> None:
    rows = load_rows(Path(args.test))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    methods = {
        "top1": lambda r: baseline_top1(r, False), "median_vote": lambda r: baseline_median_vote(r, False),
        "top1_req_type": lambda r: baseline_top1(r, True), "median_vote_req_type": lambda r: baseline_median_vote(r, True),
    }
    results: dict[str, Any] = {}
    th = V.load_thresholds(Path(args.thresholds)) if args.thresholds else None
    for name, fn in methods.items():
        preds = {r["id"]: {"id": r["id"], "output": fn(r)} for r in rows}
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for p in preds.values():
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")
        if args.score:
            results[name] = score(rows, preds, th)
            print_summary(name, results[name])
    if args.score and rows[0].get("gold"):
        gold = {r["id"]: {"id": r["id"], "output": r["gold"]} for r in rows}
        results["gold (sanity)"] = score(rows, gold, th)
        print_summary("gold (sanity)", results["gold (sanity)"])
    if args.score and args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--test", required=True)
    s.add_argument("--pred", required=True)
    s.add_argument("--thresholds", default=None)
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_score)
    b = sub.add_parser("baselines")
    b.add_argument("--test", required=True)
    b.add_argument("--out-dir", required=True)
    b.add_argument("--thresholds", default=None)
    b.add_argument("--score", action="store_true")
    b.add_argument("--out", default=None)
    b.set_defaults(fn=cmd_baselines)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
