#!/usr/bin/env python3
"""SHIPS が『全項目空の 1 行のみ』のクラス (DB 内 2,648 ページ) の実ページを 10 件標本確認する。
出力: docs/nsdb_audit/ship_table_variants.json  (seed 固定。取得はキャッシュ優先・1 req/s)"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nsdb_audit_lib import Crawler, open_ro  # noqa: E402
from nsdb_audit_pages import parse_live  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "nsdb_audit" / "ship_table_variants.json"
db = open_ro()
rows = db.execute("select source_url, sum(NAME='' and NO='' and Yard_No='' and Builder='' and Laid_down='' and Launched=''), count(*) from SHIPS group by source_url").fetchall()
all_blank = [u for u, a, n in rows if u and a == n]
rnd = random.Random(7)
rnd.shuffle(all_blank)
cr = Crawler()
res = {"db_pages_whose_ship_rows_are_all_blank": len(all_blank), "checked": []}
for u in all_blank[:10]:
    st, raw = cr.get(u)
    if st != 200:
        res["checked"].append({"url": u, "status": st})
        continue
    p = parse_live(raw)
    res["checked"].append({"url": u, "status": st, "ship_headers": p["ship_headers"], "n_rows": len(p["ships"]),
                           "first_row": {k: v for k, v in (p["ships"][0] if p["ships"] else {}).items() if v}})
res["pages_with_group_summary_table_headers"] = sum(1 for c in res["checked"] if c.get("ship_headers") and c["ship_headers"][0] == "Names")
res["stats"] = cr.stats()
OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
print(res["pages_with_group_summary_table_headers"], "/ 10", res["stats"])
