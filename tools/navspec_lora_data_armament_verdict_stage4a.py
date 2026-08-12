#!/usr/bin/env python3
"""Build the compact Stage 4a armament verdict auxiliary dataset.

full teacher JSON では armament 本体・説明・メッセージが判定信号を薄めるため、
批評タスクの全行を ``status`` と error 由来の ``reason_codes`` だけを返す
補助タスクへ変換する。入力には元の context + candidate を残す。

使用法::

    uv run python tools/navspec_lora_data_armament_verdict_stage4a.py \
        --input-dir data/navspec/lora/stage4a_armament_critique_v1 \
        --output-dir data/navspec/lora/stage4a_armament_verdict_v1 \
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
SCHEMA_VERSION = "navspec.armament_verdict.v1"
FULL_SCHEMA_VERSION = "navspec.armament_teacher.v1"

SYSTEM_PROMPT = (
    "You are navspec-armament-judge. Given the ship context and proposed main battery, "
    "return only JSON matching navspec.armament_verdict.v1. "
    "The top-level keys must be schema_version,status,reason_codes. "
    "Use status=impossible when any armament rule is violated; otherwise use status=ok. "
    "reason_codes must contain only the error reason codes, as a JSON string array."
)

REASON_CODES = {
    "armament.weapon_unavailable_for_era",
    "armament.weapon_unavailable_for_nation",
    "armament.mount_unavailable_for_era",
    "armament.mount_unavailable_for_nation",
    "armament.weapon_mount_incompatible",
    "armament.barbette_too_large_for_beam",
    "armament.turret_weight_exceeds_hull_limit",
    "armament.magazine_volume_insufficient",
    "armament.topweight_limit_exceeded",
    "armament.firing_arc_conflict",
    "armament.blast_interference",
    "armament.battery_layout_conflict",
    "armament.armament_displacement_conflict",
    "armament.fire_control_unavailable",
}


def _read_rows(directory: Path, split: str) -> list[dict[str, Any]]:
    path = directory / f"{split}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or not isinstance(row.get("messages"), list):
            raise ValueError(f"{path}:{line_number}: messages が無い行")
        rows.append(row)
    return rows


def validate_verdict(output: dict[str, Any]) -> list[str]:
    """補助タスクの最小契約を検査する。"""
    errors: list[str] = []
    if set(output) != {"schema_version", "status", "reason_codes"}:
        errors.append("top-level keys must be schema_version,status,reason_codes")
    if output.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version != {SCHEMA_VERSION}")
    if output.get("status") not in {"ok", "impossible"}:
        errors.append("status must be ok or impossible")
    codes = output.get("reason_codes")
    if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
        errors.append("reason_codes must be a string array")
        return errors
    if len(codes) != len(set(codes)):
        errors.append("reason_codes must not contain duplicates")
    unknown = sorted(set(codes) - REASON_CODES)
    if unknown:
        errors.append(f"unknown reason code: {unknown}")
    if output.get("status") == "ok" and codes:
        errors.append("ok verdict must have empty reason_codes")
    if output.get("status") == "impossible" and not codes:
        errors.append("impossible verdict must have reason_codes")
    return errors


def make_verdict(row: dict[str, Any]) -> dict[str, Any]:
    """full teacher row を compact verdict row に変換する。"""
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 3:
        raise ValueError("full teacher row の messages が不正です")
    expected = json.loads(messages[-1]["content"])
    if expected.get("schema_version") != FULL_SCHEMA_VERSION:
        raise ValueError("full teacher row の schema_version が不正です")
    # full teacher は複数バッテリーの同一違反を複数行で持ち得るが、
    # verdict は multi-label の集合なので順序を保って一意化する。
    codes = list(dict.fromkeys(item["code"] for item in expected.get("reason") or []))
    output = {
        "schema_version": SCHEMA_VERSION,
        "status": expected.get("status"),
        "reason_codes": codes,
    }
    problems = validate_verdict(output)
    if problems:
        raise ValueError(f"verdict output invalid: {problems}")
    verdict_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        # 元の user JSON に context と candidate が含まれる。system prompt
        # だけを置換し、分類対象の入力情報は変えない。
        {"role": "user", "content": messages[-2]["content"]},
        # status を最初に出して、判定トークンが reason_codes の長さに
        # 埋もれないようにする。JSONのキー順は学習列の順序でもある。
        {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
    ]
    result = {
        "id": row.get("id"),
        "expected_status": output["status"],
        "expected_reason_codes": codes,
        "messages": verdict_messages,
        "output": output,
    }
    return result


def build_dataset(input_dir: Path, output_dir: Path, seed: int = 42) -> dict[str, Any]:
    """批評データ全件を compact verdict ChatDataset へ変換する。"""
    source_manifest = json.loads(
        (input_dir / "manifest.json").read_text(encoding="utf-8")
    )
    if source_manifest.get("schema_version") != FULL_SCHEMA_VERSION:
        raise ValueError("入力データは navspec.armament_teacher.v1 ではありません")

    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    split_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    for split in SPLITS:
        converted: list[dict[str, Any]] = []
        for row in _read_rows(input_dir, split):
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                raise ValueError(f"{input_dir}/{split}.jsonl: id が無い行")
            if row_id in seen_ids:
                raise ValueError(f"重複 id: {row_id}")
            seen_ids.add(row_id)
            result = make_verdict(row)
            converted.append({"messages": result["messages"]})
            status_counts[result["output"]["status"]] += 1
            for code in result["output"]["reason_codes"]:
                reason_counts[code] += 1
        rng.shuffle(converted)
        (output_dir / f"{split}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in converted),
            encoding="utf-8",
        )
        split_counts[split] = len(converted)

    manifest = {
        "dataset": output_dir.name,
        "schema": "schemas/navspec_armament_verdict_stage4a.schema.json",
        "schema_version": SCHEMA_VERSION,
        "task": "judge_navspec_armament",
        "purpose": "status/reason code の判定信号を full teacher JSON から分離する補助タスク",
        "source": str(input_dir),
        "source_manifest": {
            "dataset": source_manifest.get("dataset"),
            "rows": source_manifest.get("rows"),
            "split_counts": source_manifest.get("split_counts", {}),
        },
        "rows": sum(split_counts.values()),
        "seed": seed,
        "split_counts": dict(sorted(split_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "system_prompt": SYSTEM_PROMPT,
        "format": "mlx_lm ChatDataset/messages, compact JSON; status key emitted first",
        "max_output_tokens": 64,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    manifest = build_dataset(args.input_dir, args.output_dir, seed=args.seed)
    print(f"rows={manifest['rows']} status={manifest['status_counts']} splits={manifest['split_counts']}")
    print(f"-> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
