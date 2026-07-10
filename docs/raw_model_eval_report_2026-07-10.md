# raw model eval report

作成日: 2026-07-10

## Summary

- total: 122
- raw JSON成功率: 38.5%
- raw schema完全一致率: 0.0%
- raw必須フィールド充足率(avg): 0.0%
- raw有効specフィールド率(avg): 0.0%
- field adoption率(avg): 0.0%
- patch dependency率(avg): 100.0%
- patch dependency率(field weighted): 100.0%
- 反復崩壊率: 0.0%

## Worst Cases

| test_id | raw_json | schema | required | adoption | patch_dependency | raw_len |
|---|---:|---:|---:|---:|---:|---:|
| unseen_001 | yes | no | 0.0% | 0.0% | 100.0% | 949 |
| unseen_002 | yes | no | 0.0% | 0.0% | 100.0% | 726 |
| unseen_003 | yes | no | 0.0% | 0.0% | 100.0% | 889 |
| unseen_004 | no | no | 0.0% | 0.0% | 100.0% | 857 |
| unseen_005 | no | no | 0.0% | 0.0% | 100.0% | 907 |

## Notes

- この評価はモデルraw出力だけを採点し、統合パイプラインの成功率とは分離している。
- `patch_dependency_rate` は、最終数値フィールドのうち raw 値を採用できなかった割合。
- 詳細JSONには各数値フィールドの raw値、patched値、delta、patch_reason を保存している。
