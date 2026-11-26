"""shipknow v0_2: two years (year / year_in_navy), the sailing_ship category and type-map fixes, new input fields, split inheritance."""

from __future__ import annotations

import json
import random
import sqlite3

import pytest

from tools import shipknow_build_tasks as B
from tools import shipknow_parse as P
from tools import shipknow_retriever as R
from tools import shipknow_split as SP
from tools import shipknow_types as T


# ---------------------------------------------------------------------------
# year / year_in_navy from the heading date
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,year,navy",
    [
        ("(1941 - 1942)", 1941, None),                       # ordinary class: heading = completion range
        ("(1926)", 1926, None),
        ("(1970s/1999)", 1970, 1999),                         # transferred: built in the 1970s, in this navy from 1999
        ("(1918/1925)", 1918, 1925),
        ("(1944 / 1946)", 1944, 1946),
        ("(1970s/1992-2006)", 1970, 1992),                    # B is a range: its first year
        ("(1944-1948/1953-1954)", 1944, 1953),
        ("(1990s)", 1990, None),                              # decade without a slash
        ("(2018-)", 2018, None),
        ("(1929 / ---)", 1929, None),                         # empty B: no transfer year
        ("(1936 / )", 1936, None),
        ("(/1992)", None, 1992),                              # A missing
        ("(/1990s)", None, 1990),
        ("(1, 1864)", 1864, None),                            # a leading count is not a year
        ("(16(38), 1919 - 1924)", 1919, None),
        ("(project 6631, 1966)", 1966, None),
        ("(1941-1942/1942-1944, 1943-1945)", 1941, 1942),     # batches: earliest A, earliest B
        ("(1982, 1972-1977/1982-1983)", 1972, 1982),
        ("", None, None),
        (None, None, None),
        ("(19th century)", None, None),
    ],
)
def test_parse_when_date_parts(text, year, navy):
    parts = P.parse_when_date_parts(text)
    assert (parts["year"], parts["navy_year"]) == (year, navy)


def test_parse_when_date_parts_kinds_and_consistency_with_parse_when_date():
    assert P.parse_when_date_parts("(1970s/1999)")["year_kind"] == "decade"
    assert P.parse_when_date_parts("(1970s/1999)")["navy_kind"] == "exact"
    assert P.parse_when_date_parts("(1941 - 1942)")["year_kind"] == "exact"
    # for headings with an A part, `year` is what parse_when_date already returned
    for t in ("(1941 - 1942)", "(1944/1947)", "(2018-)", "(1990s)", "(1962-1965/1992)"):
        assert P.parse_when_date_parts(t)["year"] == P.parse_when_date(t)[0]


def _row(**kw):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    base = dict(rowid=1, CLASS_NAME="TEST Class", TYPE="patrol-boats", COUNTRY="Albania", Displacement_normal="100", Displacement_full="", Length="30",
                Breadth="", Draught="", No_of_shafts="", Machinery="", Power="", speed="", Fuel="", Endurance="", Armament="", Armour="",
                Displacement_standard="", Electronic_equipment="", Complement="", WHEN_DATE="", HEADING="", extra_specs="", ships_kind="individual",
                source_url="https://www.navypedia.org/ships/albania/al_cg_r117.htm")
    base.update(kw)
    cols = list(base)
    conn.execute(f"CREATE TABLE t ({','.join(cols)})")
    conn.execute(f"INSERT INTO t VALUES ({','.join('?' * len(cols))})", [base[c] for c in cols])
    return conn.execute("SELECT * FROM t").fetchone()


URL = "https://myownonpmirror.com/ships/albania/al_cg_r117.html"


def _rec(when, ships=None, group=None, comp=None, launched=None, **kw):
    from tools import shipknow_extract as E

    return E.build_record(_row(WHEN_DATE=when, **kw), ships or {}, group, comp, launched)


