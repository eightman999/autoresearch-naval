"""Evaluate Qwen2.5-0.5B-Instruct raw JSON output for navspec LoRA.

This evaluator measures the base/adapter model directly against
navspec.lora_candidate.v1. It intentionally does not invoke the classic
rule patcher, so schema and field adoption are visible.
"""

from __future__ import annotations

import argparse
import inspect
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

from mlx_lm_compat import patch_transformers_5_tokenizer_register  # noqa: E402
from navspec_lora_stage2_schema import (  # noqa: E402
    MAX_REASON_CODES,
    REASON_CODES,
    extract_reason_codes,
    normalize_reason_code_sequence,
    reason_code_metrics,
    validate_stage2,
)
from validate_navspec_output import validate_schema  # noqa: E402


DEFAULT_CASES = ROOT / "data" / "navspec" / "lora" / "raw_baseline_122_plus_500" / "cases.jsonl"
DEFAULT_SCHEMA = ROOT / "schemas" / "navspec_lora_candidate.schema.json"
DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
DATE_STAMP = date.today().isoformat()

# json-prefill モードを人間可読な評価モードに写す。
# free_raw       : prefill なし（モデルの素の JSON 生成力を測る）
# object_prefill : "{\n" だけ先入れ
# schema_assisted: schema_version 行まで先入れ（schema プライミング）
PREFILL_TO_MODE = {
    "none": "free_raw",
    "object": "object_prefill",
    "schema": "schema_assisted",
}


def eval_mode_for(prefill: str) -> str:
    return PREFILL_TO_MODE[prefill]


def run_slug_for(model: str, adapter_path: Path | None) -> str:
    base = "qwen2_5_0_5b"
    if adapter_path is not None:
        name = adapter_path.name or adapter_path.parent.name
        return f"{base}_{name}"
    return f"{base}_zero_shot"


def default_json_report(slug: str, eval_mode: str) -> Path:
    return ROOT / "data" / "navspec" / "lora" / "eval" / slug / eval_mode / "report.json"


