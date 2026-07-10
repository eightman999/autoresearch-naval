"""Build navspec LoRA schema-only and raw-baseline datasets.

The generated teacher output is intentionally shorter than ShipSpecRecord.
Classic rules remain the final authority; the LoRA learns to emit a stable
candidate JSON envelope that can be validated and then passed to rules.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_constraints import apply_constraints, parse_constraints  # noqa: E402
from navspec_contradiction import check_contradiction  # noqa: E402
from navspec_equipment import equipment_plan, estimate_dimensions, estimate_power_shp  # noqa: E402
from navspec_nation_tier import apply_tier_clamps, get_nation_tier, get_tier_limits  # noqa: E402
from navspec_shiptypes import (  # noqa: E402
    SHIP_TYPE_SPECS,
    clamp_main_caliber,
    clamp_speed,
    get_ship_type_specs,
    hull_kind_for_ship_type,
    lb_ratio_band_for,
)
from validate_navspec_output import validate_schema  # noqa: E402


DEFAULT_SCHEMA = ROOT / "schemas" / "navspec_lora_candidate.schema.json"
DEFAULT_EVAL_CASES = ROOT / "tools" / "eval_test_cases.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "navspec" / "lora" / "schema_only_v0"
DEFAULT_BASELINE_DIR = ROOT / "data" / "navspec" / "lora" / "raw_baseline_500"
SYSTEM_PROMPT = (
    "You are navspec, a naval vessel specification generator. "
    "Return only JSON matching navspec.lora_candidate.v1. "
    "Do not repeat the user request. "
    "The top-level keys must be schema_version,status,spec,reason,suggested_revision,design_concept,confidence. "
    "Use status=impossible with reason[] and suggested_revision when the request cannot be satisfied."
)
TOP_LEVEL_OUTPUT_FIELDS = [
    "schema_version",
    "status",
    "spec",
    "reason",
    "suggested_revision",
    "design_concept",
    "confidence",
]
SPEC_OUTPUT_FIELDS = [
    "nation",
    "year",
    "ship_type",
    "standard_displacement_t",
    "full_load_displacement_t",
    "length_m",
    "beam_m",
    "draft_m",
    "lb_ratio",
    "power_shp",
    "speed_kt",
    "range_nm",
    "main_gun_caliber_mm",
    "main_gun_count",
    "belt_armor_mm",
    "torpedo_tubes",
    "radar",
    "nation_tier",
]

NATIONS = [
    "United States",
    "United Kingdom",
    "Japan",
    "Germany",
    "France",
    "Italy",
    "Netherlands",
    "Argentina",
    "Brazil",
    "Sweden",
    "Portugal",
    "Thailand",
    "Qing China",
    "Chile",
    "Egypt",
    "Norway",
]

YEARS = [1855, 1885, 1888, 1905, 1907, 1912, 1922, 1930, 1934, 1936, 1938, 1941, 1943, 1944, 1950]

SHIP_TYPES = [
    "ship_of_the_line",
    "protected_cruiser",
    "armored_cruiser",
    "pre_dreadnought_battleship",
    "dreadnought_battleship",
    "treaty_cruiser",
    "heavy_cruiser",
    "light_cruiser",
    "anti_air_light_cruiser",
    "destroyer",
    "large_destroyer",
    "anti_air_destroyer",
    "destroyer_escort",
    "patrol_frigate",
    "patrol_boat",
    "gunboat",
    "coastal_defense_ship",
    "battleship",
    "fast_battleship",
]

ROLES = [
    "fleet_battle",
    "night_battle",
    "long_range_scout",
    "carrier_screen",
    "convoy_escort",
    "anti_submarine_escort",
    "colonial_patrol",
    "regional_flagship",
    "commerce_raiding",
    "coastal_defense",
    "fleet_air_defense",
]

TEST_NATIONS = {"Portugal", "Thailand", "Qing China", "Egypt"}
TEST_SHIP_TYPES = {"ship_of_the_line", "gunboat", "patrol_boat"}
VALIDATION_ROLES = {"carrier_screen", "anti_submarine_escort", "colonial_patrol"}


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def clamp_num(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def base_range_nm(ship_type: str, role: str, year: int, tier: int) -> float:
    hull = hull_kind_for_ship_type(ship_type)
    if ship_type == "ship_of_the_line":
        value = 1200
    elif hull == "battleship":
        value = 6500
    elif hull == "battlecruiser":
        value = 7000
    elif hull == "cruiser":
        value = 8000
    elif hull == "destroyer":
        value = 3500
    else:
        value = 2500

    if "long_range" in role or "commerce" in role or "colonial" in role:
        value *= 1.25
    if "coastal" in role:
        value *= 0.65
    if year < 1900:
        value *= 0.65
    if tier >= 3:
        value *= 0.85
    if tier >= 4:
        value *= 0.75
    return round(clamp_num(value, 500, 18000))


def torpedo_tubes_for(ship_type: str, role: str, constraints: dict[str, Any]) -> int:
    hull = hull_kind_for_ship_type(ship_type)
    if constraints.get("torpedo_required") or "torpedo" in role or "night" in role:
        if hull == "destroyer":
            return 8
        if hull == "cruiser":
            return 12 if ship_type == "heavy_cruiser" else 6
        return 2
    if hull == "destroyer":
        return 4
    if hull == "cruiser" and ship_type not in {"protected_cruiser", "anti_air_light_cruiser"}:
        return 4
    return 0


def request_prompt(nation: str, year: int, ship_type: str, role: str, constraints: dict[str, Any], index: int) -> str:
    if index % 3 == 0:
        return f"{year}年の{nation}向けに、{role}を重視した{ship_type}を設計せよ。JSONのみで返す。"
    if constraints:
        return f"Design a {ship_type} for {nation} in {year} with role={role} and the listed constraints. Return JSON only."
    return f"Design a plausible {role} {ship_type} for {nation} in {year}. Return JSON only."


def generated_case(index: int) -> dict[str, Any]:
    mod = index % 20
    if mod == 0:
        return {
            "id": f"lora_case_{index:05d}",
            "category": "impossible_small_ship_combo",
            "nation": "Japan",
            "year": 1937,
            "ship_type": "destroyer",
            "role": "impossible_super_destroyer",
            "constraints": {
                "standard_displacement_t_max": 1500,
                "speed_kt_min": 40,
                "main_gun_caliber_mm_min": 203,
                "main_gun_barrels_min": 6,
                "belt_armor_mm_min": 150,
                "range_nm_min": 10000,
            },
            "request": "1937年の駆逐艦に20.3cm砲6門、150mm装甲帯、40kt、10,000海里を同時要求する。",
        }
    if mod == 1:
        return {
            "id": f"lora_case_{index:05d}",
            "category": "impossible_era_equipment",
            "nation": "United Kingdom",
            "year": 1890,
            "ship_type": "protected_cruiser",
            "role": "early_electronics",
            "constraints": {
                "radar_required": True,
                "diesel_required": True,
                "dual_purpose_gun_required": True,
                "speed_kt_min": 40,
            },
            "request": "1890年の防護巡洋艦にレーダー、両用砲、ディーゼル機関、40ktを要求する。",
        }
    if mod == 2:
        treaty = index % 40 == 2
        return {
            "id": f"lora_case_{index:05d}",
            "category": "impossible_treaty" if treaty else "impossible_tier",
            "nation": "United States" if treaty else "Chile",
            "year": 1930 if treaty else 1940,
            "ship_type": "treaty_cruiser" if treaty else "battleship",
            "role": "treaty_limit_test" if treaty else "minor_power_capital_ship",
            "constraints": (
                {"standard_displacement_t_min": 16000, "main_gun_caliber_mm_min": 254, "treaty_limited": True}
                if treaty
                else {}
            ),
            "request": (
                "条約期の巡洋艦に16,000t以上と254mm砲を要求する。"
                if treaty
                else "限定工業力国が1940年に新造戦艦を自主建造する。"
            ),
        }

    nation = NATIONS[(index * 5 + index // 7) % len(NATIONS)]
    year = YEARS[(index * 3 + index // 11) % len(YEARS)]
    ship_type = SHIP_TYPES[(index * 7 + index // 13) % len(SHIP_TYPES)]
    role = ROLES[(index * 4 + index // 17) % len(ROLES)]
    specs = get_ship_type_specs(ship_type)
    constraints: dict[str, Any] = {}
    category = "normal"

    if mod in {3, 9, 15}:
        constraints["standard_displacement_t_max"] = round(specs["normal_t"] * (1.05 + (index % 3) * 0.1))
        category = "constraint_displacement"
    if mod in {4, 10, 16}:
        constraints["speed_kt_min"] = clamp_speed(specs["speed"] + (index % 3), ship_type, year)
        category = "constraint_speed"
    if mod in {5, 11, 17}:
        constraints["main_gun_caliber_mm_max"] = clamp_main_caliber(specs["main_caliber_mm"], ship_type, year)
        category = "constraint_gun"
    if mod in {6, 12}:
        constraints["range_nm_min"] = base_range_nm(ship_type, role, year, get_nation_tier(nation))
        category = "constraint_range"
    if mod in {7, 13}:
        constraints["torpedo_required"] = hull_kind_for_ship_type(ship_type) in {"destroyer", "cruiser"}
        role = "night_battle" if constraints["torpedo_required"] else role
        category = "constraint_torpedo"
    if mod in {8, 14, 18}:
        constraints["low_cost"] = True
        constraints["standard_displacement_t_max"] = round(specs["normal_t"] * 0.9)
        category = "low_cost"

    return {
        "id": f"lora_case_{index:05d}",
        "category": category,
        "nation": nation,
        "year": year,
        "ship_type": ship_type,
        "role": role,
        "constraints": constraints,
        "request": request_prompt(nation, year, ship_type, role, constraints, index),
    }


def load_classic_eval_cases(extended_count: int = 100) -> list[dict[str, Any]]:
    from eval_suite import build_extended_tests

    tests = json.loads(DEFAULT_EVAL_CASES.read_text(encoding="utf-8"))
    tests.extend(build_extended_tests(extended_count))
    cases: list[dict[str, Any]] = []
    for test in tests:
        test_id = test["test_id"]
        cases.append(
            {
                "id": f"classic_eval_{test_id}",
                "category": f"classic_eval_{test_id.rsplit('_', 1)[0]}",
                "nation": test["nation"],
                "year": int(test["year"]),
                "ship_type": test["ship_type"],
                "role": test.get("role") or "classic_eval",
                "constraints": test.get("constraints", {}),
                "request": test.get("prompt") or request_prompt(
                    test["nation"],
                    int(test["year"]),
                    test["ship_type"],
                    test.get("role") or "classic_eval",
                    test.get("constraints", {}),
                    0,
                ),
            }
        )
    return cases


def split_for(case: dict[str, Any], index: int) -> str:
    if case["category"].startswith("impossible") and index % 10 == 0:
        return "test"
    if (case["nation"] in TEST_NATIONS or case["ship_type"] in TEST_SHIP_TYPES) and index % 4 == 0:
        return "test"
    if (case["nation"] in TEST_NATIONS or case["ship_type"] in TEST_SHIP_TYPES) and index % 4 == 1:
        return "validation"
    if (case["role"] in VALIDATION_ROLES or case["year"] in {1888, 1931, 1950}) and index % 3 == 0:
        return "validation"
    if index % 17 == 0:
        return "test"
    if index % 11 == 0:
        return "validation"
    return "train"


def make_request(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "generate_navspec_lora_candidate",
        "output_schema": "navspec.lora_candidate.v1",
        "response_format": {
            "json_only": True,
            "top_level_required": TOP_LEVEL_OUTPUT_FIELDS,
            "spec_required_when_status_ok": SPEC_OUTPUT_FIELDS,
            "spec_must_be_null_when_status_impossible": True,
        },
        "nation": case["nation"],
        "year": case["year"],
        "ship_type": case["ship_type"],
        "role": case["role"],
        "constraints": case.get("constraints", {}),
        "request": case["request"],
    }


def make_ship_record(case: dict[str, Any]) -> dict[str, Any]:
    nation = case["nation"]
    year = int(case["year"])
    ship_type = case["ship_type"]
    hull = hull_kind_for_ship_type(ship_type)
    specs = get_ship_type_specs(ship_type)
    main_cal = clamp_main_caliber(specs["main_caliber_mm"], ship_type, year)
    speed = clamp_speed(specs["speed"], ship_type, year)
    disp = float(specs["normal_t"])
    length, beam, draft = estimate_dimensions(disp, hull, ship_type)
    power = estimate_power_shp(disp, speed)

    record = {
        "schema_version": "navspec.ship_spec_record.v1",
        "record_id": f"lora-teacher:{nation}:{ship_type}:{year}:{case['id']}",
        "source_kind": "fictional_bounded",
        "class_name": f"{nation} {ship_type.replace('_', ' ').title()} Study",
        "country": nation,
        "type": ship_type,
        "hull_kind": hull,
        "year": year,
        "displacement": {
            "normal_t": disp,
            "full_t": round(disp * 1.12, 1),
            "normal_raw": "lora teacher estimate",
            "full_raw": "lora teacher estimate",
        },
        "dimensions": {
            "length_m": length,
            "breadth_m": beam,
            "draught_m": draft,
            "length_raw": "lora teacher estimate",
            "breadth_raw": "lora teacher estimate",
            "draught_raw": "lora teacher estimate",
        },
        "propulsion": {
            "machinery": "lora teacher estimate",
            "shafts": specs["shafts"],
            "power_shp": power,
            "speed_kn": speed,
            "fuel_raw": "lora teacher estimate",
            "endurance_raw": "lora teacher estimate",
            "power_raw": "lora teacher estimate",
            "speed_raw": "lora teacher estimate",
        },
        "armor": {
            "belt_mm": specs.get("belt_mm") or 0,
            "raw": "lora teacher estimate",
        },
        "armament": {
            "main_caliber_mm": main_cal,
            "main_barrels": int(specs["barrels"]),
            "mounts": [],
            "raw": "lora teacher estimate",
        },
        "complement": {"crew": None, "raw": "lora teacher estimate"},
        "source_url": None,
        "derived_from": {"generator": "navspec-classic-rule-teacher"},
        "fictional_constraints": {"teacher_case": case["id"]},
    }

    parsed_constraints = parse_constraints(case.get("constraints"))
    record, _ = apply_constraints(record, parsed_constraints, ship_type=ship_type, year=year)
    record, _ = apply_tier_clamps(record, nation, ship_type, year)

    normal_t = record["displacement"]["normal_t"]
    length, beam, draft = estimate_dimensions(normal_t, hull, ship_type)
    record["dimensions"]["length_m"] = length
    record["dimensions"]["breadth_m"] = beam
    record["dimensions"]["draught_m"] = draft

    speed = record["propulsion"]["speed_kn"]
    record["propulsion"]["power_shp"] = estimate_power_shp(normal_t, speed)

    equipment = equipment_plan(record)
    record["era_profile"] = equipment["era_profile"]
    record["design_doctrine"] = equipment["design_doctrine"]
    record["equipment"] = equipment["equipment"]
    record["armor_scheme"] = equipment["armor_scheme"]
    record["equipment_candidates"] = equipment["equipment_candidates"]
    return record


def make_ok_output(case: dict[str, Any]) -> dict[str, Any]:
    constraints = parse_constraints(case.get("constraints"))
    record = make_ship_record(case)
    dims = record["dimensions"]
    disp = record["displacement"]
    propulsion = record["propulsion"]
    armament = record["armament"]
    armor = record["armor"]
    nation = case["nation"]
    year = int(case["year"])
    ship_type = case["ship_type"]
    tier = get_nation_tier(nation)
    range_nm = base_range_nm(ship_type, case["role"], year, tier)
    if "range_nm_min" in constraints:
        range_nm = max(range_nm, float(constraints["range_nm_min"]))
    if "range_nm_max" in constraints:
        range_nm = min(range_nm, float(constraints["range_nm_max"]))

    length = float(dims["length_m"])
    beam = float(dims["breadth_m"])
    lb_ratio = round(length / beam, 2)
    lb_min, lb_max = lb_ratio_band_for(ship_type, record["hull_kind"])
    lb_ratio = round(clamp_num(lb_ratio, lb_min, lb_max), 2)

    eq = record.get("equipment", {})
    fc = eq.get("fire_control", {}) if isinstance(eq, dict) else {}
    radar = bool(fc.get("radar")) if isinstance(fc, dict) else False

    spec = {
        "nation": nation,
        "year": year,
        "ship_type": ship_type,
        "standard_displacement_t": round(float(disp["normal_t"]), 1),
        "full_load_displacement_t": round(float(disp.get("full_t") or disp["normal_t"] * 1.12), 1),
        "length_m": round(length, 1),
        "beam_m": round(beam, 1),
        "draft_m": round(float(dims["draught_m"]), 1),
        "lb_ratio": lb_ratio,
        "power_shp": round(float(propulsion["power_shp"])) if propulsion.get("power_shp") else None,
        "speed_kt": round(float(propulsion["speed_kn"]), 1),
        "range_nm": round(range_nm),
        "main_gun_caliber_mm": round(float(armament["main_caliber_mm"]), 1),
        "main_gun_count": int(armament["main_barrels"]),
        "belt_armor_mm": round(float(armor.get("belt_mm") or 0), 1),
        "torpedo_tubes": torpedo_tubes_for(ship_type, case["role"], constraints),
        "radar": radar,
        "nation_tier": tier,
    }
    concept = (
        f"{year}年の{nation}向けに、{case['role']}を重視した"
        f"{ship_type}案。tier-{tier}と艦種別L/B {lb_min:.1f}-{lb_max:.1f}を反映。"
    )
    return {
        "schema_version": "navspec.lora_candidate.v1",
        "status": "ok",
        "spec": spec,
        "reason": [],
        "suggested_revision": {},
        "design_concept": concept,
        "confidence": 0.88,
    }


def make_impossible_output(case: dict[str, Any], contradiction: dict[str, Any]) -> dict[str, Any]:
    revision = contradiction.get("suggested_revision") or {}
    if revision and "range_nm" not in revision:
        revision["range_nm"] = base_range_nm(case["ship_type"], case["role"], int(case["year"]), get_nation_tier(case["nation"]))
    return {
        "schema_version": "navspec.lora_candidate.v1",
        "status": "impossible",
        "spec": None,
        "reason": contradiction.get("reason") or contradiction.get("reasons") or ["要求条件が成立しない"],
        "suggested_revision": revision,
        "design_concept": "要求条件のうち成立しない要素を緩和し、同じ艦種・年代・国家に近い実現可能案へ修正する。",
        "confidence": 0.93,
    }


def make_output(case: dict[str, Any]) -> dict[str, Any]:
    contradiction = check_contradiction(
        case["ship_type"],
        int(case["year"]),
        case["nation"],
        constraints=case.get("constraints"),
        raw_prompt=case.get("request"),
    )
    if contradiction.get("status") == "impossible":
        return make_impossible_output(case, contradiction)
    return make_ok_output(case)


def make_messages(request: dict[str, Any], output: dict[str, Any] | None) -> list[dict[str, str]]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": compact(request)},
    ]
    if output is not None:
        messages.append({"role": "assistant", "content": compact(output)})
    return messages


def conversation_text(messages: list[dict[str, str]]) -> str:
    return "\n".join(f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in messages)


def build_record(case: dict[str, Any], schema: dict[str, Any], include_assistant: bool) -> dict[str, Any]:
    request = make_request(case)
    output = make_output(case)
    errors = validate_schema(output, schema)
    if errors:
        raise ValueError(f"{case['id']} teacher output failed schema: {errors[:5]}")
    messages = make_messages(request, output if include_assistant else None)
    row = {
        "id": case["id"],
        "category": case["category"],
        "input": request,
        "messages": messages,
        "text": conversation_text(messages),
    }
    if include_assistant:
        row["output"] = output
    else:
        row["expected_output"] = output
    return row


def build_rows(
    count: int,
    schema: dict[str, Any],
    include_assistant: bool,
    *,
    include_classic_eval: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    index_offset = 0
    if include_classic_eval:
        for case in load_classic_eval_cases():
            row = build_record(case, schema, include_assistant=include_assistant)
            row["split"] = "baseline" if not include_assistant else split_for(case, index_offset)
            rows.append(row)
            index_offset += 1
    for index in range(count):
        case = generated_case(index)
        row = build_record(case, schema, include_assistant=include_assistant)
        if include_assistant:
            row["split"] = split_for(case, index)
        else:
            row["split"] = "baseline"
        rows.append(row)
    return rows


def write_manifest(path: Path, rows: list[dict[str, Any]], dataset: str, schema_path: Path) -> None:
    split_counts = Counter(row["split"] for row in rows)
    category_counts = Counter(row["category"] for row in rows)
    status_counts = Counter((row.get("output") or row.get("expected_output") or {}).get("status") for row in rows)
    manifest = {
        "dataset": dataset,
        "schema": str(schema_path),
        "schema_version": "navspec.lora_candidate.v1",
        "rows": len(rows),
        "split_counts": dict(sorted(split_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "system_prompt": SYSTEM_PROMPT,
        "teacher": "navspec-classic-v1 rule engine",
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build navspec 0.5B LoRA datasets")
    parser.add_argument("--dataset", choices=["schema-only", "raw-baseline"], default="schema-only")
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--include-classic-eval",
        action="store_true",
        help="raw-baselineに既存22件+拡張100件のclassic eval入力を先頭追加する",
    )
    args = parser.parse_args()

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    if args.output_dir is None:
        args.output_dir = DEFAULT_BASELINE_DIR if args.dataset == "raw-baseline" else DEFAULT_OUTPUT_DIR

    include_assistant = args.dataset == "schema-only"
    rows = build_rows(
        args.count,
        schema,
        include_assistant=include_assistant,
        include_classic_eval=args.include_classic_eval,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.dataset == "schema-only":
        by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
        for row in rows:
            by_split[row["split"]].append(row)
        for split, split_rows in by_split.items():
            write_jsonl(args.output_dir / f"{split}.jsonl", split_rows)
        write_jsonl(args.output_dir / "valid.jsonl", by_split["validation"])
        write_jsonl(args.output_dir / "all.jsonl", rows)
    else:
        write_jsonl(args.output_dir / "cases.jsonl", rows)

    write_manifest(args.output_dir / "dataset_manifest.json", rows, args.dataset, args.schema)
    print(f"dataset: {args.dataset}")
    print(f"rows: {len(rows)}")
    print(f"output_dir: {args.output_dir}")
    print("splits:", dict(sorted(Counter(row["split"] for row in rows).items())))
    print("status:", dict(sorted(Counter((row.get("output") or row.get("expected_output"))["status"] for row in rows).items())))


if __name__ == "__main__":
    main()
