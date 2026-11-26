#!/usr/bin/env python3
"""統一評価基盤から呼び出す NavSpec deterministic runner adapter。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_armament_pipeline import (  # noqa: E402
    DEFAULT_DATA_DIR,
    run_armament_pipeline,
    validate_pipeline_result,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["armament"], required=True)
    parser.add_argument(
        "--strategy",
        choices=["representative", "mid_caliber", "random"],
        default="representative",
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--model", default="eval-runner")
    parser.add_argument("--adapter", default="none")
    args = parser.parse_args(argv)

    payload = json.loads(sys.stdin.read())
    if not isinstance(payload, dict):
        raise SystemExit("evaluation input must be a JSON object")
    seed = int(os.environ.get("NAVSPEC_EVAL_SEED", "0"))
    if args.mode == "armament":
        result = run_armament_pipeline(
            payload,
            strategy=args.strategy,
            seed=seed,
            data_dir=args.data_dir,
            model_name=args.model,
            adapter_name=args.adapter,
        )
        errors = validate_pipeline_result(result)
        if errors:
            print(json.dumps({"errors": errors}, ensure_ascii=False), file=sys.stderr)
            return 2
    else:
        raise AssertionError(f"unsupported mode: {args.mode}")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
