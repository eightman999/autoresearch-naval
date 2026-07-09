"""Static evaluator for ShipSpecRecord JSONL files."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from navspec_common import read_jsonl, validate_spec  # direct run: tools/ on sys.path
except ImportError:
    from tools.navspec_common import read_jsonl, validate_spec  # package import from repo root


REQUIRED_TOP_LEVEL = [
    "schema_version",
    "record_id",
    "source_kind",
    "class_name",
    "country",
    "type",
    "hull_kind",
    "displacement",
    "dimensions",
    "propulsion",
    "armor",
    "armament",
    "complement",
]


def missing_required(record: dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_TOP_LEVEL if field not in record]


def evaluate_records(path: Path) -> dict[str, Any]:
    records = read_jsonl(path)
    issue_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    invented_source_count = 0

    for record in records:
        source_counts[record.get("source_kind", "missing")] += 1
        for field in missing_required(record):
            missing_counts[field] += 1

        if record.get("source_kind") == "fictional_bounded" and record.get("source_url"):
            invented_source_count += 1

        issues = validate_spec(record)
        for issue in issues:
            issue_counts[issue["code"]] += 1
            severity_counts[issue["severity"]] += 1

    return {
        "path": str(path),
        "records": len(records),
        "source_counts": dict(source_counts),
        "missing_required": dict(missing_counts),
        "issue_counts": dict(issue_counts),
        "severity_counts": dict(severity_counts),
        "fictional_records_with_source_url": invented_source_count,
        "passes": not missing_counts and invented_source_count == 0 and severity_counts.get("error", 0) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate ShipSpecRecord JSONL files")
    parser.add_argument("records", type=Path, nargs="+")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    reports = [evaluate_records(path) for path in args.records]
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2, sort_keys=True))
        return

    for report in reports:
        print(f"{report['path']}:")
        print(f"  records: {report['records']}")
        print(f"  source_counts: {report['source_counts']}")
        print(f"  missing_required: {report['missing_required']}")
        print(f"  severity_counts: {report['severity_counts']}")
        print(f"  issue_counts: {report['issue_counts']}")
        print(f"  fictional_records_with_source_url: {report['fictional_records_with_source_url']}")
        print(f"  passes: {report['passes']}")


if __name__ == "__main__":
    main()
