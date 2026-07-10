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

## 方針

- classicは削除・上書きしない。
- LoRAは `navspec.lora_candidate.v1` の短いJSON候補を生成する。
- 最終的な数値保証、矛盾判定、条約・年代・nation tier制限はclassic rule engine側で維持する。
- Stage 1の採用判定は `raw_json_rate` と `schema_rate` を優先し、val_bpb単独では判断しない。

## 未実行

- git commit/tag/branch作成は未実行。現ワークツリーがdirtyで、明示指示なしに履歴操作しないため。
- 0.5B級base modelは未選定。候補モデルのローカル配置または利用可否確認後にゼロショット比較を行う。
- Stage 1 LoRA学習は未実行。base model選定とMLX学習コマンド確定後に実行する。
