# navspec Stage 2 LoRA: Stage1 vs Stage2 差分比較

作成日: 2026-07-10

## 概要

- ベース: `Qwen/Qwen2.5-0.5B-Instruct` + Stage1 adapter (`stage1-r8`, sha256 `ce982e6f...2612`, 凍結・不変)
- Stage2 出力: `reason=[{code,message}]` 構造化 / `status=impossible` は `spec:{}` / `status=ok` は18フィールド (新規 `schemas/navspec_lora_candidate_stage2.schema.json`, v1は温存)
- 教師データ: `data/navspec/lora/stage2_status_v0/` 20,000件 (train 13,927 / valid 3,487 / test 2,586), status 比 ok:impossible ≒ 46:54, reason code 10種網羅, classic rule engine と 99.5% 一致
- 構成: schema replay 30% / normal ok 25% / impossible 25% / reason+revision 15% / hard negative 5%
- 学習: Stage1 から resume (`--resume-adapter-file`), `--mask-prompt` 必須, lr 5e-5, 1000 iters, save_every 250, trainable 0.594% (2.933M)
- Val loss: 0.903(1) → 0.063(250) → 0.046(500) → 0.041(750) → 0.044(1000)
- 評価: 主評価 `free_raw`, Stage2専用ケース480件 (`stage2_eval_v0`) + 既存622件. schema_assisted は不使用(キー順序衝突).

## 1. Stage2セット(480) free_raw — Stage1 vs チェックポイント

| metric | Stage1 | iter250 | iter500 | iter1000 |
|---|---:|---:|---:|---:|
| raw JSON成功率 | 99.2% | 100.0% | 100.0% | 100.0% |
| schema完全一致率 | 87.1% | 100.0% | 100.0% | 100.0% |
| status一致率 | 32.7% | 95.2% | 98.1% | 99.2% |
| impossible precision | 94.7% | 99.7% | 100.0% | 100.0% |
| impossible recall | 14.6% | 94.1% | 97.6% | 98.9% |
| impossible F1 | 25.3% | 96.8% | 98.8% | 99.5% |
| reason+revision成立率 | 0.0% | 72.7% | 75.2% | 76.2% |
| 反復崩壊率 | 0.8% | 0.0% | 0.0% | 0.0% |
| reason code exact率 | 22.9% | 73.1% | 88.5% | 86.5% |
| reason code F1(avg) | 0.0% | 65.1% | 72.8% | 70.9% |
| field adoption(ok例のみ) | 66.8% | 83.7% | 84.4% | 87.4% |

> 補足: `spec_field_adoption` は impossible 案例(`spec:{}`)が0になるため、ok 110例のみの平均で表示。全体avgは impossible 370例で希釈され約20%になるが設計通り。

## 2. 採用基準(section 14) クリア判定 — iter1000 free_raw

| 指標 | 最低条件 | 実測 | 判定 |
|---|---:|---:|:--:|
| raw JSON成功率 | 93%以上 | 100.0% | PASS |
| schema完全一致率 | 88%以上 | 100.0% | PASS |
| field adoption率(ok例) | 60%以上 | 87.4% | PASS |
| status一致率 | 88%以上 | 99.2% | PASS |
| impossible precision | 90%以上 | 100.0% | PASS |
| impossible recall | 80%以上 | 98.9% | PASS |
| impossible F1 | 85%以上 | 99.5% | PASS |
| reason+revision成立率 | 70%以上 | 76.2% | PASS |
| 反復崩壊率 | 1%未満 | 0.0% | PASS |

**全項目 PASS** (iter1000)。理想目標(schema≥90%, adoption≥70%, status≥90%, F1≥90%)も全て達成。

## 3. カテゴリ別集計 — Stage1 vs iter1000 (Stage2セット480)

| カテゴリ | n | status(Stage1) | status(iter1000) | reason code exact(iter1000) |
|---|---:|---:|---:|---:|
| normal ok | 60 | 81.7% | 93.3% | 86.7% |
| small ship contradiction | 60 | 0.0% | 100.0% | 83.3% |
| constraint boundary(armor) | 60 | 0.0% | 100.0% | 100.0% |
| constraint boundary(speed/armor/range) | 60 | 0.0% | 100.0% | 66.7% |
| era contradiction | 60 | 13.3% | 100.0% | 80.0% |
| treaty contradiction | 60 | 0.0% | 100.0% | 100.0% |
| nation tier contradiction | 60 | 75.0% | 100.0% | 75.0% |
| hard negative | 60 | 91.7% | 100.0% | 100.0% |

- 全 impossible 系カテゴリで status 100%。hard negative も100%(過剰拒否なし)。normal ok 93%。

## 4. 既存622件 free_raw — Stage1 vs Stage2(iter1000) 連続性

| metric | Stage1(622) | Stage2 iter1000(622) |
|---|---:|---:|
| raw JSON | 96.0% | 100.0% |
| schema(v1基準) | 91.3% | 73.2% |
| status一致 | 77.7% | 91.2% |
| impossible precision | 100.0% | 85.5% |
| impossible recall | 20.2% | 82.1% |
| impossible F1 | 33.7% | 83.8% |
| 反復崩壊 | 0.0% | 0.0% |

- 622の schema は v1 基準のため、Stage2 の構造化 reason / `spec:{}` が v1 schema に合わず 73% に見える(フォーマット差であり能力低下ではない)。
- impossible recall が **20.2% → 82.1%**, F1 **33.7% → 83.8%** へ大幅改善(元の分布でも維持)。

## 5. 推奨チェックポイント

- **推奨: iter1000 (`models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8`)** — status 99.2% / impossible F1 99.5% / schema 100% / ok例 adoption 87.4% / reason+revision 76.3% / 反復崩壊 0%。
- reason code exact率は iter500(88.5%) が iter1000(86.5%) を僅差で上回る。reason code の正確さを最優先する用途では iter500 (`stage2-iter500`) も候補。ただし status/F1/adoption は iter1000 が最高。
- iter250 でも既にゲート大部分をクリア(高速プレビュー用)。

## 6. 残存問題

- reason code exact率 86%台: speed_armor_range 系の複数コード(env: armor+speed_armor_range+range_volume)で、モデルが一部コードを省略/過剰。複数コード同時命中の精度が次段階の改善点。
- `schema_assisted` モードは学習データ canonical キー順と prefill が衝突するため今回も不使用(section16通り)。修正は別途(prefill を canonical prefix に合わせるかキー順再設計+再学習)。
- 622件は v1 フォーマット期待のため、Stage2 モデルを通すと impossible 側の schema 完全一致が v1 基準で下がる。実運用では Stage2 schema で評価する前提。
- Stage2 は候補生成と拒否理由が役割。最終物理保証は classic rule engine 側(section20の役割分担維持)。

## 7. 成果物

- adapter: `models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/adapters.safetensors` (checkpoints 250/500/750/1000 同梱)
- データ: `data/navspec/lora/stage2_status_v0/`, `data/navspec/lora/stage2_eval_v0/`
- 評価レポート: `data/navspec/lora/eval/{stage1-r8,stage2-iter250,stage2-iter500,stage2-status-r8}/stage2eval_free_raw/report.json`, `.../stage2-status-r8/set622_free_raw/report.json`
- 学習ログ: `runs/train_stage2.log`, 評価ログ: `runs/eval_stage2.log`
- ツール: `tools/navspec_lora_data_stage2.py`, `tools/navspec_lora_stage2_schema.py`, schema `schemas/navspec_lora_candidate_stage2.schema.json`
