# navspec Stage 3 Numeric Quality 実験レポート

作成日: 2026-07-12  
対象: Qwen2.5-0.5B-Instruct + navspec LoRA  
baseline: stage2-status-r8 (iter1000)  
classic validator: navspec-classic-v1

---

## 1. 概要

Stage 2で確立したschema/status/reason生成能力を維持しつつ、status=ok時の数値候補品質を改善する目的でStage 3 numeric quality学習を実施した。

### 実験バリアント

| version | 主な違い | 結果 |
|---|---|---|
| v0 | speed_ktに±0.5kt jitter、power_shpに±5% random jitter | speed/power壊滅的劣化、不採用 |
| **v1** | speed_kt不変（canonical維持）、power_shp=canonical speedから再計算 | **主要ゲート通過、候補採用** |

### 最終判定

- **v1 iter500**: candidate（主要数値ゲート通過、range_nm/power_shpに残課題）
- **正式default昇格**: Stage 2 baselineとの回帰比較完了後に判断
- **現在の正式モデル**: stage2-status-r8 (iter1000) 維持

---

## 2. 変更ファイル

### 新規

- `tools/navspec_patch_analyzer.py` — raw vs canonical specのフィールドレベルpatch解析
- `tools/navspec_numeric_eval.py` — Stage 3数値品質評価器
- `tools/navspec_lora_data_stage3.py` — Stage 3教師データ＋評価ケース生成
- `data/navspec/lora/contracts/navspec_stage3_numeric_eval_contract_v1.json` — 評価contract
- `data/navspec/lora/stage3_numeric_quality_v0/` — v0訓練データ (40,000件)
- `data/navspec/lora/stage3_numeric_quality_v1/` — v1訓練データ (40,000件)
- `data/navspec/lora/stage3_numeric_eval_v0/` — 評価ケース (1,000件)
- `models/navspec-lora-0.5b-v0.1/adapter/stage3-numeric-quality-r8/` — v0 adapter
- `models/navspec-lora-0.5b-v0.1/adapter/stage3-numeric-quality-r8-v1/` — v1 adapter

### 保護対象確認

以下はすべて不変:

```
artifacts/navspec-classic-v1/
models/navspec-lora-0.5b-v0.1/adapter/stage1-r8/
models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/
models/navspec-lora-0.5b-v0.1/adapter/stage2.1-status-r8/
models/navspec-lora-0.5b-v0.1/adapter/stage2.2-balanced-reason-r8/
models/navspec-lora-0.5b-v0.1/adapter/stage2.3-era-balanced-r8/
schemas/navspec_lora_candidate.schema.json
schemas/navspec_lora_candidate_stage2.schema.json
既存Stage 1/2/2.xデータ・評価contract・レポート
```

---

## 3. 学習データ (v1)

### 構成

| category | ratio | 件数(40k時) |
|---|---:|---:|
| Stage 2 replay | 20% | 8,000 |
| normal numeric | 25% | 10,000 |
| ship_type contrast | 12.5% | 5,000 |
| era contrast | 10% | 4,000 |
| nation tier contrast | 7.5% | 3,000 |
| role contrast | 7.5% | 3,000 |
| single constraint | 7.5% | 3,000 |
| multi constraint | 5% | 2,000 |
| boundary / hard negative | 2.5% | 1,000 |
| diversity | 2.5% | 1,000 |

### ステータス分布

- ok: 35,702 (89.3%)
- impossible: 4,298 (10.7%)
- classic disagreements: 0

### Teacher validation

- validate_stage2: 全件 PASS
- classic check_contradiction との一致: 100%

### Jitter設定 (v1)

| field | jitter | 理由 |
|---|---|---|
| standard_displacement_t | ±5% rel | 設計バリエーションを許容 |
| full_load_displacement_t | ratio追従 | 比例関係維持 |
| speed_kt | **不変 (canonical)** | v0で壊滅的劣化、省略 |
| power_shp | **canonical speedから再計算** | speed³に比例、独立jitter不可 |
| main_gun_caliber_mm | ±2mm abs | 口径バリエーション |
| main_gun_count | ±1 (確率付) | 装備数バリエーション |
| belt_armor_mm | ±5mm/5% | 装甲バリエーション |
| range_nm | ±5% rel | 航続距離バリエーション |
| length/beam/draft | estimate_dimensions再計算 | 排水量から再導出 |

---

## 4. 学習設定

```
base model:     Qwen/Qwen2.5-0.5B-Instruct
resume from:    stage2-status-r8/adapters.safetensors
adapter output: stage3-numeric-quality-r8-v1/
LoRA rank:      8
num layers:     16
learning rate:  1.0e-5
iters:          500
batch size:     1
mask prompt:    true (自動検出)
save every:     250
trainable:      0.594% (2.933M / 494.033M)
peak memory:    2.881 GB
```

