# autoresearch-naval 改善計画

- 策定日: 2026-09-02
- 状態: P0実装済み・release gate未達
- 対象: NavSpec の生成、検証、詳細兵装、評価、運用基盤
- 派生計画: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

本書を今後の改善判断における正典とする。`IMPLEMENTATION_PLAN.md` と本書が矛盾した場合は
本書を正とする。既存データ契約については `schemas/`、
`data/navspec/lora/contracts/`、`models/registry.json` を引き続き正とし、本書はそれらを暗黙に
書き換えない。

## §1. 目標

史実データと明示的なルールを根拠に、架空艦の仕様と詳細兵装を再現可能な形で生成し、
「なぜ採用可能か／不可能か」を追跡できる実用パイプラインにする。

P0 の成果は、既存の Stage 2、numeric specialist、classic validator を保護したまま、詳細兵装の
retriever と validator を実際の CLI・GUI フローへ接続したハイブリッド生成器である。LLM は候補生成や
説明を担当し、`status`、reason code、warning code の最終判定は決定的ルールエンジンが担当する。

## §2. 2026-09-02 時点の基準線

### §2.1 動作・運用

- 新リポジトリは `eightman999/autoresearch-naval`。既定ブランチは
  `navspec-armament-stage4`。
- `uv run python -m pytest -q` は **82 passed**。
- `uv run pytest -q` と `./verify.sh` は `tools` の import 解決に失敗し、テスト収集段階で停止する。
- GitHub Actions は未整備。ローカル以外での継続的な回帰検知はない。
- Stage 2 default は `navspec-qwen2.5-0.5b-stage2-r8`。変更しない保護対象である。
- 二段 pipeline は既存レポート上、1000件評価で全ゲートを通過し、実測遅延は平均 3.483 秒、
  fallback 率は 0.2% である。

### §2.2 詳細兵装

- Armament DB v1: weapons 71件、mounts 71件、ammunition 71件、fire control 17件、11か国。
- mount の導入年は4件が未決定であり、推測値で埋めない。
- retriever、validator、teacher/critique データ生成、LoRA 学習、評価ツールは実装済み。
- 詳細兵装は `generate_navspec.py`、`tools/navspec_two_stage_pipeline.py`、
  `tools/navspec_gui.py` の実利用フローへ未接続である。

### §2.3 Stage 4a 学習結果

`stage4a-armament-r8-v1` の test 299件実測:

| 指標 | 実測値 |
| --- | ---: |
| JSON valid | 0.9599 |
| contract OK | 0.9498 |
| status accuracy | 0.5552 |
| 多数派ベースライン | 約0.5452 |
| reason F1 | 0.2331 |
| warning F1 | 0.0444 |

Verdict v1/v2 は test 142件を全件 `ok` と予測し、status accuracy 0.4296、reason F1 0.0 だった。
したがって現行 0.5B LoRA は形式生成には使えても、ルール判定の最終権限にはできない。

### §2.4 リポジトリ資産

- Git LFS 管理対象は safetensors/bin を中心に63件。
- 50 MBを超える JSONL が通常Gitオブジェクトとして複数存在し、新リポジトリへのpush時に
  GitHubから警告を受けている。
- データ、評価結果、実験レポートが複数ディレクトリへ分散し、同じ実験の再開・比較方法が
  一意ではない。

## §3. 解決すべき問題

### §3.1 P0: 信頼できる実利用経路

1. 標準検証コマンドが失敗し、`verify.sh` が品質ゲートとして機能していない。
2. 詳細兵装の実装が実際の生成器・GUIへ届いていない。
3. LLM出力と決定的ルールの責任境界が成果物ごとに異なる。
4. 評価実行、採点、履歴、前回比diffが統一されていない。
5. CIがなく、clone直後の再現性が継続検証されていない。

### §3.2 P1: データと研究の品質

1. Generation と Critique の同一条件が別splitへ入る可能性があり、リークを機械検出できない。
2. reason/warning の分布が偏り、warning-only 4種の学習・評価が特に弱い。
3. accuracyだけではクラス崩壊を検知しにくく、macro/balanced 指標が不足している。
4. mount導入年4件、艦種別口径ルール、国・年代カバレッジに未確定・不足がある。
5. 大容量生成データの保存方針が明文化されていない。

### §3.3 P2: スケールと配布

1. 3B/7B級モデル、batch推論、キャッシュの費用対効果が未測定。
2. GUIは研究用で、評価trace、出典、詳細兵装を十分に表示できない。
3. 再現可能なrelease artifactとバージョン規則が不足している。

## §4. 判断原則

1. **決定的ルールを最終権限にする。** LLMの判定を無検証で採用しない。
2. **既存defaultを保護する。** Stage 2 default、classic validator、既存schema、固定artifactを
   昇格ゲート通過前に変更しない。
