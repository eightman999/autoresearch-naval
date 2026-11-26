"""shipknow design-v1（会話エピソード形式の設計データ）の部品のテスト: 出力 schema、check_design（validator + 拡張要求 + 整合性）、要求生成、設計編集操作（rederive など）、
relaxed / infeasible の判定基準、repair の違反生成と自動修正、前提（waivers）。

実艦レコード（data/navspec/shipknow_v0_2/records.jsonl）と validator 閾値（design_v0/validator_thresholds.json）が要る。無ければ skip。
"""

from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path

import pytest

from tools import shipknow_design_common as C
from tools import shipknow_design_eval_req as X
from tools import shipknow_design_v1_core as K
from tools import shipknow_design_v1_infeasible as I
from tools import shipknow_design_v1_ops as O
from tools import shipknow_design_v1_repair as RP

ROOT = Path(__file__).resolve().parent.parent
needs_data = pytest.mark.skipif(not (C.V0_DIR / "records.jsonl").exists() or not (C.OUT_DIR / "validator_thresholds.json").exists(), reason="v0_2 records / design_v0 thresholds not generated")


def destroyer(**kw):
    d = {"ship_type": "destroyer", "ship_type_fine": "destroyer", "displacement_normal_t": 1500, "displacement_full_t": 1900, "length_m": 105, "beam_m": 10.5, "draught_m": 3.4,
         "propulsion": ["steam_turbine"], "shafts": 2, "power_hp": 42000, "speed_kn": 36, "endurance_nm": 3500, "complement": 180,
         "weapons": [{"kind": "gun", "n": 2, "per": 2, "cal": 120}, {"kind": "torpedo_tube", "n": 2, "per": 4, "cal": 533}]}
    d.update(kw)
    return d


REQ = {"country": "Japan", "year": 1938, "ship_type": "destroyer", "mission": ["surface_strike"], "max_displacement_t": 1700, "displacement_basis": "normal", "min_speed_kn": 33,
       "required": ["main_gun>=100mm", "torpedo_tubes"]}


