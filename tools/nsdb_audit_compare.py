#!/usr/bin/env python3
"""NSDB 網羅性監査 B: 一覧クロール結果(現行サイト/ミラー)と NSDB CLASS の突き合わせ。

入力: scratchpad の crawl_origin.json / crawl_mirror.json, NSDB.db (mode=ro)
出力: docs/nsdb_audit/site_vs_nsdb.json
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nsdb_audit_lib import SCRATCH, canon_url, open_ro  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "nsdb_audit" / "site_vs_nsdb.json"
CSV = REPO / "nsdb" / "SHIP_TYPE_LIST.csv"


def fetcher_ship_types():
    ts = []
    for line in CSV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            ts.append(line.split("]")[0].strip("[").replace(",", "-"))
    return ts


def site_sets(crawl, ship_types):
    """(all class canon -> info, fetcher-discoverable canon set)"""
    allc, disc = {}, set()
    type_pages = {}
    for co in crawl["countries"]:
        cdir = co["canon"].split("/")[2] if co["canon"].count("/") >= 2 else ""
        for t in co["types"]:
            texts = t.get("texts") or [t["text"]]
            text = next((x for x in texts if any(st in x for st in ship_types)), t["text"])
            visited = any(st in x for x in texts for st in ship_types) and not t["canon"].endswith("_index.html")
            tp = type_pages.setdefault(t["canon"], {"canon": t["canon"], "text": text, "country_dir": cdir, "country": co["name"], "status": t["status"],
                                                    "n_links": 0, "fetcher_would_visit": visited, "heading": t.get("heading", "")})
            for cl in t["class_links"]:
                tp["n_links"] += 1
                allc.setdefault(cl["canon"], {"canon": cl["canon"], "text": cl["text"], "country_dir": cdir, "country": co["name"], "type_page": t["canon"],
                                              "type_text": text, "fetcher_would_visit_type": visited, "url": cl["url"]})
                if visited:
                    disc.add(cl["canon"])
    return allc, disc, type_pages


def main():
    origin = json.loads((SCRATCH / "crawl_origin.json").read_text())
    mirror = json.loads((SCRATCH / "crawl_mirror.json").read_text())
    ship_types = fetcher_ship_types()
    L, Ld, Ltp = site_sets(origin, ship_types)
    M, Md, Mtp = site_sets(mirror, ship_types)
    db = open_ro()
    D = {}
    for rid, src, country, cname, typ, fa in db.execute("select rowid,source_url,COUNTRY,CLASS_NAME,TYPE,fetched_at from CLASS where source_url is not null"):
        D.setdefault(canon_url(src), {"canon": canon_url(src), "country": country, "class_name": cname, "type": typ, "rows": 0, "hosts": set()})
        D[canon_url(src)]["rows"] += 1
        D[canon_url(src)]["hosts"].add("mirror" if "myownonpmirror" in src else "origin")
    # 補助: NSDB_new.db (repo 内。ミラーを fetcher.py で別途クロールした試験 DB)
    newdb_set = set()
    try:
        c2 = sqlite3.connect(f"file:{REPO / 'nsdb' / 'NSDB_new.db'}?mode=ro", uri=True)
        newdb_set = {canon_url(r[0]) for r in c2.execute("select source_url from CLASS where source_url is not null")}
        newdb_cache = {canon_url(r[0]) for r in c2.execute("select url from CACHE_CLASS_URLS")}
    except Exception:
        newdb_cache = set()
    Ls, Ms, Ds = set(L), set(M), set(D)

    # ページ種別: 艦級リンクらしいか (本文が '数字.' で始まる)
    looks_class = lambda info: bool(re.match(r"^\s*\d+\s*[.!]", info["text"] or ""))

    res = {
        "crawl": {"origin": {"stopped": origin.get("stopped"), "stats": origin.get("stats"), "countries": len(origin["countries"]), "errors": origin.get("errors", [])[:20]},
                  "mirror": {"stopped": mirror.get("stopped"), "stats": mirror.get("stats"), "countries": len(mirror["countries"]), "errors": mirror.get("errors", [])[:20]}},
        "counts": {"live_class_pages": len(Ls), "mirror_class_pages": len(Ms), "nsdb_class_pages_distinct": len(Ds),
                   "live_type_pages": len(Ltp), "mirror_type_pages": len(Mtp),
                   "live_in_nsdb": len(Ls & Ds), "live_not_in_nsdb": len(Ls - Ds), "nsdb_not_in_live": len(Ds - Ls),
                   "mirror_in_nsdb": len(Ms & Ds), "mirror_not_in_nsdb": len(Ms - Ds), "nsdb_not_in_mirror": len(Ds - Ms),
                   "live_not_in_mirror": len(Ls - Ms), "mirror_not_in_live": len(Ms - Ls),
                   "pct_live_covered_by_nsdb": round(100 * len(Ls & Ds) / max(1, len(Ls)), 2),
                   "pct_nsdb_present_on_live": round(100 * len(Ls & Ds) / max(1, len(Ds)), 2),
                   "live_discoverable_by_current_fetcher_filter": len(Ld), "live_not_discoverable_by_filter": len(Ls - Ld),
                   "mirror_discoverable_by_current_fetcher_filter": len(Md),
                   "NSDB_new_db_class_pages(mirror, 2026-07-10)": len(newdb_set), "NSDB_new_db_cache_urls": len(newdb_cache),
                   "NSDB_new_vs_mirror_crawl": {"in_both": len(newdb_set & Ms), "only_newdb": len(newdb_set - Ms), "only_crawl": len(Ms - newdb_set)}},
    }

    def agg(keys, src, label_fn):
        c = Counter(label_fn(src[k]) for k in keys)
        return sorted(c.items(), key=lambda kv: -kv[1])

    # サイトにあって NSDB に無い
    miss = sorted(Ls - Ds)
    res["live_not_in_nsdb"] = {
        "total": len(miss),
        "by_country": agg(miss, L, lambda i: i["country"]),
        "by_type_page": agg(miss, L, lambda i: f'{i["type_page"]} ({i["type_text"]})'),
        "not_visited_by_fetcher_type_filter": sum(1 for k in miss if not L[k]["fetcher_would_visit_type"]),
        "not_class_like_link_text": sum(1 for k in miss if not looks_class(L[k])),
        "also_in_mirror": sum(1 for k in miss if k in Ms),
        "list": [{"canon": k, "text": L[k]["text"][:90], "country": L[k]["country"], "type_page": L[k]["type_page"], "in_mirror": k in Ms, "url": L[k]["url"]} for k in miss],
    }
    # 取りこぼしの原因分類 (ルールベース; 状態は requests_log.jsonl に残る実取得結果で裏付け)
    status_log = {}
    try:
        for line in (SCRATCH / "requests_log.jsonl").read_text().splitlines():
            r = json.loads(line)
            status_log[r["url"]] = r["status"]
    except FileNotFoundError:
        pass
    label_pages = defaultdict(lambda: [0, 0])  # label -> [pages, fully_missing_pages]
    type_missing = {}
    for tp, t in Ltp.items():
        ks = [k for k, i in L.items() if i["type_page"] == tp]
        if not ks:
            continue
        lab = re.sub(r"^\d+\s*[.!]?\s*", "", t["text"]).strip()
        full = all(k not in Ds for k in ks)
        label_pages[lab][0] += 1
        label_pages[lab][1] += full
        type_missing[tp] = (lab, full)
    label_driven_labels = {lab for lab, (n, f) in label_pages.items() if f / n >= 0.5}
    causes = Counter()
    cause_of = {}
    for k in miss:
        i = L[k]
        lab, full = type_missing.get(i["type_page"], ("", False))
        if "file:" in i["url"]:
            c = "壊れた href (file:///C:/seawar/...)。現行・ミラーとも 404 (リンク切れ)"
        elif "%20" in k:
            c = "URL に空白(%20)。現行サイトは 404、ミラーには存在"
        elif status_log.get(i["url"]) == 500:
            c = "現行サイトが HTTP 500 を返すページ (ミラーには存在)"
        elif full and lab in label_driven_labels:
            c = "艦種ページのラベルが取得時の艦種リストに無かった疑い (その艦種ページの艦級が全滅)"
        else:
            c = "原因不明 (現行・ミラーとも 200 で取得可。2026-02 取得時の一時エラー等の疑い)"
        causes[c] += 1
        cause_of[k] = c
    res["live_not_in_nsdb"]["causes"] = dict(causes)
    res["live_not_in_nsdb"]["label_driven_labels"] = sorted(label_driven_labels)
    for it in res["live_not_in_nsdb"]["list"]:
        it["cause"] = cause_of[it["canon"]]
    stale = sorted(Ds - Ls)
    res["nsdb_not_in_live"] = {
        "total": len(stale),
        "by_country": agg(stale, D, lambda i: i["country"]),
        "in_mirror": sum(1 for k in stale if k in Ms),
        "list": [{"canon": k, "country": D[k]["country"], "class_name": D[k]["class_name"], "type": D[k]["type"], "in_mirror": k in Ms, "hosts": sorted(D[k]["hosts"])} for k in stale],
    }
    # ミラー vs 現行
    only_m = sorted(Ms - Ls)
    only_l = sorted(Ls - Ms)
    res["mirror_vs_live"] = {
        "mirror_only": {"total": len(only_m), "by_country": agg(only_m, M, lambda i: i["country"]), "list": [{"canon": k, "text": M[k]["text"][:80]} for k in only_m[:200]]},
        "live_only": {"total": len(only_l), "by_country": agg(only_l, L, lambda i: i["country"]), "list": [{"canon": k, "text": L[k]["text"][:80]} for k in only_l[:200]]},
    }
    # 国別集計
    ctab = defaultdict(lambda: Counter())
    # NSDB の国名 ← COUNTRIES.URL のディレクトリで対応
    short_by_dir = {}
    for short, long_, url in db.execute("select Short,Long,URL from COUNTRIES"):
        p = canon_url(url).split("/")
        if len(p) > 2:
            short_by_dir[p[2]] = short
    for k, i in L.items():
        ctab[i["country_dir"]]["live"] += 1
        ctab[i["country_dir"]]["live_in_nsdb"] += k in Ds
    for k, i in M.items():
        ctab[i["country_dir"]]["mirror"] += 1
    for k, i in D.items():
        d = k.split("/")[2] if k.count("/") >= 2 else ""
        ctab[d]["nsdb"] += 1
        ctab[d]["nsdb_in_live"] += k in Ls
    rows = []
    for d, c in sorted(ctab.items(), key=lambda kv: -kv[1]["live"]):
        rows.append({"dir": d, "nsdb_country": short_by_dir.get(d, ""), "live": c["live"], "mirror": c["mirror"], "nsdb": c["nsdb"],
                     "live_missing_in_nsdb": c["live"] - c["live_in_nsdb"], "nsdb_not_on_live": c["nsdb"] - c["nsdb_in_live"]})
    res["by_country"] = rows
    # NSDB に CLASS が 0 件の国 → サイト上の件数
    zero = []
    for short, long_, url in db.execute("select Short,Long,URL from COUNTRIES"):
        p = canon_url(url).split("/")
        d = p[2] if len(p) > 2 else ""
        if not any(r["nsdb"] for r in rows if r["dir"] == d):
            r = next((r for r in rows if r["dir"] == d), None)
            zero.append({"Short": short, "dir": d, "live_classes": r["live"] if r else 0, "mirror_classes": r["mirror"] if r else 0})
    res["nsdb_zero_class_countries_on_site"] = zero
    # 艦種ページ別 (fetcher の type フィルタで拾えない艦種ページ)
    skipped = [t for t in Ltp.values() if not t["fetcher_would_visit"]]
    res["type_pages_skipped_by_fetcher_filter"] = {
        "count": len(skipped), "classes_only_under_skipped": sum(1 for k in Ls if not L[k]["fetcher_would_visit_type"] and not any(
            True for _ in [])),  # 下で上書き
        "list": [{"canon": t["canon"], "text": t["text"], "n_links": t["n_links"], "country": t["country"], "status": t["status"]} for t in sorted(skipped, key=lambda t: -t["n_links"])[:80]],
    }
    only_under_skipped = [k for k in Ls if k not in Ld]
    res["type_pages_skipped_by_fetcher_filter"]["classes_only_under_skipped"] = len(only_under_skipped)
    res["type_pages_skipped_by_fetcher_filter"]["of_which_in_nsdb"] = sum(1 for k in only_under_skipped if k in Ds)
    # 艦種ページごとの NSDB カバー率
    tcov = []
    byt = defaultdict(list)
    for k, i in L.items():
        byt[i["type_page"]].append(k)
    for tp, ks in byt.items():
        n_in = sum(1 for k in ks if k in Ds)
        tcov.append({"type_page": tp, "text": Ltp[tp]["text"], "country": Ltp[tp]["country"], "classes": len(ks), "in_nsdb": n_in, "missing": len(ks) - n_in})
    res["live_type_page_coverage_worst"] = sorted(tcov, key=lambda r: -r["missing"])[:40]
    res["live_error_pages"] = origin.get("errors", [])
    res["mirror_error_pages"] = mirror.get("errors", [])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=list))
    print(json.dumps(res["counts"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
