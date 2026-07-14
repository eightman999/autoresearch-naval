"""Stage 3 numeric quality evaluator for navspec LoRA.

Runs the model on numeric-focused eval cases, extracts raw specs,
compares against canonical classic specs, and measures field adoption,
patch dependency, constraint follow, and diversity.

Uses qwen_lora_eval generation infrastructure + navspec_patch_analyzer.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_lora_data import compact  # noqa: E402
from navspec_lora_stage2_schema import validate_stage2  # noqa: E402
from navspec_patch_analyzer import (  # noqa: E402
    aggregate_results,
    analyze_spec,
    check_constraint_follow,
    load_contract,
    NUMERIC_SPEC_FIELDS,
)
from qwen_lora_eval import (  # noqa: E402
    DEFAULT_MODEL,
    DATE_STAMP,
    evaluate_row,
    extract_candidate_json,
    generate_text,
    json_prefill,
    load_mlx_lm,
    prompt_from_row,
    read_jsonl,
    repetition_collapse,
    summarize,
)
from validate_navspec_output import validate_schema  # noqa: E402

DEFAULT_SCHEMA = ROOT / "schemas" / "navspec_lora_candidate.schema.json"
DEFAULT_STAGE2_SCHEMA = ROOT / "schemas" / "navspec_lora_candidate_stage2.schema.json"
DEFAULT_CONTRACT = ROOT / "data" / "navspec" / "lora" / "contracts" / "navspec_stage3_numeric_eval_contract_v1.json"


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def era_label_for(year: int, contract: dict[str, Any]) -> str:
    for era in contract.get("categories", {}).get("eras", []):
        lo, hi = era["range"]
        if lo <= year <= hi:
            return era["label"]
    return "unknown"


def evaluate_numeric_row(
    row: dict[str, Any],
    raw_text: str,
    schema: dict[str, Any],
    contract: dict[str, Any],
    stage2: bool = False,
) -> dict[str, Any]:
    """Evaluate a single row with numeric patch analysis."""
    expected = row.get("expected_output") or row.get("output") or {}

    raw_obj: Any = {}
    try:
        raw_obj = extract_candidate_json(raw_text, schema)
    except Exception:
        pass

    if stage2:
        schema_errors = validate_stage2(raw_obj) if isinstance(raw_obj, dict) else ["not_json_object"]
    else:
        schema_errors = validate_schema(raw_obj, schema) if isinstance(raw_obj, dict) else ["not_json_object"]

    actual_status = raw_obj.get("status") if isinstance(raw_obj, dict) else None
    expected_status = expected.get("status")

    raw_spec = raw_obj.get("spec") if isinstance(raw_obj, dict) and isinstance(raw_obj.get("spec"), dict) else {}
    canonical_spec = expected.get("spec") if isinstance(expected.get("spec"), dict) else {}

    patch_analysis = None
    constraint_follow = None

    if expected_status == "ok" and actual_status == "ok":
        patch_analysis = analyze_spec(raw_spec, canonical_spec, contract)
        constraints = (row.get("input") or {}).get("constraints") or {}
        if constraints:
            constraint_follow = check_constraint_follow(raw_spec, constraints, contract)

    input_data = row.get("input") or {}
    ship_type = input_data.get("ship_type", "")
    nation = input_data.get("nation", "")
    year = int(input_data.get("year", 0))
    constraints = input_data.get("constraints") or {}

    return {
        "id": row.get("id"),
        "category": row.get("category"),
        "ship_type": ship_type,
        "nation": nation,
        "year": year,
        "era": era_label_for(year, contract),
        "raw_json_valid": isinstance(raw_obj, dict),
        "schema_complete": isinstance(raw_obj, dict) and not schema_errors,
        "schema_errors": schema_errors[:5] if schema_errors else [],
        "expected_status": expected_status,
        "actual_status": actual_status,
        "status_exact": actual_status == expected_status,
        "repetition_collapse": repetition_collapse(raw_text),
        "raw_text_len": len(raw_text),
        "status": actual_status,
        "patch_analysis": patch_analysis,
        "constraint_follow": constraint_follow,
        "has_constraints": bool(constraints),
        "constraint_count": len(constraints),
    }


def compute_diversity(
    model: Any,
    tokenizer: Any,
    generate_fn: Any,
    diversity_rows: list[dict[str, Any]],
    schema: dict[str, Any],
    contract: dict[str, Any],
    max_tokens: int,
    num_seeds: int,
) -> dict[str, Any]:
    """Generate same prompts with different temperatures to measure diversity."""
    if not diversity_rows:
        return {"unique_spec_rate": 0.0, "near_duplicate_rate": 0.0}

    temps = [0.3, 0.5, 0.7, 0.9, 1.0][:num_seeds]
    all_specs: dict[str, list[dict[str, float]]] = defaultdict(list)

    for row in diversity_rows:
        prompt = prompt_from_row(tokenizer, row)
        for temp in temps:
            text = generate_text(generate_fn, model, tokenizer, prompt, max_tokens, temp)
            try:
                obj = extract_candidate_json(text, schema)
                if isinstance(obj, dict) and isinstance(obj.get("spec"), dict):
                    spec = obj["spec"]
                    numeric_vals = {}
                    for f in NUMERIC_SPEC_FIELDS:
                        v = _num(spec.get(f))
                        if v is not None:
                            numeric_vals[f] = v
                    all_specs[row["id"]].append(numeric_vals)
            except Exception:
                pass

    unique_count = 0
    near_dup_count = 0
    total = 0

    for row_id, specs in all_specs.items():
        if len(specs) < 2:
            continue
        for i in range(len(specs)):
            for j in range(i + 1, len(specs)):
                total += 1
                s1, s2 = specs[i], specs[j]
                if s1 == s2:
                    continue
                unique_count += 1
                max_rel = 0.0
                common_keys = set(s1.keys()) & set(s2.keys())
                for k in common_keys:
                    if s1[k] > 0 and s2[k] > 0:
                        rel = abs(s1[k] - s2[k]) / max(abs(s1[k]), abs(s2[k]))
                        max_rel = max(max_rel, rel)
                if max_rel < contract.get("diversity", {}).get("near_duplicate_threshold", 0.02):
                    near_dup_count += 1

    return {
        "unique_spec_rate": unique_count / total if total else 0.0,
        "near_duplicate_rate": near_dup_count / total if total else 0.0,
        "total_pairs": total,
        "row_count": len(all_specs),
    }


def aggregate_numeric(results: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    """Aggregate numeric evaluation results."""
    total = len(results)
    if total == 0:
        return {"total": 0}

    raw_json_ok = sum(1 for r in results if r["raw_json_valid"])
    schema_ok = sum(1 for r in results if r["schema_complete"])
    status_ok = sum(1 for r in results if r["status_exact"])
    repetition = sum(1 for r in results if r["repetition_collapse"])

    expected_ok = [r for r in results if r["expected_status"] == "ok"]
    actual_ok = [r for r in results if r["actual_status"] == "ok"]
    expected_impossible = [r for r in results if r["expected_status"] == "impossible"]
    actual_impossible = [r for r in results if r["actual_status"] == "impossible"]

    tp = sum(1 for r in results if r["expected_status"] == "impossible" and r["actual_status"] == "impossible")
    fp = sum(1 for r in results if r["expected_status"] != "impossible" and r["actual_status"] == "impossible")
    fn = sum(1 for r in results if r["expected_status"] == "impossible" and r["actual_status"] != "impossible")
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    patch_agg = aggregate_results(results)

    field_summary = patch_agg.get("field_summary", {})

    by_ship_type: dict[str, dict[str, float]] = {}
    by_era: dict[str, dict[str, float]] = {}
    for r in results:
        if r.get("patch_analysis"):
            pa = r["patch_analysis"]
            st = r.get("ship_type", "unknown")
            era = r.get("era", "unknown")
            for bucket, key in [(by_ship_type, st), (by_era, era)]:
                if key not in bucket:
                    bucket[key] = {"count": 0, "adoption_sum": 0.0, "patch_dep_sum": 0.0, "major_sum": 0.0}
                bucket[key]["count"] += 1
                bucket[key]["adoption_sum"] += pa["tolerant_field_adoption_rate"]
                bucket[key]["patch_dep_sum"] += pa["patch_dependency_rate"]
                bucket[key]["major_sum"] += pa["major_patch_rate"]

    for bucket in [by_ship_type, by_era]:
        for key, vals in bucket.items():
            n = vals["count"]
            vals["tolerant_adoption_avg"] = round(vals.pop("adoption_sum") / n, 4) if n else 0.0
            vals["patch_dependency_avg"] = round(vals.pop("patch_dep_sum") / n, 4) if n else 0.0
            vals["major_patch_avg"] = round(vals.pop("major_sum") / n, 4) if n else 0.0

    gates = contract.get("adoption_gates", {})
    regression_gates = contract.get("regression_gates", {})
    field_gates = contract.get("field_gates", {})

    gate_results = {
        "exact_field_adoption": patch_agg["exact_field_adoption_rate"] >= gates.get("exact_field_adoption_min", 0.0),
        "tolerant_field_adoption": patch_agg["tolerant_field_adoption_rate"] >= gates.get("tolerant_field_adoption_min", 0.0),
        "patch_dependency": patch_agg["patch_dependency_rate"] <= gates.get("patch_dependency_max", 1.0),
        "major_patch_rate": patch_agg["major_patch_rate"] <= gates.get("major_patch_rate_max", 1.0),
        "constraint_follow": patch_agg["constraint_follow_rate"] >= gates.get("constraint_follow_min", 0.0),
        "multi_constraint_follow": patch_agg["multi_constraint_follow_rate"] >= gates.get("multi_constraint_follow_min", 0.0),
        "schema_complete": schema_ok / total >= (1.0 - regression_gates.get("schema_drop_max_pt", 1.0) / 100.0),
        "status_exact": status_ok / total >= (1.0 - regression_gates.get("status_drop_max_pt", 1.0) / 100.0),
        "impossible_f1": f1 >= (1.0 - regression_gates.get("impossible_f1_drop_max_pt", 1.0) / 100.0),
        "truncated_json": (total - raw_json_ok) / total <= regression_gates.get("truncated_json_max", 1.0),
        "repetition": repetition / total <= regression_gates.get("repetition_max", 1.0),
    }

    field_gate_results = {}
    for field, stats in field_summary.items():
        field_gate_results[field] = {
            "tolerant_adoption_pass": stats["tolerant_adoption_rate"] >= field_gates.get("tolerant_adoption_min", 0.0),
            "major_patch_pass": stats["major_patch_rate"] <= field_gates.get("major_patch_max", 1.0),
            "tolerant_adoption_rate": round(stats["tolerant_adoption_rate"], 4),
            "major_patch_rate": round(stats["major_patch_rate"], 4),
        }

    return {
        "total": total,
        "raw_json_rate": raw_json_ok / total,
        "schema_complete_rate": schema_ok / total,
        "status_exact_rate": status_ok / total,
        "repetition_collapse_rate": repetition / total,
        "impossible_precision": precision,
        "impossible_recall": recall,
        "impossible_f1": f1,
        "expected_ok_count": len(expected_ok),
        "actual_ok_count": len(actual_ok),
        "expected_impossible_count": len(expected_impossible),
        "actual_impossible_count": len(actual_impossible),
        "numeric_quality": patch_agg,
        "by_ship_type": by_ship_type,
        "by_era": by_era,
        "gate_results": gate_results,
        "field_gate_results": field_gate_results,
        "all_gates_pass": all(gate_results.values()),
    }


def write_numeric_markdown(path: Path, summary: dict[str, Any]) -> None:
    nq = summary.get("numeric_quality", {})
    gates = summary.get("gate_results", {})

    lines = [
        "# navspec Stage 3 Numeric Quality Eval",
        "",
        f"作成日: {summary.get('date', '')}",
        "",
        "## Summary",
        "",
        f"- model: `{summary.get('model')}`",
        f"- adapter: `{summary.get('adapter_path')}`" if summary.get("adapter_path") else "- adapter: _(none)_",
        f"- total: {summary['total']}",
        f"- raw JSON成功率: {summary['raw_json_rate']:.1%}",
        f"- schema完全一致率: {summary['schema_complete_rate']:.1%}",
        f"- status一致率: {summary['status_exact_rate']:.1%}",
        f"- impossible P/R/F1: {summary['impossible_precision']:.1%} / {summary['impossible_recall']:.1%} / {summary['impossible_f1']:.1%}",
        f"- 反復崩壊率: {summary['repetition_collapse_rate']:.1%}",
        "",
        "## Numeric Quality",
        "",
        f"- ok case count: {nq.get('ok_case_count', 0)}",
        f"- exact field adoption: {nq.get('exact_field_adoption_rate', 0):.1%}",
        f"- tolerant field adoption: {nq.get('tolerant_field_adoption_rate', 0):.1%}",
        f"- patch dependency: {nq.get('patch_dependency_rate', 0):.1%}",
        f"- major patch rate: {nq.get('major_patch_rate', 0):.1%}",
        f"- constraint follow: {nq.get('constraint_follow_rate', 0):.1%}",
        f"- multi constraint follow: {nq.get('multi_constraint_follow_rate', 0):.1%}",
        f"- median rel patch delta: {nq.get('relative_patch_delta_median_avg', 0):.4f}",
        f"- p90 rel patch delta: {nq.get('relative_patch_delta_p90_avg', 0):.4f}",
        "",
        "## Gate Results",
        "",
        "| gate | result |",
        "|---|:--:|",
    ]

    for gate_name, passed in gates.items():
        lines.append(f"| {gate_name} | {'PASS' if passed else 'FAIL'} |")

    lines.append("")
    lines.append(f"All gates: **{'PASS' if summary.get('all_gates_pass') else 'FAIL'}**")
    lines.append("")

    field_gates = summary.get("field_gate_results", {})
    if field_gates:
        lines.extend([
            "## Field-Level Results",
            "",
            "| field | tolerant_adoption | major_patch | adoption_pass | major_pass |",
            "|---|---:|---:|:--:|:--:|",
        ])
        for field, fg in sorted(field_gates.items()):
            lines.append(
                f"| {field} | {fg['tolerant_adoption_rate']:.1%} | {fg['major_patch_rate']:.1%} | "
                f"{'PASS' if fg['tolerant_adoption_pass'] else 'FAIL'} | "
                f"{'PASS' if fg['major_patch_pass'] else 'FAIL'} |"
            )

    by_st = summary.get("by_ship_type", {})
    if by_st:
        lines.extend([
            "",
            "## Ship-Type Breakdown",
            "",
            "| ship_type | count | tolerant_adoption | patch_dep | major_patch |",
            "|---|---:|---:|---:|---:|",
        ])
        for st, vals in sorted(by_st.items(), key=lambda kv: -kv[1]["count"]):
            lines.append(
                f"| {st} | {vals['count']} | {vals['tolerant_adoption_avg']:.1%} | "
                f"{vals['patch_dependency_avg']:.1%} | {vals['major_patch_avg']:.1%} |"
            )

    by_era = summary.get("by_era", {})
    if by_era:
        lines.extend([
            "",
            "## Era Breakdown",
            "",
            "| era | count | tolerant_adoption | patch_dep | major_patch |",
            "|---|---:|---:|---:|---:|",
        ])
        for era, vals in sorted(by_era.items()):
            lines.append(
                f"| {era} | {vals['count']} | {vals['tolerant_adoption_avg']:.1%} | "
                f"{vals['patch_dependency_avg']:.1%} | {vals['major_patch_avg']:.1%} |"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 3 numeric quality evaluator")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--adapter-path", type=Path, default=None)
    parser.add_argument("--cases", type=Path, required=True, help="Stage 3 numeric eval cases (JSONL)")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--json-prefill", choices=["none", "object", "schema"], default="none")
    parser.add_argument("--stage2", action="store_true", help="Use Stage 2 schema validation")
    parser.add_argument("--diversity-count", type=int, default=0, help="Number of rows for diversity test (0=skip)")
    parser.add_argument("--diversity-seeds", type=int, default=5)
    parser.add_argument("--json-report", type=Path, default=None)
    parser.add_argument("--md-report", type=Path, default=None)
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    contract = load_contract(args.contract)
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    rows = read_jsonl(args.cases)
    if args.limit is not None:
        rows = rows[: args.limit]

    adapter_name = args.adapter_path.name if args.adapter_path else "zero_shot"
    run_slug = f"stage3_numeric_{adapter_name}"
    if args.json_report is None:
        args.json_report = ROOT / "data" / "navspec" / "lora" / "eval" / run_slug / "report.json"
    if args.md_report is None:
        args.md_report = ROOT / "docs" / f"{run_slug}_{DATE_STAMP}.md"

    if args.dry_run:
        print(f"cases: {len(rows)}")
        print(f"model: {args.model}")
        print(f"adapter: {args.adapter_path}")
        print(f"contract: {args.contract}")
        print(f"schema: {args.schema}")
        print(f"json_report: {args.json_report}")
        print(f"md_report: {args.md_report}")
        return

    model, tokenizer, generate_fn = load_mlx_lm(args.model, args.adapter_path)
    prefill = json_prefill(args.json_prefill)

    results: list[dict[str, Any]] = []
    raw_records: list[dict[str, Any]] = []

    for index, row in enumerate(rows, start=1):
        prompt = prompt_from_row(tokenizer, row) + prefill
        raw_text = generate_text(generate_fn, model, tokenizer, prompt, args.max_tokens, args.temperature)
        result = evaluate_numeric_row(row, raw_text, schema, contract, stage2=args.stage2)
        results.append(result)
        raw_records.append({"id": row.get("id"), "prompt": prompt, "raw_text": raw_text})

        pa = result.get("patch_analysis")
        adopt = pa["tolerant_field_adoption_rate"] if pa else 0.0
        print(
            "[{}/{}] {} status={} adopt={:.1%}".format(
                index, len(rows), row.get("id"), result["actual_status"], adopt
            ),
            flush=True,
        )

    diversity = None
    if args.diversity_count > 0:
        diversity_rows = rows[:args.diversity_count]
        diversity = compute_diversity(
            model, tokenizer, generate_fn, diversity_rows, schema, contract,
            args.max_tokens, args.diversity_seeds,
        )
        print(f"diversity: {diversity}")

    summary = aggregate_numeric(results, contract)
    summary["model"] = args.model
    summary["adapter_path"] = str(args.adapter_path) if args.adapter_path else None
    summary["date"] = DATE_STAMP
    summary["cases"] = str(args.cases)
    summary["contract"] = str(args.contract)
    summary["max_tokens"] = args.max_tokens
    summary["temperature"] = args.temperature
    if diversity:
        summary["diversity"] = diversity

    report = {"summary": summary, "results": results}
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_numeric_markdown(args.md_report, summary)

    if args.raw_dir:
        args.raw_dir.mkdir(parents=True, exist_ok=True)
        for raw in raw_records:
            (args.raw_dir / f"{raw['id']}.txt").write_text(raw["raw_text"], encoding="utf-8")

    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report: {args.json_report}")
    print(f"md_report: {args.md_report}")


if __name__ == "__main__":
    main()
