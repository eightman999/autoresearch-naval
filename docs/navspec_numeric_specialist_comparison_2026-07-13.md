# navspec Numeric Specialist Comparison Report

作成日: 2026-07-13 (評価実施: 2026-07-13〜14)
対象: navspec Qwen2.5-0.5B LoRA numeric specialist 分離評価
正式default: Stage 2 iter1000 (`stage2-status-r8`)
最終保証: navspec-classic-v1

---

## 1. 目的と結論

### 目的

Stage 3系adapterを all-in-one モデルとして再昇格させるのではなく、`status=ok` 確定後の数値生成のみを担当する numeric specialist として再評価する。責務分離:

```
Stage 2:  status / impossible / reason候補
classic:  正式status / 正式reason code / 最終保証
numeric:  status=ok時のspec数値候補のみ
```

### 結論サマリー

| checkpoint | tolerant | major | 採用ゲート | pipeline最終 |
|---|---:|---:|---|---|
| **Stage 3 v1 iter500** | **90.1%** | **9.6%** | partial FAIL | **best available** |
| Stage 3.1 iter250 | 75.7% | 17.0% | FAIL | 不採用 |
| Stage 2 iter1000 | 74.5% | 15.4% | FAIL (baseline) | status担当 |

- Stage 3 v1 iter500が3候補中最優位（tolerant +15.6pt, major -5.8pt vs Stage 2）
- 厳格numeric specialistゲートは `power_shp` / `range_nm` / `constraint_follow` / `speed_power_consistency` でFAIL
- ただし二段pipeline (specialist + classic patch) のend-to-end評価は **全ゲートPASS**（別紙 pipeline validation report 参照）
- Phase N1 (numeric_candidate.v1直接出力) は **UNSTABLE** → 運用は Phase N0 (Stage 2 schema出力 → 抽出正規化)

---

## 2. 保護対象不変確認

本作業で作成・変更したファイルは新規のみ。以下はすべて未変更（git status / SHA-256 確認済み）:

```
artifacts/navspec-classic-v1/                         (不変)
models/.../stage1-r8/                                 ce982e6f... (不変)
models/.../stage2-status-r8/                          1b9f5eb8... (不変・registry default)
models/.../stage2.1-status-r8/                        f73645c0... (不変)
models/.../stage2.2-balanced-reason-r8/               fbc0c838... (不変)
models/.../stage2.3-era-balanced-r8/                  9ede54bc... (不変)
models/.../stage3-numeric-quality-r8-v1/              303fadf0... (不変)
models/.../stage3.1-balanced-numeric-r8/              c8e47ff1... (不変)
schemas/navspec_lora_candidate.schema.json            9819e369... (不変)
schemas/navspec_lora_candidate_stage2.schema.json     47cac8e7... (不変)
data/.../navspec_stage3_numeric_eval_contract_v1.json 17f6c0ca... (不変)
data/.../stage3_numeric_eval_v0/                      f0393445... (不変)
models/registry.json default設定                       stage2=true (不変)
```

Stage 2 baseline hash: `1b9f5eb892e70a321ff04236af5e753bf19d035d639a0039c0bece75752f4098` (registry.json と一致)

---

## 3. 変更ファイル（新規のみ）

### 新規schema

- `schemas/navspec_numeric_candidate.schema.json` — numeric specialist 出力 schema (`navspec.numeric_candidate.v1`)

### 新規contract

- `data/navspec/lora/contracts/navspec_numeric_specialist_eval_contract_v1.json` — numeric specialist 評価contract

### 新規subset

- `data/navspec/lora/stage3_numeric_eval_ok_v1/eval.jsonl` — ok のみ935件
- `data/navspec/lora/stage3_numeric_eval_ok_v1/impossible_holdout.jsonl` — impossible 65件 (pipeline誤呼び出し検証用)
- `data/navspec/lora/stage3_numeric_eval_ok_v1/manifest.json` — 分布・hash記録

### 新規tools

- `tools/navspec_build_ok_subset.py` — ok subset抽出
- `tools/navspec_numeric_specialist_eval.py` — numeric specialist評価器
- `tools/navspec_phase_n1_probe.py` — Phase N1安定性プローブ
- `tools/navspec_two_stage_pipeline.py` — 二段pipeline
- `tools/navspec_pipeline_reason_reeval.py` — pipeline reason再評価

### 新規eval結果

- `data/navspec/lora/eval/numeric_specialist_n0_stage2-status-r8_iter1000/`
- `data/navspec/lora/eval/numeric_specialist_n0_stage3-numeric-quality-r8-v1_iter500/`
- `data/navspec/lora/eval/numeric_specialist_n0_ns_stage31_iter250_iter250/`
- `data/navspec/lora/eval/numeric_specialist_n0_stage3.1-balanced-numeric-r8_iter500_final/`
- `data/navspec/lora/eval/numeric_specialist_n1_stability_probe/`
- `data/navspec/lora/eval/two_stage_pipeline_2026-07-13/`

