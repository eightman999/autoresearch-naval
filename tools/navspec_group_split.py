#!/usr/bin/env python3
"""Generation/Critique を結合し、case_group_id 単位で再分割する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_eval_run import (  # noqa: E402
    canonical_json,
    case_group_id,
    evaluation_input,
    sha256_path,
)


SPLITS = ("train", "valid", "test")
DEFAULT_RATIOS = {"train": 0.8, "valid": 0.1, "test": 0.1}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: row must be an object")
            rows.append(value)
    return rows


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _case_codes(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    expected = row.get("expected_output")
    if not isinstance(expected, dict):
        expected = row.get("output") if isinstance(row.get("output"), dict) else {}
    reason: list[str] = []
    warning: list[str] = []
    for key, target in (("reason", reason), ("warnings", warning)):
        values = expected.get(key) or []
        if not isinstance(values, list):
            continue
        for item in values:
            code = item.get("code") if isinstance(item, dict) else item
            if isinstance(code, str) and code not in target:
                target.append(code)
    return reason, warning


def _assign_groups(
    groups: dict[str, list[dict[str, Any]]],
    *,
    seed: int,
    ratios: dict[str, float],
) -> dict[str, str]:
    total_rows = sum(len(rows) for rows in groups.values())
    targets = {split: total_rows * ratios[split] for split in SPLITS}
    assigned_rows = {split: 0 for split in SPLITS}
    assignments: dict[str, str] = {}
    ordered = sorted(
        groups,
        key=lambda group: hashlib.sha256(f"{seed}\0{group}".encode("utf-8")).hexdigest(),
    )

    for index, group in enumerate(ordered):
        if index < len(SPLITS):
            split = SPLITS[index]
        else:
            split = min(
                SPLITS,
                key=lambda candidate: (
                    assigned_rows[candidate] / max(targets[candidate], 1.0),
                    assigned_rows[candidate] - targets[candidate],
                    candidate,
                ),
            )
        assignments[group] = split
        assigned_rows[split] += len(groups[group])
    return assignments


def build_group_split(
    input_paths: list[Path],
    output_dir: Path,
    *,
    seed: int = 42,
    ratios: dict[str, float] | None = None,
) -> dict[str, Any]:
    if not input_paths:
        raise ValueError("input path is required")
    ratios = ratios or dict(DEFAULT_RATIOS)
    if set(ratios) != set(SPLITS) or any(value <= 0 for value in ratios.values()):
        raise ValueError(f"ratios must contain positive {SPLITS}")
    ratio_total = sum(ratios.values())
    if abs(ratio_total - 1.0) > 1e-9:
        raise ValueError(f"split ratios must sum to 1.0, got {ratio_total}")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = [output_dir / f"{split}.jsonl" for split in SPLITS] + [output_dir / "manifest.json"]
    existing = [path for path in output_paths if path.exists()]
    if existing:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"既存versionへの上書きを拒否しました: {names}")

    rows: list[dict[str, Any]] = []
    source_meta: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in input_paths:
        path = path.resolve()
        source_rows = _read_jsonl(path)
        source_meta.append({
            "path": _relative(path),
            "sha256": sha256_path(path),
            "rows": len(source_rows),
        })
        for row_number, row in enumerate(source_rows, start=1):
            item_id = row.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError(f"{path}:{row_number}: stable id is required")
            if item_id in seen_ids:
                raise ValueError(f"duplicate id across inputs: {item_id}")
            seen_ids.add(item_id)
            input_value = evaluation_input(row)
            group = case_group_id(row, input_value)
            enriched = dict(row)
            enriched["case_group_id"] = group
            enriched["source_file"] = _relative(path)
            enriched["source_row"] = row_number
            enriched["source_split"] = row.get("split")
            rows.append(enriched)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["case_group_id"]].append(row)
    assignments = _assign_groups(groups, seed=seed, ratios=ratios)
    split_rows: dict[str, list[dict[str, Any]]] = {split: [] for split in SPLITS}
    for row in rows:
        split = assignments[row["case_group_id"]]
        row["split"] = split
        split_rows[split].append(row)

    split_groups = {
        split: {row["case_group_id"] for row in values}
        for split, values in split_rows.items()
    }
    overlap: dict[str, list[str]] = {}
    for index, left in enumerate(SPLITS):
        for right in SPLITS[index + 1:]:
            shared = sorted(split_groups[left] & split_groups[right])
            if shared:
                overlap[f"{left}:{right}"] = shared

    status_counts = {
        split: dict(Counter(row.get("expected_status") for row in values))
        for split, values in split_rows.items()
    }
    reason_counts: dict[str, dict[str, int]] = {split: {} for split in SPLITS}
    warning_counts: dict[str, dict[str, int]] = {split: {} for split in SPLITS}
    all_codes: set[str] = set()
    for split, values in split_rows.items():
        reasons = Counter()
        warnings = Counter()
        for row in values:
            row_reasons, row_warnings = _case_codes(row)
            reasons.update(row_reasons)
            warnings.update(row_warnings)
        reason_counts[split] = dict(sorted(reasons.items()))
        warning_counts[split] = dict(sorted(warnings.items()))
        all_codes.update(reasons)
        all_codes.update(warnings)

    contract_path = ROOT / "data" / "navspec" / "lora" / "contracts" / "navspec_end_to_end_eval_contract_v1.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    known_codes = set(contract.get("known_codes", {}).get("stage2_reason", []))
    known_codes.update(contract.get("known_codes", {}).get("armament_reason", []))
    known_codes.update(contract.get("known_codes", {}).get("armament_warning", []))
    unknown_codes = sorted(all_codes - known_codes)

    manifest = {
        "manifest_version": "navspec.group_split.v1",
        "generator": "tools/navspec_group_split.py",
        "seed": seed,
        "ratios": ratios,
        "source_files": source_meta,
        "source_rows": len(rows),
        "total_rows": len(rows),
        "total_groups": len(groups),
        "split_counts": {split: len(values) for split, values in split_rows.items()},
        "split_group_counts": {split: len(values) for split, values in split_groups.items()},
        "group_overlap": overlap,
        "status_counts": status_counts,
        "reason_code_counts": reason_counts,
        "warning_code_counts": warning_counts,
        "unknown_codes": unknown_codes,
        "contract": {
            "path": _relative(contract_path),
            "sha256": sha256_path(contract_path),
        },
    }

    for split, values in split_rows.items():
        with (output_dir / f"{split}.jsonl").open("w", encoding="utf-8") as handle:
            for row in values:
                handle.write(canonical_json(row) + "\n")
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", dest="input_paths", action="append", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    manifest = build_group_split(args.input_paths, args.output_dir, seed=args.seed)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
