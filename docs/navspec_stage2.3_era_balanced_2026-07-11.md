# navspec Stage 2.3 era-balanced 実験レポート

作成日: 2026-07-11

## 概要

- 目的: Stage 2.2 の速度系 reason 能力を保持しつつ、era ケースの JSON 完結性と schema 安定性を回復する。
- 出発点: `models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/adapters.safetensors`。Stage 2.1/2.2 からは resume していない。
- 出力先: `models/navspec-lora-0.5b-v0.1/adapter/stage2.3-era-balanced-r8/`。
- 結論: **正式採用不可**。best は総合的に iter250 だが、Stage2 schema 98.1% と strict reason、era status 93.5%、speed reason micro F1 89.3% が採用ゲート未達。Stage 2 iter1000 を正式既定モデルとして維持する。

## 変更ファイル

- 追加: `tools/navspec_lora_data_stage2_3.py`
- 追加: `data/navspec/lora/stage2_3_era_balanced_v0/`
- 追加: `data/navspec/lora/stage2_3_era_eval_v0/`
- 追加: `models/navspec-lora-0.5b-v0.1/adapter/stage2.3-era-balanced-r8/`
- 追加: `models/navspec-lora-0.5b-v0.1/adapter/stage2.3-iter{125,250,500}/` 評価用 checkpoint adapter
- 追加: `data/navspec/lora/eval/stage2.3-iter{125,250,500}/`
- 追加: `docs/stage2.3-iter{125,250,500}_{stage2eval,speedeval,eraeval}_free_raw_2026-07-11.md`
- 追加: `docs/navspec_stage2.3_era_balanced_2026-07-11.md`

## 生成データ

| dataset | rows | train | validation | test/eval | status ok | status impossible |
|---|---:|---:|---:|---:|---:|---:|
| stage2_3_era_balanced_v0 | 5000 | 3757 | 852 | 391 | 2660 | 2340 |
| stage2_3_era_eval_v0 | 400 | - | - | 400 | 140 | 260 |

構成: Stage2 replay 1500、era replay 1000、schema/status replay 750、speed_power 500、speed_armor_range 500、era×speed 250、nation/treaty 250、hard negative 250。速度系は 25%。

## Teacher Validation

- Stage 2 schema: PASS
- reason enum / 重複なし / 最大3件 / canonical順序: PASS
- message <= 60字: PASS
- design_concept <= 80字: PASS
- status=ok 時 reason=[] / suggested_revision={}: PASS
- status=impossible 時 spec={} / reasonあり / suggested_revisionあり: PASS
- assistant長: max 557字、p95 541字、平均 421.77字。上限620字、p95目標560字を PASS。
- EOS/mask 10件検査: PASS。EOS `151645` が非マスク領域にあり、offset > 0、非マスク先頭は assistant JSON。

## 学習

```bash
uv run python tools/qwen_lora_train.py \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --train \
  --data data/navspec/lora/stage2_3_era_balanced_v0 \
  --resume-adapter-file models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/adapters.safetensors \
  --adapter-path models/navspec-lora-0.5b-v0.1/adapter/stage2.3-era-balanced-r8 \
  --iters 500 \
  --batch-size 1 \
  --num-layers 16 \
  --learning-rate 0.00001 \
  --mask-prompt \
  --save-every 125
```

- 学習時間: 約1分31秒
- trainable parameters: 0.594% (2.933M/494.033M)
- Val loss: iter1 0.823、iter200 0.053、iter400 0.020、iter500 0.018
- Checkpoints: `0000125_adapters.safetensors`, `0000250_adapters.safetensors`, `0000375_adapters.safetensors`, `0000500_adapters.safetensors`, final `adapters.safetensors`

## 評価結果

主評価は `free_raw --stage2 --max-tokens 512`。

