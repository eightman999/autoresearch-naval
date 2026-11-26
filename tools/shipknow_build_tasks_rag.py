"""shipknow v1 (RAG): v0 の各例に近傍艦 `neighbors` を添えたデータを作る。

    .venv/bin/python tools/shipknow_build_tasks_rag.py --tokenizer <Qwen3.5 tokenizer.json> [--k 3]
    .venv/bin/python tools/shipknow_build_tasks_rag.py --verify-only        # 出力を v0 から独立に再検証

  * 例の id・分割・assistant 正解・user の元 JSON は v0 (data/navspec/shipknow_v0) と同一。user JSON の末尾に
    "neighbors": [...]（近い順、各要素に距離 "d"）を追加し、system に neighbors の説明を 1 文足す。
  * 近傍検索は tools/shipknow_retriever.py（train 艦級のみ。train 例は自分と同じグループを除外 = leave-group-out。
    距離は例の入力に見えている項目だけで計算）。
  * 艦名・艦級名・URL・レコード id は prompt に出さない（近傍の id は meta.nbr にだけ記録 = 分析用）。

出力: data/navspec/shipknow_v1_rag/{train,valid,test}.jsonl, manifest.json
（records / splits / type_map は v0 のものを参照するので複製しない）
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402
import shipknow_retriever as R  # noqa: E402

NBR_SYSTEM = (
    " The input also has \"neighbors\": reference warship classes similar to this one, retrieved from the training set "
    "(nearest first, \"d\" = distance, smaller is closer). They are hints, not the answer: use them where they fit and correct them where they do not."
)
ALLOWED_NBR_KEYS = {"ship_type", "ship_type_fine", "year", "country", "propulsion", "weapons", "sensors", "armament", "d", *B.NUMERIC_FIELDS, *R.NBR_NEW}


def system_v1(task: str, base: str | None = None) -> str:
    """The v0 system prompt of the task (taken from the v0 example itself, so v0_2's note on the new fields is kept) + the neighbours sentence."""
    return (base or B.SYSTEM[task]) + NBR_SYSTEM


def build_example(e: dict[str, Any], pool: R.Pool, params: dict[str, Any], group_of: dict[str, str], k: int) -> dict[str, Any]:
    task = e["task"]
    view = json.loads(e["messages"][1]["content"])
    nb = pool.search(view, task, params[task], exclude_group=group_of[e["row"]], k=k)
    payloads = [R.neighbor_payload(pool.recs[j], task, view, d) for j, d in nb]
    user = e["messages"][1]["content"]
    assert user.endswith("}")
    user_v1 = user[:-1] + ',"neighbors":' + R.jdump(payloads) + "}"
    out = dict(e)
    out["messages"] = [{"role": "system", "content": system_v1(task, e["messages"][0]["content"])}, {"role": "user", "content": user_v1}, e["messages"][2]]
    out["meta"] = {**e.get("meta", {}), "nbr": [pool.ids[j] for j, _ in nb], "nbr_d": [round(d, 6) for _, d in nb]}
    return out


def pct(xs: list[int], p: float) -> int:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0


# ---------------------------------------------------------------------------
# independent verification (re-derives everything from v0 records / splits / v0 examples)
# ---------------------------------------------------------------------------
def verify_dir(out_dir: Path, v0_dir: Path, k: int | None = None) -> dict[str, Any]:
    recs = {r["id"]: r for r in R.read_jsonl(v0_dir / "records.jsonl")}
    sp = json.loads((v0_dir / "splits.json").read_text(encoding="utf-8"))
    assign, group_of = sp["assignments"], sp["group_of"]
    train_ids = {i for i, s in assign.items() if s == "train"}
    train_groups = {group_of[i] for i in train_ids}
    rep: dict[str, Any] = {"splits": {}}
    id_pat = re.compile(r"\bc\d{5}\b")
    bad: list[str] = []
    for split in ("train", "valid", "test"):
        v0 = {e["id"]: e for e in R.read_jsonl(v0_dir / f"{split}.jsonl")}
        v1 = R.read_jsonl(out_dir / f"{split}.jsonl")
        r: dict[str, Any] = {"n": len(v1), "same_ids_as_v0": {e["id"] for e in v1} == set(v0), "same_order_as_v0": [e["id"] for e in v1] == list(v0)}
        n_same_group = n_not_train = n_self = n_total = n_mismatch_view = n_mismatch_ans = n_idleak = n_badkeys = n_rerender = 0
        n_nbr_hist: collections.Counter = collections.Counter()
        for e in v1:
            o = v0[e["id"]]
            if e["messages"][2] != o["messages"][2] or e["task"] != o["task"] or e["variant"] != o["variant"] or e["row"] != o["row"] or e["group"] != o["group"]:
                n_mismatch_ans += 1
            ou = o["messages"][1]["content"]
            nu = e["messages"][1]["content"]
            if not nu.startswith(ou[:-1] + ',"neighbors":'):
                n_mismatch_view += 1
            payloads = json.loads(nu)["neighbors"]
            own_group = group_of[e["row"]]
            ids = e["meta"]["nbr"]
            n_nbr_hist[len(ids)] += 1
            assert len(ids) == len(payloads)
            view = json.loads(ou)
            for nid, pl, d in zip(ids, payloads, e["meta"]["nbr_d"]):
                n_total += 1
                n_same_group += group_of[nid] == own_group
                n_self += nid == e["row"]
                n_not_train += nid not in train_ids
                if set(pl) - ALLOWED_NBR_KEYS:
                    n_badkeys += 1
                if id_pat.search(json.dumps(pl)):
                    n_idleak += 1
                # the text shown must be exactly the payload of the recorded neighbour
                fresh = R.neighbor_payload(recs[nid], e["task"], view, None)
                shown = {kk: vv for kk, vv in pl.items() if kk != "d"}
                if R.jdump(fresh) != R.jdump(shown) or abs(pl.get("d", -9) - d) > 0.0051:
                    n_rerender += 1
            if id_pat.search(nu) and not id_pat.search(ou):
                n_idleak += 1
        r.update({"neighbors_total": n_total, "neighbors_per_example": dict(n_nbr_hist), "nbr_same_group_as_example": n_same_group, "nbr_is_self": n_self,
                  "nbr_not_in_train_split": n_not_train, "mismatch_assistant_or_meta_vs_v0": n_mismatch_ans, "mismatch_user_prefix_vs_v0": n_mismatch_view,
                  "payload_unexpected_keys": n_badkeys, "id_pattern_in_prompt": n_idleak, "payload_mismatch_vs_record": n_rerender})
        rep["splits"][split] = r
        for key in ("nbr_same_group_as_example", "nbr_is_self", "nbr_not_in_train_split", "mismatch_assistant_or_meta_vs_v0", "mismatch_user_prefix_vs_v0",
                    "payload_unexpected_keys", "id_pattern_in_prompt", "payload_mismatch_vs_record"):
            if r[key]:
                bad.append(f"{split}.{key}={r[key]}")
        if not (r["same_ids_as_v0"] and r["same_order_as_v0"]):
            bad.append(f"{split}.ids_differ")
    # split-level facts
    rep["train_groups_disjoint_from_valid_test"] = not (train_groups & {group_of[i] for i, s in assign.items() if s != "train"})
    if not rep["train_groups_disjoint_from_valid_test"]:
        bad.append("train/valid-test group overlap")
    rep["ok"] = not bad
    rep["problems"] = bad
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v0-dir", type=Path, default=R.V0_DIR)
    ap.add_argument("--out-dir", type=Path, default=R.V1_DIR)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--tokenizer", type=Path, default=None, help="Qwen3.5 tokenizer.json（無ければ chars/3.2 の概算）")
    ap.add_argument("--tok-per-sec", type=float, default=2800.0, help="学習スループット（v0 実測 2.8k tok/s）")
    ap.add_argument("--params", type=Path, default=None, help="retriever_params.json (default: the one in shipknow_v1_rag)")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="デバッグ: 各 split 先頭 N 例")
    args = ap.parse_args()

    if args.verify_only:
        rep = verify_dir(args.out_dir, args.v0_dir)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        (args.out_dir / "leakage_check.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        raise SystemExit(0 if rep["ok"] else 1)

    pool, assign, group_of = R.load_pool(args.v0_dir)
    params = R.load_params(args.params)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    tok, tok_note = B.load_tokenizer(args.tokenizer)
    stats: dict[str, Any] = {}
    for split in ("train", "valid", "test"):
        rows = R.read_jsonl(args.v0_dir / f"{split}.jsonl")
        if args.limit:
            rows = rows[: args.limit]
        out = [build_example(e, pool, params, group_of, args.k) for e in rows]
        with (args.out_dir / f"{split}.jsonl").open("w", encoding="utf-8") as fh:
            for e in out:
                fh.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
        st: dict[str, Any] = {}
        for task in ("T1", "T2", "T3"):
            sel = [e for e in out if e["task"] == task]
            toks = [B.count_tokens(tok, e) for e in sel]
            tt = [a for a, _ in toks]
            st[task] = {"examples": len(sel), "tokens_total": sum(tt), "tokens_avg": round(statistics.mean(tt), 1) if tt else 0,
                        "tokens_p95": pct(tt, 0.95), "tokens_max": max(tt) if tt else 0}
        st["all"] = {"examples": len(out), "tokens_total": sum(st[t]["tokens_total"] for t in ("T1", "T2", "T3")),
                     "tokens_max": max(st[t]["tokens_max"] for t in ("T1", "T2", "T3"))}
        stats[split] = st
        print(split, {t: (st[t]["tokens_avg"], st[t]["tokens_p95"], st[t]["tokens_max"]) for t in ("T1", "T2", "T3")}, st["all"], flush=True)
    train_tokens = stats["train"]["all"]["tokens_total"]
    v0m = json.loads((args.v0_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest = {
        "name": args.out_dir.name, "derived_from": f"{args.v0_dir.name} (same example ids / splits / assistant targets)", "k": args.k,
        "retriever": {"module": "tools/shipknow_retriever.py", "params": params, "pool": "train split class records only",
                      "train_examples": "leave-group-out (own splits.json group excluded)", "distance_inputs": "only fields visible in the example's user JSON"},
        "examples": v0m["examples"], "token_stats": stats, "tokenizer": tok_note,
        "train_time_estimate": {"train_tokens": train_tokens, "assumed_tok_per_sec": args.tok_per_sec, "hours": round(train_tokens / args.tok_per_sec / 3600, 2)},
        "coarse_types": v0m["coarse_types"], "weapon_kinds": v0m["weapon_kinds"], "t3_fields": v0m["t3_fields"], "rounding_rules": v0m["rounding_rules"],
        "system_prompts": {t: system_v1(t, v0m["system_prompts"][t] if "system_prompts" in v0m else None) for t in ("T1", "T2", "T3")},
        "never_in_prompts": ["ship/class names", "raw TYPE", "source_url", "record ids", "Naval_service", "Project_history"],
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    rep = verify_dir(args.out_dir, args.v0_dir) if not args.limit else {"ok": None, "note": "limit mode"}
    (args.out_dir / "leakage_check.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print("train tokens", train_tokens, "est hours", manifest["train_time_estimate"]["hours"], "leakage ok:", rep["ok"])
    if rep["ok"] is False:
        print(json.dumps(rep, indent=1))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
