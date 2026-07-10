"""Wrapper for Qwen navspec Stage 1 LoRA training via mlx-lm.

Use this instead of `python -m mlx_lm.lora` in this repository so the local
transformers 5.x compatibility shim is applied before mlx-lm imports.

navspec schema-only SFT must train on assistant tokens only. mlx-lm's
``--mask-prompt`` is effective only for chat (``messages``) or completion
datasets; ``schema_only_v0`` uses ``messages``, so it qualifies. This wrapper
injects ``--mask-prompt`` when training a navspec schema-only dataset unless it
is already set, so the flag cannot be silently dropped. Set the environment
variable ``NAVSPEC_NO_MASK_PROMPT=1`` to opt out for ablation experiments.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from mlx_lm_compat import patch_transformers_5_tokenizer_register


def _arg_value(argv: list[str], flag: str) -> str | None:
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
    return None


def ensure_navspec_mask_prompt(argv: list[str]) -> list[str]:
    """Force ``--mask-prompt`` on for navspec schema-only SFT.

    Returns a possibly-extended argv list. Only acts when ``--train`` is set and
    ``--data`` points at a navspec schema_only directory.
    """
    if "--train" not in argv:
        return argv
    data_value = _arg_value(argv, "--data")
    if data_value is None:
        return argv
    normalized = str(data_value).replace("\\", "/")
    if "schema_only" not in normalized:
        return argv

    if "--mask-prompt" in argv:
        print("[navspec] --mask-prompt is set (response-only loss)", flush=True)
        return argv
    if os.environ.get("NAVSPEC_NO_MASK_PROMPT") == "1":
        print(
            "[navspec] WARNING: --mask-prompt NOT set "
            "(NAVSPEC_NO_MASK_PROMPT=1); loss covers the full sequence",
            flush=True,
        )
        return argv
    print(
        "[navspec] auto-enabling --mask-prompt for schema_only SFT "
        "(response-only loss; set NAVSPEC_NO_MASK_PROMPT=1 to opt out)",
        flush=True,
    )
    return argv + ["--mask-prompt"]


def main() -> None:
    patch_transformers_5_tokenizer_register()
    sys.argv = ensure_navspec_mask_prompt(list(sys.argv))
    from mlx_lm.lora import main as mlx_lora_main

    mlx_lora_main()


if __name__ == "__main__":
    main()
