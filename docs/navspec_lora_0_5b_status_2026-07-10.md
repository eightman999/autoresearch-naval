# navspec 0.5B LoRA 準備状況

作成日: 2026-07-10

## 完了

- `artifacts/navspec-classic-v1/` に現行classic一式を保存。
- `schemas/navspec_lora_candidate.schema.json` でLoRA raw出力用schemaを固定。
- `tools/navspec_lora_data.py` を追加し、schema-only SFTデータとraw-baseline用500件ケースを生成可能にした。
- `data/navspec/lora/raw_baseline_122_plus_500/` に現行122件 + raw専用500件のbaseline入力を生成。
- `data/navspec/lora/schema_only_v0/` にschema-only 5,000件を生成。
- `models/navspec-lora-0.5b-v0.1/` にLoRA設定・chat template・dataset manifestを追加。
- `models/registry.json` にclassicとLoRA候補を登録。
- base modelを `Qwen/Qwen2.5-0.5B-Instruct` に固定。
- Qwen raw評価器 [tools/qwen_lora_eval.py](/Users/eightman/dev/apps/autoresearch-naval/tools/qwen_lora_eval.py) を追加。
- `mlx-lm` / `transformers 5.x` 互換用の限定パッチ [tools/mlx_lm_compat.py](/Users/eightman/dev/apps/autoresearch-naval/tools/mlx_lm_compat.py) と訓練wrapper [tools/qwen_lora_train.py](/Users/eightman/dev/apps/autoresearch-naval/tools/qwen_lora_train.py) を追加。
- `mlx-lm==0.31.3` を依存に追加し、wrapper経由のCLI起動を確認。
- Qwen2.5-0.5B-Instructを取得し、1件のゼロショットsmokeを実行。
- zero-shot smokeではraw JSONは出たが、schema完全一致は0%。入力echoまたは途中終了が発生したため、評価器に `--json-prefill schema` を追加。
- schema_only SFT のため `--mask-prompt`（応答のみロス）を有効化。`qwen_lora_train.py` は schema_only データ学習時に未指定なら自動で `--mask-prompt` を補強し、`NAVSPEC_NO_MASK_PROMPT=1` で opt-out できる。
- 評価レポートを `--json-prefill` モード別(free_raw / object_prefill / schema_assisted)に分離。未指定時は `data/navspec/lora/eval/{run_slug}/{eval_mode}/report.json` と `docs/{run_slug}_{eval_mode}_{date}.md` に自動配置される。
- chat template / EOS / mask_prompt offset の token ID 整合性を検証する standalone テスト [tools/test_navspec_lora_tokenizer.py](/Users/eightman/dev/apps/autoresearch-naval/tools/test_navspec_lora_tokenizer.py) を追加。実トークナイザで 5/5 PASS（offset=15、schema_only_v0 実訓練行で offset=322 を確認）。pytest 依存は未追加で、check_* 純粋関数構造により後から pytest へ移植可能。

## 方針

- classicは削除・上書きしない。
- LoRAは `navspec.lora_candidate.v1` の短いJSON候補を生成する。
- 最終的な数値保証、矛盾判定、条約・年代・nation tier制限はclassic rule engine側で維持する。
- Stage 1の採用判定は `raw_json_rate` と `schema_rate` を優先し、val_bpb単独では判断しない。
- Qwen 0.5Bのゼロショットは現時点でschema安定性が低く、Stage 1 schema-only LoRAを優先して改善する。

## Qwen 実行コマンド

ゼロショット評価（schema-assisted / free-raw をモード別レポートに分離）:

```bash
# schema プライミングあり
uv run python tools/qwen_lora_eval.py \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --cases data/navspec/lora/raw_baseline_122_plus_500/cases.jsonl \
  --json-prefill schema
# -> eval/qwen2_5_0_5b_zero_shot/schema_assisted/report.json

# prefill なし（モデルの素の JSON 生成力）
uv run python tools/qwen_lora_eval.py \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --cases data/navspec/lora/raw_baseline_122_plus_500/cases.jsonl \
  --json-prefill none
# -> eval/qwen2_5_0_5b_zero_shot/free_raw/report.json
```

Stage 1 schema-only LoRA（`--mask-prompt` は応答のみロス。wrapper が schema_only データ学習時に未指定なら自動補強）:

```bash
uv run python tools/qwen_lora_train.py \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  --train \
  --data data/navspec/lora/schema_only_v0 \
  --adapter-path models/navspec-lora-0.5b-v0.1/adapter/stage1-r8 \
  --iters 1000 \
  --batch-size 1 \
  --num-layers 16 \
  --learning-rate 0.0001 \
  --mask-prompt
```

tokenizer 契約テスト（chat template / EOS / mask_prompt offset）:

```bash
uv run python tools/test_navspec_lora_tokenizer.py
```

## 未実行

- git commit/tag/branch作成は未実行。現ワークツリーがdirtyで、明示指示なしに履歴操作しないため。
- `--json-prefill schema` 導入後の再smokeは未実行。権限付き実行の利用上限に到達したため。
- 622件フルゼロショット評価は未実行。
- Stage 1 LoRA学習は未実行。
