"""nsdb/fetcher.py のオフライン回帰テスト (ネットワーク・正本 DB には触れない)。

tests/fixtures/nsdb/ の HTML は 2026-10 の網羅性監査 (docs/nsdb_completeness_audit_2026-10-06.md) で取得した
現行サイト www.navypedia.org (live_*) と 2021 年 HTTrack ミラー (mirror_*) のページ。
監査で見つかった問題ごとに、修正後の期待値を固定する。
"""
import gzip
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nsdb"


def _load_fetcher():
    spec = importlib.util.spec_from_file_location("nsdb_fetcher_under_test", ROOT / "nsdb" / "fetcher.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nsdb_fetcher_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


F = _load_fetcher()
LIVE = "https://www.navypedia.org"


def raw(name):
    return gzip.decompress((FIXTURES / f"{name}.html.gz").read_bytes())


def text(name):
    return F.decode_html(raw(name))


def parse(name, path="ships/x/x_y.htm", country="X", site="live"):
    return F.parse_class_page(raw(name), f"{LIVE}/{path}", country, site=site)


@pytest.fixture(scope="module")
def yamato():
    return parse("live_japan_jap_bb_yamato", "ships/japan/jap_bb_yamato.htm", "Japan")


# ---------------------------------------------------------------------------
# 3. Armour 列
# ---------------------------------------------------------------------------

def test_armour_is_mapped(yamato):
    assert yamato["spec_cols"]["Armour"].startswith("belt: 410+ bulkheads: 300")
    # 装甲行を持つ他の艦級 (空欄ではない)
    for name in ("live_usa_us_cv_independence", "live_france_fr_bb_rochambeau"):
        assert parse(name)["spec_cols"].get("Armour"), name
    # 装甲行の無いページでは空
    assert "Armour" not in parse("live_india_in_cv_vikrant")["spec_cols"]


def test_spec_label_variants_map_to_armour():
    assert F.spec_column_for("Armour, mm") == "Armour"
    assert F.spec_column_for("Armor") == "Armour"
    assert F.spec_column_for("  armour , mm ") == "Armour"


# ---------------------------------------------------------------------------
# 4. 潜水艦の排水量・未定義ラベル
# ---------------------------------------------------------------------------

def test_submarine_displacement_standard_and_normal_kept():
    # jap_ss_kd4: 標準 1635 と 常備 1720 / 2300 の両方がある。従来は後勝ちで標準が消えた
    cols = parse("live_japan_jap_ss_kd4")["spec_cols"]
    assert cols["Displacement_standard"] == "1635"
    assert cols["Displacement_normal"] == "1720 / 2300"
    # 標準が元ページで空の潜水艦 (U57) は normal の値がそのまま残る
    u57 = parse("live_germany_ger_ss_u57")["spec_cols"]
    assert u57["Displacement_normal"] and not u57.get("Displacement_standard")


def test_surface_ship_displacement_normal_column_keeps_old_meaning(yamato):
    # 従来: standard しか無い艦は Displacement_normal に standard の値が入っていた (下流が読む)
    assert yamato["spec_cols"]["Displacement_standard"] == "63200"
    assert yamato["spec_cols"]["Displacement_normal"] == "63200"


def test_unmapped_labels_go_to_extra_specs_not_dropped():
    assert parse("live_japan_jap_ss_kd4")["extra_specs"]["Diving depth operational, m"] == "60"
    assert parse("live_indonesia_ino_ls_adrixlv")["extra_specs"]["Military load"]
    kfk = parse("live_germany_ger_mb_kfk2")
    assert kfk["extra_specs"]["Data variant"] == "as completed"
    # 列が複数ある技術表 (変種) の 2 列目以降も捨てない
    assert kfk["extra_specs"]["Data variant [col 3]"] == "Bundesmarine and Frontier Guard"
    assert kfk["extra_specs"]["Length, m [col 3]"] == "22.3"
    # 既知ラベルは extra_specs に重複して入らない
    assert "Length, m" not in kfk["extra_specs"]


# ---------------------------------------------------------------------------
# 5. テキスト節の空白・表形式の節
# ---------------------------------------------------------------------------

def test_text_sections_keep_spaces_at_inline_tag_boundaries(yamato):
    ns = yamato["sections"]["Naval service"]
    assert "Musashi 29.3.1944 was damaged by a torpedo from American submarine Tunny" in ns
    assert "submarineTunny" not in ns and "Musashi29.3.1944" not in ns
    assert "R-2 and R-92 were lost" in parse("live_russia_ru_cf_r141")["sections"]["Naval service"]
    assert "I61 1.10.1941" in parse("live_japan_jap_ss_kd4")["sections"]["Naval service"]


def test_text_sections_have_no_space_before_punctuation(yamato):
    ns = yamato["sections"]["Naval service"]
    assert "Tunny," in ns and "Tunny ," not in ns


def test_sections_are_complete_not_first_paragraph_only(yamato):
    ph = yamato["sections"]["Project history"]
    assert len(ph) > 3000
    assert "interchanged" not in ph and "Last three ships should differ" in ph  # 最終段落まで
    assert yamato["sections"]["Naval service"].count("\n") >= 2  # <br><br> は改行


def test_table_sections_are_table_text_not_the_next_sections_paragraph():
    vik = parse("live_india_in_cv_vikrant")["sections"]
    assert vik["Air group"].startswith("Year | Attackers | ASW planes | Helicopters")
    assert "1961 | 10 Sea Hawk" in vik["Air group"]
    assert "Flight deck" not in vik["Air group"] and "Flight deck" in vik["Aircraft facilities"]
    fi = parse("live_finland_fi_cm_pg2")["sections"]
    key = "100 - 1000 t displacement/ BRT tonnage vessels"
    assert fi[key].startswith("Aunus (ex-Kingston Emerald)")
    assert "Former trawlers" not in fi[key] and "Former trawlers" in fi["Project history"]


def test_numbered_section_titles_are_normalized(yamato):
    assert "Modernizations" in yamato["sections"] and not any(k.startswith("Modernizations ") for k in yamato["sections"])
    assert "Modernizations" in parse("live_japan_jap_ss_kd4")["sections"]
    assert "Ship protection" in yamato["sections"]


def test_html_comments_do_not_create_sections(yamato):
    # <!--<h2 ...>Menu</h2>--> がコメントで無効化されていること
    assert "Menu" not in yamato["sections"] and "Photo" not in yamato["sections"]
    assert set(yamato["sections"]) == {"Project history", "Ship protection", "Modernizations", "Naval service"}


# ---------------------------------------------------------------------------
# 6. SHIPS: 重複・群れ要約表・Comp
# ---------------------------------------------------------------------------

def test_ship_rows_are_not_duplicated_by_unclosed_tr():
    # 閉じ忘れ <tr> が 296 個ある 278+ 行の表。従来は 31,180 行に膨らんだ
    r = parse("live_russia_ru_cf_r141")
    assert len(r["ships"]) == 281
    assert parse("live_germany_ger_mb_kfk2")["ships"].__len__() == 709
    assert len(parse("live_china_ch_cf_2201")["ships"]) == 82
    # 完全重複した行は表に実在する 1 組だけ (nest 由来の連鎖重複が無い)
    seen, dups = set(), 0
    for s in r["ships"]:
        k = tuple(sorted((a, b) for a, b in s.items() if a != "_extra"))
        dups += k in seen
        seen.add(k)
    assert dups <= 1


def test_ship_rows_content_and_columns_by_header_name(yamato):
    ships = yamato["ships"]
    assert [s["NAME"] for s in ships] == ["大和 <Yamato>", "武蔵 <Musashi>", "信濃 <Shinano>", "第111号艦 <No111>"]
    assert ships[1]["Yard_No"] == "800" and ships[1]["Builder"] == "Mitsubishi- Nagasaki"
    assert ships[0]["Laid_down"] == "4.11.1937" and ships[0]["Launched"] == "8.8.1940"
    # 見出しが No, Name の順の表 (Comm 列) でも列がずれない
    ind = parse("live_usa_us_cv_independence")["ships"][0]
    assert ind["NAME"].startswith("Independence") and ind["NO"].startswith("CV22") and ind["Comp"] == "1.1.1943"
    # 列の多い表 (Modification) は Extra に残る
    assert parse("live_russia_ru_cf_r141")["ships"][0]["_extra"]["Modification"] == "Project 205"


def test_comp_column_is_kept(yamato):
    assert [s["Comp"] for s in yamato["ships"]] == ["16.12.1941", "5.8.1942", "---", "---"]
    assert all(s["Comp"] for s in parse("live_japan_jap_dd_hatsuyuki")["ships"])
    # 変則見出し (Launched // Comm.) も Launched と Comp に分ける
    lss = parse("live_uk_brit_aws_lss")["ships"][0]
    assert lss["Launched"] == "9/1917" and lss["Comp"] == "9.1940" and "Tonnage, BRT" in lss["_extra"]


@pytest.mark.parametrize("name", ["live_india_in_cg_h181", "live_uk_brit_c_f_mtb539"])
def test_group_summary_table_is_kept_separately(name):
    r = parse(name)
    assert r["ships_kind"] == "group" and r["ships"] == []
    g = r["ship_groups"]
    assert len(g) == 1 and g[0]["Names"] and g[0]["Builders"] and g[0]["Losses"]
    assert r["ships_headers"][:2] == ["Names", "Builders"]


def test_group_table_lines_are_preserved():
    g = parse("live_india_in_cg_h181")["ship_groups"][0]
    assert g["Completed"].split("\n")[:2] == ["2000: H181", "2001: H182-185"]


def test_fate_split_keeps_spaces():
    assert F.split_fate("completed as carrier 1944") == ("completed as carrier", "1944")
    assert F.split_fate("sunk 7.4.1945") == ("sunk", "7.4.1945")
    assert F.split_fate("BU incomplete 3.1942") == ("BU incomplete", "3.1942")
    assert F.split_fate("") == ("", "")


# ---------------------------------------------------------------------------
# 7. CLASS_NAME / TYPE / WHEN_DATE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("heading,expected", [
    ("YAMATO battleships (1941 - 1942)", ("YAMATO Class", "battleships", "(1941 - 1942)")),
    ("KFK2 ASW boats (1942 - 1945)", ("KFK2 ASW Class", "boats", "(1942 - 1945)")),
    ("TK-36 missile patrol craft (projects 205, 205U) (1960 - 1977)", ("TK-36 Class", "missile-patrol-craft", "(1960 - 1977)")),
    ("No2201 (project 022) fast attack craft (missile) (2004-2010)", ("No2201 (project 022) Class", "fast-attack-craft", "(2004-2010)")),
    ('"Kaidai 4" large submarines (I61) (1929 - 1930)', ("Kaidai 4 Class", "large-submarines", "(1929 - 1930)")),
    ("UTSIRA submarines (1944, 1943-1944/1946)", ("UTSIRA Class", "submarines", "(1944, 1943-1944/1946)")),
    ("ALMIRANTE PADILLA frigates (1944/1947-1953)", ("ALMIRANTE PADILLA Class", "frigates", "(1944/1947-1953)")),
    ("RAÏS HASSEN BARBIAR corvette (2017)", ("RAÏS HASSEN BARBIAR Class", "corvette", "(2017)")),
    ("PRIMEIRO DE MARÇO screw sloop (1881)", ("PRIMEIRO DE MARÇO Class", "screw-sloop", "(1881)")),
    ("gunboats of WWII", ("WWII Class", "gunboats", "")),
    ("requisitioned fishing vessels of WWII", ("WWII Class", "requisitioned-fishing-vessels", "")),
    ('"E" and "F" destroyers (ESCAPADE) (16, 1934 - 1935)', ('"E" and "F" Class', "destroyers", "(16, 1934 - 1935)")),
    ("ADMIRAL FLOTA SOVETSKOGO SOYUZA KUZNETSOV heavy aircraft carrying cruisers (projects 1143.5, 1143.6) (1990)",
     ("ADMIRAL FLOTA SOVETSKOGO SOYUZA KUZNETSOV Class", "heavy-aircraft-carrying-cruisers", "(1990)")),
    ("type 53A river patrol craft (1950-1955)", ("Type 53A Class", "river-patrol-craft", "(1950-1955)")),
    ("EMERALD 4th rate sailing frigates", ("EMERALD 4th Rate Class", "sailing-frigates", "")),
])
def test_split_class_heading(heading, expected):
    assert F.split_class_heading(heading) == expected


