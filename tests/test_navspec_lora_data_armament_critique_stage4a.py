"""Stage 4a armament 批評タスクデータ生成のテスト。

このデータセットの存在理由は「生成タスクでは踏めない reason code を踏ませること」なので、
テストの主眼は **狙った code が実際に出ているか** に置く。
摂動が validator の別の分岐を踏んでしまっても気づけるように、code を名指しで確認する。
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
from navspec_lora_data_armament_critique_stage4a import (  # noqa: E402
    PERTURBATIONS,
    RATIOS,
    SCHEMA_VERSION,
    build_cases,
    build_dataset,
    build_record,
    make_candidate,
    make_output,
)
from navspec_lora_data_armament_stage4a import (  # noqa: E402
    HULL_DEFAULTS,
    validate_teacher,
)

DATA_DIR = ROOT / "data" / "navspec" / "armament"

# 摂動カテゴリ -> 出てほしい reason / warning code
EXPECTED_CODES = {
    "incompatible_mount": "armament.weapon_mount_incompatible",
    "foreign_mount": "armament.mount_unavailable_for_nation",
    "bad_positions": "armament.firing_arc_conflict",
    "stacked_mounts": "armament.blast_interference",
    "unknown_fire_control": "armament.fire_control_unavailable",
    "early_radar_fire_control": "armament.fire_control_unavailable",
    "oversized_magazine": "armament.magazine_volume_insufficient",
}


@pytest.fixture(scope="module")
def db():
    return load_db(DATA_DIR)


@pytest.fixture(scope="module")
def rows(db):
    cases = build_cases(600, db, seed=42)
    for index, case in enumerate(cases):
        case["split"] = "train" if index % 3 else "validation"
    rng = random.Random(43)
    out = []
    for case in cases:
        row = build_record(case, db, rng)
        if row is not None:
            out.append(row)
    return out


def test_every_perturbation_category_is_reachable(rows):
    """RATIOS に載せたカテゴリは、必ず1件以上のレコードになること。

    材料不足で全件スキップされると、静かに0件のカテゴリができる。
    """
    produced = {r["category"] for r in rows}
    for category in RATIOS:
        assert category in produced, f"{category} が1件も生成されていない"


@pytest.mark.parametrize("category,code", sorted(EXPECTED_CODES.items()))
def test_perturbation_produces_its_code(rows, category, code):
    hit = [r for r in rows
           if r["category"] == category
           and code in (r["expected_reason_codes"] + r["expected_warning_codes"])]
    subset = [r for r in rows if r["category"] == category]
    assert subset, f"{category} のレコードが無い"
    assert hit, f"{category} が {code} を1件も出していない"


def test_clean_candidates_are_accepted(rows):
    """壊していない候補は status=ok になること（常に拒否する teacher にしない）。"""
    clean = [r for r in rows if r["category"] == "clean"]
    assert clean
    ok = [r for r in clean if r["expected_status"] == "ok"]
    assert len(ok) / len(clean) > 0.9, f"clean の ok 率が低すぎる: {len(ok)}/{len(clean)}"


def test_all_records_satisfy_the_contract(rows):
    for row in rows:
        assert validate_teacher(row["output"]) == []
        assert row["messages"][0]["role"] == "system"
        assert json.loads(row["messages"][1]["content"])["task"] == "critique_navspec_armament"
        assert "candidate" in json.loads(row["messages"][1]["content"])


def test_impossible_records_have_null_armament(rows):
    for row in rows:
        if row["expected_status"] == "impossible":
            assert row["output"]["armament"] is None
            assert row["output"]["reason"]


def test_mount_year_is_missing_in_the_db(db):
    """`mount_unavailable_for_era` が踏めない理由をテストとして固定しておく。

    mounts.jsonl に year_introduced が入ったらこのテストが落ちる。そのときは
    perturb_anachronistic_mount を RATIOS に戻すこと。
    """
    with_year = [m for m in db["mounts"].values() if m.get("year_introduced")]
    assert not with_year, (
        "mounts.jsonl に year_introduced が入った。"
        "perturb_anachronistic_mount を RATIOS に戻して "
        "armament.mount_unavailable_for_era をカバーできる"
    )
    assert "anachronistic_mount" not in RATIOS
    assert "anachronistic_mount" in PERTURBATIONS  # 復活できる状態で残してある


def test_perturbation_does_not_mutate_the_clean_candidate(db):
    """摂動は deepcopy 経由で行い、元の candidate を壊さないこと。"""
    case = {
        "id": "t", "category": "clean", "nation": "JP", "year": 1941,
        "ship_type": "battleship", "role": "fleet_battle",
        "displacement_t": HULL_DEFAULTS["battleship"][0],
        "beam_m": HULL_DEFAULTS["battleship"][1], "split": "train",
    }
    rng = random.Random(0)
    clean = make_candidate(case, db, rng)
    assert clean is not None
    before = json.dumps(clean, sort_keys=True)

    case_broken = {**case, "category": "foreign_mount"}
    make_candidate(case_broken, db, random.Random(0))
    assert json.dumps(clean, sort_keys=True) == before


def test_build_dataset_writes_expected_files(tmp_path, db):
    rows = build_dataset(400, tmp_path, DATA_DIR, seed=42)
    assert rows
    for name in ("train.jsonl", "valid.jsonl", "test.jsonl", "manifest.json"):
        assert (tmp_path / name).exists(), name
    assert not (tmp_path / "validation.jsonl").exists()
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["task"] == "critique_navspec_armament"
    # スキップは黙って捨てず件数を残していること
    assert "skipped_no_material" in manifest
    assert sum(manifest["status_counts"].values()) == len(rows)


def test_dataset_is_deterministic(tmp_path, db):
    a = build_dataset(300, tmp_path / "a", DATA_DIR, seed=42)
    b = build_dataset(300, tmp_path / "b", DATA_DIR, seed=42)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert [r["output"] for r in a] == [r["output"] for r in b]


def test_output_matches_validator_verdict(db):
    """teacher が validator の判定を写しているか（推測で code を作っていないか）。"""
    from navspec_armament_validator import validate_armament
    from navspec_lora_data_armament_critique_stage4a import context_of

    case = {
        "id": "t", "category": "foreign_mount", "nation": "JP", "year": 1941,
        "ship_type": "battleship", "role": "fleet_battle",
        "displacement_t": HULL_DEFAULTS["battleship"][0],
        "beam_m": HULL_DEFAULTS["battleship"][1], "split": "train",
    }
    rng = random.Random(3)
    candidate = make_candidate(case, db, rng)
    out = make_output(case, candidate, db)
    verdict = validate_armament(candidate, context_of(case), db)
    errors = {i["code"] for i in verdict["issues"] if i["severity"] == "error"}
    warns = {i["code"] for i in verdict["issues"] if i["severity"] == "warn"}
    assert {i["code"] for i in out["reason"]} == errors
    assert {i["code"] for i in out["warnings"]} == warns
