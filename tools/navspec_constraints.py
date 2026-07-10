"""ユーザー指定制約のパースと適用。

構造化されたconstraints辞書から数値制約を読み取り、
生成された諸元値を制約範囲内にクランプする。
"""

from __future__ import annotations

import math
from typing import Any


CONSTRAINT_KEYS = {
    "standard_displacement_t_min", "standard_displacement_t_max",
    "speed_kt_min", "speed_kt_max",
    "main_gun_caliber_mm_min", "main_gun_caliber_mm_max",
    "belt_armor_mm_min", "belt_armor_mm_max",
    "range_nm_min", "range_nm_max",
    "main_gun_barrels_min", "main_gun_barrels_max",
    "crew_max",
    "torpedo_required",
    "anti_air_priority", "anti_submarine_priority",
    "low_cost",
    "dual_purpose_gun_required", "radar_required",
    "catapult_required", "diesel_required", "turbine_required",
    "treaty_limited",
}


def normalize_constraint_key(key: str) -> str:
    aliases = {
        "main_gun_caliber_cm_max": "main_gun_caliber_mm_max",
        "main_gun_caliber_cm_min": "main_gun_caliber_mm_min",
        "standard_displacement_t_max": "standard_displacement_t_max",
        "standard_displacement_t_min": "standard_displacement_t_min",
    }
    return aliases.get(key, key)


def parse_constraints(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw:
        return {}
    parsed: dict[str, Any] = {}
    for key, val in raw.items():
        nk = normalize_constraint_key(key)
        if nk.startswith("main_gun_caliber_mm_") and nk in ("main_gun_caliber_mm_max", "main_gun_caliber_mm_min"):
            if nk == "main_gun_caliber_mm_max" and key.endswith("_cm_max"):
                parsed[nk] = float(val) * 10
            elif nk == "main_gun_caliber_mm_min" and key.endswith("_cm_min"):
                parsed[nk] = float(val) * 10
            else:
                parsed[nk] = float(val) if val is not None else None
        elif nk in CONSTRAINT_KEYS:
            parsed[nk] = val
    return parsed


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def apply_constraints(
    record: dict[str, Any],
    constraints: dict[str, Any],
    ship_type: str | None = None,
    year: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """レコードの数値フィールドを制約内にクランプし、適用ログを返す。"""
    try:
        from navspec_shiptypes import clamp_main_caliber, clamp_speed
    except ImportError:
        from tools.navspec_shiptypes import clamp_main_caliber, clamp_speed

    log: list[str] = []
    if not constraints:
        return record, log

    disp = record.get("displacement", {})
    normal_t = _num(disp.get("normal_t"))
    full_t = _num(disp.get("full_t"))

    if "standard_displacement_t_max" in constraints and normal_t is not None:
        lim = _num(constraints["standard_displacement_t_max"])
        if lim is not None and normal_t > lim:
            scale = lim / normal_t
            disp["normal_t"] = round(lim, 1)
            if full_t is not None:
                disp["full_t"] = round(full_t * scale, 1)
            log.append(f"disp clamped {normal_t}→{lim}t (max constraint)")

    if "standard_displacement_t_min" in constraints and normal_t is not None:
        lim = _num(constraints["standard_displacement_t_min"])
        if lim is not None and normal_t < lim:
            disp["normal_t"] = round(lim, 1)
            log.append(f"disp raised {normal_t}→{lim}t (min constraint)")

    record["displacement"] = disp

    propulsion = record.get("propulsion", {})
    speed = _num(propulsion.get("speed_kn"))

    if "speed_kt_min" in constraints and speed is not None:
        lim = _num(constraints["speed_kt_min"])
        if lim is not None and speed < lim:
            propulsion["speed_kn"] = round(lim, 1)
            log.append(f"speed raised {speed}→{lim}kt (min constraint)")

    if "speed_kt_max" in constraints and speed is not None:
        lim = _num(constraints["speed_kt_max"])
        if lim is not None and speed > lim:
            propulsion["speed_kn"] = round(lim, 1)
            log.append(f"speed clamped {speed}→{lim}kt (max constraint)")

    record["propulsion"] = propulsion

    arm = record.get("armament", {})
    main_cal = _num(arm.get("main_caliber_mm"))

    if "main_gun_caliber_mm_max" in constraints and main_cal is not None:
        lim = _num(constraints["main_gun_caliber_mm_max"])
        if lim is not None and main_cal > lim + 0.5:
            arm["main_caliber_mm"] = round(lim, 1)
            log.append(f"gun clamped {main_cal}→{lim}mm (max constraint)")
            for mount in arm.get("mounts", []):
                if _num(mount.get("caliber_mm")) == main_cal:
                    mount["caliber_mm"] = round(lim, 1)

    if "main_gun_caliber_mm_min" in constraints and main_cal is not None:
        lim = _num(constraints["main_gun_caliber_mm_min"])
        if lim is not None and main_cal < lim - 0.5:
            arm["main_caliber_mm"] = round(lim, 1)
            log.append(f"gun raised {main_cal}→{lim}mm (min constraint)")

    record["armament"] = arm

    armor = record.get("armor", {})
    belt = _num(armor.get("belt_mm"))

    if "belt_armor_mm_max" in constraints and belt is not None:
        lim = _num(constraints["belt_armor_mm_max"])
        if lim is not None and belt > lim:
            armor["belt_mm"] = round(lim, 1)
            log.append(f"belt clamped {belt}→{lim}mm (max constraint)")

    if "belt_armor_mm_min" in constraints and belt is not None:
        lim = _num(constraints["belt_armor_mm_min"])
        if lim is not None and belt < lim:
            armor["belt_mm"] = round(lim, 1)
            log.append(f"belt raised {belt}→{lim}mm (min constraint)")
    elif "belt_armor_mm_min" in constraints and (belt is None or belt == 0):
        lim = _num(constraints["belt_armor_mm_min"])
        if lim is not None and lim > 0:
            armor["belt_mm"] = round(lim, 1)
            log.append(f"belt set {lim}mm (min constraint, was None/0)")

    record["armor"] = armor

    barrels = _num(arm.get("main_barrels"))
    if "main_gun_barrels_max" in constraints and barrels is not None:
        lim = _num(constraints["main_gun_barrels_max"])
        if lim is not None and barrels > lim:
            arm["main_barrels"] = int(lim)
            log.append(f"barrels clamped {barrels}→{int(lim)} (max constraint)")

    if "main_gun_barrels_min" in constraints and barrels is not None:
        lim = _num(constraints["main_gun_barrels_min"])
        if lim is not None and barrels < lim:
            arm["main_barrels"] = int(lim)
            log.append(f"barrels raised {barrels}→{int(lim)} (min constraint)")

    if ship_type and year is not None:
        clamped_cal = clamp_main_caliber(_num(record.get("armament", {}).get("main_caliber_mm")), ship_type, year)
        if clamped_cal is not None:
            record["armament"]["main_caliber_mm"] = clamped_cal

        clamped_spd = clamp_speed(_num(record.get("propulsion", {}).get("speed_kn")), ship_type, year)
        if clamped_spd is not None:
            record["propulsion"]["speed_kn"] = clamped_spd

    return record, log
