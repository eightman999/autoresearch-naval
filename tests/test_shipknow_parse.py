"""Parser tests for the shipknow dataset (NSDB text conventions)."""

from __future__ import annotations

import sqlite3

import pytest

from tools import shipknow_parse as P
from tools import shipknow_types as T


# ---------------------------------------------------------------------------
# scalar fields: per-ship variants glued without separator
# ---------------------------------------------------------------------------
def test_hatsuharu_variant_first_value_and_label():
    seg, label, multi = P.isolate_first_variant("Hatsuharu+ Nenohi: 1490others: 1715")
    assert (seg, label, multi) == ("1490", "Hatsuharu+ Nenohi", True)
    assert P.parse_displacement("Hatsuharu+ Nenohi: 1490others: 1715")["value"] == 1490


def test_numeric_hull_number_labels_do_not_swallow_the_value():
    # "122-128: 2950" + glued label "129-132: 3050" must give 2950, not 2950129
    assert P.isolate_first_variant("122-128: 2950129-132: 3050")[0] == "2950"
    assert P.isolate_first_variant("56: 3856K: 35.5")[0] == "38"
    assert P.parse_power_hp("01+ 02: 37540 + 4000 = 4154007: 52300 + 4000 = 56300")[0] == 41540


def test_value_first_variant():
    assert P.isolate_first_variant("578Garm: 597")[:2] == ("578", None)


def test_gross_tonnage_is_not_displacement():
    d = P.parse_displacement("1533bm 1894 normal")
    assert d["value"] == 1894
    assert P.parse_displacement("2300bm")["value"] is None


def test_submarine_surface_submerged_pairs():
    d = P.parse_displacement("975 / 1164")
    assert (d["value"], d["submerged"]) == (975, 1164)
    s = P.parse_speed_kn("15.7 / 10")
    assert (s["value"], s["submerged"]) == (15.7, 10)


def test_length_prefers_overall_and_ocr_spaces():
    assert P.parse_length_m("103.5 pp 105.5 wl 109.5 oa")[0] == 109.5
    assert P.parse_length_m("111 . 6 pp 118 . 9 oa")[0] == 118.9
    assert P.parse_length_m("Suffren: 185.0 pp 196.0 oaColbert+ Foch: 185.0 pp 194.2 oa")[0] == 196.0


def test_speed_range_takes_upper_bound_and_power_total():
    assert P.parse_speed_kn("20 - 24")["value"] == 24
    assert P.parse_power_hp("20000 + 16000 = 36000")[0] == 36000
    assert P.parse_endurance("3500(7.5) / 75(5)") == {"nm": 3500.0, "kn": 7.5, "unlimited": False}


def test_missing_attributes_are_none():
    """Empty NSDB cells must become None, never 0 or an exception."""
    assert P.parse_displacement("")["value"] is None
    assert P.parse_length_m(None)[0] is None
    assert P.parse_power_hp("")[0] is None
    assert P.parse_speed_kn(None)["value"] is None
    assert P.parse_endurance("")["nm"] is None
    arm = P.parse_armament("")
    assert arm["weapons"] is None
    assert P.parse_sensors(None)["sensors"] is None
    assert P.parse_class_year("") == (None, "")


# ---------------------------------------------------------------------------
# class name -> year / base name
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,year,base",
    [
        ("HATSUHARU1935) Class", 1935, "hatsuharu"),
        ("ME3619431945) Class", 1943, "me36"),
        ("R1252004) Class", 2004, "r125"),
        ("KIROV(project26)1940) Class", 1940, "kirov"),
        ("YUZHENG13009 Class", None, "yuzheng13009"),
        ('"ModifiedBITTERN"19411943) Class', 1941, "bittern"),
    ],
)
def test_class_year_and_base(name, year, base):
    assert P.parse_class_year(name)[0] == year
    assert P.class_base_name(name) == base


# ---------------------------------------------------------------------------
# armament
# ---------------------------------------------------------------------------
def kinds(arm):
    return [(w["kind"], w["count"], w["per_mount"], w["caliber_mm"]) for w in arm["weapons"]]


def test_destroyer_with_torpedo_tubes_and_depth_charges():
    arm = P.parse_armament("3 x 2 - 127/50 3-shiki+ 2 x 4 - 610 TT (16)+ 2 DCT")
    assert kinds(arm) == [("gun", 3, 2, 127.0), ("torpedo_tube", 2, 4, 610.0), ("depth_charge", 2, 1, None)]
    assert arm["weapons"][1]["rounds"] == 16
    assert P.main_gun(arm["weapons"]) == {"caliber_mm": 127.0, "barrels": 6, "mounts": 3}


def test_missile_cruiser_with_aircraft():
    arm = P.parse_armament(
        "6 x 2 Bazalt SSM (12 P-500(4K80))+ 4 x 6 Kinzhal SAM (192 9M330)+ 2 x 1 - 100/59 AK-100+ "
        "8 x 6 - 30/54 AK-630M+ 2 x 10 RBU-12000 Udav-1M ASWRL+ 34 aircraft (Yak-38M attackers+ Ka-27+ Ka-27PS helicopters)"
    )
    got = {(w["kind"], w["count"], w["per_mount"]) for w in arm["weapons"]}
    assert ("ssm", 6, 2) in got and ("sam", 4, 6) in got
    assert ("ciws", 8, 6) in got and ("asw_rocket", 2, 10) in got
    assert ("aircraft_helicopter", 34, None) in got or any(w["kind"] == "aircraft_helicopter" and w["count"] == 34 for w in arm["weapons"])
    assert arm["n_other"] == 0  # the parenthesised aircraft list stays one item


def test_armament_variants_take_the_first_and_report_label():
    text = "Descartes: 4 x 1 - 165/45 M1891+ 2 - 450 TT (aw+ beam)Pascal: 4 x 1 - 165/45 M1891+ 14 x 1 - 47/40 M1885+ 2 - 450 TT (aw+ beam)"
    arm = P.parse_armament(text)
    assert arm["variant"]["multi"] and arm["variant"]["label"] == "Descartes"
    assert kinds(arm) == [("gun", 4, 1, 165.0), ("torpedo_tube", 2, 1, 450.0)]
    # unlabeled first variant followed by a labelled second one
    hn = "3 x 1 - 76/50 Mk 20+ 2 DCR (100)PF17+ 18: 2 x 1 - 76/50 Mk 20+ 8 DCT"
    arm = P.parse_armament(hn)
    assert [w["kind"] for w in arm["weapons"]] == ["gun", "depth_charge"] and arm["variant"]["multi"]


