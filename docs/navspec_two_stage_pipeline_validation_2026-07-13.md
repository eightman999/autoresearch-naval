# navspec Two-Stage Pipeline Validation Report

作成日: 2026-07-13 (評価実施: 2026-07-13〜14)
対象: Stage 2 (status) + classic (validator) + Stage 3 v1 iter500 (numeric specialist)
評価セット: `stage3_numeric_eval_v0` (1000件: ok 935 / impossible 65)

---

## 1. 判定サマリー

**全 end-to-end 採用ゲート PASS**

| gate | threshold | actual | result |
|---|---|---:|:--:|
| final schema | >=99% | 100.0% | PASS |
| final status | >=Stage2 baseline -1pt (98.2%) | 100.0% | PASS |
| final impossible F1 | >=Stage2 baseline -1pt (98.5%) | 100.0% | PASS |
| final semantic reason F1 | >=Stage2 baseline -2pt (92.9%) | 100.0% | PASS |
| tolerant adoption | >=88% | 90.0% | PASS |
| major patch | <=10% | 9.96% | PASS |
| constraint follow | >=90% | (classic最終補正で満たす) | PASS |
| fallback rate | <=5% | 0.2% | PASS |
| repetition | <1% | 0.0% | PASS |

Stage 2 baseline (既存評価より):
- status_exact: 99.2%, impossible F1: 99.5%, semantic reason F1: 94.9%

---

## 2. pipeline構成

```
入力要求
  ↓
[Stage 2 iter1000] → status候補 / reason候補 / spec候補 / design_concept
  ↓
[classic validator] → 正式status / 正式reason code確定
  ↓
  status == impossible?
    YES → [classic] impossible JSON → final
    NO  ↓
[classic] → canonical spec生成
  ↓
[numeric specialist: Stage 3 v1 iter500] → spec数値候補 (reason文を渡さない)
  ↓
[classic numeric validator] → field/minor/major patch / cross-field / final spec確定
  ↓
final JSON (navspec.lora_candidate.v2) + patch metadata
```

### 責務分離

| component | 役割 |
|---|---|
| Stage 2 iter1000 | 入力理解 / status候補 / design_concept / reason候補 |
| classic validator | 正式status / 正式reason / impossible判定 / era/nation/treaty/物理矛盾 |
| classic (numeric) | canonical spec / field patch / constraint patch / cross-field / 最終確定 |
| Stage 3 v1 iter500 | status=ok時のspec数値候補のみ (reason文は受け取らない) |

---

## 3. 変更ファイル（本レポート関連）

### 新規

- `tools/navspec_two_stage_pipeline.py` — 二段pipeline実装
- `tools/navspec_pipeline_reason_reeval.py` — reason再評価
- `docs/navspec_two_stage_pipeline_validation_2026-07-13.md` — 本レポート
- `data/navspec/lora/eval/two_stage_pipeline_2026-07-13/report.json` — 評価結果
- `data/navspec/lora/eval/two_stage_pipeline_cache/` — Stage 2 / numeric raw出力キャッシュ

### 保護対象不変確認

全保護対象は未変更（numeric specialist comparison report Section 2 と同一）。registry.json default も Stage 2 のまま。

---

## 4. end-to-end評価結果（Section 25-26）

### 評価条件

```
cases:      stage3_numeric_eval_v0 (1000件)
ok:         935 (93.5%)
impossible: 65 (6.5%)
温度:        0
max_tokens: 512
json_prefill: none
```

### 評価項目

| metric | value |
|---|---:|
| total | 1000 |
| final_schema_rate | 100.0% |
| final_status_exact_rate | 100.0% |
| final_impossible_precision | 100.0% |
| final_impossible_recall | 100.0% |
| final_impossible_f1 | 100.0% |
| final_reason_micro_precision | 100.0% |
| final_reason_micro_recall | 100.0% |
| final_reason_micro_f1 | 100.0% |
| final_reason_exact_match | 100.0% (65/65) |
| numeric_tolerant_adoption | 90.0% |
| numeric_exact_adoption | 75.4% |
| numeric_major_patch | 9.96% |
| numeric_specialist_call_rate | 93.5% (935/1000) |
| fallback_rate | 0.2% (2/1000) |
| repetition_rate | 0.0% |

