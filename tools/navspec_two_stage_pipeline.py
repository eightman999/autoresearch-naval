"""navspec two-stage pipeline.

Stage 2 (status candidate) -> classic validator (official status/reason)
-> numeric specialist (spec candidates when ok) -> classic numeric patch
-> final JSON.

Sections 5, 20-28 に従う:
- Section 5: classic が impossible なら numeric specialist を呼ばない
- Section 21: numeric specialist へ reason 文を渡さない
- Section 22: final JSON は Stage 2 schema へ戻す
- Section 23: confidence は pipeline 側で補正
- Section 24: patch metadata を内部評価用に保存
- Section 28: fallback 条件

推論は Stage 2 → numeric specialist の順で別々にモデルを load する。
classic validation / patch は model 不要で実行できる。
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_contradiction import check_contradiction  # noqa: E402
from navspec_lora_data import (  # noqa: E402
    compact,
    make_impossible_output,
    make_ok_output,
)
from navspec_nation_tier import get_nation_tier  # noqa: E402
from navspec_patch_analyzer import _major_threshold, _minor_tolerance, _num  # noqa: E402
from qwen_lora_eval import (  # noqa: E402
    DEFAULT_MODEL,
    DATE_STAMP,
    extract_candidate_json,
    generate_text,
    json_prefill,
    load_mlx_lm,
    prompt_from_row,
    read_jsonl,
    repetition_collapse,
)

DEFAULT_SCHEMA = ROOT / "schemas" / "navspec_lora_candidate.schema.json"
DEFAULT_STAGE2_ADAPTER = ROOT / "models" / "navspec-lora-0.5b-v0.1" / "adapter" / "stage2-status-r8"
DEFAULT_NUMERIC_ADAPTER = ROOT / "models" / "navspec-lora-0.5b-v0.1" / "adapter" / "stage3-numeric-quality-r8-v1"
DEFAULT_CONTRACT = ROOT / "data" / "navspec" / "lora" / "contracts" / "navspec_numeric_specialist_eval_contract_v1.json"

# pipeline final schema (Section 22)
FINAL_SCHEMA_VERSION = "navspec.lora_candidate.v2"

NUMERIC_FIELDS = [
    "standard_displacement_t", "full_load_displacement_t", "length_m", "beam_m",
    "draft_m", "power_shp", "speed_kt", "range_nm",
    "main_gun_caliber_mm", "main_gun_count", "belt_armor_mm", "torpedo_tubes",
]

CONFIDENCE_PENALTIES = {
    "major_patch": 0.10,
    "minor_patch": 0.02,
    "missing_required_field": 0.20,
    "constraint_violation": 0.20,
    "category_outlier": 0.05,
}


def era_label_for(year: int, contract: dict[str, Any]) -> str:
    for era in contract.get("categories", {}).get("eras", []):
        lo, hi = era["range"]
        if lo <= year <= hi:
            return era["label"]
    return "unknown"


def classic_validate_status(case: dict[str, Any]) -> dict[str, Any]:
    """classic rule engine で正式 status/reason を確定。"""
    inp = case.get("input") or case
    ship_type = inp.get("ship_type", case.get("ship_type", ""))
    year = int(inp.get("year", case.get("year", 0)))
    nation = inp.get("nation", case.get("nation", ""))
    constraints = inp.get("constraints", case.get("constraints"))
    raw_prompt = inp.get("request", case.get("request"))

    return check_contradiction(
        ship_type=ship_type,
        year=year,
        nation=nation,
        constraints=constraints,
        raw_prompt=raw_prompt,
    )


def classic_make_output(case: dict[str, Any], contradiction: dict[str, Any]) -> dict[str, Any]:
    """classic で正式な ok / impossible 出力を構築。"""
    if contradiction.get("status") == "impossible":
        return make_impossible_output(case, contradiction)
    return make_ok_output(case)


def patch_numeric_spec(
    raw_spec: dict[str, Any] | None,
    canonical_spec: dict[str, Any],
    contract: dict[str, Any],
    constraints: dict[str, Any] | None,
) -> dict[str, Any]:
    """numeric specialist の raw spec を classic canonical へ向けて patch。

    Section 5.4: field validation / minor patch / major patch / cross-field / final 確定。
    返り値: {final_spec, patches[], exact_adoption, tolerant_adoption, major_patch_rate}
    """
    raw_spec = raw_spec or {}
    constraint_fields = contract.get("constraint_fields", {})
    fields_out: list[dict[str, Any]] = []
    final_spec: dict[str, Any] = {}

    for field in NUMERIC_FIELDS:
        raw_v = _num(raw_spec.get(field))
        canon_v = _num(canonical_spec.get(field))

        if raw_v is None:
            severity = "missing"
            final_v = canon_v
            adopted = False
            reason = "raw_value_missing_or_non_numeric"
        elif canon_v is None:
            severity = "canonical_missing"
            final_v = raw_v
            adopted = False
            reason = "canonical_value_unavailable"
        else:
            delta = abs(raw_v - canon_v)
            eps = max(abs(canon_v), 0.01)
            rel_delta = delta / eps
            minor_tol = _minor_tolerance(field, contract, canon_v)
            major_thr = _major_threshold(field, contract, canon_v)
            if delta <= minor_tol:
                severity = "adopted"
                final_v = raw_v
                adopted = True
                reason = "within_tolerance"
            elif delta <= major_thr:
                severity = "minor"
                final_v = canon_v
                adopted = True
                reason = "minor_adjustment"
            else:
                severity = "major"
                final_v = canon_v
                adopted = False
                reason = "major_replacement"

        # constraint 優先: 制約がある場合、final 値が制約を満たすように canonical を優先
        if constraints and canon_v is not None:
            for ckey, cmeta in constraint_fields.items():
                if ckey not in constraints:
                    continue
                if cmeta["target"] != field:
                    continue
                limit = _num(constraints[ckey])
                if limit is None:
                    continue
                if cmeta["direction"] == "min" and final_v is not None and final_v < limit:
                    final_v = max(final_v, limit) if final_v else limit
                elif cmeta["direction"] == "max" and final_v is not None and final_v > limit:
                    final_v = min(final_v, limit) if final_v else limit

        if final_v is not None:
            final_spec[field] = final_v

        fields_out.append({
            "field": field,
            "raw": raw_v,
            "canonical": canon_v,
            "final": final_v,
            "severity": severity,
            "adopted": adopted,
            "patch_reason": reason,
            "absolute_delta": round(abs(raw_v - canon_v), 4) if (raw_v is not None and canon_v is not None) else None,
            "relative_delta": round(abs(raw_v - canon_v) / max(abs(canon_v), 0.01), 4) if (raw_v is not None and canon_v is not None) else None,
        })

    total = len(fields_out)
    adopted_count = sum(1 for f in fields_out if f["adopted"])
    exact = sum(1 for f in fields_out if f["severity"] == "adopted")
    major = sum(1 for f in fields_out if f["severity"] == "major")
    missing = sum(1 for f in fields_out if f["severity"] == "missing")

    # non-numeric spec fields は canonical から引き継ぐ
    for k in ["nation", "year", "ship_type", "lb_ratio", "radar", "nation_tier"]:
        if canonical_spec.get(k) is not None:
            final_spec[k] = canonical_spec[k]

    return {
        "final_spec": final_spec,
        "patches": fields_out,
        "exact_adoption": round(exact / total, 4) if total else 0.0,
        "tolerant_adoption": round(adopted_count / total, 4) if total else 0.0,
        "major_patch_rate": round(major / total, 4) if total else 0.0,
        "missing_rate": round(missing / total, 4) if total else 0.0,
    }


def compute_pipeline_confidence(
    base_confidence: float,
    patch_result: dict[str, Any],
    constraint_violations: int,
    missing_required: int,
) -> float:
    """Section 23: pipeline 側で confidence を補正。"""
    conf = base_confidence
    patches = patch_result.get("patches", [])
    major_count = sum(1 for p in patches if p["severity"] == "major")
    minor_count = sum(1 for p in patches if p["severity"] == "minor")
    conf -= major_count * CONFIDENCE_PENALTIES["major_patch"]
    conf -= minor_count * CONFIDENCE_PENALTIES["minor_patch"]
    conf -= missing_required * CONFIDENCE_PENALTIES["missing_required_field"]
    conf -= constraint_violations * CONFIDENCE_PENALTIES["constraint_violation"]
    return round(max(0.0, min(1.0, conf)), 4)


def build_final_json(
    case: dict[str, Any],
    classic_status: str,
    classic_output: dict[str, Any],
    numeric_raw_spec: dict[str, Any] | None,
    numeric_design_concept: str | None,
    patch_result: dict[str, Any] | None,
    stage2_design_concept: str | None,
    generator_name: str,
    status_model_name: str,
    validator_name: str,
) -> dict[str, Any]:
    """Section 22: final JSON 構築。"""
    constraints = (case.get("input") or case).get("constraints") or {}

    if classic_status == "impossible":
        return {
            "schema_version": FINAL_SCHEMA_VERSION,
            "status": "impossible",
            "reason": classic_output.get("reason", []),
            "spec": None,
            "suggested_revision": classic_output.get("suggested_revision", {}),
            "design_concept": classic_output.get("design_concept", ""),
            "confidence": round(classic_output.get("confidence", 0.9), 4),
        }

    final_spec = patch_result["final_spec"] if patch_result else classic_output.get("spec", {})
    required = ["nation", "year", "ship_type", "standard_displacement_t", "full_load_displacement_t",
                "length_m", "beam_m", "draft_m", "lb_ratio", "power_shp", "speed_kt", "range_nm",
                "main_gun_caliber_mm", "main_gun_count", "belt_armor_mm", "torpedo_tubes", "radar", "nation_tier"]
    missing_required = sum(1 for f in required if final_spec.get(f) is None)

    constraint_violations = 0
    constraint_fields = {}
    try:
        contract = json.loads(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
        constraint_fields = contract.get("constraint_fields", {})
    except Exception:
        pass
    for ckey, cmeta in constraint_fields.items():
        if ckey not in constraints:
            continue
        limit = _num(constraints[ckey])
        final_v = _num(final_spec.get(cmeta["target"]))
        if limit is None or final_v is None:
            continue
        if cmeta["direction"] == "min" and final_v < limit:
            constraint_violations += 1
        elif cmeta["direction"] == "max" and final_v > limit:
            constraint_violations += 1

    base_conf = 0.88
    confidence = compute_pipeline_confidence(
        base_conf, patch_result or {}, constraint_violations, missing_required
    )

    design_concept = numeric_design_concept or stage2_design_concept or classic_output.get("design_concept", "")

    final = {
        "schema_version": FINAL_SCHEMA_VERSION,
        "status": "ok",
        "reason": [],
        "spec": final_spec,
        "suggested_revision": {},
        "design_concept": design_concept,
        "confidence": confidence,
    }

    if patch_result:
        final["_patch_metadata"] = {
            "generator": generator_name,
            "status_model": status_model_name,
            "validator": validator_name,
            "patch_summary": {
                "exact_adoption": patch_result["exact_adoption"],
                "tolerant_adoption": patch_result["tolerant_adoption"],
                "major_patch_rate": patch_result["major_patch_rate"],
            },
            "patches": patch_result["patches"],
        }

    return final


def determine_fallback(
    raw_numeric_text: str,
    numeric_spec: dict[str, Any] | None,
    patch_result: dict[str, Any] | None,
) -> str | None:
    """Section 28: fallback 条件を判定。"""
    if not numeric_spec:
        return "numeric_spec_empty"
    if patch_result and patch_result["missing_rate"] > 0.30:
        return "spec_required_insufficient"
    if repetition_collapse(raw_numeric_text):
        return "repetition_collapse"
    return None


def run_stage2_phase(
    model_name: str,
    adapter_path: Path,
    rows: list[dict[str, Any]],
    schema: dict[str, Any],
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Stage 2 を全 case で実行し raw_text + parsed を返す。"""
    model, tokenizer, generate_fn = load_mlx_lm(model_name, adapter_path)
    prefill = ""
    outputs = []
    t0 = time.time()
    for i, row in enumerate(rows, start=1):
        prompt = prompt_from_row(tokenizer, row) + prefill
        raw_text = generate_text(generate_fn, model, tokenizer, prompt, max_tokens, 0.0)
        try:
            obj = extract_candidate_json(raw_text, schema)
        except Exception:
            obj = None
        outputs.append({
            "id": row.get("id"),
            "raw_text": raw_text,
            "parsed": obj if isinstance(obj, dict) else {},
        })
        if i % 100 == 0 or i == len(rows):
            print(f"  stage2 [{i}/{len(rows)}] ({time.time()-t0:.0f}s)", flush=True)
    del model, tokenizer, generate_fn
    gc.collect()
    return outputs


