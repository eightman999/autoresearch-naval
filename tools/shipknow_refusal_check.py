#!/usr/bin/env python3
"""design-v1 の拒否（status = relaxed / infeasible）を実艦で照合し、根拠のない拒否を捨てて再要求するフィルタ。

■ 判定（`check_output`、LLM は使わない）
  status が relaxed / infeasible の出力について、train の実艦（同じ coarse 艦種、要求年 + YEAR_AHEAD 年以内に設計、validator 合格の gold 候補）のうち、
  要求の全制約を満たすもの（witness）を探す。これは design-v1 の学習データで「不可能」を決めた oracle（shipknow_design_v1_infeasible.real_satisfying）と
  同じ基準なので、witness があれば「全制約を同時に満たせる」ことが実艦で示され、拒否には根拠が無い。
    verdict = feasible     … status = feasible（照合しない）
              supported    … 拒否で、witness が 0 隻（拒否を受け入れる）
              unsupported  … 拒否で、witness が 1 隻以上（拒否を捨てて再要求する）
              false_claims … 拒否で、witness は 0 隻だが、conflicts の主張が全て誤り（下の in_request / alone_ok で反証できる）。
                             --reject-false-claims のときだけ再要求する（eval_v1 の s1200 では E の正しい拒否の約 1 割もここに入るので既定は受け入れ）
              invalid      … JSON として読めない / status が無い（このフィルタの対象外。そのまま返す）
  conflicts の各主張には、診断として次を付ける（verdict は変えない）:
    in_request … その constraint が要求の制約 id（constraint_ids）にあるか
    blocking   … その制約だけを外すと witness が見つかるか（witness 0 隻の拒否で、その制約が実際に満たせない原因か）
    alone_ok   … 艦種・年代・排水量の制約にその制約 1 つだけを足した要求を満たす実艦があるか（「この大きさ・年代では無理」という主張の反証）。
                 排水量の制約そのものへの主張は None（反証しない）
■ 再要求
  unsupported の行について、元の会話 + 拒否した assistant 出力 + validator 形式の user ターン（repair エピソードと同じ
  {"validator": {...}, "instruction": ...} の形）を作る。shipknow_infer_hf.py --multi-turn-eval でそのまま生成できる
  （最後に空の assistant を置き、metadata.train_turns でその位置を指す）。
  user ターンには witness の数と、最大 --show-witness 隻の主要値（排水量・速力のように要求に関わる値だけ）を載せる。
  false_claims の再要求は「全部満たせる」とは言わず、誤った主張だけを指摘して見直させる（INSTRUCTION_CLAIMS）。

使い方::

    # 1) 照合と再要求ファイルの作成
    python tools/shipknow_refusal_check.py check --problems data/navspec/shipknow_design_eval_v1/all_v1sys.jsonl \\
        --pred docs/shipknow_design_v1_compare/preds/evalv1_s1200_s1001 --out-dir docs/shipknow_refusal_check/s1200
    # 2) llm_master で再要求を生成（RTX 3060）
    python shipknow/tools/shipknow_infer_hf.py --multi-turn-eval --test <out-dir>/rerequest.jsonl --adapter <design_v1 step1200> \\
        --out <out-dir>/rerequest_preds --sample-seed 1001 --temperature 0.7 --batch-size 16 --max-new-tokens 448
    # 3) 元の予測へ差し戻し（unsupported の行だけ再要求の出力に置き換える）
    python tools/shipknow_refusal_check.py merge --pred <元の予測> --verdicts <out-dir>/verdicts.jsonl \\
        --rerequest <out-dir>/rerequest.jsonl --rerequest-pred <out-dir>/rerequest_preds --out <out-dir>/merged
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_design_common as C  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402
import shipknow_design_v1_data as DD  # noqa: E402

YEAR_AHEAD = 3  # shipknow_design_v1_infeasible.YEAR_AHEAD と同じ
REFUSAL = {"relaxed", "infeasible"}
INSTRUCTION = "All requirements can be met together. Give a feasible design that meets every requirement."
INSTRUCTION_CLAIMS = "The listed conflicts are not supported by real ships. Re-check the request: meet every requirement if possible, and cite only conflicts that really exist."
SIZE_KEYS = ("max_displacement_t", "min_displacement_t")
CODE = "refusal_unsupported"
# witness の要約に載せる値（要求キー -> 設計のフィールド）
_SUMMARY_FIELDS = [("min_speed_kn", "speed_kn"), ("max_speed_kn", "speed_kn"), ("max_complement", "complement"), ("min_endurance_nm", "endurance_nm")]


# --------------------------------------------------------------------------------------------------
# oracle index
# --------------------------------------------------------------------------------------------------
def load_index(split: str = "train") -> K.DesignIndex:
    """shipknow_design_v1_data.Data の idx[split] と同じ実艦集合（validator 合格の gold 候補）。NSDB は読まない。"""
    recs = [json.loads(l) for l in (C.V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assign = json.loads((C.V0_DIR / "splits.json").read_text(encoding="utf-8"))["assignments"]
    keep, designs = [], {}
    for r in recs:
        if assign.get(r["id"]) != split or not C.design_eligible(r):
            continue
        d = C.design_from_record(r)
        if K.check_design({"year": C.request_year(r), "country": r["country"]}, d)["ok"] and DD.sane_geometry(d):
            keep.append(r)
            designs[r["id"]] = d
    return K.DesignIndex(keep, designs)


def witnesses(idx: K.DesignIndex, req: dict[str, Any]) -> list[int]:
    return idx.satisfying(req, year_hi=req["year"] + YEAR_AHEAD)


# --------------------------------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------------------------------
def parse_loose(text: Any) -> dict[str, Any] | None:
    """モデル出力の JSON を読む（前後に余計な文字があれば最初の object を取る。unwrap_v1.py と同じ緩さ）。"""
    if not isinstance(text, str):
        return None
    try:
        o = json.loads(text)
    except Exception:  # noqa: BLE001
        s = text.find("{")
        if s < 0:
            return None
        try:
            o = json.JSONDecoder().raw_decode(text[s:])[0]
        except Exception:  # noqa: BLE001
            return None
    return o if isinstance(o, dict) else None


def check_output(idx: K.DesignIndex, req: dict[str, Any], obj: dict[str, Any] | None) -> dict[str, Any]:
    if obj is None or "status" not in obj:
        return {"verdict": "invalid", "status": None}
    status = obj["status"]
    if status not in REFUSAL:
        return {"verdict": "feasible", "status": status}
    wit = witnesses(idx, req)
    ids = K.constraint_ids(req)
    claims = []
    for c in obj.get("conflicts") or []:
        cid = c.get("constraint") if isinstance(c, dict) else None
        inreq = cid in ids
        blocking = alone = None
        if inreq and not wit:
            blocking = bool(witnesses(idx, K.drop_constraint(req, cid)))
            if cid not in SIZE_KEYS:
                alone = bool(witnesses(idx, K.apply_relaxations(req, [{"constraint": x} for x in ids if x != cid and x not in SIZE_KEYS])))
        claims.append({"constraint": cid, "in_request": inreq, "blocking": blocking, "alone_ok": alone})
    if wit:
        verdict = "unsupported"
    elif claims and all(not c["in_request"] or c["alone_ok"] for c in claims):
        verdict = "false_claims"
    else:
        verdict = "supported"
    return {"verdict": verdict, "status": status, "n_witness": len(wit), "witness_ids": [idx.ids[i] for i in wit[:10]], "claims": claims}


def witness_summary(idx: K.DesignIndex, req: dict[str, Any], i: int) -> str:
    rec, d = idx.recs[i], idx.designs[i]
    basis = req.get("displacement_basis", "normal")
    parts = [f"{rec['country']} {d['ship_type']} designed {C.request_year(rec)}"]
    disp = d.get(f"displacement_{basis}_t")
    if disp is not None:
        parts.append(f"{K.num(disp)} t ({basis})")
    seen = set()
    for key, f in _SUMMARY_FIELDS:
        if req.get(key) is not None and f not in seen and d.get(f) is not None:
            seen.add(f)
            parts.append(f"{f} {K.num(d[f])}")
    return ", ".join(parts)


def feedback_turn(idx: K.DesignIndex, req: dict[str, Any], verdict: dict[str, Any], show_witness: int = 2) -> str:
    """repair エピソードの validator ターンと同じ形（K.jdump の {"validator": {...}, "instruction": ...}）。"""
    if verdict["verdict"] == "false_claims":
        viols = []
        for c in verdict["claims"]:
            why = "not a requirement of this request" if not c["in_request"] else "real ships of this type, size and year meet it"
            viols.append({"code": "conflict_unsupported", "field": str(c["constraint"]), "detail": why})
        return K.jdump({"validator": {"valid": False, "violations": viols}, "instruction": INSTRUCTION_CLAIMS})
    pos = {rid: j for j, rid in enumerate(idx.ids)}
    ex = [witness_summary(idx, req, pos[w]) for w in verdict["witness_ids"][:show_witness]]
    detail = f"{verdict['n_witness']} real ship class(es) meet every requirement together"
    if ex:
        detail += "; e.g. " + "; ".join(ex)
    viols = [{"code": CODE, "field": "status", "detail": detail}]
    for c in verdict["claims"]:
        if c["constraint"] is not None:
            viols.append({"code": CODE, "field": str(c["constraint"]), "detail": "this requirement can be met; do not relax it"})
    return K.jdump({"validator": {"valid": False, "violations": viols}, "instruction": INSTRUCTION})


def rerequest_episode(problem: dict[str, Any], text: str, feedback: str) -> dict[str, Any]:
    msgs = [m for m in problem["messages"] if m["role"] != "assistant"]
    msgs = msgs + [{"role": "assistant", "content": text}, {"role": "user", "content": feedback}, {"role": "assistant", "content": ""}]
    return {"id": problem["id"], "category": problem.get("category") or "D", "messages": msgs, "metadata": {"train_turns": [len(msgs) - 1]}}


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------
def read_jsonl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_jsonl(p: Path, rows: list[dict[str, Any]]) -> None:
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def category_of(problem: dict[str, Any]) -> str:
    return problem.get("category") or ("P" if problem["id"].startswith("pair_") else "?")


def cmd_check(args: argparse.Namespace) -> int:
    problems = {r["id"]: r for r in read_jsonl(args.problems)}
    preds = read_jsonl(args.pred)
    idx = load_index()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    verdicts, rereq = [], []
    rerequest_on = {"unsupported", "false_claims"} if args.reject_false_claims else {"unsupported"}
    summ: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in preds:
        pb = problems[r["id"]]
        v = check_output(idx, pb["req"], parse_loose(r.get("text")))
        cat = category_of(pb)
        summ[cat][v["verdict"]] += 1
        verdicts.append({"id": r["id"], "seed": r.get("seed"), "category": cat, **v})
        if v["verdict"] in rerequest_on:
            rereq.append(rerequest_episode(pb, r["text"], feedback_turn(idx, pb["req"], v, args.show_witness)))
    write_jsonl(args.out_dir / "verdicts.jsonl", verdicts)
    write_jsonl(args.out_dir / "rerequest.jsonl", rereq)
    claim_stats: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for v in verdicts:
        for c in v.get("claims") or []:
            claim_stats[v["verdict"]]["in_request" if c["in_request"] else "not_in_request"] += 1
            if c["blocking"] is not None:
                claim_stats[v["verdict"]]["blocking" if c["blocking"] else "not_blocking"] += 1
            if c["alone_ok"] is not None:
                claim_stats[v["verdict"]]["alone_ok" if c["alone_ok"] else "alone_ng"] += 1
    out = {"pred": str(args.pred), "index_size": len(idx), "by_category": {k: dict(v) for k, v in sorted(summ.items())},
           "claims": {k: dict(v) for k, v in claim_stats.items()}, "rerequest_on": sorted(rerequest_on), "n_rerequest": len(rereq)}
    (args.out_dir / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """再要求した行（rerequest.jsonl に入った行）を再要求の出力で置き換える。再要求でまた拒否した行は、その拒否（2 回目）をそのまま使う。"""
    asked = {r["id"] for r in read_jsonl(args.rerequest)}
    unsup = {(v["id"], v.get("seed")) for v in read_jsonl(args.verdicts) if v["id"] in asked}
    new = {r["id"]: r["text"] for r in read_jsonl(args.rerequest_pred)}
    out, n = [], 0
    for r in read_jsonl(args.pred):
        if (r["id"], r.get("seed")) in unsup and r["id"] in new:
            r = {**r, "text": new[r["id"]], "rerequested": True}
            n += 1
        out.append(r)
    write_jsonl(args.out, out)
    print(json.dumps({"rows": len(out), "replaced": n, "unsupported": len(unsup)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="拒否を実艦で照合し、verdicts.jsonl / rerequest.jsonl / summary.json を書く")
    c.add_argument("--problems", type=Path, required=True, help="問題（id, category, req, messages）。eval_v1 なら all_v1sys.jsonl")
    c.add_argument("--pred", type=Path, required=True, help="予測 JSONL（id, text, seed）")
    c.add_argument("--out-dir", type=Path, required=True)
    c.add_argument("--show-witness", type=int, default=2, help="再要求に載せる witness の要約の数（0 で数だけ）")
    c.add_argument("--reject-false-claims", action="store_true", help="false_claims の拒否も再要求する（既定は unsupported だけ）")
    m = sub.add_parser("merge", help="unsupported の行を再要求の出力で置き換える")
    m.add_argument("--pred", type=Path, required=True)
    m.add_argument("--verdicts", type=Path, required=True)
    m.add_argument("--rerequest", type=Path, required=True, help="check が書いた rerequest.jsonl（置き換える行の集合）")
    m.add_argument("--rerequest-pred", type=Path, required=True)
    m.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    return {"check": cmd_check, "merge": cmd_merge}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
