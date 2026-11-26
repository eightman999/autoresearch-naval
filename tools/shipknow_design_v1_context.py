"""design-v1 の context エピソード（1-shot 崩壊対策: 過去の出力・手本が文脈にあっても最新の user 要求に答える）。

design-v0 は手本に 1 つ設計を見せると、84% が手本の艦種（駆逐艦）で答えて要求を無視した。ここでは 2〜4 ターンの会話で、最後の user 要求の正解は常に
その要求を満たす実艦（同じ split）で、文脈の設計・参考例とは別の艦種・国・年代（または大きく別の規模）にする。変種:
  diff             過去の 1〜3 往復は別の艦種・国・年代の設計。最後は全く別の要求。
  long_prev        過去の出力が長い（装備の項目が多い大型艦）。最後は小型艇。
  reference        最後の user 要求に「参考例」(reference = 過去の設計。同じ艦種で別の国・20 年以上離れた年) が付く。正解は新しい要求だけで決まる。
  conflict_ref     参考例が現在の要求と衝突する（別の艦種・大きさで、要求の制約を 2 つ以上満たさない）。
  final_infeasible 過去は満たせる要求、最後は relaxed / infeasible（不可能な要求を、過去の設計で黙って埋めない）。
  prev_infeasible  過去の往復が relaxed / infeasible。最後は普通の要求（過去の null や緩和に引きずられない）。
  conflict_ref_long（test 専用）4 ターン、過去 3 往復 + 衝突する参考例。
学習対象（train_turns）は最後の assistant ターンだけ。自動検査: 最後の設計の艦種・国・年が最新の要求に一致し、過去の設計・参考例の複製ではない。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_design_common as C  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402
import shipknow_design_v1_data as DD  # noqa: E402
import shipknow_design_v1_gen_basic as G  # noqa: E402
import shipknow_design_v1_infeasible as I  # noqa: E402

VARIANTS = [("diff", 34), ("long_prev", 12), ("reference", 22), ("conflict_ref", 14), ("final_infeasible", 10), ("prev_infeasible", 8)]
TEST_ONLY = [("conflict_ref_long", 28)]
HINT = " Use the reference only for style; the requirements in this request take priority."
P_PRIOR_NBR = 0.35
BIG_TYPES = {"battleship", "battlecruiser", "heavy_cruiser", "light_cruiser", "cruiser", "aircraft_carrier", "armored_protected_cruiser", "destroyer", "frigate", "amphibious"}
SMALL_TYPES = {"patrol", "fast_attack_craft", "torpedo_boat", "gunboat_river", "coast_guard", "mine_warfare", "submarine"}


def _final(D: DD.Data, split: str, rng: random.Random, pred: str = "any", types: set[str] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool] | None:
    rec = D.pick(split, rng, pred, types=types)
    if rec is None:
        return None
    r = G.make_request(D, split, rec, rng)
    if r is None:
        return None
    req, unseen = r
    design = D.design(rec)
    if not K.check_design(req, design)["ok"]:
        return None
    return rec, design, req, unseen


def _prior(D: DD.Data, split: str, rng: random.Random, final_req: dict[str, Any], used: list[dict[str, Any]], pred: str = "any", types: set[str] | None = None,
           need_conflict: bool = False, same_type: bool = False) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """最後の要求と別の艦種・国・年代の過去の艦（と、その要求）。"""
    for _ in range(30):
        rec = D.pick(split, rng, pred, types=types)
        if rec is None:
            return None
        d = D.design(rec)
        yr = C.request_year(rec)
        if same_type:
            ok = d["ship_type"] == final_req["ship_type"] and rec["country"] != final_req["country"] and abs(yr - final_req["year"]) >= 20
        else:
            ok = d["ship_type"] != final_req["ship_type"] and (rec["country"] != final_req["country"] or abs(yr - final_req["year"]) >= 25)
        if not ok or any(rec["id"] == u["id"] or D.group_of[rec["id"]] == D.group_of[u["id"]] for u in used):
            continue
        if any(d["ship_type"] == u["ship_type"] for u in used) and not same_type and len(used) >= 1 and rng.random() < 0.5:
            continue
        r = G.make_request(D, split, rec, rng)
        if r is None:
            continue
        req = r[0]
        if need_conflict and len(K.failed_constraints(final_req, d, ignore_hull=False)) < 2:
            continue
        return rec, d, req
    return None


def gen_context(D: DD.Data, split: str, idx: int) -> dict[str, Any] | None:
    rng = K.sub_rng(G.SEED, "context", split, idx)
    vs = VARIANTS + (TEST_ONLY if split == "test" else [])
    variant = rng.choices([v for v, _ in vs], weights=[w for _, w in vs])[0]
    for _ in range(10):
        ep = _gen_once(D, split, rng, variant)
        if ep is not None:
            return ep
    G.fail("context:" + variant)
    return None


def _gen_once(D: DD.Data, split: str, rng: random.Random, variant: str) -> dict[str, Any] | None:
    n_user = {"long_prev": 2, "conflict_ref_long": 4}.get(variant) or rng.choices([2, 3, 4], weights=[0.4, 0.4, 0.2])[0]
    if variant == "long_prev":
        fin = _final(D, split, rng, "small", types=SMALL_TYPES)
    elif variant == "final_infeasible":
        fin = None
    else:
        fin = _final(D, split, rng)
    final_case = None
    if variant == "final_infeasible":
        final_case = I.build_case(D, split, rng)
        if final_case is None or final_case.anchor is None:
            return None
        fin_req = final_case.req
        fin_rec = final_case.anchor
        unseen = False
    else:
        if fin is None:
            return None
        fin_rec, fin_design, fin_req, unseen = fin
    if split != "test" and K.is_heldout_combo(fin_req):
        return None
    ep = G.new_episode("context", variant, split)
    used: list[dict[str, Any]] = [fin_rec]
    prior_designs: list[dict[str, Any]] = []
    n_prior = n_user - 1
    reference = None
    if variant in ("reference", "conflict_ref", "conflict_ref_long"):
        # the last prior exchange's design (or a separate real design for a 1-prior conversation) is shown again as the reference
        pass
    for i in range(n_prior):
        pred = "multi_weapon" if variant == "long_prev" and i == n_prior - 1 else "any"
        types = BIG_TYPES if variant == "long_prev" else None
        conflict = variant in ("conflict_ref", "conflict_ref_long") and i == n_prior - 1
        same = variant == "reference" and i == n_prior - 1
        got = _prior(D, split, rng, fin_req, used, pred=pred, types=types, need_conflict=conflict, same_type=same)
        if got is None:
            return None
        rec, d, req = got
        used.append(rec)
        status, out_d, confs, rels, origin = "feasible", d, [], [], "real"
        if variant == "prev_infeasible" and i == n_prior - 1:
            case = I.build_case(D, split, rng)
            if case is None or case.anchor is None or D.assign[case.anchor["id"]] != split:
                return None
            if case.req["ship_type"] == fin_req["ship_type"]:
                return None
            req, status = case.req, case.status
            rec = case.anchor
            used[-1] = rec
            out_d = I.out_design(case)
            confs, rels = I.conflict_outputs(case.conflicts, status)
            origin = "real" if out_d else "none"
        nbr = D.neighbors(req, [rec["id"]]) if rng.random() < P_PRIOR_NBR or i == 0 and n_prior == 1 and rng.random() < 0.7 else None
        ep["messages"].append(G.msg("user", G.user_turn(K.request_text_v1(req, rng.randrange(6)), req, nbr)))
        G.add_asst(ep, G.asst_turn(status, out_d, confs, rels), status=status, origin=origin, source=rec["id"] if out_d else None, role="context", train=False,
                   conflict_info=[c.info() for c in case.conflicts] if variant == "prev_infeasible" and i == n_prior - 1 else [])
        G.add_source(ep, rec["id"], D)
        prior_designs.append(out_d if out_d is not None else d)
        if variant in ("reference", "conflict_ref", "conflict_ref_long") and i == n_prior - 1:
            reference = d
    nbr_f = D.neighbors(fin_req, [fin_rec["id"]])
    text = K.request_text_v1(fin_req, rng.randrange(6))
    ref_design = None
    if reference is not None:
        ref_design = K.order_design(reference)
        if rng.random() < 0.5:
            text += HINT
    ep["messages"].append(G.msg("user", G.user_turn(text, fin_req, nbr_f, reference=ref_design)))
    if final_case is not None:
        confs, rels = I.conflict_outputs(final_case.conflicts, final_case.status)
        fd = I.out_design(final_case)
        G.add_asst(ep, G.asst_turn(final_case.status, fd, confs, rels), status=final_case.status,
                   origin="real" if fd else "none", source=fin_rec["id"] if fd else None, role="answer", train=True, family=final_case.family,
                   conflict_info=[c.info() for c in final_case.conflicts])
    else:
        G.add_asst(ep, G.asst_turn("feasible", fin_design), status="feasible", origin="real", source=fin_rec["id"], role="answer", train=True)
    G.add_source(ep, fin_rec["id"], D)
    m = ep["meta"]
    m["context_variant"] = variant
    m["unseen_combo"] = unseen
    m["n_prior"] = n_prior
    m["has_reference"] = reference is not None
    if reference is not None:
        m["reference_failed"] = K.failed_constraints(fin_req, reference)
    m["prior_types"] = sorted({d["ship_type"] for d in prior_designs})
    if variant == "conflict_ref_long":
        m["holdout"].append("context_variant")
    if not G.split_check(D, ep):
        return None
    return G.fin_meta(ep, D, fin_req)
