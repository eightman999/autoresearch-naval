"""Build navspec Stage 2.3 era-balanced LoRA dataset and era eval set.

Stage 2.2 で導入した厳格 reason 定義と短縮 teacher 出力を再利用しつつ、
速度重点による era 忘却を避けるため、era replay と era 対照ペアを明示的に
混ぜる。出力は Stage 2 から resume する実験用であり、既存 adapter や registry
は変更しない。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_lora_data import (  # noqa: E402
    SYSTEM_PROMPT,
    compact,
    conversation_text,
    generated_case,
    load_classic_eval_cases,
    make_request,
    request_prompt,
    split_for,
    write_jsonl,
)
from navspec_lora_data_stage2_2 import (  # noqa: E402
    MAX_MESSAGE_LEN,
    MAX_REASON,
    build_record as build_stage2_2_record,
    gen_hard_negative as gen_stage2_2_hard_negative,
    gen_nation_treaty_impossible,
    gen_speed_armor_range_impossible,
    gen_speed_armor_range_ok,
    gen_speed_power_impossible,
    gen_speed_power_ok,
    make_stage2_2_output,
    teacher_checks as stage2_2_teacher_checks,
)
from navspec_lora_stage2_schema import REASON_CODES, validate_stage2  # noqa: E402

DEFAULT_OUTPUT_DIR = ROOT / "data" / "navspec" / "lora" / "stage2_3_era_balanced_v0"
DEFAULT_EVAL_DIR = ROOT / "data" / "navspec" / "lora" / "stage2_3_era_eval_v0"
DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
ASSISTANT_MAX_CHARS = 620
ASSISTANT_P95_TARGET = 560
DESIGN_CONCEPT_MAX_CHARS = 80


def _base(
    index: int,
    category: str,
    nation: str,
    year: int,
    ship_type: str,
    role: str,
    constraints: dict[str, Any],
    request: str,
) -> dict[str, Any]:
    return {
        "id": f"s23_{category}_{index:05d}",
        "category": category,
        "nation": nation,
        "year": int(year),
        "ship_type": ship_type,
        "role": role,
        "constraints": constraints,
        "request": request,
    }


def _assistant_text(output: dict[str, Any]) -> str:
    return compact(output)


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    return int(math.ceil(statistics.quantiles(values, n=100, method="inclusive")[94]))


def _shorten_output(case: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Stage 2.3 では長文化を避けるため design_concept を固定短文にする。"""
    out = dict(output)
    if out.get("status") == "ok":
        out["design_concept"] = f"{case['year']}年{case['ship_type']}の実現可能案。"
    else:
        out["design_concept"] = "成立しない条件を緩和する。"
    return out


def make_stage2_3_output(case: dict[str, Any]) -> dict[str, Any]:
    return _shorten_output(case, make_stage2_2_output(case))


def teacher_checks(obj: Any, assistant_text: str) -> dict[str, bool]:
    checks = stage2_2_teacher_checks(obj)
    checks["design_concept_len"] = (
        isinstance(obj, dict)
        and isinstance(obj.get("design_concept"), str)
        and len(obj["design_concept"]) <= DESIGN_CONCEPT_MAX_CHARS
    )
    checks["assistant_len"] = len(assistant_text) <= ASSISTANT_MAX_CHARS
    if isinstance(obj, dict) and isinstance(obj.get("suggested_revision"), dict):
        # suggested_revision は必要フィールドのみ。速度/era複合でも4キー以内に抑える。
        checks["suggested_revision_minimal"] = obj.get("status") != "impossible" or len(obj["suggested_revision"]) <= 4
    else:
        checks["suggested_revision_minimal"] = False
    return checks


def build_record(case: dict[str, Any]) -> dict[str, Any]:
    request = make_request(case)
    output = make_stage2_3_output(case)
    errs = validate_stage2(output)
    if errs:
        raise ValueError(f"{case.get('id')} stage2.3 teacher invalid: {errs[:5]}")
    assistant = _assistant_text(output)
    checks = teacher_checks(output, assistant)
    failed = [key for key, ok in checks.items() if not ok]
    if failed:
        raise ValueError(f"{case.get('id')} teacher_checks failed: {failed}; assistant_len={len(assistant)}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": compact(request)},
        {"role": "assistant", "content": assistant},
    ]
    return {
        "id": case["id"],
        "category": case["category"],
        "nation": case["nation"],
        "year": int(case["year"]),
        "ship_type": case["ship_type"],
        "role": case.get("role", ""),
        "constraints": case.get("constraints", {}),
        "request": case["request"],
        "input": request,
        "expected_status": output["status"],
        "expected_reason_codes": [r["code"] for r in output["reason"]],
        "messages": messages,
        "text": conversation_text(messages),
        "output": output,
        "expected_output": output,
        "assistant_char_len": len(assistant),
        "split": case.get("split", "train"),
    }


