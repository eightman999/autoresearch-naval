#!/usr/bin/env python3
"""Build the armament verdict v3 dataset: v2.1 + raw DB facts in the prompt.

R3 実験用。v2.1 の行・分割・assistant 正解は変えず、user JSON に ``db_facts`` を足す。
``db_facts`` は candidate が参照する weapon_id / mount_id / fire_control_id の DB レコードから
判定に関係する生の値だけを抜き出したもの。validator の判定結果や、閾値との比較結果
（重量超過 bool など）は入れない。判定はモデルにさせる。

ルールの定数（排水量に対する比率など）と、DB 値の読み方は system プロンプトに短く足す。

使用法::

    uv run python tools/navspec_lora_data_armament_verdict_v3.py \
        --input-dir data/navspec/lora/stage4a_armament_verdict_v2_1 \
        --output-dir data/navspec/lora/stage4a_armament_verdict_v3 \
        --db-dir data/navspec/armament

整合性チェック（各行を現在の DB + classic validator で判定し直し、v2.1 の正解と比較）は
常にビルド時に実行し manifest.json の ``consistency`` に記録する。正解ラベルは書き換えない。

Qwen3.5 トークナイザでのトークン長統計（任意）::

    python tools/navspec_lora_data_armament_verdict_v3.py --token-stats-only \
        --output-dir data/stage4a_armament_verdict_v3 --tokenizer Qwen/Qwen3.5-0.8B
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from navspec_armament_validator import load_db, validate_armament  # noqa: E402

SPLITS = ("train", "valid", "test")
SOURCE_DATASET = "stage4a_armament_verdict_v2_1"

# v2.1 の system プロンプト（先頭部分は完全に同じ）に DB 値の読み方と定数を追記する。
V21_SYSTEM_PROMPT = (
    "You are navspec-armament-judge. Given the ship context and proposed main battery, "
    "return only JSON matching navspec.armament_verdict.v1. "
    "The top-level keys must be schema_version,status,reason_codes. "
    "Use status=impossible when any armament rule is violated; otherwise use status=ok. "
    "reason_codes must contain only the error reason codes, as a JSON string array."
)
# 定数は compatibility_rules.json / validator の値（0.06 * 2.5 = 15%、50%）。
# 実行時に DB の値と一致することを assert する（build_system_prompt）。
SINGLE_TURRET_FRACTION = 0.15
TOTAL_TURRET_FRACTION = 0.50
SYSTEM_PROMPT_ADDENDUM = (
    " The user JSON includes db_facts: raw database records for every weapon_id, mount_id and "
    "fire_control_id the candidate references (found=false means the id is not in the database). "
    "A mount is unavailable for the era when its year_introduced, or the battery weapon's year_introduced "
    "if the mount's is null, is later than year. "
    "A single mount whose turret_weight_t is more than 15% of hull.displacement_t violates "
    "turret_weight_exceeds_hull_limit. "
    "The sum of turret_weight_t x mount_count over all batteries above 50% of hull.displacement_t "
    "violates armament_displacement_conflict."
)
SYSTEM_PROMPT = V21_SYSTEM_PROMPT + SYSTEM_PROMPT_ADDENDUM

DB_FACTS_KEY = "db_facts"

# 抜き出すフィールド。error 判定に使われる値だけに絞る（barbette / magazine / caliber 範囲は
# validator で warn 止まりで、barbette_diameter_m 等は DB 側がほぼ null のため入れない）。
WEAPON_FIELDS = ("nation", "year_introduced")
MOUNT_FIELDS = ("weapon_id", "nation", "year_introduced", "barrels_per_mount", "turret_weight_t")
FIRE_CONTROL_FIELDS = ("nation", "year_introduced", "radar")


def check_constants(db: dict[str, Any]) -> None:
    """system プロンプトに書く定数が DB / validator の値と一致することを確認する。"""
    rules = db["compatibility_rules"]
    frac = rules["turret_weight_to_displacement"]["max_turret_weight_fraction_of_displacement"]
    if abs(frac * 2.5 - SINGLE_TURRET_FRACTION) > 1e-9:
        raise ValueError(f"単砲塔閾値が変わっています: {frac}*2.5 != {SINGLE_TURRET_FRACTION}")
    # 50% は validator 内のハードコード定数。ソースの文言で検証する。
    src = (Path(__file__).resolve().parent / "navspec_armament_validator.py").read_text(encoding="utf-8")
    if "total_turret_weight_t > disp_t * 0.5" not in src:
        raise ValueError("validator の armament_displacement_conflict 閾値が変わっています")
    if "tw > disp_t * max_frac * 2.5" not in src:
        raise ValueError("validator の単砲塔閾値の式が変わっています")


def _pick(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {"found": True}
    for name in fields:
        out[name] = record.get(name)
    return out


def build_db_facts(candidate: dict[str, Any], db: dict[str, Any]) -> dict[str, Any]:
    """candidate が参照する ID の生 DB 値。判定結果・比較結果は含めない。"""
    weapons: dict[str, Any] = {}
    mounts: dict[str, Any] = {}
    fire_control: dict[str, Any] = {}
    w2m = db["compatibility_rules"].get("weapon_to_mount", {})
    for battery in candidate.get("main_batteries", []):
        wid = battery.get("weapon_id")
        mid = battery.get("mount_id")
        fid = battery.get("fire_control_id")
        if wid is not None and wid not in weapons:
            record = db["weapons"].get(wid)
            if record is None:
                weapons[wid] = {"found": False}
            else:
                fact = _pick(record, WEAPON_FIELDS)
                # weapon_to_mount は DB の互換表（weapon_id -> 載せられる mount_id の一覧）。
                fact["compatible_mount_ids"] = list(w2m.get(wid, []))
                weapons[wid] = fact
        if mid is not None and mid not in mounts:
            record = db["mounts"].get(mid)
            mounts[mid] = {"found": False} if record is None else _pick(record, MOUNT_FIELDS)
        if fid is not None and fid not in fire_control:
            record = db["fire_control"].get(fid)
            fire_control[fid] = {"found": False} if record is None else _pick(record, FIRE_CONTROL_FIELDS)
    return {"weapons": weapons, "mounts": mounts, "fire_control": fire_control}


def dumps_user(user: dict[str, Any]) -> str:
    # v2.1 と同じ compact / sort_keys 形式（往復一致を確認済み）
    return json.dumps(user, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def recheck(user: dict[str, Any], db: dict[str, Any]) -> tuple[str, list[str]]:
    """現在の DB + classic validator で candidate を判定し直す（status, error 由来の reason codes）。"""
    ctx = {
        "ship_type": user["ship_type"],
        "year": user["year"],
        "nation": user["nation"],
        "displacement_t": user["hull"]["displacement_t"],
        "beam_m": user["hull"]["beam_m"],
    }
    result = validate_armament(user["candidate"], ctx, db)
    codes = sorted({i["code"] for i in result["issues"] if i["severity"] == "error"})
    return result["status"], codes


def build_dataset(input_dir: Path, output_dir: Path, db_dir: Path) -> dict[str, Any]:
    db = load_db(db_dir)
    check_constants(db)
    source_manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)

    split_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    # 整合性チェック
    cons: Counter[str] = Counter()
    mismatches: list[dict[str, Any]] = []
    found_false: Counter[str] = Counter()
    null_year_mount_rows = 0

    for split in SPLITS:
        src_rows = read_rows(input_dir / f"{split}.jsonl")
        out_lines: list[str] = []
        for index, row in enumerate(src_rows):
            msgs = row["messages"]
            if [m["role"] for m in msgs] != ["system", "user", "assistant"]:
                raise ValueError(f"{split}[{index}]: roles が想定外")
            if msgs[0]["content"] != V21_SYSTEM_PROMPT:
                raise ValueError(f"{split}[{index}]: v2.1 の system プロンプトと一致しません")
            user = json.loads(msgs[1]["content"])
            if DB_FACTS_KEY in user:
                raise ValueError(f"{split}[{index}]: 既に {DB_FACTS_KEY} があります")
            expected = json.loads(msgs[2]["content"])

            facts = build_db_facts(user["candidate"], db)
            for kind in facts:
                for rec in facts[kind].values():
                    if not rec["found"]:
                        found_false[kind] += 1
            if any(rec["found"] and rec["year_introduced"] is None for rec in facts["mounts"].values()):
                null_year_mount_rows += 1
            new_user = dict(user)
            new_user[DB_FACTS_KEY] = facts

            new_row = {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": dumps_user(new_user)},
                    # assistant は v2.1 と同一（バイト一致）
                    {"role": "assistant", "content": msgs[2]["content"]},
                ]
            }
            out_lines.append(json.dumps(new_row, ensure_ascii=False, sort_keys=True) + "\n")

            # 整合性: 現在の DB + validator と v2.1 正解
            status, codes = recheck(user, db)
            exp_codes = sorted(expected["reason_codes"])
            status_ok = status == expected["status"]
            codes_ok = codes == exp_codes
            cons["rows"] += 1
            cons["status_match"] += status_ok
            cons["reason_codes_match"] += codes_ok
            cons["both_match"] += status_ok and codes_ok
            if not (status_ok and codes_ok):
                mismatches.append(
                    {
                        "split": split,
                        "index": index,
                        "v2_1_status": expected["status"],
                        "v2_1_reason_codes": exp_codes,
                        "validator_status": status,
                        "validator_reason_codes": codes,
                    }
                )
            status_counts[expected["status"]] += 1
            for code in expected["reason_codes"]:
                reason_counts[code] += 1
        (output_dir / f"{split}.jsonl").write_text("".join(out_lines), encoding="utf-8")
        split_counts[split] = len(src_rows)

    manifest = {
        "dataset": output_dir.name,
        "format": "mlx_lm ChatDataset/messages, compact JSON; status key emitted first",
        "max_output_tokens": 64,
        "purpose": "R3: v2.1 の user JSON に判定に要る生の DB 値 (db_facts) を足した判定タスク",
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "rows": sum(split_counts.values()),
        "schema": "schemas/navspec_armament_verdict_stage4a.schema.json",
        "schema_version": "navspec.armament_verdict.v1",
        "source": f"data/navspec/lora/{input_dir.name}",
        "source_manifest": {
            "dataset": source_manifest.get("dataset"),
            "rows": source_manifest.get("rows"),
            "split_counts": source_manifest.get("split_counts", {}),
        },
        "split_counts": dict(sorted(split_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "system_prompt": SYSTEM_PROMPT,
        "task": "judge_navspec_armament",
        "derivation": (
            "v2.1 の行・分割・assistant を維持。user JSON に db_facts（candidate が参照する "
            "weapon/mount/fire_control の生 DB 値）を追加し、system プロンプトに定数と DB 値の読み方を追記"
        ),
        "db_facts": {
            "key": DB_FACTS_KEY,
            "weapon_fields": list(WEAPON_FIELDS) + ["compatible_mount_ids"],
            "mount_fields": list(MOUNT_FIELDS),
            "fire_control_fields": list(FIRE_CONTROL_FIELDS),
            "missing_id": {"found": False},
            "db_dir": str(db_dir),
            "db_counts": {k: len(db[k]) for k in ("weapons", "mounts", "fire_control")},
            "found_false_counts": dict(found_false),
            "rows_with_null_year_mount": null_year_mount_rows,
            "excluded": "validator の判定結果・閾値比較の結果は含めない（生の値のみ）",
        },
        "consistency": {
            "note": "現在の DB + tools/navspec_armament_validator.py で各行を判定し直し、v2.1 の正解と比較（ラベルは書き換えない）",
            **dict(cons),
            "mismatch_rows": mismatches,
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def token_stats(data_dir: Path, tokenizer_name: str) -> dict[str, Any]:
    """chat template 適用後（generation prompt 無し、assistant 込み）のトークン長。"""
    from transformers import AutoTokenizer  # 遅延 import

    tok = AutoTokenizer.from_pretrained(tokenizer_name)
    out: dict[str, Any] = {"tokenizer": tokenizer_name}
    all_total: list[int] = []
    all_prompt: list[int] = []
    for split in SPLITS:
        totals: list[int] = []
        prompts: list[int] = []
        for row in read_rows(data_dir / f"{split}.jsonl"):
            msgs = row["messages"]
            full = tok.apply_chat_template(msgs, tokenize=False)
            prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
            totals.append(len(tok(full, add_special_tokens=False)["input_ids"]))
            prompts.append(len(tok(prompt, add_special_tokens=False)["input_ids"]))
        all_total += totals
        all_prompt += prompts
        out[split] = {
            "rows": len(totals),
            "total_max": max(totals),
            "total_avg": round(sum(totals) / len(totals), 1),
            "prompt_max": max(prompts),
            "prompt_avg": round(sum(prompts) / len(prompts), 1),
        }
    out["all"] = {
        "rows": len(all_total),
        "total_max": max(all_total),
        "total_avg": round(sum(all_total) / len(all_total), 1),
        "prompt_max": max(all_prompt),
        "prompt_avg": round(sum(all_prompt) / len(all_prompt), 1),
    }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "data/navspec/lora" / SOURCE_DATASET)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/navspec/lora/stage4a_armament_verdict_v3")
    parser.add_argument("--db-dir", type=Path, default=ROOT / "data/navspec/armament")
    parser.add_argument("--tokenizer", default=None, help="指定するとトークン長統計を manifest に記録する")
    parser.add_argument("--token-stats-only", action="store_true", help="既存の output-dir に統計だけ追記する")
    args = parser.parse_args(argv)

    if args.token_stats_only:
        if not args.tokenizer:
            parser.error("--token-stats-only には --tokenizer が必要")
        manifest_path = args.output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = build_dataset(args.input_dir, args.output_dir, args.db_dir)
        manifest_path = args.output_dir / "manifest.json"
        c = manifest["consistency"]
        print(
            f"rows={manifest['rows']} splits={manifest['split_counts']} "
            f"consistency: both={c['both_match']}/{c['rows']} status={c['status_match']} codes={c['reason_codes_match']}"
        )
    if args.tokenizer:
        manifest["token_stats"] = token_stats(args.output_dir, args.tokenizer)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(json.dumps(manifest["token_stats"], ensure_ascii=False))
    print(f"-> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
