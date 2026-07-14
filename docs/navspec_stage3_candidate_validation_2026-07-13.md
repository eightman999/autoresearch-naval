# navspec Stage 3 候補検証・正式昇格判定レポート

作成日: 2026-07-13  
対象: Stage 3 v1 iter250 / iter500  
baseline: Stage 2 iter1000  
判定: **C. Stage 2を維持**（両候補とも回帰ゲート未達）

---

## 1. 判定サマリー

| checkpoint | 数値品質 | Stage 2回帰 | 622一般化 | 総合判定 |
|---|:--:|:--:|:--:|---|
| Stage 3 v1 iter500 | PASS | **FAIL** | 部分PASS | experimental |
| Stage 3 v1 iter250 | PASS | **FAIL** | — | experimental |
| Stage 2 iter1000 | — | — | — | **default維持** |

### 回帰ゲート結果（Stage 2 eval 480件）

| metric | gate | iter250 | 判定 | iter500 | 判定 |
|---|---|---:|:--:|---:|:--:|
| schema | -2.0pt | +12.3pt | PASS | +11.5pt | PASS |
| status | -1.5pt | -3.8pt | **FAIL** | -5.6pt | **FAIL** |
| impossible F1 | -1.5pt | -2.4pt | **FAIL** | -3.7pt | **FAIL** |
| semantic reason F1 | -5.0pt | -10.2pt | **FAIL** | -7.9pt | **FAIL** |
| truncated JSON | <1% | 0.0% | PASS | 0.2% | PASS |
| repetition | <1% | 0.0% | PASS | 0.2% | PASS |
| invalid reason | <5% | 0.0% | PASS | 0.0% | PASS |

両候補とも status / impossible F1 / semantic reason F1 で回帰ゲート超過。

---

## 2. 評価対象

| model | adapter path |
|---|---|
| A. Stage 2 baseline | `models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/` |
| B. Stage 3 iter250 | `stage3-numeric-quality-r8-v1/0000250_adapters.safetensors` |
| C. Stage 3 iter500 | `stage3-numeric-quality-r8-v1/adapters.safetensors` |

### 保護対象確認

以下はすべて不変:

```
Stage 1/2/2.1/2.2/2.3 adapter
Stage 3 v1 adapter (iter250 + iter500)
schemas/navspec_lora_candidate.schema.json
schemas/navspec_lora_candidate_stage2.schema.json
stage2x_eval_contract_v1
stage3_numeric_eval_v0
navspec_stage3_numeric_eval_contract_v1.json
既存レポート・manifest・hash一覧
```

---

## 3. Stage 2回帰評価（480件, free_raw, Stage 2 schema）

### 全体指標

| metric | Stage 2 | iter250 | iter500 | i250-b | i500-b |
|---|---:|---:|---:|---:|---:|
| raw JSON | 100.0% | 100.0% | 99.8% | +0.0pt | -0.2pt |
| schema complete | 87.5% | 99.8% | 99.0% | +12.3pt | +11.5pt |
| status exact | 99.2% | 95.4% | 93.5% | **-3.8pt** | **-5.6pt** |
| impossible precision | 100.0% | 97.5% | 100.0% | -2.5pt | +0.0pt |
| impossible recall | 98.9% | 96.5% | 91.9% | -2.4pt | **-7.0pt** |
| impossible F1 | 99.5% | 97.0% | 95.8% | **-2.4pt** | **-3.7pt** |
| truncated JSON | 0.0% | 0.0% | 0.2% | +0.0pt | +0.2pt |
| repetition | 0.0% | 0.0% | 0.2% | +0.0pt | +0.2pt |

### Reason指標

| metric | Stage 2 | iter250 | iter500 | i250-b | i500-b |
|---|---:|---:|---:|---:|---:|
| strict reason exact | 78.1% | 66.7% | 69.8% | -11.5pt | -8.3pt |
| semantic reason micro F1 | 94.9% | 84.6% | 87.0% | **-10.2pt** | **-7.9pt** |
| primary reason accuracy | 94.1% | 73.0% | 74.6% | -21.1pt | -19.5pt |
| extra reason rate | 4.1% | 7.3% | 8.4% | +3.2pt | +4.3pt |
| missing reason rate | 11.9% | 31.4% | 29.7% | +19.5pt | +17.8pt |
| invalid reason rate | 0.0% | 0.0% | 0.0% | +0.0pt | +0.0pt |

