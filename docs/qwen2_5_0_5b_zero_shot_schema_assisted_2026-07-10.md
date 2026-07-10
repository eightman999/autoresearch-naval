# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-10

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: _(none, zero-shot)_
- run_slug: `qwen2_5_0_5b_zero_shot`
- eval_mode: `schema_assisted` (json_prefill=schema)
- total: 622
- raw JSON成功率: 92.1%
- schema完全一致率: 0.0%
- status一致率: 15.6%
- top必須フィールド率(avg): 80.5%
- spec必須フィールド率(avg): 49.4%
- spec field adoption率(avg): 23.3%
- impossible precision/recall/F1: 22.8% / 56.1% / 32.4%
- reason+revision成立率(avg): 0.0%
- 反復崩壊率: 5.6%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_unseen_001 | classic_eval_unseen | no | no | no | 0.0% | 466 |
| classic_eval_unseen_003 | classic_eval_unseen | no | no | no | 0.0% | 471 |
| classic_eval_era_008 | classic_eval_era | no | no | no | 0.0% | 473 |
| classic_eval_extended_lb_066 | classic_eval_extended_lb | no | no | no | 0.0% | 581 |
| lora_case_00018 | low_cost | no | no | no | 0.0% | 469 |
| lora_case_00027 | constraint_torpedo | no | no | no | 0.0% | 472 |
| lora_case_00033 | constraint_torpedo | no | no | no | 0.0% | 593 |
| lora_case_00052 | constraint_range | no | no | no | 0.0% | 468 |
| lora_case_00066 | constraint_range | no | no | no | 0.0% | 473 |
| lora_case_00087 | constraint_torpedo | no | no | no | 0.0% | 466 |
