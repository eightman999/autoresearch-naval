# autoresearch-naval 実装計画

- 策定日: 2026-09-02
- 状態: Phase 0実施中
- 正典: [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md)

本書は `IMPROVEMENT_PLAN.md` から派生する実行計画である。矛盾時は改善計画を正とする。
チェックボックスは実測でゲートを通過した場合のみ更新する。commit、push、tag、default変更、
履歴書換え、データ削除は、それぞれ明示指示を受けるまで実行しない。

## 0. 実行規則

1. Phaseを飛ばさない。各PhaseのExit Gateが通るまで次へ進まない（改善計画 §4, §8）。
2. 作業開始時に `git status --short --branch` と対象差分を確認する。
3. 1タスクで主要な変更軸を1つに限定する（改善計画 §4）。
4. fast検証は追跡済み生成物を書き換えない。一時出力は専用run directoryまたは一時ディレクトリへ置く。
5. モデル・データ実験は、入力hash、contract hash、adapter hash、seed、実行コマンドを記録する。
6. Stage 2 default、classic validator、既存schema、ID、固定artifactを保護する（改善計画 §4, §6.4）。
7. Phase完了報告には、結論、実行コマンドと出力、変更ファイル、未確認事項を含める。

## Phase 0 — Baseline Recovery

**Goal:** fresh cloneとローカルで同じfast検証が安定して成功する（改善計画 §3.1, §7.1）。

### 0.1 テスト入口の正常化

- [x] `pyproject.toml` にpytestのroot/import設定と `testpaths = ["tests"]` を固定する。
- [x] `verify.sh` のpytest呼び出しを `uv run python -m pytest tests/ -q` に統一する。
- [x] READMEと `program.md` の標準検証コマンドを同じ呼び出しへ揃える。
- [x] `uv run pytest -q` と `uv run python -m pytest -q` の両方で収集エラーがないことを確認する。

対象候補:

- `pyproject.toml`
- `verify.sh`
- `README.md`
- `program.md`

検証:

```bash
./verify.sh --check
./verify.sh
uv run pytest -q
uv run python -m pytest -q
git diff --check
```

### 0.2 fast CI

- [x] `.github/workflows/ci.yml` を追加する。
- [x] pytest、Beautiful Soup、requests等のfast testに必要な最小依存をCI専用groupへ分離する。
- [x] CIではMLXをインストールせず、`./verify.sh --check` とfast testを実行する。
- [x] CIではNSDB、モデルdownload、LoRA推論、full学習を要求しない。
- [x] Apple Silicon固有のMLX full検証はローカルの `./verify.sh --full` に残す。
- [x] fast test後に追跡済みファイル差分がないことを検査する。

### 0.3 リポジトリ移設の仕上げ

- [ ] clone URL、description、repository名の旧参照がないことをgrep確認する。
- [x] 新リポジトリの安定ブランチを `main` にする手順を別途提示する。
- [ ] `main` と現既定ブランチのSHA一致確認後にのみdefault変更を行う。
- [ ] 新リポジトリ側の旧branch削除は明示承認後に限定する。
- [x] 50 MB超JSONLを一覧化し、P0では履歴移行しないことを記録する。

### Phase 0 Exit Gate

- [x] `./verify.sh --check`: PASS。
- [x] `./verify.sh`: 82 tests以上 PASS。
- [x] 2種類のpytest呼び出しがともにPASS。
- [ ] CI: PASS。
- [x] fast検証後の意図しない差分: 0件。

## Phase 1 — Evaluation Foundation

**Goal:** 同じ入力と構成から比較可能なrunを再生成し、前回比の悪化itemを特定できる
（改善計画 §3.1, §7.2）。

### 1.1 統一評価contract

- [x] `data/navspec/lora/contracts/navspec_end_to_end_eval_contract_v1.json` を追加する。
- [x] 既存Stage 2・numeric contractの閾値を参照し、重複定義ではなく参照元を明記する。
- [x] status/reason/warning、schema、numeric、armament、fallback、latencyの軸を定義する。
- [x] 非該当軸は `null`、人手確認は `needs_manual` として扱う。
- [x] goldenケースへ安定IDと `case_group_id` を付与する。

### 1.2 run_eval