3. **評価を先に固定する。** データ・モデル・promptを変える前に同一条件の基準線を残す。
4. **スモークを先に通す。** データ準備→生成/学習→評価→成果物登録を最小件数で一周させる。
5. **loss低下を採用根拠にしない。** 多軸評価と前回比で回帰がないことを必須にする。
6. **推測値を正本へ入れない。** 不明年・不明IDは未決定のまま、出典とconfidenceを記録する。
7. **破壊的整理を改善と混同しない。** 履歴書換え、データ削除、既存ID変更は別承認とする。
8. **変更を一軸に限定する。** 1実験で同時に変える主要因は原則1つとする。

判断に迷う場合の優先順位は、正しい判定、再現性、既存回帰なし、利用可能性、速度、モデルの
新しさの順とする。

## §5. 目標アーキテクチャ

```text
request
  -> Stage 2 status/reason candidate
  -> classic status/reason validator（最終権限）
      -> impossible: 理由・修正案を返す
      -> ok:
          -> numeric specialist
          -> classic numeric patch/validator
          -> deterministic armament retriever
          -> classic armament validator（最終権限）
          -> pipeline result + validation trace + provenance
  -> CLI / GUI
```

P0ではarmament LoRAを本番判定経路へ置かない。P1のモデル実験はshadow modeで決定的validatorとの
一致率を測り、合格してもvalidatorを迂回しない。

既存 `ShipSpecRecord` を破壊的に拡張せず、P0では次の加算的な結果契約を新設する。

- `ship_spec`: 既存 `ShipSpecRecord`
- `detailed_armament`: 既存 armament candidate 契約に準拠
- `validation_trace`: 適用ルール、patch、reason/warning、出典
- `provenance`: DB/contract/model/adapter/seed の識別子とhash

## §6. スコープ

### §6.1 P0 — 実用パイプライン

- [ ] 標準検証コマンドと `verify.sh` を正常化する。
- [ ] fast CIを追加し、モデル取得なしで全ユニットテストを通す。
- [ ] 決定的入力、再開、採点、summary upsert、item diffを持つ統一評価基盤を作る。
- [ ] 詳細兵装retriever/validatorを二段pipelineへ接続する。
- [ ] 結果契約とvalidation traceを追加する。
- [ ] CLIとGUIから詳細兵装付き生成を一度以上実動作確認する。
- [ ] Stage 2・numeric・classic・armamentの回帰ゲートを全て通す。
- [ ] README、registry、評価レポートに再現手順と採否を記録する。

### §6.2 P1 — データ品質とshadow研究

- [ ] `case_group_id` によるgroup splitとリーク検査を導入する。
- [ ] reason/warning別の層化、過少クラス補強、warning専用評価を整備する。
- [ ] 0.5Bでclass-balanced sampling、短いverdict出力、重み付き損失を一軸ずつ比較する。
- [ ] 0.5Bで明確な改善が出た後に1.5B/3Bを同一contractで比較する。
- [ ] DBの未決定4件と不足カテゴリを、一次情報または人手レビューで更新する。
- [ ] 推論cache、短縮prompt、batchの順に遅延を改善する。
- [ ] 大容量JSONLの保存・配布・LFS移行方針を決定する。

### §6.3 P2 — 拡張

- [ ] 7B級モデルは3Bまでの費用対効果が不足する場合のみ比較する。
- [ ] 艦種、年代、国家、兵装カテゴリを拡張する。
- [ ] release artifact、モデルカード、データカードを自動生成する。
- [ ] GUIで候補比較、出典参照、trace差分を扱えるようにする。

### §6.4 P0に含めないもの

- LLMによるvalidatorの置換。
- Stage 2 defaultの変更。
- 未確定の史実値を推測で補完すること。
- 既存データ・checkpoint・タグの削除。
- Git履歴の書換えや大容量ファイルの一括移行。
- 3B/7Bの本学習。

## §7. 評価契約とPhaseゲート

### §7.1 基盤ゲート

- `./verify.sh --check` が成功する。
- `./verify.sh` が収集エラーなしで82件以上を通過する。
- fast test実行後に追跡ファイルの意図しない差分がない。
- CIはmodel download、NSDB、秘密情報なしで成功する。

### §7.2 データゲート

- 同じseed・引数・入力hashから同じ評価入力を再生成できる。
- train/valid/test間の `case_group_id` 重複が0件。
- manifestにgenerator version、seed、source hash、split件数、code分布を記録する。
- 未知reason/warning codeを生成データへ混入させない。

### §7.3 P0ハイブリッドpipelineゲート

