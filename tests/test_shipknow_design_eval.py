"""shipknow 設計評価 v1（shipknow_design_eval_v1）のテスト: 拡張要求の判定、整合性・plausibility・丸写し・多様性・E・ペアの各判定の代表ケース、
モデル比較表、推論スクリプトの補助関数、（生成済みなら）評価データの構造。

採点器の参照分布は data/navspec/shipknow_v0_2/{records,splits} の train 実艦から作る（NSDB は不要）。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import shipknow_build_design_eval as BE
from tools import shipknow_design_common as C
from tools import shipknow_design_eval as DE
from tools import shipknow_design_eval_cases as K
from tools import shipknow_design_eval_req as X
from tools import shipknow_design_validator as V
from tools import shipknow_types as T
from tools import shipknow_infer_hf as INF

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "data" / "navspec" / "shipknow_design_eval_v1"
needs_records = pytest.mark.skipif(not (C.V0_DIR / "records.jsonl").exists(), reason="shipknow_v0_2 records not generated")


@pytest.fixture(scope="module")
def ref():
    return DE.default_ref()


@pytest.fixture(scope="module")
def th():
    return V.load_thresholds()


def destroyer(**kw):
    d = {"ship_type": "destroyer", "ship_type_fine": "destroyer", "displacement_normal_t": 1500, "displacement_full_t": 1900, "length_m": 105, "beam_m": 10.5, "draught_m": 3.4,
         "propulsion": ["steam_turbine"], "shafts": 2, "power_hp": 42000, "speed_kn": 36, "endurance_nm": 3500, "complement": 180,
         "weapons": [{"kind": "gun", "n": 2, "per": 2, "cal": 120}, {"kind": "torpedo_tube", "n": 2, "per": 4, "cal": 533}]}
    d.update(kw)
    return d


def prob(req, cat="A", nbr=None, meta=None, pid="X01"):
    return {"id": pid, "category": cat, "req": req, "nbr": nbr or [], "meta": meta or {}}


REQ = {"country": "Japan", "year": 1938, "ship_type": "destroyer", "mission": ["surface_strike"], "max_displacement_t": 1700, "displacement_basis": "normal", "min_speed_kn": 33,
       "required": ["main_gun>=100mm", "torpedo_tubes"]}


# --------------------------------------------------------------------------------------------------
# extended requirement checks
# --------------------------------------------------------------------------------------------------
def test_req_checks_each_constraint_type():
    req = {"country": "UK", "year": 1970, "ship_type": "frigate", "ship_type_fine": "missile-frigate", "max_displacement_t": 3000, "displacement_basis": "full", "min_speed_kn": 28,
           "max_complement": 200, "min_endurance_nm": 4000, "required": ["main_gun>=100mm", "sam", "helicopter", "sonar", "air_search_radar"], "min_gun_barrels": 2,
           "min_gun_barrels_cal_mm": 100, "min_aircraft": 2, "min_torpedo_tubes": 6, "required_propulsion": ["gas_turbine"]}
    d = {"ship_type": "frigate", "ship_type_fine": "missile-frigate", "displacement_full_t": 2900, "speed_kn": 29, "complement": 190, "endurance_nm": 4500,
         "weapons": [{"kind": "gun", "n": 1, "per": 2, "cal": 114}, {"kind": "sam", "n": 1}, {"kind": "aircraft_helicopter", "n": 2}, {"kind": "torpedo_tube", "n": 2, "per": 3, "cal": 324}],
         "sensors": ["sonar", "air_search_radar"], "air": ["helicopter"], "propulsion": ["gas_turbine", "diesel"]}
    got = {c["key"]: c["ok"] for c in X.req_checks(req, d)}
    assert all(v is True for v in got.values()), got
    assert {c["type"] for c in X.req_checks(req, d)} >= {"displacement", "speed", "complement", "endurance", "main_gun", "sensor", "weapon", "aircraft_fit", "gun_barrels",
                                                          "aircraft_count", "torpedo_tubes", "propulsion", "ship_type", "ship_type_fine"}
    # one by one violations
    bad = copy.deepcopy(d)
    bad.update({"displacement_full_t": 3100, "speed_kn": 27, "complement": 210, "endurance_nm": 3000, "propulsion": ["diesel"]})
    bad["weapons"] = [{"kind": "gun", "n": 1, "cal": 114}, {"kind": "aircraft_helicopter", "n": 1}, {"kind": "torpedo_tube", "n": 1, "per": 3}]
    bad["sensors"] = []
    got = {c["key"]: c["ok"] for c in X.req_checks(req, bad)}
    assert got["max_displacement_t(full)"] is False and got["min_speed_kn"] is False and got["max_complement"] is False and got["min_endurance_nm"] is False
    assert got["min_gun_barrels"] is False and got["min_aircraft"] is False and got["min_torpedo_tubes"] is False and got["required_propulsion:gas_turbine"] is False
    assert got["required:sam"] is False and got["required:sonar"] is False and got["required:air_search_radar"] is False and got["required:main_gun>=100mm"] is True


def test_req_checks_missing_numeric_is_undeterminable_missing_list_is_unsatisfied():
    req = {"ship_type": "patrol", "max_displacement_t": 100, "displacement_basis": "normal", "min_speed_kn": 20, "max_complement": 30, "min_endurance_nm": 500, "required": ["sonar"],
           "required_propulsion": ["diesel"]}
    got = {c["key"]: c["ok"] for c in X.req_checks(req, {"ship_type": "patrol"})}
    assert got["max_displacement_t(normal)"] is None and got["min_speed_kn"] is None and got["max_complement"] is None and got["min_endurance_nm"] is None
    assert got["required:sonar"] is False and got["required_propulsion:diesel"] is None and got["ship_type"] is True
    # the basis matters: a full displacement does not answer a normal-basis limit
    assert X.req_checks(req, {"ship_type": "patrol", "displacement_full_t": 50})[1]["ok"] is None


def test_gun_barrels_counts_n_times_per_and_threshold():
    d = {"weapons": [{"kind": "gun", "n": 2, "per": 3, "cal": 203}, {"kind": "gun", "n": 4, "cal": 76}, {"kind": "sam", "n": 8}]}
    assert X.gun_barrels(d) == 10 and X.gun_barrels(d, 100) == 6 and X.gun_barrels(d, 300) == 0
    assert X.aircraft_count({"weapons": [{"kind": "aviation_facility"}]}) == 0  # a facility without a count carries no counted aircraft
    assert X.aircraft_count({"weapons": [{"kind": "aircraft_helicopter", "n": 3}, {"kind": "aviation_facility", "n": 1}]}) == 3


def test_constraint_units_and_request_text_extension():
    req = {"country": "Japan", "year": 1990, "ship_type": "destroyer", "mission": ["aaw"], "max_displacement_t": 5000, "displacement_basis": "full", "min_speed_kn": 30,
           "required": ["sam", "main_gun>=127mm"], "min_gun_barrels": 4, "min_gun_barrels_cal_mm": 127, "min_aircraft": 2, "required_propulsion": ["gas_turbine"]}
    assert len(X.constraint_units(req)) == 7
    for v in range(3):
        t = X.request_text_ext(req, v, extra_phrases=["a vertical launching system"])
        assert "at least 4 gun barrels of 127 mm or larger" in t and "at least 2 embarked aircraft" in t and "vertical launching system" in t and "propulsion by gas turbine" in t
    assert "砲4門以上" in X.request_text_ja_ext(req) and "搭載機2機以上" in X.request_text_ja_ext(req)
    # the retrieval view maps the extension keys onto the v0 required vocabulary
    r = X.retrieval_req({"country": "X", "year": 1990, "ship_type": "destroyer", "min_aircraft": 1, "min_torpedo_tubes": 4, "min_gun_barrels": 4, "min_gun_barrels_cal_mm": 130})
    assert {"helicopter", "torpedo_tubes", "main_gun>=127mm"} <= set(r["required"])
    assert "min_aircraft" not in r


def test_extended_keys_do_not_break_v0_validator(th):
    req = {**REQ, "min_gun_barrels": 40, "min_aircraft": 9, "required_propulsion": ["nuclear"]}
    assert V.validate(req, destroyer(), th, layers=("req",))["ok"] is True  # v0 ignores the keys; DE judges them


# --------------------------------------------------------------------------------------------------
# row scoring: format, requirement
# --------------------------------------------------------------------------------------------------
@needs_records
def test_format_invalid_and_missing_fields(ref, th):
    r = DE.score_row(prob(REQ), {"id": "X01", "text": "I cannot design that."}, ref, th)
    assert r["json_ok"] is False and r["marker"] is True  # a refusal is not a design but is recognised as one
    r = DE.score_row(prob(REQ), {"id": "X01", "text": "garbage"}, ref, th)
    assert r["json_ok"] is False and r["marker"] is False and r["req_all_sat"] is False
    r = DE.score_row(prob(REQ), None, ref, th)
    assert r["json_ok"] is False and r["design_present"] is False
    d = destroyer()
    del d["length_m"], d["weapons"]
    r = DE.score_row(prob(REQ), {"id": "X01", "output": d}, ref, th)
    assert r["json_ok"] and not r["schema_ok"] and r["missing_required"] and {"length_m", "weapons"} <= set(r["missing"])
    # fenced JSON with prose parses
    r = DE.score_row(prob(REQ), {"id": "X01", "text": "```json\n" + json.dumps(destroyer()) + "\n```"}, ref, th)
    assert r["json_ok"] and r["schema_ok"] and r["req_all_sat"]


@needs_records
def test_requirement_counts_by_type(ref, th):
    r = DE.score_row(prob(REQ), {"id": "X01", "output": destroyer(speed_kn=30)}, ref, th)
    assert r["req_total"] == 5 and r["req_sat"] == 4 and r["req_det"] == 5
    assert r["req_by_type"]["speed"] == [1, 1, 0] and r["req_by_type"]["main_gun"] == [1, 1, 1] and not r["req_all_sat"] and "min_speed_kn" in r["req_failed"]
    a = DE.aggregate([r, DE.score_row(prob(REQ), {"id": "X01", "output": destroyer()}, ref, th), DE.score_row(prob(REQ), None, ref, th)])
    assert a["n"] == 3 and a["json_ok_rate"] == round(2 / 3, 4) and a["all_req_satisfied_rate"] == round(1 / 3, 4)
    assert a["req_by_type"]["speed"]["rate_determinable"] == 0.5


# --------------------------------------------------------------------------------------------------
# internal consistency
# --------------------------------------------------------------------------------------------------
@needs_records
def test_consistency_passes_a_normal_destroyer(ref, th):
    hard, _soft = DE.consistency(destroyer(), REQ, ref, th)
    assert hard == []


@needs_records
def test_consistency_armament_excess_and_validator_gun(ref, th):
    d = destroyer(displacement_normal_t=300, displacement_full_t=330, length_m=60, beam_m=7, draught_m=2, power_hp=9000, speed_kn=30, complement=60)
    d["weapons"] = [{"kind": "gun", "n": 3, "per": 3, "cal": 460}]
    hard, _ = DE.consistency(d, REQ, ref, th)
    assert "armament_excess" in hard
    d["weapons"] = [{"kind": "gun", "n": 4, "per": 2, "cal": 203}]
    hard, _ = DE.consistency(d, REQ, ref, th)
    assert "armament_excess" in hard or any(h.startswith("gun_too_big") for h in hard)


@needs_records
def test_consistency_weapon_count_excess(ref, th):
    d = destroyer(displacement_normal_t=60, displacement_full_t=70, length_m=26, beam_m=5, draught_m=1.2, power_hp=3000, speed_kn=30, complement=12)
    d["weapons"] = [{"kind": "gun", "n": 1, "cal": 20}, {"kind": "sam", "n": 96}]
    hard, _ = DE.consistency(d, {**REQ, "ship_type": "patrol"}, ref, th)
    assert "weapon_count_excess:sam" in hard


@needs_records
def test_consistency_type_displacement_and_order_and_shape(ref, th):
    d = destroyer(ship_type="battleship", displacement_normal_t=200, displacement_full_t=250, length_m=45, beam_m=7, draught_m=2.5)
    hard, _ = DE.consistency(d, {**REQ, "year": 1920, "ship_type": "battleship"}, ref, th)
    assert "type_displacement_mismatch" in hard
    hard, _ = DE.consistency(destroyer(displacement_full_t=1300), REQ, ref, th)
    assert "displacement_order" in hard
    hard, _ = DE.consistency(destroyer(beam_m=110), REQ, ref, th)
    assert "beam_ge_length" in hard


@needs_records
def test_consistency_aircraft_rules(ref, th):
    d = destroyer(air=["helicopter"])
    hard, _ = DE.consistency(d, {**REQ, "year": 1975}, ref, th)
    assert "air_without_entry" in hard
    d = destroyer()
    d["weapons"] = d["weapons"] + [{"kind": "aircraft_helicopter", "n": 1}]
    hard, _ = DE.consistency(d, {**REQ, "year": 1975}, ref, th)
    assert "air_entry_without_air" in hard
    d["air"] = ["helicopter"]
    d["length_m"] = 24
    d["displacement_normal_t"], d["displacement_full_t"] = 90, 110
    hard, _ = DE.consistency(d, {**REQ, "year": 1975}, ref, th)
    assert "heli_hull_too_short" in hard
    d = destroyer(air=["helicopter"], weapons=[{"kind": "gun", "n": 1, "cal": 76}, {"kind": "aircraft_helicopter", "n": 1}])
    hard, _ = DE.consistency(d, {**REQ, "year": 1975}, ref, th)
    assert not {"air_without_entry", "air_entry_without_air", "heli_hull_too_short"} & set(hard)


@needs_records
def test_consistency_submarine_and_nuclear_and_era_waiver(ref, th):
    sub = {"ship_type": "submarine", "ship_type_fine": "submarine", "displacement_normal_t": 800, "length_m": 60, "beam_m": 6.5, "draught_m": 4, "speed_kn": 14, "power_hp": 1800,
           "propulsion": ["diesel"], "weapons": [{"kind": "gun", "n": 4, "per": 1, "cal": 127}, {"kind": "torpedo_tube", "n": 4}]}
    hard, _ = DE.consistency(sub, {**REQ, "ship_type": "submarine", "year": 1930}, ref, th)
    assert "sub_many_big_guns" in hard
    nuc = destroyer(propulsion=["nuclear"], displacement_normal_t=400, displacement_full_t=450, length_m=50, beam_m=7, draught_m=2.4, power_hp=9000, speed_kn=28, complement=40)
    hard, _ = DE.consistency(nuc, {**REQ, "year": 1990}, ref, th)
    assert "nuclear_small_hull" in hard
    # SAM + air-search radar in 1938: an era violation, waived when the premise brings the technology forward
    d = destroyer(sensors=["air_search_radar"], weapons=[{"kind": "gun", "n": 2, "cal": 127}, {"kind": "sam", "n": 2}])
    hard, _ = DE.consistency(d, REQ, ref, th)
    assert "era_early:sam" in hard and "era_early:air_search_radar" in hard
    hard, _ = DE.consistency(d, REQ, ref, th, ["sam", "air_search_radar", "radar_any"])
    assert not any(h.startswith("era_early") for h in hard)


@needs_records
def test_consistency_false_positives_on_real_ships_are_low(ref, th):
    # the extra rules (not the validator's) must not reject real held-out ships; the validator itself has about 3 % false positives
    rows = [json.loads(l) for l in (C.OUT_DIR / "test.jsonl").read_text(encoding="utf-8").splitlines()[:400]] if (C.OUT_DIR / "test.jsonl").exists() else []
    if not rows:
        pytest.skip("design_v0 test not generated")
    extra = 0
    for e in rows:
        req = json.loads(e["messages"][1]["content"])["req"]
        gold = json.loads(e["messages"][2]["content"])
        hard, _ = DE.consistency(gold, req, ref, th)
        extra += any(not h.startswith(("ratio_", "gun_", "era_", "type_equipment")) for h in hard)
    assert extra <= 2


# --------------------------------------------------------------------------------------------------
# plausibility and copy
# --------------------------------------------------------------------------------------------------
@needs_records
def test_plausibility_in_range_and_out_of_range(ref):
    req = {"country": "United-Kingdom", "year": 1940, "ship_type": "destroyer", "displacement_basis": "normal"}
    ok = ref.plausibility(destroyer(), req)
    assert ok["level"] in ("country", "region") and ok["fields"]["speed_kn"] is True and ok["all_in_range"] is True
    bad = ref.plausibility(destroyer(displacement_normal_t=30000, length_m=400, speed_kn=70, power_hp=900000, complement=9000, weapons=[{"kind": "gun", "n": 1, "cal": 460}]), req)
    assert bad["all_in_range"] is False and bad["frac_in_range"] == 0.0
    # fields that the design lacks are not judged
    part = ref.plausibility({"ship_type": "destroyer", "speed_kn": 36}, req)
    assert part["fields"]["speed_kn"] is True and part["fields"]["power_hp"] is None and part["fields"]["displacement"] is None


@needs_records
def test_plausibility_group_fallback_to_region_and_world(ref):
    req = {"country": "United-Kingdom", "year": 1940, "ship_type": "destroyer", "displacement_basis": "normal"}
    assert ref.plausibility(destroyer(), req)["level"] == "country"
    assert ref.plausibility(destroyer(), {**req, "country": "Japan", "year": 1938})["level"] in ("region", "region20", "country")
    fict = ref.plausibility(destroyer(), {**req, "country": "Valdoria"})
    assert fict["level"] in ("world", "world20")
    assert DE.region_of("Japan") == "east_asia" and DE.region_of("United-Kingdom") == "west_europe" and DE.region_of("Valdoria") is None
    # every country of the records has a region
    assert all(DE.region_of(r["country"]) for r in ref.all_recs)


@needs_records
def test_copy_detection_real_ship_and_neighbour(ref, th):
    rec = next(r for r in ref.train if r["ship_type"] == "destroyer" and r.get("displacement_normal_t") and r.get("beam_m") and r.get("speed_kn"))
    d = C.design_from_record(rec)
    r = DE.score_row(prob({**REQ, "max_displacement_t": 99999, "min_speed_kn": 1}), {"id": "X01", "output": d}, ref, th)
    assert r["copy_any"] is True and rec["id"] in r["copy_ids"]
    shifted = copy.deepcopy(d)
    shifted["length_m"] = round(d["length_m"] * 1.05, 1)
    assert DE.score_row(prob(REQ), {"id": "X01", "output": shifted}, ref, th)["copy_any"] is False
    nb = C.nbr_payload(rec, None)
    assert DE.nbr_copy(d, [nb]) is True and DE.nbr_copy(shifted, [nb]) is False
    r = DE.score_row(prob(REQ, nbr=[nb]), {"id": "X01", "output": d}, ref, th)
    assert r["copy_nbr"] is True
    a = DE.aggregate([r])
    assert a["copy_any_rate"] == 1.0 and a["copy_nbr_share_of_copy"] == 1.0


# --------------------------------------------------------------------------------------------------
# diversity
# --------------------------------------------------------------------------------------------------
@needs_records
def test_diversity_seed_and_cross_problem(ref, th):
    rows = []
    for pid, spd in (("P1", 30), ("P2", 30)):
        for seed in (1, 2, 3):
            rows.append(DE.score_row(prob(REQ, pid=pid), {"id": pid, "seed": seed, "output": destroyer(speed_kn=spd if seed < 3 else spd + 3)}, ref, th))
    dv = DE.diversity(rows)
    assert dv["n_problems_multi_seed"] == 2 and dv["n_seed_pairs"] == 6 and dv["seed_identical_rate"] == round(2 / 6, 4)  # seed 1 == seed 2 in each problem
    assert dv["cross_problem_identical_rate"] == 1.0  # P1 and P2 give the same outputs
    assert dv["distinct_output_ratio"] == round(2 / 6, 4)
    assert dv["seed_numeric_cv_median"] > 0


# --------------------------------------------------------------------------------------------------
# E: recognition, relaxation, filling with numbers
# --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,obj,expect", [
    ('{"status": "infeasible", "ship_type": "destroyer"}', {"status": "infeasible", "ship_type": "destroyer"}, (True, "structured_key")),
    ('{"feasible": false}', {"feasible": False}, (True, "structured_key")),
    ('{"infeasible": true}', {"infeasible": True}, (True, "structured_key")),
    ('{"conflicts": ["speed vs displacement"]}', {"conflicts": ["speed vs displacement"]}, (True, "structured_key")),
    ('{"ship_type": "destroyer", "notes": "Aegis did not exist in 1895"}', {"ship_type": "destroyer", "notes": "Aegis did not exist in 1895"}, (True, "note_text")),
    ('{"ship_type": "impossible"}', {"ship_type": "impossible"}, (True, "ship_type")),
    ("This cannot be built in 1895.", None, (True, "text")),
    ('Sorry, impossible. {"ship_type": "destroyer"}', {"ship_type": "destroyer"}, (True, "text")),
    ('{"ship_type": "destroyer", "notes": "fast and well armed"}', {"ship_type": "destroyer", "notes": "fast and well armed"}, (False, "")),
    ('{"ship_type": "destroyer", "conflicts": []}', {"ship_type": "destroyer", "conflicts": []}, (False, "")),
    ('{"ship_type": "destroyer", "status": "ok"}', {"ship_type": "destroyer", "status": "ok"}, (False, "")),
    ("{broken", None, (False, "")),
])
def test_infeasible_marker(text, obj, expect):
    assert DE.infeasible_marker(text, obj) == expect


@needs_records
def test_e_classes(ref, th):
    req = {"country": "Japan", "year": 1936, "ship_type": "destroyer", "mission": ["aaw"], "max_displacement_t": 1500, "displacement_basis": "normal", "min_speed_kn": 33,
           "required": ["sam", "air_search_radar"]}
    p = prob(req, cat="E", pid="E01")
    refused = DE.score_row(p, {"id": "E01", "text": '{"status": "infeasible", "reason": "no SAM in 1936"}'}, ref, th)
    assert refused["e_class"] == "refused"
    invalid = DE.score_row(p, {"id": "E01", "text": "???"}, ref, th)
    assert invalid["e_class"] == "invalid"
    relaxed = DE.score_row(p, {"id": "E01", "output": destroyer()}, ref, th)  # a plain destroyer: ignores the SAM and radar
    assert relaxed["e_class"] == "relaxed"
    filled = DE.score_row(p, {"id": "E01", "output": destroyer(sensors=["air_search_radar"], weapons=[{"kind": "gun", "n": 2, "cal": 127}, {"kind": "sam", "n": 2}])}, ref, th)
    assert filled["req_all_sat"] and filled["e_class"] == "filled"  # satisfies everything but is anachronistic
    ok = DE.score_row(prob({**req, "year": 1975}, cat="E", pid="E01"), {"id": "E01", "output": destroyer(sensors=["air_search_radar"], weapons=[{"kind": "gun", "n": 2, "cal": 127}, {"kind": "sam", "n": 2}])},
                      ref, th)
    assert ok["e_class"] == "ok"
    a = DE.aggregate([refused, invalid, relaxed, filled, ok])
    assert a["e"]["recognized_infeasible_rate"] == 0.2 and a["e"]["relaxed_rate"] == 0.2 and a["e"]["filled_with_numbers_rate"] == 0.2 and a["e"]["satisfied_and_consistent_rate"] == 0.2


# --------------------------------------------------------------------------------------------------
# pairs
# --------------------------------------------------------------------------------------------------
def pair_problem(pid, role, req, kind="disp_down25", expected=None):
    exp = expected or X_EXP[kind]
    return {"id": f"{pid}_{role}", "category": "P", "req": req, "nbr": [], "meta": {"pair": {"id": pid, "role": role, "kind": kind, "changed": [], "expected": exp}}}


X_EXP = BE.EXP


@needs_records
def test_pair_eval_directions_identical_and_by_kind(ref, th):
    reqA = {**REQ, "max_displacement_t": 2000}
    reqB = {**REQ, "max_displacement_t": 1500}
    probs = {}
    for pid, kind in (("pair_01", "disp_down25"), ("pair_02", "speed_up4"), ("pair_03", "heli_add")):
        probs[f"{pid}_A"] = pair_problem(pid, "A", reqA, kind)
        probs[f"{pid}_B"] = pair_problem(pid, "B", reqB, kind)
    small = destroyer(displacement_normal_t=1400, power_hp=40000)
    big = destroyer(displacement_normal_t=1900, power_hp=40000)
    heli = destroyer(weapons=destroyer()["weapons"] + [{"kind": "aircraft_helicopter", "n": 1}], air=["helicopter"])
    outs = {"pair_01_A": big, "pair_01_B": small,                   # displacement decreased: correct
            "pair_02_A": destroyer(), "pair_02_B": destroyer(),    # identical output: speed did not increase
            "pair_03_A": destroyer(), "pair_03_B": heli}           # helicopter present in B: correct
    rows = [DE.score_row(probs[i], {"id": i, "seed": 1001, "output": o}, ref, th, skip_copy=True) for i, o in outs.items()]
    res = DE.pair_eval(rows, probs)
    assert res["n_pair_seed"] == 3 and res["accuracy"] == round(2 / 3, 4)
    assert res["by_kind"]["disp_down25"]["accuracy"] == 1.0 and res["by_kind"]["speed_up4"]["accuracy"] == 0.0 and res["by_kind"]["speed_up4"]["identical_rate"] == 1.0
    assert res["identical_output_rate"] == round(1 / 3, 4)
    assert res["by_metric"]["helicopter:present_B"]["rate"] == 1.0
    # an unreadable side makes the pair incorrect
    rows2 = [DE.score_row(probs["pair_01_A"], {"id": "pair_01_A", "seed": 1001, "output": big}, ref, th, skip_copy=True),
             DE.score_row(probs["pair_01_B"], {"id": "pair_01_B", "seed": 1001, "text": "x"}, ref, th, skip_copy=True)]
    assert DE.pair_eval(rows2, probs)["accuracy"] == 0.0


def test_eval_direction_ops():
    a, b = {"displacement": 100.0, "sam": False, "speed": None}, {"displacement": 80.0, "sam": True, "speed": 30.0}
    assert X.eval_direction({"metric": "displacement", "op": "decrease"}, a, b) is True
    assert X.eval_direction({"metric": "displacement", "op": "increase"}, a, b) is False
    assert X.eval_direction({"metric": "displacement", "op": "decrease"}, a, {"displacement": 100.0}) is False  # equal is not a decrease
    assert X.eval_direction({"metric": "sam", "op": "present_B"}, a, b) is True and X.eval_direction({"metric": "sam", "op": "absent_B"}, a, b) is False
    assert X.eval_direction({"metric": "speed", "op": "increase"}, a, b) is None


# --------------------------------------------------------------------------------------------------
# model comparison and reports
# --------------------------------------------------------------------------------------------------
@needs_records
def test_compare_models_table(ref, th):
    probs = {f"X{i:02d}": prob(REQ, cat="A" if i % 2 else "B", pid=f"X{i:02d}") for i in range(4)}

    def mk(spd):
        preds = [{"id": pid, "seed": s, "output": destroyer(speed_kn=spd)} for pid in probs for s in (1, 2)]
        rows = DE.score_model(probs, preds, ref, th, skip_copy=True)
        return DE.summarize(rows, probs)

    s1, s2 = mk(30), mk(34)
    assert s1["overall"]["all_req_satisfied_rate"] == 0.0 and s2["overall"]["all_req_satisfied_rate"] == 1.0
    assert s1["seeds"] == [1, 2] and set(s1["by_category"]) == {"A", "B"} and s1["overall"]["n"] == 8
    md = DE.compare_models({"before": s1, "after": s2})
    assert "基準: before" in md and "Δ after" in md and "+100.0" in md and "要求を全て満たした行" in md
    assert "# before" in DE.summary_markdown("before", s1) and "ペアテスト" not in DE.summary_markdown("before", s1)
    # a missing seed in a model's predictions counts as an invalid row
    rows = DE.score_model(probs, [{"id": "X00", "seed": 1, "output": destroyer()}, {"id": "X01", "seed": 2, "output": destroyer()}], ref, th, skip_copy=True)
    assert len(rows) == 8 and sum(r["json_ok"] for r in rows) == 2


# --------------------------------------------------------------------------------------------------
# inference helpers
# --------------------------------------------------------------------------------------------------
def test_infer_helpers(tmp_path):
    assert INF.count_generated([[5, 6, 99, 0, 0], [1, 2, 3]], [99, 98]) == 6  # first eos inclusive; no eos -> full length
    (tmp_path / "a.json").write_text(json.dumps({"ids": {"T1": ["a", "b"], "T2": ["c"]}, "all_ids": ["a", "b", "c"]}))
    (tmp_path / "b.json").write_text(json.dumps({"ids": {"T1": ["a"], "T2": ["c"]}}))
    (tmp_path / "c.json").write_text(json.dumps(["x", "y"]))
    (tmp_path / "d.txt").write_text("p\nq\n")
    assert INF.load_id_filter(tmp_path / "a.json") == {"a", "b", "c"} and INF.load_id_filter(tmp_path / "b.json") == {"a", "c"}
    assert INF.load_id_filter(tmp_path / "c.json") == {"x", "y"} and INF.load_id_filter(tmp_path / "d.txt") == {"p", "q"}


def test_make_forget_test_filters_and_copies_manifest(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "test.jsonl").write_text("\n".join(json.dumps({"id": i, "task": "T1"}) for i in ("a", "b", "c")) + "\n")
    (src / "manifest.json").write_text('{"coarse_types": ["x"]}')
    (tmp_path / "ids.json").write_text(json.dumps({"all_ids": ["a", "c", "zzz"]}))
    n = DE.make_forget_test(src / "test.jsonl", tmp_path / "ids.json", tmp_path / "out")
    assert n == 2 and [json.loads(l)["id"] for l in (tmp_path / "out" / "test.jsonl").read_text().splitlines()] == ["a", "c"]
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["coarse_types"] == ["x"]


# --------------------------------------------------------------------------------------------------
# builder pieces (no NSDB) and the generated data
# --------------------------------------------------------------------------------------------------
@needs_records
def test_design_index_matches_slow_requirement_check(ref):
    ix = BE.DesignIndex(ref.train, ref.designs)
    reqs = [{**REQ, "required": ["main_gun>=100mm", "torpedo_tubes"]},
            {"country": "X", "year": 1990, "ship_type": "frigate", "max_displacement_t": 4000, "displacement_basis": "full", "min_speed_kn": 25, "required": ["sam", "helicopter"], "min_aircraft": 1},
            {"country": "X", "year": 1990, "ship_type": "patrol", "max_displacement_t": 500, "displacement_basis": "normal", "max_complement": 40, "min_endurance_nm": 1000,
             "required": ["radar"], "min_gun_barrels": 2, "min_gun_barrels_cal_mm": 40, "required_propulsion": ["diesel"]},
            {"country": "X", "year": 1960, "ship_type": "destroyer", "max_displacement_t": 3000, "displacement_basis": "standard", "min_speed_kn": 30, "required": ["main_gun>=127mm"], "min_torpedo_tubes": 5}]
    for req in reqs:
        for ignore_hull in (True, False):
            fast = ix.mask(req, ignore_hull=ignore_hull)
            slow = [X.all_satisfied(req, d, ignore_hull=ignore_hull) for d in ref.designs]
            assert list(fast) == slow, req
        assert ix.mask(req).sum() >= 0


def test_manual_cases_are_well_formed():
    D, E = K.cases_D(), K.cases_E()
    assert len(D) == 50 and len(E) == 50
    for c in D + E:
        r = c["req"]
        assert r["max_displacement_t"] > 0 and r["displacement_basis"] in ("normal", "full", "standard") and r["mission"] and r["ship_type"] in T.COARSE_TYPES
        assert set(r) <= set(X.REQ_KEY_ORDER)
        assert all(t.startswith("main_gun>=") or t in C.REQ_WEIGHTS or t in ("mines", "minesweeping") for t in r.get("required", []))
    assert all(c["premise"] for c in D) and {c["meta"]["fiction_kind"] for c in D} == {"victor_alt", "fictional_nation", "tech_shift", "odd_constraints"}
    assert all(c["meta"]["why_impossible"]["reason"] and c["meta"]["why_impossible"]["category"] in ("era", "physics", "contradiction") for c in E)
    assert not {c["req"]["country"] for c in D if c["meta"]["fiction_kind"] == "fictional_nation"} & set(DE.REGION_OF)  # invented countries are not real ones


@pytest.mark.skipif(not (EVAL_DIR / "problems.jsonl").exists(), reason="eval data not generated")
def test_generated_eval_data_structure():
    probs = [json.loads(l) for l in (EVAL_DIR / "problems.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    pairs = [json.loads(l) for l in (EVAL_DIR / "pairs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    v0 = json.loads((C.OUT_DIR / "manifest.json").read_text(encoding="utf-8"))["system_prompt"]
    assert len(probs) == 250 and len(pairs) == 100 and len({r["id"] for r in probs + pairs}) == 350
    assert {c: sum(r["category"] == c for r in probs) for c in "ABCDE"} == {c: 50 for c in "ABCDE"}
    for r in probs + pairs:
        assert len(r["messages"]) == 2 and r["messages"][0]["content"] == v0  # same system prompt as design_v0, no gold
        u = json.loads(r["messages"][1]["content"])
        assert u["req"] == r["req"] and u["request"] == r["request"] and len(u["nbr"]) == 3
        assert all(y <= r["req"]["year"] + 2 for y in r["meta"]["nbr_years"])
    for r in probs:
        if r["category"] == "B":
            assert 4 <= len(X.constraint_units(r["req"])) <= 7 and r["meta"]["witness"] and r["meta"]["witness"] not in r["meta"]["nbr"]
        if r["category"] == "C":
            assert r["meta"]["n_sat_train_any"] <= 4 and r["meta"]["relaxed_counts"]
        if r["category"] == "E":
            assert r["meta"]["why_impossible"]["reason"] and r["meta"]["n_sat_train_same_type_up_to_year"] == 0
    ids = {r["meta"]["pair"]["id"] for r in pairs}
    assert len(ids) == 50 and all(r["meta"]["pair"]["expected"] for r in pairs)
    ov = json.loads((EVAL_DIR / "overlap_check.json").read_text(encoding="utf-8"))
    assert not any(ov["external_collisions"].values()) and ov["internal_collisions"] == []
    f = json.loads((EVAL_DIR / "forget_ids.json").read_text(encoding="utf-8"))
    assert f["counts"] == {"T1": 1000, "T2": 1000, "T3": 1000} and len(f["all_ids"]) == 3000 and len(set(f["all_ids"])) == 3000


@pytest.mark.skipif(not (BE.V1_2_TEST.exists() and (EVAL_DIR / "forget_ids.json").exists()), reason="v1_2 test / forget ids not generated")
def test_forget_subset_is_deterministic_and_stratified():
    a = BE.forget_subset()
    assert a["counts"] == {"T1": 1000, "T2": 1000, "T3": 1000}
    assert a == BE.forget_subset()
    assert a["ids"] == json.loads((EVAL_DIR / "forget_ids.json").read_text(encoding="utf-8"))["ids"]
    assert all(i.split(":")[1] == t for t, ids in a["ids"].items() for i in ids)
