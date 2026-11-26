# autoresearch-naval 作業ガイド

このリポジトリは、史実艦データを基に架空艦の `ShipSpecRecord` を生成・検証する NavSpec の
研究環境である。MLX の classic モデルと Qwen2.5-0.5B-Instruct の LoRA 実験を扱う。

`autoresearch` は実験反復の着想元であるが、このリポジトリは「`train.py` だけを自律変更して
`val_bpb` を最小化する」汎用ループではない。仕様の妥当性、矛盾判定、理由コード、数値品質、
既存評価への非回帰を同等に扱う。

標準の高速検証は、リポジトリルートで次を実行する。

```bash
./verify.sh --check
./verify.sh
uv run pytest -q
uv run python -m pytest -q
```

## 作業開始時

1. `git status --short` で既存の変更・生成物を確認し、依頼範囲以外には触れない。
2. [README.md](README.md)、[docs/navspec_pipeline.md](docs/navspec_pipeline.md)、対象機能に対応する
   `docs/` の評価レポートを読む。
3. 目的が classic 生成器、データセット、LoRA、評価契約のどれかを明確にする。複数を同時に
   変更する必要がある場合は、接続方法と保護対象を先に示す。

## 主要な構成

| 対象 | 役割 |
| --- | --- |
| `schemas/` | `ShipSpecRecord` と LoRA 候補出力の正規スキーマ |
| `tools/navspec_*.py` | 抽出、変異、制約、矛盾判定、データセット、評価 |
| `generate_navspec.py` | classic モデルによる生成とルールベース補正 |
| `validate_navspec_output.py` | JSON / JSONL のスキーマ・ルール検証 |
| `tools/qwen_lora_*.py` | Qwen LoRA の訓練・生出力評価 |
| `models/registry.json` | アダプターの採用状態・対応データセットの正本 |
| `artifacts/` | classic の固定アーカイブと LoRA の整合性 manifest |

## モデルと評価の扱い

- 既定 LoRA は `stage2-status-r8`。`models/registry.json` の `default: true` と評価レポートを
  根拠にする。
- Stage 3 数値品質アダプターは実験扱いである。数値品質の改善だけを根拠に既定へ昇格させない。
  `impossible` 判定・理由コードの Stage 2 回帰ゲートも満たす必要がある。
- `schemas/`、`data/navspec/lora/contracts/`、既存アダプター、固定 artifact、既存の評価 manifest は
  保護対象である。明示依頼なしに書き換え・削除・再生成しない。
- NSDB はローカルデータベースであり、リポジトリには含めない。抽出元を変更する場合はデータの
  来歴と再現手順を併せて記録する。

## 標準ワークフロー

### classic 生成器

1. 変更対象を `generate_navspec.py`、`tools/navspec_*.py`、スキーマのいずれかに限定する。
2. 生成結果をスキーマとルールで検証する。
3. 対象ケースを含む評価スイートを実行し、既存ケースの回帰有無を確認する。

```bash
uv run python generate_navspec.py \
  --country Japan --year 1938 --ship-type battleship \
  --raw-output /tmp/navspec_raw.txt --json-output /tmp/navspec.json
uv run python validate_navspec_output.py /tmp/navspec.json
uv run python -m tools.eval_suite --extended-count 100 \
  --json-report /tmp/navspec_eval_report.json
```

### データセット

1. NSDB からの抽出、架空バリエーション、タスク化、妥当性評価の順を保つ。
2. 既存 JSONL を置換する前に件数・スキーマ・差分を確認する。
3. 生成データセットには seed、入力件数、分割、制約バージョンを manifest に残す。

```bash
uv run python tools/navspec_extract.py --db /path/to/NSDB.db
uv run python tools/navspec_mutate.py
uv run python tools/navspec_tasks.py
uv run python tools/navspec_plausibility.py \
  data/navspec/battleship_fictional_specs.jsonl \
  --base-records data/navspec/battleship_real_specs.jsonl
```

### LoRA

1. Stage に対応するデータセット作成スクリプトで manifest を作る。
2. 学習後は raw 出力評価を行い、schema 完全性、`status`、`impossible` F1、理由コード、数値品質を
   既定アダプターと比較する。
3. 同一の評価ケース・生成モード・乱数条件で比較できない結果は昇格根拠にしない。
4. 採否、ハッシュ、回帰の有無を `models/registry.json` と `docs/` に記録する。

## classic MLX 学習

`train.py` と `prepare.py` は classic モデルのために残している。短時間学習は評価や生成器の変更を
正当化しないため、`val_bpb` だけで採否を決めない。チェックポイントの保存先、データキャッシュ、
時間予算は `AUTORESEARCH_*` 環境変数で分離する。

```bash
env AUTORESEARCH_CACHE_DIR="$PWD/.cache/autoresearch-navspec" \
  AUTORESEARCH_TIME_BUDGET=60 \
  AUTORESEARCH_EVAL_TOKENS=131072 \
  AUTORESEARCH_MAX_SEQ_LEN=512 \
  uv run python train.py
```

## 完了前の確認

1. `git diff --check` と `git diff` で、変更が依頼範囲だけであることを確認する。
2. 変更内容に対応する検証を実行し、実出力を確認する。
3. 実機推論・LoRA 学習・全件評価を行っていない場合は、その未検証範囲を明記する。
4. コミット・プッシュ・既定アダプターの変更は、明示指示がある場合のみ行う。