- [x] 薄いオーケストレータ `tools/navspec_eval_run.py` を追加する。
- [x] `--config`、`--eval-set`、`--limit`、`--ids`、`--run-dir` を受ける。
- [x] temperature 0、seed固定、入力hash固定を既定にする。
- [x] 出力JSONLの既処理IDをskipし、中断後に再開できるようにする。
- [x] モデル出力cacheをrun directoryへ保存し、再採点で再推論しない。
- [x] run metadataへGit SHA、dirty状態、Python/MLX情報、contract/model/adapter/data hashを残す。

### 1.3 score・summary・diff

- [x] `tools/navspec_eval_score.py` で軸別合否とper-item結果を出す。
- [x] `evaluation/scores/summary.csv` をrun/configキーでupsertする。
- [x] `tools/navspec_eval_diff.py` で改善・悪化・不変をitem単位に出す。
- [x] 人間可読Markdownと機械可読JSONLを同時に生成する。
- [x] 未知reason/warning codeを独立FAIL軸にする。

### 1.4 評価基盤テスト

- [x] 同一引数で評価入力hashが一致するテスト。
- [x] 中断後の再開で重複行が出ないテスト。
- [x] summary upsertが他runを消さないテスト。
- [x] per-item diffが改善・悪化を正しく分類するテスト。
- [x] contract不一致と未知codeがFAILになるテスト。

### Phase 1 Exit Gate

- [x] 10件smoke runが生成→採点→summary→diffまで完走。
- [x] 同一run再実行でモデル呼出しなし、重複なし。
- [x] Stage 2 baselineの基準線を固定し、hash付きで保存。
- [x] `./verify.sh` とPhase 1追加テストがPASS。

## Phase 2 — Hybrid Armament Integration

**Goal:** 既存pipelineへ詳細兵装を加算的に接続し、最終判定を決定的validatorへ統一する
（改善計画 §1, §5, §6.1）。

### 2.1 結果契約

- [x] `schemas/navspec_pipeline_result.schema.json` を追加する。
- [x] `ship_spec` は既存 `ShipSpecRecord` を変更せず参照する。
- [x] `detailed_armament` は既存armament candidate契約を再利用する。
- [x] `validation_trace` にrule、before/after、reason/warning、severity、sourceを定義する。
- [x] `provenance` にDB version/hash、contract hash、model/adapter、seedを定義する。
- [x] schema versionを `navspec.pipeline_result.v1` とする。

### 2.2 armament orchestrator

- [x] `tools/navspec_armament_pipeline.py` を追加する。
- [x] `ShipSpecRecord` からnation/year/ship_type/beam/displacement/主砲条件を安全に写像する。
- [x] retrieverで候補を生成し、validatorで全候補を検査する。
- [x] errorがあれば最終 `impossible`、warning-onlyなら `ok` + warningsとする。
- [x] reason/warningはvalidatorの既知語彙だけを返し、LLMの未知codeを採用しない。
- [x] 候補なし・DB読込失敗・schema不一致のfallbackを明示する。
- [x] 選択根拠、除外候補、出典をtraceへ残す。

### 2.3 二段pipelineへの接続

- [x] `tools/navspec_two_stage_pipeline.py` のstatus/numeric確定後にarmament層を追加する。
- [x] Stage 2がimpossibleならnumeric/armamentを呼ばない。
- [x] armamentによるimpossibleは元のStage 2出力を上書きせず、統合traceで根拠を示す。
- [x] `tools/navspec_equipment.py` の主砲概要と詳細兵装の口径・門数を整合検査する。
- [x] 既存CLI出力との互換モードを維持する。

### 2.4 テスト

- [x] 正常な戦艦・巡洋艦・駆逐艦。
- [x] 早すぎるweapon/mount、他国mount、非互換mount。
- [x] barbette、magazine、topweight、blastのwarning-only。
- [x] DBにない国家・艦種・候補なしのfallback。
- [x] Stage 2 impossible時に下流推論がskipされること。
- [x] equipment概要と詳細兵装の不一致検出。
- [x] pipeline result schemaとprovenanceの必須項目。

### Phase 2 Exit Gate

