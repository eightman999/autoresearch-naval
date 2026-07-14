# navspec Stage 3.1 (Balanced Correlated Numeric) 評価レポート

作成日: 2026-07-13
対象: `Qwen/Qwen2.5-0.5B-Instruct` + navspec LoRA (r=8)
baseline: Stage 2 iter1000 (`stage2-status-r8`)
resume source: `models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/adapters.safetensors`
判定: **experimental_rejected** (semantic_reason_micro_f1 回帰ゲート FAIL)

---

## 1. 判定サマリー

| checkpoint | schema | status | impossible F1 | semantic reason F1 | 総合 |
|---|:--:|:--:|:--:|:--:|---|
| iter125 | PASS | **HARDSTOP** | **HARDSTOP** | **HARDSTOP** | 学習不足 |
| iter250 | PASS | PASS | PASS | **FAIL** | 回帰ゲート FAIL |
| iter375 | PASS | PASS | PASS | **FAIL** | 回帰ゲート FAIL |
| iter500 | PASS | PASS | PASS | **FAIL** | 回帰ゲート FAIL |
| Stage 2 iter1000 | — | — | — | — | **default 維持** |

- 全 checkpoint が `semantic_reason_micro_f1` で回帰ゲート (-5.0pt) に届かず (86.4-86.7%, gate 89.9%)
- `schema_complete` / `status_exact` / `impossible_f1` / `truncated` / `repetition` は iter250 以降すべて PASS
- 正式候補なし → Stage 3.1 は `experimental_rejected`

---

## 2. 目標と結果

### Stage 3.1 の目標（仕様書より）

- Stage 2 の schema/status/impossible 能力を維持する → **達成**（iter250 以降）
- Stage 3 v1 の数値品質改善を再現する → 未評価（回帰ゲート FAIL のため第16節に従い数値評価を実施せず）
- `power_shp` と `range_nm` の独立 jitter を廃止する → **達成**（generator 実装）
- 相関付き canonical 数値生成を導入する → **達成**（generator 実装）

### Stage 3 v1 との根本原因比較

| 項目 | Stage 3 v1 | Stage 3.1 |
|---|---|---|
| impossible 比率 | 10.7% (不足) | **45.0%** (適正) |
| status 回帰 | -5.6pt (FAIL) | **-0.7pt** (PASS) |
| impossible F1 回帰 | -3.7pt (FAIL) | **-0.4pt** (PASS) |
| semantic F1 回帰 | -7.9pt (FAIL) | -8.5pt (FAIL) |
| schema | +11.5pt | +12.5pt |

Stage 3 v1 の根本原因（impossible 比率不足による status/impossible 能力低下）は解消した。
ただし reason code 精度の低下は別問題として残存。

---

## 3. 変更ファイル（新規のみ、保護対象は不変）

### 新規

- `tools/navspec_lora_data_stage3_1.py` — Stage 3.1 dataset generator（correlated mutation + 6-profile range + Stage 2 direct replay + stratified ordering）
- `data/navspec/lora/stage3_1_balanced_numeric_v0/` — 訓練データ 40,000 件 (train/valid/test.jsonl + manifest.json)
- `models/navspec-lora-0.5b-v0.1/adapter/stage3.1-balanced-numeric-r8/` — adapter (iter125/250/375/500 + final)
- `artifacts/navspec-lora/stage3_1_hashes.sha256` — SHA-256 一覧
- 本レポート

### 保護対象不変確認

以下はすべて未変更（git status / 目視確認済み）:

