# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-11

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.2-iter250`
- run_slug: `qwen2_5_0_5b_stage2.2-iter250`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 100.0%
- schema完全一致率: 83.8%
- status一致率: 87.5%
- top必須フィールド率(avg): 88.3%
- spec必須フィールド率(avg): 23.8%
- spec field adoption率(avg): 21.7%
- impossible precision/recall/F1: 100.0% / 83.8% / 91.2%
- reason+revision成立率(avg): 64.6%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_armor_0051 | eval_armor | yes | no | no | 0.0% | 312 |
| eval_era_0001 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0002 | eval_era | yes | no | no | 0.0% | 664 |
| eval_era_0003 | eval_era | yes | no | no | 0.0% | 670 |
| eval_era_0005 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0006 | eval_era | yes | no | no | 0.0% | 661 |
| eval_era_0007 | eval_era | yes | no | no | 0.0% | 661 |
| eval_era_0009 | eval_era | yes | no | no | 0.0% | 292 |
| eval_era_0010 | eval_era | yes | no | no | 0.0% | 661 |
| eval_era_0011 | eval_era | yes | no | no | 0.0% | 657 |
