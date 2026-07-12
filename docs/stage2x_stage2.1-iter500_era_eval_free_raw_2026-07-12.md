# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-12

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.1-iter500`
- run_slug: `qwen2_5_0_5b_stage2.1-iter500`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 400
- raw JSON成功率: 100.0%
- schema完全一致率: 100.0%
- status一致率: 84.2%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 25.8%
- spec field adoption率(avg): 21.5%
- impossible precision/recall/F1: 83.2% / 95.0% / 88.7%
- reason+revision成立率(avg): 74.2%
- 反復崩壊率: 0.0%
- legacy reason exact: 64.2%
- strict reason exact: 64.2%
- semantic reason micro P/R/F1: 65.0% / 95.8% / 77.4%
- primary reason accuracy: 95.0%
- extra/missing/invalid reason rate: 30.8% / 5.0% / 0.0%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 0, 'root_parse_error': 0, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_era_equipment_impossible_0000 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 449 |
| eval_era_equipment_impossible_0001 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 461 |
| eval_era_equipment_impossible_0002 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 446 |
| eval_era_equipment_impossible_0003 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 448 |
| eval_era_equipment_impossible_0004 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 449 |
| eval_era_equipment_impossible_0005 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 461 |
| eval_era_equipment_impossible_0006 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 446 |
| eval_era_equipment_impossible_0007 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 448 |
| eval_era_equipment_impossible_0008 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 449 |
| eval_era_equipment_impossible_0009 | eval_era_equipment_impossible | yes | yes | yes | 0.0% | 461 |
