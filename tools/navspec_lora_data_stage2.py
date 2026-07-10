"""Build navspec Stage 2 (status + reason code) LoRA datasets.

Stage 1 (schema_only_v0) は JSON envelope の習得が目的だった。Stage 2 は:
  - status=ok / impossible の判定
  - impossible 時の固定 reason code (tools/navspec_lora_stage2_schema.REASON_CODES)
  - suggested_revision の生成
  - hard negative (厳しいが可能) を ok として学ばせ、過剰拒否を防ぐ

出力フォーマットは Stage 2 schema (reason=[{code,message}], impossible は spec={})。
全 teacher 出力は validate_stage2 で検証し、1件でも不正なら生成を中止する。

classic rule engine (check_contradiction) を status 判定の権威として使い、
reason code は制約/諸元/tier/era から機械的に再導出する。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_constraints import parse_constraints  # noqa: E402
from navspec_contradiction import (  # noqa: E402
    _estimate_required_power,
    _fuel_volume_fraction,
    _suggest_revision,
    check_contradiction,
)
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
    load_classic_eval_cases,
    make_ok_output,
    make_request,
    request_prompt,
    split_for,
    write_jsonl,
)
from navspec_lora_stage2_schema import REASON_CODES, validate_stage2  # noqa: E402
from navspec_nation_tier import (  # noqa: E402
    get_nation_tier,
    get_tier_limits,
    is_capital_ship,
)
from navspec_shiptypes import (  # noqa: E402
    SHIP_TYPE_MAIN_GUN_CLAMP,
    SHIP_TYPE_SPEED_CLAMP,
    get_ship_type_specs,
    hull_kind_for_ship_type,
)
from validate_navspec_output import validate_schema  # noqa: E402

DEFAULT_OUTPUT_DIR = ROOT / "data" / "navspec" / "lora" / "stage2_status_v0"
DEFAULT_EVAL_DIR = ROOT / "data" / "navspec" / "lora" / "stage2_eval_v0"

SMALL_SHIP_TYPES = {
    "destroyer", "large_destroyer", "anti_air_destroyer", "destroyer_escort",
    "patrol_frigate", "frigate", "corvette", "sloop", "patrol_boat", "gunboat",
    "torpedo_boat", "minesweeper",
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def classify_reason_codes(case: dict[str, Any]) -> list[dict[str, str]]:
    """制約/諸元/tier/era から固定 reason code を機械的に導出する。

    check_contradiction と同じ信号を使うが、自由文ではなく code を出す。
    """
    ship_type = case["ship_type"]
    year = int(case["year"])
    nation = case["nation"]
    role = case.get("role", "")
    constraints = parse_constraints(case.get("constraints") or {})
    specs = get_ship_type_specs(ship_type)
    tier = get_nation_tier(nation)
    limits = get_tier_limits(nation)
    hull = hull_kind_for_ship_type(ship_type)

    req_gun_min = _num(constraints.get("main_gun_caliber_mm_min"))
    req_speed_min = _num(constraints.get("speed_kt_min"))
    req_belt_min = _num(constraints.get("belt_armor_mm_min"))
    req_range_min = _num(constraints.get("range_nm_min"))
    req_disp_max = _num(constraints.get("standard_displacement_t_max"))
    req_disp_min = _num(constraints.get("standard_displacement_t_min"))
    req_barrels_min = constraints.get("main_gun_barrels_min")

    eff_disp = req_disp_max if req_disp_max is not None else float(specs["normal_t"])
    eff_speed = req_speed_min if req_speed_min is not None else float(specs["speed"])
    eff_gun = req_gun_min if req_gun_min is not None else float(specs["main_caliber_mm"])
    eff_belt = req_belt_min if req_belt_min is not None else float(specs.get("belt_mm") or 0)

    _, gun_hi = SHIP_TYPE_MAIN_GUN_CLAMP.get(ship_type, (76, 520))
    _, spd_hi = SHIP_TYPE_SPEED_CLAMP.get(ship_type, (5, 45))
    is_small = ship_type in SMALL_SHIP_TYPES or eff_disp < 3500

    codes: list[str] = []

    if req_gun_min is not None and req_gun_min > gun_hi + 5:
        codes.append("main_gun_too_large_for_hull")
    if req_barrels_min is not None and req_gun_min is not None and req_gun_min >= 180 and int(req_barrels_min) >= 6 and is_small:
        if "main_gun_too_large_for_hull" not in codes:
            codes.append("main_gun_too_large_for_hull")

    if req_belt_min is not None and req_belt_min > 0 and eff_disp < 3000 and req_belt_min > 50:
        codes.append("armor_too_heavy_for_hull")

    if req_speed_min is not None and eff_disp > 0:
        if req_speed_min > spd_hi + 1:
            codes.append("speed_power_conflict")
        elif req_speed_min >= 38 and eff_disp > 3000:
            codes.append("speed_power_conflict")

    if req_belt_min is not None and req_belt_min > 100 and req_speed_min is not None and req_speed_min >= 35 and eff_disp < 5000:
        codes.append("speed_armor_range_conflict")

    combined_flags = [
        is_small,
        eff_gun >= 180,
        eff_belt >= 100,
        eff_speed >= 34,
        req_range_min is not None and req_range_min >= 8000,
    ]
    if all(combined_flags) and "speed_armor_range_conflict" not in codes:
        codes.append("speed_armor_range_conflict")

    if req_range_min is not None and req_range_min >= 8000 and eff_speed > 0 and eff_disp > 0:
        frac = _fuel_volume_fraction(req_range_min, eff_speed, eff_disp)
        if frac > 0.55 or (eff_disp < 5000 and frac > 0.35):
            codes.append("range_volume_conflict")

    era_violated = False
    if constraints.get("radar_required") and year < 1938:
        era_violated = True
    if constraints.get("diesel_required") and year < 1910:
        era_violated = True
    if constraints.get("catapult_required") and year < 1925:
        era_violated = True
    if constraints.get("dual_purpose_gun_required") and year < 1925:
        era_violated = True
    if constraints.get("radar_required") and year < limits["radar_from_year"]:
        era_violated = True
    if era_violated:
        codes.append("era_equipment_unavailable")

    treaty_violated = False
    if 1922 <= year <= 1936:
        if eff_disp > 35000 and is_capital_ship(ship_type):
            treaty_violated = True
        if eff_gun > 406 and is_capital_ship(ship_type):
            treaty_violated = True
        if ("cruiser" in ship_type or constraints.get("treaty_limited")) and eff_disp > 10000 and not is_capital_ship(ship_type):
            treaty_violated = True
        if ("cruiser" in ship_type or constraints.get("treaty_limited")) and eff_gun > 203 and not is_capital_ship(ship_type):
            treaty_violated = True
    if treaty_violated:
        codes.append("treaty_limit_exceeded")

    cap_violated = False
    if is_capital_ship(ship_type) and not limits["battleship_allowed"]:
        cap_violated = True
    max_td = limits["max_displacement_t"]
    if max_td is not None and eff_disp > max_td * 1.1:
        cap_violated = True
    max_tc = limits["max_main_caliber_mm"]
    if max_tc is not None and eff_gun > max_tc + 5:
        cap_violated = True
    if cap_violated:
        codes.append("nation_capability_exceeded")

    if constraints.get("torpedo_required") and hull in {"battleship", "battlecruiser"}:
        codes.append("ship_type_role_conflict")
    elif role in {"fleet_air_defense", "carrier_screen"} and ship_type in {"ship_of_the_line", "pre_dreadnought_battleship"}:
        codes.append("ship_type_role_conflict")

    if req_disp_min is not None and req_disp_max is not None and req_disp_min > req_disp_max:
        codes.append("displacement_limit_exceeded")

    deduped: list[str] = []
    for code in codes:
        if code not in deduped:
            deduped.append(code)
    return [{"code": code, "message": _message_for(code, case, specs, limits, tier)} for code in deduped]


_MESSAGE_TEMPLATE = {
    "main_gun_too_large_for_hull": "指定された船体規模に対して主砲口径が過大です",
    "armor_too_heavy_for_hull": "船体規模に対して装甲帯が過重です",
    "speed_power_conflict": "要求速力に必要な機関出力が船体に対して過大です",
    "speed_armor_range_conflict": "高速・重装甲・長航続を同時に満たす重量配分が成立しません",
    "range_volume_conflict": "要求航続距離に対する燃料体積が排水量の許容を超えます",
    "era_equipment_unavailable": "要求年代では未実用化の装備が含まれています",
    "treaty_limit_exceeded": "条約制限(排水量または主砲口径)を超過しています",
    "nation_capability_exceeded": "国家の工業力(tier)を超える建造要求です",
    "ship_type_role_conflict": "艦種と要求ロールが技術的に両立しません",
    "displacement_limit_exceeded": "排水量制約の上下限が矛盾しています",
}


def _message_for(code: str, case: dict[str, Any], specs: dict[str, Any], limits: dict[str, Any], tier: int) -> str:
    return _MESSAGE_TEMPLATE.get(code, code)


def make_stage2_impossible_output(case: dict[str, Any], reason_codes: list[dict[str, str]], revision: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "navspec.lora_candidate.v1",
        "status": "impossible",
        "spec": {},
        "reason": reason_codes,
        "suggested_revision": revision,
        "design_concept": "要求条件のうち成立しない要素を緩和し、同じ艦種・年代・国家に近い実現可能案へ修正する。",
        "confidence": 0.94,
    }


def make_stage2_output(case: dict[str, Any]) -> dict[str, Any]:
    """case から Stage 2 正解出力を生成する。status は reason code が空か否かで決まる。

    classic check_contradiction と交差検査し、分類が一致しない場合は classic 側の
    判定を優先してコードを補う(teacher と classic の整合を保つため)。
    """
    reason_codes = classify_reason_codes(case)
    contradiction = check_contradiction(
        case["ship_type"],
        int(case["year"]),
        case["nation"],
        constraints=case.get("constraints"),
        raw_prompt=case.get("request"),
    )
    classic_impossible = contradiction.get("status") == "impossible"

    if reason_codes:
        status = "impossible"
    elif classic_impossible:
        status = "impossible"
        reason_codes = [{"code": "displacement_limit_exceeded", "message": contradiction.get("reason", ["要求が成立しない"])[0] if contradiction.get("reason") else "要求が成立しない"}]
    else:
        status = "ok"

    if status == "ok":
        return make_ok_output(case)

    if not reason_codes:
        reason_codes = [{"code": "displacement_limit_exceeded", "message": "要求が成立しない"}]
    parsed = parse_constraints(case.get("constraints"))
    revision = _suggest_revision(
        case["ship_type"],
        int(case["year"]),
        case["nation"],
        get_ship_type_specs(case["ship_type"]),
        parsed,
        get_tier_limits(case["nation"]),
        get_nation_tier(case["nation"]),
    )
    return make_stage2_impossible_output(case, reason_codes, revision)


def build_record(case: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    request = make_request(case)
    output = make_stage2_output(case)
    errors = validate_stage2(output)
    if errors:
        raise ValueError(f"{case.get('id')} stage2 teacher invalid: {errors[:5]}")
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


# ---- case family generators -------------------------------------------------

def _base_case(index: int, category: str, nation: str, year: int, ship_type: str, role: str, constraints: dict[str, Any], request: str) -> dict[str, Any]:
    return {
        "id": f"stage2_{category}_{index:05d}",
        "category": category,
        "nation": nation,
        "year": int(year),
        "ship_type": ship_type,
        "role": role,
        "constraints": constraints,
        "request": request,
    }


def gen_small_gun(index: int) -> dict[str, Any]:
    st = ["patrol_boat", "gunboat", "minesweeper", "corvette", "destroyer_escort"][index % 5]
    nation = NATIONS[(index * 3) % len(NATIONS)]
    year = YEARS[(index * 5) % len(YEARS)]
    gun = [180, 203, 254, 305][index % 4]
    disp_max = [300, 500, 700, 1000][index % 4]
    constraints = {"standard_displacement_t_max": disp_max, "main_gun_caliber_mm_min": gun}
    request = f"{year}年の{nation}向け{st}に{gun}mm主砲を{disp_max}t以下で要求する。JSONのみ。"
    return _base_case(index, "impossible_small_gun", nation, year, st, "fleet_battle", constraints, request)


def gen_armor_heavy(index: int) -> dict[str, Any]:
    st = ["destroyer", "frigate", "corvette", "large_destroyer"][index % 4]
    nation = NATIONS[(index * 7) % len(NATIONS)]
    year = YEARS[(index * 2) % len(YEARS)]
    belt = [100, 150, 200, 250][index % 4]
    disp_max = [1200, 1500, 1800, 2200][index % 4]
    constraints = {"standard_displacement_t_max": disp_max, "belt_armor_mm_min": belt}
    request = f"{year}年の{nation}向け{st}に{belt}mm装甲帯を{disp_max}t以下で要求する。JSONのみ。"
    return _base_case(index, "impossible_armor", nation, year, st, "fleet_battle", constraints, request)


def gen_speed_armor_range(index: int) -> dict[str, Any]:
    st = ["destroyer", "large_destroyer", "anti_air_destroyer"][index % 3]
    nation = NATIONS[(index * 11) % len(NATIONS)]
    year = YEARS[(index + 3) % len(YEARS)]
    constraints = {"standard_displacement_t_max": 1500, "speed_kt_min": 40, "belt_armor_mm_min": 120, "range_nm_min": 10000}
    request = f"{year}年の{nation}向け{st}に40kt・120mm装甲・10000海里を1500t以下で同時要求する。JSONのみ。"
    return _base_case(index, "impossible_speed_armor_range", nation, year, st, "night_battle", constraints, request)


def gen_era(index: int) -> dict[str, Any]:
    kind = index % 4
    st = ["protected_cruiser", "destroyer", "light_cruiser", "pre_dreadnought_battleship"][kind]
    nation = NATIONS[(index * 13) % len(NATIONS)]
    year = [1890, 1900, 1905, 1920][kind]
    constraints: dict[str, Any] = {}
    label = ""
    if kind == 0:
        constraints = {"radar_required": True, "speed_kt_min": 40}
        label = "レーダーと40kt"
    elif kind == 1:
        constraints = {"diesel_required": True, "dual_purpose_gun_required": True}
        label = "ディーゼルと両用砲"
    elif kind == 2:
        constraints = {"radar_required": True, "catapult_required": True}
        label = "レーダーとカタパルト"
    else:
        constraints = {"radar_required": True, "anti_air_priority": True}
        label = "レーダーと対空重視"
    request = f"{year}年の{nation}向け{st}に{label}を要求する。JSONのみ。"
    return _base_case(index, "impossible_era", nation, year, st, "early_electronics", constraints, request)


def gen_treaty(index: int) -> dict[str, Any]:
    kind = index % 4
    nation = ["United States", "United Kingdom", "Japan", "France"][kind]
    year = [1925, 1928, 1930, 1934][kind]
    if kind < 2:
        st = "treaty_cruiser"
        constraints = {"standard_displacement_t_min": 12000 + (index % 3) * 2000, "main_gun_caliber_mm_min": 254}
    else:
        st = "heavy_cruiser"
        constraints = {"standard_displacement_t_min": 14000, "main_gun_caliber_mm_min": 280, "treaty_limited": True}
    request = f"{year}年の条約期に{nation}向け{st}を{constraints['standard_displacement_t_min']}t以上・{constraints['main_gun_caliber_mm_min']}mm砲で要求する。JSONのみ。"
    return _base_case(index, "impossible_treaty", nation, year, st, "treaty_limit_test", constraints, request)


def gen_nation(index: int) -> dict[str, Any]:
    kind = index % 4
    nation = ["Chile", "Thailand", "Portugal", "Egypt"][(index + kind) % 4]
    year = YEARS[(index + 5) % len(YEARS)]
    if kind == 0:
        st = "battleship"
        constraints = {}
    elif kind == 1:
        st = "battlecruiser"
        constraints = {"standard_displacement_t_min": 25000}
    elif kind == 2:
        st = "battleship"
        constraints = {"main_gun_caliber_mm_min": 381}
    else:
        st = "fast_battleship"
        constraints = {"standard_displacement_t_min": 30000}
    request = f"{year}年の{nation}が自主建造で{st}を要求する。JSONのみ。"
    return _base_case(index, "impossible_nation", nation, year, st, "regional_flagship", constraints, request)


def gen_speed_power(index: int) -> dict[str, Any]:
    st = ["light_cruiser", "heavy_cruiser", "large_cruiser"][index % 3]
    nation = NATIONS[(index * 9) % len(NATIONS)]
    year = [1938, 1941, 1944][index % 3]
    disp_min = [8000, 10000, 12000][index % 3]
    constraints = {"standard_displacement_t_min": disp_min, "speed_kt_min": 39 + (index % 3)}
    request = f"{year}年の{nation}向け{st}に{constraints['speed_kt_min']}ktを{disp_min}t級で要求する。JSONのみ。"
    return _base_case(index, "impossible_speed_power", nation, year, st, "commerce_raiding", constraints, request)


def gen_displacement(index: int) -> dict[str, Any]:
    st = ["destroyer", "light_cruiser", "patrol_frigate"][index % 3]
    nation = NATIONS[(index * 4) % len(NATIONS)]
    year = YEARS[(index + 1) % len(YEARS)]
    constraints = {"standard_displacement_t_min": 5000, "standard_displacement_t_max": 2000}
    request = f"{year}年の{nation}向け{st}に排水量5000t以上かつ2000t以下を要求する。JSONのみ。"
    return _base_case(index, "impossible_displacement", nation, year, st, "convoy_escort", constraints, request)


def gen_combined(index: int) -> dict[str, Any]:
    st = ["destroyer", "large_destroyer", "anti_air_destroyer"][index % 3]
    nation = NATIONS[(index * 6) % len(NATIONS)]
    year = [1936, 1938, 1941][index % 3]
    constraints = {
        "standard_displacement_t_max": 1500,
        "speed_kt_min": 40,
        "belt_armor_mm_min": 150,
        "range_nm_min": 10000,
        "main_gun_caliber_mm_min": 203,
        "main_gun_barrels_min": 6,
    }
    request = f"{year}年の{nation}向け{st}に20.3cm砲6門・150mm装甲・40kt・10000海里を1500t以下で同時要求する。JSONのみ。"
    return _base_case(index, "impossible_combined", nation, year, st, "impossible_super_destroyer", constraints, request)


IMPOSSIBLE_GENS = [
    ("impossible_small_gun", gen_small_gun),
    ("impossible_armor", gen_armor_heavy),
    ("impossible_speed_armor_range", gen_speed_armor_range),
    ("impossible_era", gen_era),
    ("impossible_treaty", gen_treaty),
    ("impossible_nation", gen_nation),
    ("impossible_speed_power", gen_speed_power),
    ("impossible_displacement", gen_displacement),
]


_HARD_NEGATIVE_ARCHETYPES = [
    ("large_destroyer", 2200, 36, 127, "fleet_battle"),
    ("coastal_defense_ship", 9000, 22, 254, "coastal_defense"),
    ("heavy_cruiser", 10000, 32, 203, "fleet_battle"),
    ("destroyer_escort", 1500, 25, 102, "convoy_escort"),
    ("heavy_cruiser", 14000, 34, 203, "commerce_raiding"),
]

# hard negative は「厷しいが可能」を ok で学ばせるため、tier-1 大国のみを使う
# (tier-3/4 に当たると nation_capability_exceeded で impossible になってしまう)。
# 年は条約期(1922-1936)を避け、沿岸防御艦は22kt(艦種上限内)へ調整して classic と整合させる。
_HARD_NEGATIVE_NATIONS = ["United States", "United Kingdom", "Germany", "Japan", "France", "Italy"]
_HARD_NEGATIVE_YEARS = [1938, 1941, 1944, 1939]


def gen_hard_negative(index: int) -> dict[str, Any]:
    st, disp, speed, gun, role = _HARD_NEGATIVE_ARCHETYPES[index % len(_HARD_NEGATIVE_ARCHETYPES)]
    nation = _HARD_NEGATIVE_NATIONS[index % len(_HARD_NEGATIVE_NATIONS)]
    year = _HARD_NEGATIVE_YEARS[index % len(_HARD_NEGATIVE_YEARS)]
    constraints = {
        "standard_displacement_t_max": disp,
        "speed_kt_min": speed,
        "main_gun_caliber_mm_min": gun,
    }
    request = f"{year}年の{nation}向け{st}を{disp}t・{speed}kt・{gun}mm砲で設計せよ。JSONのみ。"
    return _base_case(index, "hard_negative_ok", nation, year, st, role, constraints, request)


def gen_normal_ok(index: int) -> dict[str, Any]:
    case = generated_case(index * 7 + 3)
    case["id"] = f"stage2_normal_ok_{index:05d}"
    case["category"] = "normal_ok"
    case["constraints"] = {}
    case["request"] = request_prompt(case["nation"], int(case["year"]), case["ship_type"], case.get("role", "fleet_battle"), {}, index)
    return case


def gen_schema_replay(index: int) -> dict[str, Any]:
    case = generated_case(index)
    case["id"] = f"stage2_replay_{index:05d}"
    case["category"] = f"replay_{case['category']}"
    return case


def _classic_replay_case(index: int) -> dict[str, Any]:
    cases = load_classic_eval_cases()
    case = cases[index % len(cases)]
    case["id"] = f"stage2_classic_replay_{index:05d}"
    case["category"] = f"replay_classic_{case['category']}"
    return case


def build_cases(count: int) -> list[dict[str, Any]]:
    ratios = {"schema_replay": 0.30, "normal_ok": 0.25, "impossible": 0.25, "reason_revision": 0.15, "hard_negative": 0.05}
    targets = {k: max(1, int(count * v)) for k, v in ratios.items()}

    cases: list[dict[str, Any]] = []
    for i in range(targets["schema_replay"]):
        cases.append(gen_classic_replay(i) if i % 3 == 0 else gen_schema_replay(i))
    for i in range(targets["normal_ok"]):
        cases.append(gen_normal_ok(i))
    per_cat = max(1, targets["impossible"] // len(IMPOSSIBLE_GENS))
    for cat_name, gen in IMPOSSIBLE_GENS:
        for i in range(per_cat):
            cases.append(gen(i))
    for i in range(targets["reason_revision"]):
        cases.append(gen_combined(i))
    for i in range(targets["hard_negative"]):
        cases.append(gen_hard_negative(i))
    return cases


def gen_classic_replay(index: int) -> dict[str, Any]:
    return _classic_replay_case(index)


def assign_splits(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, case in enumerate(cases):
        case["split"] = split_for(case, index)
    return cases


def build_dataset(count: int, output_dir: Path, schema_path: Path) -> list[dict[str, Any]]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    cases = build_cases(count)
    assign_splits(cases)
    rows: list[dict[str, Any]] = []
    status_counts: Counter = Counter()
    code_counts: Counter = Counter()
    category_counts: Counter = Counter()
    disagreements = 0
    for case in cases:
        classic = check_contradiction(case["ship_type"], int(case["year"]), case["nation"], constraints=case.get("constraints"), raw_prompt=case.get("request"))
        row = build_record(case, schema)
        classic_impossible = classic.get("status") == "impossible"
        if (row["expected_status"] == "impossible") != classic_impossible:
            disagreements += 1
        rows.append(row)
        status_counts[row["expected_status"]] += 1
        category_counts[row["category"]] += 1
        for code in row["expected_reason_codes"]:
            code_counts[code] += 1
    write_splits(output_dir, rows)
    write_manifest(output_dir, rows, status_counts, code_counts, category_counts, disagreements, count)
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
    code_counts: Counter,
    category_counts: Counter,
    disagreements: int,
    requested: int,
) -> None:
    split_counts = Counter(row["split"] for row in rows)
    manifest = {
        "dataset": "stage2_status_v0",
        "schema": "schemas/navspec_lora_candidate_stage2.schema.json",
        "schema_version": "navspec.lora_candidate.v1",
        "teacher": "navspec-classic-v1 rule engine + reason-code classifier",
        "rows": len(rows),
        "requested": requested,
        "split_counts": dict(sorted(split_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "reason_code_counts": dict(sorted(code_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "classic_classifier_disagreements": disagreements,
        "system_prompt": SYSTEM_PROMPT,
        "format": "mlx_lm ChatDataset/messages, compact JSON sort_keys=True, assistant ends with <|im_end|>",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_eval_cases(count: int, output_dir: Path) -> list[dict[str, Any]]:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    gens = [
        ("eval_small_gun", gen_small_gun),
        ("eval_armor", gen_armor_heavy),
        ("eval_speed_armor_range", gen_speed_armor_range),
        ("eval_era", gen_era),
        ("eval_treaty", gen_treaty),
        ("eval_nation", gen_nation),
        ("eval_hard_negative_ok", gen_hard_negative),
        ("eval_normal_ok", gen_normal_ok),
    ]
    per_cat = max(20, count // len(gens))
    cases: list[dict[str, Any]] = []
    for cat_name, gen in gens:
        base = 7000 if "ok" in cat_name else 8000
        for i in range(per_cat):
            case = gen(base + i)
            case["id"] = f"{cat_name}_{i:04d}"
            case["category"] = cat_name
            cases.append(case)
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        row = build_record(case, schema)
        row["split"] = "eval"
        rows.append(row)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "cases.jsonl", rows)
    write_manifest(output_dir, rows, Counter(r["expected_status"] for r in rows), Counter(c for r in rows for c in r["expected_reason_codes"]), Counter(r["category"] for r in rows), 0, count)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build navspec Stage 2 status/reason-code datasets")
    parser.add_argument("--count", type=int, default=20000)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--eval", action="store_true", help="Stage 2 専用評価ケースセットを生成する")
    parser.add_argument("--eval-count", type=int, default=480)
    parser.add_argument("--eval-dir", type=Path, default=DEFAULT_EVAL_DIR)
    args = parser.parse_args()

    if args.eval:
        rows = build_eval_cases(args.eval_count, args.eval_dir)
        print(f"eval cases: {len(rows)}")
        print(f"output_dir: {args.eval_dir}")
        return

    rows = build_dataset(args.count, args.output_dir, args.schema)
    manifest = json.loads((args.output_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"dataset: stage2_status_v0")
    print(f"rows: {len(rows)}")
    print(f"output_dir: {args.output_dir}")
    print("splits:", manifest["split_counts"])
    print("status:", manifest["status_counts"])
    print("reason_codes:", manifest["reason_code_counts"])
    print("top categories:", dict(sorted(manifest["category_counts"].items(), key=lambda kv: kv[1], reverse=True)[:10]))
    print("classic disagreements:", manifest["classic_classifier_disagreements"])


if __name__ == "__main__":
    main()
