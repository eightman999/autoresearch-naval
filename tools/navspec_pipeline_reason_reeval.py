"""Re-evaluate pipeline reason codes using Stage 2 classification.

The pipeline report stores final_json.reason as plain strings (classic format).
Expected_output reason is [{code, message}] (Stage 2 format). This script
re-classifies the pipeline's impossible cases using make_stage2_output to
produce comparable reason codes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_lora_data_stage2 import make_stage2_output  # noqa: E402
from navspec_lora_stage2_schema import normalize_reason_code_sequence, reason_code_metrics  # noqa: E402


def main() -> None:
    report_path = ROOT / "data" / "navspec" / "lora" / "eval" / "two_stage_pipeline_2026-07-13" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    results = report["results"]

    impossible_cases = [r for r in results if r["expected_status"] == "impossible"]
    print(f"impossible cases: {len(impossible_cases)}")

    tp = fp = fn = 0
    reason_tp = reason_fp = reason_fn = 0
    exact_match = 0
    semantic_match = 0

    # load original cases for proper reason classification
    cases_path = ROOT / "data" / "navspec" / "lora" / "stage3_numeric_eval_v0" / "cases.jsonl"
    original_cases = {}
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            c = json.loads(line)
            original_cases[c.get("id")] = c

    for r in impossible_cases:
        rid = r["id"]
        orig = original_cases.get(rid, {})
        case = {
            "ship_type": orig.get("ship_type") or (orig.get("input") or {}).get("ship_type", ""),
            "year": int(orig.get("year") or (orig.get("input") or {}).get("year", 0)),
            "nation": orig.get("nation") or (orig.get("input") or {}).get("nation", ""),
            "constraints": orig.get("constraints") or (orig.get("input") or {}).get("constraints", {}),
            "request": orig.get("request") or (orig.get("input") or {}).get("request", ""),
            "role": orig.get("role") or (orig.get("input") or {}).get("role", ""),
        }

        # pipeline uses classic to determine status - re-derive reason codes
        stage2_out = make_stage2_output(case)
        pipeline_codes = normalize_reason_code_sequence(
            [item["code"] for item in stage2_out.get("reason", []) if isinstance(item, dict)]
        )
        pipeline_code_set = set(pipeline_codes)

        # expected reason codes
        exp = r.get("expected_output") or {}
        exp_codes_raw = [item["code"] for item in (exp.get("reason") or []) if isinstance(item, dict)]
        expected_codes = normalize_reason_code_sequence(exp_codes_raw)
        expected_set = set(expected_codes)

        # metrics
        if r["final_status"] == "impossible":
            tp += 1
        else:
            fn += 1

        if pipeline_code_set == expected_set:
            exact_match += 1

        # micro-F1 for reason codes
        for code in pipeline_code_set & expected_set:
            reason_tp += 1
        for code in pipeline_code_set - expected_set:
            reason_fp += 1
        for code in expected_set - pipeline_code_set:
            reason_fn += 1

    # also check FP (pipeline says impossible but expected ok)
    ok_cases = [r for r in results if r["expected_status"] != "impossible"]
    for r in ok_cases:
        if r["final_status"] == "impossible":
            fp += 1

    total_impossible = len(impossible_cases)
    impossible_precision = tp / (tp + fp) if (tp + fp) else 0.0
    impossible_recall = tp / (tp + fn) if (tp + fn) else 0.0
    impossible_f1 = (2 * impossible_precision * impossible_recall / (impossible_precision + impossible_recall)) if (impossible_precision + impossible_recall) else 0.0

    reason_precision = reason_tp / (reason_tp + reason_fp) if (reason_tp + reason_fp) else 0.0
    reason_recall = reason_tp / (reason_tp + reason_fn) if (reason_tp + reason_fn) else 0.0
    reason_f1 = (2 * reason_precision * reason_recall / (reason_precision + reason_recall)) if (reason_precision + reason_recall) else 0.0

    print(f"impossible P/R/F1: {impossible_precision:.4f} / {impossible_recall:.4f} / {impossible_f1:.4f}")
    print(f"reason micro P/R/F1: {reason_precision:.4f} / {reason_recall:.4f} / {reason_f1:.4f}")
    print(f"reason exact match: {exact_match}/{total_impossible} ({exact_match/total_impossible:.4f})")

    # Update the report summary
    report["summary"]["final_reason_micro_precision"] = round(reason_precision, 4)
    report["summary"]["final_reason_micro_recall"] = round(reason_recall, 4)
    report["summary"]["final_reason_micro_f1"] = round(reason_f1, 4)
    report["summary"]["final_reason_exact_match_rate"] = round(exact_match / total_impossible, 4) if total_impossible else 0.0

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(f"updated: {report_path}")


if __name__ == "__main__":
    main()