---

## 4. ok subset構成

```
source: stage3_numeric_eval_v0 (1000件)
extraction: expected_status == ok (== classic final status ok)
ok cases: 935
impossible holdout: 65 (pipeline誤呼出検証用)
```

| 分布 | 件数 |
|---|---:|
| ship_type種 | 30 |
| era種 | 7 (pre_1860:197, 1937_1945:330, 1906_1918:190, ...) |
| nation tier | major:691, regional:109, minor:100, limited:35 |
| constraint | なし:695, 単一:150, 複数:90 |
| category | normal_numeric:253, era_contrast:148, role_contrast:98, single_constraint:100, ... |

eval.jsonl SHA-256: `c92ed494d3606801be80617ddff58720c456c1d1a0cdc16f6e8c65b6e7530386`

---

## 5. 推論条件（Section 10）

全candidate同一条件:

```
json_prefill:   none
temperature:    0
max_tokens:     512
seed:           fixed (MLX default)
prompt:         same (existing free_raw, Stage 2 system prompt)
schema:         navspec.lora_candidate.v1 (Phase N0)
batch:          sequential
```

---

## 6. Phase N0 主指標比較（Section 11）

ok subset (935件) での評価。status/reason は無視し、spec数値品質のみ集計。

| metric | Stage 2 iter1000 | **Stage 3 v1 iter500** | Stage 3.1 iter250 | Stage 3.1 iter500 | gate |
|---|---:|---:|---:|---:|---|
| raw_json_rate | 100.0% | 100.0% | 100.0% | 100.0% | >=99% |
| spec_required_field | 89.9% | **99.8%** | 92.7% | 88.8% | >=98% |
| exact_adoption | 66.6% | **75.2%** | 66.0% | 65.5% | - |
| **tolerant_adoption** | 74.5% | **90.1%** | 75.7% | 76.4% | >=88% |
| minor_patch | 9.0% | 0.5% | 1.5% | 12.3% | - |
| **major_patch** | 15.4% | **9.6%** | 17.0% | 12.4% | <=10% |
| patch_dependency | 25.5% | **9.9%** | 24.3% | 23.6% | <=30% |
| median_rel_delta | 0.0414 | **0.0276** | 0.0448 | 0.0307 | <=5% |
| p90_rel_delta | 1.2082 | 2.3164 | 1.4242 | 1.4143 | <=15% |
| constraint_follow | 68.9% | 78.3% | **81.5%** | 70.2% | >=90% |
| multi_constraint | 73.9% | **93.3%** | 85.0% | 69.4% | >=80% |
| boundary_constraint | - | 50.0% | - | - | >=80% |
| cross_field_consistency | 24.7% | **44.7%** | 34.4% | 35.8% | >=95% |
| speed_power_consistency | 24.7% | **44.7%** | 34.4% | 35.8% | >=95% |
| repetition_collapse | 0.0% | 0.0% | 0.0% | 0.0% | <1% |
| **all_gates** | FAIL | **FAIL** | FAIL | FAIL | |

### 備考

- Stage 3 v1 iter500 が tolerant / major / median_delta / multi_constraint / cross_field で最優位
- 全候補で p90_rel_delta / constraint_follow / cross_field_consistency / speed_power_consistency がFAIL
- 根本原因: `power_shp` と `range_nm` の相関計算（Admiralty式・航続プロファイル）をモデルが学習できていない
- 0.5B r=8 LoRA では物理相関の暗黙表現が限界 → classic numeric validator での補正が必要

---

## 7. フィールド別評価（Section 13）

### tolerant_adoption / major_patch

| field | Stage 2 | **Stage 3 v1 iter500** | Stage 3.1 iter250 | field gate |
|---|---|---|---|---|
| standard_displacement_t | 77.0%/12.9% | **94.0%/5.8%** | 73.9%/18.8% | >=60% / <=15% |
| full_load_displacement_t | 79.1%/10.8% | **92.8%/7.0%** | 76.8%/15.9% | PASS |
| length_m | 80.8%/9.2% | **96.2%/3.6%** | 81.0%/11.8% | PASS |
| beam_m | 81.3%/8.7% | **96.2%/3.6%** | 85.2%/7.5% | PASS |
| draft_m | 82.9%/7.1% | **96.0%/3.7%** | 83.7%/9.0% | PASS |
| **power_shp** | 46.2%/43.7% | **67.3%/32.5%** | 47.7%/45.0% | FAIL |
| speed_kt | 64.2%/25.8% | **85.7%/14.1%** | 65.0%/27.7% | PASS/FAIL |
| **range_nm** | 49.8%/40.1% | **66.5%/33.3%** | 44.5%/48.2% | FAIL |
| main_gun_caliber_mm | 75.5%/14.4% | **91.1%/8.7%** | 78.3%/14.4% | PASS |
| main_gun_count | 89.0%/1.0% | **98.9%/0.9%** | 90.3%/2.5% | PASS |
| belt_armor_mm | 84.5%/5.5% | **97.8%/2.0%** | 89.8%/2.9% | PASS |
| torpedo_tube_count | 84.0%/6.0% | **99.2%/0.5%** | 92.2%/0.5% | PASS |

