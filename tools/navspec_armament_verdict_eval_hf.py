#!/usr/bin/env python3
"""Evaluate the Stage 4a armament verdict task with PyTorch / transformers + PEFT.

``tools/navspec_armament_verdict_eval.py`` (mlx_lm 専用) と同じ採点ロジック
（JSON 抽出・契約検証・status/reason 集計・出力キー）を再利用する HF 版。
追加指標: status 混同行列、クラス別 recall、reason code 別 precision/recall/support、
定義外 reason code 件数。

--adapter を省略するとゼロショット評価になる。greedy、enable_thinking=False、
左パディングのバッチ生成。

使用例::

    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<3060 UUID> \
    python tools/navspec_armament_verdict_eval_hf.py \
        --model Qwen/Qwen3.5-0.8B --data data/stage4a_armament_verdict_v2 \
        --split test --batch-size 8 --out out/zeroshot_test.json
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

# mlx 版は import 時に mlx を要求しない（mlx_lm は main() 内で遅延 import）。
# 採点の純関数と契約検証をそのまま再利用して挙動差を避ける。
from navspec_armament_verdict_eval import first_json_object, read_jsonl  # noqa: E402
from navspec_lora_data_armament_verdict_stage4a import (  # noqa: E402
    REASON_CODES,
    validate_verdict,
)

STATUSES = ("ok", "impossible")
INVALID = "__invalid__"
MISSING = "__missing__"


def score_generations(
    rows: list[dict[str, Any]], generations: list[str]
) -> dict[str, Any]:
    """mlx 版 main() の採点ループと同じ集計 + 追加指標を返す。"""
    stats: Counter[str] = Counter()
    predicted_status_counts: Counter[str] = Counter()
    predicted_reason_counts: Counter[str] = Counter()
    predicted_unknown_reason_counts: Counter[str] = Counter()
    reason_tp = reason_fp = reason_fn = 0
    failures: list[dict[str, Any]] = []

    # 追加指標用（json 不正の行は「空集合を予測」として数える）
    confusion: dict[str, Counter[str]] = {}
    code_tp: Counter[str] = Counter()
    code_fp: Counter[str] = Counter()
    code_fn: Counter[str] = Counter()
    code_support: Counter[str] = Counter()

    for index, (row, generated) in enumerate(zip(rows, generations), 1):
        expected = json.loads(row["messages"][-1]["content"])
        exp_status = expected.get("status")
        exp_codes = set(expected.get("reason_codes") or [])
        stats["total"] += 1
        for code in exp_codes:
            code_support[code] += 1
        try:
            got = first_json_object(generated)
        except Exception as exc:
            stats["json_invalid"] += 1
            if len(failures) < 10:
                failures.append({"index": index, "kind": "json_invalid", "error": str(exc), "raw": generated[:300]})
            confusion.setdefault(str(exp_status), Counter())[INVALID] += 1
            for code in exp_codes:
                code_fn[code] += 1
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

        got_status = got.get("status")
        confusion.setdefault(str(exp_status), Counter())[
            got_status if isinstance(got_status, str) else MISSING
        ] += 1
        if got_status == exp_status:
            stats["status_match"] += 1
        got_codes = set(got.get("reason_codes") or []) if isinstance(got.get("reason_codes"), list) else set()
        # 定義外コードは unhashable ではないが念のため文字列のみを per-code 集計に使う
        reason_tp += len(exp_codes & got_codes)
        reason_fp += len(got_codes - exp_codes)
        reason_fn += len(exp_codes - got_codes)
        for code in exp_codes & got_codes:
            code_tp[code] += 1
        for code in got_codes - exp_codes:
            code_fp[code] += 1
        for code in exp_codes - got_codes:
            code_fn[code] += 1
        if exp_codes == got_codes:
            stats["reason_set_exact"] += 1

    total = stats["total"] or 1
    precision = reason_tp / (reason_tp + reason_fp) if reason_tp + reason_fp else None
    recall = reason_tp / (reason_tp + reason_fn) if reason_tp + reason_fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None

    # status 混同行列とクラス別 recall
    pred_labels = sorted({p for row in confusion.values() for p in row} | set(STATUSES))
    matrix = {
        exp: {pred: confusion.get(exp, Counter()).get(pred, 0) for pred in pred_labels}
        for exp in sorted(set(confusion) | set(STATUSES))
    }
    status_recall: dict[str, Any] = {}
    for exp, preds in matrix.items():
        support = sum(preds.values())
        status_recall[exp] = {
            "support": support,
            "recall": round(preds.get(exp, 0) / support, 4) if support else None,
        }

    def _r(x: float | None) -> float | None:
        return round(x, 4) if x is not None else None

    all_codes = sorted(set(code_support) | set(code_tp) | set(code_fp) | set(code_fn))
    per_code: dict[str, Any] = {}
    for code in all_codes:
        tp, fp, fn = code_tp[code], code_fp[code], code_fn[code]
        per_code[code] = {
            "support": code_support[code],
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": _r(tp / (tp + fp)) if tp + fp else None,
            "recall": _r(tp / (tp + fn)) if tp + fn else None,
            "defined": code in REASON_CODES,
        }

    return {
        "n": stats["total"],
        "json_valid_rate": round(stats["json_valid"] / total, 4),
        "contract_ok_rate": round(stats["contract_ok"] / total, 4),
        "status_accuracy": round(stats["status_match"] / total, 4),
        "reason_set_exact_rate": round(stats["reason_set_exact"] / total, 4),
        "reason_code_precision": _r(precision),
        "reason_code_recall": _r(recall),
        "reason_code_f1": _r(f1),
        "counts": dict(stats),
        "predicted_status_counts": dict(sorted(predicted_status_counts.items())),
        "predicted_reason_code_counts": dict(sorted(predicted_reason_counts.items())),
        "predicted_unknown_reason_counts": dict(sorted(predicted_unknown_reason_counts.items())),
        "unknown_reason_code_total": sum(predicted_unknown_reason_counts.values()),
        "status_confusion": matrix,
        "status_recall_by_class": status_recall,
        "reason_code_metrics": per_code,
        "reason_code_metrics_note": "json_invalid の行は空集合を予測したものとして fn に数える（全体 precision/recall は mlx 版互換で除外）",
        "failure_samples": failures,
    }


def build_prompts(tokenizer: Any, rows: list[dict[str, Any]]) -> list[str]:
    prompts = []
    for row in rows:
        # Qwen3.5 は enable_thinking=False で空 <think> ブロックを付ける。
        # Qwen2.5 の template は enable_thinking を参照しないので無害に無視される。
        prompts.append(
            tokenizer.apply_chat_template(
                row["messages"][:-1],
                add_generation_prompt=True,
                tokenize=False,
                enable_thinking=False,
            )
        )
    return prompts


def generate_all(
    model: Any, tokenizer: Any, prompts: list[str], max_tokens: int, batch_size: int
) -> list[str]:
    import torch

    device = next(model.parameters()).device
    eos_ids = {tokenizer.eos_token_id}
    im_end = tokenizer.convert_tokens_to_ids("<|im_end|>")
    if isinstance(im_end, int) and im_end >= 0:
        eos_ids.add(im_end)
    eos_ids = sorted(i for i in eos_ids if i is not None)
    # 長さ順に並べてパディングを減らし、結果は元の順に戻す
    lengths = [len(tokenizer(p, add_special_tokens=False)["input_ids"]) for p in prompts]
    order = sorted(range(len(prompts)), key=lambda i: lengths[i])
    outputs: list[str] = [""] * len(prompts)
    done = 0
    for start in range(0, len(order), batch_size):
        idx = order[start : start + batch_size]
        enc = tokenizer(
            [prompts[i] for i in idx], return_tensors="pt", padding=True, add_special_tokens=False
        ).to(device)
        with torch.no_grad():
            gen = model.generate(
                **enc,
                max_new_tokens=max_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
                eos_token_id=eos_ids,
                pad_token_id=tokenizer.pad_token_id,
            )
        new_tokens = gen[:, enc["input_ids"].shape[1] :]
        for i, seq in zip(idx, new_tokens):
            outputs[i] = tokenizer.decode(seq, skip_special_tokens=True)
        done += len(idx)
        print(f"  {done}/{len(prompts)} ...", flush=True)
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="Qwen/Qwen3.5-0.8B")
    parser.add_argument("--adapter", type=Path, default=None, help="PEFT ディレクトリ。省略でゼロショット")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--merge", action="store_true", help="アダプタをマージしてから生成（既定は非マージ）")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, padding_side="left")
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.bfloat16)
    if args.adapter is not None:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(args.adapter))
        if args.merge:
            model = model.merge_and_unload()
    model = model.cuda().eval()

    rows = read_jsonl(args.data / f"{args.split}.jsonl")
    if args.limit > 0:
        rows = rows[: args.limit]
    prompts = build_prompts(tokenizer, rows)
    generations = generate_all(model, tokenizer, prompts, args.max_tokens, args.batch_size)

    report = {
        "model": args.model,
        "adapter": str(args.adapter) if args.adapter is not None else None,
        "data": str(args.data),
        "split": args.split,
        "backend": "hf",
        "batch_size": args.batch_size,
        "max_tokens": args.max_tokens,
        **score_generations(rows, generations),
        "generation_samples": [
            {"index": i + 1, "raw": generations[i][:300]} for i in range(min(5, len(generations)))
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"書き出し: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
