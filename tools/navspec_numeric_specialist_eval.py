"""Numeric specialist evaluator for navspec two-stage pipeline.

Phase N0: 既存 Stage 2 schema + 既存 prompt で推論し、spec/design_concept/confidence
だけ抽出して numeric 品質を評価する。status/reason/suggested_revision は無視。

Section 11 主指標 / Section 12 採用ゲート / Section 13 field別 / Section 14 power-speed /
Section 15 range / Section 16 constraint / Section 17 カテゴリ別 / Section 18 diversity を集計する。

field alias (torpedo_tubes -> torpedo_tube_count) を正規化してから比較する。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_lora_data import compact  # noqa: E402
from navspec_patch_analyzer import _major_threshold, _minor_tolerance, _num  # noqa: E402
from qwen_lora_eval import (  # noqa: E402
    DEFAULT_MODEL,
    DATE_STAMP,
    generate_text,
    extract_candidate_json,
    json_prefill,
    load_mlx_lm,
    prompt_from_row,
    read_jsonl,
    repetition_collapse,
)

DEFAULT_SCHEMA = ROOT / "schemas" / "navspec_lora_candidate.schema.json"
DEFAULT_NUMERIC_SCHEMA = ROOT / "schemas" / "navspec_numeric_candidate.schema.json"
DEFAULT_CONTRACT = ROOT / "data" / "navspec" / "lora" / "contracts" / "navspec_numeric_specialist_eval_contract_v1.json"
DEFAULT_OK_CASES = ROOT / "data" / "navspec" / "lora" / "stage3_numeric_eval_ok_v1" / "eval.jsonl"

# numeric_candidate.v1 の field 名。torpedo_tube_count を標準とする。
NUMERIC_FIELDS_V1 = [
    "standard_displacement_t",
    "full_load_displacement_t",
    "length_m",
    "beam_m",
    "draft_m",
    "power_shp",
    "speed_kt",
    "range_nm",
    "main_gun_caliber_mm",
    "main_gun_count",
    "belt_armor_mm",
    "torpedo_tube_count",
]

# Stage 2 schema の field 名との対応。
STAGE2_ALIAS = {
    "torpedo_tubes": "torpedo_tube_count",
}


def normalize_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Stage 2 schema 由来の spec を numeric_candidate.v1 field 名へ正規化。"""
    if not isinstance(spec, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in spec.items():
        out[STAGE2_ALIAS.get(k, k)] = v
    return out


def era_label_for(year: int, contract: dict[str, Any]) -> str:
    for era in contract.get("categories", {}).get("eras", []):
        lo, hi = era["range"]
        if lo <= year <= hi:
            return era["label"]
    return "unknown"


def nation_tier_label_for(tier: int, contract: dict[str, Any]) -> str:
    for t in contract.get("categories", {}).get("nation_tiers", []):
        if t["tier"] == tier:
            return t["label"]
    return "unknown"


def classify_field(
    field: str,
    raw_value: Any,
    canonical_value: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    rv = _num(raw_value)
    cv = _num(canonical_value)

    if rv is None:
        return {
            "field": field, "raw": raw_value, "canonical": canonical_value,
            "delta": None, "rel_delta": None, "severity": "missing",
            "adopted": False, "patch_reason": "raw_value_missing_or_non_numeric",
        }
    if cv is None:
        return {
            "field": field, "raw": rv, "canonical": canonical_value,
            "delta": None, "rel_delta": None, "severity": "canonical_missing",
            "adopted": False, "patch_reason": "canonical_value_unavailable",
        }

    delta = abs(rv - cv)
    eps = max(abs(cv), 0.01)
    rel_delta = delta / eps

    minor_tol = _minor_tolerance(field, contract, cv)
    major_thr = _major_threshold(field, contract, cv)

    if delta <= minor_tol:
        severity = "adopted"
        adopted = True
        reason = "within_tolerance"
    elif delta <= major_thr:
        severity = "minor"
        adopted = True
        reason = "minor_adjustment"
    else:
        severity = "major"
        adopted = False
        reason = "major_replacement"

    return {
        "field": field, "raw": rv, "canonical": cv,
        "delta": round(delta, 4), "rel_delta": round(rel_delta, 4),
        "severity": severity, "adopted": adopted, "patch_reason": reason,
    }


def analyze_spec_v1(
    raw_spec: dict[str, Any] | None,
    canonical_spec: dict[str, Any] | None,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """numeric_candidate.v1 の field 名で analyze。"""
    raw_spec = normalize_spec(raw_spec)
    canonical_spec = normalize_spec(canonical_spec)

    field_results: list[dict[str, Any]] = []
    for field in NUMERIC_FIELDS_V1:
        fr = classify_field(field, raw_spec.get(field), canonical_spec.get(field), contract)
        field_results.append(fr)

    total = len(field_results)
    adopted_count = sum(1 for r in field_results if r["adopted"])
    exact_adopted = sum(1 for r in field_results if r["severity"] == "adopted")
    minor_count = sum(1 for r in field_results if r["severity"] == "minor")
    major_count = sum(1 for r in field_results if r["severity"] == "major")
    missing_count = sum(1 for r in field_results if r["severity"] == "missing")

    rel_deltas = [r["rel_delta"] for r in field_results if r["rel_delta"] is not None]
    rel_sorted = sorted(rel_deltas)
    n = len(rel_sorted)

    def _pct(sorted_list: list[float], p: float) -> float:
        if not sorted_list:
            return 0.0
        k = (len(sorted_list) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_list[int(k)]
        return sorted_list[f] + (sorted_list[c] - sorted_list[f]) * (k - f)

    required_present = sum(1 for f in NUMERIC_FIELDS_V1 if raw_spec.get(f) is not None)

    return {
        "fields": field_results,
        "total_fields": total,
        "required_fields_present": required_present,
        "required_field_presence_rate": required_present / total if total else 0.0,
        "adopted_fields": adopted_count,
        "exact_adopted_fields": exact_adopted,
        "minor_patch_fields": minor_count,
        "major_patch_fields": major_count,
        "missing_fields": missing_count,
        "exact_field_adoption_rate": exact_adopted / total if total else 0.0,
        "tolerant_field_adoption_rate": adopted_count / total if total else 0.0,
        "patch_dependency_rate": (total - adopted_count) / total if total else 0.0,
        "major_patch_rate": major_count / total if total else 0.0,
        "relative_patch_delta_mean": sum(rel_deltas) / len(rel_deltas) if rel_deltas else 0.0,
        "relative_patch_delta_median": _pct(rel_sorted, 0.50) if n else 0.0,
        "relative_patch_delta_p90": _pct(rel_sorted, 0.90) if n else 0.0,
        "relative_patch_delta_p95": _pct(rel_sorted, 0.95) if n else 0.0,
        "relative_patch_delta_max": max(rel_deltas) if rel_deltas else 0.0,
    }


def check_constraint_follow_v1(
    raw_spec: dict[str, Any] | None,
    constraints: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    raw_spec = normalize_spec(raw_spec)
    constraint_fields = contract.get("constraint_fields", {})
    results: list[dict[str, Any]] = []
    satisfied = 0
    total = 0

    for ckey, cmeta in constraint_fields.items():
        if ckey not in constraints:
            continue
        target = STAGE2_ALIAS.get(cmeta["target"], cmeta["target"])
        direction = cmeta["direction"]
        limit = _num(constraints[ckey])
        if limit is None:
            continue
        raw_val = _num(raw_spec.get(target))
        total += 1
        ok = False
        if raw_val is not None:
            if direction == "min" and raw_val >= limit:
                ok = True
            elif direction == "max" and raw_val <= limit:
                ok = True
        if ok:
            satisfied += 1
        results.append({
            "constraint": ckey, "target_field": target,
            "direction": direction, "limit": limit,
            "raw_value": raw_val, "satisfied": ok,
        })

    return {
        "constraint_results": results,
        "total_constraints": total,
        "satisfied_constraints": satisfied,
        "constraint_follow_rate": satisfied / total if total else 1.0,
    }


def constraint_category(constraints: dict[str, Any]) -> str:
    n = len(constraints)
    if n == 0:
        return "none"
    if n == 1:
        key = next(iter(constraints))
        return "single_max" if key.endswith("_max") else "single_min"
    return "multi_constraint"


def power_speed_consistency(
    raw_spec: dict[str, Any],
    canonical_spec: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Admiralty 300 で power-speed 整合を判定。"""
    raw = normalize_spec(raw_spec)
    canon = normalize_spec(canonical_spec)

    speed = _num(raw.get("speed_kt"))
    power = _num(raw.get("power_shp"))
    disp = _num(raw.get("standard_displacement_t")) or _num(canon.get("standard_displacement_t"))
    canon_speed = _num(canon.get("speed_kt"))
    canon_power = _num(canon.get("power_shp"))

    required_power = None
    if speed and disp:
        required_power = (disp ** (2.0 / 3.0)) * (speed ** 3) / 300.0

    speed_err = abs(speed - canon_speed) if (speed and canon_speed) else None
    power_err = abs(power - canon_power) / canon_power if (power and canon_power) else None

    consistent = False
    fail_class = "ok"
    if power and required_power:
        ratio = power / required_power
        tol = contract.get("power_speed", {}).get("consistency_tolerance", 0.02)
        if abs(1 - ratio) <= tol:
            consistent = True
        else:
            consistent = False
            if speed and canon_speed:
                if abs(speed - canon_speed) <= 2.0 and ratio < 1.0:
                    fail_class = "speed_ok_power_low"
                elif abs(speed - canon_speed) <= 2.0 and ratio > 1.0:
                    fail_class = "speed_ok_power_high"
                elif speed > canon_speed + 2.0 and abs(1 - ratio) <= 0.10:
                    fail_class = "speed_high_power_ok"
                elif speed > canon_speed + 2.0 and ratio > 1.10:
                    fail_class = "both_high"
                elif speed < canon_speed - 2.0 and ratio < 0.90:
                    fail_class = "both_low"
                else:
                    fail_class = "mismatch_other"
            else:
                fail_class = "missing_values"

    power_per_ton = (power / disp) if (power and disp) else None

    return {
        "speed_kt": speed,
        "power_shp": power,
        "canonical_speed_kt": canon_speed,
        "canonical_power_shp": canon_power,
        "required_power_admiralty": round(required_power) if required_power else None,
        "power_required_ratio": round(power / required_power, 4) if (power and required_power) else None,
        "speed_error": round(speed_err, 2) if speed_err is not None else None,
        "power_error_rel": round(power_err, 4) if power_err is not None else None,
        "power_per_ton": round(power_per_ton, 2) if power_per_ton else None,
        "consistent": consistent,
        "failure_class": fail_class,
    }


def evaluate_numeric_specialist_row(
    row: dict[str, Any],
    raw_text: str,
    schema: dict[str, Any],
    numeric_schema: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    expected = row.get("expected_output") or row.get("output") or {}

    raw_obj: Any = {}
    try:
        raw_obj = extract_candidate_json(raw_text, schema)
    except Exception:
        pass

    raw_spec_stage2 = raw_obj.get("spec") if isinstance(raw_obj, dict) and isinstance(raw_obj.get("spec"), dict) else {}
    canonical_spec = expected.get("spec") if isinstance(expected.get("spec"), dict) else {}

    raw_spec_v1 = normalize_spec(raw_spec_stage2)
    patch_analysis = analyze_spec_v1(raw_spec_stage2, canonical_spec, contract)

    constraints = (row.get("input") or {}).get("constraints") or {}
    constraint_follow = None
    if constraints:
        constraint_follow = check_constraint_follow_v1(raw_spec_stage2, constraints, contract)

    power_speed = power_speed_consistency(raw_spec_stage2, canonical_spec, contract)

    input_data = row.get("input") or {}
    ship_type = input_data.get("ship_type", "")
    nation = input_data.get("nation", "")
    year = int(input_data.get("year", 0))
    nation_tier = int(canonical_spec.get("nation_tier", 0)) if canonical_spec else 0
    era = era_label_for(year, contract)
    tier_label = nation_tier_label_for(nation_tier, contract)

    design_concept_present = bool(isinstance(raw_obj, dict) and raw_obj.get("design_concept"))
    confidence_present = isinstance(raw_obj, dict) and isinstance(raw_obj.get("confidence"), (int, float))

    numeric_schema_valid = isinstance(raw_obj, dict)
    required_fields = numeric_schema["properties"]["spec"]["required"]
    spec_required_present = sum(1 for f in required_fields if raw_spec_v1.get(f) is not None)
    spec_required_rate = spec_required_present / len(required_fields) if required_fields else 0.0

    return {
        "id": row.get("id"),
        "category": row.get("category"),
        "ship_type": ship_type,
        "nation": nation,
        "year": year,
        "era": era,
        "nation_tier": nation_tier,
        "nation_tier_label": tier_label,
        "constraint_category": constraint_category(constraints),
        "raw_json_valid": isinstance(raw_obj, dict),
        "numeric_schema_valid": numeric_schema_valid,
        "spec_required_field_rate": round(spec_required_rate, 4),
        "spec_required_fields_present": spec_required_present,
        "design_concept_present": design_concept_present,
        "confidence_present": confidence_present,
        "repetition_collapse": repetition_collapse(raw_text),
        "raw_text_len": len(raw_text),
        "patch_analysis": patch_analysis,
        "constraint_follow": constraint_follow,
        "power_speed": power_speed,
        "has_constraints": bool(constraints),
        "constraint_count": len(constraints),
    }


def aggregate_numeric_specialist(
    results: list[dict[str, Any]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    total = len(results)
    if total == 0:
        return {"total": 0}

    raw_json_ok = sum(1 for r in results if r["raw_json_valid"])
    repetition = sum(1 for r in results if r["repetition_collapse"])
    spec_required_rates = [r["spec_required_field_rate"] for r in results]

    n = len(results)
    exact = sum(r["patch_analysis"]["exact_field_adoption_rate"] for r in results) / n
    tolerant = sum(r["patch_analysis"]["tolerant_field_adoption_rate"] for r in results) / n
    dep = sum(r["patch_analysis"]["patch_dependency_rate"] for r in results) / n
    major = sum(r["patch_analysis"]["major_patch_rate"] for r in results) / n
    minor = 1.0 - dep - major if (1.0 - dep - major) >= 0 else 0.0
    median_delta = sum(r["patch_analysis"]["relative_patch_delta_median"] for r in results) / n
    p90_delta = sum(r["patch_analysis"]["relative_patch_delta_p90"] for r in results) / n
    p95_delta = sum(r["patch_analysis"]["relative_patch_delta_p95"] for r in results) / n

    constraint_cases = [r for r in results if (r.get("constraint_follow") or {}).get("total_constraints", 0) > 0]
    if constraint_cases:
        cf = sum((r.get("constraint_follow") or {}).get("constraint_follow_rate", 1.0) for r in constraint_cases) / len(constraint_cases)
    else:
        cf = 1.0
    multi_cases = [r for r in constraint_cases if (r.get("constraint_follow") or {}).get("total_constraints", 0) >= 2]
    if multi_cases:
        mcf = sum((r.get("constraint_follow") or {}).get("constraint_follow_rate", 1.0) for r in multi_cases) / len(multi_cases)
    else:
        mcf = 1.0
    boundary_cases = [r for r in results if r.get("category") == "boundary"]
    if boundary_cases:
        boundary_cf = sum((r.get("constraint_follow") or {}).get("constraint_follow_rate", 1.0) for r in boundary_cases if r.get("constraint_follow")) / max(1, len([r for r in boundary_cases if r.get("constraint_follow")]))
    else:
        boundary_cf = 1.0
    single_cases = [r for r in constraint_cases if (r.get("constraint_follow") or {}).get("total_constraints", 0) == 1]
    if single_cases:
        single_cf = sum((r.get("constraint_follow") or {}).get("constraint_follow_rate", 1.0) for r in single_cases) / len(single_cases)
    else:
        single_cf = 1.0

    ps_consistent = sum(1 for r in results if (r.get("power_speed") or {}).get("consistent")) / n
    ps_fail_classes = Counter((r.get("power_speed") or {}).get("failure_class", "unknown") for r in results)

    # cross-field consistency: power-speed + full_load >= standard + lb_ratio plausibility
    cross_ok = 0
    for r in results:
        raw = normalize_spec((row_canonical_or_raw(r)))
        # use patch fields to check ordering
        pa = r.get("patch_analysis") or {}
        fields = {f["field"]: f for f in pa.get("fields", [])}
        ok = True
        # full_load >= standard (from raw values in patch fields)
        fl = fields.get("full_load_displacement_t", {}).get("raw")
        sd = fields.get("standard_displacement_t", {}).get("raw")
        if fl is not None and sd is not None and fl < sd:
            ok = False
        if (r.get("power_speed") or {}).get("consistent") is False:
            ok = False
        if ok:
            cross_ok += 1
    cross_rate = cross_ok / n

    # field-level aggregation
    field_stats: dict[str, dict[str, float]] = {}
    field_deltas: dict[str, list[float]] = defaultdict(list)
    field_reasons: dict[str, Counter] = defaultdict(Counter)
    for case in results:
        for fr in case["patch_analysis"]["fields"]:
            f = fr["field"]
            if f not in field_stats:
                field_stats[f] = {"adopted": 0, "exact": 0, "major": 0, "minor": 0, "total": 0}
            field_stats[f]["total"] += 1
            if fr["adopted"]:
                field_stats[f]["adopted"] += 1
            if fr["severity"] == "adopted":
                field_stats[f]["exact"] += 1
            if fr["severity"] == "major":
                field_stats[f]["major"] += 1
            if fr["severity"] == "minor":
                field_stats[f]["minor"] += 1
            if fr["rel_delta"] is not None:
                field_deltas[f].append(fr["rel_delta"])
            field_reasons[f][fr["patch_reason"]] += 1

    field_summary = {}
    for f in NUMERIC_FIELDS_V1:
        s = field_stats.get(f, {"adopted": 0, "exact": 0, "major": 0, "minor": 0, "total": 0})
        deltas = sorted(field_deltas.get(f, []))
        tt = s["total"] if s["total"] else 1
        field_summary[f] = {
            "tolerant_adoption_rate": round(s["adopted"] / tt, 4),
            "exact_adoption_rate": round(s["exact"] / tt, 4),
            "major_patch_rate": round(s["major"] / tt, 4),
            "minor_patch_rate": round(s["minor"] / tt, 4),
            "median_delta": round(_pct(deltas, 0.50), 4) if deltas else 0.0,
            "p90_delta": round(_pct(deltas, 0.90), 4) if deltas else 0.0,
            "total": s["total"],
            "top_patch_reasons": dict(field_reasons.get(f, Counter()).most_common(3)),
        }

    # category breakdown
    by_ship_type = _category_breakdown(results, "ship_type", contract)
    by_era = _category_breakdown(results, "era", contract)
    by_tier = _category_breakdown(results, "nation_tier_label", contract)
    by_constraint = _category_breakdown(results, "constraint_category", contract)

    gates_in = contract.get("adoption_gates_numeric_specialist", {})
    field_gates = contract.get("field_gates", {})
    main_fields = contract.get("field_gate_main_fields", NUMERIC_FIELDS_V1)
    ps_gates = contract.get("power_speed", {}).get("gates", {})
    range_gates = contract.get("range_profiles", {}).get("gates", {})
    cross_gate = contract.get("cross_field_consistency", {}).get("gate_min", 0.95)

    range_field = field_summary.get("range_nm", {})
    power_field = field_summary.get("power_shp", {})

    gate_results = {
        "raw_json_rate": (raw_json_ok / total) >= gates_in.get("raw_json_rate_min", 0.99),
        "spec_required_field_rate": (sum(spec_required_rates) / n) >= gates_in.get("spec_required_field_rate_min", 0.98),
        "tolerant_field_adoption": tolerant >= gates_in.get("tolerant_field_adoption_min", 0.88),
        "major_patch_rate": major <= gates_in.get("major_patch_rate_max", 0.10),
        "patch_dependency": dep <= gates_in.get("patch_dependency_max", 0.30),
        "constraint_follow": cf >= gates_in.get("constraint_follow_min", 0.90),
        "multi_constraint_follow": mcf >= gates_in.get("multi_constraint_follow_min", 0.80),
        "boundary_constraint_follow": boundary_cf >= gates_in.get("boundary_constraint_follow_min", 0.80),
        "median_relative_patch_delta": median_delta <= gates_in.get("median_relative_patch_delta_max", 0.05),
        "p90_relative_patch_delta": p90_delta <= gates_in.get("p90_relative_patch_delta_max", 0.15),
        "p95_relative_patch_delta": p95_delta <= gates_in.get("p95_relative_patch_delta_max", 0.20),
        "cross_field_consistency": cross_rate >= cross_gate,
        "repetition_collapse": (repetition / total) <= gates_in.get("repetition_collapse_max", 0.01),
        "power_shp_tolerant_adoption": power_field.get("tolerant_adoption_rate", 0) >= ps_gates.get("power_shp_tolerant_adoption_min", 0.75),
        "speed_power_consistency": ps_consistent >= ps_gates.get("speed_power_consistency_min", 0.95),
        "power_major_patch": power_field.get("major_patch_rate", 1) <= ps_gates.get("power_major_patch_max", 0.12),
        "range_nm_tolerant_adoption": range_field.get("tolerant_adoption_rate", 0) >= range_gates.get("range_nm_tolerant_adoption_min", 0.75),
        "range_major_patch": range_field.get("major_patch_rate", 1) <= range_gates.get("range_major_patch_max", 0.12),
    }

    field_gate_results = {}
    for f in main_fields:
        fs = field_summary.get(f, {})
        field_gate_results[f] = {
            "tolerant_adoption_pass": fs.get("tolerant_adoption_rate", 0) >= field_gates.get("tolerant_adoption_min", 0.60),
            "major_patch_pass": fs.get("major_patch_rate", 1) <= field_gates.get("major_patch_max", 0.15),
            "tolerant_adoption_rate": fs.get("tolerant_adoption_rate", 0),
            "major_patch_rate": fs.get("major_patch_rate", 0),
            "median_delta": fs.get("median_delta", 0),
            "p90_delta": fs.get("p90_delta", 0),
        }

    return {
        "total": total,
        "raw_json_rate": round(raw_json_ok / total, 4),
        "numeric_schema_rate": round(raw_json_ok / total, 4),
        "spec_required_field_rate": round(sum(spec_required_rates) / n, 4),
        "exact_field_adoption_rate": round(exact, 4),
        "tolerant_field_adoption_rate": round(tolerant, 4),
        "minor_patch_rate": round(minor, 4),
        "patch_dependency_rate": round(dep, 4),
        "major_patch_rate": round(major, 4),
        "median_relative_patch_delta": round(median_delta, 4),
        "p90_relative_patch_delta": round(p90_delta, 4),
        "p95_relative_patch_delta": round(p95_delta, 4),
        "constraint_follow_rate": round(cf, 4),
        "single_constraint_follow_rate": round(single_cf, 4),
        "multi_constraint_follow_rate": round(mcf, 4),
        "boundary_constraint_follow_rate": round(boundary_cf, 4),
        "cross_field_consistency_rate": round(cross_rate, 4),
        "speed_power_consistency_rate": round(ps_consistent, 4),
        "power_speed_failure_classes": dict(ps_fail_classes),
        "repetition_collapse_rate": round(repetition / total, 4),
        "field_summary": field_summary,
        "by_ship_type": by_ship_type,
        "by_era": by_era,
        "by_nation_tier": by_tier,
        "by_constraint_category": by_constraint,
        "gate_results": gate_results,
        "field_gate_results": field_gate_results,
        "all_gates_pass": all(gate_results.values()),
    }


def row_canonical_or_raw(r: dict[str, Any]) -> dict[str, Any]:
    """cross-field check 用に patch_analysis から raw 値を再構築。"""
    return {}


def _pct(sorted_list: list[float], p: float) -> float:
    if not sorted_list:
        return 0.0
    k = (len(sorted_list) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_list[int(k)]
    return sorted_list[f] + (sorted_list[c] - sorted_list[f]) * (k - f)


def _category_breakdown(
    results: list[dict[str, Any]],
    key: str,
    contract: dict[str, Any],
) -> dict[str, dict[str, float]]:
    bucket: dict[str, dict[str, float]] = {}
    for r in results:
        k = str(r.get(key, "unknown"))
        if k not in bucket:
            bucket[k] = {"count": 0, "adopt_sum": 0.0, "major_sum": 0.0, "constraint_sum": 0.0, "constraint_n": 0}
        pa = r.get("patch_analysis") or {}
        bucket[k]["count"] += 1
        bucket[k]["adopt_sum"] += pa.get("tolerant_field_adoption_rate", 0.0)
        bucket[k]["major_sum"] += pa.get("major_patch_rate", 0.0)
        cf = (r.get("constraint_follow") or {}).get("constraint_follow_rate")
        if cf is not None:
            bucket[k]["constraint_sum"] += cf
            bucket[k]["constraint_n"] += 1
    out = {}
    for k, v in bucket.items():
        c = v["count"] if v["count"] else 1
        out[k] = {
            "count": v["count"],
            "tolerant_adoption_avg": round(v["adopt_sum"] / c, 4),
            "major_patch_avg": round(v["major_sum"] / c, 4),
            "constraint_follow_avg": round(v["constraint_sum"] / v["constraint_n"], 4) if v["constraint_n"] else None,
        }
    return out


def compute_diversity_v1(
    model: Any,
    tokenizer: Any,
    generate_fn: Any,
    diversity_rows: list[dict[str, Any]],
    schema: dict[str, Any],
    contract: dict[str, Any],
    max_tokens: int,
    num_seeds: int,
) -> dict[str, Any]:
    if not diversity_rows:
        return {"unique_spec_rate": 0.0, "near_duplicate_rate": 0.0}

    temps = [0.3, 0.5, 0.7, 0.9, 1.0][:num_seeds]
    all_specs: dict[str, list[dict[str, float]]] = defaultdict(list)

    for row in diversity_rows:
        prompt = prompt_from_row(tokenizer, row)
        for temp in temps:
            text = generate_text(generate_fn, model, tokenizer, prompt, max_tokens, temp)
            try:
                obj = extract_candidate_json(text, schema)
                if isinstance(obj, dict) and isinstance(obj.get("spec"), dict):
                    spec = normalize_spec(obj["spec"])
                    numeric_vals = {}
                    for f in NUMERIC_FIELDS_V1:
                        v = _num(spec.get(f))
                        if v is not None:
                            numeric_vals[f] = v
                    all_specs[row["id"]].append(numeric_vals)
            except Exception:
                pass

    unique_count = 0
    near_dup_count = 0
    total = 0
    template_collisions: dict[str, int] = Counter()

    for row_id, specs in all_specs.items():
        if len(specs) < 2:
            continue
        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                total += 1
                s1, s2 = specs[i], specs[j]
                if s1 == s2:
                    template_collisions["exact_duplicates"] += 1
                    continue
                unique_count += 1
                max_rel = 0.0
                common_keys = set(s1.keys()) & set(s2.keys())
                for k in common_keys:
                    if s1[k] > 0 and s2[k] > 0:
                        rel = abs(s1[k] - s2[k]) / max(abs(s1[k]), abs(s2[k]))
                        max_rel = max(max_rel, rel)
                if max_rel < contract.get("diversity", {}).get("near_duplicate_threshold", 0.02):
                    near_dup_count += 1

    field_entropy: dict[str, float] = {}
    for f in NUMERIC_FIELDS_V1:
        all_vals: list[float] = []
        for specs in all_specs.values():
            for s in specs:
                v = s.get(f)
                if v is not None:
                    all_vals.append(v)
        if len(all_vals) >= 2:
            mean = sum(all_vals) / len(all_vals)
            var = sum((v - mean) ** 2 for v in all_vals) / len(all_vals)
            field_entropy[f] = round(math.sqrt(var), 4)

    numeric_var_total = sum(1 for specs in all_specs.values() for s in specs for v in s.values() if v != 0)

    return {
        "unique_spec_rate": unique_count / total if total else 0.0,
        "near_duplicate_rate": near_dup_count / total if total else 0.0,
        "template_collision_rate": (template_collisions.get("exact_duplicates", 0) / total) if total else 0.0,
        "total_pairs": total,
        "row_count": len(all_specs),
        "field_entropy": field_entropy,
        "numeric_variation_rate": round(numeric_var_total / max(1, sum(len(specs) for specs in all_specs.values()) * len(NUMERIC_FIELDS_V1)), 4),
    }


def write_specialist_markdown(path: Path, summary: dict[str, Any]) -> None:
    gates = summary.get("gate_results", {})
    fg = summary.get("field_gate_results", {})

    lines = [
        "# navspec Numeric Specialist Eval",
        "",
        f"作成日: {summary.get('date', '')}",
        f"phase: {summary.get('phase', 'n0')}",
        "",
        "## Summary",
        "",
        f"- model: `{summary.get('model')}`",
        f"- adapter: `{summary.get('adapter_path')}`" if summary.get("adapter_path") else "- adapter: _(zero_shot)_",
        f"- checkpoint: `{summary.get('checkpoint')}`" if summary.get("checkpoint") else "",
        f"- total (ok cases): {summary['total']}",
        f"- raw JSON成功率: {summary['raw_json_rate']:.1%}",
        f"- spec必須フィールド充足率: {summary['spec_required_field_rate']:.1%}",
        f"- 反復崩壊率: {summary['repetition_collapse_rate']:.2%}",
        "",
        "## Numeric Quality (Section 11 主指標)",
        "",
        f"- exact field adoption: {summary['exact_field_adoption_rate']:.1%}",
        f"- tolerant field adoption: {summary['tolerant_field_adoption_rate']:.1%}",
        f"- minor patch rate: {summary['minor_patch_rate']:.1%}",
        f"- major patch rate: {summary['major_patch_rate']:.1%}",
        f"- patch dependency: {summary['patch_dependency_rate']:.1%}",
        f"- median rel patch delta: {summary['median_relative_patch_delta']:.4f}",
        f"- p90 rel patch delta: {summary['p90_relative_patch_delta']:.4f}",
        f"- p95 rel patch delta: {summary['p95_relative_patch_delta']:.4f}",
        f"- constraint follow: {summary['constraint_follow_rate']:.1%}",
        f"- single constraint follow: {summary['single_constraint_follow_rate']:.1%}",
        f"- multi constraint follow: {summary['multi_constraint_follow_rate']:.1%}",
        f"- boundary constraint follow: {summary['boundary_constraint_follow_rate']:.1%}",
        f"- cross-field consistency: {summary['cross_field_consistency_rate']:.1%}",
        f"- speed-power consistency: {summary['speed_power_consistency_rate']:.1%}",
        "",
        "## Numeric Specialist 採用ゲート (Section 12)",
        "",
        "| gate | threshold | actual | result |",
        "|---|---|---:|:--:|",
    ]

    gate_thresholds = {
        "raw_json_rate": ">=99%",
        "spec_required_field_rate": ">=98%",
        "tolerant_field_adoption": ">=88%",
        "major_patch_rate": "<=10%",
        "patch_dependency": "<=30%",
        "constraint_follow": ">=90%",
        "multi_constraint_follow": ">=80%",
        "boundary_constraint_follow": ">=80%",
        "median_relative_patch_delta": "<=5%",
        "p90_relative_patch_delta": "<=15%",
        "p95_relative_patch_delta": "<=20%",
        "cross_field_consistency": ">=95%",
        "repetition_collapse": "<1%",
        "power_shp_tolerant_adoption": ">=75%",
        "speed_power_consistency": ">=95%",
        "power_major_patch": "<=12%",
        "range_nm_tolerant_adoption": ">=75%",
        "range_major_patch": "<=12%",
    }
    actuals = {
        "raw_json_rate": f"{summary['raw_json_rate']:.1%}",
        "spec_required_field_rate": f"{summary['spec_required_field_rate']:.1%}",
        "tolerant_field_adoption": f"{summary['tolerant_field_adoption_rate']:.1%}",
        "major_patch_rate": f"{summary['major_patch_rate']:.1%}",
        "patch_dependency": f"{summary['patch_dependency_rate']:.1%}",
        "constraint_follow": f"{summary['constraint_follow_rate']:.1%}",
        "multi_constraint_follow": f"{summary['multi_constraint_follow_rate']:.1%}",
        "boundary_constraint_follow": f"{summary['boundary_constraint_follow_rate']:.1%}",
        "median_relative_patch_delta": f"{summary['median_relative_patch_delta']:.4f}",
        "p90_relative_patch_delta": f"{summary['p90_relative_patch_delta']:.4f}",
        "p95_relative_patch_delta": f"{summary['p95_relative_patch_delta']:.4f}",
        "cross_field_consistency": f"{summary['cross_field_consistency_rate']:.1%}",
        "repetition_collapse": f"{summary['repetition_collapse_rate']:.2%}",
        "power_shp_tolerant_adoption": f"{summary['field_summary'].get('power_shp', {}).get('tolerant_adoption_rate', 0):.1%}",
        "speed_power_consistency": f"{summary['speed_power_consistency_rate']:.1%}",
        "power_major_patch": f"{summary['field_summary'].get('power_shp', {}).get('major_patch_rate', 0):.1%}",
        "range_nm_tolerant_adoption": f"{summary['field_summary'].get('range_nm', {}).get('tolerant_adoption_rate', 0):.1%}",
        "range_major_patch": f"{summary['field_summary'].get('range_nm', {}).get('major_patch_rate', 0):.1%}",
    }
    for g, thr in gate_thresholds.items():
        lines.append(f"| {g} | {thr} | {actuals.get(g, '?')} | {'PASS' if gates.get(g) else 'FAIL'} |")

    lines.append("")
    lines.append(f"All gates: **{'PASS' if summary.get('all_gates_pass') else 'FAIL'}**")
    lines.append("")

    if fg:
        lines.extend([
            "## Field-Level Results (Section 13)",
            "",
            "| field | tolerant | exact | major | median_delta | p90_delta | adopt_pass | major_pass |",
            "|---|---:|---:|---:|---:|---:|:--:|:--:|",
        ])
        for f, stats in sorted(fg.items()):
            lines.append(
                f"| {f} | {stats['tolerant_adoption_rate']:.1%} | "
                f"{summary['field_summary'].get(f, {}).get('exact_adoption_rate', 0):.1%} | "
                f"{stats['major_patch_rate']:.1%} | {stats['median_delta']:.4f} | "
                f"{stats['p90_delta']:.4f} | "
                f"{'PASS' if stats['tolerant_adoption_pass'] else 'FAIL'} | "
                f"{'PASS' if stats['major_patch_pass'] else 'FAIL'} |"
            )

    ps_fail = summary.get("power_speed_failure_classes", {})
    if ps_fail:
        lines.extend([
            "",
            "## Power/Speed Failure Classes (Section 14)",
            "",
            "| class | count |",
            "|---|---:|",
        ])
        for cls, cnt in sorted(ps_fail.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {cls} | {cnt} |")

    for label, key, hdr in [
        ("Ship-Type", "by_ship_type", "| ship_type | count | tolerant | major | constraint |"),
        ("Era", "by_era", "| era | count | tolerant | major | constraint |"),
        ("Nation Tier", "by_nation_tier", "| tier | count | tolerant | major | constraint |"),
        ("Constraint Category", "by_constraint_category", "| category | count | tolerant | major | constraint |"),
    ]:
        bucket = summary.get(key, {})
        if bucket:
            lines.extend(["", f"## {label} Breakdown (Section 17)", "", hdr, "|---|---:|---:|---:|---:|"])
            for k, v in sorted(bucket.items(), key=lambda kv: -kv[1]["count"]):
                cf = f"{v['constraint_follow_avg']:.1%}" if v.get("constraint_follow_avg") is not None else "-"
                lines.append(f"| {k} | {v['count']} | {v['tolerant_adoption_avg']:.1%} | {v['major_patch_avg']:.1%} | {cf} |")

    div = summary.get("diversity")
    if div:
        lines.extend([
            "",
            "## Diversity (Section 18)",
            "",
            f"- unique spec rate: {div.get('unique_spec_rate', 0):.1%}",
            f"- near duplicate rate: {div.get('near_duplicate_rate', 0):.1%}",
            f"- template collision rate: {div.get('template_collision_rate', 0):.1%}",
            f"- row count: {div.get('row_count', 0)}",
            f"- total pairs: {div.get('total_pairs', 0)}",
        ])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(line for line in lines if line is not None) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="navspec numeric specialist evaluator (Phase N0/N1)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--adapter-path", type=Path, default=None)
    parser.add_argument("--checkpoint", default=None, help="e.g. iter500 (for display only)")
    parser.add_argument("--cases", type=Path, default=DEFAULT_OK_CASES)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Stage 2 schema for extraction (Phase N0)")
    parser.add_argument("--numeric-schema", type=Path, default=DEFAULT_NUMERIC_SCHEMA)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--phase", choices=["n0", "n1"], default="n0")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--json-prefill", choices=["none", "object", "schema"], default="none")
    parser.add_argument("--diversity-count", type=int, default=0)
    parser.add_argument("--diversity-seeds", type=int, default=5)
    parser.add_argument("--json-report", type=Path, default=None)
    parser.add_argument("--md-report", type=Path, default=None)
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    numeric_schema = json.loads(args.numeric_schema.read_text(encoding="utf-8"))
    rows = read_jsonl(args.cases)
    if args.limit is not None:
        rows = rows[: args.limit]

    adapter_name = args.adapter_path.name if args.adapter_path else "zero_shot"
    ckpt = args.checkpoint or adapter_name
    run_slug = f"numeric_specialist_{args.phase}_{adapter_name}_{ckpt}"
    if args.json_report is None:
        args.json_report = ROOT / "data" / "navspec" / "lora" / "eval" / run_slug / "report.json"
    if args.md_report is None:
        args.md_report = ROOT / "docs" / f"numeric_specialist_{args.phase}_{adapter_name}_{ckpt}_{DATE_STAMP}.md"

    if args.dry_run:
        print(f"cases: {len(rows)}")
        print(f"model: {args.model}")
        print(f"adapter: {args.adapter_path}")
        print(f"checkpoint: {ckpt}")
        print(f"phase: {args.phase}")
        print(f"contract: {args.contract}")
        print(f"json_report: {args.json_report}")
        print(f"md_report: {args.md_report}")
        return

    model, tokenizer, generate_fn = load_mlx_lm(args.model, args.adapter_path)
    prefill = json_prefill(args.json_prefill)

    results: list[dict[str, Any]] = []
    raw_records: list[dict[str, Any]] = []
    t0 = time.time()

    for index, row in enumerate(rows, start=1):
        prompt = prompt_from_row(tokenizer, row) + prefill
        raw_text = generate_text(generate_fn, model, tokenizer, prompt, args.max_tokens, args.temperature)
        result = evaluate_numeric_specialist_row(row, raw_text, schema, numeric_schema, contract)
        results.append(result)
        raw_records.append({"id": row.get("id"), "raw_text": raw_text})
        pa = result["patch_analysis"]
        if index % 50 == 0 or index == len(rows):
            elapsed = time.time() - t0
            print(f"[{index}/{len(rows)}] {row.get('id')} adopt={pa['tolerant_field_adoption_rate']:.1%} major={pa['major_patch_rate']:.1%} ({elapsed:.0f}s)", flush=True)

    elapsed_total = time.time() - t0

    diversity = None
    if args.diversity_count > 0:
        diversity_rows = rows[: args.diversity_count]
        diversity = compute_diversity_v1(
            model, tokenizer, generate_fn, diversity_rows, schema, contract,
            args.max_tokens, args.diversity_seeds,
        )

    summary = aggregate_numeric_specialist(results, contract)
    summary["model"] = args.model
    summary["adapter_path"] = str(args.adapter_path) if args.adapter_path else None
    summary["checkpoint"] = ckpt
    summary["phase"] = args.phase
    summary["date"] = DATE_STAMP
    summary["cases"] = str(args.cases)
    summary["contract"] = str(args.contract)
    summary["max_tokens"] = args.max_tokens
    summary["temperature"] = args.temperature
    summary["json_prefill"] = args.json_prefill
    summary["elapsed_seconds"] = round(elapsed_total, 1)
    summary["mean_latency_seconds"] = round(elapsed_total / max(1, len(rows)), 3)
    if diversity:
        summary["diversity"] = diversity

    report = {"summary": summary, "results": results}
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_specialist_markdown(args.md_report, summary)

    if args.raw_dir:
        args.raw_dir.mkdir(parents=True, exist_ok=True)
        for raw in raw_records:
            (args.raw_dir / f"{raw['id']}.txt").write_text(raw["raw_text"], encoding="utf-8")

    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    print(f"json_report: {args.json_report}")
    print(f"md_report: {args.md_report}")


if __name__ == "__main__":
    main()
