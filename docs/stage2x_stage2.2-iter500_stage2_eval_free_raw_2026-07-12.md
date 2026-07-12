# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-12

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.2-balanced-reason-r8`
- run_slug: `qwen2_5_0_5b_stage2.2-balanced-reason-r8`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 100.0%
- schema完全一致率: 93.8%
- status一致率: 95.6%
- top必須フィールド率(avg): 95.7%
- spec必須フィールド率(avg): 26.5%
- spec field adoption率(avg): 21.7%
- impossible precision/recall/F1: 100.0% / 94.3% / 97.1%
- reason+revision成立率(avg): 70.0%
- 反復崩壊率: 0.0%
- legacy reason exact: 81.2%
- strict reason exact: 81.2%
- semantic reason micro P/R/F1: 94.6% / 89.2% / 91.8%
- primary reason accuracy: 86.8%
- extra/missing/invalid reason rate: 8.1% / 16.2% / 0.0%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 30, 'root_parse_error': 30, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_era_0001 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0005 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0009 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0013 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0014 | eval_era | yes | no | no | 0.0% | 295 |
| eval_era_0017 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0021 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0025 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0029 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0033 | eval_era | yes | no | no | 0.0% | 292 |
