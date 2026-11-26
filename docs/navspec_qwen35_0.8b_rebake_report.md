# navspec armament verdict: Qwen3.5-0.8B 焼き直し 実験レポート

作成: 2026-10-06 / 計画: [navspec_qwen35_0.8b_rebake_plan.md](navspec_qwen35_0.8b_rebake_plan.md)
生データ（評価 JSON・学習ログ・config・実行スクリプト）: [navspec_qwen35_0.8b_rebake/](navspec_qwen35_0.8b_rebake/)

## 1. 結論

- **Qwen3.5-0.8B では判定を学習できた。** test 142件で status 正解率 1.000、reason code F1 0.903（R2）。
  公開中の v0.1（Qwen2.5-0.5B）は全件 `ok` に崩壊しており 0.430。
- **改善の主因はベースモデルの変更。** 同じ PyTorch 学習器・同じ v0.1 相当設定で
  Qwen2.5-0.5B（C0）は全件 `impossible` に崩壊し、Qwen3.5-0.8B（R0）は 0.986。
  学習環境（MLX→PyTorch）の差ではない。
- 残る誤りは、プロンプトに無い DB 値が要る理由コードに集中している（§4）。
- **公開中の `j-llm/navspec-lora-0.5b-v0.1` の PEFT 重みは壊れている**（§5）。

## 2. 環境

- llm_master RTX 3060 12GB（`CUDA_VISIBLE_DEVICES=GPU-1d6afaad-…` で固定、V100 は不使用）
- `~/navspec-qwen35/.venv`: python 3.12, torch 2.9.1+cu126, transformers 5.18.0, peft 0.21.2,
  flash-linear-attention 0.5.2, causal-conv1d（DeltaNet の fla/conv1d カーネルが実際に呼ばれることを profiler で確認）
- 語彙 248k のため全位置の logits は OOM。completion 位置だけ lm_head に通す自前ループ
  （`tools/navspec_qwen35_verdict_train.py`）。bs4 でピーク 5〜7.5GiB、約 3〜4k tok/s。
- 評価: `tools/navspec_armament_verdict_eval_hf.py`（mlx 版と同じ採点関数、greedy、`enable_thinking=False`）。
  v0.1 を転置修正した PEFT で再評価し、mlx 版の既存結果と全指標一致を確認済み。

## 3. 結果（test 142件、多数派ベースライン = 全件 impossible = 0.570）

| run | JSON | 契約 | status | reason P | reason R | reason F1 | reason集合一致 | 予測status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| v0.1 (Qwen2.5-0.5B, MLX) | 1.000 | 1.000 | 0.430 | — | 0.000 | — | 0.430 | ok 142 |
| Qwen3.5-0.8B ゼロショット | 0.056 | 0.000 | 0.021 | 0.000 | 0.000 | — | 0.000 | ok 8（他は JSON 不正） |
| C0: Qwen2.5-0.5B × R0設定 (PyTorch) | 1.000 | 1.000 | 0.570 | 0.494 | 0.623 | 0.551 | 0.317 | impossible 142 |
| R0: Qwen3.5-0.8B × v0.1相当 | 1.000 | 1.000 | 0.986 | 0.893 | 0.783 | 0.834 | 0.845 | impossible 81 / ok 61 |
| R1: Qwen3.5-0.8B × 設定修正 | 1.000 | 1.000 | **1.000** | 0.927 | 0.826 | 0.874 | 0.866 | impossible 81 / ok 61 |
| **R2: R1 + v2.1データ** | 1.000 | 1.000 | **1.000** | **0.975** | **0.841** | **0.903** | **0.894** | impossible 81 / ok 61 |

設定:
- R0 / C0: 層 8–23, r8, alpha160, dropout 0, lr 5e-6 constant, 500 step × bs4（v0.1 の bs1×GA4×500 と同サンプル数）
- R1 / R2: 全24層, r16, alpha32, dropout 0.05, lr 2e-4 cosine（warmup 5%）, 5 epoch（424 step, bs4×GA2）
- v2.1: v2 の user JSON から teacher 用の `output_schema` / `response_format` を除き、`task` を
  `judge_navspec_armament` に統一（`data/navspec/lora/stage4a_armament_verdict_v2_1/`）。行・分割・正解は v2 と同一
- best checkpoint は valid の status トークン正解率で選択（同点は valid loss）
- train/test 間で入力の完全一致 0 件、艦条件（国・艦種・年・排水量）の重複 0 件
- seed 間のばらつき（R2 を seed 0/1/2 で学習）: status は 3本とも 1.000、JSON 1.000、定義外コード 0。
  reason F1 は 0.903 / 0.882 / 0.867（平均 0.884）、集合一致は 0.894 / 0.880 / 0.859。
  差はほぼ `mount_unavailable_for_era`（TP 14 / 14 / 9）だけで生じる。採用候補は seed 0 の R2 best