def test_alternatives_and_ranges():
    assert kinds(P.parse_armament("1 x 1 - 13.2/76 or 1 x 1 - 25/60 96-shiki+ 2 - 450 TT")) == [("mg", 1, 1, 13.2), ("torpedo_tube", 2, 1, 450.0)]
    assert P.parse_weapon_piece("(2 - 4) x 1 - 20/70 Oerlikon Mk II/IV")["count"] == 4
    assert P.parse_weapon_piece("small arms")["kind"] == "small_arms"


def test_mines_and_none():
    arm = P.parse_armament("2 x 1 - 40/70 Bofors 315+ 320 mines")
    assert arm["weapons"][-1]["kind"] == "mine" and arm["weapons"][-1]["count"] == 320
    assert P.parse_armament("none")["weapons"] == []


# ---------------------------------------------------------------------------
# sensors
# ---------------------------------------------------------------------------
def test_sensor_groups_inherit_the_trailing_kind_word():
    s = P.parse_sensors("Fut-N+ Fut-B+ Don radars+ MG-312IE Titan+ MG-311 Vychegda sonars+ Bizan'-4B ECM suite")
    assert [x["kind"] for x in s["sensors"]] == ["radar", "radar", "radar", "sonar", "sonar", "esm_ecm"]
    assert P.sensor_kinds(s["sensors"]) == ["esm_ecm", "radar", "sonar"]


def test_sensor_descriptive_words_and_variants():
    s = P.parse_sensors("SPS-49 air search+ SPS-10 surface search radars")
    assert P.sensor_kinds(s["sensors"]) == ["air_search_radar", "surface_radar"]
    v = P.parse_sensors("DD164+ 165: OPS-11B+ SPG-35 radars+ SQS-23 sonar+ OLT-3 ECM suitesDD166+ 167: OPS-11B+ OQS-3 sonar")
    assert v["variant"]["multi"] and "sonar" in P.sensor_kinds(v["sensors"])


# ---------------------------------------------------------------------------
# ship type normalisation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,fine,coarse",
    [
        ("patrol-boats", "patrol-boat", "patrol"),
        ("and-destroyers", "destroyer", "destroyer"),
        ("destroyer-escorts", "destroyer-escort", "escort"),
        ("class-torpedo-boats", "torpedo-boat", "torpedo_boat"),
        ("auxiliary-minesweepers-of-interwar-and-eras", "auxiliary-minesweeper", "mine_warfare"),
        ("coast-defence-battleships", "coast-defence-battleship", "coastal_defense_monitor"),
        ("nuclear-powered-ballistic-missile-submarines", "nuclear-powered-ballistic-missile-submarine", "submarine"),
        ("heavy-aircraft-carrying-cruiser", "heavy-aircraft-carrying-cruiser", "cruiser"),
        ("submarine-chasers", "submarine-chaser", "patrol"),
        ("rate-sailing", "rate-sailing", "ship_of_the_line"),
    ],
)
def test_type_mapping(raw, fine, coarse):
    r = T.classify(raw)
    assert (r["fine"], r["coarse"]) == (fine, coarse)


def test_year_dependent_and_url_fallback_types():
    assert T.classify("sloops", None, 1870)["coarse"] == "early_warship"
    assert T.classify("sloops", None, 1935)["coarse"] == "escort"
    r = T.classify("", "https://www.navypedia.org/ships/russia/ru_ms_t651.htm")
    assert (r["coarse"], r["type_source"]) == ("mine_warfare", "url_code")
    assert T.classify("")["coarse"] == "unknown"


# ---------------------------------------------------------------------------
# extract: a whole row with missing cells
# ---------------------------------------------------------------------------
def _row(**kw):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    base = dict(rowid=1, CLASS_NAME="TEST Class", TYPE="patrol-boats", COUNTRY="Japan", Displacement_normal="", Displacement_full="", Length="",
                Breadth="", Draught="", No_of_shafts="", Machinery="", Power="", speed="", Fuel="", Endurance="", Armament="",
                Electronic_equipment="", Complement="", source_url="https://www.navypedia.org/ships/japan/jap_cf_test.htm")
    base.update(kw)
    cols = list(base)
    conn.execute(f"CREATE TABLE t ({','.join(cols)})")
    conn.execute(f"INSERT INTO t VALUES ({','.join('?' * len(cols))})", [base[c] for c in cols])
    return conn.execute("SELECT * FROM t").fetchone()


def test_extract_record_with_missing_cells():
    from tools import shipknow_extract as E

    rec = E.build_record(_row(), {})
    assert rec["displacement_normal_t"] is None and rec["length_m"] is None and rec["weapons"] is None
    assert rec["year"] is None and rec["ship_type"] == "patrol"
    assert E.exclusion_reason(rec) == "no_core_numeric"


def test_extract_year_fallback_from_ships_and_numeric_row():
    from tools import shipknow_extract as E

    row = _row(CLASS_NAME="HATSUHARU1935) Class", TYPE="destroyers", Displacement_normal="Hatsuharu+ Nenohi: 1490others: 1715", Length="103.5 pp 105.5 wl 109.5 oa",
               speed="36", Power="42000", Armament="3 x 2 - 127/50 3-shiki+ 2 x 4 - 610 TT (16)+ 2 DCT", Electronic_equipment="")
    rec = E.build_record(row, {})
    assert (rec["year"], rec["year_source"]) == (1935, "class_name")
    assert rec["displacement_normal_t"] == 1490 and rec["length_m"] == 109.5 and rec["speed_kn"] == 36
    assert rec["variant_note"]["displacement_normal"] == "Hatsuharu+ Nenohi"
    assert rec["ship_type"] == "destroyer" and rec["armament_parse_ok"]
    assert rec["sensors"] is None and rec["sensors_known"] is False  # 1935+: empty Electronic_equipment means unknown
    old = E.build_record(_row(CLASS_NAME="OLD1905) Class", Length="50"), {})
    assert old["sensors"] == [] and old["sensors_known"] is True  # pre-radar era: empty means none
    row2 = _row(CLASS_NAME="FOO Class", Length="50")
    rec2 = E.build_record(row2, {"https://myownonpmirror.com/ships/japan/jap_cf_test.html": (1944, "ships_laid_launched")})
    assert (rec2["year"], rec2["year_source"]) == (1944, "ships_laid_launched")