def test_class_name_has_no_broken_parentheses_on_any_fixture_page():
    names = []
    for p in FIXTURES.glob("live_*_*_*.html.gz"):
        r = F.parse_class_page(gzip.decompress(p.read_bytes()), f"{LIVE}/ships/x/y.htm", "X")
        if not r["heading"] or not r["class_name"]:
            continue
        names.append(r["class_name"])
        # 従来は `YAMATO1942) Class` のように括弧・年が混入した
        assert not any(ch in r["class_name"] for ch in "()") or r["class_name"].count("(") == r["class_name"].count(")"), r["class_name"]
        assert not F._YEAR_RE.search(r["class_name"]), r["class_name"]
        assert r["class_name"].endswith(" Class")
    assert len(names) >= 20


def test_when_date_is_kept_on_class_and_ships(yamato):
    assert yamato["class_name"] == "YAMATO Class" and yamato["ship_type"] == "battleships"
    assert yamato["when_date"] == "(1941 - 1942)"
    assert yamato["heading"] == "YAMATO battleships (1941 - 1942)"


# ---------------------------------------------------------------------------
# 2. 艦種リンクの網羅 (ラベル照合ではなく構造)
# ---------------------------------------------------------------------------

def test_country_index_has_181_countries_including_jamaica():
    countries = F.parse_country_links(text("live_index"))
    assert len(countries) == 181
    by_short = {c["short"]: c for c in countries}
    jam = by_short["Jamaica"]
    assert jam["url"] == f"{LIVE}/ships/jamaica/jam_cg.htm"  # `_index` でない特例
    # 下流が COUNTRY の値をキーにしているので、従来の短縮名を保つ
    assert {"United-Kingdom", "United-States", "Russian", "NewZealand", "Austria-Hungary", "North-Korea"} <= set(by_short)


