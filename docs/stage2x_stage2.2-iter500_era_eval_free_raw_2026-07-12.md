# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-12

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.2-balanced-reason-r8`
- run_slug: `qwen2_5_0_5b_stage2.2-balanced-reason-r8`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 400
- raw JSON成功率: 100.0%
- schema完全一致率: 95.0%
- status一致率: 77.5%
- top必須フィールド率(avg): 98.6%
- spec必須フィールド率(avg): 62.5%
- spec field adoption率(avg): 32.8%
- impossible precision/recall/F1: 100.0% / 65.4% / 79.1%
- reason+revision成立率(avg): 37.5%
- 反復崩壊率: 0.0%
- legacy reason exact: 60.0%
- strict reason exact: 60.0%
- semantic reason micro P/R/F1: 100.0% / 48.4% / 65.2%
- primary reason accuracy: 38.5%
- extra/missing/invalid reason rate: 0.0% / 61.5% / 0.0%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 20, 'root_parse_error': 20, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_era_equipment_impossible_0002 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
| eval_era_equipment_impossible_0006 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
| eval_era_equipment_impossible_0010 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
| eval_era_equipment_impossible_0014 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
| eval_era_equipment_impossible_0018 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
| eval_era_equipment_impossible_0022 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
| eval_era_equipment_impossible_0026 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
| eval_era_equipment_impossible_0030 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
| eval_era_equipment_impossible_0034 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
| eval_era_equipment_impossible_0038 | eval_era_equipment_impossible | yes | no | yes | 0.0% | 842 |