def test_nominal_hp_and_moulded_depth_are_not_used():
    assert P.parse_power_hp("Firefly: 220nhpSpitfire: 140nhp 380ihp")[0] is None  # first variant is nominal hp only
    assert P.parse_power_hp("140nhp 380ihp")[0] == 380
    assert P.parse_draught_m("4.11 hold depth")[0] is None
    assert P.parse_draught_m("2.67 deep load")[0] == 2.67
    s = P.parse_sensors("Donets-2 or Liman radar")
    assert P.sensor_kinds(s["sensors"]) == ["radar"]


def test_item_tail_glued_to_next_variant_label_is_kept():
    arm = P.parse_armament("Sebenico: 4 x 1 - 120/46 Skoda+ 2 DCT+ 30 minesLubiana: 4 x 1 - 120/46 Skoda+ 1 x 1 - 37/54 Breda")
    assert [w["kind"] for w in arm["weapons"]] == ["gun", "depth_charge", "mine"] and arm["weapons"][-1]["count"] == 30
    assert arm["variant"]["label"] == "Sebenico"
    assert P.parse_weapon_piece("1 x 1 - 81/12 ASW mortar")["kind"] == "asw_rocket"


# ---------------------------------------------------------------------------
# torpedo tubes mis-read as guns (regression: 19 records had kind=gun with a torpedo caliber)
# ---------------------------------------------------------------------------
def first_kinds(text):
    arm = P.parse_armament(text)
    return kinds(arm), arm


def test_tube_keyword_glued_to_next_variant_label_is_a_tube_and_is_cut_off():
    # "TTHelgoland": the tube item is the last item of the first variant, "Helgoland" starts the next label
    got, arm = first_kinds(
        "Saida: 9 x 1 - 100/47 G. L/50 K.11+ 1 x 1 - 47/40 SFK L/44 S+ 3 x 2 - 450 TTHelgoland+ Novara: "
        "9 x 1 - 100/47 G. L/50 K.11+ 1 x 1 - 47/40 SFK L/44 S+ 1 x 2 - 533 TT+ 2 x 2 - 450 TT"
    )
    assert got == [("gun", 9, 1, 100.0), ("gun", 1, 1, 47.0), ("torpedo_tube", 3, 2, 450.0)]
    assert arm["variant"]["label"] == "Saida" and arm["variant"]["multi"]
    # the next variant's tubes (1 x 2 - 533, 2 x 2 - 450) must not leak into the first one
    assert all(w["caliber_mm"] != 533.0 for w in arm["weapons"])
    items, _ = P.first_variant_pieces("Saida: 9 x 1 - 100/47 G.+ 3 x 2 - 450 TTHelgoland+ Novara: 9 x 1 - 100/47 G.+ 1 x 2 - 533 TT")
    assert items[-1] == "3 x 2 - 450 TT"
    variants = P.split_variants("Saida: 9 x 1 - 100/47 G.+ 3 x 2 - 450 TTHelgoland+ Novara: 9 x 1 - 100/47 G.+ 1 x 2 - 533 TT")
    assert variants[1][0] == "Helgoland Novara" and variants[1][1] == ["9 x 1 - 100/47 G.", "1 x 2 - 533 TT"]


def test_tube_glued_label_variants_chile_france_germany():
    got, _ = first_kinds(
        "Almirante Uribe+ Almirante Riveros (ex-Faulknor): 2 x 1 - 120/45 BL Mk I+ 2 x 1 - 102/45 QF Mk VI+ 2 x 1 - 40/39 QF Mk II+ "
        "4 x 1 - 533 TTAlmirante Williams+ Almirante Riveros: 2 x 1 - 120/45 BL Mk I+ 2 x 2 - 533 TT"
    )
    assert got[-1] == ("torpedo_tube", 4, 1, 533.0) and [g[0] for g in got[:-1]] == ["gun"] * 3
    got, _ = first_kinds("D'Iberville: 1 x 1 - 100/45 M1891+ 2 - 450 TT (bow)+ 4 x 1 - 450 TTCasabianca+ Cassini: 1 x 1 - 100/45 M1891+ 1 - 450 TT (bow)+ 2 x 1 - 450 TT")
    assert got == [("gun", 1, 1, 100.0), ("torpedo_tube", 2, 1, 450.0), ("torpedo_tube", 4, 1, 450.0)]
    got, _ = first_kinds("Deutschland: 2 x 3 - 283/49 SK C/28+ 2 x 4 - 500 TTAdmiral Scheer+ Admiral Graf Spee: 2 x 3 - 283/49 SK C/28+ 2 x 4 - 533 TT")
    assert got == [("gun", 2, 3, 283.0), ("torpedo_tube", 2, 4, 500.0)]


def test_tube_glued_label_followed_by_more_variant_text_does_not_mix_variants():
    # label directly after TT, next variant carries different items (depth charges) that must stay out of the first variant
    arm = P.parse_armament("pr. 125: 2 x 2 - 23/43 AN-23+ 2 - 533 TTpr. 125A: 2 x 2 - 23/43 AN-23+ 2 DCR (4)")
    assert kinds(arm) == [("gun", 2, 2, 23.0), ("torpedo_tube", 2, 1, 533.0)] and arm["variant"]["label"] == "pr. 125"
    arm = P.parse_armament("P6058: 2 x 1 - 20/70 Mk 7 (HS804)+ 4 - 533 TTP6059-6077+ 6082-6091: 2 x 1 - 40/70 Bofors 350+ 4 - 533 TT or (2 - 533 TT+ mines)")
    assert kinds(arm) == [("gun", 2, 1, 20.0), ("torpedo_tube", 4, 1, 533.0)]
    # numeric label glued on the keyword ("TT11 - 19-go", "TT78Ya") and a lower-case one ("TTothers")
    assert kinds(P.parse_armament("10-go: 2 x 1 - 25/60 96-shiki+ 2 - 450 TT11 - 19-go: 3 x 1 - 25/60 96-shiki+ (2 - 450 TT+ 2 DCT (8)) or 4 DCT (12)")) == [("gun", 2, 1, 25.0), ("torpedo_tube", 2, 1, 450.0)]
    arm = P.parse_armament("H1+ 2: 2 - 450 TTothers: 1 x 1 - 7.9/79+ 2 - 450 TT")
    assert kinds(arm) == [("torpedo_tube", 2, 1, 450.0)] and arm["variant"]["multi"]  # "others" item and its tubes are the 2nd variant
    arm = P.parse_armament("76Ya+ 77Ya: 2 x 5 - 37/20 H corto+ 2 - 350 TT (bow)+ 1 x 2 - 350 TT78Ya+ 79Ya: 2 x 5 - 37/25 H lungo+ 1 x 1 - 350 TT")
    assert kinds(arm) == [("gun", 2, 5, 37.0), ("torpedo_tube", 2, 1, 350.0), ("torpedo_tube", 1, 2, 350.0)]