- [x] representative/goldenケースのstatus/reason/warningがvalidatorと100%一致。
- [x] pipeline result contract準拠率100%。
- [x] 未知code 0件。
- [x] Stage 2 default・classic artifact・既存schemaのhash不変。
- [x] 既存82件とPhase 2追加テストが全てPASS。

## Phase 3 — CLI / GUI End-to-End

**Goal:** 利用者が詳細兵装と判定根拠を実際に確認できる（改善計画 §6.1, §10, §11）。

### 3.1 CLI

- [x] 二段pipelineのCLIに詳細兵装有効化、戦略、trace出力先を追加する。
- [x] 既定出力を `navspec.pipeline_result.v1` にできる明示オプションを用意する。
- [x] 既存classic `generate_navspec.py` はP0で破壊的に置換しない。
- [x] normal、impossible、warning-onlyの再現コマンドをREADMEへ記載する。

### 3.2 GUI

- [x] `tools/navspec_gui.py` を同じpipeline facadeへ接続する。
- [x] 「詳細兵装」タブへweapon/mount/ammunition/fire control、出典、confidenceを表示する。
- [x] warningとerrorを分離し、適用ruleと修正内容を表示する。
- [x] fallback発生時に理由を表示し、成功に見せかけない。
- [x] UI処理中の二重実行を防ぎ、失敗後に再実行できるようにする。

### 3.3 実動作smoke

- [x] normalケースをCLIで生成しschema検証する。
- [x] impossibleケースで下流skipと理由表示を確認する。
- [x] warning-onlyケースで `ok` 維持を確認する。
- [x] GUIを実プロセスで起動し、上記3ケースを最低1回操作する。
- [x] raw JSONと画面表示が一致することを確認する。

### Phase 3 Exit Gate

- [x] CLI/GUIが同じcontractとpipelineを使用。
- [x] 3本のE2Eストーリーが実動作。
- [ ] fallback率1.0%以下。
- [x] 参照環境でp95 5秒以下、または超過理由を実測レポート化。

実測注記: `data/navspec/lora/stage4a_armament_v1/valid.jsonl` 177件では、DB対象外
国家/艦種および意図的な no-candidate を含むため明示fallbackが43/177 (24.2938%)。
詳細兵装 golden（`expected_output.armament` が存在する122件）では、retrieverの
「最大口径だが未就役mount」を代替候補へ切り替える修正後、fallback 0/122 (0%)。
このため全valid集合に対する1%ゲートは未達のまま保持し、対象範囲を変更して合格扱いにはしない。

## Phase 4 — Release Gate

**Goal:** P0成果を再現可能なrelease candidateとして固定する（改善計画 §7.3, §10, §11）。

### 4.1 全件評価

- [x] Stage 2回帰セット、622 semantic、numeric 1000、armament goldenを順に評価する。
- [x] Phase 1の統一run/score/diffを使う。
- [ ] Stage 2 regression: schema -2.0pt以内、status -1.5pt以内、impossible F1 -1.5pt以内。
- [ ] numeric specialistの既存contract全ゲートを維持する。
- [ ] armament status/reason/warningはvalidator一致100%、未知code 0件。
- [x] latencyとfallbackを実測する。

実測: Stage 2 regression 480件、semantic 622件、numeric 1000件、armament golden 122件を
統一run/scoreへ投入した。golden replayの3系統は全軸1.0、armamentはschema 100%、unknown code
0件、fallback 0/122だが、既存teacher fixtureとclassic判定の不一致5件がありstatus/reason/warning
完全一致ゲートは未達。valid全体177件ではfallback 43件 (24.2938%) もあるため、対象を狭めて
P0ゲート合格とは扱わない。詳細は `evaluation/reports/phase4_release_candidate_v1.md`。

### 4.2 成果物

- [x] `models/registry.json` にpipeline構成と採否を加算的に登録する。
- [x] contract、eval summary、per-item diff、hash一覧を保存する。
- [x] READMEへ標準フロー、検証、既知制約を反映する。
- [ ] `STAGE4_PROGRESS.md` の古いbranch/commit待ち記述を現状に合わせる。
- [ ] データ・モデル・ライセンス・出典の注意事項を確認する。

### 4.3 release前確認

