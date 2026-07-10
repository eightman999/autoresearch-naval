"""矛盾制約検出。

ユーザー指定の条件が物理的・時代的・技術的に不可能な場合、
status=impossible を返し、理由と修正案を提示する。
"""

from __future__ import annotations

import math
import re
from typing import Any

try:
    from navspec_shiptypes import (
        SHIP_TYPE_SPECS, SHIP_TYPE_MAIN_GUN_CLAMP, SHIP_TYPE_SPEED_CLAMP,
        clamp_main_caliber, clamp_speed, era_for_year, get_ship_type_specs,
    )
except ImportError:
    from tools.navspec_shiptypes import (
        SHIP_TYPE_SPECS, SHIP_TYPE_MAIN_GUN_CLAMP, SHIP_TYPE_SPEED_CLAMP,
        clamp_main_caliber, clamp_speed, era_for_year, get_ship_type_specs,
    )

try:
    from navspec_nation_tier import get_nation_tier, get_tier_limits, is_capital_ship
except ImportError:
    from tools.navspec_nation_tier import get_nation_tier, get_tier_limits, is_capital_ship

try:
    from navspec_constraints import parse_constraints
except ImportError:
    from tools.navspec_constraints import parse_constraints


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def _admiralty_coefficient(disp_t: float, speed_kn: float, power_shp: float) -> float:
    if disp_t <= 0 or power_shp <= 0 or speed_kn <= 0:
        return 0.0
    return disp_t ** (2.0 / 3.0) * speed_kn ** 3 / power_shp


def _estimate_required_power(disp_t: float, speed_kn: float) -> float:
    ac = 250.0
    return disp_t ** (2.0 / 3.0) * speed_kn ** 3 / ac


def _fuel_volume_fraction(range_nm: float, speed_kn: float, disp_t: float) -> float:
    if disp_t <= 0 or speed_kn <= 0:
        return 1.0
    tons_per_hour = _estimate_required_power(disp_t, speed_kn) * 0.0003
    hours = range_nm / speed_kn
    fuel_tons = tons_per_hour * hours
    return fuel_tons / disp_t


def _parse_number(text: str) -> float | None:
    try:
        return float(text.replace(",", "").replace("，", ""))
    except ValueError:
        return None


def _bound_key(base: str, op: str | None) -> str:
    if op in {"以下", "以内", "未満", "まで", "上限"}:
        return f"{base}_max"
    if op in {"以上", "超", "下限"}:
        return f"{base}_min"
    return f"{base}_min"


def infer_constraints_from_prompt(raw_prompt: str | None) -> dict[str, Any]:
    """短い自然文要求から矛盾検出に必要な制約だけを抽出する。"""
    if not raw_prompt:
        return {}
    text = raw_prompt.replace("ノット", "kt").replace("トン", "t")
    parsed: dict[str, Any] = {}

    for match in re.finditer(r"(?:基準排水量|排水量)?\s*([0-9][0-9,，.]*)\s*t\s*(以下|以内|未満|以上|超)?", text, re.I):
        value = _parse_number(match.group(1))
        if value is None:
            continue
        parsed[_bound_key("standard_displacement_t", match.group(2))] = value

    for match in re.finditer(r"(?:速力)?\s*([0-9][0-9,，.]*)\s*kt\s*(以下|以内|未満|以上|超)?", text, re.I):
        value = _parse_number(match.group(1))
        if value is None:
            continue
        parsed[_bound_key("speed_kt", match.group(2))] = value

    for match in re.finditer(r"([0-9][0-9,，.]*)\s*(cm|mm|センチ|ミリ)\s*砲\s*([0-9]+)?\s*門?\s*(以下|以内|未満|以上|超)?", text, re.I):
        value = _parse_number(match.group(1))
        if value is None:
            continue
        unit = match.group(2).lower()
        if unit in {"cm", "センチ"}:
            value *= 10
        parsed[_bound_key("main_gun_caliber_mm", match.group(4))] = value
        if match.group(3):
            parsed["main_gun_barrels_min"] = int(match.group(3))

    for match in re.finditer(r"(?:主砲|主砲口径|砲口径).{0,8}?([0-9][0-9,，.]*)\s*(cm|mm|センチ|ミリ)\s*(以下|以内|未満|以上|超)?", text, re.I):
        value = _parse_number(match.group(1))
        if value is None:
            continue
        unit = match.group(2).lower()
        if unit in {"cm", "センチ"}:
            value *= 10
        parsed[_bound_key("main_gun_caliber_mm", match.group(3))] = value

    for match in re.finditer(r"(?:装甲帯|ベルト装甲|舷側装甲).{0,8}?([0-9][0-9,，.]*)\s*mm\s*(以下|以内|未満|以上|超)?", text, re.I):
        value = _parse_number(match.group(1))
        if value is None:
            continue
        parsed[_bound_key("belt_armor_mm", match.group(2))] = value

    for match in re.finditer(r"(?:航続距離|航続).{0,8}?([0-9][0-9,，.]*)\s*(?:海里|nm|NM)\s*(以下|以内|未満|以上|超)?", text):
        value = _parse_number(match.group(1))
        if value is None:
            continue
        parsed[_bound_key("range_nm", match.group(2))] = value

    if "レーダー" in text or "radar" in text.lower():
        parsed["radar_required"] = True
    if "ディーゼル" in text or "diesel" in text.lower():
        parsed["diesel_required"] = True
    if "カタパルト" in text or "catapult" in text.lower():
        parsed["catapult_required"] = True
    if "両用砲" in text or "dual-purpose" in text.lower() or "dual purpose" in text.lower():
        parsed["dual_purpose_gun_required"] = True
    if "条約" in text:
        parsed["treaty_limited"] = True

    return parsed


