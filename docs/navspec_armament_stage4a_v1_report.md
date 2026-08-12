# navspec Stage 4a Armament v1 継続実験・評価レポート

**作成日**: 2026-08-12  
**対象コンポーネント**: Stage 4a Armament (データ生成 v1, Full Armament Adapter v1, Verdict Auxiliary Model v1/v2)  
**保護対象**: 既存 Stage 2 default (`navspec-qwen2.5-0.5b-stage2-r8`) / v0 adapter / classic validator は一切変更なし

---

## 1. 概要

本レポートは、`navspec` Stage 4a Armament パイプラインにおける現行 DB v1 を用いた再生成・結合データセットに対する学習および詳細評価結果をまとめたものである。  
データ再生成・データ結合・モデル学習・評価の全工程を完了した。形式（JSON構造および契約遵守）の再現性は極めて高い一方、判定器（status / reason code の正解率）としては多数派ベースライン付近に留まっており、Verdict 補助タスクも含めて判定ロジックの獲得には課題が残る結果となった。

---

## 2. データセット実測値 (v1)

### 2.1 Armament DB 実測値
- **Weapons**: 71件
- **Mounts**: 71件
- **Ammunition**: 71件
- **Fire Control**: 17件
- **収録国数**: 11 nations
- **Weapon 導入年**: 1910–1949
- **Mount 導入年 null**: 4件（未決定のまま維持）

### 2.2 データセット分割件数

| データセット | 総行数 (rows) | ok 件数 | impossible 件数 | train | valid | test | 備考 |
|---|---|---|---|---|---|---|---|
| **Generation v1** | 1,200 | 832 | 368 | 866 | 177 | 157 | 教師データ生成タスク |
| **Critique v1** | 980 | 437 | 543 | 679 | 159 | 142 | 批評タスク（900要求、10件 skipped_no_material） |
| **Combined v1** | 2,180 | 1,269 | 911 | 1,545 | 336 | 299 | Generation v1 + Critique v1 結合 |

### 2.3 Reason Code & Warning Code の分布
- **Reason Code**: 
  - **Combined v1 (generation v1 + critique v1)** の `reason[]` カバレッジ: 全14種中 **10/14 種**。
  - **Critique-only v1** の `reason[]` カバレッジ: 全14種中 **7/14 種**（Combined v1 の 10/14 とは母集団が異なる）。
- **Warning-only Code (4種)**: 
  - `barbette_too_large_for_beam`
  - `magazine_volume_insufficient`
  - `topweight_limit_exceeded`
  - `blast_interference`
  - これら4種は警告（warning）としてのみ出力されるため、`reason[]` には出現しない（reason[] カバレッジを 14/14 とはカウントしない）。ただし warning code 自体は学習データセット内に含まれている。

---

## 3. Full Armament Adapter 実験結果 (stage4a-armament-r8-v1)

### 3.1 ハイパーパラメータ & 学習経過
- **ベースモデル**: Qwen/Qwen2.5-0.5B-Instruct
- **学習設定**: 500 iter, 16 layers (LoRA target), lr = 5e-6, mask-prompt 有効
- **学習損失**: final Val loss = 0.102

### 3.2 評価指標 (`docs/navspec_armament_stage4a_eval_v1.json`, test全299件)

| 指標 (Metric) | 実測値 | 説明 |
|---|---|---|
| **json_valid_rate** | **0.9599** (287/299) | 有効なJSONとしてパース成功した割合 |
| **contract_ok_rate** | **0.9498** (284/299) | `navspec.armament_teacher.v1` 契約を満たす割合 |
| **status_accuracy** | **0.5552** (166/299) | status (ok/impossible) の予測一致率 |
| **reason_code_f1** | **0.2331** | reason code の F1 スコア (Precision: 0.1906, Recall: 0.3000) |
| **warning_code_f1** | **0.0444** | warning code の F1 スコア (Precision: 0.5000, Recall: 0.0233) |
| **reason_set_exact_rate** | **0.2676** (80/299) | reason code 集合の完全一致率 |
| **warning_set_exact_rate** | **0.6990** (209/299) | warning code 集合の完全一致率 |

### 3.3 予測傾向および未知 Reason Code
- **status 予測件数**: `impossible`: 181件 / `ok`: 105件 （未パース・不整合を除く）
- **未知 Reason Code の発生**: 以下の3種類の定義外/ハルシネーション reason code が計3件発生した。
  1. `armament.armament_count_incompatible` (1件)
  2. `armament.armament_weight_limit_exceeded` (1件)
  3. `armament.nation_unavailable` (1件)

---

## 4. Verdict Auxiliary Task 実験結果 (診断用補助タスク)

判定機能に特化させた補助モデル（Verdict タスク）の学習と検証を、Critique v1 の全980件データセットを用いて実施した。

| モデルバージョン | JSON構造特徴 | Final Val Loss | Test 142件 JSON/Contract | Status Accuracy | Reason F1 | 予測 status 分布 | 判定 |
|---|---|---|---|---|---|---|---|
| **Verdict v1** | `status` キーが JSON **末尾** | 0.070 | 1.0 (142/142) | 0.4296 (61/142) | N/A (0.0) | `ok`: 142件固定 / reason: 0 | **採用不可** |
| **Verdict v2** | `status` キーを JSON **先頭**に変更 | 0.078 | 1.0 (142/142) | 0.4296 (61/142) | N/A (0.0) | `ok`: 142件固定 / reason: 0 | **採用不可** |

**評価結果考察**:
v1・v2 ともに JSON フォーマット遵守率は 100% と完璧であったが、予測が全件 `status: ok`（かつ reason 空）に崩壊・固定化し、ルール判定器として全く機能しなかったため診断用実験として打ち切り、本採用は見送られた。

---

## 5. 検証・品質状況

- **Seed Validator**: 正常（0 errors / 0 warnings）
- **ユニットテスト**: `PYTHONPATH=. pytest` を実行し、Stage 4a 関連・combiner・verdict テストを含む全体テストスイートが正常パス（`PYTHONPATH=. pytest` 全体 82 passed）。
- **評価対象件数**: Full v1 モデルの評価は実生成 299 件の test 分割全件で正確に検証。

---

## 6. 結論と次のステップ

### 6.1 結論
1. データ再生成、結合、LoRA 学習、評価スクリプトのパイプライン構築および実行は正常に完了した。
2. 0.5B モデルは複雑な JSON スキーマや契約形式を高精度（~96%）で習得可能である。
3. しかし、判定能力（`status` 判定精度 0.5552）は多数派ベースライン（~0.5452）とほぼ同等であり、ルールエンジンのロジック学習には至っていない。Verdict 補助タスクにおいても全件 `ok` 予測にバイアスされた。
4. 既存の Stage 2 default や v0 アダプター等の動作環境への悪影響はない（保護対象不変）。

### 6.2 今後の改善候補 (Next Steps)
1. **クラス別オーバーサンプリング & 損失関数の再設計**:
   - `impossible` や特定の reason/warning トークンに対する損失重み付けの導入。
2. **より大きいベースモデルの適用**:
   - Qwen2.5-3B / 7B 等、より高い推論・条件判定能力を持つモデルでの検証。
3. **データ漏洩防止と分割の厳格化**:
   - 生成タスク (Generation) と批評タスク (Critique) 間における同一条件のデータ漏洩を防ぐ厳密なデータスプリット設計。
4. **Warning 評価の精緻化**:
   - Warning-only コードの評価手法の向上と独立した検証の継続。
