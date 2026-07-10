"""Standalone verification of the navspec LoRA tokenizer contract.

実トークナイザ(Qwen/Qwen2.5-0.5B-Instruct)をロードして、以下を検証する:
  1. EOS / <|im_end|> / <|im_start|> の token ID 整合性
  2. chat template が system/user/assistant の境界 marker を正しく描画する
  3. mlx_lm ChatDataset の mask_prompt offset が応答のみを非マスクにする
  4. schema_only_v0 の実訓練行で、非マスク領域が assistant JSON から始まる

これらは mask_prompt 有効化(qwen_lora_train.py)と schema_only SFT の前提条件であり、
offset が崩れていると応答ロスではなくプロンプトロスを学習してしまう。

実行:
    uv run python tools/test_navspec_lora_tokenizer.py
    uv run python tools/test_navspec_lora_tokenizer.py --model Qwen/Qwen2.5-0.5B-Instruct

第2段階(pytest)移行方針:
    各 check_* は (ok, name, detail) を返す純粋関数。pytest を入れたら
    本モジュールを import して `assert check_*(tok)[0]` を並べるだけで
    test_*.py に移植できるよう、状態を持たせない構造にしてある。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from mlx_lm_compat import patch_transformers_5_tokenizer_register  # noqa: E402

DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
DEFAULT_TRAIN_ROW = ROOT / "data" / "navspec" / "lora" / "schema_only_v0" / "train.jsonl"
IM_START = "<|im_start|>"
IM_END = "<|im_end|>"
ENDOFTEXT = "<|endoftext|>"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_tuple(self) -> tuple[bool, str, str]:
        return self.ok, self.name, self.detail


def load_tokenizer(model: str) -> Any:
    patch_transformers_5_tokenizer_register()
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(model)


def check_special_token_ids(tok: Any) -> CheckResult:
    eos_id = tok.eos_token_id
    im_end_id = tok.convert_tokens_to_ids(IM_END)
    im_start_id = tok.convert_tokens_to_ids(IM_START)
    eot_id = tok.convert_tokens_to_ids(ENDOFTEXT)
    data = {
        "eos_token_id": eos_id,
        "im_end_id": im_end_id,
        "im_start_id": im_start_id,
        "endoftext_id": eot_id,
    }
    if eos_id is None:
        return CheckResult("special_token_ids", False, "eos_token_id is None", data)
    if eos_id != im_end_id:
        return CheckResult(
            "special_token_ids",
            False,
            f"eos_token_id({eos_id}) != <|im_end|> id({im_end_id}); "
            "Qwen2.5-Instruct must use <|im_end|> as EOS",
            data,
        )
    if im_start_id == im_end_id or im_start_id <= 0 or im_end_id <= 0:
        return CheckResult(
            "special_token_ids",
            False,
            f"invalid markers: im_start={im_start_id} im_end={im_end_id}",
            data,
        )
    return CheckResult("special_token_ids", True, "eos==im_end, markers distinct", data)


def check_eos_decodes_to_im_end(tok: Any) -> CheckResult:
    eos_id = tok.eos_token_id
    decoded = tok.decode([eos_id])
    if decoded == IM_END:
        return CheckResult("eos_decode", True, f"decode([{eos_id}])=='{IM_END}'")
    return CheckResult("eos_decode", False, f"decode([{eos_id}])={decoded!r} != '{IM_END}'")


def check_chat_template_structure(tok: Any) -> CheckResult:
    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
        {"role": "assistant", "content": "ASSISTANT"},
    ]
    text = tok.apply_chat_template(messages, tokenize=False, return_dict=False)
    expected_order = [
        "<|im_start|>system",
        "SYS",
        "<|im_end|>",
        "<|im_start|>user",
        "USER",
        "<|im_end|>",
        "<|im_start|>assistant",
        "ASSISTANT",
        "<|im_end|>",
    ]
    cursor = 0
    missing = []
    for marker in expected_order:
        pos = text.find(marker, cursor)
        if pos < 0:
            pos_anywhere = text.find(marker)
            if pos_anywhere < 0:
                missing.append(marker)
            else:
                missing.append(f"{marker}(out-of-order)")
        else:
            cursor = pos + len(marker)
    if missing:
        return CheckResult(
            "chat_template_structure",
            False,
            f"markers missing/out-of-order: {missing}",
            {"rendered": text},
        )
    return CheckResult("chat_template_structure", True, "all markers in order", {"rendered": text})


def check_mask_prompt_offset(tok: Any) -> CheckResult:
    from mlx_lm.tuner.datasets import ChatDataset

    messages = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
        {"role": "assistant", "content": "RESPONSE_BODY"},
    ]
    row = {"messages": messages}
    ds_masked = ChatDataset([row], tok, chat_key="messages", mask_prompt=True)
    ds_full = ChatDataset([row], tok, chat_key="messages", mask_prompt=False)
    tokens, offset = ds_masked.process(row)
    _, offset_full = ds_full.process(row)
    prefix = tok.apply_chat_template(messages[:-1], add_generation_prompt=True, return_dict=False)
    unmasked = tokens[offset:]
    unmasked_text = tok.decode(unmasked)
    data = {
        "tokens_len": len(tokens),
        "offset_masked": offset,
        "offset_full": offset_full,
        "expected_offset": len(prefix),
        "unmasked_text": unmasked_text,
    }
    if offset_full != 0:
        return CheckResult("mask_prompt_offset", False, "mask_prompt=False should yield offset 0", data)
    if offset <= 0:
        return CheckResult("mask_prompt_offset", False, "mask_prompt=True yielded non-positive offset", data)
    if offset != len(prefix):
        return CheckResult(
            "mask_prompt_offset",
            False,
            f"offset({offset}) != len(prefix)({len(prefix)}); mlx-lm offset is inconsistent",
            data,
        )
    if not unmasked_text.startswith("RESPONSE_BODY"):
        return CheckResult(
            "mask_prompt_offset",
            False,
            f"unmasked region does not start with assistant body: {unmasked_text!r}",
            data,
        )
    if tok.eos_token_id not in unmasked:
        return CheckResult(
            "mask_prompt_offset",
            False,
            f"EOS id {tok.eos_token_id} not in unmasked region; response terminator not trained",
            data,
        )
    return CheckResult(
        "mask_prompt_offset",
        True,
        f"offset={offset}, unmasked='RESPONSE_BODY...<|im_end|>'",
        data,
    )


def check_schema_only_train_row(tok: Any, train_row_path: Path) -> CheckResult:
    import json

    if not train_row_path.exists():
        return CheckResult(
            "schema_only_train_row",
            False,
            f"train row not found: {train_row_path}",
        )
    with train_row_path.open("r", encoding="utf-8") as handle:
        row = json.loads(handle.readline())
    messages = row.get("messages")
    if not messages or messages[-1].get("role") != "assistant":
        return CheckResult(
            "schema_only_train_row",
            False,
            "schema_only row must end with an assistant message",
        )
    from mlx_lm.tuner.datasets import ChatDataset

    ds = ChatDataset([row], tok, chat_key="messages", mask_prompt=True)
    tokens, offset = ds.process(row)
    unmasked_text = tok.decode(tokens[offset:])
    assistant_text = messages[-1].get("content", "")
    first_key = '"schema_version"'
    data = {
        "id": row.get("id"),
        "tokens_len": len(tokens),
        "offset": offset,
        "unmasked_head": unmasked_text[:80],
        "assistant_head": assistant_text[:80],
    }
    if not assistant_text.startswith("{"):
        return CheckResult("schema_only_train_row", False, "assistant content is not JSON", data)
    if offset <= 0:
        return CheckResult("schema_only_train_row", False, "offset non-positive; prompt not masked", data)
    if not unmasked_text.lstrip().startswith("{"):
        return CheckResult(
            "schema_only_train_row",
            False,
            f"unmasked region does not start with assistant JSON: {unmasked_text[:80]!r}",
            data,
        )
    if first_key not in unmasked_text:
        return CheckResult(
            "schema_only_train_row",
            False,
            f"assistant JSON missing {first_key} in unmasked region",
            data,
        )
    if tok.eos_token_id not in tokens[offset:]:
        return CheckResult(
            "schema_only_train_row",
            False,
            "EOS id missing from unmasked region of real training row",
            data,
        )
    return CheckResult(
        "schema_only_train_row",
        True,
        f"id={row.get('id')} offset={offset} unmasked starts with JSON",
        data,
    )


CHECKS: list[Callable[[Any], CheckResult]] = [
    check_special_token_ids,
    check_eos_decodes_to_im_end,
    check_chat_template_structure,
    check_mask_prompt_offset,
]


def run_all(model: str, train_row_path: Path) -> list[CheckResult]:
    tok = load_tokenizer(model)
    results = [check(tok) for check in CHECKS]
    results.append(check_schema_only_train_row(tok, train_row_path))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify navspec LoRA tokenizer chat template / EOS / mask_prompt contract")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--train-row", type=Path, default=DEFAULT_TRAIN_ROW)
    args = parser.parse_args()

    results = run_all(args.model, args.train_row)
    width = max(len(r.name) for r in results)
    failed = 0
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        if not result.ok:
            failed += 1
        print(f"[{status}] {result.name.ljust(width)}  {result.detail}")
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    if failed:
        print(f"{failed} check(s) FAILED")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
