# navspec Stage 2.2 balanced-reason: 速度系reason厳格化とJSON安定性

作成日: 2026-07-11

## 概要
- 出発点: Stage2 iter1000 から resume(Stage2.1 からではない)。lr 1.5e-5, mask_prompt, 500 iters。
- 教師: `stage2_2_balanced_reason_v0`(5000件, train 3680)。section5厳格定義(speed_armor_range=速力+装甲+航続の3要素必須, speed_powerと排他)、section7短縮(message≤60字・reason≤3・最小revision)、section9対照ペア。teacher validation(section10)全件PASS。
- Val loss: 0.044(resume) → 0.029(250) → 0.023(375) → **0.016(500)**。過学習なし。
- 評価セット: 既存480(旧定義) + 新規speed400(section5新定義)。

## 1. 学習前診断(section4)の結論
- Stage2.1 iter500 のJSON破損19件は eval_speed_armor_range。再生成で **finish=stop**(lengthではない)、max_tokens 512→1024 で同一(258 token)。
- 出力は4理由+冗長revisionで~684字が終端境界にあり、`}}` が脱落→root JSON未完→抽出がネスト小オブジェクトに退化→status=None。
- section4.3判定: finish=stop → **学習対象**。対策は出力短縮(section7)。

## 2. Stage2 eval set 480(free_raw) — 比較

| metric | Stage2 iter1000 | 2.1 iter500 | 2.2 iter250 | 2.2 iter500 |
|---|---:|---:|---:|---:|
| status一致 | 99.2% | 95.0% | 87.5% | 92.9% |
| schema完全一致 | 100.0% | 96.0% | 83.8% | 93.8% |
| impossible F1 | 99.5% | 96.7% | 91.2% | 95.2% |
| reason code exact(旧定義期待) | 86.5% | 88.1% | 62.5% | 76.9% |
| reason code micro F1 | 93.3% | 89.9% | 83.2% | 90.2% |
| nation_capability recall | 52.7% | 78.0% | 83.5% | 80.2% |
| speed_armor_range recall | 100.0% | 68.3% | 100.0% | 100.0% |
| speed_power recall | 42.9% | 45.7% | 42.9% | 42.9% |
| treaty precision | 98.5% | 90.9% | 75.8% | 98.5% |
| truncated JSON | 0.0% | 0.0% | 11.7% | 6.2% |
| reason+revision | 76.2% | 73.3% | 64.6% | 70.0% |

> 480の期待reasonは**旧定義**(speed_armor_rangeに航続不要・speed_powerと共存)。2.2は新定義で学習しているため、reason exactは定義ミスマッチで過小評価される(下記speed400参照)。

## 3. speed400(新定義) — 速度系 目標達成

| metric | Stage2 iter1000 | 2.2 iter500 |
|---|---:|---:|
| status一致 | 100.0% | 100.0% |
| schema完全一致 | 100.0% | 100.0% |
| reason code exact | 78.2% | 100.0% |
| truncated JSON | 0.0% | 0.0% |
| reason code micro F1 | 87.2% | 100.0% |

**速度系reason識別は目標達成**: 2.2 iter500 は新定義のspeed400で status/schema/microF1/exact すべて100%、切捨て0%。厳格定義+speed_power/speed_armor_range排他+対照ペアで、Stage2の弱点(speed_power recall 43%/混同)を解消した。

## 4. 480での回帰 — 原因は era ケース

- 2.2 iter500 の480でのschema_fail 30件・truncated 30件は **全件 eval_era**(60件中30件)。
- era案例で2.2モデルが冗長出力(~661字)を生成し `suggested_revi…` で途切れる(Stage2 iter1000は era raw_len 441-522 で全完結)。
- 原因: 2.2学習データ(速度重点の構成)で era 例が不足し、era生成の簡潔さが維持できなかった。
- これが status 92.9% / schema 93.8% / trunc 6.2% の主因。

## 5. 採用ゲート(section16)判定 — iter500 @480

| 指標 | 条件 | 実測 | 判定 |
|---|---:|---:|:--:|
| status一致 | 98% | 92.9% | FAIL |
| schema | 98% | 93.8% | FAIL |
| impossible F1 | 98% | 95.2% | FAIL |
| truncated JSON | <1% | 6.2% | FAIL |
| speed_armor_range recall | 90% | 100.0% | PASS |

**採用ゲート不合格**(status/schema/truncがera回帰で閾値下回る)。section19通り Stage2 iter1000 を正式既定モデルに維持。

## 6. 推奨
- **正式モデル: Stage2 iter1000(維持)**。tag `navspec-lora-stage2-0.5b-v0.1`。
- **2.2 iter500 は experimental**: 速度系reason識別の概念実証(新定義speed400で完璧)。ただし era 安定性回帰で広域分布には使えない。
- 次段階(2.3): 構成に era 補強を加えれば era 回帰を解消しつつ速度系改善を維持できる見込み(era例の追加のみで、学習方針は踏襲)。

## 7. 成果物(未コミット)
- adapter: `models/navspec-lora-0.5b-v0.1/adapter/stage2.2-balanced-reason-r8/`(125/250/375/500)
- データ: `data/navspec/lora/stage2_2_balanced_reason_v0/`, `stage2_2_speed_eval_v0/`
- 評価: `data/navspec/lora/eval/{stage2.2-iter250,stage2.2-iter500}/stage2eval_free_raw/`, `stage2.2-iter500/speedeval_free_raw/`, `stage2-status-r8/speedeval_free_raw/`
- ツール: `tools/navspec_lora_data_stage2_2.py`, evaluator拡張(micro/truncated), canonical順序更新
- ログ: `runs/train_stage2_2.log`, `runs/eval_stage2_2.log`
