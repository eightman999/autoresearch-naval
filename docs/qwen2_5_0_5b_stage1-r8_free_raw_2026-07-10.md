# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-10

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage1-r8`
- run_slug: `qwen2_5_0_5b_stage1-r8`
- eval_mode: `free_raw` (json_prefill=none)
- total: 622
- raw JSON成功率: 96.0%
- schema完全一致率: 91.3%
- status一致率: 77.7%
- top必須フィールド率(avg): 95.8%
- spec必須フィールド率(avg): 80.1%
- spec field adoption率(avg): 65.1%
- impossible precision/recall/F1: 100.0% / 20.2% / 33.7%
- reason+revision成立率(avg): 4.0%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_era_001 | classic_eval_era | yes | no | no | 0.0% | 555 |
| lora_case_00000 | impossible_small_ship_combo | yes | no | no | 0.0% | 548 |
| lora_case_00020 | impossible_small_ship_combo | yes | no | no | 0.0% | 548 |
| lora_case_00040 | impossible_small_ship_combo | yes | no | no | 0.0% | 548 |
| lora_case_00060 | impossible_small_ship_combo | yes | no | no | 0.0% | 548 |
| lora_case_00080 | impossible_small_ship_combo | yes | no | no | 0.0% | 548 |
| lora_case_00100 | impossible_small_ship_combo | yes | no | no | 0.0% | 548 |
| lora_case_00120 | impossible_small_ship_combo | yes | no | no | 0.0% | 548 |
| lora_case_00140 | impossible_small_ship_combo | yes | no | no | 0.0% | 548 |
| lora_case_00160 | impossible_small_ship_combo | yes | no | no | 0.0% | 548 |
