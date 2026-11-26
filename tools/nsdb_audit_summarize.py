#!/usr/bin/env python3
"""sample_pages.json を集計して docs/nsdb_audit/sample_summary.json を作る。"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "docs" / "nsdb_audit"


def main():
    s = json.loads((D / "sample_pages.json").read_text())
    pages = s["sample"]
    out = {"pages_total": len(pages), "pages_by_origin_status": dict(Counter(p["origin_status"] for p in pages)),
           "pages_by_mirror_status": dict(Counter(p["mirror_status"] for p in pages))}
    ok = [p for p in pages if p.get("compare")]
    in_db = [p for p in ok if p["in_db"]]
    out["pages_compared"] = len(ok)
    out["pages_compared_in_db"] = len(in_db)
    out["pages_not_in_db_but_on_site"] = len(ok) - len(in_db)

    # specs
    spec = defaultdict(Counter)  # source -> status
    spec_armour = defaultdict(Counter)
    for p in ok:
        for label, r in p["compare"]["specs"].items():
            for src in ("db", "fetcher_live", "fetcher_mirror"):
                if src == "db" and not p["in_db"]:
                    continue
                spec[src][r[src]] += 1
                if label.lower().startswith("armo"):
                    spec_armour[src][r[src]] += 1
    out["spec_cells"] = {k: dict(v) for k, v in spec.items()}
    out["spec_cells_armour_only"] = {k: dict(v) for k, v in spec_armour.items()}
    # 項目(label)別 db missing
    per_label = defaultdict(Counter)
    for p in in_db:
        for label, r in p["compare"]["specs"].items():
            per_label[label][r["db"]] += 1
    out["spec_db_by_label"] = {k: dict(v) for k, v in sorted(per_label.items())}
    # unmapped labels
    um = Counter()
    um_pages = Counter()
    for p in ok:
        seen = set()
        for label, val in p["compare"]["unmapped_labels"]:
            um[label] += 1
            seen.add(label)
    out["unmapped_labels_on_pages"] = um.most_common()
    # pages with spec label not in DB's schema but present
    # sections
    sec = defaultdict(Counter)
    sec_titles = defaultdict(lambda: defaultdict(Counter))
    numbered = Counter()
    for p in ok:
        for title, r in p["compare"]["sections"].items():
            base = title if not r["numbered_title"] else "Modernizations N. (numbered)"
            for src in ("db_fixed_column", "db_anywhere", "fetcher_live", "fetcher_mirror"):
                if src.startswith("db") and not p["in_db"]:
                    continue
                v = r.get(src)
                if v is None:
                    continue
                sec[src][v] += 1
                sec_titles[src][base][v] += 1
            if r["numbered_title"]:
                numbered[p["canon"]] += 1
    out["sections"] = {k: dict(v) for k, v in sec.items()}
    out["sections_by_title"] = {src: {t: dict(c) for t, c in d.items()} for src, d in sec_titles.items()}
    out["pages_with_numbered_modernizations_title"] = list(numbered)
    # ships
    sh = Counter()
    for p in ok:
        r = p["compare"]["ships"]
        sh["pages"] += 1
        sh["truth_rows"] += r["truth_rows"]
        sh["truth_rows_with_Comp_date"] += r["truth_with_comp"]
        sh["truth_blank_name_rows"] += r["truth_blank_name_rows"]
        if p["in_db"]:
            sh["db_pages"] += 1
            sh["db_truth_rows_for_db_pages"] += r["truth_rows"]
            sh["db_rows_total"] += r.get("db_rows", 0)
            sh["db_rows_distinct"] += r.get("db_distinct_rows", 0)
            sh["db_missing_vs_truth"] += r.get("db_missing_vs_truth", 0)
            sh["db_extra_vs_truth"] += r.get("db_extra_vs_truth", 0)
            if r.get("db_rows", 0) != r.get("db_distinct_rows", 0):
                sh["db_pages_with_dup_rows"] += 1
        sh["fetcher_live_rows"] += r.get("fetcher_live_rows", 0)
        sh["fetcher_live_missing_vs_truth"] += r.get("fetcher_live_missing_vs_truth", 0)
        sh["fetcher_mirror_rows"] += r.get("fetcher_mirror_rows", 0)
        sh["fetcher_mirror_missing_vs_truth"] += r.get("fetcher_mirror_missing_vs_truth", 0)
        sh["fetcher_mirror_extra_vs_truth"] += r.get("fetcher_mirror_extra_vs_truth", 0)
        if r["truth_rows"] and r.get("fetcher_live_rows", 0) == 0:
            sh["pages_where_fetcher_live_returns_0_ships_but_page_has_ships"] += 1
        if r["truth_rows"] and r.get("fetcher_mirror_rows", 0) == 0:
            sh["pages_where_fetcher_mirror_returns_0_ships_but_page_has_ships"] += 1
    out["ships"] = dict(sh)
    # class name / type
    nm = Counter()
    ex = []
    for p in ok:
        fl = p["fetcher_live_meta"]
        nm["pages"] += 1
        if p["in_db"] and p.get("db_meta", {}).get("origin"):
            dbn = p["db_meta"]["origin"]["CLASS_NAME"]
            heading = p["truth_summary"]["heading"]
            ex.append({"canon": p["canon"], "page_heading": heading, "db_CLASS_NAME": dbn, "db_TYPE": p["db_meta"]["origin"]["TYPE"],
                       "fetcher_live_class_name": fl["class_name"], "fetcher_live_type": fl["ship_type"],
                       "fetcher_mirror_class_name": (p.get("fetcher_mirror_meta") or {}).get("class_name"), "fetcher_mirror_type": (p.get("fetcher_mirror_meta") or {}).get("ship_type")})
    out["class_name_type_examples"] = ex
    # images
    im = Counter()
    for p in ok:
        c = p["truth_summary"]["img_counts"]
        if p["in_db"] and p.get("db_images"):
            im["pages"] += 1
            for k, db_k in (("photo", "has_photo"), ("standard scale images", "has_images"), ("graphics", "has_graphics")):
                n = c.get(k, 0)
                if n:
                    im[f"{k}_page_has_images"] += 1
                    if p["db_images"][db_k]:
                        im[f"{k}_db_has_first_image"] += 1
                    if n > 1:
                        im[f"{k}_page_has_multiple_images"] += 1
    out["images"] = dict(im)
    # 複数表
    out["pages_with_multiple_blue_or_cwd_tables"] = [p["canon"] for p in ok if p["truth_summary"]["n_blue_tables"] > 1 or p["truth_summary"]["n_cwd_tables"] > 1]
    out["extra_blue_rows_examples"] = [{"canon": p["canon"], "rows": p["truth_summary"]["extra_blue_rows"]} for p in ok if p["truth_summary"]["extra_blue_rows"]][:5]
    # extra h2 titles
    h2 = Counter()
    for p in ok:
        for t in p["truth_summary"]["h2_titles"]:
            h2[t] += 1
    out["h2_titles_on_sample_pages"] = h2.most_common()
    # 標本のうち『層化ランダム』だけでの率 (特例ページの偏りを除く)
    rnd = [p for p in ok if p["why"] == "層化ランダム"]
    r_spec = defaultdict(Counter)
    r_sec = defaultdict(Counter)
    r_ship = Counter()
    for p in rnd:
        for label, r in p["compare"]["specs"].items():
            for src in ("db", "fetcher_live", "fetcher_mirror"):
                r_spec[src][r[src]] += 1
        for title, r in p["compare"]["sections"].items():
            for src in ("db_fixed_column", "db_anywhere", "fetcher_live"):
                if r.get(src) is not None:
                    r_sec[src][r[src]] += 1
        sr = p["compare"]["ships"]
        r_ship["pages"] += 1
        r_ship["truth_rows"] += sr["truth_rows"]
        r_ship["db_rows"] += sr.get("db_rows", 0)
        r_ship["db_distinct"] += sr.get("db_distinct_rows", 0)
        r_ship["db_missing"] += sr.get("db_missing_vs_truth", 0)
    out["random_only"] = {"pages": len(rnd), "spec_cells": {k: dict(v) for k, v in r_spec.items()}, "sections": {k: dict(v) for k, v in r_sec.items()}, "ships": dict(r_ship)}
    pg_glue = sum(1 for p in in_db if any(r.get("db_fixed_column") == "ok_but_spaces_lost" for r in p["compare"]["sections"].values()))
    out["pages_with_spaces_lost_in_db_text"] = {"pages_in_db": len(in_db), "pages_with_glued_text": pg_glue}
    out["pages_not_reachable"] = [{"canon": p["canon"], "origin": p["origin_status"], "mirror": p["mirror_status"], "origin_url": p["origin_url"], "why": p["why"]} for p in pages if p["origin_status"] != 200]
    (D / "sample_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("class_name_type_examples", "sections_by_title", "spec_db_by_label", "h2_titles_on_sample_pages")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
