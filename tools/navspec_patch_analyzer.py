"""Stage 3 patch analyzer: compare raw model spec vs canonical classic spec.

For each numeric field in navspec.lora_candidate.v1 spec, this module classifies
whether the model's raw value was:
  - adopted:  within tolerance of the canonical value (no patch needed)
  - minor:    small adjustment (rounding, boundary clamp)
  - major:    large replacement (>10% relative or threshold)
  - missing:  model did not output the field

The canonical spec is produced by the classic rule engine (make_ok_output).
This module does NOT invoke the model — it only compares two spec dicts.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

DEFAULT_CONTRACT = ROOT / "data" / "navspec" / "lora" / "contracts" / "navspec_stage3_numeric_eval_contract_v1.json"

NUMERIC_SPEC_FIELDS = [
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
    "torpedo_tubes",
]

NON_NUMERIC_SPEC_FIELDS = [
    "nation",
    "year",
    "ship_type",
    "lb_ratio",
    "radar",
    "nation_tier",
]


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def load_contract(path: Path | None = None) -> dict[str, Any]:
    contract_path = path or DEFAULT_CONTRACT
    return json.loads(contract_path.read_text(encoding="utf-8"))


def _minor_tolerance(field: str, contract: dict[str, Any], canonical_value: float) -> float:
    """Return absolute tolerance for minor patch classification."""
    fc = contract["numeric_fields"].get(field, {})
    abs_tol = fc.get("minor_abs_tolerance")
    rel_tol = fc.get("minor_rel_tolerance")
    parts: list[float] = []
    if abs_tol is not None:
        parts.append(abs_tol)
    if rel_tol is not None and canonical_value > 0:
        parts.append(abs(canonical_value) * rel_tol)
    return max(parts) if parts else 0.0


def _major_threshold(field: str, contract: dict[str, Any], canonical_value: float) -> float:
    """Return absolute threshold for major patch classification."""
    fc = contract["numeric_fields"].get(field, {})
    abs_thr = fc.get("major_abs_threshold")
    rel_thr = fc.get("major_rel_threshold")
    parts: list[float] = []
    if abs_thr is not None:
        parts.append(abs_thr)
    if rel_thr is not None and canonical_value > 0:
        parts.append(abs(canonical_value) * rel_thr)
    return max(parts) if parts else abs(canonical_value) * 0.10


def classify_field(
    field: str,
    raw_value: Any,
    canonical_value: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Classify a single numeric field.

    Returns dict with:
      field, raw, canonical, delta, rel_delta, severity, adopted, patch_reason
    """
    rv = _num(raw_value)
    cv = _num(canonical_value)

    if rv is None:
        return {
            "field": field,
            "raw": raw_value,
            "canonical": canonical_value,
            "delta": None,
            "rel_delta": None,
            "severity": "missing",
            "adopted": False,
            "patch_reason": "raw_value_missing_or_non_numeric",
        }

    if cv is None:
        return {
            "field": field,
            "raw": rv,
            "canonical": canonical_value,
            "delta": None,
            "rel_delta": None,
            "severity": "canonical_missing",
            "adopted": False,
            "patch_reason": "canonical_value_unavailable",
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
        "field": field,
        "raw": rv,
        "canonical": cv,
        "delta": round(delta, 4),
        "rel_delta": round(rel_delta, 4),
        "severity": severity,
        "adopted": adopted,
        "patch_reason": reason,
    }