- pipeline result schema/contract準拠率: 100%。
- 最終status/reason/warningと決定的validatorの一致率: 100%。
- 未知reason/warning code率: 0%。
- Stage 2回帰: schema低下2.0pt以内、status低下1.5pt以内、impossible F1低下1.5pt以内。
- numeric specialist: 既存contractの全採用ゲートを維持する。
- armament representative/golden cases: 全件で参照整合性とvalidator合格。
- fallback率: 1.0%以下。
- 参照Apple Silicon環境でのpipeline p95: 5秒以下を目標値とする。
- GUIで normal、impossible、warning-only を各1件実操作し、表示とtraceを確認する。

### §7.4 P1 armament modelの昇格候補ゲート

shadow評価で次を全て満たすまで本番候補としない。

- JSON valid / contract OK: 0.99以上。
- status balanced accuracy: 0.80以上。
- reason micro F1: 0.70以上。
- warning micro F1: 0.50以上。
- 未知code率: 0%。
- 3 seedの平均で合格し、既定pipelineの各指標に回帰がない。
- item単位diffをレビューし、既知カテゴリだけに改善が偏っていない。

## §8. Build Order

1. **Phase 0 — Baseline Recovery:** 標準検証とCIを緑にする。
2. **Phase 1 — Evaluation Foundation:** 決定的評価、summary、diffを固定する。
3. **Phase 2 — Hybrid Armament Integration:** 詳細兵装を決定的経路で接続する。
4. **Phase 3 — CLI/GUI End-to-End:** 利用者フローとtrace表示を完成させる。
5. **Phase 4 — Release Gate:** 全評価、文書、registryを整合させる。
6. **Phase 5 — Data and Shadow ML:** group split後にモデル改善を再開する。
7. **Phase 6 — Scale and Operations:** DB、性能、保存・配布を改善する。

各Phaseは対応するスモークとゲートが通るまで次へ進めない。詳細タスクは
`IMPLEMENTATION_PLAN.md` に定義する。

## §9. リスクと対策

| リスク | 対策 |
| --- | --- |
| 既存Stage 2を壊す | default不変テスト、hash確認、回帰ゲートを先行実行 |
| validatorとモデルの判定が競合 | validatorを最終権限とし、モデル値はtraceへ残す |
| データ漏洩で指標が過大になる | canonical `case_group_id` とsplit重複検査 |
| 生成物で作業ツリーが汚れる | fast testは一時ディレクトリ、full evalはrun directoryへ出力 |
| 大容量データ移行で履歴を壊す | P0では移行せず、別計画・backup・明示承認を必須にする |
| 大型モデルで時間だけ消費する | 0.5B→1.5B/3B→7Bの順に同一contractで比較 |
| 史実データの誤補完 | null維持、source/confidence/verifiedを必須化 |

## §10. Acceptance Criteria

### 機能

- [ ] 同一requestから `ship_spec`、`detailed_armament`、`validation_trace` を返せる。
- [ ] impossible時はnumeric/armamentの不要な推論を行わない。
- [ ] warning-onlyケースを `ok` のまま警告付きで返せる。
- [ ] CLIとGUIが同じpipeline契約を使う。

### 品質

- [ ] §7.1〜§7.3の全ゲートが実測で合格する。
- [ ] 既存default、schema、固定artifactのhashが意図なく変わらない。
- [ ] 評価runに入力・コード・モデル・adapter・contractのhashが残る。
- [ ] 前回比の改善・悪化itemを機械的に列挙できる。

### 運用

- [ ] fresh cloneからREADME記載手順でfast検証を再現できる。
- [ ] CIとローカルの標準テストコマンドが一致する。
- [ ] 生成物、LFS、Git管理外データの境界が文書化されている。
- [ ] release判定に実行コマンドと実出力を添付できる。

## §11. Definition of Done

利用者がCLIまたはGUIへ国家・年代・艦種・制約を入力し、Stage 2判定、classic検証、数値候補、
数値補正、詳細兵装検索、兵装検証を経て、出典とtrace付きの結果を得られる。矛盾入力では理由と
修正案、成立入力では整合する詳細兵装、warning-only入力では成立状態を維持した警告が表示される。
この一連のフローを実プロセスで確認し、全自動ゲートと前回比回帰検査が合格した状態をP0完了とする。

モデルがvalidatorを単独で再現すること、3B/7B学習、大容量履歴の整理はP0完了条件に含めない。

## §12. 最終優先順位

1. `verify.sh` とCIの正常化。
2. 統一評価contractと基準線の固定。
3. 決定的な詳細兵装統合。
4. CLI/GUIの実動作。
5. 全回帰ゲートとrelease証跡。
6. データ分割改善。
7. shadow ML改善。
8. 大型モデル、性能、配布最適化。