def run_numeric_phase(
    model_name: str,
    adapter_path: Path,
    rows: list[dict[str, Any]],
    schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, dict[str, Any]]:
    """numeric specialist を指定 case で実行。raw_text + parsed を id ごとに返す。"""
    model, tokenizer, generate_fn = load_mlx_lm(model_name, adapter_path)
    prefill = ""
    outputs: dict[str, dict[str, Any]] = {}
    t0 = time.time()
    for i, row in enumerate(rows, start=1):
        prompt = prompt_from_row(tokenizer, row) + prefill
        raw_text = generate_text(generate_fn, model, tokenizer, prompt, max_tokens, 0.0)
        try:
            obj = extract_candidate_json(raw_text, schema)
        except Exception:
            obj = None
        outputs[row.get("id")] = {
            "raw_text": raw_text,
            "parsed": obj if isinstance(obj, dict) else {},
        }
        if i % 100 == 0 or i == len(rows):
            print(f"  numeric [{i}/{len(rows)}] ({time.time()-t0:.0f}s)", flush=True)
    del model, tokenizer, generate_fn
    gc.collect()
    return outputs


def assemble_pipeline(
    rows: list[dict[str, Any]],
    stage2_outputs: list[dict[str, Any]],
    numeric_outputs: dict[str, dict[str, Any]],
    contract: dict[str, Any],
    generator_name: str,
    status_model_name: str,
    validator_name: str,
) -> list[dict[str, Any]]:
    """classic validation + patch + final JSON 構築。"""
    results = []
    fallback_count = 0
    numeric_call_count = 0
    latencies: list[float] = []

    for row, s2 in zip(rows, stage2_outputs):
        t0 = time.time()
        case = row.get("input") or row
        case_full = {"input": case, **{k: v for k, v in row.items() if k != "input"}}
        # expected
        expected = row.get("expected_output") or {}

        # classic validation (official status)
        contradiction = classic_validate_status(case_full)
        classic_status = contradiction.get("status", "ok")
        classic_output = classic_make_output(case_full, contradiction)

        stage2_parsed = s2.get("parsed", {})
        stage2_design_concept = stage2_parsed.get("design_concept") if isinstance(stage2_parsed, dict) else None

        patch_result = None
        numeric_raw_spec = None
        numeric_design_concept = None
        fallback_reason = None
        numeric_called = False

        if classic_status == "ok":
            numeric_called = True
            numeric_call_count += 1
            num_out = numeric_outputs.get(row.get("id"), {})
            num_parsed = num_out.get("parsed", {}) if isinstance(num_out, dict) else {}
            numeric_raw_spec = num_parsed.get("spec") if isinstance(num_parsed, dict) else None
            numeric_design_concept = num_parsed.get("design_concept") if isinstance(num_parsed, dict) else None
            raw_numeric_text = num_out.get("raw_text", "") if isinstance(num_out, dict) else ""

            canonical_spec = classic_output.get("spec", {})
            constraints = case.get("constraints") or {}
            patch_result = patch_numeric_spec(numeric_raw_spec, canonical_spec, contract, constraints)

            fallback_reason = determine_fallback(raw_numeric_text, numeric_raw_spec, patch_result)
            if fallback_reason:
                fallback_count += 1
                # fallback: classic-only spec
                patch_result_fallback = patch_numeric_spec(canonical_spec, canonical_spec, contract, constraints)
                patch_result = patch_result_fallback

        final_json = build_final_json(
            case_full, classic_status, classic_output,
            numeric_raw_spec, numeric_design_concept,
            patch_result, stage2_design_concept,
            generator_name, status_model_name, validator_name,
        )

        elapsed = time.time() - t0
        latencies.append(elapsed)

        results.append({
            "id": row.get("id"),
            "category": row.get("category"),
            "ship_type": case.get("ship_type", ""),
            "year": case.get("year", 0),
            "era": era_label_for(int(case.get("year", 0)), contract),
            "expected_status": row.get("expected_status"),
            "classic_status": classic_status,
            "stage2_predicted_status": stage2_parsed.get("status") if isinstance(stage2_parsed, dict) else None,
            "final_status": final_json["status"],
            "final_json": final_json,
            "numeric_called": numeric_called,
            "fallback_reason": fallback_reason,
            "patch_summary": {
                "exact_adoption": patch_result["exact_adoption"] if patch_result else None,
                "tolerant_adoption": patch_result["tolerant_adoption"] if patch_result else None,
                "major_patch_rate": patch_result["major_patch_rate"] if patch_result else None,
            } if patch_result else None,
            "assemble_latency_s": round(elapsed, 4),
            "expected_output": expected,
        })

    summary_extra = {
        "total": len(results),
        "numeric_call_count": numeric_call_count,
        "numeric_call_rate": round(numeric_call_count / len(results), 4) if results else 0,
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_count / len(results), 4) if results else 0,
        "assemble_latency_mean": round(sum(latencies) / len(latencies), 4) if latencies else 0,
    }
    return results, summary_extra