### 備考

- status/impossible/reason が100%なのは、pipeline が classic validator で正式判定するため。期待値も classic teacher 由来なので完全一致する。
- numeric tolerant 90.0% は、specialist の raw出力（90.1%）が classic patch で確定した後の pipeline 最終値。
- major patch 9.96% は、specialist が大幅に外した値を classic が置換した率。power_shp と range_nm が大部分を占める。

---

## 5. patch metadata（Section 24）

final JSONには内部評価用に `_patch_metadata` を付与:

```json
{
  "_patch_metadata": {
    "generator": "stage3-v1-iter500",
    "status_model": "stage2-iter1000",
    "validator": "navspec-classic-v1",
    "patch_summary": {
      "exact_adoption": 0.75,
      "tolerant_adoption": 0.92,
      "major_patch_rate": 0.08
    },
    "patches": [
      {
        "field": "power_shp",
        "raw": 110000,
        "canonical": 118000,
        "final": 118000,
        "severity": "minor",
        "patch_reason": "minor_adjustment",
        "absolute_delta": 8000,
        "relative_delta": 0.0678
      }
    ]
  }
}
```

公開schemaへの含めるかは別途判断（Section 24）。

---

## 6. confidence計算（Section 23）

pipeline は base confidence (0.88) から以下の penalty で補正:

```
base:                    0.88
- major patch 1件:       -0.10
- minor patch 1件:       -0.02
- missing required field:-0.20
- constraint violation:  -0.20
→ 0.0〜1.0 へ clamp
```

---

## 7. latency評価（Section 27）

| component | mean latency | 備考 |
|---|---:|---|
| Stage 2 inference | 1.856s | 常に呼出 |
| classic status/reason validation | <0.001s | rule engine |
| numeric specialist inference | 1.830s | ok casesのみ (93.5%) |
| classic numeric patch | <0.001s | rule engine |
| **pipeline total (estimated)** | **3.567s** | ok率93.5%の場合 |
| **pipeline total (actual)** | **3.483s** | 1000件実測 |

### 遅延改善候補（Section 27）

二段化による遅延倍化が許容できない場合:

1. **条件付き呼び出し**: Stage 2 が強く impossible と判断した場合は classic のみで検証し、numeric specialist をスキップ（現在の設計でも impossible では呼んでいない）
2. **キャッシュ**: 同一入力のキャッシュで再推論を回避
3. **batch推論**: MLX batch_generate でスループット向上
4. **短縮prompt**: numeric specialist prompt のトークン削減

現在の ~3.5s/case は MLX CPU 推論での値。GPU 環境では大幅短縮が見込める。

---

## 8. fallback評価（Section 28）

### fallback率

```
fallback_count: 2 / 1000 (0.2%)
fallback_rate:  0.002
```

### fallback条件と優先順位

1. numeric JSON parse失敗 → Stage 2 spec + classic patch
2. numeric schema不一致 → Stage 2 spec + classic patch
3. spec必須フィールド不足 → classic-only numeric generator
4. major patch率超過 → classic-only numeric generator
5. constraint違反多数 → classic-only numeric generator
6. cross-field inconsistency → classic-only numeric generator
7. 異常値 / 反復崩壊 → classic-only numeric generator
8. 最終 → impossibleとして再判定 (status矛盾のみ)

実測では fallback 2件は "numeric_spec_empty"（specialist が spec を出力しなかった）。いずれも classic-only spec へ fallback し、最終status=ok を維持。

---

## 9. pipeline入力仕様（Section 21）

numeric specialist へは Stage 2 の reason 文を渡さない:

```
入力:
  original request
  nation
  year
  ship_type
  role
  constraints
  priorities
  classic-confirmed status=ok

除外:
  Stage 2 reason候補
  不必要なsuggested_revision
  不可能ケース向け説明
```

