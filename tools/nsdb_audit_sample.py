#!/usr/bin/env python3
"""NSDB 網羅性監査 B: 艦級ページの標本照合 (標本 ~40 件)。

ソース別に『現行サイト(navypedia.org)の正解』と比較する:
  DB(2026-02 取得 origin 行) / fetcher.py 現行ロジック×現行サイト / fetcher.py 現行ロジック×ミラー
前提: nsdb_audit_crawl.py の crawl_origin.json / crawl_mirror.json が scratchpad にあること。
出力: docs/nsdb_audit/sample_pages.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nsdb_audit_lib import MIRROR, ORIGIN, SCRATCH, Crawler, StopCrawl, canon_url, open_ro  # noqa: E402
from nsdb_audit_pages import norm, parse_live, run_fetcher_offline, spec_to_column  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "nsdb_audit" / "sample_pages.json"
NUMERIC_COLS = {"Displacement_normal", "Displacement_full", "Length", "Breadth", "Draught", "No_of_shafts", "Power"}
SECTION_COL = {"project history": "Project_history", "modernizations": "Modernizations", "naval service": "Naval_service"}
DB_SPEC_COLS = ["Displacement_normal", "Displacement_full", "Length", "Breadth", "Draught", "No_of_shafts", "Machinery",
                "Power", "speed", "Fuel", "Endurance", "Armour", "Armament", "Electronic_equipment", "Complement"]


def cmp_val(src: str, truth: str, col: str = "") -> str:
    src, truth = (src or "").strip(), (truth or "").strip()
    if not truth:
        return "spurious" if src else "absent"
    if not src:
        return "missing"
    ns, nt = norm(src), norm(truth)
    if ns == nt:
        if re.search(r"\d \d", src) and not re.search(r"\d \d", truth):
            return "ok_digit_split"  # ミラー由来: 数字の途中に空白が混入 (例 '5 6 0')
        return "ok"
    if col in NUMERIC_COLS:
        nums_t = re.findall(r"\d+(?:\.\d+)?", truth.replace(" ", "")) + re.findall(r"\d+(?:\.\d+)?", truth)
        if src.replace(",", "") in nums_t or norm(src) in {norm(x) for x in nums_t}:
            return "ok_numeric_normalized"
    if nt.startswith(ns) and len(ns) < len(nt):
        return "truncated"
    if ns in nt:
        return "partial_substring"
    return "different"


def ship_key(name, builder, laid, launched):
    return "|".join(norm(x) for x in (name, builder, laid, launched))


def pick_sample(origin_crawl, mirror_crawl, db, n_target=40, seed=20261006):
    rnd = random.Random(seed)
    cur = db.execute("SELECT rowid,source_url,COUNTRY,CLASS_NAME,TYPE,sections,fetched_at FROM CLASS WHERE source_url LIKE 'https://www.navypedia.org/%'")
    rows = cur.fetchall()
    ships = defaultdict(list)
    for src, when, ld, lau in db.execute("SELECT source_url,WHEN_DATE,Laid_down,Launched FROM SHIPS WHERE source_url LIKE 'https://www.navypedia.org/%'"):
        ships[canon_url(src)].append((when, ld, lau))

    def era(canon):
        ys = []
        for when, ld, lau in ships.get(canon, [])[:5]:
            for t in (when, ld, lau):
                m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", t or "")
                if m:
                    ys.append(int(m.group(1)))
                    break
        if not ys:
            return "unknown"
        y = min(ys)
        return "<1900" if y < 1900 else "1900-19" if y < 1920 else "1920-45" if y < 1946 else "1946-69" if y < 1970 else "1970-99" if y < 2000 else "2000+"

    recs = {}
    for rid, src, country, cname, typ, sections, fa in rows:
        c = canon_url(src)
        if c not in recs:
            recs[c] = {"canon": c, "db_url": src, "country": country, "class_name": cname, "type": typ, "era": era(c), "sections": sections}
    chosen: dict[str, str] = {}  # canon -> reason

    def add(c, why):
        if c in recs and c not in chosen:
            chosen[c] = why
            return True
        return False

    # 1) 特定の取りこぼし/異常候補
    for c in ("/ships/russia/ru_cf_r141.html", "/ships/china/ch_cf_2201.html", "/ships/germany/ger_mb_kfk2.html"):
        add(c, "SHIPS 重複倍率が極端なクラス")
    zero = [c for c in recs if c not in ships]
    rnd.shuffle(zero)
    for c in zero[:3]:
        add(c, "SHIPS が 0 件のクラス")
    numbered = [c for c, r in recs.items() if r["sections"] and re.search(r'"Modernizations \d+\.', r["sections"])]
    rnd.shuffle(numbered)
    for c in ["/ships/japan/jap_bb_yamato.html"] + numbered[:1]:
        add(c, "Modernizations が番号付き見出し ('Modernizations N.')")
    emptytype = [c for c, r in recs.items() if not (r["type"] or "").strip()]
    rnd.shuffle(emptytype)
    for c in emptytype[:2]:
        add(c, "TYPE が空")
    carriers = [c for c, r in recs.items() if r["sections"] and '"Air group"' in r["sections"]]
    rnd.shuffle(carriers)
    for c in carriers[:1]:
        add(c, "空母 (Air group / Aircraft facilities セクション)")
    prot = [c for c, r in recs.items() if r["sections"] and '"Ship protection"' in r["sections"]]
    rnd.shuffle(prot)
    for c in prot[:1]:
        add(c, "Ship protection セクションあり")
    # 2) 層化ランダム (年代 × 艦種語 × 国 を散らす)
    kind = lambda t: ("sub" if "submarine" in t else "patrol" if "patrol" in t or "boat" in t or "craft" in t else "large" if re.search(r"battle|cruiser|carrier|destroyer|frigate|corvette", t) else "aux/other")
    pool = list(recs.values())
    rnd.shuffle(pool)
    cov_country, cov_era, cov_kind = Counter(), Counter(), Counter()
    need = n_target - len(chosen) - 7  # 7 件は live 側の差分候補用に残す
    while need > 0 and pool:
        best = min(pool[:400], key=lambda r: (cov_country[r["country"]] * 3 + cov_era[r["era"]] * 2 + cov_kind[kind(r["type"] or "")], rnd.random()))
        pool.remove(best)
        if best["canon"] in chosen:
            continue
        chosen[best["canon"]] = "層化ランダム"
        cov_country[best["country"]] += 1
        cov_era[best["era"]] += 1
        cov_kind[kind(best["type"] or "")] += 1
        need -= 1
    # 3) 現行サイトにあって NSDB に無い艦級 / ミラーのみの艦級 (取りこぼし候補)
    live_urls = {}
    for co in origin_crawl["countries"]:
        for t in co["types"]:
            for cl in t["class_links"]:
                live_urls.setdefault(cl["canon"], (cl["url"], co["name"]))
    missing_in_db = [c for c in live_urls if c not in recs and c not in {canon_url(r["db_url"]) for r in recs.values()}]
    rnd.shuffle(missing_in_db)
    extra = []
    forced = ["/ships/bahamas/baha_cf_yellow%20elder.html", "/ships/sarawak/sa_of_aden.html"]
    for c in forced:
        if c in live_urls and c not in recs:
            extra.append((c, "現行サイトにあって NSDB に無い艦級 (URL に空白 %20 / 壊れた file:/// href)"))
    for c in [x for x in missing_in_db if x not in forced][:4]:
        extra.append((c, "現行サイトにあって NSDB に無い艦級"))
    # 4) NSDB にあって現行サイトに無い艦級
    gone = [c for c in recs if c not in live_urls]
    rnd.shuffle(gone)
    for c in gone[:3]:
        extra.append((c, "NSDB にあって現行サイト一覧に無い艦級"))
    return recs, chosen, extra, live_urls


def db_rows_for(db, canon):
    out = {"origin": None, "mirror": None, "origin_ships": [], "mirror_ships": []}
    cols = ["Photo IS NOT NULL AND length(Photo)>0 AS has_photo", "length(images)>0 AS has_images", "length(Graphics)>0 AS has_graphics",
            "CLASS_NAME", "TYPE", "COUNTRY", "source_url", "sections"] + DB_SPEC_COLS + ["Project_history", "Modernizations", "Naval_service"]
    cur = db.execute(f"SELECT {','.join(cols)} FROM CLASS WHERE source_url IN (?,?,?)",
                     (ORIGIN + canon[:-5] + ".htm", ORIGIN + canon, MIRROR + canon))
    names = [d[0] for d in cur.description]
    for r in cur.fetchall():
        d = dict(zip(names, r))
        out["mirror" if "myownonpmirror" in d["source_url"] else "origin"] = d
    for key, u in (("origin_ships", [ORIGIN + canon[:-5] + ".htm", ORIGIN + canon]), ("mirror_ships", [MIRROR + canon])):
        cur = db.execute(f"SELECT NAME,NO,Yard_No,Builder,Laid_down,Launched,Fate_WHY,Fate_WHEN FROM SHIPS WHERE source_url IN ({','.join('?' * len(u))})", u)
        out[key] = cur.fetchall()
    return out


def compare_page(truth, db_row, fetch_live, fetch_mirror, db_ships, s2c):
    """項目ごとの判定を返す。"""
    rec = {"specs": {}, "sections": {}, "ships": {}, "unmapped_labels": []}
    # --- specs
    for label, tv in truth["specs"].items():
        col = s2c.get(label)
        if not col:
            rec["unmapped_labels"].append([label, tv[:60]])
            continue
        r = {"truth": tv[:80]}
        r["db"] = cmp_val(db_row[col], tv, col) if db_row else "n/a"
        r["fetcher_live"] = cmp_val(fetch_live["spec_cols"].get(col, "") if fetch_live else "", tv, col) if fetch_live else "n/a"
        r["fetcher_mirror"] = cmp_val(fetch_mirror["spec_cols"].get(col, ""), tv, col) if fetch_mirror else "n/a"
        rec["specs"][label] = r
    # DB に在って truth に無い (spurious) 列
    if db_row:
        mapped = {s2c.get(l) for l in truth["specs"]}
        sp = [c for c in DB_SPEC_COLS if db_row[c] and c not in mapped]
        rec["db_spurious_cols"] = sp
    # --- sections
    sec_json = {}
    if db_row and db_row.get("sections"):
        try:
            sec_json = json.loads(db_row["sections"])
        except Exception:
            sec_json = {}
    for title, sv in truth["sections"].items():
        if title.lower() == "menu":
            continue
        base = re.sub(r"\s*\d+\.?\s*$", "", title).strip().lower()
        col = SECTION_COL.get(base)
        r = {"truth_len": len(sv["text"]), "n_p": sv["n_p"], "col": col, "numbered_title": title.lower() != base}
        if db_row:
            fixed = (db_row[col] if col else "")
            r["db_fixed_column"] = cmp_sec(fixed, sv["text"])
            r["db_anywhere"] = cmp_sec(fixed or sec_json.get(title, ""), sv["text"])
        fl = fetch_live["sections"].get(title, "") if fetch_live else ""
        r["fetcher_live"] = cmp_sec(fl, sv["text"]) if fetch_live else "n/a"
        fm = fetch_mirror["sections"].get(title, "") if fetch_mirror else ""
        r["fetcher_mirror"] = cmp_sec(fm, sv["text"]) if fetch_mirror else "n/a"
        rec["sections"][title] = r
    # --- ships
    tships = truth["ships"]
    tkeys = [ship_key(s.get("Name"), s.get("Builder"), s.get("Laid down"), s.get("Launched")) for s in tships]
    r = {"truth_rows": len(tships), "truth_with_comp": sum(1 for s in tships if (s.get("Comp") or "").strip() not in ("", "---"))}
    r["truth_blank_name_rows"] = sum(1 for s in tships if not (s.get("Name") or "").strip())
    if db_row is not None:
        dkeys = [ship_key(a[0], a[3], a[4], a[5]) for a in db_ships]
        r["db_rows"] = len(dkeys)
        r["db_distinct_rows"] = len({tuple(a) for a in db_ships})
        r["db_missing_vs_truth"] = len(set(tkeys) - set(dkeys))
        r["db_extra_vs_truth"] = len(set(dkeys) - set(tkeys))
    for nm, fd in (("fetcher_live", fetch_live), ("fetcher_mirror", fetch_mirror)):
        if fd is None:
            continue
        fk = [ship_key(s.get("Name"), s.get("Builder"), s.get("Laid down"), s.get("Launched")) for s in fd["ships"]]
        r[f"{nm}_rows"] = len(fk)
        r[f"{nm}_missing_vs_truth"] = len(set(tkeys) - set(fk))
        r[f"{nm}_extra_vs_truth"] = len(set(fk) - set(tkeys))
    rec["ships"] = r
    return rec


def cmp_sec(src, truth):
    src, truth = (src or "").strip(), (truth or "").strip()
    if not truth:
        return "absent" if not src else "spurious"
    if not src:
        return "missing"
    ns, nt = norm(src), norm(truth)
    ratio = len(ns) / max(1, len(nt))
    if ns == nt or ratio >= 0.985:
        # 英数字だけ比べれば一致でも、空白が違えば語が連結している (例 'R-2andR-92were')
        a, b = re.sub(r"\s+", " ", src), re.sub(r"\s+", " ", truth)
        if a == b:
            return "ok"
        return "ok_but_spaces_lost" if a.count(" ") < b.count(" ") else "ok_but_extra_spaces"
    if nt.startswith(ns[: max(10, int(len(ns) * 0.9))]) and ratio < 0.985:
        return "truncated"
    return "partial_or_different"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--select-only", action="store_true")
    a = ap.parse_args()
    origin_crawl = json.loads((SCRATCH / "crawl_origin.json").read_text())
    mirror_crawl = json.loads((SCRATCH / "crawl_mirror.json").read_text())
    db = open_ro()
    recs, chosen, extra, live_urls = pick_sample(origin_crawl, mirror_crawl, db, a.n)
    sample = [(c, why) for c, why in chosen.items()] + extra
    print(f"sample size {len(sample)}")
    for c, why in sample:
        print(" ", c, "-", why)
    if a.select_only:
        return
    cr = Crawler()
    s2c = spec_to_column()
    out = {"sample": [], "request_stats": None, "errors": []}
    try:
        for c, why in sample:
            rec = {"canon": c, "why": why}
            origin_url = live_urls.get(c, (None,))[0] or (recs[c]["db_url"] if c in recs else ORIGIN + c[:-5] + ".htm")
            if "navypedia.org" not in origin_url or "file:" in origin_url:
                origin_url = ORIGIN + c[:-5] + ".htm"
            mirror_url = MIRROR + c
            rec["origin_url"], rec["mirror_url"] = origin_url, mirror_url
            st, raw_o = cr.get(origin_url)
            rec["origin_status"] = st
            st2, raw_m = cr.get(mirror_url)
            rec["mirror_status"] = st2
            rec["country"] = recs[c]["country"] if c in recs else live_urls.get(c, (None, ""))[1]
            rec["in_db"] = c in recs
            if st != 200:
                out["sample"].append(rec)
                continue
            truth = parse_live(raw_o)
            rec["truth_summary"] = {"heading": truth["heading"], "n_specs": len(truth["specs"]), "spec_labels": list(truth["specs"]), "n_ships": len(truth["ships"]),
                                    "ship_headers": truth["ship_headers"], "n_blue_tables": truth["n_blue_tables"], "n_cwd_tables": truth["n_cwd_tables"],
                                    "h2_titles": truth["h2_titles"], "img_counts": truth["img_counts"], "has_photo": truth["has_photo_section"],
                                    "extra_blue_rows": truth.get("extra_blue_rows", [])[:6]}
            fl = run_fetcher_offline(raw_o, origin_url, rec["country"])
            fm = run_fetcher_offline(raw_m, mirror_url, rec["country"]) if st2 == 200 else None
            rec["fetcher_live_meta"] = {"class_name": fl["class_name"], "ship_type": fl["ship_type"], "when_date": fl["when_date"], "n_ships": len(fl["ships"])}
            if fm:
                rec["fetcher_mirror_meta"] = {"class_name": fm["class_name"], "ship_type": fm["ship_type"], "n_ships": len(fm["ships"]), "n_sections": len(fm["sections"])}
            if c in recs:
                d = db_rows_for(db, c)
                rec["db_meta"] = {"origin": {k: d["origin"][k] for k in ("CLASS_NAME", "TYPE", "source_url")} if d["origin"] else None,
                                  "mirror": {k: d["mirror"][k] for k in ("CLASS_NAME", "TYPE", "source_url")} if d["mirror"] else None,
                                  "origin_ship_rows": len(d["origin_ships"]), "mirror_ship_rows": len(d["mirror_ships"])}
                cmpres = compare_page(truth, d["origin"], fl, fm, d["origin_ships"], s2c)
                if d["origin"]:
                    rec["db_images"] = {"has_photo": bool(d["origin"]["has_photo"]), "has_images": bool(d["origin"]["has_images"]), "has_graphics": bool(d["origin"]["has_graphics"])}
            else:
                cmpres = compare_page(truth, None, fl, fm, [], s2c)
            rec["compare"] = cmpres
            out["sample"].append(rec)
    except StopCrawl as e:
        out["errors"].append(str(e))
        print("STOP", e)
    out["request_stats"] = cr.stats()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print("wrote", OUT, out["request_stats"])


if __name__ == "__main__":
    main()
