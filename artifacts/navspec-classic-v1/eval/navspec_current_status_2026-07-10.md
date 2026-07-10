# navspec 現状レポート

作成日: 2026-07-10
対象: 次フェーズ実装後の生成器・評価スイート

## サマリー

- `estimate_dimensions` は艦種別 L/B 比へ調整済み。
- contradiction detector は `status=impossible`、`reason[]`、`suggested_revision` を返す経路を実装済み。
- nation capability tier による排水量・主砲・機関性能・レーダー採用時期・大型艦可否の制限を実装済み。
- `eval_suite` は既存22件に加えて100件の拡張テストを自動生成・評価する。

## 評価結果

実行コマンド:

```bash
uv run python -m tools.eval_suite --extended-count 100 --json-report data/navspec/generated/eval/report.json
```

結果:

| 指標 | 結果 |
|---|---:|
| 総テスト数 | 122 |
| パッチ後JSON成功 | 122/122 |
| schemaエラー0件 | 122/122 |
| L/B ratio FAIL | 0 |
| contradiction detection FAIL | 0 |
| nation tier反映 FAIL | 0 |
| impossible応答 | 42 |
| plausibility accepted | 72/122 |
| plausibility score | avg 90.9 / min 76.0 / max 97.0 |

## 実装済み範囲

- 艦種別 L/B 比:
  - destroyer系: 9.0-11.5
  - cruiser系: 8.5-10.5
  - battleship系: 6.5-8.5
  - patrol/gunboat系: 5.0-8.0
  - ship_of_the_line: 3.5-5.0

- 矛盾検出:
  - 小型艦への大口径砲・重装甲・高速度・長航続の同時要求
  - 年代不可能装備
  - 条約期の排水量・主砲制限違反
  - 国家ティア超過

- 国家ティア:
  - tier別の最大排水量、最大主砲口径、機関性能倍率、レーダー採用年、大型艦可否を適用。
  - 生成後の `nation_capability_tier` と `constraint_adjustments` に反映。

## 残課題

- 小型艦の `main_caliber_range` や `displacement_area_ratio` は一部 review のまま。
- large_destroyer / commerce_raider_cruiser など、艦種別主砲評価の細分化は追加改善余地あり。
- モデルraw出力は依然ルールベース補正への依存が大きい。

## 成果物

- 評価JSON: `data/navspec/generated/eval/report.json`
- 詳細レポート: `docs/eval_suite_report_2026-07-10.md`
