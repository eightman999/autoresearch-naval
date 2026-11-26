"""design-v1 データの検査（品質・重複・統計・サンプル・README）。生成器の内部に依存せず、出力 JSONL とメッセージの内容から再計算する。

    .venv/bin/python tools/shipknow_check_design_v1.py --dir data/navspec/shipknow_design_v1_pilot [--tokenizer <tokenizer.json>] [--n-full 100000]

出力（<dir>/）: quality_check.json（全件の自動検査）、overlap_check.json（重複検査）、stats.json（統計）、samples/<category>.md（人間可読サンプル）、README.md。
終了コード: 品質検査・重複検査のどれかに違反があれば 1。

品質検査（全件）: record / messages の形、user / assistant の JSON parse（assistant に自然文が混ざっていないか）、design の schema・キー・NaN / inf・空文字・数値範囲・年代範囲、
requirements の型、status の値、status ごとの整合（feasible = 衝突なし・全要求を満たす・validator 合格、relaxed = relaxations あり・緩和した制約だけを満たさない・他は満たす・
validator 合格・relaxed_to が設計の値、infeasible = design null・relaxations 空・衝突あり）、relaxed / infeasible の判定基準の再計算（decide_status）と実データ上の不可能性（oracle）、
real 由来の設計が実艦の設計と一致、split、カテゴリ固有（modify の連動と設計の変化、repair の違反再現と修正、ood の免除、context の最新要求）。
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402
import shipknow_design_v1_infeasible as I  # noqa: E402
import shipknow_design_v1_ops as O  # noqa: E402
import shipknow_design_validator as V  # noqa: E402

ID_RE = re.compile(r"^dv1_[0-9a-f]{8,}$")
USER_KEYS = {"request", "req", "reference", "nbr", "validator", "instruction"}
PREMISE_TYPES = {"fictional_nation", "tech_early", "war_outcome", "doctrine"}
YEAR_RANGE = (1860, 2100)


# --------------------------------------------------------------------------------------------------
# context (real ships, train index)
# --------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, out_dir: Path):
        self.dir = out_dir
        self.manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        recs = [json.loads(l) for l in (C.V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        sp = json.loads((C.V0_DIR / "splits.json").read_text(encoding="utf-8"))
        self.assign: dict[str, str] = sp["assignments"]
        self.group_of: dict[str, str] = sp["group_of"]
        self.rec = {r["id"]: r for r in recs}
        elig = [r for r in recs if C.design_eligible(r)]
        self.designs = {r["id"]: C.design_from_record(r) for r in elig}
        # the oracle index mirrors the generator's: train ships whose own design passes the validator and the geometry sanity filter
        good = [r for r in elig if self.assign[r["id"]] == "train" and K.check_design({"year": C.request_year(r), "country": r["country"]}, self.designs[r["id"]])["ok"]
                and C.ratios_of(self.designs[r["id"]]).get("slender", 0) <= 16 and C.ratios_of(self.designs[r["id"]]).get("lb", 0) <= 18]
        self.idx_train = K.DesignIndex(good, self.designs)
        self.th = K.thresholds()
        self.system = self.manifest["system_prompt"]
        self.fictional = self.manifest["holdout"]["fictional_names"]


def _finite_all(o: Any) -> bool:
    if isinstance(o, float):
        return math.isfinite(o)
    if isinstance(o, dict):
        return all(_finite_all(v) for v in o.values())
    if isinstance(o, list):
        return all(_finite_all(v) for v in o)
    return True


def _has_empty_string(o: Any) -> bool:
    if isinstance(o, str):
        return o.strip() == ""
    if isinstance(o, dict):
        return any(_has_empty_string(v) for v in o.values())
    if isinstance(o, list):
        return any(_has_empty_string(v) for v in o)
    return False


def canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# --------------------------------------------------------------------------------------------------
# episode check
# --------------------------------------------------------------------------------------------------
def check_req_types(req: Any) -> list[str]:
    p: list[str] = []
    if not isinstance(req, dict):
        return ["req_not_object"]
    unknown = set(req) - set(K.REQ_ORDER)
    if unknown:
        p.append(f"req_unknown_keys:{sorted(unknown)}")
    for k in ("country", "ship_type"):
        if not isinstance(req.get(k), str) or not req.get(k):
            p.append(f"req_{k}_missing")
    for k in K.REQ_NUM_KEYS:
        if k in req and not K._isnum(req[k]):
            p.append(f"req_{k}_not_number")
    for k in K.REQ_INT_KEYS:
        if k in req and K._isnum(req[k]) and float(req[k]) != int(req[k]):
            p.append(f"req_{k}_not_int")
    if not (isinstance(req.get("year"), int) and YEAR_RANGE[0] <= req["year"] <= YEAR_RANGE[1]):
        p.append("req_year_range")
    if req.get("ship_type") not in V.T.COARSE_TYPES:
        p.append("req_ship_type_unknown")
    for k in ("required", "mission", "required_propulsion"):
        if k in req and not (isinstance(req[k], list) and all(isinstance(x, str) and x for x in req[k])):
            p.append(f"req_{k}_not_list_of_str")
    for p_ in req.get("required_propulsion") or []:
        if p_ not in K.PROPULSION_KINDS:
            p.append("req_propulsion_unknown")
    if req.get("displacement_basis") not in ("normal", "full", "standard"):
        p.append("req_basis")
    mx, mn = req.get("max_displacement_t"), req.get("min_displacement_t")
    if not K._isnum(mx) or mx <= 0:
        p.append("req_max_displacement")
    if "premise" in req and not (isinstance(req["premise"], dict) and req["premise"].get("type") in PREMISE_TYPES):
        p.append("req_premise_type")
    return p


def design_problems(d: Any) -> list[str]:
    p: list[str] = []
    if not isinstance(d, dict):
        return ["design_not_object"]
    extra = set(d) - set(C.DESIGN_KEY_ORDER)
    if extra:
        p.append(f"design_unknown_keys:{sorted(extra)}")
    if list(d) != [k for k in C.DESIGN_KEY_ORDER if k in d]:
        p.append("design_key_order")
    sch = V._schema(d)
    for x in sch:
        p.append(f"design_{x['code']}:{x['field']}")
    if not _finite_all(d):
        p.append("design_nan_inf")
    if _has_empty_string(d):
        p.append("design_empty_string")
    return p


def req_diff_keys(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def conflicts_from_info(info: list[dict[str, Any]]) -> list[I.Conflict]:
    return [I.Conflict(i["constraint"], i["family"], "", i["relaxable"], i["gap"], i["mission_core"]) for i in info]


def relaxed_value_ok(cid: str, rel: dict[str, Any], design: dict[str, Any], req: dict[str, Any]) -> bool:
    """relaxed_to が設計の実際の値と一致する（数値の制約）。"""
    v = rel.get("relaxed_to")
    if cid == "min_speed_kn":
        return K._isnum(v) and K._isnum(design.get("speed_kn")) and abs(v - design["speed_kn"]) < 1e-6
    if cid == "min_gun_barrels":
        return K._isnum(v) and v == X.gun_barrels(design, req.get("min_gun_barrels_cal_mm"))
    if cid == "min_aircraft":
        return K._isnum(v) and v == X.aircraft_count(design)
    if cid == "min_torpedo_tubes":
        return K._isnum(v) and v == X.torpedo_tubes(design)
    if cid == "max_complement":
        return K._isnum(v) and K._isnum(design.get("complement")) and abs(v - design["complement"]) < 1e-6
    return v is not None


def check_episode(ep: Any, X_: Ctx) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    probs: list[tuple[str, str]] = []
    info: dict[str, Any] = {}

    def bad(code: str, detail: str = "") -> None:
        probs.append((code, detail))

    # ---- record
    if not isinstance(ep, dict) or set(ep) != {"id", "category", "messages", "metadata"}:
        bad("record_keys")
        return probs, info
    cat, meta, msgs = ep["category"], ep["metadata"], ep["messages"]
    if not (isinstance(ep["id"], str) and ID_RE.match(ep["id"])):
        bad("id_format", str(ep.get("id")))
    if cat not in K.CATEGORIES:
        bad("category", str(cat))
    if not isinstance(msgs, list) or len(msgs) < 3:
        bad("messages_short")
        return probs, info
    if msgs[0].get("role") != "system" or msgs[0].get("content") != X_.system:
        bad("system_prompt")
    roles = [m.get("role") for m in msgs]
    if roles[1:] != [("user" if i % 2 == 0 else "assistant") for i in range(len(roles) - 1)] or roles[-1] != "assistant":
        bad("roles_order", str(roles))
        return probs, info
    if any(not isinstance(m.get("content"), str) or not m["content"].strip() for m in msgs):
        bad("content_not_string_or_empty")
        return probs, info
    if set().union(*[set(m) for m in msgs]) != {"role", "content"}:
        bad("message_extra_keys")
    split = meta.get("split")
    if split not in K.SPLITS:
        bad("split_value", str(split))
    # ---- parse turns
    turn_by_idx = {t["idx"]: t for t in meta.get("turns", [])}
    cur_req: dict[str, Any] | None = None
    first_req: dict[str, Any] | None = None
    users: list[dict[str, Any]] = []
    asst: list[dict[str, Any]] = []  # {idx, obj, req, turn}
    for i, m in enumerate(msgs[1:], start=1):
        if m["role"] == "user":
            try:
                u = json.loads(m["content"])
            except Exception:  # noqa: BLE001
                bad("user_json", f"idx {i}")
                return probs, info
            if not isinstance(u, dict) or set(u) - USER_KEYS:
                bad("user_keys", f"idx {i}: {sorted(set(u) - USER_KEYS) if isinstance(u, dict) else 'not dict'}")
                return probs, info
            users.append(u)
            if "validator" in u:
                v = u["validator"]
                if not (isinstance(v, dict) and v.get("valid") is False and isinstance(v.get("violations"), list) and v["violations"]):
                    bad("validator_msg", f"idx {i}")
                if u.get("instruction") != "Fix the design. Keep the original request as far as possible.":
                    bad("validator_instruction")
            else:
                if not isinstance(u.get("request"), str) or not u["request"].strip():
                    bad("request_empty", f"idx {i}")
                for rp in check_req_types(u.get("req")):
                    bad("req_type", f"idx {i}: {rp}")
                if isinstance(u.get("req"), dict):
                    cur_req = u["req"]
                    first_req = first_req or cur_req
                if "nbr" in u and not (isinstance(u["nbr"], list) and u["nbr"]):
                    bad("nbr_bad", f"idx {i}")
                if "reference" in u:
                    for dp in design_problems(u["reference"]):
                        bad("reference_" + dp, f"idx {i}")
        else:
            obj, err = K.parse_output(m["content"])
            if obj is None:
                bad("assistant_parse" if not err.startswith("json") else "assistant_json", f"idx {i}: {err}")
                return probs, info
            if K.jdump(obj) != m["content"]:
                bad("assistant_not_compact_json", f"idx {i}")
            asst.append({"idx": i, "obj": obj, "req": cur_req, "turn": turn_by_idx.get(i)})
    if cur_req is None or first_req is None:
        bad("no_req")
        return probs, info
    if any(c in ("req_type", "reference_design") or c.startswith("reference_") for c, _ in probs):
        return probs, info  # the semantic checks below assume well-typed requirements
    if len(asst) != len(meta.get("turns", [])) or any(a["turn"] is None for a in asst):
        bad("turns_meta_mismatch")
        return probs, info
    if not all(t in {a["idx"] for a in asst} for t in meta.get("train_turns", [])) or not meta.get("train_turns"):
        bad("train_turns")
    if msgs and (len(msgs) - 1) // 2 != meta.get("turns_user"):
        bad("turns_user_meta")
    info.update({"n_user": len([u for u in users if "validator" not in u]), "n_msgs": len(msgs), "final_req": cur_req, "first_req": first_req, "status_seq": [a["obj"]["status"] for a in asst]})

    # ---- per assistant turn
    designs: list[dict[str, Any] | None] = []
    last_src: str | None = None
    changes_all = " ".join(str(t.get("change", "")) for t in meta.get("turns", []))
    year_limit = 7 if ("year_change" in changes_all or "country_change" in changes_all) else 5
    year_gaps: list[int] = []
    for a in asst:
        obj, req, turn = a["obj"], a["req"], a["turn"]
        role, st = turn["role"], obj["status"]
        if st not in K.STATUSES:
            bad("status_value", str(st))
            continue
        if st != turn["status"]:
            bad("status_meta_mismatch", f"idx {a['idx']}")
        d = obj["design"]
        designs.append(d)
        if turn.get("source"):
            last_src = turn["source"]
        if d is not None and a["idx"] in meta.get("train_turns", []) and K._isnum(req.get("year")):
            # the source ship (or, for a rescaled design, the real ship it was derived from) must be of the request's era
            rec_src = X_.rec.get(turn.get("source") or last_src)
            if rec_src is None:
                bad("source_missing", f"idx {a['idx']}")
            else:
                gap = int(req["year"]) - C.request_year(rec_src)
                year_gaps.append(gap)
                if abs(gap) > year_limit:
                    bad("source_year_gap", f"idx {a['idx']}: request {req['year']} vs source {C.request_year(rec_src)} ({gap:+d}, limit {year_limit})")
        if not (isinstance(obj["conflicts"], list) and isinstance(obj["relaxations"], list)):
            bad("conflicts_relaxations_type", f"idx {a['idx']}")
            continue
        for c in obj["conflicts"]:
            if not (isinstance(c, dict) and list(c) == ["constraint", "reason"] and all(isinstance(c[k], str) and c[k].strip() for k in c)):
                bad("conflict_schema", f"idx {a['idx']}")
            elif len(c["reason"]) > 400:
                bad("reason_too_long", f"idx {a['idx']}")
        for r in obj["relaxations"]:
            if not (isinstance(r, dict) and list(r) == ["constraint", "requested", "relaxed_to", "reason"] and isinstance(r["constraint"], str) and r["constraint"] and r["requested"] not in (None, "")
                    and r["relaxed_to"] not in (None, "") and isinstance(r["reason"], str) and r["reason"].strip()):
                bad("relaxation_schema", f"idx {a['idx']}")
        if d is not None:
            for dp in design_problems(d):
                if role in ("initial", "partial_fix") and dp.startswith("design_schema_range"):
                    continue  # a corrupted design may be out of range on purpose (the validator reports it)
                bad(dp, f"idx {a['idx']}")
        waivers = K.waivers_for_premise(req.get("premise"))
        if sorted(turn.get("waivers", [])) != waivers and role not in ("context",):
            bad("waiver_mismatch", f"idx {a['idx']}: {turn.get('waivers')} vs {waivers}")
        if role in ("initial", "partial_fix"):
            # intentionally violating designs: must claim feasible and must really violate the validator
            if st != "feasible" or obj["conflicts"] or obj["relaxations"] or d is None:
                bad("initial_not_feasible_claim", f"idx {a['idx']}")
            elif K.check_design(req, d, waivers)["ok"]:
                bad("initial_has_no_violation", f"idx {a['idx']}")
            continue
        n_conf = len(K.constraint_ids(req))
        if st == "feasible":
            if obj["conflicts"] or obj["relaxations"]:
                bad("feasible_with_conflicts", f"idx {a['idx']}")
            if d is None:
                bad("feasible_without_design", f"idx {a['idx']}")
                continue
            failed = K.failed_constraints(req, d)
            if failed:
                bad("feasible_unsatisfied", f"idx {a['idx']}: {failed}")
            res = K.check_design(req, d, waivers)
            if not res["ok"]:
                bad("feasible_validator", f"idx {a['idx']}: {[x['code'] + ':' + x['field'] for x in res['violations']][:4]}")
        elif st == "relaxed":
            if not obj["relaxations"]:
                bad("relaxed_empty_relaxations", f"idx {a['idx']}")
            if not obj["conflicts"]:
                bad("relaxed_empty_conflicts", f"idx {a['idx']}")
            if d is None:
                bad("relaxed_without_design", f"idx {a['idx']}")
                continue
            rel_ids = [r["constraint"] for r in obj["relaxations"]]
            have = set(K.constraint_ids(req))
            if any(r not in have for r in rel_ids):
                bad("relaxed_unknown_constraint", f"idx {a['idx']}: {rel_ids}")
            eff = K.apply_relaxations(req, obj["relaxations"])
            res = K.check_design(eff, d, waivers)
            if not res["ok"]:
                bad("relaxed_validator", f"idx {a['idx']}: {[x['code'] + ':' + x['field'] for x in res['violations']][:4]}")
            failed = set(K.failed_constraints(req, d))
            if failed != set(rel_ids):
                bad("relaxed_set_mismatch", f"idx {a['idx']}: failed {sorted(failed)} vs relaxed {sorted(rel_ids)}")
            for r in obj["relaxations"]:
                if not relaxed_value_ok(r["constraint"], r, d, req):
                    bad("relaxed_value", f"idx {a['idx']}: {r['constraint']}")
            if [c["constraint"] for c in obj["conflicts"]] != rel_ids:
                bad("relaxed_conflict_relaxation_mismatch", f"idx {a['idx']}")
            _status_rule(a, turn, req, st, n_conf, X_, bad)
        else:  # infeasible
            if d is not None:
                bad("infeasible_with_design", f"idx {a['idx']}")
            if obj["relaxations"]:
                bad("infeasible_with_relaxations", f"idx {a['idx']}")
            if not obj["conflicts"]:
                bad("infeasible_without_conflicts", f"idx {a['idx']}")
            _status_rule(a, turn, req, st, n_conf, X_, bad)
        # origin == real: verbatim real design
        if turn.get("origin") == "real" and d is not None:
            sid = turn.get("source")
            if sid not in X_.designs or K.order_design(X_.designs[sid]) != d:
                bad("real_design_mismatch", f"idx {a['idx']}: {sid}")
            else:
                rr = C.ratios_of(d)
                if rr.get("slender", 0) > 16 or rr.get("lb", 0) > 18:
                    bad("real_design_geometry_outlier", f"idx {a['idx']}: {sid}")
        if d is not None and K._isnum(req.get("year")) and not (YEAR_RANGE[0] <= req["year"] <= YEAR_RANGE[1]):
            bad("year_range")
    info["year_gaps"] = year_gaps
    # ---- sources / split
    for s in meta.get("sources", []):
        if X_.assign.get(s) != split:
            bad("source_split", f"{s}: {X_.assign.get(s)} vs {split}")
    # ---- category rules
    _category_checks(ep, cat, meta, msgs, users, asst, info, X_, bad)
    return probs, info


def _status_rule(a: dict[str, Any], turn: dict[str, Any], req: dict[str, Any], st: str, n_conf: int, X_: Ctx, bad) -> None:
    """relaxed / infeasible の判定基準の再計算（decide_status）と、実データ上の不可能性（oracle）。"""
    cinfo = turn.get("conflict_info")
    if cinfo:
        want = I.decide_status(conflicts_from_info(cinfo), n_conf)
        if want != st:
            bad("status_rule_mismatch", f"idx {a['idx']}: decide_status={want}, status={st}")
        if len(cinfo) != len(a["obj"]["conflicts"]):
            bad("conflict_info_count", f"idx {a['idx']}")
    else:
        bad("conflict_info_missing", f"idx {a['idx']}")
    # oracle: no real train ship (same coarse type, designed up to year+3) satisfies the whole request
    class _D:  # minimal duck type for I.real_satisfying
        idx_train = X_.idx_train

    n_real = len(I.real_satisfying(_D, req))
    if n_real:
        bad("oracle_possible", f"idx {a['idx']}: {n_real} real train ships satisfy it")


def _category_checks(ep, cat, meta, msgs, users, asst, info, X_: Ctx, bad) -> None:
    n_user = info["n_user"]
    st_seq = info["status_seq"]
    final = asst[-1]
    if cat == "single":
        if n_user != 1 or len(asst) != 1 or st_seq != ["feasible"]:
            bad("single_shape")
        if "premise" in info["final_req"]:
            bad("single_has_premise")
    elif cat == "modify":
        if n_user < 2:
            bad("modify_shape")
            return
        a0, a1 = asst[0], asst[1]
        if a0["obj"]["status"] != "feasible" or a0["obj"]["design"] is None:
            bad("modify_first_not_feasible")
            return
        reqs = [u["req"] for u in users if "req" in u]
        for i in range(1, len(reqs)):
            diff = req_diff_keys(reqs[i - 1], reqs[i])
            if not diff:
                bad("modify_req_unchanged", f"turn {i}")
        for k in range(1, len(asst)):
            dA, dB = asst[k - 1]["obj"]["design"], asst[k]["obj"]["design"]
            if dB is None:
                continue
            if dA == dB:
                bad("modify_design_unchanged", f"turn {k}")
                continue
            nchg = len(O.changed_fields(dA, dB))
            info.setdefault("changed_n", []).append(nchg)
            if nchg < 2:
                info["single_field_edit"] = info.get("single_field_edit", 0) + 1
            ch = (asst[k]["turn"].get("change") or "")
            reqB = reqs[k] if k < len(reqs) else reqs[-1]
            for c in ch.split("+"):
                msg = _coupling_check(c, dA, dB, reqs[k - 1], reqB)
                if msg:
                    bad("modify_coupling", f"{c}: {msg}")
                info.setdefault("coupling_checked", []).append(c)
        info["route"] = [t.get("route") for t in meta["turns"] if t.get("route")]
        if len(reqs) >= 2 and "ship_type_fine" in reqs[0]:
            bad("modify_fine_type_in_first_req")
    elif cat == "repair":
        if len(asst) not in (2, 3) or n_user != 1:
            bad("repair_shape")
            return
        init, fin = asst[0], asst[-1]
        req = info["first_req"]
        t0 = init["turn"]
        if t0["role"] != "initial" or fin["turn"]["role"] != "fix":
            bad("repair_roles")
        if meta["train_turns"] != [fin["idx"]]:
            bad("repair_train_turns")
        fbs = [json.loads(m["content"]) for m in msgs[1:] if m["role"] == "user" and "validator" in json.loads(m["content"])]
        if len(fbs) != len(asst) - 1:
            bad("repair_feedback_count")
            return
        # the shown violations are the validator's real output on the design that precedes the feedback
        for fb, prev in zip(fbs, asst[:-1]):
            res = K.check_design(req, prev["obj"]["design"], K.waivers_for_premise(req.get("premise")))
            real = {(v["code"], v["field"]): re.sub(r"\s+", " ", v["detail"])[:160] for v in res["violations"]}
            for v in fb["validator"]["violations"]:
                key = (v["code"], v["field"])
                if key not in real:
                    bad("repair_violation_not_reproduced", f"{key}")
                elif real[key] != v["detail"]:
                    bad("repair_violation_detail_differs", f"{key}")
            if not res["violations"] or len(fb["validator"]["violations"]) != min(6, len(res["violations"])):
                bad("repair_violation_count", f"{len(fb['validator']['violations'])} vs {len(res['violations'])}")
        if fin["obj"]["status"] == "feasible" and fin["obj"]["design"] == init["obj"]["design"]:
            bad("repair_unchanged")
        info["repair_method"] = meta.get("repair_method")
        info["violation_kinds"] = meta.get("violation_kinds", [])
        info["violation_codes"] = meta.get("violation_codes", [])
    elif cat == "infeasible":
        if n_user != 1 or len(asst) != 1 or st_seq[0] == "feasible":
            bad("infeasible_shape")
    elif cat == "ood":
        req = info["final_req"]
        prem = req.get("premise")
        if n_user != 1 or not prem:
            bad("ood_shape")
            return
        t = prem["type"]
        info["premise_type"] = t
        d = final["obj"]["design"]
        waivers = K.waivers_for_premise(prem)
        if t == "fictional_nation":
            names = X_.fictional
            if req["country"] not in names[meta["split"]]:
                bad("ood_fictional_name_split", req["country"])
        elif t == "tech_early":
            if d is not None and X_.designs.get(final["turn"].get("source")) is not None and K.order_design(X_.designs[final["turn"]["source"]]) == d:
                bad("ood_tech_not_added")
            if d is not None and K.check_design(req, d)["ok"] and final["obj"]["status"] != "infeasible":
                bad("ood_premise_not_needed")
            if d is not None:
                res = K.check_design(req, d)
                early = {v["field"] for v in res["violations"] if v["code"] == "era_early"}
                if not early or not early <= set(waivers):
                    bad("ood_unpremised_anachronism", f"{sorted(early)} vs waivers {waivers}")
        elif t == "doctrine":
            if meta.get("doctrine") not in ("torpedo_centric", "gun_centric", "missile_centric", "air_centric", "asw_centric"):
                bad("ood_doctrine_name")
        if meta.get("premise_type") == "tech_early" and final["turn"].get("origin") not in ("tech_added", "none"):
            bad("ood_tech_origin")
        if t != "tech_early" and waivers:
            bad("ood_waivers_without_tech")
    elif cat == "context":
        if not (2 <= n_user <= 4):
            bad("context_turns", str(n_user))
        req = info["final_req"]
        d = final["obj"]["design"]
        info["variant"] = meta.get("context_variant")
        if final["turn"]["role"] != "answer" or meta["train_turns"] != [final["idx"]]:
            bad("context_train_turns")
        prev_designs = [a["obj"]["design"] for a in asst[:-1] if a["obj"]["design"] is not None]
        ref = None
        last_user = users[-1]
        if "reference" in last_user:
            ref = last_user["reference"]
        if d is not None and final["obj"]["status"] != "infeasible":
            if d.get("ship_type") != req["ship_type"]:
                bad("context_final_type_mismatch", f"{d.get('ship_type')} vs {req['ship_type']}")
            sid = final["turn"].get("source")
            r = X_.rec.get(sid)
            if r is None or r["country"] != req["country"] or C.request_year(r) != req["year"]:
                bad("context_final_country_year_mismatch", f"{sid}")
            for pd in prev_designs + ([ref] if ref else []):
                if canon(pd) == canon(d):
                    bad("context_copies_earlier_design")
                if _near_copy(pd, d):
                    bad("context_near_copy")
            info["final_same_type_as_prev"] = any(pd.get("ship_type") == d.get("ship_type") for pd in prev_designs)
        if ref is not None:
            info["ref_conflict_n"] = len(K.failed_constraints(req, ref))
        if "context_variant" not in meta:
            bad("context_variant_missing")


def _near_copy(a: dict[str, Any], b: dict[str, Any], tol: float = 0.01) -> bool:
    keys = ("length_m", "speed_kn", "beam_m")
    if not all(K._isnum(a.get(k)) and K._isnum(b.get(k)) for k in keys):
        return False
    close = lambda x, y: abs(x - y) <= tol * abs(y) + 1e-9  # noqa: E731
    da, db = C.ref_displacement(a)[0], C.ref_displacement(b)[0]
    return bool(da and db and close(da, db) and all(close(a[k], b[k]) for k in keys))


def _coupling_check(ctype: str, dA: dict[str, Any], dB: dict[str, Any], reqA: dict[str, Any], reqB: dict[str, Any]) -> str:
    """modify の連動: 関連項目が合理的な向きに動いたか。違反なら理由、問題なければ ''。"""
    pa, pb = dA.get("power_hp"), dB.get("power_hp")
    if ctype == "speed_up" and K._isnum(pa) and K._isnum(pb) and not pb > pa:
        return "speed up but power did not rise"
    if ctype == "speed_down" and K._isnum(pa) and K._isnum(pb) and not pb < pa:
        return "speed down but power did not fall"
    if ctype == "disp_down":
        basis = reqB.get("displacement_basis", "normal")
        a, b = O.disp_in_basis(dA, basis), O.disp_in_basis(dB, basis)
        if a and b and not b < a:
            return "displacement did not fall"
        moved = 0
        for f in ("length_m", "power_hp", "complement"):
            if K._isnum(dA.get(f)) and K._isnum(dB.get(f)) and dB[f] <= dA[f]:
                moved += 1
        if moved < 2 and len(O.changed_fields(dA, dB)) < 3:
            return "related fields (length, power, crew) did not follow"
    if ctype == "add_heli" and (X.aircraft_count(dB) < 1 or "helicopter" not in (dB.get("air") or [])):
        return "no helicopter / air entry"
    if ctype == "gun_barrels_up" and X.gun_barrels(dB, reqB.get("min_gun_barrels_cal_mm")) <= X.gun_barrels(dA, reqB.get("min_gun_barrels_cal_mm")):
        return "barrels (of the requested caliber) did not increase"
    if ctype == "aircraft_up" and X.aircraft_count(dB) <= X.aircraft_count(dA):
        return "aircraft count did not increase"
    return ""


# --------------------------------------------------------------------------------------------------
# overlap
# --------------------------------------------------------------------------------------------------
def load_rows(d: Path) -> dict[str, list[dict[str, Any]]]:
    out = {}
    for s in K.SPLITS:
        out[s] = [json.loads(l) for l in (d / f"{s}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return out


def main_constraints_key(req: dict[str, Any]) -> str:
    return canon({"country": req.get("country"), "year": req.get("year"), "ship_type": req.get("ship_type"), "max_displacement_t": req.get("max_displacement_t"),
                  "min_speed_kn": req.get("min_speed_kn"), "required": sorted(req.get("required") or [])})


def user_reqs(ep: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for m in ep["messages"]:
        if m["role"] == "user":
            u = json.loads(m["content"])
            if "req" in u and "request" in u:
                out.append((u["request"], u["req"]))
    return out


def overlap_check(rows: dict[str, list[dict[str, Any]]], X_: Ctx) -> dict[str, Any]:
    res: dict[str, Any] = {}
    allrows = [(s, r) for s in K.SPLITS for r in rows[s]]
    ids = [r["id"] for _, r in allrows]
    res["id_duplicates"] = len(ids) - len(set(ids))
    # exact duplicate episodes (messages after the system prompt)
    h = collections.Counter(hashlib.sha256(canon(r["messages"][1:]).encode()).hexdigest() for _, r in allrows)
    res["exact_duplicate_episodes"] = sum(v - 1 for v in h.values() if v > 1)
    # normalized request text / structured req across episodes (every request turn)
    tx: dict[str, set[int]] = collections.defaultdict(set)
    rq: dict[str, set[int]] = collections.defaultdict(set)
    for i, (s, r) in enumerate(allrows):
        for text, req in user_reqs(r):
            tx[norm_text(text)].add(i)
            rq[canon(req)].add(i)
    res["normalized_request_text_duplicates_across_episodes"] = sum(1 for v in tx.values() if len(v) > 1)
    res["structured_req_duplicates_across_episodes"] = sum(1 for v in rq.values() if len(v) > 1)

    def cross_split(d: dict[str, set[int]]) -> int:
        return sum(1 for v in d.values() if len({allrows[i][0] for i in v}) > 1)

    res["normalized_request_text_across_splits"] = cross_split(tx)
    res["structured_req_across_splits"] = cross_split(rq)
    # source ships / class groups / derived episodes never cross splits
    src_split: dict[str, set[str]] = collections.defaultdict(set)
    grp_split: dict[str, set[str]] = collections.defaultdict(set)
    ep_cross = 0
    for s, r in allrows:
        meta = r["metadata"]
        for sid in meta.get("sources", []):
            src_split[sid].add(s)
            grp_split[X_.group_of.get(sid, sid)].add(s)
        if len({X_.assign.get(sid) for sid in meta.get("sources", [])}) > 1:
            ep_cross += 1
    res["source_ship_in_multiple_splits"] = sum(1 for v in src_split.values() if len(v) > 1)
    res["class_group_in_multiple_splits"] = sum(1 for v in grp_split.values() if len(v) > 1)
    res["episodes_with_sources_from_several_splits(pair/repair/context derivations)"] = ep_cross
    res["source_split_differs_from_v0_2_assignment"] = sum(1 for sid, v in src_split.items() if X_.assign.get(sid) not in v)
    # existing datasets: design-v0 (train/valid/test/scenarios) and shipknow_design_eval_v1
    for name, paths in (("design_v0", [K.DESIGN_V0_DIR / f"{s}.jsonl" for s in ("train", "valid", "test", "scenarios")]), ("design_eval_v1", [K.EVAL_V1_DIR / "all.jsonl"])):
        texts, reqs, mains = set(), set(), set()
        n = 0
        for p in paths:
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                e = json.loads(line)
                u = json.loads(e["messages"][1]["content"])
                n += 1
                texts.add(norm_text(u["request"]))
                reqs.add(canon(u["req"]))
                mains.add(main_constraints_key(u["req"]))
        t_hit = r_hit = m_hit = 0
        by_split = {s: 0 for s in K.SPLITS}
        for s, r in allrows:
            reqs_ep = user_reqs(r)
            hit_t = any(norm_text(t) in texts for t, _ in reqs_ep)
            hit_r = any(canon(q) in reqs for _, q in reqs_ep)
            hit_m = any(main_constraints_key(q) in mains for _, q in reqs_ep)
            t_hit += hit_t
            r_hit += hit_r
            m_hit += hit_m
            if hit_t or hit_r:
                by_split[s] += 1
        res[f"vs_{name}"] = {"reference_requests": n, "episodes_with_identical_request_text": t_hit, "episodes_with_identical_structured_req": r_hit,
                              "episodes_with_identical_main_constraints(reference only)": m_hit, "episodes_by_split_with_text_or_req_hit": by_split}
    # held-out guards
    ho_combo = sum(1 for s in ("train", "valid") for r in rows[s] for _, q in user_reqs(r) if K.is_heldout_combo(q))
    res["train_valid_requests_with_test_only_combo"] = ho_combo
    fam = sum(1 for s in ("train", "valid") for r in rows[s] if r["metadata"].get("family") in I.HOLDOUT_FAMILIES and r["category"] in ("infeasible", "context", "repair"))
    res["train_valid_episodes_with_test_only_infeasible_family"] = fam
    names_split: dict[str, set[str]] = collections.defaultdict(set)
    for s, r in allrows:
        for _, q in user_reqs(r):
            if q.get("premise", {}).get("type") == "fictional_nation":
                names_split[q["country"]].add(s)
    res["fictional_name_in_multiple_splits"] = sum(1 for v in names_split.values() if len(v) > 1)
    res["fictional_names_used_by_eval_v1"] = sum(1 for n in names_split if n in {"Valdoria", "Norvane", "Kestria", "Aurelan", "Tarsonia", "Meridia", "Zephyria", "Brannoch", "Solmara", "Ysbelle"})
    res["test_only_doctrine_in_train_valid"] = sum(1 for s in ("train", "valid") for r in rows[s] if r["metadata"].get("doctrine") in ("air_centric", "asw_centric"))
    res["test_only_violation_pair_in_train_valid"] = sum(1 for s in ("train", "valid") for r in rows[s] if "violation_pair" in r["metadata"].get("holdout", []))
    res["test_only_context_variant_in_train_valid"] = sum(1 for s in ("train", "valid") for r in rows[s] if r["metadata"].get("context_variant") == "conflict_ref_long")
    res["country_pair_heldout_in_train_valid"] = sum(1 for s in ("train", "valid") for r in rows[s] if "country_pair" in r["metadata"].get("holdout", []))
    res["year_pair_heldout_in_train_valid"] = sum(1 for s in ("train", "valid") for r in rows[s] if "year_pair" in r["metadata"].get("holdout", []))
    keys = [k for k, v in res.items() if isinstance(v, int)]
    res["ok"] = all(res[k] == 0 for k in keys if k != "episodes_with_sources_from_several_splits(pair/repair/context derivations)") and \
        res["episodes_with_sources_from_several_splits(pair/repair/context derivations)"] == 0 and all(
        res[f"vs_{n}"]["episodes_with_identical_request_text"] == 0 and res[f"vs_{n}"]["episodes_with_identical_structured_req"] == 0 for n in ("design_v0", "design_eval_v1"))
    return res


# --------------------------------------------------------------------------------------------------
# quality
# --------------------------------------------------------------------------------------------------
def quality_check(rows: dict[str, list[dict[str, Any]]], X_: Ctx) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    codes: collections.Counter = collections.Counter()
    by_cat: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    examples: dict[str, list[str]] = collections.defaultdict(list)
    infos: dict[str, list[dict[str, Any]]] = {s: [] for s in K.SPLITS}
    n = 0
    bad_eps = 0
    for s in K.SPLITS:
        for r in rows[s]:
            n += 1
            probs, info = check_episode(r, X_)
            info["id"] = r["id"]
            infos[s].append(info)
            if probs:
                bad_eps += 1
            for code, detail in probs:
                codes[code] += 1
                by_cat[r.get("category", "?")][code] += 1
                if len(examples[code]) < 3:
                    examples[code].append(f"{r['id']}: {detail}")
    gaps: dict[str, list[int]] = collections.defaultdict(list)
    for s_ in K.SPLITS:
        for r, i in zip(rows[s_], infos[s_]):
            gaps[r["category"]].extend(i.get("year_gaps", []))
    year_gap = {}
    for c, g in gaps.items():
        ab = sorted(abs(x) for x in g)
        year_gap[c] = {"n_trained_designs": len(g), "median_signed(request - source)": statistics.median(g) if g else None, "median_abs": statistics.median(ab) if ab else None, "max_abs": max(ab) if ab else None,
                       "hist_abs": {"0": sum(1 for x in ab if x == 0), "1-2": sum(1 for x in ab if 1 <= x <= 2), "3-5": sum(1 for x in ab if 3 <= x <= 5), "6-7": sum(1 for x in ab if 6 <= x <= 7), ">7": sum(1 for x in ab if x > 7)},
                       "source_year_after_request(count)": sum(1 for x in g if x < 0)}
    out = {"episodes": n, "episodes_with_problems": bad_eps, "year_gap_request_vs_source": year_gap, "problem_counts": dict(codes.most_common()), "problem_counts_by_category": {c: dict(v.most_common()) for c, v in by_cat.items()},
           "examples": examples, "ok": bad_eps == 0}
    return out, infos


# --------------------------------------------------------------------------------------------------
# tokens
# --------------------------------------------------------------------------------------------------
def count_tokens(tok, ep: dict[str, Any]) -> tuple[int, int, int]:
    """(total, assistant 全ターン, 学習対象ターン) のトークン数（ChatML + Qwen3.5 の非 thinking 用の空 think ブロック分 +6。B.count_tokens と同じ規約）。"""
    m = ep["messages"]
    text = "".join(f"<|im_start|>{x['role']}\n{x['content']}<|im_end|>\n" for x in m)
    train = set(ep["metadata"]["train_turns"])
    if tok is None:
        return int(len(text) / 3.2) + 6, int(sum(len(x["content"]) for x in m if x["role"] == "assistant") / 3.2), int(sum(len(m[i]["content"]) for i in train) / 3.2)
    total = len(tok.encode(text).ids) + 6
    asst_all = sum(len(tok.encode(x["content"] + "<|im_end|>").ids) for x in m if x["role"] == "assistant")
    asst_train = sum(len(tok.encode(m[i]["content"] + "<|im_end|>").ids) for i in train)
    return total, asst_all, asst_train


def pct(xs: list[int], p: float) -> int:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0


def token_stats(rows: dict[str, list[dict[str, Any]]], tok_path: Path | None) -> dict[str, Any]:
    tok, note = B.load_tokenizer(tok_path) if tok_path else (None, "chars/3.2 estimate (no tokenizer)")
    out: dict[str, Any] = {"tokenizer": note, "by_split": {}, "by_category": {}, "by_split_category": {}}
    per_cat: dict[str, list[tuple[int, int, int]]] = collections.defaultdict(list)
    first = next((r for s_ in K.SPLITS for r in rows[s_]), None)
    sys_tokens = len(tok.encode(first["messages"][0]["content"]).ids) if tok and first else 0
    out["system_prompt_tokens"] = sys_tokens
    for s in K.SPLITS:
        vals = []
        for r in rows[s]:
            t = count_tokens(tok, r)
            vals.append(t)
            per_cat[r["category"]].append(t)
            out["by_split_category"].setdefault(f"{s}/{r['category']}", []).append(t)
        tt = [v[0] for v in vals]
        if not tt:
            continue
        out["by_split"][s] = {"episodes": len(tt), "tokens_total": sum(tt), "tokens_avg": round(statistics.mean(tt), 1), "p50": pct(tt, 0.5), "p95": pct(tt, 0.95), "max": max(tt),
                              "assistant_tokens_total": sum(v[1] for v in vals), "trained_tokens_total": sum(v[2] for v in vals)}
    for c, vals in per_cat.items():
        tt = [v[0] for v in vals]
        out["by_category"][c] = {"episodes": len(tt), "tokens_total": sum(tt), "tokens_avg": round(statistics.mean(tt), 1), "p50": pct(tt, 0.5), "p95": pct(tt, 0.95), "max": max(tt),
                                 "assistant_tokens_avg": round(statistics.mean(v[1] for v in vals), 1), "trained_tokens_avg": round(statistics.mean(v[2] for v in vals), 1)}
    out["by_split_category"] = {k: {"episodes": len(v), "tokens_avg": round(statistics.mean(x[0] for x in v), 1)} for k, v in out["by_split_category"].items()}
    return out


def full_estimate(tokens: dict[str, Any], n_full: int, size_bytes: dict[str, int], n_pilot: int, tok_per_sec: float = 2000.0) -> dict[str, Any]:
    """パイロットのカテゴリ別平均トークン数から、全量（n_full、目標比率）のトークン総量・ディスクサイズ・学習時間（RTX 3060・約 2,000 tok/s・GC あり）を見積もる。"""
    cats = {c: int(round(n_full * K.CATEGORY_RATIO[c])) for c in K.CATEGORIES}
    tot = {c: cats[c] * tokens["by_category"][c]["tokens_avg"] for c in K.CATEGORIES if c in tokens["by_category"]}
    trained = {c: cats[c] * tokens["by_category"][c]["trained_tokens_avg"] for c in K.CATEGORIES if c in tokens["by_category"]}
    all_tokens = sum(tot.values())
    train_frac = 0.8
    train_tokens = all_tokens * train_frac
    bytes_per_ep = sum(size_bytes.values()) / max(1, n_pilot)
    return {"n_full": n_full, "episodes_by_category": cats, "tokens_total_by_category": {c: int(v) for c, v in tot.items()}, "tokens_total": int(all_tokens),
            "train_tokens_total(80%)": int(train_tokens), "trained_assistant_tokens_total": int(sum(trained.values())),
            "assumed_tok_per_sec": tok_per_sec, "epoch_hours_train": round(train_tokens / tok_per_sec / 3600, 1), "epoch_hours_all_splits": round(all_tokens / tok_per_sec / 3600, 1),
            "disk_bytes_est": int(bytes_per_ep * n_full), "disk_gb_est": round(bytes_per_ep * n_full / 1e9, 2), "bytes_per_episode_pilot": int(bytes_per_ep),
            "note": "tokens: pilot per-category average x full-run category counts (ChatML + 6, Qwen3.5-0.8B tokenizer). Train = 80 % of episodes. 2,000 tok/s is the RTX 3060 + gradient checkpointing rate assumed by the user."}


# --------------------------------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------------------------------
def bin_year(y: int) -> str:
    for lo, hi in C.ERA_BINS:
        if lo <= y <= hi:
            return f"{lo}-{hi}" if hi < 2100 else f"{lo}-"
    return "<1860"


def stats(rows: dict[str, list[dict[str, Any]]], infos: dict[str, list[dict[str, Any]]], X_: Ctx, quality: dict[str, Any], overlap: dict[str, Any]) -> dict[str, Any]:
    st: dict[str, Any] = {}
    allrows = [(s, r) for s in K.SPLITS for r in rows[s]]
    st["episodes_total"] = len(allrows)
    st["by_split"] = {s: len(rows[s]) for s in K.SPLITS}
    st["by_category"] = dict(collections.Counter(r["category"] for _, r in allrows))
    st["by_category_share"] = {c: round(v / len(allrows), 4) for c, v in st["by_category"].items()}
    st["by_split_category"] = {s: dict(collections.Counter(r["category"] for r in rows[s])) for s in K.SPLITS}
    st["by_subtype"] = {c: dict(collections.Counter(r["metadata"]["subtype"] for _, r in allrows if r["category"] == c).most_common()) for c in K.CATEGORIES}
    st["user_turns_by_category"] = {c: dict(sorted(collections.Counter(r["metadata"]["turns_user"] for _, r in allrows if r["category"] == c).items())) for c in K.CATEGORIES}
    st["assistant_turns"] = {"total": sum(len(r["metadata"]["turns"]) for _, r in allrows), "trained": sum(len(r["metadata"]["train_turns"]) for _, r in allrows)}
    # distributions over the final request of each episode
    fin = [(s, r, i["final_req"]) for s in K.SPLITS for r, i in zip(rows[s], infos[s]) if "final_req" in i]
    st["year_bins_final_req"] = dict(sorted(collections.Counter(bin_year(q["year"]) for _, _, q in fin).items()))
    st["country_top30_final_req"] = dict(collections.Counter(q["country"] for _, _, q in fin).most_common(30))
    st["n_countries_final_req"] = len({q["country"] for _, _, q in fin})
    st["ship_type_final_req"] = dict(collections.Counter(q["ship_type"] for _, _, q in fin).most_common())
    st["mission_final_req"] = dict(collections.Counter(m for _, _, q in fin for m in q.get("mission", [])).most_common())
    st["ship_type_by_category"] = {c: dict(collections.Counter(q["ship_type"] for s, r, q in fin if r["category"] == c).most_common(8)) for c in K.CATEGORIES}
    # requirement keys across every request turn
    key_req: collections.Counter = collections.Counter()
    key_ep: collections.Counter = collections.Counter()
    key_by_cat: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    n_req_turns = 0
    cons: collections.Counter = collections.Counter()
    for s, r in allrows:
        seen_ep = set()
        for text, q in user_reqs(r):
            n_req_turns += 1
            for k in q:
                key_req[k] += 1
                seen_ep.add(k)
                key_by_cat[r["category"]][k] += 1
            for cid in K.constraint_ids(q):
                cons[re.sub(r"main_gun>=\d+(\.\d+)?mm", "main_gun>=Nmm", cid)] += 1
        key_ep.update(seen_ep)
    st["request_turns_total"] = n_req_turns
    st["requirement_key_counts_over_request_turns"] = dict(key_req.most_common())
    st["requirement_key_counts_episodes_containing"] = dict(key_ep.most_common())
    st["requirement_key_counts_by_category"] = {c: dict(v.most_common()) for c, v in key_by_cat.items()}
    st["constraint_occurrence_over_request_turns"] = dict(cons.most_common())
    st["constraint_occurrence_rate"] = {k: round(v / max(1, n_req_turns), 4) for k, v in cons.most_common()}
    # statuses (trained assistant turns)
    status_all: collections.Counter = collections.Counter()
    status_cat: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    origin: collections.Counter = collections.Counter()
    origin_cat: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for s, r in allrows:
        tt = set(r["metadata"]["train_turns"])
        for t in r["metadata"]["turns"]:
            if t["idx"] in tt:
                status_all[t["status"]] += 1
                status_cat[r["category"]][t["status"]] += 1
                origin[t.get("origin")] += 1
                origin_cat[r["category"]][t.get("origin")] += 1
    n_tr = sum(status_all.values())
    st["status_trained_turns"] = {"counts": dict(status_all), "share": {k: round(v / n_tr, 4) for k, v in status_all.items()}}
    st["status_trained_turns_by_category"] = {c: dict(v) for c, v in status_cat.items()}
    n_design_turns = sum(v for k, v in origin.items() if k != "none")
    real_n = origin.get("real", 0)
    st["design_origin_trained_turns"] = {"counts": dict(origin), "by_category": {c: dict(v) for c, v in origin_cat.items()},
                                         "note": "real = a real ship's design verbatim from records; scaled = a real design rescaled by shipknow_design_v1_ops (physical coupling); tech_added = a real design of the request year plus only the premised technology (ood); repaired = rule-based fix of a corrupted real design; gold = repair fallback (real design); none = infeasible (design null)",
                                         "real_share_of_designs": round((real_n + origin.get("gold", 0)) / max(1, n_design_turns), 4),
                                         "derived_share_of_designs": round((origin.get("scaled", 0) + origin.get("repaired", 0) + origin.get("tech_added", 0)) / max(1, n_design_turns), 4)}
    # modify
    mod = [(r, i) for s in K.SPLITS for r, i in zip(rows[s], infos[s]) if r["category"] == "modify"]
    st["modify"] = {"changes": dict(collections.Counter(c for r, _ in mod for c in r["metadata"].get("changes", [])).most_common()),
                    "mode": dict(collections.Counter(r["metadata"].get("mode") for r, _ in mod)),
                    "route": dict(collections.Counter(x for _, i in mod for x in i.get("route", []))),
                    "changed_design_fields_hist": dict(sorted(collections.Counter(min(n, 12) for _, i in mod for n in i.get("changed_n", [])).items())),
                    "single_field_edits": sum(i.get("single_field_edit", 0) for _, i in mod), "coupling_tags": dict(collections.Counter(t for r, _ in mod for t in r["metadata"].get("coupling", [])).most_common()),
                    "status_of_final_turn": dict(collections.Counter(r["metadata"]["status_seq"][-1] for r, _ in mod))}
    # repair
    rep = [(r, i) for s in K.SPLITS for r, i in zip(rows[s], infos[s]) if r["category"] == "repair"]
    st["repair"] = {"violation_kinds": dict(collections.Counter(k for r, _ in rep for k in r["metadata"].get("violation_kinds", [])).most_common()),
                    "violation_codes": dict(collections.Counter(c for r, _ in rep for c in r["metadata"].get("violation_codes", [])).most_common(40)),
                    "violations_per_episode": dict(sorted(collections.Counter(len(r["metadata"].get("violation_codes", [])) for r, _ in rep).items())),
                    "kinds_per_episode": dict(sorted(collections.Counter(len(r["metadata"].get("violation_kinds", [])) for r, _ in rep).items())),
                    "repair_method": dict(collections.Counter(r["metadata"].get("repair_method") for r, _ in rep)), "rounds": dict(collections.Counter(r["metadata"].get("rounds") for r, _ in rep)),
                    "final_status": dict(collections.Counter(r["metadata"]["status_seq"][-1] for r, _ in rep)),
                    "n_episodes": len(rep)}
    # infeasible / ood / context
    inf = [r for _, r in allrows if r["category"] == "infeasible"]
    st["infeasible"] = {"families": dict(collections.Counter(r["metadata"].get("family") for r in inf).most_common()), "status": dict(collections.Counter(r["metadata"]["status_seq"][-1] for r in inf)),
                        "status_by_family": {f: dict(collections.Counter(r["metadata"]["status_seq"][-1] for r in inf if r["metadata"].get("family") == f)) for f in sorted({r["metadata"].get("family") for r in inf})}}
    ood = [r for _, r in allrows if r["category"] == "ood"]
    st["ood"] = {"premise_type": dict(collections.Counter(r["metadata"].get("premise_type") for r in ood)), "tech": dict(collections.Counter(r["metadata"].get("tech") for r in ood if r["metadata"].get("tech"))),
                 "doctrine": dict(collections.Counter(r["metadata"].get("doctrine") for r in ood if r["metadata"].get("doctrine"))), "status": dict(collections.Counter(r["metadata"]["status_seq"][-1] for r in ood)),
                 "fictional_names": dict(collections.Counter(r["metadata"].get("fictional_name") for r in ood if r["metadata"].get("fictional_name")))}
    ctx = [(r, i) for s in K.SPLITS for r, i in zip(rows[s], infos[s]) if r["category"] == "context"]
    st["context"] = {"variants": dict(collections.Counter(r["metadata"].get("context_variant") for r, _ in ctx)), "turns": dict(collections.Counter(r["metadata"]["turns_user"] for r, _ in ctx)),
                     "with_reference": sum(1 for r, _ in ctx if r["metadata"].get("has_reference")), "final_status": dict(collections.Counter(r["metadata"]["status_seq"][-1] for r, _ in ctx)),
                     "final_type_equals_prev_type": sum(1 for _, i in ctx if i.get("final_same_type_as_prev")),
                     "reference_conflicts_hist": dict(sorted(collections.Counter(min(5, i["ref_conflict_n"]) for _, i in ctx if "ref_conflict_n" in i).items()))}
    # the individual counters asked for
    def req_count(key: str) -> dict[str, Any]:
        eps = {c: sum(1 for s, r in allrows if r["category"] == c and any(key in q for _, q in user_reqs(r))) for c in K.CATEGORIES}
        final_n = sum(1 for _, _, q in fin if key in q)
        return {"request_turns": key_req.get(key, 0), "episodes_containing": key_ep.get(key, 0), "episodes_by_category": eps, "episodes_final_req": final_n}

    modsub = collections.Counter(r["metadata"]["subtype"] for _, r in allrows if r["category"] == "modify")
    spd_mod = sum(1 for r, _ in mod if any(c in ("speed_up", "speed_down") for ch in r["metadata"].get("changes", []) + [x for t in r["metadata"]["turns"] for x in str(t.get("change", "")).split("+")] for c in [ch]))
    spd_rep = sum(1 for r, _ in rep if "speed_power" in r["metadata"].get("violation_kinds", []))
    spd_inf = sum(1 for r in inf if r["metadata"].get("family") == "speed")
    air_mod = sum(1 for r, _ in mod if any(x in ("add_heli", "aircraft_up") or x.startswith("add_heli") for t in r["metadata"]["turns"] for x in str(t.get("change", "")).split("+")))
    air_mod += sum(1 for r, _ in mod if "aircraft_disp" in r["metadata"].get("coupling", []) and not any(x in ("add_heli", "aircraft_up") for t in r["metadata"]["turns"] for x in str(t.get("change", "")).split("+")))
    air_rep = sum(1 for r, _ in rep if {"aircraft_size", "aircraft_count_short"} & set(r["metadata"].get("violation_kinds", [])))
    air_inf = sum(1 for r in inf if r["metadata"].get("family") in ("aircraft", "extreme"))
    st["individual_counts"] = {
        "gun_barrels(min_gun_barrels)": req_count("min_gun_barrels"), "aircraft_count(min_aircraft)": req_count("min_aircraft"), "torpedo_tubes(min_torpedo_tubes)": req_count("min_torpedo_tubes"),
        "required_propulsion": req_count("required_propulsion"), "min_displacement_t": req_count("min_displacement_t"), "max_speed_kn": req_count("max_speed_kn"), "premise": req_count("premise"),
        "speed_power_episodes": {"modify(speed_up/speed_down in any turn)": spd_mod, "repair(speed_power kind)": spd_rep, "infeasible(speed family)": spd_inf, "total": spd_mod + spd_rep + spd_inf,
                                 "modify_with_speed_power_coupling_tag": sum(1 for r, _ in mod if "speed_power" in r["metadata"].get("coupling", []))},
        "aircraft_displacement_episodes": {"modify(add_heli/aircraft_up or aircraft_disp coupling)": air_mod, "repair(aircraft_size/aircraft_count_short)": air_rep, "infeasible(aircraft/extreme)": air_inf,
                                           "total": air_mod + air_rep + air_inf},
        "context_robustness_episodes": {"total": len(ctx), "by_variant": dict(collections.Counter(r["metadata"].get("context_variant") for r, _ in ctx)),
                                        "with_reference": sum(1 for r, _ in ctx if r["metadata"].get("has_reference")),
                                        "final_not_a_copy_checked": sum(1 for _, i in ctx if "final_same_type_as_prev" in i)},
    }
    # validator pass rates
    n_feas = n_feas_ok = n_rel = n_rel_ok = 0
    n_init = n_init_viol = 0
    for s in K.SPLITS:
        for r in rows[s]:
            msgs = r["messages"]
            reqc = None
            for i, m in enumerate(msgs[1:], start=1):
                if m["role"] == "user":
                    u = json.loads(m["content"])
                    if "req" in u:
                        reqc = u["req"]
                else:
                    t = next(t for t in r["metadata"]["turns"] if t["idx"] == i)
                    o = json.loads(m["content"])
                    if o["design"] is None:
                        continue
                    w = K.waivers_for_premise(reqc.get("premise"))
                    if t["role"] in ("initial", "partial_fix"):
                        n_init += 1
                        n_init_viol += not K.check_design(reqc, o["design"], w)["ok"]
                        continue
                    if o["status"] == "feasible":
                        n_feas += 1
                        n_feas_ok += K.check_design(reqc, o["design"], w)["ok"]
                    else:
                        n_rel += 1
                        n_rel_ok += K.check_design(K.apply_relaxations(reqc, o["relaxations"]), o["design"], w)["ok"]
    st["validator"] = {"feasible_designs": n_feas, "feasible_pass_rate": round(n_feas_ok / max(1, n_feas), 4), "relaxed_designs(vs_relaxed_req)": n_rel, "relaxed_pass_rate": round(n_rel_ok / max(1, n_rel), 4),
                       "corrupted_initial_designs": n_init, "corrupted_detected_rate": round(n_init_viol / max(1, n_init), 4),
                       "gold_real_designs_rejected_before_use": X_.manifest.get("gold_rejected_by_validator"), "eligible_real_designs": X_.manifest.get("eligible_by_split"),
                       "note": "pass rates are over the designs that were kept (gold designs that fail the validator on their own are dropped at generation: gold_rejected_by_validator)"}
    st["year_gap_request_vs_source"] = quality["year_gap_request_vs_source"]
    st["quality_ok"] = quality["ok"]
    st["overlap_ok"] = overlap["ok"]
    st["overlap_summary"] = {k: v for k, v in overlap.items() if isinstance(v, int)}
    return st


# --------------------------------------------------------------------------------------------------
# samples
# --------------------------------------------------------------------------------------------------
def design_line(d: dict[str, Any] | None) -> str:
    if d is None:
        return "design: null"
    w = ", ".join(f"{x['kind']} {x.get('n', '')}{'x' + str(x['per']) if x.get('per') else ''}{' ' + format(x['cal'], 'g') + 'mm' if x.get('cal') else ''}".strip() for x in d.get("weapons", []))
    disp = "/".join(f"{k[13:-2]} {d[k]}" for k in ("displacement_normal_t", "displacement_full_t", "displacement_standard_t") if k in d)
    arm = d.get("armour")
    return (f"{d.get('ship_type')} / {d.get('ship_type_fine')}; disp {disp} t; {d.get('length_m')}x{d.get('beam_m')}x{d.get('draught_m')} m; {d.get('power_hp')} hp {d.get('propulsion')} x{d.get('shafts')} shaft; "
            f"{d.get('speed_kn')} kn; crew {d.get('complement')}; endurance {d.get('endurance_nm')} nm; armour {arm}; sensors {d.get('sensors')}; air {d.get('air')}; weapons [{w}] "
            f"(gun barrels {X.gun_barrels(d)}, aircraft {X.aircraft_count(d)}, torpedo tubes {X.torpedo_tubes(d)})")


def render_episode(r: dict[str, Any], n: int) -> str:
    meta = r["metadata"]
    lines = [f"### {n}. `{r['id']}` — {r['category']} / {meta['subtype']} ({meta['split']})", ""]
    keys = {k: meta[k] for k in ("status_seq", "train_turns", "sources", "unseen_combo", "holdout", "premise_type", "violation_kinds", "repair_method", "rounds", "changes", "mode", "coupling", "family",
                                 "context_variant", "waivers") if k in meta and meta[k] not in (None, [], "")}
    lines.append("metadata: " + json.dumps(keys, ensure_ascii=False))
    lines.append("")
    prev_design = None
    for i, m in enumerate(r["messages"][1:], start=1):
        if m["role"] == "user":
            u = json.loads(m["content"])
            if "validator" in u:
                lines.append("**user (validator feedback)**")
                for v in u["validator"]["violations"]:
                    lines.append(f"- `{v['code']}` `{v['field']}`: {v['detail']}")
            else:
                lines.append("**user**: " + u["request"])
                lines.append("")
                lines.append("`req` = `" + json.dumps(u["req"], ensure_ascii=False) + "`" + (f"  (+ {len(u['nbr'])} neighbours)" if u.get("nbr") else ""))
                if "reference" in u:
                    lines.append("")
                    lines.append("reference design: " + design_line(u["reference"]))
        else:
            o = json.loads(m["content"])
            t = next((t for t in meta["turns"] if t["idx"] == i), {})
            trained = "LOSS" if i in meta["train_turns"] else "no loss"
            lines.append(f"**assistant** [{o['status']}; {t.get('role')}; origin {t.get('origin')}; {trained}]")
            for c in o["conflicts"]:
                lines.append(f"- conflict `{c['constraint']}`: {c['reason']}")
            for x in o["relaxations"]:
                lines.append(f"- relaxed `{x['constraint']}`: requested {x['requested']} -> {x['relaxed_to']} ({x['reason']})")
            lines.append("")
            lines.append(design_line(o["design"]))
            if prev_design is not None and o["design"] is not None and prev_design != o["design"]:
                lines.append("")
                lines.append("changed fields vs previous design: " + ", ".join(O.changed_fields(prev_design, o["design"])))
            if o["design"] is not None:
                prev_design = o["design"]
        lines.append("")
    return "\n".join(lines)


def write_samples(d: Path, rows: dict[str, list[dict[str, Any]]], per_cat: int) -> dict[str, int]:
    (d / "samples").mkdir(exist_ok=True)
    out = {}
    for c in K.CATEGORIES:
        eps = [r for s in K.SPLITS for r in rows[s] if r["category"] == c]
        # stratify over (split, subtype), round robin, deterministic
        groups: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
        for r in sorted(eps, key=lambda r: r["id"]):
            groups[(r["metadata"]["split"], r["metadata"]["subtype"])].append(r)
        picked: list[dict[str, Any]] = []
        keys = sorted(groups, key=lambda k: (k[0] != "train", k))
        while len(picked) < min(per_cat, len(eps)):
            moved = False
            for k in keys:
                if groups[k] and len(picked) < per_cat:
                    picked.append(groups[k].pop(0))
                    moved = True
            if not moved:
                break
        head = [f"# design-v1 samples: {c} ({len(picked)} of {len(eps)} episodes)", "",
                "system prompt omitted (identical for every episode, see manifest.json). neighbours (`nbr`) are summarised as a count. LOSS = assistant turn with training loss (metadata.train_turns).", ""]
        (d / "samples" / f"{c}.md").write_text("\n".join(head) + "\n" + "\n---\n\n".join(render_episode(r, i + 1) for i, r in enumerate(picked)) + "\n", encoding="utf-8")
        out[c] = len(picked)
    return out


# --------------------------------------------------------------------------------------------------
# README
# --------------------------------------------------------------------------------------------------
def md_table(header: list[str], rows_: list[list[Any]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows_:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def pct_s(x: float) -> str:
    return f"{100 * x:.1f}%"


def write_readme(d: Path, manifest: dict[str, Any], st: dict[str, Any], quality: dict[str, Any], overlap: dict[str, Any], tokens: dict[str, Any], est: dict[str, Any] | None,
                 samples: dict[str, int], timing: dict[str, Any]) -> None:
    name = d.name
    n = st["episodes_total"]
    cats = K.CATEGORIES
    L: list[str] = []
    A = L.append
    A(f"# shipknow design-v1 データセット（{name}）")
    A("")
    A(f"作成: {time.strftime('%Y-%m-%d')} / 生成: `tools/shipknow_build_design_v1.py`（LLM・GPU・他マシン不使用、seed {manifest['seed']} 固定で再現可能）/ 検査: `tools/shipknow_check_design_v1.py`。")
    A("**学習はしていない。** design-v0（要求 -> 設計 JSON）の弱点（砲門数・搭載機数・魚雷管数の要求を学習していない、速力と出力の連動、不可能な要求を黙って緩和 / 数字で埋める、"
      "1-shot で手本の艦種に引きずられる）を直すための、会話エピソード形式のデータ。")
    A("")
    A("## 1. 形式")
    A("")
    A("- `train.jsonl` / `valid.jsonl` / `test.jsonl`: 1 行 = 1 エピソード `{\"id\": \"dv1_xxxxxxxx\", \"category\": single|modify|repair|infeasible|ood|context, \"messages\": [...], \"metadata\": {...}}`。")
    A("- `messages` は chat template にそのまま投入できる（role は system / user / assistant、**content は全て文字列**）。先頭は design-v1 用 system（design-v0 の system に出力 schema・status の定義・「最新の user 要求に答える」「要求を黙って無視しない」「nbr はヒント」を足して 250 トークン以下に書き直した短縮版。nbr の略語表は省略。全文は `manifest.json`）。")
    A("- user の content は JSON 文字列: 最初の要求 `{\"request\": 自然文, \"req\": 構造化要求, \"nbr\": 近傍（規則 NB-1、train の実艦のみ）}`、modify の 2 ターン目以降 `{\"request\": 変更の自然文, \"req\": 更新後の全要求}`"
      "（国・年の変更では `nbr` も付く）、repair の feedback `{\"validator\": {\"valid\": false, \"violations\": [{code, field, detail}]}, \"instruction\": ...}`、context の参考例は `\"reference\": 設計 JSON`。")
    A("- assistant の content は JSON 文字列 1 つだけ（自然文なし）: `{\"status\": \"feasible|relaxed|infeasible\", \"conflicts\": [{\"constraint\", \"reason\"}], "
      "\"relaxations\": [{\"constraint\", \"requested\", \"relaxed_to\", \"reason\"}], \"design\": {...}|null}`。`design` は design-v0 と同じキー・語彙。")
    A("- 要求 `req` のキー: 国・年・艦種（+細分類）・任務・排水量上限 / 下限（`max_displacement_t` / `min_displacement_t`、基準 `displacement_basis`）・速力下限 / 上限（`min_speed_kn` / `max_speed_kn`）・"
      "乗員上限・航続下限・必須装備 `required`（主砲口径 `main_gun>=Nmm`、魚雷発射管、SAM / SSM / CIWS / 対潜、各種レーダー、ソナー、ESM、ヘリ・航空機 ...）・"
      "**砲門数 `min_gun_barrels`（+口径 `min_gun_barrels_cal_mm`）・搭載機数 `min_aircraft`・魚雷管数 `min_torpedo_tubes`**・推進方式 `required_propulsion`・前提 `premise`（ood）。")
    A("- **損失を取るターン**: `metadata.train_turns`（assistant ターンの message index）。repair の初期設計（意図的に違反を含むが status = feasible を名乗る）と context の過去ターンには損失を取らない。")
    A("")
    A("## 2. 構成")
    A("")
    A(f"総件数 {n:,}（目標比率 single 30% / modify 20% / repair 25% / infeasible・relaxation 10% / ood 5% / context 10%）。")
    A("")
    A(md_table(["category", "件数", "比率", "train", "valid", "test", "user ターン数（分布）", "サブタイプ上位"],
               [[c, f"{st['by_category'].get(c, 0):,}", pct_s(st["by_category_share"].get(c, 0)), st["by_split_category"]["train"].get(c, 0), st["by_split_category"]["valid"].get(c, 0),
                 st["by_split_category"]["test"].get(c, 0), json.dumps(st["user_turns_by_category"].get(c, {})), ", ".join(f"{k} {v}" for k, v in list(st["by_subtype"].get(c, {}).items())[:6])] for c in cats]))
    A("")
    A(f"split 件数: train {st['by_split']['train']:,} / valid {st['by_split']['valid']:,} / test {st['by_split']['test']:,}。分割は shipknow_v0_2 の艦級グループ割り当てを継承（1 エピソードに関わる実艦は全て同じ split）。")
    A(f"assistant ターン {st['assistant_turns']['total']:,}（うち損失対象 {st['assistant_turns']['trained']:,}）。")
    A("")
    A("## 3. カテゴリ別の生成方式")
    A("")
    A("**single** 1 ターン。元艦（実艦、validator 合格のもの）の設計を正解とし、要求は元艦から逆算（排水量上限 +5〜20%、最低速力 −0〜2kn、必須装備、乗員上限、航続下限）に拡張キー"
      "（砲門数・搭載機数・魚雷管数・推進方式・排水量下限・速力上限）を足す。拡張キーは必ず元艦が満たす。元艦の艦種は件数^0.6 に比例して選ぶ（patrol 偏重を弱める）。要求文は 6 通りの文型。")
    A("")
    A("**modify** 同じ会話で直前の設計の条件を変更する（排水量±、速力±、航続、主砲口径、砲門数、SAM / SSM / CIWS / ヘリ追加、搭載機数、乗員削減、国変更、年代変更、推進変更、"
      "排水量上限の引き上げ + 別の要望）。1 変更 60% / 同時 2 変更 25% / 2 ターン連鎖 15%。変更後の設計 B は (1) A の元艦の艦級グループ外で、A に近い実艦（同 split・同艦種・近い年・近い規模・武装が似る）"
      "のうち新しい要求を全て満たすもの（real、70% の確率で優先）、無ければ (2) A の物理スケーリング（scaled）。scaled は `shipknow_design_v1_ops.rederive`: 排水量 Δ と速力 V を変えたとき、"
      "L = slender·Δ^(1/3)、B = L/(L/B)、T = Δ/(cb·L·B)、P = Δ^(2/3)·V^3/adm（Admiralty 係数）、乗員 = (乗員/Δ)·Δ を、validator と同じ (艦種×年代) 層の回帰の予測 + 元の設計の残差で決める"
      "（速力↑ -> 出力は立方・艦が長くなる、排水量↓ -> 寸法・乗員・出力・武装が縮む）。砲・ミサイル・航空機・推進の追加は重量（砲は 0.0005·口径^2.3 t/門 ×1.5）を排水量に反映して再計算し、"
      "必要なら上限の引き上げを user の文に足す。どちらも check_design（validator + 要求充足）に合格したものだけを採る。国変更・年代変更は real のみ（年代変更で要求の装備が年代的に不可能になれば relaxed / infeasible）。")
    A("")
    A("**repair**（中心データ）user 要求 -> assistant 初期設計（正解設計を決定論的に壊す。status は feasible を名乗る）-> user「validator: valid=false, violations=[...]」-> assistant 修正版。"
      "違反は 10 種（速力と出力の不整合、排水量に対し兵装過多、艦載機と艦サイズの不整合、時代不相応な兵装、時代不相応なセンサー、砲門数不足、魚雷管数不足、搭載機数不足、推進と年代の不整合、要求未充足）で、"
      "壊した結果を check_design にかけ、狙った種類の違反が実際に検出されるまで強さを上げる（検出されなければ捨てる）。user に見せる violations は validator の実出力（detail は 160 字で切る）。"
      "**修正版は auto_repair**: 違反を 1 つずつ種類ごとの規則（出力の再計算、小口径砲から削る、時代外の装備を外す、砲塔を増やして重量を排水量へ ...）で直し、check_design を再実行して合格するまで繰り返す"
      "（最大 12 回）。合格しなければ元の実艦設計（`repair_method = gold_fallback`）。2 ラウンドの例は 1 回目の修正で一部だけ直し（違反を減らし、新しい違反は作らない）、validator が残りを指摘してから最終版を出す（途中の修正は損失対象外）。"
      "「元の要求が不可能」の例（`repair_method = transition`）は、要求を黙って満たさない設計 -> validator が req 違反を返す -> relaxed / infeasible に遷移する。各割合は 6 節の repair の統計（修正方法・ラウンド）。")
    A("")
    A("**infeasible / relaxation** 満たせる要求（元艦由来）に衝突を注入し、元艦（同 split）を緩和後の設計にする。衝突の家族: era_cap / era_multi / era_core（年代にない技術）、hull_type（その年にない艦種）、"
      "speed / gun_barrels / gun_cal / aircraft / torpedo / crew（実データの到達可能な最良値を超える）、contradiction（min と max が矛盾）、extreme（極小排水量 + 極端な兵装）、type_size（艦種の大きさの帯の外）。"
      "「不可能」= 年代の衝突は能力の初出年（train から）より 10 年以上前、数値・装備は train の実艦（同艦種・要求年+3 年以内）で要求の全制約を満たすものが 0 隻。")
    A("")
    A("**relaxed と infeasible の判定基準**（`shipknow_design_v1_infeasible.decide_status`）: 次を全て満たすときだけ relaxed、1 つでも外れれば infeasible。")
    A("1. 全ての衝突が緩和可能（hull_type / contradiction / aircraft_zero〈その大きさで搭載機を持つ実艦が無い〉/ type_size は緩和不可）")
    A(f"2. 衝突が {I.MAX_RELAXED_CONFLICTS} 個以下、かつ 衝突数 × 2 <= 制約の総数")
    A("3. 任務の中核の制約を緩和しない（主任務 aaw の SAM、asw の対潜兵装・ソナー、carrier_air の航空機）")
    A("4. 数値の衝突は、要求値が到達可能な最良値を超える割合 gap が上限以内: " + "、".join(f"{k} {v}" for k, v in I.RELAX_GAP_MAX.items()))
    A("")
    A("relaxed の design は、緩和した制約だけを満たさず、他の制約と validator を満たす実艦ベースの設計（`relaxed_to` は設計の実際の値）。infeasible は design = null、relaxations は空。"
      "gap が中間（曖昧）になる例は生成時に捨てる。")
    A("")
    A("**ood** 前提が変えるものだけを変える。fictional_nation（国名だけ架空。設計は「似ている」と宣言した実在国の実艦）、"
      "**tech_early**（名指しした技術だけ前倒し。元艦は**要求年と同じ年の実艦**〔その技術をまだ持たず、足せる大きさのもの〕で、設計は「その実艦 + 前提の技術だけ」: "
      "センサーは sensors に追加、ミサイル・CIWS は小さな 1 項目、ヘリは 1 機、推進は機関の差し替え。重量・排水量・寸法・乗員は足した分だけ再計算。推進・魚雷口径・他の装備を後年の艦から持ち込まない。"
      "要求は足した設計で満たせる範囲で作り、validator の年代検査 era_early はその技術の能力語彙だけを免除。`premise` が無ければ違反になることを確認）、"
      "tech_contrast（前提の外の技術を要求に足した例 = relaxed）、war_outcome（戦争結果の変更。技術は史実のまま）、doctrine（兵装の重点だけを変え、構造化要求の制約として明示）。")
    A("")
    A("**context** 2〜4 ターン。過去の assistant 出力・別艦の設計・参考例（`reference`）が文脈にあっても最新の user 要求に答える。変種: diff / long_prev（過去の出力が長い）/ reference / "
      "conflict_ref（参考例が現要求と衝突）/ final_infeasible / prev_infeasible / conflict_ref_long（test 専用）。損失は最後のターンだけ。自動検査: 最後の設計の艦種・国・年が最新要求に一致し、"
      "過去の設計・参考例の複製でない。")
    A("")
    A("## 4. split と test 専用")
    A("")
    A(f"- 分割: shipknow_v0_2 の艦級グループ割り当てを継承。train / valid / test = 約 80 / 10 / 10%（カテゴリごと）。近傍は train の実艦のみ。")
    A(f"- test 専用（train / valid に出さない。`metadata.holdout` / `unseen_combo`）: 未見の要求の組み合わせ（艦種 + 制約の種別の署名の hash 1/{K.HELDOUT_COMBO_MOD}）、架空国家名（train {len(manifest['holdout']['fictional_names']['train'])} / "
      f"valid {len(manifest['holdout']['fictional_names']['valid'])} / test {len(manifest['holdout']['fictional_names']['test'])} 名、互いに素、eval_v1 の名前も不使用）、"
      f"ドクトリン {', '.join(manifest['holdout']['doctrines'])}、infeasible の家族 {', '.join(manifest['holdout']['infeasible_families'])}、repair の違反の種類の組み合わせ（hash 1/{RP_MOD()}）、"
      f"modify の国変更の国の組（hash 1/{DD_MOD()}）と年代変更の（国・十年の組）（hash 1/{DD_YMOD()}）、context の変種 conflict_ref_long。")
    A("")
    A("## 5. 検査結果")
    A("")
    A(f"- 品質検査（全 {quality['episodes']:,} 件）: 問題のあるエピソード **{quality['episodes_with_problems']}** 件 -> {'PASS' if quality['ok'] else 'FAIL'}。"
      + (" 内訳: " + json.dumps(quality["problem_counts"], ensure_ascii=False) if quality["problem_counts"] else ""))
    A(f"- 重複検査（`overlap_check.json`）: **{'PASS' if overlap['ok'] else 'FAIL'}**。")
    A("")
    A("**元艦の年代の検査**（全カテゴリ。損失対象ターンの設計が実艦由来、またはその修正・スケーリング・技術追加の元である場合、|要求年 − 元艦年| を記録。modify の year_change / country_change は 7 年、それ以外は 5 年を超えるとエラー。infeasible で design = null は対象外）:")
    A("")
    yg = quality["year_gap_request_vs_source"]
    A(md_table(["category", "対象設計数", "要求年−元艦年の中央値", "|差| の中央値", "|差| 最大", "|差| 0 / 1-2 / 3-5 / 6-7 / >7", "元艦年 > 要求年"],
               [[c, v["n_trained_designs"], v["median_signed(request - source)"], v["median_abs"], v["max_abs"], " / ".join(str(v["hist_abs"][k]) for k in ("0", "1-2", "3-5", "6-7", ">7")), v["source_year_after_request(count)"]] for c, v in yg.items()]))
    A("")
    rows_ov = [[k, v] for k, v in overlap.items() if isinstance(v, int)]
    A(md_table(["検査", "件数"], rows_ov))
    for nme in ("design_v0", "design_eval_v1"):
        v = overlap[f"vs_{nme}"]
        A("")
        A(f"vs {nme}（参照 {v['reference_requests']:,} 要求）: 要求文一致 {v['episodes_with_identical_request_text']}、構造化 req 一致 {v['episodes_with_identical_structured_req']}、"
          f"（参考）主要制約（国・年・艦種・排水量上限・最低速力・必須装備）の一致 {v['episodes_with_identical_main_constraints(reference only)']}")
    A("")
    vv = st["validator"]
    A(f"- validator 合格率: feasible の設計 {vv['feasible_designs']:,} 件の **{pct_s(vv['feasible_pass_rate'])}**、relaxed の設計 {vv['relaxed_designs(vs_relaxed_req)']:,} 件（緩和後の要求に対して）"
      f"**{pct_s(vv['relaxed_pass_rate'])}**。repair の初期設計 {vv['corrupted_initial_designs']:,} 件は **{pct_s(vv['corrupted_detected_rate'])}** で違反を検出。"
      f"元艦の設計が単独で validator に落ちたため gold に使わなかった実艦: {vv['gold_real_designs_rejected_before_use']} / 適格 {sum(vv['eligible_real_designs'].values()) + (vv['gold_real_designs_rejected_before_use'] or 0)}。")
    A("")
    A("## 6. 統計")
    A("")
    A("### status（損失対象ターン）")
    A("")
    sc = st["status_trained_turns"]
    A(md_table(["status", "件数", "比率"], [[k, v, pct_s(sc["share"][k])] for k, v in sc["counts"].items()]))
    A("")
    A(md_table(["category"] + list(K.STATUSES), [[c] + [st["status_trained_turns_by_category"].get(c, {}).get(s, 0) for s in K.STATUSES] for c in cats]))
    A("")
    do = st["design_origin_trained_turns"]
    A(f"### 設計の由来（損失対象ターン）: 元データ由来（実艦そのまま + gold fallback）{pct_s(do['real_share_of_designs'])} / 合成（物理スケーリング・自動修正）{pct_s(do['derived_share_of_designs'])}（設計のあるターンに対して）")
    A("")
    A(md_table(["category"] + sorted({k for v in do["by_category"].values() for k in v}), [[c] + [do["by_category"][c].get(k, 0) for k in sorted({k for v in do["by_category"].values() for k in v})] for c in cats if c in do["by_category"]]))
    A("")
    A("### 個別の件数（要求したもの）")
    A("")
    ic = st["individual_counts"]
    A(md_table(["項目", "request ターン数", "含むエピソード数", "カテゴリ別（エピソード）"],
               [[k, v["request_turns"], v["episodes_containing"], json.dumps(v["episodes_by_category"])] for k, v in ic.items() if isinstance(v, dict) and "request_turns" in v]))
    A("")
    A(f"- 速力↔出力（連動）: {json.dumps(ic['speed_power_episodes'], ensure_ascii=False)}")
    A(f"- 搭載機↔排水量（艦サイズ）: {json.dumps(ic['aircraft_displacement_episodes'], ensure_ascii=False)}")
    A(f"- context robustness: {json.dumps(ic['context_robustness_episodes'], ensure_ascii=False)}")
    A("")
    A("### 制約の出現率（全 request ターンあたり）")
    A("")
    A(md_table(["constraint", "回数", "率"], [[k, st["constraint_occurrence_over_request_turns"][k], pct_s(v)] for k, v in list(st["constraint_occurrence_rate"].items())[:40]]))
    A("")
    A("### 年代・国・艦種（最後の要求）")
    A("")
    A("年代: " + ", ".join(f"{k}: {v}" for k, v in st["year_bins_final_req"].items()) + f" / 国の種類 {st['n_countries_final_req']}（上位: " + ", ".join(f"{k} {v}" for k, v in list(st["country_top30_final_req"].items())[:12]) + "）")
    A("")
    A("艦種: " + ", ".join(f"{k} {v}" for k, v in st["ship_type_final_req"].items()))
    A("")
    A("### modify")
    A("")
    m = st["modify"]
    A(f"変更種別: {json.dumps(m['changes'], ensure_ascii=False)} / モード: {json.dumps(m['mode'])} / B の由来: {json.dumps(m['route'])} / 最終ターンの status: {json.dumps(m['status_of_final_turn'])}")
    A("")
    A(f"A -> B で変わった設計のトップレベルキー数の分布: {json.dumps(m['changed_design_fields_hist'])}（1 項目だけの書き換え {m['single_field_edits']} 件）。連動タグ: {json.dumps(m['coupling_tags'], ensure_ascii=False)}")
    A("")
    A("### repair")
    A("")
    r_ = st["repair"]
    A(f"違反の種類（エピソード数）: {json.dumps(r_['violation_kinds'], ensure_ascii=False)}")
    A("")
    A(f"validator の違反コード: {json.dumps(r_['violation_codes'], ensure_ascii=False)}")
    A("")
    A(f"1 エピソードの違反の種類数: {json.dumps(r_['kinds_per_episode'])} / 違反コード数: {json.dumps(r_['violations_per_episode'])} / 修正方法: {json.dumps(r_['repair_method'])} / ラウンド: {json.dumps(r_['rounds'])} / 最終 status: {json.dumps(r_['final_status'])}")
    A("")
    A("### infeasible / ood / context")
    A("")
    i_ = st["infeasible"]
    A(f"infeasible 家族: {json.dumps(i_['families'], ensure_ascii=False)} / status: {json.dumps(i_['status'])}")
    A("")
    A(f"家族別 status: {json.dumps(i_['status_by_family'], ensure_ascii=False)}")
    A("")
    o_ = st["ood"]
    A(f"ood: 前提 {json.dumps(o_['premise_type'])} / 技術 {json.dumps(o_['tech'])} / ドクトリン {json.dumps(o_['doctrine'])} / status {json.dumps(o_['status'])}")
    A("")
    c_ = st["context"]
    A(f"context: 変種 {json.dumps(c_['variants'])} / ターン数 {json.dumps(c_['turns'])} / 参考例あり {c_['with_reference']} / 最終 status {json.dumps(c_['final_status'])} / 参考例の衝突数の分布 {json.dumps(c_['reference_conflicts_hist'])}")
    A("")
    A("## 7. トークン長・学習時間・全量の見積もり")
    A("")
    A(f"tokenizer: {tokens['tokenizer']}。system プロンプト {tokens.get('system_prompt_tokens', 0)} トークン（初版 {K.SYSTEM_V1_PREVIOUS_TOKENS} トークンから短縮。全エピソードに 1 回ずつ入る）。")
    A("")
    A(md_table(["category", "件数", "平均", "p50", "p95", "最大", "assistant 平均（全ターン）", "損失対象 平均"],
               [[c, v["episodes"], v["tokens_avg"], v["p50"], v["p95"], v["max"], v["assistant_tokens_avg"], v["trained_tokens_avg"]] for c, v in ((c, tokens["by_category"][c]) for c in cats if c in tokens["by_category"])]))
    A("")
    A(md_table(["split", "件数", "トークン総量", "平均", "p95", "最大", "損失対象トークン"], [[s, v["episodes"], f"{v['tokens_total']:,}", v["tokens_avg"], v["p95"], v["max"], f"{v['trained_tokens_total']:,}"] for s, v in tokens["by_split"].items()]))
    A("")
    if est:
        A(f"**全量（{est['n_full']:,} エピソード、目標比率）の見積もり**: トークン総量 約 {est['tokens_total'] / 1e6:.0f}M（train 80% = {est['train_tokens_total(80%)'] / 1e6:.0f}M、うち損失対象の assistant トークン "
          f"{est['trained_assistant_tokens_total'] / 1e6:.1f}M）。RTX 3060・約 {est['assumed_tok_per_sec']:.0f} tok/s・勾配チェックポイントありで **train 1 epoch ≈ {est['epoch_hours_train']} 時間**"
          f"（全 split でも {est['epoch_hours_all_splits']} 時間）。ディスク ≈ {est['disk_gb_est']} GB（パイロットの平均 {est['bytes_per_episode_pilot']:,} バイト / エピソード、JSONL・system プロンプト込み）。")
        A("")
        A(md_table(["category", "全量の件数", "トークン総量"], [[c, f"{est['episodes_by_category'][c]:,}", f"{est['tokens_total_by_category'].get(c, 0) / 1e6:.1f}M"] for c in cats]))
    A("")
    A("## 8. 生成・検査の所要時間")
    A("")
    A(f"このデータ: {json.dumps(timing, ensure_ascii=False)}")
    trial_path = d / "scale_trial.json"
    if trial_path.exists():
        tr = json.loads(trial_path.read_text(encoding="utf-8"))
        A("")
        A("全量生成の見積もりのために、パイロットと同じパイプラインを大きい件数で試した結果（出力は scratch に作り、リポジトリには残していない）:")
        A("")
        A(md_table(["試行", "件数", "生成時間", "検査時間", "品質 / 重複", "備考"], [[t["name"], f"{t['episodes']:,}", t["generate"], t["check"], t["result"], t["note"]] for t in tr["trials"]]))
        A("")
        A(tr.get("extrapolation", ""))
    A("")
    A("## 9. 再現コマンド")
    A("")
    A("```bash")
    A("cd ~/dev/apps/autoresearch-naval")
    A(f".venv/bin/python tools/shipknow_build_design_v1.py --out data/navspec/{name} --n-total {manifest['n_total_target']} --procs 8")
    A(f".venv/bin/python tools/shipknow_check_design_v1.py --dir data/navspec/{name}")
    A(".venv/bin/python -m pytest tests -q")
    A("```")
    A("")
    A("## 10. 既知の限界")
    A("")
    for t in LIMITS:
        A(f"- {t}")
    A("")
    A("サンプル（人間可読）: " + ", ".join(f"`samples/{c}.md`（{v} 件）" for c, v in samples.items()))
    (d / "README.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def RP_MOD() -> int:
    import shipknow_design_v1_repair as RP
    return RP.HELDOUT_KIND_PAIR_MOD


def DD_YMOD() -> int:
    import shipknow_design_v1_data as DD
    return DD.HELDOUT_YEAR_PAIR_MOD


def DD_MOD() -> int:
    import shipknow_design_v1_data as DD
    return DD.HELDOUT_COUNTRY_PAIR_MOD


LIMITS = [
    "要求は元艦から逆算した合成で、実際の依頼より整っている（必ず元艦で満たせる）。要求文は 6 通りの文型だけ。",
    "validator は物理の「あり得なさ」の歯止めで、設計の正しさの保証ではない（design-v0 レポートの 3.4% の誤検出と、中程度の改変の検出率が半分前後という限界は残る）。"
    "repair の違反は検出されるまで強めているので、実際のモデルの誤りより大きめの違反に偏る。",
    "modify の scaled は層の回帰（実艦の統計）で寸法・出力・乗員を決める近似で、「物理的に正しい」とは限らない（validator と要求充足は通している）。",
    "relaxed / infeasible の「不可能」は train の実艦の有無で定義している（データに無い設計は不可能扱い）。gap の中間帯の例は捨てているので、境界付近の判断は教えていない。",
    "近傍（nbr）は前提・変更後の要求を知らない（ood の tech_early では近傍は史実の年代の艦）。modify の 2 ターン目以降は国・年の変更時だけ近傍を付ける。",
    "架空国家・前提は 5 種類の型に限られ、前提文は定型。doctrine は構造化要求の制約として明示している。",
]


# --------------------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--tokenizer", type=Path, default=K.DEFAULT_TOKENIZER)
    ap.add_argument("--n-full", type=int, default=100000, help="size of the full run for the token / disk / training-time estimate")
    ap.add_argument("--samples", type=int, default=100, help="samples per category")
    ap.add_argument("--tok-per-sec", type=float, default=2000.0)
    ap.add_argument("--no-estimate", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    d = args.dir
    X_ = Ctx(d)
    rows = load_rows(d)
    print(f"loaded {sum(len(v) for v in rows.values())} episodes in {time.time() - t0:.1f}s", flush=True)
    quality, infos = quality_check(rows, X_)
    print(f"quality: {quality['episodes_with_problems']} episodes with problems ({time.time() - t0:.1f}s)", flush=True)
    (d / "quality_check.json").write_text(json.dumps(quality, ensure_ascii=False, indent=1), encoding="utf-8")
    overlap = overlap_check(rows, X_)
    (d / "overlap_check.json").write_text(json.dumps(overlap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"overlap ok={overlap['ok']} ({time.time() - t0:.1f}s)", flush=True)
    st = stats(rows, infos, X_, quality, overlap)
    tokens = token_stats(rows, args.tokenizer if args.tokenizer and Path(args.tokenizer).exists() else None)
    sizes = {s: (d / f"{s}.jsonl").stat().st_size for s in K.SPLITS}
    est = None if args.no_estimate else full_estimate(tokens, args.n_full, sizes, st["episodes_total"], args.tok_per_sec)
    st["tokens"] = tokens
    st["file_bytes"] = sizes
    st["full_estimate"] = est
    st["generation"] = {k: X_.manifest.get(k) for k in ("elapsed_sec", "procs", "waves", "oversample", "dropped", "gold_rejected_by_validator")}
    (d / "stats.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    samples = write_samples(d, rows, args.samples)
    timing = {"generation_sec": X_.manifest.get("elapsed_sec"), "procs": X_.manifest.get("procs"), "check_sec": round(time.time() - t0, 1), "dropped_at_generation": X_.manifest.get("dropped")}
    write_readme(d, X_.manifest, st, quality, overlap, tokens, est, samples, timing)
    print(json.dumps({"quality_ok": quality["ok"], "overlap_ok": overlap["ok"], "episodes": st["episodes_total"], "problems": quality["problem_counts"], "elapsed_sec": round(time.time() - t0, 1)}, ensure_ascii=False))
    sys.exit(0 if (quality["ok"] and overlap["ok"]) else 1)


if __name__ == "__main__":
    main()