### 回帰の主要因

1. **impossible recall低下**: Stage 3学習データのimpossible比率が10.7%と低く（Stage 2は~54%）、モデルがimpossible判定を学習しにくくなった
2. **missing_reason_rate増加**: impossible caseでreason codeを出力しないケースが増加
3. **primary_reason_accuracy低下**: 原因コードの特定精度が低下
4. **schema改善**: Stage 3学習によりJSON構造の安定性は向上

---

## 4. 622件semantic回帰評価（622件, free_raw, Stage 2 schema）

### 全体指標

| metric | Stage 2 | iter500 | delta |
|---|---:|---:|---:|
| raw JSON | 100.0% | 99.7% | -0.3pt |
| schema complete | 94.4% | 99.4% | **+5.0pt** |
| status exact | 90.2% | 89.9% | -0.3pt |
| impossible precision | 85.5% | 98.4% | **+12.8pt** |
| impossible recall | 79.3% | 67.0% | **-12.3pt** |
| impossible F1 | 82.3% | 79.7% | -2.6pt |
| spec field adoption | 56.2% | 55.2% | -1.0pt |
| spec required present | 73.3% | 80.2% | **+6.9pt** |
| truncated JSON | 0.0% | 0.6% | +0.6pt |
| repetition | 0.0% | 0.0% | +0.0pt |

### Reason指標

| metric | Stage 2 | iter500 | delta |
|---|---:|---:|---:|
| strict reason exact | 82.3% | 88.4% | **+6.1pt** |
| semantic reason micro F1 | 81.1% | 86.0% | **+4.9pt** |
| primary reason accuracy | 73.7% | 66.5% | -7.3pt |
| missing reason rate | 28.5% | 39.1% | +10.6pt |

### 622件での観察

- schema安定性とspec必須フィールド充填率は改善
- impossible precisionは大幅改善（+12.8pt）だがrecallは大幅低下（-12.3pt）
  - モデルが「impossible」と判定した場合は正しいことが多いが、多くのimpossible caseを見逃している
- strict reason exactとsemantic reason micro F1は622件では改善
  - Stage 2 eval(480)では低下していたため、eval setの性質差による分岐
- status exactはほぼ維持（-0.3pt）

---

## 5. Stage 3数値品質評価（1000件 + 200件比較）

### 200件比較（3モデル同一条件）

| metric | Stage 2 | iter250 | iter500 |
|---|---:|---:|---:|
| exact adoption | 74.2% | 75.4% | 76.9% |
| tolerant adoption | 85.0% | 89.5% | 90.7% |
| patch dependency | 15.0% | 10.5% | 9.3% |
| major patch rate | 15.0% | 10.5% | 9.3% |
| constraint follow | 100.0% | 100.0% | 100.0% |
| multi constraint | 100.0% | 100.0% | 100.0% |
| median delta | 0.0160 | 0.0051 | 0.0063 |
| p90 delta | 0.1494 | 0.1599 | 0.1048 |

### 1000件フル評価（iter500のみ）

| metric | 値 | gate | 判定 |
|---|---:|---|:--:|
| exact adoption | 75.3% | ≥35% | PASS |
| tolerant adoption | 90.3% | ≥85% | PASS |
| patch dependency | 9.7% | ≤30% | PASS |
| major patch rate | 9.7% | ≤10% | PASS |
| constraint follow | 78.3% | ≥90% | FAIL |
| multi constraint | 93.3% | ≥80% | PASS |
| median delta | 0.0277 | ≤5% | PASS |
| p90 delta | 2.3214 | ≤15% | FAIL |

### フィールド別（200件比較）

| field | baseline | iter250 | iter500 |
|---|---:|---:|---:|
| beam_m | 93.1% | 100.0% | 99.4% |
| belt_armor_mm | 97.5% | 100.0% | 100.0% |
| draft_m | 97.5% | 100.0% | 99.4% |
| full_load_displacement_t | 89.3% | 100.0% | 97.0% |
| length_m | 93.1% | 100.0% | 100.0% |
| main_gun_caliber_mm | 86.2% | 90.4% | 95.8% |
| main_gun_count | 100.0% | 100.0% | 100.0% |
| power_shp | 56.0% | 55.9% | 63.6% |
| range_nm | 50.9% | 41.9% | 51.5% |
| speed_kt | 76.1% | 87.5% | 85.5% |
| standard_displacement_t | 87.4% | 100.0% | 97.0% |
| torpedo_tubes | 93.1% | 98.5% | 98.8% |

