"""Generate a fictional ShipSpecRecord from a saved navspec checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import mlx.core as mx

from prepare import Tokenizer
from tools.navspec_common import MUTATION_PROFILES, read_jsonl, render_wiki_table, validate_spec
from tools.navspec_equipment import equipment_plan, estimate_dimensions
from tools.navspec_mutate import mutate_record
from tools.navspec_naming import naming_plan
from tools.navspec_shiptypes import (
    get_ship_type_specs,
    hull_kind_for_ship_type,
    resolve_ship_type,
    clamp_main_caliber,
    clamp_speed,
    era_for_year,
)
from tools.navspec_constraints import apply_constraints, parse_constraints
from tools.navspec_nation_tier import apply_tier_clamps, get_tier_limits
from tools.navspec_contradiction import check_contradiction
from validate_navspec_output import extract_json_object, validate_schema
from tools.navspec_eval import missing_required
from train import GPT, GPTConfig


DEFAULT_CHECKPOINT = Path("checkpoints/navspec/latest.json")
DEFAULT_RECORDS = Path("data/navspec/battleship_real_specs.jsonl")
DEFAULT_SCHEMA = Path("schemas/ship_spec_record.schema.json")
DEFAULT_RAW_OUTPUT = Path("data/navspec/generated/latest_raw.txt")
DEFAULT_JSON_OUTPUT = Path("data/navspec/generated/latest.json")

def make_synthetic_base(year: int, country: str, ship_type: str) -> dict[str, Any]:
    resolved = resolve_ship_type(ship_type, None)
    specs = get_ship_type_specs(resolved)
    hull_kind = hull_kind_for_ship_type(resolved)
    est_l, est_b, est_d = estimate_dimensions(specs["normal_t"], hull_kind, resolved)
    main_cal = clamp_main_caliber(specs["main_caliber_mm"], resolved, year)
    speed = clamp_speed(specs["speed"], resolved, year)
    return {
        "schema_version": "navspec.ship_spec_record.v1",
        "record_id": f"synthetic:{country}:{resolved}:{year}",
        "source_kind": "synthetic",
        "class_name": f"{country} {resolved.replace('_', ' ').title()} Design",
        "country": country,
        "type": resolved,
        "hull_kind": hull_kind,
        "year": year,
        "displacement": {"normal_t": specs["normal_t"], "full_t": round(specs["normal_t"] * 1.1), "normal_raw": "synthetic", "full_raw": "synthetic"},
        "dimensions": {"length_m": est_l, "breadth_m": est_b, "draught_m": est_d, "length_raw": "synthetic", "breadth_raw": "synthetic", "draught_raw": "synthetic"},
        "propulsion": {"machinery": "synthetic", "shafts": specs["shafts"], "power_shp": None, "speed_kn": speed, "fuel_raw": "synthetic", "endurance_raw": "synthetic", "power_raw": "synthetic", "speed_raw": "synthetic"},
        "armor": {"belt_mm": specs["belt_mm"], "raw": "synthetic"},
        "armament": {"main_caliber_mm": main_cal, "main_barrels": specs["barrels"], "mounts": [], "raw": "synthetic"},
        "complement": {"crew": None, "raw": "synthetic"},
        "source_url": None,
        "source_trace": {"database": "synthetic", "source_url_raw": "", "fetched_at": ""},
    }


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json_result(obj: dict[str, Any], raw_path: Path, json_path: Path) -> None:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def load_weights_preserving_dicts(model: GPT, weights_path: Path) -> None:
    """Load safetensors weights while preserving dict-typed submodules.

    MLX's tree_unflatten coerces numeric string keys (GPT.value_embeds uses
    {"1": ..., "3": ...}) into a list, which then collides with the model's
    dict in Module.update and raises KeyError. Traverse the model's own
    parameter tree so dict keys stay dict keys, and inject by dotted path.
    """
    flat = mx.load(str(weights_path))

    def build(node: Any, prefix: str) -> Any:
        if isinstance(node, dict):
            return {
                k: build(v, f"{prefix}.{k}" if prefix else str(k))
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [build(v, f"{prefix}.{i}") for i, v in enumerate(node)]
        return flat.get(prefix, node)

    model.update(build(model.parameters(), ""), strict=False)


def load_checkpoint(metadata_path: Path) -> tuple[GPT, Tokenizer, dict[str, Any]]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    weights_path = Path(metadata["weights"])
    if not weights_path.is_absolute():
        candidate = metadata_path.parent / weights_path.name
        weights_path = candidate if candidate.exists() else weights_path

    config = GPTConfig(**metadata["config"])
    model = GPT(config)
    load_weights_preserving_dicts(model, weights_path)
    mx.eval(model.parameters())

    tokenizer = Tokenizer.from_directory(metadata["tokenizer_dir"])
    return model, tokenizer, metadata


def find_base_record(records: list[dict[str, Any]], country: str | None, class_like: str | None) -> dict[str, Any]:
    candidates = records
    if country:
        needle = country.lower()
        candidates = [record for record in candidates if needle in (record.get("country") or "").lower()]
    if class_like:
        needle = class_like.lower()
        named = [record for record in candidates if needle in (record.get("class_name") or "").lower()]
        if named:
            candidates = named
    if not candidates:
        raise ValueError("No base record matched the requested country/class filters")
    return candidates[0]


def build_prompt(base: dict[str, Any], profile_index: int, user_prompt: str | None) -> str:
    profile = MUTATION_PROFILES[profile_index % len(MUTATION_PROFILES)]
    name_plan = naming_plan(base, profile_index)
    eq_plan = equipment_plan(base, profile)
    request = {
        "base": {
            "record_id": base.get("record_id"),
            "class_name": base.get("class_name"),
            "country": base.get("country"),
            "type": base.get("type"),
            "source_url": base.get("source_url"),
        },
        "constraints": {
            "method": "bounded_mutation",
            "profile": profile.note,
            "displacement_scale": profile.displacement_scale,
            "speed_delta_kn": profile.speed_delta,
            "armor_scale": profile.armor_scale,
            "main_caliber_delta_mm": profile.main_caliber_delta,
            "target_country": name_plan["target_country"],
            "target_class_name": name_plan["target_class_name"],
            "naming_category": name_plan["naming_category"],
            "naming_rule": name_plan["naming_rule"],
            "naming_description": name_plan["naming_description"],
            "name_pool": name_plan["name_pool"],
            "name_pool_size": name_plan["name_pool_size"],
            "name_pool_source_note": name_plan["name_pool_source_note"],
            "prefix": name_plan["prefix"],
            "prefix_note": name_plan["prefix_note"],
            "prefix_source": name_plan["prefix_source"],
            "equipment_rule": "navspec.equipment.v1",
            "equipment_candidates": eq_plan["equipment_candidates"],
            "expected_era_profile": eq_plan["era_profile"],
            "expected_design_doctrine": eq_plan["design_doctrine"],
            "user_prompt": user_prompt or "",
        },
        "base_record": base,
        "output_schema": "navspec.ship_spec_record.v1",
    }
    return (
        "<task>\n"
        "kind: bounded_fictionalize\n"
        "instruction: Create a fictional battleship specification from the base record and constraints. "
        "Return one compact JSON ShipSpecRecord only. Use target_country and target_class_name exactly, "
        "follow naming_category/naming_rule/name_pool, include era_profile/design_doctrine/equipment/armor_scheme, "
        "use equipment_candidates for equipment choices, mark source_kind as fictional_bounded, "
        "and do not invent a historical source_url.\n"
        "<input>\n"
        f"{compact(request)}\n"
        "</input>\n"
        "<output>\n"
    )


def patch_record(
    record: dict[str, Any],
    base: dict[str, Any],
    profile_index: int,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成後補正パッチ: mutate_recordで完全な構造を生成し、モデルが正しく
    生成した数値フィールドがあれば上書きする。その後、era clamp と
    ユーザー制約 clamp を適用する。
    """
    canonical = mutate_record(base, profile_index)

    _NUMERIC_RANGES = {
        "displacement": {"normal_t": (500, 150000), "full_t": (500, 150000)},
        "dimensions": {"length_m": (30, 400), "breadth_m": (5, 60), "draught_m": (1, 20)},
        "propulsion": {"power_shp": (100, 500000), "speed_kn": (5, 45)},
        "armor": {"belt_mm": (10, 600)},
        "armament": {"main_caliber_mm": (76, 520), "main_barrels": (1, 16)},
        "complement": {"crew": (10, 5000)},
    }

    for field, checks in _NUMERIC_RANGES.items():
        model_val = record.get(field)
        if isinstance(model_val, dict):
            for key, (lo, hi) in checks.items():
                v = model_val.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi:
                    canonical[field][key] = v

    ship_type = base.get("type") or base.get("hull_kind") or "other"
    year = base.get("year")
    nation = base.get("country")

    cal = canonical.get("armament", {}).get("main_caliber_mm")
    clamped_cal = clamp_main_caliber(cal, ship_type, year)
    if clamped_cal is not None:
        canonical["armament"]["main_caliber_mm"] = clamped_cal

    spd = canonical.get("propulsion", {}).get("speed_kn")
    clamped_spd = clamp_speed(spd, ship_type, year)
    if clamped_spd is not None:
        canonical["propulsion"]["speed_kn"] = clamped_spd

    constraint_log: list[str] = []
    if constraints:
        parsed = parse_constraints(constraints)
        if parsed:
            canonical, constraint_log = apply_constraints(canonical, parsed, ship_type=ship_type, year=year)

    tier_log: list[str] = []
    canonical, tier_log = apply_tier_clamps(canonical, nation, ship_type, year, apply_equipment=False)

    disp = canonical.get("displacement", {}).get("normal_t") or canonical.get("displacement", {}).get("full_t")
    est_l, est_b, est_d = estimate_dimensions(disp, canonical.get("hull_kind"), ship_type)
    if est_l is not None and est_b is not None and est_d is not None:
        canonical["dimensions"] = {
            "length_m": est_l,
            "breadth_m": est_b,
            "draught_m": est_d,
            "length_raw": "fictional bounded estimate",
            "breadth_raw": "fictional bounded estimate",
            "draught_raw": "fictional bounded estimate",
        }

    all_logs = constraint_log + tier_log

    patched_eq = equipment_plan(canonical)
    canonical["era_profile"] = patched_eq["era_profile"]
    canonical["design_doctrine"] = patched_eq["design_doctrine"]
    canonical["equipment"] = patched_eq["equipment"]
    canonical["armor_scheme"] = patched_eq["armor_scheme"]
    canonical["equipment_candidates"] = patched_eq["equipment_candidates"]
    canonical["fictional_constraints"]["equipment_candidates"] = patched_eq["equipment_candidates"]
    canonical["fictional_constraints"]["expected_era_profile"] = patched_eq["era_profile"]
    canonical["fictional_constraints"]["expected_design_doctrine"] = patched_eq["design_doctrine"]
    tier_equipment_log: list[str] = []
    canonical, tier_equipment_log = apply_tier_clamps(
        canonical, nation, ship_type, year, apply_numeric=False, apply_equipment=True
    )
    all_logs.extend(tier_equipment_log)
    if "nation_capability_tier" in canonical:
        canonical["fictional_constraints"]["nation_capability_tier"] = canonical["nation_capability_tier"]
    if isinstance(canonical.get("equipment"), dict) and isinstance(canonical["equipment"].get("propulsion_fit"), dict):
        canonical["equipment"]["propulsion_fit"]["speed_kn"] = canonical.get("propulsion", {}).get("speed_kn")
        canonical["equipment"]["propulsion_fit"]["power_shp"] = canonical.get("propulsion", {}).get("power_shp")
    if all_logs:
        canonical["constraint_adjustments"] = all_logs
    canonical["validation_issues"] = validate_spec(canonical)
    canonical["wiki_table_markdown"] = render_wiki_table(canonical)
    return canonical


