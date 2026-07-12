# Qwen2.5-0.5B-Instruct navspec eval

作成日: 2026-07-12

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter_path: `models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8`
- run_slug: `qwen2_5_0_5b_stage2-status-r8`
- eval_mode: `free_raw` (json_prefill=none)
- stage2: `True`
- total: 622
- raw JSON成功率: 100.0%
- schema完全一致率: 94.4%
- status一致率: 90.5%
- top必須フィールド率(avg): 100.0%
- spec必須フィールド率(avg): 73.3%
- spec field adoption率(avg): 56.4%
- impossible precision/recall/F1: 86.1% / 79.9% / 82.9%
- reason+revision成立率(avg): 26.7%
- 反復崩壊率: 0.0%
- legacy reason exact: 82.6%
- strict reason exact: 82.6%
- semantic reason micro P/R/F1: 79.3% / 83.7% / 81.4%
- primary reason accuracy: 74.3%
- extra/missing/invalid reason rate: 25.1% / 27.9% / 0.0%
- JSON damage: {'length_truncation': 0, 'early_eos_invalid_json': 0, 'root_parse_error': 0, 'missing_closing_brace': 0, 'reason_array_truncation': 0, 'suggested_revision_truncation': 0}

## Worst Cases

| id | category | raw_json | schema | status | spec_adoption | raw_len |
|---|---|---:|---:|---:|---:|---:|
| classic_eval_extended_contradiction_001 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_011 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_021 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_031 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_041 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_051 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_061 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_071 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_081 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
| classic_eval_extended_contradiction_091 | common_classic_eval_extended_contradiction | yes | no | yes | 0.0% | 679 |
