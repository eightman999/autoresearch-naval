"""Rule-based equipment candidates for fictional naval specifications."""

from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _profile_value(profile: Any, name: str, default: float) -> float:
    if profile is None:
        return default
    return float(getattr(profile, name, default))


def displacement_t(record: dict[str, Any], profile: Any = None) -> float | None:
    disp = record.get("displacement", {})
    value = _num(disp.get("full_t")) or _num(disp.get("normal_t"))
    if value is None:
        return None
    return _round(value * _profile_value(profile, "displacement_scale", 1.0))


def speed_kn(record: dict[str, Any], profile: Any = None) -> float | None:
    value = _num(record.get("propulsion", {}).get("speed_kn"))
    if value is None:
        return None
    return _round(value + _profile_value(profile, "speed_delta", 0.0))


def main_caliber_mm(record: dict[str, Any], profile: Any = None) -> float | None:
    value = _num(record.get("armament", {}).get("main_caliber_mm"))
    if value is None:
        return None
    return _round(value + _profile_value(profile, "main_caliber_delta", 0.0))


def year_value(record: dict[str, Any]) -> int | None:
    try:
        return int(record.get("year"))
    except (TypeError, ValueError):
        return None


def era_profile_for(year: int | None, disp: float | None) -> dict[str, Any]:
    if year is None:
        era = "unknown"
        year_band = "unknown"
        technology_level = "unknown"
    elif year < 1906:
        era = "pre_dreadnought"
        year_band = "1890-1905"
        technology_level = "pre_dreadnought"
    elif year <= 1918:
        era = "dreadnought"
        year_band = "1906-1918"
        technology_level = "dreadnought"
    elif year <= 1935:
        era = "treaty_interwar"
        year_band = "1919-1935"
        technology_level = "interwar"
    elif year <= 1945:
        era = "fast_battleship"
        year_band = "1936-1945"
        technology_level = "late_war"
    else:
        era = "postwar"
        year_band = "1946+"
        technology_level = "postwar"

    if disp is None:
        size_band = "unknown"
    elif disp < 12_000:
        size_band = "coastal_or_small_capital"
    elif disp < 22_000:
        size_band = "early_capital_ship"
    elif disp < 35_000:
        size_band = "treaty_capital_ship"
    elif disp < 50_000:
        size_band = "modern_capital_ship"
    else:
        size_band = "super_capital_ship"

    return {
        "era": era,
        "year_band": year_band,
        "technology_level": technology_level,
        "size_band": size_band,
    }


def main_battery_range(era: str, size_band: str, hull_kind: str | None) -> tuple[int, int]:
    if hull_kind in ("destroyer", "frigate"):
        return (76, 127)
    if hull_kind == "cruiser":
        return (127, 203)
    if hull_kind == "other":
        return (76, 203)
    if size_band == "coastal_or_small_capital":
        return (203, 305)
    if era == "pre_dreadnought":
        return (254, 305)
    if era == "dreadnought":
        return (280, 381 if hull_kind == "battlecruiser" else 356)
    if era == "treaty_interwar":
        return (305, 406)
    if era == "fast_battleship":
        return (330, 460)
    if size_band == "super_capital_ship":
        return (406, 510)
    return (254, 406)


def secondary_candidates(era: str) -> list[dict[str, Any]]:
    if era == "pre_dreadnought":
        return [
            {"caliber_mm": 76, "role": "anti_torpedo_boat"},
            {"caliber_mm": 102, "role": "anti_torpedo_boat"},
            {"caliber_mm": 152, "role": "casemate_secondary"},
        ]
    if era == "dreadnought":
        return [
            {"caliber_mm": 102, "role": "anti_torpedo_boat"},
            {"caliber_mm": 120, "role": "secondary_battery"},
            {"caliber_mm": 152, "role": "casemate_secondary"},
        ]
    if era == "treaty_interwar":
        return [
            {"caliber_mm": 120, "role": "secondary_battery"},
            {"caliber_mm": 127, "role": "dual_purpose_candidate"},
            {"caliber_mm": 152, "role": "secondary_battery"},
        ]
    return [
        {"caliber_mm": 100, "role": "dual_purpose"},
        {"caliber_mm": 127, "role": "dual_purpose"},
        {"caliber_mm": 152, "role": "secondary_battery"},
    ]


