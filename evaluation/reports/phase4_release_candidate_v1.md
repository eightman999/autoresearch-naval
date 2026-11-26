# Phase 4 release candidate v1 実測レポート

- 実施日: 2026-09-02 (JST)
- 対象ブランチ: `navspec-armament-stage4`
- 判定: **P0 release gate 未達。既定モデルを変更せず、candidate のまま保持する。**

実装したP0成果物は、既存のStage 2 default・classic validator・既存schema・固定artifactを
変更せずに、統一評価、pipeline result、詳細兵装retriever/validator、CLI/GUI経路へ接続した。
ただし、全ゲートを通過したとは扱わない。特に、モデル依存のfull検証、CI実行、armament valid集合の
fallback率、既存teacher fixtureとclassic判定の不一致が未解決である。

## 統一評価結果

実行結果は `evaluation/scores/summary.csv`、per-item結果とmetadataは
`evaluation/runs/release_candidate_20260902/` に保存した。golden runnerの結果は評価基盤の
決定性を確認する固定点であり、モデル推論品質を意味しない。

Stage 2 regressionの同一条件baseline/candidate diffは480件すべて `unchanged`、`regressed=0`、
`improved=0` だった（`stage2_regression_diff/diff.json`）。

| run | 件数 | schema | status accuracy | impossible F1 | reason F1 | warning F1 | fallback | unknown code | 判定 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Stage 2 regression | 480 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | n/a | 0 | PASS |
| Stage 2 semantic | 622 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | n/a | 0 | PASS |
| numeric | 1000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | n/a | 0 | PASS |
| armament golden (expected armamentあり) | 122 | 1.0000 | 0.9672 | 0.0000 | 0.0000 | 0.9697 | 0/122 | 0 | FAIL |
| armament valid全体 | 177 | 1.0000 | 0.7345 | 0.3380 | 0.0000 | 0.9697 | 43/177 | 0 | FAIL |

armament goldenの5件の不一致は、retrieverの参照不整合ではない。4件は入力のheavy cruiserと
1930年代・1万t超条件に対してclassicが `treaty_limit_exceeded` を返し、Stage 2の最終権限に
従って下流をskipした。残り1件はvalidatorが `armament.barbette_too_large_for_beam` をwarningと
判定した。既存teacher出力を変更して合格扱いにはしていない。

valid全体のfallback 43件は、DB対象外国家11件、DB対象外艦種8件、候補なし24件である。
これは明示的なfallbackであり、成功に偽装していない。1%ゲートは未達である。

## 実プロセス確認

- `uv run python tools/navspec_gui.py --smoke all`: normal / impossible / warning の3件が契約検証を通過。
- `uv run python tools/navspec_gui.py --smoke-ui all`: Tkウィジェット生成、入力、生成、タブ表示、raw JSONとの一致を3件とも確認。
- standalone armament CLI、二段CLIの `--with-armament --output-format pipeline-result`、trace出力を確認。
- 測定したarmament処理時間: p50 0.0488秒、p95 0.0539秒 (177件)。Apple Siliconの5秒目標内。

## 検証コマンド

```text
./verify.sh --check                         PASS
./verify.sh                                  105 passed / VERIFY PASS
uv run pytest -q                            105 passed
uv run python -m pytest tests/ -q            105 passed
git diff --check                             PASS
git lfs fsck                                 Git LFS fsck OK
actionlint .github/workflows/ci.yml          exit 0
```

`./verify.sh --full` は一時eval/checkpointディレクトリで実行したが、既存のMLX tokenizer cache
`/Users/eightman/dev/apps/autoresearch-naval/.cache/autoresearch-navspec/tokenizer/tokenizer.pkl`
が存在せず、model-dependent inference/training開始前に失敗した。fullをPASSとは記録しない。
CIはworkflow静的検査とMLXなしfast環境での82件検証までは確認済みだが、GitHub Actions上の実行は
未確認である。ローカル `act` はsetup-uvのaction emulator互換性で停止したため、CI PASSの代用にはしない。

## 契約・registry・hash

| 対象 | SHA-256 |
| --- | --- |
| `data/navspec/lora/contracts/navspec_end_to_end_eval_contract_v1.json` | `f1f890d8ed364285f14a36e9c045471a4552af037ce0fc156bb5df4300d17f43` |
| `schemas/navspec_pipeline_result.schema.json` | `684e13f21ad147371cfc5db83be3227c05e360c058b47daa12da942c9a5c1833` |
| `tools/navspec_eval_run.py` | `a4d8fd728797eb807dacbbfecb0a8ffd579b7dac4001eb68ef76b8b818acf759` |
| `models/registry.json` | `fac7f5ffce77fd082c51fa4410e8619c0eaa75840d4479fb1a885574c3fd3903` |
| `evaluation/baselines/stage2_baseline_v1.json` | `a758ef1ea95a40b7ab2a35d6be3e0c0534a57a257b7ff635414169af62489e69` |

registryには `navspec-pipeline-armament-v1` を `default=false`, `status=candidate`,
`promotion_decision=p0-integration-ready-gates-pending` として加算登録した。

## 未達と次の判断

1. tokenizer/model cacheを用意した環境で `./verify.sh --full` を再実行する。
2. GitHub Actionsでfast workflowを実行し、fresh clone確認を完了する。
3. armament fixtureの5件のclassic/teacher契約差分を、データ修正か評価母集団分離か明示的に決める。
4. 43件のfallbackはDB拡張または対象範囲の明示なしに、1%以下へ縮小したと主張しない。

commit、tag、push、default branch変更、旧branch削除、既存大容量JSONLの移行は実施していない。
