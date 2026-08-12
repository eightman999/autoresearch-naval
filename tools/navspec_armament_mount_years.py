#!/usr/bin/env python3
"""`mounts.jsonl` の `year_introduced` を NSDB から導出する。

背景: armament DB の砲塔レコードは `year_introduced` が全件 null で、
そのせいで `armament.mount_unavailable_for_era` が構造的に踏めない
(`docs/navspec_armament_stage4a_teacher.md` §8-9)。

キャッシュ済み navweaps HTML には砲塔固有の就役年が無い。一方 NSDB
(`nsdb/NSDB.db`) の `CLASS.Armament` は

    3 x 3 - 406/45 Mk 6+ 10 x 2 - 127/38 Mk 12+ ...

という形式で、**砲塔数・連装数・口径・砲身長・形式記号**をすべて持っている。
これを armament DB の砲塔レコードと突合すれば、その砲塔を積んだ艦級が特定でき、
艦級の年をもって砲塔の就役年とできる。

同名異艦の罠: 対象国では艦名が世代をまたいで再利用されている
(扶桑 1877/1915、Tennessee 1904/1919、Brooklyn 1895/1936)。
**艦級名では突合しない。** 口径と連装数で絞り、形式記号と砲身長で順位付けする。

出典は必ずレコードに残す (`year_source`)。DB の慣行どおり `verified` は
false のままにし、人手照合で true に上げる。

使用法::

    # 何が入るか見るだけ（既定）
    uv run python tools/navspec_armament_mount_years.py

    # レビュー用のTSVを書き出す
    uv run python tools/navspec_armament_mount_years.py --report /tmp/mount_years.tsv

    # mounts.jsonl に反映する
    uv run python tools/navspec_armament_mount_years.py --apply
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / "data" / "navspec" / "armament"
DEFAULT_NSDB = ROOT / "nsdb" / "NSDB.db"

NATION_TO_COUNTRY = {
    "JP": "Japan",
    "UK": "United-Kingdom",
    "US": "United-States",
    "DE": "Germany",
    "FR": "France",
    "IT": "Italy",
    "RU": "Russian",
    "AH": "Austria-Hungary",
    "ES": "Spain",
    "SE": "Sweden",
    "NL": "Netherlands",
}

# "3 x 3 - 406/45 Mk 6" → (mounts, barrels, caliber_mm, length_cal, designation)
ARMAMENT_ENTRY = re.compile(
    r"(\d+)\s*x\s*(\d+)\s*-\s*(\d+(?:\.\d+)?)\s*/\s*(\d+)\s*([^+]*)"
)

CALIBER_TOLERANCE_MM = 4.0  # 同じ砲でも出典により 380/381、283/280 と揺れる

# NSDB の艦級年と navweaps の砲の就役年は系統的にずれる。
# 実測（56件）では差の66%が ±2年 に収まり、そこに明確な山がある
# （中央値 +1、-2〜+2 が37件）。一方 +3年以上には別の山があり、
# こちらは「砲は先にあったが、この砲塔形式は後から」という実信号とみなせる。
# よって ±2年以内はノイズとして砲の年に寄せ、+3年以上だけを採用する。
# 砲より前に砲塔が就役することは有り得ないので、負側は突合ミスとして砲の年に寄せる。
YEAR_NOISE_TOLERANCE = 2


def strip_accents(s: Any) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c))


def key(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", strip_accents(s).lower())


def load_nsdb_classes(db_path: Path) -> list[dict[str, Any]]:
    """NSDB の艦級を、装備を解析した形で返す。

    年は艦級名に埋まっていればそれを使い (`IOWA1944) Class` → 1944)、
    無ければ所属艦の進水年の最小値で補う (`NORTHCAROLINA Class` など)。
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        ship_years: dict[str, list[int]] = collections.defaultdict(list)
        for cls, launched, when in con.execute(
            "select CLASS, Launched, WHEN_DATE from SHIPS "
            "where CLASS is not null and CLASS != ''"
        ):
            for field in (launched, when):
                if not field:
                    continue
                m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", str(field))
                if m:
                    ship_years[cls].append(int(m.group(1)))
                    break

        out: list[dict[str, Any]] = []
        for name, ship_type, country, armament in con.execute(
            "select CLASS_NAME, TYPE, COUNTRY, Armament from CLASS "
            "where Armament is not null and Armament != ''"
        ):
            m = re.search(r"(1[89]\d{2}|20\d{2})", str(name))
            if m:
                year, year_from = int(m.group(1)), "class_name"
            elif ship_years.get(name):
                year, year_from = min(ship_years[name]), "ship_launch"
            else:
                continue
            entries = [
                {
                    "mount_count": int(a),
                    "barrels": int(b),
                    "caliber_mm": float(c),
                    "length_cal": int(d),
                    "designation": strip_accents(g).strip(),
                }
                for a, b, c, d, g in ARMAMENT_ENTRY.findall(strip_accents(armament))
            ]
            if entries:
                out.append({
                    "class_name": name, "ship_type": ship_type, "country": country,
                    "year": year, "year_from": year_from, "entries": entries,
                })
        return out
    finally:
        con.close()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def weapon_form(weapon_id: str) -> str:
    """'US_16INCH_TRIPLE_MK6' ではなく weapon_id 'US_16INCH_MK6' の末尾要素を返す。"""
    parts = weapon_id.split("_", 2)
    return parts[2] if len(parts) >= 3 else ""


