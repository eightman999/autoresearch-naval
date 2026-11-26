#!/usr/bin/env python3
"""NSDB 網羅性監査 A: DB 内部の点検 (ネットワーク不要 / NSDB は mode=ro)。

出力: docs/nsdb_audit/internal.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nsdb_audit_lib import canon_url, open_ro  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "nsdb_audit" / "internal.json"

SPEC_COLS = ["Displacement_normal", "Displacement_full", "Length", "Breadth", "Draught", "No_of_shafts",
             "Machinery", "Power", "speed", "Fuel", "Endurance", "Armour", "Armament",
             "Electronic_equipment", "Complement"]
TEXT_COLS = ["Project_history", "Modernizations", "Naval_service"]
IMG_COLS = ["Photo", "images", "Graphics"]
ALL_COLS = ["CLASS_NAME", "TYPE"] + SPEC_COLS + TEXT_COLS + IMG_COLS


def empty(v) -> bool:
    return v is None or str(v).strip() == ""


def pct(a, b):
    return round(100.0 * a / b, 2) if b else None


def host_kind(u):
    if not u:
        return "NULL"
    if "myownonpmirror.com" in u:
        return "mirror(.html)"
    if u.endswith(".htm"):
        return "navypedia.org(.htm)"
    return "navypedia.org(.html)"


def first_year(*texts):
    for t in texts:
        if not t:
            continue
        m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", t)
        if m:
            return int(m.group(1))
    return None


def era_of(y):
    if y is None:
        return "unknown"
    if y < 1860:
        return "<1860"
    if y < 1900:
        return "1860-1899"
    if y < 1920:
        return "1900-1919"
    if y < 1946:
        return "1920-1945"
    if y < 1970:
        return "1946-1969"
    if y < 2000:
        return "1970-1999"
    return "2000+"


def main():
    db = open_ro()
    res: dict = {"generated": datetime.now().isoformat(timespec="seconds")}

    cols_sql = ",".join(["rowid", "source_url", "COUNTRY", "fetched_at", "sections"] +
                        [c for c in ALL_COLS if c not in IMG_COLS] +
                        [f"length({c}) AS len_{c}" for c in IMG_COLS])
    cur = db.execute(f"SELECT {cols_sql} FROM CLASS ORDER BY rowid")
    names = [d[0] for d in cur.description]
    classes = [dict(zip(names, r)) for r in cur.fetchall()]
    n_class = len(classes)
    for c in classes:
        c["canon"] = canon_url(c["source_url"]) if c["source_url"] else None
        c["host_kind"] = host_kind(c["source_url"])

    cur = db.execute("SELECT rowid,NAME,CLASS,COUNTRY,NO,Yard_No,Builder,Laid_down,Launched,Fate_WHY,Fate_WHEN,TYPE,WHEN_DATE,source_url FROM SHIPS")
    names = [d[0] for d in cur.description]
    ships = [dict(zip(names, r)) for r in cur.fetchall()]
    n_ships = len(ships)
    for s in ships:
        s["canon"] = canon_url(s["source_url"]) if s["source_url"] else None
        s["host_kind"] = host_kind(s["source_url"])

    res["totals"] = {"CLASS": n_class, "SHIPS": n_ships,
                     "COUNTRIES": db.execute("select count(*) from COUNTRIES").fetchone()[0],
                     "TRANSLATIONS": db.execute("select count(*) from TRANSLATIONS").fetchone()[0],
                     "CACHE_SHIP_TYPES": db.execute("select count(*) from CACHE_SHIP_TYPES").fetchone()[0],
                     "CACHE_CLASS_URLS": db.execute("select count(*) from CACHE_CLASS_URLS").fetchone()[0]}

    # ---- A5/構成: source_url 種別 × fetched_at 日
    comp = Counter((c["host_kind"], (c["fetched_at"] or "NULL")[:10]) for c in classes)
    res["class_source_composition"] = [{"kind": k[0], "fetched_date": k[1], "rows": v} for k, v in sorted(comp.items())]
    comp = Counter(s["host_kind"] for s in ships)
    res["ships_source_composition"] = dict(comp)

    # ---- A1: 重複 (canon キー)
    by = defaultdict(list)
    for c in classes:
        if c["canon"]:
            by[c["canon"]].append(c)
    dup = {k: v for k, v in by.items() if len(v) > 1}
    dup_kinds = Counter(tuple(sorted(x["host_kind"] for x in v)) for v in dup.values())
    res["class_duplicates"] = {
        "distinct_canon_keys": len(by), "keys_with_dups": len(dup), "extra_rows": sum(len(v) - 1 for v in dup.values()),
        "kinds": {" + ".join(k): v for k, v in dup_kinds.items()},
        "null_source_url_rows": sum(1 for c in classes if not c["canon"]),
        "examples": [{"canon": k, "rows": [{"rowid": x["rowid"], "src": x["source_url"], "CLASS_NAME": x["CLASS_NAME"], "TYPE": x["TYPE"], "fetched_at": x["fetched_at"]} for x in v]} for k, v in list(dup.items())[:8]],
    }
    # mirror 行が origin 行より悪化している箇所
    mirror_rows = [c for c in classes if c["host_kind"] == "mirror(.html)"]
    worse = Counter()
    for m in mirror_rows:
        twin = [x for x in by[m["canon"]] if x is not m and x["host_kind"] != "mirror(.html)"]
        if not twin:
            worse["mirror_row_without_origin_twin"] += 1
            continue
        t = twin[0]
        if m["sections"] in ("{}", "", None) and t["sections"] not in ("{}", "", None):
            worse["mirror_sections_empty_but_origin_has"] += 1
        if empty(m["TYPE"]) and not empty(t["TYPE"]):
            worse["mirror_TYPE_empty_but_origin_has"] += 1
        if m["CLASS_NAME"] != t["CLASS_NAME"]:
            worse["CLASS_NAME_differs"] += 1
        if m["TYPE"] != t["TYPE"]:
            worse["TYPE_differs"] += 1
        for col in SPEC_COLS + TEXT_COLS:
            if empty(m[col]) and not empty(t[col]):
                worse[f"mirror_{col}_empty_but_origin_has"] += 1
    res["mirror_vs_origin_twin_rows"] = {"mirror_rows": len(mirror_rows), **dict(worse)}

    # SHIPS 重複 (同一 source_url 内で全項目同一の行 / mirror と origin 双方に同 canon)
    ships_by_canon = defaultdict(list)
    for s in ships:
        ships_by_canon[s["canon"]].append(s)
    ship_dup_pages = {k: v for k, v in ships_by_canon.items() if k and len({x["host_kind"] for x in v}) > 1}
    res["ships_duplicated_across_hosts"] = {"classes_with_ships_from_both_mirror_and_origin": len(ship_dup_pages),
                                            "ship_rows_involved": sum(len(v) for v in ship_dup_pages.values())}

    # ---- A1: cache 突合
    cc_urls = [r[0] for r in db.execute("select url from CACHE_CLASS_URLS")]
    cc_canon = {canon_url(u) for u in cc_urls}
    class_canon = set(by)
    ship_canon = {k for k in ships_by_canon if k}
    cst = db.execute("select country_url,name,url from CACHE_SHIP_TYPES").fetchall()
    cc_types = db.execute("select type_url,count(*) from CACHE_CLASS_URLS group by 1").fetchall()
    cst_urls = {canon_url(u) for _, _, u in cst}
    res["cache_crosscheck"] = {
        "CACHE_CLASS_URLS_distinct": len(cc_canon),
        "CACHE_CLASS_URLS_without_CLASS_row": sorted(cc_canon - class_canon)[:50],
        "CACHE_CLASS_URLS_without_CLASS_row_count": len(cc_canon - class_canon),
        "CACHE_CLASS_URLS_without_SHIPS_count": len(cc_canon - ship_canon),
        "CACHE_CLASS_URLS_countries": sorted({p.split("/")[2] for p in cc_canon}),
        "CACHE_SHIP_TYPES_countries": sorted({canon_url(u).split("/")[2] for u, _, _ in cst}),
        "CACHE_SHIP_TYPES_rows": len(cst),
        "type_pages_in_CACHE_SHIP_TYPES": len(cst_urls),
        "type_pages_with_CACHE_CLASS_URLS": len({canon_url(t) for t, _ in cc_types}),
        "type_pages_in_CACHE_SHIP_TYPES_without_CACHE_CLASS_URLS": sorted(cst_urls - {canon_url(t) for t, _ in cc_types})[:40],
        "note": "CACHE_* は Abkhazia..Azerbaijan(11カ国, ミラー) と Japan(navypedia.org由来)のみ。全体の発見済みURL集合としては使えない。",
    }

    # ---- A1: CLASS ↔ SHIPS
    class_no_ships = [c for c in by if c not in ship_canon]
    ships_no_class = Counter(canon for canon in ship_canon if canon not in class_canon)
    ships_null_src = sum(1 for s in ships if not s["canon"])
    class_ship_cnt = {k: len(ships_by_canon.get(k, [])) for k in by}
    res["class_vs_ships"] = {
        "class_pages_distinct": len(by),
        "class_pages_with_zero_ships": len(class_no_ships),
        "pct_class_pages_zero_ships": pct(len(class_no_ships), len(by)),
        "ships_source_canon_distinct": len(ship_canon),
        "ships_source_not_in_CLASS_pages": len(ships_no_class),
        "ships_rows_whose_source_not_in_CLASS": sum(len(ships_by_canon[k]) for k in ships_no_class),
        "ships_rows_null_source_url": ships_null_src,
        "ships_not_in_CLASS_examples": sorted(ships_no_class)[:20],
        "ship_count_per_class_hist": dict(sorted(Counter(min(v, 30) for v in class_ship_cnt.values()).items())),
    }
    zero_by_country = Counter()
    zero_by_type = Counter()
    zero_examples = []
    cls_by_canon = {k: v[0] for k, v in by.items()}
    for k in class_no_ships:
        c = cls_by_canon[k]
        zero_by_country[c["COUNTRY"]] += 1
        zero_by_type[c["TYPE"] or "(empty)"] += 1
        if len(zero_examples) < 25:
            zero_examples.append({"canon": k, "CLASS_NAME": c["CLASS_NAME"], "COUNTRY": c["COUNTRY"], "TYPE": c["TYPE"], "host": c["host_kind"]})
    res["class_vs_ships"]["zero_ship_classes_by_country_top"] = zero_by_country.most_common(25)
    res["class_vs_ships"]["zero_ship_classes_by_type_top"] = zero_by_type.most_common(25)
    res["class_vs_ships"]["zero_ship_class_examples"] = zero_examples
    res["class_vs_ships"]["zero_ship_class_by_host"] = dict(Counter(cls_by_canon[k]["host_kind"] for k in class_no_ships))

    # SHIPS.CLASS が CLASS.CLASS_NAME(同 country) に無い
    cname = {(c["COUNTRY"], c["CLASS_NAME"]) for c in classes}
    miss = Counter()
    for s in ships:
        if (s["COUNTRY"], s["CLASS"]) not in cname:
            miss[(s["COUNTRY"], s["CLASS"])] += 1
    res["class_vs_ships"]["ships_CLASS_name_not_in_CLASS_table"] = {"distinct_pairs": len(miss), "rows": sum(miss.values()), "examples": [list(k) + [v] for k, v in miss.most_common(10)]}

    # ---- 国別 / 艦種別 件数
    ship_by_country = Counter(s["COUNTRY"] for s in ships)
    class_by_country = Counter(c["COUNTRY"] for c in classes)
    cs = db.execute("select Short,Long,URL from COUNTRIES").fetchall()
    country_rows = []
    for short, long_, url in cs:
        country_rows.append({"Short": short, "Long": long_, "URL": url, "classes": class_by_country.get(short, 0), "ships": ship_by_country.get(short, 0)})
    res["countries_table"] = sorted(country_rows, key=lambda r: -r["classes"])
    res["countries_without_class"] = [r for r in country_rows if r["classes"] == 0]
    res["class_countries_not_in_COUNTRIES"] = sorted(set(class_by_country) - {s for s, _, _ in cs})
    res["by_type_top50"] = Counter(c["TYPE"] or "(empty)" for c in classes).most_common(50)
    res["by_type_distinct"] = len({c["TYPE"] for c in classes})

    # ---- A3: 列ごと空率 (全体 / 国別 / 年代別 / ソース別)
    def empty_rate(rows):
        n = len(rows)
        out = {"n": n}
        for col in ALL_COLS:
            if col in IMG_COLS:
                e = sum(1 for r in rows if not r.get(f"len_{col}"))
            else:
                e = sum(1 for r in rows if empty(r[col]))
            out[col] = pct(e, n)
        return out

    res["empty_rate_overall"] = empty_rate(classes)
    res["empty_rate_by_source"] = {k: empty_rate([c for c in classes if c["host_kind"] == k]) for k in sorted({c["host_kind"] for c in classes})}

    # 年代: SHIPS の WHEN_DATE / Laid_down / Launched から
    ys = {}
    for k, v in ships_by_canon.items():
        if not k:
            continue
        y = first_year(*(s["WHEN_DATE"] for s in v))
        if y is None:
            cand = [first_year(s["Laid_down"], s["Launched"]) for s in v]
            cand = [x for x in cand if x]
            y = min(cand) if cand else None
        ys[k] = y
    for c in classes:
        c["era"] = era_of(ys.get(c["canon"]))
    eras = ["<1860", "1860-1899", "1900-1919", "1920-1945", "1946-1969", "1970-1999", "2000+", "unknown"]
    res["empty_rate_by_era"] = {e: empty_rate([c for c in classes if c["era"] == e]) for e in eras}
    byc = defaultdict(list)
    for c in classes:
        byc[c["COUNTRY"]].append(c)
    res["empty_rate_by_country"] = {k: empty_rate(v) for k, v in sorted(byc.items(), key=lambda kv: -len(kv[1]))}

    # ---- A3: Armour と sections
    sec_keys = Counter()
    sec_nonempty = Counter()
    sec_per_class = []
    sections_bad = 0
    norm = lambda k: re.sub(r"\s*\d+\.\s*$", "", k.strip())  # 'Modernizations 9.' -> 'Modernizations'
    numbered_mod = 0
    protection_classes = 0
    protection_keys = Counter()
    heading_junk = 0
    for c in classes:
        s = c["sections"]
        if not s:
            continue
        try:
            d = json.loads(s)
        except Exception:
            sections_bad += 1
            continue
        sec_per_class.append(len(d))
        has_prot = False
        for k, v in d.items():
            sec_keys[k] += 1
            if v:
                sec_nonempty[k] += 1
            if re.search(r"protection|armou?r", k, re.I):
                protection_keys[k] += 1
                has_prot = True
        if has_prot:
            protection_classes += 1
        if any(re.match(r"Modernizations\s+\d+\.?\s*$", k) for k in d):
            numbered_mod += 1
        if any(k.isupper() or k.strip() == "" for k in d):
            heading_junk += 1
    std = {"Project history", "Naval service", "Modernizations"}
    res["sections"] = {
        "classes_with_nonempty_sections_json": sum(1 for c in classes if c["sections"] not in (None, "", "{}")),
        "classes_with_sections_{}": sum(1 for c in classes if c["sections"] == "{}"),
        "classes_with_sections_NULL": sum(1 for c in classes if c["sections"] is None),
        "bad_json": sections_bad,
        "distinct_keys": len(sec_keys),
        "key_counts_top40": [[k, n, sec_nonempty[k]] for k, n in sec_keys.most_common(40)],
        "keys_not_mapped_to_fixed_columns_top30": [[k, n] for k, n in sec_keys.most_common() if norm(k) not in std][:30],
        "classes_with_protection_like_key": protection_classes,
        "protection_like_keys_top": protection_keys.most_common(12),
        "classes_with_numbered_Modernizations_keys": numbered_mod,
        "classes_with_uppercase_or_blank_heading_keys": heading_junk,
        "hist_keys_per_class": dict(sorted(Counter(sec_per_class).items())),
    }
    # Armour 以外の固定列に装甲が混入している兆候 (Armament/Machinery 等の値に 'belt' 等)
    arm_like = Counter()
    pat = re.compile(r"\b(belt|barbettes?|turrets?\s*:|conning tower|deck\s*:|bulkheads?)\b", re.I)
    ex = []
    for c in classes:
        for col in ("Armament", "Machinery", "Electronic_equipment", "Complement", "Project_history"):
            if not empty(c[col]) and pat.search(str(c[col])):
                arm_like[col] += 1
                if len(ex) < 6 and col != "Project_history":
                    ex.append({"canon": c["canon"], "col": col, "val": str(c[col])[:160]})
    res["armour"] = {
        "Armour_nonempty_rows": sum(1 for c in classes if not empty(c["Armour"])),
        "Armour_empty_rows": sum(1 for c in classes if empty(c["Armour"])),
        "classes_with_Ship_protection_section": sum(1 for c in classes if c["sections"] and '"Ship protection"' in c["sections"]),
        "armour_like_words_in_other_columns": dict(arm_like),
        "examples": ex,
        "SPEC_TO_COLUMN_keys_note": "fetcher.py の SPEC_TO_COLUMN は 'Armor','Armour','Armor, mm','Armour, mm' を Armour に写す。2026-02-05 取得分は空 → 当時の版で未対応、または取得ページに th/td 形式の Armour 行が無かった可能性。B 節の標本で判定。",
    }

    # ---- A4: 壊れ/重複行
    anom = Counter()
    ex = defaultdict(list)

    def add(k, c):
        anom[k] += 1
        if len(ex[k]) < 6:
            ex[k].append({"rowid": c["rowid"], "CLASS_NAME": c["CLASS_NAME"], "TYPE": c["TYPE"], "COUNTRY": c["COUNTRY"], "src": c["source_url"]})

    spec_empty_all = [c for c in classes if all(empty(c[x]) for x in SPEC_COLS)]
    for c in classes:
        n = c["CLASS_NAME"] or ""
        if not n.strip():
            add("CLASS_NAME_empty", c)
        if empty(c["TYPE"]):
            add("TYPE_empty", c)
        if re.search(r"\)\s*Class$", n) and "(" not in n:
            add("CLASS_NAME_unbalanced_paren_tail", c)
        if re.search(r"\s-\s.*\(", n) or "Navy" in n:
            add("CLASS_NAME_contains_navy_context_or_dash", c)
        if len(n) > 60:
            add("CLASS_NAME_len_gt_60", c)
        if re.search(r"-(?:s-)?[a-z]+-navy|-navy-|-force|-guard", c["TYPE"] or ""):
            add("TYPE_contains_navy_or_country_tokens", c)
        if empty(c["TYPE"]) is False and len(c["TYPE"]) > 45:
            add("TYPE_len_gt_45", c)
        if all(empty(c[x]) for x in SPEC_COLS):
            add("all_15_spec_columns_empty", c)
        if empty(c["Project_history"]) and empty(c["Modernizations"]) and empty(c["Naval_service"]):
            add("all_3_text_sections_empty", c)
    res["anomalies"] = {k: {"rows": v, "pct": pct(v, n_class), "examples": ex[k]} for k, v in anom.items()}
    tbl_types = Counter(c["TYPE"] for c in classes if empty(c["TYPE"]) is False)
    res["anomalies"]["TYPE_values_with_1_class"] = sum(1 for v in tbl_types.values() if v == 1)
    res["anomalies"]["TYPE_values_total"] = len(tbl_types)
    sh_anom = Counter()
    sh_ex = defaultdict(list)
    for s in ships:
        if empty(s["NAME"]):
            sh_anom["NAME_empty"] += 1
            if empty(s["NO"]) and empty(s["Yard_No"]) and empty(s["Builder"]) and empty(s["Laid_down"]) and empty(s["Launched"]):
                sh_anom["NAME_and_all_other_id_cols_empty"] += 1
            if len(sh_ex["NAME_empty"]) < 5:
                sh_ex["NAME_empty"].append({k: s[k] for k in ("NAME", "CLASS", "NO", "Builder", "source_url")})
        if empty(s["CLASS"]):
            sh_anom["CLASS_empty"] += 1
        if empty(s["TYPE"]):
            sh_anom["TYPE_empty"] += 1
        if empty(s["source_url"]):
            sh_anom["source_url_null"] += 1
        if empty(s["Builder"]):
            sh_anom["Builder_empty"] += 1
        if empty(s["Laid_down"]):
            sh_anom["Laid_down_empty"] += 1
        if empty(s["Launched"]):
            sh_anom["Launched_empty"] += 1
    # 完全同一の SHIPS 行 (source_url ごと)
    seen = Counter()
    for s in ships:
        seen[(s["canon"], s["NAME"], s["NO"], s["Yard_No"], s["Builder"], s["Laid_down"], s["Launched"], s["Fate_WHY"], s["Fate_WHEN"])] += 1
    exact_dup_rows = sum(v - 1 for v in seen.values() if v > 1)
    res["ships_anomalies"] = {**dict(sh_anom), "pct_NAME_empty": pct(sh_anom["NAME_empty"], n_ships), "exact_duplicate_rows_same_canon": exact_dup_rows, "examples": dict(sh_ex),
                              "note_SID": "SID は 11 行のみ非NULL (旧版の連番)。SHIPS には Comp(竣工日) 列が無く取得しても保存されない。"}
    # SHIPS の重複: 同一 source_url 内の完全一致行 (取得時の二重取り込み) と、ホスト違い(mirror+origin)の二重
    key8 = lambda s: (s["NAME"], s["NO"], s["Yard_No"], s["Builder"], s["Laid_down"], s["Launched"], s["Fate_WHY"], s["Fate_WHEN"])
    per_url = defaultdict(Counter)
    for s in ships:
        per_url[s["source_url"]][key8(s)] += 1
    excess_by_url = {u: sum(v - 1 for v in c.values()) for u, c in per_url.items()}
    dup_urls = {u: e for u, e in excess_by_url.items() if e > 0}
    tot_dup_rows = sum(dup_urls.values())
    top = sorted(dup_urls.items(), key=lambda kv: -kv[1])[:15]
    res["ships_duplicates"] = {
        "rows_total": n_ships,
        "rows_distinct_per_source_url": n_ships - tot_dup_rows,
        "excess_rows_exact_dup_within_same_source_url": tot_dup_rows,
        "pct_excess": pct(tot_dup_rows, n_ships),
        "classes_with_exact_dup_rows": len(dup_urls),
        "distinct_rows_with_blank_NAME": sum(1 for u, c in per_url.items() for k in c if empty(k[0])),
        "pct_distinct_rows_with_blank_NAME": pct(sum(1 for u, c in per_url.items() for k in c if empty(k[0])), n_ships - tot_dup_rows),
        "excess_rows_in_top_10_classes": sum(e for _, e in top[:10]),
        "top15": [{"source_url": u, "rows": sum(per_url[u].values()), "distinct": len(per_url[u]), "excess": e} for u, e in top],
        "note": "同一 source_url 内で 8 列(NAME,NO,Yard_No,Builder,Laid_down,Launched,Fate_WHY,Fate_WHEN)が完全一致する行。"
                "番号艇の連番で名前も欠けるクラス(例 ru_cf_r141)では実在の同形行がある可能性もあるが、重複倍率が 100 倍超のものは取り込み時の行重複(ネスト表の二重走査等)の疑いが濃い。B 節で実ページと照合。",
    }
    # 『全行が id 6 列すべて空』= 実質ひとつも個艦が取れていないクラス
    per_page_blank = defaultdict(lambda: [0, 0])
    for s in ships:
        if not s["source_url"]:
            continue
        pb = per_page_blank[s["source_url"]]
        pb[1] += 1
        if all(empty(s[k]) for k in ("NAME", "NO", "Yard_No", "Builder", "Laid_down", "Launched")):
            pb[0] += 1
    all_blank_pages = [u for u, (a, n) in per_page_blank.items() if a == n]
    res["ships_duplicates"]["class_pages_whose_ship_rows_are_all_blank_id_rows"] = len(all_blank_pages)
    res["ships_duplicates"]["pct_of_class_pages"] = pct(len(all_blank_pages), len(by))
    res["ships_duplicates"]["note_all_blank"] = ("行頭ヘッダが Names/Builders/Completed|Commissioned/Losses/Transfers/Discarding の『群れ要約表』を持つページ。"
                                                  "標準の 8 列(Name…Fate)表ではないため個艦が 1 行も取れず、全項目空の 1 行が残る (docs/nsdb_audit/ship_table_variants.json で実ページ 10/10 件確認)。")
    # NAME 空の上位クラス
    name_empty_by_class = Counter(s["canon"] for s in ships if empty(s["NAME"]))
    res["ships_anomalies"]["NAME_empty_top_classes"] = [[k, v, class_ship_cnt.get(k)] for k, v in name_empty_by_class.most_common(10)]

    # ---- A5: fetched_at 分布
    fa = [(c["rowid"], c["COUNTRY"], c["fetched_at"]) for c in classes if c["fetched_at"]]
    dt = [(r, c, datetime.fromisoformat(f)) for r, c, f in fa]
    runs = []
    cur_run = None
    for r, c, t in dt:
        if cur_run and (t - cur_run["end"]).total_seconds() <= 600:
            cur_run["end"] = t
            cur_run["rows"] += 1
            cur_run["countries"].append(c) if cur_run["countries"][-1] != c else None
        else:
            if cur_run:
                runs.append(cur_run)
            cur_run = {"start": t, "end": t, "rows": 1, "countries": [c], "first_rowid": r}
    runs.append(cur_run)
    res["fetch_runs"] = [{"start": r["start"].isoformat(), "end": r["end"].isoformat(), "rows": r["rows"], "first_rowid": r["first_rowid"],
                          "countries_n": len(set(r["countries"])), "first_country": r["countries"][0], "last_country": r["countries"][-1]} for r in runs]
    res["fetched_at_by_day"] = dict(Counter(f[:10] for _, _, f in fa))
    res["fetched_at_null_rows"] = n_class - len(fa)
    # 国ごとの取得日(多数日)
    cday = defaultdict(Counter)
    for _, c, f in fa:
        cday[c][f[:10]] += 1
    res["countries_fetched_on_multiple_days"] = {c: dict(v) for c, v in cday.items() if len(v) > 1}
    # 国の並び(rowid順)で Feb 5 本走行が A→Z を通したか
    feb = [c for c in classes if (c["fetched_at"] or "").startswith("2026-02-05") and c["rowid"] >= 680]
    order, prev = [], None
    for c in feb:
        if c["COUNTRY"] != prev:
            order.append(c["COUNTRY"])
            prev = c["COUNTRY"]
    res["feb05_full_run_country_order_first_last"] = [order[0], order[-1], len(order)]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