def test_type_pages_are_found_structurally_without_label_matching():
    it = F.parse_index_links(text("live_italy_it_index"), f"{LIVE}/ships/italy/it_index.htm")
    names = {t["name"] for t in it}
    cg = [t for t in it if t["url"].endswith("/ships/italy/it_cg.htm")]
    assert cg, names
    assert "PARAMILITARY SERVICES" in cg[0]["name"]
    # (艦種ラベルの CSV は使わない。未知のラベルでも拾えることは e2e テスト `TOTALLY NEW LABEL` で確認)
    classes = F.parse_index_links(text("live_italy_it_cg"), f"{LIVE}/ships/italy/it_cg.htm", exclude=[f"{LIVE}/ships/italy/it_index.htm"])
    assert len(classes) == 72  # 従来すべて取りこぼしていた沿岸・準軍事の艦級
    assert all(c["url"].startswith(f"{LIVE}/ships/italy/") for c in classes)


def test_countries_whose_only_type_page_was_missed_are_found():
    types = F.parse_index_links(text("live_falklands_falk_index"), f"{LIVE}/ships/falklands/falk_index.htm")
    assert [t["url"] for t in types] == [f"{LIVE}/ships/falklands/falk_cg.htm"]
    classes = F.parse_index_links(text("live_falklands_falk_cg"), f"{LIVE}/ships/falklands/falk_cg.htm")
    assert len(classes) == 12


def test_jamaica_country_page_is_not_a_class():
    types = F.parse_index_links(text("live_jamaica_jam_cg"), f"{LIVE}/ships/jamaica/jam_cg.htm")
    assert [t["url"] for t in types] == [f"{LIVE}/ships/jamaica/jam_cgg.htm"]
    classes = F.parse_index_links(text("live_jamaica_jam_cgg"), f"{LIVE}/ships/jamaica/jam_cgg.htm",
                                  exclude=[f"{LIVE}/ships/jamaica/jam_cg.htm"])
    assert len(classes) == 7
    assert all(c["url"] != f"{LIVE}/ships/jamaica/jam_cg.htm" for c in classes)


