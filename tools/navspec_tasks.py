"""Build text training tasks from naval specification JSONL records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from navspec_common import read_jsonl, write_jsonl


DEFAULT_REAL = Path("data/navspec/battleship_real_specs.jsonl")
DEFAULT_FICTIONAL = Path("data/navspec/battleship_fictional_specs.jsonl")
DEFAULT_TRAIN = Path("data/navspec/tasks.train.jsonl")
DEFAULT_VAL = Path("data/navspec/tasks.val.jsonl")


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def core_record(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in {"wiki_table_markdown"}}


def raw_source_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "class_name": record.get("class_name"),
        "country": record.get("country"),
        "type": record.get("type"),
        "raw": {
            "displacement_normal": record.get("displacement", {}).get("normal_raw"),
            "displacement_full": record.get("displacement", {}).get("full_raw"),
            "length": record.get("dimensions", {}).get("length_raw"),
            "breadth": record.get("dimensions", {}).get("breadth_raw"),
            "draught": record.get("dimensions", {}).get("draught_raw"),
            "machinery": record.get("propulsion", {}).get("machinery"),
            "power": record.get("propulsion", {}).get("power_raw"),
            "speed": record.get("propulsion", {}).get("speed_raw"),
            "endurance": record.get("propulsion", {}).get("endurance_raw"),
            "armament": record.get("armament", {}).get("raw"),
            "armor": record.get("armor", {}).get("raw"),
            "complement": record.get("complement", {}).get("raw"),
        },
        "source_url": record.get("source_url"),
    }


def task_text(kind: str, instruction: str, input_obj: Any, output_obj: Any) -> str:
    return (
        "<task>\n"
        f"kind: {kind}\n"
        f"instruction: {instruction}\n"
        "<input>\n"
        f"{compact(input_obj)}\n"
        "</input>\n"
        "<output>\n"
        f"{compact(output_obj)}\n"
        "</output>\n"
        "</task>"
    )


def build_real_tasks(record: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "kind": "normalize_ship_spec",
            "text": task_text(
                "normalize_ship_spec",
                "Convert the raw NSDB battleship row into a ShipSpecRecord. Do not invent missing values.",
                raw_source_view(record),
                core_record(record),
            ),
        },
        {
            "kind": "render_wiki_table",
            "text": task_text(
                "render_wiki_table",
                "Render this ShipSpecRecord as a compact wiki-style Markdown specification table.",
                core_record(record),
                record.get("wiki_table_markdown", ""),
            ),
        },
    ]


def build_fictional_tasks(record: dict[str, Any]) -> list[dict[str, str]]:
    request = {
        "base": record.get("derived_from"),
        "constraints": record.get("fictional_constraints"),
        "output_schema": "navspec.ship_spec_record.v1",
    }
    return [
        {
            "kind": "bounded_fictionalize",
            "text": task_text(
                "bounded_fictionalize",
                "Create a fictional battleship specification from the base record and constraints. Mark source_kind as fictional_bounded.",
                request,
                core_record(record),
            ),
        },
        {
            "kind": "validate_ship_spec",
            "text": task_text(
                "validate_ship_spec",
                "Return only validation_issues for this ShipSpecRecord.",
                core_record(record),
                record.get("validation_issues", []),
            ),
        },
    ]


def is_val(text: str, val_ratio: float) -> bool:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return bucket < val_ratio


def main() -> None:
    parser = argparse.ArgumentParser(description="Build navspec training tasks")
    parser.add_argument("--real", type=Path, default=DEFAULT_REAL)
    parser.add_argument("--fictional", type=Path, default=DEFAULT_FICTIONAL)
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--val", type=Path, default=DEFAULT_VAL)
    parser.add_argument("--val-ratio", type=float, default=0.12)
    args = parser.parse_args()

    tasks: list[dict[str, str]] = []
    for record in read_jsonl(args.real):
        tasks.extend(build_real_tasks(record))
    for record in read_jsonl(args.fictional):
        tasks.extend(build_fictional_tasks(record))

    train: list[dict[str, str]] = []
    val: list[dict[str, str]] = []
    for task in tasks:
        (val if is_val(task["text"], args.val_ratio) else train).append(task)

    write_jsonl(args.train, train)
    write_jsonl(args.val, val)
    print(f"wrote {len(train)} train tasks to {args.train}")
    print(f"wrote {len(val)} val tasks to {args.val}")


if __name__ == "__main__":
    main()
