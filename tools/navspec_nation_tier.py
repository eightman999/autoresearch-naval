"""国家の工業力ティアに基づく制限。

tier_1: 主要工業国（大国）
tier_2: 地域工業国（中堅国）
tier_3: 限定工業力国
tier_4: 輸入依存国・小規模海軍
"""

from __future__ import annotations

from typing import Any


NATION_TIERS: dict[str, int] = {
    "United States": 1, "United States of America": 1,
    "United Kingdom": 1, "UK": 1, "Great Britain": 1,
    "Germany": 1,
    "Japan": 1,
    "France": 1,
    "Italy": 1,
    "Russia": 2, "Russia-Ussr": 2, "Soviet Union": 2, "USSR": 2,
    "Netherlands": 2,
    "Spain": 2,
    "Sweden": 2,
    "Argentina": 2,
    "Brazil": 2,
    "Turkey": 2, "Ottoman Empire": 2,
    "China": 2, "Qing China": 3, "Republic of China": 2,
    "Canada": 2,
    "Australia": 2,
    "India": 3,
    "Poland": 2,
    "Czechoslovakia": 2,
    "Yugoslavia": 3,
    "Portugal": 3,
    "Thailand": 3, "Siam": 3,
    "Greece": 3,
    "Chile": 3,
    "Peru": 3,
    "Norway": 3,
    "Denmark": 3,
    "Finland": 3,
    "Romania": 3,
    "Bulgaria": 3,
    "Mexico": 3,
    "Colombia": 3,
    "Venezuela": 3,
    "Egypt": 4,
    "Algeria": 4,
    "Iran": 4,
    "Philippines": 4,
    "Indonesia": 4,
    "Vietnam": 4,
    "Pakistan": 4,
    "South Korea": 4, "Korea": 4,
    "Taiwan": 4,
    "Cuba": 4,
    "Ecuador": 4,
    "Uruguay": 4,
    "Paraguay": 4,
    "Bolivia": 4,
    "Ireland": 4,
    "Belgium": 3,
    "Switzerland": 4,
    "Austria": 4,
    "Austria-Hungary": 2,
    "Hungary": 4,
}


TIER_LIMITS: dict[int, dict[str, Any]] = {
    1: {
        "max_displacement_t": None,
        "max_main_caliber_mm": None,
        "radar_from_year": 1938,
        "gas_turbine_from_year": 1948,
        "battleship_allowed": True,
        "carrier_allowed": True,
        "battlecruiser_allowed": True,
        "max_speed_multiplier": 1.0,
        "max_power_multiplier": 1.0,
        "description": "major industrial power",
    },
    2: {
        "max_displacement_t": 28000,
        "max_main_caliber_mm": 381,
        "radar_from_year": 1939,
        "gas_turbine_from_year": 1950,
        "battleship_allowed": True,
        "carrier_allowed": True,
        "battlecruiser_allowed": False,
        "max_speed_multiplier": 0.97,
        "max_power_multiplier": 0.94,
        "description": "regional industrial power",
    },
    3: {
        "max_displacement_t": 12000,
        "max_main_caliber_mm": 203,
        "radar_from_year": 1941,
        "gas_turbine_from_year": 1955,
        "battleship_allowed": False,
        "carrier_allowed": False,
        "battlecruiser_allowed": False,
        "max_speed_multiplier": 0.92,
        "max_power_multiplier": 0.78,
        "description": "limited industrial capacity",
    },
    4: {
        "max_displacement_t": 5000,
        "max_main_caliber_mm": 152,
        "radar_from_year": 1943,
        "gas_turbine_from_year": 1960,
        "battleship_allowed": False,
        "carrier_allowed": False,
        "battlecruiser_allowed": False,
        "max_speed_multiplier": 0.88,
        "max_power_multiplier": 0.65,
        "description": "import-dependent / small navy",
    },
}


_CAPITAL_SHIP_TYPES = {
    "battleship", "pre_dreadnought_battleship", "dreadnought_battleship",
    "fast_battleship", "battlecruiser", "large_cruiser",
}


