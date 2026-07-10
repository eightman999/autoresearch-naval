"""Raw model evaluator for navspec generation.

統合パイプラインの patch 後品質ではなく、モデルが直接出した raw 出力を
独立して測るための評価器。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import mlx.core as mx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from generate_navspec import (  # noqa: E402
    build_prompt,
    generate_text,
    load_checkpoint,
    make_synthetic_base,
    patch_record,
)
from tools.eval_suite import build_extended_tests  # noqa: E402
from tools.navspec_eval import REQUIRED_TOP_LEVEL, missing_required  # noqa: E402
from validate_navspec_output import extract_json_object, validate_schema  # noqa: E402


DEFAULT_CHECKPOINT = ROOT / "checkpoints/navspec/latest.json"
DEFAULT_TEST_CASES = ROOT / "tools/eval_test_cases.json"
DEFAULT_SCHEMA = ROOT / "schemas/ship_spec_record.schema.json"
DEFAULT_JSON_REPORT = ROOT / "data/navspec/generated/raw_eval/report.json"
DEFAULT_MD_REPORT = ROOT / "docs/raw_model_eval_report_2026-07-10.md"


NUMERIC_PATHS: dict[str, tuple[tuple[str, ...], tuple[float, float]]] = {
    "displacement.normal_t": (("displacement", "normal_t"), (500, 150000)),
    "displacement.full_t": (("displacement", "full_t"), (500, 150000)),
    "dimensions.length_m": (("dimensions", "length_m"), (30, 400)),
    "dimensions.breadth_m": (("dimensions", "breadth_m"), (5, 60)),
    "dimensions.draught_m": (("dimensions", "draught_m"), (1, 20)),
    "propulsion.power_shp": (("propulsion", "power_shp"), (100, 500000)),
    "propulsion.speed_kn": (("propulsion", "speed_kn"), (5, 45)),
    "armor.belt_mm": (("armor", "belt_mm"), (10, 600)),
    "armament.main_caliber_mm": (("armament", "main_caliber_mm"), (76, 520)),
    "armament.main_barrels": (("armament", "main_barrels"), (1, 16)),
    "complement.crew": (("complement", "crew"), (10, 5000)),
}


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def get_path(record: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def in_range(value: float | None, bounds: tuple[float, float]) -> bool:
    if value is None:
        return False
    lo, hi = bounds
    return lo <= value <= hi


def nearly_equal(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= max(0.01, abs(b) * 0.001)


def relative_delta(raw_value: float, patched_value: float) -> float | None:
    denom = abs(patched_value)
    if denom < 1e-9:
        return None
    return abs(raw_value - patched_value) / denom


def repetition_collapse(text: str) -> bool:
    words = text.split()
    if len(words) >= 12 and len(set(words[-12:])) <= 3:
        return True
    tail = text[-80:]
    return bool(tail and len(set(tail)) <= 4 and len(tail) >= 40)


def make_base(test: dict[str, Any]) -> dict[str, Any]:
    return make_synthetic_base(test["year"], test["nation"], test["ship_type"])


def raw_schema_report(raw_obj: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_obj, dict):
        return {
            "schema_errors": ["raw output is not a JSON object"],
            "missing_required": REQUIRED_TOP_LEVEL,
            "required_present_rate": 0.0,
            "schema_complete": False,
        }
    schema_errors = validate_schema(raw_obj, schema)
    missing = missing_required(raw_obj)
    required_present = len(REQUIRED_TOP_LEVEL) - len(missing)
    return {
        "schema_errors": schema_errors,
        "missing_required": missing,
        "required_present_rate": required_present / len(REQUIRED_TOP_LEVEL),
        "schema_complete": not schema_errors and not missing,
    }


def compare_fields(
    raw_obj: dict[str, Any],
    patched: dict[str, Any],
) -> dict[str, Any]:
    field_deltas: list[dict[str, Any]] = []
    raw_valid_fields = 0
    adopted_fields = 0
    output_fields = 0

    for name, (path, bounds) in NUMERIC_PATHS.items():
        raw_value = numeric(get_path(raw_obj, path))
        patched_value = numeric(get_path(patched, path))
        raw_valid = in_range(raw_value, bounds)
        adopted = raw_valid and nearly_equal(raw_value, patched_value)

        if raw_valid:
            raw_valid_fields += 1
        if patched_value is not None:
            output_fields += 1
        if adopted:
            adopted_fields += 1

        if raw_value is None:
            reason = "missing_raw"
        elif not raw_valid:
            reason = "invalid_raw_or_out_of_range"
        elif adopted:
            reason = "adopted"
        else:
            reason = "patched_or_clamped"

        delta = None
        rel_delta = None
        if raw_value is not None and patched_value is not None:
            delta = abs(raw_value - patched_value)
            rel_delta = relative_delta(raw_value, patched_value)

        field_deltas.append(
            {
                "field": name,
                "raw_value": raw_value,
                "patched_value": patched_value,
                "raw_valid": raw_valid,
                "adopted": adopted,
                "absolute_delta": delta,
                "relative_delta": rel_delta,
                "patch_reason": reason,
            }
        )

    patched_fields = max(0, output_fields - adopted_fields)
    patch_dependency_rate = patched_fields / output_fields if output_fields else 1.0
    field_adoption_rate = adopted_fields / output_fields if output_fields else 0.0
    raw_valid_field_rate = raw_valid_fields / len(NUMERIC_PATHS)

    return {
        "raw_valid_fields": raw_valid_fields,
        "adopted_fields": adopted_fields,
        "output_fields": output_fields,
        "patched_fields": patched_fields,
        "raw_valid_field_rate": raw_valid_field_rate,
        "field_adoption_rate": field_adoption_rate,
        "patch_dependency_rate": patch_dependency_rate,
        "field_deltas": field_deltas,
    }


def evaluate_one(
    test: dict[str, Any],
    model: Any,
    tokenizer: Any,
    schema: dict[str, Any],
    profile: int,
    max_new_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    base = make_base(test)
    prompt = build_prompt(base, profile, test.get("prompt"))
    t0 = time.time()
    raw_text = generate_text(model, tokenizer, prompt, max_new_tokens, temperature)
    elapsed = time.time() - t0

    raw_json_valid = False
    raw_obj: Any = None
    raw_extract_error = None
    try:
        raw_obj = extract_json_object(raw_text)
        raw_json_valid = isinstance(raw_obj, dict)
    except Exception as exc:
        raw_obj = {}
        raw_extract_error = str(exc)

    constraints = test.get("constraints")
    patched = patch_record(raw_obj if isinstance(raw_obj, dict) else {}, base, profile, constraints=constraints)
    schema_info = raw_schema_report(raw_obj, schema)
    field_info = compare_fields(raw_obj if isinstance(raw_obj, dict) else {}, patched)

    return {
        "test_id": test["test_id"],
        "category": test["test_id"].rsplit("_", 1)[0],
        "nation": test["nation"],
        "year": test["year"],
        "ship_type": test["ship_type"],
        "raw_text_len": len(raw_text),
        "raw_text_preview": raw_text[:240],
        "raw_json_valid": raw_json_valid,
        "raw_extract_error": raw_extract_error,
        "raw_schema_complete": schema_info["schema_complete"],
        "raw_schema_errors": schema_info["schema_errors"],
        "raw_missing_required": schema_info["missing_required"],
        "raw_required_present_rate": schema_info["required_present_rate"],
        "repetition_collapse": repetition_collapse(raw_text),
        "elapsed_s": round(elapsed, 3),
        "patched_output_status": patched.get("status", "generated"),
        **field_info,
    }


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def summarize(results: list[dict[str, Any]], metadata: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    total = len(results)
    if not total:
        return {"total": 0}

    def avg(key: str) -> float:
        return sum(float(r.get(key) or 0.0) for r in results) / total

    raw_json = sum(1 for r in results if r["raw_json_valid"])
    schema_complete = sum(1 for r in results if r["raw_schema_complete"])
    repetition = sum(1 for r in results if r["repetition_collapse"])
    total_output_fields = sum(int(r.get("output_fields") or 0) for r in results)
    total_adopted_fields = sum(int(r.get("adopted_fields") or 0) for r in results)
    total_patched_fields = sum(int(r.get("patched_fields") or 0) for r in results)

    return {
        "checkpoint": str(args.checkpoint),
        "checkpoint_val_bpb": metadata.get("metrics", {}).get("val_bpb"),
        "total": total,
        "raw_json_rate": raw_json / total,
        "raw_schema_complete_rate": schema_complete / total,
        "raw_required_present_rate_avg": avg("raw_required_present_rate"),
        "raw_valid_field_rate_avg": avg("raw_valid_field_rate"),
        "field_adoption_rate_avg": avg("field_adoption_rate"),
        "patch_dependency_rate_avg": avg("patch_dependency_rate"),
        "patch_dependency_rate_field_weighted": total_patched_fields / total_output_fields if total_output_fields else 1.0,
        "field_adoption_rate_field_weighted": total_adopted_fields / total_output_fields if total_output_fields else 0.0,
        "repetition_collapse_rate": repetition / total,
        "total_output_fields": total_output_fields,
        "total_adopted_fields": total_adopted_fields,
        "total_patched_fields": total_patched_fields,
        "temperature": args.temperature,
        "max_new_tokens": args.max_new_tokens,
        "extended_count": args.extended_count,
    }


def write_markdown(path: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    worst = sorted(results, key=lambda r: (r.get("field_adoption_rate") or 0.0, r.get("raw_required_present_rate") or 0.0))[:5]
    lines = [
        "# raw model eval report",
        "",
        "作成日: 2026-07-10",
        "",
        "## Summary",
        "",
        f"- total: {summary['total']}",
        f"- raw JSON成功率: {pct(summary['raw_json_rate'])}",
        f"- raw schema完全一致率: {pct(summary['raw_schema_complete_rate'])}",
        f"- raw必須フィールド充足率(avg): {pct(summary['raw_required_present_rate_avg'])}",
        f"- raw有効specフィールド率(avg): {pct(summary['raw_valid_field_rate_avg'])}",
        f"- field adoption率(avg): {pct(summary['field_adoption_rate_avg'])}",
        f"- patch dependency率(avg): {pct(summary['patch_dependency_rate_avg'])}",
        f"- patch dependency率(field weighted): {pct(summary['patch_dependency_rate_field_weighted'])}",
        f"- 反復崩壊率: {pct(summary['repetition_collapse_rate'])}",
        "",
        "## Worst Cases",
        "",
        "| test_id | raw_json | schema | required | adoption | patch_dependency | raw_len |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in worst:
        lines.append(
            "| {test_id} | {raw_json} | {schema} | {required} | {adoption} | {dependency} | {raw_len} |".format(
                test_id=row["test_id"],
                raw_json="yes" if row["raw_json_valid"] else "no",
                schema="yes" if row["raw_schema_complete"] else "no",
                required=pct(row["raw_required_present_rate"]),
                adoption=pct(row["field_adoption_rate"]),
                dependency=pct(row["patch_dependency_rate"]),
                raw_len=row["raw_text_len"],
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- この評価はモデルraw出力だけを採点し、統合パイプラインの成功率とは分離している。",
            "- `patch_dependency_rate` は、最終数値フィールドのうち raw 値を採用できなかった割合。",
            "- 詳細JSONには各数値フィールドの raw値、patched値、delta、patch_reason を保存している。",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_tests(path: Path, extended_count: int, limit: int | None) -> list[dict[str, Any]]:
    tests = json.loads(path.read_text(encoding="utf-8"))
    if extended_count > 0:
        tests = tests + build_extended_tests(extended_count)
    if limit is not None:
        tests = tests[:limit]
    return tests


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate raw navspec model output without patch success masking")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--test-cases", type=Path, default=DEFAULT_TEST_CASES)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--profile", type=int, default=0)
    parser.add_argument("--extended-count", type=int, default=100)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json-report", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--md-report", type=Path, default=DEFAULT_MD_REPORT)
    args = parser.parse_args()

    if hasattr(mx.random, "seed"):
        mx.random.seed(args.seed)

    tests = load_tests(args.test_cases, args.extended_count, args.limit)
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    model, tokenizer, metadata = load_checkpoint(args.checkpoint)

    results: list[dict[str, Any]] = []
    for index, test in enumerate(tests, start=1):
        print(f"[{index}/{len(tests)}] {test['test_id']} {test['nation']}/{test['year']}/{test['ship_type']}", flush=True)
        result = evaluate_one(test, model, tokenizer, schema, args.profile, args.max_new_tokens, args.temperature)
        results.append(result)
        print(
            "  raw_json={raw_json} schema={schema} adoption={adoption:.1%} patch_dependency={dependency:.1%}".format(
                raw_json=result["raw_json_valid"],
                schema=result["raw_schema_complete"],
                adoption=result["field_adoption_rate"],
                dependency=result["patch_dependency_rate"],
            ),
            flush=True,
        )

    summary = summarize(results, metadata, args)
    report = {"summary": summary, "results": results}
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(args.md_report, summary, results)

    print("\nsummary")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report: {args.json_report}")
    print(f"md_report: {args.md_report}")


if __name__ == "__main__":
    main()