```
artifacts/navspec-classic-v1/
models/navspec-lora-0.5b-v0.1/adapter/stage1-r8/
models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/
models/navspec-lora-0.5b-v0.1/adapter/stage2.1-status-r8/
models/navspec-lora-0.5b-v0.1/adapter/stage2.2-balanced-reason-r8/
models/navspec-lora-0.5b-v0.1/adapter/stage2.3-era-balanced-r8/
models/navspec-lora-0.5b-v0.1/adapter/stage3-numeric-quality-r8-v1/
schemas/navspec_lora_candidate.schema.json
schemas/navspec_lora_candidate_stage2.schema.json
data/navspec/lora/contracts/navspec_stage3_numeric_eval_contract_v1.json
data/navspec/lora/stage3_numeric_quality_v1/
data/navspec/lora/stage3_numeric_eval_v0/
既存 Stage 1〜3 の dataset / eval / report / hash
models/registry.json の default 設定
```

Stage 2 baseline hash: `1b9f5eb892e70a321ff04236af5e753bf19d035d639a0039c0bece75752f4098` (registry.json と一致)

---

## 4. データセット

### 件数・構成

| 項目 | 値 |
|---|---|
| 総件数 | 40,000 (train 28,851 / validation 6,759 / test 4,390) |
| status | impossible 18,000 (45.0%) / ok 22,000 (55.0%) |
| impossible 許容帯 | 40-50% → **45.0% で達成** |

### カテゴリ構成

| カテゴリ | 件数 |
|---|---:|
| normal_numeric (生成ok) | 5,868 |
| ship_type_contrast | 3,546 |
| replay_impossible_impossible_combined | 3,000 |
| era_contrast | 2,213 |
| single_constraint | 2,187 |
| role_contrast | 1,428 |
| multi_constraint | 1,423 |
| nation_contrast | 1,409 |
| replay_ok_normal_ok | 1,402 |
| boundary | 1,394 |
| supplement_impossible_* | 5,747 |
| replay_impossible_* (Stage2 direct) | 10,784 |
| その他 | — |

### Stage 2 direct replay（impossible 45% の固定床）

- Stage 2 impossible replay: 10,784 件（`stage2_status_v0/` の全 impossible 行を直接読み込み）
- Stage 2 ok replay: 4,000 件（同 ok 行をサンプリング）
- supplement impossible: 5,747 件（ok slot を置換して 45% に到達・件数維持）
- reason code 10 種すべて網羅（Stage 2 replay が保証）

### stratified ordering（第7節）

```
strategy: train.jsonl interleaves impossible/ok (bresenham merge against target ratio)
longest_ok_streak_train: 2
longest_impossible_streak_train: 1
max_allowed: 100
shuffle_seed_validation: 20260713
shuffle_seed_test: 20260714
```

第7節「100件以上連続禁止」を大きくクリア。

### 数値生成ポリシー（第8-11節）

```
independent_jitter_removed: [power_shp, range_nm, speed_kt, length_m, beam_m, draft_m]

correlated_mutation:
  primary_variable: standard_displacement_t (±5% scaled by mutation_strength)
  dependent_recomputed:
    - full_load_displacement_t (ratio preserved)
    - length_m / beam_m / draft_m (estimate_dimensions)
    - lb_ratio (clamped to ship_type band)
    - power_shp (estimate_power_shp from mutated displacement + canonical speed)
    - range_nm (base_range_nm + profile clamp)

power_shp: canonical estimate_power_shp only; no random error
range_nm: canonical base_range_nm clamped into 6 profile bands; no independent jitter
range_profiles: coastal/short_range/fleet_standard/long_range/oceanic/commerce_raider
```

### teacher validation（第13節・全件）

| 検証 | 結果 |
|---|---|
| validate_stage2 (Stage 2 schema) | **40,000 / 40,000 PASS** |
| power-speed consistency (Admiralty 300, ±2%) | **22,000 / 22,000 PASS** |
| impossible spec empty | 18,000 / 18,000 |
| ok 全 SPEC_REQUIRED 非 null | 22,000 / 22,000 |
| range profile band（生成ok 18,824件, constraint 優先以外） | 18,758 PASS / 66 viol（全て constraint `range_nm_min=4000` 優先の仕様通り） |
| classic check_contradiction 整合 | disagreement 108 件（Stage 2 replay 由来・Stage 2 manifest と同一の許容値） |

