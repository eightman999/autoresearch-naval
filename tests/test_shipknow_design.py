"""shipknow 設計タスク D のテスト: 年表抽出、要求生成、validator の代表ケース、漏れ防止、採点器・ベースライン。

合成レコードだけで動く（NSDB・生成済みデータは不要）。生成済みデータがあれば追加で整合性を確認する。
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

import pytest

from tools import shipknow_build_design as BD
from tools import shipknow_design_common as C
from tools import shipknow_design_score as SC
from tools import shipknow_design_validator as V
from tools import shipknow_history as H
from tools import shipknow_retriever as R

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "navspec" / "shipknow_design_v0"


# --------------------------------------------------------------------------------------------------
# synthetic records
# --------------------------------------------------------------------------------------------------
def make_rec(i: int, year: int, ship_type: str = "destroyer", fine: str = "destroyer", country: str = "Japan", rng: random.Random | None = None, **kw):
    rng = rng or random.Random(i)
    k = rng.uniform(0.85, 1.15)
    rec = {
        "id": f"c{i:05d}", "class_name": f"SHIPNAME{i} Class", "class_base": f"shipname{i}", "country": country, "source_url": f"http://u{i}",
        "year": year, "year_source": "class_when_date", "year_in_navy": year, "ship_type": ship_type, "ship_type_fine": fine,
        "displacement_normal_t": round(1500 * k), "displacement_full_t": round(1900 * k), "length_m": round(100 * k ** (1 / 3), 1), "beam_m": round(10 * k ** (1 / 3), 1),
        "draught_m": round(3.3 * k ** (1 / 3), 2), "shafts": 2, "power_hp": round(42000 * k * rng.uniform(0.9, 1.1)), "speed_kn": round(33 * rng.uniform(0.95, 1.05) * 2) / 2,
        "endurance_nm": 4000, "complement": round(180 * k), "propulsion": ["steam_turbine"],
        "weapons": [{"kind": "gun", "count": 4, "per_mount": 1, "caliber_mm": 127.0, "caliber_length": 50.0, "rounds": None, "name": "127mm"},
                    {"kind": "torpedo_tube", "count": 2, "per_mount": 4, "caliber_mm": 533.0, "caliber_length": None, "rounds": 8, "name": "TT"}],
        "armament_parse_ok": True, "sensors": [], "sensor_kinds": [], "sensors_known": True,
    }
    rec.update(kw)
    return rec


@pytest.fixture(scope="module")
def world():
    """合成の艦隊: 1930-44 の駆逐艦 300 隻（装備なし）、1960-80 のミサイル駆逐艦 60 隻（SAM / SSM / レーダー / ヘリ / ガスタービン）、1925-40 の潜水艦 60 隻。"""
    rng = random.Random(1)
    recs = []
    i = 0
    for _ in range(300):
        i += 1
        recs.append(make_rec(i, rng.randint(1930, 1944), rng=rng))
    for _ in range(60):
        i += 1
        recs.append(make_rec(i, rng.randint(1960, 1980), "destroyer", "missile-destroyer", "Italy", rng=rng, propulsion=["gas_turbine"], sensor_kinds=["air_search_radar", "sonar"],
                             weapons=[{"kind": "gun", "count": 1, "per_mount": 1, "caliber_mm": 127.0, "caliber_length": 54.0, "rounds": None, "name": "127"},
                                      {"kind": "sam", "count": 1, "per_mount": 1, "caliber_mm": None, "caliber_length": None, "rounds": None, "name": "SAM"},
                                      {"kind": "ssm", "count": 4, "per_mount": 1, "caliber_mm": None, "caliber_length": None, "rounds": None, "name": "SSM"},
                                      {"kind": "aircraft_helicopter", "count": 1, "per_mount": 1, "caliber_mm": None, "caliber_length": None, "rounds": None, "name": "1 helicopter"}]))
    for _ in range(60):
        i += 1
        k = rng.uniform(0.8, 1.2)
        recs.append(make_rec(i, rng.randint(1925, 1940), "submarine", "submarine", "Germany", rng=rng, displacement_normal_t=round(700 * k), displacement_full_t=None,
                             length_m=round(65 * k ** (1 / 3), 1), beam_m=round(6.2 * k ** (1 / 3), 1), draught_m=4.0, power_hp=2800, speed_kn=17.0, complement=44, propulsion=["diesel"],
                             weapons=[{"kind": "torpedo_tube", "count": 5, "per_mount": 1, "caliber_mm": 533.0, "caliber_length": None, "rounds": None, "name": "TT"},
                                      {"kind": "gun", "count": 1, "per_mount": 1, "caliber_mm": 88.0, "caliber_length": None, "rounds": None, "name": "88"}]))
    group_of = {r["id"]: f"g{r['id']}" for r in recs}
    th = V.build_thresholds(recs, group_of, None, min_n=30)
    return {"recs": recs, "group_of": group_of, "th": th}


# --------------------------------------------------------------------------------------------------
# history extraction
# --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("head,year,year_end,prec", [
    ("1923, Politruk", 1923, None, "exact"),
    ("7.1941", 1941, None, "exact"),
    ("4/1942", 1942, None, "exact"),
    ("mid-1990s, all", 1995, None, "decade"),
    ("late 1820s, all survived", 1828, None, "decade"),
    ("1935 - 1939, Australia", 1935, 1939, "exact"),
    ("1880s-1890s", 1885, 1899, "decade"),
    ("(Spring 1942, Bombay DYd), Kountouriotis, Spetsai", 1942, None, "exact"),
])
def test_parse_year_head(head, year, year_end, prec):
    d = H.parse_year_head(head)
    assert d is not None and d["year"] == year and d["year_end"] == year_end and d["prec"] == prec


def test_hull_numbers_are_not_years():
    assert H.parse_year_head("PC-1586, PC1590") is None
    assert H.parse_year_head("No.1650") is None


def test_modernizations_ops_and_kinds():
    text = "4/1942: - 8 x 1 - 12.7/90, CXAS Mk 1 radar; + 12 x 1 - 20/70 Mk 4, SC, 2x Mk 4, Mk 8 radars\n1946, MAS8: - 2 - 450 TT, 6 DC\n1957: + 1 x 3 - 305 Squid Mk 4 ASWRL, type 147 sonar"
    ev = H.parse_modernizations(text)
    assert [e["year"] for e in ev] == [1942, 1946, 1957]
    assert "radar" in ev[0]["add"] and "gun" in ev[0]["add"] and "mg" in ev[0]["remove"] and "radar" in ev[0]["remove"]
    # a leading minus is a removal, not an addition
    assert ev[1]["remove"] == ["torpedo_tube", "asw_weapon"] and ev[1]["add"] == []
    assert set(ev[2]["add"]) >= {"asw_weapon", "sonar"}
    # the ship-name prefix of the heading is not part of the text
    assert "MAS8" not in ev[1]["text"]


def test_naval_service_sentences_and_noise():
    text = "Both served on Adriatic Sea. TA22 25.6.1944 was hard damaged by US aircraft, 11.8.1944 disarmed and 2.5.1945 scuttled. TA35 was lost on a mine off Pola 17.8.1944. © Ivan Gogin, 2015\nNo significant events."
    ev = H.parse_naval_service(text)
    assert [e["year"] for e in ev] == [1944, 1944]
    assert all(len(e["text"]) <= H.MAX_TEXT for e in ev)
    assert H.parse_naval_service("No significant events. Many thanks to Wolfgang Stöhr for additional information on this page.") == []
    assert H.parse_modernizations("None.") == []


def test_kinds_do_not_misread_boat_types():
    assert H.kinds_in("They set the pattern for the torpedo boat destroyers, armoured gunboats") == []
    assert "torpedo_tube" in H.kinds_in("+ 2 x 2 - 533 TT") and "torpedo_tube" in H.kinds_in("fitted with torpedoes")
    assert H.kinds_in("1 x 1 - 20/70 Oerlikon") == ["gun"] and H.kinds_in("9 - 20/65 M1940") == ["gun"]
    assert H.kinds_in("Strela-2M SAM, Harpoon SSM, Phalanx CIWS") == ["ssm", "sam", "ciws"]


def test_removal_phrases_are_removals():
    ev = H.parse_modernizations("(1943-4/1944): all aircraft armament was removed (- 1 catapult, 6 seaplanes); + 4 x 2 - 40/56")
    assert ev and ev[0]["year"] == 1943 and "aircraft" in ev[0]["remove"] and "aircraft" not in ev[0]["add"] and "gun" in ev[0]["add"]


def test_event_text_is_cut_to_200_chars():
    long = "In 1943 " + "the ship was refitted and rearmed with new equipment " * 20 + "."
    ev = H.parse_naval_service(long)
    assert len(ev) == 1 and len(ev[0]["text"]) <= H.MAX_TEXT and ev[0]["text"].endswith("…")


def test_timeline_filters():
    ev = H.extract_events("1940: + 2 x 1 - 20/70 Oerlikon guns fitted at the dockyard\n1955: + SAM, radar\n1990s, all: + CIWS", "Alpha was sunk 12.3.1942 near Malta.", "")
    tl = H.timeline(ev, max_n=5, max_year=1950)
    assert [y for y, _ in tl] == [1940, 1942]  # nothing after the request year (1990s -> displayed 1995 is also out)
    assert [y for y, _ in H.timeline(ev, max_n=5, max_year=1950, forbidden=["Alpha"])] == [1940]
    assert H.timeline(ev, max_n=5, max_year=1950, exclude_prefixes=H.own_prefixes(ev)) == []
    assert len(H.timeline(ev, max_n=1)) == 1


# --------------------------------------------------------------------------------------------------
# requirements
# --------------------------------------------------------------------------------------------------
def test_requirements_are_satisfied_by_their_source_design(world):
    th = world["th"]
    rng = random.Random(5)
    n = 0
    for rec in world["recs"]:
        d = C.design_from_record(rec)
        for s in range(3):
            req = C.make_requirements(d, rec["country"], rec["year"], random.Random(s * 1000 + n))
            assert V.validate(req, d, th, layers=("req",))["ok"], (req, d)
            dref, _ = C.ref_displacement(d)
            ratio = req["max_displacement_t"] / dref
            assert 1.05 <= ratio <= 1.40
            assert d["speed_kn"] - 2.6 <= req["min_speed_kn"] <= d["speed_kn"]
            assert 1 <= len(req["required"]) <= 3 or not C.candidate_tokens(d)
            assert req["mission"] and set(req["mission"]) <= set(C.MISSION_TAGS)
            n += 1
        assert rng  # keep rng referenced


def test_mission_tag_rules():
    base = {"ship_type": "destroyer", "ship_type_fine": "destroyer", "weapons": [{"kind": "gun", "n": 4, "cal": 127}, {"kind": "torpedo_tube", "n": 2, "per": 4, "cal": 533}]}
    assert C.mission_tags(base) == ["surface_strike"]
    asw = {"ship_type": "frigate", "ship_type_fine": "frigate", "weapons": [{"kind": "gun", "n": 1, "cal": 100}, {"kind": "asw_rocket", "n": 1, "per": 6, "cal": 375}], "sensors": ["sonar"],
           "air": ["helicopter"], }
    assert C.mission_tags(asw)[:2] == ["escort", "asw"]
    assert C.mission_tags({"ship_type": "submarine", "ship_type_fine": "submarine", "weapons": []}) == ["submarine_ops"]
    assert C.mission_tags({"ship_type": "patrol", "ship_type_fine": "patrol-boat", "weapons": [{"kind": "mg", "n": 2}]}) == ["patrol"]


def test_required_tokens_are_checked_against_capabilities():
    d = {"ship_type": "destroyer", "weapons": [{"kind": "gun", "n": 2, "cal": 76}, {"kind": "depth_charge", "n": 2}], "sensors": ["navigation_radar", "sonar"], "air": ["helicopter"]}
    assert C.satisfies("main_gun>=76mm", d) and not C.satisfies("main_gun>=100mm", d)
    assert C.satisfies("asw_weapon", d) and C.satisfies("sonar", d) and C.satisfies("helicopter", d) and C.satisfies("radar", d)
    assert not C.satisfies("air_search_radar", d) and not C.satisfies("ssm", d) and not C.satisfies("torpedo_tubes", d)


def test_request_texts_mention_the_limits():
    req = {"country": "Japan", "year": 1950, "ship_type": "patrol", "mission": ["patrol"], "max_displacement_t": 150, "displacement_basis": "normal", "min_speed_kn": 22,
           "required": ["main_gun>=20mm", "radar"]}
    for v_ in range(3):
        t = C.request_text(req, v_)
        assert "150 t" in t and "22 kn" in t and "1950" in t and "20 mm" in t
    assert "Assume Japan won" in C.request_text(req, 0, premise="Assume Japan won the war.")
    ja = C.request_text_ja(req)
    assert "150t" in ja and "22kn" in ja and "日本" in ja


# --------------------------------------------------------------------------------------------------
# validator
# --------------------------------------------------------------------------------------------------
def _gold(world, idx: int):
    rec = world["recs"][idx]
    return rec, C.design_from_record(rec)


def test_validator_accepts_most_real_designs(world):
    ok = 0
    for rec in world["recs"]:
        d = C.design_from_record(rec)
        ok += V.validate(None, {**d, "year": rec["year"]}, world["th"])["ok"]
    assert ok / len(world["recs"]) > 0.9


def test_validator_flags_requirement_violations(world):
    rec, d = _gold(world, 3)
    req = C.make_requirements(d, rec["country"], rec["year"], random.Random(1))
    assert V.validate(req, d, world["th"], layers=("req",))["ok"]
    too_big = copy.deepcopy(d)
    too_big["displacement_normal_t"] = int(req["max_displacement_t"] * 1.05)
    r = V.validate(req, too_big, world["th"], layers=("req",))
    assert not r["ok"] and r["violations"][0]["code"] == "req_displacement_exceeds"
    slow = {**d, "speed_kn": req["min_speed_kn"] - 1}
    assert any(x["code"] == "req_speed_below" for x in V.validate(req, slow, world["th"])["violations"])
    req2 = {**req, "required": ["sam"]}
    assert any(x["code"] == "req_equipment_missing" and x["field"] == "sam" for x in V.validate(req2, d, world["th"])["violations"])
    assert any(x["code"] == "req_type_mismatch" for x in V.validate({**req, "ship_type": "patrol"}, d, world["th"])["violations"])
    assert any(x["code"] == "req_country_mismatch" for x in V.validate(req, {**d, "country": "France"}, world["th"])["violations"])


def test_validator_flags_physical_nonsense(world):
    rec, d = _gold(world, 5)
    th = world["th"]
    ctx = {"year": rec["year"]}
    assert V.validate(None, {**d, **ctx}, th)["ok"]
    triple = {**d, **ctx, **{k: d[k] * 3 for k in ("displacement_normal_t", "displacement_full_t")}}
    assert any(x["code"].startswith("ratio_") for x in V.validate(None, triple, th)["violations"])
    fast = {**d, **ctx, "speed_kn": d["speed_kn"] + 25}
    assert any(x["field"] in ("adm", "fr", "hp_t") for x in V.validate(None, fast, th)["violations"])
    wide = {**d, **ctx, "beam_m": d["beam_m"] * 2.5}
    assert not V.validate(None, wide, th)["ok"]
    crowded = {**d, **ctx, "complement": d["complement"] * 20}
    assert any(x["field"] == "crew_t" for x in V.validate(None, crowded, th)["violations"])
    assert not V.validate(None, {"ship_type": "destroyer"}, th)["ok"]  # schema: no displacement / length / speed
    assert V.validate(None, "not a dict", th)["violations"][0]["code"] == "schema_not_object"
    assert any(x["code"] == "schema_ship_type" for x in V.validate(None, {**d, "ship_type": "spaceship"}, th)["violations"])


def test_validator_flags_era_violations(world):
    th = world["th"]
    assert th["first_year"]["sam"]["year"] >= 1958 and th["first_year"]["prop:gas_turbine"]["year"] >= 1958
    rec, d = _gold(world, 7)  # a 1930s destroyer
    d_sam = copy.deepcopy(d)
    d_sam["weapons"].append({"kind": "sam", "n": 2})
    r = V.validate({"year": 1938, "country": "Japan"}, d_sam, th)
    assert any(x["code"] == "era_early" and x["field"] == "sam" for x in r["violations"])
    d_gt = {**d, "propulsion": ["gas_turbine"], "air": ["helicopter"], "weapons": d["weapons"] + [{"kind": "aircraft_helicopter", "n": 1}]}
    r = V.validate({"year": 1938, "country": "Japan"}, d_gt, th)
    assert {"prop:gas_turbine", "air:helicopter"} <= {x["field"] for x in r["violations"] if x["code"] == "era_early"}
    # the same equipment in the right decade is fine
    rec2 = next(r_ for r_ in world["recs"] if r_["ship_type_fine"] == "missile-destroyer")
    d2 = C.design_from_record(rec2)
    assert not any(x["code"] == "era_early" for x in V.validate({"year": rec2["year"], "country": "Italy"}, d2, th)["violations"])
    # five years of slack
    assert not any(x["code"] == "era_early" for x in V.validate({"year": th["first_year"]["sam"]["year"] - 5}, d_sam, th)["violations"])


def test_validator_flags_type_equipment_mismatch(world):
    th = world["th"]
    sub = next(C.design_from_record(r) for r in world["recs"] if r["ship_type"] == "submarine")
    sub_heli = {**sub, "air": ["helicopter"], "weapons": sub["weapons"] + [{"kind": "aircraft_helicopter", "n": 1}]}
    assert any(x["code"] == "type_equipment_mismatch" for x in V.validate({"year": 1930}, sub_heli, th)["violations"])
    sub_seaplane = {**sub, "air": ["seaplane"], "weapons": sub["weapons"] + [{"kind": "aviation_facility"}]}
    assert not any(x["code"] == "type_equipment_mismatch" for x in V.validate({"year": 1930}, sub_seaplane, th)["violations"])
    patrol = {"ship_type": "patrol", "ship_type_fine": "patrol-boat", "displacement_normal_t": 120, "length_m": 30, "speed_kn": 20, "weapons": [{"kind": "gun", "n": 1, "cal": 305}]}
    assert any(x["code"] == "type_equipment_mismatch" for x in V.validate({"year": 1960}, patrol, th)["violations"])


def test_corruptions_are_detected_on_synthetic_fleet(world):
    rng = random.Random(3)
    det = tot = 0
    for rec in world["recs"][:200]:
        d = C.design_from_record(rec)
        req = C.make_requirements(d, rec["country"], rec["year"], random.Random(1))
        for name, bad, _layer in V.corruptions(rng, req, d):
            if name in ("disp_x3", "power_x5", "crew_x8", "draught_x3"):
                tot += 1
                det += not V.validate(None, {**bad, "year": rec["year"]}, world["th"])["ok"]
    assert tot > 0 and det / tot > 0.8


# --------------------------------------------------------------------------------------------------
# leak guard / builder
# --------------------------------------------------------------------------------------------------
def test_builder_leak_guard_and_neighbor_rules(world):
    recs = world["recs"]
    group_of = dict(world["group_of"])
    target = recs[10]
    # make the target's group contain a sister class (must be excluded) and give every record a history that mentions names
    sister = make_rec(9001, target["year"], rng=random.Random(9))
    group_of[sister["id"]] = group_of[target["id"]]
    pool_recs = [r for r in recs if r["id"] != target["id"]] + [sister]
    pool = R.Pool(pool_recs, group_of)
    events = {}
    for r in pool_recs:
        events[r["id"]] = H.extract_events(f"{r['year']}: + radar\n{r['year'] + 20}: + SAM", f"{r['class_name'].split()[0]} was sunk 1.1.{r['year'] + 3} by {target['class_name'].split()[0]}.", "")
    own_events = H.extract_events("1941: + 2 x 1 - 20/70 Oerlikon Mk II/IV for the target", f"{target['class_name'].split()[0]} was sunk 1.1.1943.", "The own design story of 1936 was long and unusual here.")
    events[target["id"]] = own_events
    own_name = target["class_name"].split()[0]
    terms = BD.name_terms(target["class_name"], ["Tsukikage", "Hanaguri (ex-Orion)"])
    own_name = target["class_name"].split()[0]
    assert own_name in terms and "Tsukikage" in terms and "Orion" in terms
    exs = BD.build_examples(target, pool, group_of, events, terms, 3, 3, 90)
    assert 1 <= len(exs) <= 2
    for e in exs:
        user = e["messages"][1]["content"]
        assert own_name not in user and "Tsukikage" not in user and "Hanaguri" not in user and target["class_name"] not in user
        assert "own design story" not in user and "Oerlikon" not in user
        u = json.loads(user)
        assert sister["id"] not in e["meta"]["nbr"] and target["id"] not in e["meta"]["nbr"]
        assert len(u["nbr"]) <= 3
        for nb in u["nbr"]:
            assert nb["y"] <= u["req"]["year"] + 2
            assert all(y <= u["req"]["year"] for y, _t in nb.get("h", []))
            assert not {"name", "class_name", "source_url", "id", "d"} & set(nb)
        gold = json.loads(e["messages"][2]["content"])
        assert V.validate(u["req"], gold, world["th"], layers=("req",))["ok"]
        assert gold["ship_type"] == u["req"]["ship_type"]


def test_generic_names_are_not_leaks():
    req = {"country": "Australia", "ship_type": "patrol", "ship_type_fine": "fast-attack-craft"}
    assert BD.drop_generic(["Australia", "fast attack craft", "Kilo"], req) == ["Kilo"]


def test_neighbors_prefer_same_country_lineage(world):
    pool = R.Pool(world["recs"], world["group_of"])
    req = {"country": "Italy", "year": 1970, "ship_type": "destroyer", "ship_type_fine": "missile-destroyer", "mission": ["aaw"], "max_displacement_t": 1800,
           "displacement_basis": "normal", "min_speed_kn": 33, "required": ["sam"]}
    picks = BD.select_neighbors(pool, req, k=3)
    assert len(picks) == 3
    assert all(pool.recs[j]["year"] <= 1972 for j, _ in picks)
    assert sum(pool.recs[j]["country"] == "Italy" for j, _ in picks) >= 2


def test_scenarios_are_well_formed():
    ids = [s["id"] for s in BD.SCENARIOS]
    assert len(ids) == len(set(ids)) and 20 <= len(ids) <= 30
    for s in BD.SCENARIOS:
        req = s["req"]
        assert req["mission"] and set(req["mission"]) <= set(C.MISSION_TAGS)
        assert req["ship_type"] in V.T.COARSE_TYPES
        assert req["displacement_basis"] in ("normal", "full", "standard") and req["max_displacement_t"] > 0
        assert all(isinstance(t, str) for t in req["required"])
    assert sum(1 for s in BD.SCENARIOS if s["premise"]) >= 4


# --------------------------------------------------------------------------------------------------
# scorer / baselines
# --------------------------------------------------------------------------------------------------
def _rows(world, n=40):
    pool = R.Pool(world["recs"], world["group_of"])
    rows = []
    for rec in world["recs"][:n]:
        d = C.design_from_record(rec)
        req = C.make_requirements(d, rec["country"], rec["year"], random.Random(2))
        picks = BD.select_neighbors(pool, req, 3, world["group_of"][rec["id"]], rec["id"])
        rows.append({"id": f"{rec['id']}:D:0", "req": req, "gold": d, "nbr": [C.nbr_payload(pool.recs[j], None) for j, _ in picks]})
    return rows


def test_scorer_gold_and_invalid_outputs(world):
    rows = _rows(world)
    preds = {r["id"]: {"id": r["id"], "output": r["gold"]} for r in rows}
    res = SC.score(rows, preds, world["th"])
    assert res["json_invalid_rate"] == 0 and res["req_satisfied"] == 1.0
    assert res["closeness"]["weapons"]["f1"] == 1.0 and res["closeness"]["numeric_overall"]["median_rel_err"] == 0
    # text outputs: fenced JSON parses, garbage and missing ids are invalid
    preds2 = {rows[0]["id"]: {"id": rows[0]["id"], "text": "```json\n" + json.dumps(rows[0]["gold"]) + "\n```"}, rows[1]["id"]: {"id": rows[1]["id"], "text": "I cannot design"}}
    res2 = SC.score(rows[:3], preds2, world["th"])
    assert res2["json_invalid_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_baselines_use_only_neighbors(world):
    rows = _rows(world)
    r = rows[0]
    top1 = SC.baseline_top1(r)
    assert top1["ship_type"] == r["nbr"][0]["ty"] and top1.get("length_m") == r["nbr"][0].get("L")
    med = SC.baseline_median_vote(r)
    assert V._schema(med) == []
    lens = sorted(n["L"] for n in r["nbr"] if "L" in n)
    assert med["length_m"] == pytest.approx(C.B.rnd("length_m", lens[len(lens) // 2]), abs=0.6)
    res = SC.score(rows, {x["id"]: {"id": x["id"], "output": SC.baseline_top1(x)} for x in rows}, world["th"])
    assert res["json_invalid_rate"] == 0 and 0 <= res["req_satisfied"] <= 1


# --------------------------------------------------------------------------------------------------
# generated data (optional)
# --------------------------------------------------------------------------------------------------
@pytest.mark.skipif(not (OUT_DIR / "test.jsonl").exists() or not (OUT_DIR / "validator_thresholds.json").exists(), reason="generated data not present")
def test_generated_test_split_gold_meets_its_requirements_and_has_no_names():
    rows = SC.load_rows(OUT_DIR / "test.jsonl")
    assert rows
    th = V.load_thresholds()
    bad = 0
    for r in rows:
        bad += not V.validate(r["req"], r["gold"], th, layers=("req", "schema"))["ok"]
        text = json.dumps(r["messages"])
        assert "class_name" not in text and "source_url" not in text and "http" not in text
    assert bad == 0
    sc = [json.loads(l) for l in (OUT_DIR / "scenarios.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(sc) >= 20 and all("gold" not in s and len(s["messages"]) == 2 for s in sc)
