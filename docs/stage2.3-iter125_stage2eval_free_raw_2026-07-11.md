# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-11

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter125`
- run_slug: `qwen2_5_0_5b_stage2.3-iter125`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 480
- raw JSON成功率: 100.0%
- schema完全一致率: 80.4%
- status一致率: 92.1%
- top必須フィールド率(avg): 92.7%
- spec必須フィールド率(avg): 23.5%
- spec field adoption率(avg): 20.2%
- impossible precision/recall/F1: 100.0% / 89.7% / 94.6%
- reason+revision成立率(avg): 69.2%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| eval_treaty_0002 | eval_treaty | yes | no | no | 0.0% | 756 |
| eval_treaty_0003 | eval_treaty | yes | no | no | 0.0% | 756 |
| eval_treaty_0004 | eval_treaty | yes | no | no | 0.0% | 702 |
| eval_treaty_0006 | eval_treaty | yes | no | no | 0.0% | 756 |
| eval_treaty_0007 | eval_treaty | yes | no | no | 0.0% | 756 |
| eval_treaty_0010 | eval_treaty | yes | no | no | 0.0% | 756 |
| eval_treaty_0011 | eval_treaty | yes | no | no | 0.0% | 756 |
| eval_treaty_0014 | eval_treaty | yes | no | no | 0.0% | 756 |
| eval_treaty_0015 | eval_treaty | yes | no | no | 0.0% | 756 |
| eval_treaty_0016 | eval_treaty | yes | no | no | 0.0% | 702 |
