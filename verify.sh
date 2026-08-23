#!/usr/bin/env bash
# autoresearch-naval 検証ハーネス (AIエージェント向け)
#
# 使い方:
#   ./verify.sh          高速検証 (uv run pytest tests/ -q)
#   ./verify.sh --check  環境確認のみ
#   ./verify.sh --full   重い検証 (pytest + eval_suite + AUTORESEARCH_TIME_BUDGET=60 のスモーク学習)
#
# 実行環境の実態:
#   - uv 管理 (pyproject.toml + uv.lock、dev group に pytest>=9.1.1)。READMEの手順は uv sync。
#   - テストは tests/ 配下。
#   - --full の eval_suite は同梱classicチェックポイント(checkpoints/navspec/latest.json)を使い、
#     生成結果を data/navspec/generated/eval/ に書き込む (モデルダウンロードは無し)。
#   - --full のスモーク学習は README「学習・評価」節記載の実コマンドそのまま。
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

MODE="fast"
case "${1:-}" in
  "")      MODE="fast" ;;
  --check) MODE="check" ;;
  --full)  MODE="full" ;;
  *) echo "使い方: $0 [--check|--full]" >&2; exit 2 ;;
esac

STEP=0
TOTAL=0
step() { STEP=$((STEP + 1)); echo "[${STEP}/${TOTAL}] $1"; }
fail() { echo "VERIFY FAIL"; exit 1; }

if [ "$MODE" = "check" ]; then
  TOTAL=4
  step "uv の確認 (必須ツール)"
  if command -v uv >/dev/null 2>&1; then
    echo "  uv: $(uv --version 2>&1)"
  else
    echo "  uv: なし (このリポジトリはuv前提。https://docs.astral.sh/uv/)"
    fail
  fi

  step "python3 の確認 (参考。実行はuvが管理)"
  if command -v python3 >/dev/null 2>&1; then
    echo "  python3: $(python3 --version 2>&1)"
  else
    echo "  python3: なし (uvがインタプリタを取得できるため致命ではない)"
  fi

  step "venv / 依存定義の確認"
  if [ -d ".venv" ]; then echo "  .venv: あり (uv sync済み)"; else echo "  .venv: なし (初回の uv run で自動作成・依存解決される)"; fi
  if [ -f "pyproject.toml" ]; then echo "  pyproject.toml: あり"; else echo "  pyproject.toml: 見当たらない"; fi
  if [ -f "uv.lock" ]; then echo "  uv.lock: あり"; else echo "  uv.lock: 見当たらない"; fi

  step "テスト・評価・学習資産の確認"
  if ls tests/test_*.py >/dev/null 2>&1; then
    echo "  tests/: $(ls tests/test_*.py | wc -l | tr -d ' ') ファイル"
  else
    echo "  tests/: テストが見当たらない"
  fi
  if [ -f "tools/eval_suite.py" ]; then echo "  tools/eval_suite.py: あり"; else echo "  tools/eval_suite.py: 見当たらない"; fi
  if [ -f "train.py" ]; then echo "  train.py: あり"; else echo "  train.py: 見当たらない"; fi
  echo "VERIFY PASS"
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  TOTAL=1
  step "uv の確認"
  echo "  uvが見つからない (このリポジトリの実行はuv前提)"
  fail
fi

if [ "$MODE" = "fast" ]; then
  TOTAL=1
  step "ユニットテスト (uv run pytest tests/ -q)"
  echo "  注: .venv未作成の初回は uv sync による依存解決が先に走る"
  if ! uv run pytest tests/ -q; then fail; fi
  echo "VERIFY PASS"
  exit 0
fi

# --full
TOTAL=3
step "ユニットテスト (uv run pytest tests/ -q)"
if ! uv run pytest tests/ -q; then fail; fi

step "評価スイート (uv run python -m tools.eval_suite)"
echo "  注意: classicチェックポイントで22+拡張ケースの生成評価を実行する (数分〜十数分)。"
echo "  注意: data/navspec/generated/eval/ 配下の生成結果ファイルを更新する。"
if ! uv run python -m tools.eval_suite; then fail; fi

step "スモーク学習 (AUTORESEARCH_TIME_BUDGET=60)"
echo "  注意: これから学習ジョブを実行する (README記載のスモーク学習実コマンド)。"
echo "  注意: 時間予算60秒 + データ準備。キャッシュは .cache/autoresearch-navspec に書く。"
if ! env AUTORESEARCH_CACHE_DIR="$PWD/.cache/autoresearch-navspec" \
    AUTORESEARCH_TIME_BUDGET=60 \
    AUTORESEARCH_EVAL_TOKENS=131072 \
    AUTORESEARCH_MAX_SEQ_LEN=512 \
    uv run python train.py; then
  fail
fi
echo "VERIFY PASS"
