"""shipknow v1: retriever そのものを答えにする非 LLM ベースライン（LLM に見せる近傍と同じ retriever）。

    .venv/bin/python tools/shipknow_baselines_rag.py [--dir data/navspec/shipknow_v1_rag]

  T1  rag_top1 = 最近傍の艦種 / rag_maj3 = 近傍 3 の多数決（同数は近い方）/ rag_maj8 = 近傍 8 の多数決
      （ranking = 投票数の平滑化確率。top-3・ECE も採点できる）
  T2  rag_top1 = 最近傍の武装・センサー / rag_medoid3 = 近傍 3 のうち他と最も一致する艦
  T3  rag_top1 / rag_median3 / rag_median8 = 近傍の中央値（項目ごと）。samples = 近傍の値（区間は近傍の 10-90% 分位）

近傍は shipknow_retriever.Pool.search（train 艦級のみ・見える項目だけで距離）で test 例ごとに引く。k=3 は prompt に出したものと
同一であることを確認する（meta.nbr との一致を assert）。採点は tools/shipknow_score.py（v0 ベースラインと同じ）。
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
import numpy as np  # noqa: E402
import shipknow_build_tasks as B  # noqa: E402
import shipknow_retriever as R  # noqa: E402
import shipknow_score as S  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULT = ROOT / "docs" / "shipknow_v1" / "baselines_rag_test.json"


def run(split_dir: Path, v0_dir: Path, k_list: tuple[int, ...] = (1, 3, 8), params_path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    pool, assign, group_of = R.load_pool(v0_dir)
    params = R.load_params(params_path)
    labels = json.loads((split_dir / "manifest.json").read_text(encoding="utf-8"))["coarse_types"]
    test = S.load_test(split_dir / "test.jsonl")
    out: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for e in test:
        task, view = e["task"], e["inp"]
        kmax = max(k_list)
        nb = pool.search(view, task, params[task], exclude_group=group_of[e["row"]], k=kmax)
        assert [pool.ids[j] for j, _ in nb[:3]] == e["meta"]["nbr"][: len(nb[:3])], "baseline neighbours differ from the prompt neighbours"
        recs = [pool.recs[j] for j, _ in nb]
        if task == "T1":
            for name, k in (("t1_rag_top1", 1), ("t1_rag_maj3", 3), ("t1_rag_maj8", 8)):
                sub = recs[:k]
                labs = [r["ship_type"] for r in sub]
                cnt = collections.Counter(labs)
                best = max(cnt.values())
                top = next(l for l in labs if cnt[l] == best)
                fine = next(r["ship_type_fine"] for r in sub if r["ship_type"] == top)
                rank = {l: float(np.log((cnt.get(l, 0) + 0.5) / (len(sub) + 0.5 * len(labels))) + (1e-3 if l == labs[0] else 0.0)) for l in labels}
                out[name].append({"id": e["id"], "output": {"ship_type": top, "ship_type_fine": fine}, "ranking": rank})
        elif task == "T2":
            want = view.get("want", ["weapons", "sensors"])

            def o_of(r: dict[str, Any]) -> dict[str, Any]:
                o: dict[str, Any] = {"weapons": B.compact_weapons(r["weapons"])}
                if "sensors" in want:
                    o["sensors"] = list(r.get("sensor_kinds") or [])
                return o

            out["t2_rag_top1"].append({"id": e["id"], "output": o_of(recs[0])})
            sub = recs[:3]
            comp = [B.compact_weapons(r["weapons"]) for r in sub]
            best_i, best_s = 0, -1.0
            for a in range(len(sub)):
                sc = sum(S.prf(*S.match_items(comp[b], comp[a]))[2] for b in range(len(sub)) if b != a)
                if sc > best_s:
                    best_i, best_s = a, sc
            out["t2_rag_medoid3"].append({"id": e["id"], "output": o_of(sub[best_i])})
        else:
            hidden = view["predict"]
            for name, k in (("t3_rag_top1", 1), ("t3_rag_median3", 3), ("t3_rag_median8", 8)):
                sub = recs[:k]
                o = {}
                for f in hidden:
                    vals = [r[f] for r in sub if r.get(f)]
                    o[f] = B.rnd(f, float(np.median(vals)))
                samples = [{f: B.rnd(f, r[f]) for f in hidden if r.get(f)} for r in sub]
                out[name].append({"id": e["id"], "output": o, "samples": samples})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=R.V1_DIR)
    ap.add_argument("--v0-dir", type=Path, default=R.V0_DIR)
    ap.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    ap.add_argument("--params", type=Path, default=None, help="retriever_params.json (default: the one in shipknow_v1_rag)")
    args = ap.parse_args()

    preds = run(args.dir, args.v0_dir, params_path=args.params)
    test = S.load_test(args.dir / "test.jsonl")
    labels = S.labels_from_manifest(args.dir / "test.jsonl")
    results: dict[str, Any] = {"n_test": dict(collections.Counter(r["task"] for r in test)), "baselines": {}}
    (args.dir / "baseline_preds").mkdir(exist_ok=True)
    for name, plist in preds.items():
        with (args.dir / "baseline_preds" / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for p in plist:
                fh.write(json.dumps(p, ensure_ascii=False) + "\n")
        task = name[:2].upper()
        res = S.score(test, {p["id"]: p for p in plist}, labels, tasks=(task,))
        results["baselines"][name] = res[task]
        print("==", name)
        S.print_summary({task: res[task]})
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote", args.result)


if __name__ == "__main__":
    main()
