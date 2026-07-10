# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-10

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2-iter250`
- run_slug: `qwen2_5_0_5b_stage2-iter250`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 100.0%
- schema完全一致率: 100.0%
- status一致率: 95.2%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 27.3%
- spec field adoption率(avg): 19.2%
- impossible precision/recall/F1: 99.7% / 94.1% / 96.8%
- reason+revision成立率(avg): 72.7%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_small_gun_0000 | eval_small_gun | yes | yes | yes | 0.0% | 516 |
| eval_small_gun_0001 | eval_small_gun | yes | yes | yes | 0.0% | 510 |
| eval_small_gun_0002 | eval_small_gun | yes | yes | yes | 0.0% | 522 |
| eval_small_gun_0003 | eval_small_gun | yes | yes | yes | 0.0% | 517 |
| eval_small_gun_0004 | eval_small_gun | yes | yes | yes | 0.0% | 526 |
| eval_small_gun_0005 | eval_small_gun | yes | yes | yes | 0.0% | 520 |
| eval_small_gun_0006 | eval_small_gun | yes | yes | yes | 0.0% | 512 |
| eval_small_gun_0007 | eval_small_gun | yes | yes | yes | 0.0% | 517 |
| eval_small_gun_0008 | eval_small_gun | yes | yes | yes | 0.0% | 516 |
| eval_small_gun_0009 | eval_small_gun | yes | yes | yes | 0.0% | 689 |
