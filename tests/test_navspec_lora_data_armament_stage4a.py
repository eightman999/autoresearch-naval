"""Stage 4a (Commit D) armament teacher データ生成のテスト。

teacher は classic ルールエンジン（retriever + validator）そのものなので、
ここで守りたいのは「ルールエンジンの判定が出力契約を壊さずに teacher へ落ちること」。
特に **誤った impossible を作らないこと** と **warn を握り潰さないこと** を見る。
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_armament_validator import load_db  # noqa: E402
from navspec_lora_data_armament_stage4a import (  # noqa: E402
    DB_NATIONS,
    DB_SHIP_TYPES,
    SCHEMA_VERSION,
    TOP_LEVEL_OUTPUT_FIELDS,
    UNCOVERED_NATIONS,
    UNCOVERED_SHIP_TYPES,
    build_cases,
    build_dataset,
    build_record,
    feasible_combos,
    make_output,
    retrieve_with_fallback,
    validate_teacher,
)

DATA_DIR = ROOT / "data" / "navspec" / "armament"


@pytest.fixture(scope="module")
def db():
    return load_db(DATA_DIR)


def _case(nation, year, ship_type, displacement_t=20000.0, beam_m=25.0,
          category="normal", role="fleet_battle"):
    return {
        "id": f"t_{category}",
        "category": category,
        "nation": nation,
        "year": year,
        "ship_type": ship_type,
        "role": role,
        "displacement_t": displacement_t,
        "beam_m": beam_m,
        "split": "train",
    }


# --- 出力契約 ---------------------------------------------------------------

def test_teacher_output_has_all_top_level_fields(db):
    out = make_output(_case("JP", 1941, "battleship", 64000.0, 38.9), db, random.Random(0))
    assert set(TOP_LEVEL_OUTPUT_FIELDS) <= set(out)
    assert out["schema_version"] == SCHEMA_VERSION
    assert validate_teacher(out) == []


def test_ok_output_shape(db):
    out = make_output(_case("JP", 1941, "battleship", 64000.0, 38.9), db, random.Random(0))
    assert out["status"] == "ok"
    assert out["armament"] is not None
    assert out["armament"]["main_batteries"]
    assert out["reason"] == []
    assert out["suggested_revision"] is None


def test_impossible_output_shape(db):
    out = make_output(_case("NL", 1941, "battleship"), db, random.Random(0))
    assert out["status"] == "impossible"
    assert out["armament"] is None
    assert out["reason"]
    assert out["suggested_revision"] is not None
    assert validate_teacher(out) == []


# --- reason code の切り分け --------------------------------------------------

def test_nation_without_weapons_gets_nation_code(db):
    for nation in UNCOVERED_NATIONS[:3]:
        out = make_output(_case(nation, 1941, "battleship"), db, random.Random(0))
        assert out["status"] == "impossible"
        assert out["reason"][0]["code"] == "armament.weapon_unavailable_for_nation"


def test_year_before_any_weapon_gets_era_code(db):
    out = make_output(_case("JP", 1860, "battleship"), db, random.Random(0))
    assert out["status"] == "impossible"
    assert out["reason"][0]["code"] == "armament.weapon_unavailable_for_era"
    # 提案は「実在する最古の年へ動かす」ものであること（架空の年を作らない）
    suggested = out["suggested_revision"]["year"]
    years = [w["year_introduced"] for w in db["weapons"].values()
             if w.get("nation") == "JP" and w.get("year_introduced")]
    assert suggested == min(years)


def test_ship_type_without_caliber_range_is_impossible(db):
    """口径範囲が未定義の艦種に、無制約で主砲を載せてはいけない。

    retrieve_available_weapons は範囲が引けないと口径フィルタを素通りさせるので、
    stage4a 側で先に止めていないと「gunboat に主砲」ができてしまう。
    """
    for ship_type in UNCOVERED_SHIP_TYPES:
        out = make_output(_case("JP", 1941, ship_type, 1200.0, 9.0), db, random.Random(0))
        assert out["status"] == "impossible", ship_type
        assert out["reason"][0]["code"] == "armament.battery_layout_conflict"
        assert out["armament"] is None


# --- 誤った impossible を作らない --------------------------------------------

def test_fallback_uses_a_smaller_gun_when_the_largest_does_not_fit(db):
    """代表砲（最大口径）が載らなくても、小口径砲が載るなら impossible にしない。

    retrieve_armament は代表砲1門しか試さないので、そのまま使うと
    「小口径砲なら成立するのに impossible」という誤った teacher ができる。
    """
    context = {"year": 1941, "nation": "JP", "ship_type": "light_cruiser",
               "displacement_t": 5500.0, "beam_m": 15.0}
    candidate = retrieve_with_fallback(context, db, random.Random(0))
    assert candidate is not None
    battery = candidate["main_batteries"][0]
    mount = db["mounts"][battery["mount_id"]]
    assert mount["turret_weight_t"] <= context["displacement_t"] * 0.20


def test_feasible_combos_actually_produce_ok(db):
    """feasible_combos が返した組み合わせは全部 status=ok になること。"""
    combos = feasible_combos(db)
    assert combos, "成立する組み合わせが1つも無い"
    rng = random.Random(0)
    for nation, ship_type, year in combos[:40]:
        out = make_output(_case(nation, year, ship_type,
                                *_hull(ship_type)), db, rng)
        assert out["status"] == "ok", (nation, ship_type, year, out["reason"])


def _hull(ship_type):
    from navspec_lora_data_armament_stage4a import HULL_DEFAULTS
    return HULL_DEFAULTS.get(ship_type, (10000.0, 18.0))


# --- warn を握り潰さない ------------------------------------------------------

def test_warnings_are_carried_into_ok_output(db):
    """砲塔重量が排水量の35〜50%の設計は validator では warn 止まりで ok になる。

    それを落とすと「小さすぎる船体に巨砲を積んで問題なし」という teacher になる。
    """
    out = make_output(
        _case("JP", 1941, "battleship", 7400.0, 12.0, category="hull_too_small"),
        db, random.Random(0),
    )
    if out["status"] == "ok":
        assert out["warnings"], "余裕の乏しい設計なのに warnings が空"
        assert out["confidence"] < 0.85
    else:
        assert out["reason"]


def test_normal_design_has_no_warnings(db):
    out = make_output(_case("JP", 1941, "battleship", 64000.0, 38.9), db, random.Random(0))
    assert out["status"] == "ok"
    assert out["warnings"] == []
    assert out["confidence"] == pytest.approx(0.85)


# --- 数値整合 -----------------------------------------------------------------

def test_total_barrels_and_positions_are_consistent(db):
    rng = random.Random(7)
    for nation in DB_NATIONS:
        for ship_type in DB_SHIP_TYPES:
            out = make_output(_case(nation, 1944, ship_type, *_hull(ship_type)), db, rng)
            for battery in ((out["armament"] or {}).get("main_batteries") or []):
                assert battery["total_barrels"] == (
                    battery["mount_count"] * battery["barrels_per_mount"]
                )
                assert len(battery["positions"]) == battery["mount_count"]


def test_validate_teacher_rejects_broken_output():
    broken = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "armament": {"main_batteries": [{
            "battery_id": "main_1", "weapon_id": "X", "mount_id": "Y",
            "mount_count": 3, "barrels_per_mount": 3, "total_barrels": 8,
            "positions": ["A", "B"],
        }]},
        "reason": [],
        "warnings": [],
        "suggested_revision": None,
        "design_rationale": "",
        "confidence": 0.85,
    }
    errors = validate_teacher(broken)
    assert any("total_barrels" in e for e in errors)
    assert any("positions" in e for e in errors)


def test_validate_teacher_rejects_unknown_reason_code():
    out = {
        "schema_version": SCHEMA_VERSION,
        "status": "impossible",
        "armament": None,
        "reason": [{"code": "armament.made_up_code", "message": "x"}],
        "warnings": [],
        "suggested_revision": None,
        "design_rationale": "",
        "confidence": 0.9,
    }
    assert any("unknown reason code" in e for e in validate_teacher(out))


# --- データセット全体 ---------------------------------------------------------

def test_build_cases_is_deterministic(db):
    a = build_cases(200, db, seed=42)
    b = build_cases(200, db, seed=42)
    assert [c["id"] for c in a] == [c["id"] for c in b]
    assert [c["nation"] for c in a] == [c["nation"] for c in b]


def test_every_record_passes_its_own_contract(db):
    rng = random.Random(1)
    cases = build_cases(200, db, seed=42)
    for index, case in enumerate(cases):
        case["split"] = "train" if index % 3 else "validation"
    for case in cases:
        row = build_record(case, db, rng)  # 契約違反なら ValueError を投げる
        assert row["messages"][-1]["role"] == "assistant"
        assert json.loads(row["messages"][-1]["content"])["status"] == row["expected_status"]


def test_build_dataset_writes_all_splits(tmp_path, db):
    rows = build_dataset(200, tmp_path, DATA_DIR, seed=42)
    assert rows
    for name in ("train.jsonl", "valid.jsonl", "test.jsonl", "manifest.json"):
        assert (tmp_path / name).exists(), name
    # 16d9552 で掃除された冗長な派生ファイルを作り直していないこと
    assert not (tmp_path / "validation.jsonl").exists()
    assert not (tmp_path / "all.jsonl").exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset"] == tmp_path.name
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert sum(manifest["split_counts"].values()) == len(rows)
    assert sum(manifest["status_counts"].values()) == len(rows)
    # 成立ケースと不成立ケースが両方入っていること
    assert manifest["status_counts"].get("ok", 0) > 0
    assert manifest["status_counts"].get("impossible", 0) > 0


def test_splits_do_not_share_ids(tmp_path, db):
    build_dataset(200, tmp_path, DATA_DIR, seed=42)
    seen: dict[str, str] = {}
    for split in ("train", "valid", "test"):
        for line in (tmp_path / f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            assert row["id"] not in seen, f"{row['id']} が {seen.get(row['id'])} と重複"
            seen[row["id"]] = split
