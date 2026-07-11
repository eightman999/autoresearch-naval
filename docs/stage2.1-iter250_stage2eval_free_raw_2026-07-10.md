# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-11

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.1-iter250`
- run_slug: `qwen2_5_0_5b_stage2.1-iter250`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 100.0%
- schema完全一致率: 95.8%
- status一致率: 94.2%
- top必須フィールド率(avg): 95.8%
- spec必須フィールド率(avg): 22.5%
- spec field adoption率(avg): 20.2%
- impossible precision/recall/F1: 98.6% / 93.8% / 96.1%
- reason+revision成立率(avg): 73.3%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_speed_armor_range_0000 | eval_speed_armor_range | yes | no | no | 0.0% | 682 |
| eval_speed_armor_range_0003 | eval_speed_armor_range | yes | no | no | 0.0% | 682 |
| eval_speed_armor_range_0006 | eval_speed_armor_range | yes | no | no | 0.0% | 682 |
| eval_speed_armor_range_0009 | eval_speed_armor_range | yes | no | no | 0.0% | 682 |
| eval_speed_armor_range_0012 | eval_speed_armor_range | yes | no | no | 0.0% | 682 |
| eval_speed_armor_range_0015 | eval_speed_armor_range | yes | no | no | 0.0% | 682 |
| eval_speed_armor_range_0018 | eval_speed_armor_range | yes | no | no | 0.0% | 685 |
| eval_speed_armor_range_0021 | eval_speed_armor_range | yes | no | no | 0.0% | 685 |
| eval_speed_armor_range_0024 | eval_speed_armor_range | yes | no | no | 0.0% | 685 |
| eval_speed_armor_range_0027 | eval_speed_armor_range | yes | no | no | 0.0% | 685 |