def check_contradiction(
    ship_type: str,
    year: int | None,
    nation: str | None,
    constraints: dict[str, Any] | None = None,
    raw_prompt: str | None = None,
) -> dict[str, Any]:
    """矛盾制約を検出し、結果を返す。

    Returns:
        {"status": "ok"} または
        {"status": "impossible", "reasons": [...], "suggested_revision": {...}}
    """
    reasons: list[str] = []
    parsed = infer_constraints_from_prompt(raw_prompt)
    parsed.update(parse_constraints(constraints) if constraints else {})
    specs = get_ship_type_specs(ship_type)
    base_disp = specs["normal_t"]
    base_speed = specs["speed"]
    base_gun = specs["main_caliber_mm"]
    base_belt = specs.get("belt_mm")
    era = era_for_year(year)
    tier = get_nation_tier(nation)
    tier_limits = get_tier_limits(nation)

    req_disp_max = _num(parsed.get("standard_displacement_t_max"))
    req_disp_min = _num(parsed.get("standard_displacement_t_min"))
    req_speed_min = _num(parsed.get("speed_kt_min"))
    req_speed_max = _num(parsed.get("speed_kt_max"))
    req_gun_max = _num(parsed.get("main_gun_caliber_mm_max"))
    req_gun_min = _num(parsed.get("main_gun_caliber_mm_min"))
    req_belt_max = _num(parsed.get("belt_armor_mm_max"))
    req_belt_min = _num(parsed.get("belt_armor_mm_min"))
    req_range_min = _num(parsed.get("range_nm_min"))
    req_barrels_min = _num(parsed.get("main_gun_barrels_min"))

    eff_disp = req_disp_max if req_disp_max is not None else base_disp
    eff_speed = req_speed_min if req_speed_min is not None else base_speed
    eff_gun = req_gun_min if req_gun_min is not None else base_gun
    eff_belt = req_belt_min if req_belt_min is not None else (base_belt or 0)

    _, st_gun_hi = SHIP_TYPE_MAIN_GUN_CLAMP.get(ship_type, (76, 520))
    _, st_spd_hi = SHIP_TYPE_SPEED_CLAMP.get(ship_type, (5, 45))

    if req_gun_min is not None and req_gun_min > st_gun_hi + 5:
        reasons.append(
            f"{ship_type}に{req_gun_min:.0f}mm砲は過大（適正上限{st_gun_hi:.0f}mm）"
        )
    if req_speed_min is not None and req_speed_min > st_spd_hi + 1:
        reasons.append(
            f"{ship_type}に{req_speed_min:.0f}ktは過大（適正上限{st_spd_hi:.0f}kt）"
        )
    if req_barrels_min is not None and req_gun_min is not None and req_gun_min >= 180 and req_barrels_min >= 6 and ship_type in {
        "destroyer", "large_destroyer", "anti_air_destroyer", "destroyer_escort",
        "frigate", "patrol_frigate", "corvette", "sloop", "patrol_boat", "gunboat",
    }:
        reasons.append(
            f"小型艦に{req_gun_min:.0f}mm砲{req_barrels_min:.0f}門は重量・復原性面で非現実的"
        )

    if req_belt_min is not None and req_belt_min > 0 and eff_disp < 3000:
        if req_belt_min > 50:
            reasons.append(
                f"{eff_disp:.0f}t級小型艦に{req_belt_min:.0f}mm装甲帯は過大"
            )

    if req_speed_min is not None and eff_disp > 0:
        required_power = _estimate_required_power(eff_disp, req_speed_min)
        power_per_ton = required_power / eff_disp
        if req_speed_min >= 38 and eff_disp > 3000:
            reasons.append(
                f"{eff_disp:.0f}t級船体で{req_speed_min:.0f}ktは機関出力{required_power/1000:.0f}kWが必要（{power_per_ton:.1f} shp/t）"
            )
        if req_belt_min is not None and req_belt_min > 100 and req_speed_min >= 35 and eff_disp < 5000:
            reasons.append(
                f"重装甲({req_belt_min:.0f}mm)と高速({req_speed_min:.0f}kt)を{eff_disp:.0f}tで両立するのは困難"
            )

    if req_range_min is not None and req_range_min >= 8000:
        fuel_frac = _fuel_volume_fraction(req_range_min, eff_speed, eff_disp)
        if fuel_frac > 0.55 or (eff_disp < 5000 and fuel_frac > 0.35):
            reasons.append(
                f"航続距離{req_range_min:.0f}nmには燃料が排水量の{fuel_frac*100:.0f}%を占める（限界35%）"
            )

    small_ship_type = ship_type in {
        "destroyer", "large_destroyer", "anti_air_destroyer", "destroyer_escort",
        "frigate", "patrol_frigate", "corvette", "sloop", "patrol_boat", "gunboat",
        "torpedo_boat", "minesweeper",
    }
    combined_flags = [
        small_ship_type or eff_disp < 3500,
        eff_gun >= 180,
        eff_belt >= 100,
        eff_speed >= 34,
        req_range_min is not None and req_range_min >= 8000,
    ]
    if all(combined_flags):
        reasons.append(
            "小型艦に大口径砲・重装甲・高速度・長航続を同時要求しており、重量配分が成立しない"
        )

    if year is not None:
        if parsed.get("radar_required") and year < 1938:
            reasons.append(f"レーダーは{year}年には実用化されていない（1938年以降）")
        if parsed.get("diesel_required") and year < 1910:
            reasons.append(f"ディーゼル機関は{year}年には艦艇用として未成熟（1910年代以降）")
        if parsed.get("catapult_required") and year < 1925:
            reasons.append(f"航空カタパルトは{year}年には未実用化（1925年以降）")
        if parsed.get("dual_purpose_gun_required") and year < 1925:
            reasons.append(f"本格的な高角両用砲は{year}年には未実用化（1925年以降）")

        if year >= 1922 and year <= 1936:
            if eff_disp > 35000 and is_capital_ship(ship_type):
                reasons.append(
                    f"ワシントン海軍条約下（{year}年）の新造主力艦は35,000t以下（実際{eff_disp:.0f}t）"
                )
            if eff_gun > 406 and is_capital_ship(ship_type):
                reasons.append(
                    f"ワシントン海軍条約下（{year}年）の主砲は16inch(406mm)以下"
                )
            if ("cruiser" in ship_type or parsed.get("treaty_limited")) and eff_disp > 10000 and not is_capital_ship(ship_type):
                reasons.append(
                    f"条約期（{year}年）の巡洋艦制限は概ね10,000t以下（実際{eff_disp:.0f}t）"
                )
            if ("cruiser" in ship_type or parsed.get("treaty_limited")) and eff_gun > 203 and not is_capital_ship(ship_type):
                reasons.append(
                    f"条約期（{year}年）の巡洋艦主砲は203mm以下"
                )

        radar_from = tier_limits["radar_from_year"]
        if parsed.get("radar_required") and year < radar_from:
            reasons.append(
                f"tier-{tier}国（{nation}）のレーダー採用は{radar_from}年以降（要求年{year}年）"
            )

    if is_capital_ship(ship_type) and not tier_limits["battleship_allowed"]:
        reasons.append(
            f"tier-{tier}国（{nation}）の{year}年自主建造で主力艦は非現実的"
        )

    max_tier_disp = tier_limits["max_displacement_t"]
    if max_tier_disp is not None and eff_disp > max_tier_disp * 1.1:
        reasons.append(
            f"tier-{tier}国（{nation}）の工業力で{eff_disp:.0f}t級艦の建造は困難（上限{max_tier_disp:.0f}t）"
        )
    max_tier_cal = tier_limits["max_main_caliber_mm"]
    if max_tier_cal is not None and eff_gun > max_tier_cal + 5:
        reasons.append(
            f"tier-{tier}国（{nation}）の主砲上限は{max_tier_cal:.0f}mm程度（要求{eff_gun:.0f}mm）"
        )

    if req_disp_min is not None and req_disp_max is not None and req_disp_min > req_disp_max:
        reasons.append(f"排水量制約が矛盾（min {req_disp_min:.0f}t > max {req_disp_max:.0f}t）")
    if req_speed_min is not None and req_speed_max is not None and req_speed_min > req_speed_max:
        reasons.append(f"速力制約が矛盾（min {req_speed_min:.0f}kt > max {req_speed_max:.0f}kt）")
    if req_gun_min is not None and req_gun_max is not None and req_gun_min > req_gun_max:
        reasons.append(f"主砲制約が矛盾（min {req_gun_min:.0f}mm > max {req_gun_max:.0f}mm）")

    if not reasons:
        return {"status": "ok"}

    revision = _suggest_revision(
        ship_type, year, nation, specs, parsed, tier_limits, tier
    )
    return {
        "status": "impossible",
        "reason": reasons,
        "reasons": reasons,
        "suggested_revision": revision,
        "tier": tier,
    }


