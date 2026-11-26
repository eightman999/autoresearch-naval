"""shipknow v1 retriever: 例の「入力に見えている項目だけ」から train 艦級の近傍を引く。

    .venv/bin/python tools/shipknow_retriever.py tune     # valid だけを使って距離の重みを軽く調整 -> retriever_params.json
    .venv/bin/python tools/shipknow_retriever.py demo     # 近傍の表示を数件出す

リーク防止の設計（API 自体で担保）
  * 検索関数は **例の user JSON（view）だけ**を受け取る。レコードの正解（T3 の隠した数値、T2 の武装・センサー、
    T1 の艦種）は引数に存在しないので、距離計算に使えない。
  * 検索対象 (Pool) は train 分割の艦級レコードのみ。exclude_group を渡すと、その艦級グループの艦を候補から除く
    （train 例では自分と同じグループ = leave-group-out。valid/test の艦は Pool に入らない）。
  * 近傍の表示 (neighbor_payload) に艦名・艦級名・URL・レコード id は出さない。

距離 = 重み付き RMS（標準化した数値、両方に値がある項目だけ。candidate が query の可視項目を欠くほど罰則）
       + カテゴリ項（国・艦種〈T2/T3 の入力に艦種がある場合〉・武装 kind の Jaccard〈T1/T3〉・主砲口径・
         センサー Jaccard〈T1〉・推進方式 Jaccard）
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402
import shipknow_score as S  # noqa: E402
import shipknow_types as T  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
V0_DIR = ROOT / "data" / "navspec" / "shipknow_v0"
V1_DIR = ROOT / "data" / "navspec" / "shipknow_v1_rag"
PARAMS_PATH = V1_DIR / "retriever_params.json"

NUM_KEYS = ["logd", "logL", "logB", "logT", "speed", "logP", "year", "logC", "logE", "shafts"]
# v0_2 inputs (weight 0 unless the params file sets them; a weight missing from an older params file counts as 0)
NEW_NUM_KEYS = ["yearN", "logS", "armB", "armD"]
PROPS = ["nuclear", "gas_turbine", "diesel", "petrol_engine", "steam_turbine", "steam_reciprocating", "electric_motor", "sail", "paddle_wheel"]
KINDS = B.KIND_ORDER
SENS = S.SENSOR_KINDS

DEFAULT_PARAMS: dict[str, Any] = {
    "T1": {"w": {"logd": 1.5, "logL": 1.0, "logB": 0.6, "logT": 0.4, "speed": 0.5, "logP": 0.4, "year": 0.8, "logC": 0.2, "logE": 0.2, "shafts": 0.1},
           "pen_missing": 1.0, "country": 0.5, "weapons": 0.8, "cal": 0.3, "sensors": 0.3, "propulsion": 0.2},
    "T2": {"w": {"logd": 1.5, "logL": 1.0, "logB": 0.6, "logT": 0.4, "speed": 0.5, "logP": 0.4, "year": 0.8, "logC": 0.2, "logE": 0.2, "shafts": 0.1},
           "pen_missing": 1.0, "country": 0.35, "type": 3.0, "fine": 0.5, "propulsion": 0.2},
    "T3": {"w": {"logd": 1.5, "logL": 1.0, "logB": 0.6, "logT": 0.4, "speed": 0.5, "logP": 0.4, "year": 0.8, "logC": 0.2, "logE": 0.2, "shafts": 0.1},
           "pen_missing": 1.0, "country": 0.2, "type": 0.6, "fine": 0.2, "weapons": 0.3, "cal": 0.1, "propulsion": 0.1},
}

# neighbor display (key subsets).  T3 shows all numeric specs; T1/T2 keep every hull number that exists.
NBR_NUM = {
    "T1": B.NUMERIC_FIELDS,
    "T2": ["displacement_normal_t", "displacement_full_t", "length_m", "beam_m", "draught_m", "speed_kn", "power_hp", "complement"],
    "T3": B.NUMERIC_FIELDS,
}
# shown for every task when the neighbour has them (same fields the example's own input can carry in v0_2; absent in older records)
NBR_NEW = ["year_in_navy", "displacement_standard_t", "armour_belt_mm", "armour_deck_mm"]


def load_params(path: Path | None = None) -> dict[str, Any]:
    path = path or PARAMS_PATH
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["params"]
    return DEFAULT_PARAMS


def _log(x: float | None) -> float:
    return math.log(x) if x and x > 0 else float("nan")


def _kinds_of_view_weapons(w: list[dict[str, Any]] | None) -> set[str] | None:
    return None if w is None else {x["kind"] for x in w}


def _main_cal(w: list[dict[str, Any]] | None) -> float | None:
    cals = [x["cal"] for x in (w or []) if x.get("kind") == "gun" and x.get("cal")]
    return max(cals) if cals else None


def jaccard_row(Q: np.ndarray, M: np.ndarray) -> np.ndarray:
    """Q: (F,) bool, M: (N,F) bool -> (N,) Jaccard distance (1 - J); empty/empty -> 0."""
    inter = (M & Q).sum(axis=1)
    union = (M | Q).sum(axis=1)
    return np.where(union > 0, 1.0 - inter / np.maximum(union, 1), 0.0)


class Pool:
    """train 艦級レコードの検索インデックス。"""

    def __init__(self, records: list[dict[str, Any]], group_of: dict[str, str]):
        self.recs = records
        self.ids = [r["id"] for r in records]
        self.groups = np.array([group_of[r["id"]] for r in records])
        n = len(records)

        def disp_pair(r: dict[str, Any]) -> tuple[float, float]:
            return _log(r.get("displacement_normal_t")), _log(r.get("displacement_full_t"))

        pairs = np.array([disp_pair(r) for r in records])
        both = ~np.isnan(pairs).any(axis=1)
        self.disp_off = float(np.median(pairs[both, 1] - pairs[both, 0]))  # log(full) - log(normal)
        logS = np.array([_log(r.get("displacement_standard_t")) for r in records])
        sn = ~np.isnan(logS) & ~np.isnan(pairs[:, 0])
        self.std_off = float(np.median(logS[sn] - pairs[sn, 0])) if sn.any() else 0.0  # log(standard) - log(normal)
        logd = np.where(~np.isnan(pairs[:, 0]), pairs[:, 0], pairs[:, 1] - self.disp_off)
        logd = np.where(np.isnan(logd), logS - self.std_off, logd)  # standard displacement only (v0_2 records)
        cols = {
            "logd": logd,
            "logL": np.array([_log(r.get("length_m")) for r in records]),
            "logB": np.array([_log(r.get("beam_m")) for r in records]),
            "logT": np.array([_log(r.get("draught_m")) for r in records]),
            "speed": np.array([r.get("speed_kn") if r.get("speed_kn") is not None else np.nan for r in records], dtype=float),
            "logP": np.array([_log(r.get("power_hp")) for r in records]),
            "year": np.array([r["year"] if r.get("year") else np.nan for r in records], dtype=float),
            "logC": np.array([_log(r.get("complement")) for r in records]),
            "logE": np.array([_log(r.get("endurance_nm")) for r in records]),
            "shafts": np.array([r.get("shafts") if r.get("shafts") is not None else np.nan for r in records], dtype=float),
            "yearN": np.array([(r.get("year_in_navy") or r.get("year") or np.nan) for r in records], dtype=float),
            "logS": logS,
            "armB": np.array([_log(r.get("armour_belt_mm")) for r in records]),
            "armD": np.array([_log(r.get("armour_deck_mm")) for r in records]),
        }
        self.num = {k: np.asarray(v, dtype=float) for k, v in cols.items()}
        self.sd = {k: float(np.nanstd(v)) or 1.0 for k, v in self.num.items()}
        self.country = np.array([(r.get("country") or "").replace("-", " ") for r in records])
        known_type = [r["ship_type"] != T.UNKNOWN and bool(r.get("ship_type_fine")) for r in records]
        self.type_known = np.array(known_type)
        self.coarse = np.array([r["ship_type"] if k else "" for r, k in zip(records, known_type)])
        self.fine = np.array([(r.get("ship_type_fine") or "") if k else "" for r, k in zip(records, known_type)])
        compact = [B.compact_weapons(r.get("weapons")) for r in records]
        self.w_ok = np.array([c is not None and bool(r.get("armament_parse_ok")) and len(c) <= B.MAX_WEAPON_ITEMS for r, c in zip(records, compact)])
        self.W = np.zeros((n, len(KINDS)), dtype=bool)
        self.maincal = np.full(n, np.nan)
        for i, c in enumerate(compact):
            if c is not None and self.w_ok[i]:
                for x in c:
                    self.W[i, KINDS.index(x["kind"])] = True
                mc = _main_cal(c)
                if mc:
                    self.maincal[i] = mc
        self.s_ok = np.array([bool(r.get("sensors_known")) and r.get("sensor_kinds") is not None for r in records])
        self.Sm = np.zeros((n, len(SENS)), dtype=bool)
        for i, r in enumerate(records):
            if self.s_ok[i]:
                for k in r["sensor_kinds"]:
                    if k in SENS:
                        self.Sm[i, SENS.index(k)] = True
        self.Pm = np.zeros((n, len(PROPS)), dtype=bool)
        self.p_ok = np.array([bool(r.get("propulsion")) for r in records])
        for i, r in enumerate(records):
            for k in r.get("propulsion") or []:
                if k in PROPS:
                    self.Pm[i, PROPS.index(k)] = True
        self.has = {f: np.array([r.get(f) is not None for r in records]) for f in B.T3_FIELDS}

    # -- query side -----------------------------------------------------
    def _query_numbers(self, view: dict[str, Any]) -> dict[str, float]:
        q: dict[str, float] = {}
        dn, df = view.get("displacement_normal_t"), view.get("displacement_full_t")
        ds = view.get("displacement_standard_t")
        if dn:
            q["logd"] = math.log(dn)
        elif df:
            q["logd"] = math.log(df) - self.disp_off
        elif ds:
            q["logd"] = math.log(ds) - self.std_off
        if ds:
            q["logS"] = math.log(ds)
        for key, f in (("armB", "armour_belt_mm"), ("armD", "armour_deck_mm")):
            if view.get(f):
                q[key] = math.log(view[f])
        # year_in_navy is only shown when it differs from year: absent means "same as year" (or dropped from a partial view)
        yn = view.get("year_in_navy") or view.get("year")
        if yn:
            q["yearN"] = float(yn)
        for key, f in (("logL", "length_m"), ("logB", "beam_m"), ("logT", "draught_m"), ("logP", "power_hp"), ("logC", "complement"), ("logE", "endurance_nm")):
            if view.get(f):
                q[key] = math.log(view[f])
        if view.get("speed_kn") is not None:
            q["speed"] = float(view["speed_kn"])
        if view.get("year"):
            q["year"] = float(view["year"])
        if view.get("shafts") is not None:
            q["shafts"] = float(view["shafts"])
        return q

    def distances(self, view: dict[str, Any], task: str, prm: dict[str, Any]) -> np.ndarray:
        """view（= 例の user JSON）に見えている項目だけで全 Pool への距離を返す。"""
        n = len(self.recs)
        q = self._query_numbers(view)
        w = prm["w"]
        num = np.zeros(n)
        wsum = np.zeros(n)
        wq = 0.0
        for k, qv in q.items():
            wk = w.get(k, 0.0)
            if wk <= 0:
                continue
            wq += wk
            col = self.num[k]
            m = ~np.isnan(col)
            diff = np.where(m, (col - qv) / self.sd[k], 0.0)
            num += wk * diff * diff
            wsum += wk * m
        if wq > 0:
            d = np.sqrt(num / np.maximum(wsum, 1e-9)) + prm["pen_missing"] * (1.0 - wsum / wq)
            d = np.where(wsum > 0, d, 3.0 + prm["pen_missing"])
        else:
            d = np.zeros(n)
        # categorical terms
        if view.get("country") and prm.get("country"):
            c = view["country"]
            d = d + prm["country"] * np.where(self.country == c, 0.0, np.where(self.country == "", 0.5, 1.0))
        if task in ("T2", "T3") and view.get("ship_type"):
            if prm.get("type"):
                d = d + prm["type"] * np.where(self.coarse == view["ship_type"], 0.0, 1.0)
            if view.get("ship_type_fine") and prm.get("fine"):
                d = d + prm["fine"] * np.where(self.fine == view["ship_type_fine"], 0.0, 1.0)
        if task in ("T1", "T3") and view.get("weapons") is not None:
            kinds = _kinds_of_view_weapons(view["weapons"]) or set()
            Q = np.array([k in kinds for k in KINDS])
            if prm.get("weapons"):
                d = d + prm["weapons"] * np.where(self.w_ok, jaccard_row(Q, self.W), 0.6)
            qc = _main_cal(view["weapons"])
            if prm.get("cal"):
                both = ~np.isnan(self.maincal) & (qc is not None)
                if qc is not None:
                    dc = np.where(both, np.minimum(1.0, np.abs(np.log(np.where(both, self.maincal, 1.0) / qc)) / math.log(3.0)), 0.0)
                    one_side = self.w_ok & np.isnan(self.maincal)  # candidate known to have no gun, query has one
                    d = d + prm["cal"] * np.where(one_side, 1.0, dc)
                else:
                    has_gun = ~np.isnan(self.maincal)
                    d = d + prm["cal"] * np.where(self.w_ok & has_gun, 1.0, 0.0)
        if task == "T1" and "sensors" in view and prm.get("sensors"):
            Q = np.array([k in set(view["sensors"]) for k in SENS])
            d = d + prm["sensors"] * np.where(self.s_ok, jaccard_row(Q, self.Sm), 0.6)
        if view.get("propulsion") and prm.get("propulsion"):
            Q = np.array([k in set(view["propulsion"]) for k in PROPS])
            d = d + prm["propulsion"] * np.where(self.p_ok, jaccard_row(Q, self.Pm), 0.5)
        return d

    def eligible(self, view: dict[str, Any], task: str, k: int) -> np.ndarray:
        """表示・正解に使える候補（入力に見えている want / predict と、候補が持つ情報から決まる）。"""
        n = len(self.recs)
        if task == "T1":
            return self.type_known.copy()
        if task == "T2":
            m = self.type_known & self.w_ok
            if "sensors" in view.get("want", ["weapons", "sensors"]):
                m2 = m & self.s_ok
                if m2.sum() >= k + 6:
                    m = m2
            return m
        m = np.ones(n, dtype=bool)
        hidden = view.get("predict", [])
        for f in hidden:
            m &= self.has[f]
        if m.sum() < k + 6:  # very rare: relax to "has at least one hidden field"
            m = np.zeros(n, dtype=bool)
            for f in hidden:
                m |= self.has[f]
        return m

    def search(self, view: dict[str, Any], task: str, prm: dict[str, Any] | None = None, exclude_group: str | None = None,
               k: int = 3, extra: int = 6, dedup: bool = True) -> list[tuple[int, float]]:
        prm = prm or load_params()[task]
        d = self.distances(view, task, prm)
        m = self.eligible(view, task, k)
        if exclude_group is not None:
            m = m & (self.groups != exclude_group)
        d = np.where(m, d, np.inf)
        cand = np.argsort(d, kind="stable")[: k + extra]  # full stable sort: ties resolve by pool order, independent of k
        out: list[tuple[int, float]] = []
        seen: set[str] = set()
        for j in cand:
            if not np.isfinite(d[j]):
                break
            if dedup:
                key = json.dumps(neighbor_payload(self.recs[int(j)], task, view), sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
            out.append((int(j), float(d[j])))
            if len(out) >= k:
                break
        return out


# ---------------------------------------------------------------------------
# neighbor display
# ---------------------------------------------------------------------------
def weapons_summary(rec: dict[str, Any]) -> dict[str, Any] | None:
    w = B.compact_weapons(rec.get("weapons"))
    if w is None or not rec.get("armament_parse_ok"):
        return None
    out: dict[str, Any] = {"kinds": sorted({x["kind"] for x in w}, key=KINDS.index)}
    guns = [x for x in w if x["kind"] == "gun" and x.get("cal")]
    if guns:
        cal = max(g["cal"] for g in guns)
        out["main_gun_mm"] = cal
        out["main_barrels"] = sum(g.get("n", 1) * g.get("per", 1) for g in guns if g["cal"] == cal)
    return out


def neighbor_payload(rec: dict[str, Any], task: str, view: dict[str, Any] | None = None, d: float | None = None) -> dict[str, Any]:
    """近傍 1 艦の表示。艦名・艦級名・URL・id は含めない。"""
    fv = B.full_view(rec)
    p: dict[str, Any] = {}
    if rec["ship_type"] != T.UNKNOWN and rec.get("ship_type_fine"):
        p["ship_type"] = rec["ship_type"]
        p["ship_type_fine"] = rec["ship_type_fine"]
    for f in ("year", "country"):
        if f in fv:
            p[f] = fv[f]
    for f in NBR_NUM[task]:
        if f in fv:
            p[f] = fv[f]
    for f in NBR_NEW:
        if f in fv:
            p[f] = fv[f]
    if task == "T1":
        if "propulsion" in fv:
            p["propulsion"] = fv["propulsion"]
        if "weapons" in fv:
            p["weapons"] = fv["weapons"]
        if "sensors" in fv:
            p["sensors"] = fv["sensors"]
    elif task == "T2":
        if "weapons" in fv:
            p["weapons"] = fv["weapons"]
        if "sensors" in fv and (view is None or "sensors" in view.get("want", ["weapons", "sensors"])):
            p["sensors"] = fv["sensors"]
    else:
        ws = weapons_summary(rec)
        if ws is not None:
            p["armament"] = ws
    if d is not None:
        p["d"] = round(d, 2)
    return p


def jdump(o: Any) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# data loading
# ---------------------------------------------------------------------------
def read_jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_pool(v0_dir: Path = V0_DIR) -> tuple[Pool, dict[str, str], dict[str, str]]:
    recs = read_jsonl(v0_dir / "records.jsonl")
    sp = json.loads((v0_dir / "splits.json").read_text(encoding="utf-8"))
    assign, group_of = sp["assignments"], sp["group_of"]
    train = [r for r in recs if assign[r["id"]] == "train"]
    return Pool(train, group_of), assign, group_of


# ---------------------------------------------------------------------------
# tuning (valid only)
# ---------------------------------------------------------------------------
def _gold(e: dict[str, Any]) -> Any:
    return json.loads(e["messages"][2]["content"])


def _view(e: dict[str, Any]) -> dict[str, Any]:
    return json.loads(e["messages"][1]["content"])


def objective(pool: Pool, exs: list[dict[str, Any]], task: str, prm: dict[str, Any], group_of: dict[str, str], k: int = 3) -> tuple[float, dict[str, float]]:
    n = len(exs)
    if task == "T1":
        top1 = maj = orc = 0
        for e in exs:
            g = _gold(e)["ship_type"]
            nb = pool.search(_view(e), "T1", prm, exclude_group=group_of[e["row"]], k=k, dedup=False)
            labs = [pool.coarse[j] for j, _ in nb]
            top1 += labs[0] == g
            orc += g in labs
            cnt: dict[str, int] = {}
            for l in labs:
                cnt[l] = cnt.get(l, 0) + 1
            best = max(cnt.values())
            maj += next(l for l in labs if cnt[l] == best) == g
        m = {"top1": top1 / n, "maj": maj / n, "oracle": orc / n}
        return (m["top1"] + m["maj"] + m["oracle"]) / 3, m
    if task == "T2":
        tp = fp = fn = 0
        for e in exs:
            gw = _gold(e)["weapons"]
            nb = pool.search(_view(e), "T2", prm, exclude_group=group_of[e["row"]], k=1, dedup=False)
            pw = B.compact_weapons(pool.recs[nb[0][0]]["weapons"]) if nb else []
            a, b, c = S.match_items(gw, pw)
            tp, fp, fn = tp + a, fp + b, fn + c
        f1 = S.prf(tp, fp, fn)[2]
        return f1, {"weapon_f1_top1": f1}
    errs = []
    for e in exs:
        gold = _gold(e)
        nb = pool.search(_view(e), "T3", prm, exclude_group=group_of[e["row"]], k=k, dedup=False)
        for f, g in gold.items():
            vals = sorted(pool.recs[j][f] for j, _ in nb if pool.recs[j].get(f))
            med = vals[len(vals) // 2]
            errs.append(min(2.0, abs(med - g) / g))
    mape = float(np.mean(errs))
    return -mape, {"mape_med_k": mape}


def tune(args: argparse.Namespace) -> None:
    import random

    pool, assign, group_of = load_pool()
    valid = read_jsonl(V0_DIR / "valid.jsonl")
    rng = random.Random(0)
    params = json.loads(json.dumps(DEFAULT_PARAMS))
    report: dict[str, Any] = {}
    for task in ("T1", "T2", "T3"):
        exs = [e for e in valid if e["task"] == task]
        rng.shuffle(exs)
        exs = exs[: args.n]
        best, m0 = objective(pool, exs, task, params[task], group_of)
        report[task] = {"default": {"obj": best, **m0}, "n": len(exs), "steps": []}
        print(task, "default", round(best, 4), m0, flush=True)
        keys = [("w", k) for k in NUM_KEYS] + [(None, k) for k in params[task] if k not in ("w", "pen_missing")] + [(None, "pen_missing")]
        for p in range(args.passes):
            for grp, key in keys:
                tgt = params[task]["w"] if grp else params[task]
                cur = tgt[key]
                for f in (0.4, 0.7, 1.5, 2.5):
                    tgt[key] = cur * f
                    o, m = objective(pool, exs, task, params[task], group_of)
                    if o > best + 1e-4:
                        best, m0 = o, m
                        cur = tgt[key]
                        report[task]["steps"].append({"key": key if not grp else f"w.{key}", "value": round(cur, 4), "obj": round(o, 4)})
                        print(task, p, key, round(cur, 3), round(o, 4), m, flush=True)
                    else:
                        tgt[key] = cur
        report[task]["tuned"] = {"obj": best, **m0}
    V1_DIR.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(json.dumps({"params": params, "tuning": report, "note": "tuned on valid (subsample) only; test never used"}, indent=1), encoding="utf-8")
    print("wrote", PARAMS_PATH)


def demo(args: argparse.Namespace) -> None:
    pool, assign, group_of = load_pool()
    test = read_jsonl(V0_DIR / "test.jsonl")
    seen = set()
    for e in test:
        if e["task"] in seen or e["variant"] not in ("full", "k2"):
            continue
        seen.add(e["task"])
        v = _view(e)
        print("\n##", e["id"], jdump(v))
        for j, d in pool.search(v, e["task"], exclude_group=group_of[e["row"]]):
            print("  ", jdump(neighbor_payload(pool.recs[j], e["task"], v, d)))
        print("  gold:", jdump(_gold(e)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tune")
    t.add_argument("--n", type=int, default=800, help="valid examples per task used for tuning")
    t.add_argument("--passes", type=int, default=2)
    t.set_defaults(fn=tune)
    d = sub.add_parser("demo")
    d.set_defaults(fn=demo)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
