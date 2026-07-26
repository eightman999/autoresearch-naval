#!/usr/bin/env python3
"""Build navspec Stage 4a armament **critique** datasets.

Commit D の生成タスク (`navspec_lora_data_armament_stage4a.py`) では、
14個ある armament reason code のうち7個が**1件も出ない**。
retriever が常に整合した candidate しか構築しないためで、
「生成」タスクでは原理的に到達できない (詳細は
`docs/navspec_armament_stage4a_teacher.md` §7)。

そこでタスクを裏返す。**候補を与えて validator の判定を当てさせる**批評タスクにすれば、
候補側を壊すことで全 code に到達できる。

  入力: context + armament candidate (健全なものと、意図的に壊したものが混在)
  出力: validator の判定 (status / reason / warnings / suggested_revision)

出力契約は生成タスクと同じ `navspec.armament_teacher.v1` を使う。
違いは request の `task` が `critique_navspec_armament` になり、
`candidate` が入ることだけ。契約を1本に保つことで、生成側の学習と
評価コードをそのまま使い回せる。

teacher は生成タスクと同じく **LLM を使わない**。
`validate_armament()` の出力をそのまま写すだけ。

使用法::

    uv run python tools/navspec_lora_data_armament_critique_stage4a.py \\
        --count 900 --output-dir data/navspec/lora/stage4a_armament_critique_v0
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_armament_validator import (  # noqa: E402
    aggregate_main_gun_fields,
    load_db,
    validate_armament,
)
from navspec_lora_data import compact, conversation_text, write_jsonl  # noqa: E402
from navspec_lora_data_armament_stage4a import (  # noqa: E402
    DEFAULT_DATA_DIR,
    HULL_DEFAULTS,
    ROLES,
    SCHEMA_VERSION,
    TOP_LEVEL_OUTPUT_FIELDS,
    _hull_for,
    context_of,
    feasible_combos,
    retrieve_with_fallback,
    split_for,
    suggest_revision,
    validate_teacher,
)

SYSTEM_PROMPT = (
    "You are navspec-armament-review, a naval main battery reviewer. "
    "You are given a ship context and a proposed main battery. "
    "Return only JSON matching navspec.armament_teacher.v1. "
    "Do not repeat the user request. "
    "The top-level keys must be "
    "schema_version,status,armament,reason,warnings,suggested_revision,design_rationale,confidence. "
    "Set status=ok and echo the accepted battery when the proposal is sound. "
    "Set status=impossible with armament=null and reason[] when the proposal violates "
    "an armament rule. Report non-fatal concerns in warnings[]."
)

# anachronistic_mount は 2026-07-26 に復活させた。
# tools/navspec_armament_mount_years.py が mounts.jsonl の year_introduced を
# NSDB から埋めたので、armament.mount_unavailable_for_era が踏めるようになった。
RATIOS = {
    "clean": 0.32,
    "incompatible_mount": 0.11,
    "foreign_mount": 0.11,
    "anachronistic_mount": 0.10,
    "bad_positions": 0.11,
    "stacked_mounts": 0.09,
    "unknown_fire_control": 0.09,
    "early_radar_fire_control": 0.08,
    "oversized_magazine": 0.09,
}


# ----------------------------------------------------------------------------
# 摂動（candidate を意図的に壊す）
# ----------------------------------------------------------------------------
# どの摂動が validator のどの分岐を踏むかは navspec_armament_validator.py の
# 実装で確認済み。「たぶんこの code が出るはず」で書かず、teacher は必ず
# validator の実際の出力から作る。

def _battery(candidate: dict[str, Any]) -> dict[str, Any]:
    return candidate["main_batteries"][0]


def perturb_incompatible_mount(candidate, context, db, rng):
    """weapon_to_mount に載っていない mount に差し替える → weapon_mount_incompatible"""
    battery = _battery(candidate)
    allowed = set(db["compatibility_rules"].get("weapon_to_mount", {})
                  .get(battery["weapon_id"], []))
    others = [m for mid, m in db["mounts"].items()
              if mid not in allowed and m.get("nation") == context.get("nation")]
    if not others:
        return None
    mount = rng.choice(others)
    battery["mount_id"] = mount["mount_id"]
    battery["barrels_per_mount"] = mount.get("barrels_per_mount", 1) or 1
    battery["total_barrels"] = battery["mount_count"] * battery["barrels_per_mount"]
    return candidate


def perturb_foreign_mount(candidate, context, db, rng):
    """他国の mount に差し替える → mount_unavailable_for_nation"""
    battery = _battery(candidate)
    others = [m for m in db["mounts"].values() if m.get("nation") != context.get("nation")]
    if not others:
        return None
    mount = rng.choice(others)
    battery["mount_id"] = mount["mount_id"]
    battery["barrels_per_mount"] = mount.get("barrels_per_mount", 1) or 1
    battery["total_barrels"] = battery["mount_count"] * battery["barrels_per_mount"]
    return candidate


def perturb_anachronistic_mount(candidate, context, db, rng):
    """context の年より後に就役した mount に差し替える → mount_unavailable_for_era

    砲塔の就役年は tools/navspec_armament_mount_years.py が NSDB から入れたもの
    （出典は各レコードの year_source）。年の入っていない砲塔3件は候補にならない。
    """
    battery = _battery(candidate)
    year = context.get("year")
    later = [m for m in db["mounts"].values()
             if m.get("year_introduced") and year and m["year_introduced"] > year]
    if not later:
        return None
    mount = min(later, key=lambda m: m["year_introduced"])
    battery["mount_id"] = mount["mount_id"]
    battery["barrels_per_mount"] = mount.get("barrels_per_mount", 1) or 1
    battery["total_barrels"] = battery["mount_count"] * battery["barrels_per_mount"]
    return candidate


def perturb_bad_positions(candidate, context, db, rng):
    """存在しない位置記号、または重複した位置にする → firing_arc_conflict"""
    battery = _battery(candidate)
    n = battery["mount_count"]
    if rng.random() < 0.5:
        battery["positions"] = ["A"] * n if n > 1 else ["K"]
    else:
        battery["positions"] = [rng.choice(["K", "M", "1"]) for _ in range(n)]
    return candidate


def perturb_stacked_mounts(candidate, context, db, rng):
    """同一位置に複数の砲塔を積む → blast_interference (+ 重複で firing_arc_conflict)"""
    battery = _battery(candidate)
    if battery["mount_count"] < 2:
        battery["mount_count"] = 2
        battery["total_barrels"] = 2 * battery["barrels_per_mount"]
    battery["positions"] = ["A"] * battery["mount_count"]
    return candidate


def perturb_unknown_fire_control(candidate, context, db, rng):
    """DB に無い fire_control_id を指定する → fire_control_unavailable (error)"""
    _battery(candidate)["fire_control_id"] = rng.choice(
        ["XX_UNKNOWN_DIRECTOR", "ZZ_NOT_A_DIRECTOR", "JP_TYPE00_PHANTOM"]
    )
    return candidate


def perturb_early_radar_fire_control(candidate, context, db, rng):
    """1938年より前にレーダー射撃指揮装置を積む → fire_control_unavailable (warn)"""
    radar_fcs = [fc for fc in db["fire_control"].values() if fc.get("radar")]
    if not radar_fcs:
        return None
    _battery(candidate)["fire_control_id"] = rng.choice(radar_fcs)["fire_control_id"]
    return candidate


def perturb_oversized_magazine(candidate, context, db, rng):
    """弾薬搭載数を過大にする → magazine_volume_insufficient (warn)"""
    _battery(candidate)["ammo_per_gun"] = rng.choice([4000, 8000, 15000])
    return candidate


PERTURBATIONS = {
    "incompatible_mount": perturb_incompatible_mount,
    "foreign_mount": perturb_foreign_mount,
    "anachronistic_mount": perturb_anachronistic_mount,
    "bad_positions": perturb_bad_positions,
    "stacked_mounts": perturb_stacked_mounts,
    "unknown_fire_control": perturb_unknown_fire_control,
    "early_radar_fire_control": perturb_early_radar_fire_control,
    "oversized_magazine": perturb_oversized_magazine,
}

# early_radar_fire_control は「1938年より前」でないと踏めない
EARLY_YEARS_FOR_RADAR = [1916, 1920, 1925, 1930, 1934, 1936]


def _max_mount_year(db: dict[str, Any]) -> int | None:
    years = [m["year_introduced"] for m in db["mounts"].values() if m.get("year_introduced")]
    return max(years) if years else None


# ----------------------------------------------------------------------------
# case / teacher
# ----------------------------------------------------------------------------

def build_cases(count: int, db: dict[str, Any], seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    combos = feasible_combos(db)
    if not combos:
        raise SystemExit("armament DB から成立する (nation, ship_type, year) が1つも作れません")
    cases: list[dict[str, Any]] = []
    for category, ratio in RATIOS.items():
        n = max(1, int(count * ratio))
        for i in range(n):
            nation, ship_type, year = combos[(i * 7 + len(cases)) % len(combos)]
            if category == "anachronistic_mount":
                # 「その年より後に就役した砲塔」が無いと摂動が作れないので、
                # 最も遅い砲塔より前の年に寄せる
                cutoff = _max_mount_year(db)
                early = [c for c in combos if cutoff and c[2] < cutoff]
                if early:
                    nation, ship_type, year = early[(i * 3) % len(early)]
            elif category == "early_radar_fire_control":
                # 1938年以降だと warn を踏まないので、成立する早い年に寄せる
                early = [c for c in combos
                         if c[0] == nation and c[1] == ship_type
                         and c[2] in EARLY_YEARS_FOR_RADAR]
                if early:
                    nation, ship_type, year = rng.choice(early)
            disp, beam = _hull_for(ship_type, rng)
            cases.append({
                "id": f"stage4a_crit_{category}_{i:05d}",
                "category": category,
                "nation": nation,
                "year": year,
                "ship_type": ship_type,
                "role": rng.choice(ROLES),
                "displacement_t": disp,
                "beam_m": beam,
            })
    rng.shuffle(cases)
    return cases


def make_candidate(case: dict[str, Any], db: dict[str, Any],
                   rng: random.Random) -> dict[str, Any] | None:
    """健全な candidate を作り、category に応じて壊す。"""
    context = context_of(case)
    candidate = retrieve_with_fallback(context, db, rng)
    if candidate is None:
        return None
    if case["category"] == "clean":
        return candidate
    perturb = PERTURBATIONS[case["category"]]
    return perturb(copy.deepcopy(candidate), context, db, rng)


def make_output(case: dict[str, Any], candidate: dict[str, Any],
                db: dict[str, Any]) -> dict[str, Any]:
    """teacher は validator の判定そのもの。推測で code を決めない。"""
    context = context_of(case)
    verdict = validate_armament(candidate, context, db)
    errors = [i for i in verdict.get("issues", []) if i.get("severity") == "error"]
    warns = [{"code": i["code"], "message": i["message"]}
             for i in verdict.get("issues", []) if i.get("severity") == "warn"]

    if verdict.get("status") == "impossible" or errors:
        reasons = [{"code": i["code"], "message": i["message"]} for i in errors]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "impossible",
            "armament": None,
            "reason": reasons,
            "warnings": warns,
            "suggested_revision": suggest_revision(case, db, reasons),
            "design_rationale": "",
            "confidence": 0.9,
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "armament": {
            "main_batteries": candidate["main_batteries"],
            "aggregate": aggregate_main_gun_fields(candidate),
        },
        "reason": [],
        "warnings": warns,
        "suggested_revision": None,
        "design_rationale": candidate.get("design_rationale", ""),
        "confidence": round(0.85 - 0.1 * min(len(warns), 3), 3),
    }


def make_request(case: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "critique_navspec_armament",
        "output_schema": SCHEMA_VERSION,
        "response_format": {
            "json_only": True,
            "top_level_required": TOP_LEVEL_OUTPUT_FIELDS,
            "armament_must_be_null_when_status_impossible": True,
        },
        "nation": case["nation"],
        "year": case["year"],
        "ship_type": case["ship_type"],
        "role": case["role"],
        "hull": {
            "displacement_t": case.get("displacement_t"),
            "beam_m": case.get("beam_m"),
        },
        "candidate": candidate,
    }


def build_record(case: dict[str, Any], db: dict[str, Any],
                 rng: random.Random) -> dict[str, Any] | None:
    candidate = make_candidate(case, db, rng)
    if candidate is None:
        return None
    request = make_request(case, candidate)
    output = make_output(case, candidate, db)
    errors = validate_teacher(output)
    if errors:
        raise ValueError(f"{case['id']} critique teacher invalid: {errors[:5]}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": compact(request)},
        {"role": "assistant", "content": compact(output)},
    ]
    return {
        "id": case["id"],
        "category": case["category"],
        "nation": case["nation"],
        "year": case["year"],
        "ship_type": case["ship_type"],
        "role": case["role"],
        "displacement_t": case.get("displacement_t"),
        "beam_m": case.get("beam_m"),
        "input": request,
        "expected_status": output["status"],
        "expected_reason_codes": [i["code"] for i in output["reason"]],
        "expected_warning_codes": [i["code"] for i in output["warnings"]],
        "messages": messages,
        "text": conversation_text(messages),
        "output": output,
        "expected_output": output,
        "split": case["split"],
    }


def build_dataset(count: int, output_dir: Path, data_dir: Path, seed: int = 42) -> list[dict]:
    db = load_db(data_dir)
    cases = build_cases(count, db, seed=seed)
    for index, case in enumerate(cases):
        case["split"] = split_for(case, index)

    rng = random.Random(seed + 1)
    rows: list[dict[str, Any]] = []
    skipped = 0
    status_counts: Counter = Counter()
    category_counts: Counter = Counter()
    reason_counts: Counter = Counter()
    warning_counts: Counter = Counter()

    for case in cases:
        row = build_record(case, db, rng)
        if row is None:
            # 摂動の材料が DB に無かったケース。黙って落とさず件数を残す
            skipped += 1
            continue
        rows.append(row)
        status_counts[row["expected_status"]] += 1
        category_counts[row["category"]] += 1
        for code in row["expected_reason_codes"]:
            reason_counts[code] += 1
        for code in row["expected_warning_codes"]:
            warning_counts[code] += 1

    output_dir.mkdir(parents=True, exist_ok=True)
    by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for row in rows:
        by_split[row["split"]].append(row)
    write_jsonl(output_dir / "train.jsonl", by_split["train"])
    write_jsonl(output_dir / "valid.jsonl", by_split["validation"])
    write_jsonl(output_dir / "test.jsonl", by_split["test"])

    manifest = {
        "dataset": "stage4a_armament_critique_v0",
        "schema": "schemas/navspec_armament_teacher_stage4a.schema.json",
        "schema_version": SCHEMA_VERSION,
        "task": "critique_navspec_armament",
        "teacher": (
            "navspec_armament_validator.validate_armament の判定をそのまま写す "
            "(no LLM in the loop)"
        ),
        "purpose": (
            "生成タスクでは到達できない reason code を、候補を壊して踏ませることで学習させる"
        ),
        "rows": len(rows),
        "requested": count,
        "skipped_no_material": skipped,
        "seed": seed,
        "split_counts": dict(sorted(Counter(r["split"] for r in rows).items())),
        "status_counts": dict(sorted(status_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "warning_code_counts": dict(sorted(warning_counts.items())),
        "system_prompt": SYSTEM_PROMPT,
        "format": "mlx_lm ChatDataset/messages, compact JSON sort_keys=True",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--count", type=int, default=900)
    p.add_argument("--output-dir", type=Path,
                   default=ROOT / "data" / "navspec" / "lora"
                   / "stage4a_armament_critique_v0")
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    rows = build_dataset(args.count, args.output_dir, args.data_dir, seed=args.seed)
    print(f"rows={len(rows)} "
          f"status={dict(Counter(r['expected_status'] for r in rows))} "
          f"splits={dict(Counter(r['split'] for r in rows))}")
    print(f"-> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