def evaluate_pipeline(results: list[dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    """end-to-end 評価。Section 25-26。"""
    total = len(results)
    if total == 0:
        return {"total": 0}

    schema_ok = sum(1 for r in results if isinstance(r["final_json"], dict) and r["final_json"].get("schema_version") == FINAL_SCHEMA_VERSION)
    status_exact = sum(1 for r in results if r["final_status"] == r["expected_status"])

    tp = sum(1 for r in results if r["expected_status"] == "impossible" and r["final_status"] == "impossible")
    fp = sum(1 for r in results if r["expected_status"] != "impossible" and r["final_status"] == "impossible")
    fn = sum(1 for r in results if r["expected_status"] == "impossible" and r["final_status"] != "impossible")
    impossible_precision = tp / (tp + fp) if (tp + fp) else 0.0
    impossible_recall = tp / (tp + fn) if (tp + fn) else 0.0
    impossible_f1 = (2 * impossible_precision * impossible_recall / (impossible_precision + impossible_recall)) if (impossible_precision + impossible_recall) else 0.0

    # reason semantic eval (classic確定値)
    reason_cases = [r for r in results if r["expected_status"] == "impossible"]
    reason_correct = 0
    reason_total = 0
    for r in reason_cases:
        exp = r.get("expected_output") or {}
        exp_codes = set()
        for item in (exp.get("reason") or []):
            if isinstance(item, dict):
                exp_codes.add(item.get("code"))
            elif isinstance(item, str):
                exp_codes.add(item)
        final_codes = set()
        for item in (r["final_json"].get("reason") or []):
            if isinstance(item, dict):
                final_codes.add(item.get("code"))
            elif isinstance(item, str):
                final_codes.add(item)
        if exp_codes or final_codes:
            reason_total += 1
            if exp_codes and exp_codes.issubset(final_codes):
                reason_correct += 1
    reason_accuracy = reason_correct / reason_total if reason_total else 0.0

    # numeric quality (ok cases only)
    ok_cases = [r for r in results if r["final_status"] == "ok" and r.get("patch_summary")]
    if ok_cases:
        tolerant = sum(r["patch_summary"]["tolerant_adoption"] for r in ok_cases) / len(ok_cases)
        exact = sum(r["patch_summary"]["exact_adoption"] for r in ok_cases) / len(ok_cases)
        major = sum(r["patch_summary"]["major_patch_rate"] for r in ok_cases) / len(ok_cases)
    else:
        tolerant = exact = major = 0.0

    numeric_call_count = sum(1 for r in results if r["numeric_called"])
    fallback_count = sum(1 for r in results if r["fallback_reason"])
    repetition = sum(1 for r in results if isinstance(r["final_json"].get("design_concept"), str) and repetition_collapse(r["final_json"].get("design_concept", "")))

    return {
        "total": total,
        "final_schema_rate": round(schema_ok / total, 4),
        "final_status_exact_rate": round(status_exact / total, 4),
        "final_impossible_precision": round(impossible_precision, 4),
        "final_impossible_recall": round(impossible_recall, 4),
        "final_impossible_f1": round(impossible_f1, 4),
        "final_reason_accuracy": round(reason_accuracy, 4),
        "numeric_tolerant_adoption": round(tolerant, 4),
        "numeric_exact_adoption": round(exact, 4),
        "numeric_major_patch": round(major, 4),
        "numeric_specialist_call_rate": round(numeric_call_count / total, 4),
        "fallback_rate": round(fallback_count / total, 4),
        "repetition_rate": round(repetition / total, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="navspec two-stage pipeline")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--stage2-adapter", type=Path, default=DEFAULT_STAGE2_ADAPTER)
    parser.add_argument("--numeric-adapter", type=Path, default=DEFAULT_NUMERIC_ADAPTER)
    parser.add_argument("--cases", type=Path, required=True, help="eval cases JSONL (full set with ok+impossible)")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--generator-name", default="stage3-v1-iter500")
    parser.add_argument("--status-model-name", default="stage2-iter1000")
    parser.add_argument("--validator-name", default="navspec-classic-v1")
    parser.add_argument("--json-report", type=Path, default=None)
    parser.add_argument("--md-report", type=Path, default=None)
    parser.add_argument("--stage2-cache", type=Path, default=None, help="reuse stage2 raw outputs JSONL")
    parser.add_argument("--numeric-cache", type=Path, default=None, help="reuse numeric raw outputs JSONL")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    rows = read_jsonl(args.cases)
    if args.limit is not None:
        rows = rows[: args.limit]

    slug = f"two_stage_pipeline_{DATE_STAMP}"
    if args.json_report is None:
        args.json_report = ROOT / "data" / "navspec" / "lora" / "eval" / slug / "report.json"
    if args.md_report is None:
        args.md_report = ROOT / "docs" / f"navspec_two_stage_pipeline_{DATE_STAMP}.md"

    if args.dry_run:
        print(f"cases: {len(rows)}")
        print(f"stage2 adapter: {args.stage2_adapter}")
        print(f"numeric adapter: {args.numeric_adapter}")
        print(f"contract: {args.contract}")
        return

    t_total = time.time()

    # Phase 1: Stage 2 on all cases
    if args.stage2_cache and args.stage2_cache.exists():
        print(f"loading stage2 cache: {args.stage2_cache}")
        stage2_outputs = [json.loads(l) for l in args.stage2_cache.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        print(f"Phase 1: Stage 2 inference on {len(rows)} cases...")
        stage2_outputs = run_stage2_phase(args.model, args.stage2_adapter, rows, schema, args.max_tokens)
        if args.stage2_cache:
            args.stage2_cache.parent.mkdir(parents=True, exist_ok=True)
            with args.stage2_cache.open("w", encoding="utf-8") as f:
                for o in stage2_outputs:
                    f.write(json.dumps(o, ensure_ascii=False) + "\n")

    # Phase 2: determine which cases need numeric specialist (classic=ok)
    print("Phase 2: classic validation to determine ok cases...")
    ok_case_ids = []
    for row, s2 in zip(rows, stage2_outputs):
        case = row.get("input") or row
        case_full = {"input": case, **{k: v for k, v in row.items() if k != "input"}}
        contradiction = classic_validate_status(case_full)
        if contradiction.get("status", "ok") == "ok":
            ok_case_ids.append(row.get("id"))

    ok_rows = [r for r in rows if r.get("id") in set(ok_case_ids)]
    print(f"  classic ok cases: {len(ok_rows)} / {len(rows)}")

    # Phase 3: numeric specialist on ok cases
    if args.numeric_cache and args.numeric_cache.exists():
        print(f"loading numeric cache: {args.numeric_cache}")
        numeric_outputs = {json.loads(l)["id"]: json.loads(l) for l in args.numeric_cache.read_text(encoding="utf-8").splitlines() if l.strip()}
    else:
        print(f"Phase 3: numeric specialist inference on {len(ok_rows)} ok cases...")
        numeric_outputs = run_numeric_phase(args.model, args.numeric_adapter, ok_rows, schema, args.max_tokens)
        if args.numeric_cache:
            args.numeric_cache.parent.mkdir(parents=True, exist_ok=True)
            with args.numeric_cache.open("w", encoding="utf-8") as f:
                for rid, o in numeric_outputs.items():
                    f.write(json.dumps({"id": rid, **o}, ensure_ascii=False) + "\n")

    # Phase 4: assemble pipeline
    print("Phase 4: assembling pipeline (classic patch + final JSON)...")
    results, extra = assemble_pipeline(
        rows, stage2_outputs, numeric_outputs, contract,
        args.generator_name, args.status_model_name, args.validator_name,
    )

    eval_summary = evaluate_pipeline(results, contract)
    eval_summary.update(extra)
    eval_summary["model"] = args.model
    eval_summary["stage2_adapter"] = str(args.stage2_adapter)
    eval_summary["numeric_adapter"] = str(args.numeric_adapter)
    eval_summary["date"] = DATE_STAMP
    eval_summary["cases"] = str(args.cases)
    eval_summary["elapsed_total_seconds"] = round(time.time() - t_total, 1)

    report = {"summary": eval_summary, "results": results}
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")

    print(json.dumps({k: v for k, v in eval_summary.items() if k != "results"}, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    print(f"json_report: {args.json_report}")


if __name__ == "__main__":
    main()
