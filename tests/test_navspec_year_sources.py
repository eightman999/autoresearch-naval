"""navspec 側の下流読み手が、再取得した NSDB (CLASS.WHEN_DATE) と旧 NSDB (艦級名に年が混入) の両方で年を決められること。"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

import navspec_armament_mount_years as MY  # noqa: E402
import navspec_common as NC  # noqa: E402
import navspec_extract as NE  # noqa: E402

CLASS_COLS = ("CLASS_NAME TEXT, TYPE TEXT, COUNTRY TEXT, Displacement_normal TEXT, Displacement_full TEXT, Length TEXT, Breadth TEXT, "
              "Draught TEXT, No_of_shafts TEXT, Machinery TEXT, Power TEXT, speed TEXT, Fuel TEXT, Endurance TEXT, Armour TEXT, "
              "Armament TEXT, Complement TEXT, sections TEXT, source_url TEXT, fetched_at TEXT")
ARM = "3 x 3 - 460/45 Type 94"
URL_Y = "https://www.navypedia.org/ships/japan/jap_bb_yamato.htm"
URL_K = "https://www.navypedia.org/ships/japan/jap_bb_kongo.htm"


def _db(path: Path, new: bool) -> Path:
    con = sqlite3.connect(path)
    extra = ", WHEN_DATE TEXT" if new else ""
    con.execute(f"CREATE TABLE CLASS ({CLASS_COLS}{extra})")
    con.execute("CREATE TABLE SHIPS (NAME TEXT, CLASS TEXT, COUNTRY TEXT, Laid_down TEXT, Launched TEXT, WHEN_DATE TEXT, source_url TEXT)")
    for old, new_name, when, url, laid, launched in [
        ("YAMATO1942) Class", "YAMATO Class", "(1941 - 1942)", URL_Y, "4.11.1937", "8.1940"),
        ("KONGO Class", "KONGO Class", "", URL_K, "17.1.1911", "18.5.1912"),
    ]:
        vals = [new_name if new else old, "battleships", "Japan", "64000", "", "263 oa", "38.9", "10.4", "4", "", "150000", "27", "oil", "",
                "belt: 410", ARM, "2500", "", url, "2026-10-07"]
        if new:
            vals.append(when)
        con.execute(f"INSERT INTO CLASS VALUES ({','.join('?' * len(vals))})", vals)
        con.execute("INSERT INTO SHIPS VALUES ('X', ?, 'Japan', ?, ?, ?, ?)", (new_name if new else old, laid, launched, when if new else "", url))
    con.commit()
    con.close()
    return path


def test_parse_when_date_year():
    assert NC.parse_when_date_year("(1941 - 1942)") == 1941
    assert NC.parse_when_date_year("(1944/1947)") == 1944
    assert NC.parse_when_date_year("(2018-)") == 2018
    assert NC.parse_when_date_year("(1990s)") == 1990  # parse_year cannot read this
    assert NC.parse_year("(1990s)") is None
    assert NC.parse_when_date_year("") is None and NC.parse_when_date_year(None) is None


def test_parse_year_itself_is_unchanged():
    # the shared helper keeps its old behaviour (existing navspec data was built with it)
    assert NC.parse_year("YAMATO1942) Class", "x", None) is None
    assert NC.parse_year(None, "(1941 - 1942)") == 1941


@pytest.mark.parametrize("new", [True, False])
def test_navspec_extract_year(tmp_path, new):
    db = _db(tmp_path / "t.db", new)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    rows = NE.fetch_rows(db, None)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before  # opened read-only
    by_name = {r["CLASS_NAME"].split()[0].rstrip("0123456789)"): NE.make_record(r, i) for i, r in enumerate(rows)}
    yamato, kongo = by_name["YAMATO"], by_name["KONGO"]
    if new:
        assert yamato["year"] == 1941  # CLASS.WHEN_DATE, not the laid-down year
        assert kongo["year"] == 1911  # no WHEN_DATE -> the old fallback chain (SHIPS.Laid_down)
    else:
        # old DB: the year glued into the name is not seen by parse_year; the SHIPS.WHEN_DATE/Laid_down fallback decides (unchanged)
        assert yamato["year"] == 1937 and kongo["year"] == 1911


@pytest.mark.parametrize("new", [True, False])
def test_mount_years_load_nsdb_classes(tmp_path, new):
    db = _db(tmp_path / "t.db", new)
    classes = {c["class_name"].split()[0].rstrip("0123456789)"): c for c in MY.load_nsdb_classes(db)}
    if new:
        assert (classes["YAMATO"]["year"], classes["YAMATO"]["year_from"]) == (1941, "class_when_date")
        assert (classes["KONGO"]["year"], classes["KONGO"]["year_from"]) == (1912, "ship_launch")  # launch year, as in the old path
    else:
        assert (classes["YAMATO"]["year"], classes["YAMATO"]["year_from"]) == (1942, "class_name")
        assert (classes["KONGO"]["year"], classes["KONGO"]["year_from"]) == (1912, "ship_launch")
    assert classes["YAMATO"]["entries"][0]["caliber_mm"] == 460.0
