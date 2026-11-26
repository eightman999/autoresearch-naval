# リポジトリ安定ブランチ手順

この手順は、`navspec-armament-stage4` の内容を確認した後に、リモートの安定ブランチを
`main` として扱うための運用メモである。確認なしに既定ブランチを変更したり、既存branchを
削除したりしない。

## 事前確認

```bash
git fetch origin --prune
git rev-parse navspec-armament-stage4
git ls-remote origin refs/heads/main refs/heads/navspec-armament-stage4
```

`main` と `navspec-armament-stage4` のSHAが一致していることを確認する。一致していない場合は
default変更を行わず、差分をレビューする。

## default変更

SHA一致を確認した後、リポジトリホスティング側の設定で `main` をdefault branchに変更する。
変更後はfresh cloneで次を確認する。

```bash
git clone <repository-url> <directory>
cd <directory>
git branch --show-current
./verify.sh --check
./verify.sh
```

## 旧branchの扱い

旧branchの削除は、利用者・CI・未マージPRがないことを確認し、明示承認を得た後にだけ行う。
本計画ではbranch削除、履歴書換え、大容量JSONLの履歴移行を実施しない。

## 大容量JSONL

50 MiBを超える既存JSONLは現行履歴の資産として保持し、P0では移行・削除しない。将来の移行は
backup、fresh clone、`git fsck`、`git lfs fsck` を含む別計画で扱う。

2026-09-02 に `find . -type f -name '*.jsonl' -size +50M` で確認した対象は次の7件である。

- `artifacts/navspec-classic-v1/dataset/tasks.train.jsonl` — 74,547,644 bytes
- `data/navspec/tasks.train.jsonl` — 74,547,644 bytes
- `data/navspec/lora/stage2_status_v0/train.jsonl` — 100,133,430 bytes
- `data/navspec/lora/stage2_1_status_v0/train.jsonl` — 101,218,762 bytes
- `data/navspec/lora/stage3_numeric_quality_v0/train.jsonl` — 184,011,582 bytes
- `data/navspec/lora/stage3_numeric_quality_v1/train.jsonl` — 184,190,306 bytes
- `data/navspec/lora/stage3_1_balanced_numeric_v0/train.jsonl` — 207,430,471 bytes