```bash
git status --short --branch
git diff --check
./verify.sh
./verify.sh --full
git lfs fsck
```

- [x] `--full` が生成物を書き換える場合は専用run directoryで実行する。
- [ ] fresh cloneでfast検証と代表E2Eを再現する。
- [ ] commit/tag/pushは実測結果提示後、明示指示に従う。

### Phase 4 Exit Gate

- [ ] 改善計画 §10 Acceptance Criteriaを全て満たす。
- [x] 改善計画 §11のユーザーフローを実プロセスで完走する。
- [x] 未検証範囲と既知制約をreleaseレポートに明記する。

## Phase 5 — Data Quality and Shadow ML（P1）

**Goal:** データ漏洩を除去したうえで、armament判定モデルの改善可能性を公平に測る
（改善計画 §3.2, §6.2, §7.4）。

### 5.1 group split

- [ ] nation、era bucket、ship type、constraint signature、weapon/mount/fire-control IDから
  canonical `case_group_id` を生成する。
- [ ] Generation/Critiqueを結合してからgroup単位でtrain/valid/testへ分ける。
- [ ] split間重複0件をテストする。
- [ ] reason/warning分布と未出codeをmanifestへ記録する。
- [ ] 既存v0/v1 datasetは削除・上書きせず、新versionとして生成する。

### 5.2 0.5B実験

次の順に1軸ずつ実験し、各runをbaselineとitem diff比較する。

1. class-balanced sampling。
2. status/reason/warningだけの短い出力契約。
3. 判定トークンへの重み付き損失。
4. full JSON生成とのmulti-task比率調整。

- [ ] 各案を10〜30件smokeで全層通しする。
- [ ] 有望案だけをfull trainする。
- [ ] 3 seedで評価する。
- [ ] 全件 `ok` / `impossible` 固定化をbalanced accuracyで検知する。

### 5.3 モデルサイズ比較

- [ ] 0.5B最良構成を基準線にする。
- [ ] 同一dataset/contractで1.5Bまたは3Bを比較する。
- [ ] 品質、学習時間、推論p95、メモリ、artifact容量を記録する。
- [ ] 7Bは3Bで改善不足かつ運用予算内の場合のみP2で実施する。

### Phase 5 Exit Gate

- [ ] splitリーク0件。
- [ ] 改善計画 §7.4のshadow昇格候補ゲートを全て満たす、または不採用を記録する。
- [ ] P0 pipelineのvalidator最終権限を維持する。

## Phase 6 — Scale and Operations（P1/P2）

**Goal:** 正確性を維持したまま、データ範囲、性能、保存・配布を改善する（改善計画 §6.2, §6.3）。

### 6.1 Armament DB

- [ ] mount年未決定4件を一次情報または人手レビューで扱う。
- [ ] 艦種・年代・国家・口径帯のcoverage matrixを作る。
- [ ] 追加対象は不足セルから選び、件数目標を先に固定する。
- [ ] 新規値にsource、source_type、verified、confidenceを必須化する。

### 6.2 性能

- [ ] 入力hashによる推論cache。
- [ ] numeric/armament promptの短縮。
- [ ] temperature 0のbatch推論。
- [ ] 各変更を同一1000件で品質・p50/p95・メモリ比較する。

### 6.3 保存・配布

- [ ] Git管理、Git LFS、release artifact、ローカル共有データ正本の境界を文書化する。
- [ ] 50 MB超JSONLの今後の追加をCIで検出する。
- [ ] 既存履歴の移行案はbackup、fresh clone、`git fsck`、`git lfs fsck` を含む別計画にする。
- [ ] 履歴書換え・削除は明示承認なしに実施しない。

### Phase 6 Exit Gate

- [ ] coverage、performance、storageの各変更が独立runで評価済み。
- [ ] P0の全回帰ゲートを維持する。
- [ ] 運用手順を第三者がfresh cloneから再現できる。

## 完了報告テンプレート

各Phase完了時は次を記録する。

1. 結論とExit Gateの合否。
2. 実行したコマンドと要約出力。
3. 変更ファイル一覧。
4. baselineとの差分と悪化item。
5. モデル推論・GUI・full評価など未確認の範囲。
6. 次Phaseへ進めるか、修正が必要か。
