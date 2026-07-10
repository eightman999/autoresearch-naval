# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-10

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: _(none, zero-shot)_
- run_slug: `qwen2_5_0_5b_zero_shot`
- eval_mode: `object_prefill` (json_prefill=object)
- total: 622
- raw JSON成功率: 97.1%
- schema完全一致率: 0.0%
- status一致率: 24.4%
- top必須フィールド率(avg): 74.9%
- spec必須フィールド率(avg): 36.6%
- spec field adoption率(avg): 22.0%
- impossible precision/recall/F1: 33.6% / 87.9% / 48.6%
- reason+revision成立率(avg): 0.0%
- 反復崩壊率: 1.6%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_unseen_002 | classic_eval_unseen | no | no | no | 0.0% | 440 |
| classic_eval_unseen_004 | classic_eval_unseen | yes | no | no | 0.0% | 618 |
| classic_eval_era_001 | classic_eval_era | yes | no | no | 0.0% | 643 |
| classic_eval_era_008 | classic_eval_era | no | no | no | 0.0% | 443 |
| classic_eval_constraint_005 | classic_eval_constraint | no | no | no | 0.0% | 397 |
| classic_eval_extended_lb_019 | classic_eval_extended_lb | yes | no | no | 0.0% | 670 |
| classic_eval_extended_lb_045 | classic_eval_extended_lb | yes | no | no | 0.0% | 670 |
| classic_eval_extended_lb_056 | classic_eval_extended_lb | yes | no | no | 0.0% | 631 |
| classic_eval_extended_lb_066 | classic_eval_extended_lb | no | no | no | 0.0% | 548 |
| lora_case_00007 | constraint_torpedo | yes | no | no | 0.0% | 657 |
