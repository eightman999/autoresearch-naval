# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-10

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage1-r8`
- run_slug: `qwen2_5_0_5b_stage1-r8`
- eval_mode: `object_prefill` (json_prefill=object)
- total: 622
- raw JSON成功率: 94.4%
- schema完全一致率: 85.0%
- status一致率: 75.9%
- top必須フィールド率(avg): 94.1%
- spec必須フィールド率(avg): 81.5%
- spec field adoption率(avg): 66.7%
- impossible precision/recall/F1: 100.0% / 14.5% / 25.3%
- reason+revision成立率(avg): 4.0%
- 反復崩壊率: 0.0%

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_era_003 | classic_eval_era | yes | no | no | 0.0% | 596 |
| lora_case_00396 | constraint_speed | yes | no | no | 0.0% | 559 |
| classic_eval_extended_contradiction_001 | classic_eval_extended_contradiction | yes | no | no | 0.0% | 580 |
| classic_eval_extended_contradiction_011 | classic_eval_extended_contradiction | yes | no | no | 0.0% | 580 |
| classic_eval_extended_contradiction_021 | classic_eval_extended_contradiction | yes | no | no | 0.0% | 580 |
| classic_eval_extended_contradiction_031 | classic_eval_extended_contradiction | yes | no | no | 0.0% | 580 |
| classic_eval_extended_contradiction_041 | classic_eval_extended_contradiction | yes | no | no | 0.0% | 580 |
| classic_eval_extended_contradiction_051 | classic_eval_extended_contradiction | yes | no | no | 0.0% | 580 |
| classic_eval_extended_contradiction_061 | classic_eval_extended_contradiction | yes | no | no | 0.0% | 580 |
| classic_eval_extended_contradiction_071 | classic_eval_extended_contradiction | yes | no | no | 0.0% | 580 |