def next_token(model: GPT, tokens: list[int], temperature: float, repetition_penalty: float = 0.0) -> int:
    idx = mx.array([tokens], dtype=mx.int32)
    logits = model(idx)[:, -1, :]
    if repetition_penalty > 0.0 and len(tokens) > 1:
        recent = tokens[-64:]
        for t in set(recent):
            count = recent.count(t)
            if logits[0, t] > 0:
                logits[0, t] = logits[0, t] / (1.0 + repetition_penalty * count)
            else:
                logits[0, t] = logits[0, t] * (1.0 + repetition_penalty * count)
    if temperature <= 0:
        return int(mx.argmax(logits, axis=-1).item())
    return int(mx.random.categorical(logits / temperature).item())


def generate_text(model: GPT, tokenizer: Tokenizer, prompt: str, max_new_tokens: int, temperature: float) -> str:
    tokens = tokenizer.encode(prompt, prepend=tokenizer.get_bos_token_id())
    generated: list[int] = []
    max_context = model.config.sequence_len
    repetition_penalty = 1.2
    for step in range(max_new_tokens):
        context = tokens[-max_context:]
        token = next_token(model, context, temperature, repetition_penalty)
        tokens.append(token)
        generated.append(token)
        if step > 20:
            tail = generated[-8:]
            if len(set(tail)) <= 2:
                break
        if step % 32 == 31:
            text = tokenizer.decode(generated)
            if "</output>" in text or "</task>" in text:
                break
    text = tokenizer.decode(generated)
    if "</output>" in text:
        text = text[:text.index("</output>")]
    return text


