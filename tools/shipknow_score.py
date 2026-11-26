"""Scorer for the shipknow multi-task test set (T1 ship type / T2 armament+sensors / T3 missing numbers).

    .venv/bin/python tools/shipknow_score.py --test data/navspec/shipknow_v0/test.jsonl --pred preds.jsonl [--out result.json]

PREDICTION FILE FORMAT (JSONL, one line per test example; ids are the `id` of test.jsonl):

    {"id": "c00004:T1:full",
     "text": "{\\"ship_type\\":\\"patrol\\",\\"ship_type_fine\\":\\"patrol-boat\\"}",   # raw model output string (LLM inference script)
     "output": {...},                       # OR an already parsed object (non-LLM baselines)
     "ranking": {"destroyer": -0.4, ...},   # T1, optional: log-likelihood of each coarse label
     "samples": ["{...}", ...]}             # T3, optional: N sampled raw outputs (or parsed objects) for intervals

  * `text` and `output` are alternatives; if both are present `text` wins.
  * T1 `ranking`: for every coarse label L (25 labels, see manifest.json) the summed token log-probability of the
    assistant prefix `{"ship_type":"L"` given the prompt.  The scorer softmaxes over the labels, so any
    consistent log-likelihood scale works.  Used for top-3 accuracy and ECE (top-1 = argmax of the ranking).
  * T3 `samples`: >=3 sampled completions (temperature ~0.7-1.0).  Interval = [q_lo, q_hi] of the per-field
    sampled values (default 0.1/0.9); coverage = P(gold in interval), width = (hi-lo)/median.
  * Missing ids are counted as invalid outputs.

METRICS
  all tasks  json_invalid_rate (missing or unparsable), schema_violation_rate (parsed but wrong keys / types / enums)
  T1         coarse_acc, macro_f1, fine_acc, top3_acc + ece (needs ranking), top confusions, per-class P/R/F1
  T2         weapon-item P/R/F1 (match = same kind and caliber within +-5 %, kinds without caliber match on kind),
             main_caliber_acc (+-5 %), main_barrels mae/exact, kind-presence F1 (torpedo/missile/aircraft/asw/mine/
             ciws/gun/sonar/radar/esm_ecm), sensor-kind-set P/R/F1 + exact match
  T3         per field and overall: mape, median relative error, within_10pct, within_25pct; with samples:
             interval coverage and relative width
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Any

WEAPON_KINDS = ["gun", "ciws", "mg", "torpedo_tube", "ssm", "sam", "ballistic_missile", "asw_rocket", "rocket_launcher",
                "grenade_launcher", "depth_charge", "mine", "minesweeping_gear", "aircraft_helicopter", "aviation_facility", "small_arms"]
SENSOR_KINDS = ["air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar", "radar", "sonar", "esm_ecm",
                "decoy", "eo_director", "combat_system"]
RADAR_KINDS = {"air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar", "radar"}
CAL_TOL = 0.05

PRESENCE_FLAGS = {
    "torpedo": lambda w, s: any(x["kind"] == "torpedo_tube" for x in w),
    "missile": lambda w, s: any(x["kind"] in ("ssm", "sam", "ballistic_missile") for x in w),
    "ssm": lambda w, s: any(x["kind"] == "ssm" for x in w),
    "sam": lambda w, s: any(x["kind"] == "sam" for x in w),
    "aircraft": lambda w, s: any(x["kind"] in ("aircraft_helicopter", "aviation_facility") for x in w),
    "asw": lambda w, s: any(x["kind"] in ("asw_rocket", "depth_charge") for x in w),
    "mine": lambda w, s: any(x["kind"] in ("mine", "minesweeping_gear") for x in w),
    "ciws": lambda w, s: any(x["kind"] == "ciws" for x in w),
    "gun": lambda w, s: any(x["kind"] == "gun" for x in w),
}
SENSOR_FLAGS = {
    "sonar": lambda w, s: "sonar" in s,
    "radar_any": lambda w, s: bool(RADAR_KINDS & set(s)),
    "esm_ecm": lambda w, s: "esm_ecm" in s,
}


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def extract_json(text: str) -> Any | None:
    if not isinstance(text, str):
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    t = re.sub(r"^<think>.*?</think>\s*", "", t, flags=re.S)
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
        start = t.find("{", start + 1)
    return None


def parse_output(pred: dict[str, Any] | None) -> Any | None:
    if pred is None:
        return None
    if "text" in pred and pred["text"] is not None:
        return extract_json(pred["text"])
    if "output" in pred:
        out = pred["output"]
        return extract_json(out) if isinstance(out, str) else out
    return None


def is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ---------------------------------------------------------------------------
# gold
# ---------------------------------------------------------------------------
def load_test(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        r["gold"] = json.loads(r["messages"][2]["content"])
        r["inp"] = json.loads(r["messages"][1]["content"])
        rows.append(r)
    return rows


def load_preds(path: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            p = json.loads(line)
            out[p["id"]] = p
    return out


# ---------------------------------------------------------------------------
# schema validation (returns error string or None)
# ---------------------------------------------------------------------------
def validate(task: str, obj: Any, row: dict[str, Any], labels: set[str]) -> str | None:
    if not isinstance(obj, dict):
        return "not_object"
    if task == "T1":
        if not isinstance(obj.get("ship_type"), str) or obj["ship_type"] not in labels:
            return "bad_ship_type"
        if not isinstance(obj.get("ship_type_fine"), str):
            return "bad_ship_type_fine"
    elif task == "T2":
        want = row["inp"].get("want", ["weapons", "sensors"])
        if set(obj) != set(want):
            return "keys_mismatch"
        w = obj.get("weapons")
        if not isinstance(w, list):
            return "weapons_not_list"
        for it in w:
            if not isinstance(it, dict) or it.get("kind") not in WEAPON_KINDS:
                return "bad_weapon_kind"
            for k in ("n", "per", "cal"):
                if k in it and it[k] is not None and not is_num(it[k]):
                    return f"bad_weapon_{k}"
        if "sensors" in want:
            s = obj.get("sensors")
            if not isinstance(s, list) or any(x not in SENSOR_KINDS for x in s):
                return "bad_sensors"
    elif task == "T3":
        hidden = set(row["inp"].get("predict", []))
        if set(obj) != hidden:
            return "keys_mismatch"
        if any(not is_num(v) or v <= 0 for v in obj.values()):
            return "bad_number"
    return None


# ---------------------------------------------------------------------------
# T1
# ---------------------------------------------------------------------------
def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def ece_score(confs: list[float], correct: list[bool], bins: int = 10) -> float:
    if not confs:
        return float("nan")
    tot = len(confs)
    e = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(confs) if (lo <= c < hi) or (b == bins - 1 and c == 1.0)]
        if idx:
            e += len(idx) / tot * abs(sum(correct[i] for i in idx) / len(idx) - statistics.mean(confs[i] for i in idx))
    return e


def softmax(d: dict[str, float]) -> dict[str, float]:
    m = max(d.values())
    ex = {k: math.exp(v - m) for k, v in d.items()}
    z = sum(ex.values())
    return {k: v / z for k, v in ex.items()}


def score_t1(items: list[tuple[dict, Any, dict | None, str | None]], labels: list[str]) -> dict[str, Any]:
    n = len(items)
    gold = [r["gold"]["ship_type"] for r, *_ in items]
    pred = []
    fine_ok = 0
    rank_items = []
    for r, obj, pr, err in items:
        pred.append(obj["ship_type"] if err is None else None)
        if err is None and obj.get("ship_type_fine") == r["gold"]["ship_type_fine"]:
            fine_ok += 1
        if pr and isinstance(pr.get("ranking"), dict) and pr["ranking"]:
            rank = {k: float(v) for k, v in pr["ranking"].items() if k in labels and is_num(v)}
            if rank:
                rank_items.append((r["gold"]["ship_type"], softmax(rank)))
    res: dict[str, Any] = {"n": n}
    res["coarse_acc"] = sum(1 for g, p in zip(gold, pred) if g == p) / n if n else 0.0
    res["fine_acc"] = fine_ok / n if n else 0.0
    per = {}
    f1s = []
    for lab in labels:
        tp = sum(1 for g, p in zip(gold, pred) if g == lab and p == lab)
        fp = sum(1 for g, p in zip(gold, pred) if g != lab and p == lab)
        fn = sum(1 for g, p in zip(gold, pred) if g == lab and p != lab)
        sup = sum(1 for g in gold if g == lab)
        if sup == 0 and fp == 0:
            continue
        p_, r_, f_ = prf(tp, fp, fn)
        per[lab] = {"p": round(p_, 3), "r": round(r_, 3), "f1": round(f_, 3), "support": sup}
        if sup > 0:
            f1s.append(f_)
    res["macro_f1"] = statistics.mean(f1s) if f1s else 0.0
    conf = collections.Counter((g, p) for g, p in zip(gold, pred) if g != p)
    res["top_confusions"] = [{"gold": g, "pred": p, "count": c} for (g, p), c in conf.most_common(12)]
    res["per_class"] = per
    if rank_items:
        top3 = 0
        confs, corr = [], []
        top1 = 0
        for g, probs in rank_items:
            order = sorted(probs, key=lambda k: -probs[k])
            top3 += g in order[:3]
            top1 += order[0] == g
            confs.append(probs[order[0]])
            corr.append(order[0] == g)
        res["ranking"] = {"n": len(rank_items), "top1_acc": top1 / len(rank_items), "top3_acc": top3 / len(rank_items), "ece": ece_score(confs, corr),
                          "mean_top1_confidence": statistics.mean(confs)}
    return res


# ---------------------------------------------------------------------------
# T2
# ---------------------------------------------------------------------------
def match_items(gold: list[dict], pred: list[dict]) -> tuple[int, int, int]:
    """Greedy kind x caliber matching -> (tp, fp, fn)."""
    used = set()
    tp = 0
    for g in gold:
        best, bd = None, 1e9
        for j, p in enumerate(pred):
            if j in used or p.get("kind") != g["kind"]:
                continue
            gc, pc = g.get("cal"), p.get("cal")
            if not gc:  # gold has no caliber -> kind match is enough
                best, bd = j, 0.0
                break
            if pc:
                d = abs(pc - gc) / gc
                if d <= CAL_TOL and d < bd:
                    best, bd = j, d
        if best is not None:
            used.add(best)
            tp += 1
    return tp, len(pred) - tp, len(gold) - tp


def main_gun(items: list[dict]) -> tuple[float, int] | None:
    guns = [x for x in items if x.get("kind") == "gun" and is_num(x.get("cal"))]
    if not guns:
        return None
    cal = max(g["cal"] for g in guns)
    barrels = sum(int(g.get("n", 1) or 1) * int(g.get("per", 1) or 1) for g in guns if g["cal"] == cal)
    return cal, barrels


def score_t2(items: list[tuple[dict, Any, dict | None, str | None]]) -> dict[str, Any]:
    n = len(items)
    tp = fp = fn = 0
    ex_f1 = []
    mc_total = mc_ok = 0
    mb_err: list[float] = []
    mb_exact = mb_tot = 0
    flag_counts = {k: [0, 0, 0] for k in list(PRESENCE_FLAGS) + list(SENSOR_FLAGS)}
    s_tp = s_fp = s_fn = 0
    s_exact = s_tot = 0
    jacc = []
    for r, obj, pr, err in items:
        gw = r["gold"]["weapons"]
        want_sensors = "sensors" in r["gold"]
        pw = obj["weapons"] if err is None else []
        a, b, c = match_items(gw, pw)
        tp, fp, fn = tp + a, fp + b, fn + c
        if not gw and not pw and err is None:
            ex_f1.append(1.0)
        else:
            ex_f1.append(prf(a, b, c)[2])
        gm = main_gun(gw)
        if gm:
            mc_total += 1
            pm = main_gun(pw)
            if pm and abs(pm[0] - gm[0]) / gm[0] <= CAL_TOL:
                mc_ok += 1
                mb_tot += 1
                mb_err.append(abs(pm[1] - gm[1]))
                mb_exact += pm[1] == gm[1]
        gs = r["gold"].get("sensors", []) if want_sensors else []
        ps = (obj.get("sensors", []) if err is None else []) if want_sensors else []
        for name, fn_ in PRESENCE_FLAGS.items():
            g, p = fn_(gw, gs), fn_(pw, ps)
            fc = flag_counts[name]
            fc[0] += g and p
            fc[1] += (not g) and p
            fc[2] += g and (not p)
        if want_sensors:
            for name, fn_ in SENSOR_FLAGS.items():
                g, p = fn_(gw, gs), fn_(pw, ps)
                fc = flag_counts[name]
                fc[0] += g and p
                fc[1] += (not g) and p
                fc[2] += g and (not p)
            gset, pset = set(gs), set(ps)
            s_tp += len(gset & pset)
            s_fp += len(pset - gset)
            s_fn += len(gset - pset)
            s_tot += 1
            s_exact += gset == pset
            jacc.append(1.0 if not gset and not pset else len(gset & pset) / len(gset | pset))
    p_, r_, f_ = prf(tp, fp, fn)
    res: dict[str, Any] = {
        "n": n,
        "weapon_item": {"precision": p_, "recall": r_, "f1": f_, "tp": tp, "fp": fp, "fn": fn, "example_mean_f1": statistics.mean(ex_f1) if ex_f1 else 0.0},
        "main_caliber_acc": mc_ok / mc_total if mc_total else None,
        "main_caliber_n": mc_total,
        "main_barrels": {"n": mb_tot, "mae": statistics.mean(mb_err) if mb_err else None, "exact": mb_exact / mb_tot if mb_tot else None},
        "kind_presence_f1": {},
    }
    for name, (a, b, c) in flag_counts.items():
        p1, r1, f1 = prf(a, b, c)
        res["kind_presence_f1"][name] = {"f1": round(f1, 4), "p": round(p1, 4), "r": round(r1, 4), "support": a + c}
    res["kind_presence_f1"]["macro"] = statistics.mean(v["f1"] for v in res["kind_presence_f1"].values() if v["support"] > 0) if any(v["support"] > 0 for v in res["kind_presence_f1"].values()) else 0.0
    p2, r2, f2 = prf(s_tp, s_fp, s_fn)
    res["sensor_kinds"] = {"n": s_tot, "precision": p2, "recall": r2, "f1": f2, "exact_match": s_exact / s_tot if s_tot else None, "mean_jaccard": statistics.mean(jacc) if jacc else None}
    return res


# ---------------------------------------------------------------------------
# T3
# ---------------------------------------------------------------------------
def quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def score_t3(items: list[tuple[dict, Any, dict | None, str | None]], q_lo: float, q_hi: float) -> dict[str, Any]:
    rel: dict[str, list[float]] = collections.defaultdict(list)
    cover: dict[str, list[bool]] = collections.defaultdict(list)
    width: dict[str, list[float]] = collections.defaultdict(list)
    by_k: dict[str, list[float]] = collections.defaultdict(list)
    n_fields = n_valid_fields = 0
    for r, obj, pr, err in items:
        for f, g in r["gold"].items():
            n_fields += 1
            if err is not None or f not in obj or not is_num(obj.get(f)):
                continue
            n_valid_fields += 1
            e = abs(obj[f] - g) / g
            rel[f].append(e)
            by_k[r["variant"]].append(e)
            if pr and pr.get("samples"):
                vals = []
                for s in pr["samples"]:
                    so = extract_json(s) if isinstance(s, str) else s
                    if isinstance(so, dict) and is_num(so.get(f)) and so[f] > 0:
                        vals.append(float(so[f]))
                if len(vals) >= 3:
                    lo, hi = quantile(vals, q_lo), quantile(vals, q_hi)
                    cover[f].append(lo <= g <= hi)
                    med = statistics.median(vals)
                    width[f].append((hi - lo) / med if med else float("nan"))

    def agg(es: list[float]) -> dict[str, Any]:
        if not es:
            return {"n": 0}
        return {"n": len(es), "mape": statistics.mean(es), "median_rel_err": statistics.median(es),
                "within_10pct": sum(e <= 0.10 for e in es) / len(es), "within_25pct": sum(e <= 0.25 for e in es) / len(es)}

    allr = [e for v in rel.values() for e in v]
    res: dict[str, Any] = {"n_examples": len(items), "n_fields": n_fields, "valid_field_rate": n_valid_fields / n_fields if n_fields else 0.0,
                           "overall": agg(allr), "per_field": {f: agg(v) for f, v in sorted(rel.items())}, "per_k": {k: agg(v) for k, v in sorted(by_k.items())}}
    if cover:
        allc = [c for v in cover.values() for c in v]
        allw = [w for v in width.values() for w in v if not math.isnan(w)]
        res["interval"] = {"quantiles": [q_lo, q_hi], "coverage": sum(allc) / len(allc), "n": len(allc), "mean_rel_width": statistics.mean(allw) if allw else None,
                           "median_rel_width": statistics.median(allw) if allw else None,
                           "per_field": {f: {"coverage": sum(v) / len(v), "n": len(v), "median_rel_width": statistics.median([w for w in width[f] if not math.isnan(w)]) if width[f] else None} for f, v in sorted(cover.items())}}
    return res


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def score(test_rows: list[dict[str, Any]], preds: dict[str, dict[str, Any]], labels: list[str], q: tuple[float, float] = (0.1, 0.9),
          tasks: tuple[str, ...] = ("T1", "T2", "T3"), variant_split: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {}
    label_set = set(labels)
    for task in tasks:
        rows = [r for r in test_rows if r["task"] == task]
        if not rows:
            continue
        items = []
        missing = invalid = schema = 0
        for r in rows:
            pr = preds.get(r["id"])
            if pr is None:
                missing += 1
                items.append((r, None, None, "missing"))
                continue
            obj = parse_output(pr)
            if obj is None:
                invalid += 1
                items.append((r, None, pr, "invalid_json"))
                continue
            err = validate(task, obj, r, label_set)
            if err:
                schema += 1
            items.append((r, obj, pr, err))
        n = len(rows)
        res: dict[str, Any] = {
            "n": n,
            "missing_predictions": missing,
            "json_invalid_rate": (missing + invalid) / n,
            "schema_violation_rate": schema / n,
            "schema_errors": dict(collections.Counter(e for *_, e in items if e not in (None, "missing", "invalid_json"))),
        }
        fn = {"T1": lambda it: score_t1(it, labels), "T2": score_t2, "T3": lambda it: score_t3(it, *q)}[task]
        res["metrics"] = fn(items)
        if variant_split:
            res["by_variant"] = {}
            for v in sorted({r["variant"] for r in rows}):
                sub = [it for it in items if it[0]["variant"] == v]
                if task == "T3":
                    continue  # per-k is already inside metrics
                res["by_variant"][v] = fn(sub)
        out[task] = res
    return out


def fmt(x: Any) -> str:
    if x is None:
        return "-"
    return f"{x:.3f}" if isinstance(x, float) else str(x)


def print_summary(res: dict[str, Any]) -> None:
    for task, r in res.items():
        m = r["metrics"]
        print(f"[{task}] n={r['n']} json_invalid={fmt(r['json_invalid_rate'])} schema_violation={fmt(r['schema_violation_rate'])}")
        if task == "T1":
            print(f"   coarse_acc={fmt(m['coarse_acc'])} macro_f1={fmt(m['macro_f1'])} fine_acc={fmt(m['fine_acc'])}", end="")
            if "ranking" in m:
                print(f" top3={fmt(m['ranking']['top3_acc'])} ece={fmt(m['ranking']['ece'])}", end="")
            print()
        elif task == "T2":
            w = m["weapon_item"]
            print(f"   weapon P/R/F1={fmt(w['precision'])}/{fmt(w['recall'])}/{fmt(w['f1'])} main_caliber_acc={fmt(m['main_caliber_acc'])} "
                  f"main_barrels_mae={fmt(m['main_barrels']['mae'])} kind_presence_macro_f1={fmt(m['kind_presence_f1']['macro'])} sensor_F1={fmt(m['sensor_kinds']['f1'])}")
        else:
            o = m["overall"]
            print(f"   MAPE={fmt(o.get('mape'))} median_rel_err={fmt(o.get('median_rel_err'))} within10={fmt(o.get('within_10pct'))} valid_field_rate={fmt(m['valid_field_rate'])}", end="")
            if "interval" in m:
                print(f" coverage={fmt(m['interval']['coverage'])} rel_width={fmt(m['interval']['median_rel_width'])}", end="")
            print()


def labels_from_manifest(test_path: Path) -> list[str]:
    man = test_path.parent / "manifest.json"
    if man.exists():
        return json.loads(man.read_text(encoding="utf-8"))["coarse_types"]
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import shipknow_types as T

    return list(T.COARSE_TYPES)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", type=Path, required=True)
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--interval-q", type=float, nargs=2, default=(0.1, 0.9))
    ap.add_argument("--tasks", nargs="*", default=["T1", "T2", "T3"])
    args = ap.parse_args()
    rows = load_test(args.test)
    preds = load_preds(args.pred)
    res = score(rows, preds, labels_from_manifest(args.test), tuple(args.interval_q), tuple(args.tasks))
    print_summary(res)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