### 分布監査

```
status: impossible 18,000 (45.0%) / ok 22,000 (55.0%)
reason codes: 10 種すべて網羅
era: pre_1860 4,834 / 1860_1889 1,800 / 1890_1905 2,544 / 1906_1918 5,354 / 1919_1936 7,154 / 1937_1945 17,244 / post_1945 1,070
nation tier: major 23,636 / regional 5,415 / minor 7,403 / limited 3,546
ship types: 34 種（destroyer 5,749 / large_destroyer 3,121 / light_cruiser 2,587 / heavy_cruiser 2,549 / ...）
power_shp: min 380 / median 19,814 / p90 58,454 / max 127,252
range_nm: min 500 / median 4,420 / p90 8,000 / max 10,000
```

---

## 5. 学習設定・結果

### 学習コマンド

```bash
uv run python tools/qwen_lora_train.py \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --train \
  --data data/navspec/lora/stage3_1_balanced_numeric_v0 \
  --resume-adapter-file models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/adapters.safetensors \
  --adapter-path models/navspec-lora-0.5b-v0.1/adapter/stage3.1-balanced-numeric-r8 \
  --iters 500 --batch-size 1 --grad-accumulation-steps 4 \
  --num-layers 16 --learning-rate 0.000005 --mask-prompt --save-every 125
```

### 学習設定

```
base model:     Qwen/Qwen2.5-0.5B-Instruct
resume from:    stage2-status-r8/adapters.safetensors
adapter output: stage3.1-balanced-numeric-r8/
LoRA rank:      8
num layers:     16
learning rate:  5.0e-6 (Stage 3 v1 の 1.0e-5 より低く安定化)
batch size:     1
grad accumulation: 4 (実効 batch 4)
mask prompt:    true
save every:     125
trainable:      0.594% (2.933M / 494.033M)
peak memory:    3.016 GB
```

### Loss 推移

| iter | train loss | val loss |
|---:|---:|---:|
| 1 | — | 0.547 |
| 10 | 0.601 | — |
| 100 | — | — |
| 400 | 0.164 | **0.089** |
| 500 | 0.128 | 0.155 |

学習時間: 約 2 分（Mac MLX）。val loss は iter400 で最小、iter500 でやや反発。

### Checkpoint 一覧

```
0000125_adapters.safetensors
0000250_adapters.safetensors
0000375_adapters.safetensors
0000500_adapters.safetensors
adapters.safetensors (= iter500, final)
adapter_config.json
```

---

## 6. Stage 2 回帰評価（480 件・第17節ゲート）

評価条件: `json_prefill=none`, `temperature=0`, `max_tokens=512`, Stage 2 schema

| metric | iter125 | iter250 | iter375 | iter500 | base | gate | 備考 |
|---|---:|---:|---:|---:|---:|---|---|
| raw_json_rate | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | — | 全 OK |
| schema_complete | 87.08% | **100.0%** | 99.79% | **100.0%** | 87.5% | -2.0pt | PASS (iter250+) |
| status_exact | 88.96% | 98.75% | 98.54% | 98.54% | 99.2% | -1.5pt | **PASS (iter250+)** |
| impossible precision | 99.07% | 98.92% | 98.92% | 98.92% | 100.0% | — | — |
| impossible recall | 86.76% | 99.19% | 99.19% | 99.19% | 98.9% | — | — |
| impossible F1 | 92.51% | 99.19% | 99.19% | 99.06% | 99.5% | -1.5pt | **PASS (iter250+)** |
| **semantic reason micro F1** | 74.93% | 86.68% | 86.61% | 86.38% | 94.9% | **-5.0pt** | **全 FAIL** |
| strict_reason_exact | 63.12% | — | — | 69.58% | 78.1% | — | — |
| primary_reason_accuracy | 61.89% | 78.11% | 78.11% | 78.65% | 94.1% | — | — |
| missing_reason_rate | 42.16% | 25.41% | 25.41% | 24.59% | 11.9% | — | 高い |
| extra_reason_rate | 8.38% | 15.41% | 15.68% | 17.03% | 4.1% | — | 高い |
| truncated_json_rate | 0% | 0% | 0.21% | 0% | 0% | <1% | PASS |
| repetition_collapse_rate | 0% | 0% | 0% | 0% | 0% | <1% | PASS |
| invalid_reason_rate | 0% | 0% | 0% | 0% | 0% | <5% | PASS |