### 数値品質の観察

- iter500は10/12フィールドでbaselineより改善
- iter250はrange_nmでbaselineより劣化（41.9% vs 50.9%）
- range_nmとpower_shpは両候補とも弱点
- iter500はiter250より総合的に優れている

---

## 6. speed / power整合性

### 既知の問題

Stage 3 v0でspeedへの独立jitterがpower関係を破壊。v1ではspeed不変・power=canonical再計算で対処。

### 現状の状況（200件比較）

| metric | baseline | iter500 |
|---|---:|---:|
| speed_kt adoption | 76.1% | 85.5% |
| power_shp adoption | 56.0% | 63.6% |

speedとpowerの間にcubic関係（power ∝ speed³）があるため、speedの誤差がpowerで増幅される構造は残っている。しかしv1ではspeedをcanonicalに固定したため、speed品質は向上し、powerも連鎖的に改善している。

---

## 7. range評価

### range_nmの弱点分析

| metric | baseline | iter250 | iter500 |
|---|---:|---:|---:|
| tolerant adoption | 50.9% | 41.9% | 51.5% |
| major patch | 49.1% | 58.1% | 48.5% |

range_nmは両候補とも改善が限定的。原因:
- classic rule engineの`base_range_nm`関数が role/year/tier から概算する値と、モデルの推定値に乖離がある
- 燃料容積計算が概算レベルであり、モデルが学習すべき正解が不安定
- range_nmのjitter（±5%）がさらに不確実性を加える

改善には:
- 教師データのrange_nmをcanonical固定（jitterなし）
- またはclassic rule engineのrange計算を精緻化

---

## 8. カテゴリ別評価

### ship_type別（iter500, 200件）

主要艦種で重大な崩壊なし。詳細は評価JSON参照。

### era別（iter500, 200件）

主要eraで重大な崩壊なし。詳細は評価JSON参照。

---

## 9. constraint評価

### 200件比較

| metric | baseline | iter250 | iter500 |
|---|---:|---:|---:|
| constraint follow | 100.0% | 100.0% | 100.0% |
| multi constraint | 100.0% | 100.0% | 100.0% |

200件サンプルでは全制約を満たしている。1000件フル評価では78.3%に低下するが、これはrare ship_typeや境界ケースに起因。

---

## 10. 多様性評価

本検証では実施せず。理由:
- 回帰ゲートFAILが確定したため、多様性評価の優先度が下がった
- 正式採用時に別途実施する

---

## 11. 総合判定

### 判定: C. Stage 2を維持

```text
正式default:
  Stage 2 iter1000 (維持)

Stage 3 v1 iter500:
  experimental (保存)

Stage 3 v1 iter250:
  experimental (保存)
```

### 判定理由

1. **回帰ゲートFAIL**: 両候補ともstatus/impossible F1/semantic reason F1で回帰ゲートを超過
   - 主因: Stage 3学習データのimpossible比率が低く（10.7%）、impossible検出能力が低下
2. **数値品質は改善**: tolerant adoption 85.0%→90.7%、major patch 15.0%→9.7%
   - ただしrange_nmとpower_shpは依然として弱点
3. **schema安定性は改善**: schema complete 87.5%→99.0%（Stage 2 eval）
4. **一般化は部分維持**: 622 semanticではstatus維持、reason一部改善

### 回帰の根本原因

Stage 3学習データ（40,000件）の構成:
- ok: 35,702 (89.3%)
- impossible: 4,298 (10.7%)

Stage 2学習データ（20,000件）の構成:
- ok: ~46%
- impossible: ~54%

Stage 3のimpossible比率が低すぎたため、モデルのimpossible検出能力が低下した。これは数値品質改善（ok caseの数値精度向上）と引き換えに、impossible判定能力を犠牲にした結果。

---

## 12. SHA-256

保存先: `artifacts/navspec-lora/stage3_candidate_hashes.sha256`