def test_navigation_links_are_not_counted():
    jap = F.parse_index_links(text("live_japan_jap_index"), f"{LIVE}/ships/japan/jap_index.htm")
    assert len(jap) == 12 and all("_index" not in t["url"] for t in jap)
    bb = F.parse_index_links(text("live_japan_jap_battleships"), f"{LIVE}/ships/japan/jap_battleships.htm")
    assert all(c["url"].startswith(f"{LIVE}/ships/japan/jap_bb_") for c in bb) and len(bb) >= 10


def test_broken_file_hrefs_are_repaired_and_percent20_kept():
    sa = F.parse_index_links(text("live_sarawak_sa_of"), f"{LIVE}/ships/sarawak/sa_of.htm")
    assert len(sa) == 4 and all(c["url"].startswith(f"{LIVE}/ships/sarawak/sa_of_") and "file:" not in c["url"] for c in sa)
    assert F.normalize_url("file:///C:/seawar/ships/sarawak/sa_of_aden.htm") == f"{LIVE}/ships/sarawak/sa_of_aden.htm"
    assert F.normalize_url("baha_cf_yellow elder.htm", f"{LIVE}/ships/bahamas/baha_cf.htm") == f"{LIVE}/ships/bahamas/baha_cf_yellow%20elder.htm"
    assert F.normalize_url("../germany/ger_cf_x.htm", f"{LIVE}/ships/germany_east/ge_cf.htm") == f"{LIVE}/ships/germany/ger_cf_x.htm"


def test_url_normalization_and_matching_keys():
    live = f"{LIVE}/ships/uk/brit_cv1_furious.htm"
    mirror = "https://myownonpmirror.com/ships/uk/brit_cv1_furious.html"
    assert F.canon_key(live) == F.canon_key(mirror) == "/ships/uk/brit_cv1_furious.html"
    assert F.canon_key(f"{LIVE}/ships/bahamas/baha_cf_yellow%20elder.htm") == F.canon_key("https://myownonpmirror.com/ships/bahamas/baha_cf_yellow elder.html")
    assert F.normalize_url(mirror) == f"{LIVE}/ships/uk/brit_cv1_furious.html"  # 拡張子はそのまま (現行の UK は .html)
    assert F.mirror_url_for(live) == mirror
    assert F.NavypediaFetcher._url_variants(f"{LIVE}/ships/bahamas/baha_cf_yellow%20elder.htm") == [
        f"{LIVE}/ships/bahamas/baha_cf_yellow%2520elder.htm", f"{LIVE}/ships/bahamas/baha_cf_yellow%20elder.htm"]


# ---------------------------------------------------------------------------
# ミラー (縮退フォールバック) のページでも壊れずに主要項目が取れる
# ---------------------------------------------------------------------------

def test_mirror_pages_still_parse_core_fields():
    m = parse("mirror_uk_brit_cv1_furious", "ships/uk/brit_cv1_furious.htm", "United-Kingdom", site="mirror")
    assert m["class_name"] == "FURIOUS Class" and m["when_date"] == "(1917)"
    assert m["spec_cols"]["Length"] == "239.7" and m["spec_cols"]["Armour"].startswith("belt: 76")
    assert len(m["ships"]) == 1 and m["ships"][0]["NAME"] == "Furious" and m["ships"][0]["Comp"] == "7/1917"
    assert m["source_site"] == "mirror"
    y_live = parse("live_japan_jap_bb_yamato")
    y_mirror = parse("mirror_japan_jap_bb_yamato", site="mirror")
    for col in ("Displacement_full", "Length", "Breadth", "Machinery", "Armour", "Complement"):
        assert y_mirror["spec_cols"][col] == y_live["spec_cols"][col], col
    assert len(y_mirror["ships"]) == 4
    assert len(parse("mirror_russia_ru_cf_r141", site="mirror")["ships"]) >= 280


# ---------------------------------------------------------------------------
# DB 保存 (スキーマ・冪等・旧スキーマ互換)
# ---------------------------------------------------------------------------

def _fetcher(tmp_path, **kw):
    kw.setdefault("images", "skip")
    return F.NavypediaFetcher(db_path=tmp_path / "t.db", **kw)


