# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-10

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8`
- run_slug: `qwen2_5_0_5b_stage2-status-r8`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `False`
- total: 622
- raw JSON成功率: 100.0%
- schema完全一致率: 73.2%
- status一致率: 91.2%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 68.3%
- spec field adoption率(avg): 57.0%
- impossible precision/recall/F1: 85.5% / 82.1% / 83.8%
- reason+revision成立率(avg): 26.7%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_extended_contradiction_001 | classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_era_002 | classic_eval_extended_era | yes | no | yes | 0.0% | 517 |
| classic_eval_extended_treaty_003 | classic_eval_extended_treaty | yes | no | yes | 0.0% | 521 |
| classic_eval_extended_tier_004 | classic_eval_extended_tier | yes | no | yes | 0.0% | 452 |
| classic_eval_extended_contradiction_011 | classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_era_012 | classic_eval_extended_era | yes | no | yes | 0.0% | 517 |
| classic_eval_extended_treaty_013 | classic_eval_extended_treaty | yes | no | yes | 0.0% | 521 |
| classic_eval_extended_tier_014 | classic_eval_extended_tier | yes | no | yes | 0.0% | 452 |
| classic_eval_extended_contradiction_021 | classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_era_022 | classic_eval_extended_era | yes | no | yes | 0.0% | 517 |
