#!/usr/bin/env python3
"""NavSpec の船体結果へ詳細兵装を加算的に統合するオーケストレータ。

このモジュールは LLM を呼ばない。Stage 2 の status と numeric の確定値を
入力として受け取り、既存の armament retriever / classic validator を順に
実行し、``navspec.pipeline_result.v1`` を返す。

重要な境界:

* Stage 2 の impossible は最終権限であり、numeric / armament は実行しない。
* 詳細兵装の error は統合結果を impossible にするが、Stage 2 の元出力を
  書き換えない。根拠は validation_trace に残す。
* ``ShipSpecRecord`` と ``navspec.armament_candidate.v1`` は変更せず、結果
  envelope から参照する。
* 未知の reason / warning code は結果へ通さない。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_armament_retriever import (  # noqa: E402
    retrieve_available_mounts,
    retrieve_available_weapons,
    retrieve_armament,
)
from navspec_armament_validator import (  # noqa: E402
    ARMAMENT_REASON_CODES,
    aggregate_main_caliber_with_db,
    load_db,
    validate_armament,
)
from navspec_contradiction import check_contradiction  # noqa: E402
from navspec_equipment import equipment_plan  # noqa: E402
from navspec_lora_data import make_ship_record  # noqa: E402

PIPELINE_SCHEMA_VERSION = "navspec.pipeline_result.v1"
ARMAMENT_SCHEMA_VERSION = "navspec.armament_candidate.v1"
SHIP_RECORD_SCHEMA_VERSION = "navspec.ship_spec_record.v1"

DEFAULT_DATA_DIR = ROOT / "data" / "navspec" / "armament"
DEFAULT_RESULT_SCHEMA = ROOT / "schemas" / "navspec_pipeline_result.schema.json"
DEFAULT_CONTRACT = ROOT / "data" / "navspec" / "lora" / "contracts" / "navspec_end_to_end_eval_contract_v1.json"

STAGE2_REASON_CODES = {
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
}
ARMAMENT_REASON_CODE_SET = set(ARMAMENT_REASON_CODES)
KNOWN_REASON_CODES = STAGE2_REASON_CODES | ARMAMENT_REASON_CODE_SET

# DB 側の語彙への写像。暗黙の類似文字列検索は行わず、ここに明記された
# 国家・艦種だけを変換する。未掲載値は候補なし fallback とする。
DB_NATION_BY_COUNTRY = {
    "JP": "JP",
    "Japan": "JP",
    "UK": "UK",
    "United Kingdom": "UK",
    "US": "US",
    "USA": "US",
    "United States": "US",
    "DE": "DE",
    "Germany": "DE",
    "FR": "FR",
    "France": "FR",
    "IT": "IT",
    "Italy": "IT",
    "RU": "RU",
    "Russia": "RU",
    "AH": "AH",
    "Austria-Hungary": "AH",
    "ES": "ES",
    "Spain": "ES",
    "SE": "SE",
    "Sweden": "SE",
    "NL": "NL",
    "Netherlands": "NL",
}

DB_SHIP_TYPE_BY_SPEC = {
    "battleship": "battleship",
    "battlecruiser": "battlecruiser",
    "heavy_cruiser": "heavy_cruiser",
    "light_cruiser": "light_cruiser",
    "destroyer": "destroyer",
    "coastal_defense": "coastal_defense",
    "coastal_defense_ship": "coastal_defense",
    "coastal_battleship": "coastal_defense",
    "fast_battleship": "battleship",
    "dreadnought_battleship": "battleship",
    "pre_dreadnought_battleship": "battleship",
    "large_cruiser": "heavy_cruiser",
    "treaty_cruiser": "heavy_cruiser",
    "armored_cruiser": "heavy_cruiser",
    "protected_cruiser": "light_cruiser",
    "commerce_raider_cruiser": "heavy_cruiser",
    "large_destroyer": "destroyer",
    "anti_air_destroyer": "destroyer",
}

ARMAMENT_SOURCE_FILES = [
    "data/navspec/armament/weapons.jsonl",
    "data/navspec/armament/mounts.jsonl",
    "data/navspec/armament/fire_control.jsonl",
    "data/navspec/armament/ammunition.jsonl",
    "data/navspec/armament/compatibility_rules.json",
]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    """ファイルまたはディレクトリを相対パス込みで安定ハッシュ化する。"""
    path = Path(path)
    if path.is_file():
        return _sha256_bytes(path.read_bytes())
    if path.is_dir():
        digest = hashlib.sha256()
        for child in sorted((p for p in path.rglob("*") if p.is_file()), key=lambda p: p.relative_to(path).as_posix()):
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(child.read_bytes())
        return digest.hexdigest()
    return _sha256_bytes(str(path).encode("utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _contract_metadata(contract_path: Path, schema_path: Path) -> tuple[str, str, str]:
    contract = _load_json(contract_path)
    contract_version = str(contract.get("contract_version") or "navspec-end-to-end-contract-unknown")
    contract_hash = _sha256_path(contract_path)
    result_schema_hash = _sha256_path(schema_path)
    return contract_version, contract_hash, result_schema_hash


def _db_metadata(db: dict[str, Any] | None, data_dir: Path) -> tuple[str, str]:
    if isinstance(db, dict):
        rules = db.get("compatibility_rules")
        if isinstance(rules, dict) and rules.get("rules_version"):
            version = str(rules["rules_version"])
        else:
            version = "navspec.armament-db.unknown"
        db_hash = _sha256_bytes(_canonical_json(db).encode("utf-8"))
        return version, db_hash

    rules = _load_json(data_dir / "compatibility_rules.json")
    version = str(rules.get("rules_version") or "navspec.armament-db.not-loaded")
    return version, _sha256_path(data_dir)


def _case_from_record(record: dict[str, Any]) -> dict[str, Any]:
    armament = record.get("armament") if isinstance(record.get("armament"), dict) else {}
    return {
        "id": str(record.get("record_id") or "pipeline_record"),
        "nation": record.get("country") or "",
        "year": record.get("year"),
        "ship_type": record.get("type") or "",
        "role": "pipeline",
        "constraints": copy.deepcopy(record.get("fictional_constraints") or {}),
        "request": "",
        "displacement_t": (record.get("displacement") or {}).get("normal_t"),
        "beam_m": (record.get("dimensions") or {}).get("breadth_m"),
        "main_gun_caliber_mm": armament.get("main_caliber_mm"),
        "main_gun_count": armament.get("main_barrels"),
    }


def _normalise_request(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """case / ShipSpecRecord / {input: case} を同じ case へ正規化する。"""
    source = request
    record: dict[str, Any] | None = None
    if isinstance(request.get("ship_spec"), dict):
        record = copy.deepcopy(request["ship_spec"])
        source = _case_from_record(record)
    elif request.get("schema_version") == SHIP_RECORD_SCHEMA_VERSION:
        record = copy.deepcopy(request)
        source = _case_from_record(record)
    elif isinstance(request.get("record"), dict):
        record = copy.deepcopy(request["record"])
        source = _case_from_record(record)
    elif isinstance(request.get("input"), dict):
        source = copy.deepcopy(request["input"])

    case = copy.deepcopy(source)
    if not case.get("nation") and case.get("country"):
        case["nation"] = case["country"]
    if not case.get("ship_type") and case.get("type"):
        case["ship_type"] = case["type"]
    hull = case.get("hull") if isinstance(case.get("hull"), dict) else {}
    if case.get("displacement_t") is None:
        case["displacement_t"] = hull.get("displacement_t")
    if case.get("beam_m") is None:
        case["beam_m"] = hull.get("beam_m")
    case.setdefault("role", "pipeline")
    case.setdefault("constraints", {})
    case.setdefault("request", "")
    case.setdefault("id", "pipeline_case")
    return case, record


def _apply_case_hull(record: dict[str, Any], case: dict[str, Any]) -> None:
    """入力に明示された hull 寸法を生成済み record へ反映する。"""
    displacement = _number(case.get("displacement_t"))
    beam = _number(case.get("beam_m"))
    hull = case.get("hull") if isinstance(case.get("hull"), dict) else {}
    if displacement is None:
        displacement = _number(hull.get("displacement_t"))
    if beam is None:
        beam = _number(hull.get("beam_m"))

    if displacement is not None:
        record.setdefault("displacement", {})["normal_t"] = displacement
        record["displacement"]["full_t"] = round(displacement * 1.12, 1)
        record["displacement"]["normal_raw"] = "pipeline input"
        record["displacement"]["full_raw"] = "pipeline input"
    if beam is not None:
        record.setdefault("dimensions", {})["breadth_m"] = beam
        record["dimensions"]["breadth_raw"] = "pipeline input"


def _apply_numeric_spec(record: dict[str, Any], numeric_spec: dict[str, Any] | None) -> None:
    if not isinstance(numeric_spec, dict):
        return
    displacement = _number(numeric_spec.get("standard_displacement_t"))
    full_load = _number(numeric_spec.get("full_load_displacement_t"))
    if displacement is not None:
        record.setdefault("displacement", {})["normal_t"] = displacement
    if full_load is not None:
        record.setdefault("displacement", {})["full_t"] = full_load
    elif displacement is not None:
        record.setdefault("displacement", {})["full_t"] = round(displacement * 1.12, 1)

    dimensions = record.setdefault("dimensions", {})
    for field, record_field in (
        ("length_m", "length_m"),
        ("beam_m", "breadth_m"),
        ("draft_m", "draught_m"),
    ):
        value = _number(numeric_spec.get(field))
        if value is not None:
            dimensions[record_field] = value

    propulsion = record.setdefault("propulsion", {})
    for field, record_field in (("power_shp", "power_shp"), ("speed_kt", "speed_kn")):
        value = _number(numeric_spec.get(field))
        if value is not None:
            propulsion[record_field] = value

    armament = record.setdefault("armament", {})
    main_caliber = _number(numeric_spec.get("main_gun_caliber_mm"))
    main_count = _integer(numeric_spec.get("main_gun_count"))
    belt = _number(numeric_spec.get("belt_armor_mm"))
    if main_caliber is not None:
        armament["main_caliber_mm"] = main_caliber
    if main_count is not None:
        armament["main_barrels"] = main_count
    if belt is not None:
        record.setdefault("armor", {})["belt_mm"] = belt


def _refresh_equipment(record: dict[str, Any]) -> None:
    try:
        equipment = equipment_plan(record)
    except (KeyError, TypeError, ValueError):
        return
    record["era_profile"] = equipment["era_profile"]
    record["design_doctrine"] = equipment["design_doctrine"]
    record["equipment"] = equipment["equipment"]
    record["armor_scheme"] = equipment["armor_scheme"]
    record["equipment_candidates"] = equipment["equipment_candidates"]


def _ship_record_for_case(
    case: dict[str, Any],
    supplied_record: dict[str, Any] | None,
    numeric_spec: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if supplied_record is not None:
        record = copy.deepcopy(supplied_record)
    else:
        try:
            record = make_ship_record(case)
        except (KeyError, TypeError, ValueError):
            return None
    _apply_case_hull(record, case)
    _apply_numeric_spec(record, numeric_spec)
    _refresh_equipment(record)
    return record


def _db_context(record: dict[str, Any]) -> tuple[dict[str, Any], str | None, str | None]:
    dimensions = record.get("dimensions") if isinstance(record.get("dimensions"), dict) else {}
    displacement = record.get("displacement") if isinstance(record.get("displacement"), dict) else {}
    armament = record.get("armament") if isinstance(record.get("armament"), dict) else {}
    country = str(record.get("country") or "")
    ship_type = str(record.get("type") or "")
    db_nation = DB_NATION_BY_COUNTRY.get(country)
    db_ship_type = DB_SHIP_TYPE_BY_SPEC.get(ship_type)
    context = {
        "year": _integer(record.get("year")),
        "nation": db_nation,
        "ship_type": db_ship_type,
        "displacement_t": _number(displacement.get("normal_t")),
        "beam_m": _number(dimensions.get("breadth_m")),
    }
    context["main_gun_caliber_mm"] = _number(armament.get("main_caliber_mm"))
    context["main_gun_count"] = _integer(armament.get("main_barrels"))
    return context, db_nation, db_ship_type


def _issue(code: str, message: str, severity: str, source: str, battery_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "message": message,
        "severity": severity,
        "source": source,
    }
    if battery_id:
        result["battery_id"] = battery_id
    return result


def _normalise_reason_items(items: Any, allowed: set[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return result
    seen: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            code = item.get("code")
            message = item.get("message")
        elif isinstance(item, str):
            code = item
            message = item
        else:
            continue
        if code not in allowed or code in seen:
            continue
        seen.add(code)
        result.append(_issue(str(code), str(message or code), "error", "classic_status_validator"))
    return result


def _stage2_output_for_reason(case: dict[str, Any]) -> dict[str, Any]:
    try:
        from navspec_lora_data_stage2 import make_stage2_output

        value = make_stage2_output(case)
        return value if isinstance(value, dict) else {}
    except (ImportError, KeyError, TypeError, ValueError):
        return {}


def _trace_entry(
    sequence: int,
    phase: str,
    rule: str,
    action: str,
    before: Any,
    after: Any,
    severity: str = "info",
    reason: dict[str, Any] | None = None,
    warning: dict[str, Any] | None = None,
    source: str | list[str] = "tools/navspec_armament_pipeline.py",
) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "phase": phase,
        "rule": rule,
        "action": action,
        "before": before,
        "after": after,
        "reason": reason,
        "warning": warning,
        "severity": severity,
        "source": source,
    }


def _candidate_contract_errors(candidate: Any) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate is not an object"]
    allowed_top = {"schema_version", "main_batteries", "design_rationale", "confidence"}
    errors: list[str] = []
    if candidate.get("schema_version") != ARMAMENT_SCHEMA_VERSION:
        errors.append("schema_version mismatch")
    if set(candidate) - allowed_top:
        errors.append("unknown top-level fields")
    batteries = candidate.get("main_batteries")
    if not isinstance(batteries, list) or not batteries:
        errors.append("main_batteries must be a non-empty array")
        return errors
    required = {
        "battery_id",
        "weapon_id",
        "mount_id",
        "mount_count",
        "barrels_per_mount",
        "total_barrels",
        "positions",
    }
    allowed_battery = required | {"ammo_per_gun", "fire_control_id"}
    for index, battery in enumerate(batteries):
        if not isinstance(battery, dict):
            errors.append(f"battery[{index}] is not an object")
            continue
        missing = sorted(required - set(battery))
        if missing:
            errors.append(f"battery[{index}] missing {','.join(missing)}")
        if set(battery) - allowed_battery:
            errors.append(f"battery[{index}] has unknown fields")
    return errors


def _equipment_summary(record: dict[str, Any]) -> dict[str, Any]:
    equipment = record.get("equipment") if isinstance(record.get("equipment"), dict) else {}
    main = equipment.get("main_battery") if isinstance(equipment.get("main_battery"), dict) else {}
    caliber = _number(main.get("caliber_mm"))
    barrels = _integer(main.get("barrels"))
    return {"main_caliber_mm": caliber, "main_barrels": barrels}


def _record_armament_summary(record: dict[str, Any]) -> dict[str, Any]:
    armament = record.get("armament") if isinstance(record.get("armament"), dict) else {}
    return {
        "main_caliber_mm": _number(armament.get("main_caliber_mm")),
        "main_barrels": _integer(armament.get("main_barrels")),
    }


def _aggregate_summary(aggregate: dict[str, Any]) -> dict[str, Any]:
    return {
        "main_caliber_mm": _number(aggregate.get("main_gun_caliber_mm")),
        "main_barrels": _integer(aggregate.get("main_gun_count")),
    }


def _same_number(left: Any, right: Any, tolerance: float = 1.0) -> bool:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is None or right_number is None:
        return left_number is None and right_number is None
    return abs(left_number - right_number) <= tolerance


def _summary_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _same_number(left.get("main_caliber_mm"), right.get("main_caliber_mm")) and left.get("main_barrels") == right.get("main_barrels")


def _reconcile_ship_record(
    record: dict[str, Any],
    candidate: dict[str, Any],
    aggregate: dict[str, Any],
    db: dict[str, Any],
) -> dict[str, Any]:
    """詳細兵装から統合結果内の既存概要を再計算する。

    これは既存 record や classic output を書き換えず、pipeline result 用の
    deepcopyだけを対象にする。詳細兵装の validator が error の場合は呼ばない。
    """
    result = copy.deepcopy(record)
    armament = result.setdefault("armament", {})
    caliber = _number(aggregate.get("main_gun_caliber_mm"))
    barrels = _integer(aggregate.get("main_gun_count"))
    if caliber is not None:
        armament["main_caliber_mm"] = caliber
    if barrels is not None:
        armament["main_barrels"] = barrels

    mounts: list[dict[str, Any]] = []
    for battery in candidate.get("main_batteries", []):
        weapon = db.get("weapons", {}).get(battery.get("weapon_id"), {})
        mounts.append(
            {
                "weapon_id": battery.get("weapon_id"),
                "mount_id": battery.get("mount_id"),
                "caliber_mm": weapon.get("caliber_mm"),
                "mount_count": battery.get("mount_count"),
                "barrels_per_mount": battery.get("barrels_per_mount"),
                "total_barrels": battery.get("total_barrels"),
            }
        )
    armament["mounts"] = mounts
    _refresh_equipment(result)
    return result


def _provenance(
    db: dict[str, Any] | None,
    data_dir: Path,
    contract_path: Path,
    schema_path: Path,
    model_name: str,
    adapter_name: str,
    seed: int,
) -> dict[str, Any]:
    db_version, db_hash = _db_metadata(db, data_dir)
    contract_version, contract_hash, result_schema_hash = _contract_metadata(contract_path, schema_path)
    return {
        "db_version": db_version,
        "db_hash": db_hash,
        "contract_version": contract_version,
        "contract_hash": contract_hash,
        "result_schema_hash": result_schema_hash,
        "model": model_name,
        "adapter": adapter_name,
        "seed": int(seed),
        "status_validator": "navspec_contradiction.classic.v1",
        "armament_retriever": "navspec_armament_retriever.v1",
        "armament_validator": "navspec_armament_validator.v1",
        "source_files": list(ARMAMENT_SOURCE_FILES),
    }


def _base_result(
    *,
    status: str,
    ship_spec: dict[str, Any] | None,
    detailed_armament: dict[str, Any] | None,
    reason: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    suggested_revision: dict[str, Any] | None,
    design_rationale: str,
    confidence: float,
    validation_trace: list[dict[str, Any]],
    provenance: dict[str, Any],
    fallback: dict[str, Any],
    armament_aggregate: dict[str, Any] | None = None,
    equipment_consistency: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "status": status,
        "ship_spec": ship_spec,
        "detailed_armament": detailed_armament,
        "armament_aggregate": armament_aggregate,
        "reason": reason,
        "warnings": warnings,
        "suggested_revision": suggested_revision,
        "design_rationale": design_rationale,
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "equipment_consistency": equipment_consistency,
        "validation_trace": validation_trace,
        "provenance": provenance,
        "fallback": fallback,
    }


def _fallback(used: bool = False, reason: str | None = None, message: str | None = None) -> dict[str, Any]:
    return {
        "used": used,
        "stage": "armament" if used else None,
        "reason": reason,
        "message": message,
    }


def build_stage2_skip_result(
    request: dict[str, Any],
    *,
    stage2_output: dict[str, Any] | None = None,
    seed: int = 0,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    contract_path: Path | str = DEFAULT_CONTRACT,
    schema_path: Path | str = DEFAULT_RESULT_SCHEMA,
    model_name: str = "classic-rule-engine",
    adapter_name: str = "none",
) -> dict[str, Any]:
    """Stage 2 impossible用の結果を作る（DB/retrieverを呼ばない）。"""
    data_dir = Path(data_dir)
    contract_path = Path(contract_path)
    schema_path = Path(schema_path)
    case, _ = _normalise_request(request)
    try:
        contradiction = check_contradiction(
            ship_type=str(case.get("ship_type") or ""),
            year=_integer(case.get("year")),
            nation=str(case.get("nation") or ""),
            constraints=case.get("constraints"),
            raw_prompt=case.get("request"),
        )
    except (KeyError, TypeError, ValueError):
        contradiction = {"status": "impossible", "reason": ["invalid pipeline input"]}
    if contradiction.get("status") != "impossible":
        raise ValueError("build_stage2_skip_result requires a classic-impossible request")

    derived_stage2 = _stage2_output_for_reason(case)
    selected_stage2 = stage2_output if isinstance(stage2_output, dict) else derived_stage2
    reason = _normalise_reason_items(selected_stage2.get("reason"), STAGE2_REASON_CODES)
    if not reason and derived_stage2:
        reason = _normalise_reason_items(derived_stage2.get("reason"), STAGE2_REASON_CODES)
    if not reason:
        reason = [_issue(
            "displacement_limit_exceeded",
            "classic validatorが要求を成立不能と判定しました",
            "error",
            "classic_status_validator",
        )]

    trace: list[dict[str, Any]] = []
    for item in reason:
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "stage2",
                item["code"],
                "validate",
                {"status": "requested"},
                {"status": "impossible"},
                "error",
                reason=item,
                source="tools/navspec_contradiction.py",
            )
        )
    trace.append(
        _trace_entry(
            len(trace) + 1,
            "pipeline",
            "pipeline.downstream_skip",
            "skip",
            {"stage2_status": "impossible"},
            {"numeric_called": False, "armament_called": False},
            "info",
            source=["tools/navspec_two_stage_pipeline.py", "tools/navspec_armament_pipeline.py"],
        )
    )
    return _base_result(
        status="impossible",
        ship_spec=None,
        detailed_armament=None,
        reason=reason,
        warnings=[],
        suggested_revision=(selected_stage2.get("suggested_revision") or contradiction.get("suggested_revision") or {}),
        design_rationale=str(selected_stage2.get("design_concept") or "要求条件のため下流処理を実行しません。"),
        confidence=_number(selected_stage2.get("confidence")) or 0.94,
        validation_trace=trace,
        provenance=_provenance(
            None,
            data_dir,
            contract_path,
            schema_path,
            model_name,
            adapter_name,
            seed,
        ),
        fallback=_fallback(),
        equipment_consistency={
            "status": "not_applicable",
            "before": {"main_caliber_mm": None, "main_barrels": None},
            "after": {"main_caliber_mm": None, "main_barrels": None},
            "detail": "Stage 2 impossible のため ship_spec / detailed_armament は生成しない",
        },
    )


def run_armament_pipeline(
    request: dict[str, Any],
    *,
    stage2_output: dict[str, Any] | None = None,
    status_output: dict[str, Any] | None = None,
    numeric_spec: dict[str, Any] | None = None,
    armament_candidate: dict[str, Any] | None = None,
    strategy: str = "representative",
    seed: int = 0,
    db: dict[str, Any] | None = None,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    contract_path: Path | str = DEFAULT_CONTRACT,
    schema_path: Path | str = DEFAULT_RESULT_SCHEMA,
    model_name: str = "classic-rule-engine",
    adapter_name: str = "none",
) -> dict[str, Any]:
    """1件の request を pipeline result へ変換する。

    ``stage2_output`` / ``status_output`` は既存の Stage 2 出力を受け取るための
    aliasで、statusの最終権限は常に classic contradiction checker にある。
    ``numeric_spec`` は確定済みの spec だけを受け取り、ここでは推論しない。
    """
    data_dir = Path(data_dir)
    contract_path = Path(contract_path)
    schema_path = Path(schema_path)
    provided_status = stage2_output or status_output
    case, supplied_record = _normalise_request(request)

    try:
        contradiction = check_contradiction(
            ship_type=str(case.get("ship_type") or ""),
            year=_integer(case.get("year")),
            nation=str(case.get("nation") or ""),
            constraints=case.get("constraints"),
            raw_prompt=case.get("request"),
        )
    except (KeyError, TypeError, ValueError):
        contradiction = {"status": "impossible", "reason": ["invalid pipeline input"]}

    classic_status = contradiction.get("status", "ok")
    derived_stage2 = _stage2_output_for_reason(case) if classic_status == "impossible" else {}
    selected_stage2 = provided_status if isinstance(provided_status, dict) else derived_stage2
    if classic_status == "impossible":
        reason = _normalise_reason_items(selected_stage2.get("reason"), STAGE2_REASON_CODES)
        if not reason and derived_stage2:
            reason = _normalise_reason_items(derived_stage2.get("reason"), STAGE2_REASON_CODES)
        if not reason:
            # classic が impossible を返したが旧形式に code が無い場合の既存
            # Stage 2 fallback。自由文を code として採用しない。
            reason = [_issue(
                "displacement_limit_exceeded",
                "classic validatorが要求を成立不能と判定しました",
                "error",
                "classic_status_validator",
            )]

        provenance = _provenance(
            None,
            data_dir,
            contract_path,
            schema_path,
            model_name,
            adapter_name,
            seed,
        )
        trace: list[dict[str, Any]] = []
        for item in reason:
            trace.append(
                _trace_entry(
                    len(trace) + 1,
                    "stage2",
                    item["code"],
                    "validate",
                    {"status": "requested"},
                    {"status": "impossible"},
                    "error",
                    reason=item,
                    source="tools/navspec_contradiction.py",
                )
            )
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "pipeline",
                "pipeline.downstream_skip",
                "skip",
                {"stage2_status": "impossible"},
                {"numeric_called": False, "armament_called": False},
                "info",
                source=["tools/navspec_two_stage_pipeline.py", "tools/navspec_armament_pipeline.py"],
            )
        )
        return _base_result(
            status="impossible",
            ship_spec=None,
            detailed_armament=None,
            reason=reason,
            warnings=[],
            suggested_revision=(selected_stage2.get("suggested_revision") or contradiction.get("suggested_revision") or {}),
            design_rationale=str(selected_stage2.get("design_concept") or "要求条件のため下流処理を実行しません。"),
            confidence=_number(selected_stage2.get("confidence")) or 0.94,
            validation_trace=trace,
            provenance=provenance,
            fallback=_fallback(),
            equipment_consistency={
                "status": "not_applicable",
                "before": {"main_caliber_mm": None, "main_barrels": None},
                "after": {"main_caliber_mm": None, "main_barrels": None},
                "detail": "Stage 2 impossible のため ship_spec / detailed_armament は生成しない",
            },
        )

    record = _ship_record_for_case(case, supplied_record, numeric_spec)
    provenance = _provenance(
        db,
        data_dir,
        contract_path,
        schema_path,
        model_name,
        adapter_name,
        seed,
    )
    trace = [
        _trace_entry(
            1,
            "stage2",
            "stage2.classic_status",
            "validate",
            {"status": "requested"},
            {"status": "ok"},
            "info",
            source="tools/navspec_contradiction.py",
        )
    ]
    if numeric_spec is not None:
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "numeric",
                "numeric.final_spec",
                "adopt",
                {"spec_provided": True},
                {"ship_spec_updated": record is not None},
                "info",
                source="tools/navspec_two_stage_pipeline.py",
            )
        )
    else:
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "numeric",
                "numeric.final_spec",
                "skip",
                {"spec_provided": False},
                {"ship_spec_updated": False},
                "info",
                source="tools/navspec_two_stage_pipeline.py",
            )
        )

    if record is None:
        fallback = _fallback(True, "schema_mismatch", "ShipSpecRecordを構築できない入力です")
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "armament",
                "pipeline.invalid_context",
                "fallback",
                {"ship_spec": None},
                {"fallback": fallback},
                "warn",
                warning=None,
                source="tools/navspec_armament_pipeline.py",
            )
        )
        return _base_result(
            status="ok",
            ship_spec=None,
            detailed_armament=None,
            reason=[],
            warnings=[],
            suggested_revision={},
            design_rationale="",
            confidence=0.0,
            validation_trace=trace,
            provenance=provenance,
            fallback=fallback,
            equipment_consistency={
                "status": "not_evaluated",
                "before": {"main_caliber_mm": None, "main_barrels": None},
                "after": {"main_caliber_mm": None, "main_barrels": None},
                "detail": "ShipSpecRecord構築失敗",
            },
        )

    context, db_nation, db_ship_type = _db_context(record)
    if db_nation is None:
        fallback = _fallback(True, "unsupported_nation", f"armament DBに国家 {record.get('country')!r} がありません")
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "armament",
                "armament.context_mapping",
                "fallback",
                {"country": record.get("country")},
                {"db_nation": None},
                "warn",
                source="tools/navspec_armament_pipeline.py",
            )
        )
        return _base_result(
            status="ok",
            ship_spec=record,
            detailed_armament=None,
            reason=[],
            warnings=[],
            suggested_revision={},
            design_rationale="詳細兵装DBの対象外国家のため、船体仕様だけを返しました。",
            confidence=0.75,
            validation_trace=trace,
            provenance=provenance,
            fallback=fallback,
            equipment_consistency={
                "status": "not_applicable",
                "before": _equipment_summary(record),
                "after": _equipment_summary(record),
                "detail": "国家写像なし",
            },
        )
    if db_ship_type is None:
        fallback = _fallback(True, "unsupported_ship_type", f"armament DBに艦種 {record.get('type')!r} の写像がありません")
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "armament",
                "armament.context_mapping",
                "fallback",
                {"ship_type": record.get("type")},
                {"db_ship_type": None},
                "warn",
                source="tools/navspec_armament_pipeline.py",
            )
        )
        return _base_result(
            status="ok",
            ship_spec=record,
            detailed_armament=None,
            reason=[],
            warnings=[],
            suggested_revision={},
            design_rationale="詳細兵装DBの対象外艦種のため、船体仕様だけを返しました。",
            confidence=0.75,
            validation_trace=trace,
            provenance=provenance,
            fallback=fallback,
            equipment_consistency={
                "status": "not_applicable",
                "before": _equipment_summary(record),
                "after": _equipment_summary(record),
                "detail": "艦種写像なし",
            },
        )

    if db is None:
        try:
            db = load_db(data_dir)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            db = None
            fallback = _fallback(True, "db_load_failure", f"armament DB読込失敗: {exc}")
            trace.append(
                _trace_entry(
                    len(trace) + 1,
                    "armament",
                    "armament.db_load",
                    "fallback",
                    {"data_dir": str(data_dir)},
                    {"db_loaded": False},
                    "warn",
                    source="tools/navspec_armament_validator.py",
                )
            )
            return _base_result(
                status="ok",
                ship_spec=record,
                detailed_armament=None,
                reason=[],
                warnings=[],
                suggested_revision={},
                design_rationale="兵装DBを読めないため、船体仕様だけを返しました。",
                confidence=0.70,
                validation_trace=trace,
                provenance=_provenance(None, data_dir, contract_path, schema_path, model_name, adapter_name, seed),
                fallback=fallback,
                equipment_consistency={
                    "status": "not_applicable",
                    "before": _equipment_summary(record),
                    "after": _equipment_summary(record),
                    "detail": "DB読込失敗",
                },
            )

    if not isinstance(db, dict) or not db.get("weapons") or not db.get("mounts"):
        fallback = _fallback(True, "db_load_failure", "armament DBに必須のweapon/mountデータがありません")
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "armament",
                "armament.db_load",
                "fallback",
                {"db_loaded": bool(db)},
                {"db_ready": False},
                "warn",
                source="tools/navspec_armament_validator.py",
            )
        )
        return _base_result(
            status="ok",
            ship_spec=record,
            detailed_armament=None,
            reason=[],
            warnings=[],
            suggested_revision={},
            design_rationale="兵装DBに必須データがないため、船体仕様だけを返しました。",
            confidence=0.70,
            validation_trace=trace,
            provenance=_provenance(None, data_dir, contract_path, schema_path, model_name, adapter_name, seed),
            fallback=fallback,
            equipment_consistency={
                "status": "not_applicable",
                "before": _equipment_summary(record),
                "after": _equipment_summary(record),
                "detail": "DB必須データ不足",
            },
        )

    rng = random.Random(seed)
    available_weapons = retrieve_available_weapons(context, db)
    candidate = copy.deepcopy(armament_candidate) if armament_candidate is not None else retrieve_armament(
        context,
        db,
        strategy=strategy,
        rng=rng,
    )
    selected_weapon_id = None
    selected_mount_id = None
    selected_fc_id = None
    if isinstance(candidate, dict) and isinstance(candidate.get("main_batteries"), list) and candidate["main_batteries"]:
        first_battery = candidate["main_batteries"][0]
        if isinstance(first_battery, dict):
            selected_weapon_id = first_battery.get("weapon_id")
            selected_mount_id = first_battery.get("mount_id")
            selected_fc_id = first_battery.get("fire_control_id")
    available_mounts = retrieve_available_mounts(selected_weapon_id, context, db) if selected_weapon_id else []
    trace.append(
        _trace_entry(
            len(trace) + 1,
            "armament",
            "armament.retriever.selection",
            "select" if candidate else "fallback",
            {
                "strategy": strategy,
                "available_weapon_ids": sorted(w.get("weapon_id") for w in available_weapons if w.get("weapon_id")),
                "available_mount_ids": sorted(m.get("mount_id") for m in available_mounts if m.get("mount_id")),
            },
            {
                "selected_weapon_id": selected_weapon_id,
                "selected_mount_id": selected_mount_id,
                "selected_fire_control_id": selected_fc_id,
                "excluded_weapon_ids": sorted(
                    w.get("weapon_id") for w in available_weapons
                    if w.get("weapon_id") and w.get("weapon_id") != selected_weapon_id
                ),
                "excluded_mount_ids": sorted(
                    m.get("mount_id") for m in available_mounts
                    if m.get("mount_id") and m.get("mount_id") != selected_mount_id
                ),
            },
            "info" if candidate else "warn",
            source=[
                "tools/navspec_armament_retriever.py",
                "data/navspec/armament/weapons.jsonl",
                "data/navspec/armament/mounts.jsonl",
                "data/navspec/armament/fire_control.jsonl",
            ],
        )
    )

    if candidate is None:
        fallback = _fallback(True, "no_candidate", "指定された国家・年代・艦種・船体条件から候補を生成できません")
        return _base_result(
            status="ok",
            ship_spec=record,
            detailed_armament=None,
            reason=[],
            warnings=[],
            suggested_revision={},
            design_rationale="詳細兵装候補なし。船体仕様とfallback理由を返しました。",
            confidence=0.72,
            validation_trace=trace,
            provenance=provenance,
            fallback=fallback,
            equipment_consistency={
                "status": "not_applicable",
                "before": _equipment_summary(record),
                "after": _equipment_summary(record),
                "detail": "詳細兵装候補なし",
            },
        )

    contract_errors = _candidate_contract_errors(candidate)
    if contract_errors:
        fallback = _fallback(True, "schema_mismatch", "; ".join(contract_errors))
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "armament",
                "armament.candidate_schema",
                "fallback",
                {"candidate_schema_version": candidate.get("schema_version") if isinstance(candidate, dict) else None},
                {"schema_valid": False},
                "warn",
                source="schemas/navspec_armament_candidate.schema.json",
            )
        )
        return _base_result(
            status="ok",
            ship_spec=record,
            detailed_armament=None,
            reason=[],
            warnings=[],
            suggested_revision={},
            design_rationale="詳細兵装候補の契約不一致。船体仕様とfallback理由を返しました。",
            confidence=0.65,
            validation_trace=trace,
            provenance=provenance,
            fallback=fallback,
            equipment_consistency={
                "status": "not_evaluated",
                "before": _equipment_summary(record),
                "after": _equipment_summary(record),
                "detail": "armament_candidate schema不一致",
            },
        )

    verdict = validate_armament(candidate, context, db)
    raw_issues = verdict.get("issues") if isinstance(verdict, dict) else []
    issues: list[dict[str, Any]] = []
    for raw_issue in raw_issues if isinstance(raw_issues, list) else []:
        if not isinstance(raw_issue, dict):
            continue
        code = raw_issue.get("code")
        severity = raw_issue.get("severity")
        if code not in ARMAMENT_REASON_CODE_SET or severity not in {"error", "warn"}:
            # validatorの未知値を理由として採用しない。候補自体は保持するが、
            # contract上のreason/warningへは漏らさない。
            continue
        issues.append(
            _issue(
                str(code),
                str(raw_issue.get("message") or code),
                str(severity),
                "tools/navspec_armament_validator.py",
                raw_issue.get("battery_id"),
            )
        )

    reason = [item for item in issues if item["severity"] == "error"]
    warnings = [item for item in issues if item["severity"] == "warn"]
    for item in issues:
        trace.append(
            _trace_entry(
                len(trace) + 1,
                "armament",
                item["code"],
                "validate",
                {"candidate": "selected"},
                {"severity": item["severity"]},
                item["severity"],
                reason=item if item["severity"] == "error" else None,
                warning=item if item["severity"] == "warn" else None,
                source="tools/navspec_armament_validator.py",
            )
        )

    aggregate = verdict.get("aggregate") if isinstance(verdict, dict) else {}
    if not isinstance(aggregate, dict) or not aggregate:
        aggregate = aggregate_main_caliber_with_db(candidate, db)
    aggregate = {
        "main_gun_caliber_mm": aggregate.get("main_gun_caliber_mm"),
        "main_gun_count": _integer(aggregate.get("main_gun_count")) or 0,
        "main_gun_mount_count": _integer(aggregate.get("main_gun_mount_count")) or 0,
    }
    detail_summary = _aggregate_summary(aggregate)
    before_equipment = _equipment_summary(record)
    before_armament = _record_armament_summary(record)
    equipment_consistency = {
        "status": "not_evaluated",
        "before": before_equipment,
        "after": before_equipment,
        "detail": "armament validator errorのため概要再計算を行わない",
    }
    final_record = record
    if not reason:
        if _summary_matches(before_equipment, detail_summary) and _summary_matches(before_armament, detail_summary):
            equipment_consistency = {
                "status": "consistent",
                "before": before_equipment,
                "after": before_equipment,
                "detail": "tools/navspec_equipment.pyの概要と詳細兵装の口径・門数が一致",
            }
        else:
            final_record = _reconcile_ship_record(record, candidate, aggregate, db)
            after_equipment = _equipment_summary(final_record)
            after_armament = _record_armament_summary(final_record)
            reconciled = _summary_matches(after_equipment, detail_summary) and _summary_matches(after_armament, detail_summary)
            equipment_consistency = {
                "status": "reconciled" if reconciled else "mismatch",
                "before": before_equipment,
                "after": after_equipment,
                "detail": (
                    "詳細兵装から統合結果内の主砲概要を再計算"
                    if reconciled
                    else "詳細兵装と主砲概要の再計算後も不一致"
                ),
            }
            trace.append(
                _trace_entry(
                    len(trace) + 1,
                    "pipeline",
                    "armament.equipment_consistency",
                    "reconcile",
                    {
                        "equipment": before_equipment,
                        "record_armament": before_armament,
                        "detailed_armament": detail_summary,
                    },
                    {
                        "equipment": after_equipment,
                        "record_armament": after_armament,
                        "consistent": reconciled,
                    },
                    "info" if reconciled else "warn",
                    source=["tools/navspec_equipment.py", "tools/navspec_armament_validator.py"],
                )
            )

    status = "impossible" if reason else "ok"
    trace.append(
        _trace_entry(
            len(trace) + 1,
            "pipeline",
            "pipeline.final_status",
            "finalize",
            {"stage2_status": "ok", "armament_validator_status": verdict.get("status") if isinstance(verdict, dict) else None},
            {"status": status},
            "error" if status == "impossible" else ("warn" if warnings else "info"),
            source="tools/navspec_armament_pipeline.py",
        )
    )
    suggested_revision: dict[str, Any] = {}
    if reason:
        suggested_revision = {
            "reason_codes": [item["code"] for item in reason],
            "note": "詳細兵装のerrorを解消するまで候補を採用しない",
        }
    design_rationale = str(candidate.get("design_rationale") or "")
    confidence = _number(candidate.get("confidence")) or 0.0
    if reason:
        confidence *= 0.5
    elif warnings:
        confidence *= 0.9
    return _base_result(
        status=status,
        ship_spec=final_record,
        detailed_armament=candidate,
        reason=reason,
        warnings=warnings,
        suggested_revision=suggested_revision,
        design_rationale=design_rationale,
        confidence=confidence,
        validation_trace=trace,
        provenance=provenance,
        fallback=_fallback(),
        armament_aggregate=aggregate,
        equipment_consistency=equipment_consistency,
    )


def build_pipeline_result(request: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """互換用の明示的な別名。"""
    return run_armament_pipeline(request, **kwargs)


class ArmamentPipeline:
    """同じDB/contract設定で複数requestを処理する薄いfacade。"""

    def __init__(self, **kwargs: Any) -> None:
        self.options = dict(kwargs)

    def run(self, request: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        options = {**self.options, **kwargs}
        return run_armament_pipeline(request, **options)


def validate_pipeline_result(result: dict[str, Any], schema_path: Path | str = DEFAULT_RESULT_SCHEMA) -> list[str]:
    """pipeline resultの参照schemaを含む最小契約検証を返す。

    プロジェクトの軽量 ``validate_schema`` は外部 ``$ref`` を解決しないため、
    参照先の2 schemaだけここで明示的に検証する。
    """
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["$: result must be an object"]
    if result.get("schema_version") != PIPELINE_SCHEMA_VERSION:
        errors.append("$.schema_version: invalid pipeline result version")
    if result.get("status") not in {"ok", "impossible"}:
        errors.append("$.status: invalid status")
    for field in ("ship_spec", "detailed_armament", "reason", "warnings", "validation_trace", "provenance", "fallback"):
        if field not in result:
            errors.append(f"$: missing required field {field!r}")

    if result.get("ship_spec") is not None:
        try:
            from validate_navspec_output import validate_schema

            ship_schema = _load_json(ROOT / "schemas" / "ship_spec_record.schema.json")
            errors.extend(validate_schema(result["ship_spec"], ship_schema, "$.ship_spec"))
        except ImportError:
            errors.append("$.ship_spec: validator unavailable")
    if result.get("detailed_armament") is not None:
        errors.extend(_candidate_contract_errors(result["detailed_armament"])[0:])

    for field in ("reason", "warnings"):
        values = result.get(field)
        if not isinstance(values, list):
            errors.append(f"$.{field}: must be an array")
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                errors.append(f"$.{field}[{index}]: must be an object")
                continue
            code = item.get("code")
            if code not in KNOWN_REASON_CODES:
                errors.append(f"$.{field}[{index}]: unknown code {code!r}")
            expected_severity = "error" if field == "reason" else "warn"
            if item.get("severity") != expected_severity:
                errors.append(f"$.{field}[{index}]: severity must be {expected_severity!r}")
            if not isinstance(item.get("source"), str) or not item["source"]:
                errors.append(f"$.{field}[{index}]: source is required")

    trace = result.get("validation_trace")
    if not isinstance(trace, list) or not trace:
        errors.append("$.validation_trace: must be a non-empty array")
    else:
        for index, item in enumerate(trace):
            if not isinstance(item, dict):
                errors.append(f"$.validation_trace[{index}]: must be an object")
                continue
            for field in ("sequence", "phase", "rule", "action", "before", "after", "reason", "warning", "severity", "source"):
                if field not in item:
                    errors.append(f"$.validation_trace[{index}]: missing {field!r}")
            if index and item.get("sequence") != trace[index - 1].get("sequence", 0) + 1:
                errors.append(f"$.validation_trace[{index}]: sequence is not contiguous")
            trace_reason = item.get("reason")
            if trace_reason is not None:
                if not isinstance(trace_reason, dict) or trace_reason.get("code") not in KNOWN_REASON_CODES:
                    errors.append(f"$.validation_trace[{index}]: unknown reason code")
            trace_warning = item.get("warning")
            if trace_warning is not None:
                if not isinstance(trace_warning, dict) or trace_warning.get("code") not in KNOWN_REASON_CODES:
                    errors.append(f"$.validation_trace[{index}]: unknown warning code")

    provenance = result.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("$.provenance: must be an object")
    else:
        for field in ("db_version", "db_hash", "contract_version", "contract_hash", "model", "adapter", "seed"):
            if field not in provenance:
                errors.append(f"$.provenance: missing {field!r}")
        for field in ("db_hash", "contract_hash"):
            if not isinstance(provenance.get(field), str) or len(provenance[field]) != 64:
                errors.append(f"$.provenance.{field}: expected sha256 hex")

    fallback = result.get("fallback")
    if not isinstance(fallback, dict):
        errors.append("$.fallback: must be an object")
    else:
        for field in ("used", "stage", "reason", "message"):
            if field not in fallback:
                errors.append(f"$.fallback: missing {field!r}")
    return errors


def _parse_constraints(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    path = Path(value)
    raw = path.read_text(encoding="utf-8") if path.exists() else value
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("constraints must be a JSON object")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build navspec.pipeline_result.v1 with detailed armament")
    parser.add_argument("--nation", "--country", dest="nation", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--ship-type", required=True)
    parser.add_argument("--role", default="fleet_battle")
    parser.add_argument("--displacement-t", type=float)
    parser.add_argument("--beam-m", type=float)
    parser.add_argument("--constraints-json")
    parser.add_argument("--strategy", choices=["representative", "mid_caliber", "random"], default="representative")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_RESULT_SCHEMA)
    parser.add_argument("--model", default="classic-rule-engine")
    parser.add_argument("--adapter", default="none")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)

    case = {
        "id": "cli_pipeline_case",
        "nation": args.nation,
        "year": args.year,
        "ship_type": args.ship_type,
        "role": args.role,
        "constraints": _parse_constraints(args.constraints_json),
        "request": "",
        "displacement_t": args.displacement_t,
        "beam_m": args.beam_m,
    }
    result = run_armament_pipeline(
        case,
        strategy=args.strategy,
        seed=args.seed,
        data_dir=args.data_dir,
        contract_path=args.contract,
        schema_path=args.schema,
        model_name=args.model,
        adapter_name=args.adapter,
    )
    errors = validate_pipeline_result(result, args.schema)
    if errors:
        print(json.dumps({"status": "contract_error", "errors": errors}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