def test_save_to_db_new_schema_and_idempotent(tmp_path, yamato):
    f = _fetcher(tmp_path)
    f.save_to_db(yamato, "Japan")
    f.save_to_db(yamato, "Japan")  # 再実行しても重複しない
    f.save_to_db(parse("live_india_in_cg_h181", "ships/india/in_cg_h181.htm", "India"), "India")
    con = sqlite3.connect(tmp_path / "t.db")
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM CLASS WHERE source_url LIKE '%jap_bb_yamato%'").fetchone()
    assert con.execute("SELECT COUNT(*) FROM CLASS").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM SHIPS WHERE source_url LIKE '%jap_bb_yamato%'").fetchone()[0] == 4
    # 既存列の名前と意味
    assert row["CLASS_NAME"] == "YAMATO Class" and row["TYPE"] == "battleships" and row["COUNTRY"] == "Japan"
    assert row["Armour"].startswith("belt: 410+") and row["Displacement_normal"] == "63200"
    assert row["Naval_service"].startswith("Musashi 29.3.1944")
    assert json.loads(row["sections"])["Ship protection"].startswith("20°inclined")
    # 追加列
    assert row["Displacement_standard"] == "63200" and row["WHEN_DATE"] == "(1941 - 1942)"
    assert row["HEADING"] == "YAMATO battleships (1941 - 1942)" and row["ships_kind"] == "individual"
    assert row["source_site"] == "live" and row["parser_version"] == F.PARSER_VERSION
    ships = con.execute("SELECT * FROM SHIPS WHERE source_url LIKE '%jap_bb_yamato%' ORDER BY seq").fetchall()
    assert ships[0]["NAME"] == "大和 <Yamato>" and ships[0]["Comp"] == "16.12.1941" and ships[0]["Fate"] == "sunk 7.4.1945"
    assert ships[0]["Fate_WHY"] == "sunk" and ships[0]["Fate_WHEN"] == "7.4.1945"
    assert ships[2]["Fate_WHY"] == "completed as carrier"
    assert ships[0]["WHEN_DATE"] == "(1941 - 1942)" and ships[0]["CLASS"] == "YAMATO Class" and ships[0]["COUNTRY"] == "Japan"
    # 群れ要約表は SHIPS ではなく SHIP_GROUPS
    assert con.execute("SELECT COUNT(*) FROM SHIPS WHERE source_url LIKE '%in_cg_h181%'").fetchone()[0] == 0
    g = con.execute("SELECT * FROM SHIP_GROUPS WHERE source_url LIKE '%in_cg_h181%'").fetchone()
    assert g["Names"] == "H181 - 198" and g["Date_label"] == "Completed" and "H181" in g["Completed"]
    assert row["ships_kind"] == "individual"
    assert con.execute("SELECT ships_kind FROM CLASS WHERE source_url LIKE '%in_cg_h181%'").fetchone()[0] == "group"
    # extra_specs は JSON
    f.save_to_db(parse("live_japan_jap_ss_kd4", "ships/japan/jap_ss_kd4.htm", "Japan"), "Japan")
    assert json.loads(con.execute("SELECT extra_specs FROM CLASS WHERE source_url LIKE '%kd4%'").fetchone()[0]) == {"Diving depth operational, m": "60"}
    con.close()


def test_downstream_queries_work_on_new_schema(tmp_path, yamato):
    """tools/navspec_extract.py / shipknow_extract.py が使う列・サブクエリがそのまま動く。"""
    f = _fetcher(tmp_path)
    f.save_to_db(yamato, "Japan")
    con = sqlite3.connect(tmp_path / "t.db")
    con.row_factory = sqlite3.Row
    r = con.execute("""
        SELECT CLASS_NAME, TYPE, COUNTRY, Displacement_normal, Displacement_full, Length, Breadth, Draught, No_of_shafts,
               Machinery, Power, speed, Fuel, Endurance, Armour, Armament, Electronic_equipment, Complement, sections,
               source_url, fetched_at,
               (SELECT WHEN_DATE FROM SHIPS s WHERE s.source_url = CLASS.source_url LIMIT 1) AS when_date,
               (SELECT Laid_down FROM SHIPS s WHERE s.source_url = CLASS.source_url LIMIT 1) AS laid_down
        FROM CLASS ORDER BY rowid""").fetchone()
    assert r["when_date"] == "(1941 - 1942)" and r["laid_down"] == "4.11.1937"
    rows = con.execute("SELECT source_url, Laid_down, Launched, WHEN_DATE FROM SHIPS").fetchall()
    assert len(rows) == 4
    con.close()


def test_old_schema_db_is_not_altered(tmp_path, yamato):
    """旧スキーマの DB (正本と同じ列構成) に対しては、列も表も追加せず、存在する列だけを書く。"""
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE COUNTRIES (Short TEXT, Long TEXT, URL TEXT)")
    con.execute("CREATE TABLE CLASS (Photo TEXT, CLASS_NAME TEXT, TYPE TEXT, COUNTRY TEXT, Displacement_normal TEXT, "
                "Displacement_full TEXT, Length TEXT, Breadth TEXT, Draught TEXT, No_of_shafts TEXT, Machinery TEXT, Power TEXT, "
                "speed TEXT, Fuel TEXT, Endurance TEXT, Armour TEXT, Armament TEXT, Electronic_equipment TEXT, Complement TEXT, "
                "images TEXT, Graphics TEXT, Project_history TEXT, Modernizations TEXT, Naval_service TEXT, sections TEXT, "
                "source_url TEXT, fetched_at TEXT)")
    con.execute("CREATE TABLE SHIPS (NAME TEXT, CLASS TEXT, COUNTRY TEXT, NO TEXT, Yard_No TEXT, Builder TEXT, Laid_down TEXT, "
                "Launched TEXT, Fate_WHY TEXT, Fate_WHEN TEXT, TYPE TEXT, SID TEXT, WHEN_DATE TEXT, source_url TEXT)")
    con.commit()
    con.close()
    f = F.NavypediaFetcher(db_path=db, images="skip")
    assert f.migrate is False
    f.save_to_db(yamato, "Japan")
    con = sqlite3.connect(db)
    assert [r[1] for r in con.execute("PRAGMA table_info(CLASS)")][-1] == "fetched_at"
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "SHIP_GROUPS" not in names and "FETCH_ERRORS" not in names
    assert con.execute("SELECT Armour FROM CLASS").fetchone()[0].startswith("belt: 410")
    assert con.execute("SELECT COUNT(*) FROM SHIPS").fetchone()[0] == 4
    con.close()


def test_migrate_adds_columns_to_existing_db(tmp_path, yamato):
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE CLASS (CLASS_NAME TEXT, source_url TEXT)")
    con.execute("CREATE TABLE SHIPS (NAME TEXT, source_url TEXT)")
    con.commit()
    con.close()
    F.NavypediaFetcher(db_path=db, images="skip", migrate=True)
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(CLASS)")}
    assert {"Displacement_standard", "WHEN_DATE", "extra_specs", "HEADING"} <= cols
    assert {"Comp", "Fate", "Extra", "seq"} <= {r[1] for r in con.execute("PRAGMA table_info(SHIPS)")}
    con.close()


# ---------------------------------------------------------------------------
# 画像: 旧 DB (ミラー形式 URL を含む) から source_url 単位でコピー
# ---------------------------------------------------------------------------

def _legacy_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE CLASS (Photo TEXT, images TEXT, Graphics TEXT, source_url TEXT)")
    con.execute("INSERT INTO CLASS VALUES ('PHOTO_B64', 'IMG_B64', 'GFX_B64', 'https://www.navypedia.org/ships/japan/jap_bb_yamato.htm')")
    # ミラー形式 (.html + ミラーのホスト) の行。現行行が無い艦級は『旧 DB に無い』扱い
    con.execute("INSERT INTO CLASS VALUES ('', '', '', 'https://myownonpmirror.com/ships/japan/jap_ss_kd4.html')")
    con.execute("INSERT INTO CLASS VALUES ('M_PHOTO', '', '', 'https://myownonpmirror.com/ships/japan/jap_bb_yamato.html')")
    con.commit()
    con.close()


class _NoNetwork:
    def get(self, *a, **k):
        raise AssertionError("ネットワークに出てはいけない")


def test_images_are_copied_from_legacy_db_by_canonical_url(tmp_path, yamato):
    legacy = tmp_path / "legacy.db"
    _legacy_db(legacy)
    src = F.LegacyImageSource(legacy)
    assert src.has_live(f"{LIVE}/ships/japan/jap_bb_yamato.htm")
    assert src.has_any("https://myownonpmirror.com/ships/japan/jap_ss_kd4.html") and not src.has_live(f"{LIVE}/ships/japan/jap_ss_kd4.htm")
    assert src.images_for(f"{LIVE}/ships/japan/jap_bb_yamato.htm") == {"photo": "PHOTO_B64", "images": "IMG_B64", "graphics": "GFX_B64"}
    src.close()

    f = _fetcher(tmp_path, images="copy", images_from=legacy, images_missing="skip")
    f.session = _NoNetwork()
    detail = dict(yamato)
    f._attach_images(detail)
    assert (detail["photo_base64"], detail["images_base64"], detail["graphics_base64"]) == ("PHOTO_B64", "IMG_B64", "GFX_B64")
    assert f.stats["images_copied"] == 1 and f.stats["images_fetched"] == 0


def test_images_are_fetched_only_for_classes_missing_from_legacy_db(tmp_path):
    legacy = tmp_path / "legacy.db"
    _legacy_db(legacy)
    f = _fetcher(tmp_path, images="copy", images_from=legacy, rate=1.0)
    f.limiter.interval = 0.0
    calls = []

    class Resp:
        status_code = 200
        content = b"\x89PNG-bytes"
        headers = {}

    class Sess:
        def get(self, url, timeout=None):
            calls.append(url)
            return Resp()

    f.session = Sess()
    kd4 = parse("live_japan_jap_ss_kd4", "ships/japan/jap_ss_kd4.htm", "Japan")  # 旧 DB はミラー行だけ → 取得対象
    f._attach_images(kd4)
    assert len(calls) == 3 and all(u.startswith(f"{LIVE}/storage/images/") for u in calls)
    assert kd4["photo_base64"] and f.stats["images_fetched"] == 3
    calls.clear()
    f._attach_images(parse("live_japan_jap_bb_yamato", "ships/japan/jap_bb_yamato.htm", "Japan"))  # 旧 DB にある → コピーのみ
    assert calls == [] and f.stats["images_copied"] == 1


# ---------------------------------------------------------------------------
# 礼儀正しい取得: レート制限・robots・ロック・キャッシュ・再開
# ---------------------------------------------------------------------------

def test_rate_limiter_spaces_requests():
    lim = F.RateLimiter(rate=20.0)  # 0.05s
    t0 = time.monotonic()
    for _ in range(5):
        lim.wait("h")
    assert time.monotonic() - t0 >= 0.04 * 4
    # ホストごとに独立
    t1 = time.monotonic()
    lim.wait("other")
    assert time.monotonic() - t1 < 0.04


def test_cli_rejects_rate_above_one_and_default_is_below_one(tmp_path):
    assert F.DEFAULT_RATE <= 1.0
    with pytest.raises(SystemExit):
        F.main(["--db", str(tmp_path / "x.db"), "--rate", "2", "index"])
    p = F.build_parser().parse_args(["--db", "x.db", "all"])
    assert p.workers <= 2 and p.rate == F.DEFAULT_RATE and p.images == "auto"
    assert "personal research" in F.USER_AGENT and "@" not in F.USER_AGENT


