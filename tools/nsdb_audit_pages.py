"""NSDB 網羅性監査: 艦級ページ (現行 navypedia.org) の『正解』パーサと、fetcher.py の現行ロジックのオフライン再現。

- parse_live(): 生 HTML を正規表現で分解して正解 (specs / ships / sections / images) を得る。
  現行サイトの HTML は <tr> が閉じていない箇所があり、html.parser だと tr がネストして
  `row.find_all('td')` が後続行まで拾う (= SHIPS 重複の原因) ので、BeautifulSoup の木構造に頼らない。
- run_fetcher_offline(): nsdb/fetcher.py の NavypediaFetcher.fetch_class_detail を、キャッシュ済み HTML に対して
  ネットワーク・DB 無しで実行する（__init__ を呼ばず DB を作らない）。
"""
from __future__ import annotations

import html as htmlmod
import importlib.util
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[1]
FETCHER_PATH = REPO / "nsdb" / "fetcher.py"

STD_SECTION_SKIP = {"photo", "ships", "technical data", "standard scale images", "graphics"}


def norm(s) -> str:
    """比較用正規化: 小文字 + 英数字(Unicode 文字含む)以外を全削除。"""
    return re.sub(r"[\W_]+", "", (s or "").lower())


def _text(fragment: str, br_sep: str = " ") -> str:
    fragment = re.sub(r"<br\s*/?>", br_sep, fragment, flags=re.I)
    fragment = re.sub(r"</p\s*>|<p[^>]*>", br_sep, fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    fragment = htmlmod.unescape(fragment).replace("\xa0", " ")
    return re.sub(r"\s+", " ", fragment).strip()


def _cells(row_html: str, tag: str):
    return [_text(m) for m in re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", row_html, flags=re.S | re.I)]


def _table_html(h: str, cls: str):
    """class=cls の table を (開始〜対応する </table>) の文字列で全て返す。入れ子は考慮しない(現行ページに無い)。"""
    out = []
    for m in re.finditer(rf"<table\b[^>]*class=['\"]{cls}['\"][^>]*>", h, flags=re.I):
        end = h.find("</table", m.end())
        out.append(h[m.end(): end if end > 0 else len(h)])
    return out


def parse_live(raw: bytes) -> dict:
    h = raw.decode("utf-8", errors="replace")
    res: dict = {"specs": {}, "specs_order": [], "ships": [], "ship_headers": [], "sections": {}, "n_blue_tables": 0,
                 "n_cwd_tables": 0, "img_counts": {}, "heading": "", "h2_titles": []}
    m = re.search(r"<(?:a|div)[^>]*class=['\"]reds2['\"][^>]*>(.*?)</(?:a|div)>", h, flags=re.S | re.I)
    res["heading"] = _text(m.group(1)) if m else ""
    m = re.search(r"<h3[^>]*>(.*?)</h3>", h, flags=re.S | re.I)
    res["heading_h3"] = _text(m.group(1)) if m else ""
    blues = _table_html(h, "blue")
    res["n_blue_tables"] = len(blues)
    for ti, tb in enumerate(blues):
        for row in re.split(r"<tr\b[^>]*>", tb, flags=re.I)[1:]:
            ths, tds = _cells(row, "th"), _cells(row, "td")
            if ths and tds:
                label = ths[0]
                if ti == 0:
                    if label not in res["specs"]:
                        res["specs"][label] = tds[0]
                        res["specs_order"].append(label)
                else:
                    res.setdefault("extra_blue_rows", []).append((ti, label, tds[0]))
    cwds = _table_html(h, "cwdtable")
    res["n_cwd_tables"] = len(cwds)
    for ti, tb in enumerate(cwds):
        rows = re.split(r"<tr\b[^>]*>", tb, flags=re.I)[1:]
        if not rows:
            continue
        headers = _cells(rows[0], "th")
        if ti == 0:
            res["ship_headers"] = headers
        for row in rows[1:]:
            tds = _cells(row, "td")
            if not tds:
                continue
            if len(tds) < len(headers):
                tds += [""] * (len(headers) - len(tds))
            res["ships"].append({"table": ti, **{headers[i] if i < len(headers) else f"col{i}": v for i, v in enumerate(tds)}})
    # sections: <h2 ...>Title</h2> 〜 次の <h2
    parts = re.split(r"(<h2\b[^>]*>.*?</h2\s*>)", h, flags=re.S | re.I)
    for i in range(1, len(parts), 2):
        title = _text(parts[i])
        body = parts[i + 1] if i + 1 < len(parts) else ""
        res["h2_titles"].append(title)
        key = title.lower()
        n_img = len(re.findall(r"<img\b", body, flags=re.I))
        if key in STD_SECTION_SKIP:
            res["img_counts"][key] = n_img
            continue
        # footer などの後続を除去: 末尾の site footer を落とすため最初の <table ...cwdtable|blue> 以降は含めない
        body_cut = re.split(r"<table\b[^>]*class=['\"](?:cwdtable|blue)['\"]", body, maxsplit=1, flags=re.I)[0]
        body_cut = re.split(r"</div\b|<script\b|<ins\b|<!--end wrapper", body_cut, maxsplit=1, flags=re.I)[0]
        res["sections"][title] = {"text": _text(body_cut), "n_p": len(re.findall(r"<p\b", body_cut, flags=re.I)),
                                  "n_br": len(re.findall(r"<br\b", body_cut, flags=re.I))}
    res["has_photo_section"] = "photo" in [t.lower() for t in res["h2_titles"]]
    return res


def _load_fetcher():
    spec = importlib.util.spec_from_file_location("nsdb_fetcher_offline", FETCHER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nsdb_fetcher_offline"] = mod
    spec.loader.exec_module(mod)
    return mod


_FETCHER_MOD = None


def run_fetcher_offline(raw: bytes, url: str, country: str = "") -> dict:
    """fetcher.NavypediaFetcher.fetch_class_detail を raw HTML に対して実行 (DB/ネットワーク非接触)。"""
    global _FETCHER_MOD
    if _FETCHER_MOD is None:
        _FETCHER_MOD = _load_fetcher()
    F = _FETCHER_MOD.NavypediaFetcher
    obj = object.__new__(F)  # __init__ (DB 作成) を回避
    obj._cancel = False
    obj._get_soup = lambda u: BeautifulSoup(raw, "html.parser")
    obj._download_image_base64 = lambda *a, **k: ""
    detail = obj.fetch_class_detail(url, country)
    # DB 保存時の数値正規化を再現
    cols = {}
    for k, v in detail["specs"].items():
        col = _FETCHER_MOD.SPEC_TO_COLUMN.get(k)
        if col:
            cols[col] = _FETCHER_MOD._clean_numeric_spec(col, v)
    detail["spec_cols"] = cols
    return detail


SPEC_TO_COLUMN = None


def spec_to_column() -> dict:
    global _FETCHER_MOD
    if _FETCHER_MOD is None:
        _FETCHER_MOD = _load_fetcher()
    return _FETCHER_MOD.SPEC_TO_COLUMN
