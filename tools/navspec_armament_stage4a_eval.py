#!/usr/bin/env python3
"""Stage 4a armament LoRA の評価。

「学習が終わった」は完了ではない。実際に生成させて、

  1. JSON として読めるか
  2. 出力契約 (`navspec.armament_teacher.v1`) を満たすか
  3. status (ok / impossible) が teacher と一致するか
  4. reason code の集合が teacher と一致するか

までを測る。teacher は classic ルールエンジンなので、これは
「ルールエンジンの判定をどれだけ再現できたか」の測定になる。

使用法::

    uv run python tools/navspec_armament_stage4a_eval.py \\
        --adapter models/navspec-lora-0.5b-v0.1/adapter/stage4a-armament-r8 \\
        --data data/navspec/lora/stage4a_armament_combined_v0 \\
        --limit 120 --out docs/navspec_armament_stage4a_eval.json
"""

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

from navspec_lora_data_armament_stage4a import validate_teacher  # noqa: E402
from navspec_armament_validator import ARMAMENT_REASON_CODES  # noqa: E402


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()


def first_json_object(text: str) -> Any:
    """先頭の JSON オブジェクトを取り出す。余計な前後があっても拾う。"""
    t = strip_fence(text)
    start = t.find("{")
    if start < 0:
        raise ValueError("no '{' in output")
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(t[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start : i + 1])
    raise ValueError("unbalanced JSON object")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    p.add_argument("--adapter", type=Path)
    p.add_argument("--data", type=Path,
                   default=ROOT / "data" / "navspec" / "lora" / "stage4a_armament_combined_v0")
    p.add_argument("--split", default="test")
    p.add_argument("--limit", type=int, default=120)
    p.add_argument("--max-tokens", type=int, default=768)
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)

    from mlx_lm import load
    from mlx_lm.generate import generate
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = (
        load(args.model, adapter_path=str(args.adapter)) if args.adapter
        else load(args.model)
    )
    sampler = make_sampler(temp=0.0)

    rows = read_jsonl(args.data / f"{args.split}.jsonl")[: args.limit]
    stats = Counter()
    reason_tp = reason_fp = reason_fn = 0
    warning_tp = warning_fp = warning_fn = 0
    predicted_status_counts: Counter[str] = Counter()
    predicted_reason_counts: Counter[str] = Counter()
    predicted_warning_counts: Counter[str] = Counter()
    predicted_unknown_reason_counts: Counter[str] = Counter()
    predicted_unknown_warning_counts: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []

    for i, row in enumerate(rows, 1):
        messages = row["messages"]
        expected = json.loads(messages[-1]["content"])
        prompt = tokenizer.apply_chat_template(
            messages[:-1], add_generation_prompt=True, tokenize=False
        )
        text = generate(model, tokenizer, prompt=prompt,
                        max_tokens=args.max_tokens, sampler=sampler, verbose=False)
        stats["total"] += 1
        try:
            got = first_json_object(text)
        except Exception as e:
            stats["json_invalid"] += 1
            if len(failures) < 10:
                failures.append({"index": i, "kind": "json_invalid",
                                 "error": str(e), "raw": text[:300]})
            continue
        stats["json_valid"] += 1

        problems = validate_teacher(got)
        if problems:
            stats["contract_violation"] += 1
            if len(failures) < 10:
                failures.append({"index": i, "kind": "contract",
                                 "problems": problems[:5]})
        else:
            stats["contract_ok"] += 1

        if isinstance(got.get("status"), str):
            predicted_status_counts[got["status"]] += 1
        if got.get("status") == expected["status"]:
            stats["status_match"] += 1
        exp_codes = {r["code"] for r in expected.get("reason") or []}
        got_codes = {r.get("code") for r in (got.get("reason") or []) if isinstance(r, dict)}
        exp_warning_codes = {r["code"] for r in expected.get("warnings") or []}
        got_warning_codes = {
            r.get("code") for r in (got.get("warnings") or []) if isinstance(r, dict)
        }
        for code in got_codes:
            if isinstance(code, str):
                predicted_reason_counts[code] += 1
                if code not in ARMAMENT_REASON_CODES:
                    predicted_unknown_reason_counts[code] += 1
        for code in got_warning_codes:
            if isinstance(code, str):
                predicted_warning_counts[code] += 1
                if code not in ARMAMENT_REASON_CODES:
                    predicted_unknown_warning_counts[code] += 1
        reason_tp += len(exp_codes & got_codes)
        reason_fp += len(got_codes - exp_codes)
        reason_fn += len(exp_codes - got_codes)
        warning_tp += len(exp_warning_codes & got_warning_codes)
        warning_fp += len(got_warning_codes - exp_warning_codes)
        warning_fn += len(exp_warning_codes - got_warning_codes)
        if exp_codes == got_codes:
            stats["reason_set_exact"] += 1
        if exp_warning_codes == got_warning_codes:
            stats["warning_set_exact"] += 1

        if i % 20 == 0:
            print(f"  {i}/{len(rows)} ...", file=sys.stderr)

    total = stats["total"] or 1
    precision = reason_tp / (reason_tp + reason_fp) if (reason_tp + reason_fp) else None
    recall = reason_tp / (reason_tp + reason_fn) if (reason_tp + reason_fn) else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)
    warning_precision = (
        warning_tp / (warning_tp + warning_fp)
        if warning_tp + warning_fp else None
    )
    warning_recall = (
        warning_tp / (warning_tp + warning_fn)
        if warning_tp + warning_fn else None
    )
    warning_f1 = (
        2 * warning_precision * warning_recall / (warning_precision + warning_recall)
        if warning_precision and warning_recall else None
    )

    report = {
        "model": args.model,
        "adapter": str(args.adapter) if args.adapter else None,
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
        "warning_set_exact_rate": round(stats["warning_set_exact"] / total, 4),
        "warning_code_precision": round(warning_precision, 4) if warning_precision is not None else None,
        "warning_code_recall": round(warning_recall, 4) if warning_recall is not None else None,
        "warning_code_f1": round(warning_f1, 4) if warning_f1 is not None else None,
        "counts": dict(stats),
        "predicted_status_counts": dict(sorted(predicted_status_counts.items())),
        "predicted_reason_code_counts": dict(sorted(predicted_reason_counts.items())),
        "predicted_warning_code_counts": dict(sorted(predicted_warning_counts.items())),
        "predicted_unknown_reason_counts": dict(sorted(predicted_unknown_reason_counts.items())),
        "predicted_unknown_warning_counts": dict(sorted(predicted_unknown_warning_counts.items())),
        "failure_samples": failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"書き出し: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