def aa_candidates(era: str, year: int | None) -> list[dict[str, Any]]:
    if era in {"pre_dreadnought", "dreadnought"}:
        return [
            {"caliber_mm": 37, "role": "light_aa_or_qf"},
            {"caliber_mm": 47, "role": "light_aa_or_qf"},
            {"caliber_mm": 76, "role": "early_aa"},
        ]
    if era == "treaty_interwar":
        return [
            {"caliber_mm": 13.2, "role": "machine_gun_aa"},
            {"caliber_mm": 25, "role": "light_aa"},
            {"caliber_mm": 76, "role": "medium_aa"},
        ]
    if year is not None and year >= 1938:
        return [
            {"caliber_mm": 20, "role": "light_aa"},
            {"caliber_mm": 25, "role": "light_aa"},
            {"caliber_mm": 40, "role": "medium_aa"},
            {"caliber_mm": 127, "role": "dual_purpose_aa"},
        ]
    return [
        {"caliber_mm": 20, "role": "light_aa"},
        {"caliber_mm": 40, "role": "medium_aa"},
        {"caliber_mm": 76, "role": "medium_aa"},
    ]


def fire_control_candidates(era: str, year: int | None) -> list[dict[str, Any]]:
    if era == "pre_dreadnought":
        return [{"system": "local_optical_sights", "radar": False}]
    if era == "dreadnought":
        return [{"system": "central_optical_director", "radar": False}]
    if era == "treaty_interwar":
        return [{"system": "director_with_mechanical_computer", "radar": False}]
    return [{"system": "director_with_radar_assist", "radar": bool(year is not None and year >= 1938)}]


def doctrine_for(country: str | None, era: str, hull_kind: str | None) -> dict[str, Any]:
    country_key = (country or "").replace("_", "-")
    doctrine = "balanced capital-ship design"
    priorities = ["balanced armament", "reasonable protection", "service speed"]
    if country_key == "Japan":
        doctrine = "high-speed capital ship with heavy battery emphasis"
        priorities = ["speed", "heavy main battery", "long-range decisive battle"]
    elif country_key == "United-States":
        doctrine = "endurance and protection for fleet action"
        priorities = ["range", "protection", "fire-control quality"]
    elif country_key == "United-Kingdom":
        doctrine = "global-service capital ship with balanced armament and protection"
        priorities = ["global range", "balanced protection", "fleet integration"]
    elif country_key == "Germany":
        doctrine = "survivable North Sea capital ship with strong protection"
        priorities = ["protection", "compartmentation", "medium-range action"]
    elif country_key == "France":
        doctrine = "fast counter-capital ship with compact protection"
        priorities = ["speed", "concentrated firepower", "modern machinery"]
    elif country_key == "Italy":
        doctrine = "Mediterranean fast capital ship"
        priorities = ["speed", "main battery", "shorter operational radius"]
    elif country_key == "Russia":
        doctrine = "regional fleet capital ship with heavy armament"
        priorities = ["main battery", "regional endurance", "protection"]
    if hull_kind == "battlecruiser":
        priorities = ["speed", "main battery"] + [item for item in priorities if item not in {"speed", "main battery"}]
    return {"country": country_key or country, "era": era, "doctrine": doctrine, "priorities": priorities[:4]}


def estimate_belt_mm(era: str, disp: float | None) -> float | None:
    """era + 排水量からベルト装甲厚を推定"""
    if disp is None or disp <= 0:
        return None
    if era == "pre_dreadnought":
        return round(min(max(disp / 100, 100), 450), 1)
    if era == "dreadnought":
        return round(min(max(disp / 90, 150), 500), 1)
    if era in ("treaty_interwar", "fast_battleship"):
        return round(min(max(disp / 80, 200), 550), 1)
    return round(min(max(disp / 120, 50), 300), 1)


def estimate_speed_kn(era: str, disp: float | None, hull_kind: str | None) -> float:
    """era + 排水量 + 艦種から速力を推定"""
    if hull_kind in ("destroyer", "frigate"):
        return 35.0
    if hull_kind in ("cruiser",):
        return 30.0
    if hull_kind == "battlecruiser":
        return 28.0
    if disp and disp > 30000:
        return 25.0
    return 20.0


def estimate_power_shp(disp: float | None, speed: float | None) -> float | None:
    """Admiralty係数300から出力を推定"""
    if disp is None or speed is None or disp <= 0 or speed <= 0:
        return None
    return round(disp ** (2 / 3) * speed ** 3 / 300)