### Loss推移

| iter | train loss | val loss |
|---:|---:|---:|
| 1 | — | 1.409 |
| 10 | 0.896 | — |
| 100 | 0.287 | — |
| 200 | 0.332 | 0.294 |
| 250 | 0.301 | — |
| 400 | 0.257 | 0.267 |
| 500 | 0.240 | 0.265 |

過学習なし。val lossは単調減少。

### Checkpoints

- `0000250_adapters.safetensors`
- `0000500_adapters.safetensors`
- `adapters.safetensors` (final = iter500)

---

## 5. 評価結果 (v1 iter500, full 1000 cases)

### 全体指標

| metric | 値 |
|---|---:|
| total | 1,000 |
| raw JSON成功率 | 100.0% |
| schema完全一致率 | 100.0% |
| status一致率 | 93.5% |
| 反復崩壊率 | 0.0% |
| impossible P/R/F1 | 50.0% / 3.1% / 5.8% |

### 数値品質 (ok cases = 933)

| metric | 値 | gate | 判定 |
|---|---:|---|:--:|
| exact field adoption | 75.3% | ≥35% | PASS |
| tolerant field adoption | 90.3% | ≥70% | PASS |
| patch dependency | 9.7% | ≤30% | PASS |
| major patch rate | **9.7%** | **≤10%** | **PASS** |
| constraint follow | 78.3% | ≥90% | FAIL |
| multi constraint follow | 93.3% | ≥80% | PASS |
| median rel patch delta | 0.0277 | ≤5% | PASS |
| p90 rel patch delta | 2.3214 | ≤15% | FAIL |

### フィールド別結果

| field | tolerant adoption | major patch | 判定 |
|---|---:|---:|---|
| range_nm | 66.7% | 33.3% | 要改善 |
| power_shp | 67.4% | 32.6% | 要改善 |
| speed_kt | 85.9% | 14.1% | 改善余地 |
| main_gun_caliber_mm | 91.3% | 8.7% | PASS |
| full_load_displacement_t | 93.0% | 7.0% | PASS |
| standard_displacement_t | 94.2% | 5.8% | PASS |
| draft_m | 96.2% | 3.8% | PASS |
| beam_m | 96.4% | 3.6% | PASS |
| length_m | 96.4% | 3.6% | PASS |
| belt_armor_mm | 98.0% | 2.0% | PASS |
| main_gun_count | 99.1% | 0.9% | PASS |
| torpedo_tubes | 99.5% | 0.5% | PASS |

---

## 6. Stage 2 baselineとの比較 (200 cases fair comparison)

| metric | Stage 2 | Stage 3 v1 | delta |
|---|---:|---:|---:|
| tolerant adoption | 85.0% | 90.7% | **+5.6pt** |
| exact adoption | 74.2% | 76.9% | **+2.7pt** |
| major patch rate | 15.0% | 9.3% | **-5.6pt** |
| patch dependency | 15.0% | 9.3% | **-5.6pt** |
| status exact | 87.0% | 83.0% | -4.0pt |
| schema complete | 100% | 100% | 0.0pt |
| repetition | 0% | 0% | 0.0pt |

### フィールド別改善 (200 cases)

| field | baseline | v1 | delta |
|---|---:|---:|---:|
| speed_kt | 76.1% | 85.5% | +9.4pt |
| main_gun_caliber_mm | 86.2% | 95.8% | +9.6pt |
| standard_displacement_t | 87.4% | 97.0% | +9.5pt |
| full_load_displacement_t | 89.3% | 97.0% | +7.7pt |
| power_shp | 56.0% | 63.6% | +7.7pt |
| length_m | 93.1% | 100.0% | +6.9pt |
| beam_m | 93.1% | 99.4% | +6.3pt |
| torpedo_tubes | 93.1% | 98.8% | +5.7pt |
| draft_m | 97.5% | 99.4% | +1.9pt |
| belt_armor_mm | 97.5% | 100.0% | +2.5pt |
| range_nm | 50.9% | 51.5% | +0.6pt |

---

## 7. v0の失敗と教訓

### v0の問題

speed_ktに±0.5ktのjitterを適用した結果、モデルがspeed範囲を正しく学習できず:

- speed_kt adoption: 76.1% → 3.0% (-73.1pt)
- power_shp adoption: 56.0% → 1.2% (-54.8pt)

### 原因