### 即時停止条件の確認

| 条件 | iter125 | iter250 | iter375 | iter500 |
|---|:--:|:--:|:--:|:--:|
| status > -2.0pt | HARDSTOP | PASS | PASS | PASS |
| impossible F1 > -2.0pt | HARDSTOP | PASS | PASS | PASS |
| semantic F1 > -6.0pt | HARDSTOP | HARDSTOP | HARDSTOP | HARDSTOP |
| schema > -3.0pt | PASS | PASS | PASS | PASS |

semantic_reason_micro_f1 のみ全 checkpoint で即時停止条件（-6.0pt）超過。

---

## 7. 後段評価（第18-22節）

第16節「回帰ゲート FAIL の checkpoint は正式候補から除外。数値評価より先に Stage 2 回帰評価を行い、PASS した checkpoint のみ Stage 3 numeric eval 1000 へ」に従い、全 checkpoint が semantic_reason_micro_f1 で回帰ゲート FAIL のため以下は未実施:

- Stage 3 numeric eval 1000（第18節・第19節）
- 622 semantic eval（第20節）
- カテゴリ別評価（第21節）
- diversity 評価（第22節）

---

## 8. 推奨 checkpoint

公式採用候補なし。ただし比較のため、schema/status/impossible F1 が最も安定している **iter250** を代表 checkpoint として記録する:

```
iter250: schema 100.0%, status 98.75%, impossible F1 99.19%, semantic F1 86.68%
```

---

## 9. 正式採用可否

**不採用（experimental_rejected）**

理由: 全 checkpoint が Stage 2 回帰ゲートの `semantic_reason_micro_f1` (-5.0pt) に届かない（86.4-86.7% vs gate 89.9%）。

### fallback

```
default:    Stage 2 iter1000 (stage2-status-r8) 維持
fallback:   navspec-classic-v1
validator:  classic rule engine
Stage 3.1:  experimental_rejected (reason code 精度改善の研究成果)
Stage 3 v1: experimental_rejected (維持)
```

### semantic_reason_micro_f1 回帰の分析

モデルは impossible 判定自体は維持（impossible F1 99.06%, precision 98.92%, recall 99.19%）。reason code の「どれを出すか」の精度が低下:

- missing_reason_rate 24.6%: impossible と判定したが reason を出さない（base 11.9%）
- extra_reason_rate 17.0%: 余分な reason を出す（base 4.1%）

これは reason code の特定精度問題。impossible 判定の閾値は維持できているが、複数の違反要因から正しい reason code を選ぶ精度が下がった。

### 次アプローチの候補（第26節「役割分担固定」を参照）

```
LoRA:
  入力理解 / status 候補 / design concept / constraint 構造化
  (Stage 3.1 はこれらを維持)

classic:
  数値生成 / reason code / 最終保証
  (reason code 精度を classic 側で担保)
```

または、学習データの reason code 構成を見直し（reason code の multi-label 精度を高める補助タスク導入等）を検討。

---

## 10. 保存対象

### Git 管理

- 生成スクリプト: `tools/navspec_lora_data_stage3_1.py`
- dataset manifest: `data/navspec/lora/stage3_1_balanced_numeric_v0/manifest.json`
- adapter metadata: `models/navspec-lora-0.5b-v0.1/adapter/stage3.1-balanced-numeric-r8/adapter_config.json`
- SHA-256 一覧: `artifacts/navspec-lora/stage3_1_hashes.sha256`
- 本レポート: `docs/navspec_stage3.1_balanced_numeric_2026-07-13.md`

