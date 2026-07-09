"""Create bounded fictional battleship specifications from historical records."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

from navspec_common import MUTATION_PROFILES, finite_or_none, read_jsonl, render_wiki_table, validate_spec, write_jsonl


DEFAULT_INPUT = Path("data/navspec/battleship_real_specs.jsonl")
DEFAULT_OUTPUT = Path("data/navspec/battleship_fictional_specs.jsonl")


def scaled(value: float | None, factor: float) -> float | None:
    return finite_or_none(value * factor) if value is not None else None


def bounded(value: float | None, lower: float, upper: float) -> float | None:
    if value is None:
        return None
    return round(max(lower, min(upper, value)), 3)


def mutate_record(record: dict, profile_index: int) -> dict:
    profile = MUTATION_PROFILES[profile_index % len(MUTATION_PROFILES)]
    out = copy.deepcopy(record)
    out["schema_version"] = "navspec.ship_spec_record.v1"
    out["record_id"] = f"fictional:{record['record_id']}:m{profile_index}"
    out["source_kind"] = "fictional_bounded"
    out["class_name"] = f"{record.get('class_name', 'Unknown')} alternate {profile_index + 1}"
    out["source_url"] = None
    out["derived_from"] = {
        "record_id": record.get("record_id"),
        "class_name": record.get("class_name"),
        "country": record.get("country"),
        "source_url": record.get("source_url"),
        "mutation_profile": profile.note,
    }

    displacement = out.get("displacement", {})
    displacement["normal_t"] = scaled(displacement.get("normal_t"), profile.displacement_scale)
    displacement["full_t"] = scaled(displacement.get("full_t"), profile.displacement_scale)
    displacement["normal_raw"] = "fictional bounded estimate"
    displacement["full_raw"] = "fictional bounded estimate"

    dim_scale = profile.displacement_scale ** (1.0 / 3.0)
    dimensions = out.get("dimensions", {})
    dimensions["length_m"] = scaled(dimensions.get("length_m"), dim_scale)
    dimensions["breadth_m"] = scaled(dimensions.get("breadth_m"), dim_scale)
    dimensions["draught_m"] = scaled(dimensions.get("draught_m"), dim_scale)
    dimensions["length_raw"] = "fictional bounded estimate"
    dimensions["breadth_raw"] = "fictional bounded estimate"
    dimensions["draught_raw"] = "fictional bounded estimate"

    propulsion = out.get("propulsion", {})
    old_disp = record.get("displacement", {}).get("full_t") or record.get("displacement", {}).get("normal_t")
    new_disp = displacement.get("full_t") or displacement.get("normal_t")
    old_speed = record.get("propulsion", {}).get("speed_kn")
    old_power = record.get("propulsion", {}).get("power_shp")
    new_speed = bounded((old_speed + profile.speed_delta) if old_speed is not None else None, 10.0, 36.0)
    if old_disp and new_disp and old_speed and old_power and new_speed:
        power_factor = (new_disp / old_disp) ** (2.0 / 3.0) * (new_speed / old_speed) ** 3
        propulsion["power_shp"] = finite_or_none(old_power * power_factor)
    else:
        propulsion["power_shp"] = scaled(old_power, profile.power_scale)
    propulsion["speed_kn"] = new_speed
    propulsion["power_raw"] = "fictional bounded estimate"
    propulsion["speed_raw"] = "fictional bounded estimate"

    armor = out.get("armor", {})
    armor["belt_mm"] = scaled(armor.get("belt_mm"), profile.armor_scale)
    armor["raw"] = "fictional bounded estimate"

    armament = out.get("armament", {})
    main_caliber = armament.get("main_caliber_mm")
    if main_caliber is not None:
        armament["main_caliber_mm"] = bounded(main_caliber + profile.main_caliber_delta, 203.0, 510.0)
    armament["raw"] = "fictional bounded estimate"
    for mount in armament.get("mounts", []):
        caliber = mount.get("caliber_mm")
        if caliber == main_caliber:
            mount["caliber_mm"] = armament.get("main_caliber_mm")

    out["fictional_constraints"] = {
        "method": "bounded_mutation",
        "profile": profile.note,
        "displacement_scale": profile.displacement_scale,
        "speed_delta_kn": profile.speed_delta,
        "armor_scale": profile.armor_scale,
        "main_caliber_delta_mm": profile.main_caliber_delta,
    }
    out["validation_issues"] = validate_spec(out)
    out["wiki_table_markdown"] = render_wiki_table(out)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Create bounded fictional battleship specs")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--variants-per-record", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    records = read_jsonl(args.input)
    if args.limit is not None:
        records = records[: args.limit]

    fictional: list[dict] = []
    for record in records:
        for idx in range(args.variants_per_record):
            fictional.append(mutate_record(record, idx))
    write_jsonl(args.output, fictional)
    print(f"wrote {len(fictional)} records to {args.output}")


if __name__ == "__main__":
    main()
