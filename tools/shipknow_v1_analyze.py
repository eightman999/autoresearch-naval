#!/usr/bin/env python3
"""shipknow v1 (RAG) の分析: v0 / v1 / ベースラインの比較表 + 近傍のコピー率・訂正率。

  .venv/bin/python tools/shipknow_v1_analyze.py --v1 docs/shipknow_v1/test_preds.jsonl.gz \\
      --v0 docs/shipknow_v0/pilot/test_preds.jsonl.gz --out docs/shipknow_v1/analysis_full.json --md docs/shipknow_v1/analysis_full.md

  * 比較表と T1 variant / 混同、T2 年代別、T3 項目別は tools/shipknow_pilot_analyze.py と同じ切り口。
  * コピー率・訂正率（近傍 = プロンプトに出した meta.nbr の k 艦。近傍の「答え」は下記）:
      T1  近傍の答え = 近傍 3 の多数決（同数は近い方）。
          コピー率: モデルの艦種 == 最近傍の艦種 / == 多数決 / いずれかの近傍の艦種。
          訂正率:   多数決が誤りの例でのモデル正解率（gain = モデル正 & 多数決誤、loss = モデル誤 & 多数決正）、
                    正解が近傍に 1 つも無い例でのモデル正解率、多数決が正しい例での保持率。
      T2  近傍の答え = 最近傍の武装リスト（sensors は最近傍のセンサー集合）。
          コピー率: 武装リストが最近傍と完全一致 / 3 近傍のいずれかと完全一致 / 出力アイテムのうち最近傍にあるものの割合。
          訂正率:   最近傍の weapon F1 <= 0.5 の例でモデル F1 > 0.5 の割合（保持率: 最近傍 F1 > 0.5 の例でモデル F1 > 0.5）。
      T3  近傍の答え = 近傍 3 の中央値（項目ごと）。
          コピー率: 出力値が最近傍の値と一致 / いずれかの近傍の値と一致 / 近傍中央値と 2% 以内。
          訂正率:   近傍中央値の相対誤差 > 10% の項目でモデルが ±10% 以内の割合（モデル誤差 < 近傍誤差の割合）。保持率: 近傍が ±10% 以内の項目でモデルも ±10% 以内。
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402
import shipknow_pilot_analyze as PA  # noqa: E402
import shipknow_retriever as R  # noqa: E402
import shipknow_score as S  # noqa: E402


def rate(xs: list[bool]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def valid_obj(task: str, r: dict[str, Any], pr: dict[str, Any] | None, labels: set[str]) -> Any | None:
    if pr is None:
        return None
    obj = S.parse_output(pr)
    if obj is None or S.validate(task, obj, r, labels):
        return None
    return obj


def t1_copy(rows: list[dict[str, Any]], preds: dict[str, Any], recs: dict[str, Any], labels: set[str]) -> dict[str, Any]:
    n = 0
    c_top1, c_maj, c_any = [], [], []
    acc, maj_acc, top1_acc, oracle = [], [], [], []
    wrong_m, wrong_copy, right_m = [], [], []
    miss_m, gain, loss = [], 0, 0
    fine_any = []
    for r in rows:
        obj = valid_obj("T1", r, preds.get(r["id"]), labels)
        nb = [recs[i] for i in r["meta"]["nbr"]]
        types = [x["ship_type"] for x in nb]
        cnt = collections.Counter(types)
        best = max(cnt.values())
        maj = next(t for t in types if cnt[t] == best)
        g = r["gold"]["ship_type"]
        pred = obj["ship_type"] if obj else None
        n += 1
        acc.append(pred == g)
        maj_acc.append(maj == g)
        top1_acc.append(types[0] == g)
        oracle.append(g in types)
        c_top1.append(pred == types[0])
        c_maj.append(pred == maj)
        c_any.append(pred in types)
        fine_any.append(bool(obj) and obj.get("ship_type_fine") in {x["ship_type_fine"] for x in nb})
        if maj != g:
            wrong_m.append(pred == g)
            wrong_copy.append(pred == maj)
            gain += pred == g
        else:
            right_m.append(pred == g)
            loss += pred != g
        if g not in types:
            miss_m.append(pred == g)
    return {
        "n": n, "model_acc": rate(acc), "nbr_majority_acc": rate(maj_acc), "nbr_top1_acc": rate(top1_acc), "gold_in_neighbors_rate": rate(oracle),
        "copy_rate_top1": rate(c_top1), "copy_rate_majority": rate(c_maj), "copy_rate_any_neighbor": rate(c_any), "fine_in_any_neighbor_rate": rate(fine_any),
        "when_majority_wrong": {"n": len(wrong_m), "model_acc": rate(wrong_m), "model_copies_the_wrong_majority": rate(wrong_copy), "gain_n": gain},
        "when_majority_right": {"n": len(right_m), "model_acc": rate(right_m), "loss_n": loss},
        "when_gold_not_in_any_neighbor": {"n": len(miss_m), "model_acc": rate(miss_m)},
    }


def t2_copy(rows: list[dict[str, Any]], preds: dict[str, Any], recs: dict[str, Any], labels: set[str]) -> dict[str, Any]:
    ex_top1, ex_any, item_in_top1, sens_top1 = [], [], [], []
    nbr_wrong, nbr_right = [], []  # model f1 > 0.5
    nbr_f1s, mod_f1s = [], []
    mw_f1, mr_f1 = [], []
    better = equal = worse = 0
    for r in rows:
        obj = valid_obj("T2", r, preds.get(r["id"]), labels)
        nb = [recs[i] for i in r["meta"]["nbr"]]
        comp = [B.compact_weapons(x["weapons"]) or [] for x in nb]
        gw = r["gold"]["weapons"]
        pw = obj["weapons"] if obj else []
        nf = S.prf(*S.match_items(gw, comp[0]))[2] if (gw or comp[0]) else 1.0
        mf = S.prf(*S.match_items(gw, pw))[2] if (gw or pw) else 1.0
        if not gw and not pw and obj:
            mf = 1.0
        nbr_f1s.append(nf)
        mod_f1s.append(mf)
        better += mf > nf + 1e-9
        equal += abs(mf - nf) <= 1e-9
        worse += mf < nf - 1e-9
        ex_top1.append(bool(obj) and R.jdump(pw) == R.jdump(comp[0]))
        ex_any.append(bool(obj) and any(R.jdump(pw) == R.jdump(c) for c in comp))
        if pw:
            tp, fp, fn = S.match_items(comp[0], pw)
            item_in_top1.append(tp / len(pw))
        if "sensors" in r["gold"]:
            sens_top1.append(bool(obj) and set(obj.get("sensors", [])) == set(nb[0].get("sensor_kinds") or []))
        if nf <= 0.5:
            nbr_wrong.append(mf > 0.5)
            mw_f1.append((nf, mf))
        else:
            nbr_right.append(mf > 0.5)
            mr_f1.append((nf, mf))
    return {
        "n": len(rows), "model_weapon_f1_example_mean": statistics.mean(mod_f1s), "nbr_top1_weapon_f1_example_mean": statistics.mean(nbr_f1s),
        "copy_exact_top1": rate(ex_top1), "copy_exact_any_of_3": rate(ex_any), "pred_items_found_in_top1_rate": statistics.mean(item_in_top1) if item_in_top1 else None,
        "sensors_equal_top1_rate": rate(sens_top1),
        "model_vs_top1_f1": {"better": better, "equal": equal, "worse": worse},
        "when_top1_f1_le_0.5": {"n": len(nbr_wrong), "model_f1_gt_0.5": rate(nbr_wrong), "mean_nbr_f1": statistics.mean(a for a, _ in mw_f1) if mw_f1 else None,
                                "mean_model_f1": statistics.mean(b for _, b in mw_f1) if mw_f1 else None},
        "when_top1_f1_gt_0.5": {"n": len(nbr_right), "model_f1_gt_0.5": rate(nbr_right), "mean_nbr_f1": statistics.mean(a for a, _ in mr_f1) if mr_f1 else None,
                                "mean_model_f1": statistics.mean(b for _, b in mr_f1) if mr_f1 else None},
    }


def t3_copy(rows: list[dict[str, Any]], preds: dict[str, Any], recs: dict[str, Any], labels: set[str]) -> dict[str, Any]:
    c_top1, c_any, c_med2 = [], [], []
    nwrong_m10, nwrong_better, nright_m10 = [], [], []
    nbr_err, mod_err = [], []
    by_field: dict[str, dict[str, list]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        obj = valid_obj("T3", r, preds.get(r["id"]), labels)
        nb = [recs[i] for i in r["meta"]["nbr"]]
        for f, g in r["gold"].items():
            vals = [B.rnd(f, x[f]) for x in nb if x.get(f)]
            if not vals:
                continue
            med = statistics.median(vals)
            ne = abs(med - g) / g
            if not obj or f not in obj:
                continue
            p = obj[f]
            me = abs(p - g) / g
            nbr_err.append(ne)
            mod_err.append(me)
            c_top1.append(abs(p - vals[0]) <= 1e-9 * max(1.0, abs(p)))
            c_any.append(any(abs(p - v) <= 1e-9 * max(1.0, abs(p)) for v in vals))
            c_med2.append(abs(p - med) / med <= 0.02)
            if ne > 0.10:
                nwrong_m10.append(me <= 0.10)
                nwrong_better.append(me < ne)
            else:
                nright_m10.append(me <= 0.10)
            bf = by_field[f]
            bf["nbr"].append(ne)
            bf["mod"].append(me)
            bf["copy_any"].append(c_any[-1])
    return {
        "n_fields": len(mod_err), "model_mape": statistics.mean(mod_err), "nbr_median_mape": statistics.mean(nbr_err),
        "copy_rate_top1_value": rate(c_top1), "copy_rate_any_neighbor_value": rate(c_any), "within_2pct_of_nbr_median_rate": rate(c_med2),
        "when_nbr_median_err_gt_10pct": {"n": len(nwrong_m10), "model_within_10pct": rate(nwrong_m10), "model_err_lt_nbr_err": rate(nwrong_better)},
        "when_nbr_median_err_le_10pct": {"n": len(nright_m10), "model_within_10pct": rate(nright_m10)},
        "per_field": {f: {"n": len(v["mod"]), "model_mape": statistics.mean(v["mod"]), "nbr_median_mape": statistics.mean(v["nbr"]), "copy_any": rate(v["copy_any"])}
                      for f, v in sorted(by_field.items())},
    }


def fmt(x: Any, nd: int = 3) -> str:
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def copy_markdown(name: str, c: dict[str, Any]) -> str:
    L = [f"## {name}: 近傍のコピー率・訂正率", ""]
    t = c["T1"]
    L += ["### T1", "", f"- n={t['n']} / モデル acc {fmt(t['model_acc'])} / 近傍多数決 acc {fmt(t['nbr_majority_acc'])} / 最近傍 acc {fmt(t['nbr_top1_acc'])} / 正解が近傍 3 に含まれる割合 {fmt(t['gold_in_neighbors_rate'])}",
          f"- コピー率: 最近傍と同じ {fmt(t['copy_rate_top1'])} / 多数決と同じ {fmt(t['copy_rate_majority'])} / いずれかの近傍と同じ {fmt(t['copy_rate_any_neighbor'])}（細分類がいずれかの近傍と同じ {fmt(t['fine_in_any_neighbor_rate'])}）",
          f"- 多数決が誤りの例 (n={t['when_majority_wrong']['n']}): モデル正解率 {fmt(t['when_majority_wrong']['model_acc'])}、誤った多数決をコピーした割合 {fmt(t['when_majority_wrong']['model_copies_the_wrong_majority'])}、gain {t['when_majority_wrong']['gain_n']} 件",
          f"- 多数決が正しい例 (n={t['when_majority_right']['n']}): モデル保持率 {fmt(t['when_majority_right']['model_acc'])}、loss {t['when_majority_right']['loss_n']} 件",
          f"- 正解が近傍 3 に 1 つも無い例 (n={t['when_gold_not_in_any_neighbor']['n']}): モデル正解率 {fmt(t['when_gold_not_in_any_neighbor']['model_acc'])}", ""]
    t = c["T2"]
    L += ["### T2", "", f"- n={t['n']} / 例平均 weapon F1: モデル {fmt(t['model_weapon_f1_example_mean'])} / 最近傍 {fmt(t['nbr_top1_weapon_f1_example_mean'])}",
          f"- コピー率: 武装リスト完全一致 最近傍 {fmt(t['copy_exact_top1'])} / 3 近傍のいずれか {fmt(t['copy_exact_any_of_3'])} / 出力アイテムが最近傍にある割合 {fmt(t['pred_items_found_in_top1_rate'])} / センサー集合が最近傍と同じ {fmt(t['sensors_equal_top1_rate'])}",
          f"- モデル vs 最近傍の例別 F1: 上回る {t['model_vs_top1_f1']['better']} / 同じ {t['model_vs_top1_f1']['equal']} / 下回る {t['model_vs_top1_f1']['worse']}",
          f"- 最近傍 F1 <= 0.5 (n={t['when_top1_f1_le_0.5']['n']}): モデル F1 > 0.5 の割合 {fmt(t['when_top1_f1_le_0.5']['model_f1_gt_0.5'])}（平均 F1 近傍 {fmt(t['when_top1_f1_le_0.5']['mean_nbr_f1'])} -> モデル {fmt(t['when_top1_f1_le_0.5']['mean_model_f1'])}）",
          f"- 最近傍 F1 > 0.5 (n={t['when_top1_f1_gt_0.5']['n']}): モデル F1 > 0.5 の割合 {fmt(t['when_top1_f1_gt_0.5']['model_f1_gt_0.5'])}（平均 F1 近傍 {fmt(t['when_top1_f1_gt_0.5']['mean_nbr_f1'])} -> モデル {fmt(t['when_top1_f1_gt_0.5']['mean_model_f1'])}）", ""]
    t = c["T3"]
    L += ["### T3", "", f"- 項目数 {t['n_fields']} / MAPE: モデル {fmt(t['model_mape'])} / 近傍中央値 {fmt(t['nbr_median_mape'])}",
          f"- コピー率: 最近傍の値と同じ {fmt(t['copy_rate_top1_value'])} / いずれかの近傍の値と同じ {fmt(t['copy_rate_any_neighbor_value'])} / 近傍中央値と 2% 以内 {fmt(t['within_2pct_of_nbr_median_rate'])}",
          f"- 近傍中央値の誤差 > 10% の項目 (n={t['when_nbr_median_err_gt_10pct']['n']}): モデルが ±10% 以内 {fmt(t['when_nbr_median_err_gt_10pct']['model_within_10pct'])} / モデル誤差 < 近傍誤差 {fmt(t['when_nbr_median_err_gt_10pct']['model_err_lt_nbr_err'])}",
          f"- 近傍中央値が ±10% 以内の項目 (n={t['when_nbr_median_err_le_10pct']['n']}): モデルも ±10% 以内 {fmt(t['when_nbr_median_err_le_10pct']['model_within_10pct'])}", "",
          "| field | n | モデル MAPE | 近傍中央値 MAPE | いずれかの近傍の値と同じ |", "|---|---|---|---|---|"]
    for f, d in t["per_field"].items():
        L.append(f"| {f} | {d['n']} | {fmt(d['model_mape'])} | {fmt(d['nbr_median_mape'])} | {fmt(d['copy_any'])} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", type=Path, default=R.V1_DIR / "test.jsonl")
    ap.add_argument("--v0-dir", type=Path, default=R.V0_DIR)
    ap.add_argument("--v1", type=Path, required=True)
    ap.add_argument("--v0", type=Path, default=None)
    ap.add_argument("--baselines", type=Path, nargs="*", default=[R.V0_DIR / "baseline_preds", R.V1_DIR / "baseline_preds"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--md", type=Path, default=None)
    args = ap.parse_args()

    test = S.load_test(args.test)
    labels = S.labels_from_manifest(args.test)
    label_set = set(labels)
    recs = {r["id"]: r for r in R.read_jsonl(args.v0_dir / "records.jsonl")}
    runs_in = {"v1_rag": PA.load_preds_any(args.v1)}
    if args.v0:
        runs_in = {"v0_pilot": PA.load_preds_any(args.v0), **runs_in}
    runs: dict[str, dict[str, Any]] = {}
    for name, p in runs_in.items():
        full = S.score(test, p, labels)
        extra = {"T1": PA.t1_extra(test, p, labels), "T2_by_era": PA.t2_by_era(test, p, labels)}
        runs[name] = {"headline": PA.headline(full), "full": full, "extra": extra}
        print("==", name)
        S.print_summary(full)
    baselines: dict[str, dict[str, Any]] = {}
    for d in args.baselines:
        for path in sorted(d.glob("*.jsonl")):
            task = path.name[:2].upper()
            full = S.score(test, S.load_preds(path), labels, tasks=(task,))
            baselines[path.stem] = {"headline": PA.headline(full), "full": full}
    copy = {name: {"T1": t1_copy([r for r in test if r["task"] == "T1"], p, recs, label_set),
                   "T2": t2_copy([r for r in test if r["task"] == "T2"], p, recs, label_set),
                   "T3": t3_copy([r for r in test if r["task"] == "T3"], p, recs, label_set)} for name, p in runs_in.items()}
    out = {"n_test": dict(collections.Counter(r["task"] for r in test)), "runs": runs, "baselines": baselines, "neighbor_copy": copy}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.md:
        md = PA.to_markdown(runs, baselines) + "\n" + "\n".join(copy_markdown(n, c) for n, c in copy.items())
        args.md.write_text(md, encoding="utf-8")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