def test_tube_spellings_ta_catapults_launchers():
    arm = P.parse_armament("1 x 1 - 120/45 11-shiki+ 1 x 2 - 13.2/76+ 6 - 533 TA (4 bow+ 2 stern+ 14)")
    assert kinds(arm) == [("gun", 1, 1, 120.0), ("mg", 1, 2, 13.2), ("torpedo_tube", 6, 1, 533.0)] and arm["weapons"][-1]["rounds"] == 14
    # French "torpedo catapults": alternative calibres 533/550 take the first one; never aviation_facility / gun
    for text, cal, rounds in [("4 - 533/550 torpedo catapults (4)", 533.0, 4), ("2 - 533/550 KD-59E torpedo catapults (10 L5 Mod. 4)", 533.0, 10),
                              ("4 - 550 torpedo catapults (10 L5)", 550.0, 10), ("2 - 550 KD-59E torpedo catapults (10 L5)", 550.0, 10)]:
        w = P.parse_weapon_piece(text)
        assert (w["kind"], w["caliber_mm"], w["caliber_length"]) == ("torpedo_tube", cal, None), text
    assert P.parse_weapon_piece("2 catapults")["kind"] == "aviation_facility"  # aircraft catapults are unchanged
    assert P.parse_weapon_piece("1 H-4B catapult")["kind"] == "aviation_facility"
    # "torpedo launchers" are tubes (like "324 TT"), not asw_rocket
    for text in ["2 x 3 - 324 Mk 32 torpedo tubes", "2 x 2 - 533 torpedo launchers", "2 - 324 torpedo launcher"]:
        assert P.parse_weapon_piece(text)["kind"] == "torpedo_tube", text
    w = P.parse_weapon_piece("2 x 3 - 324 Mk 32 torpedo tubes")
    assert (w["count"], w["per_mount"], w["caliber_mm"]) == (2, 3, 324.0)
    assert P.parse_weapon_piece("2 torpedo launchers")["count"] == 2
    assert P.parse_weapon_piece("2 x 3 - 324 Mk 108 Weapon Alfa ASWRL")["kind"] == "asw_rocket"  # rocket launchers keep their kind


def test_tube_names_between_caliber_and_keyword():
    assert kinds(P.parse_armament("2 x 2 - 533 DTA-53 (Larsen & Toubro) TT (SET-65E+ 53-56KE)")) == [("torpedo_tube", 2, 2, 533.0)]
    for text, n, per in [("2 x 3 - 324 type 68 / HOS-301 TT", 2, 3), ("2 x 2 - 324 B515/H/2 TT (19 MU90)", 2, 2), ("2 x 3 - 324 Mk 32 TTD220", 2, 3)]:
        w = P.parse_weapon_piece(text)
        assert (w["kind"], w["count"], w["per_mount"], w["caliber_mm"]) == ("torpedo_tube", n, per, 324.0), text
    # 324 mm launchers without a TT keyword but with a torpedo-system name
    assert P.parse_weapon_piece("2 x 3 - 324 STWS.2")["kind"] == "torpedo_tube"
    assert P.parse_weapon_piece("4 - 324 DMTS90 (Stingray)")["kind"] == "torpedo_tube"


def test_torpedo_word_and_length_that_is_not_a_calibre_length():
    for text in ["2 - 533 torpedoes (side-drop collars) or 4 mines", "1 - 533 torpedo (bottom-drop collar or towed)", "4 - 533/550 foo"]:
        assert P.parse_weapon_piece(text)["kind"] == "torpedo_tube", text
    # cyrillic Te typed before "TT" must not hide the tubes behind a "mines" mention
    w = P.parse_weapon_piece(P._norm_text("6 - 533 ТTT (4 bow+ 2 stern+ 24 or 72 mines)"))
    assert (w["kind"], w["count"], w["caliber_mm"]) == ("torpedo_tube", 6, 533.0)


def test_big_guns_with_torpedo_calibres_stay_guns():
    """The caliber alone must not decide: 450 / 457 / 460 / 406 mm guns exist (and TAG/TR/TT in a gun name is not a tube)."""
    for text, cal, length in [("2 x 2 - 450/20 Armstrong 100ton MLR", 450.0, 20.0), ("1 x 1 - 457/40 BL Mk I", 457.0, 40.0),
                              ("3 x 3 - 460/45 94-shiki", 460.0, 45.0), ("3 x 3 - 406/50 Mk 7", 406.0, 50.0), ("2 x 2 - 381/42 BL Mk I", 381.0, 42.0)]:
        w = P.parse_weapon_piece(text)
        assert (w["kind"], w["caliber_mm"], w["caliber_length"]) == ("gun", cal, length), text
    assert P.parse_weapon_piece("1 x 1 - 457 BL gun")["kind"] == "gun"  # no keyword, no torpedo word
    for text in ["16 x 1 - 152/50 Armstrong TT", "2 x 1 - 152/50 EOC TT", "1 x 1 - 76/50 Vickers Mk TR", "2 x 1 - 90/42 G. L/45 K.12 TAG/BAG", "1 x 1 - 120/46 TAK120"]:
        assert P.parse_weapon_piece(text)["kind"] == "gun", text
    # a gun item ending a variant right before a glued label is not mistaken for a tube
    arm = P.parse_armament("Foo: 2 x 1 - 90/42 G. L/45 K.12 TAG/BAG+ 2 - 450 TTBar: 1 x 1 - 76/50 X")
    assert kinds(arm) == [("gun", 2, 1, 90.0), ("torpedo_tube", 2, 1, 450.0)]


