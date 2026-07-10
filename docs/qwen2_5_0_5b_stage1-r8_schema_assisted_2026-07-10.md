# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-10

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage1-r8`
- run_slug: `qwen2_5_0_5b_stage1-r8`
- eval_mode: `schema_assisted` (json_prefill=schema)
- total: 622
- raw JSON成功率: 99.8%
- schema完全一致率: 0.0%
- status一致率: 53.4%
- top必須フィールド率(avg): 50.7%
- spec必須フィールド率(avg): 22.4%
- spec field adoption率(avg): 16.3%
- impossible precision/recall/F1: 17.4% / 15.0% / 16.1%
- reason+revision成立率(avg): 0.0%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_constraint_006 | classic_eval_constraint | yes | no | no | 0.0% | 141 |
| lora_case_00046 | constraint_range | yes | no | no | 0.0% | 243 |
| lora_case_00090 | constraint_speed | yes | no | no | 0.0% | 227 |
| lora_case_00149 | constraint_displacement | yes | no | no | 0.0% | 226 |
| lora_case_00212 | constraint_range | yes | no | no | 0.0% | 228 |
| lora_case_00230 | constraint_speed | yes | no | no | 0.0% | 227 |
| lora_case_00286 | constraint_range | yes | no | no | 0.0% | 227 |
| lora_case_00289 | constraint_displacement | no | no | no | 0.0% | 1086 |
| lora_case_00395 | constraint_displacement | yes | no | no | 0.0% | 231 |
| lora_case_00426 | constraint_range | yes | no | no | 0.0% | 227 |
