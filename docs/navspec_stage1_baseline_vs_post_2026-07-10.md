# navspec Stage 1 LoRA: baseline vs post-training 差分比較

作成日: 2026-07-10

## 概要

- ベースモデル: `Qwen/Qwen2.5-0.5B-Instruct`
- 評価ケース: 622件 (`data/navspec/lora/raw_baseline_122_plus_500/cases.jsonl`), 同一順序
- 比較: zero-shot baseline (`qwen2_5_0_5b_zero_shot`) vs Stage1 adapter (`qwen2_5_0_5b_stage1-r8`)
- 評価モード(3): free_raw / object_prefill / schema_assisted
- 学習: Stage 1 schema-only LoRA, 1000 iters, `--mask-prompt` 有効, trainable 0.594% (2.933M/494.033M)
- 学習ロス: Val 2.010 → 0.070 / Train(iter1000) 0.061
- 各モード・各モデルで622件を完全実行(サンプリングなし)。レポートは `data/navspec/lora/eval/{slug}/{mode}/report.json`。

## モード別サマリ (base → post, delta)

### free_raw — free raw (--json-prefill none)

| metric | baseline | post-train | delta |
|---|---:|---:|---:|
| raw JSON成功率 | 95.5% | 96.0% | +0.5pt |
| schema完全一致率 | 0.0% | 91.3% | +91.3pt |
| status一致率 | 18.2% | 77.7% | +59.5pt |
| top必須フィールド率(avg) | 69.8% | 95.8% | +26.0pt |
| spec必須フィールド率(avg) | 34.5% | 80.1% | +45.5pt |
| spec field adoption率(avg) | 16.2% | 65.1% | +48.9pt |
| impossible precision | 29.8% | 100.0% | +70.2pt |
| impossible recall | 65.3% | 20.2% | -45.1pt |
| impossible F1 | 40.9% | 33.7% | -7.3pt |
| reason+revision成立率(avg) | 0.0% | 4.0% | +4.0pt |
| 反復崩壊率 | 3.1% | 0.0% | -3.1pt |

### object_prefill — object prefill (--json-prefill object)

| metric | baseline | post-train | delta |
|---|---:|---:|---:|
| raw JSON成功率 | 97.1% | 94.4% | -2.7pt |
| schema完全一致率 | 0.0% | 85.0% | +85.0pt |
| status一致率 | 24.4% | 75.9% | +51.4pt |
| top必須フィールド率(avg) | 74.9% | 94.1% | +19.2pt |
| spec必須フィールド率(avg) | 36.6% | 81.5% | +44.9pt |
| spec field adoption率(avg) | 22.0% | 66.7% | +44.7pt |
| impossible precision | 33.6% | 100.0% | +66.4pt |
| impossible recall | 87.9% | 14.5% | -73.4pt |
| impossible F1 | 48.6% | 25.3% | -23.3pt |
| reason+revision成立率(avg) | 0.0% | 4.0% | +4.0pt |
| 反復崩壊率 | 1.6% | 0.0% | -1.6pt |

### schema_assisted — schema assisted (--json-prefill schema)

| metric | baseline | post-train | delta |
|---|---:|---:|---:|
| raw JSON成功率 | 92.1% | 99.8% | +7.7pt |
| schema完全一致率 | 0.0% | 0.0% | +0.0pt |
| status一致率 | 15.6% | 53.4% | +37.8pt |
| top必須フィールド率(avg) | 80.5% | 50.7% | -29.7pt |
| spec必須フィールド率(avg) | 49.4% | 22.4% | -27.0pt |
| spec field adoption率(avg) | 23.3% | 16.3% | -7.1pt |
| impossible precision | 22.8% | 17.4% | -5.4pt |
| impossible recall | 56.1% | 15.0% | -41.0pt |
| impossible F1 | 32.4% | 16.1% | -16.3pt |
| reason+revision成立率(avg) | 0.0% | 0.0% | +0.0pt |
| 反復崩壊率 | 5.6% | 0.0% | -5.6pt |

## 主要観察

- **free_raw / object_prefill は学習で大幅改善**: schema完全一致率 0%→91.3% / 0%→85.0%、spec field adoption 16.2%→65.1% / 22.0%→66.8%、status一致率 18.2%→77.6% / 24.4%→75.9%。反復崩壊は両モードで 0% に消除。
- **schema_assisted は学習で一部劣化**: schema完全一致率は 0%→0% のまま、top必須フィールド率 80.5%→50.7%、spec必須フィールド率 49.4%→22.4% に低下。status一致率・raw JSON率は改善したが、フィールド完備性は base を下回った。
- **impossible 判定のシフト**: 学習後 precision は free/object で 1.0 に達した一方、recall は全モードで低下(過少申告傾向)。F1 は schema_assisted で低下、free_raw で微減、object_prefill で低下。

## schema_assisted 劣化の原因(エビデンス)

schema prefill は `schema_version` を先頭に注入するが、学習データ(`sort_keys=True` の compact JSON)のキー順は confidence→design_concept→reason→schema_version→spec→status→suggested_revision。順序不整合により、学習後モデルは prefill 続きで早すぎる終了を起こし末尾の必須フィールド(`reason` / `design_concept` / `confidence`)が欠落する。失敗例:

```
schema_assisted post 失敗例 classic_eval_unseen_001:
  {\n  "schema_version": "navspec.lora_candidate.v1",\n "spec": "...","status":"ok","suggested_revision":{}}
  -> missing required: reason, design_concept

free_raw post 成功例 classic_eval_unseen_001:
  {"confidence":0.88,"design_concept":"...","reason":[],"schema_version":"...","spec":{...},"status":"ok","suggested_revision":{}}
  -> 全7フィールド揃い schema valid
```

## 推奨

- Stage 1 adapter の評価は **free_raw または object_prefill** を主指標に使う。schema_assisted(prefill)は訓練フォーマットと衝突するため、アダプタ評価では避けるか prefill を compact/ソート済み形式に合わせる。
- Stage ゲート(`stage_1_raw_json_rate_min=0.9`, `stage_1_schema_rate_min=0.8`)は free_raw の数値(schema_complete 91.3%, raw_json 95.9%)でクリア。
- impossible recall の低下は別途要因分析(過少申告)。次段階で reason/revision 品質を追加学習する候補。

## レポートファイル

- baseline free_raw: `data/navspec/lora/eval/qwen2_5_0_5b_zero_shot/free_raw/report.json`
- baseline object_prefill: `data/navspec/lora/eval/qwen2_5_0_5b_zero_shot/object_prefill/report.json`
- baseline schema_assisted: `data/navspec/lora/eval/qwen2_5_0_5b_zero_shot/schema_assisted/report.json`

- post free_raw: `data/navspec/lora/eval/qwen2_5_0_5b_stage1-r8/free_raw/report.json`
- post object_prefill: `data/navspec/lora/eval/qwen2_5_0_5b_stage1-r8/object_prefill/report.json`
- post schema_assisted: `data/navspec/lora/eval/qwen2_5_0_5b_stage1-r8/schema_assisted/report.json`

- 学習ログ: `runs/train_stage1.log` / adapter: `models/navspec-lora-0.5b-v0.1/adapter/stage1-r8/adapters.safetensors`
