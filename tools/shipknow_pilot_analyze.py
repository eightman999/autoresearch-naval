#!/usr/bin/env python3
"""shipknow pilot の追加分析と比較表を作る（tools/shipknow_score.py を import して使う）。

  python tools/shipknow_pilot_analyze.py --test data/navspec/shipknow_v0/test.jsonl \\
      --run pilot=preds_pilot.jsonl.gz --run zeroshot=preds_zs.jsonl --baselines data/navspec/shipknow_v0/baseline_preds \\
      --out docs/shipknow_v0/pilot/analysis.json --md docs/shipknow_v0/pilot/analysis.md

  * --run name=pred_path（複数可、.gz 可）。--subset-of NAME を指定すると、その run の予測 id に test を絞り、
    全 run・全ベースラインを同じサブセットで採点する（ゼロショットのサブセット比較用）。
  * 各 run について、全体採点（score の出力）に加えて:
      T1: variant 別 accuracy、混同上位 20 と全混同、ranking（top3/ECE）
      T2: 年代別（<=1905 / 1906-1945 / >=1946 / unknown）の weapon F1 ほか
      T3: 項目別 MAPE / ±10 % 以内率 / 区間被覆率（score の per_field / interval）、k（同時に隠した項目数）別
  * ベースライン: baseline_preds/{t1_*,t2_*,t3_*}.jsonl を該当タスクだけ採点する。
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_score as S  # noqa: E402


def load_preds_any(path: Path) -> dict[str, dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    out = {}
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                p = json.loads(line)
                out[p["id"]] = p
    return out


def era_of(row: dict[str, Any]) -> str:
    y = row["inp"].get("year")
    if not isinstance(y, (int, float)):
        return "unknown"
    if y <= 1905:
        return "<=1905"
    if y <= 1945:
        return "1906-1945"
    return ">=1946"


def build_items(task: str, rows: list[dict[str, Any]], preds: dict[str, dict[str, Any]], labels: list[str]) -> list[tuple]:
    items = []
    lab = set(labels)
    for r in rows:
        pr = preds.get(r["id"])
        if pr is None:
            items.append((r, None, None, "missing"))
            continue
        obj = S.parse_output(pr)
        if obj is None:
            items.append((r, None, pr, "invalid_json"))
            continue
        items.append((r, obj, pr, S.validate(task, obj, r, lab)))
    return items


def t1_extra(rows: list[dict[str, Any]], preds: dict[str, dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    items = build_items("T1", [r for r in rows if r["task"] == "T1"], preds, labels)
    conf: collections.Counter = collections.Counter()
    for r, obj, _, err in items:
        p = obj["ship_type"] if err is None else "<invalid>"
        if p != r["gold"]["ship_type"]:
            conf[(r["gold"]["ship_type"], p)] += 1
    by_variant = {}
    for v in sorted({it[0]["variant"] for it in items}):
        sub = [it for it in items if it[0]["variant"] == v]
        ok = sum(1 for r, obj, _, err in sub if err is None and obj["ship_type"] == r["gold"]["ship_type"])
        by_variant[v] = {"n": len(sub), "coarse_acc": ok / len(sub) if sub else None}
    return {"by_variant_acc": by_variant,
            "top_confusions_20": [{"gold": g, "pred": p, "count": c} for (g, p), c in conf.most_common(20)],
            "n_confusions": sum(conf.values())}


def t2_by_era(rows: list[dict[str, Any]], preds: dict[str, dict[str, Any]], labels: list[str]) -> dict[str, Any]:
    items = build_items("T2", [r for r in rows if r["task"] == "T2"], preds, labels)
    out = {}
    for era in ("<=1905", "1906-1945", ">=1946", "unknown"):
        sub = [it for it in items if era_of(it[0]) == era]
        if not sub:
            continue
        m = S.score_t2(sub)
        out[era] = {"n": len(sub), "weapon_f1": m["weapon_item"]["f1"], "weapon_precision": m["weapon_item"]["precision"],
                    "weapon_recall": m["weapon_item"]["recall"], "main_caliber_acc": m["main_caliber_acc"],
                    "kind_presence_macro_f1": m["kind_presence_f1"]["macro"], "sensor_f1": m["sensor_kinds"]["f1"]}
    return out


def headline(res: dict[str, Any]) -> dict[str, Any]:
    h: dict[str, Any] = {}
    if "T1" in res:
        m = res["T1"]["metrics"]
        h["T1"] = {"n": res["T1"]["n"], "json_invalid": res["T1"]["json_invalid_rate"], "schema_violation": res["T1"]["schema_violation_rate"],
                   "acc": m["coarse_acc"], "macro_f1": m["macro_f1"], "fine_acc": m["fine_acc"],
                   "top3": m.get("ranking", {}).get("top3_acc"), "ece": m.get("ranking", {}).get("ece")}
    if "T2" in res:
        m = res["T2"]["metrics"]
        h["T2"] = {"n": res["T2"]["n"], "json_invalid": res["T2"]["json_invalid_rate"], "schema_violation": res["T2"]["schema_violation_rate"],
                   "weapon_f1": m["weapon_item"]["f1"], "main_caliber_acc": m["main_caliber_acc"], "main_barrels_mae": m["main_barrels"]["mae"],
                   "kind_presence_macro_f1": m["kind_presence_f1"]["macro"], "sensor_f1": m["sensor_kinds"]["f1"], "sensor_exact": m["sensor_kinds"]["exact_match"]}
    if "T3" in res:
        m = res["T3"]["metrics"]
        o = m["overall"]
        h["T3"] = {"n": res["T3"]["n"], "json_invalid": res["T3"]["json_invalid_rate"], "schema_violation": res["T3"]["schema_violation_rate"],
                   "mape": o.get("mape"), "median_rel_err": o.get("median_rel_err"), "within_10pct": o.get("within_10pct"),
                   "coverage": m.get("interval", {}).get("coverage"), "median_rel_width": m.get("interval", {}).get("median_rel_width")}
    return h


def f(x: Any, nd: int = 3) -> str:
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def to_markdown(runs: dict[str, dict[str, Any]], baselines: dict[str, dict[str, Any]]) -> str:
    L: list[str] = []
    allr = {**runs, **{k: v for k, v in baselines.items()}}
    L += ["## T1 艦種判定", "", "| model | n | acc | macro-F1 | fine acc | top-3 | ECE | JSON不正 |", "|---|---|---|---|---|---|---|---|"]
    for name, r in allr.items():
        h = r["headline"].get("T1")
        if h:
            L.append(f"| {name} | {h['n']} | {f(h['acc'])} | {f(h['macro_f1'])} | {f(h['fine_acc'])} | {f(h['top3'])} | {f(h['ece'])} | {f(h['json_invalid'])} |")
    L += ["", "## T2 武装・センサー", "", "| model | n | weapon F1 | 主砲口径acc | 主砲門数MAE | kind-presence macro F1 | sensor F1 | sensor exact | JSON不正 |", "|---|---|---|---|---|---|---|---|---|"]
    for name, r in allr.items():
        h = r["headline"].get("T2")
        if h:
            L.append(f"| {name} | {h['n']} | {f(h['weapon_f1'])} | {f(h['main_caliber_acc'])} | {f(h['main_barrels_mae'])} | {f(h['kind_presence_macro_f1'])} | {f(h['sensor_f1'])} | {f(h['sensor_exact'])} | {f(h['json_invalid'])} |")
    L += ["", "## T3 欠損諸元", "", "| model | n | MAPE | median rel err | ±10%以内率 | 区間被覆率(10-90%) | 区間相対幅(中央値) | JSON不正 |", "|---|---|---|---|---|---|---|---|"]
    for name, r in allr.items():
        h = r["headline"].get("T3")
        if h:
            L.append(f"| {name} | {h['n']} | {f(h['mape'])} | {f(h['median_rel_err'])} | {f(h['within_10pct'])} | {f(h['coverage'])} | {f(h['median_rel_width'])} | {f(h['json_invalid'])} |")
    for name, r in runs.items():
        ex = r.get("extra", {})
        if "T1" in ex:
            L += ["", f"### {name}: T1 variant 別 accuracy", ""]
            L += [f"- {v}: n={d['n']} acc={f(d['coarse_acc'])}" for v, d in ex["T1"]["by_variant_acc"].items()]
            L += ["", f"### {name}: T1 混同上位", ""]
            L += [f"- {c['gold']} -> {c['pred']}: {c['count']}" for c in ex["T1"]["top_confusions_20"][:12]]
        if "T2_by_era" in ex:
            L += ["", f"### {name}: T2 年代別", "", "| era | n | weapon F1 | P | R | 主砲口径acc | sensor F1 |", "|---|---|---|---|---|---|---|"]
            for era, d in ex["T2_by_era"].items():
                L.append(f"| {era} | {d['n']} | {f(d['weapon_f1'])} | {f(d['weapon_precision'])} | {f(d['weapon_recall'])} | {f(d['main_caliber_acc'])} | {f(d['sensor_f1'])} |")
        if "T3" in r["full"]:
            m = r["full"]["T3"]["metrics"]
            L += ["", f"### {name}: T3 項目別", "", "| field | n | MAPE | median | ±10% | 被覆率 | 区間相対幅 |", "|---|---|---|---|---|---|---|"]
            for fld, d in m["per_field"].items():
                iv = m.get("interval", {}).get("per_field", {}).get(fld, {})
                L.append(f"| {fld} | {d['n']} | {f(d.get('mape'))} | {f(d.get('median_rel_err'))} | {f(d.get('within_10pct'))} | {f(iv.get('coverage'))} | {f(iv.get('median_rel_width'))} |")
            L += ["", "per k (同時に隠した項目数): " + ", ".join(f"{k}: MAPE {f(d.get('mape'))} / ±10% {f(d.get('within_10pct'))} (n={d['n']})" for k, d in m["per_k"].items())]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", type=Path, required=True)
    ap.add_argument("--run", action="append", default=[], help="name=pred_path（複数可）")
    ap.add_argument("--subset-of", default=None, help="この run の予測 id に test を絞る")
    ap.add_argument("--baselines", type=Path, default=None, help="baseline_preds ディレクトリ")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--md", type=Path, default=None)
    args = ap.parse_args()

    test = S.load_test(args.test)
    labels = S.labels_from_manifest(args.test)
    preds = {}
    for spec in args.run:
        name, path = spec.split("=", 1)
        preds[name] = load_preds_any(Path(path))
    if args.subset_of:
        ids = set(preds[args.subset_of])
        test = [r for r in test if r["id"] in ids]
    print(f"test rows: {len(test)} {dict(collections.Counter(r['task'] for r in test))}")

    runs: dict[str, dict[str, Any]] = {}
    for name, p in preds.items():
        tasks = tuple(t for t in ("T1", "T2", "T3") if any(r["task"] == t and r["id"] in p for r in test))
        full = S.score(test, p, labels, tasks=tasks)
        extra: dict[str, Any] = {}
        if "T1" in tasks:
            extra["T1"] = t1_extra(test, p, labels)
        if "T2" in tasks:
            extra["T2_by_era"] = t2_by_era(test, p, labels)
        runs[name] = {"headline": headline(full), "full": full, "extra": extra}
        print(f"== {name}")
        S.print_summary(full)

    baselines: dict[str, dict[str, Any]] = {}
    if args.baselines:
        for path in sorted(args.baselines.glob("*.jsonl")):
            task = path.name[:2].upper()
            p = S.load_preds(path)
            full = S.score(test, p, labels, tasks=(task,))
            baselines[path.stem] = {"headline": headline(full), "full": full}
    out = {"n_test": dict(collections.Counter(r["task"] for r in test)), "runs": runs, "baselines": baselines}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.md:
        args.md.write_text(to_markdown(runs, baselines), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
