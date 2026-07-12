# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-12

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.1-iter500`
- run_slug: `qwen2_5_0_5b_stage2.1-iter500`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 100.0%
- schema完全一致率: 87.5%
- status一致率: 99.2%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 22.9%
- spec field adoption率(avg): 21.6%
- impossible precision/recall/F1: 99.5% / 99.5% / 99.5%
- reason+revision成立率(avg): 77.1%
- 反復崩壊率: 0.0%
- legacy reason exact: 88.1%
- strict reason exact: 79.8%
- semantic reason micro P/R/F1: 94.7% / 95.8% / 95.2%
- primary reason accuracy: 99.2%
- extra/missing/invalid reason rate: 8.1% / 6.8% / 0.0%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 0, 'root_parse_error': 0, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_speed_armor_range_0000 | eval_speed_armor_range | yes | no | yes | 0.0% | 684 |
| eval_speed_armor_range_0001 | eval_speed_armor_range | yes | no | yes | 0.0% | 610 |
| eval_speed_armor_range_0002 | eval_speed_armor_range | yes | no | yes | 0.0% | 614 |
| eval_speed_armor_range_0003 | eval_speed_armor_range | yes | no | yes | 0.0% | 684 |
| eval_speed_armor_range_0004 | eval_speed_armor_range | yes | no | yes | 0.0% | 610 |
| eval_speed_armor_range_0005 | eval_speed_armor_range | yes | no | yes | 0.0% | 614 |
| eval_speed_armor_range_0006 | eval_speed_armor_range | yes | no | yes | 0.0% | 684 |
| eval_speed_armor_range_0007 | eval_speed_armor_range | yes | no | yes | 0.0% | 610 |
| eval_speed_armor_range_0008 | eval_speed_armor_range | yes | no | yes | 0.0% | 614 |
| eval_speed_armor_range_0009 | eval_speed_armor_range | yes | no | yes | 0.0% | 684 |