def match_mount(mount: dict, weapon: dict, classes: list[dict]) -> list[dict]:
    """砲塔に合致する艦級を、確からしい順に返す。

    絞り込み: 同一国 / 連装数一致 / 口径が ±4mm
    順位付け: 形式記号が一致 → 砲身長が近い → 砲の就役年に近い → 古い順
    """
    caliber = weapon.get("caliber_mm")
    length = weapon.get("barrel_length_calibers")
    barrels = mount.get("barrels_per_mount")
    country = NATION_TO_COUNTRY.get(mount.get("nation"))
    form = key(weapon_form(weapon["weapon_id"]))
    weapon_year = weapon.get("year_introduced")
    if not (caliber and country):
        return []

    hits: list[dict] = []
    for cls in classes:
        if cls["country"] != country:
            continue
        for entry in cls["entries"]:
            if barrels and entry["barrels"] != barrels:
                continue
            if abs(entry["caliber_mm"] - caliber) > CALIBER_TOLERANCE_MM:
                continue
            desig = key(entry["designation"])
            hits.append({
                "designation_matched": bool(form and desig and form in desig),
                "length_delta": abs(entry["length_cal"] - length) if length else None,
                "year": cls["year"],
                "class_name": cls["class_name"],
                "class_type": cls["ship_type"],
                "year_from": cls["year_from"],
                "entry": (
                    f"{entry['mount_count']} x {entry['barrels']} - "
                    f"{entry['caliber_mm']:g}/{entry['length_cal']} "
                    f"{entry['designation']}".strip()
                ),
            })
            break

    def rank(h: dict) -> tuple:
        return (
            0 if h["designation_matched"] else 1,
            h["length_delta"] if h["length_delta"] is not None else 99,
            abs(h["year"] - weapon_year) if weapon_year else 0,
            h["year"],
        )

    hits.sort(key=rank)
    return hits