def _suggest_revision(
    ship_type: str,
    year: int | None,
    nation: str | None,
    base_specs: dict[str, Any],
    parsed: dict[str, Any],
    tier_limits: dict[str, Any],
    tier: int,
) -> dict[str, Any]:
    rev: dict[str, Any] = {}
    base_disp = base_specs["normal_t"]
    base_speed = base_specs["speed"]
    base_gun = base_specs["main_caliber_mm"]

    max_tier_disp = tier_limits["max_displacement_t"]
    rev_disp = min(base_disp, max_tier_disp) if max_tier_disp else base_disp

    req_disp_max = _num(parsed.get("standard_displacement_t_max"))
    if req_disp_max and req_disp_max < rev_disp:
        rev_disp = max(req_disp_max, base_disp * 0.5)

    req_speed_min = _num(parsed.get("speed_kt_min"))
    rev_speed = clamp_speed(base_speed, ship_type, year)
    if req_speed_min:
        rev_speed = min(req_speed_min, clamp_speed(req_speed_min, ship_type, year))
    if ship_type in {
        "destroyer", "large_destroyer", "anti_air_destroyer", "destroyer_escort",
        "frigate", "patrol_frigate", "corvette", "sloop", "patrol_boat", "gunboat",
    } and req_speed_min and req_speed_min >= 38:
        rev_speed = min(rev_speed, max(base_speed, 36.0))

    req_gun_max = _num(parsed.get("main_gun_caliber_mm_max"))
    req_gun_min = _num(parsed.get("main_gun_caliber_mm_min"))
    rev_gun = clamp_main_caliber(base_gun, ship_type, year)
    if req_gun_max and req_gun_max < rev_gun:
        rev_gun = req_gun_max
    if req_gun_min and req_gun_min > rev_gun:
        rev_gun = clamp_main_caliber(req_gun_min, ship_type, year)

    max_tier_cal = tier_limits["max_main_caliber_mm"]
    if max_tier_cal and rev_gun > max_tier_cal:
        rev_gun = max_tier_cal

    req_belt_min = _num(parsed.get("belt_armor_mm_min"))
    rev_belt = base_specs.get("belt_mm") or 0
    if req_belt_min:
        max_feasible_belt = rev_disp / 80
        rev_belt = min(req_belt_min, max_feasible_belt)

    req_range_min = _num(parsed.get("range_nm_min"))
    if req_range_min:
        max_feasible_range = rev_disp * 3 / (rev_speed if rev_speed else 20)
        rev_range = min(req_range_min, max(max_feasible_range, 3000))
    else:
        rev_range = None

    rev["ship_type"] = ship_type
    rev["standard_displacement_t"] = round(rev_disp)
    rev["speed_kt"] = round(rev_speed, 1) if rev_speed else None
    rev["main_gun_caliber_mm"] = round(rev_gun, 1) if rev_gun else None
    rev["belt_armor_mm"] = round(rev_belt, 1) if rev_belt > 0 else 0
    if rev_range:
        rev["range_nm"] = round(rev_range)
    rev["tier"] = tier
    rev["rationale"] = f"tier-{tier} {tier_limits['description']}として現実的な値に調整"

    return rev
