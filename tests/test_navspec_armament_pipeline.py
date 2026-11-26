from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.navspec_armament_pipeline import (
    run_armament_pipeline,
    validate_pipeline_result,
)
from tools.navspec_armament_retriever import build_candidate
from tools.navspec_armament_validator import load_db


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "navspec" / "armament"


@pytest.fixture()
def db() -> dict:
    return load_db(DATA_DIR)


def _candidate_for(db: dict, weapon_nation: str = "JP", caliber: int = 356, *, positions=None) -> dict:
    weapon = next(
        item
        for item in db["weapons"].values()
        if item.get("nation") == weapon_nation and item.get("caliber_mm") == caliber
    )
    mount = next(item for item in db["mounts"].values() if item.get("weapon_id") == weapon["weapon_id"])
    mount_count = 4 if mount.get("barrels_per_mount") == 2 else 3
    return build_candidate(
        weapon,
        mount,
        mount_count,
        positions or ["A", "B", "X", "Y"][:mount_count],
        None,
    )


def _normal_case(**overrides):
    case = {
        "id": "pipeline_normal",
        "nation": "Japan",
        "year": 1941,
        "ship_type": "battleship",
        "role": "fleet_battle",
        "constraints": {},
        "displacement_t": 30000,
        "beam_m": 33,
        "request": "",
    }
    case.update(overrides)
    return case


def test_normal_pipeline_returns_contract_and_reconciles_summary(db):
    result = run_armament_pipeline(_normal_case(), db=db, seed=11)

    assert result["schema_version"] == "navspec.pipeline_result.v1"
    assert result["status"] == "ok"
    assert result["detailed_armament"] is not None
    assert result["armament_aggregate"]["main_gun_count"] > 0
    assert result["equipment_consistency"]["status"] in {"consistent", "reconciled"}
    assert validate_pipeline_result(result) == []


@pytest.mark.parametrize(
    ("ship_type", "displacement_t", "beam_m"),
    [
        ("heavy_cruiser", 12000, 20),
        ("destroyer", 2000, 10.5),
    ],
)
def test_normal_cruiser_and_destroyer_pipeline(ship_type, displacement_t, beam_m, db):
    result = run_armament_pipeline(
        _normal_case(
            id=f"pipeline_{ship_type}",
            ship_type=ship_type,
            displacement_t=displacement_t,
            beam_m=beam_m,
        ),
        db=db,
        seed=5,
    )

    assert result["status"] == "ok"
    assert result["detailed_armament"] is not None
    assert result["armament_aggregate"]["main_gun_count"] > 0
    assert validate_pipeline_result(result) == []


def test_warning_only_keeps_status_ok_and_records_warning(db):
    candidate = _candidate_for(db, caliber=356)
    result = run_armament_pipeline(
        _normal_case(beam_m=1),
        db=db,
        armament_candidate=candidate,
    )

    assert result["status"] == "ok"
    assert result["reason"] == []
    assert any(item["code"] == "armament.barbette_too_large_for_beam" for item in result["warnings"])
    assert validate_pipeline_result(result) == []


def test_armament_error_is_impossible_but_candidate_is_retained(db):
    candidate = _candidate_for(db, caliber=356)
    candidate["main_batteries"][0]["mount_id"] = "not-a-real-mount"
    result = run_armament_pipeline(_normal_case(), db=db, armament_candidate=candidate)

    assert result["status"] == "impossible"
    assert result["detailed_armament"] == candidate
    assert any(item["code"] == "armament.weapon_mount_incompatible" for item in result["reason"])
    assert result["equipment_consistency"]["status"] == "not_evaluated"
    assert validate_pipeline_result(result) == []


def test_stage2_impossible_skips_downstream_and_has_only_known_codes(monkeypatch):
    case = _normal_case(
        nation="United States",
        year=1944,
        ship_type="destroyer",
        constraints={
            "main_gun_caliber_mm_min": 203,
            "main_gun_barrels_min": 6,
            "speed_kt_min": 40,
            "belt_armor_mm_min": 150,
            "range_nm_min": 10000,
        },
    )
    called = {"load_db": 0}

    def fail_if_loaded(*args, **kwargs):
        called["load_db"] += 1
        raise AssertionError("Stage 2 impossible must skip armament DB load")

    monkeypatch.setattr("tools.navspec_armament_pipeline.load_db", fail_if_loaded)
    result = run_armament_pipeline(case)

    assert result["status"] == "impossible"
    assert result["ship_spec"] is None
    assert result["detailed_armament"] is None
    assert result["fallback"]["used"] is False
    assert called["load_db"] == 0
    assert all(item["code"] in {
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
    } for item in result["reason"])
    assert validate_pipeline_result(result) == []