# ---- Stage 2.3 training generators ---------------------------------------


def gen_replay_stage2(index: int) -> dict[str, Any]:
    if index % 3 == 0:
        cases = load_classic_eval_cases()
        case = dict(cases[index % len(cases)])
        case["id"] = f"s23_replay_classic_{index:05d}"
        case["category"] = f"replay_classic_{case['category']}"
        return case
    case = generated_case(index)
    case["id"] = f"s23_replay_{index:05d}"
    case["category"] = f"replay_{case['category']}"
    return case


def gen_schema_status_replay(index: int) -> dict[str, Any]:
    case = generated_case(20000 + index)
    case["id"] = f"s23_schema_status_replay_{index:05d}"
    case["category"] = f"schema_status_replay_{case['category']}"
    return case


def gen_era_equipment_impossible(index: int) -> dict[str, Any]:
    kind = index % 4
    if kind == 0:
        nation, year, st = "United Kingdom", 1890, "protected_cruiser"
        constraints = {"radar_required": True}
        label = "レーダー"
    elif kind == 1:
        nation, year, st = "Japan", 1905, "pre_dreadnought_battleship"
        constraints = {"dual_purpose_gun_required": True}
        label = "両用砲"
    elif kind == 2:
        nation, year, st = "United States", 1910, "light_cruiser"
        constraints = {"catapult_required": True}
        label = "航空機カタパルト"
    else:
        nation, year, st = "Germany", 1900, "armored_cruiser"
        constraints = {"diesel_required": True}
        label = "大型ディーゼル機関"
    request = f"{year}年の{nation}向け{st}に{label}を要求する。JSONのみ。"
    return _base(index, "era_equipment_impossible", nation, year, st, "early_electronics", constraints, request)


def gen_era_equipment_ok(index: int) -> dict[str, Any]:
    options = [
        ("United Kingdom", 1890, "protected_cruiser", "colonial_patrol", {}),
        ("Japan", 1905, "pre_dreadnought_battleship", "fleet_battle", {}),
        ("United States", 1918, "destroyer", "night_battle", {"speed_kt_min": 32}),
        ("France", 1935, "treaty_cruiser", "fleet_battle", {"speed_kt_min": 32, "main_gun_caliber_mm_min": 203}),
    ]
    nation, year, st, role, constraints = options[index % len(options)]
    request = request_prompt(nation, year, st, role, constraints, index)
    return _base(index, "era_equipment_ok", nation, year, st, role, dict(constraints), request)


def gen_era_speed_impossible(index: int) -> dict[str, Any]:
    options = [
        ("United Kingdom", 1895, "protected_cruiser", 40),
        ("Japan", 1905, "pre_dreadnought_battleship", 35),
        ("United States", 1915, "destroyer", 45),
        ("Germany", 1900, "armored_cruiser", 36),
    ]
    nation, year, st, speed = options[index % len(options)]
    constraints = {"speed_kt_min": speed}
    request = f"{year}年の{nation}向け{st}に{speed}ktを要求する。JSONのみ。"
    return _base(index, "era_speed_impossible", nation, year, st, "speed_trial", constraints, request)


def gen_era_speed_ok(index: int) -> dict[str, Any]:
    options = [
        ("United Kingdom", 1905, "pre_dreadnought_battleship", 21),
        ("Japan", 1918, "destroyer", 32),
        ("United States", 1930, "light_cruiser", 33),
        ("Germany", 1940, "fast_battleship", 30),
    ]
    nation, year, st, speed = options[index % len(options)]
    constraints = {"speed_kt_min": speed}
    request = f"{year}年の{nation}向け{st}を{speed}ktで設計せよ。JSONのみ。"
    return _base(index, "era_speed_ok", nation, year, st, "fleet_battle", constraints, request)


