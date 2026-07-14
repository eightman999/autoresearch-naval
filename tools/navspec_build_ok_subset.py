"""Build numeric specialist ok-only eval subset from stage3_numeric_eval_v0.

Extracts cases where expected_status == "ok" (== classic final status ok).
Impossible cases are kept separately as a holdout for pipeline false-call
verification (Section 8). Writes eval.jsonl + manifest.json with full
distribution audit (Section 8 manifest requirements).
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

SOURCE_CASES = ROOT / "data" / "navspec" / "lora" / "stage3_numeric_eval_v0" / "cases.jsonl"
SOURCE_MANIFEST = ROOT / "data" / "navspec" / "lora" / "stage3_numeric_eval_v0" / "manifest.json"
CONTRACT = ROOT / "data" / "navspec" / "lora" / "contracts" / "navspec_numeric_specialist_eval_contract_v1.json"
OUT_DIR = ROOT / "data" / "navspec" / "lora" / "stage3_numeric_eval_ok_v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def era_label(year: int, eras: list[dict]) -> str:
    for era in eras:
        lo, hi = era["range"]
        if lo <= year <= hi:
            return era["label"]
    return "unknown"


def nation_tier_label(tier: int, tiers: list[dict]) -> str:
    for t in tiers:
        if t["tier"] == tier:
            return t["label"]
    return "unknown"


def main() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in SOURCE_CASES.read_text(encoding="utf-8").splitlines() if line.strip()]

    ok_rows = [r for r in rows if r.get("expected_status") == "ok"]
    impossible_rows = [r for r in rows if r.get("expected_status") == "impossible"]

    eras = contract["categories"]["eras"]
    tiers = contract["categories"]["nation_tiers"]

    ship_type_dist = Counter()
    era_dist = Counter()
    nation_tier_dist = Counter()
    constraint_dist = Counter()
    category_dist = Counter()
    role_dist = Counter()

    for r in ok_rows:
        inp = r.get("input") or {}
        spec = (r.get("expected_output") or {}).get("spec") or {}
        ship_type_dist[inp.get("ship_type", "unknown")] += 1
        era_dist[era_label(int(inp.get("year", 0)), eras)] += 1
        nation_tier_dist[nation_tier_label(int(spec.get("nation_tier", 0)), tiers)] += 1
        constraints = inp.get("constraints") or {}
        constraint_dist[len(constraints)] += 1
        category_dist[r.get("category", "unknown")] += 1
        role_dist[inp.get("role", "unknown")] += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    eval_path = OUT_DIR / "eval.jsonl"
    with eval_path.open("w", encoding="utf-8") as f:
        for r in ok_rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    holdout_path = OUT_DIR / "impossible_holdout.jsonl"
    with holdout_path.open("w", encoding="utf-8") as f:
        for r in impossible_rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    manifest = {
        "dataset": "stage3_numeric_eval_ok_v1",
        "created": "2026-07-13",
        "source_dataset": "stage3_numeric_eval_v0",
        "source_cases_hash": sha256_file(SOURCE_CASES),
        "source_manifest_hash": sha256_file(SOURCE_MANIFEST),
        "extraction_condition": "expected_status == ok (== classic final status ok, teacher=navspec-classic-v1)",
        "extraction_count": len(ok_rows),
        "impossible_holdout_count": len(impossible_rows),
        "source_total": len(rows),
        "seed": source_manifest.get("seed", 99),
        "ship_type_distribution": dict(sorted(ship_type_dist.items(), key=lambda kv: -kv[1])),
        "era_distribution": dict(sorted(era_dist.items())),
        "nation_tier_distribution": dict(sorted(nation_tier_dist.items())),
        "constraint_count_distribution": {str(k): v for k, v in sorted(constraint_dist.items())},
        "category_distribution": dict(sorted(category_dist.items(), key=lambda kv: -kv[1])),
        "role_distribution": dict(sorted(role_dist.items(), key=lambda kv: -kv[1])),
        "eval_jsonl_hash": sha256_file(eval_path),
        "impossible_holdout_hash": sha256_file(holdout_path),
        "contract_version": contract["contract_version"],
        "note": "impossible_holdout.jsonl は pipeline 誤呼出検証用 (Section 8)。numeric specialist 採点対象は eval.jsonl のみ。",
    }

    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"eval.jsonl: {len(ok_rows)} cases")
    print(f"impossible_holdout.jsonl: {len(impossible_rows)} cases")
    print(f"ship_types: {len(ship_type_dist)}")
    print(f"eras: {len(era_dist)}")
    print(f"nation_tiers: {dict(nation_tier_dist)}")
    print(f"constraint_dist: {dict(sorted(constraint_dist.items()))}")
    print(f"eval_jsonl_hash: {manifest['eval_jsonl_hash']}")


if __name__ == "__main__":
    main()