def test_grenade_launchers_with_calibre_length_are_not_guns():
    assert P.parse_weapon_piece("1 x 1 - 40/19 Mk 19 grenade launcher")["kind"] == "grenade_launcher"
    assert P.parse_weapon_piece("1 x 1 - 30/10 PB-30 grenade launcher")["kind"] == "grenade_launcher"
    assert P.parse_weapon_piece("1 x 1 - 40/70 Bofors")["kind"] == "gun"


# ---------------------------------------------------------------------------
# variants: several "label:" marks inside one "+"-piece must not swallow the 2nd variant
# ---------------------------------------------------------------------------
def test_pieces_with_two_labels_do_not_mix_variants():
    arm = P.parse_armament("84: 1 x 1 - 12.7/90PC85: 1 x 1 - 20/70 Mk 10")
    assert kinds(arm) == [("mg", 1, 1, 12.7)] and arm["variant"]["multi"] and arm["variant"]["label"] == "84"
    assert arm["weapons"][0]["caliber_length"] == 90.0
    # numeric hull-number label glued on the calibre length ("90108-124"): length stays 90
    arm = P.parse_armament("105-107: 1 x 1 - 12.7/90108-124: 1 x 3 - 20/76 JM-61-MB Sea Vulcan")
    assert kinds(arm) == [("mg", 1, 1, 12.7)] and arm["weapons"][0]["caliber_length"] == 90.0
    arm = P.parse_armament("581+ 582+ 583+ 585: 2 x 2 Standard ARM SSM (4 RGM-66D)+ 1 x 1 - 76/50 Mk 34+ 2 x 1 - 12.7/90586+ 587+ 589+ 591: 2 x 2 Harpoon SSM (4 RGM-84A)+ 1 x 1 - 76/62 OTO-Melara Compact+ 2 x 1 - 12.7/90")
    assert kinds(arm) == [("ssm", 2, 2, None), ("gun", 1, 1, 76.0), ("mg", 2, 1, 12.7)] and arm["weapons"][-1]["caliber_length"] == 90.0
    # items of later variants stay out of the first one
    arm = P.parse_armament("CC1: 5 - 450 TT (4 bow+ 1 stern+ 5)CC2: 3 - 450 TT (2 bow+ 1 stern+ 3)")
    assert kinds(arm) == [("torpedo_tube", 5, 1, 450.0)] and arm["variant"]["multi"]
    arm = P.parse_armament("S1: 1 x 1 - 12.7/62S2: 4 x 1 - 12.7/62+ 8 DCT")
    assert kinds(arm) == [("mg", 1, 1, 12.7)]
    assert P.parse_armament("Sylfe: noneFavori: 2 x 1 espingoles")["weapons"] == []
    arm = P.parse_armament("Renard: 2 gunsothers: 6 guns")
    assert [(w["kind"], w["count"]) for w in arm["weapons"]] == [("gun", 2)]
    arm = P.parse_armament("Sphinx+ Crocodile: 6 x 1 - 151/8 24pdr SBML carronadesFulton and later: 2 x 1 - 151/8 24pdr SBML carronades+ 2 x 1 - 139/18 SBML")
    assert kinds(arm) == [("gun", 6, 1, 151.0)]
    arm = P.parse_armament("Tempête: 1 x 2 - 274/20 M1875 No.2Vengeur: 1 x 2 - 340/18 M1875")
    assert kinds(arm) == [("gun", 1, 2, 274.0)]


def test_label_like_ship_names_with_esm_inside():
    assert P._is_label_like("Statesman") and P._is_label_like("Sea Nymph")
    assert not P._is_label_like("2 ESM suite") and not P._is_label_like("ESM")


def test_bare_caliber_before_tt_is_not_a_count():
    w = P.parse_weapon_piece("533 TT (4)")  # Danish T53: "533 TT (4)" had been read as 533 tubes
    assert (w["kind"], w["count"], w["caliber_mm"], w["rounds"]) == ("torpedo_tube", None, 533.0, 4)
    assert P.parse_weapon_piece("6 TT (1 fwd aw+ 2 beam aw+ 1 aft aw+ 2 beam sub+)")["count"] == 6


def test_label_like_does_not_swallow_count_plus_mines_glued_to_a_label():
    # "8 minesGepard": the mines of the first variant, followed by the glued label of the next one
    arm = P.parse_armament("Bars: 12 - 450 TT (2 bow+ 2 stern+ 8 side drop-collars+ 12)+ 8 minesGepard+ Vepr': 12 - 450 TT (2 bow+ 12)+ 8 minesVolk: 1 x 1 - 75/48 Canet")
    assert [(w["kind"], w["count"]) for w in arm["weapons"]] == [("torpedo_tube", 12), ("mine", 8)]
    assert not P._is_label_like("8 minesGepard") and P._is_label_like("Gepard")


def test_hull_number_label_glued_to_a_noun_keeps_the_item():
    arm = P.parse_armament("105-107+ 131: 2 x 2 - 130/58 type 76+ 4 DCT+ 38 mines108-110+ 132-134: 2 x 2 - 130/58 type 76")
    assert [(w["kind"], w["count"]) for w in arm["weapons"]] == [("gun", 2), ("depth_charge", 4), ("mine", 38)] and arm["n_other"] == 0
    arm = P.parse_armament("MTB90 - 92+ 94: 1 x 2 - 12.7/62+ 2 - 533 TT+ 4 DCMTB227+ 239: 1 x 2 - 12.7/62+ 2 - 533 TT+ 2 DC")
    assert [(w["kind"], w["count"]) for w in arm["weapons"]] == [("mg", 1), ("torpedo_tube", 2), ("depth_charge", 4)] and arm["n_other"] == 0


def test_rocket_launcher_keyword_glued_to_label_is_not_a_big_gun():
    arm = P.parse_armament("A: 3 x 1 - 76/50 Mk 22+ 10 x 1 - 127 Mk 7 RL108: 3 x 1 - 76/50 Mk 22")
    assert [(w["kind"], w["caliber_mm"]) for w in arm["weapons"]] == [("gun", 76.0), ("rocket_launcher", 127.0)]
    arm = P.parse_armament("pr. A: 2 x 1 - 76/50 X+ 1 x 4 - 375 Mod. 1972 F1 ASWRLF782: 2 x 1 - 76/50 X")
    assert [(w["kind"], w["caliber_mm"]) for w in arm["weapons"]] == [("gun", 76.0), ("asw_rocket", 375.0)]
    assert P.parse_weapon_piece("1 x 4 - 178 Mousetrap DCT")["kind"] == "asw_rocket"


