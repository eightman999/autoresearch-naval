"""Stage 4 Commit B: armament validator のテスト (progress §23 全10件).

テスト対象: tools/navspec_armament_validator.py
DB seed: data/navspec/armament/ (Commit A で生成)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# tools/ を import path に追加
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from navspec_armament_validator import (  # noqa: E402
    ARMAMENT_REASON_CODES,
    aggregate_main_gun_fields,
    aggregate_main_caliber_with_db,
    load_db,
    validate_armament,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "navspec" / "armament"


# ----------------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db():
    return load_db(DATA_DIR)


@pytest.fixture
def yamato_candidate():
    """大和型 460mm×3×3 = 9門。正常系の代表例。"""
    return {
        "schema_version": "navspec.armament_candidate.v1",
        "main_batteries": [
            {
                "battery_id": "main_1",
                "weapon_id": "JP_460MM_94SHIKI",
                "mount_id": "JP_460MM_TRIPLE_94SHIKI",
                "mount_count": 3,
                "barrels_per_mount": 3,
                "total_barrels": 9,
                "positions": ["A", "B", "X"],
                "ammo_per_gun": 100,
                "fire_control_id": "JP_TYPE94_DIRECTOR",
            }
        ],
        "design_rationale": "3 triple turrets superfiring fore/aft",
        "confidence": 0.9,
    }


@pytest.fixture
def yamato_context():
    return {
        "ship_type": "battleship",
        "year": 1941,
        "nation": "JP",
        "displacement_t": 65000,
        "beam_m": 38.0,
    }


def _codes(result):
    return [i["code"] for i in result["issues"]]


# ----------------------------------------------------------------------------
# 1. test_weapon_schema
# ----------------------------------------------------------------------------

def test_weapon_schema(db):
    """weapons.jsonl の全レコードが必須フィールドを持つこと。"""
    required = ["weapon_id", "name", "nation", "caliber_mm",
                "barrel_length_calibers", "gun_weight_t", "shell_weight_kg",
                "muzzle_velocity_mps", "rate_of_fire_rpm", "max_range_m",
                "loading_type", "source"]
    assert len(db["weapons"]) >= 30, "weapons.jsonl に30件以上あること"
    for wid, w in db["weapons"].items():
        for f in required:
            assert w.get(f) is not None, f"weapon {wid} missing {f}"
        assert w["nation"] in {
            "JP", "US", "UK", "DE", "FR", "IT", "RU", "AH", "ES", "SE", "NL",
        }
        assert 50 <= w["caliber_mm"] <= 510


# ----------------------------------------------------------------------------
# 2. test_mount_schema
# ----------------------------------------------------------------------------

def test_mount_schema(db):
    """mounts.jsonl の全レコードが必須フィールドを持つこと。"""
    required = ["mount_id", "weapon_id", "nation", "barrels_per_mount",
                "mount_type", "turret_weight_t", "source"]
    assert len(db["mounts"]) >= 30
    valid_types = {"single", "twin", "triple", "quadruple"}
    type_to_barrels = {"single": 1, "twin": 2, "triple": 3, "quadruple": 4}
    for mid, m in db["mounts"].items():
        for f in required:
            assert m.get(f) is not None, f"mount {mid} missing {f}"
        assert m["mount_type"] in valid_types
        assert m["barrels_per_mount"] == type_to_barrels[m["mount_type"]]
        # weapon_id が weapons.jsonl に存在
        assert m["weapon_id"] in db["weapons"], f"mount {mid} weapon_id {m['weapon_id']} dangling"


# ----------------------------------------------------------------------------
# 3. test_weapon_mount_compatibility
# ----------------------------------------------------------------------------

def test_weapon_mount_compatibility_ok(db, yamato_candidate, yamato_context):
    """正常な weapon/mount 組は weapon_mount_incompatible を出さない。"""
    result = validate_armament(yamato_candidate, yamato_context, db)
    assert "armament.weapon_mount_incompatible" not in _codes(result)


def test_weapon_mount_compatibility_incompatible(db, yamato_context):
    """weapon と無関係な mount_id を与えると incompatible エラー。"""
    candidate = {
        "schema_version": "navspec.armament_candidate.v1",
        "main_batteries": [{
            "battery_id": "main_1",
            "weapon_id": "JP_460MM_94SHIKI",
            "mount_id": "US_16INCH_TRIPLE_MK7",  # 異なる weapon の mount
            "mount_count": 3, "barrels_per_mount": 3, "total_barrels": 9,
            "positions": ["A", "B", "X"],
        }],
    }
    result = validate_armament(candidate, yamato_context, db)
    assert "armament.weapon_mount_incompatible" in _codes(result)
    assert result["status"] == "impossible"


# ----------------------------------------------------------------------------
# 4. test_barbette_beam_rule
# ----------------------------------------------------------------------------

def test_barbette_beam_rule_warning(db):
    """beam が狭すぎると barbette_too_large_for_beam 警告。"""
    candidate = {
        "schema_version": "navspec.armament_candidate.v1",
        "main_batteries": [{
            "battery_id": "main_1",
            "weapon_id": "JP_460MM_94SHIKI",
            "mount_id": "JP_460MM_TRIPLE_94SHIKI",
            "mount_count": 3, "barrels_per_mount": 3, "total_barrels": 9,
            "positions": ["A", "B", "X"],  # wing 配置
        }],
    }
    narrow_context = {
        "ship_type": "battleship", "year": 1941, "nation": "JP",
        "displacement_t": 65000, "beam_m": 8.0,  # 460mm 砲塔に狭すぎる
    }
    result = validate_armament(candidate, narrow_context, db)
    assert "armament.barbette_too_large_for_beam" in _codes(result)


# ----------------------------------------------------------------------------
# 5. test_turret_weight_rule
# ----------------------------------------------------------------------------

def test_turret_weight_rule_exceeds(db):
    """単砲塔重量が大きすぎると turret_weight_exceeds_hull_limit エラー。"""
    candidate = {
        "schema_version": "navspec.armament_candidate.v1",
        "main_batteries": [{
            "battery_id": "main_1",
            "weapon_id": "JP_460MM_94SHIKI",
            "mount_id": "JP_460MM_TRIPLE_94SHIKI",
            "mount_count": 1, "barrels_per_mount": 3, "total_barrels": 3,
            "positions": ["A"],
        }],
    }
    # 排水量 5000t に 460mm triple (2730t) は明らかに過大
    tiny_context = {
        "ship_type": "destroyer", "year": 1941, "nation": "JP",
        "displacement_t": 5000, "beam_m": 12.0,
    }
    result = validate_armament(candidate, tiny_context, db)
    assert "armament.turret_weight_exceeds_hull_limit" in _codes(result)


# ----------------------------------------------------------------------------
# 6. test_magazine_volume_rule
# ----------------------------------------------------------------------------

def test_magazine_volume_rule_warning(db):
    """ammo_per_gun が異常に大きいと magazine_volume_insufficient 警告。"""
    candidate = {
        "schema_version": "navspec.armament_candidate.v1",
        "main_batteries": [{
            "battery_id": "main_1",
            "weapon_id": "JP_460MM_94SHIKI",
            "mount_id": "JP_460MM_TRIPLE_94SHIKI",
            "mount_count": 3, "barrels_per_mount": 3, "total_barrels": 9,
            "positions": ["A", "B", "X"],
            "ammo_per_gun": 10000,  # 1万発/砲 = 異常
        }],
    }
    context = {
        "ship_type": "battleship", "year": 1941, "nation": "JP",
        "displacement_t": 65000, "beam_m": 38.0,
    }
    result = validate_armament(candidate, context, db)
    assert "armament.magazine_volume_insufficient" in _codes(result)


# ----------------------------------------------------------------------------
# 7. test_position_validation
# ----------------------------------------------------------------------------

def test_position_validation_ok(db, yamato_candidate, yamato_context):
    """正常な positions は firing_arc_conflict を出さない。"""
    result = validate_armament(yamato_candidate, yamato_context, db)
    codes = _codes(result)
    assert "armament.firing_arc_conflict" not in codes
    assert "armament.blast_interference" not in codes


def test_position_validation_duplicate(db, yamato_context):
    """重複 positions は firing_arc_conflict エラー。"""
    candidate = {
        "schema_version": "navspec.armament_candidate.v1",
        "main_batteries": [{
            "battery_id": "main_1",
            "weapon_id": "JP_460MM_94SHIKI",
            "mount_id": "JP_460MM_TRIPLE_94SHIKI",
            "mount_count": 3, "barrels_per_mount": 3, "total_barrels": 9,
            "positions": ["A", "A", "X"],  # A が重複
        }],
    }
    result = validate_armament(candidate, yamato_context, db)
    assert "armament.firing_arc_conflict" in _codes(result)


def test_position_validation_invalid(db, yamato_context):
    """無効な position は firing_arc_conflict エラー。"""
    candidate = {
        "schema_version": "navspec.armament_candidate.v1",
        "main_batteries": [{
            "battery_id": "main_1",
            "weapon_id": "JP_460MM_94SHIKI",
            "mount_id": "JP_460MM_TRIPLE_94SHIKI",
            "mount_count": 3, "barrels_per_mount": 3, "total_barrels": 9,
            "positions": ["A", "B", "W"],  # W は無効
        }],
    }
    result = validate_armament(candidate, yamato_context, db)
    assert "armament.firing_arc_conflict" in _codes(result)


# ----------------------------------------------------------------------------
# 8. test_aggregate_main_gun_fields
# ----------------------------------------------------------------------------

def test_aggregate_main_gun_fields(yamato_candidate):
    """aggregate_main_gun_fields が mount_count*barrels を正しく導出する。"""
    agg = aggregate_main_gun_fields(yamato_candidate)
    assert agg["main_gun_count"] == 9  # 3 mounts * 3 barrels
    assert agg["main_gun_mount_count"] == 3


def test_aggregate_main_caliber_with_db(db, yamato_candidate):
    """DB 参照で main_gun_caliber_mm が正しく導出される。"""
    agg = aggregate_main_caliber_with_db(yamato_candidate, db)
    assert agg["main_gun_caliber_mm"] == 460
    assert agg["main_gun_count"] == 9


# ----------------------------------------------------------------------------
# 9. test_armament_reason_codes
# ----------------------------------------------------------------------------

def test_armament_reason_codes_count():
    """progress §16 の14 reason code が定義されていること。"""
    assert len(ARMAMENT_REASON_CODES) == 14


def test_armament_reason_codes_prefix():
    """全 reason code が 'armament.' プレフィックスを持つこと。"""
    for code in ARMAMENT_REASON_CODES:
        assert code.startswith("armament."), f"{code} missing armament. prefix"


def test_armament_reason_codes_emitted(db):
    """validator が出す reason code が全て ARMAMENT_REASON_CODES に含まれること。"""
    candidate = {
        "schema_version": "navspec.armament_candidate.v1",
        "main_batteries": [{
            "battery_id": "main_1",
            "weapon_id": "JP_460MM_94SHIKI",
            "mount_id": "JP_460MM_TRIPLE_94SHIKI",
            "mount_count": 3, "barrels_per_mount": 3, "total_barrels": 9,
            "positions": ["A", "A", "X"],
            "fire_control_id": "NONEXISTENT_FC",
        }],
    }
    context = {"ship_type": "destroyer", "year": 1920, "nation": "US",
               "displacement_t": 1000, "beam_m": 5.0}
    result = validate_armament(candidate, context, db)
    for code in _codes(result):
        assert code in ARMAMENT_REASON_CODES, f"unknown reason code: {code}"


# ----------------------------------------------------------------------------
# 10. test_armament_pipeline_fallback
# ----------------------------------------------------------------------------

def test_armament_pipeline_fallback_unknown_weapon(db):
    """DB に無い weapon_id の場合、weapon_mount_incompatible でフォールバック。"""
    candidate = {
        "schema_version": "navspec.armament_candidate.v1",
        "main_batteries": [{
            "battery_id": "main_1",
            "weapon_id": "ZZ_999MM_FAKE",
            "mount_id": "ZZ_999MM_TRIPLE_FAKE",
            "mount_count": 3, "barrels_per_mount": 3, "total_barrels": 9,
            "positions": ["A", "B", "X"],
        }],
    }
    context = {"ship_type": "battleship", "year": 1941, "nation": "JP",
               "displacement_t": 65000, "beam_m": 38.0}
    result = validate_armament(candidate, context, db)
    assert result["status"] == "impossible"
    assert "armament.weapon_mount_incompatible" in _codes(result)


def test_armament_pipeline_fallback_empty_batteries(db):
    """main_batteries 空の場合、即 impossible。"""
    candidate = {"schema_version": "navspec.armament_candidate.v1", "main_batteries": []}
    result = validate_armament(candidate, {}, db)
    assert result["status"] == "impossible"
