# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-11

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter125`
- run_slug: `qwen2_5_0_5b_stage2.3-iter125`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 400
- raw JSON成功率: 100.0%
- schema完全一致率: 100.0%
- status一致率: 93.5%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 41.5%
- spec field adoption率(avg): 33.5%
- impossible precision/recall/F1: 100.0% / 90.0% / 94.7%
- reason+revision成立率(avg): 58.5%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_era_equipment_impossible_0000 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 258 |
| eval_era_equipment_impossible_0001 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 287 |
| eval_era_equipment_impossible_0002 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 258 |
| eval_era_equipment_impossible_0003 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 290 |
| eval_era_equipment_impossible_0004 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 258 |
| eval_era_equipment_impossible_0005 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 287 |
| eval_era_equipment_impossible_0006 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 258 |
| eval_era_equipment_impossible_0007 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 290 |
| eval_era_equipment_impossible_0008 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 258 |
| eval_era_equipment_impossible_0009 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 287 |
