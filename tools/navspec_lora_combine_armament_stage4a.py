#!/usr/bin/env python3
"""Combine Stage 4a generation and critique ChatDataset splits.

生成タスクと批評タスクは同じ assistant 出力契約を使うため、各 split の
``messages`` だけを取り出して一つの mlx-lm ChatDataset にまとめられる。
入力データ本体は gitignore 対象なので、manifest と split 件数を出力側に残し、
seed 固定のシャッフルで再現可能にする。

使用法::

    uv run python tools/navspec_lora_combine_armament_stage4a.py \
        --generation-dir data/navspec/lora/stage4a_armament_v1 \
        --critique-dir data/navspec/lora/stage4a_armament_critique_v1 \
        --output-dir data/navspec/lora/stage4a_armament_combined_v1 \
        --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SPLITS = ("train", "valid", "test")
SCHEMA_VERSION = "navspec.armament_teacher.v1"


def _split_path(directory: Path, split: str) -> Path:
    return directory / f"{split}.jsonl"


def _read_rows(directory: Path, split: str) -> list[dict[str, Any]]:
    path = _split_path(directory, split)
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("messages"), list):
            raise ValueError(f"{path}:{line_number}: messages が無い行")
        if len(row["messages"]) < 3:
            raise ValueError(f"{path}:{line_number}: messages が短すぎる")
        rows.append(row)
    return rows


def _load_manifest(directory: Path) -> dict[str, Any]:
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path}: schema_version が {SCHEMA_VERSION} ではありません")
    return manifest


def combine_datasets(
    generation_dir: Path,
    critique_dir: Path,
    output_dir: Path,
    seed: int = 42,
) -> dict[str, Any]:
    """二つの dataset を検査・シャッフルして結合し、manifest を返す。"""
    generation_manifest = _load_manifest(generation_dir)
    critique_manifest = _load_manifest(critique_dir)
    source_dirs = {
        "generation": generation_dir,
        "critique": critique_dir,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    split_counts: Counter[str] = Counter()
    source_counts: dict[str, dict[str, int]] = {}
    seen_ids: set[str] = set()
    rng = random.Random(seed)

    for split in SPLITS:
        combined: list[dict[str, Any]] = []
        source_counts[split] = {}
        for source_name, source_dir in source_dirs.items():
            rows = _read_rows(source_dir, split)
            source_counts[split][source_name] = len(rows)
            for row in rows:
                row_id = row.get("id")
                if not isinstance(row_id, str) or not row_id:
                    raise ValueError(f"{source_dir}/{split}.jsonl: id が無い行")
                if row_id in seen_ids:
                    raise ValueError(f"重複 id: {row_id}")
                seen_ids.add(row_id)
                # mlx-lm の学習入力は messages のみ。teacherのメタデータは
                # 各ソース側に保持し、結合データへは持ち込まない。
                combined.append({"messages": row["messages"]})
        rng.shuffle(combined)
        output_path = _split_path(output_dir, split)
        output_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in combined),
            encoding="utf-8",
        )
        split_counts[split] = len(combined)

    manifest = {
        "dataset": output_dir.name,
        "schema": "schemas/navspec_armament_teacher_stage4a.schema.json",
        "schema_version": SCHEMA_VERSION,
        "format": "mlx_lm ChatDataset/messages, compact JSON sort_keys=True",
        "purpose": "Stage 4a generation + critique datasets",
        "seed": seed,
        "sources": {
            "generation": str(generation_dir),
            "critique": str(critique_dir),
        },
        "source_manifests": {
            "generation": {
                "dataset": generation_manifest.get("dataset"),
                "rows": generation_manifest.get("rows"),
                "split_counts": generation_manifest.get("split_counts", {}),
            },
            "critique": {
                "dataset": critique_manifest.get("dataset"),
                "rows": critique_manifest.get("rows"),
                "split_counts": critique_manifest.get("split_counts", {}),
                "skipped_no_material": critique_manifest.get("skipped_no_material", 0),
            },
        },
        "rows": sum(split_counts.values()),
        "split_counts": dict(sorted(split_counts.items())),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-dir", type=Path, required=True)
    parser.add_argument("--critique-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    manifest = combine_datasets(
        args.generation_dir, args.critique_dir, args.output_dir, seed=args.seed
    )
    print(f"rows={manifest['rows']} splits={manifest['split_counts']}")
    print(f"-> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
