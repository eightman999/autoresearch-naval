# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-10

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage1-r8`
- run_slug: `qwen2_5_0_5b_stage1-r8`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 99.2%
- schema完全一致率: 87.1%
- status一致率: 32.7%
- top必須フィールド率(avg): 99.0%
- spec必須フィールド率(avg): 87.5%
- spec field adoption率(avg): 15.3%
- impossible precision/recall/F1: 94.7% / 14.6% / 25.3%
- reason+revision成立率(avg): 0.0%
- 反復崩壊率: 0.8%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_small_gun_0034 | eval_small_gun | yes | no | no | 0.0% | 549 |
| eval_hard_negative_ok_0002 | eval_hard_negative_ok | no | no | no | 0.0% | 281 |
| eval_hard_negative_ok_0027 | eval_hard_negative_ok | no | no | no | 0.0% | 281 |
| eval_hard_negative_ok_0032 | eval_hard_negative_ok | no | no | no | 0.0% | 284 |
| eval_hard_negative_ok_0057 | eval_hard_negative_ok | no | no | no | 0.0% | 281 |
| eval_era_0001 | eval_era | yes | no | yes | 0.0% | 517 |
| eval_era_0005 | eval_era | yes | no | yes | 0.0% | 539 |
| eval_era_0017 | eval_era | yes | no | yes | 0.0% | 517 |
| eval_era_0021 | eval_era | yes | no | yes | 0.0% | 539 |
| eval_era_0033 | eval_era | yes | no | yes | 0.0% | 517 |
