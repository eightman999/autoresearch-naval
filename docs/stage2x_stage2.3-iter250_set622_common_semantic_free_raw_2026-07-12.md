# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-12

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter250`
- run_slug: `qwen2_5_0_5b_stage2.3-iter250`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 622
- raw JSON成功率: 100.0%
- schema完全一致率: 100.0%
- status一致率: 89.2%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 72.0%
- spec field adoption率(avg): 57.1%
- impossible precision/recall/F1: 82.2% / 79.9% / 81.0%
- reason+revision成立率(avg): 28.0%
- 反復崩壊率: 0.0%
- legacy reason exact: 87.0%
- strict reason exact: 87.0%
- semantic reason micro P/R/F1: 86.5% / 84.0% / 85.2%
- primary reason accuracy: 74.9%
- extra/missing/invalid reason rate: 5.6% / 27.4% / 0.0%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 0, 'root_parse_error': 0, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_contradiction_001 | common_classic_eval_contradiction | yes | yes | yes | 0.0% | 311 |
| classic_eval_contradiction_002 | common_classic_eval_contradiction | yes | yes | no | 0.0% | 534 |
| classic_eval_extended_contradiction_001 | common_classic_eval_extended_contradiction | yes | yes | yes | 0.0% | 420 |
| classic_eval_extended_era_002 | common_classic_eval_extended_era | yes | yes | yes | 0.0% | 305 |
| classic_eval_extended_treaty_003 | common_classic_eval_extended_treaty | yes | yes | yes | 0.0% | 315 |
| classic_eval_extended_tier_004 | common_classic_eval_extended_tier | yes | yes | yes | 0.0% | 287 |
| classic_eval_extended_contradiction_011 | common_classic_eval_extended_contradiction | yes | yes | yes | 0.0% | 420 |
| classic_eval_extended_era_012 | common_classic_eval_extended_era | yes | yes | yes | 0.0% | 305 |
| classic_eval_extended_treaty_013 | common_classic_eval_extended_treaty | yes | yes | yes | 0.0% | 315 |
| classic_eval_extended_tier_014 | common_classic_eval_extended_tier | yes | yes | yes | 0.0% | 287 |