def test_year_in_navy_transferred_class_takes_b_even_if_ships_say_otherwise():
    # Albania R117: SHIP_GROUPS.Completed = "1970s // 1999" -> year 1970, in this navy from 1999
    rec = _rec("(1970s/1999)", group={URL: 1970}, comp={URL: 1970})
    assert (rec["year"], rec["year_source"]) == (1970, "class_when_date_decade")
    assert (rec["year_in_navy"], rec["year_in_navy_source"]) == (1999, "when_date_b")
    # B given as a decade
    dec = _rec("(1960s/1990s)")
    assert (dec["year"], dec["year_in_navy"], dec["year_in_navy_source"]) == (1960, 1990, "when_date_b_decade")


def test_year_in_navy_ordinary_class_uses_ships_comp_then_group_then_launched_then_year():
    # Yamato-like: heading (1941 - 1942), SHIPS.Comp 1941 -> year_in_navy equals year
    rec = _rec("(1941 - 1942)", ships={URL: (1937, "ships_laid_launched")}, comp={URL: 1941})
    assert (rec["year"], rec["year_in_navy"], rec["year_in_navy_source"]) == (1941, 1941, "ships_comp")
    # a completion later than the heading start is kept (the first ship of the class entered service in 1943)
    late = _rec("(1941 - 1945)", comp={URL: 1943})
    assert (late["year"], late["year_in_navy"], late["year_in_navy_source"]) == (1941, 1943, "ships_comp")
    # no SHIPS.Comp -> SHIP_GROUPS.Completed
    grp = _rec("(1941 - 1942)", group={URL: 1942})
    assert (grp["year_in_navy"], grp["year_in_navy_source"]) == (1942, "ship_groups_completed")
    # no completion at all -> SHIPS.Launched, but only when it is not earlier than the build year
    ok = _rec("(1941 - 1942)", launched={URL: 1942})
    assert (ok["year_in_navy"], ok["year_in_navy_source"]) == (1942, "ships_launched")
    early = _rec("(1941 - 1942)", launched={URL: 1939})
    assert (early["year_in_navy"], early["year_in_navy_source"]) == (1941, "year_fallback")
    # nothing -> same value as year, and the source says so
    none = _rec("(1941 - 1942)")
    assert (none["year"], none["year_in_navy"], none["year_in_navy_source"]) == (1941, 1941, "year_fallback")


def test_year_edge_cases_empty_heading_and_b_only():
    # no heading: year from SHIPS laid/launched, year_in_navy from SHIPS.Comp
    rec = _rec("", ships={URL: (1910, "ships_laid_launched")}, comp={URL: 1913})
    assert (rec["year"], rec["year_source"], rec["year_in_navy"]) == (1910, "ships_laid_launched", 1913)
    # heading "(/1992)": only B -> year from SHIPS first; B is the in-navy year
    b = _rec("(/1992)", ships={URL: (1990, "ships_laid_launched")})
    assert (b["year"], b["year_source"], b["year_in_navy"], b["year_in_navy_source"]) == (1990, "ships_laid_launched", 1992, "when_date_b")
    # B only and nothing else: B doubles as year (last resort)
    b2 = _rec("(/1992)")
    assert (b2["year"], b2["year_source"], b2["year_in_navy"]) == (1992, "class_when_date_b_only", 1992)
    # no year at all
    nothing = _rec("")
    assert (nothing["year"], nothing["year_in_navy"], nothing["year_in_navy_source"]) == (None, None, None)
    # only a service year known (SHIPS.Comp): it also stands in for the missing build year
    only = _rec("", comp={URL: 2019})
    assert (only["year"], only["year_source"], only["year_in_navy"]) == (2019, "year_in_navy_fallback", 2019)


