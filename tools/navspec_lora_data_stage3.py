"""Build navspec Stage 3 (numeric quality) LoRA datasets.

Stage 2 established schema/status/reason generation. Stage 3 improves the
NUMERIC quality of status=ok candidate specs so the classic rule engine
needs fewer major patches.

Key difference from Stage 2: teacher outputs include controlled numeric
variation within valid ranges, so the model learns RANGES not point values.

Teacher generation:
  1. Canonical spec from classic rule engine (make_ok_output)
  2. Controlled jitter within ship_type/era/tier bounds
  3. Constraint-aware adjustment

All ok teachers are validated against Stage 2 schema (validate_stage2).
All impossible teachers are identical to Stage 2 (classic-ruled).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_constraints import parse_constraints  # noqa: E402
from navspec_contradiction import check_contradiction  # noqa: E402
from navspec_equipment import estimate_dimensions, estimate_power_shp  # noqa: E402
from navspec_lora_data import (  # noqa: E402
    DEFAULT_SCHEMA,
    NATIONS,
    ROLES,
    SHIP_TYPES,
    SYSTEM_PROMPT,
    YEARS,
    compact,
    conversation_text,
    generated_case,
    make_ok_output,
    make_request,
    request_prompt,
    split_for,
    write_jsonl,
)
from navspec_lora_data_stage2 import make_stage2_output  # noqa: E402
from navspec_lora_stage2_schema import validate_stage2  # noqa: E402
from navspec_nation_tier import get_nation_tier, get_tier_limits  # noqa: E402
from navspec_shiptypes import (  # noqa: E402
    SHIP_TYPE_MAIN_GUN_CLAMP,
    SHIP_TYPE_SPEED_CLAMP,
    clamp_main_caliber,
    clamp_speed,
    get_ship_type_specs,
    hull_kind_for_ship_type,
    lb_ratio_band_for,
)

DEFAULT_OUTPUT_DIR = ROOT / "data" / "navspec" / "lora" / "stage3_numeric_quality_v0"
DEFAULT_EVAL_DIR = ROOT / "data" / "navspec" / "lora" / "stage3_numeric_eval_v0"

TIER1_NATIONS = ["United States", "United Kingdom", "Germany", "Japan", "France", "Italy"]
TIER2_NATIONS = ["Netherlands", "Argentina", "Brazil", "Sweden", "Spain", "Russia"]
TIER3_NATIONS = ["Portugal", "Thailand", "Chile", "Norway", "Denmark", "Greece"]
TIER4_NATIONS = ["Egypt", "Algeria", "Philippines", "Vietnam"]

MAIN_SHIP_TYPES = [
    "battleship", "fast_battleship", "battlecruiser",
    "heavy_cruiser", "light_cruiser", "treaty_cruiser",
    "destroyer", "large_destroyer", "destroyer_escort",
    "patrol_frigate", "patrol_boat", "gunboat",
    "coastal_defense_ship", "protected_cruiser", "armored_cruiser",
    "pre_dreadnought_battleship", "dreadnought_battleship",
]

RARE_SHIP_TYPES = [
    "commerce_raider_cruiser", "anti_air_light_cruiser",
    "anti_air_destroyer", "torpedo_boat", "minesweeper", "minelayer",
    "large_cruiser", "ship_of_the_line",
    "submarine_tender", "seaplane_tender", "oiler", "repair_ship", "training_ship",
]

ALL_EVAL_SHIP_TYPES = MAIN_SHIP_TYPES + RARE_SHIP_TYPES

ERA_YEARS = {
    "pre_1860": [1850, 1855, 1860],
    "1860_1889": [1865, 1875, 1885],
    "1890_1905": [1890, 1898, 1905],
    "1906_1918": [1907, 1912, 1916],
    "1919_1936": [1922, 1928, 1934],
    "1937_1945": [1938, 1941, 1944],
    "post_1945": [1948, 1950, 1955],
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def jitter_value(
    canonical: float,
    rel_jitter: float,
    abs_jitter: float | None,
    lo: float | None,
    hi: float | None,
    rng: random.Random,
    round_digits: int = 1,
) -> float:
    """Apply controlled jitter within bounds."""
    delta = canonical * rel_jitter * rng.uniform(-1, 1)
    if abs_jitter is not None:
        delta += abs_jitter * rng.uniform(-1, 1)
    result = canonical + delta
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return round(result, round_digits)


def jitter_spec(
    canonical_spec: dict[str, Any],
    case: dict[str, Any],
    rng: random.Random,
    strength: float = 1.0,
) -> dict[str, Any]:
    """Apply controlled jitter to numeric spec fields within valid bounds."""
    ship_type = case["ship_type"]
    year = int(case["year"])
    nation = case["nation"]
    specs = get_ship_type_specs(ship_type)
    tier = get_nation_tier(nation)
    limits = get_tier_limits(nation)
    hull = hull_kind_for_ship_type(ship_type)

    _, gun_hi = SHIP_TYPE_MAIN_GUN_CLAMP.get(ship_type, (76, 520))
    _, spd_hi = SHIP_TYPE_SPEED_CLAMP.get(ship_type, (5, 45))
    _, spd_lo = SHIP_TYPE_SPEED_CLAMP.get(ship_type, (5, spd_hi))

    spec = dict(canonical_spec)

    disp = _num(spec.get("standard_displacement_t"))
    if disp is not None:
        max_td = limits.get("max_displacement_t")
        hi = max_td if max_td is not None else disp * 1.3
        lo = disp * 0.7
        spec["standard_displacement_t"] = jitter_value(disp, 0.05 * strength, None, lo, hi, rng)

    full = _num(spec.get("full_load_displacement_t"))
    if full is not None and disp is not None:
        ratio = full / disp if disp > 0 else 1.12
        new_full = _num(spec["standard_displacement_t"]) * ratio
        spec["full_load_displacement_t"] = round(new_full, 1)

    speed = _num(spec.get("speed_kt"))
    if speed is not None:
        spec["speed_kt"] = round(speed, 1)

    power = _num(spec.get("power_shp"))
    if power is not None and disp is not None:
        new_disp = _num(spec.get("standard_displacement_t", disp))
        if speed and new_disp:
            new_power = estimate_power_shp(new_disp, speed)
            if new_power is not None:
                spec["power_shp"] = round(new_power)

    gun = _num(spec.get("main_gun_caliber_mm"))
    if gun is not None:
        lo_gun = max(76, gun - 25 * strength)
        hi_gun = min(gun_hi + 5, gun + 25 * strength)
        spec["main_gun_caliber_mm"] = jitter_value(gun, 0.0, 2.0 * strength, lo_gun, hi_gun, rng)

    barrels = spec.get("main_gun_count")
    if isinstance(barrels, (int, float)):
        delta = rng.choice([-1, 0, 0, 0, 1]) if strength > 0.5 else 0
        spec["main_gun_count"] = max(1, int(barrels) + delta)

    belt = _num(spec.get("belt_armor_mm"))
    if belt is not None:
        spec["belt_armor_mm"] = jitter_value(belt, 0.05 * strength, 5.0 * strength, 0, None, rng)

    range_val = _num(spec.get("range_nm"))
    if range_val is not None:
        spec["range_nm"] = jitter_value(range_val, 0.05 * strength, None, 500, 20000, rng, round_digits=0)

    tubes = spec.get("torpedo_tubes")
    if isinstance(tubes, (int, float)) and tubes > 0 and strength > 0.5:
        delta = rng.choice([-2, 0, 0, 2])
        spec["torpedo_tubes"] = max(0, int(tubes) + delta)

    new_disp = _num(spec.get("standard_displacement_t"))
    if new_disp is not None:
        length, beam, draft = estimate_dimensions(new_disp, hull, ship_type)
        if length is not None:
            spec["length_m"] = length
        if beam is not None:
            spec["beam_m"] = beam
        if draft is not None:
            spec["draft_m"] = draft
        lb_min, lb_max = lb_ratio_band_for(ship_type, hull)
        raw_lb = length / beam if beam and beam > 0 else 0
        spec["lb_ratio"] = round(max(lb_min, min(lb_max, raw_lb)), 2)

    spec["nation"] = nation
    spec["year"] = year
    spec["ship_type"] = ship_type
    spec["nation_tier"] = tier

    return spec


def make_stage3_ok_output(
    case: dict[str, Any],
    rng: random.Random,
    jitter_strength: float = 1.0,
) -> dict[str, Any]:
    """Generate a status=ok teacher with controlled numeric variation."""
    canonical = make_ok_output(case)
    if canonical["status"] != "ok":
        return canonical

    jittered_spec = jitter_spec(canonical["spec"], case, rng, strength=jitter_strength)
    output = dict(canonical)
    output["spec"] = jittered_spec
    output["confidence"] = round(rng.uniform(0.82, 0.94), 2)

    concepts = [
        f"{case.get('year', '')}年の{case.get('nation', '')}向け{case.get('ship_type', '')}案",
        f"{case.get('role', 'fleet_battle')}重視の設計",
        f"tier-{get_nation_tier(case.get('nation', ''))}工業力を反映",
    ]
    output["design_concept"] = "。".join(rng.sample(concepts, min(2, len(concepts))))

    errors = validate_stage2(output)
    if errors:
        return canonical
    return output


def make_stage3_output(
    case: dict[str, Any],
    rng: random.Random,
    jitter_strength: float = 1.0,
) -> dict[str, Any]:
    """Generate Stage 3 teacher output. impossible cases use Stage 2 logic."""
    contradiction = check_contradiction(
        case["ship_type"],
        int(case["year"]),
        case["nation"],
        constraints=case.get("constraints"),
        raw_prompt=case.get("request"),
    )
    if contradiction.get("status") == "impossible":
        return make_stage2_output(case)

    return make_stage3_ok_output(case, rng, jitter_strength=jitter_strength)


def build_record(
    case: dict[str, Any],
    schema: dict[str, Any],
    rng: random.Random,
    jitter_strength: float = 1.0,
) -> dict[str, Any]:
    request = make_request(case)
    output = make_stage3_output(case, rng, jitter_strength=jitter_strength)
    errors = validate_stage2(output)
    if errors:
        raise ValueError(f"{case.get('id')} stage3 teacher invalid: {errors[:5]}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": compact(request)},
        {"role": "assistant", "content": compact(output)},
    ]
    return {
        "id": case["id"],
        "category": case["category"],
        "nation": case["nation"],
        "year": int(case["year"]),
        "ship_type": case["ship_type"],
        "role": case.get("role", ""),
        "constraints": case.get("constraints", {}),
        "request": case["request"],
        "input": request,
        "expected_status": output["status"],
        "expected_reason_codes": [item["code"] for item in output["reason"]],
        "messages": messages,
        "text": conversation_text(messages),
        "output": output,
        "expected_output": output,
        "split": case.get("split", "train"),
    }


# ---- case generators --------------------------------------------------------

def _base_case(
    index: int,
    category: str,
    nation: str,
    year: int,
    ship_type: str,
    role: str,
    constraints: dict[str, Any],
    request: str,
    id_prefix: str = "stage3",
) -> dict[str, Any]:
    return {
        "id": f"{id_prefix}_{category}_{index:05d}",
        "category": category,
        "nation": nation,
        "year": int(year),
        "ship_type": ship_type,
        "role": role,
        "constraints": constraints,
        "request": request,
    }


def gen_normal_numeric(index: int) -> dict[str, Any]:
    st = MAIN_SHIP_TYPES[index % len(MAIN_SHIP_TYPES)]
    nation = NATIONS[(index * 3) % len(NATIONS)]
    year = YEARS[(index * 5) % len(YEARS)]
    role = ROLES[(index * 4) % len(ROLES)]
    constraints: dict[str, Any] = {}
    request = request_prompt(nation, year, st, role, constraints, index)
    return _base_case(index, "normal_numeric", nation, year, st, role, constraints, request)


def gen_ship_type_contrast(index: int) -> dict[str, Any]:
    nation = TIER1_NATIONS[index % len(TIER1_NATIONS)]
    year = 1938
    st = MAIN_SHIP_TYPES[index % len(MAIN_SHIP_TYPES)]
    role = "fleet_battle"
    constraints: dict[str, Any] = {}
    request = f"{year}年の{nation}向け{st}を設計せよ。JSONのみ。"
    return _base_case(index, "ship_type_contrast", nation, year, st, role, constraints, request)


def gen_era_contrast(index: int) -> dict[str, Any]:
    era_keys = list(ERA_YEARS.keys())
    era = era_keys[index % len(era_keys)]
    years = ERA_YEARS[era]
    year = years[index % len(years)]
    st = MAIN_SHIP_TYPES[(index * 3) % len(MAIN_SHIP_TYPES)]
    nation = TIER1_NATIONS[index % len(TIER1_NATIONS)]
    role = "fleet_battle"
    constraints: dict[str, Any] = {}
    request = f"{year}年({era})の{nation}向け{st}を設計せよ。JSONのみ。"
    return _base_case(index, "era_contrast", nation, year, st, role, constraints, request)


def gen_nation_contrast(index: int) -> dict[str, Any]:
    tiers = [
        ("tier1", TIER1_NATIONS),
        ("tier2", TIER2_NATIONS),
        ("tier3", TIER3_NATIONS),
        ("tier4", TIER4_NATIONS),
    ]
    tier_label, tier_nations = tiers[index % len(tiers)]
    nation = tier_nations[(index // 4) % len(tier_nations)]
    year = 1938
    st = "destroyer"
    role = "fleet_battle"
    constraints: dict[str, Any] = {}
    request = f"{year}年の{nation}向け{st}を設計せよ。JSONのみ。"
    return _base_case(index, f"nation_{tier_label}", nation, year, st, role, constraints, request)


def gen_role_contrast(index: int) -> dict[str, Any]:
    roles_subset = [
        "fleet_battle", "night_battle", "commerce_raiding",
        "convoy_escort", "coastal_defense", "colonial_patrol",
    ]
    role = roles_subset[index % len(roles_subset)]
    st = MAIN_SHIP_TYPES[(index * 3) % len(MAIN_SHIP_TYPES)]
    nation = TIER1_NATIONS[index % len(TIER1_NATIONS)]
    year = YEARS[(index * 7) % len(YEARS)]
    constraints: dict[str, Any] = {}
    request = request_prompt(nation, year, st, role, constraints, index)
    return _base_case(index, "role_contrast", nation, year, st, role, constraints, request)


def gen_single_constraint(index: int) -> dict[str, Any]:
    st = MAIN_SHIP_TYPES[index % len(MAIN_SHIP_TYPES)]
    nation = TIER1_NATIONS[index % len(TIER1_NATIONS)]
    year = YEARS[(index * 5) % len(YEARS)]
    role = ROLES[index % len(ROLES)]
    specs = get_ship_type_specs(st)
    constraint_type = index % 4
    constraints: dict[str, Any] = {}

    if constraint_type == 0:
        constraints["speed_kt_min"] = min(float(specs["speed"]) - 2, float(specs["speed"]))
        request = f"{year}年の{nation}向け{st}を速度{constraints['speed_kt_min']}kt以上で設計せよ。JSONのみ。"
    elif constraint_type == 1:
        constraints["standard_displacement_t_max"] = round(float(specs["normal_t"]) * 1.1)
        request = f"{year}年の{nation}向け{st}を排水量{constraints['standard_displacement_t_max']}t以下で設計せよ。JSONのみ。"
    elif constraint_type == 2:
        constraints["main_gun_caliber_mm_max"] = float(specs["main_caliber_mm"])
        request = f"{year}年の{nation}向け{st}を主砲{constraints['main_gun_caliber_mm_max']}mm以下で設計せよ。JSONのみ。"
    else:
        constraints["range_nm_min"] = 4000
        request = f"{year}年の{nation}向け{st}を航続距離{constraints['range_nm_min']}海里以上で設計せよ。JSONのみ。"

    return _base_case(index, "single_constraint", nation, year, st, role, constraints, request)


def gen_multi_constraint(index: int) -> dict[str, Any]:
    st = MAIN_SHIP_TYPES[index % len(MAIN_SHIP_TYPES)]
    nation = TIER1_NATIONS[index % len(TIER1_NATIONS)]
    year = YEARS[(index * 3) % len(YEARS)]
    role = ROLES[index % len(ROLES)]
    specs = get_ship_type_specs(st)
    constraints: dict[str, Any] = {}
    constraints["speed_kt_min"] = max(20.0, float(specs["speed"]) - 3)
    constraints["standard_displacement_t_max"] = round(float(specs["normal_t"]) * 1.15)
    request = (
        f"{year}年の{nation}向け{st}を速度{constraints['speed_kt_min']}kt以上・"
        f"排水量{constraints['standard_displacement_t_max']}t以下で設計せよ。JSONのみ。"
    )
    return _base_case(index, "multi_constraint", nation, year, st, role, constraints, request)


def gen_boundary(index: int) -> dict[str, Any]:
    st = MAIN_SHIP_TYPES[index % len(MAIN_SHIP_TYPES)]
    nation = TIER1_NATIONS[index % len(TIER1_NATIONS)]
    year = YEARS[(index * 5) % len(YEARS)]
    role = "fleet_battle"
    specs = get_ship_type_specs(st)
    _, spd_hi = SHIP_TYPE_SPEED_CLAMP.get(st, (5, 45))
    constraints: dict[str, Any] = {"speed_kt_min": float(spd_hi) - 1}
    request = f"{year}年の{nation}向け{st}を最高速付近で設計せよ。JSONのみ。"
    return _base_case(index, "boundary", nation, year, st, role, constraints, request)


def gen_rare_ship_type(index: int) -> dict[str, Any]:
    st = RARE_SHIP_TYPES[index % len(RARE_SHIP_TYPES)]
    nation = NATIONS[(index * 3) % len(NATIONS)]
    year = YEARS[(index * 5) % len(YEARS)]
    role = ROLES[index % len(ROLES)]
    constraints: dict[str, Any] = {}
    request = request_prompt(nation, year, st, role, constraints, index)
    return _base_case(index, "rare_ship_type", nation, year, st, role, constraints, request)


def gen_diversity_pair(index: int) -> dict[str, Any]:
    st = MAIN_SHIP_TYPES[index % len(MAIN_SHIP_TYPES)]
    nation = TIER1_NATIONS[index % len(TIER1_NATIONS)]
    year = YEARS[(index * 7) % len(YEARS)]
    role = ROLES[index % len(ROLES)]
    constraints: dict[str, Any] = {}
    request = request_prompt(nation, year, st, role, constraints, index)
    return _base_case(index, "diversity", nation, year, st, role, constraints, request)


def gen_stage2_replay(index: int) -> dict[str, Any]:
    case = generated_case(index)
    case["id"] = f"stage3_replay_{index:05d}"
    case["category"] = f"replay_{case['category']}"
    return case


# ---- dataset builder --------------------------------------------------------

RATIOS = {
    "stage2_replay": 0.20,
    "normal_numeric": 0.25,
    "ship_type_contrast": 0.125,
    "era_contrast": 0.10,
    "nation_contrast": 0.075,
    "role_contrast": 0.075,
    "single_constraint": 0.075,
    "multi_constraint": 0.05,
    "boundary": 0.025,
    "rare_diversity": 0.025,
}


def build_cases(count: int, seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    targets = {k: max(1, int(count * v)) for k, v in RATIOS.items()}
    cases: list[dict[str, Any]] = []

    for i in range(targets["stage2_replay"]):
        cases.append(gen_stage2_replay(i))
    for i in range(targets["normal_numeric"]):
        cases.append(gen_normal_numeric(i))
    for i in range(targets["ship_type_contrast"]):
        cases.append(gen_ship_type_contrast(i))
    for i in range(targets["era_contrast"]):
        cases.append(gen_era_contrast(i))
    for i in range(targets["nation_contrast"]):
        cases.append(gen_nation_contrast(i))
    for i in range(targets["role_contrast"]):
        cases.append(gen_role_contrast(i))
    for i in range(targets["single_constraint"]):
        cases.append(gen_single_constraint(i))
    for i in range(targets["multi_constraint"]):
        cases.append(gen_multi_constraint(i))

    half_boundary = targets["boundary"] // 2
    for i in range(half_boundary):
        cases.append(gen_boundary(i))
    for i in range(targets["rare_diversity"] // 2):
        cases.append(gen_rare_ship_type(i))
    for i in range(targets["rare_diversity"] - targets["rare_diversity"] // 2):
        cases.append(gen_diversity_pair(i))
    for i in range(targets["boundary"] - half_boundary):
        cases.append(gen_boundary(i + half_boundary))

    rng.shuffle(cases)
    return cases


def build_dataset(
    count: int,
    output_dir: Path,
    schema_path: Path,
    seed: int = 42,
) -> list[dict[str, Any]]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    cases = build_cases(count, seed=seed)
    for index, case in enumerate(cases):
        case["split"] = split_for(case, index)

    rng = random.Random(seed + 1)
    rows: list[dict[str, Any]] = []
    status_counts: Counter = Counter()
    category_counts: Counter = Counter()
    disagreements = 0

    for case in cases:
        classic = check_contradiction(
            case["ship_type"], int(case["year"]), case["nation"],
            constraints=case.get("constraints"), raw_prompt=case.get("request"),
        )
        jitter_strength = 0.5 if case["category"].startswith("replay") else 1.0
        row = build_record(case, schema, rng, jitter_strength=jitter_strength)
        classic_impossible = classic.get("status") == "impossible"
        if (row["expected_status"] == "impossible") != classic_impossible:
            disagreements += 1
        rows.append(row)
        status_counts[row["expected_status"]] += 1
        category_counts[row["category"]] += 1

    write_splits(output_dir, rows)
    write_manifest(output_dir, rows, status_counts, category_counts, disagreements, count, seed)
    return rows


def write_splits(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for row in rows:
        by_split[row["split"]].append(row)
    for split in ("train", "validation", "test"):
        write_jsonl(output_dir / f"{split}.jsonl", by_split[split])
    write_jsonl(output_dir / "valid.jsonl", by_split["validation"])
    write_jsonl(output_dir / "all.jsonl", rows)


def write_manifest(
    output_dir: Path,
    rows: list[dict[str, Any]],
    status_counts: Counter,
    category_counts: Counter,
    disagreements: int,
    requested: int,
    seed: int,
) -> None:
    split_counts = Counter(row["split"] for row in rows)
    manifest = {
        "dataset": "stage3_numeric_quality_v0",
        "schema": "schemas/navspec_lora_candidate_stage2.schema.json",
        "schema_version": "navspec.lora_candidate.v1",
        "teacher": "navspec-classic-v1 + controlled numeric jitter within valid bounds",
        "rows": len(rows),
        "requested": requested,
        "seed": seed,
        "split_counts": dict(sorted(split_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "classic_classifier_disagreements": disagreements,
        "system_prompt": SYSTEM_PROMPT,
        "format": "mlx_lm ChatDataset/messages, compact JSON sort_keys=True, assistant ends with <|im_end|>",
        "jitter": {
            "replay_strength": 0.5,
            "normal_strength": 1.0,
            "fields": [
                "standard_displacement_t ±5%",
                "speed_kt: canonical (no jitter)",
                "main_gun_caliber_mm ±2mm",
                "belt_armor_mm ±5mm/5%",
                "range_nm ±5%",
                "main_gun_count ±1",
                "power_shp: recalculated from canonical speed + jittered displacement",
            ],
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# ---- eval case builder ------------------------------------------------------

EVAL_COMPOSITION = {
    "normal_by_ship_type": 300,
    "era_contrast": 150,
    "nation_tier_contrast": 100,
    "role_contrast": 100,
    "single_constraint": 100,
    "multi_constraint": 100,
    "boundary": 50,
    "rare_ship_type": 50,
    "diversity_repeated": 50,
}


def build_eval_cases(output_dir: Path, seed: int = 99) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []

    for i in range(EVAL_COMPOSITION["normal_by_ship_type"]):
        cases.append(gen_normal_numeric(9000 + i))
    for i in range(EVAL_COMPOSITION["era_contrast"]):
        cases.append(gen_era_contrast(9000 + i))
    for i in range(EVAL_COMPOSITION["nation_tier_contrast"]):
        cases.append(gen_nation_contrast(9000 + i))
    for i in range(EVAL_COMPOSITION["role_contrast"]):
        cases.append(gen_role_contrast(9000 + i))
    for i in range(EVAL_COMPOSITION["single_constraint"]):
        cases.append(gen_single_constraint(9000 + i))
    for i in range(EVAL_COMPOSITION["multi_constraint"]):
        cases.append(gen_multi_constraint(9000 + i))
    for i in range(EVAL_COMPOSITION["boundary"]):
        cases.append(gen_boundary(9000 + i))
    for i in range(EVAL_COMPOSITION["rare_ship_type"]):
        cases.append(gen_rare_ship_type(9000 + i))
    for i in range(EVAL_COMPOSITION["diversity_repeated"]):
        cases.append(gen_diversity_pair(9000 + i))

    for idx, case in enumerate(cases):
        case["id"] = f"stage3_eval_{idx:04d}"
        case["split"] = "eval"

    rows: list[dict[str, Any]] = []
    for case in cases:
        canonical_output = make_ok_output(case)
        contradiction = check_contradiction(
            case["ship_type"], int(case["year"]), case["nation"],
            constraints=case.get("constraints"), raw_prompt=case.get("request"),
        )
        if contradiction.get("status") == "impossible":
            canonical_output = make_stage2_output(case)
        errors = validate_stage2(canonical_output)
        if errors:
            raise ValueError(f"{case['id']} eval canonical invalid: {errors[:5]}")
        request = make_request(case)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": compact(request)},
            {"role": "assistant", "content": compact(canonical_output)},
        ]
        rows.append({
            "id": case["id"],
            "category": case["category"],
            "nation": case["nation"],
            "year": int(case["year"]),
            "ship_type": case["ship_type"],
            "role": case.get("role", ""),
            "constraints": case.get("constraints", {}),
            "request": case["request"],
            "input": request,
            "expected_status": canonical_output["status"],
            "expected_reason_codes": [item["code"] for item in canonical_output["reason"]],
            "messages": messages,
            "text": conversation_text(messages),
            "output": canonical_output,
            "expected_output": canonical_output,
            "split": "eval",
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "cases.jsonl", rows)

    status_counts = Counter(r["expected_status"] for r in rows)
    category_counts = Counter(r["category"] for r in rows)
    manifest = {
        "dataset": "stage3_numeric_eval_v0",
        "rows": len(rows),
        "seed": seed,
        "status_counts": dict(sorted(status_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "teacher": "navspec-classic-v1 canonical (no jitter — baseline for patch measurement)",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build navspec Stage 3 numeric quality datasets")
    parser.add_argument("--count", type=int, default=40000)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval", action="store_true", help="Generate eval cases instead of training data")
    parser.add_argument("--eval-dir", type=Path, default=DEFAULT_EVAL_DIR)
    args = parser.parse_args()

    if args.eval:
        rows = build_eval_cases(args.eval_dir, seed=99)
        print(f"eval cases: {len(rows)}")
        print(f"output_dir: {args.eval_dir}")
        return

    if args.output_dir is None:
        args.output_dir = DEFAULT_OUTPUT_DIR

    rows = build_dataset(args.count, args.output_dir, args.schema, seed=args.seed)
    manifest = json.loads((args.output_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"dataset: stage3_numeric_quality_v0")
    print(f"rows: {len(rows)}")
    print(f"output_dir: {args.output_dir}")
    print("splits:", manifest["split_counts"])
    print("status:", manifest["status_counts"])
    print("top categories:", dict(sorted(manifest["category_counts"].items(), key=lambda kv: kv[1], reverse=True)[:12]))
    print("classic disagreements:", manifest["classic_classifier_disagreements"])


if __name__ == "__main__":
    main()
