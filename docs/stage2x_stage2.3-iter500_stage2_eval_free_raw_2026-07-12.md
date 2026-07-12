# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-12

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter500`
- run_slug: `qwen2_5_0_5b_stage2.3-iter500`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 100.0%
- schema完全一致率: 90.4%
- status一致率: 99.2%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 23.8%
- spec field adoption率(avg): 21.6%
- impossible precision/recall/F1: 100.0% / 98.9% / 99.5%
- reason+revision成立率(avg): 76.2%
- 反復崩壊率: 0.0%
- legacy reason exact: 76.2%
- strict reason exact: 75.6%
- semantic reason micro P/R/F1: 84.8% / 94.4% / 89.4%
- primary reason accuracy: 95.4%
- extra/missing/invalid reason rate: 25.1% / 8.9% / 3.8%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 0, 'root_parse_error': 0, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_armor_0003 | eval_armor | yes | no | yes | 0.0% | 344 |
| eval_armor_0004 | eval_armor | yes | no | yes | 0.0% | 323 |
| eval_armor_0006 | eval_armor | yes | no | yes | 0.0% | 381 |
| eval_armor_0007 | eval_armor | yes | no | yes | 0.0% | 323 |
| eval_armor_0009 | eval_armor | yes | no | yes | 0.0% | 478 |
| eval_armor_0010 | eval_armor | yes | no | yes | 0.0% | 353 |
| eval_armor_0011 | eval_armor | yes | no | yes | 0.0% | 323 |
| eval_armor_0013 | eval_armor | yes | no | yes | 0.0% | 478 |
| eval_armor_0015 | eval_armor | yes | no | yes | 0.0% | 413 |
| eval_armor_0019 | eval_armor | yes | no | yes | 0.0% | 413 |
