"""Prepare local naval-spec parquet shards and tokenizer for autoresearch.

This script avoids the default HuggingFace corpus. It converts local
data/navspec/tasks.*.jsonl files into the parquet layout expected by prepare.py,
then trains the tokenizer against that local corpus.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE = ROOT / ".cache" / "autoresearch-navspec"
DEFAULT_TRAIN = ROOT / "data" / "navspec" / "tasks.train.jsonl"
DEFAULT_VAL = ROOT / "data" / "navspec" / "tasks.val.jsonl"


def read_task_texts(path: Path) -> list[str]:
    texts: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Task row without text in {path}")
        texts.append(text)
    return texts


def write_text_parquet(path: Path, texts: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table({"text": pa.array(texts, type=pa.string())})
    pq.write_table(table, path)


def clear_local_cache(cache_dir: Path) -> None:
    for child in [cache_dir / "data", cache_dir / "tokenizer"]:
        if not child.exists():
            continue
        for path in child.glob("*"):
            if path.is_file():
                path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare local navspec data for MLX autoresearch")
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--val", type=Path, default=DEFAULT_VAL)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--vocab-size", type=int, default=2048)
    parser.add_argument("--force", action="store_true", help="Clear local navspec cache before writing")
    args = parser.parse_args()

    cache_dir = args.cache_dir.resolve()
    os.environ["AUTORESEARCH_CACHE_DIR"] = str(cache_dir)
    os.environ["AUTORESEARCH_VOCAB_SIZE"] = str(args.vocab_size)

    from prepare import DATA_DIR, TOKENIZER_DIR, VAL_FILENAME, train_tokenizer

    if args.force:
        clear_local_cache(cache_dir)

    train_texts = read_task_texts(args.train)
    val_texts = read_task_texts(args.val)
    if not train_texts:
        raise ValueError("No train tasks found")
    if not val_texts:
        raise ValueError("No val tasks found")

    write_text_parquet(Path(DATA_DIR) / "shard_00000.parquet", train_texts)
    write_text_parquet(Path(DATA_DIR) / VAL_FILENAME, val_texts)
    print(f"wrote {len(train_texts)} train texts to {Path(DATA_DIR) / 'shard_00000.parquet'}")
    print(f"wrote {len(val_texts)} val texts to {Path(DATA_DIR) / VAL_FILENAME}")
    print(f"tokenizer dir: {TOKENIZER_DIR}")
    train_tokenizer()
    print("Done. Train with:")
    print(f"  AUTORESEARCH_CACHE_DIR={cache_dir} uv run train.py")


if __name__ == "__main__":
    main()