def estimate_dimensions(disp_t: float | None, hull_kind: str | None, ship_type: str | None = None) -> tuple[float | None, float | None, float | None]:
    """排水量から L/B/D を推定。ship_typeがあれば艦種別L/B比を使用。"""
    if disp_t is None or disp_t <= 0:
        return None, None, None
    try:
        from navspec_shiptypes import get_dim_factors, lb_ratio_band_for
    except ImportError:
        from tools.navspec_shiptypes import get_dim_factors, lb_ratio_band_for
    fL, fB, fD = get_dim_factors(ship_type, hull_kind)
    lb_min, lb_max = lb_ratio_band_for(ship_type, hull_kind)
    scale = disp_t ** (1 / 3)
    length = fL * scale
    current_ratio = fL / fB if fB > 0 else (lb_min + lb_max) / 2
    target_ratio = max(lb_min, min(lb_max, current_ratio))
    if target_ratio <= lb_min + 0.02:
        target_ratio = min(lb_max, lb_min + 0.08)
    elif target_ratio >= lb_max - 0.02:
        target_ratio = max(lb_min, lb_max - 0.08)
    breadth = length / target_ratio
    return round(length, 1), round(breadth, 1), round(fD * scale, 1)


def armor_scheme_for(record: dict[str, Any], era: str) -> dict[str, Any]:
    belt = _num(record.get("armor", {}).get("belt_mm"))
    if belt is None:
        disp = _num(record.get("displacement", {}).get("normal_t")) or _num(record.get("displacement", {}).get("full_t"))
        belt = estimate_belt_mm(era, disp)
    if era == "pre_dreadnought":
        scheme = "distributed_armor"
        deck_factor = 0.18
        turret_factor = 1.05
    elif era == "dreadnought":
        scheme = "dreadnought_citadel"
        deck_factor = 0.22
        turret_factor = 1.10
    else:
        scheme = "all_or_nothing_or_compact_citadel"
        deck_factor = 0.30
        turret_factor = 1.15
    return {
        "scheme": scheme,
        "belt_mm": _round(belt),
        "deck_mm": _round(belt * deck_factor if belt is not None else None),
        "turret_face_mm": _round(belt * turret_factor if belt is not None else None),
        "conning_tower_mm": _round(belt * 0.95 if belt is not None else None),
        "source": "rule_estimate_from_belt_and_era",
    }


def infer_main_mount_count(record: dict[str, Any]) -> int | None:
    arm = record.get("armament", {})
    main_cal = _num(arm.get("main_caliber_mm"))
    if main_cal is None:
        return None
    mount_count = 0
    for mount in arm.get("mounts", []):
        if abs((_num(mount.get("caliber_mm")) or -1.0) - main_cal) <= 1.0:
            mount_count += int(_num(mount.get("mount_count")) or 0)
    return mount_count or None


