# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-11

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.2-balanced-reason-r8`
- run_slug: `qwen2_5_0_5b_stage2.2-balanced-reason-r8`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 400
- raw JSON成功率: 100.0%
- schema完全一致率: 100.0%
- status一致率: 100.0%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 30.0%
- spec field adoption率(avg): 30.0%
- impossible precision/recall/F1: 100.0% / 100.0% / 100.0%
- reason+revision成立率(avg): 70.0%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_speed_power_impossible_0000 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
| eval_speed_power_impossible_0001 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
| eval_speed_power_impossible_0002 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
| eval_speed_power_impossible_0003 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
| eval_speed_power_impossible_0004 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
| eval_speed_power_impossible_0005 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
| eval_speed_power_impossible_0006 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
| eval_speed_power_impossible_0007 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
| eval_speed_power_impossible_0008 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
| eval_speed_power_impossible_0009 | eval_speed_power_impossible | yes | yes | yes | 0.0% | 262 |
