"""Stage 4a generation/critique dataset combiner tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.navspec_lora_combine_armament_stage4a import combine_datasets


def _source(path: Path, prefix: str, rows_per_split: int = 2) -> None:
    path.mkdir()
    manifest = {
        "dataset": path.name,
        "schema_version": "navspec.armament_teacher.v1",
        "rows": rows_per_split * 3,
        "split_counts": {"train": rows_per_split, "valid": rows_per_split, "test": rows_per_split},
    }
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for split in ("train", "valid", "test"):
        rows = [
            {"id": f"{prefix}_{split}_{i}", "messages": [{"role": "system"}, {"role": "user"}, {"role": "assistant"}]}
            for i in range(rows_per_split)
        ]
        (path / f"{split}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )


def test_combines_messages_only_and_records_counts(tmp_path: Path):
    generation = tmp_path / "generation"
    critique = tmp_path / "critique"
    output = tmp_path / "combined"
    _source(generation, "gen")
    _source(critique, "crit")

    manifest = combine_datasets(generation, critique, output, seed=42)

    assert manifest["dataset"] == "combined"
    assert manifest["rows"] == 12
    assert manifest["split_counts"] == {"test": 4, "train": 4, "valid": 4}
    row = json.loads((output / "train.jsonl").read_text().splitlines()[0])
    assert list(row) == ["messages"]


def test_combiner_is_deterministic(tmp_path: Path):
    generation = tmp_path / "generation"
    critique = tmp_path / "critique"
    _source(generation, "gen", rows_per_split=4)
    _source(critique, "crit", rows_per_split=4)

    combine_datasets(generation, critique, tmp_path / "a", seed=7)
    combine_datasets(generation, critique, tmp_path / "b", seed=7)

    for split in ("train", "valid", "test"):
        assert (tmp_path / "a" / f"{split}.jsonl").read_bytes() == (
            tmp_path / "b" / f"{split}.jsonl"
        ).read_bytes()


def test_combiner_rejects_duplicate_ids(tmp_path: Path):
    generation = tmp_path / "generation"
    critique = tmp_path / "critique"
    _source(generation, "same")
    _source(critique, "same")

    with pytest.raises(ValueError, match="重複 id"):
        combine_datasets(generation, critique, tmp_path / "combined")
