# navspec armament verdict: Qwen3.5-0.8B での焼き直し計画

作成: 2026-10-06 / 状態: Phase 0〜5 完了。Phase 6（NAS 保存・HF 公開）は未実施。結果は [navspec_qwen35_0.8b_rebake_report.md](navspec_qwen35_0.8b_rebake_report.md)

## 0. 目的と前提

- 対象: HF 公開中の `j-llm/navspec-lora-0.5b-v0.1`（LoRA）と
  `j-llm/qwen25-0.5b-navspec-gguf`（マージ済み GGUF）を、ベース `Qwen/Qwen3.5-0.8B` で作り直す。
- 実行場所: llm_master の RTX 3060（12GB）。V100 で動いている `j72-4e-top1` の学習には一切触れない。
- 公開済みの 2 リポジトリは上書きしない。新リポジトリとして出す（公開は別途承認）。

### 0.1 公開中 v0.1 の実体（ローカル調査結果）

`hf_upload/navspec-lora-0.5b-v0.1/mlx_adapter_config.json` から特定した。

| 項目 | 値 |
|---|---|
| アダプタ | `models/navspec-lora-0.5b-v0.1/adapter/stage4a-armament-verdict-r8-v2`（MLX→PEFT 変換） |
| データ | `data/navspec/lora/stage4a_armament_verdict_v2`（train 679 / valid 159 / test 142、messages 形式） |
| 出力契約 | `navspec.armament_verdict.v1`（`schema_version, status, reason_codes` の短い JSON） |
| 学習 | mlx_lm, r=8, scale=20（PEFT 換算 alpha=160）, 上位16層, lr 5e-6, 500 iter, bs1×GA4, mask-prompt |
| 評価（test 142） | JSON/契約 1.0、**status 正解率 0.4296、全件 `ok` に崩壊**、reason recall 0 |

test の多数派は `impossible`（81/142 = 0.570）。つまり v0.1 は多数派ベースラインを下回る。
「同じレシピを 0.8B に載せ替えるだけ」では同じ崩壊を再現する可能性が高いので、
**忠実再現（R0）とレシピ修正（R1〜）を分けて走らせる**。

### 0.2 崩壊の仮説

1. lr 5e-6 が LoRA には低すぎる（一般的には 1e-4〜2e-4）。val loss 0.078 は
   約30トークンの定型 JSON がほぼ当たれば出る値で、status 1トークンの正誤は平均損失にほとんど効かない。
   → **平均 loss ではなく、valid の status トークン正解率を学習中に見る**。
2. 入力だけでは判定できない理由がある。`mount_unavailable_for_era`（砲塔の就役年）、
   `turret_weight_exceeds_hull_limit`（砲塔重量）、`weapon_mount_incompatible`（互換表）は
   DB の値がプロンプトに入っていない。679件で DB を暗記させるのは無理筋。
3. user プロンプトに `output_schema: navspec.armament_teacher.v1` と teacher 用の
   `response_format` が残っており、system プロンプトの verdict 契約と矛盾している。

## 1. Qwen3.5-0.8B の注意点（HF config で確認済み）

- `Qwen3_5ForConditionalGeneration`（画像・動画入力つき）。テキストのみで読む（vision tower は学習・マージ対象外）。
- 24層中18層が Gated DeltaNet（linear attention）、6層が full attention のハイブリッド。
  LoRA の target module 名が Qwen2.5 と違う（linear 層は `in_proj_*` / `out_proj` 系）。
  名前は実際の state_dict から確定する。推測で書かない。
- 高速経路には `flash-linear-attention` と `causal-conv1d` が要る。無ければ torch fallback（遅いが入力約500トークンなので致命的ではない）。
- vocab 248,320・tied embedding。logits のメモリが大きいので、バッチは小さく、必要なら fused CE（Liger）を使う。
- chat template に thinking がある。学習・評価・GGUF 推論すべて **non-thinking（`enable_thinking=False`）で揃える**。
- 旧 GGUF カードのハード互換表（Kepler / GT730 / RX 6400 Vulkan）は Qwen2.5 前提。
  Qwen3.5 は新しい llama.cpp と DeltaNet 系 op が必要なので、**互換表は全部取り直し**になる（古い環境では動かない可能性が高い）。

## 2. フェーズ

### Phase 0: llm_master の事前確認（読み取りのみ）

- `nvidia-smi -L` と `CUDA_DEVICE_ORDER=PCI_BUS_ID` で 3060 の index/UUID を確定。以降は UUID で `CUDA_VISIBLE_DEVICES` を固定。
- j72-4e-top1 がどの GPU・何コア・どのディスクを使っているかを見る（プロセスには触れない）。
- 3060 の空き VRAM、ホスト RAM、ディスク空き、CUDA ドライバ版、既存の Python/uv 環境。
- 完了条件: 3060 だけが見える状態で `torch.cuda.get_device_name()` が RTX 3060 を返す。

### Phase 1: 環境構築（llm_master、新規 venv）

- 専用ディレクトリ（例 `~/navspec-qwen35/`）に uv venv。既存の j72 環境は共有しない。
- torch（CUDA 12.x, sm_86）, `transformers`（qwen3_5 対応版）, `peft`, `trl`, `accelerate`,
  `flash-linear-attention`, `causal-conv1d`, 任意で `liger-kernel`。