def gen_era_speed_composite(index: int) -> dict[str, Any]:
    options = [
        ("United Kingdom", 1895, "protected_cruiser", 40, "radar_required", "レーダー"),
        ("Japan", 1905, "pre_dreadnought_battleship", 35, "dual_purpose_gun_required", "両用砲"),
        ("United States", 1915, "destroyer", 45, "catapult_required", "航空機カタパルト"),
        ("Germany", 1900, "armored_cruiser", 36, "diesel_required", "大型ディーゼル機関"),
    ]
    nation, year, st, speed, key, label = options[index % len(options)]
    constraints = {"speed_kt_min": speed, key: True}
    request = f"{year}年の{nation}向け{st}に{speed}ktと{label}を同時要求する。JSONのみ。"
    return _base(index, "era_speed_composite_impossible", nation, year, st, "early_fast_ship", constraints, request)


def gen_era_nation_impossible(index: int) -> dict[str, Any]:
    options = [
        ("Chile", 1910, "armored_cruiser", {"speed_kt_min": 33, "standard_displacement_t_min": 18000}),
        ("Thailand", 1915, "light_cruiser", {"speed_kt_min": 34, "main_gun_caliber_mm_min": 180}),
        ("Portugal", 1908, "pre_dreadnought_battleship", {"main_gun_caliber_mm_min": 305}),
        ("Egypt", 1912, "dreadnought_battleship", {"standard_displacement_t_min": 25000}),
    ]
    nation, year, st, constraints = options[index % len(options)]
    request = f"{year}年の{nation}が{st}を高性能仕様で自主建造する。JSONのみ。"
    return _base(index, "era_nation_impossible", nation, year, st, "regional_flagship", dict(constraints), request)


def gen_era_replay(index: int) -> dict[str, Any]:
    gens = [
        gen_era_equipment_impossible,
        gen_era_equipment_ok,
        gen_era_speed_impossible,
        gen_era_speed_ok,
        gen_era_speed_composite,
        gen_era_nation_impossible,
    ]
    gen = gens[index % len(gens)]
    case = gen(index)
    case["id"] = f"s23_era_replay_{index:05d}"
    case["category"] = f"era_replay_{case['category']}"
    return case


def gen_hard_negative(index: int) -> dict[str, Any]:
    if index % 3 == 0:
        return gen_era_speed_ok(index)
    case = gen_stage2_2_hard_negative(index)
    case["id"] = f"s23_hard_negative_{index:05d}"
    case["category"] = "hard_negative_ok"
    return case


def build_cases(count: int) -> list[dict[str, Any]]:
    ratios = {
        "replay_stage2": 0.30,
        "era_replay": 0.20,
        "schema_status_replay": 0.15,
        "speed_power": 0.10,
        "speed_armor_range": 0.10,
        "era_speed_composite": 0.05,
        "nation_treaty": 0.05,
        "hard_negative": 0.05,
    }
    targets = {key: int(count * ratio) for key, ratio in ratios.items()}
    targets["hard_negative"] += count - sum(targets.values())

    cases: list[dict[str, Any]] = []
    for i in range(targets["replay_stage2"]):
        cases.append(gen_replay_stage2(i))
    for i in range(targets["era_replay"]):
        cases.append(gen_era_replay(i))
    for i in range(targets["schema_status_replay"]):
        cases.append(gen_schema_status_replay(i))
    half = targets["speed_power"] // 2
    for i in range(half):
        cases.append(gen_speed_power_impossible(i))
    for i in range(targets["speed_power"] - half):
        cases.append(gen_speed_power_ok(i))
    half = targets["speed_armor_range"] // 2
    for i in range(half):
        cases.append(gen_speed_armor_range_impossible(i))
    for i in range(targets["speed_armor_range"] - half):
        cases.append(gen_speed_armor_range_ok(i))
    for i in range(targets["era_speed_composite"]):
        cases.append(gen_era_speed_composite(i))
    for i in range(targets["nation_treaty"]):
        cases.append(gen_nation_treaty_impossible(i))
    for i in range(targets["hard_negative"]):
        cases.append(gen_hard_negative(i))
    return cases


def assign_splits(cases: list[dict[str, Any]]) -> None:
    for index, case in enumerate(cases):
        case["split"] = split_for(case, index)


