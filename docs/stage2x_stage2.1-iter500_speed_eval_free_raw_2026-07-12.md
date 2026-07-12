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
- schema完全一致率: 50.0%
- status一致率: 85.0%
- top必須フィールド率(avg): 85.0%
- spec必須フィールド率(avg): 30.0%
- spec field adoption率(avg): 24.0%
- impossible precision/recall/F1: 100.0% / 78.6% / 88.0%
- reason+revision成立率(avg): 55.0%
- 反復崩壊率: 0.0%
- legacy reason exact: 65.0%
- strict reason exact: 30.0%
- semantic reason micro P/R/F1: 86.2% / 89.3% / 87.7%
- primary reason accuracy: 78.6%
- extra/missing/invalid reason rate: 28.6% / 21.4% / 0.0%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 60, 'root_parse_error': 60, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_range_volume_impossible_0000 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
| eval_range_volume_impossible_0001 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
| eval_range_volume_impossible_0002 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
| eval_range_volume_impossible_0003 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
| eval_range_volume_impossible_0004 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
| eval_range_volume_impossible_0005 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
| eval_range_volume_impossible_0006 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
| eval_range_volume_impossible_0007 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
| eval_range_volume_impossible_0008 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
| eval_range_volume_impossible_0009 | eval_range_volume_impossible | yes | no | no | 0.0% | 343 |
