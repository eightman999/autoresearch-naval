#!/usr/bin/env python3
"""mlx_lm の LoRA アダプタ (adapters.safetensors) を PEFT 形式へ変換する。

mlx: lora_a (in, r), lora_b (r, out)  ->  PEFT: lora_A.weight (r, in), lora_B.weight (out, r)
必ず転置（reshape ではない）。adapter_config.json は既存の PEFT 設定をそのままコピーする。

    python tools/navspec_mlx_lora_to_peft.py \
        --mlx-adapter hf_upload/navspec-lora-0.5b-v0.1/adapters.safetensors \
        --peft-config hf_upload/navspec-lora-0.5b-v0.1/adapter_config.json \
        --out adapters/v0.1_fixed
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mlx-adapter", type=Path, required=True)
    ap.add_argument("--peft-config", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    from safetensors.torch import load_file, save_file

    src = load_file(str(args.mlx_adapter))
    dst = {}
    for key, tensor in src.items():
        base, kind = key.rsplit(".", 1)
        if kind not in ("lora_a", "lora_b"):
            raise ValueError(f"unexpected key: {key}")
        dst[f"base_model.model.{base}.lora_{kind[-1].upper()}.default.weight"] = tensor.T.contiguous()
    args.out.mkdir(parents=True, exist_ok=True)
    save_file(dst, str(args.out / "adapter_model.safetensors"), metadata={"format": "pt"})
    shutil.copy(args.peft_config, args.out / "adapter_config.json")
    print(f"{len(dst)} tensors -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