1. speedのjitter自体は小さい(±0.5kt)だが、学習データ全体でspeed分布が広がり、モデルがship_type/era別のspeed範囲を正確に学習できなかった
2. power_shpはspeed³に比例するため、speedの差が増幅されてpowerの誤差が巨大化

### v1での修正

- speed_kt: canonical値をそのまま使用（jitterなし）
- power_shp: canonical speedから再計算（jittered displacement使用）
- range_nm: jitterを±8%から±5%へ軽減

---

## 8. 残存課題

### range_nm (66.7% adoption, 33.3% major patch)

最大の弱点。航続距離は燃料容量・機関効率・船型に依存する複合値であり、classic rule engineの概算（`base_range_nm`関数）とモデルの推定値に乖離がある。

改善案:
- 教師データのrange_nmをcanonical値に固定（jitterなし）
- またはjitterを±3%以下へ縮小

### power_shp (67.4% adoption, 32.6% major patch)

2番目の弱点。Admiralty係数300による概算と、排水量jitterの影響が重なる。

改善案:
- 排水量jitterを±3%へ縮小
- またはpower_shpもcanonical値に固定

### constraint_follow (78.3%)

range_nmとpower_shpの誤差が制約違反を引き起こしている。上記フィールド改善で連鎖的に改善する見込み。

### status_exact / impossible_f1

Stage 3 numeric eval setは数値品質測定用であり、impossible casesが極めて少ない（~1%）。このeval setでのimpossible F1は参考値扱い。回帰判定にはStage 2 eval setでの評価が必要。

---

## 9. 採用判定

### 現在の判定

```
Stage 3 v1 iter500: candidate
正式default:        stage2-status-r8 維持
```

### 昇格条件

以下を満たせば正式defaultへ昇格:

1. Stage 2 eval set (480件) での回帰確認:
   - schema ≥ Stage 2 -2pt
   - status ≥ Stage 2 -1.5pt
   - impossible F1 ≥ Stage 2 -1.5pt
   - reason metrics ≥ Stage 2 水準

2. range_nm / power_shp の改善または許容確認

### 推奨checkpoint

```
iter500 (v1): candidate
  - tolerant adoption: 90.3%
  - major patch rate: 9.7%
  - schema: 100%
  - repetition: 0%

iter250 (v1): 比較用保存
```

---

## 10. 保存方針

### Git管理

- 生成スクリプト (tools/navspec_*.py)
- contract JSON
- dataset manifest
- adapter metadata
- 本レポート

### Git LFS

- stage3-numeric-quality-r8-v1/adapters.safetensors
- stage3-numeric-quality-r8-v1/0000250_adapters.safetensors
- stage3-numeric-quality-r8-v1/0000500_adapters.safetensors
- stage3-numeric-quality-r8/ (v0, experimental_rejected)

### Git管理外

- 訓練データJSONL本体 (再生成可能)
- 評価ケースJSONL本体 (再生成可能)
- 評価結果JSON本体 (再生成可能)
- ベースモデルキャッシュ

---

## 11. Git操作

commit/tag/pushは未実施（明示指示待ち）。

### commit候補

```bash
# 評価インフラ + データ生成 + 学習 + 評価
git add tools/navspec_patch_analyzer.py \
       tools/navspec_numeric_eval.py \
       tools/navspec_lora_data_stage3.py \
       data/navspec/lora/contracts/ \
       data/navspec/lora/stage3_numeric_quality_v1/manifest.json \
       data/navspec/lora/stage3_numeric_eval_v0/manifest.json \
       models/navspec-lora-0.5b-v0.1/adapter/stage3-numeric-quality-r8-v1/adapter_config.json \
       docs/navspec_stage3_numeric_quality_2026-07-12.md

git commit -m "feat(navspec-lora): add Stage 3 numeric quality evaluation and training"
```

### tag候補

```bash
git tag -a navspec-lora-stage3-r8-v1-candidate \
  -m "Stage 3 numeric quality: tolerant adoption 90.3%, major patch 9.7%"
```

---

## 12. 次フェーズ候補

### Stage 3.1: range_nm / power_shp改善

目標:
- range_nm adoption ≥ 80%
- power_shp adoption ≥ 80%
- constraint follow ≥ 90%

方法:
- range_nm / power_shp のjitterを削除または大幅縮小
- lr 5e-6、500 iter
- resume from stage3-numeric-quality-r8-v1 iter500

### Stage 2 eval回帰確認

Stage 3 v1をStage 2 eval set (480件)で評価し、schema/status/reasonの回帰を確認。

### Stage 4 (将来)

- 自然文設計思想の品質改善
- 複数案生成
- HOI4 stats変換