def _norm_nation(value: str) -> str:
    return " ".join(value.replace("-", " ").replace("_", " ").split()).lower()


def get_nation_tier(nation: str | None) -> int:
    if not nation:
        return 3
    key = nation.strip()
    if key in NATION_TIERS:
        return NATION_TIERS[key]
    normalized_key = _norm_nation(key)
    for known, tier in NATION_TIERS.items():
        normalized_known = _norm_nation(known)
        if normalized_key in normalized_known or normalized_known in normalized_key:
            return tier
    return 3


def get_tier_limits(nation: str | None) -> dict[str, Any]:
    return TIER_LIMITS[get_nation_tier(nation)]


def is_capital_ship(ship_type: str | None) -> bool:
    return ship_type in _CAPITAL_SHIP_TYPES if ship_type else False


def apply_tier_clamps(
    record: dict[str, Any],
    nation: str | None,
    ship_type: str | None,
    year: int | None,
    *,
    apply_numeric: bool = True,
    apply_equipment: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """レコードにティア制限を適用し、調整ログを返す。"""
    limits = get_tier_limits(nation)
    tier = get_nation_tier(nation)
    log: list[str] = []

    if is_capital_ship(ship_type) and not limits["battleship_allowed"]:
        log.append(f"capital ship not available for tier-{tier} nation")

    if apply_numeric:
        disp = record.get("displacement", {})
        normal_t = disp.get("normal_t")
        max_disp = limits["max_displacement_t"]
        if max_disp is not None and normal_t is not None and normal_t > max_disp:
            scale = max_disp / normal_t
            disp["normal_t"] = round(max_disp, 1)
            full_t = disp.get("full_t")
            if full_t is not None:
                disp["full_t"] = round(full_t * scale, 1)
            log.append(f"disp clamped {normal_t}→{max_disp}t (tier limit)")
        record["displacement"] = disp

        arm = record.get("armament", {})
        main_cal = arm.get("main_caliber_mm")
        max_cal = limits["max_main_caliber_mm"]
        if max_cal is not None and main_cal is not None and main_cal > max_cal:
            arm["main_caliber_mm"] = round(max_cal, 1)
            log.append(f"gun clamped {main_cal}→{max_cal}mm (tier limit)")
        record["armament"] = arm

        propulsion = record.get("propulsion", {})
        speed = propulsion.get("speed_kn")
        mult = limits["max_speed_multiplier"]
        if speed is not None and mult < 1.0:
            clamped = round(speed * mult, 1)
            if clamped < speed:
                propulsion["speed_kn"] = clamped
                log.append(f"speed adjusted {speed}→{clamped}kt (tier {tier})")
        power = propulsion.get("power_shp")
        power_mult = limits.get("max_power_multiplier", 1.0)
        if power is not None and power_mult < 1.0:
            clamped_power = round(power * power_mult, 1)
            if clamped_power < power:
                propulsion["power_shp"] = clamped_power
                log.append(f"power adjusted {power}→{clamped_power}shp (tier {tier})")
        record["propulsion"] = propulsion

    if apply_equipment:
        eq = record.get("equipment", {})
        fc = eq.get("fire_control", {}) if isinstance(eq, dict) else {}
        radar_from = limits["radar_from_year"]
        if fc.get("radar") and year is not None and year < radar_from:
            fc["radar"] = False
            log.append(f"radar removed (tier-{tier} radar from {radar_from})")
            eq["fire_control"] = fc
            record["equipment"] = eq

    record["nation_capability_tier"] = {
        "tier": tier,
        "description": limits["description"],
        "limits": {
            "max_displacement_t": limits["max_displacement_t"],
            "max_main_caliber_mm": limits["max_main_caliber_mm"],
            "max_speed_multiplier": limits["max_speed_multiplier"],
            "max_power_multiplier": limits["max_power_multiplier"],
            "radar_from_year": limits["radar_from_year"],
            "battleship_allowed": limits["battleship_allowed"],
            "battlecruiser_allowed": limits["battlecruiser_allowed"],
            "carrier_allowed": limits["carrier_allowed"],
        },
    }

    return record, log
