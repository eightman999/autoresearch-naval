"""Stage 4 Commit C: armament retriever のテスト。

テスト対象: tools/navspec_armament_retriever.py
DB seed: data/navspec/armament/ (Commit A で生成)
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from navspec_armament_retriever import (  # noqa: E402
    build_candidate,
    estimate_mount_count,
    generate_positions,
    retrieve_armament,
    retrieve_armament_variations,
    retrieve_available_fire_control,
    retrieve_available_mounts,
    retrieve_available_weapons,
    select_representative_weapon,
)
from navspec_armament_validator import load_db, validate_armament  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "navspec" / "armament"


@pytest.fixture(scope="module")
def db():
    return load_db(DATA_DIR)


@pytest.fixture
def yamato_context():
    return {
        "ship_type": "battleship",
        "year": 1941,
        "nation": "JP",
        "displacement_t": 65000,
        "beam_m": 38.0,
    }


# ----------------------------------------------------------------------------
# retrieve_available_*
# ----------------------------------------------------------------------------

def test_retrieve_available_weapons_filters(db, yamato_context):
    """available_weapons が era/nation/caliber_range でフィルタされる。"""
    weapons = retrieve_available_weapons(yamato_context, db)
    assert len(weapons) >= 1
    for w in weapons:
        assert w["nation"] == "JP"
        assert w.get("year_introduced", 9999) <= 1941
        assert 305 <= w["caliber_mm"] <= 460  # battleship range


def test_retrieve_available_weapons_empty_for_early_year(db):
    """1900年の日本主力艦だと候補が無い (DB の最古が1910年代以降)。"""
    ctx = {"ship_type": "battleship", "year": 1900, "nation": "JP"}
    assert retrieve_available_weapons(ctx, db) == []


def test_retrieve_available_weapons_wrong_nation(db, yamato_context):
    """nation が違うと候補が無い。"""
    ctx = {**yamato_context, "nation": "XX"}
    assert retrieve_available_weapons(ctx, db) == []


def test_retrieve_available_mounts_for_weapon(db, yamato_context):
    """JP_460MM_94SHIKI の互換 mount が取れる。"""
    mounts = retrieve_available_mounts("JP_460MM_94SHIKI", yamato_context, db)
    assert len(mounts) >= 1
    assert mounts[0]["mount_id"] == "JP_460MM_TRIPLE_94SHIKI"


def test_retrieve_available_fire_control(db, yamato_context):
    """JP 1941 で main_battery_director が取れる。"""
    fcs = retrieve_available_fire_control(yamato_context, db)
    assert any(fc["fire_control_id"] == "JP_TYPE94_DIRECTOR" for fc in fcs)


def test_retrieve_available_fire_control_radar_too_early(db):
    """year < 1938 では radar=true の FC が除外される。"""
    ctx = {"year": 1930, "nation": "US"}
    fcs = retrieve_available_fire_control(ctx, db)
    for fc in fcs:
        if fc.get("radar"):
            pytest.fail(f"radar FC {fc['fire_control_id']} should be excluded for year 1930")


# ----------------------------------------------------------------------------
# select_representative_weapon
# ----------------------------------------------------------------------------

def test_select_representative_weapon_strategies(db, yamato_context):
    """representative/mid_caliber/random の3戦略が動く。"""
    weapons = retrieve_available_weapons(yamato_context, db)
    rep = select_representative_weapon(weapons, yamato_context, "representative")
    mid = select_representative_weapon(weapons, yamato_context, "mid_caliber")
    rnd = select_representative_weapon(weapons, yamato_context, "random", random.Random(1))
    assert rep is not None and mid is not None and rnd is not None
    # representative は最大口径
    assert rep["caliber_mm"] == max(w["caliber_mm"] for w in weapons)
    # mid は representative より口径が小さいか同等 (大和型だと候補が少ないので同等の可能性)
    assert mid["caliber_mm"] <= rep["caliber_mm"]


def test_select_representative_weapon_empty():
    assert select_representative_weapon([], {}) is None


# ----------------------------------------------------------------------------
# estimate_mount_count / generate_positions
# ----------------------------------------------------------------------------

def test_estimate_mount_count_battleship_large(db):
    """大和型 460mm battleship は 3 砲塔。"""
    w = db["weapons"]["JP_460MM_94SHIKI"]
    ctx = {"ship_type": "battleship", "displacement_t": 65000}
    assert estimate_mount_count(w, ctx) == 3


def test_estimate_mount_count_cruiser(db):
    """152mm light_cruiser は 4 砲塔。"""
    w = db["weapons"]["US_6INCH_MK16"]
    ctx = {"ship_type": "light_cruiser", "displacement_t": 12000}
    assert estimate_mount_count(w, ctx) == 4


def test_generate_positions_patterns():
    """mount_count ごとの標準 positions パターン。"""
    assert generate_positions(1) == ["A"]
    assert generate_positions(2) == ["A", "X"]
    assert generate_positions(4) == ["A", "B", "X", "Y"]
    # mount_count=3 は2パターンのいずれか
    p3 = generate_positions(3, random.Random(0))
    assert p3 in (["A", "B", "X"], ["A", "X", "Y"])
    assert generate_positions(0) == []
    assert generate_positions(5) == ["A", "B", "X", "Y", "P"]
    assert len(set(generate_positions(6))) == 6


# ----------------------------------------------------------------------------
# retrieve_armament (統合)
# ----------------------------------------------------------------------------

def test_retrieve_armament_representative(db, yamato_context):
    """大和型 context で代表 candidate が生成される。"""
    cand = retrieve_armament(yamato_context, db, rng=random.Random(42))
    assert cand is not None
    assert cand["schema_version"] == "navspec.armament_candidate.v1"
    b = cand["main_batteries"][0]
    assert b["weapon_id"] == "JP_460MM_94SHIKI"
    assert b["mount_id"] == "JP_460MM_TRIPLE_94SHIKI"
    assert b["mount_count"] == 3
    assert b["total_barrels"] == 9


def test_retrieve_armament_no_candidate(db):
    """該当武器が無い context で None。"""
    ctx = {"ship_type": "battleship", "year": 1900, "nation": "JP"}
    assert retrieve_armament(ctx, db) is None


def test_retrieve_armament_new_nation_ru(db):
    """追加したRU武装が時代・艦種・砲塔就役年を通じて検索できる。"""
    ctx = {
        "ship_type": "coastal_defense",
        "year": 1940,
        "nation": "RU",
        "displacement_t": 15000,
        "beam_m": 18.0,
    }
    cand = retrieve_armament(ctx, db, rng=random.Random(7))
    assert cand is not None
    battery = cand["main_batteries"][0]
    assert battery["weapon_id"] == "RU_180MM_B1P_M1932"
    assert battery["mount_id"] == "RU_180MM_TRIPLE_B1P_M1932"
    result = validate_armament(cand, ctx, db)
    assert result["status"] == "ok", result["issues"]


def test_retrieve_armament_validates_ok(db, yamato_context):
    """生成 candidate が validator で ok になる (主要主力艦)。"""
    cand = retrieve_armament(yamato_context, db, rng=random.Random(42))
    result = validate_armament(cand, yamato_context, db)
    assert result["status"] == "ok"
    # エラー理由が含まれていないこと
    for i in result["issues"]:
        assert i["severity"] != "error"


def test_retrieve_armament_variations(db, yamato_context):
    """バリエーション生成: n 件の多様な candidate。"""
    cands = retrieve_armament_variations(yamato_context, db, n=5, seed=42)
    assert len(cands) >= 1
    # 各 candidate が validator で ok (error なし)
    for c in cands:
        result = validate_armament(c, yamato_context, db)
        assert result["status"] == "ok", f"candidate failed: {result['issues']}"


def test_retrieve_armament_variations_dedup(db, yamato_context):
    """バリエーションは (weapon_id, mount_count) で重複しない。"""
    cands = retrieve_armament_variations(yamato_context, db, n=8, seed=42)
    keys = [(c["main_batteries"][0]["weapon_id"], c["main_batteries"][0]["mount_count"]) for c in cands]
    assert len(keys) == len(set(keys))


# ----------------------------------------------------------------------------
# build_candidate
# ----------------------------------------------------------------------------

def test_build_candidate_structure(db):
    """build_candidate が armament_candidate.v1 構造を出す。"""
    w = db["weapons"]["JP_460MM_94SHIKI"]
    m = db["mounts"]["JP_460MM_TRIPLE_94SHIKI"]
    fc = db["fire_control"]["JP_TYPE94_DIRECTOR"]
    cand = build_candidate(w, m, mount_count=3, positions=["A", "B", "X"], fire_control=fc)
    assert cand["schema_version"] == "navspec.armament_candidate.v1"
    b = cand["main_batteries"][0]
    assert b["total_barrels"] == 9
    assert b["fire_control_id"] == "JP_TYPE94_DIRECTOR"