def analyze_spec(
    raw_spec: dict[str, Any] | None,
    canonical_spec: dict[str, Any] | None,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Analyze all numeric fields in a spec pair.

    Returns per-field results plus aggregate metrics.
    """
    raw_spec = raw_spec or {}
    canonical_spec = canonical_spec or {}
    numeric_fields = [f for f in contract.get("numeric_fields", {}) if f in NUMERIC_SPEC_FIELDS]

    field_results: list[dict[str, Any]] = []
    for field in numeric_fields:
        fr = classify_field(field, raw_spec.get(field), canonical_spec.get(field), contract)
        field_results.append(fr)

    total = len(field_results)
    adopted_count = sum(1 for r in field_results if r["adopted"])
    exact_adopted = sum(1 for r in field_results if r["severity"] == "adopted")
    minor_count = sum(1 for r in field_results if r["severity"] == "minor")
    major_count = sum(1 for r in field_results if r["severity"] == "major")
    missing_count = sum(1 for r in field_results if r["severity"] == "missing")

    rel_deltas = [r["rel_delta"] for r in field_results if r["rel_delta"] is not None]

    rel_delta_sorted = sorted(rel_deltas)
    n = len(rel_delta_sorted)

    def _percentile(sorted_list: list[float], p: float) -> float:
        if not sorted_list:
            return 0.0
        k = (len(sorted_list) - 1) * p
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_list[int(k)]
        return sorted_list[f] + (sorted_list[c] - sorted_list[f]) * (k - f)

    return {
        "fields": field_results,
        "total_fields": total,
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
        "relative_patch_delta_median": _percentile(rel_delta_sorted, 0.50) if n else 0.0,
        "relative_patch_delta_p90": _percentile(rel_delta_sorted, 0.90) if n else 0.0,
        "relative_patch_delta_p95": _percentile(rel_delta_sorted, 0.95) if n else 0.0,
        "relative_patch_delta_max": max(rel_deltas) if rel_deltas else 0.0,
    }


def check_constraint_follow(
    raw_spec: dict[str, Any] | None,
    constraints: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Check if raw spec values satisfy user constraints directly (without patching)."""
    raw_spec = raw_spec or {}
    constraint_fields = contract.get("constraint_fields", {})
    results: list[dict[str, Any]] = []
    satisfied = 0
    total = 0

    for ckey, cmeta in constraint_fields.items():
        if ckey not in constraints:
            continue
        target = cmeta["target"]
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
            "constraint": ckey,
            "target_field": target,
            "direction": direction,
            "limit": limit,
            "raw_value": raw_val,
            "satisfied": ok,
        })

    return {
        "constraint_results": results,
        "total_constraints": total,
        "satisfied_constraints": satisfied,
        "constraint_follow_rate": satisfied / total if total else 1.0,
    }


def aggregate_results(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate patch analysis across all evaluated cases (status=ok only)."""
    ok_cases = [r for r in case_results if r.get("status") == "ok" and r.get("patch_analysis")]
    if not ok_cases:
        return {
            "ok_case_count": 0,
            "exact_field_adoption_rate": 0.0,
            "tolerant_field_adoption_rate": 0.0,
            "patch_dependency_rate": 0.0,
            "major_patch_rate": 0.0,
        }

    n = len(ok_cases)
    exact = sum(r["patch_analysis"]["exact_field_adoption_rate"] for r in ok_cases) / n
    tolerant = sum(r["patch_analysis"]["tolerant_field_adoption_rate"] for r in ok_cases) / n
    dep = sum(r["patch_analysis"]["patch_dependency_rate"] for r in ok_cases) / n
    major = sum(r["patch_analysis"]["major_patch_rate"] for r in ok_cases) / n
    median_delta = sum(r["patch_analysis"]["relative_patch_delta_median"] for r in ok_cases) / n
    p90_delta = sum(r["patch_analysis"]["relative_patch_delta_p90"] for r in ok_cases) / n

    constraint_cases = [r for r in ok_cases if (r.get("constraint_follow") or {}).get("total_constraints", 0) > 0]
    if constraint_cases:
        cf = sum((r.get("constraint_follow") or {}).get("constraint_follow_rate", 1.0) for r in constraint_cases) / len(constraint_cases)
    else:
        cf = 1.0

    multi_constraint_cases = [r for r in constraint_cases if (r.get("constraint_follow") or {}).get("total_constraints", 0) >= 2]
    if multi_constraint_cases:
        mcf = sum((r.get("constraint_follow") or {}).get("constraint_follow_rate", 1.0) for r in multi_constraint_cases) / len(multi_constraint_cases)
    else:
        mcf = 1.0

    field_stats: dict[str, dict[str, float]] = {}
    for case in ok_cases:
        for fr in case["patch_analysis"]["fields"]:
            f = fr["field"]
            if f not in field_stats:
                field_stats[f] = {"adopted": 0, "major": 0, "total": 0}
            field_stats[f]["total"] += 1
            if fr["adopted"]:
                field_stats[f]["adopted"] += 1
            if fr["severity"] == "major":
                field_stats[f]["major"] += 1

    field_summary = {}
    for f, s in field_stats.items():
        field_summary[f] = {
            "tolerant_adoption_rate": s["adopted"] / s["total"] if s["total"] else 0.0,
            "major_patch_rate": s["major"] / s["total"] if s["total"] else 0.0,
            "total": s["total"],
        }

    return {
        "ok_case_count": n,
        "exact_field_adoption_rate": round(exact, 4),
        "tolerant_field_adoption_rate": round(tolerant, 4),
        "patch_dependency_rate": round(dep, 4),
        "major_patch_rate": round(major, 4),
        "constraint_follow_rate": round(cf, 4),
        "multi_constraint_follow_rate": round(mcf, 4),
        "relative_patch_delta_median_avg": round(median_delta, 4),
        "relative_patch_delta_p90_avg": round(p90_delta, 4),
        "field_summary": field_summary,
    }
