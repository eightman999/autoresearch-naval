"""Extract battleship ShipSpecRecord JSONL from the SSW NSDB."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

from navspec_common import (
    DEFAULT_NSDB,
    first_number,
    normalize_source_url,
    parse_armament_summary,
    parse_belt_mm,
    parse_length,
    parse_power,
    parse_year,
    render_wiki_table,
    ship_hull_kind,
    validate_spec,
    write_jsonl,
)


DEFAULT_OUTPUT = Path("data/navspec/battleship_real_specs.jsonl")


def fetch_rows(db_path: Path, limit: int | None) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT
                CLASS_NAME, TYPE, COUNTRY,
                Displacement_normal, Displacement_full,
                Length, Breadth, Draught,
                No_of_shafts, Machinery, Power, speed, Fuel, Endurance,
                Armour, Armament, Complement, sections, source_url, fetched_at,
                (SELECT WHEN_DATE FROM SHIPS s WHERE s.source_url = CLASS.source_url LIMIT 1) as when_date,
                (SELECT Laid_down FROM SHIPS s WHERE s.source_url = CLASS.source_url LIMIT 1) as laid_down
            FROM CLASS
            WHERE lower(TYPE) NOT LIKE '%submarine%'
              AND lower(TYPE) NOT LIKE '%aircraft%'
              AND TYPE IS NOT NULL AND TYPE != ''
            ORDER BY COUNTRY, TYPE, CLASS_NAME
        """
        if limit is not None:
            sql += " LIMIT ?"
            return list(conn.execute(sql, (limit,)))
        return list(conn.execute(sql))
    finally:
        conn.close()


def make_record(row: sqlite3.Row, index: int) -> dict[str, Any]:
    class_name = row["CLASS_NAME"] or "unknown"
    ship_type = row["TYPE"] or "unknown"
    country = row["COUNTRY"] or "unknown"
    armament = parse_armament_summary(row["Armament"])
    year = parse_year(class_name, row["source_url"], row["sections"], row["when_date"], row["laid_down"])
    record = {
        "schema_version": "navspec.ship_spec_record.v1",
        "record_id": f"nsdb:{country}:{ship_type}:{class_name}:{index}",
        "source_kind": "historical",
        "class_name": class_name,
        "country": country,
        "type": ship_type,
        "hull_kind": ship_hull_kind(ship_type, class_name),
        "year": year,
        "displacement": {
            "normal_t": first_number(row["Displacement_normal"]),
            "full_t": first_number(row["Displacement_full"]),
            "normal_raw": row["Displacement_normal"] or "",
            "full_raw": row["Displacement_full"] or "",
        },
        "dimensions": {
            "length_m": parse_length(row["Length"]),
            "breadth_m": first_number(row["Breadth"]),
            "draught_m": first_number(row["Draught"]),
            "length_raw": row["Length"] or "",
            "breadth_raw": row["Breadth"] or "",
            "draught_raw": row["Draught"] or "",
        },
        "propulsion": {
            "machinery": row["Machinery"] or "",
            "shafts": first_number(row["No_of_shafts"]),
            "power_shp": parse_power(row["Power"]),
            "speed_kn": first_number(row["speed"]),
            "fuel_raw": row["Fuel"] or "",
            "endurance_raw": row["Endurance"] or "",
            "power_raw": row["Power"] or "",
            "speed_raw": row["speed"] or "",
        },
        "armor": {
            "raw": row["Armour"] or "",
            "belt_mm": parse_belt_mm(row["Armour"], row["sections"]),
        },
        "armament": armament,
        "complement": {
            "crew": first_number(row["Complement"]),
            "raw": row["Complement"] or "",
        },
        "source_url": normalize_source_url(row["source_url"]),
        "source_trace": {
            "database": str(DEFAULT_NSDB),
            "source_url_raw": row["source_url"] or "",
            "fetched_at": row["fetched_at"] or "",
        },
    }
    record["validation_issues"] = validate_spec(record)
    record["wiki_table_markdown"] = render_wiki_table(record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract battleship ShipSpecRecord JSONL from NSDB")
    parser.add_argument("--db", type=Path, default=DEFAULT_NSDB, help="Path to SSW nvpda-db/NSDB.db")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSONL path")
    parser.add_argument("--limit", type=int, default=None, help="Limit records for smoke tests")
    args = parser.parse_args()

    rows = fetch_rows(args.db, args.limit)
    all_records = [make_record(row, index) for index, row in enumerate(rows)]
    records = [r for r in all_records if not any(i["severity"] == "error" for i in r.get("validation_issues", []))]
    skipped = len(all_records) - len(records)
    if skipped:
        print(f"  excluded {skipped}/{len(all_records)} records with validation errors")
    write_jsonl(args.output, records)
    print(f"wrote {len(records)} records to {args.output}")


if __name__ == "__main__":
    main()