- Stage 3 v1 iter500 は `power_shp` と `range_nm` 以外の全フィールドで field gate PASS
- `power_shp` (67.3%) と `range_nm` (66.5%) は gate (>=75%) に届かず
- これらは classic numeric validator が major patch で補正する設計領域

---

## 8. Power / Speed 評価（Section 14）

Stage 3 v1 iter500 の失敗分類:

| class | count |
|---|---:|
| ok (一貫) | 420 (44.9%) |
| speed_ok_power_high | 224 (24.0%) |
| speed_ok_power_low | 188 (20.1%) |
| mismatch_other | 82 (8.8%) |
| speed_ok_power_high | 224 |
| both_high | 10 |
| speed_high_power_ok | 7 |
| both_low | 4 |

- speed_power_consistency: 44.7% (gate >=95% FAIL)
- 主因: モデルが Admiralty 300式による power推定を精度良く再生できない
- pipeline では classic が `estimate_power_shp(displacement, speed)` で最終値を確定するため、end-to-endでは影響なし

---

## 9. Range 評価（Section 15）

| metric | Stage 3 v1 iter500 |
|---|---:|
| range_nm tolerant | 66.5% (gate >=75% FAIL) |
| range_nm major patch | 33.3% (gate <=12% FAIL) |

- モデルは航続プロファイル（coastal/fleet_standard/oceanic等）の暗黙選択を学習できていない
- classic は `base_range_nm(ship_type, role, year, tier)` でプロファイルに基づく正確な値を生成する
- pipeline では classic が最終値を確定

---

## 10. Constraint 評価（Section 16）

| category | Stage 3 v1 iter500 follow rate |
|---|---:|
| single | 69.3% |
| multi (2個) | 93.3% (gate >=80% PASS) |
| boundary | 50.0% (gate >=80% FAIL) |
| overall | 78.3% (gate >=90% FAIL) |

- 単一制約のfollow率が低い（69.3%）。モデルが min/max 制約を仕様に反映しきれない
- 複数制約は逆に高い（93.3%）。制約が多いほど学習データのパターンに近くなるため推測
- pipeline では classic numeric validator が制約違反を最終patchで修正

---

## 11. カテゴリ別評価（Section 17）

### ship type別 tolerant_adoption (Stage 3 v1 iter500)

主要艦種（case数 >= 30）は全て tolerant >= 85%:

| ship_type | count | tolerant | major |
|---|---:|---:|---:|
| destroyer | 146 | 94.9% | 5.1% |
| battleship | 45 | 95.4% | 4.6% |
| heavy_cruiser | 45 | 88.0% | 12.0% |
| light_cruiser | 49 | 87.2% | 12.8% |
| battlecruiser | 43 | 95.7% | 4.3% |
| dreadnought_battleship | 45 | 95.7% | 4.3% |

少数艦種（case数 < 10）は安定性に課題:

| ship_type | count | tolerant | major |
|---|---:|---:|---:|
| oiler | 4 | 20.8% | 79.2% |
| training_ship | 4 | 25.0% | 75.0% |
| ship_of_the_line | 4 | 29.2% | 70.8% |
| minesweeper | 4 | 31.2% | 68.8% |

### era別

| era | count | tolerant | major |
|---|---:|---:|---:|
| pre_1860 | 197 | 86.4% | 13.6% |
| 1860_1889 | 47 | 93.5% | 6.5% |
| 1890_1905 | 51 | 93.5% | 6.5% |
| 1906_1918 | 190 | 86.7% | 13.3% |
| 1919_1936 | 87 | 95.6% | 4.4% |
| 1937_1945 | 330 | 92.0% | 8.0% |
| post_1945 | 33 | 95.5% | 4.5% |

---

## 12. Diversity 評価（Section 18）

Stage 3 v1 iter500、50入力 × 5 temperature (0.3/0.5/0.7/0.9/1.0):

| metric | result | gate |
|---|---:|---|
| unique_spec_rate | 0.0% | >=80% FAIL |
| near_duplicate_rate | 0.0% | <=20% PASS |
| template_collision_rate | 100.0% | <=15% FAIL |