def validation_summary(record: dict[str, Any], schema_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = validate_schema(record, schema)
    errors.extend(f"missing top-level field: {field}" for field in missing_required(record))
    rule_issues = validate_spec(record)
    errors.extend(f"{issue['code']}: {issue['message']}" for issue in rule_issues if issue["severity"] == "error")
    warnings = [issue for issue in rule_issues if issue["severity"] == "warn"]
    return errors, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a fictional ShipSpecRecord from a saved checkpoint")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--country", default="Japan")
    parser.add_argument("--class-like", default="KONGO")
    parser.add_argument("--profile", type=int, default=0)
    parser.add_argument("--year", type=int, default=None, help="Year for synthetic mode")
    parser.add_argument("--ship-type", default=None, help="Ship type for synthetic mode (e.g. destroyer_escort)")
    parser.add_argument("--hull-kind", default=None, help="(deprecated) Hull kind for synthetic mode; use --ship-type")
    parser.add_argument("--constraints-json", default=None, help="JSON string of user constraints")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--raw-output", type=Path, default=DEFAULT_RAW_OUTPUT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    args = parser.parse_args()

    ship_type = args.ship_type or args.hull_kind
    resolved_ship_type = resolve_ship_type(ship_type, None) if ship_type else "other"
    constraints = json.loads(args.constraints_json) if args.constraints_json else None
    contradiction = check_contradiction(
        resolved_ship_type,
        args.year,
        args.country,
        constraints=constraints,
        raw_prompt=args.prompt,
    )
    if contradiction.get("status") == "impossible":
        tier_limits = get_tier_limits(args.country)
        result = {
            "status": "impossible",
            "reason": contradiction.get("reason") or contradiction.get("reasons") or [],
            "suggested_revision": contradiction.get("suggested_revision", {}),
            "request": {
                "country": args.country,
                "year": args.year,
                "ship_type": resolved_ship_type,
                "constraints": constraints or {},
                "prompt": args.prompt,
            },
            "nation_capability_tier": {
                "tier": contradiction.get("tier"),
                "description": tier_limits["description"],
                "limits": {
                    "max_displacement_t": tier_limits["max_displacement_t"],
                    "max_main_caliber_mm": tier_limits["max_main_caliber_mm"],
                    "max_speed_multiplier": tier_limits["max_speed_multiplier"],
                    "max_power_multiplier": tier_limits["max_power_multiplier"],
                    "radar_from_year": tier_limits["radar_from_year"],
                    "battleship_allowed": tier_limits["battleship_allowed"],
                    "battlecruiser_allowed": tier_limits["battlecruiser_allowed"],
                    "carrier_allowed": tier_limits["carrier_allowed"],
                },
            },
        }
        write_json_result(result, args.raw_output, args.json_output)
        print(f"raw_output: {args.raw_output}")
        print(f"json_output: {args.json_output}")
        print("status: impossible")
        for reason in result["reason"]:
            print(f"  REASON {reason}")
        raise SystemExit(0)

    model, tokenizer, metadata = load_checkpoint(args.checkpoint)
    if args.year is not None and ship_type:
        base = make_synthetic_base(args.year, args.country or "Unknown", ship_type)
    elif args.year is not None and not ship_type:
        base = make_synthetic_base(args.year, args.country or "Unknown", "other")
    else:
        records = read_jsonl(args.records)
        base = find_base_record(records, args.country, args.class_like)
    prompt = build_prompt(base, args.profile, args.prompt)
    generated = generate_text(model, tokenizer, prompt, args.max_new_tokens, args.temperature)

    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_text(generated, encoding="utf-8")
    print(f"raw_output: {args.raw_output}")
    print(f"checkpoint_val_bpb: {metadata.get('metrics', {}).get('val_bpb')}")

    try:
        record = extract_json_object(generated)
    except ValueError as exc:
        print(f"json_extract: failed: {exc} — using deterministic fallback")
        record = {}

    record = patch_record(record, base, args.profile, constraints=constraints)

    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"json_output: {args.json_output}")

    errors, warnings = validation_summary(record, args.schema)
    print(f"validation_errors: {len(errors)}")
    for error in errors:
        print(f"  ERROR {error}")
    print(f"validation_warnings: {len(warnings)}")
    for warning in warnings:
        print(f"  WARN {warning['code']}: {warning['message']}")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