| checkpoint | set | status | schema | impossible F1 | reason exact | reason micro F1 | trunc JSON | repetition |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| iter125 | Stage2 480 | 92.1% | 80.4% | 94.6% | 56.2% | 75.4% | 7.3% | 0.0% |
| iter125 | speed400 | 100.0% | 100.0% | 100.0% | 78.2% | 92.8% | 0.0% | 0.0% |
| iter125 | era400 | 93.5% | 100.0% | 94.7% | 68.5% | 74.6% | 0.0% | 0.0% |
| iter250 | Stage2 480 | 99.2% | 98.1% | 99.5% | 73.1% | 87.9% | 0.0% | 0.0% |
| iter250 | speed400 | 100.0% | 100.0% | 100.0% | 85.0% | 89.3% | 0.0% | 0.0% |
| iter250 | era400 | 93.5% | 100.0% | 94.7% | 88.5% | 88.9% | 0.0% | 0.0% |
| iter500 | Stage2 480 | 99.2% | 97.3% | 99.5% | 74.0% | 88.5% | 0.0% | 0.0% |
| iter500 | speed400 | 100.0% | 100.0% | 100.0% | 85.0% | 89.3% | 0.0% | 0.0% |
| iter500 | era400 | 93.5% | 100.0% | 94.7% | 85.5% | 84.8% | 0.0% | 0.0% |

## Stage 2 / Stage 2.2 比較

| metric | Stage2 iter1000 | Stage2.2 iter500 | Stage2.3 best(iter250) |
|---|---:|---:|---:|
| Stage2 status | 99.2% | 92.9% | 99.2% |
| Stage2 schema | 100.0% | 93.8% | 98.1% |
| Stage2 impossible F1 | 99.5% | 95.2% | 99.5% |
| Stage2 truncated JSON | 0.0% | 6.2% | 0.0% |
| Stage2 reason exact | 86.5% | 76.9% | 73.1% |
| speed400 reason exact | 78.2% | 100.0% | 85.0% |
| speed400 micro F1 | 87.2% | 100.0% | 89.3% |
| era400 status | - | - | 93.5% |
| era400 schema | - | - | 100.0% |
| era400 truncation | - | - | 0.0% |

Stage2.3 は Stage2.2 の era truncation を解消したが、Stage2全体の schema と reason exact、speed400 の strict reason を十分に回復できなかった。

## Reason Code Recall

| checkpoint/set | era equipment | speed power | speed armor range | range volume | nation capability | treaty |
|---|---:|---:|---:|---:|---:|---:|
| iter250 Stage2 | 100.0% | 60.0% | 90.0% | 100.0% | 63.7% | 98.5% |
| iter250 speed400 | - | 100.0% | 100.0% | 70.0% | - | - |
| iter250 era400 | 84.6% | 90.9% | - | - | 100.0% | - |
| iter500 Stage2 | 100.0% | 74.3% | 81.7% | 100.0% | 83.5% | 90.9% |
| iter500 speed400 | - | 100.0% | 100.0% | 70.0% | - | - |
| iter500 era400 | 84.6% | 90.9% | - | - | 50.0% | - |

speed400 の未達主因は `eval_range_volume_impossible` 60件で、期待 `range_volume_conflict` に対し `speed_power_conflict` を出したこと。

## JSON破損内訳

| checkpoint/set | truncated | breakdown |
|---|---:|---|
| iter125 Stage2 | 7.3% | reason_array_truncation 35 |
| iter250 Stage2 | 0.0% | {} |
| iter500 Stage2 | 0.0% | {} |
| iter125/250/500 speed400 | 0.0% | {} |
| iter125/250/500 era400 | 0.0% | {} |

early EOS invalid JSON は今回の評価器では独立分類していない。`root_json_valid=false` かつ抽出JSONありの破損は `truncated_json` として計上し、iter250/500 では 0%。

## 主な混同

iter250 Stage2:

