# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-10

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: _(none, zero-shot)_
- run_slug: `qwen2_5_0_5b_zero_shot`
- eval_mode: `free_raw` (json_prefill=none)
- total: 622
- raw JSON成功率: 95.5%
- schema完全一致率: 0.0%
- status一致率: 18.2%
- top必須フィールド率(avg): 69.8%
- spec必須フィールド率(avg): 34.5%
- spec field adoption率(avg): 16.2%
- impossible precision/recall/F1: 29.8% / 65.3% / 40.9%
- reason+revision成立率(avg): 0.0%
- 反復崩壊率: 3.1%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_unseen_001 | classic_eval_unseen | yes | no | no | 0.0% | 598 |
| classic_eval_unseen_002 | classic_eval_unseen | yes | no | no | 0.0% | 641 |
| classic_eval_unseen_004 | classic_eval_unseen | no | no | no | 0.0% | 443 |
| classic_eval_unseen_005 | classic_eval_unseen | yes | no | no | 0.0% | 640 |
| classic_eval_era_001 | classic_eval_era | yes | no | no | 0.0% | 643 |
| classic_eval_era_002 | classic_eval_era | yes | no | no | 0.0% | 637 |
| classic_eval_era_004 | classic_eval_era | yes | no | no | 0.0% | 646 |
| classic_eval_era_005 | classic_eval_era | yes | no | no | 0.0% | 644 |
| classic_eval_era_006 | classic_eval_era | yes | no | no | 0.0% | 645 |
| classic_eval_era_007 | classic_eval_era | yes | no | no | 0.0% | 704 |