```
Stage 2 baseline:    1b9f5eb892e70a321ff04236af5e753bf19d035d639a0039c0bece75752f4098
Stage 3 v1 iter250:  d6da4c481e6360ee51b376e891b6979fa38219bc6eebe105672f9661cd9a0f34
Stage 3 v1 iter500:  303fadf04c9e10d0f0046dd23fc8127c43c629e3f164be71f857b745d6d08d71
Contract:            17f6c0ca7e64f4a5b0baeb4db615bf51ea1cedc9a25363fd76f98b9a9cb85f9e
Dataset manifest:    658e4095f3909f116faaa4dd669c238a48ae41ac38336d6161b88f2d25266d7e
Eval manifest:       f0393445da326dbfb95c86674a07db7c73a915a84559d0a3ab5f8ec5e8168f8e
```

---

## 13. 比較サマリーテーブル

| metric | Stage 2 | iter250 | iter500 |
| --- | ------: | ------: | ------: |
| **Stage 2 eval (480)** | | | |
| schema complete | 87.5% | 99.8% | 99.0% |
| status exact | 99.2% | 95.4% | 93.5% |
| impossible F1 | 99.5% | 97.0% | 95.8% |
| semantic reason F1 | 94.9% | 84.6% | 87.0% |
| truncated JSON | 0.0% | 0.0% | 0.2% |
| **622 semantic** | | | |
| status exact | 90.2% | — | 89.9% |
| impossible F1 | 82.3% | — | 79.7% |
| spec field adoption | 56.2% | — | 55.2% |
| **Stage 3 numeric (200)** | | | |
| exact adoption | 74.2% | 75.4% | 76.9% |
| tolerant adoption | 85.0% | 89.5% | 90.7% |
| patch dependency | 15.0% | 10.5% | 9.3% |
| major patch | 15.0% | 10.5% | 9.3% |
| constraint follow | 100.0% | 100.0% | 100.0% |
| **Stage 3 numeric (1000)** | | | |
| tolerant adoption | — | — | 90.3% |
| major patch | — | — | 9.7% |

---

## 14. 次フェーズ提案

### Stage 3.1: impossible比率修正

目標: 回帰ゲート通過しつつ数値品質改善を維持

方法:
- 学習データのimpossible比率を40-50%へ引き上げ
- Stage 2 replay比率を20%から30%へ増加
- range_nm / power_shp のjitterを削除または大幅縮小
- lr 5e-6、500 iter
- resume from stage2-status-r8 iter1000（Stage 3 v1からではない）

### 採用判定条件

- Stage 2回帰ゲート: 全項目PASS
- tolerant adoption ≥ 85%
- major patch ≤ 10%
- range_nm adoption ≥ 75%
- power_shp adoption ≥ 75%

---

## 15. 保存方針

### Git管理

- 評価ツール（navspec_patch_analyzer.py, navspec_numeric_eval.py, navspec_lora_data_stage3.py）
- contract JSON
- dataset manifest
- adapter metadata + SHA-256
- 本レポート

### Git LFS

- stage3-numeric-quality-r8-v1/adapters.safetensors (iter500)
- stage3-numeric-quality-r8-v1/0000250_adapters.safetensors (iter250)

### Git管理外

- 訓練データJSONL本体
- 評価ケースJSONL本体
- 評価結果JSON本体

---

## 16. Git操作

commit/tag/pushは未実施（明示指示待ち）。

### commit候補

```bash
git add tools/navspec_patch_analyzer.py \
       tools/navspec_numeric_eval.py \
       tools/navspec_lora_data_stage3.py \
       data/navspec/lora/contracts/ \
       artifacts/navspec-lora/stage3_candidate_hashes.sha256 \
       docs/navspec_stage3_numeric_quality_2026-07-12.md \
       docs/navspec_stage3_candidate_validation_2026-07-13.md

git commit -m "feat(navspec-lora): add Stage 3 numeric quality evaluation infrastructure and candidate validation"
```

### tag候補

```bash
git tag -a navspec-lora-stage3-r8-v1-exp \
  -m "Stage 3 numeric quality: adoption 90.3%, major patch 9.7%, but status/impossible regression — experimental"
```

---

## 17. 運用構成（現状維持）

```text
default:
  Stage 2 iter1000

fallback:
  navspec-classic-v1

final validator:
  classic rule engine

Stage 3 v1:
  experimental (数値品質改善の研究成果)
```
