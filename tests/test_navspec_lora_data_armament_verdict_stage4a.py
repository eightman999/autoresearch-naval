"""Compact Stage 4a verdict auxiliary dataset tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.navspec_lora_data_armament_verdict_stage4a import (  # noqa: E402
    SCHEMA_VERSION,
    build_dataset,
    make_verdict,
    validate_verdict,
)


def _source(path: Path) -> None:
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps({"dataset": "critique", "schema_version": "navspec.armament_teacher.v1"}),
        encoding="utf-8",
    )
    for split in ("train", "valid", "test"):
        output = {
            "schema_version": "navspec.armament_teacher.v1",
            "status": "impossible" if split == "test" else "ok",
            "armament": None if split == "test" else {"main_batteries": []},
            "reason": ([{"code": "armament.weapon_mount_incompatible", "message": "x"}]
                       if split == "test" else []),
            "warnings": [], "suggested_revision": None,
            "design_rationale": "", "confidence": 0.85,
        }
        row = {
            "id": f"{split}_0",
            "messages": [
                {"role": "system", "content": "old"},
                {"role": "user", "content": '{"task":"critique_navspec_armament"}'},
                {"role": "assistant", "content": json.dumps(output)},
            ],
        }
        (path / f"{split}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_validate_verdict_contract():
    assert validate_verdict({
        "schema_version": SCHEMA_VERSION,
        "status": "impossible",
        "reason_codes": ["armament.weapon_mount_incompatible"],
    }) == []
    assert validate_verdict({
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "reason_codes": ["armament.weapon_mount_incompatible"],
    })


def test_make_verdict_keeps_context_and_compacts_output(tmp_path: Path):
    source = tmp_path / "source"
    _source(source)
    row = json.loads((source / "test.jsonl").read_text().splitlines()[0])
    result = make_verdict(row)
    assert result["output"] == {
        "schema_version": SCHEMA_VERSION,
        "status": "impossible",
        "reason_codes": ["armament.weapon_mount_incompatible"],
    }
    assert result["messages"][0]["content"].startswith("You are navspec-armament-judge")
    assert json.loads(result["messages"][1]["content"])["task"] == "critique_navspec_armament"
    assert list(json.loads(result["messages"][2]["content"])) == [
        "schema_version", "status", "reason_codes"
    ]
    assert set(json.loads(result["messages"][2]["content"])) == {
        "schema_version", "status", "reason_codes"
    }


def test_build_dataset_uses_all_source_rows_and_is_deterministic(tmp_path: Path):
    source = tmp_path / "source"
    _source(source)
    a = tmp_path / "a"
    b = tmp_path / "b"
    ma = build_dataset(source, a, seed=42)
    mb = build_dataset(source, b, seed=42)
    assert ma["rows"] == 3
    assert ma["status_counts"] == {"impossible": 1, "ok": 2}
    assert ma["reason_code_counts"] == {"armament.weapon_mount_incompatible": 1}
    for split in ("train", "valid", "test"):
        assert (a / f"{split}.jsonl").read_bytes() == (b / f"{split}.jsonl").read_bytes()
        row = json.loads((a / f"{split}.jsonl").read_text().splitlines()[0])
        assert list(row) == ["messages"]