### Git LFS

- `stage3.1-balanced-numeric-r8/adapters.safetensors` (iter500/final)
- `stage3.1-balanced-numeric-r8/0000250_adapters.safetensors` (代表 checkpoint)
- `stage3.1-balanced-numeric-r8/0000125_adapters.safetensors`
- `stage3.1-balanced-numeric-r8/0000375_adapters.safetensors`
- `stage3.1-balanced-numeric-r8/0000500_adapters.safetensors`

### Git 管理外（再生成可能）

- 訓練データ JSONL 本体 (40,000 件)
- 評価ケース JSONL 本体
- 評価結果 JSON 本体
- ベースモデルキャッシュ
- `/tmp/stage31_iter*` 一時 adapter ディレクトリ

Git 管理外データには manifest（件数・構成比・seed・生成コマンド・SHA-256・保存場所）を `data/navspec/lora/stage3_1_balanced_numeric_v0/manifest.json` に集約済み。

---

## 11. SHA-256

保存先: `artifacts/navspec-lora/stage3_1_hashes.sha256`

```
Stage 2 baseline adapter:        1b9f5eb892e70a321ff04236af5e753bf19d035d639a0039c0bece75752f4098
Stage 3.1 iter125:               0709780c617d0a3891aff5aec708f9483e154411f2ab2b3d66ff721f47b401b7
Stage 3.1 iter250:               e96b4835dc66c0af7e5665f1488b54071bf6629cb5a62df04eb4bbca999ade2a
Stage 3.1 iter375:               8586c8019a5f5de9ea4338238ade8299863232ccd39972e39ac2e9725d86806f
Stage 3.1 iter500 (final):       c8e47ff17f787ed76c826d5f68d21623e391adb1528e4155a6fbb39c89fbdd81
Stage 3.1 dataset manifest:      d787f5b10b82c4e6cbc83ac0d4b7ce1046e670cecd79042f861d7f59ef717f9f
Stage 3.1 generator:             0c9f160555083b1de677294396cef2356c1fa9e0a583ebb0086674562e810a5d
```

---

## 12. Git 操作

commit / tag / push は未実施（明示指示待ち）。

### commit 候補

```bash
# commit 1: データ生成 + 学習
git add tools/navspec_lora_data_stage3_1.py \
       data/navspec/lora/stage3_1_balanced_numeric_v0/manifest.json \
       models/navspec-lora-0.5b-v0.1/adapter/stage3.1-balanced-numeric-r8/adapter_config.json \
       docs/navspec_stage3.1_balanced_numeric_2026-07-13.md \
       artifacts/navspec-lora/stage3_1_hashes.sha256
git commit -m "feat(navspec-lora): add Stage 3.1 balanced correlated numeric training (experimental_rejected)"

# commit 2: 評価レポート（本レポートが既に commit 1 に含まれる場合は省略）
```

### tag 候補

```bash
git tag -a navspec-lora-stage3.1-r8-exp-rejected \
  -m "Stage 3.1: impossible 45% balance fixes status/impossible regression, but semantic_reason F1 remains below gate — experimental_rejected"
```

Stage 2 正式 tag は移動・上書きしない。

---

## 13. 運用構成（現状維持）

```text
default:    Stage 2 iter1000 (stage2-status-r8)
fallback:   navspec-classic-v1
validator:  classic rule engine

Stage 3 v1:   experimental_rejected (数値品質改善の研究成果)
Stage 3.1:    experimental_rejected (impossible バランス改善の研究成果)
```

---

## 14. Git 操作未実施確認

- git commit: 未実施
- git tag: 未実施
- git push: 未実施
- registry.json default 変更: 未実施
- checkpoint 削除: 未実施
- dataset 削除: 未実施
- 既存タグ変更: 未実施