- R0・R1・C0 は seed 0 の 1 本のみ

学習時間（3060）: R0 281秒、R1 622秒、R2 542秒、C0 185秒。V100 は使っていない。

## 4. reason code 別（R2）

| reason code | support | TP | FP | FN | 入力だけで判定できるか |
|---|---:|---:|---:|---:|---|
| weapon_mount_incompatible | 36 | 36 | 0 | 0 | ID から推定可 |
| firing_arc_conflict | 36 | 36 | 0 | 0 | positions から可 |
| mount_unavailable_for_nation | 21 | 21 | 0 | 0 | ID の国記号から可 |
| fire_control_unavailable | 9 | 9 | 0 | 0 | ID から可 |
| mount_unavailable_for_era | 18 | 14 | 3 | 4 | 砲塔の就役年が要る（入力に無い） |
| turret_weight_exceeds_hull_limit | 9 | 0 | 0 | 9 | 砲塔重量が要る（入力に無い） |
| armament_displacement_conflict | 9 | 0 | 0 | 9 | 兵装総重量が要る（入力に無い） |

入力から読み取れる理由コードはすべて正解。DB 値を要する 3 コードだけが落ちる。
status は 1.000 なので、test ではこの 3 コードが単独で出るケースは無かった（他の理由と同時に出ている）。
この 3 コードを取るには、判定に要る DB 値を入力に出す（計画の R3）必要がある。

## 5. 公開中 v0.1 PEFT アダプタの不具合

`hf_upload/navspec-lora-0.5b-v0.1/adapter_model.safetensors`（= HF 公開物）は、
MLX の `lora_a (in, r)` / `lora_b (r, out)` を **転置せず reshape** して PEFT の形にしている。
全 224 テンソルが転置と不一致（サブエージェントの全件照合）、うち layers.8 q_proj の A/B は reshape と完全一致・転置と不一致であることを NumPy で再確認した（2026-10-06）。
リポジトリの `convert_mlx_to_peft.py` は転置しているので、アップロード物は別経路で作られたとみられる。

- この PEFT アダプタを読むとベースモデルとほぼ同じ挙動になり、JSON 有効率 0.0（PyTorch 実測）。
- 転置して作り直したもの（llm_master `~/navspec-qwen35/adapters/v0.1_fixed`）は mlx 版の評価と全指標一致。
- `j-llm/qwen25-0.5b-navspec-gguf` はこの PEFT を `merge_and_unload()` したものなので、
  同じく壊れている可能性が高い（GGUF 自体は未検証）。
- ただし正しく直しても v0.1 は全件 `ok` に崩壊したモデルである。

## 6. GGUF

R2 best を `merge_and_unload()` → `convert_hf_to_gguf.py --outtype bf16 --no-mtp`（llama.cpp b29c606）→ `llama-quantize`。
成果物は llm_master `~/navspec-qwen35/gguf/navspec-qwen35-0.8b-{bf16,q8_0,q4_0,q4_k_m}.gguf`。

- マージ版（PyTorch）と非マージ版は test 142件の出力文字列が完全一致。
- `--no-mtp` が必須。付けないと MTP 層込みの `block_count=25` になり、マージ版に `mtp.*` 重みが無いため llama-server が読めない。
- llama-server（chat completions, temperature 0, thinking 無効）で test 142件。プロンプト末尾が
  `<|im_start|>assistant\n<think>\n\n</think>\n\n` になることを `/apply-template` で確認済み。

| 量子化 | サイズ | JSON | status | reason F1 | reason集合一致 | PyTorch と出力完全一致 | 3060 decode t/s（bench tg128） |
|---|---:|---:|---:|---:|---:|---:|---:|
| PyTorch merged | 1.50 GB | 1.000 | 1.000 | 0.903 | 0.894 | — | — |
| bf16 | 1.52 GB | 1.000 | 1.000 | 0.903 | 0.894 | 142/142 | 151 |
| Q8_0 | 812 MB | 1.000 | 1.000 | 0.903 | 0.894 | 142/142 | 250 |
| Q4_0 | 501 MB | 1.000 | 1.000 | 0.910 | 0.908 | 140/142 | 302 |
| Q4_K_M | 529 MB | 1.000 | 0.993 | 0.903 | 0.894 | 140/142 | 272 |

- Q4_0 の差分 2 件は、たまたま正解に近づいた方向（余分な `mount_unavailable_for_era` が消えた）。量子化が優れているという意味ではない。
- Q4_K_M は 1 件（正解 ok）を `impossible` に誤答。
- 公開するなら Q8_0 が既定、Q4_0 は小容量版。
- 旧カードのハード互換表（Kepler / RX 6400 Vulkan / Celeron）は Qwen3.5 では未検証。古い llama.cpp は `qwen35` アーキテクチャを読めない。

