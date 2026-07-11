# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-11

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter250`
- run_slug: `qwen2_5_0_5b_stage2.3-iter250`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 100.0%
- schema完全一致率: 98.1%
- status一致率: 99.2%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 23.3%
- spec field adoption率(avg): 21.5%
- impossible precision/recall/F1: 99.7% / 99.2% / 99.5%
- reason+revision成立率(avg): 76.7%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_armor_0006 | eval_armor | yes | no | yes | 0.0% | 412 |
| eval_armor_0010 | eval_armor | yes | no | yes | 0.0% | 412 |
| eval_armor_0022 | eval_armor | yes | no | yes | 0.0% | 412 |
| eval_armor_0026 | eval_armor | yes | no | yes | 0.0% | 412 |
| eval_armor_0038 | eval_armor | yes | no | yes | 0.0% | 412 |
| eval_armor_0042 | eval_armor | yes | no | yes | 0.0% | 412 |
| eval_armor_0054 | eval_armor | yes | no | yes | 0.0% | 412 |
| eval_armor_0058 | eval_armor | yes | no | yes | 0.0% | 412 |
| eval_armor_0059 | eval_armor | yes | no | yes | 0.0% | 541 |
| eval_small_gun_0000 | eval_small_gun | yes | yes | yes | 0.0% | 321 |
