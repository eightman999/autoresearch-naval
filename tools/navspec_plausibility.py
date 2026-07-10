"""Plausibility evaluator for bounded fictional ShipSpecRecord files."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from navspec_common import read_jsonl, validate_spec  # direct run: tools/ on sys.path
    from navspec_equipment import equipment_plan  # direct run: tools/ on sys.path
    from navspec_eval import missing_required  # direct run: tools/ on sys.path
    from navspec_shiptypes import lb_ratio_band_for  # direct run: tools/ on sys.path
except ImportError:
    from tools.navspec_common import read_jsonl, validate_spec  # package import from repo root
    from tools.navspec_equipment import equipment_plan  # package import from repo root
    from tools.navspec_eval import missing_required  # package import from repo root
    from tools.navspec_shiptypes import lb_ratio_band_for  # package import from repo root


@dataclass(frozen=True)
class RangeBand:
    good_min: float
    good_max: float
    warn_min: float
    warn_max: float


@dataclass(frozen=True)
class Finding:
    severity: str
    category: str
    code: str
    message: str
    penalty: float
    value: float | str | None = None
    expected: str | None = None


VERDICT_ORDER = {"accepted": 0, "review": 1, "rejected": 2}
CORE_CATEGORIES = (
    "structure",
    "fictional_metadata",
    "geometry",
    "propulsion",
    "protection",
    "armament",
    "equipment",
    "bounded_mutation",
    "completeness",
)


LB_BANDS = {
    "destroyer": RangeBand(7.0, 12.5, 5.0, 15.0),
    "cruiser": RangeBand(7.0, 11.5, 5.0, 14.0),
    "battleship": RangeBand(5.5, 8.5, 4.2, 10.0),
    "battlecruiser": RangeBand(6.0, 9.5, 4.5, 11.5),
    "coastal_battleship": RangeBand(4.5, 7.5, 3.5, 9.0),
    "carrier": RangeBand(6.0, 10.5, 4.5, 13.0),
    "submarine": RangeBand(7.0, 13.0, 5.0, 16.0),
    "other": RangeBand(5.0, 10.5, 3.5, 13.0),
}

AREA_BANDS = {
    "destroyer": RangeBand(1.2, 3.5, 0.6, 6.0),
    "cruiser": RangeBand(2.0, 4.8, 0.8, 8.0),
    "battleship": RangeBand(4.0, 7.5, 2.0, 12.0),
    "battlecruiser": RangeBand(3.5, 7.0, 1.5, 11.0),
    "coastal_battleship": RangeBand(2.0, 5.5, 0.8, 7.0),
    "carrier": RangeBand(1.8, 5.0, 0.8, 8.0),
    "submarine": RangeBand(1.0, 4.0, 0.4, 7.0),
    "other": RangeBand(1.0, 5.0, 0.4, 8.0),
}

FN_BANDS = {
    "destroyer": RangeBand(0.28, 0.55, 0.15, 0.70),
    "cruiser": RangeBand(0.22, 0.43, 0.13, 0.60),
    "battleship": RangeBand(0.20, 0.36, 0.13, 0.45),
    "battlecruiser": RangeBand(0.24, 0.42, 0.15, 0.52),
    "coastal_battleship": RangeBand(0.15, 0.34, 0.10, 0.45),
    "carrier": RangeBand(0.18, 0.40, 0.10, 0.55),
    "submarine": RangeBand(0.10, 0.35, 0.06, 0.50),
    "other": RangeBand(0.16, 0.55, 0.08, 0.75),
}

POWER_PER_TON_BANDS = {
    "destroyer": RangeBand(4.0, 25.0, 0.8, 60.0),
    "cruiser": RangeBand(1.5, 12.0, 0.5, 30.0),
    "battleship": RangeBand(0.5, 4.5, 0.15, 8.0),
    "battlecruiser": RangeBand(1.0, 8.0, 0.25, 14.0),
    "coastal_battleship": RangeBand(0.3, 4.0, 0.1, 8.0),
    "carrier": RangeBand(1.0, 10.0, 0.2, 20.0),
    "submarine": RangeBand(0.2, 12.0, 0.05, 30.0),
    "other": RangeBand(0.5, 20.0, 0.05, 45.0),
}

SPEED_BANDS = {
    "destroyer": RangeBand(22.0, 42.0, 12.0, 48.0),
    "cruiser": RangeBand(18.0, 36.0, 10.0, 40.0),
    "battleship": RangeBand(16.0, 32.0, 10.0, 36.0),
    "battlecruiser": RangeBand(24.0, 35.0, 15.0, 38.0),
    "coastal_battleship": RangeBand(12.0, 24.0, 8.0, 30.0),
    "carrier": RangeBand(18.0, 34.0, 10.0, 38.0),
    "submarine": RangeBand(6.0, 25.0, 4.0, 35.0),
    "other": RangeBand(10.0, 35.0, 5.0, 45.0),
}


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def get_path(record: dict[str, Any], *path: str) -> Any:
    value: Any = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def used_displacement(record: dict[str, Any]) -> float | None:
    return numeric(get_path(record, "displacement", "full_t")) or numeric(
        get_path(record, "displacement", "normal_t")
    )


def range_message(label: str, value: float, band: RangeBand, unit: str) -> str:
    suffix = f" {unit}" if unit else ""
    return f"{label} {value:.3g}{suffix} outside normal band {band.good_min:g}-{band.good_max:g}{suffix}"


def add_range_finding(
    findings: list[Finding],
    *,
    category: str,
    code: str,
    label: str,
    value: float | None,
    band: RangeBand,
    unit: str = "",
    minor_penalty: float = 4.0,
    major_penalty: float = 12.0,
) -> None:
    if value is None:
        return
    expected = f"{band.good_min:g}-{band.good_max:g}{(' ' + unit) if unit else ''}"
    if value < band.warn_min or value > band.warn_max:
        findings.append(
            Finding(
                "warn",
                category,
                code,
                range_message(label, value, band, unit)
                + f"; hard band is {band.warn_min:g}-{band.warn_max:g}",
                major_penalty,
                round(value, 4),
                expected,
            )
        )
    elif value < band.good_min or value > band.good_max:
        findings.append(
            Finding(
                "warn",
                category,
                code,
                range_message(label, value, band, unit),
                minor_penalty,
                round(value, 4),
                expected,
            )
        )


def issue_category(code: str) -> str:
    if code.startswith(("missing_", "fictional_", "target_", "derived_", "prefix_", "naming_", "class_name")):
        return "fictional_metadata"
    if code in {"length_breadth_ratio", "displacement_area_ratio", "displacement_range"}:
        return "geometry"
    if code in {"speed_range", "admiralty_coefficient"}:
        return "propulsion"
    if code in {"belt_range", "armor_scheme_belt_mismatch"}:
        return "protection"
    if code in {"main_caliber_range", "main_barrel_count", "main_battery_candidate_range"}:
        return "armament"
    if code in {"era_profile_mismatch", "main_battery_equipment_mismatch", "radar_too_early", "catapult_too_early"}:
        return "equipment"
    return "structure"


def finding_from_rule_issue(issue: dict[str, str]) -> Finding:
    severity = issue.get("severity") or "warn"
    code = issue.get("code") or "rule_issue"
    penalty = 25.0 if severity == "error" else 6.0
    if code in {"missing_displacement", "main_battery_candidate_range", "displacement_area_ratio"}:
        penalty = 5.0
    if code in {"length_breadth_ratio", "admiralty_coefficient", "main_barrel_count"}:
        penalty = 6.0
    return Finding(
        severity,
        issue_category(code),
        code,
        issue.get("message") or code,
        penalty,
    )


def check_structure(record: dict[str, Any], findings: list[Finding]) -> None:
    for field in missing_required(record):
        findings.append(
            Finding(
                "error",
                "structure",
                "missing_required_field",
                f"missing required top-level field: {field}",
                25.0,
                field,
            )
        )

    for path in (
        ("displacement", "normal_t"),
        ("displacement", "full_t"),
        ("dimensions", "length_m"),
        ("dimensions", "breadth_m"),
        ("dimensions", "draught_m"),
        ("propulsion", "power_shp"),
        ("propulsion", "speed_kn"),
        ("armor", "belt_mm"),
        ("armament", "main_caliber_mm"),
        ("armament", "main_barrels"),
        ("complement", "crew"),
    ):
        value = get_path(record, *path)
        number = numeric(value)
        if value is not None and number is None:
            findings.append(
                Finding(
                    "error",
                    "structure",
                    "non_numeric_value",
                    f"{'.'.join(path)} must be numeric or null",
                    15.0,
                    repr(value),
                )
            )
        elif number is not None and number <= 0:
            findings.append(
                Finding(
                    "error",
                    "structure",
                    "non_positive_value",
                    f"{'.'.join(path)} must be positive when present",
                    20.0,
                    number,
                )
            )


def check_completeness(record: dict[str, Any], findings: list[Finding]) -> None:
    if used_displacement(record) is None:
        findings.append(
            Finding(
                "warn",
                "completeness",
                "missing_scoring_input",
                "missing displacement; plausibility score has lower confidence",
                3.0,
                "displacement.normal_t|full_t",
            )
        )

    required_for_scoring = (
        (("dimensions", "length_m"), "length"),
        (("dimensions", "breadth_m"), "breadth"),
        (("propulsion", "speed_kn"), "speed"),
        (("propulsion", "power_shp"), "power"),
        (("armament", "main_caliber_mm"), "main caliber"),
    )
    for path, label in required_for_scoring:
        if get_path(record, *path) is None:
            findings.append(
                Finding(
                    "warn",
                    "completeness",
                    "missing_scoring_input",
                    f"missing {label}; plausibility score has lower confidence",
                    3.0,
                    ".".join(path),
                )
            )


def check_fictional_metadata(record: dict[str, Any], findings: list[Finding]) -> None:
    source_kind = record.get("source_kind")
    if source_kind != "fictional_bounded":
        findings.append(
            Finding(
                "warn",
                "fictional_metadata",
                "not_fictional_bounded",
                f"source_kind is {source_kind!r}; bounded-fiction checks are limited",
                4.0,
                str(source_kind),
                "fictional_bounded",
            )
        )
        return

    if record.get("source_url") is not None:
        findings.append(
            Finding(
                "error",
                "fictional_metadata",
                "fictional_source_url",
                "fictional records must not carry a historical source_url",
                25.0,
                str(record.get("source_url")),
                "null",
            )
        )

    record_id = str(record.get("record_id") or "")
    if not record_id.startswith("fictional:"):
        findings.append(
            Finding(
                "error",
                "fictional_metadata",
                "fictional_record_id",
                "fictional record_id must start with fictional:",
                25.0,
                record_id,
            )
        )

    derived = record.get("derived_from")
    if not isinstance(derived, dict) or not derived.get("record_id"):
        findings.append(
            Finding(
                "error",
                "fictional_metadata",
                "missing_derived_from",
                "fictional records need derived_from.record_id",
                25.0,
            )
        )

    constraints = record.get("fictional_constraints")
    if not isinstance(constraints, dict):
        findings.append(
            Finding(
                "warn",
                "fictional_metadata",
                "missing_fictional_constraints",
                "fictional_constraints is missing; mutation-bounds cannot be checked",
                10.0,
            )
        )
        return

    if constraints.get("method") and constraints.get("method") != "bounded_mutation":
        findings.append(
            Finding(
                "warn",
                "fictional_metadata",
                "unknown_fictional_method",
                f"unknown fictional method: {constraints.get('method')!r}",
                5.0,
                str(constraints.get("method")),
                "bounded_mutation",
            )
        )

    scale_ranges = {
        "displacement_scale": (0.65, 1.45),
        "armor_scale": (0.55, 1.60),
        "speed_delta_kn": (-6.0, 6.0),
        "main_caliber_delta_mm": (-76.0, 76.0),
    }
    for key, (lo, hi) in scale_ranges.items():
        if key not in constraints:
            continue
        value = numeric(constraints.get(key))
        if value is None or not lo <= value <= hi:
            findings.append(
                Finding(
                    "warn",
                    "fictional_metadata",
                    "fictional_constraint_out_of_band",
                    f"{key}={constraints.get(key)!r} is outside the bounded-mutation envelope",
                    8.0,
                    str(constraints.get(key)),
                    f"{lo:g}-{hi:g}",
                )
            )


def check_geometry(record: dict[str, Any], findings: list[Finding], metrics: dict[str, float]) -> None:
    hull_kind = record.get("hull_kind") or "other"
    disp = used_displacement(record)
    normal = numeric(get_path(record, "displacement", "normal_t"))
    full = numeric(get_path(record, "displacement", "full_t"))
    length = numeric(get_path(record, "dimensions", "length_m"))
    breadth = numeric(get_path(record, "dimensions", "breadth_m"))
    draught = numeric(get_path(record, "dimensions", "draught_m"))

    if normal is not None and full is not None:
        if full < normal:
            findings.append(
                Finding(
                    "error",
                    "geometry",
                    "full_displacement_below_normal",
                    f"full displacement {full:g} t is below normal displacement {normal:g} t",
                    25.0,
                    full,
                    f">= {normal:g} t",
                )
            )
        elif full > normal * 1.55:
            findings.append(
                Finding(
                    "warn",
                    "geometry",
                    "full_normal_displacement_gap",
                    f"full displacement is {full / normal:.2f}x normal displacement",
                    8.0,
                    round(full / normal, 4),
                    "<= 1.55x",
                )
            )

    if length and breadth:
        lb_ratio = length / breadth
        metrics["length_breadth_ratio"] = round(lb_ratio, 4)
        lb_good_min, lb_good_max = lb_ratio_band_for(record.get("type"), str(hull_kind))
        add_range_finding(
            findings,
            category="geometry",
            code="length_breadth_ratio_band",
            label="length/breadth ratio",
            value=lb_ratio,
            band=RangeBand(lb_good_min, lb_good_max, max(3.0, lb_good_min - 2.0), lb_good_max + 2.0),
        )

    if disp and length and breadth:
        area_loading = disp / (length * breadth)
        metrics["displacement_area_loading"] = round(area_loading, 4)
        add_range_finding(
            findings,
            category="geometry",
            code="displacement_area_loading_band",
            label="displacement per L*B",
            value=area_loading,
            band=AREA_BANDS.get(str(hull_kind), AREA_BANDS["other"]),
            unit="t/m2",
        )

    if disp and length and breadth and draught:
        block_coeff = disp / (1.025 * length * breadth * draught)
        metrics["block_coefficient_estimate"] = round(block_coeff, 4)
        band = RangeBand(0.45, 0.75, 0.30, 0.92)
        if hull_kind == "submarine":
            band = RangeBand(0.50, 0.90, 0.35, 1.05)
        add_range_finding(
            findings,
            category="geometry",
            code="block_coefficient_band",
            label="estimated block coefficient",
            value=block_coeff,
            band=band,
            minor_penalty=5.0,
            major_penalty=14.0,
        )

    if breadth and draught:
        draught_breadth = draught / breadth
        metrics["draught_breadth_ratio"] = round(draught_breadth, 4)
        add_range_finding(
            findings,
            category="geometry",
            code="draught_breadth_ratio_band",
            label="draught/breadth ratio",
            value=draught_breadth,
            band=RangeBand(0.18, 0.45, 0.08, 0.62),
            minor_penalty=3.0,
            major_penalty=10.0,
        )


def check_propulsion(record: dict[str, Any], findings: list[Finding], metrics: dict[str, float]) -> None:
    hull_kind = record.get("hull_kind") or "other"
    disp = used_displacement(record)
    length = numeric(get_path(record, "dimensions", "length_m"))
    speed = numeric(get_path(record, "propulsion", "speed_kn"))
    power = numeric(get_path(record, "propulsion", "power_shp"))

    add_range_finding(
        findings,
        category="propulsion",
        code="speed_role_band",
        label="speed",
        value=speed,
        band=SPEED_BANDS.get(str(hull_kind), SPEED_BANDS["other"]),
        unit="kn",
        minor_penalty=4.0,
        major_penalty=14.0,
    )

    if length and speed:
        froude = speed * 0.514444 / math.sqrt(9.80665 * length)
        metrics["froude_number"] = round(froude, 4)
        add_range_finding(
            findings,
            category="propulsion",
            code="froude_number_band",
            label="Froude number",
            value=froude,
            band=FN_BANDS.get(str(hull_kind), FN_BANDS["other"]),
            minor_penalty=5.0,
            major_penalty=15.0,
        )

    if disp and power:
        power_per_ton = power / disp
        metrics["power_per_ton"] = round(power_per_ton, 4)
        add_range_finding(
            findings,
            category="propulsion",
            code="power_per_ton_band",
            label="power per displacement ton",
            value=power_per_ton,
            band=POWER_PER_TON_BANDS.get(str(hull_kind), POWER_PER_TON_BANDS["other"]),
            unit="shp/t",
            minor_penalty=4.0,
            major_penalty=12.0,
        )

    if disp and speed and power:
        admiralty = disp ** (2.0 / 3.0) * speed**3 / power
        if math.isfinite(admiralty):
            metrics["admiralty_coefficient"] = round(admiralty, 4)
            band = RangeBand(120.0, 450.0, 45.0, 900.0)
            if hull_kind in {"destroyer", "submarine", "other"}:
                band = RangeBand(80.0, 420.0, 25.0, 900.0)
            add_range_finding(
                findings,
                category="propulsion",
                code="admiralty_coefficient_band",
                label="Admiralty coefficient",
                value=admiralty,
                band=band,
                minor_penalty=4.0,
                major_penalty=12.0,
            )


def check_protection(record: dict[str, Any], findings: list[Finding], metrics: dict[str, float]) -> None:
    hull_kind = record.get("hull_kind") or "other"
    year = numeric(record.get("year"))
    belt = numeric(get_path(record, "armor", "belt_mm"))
    main_cal = numeric(get_path(record, "armament", "main_caliber_mm"))

    capital = hull_kind in {"battleship", "battlecruiser", "coastal_battleship"}
    if capital and belt is None and (year is None or year < 1950):
        findings.append(
            Finding(
                "warn",
                "protection",
                "missing_capital_ship_belt",
                "capital-ship fictional record lacks belt armor",
                8.0,
                expected="belt_mm present or explicit no-armor rationale",
            )
        )

    if belt is not None:
        bands = {
            "destroyer": RangeBand(0.0, 50.0, 0.0, 100.0),
            "cruiser": RangeBand(25.0, 180.0, 0.0, 250.0),
            "battleship": RangeBand(150.0, 450.0, 75.0, 600.0),
            "battlecruiser": RangeBand(100.0, 350.0, 50.0, 500.0),
            "coastal_battleship": RangeBand(75.0, 300.0, 25.0, 450.0),
            "carrier": RangeBand(0.0, 160.0, 0.0, 250.0),
            "submarine": RangeBand(0.0, 40.0, 0.0, 80.0),
            "other": RangeBand(0.0, 180.0, 0.0, 300.0),
        }
        add_range_finding(
            findings,
            category="protection",
            code="belt_by_hull_band",
            label="belt armor",
            value=belt,
            band=bands.get(str(hull_kind), bands["other"]),
            unit="mm",
            minor_penalty=5.0,
            major_penalty=15.0,
        )

    if belt is not None and main_cal:
        belt_caliber_ratio = belt / main_cal
        metrics["belt_to_main_caliber_ratio"] = round(belt_caliber_ratio, 4)
        if capital:
            band = RangeBand(0.45, 1.20, 0.20, 1.55)
        elif hull_kind == "cruiser":
            band = RangeBand(0.10, 1.00, 0.00, 1.35)
        else:
            band = RangeBand(0.00, 0.80, 0.00, 1.10)
        add_range_finding(
            findings,
            category="protection",
            code="belt_to_caliber_band",
            label="belt/main-caliber ratio",
            value=belt_caliber_ratio,
            band=band,
            minor_penalty=4.0,
            major_penalty=12.0,
        )


def sum_main_mount_barrels(record: dict[str, Any], main_cal: float) -> int | None:
    mounts = get_path(record, "armament", "mounts")
    if not isinstance(mounts, list):
        return None
    total = 0
    matched = False
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        caliber = numeric(mount.get("caliber_mm"))
        if caliber is None or abs(caliber - main_cal) > 1.0:
            continue
        matched = True
        barrels = numeric(mount.get("barrels"))
        if barrels is None:
            barrels = (numeric(mount.get("mount_count")) or 0.0) * (numeric(mount.get("barrels_per_mount")) or 1.0)
        total += int(round(barrels))
    return total if matched else None


def check_armament(record: dict[str, Any], findings: list[Finding], metrics: dict[str, float]) -> None:
    hull_kind = record.get("hull_kind") or "other"
    disp = used_displacement(record)
    main_cal = numeric(get_path(record, "armament", "main_caliber_mm"))
    main_barrels = numeric(get_path(record, "armament", "main_barrels"))

    if main_cal is not None:
        caliber_bands = {
            "destroyer": RangeBand(76.0, 130.0, 37.0, 155.0),
            "cruiser": RangeBand(127.0, 203.0, 76.0, 240.0),
            "battleship": RangeBand(254.0, 460.0, 203.0, 520.0),
            "battlecruiser": RangeBand(254.0, 460.0, 203.0, 520.0),
            "coastal_battleship": RangeBand(203.0, 356.0, 152.0, 420.0),
            "carrier": RangeBand(76.0, 152.0, 37.0, 203.0),
            "submarine": RangeBand(0.0, 152.0, 0.0, 203.0),
            "other": RangeBand(57.0, 203.0, 20.0, 254.0),
        }
        add_range_finding(
            findings,
            category="armament",
            code="main_caliber_by_hull_band",
            label="main caliber",
            value=main_cal,
            band=caliber_bands.get(str(hull_kind), caliber_bands["other"]),
            unit="mm",
            minor_penalty=6.0,
            major_penalty=16.0,
        )

    if main_cal is not None and main_barrels is not None:
        metrics["main_barrels"] = round(main_barrels, 4)
        if hull_kind in {"battleship", "battlecruiser", "coastal_battleship"} and main_cal >= 254 and main_barrels < 4:
            findings.append(
                Finding(
                    "warn",
                    "armament",
                    "capital_ship_too_few_main_barrels",
                    f"capital ship has only {main_barrels:g} main barrels",
                    10.0,
                    main_barrels,
                    ">= 4",
                )
            )
        if hull_kind in {"destroyer", "submarine", "other"} and main_cal >= 203 and main_barrels <= 1:
            findings.append(
                Finding(
                    "warn",
                    "armament",
                    "oversized_single_main_gun",
                    f"{hull_kind} record has a single {main_cal:g} mm main gun",
                    10.0,
                    main_barrels,
                    "multi-gun fit or smaller caliber",
                )
            )

    if main_cal is not None and main_barrels is not None:
        mount_barrels = sum_main_mount_barrels(record, main_cal)
        if mount_barrels is not None and abs(mount_barrels - main_barrels) > 0.5:
            findings.append(
                Finding(
                    "warn",
                    "armament",
                    "main_mount_barrel_mismatch",
                    f"main_barrels={main_barrels:g} but mounts sum to {mount_barrels}",
                    8.0,
                    main_barrels,
                    str(mount_barrels),
                )
            )

    if disp and main_cal:
        caliber_per_kton = main_cal / (disp / 1000.0)
        metrics["main_caliber_per_kton"] = round(caliber_per_kton, 4)
        if hull_kind in {"battleship", "battlecruiser", "coastal_battleship"}:
            band = RangeBand(6.0, 28.0, 3.0, 45.0)
        elif hull_kind == "cruiser":
            band = RangeBand(10.0, 70.0, 5.0, 120.0)
        else:
            band = RangeBand(8.0, 90.0, 2.0, 180.0)
        add_range_finding(
            findings,
            category="armament",
            code="main_caliber_displacement_band",
            label="main-caliber per thousand tons",
            value=caliber_per_kton,
            band=band,
            unit="mm/kt",
            minor_penalty=4.0,
            major_penalty=10.0,
        )


def check_equipment(record: dict[str, Any], findings: list[Finding], rule_codes: set[str]) -> None:
    if record.get("source_kind") != "fictional_bounded":
        return
    try:
        expected = equipment_plan(record)
    except Exception as exc:  # noqa: BLE001 - keep evaluator diagnostic, not fatal.
        findings.append(
            Finding(
                "warn",
                "equipment",
                "equipment_plan_failed",
                f"could not derive expected equipment plan: {exc}",
                8.0,
            )
        )
        return

    expected_era = expected.get("era_profile", {}).get("era")
    actual_era = get_path(record, "era_profile", "era")
    if actual_era and expected_era and actual_era != expected_era and "era_profile_mismatch" not in rule_codes:
        findings.append(
            Finding(
                "error",
                "equipment",
                "era_profile_mismatch",
                f"era_profile.era={actual_era!r} does not match derived era {expected_era!r}",
                25.0,
                str(actual_era),
                str(expected_era),
            )
        )

    candidates = expected.get("equipment_candidates", {}).get("main_battery", {})
    main_cal = numeric(get_path(record, "armament", "main_caliber_mm"))
    if main_cal is not None and candidates:
        lo = numeric(candidates.get("caliber_mm_min"))
        hi = numeric(candidates.get("caliber_mm_max"))
        if (
            lo is not None
            and hi is not None
            and not (lo - 25.0 <= main_cal <= hi + 25.0)
            and "main_battery_candidate_range" not in rule_codes
        ):
            findings.append(
                Finding(
                    "warn",
                    "equipment",
                    "main_battery_outside_candidate_band",
                    f"main caliber {main_cal:g} mm is outside derived candidate band {lo:g}-{hi:g} mm",
                    8.0,
                    main_cal,
                    f"{lo:g}-{hi:g} mm",
                )
            )

    stored = record.get("validation_issues")
    if isinstance(stored, list):
        current_codes = sorted(issue.get("code") for issue in validate_spec(record))
        stored_codes = sorted(issue.get("code") for issue in stored if isinstance(issue, dict))
        if current_codes != stored_codes:
            findings.append(
                Finding(
                    "warn",
                    "equipment",
                    "stale_validation_issues",
                    "stored validation_issues differ from current rule evaluation",
                    3.0,
                    ",".join(str(code) for code in stored_codes),
                    ",".join(str(code) for code in current_codes),
                )
            )


def relative_delta(actual: float, expected: float) -> float:
    if expected == 0:
        return abs(actual - expected)
    return abs(actual - expected) / abs(expected)


def add_expected_numeric_check(
    findings: list[Finding],
    *,
    code: str,
    label: str,
    actual: float | None,
    expected: float | None,
    tolerance: float,
    unit: str = "",
    penalty: float = 9.0,
) -> None:
    if actual is None or expected is None:
        return
    if relative_delta(actual, expected) <= tolerance:
        return
    suffix = f" {unit}" if unit else ""
    findings.append(
        Finding(
            "warn",
            "bounded_mutation",
            code,
            f"{label} {actual:g}{suffix} differs from bounded-mutation expectation {expected:g}{suffix}",
            penalty,
            round(actual, 4),
            f"{expected:g}{suffix} +/- {tolerance * 100:g}%",
        )
    )


def check_bounded_mutation(
    record: dict[str, Any],
    findings: list[Finding],
    base_index: dict[str, dict[str, Any]] | None,
) -> None:
    if record.get("source_kind") != "fictional_bounded":
        return
    if not base_index:
        return

    derived = record.get("derived_from")
    derived_id = derived.get("record_id") if isinstance(derived, dict) else None
    if not derived_id:
        return
    base = base_index.get(str(derived_id))
    if base is None:
        findings.append(
            Finding(
                "warn",
                "bounded_mutation",
                "base_record_not_found",
                f"derived_from.record_id {derived_id!r} was not found in --base-records",
                6.0,
                str(derived_id),
            )
        )
        return

    constraints = record.get("fictional_constraints")
    if not isinstance(constraints, dict):
        return

    displacement_scale = numeric(constraints.get("displacement_scale"))
    armor_scale = numeric(constraints.get("armor_scale"))
    speed_delta = numeric(constraints.get("speed_delta_kn"))
    caliber_delta = numeric(constraints.get("main_caliber_delta_mm"))

    base_disp = used_displacement(base)
    actual_disp = used_displacement(record)
    if base_disp and displacement_scale:
        add_expected_numeric_check(
            findings,
            code="displacement_scale_mismatch",
            label="displacement",
            actual=actual_disp,
            expected=base_disp * displacement_scale,
            tolerance=0.04,
            unit="t",
        )

        dim_scale = displacement_scale ** (1.0 / 3.0)
        for field, label in (("length_m", "length"), ("breadth_m", "breadth"), ("draught_m", "draught")):
            add_expected_numeric_check(
                findings,
                code=f"{field}_scale_mismatch",
                label=label,
                actual=numeric(get_path(record, "dimensions", field)),
                expected=(numeric(get_path(base, "dimensions", field)) or 0.0) * dim_scale
                if numeric(get_path(base, "dimensions", field)) is not None
                else None,
                tolerance=0.04,
                unit="m",
                penalty=7.0,
            )

    base_speed = numeric(get_path(base, "propulsion", "speed_kn"))
    actual_speed = numeric(get_path(record, "propulsion", "speed_kn"))
    if base_speed is not None and speed_delta is not None:
        expected_speed = max(10.0, min(36.0, base_speed + speed_delta))
        if actual_speed is not None and abs(actual_speed - expected_speed) > 1.0:
            findings.append(
                Finding(
                    "warn",
                    "bounded_mutation",
                    "speed_delta_mismatch",
                    f"speed {actual_speed:g} kn differs from bounded-mutation expectation {expected_speed:g} kn",
                    8.0,
                    actual_speed,
                    f"{expected_speed:g} kn +/- 1 kn",
                )
            )

    base_belt = numeric(get_path(base, "armor", "belt_mm"))
    actual_belt = numeric(get_path(record, "armor", "belt_mm"))
    if base_belt is not None and armor_scale is not None:
        add_expected_numeric_check(
            findings,
            code="armor_scale_mismatch",
            label="belt armor",
            actual=actual_belt,
            expected=base_belt * armor_scale,
            tolerance=0.08,
            unit="mm",
            penalty=7.0,
        )

    base_caliber = numeric(get_path(base, "armament", "main_caliber_mm"))
    actual_caliber = numeric(get_path(record, "armament", "main_caliber_mm"))
    if base_caliber is not None and caliber_delta is not None:
        expected_caliber = max(203.0, min(510.0, base_caliber + caliber_delta))
        if actual_caliber is not None and abs(actual_caliber - expected_caliber) > 2.0:
            findings.append(
                Finding(
                    "warn",
                    "bounded_mutation",
                    "main_caliber_delta_mismatch",
                    f"main caliber {actual_caliber:g} mm differs from bounded-mutation expectation {expected_caliber:g} mm",
                    8.0,
                    actual_caliber,
                    f"{expected_caliber:g} mm +/- 2 mm",
                )
            )


def category_scores(findings: list[Finding]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for category in CORE_CATEGORIES:
        penalty = sum(f.penalty for f in findings if f.category == category)
        scores[category] = round(max(0.0, 100.0 - penalty), 2)
    return scores


def verdict_for(score: float, findings: list[Finding]) -> str:
    if any(f.severity == "error" for f in findings) or score < 60.0:
        return "rejected"
    if score < 85.0 or any(f.penalty >= 10.0 for f in findings):
        return "review"
    return "accepted"


def evaluate_record(
    record: dict[str, Any],
    *,
    base_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    findings: list[Finding] = []
    metrics: dict[str, float] = {}

    check_structure(record, findings)
    check_fictional_metadata(record, findings)
    check_completeness(record, findings)

    rule_issues = validate_spec(record)
    rule_codes = {str(issue.get("code")) for issue in rule_issues}
    for issue in rule_issues:
        findings.append(finding_from_rule_issue(issue))

    check_geometry(record, findings, metrics)
    check_propulsion(record, findings, metrics)
    check_protection(record, findings, metrics)
    check_armament(record, findings, metrics)
    check_equipment(record, findings, rule_codes)
    check_bounded_mutation(record, findings, base_index)

    penalty = sum(f.penalty for f in findings)
    score = round(max(0.0, 100.0 - penalty), 2)
    return {
        "record_id": record.get("record_id"),
        "class_name": record.get("class_name"),
        "country": record.get("country"),
        "hull_kind": record.get("hull_kind"),
        "year": record.get("year"),
        "score": score,
        "verdict": verdict_for(score, findings),
        "category_scores": category_scores(findings),
        "metrics": metrics,
        "findings": [asdict(f) for f in findings],
    }


def read_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        return read_jsonl(path)
    raw = path.read_text(encoding="utf-8").strip()
    value = json.loads(raw)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    raise ValueError(f"{path} does not contain a JSON object, array, or JSONL records")


def load_base_index(paths: list[Path]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in paths:
        for record in read_records(path):
            record_id = record.get("record_id")
            if record_id:
                index[str(record_id)] = record
    return index


def summarize_reports(path: Path, reports: list[dict[str, Any]]) -> dict[str, Any]:
    verdict_counts = Counter(report["verdict"] for report in reports)
    finding_counts = Counter(
        finding["code"]
        for report in reports
        for finding in report["findings"]
        if finding["severity"] in {"error", "warn"}
    )
    scores = [report["score"] for report in reports]
    return {
        "path": str(path),
        "records": len(reports),
        "verdict_counts": dict(verdict_counts),
        "score_min": min(scores) if scores else None,
        "score_avg": round(sum(scores) / len(scores), 2) if scores else None,
        "score_max": max(scores) if scores else None,
        "top_findings": dict(finding_counts.most_common(12)),
        "reports": reports,
    }


def print_text_report(summary: dict[str, Any], *, show_findings: int) -> None:
    print(f"{summary['path']}:")
    print(f"  records: {summary['records']}")
    print(f"  verdict_counts: {summary['verdict_counts']}")
    print(
        "  score: "
        f"min={summary['score_min']} avg={summary['score_avg']} max={summary['score_max']}"
    )
    print(f"  top_findings: {summary['top_findings']}")

    worst = sorted(summary["reports"], key=lambda report: report["score"])[:5]
    if worst:
        print("  worst_records:")
    for report in worst:
        print(
            "    "
            f"{report['score']:>5} {report['verdict']:<8} "
            f"{report.get('record_id') or '<missing id>'} "
            f"({report.get('class_name') or 'unknown'})"
        )
        for finding in report["findings"][:show_findings]:
            print(
                "      "
                f"{finding['severity'].upper()} {finding['code']}: {finding['message']} "
                f"(-{finding['penalty']:g})"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate fictional ship-spec plausibility")
    parser.add_argument("records", type=Path, nargs="+", help="ShipSpecRecord JSON or JSONL files")
    parser.add_argument(
        "--base-records",
        type=Path,
        action="append",
        default=[],
        help="Historical/base JSON or JSONL files used to check bounded mutations",
    )
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N records per input")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument(
        "--show-findings",
        type=int,
        default=4,
        help="Number of findings to print for each worst record in text mode",
    )
    parser.add_argument(
        "--fail-on",
        choices=("none", "review", "rejected"),
        default="rejected",
        help="Exit non-zero when any record reaches this verdict or worse",
    )
    args = parser.parse_args()

    base_index = load_base_index(args.base_records) if args.base_records else None
    summaries: list[dict[str, Any]] = []
    worst_verdict = "accepted"

    for path in args.records:
        records = read_records(path)
        if args.limit is not None:
            records = records[: args.limit]
        reports = [evaluate_record(record, base_index=base_index) for record in records]
        for report in reports:
            if VERDICT_ORDER[report["verdict"]] > VERDICT_ORDER[worst_verdict]:
                worst_verdict = report["verdict"]
        summaries.append(summarize_reports(path, reports))

    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for index, summary in enumerate(summaries):
            if index:
                print()
            print_text_report(summary, show_findings=args.show_findings)

    if args.fail_on != "none" and VERDICT_ORDER[worst_verdict] >= VERDICT_ORDER[args.fail_on]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
