from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.navspec_group_split import build_group_split


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _rows(prefix: str, count: int = 3) -> list[dict]:
    rows = []
    for index in range(count):
        candidate = {
            "schema_version": "navspec.armament_candidate.v1",
            "main_batteries": [{
                "weapon_id": f"JP_WEAPON_{index}",
                "mount_id": f"JP_MOUNT_{index}",
                "fire_control_id": "JP_FC",
            }],
        }
        rows.append({
            "id": f"{prefix}_{index}",
            "input": {
                "candidate": candidate,
                "nation": "JP",
                "year": 1941,
                "ship_type": "battleship",
                "role": "fleet_battle",
                "constraints": {"beam_m_min": 30 + index},
            },
            "expected_status": "ok",
            "expected_output": {"status": "ok", "reason": [], "warnings": []},
            "split": prefix,
        })
    return rows


def test_group_split_is_deterministic_and_has_no_cross_split_leak(tmp_path: Path) -> None:
    generation = tmp_path / "generation.jsonl"
    critique = tmp_path / "critique.jsonl"
    _write_rows(generation, _rows("generation"))
    _write_rows(critique, _rows("critique"))

    first_dir = tmp_path / "group-v1-a"
    second_dir = tmp_path / "group-v1-b"
    first = build_group_split([generation, critique], first_dir, seed=17)
    second = build_group_split([generation, critique], second_dir, seed=17)

    assert first["total_rows"] == 6
    assert first["total_groups"] == 3
    assert first["group_overlap"] == {}
    assert first["unknown_codes"] == []
    assert first["split_counts"] == second["split_counts"]
    assert first["split_group_counts"] == second["split_group_counts"]
    assert first["source_files"][0]["sha256"] == second["source_files"][0]["sha256"]

    rows = []
    for split in ("train", "valid", "test"):
        rows.extend(json.loads(line) for line in (first_dir / f"{split}.jsonl").read_text().splitlines())
    assert len(rows) == 6
    groups_by_split = {
        split: {
            json.loads(line)["case_group_id"]
            for line in (first_dir / f"{split}.jsonl").read_text().splitlines()
        }
        for split in ("train", "valid", "test")
    }
    assert not (groups_by_split["train"] & groups_by_split["valid"])
    assert not (groups_by_split["train"] & groups_by_split["test"])
    assert not (groups_by_split["valid"] & groups_by_split["test"])


def test_group_split_refuses_to_overwrite_existing_version(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    _write_rows(source, _rows("source"))
    output_dir = tmp_path / "group-v1"
    build_group_split([source], output_dir)

    with pytest.raises(FileExistsError, match="上書き"):
        build_group_split([source], output_dir)
