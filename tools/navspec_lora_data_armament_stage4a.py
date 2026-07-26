#!/usr/bin/env python3
"""Build navspec Stage 4a (armament) LoRA teacher datasets.

STAGE4_PROGRESS.md §2 Phase 2-3 の Commit D。

teacher の作り方は Stage 2/3 と同じ思想で、**LLMには一切頼らない**:

  1. `navspec_armament_retriever.retrieve_armament()` が DB seed から candidate を作る
  2. `navspec_armament_validator.validate_armament()` がそれを判定する
  3. 判定が ok なら status=ok、error が出たら status=impossible + その reason code

つまり classic のルールエンジンがそのまま teacher であり、
生徒モデルはルールエンジンの入出力関係を学ぶ。Stage 2 の
「classic-ruled impossible」と同じ構図を armament レイヤーに持ち込んだもの。

出力契約は `schemas/navspec_armament_teacher_stage4a.schema.json`
(`navspec.armament_teacher.v1`)。トップレベルは Stage 2 の
navspec.lora_candidate.v1 に揃えてあり、`spec` を `armament` に、
`design_concept` を `design_rationale` に置き換えただけの形。

語彙について: nation / ship_type は **armament DB 側の語彙**をそのまま使う
(DB は JP/UK/US/DE/FR/IT の6カ国、battleship/battlecruiser/heavy_cruiser/
light_cruiser/destroyer/coastal_defense の6艦種、砲は1911-1944年)。
Stage 2 の SHIP_TYPES へ写像するエイリアス表は作らない。DB に無い国・艦種・年代は
そのまま「不可能ケース」の材料として使う。

使用法::

    uv run python tools/navspec_lora_data_armament_stage4a.py \\
        --count 1200 --output-dir data/navspec/lora/stage4a_armament_v0
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_armament_retriever import (  # noqa: E402
    build_candidate,
    estimate_mount_count,
    generate_positions,
    retrieve_available_mounts,
    retrieve_available_weapons,
    retrieve_armament,
    retrieve_armament_variations,
    select_fire_control,
)
from navspec_armament_validator import (  # noqa: E402
    aggregate_main_gun_fields,
    load_db,
    validate_armament,
)
from navspec_lora_data import compact, conversation_text, write_jsonl  # noqa: E402

SCHEMA_VERSION = "navspec.armament_teacher.v1"
CANDIDATE_SCHEMA_VERSION = "navspec.armament_candidate.v1"
DEFAULT_DATA_DIR = ROOT / "data" / "navspec" / "armament"
DEFAULT_SCHEMA = ROOT / "schemas" / "navspec_armament_teacher_stage4a.schema.json"

SYSTEM_PROMPT = (
    "You are navspec-armament, a naval main battery designer. "
    "Return only JSON matching navspec.armament_teacher.v1. "
    "Do not repeat the user request. "
    "The top-level keys must be "
    "schema_version,status,armament,reason,warnings,suggested_revision,design_rationale,confidence. "
    "Use status=impossible with armament=null and reason[] when no main battery can be "
    "fitted from the historical armament database for the requested nation, year, "
    "ship type and hull constraints."
)

TOP_LEVEL_OUTPUT_FIELDS = [
    "schema_version",
    "status",
    "armament",
    "reason",
    "warnings",
    "suggested_revision",
    "design_rationale",
    "confidence",
]

# --- 語彙（DB 側に合わせる） -------------------------------------------------
DB_NATIONS = ["JP", "UK", "US", "DE", "FR", "IT"]
# DB に砲を持たない国。nation 不成立ケースの材料
UNCOVERED_NATIONS = ["NL", "AR", "BR", "SE", "PT", "TH", "CN", "CL", "EG", "NO"]

DB_SHIP_TYPES = [
    "battleship",
    "battlecruiser",
    "heavy_cruiser",
    "light_cruiser",
    "destroyer",
    "coastal_defense",
]
# compatibility_rules.caliber_min_max_by_ship_type に無い艦種
UNCOVERED_SHIP_TYPES = ["patrol_boat", "gunboat", "destroyer_escort", "ship_of_the_line"]

COVERED_YEARS = [1912, 1916, 1920, 1925, 1930, 1934, 1936, 1938, 1941, 1943, 1944, 1950]
EARLY_YEARS = [1860, 1880, 1895, 1900, 1905, 1908]

ROLES = [
    "fleet_battle",
    "night_battle",
    "long_range_scout",
    "carrier_screen",
    "convoy_escort",
    "coastal_defense",
    "commerce_raiding",
    "regional_flagship",
]

# ship_type ごとの標準的な排水量・全幅（不可能ケースを作るときはここから外す）
HULL_DEFAULTS: dict[str, tuple[float, float]] = {
    "battleship": (38000.0, 33.0),
    "battlecruiser": (32000.0, 31.0),
    "heavy_cruiser": (12000.0, 20.0),
    "light_cruiser": (7000.0, 16.0),
    "destroyer": (2000.0, 10.5),
    "coastal_defense": (4000.0, 15.0),
}

RATIOS = {
    "normal": 0.34,
    "variation": 0.20,
    "ship_type_contrast": 0.10,
    "era_unavailable": 0.10,
    "nation_unavailable": 0.08,
    "ship_type_uncovered": 0.06,
    "hull_too_small": 0.12,
}

# split 振り分け。学習に出てこない国・艦種を test に寄せる
TEST_NATIONS = {"FR", "IT"}
TEST_SHIP_TYPES = {"coastal_defense", "ship_of_the_line", "gunboat"}
VALIDATION_ROLES = {"carrier_screen", "convoy_escort"}


# ----------------------------------------------------------------------------
# case 生成
# ----------------------------------------------------------------------------

def _base_case(
    index: int,
    category: str,
    nation: str,
    year: int,
    ship_type: str,
    role: str,
    displacement_t: float | None,
    beam_m: float | None,
) -> dict[str, Any]:
    return {
        "id": f"stage4a_{category}_{index:05d}",
        "category": category,
        "nation": nation,
        "year": int(year),
        "ship_type": ship_type,
        "role": role,
        "displacement_t": displacement_t,
        "beam_m": beam_m,
    }


def _hull_for(ship_type: str, rng: random.Random, scale: float = 1.0) -> tuple[float, float]:
    disp, beam = HULL_DEFAULTS.get(ship_type, (10000.0, 18.0))
    disp = round(disp * scale * rng.uniform(0.9, 1.1), 1)
    beam = round(beam * (scale ** 0.34) * rng.uniform(0.95, 1.05), 2)
    return disp, beam


def feasible_combos(db: dict[str, Any]) -> list[tuple[str, str, int]]:
    """DB 上で実際に主砲が引ける (nation, ship_type, year) を列挙する。

    「normal」を名前どおり成立ケースにするために必要。DB は59門・6カ国しか
    無く、たとえば US の最古の主砲は1926年なので、素朴に年を振ると normal の
    2〜3割が impossible になってしまう（判定自体は正しいが、カテゴリの意味が壊れる）。
    """
    caliber_ranges = db.get("compatibility_rules", {}).get("caliber_min_max_by_ship_type", {})
    out: list[tuple[str, str, int]] = []
    for nation in DB_NATIONS:
        for ship_type in DB_SHIP_TYPES:
            st_range = caliber_ranges.get(ship_type)
            if not st_range:
                continue
            lo, hi = st_range.get("min"), st_range.get("max")
            for year in COVERED_YEARS:
                ok = any(
                    not w.get("fictional")
                    and w.get("nation") == nation
                    and w.get("year_introduced")
                    and w["year_introduced"] <= year
                    and w.get("caliber_mm") is not None
                    and lo <= w["caliber_mm"] <= hi
                    and retrieve_available_mounts(
                        w["weapon_id"],
                        {
                            "displacement_t": HULL_DEFAULTS.get(
                                ship_type, (10000.0, 18.0))[0],
                            # 砲塔にも就役年があるので year を渡さないと
                            # 「砲は在るが砲塔がまだ無い」年を成立扱いしてしまう
                            "year": year,
                            "nation": nation,
                        },
                        db,
                    )
                    for w in db["weapons"].values()
                )
                if ok:
                    out.append((nation, ship_type, year))
    return out


def gen_normal(i: int, rng: random.Random, combos: list[tuple[str, str, int]]) -> dict[str, Any]:
    nation, ship_type, year = combos[i % len(combos)]
    disp, beam = _hull_for(ship_type, rng)
    return _base_case(i, "normal", nation, year, ship_type, rng.choice(ROLES), disp, beam)


def gen_variation(i: int, rng: random.Random, combos: list[tuple[str, str, int]]) -> dict[str, Any]:
    case = gen_normal(i, rng, combos)
    case["category"] = "variation"
    case["id"] = f"stage4a_variation_{i:05d}"
    # 同一 context から別の weapon/mount_count を選ばせるための添字
    case["variation_index"] = i % 6
    return case


def gen_ship_type_contrast(i: int, rng: random.Random,
                           combos: list[tuple[str, str, int]]) -> dict[str, Any]:
    """同一の国・年で艦種だけを振り、口径帯の違いを学ばせる。

    成立する組み合わせの中から、艦種が順に一巡するように選ぶ。
    """
    by_type: dict[str, list[tuple[str, str, int]]] = {}
    for combo in combos:
        by_type.setdefault(combo[1], []).append(combo)
    types = sorted(by_type)
    ship_type = types[i % len(types)]
    pool = by_type[ship_type]
    nation, _, year = pool[(i // len(types)) % len(pool)]
    disp, beam = _hull_for(ship_type, rng)
    return _base_case(i, "ship_type_contrast", nation, year, ship_type,
                      rng.choice(ROLES), disp, beam)


def gen_era_unavailable(i: int, rng: random.Random) -> dict[str, Any]:
    """DB の最古の砲(1911)より前の年代。どの国でも主砲が引けない。"""
    nation = DB_NATIONS[i % len(DB_NATIONS)]
    ship_type = DB_SHIP_TYPES[i % len(DB_SHIP_TYPES)]
    year = EARLY_YEARS[i % len(EARLY_YEARS)]
    disp, beam = _hull_for(ship_type, rng)
    return _base_case(i, "era_unavailable", nation, year, ship_type,
                      rng.choice(ROLES), disp, beam)


def gen_nation_unavailable(i: int, rng: random.Random) -> dict[str, Any]:
    nation = UNCOVERED_NATIONS[i % len(UNCOVERED_NATIONS)]
    ship_type = DB_SHIP_TYPES[i % len(DB_SHIP_TYPES)]
    year = rng.choice(COVERED_YEARS)
    disp, beam = _hull_for(ship_type, rng)
    return _base_case(i, "nation_unavailable", nation, year, ship_type,
                      rng.choice(ROLES), disp, beam)


def gen_ship_type_uncovered(i: int, rng: random.Random) -> dict[str, Any]:
    nation = DB_NATIONS[i % len(DB_NATIONS)]
    ship_type = UNCOVERED_SHIP_TYPES[i % len(UNCOVERED_SHIP_TYPES)]
    year = rng.choice(COVERED_YEARS)
    return _base_case(i, "ship_type_uncovered", nation, year, ship_type,
                      rng.choice(ROLES), 1200.0, 9.0)


def gen_hull_too_small(i: int, rng: random.Random) -> dict[str, Any]:
    """艦種に対して船体が小さすぎる。砲塔重量・バーベット径の制約で弾かれる想定。"""
    nation = DB_NATIONS[i % len(DB_NATIONS)]
    ship_type = ["battleship", "battlecruiser", "heavy_cruiser"][i % 3]
    year = rng.choice(COVERED_YEARS)
    disp, beam = _hull_for(ship_type, rng, scale=rng.uniform(0.10, 0.28))
    return _base_case(i, "hull_too_small", nation, year, ship_type,
                      rng.choice(ROLES), disp, beam)


# 成立する組み合わせ一覧を必要とする生成器
COMBO_GENERATORS = {
    "normal": gen_normal,
    "variation": gen_variation,
    "ship_type_contrast": gen_ship_type_contrast,
}
GENERATORS = {
    "era_unavailable": gen_era_unavailable,
    "nation_unavailable": gen_nation_unavailable,
    "ship_type_uncovered": gen_ship_type_uncovered,
    "hull_too_small": gen_hull_too_small,
}


def build_cases(count: int, db: dict[str, Any], seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    combos = feasible_combos(db)
    if not combos:
        raise SystemExit("armament DB から成立する (nation, ship_type, year) が1つも作れません")
    cases: list[dict[str, Any]] = []
    for category, ratio in RATIOS.items():
        n = max(1, int(count * ratio))
        for i in range(n):
            if category in COMBO_GENERATORS:
                cases.append(COMBO_GENERATORS[category](i, rng, combos))
            else:
                cases.append(GENERATORS[category](i, rng))
    rng.shuffle(cases)
    return cases


def split_for(case: dict[str, Any], index: int) -> str:
    if case["nation"] in TEST_NATIONS and index % 4 == 0:
        return "test"
    if case["ship_type"] in TEST_SHIP_TYPES and index % 4 == 1:
        return "test"
    if case["role"] in VALIDATION_ROLES and index % 3 == 0:
        return "validation"
    if index % 17 == 0:
        return "test"
    if index % 11 == 0:
        return "validation"
    return "train"


# ----------------------------------------------------------------------------
# teacher 生成（classic ルールエンジンがそのまま teacher）
# ----------------------------------------------------------------------------

def context_of(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "year": case["year"],
        "nation": case["nation"],
        "ship_type": case["ship_type"],
        "displacement_t": case.get("displacement_t"),
        "beam_m": case.get("beam_m"),
    }


def diagnose_no_candidate(context: dict[str, Any], db: dict[str, Any]) -> list[dict[str, str]]:
    """candidate が引けなかった理由を、validator と同じ reason code で説明する。

    retrieve_available_weapons が空になる経路は3つある。どれなのかを
    フィルタを1段ずつ外して切り分ける（推測で1つに決めない）。
    """
    nation = context.get("nation")
    year = context.get("year")
    ship_type = context.get("ship_type")

    by_nation = [w for w in db["weapons"].values()
                 if not w.get("fictional") and w.get("nation") == nation]
    if not by_nation:
        return [{
            "code": "armament.weapon_unavailable_for_nation",
            "message": f"no main battery weapon in the database for nation {nation!r}",
        }]

    by_era = [w for w in by_nation
              if not w.get("year_introduced") or w["year_introduced"] <= year]
    if not by_era:
        earliest = min(w["year_introduced"] for w in by_nation if w.get("year_introduced"))
        return [{
            "code": "armament.weapon_unavailable_for_era",
            "message": (
                f"earliest {nation} main battery weapon enters service in {earliest}, "
                f"after the requested year {year}"
            ),
        }]

    compat = db.get("compatibility_rules", {})
    st_range = compat.get("caliber_min_max_by_ship_type", {}).get(ship_type)
    if not st_range:
        return [{
            "code": "armament.battery_layout_conflict",
            "message": f"ship type {ship_type!r} has no main battery caliber range defined",
        }]
    lo, hi = st_range.get("min"), st_range.get("max")
    in_range = [w for w in by_era
                if w.get("caliber_mm") is not None and lo <= w["caliber_mm"] <= hi]
    if not in_range:
        return [{
            "code": "armament.battery_layout_conflict",
            "message": (
                f"no {nation} weapon available by {year} falls in the {ship_type} "
                f"caliber range [{lo},{hi}]mm"
            ),
        }]

    # 砲は在るのに candidate が引けなかった＝ 船体に載る砲塔が1つも無い。
    # retrieve_available_mounts が turret_weight > displacement * 0.20 で全部落としている。
    disp = context.get("displacement_t")
    unmounted = [w["weapon_id"] for w in in_range
                 if not retrieve_available_mounts(w["weapon_id"], context, db)]
    if len(unmounted) == len(in_range):
        if disp:
            lightest = min(
                (m.get("turret_weight_t") for w in in_range
                 for m in (db["mounts"].get(mid) for mid in
                           db.get("compatibility_rules", {})
                             .get("weapon_to_mount", {}).get(w["weapon_id"], []))
                 if m and m.get("turret_weight_t")),
                default=None,
            )
            detail = (
                f"lightest available turret is {lightest:.0f}t, over the "
                f"{disp * 0.20:.0f}t limit for a {disp:.0f}t hull"
                if lightest else f"no turret fits a {disp:.0f}t hull"
            )
            return [{
                "code": "armament.turret_weight_exceeds_hull_limit",
                "message": (
                    f"{nation} {ship_type} guns exist for {year} but {detail}"
                ),
            }]
        return [{
            "code": "armament.weapon_mount_incompatible",
            "message": (
                f"no mount is registered for any {nation} weapon in the "
                f"{ship_type} caliber range [{lo},{hi}]mm"
            ),
        }]

    return [{
        "code": "armament.battery_layout_conflict",
        "message": (
            f"no usable {nation} main battery could be assembled for a {year} "
            f"{ship_type} within the caliber range [{lo},{hi}]mm"
        ),
    }]


def suggest_revision(case: dict[str, Any], db: dict[str, Any],
                     reasons: list[dict[str, str]]) -> dict[str, Any]:
    """何を緩めれば成立するかを、実在データから1つだけ提案する。"""
    codes = {r["code"] for r in reasons}
    nation = case["nation"]
    revision: dict[str, Any] = {
        "year": None, "nation": None, "ship_type": None,
        "displacement_t": None, "beam_m": None, "note": "",
    }
    if "armament.weapon_unavailable_for_nation" in codes:
        revision["nation"] = "UK"
        revision["note"] = (
            f"the armament database has no {nation} guns; "
            "use a nation covered by the database or import a foreign design"
        )
        return revision
    if "armament.weapon_unavailable_for_era" in codes:
        cands = [w["year_introduced"] for w in db["weapons"].values()
                 if w.get("nation") == nation and w.get("year_introduced")]
        if cands:
            revision["year"] = min(cands)
            revision["note"] = f"move the design year to {min(cands)} or later"
        return revision
    if codes & {
        "armament.turret_weight_exceeds_hull_limit",
        "armament.barbette_too_large_for_beam",
        "armament.topweight_limit_exceeded",
        "armament.armament_displacement_conflict",
        "armament.magazine_volume_insufficient",
    }:
        disp, beam = HULL_DEFAULTS.get(case["ship_type"], (10000.0, 18.0))
        revision["displacement_t"] = disp
        revision["beam_m"] = beam
        revision["note"] = (
            f"the hull is too small for this battery; "
            f"a {case['ship_type']} needs roughly {disp:.0f}t / {beam:.1f}m beam"
        )
        return revision
    revision["ship_type"] = "light_cruiser"
    revision["note"] = "choose a ship type with a defined main battery caliber range"
    return revision


def retrieve_with_fallback(
    context: dict[str, Any], db: dict[str, Any], rng: random.Random
) -> dict[str, Any] | None:
    """代表砲が載らない船体でも、載る砲があるならそれを使う。

    `retrieve_armament` は代表砲（最大口径）を1門選び、その砲に載る砲塔が
    無ければ即 None を返す。船体が小さいケースではこれが「小口径砲なら成立するのに
    impossible」という**誤った teacher** を生む。ここでは口径の小さい順に総当たりして、
    本当に1門も載らないときだけ諦める。
    """
    candidate = retrieve_armament(context, db, strategy="representative", rng=rng)
    if candidate is not None:
        return candidate

    weapons = retrieve_available_weapons(context, db)
    for weapon in sorted(weapons, key=lambda w: w.get("caliber_mm") or 0):
        mounts = retrieve_available_mounts(weapon["weapon_id"], context, db)
        if not mounts:
            continue
        mount = mounts[0]
        mount_count = estimate_mount_count(weapon, context)
        positions = generate_positions(mount_count, rng)
        fc = select_fire_control(context, db, weapon, rng)
        return build_candidate(weapon, mount, mount_count, positions, fc)
    return None


def make_output(case: dict[str, Any], db: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """1ケース分の teacher 出力を作る。判定は必ず validator に委ねる。"""
    context = context_of(case)

    # 口径範囲が定義されていない艦種は retriever に渡さない。
    # retrieve_available_weapons は範囲が引けないと口径フィルタを丸ごと素通りさせるので、
    # そのまま渡すと「主砲の定義が無い艦種に無制約で主砲が載る」teacher ができてしまう。
    caliber_ranges = db.get("compatibility_rules", {}).get("caliber_min_max_by_ship_type", {})
    if case["ship_type"] not in caliber_ranges:
        reasons = [{
            "code": "armament.battery_layout_conflict",
            "message": (
                f"ship type {case['ship_type']!r} has no main battery caliber range "
                f"defined in compatibility_rules"
            ),
        }]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "impossible",
            "armament": None,
            "reason": reasons,
            "warnings": [],
            "suggested_revision": suggest_revision(case, db, reasons),
            "design_rationale": "",
            "confidence": 0.9,
        }

    if case["category"] == "variation":
        variations = retrieve_armament_variations(
            context, db, n=6, seed=case["year"] * 1000 + case["variation_index"]
        )
        candidate = variations[case["variation_index"] % len(variations)] if variations else None
    else:
        candidate = retrieve_with_fallback(context, db, rng)

    if candidate is None:
        reasons = diagnose_no_candidate(context, db)
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "impossible",
            "armament": None,
            "reason": reasons,
            "warnings": [],
            "suggested_revision": suggest_revision(case, db, reasons),
            "design_rationale": "",
            "confidence": 0.9,
        }

    verdict = validate_armament(candidate, context, db)
    errors = [i for i in verdict.get("issues", []) if i.get("severity") == "error"]
    if verdict.get("status") == "impossible" or errors:
        reasons = [{"code": i["code"], "message": i["message"]} for i in errors] or [
            {"code": i["code"], "message": i["message"]} for i in verdict.get("issues", [])
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "impossible",
            "armament": None,
            "reason": reasons,
            "warnings": [],
            "suggested_revision": suggest_revision(case, db, reasons),
            "design_rationale": "",
            "confidence": 0.9,
        }

    armament = {
        "main_batteries": candidate["main_batteries"],
        "aggregate": aggregate_main_gun_fields(candidate),
    }
    # severity=warn は捨てない。validator の閾値設計上、
    # 「砲塔重量が排水量の35〜50%」のような余裕の乏しい設計は warn 止まりで ok になる。
    # これを落とすと「7,356tの戦艦に41cm砲6門で問題なし」という teacher ができてしまう。
    warnings = [
        {"code": i["code"], "message": i["message"]}
        for i in verdict.get("issues", [])
        if i.get("severity") == "warn"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "armament": armament,
        "reason": [],
        "warnings": warnings,
        "suggested_revision": None,
        "design_rationale": candidate.get("design_rationale", ""),
        "confidence": round(
            float(candidate.get("confidence", 0.85)) - 0.1 * min(len(warnings), 3), 3
        ),
    }


def make_request(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "generate_navspec_armament",
        "output_schema": SCHEMA_VERSION,
        "response_format": {
            "json_only": True,
            "top_level_required": TOP_LEVEL_OUTPUT_FIELDS,
            "armament_required_when_status_ok": ["main_batteries"],
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
    }


# ----------------------------------------------------------------------------
# 出力検証（jsonschema 非依存の最小チェック）
# ----------------------------------------------------------------------------

VALID_CODES = {
    "armament.weapon_unavailable_for_era",
    "armament.weapon_unavailable_for_nation",
    "armament.mount_unavailable_for_era",
    "armament.mount_unavailable_for_nation",
    "armament.weapon_mount_incompatible",
    "armament.barbette_too_large_for_beam",
    "armament.turret_weight_exceeds_hull_limit",
    "armament.magazine_volume_insufficient",
    "armament.topweight_limit_exceeded",
    "armament.firing_arc_conflict",
    "armament.blast_interference",
    "armament.battery_layout_conflict",
    "armament.armament_displacement_conflict",
    "armament.fire_control_unavailable",
}


def validate_teacher(output: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in TOP_LEVEL_OUTPUT_FIELDS:
        if key not in output:
            errors.append(f"missing top-level key: {key}")
    if output.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version != {SCHEMA_VERSION}")
    status = output.get("status")
    if status not in ("ok", "impossible"):
        errors.append(f"invalid status: {status!r}")
    if status == "ok":
        arm = output.get("armament")
        if not isinstance(arm, dict) or not arm.get("main_batteries"):
            errors.append("status=ok but armament.main_batteries is empty")
        if output.get("reason"):
            errors.append("status=ok but reason[] is not empty")
        if output.get("suggested_revision") is not None:
            errors.append("status=ok but suggested_revision is not null")
    elif status == "impossible":
        if output.get("armament") is not None:
            errors.append("status=impossible but armament is not null")
        if not output.get("reason"):
            errors.append("status=impossible but reason[] is empty")
    if not isinstance(output.get("warnings"), list):
        errors.append("warnings must be a list")
    for field in ("reason", "warnings"):
        for item in output.get(field) or []:
            if item.get("code") not in VALID_CODES:
                errors.append(f"unknown {field} code: {item.get('code')!r}")
            if not item.get("message"):
                errors.append(f"empty message for {field} {item.get('code')!r}")
    for battery in ((output.get("armament") or {}).get("main_batteries") or []):
        expected = (battery.get("mount_count", 0) or 0) * (battery.get("barrels_per_mount", 0) or 0)
        if battery.get("total_barrels") != expected:
            errors.append(
                f"total_barrels {battery.get('total_barrels')} != "
                f"mount_count * barrels_per_mount ({expected})"
            )
        if len(battery.get("positions") or []) != (battery.get("mount_count") or 0):
            errors.append(
                f"positions length {len(battery.get('positions') or [])} != "
                f"mount_count {battery.get('mount_count')}"
            )
    return errors


# ----------------------------------------------------------------------------
# レコード・データセット構築
# ----------------------------------------------------------------------------

def build_record(case: dict[str, Any], db: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    request = make_request(case)
    output = make_output(case, db, rng)
    errors = validate_teacher(output)
    if errors:
        raise ValueError(f"{case['id']} stage4a teacher invalid: {errors[:5]}")
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
        "expected_reason_codes": [item["code"] for item in output["reason"]],
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
    status_counts: Counter = Counter()
    category_counts: Counter = Counter()
    reason_counts: Counter = Counter()

    for case in cases:
        row = build_record(case, db, rng)
        rows.append(row)
        status_counts[row["expected_status"]] += 1
        category_counts[row["category"]] += 1
        for code in row["expected_reason_codes"]:
            reason_counts[code] += 1

    write_splits(output_dir, rows)
    write_manifest(output_dir, rows, status_counts, category_counts, reason_counts, count, seed)
    return rows


def write_splits(output_dir: Path, rows: list[dict[str, Any]]) -> None:
    """train / valid / test の3本だけ書く。

    Stage 2/3 の生成器は valid.jsonl と md5 完全一致の validation.jsonl、および
    3分割を連結しただけの all.jsonl も書いていたが、いずれも読み込み側の参照が無く
    `16d9552 chore(navspec): 冗長な jsonl 派生ファイルを削除` で掃除済み。
    ここでは最初から作らない。valid.jsonl は mlx_lm が既定で読む名前。
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for row in rows:
        by_split[row["split"]].append(row)
    write_jsonl(output_dir / "train.jsonl", by_split["train"])
    write_jsonl(output_dir / "valid.jsonl", by_split["validation"])
    write_jsonl(output_dir / "test.jsonl", by_split["test"])


