#!/usr/bin/env python3
"""NSDB 網羅性監査: 一覧ページのみをたどって艦級ページ URL の全集合を作る。

国一覧 → 各国ページ → 艦種別ページ(艦級リンク一覧) だけを取得する（艦級ページ本体は取らない）。
1 req/s 以下・HTML は scratchpad にキャッシュ・読み取り専用。

使い方:
  python3 tools/nsdb_audit_crawl.py --site mirror   # https://myownonpmirror.com (HTTrack 2021-01 スナップショット)
  python3 tools/nsdb_audit_crawl.py --site origin   # https://www.navypedia.org (現行サイト)
出力: scratchpad/nsdb_audit/crawl_<site>.json （--out で変更可）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nsdb_audit_lib import MIRROR, ORIGIN, SCRATCH, Crawler, StopCrawl, canon_url, clean_text  # noqa: E402

SITES = {
    "mirror": {"base": MIRROR, "index": MIRROR + "/ships_index.html"},
    "origin": {"base": ORIGIN, "index": ORIGIN + "/ships_index.htm"},
}


def soup_of(raw: bytes) -> BeautifulSoup:
    return BeautifulSoup(raw, "html.parser")


def links_of(soup: BeautifulSoup, page_url: str):
    """(絶対URL, canon path, text) のリスト。#fragment・mailto 等は除外。"""
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absu = urljoin(page_url, href)
        p = urlparse(absu)
        if p.scheme not in ("http", "https"):
            continue
        out.append((absu, canon_url(absu), clean_text(a.get_text(" ", strip=True))))
    return out


def is_site_path(canon: str) -> bool:
    return canon.startswith("/ships/")


def crawl(site: str, out_path: Path, max_requests: int) -> dict:
    cfg = SITES[site]
    cr = Crawler(base=cfg["base"])
    result = {"site": site, "base": cfg["base"], "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "countries": [], "errors": [], "stopped": None}

    def save():
        result["stats"] = cr.stats()
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1))

    try:
        st, raw = cr.get(cfg["index"])
        if st != 200:
            raise StopCrawl(f"index status {st}")
        isoup = soup_of(raw)
        seen = {}
        for absu, canon, text in links_of(isoup, cfg["index"]):
            if re.search(r"/ships/.*_index\.html$", canon) and canon not in seen:
                seen[canon] = (absu, text)
        # Jamaica は国ページ(_index)でなく jam_cg に直接リンクしている (両サイト共通の特例)
        for absu, canon, text in links_of(isoup, cfg["index"]):
            if is_site_path(canon) and not canon.endswith("_index.html") and canon not in seen:
                seen[canon] = (absu, text or "(direct)")
        print(f"[{site}] countries on index: {len(seen)}", flush=True)

        for i, (ccanon, (curl, cname)) in enumerate(seen.items(), 1):
            if cr.n_net >= max_requests:
                result["stopped"] = f"max_requests {max_requests}"
                break
            crec = {"canon": ccanon, "url": curl, "name": cname, "status": None, "types": [], "direct_class_like": []}
            result["countries"].append(crec)
            # Jamaica は index が直接 jam_cg(国ページ相当) を指し、艦種ページ jam_cgg → 艦級 と 1 段深い。
            # 全国共通で「index リンク先を国ページとして取得し、その中の非 index リンクを艦種ページとみなす」。
            st, raw = cr.get(curl)
            crec["status"] = st
            if st != 200:
                result["errors"].append({"url": curl, "status": st})
                save()
                continue
            csoup = soup_of(raw)
            type_links = []
            tidx = {}
            for absu, canon, text in links_of(csoup, curl):
                if not is_site_path(canon) or canon == ccanon or canon.endswith("_index.html"):
                    continue
                if canon in tidx:
                    type_links[tidx[canon]][3].append(text)  # 同一 URL の別アンカー文言 (ミラーは '5' + '. ESCORTS' に分割される)
                    continue
                tidx[canon] = len(type_links)
                type_links.append((absu, canon, text, [text]))
            for turl, tcanon, ttext, ttexts in type_links:
                trec = {"canon": tcanon, "url": turl, "text": ttext, "texts": ttexts, "status": None, "class_links": []}
                crec["types"].append(trec)
                st, raw = cr.get(turl)
                trec["status"] = st
                if st != 200:
                    result["errors"].append({"url": turl, "status": st})
                    continue
                tsoup = soup_of(raw)
                lseen = set()
                # 艦種ページ内 <title>/h1/h2 (種別ラベル) を記録
                h = tsoup.find(["h2", "a"], class_="reds2") or tsoup.find("h2")
                trec["heading"] = clean_text(h.get_text(" ", strip=True)) if h else ""
                for absu, canon, text in links_of(tsoup, turl):
                    if not is_site_path(canon) or canon.endswith("_index.html") or canon in (tcanon, ccanon):
                        continue
                    if canon in lseen:
                        continue
                    lseen.add(canon)
                    trec["class_links"].append({"canon": canon, "url": absu, "text": text})
            if i % 10 == 0:
                print(f"[{site}] {i}/{len(seen)} countries  net={cr.n_net} elapsed={cr.stats()['elapsed_sec']}s", flush=True)
                save()
    except StopCrawl as e:
        result["stopped"] = f"STOP: {e}"
        print("STOP:", e, flush=True)
    save()
    print(json.dumps(result["stats"]), flush=True)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=list(SITES), required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-requests", type=int, default=1500)
    a = ap.parse_args()
    out = Path(a.out) if a.out else SCRATCH / f"crawl_{a.site}.json"
    crawl(a.site, out, a.max_requests)
