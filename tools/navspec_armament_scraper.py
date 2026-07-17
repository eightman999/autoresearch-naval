"""Stage 4 Commit A: navweaps.com からの主砲データ機械的スクレイピング.

STAGE4_PROGRESS.md §2 Commit A の DB seed 収集フェーズ。
progress §4 の代表的主砲候補リストを起点に、6カ国 / 76-460mm / 59件を
navweaps.com の個別武器ページから機械的に抽出し、
``data/navspec/armament/weapons.jsonl`` を生成する。

設計:
- 対象 URL リストは本ファイル内の TARGETS に固定 (URL パターンは既知)。
- 各ページの<table>を行単位で走査し、最初の列をキー名とみなして
  正規表現でマッチした行の2列目から数値を抽出する。
- 構造の揺らぎ (Designation 行の有無など) に強いよう、複数フォールバックを持つ。
- 404 や抽出失敗はスキップし、manifest に status を記録して再実行可能にする。

使用法::

    uv run python tools/navspec_armament_scraper.py \
        --out data/navspec/armament/weapons.jsonl \
        --manifest data/navspec/armament/weapons_manifest.json \
        [--limit 1]  # プロトタイプ実行用

注: ``robots.txt`` は存在せず (404)、1ページあたりの取得間隔は
``--sleep`` (規定1.5秒) で配慮する。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

USER_AGENT = "navspec-research-bot/0.1 (academic research; autoresearch-naval)"
BASE_URL = "https://www.navweaps.com/Weapons/"


@dataclass
class WeaponTarget:
    """スクレイピング対象1件の定義."""

    weapon_id: str
    nation: str  # JP / US / UK / DE / FR / IT
    display_name: str  # 人間向け名称 (例: "46 cm/45 Type 94 (Yamato)")
    url: str
    # ページから機械抽出困難な属性は target 側で指定する
    dual_purpose: bool = False
    technology_tier: str = "mainstream"
    notes_hint: str = ""
    # 抽出値の補正 ( navweaps 表記ゆれや本文中のみの数値に対する上書き )
    caliber_override: int | None = None
    max_range_override: int | None = None
    mount_type_override: str | None = None


# progress §3 の id 体系例に従う:
#   - JP/FR/IT: {NATION}_{CALIBER_MM}_{FORM}
#   - UK/US  : {NATION}_{CALIBER_INCH}_{FORM}
#   - DE     : {NATION}_{CALIBER_CM}_{FORM}
# progress §4 の候補リスト35件と、巡洋艦砲・駆逐艦砲・両用砲の
# 追加候補24件を navweaps URL パターンへマップ。
TARGETS: list[WeaponTarget] = [
    # --- Japan (mm 表記) ---
    WeaponTarget(
        "JP_460MM_94SHIKI", "JP", "46 cm/45 Type 94 (Yamato)",
        BASE_URL + "WNJAP_18-45_t94.php",
        notes_hint="Actual 46cm; official designation 40 cm/45 to mask size.",
    ),
    WeaponTarget(
        "JP_410MM_3SHIKI", "JP", "41 cm/45 3rd Year Type (Nagato)",
        BASE_URL + "WNJAP_161-45_3ns.php",
    ),
    WeaponTarget(
        "JP_203MM_3SHIKI2GO", "JP", "20 cm/50 3rd Year Type No.2 (Takao/Mogami)",
        BASE_URL + "WNJAP_8-50_3ns.php",
        # navweaps 表記は "20 cm" だが実口径は 8 inch = 20.32 cm = 203 mm (二号二十粁砲)
        caliber_override=203,
    ),
    WeaponTarget(
        "JP_155MM_3SHIKI", "JP", "15.5 cm/60 3rd Year Type (Mogami/Yamato secondary)",
        BASE_URL + "WNJAP_61-60_3ns.php",
    ),
    WeaponTarget(
        "JP_127MM_3SHIKI", "JP", "12.7 cm/50 3rd Year Type (Fubuki/Special Type)",
        BASE_URL + "WNJAP_5-50_3ns.php",
    ),
    WeaponTarget(
        "JP_100MM_98SHIKI", "JP", "10 cm/65 Type 98 (Akizuki/Oyodo)",
        BASE_URL + "WNJAP_39-65_t98.php",
        dual_purpose=True,
    ),
    WeaponTarget(
        "JP_140MM_3SHIKI", "JP", "14 cm/50 3rd Year Type (Kongo secondary)",
        BASE_URL + "WNJAP_55-50_3ns.php",
    ),
    WeaponTarget(
        "JP_356MM_41SHIKI", "JP", "36 cm/45 41st Year Type (Kongo/Fuso/Ise)",
        BASE_URL + "WNJAP_14-45_t41.php",
        caliber_override=356,
    ),
    WeaponTarget(
        "JP_152MM_41SHIKI", "JP", "15 cm/50 41st Year Type (Kongo/Fuso/Agano)",
        BASE_URL + "WNJAP_6-50_t41.php",
        caliber_override=152,
    ),
    WeaponTarget(
        "JP_127MM_89SHIKI", "JP", "12.7 cm/40 Type 89 (heavy AA/dual-purpose)",
        BASE_URL + "WNJAP_5-40_t89.php",
        dual_purpose=True,
    ),
    WeaponTarget(
        "JP_120MM_3SHIKI", "JP", "12 cm/45 3rd Year Type (Japanese destroyers)",
        BASE_URL + "WNJAP_47-45_11ns.php",
    ),
    # --- United States (inch 表記) ---
    WeaponTarget(
        "US_16INCH_MK7", "US", "16\"/50 Mark 7 (Iowa/Montana)",
        BASE_URL + "WNUS_16-50_mk7.php",
    ),
    WeaponTarget(
        "US_16INCH_MK6", "US", "16\"/45 Mark 6 (North Carolina/South Dakota)",
        BASE_URL + "WNUS_16-45_mk6.php",
    ),
    WeaponTarget(
        "US_14INCH_MK11", "US", "14\"/50 Mark 11 (New Mexico/Tennessee)",
        BASE_URL + "WNUS_14-50_mk11.php",
    ),
    WeaponTarget(
        "US_12INCH_MK8", "US", "12\"/50 Mark 8 (Wyoming/Arkansas)",
        BASE_URL + "WNUS_12-50_mk8.php",
    ),
    WeaponTarget(
        "US_8INCH_MK12", "US", "8\"/55 Mark 12 (New Orleans/Wichita)",
        BASE_URL + "WNUS_8-55_mk12-15.php",
    ),
    WeaponTarget(
        "US_6INCH_MK16", "US", "6\"/47 Mark 16 (Cleveland/Brooklyn)",
        BASE_URL + "WNUS_6-47_mk16.php",
    ),
    WeaponTarget(
        "US_5INCH_MK12", "US", "5\"/38 Mark 12 (dual-purpose, ubiquitous)",
        BASE_URL + "WNUS_5-38_mk12.php",
        dual_purpose=True,
    ),
    WeaponTarget(
        "US_16INCH_MK5", "US", "16\"/45 Mark 5 (Colorado class)",
        BASE_URL + "WNUS_16-45_mk5.php",
    ),
    WeaponTarget(
        "US_14INCH_MK10", "US", "14\"/45 Marks 8/9/10/12 (standard battleships)",
        BASE_URL + "WNUS_14-45_mk10.php",
    ),
    WeaponTarget(
        "US_8INCH_MK9", "US", "8\"/55 Marks 9/10/11/13/14 (treaty cruisers)",
        BASE_URL + "WNUS_8-55_mk9.php",
    ),
    WeaponTarget(
        "US_5INCH_MK10", "US", "5\"/25 Marks 10/11/13/17 (heavy AA)",
        BASE_URL + "WNUS_5-25_mk10.php",
        dual_purpose=True,
    ),
    # --- United Kingdom (inch 表記; navweaps 国コードは WNBR_) ---
    WeaponTarget(
        "UK_15INCH_MKI", "UK", "15\"/42 Mark I (QE/Revenge/Hood/Vanguard)",
        BASE_URL + "WNBR_15-42_mk1.php",
    ),
    WeaponTarget(
        "UK_16INCH_MKI", "UK", "16\"/45 Mark I (Nelson class)",
        BASE_URL + "WNBR_16-45_mk1.php",
    ),
    WeaponTarget(
        "UK_14INCH_MKVII", "UK", "14\"/45 Mark VII (KGV)",
        BASE_URL + "WNBR_14-45_mk7.php",
    ),
    WeaponTarget(
        "UK_135INCH_MKV", "UK", "13.5\"/45 Mark V (Iron Duke/Tiger)",
        BASE_URL + "WNBR_135-45_mk5.php",
    ),
    WeaponTarget(
        "UK_6INCH_MKXXIII", "UK", "6\"/50 Mark XXIII (Southampton/Edinburgh)",
        BASE_URL + "WNBR_6-50_mk23.php",
    ),
    WeaponTarget(
        "UK_525INCH_MKI", "UK", "5.25\"/50 Mark I (KGV/Dido)",
        BASE_URL + "WNBR_525-50_mk1.php",
        dual_purpose=True,
    ),
    WeaponTarget(
        "UK_8INCH_MKVIII", "UK", "8\"/50 Mark VIII (County/York classes)",
        BASE_URL + "WNBR_8-50_mk8.php",
    ),
    WeaponTarget(
        "UK_47INCH_MKIX", "UK", "4.7\"/45 QF Mark IX (British destroyers)",
        BASE_URL + "WNBR_47-45_mk9.php",
        caliber_override=120,
    ),
    WeaponTarget(
        "UK_45INCH_MKI", "UK", "4.5\"/45 QF Marks I/III/IV (dual-purpose)",
        BASE_URL + "WNBR_45-45_mk1.php",
        dual_purpose=True,
        caliber_override=113,
    ),
    WeaponTarget(
        "UK_4INCH_MKIX", "UK", "4\"/45 BL Marks IX/X (escort main gun)",
        BASE_URL + "WNBR_4-45_mk9.php",
        caliber_override=102,
    ),
    # --- Germany (cm 表記) ---
    WeaponTarget(
        "DE_38CM_SKC34", "DE", "38 cm/52 SK C/34 (Bismarck)",
        BASE_URL + "WNGER_15-52_skc34.php",
    ),
    WeaponTarget(
        "DE_28CM_SKC34", "DE", "28 cm/54.5 SK C/34 (Scharnhorst upgrade)",
        BASE_URL + "WNGER_11-545_skc34.php",
    ),
    WeaponTarget(
        "DE_28CM_SKC28", "DE", "28 cm/52 SK C/28 (Deutschland class)",
        BASE_URL + "WNGER_11-52_skc28.php",
    ),
    WeaponTarget(
        "DE_15CM_SKC28", "DE", "15 cm/55 SK C/28 (capital ship secondary)",
        BASE_URL + "WNGER_59-55_skc28.php",
    ),
    WeaponTarget(
        "DE_105CM_SKC33", "DE", "10.5 cm/65 SK C/33 (heavy AA, dual-purpose)",
        BASE_URL + "WNGER_41-65_skc33.php",
        dual_purpose=True,
    ),
    WeaponTarget(
        "DE_305CM_SKL50", "DE", "30.5 cm/50 SK L/50 (German dreadnoughts)",
        BASE_URL + "WNGER_12-50_skc12.php",
        caliber_override=305,
        technology_tier="legacy",
    ),
    WeaponTarget(
        "DE_203CM_SKC34", "DE", "20.3 cm/60 SK C/34 (Admiral Hipper)",
        BASE_URL + "WNGER_8-60_skc34.php",
        caliber_override=203,
    ),
    WeaponTarget(
        "DE_15CM_SKC25", "DE", "15 cm/60 SK C/25 (K-class/Leipzig/Nurnberg)",
        BASE_URL + "WNGER_59-60_skc25.php",
    ),
    WeaponTarget(
        "DE_127CM_SKC34", "DE", "12.7 cm/45 SK C/34 (German destroyers)",
        BASE_URL + "WNGER_5-45_skc34.php",
        caliber_override=128,
        mount_type_override="single",
    ),
    # --- France (mm 表記) ---
    WeaponTarget(
        "FR_381MM_M1935", "FR", "380 mm/45 Mle 1935 (Richelieu)",
        BASE_URL + "WNFR_15-45_m1935.php",
    ),
    WeaponTarget(
        "FR_330MM_M1931", "FR", "330 mm/50 Mle 1931 (Dunkerque)",
        BASE_URL + "WNFR_13-50_m1931.php",
    ),
    WeaponTarget(
        "FR_340MM_M1912", "FR", "340 mm/45 Mle 1912 (Bretagne)",
        BASE_URL + "WNFR_134-45_m1912.php",
    ),
    # 注: progress §4 は「M1920 ラ・ガリソニエール」とあるが、史実の
    # La Galissonniere 級 152mm/55 は Mle 1930。navweaps に M1920 ページは存在しないため
    # M1930 ページを使用し weapon_id も M1930 に統一 (STAGE4_PROGRESS.md §4 も更新済みの想定)。
    WeaponTarget(
        "FR_152MM_M1930", "FR", "152 mm/55 Mle 1930 (La Galissonniere)",
        BASE_URL + "WNFR_6-55_m1930.php",
    ),
    WeaponTarget(
        "FR_130MM_M1932", "FR", "130 mm/45 Mle 1932 (Le Fantastque)",
        BASE_URL + "WNFR_51-45_m1932.php",
        # ページ本文中の表 "45 degrees 22,820 yards (20,870 m)" を補完 (table タグ外のため自動抽出不可)
        max_range_override=20870,
        notes_hint="max_range_m from navweaps inline table (not in <table> tag).",
    ),
    WeaponTarget(
        "FR_203MM_M1924", "FR", "203 mm/50 Mle 1924 (French treaty cruisers)",
        BASE_URL + "WNFR_8-50_m1924.php",
    ),
    WeaponTarget(
        "FR_139MM_M1923", "FR", "138.6 mm/40 Mle 1923 (Bison/Pluton)",
        BASE_URL + "WNFR_55-40_m1923.php",
        caliber_override=139,
    ),
    WeaponTarget(
        "FR_139MM_M1929", "FR", "138.6 mm/50 Mle 1929 (Le Fantasque)",
        BASE_URL + "WNFR_55-50_m1929.php",
        caliber_override=139,
    ),
    WeaponTarget(
        "FR_100MM_M1930", "FR", "100 mm/45 Mle 1930 (heavy AA/dual-purpose)",
        BASE_URL + "WNFR_39-45_m1930.php",
        dual_purpose=True,
    ),
    # --- Italy (mm 表記) ---
    WeaponTarget(
        "IT_381MM_M1934", "IT", "381 mm/50 Mle 1934 (Littorio/Vittorio Veneto)",
        BASE_URL + "WNIT_15-50_m1934.php",
    ),
    WeaponTarget(
        "IT_320MM_M1934", "IT", "320 mm/44 Mle 1934 (Cavour reconstruction)",
        BASE_URL + "WNIT_126-44_m1934.php",
    ),
    WeaponTarget(
        "IT_152MM_M1926", "IT", "152 mm/53 Mle 1926 (Zara/Condottieri)",
        BASE_URL + "WNIT_6-53_m1926.php",
    ),
    WeaponTarget(
        "IT_135MM_M1938", "IT", "135 mm/45 Mle 1938 (Capitani Romani)",
        BASE_URL + "WNIT_53-45_m1938.php",
        # ページ本文中の表 "45 degrees 21,435 yards (19,600 m)" を補完
        max_range_override=19600,
        notes_hint="max_range_m from navweaps inline table (not in <table> tag).",
    ),
    WeaponTarget(
        "IT_90MM_M1939", "IT", "90 mm/50 Mle 1939 (heavy AA, dual-purpose)",
        BASE_URL + "WNIT_35-50_m1939.php",
        dual_purpose=True,
    ),
    WeaponTarget(
        "IT_203MM_M1927", "IT", "203 mm/53 Mle 1927 (Zara/Bolzano)",
        BASE_URL + "WNIT_8-53_m1927.php",
    ),
    WeaponTarget(
        "IT_152MM_M1936", "IT", "152 mm/55 Mle 1936 (Littorio/Garibaldi)",
        BASE_URL + "WNIT_6-55_m1936.php",
    ),
    WeaponTarget(
        "IT_120MM_M1926", "IT", "120 mm/50 Mle 1926 (Italian destroyers)",
        BASE_URL + "WNIT_47-50_m1926.php",
    ),
    WeaponTarget(
        "IT_102MM_M1917", "IT", "102 mm/45 Mle 1917/1919 (torpedo boats/submarines)",
        BASE_URL + "WNIT_4-45_m1917.php",
        technology_tier="legacy",
    ),
]


# ----------------------------------------------------------------------------
# 抽出ヘルパ
# ----------------------------------------------------------------------------

def fetch_html(url: str, timeout: float = 25.0, cache_dir: Path | None = None) -> str:
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = re.sub(r"[^A-Za-z0-9_.-]", "_", url.rsplit("/", 1)[-1])
        cache_path = cache_dir / key
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode(errors="replace")
    if cache_dir is not None:
        cache_path.write_text(html, encoding="utf-8")  # type: ignore[possibly-undefined]
    return html


def _tables(soup: BeautifulSoup) -> list[Any]:
    return soup.find_all("table")


def find_row(tables: list[Any], label_re: str) -> str | None:
    """最初の列が label_re にマッチする行の2列目を返す。"""
    pat = re.compile(label_re, re.I)
    for t in tables:
        for tr in t.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:
                key = cells[0].get_text(" ", strip=True)
                if pat.search(key):
                    return cells[1].get_text(" ", strip=True)
    return None


def find_max_range_m(tables: list[Any]) -> int | None:
    """Range 表または Maximum Range 行からメートル表記の最大射程を抽出。

    戦略 (順番に試行):
      1. "Elevation" + "Range" ヘッダを持つ表の Range 列から (N m) の最大値
      2. "Range" 単独ヘッダの表、または "Maximum Range" 行の (N m)
      3. 上記で取れなければ None
    """
    best: int | None = None

    def _consider(text: str) -> None:
        nonlocal best
        for m in re.finditer(r"\(([\d,]+)\s*m\)", text):
            val = int(m.group(1).replace(",", ""))
            # 10 km 未満の副砲でも数千 m、主砲は 20,000-45,000 m
            # 全く別の単位 (Length in m 等) と誤認しにくいよう下限 1,000 m を設定
            if val >= 1000 and (best is None or val > best):
                best = val

    # 戦略1: Elevation ヘッダの表 (Range/Distance が無く弾種名列でも射程表とみなす)
    for t in tables:
        rows = t.find_all("tr")
        if not rows:
            continue
        header = " ".join(c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"]))
        if "Elevation" in header:
            for tr in rows[1:]:
                cells = tr.find_all(["th", "td"])
                text = " ".join(c.get_text(" ", strip=True) for c in cells)
                _consider(text)
            if best is not None:
                return best

    # 戦略2: "Range" ヘッダのみの表 (装甲貫徠表など) の Range 列から最大 m を探す
    for t in tables:
        rows = t.find_all("tr")
        if not rows:
            continue
        header = " ".join(c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"]))
        # "Elevation + Range/Distance" は戦略1で処理済みなので、ここでは Range 単独表
        if header.strip().startswith("Range") or " | Range " in (" | " + header + " | "):
            for tr in rows:
                cells = tr.find_all(["th", "td"])
                # 最初の列 (Range) のみ評価し、装甲列の "(N mm)" に誤反応しないようにする
                if cells:
                    _consider(cells[0].get_text(" ", strip=True))
            if best is not None:
                return best

    # 戦略3: "Maximum Range" キーを行から探す
    mr_row = find_row(tables, r"Maximum Range|Max Range|Max\.? Range")
    if mr_row:
        _consider(mr_row)
    return best


_RE_KG = re.compile(r"\(([\d,]+\.?\d*)\s*kg\)")
_RE_KG_LOOSE = re.compile(r"([\d,]+\.?\d*)\s*(?:[-–]|to)?\s*(?:[\d,]+\.?\d*)?\s*kg\b", re.I)
_RE_MT = re.compile(r"\(([\d,]+\.?\d*)\s*m\.?\s*t\.?\)")
_RE_MT_LOOSE = re.compile(r"([\d,]+\.?\d*)\s*(?:[-–]|to)?\s*(?:[\d,]+\.?\d*)?\s*m\.?\s*t\.?\b", re.I)
_RE_TON = re.compile(r"([\d,]+\.?\d*)\s*tons?\b")  # "111.6 tons" (短/長吨判定不能なら kg 優先)
_RE_LBS = re.compile(r"([\d,]+\.?\d*)\s*lbs?\.?")
_RE_M = re.compile(r"\(([\d,]+\.?\d*)\s*m\)")
_RE_MPS = re.compile(r"([\d,]+\.?\d*)\s*mps")
_RE_FPS = re.compile(r"([\d,]+\.?\d*)\s*fps")
_RE_FIRST_INT = re.compile(r"(\d{4})")


def _first_mass_kg(text: str | None) -> float | None:
    """Gun Weight 行から kg 換算質量を抽出。優先度: kg > mt > lbs(0.4536).

    "5,600 - 5,700 kg" のような範囲表記には最初の値を採用する。
    """
    if not text:
        return None
    m = _RE_KG.search(text) or _RE_KG_LOOSE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    m = _RE_MT.search(text) or _RE_MT_LOOSE.search(text)
    if m:
        return float(m.group(1).replace(",", "")) * 1000.0
    # lbs のみ (kg/mt が無い) なら lbs -> kg
    m_lbs = _RE_LBS.search(text)
    m_tons = _RE_TON.search(text)
    if m_lbs and not m_tons:
        return float(m_lbs.group(1).replace(",", "")) * 0.45359237
    return None


def _first_float(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", text)
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def _first_rate_of_fire(text: str | None) -> float | None:
    """発射速度の最初の値を抽出する。

    NavWeaps の発射速度行には ``Model 1929`` や ``Type 88`` のような
    形式年・型式番号が先に現れることがある。単純な先頭数値抽出ではそれらを
    rpm と誤認するため、``rounds per minute`` / ``rounds / minute`` に直結する
    数値だけを優先する。範囲表記は従来どおり下限値を採用する。
    """
    if not text:
        return None
    m = re.search(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*"
        r"(?:(?:-|–|to)\s*\d+(?:\.\d+)?\s*)?"
        r"rounds?\s*(?:per|/)\s*minute",
        text,
        re.I,
    )
    if m:
        return float(m.group(1))
    return _first_float(text)


def _first_angular_rate(text: str | None) -> float | None:
    """旋回・俯仰速度から型式年を除外して deg/s の値を抽出する。"""
    if not text:
        return None
    m = re.search(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*"
        r"(?:(?:-|–|to)\s*\d+(?:\.\d+)?\s*)?"
        r"degrees?\s*per\s*second",
        text,
        re.I,
    )
    return float(m.group(1)) if m else None


def _first_year(text: str | None) -> int | None:
    if not text:
        return None
    m = _RE_FIRST_INT.search(text)
    return int(m.group(1)) if m else None


def _first_kg(text: str | None) -> float | None:
    """旧名 (後方互換; _first_mass_kg へ alias)."""
    return _first_mass_kg(text)


def _caliber_mm_from_text(text: str | None) -> int | None:
    """テキストから口径mmを抽出。cm / mm / inch いずれかの表記に対応。

    優先順位:
      1. ``Actual Size: N cm`` (navweaps 慣習: 大和型 40cm 公称は 46cm 実口径)
      2. ``N mm`` (直接表記、例: 340 mm)
      3. ``N cm`` (例: 15.2 cm)
      4. ``N"`` or ``N inch`` → 25.4 倍
    """
    if not text:
        return None
    m = re.search(r"Actual Size:?\s*(\d+(?:\.\d+)?)\s*cm", text, re.I)
    if m:
        return int(round(float(m.group(1)) * 10))
    m = re.search(r"(\d{2,3})\s*mm\b", text)
    if m:
        return int(float(m.group(1)))
    m = re.search(r"(\d+(?:\.\d+)?)\s*cm", text)
    if m:
        return int(round(float(m.group(1)) * 10))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:\"|inch|in\.)", text, re.I)
    if m:
        return int(round(float(m.group(1)) * 25.4))
    return None


def _url_caliber_mm(url: str) -> int | None:
    """URL ``WN{NAT}_{CAL}-{LEN}_...`` の CAL 部から口径mmを推定 (フォールバック用)。

    - 2桁: 整数インチ (16 -> 406)
    - 3桁: 1桁目.残り2桁 (525 -> 5.25 -> 133)
    - 4桁: 2桁.残り2桁 (例は稀)
    """
    m = re.search(r"/WN[A-Z]+_(\d+)-", url)
    if not m:
        return None
    digits = m.group(1)
    if len(digits) <= 2:
        inch = float(digits)
    elif len(digits) == 3:
        inch = float(digits[0] + "." + digits[1:])
    elif len(digits) == 4:
        inch = float(digits[:2] + "." + digits[2:])
    else:
        return None
    return int(round(inch * 25.4))


def _url_barrel_length(url: str) -> float | None:
    """URL ``WN{NAT}_{CAL}-{LEN}_...`` の LEN 部から口径長を推定 (フォールバック用)。"""
    m = re.search(r"/WN[A-Z]+_\d+-([\d.]+)_", url)
    return float(m.group(1)) if m else None


def _barrel_length_from_text(text: str | None) -> float | None:
    """口径長をテキストから抽出。"cm/N" "mm/N" "inch/N" "\"/N" の N を優先。

    "SK C/34" のような形式番号 (/ の後にあるが単位の後ではない) は除外される。
    """
    if not text:
        return None
    # 単位の直後の /N を優先 (例: "40 cm/45", "152 mm/55", '16"/50')
    m = re.search(r"(?:cm|mm|m\.?\s*t\.?|inch|in\.?|\")\s*/?\s*/\s*(\d+(?:\.\d+)?)", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"(?:cm|mm|inch|in\.?|\")\s*/\s*(\d+(?:\.\d+)?)", text, re.I)
    if m:
        return float(m.group(1))
    # "N caliber" 表記
    m = re.search(r"(\d+(?:\.\d+)?)\s*caliber", text, re.I)
    if m:
        return float(m.group(1))
    return None


def _extract_caliber_and_length(
    designation: str | None,
    title: str | None,
    url: str,
) -> tuple[int | None, float | None]:
    """Designation行 → title → URL の順で口径と口径長を推定。"""
    barrel_len = _barrel_length_from_text(designation) or _barrel_length_from_text(title)

    caliber_mm = (
        _caliber_mm_from_text(designation)
        or _caliber_mm_from_text(title)
        or _url_caliber_mm(url)
    )
    if barrel_len is None:
        barrel_len = _url_barrel_length(url)
    return caliber_mm, barrel_len


def _normalize_loading_type(text: str | None) -> str:
    if not text:
        return "unknown"
    t = text.lower()
    if "bag" in t:
        return "bag"
    if "cartridge" in t or "separate" in t:
        if "qf" in t:
            return "qf_cartridge"
        return "cartridge"
    if "fixed" in t:
        return "fixed"
    return "unknown"


def extract_record(target: WeaponTarget, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    tables = _tables(soup)
    title = (soup.title.string.strip() if soup.title and soup.title.string else target.display_name)

    designation = find_row(tables, r"Designation")
    date_in_service = find_row(tables, r"Date In Service")
    date_of_design = find_row(tables, r"Date Of Design")
    ship_class = find_row(tables, r"Ship Class Used On")
    gun_weight = find_row(tables, r"Gun Weight")
    bore_length = find_row(tables, r"Bore Length")
    ammo_type = find_row(tables, r"^Type\b")
    projectile_weights = find_row(tables, r"Projectile Types and Weights")
    muzzle_velocity = find_row(tables, r"Muzzle Velocity")
    rate_of_fire = find_row(tables, r"Rate of Fire")

    caliber_mm, barrel_len = _extract_caliber_and_length(designation, title, target.url)
    gun_weight_kg = _first_kg(gun_weight)
    gun_weight_t = round(gun_weight_kg / 1000.0, 3) if gun_weight_kg else None

    shell_weight_kg = _first_kg(projectile_weights)
    mv_mps = None
    if muzzle_velocity:
        m = _RE_MPS.search(muzzle_velocity)
        if m:
            mv_mps = float(m.group(1).replace(",", ""))
        else:
            m = _RE_FPS.search(muzzle_velocity)
            if m:
                mv_mps = round(float(m.group(1).replace(",", "")) * 0.3048, 1)
    max_range_m = find_max_range_m(tables)
    rate_of_fire_rpm = _first_rate_of_fire(rate_of_fire)
    if rate_of_fire_rpm is not None:
        rate_of_fire_rpm = round(rate_of_fire_rpm, 2)
    year_introduced = _first_year(date_in_service) or _first_year(date_of_design)

    record: dict[str, Any] = {
        "weapon_id": target.weapon_id,
        "name": target.display_name,
        "nation": target.nation,
        "year_introduced": year_introduced,
        "year_retired": None,
        "caliber_mm": target.caliber_override if target.caliber_override else caliber_mm,
        "barrel_length_calibers": barrel_len,
        "gun_weight_t": gun_weight_t,
        "shell_weight_kg": shell_weight_kg,
        "muzzle_velocity_mps": mv_mps,
        "rate_of_fire_rpm": rate_of_fire_rpm,
        "max_range_m": target.max_range_override if target.max_range_override else max_range_m,
        "loading_type": _normalize_loading_type(ammo_type),
        "dual_purpose": target.dual_purpose,
        "technology_tier": target.technology_tier,
        "source": target.url,
        "source_type": "navweaps",
        "verified": False,
        "confidence": 0.8,
        "notes": target.notes_hint,
        "fictional": False,
        # 抽出メタ (debug 補助; 最終 JSONL から除去してもよい)
        "_navweaps_title": title,
        "_ship_class": ship_class,
    }
    if target.caliber_override and caliber_mm and target.caliber_override != caliber_mm:
        record["_caliber_extracted"] = caliber_mm
    if target.max_range_override and max_range_m and target.max_range_override != max_range_m:
        record["_max_range_extracted"] = max_range_m
    return record


# ----------------------------------------------------------------------------
# mounts / ammunition 抽出 (Stage 4 Commit A の DB seed 残り)
# ----------------------------------------------------------------------------

_MOUNT_TYPE_PATTERNS: list[tuple[str, str, int]] = [
    # (ヘッダに含まれるキーワード, mount_type, barrels_per_mount)
    ("three-gun", "triple", 3),
    ("triple", "triple", 3),
    ("three gun", "triple", 3),
    ("four-gun", "quadruple", 4),
    ("quadruple", "quadruple", 4),
    ("quad", "quadruple", 4),
    ("two-gun", "twin", 2),
    ("twin", "twin", 2),
    ("two gun", "twin", 2),
    ("single", "single", 1),
]


def _mount_type_from_header(header: str) -> tuple[str | None, int | None]:
    h = header.lower()
    for kw, mtype, barrels in _MOUNT_TYPE_PATTERNS:
        if kw in h:
            return mtype, barrels
    return None, None


def _find_mount_table(tables: list[Any]) -> tuple[Any | None, str]:
    """Mount/Turret Data 表 (ヘッダに Designation + Turret/Mount を含む) を返す。"""
    for t in tables:
        rows = t.find_all("tr")
        if not rows:
            continue
        header = " ".join(c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"]))
        h_lower = header.lower()
        if "designation" in h_lower and ("turret" in h_lower or "mount" in h_lower):
            return t, header
    return None, ""


def _first_tons(text: str | None) -> float | None:
    if not text:
        return None
    # "2,730.2 tons" or "770 tons" (優先)
    m = re.search(r"([\d,]+\.?\d*)\s*tons?\b", text, re.I)
    if m:
        return float(m.group(1).replace(",", ""))
    # "2,774 mt" metric ton
    m = _RE_MT_LOOSE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    # "111,664 kg" → t 換算
    kg = _first_mass_kg(text)
    return round(kg / 1000.0, 3) if kg else None


def _elevation_range(text: str | None) -> str | None:
    if not text:
        return None
    # "-5 / +45 degrees" or "+2 / +30 degrees"
    m = re.search(r"([-+]?\d+(?:\.\d+)?)\s*(?:/|to)\s*([-+]?\d+(?:\.\d+)?)\s*degrees?", text, re.I)
    if m:
        lo, hi = m.group(1), m.group(2)
        return f"{lo}/{hi}"
    return None


def extract_mount_record(target: WeaponTarget, html: str) -> dict[str, Any] | None:
    """各 weapon ページの Mount/Turret Data 表から代表砲塔1件を抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    tables = _tables(soup)
    mt, header = _find_mount_table(tables)
    if not mt:
        return None
    mount_type, barrels = _mount_type_from_header(header)
    if not mount_type:
        return None
    if target.mount_type_override:
        mount_type = target.mount_type_override
        barrels = {
            "single": 1,
            "twin": 2,
            "triple": 3,
            "quadruple": 4,
        }.get(mount_type)
        if barrels is None:
            raise ValueError(f"unsupported mount_type_override: {mount_type}")

    weight = find_row([mt], r"^Weight\b|Gun Weight")
    elevation = find_row([mt], r"^Elevation\b")
    elev_rate = find_row([mt], r"Elevation Rate|Rate of Elevation")
    train_rate = find_row([mt], r"Train Rate|Rate of Train")
    train_range = find_row([mt], r"^Train\b")

    turret_weight_t = _first_tons(weight)
    elevation_rng = _elevation_range(elevation)
    train_rate_val = _first_angular_rate(train_rate) or _first_angular_rate(elev_rate)

    # mount_id 構築: {NATION}_{CALIBER_PART}_{TYPE_UPPER}_{FORM}
    # weapon_id が "JP_460MM_94SHIKI" の場合、caliber_part=460MM, form=94SHIKI
    parts = target.weapon_id.split("_", 2)
    caliber_part = parts[1] if len(parts) >= 2 else target.weapon_id
    form = parts[2] if len(parts) >= 3 else "GENERIC"
    mount_id = f"{target.nation}_{caliber_part}_{mount_type.upper()}_{form}"

    # 艦級 (ヘッダの2列目から推定)
    ship_hint = header.split("|", 1)[1].strip()[:80] if "|" in header else header[:80]

    return {
        "mount_id": mount_id,
        "weapon_id": target.weapon_id,
        "name": f"{mount_type.title()} mount ({target.display_name})",
        "nation": target.nation,
        "year_introduced": None,
        "barrels_per_mount": barrels,
        "mount_type": mount_type,
        "turret_weight_t": turret_weight_t,
        "barbette_diameter_m": None,
        "deck_penetration_count": None,
        "minimum_beam_m": None,
        "minimum_displacement_t": None,
        "turret_armor": None,
        "training_speed_deg_s": train_rate_val,
        "elevation_range": elevation_rng,
        "crew": None,
        "power_requirement_kw": None,
        "magazine_volume_m3": None,
        "technology_tier": target.technology_tier,
        "source": target.url,
        "source_type": "navweaps",
        "verified": False,
        "confidence": 0.7,
        "notes": "",
        "fictional": False,
        "_ship_hint": ship_hint,
    }