def write_manifest(
    output_dir: Path,
    rows: list[dict[str, Any]],
    status_counts: Counter,
    category_counts: Counter,
    reason_counts: Counter,
    requested: int,
    seed: int,
) -> None:
    split_counts = Counter(row["split"] for row in rows)
    manifest = {
        "dataset": "stage4a_armament_v0",
        "schema": "schemas/navspec_armament_teacher_stage4a.schema.json",
        "schema_version": SCHEMA_VERSION,
        "teacher": (
            "navspec_armament_retriever.retrieve_armament + "
            "navspec_armament_validator.validate_armament (no LLM in the loop)"
        ),
        "armament_db": "data/navspec/armament/ (59 weapons / 59 mounts / 6 nations / 1911-1944)",
        "rows": len(rows),
        "requested": requested,
        "seed": seed,
        "split_counts": dict(sorted(split_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "system_prompt": SYSTEM_PROMPT,
        "format": "mlx_lm ChatDataset/messages, compact JSON sort_keys=True",
        "vocabulary": {
            "nations": DB_NATIONS,
            "uncovered_nations": UNCOVERED_NATIONS,
            "ship_types": DB_SHIP_TYPES,
            "uncovered_ship_types": UNCOVERED_SHIP_TYPES,
            "note": (
                "armament DB の語彙をそのまま使う。Stage 2 の SHIP_TYPES への"
                "エイリアス表は意図的に作っていない"
            ),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--count", type=int, default=1200)
    p.add_argument("--output-dir", type=Path,
                   default=ROOT / "data" / "navspec" / "lora" / "stage4a_armament_v0")
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)

    rows = build_dataset(args.count, args.output_dir, args.data_dir, seed=args.seed)
    status = Counter(r["expected_status"] for r in rows)
    splits = Counter(r["split"] for r in rows)
    print(f"rows={len(rows)} status={dict(status)} splits={dict(splits)}")
    print(f"-> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
