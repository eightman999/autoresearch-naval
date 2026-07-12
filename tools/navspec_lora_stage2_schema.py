"""Stage 2 navspec LoRA schema validation and reason-code metrics.

Stage 2 は reason を [{"code": str, "message": str}] の構造化オブジェクトにする。
  - status=ok        : reason==[] 、 spec は18必須フィールド
  - status=impossible: reason は1件以上の {code,message} 、 spec は {} または null

この仕様は v1 schema (string reason / 常に18必須 spec) と衝突するため、
専用 validator をここに置き、v1 schema および validate_navspec_output.validate_schema は
一切変更しない。code は REASON_CODES の固定文字列のみを許可する。
"""

from __future__ import annotations

from typing import Any, Iterable

SCHEMA_VERSION = "navspec.lora_candidate.v1"

REASON_CODES = [
    "displacement_limit_exceeded",
    "main_gun_too_large_for_hull",
    "armor_too_heavy_for_hull",
    "speed_power_conflict",
    "range_volume_conflict",
    "speed_armor_range_conflict",
    "era_equipment_unavailable",
    "treaty_limit_exceeded",
    "nation_capability_exceeded",
    "ship_type_role_conflict",
]
REASON_CODE_SET = set(REASON_CODES)
MAX_REASON_CODES = 3

TOP_REQUIRED = [
    "schema_version",
    "status",
    "spec",
    "reason",
    "suggested_revision",
    "design_concept",
    "confidence",
]

SPEC_REQUIRED = [
    "nation",
    "year",
    "ship_type",
    "standard_displacement_t",
    "full_load_displacement_t",
    "length_m",
    "beam_m",
    "draft_m",
    "lb_ratio",
    "power_shp",
    "speed_kt",
    "range_nm",
    "main_gun_caliber_mm",
    "main_gun_count",
    "belt_armor_mm",
    "torpedo_tubes",
    "radar",
    "nation_tier",
]


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def normalize_reason_code_sequence(codes: Iterable[str]) -> list[str]:
    """契約用に reason code をcanonical順・最大3件へ正規化する。

    speed_armor_range_conflict は速力・装甲・航続の複合主原因であり、
    旧Stage 2 goldにだけ残る4件セットでは speed_power_conflict を重複原因
    として除外する。これはStage 2.2以降の厳格定義と同じ扱いである。
    """
    unique = {code for code in codes if code in REASON_CODE_SET}
    if "speed_armor_range_conflict" in unique and len(unique) > MAX_REASON_CODES:
        unique.discard("speed_power_conflict")
    return sorted(unique, key=REASON_CODES.index)[:MAX_REASON_CODES]


def reason_code_contract_errors(reason: Any) -> list[str]:
    """reason配列に固有の件数・enum・重複・canonical順序を検証する。"""
    if not isinstance(reason, list):
        return ["$: reason must be an array"]

    errors: list[str] = []
    codes: list[str] = []
    for index, item in enumerate(reason):
        if not isinstance(item, dict):
            errors.append(f"$.reason[{index}]: must be an object {{code,message}}")
            continue
        code = item.get("code")
        if not isinstance(code, str) or code not in REASON_CODE_SET:
            errors.append(f"$.reason[{index}]: invalid code {code!r}")
        else:
            codes.append(code)
        message = item.get("message")
        if not isinstance(message, str) or not message.strip():
            errors.append(f"$.reason[{index}]: message must be a non-empty string")

    if len(reason) > MAX_REASON_CODES:
        errors.append(f"$.reason: at most {MAX_REASON_CODES} reason codes are allowed")
    if len(codes) != len(set(codes)):
        errors.append("$.reason: duplicate reason codes are not allowed")
    if codes != sorted(codes, key=REASON_CODES.index):
        errors.append("$.reason: reason codes must use canonical order")
    return errors


def validate_stage2(obj: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["$: not an object"]

    for field in TOP_REQUIRED:
        if field not in obj:
            errors.append(f"$: missing required field {field!r}")

    if obj.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"$: schema_version must be {SCHEMA_VERSION!r}, got {obj.get('schema_version')!r}")

    status = obj.get("status")
    if status not in ("ok", "impossible"):
        errors.append(f"$: status must be ok|impossible, got {status!r}")

    reason = obj.get("reason")
    errors.extend(reason_code_contract_errors(reason))
    if not isinstance(reason, list):
        reason = []

    if status == "ok" and len(reason) > 0:
        errors.append("$: status=ok requires reason==[]")
    if status == "impossible" and len(reason) == 0:
        errors.append("$: status=impossible requires at least one reason code")

    spec = obj.get("spec")
    if status == "ok":
        if not isinstance(spec, dict):
            errors.append("$: status=ok requires spec as an object")
        else:
            for field in SPEC_REQUIRED:
                if field not in spec:
                    errors.append(f"$.spec: missing required field {field!r}")
    elif status == "impossible":
        if spec is not None and spec != {}:
            errors.append("$: status=impossible spec must be {} or null")

    revision = obj.get("suggested_revision")
    if not isinstance(revision, dict):
        errors.append("$: suggested_revision must be an object")
    elif status == "impossible" and not revision:
        errors.append("$: status=impossible suggested_revision must be non-empty")

    concept = obj.get("design_concept")
    if not isinstance(concept, str) or not concept.strip():
        errors.append("$: design_concept must be a non-empty string")

    confidence = _num(obj.get("confidence"))
    if confidence is None or not (0.0 <= confidence <= 1.0):
        errors.append(f"$: confidence must be a number in [0,1], got {obj.get('confidence')!r}")

    return errors


def extract_reason_codes(obj: Any) -> set[str]:
    if not isinstance(obj, dict):
        return set()
    reason = obj.get("reason")
    if not isinstance(reason, list):
        return set()
    codes: set[str] = set()
    for item in reason:
        if isinstance(item, dict) and isinstance(item.get("code"), str):
            codes.add(item["code"])
    return codes


def reason_code_metrics(expected_codes: set[str], actual_codes: set[str]) -> dict[str, Any]:
    exact = expected_codes == actual_codes
    if not expected_codes and not actual_codes:
        return {
            "reason_code_exact": True,
            "reason_code_precision": 1.0,
            "reason_code_recall": 1.0,
            "reason_code_f1": 1.0,
            "tp": 0,
            "fp": 0,
            "fn": 0,
        }
    tp = len(expected_codes & actual_codes)
    fp = len(actual_codes - expected_codes)
    fn = len(expected_codes - actual_codes)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "reason_code_exact": exact,
        "reason_code_precision": precision,
        "reason_code_recall": recall,
        "reason_code_f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }
