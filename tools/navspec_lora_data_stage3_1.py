"""Build navspec Stage 3.1 (balanced correlated numeric) LoRA datasets.

Stage 3.1 fixes the Stage 3 v1 regression root cause: the v1 training data
had only 10.7% impossible cases (Stage 2 baseline ~54%), which degraded
status / impossible F1 / semantic reason F1 below the regression gate.

Stage 3.1 goals (spec 2026-07-13):
  - keep Stage 2 schema/status/impossible ability (impossible ratio 40-50%, target 45%)
  - reproduce Stage 3 v1 numeric quality gains
  - REMOVE independent jitter on power_shp and range_nm
  - introduce correlated canonical numeric generation

Key design decisions (confirmed):
  - Stage 2 replay: load existing stage2_status_v0 rows directly (guarantees the
    10 reason-code categories and the impossible ratio floor)
  - range_nm: keep existing base_range_nm, add 6 explicit range profiles
    (coastal/short_range/fleet_standard/long_range/oceanic/commerce_raider),
    clamp to the selected profile band, NO independent jitter
  - power_shp: canonical estimate_power_shp(disp, speed), recomputed whenever
    displacement mutates; NO independent jitter
  - correlated mutation: only primary variables (displacement ratio) move;
    dependent fields (full_load, length/beam/draft, lb_ratio, power, range)
    are all recomputed
  - stratified ordering: train.jsonl interleaves impossible/ok; no 100+ streak

All ok teachers are validated against Stage 2 schema (validate_stage2).
All impossible teachers come from Stage 2 logic (classic-ruled).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_constraints import parse_constraints  # noqa: E402
from navspec_contradiction import check_contradiction  # noqa: E402
from navspec_equipment import estimate_dimensions, estimate_power_shp  # noqa: E402
from navspec_lora_data import (  # noqa: E402
    DEFAULT_SCHEMA,
    NATIONS,
    ROLES,
    SHIP_TYPES,
    SYSTEM_PROMPT,
    YEARS,
    base_range_nm,
    compact,
    conversation_text,
    make_ok_output,
    make_request,
    request_prompt,
    split_for,
    write_jsonl,
)
from navspec_lora_data_stage2 import make_stage2_output  # noqa: E402
from navspec_lora_stage2_schema import (  # noqa: E402
    REASON_CODE_SET,
    normalize_reason_code_sequence,
    validate_stage2,
)
from navspec_nation_tier import get_nation_tier, get_tier_limits  # noqa: E402
from navspec_shiptypes import (  # noqa: E402
    SHIP_TYPE_MAIN_GUN_CLAMP,
    SHIP_TYPE_SPEED_CLAMP,
    get_ship_type_specs,
    hull_kind_for_ship_type,
    lb_ratio_band_for,
)

DEFAULT_OUTPUT_DIR = ROOT / "data" / "navspec" / "lora" / "stage3_1_balanced_numeric_v0"
STAGE2_STATUS_DIR = ROOT / "data" / "navspec" / "lora" / "stage2_status_v0"

# Stage 3 v1 generator helpers (case construction only; we re-derive teachers)
from navspec_lora_data_stage3 import (  # noqa: E402
    ERA_YEARS,
    MAIN_SHIP_TYPES,
    RARE_SHIP_TYPES,
    gen_boundary as _v1_gen_boundary,
    gen_diversity_pair as _v1_gen_diversity,
    gen_era_contrast as _v1_gen_era_contrast,
    gen_multi_constraint as _v1_gen_multi_constraint,
    gen_nation_contrast as _v1_gen_nation_contrast,
    gen_normal_numeric as _v1_gen_normal_numeric,
    gen_rare_ship_type as _v1_gen_rare_ship_type,
    gen_role_contrast as _v1_gen_role_contrast,
    gen_ship_type_contrast as _v1_gen_ship_type_contrast,
    gen_single_constraint as _v1_gen_single_constraint,
)
from navspec_lora_data_stage2 import (  # noqa: E402
    IMPOSSIBLE_GENS as _STAGE2_IMPOSSIBLE_GENS,
    gen_combined as _stage2_gen_combined,
    gen_composite_nation as _stage2_gen_composite_nation,
    gen_composite_speed_power as _stage2_gen_composite_speed_power,
)

TIER1_NATIONS = ["United States", "United Kingdom", "Germany", "Japan", "France", "Italy"]
TIER2_NATIONS = ["Netherlands", "Argentina", "Brazil", "Sweden", "Spain", "Russia"]
TIER3_NATIONS = ["Portugal", "Thailand", "Chile", "Norway", "Denmark", "Greece"]

# ---- §10 range profiles ----
# (min_nm, max_nm) per profile. base_range_nm is kept as the canonical base;
# the selected profile clamps the value into a meaningful band. Independent
# jitter on range_nm is removed entirely.
RANGE_PROFILES: dict[str, tuple[int, int]] = {
    "coastal": (500, 2500),
    "short_range": (1500, 4000),
    "fleet_standard": (3000, 8000),
    "long_range": (6000, 12000),
    "oceanic": (8000, 18000),
    "commerce_raider": (8000, 20000),
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def select_range_profile(ship_type: str, role: str, year: int, nation: str, tier: int) -> str:
    """Decide the endurance profile from role / hull / era / tier.

    Role takes priority (it carries mission intent), then hull kind, then a
    year/tier nudge for early or minor-power designs.
    """
    hull = hull_kind_for_ship_type(ship_type)
    role_key = role or ""
    if "commerce" in role_key:
        return "commerce_raider"
    if "coastal" in role_key:
        return "coastal"
    if "colonial" in role_key or "long_range" in role_key:
        return "long_range"
    if "convoy" in role_key or "escort" in role_key or "anti_submarine" in role_key:
        return "short_range" if hull in ("destroyer", "frigate") else "fleet_standard"
    if hull == "battlecruiser":
        return "oceanic"
    if hull == "battleship":
        return "fleet_standard"
    if hull == "cruiser":
        return "fleet_standard"
    if hull in ("destroyer", "frigate"):
        return "fleet_standard" if year >= 1918 else "short_range"
    if tier >= 4:
        return "short_range"
    if year < 1890:
        return "coastal"
    return "fleet_standard"


def canonical_range_nm(
    ship_type: str, role: str, year: int, tier: int, nation: str
) -> tuple[float, str]:
    """§10 canonical range: keep base_range_nm, clamp into profile band."""
    profile = select_range_profile(ship_type, role, year, nation, tier)
    base = base_range_nm(ship_type, role, year, tier)
    lo, hi = RANGE_PROFILES[profile]
    value = max(lo, min(hi, round(base)))
    return float(value), profile


# ---- §8/§9/§11 correlated mutation ----
def correlated_mutate_spec(
    canonical_spec: dict[str, Any],
    case: dict[str, Any],
    rng: random.Random,
    mutation_strength: float = 1.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mutate only primary variables; recompute every dependent field.

    Primary variable: standard_displacement_t (±5% scaled by strength).
    Dependent recomputed: full_load_displacement_t (ratio preserved),
    length_m / beam_m / draft_m (estimate_dimensions), lb_ratio,
    power_shp (estimate_power_shp with canonical speed + mutated displacement),
    range_nm (canonical_range_nm with profile clamp).

    NO independent jitter on speed_kt, power_shp, range_nm, length/beam/draft.
    main_gun_caliber_mm, main_gun_count, belt_armor_mm, torpedo_tubes, radar,
    nation_tier stay canonical (ship_type/era spec) — their valid ranges are
    verified against the mutated displacement by validate_stage2.
    """
    ship_type = case["ship_type"]
    year = int(case["year"])
    nation = case["nation"]
    role = case.get("role", "")
    tier = get_nation_tier(nation)
    limits = get_tier_limits(nation)
    hull = hull_kind_for_ship_type(ship_type)
    constraints = parse_constraints(case.get("constraints"))

    spec = dict(canonical_spec)
    meta: dict[str, Any] = {
        "source_rule": "stage3_1_correlated",
        "mutation_strength": mutation_strength,
        "independent_jitter_removed": ["power_shp", "range_nm", "speed_kt", "length_m", "beam_m", "draft_m"],
    }

    disp = _num(spec.get("standard_displacement_t"))
    mutated_disp = disp
    if disp is not None:
        max_td = limits.get("max_displacement_t")
        hi = float(max_td) if max_td is not None else disp * 1.3
        lo = disp * 0.7
        delta_frac = rng.uniform(-0.05, 0.05) * mutation_strength
        new_disp = max(lo, min(hi, disp * (1.0 + delta_frac)))
        spec["standard_displacement_t"] = round(new_disp, 1)
        meta["displacement_delta_frac"] = round(delta_frac, 4)
        mutated_disp = new_disp

    full = _num(spec.get("full_load_displacement_t"))
    if full is not None and disp is not None and disp > 0 and mutated_disp is not None:
        ratio = full / disp
        spec["full_load_displacement_t"] = round(mutated_disp * ratio, 1)
        meta["full_load_source"] = "ratio_preserved"

    speed = _num(spec.get("speed_kt"))
    if speed is not None:
        spec["speed_kt"] = round(speed, 1)
        meta["speed_source"] = "canonical_no_jitter"

    if speed is not None and mutated_disp is not None:
        power = estimate_power_shp(mutated_disp, speed)
        if power is not None and power > 0:
            spec["power_shp"] = round(power)
            meta["power_source"] = "canonical_recalc_from_mutated_disp_and_canonical_speed"

    if mutated_disp is not None:
        length, beam, draft = estimate_dimensions(mutated_disp, hull, ship_type)
        if length is not None:
            spec["length_m"] = length
        if beam is not None:
            spec["beam_m"] = beam
        if draft is not None:
            spec["draft_m"] = draft
        lb_min, lb_max = lb_ratio_band_for(ship_type, hull)
        raw_lb = (length / beam) if (length and beam and beam > 0) else 0.0
        spec["lb_ratio"] = round(max(lb_min, min(lb_max, raw_lb)), 2)
        meta["dimensions_source"] = "estimate_dimensions_from_mutated_disp"

    range_val, profile = canonical_range_nm(ship_type, role, year, tier, nation)
    if "range_nm_min" in constraints:
        range_val = max(range_val, float(constraints["range_nm_min"]))
    if "range_nm_max" in constraints:
        range_val = min(range_val, float(constraints["range_nm_max"]))
    if range_val > 0:
        spec["range_nm"] = round(range_val)
        meta["range_profile"] = profile
        meta["range_source"] = "canonical_base_range_nm_with_profile_clamp_no_jitter"

    meta["main_gun_source"] = "canonical_ship_type_spec"
    meta["armor_source"] = "canonical_ship_type_spec"
    meta["torpedo_source"] = "canonical_role_spec"

    spec["nation"] = nation
    spec["year"] = year
    spec["ship_type"] = ship_type
    spec["nation_tier"] = tier
    return spec, meta


def make_stage3_1_ok_output(
    case: dict[str, Any],
    rng: random.Random,
    mutation_strength: float = 1.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate a status=ok teacher with correlated numeric mutation."""
    canonical = make_ok_output(case)
    if canonical["status"] != "ok":
        return canonical, {"source_rule": "fallback_canonical_status_not_ok"}
    meta_seed = rng.randint(0, 2**31 - 1)
    meta = {"seed": meta_seed, "status_category": "ok"}
    mutated_spec, mut_meta = correlated_mutate_spec(
        canonical["spec"], case, rng, mutation_strength=mutation_strength
    )
    output = dict(canonical)
    output["spec"] = mutated_spec
    output["confidence"] = round(rng.uniform(0.82, 0.94), 2)
    tier = get_nation_tier(case.get("nation", ""))
    concepts = [
        f"{case.get('year', '')}年の{case.get('nation', '')}向け{case.get('ship_type', '')}案",
        f"{case.get('role', 'fleet_battle')}重視の設計",
        f"tier-{tier}工業力と艦種別諸元を反映",
    ]
    output["design_concept"] = "。".join(rng.sample(concepts, min(2, len(concepts))))
    errors = validate_stage2(output)
    if errors:
        return canonical, {"source_rule": "fallback_validate_stage2_failed", "errors": errors[:3]}
    meta.update(mut_meta)
    return output, meta


def make_stage3_1_output(
    case: dict[str, Any],
    rng: random.Random,
    mutation_strength: float = 1.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """impossible uses Stage 2 logic; ok uses correlated mutation."""
    contradiction = check_contradiction(
        case["ship_type"],
        int(case["year"]),
        case["nation"],
        constraints=case.get("constraints"),
        raw_prompt=case.get("request"),
    )
    if contradiction.get("status") == "impossible":
        out = make_stage2_output(case)
        return out, {"source_rule": "stage2_impossible", "status_category": "impossible"}
    return make_stage3_1_ok_output(case, rng, mutation_strength=mutation_strength)


def build_record(
    case: dict[str, Any],
    rng: random.Random,
    mutation_strength: float = 1.0,
) -> dict[str, Any]:
    request = make_request(case)
    output, meta = make_stage3_1_output(case, rng, mutation_strength=mutation_strength)
    errors = validate_stage2(output)
    if errors:
        raise ValueError(f"{case.get('id')} stage3.1 teacher invalid: {errors[:5]}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": compact(request)},
        {"role": "assistant", "content": compact(output)},
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
        "expected_reason_codes": [item["code"] for item in output["reason"]],
        "messages": messages,
        "text": conversation_text(messages),
        "output": output,
        "expected_output": output,
        "teacher_meta": meta,
        "split": case.get("split", "train"),
    }


# ---- §5 Stage 2 direct replay ----
def load_stage2_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in ("train", "validation", "test"):
        path = STAGE2_STATUS_DIR / f"{split}.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _repair_replay_output(output: dict[str, Any]) -> dict[str, Any]:
    """Normalize Stage 2 replay impossible outputs so they pass validate_stage2.

    Some legacy Stage 2 rows carry >3 reason codes or a non-canonical order.
    We keep the human-readable messages but re-derive the code sequence via
    normalize_reason_code_sequence (max 3, canonical order), matching the
    Stage 2.2+ contract that validate_stage2 enforces.
    """
    if not isinstance(output, dict):
        return output
    if output.get("status") != "impossible":
        return output
    reason = output.get("reason")
    if not isinstance(reason, list):
        reason = []
    msg_map: dict[str, str] = {}
    codes: list[str] = []
    for item in reason:
        if isinstance(item, dict):
            code = item.get("code")
            if isinstance(code, str) and code in REASON_CODE_SET:
                codes.append(code)
                msg_map.setdefault(code, item.get("message") or code)
    normalized = normalize_reason_code_sequence(codes)
    if not normalized:
        normalized = ["displacement_limit_exceeded"]
        msg_map.setdefault("displacement_limit_exceeded", "要求が成立しない")
    new_reason = [{"code": c, "message": msg_map.get(c, c)} for c in normalized]
    new_output = dict(output)
    new_output["reason"] = new_reason
    if not isinstance(new_output.get("suggested_revision"), dict) or not new_output["suggested_revision"]:
        new_output["suggested_revision"] = {"note": "要求条件を緩和し、同じ艦種・年代・国家に近い実現可能案へ修正する。"}
    return new_output


def replay_stage2_status(
    stage2_rows: list[dict[str, Any]],
    impossible_count: int,
    ok_count: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Replay existing Stage 2 rows directly.

    Preserves messages/text/output (status + reason codes intact), only
    re-assigns id/category/split to avoid collisions inside Stage 3.1.
    """
    rng = random.Random(seed)
    impossible_pool = [r for r in stage2_rows if r.get("output", {}).get("status") == "impossible"]
    ok_pool = [r for r in stage2_rows if r.get("output", {}).get("status") == "ok"]
    rng.shuffle(impossible_pool)
    rng.shuffle(ok_pool)

    out: list[dict[str, Any]] = []

    def _adopt(row: dict[str, Any], new_id: str, category: str) -> dict[str, Any]:
        new_row = dict(row)
        new_row["id"] = new_id
        new_row["category"] = category
        repaired = _repair_replay_output(row.get("output", {}))
        new_row["output"] = repaired
        new_row["expected_output"] = repaired
        new_row["expected_status"] = repaired.get("status")
        new_row["expected_reason_codes"] = [item["code"] for item in repaired.get("reason", [])]
        request = row.get("input") or make_request(
            {
                "nation": row.get("nation", ""),
                "year": row.get("year", 1938),
                "ship_type": row.get("ship_type", ""),
                "role": row.get("role", ""),
                "constraints": row.get("constraints", {}),
                "request": row.get("request", ""),
            }
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": compact(request)},
            {"role": "assistant", "content": compact(repaired)},
        ]
        new_row["messages"] = messages
        new_row["text"] = conversation_text(messages)
        new_row["teacher_meta"] = {
            "source_rule": "stage2_direct_replay_normalized",
            "status_category": repaired.get("status"),
        }
        new_row.pop("split", None)
        return new_row

    for i, row in enumerate(impossible_pool[:impossible_count]):
        out.append(_adopt(row, f"stage31_replay_imp_{i:05d}", f"replay_impossible_{row.get('category', 'unknown')}"))
    for i, row in enumerate(ok_pool[:ok_count]):
        out.append(_adopt(row, f"stage31_replay_ok_{i:05d}", f"replay_ok_{row.get('category', 'unknown')}"))
    return out


# ---- supplement impossible cases (reason-code coverage boost) ----
def _supplement_impossible_generators() -> list:
    """Stage 2 impossible generators that cover the 10 reason codes."""
    gens = [(name, fn) for name, fn in _STAGE2_IMPOSSIBLE_GENS]
    gens.append(("impossible_combined", _stage2_gen_combined))
    gens.append(("impossible_composite_nation", _stage2_gen_composite_nation))
    gens.append(("impossible_composite_speed_power", _stage2_gen_composite_speed_power))
    return gens


def gen_supplement_impossible(index: int) -> dict[str, Any]:
    gens = _supplement_impossible_generators()
    name, fn = gens[index % len(gens)]
    case = fn(index)
    case["id"] = f"stage31_supp_imp_{index:05d}"
    case["category"] = f"supplement_{name}"
    return case


# ---- ok-numeric case generators (reuse Stage 3 v1 family) ----
def _adopt_v1_case(case: dict[str, Any], prefix: str, category: str, index: int) -> dict[str, Any]:
    case["id"] = f"{prefix}_{index:05d}"
    case["category"] = category
    return case


def gen_normal_numeric(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_normal_numeric(index), "stage31_normal", "normal_numeric", index)


def gen_ship_type_contrast(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_ship_type_contrast(index), "stage31_st_contrast", "ship_type_contrast", index)


def gen_era_contrast(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_era_contrast(index), "stage31_era", "era_contrast", index)


def gen_nation_contrast(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_nation_contrast(index), "stage31_nation", "nation_contrast", index)


def gen_role_contrast(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_role_contrast(index), "stage31_role", "role_contrast", index)


def gen_single_constraint(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_single_constraint(index), "stage31_sconst", "single_constraint", index)


def gen_multi_constraint(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_multi_constraint(index), "stage31_mconst", "multi_constraint", index)


def gen_boundary(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_boundary(index), "stage31_boundary", "boundary", index)


def gen_rare_ship_type(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_rare_ship_type(index), "stage31_rare", "rare_ship_type", index)


def gen_diversity(index: int) -> dict[str, Any]:
    return _adopt_v1_case(_v1_gen_diversity(index), "stage31_diversity", "diversity", index)


# ---- dataset builder --------------------------------------------------------

# Target case-generation composition (§6 guideline). Final status ratio is
# governed by Stage 2 direct replay (fixed impossible floor) plus the natural
# impossible yield of the ok-numeric families and the supplement block.
# Target case-generation composition for the generated (non-replay) block.
# Ratios are normalized against generated_count, so they should sum to 1.0;
# a small pad step below covers rounding residual so generated rows always
# fill the remainder exactly.
CASE_RATIOS = {
    "normal_numeric": 0.28,
    "ship_type_contrast": 0.18,
    "era_contrast": 0.11,
    "nation_contrast": 0.07,
    "role_contrast": 0.07,
    "single_constraint": 0.11,
    "multi_constraint": 0.07,
    "boundary": 0.07,
    "rare_diversity": 0.04,
}


def build_cases(
    count: int,
    stage2_rows: list[dict[str, Any]],
    seed: int,
    target_impossible_ratio: float = 0.45,
    stage2_ok_replay_count: int = 4000,
) -> tuple[list[dict[str, Any]], int, int]:
    """Build the full case list (replay + generated ok-numeric).

    Allocation is strictly proportional to `count` so the output size tracks
    the request:
      - Stage 2 impossible replay: min(available, count*0.27)  (~60% of the
        impossible target, since ~10,784 rows exist for count=40k)
      - Stage 2 ok replay: min(available, stage2_ok_replay_count, count*0.10)
      - generated ok-numeric families: the remainder

    Returns (cases, replay_impossible_used, replay_ok_used).
    """
    rng = random.Random(seed)

    stage2_impossible_total = sum(
        1 for r in stage2_rows if r.get("output", {}).get("status") == "impossible"
    )
    stage2_ok_total = sum(
        1 for r in stage2_rows if r.get("output", {}).get("status") == "ok"
    )

    replay_impossible = min(stage2_impossible_total, int(count * 0.27))
    replay_ok = min(stage2_ok_total, stage2_ok_replay_count, int(count * 0.10))

    replay_block = replay_stage2_status(
        stage2_rows,
        impossible_count=replay_impossible,
        ok_count=replay_ok,
        seed=seed + 1,
    )

    generated_count = max(0, count - len(replay_block))
    targets = {k: max(1, int(generated_count * v)) for k, v in CASE_RATIOS.items()}
    # Trim back to generated_count if rounding overshot.
    while sum(targets.values()) > generated_count and targets:
        biggest = max(targets, key=targets.get)
        targets[biggest] = max(1, targets[biggest] - 1)
    # Pad normal_numeric if rounding undershot (keeps total == generated_count).
    while sum(targets.values()) < generated_count:
        targets["normal_numeric"] += 1

    ok_cases: list[dict[str, Any]] = []
    for i in range(targets["normal_numeric"]):
        ok_cases.append(gen_normal_numeric(i))
    for i in range(targets["ship_type_contrast"]):
        ok_cases.append(gen_ship_type_contrast(i))
    for i in range(targets["era_contrast"]):
        ok_cases.append(gen_era_contrast(i))
    for i in range(targets["nation_contrast"]):
        ok_cases.append(gen_nation_contrast(i))
    for i in range(targets["role_contrast"]):
        ok_cases.append(gen_role_contrast(i))
    for i in range(targets["single_constraint"]):
        ok_cases.append(gen_single_constraint(i))
    for i in range(targets["multi_constraint"]):
        ok_cases.append(gen_multi_constraint(i))
    half_boundary = targets["boundary"] // 2
    for i in range(half_boundary):
        ok_cases.append(gen_boundary(i))
    rare_total = targets["rare_diversity"]
    for i in range(rare_total // 2):
        ok_cases.append(gen_rare_ship_type(i))
    for i in range(rare_total - rare_total // 2):
        ok_cases.append(gen_diversity(i))
    for i in range(targets["boundary"] - half_boundary):
        ok_cases.append(gen_boundary(i + half_boundary))

    cases = list(replay_block) + ok_cases
    return cases, replay_impossible, replay_ok


def materialize_rows(
    cases: list[dict[str, Any]],
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Turn cases into teacher rows.

    Stage 2 replay rows already carry messages/text/output; we keep them as-is
    (only split is reassigned later). Generated cases are run through
    make_stage3_1_output and validate_stage2.
    """
    rng = random.Random(seed + 2)
    rows: list[dict[str, Any]] = []
    stats: Counter = Counter()
    teacher_meta_samples: list[dict[str, Any]] = []
    for case in cases:
        if "messages" in case and "output" in case and "text" in case:
            row = dict(case)
            row.setdefault("expected_status", row["output"]["status"])
            row.setdefault("expected_reason_codes", [item["code"] for item in row["output"].get("reason", [])])
            rows.append(row)
            stats[row["output"]["status"]] += 1
            continue
        row = build_record(case, rng, mutation_strength=1.0)
        rows.append(row)
        stats[row["expected_status"]] += 1
        if row["expected_status"] == "ok" and len(teacher_meta_samples) < 50:
            teacher_meta_samples.append(row.get("teacher_meta", {}))
    return rows, {
        "generated_status_counts": dict(stats),
        "teacher_meta_samples": teacher_meta_samples,
    }


def _longest_streak(rows: list[dict[str, Any]], status: str) -> int:
    longest = 0
    cur = 0
    for row in rows:
        if row.get("expected_status") == status:
            cur += 1
            longest = max(longest, cur)
        else:
            cur = 0
    return longest


def stratified_interleave(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """§7 interleave impossible/ok so no long streak forms.

    Uses a bresenham-style merge against the target ratio: at each step place
    whichever status is more "behind" its ideal share. This keeps the maximum
    streak near ceil(max/min) instead of dumping the surplus of the larger
    class at the tail (which the previous naive interleave did).
    """
    impossible = [r for r in rows if r.get("expected_status") == "impossible"]
    ok = [r for r in rows if r.get("expected_status") == "ok"]
    if not impossible:
        return ok
    if not ok:
        return impossible
    n_imp = len(impossible)
    n_ok = len(ok)
    max_streak_cap = 99  # §7 forbids 100+ consecutive same-status rows
    result: list[dict[str, Any]] = []
    i = k = 0
    placed_imp = 0
    placed_ok = 0
    cur_streak_status = ""
    cur_streak = 0
    while i < n_imp or k < n_ok:
        share_imp = placed_imp / n_imp if n_imp else 1.0
        share_ok = placed_ok / n_ok if n_ok else 1.0
        prefer_imp = i < n_imp and (k >= n_ok or share_imp <= share_ok)
        # Force-switch if we would exceed the streak cap.
        if cur_streak >= max_streak_cap and cur_streak_status == "ok" and i < n_imp:
            prefer_imp = True
        elif cur_streak >= max_streak_cap and cur_streak_status == "impossible" and k < n_ok:
            prefer_imp = False
        if prefer_imp:
            result.append(impossible[i])
            i += 1
            placed_imp += 1
            nxt_status = "impossible"
        else:
            result.append(ok[k])
            k += 1
            placed_ok += 1
            nxt_status = "ok"
        if nxt_status == cur_streak_status:
            cur_streak += 1
        else:
            cur_streak_status = nxt_status
            cur_streak = 1
    return result


def assign_splits(rows: list[dict[str, Any]], seed: int) -> dict[str, int]:
    """Assign train/validation/test using split_for on each row's case info."""
    rng = random.Random(seed + 3)
    indexed = list(enumerate(rows))
    rng.shuffle(indexed)
    counts: Counter = Counter()
    for idx, (_, row) in enumerate(indexed):
        case_like = {
            "category": row.get("category", ""),
            "nation": row.get("nation", ""),
            "ship_type": row.get("ship_type", ""),
            "role": row.get("role", ""),
            "year": row.get("year", 1938),
        }
        split = split_for(case_like, idx)
        row["split"] = split
        counts[split] += 1
    return dict(counts)


def order_splits(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for row in rows:
        by_split.setdefault(row["split"], []).append(row)
    by_split["train"] = stratified_interleave(by_split.get("train", []))
    rng_v = random.Random(20260713)
    rng_v.shuffle(by_split.get("validation", []))
    rng_t = random.Random(20260714)
    rng_t.shuffle(by_split.get("test", []))
    return by_split


def write_splits(output_dir: Path, by_split: dict[str, list[dict[str, Any]]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "validation", "test"):
        write_jsonl(output_dir / f"{split}.jsonl", by_split.get(split, []))
    write_jsonl(output_dir / "valid.jsonl", by_split.get("validation", []))
    write_jsonl(output_dir / "all.jsonl", by_split.get("train", []) + by_split.get("validation", []) + by_split.get("test", []))


def audit_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(r.get("expected_status") for r in rows)
    category_counts = Counter(r.get("category") for r in rows)
    nation_counts = Counter(r.get("nation") for r in rows)
    ship_type_counts = Counter(r.get("ship_type") for r in rows)
    role_counts = Counter(r.get("role") for r in rows)

    reason_codes: Counter = Counter()
    for row in rows:
        output = row.get("output") or row.get("expected_output") or {}
        for item in output.get("reason", []) or []:
            if isinstance(item, dict) and item.get("code"):
                reason_codes[item["code"]] += 1

    def _era(year: Any) -> str:
        try:
            y = int(year)
        except (TypeError, ValueError):
            return "unknown"
        if y < 1860:
            return "pre_1860"
        if y < 1890:
            return "1860_1889"
        if y < 1906:
            return "1890_1905"
        if y <= 1918:
            return "1906_1918"
        if y <= 1936:
            return "1919_1936"
        if y <= 1945:
            return "1937_1945"
        return "post_1945"

    era_counts = Counter(_era(r.get("year")) for r in rows)

    def _tier(nation: str) -> str:
        if nation in TIER1_NATIONS:
            return "major"
        if nation in TIER2_NATIONS:
            return "regional"
        if nation in TIER3_NATIONS:
            return "minor"
        return "limited"

    tier_counts = Counter(_tier(r.get("nation", "")) for r in rows)

    power_hist = Counter()
    range_hist = Counter()
    power_values: list[float] = []
    range_values: list[float] = []
    for row in rows:
        spec = (row.get("output") or row.get("expected_output") or {}).get("spec") or {}
        if not isinstance(spec, dict):
            continue
        p = _num(spec.get("power_shp"))
        if p is not None and p > 0:
            power_values.append(p)
            power_hist[min(9, int(math.log10(max(1.0, p))))] += 1
        rg = _num(spec.get("range_nm"))
        if rg is not None and rg > 0:
            range_values.append(rg)
            range_hist[int(rg // 2000)] += 1

    def _summarize(values: list[float]) -> dict[str, Any]:
        if not values:
            return {}
        sv = sorted(values)
        return {
            "n": len(sv),
            "min": round(sv[0], 1),
            "p25": round(sv[len(sv) // 4], 1),
            "median": round(sv[len(sv) // 2], 1),
            "p75": round(sv[(3 * len(sv)) // 4], 1),
            "p90": round(sv[int(len(sv) * 0.9)], 1),
            "max": round(sv[-1], 1),
        }

    return {
        "status_counts": dict(sorted(status_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "nation_counts": dict(sorted(nation_counts.items())),
        "ship_type_counts": dict(sorted(ship_type_counts.items())),
        "role_counts": dict(sorted(role_counts.items())),
        "reason_code_counts": dict(sorted(reason_codes.items())),
        "era_counts": dict(sorted(era_counts.items())),
        "nation_tier_counts": dict(sorted(tier_counts.items())),
        "power_shp_summary": _summarize(power_values),
        "range_nm_summary": _summarize(range_values),
    }


def write_manifest(
    output_dir: Path,
    rows: list[dict[str, Any]],
    by_split: dict[str, list[dict[str, Any]]],
    audit: dict[str, Any],
    requested: int,
    seed: int,
    replay_impossible: int,
    stage2_ok_replay: int,
) -> dict[str, Any]:
    total = len(rows)
    status_counts = Counter(r.get("expected_status") for r in rows)
    impossible_ratio = status_counts.get("impossible", 0) / total if total else 0.0

    train_rows = by_split.get("train", [])
    longest_ok = _longest_streak(train_rows, "ok")
    longest_imp = _longest_streak(train_rows, "impossible")

    manifest = {
        "dataset": "stage3_1_balanced_numeric_v0",
        "schema": "schemas/navspec_lora_candidate_stage2.schema.json",
        "schema_version": "navspec.lora_candidate.v1",
        "teacher": "navspec-classic-v1 + correlated canonical numeric mutation (Stage 2 direct replay for impossible)",
        "rows": total,
        "requested": requested,
        "seed": seed,
        "split_counts": {s: len(by_split.get(s, [])) for s in ("train", "validation", "test")},
        "status_counts": dict(sorted(status_counts.items())),
        "status_ratio": {
            "ok": round(status_counts.get("ok", 0) / total, 4) if total else 0.0,
            "impossible": round(impossible_ratio, 4),
            "impossible_target": 0.45,
            "impossible_band": [0.40, 0.50],
        },
        "category_counts": dict(sorted(Counter(r.get("category") for r in rows).items())),
        "stage2_replay": {
            "impossible_replayed": replay_impossible,
            "ok_replayed": stage2_ok_replay,
            "source_dir": str(STAGE2_STATUS_DIR.relative_to(ROOT)) if STAGE2_STATUS_DIR.exists() else str(STAGE2_STATUS_DIR),
        },
        "stratified_ordering": {
            "strategy": "train.jsonl interleaves impossible/ok; validation/test shuffled",
            "longest_ok_streak_train": longest_ok,
            "longest_impossible_streak_train": longest_imp,
            "max_allowed_streak": 100,
            "shuffle_seed_validation": 20260713,
            "shuffle_seed_test": 20260714,
        },
        "numeric_generation": {
            "independent_jitter_removed": ["power_shp", "range_nm", "speed_kt", "length_m", "beam_m", "draft_m"],
            "correlated_mutation": {
                "primary_variable": "standard_displacement_t",
                "displacement_jitter_rel": "±5% scaled by mutation_strength",
                "dependent_recomputed": [
                    "full_load_displacement_t (ratio preserved)",
                    "length_m / beam_m / draft_m (estimate_dimensions)",
                    "lb_ratio (clamped to ship_type band)",
                    "power_shp (estimate_power_shp from mutated displacement + canonical speed)",
                    "range_nm (base_range_nm + profile clamp)",
                ],
            },
            "power_shp": "canonical estimate_power_shp only; no random error",
            "range_nm": "canonical base_range_nm clamped into 6 profile bands; no independent jitter",
            "range_profiles": {k: list(v) for k, v in RANGE_PROFILES.items()},
        },
        "teacher_metadata_fields": [
            "source_rule",
            "status_category",
            "seed",
            "range_profile",
            "displacement_delta_frac",
            "power_source",
            "range_source",
            "mutation_strength",
        ],
        "reason_code_counts": audit["reason_code_counts"],
        "distribution_audit": {
            "era_counts": audit["era_counts"],
            "nation_tier_counts": audit["nation_tier_counts"],
            "ship_type_counts": audit["ship_type_counts"],
            "power_shp_summary": audit["power_shp_summary"],
            "range_nm_summary": audit["range_nm_summary"],
        },
        "system_prompt": SYSTEM_PROMPT,
        "format": "mlx_lm ChatDataset/messages, compact JSON sort_keys=True, assistant ends with <|im_end|>",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def build_dataset(
    count: int,
    output_dir: Path,
    schema_path: Path,
    seed: int = 42,
    target_impossible_ratio: float = 0.45,
    stage2_ok_replay_count: int = 4000,
) -> dict[str, Any]:
    _ = schema_path  # schema path retained for CLI compatibility; teacher uses validate_stage2
    stage2_rows = load_stage2_rows()
    if not stage2_rows:
        raise RuntimeError(f"Stage 2 status dataset not found at {STAGE2_STATUS_DIR}")

    cases, replay_impossible, stage2_ok_replay = build_cases(  # type: ignore[misc]
        count,
        stage2_rows,
        seed=seed,
        target_impossible_ratio=target_impossible_ratio,
        stage2_ok_replay_count=stage2_ok_replay_count,
    )

    rows, gen_stats = materialize_rows(cases, seed=seed)

    impossible_count = sum(1 for r in rows if r.get("expected_status") == "impossible")
    ok_count = sum(1 for r in rows if r.get("expected_status") == "ok")
    actual_ratio = impossible_count / len(rows) if rows else 0.0

    # Keep `count` rows total; if the natural status yield drifted outside
    # the 40-50% band, swap same-status-margin rows for supplement cases of
    # the deficient status (size-preserving replacement).
    supplement_appended = 0
    swapped_to_ok = 0
    if actual_ratio < 0.40 and ok_count > 0:
        target_imp = int(count * 0.45)
        deficit = max(0, target_imp - impossible_count)
        deficit = min(deficit, ok_count)
        if deficit > 0:
            supp_cases = [gen_supplement_impossible(i) for i in range(deficit * 3)]
            supp_rows, _ = materialize_rows(supp_cases, seed=seed + 8)
            supp_imp = [r for r in supp_rows if r.get("expected_status") == "impossible"][:deficit]
            rng_swap = random.Random(seed + 9)
            ok_indices = [i for i, r in enumerate(rows) if r.get("expected_status") == "ok"]
            rng_swap.shuffle(ok_indices)
            for idx, supp in zip(ok_indices, supp_imp):
                rows[idx] = supp
            supplement_appended = len(supp_imp)
    elif actual_ratio > 0.50 and impossible_count > 0:
        target_imp = int(count * 0.45)
        surplus = max(0, impossible_count - target_imp)
        surplus = min(surplus, impossible_count)
        if surplus > 0:
            gen_cases = [gen_normal_numeric(50000 + i) for i in range(surplus * 3)]
            gen_rows_extra, _ = materialize_rows(gen_cases, seed=seed + 10)
            gen_ok = [r for r in gen_rows_extra if r.get("expected_status") == "ok"][:surplus]
            rng_swap = random.Random(seed + 11)
            imp_indices = [i for i, r in enumerate(rows) if r.get("expected_status") == "impossible"]
            rng_swap.shuffle(imp_indices)
            for idx, rep in zip(imp_indices, gen_ok):
                rows[idx] = rep
            swapped_to_ok = len(gen_ok)


    assign_splits(rows, seed=seed)
    by_split = order_splits(rows)
    write_splits(output_dir, by_split)

    audit = audit_distribution(rows)
    manifest = write_manifest(
        output_dir,
        rows,
        by_split,
        audit,
        requested=count,
        seed=seed,
        replay_impossible=replay_impossible,
        stage2_ok_replay=stage2_ok_replay,
    )
    manifest["supplement_impossible_appended"] = supplement_appended
    manifest["impossible_surplus_swapped_to_ok"] = swapped_to_ok
    manifest["generated_status_counts_pre_adjust"] = gen_stats["generated_status_counts"]
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build navspec Stage 3.1 balanced numeric datasets")
    parser.add_argument("--count", type=int, default=40000)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--target-impossible-ratio", type=float, default=0.45)
    parser.add_argument("--stage2-ok-replay", type=int, default=4000)
    args = parser.parse_args()

    output_dir = args.output_dir or DEFAULT_OUTPUT_DIR
    manifest = build_dataset(
        args.count,
        output_dir,
        args.schema,
        seed=args.seed,
        target_impossible_ratio=args.target_impossible_ratio,
        stage2_ok_replay_count=args.stage2_ok_replay,
    )

    status = manifest["status_counts"]
    ratio = manifest["status_ratio"]
    strat = manifest["stratified_ordering"]
    print(f"dataset: stage3_1_balanced_numeric_v0")
    print(f"rows: {manifest['rows']} (requested {manifest['requested']})")
    print(f"output_dir: {output_dir}")
    print(f"splits: {manifest['split_counts']}")
    print(f"status: {status}")
    print(f"impossible ratio: {ratio['impossible']} (band {ratio['impossible_band']})")
    print(f"stage2 replay: impossible={manifest['stage2_replay']['impossible_replayed']} ok={manifest['stage2_replay']['ok_replayed']}")
    print(f"supplement impossible appended: {manifest.get('supplement_impossible_appended', 0)}")
    print(f"impossible surplus swapped to ok: {manifest.get('impossible_surplus_swapped_to_ok', 0)}")
    print(f"train streaks: ok={strat['longest_ok_streak_train']} impossible={strat['longest_impossible_streak_train']} (max allowed {strat['max_allowed_streak']})")
    print(f"reason codes: {len(manifest['reason_code_counts'])} kinds")
    top_cats = dict(sorted(manifest["category_counts"].items(), key=lambda kv: kv[1], reverse=True)[:12])
    print(f"top categories: {top_cats}")


if __name__ == "__main__":
    main()