def test_year_in_navy_old_db_format_falls_back_to_year():
    from tools import shipknow_extract as E

    # the old DB has no WHEN_DATE column: the year comes from the class name and year_in_navy cannot know better
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (CLASS_NAME, TYPE, COUNTRY, Displacement_normal, Displacement_full, Length, Breadth, Draught, No_of_shafts, Machinery, "
                 "Power, speed, Fuel, Endurance, Armament, Electronic_equipment, Complement, source_url)")
    conn.execute("INSERT INTO t VALUES ('YAMATO1942) Class','battleships','Japan','64000','','263','','','','','','','','','','','','https://www.navypedia.org/ships/japan/jap_bb_yamato.htm')")
    old = E.build_record(conn.execute("SELECT *, rowid AS rowid FROM t").fetchone(), {})
    assert (old["year"], old["year_in_navy"], old["year_in_navy_source"]) == (1942, 1942, "year_fallback")


# ---------------------------------------------------------------------------
# ship types
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,fine,coarse",
    [
        # ships of the line keep their own class (the re-fetched TYPEs spell them "sailing-ships-of-the-line", "screw-ship-of-the-line")
        ("sailing-ships-of-the-line", "sailing-ship-of-the-line", "ship_of_the_line"),
        ("sailing-ship-of-the-line", "sailing-ship-of-the-line", "ship_of_the_line"),
        ("screw-ships-of-the-line", "screw-ship-of-the-line", "ship_of_the_line"),
        ("ships-of-the-line", "ship-of-the-line", "ship_of_the_line"),
        # wind-powered non-line warships are the new sailing_ship
        ("sailing-frigates", "sailing-frigate", "sailing_ship"),
        ("sailing-corvette", "sailing-corvette", "sailing_ship"),
        ("sailing-brigs", "sailing-brig", "sailing_ship"),
        ("sailing-schooners", "sailing-schooner", "sailing_ship"),
        ("sailing-cutters", "sailing-cutter", "sailing_ship"),
        ("sailing-bomb-vessels", "sailing-bomb-vessel", "sailing_ship"),
        ("brig", "brig", "sailing_ship"),
        # steamers stay in early_warship
        ("paddle-frigates", "paddle-frigate", "early_warship"),
        ("screw-corvettes", "screw-corvette", "early_warship"),
        ("composite-screw-sloops", "composite-screw-sloop", "early_warship"),
        # not submarines / not early steamers
        ("semi-submersible-boat", "semi-submersible-boat", "patrol"),
        ("semi-submersible-fast-attack-craft", "semi-submersible-fast-attack-craft", "fast_attack_craft"),
        ("auxiliary-patrol-and-anti-submarine-vessels", "auxiliary-patrol-and-anti-submarine-vessel", "patrol"),
        ("submersible-torpedo-boats", "submersible-torpedo-boat", "submarine"),
        ("drifters-steel-hull", "drifter-steel-hull", "patrol"),
        ("trawlers-wooden-hull", "trawler-wooden-hull", "patrol"),
        ("board-guard-ships", "board-guard-ship", "coast_guard"),
        ("armoured-river-motor-gunboats", "armoured-river-motor-gunboat", "gunboat_river"),
        ("river-motor-gunboats", "river-motor-gunboat", "gunboat_river"),
        ("motor-gun-boats", "motor-gun-boat", "fast_attack_craft"),
    ],
)
def test_new_db_type_mapping(raw, fine, coarse):
    r = T.classify(raw)
    assert (r["fine"], r["coarse"]) == (fine, coarse)


def test_sailing_ship_is_a_coarse_class_and_coexists_with_ship_of_the_line():
    assert "sailing_ship" in T.COARSE_TYPES and "ship_of_the_line" in T.COARSE_TYPES
    assert T.COARSE_TYPES.index("sailing_ship") == len(T.COARSE_TYPES) - 1  # appended: the order of the others is unchanged
    assert B.SYSTEM["T1"].count("sailing_ship") == 1


def test_cutters_follow_the_coast_guard_url_code_but_sailing_cutters_do_not():
    assert T.classify("fast-response-cutters", "https://www.navypedia.org/ships/usa/us_cg_webber.htm")["coarse"] == "coast_guard"
    assert T.classify("cutter-icebreaker", "https://www.navypedia.org/ships/usa/us_cg_northland.htm")["coarse"] == "coast_guard"
    assert T.classify("sailing-cutters", "https://www.navypedia.org/ships/france/fr_of_furet.htm")["coarse"] == "sailing_ship"
    assert T.classify("cutters", "https://www.navypedia.org/ships/usa/us_cg_x.htm")["coarse"] == "coast_guard"


