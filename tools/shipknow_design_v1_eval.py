#!/usr/bin/env python3
"""design-v1 の会話エピソード向け採点器（予測 JSONL と gold の突き合わせ。LLM・GPU 不使用）。

    python tools/shipknow_design_v1_eval.py --pred preds.jsonl --gold data/navspec/shipknow_design_v1/test.jsonl \\
        --out-json report.json --out-md report.md

予測は ``tools/shipknow_infer_hf.py --multi-turn-eval`` の出力行 ``{id, category, turn_index, text, seed}``。gold は design-v1 の 1 行 = 1 エピソード。
採点するのは turn_index（最後の学習対象ターン）の 1 ターンで、それより前の assistant ターンは gold が文脈に入っている（teacher-forcing）。
同じ id が seed 違いで複数あれば全行を採点し、集計では 1 行 = 1 件として数える。

行ごとの指標（category 別・全体に集計）:
  json_ok          text 全体が JSON object として parse できる（strict）。json_loose_ok = 先頭の JSON object を取り出せる（後続の余計な文字を許す）。以降は loose で採点
  schema_ok        status / conflicts / relaxations / design の 4 キーが揃い、型と整合（infeasible <=> design null、feasible は conflicts / relaxations 空、relaxed は relaxations あり）
  status_match     status が gold と一致（混同行列 gold x pred。parse 不能は "invalid"）
  design_req_ok    status の主張に対する要求充足: feasible = 全要求を満たす（req_checks_v1）、relaxed = 宣言した緩和だけを満たさず他は満たす（宣言と実際の食い違い 0）
  validator_ok     check_design 合格（feasible は元の要求、relaxed は緩和後の要求に対して。前提 premise の免除 waivers を適用）
  false_feasible   feasible と主張しているのに要求を満たさない / validator に落ちる（黙って緩和・無視）
  conflict_*       gold に conflicts がある行: 予測 conflicts の制約名の集合が gold と一致 / recall / precision（relaxed は gold relaxed の行だけでも出す）
  design_null      infeasible と予測した行で design が null / gold が infeasible の行で予測 design が null
  row_ok           status 一致 かつ（feasible: 要求充足 + validator 合格 / relaxed: 緩和の宣言が正直 + 緩和後 validator 合格 / infeasible: design null）
カテゴリ固有:
  repair   repair_changed（修正版が直前の設計と異なる）、repair_fixed_listed（feedback の違反 (code, field) が全て消えた）。この 2 つは gold が feasible の行だけ、repair_valid（修正版が validator 合格 = validator_ok）
  context  context_type_match（設計の艦種 = 最新要求）、context_copy_prev（前ターンの設計 / 参考例の複製・ほぼ複製）、context_follows_distractor_type（最新要求と艦種が違い、
           前ターン / 参考例の艦種に引きずられた）、context_era_ok（最新要求の年に対し era_early 違反が無い）。国・年は設計に出ないので、年は validator の年代検査、
           国・年の取り違えは「他の要求の設計の複製」で見る。context_final_ok = 艦種一致 + 複製でない + 年代 OK + 要求充足 + validator 合格
  modify   modify_target_follow（gold の変更種別 change が示す方向に設計が動いた）、modify_linked_follow（連動項目: 速力 <-> 出力、排水量 <-> 寸法 / 出力 / 乗員 など）、
           modify_changed（直前の設計と異なる）
  ood      ood_no_unpremised_anachronism（前提が名指ししていない技術の年代違反が無い）、ood_premise_tech_present（tech_early: 前提の技術が設計に入っている）
  共通     design_exact_gold（予測 design が gold design と完全一致）

結果は JSON（行ごとの記録を含む）と Markdown に出す。parse 不能・検査中の例外も 1 件として数える（例外は check_crash）。
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
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402

STATUSES = list(K.STATUSES)
CONF_LABELS = STATUSES + ["invalid"]
CATEGORIES = list(K.CATEGORIES)


# --------------------------------------------------------------------------------------------------
# 入出力・parse
# --------------------------------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def parse_text(text: Any) -> tuple[dict[str, Any] | None, bool, bool]:
    """(obj, json_ok, json_loose_ok)。strict = text 全体が JSON object、loose = 先頭の JSON object（後ろの余計な文字を許す）。"""
    if not isinstance(text, str):
        return None, False, False
    t = text.strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj, True, True
    except Exception:  # noqa: BLE001
        pass
    i = t.find("{")
    if i >= 0:
        try:
            obj, _ = json.JSONDecoder().raw_decode(t[i:])
            if isinstance(obj, dict):
                return obj, False, True
        except Exception:  # noqa: BLE001
            pass
    return None, False, False


def schema_problems(obj: Any) -> list[str]:
    """出力 schema の違反（空なら schema_ok）。"""
    if not isinstance(obj, dict):
        return ["not_object"]
    p: list[str] = []
    if set(obj) != set(K.OUT_KEYS):
        p.append(f"keys:{sorted(obj)}")
        return p
    st = obj["status"]
    if st not in K.STATUSES:
        p.append("status_value")
    confs, rels, design = obj["conflicts"], obj["relaxations"], obj["design"]
    if not (isinstance(confs, list) and all(isinstance(c, dict) and isinstance(c.get("constraint"), str) and isinstance(c.get("reason"), str) for c in confs)):
        p.append("conflicts_type")
    if not (isinstance(rels, list) and all(isinstance(r, dict) and isinstance(r.get("constraint"), str) and {"requested", "relaxed_to", "reason"} <= set(r) for r in rels)):
        p.append("relaxations_type")
    if not (design is None or isinstance(design, dict)):
        p.append("design_type")
    if p:
        return p
    if st == "infeasible" and design is not None:
        p.append("infeasible_with_design")
    if st in ("feasible", "relaxed") and design is None:
        p.append("design_null_but_" + st)
    if st == "feasible" and (confs or rels):
        p.append("feasible_with_conflicts")
    if st == "relaxed" and not rels:
        p.append("relaxed_without_relaxations")
    return p


# --------------------------------------------------------------------------------------------------
# エピソードの文脈
# --------------------------------------------------------------------------------------------------
def _json(s: Any) -> Any:
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return None


def episode_context(ep: dict[str, Any], k: int) -> dict[str, Any]:
    """gold エピソードと評価ターン k から、採点に必要な文脈（最新要求・過去の設計・参考例・feedback・gold 出力・変更種別）を取り出す。"""
    msgs = ep["messages"]
    meta = ep.get("metadata") or {}
    users = [_json(m["content"]) or {} for m in msgs[:k] if m["role"] == "user"]
    reqs = [u["req"] for u in users if isinstance(u.get("req"), dict)]
    prev_designs: list[dict[str, Any]] = []
    prev_obj: dict[str, Any] | None = None
    for m in msgs[:k]:
        if m["role"] == "assistant":
            o = _json(m["content"])
            if isinstance(o, dict):
                prev_obj = o
                if isinstance(o.get("design"), dict):
                    prev_designs.append(o["design"])
    last_user = users[-1] if users else {}
    refs = [u["reference"] for u in users if isinstance(u.get("reference"), dict)]
    gold = _json(msgs[k]["content"]) if k < len(msgs) else None
    turn_meta = next((t for t in meta.get("turns", []) if t.get("idx") == k), {})
    feedback = last_user.get("validator") if isinstance(last_user.get("validator"), dict) else None
    return {"req": reqs[-1] if reqs else {}, "prev_req": reqs[-2] if len(reqs) >= 2 else None, "prev_designs": prev_designs,
            "prev_design": prev_designs[-1] if prev_designs else None, "prev_obj": prev_obj, "references": refs, "feedback": feedback,
            "gold": gold if isinstance(gold, dict) else None, "turn_meta": turn_meta, "change": turn_meta.get("change") or "",
            "n_user_turns": len(users)}


# --------------------------------------------------------------------------------------------------
# 設計の検査（例外は握りつぶして check_crash とする）
# --------------------------------------------------------------------------------------------------
def safe_check(req: dict[str, Any], design: Any, waivers: list[str]) -> tuple[dict[str, Any] | None, str]:
    try:
        return K.check_design(req, design, waivers), ""
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def safe_failed(req: dict[str, Any], design: Any) -> tuple[list[str] | None, str]:
    try:
        return K.failed_constraints(req, design), ""
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _num(d: Any, key: str) -> float | None:
    v = d.get(key) if isinstance(d, dict) else None
    return float(v) if K._isnum(v) else None


def _disp(d: dict[str, Any], basis: str) -> float | None:
    return _num(d, f"displacement_{basis}_t")


def direction_check(ctype: str, dA: dict[str, Any], dB: dict[str, Any], reqB: dict[str, Any]) -> tuple[bool | None, bool | None]:
    """modify の 1 種類の変更 ctype について (対象が動いた向きに従ったか, 連動項目が合理的に動いたか)。判定対象外は None。"""
    basis = reqB.get("displacement_basis", "normal")
    pa, pb = _num(dA, "power_hp"), _num(dB, "power_hp")
    sa, sb = _num(dA, "speed_kn"), _num(dB, "speed_kn")
    if ctype == "speed_up":
        return (sa is not None and sb is not None and sb > sa), (pa is not None and pb is not None and pb > pa)
    if ctype == "speed_down":
        return (sa is not None and sb is not None and sb <= sa), (pa is not None and pb is not None and pb < pa)
    if ctype in ("disp_up", "disp_down"):
        a, b = _disp(dA, basis), _disp(dB, basis)
        if a is None or b is None:
            return False, None
        target = b >= a if ctype == "disp_up" else b < a  # disp_up は上限の引き上げ + 別の要望の合成があり、排水量が減らなければよい
        moved = [f for f in ("length_m", "power_hp", "complement") if _num(dA, f) is not None and _num(dB, f) is not None
                 and ((_num(dB, f) > _num(dA, f)) if ctype == "disp_up" else (_num(dB, f) < _num(dA, f)))]
        return target, len(moved) >= 2 or len(_changed_fields(dA, dB)) >= 3
    if ctype == "endurance_up":
        ea, eb = _num(dA, "endurance_nm"), _num(dB, "endurance_nm")
        return (eb is not None and (ea is None or eb > ea)), None
    if ctype == "crew_down":
        ca, cb = _num(dA, "complement"), _num(dB, "complement")
        lim = reqB.get("max_complement")
        return (cb is not None and (ca is None or cb < ca) and (lim is None or cb <= lim)), None
    if ctype == "gun_cal_up":
        ga, gb = C.main_gun_mm(dA), C.main_gun_mm(dB)
        return (gb is not None and (ga is None or gb > ga)), None
    if ctype == "gun_barrels_up":
        cal = reqB.get("min_gun_barrels_cal_mm")
        return X.gun_barrels(dB, cal) > X.gun_barrels(dA, cal), None
    if ctype in ("add_sam", "add_ssm", "add_ciws"):
        kind = ctype[4:]
        return X.kind_count(dB, kind) > X.kind_count(dA, kind), None
    if ctype == "add_heli":
        return X.aircraft_count(dB) >= 1 and "helicopter" in (dB.get("air") or []), None
    if ctype == "aircraft_up":
        return X.aircraft_count(dB) > X.aircraft_count(dA), None
    if ctype == "propulsion_change":
        want = set(reqB.get("required_propulsion") or [])
        return bool(want) and want <= set(dB.get("propulsion") or []), None
    return None, None  # country_change / year_change など: 設計の向きでは判定しない（要求充足と複製でないことだけ）


def _changed_fields(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def _canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False)


def _near_copy(a: dict[str, Any], b: dict[str, Any], tol: float = 0.01) -> bool:
    keys = ("length_m", "speed_kn", "beam_m")
    if not all(K._isnum(a.get(k)) and K._isnum(b.get(k)) for k in keys):
        return False
    close = lambda x, y: abs(x - y) <= tol * abs(y) + 1e-9  # noqa: E731
    da, db = C.ref_displacement(a)[0], C.ref_displacement(b)[0]
    return bool(da and db and close(da, db) and all(close(a[k], b[k]) for k in keys))


def premise_tech_present(premise: dict[str, Any], design: dict[str, Any]) -> bool | None:
    techs = ([premise.get("tech")] if premise.get("tech") else []) + list(premise.get("also_tech") or [])
    res = []
    for t in techs:
        if t in K.TECH_PROP:
            res.append(K.TECH_PROP[t] in (design.get("propulsion") or []))
        elif t in K.TECH_TOKEN:
            res.append(K.satisfies_all({"required": [K.TECH_TOKEN[t]]}, design, True))
    return all(res) if res else None


# --------------------------------------------------------------------------------------------------
# 1 行の採点
# --------------------------------------------------------------------------------------------------
def score_row(pred: dict[str, Any], ep: dict[str, Any]) -> dict[str, Any]:
    """予測 1 行を gold エピソードと突き合わせて指標の dict にする（値は bool / None / 数値 / 文字列）。None = その行では判定対象外。"""
    cat = ep.get("category") or pred.get("category") or "?"
    k = int(pred.get("turn_index") if pred.get("turn_index") is not None else (ep.get("metadata") or {}).get("train_turns", [len(ep["messages"]) - 1])[-1])
    ctx = episode_context(ep, k)
    req, gold = ctx["req"], ctx["gold"] or {}
    gold_status = gold.get("status")
    rec: dict[str, Any] = {"id": ep["id"], "category": cat, "seed": pred.get("seed"), "turn_index": k, "gold_status": gold_status,
                           "subtype": (ep.get("metadata") or {}).get("subtype")}
    obj, strict, loose = parse_text(pred.get("text"))
    rec["json_ok"], rec["json_loose_ok"] = strict, loose
    probs = schema_problems(obj) if loose else ["unparsable"]
    rec["schema_ok"] = not probs
    rec["schema_problems"] = probs[:3]
    pred_status = obj.get("status") if loose and isinstance(obj, dict) and obj.get("status") in K.STATUSES else None
    rec["pred_status"] = pred_status or "invalid"
    rec["status_match"] = pred_status is not None and pred_status == gold_status
    design = obj.get("design") if loose and isinstance(obj, dict) else None
    confs = obj.get("conflicts") if loose and isinstance(obj, dict) and isinstance(obj.get("conflicts"), list) else []
    rels = obj.get("relaxations") if loose and isinstance(obj, dict) and isinstance(obj.get("relaxations"), list) else []
    waivers = K.waivers_for_premise(req.get("premise"))

    # ---- conflicts の制約名（gold に conflicts がある行）
    gold_confs = {str(c.get("constraint", "")).strip().lower() for c in gold.get("conflicts") or [] if isinstance(c, dict)}
    pred_confs = {str(c.get("constraint", "")).strip().lower() for c in confs if isinstance(c, dict)}
    if gold_confs:
        inter = gold_confs & pred_confs
        rec["conflict_set_exact"] = pred_confs == gold_confs
        rec["conflict_recall"] = len(inter) / len(gold_confs)
        rec["conflict_precision"] = (len(inter) / len(pred_confs)) if pred_confs else 0.0
        rec["conflict_relaxed_exact"] = (pred_confs == gold_confs) if gold_status == "relaxed" else None
    # ---- design の null
    if gold_status == "infeasible":
        rec["gold_infeasible_design_null"] = loose and design is None and pred_status is not None
    if pred_status == "infeasible":
        rec["infeasible_design_null"] = design is None

    # ---- 要求充足と validator
    design_ok_for_claim: bool | None = None
    rec["check_crash"] = ""
    if isinstance(design, dict) and pred_status in ("feasible", "relaxed"):
        failed, err = safe_failed(req, design)
        if failed is None:
            rec["check_crash"] = err
            rec["design_req_ok"] = rec["validator_ok"] = False
            design_ok_for_claim = False
        else:
            if pred_status == "feasible":
                eff = req
                rec["design_req_ok"] = not failed
                rec["false_feasible"] = bool(failed)
            else:
                declared = {str(r.get("constraint")) for r in rels if isinstance(r, dict)}
                eff = K.apply_relaxations(req, [r for r in rels if isinstance(r, dict)])
                rec["design_req_ok"] = set(failed) == declared
                rec["relaxed_hidden_violations"] = len(set(failed) - declared)
                rec["relaxed_declared_not_violated"] = len(declared - set(failed))
            res, err = safe_check(eff, design, waivers)
            if res is None:
                rec["check_crash"] = err
                rec["validator_ok"] = False
            else:
                rec["validator_ok"] = res["ok"]
                rec["violation_codes"] = sorted({f"{v['code']}:{v['field']}" for v in res["violations"]})[:6]
                if pred_status == "feasible":
                    rec["false_feasible"] = rec["false_feasible"] or not res["ok"]
            design_ok_for_claim = bool(rec["design_req_ok"] and rec["validator_ok"])
    elif pred_status == "infeasible":
        design_ok_for_claim = design is None
    rec["row_ok"] = bool(rec["status_match"] and design_ok_for_claim)

    if isinstance(gold.get("design"), dict) and isinstance(design, dict):
        rec["design_exact_gold"] = _canon(design) == _canon(gold["design"])
    elif gold.get("design") is None:
        rec["design_exact_gold"] = None

    # ---- カテゴリ固有
    d = design if isinstance(design, dict) and pred_status in ("feasible", "relaxed") else None
    if cat == "repair":
        prev = ctx["prev_design"]
        rec["repair_valid"] = rec.get("validator_ok") if d is not None else (pred_status == "infeasible" and gold_status == "infeasible")
        if d is not None and prev is not None and gold_status == "feasible":  # gold が緩和（request_impossible）の行は設計を変えなくてよい
            rec["repair_changed"] = _canon(d) != _canon(prev)
        if d is not None and ctx["feedback"] and gold_status == "feasible":
            listed = {(v.get("code"), v.get("field")) for v in ctx["feedback"].get("violations") or [] if isinstance(v, dict)}
            res, _ = safe_check(req, d, waivers)
            if res is not None and listed:
                now = {(v["code"], v["field"]) for v in res["violations"]}
                rec["repair_fixed_listed"] = not (listed & now)
                rec["repair_new_violations"] = len(now - listed)
    elif cat == "context":
        if d is not None:
            rec["context_type_match"] = d.get("ship_type") == req.get("ship_type")
            others = list(ctx["prev_designs"]) + list(ctx["references"])
            copy = any(_canon(o) == _canon(d) or _near_copy(o, d) for o in others)
            rec["context_copy_prev"] = copy
            rec["context_follows_distractor_type"] = (d.get("ship_type") != req.get("ship_type")
                                                      and any(o.get("ship_type") == d.get("ship_type") for o in others))
            res, _ = safe_check({**req, "year": req.get("year")}, d, waivers)
            rec["context_era_ok"] = res is not None and not any(v["code"] == "era_early" for v in res["violations"])
            rec["context_final_ok"] = bool(rec["context_type_match"] and not copy and rec["context_era_ok"] and rec.get("design_req_ok") and rec.get("validator_ok"))
        elif pred_status == "infeasible" or pred_status is None:
            rec["context_final_ok"] = None
    elif cat == "modify":
        prev = ctx["prev_design"]
        if d is not None and prev is not None:
            rec["modify_changed"] = _canon(d) != _canon(prev)
            tgt, link = [], []
            for c in [c for c in ctx["change"].split("+") if c]:
                a_, b_ = direction_check(c, prev, d, req)
                if a_ is not None:
                    tgt.append(bool(a_))
                if b_ is not None:
                    link.append(bool(b_))
            if tgt:
                rec["modify_target_follow"] = bool(rec["modify_changed"] and all(tgt))  # 直前の設計のコピーは追従と数えない
            if link:
                rec["modify_linked_follow"] = bool(rec["modify_changed"] and all(link))
    elif cat == "ood":
        prem = req.get("premise") or {}
        if d is not None:
            res, _ = safe_check(req, d, waivers)
            if res is not None:
                early = [v for v in res["violations"] if v["code"] == "era_early"]
                rec["ood_no_unpremised_anachronism"] = not early
                rec["ood_anachronisms"] = sorted({v["field"] for v in early})[:5]
            if prem.get("type") == "tech_early":
                rec["ood_premise_tech_present"] = premise_tech_present(prem, d)
    return rec


# --------------------------------------------------------------------------------------------------
# 集計
# --------------------------------------------------------------------------------------------------
RATE_KEYS = ["json_ok", "json_loose_ok", "schema_ok", "status_match", "row_ok", "design_req_ok", "validator_ok", "false_feasible", "check_crash_any",
             "conflict_set_exact", "conflict_relaxed_exact", "gold_infeasible_design_null", "infeasible_design_null", "design_exact_gold",
             "repair_valid", "repair_changed", "repair_fixed_listed",
             "context_type_match", "context_copy_prev", "context_follows_distractor_type", "context_era_ok", "context_final_ok",
             "modify_target_follow", "modify_linked_follow", "modify_changed", "ood_no_unpremised_anachronism", "ood_premise_tech_present"]
MEAN_KEYS = ["conflict_recall", "conflict_precision", "relaxed_hidden_violations", "relaxed_declared_not_violated", "repair_new_violations"]


def aggregate_group(recs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(recs)}
    for key in RATE_KEYS:
        vals = [(bool(r["check_crash"]) if key == "check_crash_any" else r.get(key)) for r in recs]
        vals = [v for v in vals if v is not None]
        out[key] = {"n": len(vals), "k": sum(1 for v in vals if v), "rate": (sum(1 for v in vals if v) / len(vals)) if vals else None}
    for key in MEAN_KEYS:
        vals = [r[key] for r in recs if r.get(key) is not None]
        out[key] = {"n": len(vals), "mean": (sum(vals) / len(vals)) if vals else None}
    conf = {g: {p: 0 for p in CONF_LABELS} for g in STATUSES}
    for r in recs:
        if r["gold_status"] in conf:
            conf[r["gold_status"]][r["pred_status"] if r["pred_status"] in CONF_LABELS else "invalid"] += 1
    out["status_confusion"] = conf
    out["status_counts_gold"] = dict(collections.Counter(r["gold_status"] for r in recs))
    out["status_counts_pred"] = dict(collections.Counter(r["pred_status"] for r in recs))
    return out


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    cats = sorted({r["category"] for r in records}, key=lambda c: CATEGORIES.index(c) if c in CATEGORIES else 99)
    res = {"overall": aggregate_group(records), "by_category": {c: aggregate_group([r for r in records if r["category"] == c]) for c in cats}}
    sub: dict[str, Any] = {}
    for c in cats:
        subs = sorted({r.get("subtype") for r in records if r["category"] == c and r.get("subtype")})
        if subs:
            sub[c] = {s: {"n": sum(1 for r in records if r["category"] == c and r.get("subtype") == s),
                          "row_ok": (lambda rs: sum(1 for r in rs if r["row_ok"]) / len(rs))([r for r in records if r["category"] == c and r.get("subtype") == s]),
                          "status_match": (lambda rs: sum(1 for r in rs if r["status_match"]) / len(rs))([r for r in records if r["category"] == c and r.get("subtype") == s])}
                      for s in subs}
    res["by_subtype"] = sub
    return res


def pct(x: dict[str, Any] | None) -> str:
    if not x or x.get("rate") is None:
        return "-"
    return f"{100 * x['rate']:.1f}% ({x['k']}/{x['n']})"


def render_markdown(agg: dict[str, Any], meta: dict[str, Any]) -> str:
    L: list[str] = ["# design-v1 採点結果", ""]
    for k, v in meta.items():
        L.append(f"- {k}: {v}")
    L.append("")
    groups = [("overall", agg["overall"])] + list(agg["by_category"].items())
    names = [g for g, _ in groups]

    def table(title: str, keys: list[tuple[str, str]]) -> None:
        L.append(f"## {title}")
        L.append("")
        L.append("| 指標 | " + " | ".join(names) + " |")
        L.append("|---|" + "---|" * len(names))
        L.append("| n | " + " | ".join(str(g["n"]) for _, g in groups) + " |")
        for key, label in keys:
            if all(g[key]["n"] == 0 for _, g in groups):
                continue
            L.append(f"| {label} | " + " | ".join(pct(g[key]) for _, g in groups) + " |")
        L.append("")

    table("1. 形式と status", [("json_ok", "JSON parse（全体が object）"), ("json_loose_ok", "JSON parse（先頭の object）"), ("schema_ok", "schema（4 キー・型・整合）"),
                              ("status_match", "status 一致率"), ("row_ok", "総合 row_ok（status 一致 + 設計が主張どおり）")])
    table("2. 設計の要求充足と validator", [("design_req_ok", "要求充足（feasible: 全部 / relaxed: 宣言した緩和だけ未充足）"), ("validator_ok", "validator 合格"),
                                        ("false_feasible", "false_feasible（feasible と主張して未充足 / 不合格）"), ("check_crash_any", "検査中の例外")])
    table("3. 衝突の扱い", [("conflict_set_exact", "conflicts の制約名が gold と一致（gold に衝突がある行）"), ("conflict_relaxed_exact", "同（gold が relaxed の行）"),
                          ("gold_infeasible_design_null", "gold が infeasible の行で design = null"), ("infeasible_design_null", "infeasible と予測した行で design = null")])
    table("4. category 固有", [("repair_valid", "repair: 修正版が validator 合格"), ("repair_changed", "repair: 直前の設計から変更"), ("repair_fixed_listed", "repair: feedback の違反が全て消えた"),
                              ("context_type_match", "context: 艦種が最新要求と一致"), ("context_copy_prev", "context: 前ターン / 参考例の複製（低いほど良い）"),
                              ("context_follows_distractor_type", "context: 前ターン / 参考例の艦種に引きずられた（低いほど良い）"), ("context_era_ok", "context: 年代 OK"),
                              ("context_final_ok", "context: 総合（艦種 + 非複製 + 年代 + 充足 + validator）"),
                              ("modify_target_follow", "modify: 変更の向きに追従"), ("modify_linked_follow", "modify: 連動項目に追従"), ("modify_changed", "modify: 直前の設計から変更"),
                              ("ood_no_unpremised_anachronism", "ood: 前提外の技術なし"), ("ood_premise_tech_present", "ood: 前提の技術が入っている"),
                              ("design_exact_gold", "design が gold と完全一致（gold に design がある行）")])
    L.append("## 5. 平均")
    L.append("")
    L.append("| 指標 | " + " | ".join(names) + " |")
    L.append("|---|" + "---|" * len(names))
    for key in MEAN_KEYS:
        if all(g[key]["n"] == 0 for _, g in groups):
            continue
        L.append(f"| {key} | " + " | ".join("-" if g[key]["mean"] is None else f"{g[key]['mean']:.3f}" for _, g in groups) + " |")
    L.append("")
    L.append("## 6. status 混同行列（行 = gold、列 = 予測）")
    L.append("")
    for name, g in groups:
        L.append(f"### {name}")
        L.append("")
        L.append("| gold \\ pred | " + " | ".join(CONF_LABELS) + " |")
        L.append("|---|" + "---|" * len(CONF_LABELS))
        for gs in STATUSES:
            row = g["status_confusion"][gs]
            if sum(row.values()):
                L.append(f"| {gs} | " + " | ".join(str(row[p]) for p in CONF_LABELS) + " |")
        L.append("")
    if agg.get("by_subtype"):
        L.append("## 7. subtype 別（row_ok / status 一致）")
        L.append("")
        for c, subs in agg["by_subtype"].items():
            L.append(f"### {c}")
            L.append("")
            L.append("| subtype | n | row_ok | status 一致 |")
            L.append("|---|---|---|---|")
            for s, v in subs.items():
                L.append(f"| {s} | {v['n']} | {100 * v['row_ok']:.1f}% | {100 * v['status_match']:.1f}% |")
            L.append("")
    return "\n".join(L) + "\n"


def evaluate(preds: list[dict[str, Any]], gold_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """(行ごとの記録, 集計, gold に無い id) を返す。"""
    gold = {r["id"]: r for r in gold_rows}
    recs, missing = [], []
    for p in preds:
        ep = gold.get(p.get("id"))
        if ep is None:
            missing.append(str(p.get("id")))
            continue
        recs.append(score_row(p, ep))
    return recs, aggregate(recs), missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", type=Path, required=True, help="予測 JSONL（.gz 可）。shipknow_infer_hf.py --multi-turn-eval の出力")
    ap.add_argument("--gold", type=Path, required=True, help="design-v1 の test.jsonl / valid.jsonl")
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--out-md", type=Path, default=None)
    ap.add_argument("--no-rows", action="store_true", help="JSON に行ごとの記録を含めない")
    args = ap.parse_args()
    preds = read_jsonl(args.pred)
    gold_rows = read_jsonl(args.gold)
    recs, agg, missing = evaluate(preds, gold_rows)
    meta = {"pred": str(args.pred), "gold": str(args.gold), "n_pred": len(preds), "n_scored": len(recs), "n_pred_ids_not_in_gold": len(missing)}
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps({"meta": meta, **agg, **({} if args.no_rows else {"rows": recs})}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = render_markdown(agg, meta)
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(md, encoding="utf-8")
    print(md)
    return 0 if recs else 1


if __name__ == "__main__":
    raise SystemExit(main())