- 0.5B r=8 LoRA は temperature 0.3-1.0 でも同一入力に対し同一数値を出力する（決定論的）
- 学習データが classic teacher の deterministic 出力であるため、モデルが温度に対して不感になっている
- pipeline運用では temperature=0 を使用するため、多様性の欠如は実害なし
- 多様性が必要な場合は Stage 3.2 で temperature-augmented training データを検討

---

## 13. Phase N1 安定性評価（Section 7）

Stage 3 v1 iter500 へ numeric-only prompt (status/reason/suggested_revision を含まない) を与え、`navspec.numeric_candidate.v1` 直接生成を試行（30件）:

| metric | result |
|---|---:|
| valid JSON object | 100.0% |
| schema_version correct | 70.0% |
| spec present | 70.0% |
| no status/reason leak | **30.0%** |
| design_concept present | 70.0% |
| required fields present | **0.0%** |

**判定: UNSTABLE**

- モデルは schema_version 文字列を変更できるが、field名を短縮形 ("beam"→beam_m, "belt"→belt_armor_mm) に変更してしまう
- status/reason/suggested_revision の漏出が70%。学習した Stage 2 schema構造が強く優先される
- Section 7に従い、numeric_candidate.v1 直接出力は必須条件としない
- 運用: Stage 2 schema出力 → spec/design_concept/confidence 抽出 → numeric_candidate.v1 へ正規化

---

## 14. numeric specialist 選定（Section 19）

### 優先順位による比較

| priority | Stage 2 | **Stage 3 v1 iter500** | Stage 3.1 iter250 |
|---|---|---|---|
| 1. gate all PASS | FAIL | FAIL | FAIL |
| 2. tolerant adoption | 74.5% | **90.1%** | 75.7% |
| 3. major patch | 15.4% | **9.6%** | 17.0% |
| 4. constraint follow | 68.9% | 78.3% | **81.5%** |
| 5. power/range | power46%/range50% | **power67%/range67%** | power48%/range45% |
| 6. cross-field consistency | 24.7% | **44.7%** | 34.4% |
| 7. category min | oiler 20.8% | oiler 20.8% | (未計算) |
| 8. diversity | n/a | 0% unique | n/a |
| 9. latency | 1.856s | 1.830s | 1.751s |

### 選定結果

**Stage 3 v1 iter500** (`models/navspec-lora-0.5b-v0.1/adapter/stage3-numeric-quality-r8-v1/adapters.safetensors`)

- tolerant adoption 90.1% は Stage 2 より +15.6pt、Stage 3.1 より +14.4pt
- major patch 9.6% は3候補中最小
- power_shp / range_nm を除く全フィールドで field gate PASS
- 厳格numeric specialist gateはFAIL（power_shp / range_nm / constraint / cross-field）
- しかし二段pipelineのend-to-end評価では classic numeric validator が弱点を補正し、**全ゲートPASS** を達成

### Section 38 判定

> A. Stage 3 v1 iter500をnumeric specialist採用: 数値専門ゲートPASS, Stage 3.1より優位

厳格には「ゲートFAIL」であるが、以下の理由から **best available candidate として採用** を提案:

1. 全候補が同じゲート項目（power_shp / range_nm / cross-field）でFAIL → 単独改善困難
2. pipeline設計は specialist の弱点を classic numeric validator で補正することを前提としている
3. end-to-end評価で tolerant 90.0% / major 9.96% / status 100% / impossible F1 100% / reason F1 100% を達成
4. Stage 3.2 numeric-only学習は power_shp/range_nm の相関学習に有効だが、pipeline運用では即時の改善が必要ではない

### Stage 3.2 検討事項（Section 29）

両candidateが厳格ゲートFAILのため、Stage 3.2 は以下を目標とする:

- `power_shp` tolerant >= 80% (現在 67.3%)
- `range_nm` tolerant >= 80% (現在 66.5%)
- speed_power_consistency >= 80%
- constraint_follow >= 90%
- 学習対象: spec/design_concept/confidence のみ（status/reason/suggested_revisionを含めない）

ただし追加学習は明示指示後に実施する。

---

## 15. SHA-256

保存先: `artifacts/navspec-lora/numeric_specialist_hashes.sha256`

```
Stage 2 baseline:           1b9f5eb892e70a321ff04236af5e753bf19d035d639a0039c0bece75752f4098
Stage 3 v1 iter500:         303fadf04c9e10d0f0046dd23fc8127c43c629e3f164be71f857b745d6d08d71
Stage 3.1 iter250:          e96b4835dc66c0af7e5665f1488b54071bf6629cb5a62df04eb4bbca999ade2a
numeric schema:             (別途計算)
numeric specialist contract: (別途計算)
ok subset manifest:         (別途計算)
```

---

## 16. Git操作未実施確認

- git commit: 未実施
- git tag: 未実施
- git push: 未実施
- registry.json default変更: 未実施
- checkpoint削除: 未実施
- dataset削除: 未実施
- 追加学習: 未実施
