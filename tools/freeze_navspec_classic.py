"""Create a navspec-classic archive without mutating git history.

このスクリプトは commit/tag を作らず、現行classic成果物を
artifacts/navspec-classic-v1/ に保存する。git操作はユーザー指示後に別途行う。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "artifacts" / "navspec-classic-v1"


COPY_ITEMS = [
    ("checkpoints/navspec/latest.json", "checkpoint/latest.json"),
    ("checkpoints/navspec/latest.safetensors", "checkpoint/latest.safetensors"),
    ("checkpoints/navspec/best.json", "checkpoint/best.json"),
    ("checkpoints/navspec/best.safetensors", "checkpoint/best.safetensors"),
    (".cache/autoresearch-navspec/tokenizer", "tokenizer"),
    ("pyproject.toml", "configs/pyproject.toml"),
    ("uv.lock", "configs/uv.lock"),
    ("schemas/ship_spec_record.schema.json", "configs/ship_spec_record.schema.json"),
    ("data/navspec/tasks.train.jsonl", "dataset/tasks.train.jsonl"),
    ("data/navspec/tasks.val.jsonl", "dataset/tasks.val.jsonl"),
    ("data/navspec/battleship_real_specs.jsonl", "dataset/battleship_real_specs.jsonl"),
    ("data/navspec/battleship_fictional_specs.jsonl", "dataset/battleship_fictional_specs.jsonl"),
    ("data/navspec/generated/eval/report.json", "eval/report.json"),
    ("data/navspec/generated/raw_eval/report.json", "eval/raw_model_eval.json"),
    ("docs/eval_suite_report_2026-07-10.md", "eval/eval_suite_report_2026-07-10.md"),
    ("docs/raw_model_eval_report_2026-07-10.md", "eval/raw_model_eval_report_2026-07-10.md"),
    ("docs/navspec_current_status_2026-07-10.md", "eval/navspec_current_status_2026-07-10.md"),
    ("results.tsv", "eval/results.tsv"),
]


def run_git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_item(src: Path, dst: Path) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    if not src.exists():
        return copied
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        for file_path in sorted(dst.rglob("*")):
            if file_path.is_file():
                copied.append(file_record(file_path, dst.parent))
        return copied

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied.append(file_record(dst, dst.parent.parent if dst.parent.name != "navspec-classic-v1" else dst.parent))
    return copied


def file_record(path: Path, base: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(base)),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def count_jsonl(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_manifest(out_dir: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    checkpoint = load_json(ROOT / "checkpoints/navspec/latest.json")
    eval_report = load_json(ROOT / "data/navspec/generated/eval/report.json")
    raw_report = load_json(ROOT / "data/navspec/generated/raw_eval/report.json")
    eval_rows = eval_report if isinstance(eval_report, list) else []
    raw_summary = raw_report.get("summary", {}) if isinstance(raw_report, dict) else {}

    return {
        "archive_id": "navspec-classic-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git": {
            "commit_hash": run_git(["rev-parse", "HEAD"]),
            "branch": run_git(["branch", "--show-current"]),
            "status_short": run_git(["status", "--short"]),
            "tag_recommended": "navspec-classic-v1",
            "tag_created_by_script": False,
        },
        "checkpoint": {
            "metadata": checkpoint,
            "latest_json_sha256": sha256_file(ROOT / "checkpoints/navspec/latest.json")
            if (ROOT / "checkpoints/navspec/latest.json").exists()
            else None,
            "latest_safetensors_sha256": sha256_file(ROOT / "checkpoints/navspec/latest.safetensors")
            if (ROOT / "checkpoints/navspec/latest.safetensors").exists()
            else None,
        },
        "dataset": {
            "tasks_train_rows": count_jsonl(ROOT / "data/navspec/tasks.train.jsonl"),
            "tasks_val_rows": count_jsonl(ROOT / "data/navspec/tasks.val.jsonl"),
            "real_specs_rows": count_jsonl(ROOT / "data/navspec/battleship_real_specs.jsonl"),
            "fictional_specs_rows": count_jsonl(ROOT / "data/navspec/battleship_fictional_specs.jsonl"),
        },
        "integrated_eval": {
            "total": len(eval_rows),
            "patched_json_success": sum(1 for row in eval_rows if row.get("patched_json_valid")),
            "schema_ok": sum(1 for row in eval_rows if not row.get("schema_errors")),
            "lb_fail": sum(c.get("status") == "FAIL" for row in eval_rows for c in row.get("lb_ratio_checks", [])),
            "contradiction_fail": sum(c.get("status") == "FAIL" for row in eval_rows for c in row.get("contradiction_checks", [])),
            "tier_fail": sum(c.get("status") == "FAIL" for row in eval_rows for c in row.get("nation_tier_checks", [])),
        },
        "raw_model_eval": raw_summary,
        "files": files,
        "archive_path": str(out_dir),
    }


def write_readme(out_dir: Path, manifest: dict[str, Any]) -> None:
    metrics = manifest["checkpoint"].get("metadata", {}).get("metrics", {})
    integrated = manifest["integrated_eval"]
    raw = manifest.get("raw_model_eval", {})
    text = f"""# navspec-classic-v1

Created: {manifest['created_at_utc']}

This archive freezes the classic navspec stack: 5.24M scratch checkpoint,
tokenizer, schema, datasets, rule-backed eval reports, and raw model eval.

## Git

- commit: `{manifest['git']['commit_hash']}`
- branch: `{manifest['git']['branch']}`
- tag recommended: `navspec-classic-v1`
- tag created by archive script: `{manifest['git']['tag_created_by_script']}`

The working tree status at archive time is recorded in `dataset_manifest.json`.

## Checkpoint

- params: {metrics.get('num_params_m')}M
- val_bpb: {metrics.get('val_bpb')}
- steps: {metrics.get('num_steps')}
- tokens seen: {metrics.get('total_tokens_m')}M
- training seconds: {metrics.get('training_seconds')}

## Integrated Eval

- total: {integrated.get('total')}
- patched JSON success: {integrated.get('patched_json_success')}/{integrated.get('total')}
- schema ok: {integrated.get('schema_ok')}/{integrated.get('total')}
- L/B FAIL: {integrated.get('lb_fail')}
- contradiction FAIL: {integrated.get('contradiction_fail')}
- tier FAIL: {integrated.get('tier_fail')}

## Raw Model Eval

- total: {raw.get('total')}
- raw JSON rate: {raw.get('raw_json_rate')}
- raw schema complete rate: {raw.get('raw_schema_complete_rate')}
- field adoption rate: {raw.get('field_adoption_rate_avg')}
- patch dependency rate: {raw.get('patch_dependency_rate_avg')}

## Restore Notes

Use this archive as a baseline and fallback. Do not overwrite it with LoRA
experiments; store LoRA adapters under `models/navspec-lora-*`.
"""
    (out_dir / "README.md").write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive navspec classic artifacts")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    for src_rel, dst_rel in COPY_ITEMS:
        files.extend(copy_item(ROOT / src_rel, out_dir / dst_rel))

    manifest = build_manifest(out_dir, files)
    (out_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    hashes = "\n".join(f"{item['sha256']}  {item['path']}" for item in sorted(files, key=lambda x: x["path"]))
    (out_dir / "hashes.txt").write_text(hashes + "\n", encoding="utf-8")
    write_readme(out_dir, manifest)

    print(f"archived: {out_dir}")
    print(f"files: {len(files)}")
    print(f"commit: {manifest['git']['commit_hash']}")
    print("tag_created_by_script: false")


if __name__ == "__main__":
    main()