def assistant_length_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lengths = [int(row["assistant_char_len"]) for row in rows]
    token_lengths = [len(row["messages"][-1]["content"].encode("utf-8")) for row in rows]
    return {
        "assistant_chars_avg": round(statistics.mean(lengths), 2) if lengths else 0,
        "assistant_chars_p95": _p95(lengths),
        "assistant_chars_max": max(lengths) if lengths else 0,
        "assistant_utf8_bytes_max": max(token_lengths) if token_lengths else 0,
        "assistant_max_chars_limit": ASSISTANT_MAX_CHARS,
        "assistant_p95_target": ASSISTANT_P95_TARGET,
        "assistant_len_pass": bool(lengths) and max(lengths) <= ASSISTANT_MAX_CHARS,
        "assistant_p95_target_pass": bool(lengths) and _p95(lengths) <= ASSISTANT_P95_TARGET,
    }


def write_splits(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for row in rows:
        by_split[row["split"]].append(row)
    for split in ("train", "validation", "test"):
        write_jsonl(output_dir / f"{split}.jsonl", by_split[split])
    write_jsonl(output_dir / "valid.jsonl", by_split["validation"])
    write_jsonl(output_dir / "all.jsonl", rows)


def write_manifest(
    output_dir: Path,
    rows: list[dict[str, Any]],
    status_counts: Counter,
    code_counts: Counter,
    category_counts: Counter,
    requested: int,
    token_checks: dict[str, Any] | None = None,
) -> None:
    split_counts = Counter(row["split"] for row in rows)
    manifest = {
        "dataset": output_dir.name,
        "schema": "schemas/navspec_lora_candidate_stage2.schema.json",
        "schema_version": "navspec.lora_candidate.v1",
        "teacher": "Stage 2.2 strict classifier + Stage 2.3 era-balanced generators",
        "rows": len(rows),
        "requested": requested,
        "split_counts": dict(sorted(split_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "reason_code_counts": dict(sorted(code_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "canonical_order": REASON_CODES,
        "reason_max": MAX_REASON,
        "message_max_len": MAX_MESSAGE_LEN,
        "design_concept_max_len": DESIGN_CONCEPT_MAX_CHARS,
        "assistant_length_stats": assistant_length_stats(rows),
        "token_checks": token_checks or {},
        "composition_targets": {
            "replay_stage2": 1500,
            "era_replay": 1000,
            "schema_status_replay": 750,
            "speed_power": 500,
            "speed_armor_range": 500,
            "era_speed_composite": 250,
            "nation_treaty": 250,
            "hard_negative": 250,
        },
        "system_prompt": SYSTEM_PROMPT,
        "format": "mlx_lm ChatDataset/messages, compact JSON sort_keys=True, response-only mask expected",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sample_token_checks(rows: list[dict[str, Any]], model: str, limit: int) -> dict[str, Any]:
    if limit <= 0:
        return {"enabled": False}
    try:
        from mlx_lm_compat import patch_transformers_5_tokenizer_register

        patch_transformers_5_tokenizer_register()
        from mlx_lm.tuner.datasets import ChatDataset
        from transformers import AutoTokenizer
    except ImportError as exc:
        return {"enabled": True, "passed": False, "error": f"import failed: {exc}"}

    tokenizer = AutoTokenizer.from_pretrained(model)
    checked: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    eos = tokenizer.eos_token_id
    for row in rows[:limit]:
        ds = ChatDataset([row], tokenizer, chat_key="messages", mask_prompt=True)
        tokens, offset = ds.process(row)
        unmasked = tokens[offset:]
        unmasked_text = tokenizer.decode(unmasked)
        ok = (
            eos in unmasked
            and offset > 0
            and unmasked_text.lstrip().startswith("{")
            and row["messages"][-1]["content"].startswith("{")
        )
        item = {
            "id": row["id"],
            "offset": offset,
            "tokens": len(tokens),
            "eos": eos,
            "eos_in_unmasked": eos in unmasked,
            "unmasked_head": unmasked_text[:40],
            "ok": ok,
        }
        checked.append(item)
        if not ok:
            failed.append(item)
    return {
        "enabled": True,
        "sample_count": len(checked),
        "passed": not failed,
        "failed": failed,
        "checked": checked,
    }


def build_dataset(count: int, output_dir: Path, token_check_limit: int, model: str) -> list[dict[str, Any]]:
    cases = build_cases(count)
    assign_splits(cases)
    rows: list[dict[str, Any]] = []
    status_counts: Counter = Counter()
    code_counts: Counter = Counter()
    category_counts: Counter = Counter()
    for case in cases:
        row = build_record(case)
        rows.append(row)
        status_counts[row["expected_status"]] += 1
        category_counts[row["category"]] += 1
        for code in row["expected_reason_codes"]:
            code_counts[code] += 1
    token_checks = sample_token_checks(rows, model, token_check_limit)
    if token_checks.get("enabled") and not token_checks.get("passed"):
        raise ValueError(f"token checks failed: {token_checks}")
    write_splits(output_dir, rows)
    write_manifest(output_dir, rows, status_counts, code_counts, category_counts, count, token_checks)
    return rows


# ---- Stage 2.3 era eval set ----------------------------------------------


def build_era_eval(count: int, output_dir: Path) -> list[dict[str, Any]]:
    plan = [
        ("eval_era_equipment_impossible", 80, gen_era_equipment_impossible),
        ("eval_era_equipment_ok", 60, gen_era_equipment_ok),
        ("eval_era_speed_impossible", 80, gen_era_speed_impossible),
        ("eval_era_speed_ok", 60, gen_era_speed_ok),
        ("eval_era_nation_impossible", 50, gen_era_nation_impossible),
        ("eval_era_speed_composite", 50, gen_era_speed_composite),
        ("eval_old_ship_type_ok", 20, gen_era_equipment_ok),
    ]
    if count != 400:
        scale = count / 400
        plan = [(cat, max(1, int(round(n * scale))), gen) for cat, n, gen in plan]
        plan[-1] = (plan[-1][0], plan[-1][1] + count - sum(n for _, n, _ in plan), plan[-1][2])

    cases: list[dict[str, Any]] = []
    for category, n, gen in plan:
        for i in range(n):
            case = gen(9000 + i)
            case["id"] = f"{category}_{i:04d}"
            case["category"] = category
            case["split"] = "eval"
            cases.append(case)

    rows = [build_record(case) for case in cases]
    for row in rows:
        row["split"] = "eval"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "cases.jsonl", rows)
    write_manifest(
        output_dir,
        rows,
        Counter(row["expected_status"] for row in rows),
        Counter(code for row in rows for code in row["expected_reason_codes"]),
        Counter(row["category"] for row in rows),
        count,
        {"enabled": False, "reason": "eval set only"},
    )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build navspec Stage 2.3 era-balanced dataset")
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--eval", action="store_true", help="Stage 2.3 era eval 400 を生成する")
    parser.add_argument("--eval-count", type=int, default=400)
    parser.add_argument("--eval-dir", type=Path, default=DEFAULT_EVAL_DIR)
    parser.add_argument("--token-check-limit", type=int, default=0, help="train rows の EOS/mask 検査件数")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if args.eval:
        rows = build_era_eval(args.eval_count, args.eval_dir)
        manifest = json.loads((args.eval_dir / "manifest.json").read_text(encoding="utf-8"))
        print("dataset: stage2_3_era_eval_v0")
        print(f"rows: {len(rows)}")
        print(f"output_dir: {args.eval_dir}")
        print("status:", manifest["status_counts"])
        print("reason_codes:", manifest["reason_code_counts"])
        print("categories:", manifest["category_counts"])
        print("assistant_length_stats:", manifest["assistant_length_stats"])
        return

    rows = build_dataset(args.count, args.output_dir, args.token_check_limit, args.model)
    manifest = json.loads((args.output_dir / "manifest.json").read_text(encoding="utf-8"))
    print("dataset: stage2_3_era_balanced_v0")
    print(f"rows: {len(rows)}")
    print(f"output_dir: {args.output_dir}")
    print("splits:", manifest["split_counts"])
    print("status:", manifest["status_counts"])
    print("reason_codes:", manifest["reason_code_counts"])
    print("top categories:", dict(sorted(manifest["category_counts"].items(), key=lambda kv: kv[1], reverse=True)[:16]))
    print("assistant_length_stats:", manifest["assistant_length_stats"])
    print("token_checks:", manifest["token_checks"])


if __name__ == "__main__":
    main()