def test_colonial_sloop_is_year_dependent():
    assert T.classify("colonial-sloop", None, 1937)["coarse"] == "escort"
    assert T.classify("colonial-sloop", None, 1850)["coarse"] == "early_warship"


# ---------------------------------------------------------------------------
# task builder: new input fields
# ---------------------------------------------------------------------------
def make_rec(i: int, **kw):
    base = {
        "id": f"c{i:05d}", "class_name": f"CLASS{i} Class", "class_base": f"class{i:03d}name", "country": "Japan", "source_url": f"http://u{i}",
        "year": 1935, "ship_type": "battleship", "ship_type_fine": "battleship",
        "displacement_normal_t": 40000 + 37 * i, "displacement_full_t": 45000, "length_m": 200.0 + i, "beam_m": 30.0, "draught_m": 9.0,
        "shafts": 4, "power_hp": 150000, "speed_kn": 27.0, "endurance_nm": 7000, "complement": 2000, "propulsion": ["steam_turbine"],
        "weapons": [{"kind": "gun", "count": 3, "per_mount": 3, "caliber_mm": 460.0, "caliber_length": 45.0, "rounds": None, "name": "x"}],
        "armament_parse_ok": True, "sensors": [], "sensor_kinds": ["radar"], "sensors_known": True,
    }
    base.update(kw)
    return base


V2 = dict(year_in_navy=1941, year_in_navy_source="ships_comp", displacement_standard_t=38000.0, armour_belt_mm=410.0, armour_deck_mm=230.0)


def test_full_view_new_fields_and_year_in_navy_only_when_different():
    v = B.full_view(make_rec(1, **V2))
    assert v["year"] == 1935 and v["year_in_navy"] == 1941
    assert v["displacement_standard_t"] == 38000 and v["armour_belt_mm"] == 410 and v["armour_deck_mm"] == 230
    same = B.full_view(make_rec(1, **{**V2, "year_in_navy": 1935}))
    assert "year_in_navy" not in same
    # no armour -> no keys; records without the v0_2 fields (v0 / v0_1) never get any of them
    bare = B.full_view(make_rec(1, year_in_navy=1935, displacement_standard_t=None, armour_belt_mm=None, armour_deck_mm=None))
    assert not set(B.NEW_KEYS) & set(bare)
    assert not set(B.NEW_KEYS) & set(B.full_view(make_rec(1)))


def test_targets_and_t3_hidden_fields_are_unchanged_by_the_new_inputs():
    rec = make_rec(3, **V2)
    old = make_rec(3)
    for builder in (B.build_t1, B.build_t2, B.build_t3):
        a, b = builder(rec, 7), builder(old, 7)
        assert [e["id"] for e in a] == [e["id"] for e in b]
        assert [e["messages"][2] for e in a] == [e["messages"][2] for e in b]  # same assistant targets
        assert [e.get("meta") for e in a] == [e.get("meta") for e in b]  # same hidden T3 fields
    t3 = B.build_t3(rec, 7)[0]
    user = json.loads(t3["messages"][1]["content"])
    assert user["displacement_standard_t"] == 38000 and "displacement_standard_t" not in user["predict"]
    assert set(json.loads(t3["messages"][2]["content"])) <= set(B.T3_FIELDS)


