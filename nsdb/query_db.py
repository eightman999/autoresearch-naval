#!/usr/bin/env python3
"""
NSDB.db クエリヘルパー
Usage: python3 query_db.py "SQL文"
       python3 query_db.py --schema          テーブル一覧とスキーマを表示
       python3 query_db.py --stats           各テーブルのレコード数を表示
       python3 query_db.py --types           艦種(TYPE)の一覧と件数を表示
       python3 query_db.py --countries       国の一覧を表示
"""
import sqlite3
import sys
import os
import json

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),  "NSDB.db")

# Base64画像カラム（巨大なため出力から除外するカラム）
IMAGE_COLUMNS = {"Photo", "images", "Graphics"}


def get_connection():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def show_schema():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        print(f"\n=== {t} ===")
        cur.execute(f"PRAGMA table_info({t})")
        for col in cur.fetchall():
            print(f"  {col['name']:25s} {col['type'] or 'TEXT'}")
    conn.close()


def show_stats():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        count = cur.fetchone()[0]
        print(f"{t:20s} {count:>10,} records")
    conn.close()


def show_types():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT TYPE, COUNT(*) as cnt FROM CLASS GROUP BY TYPE ORDER BY cnt DESC")
    print(f"{'TYPE':40s} {'COUNT':>8s}")
    print("-" * 50)
    for r in cur.fetchall():
        print(f"{r['TYPE'] or '(empty)':40s} {r['cnt']:>8,}")
    conn.close()


def show_countries():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT Short, Long FROM COUNTRIES ORDER BY Long")
    print(f"{'Code':6s} {'Country Name'}")
    print("-" * 50)
    for r in cur.fetchall():
        print(f"{r['Short']:6s} {r['Long']}")
    conn.close()


def run_query(sql):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        if cur.description is None:
            print("Query executed (no results).")
            conn.close()
            return

        columns = [d[0] for d in cur.description]
        # 画像カラムのインデックスを特定
        img_indices = {i for i, c in enumerate(columns) if c in IMAGE_COLUMNS}

        rows = cur.fetchall()
        if not rows:
            print("No results.")
            conn.close()
            return

        # ヘッダー出力
        display_cols = [c for i, c in enumerate(columns) if i not in img_indices]
        print("\t".join(display_cols))
        print("-" * (len("\t".join(display_cols)) + 20))

        for row in rows:
            values = []
            for i, val in enumerate(row):
                if i in img_indices:
                    continue  # 画像カラムはスキップ
                if val is None:
                    values.append("")
                elif isinstance(val, str) and len(val) > 500:
                    values.append(val[:500] + "...(truncated)")
                else:
                    values.append(str(val))
            print("\t".join(values))

        print(f"\n({len(rows)} rows)")
    except sqlite3.Error as e:
        print(f"SQL Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    arg = sys.argv[1]
    if arg == "--schema":
        show_schema()
    elif arg == "--stats":
        show_stats()
    elif arg == "--types":
        show_types()
    elif arg == "--countries":
        show_countries()
    else:
        run_query(arg)


if __name__ == "__main__":
    main()