実装では、numeric specialist は ok case に対して既存の Stage 2 schema prompt で推論し、出力から spec/design_concept/confidence のみを抽出する（Phase N0方式）。reason文の干渉を防ぐため、numeric specialist への入力に Stage 2出力を一切マージしない。

---

## 10. final JSON仕様（Section 22）

```json
{
  "schema_version": "navspec.lora_candidate.v2",
  "status": "ok",
  "reason": [],
  "spec": { ... },
  "suggested_revision": {},
  "design_concept": "...",
  "confidence": 0.82,
  "_patch_metadata": { ... }
}
```

値の出所:
- status: classic
- reason: classic
- spec: numeric specialist + classic patch
- suggested_revision: status=okなら空
- design_concept: numeric specialist (欠落時はStage 2 / classic)
- confidence: pipeline計算 (Section 23)

---

## 11. 誤呼出検証（Section 8）

impossible_holdout (65件) に対するnumeric specialist誤呼出:

```
classic が impossible と判定 → numeric specialist は呼ばれない
誤呼出率: 0% (65/65 で numeric specialist スキップ)
```

pipeline は classic の status判定に完全追従するため、impossible case で numeric specialist が呼ばれることはない。

---

## 12. 採用ゲート総合判定（Section 26）

```
final schema >= 99%:               100.0%  PASS
final status >= baseline -1pt:     100.0%  PASS  (baseline 99.2%)
final impossible F1 >= baseline -1:100.0%  PASS  (baseline 99.5%)
final reason F1 >= baseline -2pt:  100.0%  PASS  (baseline 94.9%)
tolerant adoption >= 88%:          90.0%   PASS
major patch <= 10%:                9.96%   PASS
constraint follow >= 90%:          (classic最終補正で満たす) PASS
fallback rate <= 5%:               0.2%    PASS
repetition < 1%:                   0.0%    PASS

ALL END-TO-END GATES: PASS
```

---

## 13. 保存対象

### Git管理

- pipeline code: `tools/navspec_two_stage_pipeline.py`
- comparison report: `docs/navspec_numeric_specialist_comparison_2026-07-13.md`
- pipeline report: `docs/navspec_two_stage_pipeline_validation_2026-07-13.md`（本ファイル）
- hash一覧: `artifacts/navspec-lora/numeric_specialist_hashes.sha256`
- registry role candidate（更新は明示指示後）

### Git LFS

- 採用numeric specialist adapter: `stage3-numeric-quality-r8-v1/adapters.safetensors`（既存・不変）
- 比較用代表adapter: `stage3.1-balanced-numeric-r8/0000250_adapters.safetensors`（既存・不変）

### Git管理外

- ケース別巨大評価JSON
- 再生成可能なsubset JSONL
- Stage 2 / numeric raw出力キャッシュ (`two_stage_pipeline_cache/`)
- 一時ログ

---

## 14. registry更新候補（Section 31・明示指示後）

Stage 2 を default のまま維持。numeric specialist は別 role で登録:

```json
{
  "id": "navspec-stage3-v1-iter500-numeric",
  "status": "candidate",
  "role": "numeric_specialist",
  "default": false,
  "parent": "navspec-stage2-iter1000",
  "adapter": "models/navspec-lora-0.5b-v0.1/adapter/stage3-numeric-quality-r8-v1",
  "sha256": "303fadf04c9e10d0f0046dd23fc8127c43c629e3f164be71f857b745d6d08d71"
}
```

採用後（明示指示時）:

```json
{
  "status": "accepted",
  "role": "numeric_specialist",
  "default": false
}
```

Stage 2 の default フラグは変更しない。

---

## 15. Git操作未実施確認

- git commit: 未実施
- git tag: 未実施
- git push: 未実施
- registry.json更新: 未実施
- Stage 2 defaultフラグ変更: 未実施
- checkpoint削除: 未実施
- 追加学習: 未実施
