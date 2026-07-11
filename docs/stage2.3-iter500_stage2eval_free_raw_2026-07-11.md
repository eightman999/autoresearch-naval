# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-11

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter500`
- run_slug: `qwen2_5_0_5b_stage2.3-iter500`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 100.0%
- schema完全一致率: 97.3%
- status一致率: 99.2%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 23.8%
- spec field adoption率(avg): 21.6%
- impossible precision/recall/F1: 100.0% / 98.9% / 99.5%
- reason+revision成立率(avg): 76.2%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_armor_0006 | eval_armor | yes | no | yes | 0.0% | 451 |
| eval_armor_0009 | eval_armor | yes | no | yes | 0.0% | 537 |
| eval_armor_0010 | eval_armor | yes | no | yes | 0.0% | 332 |
| eval_armor_0013 | eval_armor | yes | no | yes | 0.0% | 478 |
| eval_armor_0022 | eval_armor | yes | no | yes | 0.0% | 451 |
| eval_armor_0026 | eval_armor | yes | no | yes | 0.0% | 451 |
| eval_armor_0029 | eval_armor | yes | no | yes | 0.0% | 537 |
| eval_armor_0038 | eval_armor | yes | no | yes | 0.0% | 480 |
| eval_armor_0042 | eval_armor | yes | no | yes | 0.0% | 451 |
| eval_armor_0045 | eval_armor | yes | no | yes | 0.0% | 478 |
