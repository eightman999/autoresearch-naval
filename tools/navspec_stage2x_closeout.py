"""Write reproducible Stage 2.x closeout artifacts after contract re-evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_ROOT = ROOT / "artifacts" / "navspec-lora"
SUMMARY_ROOT = ARTIFACT_ROOT / "stage2x_eval_summaries"
REPORT_ROOT = ROOT / "data" / "navspec" / "lora" / "eval"

CHECKPOINTS = [
    {
        "label": "stage2-iter1000",
        "id": "navspec-qwen2.5-0.5b-stage2-r8",
        "status": "accepted",
        "default": True,
        "adapter": "models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8",
        "weight": "models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/adapters.safetensors",
        "dataset_manifest": "data/navspec/lora/stage2_status_v0/manifest.json",
        "promotion_decision": "accepted_default",
        "strengths": ["Stage 2.x正式default", "statusとimpossible判定の安定性"],
        "regressions": [],
    },
    {
        "label": "stage2.1-iter500",
        "id": "navspec-qwen2.5-0.5b-stage2.1-r8-iter500",
        "status": "experimental",
        "default": False,
        "adapter": "models/navspec-lora-0.5b-v0.1/adapter/stage2.1-iter500",
        "weight": "models/navspec-lora-0.5b-v0.1/adapter/stage2.1-iter500/adapters.safetensors",
        "dataset_manifest": "data/navspec/lora/stage2_1_status_v0/manifest.json",
        "promotion_decision": "experimental_only",
        "strengths": ["nation_capability/treatyの実験"],
        "regressions": ["正式defaultには昇格しない"],
    },
    {
        "label": "stage2.2-iter500",
        "id": "navspec-qwen2.5-0.5b-stage2.2-r8-iter500",
        "status": "experimental_rejected",
        "default": False,
        "adapter": "models/navspec-lora-0.5b-v0.1/adapter/stage2.2-balanced-reason-r8",
        "weight": "models/navspec-lora-0.5b-v0.1/adapter/stage2.2-balanced-reason-r8/adapters.safetensors",
        "dataset_manifest": "data/navspec/lora/stage2_2_balanced_reason_v0/manifest.json",
        "promotion_decision": "experimental_rejected",
        "strengths": ["speed reason厳格定義の概念実証"],
        "regressions": ["era JSON安定性と総合ゲート"],
    },
    {
        "label": "stage2.3-iter250",
        "id": "navspec-qwen2.5-0.5b-stage2.3-r8-iter250",
        "status": "experimental_rejected",
        "default": False,
        "adapter": "models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter250",
        "weight": "models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter250/adapters.safetensors",
        "dataset_manifest": "data/navspec/lora/stage2_3_era_balanced_v0/manifest.json",
        "promotion_decision": "experimental_rejected_representative",
        "strengths": ["era truncation解消の実験代表checkpoint"],
        "regressions": ["Stage 2正式採用ゲート未達"],
    },
    {
        "label": "stage2.3-iter500",
        "id": "navspec-qwen2.5-0.5b-stage2.3-r8-iter500",
        "status": "experimental_rejected",
        "default": False,
        "adapter": "models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter500",
        "weight": "models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter500/adapters.safetensors",
        "dataset_manifest": "data/navspec/lora/stage2_3_era_balanced_v0/manifest.json",
        "promotion_decision": "experimental_rejected_comparison",
        "strengths": ["Stage 2.3比較checkpoint"],
        "regressions": ["正式採用ゲート未達"],
    },
]
DATASETS = ("stage2_eval", "speed_eval", "era_eval", "set622_common_semantic")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def report_path(label: str, dataset: str) -> Path:
    return REPORT_ROOT / f"stage2x-{label}" / dataset / "report.json"


def collect_summaries() -> dict[str, dict[str, dict[str, Any]]]:
    summaries: dict[str, dict[str, dict[str, Any]]] = {}
    for checkpoint in CHECKPOINTS:
        label = checkpoint["label"]
        summaries[label] = {}
        for dataset in DATASETS:
            path = report_path(label, dataset)
            if not path.exists():
                raise FileNotFoundError(f"missing re-evaluation report: {path.relative_to(ROOT)}")
            summary = read_json(path)["summary"]
            summaries[label][dataset] = summary
            summary_path = SUMMARY_ROOT / f"{label}_{dataset}.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summaries


def write_hashes(summaries: dict[str, dict[str, dict[str, Any]]]) -> Path:
    paths = [
        "models/navspec-lora-0.5b-v0.1/adapter/stage1-r8/adapters.safetensors",
        *[checkpoint["weight"] for checkpoint in CHECKPOINTS],
        "schemas/navspec_lora_candidate.schema.json",
        "schemas/navspec_lora_candidate_stage2.schema.json",
        "data/navspec/lora/stage2_status_v0/manifest.json",
        "data/navspec/lora/stage2_1_status_v0/manifest.json",
        "data/navspec/lora/stage2_2_balanced_reason_v0/manifest.json",
        "data/navspec/lora/stage2_3_era_balanced_v0/manifest.json",
        "data/navspec/lora/stage2x_eval_contract_v1/manifest.json",
    ]
    for checkpoint in CHECKPOINTS:
        for dataset in DATASETS:
            paths.append(str((SUMMARY_ROOT / f"{checkpoint['label']}_{dataset}.json").relative_to(ROOT)))
    lines = []
    for relative in paths:
        path = ROOT / relative
        if not path.exists():
            raise FileNotFoundError(relative)
        lines.append(f"{sha256(path)}  {relative}")
    target = ARTIFACT_ROOT / "stage2x_hashes.sha256"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def pct(value: Any) -> str:
    return f"{float(value or 0.0) * 100:.1f}%"


def write_comparison(summaries: dict[str, dict[str, dict[str, Any]]]) -> Path:
    lines = [
        "# navspec Stage 2.x 最終比較",
        "",
        f"作成日: {date.today().isoformat()}",
        "",
        "評価契約 `navspec-stage2x-eval-contract-v1` を使用。正式比較は strict reason exact と semantic reason micro F1 を使用し、legacy reason exact は連続性確認用です。",
        "",
        "| checkpoint | status | schema | impossible F1 | legacy exact | strict exact | semantic micro F1 | primary | extra | missing | invalid | field adoption | truncated | early EOS | speed strict | era strict | 622 semantic | 採用 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for checkpoint in CHECKPOINTS:
        metrics = summaries[checkpoint["label"]]
        primary = metrics["stage2_eval"]
        speed = metrics["speed_eval"]
        era = metrics["era_eval"]
        common = metrics["set622_common_semantic"]
        lines.append(
            "| {label} | {status} | {schema} | {f1} | {legacy} | {strict} | {semantic} | {primary_reason} | {extra} | {missing} | {invalid} | {field} | {truncated} | {early} | {speed} | {era} | {common} | {decision} |".format(
                label=checkpoint["label"],
                status=checkpoint["status"],
                schema=pct(primary.get("schema_complete_rate")),
                f1=pct(primary.get("impossible_f1")),
                legacy=pct(primary.get("legacy_reason_exact_rate")),
                strict=pct(primary.get("strict_reason_exact_rate")),
                semantic=pct(primary.get("semantic_reason_micro_f1")),
                primary_reason=pct(primary.get("primary_reason_accuracy")),
                extra=pct(primary.get("extra_reason_rate")),
                missing=pct(primary.get("missing_reason_rate")),
                invalid=pct(primary.get("invalid_reason_rate")),
                field=pct(primary.get("spec_field_adoption_rate_avg")),
                truncated=pct(primary.get("truncated_json_rate")),
                early=primary.get("json_damage_counts", {}).get("early_eos_invalid_json", 0),
                speed=pct(speed.get("strict_reason_exact_rate")),
                era=pct(era.get("strict_reason_exact_rate")),
                common=pct(common.get("semantic_reason_micro_f1")),
                decision=checkpoint["promotion_decision"],
            )
        )
    lines.extend([
        "",
        "## 決定",
        "",
        "- Stage 2 iter1000をaccepted/defaultとして維持する。",
        "- Stage 2.1はexperimental、Stage 2.2/2.3はexperimental_rejectedとして保存する。",
        "- Stage 2.4は実施せず、Stage 3はStage 2 iter1000 + classic validatorをbaselineとする。",
        "",
        "## 保存",
        "",
        "- ケース別評価JSONは再生成可能なためGit管理外。要約JSON、contract manifest、hash一覧、adapter metadataと本レポートをGit管理する。",
        "- LoRA重みはGit LFS管理。正式reason codeはclassic rule engineを正とする。",
    ])
    target = ROOT / "docs" / "navspec_stage2x_final_comparison_2026-07-11.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def update_registry(summaries: dict[str, dict[str, dict[str, Any]]]) -> Path:
    path = ROOT / "models" / "registry.json"
    registry = read_json(path)
    lora = registry.setdefault("lora", {})
    stage2x: dict[str, Any] = {}
    for checkpoint in CHECKPOINTS:
        primary_summary = SUMMARY_ROOT / f"{checkpoint['label']}_stage2_eval.json"
        item = {key: value for key, value in checkpoint.items() if key not in {"label", "weight"}}
        item.update({
            "parent": "navspec-qwen2.5-0.5b-stage1-r8",
            "base_model": "Qwen/Qwen2.5-0.5B-Instruct",
            "schema_version": "navspec.lora_candidate.v1",
            "training_config": f"{checkpoint['adapter']}/adapter_config.json",
            "primary_eval_summary": str(primary_summary.relative_to(ROOT)),
            "sha256": sha256(ROOT / checkpoint["weight"]),
        })
        stage2x[checkpoint["id"]] = item
    lora["stage2x"] = stage2x
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_readme(hash_path: Path) -> Path:
    target = ARTIFACT_ROOT / "README.md"
    target.write_text(
        "# navspec LoRA artifacts\n\n"
        "Stage 2.x closeout metadata. Adapter binaries are managed by Git LFS; "
        "case-level evaluation JSON is reproducible and intentionally not committed.\n\n"
        f"Integrity manifest: `{hash_path.relative_to(ROOT)}`.\n",
        encoding="utf-8",
    )
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize navspec Stage 2.x closeout artifacts")
    parser.add_argument("--update-registry", action="store_true")
    args = parser.parse_args()
    summaries = collect_summaries()
    hash_path = write_hashes(summaries)
    comparison = write_comparison(summaries)
    readme = write_readme(hash_path)
    registry = update_registry(summaries) if args.update_registry else None
    print(json.dumps({
        "hashes": str(hash_path.relative_to(ROOT)),
        "comparison": str(comparison.relative_to(ROOT)),
        "readme": str(readme.relative_to(ROOT)),
        "registry": str(registry.relative_to(ROOT)) if registry else None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