def test_items_before_a_variant_label_are_not_swallowed_by_it():
    # space-separated label ("... Mk 10 A3:") and upper-case label glued ("... Mk IOkinoshima:") after the last item of a variant
    arm = P.parse_armament("A 1: 3 x 1 - 40/60 Mk 3+ 2 x 1 - 20/70 Mk 10 A3: 1 x 2 - 40/60 Mk 1+ 2 x 1 - 20/70 Mk 10 A6: 1 x 1 - 40/60 Mk 3")
    assert kinds(arm) == [("gun", 3, 1, 40.0), ("gun", 2, 1, 20.0)]
    arm = P.parse_armament("Manileono: 1 x 1 - 57/50 Driggs-Schroeder Mk II/III+ 2 x 1 - 37/40 Driggs-Schroeder heavy Mk IMariveles: 1 x 1 - 47/40-45 Driggs-Schroeder Mk I/II")
    assert kinds(arm) == [("gun", 1, 1, 57.0), ("gun", 2, 1, 37.0)]
    # depth charges / mines glued to the next label ("2 DCRP630", "4 DCcompleted 1943", "mines515")
    arm = P.parse_armament("completed 1940 - 1943: 2 x 1 - 12.7/79+ 2 - 533 TT+ 4 DCcompleted 1943 - 1945: 1 x 1 - 20/62 ShVAK+ 2 - 533 TT+ 4 DC")
    assert kinds(arm) == [("mg", 2, 1, 12.7), ("torpedo_tube", 2, 1, 533.0), ("depth_charge", 4, 1, None)]
    arm = P.parse_armament("P3101: 1 x 1 - 40/60 Mk 7+ 1 x 3 - 305 Squid Mk 3 ASWRLP3102 - 3124: 1 x 1 - 40/60 Mk 7+ 2 DCT")
    assert kinds(arm) == [("gun", 1, 1, 40.0), ("asw_rocket", 1, 3, 305.0)]
    # USV / sweeping items are items, not ship names
    arm = P.parse_armament("pr. 257D: 1 x 2 - 30/71 AK-230M+ 2 DCR (27)+ IU-1 USV+ IT-3 USV+ I-2 USV+ pr. 257DM+ 257DME: 1 x 2 - 30/71 AK-230M+ 2 DCR (27)")
    assert [w["kind"] for w in arm["weapons"]] == ["gun", "depth_charge", "minesweeping_gear", "minesweeping_gear", "minesweeping_gear"]
    assert arm["variant"]["label"] == "pr. 257D"


def test_mines_glued_to_an_as_designed_label_are_kept():
    arm = P.parse_armament("as designed: 3 x 2 - 130/50 B-2LM+ 3 x 3 - 533 TT+ 2 DCR (25)+ 80 minesas completed: 3 x 1 - 130/50 B-13+ 80 mines")
    assert [(w["kind"], w["count"]) for w in arm["weapons"]] == [("gun", 3), ("torpedo_tube", 3), ("depth_charge", 2), ("mine", 80)]
    assert arm["variant"]["label"] == "as designed" and arm["variant"]["n_variants"] == 2


def test_parenthesised_first_alternative_is_a_group_of_items():
    arm = P.parse_armament("(1 x 1 - 20/70 Oerlikon Mk II/IV+ 1 x 2 - 12.7/62) or 2 x 2 - 12.7/62+ mechanical minesweeping gear")
    assert [(w["kind"], w["count"]) for w in arm["weapons"]] == [("gun", 1), ("mg", 1), ("minesweeping_gear", None)]
    arm = P.parse_armament("earlier: 1 x 1 - 76/40 12pdr QF Mk I+ (1 x 1 - 40/39 2pdr or 1 x 1 - 40/56 Bofors+ 2 x 1 - 20/70 Oerlikon) or 3 x 1 - 20/70 Oerlikon+ 4 DCT+ mechanical minesweeping gearlater: 1 x 1 - 102/45 QF Mk XVI")
    assert [(w["kind"], w["count"], w["caliber_mm"]) for w in arm["weapons"]] == [("gun", 1, 76.0), ("gun", 1, 40.0), ("gun", 2, 20.0), ("depth_charge", 4, None), ("minesweeping_gear", None, None)]
    # remarks in parentheses are still ignored; an item that merely starts with a group and ends with a parenthesis is not a remark
    assert P.parse_weapon_piece("(fitted for)") is None and P.parse_weapon_piece("(some remark)") is None
    w = P.parse_weapon_piece("(1 x 64 + 1 x 32) Standard SM-2MR SAM / ASROC VLA ASuR Mk 41 (VLS+ 96 RIM-66)")
    assert (w["kind"], w["count"], w["per_mount"]) == ("sam", 1, 96)  # two VLS groups -> total cells
    w = P.parse_weapon_piece("(6 x 6 + 2 x 6) HQ-9 SAM (VLS+ 48 HQ-9)")
    assert (w["kind"], w["count"], w["per_mount"]) == ("sam", 1, 48)


def test_mines_glued_to_a_parenthesised_label():
    arm = P.parse_armament("(ex-AK-582)+ (ex-AK-599): 1 x 1 - 76/48 D-56TS+ 1 x 1 - 7.6/94+ 10 mines(ex-AK-248): 1 x 1 - 76/48 D-56TS+ 10 mines")
    assert [(w["kind"], w["count"]) for w in arm["weapons"]] == [("gun", 1), ("mg", 1), ("mine", 10)] and arm["variant"]["n_variants"] == 2


def test_depth_charges_glued_to_an_as_xxx_label_are_kept():
    arm = P.parse_armament("as MGB: 1 x 1 - 47/40 H1901+ 1 x 1 - 6.5/115+ 2 DCas MTB: (2 - 3) x 1 - 6.5/115+ 2 - 450 TT+ 4 DC")
    assert [(w["kind"], w["count"]) for w in arm["weapons"]] == [("gun", 1), ("mg", 1), ("depth_charge", 2)]