def test_cli_refuses_canonical_db_without_flag(tmp_path, monkeypatch):
    fake = tmp_path / "NSDB.db"
    fake.write_bytes(b"x")
    monkeypatch.setattr(F, "DEFAULT_DB_PATH", fake)
    assert F.main(["--db", str(fake), "--offline", "--no-cache", "index"]) == 2


def test_robots_parse():
    ok, delay = F.robots_allows("User-agent: *\nDisallow:\n", f"{LIVE}/ships/x.htm")
    assert ok and delay is None
    ok, _ = F.robots_allows("User-agent: *\nDisallow: /ships/\n", f"{LIVE}/ships/x.htm")
    assert not ok
    ok, delay = F.robots_allows("User-agent: *\nCrawl-delay: 5\nDisallow:\n", f"{LIVE}/ships/x.htm")
    assert ok and delay == 5


def test_run_lock_blocks_second_instance(tmp_path):
    db = tmp_path / "a.db"
    with F.RunLock(db):
        with pytest.raises(RuntimeError):
            F.RunLock(db).acquire()
    F.RunLock(db).acquire().release()  # 解放後は取れる


def test_html_cache_roundtrip_and_offline_mode(tmp_path):
    c = F.HtmlCache(tmp_path / "cache")
    url = f"{LIVE}/ships/japan/jap_bb_yamato.htm"
    assert c.get(url) is None
    c.put(url, 200, b"<html>hello</html>")
    c.put(f"{LIVE}/ships/x/gone.htm", 404, b"")
    assert c.get(url) == (200, b"<html>hello</html>")
    assert c.get(f"{LIVE}/ships/x/gone.htm") == (404, b"")
    f = _fetcher(tmp_path, cache_dir=tmp_path / "cache", offline=True)
    assert f._http(url) == (200, b"<html>hello</html>")
    with pytest.raises(F.OfflineMiss):
        f._http(f"{LIVE}/ships/not/cached.htm")


class _Resp:
    def __init__(self, status, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}


class _FakeSession:
    """URL → (status, bytes) の辞書で応答する偽セッション。呼ばれた URL を記録する。"""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        if url not in self.table:
            return _Resp(404)
        status, content = self.table[url]
        return _Resp(status, content)


def _fast(f, session):
    f.session = session
    f.limiter.interval = 0.0
    return f


def test_percent20_is_requested_double_encoded_first(tmp_path):
    base = f"{LIVE}/ships/bahamas/baha_cf_yellow%20elder.htm"
    page = raw("live_japan_jap_ss_kd4")
    sess = _FakeSession({base.replace("%20", "%2520"): (200, page)})
    f = _fast(_fetcher(tmp_path), sess)
    status, body, site = f._get_html(base)
    assert (status, site) == (200, "live") and sess.calls == [base.replace("%20", "%2520")]


def test_mirror_fallback_only_on_server_error(tmp_path):
    url = f"{LIVE}/ships/uk/brit_cv1_furious.htm"
    mirror = F.mirror_url_for(url)
    sess = _FakeSession({url: (500, b""), mirror: (200, raw("mirror_uk_brit_cv1_furious"))})
    f = _fast(_fetcher(tmp_path, retries=2), sess)
    status, body, site = f._get_html(url)
    assert (status, site) == (200, "mirror") and f.stats["mirror_fallbacks"] == 1
    assert sess.calls.count(url) == 2 and sess.calls[-1] == mirror
    # 404 のときはミラーを見に行かない
    sess2 = _FakeSession({mirror: (200, b"x")})
    f2 = _fast(_fetcher(tmp_path / "d2" if False else tmp_path, retries=2), sess2)
    f2.cache = None
    status, _, site = f2._get_html(f"{LIVE}/ships/uk/none.htm")
    assert status == 404 and mirror_not_called(sess2)


def mirror_not_called(sess):
    return not any("myownonpmirror" in u for u in sess.calls)


def test_offline_rebuild_uses_cached_mirror_page_for_classes_that_were_5xx_live(tmp_path):
    url = f"{LIVE}/ships/uk/brit_cv1_furious.htm"
    cache = F.HtmlCache(tmp_path / "cache")
    cache.put(F.mirror_url_for(url), 200, raw("mirror_uk_brit_cv1_furious"))
    f = _fetcher(tmp_path, cache_dir=tmp_path / "cache", offline=True)
    detail = f.fetch_class_detail(url, "United-Kingdom")
    assert detail["source_site"] == "mirror" and detail["class_name"] == "FURIOUS Class" and detail["spec_cols"]["Armour"]
    f.mirror_fallback = False
    with pytest.raises(F.OfflineMiss):
        f._get_html(url)


def test_blocking_stops_without_retrying(tmp_path):
    sess = _FakeSession({f"{LIVE}/ships/x.htm": (403, b"blocked")})
    f = _fast(_fetcher(tmp_path), sess)
    with pytest.raises(F.StopFetch):
        f._http(f"{LIVE}/ships/x.htm")
    assert len(sess.calls) == 1
    sess2 = _FakeSession({f"{LIVE}/ships/y.htm": (200, b"<title>Just a moment...</title>")})
    f2 = _fast(_fetcher(tmp_path), sess2)
    with pytest.raises(F.StopFetch):
        f2._http(f"{LIVE}/ships/y.htm")