def settle_year(derived: int, weapon_year: int | None) -> int:
    """NSDB 由来の年を、砲の就役年と突き合わせて確定させる。

    - 砲より前 → 突合ミス。砲の年に寄せる（砲より前に砲塔は就役しない）
    - 差が ±2年以内 → 出典間のノイズ。砲の年に寄せる
    - +3年以上 → 実信号として採用する
    """
    if weapon_year is None:
        return derived
    if derived <= weapon_year + YEAR_NOISE_TOLERANCE:
        return weapon_year
    return derived


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    p.add_argument("--nsdb", type=Path, default=DEFAULT_NSDB)
    p.add_argument("--report", type=Path, help="レビュー用TSVの出力先")
    p.add_argument("--apply", action="store_true",
                   help="mounts.jsonl に year_introduced と year_source を書き込む")
    p.add_argument("--only-designation-match", action="store_true",
                   help="形式記号まで一致したものだけを採用する（保守的）")
    args = p.parse_args(argv)

    if not args.nsdb.exists():
        print(f"ERROR: NSDB が見つかりません: {args.nsdb}", file=sys.stderr)
        return 1

    classes = load_nsdb_classes(args.nsdb)
    weapons = {w["weapon_id"]: w for w in load_jsonl(args.data_dir / "weapons.jsonl")}
    mounts = load_jsonl(args.data_dir / "mounts.jsonl")

    resolved = 0
    exact = 0
    report_lines = [
        "mount_id\tweapon_year\tmount_year\traw_class_year\tdesignation_matched\t"
        "class_name\tclass_type\tnsdb_entry\tyear_from\tcandidates"
    ]
    for mount in mounts:
        # scraper の手動形式補正（例: Kirov MK-3-180=1938）は NSDB の
        # クラス名マッチより優先し、再適用で上書きしない。
        if str(mount.get("year_source", "")).startswith(("manual:", "Kirov class commissioning year")):
            weapon = weapons.get(mount.get("weapon_id"))
            if weapon and mount.get("year_introduced") is not None:
                # 手動補正もレビュー表から欠落させず、NSDB 自動照合と区別して記録する。
                report_lines.append(
                    f"{mount['mount_id']}\t{weapon.get('year_introduced')}\t"
                    f"{mount.get('year_introduced')}\t\tmanual\t\t\t\tmanual"
                )
                resolved += 1
            continue
        weapon = weapons.get(mount.get("weapon_id"))
        if not weapon:
            continue
        hits = match_mount(mount, weapon, classes)
        if args.only_designation_match:
            hits = [h for h in hits if h["designation_matched"]]
        if not hits:
            report_lines.append(
                f"{mount['mount_id']}\t{weapon.get('year_introduced')}\t\t\t\t\t\t\t\t0")
            continue
        best = hits[0]
        resolved += 1
        if best["designation_matched"]:
            exact += 1
        best["raw_year"] = best["year"]
        best["year"] = settle_year(best["year"], weapon.get("year_introduced"))
        report_lines.append(
            f"{mount['mount_id']}\t{weapon.get('year_introduced')}\t{best['year']}\t"
            f"{best['raw_year']}\t{best['designation_matched']}\t"
            f"{best['class_name']}\t{best['class_type']}\t"
            f"{best['entry']}\t{best['year_from']}\t{len(hits)}"
        )
        if args.apply:
            mount["year_introduced"] = best["year"]
            mount["year_source"] = (
                f"NSDB CLASS {best['class_name']!r} ({best['entry']}); "
                f"class year {best['raw_year']} from {best['year_from']}; "
                f"settled to {best['year']} against weapon year "
                f"{weapon.get('year_introduced')}; "
                f"designation_matched={best['designation_matched']}; "
                f"candidates={len(hits)}"
            )

    print(f"砲塔 {len(mounts)}件中 {resolved}件で就役年を決定")
    print(f"  うち砲の形式記号まで一致: {exact}件")
    print(f"  未決定: {len(mounts) - resolved}件")

    if args.report:
        args.report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"レビュー用TSV: {args.report}")

    if args.apply:
        path = args.data_dir / "mounts.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for mount in mounts:
                f.write(json.dumps(mount, ensure_ascii=False) + "\n")
        print(f"書き込み: {path}")
        print("verified は false のまま。人手照合が済んだものから true に上げること。")
    else:
        print("（--apply を付けるまで mounts.jsonl は書き換えません）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