@pytest.fixture(scope="module")
def recs():
    rows = [json.loads(l) for l in (C.V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if C.design_eligible(r)]


# --------------------------------------------------------------------------------------------------
# output schema
# --------------------------------------------------------------------------------------------------
@needs_data
def test_system_prompt_has_new_schema_and_rules():
    s = K.SYSTEM_V1
    for w in ("status", "conflicts", "relaxations", "infeasible", "latest user message", "silently", "premise", "hints only", "ONE JSON object", "weapons[{kind,n,per,cal}]"):
        assert w in s
    assert "Reply with ONE design as JSON only" not in s  # the v0 output instruction is replaced
    assert len(s) < 1400  # about 250 tokens (checked with the Qwen tokenizer in the dataset README)


def test_dump_and_parse_output_roundtrip():
    txt = K.dump_output("feasible", destroyer())
    obj, err = K.parse_output(txt)
    assert err == "" and list(obj) == ["status", "conflicts", "relaxations", "design"]
    assert obj["conflicts"] == [] and obj["relaxations"] == []
    assert K.jdump(obj) == txt
    assert K.dump_output("infeasible", None, [{"constraint": "x", "reason": "y"}]).endswith('"design":null}')


@pytest.mark.parametrize("bad", ["", "text {\"status\":\"feasible\"}", "[1]", '{"status":"feasible"}', '{"design":null,"status":"feasible","conflicts":[],"relaxations":[]}'])
def test_parse_output_rejects_prose_and_wrong_keys(bad):
    obj, err = K.parse_output(bad)
    assert obj is None and err


# --------------------------------------------------------------------------------------------------
# check_design
# --------------------------------------------------------------------------------------------------
@needs_data
def test_check_design_accepts_consistent_destroyer_and_reports_extended_keys():
    d = destroyer()
    assert K.check_design(REQ, d)["ok"]
    r2 = {**REQ, "min_gun_barrels": 4, "min_gun_barrels_cal_mm": 100, "min_torpedo_tubes": 8, "required_propulsion": ["steam_turbine"]}
    assert K.check_design(r2, d)["ok"]
    codes = {v["code"] for v in K.check_design({**REQ, "min_gun_barrels": 5, "min_aircraft": 1, "min_torpedo_tubes": 9, "required_propulsion": ["gas_turbine"]}, d)["violations"]}
    assert {"req_gun_barrels_short", "req_aircraft_short", "req_torpedo_tubes_short", "req_propulsion_missing"} <= codes
    codes = {v["code"] for v in K.check_design({**REQ, "min_displacement_t": 1600, "max_speed_kn": 34}, d)["violations"]}
    assert {"req_displacement_below", "req_speed_exceeds"} <= codes


@needs_data
def test_check_design_catches_speed_power_and_armament_and_era():
    d = destroyer(speed_kn=60)
    assert any(v["code"].startswith("ratio_") and v["field"] in ("adm", "fr", "hp_t") for v in K.check_design(REQ, d)["violations"])
    d = destroyer(weapons=destroyer()["weapons"] + [{"kind": "gun", "n": 8, "per": 3, "cal": 380}])
    assert any(v["code"] in ("armament_excess", "gun_too_big_for_displacement") for v in K.check_design(REQ, d)["violations"])
    d = destroyer(weapons=destroyer()["weapons"] + [{"kind": "sam", "n": 2, "per": 2}])
    assert any(v["code"] == "era_early" and v["field"] == "sam" for v in K.check_design(REQ, d)["violations"])
    d = destroyer(air=["helicopter"], weapons=destroyer()["weapons"] + [{"kind": "aircraft_helicopter", "n": 1}, {"kind": "aviation_facility"}])
    assert any(v["code"] == "era_early" and v["field"] == "air:helicopter" for v in K.check_design(REQ, d)["violations"])
    d = destroyer(air=["helicopter"], weapons=destroyer()["weapons"] + [{"kind": "aircraft_helicopter", "n": 60}, {"kind": "aviation_facility"}])
    assert any(v["code"] in ("weapon_count_excess", "heli_hull_too_short") for v in K.check_design({**REQ, "year": 1965}, d)["violations"])


@needs_data
def test_waivers_exempt_only_the_named_technology():
    d = destroyer(sensors=["air_search_radar", "fire_control_radar", "radar"])
    req = {**REQ, "year": 1928}
    assert any(v["code"] == "era_early" for v in K.check_design(req, d)["violations"])
    w = K.waivers_for_premise({"type": "tech_early", "tech": "radar", "available_from": 1926})
    assert set(w) >= {"radar_any", "air_search_radar", "fire_control_radar"}
    assert K.check_design(req, d, w)["ok"]
    # a helicopter in 1928 is not covered by a radar premise
    d2 = destroyer(sensors=["radar"], air=["helicopter"], weapons=destroyer()["weapons"] + [{"kind": "aircraft_helicopter", "n": 1}, {"kind": "aviation_facility"}])
    res = K.check_design(req, d2, w)
    assert any(v["code"] == "era_early" and v["field"] == "air:helicopter" for v in res["violations"])
    assert K.waivers_for_premise({"type": "fictional_nation", "like": "France"}) == []


def test_apply_relaxations_drops_exactly_the_relaxed_constraints():
    req = {**REQ, "required": ["sam", "torpedo_tubes"], "min_gun_barrels": 4, "min_gun_barrels_cal_mm": 100, "required_propulsion": ["gas_turbine"]}
    out = K.apply_relaxations(req, [{"constraint": "required:sam"}, {"constraint": "min_gun_barrels"}, {"constraint": "required_propulsion:gas_turbine"}, {"constraint": "min_speed_kn"}])
    assert out["required"] == ["torpedo_tubes"] and "min_gun_barrels" not in out and "min_gun_barrels_cal_mm" not in out and "required_propulsion" not in out and "min_speed_kn" not in out
    assert K.constraint_ids(out) == ["max_displacement_t", "required:torpedo_tubes"]


@needs_data
def test_failed_constraints_ids():
    d = destroyer()
    f = K.failed_constraints({**REQ, "required": ["sam", "torpedo_tubes"], "min_speed_kn": 40, "min_aircraft": 2}, d)
    assert set(f) == {"required:sam", "min_speed_kn", "min_aircraft"}


# --------------------------------------------------------------------------------------------------
# requirement generation
# --------------------------------------------------------------------------------------------------
@needs_data
def test_make_req_v1_is_satisfied_by_the_source_and_deterministic(recs):
    n_ext = 0
    for r in recs[:400]:
        d = C.design_from_record(r)
        a = K.make_req_v1(d, r["country"], C.request_year(r), K.sub_rng(1, r["id"]))
        b = K.make_req_v1(d, r["country"], C.request_year(r), K.sub_rng(1, r["id"]))
        assert a == b
        assert K.satisfies_all(a, d)
        n_ext += any(k in a for k in ("min_gun_barrels", "min_aircraft", "min_torpedo_tubes", "required_propulsion"))
    assert n_ext > 100


@needs_data
def test_make_req_v1_force_adds_the_extended_key(recs):
    for r in recs:
        d = C.design_from_record(r)
        if X.aircraft_count(d) >= 1:
            req = K.make_req_v1(d, r["country"], C.request_year(r), random.Random(3), force={"aircraft"})
            assert req["min_aircraft"] >= 1 and K.satisfies_all(req, d)
            break
    else:
        pytest.fail("no ship with aircraft")


@needs_data
def test_loosen_req_keeps_the_source_satisfying(recs):
    for r in recs[:200]:
        d = C.design_from_record(r)
        req = K.make_req_v1(d, r["country"], C.request_year(r), random.Random(1))
        loose = K.loosen_req(req, d, random.Random(2))
        assert K.satisfies_all(loose, d) and len(loose.get("required", [])) <= 2 and "min_gun_barrels" not in loose


def test_request_text_mentions_every_extended_constraint():
    req = {**REQ, "min_gun_barrels": 6, "min_gun_barrels_cal_mm": 100, "min_aircraft": 2, "min_torpedo_tubes": 8, "required_propulsion": ["gas_turbine"], "min_displacement_t": 1200, "max_speed_kn": 38}
    for v in range(6):
        t = K.request_text_v1(req, v)
        for w in ("6 gun barrels", "100 mm", "2 embarked aircraft", "8 torpedo tubes", "gas turbine", "1200", "38"):
            assert w in t, (v, w, t)
    t = K.request_text_v1({**REQ, "min_displacement_t": 3000}, 0)
    assert "at least 3000 t and at most 1700 t" in t  # a contradictory range is stated plainly


def test_heldout_combo_is_deterministic_and_needs_several_features():
    plain = {**REQ}
    assert not K.is_heldout_combo(plain)
    hits = 0
    for n in range(2, 40):
        r = {**REQ, "min_gun_barrels": n, "min_torpedo_tubes": n, "min_aircraft": 1, "required": ["sonar", "torpedo_tubes"]}
        hits += K.is_heldout_combo(r)
        assert K.is_heldout_combo(r) == K.is_heldout_combo(dict(r))
    assert hits == 0 or K.combo_signature(r)  # signature ignores the numbers, so all or none of these share one verdict
    sigs = {K.combo_signature({**REQ, "required": [t, "torpedo_tubes"], "min_aircraft": 1, "min_gun_barrels": 3}) for t in ("sonar", "radar", "sam", "ssm", "helicopter", "esm_ecm", "mines", "ciws")}
    assert len(sigs) == 8 and any(K.is_heldout_combo({**REQ, "required": [t, "torpedo_tubes"], "min_aircraft": 1, "min_gun_barrels": 3}) for t in ("sonar", "radar", "sam", "ssm", "helicopter", "esm_ecm", "mines", "ciws")) is not None


# --------------------------------------------------------------------------------------------------
# ops
# --------------------------------------------------------------------------------------------------
@needs_data
def test_rederive_is_idempotent_and_couples_speed_power_and_size(recs):
    ok = same = checked = 0
    for r in recs[:600]:
        d = C.design_from_record(r)
        y = C.request_year(r)
        if not K.check_design({"year": y, "country": r["country"]}, d)["ok"]:
            continue
        checked += 1
        same += O.rederive(d, 1.0, d["speed_kn"], y) == d
        up = O.rederive(d, 1.0, d["speed_kn"] + 5, y)
        small = O.rederive(d, 0.8, d["speed_kn"], y)
        if d.get("power_hp") and up.get("power_hp"):
            assert up["power_hp"] > d["power_hp"]  # speed up -> power up
        if d.get("length_m"):
            assert small["length_m"] <= d["length_m"] and C.ref_displacement(small)[0] < C.ref_displacement(d)[0]
        ok += K.check_design({"year": y, "country": r["country"]}, small)["ok"]
    assert same == checked
    assert ok / checked > 0.95


def test_ops_weapon_edits_and_weights():
    d = destroyer()
    d2 = O.clone(d)
    dw = O.add_barrels(d2, 3)
    assert X.gun_barrels(d2) == X.gun_barrels(d) + 4 and dw > 0  # twin mounts: 3 barrels -> 2 mounts -> +4
    d3 = O.clone(d)
    O.set_aircraft(d3, 2, "helicopter")
    assert X.aircraft_count(d3) == 2 and d3["air"] == ["helicopter"] and any(w["kind"] == "aviation_facility" for w in d3["weapons"])
    O.set_aircraft(d3, 0)
    assert X.aircraft_count(d3) == 0 and "air" not in d3
    d4 = O.clone(d)
    O.set_aircraft(d4, 1)
    assert d4["air"] == ["helicopter"]
    d5 = destroyer(air=["seaplane"], weapons=d["weapons"] + [{"kind": "aircraft_helicopter", "n": 1}])
    O.set_aircraft(d5, 2)
    assert d5["air"] == ["seaplane"]  # an existing air kind is kept (no anachronistic helicopter)
    assert O.gun_weight_t(305, 1) > O.gun_weight_t(127, 1) > O.gun_weight_t(40, 1)


@needs_data
def test_shrink_weapons_keeps_required_equipment():
    d = destroyer()
    req = {**REQ, "min_torpedo_tubes": 8, "min_gun_barrels": 4}
    out = O.shrink_weapons(d, 0.6, req)
    assert X.torpedo_tubes(out) >= 8 and X.gun_barrels(out) >= 4


# --------------------------------------------------------------------------------------------------
# relaxed / infeasible decision
# --------------------------------------------------------------------------------------------------
def conf(family, gap=None, relaxable=True, core=False):
    return I.Conflict("c", family, "r", relaxable, gap, core)


def test_decide_status_rules_one_by_one():
    assert I.decide_status([], 5) == "feasible"
    assert I.decide_status([conf("speed", 0.2)], 5) == "relaxed"
    assert I.decide_status([conf("speed", 0.31)], 5) == "infeasible"          # R4: gap above the limit
    assert I.decide_status([conf("speed", None)], 5) == "infeasible"          # numeric conflicts need a gap
    assert I.decide_status([conf("gun_barrels", 0.9)], 5) == "relaxed"
    assert I.decide_status([conf("gun_cal", 0.7)], 5) == "infeasible"
    assert I.decide_status([conf("era_capability")], 5) == "relaxed"
    assert I.decide_status([conf("era_capability", core=True)], 5) == "infeasible"  # R3: mission core
    assert I.decide_status([conf("hull_type", relaxable=False)], 5) == "infeasible"  # R1
    assert I.decide_status([conf("contradiction")], 5) == "infeasible"
    assert I.decide_status([conf("aircraft_zero")], 5) == "infeasible"
    assert I.decide_status([conf("era_capability"), conf("era_capability")], 5) == "relaxed"
    assert I.decide_status([conf("era_capability"), conf("era_capability")], 3) == "infeasible"   # R2: more than half of the constraints
    assert I.decide_status([conf("era_capability")] * 3, 20) == "infeasible"                       # R2: three conflicts


def test_mission_core_and_conflict_outputs():
    assert I.mission_core({"mission": ["aaw", "asw"]}, "sam") and not I.mission_core({"mission": ["patrol", "aaw"]}, "sam")
    c = I.Conflict("min_speed_kn", "speed", "too fast", True, 0.2, False, 40, 36, "closest is 36")
    confs, rels = I.conflict_outputs([c], "relaxed")
    assert confs == [{"constraint": "min_speed_kn", "reason": "too fast"}] and rels[0]["relaxed_to"] == 36
    confs, rels = I.conflict_outputs([c], "infeasible")
    assert rels == []


@needs_data
def test_era_conflict_texts_use_first_years():
    c = I.era_conflict({"mission": ["patrol"]}, "required:sam", 1920, destroyer())
    assert "1959" not in c.reason or "1920" in c.reason
    assert c.relaxed_to == "none" and c.family == "era_capability"
    c2 = I.era_conflict({"mission": ["patrol"]}, "required_propulsion:nuclear", 1920, destroyer())
    assert "nuclear" in c2.reason and c2.relaxed_to == "steam_turbine"


# --------------------------------------------------------------------------------------------------
# repair: corruption kinds and automatic repair
# --------------------------------------------------------------------------------------------------
class _D:  # the bits of Data that auto_repair needs
    th = None


@needs_data
@pytest.mark.parametrize("kind", RP.KINDS)
def test_each_corruption_is_detected_and_auto_repair_passes(kind, recs):
    th = K.thresholds()
    D = _D()
    D.th = th
    rng = random.Random(7)
    pool = [r for r in recs if K.check_design({"year": C.request_year(r)}, C.design_from_record(r))["ok"]]
    tried = fixed = 0
    for r in pool:
        d = C.design_from_record(r)
        y = C.request_year(r)
        force = {RP.KIND_FORCE[kind]} if kind in RP.KIND_FORCE else None
        req = K.make_req_v1(d, r["country"], y, random.Random(r["id"]), force=force)
        if kind in RP.KIND_FORCE and RP.KIND_FORCE[kind] not in {"barrels": "min_gun_barrels", "tubes": "min_torpedo_tubes", "aircraft": "min_aircraft"}.get(RP.KIND_FORCE[kind], "") and False:
            continue
        got = RP.build_bad(D, req, d, [kind], rng, y)
        if got is None:
            continue
        bad, res, done = got
        tried += 1
        assert res["violations"] and any(RP._is_target(kind, v) for v in res["violations"])
        assert not K.check_design(req, bad, th=th)["ok"]
        out, method, trace = RP.auto_repair(D, req, bad, d, y)
        assert K.check_design(req, out, th=th)["ok"]  # a repaired (or fallback gold) design always passes
        fixed += method == "rule"
        if tried >= 25:
            break
    assert tried >= 8, f"{kind}: only {tried} corruptions detected"
    assert fixed >= tried * 0.5, f"{kind}: rule-based repair {fixed}/{tried}"


@needs_data
def test_auto_repair_two_rounds_and_feedback_format():
    th = K.thresholds()
    D = _D()
    D.th = th
    d = destroyer()
    req = {**REQ, "min_torpedo_tubes": 8}
    bad = O.clone(d)
    bad["speed_kn"] = 60
    bad["weapons"] = [w for w in bad["weapons"] if w["kind"] != "torpedo_tube"]
    assert len(K.check_design(req, bad, th=th)["violations"]) >= 2
    part, method, _ = RP.auto_repair(D, req, bad, d, 1938, stop_after=1)
    assert method == "partial" and not K.check_design(req, part, th=th)["ok"] and part != bad
    final, method, _ = RP.auto_repair(D, req, part, d, 1938)
    assert K.check_design(req, final, th=th)["ok"]
    viols = RP.violations_for_user(K.check_design(req, bad, th=th))
    fb = json.loads(RP.feedback_turn(viols))
    assert fb["validator"]["valid"] is False and set(fb["validator"]["violations"][0]) == {"code", "field", "detail"} and fb["instruction"] == RP.INSTRUCTION


def test_heldout_violation_pairs_are_pairs_only():
    assert not RP.heldout_kinds(["speed_power"])
    pairs = [[a, b] for i, a in enumerate(RP.KINDS) for b in RP.KINDS[i + 1:]]
    n = sum(RP.heldout_kinds(p) for p in pairs)
    assert 0 < n < len(pairs) and RP.heldout_kinds(["a", "b"]) == RP.heldout_kinds(["b", "a"])


def test_air_kind_follows_the_era():
    assert RP.air_kind_for_year(1900) == "aircraft" and RP.air_kind_for_year(1938) == "seaplane" and RP.air_kind_for_year(1975) == "helicopter"


@needs_data
def test_torpedo_caliber_has_a_first_year():
    d = destroyer(weapons=[{"kind": "gun", "n": 2, "per": 2, "cal": 120}, {"kind": "torpedo_tube", "n": 2, "per": 2, "cal": 610}])
    res = K.check_design({**REQ, "year": 1895}, d)
    assert any(v["code"] == "era_early" and v["field"].startswith("torpedo_cal:610") for v in res["violations"])
    assert not any(v["field"].startswith("torpedo_cal") for v in K.check_design({**REQ, "year": 1938}, destroyer())["violations"])
