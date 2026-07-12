"""Create immutable Stage 2.x evaluation-contract datasets.

Existing Stage 1/2.x datasets are protected historical artifacts.  This tool
creates a new contract version instead of overwriting them.  The Stage 2 eval
set contained twenty legacy four-code gold rows; their redundant
speed_power_conflict is removed under the Stage 2.2+ strict definition.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_lora_data import compact, conversation_text, write_jsonl  # noqa: E402
from navspec_lora_data_stage2 import make_stage2_output  # noqa: E402
from navspec_lora_stage2_schema import (  # noqa: E402
    MAX_REASON_CODES,
    REASON_CODES,
    normalize_reason_code_sequence,
    validate_stage2,
)


DEFAULT_OUTPUT_ROOT = ROOT / "data" / "navspec" / "lora" / "stage2x_eval_contract_v1"
SOURCES = {
    "stage2_eval": ROOT / "data" / "navspec" / "lora" / "stage2_eval_v0" / "cases.jsonl",
    "speed_eval": ROOT / "data" / "navspec" / "lora" / "stage2_2_speed_eval_v0" / "cases.jsonl",
    "era_eval": ROOT / "data" / "navspec" / "lora" / "stage2_3_era_eval_v0" / "cases.jsonl",
    "set622_common_semantic": ROOT / "data" / "navspec" / "lora" / "raw_baseline_122_plus_500" / "cases.jsonl",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def reason_objects(output: dict[str, Any], codes: list[str]) -> list[dict[str, Any]]:
    messages = {
        item.get("code"): item.get("message")
        for item in output.get("reason", [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }
    return [{"code": code, "message": messages.get(code, code)} for code in codes]


def refresh_text(row: dict[str, Any], output: dict[str, Any]) -> None:
    messages = [
        message for message in row.get("messages", [])
        if message.get("role") in {"system", "user"}
    ]
    if len(messages) != 2:
        raise ValueError(f"{row.get('id')}: expected system/user messages")
    messages.append({"role": "assistant", "content": compact(output)})
    row["messages"] = messages
    row["text"] = conversation_text(messages)


def normalize_existing_row(row: dict[str, Any]) -> tuple[dict[str, Any], bool, bool]:
    result = copy.deepcopy(row)
    output = copy.deepcopy(result.get("expected_output") or result.get("output") or {})
    legacy_codes = [
        item.get("code")
        for item in output.get("reason", [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    ]
    strict_codes = normalize_reason_code_sequence(legacy_codes)
    reason_normalized = set(legacy_codes) != set(strict_codes)
    canonical_reordered = legacy_codes != sorted(legacy_codes, key=REASON_CODES.index)
    output["reason"] = reason_objects(output, strict_codes)
    for key in ("expected_output", "output"):
        result[key] = copy.deepcopy(output)
    result["expected_reason_codes"] = strict_codes
    result["split"] = "eval"
    refresh_text(result, output)
    errors = validate_stage2(output)
    if errors:
        raise ValueError(f"{result.get('id')}: strict output invalid: {errors[:5]}")
    return result, reason_normalized, canonical_reordered


def stage2_case_from_v1(row: dict[str, Any]) -> dict[str, Any]:
    source = row.get("input") or {}
    return {
        "id": row["id"],
        "category": row.get("category", "set622_common_semantic"),
        "nation": source["nation"],
        "year": int(source["year"]),
        "ship_type": source["ship_type"],
        "role": source.get("role", ""),
        "constraints": source.get("constraints") or {},
        "request": source["request"],
    }


def convert_set622_row(row: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(row)
    case = stage2_case_from_v1(result)
    output = make_stage2_output(case)
    errors = validate_stage2(output)
    if errors:
        raise ValueError(f"{case['id']}: generated Stage 2 output invalid: {errors[:5]}")
    result["category"] = f"common_{result.get('category', 'semantic')}"
    result["expected_status"] = output["status"]
    result["expected_reason_codes"] = [item["code"] for item in output["reason"]]
    result["expected_output"] = output
    result["output"] = copy.deepcopy(output)
    result["split"] = "eval"
    refresh_text(result, output)
    return result


def write_contract(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    source_info: dict[str, Any] = {}
    total_rows = 0
    normalized_rows = 0
    reordered_rows = 0

    for name, source in SOURCES.items():
        source_rows = read_jsonl(source)
        if name == "set622_common_semantic":
            rows = [convert_set622_row(row) for row in source_rows]
            changed = 0
            mode = "rebuild_stage2_gold_from_classic_input"
        else:
            converted = [normalize_existing_row(row) for row in source_rows]
            rows = [row for row, _, _ in converted]
            changed = sum(1 for _, reason_changed, _ in converted if reason_changed)
            reordered = sum(1 for _, _, row_reordered in converted if row_reordered)
            mode = "normalize_existing_stage2_gold"
        destination = output_root / name / "cases.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_jsonl(destination, rows)
        source_info[name] = {
            "source": str(source.relative_to(ROOT)),
            "source_sha256": sha256(source),
            "cases": str(destination.relative_to(ROOT)),
            "cases_sha256": sha256(destination),
            "rows": len(rows),
            "mode": mode,
            "normalized_reason_rows": changed,
            "canonical_reordered_rows": reordered if name != "set622_common_semantic" else 0,
        }
        total_rows += len(rows)
        normalized_rows += changed
        reordered_rows += reordered if name != "set622_common_semantic" else 0

    manifest = {
        "contract_version": "navspec-stage2x-eval-contract-v1",
        "reason_max": MAX_REASON_CODES,
        "canonical_order": REASON_CODES,
        "four_code_normalization": {
            "legacy": [
            "armor_too_heavy_for_hull",
                "speed_power_conflict",
                "range_volume_conflict",
                "speed_armor_range_conflict",
            ],
            "strict": [
                "armor_too_heavy_for_hull",
                "range_volume_conflict",
                "speed_armor_range_conflict",
            ],
            "rationale": "speed_armor_range_conflict is the composite primary cause; speed_power_conflict is redundant under the strict definition.",
        },
        "total_rows": total_rows,
        "normalized_reason_rows": normalized_rows,
        "canonical_reordered_rows": reordered_rows,
        "sources": source_info,
        "reproduction": "uv run --offline python tools/navspec_stage2x_contract.py --output-root data/navspec/lora/stage2x_eval_contract_v1",
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate immutable Stage 2.x evaluation-contract datasets")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    manifest = write_contract(args.output_root)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
