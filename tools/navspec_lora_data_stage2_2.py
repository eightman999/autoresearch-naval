"""Build navspec Stage 2.2 balanced-reason LoRA dataset.

Stage 2.1 では複合reason学習で speed_armor_range_recall が低下し、長い reason 配列を
持つケースで JSON が途切れる問題が起きた。Stage 2.2 は:

- reason 定義を厳格化(section5): speed_armor_range_conflict は「速力+装甲+航続」の3要素
  が同時に存在する場合のみ付与。speed_power_conflict は機関出力が上限を超える場合のみ。
- 出力短縮(section7): reason message は定型短文、reason は最大3件、suggested_revision
  は変更フィールドのみ(rationaleなし)。
- canonical 順序(section6) は navspec_lora_stage2_schema.REASON_CODES に従う。
- 対照ペア(section9): impossible と近接 ok を対で生成し、境界判断を強化。
- 構成(section8): Stage2 replay 35% / schema-status replay 20% / speed_power 15% /
  speed_armor_range 15% / nation-treaty composite 10% / hard negative 5%。約5000件。

Stage2.1 の composite 比 30% は使わない。理由は学習対象を絞り出力を短く保つため。
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
from navspec_contradiction import check_contradiction  # noqa: E402
from navspec_lora_data import (  # noqa: E402
    DEFAULT_SCHEMA,
    SYSTEM_PROMPT,
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

DEFAULT_OUTPUT_DIR = ROOT / "data" / "navspec" / "lora" / "stage2_2_balanced_reason_v0"
DEFAULT_EVAL_DIR = ROOT / "data" / "navspec" / "lora" / "stage2_2_speed_eval_v0"
MAX_REASON = 3
MAX_MESSAGE_LEN = 60

SMALL_SHIP_TYPES = {
    "destroyer", "large_destroyer", "anti_air_destroyer", "destroyer_escort",
    "patrol_frigate", "frigate", "corvette", "sloop", "patrol_boat", "gunboat",
    "torpedo_boat", "minesweeper",
}

_MESSAGE = {
    "main_gun_too_large_for_hull": "主砲口径が船体規模に過大です",
    "armor_too_heavy_for_hull": "船体規模に対し装甲が過重です",
    "speed_power_conflict": "要求速力の機関出力が上限を超えます",
    "speed_armor_range_conflict": "速力・装甲・航続を同時に満たせません",
    "range_volume_conflict": "航続距離の燃料体積が許容を超えます",
    "era_equipment_unavailable": "要求年代で未実用化の装備です",
    "treaty_limit_exceeded": "条約制限を超過しています",
    "nation_capability_exceeded": "国家工業力を超える要求です",
    "ship_type_role_conflict": "艦種とロールが両立しません",
    "displacement_limit_exceeded": "排水量制約が矛盾しています",
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def classify_reason_codes(case: dict[str, Any]) -> list[str]:
    """Section5 厳格定義で reason code を導出する(固定 message は呼び側で付与)。"""
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

    _, gun_hi = SHIP_TYPE_MAIN_GUN_CLAMP.get(ship_type, (76, 520))
    _, spd_hi = SHIP_TYPE_SPEED_CLAMP.get(ship_type, (5, 45))
    is_small = ship_type in SMALL_SHIP_TYPES or eff_disp < 3500

    codes: list[str] = []

    # main_gun_too_large_for_hull: 船体に対する砲口径過大(単独成立)
    if req_gun_min is not None and req_gun_min > gun_hi + 5:
        codes.append("main_gun_too_large_for_hull")
    elif req_barrels_min is not None and req_gun_min is not None and req_gun_min >= 180 and int(req_barrels_min) >= 6 and is_small:
        codes.append("main_gun_too_large_for_hull")

    # armor_too_heavy_for_hull: 装重量そのものが船体に過大(単独成立)
    if req_belt_min is not None and req_belt_min > 0 and eff_disp < 3000 and req_belt_min > 50:
        codes.append("armor_too_heavy_for_hull")

    # speed_armor_range_conflict: 速力+装甲+航続の3要素が同一船体で同時不可の場合のみ
    # section5.5: この三複合時は速度系コードは speed_armor_range で代表し speed_power とは排他。
    has_speed = req_speed_min is not None and req_speed_min >= 35
    has_armor = req_belt_min is not None and req_belt_min > 100
    has_range = req_range_min is not None and req_range_min >= 8000
    speed_armor_range_fired = has_speed and has_armor and has_range and eff_disp < 6000
    if speed_armor_range_fired:
        codes.append("speed_armor_range_conflict")

    # speed_power_conflict: 機関出力が上限を超える場合のみ(speed_armor_range と排他)
    if req_speed_min is not None and not speed_armor_range_fired:
        power_infeasible = False
        if req_speed_min > spd_hi + 1:
            power_infeasible = True
        elif req_speed_min >= 38 and eff_disp > 3000:
            power_infeasible = True
        elif eff_disp < 3000 and req_speed_min >= 40:
            power_infeasible = True
        elif tier >= 3 and req_speed_min > spd_hi:
            power_infeasible = True
        if power_infeasible:
            codes.append("speed_power_conflict")

    # range_volume_conflict: 燃料/容積競合で航続確保不可(長航続だけでは不可)
    if req_range_min is not None and req_range_min >= 8000 and eff_speed > 0 and eff_disp > 0:
        tons_per_hour = (eff_disp ** (2.0 / 3.0) * eff_speed ** 3 / 250.0) * 0.0003
        fuel_tons = tons_per_hour * (req_range_min / eff_speed)
        frac = fuel_tons / eff_disp
        if frac > 0.55 or (eff_disp < 5000 and frac > 0.35):
            codes.append("range_volume_conflict")

    # era_equipment_unavailable
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

    # treaty_limit_exceeded
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

    # nation_capability_exceeded
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

    # canonical 順序へソート + 最大3件に絞る
    ordered = sorted(set(codes), key=lambda c: REASON_CODES.index(c))
    return ordered[:MAX_REASON]


def _reason_objects(codes: list[str]) -> list[dict[str, str]]:
    return [{"code": c, "message": _MESSAGE[c]} for c in codes]


def _minimal_revision(case: dict[str, Any], codes: list[str]) -> dict[str, Any]:
    """section7.4: 変更が必要なフィールドのみ。rationale は持たせない。"""
    ship_type = case["ship_type"]
    year = int(case["year"])
    nation = case["nation"]
    specs = get_ship_type_specs(ship_type)
    constraints = parse_constraints(case.get("constraints") or {})
    limits = get_tier_limits(nation)
    rev: dict[str, Any] = {}
    code_set = set(codes)

    if {"main_gun_too_large_for_hull", "nation_capability_exceeded", "treaty_limit_exceeded"} & code_set:
        gun = specs["main_caliber_mm"]
        if "main_gun_too_large_for_hull" in code_set:
            _, gun_hi = SHIP_TYPE_MAIN_GUN_CLAMP.get(ship_type, (76, 520))
            gun = min(gun, gun_hi)
        if limits["max_main_caliber_mm"]:
            gun = min(gun, limits["max_main_caliber_mm"])
        rev["main_gun_caliber_mm"] = round(float(gun), 1)
    if {"armor_too_heavy_for_hull", "speed_armor_range_conflict"} & code_set:
        disp_for_belt = constraints.get("standard_displacement_t_max") or specs["normal_t"]
        rev["belt_armor_mm"] = round(min(float(constraints.get("belt_armor_mm_min", 50)), float(disp_for_belt) / 80), 1)
    if {"speed_power_conflict", "speed_armor_range_conflict"} & code_set:
        _, spd_hi = SHIP_TYPE_SPEED_CLAMP.get(ship_type, (5, 45))
        rev["speed_kt"] = round(min(float(constraints.get("speed_kt_min", spd_hi)), float(spd_hi)), 1)
    if {"range_volume_conflict", "speed_armor_range_conflict"} & code_set:
        rev["range_nm"] = 5000
    if {"nation_capability_exceeded", "displacement_limit_exceeded"} & code_set:
        if limits["max_displacement_t"]:
            rev["standard_displacement_t"] = int(limits["max_displacement_t"])
    return rev


def make_stage2_2_output(case: dict[str, Any]) -> dict[str, Any]:
    codes = classify_reason_codes(case)
    classic = check_contradiction(
        case["ship_type"], int(case["year"]), case["nation"],
        constraints=case.get("constraints"), raw_prompt=case.get("request"),
    )
    classic_impossible = classic.get("status") == "impossible"
    if not codes and classic_impossible:
        codes = ["displacement_limit_exceeded"]
    if not codes:
        return make_ok_output(case)
    objects = _reason_objects(codes)
    revision = _minimal_revision(case, codes)
    if not revision:
        revision = {"standard_displacement_t": int(get_ship_type_specs(case["ship_type"])["normal_t"])}
    return {
        "schema_version": "navspec.lora_candidate.v1",
        "status": "impossible",
        "spec": {},
        "reason": objects,
        "suggested_revision": revision,
        "design_concept": "成立しない要素を緩和し、同艦種・年代・国家で実現可能な案へ修正する。",
        "confidence": 0.93,
    }


# ---- teacher validation (section10) ----
def teacher_checks(obj: Any) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    errs = validate_stage2(obj)
    checks["stage2_schema"] = not errs
    if isinstance(obj, dict):
        reason = obj.get("reason")
        codes = [r.get("code") for r in reason] if isinstance(reason, list) else []
        checks["reason_max_3"] = isinstance(reason, list) and len(reason) <= MAX_REASON
        checks["reason_no_dup"] = isinstance(reason, list) and len(set(codes)) == len(codes)
        order_idx = [REASON_CODES.index(c) for c in codes if c in REASON_CODES]
        checks["reason_canonical_order"] = order_idx == sorted(order_idx)
        checks["message_len"] = isinstance(reason, list) and all(
            isinstance(r.get("message"), str) and len(r["message"]) <= MAX_MESSAGE_LEN for r in reason
        )
        status = obj.get("status")
        checks["ok_reason_empty"] = (status != "ok") or (reason == [])
        checks["ok_revision_empty"] = (status != "ok") or (obj.get("suggested_revision") == {})
        checks["imp_spec_empty"] = (status != "impossible") or (obj.get("spec") in ({}, None))
        checks["imp_reason_nonempty"] = (status != "impossible") or (isinstance(reason, list) and len(reason) >= 1)
    else:
        for k in ["reason_max_3", "reason_no_dup", "reason_canonical_order", "message_len",
                  "ok_reason_empty", "ok_revision_empty", "imp_spec_empty", "imp_reason_nonempty"]:
            checks[k] = False
    return checks


def build_record(case: dict[str, Any]) -> dict[str, Any]:
    request = make_request(case)
    output = make_stage2_2_output(case)
    errs = validate_stage2(output)
    if errs:
        raise ValueError(f"{case.get('id')} stage2.2 teacher invalid: {errs[:5]}")
    checks = teacher_checks(output)
    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise ValueError(f"{case.get('id')} teacher_checks failed: {failed}")
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
        "expected_reason_codes": [r["code"] for r in output["reason"]],
        "messages": messages,
        "text": conversation_text(messages),
        "output": output,
        "expected_output": output,
        "split": case.get("split", "train"),
    }


def _base(index: int, category: str, nation: str, year: int, ship_type: str, role: str, constraints: dict[str, Any], request: str) -> dict[str, Any]:
    return {"id": f"s22_{category}_{index:05d}", "category": category, "nation": nation,
            "year": int(year), "ship_type": ship_type, "role": role, "constraints": constraints, "request": request}


# ---- generators ----
def gen_replay_stage2(index: int) -> dict[str, Any]:
    case = generated_case(index)
    case["id"] = f"s22_replay_{index:05d}"
    case["category"] = f"replay_{case['category']}"
    return case


def gen_replay_classic(index: int) -> dict[str, Any]:
    cases = load_classic_eval_cases()
    case = cases[index % len(cases)]
    case["id"] = f"s22_replay_classic_{index:05d}"
    case["category"] = f"replay_classic_{case['category']}"
    return case


def gen_normal_ok(index: int) -> dict[str, Any]:
    case = generated_case(index * 7 + 3)
    case["id"] = f"s22_normal_ok_{index:05d}"
    case["category"] = "normal_ok"
    case["constraints"] = {}
    case["request"] = request_prompt(case["nation"], int(case["year"]), case["ship_type"], case.get("role", "fleet_battle"), {}, index)
    return case


# speed_power 重点 + 対照ok
def gen_speed_power_impossible(index: int) -> dict[str, Any]:
    kind = index % 3
    if kind == 0:
        st, nation, year = "destroyer", "Japan", 1940
        constraints = {"standard_displacement_t_max": 1200, "speed_kt_min": 45}
    elif kind == 1:
        st, nation, year = "protected_cruiser", "United Kingdom", 1895
        constraints = {"speed_kt_min": 40}
    else:
        st, nation, year = "destroyer", "Chile", 1941
        constraints = {"standard_displacement_t_max": 1500, "speed_kt_min": 42}
    request = f"{year}年の{nation}向け{st}に{constraints['speed_kt_min']}ktを要求する。JSONのみ。"
    return _base(index, "speed_power_impossible", nation, year, st, "fleet_battle", constraints, request)


def gen_speed_power_ok(index: int) -> dict[str, Any]:
    st, nation, year = "large_destroyer", "Germany", 1940
    constraints = {"standard_displacement_t_max": 2200, "speed_kt_min": 38}
    request = f"{year}年の{nation}向け{st}を2200t・38ktで設計せよ。JSONのみ。"
    return _base(index, "speed_power_ok", nation, year, st, "fleet_battle", constraints, request)


# speed_armor_range 重点(3要素必須)+ 対照ok
def gen_speed_armor_range_impossible(index: int) -> dict[str, Any]:
    st = ["destroyer", "large_destroyer"][index % 2]
    nation = ["United States", "Japan"][index % 2]
    year = 1938
    constraints = {"standard_displacement_t_max": 1500, "speed_kt_min": 40, "belt_armor_mm_min": 120, "range_nm_min": 10000}
    request = f"{year}年の{nation}向け{st}に40kt・120mm装甲・10000海里を1500t以下で要求する。JSONのみ。"
    return _base(index, "speed_armor_range_impossible", nation, year, st, "night_battle", constraints, request)


def gen_speed_armor_range_ok(index: int) -> dict[str, Any]:
    nation = ["United States", "Japan"][index % 2]
    year = 1938
    st = "large_destroyer"
    constraints = {"standard_displacement_t_max": 3500, "speed_kt_min": 35, "belt_armor_mm_min": 30, "range_nm_min": 6000}
    request = f"{year}年の{nation}向け{st}を3500t・35kt・軽装甲・6000海里で設計せよ。JSONのみ。"
    return _base(index, "speed_armor_range_ok", nation, year, st, "fleet_battle", constraints, request)


# nation/treaty composite(控えめ)
def gen_nation_treaty_impossible(index: int) -> dict[str, Any]:
    kind = index % 2
    if kind == 0:
        nation, st, year = "Chile", "commerce_raider_cruiser", 1928
        constraints = {}
        request = f"{year}年の条約期に{nation}向け{st}(28cm砲)を要求する。JSONのみ。"
    else:
        nation, st, year = "Egypt", "battleship", 1940
        constraints = {"main_gun_caliber_mm_min": 381}
        request = f"{year}年の{nation}が自主建造で381mm砲の{st}を要求する。JSONのみ。"
    return _base(index, "nation_treaty_impossible", nation, year, st, "regional_flagship", constraints, request)


# hard negative
_HARD = [("large_destroyer", 2200, 36, 127, "United States"), ("heavy_cruiser", 10000, 32, 203, "Japan"),
         ("destroyer_escort", 1500, 25, 102, "United Kingdom"), ("coastal_defense_ship", 9000, 22, 254, "France")]
_YEARS = [1938, 1941, 1944, 1939]


def gen_hard_negative(index: int) -> dict[str, Any]:
    st, disp, speed, gun, nation = _HARD[index % len(_HARD)]
    year = _YEARS[index % len(_YEARS)]
    constraints = {"standard_displacement_t_max": disp, "speed_kt_min": speed, "main_gun_caliber_mm_min": gun}
    request = f"{year}年の{nation}向け{st}を{disp}t・{speed}kt・{gun}mm砲で設計せよ。JSONのみ。"
    return _base(index, "hard_negative_ok", nation, year, st, "fleet_battle", constraints, request)


def build_cases(count: int) -> list[dict[str, Any]]:
    ratios = {"replay_stage2": 0.20, "replay_schema_status": 0.15, "normal_ok": 0.15,
              "speed_power": 0.17, "speed_armor_range": 0.17, "nation_treaty": 0.10, "hard_negative": 0.06}
    targets = {k: max(1, int(count * v)) for k, v in ratios.items()}

    cases: list[dict[str, Any]] = []
    for i in range(targets["replay_stage2"]):
        cases.append(gen_replay_classic(i) if i % 3 == 0 else gen_replay_stage2(i))
    for i in range(targets["replay_schema_status"]):
        cases.append(gen_replay_stage2(10000 + i))
    for i in range(targets["normal_ok"]):
        cases.append(gen_normal_ok(i))
    half_sp = targets["speed_power"] // 2
    for i in range(half_sp):
        cases.append(gen_speed_power_impossible(i))
    for i in range(targets["speed_power"] - half_sp):
        cases.append(gen_speed_power_ok(i))
    half_sar = targets["speed_armor_range"] // 2
    for i in range(half_sar):
        cases.append(gen_speed_armor_range_impossible(i))
    for i in range(targets["speed_armor_range"] - half_sar):
        cases.append(gen_speed_armor_range_ok(i))
    for i in range(targets["nation_treaty"]):
        cases.append(gen_nation_treaty_impossible(i))
    for i in range(targets["hard_negative"]):
        cases.append(gen_hard_negative(i))
    return cases


def assign_splits(cases: list[dict[str, Any]]) -> None:
    for index, case in enumerate(cases):
        case["split"] = split_for(case, index)


def build_dataset(count: int, output_dir: Path) -> list[dict[str, Any]]:
    schema = json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))
    cases = build_cases(count)
    assign_splits(cases)
    rows: list[dict[str, Any]] = []
    status_counts: Counter = Counter()
    code_counts: Counter = Counter()
    cat_counts: Counter = Counter()
    disagreements = 0
    for case in cases:
        classic = check_contradiction(case["ship_type"], int(case["year"]), case["nation"], constraints=case.get("constraints"), raw_prompt=case.get("request"))
        row = build_record(case)
        if (row["expected_status"] == "impossible") != (classic.get("status") == "impossible"):
            disagreements += 1
        rows.append(row)
        status_counts[row["expected_status"]] += 1
        cat_counts[row["category"]] += 1
        for c in row["expected_reason_codes"]:
            code_counts[c] += 1
    _ = schema  # kept for interface parity; validate_stage2 is schemaless
    write_splits(output_dir, rows)
    write_manifest(output_dir, rows, status_counts, code_counts, cat_counts, disagreements, count)
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


def write_manifest(output_dir, rows, status_counts, code_counts, cat_counts, disagreements, requested):
    split_counts = Counter(r["split"] for r in rows)
    manifest = {
        "dataset": "stage2_2_balanced_reason_v0",
        "schema": "schemas/navspec_lora_candidate_stage2.schema.json",
        "schema_version": "navspec.lora_candidate.v1",
        "teacher": "classic rule engine + section5 strict classifier",
        "reason_max": MAX_REASON,
        "message_max_len": MAX_MESSAGE_LEN,
        "canonical_order": REASON_CODES,
        "rows": len(rows),
        "requested": requested,
        "split_counts": dict(sorted(split_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "reason_code_counts": dict(sorted(code_counts.items())),
        "category_counts": dict(sorted(cat_counts.items())),
        "classic_disagreements": disagreements,
        "system_prompt": SYSTEM_PROMPT,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---- speed eval set (section13.2) ----
def build_speed_eval(count: int, output_dir: Path) -> list[dict[str, Any]]:
    gens = [
        ("eval_speed_power_impossible", gen_speed_power_impossible),
        ("eval_speed_power_ok", gen_speed_power_ok),
        ("eval_speed_armor_range_impossible", gen_speed_armor_range_impossible),
        ("eval_speed_armor_range_ok", gen_speed_armor_range_ok),
    ]
    plan = [("eval_speed_power_impossible", 80), ("eval_speed_power_ok", 60),
            ("eval_speed_armor_range_impossible", 80), ("eval_speed_armor_range_ok", 60),
            ("eval_range_volume_impossible", 60), ("eval_three_code_composite", 60)]
    # range_volume + 3-code composite 専用
    def gen_range_volume(i):
        st, nation, year = "destroyer", "Japan", 1938
        c = {"standard_displacement_t_max": 1500, "range_nm_min": 10000, "speed_kt_min": 30}
        return _base(i, "eval_range_volume_impossible", nation, year, st, "convoy_escort", c, f"{year}年の{nation}向け{st}に10000海里を1500t以下で要求する。JSONのみ。")
    def gen_three_code(i):
        st, nation, year = "destroyer", "United States", 1938
        c = {"standard_displacement_t_max": 1500, "speed_kt_min": 40, "belt_armor_mm_min": 120, "range_nm_min": 10000}
        return _base(i, "eval_three_code_composite", nation, year, st, "night_battle", c, f"{year}年の{nation}向け{st}に40kt・120mm装甲・10000海里を1500t以下で要求する。JSONのみ。")
    cases: list[dict[str, Any]] = []
    for cat, n in plan:
        gen = {"eval_range_volume_impossible": gen_range_volume, "eval_three_code_composite": gen_three_code}.get(cat)
        for i in range(n):
            if gen:
                case = gen(9000 + i)
            else:
                g = [g for c2, g in gens if c2 == cat][0]
                case = g(9000 + i)
            case["id"] = f"{cat}_{i:04d}"
            case["category"] = cat
            cases.append(case)
    rows = [build_record(c) for c in cases]
    for r in rows:
        r["split"] = "eval"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "cases.jsonl", rows)
    sc = Counter(r["expected_status"] for r in rows)
    cc = Counter(c for r in rows for c in r["expected_reason_codes"])
    hc = Counter(r["category"] for r in rows)
    write_manifest(output_dir, rows, sc, cc, hc, 0, count)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build navspec Stage 2.2 balanced-reason dataset")
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--eval", action="store_true")
    parser.add_argument("--eval-count", type=int, default=400)
    parser.add_argument("--eval-dir", type=Path, default=DEFAULT_EVAL_DIR)
    args = parser.parse_args()

    if args.eval:
        rows = build_speed_eval(args.eval_count, args.eval_dir)
        print(f"eval cases: {len(rows)}")
        print(f"output_dir: {args.eval_dir}")
        return

    rows = build_dataset(args.count, args.output_dir)
    m = json.loads((args.output_dir / "manifest.json").read_text(encoding="utf-8"))
    print(f"dataset: stage2_2_balanced_reason_v0")
    print(f"rows: {len(rows)}")
    print(f"output_dir: {args.output_dir}")
    print("splits:", m["split_counts"])
    print("status:", m["status_counts"])
    print("reason_codes:", m["reason_code_counts"])
    print("categories:", dict(sorted(m["category_counts"].items(), key=lambda kv: kv[1], reverse=True)[:12]))
    print("classic disagreements:", m["classic_disagreements"])


if __name__ == "__main__":
    main()