def test_partial_view_keeps_the_old_random_stream_when_new_fields_exist():
    rec = make_rec(5, **V2)
    full_new, full_old = B.full_view(rec), B.full_view(make_rec(5))
    for seed in range(40):
        pn = B.partial_view(full_new, random.Random(seed), rng_new=random.Random(seed + 1000))
        po = B.partial_view(full_old, random.Random(seed))
        assert {k: v for k, v in pn.items() if k not in B.NEW_KEYS} == po  # old fields: identical draw for draw
    # the new fields are dropped sometimes, never invented, and kept when no rng_new is given
    kept = [set(B.partial_view(full_new, random.Random(1), rng_new=random.Random(s))) & set(B.NEW_KEYS) for s in range(60)]
    assert any(k != set(B.NEW_KEYS & full_new.keys()) for k in kept) and any(kept)
    assert set(B.NEW_KEYS) & set(full_new) <= set(B.partial_view(full_new, random.Random(1)))


def test_system_prompt_explains_the_new_fields_only_for_v0_2_records():
    new = B.build_t1(make_rec(2, **V2), 1)[0]["messages"][0]["content"]
    old = B.build_t1(make_rec(2), 1)[0]["messages"][0]["content"]
    assert "year_in_navy" in new and "year_in_navy" not in old
    assert new.startswith(old)
    for t in (B.build_t2, B.build_t3):
        assert "year_in_navy" in t(make_rec(2, **V2), 1)[0]["messages"][0]["content"]


# ---------------------------------------------------------------------------
# retriever: new fields are shown, not (yet) part of the distance
# ---------------------------------------------------------------------------
def _pool(n=30):
    recs = [make_rec(i, country="Japan" if i % 2 else "Italy", **(V2 if i % 3 == 0 else {})) for i in range(1, n + 1)]
    group_of = {r["id"]: f"g{i // 4:03d}" for i, r in enumerate(recs)}
    return R.Pool(recs, group_of), recs, group_of


def test_neighbor_payload_shows_new_fields_and_no_ids():
    pool, recs, _ = _pool()
    rec = next(r for r in recs if r.get("year_in_navy"))
    p = R.neighbor_payload(rec, "T1", None, 0.5)
    assert p["year_in_navy"] == 1941 and p["displacement_standard_t"] == 38000 and p["armour_belt_mm"] == 410 and p["armour_deck_mm"] == 230
    for task in ("T2", "T3"):
        assert "displacement_standard_t" in R.neighbor_payload(rec, task, None, 0.5)
    assert "id" not in p and "c000" not in json.dumps(p)
    bare = R.neighbor_payload(make_rec(1), "T1", None, 0.5)
    assert not set(R.NBR_NEW) & set(bare)


def test_new_numeric_keys_have_zero_weight_unless_set():
    pool, recs, group_of = _pool()
    prm = json.loads(json.dumps(R.DEFAULT_PARAMS["T3"]))
    view = B.full_view(make_rec(100, **V2))
    d0 = pool.distances(view, "T3", prm)
    view_plain = {k: v for k, v in view.items() if k not in B.NEW_KEYS}
    assert (pool.distances(view_plain, "T3", prm) == d0).all()  # the new fields do not enter the distance by default
    prm["w"]["armB"] = 1.0
    assert not (pool.distances(view, "T3", prm) == d0).all()  # ... until a weight is given


def test_standard_displacement_stands_in_for_missing_normal_and_full():
    recs = [make_rec(i, displacement_normal_t=None, displacement_full_t=None, displacement_standard_t=30000.0 + 100 * i) for i in range(1, 7)]
    recs += [make_rec(i + 10, displacement_normal_t=30000.0 + 100 * i, displacement_full_t=33000.0 + 100 * i,
                      displacement_standard_t=29000.0 + 100 * i) for i in range(1, 7)]
    pool = R.Pool(recs, {r["id"]: f"g{i}" for i, r in enumerate(recs)})
    assert pool.std_off == pool.std_off  # finite (no NaN)
    q = pool._query_numbers({"displacement_standard_t": 30300})
    assert "logd" in q and "logS" in q
    assert not any(v != v for v in pool.num["logd"])  # every pool row has a usable displacement