def test_max_requests_limit(tmp_path):
    sess = _FakeSession({f"{LIVE}/ships/{i}.htm": (200, b"<html></html>") for i in range(5)})
    f = _fast(_fetcher(tmp_path, max_requests=2), sess)
    f._http(f"{LIVE}/ships/0.htm")
    f._http(f"{LIVE}/ships/1.htm")
    with pytest.raises(F.StopFetch):
        f._http(f"{LIVE}/ships/2.htm")


def _site_table():
    """index → 国 X → 艦種ページ (ラベル不問) → 艦級 3 件 (うち 1 件は 404) の小さなサイト。"""
    def page(body):
        return ("<html><body><div id='bio'><div><a class='blacks2' href='/'>HOME</a>"
                "<a class='blacks2' href='https://www.navypedia.org/ships_index.htm'>FIGHTING SHIPS</a></div>"
                f"<p>{body}</p></div></body></html>").encode()
    t = {
        f"{LIVE}/ships_index.htm": (200, page(
            '<a class="blacks" href="ships/japan/jap_index.htm"><img src="https://www.navypedia.org/storage/images/flags/Japan.png"> JAPAN</a><br>')),
        f"{LIVE}/ships/japan/jap_index.htm": (200, page(
            '<a class="blacks" href="/ships/japan/jap_zz.htm">1. TOTALLY NEW LABEL</a>'
            '<a class="blacks2" href="/ships/japan/jap_index.htm">JAPAN</a>')),
        f"{LIVE}/ships/japan/jap_zz.htm": (200, page(
            '<a class="blacks2" href="/ships/japan/jap_index.htm">JAPAN</a>'
            '<a class="blacks" href="/ships/japan/jap_bb_yamato.htm"><b>1.</b> YAMATO battleships (1941 - 1942)</a>'
            '<a class="blacks" href="/ships/japan/jap_ss_kd4.htm"><b>2.</b> Kaidai 4</a>'
            '<a class="blacks" href="/ships/japan/jap_gone.htm"><b>3.</b> GONE</a>')),
        f"{LIVE}/ships/japan/jap_bb_yamato.htm": (200, raw("live_japan_jap_bb_yamato")),
        f"{LIVE}/ships/japan/jap_ss_kd4.htm": (200, raw("live_japan_jap_ss_kd4")),
    }
    return t


def test_end_to_end_all_countries_with_resume_and_error_record(tmp_path):
    sess = _FakeSession(_site_table())
    f = _fast(_fetcher(tmp_path, cache_dir=tmp_path / "cache"), sess)
    assert f.fetch_all_countries() is True
    con = sqlite3.connect(tmp_path / "t.db")
    assert [tuple(r) for r in con.execute("SELECT Short, Long, URL FROM COUNTRIES")] == [("Japan", "JAPAN", f"{LIVE}/ships/japan/jap_index.htm")]
    assert sorted(r[0] for r in con.execute("SELECT CLASS_NAME FROM CLASS")) == ["Kaidai 4 Class", "YAMATO Class"]
    assert con.execute("SELECT DISTINCT COUNTRY FROM CLASS").fetchall() == [("Japan",)]
    err = con.execute("SELECT url, kind, status FROM FETCH_ERRORS").fetchall()
    assert err == [(f"{LIVE}/ships/japan/jap_gone.htm", "class", 404)]
    st = dict(con.execute("SELECT key, value FROM FETCH_STATE").fetchall())
    assert st["status"] == "complete" and st["total_expected"] == "3"
    con.close()
    # 再実行: 取得済みはスキップ。404 だけは再試行するがキャッシュ (404 マーカー) からで通信なし
    sess2 = _FakeSession(_site_table())
    f2 = _fast(_fetcher(tmp_path, cache_dir=tmp_path / "cache"), sess2)
    f2.fetch_all_countries()
    assert sess2.calls == []
    assert f2.stats["classes_skipped"] == 2


def test_resume_after_partial_run_fetches_only_missing(tmp_path):
    sess = _FakeSession(_site_table())
    f = _fast(_fetcher(tmp_path), sess)  # キャッシュ無し
    items = f.discover_country(f"{LIVE}/ships/japan/jap_index.htm")
    assert [i["url"].rsplit("/", 1)[1] for i in items] == ["jap_bb_yamato.htm", "jap_ss_kd4.htm", "jap_gone.htm"]
    f.process_classes(items[:1], "Japan")  # 1 件だけ取得して「中断」
    sess.calls.clear()
    f.process_classes(items, "Japan")
    assert f"{LIVE}/ships/japan/jap_bb_yamato.htm" not in sess.calls
    assert f"{LIVE}/ships/japan/jap_ss_kd4.htm" in sess.calls


def test_offline_rebuild_from_cache_only(tmp_path):
    cache = tmp_path / "cache"
    f = _fast(_fetcher(tmp_path, cache_dir=cache), _FakeSession(_site_table()))
    f.fetch_all_countries()
    # 別 DB を、キャッシュだけで (ネットワーク無しで) 作り直す
    f2 = F.NavypediaFetcher(db_path=tmp_path / "rebuilt.db", cache_dir=cache, offline=True, images="skip")
    f2.session = _NoNetwork()
    assert f2.fetch_all_countries() is True
    con = sqlite3.connect(tmp_path / "rebuilt.db")
    assert con.execute("SELECT COUNT(*) FROM CLASS").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM SHIPS").fetchone()[0] == 7
    con.close()