def equipment_plan(record: dict[str, Any], profile: Any = None) -> dict[str, Any]:
    disp = displacement_t(record, profile)
    if disp is None:
        disp = _num(record.get("displacement", {}).get("normal_t")) or _num(record.get("displacement", {}).get("full_t"))
    year = year_value(record)
    era_profile = era_profile_for(year, disp)
    era = era_profile["era"]
    hull_kind = record.get("hull_kind")
    main_cal = main_caliber_mm(record, profile)
    main_min, main_max = main_battery_range(era, era_profile["size_band"], hull_kind)
    selected_main = main_cal if main_cal is not None else float((main_min + main_max) // 2)
    arm = record.get("armament", {})
    main_barrels = _num(arm.get("main_barrels"))
    if main_barrels is None:
        main_barrels = 8.0 if hull_kind in ("battleship", "battlecruiser") else (4.0 if hull_kind in ("destroyer", "frigate") else 6.0)
    secondaries = secondary_candidates(era)
    aa = aa_candidates(era, year)
    fire_control = fire_control_candidates(era, year)[0]
    secondary = secondaries[min(1, len(secondaries) - 1)]
    anti_air = aa[-1] if era in {"pre_dreadnought", "dreadnought"} else aa[min(1, len(aa) - 1)]
    speed = speed_kn(record, profile)
    if speed is None:
        speed = estimate_speed_kn(era, disp, hull_kind)
    power = estimate_power_shp(disp, speed)

    equipment = {
        "main_battery": {
            "caliber_mm": _round(selected_main),
            "barrels": _round(main_barrels, 0),
            "mount_count": infer_main_mount_count(record),
            "fit": "selected_from_record_with_rule_bounds",
        },
        "secondary_battery": secondary,
        "anti_air": anti_air,
        "fire_control": fire_control,
        "torpedoes": {
            "carried": bool(hull_kind in ("destroyer", "cruiser") or (hull_kind == "battlecruiser" and year is not None and year <= 1942)),
            "fit": "hull_era_rule",
        },
        "aviation": {
            "catapult": bool(year is not None and year >= 1925 and (disp or 0) >= 20_000),
            "fit": "era_displacement_rule",
        },
        "propulsion_fit": {
            "speed_kn": speed,
            "speed_role": "fast" if speed is not None and speed >= 27 else "standard",
            "power_shp": power,
        },
    }
    candidates = {
        "rules_version": "navspec.equipment.v1",
        "basis": {
            "year": year,
            "displacement_t": disp,
            "hull_kind": hull_kind,
            "country": record.get("country"),
        },
        "main_battery": {
            "caliber_mm_min": main_min,
            "caliber_mm_max": main_max,
            "barrels_min": 4,
            "barrels_max": 12,
        },
        "secondary_battery": secondaries,
        "anti_air": aa,
        "fire_control": fire_control_candidates(era, year),
        "aviation": {"catapult_allowed": bool(year is not None and year >= 1925 and (disp or 0) >= 20_000)},
        "torpedoes": {"allowed": equipment["torpedoes"]["carried"]},
    }
    return {
        "era_profile": era_profile,
        "design_doctrine": doctrine_for(record.get("country"), era, hull_kind),
        "equipment": equipment,
        "armor_scheme": armor_scheme_for(record, era),
        "equipment_candidates": candidates,
    }


def validate_equipment(record: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if record.get("source_kind") != "fictional_bounded":
        return issues

    required = ("era_profile", "design_doctrine", "equipment", "armor_scheme", "equipment_candidates")
    for field in required:
        if field not in record:
            issues.append({"severity": "error", "code": f"missing_{field}", "message": f"fictional record needs {field}"})
    if issues:
        return issues

    expected = equipment_plan(record)
    if record.get("era_profile", {}).get("era") != expected["era_profile"]["era"]:
        issues.append(
            {
                "severity": "error",
                "code": "era_profile_mismatch",
                "message": f"era {record.get('era_profile', {}).get('era')!r} does not match {expected['era_profile']['era']!r}",
            }
        )

    equipment = record.get("equipment", {})
    main = equipment.get("main_battery", {}) if isinstance(equipment, dict) else {}
    arm = record.get("armament", {})
    main_cal = _num(arm.get("main_caliber_mm"))
    equip_cal = _num(main.get("caliber_mm"))
    if main_cal is not None and equip_cal is not None and abs(main_cal - equip_cal) > 1.0:
        issues.append(
            {
                "severity": "error",
                "code": "main_battery_equipment_mismatch",
                "message": f"equipment main caliber {equip_cal} does not match armament {main_cal}",
            }
        )

    cand = expected["equipment_candidates"]["main_battery"]
    if main_cal is not None and not (cand["caliber_mm_min"] - 25 <= main_cal <= cand["caliber_mm_max"] + 25):
        issues.append(
            {
                "severity": "warn",
                "code": "main_battery_candidate_range",
                "message": f"main caliber {main_cal} outside candidate range {cand['caliber_mm_min']}-{cand['caliber_mm_max']}",
            }
        )

    armor_scheme = record.get("armor_scheme", {})
    scheme_belt = _num(armor_scheme.get("belt_mm")) if isinstance(armor_scheme, dict) else None
    belt = _num(record.get("armor", {}).get("belt_mm"))
    if belt is not None and scheme_belt is not None and abs(belt - scheme_belt) > 1.0:
        issues.append(
            {
                "severity": "error",
                "code": "armor_scheme_belt_mismatch",
                "message": f"armor_scheme belt {scheme_belt} does not match armor belt {belt}",
            }
        )

    year = year_value(record)
    fire_control = equipment.get("fire_control", {}) if isinstance(equipment, dict) else {}
    if fire_control.get("radar") and year is not None and year < 1938:
        issues.append(
            {
                "severity": "error",
                "code": "radar_too_early",
                "message": f"radar fire control is too early for year {year}",
            }
        )

    aviation = equipment.get("aviation", {}) if isinstance(equipment, dict) else {}
    if aviation.get("catapult") and year is not None and year < 1925:
        issues.append(
            {
                "severity": "warn",
                "code": "catapult_too_early",
                "message": f"aviation catapult is early for year {year}",
            }
        )

    return issues
