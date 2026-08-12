#!/usr/bin/env python3
"""Evaluate the compact Stage 4a armament verdict adapter."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from mlx_lm_compat import patch_transformers_5_tokenizer_register  # noqa: E402

patch_transformers_5_tokenizer_register()

from navspec_lora_data_armament_verdict_stage4a import (  # noqa: E402
    REASON_CODES,
    validate_verdict,
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def strip_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1] if "\n" in value else value
        if value.endswith("```"):
            value = value[:-3]
    return value.strip()


def first_json_object(text: str) -> Any:
    value = strip_fence(text)
    start = value.find("{")
    if start < 0:
        raise ValueError("no '{' in output")
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(value[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(value[start : index + 1])
    raise ValueError("unbalanced JSON object")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    from mlx_lm import load
    from mlx_lm.generate import generate
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = load(args.model, adapter_path=str(args.adapter))
    sampler = make_sampler(temp=0.0)
    rows = read_jsonl(args.data / f"{args.split}.jsonl")
    if args.limit > 0:
        rows = rows[: args.limit]

    stats = Counter()
    predicted_status_counts: Counter[str] = Counter()
    predicted_reason_counts: Counter[str] = Counter()
    predicted_unknown_reason_counts: Counter[str] = Counter()
    reason_tp = reason_fp = reason_fn = 0
    failures: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        messages = row["messages"]
        expected = json.loads(messages[-1]["content"])
        prompt = tokenizer.apply_chat_template(messages[:-1], add_generation_prompt=True, tokenize=False)
        generated = generate(
            model, tokenizer, prompt=prompt, max_tokens=args.max_tokens,
            sampler=sampler, verbose=False,
        )
        stats["total"] += 1
        try:
            got = first_json_object(generated)
        except Exception as exc:
            stats["json_invalid"] += 1
            if len(failures) < 10:
                failures.append({"index": index, "kind": "json_invalid", "error": str(exc), "raw": generated[:300]})
            continue
        stats["json_valid"] += 1
        problems = validate_verdict(got)
        if problems:
            stats["contract_violation"] += 1
            if len(failures) < 10:
                failures.append({"index": index, "kind": "contract", "problems": problems[:5]})
        else:
            stats["contract_ok"] += 1

        if isinstance(got.get("status"), str):
            predicted_status_counts[got["status"]] += 1
        if isinstance(got.get("reason_codes"), list):
            for code in got["reason_codes"]:
                if isinstance(code, str):
                    predicted_reason_counts[code] += 1
                    if code not in REASON_CODES:
                        predicted_unknown_reason_counts[code] += 1

        if got.get("status") == expected.get("status"):
            stats["status_match"] += 1
        expected_codes = set(expected.get("reason_codes") or [])
        got_codes = set(got.get("reason_codes") or []) if isinstance(got.get("reason_codes"), list) else set()
        reason_tp += len(expected_codes & got_codes)
        reason_fp += len(got_codes - expected_codes)
        reason_fn += len(expected_codes - got_codes)
        if expected_codes == got_codes:
            stats["reason_set_exact"] += 1
        if index % 20 == 0:
            print(f"  {index}/{len(rows)} ...", flush=True)

    total = stats["total"] or 1
    precision = reason_tp / (reason_tp + reason_fp) if reason_tp + reason_fp else None
    recall = reason_tp / (reason_tp + reason_fn) if reason_tp + reason_fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    report = {
        "model": args.model,
        "adapter": str(args.adapter),
        "data": str(args.data),
        "split": args.split,
        "n": stats["total"],
        "json_valid_rate": round(stats["json_valid"] / total, 4),
        "contract_ok_rate": round(stats["contract_ok"] / total, 4),
        "status_accuracy": round(stats["status_match"] / total, 4),
        "reason_set_exact_rate": round(stats["reason_set_exact"] / total, 4),
        "reason_code_precision": round(precision, 4) if precision is not None else None,
        "reason_code_recall": round(recall, 4) if recall is not None else None,
        "reason_code_f1": round(f1, 4) if f1 is not None else None,
        "counts": dict(stats),
        "predicted_status_counts": dict(sorted(predicted_status_counts.items())),
        "predicted_reason_code_counts": dict(sorted(predicted_reason_counts.items())),
        "predicted_unknown_reason_counts": dict(sorted(predicted_unknown_reason_counts.items())),
        "failure_samples": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"書き出し: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
