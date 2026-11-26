"""NSDB CLASS -> normalised shipknow records (JSONL).

    uv run python tools/shipknow_extract.py            # records + type_map + splits
    uv run python tools/shipknow_extract.py --limit 200   # smoke test

Reads ``nsdb/NSDB.db`` strictly read-only (SQLite ``mode=ro``).  Output goes to
``data/navspec/shipknow_v0/`` by default (records.jsonl, type_map.json, extract_report.json);
pass ``--out-dir`` for a new version.  Splitting is done by ``shipknow_split.py`` (called at
the end unless ``--no-split``).

Two NSDB formats are supported (detected from the columns that exist):

* old (``NSDB.orig-20260624.db``): the year is glued into ``CLASS_NAME`` (``YAMATO1942) Class``);
  year order = class_name -> SHIPS laid/launched -> SHIPS.WHEN_DATE.
* new (re-fetched from navypedia.org): ``CLASS_NAME`` carries no year, ``CLASS.WHEN_DATE`` holds the
  heading date.  Year order = CLASS.WHEN_DATE -> SHIPS laid/launched -> SHIPS.WHEN_DATE ->
  SHIP_GROUPS.Completed (group-summary classes have no SHIPS rows).  New columns are read when
  present: Armour, Displacement_standard, extra_specs (diving depth), SHIPS.Comp.

Two years per record (v0_2):

* ``year``           = when the class was designed / built: the first year of the heading date ``WHEN_DATE``
                       (``(1970s/1999)`` -> 1970, ``(1941 - 1942)`` -> 1941); else the earliest SHIPS laid/launched year;
                       else SHIPS.WHEN_DATE; else SHIP_GROUPS.Completed; (a heading that only has a ``/B`` part: B; then year_in_navy).
* ``year_in_navy``   = when the class entered *this* country's navy.  Heading ``A/B`` (transfer / purchase) -> B
                       (``(1970s/1999)`` -> 1999).  Otherwise the earliest SHIPS.Comp year, then SHIP_GROUPS.Completed,
                       then the earliest SHIPS.Launched year (never earlier than ``year``), and finally ``year`` itself.
  ``year_in_navy_source`` names the winner (``when_date_b`` / ``when_date_b_decade`` / ``ships_comp`` /
  ``ship_groups_completed`` / ``ships_launched`` / ``year_fallback``).
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shipknow_parse as P  # noqa: E402
import shipknow_types as T  # noqa: E402

try:  # reuse the shared helper from the navspec toolchain (read-only import)
    from navspec_common import normalize_source_url  # noqa: E402
except Exception:  # pragma: no cover - fallback if the toolchain import fails

    def normalize_source_url(url: str | None) -> str | None:  # type: ignore[misc]
        if not url:
            return None
        url = url.strip().replace("https://www.navypedia.org/", "https://myownonpmirror.com/")
        return url[:-4] + ".html" if url.endswith(".htm") else url


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "nsdb" / "NSDB.db"
DEFAULT_OUT = ROOT / "data" / "navspec" / "shipknow_v0"

# ----------------------------------------------------------------------------
# value sanity ranges: value outside -> set to None and flag "<field>_outlier"
# (decided from the measured distributions, see docs/shipknow_v0/README.md)
# ----------------------------------------------------------------------------
RANGES: dict[str, tuple[float, float]] = {
    "displacement_normal_t": (1.0, 120_000.0),
    "displacement_full_t": (1.0, 150_000.0),
    "displacement_standard_t": (1.0, 120_000.0),
    "diving_depth_m": (3.0, 1500.0),
    "length_m": (3.0, 400.0),
    "beam_m": (0.8, 80.0),
    "draught_m": (0.1, 20.0),
    "power_hp": (5.0, 500_000.0),
    "speed_kn": (2.0, 75.0),
    "complement": (1.0, 8000.0),
    "endurance_nm": (10.0, 60_000.0),
}

YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20[0-3]\d)\b")


def years_in(text: str | None) -> list[int]:
    return [int(y) for y in YEAR_RE.findall(text or "")]


def load_ship_years(conn: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    """normalised source_url -> (min year, source) from SHIPS Laid_down/Launched (fallback WHEN_DATE)."""
    best: dict[str, tuple[int, str]] = {}
    for url, laid, launched, when in conn.execute("SELECT source_url, Laid_down, Launched, WHEN_DATE FROM SHIPS"):
        key = normalize_source_url(url)
        if not key:
            continue
        ys = years_in(laid) + years_in(launched)
        src = "ships_laid_launched"
        if not ys:
            w = years_in(when)
            ys, src = (w[:1], "ships_when") if w else ([], "")
        if not ys:
            continue
        y = min(ys)
        cur = best.get(key)
        # an explicit laid/launched year beats a WHEN_DATE-only year
        if cur is None or (src == "ships_laid_launched" and cur[1] == "ships_when") or (src == cur[1] and y < cur[0]):
            best[key] = (y, src)
    return best


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def load_ship_comp_years(conn: sqlite3.Connection) -> dict[str, int]:
    """normalised source_url -> earliest completion year from SHIPS.Comp (new DB only; {} otherwise)."""
    if "Comp" not in _table_columns(conn, "SHIPS"):
        return {}
    best: dict[str, int] = {}
    for url, comp in conn.execute("SELECT source_url, Comp FROM SHIPS WHERE Comp IS NOT NULL AND Comp != ''"):
        key = normalize_source_url(url)
        y = P.parse_comp_year(comp)
        if key and y is not None and (key not in best or y < best[key]):
            best[key] = y
    return best


def load_ship_launched_years(conn: sqlite3.Connection) -> dict[str, int]:
    """normalised source_url -> earliest SHIPS.Launched year (new DB only; used as a late fallback for ``year_in_navy``)."""
    best: dict[str, int] = {}
    if "Comp" not in _table_columns(conn, "SHIPS"):
        return best
    for url, launched in conn.execute("SELECT source_url, Launched FROM SHIPS WHERE Launched IS NOT NULL AND Launched != ''"):
        key = normalize_source_url(url)
        ys = years_in(launched)
        if key and ys and (key not in best or min(ys) < best[key]):
            best[key] = min(ys)
    return best


def load_group_years(conn: sqlite3.Connection) -> dict[str, int]:
    """normalised source_url -> earliest completion year from SHIP_GROUPS.Completed (new DB only; {} otherwise).

    Group-summary classes (``ships_kind='group'``) have no SHIPS rows; their per-batch dates live here.
    """
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "SHIP_GROUPS" not in tables:
        return {}
    best: dict[str, int] = {}
    for url, completed in conn.execute("SELECT source_url, Completed FROM SHIP_GROUPS WHERE Completed IS NOT NULL AND Completed != ''"):
        key = normalize_source_url(url)
        y = P.completed_year(completed)
        if key and y is not None and (key not in best or y < best[key]):
            best[key] = y
    return best


def _val(row: sqlite3.Row, key: str) -> Any:
    """row[key] if the column exists (old-format DB rows lack the new columns), else None."""
    return row[key] if key in row.keys() else None


def decide_year(row: sqlite3.Row, url: str | None, ship_years: dict[str, tuple[int, str]],
                group_years: dict[str, int] | None = None) -> tuple[int | None, str | None]:
    """The class's design / build year and where it came from.

    New format (the row has a ``WHEN_DATE`` column): the first year of the heading date (the ``A`` of ``A/B``;
    see ``shipknow_parse.parse_when_date_parts``) -> SHIPS laid/launched -> SHIPS.WHEN_DATE -> SHIP_GROUPS.Completed
    -> (a heading with only ``/B``) B -> the year_in_navy below (``year_in_navy_fallback``).  ``CLASS_NAME`` is not consulted: it carries no year any more, and a digit run
    that survives in a name is not a date.
    Old format: class_name -> SHIPS laid/launched -> SHIPS.WHEN_DATE (unchanged).
    """
    parts = None
    if "WHEN_DATE" in row.keys():
        parts = P.parse_when_date_parts(row["WHEN_DATE"])
        if parts["year"] is not None:
            return parts["year"], "class_when_date" if parts["year_kind"] == "exact" else "class_when_date_decade"
    else:
        cy, _ = P.parse_class_year(row["CLASS_NAME"])
        if cy is not None:
            return cy, "class_name"
    if url and url in ship_years:
        return ship_years[url]
    if url and group_years and url in group_years:
        return group_years[url], "ship_groups_completed"
    if parts and parts["navy_year"] is not None:
        return parts["navy_year"], "class_when_date_b_only"
    return None, None


def decide_year_in_navy(row: sqlite3.Row, url: str | None, year: int | None, comp_years: dict[str, int] | None = None,
                        group_years: dict[str, int] | None = None,
                        launched_years: dict[str, int] | None = None) -> tuple[int | None, str | None]:
    """The year the class entered this country's navy, and the source (see the module docstring).

    1. heading ``A/B`` with a year in B -> B (a transfer / purchase: ``(1970s/1999)`` -> 1999);
    2. SHIPS.Comp (earliest completion) -> 3. SHIP_GROUPS.Completed -> 4. SHIPS.Launched, accepted only when it is not
       earlier than ``year`` (a launch before the build era would make the ship enter service before it was built);
    5. ``year`` itself (``year_fallback``); ``(None, None)`` when there is no year at all.
    """
    if "WHEN_DATE" in row.keys():
        parts = P.parse_when_date_parts(row["WHEN_DATE"])
        if parts["navy_year"] is not None:
            return parts["navy_year"], "when_date_b" if parts["navy_kind"] == "exact" else "when_date_b_decade"
    for table, src in ((comp_years, "ships_comp"), (group_years, "ship_groups_completed"), (launched_years, "ships_launched")):
        if url and table and url in table and (year is None or table[url] >= year or src != "ships_launched"):
            return table[url], src
    if year is not None:
        return year, "year_fallback"
    return None, None


def _set(rec: dict[str, Any], field: str, value: float | None, flags: list[str]) -> None:
    lo, hi = RANGES[field]
    if value is not None and not (lo <= value <= hi):
        flags.append(f"{field}_outlier:{value:g}")
        value = None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    rec[field] = value


def build_record(row: sqlite3.Row, ship_years: dict[str, tuple[int, str]], group_years: dict[str, int] | None = None,
                 comp_years: dict[str, int] | None = None, launched_years: dict[str, int] | None = None) -> dict[str, Any]:
    flags: list[str] = []
    variants: dict[str, str] = {}
    multi: list[str] = []

    def note(field: str, label: str | None, was_multi: bool) -> None:
        if was_multi:
            multi.append(field)
            if label:
                variants[field] = label[:60]

    url = normalize_source_url(row["source_url"])
    year, year_source = decide_year(row, url, ship_years, group_years)
    year_in_navy, year_in_navy_source = decide_year_in_navy(row, url, year, comp_years, group_years, launched_years)
    if year is None and year_in_navy is not None:  # last resort: no build-era evidence at all, but the service year is known
        year, year_source = year_in_navy, "year_in_navy_fallback"

    rec: dict[str, Any] = {
        "id": f"c{row['rowid']:05d}",
        "class_name": row["CLASS_NAME"],
        "class_base": P.class_base_name(row["CLASS_NAME"]),
        "country": row["COUNTRY"],
        "source_url": url,
        "year": year,
        "year_source": year_source,
        "year_in_navy": year_in_navy,
        "year_in_navy_source": year_in_navy_source,
        "type_raw": row["TYPE"] or "",
    }
    # --- columns that exist only in the re-fetched DB (None when absent); the year columns are informational ---
    ship_y = ship_years.get(url) if url else None
    rec["when_date_raw"] = _val(row, "WHEN_DATE") or None
    rec["year_laid_launched"] = ship_y[0] if ship_y and ship_y[1] == "ships_laid_launched" else None
    comp = (comp_years or {}).get(url) if url else None
    if comp is None and url and group_years:
        comp = group_years.get(url)
    rec["year_commissioned"] = comp
    rec["ships_kind"] = _val(row, "ships_kind") or None
    t = T.classify(row["TYPE"], row["source_url"], year, row["CLASS_NAME"])
    rec["ship_type_fine"] = t["fine"]
    rec["ship_type"] = t["coarse"]
    rec["type_source"] = t["type_source"]
    rec["type_rule"] = t["type_rule"]

    d = P.parse_displacement(row["Displacement_normal"])
    note("displacement_normal", d["label"], d["multi"])
    flags += d["flags"]
    _set(rec, "displacement_normal_t", d["value"], flags)
    df = P.parse_displacement(row["Displacement_full"])
    note("displacement_full", df["label"], df["multi"])
    flags += [f"full:{x}" for x in df["flags"]]
    _set(rec, "displacement_full_t", df["value"], flags)
    sub = d["submerged"] or df["submerged"]
    rec["displacement_submerged_t"] = sub if sub and 1 <= sub <= 200_000 else None
    ds = P.parse_displacement_standard(_val(row, "Displacement_standard"))
    flags += [f"standard:{x}" for x in ds["flags"]]
    _set(rec, "displacement_standard_t", ds["value"], flags)
    dd, _, _ = P.parse_diving_depth_m(_val(row, "extra_specs"))
    _set(rec, "diving_depth_m", dd, flags)
    arm_s = P.parse_armour(_val(row, "Armour"))
    rec["armour_raw"] = _val(row, "Armour") or ""
    rec["armour_material"] = arm_s["material"]
    rec["armour_parts"] = arm_s["parts"] or None
    for part, field in (("belt", "armour_belt_mm"), ("deck", "armour_deck_mm"), ("turret", "armour_turret_mm"), ("conning_tower", "armour_ct_mm")):
        rec[field] = P.armour_max(arm_s["parts"], part)

    v, lab, mu = P.parse_length_m(row["Length"])
    note("length", lab, mu)
    _set(rec, "length_m", v, flags)
    v, lab, mu = P.parse_first(row["Breadth"])
    note("beam", lab, mu)
    _set(rec, "beam_m", v, flags)
    v, lab, mu = P.parse_draught_m(row["Draught"])
    note("draught", lab, mu)
    _set(rec, "draught_m", v, flags)
    shafts, propulsor = P.parse_shafts(row["No_of_shafts"])
    rec["shafts"] = shafts
    rec["propulsor"] = propulsor
    v, lab, mu = P.parse_power_hp(row["Power"])
    note("power", lab, mu)
    _set(rec, "power_hp", v, flags)
    sp = P.parse_speed_kn(row["speed"])
    note("speed", sp["label"], sp["multi"])
    _set(rec, "speed_kn", sp["value"], flags)
    rec["speed_submerged_kn"] = sp["submerged"] if sp["submerged"] and 1 <= sp["submerged"] <= 60 else None
    en = P.parse_endurance(row["Endurance"])
    _set(rec, "endurance_nm", en["nm"], flags)
    rec["endurance_kn"] = en["kn"] if en["nm"] is not None and en["kn"] and 1 <= en["kn"] <= 50 else None
    if rec["endurance_nm"] is None:
        rec["endurance_kn"] = None
    rec["endurance_unlimited"] = en["unlimited"]
    v, lab, mu = P.parse_first(row["Complement"])
    note("complement", lab, mu)
    _set(rec, "complement", v, flags)
    fuel = P.parse_fuel(row["Fuel"])
    rec["fuel_type"] = fuel["type"]
    rec["fuel_amount"] = fuel["amount"]
    rec["fuel_unit"] = fuel["unit"]
    rec["propulsion"] = P.propulsion_tags(row["Machinery"])

    # cross-field plausibility (flag + null the less reliable field)
    dn = rec["displacement_normal_t"] or rec["displacement_full_t"]
    if rec["length_m"] and rec["beam_m"] and rec["beam_m"] > 0 and not (1.2 <= rec["length_m"] / rec["beam_m"] <= 25):
        flags.append(f"length_beam_ratio:{rec['length_m'] / rec['beam_m']:.1f}")
        rec["beam_m"] = None
    if dn and rec["power_hp"] and rec["power_hp"] / dn > 400:
        flags.append(f"power_per_tonne:{rec['power_hp'] / dn:.0f}")
        rec["power_hp"] = None
    if dn and rec["length_m"] and dn / rec["length_m"] ** 3 > 0.9:  # density sanity (t/m^3 of L^3)
        flags.append("displacement_length_inconsistent")
    if rec["displacement_normal_t"] and rec["displacement_full_t"] and rec["displacement_full_t"] < 0.5 * rec["displacement_normal_t"]:
        flags.append("full_lt_normal")

    arm = P.parse_armament(row["Armament"])
    rec["weapons"] = arm["weapons"]
    rec["armament_n_other"] = arm["n_other"]
    rec["armament_other_pieces"] = arm["other_pieces"][:4]
    rec["armament_parse_ok"] = arm["weapons"] is not None and (arm["n_pieces"] == 0 or arm["n_other"] / max(1, arm["n_pieces"]) <= 0.34)
    if arm["variant"].get("multi"):
        multi.append("armament")
        if arm["variant"].get("label"):
            variants["armament"] = str(arm["variant"]["label"])[:60]
    mg = P.main_gun(arm["weapons"])
    rec["main_gun"] = mg
    sen = P.parse_sensors(row["Electronic_equipment"])
    rec["sensors"] = sen["sensors"]
    rec["sensor_kinds"] = P.sensor_kinds(sen["sensors"])
    if sen["variant"].get("multi"):
        multi.append("sensors")
        if sen["variant"].get("label"):
            variants["sensors"] = str(sen["variant"]["label"])[:60]
    # empty Electronic_equipment on a post-radar ship means "unknown", not "none"
    rec["sensors_known"] = bool(sen["sensors"]) or (year is not None and year < 1935 and sen["sensors"] is None)
    if sen["sensors"] is None and rec["sensors_known"]:
        rec["sensors"], rec["sensor_kinds"] = [], []

    rec["variant_note"] = variants
    rec["multi_variant_fields"] = multi
    rec["armament_raw"] = row["Armament"] or ""
    rec["sensors_raw"] = row["Electronic_equipment"] or ""
    rec["flags"] = flags
    return rec


def exclusion_reason(rec: dict[str, Any]) -> str | None:
    dn = rec["displacement_normal_t"] or rec["displacement_full_t"]
    if not dn and not rec["length_m"] and not rec["speed_kn"]:
        return "no_core_numeric"  # displacement, length and speed all missing
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--seed", type=int, default=20261006)
    ap.add_argument("--no-split", action="store_true")
    ap.add_argument("--inherit-from", type=Path, default=None, help="earlier version dir: classes already in it keep its split (see shipknow_split.run_inherit)")
    args = ap.parse_args()

    uri = f"file:{args.db.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    ship_years = load_ship_years(conn)
    group_years = load_group_years(conn)
    comp_years = load_ship_comp_years(conn)
    launched_years = load_ship_launched_years(conn)
    class_cols = _table_columns(conn, "CLASS")
    base_cols = ["CLASS_NAME", "TYPE", "COUNTRY", "Displacement_normal", "Displacement_full", "Length", "Breadth", "Draught",
                 "No_of_shafts", "Machinery", "Power", "speed", "Fuel", "Endurance", "Armament", "Electronic_equipment", "Complement", "source_url"]
    # columns that only the re-fetched DB has; selected when present so that the old DB keeps working
    new_cols = [c for c in ("Armour", "Displacement_standard", "WHEN_DATE", "HEADING", "extra_specs", "ships_kind") if c in class_cols]
    sql = f"SELECT rowid AS rowid, {', '.join(base_cols + new_cols)} FROM CLASS ORDER BY rowid"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = list(conn.execute(sql))
    conn.close()

    records: list[dict[str, Any]] = []
    excluded: collections.Counter[str] = collections.Counter()
    excluded_with_new_fields: collections.Counter[str] = collections.Counter()  # excluded rows that do carry a new-format field
    seen: dict[tuple[str | None, str | None], str] = {}
    dup_pairs: list[tuple[str, str]] = []
    for row in rows:
        rec = build_record(row, ship_years, group_years, comp_years, launched_years)
        key = (rec["source_url"], rec["class_name"])
        if rec["source_url"] and key in seen:
            excluded["duplicate_row"] += 1
            dup_pairs.append((rec["id"], seen[key]))
            continue
        seen[key] = rec["id"]
        reason = exclusion_reason(rec)
        if reason:
            excluded[reason] += 1
            if rec["displacement_standard_t"] or rec["armour_parts"] or rec["diving_depth_m"]:
                excluded_with_new_fields[reason] += 1
            continue
        records.append(rec)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "records.jsonl").open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    # explicit raw TYPE -> (fine, coarse, rule) table
    tmap: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw = row["TYPE"] or ""
        if raw in tmap:
            tmap[raw]["rows"] += 1
            continue
        t = T.classify(raw, None, None)
        tmap[raw] = {"fine": t["fine"], "coarse": t["coarse"], "rule": t["type_rule"], "rows": 1}
    (args.out_dir / "type_map.json").write_text(
        json.dumps(
            {
                "coarse_types": T.COARSE_TYPES,
                "url_code_map": T.URL_CODE_MAP,
                "year_rules": [{"fine_regex": r, "cutoff_year": c, "before": b, "after": a} for r, c, b, a in T.YEAR_RULES],
                "overrides": T.OVERRIDES,
                "note": "'coarse' below is TYPE-only; rows whose TYPE is generic/unknown fall back to the URL section code (type_source=url_code) at record level.",
                "map": dict(sorted(tmap.items(), key=lambda kv: -kv[1]["rows"])),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    # report
    by_type = collections.Counter(r["ship_type"] for r in records)
    by_src = collections.Counter(r["type_source"] for r in records)
    report = {
        "rows_in_class": len(rows),
        "records_kept": len(records),
        "excluded": dict(excluded),
        "excluded_with_new_fields": dict(excluded_with_new_fields),
        "source_db": str(args.db),
        "db_format": "new" if "WHEN_DATE" in class_cols else "old",
        "new_columns_read": new_cols,
        "year_source_note": "new format: class_when_date(_decade; the A of A/B) > ships_laid_launched > ships_when > ship_groups_completed > class_when_date_b_only; old format: class_name > ships_laid_launched > ships_when",
        "year_in_navy_source_counts": dict(collections.Counter(r["year_in_navy_source"] for r in records)),
        "year_in_navy_differs": sum(1 for r in records if r["year_in_navy"] and r["year"] and r["year_in_navy"] != r["year"]),
        "ship_type_counts": dict(by_type.most_common()),
        "type_source_counts": dict(by_src),
        "unknown_type_rate": round(by_type.get("unknown", 0) / max(1, len(records)), 4),
        "year_source_counts": dict(collections.Counter(r["year_source"] for r in records)),
        "field_coverage": {
            k: sum(1 for r in records if r.get(k) not in (None, [], "", False)) for k in
            ["year", "displacement_normal_t", "displacement_full_t", "length_m", "beam_m", "draught_m", "shafts", "power_hp", "speed_kn",
             "endurance_nm", "complement", "fuel_type", "weapons", "sensors", "sensors_known", "armament_parse_ok",
             "year_in_navy", "displacement_standard_t", "diving_depth_m", "armour_parts", "armour_belt_mm", "armour_deck_mm", "armour_turret_mm",
             "armour_ct_mm", "year_commissioned", "year_laid_launched", "when_date_raw"]
        },
        "ships_kind_counts": dict(collections.Counter(r["ships_kind"] for r in records)),
        "multi_variant_rows": sum(1 for r in records if r["multi_variant_fields"]),
        "outlier_flag_counts": dict(collections.Counter(f.split(":")[0] for r in records for f in r["flags"]).most_common()),
        "armament_parse_ok_rate": round(sum(1 for r in records if r["armament_parse_ok"]) / max(1, sum(1 for r in records if r["weapons"] is not None)), 4),
        "weapon_kind_counts": dict(collections.Counter(w["kind"] for r in records for w in (r["weapons"] or [])).most_common()),
        "sensor_kind_counts": dict(collections.Counter(s["kind"] for r in records for s in (r["sensors"] or [])).most_common()),
    }
    (args.out_dir / "extract_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("rows_in_class", "records_kept", "excluded", "unknown_type_rate", "armament_parse_ok_rate")}, ensure_ascii=False))
    print(f"wrote {len(records)} records -> {args.out_dir / 'records.jsonl'}")

    if not args.no_split:
        import shipknow_split as S

        if args.inherit_from:
            S.run_inherit(args.out_dir, args.inherit_from, args.seed)
        else:
            S.run(args.out_dir, args.seed)


if __name__ == "__main__":
    main()