def test_unsupported_nation_and_db_failure_are_explicit_fallbacks(tmp_path, db):
    unsupported = run_armament_pipeline(_normal_case(nation="Argentina"), db=db)
    assert unsupported["status"] == "ok"
    assert unsupported["fallback"] == {
        "used": True,
        "stage": "armament",
        "reason": "unsupported_nation",
        "message": "armament DBに国家 'Argentina' がありません",
    }
    assert validate_pipeline_result(unsupported) == []

    db_failure = run_armament_pipeline(_normal_case(), data_dir=tmp_path)
    assert db_failure["fallback"]["used"] is True
    assert db_failure["fallback"]["reason"] == "db_load_failure"
    assert validate_pipeline_result(db_failure) == []


def test_candidate_schema_mismatch_is_not_emitted_as_unknown_code(db):
    bad_candidate = copy.deepcopy(_candidate_for(db))
    bad_candidate["unexpected"] = True
    result = run_armament_pipeline(_normal_case(), db=db, armament_candidate=bad_candidate)

    assert result["status"] == "ok"
    assert result["detailed_armament"] is None
    assert result["fallback"]["reason"] == "schema_mismatch"
    assert result["reason"] == []
    assert result["warnings"] == []
    assert validate_pipeline_result(result) == []


def test_two_stage_assemble_armament_is_opt_in_and_preserves_legacy_output():
    from tools.navspec_two_stage_pipeline import assemble_pipeline

    case = _normal_case(id="assemble_normal")
    rows = [{**case, "expected_status": "ok", "expected_output": {}}]
    stage2_outputs = [{"id": case["id"], "parsed": {"status": "ok", "design_concept": "stage2"}}]
    numeric_outputs = {case["id"]: {"parsed": {"spec": {}}, "raw_text": ""}}
    from tools.navspec_two_stage_pipeline import DEFAULT_CONTRACT

    contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))

    results, extra = assemble_pipeline(
        rows,
        stage2_outputs,
        numeric_outputs,
        contract,
        "generator",
        "status-model",
        "validator",
        armament_enabled=True,
        armament_seed=3,
    )

    assert results[0]["final_json"]["schema_version"] == "navspec.lora_candidate.v2"
    assert results[0]["pipeline_result"]["schema_version"] == "navspec.pipeline_result.v1"
    assert results[0]["detailed_armament"] == results[0]["pipeline_result"]["detailed_armament"]
    assert extra["armament_enabled"] is True
    assert extra["armament_count"] == 1


def test_two_stage_impossible_does_not_call_armament(monkeypatch):
    from tools.navspec_two_stage_pipeline import assemble_pipeline
    import navspec_armament_pipeline

    case = _normal_case(
        id="assemble_impossible",
        nation="United States",
        year=1944,
        ship_type="destroyer",
        constraints={"main_gun_caliber_mm_min": 203, "main_gun_barrels_min": 6},
    )
    rows = [{**case, "expected_status": "impossible", "expected_output": {}}]
    stage2_outputs = [{"id": case["id"], "parsed": {"status": "impossible", "reason": []}}]

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Stage 2 impossible must not call armament runner")

    monkeypatch.setattr(navspec_armament_pipeline, "run_armament_pipeline", fail_if_called)

    results, _ = assemble_pipeline(
        rows,
        stage2_outputs,
        {},
        {"categories": {"eras": []}, "constraint_fields": {}},
        "generator",
        "status-model",
        "validator",
        armament_enabled=True,
    )

    assert results[0]["numeric_called"] is False
    assert results[0]["pipeline_result"]["detailed_armament"] is None
    assert results[0]["pipeline_result"]["fallback"]["used"] is False