def default_md_report(slug: str, eval_mode: str) -> Path:
    return ROOT / "docs" / f"{slug}_{eval_mode}_{DATE_STAMP}.md"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fallback_prompt(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "user")
        parts.append(f"<|im_start|>{role}\n{message.get('content', '')}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def prompt_from_row(tokenizer: Any, row: dict[str, Any]) -> str:
    messages = [
        message for message in row.get("messages", [])
        if message.get("role") in {"system", "user"}
    ]
    if not messages:
        messages = [
            {"role": "system", "content": "Return only JSON matching navspec.lora_candidate.v1."},
            {"role": "user", "content": compact(row["input"])},
        ]
    apply_chat_template = getattr(tokenizer, "apply_chat_template", None)
    if apply_chat_template:
        try:
            return apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        except TypeError:
            pass
    return fallback_prompt(messages)


def json_prefill(mode: str) -> str:
    if mode == "none":
        return ""
    if mode == "object":
        return "{\n"
    if mode == "schema":
        return '{\n  "schema_version": "navspec.lora_candidate.v1",\n'
    raise ValueError(f"unknown json prefill mode: {mode}")


def load_mlx_lm(model_name: str, adapter_path: Path | None) -> tuple[Any, Any, Any]:
    patch_transformers_5_tokenizer_register()
    try:
        from mlx_lm import generate, load
    except ImportError as exc:
        raise SystemExit(
            "mlx_lm is not installed. Install mlx-lm before running Qwen evaluation."
        ) from exc

    if adapter_path is not None:
        try:
            model, tokenizer = load(model_name, adapter_path=str(adapter_path))
            return model, tokenizer, generate
        except TypeError:
            model, tokenizer = load(model_name)
            return model, tokenizer, generate
    model, tokenizer = load(model_name)
    return model, tokenizer, generate


def generate_text(
    generate_fn: Any,
    model: Any,
    tokenizer: Any,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> str:
    kwargs: dict[str, Any] = {"prompt": prompt}
    sig = inspect.signature(generate_fn)
    if "max_tokens" in sig.parameters:
        kwargs["max_tokens"] = max_tokens
    if "max_new_tokens" in sig.parameters:
        kwargs["max_new_tokens"] = max_tokens
    if "temp" in sig.parameters:
        kwargs["temp"] = temperature
    elif "temperature" in sig.parameters:
        kwargs["temperature"] = temperature
    if "verbose" in sig.parameters:
        kwargs["verbose"] = False
    return str(generate_fn(model, tokenizer, **kwargs))


def generate_batch_texts(
    model: Any,
    tokenizer: Any,
    prompts: list[str],
    max_tokens: int,
    batch_size: int,
) -> list[str]:
    """temperature=0評価用のMLX連続バッチ生成。"""
    try:
        from mlx_lm import batch_generate
    except ImportError as exc:
        raise SystemExit("mlx_lm batch_generate is unavailable") from exc
    encoded = [tokenizer.encode(prompt) for prompt in prompts]
    response = batch_generate(
        model,
        tokenizer,
        encoded,
        max_tokens=max_tokens,
        completion_batch_size=batch_size,
        prefill_batch_size=min(batch_size, 8),
        verbose=False,
    )
    return [str(text) for text in response.texts]


def repetition_collapse(text: str) -> bool:
    words = text.split()
    if len(words) >= 12 and len(set(words[-12:])) <= 3:
        return True
    tail = text[-100:]
    return bool(tail and len(tail) >= 60 and len(set(tail)) <= 5)


def strip_fenced_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def root_json_valid(raw_text: str) -> bool:
    """生成テキスト全体が単一の完結した JSON オブジェクトか(切捨て検出用)。"""
    candidate = strip_fenced_json(raw_text)
    try:
        obj = json.loads(candidate)
        return isinstance(obj, dict)
    except json.JSONDecodeError:
        return False


def truncation_kind(raw_text: str) -> str:
    """root JSON が壊れている場合、どこで切れたかを分類する。"""
    text = strip_fenced_json(raw_text).rstrip()
    if not text:
        return "empty"
    if text.startswith("{") and not (text.endswith("}") or text.endswith("}}")):
        tail = text[-40:]
        if '"suggested_revision"' in tail or "rationale" in tail:
            return "suggested_revision_truncation"
        if '"reason"' in text and text.rfind('"reason"') > text.rfind('"spec"'):
            return "reason_array_truncation"
        return "missing_closing_brace"
    if "{" in text:
        return "parse_error_nested_only"
    return "parse_error"


def raw_reason_codes(obj: Any) -> tuple[list[str], int]:
    """reasonから有効codeとenum外/空code件数を取り出す。"""
    if not isinstance(obj, dict) or not isinstance(obj.get("reason"), list):
        return [], 0
    codes: list[str] = []
    invalid = 0
    for item in obj["reason"]:
        code = item.get("code") if isinstance(item, dict) else None
        if not isinstance(code, str) or not code or code not in REASON_CODES:
            invalid += 1
        else:
            codes.append(code)
    return codes, invalid


def primary_reason_for(category: Any, expected_codes: list[str]) -> str | None:
    """評価カテゴリごとの主原因。未知カテゴリはcanonical先頭を採用する。"""
    name = str(category or "")
    category_map = (
        ("speed_armor_range", "speed_armor_range_conflict"),
        ("three_code", "speed_armor_range_conflict"),
        ("speed_power", "speed_power_conflict"),
        ("range_volume", "range_volume_conflict"),
        ("small_gun", "main_gun_too_large_for_hull"),
        ("armor", "armor_too_heavy_for_hull"),
        ("era", "era_equipment_unavailable"),
        ("treaty", "treaty_limit_exceeded"),
        ("nation", "nation_capability_exceeded"),
        ("displacement", "displacement_limit_exceeded"),
        ("role", "ship_type_role_conflict"),
    )
    for token, code in category_map:
        if token in name and code in expected_codes:
            return code
    return expected_codes[0] if expected_codes else None


def json_damage_flags(raw_text: str, generation_token_count: int | None, max_tokens: int) -> dict[str, Any]:
    """root JSON破損を、終了上限と構造的な破損箇所に分けて記録する。"""
    root_valid = root_json_valid(raw_text)
    kind = "" if root_valid else truncation_kind(raw_text)
    length = bool(not root_valid and generation_token_count is not None and generation_token_count >= max_tokens)
    early_eos = bool(not root_valid and not length)
    return {
        "root_json_valid": root_valid,
        "generation_finish_reason": "length" if length else "stop",
        "length_truncation": length,
        "early_eos_invalid_json": early_eos,
        "root_parse_error": not root_valid,
        "missing_closing_brace": kind == "missing_closing_brace",
        "reason_array_truncation": kind == "reason_array_truncation",
        "suggested_revision_truncation": kind == "suggested_revision_truncation",
        "truncation_kind": kind,
    }


def extract_candidate_json(text: str, schema: dict[str, Any]) -> Any:
    """Extract the most schema-like JSON object from model output."""
    decoder = json.JSONDecoder()
    required = set(schema["required"])
    candidates: list[tuple[int, int, Any]] = []

    fenced = strip_fenced_json(text)
    for candidate_text in (fenced, text.strip()):
        try:
            obj = json.loads(candidate_text)
            if isinstance(obj, dict):
                score = sum(1 for key in required if key in obj)
                candidates.append((score, len(candidate_text), obj))
        except json.JSONDecodeError:
            pass

    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            score = sum(1 for key in required if key in obj)
            candidates.append((score, end, obj))

    if not candidates:
        raise ValueError("No JSON object found")
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def adopted(actual: Any, expected: Any) -> bool:
    av = numeric(actual)
    ev = numeric(expected)
    if av is not None and ev is not None:
        return abs(av - ev) <= max(0.2, abs(ev) * 0.02)
    return actual == expected


def spec_field_report(actual: Any, expected: Any, schema: dict[str, Any]) -> dict[str, Any]:
    required = schema["properties"]["spec"]["required"]
    if not isinstance(expected, dict):
        return {
            "spec_required_present_rate": 1.0 if actual is None else 0.0,
            "spec_field_adoption_rate": 1.0 if actual is None else 0.0,
            "spec_fields_present": 0,
            "spec_fields_adopted": 0,
            "spec_fields_total": 0,
        }
    if not isinstance(actual, dict):
        return {
            "spec_required_present_rate": 0.0,
            "spec_field_adoption_rate": 0.0,
            "spec_fields_present": 0,
            "spec_fields_adopted": 0,
            "spec_fields_total": len(required),
        }
    present = sum(1 for field in required if field in actual)
    adopted_count = sum(1 for field in required if field in actual and adopted(actual.get(field), expected.get(field)))
    return {
        "spec_required_present_rate": present / len(required),
        "spec_field_adoption_rate": adopted_count / len(required),
        "spec_fields_present": present,
        "spec_fields_adopted": adopted_count,
        "spec_fields_total": len(required),
    }


def evaluate_row(
    row: dict[str, Any],
    raw_text: str,
    schema: dict[str, Any],
    stage2: bool = False,
    generation_token_count: int | None = None,
    max_tokens: int = 512,
) -> dict[str, Any]:
    expected = row.get("expected_output") or row.get("output") or {}
    raw_obj: Any = {}
    raw_json_valid = False
    extract_error = None
    try:
        raw_obj = extract_candidate_json(raw_text, schema)
        raw_json_valid = isinstance(raw_obj, dict)
    except Exception as exc:
        extract_error = str(exc)

    if stage2:
        schema_errors = validate_stage2(raw_obj) if raw_json_valid else ["raw output is not a JSON object"]
    else:
        schema_errors = validate_schema(raw_obj, schema) if raw_json_valid else ["raw output is not a JSON object"]
    actual_status = raw_obj.get("status") if isinstance(raw_obj, dict) else None
    expected_status = expected.get("status")
    expected_impossible = expected_status == "impossible"
    actual_impossible = actual_status == "impossible"
    reason = raw_obj.get("reason") if isinstance(raw_obj, dict) else None
    suggested = raw_obj.get("suggested_revision") if isinstance(raw_obj, dict) else None
    if stage2:
        reason_revision_ok = bool(
            actual_impossible
            and isinstance(reason, list)
            and reason
            and all(isinstance(item, dict) and item.get("code") for item in reason)
            and isinstance(suggested, dict)
            and suggested
        )
    else:
        reason_revision_ok = bool(actual_impossible and isinstance(reason, list) and reason and isinstance(suggested, dict) and suggested)

    top_required = schema["required"]
    top_present = sum(1 for field in top_required if isinstance(raw_obj, dict) and field in raw_obj)
    spec_info = spec_field_report(
        raw_obj.get("spec") if isinstance(raw_obj, dict) else None,
        expected.get("spec"),
        schema,
    )

    result = {
        "id": row.get("id"),
        "category": row.get("category"),
        "raw_text_len": len(raw_text),
        "raw_text_preview": raw_text[:240],
        "raw_json_valid": raw_json_valid,
        "raw_extract_error": extract_error,
        "schema_complete": raw_json_valid and not schema_errors,
        "schema_errors": schema_errors,
        "top_required_present_rate": top_present / len(top_required),
        "expected_status": expected_status,
        "actual_status": actual_status,
        "status_exact": actual_status == expected_status,
        "impossible_tp": expected_impossible and actual_impossible,
        "impossible_fp": (not expected_impossible) and actual_impossible,
        "impossible_fn": expected_impossible and not actual_impossible,
        "reason_revision_ok": reason_revision_ok,
        "repetition_collapse": repetition_collapse(raw_text),
        **spec_info,
    }

    if stage2:
        expected_legacy = extract_reason_codes(expected)
        expected_strict = normalize_reason_code_sequence(expected_legacy)
        actual_raw, invalid_count = raw_reason_codes(raw_obj)
        actual_codes = set(actual_raw)
        duplicate_count = len(actual_raw) - len(actual_codes)
        actual_canonical = actual_raw == sorted(actual_raw, key=REASON_CODES.index) if not invalid_count else False
        strict_contract_ok = (
            invalid_count == 0
            and duplicate_count == 0
            and len(actual_raw) <= MAX_REASON_CODES
            and actual_canonical
        )
        legacy_metrics = reason_code_metrics(expected_legacy, actual_codes)
        semantic_metrics = reason_code_metrics(set(expected_strict), actual_codes)
        primary = primary_reason_for(row.get("category"), expected_strict)
        extra = actual_codes - set(expected_strict)
        missing = set(expected_strict) - actual_codes
        result["reason_code_expected_legacy"] = sorted(expected_legacy, key=REASON_CODES.index)
        result["reason_code_expected_strict"] = expected_strict
        result["reason_code_actual"] = sorted(actual_codes, key=REASON_CODES.index)
        result["legacy_reason_exact"] = legacy_metrics["reason_code_exact"]
        result["strict_reason_exact"] = strict_contract_ok and actual_raw == expected_strict
        result["semantic_reason_precision"] = semantic_metrics["reason_code_precision"]
        result["semantic_reason_recall"] = semantic_metrics["reason_code_recall"]
        result["semantic_reason_f1"] = semantic_metrics["reason_code_f1"]
        result["primary_reason_expected"] = primary
        result["primary_reason_correct"] = primary is not None and primary in actual_codes
        result["extra_reason_count"] = len(extra)
        result["missing_reason_count"] = len(missing)
        result["invalid_reason_count"] = invalid_count + duplicate_count
        # 旧report利用側との互換キー。新規比較にはstrict/semanticを使う。
        result["reason_code_expected"] = result["reason_code_expected_strict"]
        result["reason_code_exact"] = result["strict_reason_exact"]
        result["reason_code_precision"] = semantic_metrics["reason_code_precision"]
        result["reason_code_recall"] = semantic_metrics["reason_code_recall"]
        result["reason_code_f1"] = semantic_metrics["reason_code_f1"]
        result.update(json_damage_flags(raw_text, generation_token_count, max_tokens))
        result["truncated_json"] = not result["root_json_valid"]

    return result


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def summarize(results: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    total = len(results)
    if total == 0:
        return {"total": 0}

    def avg(key: str) -> float:
        return sum(float(row.get(key) or 0.0) for row in results) / total

    raw_json = sum(1 for row in results if row["raw_json_valid"])
    schema_complete = sum(1 for row in results if row["schema_complete"])
    status_exact = sum(1 for row in results if row["status_exact"])
    repetition = sum(1 for row in results if row["repetition_collapse"])
    tp = sum(1 for row in results if row["impossible_tp"])
    fp = sum(1 for row in results if row["impossible_fp"])
    fn = sum(1 for row in results if row["impossible_fn"])
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0

    summary = {
        "model": args.model,
        "adapter_path": str(args.adapter_path) if args.adapter_path else None,
        "run_slug": getattr(args, "run_slug", None),
        "eval_mode": getattr(args, "eval_mode", None),
        "json_prefill": args.json_prefill,
        "stage2": getattr(args, "stage2", False),
        "date": getattr(args, "date", DATE_STAMP),
        "cases": str(args.cases),
        "total": total,
        "raw_json_rate": raw_json / total,
        "schema_complete_rate": schema_complete / total,
        "status_exact_rate": status_exact / total,
        "top_required_present_rate_avg": avg("top_required_present_rate"),
        "spec_required_present_rate_avg": avg("spec_required_present_rate"),
        "spec_field_adoption_rate_avg": avg("spec_field_adoption_rate"),
        "impossible_precision": precision,
        "impossible_recall": recall,
        "impossible_f1": f1,
        "reason_revision_ok_rate": avg("reason_revision_ok"),
        "repetition_collapse_rate": repetition / total,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
    }

    if getattr(args, "stage2", False):
        legacy_exact = sum(1 for row in results if row.get("legacy_reason_exact"))
        strict_exact = sum(1 for row in results if row.get("strict_reason_exact"))
        summary["legacy_reason_exact_rate"] = legacy_exact / total
        summary["strict_reason_exact_rate"] = strict_exact / total
        summary["reason_code_exact_rate"] = summary["strict_reason_exact_rate"]
        summary["reason_code_precision_avg"] = avg("reason_code_precision")
        summary["reason_code_recall_avg"] = avg("reason_code_recall")
        summary["reason_code_f1_avg"] = avg("reason_code_f1")
        # micro P/R/F1: 全 impossible 案例のコードをプールして集計
        tp = fp = fn = 0
        for row in results:
            exp = set(row.get("reason_code_expected") or [])
            act = set(row.get("reason_code_actual") or [])
            tp += len(exp & act)
            fp += len(act - exp)
            fn += len(exp - act)
        micro_p = tp / (tp + fp) if (tp + fp) else 0.0
        micro_r = tp / (tp + fn) if (tp + fn) else 0.0
        micro_f1 = (2 * micro_p * micro_r / (micro_p + micro_r)) if (micro_p + micro_r) else 0.0
        summary["reason_code_micro_precision"] = micro_p
        summary["reason_code_micro_recall"] = micro_r
        summary["reason_code_micro_f1"] = micro_f1
        summary["semantic_reason_micro_precision"] = micro_p
        summary["semantic_reason_micro_recall"] = micro_r
        summary["semantic_reason_micro_f1"] = micro_f1
        primary_rows = [row for row in results if row.get("primary_reason_expected")]
        summary["primary_reason_accuracy"] = (
            sum(1 for row in primary_rows if row.get("primary_reason_correct")) / len(primary_rows)
            if primary_rows else 0.0
        )
        reason_rows = [row for row in results if row.get("reason_code_expected_strict")]
        denominator = len(reason_rows) or 1
        summary["extra_reason_rate"] = sum(1 for row in reason_rows if row.get("extra_reason_count", 0) > 0) / denominator
        summary["missing_reason_rate"] = sum(1 for row in reason_rows if row.get("missing_reason_count", 0) > 0) / denominator
        summary["invalid_reason_rate"] = sum(1 for row in reason_rows if row.get("invalid_reason_count", 0) > 0) / denominator
        # JSON 安定性
        truncated = sum(1 for row in results if row.get("truncated_json"))
        root_valid = sum(1 for row in results if row.get("root_json_valid"))
        summary["root_json_valid_rate"] = root_valid / total
        summary["truncated_json_rate"] = truncated / total
        summary["json_damage_counts"] = {
            key: sum(1 for row in results if row.get(key))
            for key in (
                "length_truncation",
                "early_eos_invalid_json",
                "root_parse_error",
                "missing_closing_brace",
                "reason_array_truncation",
                "suggested_revision_truncation",
            )
        }

    return summary


def write_markdown(path: Path, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    worst = sorted(
        results,
        key=lambda row: (
            row.get("schema_complete", False),
            row.get("spec_field_adoption_rate", 0.0),
            row.get("top_required_present_rate", 0.0),
        ),
    )[:10]
    lines = [
        "# Qwen2.5-0.5B-Instruct navspec eval",
        "",
        f"作成日: {summary.get('date', '')}",
        "",
        "## Summary",
        "",
        f"- model: `{summary['model']}`",
        f"- adapter_path: `{summary['adapter_path']}`" if summary.get("adapter_path") else f"- adapter_path: _(none, zero-shot)_",
        f"- run_slug: `{summary.get('run_slug')}`",
        f"- eval_mode: `{summary.get('eval_mode')}` (json_prefill={summary.get('json_prefill')})",
        f"- stage2: `{summary.get('stage2')}`",
        f"- total: {summary['total']}",
        f"- raw JSON成功率: {pct(summary['raw_json_rate'])}",
        f"- schema完全一致率: {pct(summary['schema_complete_rate'])}",
        f"- status一致率: {pct(summary['status_exact_rate'])}",
        f"- top必須フィールド率(avg): {pct(summary['top_required_present_rate_avg'])}",
        f"- spec必須フィールド率(avg): {pct(summary['spec_required_present_rate_avg'])}",
        f"- spec field adoption率(avg): {pct(summary['spec_field_adoption_rate_avg'])}",
        f"- impossible precision/recall/F1: {pct(summary['impossible_precision'])} / {pct(summary['impossible_recall'])} / {pct(summary['impossible_f1'])}",
        f"- reason+revision成立率(avg): {pct(summary['reason_revision_ok_rate'])}",
        f"- 反復崩壊率: {pct(summary['repetition_collapse_rate'])}",
    ]
    if summary.get("stage2"):
        lines.extend([
            f"- legacy reason exact: {pct(summary['legacy_reason_exact_rate'])}",
            f"- strict reason exact: {pct(summary['strict_reason_exact_rate'])}",
            f"- semantic reason micro P/R/F1: {pct(summary['semantic_reason_micro_precision'])} / {pct(summary['semantic_reason_micro_recall'])} / {pct(summary['semantic_reason_micro_f1'])}",
            f"- primary reason accuracy: {pct(summary['primary_reason_accuracy'])}",
            f"- extra/missing/invalid reason rate: {pct(summary['extra_reason_rate'])} / {pct(summary['missing_reason_rate'])} / {pct(summary['invalid_reason_rate'])}",
            f"- JSON damage: {summary['json_damage_counts']}",
        ])
    lines.extend([
        "",
        "## Worst Cases",
        "",
        "| id | category | raw_json | schema | status | spec_adoption | raw_len |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for row in worst:
        lines.append(
            "| {id} | {category} | {raw_json} | {schema} | {status} | {adoption} | {raw_len} |".format(
                id=row.get("id"),
                category=row.get("category"),
                raw_json="yes" if row.get("raw_json_valid") else "no",
                schema="yes" if row.get("schema_complete") else "no",
                status="yes" if row.get("status_exact") else "no",
                adoption=pct(float(row.get("spec_field_adoption_rate") or 0.0)),
                raw_len=row.get("raw_text_len"),
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Qwen raw navspec JSON")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--adapter-path", type=Path, default=None)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--batch-size", type=int, default=1, help="MLX batch_generate件数。temperature=0のみ")
    parser.add_argument("--json-prefill", choices=["none", "object", "schema"], default="schema")
    parser.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="未指定時は eval/{run_slug}/{eval_mode}/report.json になる",
    )
    parser.add_argument(
        "--md-report",
        type=Path,
        default=None,
        help="未指定時は docs/{run_slug}_{eval_mode}_{date}.md になる",
    )
    parser.add_argument("--raw-dir", type=Path, default=None)
    parser.add_argument("--stage2", action="store_true", help="Stage 2 schema/reason-code 評価 (v1 とは非互換)")
    parser.add_argument("--dry-run", action="store_true", help="load cases/schema only; do not load model")
    args = parser.parse_args()

    eval_mode = eval_mode_for(args.json_prefill)
    run_slug = run_slug_for(args.model, args.adapter_path)
    args.eval_mode = eval_mode
    args.run_slug = run_slug
    args.date = DATE_STAMP
    if args.json_report is None:
        args.json_report = default_json_report(run_slug, eval_mode)
    if args.md_report is None:
        args.md_report = default_md_report(run_slug, eval_mode)

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    rows = read_jsonl(args.cases)
    if args.limit is not None:
        rows = rows[: args.limit]

    if args.dry_run:
        print(f"model: {args.model}")
        print(f"adapter_path: {args.adapter_path}")
        print(f"run_slug: {run_slug}")
        print(f"eval_mode: {eval_mode} (json_prefill={args.json_prefill})")
        print(f"stage2: {args.stage2}")
        print(f"cases: {len(rows)}")
        print(f"schema: {args.schema}")
        print(f"json_report: {args.json_report}")
        print(f"md_report: {args.md_report}")
        print("dry_run: true")
        return

    model, tokenizer, generate_fn = load_mlx_lm(args.model, args.adapter_path)
    results: list[dict[str, Any]] = []
    raw_records: list[dict[str, Any]] = []
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    if args.batch_size > 1 and args.temperature != 0.0:
        raise SystemExit("--batch-size supports deterministic temperature=0 evaluation only")

    for chunk_start in range(0, len(rows), args.batch_size):
        chunk = rows[chunk_start:chunk_start + args.batch_size]
        prefill = json_prefill(args.json_prefill)
        prompts = [prompt_from_row(tokenizer, row) + prefill for row in chunk]
        started = time.time()
        if args.batch_size > 1:
            generated = generate_batch_texts(model, tokenizer, prompts, args.max_tokens, args.batch_size)
        else:
            generated = [generate_text(generate_fn, model, tokenizer, prompt, args.max_tokens, args.temperature) for prompt in prompts]
        elapsed_per_row = (time.time() - started) / len(chunk)
        for offset, (row, prompt, generated_text) in enumerate(zip(chunk, prompts, generated), start=1):
            raw_text = prefill + generated_text
            generation_token_count = len(tokenizer.encode(raw_text[len(prefill):]))
            result = evaluate_row(
                row,
                raw_text,
                schema,
                stage2=args.stage2,
                generation_token_count=generation_token_count,
                max_tokens=args.max_tokens,
            )
            result["elapsed_s"] = round(elapsed_per_row, 3)
            results.append(result)
            raw_records.append({"id": row.get("id"), "prompt": prompt, "raw_text": raw_text})
            index = chunk_start + offset
            print(
                "[{}/{}] {} raw_json={} schema={} status={} adoption={:.1%}".format(
                    index,
                    len(rows),
                    row.get("id"),
                    result["raw_json_valid"],
                    result["schema_complete"],
                    result["status_exact"],
                    result["spec_field_adoption_rate"],
                ),
                flush=True,
            )

    summary = summarize(results, args)
    report = {"summary": summary, "results": results}
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(args.md_report, summary, results)

    if args.raw_dir:
        args.raw_dir.mkdir(parents=True, exist_ok=True)
        for raw in raw_records:
            (args.raw_dir / f"{raw['id']}.txt").write_text(raw["raw_text"], encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"json_report: {args.json_report}")
    print(f"md_report: {args.md_report}")


if __name__ == "__main__":
    main()
