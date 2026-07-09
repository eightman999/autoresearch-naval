"""Generate a fictional ShipSpecRecord from a saved navspec checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import mlx.core as mx

from prepare import Tokenizer
from tools.navspec_common import MUTATION_PROFILES, read_jsonl
from validate_navspec_output import extract_json_object, validate_schema
from tools.navspec_eval import missing_required
from tools.navspec_common import validate_spec
from train import GPT, GPTConfig


DEFAULT_CHECKPOINT = Path("checkpoints/navspec/latest.json")
DEFAULT_RECORDS = Path("data/navspec/battleship_real_specs.jsonl")
DEFAULT_SCHEMA = Path("schemas/ship_spec_record.schema.json")
DEFAULT_RAW_OUTPUT = Path("data/navspec/generated/latest_raw.txt")
DEFAULT_JSON_OUTPUT = Path("data/navspec/generated/latest.json")


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
            "user_prompt": user_prompt or "",
        },
        "base_record": base,
        "output_schema": "navspec.ship_spec_record.v1",
    }
    return (
        "<task>\n"
        "kind: bounded_fictionalize\n"
        "instruction: Create a fictional battleship specification from the base record and constraints. "
        "Return one compact JSON ShipSpecRecord only. Mark source_kind as fictional_bounded and do not invent a historical source_url.\n"
        "<input>\n"
        f"{compact(request)}\n"
        "</input>\n"
        "<output>\n"
    )


def next_token(model: GPT, tokens: list[int], temperature: float) -> int:
    idx = mx.array([tokens], dtype=mx.int32)
    logits = model(idx)[:, -1, :]
    if temperature <= 0:
        return int(mx.argmax(logits, axis=-1).item())
    return int(mx.random.categorical(logits / temperature).item())


def generate_text(model: GPT, tokenizer: Tokenizer, prompt: str, max_new_tokens: int, temperature: float) -> str:
    tokens = tokenizer.encode(prompt, prepend=tokenizer.get_bos_token_id())
    generated: list[int] = []
    max_context = model.config.sequence_len
    for _ in range(max_new_tokens):
        context = tokens[-max_context:]
        token = next_token(model, context, temperature)
        tokens.append(token)
        generated.append(token)
        text = tokenizer.decode(generated)
        if "</output>" in text or "</task>" in text:
            break
    return tokenizer.decode(generated)


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
    parser.add_argument("--prompt", default="")
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--raw-output", type=Path, default=DEFAULT_RAW_OUTPUT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    args = parser.parse_args()

    model, tokenizer, metadata = load_checkpoint(args.checkpoint)
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
        print(f"json_extract: failed: {exc}")
        raise SystemExit(1)

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
