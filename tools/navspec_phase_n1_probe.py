"""Phase N1 quick stability probe.

Stage 3 v1 adapter へ numeric-only prompt (status/reason/suggested_revision を含まない)
を与え、navspec.numeric_candidate.v1 を直接生成できるか確認する。

Section 7: N1 が不安定な場合は無理に schema を変更しない。運用では Stage 2 schema
出力からの抽出を許可する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from qwen_lora_eval import extract_candidate_json, load_mlx_lm, generate_text, read_jsonl  # noqa: E402

NUMERIC_SCHEMA = json.loads((ROOT / "schemas" / "navspec_numeric_candidate.schema.json").read_text(encoding="utf-8"))
OK_CASES = ROOT / "data" / "navspec" / "lora" / "stage3_numeric_eval_ok_v1" / "eval.jsonl"

SYSTEM_NUMERIC = (
    "You are navspec numeric specialist. Generate only spec numeric values, "
    "design_concept, and confidence. Return JSON matching "
    "navspec.numeric_candidate.v1 with keys: schema_version, spec, design_concept, "
    "confidence. Do not include status, reason, or suggested_revision."
)


def build_numeric_prompt(tokenizer, row: dict) -> str:
    inp = row.get("input") or {}
    request_obj = {
        "request": inp.get("request", ""),
        "nation": inp.get("nation", ""),
        "year": inp.get("year", 0),
        "ship_type": inp.get("ship_type", ""),
        "role": inp.get("role", ""),
        "constraints": inp.get("constraints", {}),
        "priorities": inp.get("priorities", []),
        "classic_confirmed_status": "ok",
        "output_schema": "navspec.numeric_candidate.v1",
    }
    messages = [
        {"role": "system", "content": SYSTEM_NUMERIC},
        {"role": "user", "content": json.dumps(request_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
    ]
    apply_chat_template = getattr(tokenizer, "apply_chat_template", None)
    if apply_chat_template:
        return apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    parts = []
    for m in messages:
        parts.append(f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def main() -> None:
    rows = read_jsonl(OK_CASES)[:30]  # 30 件で安定性確認
    adapter = ROOT / "models" / "navspec-lora-0.5b-v0.1" / "adapter" / "stage3-numeric-quality-r8-v1"
    model, tokenizer, generate_fn = load_mlx_lm("Qwen/Qwen2.5-0.5B-Instruct", adapter)

    valid = 0
    schema_version_ok = 0
    spec_present = 0
    no_status_leak = 0
    has_design_concept = 0
    required_fields_present = 0
    required = NUMERIC_SCHEMA["properties"]["spec"]["required"]

    samples = []
    for i, row in enumerate(rows):
        prompt = build_numeric_prompt(tokenizer, row)
        text = generate_text(generate_fn, model, tokenizer, prompt, 512, 0.0)
        try:
            obj = extract_candidate_json(text, NUMERIC_SCHEMA)
        except Exception:
            obj = None

        is_dict = isinstance(obj, dict)
        sv_ok = is_dict and obj.get("schema_version") == "navspec.numeric_candidate.v1"
        sp_ok = is_dict and isinstance(obj.get("spec"), dict)
        ns_ok = is_dict and "status" not in obj and "reason" not in obj
        dc_ok = is_dict and bool(obj.get("design_concept"))
        rf_ok = False
        if sp_ok:
            spec = obj["spec"]
            rf_ok = all(spec.get(f) is not None for f in required)

        if is_dict:
            valid += 1
        if sv_ok:
            schema_version_ok += 1
        if sp_ok:
            spec_present += 1
        if ns_ok:
            no_status_leak += 1
        if dc_ok:
            has_design_concept += 1
        if rf_ok:
            required_fields_present += 1

        if i < 3:
            samples.append({"id": row.get("id"), "raw_head": text[:300], "parsed_keys": sorted(obj.keys()) if is_dict else None, "schema_version": obj.get("schema_version") if is_dict else None})

    n = len(rows)
    print(f"=== Phase N1 stability probe (Stage 3 v1 iter500, {n} cases) ===")
    print(f"valid JSON object:        {valid}/{n} ({valid/n:.1%})")
    print(f"schema_version correct:   {schema_version_ok}/{n} ({schema_version_ok/n:.1%})")
    print(f"spec present:             {spec_present}/{n} ({spec_present/n:.1%})")
    print(f"no status/reason leak:    {no_status_leak}/{n} ({no_status_leak/n:.1%})")
    print(f"design_concept present:   {has_design_concept}/{n} ({has_design_concept/n:.1%})")
    print(f"all required spec fields: {required_fields_present}/{n} ({required_fields_present/n:.1%})")
    print()
    verdict = "STABLE" if (valid/n >= 0.90 and schema_version_ok/n >= 0.80 and no_status_leak/n >= 0.90) else "UNSTABLE"
    print(f"verdict: {verdict}")
    print()
    print("samples:")
    for s in samples:
        print(f"  {s['id']}: keys={s['parsed_keys']} sv={s['schema_version']}")
        print(f"    head: {s['raw_head'][:200]!r}")

    result = {
        "phase": "n1",
        "adapter": str(adapter),
        "sample_count": n,
        "valid_json_rate": valid / n,
        "schema_version_correct_rate": schema_version_ok / n,
        "spec_present_rate": spec_present / n,
        "no_status_leak_rate": no_status_leak / n,
        "design_concept_present_rate": has_design_concept / n,
        "required_fields_present_rate": required_fields_present / n,
        "verdict": verdict,
    }
    out = ROOT / "data" / "navspec" / "lora" / "eval" / "numeric_specialist_n1_stability_probe" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nreport: {out}")


if __name__ == "__main__":
    main()
