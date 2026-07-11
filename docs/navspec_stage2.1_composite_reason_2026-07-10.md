# navspec Stage 2.1 複合reason改善: 計量バグ修正 + composite boost

作成日: 2026-07-10

## 目的とアプローチ

Stage 2 iter1000 で impossible reason_code exact 82.4%、うち nation_capability recall 0.53 / speed_power recall 0.43(共存時にコード脱落)が弱点だった。Stage 2.1 は:
- 計量バグ修正: 空コード集合(ok例)の reason_code_f1 が 0.0 になっていたのを 1.0 に修正(これまでの reason_code_f1_avg は不当に低かった)。
- 教師改善(profile=stage2.1): reason code の canonical ソート + composite 例の強化(nation_capability/speed_power が他codeと共存する例を 6000件追加) + treaty境界 ok 例 1000件。
- 学習: Stage2 iter1000 から resume, lr 2.5e-5, --mask-prompt, 750 iters。
- データ: `stage2_1_status_v0`(20,000件)。Stage2データ/adapter/schemaは全て保護(別dir)。

## 学習
- Val loss: 0.070(1, resume確認) → 0.041(250) → **0.026(500, 最小)** → 0.041(750)
- checkpoints: stage2.1-status-r8/{250,500,750} + final

## 1. 比較 — Stage2 iter1000 vs Stage2.1 (Stage2 eval set 480, free_raw, stage2 schema)

| metric | Stage2 iter1000 | 2.1 iter250 | 2.1 iter500 | 2.1 iter750 |
|---|---:|---:|---:|---:|
| status一致率 | 99.2% | 94.2% | 95.0% | 95.2% |
| schema完全一致率 | 100.0% | 95.8% | 96.0% | 95.8% |
| impossible precision | 100.0% | 98.6% | 99.1% | 100.0% |
| impossible recall | 98.9% | 93.8% | 94.3% | 93.8% |
| impossible F1 | 99.5% | 96.1% | 96.7% | 96.8% |
| reason code exact(impossible) | 82.4% | 71.4% | 85.4% | 86.5% |
| reason code F1(impossible,修正後) | 91.9% | 85.5% | 91.4% | 91.4% |
| nation_capability recall | 53% | 82% | 78% | 74% |
| speed_power recall | 43% | 43% | 46% | 43% |
| speed_armor_range recall | 100% | 67% | 68% | 67% |
| treaty precision | 82% | 100% | 100% | 100% |
| reason+revision成立率 | 76.2% | 73.3% | 73.3% | 72.3% |
| 反復崩壊率 | 0.0% | 0.0% | 0.0% | 0.0% |

## 2. 所見(エビデンス)

**改善(目標達成):**
- nation_capability recall **53%→78%(iter500)**: composite_nation 例(弱小国+条約/大砲の共存)で、モデルが他codeと併せて nation_capability を落とさなくなった。
- treaty precision **82%→100%**: treaty境界 ok 例(非条約年の大型巡洋艦 ok)で、条約年以外の過剰発火が消失。
- reason code exact **82.4%→86.5%(iter750)**。

**回帰(代償):**
- speed_armor_range recall **100%→68%(iter500)**: speed系コード間で混同。composite_speed_power 例が speed_power vs speed_armor_range の境界を曖昧にした。
- speed_power recall は **43%→46%** で殆ど不変(composite boostの効果限定的)。
- status一致率 **99.2%→95.0%** / schema **100%→96%**: 回帰22件のうち19件は `eval_speed_armor_range`(3コードの最複雑ケース)で、モデルが多数コードを並べようとして JSON が途中で壊れ status が None 化。

## 3. 採用基準(section 14) クリア判定

| 指標 | 最低 | Stage2 iter1000 | 2.1 iter250 | 2.1 iter500 | 2.1 iter750 |
|---|---:|:--:|:--:|:--:|:--:|
| raw JSON | 93% | PASS |PASS |PASS |PASS |
| schema | 88% | PASS |PASS |PASS |PASS |
| status | 88% | PASS |PASS |PASS |PASS |
| imp precision | 90% | PASS |PASS |PASS |PASS |
| imp recall | 80% | PASS |PASS |PASS |PASS |
| imp F1 | 85% | PASS |PASS |PASS |PASS |
| reason+revision | 70% | PASS |PASS |PASS |PASS |
| 反復崩壊 | <1% | PASS |PASS |PASS |PASS |

全チェックポイントが section14 ゲートをクリア(回帰はstage2の完璧値からの相対低下で、閾値にはまだ余裕)。

## 4. 推奨

- **バランス最優先: Stage2 iter1000** (`stage2-status-r8`, tag `navspec-lora-stage2-0.5b-v0.1`) — status 99.2% / schema 100% / impF1 99.5%。reason code は nation_capability/speed_power で弱点だが、主指標は最高。
- **reason code の網羅性(nation_capability)優先: Stage2.1 iter500** (`stage2.1-iter500`) — nation_cap recall 78%・treaty precision 100%・exact 85.4%。ゲートクリア済み。status/schema は数pt譲る。
- どちらも実運用可能。統合時の「モデル値70%採用」要件には Stage2 iter1000 が無難。

## 5. 次段階(2.2)の課題

- speed_power recall(46%)と speed_armor_range recall(68%)の同時改善: 2.1 は速度系コードを振り回した。速度系だけを分離強化するか、コード定義の境界(高速 vs 高速+装甲)を整理する必要。
- 複雑3コード_case(eval_speed_armor_range)の JSON 安定性: 長い reason 配列を生成する際の出力途切れ。max_tokens か訓練シーケンス長の調整、または reason 件数の上限運用。
- composite 比は 30% で強すぎた可能性。15-20% に下げれば status 回帰を抑えつつ nation_capability 改善を維持できるかもしれない。

## 6. 成果物(未コミット)

- adapter: `models/navspec-lora-0.5b-v0.1/adapter/stage2.1-status-r8/`(250/500/750)
- データ: `data/navspec/lora/stage2_1_status_v0/`
- 評価: `data/navspec/lora/eval/stage2.1-{iter250,iter500,iter750}/stage2eval_free_raw/report.json`
- 計量修正: `tools/navspec_lora_stage2_schema.py`(reason_code_metrics 空集合→1.0)
- ログ: `runs/train_stage2_1.log`, `runs/eval_stage2_1.log`
