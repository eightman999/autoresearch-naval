# autoresearch-naval

歴史上の艦艇データを基に、架空艦の仕様を構造化 JSON として生成・検証する
NavSpec 実験環境です。Apple Silicon 上の MLX を使う小規模な学習器と、Qwen2.5-0.5B
向け LoRA 学習・評価パイプラインを含みます。

本リポジトリは [autoresearch](https://github.com/karpathy/autoresearch) の実験ループを
出発点にしていますが、現在の主目的は汎用言語モデルの探索ではなく、艦艇仕様の生成品質・
制約遵守・回帰評価です。

## できること

- `ShipSpecRecord` スキーマに沿った架空艦仕様の生成
- 船型、年代、国家能力、条約、兵装、速力・航続距離などの整合性検査
- 矛盾する要求を `impossible` として理由コード付きで返す判定
- NSDB からの史実レコード抽出と、範囲を限定した架空バリエーションの作成
- MLX による classic モデルの学習、および Qwen2.5-0.5B-Instruct の LoRA 学習・評価

生成結果は設計案の補助であり、史実・工学的な正確さを保証するものではありません。ゲームへ
反映する前に、出力された数値と制約判定を必ずレビューしてください。

## 必要環境

- Apple Silicon 搭載 Mac
- Python 3.10 以上 3.14 未満
- [uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/eightman999/autoresearch-naval.git
cd autoresearch-naval
uv sync
```

標準の高速検証は、リポジトリルートで次を実行します。

```bash
./verify.sh --check
./verify.sh
uv run pytest -q
uv run python -m pytest -q
```

LoRA の推論・学習では、初回に Qwen のベースモデル取得が必要になる場合があります。モデルと
データセットには、それぞれの配布元のライセンス・利用条件が適用されます。

## まず生成する

classic モデルのチェックポイントとサンプルデータは同梱されています。次の例では、1938 年の
日本戦艦案を生成して `data/navspec/generated/example.json` に保存します。

```bash
uv run python generate_navspec.py \
  --country Japan \
  --year 1938 \
  --ship-type battleship \
  --constraints-json '{"speed_kt": 30, "main_gun_caliber_mm": 410}' \
  --raw-output data/navspec/generated/example_raw.txt \
  --json-output data/navspec/generated/example.json

uv run python validate_navspec_output.py data/navspec/generated/example.json
```

`generate_navspec.py` は、生のモデル出力を保存した後にルールベースの補正とスキーマ検証を
行います。`status: impossible` が返る場合は、出力 JSON の `reason` と
`suggested_revision` を確認してください。

### GUI で設計する

二段 pipeline (Stage 2 + classic + numeric specialist) を GUI から操作できます。
年代・国家・艦種・制約を入力して「設計生成」ボタンを押すと、3〜5 秒で仕様が生成されます。

```bash
uv run python tools/navspec_gui.py
```

タブ構成: 仕様表 / 設計コンセプト / パッチ詳細 / 詳細兵装 / 検証trace / 不可理由 / 生JSON。
依存関係追加なし（Python 標準の Tkinter を使用）。

### 詳細兵装付きCLI

詳細兵装は既存のclassic出力を置き換えず、明示的にpipeline resultとして取得できます。

```bash
# normal
uv run python tools/navspec_armament_pipeline.py \
  --nation Japan --year 1941 --ship-type battleship \
  --displacement-t 30000 --beam-m 38 --output /tmp/navspec-normal.json

# impossible（Stage 2相当の下流skip）
uv run python tools/navspec_armament_pipeline.py \
  --nation 'United States' --year 1944 --ship-type destroyer \
  --constraints-json '{"main_gun_caliber_mm_min":203,"main_gun_barrels_min":6,"speed_kt_min":40,"belt_armor_mm_min":150,"range_nm_min":10000}'

# warning-only（status=okのままwarningを保持）
uv run python tools/navspec_armament_pipeline.py \
  --nation Japan --year 1941 --ship-type battleship \
  --displacement-t 30000 --beam-m 1
```

モデルのStage 2/numeric cacheを使う二段CLIでは、`--with-armament`を付けると同じ
`navspec.pipeline_result.v1` facadeを接続できます。`--trace-output`は結果をJSONLで保存し、
`--output-format pipeline-result`はjson reportのresultsを統合結果へ切り替えます。
既存のclassic `final_json`を使う場合はオプションを付けないでください。

GUIを開かず同じfacadeを実プロセスで確認するには、次を実行します。

```bash
uv run python tools/navspec_gui.py --smoke all
```

Tk画面を実際に起動し、入力欄から normal / impossible / warning-only を順に操作する
deterministic smoke は次で確認できます。

```bash
uv run python tools/navspec_gui.py --smoke-ui all
```

## データセットを再構築する

史実レコードの抽出元である NSDB SQLite データベースはリポジトリに含めていません。用意した
データベースを指定してから、以下の順に実行します。

```bash
uv run python tools/navspec_extract.py --db /path/to/NSDB.db
uv run python tools/navspec_mutate.py
uv run python tools/navspec_tasks.py
uv run python tools/navspec_eval.py \
  data/navspec/battleship_real_specs.jsonl \
  data/navspec/battleship_fictional_specs.jsonl
uv run python tools/navspec_plausibility.py \
  data/navspec/battleship_fictional_specs.jsonl \
  --base-records data/navspec/battleship_real_specs.jsonl
uv run python prepare_navspec.py --force
```

NSDB の取得・更新方法は [nsdb/README.md](nsdb/README.md) を参照してください。抽出対象は初期設定で
戦艦・巡洋戦艦・海防戦艦です。詳しいデータフローとスモーク学習の環境変数は
[docs/navspec_pipeline.md](docs/navspec_pipeline.md) にまとめています。

## 学習・評価

classic モデルの短いスモーク学習:

```bash
env AUTORESEARCH_CACHE_DIR="$PWD/.cache/autoresearch-navspec" \
  AUTORESEARCH_TIME_BUDGET=60 \
  AUTORESEARCH_EVAL_TOKENS=131072 \
  AUTORESEARCH_MAX_SEQ_LEN=512 \
  uv run python train.py
```

既存の評価ケースを使った生成評価:

```bash
uv run python -m tools.eval_suite \
  --extended-count 100 \
  --json-report data/navspec/generated/eval/report.json
```

LoRA のデータ生成・訓練・評価スクリプトは `tools/navspec_lora_data*.py`、
`tools/qwen_lora_train.py`、`tools/qwen_lora_eval.py` にあります。データセット・評価レポート・
アダプターの対応は `models/registry.json` と `docs/` を正とします。

2026-07-13 時点の既定アダプターは Stage 2（`stage2-status-r8`）です。Stage 3 は数値品質を
改善した一方で `impossible` 判定と理由コードに回帰があり、実験扱いに留めています。詳細は
[Stage 3 候補検証レポート](docs/navspec_stage3_candidate_validation_2026-07-13.md) を参照してください。

## リポジトリ構成

| パス | 内容 |
| --- | --- |
| `schemas/` | ShipSpecRecord と LoRA 候補出力の JSON Schema |
| `tools/` | 抽出、変異、制約、学習データ作成、学習、評価ツール |
| `data/navspec/` | 学習・評価データ、生成結果、実験 manifest |
| `models/` | LoRA アダプターと採用状況のレジストリ |
| `artifacts/` | classic モデルの固定アーカイブと整合性 manifest |
| `docs/` | パイプライン、実験結果、昇格判定の記録 |
| `nsdb/` | Navypedia ミラー由来のデータ取得・参照ツール |

## 謝辞

- [Andrej Karpathy / autoresearch](https://github.com/karpathy/autoresearch) — 自律実験ループの原案
- [MLX](https://github.com/ml-explore/mlx) — Apple Silicon 向け機械学習フレームワーク
- [Qwen](https://github.com/QwenLM/Qwen) — LoRA 実験のベースモデル
- Navypedia および NSDB のデータ整備に関わる各プロジェクト

## License

MIT License。詳細は [LICENSE](LICENSE) を参照してください。
