# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-12

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2.1-iter500`
- run_slug: `qwen2_5_0_5b_stage2.1-iter500`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 622
- raw JSON成功率: 100.0%
- schema完全一致率: 94.2%
- status一致率: 85.7%
- top必須フィールド率(avg): 99.8%
- spec必須フィールド率(avg): 63.3%
- spec field adoption率(avg): 52.3%
- impossible precision/recall/F1: 70.0% / 88.8% / 78.3%
- reason+revision成立率(avg): 36.5%
- 反復崩壊率: 0.0%
- legacy reason exact: 78.9%
- strict reason exact: 78.9%
- semantic reason micro P/R/F1: 72.3% / 91.0% / 80.6%
- primary reason accuracy: 84.9%
- extra/missing/invalid reason rate: 21.2% / 15.6% / 0.0%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 1, 'root_parse_error': 1, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| lora_case_00050 | common_constraint_speed | yes | no | no | 0.0% | 596 |
| classic_eval_extended_contradiction_001 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_011 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_021 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_031 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_041 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_051 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_061 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_071 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_081 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