- 50件: expected `main_gun_too_large_for_hull` → actual `main_gun_too_large_for_hull` + `nation_capability_exceeded`
- 15件: expected `armor_too_heavy_for_hull` → actual `armor_too_heavy_for_hull` + `displacement_limit_exceeded`
- 14件: expected `armor_too_heavy_for_hull` + `range_volume_conflict` + `speed_armor_range_conflict` + `speed_power_conflict` → actual `armor_too_heavy_for_hull` + `range_volume_conflict` + `speed_armor_range_conflict`
- 14件: expected `nation_capability_exceeded` → actual `treaty_limit_exceeded`
- 12件: expected `era_equipment_unavailable` + `nation_capability_exceeded` → actual `era_equipment_unavailable`

iter250 speed400:

- 60件: expected `range_volume_conflict` → actual `speed_power_conflict`

iter250 era400:

- 20件: expected `era_equipment_unavailable` → actual `displacement_limit_exceeded`
- 13件: expected `speed_power_conflict` → actual no reason
- 13件: expected `main_gun_too_large_for_hull` → actual no reason

## 採用ゲート

| gate | best observed | condition | result |
|---|---:|---:|:--:|
| Stage2 status | 99.2% | >=98.5% | PASS |
| Stage2 schema | 98.1% | >=98.5% | FAIL |
| Stage2 impossible F1 | 99.5% | >=98.5% | PASS |
| Stage2 truncated JSON | 0.0% | <1% | PASS |
| Stage2 reason exact | 73.1% | >=85% | FAIL |
| Stage2 reason micro F1 | 88.5% | >=95% | FAIL |
| Stage2 repetition | 0.0% | <1% | PASS |
| era status | 93.5% | >=99% | FAIL |
| era schema | 100.0% | >=99% | PASS |
| era equipment recall | 84.6% | >=95% | FAIL |
| era speed recall | 90.9% | >=90% | PASS |
| era truncated JSON | 0.0% | 0% | PASS |
| speed status | 100.0% | >=99% | PASS |
| speed schema | 100.0% | >=99% | PASS |
| speed reason exact | 85.0% | >=95% | FAIL |
| speed micro F1 | 89.3% | >=98% | FAIL |
| speed_power recall | 100.0% | >=85% | PASS |
| speed_armor_range recall | 100.0% | >=95% | PASS |
| speed truncated JSON | 0.0% | 0% | PASS |

Stage2からの回帰上限: status 0.0pt PASS、schema -1.9pt FAIL、impossible F1 0.0pt PASS、field adoption +1.5pt PASS。

## 推奨 checkpoint と正式モデル更新

- 推奨 checkpoint: `stage2.3-iter250` を実験成果の代表として保存。
- 正式モデル更新: **不可**。主要ゲート未達のため `models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8/` を正式既定モデルとして維持する。
- registry/default/tag は変更していない。

## 残存問題

- Stage2 480 の `eval_armor` で invalid reason code 生成により schema が 85.0%(iter250) / 78.3%(iter500) に低下。
- Stage2 480 の `eval_normal_ok` で過剰 impossible が残る(status 93.3%)。
- era400 の `eval_era_nation_impossible` は status 48.0%。era×nation の対照設計が弱い。
- speed400 の `range_volume_conflict` recall が 70.0%。`speed_power_conflict` へ寄る。
- reason exact は Stage2.2 の speed専用100%から後退。era replay 追加で速度専用の厳密 reason 境界が薄まった。

## Git / LFS

- `git commit`, `git tag`, `git push`, registry default変更は未実施。
- `git diff --stat` は tracked 変更なし。成果物は新規未追跡ファイル/ディレクトリ。
- 新規 safetensors は各約11MB、Stage2.3 adapter全体は約56MB、評価用 checkpoint adapter は各約11MB。現時点で `.gitattributes` は見つからない。コミットする場合は Git LFS の導入を推奨する。

## 保護対象確認

既存の Stage 1 / Stage 2 / Stage 2.1 / Stage 2.2 adapter、既存 schema、既存 dataset、既存レポート、`models/registry.json` は変更していない。