# ---------------------------------------------------------------------------
# year decision: re-fetched NSDB (CLASS.WHEN_DATE) vs old NSDB (year glued into CLASS_NAME)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("(1941 - 1942)", (1941, "exact")),
        ("(1944/1947)", (1944, "exact")),
        ("(1944 / 1946)", (1944, "exact")),
        ("(2018-)", (2018, "exact")),
        ("(1962-1965/1992)", (1962, "exact")),
        ("(1990s)", (1990, "decade")),
        ("(1980s-90s)", (1980, "decade")),
        ("(1970s/1992-2006)", (1970, "decade")),
        ("", (None, None)),
        (None, (None, None)),
        ("(19th century)", (None, None)),
    ],
)
def test_parse_when_date(text, expected):
    assert P.parse_when_date(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("7.1941", 1941), ("8.4.1939", 1939), ("12/1951", 1951), ("1963-69 // 1967-69", 1963), ("1980s-90s", 1980),
     ("1969-90 // 1992: A-01", 1969), ("---", None), ("", None), (None, None), ("// 1950", None)],
)
def test_parse_comp_year(text, expected):
    assert P.parse_comp_year(text) == expected


def test_completed_year_takes_the_earliest_line():
    assert P.completed_year("1983 // 2004: A-03\n1969-90 // 1992: A-01\n1970 // 2004 A-05") == 1969
    assert P.completed_year("") is None


def _new_class_row(**kw):
    """A CLASS row of the re-fetched DB: no year in CLASS_NAME, WHEN_DATE/HEADING/ships_kind/... present."""
    base = dict(CLASS_NAME="YAMATO Class", TYPE="battleships", WHEN_DATE="(1941 - 1942)", HEADING="YAMATO battleships (1941 - 1942)",
                ships_kind="individual", Armour="", Displacement_standard="", extra_specs="",
                Displacement_normal="64000", Length="263 oa")
    base.update(kw)
    return _row(**base)


def test_extract_year_prefers_class_when_date_in_new_db():
    from tools import shipknow_extract as E

    ships = {"https://myownonpmirror.com/ships/japan/jap_cf_test.html": (1937, "ships_laid_launched")}
    rec = E.build_record(_new_class_row(), ships)
    # not the laid-down year 1937, and not a year read from the (year-less) name
    assert (rec["year"], rec["year_source"]) == (1941, "class_when_date")
    assert rec["when_date_raw"] == "(1941 - 1942)" and rec["year_laid_launched"] == 1937 and rec["ships_kind"] == "individual"
    dec = E.build_record(_new_class_row(WHEN_DATE="(1990s)"), ships)
    assert (dec["year"], dec["year_source"]) == (1990, "class_when_date_decade")


def test_extract_year_fallbacks_in_new_db():
    from tools import shipknow_extract as E

    url = "https://myownonpmirror.com/ships/japan/jap_cf_test.html"
    # no WHEN_DATE -> SHIPS laid/launched, then SHIPS.WHEN_DATE
    rec = E.build_record(_new_class_row(WHEN_DATE=""), {url: (1944, "ships_laid_launched")})
    assert (rec["year"], rec["year_source"]) == (1944, "ships_laid_launched")
    rec = E.build_record(_new_class_row(WHEN_DATE=None), {url: (1951, "ships_when")})
    assert (rec["year"], rec["year_source"]) == (1951, "ships_when")
    # group-summary class: no SHIPS rows -> SHIP_GROUPS.Completed
    grp = E.build_record(_new_class_row(WHEN_DATE="", ships_kind="group"), {}, {url: 1969}, {})
    assert (grp["year"], grp["year_source"], grp["year_commissioned"]) == (1969, "ship_groups_completed", 1969)
    none = E.build_record(_new_class_row(WHEN_DATE=""), {})
    assert (none["year"], none["year_source"]) == (None, None)
    # a digit run in a year-less name is not a date in the new format
    assert E.build_record(_new_class_row(CLASS_NAME="Type 1936 (1936) Class", WHEN_DATE=""), {})["year"] is None


def test_extract_year_in_old_db_is_unchanged():
    from tools import shipknow_extract as E

    url = "https://myownonpmirror.com/ships/japan/jap_cf_test.html"
    old = E.build_record(_row(CLASS_NAME="YAMATO1942) Class", Length="263 oa"), {url: (1937, "ships_laid_launched")})
    assert (old["year"], old["year_source"]) == (1942, "class_name")
    assert old["when_date_raw"] is None and old["displacement_standard_t"] is None and old["armour_parts"] is None
    assert old["year_commissioned"] is None and old["diving_depth_m"] is None
    # an old-format row ignores the new group table
    assert E.build_record(_row(CLASS_NAME="FOO Class", Length="50"), {}, {url: 1969}, {})["year_source"] == "ship_groups_completed"


def test_new_columns_become_record_fields():
    from tools import shipknow_extract as E

    rec = E.build_record(
        _new_class_row(
            Armour="Harvey steel; belt: 350 - 150+ deck: 45+ turrets: 220+ CT: 125", Displacement_standard="305dwt",
            extra_specs='{"Diving depth operational, m": "75 (60 with landing craft)"}',
        ),
        {}, {}, {"https://myownonpmirror.com/ships/japan/jap_cf_test.html": 1941},
    )
    assert rec["armour_material"] == "Harvey steel" and rec["armour_belt_mm"] == 350 and rec["armour_deck_mm"] == 45
    assert rec["armour_turret_mm"] == 220 and rec["armour_ct_mm"] == 125 and rec["armour_raw"].startswith("Harvey steel")
    assert rec["displacement_standard_t"] is None and "standard:displacement_standard_dwt" in rec["flags"]
    assert rec["diving_depth_m"] == 75 and rec["year_commissioned"] == 1941
    ok = E.build_record(_new_class_row(Displacement_standard="1576"), {})
    assert ok["displacement_standard_t"] == 1576


