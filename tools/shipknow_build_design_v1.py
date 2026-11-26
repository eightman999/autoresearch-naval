"""shipknow 設計データ design-v1 の生成パイプライン（会話エピソード形式。LLM・GPU・他マシンは使わない。seed 固定で再現可能）。

    # パイロット（約 2,000 エピソード）
    .venv/bin/python tools/shipknow_build_design_v1.py --out data/navspec/shipknow_design_v1_pilot --n-total 2000
    # 全量（約 10 万。出力先を省略すると data/navspec/shipknow_design_v1）
    .venv/bin/python tools/shipknow_build_design_v1.py --n-total 100000 --procs 10
    # 検査（品質・重複・統計・サンプル・README）
    .venv/bin/python tools/shipknow_check_design_v1.py --dir data/navspec/shipknow_design_v1_pilot

入力: data/navspec/shipknow_v0_2/{records.jsonl, splits.json}（艦級グループ単位の分割を継承）、nsdb/NSDB.db（mode=ro。艦歴・艦名）、
      data/navspec/shipknow_design_v0/validator_thresholds.json、既存の design-v0 / 評価 v1（重複の除外に読むだけ）。
出力: <out>/{train,valid,test}.jsonl, manifest.json（検査は shipknow_check_design_v1 が stats.json / overlap_check.json / quality_check.json / samples/ / README.md を作る）。

JSONL 1 行 = 1 会話エピソード: {"id": "dv1_xxxxxxxx", "category", "messages": [system, user, assistant, ...], "metadata": {...}}。
カテゴリ（全量の目標比率）: single 30% / modify 20% / repair 25% / infeasible 10% / ood 5% / context 10%。
各カテゴリの生成方式はそれぞれのモジュールの docstring を参照:
  single   shipknow_design_v1_gen_basic（元艦の実設計 + 拡張キー込みの要求）
  modify   shipknow_design_v1_gen_basic + shipknow_design_v1_ops（近い実艦 / 物理スケーリング。関連項目を連動）
  repair   shipknow_design_v1_repair（違反の生成 + validator 自動修正ループ）
  infeasible shipknow_design_v1_infeasible（relaxed / infeasible の判定基準 decide_status）
  ood      shipknow_design_v1_ood（前提が変えるものだけを変える）
  context  shipknow_design_v1_context（最新の要求を優先）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import sys
import time
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_design_v1_context as CX  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402
import shipknow_design_v1_data as DD  # noqa: E402
import shipknow_design_v1_gen_basic as G  # noqa: E402
import shipknow_design_v1_infeasible as I  # noqa: E402
import shipknow_design_v1_ood as OD  # noqa: E402
import shipknow_design_v1_repair as RP  # noqa: E402

GENERATORS = {"single": G.gen_single, "modify": G.gen_modify, "repair": RP.gen_repair, "infeasible": I.gen_infeasible, "ood": OD.gen_ood, "context": CX.gen_context}
SPLIT_FRAC = {"valid": 0.10, "test": 0.10}  # train は残り
CHUNK = 200
MAX_ATTEMPT_FACTOR = 12.0

_D: DD.Data | None = None


def plan(n_total: int) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    cats = {c: int(round(n_total * K.CATEGORY_RATIO[c])) for c in K.CATEGORIES}
    cats["single"] += n_total - sum(cats.values())
    for c, n in cats.items():
        nv, nt = int(round(n * SPLIT_FRAC["valid"])), int(round(n * SPLIT_FRAC["test"]))
        out[(c, "valid")], out[(c, "test")], out[(c, "train")] = nv, nt, n - nv - nt
    return out


def _run_chunk(task: tuple[str, str, int, int]) -> tuple[str, str, list[tuple[int, dict[str, Any] | None]], dict[str, int], list[str]]:
    cat, split, i0, i1 = task
    G.FAIL.clear()
    out: list[tuple[int, dict[str, Any] | None]] = []
    errs: list[str] = []
    fn = GENERATORS[cat]
    for i in range(i0, i1):
        try:
            ep = fn(_D, split, i)
        except Exception as e:  # noqa: BLE001
            G.fail(f"exception:{cat}:{type(e).__name__}")
            if len(errs) < 3:
                errs.append(f"{cat}/{split}/{i}: {traceback.format_exc(limit=4)}")
            ep = None
        out.append((i, ep))
    return cat, split, out, dict(G.FAIL), errs


def request_keys(ep: dict[str, Any]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """エピソードの全ての request ターンの正規化要求文・構造化要求（重複と test 専用の検査に使う）。"""
    texts, reqs, rq = [], [], []
    for m in ep["messages"]:
        if m["role"] == "user":
            u = json.loads(m["content"])
            if "req" in u and "request" in u:
                texts.append(DD.norm_text(u["request"]))
                reqs.append(DD.canon_req(u["req"]))
                rq.append(u["req"])
    return texts, reqs, rq


def make_id(cat: str, split: str, idx: int, seed: int, used: set[str]) -> str:
    salt = 0
    while True:
        h = hashlib.sha256(f"{cat}|{split}|{idx}|{seed}|{salt}".encode()).hexdigest()[:8]
        if h not in used:
            used.add(h)
            return "dv1_" + h
        salt += 1


def generate(n_total: int, procs: int, oversample: float, log=print, only_split: str | None = None) -> dict[str, Any]:
    global _D
    t0 = time.time()
    _D = DD.Data()
    forbid = DD.load_forbidden()
    log(f"data loaded in {time.time() - t0:.1f}s  eligible by split: { {s: len(_D.by_split[s]) for s in K.SPLITS} }  gold rejected: {_D.n_gold_rejected}")
    quotas = plan(n_total)
    if only_split:  # 全量の 1 つの split だけ作る（小さい split が全量で目標件数に届くかの確認用）
        quotas = {k: (q if k[1] == only_split else 0) for k, q in quotas.items()}
    kept: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {k: [] for k in quotas}
    next_idx = {k: 0 for k in quotas}
    attempts = {k: 0 for k in quotas}
    drops = {"dup_text": 0, "dup_req": 0, "forbidden_text": 0, "forbidden_req": 0, "split_leak": 0, "heldout_combo_in_train_valid": 0}
    seen_text: set[str] = set()
    seen_req: set[str] = set()
    fail_total: dict[str, int] = {}
    errors: list[str] = []
    ctx = mp.get_context("fork")
    wave = 0
    while True:
        tasks = []
        for (cat, split), q in quotas.items():
            have = len(kept[(cat, split)])
            if have >= q or attempts[(cat, split)] >= MAX_ATTEMPT_FACTOR * q + 50:
                continue
            need = q - have
            n_try = int(math.ceil(need * (oversample if wave == 0 else 2.5))) + 6
            i0 = next_idx[(cat, split)]
            next_idx[(cat, split)] += n_try
            attempts[(cat, split)] += n_try
            for a in range(i0, i0 + n_try, CHUNK):
                tasks.append((cat, split, a, min(i0 + n_try, a + CHUNK)))
        if not tasks:
            break
        wave += 1
        tw = time.time()
        if procs > 1:
            with ctx.Pool(procs) as pool:
                results = pool.map(_run_chunk, tasks, chunksize=1)
        else:
            results = [_run_chunk(t) for t in tasks]
        got: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
        for cat, split, out, fail, errs in results:
            for k, v in fail.items():
                fail_total[k] = fail_total.get(k, 0) + v
            errors += errs
            got.setdefault((cat, split), []).extend((i, ep) for i, ep in out if ep is not None)
        for key in sorted(got, key=lambda k: (K.CATEGORIES.index(k[0]), k[1])):
            q = quotas[key]
            split = key[1]
            for i, ep in sorted(got[key], key=lambda x: x[0]):
                if len(kept[key]) >= q:
                    break
                texts, reqs, rq = request_keys(ep)
                if split != "test" and any(K.is_heldout_combo(q) for q in rq):
                    drops["heldout_combo_in_train_valid"] += 1
                    continue
                if any(t in forbid["texts"] for t in texts):
                    drops["forbidden_text"] += 1
                    continue
                if any(r in forbid["reqs"] for r in reqs):
                    drops["forbidden_req"] += 1
                    continue
                if len(set(texts)) < len(texts):
                    drops["dup_text"] += 1
                    continue
                if any(t in seen_text for t in texts):
                    drops["dup_text"] += 1
                    continue
                if any(r in seen_req for r in reqs):
                    drops["dup_req"] += 1
                    continue
                if not G.split_check(_D, ep):
                    drops["split_leak"] += 1
                    continue
                seen_text.update(texts)
                seen_req.update(reqs)
                kept[key].append((i, ep))
        log(f"wave {wave}: {time.time() - tw:.1f}s  " + "  ".join(f"{c}/{s}:{len(kept[(c, s)])}/{quotas[(c, s)]}" for (c, s) in quotas if len(kept[(c, s)]) < quotas[(c, s)]) + (" (all quotas met)" if all(len(kept[k]) >= quotas[k] for k in quotas) else ""))
        if wave >= 24:
            break
    # ids and rows
    used: set[str] = set()
    rows: dict[str, list[dict[str, Any]]] = {s: [] for s in K.SPLITS}
    for (cat, split) in sorted(quotas, key=lambda k: (K.CATEGORIES.index(k[0]), k[1])):
        for i, ep in kept[(cat, split)]:
            meta = dict(ep["meta"])
            meta.update({"split": split, "subtype": ep["subtype"], "gen_idx": i, "seed": G.SEED})
            rows[split].append({"id": make_id(cat, split, i, G.SEED, used), "category": cat, "messages": ep["messages"], "metadata": meta})
    for s in rows:
        rows[s].sort(key=lambda r: r["id"])
    return {"rows": rows, "quotas": quotas, "attempts": attempts, "drops": drops, "fail": fail_total, "errors": errors, "elapsed_sec": time.time() - t0, "waves": wave,
            "gold_rejected": _D.n_gold_rejected, "n_eligible": {s: len(_D.by_split[s]) for s in K.SPLITS}}


def write_outputs(out: Path, res: dict[str, Any], args: argparse.Namespace) -> None:
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, Any] = {}
    for s, rows in res["rows"].items():
        with (out / f"{s}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        c: dict[str, int] = {}
        for r in rows:
            c[r["category"]] = c.get(r["category"], 0) + 1
        counts[s] = {"total": len(rows), **{k: c.get(k, 0) for k in K.CATEGORIES}}
    manifest = {
        "name": out.name, "version": "design-v1", "seed": G.SEED, "n_total_target": args.n_total, "n_total": sum(c["total"] for c in counts.values()),
        "counts": counts, "category_ratio_target": K.CATEGORY_RATIO, "split_fraction": {"train": 1 - sum(SPLIT_FRAC.values()), **SPLIT_FRAC},
        "quotas": {f"{c}/{s}": q for (c, s), q in res["quotas"].items()}, "attempts": {f"{c}/{s}": q for (c, s), q in res["attempts"].items()},
        "dropped": res["drops"], "generation_failures": dict(sorted(res["fail"].items(), key=lambda x: -x[1])), "generation_errors": res["errors"],
        "elapsed_sec": round(res["elapsed_sec"], 1), "procs": args.procs, "waves": res["waves"], "oversample": args.oversample,
        "source": "shipknow_v0_2 records + splits (class-group split inherited) + nsdb/NSDB.db (read-only) + design_v0 validator_thresholds.json; no LLM",
        "eligible_by_split": res["n_eligible"], "gold_rejected_by_validator": res["gold_rejected"],
        "system_prompt": K.SYSTEM_V1, "output_schema": {"keys": K.OUT_KEYS, "status": list(K.STATUSES)},
        "status_criteria": I.__doc__.split("■ 「不可能」")[0].strip(), "relax_gap_max": I.RELAX_GAP_MAX, "max_relaxed_conflicts": I.MAX_RELAXED_CONFLICTS,
        "holdout": {"combo_mod": K.HELDOUT_COMBO_MOD, "country_pair_mod": DD.HELDOUT_COUNTRY_PAIR_MOD, "year_pair_mod": DD.HELDOUT_YEAR_PAIR_MOD, "violation_pair_mod": RP.HELDOUT_KIND_PAIR_MOD, "infeasible_families": sorted(I.HOLDOUT_FAMILIES),
                    "doctrines": sorted(OD.DOCTRINES_TEST_ONLY), "fictional_names": DD.FICTIONAL, "context_variants": [v for v, _ in CX.TEST_ONLY]},
        "files": {"train": "train.jsonl", "valid": "valid.jsonl", "test": "test.jsonl"},
        "never_in_input": ["target ship / class names", "target Naval_service / Project_history / Modernizations", "source_url", "year_in_navy"],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=K.OUT_DIR)
    ap.add_argument("--n-total", type=int, default=100000)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--oversample", type=float, default=1.4, help="attempts per quota in the first wave (duplicates / held-out filters / failed generations are replaced in later waves)")
    ap.add_argument("--only-split", choices=K.SPLITS, default=None, help="generate only this split's share of --n-total (feasibility check of a full run's smaller splits)")
    ap.add_argument("--seed", type=int, default=None, help="override the generator seed (default: fixed constant in shipknow_design_v1_gen_basic)")
    args = ap.parse_args()
    if args.seed is not None:
        G.SEED = args.seed
    res = generate(args.n_total, args.procs, args.oversample, only_split=args.only_split)
    write_outputs(args.out, res, args)
    tot = sum(len(v) for v in res["rows"].values())
    print(json.dumps({"out": str(args.out), "episodes": tot, "elapsed_sec": round(res["elapsed_sec"], 1), "dropped": res["drops"], "failures_top": dict(sorted(res["fail"].items(), key=lambda x: -x[1])[:8])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
