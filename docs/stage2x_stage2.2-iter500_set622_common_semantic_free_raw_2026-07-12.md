# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-12

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.2-balanced-reason-r8`
- run_slug: `qwen2_5_0_5b_stage2.2-balanced-reason-r8`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 622
- raw JSON成功率: 100.0%
- schema完全一致率: 100.0%
- status一致率: 90.8%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 79.7%
- spec field adoption率(avg): 63.7%
- impossible precision/recall/F1: 98.4% / 69.3% / 81.3%
- reason+revision成立率(avg): 20.3%
- 反復崩壊率: 0.0%
- legacy reason exact: 89.7%
- strict reason exact: 89.7%
- semantic reason micro P/R/F1: 96.5% / 79.8% / 87.4%
- primary reason accuracy: 66.5%
- extra/missing/invalid reason rate: 3.9% / 33.5% / 0.0%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 0, 'root_parse_error': 0, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_contradiction_001 | common_classic_eval_contradiction | yes | yes | no | 0.0% | 558 |
| classic_eval_contradiction_002 | common_classic_eval_contradiction | yes | yes | no | 0.0% | 591 |
| classic_eval_extended_contradiction_001 | common_classic_eval_extended_contradiction | yes | yes | yes | 0.0% | 441 |
| classic_eval_extended_era_002 | common_classic_eval_extended_era | yes | yes | yes | 0.0% | 326 |
| classic_eval_extended_treaty_003 | common_classic_eval_extended_treaty | yes | yes | yes | 0.0% | 336 |
| classic_eval_extended_tier_004 | common_classic_eval_extended_tier | yes | yes | yes | 0.0% | 308 |
| classic_eval_extended_contradiction_011 | common_classic_eval_extended_contradiction | yes | yes | yes | 0.0% | 441 |
| classic_eval_extended_era_012 | common_classic_eval_extended_era | yes | yes | yes | 0.0% | 326 |
| classic_eval_extended_treaty_013 | common_classic_eval_extended_treaty | yes | yes | yes | 0.0% | 336 |
| classic_eval_extended_tier_014 | common_classic_eval_extended_tier | yes | yes | yes | 0.0% | 308 |