- データ（verdict_v2, 約1.5MB）と評価に要る `tools/` の一部をコピー。
- スモーク: Qwen3.5-0.8B をテキストのみで読み、bf16 で forward/backward 1 step。
  ピーク VRAM と tokens/s を記録し、fla の高速経路が効いているか確認。
- 完了条件: 1 step が通り、ピーク VRAM が 10GB 未満。

### Phase 2: PyTorch 評価器とベースライン

- `tools/navspec_armament_verdict_eval.py` は mlx_lm 専用なので、同じ採点ロジック
  （JSON 抽出・契約検証・status/reason 集計）を再利用する transformers 版バックエンドを追加する
  （`--backend {mlx,hf}` か別ファイル。採点関数は共通化）。
- 指標を追加: 理由コード別の recall、status の混同行列。
- ベースライン（test 142）:
  - 多数派（全件 impossible）: 0.570
  - Qwen3.5-0.8B ゼロショット（non-thinking, greedy）
  - 既存 v0.1 アダプタを PyTorch で再評価し、MLX の結果（0.4296）と一致するか（評価器の検証を兼ねる）

### Phase 3: 学習（3060、TRL SFT + PEFT LoRA）

共通: assistant 部分のみで損失、greedy 評価、seed 固定、各 run の config・ログ・評価 JSON を保存。
1 run は数分〜数十分の見込み（Phase 1 の実測で更新する）。

| run | データ | 設定 | ねらい |
|---|---|---|---|
| R0 | verdict_v2 そのまま | v0.1 相当: r8, alpha160, lr 5e-6, 有効 2000 samples（約3 epoch）, 上位16層相当 | 忠実な焼き直し。崩壊が再現するか |
| R1 | verdict_v2 そのまま | r16, alpha32, lr 1e-4〜2e-4, 全層, 3〜5 epoch, cosine | レシピだけで判定が学習できるか |
| R2 | v2 + プロンプト矛盾の除去（v2.1） | R1 の良い方 | 仮説3の効果 |
| R3（要判断） | v3: 判定に要る DB 値（砲塔就役年・重量・互換武装）を入力へ注入 | R1 の良い方 | 仮説2。classic validator が持つ情報を入力に出す |

- 学習中は valid の status 正解率で early stopping / ベスト checkpoint 選択。
- R3 はタスク定義（入力スキーマ）が変わるので、着手前に確認する。

### Phase 4: 採用判定

test 142 で以下をすべて満たしたものを v0.2 候補にする（閾値は Phase 2 のゼロショット値を見て調整可）。

- JSON / 契約遵守 ≥ 0.98
- status 正解率 ≥ 0.75（多数派 0.570 を明確に上回る）
- 予測 status が片側に崩壊していない（両クラスとも recall ≥ 0.5）
- 定義外 reason code 0
- 満たさなければ「焼き直したが判定は未獲得」として結果だけ記録し、公開はしない。

### Phase 5: マージと GGUF 化

- `merge_and_unload()` でテキストモデルにマージし、safetensors で保存。
- 最新 llama.cpp の `convert_hf_to_gguf.py` で変換（Qwen3.5 のテキスト部分を扱えるか最初に確認）→ `llama-quantize` で Q8_0 / Q4_0（必要なら Q4_K_M）。
- 同等性検証: GGUF（`llama-server`, non-thinking, temp 0）で test 142 を再評価し、PyTorch マージ版との status 一致率と各指標の差を記録。
- 速度は llm_master と手元 Mac で実測。旧カードのハード表は「未検証」とする。

### Phase 6: 成果物の保存と公開（公開は承認後）

- 学習成果物（アダプタ、マージ済み、GGUF、ログ）: llm_master → NAS `/Volumes/Share/OTHER/navspec/qwen35-0.8b/`。
- リポジトリに残すもの: 学習・評価スクリプト、config、評価 JSON、レポート `docs/navspec_qwen35_0.8b_rebake_report.md`、`models/registry.json` への登録（status は experimental から）。
- HF 公開案: `j-llm/navspec-lora-0.8b-v0.2`, `j-llm/qwen35-0.8b-navspec-gguf`（新規）。
  README に実測値・ベースライン・崩壊の経緯・ハード互換の未検証範囲を書く。
  **アップロードはユーザーの明示承認後**。既存 2 リポジトリは触らない。

## 3. リスクと対処

| リスク | 対処 |
|---|---|
| j72 学習への干渉（CPU・RAM・ディスク IO） | GPU を UUID で固定、dataloader worker を少なく、`nice`。Phase 0 で j72 の資源使用を確認 |
| fla / causal-conv1d が入らない・ビルドが通らない | torch fallback で続行（短い入力なので許容）。速度だけ記録 |
| transformers の qwen3_5 実装と PEFT の相性 | target module を state_dict から列挙して明示。スモークで LoRA の勾配が 0 でないことを確認 |
| llama.cpp が LoRA マージ後の Qwen3.5 を変換できない | 変換可否を Phase 1 で素のモデルで先に確認しておく |
| 0.8B でも判定が学習できない | R3（DB 値の注入）か、classic validator を最終判定にする既存方針（`classic_final_authority: true`）を維持 |

## 4. 未決事項（ユーザー判断）

1. R3（入力に DB 値を注入するデータ v3）まで進めてよいか。タスク定義が変わる。
2. 公開時のリポジトリ名とバージョン表記（案: v0.2）。
3. Phase 0〜1 の llm_master 操作は SSH が必要（今回の計画作成では SSH していない）。
