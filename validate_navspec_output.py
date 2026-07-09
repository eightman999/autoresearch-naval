"""Validate generated ShipSpecRecord JSON against the project schema and rules."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tools.navspec_common import validate_spec
from tools.navspec_eval import missing_required


DEFAULT_SCHEMA = Path("schemas/ship_spec_record.schema.json")


def extract_json_object(text: str) -> Any:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
            return obj
        except json.JSONDecodeError:
            continue
    raise ValueError("No JSON object found")


def type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {value!r}")

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}, got {value!r}")

    if "type" in schema:
        expected = schema["type"]
        if isinstance(expected, list):
            if not any(type_matches(value, item) for item in expected):
                errors.append(f"{path}: expected type {expected!r}, got {type(value).__name__}")
        elif not type_matches(value, expected):
            errors.append(f"{path}: expected type {expected!r}, got {type(value).__name__}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")

    if isinstance(value, dict):
        for field in schema.get("required", []):
            if field not in value:
                errors.append(f"{path}: missing required field {field!r}")
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                errors.extend(validate_schema(child, properties[key], f"{path}.{key}"))

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(validate_schema(item, schema["items"], f"{path}[{index}]"))

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate generated ShipSpecRecord JSON")
    parser.add_argument("input", type=Path, help="JSON file, raw generation text, or JSONL file")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--jsonl", action="store_true", help="Validate every line as a JSON record")
    args = parser.parse_args()

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    records: list[Any] = []
    raw = args.input.read_text(encoding="utf-8")
    if args.jsonl:
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    else:
        records = [json.loads(raw) if raw.lstrip().startswith("{") else extract_json_object(raw)]

    total_errors = 0
    total_warnings = 0
    for idx, record in enumerate(records):
        schema_errors = validate_schema(record, schema)
        required_missing = missing_required(record) if isinstance(record, dict) else []
        rule_issues = validate_spec(record) if isinstance(record, dict) else []
        errors = schema_errors + [f"missing top-level field: {field}" for field in required_missing]
        warnings = [issue for issue in rule_issues if issue["severity"] == "warn"]
        hard_errors = [issue for issue in rule_issues if issue["severity"] == "error"]
        errors.extend(f"{issue['code']}: {issue['message']}" for issue in hard_errors)

        total_errors += len(errors)
        total_warnings += len(warnings)
        print(f"record {idx}: errors={len(errors)} warnings={len(warnings)}")
        for error in errors:
            print(f"  ERROR {error}")
        for warning in warnings:
            print(f"  WARN {warning['code']}: {warning['message']}")

    print(f"summary: records={len(records)} errors={total_errors} warnings={total_warnings}")
    raise SystemExit(1 if total_errors else 0)


if __name__ == "__main__":
    main()