def test_parse_armour_and_diving_depth():
    a = P.parse_armour("main belt: 356 - 343+ bulkheads: 305+ barbettes: 330 - 279+ turrets: 330 (face) - 229 - 178 (sides) - 152 (roof)+ CT: 76 -64")
    assert [(p["part"], p["max_mm"]) for p in a["parts"]] == [("belt", 356), ("bulkhead", 305), ("barbette", 330), ("turret", 330), ("conning_tower", 76)]
    assert P.parse_armour("")["parts"] == [] and P.parse_armour(None)["material"] is None
    assert P.parse_diving_depth_m('{"Diving depth operational, m": "30 max"}')[0] == 30
    assert P.parse_diving_depth_m('{"Diving depth operational, m": "0 (escape hatch)"}')[0] is None
    assert P.parse_diving_depth_m('{"Military load": "210 t cargo"}')[0] is None
    assert P.parse_diving_depth_m("not json")[0] is None


def _make_db(path, new: bool):
    con = sqlite3.connect(path)
    cols = ("CLASS_NAME TEXT, TYPE TEXT, COUNTRY TEXT, Displacement_normal TEXT, Displacement_full TEXT, Length TEXT, Breadth TEXT, Draught TEXT, "
            "No_of_shafts TEXT, Machinery TEXT, Power TEXT, speed TEXT, Fuel TEXT, Endurance TEXT, Armour TEXT, Armament TEXT, "
            "Electronic_equipment TEXT, Complement TEXT, sections TEXT, source_url TEXT, fetched_at TEXT")
    if new:
        con.execute(f"CREATE TABLE CLASS ({cols}, Displacement_standard TEXT, WHEN_DATE TEXT, HEADING TEXT, extra_specs TEXT, ships_kind TEXT)")
        con.execute("CREATE TABLE SHIPS (NAME TEXT, CLASS TEXT, COUNTRY TEXT, Laid_down TEXT, Launched TEXT, WHEN_DATE TEXT, source_url TEXT, Comp TEXT)")
        con.execute("CREATE TABLE SHIP_GROUPS (COUNTRY TEXT, CLASS TEXT, source_url TEXT, seq INTEGER, Names TEXT, Builders TEXT, Completed TEXT)")
    else:
        con.execute(f"CREATE TABLE CLASS ({cols})")
        con.execute("CREATE TABLE SHIPS (NAME TEXT, CLASS TEXT, COUNTRY TEXT, Laid_down TEXT, Launched TEXT, WHEN_DATE TEXT, source_url TEXT)")
    U = "https://www.navypedia.org/ships/japan/"
    arm = "3 x 3 - 460/45 Type 94+ 2 x 2 - 155/60"
    rows = [  # (name_old, name_new, when, url, laid, launched, comp)
        ("YAMATO1942) Class", "YAMATO Class", "(1941 - 1942)", U + "jap_bb_yamato.htm", "4.11.1937", "8.1940", "12.1941"),
        ("KONGO Class", "KONGO Class", "", U + "jap_bb_kongo.htm", "17.1.1911", "18.5.1912", "16.8.1913"),
    ]
    for old_name, new_name, when, url, laid, launched, comp in rows:
        vals = [old_name if not new else new_name, "battleships", "Japan", "64000", "", "263 oa", "38.9", "10.4", "4", "", "150000", "27", "oil", "", "",
                arm, "", "2500", "", url, "2026-10-07"]
        if new:
            vals += ["", when, "", "", "individual"]
            con.execute(f"INSERT INTO CLASS VALUES ({','.join('?' * len(vals))})", vals)
            con.execute("INSERT INTO SHIPS VALUES ('X', ?, 'Japan', ?, ?, ?, ?, ?)", (new_name, laid, launched, when, url, comp))
        else:
            con.execute(f"INSERT INTO CLASS VALUES ({','.join('?' * len(vals))})", vals)
            con.execute("INSERT INTO SHIPS VALUES ('X', ?, 'Japan', ?, ?, ?, ?)", (old_name, laid, launched, when, url))
    if new:  # a group-summary class: only SHIP_GROUPS has dates
        vals = ["A-01 Class", "patrol-boats", "Abkhazia", "", "", "23.8", "5.1", "1", "2", "", "2000", "29", "diesel", "", "", "", "", "9", "",
                "https://www.navypedia.org/ships/abkhazia/abk_cf_1400m.htm", "2026-10-07", "36", "", "", "", "group"]
        con.execute(f"INSERT INTO CLASS VALUES ({','.join('?' * len(vals))})", vals)
        con.execute("INSERT INTO SHIP_GROUPS VALUES ('Abkhazia', 'A-01 Class', 'https://www.navypedia.org/ships/abkhazia/abk_cf_1400m.htm', 0, 'A-01', 'x', "
                    "'1969-90 // 1992: A-01\n1983 // 2004: A-03')")
    con.commit()
    con.close()


@pytest.mark.parametrize("new", [True, False])
def test_extract_main_year_sources_on_both_db_formats_and_db_untouched(tmp_path, monkeypatch, new):
    import hashlib
    import json

    from tools import shipknow_extract as E

    db = tmp_path / ("new.db" if new else "old.db")
    _make_db(db, new)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    out = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["shipknow_extract", "--db", str(db), "--out-dir", str(out), "--no-split"])
    E.main()
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before  # read-only
    recs = {r["source_url"]: r for r in map(json.loads, (out / "records.jsonl").read_text(encoding="utf-8").splitlines())}
    yamato = recs["https://myownonpmirror.com/ships/japan/jap_bb_yamato.html"]
    kongo = recs["https://myownonpmirror.com/ships/japan/jap_bb_kongo.html"]
    report = json.loads((out / "extract_report.json").read_text(encoding="utf-8"))
    assert report["db_format"] == ("new" if new else "old")
    if new:
        assert (yamato["year"], yamato["year_source"]) == (1941, "class_when_date")  # the heading date, not the laid-down 1937
        assert (kongo["year"], kongo["year_source"]) == (1911, "ships_laid_launched")  # empty WHEN_DATE -> SHIPS
        assert yamato["year_commissioned"] == 1941 and kongo["year_commissioned"] == 1913
        grp = recs["https://myownonpmirror.com/ships/abkhazia/abk_cf_1400m.html"]
        assert (grp["year"], grp["year_source"], grp["ships_kind"]) == (1969, "ship_groups_completed", "group")
    else:
        assert (yamato["year"], yamato["year_source"]) == (1942, "class_name")
        assert (kongo["year"], kongo["year_source"]) == (1911, "ships_laid_launched")
        assert yamato["year_commissioned"] is None
