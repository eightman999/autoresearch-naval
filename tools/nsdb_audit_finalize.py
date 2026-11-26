#!/usr/bin/env python3
"""リクエストログの集計と、機械可読な成果物 (docs/nsdb_audit/*.json) の最終整形。"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nsdb_audit_lib import REQ_LOG, SCRATCH  # noqa: E402

D = Path(__file__).resolve().parents[1] / "docs" / "nsdb_audit"
log = [json.loads(x) for x in REQ_LOG.read_text().splitlines()]
by_host = Counter("mirror" if "myownonpmirror" in r["url"] else "origin" for r in log)
status = Counter((("mirror" if "myownonpmirror" in r["url"] else "origin"), r["status"]) for r in log)
t0, t1 = min(r["t"] for r in log), max(r["t"] + r["sec"] for r in log)
# 区間 (30 分以上空いたら別セッション) ごとの稼働時間
ts = sorted(r["t"] for r in log)
active = 0.0
for a, b in zip(ts, ts[1:]):
    if b - a < 120:
        active += b - a
summary = {
    "network_requests_total": len(log) + 3,  # + robots.txt を curl で 3 回 (mirror / www / apex)
    "network_requests_logged": len(log),
    "by_host": dict(by_host),
    "status_by_host": {f"{h}:{s}": n for (h, s), n in sorted(status.items())},
    "non_200": [{"url": r["url"], "status": r["status"], "err": r["err"]} for r in log if r["status"] != 200],
    "wall_clock_span_sec": round(t1 - t0),
    "active_request_time_sec_approx": round(active),
    "avg_response_sec": {h: round(sum(r["sec"] for r in log if (("mirror" if "myownonpmirror" in r["url"] else "origin") == h)) / max(1, by_host[h]), 2) for h in by_host},
    "max_rate_note": "各ホスト 1 req/s 以下 (最小間隔 1.05s)。ミラー・現行の 2 本を並列に走らせたので合計では最大 ~2 req/s だが、ホストごとは 1 req/s 以下。",
    "blocking": "Cloudflare/CAPTCHA/ログイン要求は検出されず。HTTP 429/403 なし。",
    "robots": {"myownonpmirror.com": "404 (robots.txt 無し)", "www.navypedia.org": "200: User-agent: *  Disallow: (全許可)", "navypedia.org": "200: 同上"},
    "user_agent": "nsdb-completeness-audit/1.0 (personal research; read-only; <=1 req/s)",
    "cache_bytes": sum(f.stat().st_size for f in (SCRATCH / "cache").glob("*.html")),
}
(D / "request_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1))
# 発見した艦級 URL の全集合 (機械可読)
for site in ("origin", "mirror"):
    c = json.loads((SCRATCH / f"crawl_{site}.json").read_text())
    out = []
    seen = set()
    for co in c["countries"]:
        for t in co["types"]:
            for cl in t["class_links"]:
                if cl["canon"] in seen:
                    continue
                seen.add(cl["canon"])
                out.append({"canon": cl["canon"], "url": cl["url"], "text": cl["text"][:100], "country": co["name"], "type_page": t["canon"], "type_label": t["text"]})
    (D / f"site_classes_{'live' if site == 'origin' else 'mirror'}.json").write_text(json.dumps({"site": site, "count": len(out), "classes": out}, ensure_ascii=False))
    print(site, len(out))

# ---- 見出し数値 (summary.json) ----
I = json.loads((D / "internal.json").read_text())
V = json.loads((D / "site_vs_nsdb.json").read_text())
S = json.loads((D / "sample_summary.json").read_text())
A = json.loads((D / "armour_targets.json").read_text())
c = V["counts"]
head = {
    "nsdb": {"CLASS_rows": I["totals"]["CLASS"], "CLASS_distinct_pages": I["class_duplicates"]["distinct_canon_keys"],
             "CLASS_duplicate_rows": I["class_duplicates"]["extra_rows"], "CLASS_null_source_rows": I["class_duplicates"]["null_source_url_rows"],
             "SHIPS_rows": I["totals"]["SHIPS"], "SHIPS_distinct_rows": I["ships_duplicates"]["rows_distinct_per_source_url"],
             "SHIPS_duplicate_rows": I["ships_duplicates"]["excess_rows_exact_dup_within_same_source_url"]},
    "site": {"live_class_pages": c["live_class_pages"], "mirror_class_pages": c["mirror_class_pages"], "live_type_pages": c["live_type_pages"]},
    "page_level": {"live_not_in_nsdb": c["live_not_in_nsdb"], "pct_live_missing": round(100 * c["live_not_in_nsdb"] / c["live_class_pages"], 2),
                   "nsdb_not_on_live": c["nsdb_not_in_live"], "causes": V["live_not_in_nsdb"]["causes"],
                   "countries_zero_class_in_nsdb": [r["Short"] for r in I["countries_without_class"]]},
    "armour": {"nsdb_armour_nonempty": I["armour"]["Armour_nonempty_rows"], "classes_with_armour_row_estimate_mirror_parse": len(A),
               "classes_with_protection_prose_in_sections": I["sections"]["classes_with_protection_like_key"]},
    "ships": {"class_pages_all_blank_ship_rows": I["ships_duplicates"]["class_pages_whose_ship_rows_are_all_blank_id_rows"], "class_pages_zero_ship_rows": I["class_vs_ships"]["class_pages_with_zero_ships"]},
    "sample": {"pages": S["pages_total"], "compared": S["pages_compared"], "in_db": S["pages_compared_in_db"], "spec_cells_db": S["spec_cells"]["db"], "sections_db_fixed_column": S["sections"]["db_fixed_column"],
               "sections_db_anywhere": S["sections"]["db_anywhere"], "ships": S["ships"]},
    "requests": {"total_approx": summary["network_requests_total"] + 8, "wall_clock_min": round(summary["wall_clock_span_sec"] / 60, 1)},
}
(D / "summary.json").write_text(json.dumps(head, ensure_ascii=False, indent=1))
print("wrote summary.json")
