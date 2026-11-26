#!/usr/bin/env python3
"""Evaluate the Stage 4a armament verdict task against a llama-server (GGUF) endpoint.

``navspec_armament_verdict_eval_hf.py`` の ``score_generations`` をそのまま import して
採点するので、PyTorch 版・mlx 版と同じ指標（json_valid / contract_ok / status_accuracy /
reason_code_f1 / reason_set_exact など）になる。標準ライブラリのみで動く（torch 不要）。

llama-server は事前に起動しておく（例）::

    CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<3060 UUID> \
    llama-server -m gguf/navspec-qwen35-0.8b-q8_0.gguf -ngl 99 -c 4096 \
        --host 127.0.0.1 --port 18765 --jinja \
        --chat-template-kwargs '{"enable_thinking":false}'

実行例::

    python tools/navspec_armament_verdict_eval_gguf.py \
        --base-url http://127.0.0.1:18765 --data data/stage4a_armament_verdict_v2_1 \
        --split test --label q8_0 --out out/gguf_q8_0_test.json \
        --compare out/r2_merged_test_gens.json

- chat completions, temperature=0, max_tokens=64, cache_prompt=false, 1 件ずつ逐次実行。
- リクエストにも ``chat_template_kwargs={"enable_thinking": false}`` を付ける。
- 最初に ``/apply-template`` で整形済みプロンプト末尾が
  ``<|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n`` であることを検証する。
- --compare に PyTorch 側の生成文字列 JSON（``generations`` キーのリスト）を渡すと、
  status 一致率と出力文字列の完全一致率を出す。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from navspec_armament_verdict_eval import first_json_object, read_jsonl  # noqa: E402
from navspec_armament_verdict_eval_hf import score_generations  # noqa: E402

EXPECTED_SUFFIX = "<|im_start|>assistant\n<think>\n\n</think>\n\n"


def post_json(url: str, payload: dict[str, Any], timeout: float = 300.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (loopback only)
        return json.loads(resp.read().decode("utf-8"))


def get_json(url: str, timeout: float = 30.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def check_template(base_url: str, messages: list[dict[str, str]]) -> tuple[bool, str]:
    """/apply-template で整形したプロンプトの末尾が thinking 無効の形か確認する。"""
    try:
        out = post_json(
            f"{base_url}/apply-template",
            {"messages": messages, "chat_template_kwargs": {"enable_thinking": False}},
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"apply-template failed: {exc}"
    prompt = out.get("prompt", "")
    return prompt.endswith(EXPECTED_SUFFIX), prompt


def status_of(text: str) -> str | None:
    try:
        got = first_json_object(text)
    except Exception:  # noqa: BLE001
        return None
    status = got.get("status") if isinstance(got, dict) else None
    return status if isinstance(status, str) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:18765")
    parser.add_argument("--model-name", default="navspec")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--label", default="gguf")
    parser.add_argument("--compare", type=Path, default=None, help="PyTorch 側の generations を含む JSON")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    rows = read_jsonl(args.data / f"{args.split}.jsonl")
    if args.limit > 0:
        rows = rows[: args.limit]

    template_ok, prompt = check_template(base, rows[0]["messages"][:-1])
    print(f"template check: {'OK' if template_ok else 'NG'} tail={prompt[-60:]!r}", flush=True)

    generations: list[str] = []
    finish_reasons: dict[str, int] = {}
    prompt_tps: list[float] = []
    decode_tps: list[float] = []
    prompt_tokens = completion_tokens = 0
    t0 = time.time()
    for i, row in enumerate(rows, 1):
        resp = post_json(
            f"{base}/v1/chat/completions",
            {
                "model": args.model_name,
                "messages": row["messages"][:-1],
                "temperature": 0,
                "max_tokens": args.max_tokens,
                "cache_prompt": False,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        choice = resp["choices"][0]
        generations.append(choice["message"].get("content") or "")
        fr = choice.get("finish_reason") or "?"
        finish_reasons[fr] = finish_reasons.get(fr, 0) + 1
        usage = resp.get("usage", {})
        prompt_tokens += usage.get("prompt_tokens", 0)
        completion_tokens += usage.get("completion_tokens", 0)
        timings = resp.get("timings") or {}
        if timings.get("prompt_per_second"):
            prompt_tps.append(timings["prompt_per_second"])
        if timings.get("predicted_per_second"):
            decode_tps.append(timings["predicted_per_second"])
        if i % 20 == 0 or i == len(rows):
            print(f"  {i}/{len(rows)} ...", flush=True)
    elapsed = time.time() - t0

    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 1) if xs else None

    report: dict[str, Any] = {
        "label": args.label,
        "backend": "llama-server",
        "base_url": base,
        "data": str(args.data),
        "split": args.split,
        "max_tokens": args.max_tokens,
        "template_check_ok": template_ok,
        "template_tail": prompt[-80:],
        **score_generations(rows, generations),
        "finish_reasons": finish_reasons,
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        "speed": {
            "mean_prompt_tokens_per_s": mean(prompt_tps),
            "mean_decode_tokens_per_s": mean(decode_tps),
            "wall_seconds": round(elapsed, 2),
            "note": "server timings の逐次リクエスト平均（prompt 約 %d tok/件）" % (prompt_tokens // max(len(rows), 1)),
        },
        "generations": generations,
    }

    if args.compare is not None:
        ref = json.loads(args.compare.read_text(encoding="utf-8"))["generations"]
        n = min(len(ref), len(generations))
        exact = sum(1 for a, b in zip(generations[:n], ref[:n]) if a == b)
        status_same = sum(1 for a, b in zip(generations[:n], ref[:n]) if status_of(a) == status_of(b))
        report["vs_reference"] = {
            "reference": str(args.compare),
            "n": n,
            "status_agreement": round(status_same / n, 4) if n else None,
            "exact_string_match": round(exact / n, 4) if n else None,
            "exact_string_match_count": exact,
            "diff_indices": [i + 1 for i, (a, b) in enumerate(zip(generations[:n], ref[:n])) if a != b][:30],
        }

    summary_keys = (
        "json_valid_rate", "contract_ok_rate", "status_accuracy",
        "reason_code_f1", "reason_set_exact_rate",
    )
    print(json.dumps({k: report[k] for k in summary_keys}, ensure_ascii=False))
    if "vs_reference" in report:
        print(json.dumps(report["vs_reference"], ensure_ascii=False))
    print(json.dumps(report["speed"], ensure_ascii=False))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"書き出し: {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