## 7. R3: 判定に要る DB 値を入力に注入（データ v3）

- データ: `data/navspec/lora/stage4a_armament_verdict_v3/`（`tools/navspec_lora_data_armament_verdict_v3.py`）。
  行・分割・正解は v2.1 と同一。user JSON に `db_facts` として、参照 ID の生の DB 値を追加した。
  - weapon: 国籍・就役年・互換 mount
  - mount: weapon・国籍・就役年・砲身数・砲塔重量
  - fire_control: 国籍・就役年・radar
  - validator の判定結果や、閾値比較の結果は入れていない。
  - system プロンプトに閾値（単砲塔 15%・合計 50%）を追記した。
- 整合性: 現 DB と validator で全 980 行を再判定し、正解と 980/980 一致。`db_facts` とルール文だけから 3 コードを機械的に再現できることも確認済み（入力情報としては足りている）。
- トークン長は平均 611（v2.1 は 340）。学習 1 本あたり約 915 秒、ピーク 10.2GiB。

| run | status | reason F1 | 集合一致 | era TP/FP/FN | 重量系2コード |
|---|---:|---:|---:|---|---|
| R2 s0 / s1 / s2 | 1.000 / 1.000 / 1.000 | 0.903 / 0.882 / 0.867 | 0.894 / 0.880 / 0.859 | 14/3/4, 14/9/4, 9/7/9 | 0件 |
| R3 s0 / s1 / s2 | 0.993 / 1.000 / 0.993 | 0.879 / 0.876 / 0.861 | 0.873 / 0.873 / 0.845 | 15/10/3, 11/7/7, 7/5/11 | 0件 |

**効果なし。** 重量系 2 コードは 3 seed とも test で 1 件も出さず、train の該当行でも出さない（学習できていない）。
「重量 ÷ 排水量 > 0.15」「重量 × 台数の合計 > 0.5」という数値比較を、0.8B がこのデータ量（該当 40 行前後）で獲得できていないとみられる。
`mount_unavailable_for_era` は seed 間のばらつきが大きく、改善もしていない。
R3 s0/s2 は、R2 では当たっていた status を 1 件落とした。採用候補は R2 s0 のまま。

次の候補（いずれも未実施）:
- 比率などの派生値を入力に出す。「計算結果は入れない」という方針から外れる。
- 重量系の行を増やす。
- この 3 コードは classic validator に任せる（現行方針 `classic_final_authority: true`）。

## 8. 旧公開物の検証（2026-10-06）

- HF の `j-llm/navspec-lora-0.5b-v0.1` の `adapter_model.safetensors` は、手元の壊れたファイルと sha256 が一致（`5e804ab4…`）。
- HF の `j-llm/qwen25-0.5b-navspec-gguf` の Q8_0（sha256 `fb5b9eb6…`、HF 記録と一致）を llama-server で test 142 件評価した。
  JSON 有効率 0.000。リクエストの `response_format` をそのまま書き返す、ベースモデル同然の出力だった。
- 対照として、転置を直した v0.1 を同じ手順で Q8_0 にしたものは、JSON 1.000・status 0.4296・全件 ok で、MLX 版の評価と一致した。
- **旧 GGUF は壊れていると確認できた。**

## 9. HF 公開（2026-10-06 実施、ユーザー承認済み）

- 非公開化: `j-llm/navspec-lora-0.5b-v0.1`、`j-llm/qwen25-0.5b-navspec-gguf`（未ログインのアクセスが 401 になることを確認）。
- 新規公開:
  - [j-llm/navspec-lora-0.8b-v0.2](https://huggingface.co/j-llm/navspec-lora-0.8b-v0.2)（R2 seed0 アダプタ、`adapter_model.safetensors` sha256 `0667311d…`）
  - [j-llm/qwen35-0.8b-navspec-gguf](https://huggingface.co/j-llm/qwen35-0.8b-navspec-gguf)（Q8_0 `b0ece833…` / Q4_0 `3eef288e…`）
- 公開物の sha256 がローカルと一致することを、未ログインの API で確認した。
- README の使用例を、Hub からアダプタを読む形のまま 3060 で実行し、想定どおりの出力を確認した。
- 公開物の元ファイル: `hf_upload/navspec-lora-0.8b-v0.2/`、`hf_upload/qwen35-0.8b-navspec-gguf/README.md`。

## 10. 未完了

- 成果物の NAS 保存（Mac に NAS 未マウント。現在は llm_master `~/navspec-qwen35/` のみ）。
- 残り 3 コードの扱い（§7 の次の候補から選ぶ）。