# ammunition 抽出 --------------------------------------------------

_AMMO_TYPE_ORDER = ["APC", "SAPBC", "SAP", "AP", "Common", "HE"]


def _extract_ammo_type(text: str | None) -> tuple[str, str]:
    """Projectile Types 行から代表弾の type と name を抽出。APC を最優先。"""
    if not text:
        return "AP", "AP"
    for t in _AMMO_TYPE_ORDER:
        m = re.search(rf"\b{t}\b\s*[A-Za-z0-9 /,''.-]*", text, re.I)
        if m:
            name = re.sub(r"\s+", " ", m.group(0)).strip().rstrip(":,' ")
            return t, name or t
    return "AP", "AP"


def extract_ammo_record(target: WeaponTarget, html: str) -> dict[str, Any] | None:
    """各 weapon ページの Ammunition 表から代表弾1件を抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    tables = _tables(soup)
    projectile = find_row(tables, r"Projectile Types and Weights")
    if not projectile:
        return None
    muzzle = find_row(tables, r"Muzzle Velocity")
    burst = find_row(tables, r"Bursting Charge|Bursting Charge ")

    ammo_type, name = _extract_ammo_type(projectile)
    shell_weight = _first_mass_kg(projectile)
    mv_mps: float | None = None
    if muzzle:
        m = _RE_MPS.search(muzzle)
        if m:
            mv_mps = float(m.group(1).replace(",", ""))
        else:
            m = _RE_FPS.search(muzzle)
            if m:
                mv_mps = round(float(m.group(1).replace(",", "")) * 0.3048, 1)
    explosive = _first_mass_kg(burst)

    ammo_id = f"{target.weapon_id}_{ammo_type}"

    return {
        "ammo_id": ammo_id,
        "weapon_id": target.weapon_id,
        "name": name,
        "type": ammo_type,
        "shell_weight_kg": shell_weight,
        "muzzle_velocity_mps": mv_mps,
        "explosive_weight_kg": explosive,
        "source": target.url,
        "source_type": "navweaps",
        "verified": False,
        "confidence": 0.7,
        "notes": "",
        "fictional": False,
    }

def run(
    out_path: Path,
    manifest_path: Path,
    mounts_path: Path | None = None,
    ammo_path: Path | None = None,
    limit: int | None = None,
    sleep: float = 1.5,
    only: str | None = None,
    cache_dir: Path | None = None,
) -> int:
    targets = TARGETS
    if only:
        targets = [t for t in targets if t.weapon_id == only]
        if not targets:
            print(f"[ERR] weapon_id={only} not found in TARGETS", file=sys.stderr)
            return 2
    if limit:
        targets = targets[:limit]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if mounts_path:
        mounts_path.parent.mkdir(parents=True, exist_ok=True)
    if ammo_path:
        ammo_path.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    mount_records: list[dict[str, Any]] = []
    ammo_records: list[dict[str, Any]] = []
    manifest_entries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for i, t in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {t.weapon_id} <- {t.url}")
        try:
            html = fetch_html(t.url, cache_dir=cache_dir)
        except urllib.error.HTTPError as e:
            msg = f"HTTP {e.code} {e.reason}"
            print(f"    FAIL {msg}")
            failures.append({"weapon_id": t.weapon_id, "url": t.url, "error": msg})
            manifest_entries.append({"weapon_id": t.weapon_id, "status": "http_error", "error": msg})
            time.sleep(sleep)
            continue
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            print(f"    FAIL {msg}")
            failures.append({"weapon_id": t.weapon_id, "url": t.url, "error": msg})
            manifest_entries.append({"weapon_id": t.weapon_id, "status": "fetch_error", "error": msg})
            time.sleep(sleep)
            continue

        try:
            rec = extract_record(t, html)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            print(f"    PARSE_FAIL {msg}")
            failures.append({"weapon_id": t.weapon_id, "url": t.url, "error": msg})
            manifest_entries.append({"weapon_id": t.weapon_id, "status": "parse_error", "error": msg})
            time.sleep(sleep)
            continue

        missing = [f"weapon.{k}" for k in (
            "caliber_mm", "barrel_length_calibers", "gun_weight_t", "shell_weight_kg",
            "muzzle_velocity_mps", "max_range_m", "rate_of_fire_rpm",
        ) if rec.get(k) is None]

        # mounts / ammunition 抽出 (失敗しても武器レコードは出力)
        mount_note = ""
        ammo_note = ""
        if mounts_path is not None:
            try:
                m_rec = extract_mount_record(t, html)
                if m_rec:
                    mount_records.append(m_rec)
                    missing.extend(
                        f"mount.{k}" for k in (
                            "mount_id", "barrels_per_mount", "mount_type", "turret_weight_t",
                        ) if m_rec.get(k) is None
                    )
                else:
                    mount_note = "no_mount_table"
                    missing.append("mount.record")
            except Exception as e:  # noqa: BLE001
                mount_note = f"mount_parse_error:{type(e).__name__}"
                missing.append("mount.record")
        if ammo_path is not None:
            try:
                a_rec = extract_ammo_record(t, html)
                if a_rec:
                    ammo_records.append(a_rec)
                    missing.extend(
                        f"ammo.{k}" for k in (
                            "ammo_id", "type", "shell_weight_kg", "muzzle_velocity_mps",
                        ) if a_rec.get(k) is None
                    )
                else:
                    ammo_note = "no_ammo_row"
                    missing.append("ammo.record")
            except Exception as e:  # noqa: BLE001
                ammo_note = f"ammo_parse_error:{type(e).__name__}"
                missing.append("ammo.record")

        status = "ok" if not missing else "partial"

        manifest_entries.append({
            "weapon_id": t.weapon_id,
            "status": status,
            "missing_fields": missing,
            "url": t.url,
            "mount": mount_note or "ok",
            "ammo": ammo_note or "ok",
        })
        print(f"    {status} missing={missing} mount={mount_note or 'ok'} ammo={ammo_note or 'ok'}")
        records.append(rec)
        time.sleep(sleep)

    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if mounts_path is not None:
        with mounts_path.open("w", encoding="utf-8") as f:
            for r in mount_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if ammo_path is not None:
        with ammo_path.open("w", encoding="utf-8") as f:
            for r in ammo_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    manifest = {
        "schema": "navspec.armament.weapons.v1",
        "source_type": "navweaps",
        "generated_by": "tools/navspec_armament_scraper.py",
        "target_count": len(targets),
        "record_count": len(records),
        "mount_count": len(mount_records),
        "ammo_count": len(ammo_records),
        "ok_count": sum(1 for m in manifest_entries if m["status"] == "ok"),
        "partial_count": sum(1 for m in manifest_entries if m["status"] == "partial"),
        "failed_count": len(failures),
        "entries": manifest_entries,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"records={len(records)} mounts={len(mount_records)} ammos={len(ammo_records)} "
          f"ok={manifest['ok_count']} partial={manifest['partial_count']} failed={len(failures)}")
    print(f"wrote: {out_path}")
    if mounts_path:
        print(f"wrote: {mounts_path}")
    if ammo_path:
        print(f"wrote: {ammo_path}")
    print(f"wrote: {manifest_path}")
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="data/navspec/armament/weapons.jsonl")
    p.add_argument("--manifest", default="data/navspec/armament/weapons_manifest.json")
    p.add_argument("--mounts", default="data/navspec/armament/mounts.jsonl")
    p.add_argument("--ammo", default="data/navspec/armament/ammunition.jsonl")
    p.add_argument("--cache", default="data/navspec/armament/_cache_html")
    p.add_argument("--limit", type=int, default=None, help="先頭N件のみ (プロトタイプ用)")
    p.add_argument("--only", default=None, help="指定 weapon_id のみ処理")
    p.add_argument("--sleep", type=float, default=1.5, help="1件ごとの取得間隔(秒)")
    args = p.parse_args(argv)
    cache_dir = Path(args.cache) if args.cache else None
    return run(
        Path(args.out),
        Path(args.manifest),
        Path(args.mounts) if args.mounts else None,
        Path(args.ammo) if args.ammo else None,
        args.limit,
        args.sleep,
        args.only,
        cache_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