# ---------------------------------------------------------------------------
# split inheritance
# ---------------------------------------------------------------------------
def _split_rec(i, url, base, **kw):
    d = {"id": f"c{i:05d}", "class_name": f"{base.upper()} Class", "class_base": base, "country": "Japan", "source_url": url, "year": 1930,
         "ship_type": "destroyer", "displacement_normal_t": round(500 * 1.1 ** i), "displacement_full_t": round(600 * 1.1 ** i + 7),
         "length_m": round(40 * 1.06 ** i, 1), "beam_m": round(5 * 1.05 ** i, 1), "power_hp": 20000 + 700 * i, "speed_kn": 30.0 + i % 7}
    d.update(kw)
    return d


def _write(d, recs, assign=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / "records.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    if assign is not None:
        (d / "splits.json").write_text(json.dumps({"assignments": assign}), encoding="utf-8")


def test_split_inheritance_keeps_old_splits_resolves_conflicts_and_places_new_groups(tmp_path):
    prev = [_split_rec(i, f"u{i}", f"name{i:03d}x") for i in range(1, 61)]
    prev_assign = {r["id"]: ("test" if i % 10 == 0 else "valid" if i % 10 == 1 else "train") for i, r in enumerate(prev, start=1)}
    _write(tmp_path / "prev", prev, prev_assign)
    # new version: new ids (shifted), same URLs; plus 12 brand-new classes; plus one bridge class that unites a train and a test class
    new = [_split_rec(i + 500, f"u{i}", f"name{i:03d}x") for i in range(1, 61)]
    new += [_split_rec(i + 700, f"unew{i}", f"fresh{i:03d}x", displacement_normal_t=round(900 * 1.1 ** i + 33), length_m=round(33 * 1.06 ** i + 0.3, 1),
                       beam_m=round(4.1 * 1.05 ** i, 1) + 0.2, power_hp=51000 + 3000 * i, speed_kn=20.0 + i) for i in range(12)]
    # make the old test class u10 and the old train class u12 one group through a shared country+name
    new[11]["class_base"] = new[9]["class_base"]
    _write(tmp_path / "new", new)
    doc = SP.run_inherit(tmp_path / "new", tmp_path / "prev", seed=3)
    a = doc["assignments"]
    inh = doc["inheritance"]
    # every old class that is not in the conflict group keeps its split
    for i in range(1, 61):
        if i in (10, 12):
            continue
        assert a[f"c{i + 500:05d}"] == prev_assign[f"c{i:05d}"], i
    # conflict: the group {u10 (test), u12 (train)} goes to one split (tie -> test) and the one that moved is reported
    assert a["c00510"] == a["c00512"] == "test"
    assert inh["conflict_groups"] == 1 and inh["earlier_records_moved"] == {"train->test": 1}
    assert inh["records_vs_earlier"] == {"same_split": 59, "changed_split": 1, "new_class": 12}
    assert doc["inherited_from"] == "prev"
    # new classes are placed (all three splits are valid values) and the leakage checks stay clean
    assert {a[f"c{i + 700:05d}"] for i in range(12)} <= {"train", "valid", "test"}
    ver = doc["verification"]
    for other in ("valid_vs_train", "test_vs_train"):
        assert ver[other]["shared_country_class_base"] == ver[other]["shared_source_url"] == 0
        assert ver[other]["shared_length_beam_fingerprint(>=15m)"] == ver[other]["shared_disp_power_speed"] == 0


def test_assign_with_fixed_groups_is_deterministic_and_respects_fixed():
    recs = [_split_rec(i, f"u{i}", f"n{i:03d}x") for i in range(1, 80)]
    roots, _ = SP.build_groups(recs)
    fixed = {roots[i]: "test" for i in range(0, 10)}
    a1 = SP.assign(recs, roots, 5, fixed=fixed)
    a2 = SP.assign(recs, roots, 5, fixed=fixed)
    assert a1 == a2 and all(a1[i] == "test" for i in range(0, 10))
    assert SP.assign(recs, roots, 5) == SP.assign(recs, roots, 5, fixed={})  # no fixed groups: the old behaviour
