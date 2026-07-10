"""Run repeated navspec training experiments with per-run checkpoints and logs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / ".cache" / "autoresearch-navspec"
DEFAULT_RUNS_DIR = ROOT / "checkpoints" / "navspec" / "runs"
DEFAULT_GENERATED_DIR = ROOT / "data" / "navspec" / "generated" / "runs"


def run_command(args: list[str], env: dict[str, str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            args,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        return proc.wait()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_one(index: int, args: argparse.Namespace, base_env: dict[str, str]) -> dict[str, Any]:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = f"{args.tag}_{index:02d}" if args.tag else f"run_{index:02d}"
    run_id = f"{timestamp}_{suffix}"
    run_dir = args.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    env = dict(base_env)
    env["AUTORESEARCH_CACHE_DIR"] = str(args.cache_dir)
    env["AUTORESEARCH_TIME_BUDGET"] = str(args.time_budget)
    env["AUTORESEARCH_EVAL_TOKENS"] = str(args.eval_tokens)
    env["AUTORESEARCH_CHECKPOINT_DIR"] = str(run_dir)
    env["AUTORESEARCH_CHECKPOINT_NAME"] = "model"
    env["AUTORESEARCH_SAVE_CHECKPOINT"] = "1"

    print(f"=== navspec train run {index}/{args.runs}: {run_id} ===")
    train_code = run_command(["uv", "run", "train.py"], env, run_dir / "train.log")
    metadata_path = run_dir / "model.json"
    metadata = read_json(metadata_path)
    result: dict[str, Any] = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "train_exit_code": train_code,
        "checkpoint_metadata": str(metadata_path),
        "metrics": metadata.get("metrics") if metadata else None,
    }

    if train_code != 0 or not metadata_path.exists() or args.skip_generate:
        return result

    args.generated_dir.mkdir(parents=True, exist_ok=True)
    raw_output = args.generated_dir / f"{run_id}_raw.txt"
    json_output = args.generated_dir / f"{run_id}.json"
    generate_code = run_command(
        [
            "uv",
            "run",
            "python",
            "generate_navspec.py",
            "--checkpoint",
            str(metadata_path),
            "--raw-output",
            str(raw_output),
            "--json-output",
            str(json_output),
            "--max-new-tokens",
            str(args.max_new_tokens),
        ],
        env,
        run_dir / "generate.log",
    )
    result["generate_exit_code"] = generate_code
    result["generated_json"] = str(json_output)
    result["generated_raw"] = str(raw_output)

    if generate_code == 0 and json_output.exists():
        validate_code = run_command(
            ["uv", "run", "python", "validate_navspec_output.py", str(json_output)],
            env,
            run_dir / "validate_generated.log",
        )
        result["validate_exit_code"] = validate_code

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run repeated navspec training experiments")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--time-budget", type=int, default=600)
    parser.add_argument("--eval-tokens", type=int, default=1572864)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--generated-dir", type=Path, default=DEFAULT_GENERATED_DIR)
    parser.add_argument("--tag", default="navspec")
    parser.add_argument("--max-new-tokens", type=int, default=1536)
    parser.add_argument("--skip-generate", action="store_true")
    args = parser.parse_args()

    summaries: list[dict[str, Any]] = []
    base_env = os.environ.copy()
    for index in range(1, args.runs + 1):
        result = run_one(index, args, base_env)
        summaries.append(result)
        if result.get("train_exit_code") != 0 and args.runs > 1:
            print(f"stopping after failed training run {result['run_id']}")
            break

    summary_path = args.runs_dir / f"summary_{time.strftime('%Y%m%d_%H%M%S')}_{args.tag}.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"run_summary: {summary_path}")
    for item in summaries:
        metrics = item.get("metrics") or {}
        print(
            f"{item['run_id']}: train={item.get('train_exit_code')} "
            f"generate={item.get('generate_exit_code')} validate={item.get('validate_exit_code')} "
            f"val_bpb={metrics.get('val_bpb')}"
        )


if __name__ == "__main__":
    main()
