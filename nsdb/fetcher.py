"""Navypedia (https://www.navypedia.org) データ収集ツール。

2026-10 の全面改修 (docs/nsdb_completeness_audit_2026-10-06.md §8 に対応):

- 取得元を現行サイト navypedia.org に統一 (ミラー myownonpmirror.com は 5xx のときだけの縮退フォールバック)。
- 艦種ページは「艦種ラベルの照合」ではなく、国ページのリンク構造から全て拾う。
- パースは純関数 (parse_class_page / parse_index_links) に分離し、HTML だけでテストできる。
  `<tr>` の閉じ忘れによる行のネスト (SHIPS の重複) は html.parser の木構造に頼らず、
  自前のイベント駆動パーサで回避する。
- 技術表: `Armour, mm` を Armour へ、潜水艦の standard/normal を別列に、未定義ラベルは捨てず extra_specs (JSON) へ。
- テキスト節: 全文を取り、インラインタグの境界で空白を足さない (`R-2andR-92were` を防ぐ)。表形式の節は行テキスト化。
- SHIPS: ヘッダ名で列を対応づける。Comp (竣工) を保存。群れ要約表 (Names/Builders/...) は SHIP_GROUPS へ。
- 礼儀正しい取得: ホスト単位のレート制限 (既定 0.9 req/s)、正直な User-Agent、robots.txt 確認、
  HTML のディスクキャッシュ (再パースはオフラインで可能)、中断・再開、二重起動ロック。
- 画像: 既定は旧 DB から source_url 単位でコピーし、旧 DB に無い艦級だけ取得する。

既存列の名前・意味は変えない (新情報は列の追加)。旧スキーマの DB に対しては列追加せず、存在する列だけを書く。
"""
import base64
import fcntl
import gzip
import hashlib
import html as html_lib
import json
import os
import posixpath
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import requests

LIVE_HOST = "www.navypedia.org"
BASE_URL = f"https://{LIVE_HOST}"
MIRROR_URL = "https://myownonpmirror.com"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "NSDB.db"
DEFAULT_CSV_PATH = Path(__file__).resolve().with_name("SHIP_TYPE_LIST.csv")
INDEX_URL = f"{BASE_URL}/ships_index.htm"
USER_AGENT = "nsdb-fetcher/2.0 (personal research; non-commercial; polite <=1 req/s; honors robots.txt)"
PARSER_VERSION = "2026-10-07"
DEFAULT_RATE = 0.9  # req/s/host (1 req/s 以下)

EXCLUSION_LIST = {
    '/',
    'https://www.navypedia.org/privacy.html',
    'https://www.paypal.com/paypalme/igogin',
    f'{BASE_URL}/ships_index.htm',
    f'{BASE_URL}/ships_index.html',
}

# テーブルの th (ラベル) → DBカラム名。正規化ラベル (小文字・空白圧縮) から単位を除いた基底名でも引く。
SPEC_TO_COLUMN = {
    "Displacement standard, t": "Displacement_standard",
    "Displacement normal, t": "Displacement_normal",
    "Displacement full, t": "Displacement_full",
    "Length, m": "Length",
    "Breadth, m": "Breadth",
    "Draught, m": "Draught",
    "No of shafts": "No_of_shafts",
    "Machinery": "Machinery",
    "Power, h. p.": "Power",
    "Max speed, kts": "speed",
    "Fuel, t": "Fuel",
    "Endurance, nm(kts)": "Endurance",
    "Armor": "Armour",
    "Armour": "Armour",
    "Armor, mm": "Armour",
    "Armour, mm": "Armour",
    "Armament": "Armament",
    "Electronic equipment": "Electronic_equipment",
    "Complement": "Complement",
}
_SPEC_BASE_TO_COLUMN = {
    "displacement standard": "Displacement_standard",
    "displacement normal": "Displacement_normal",
    "displacement full": "Displacement_full",
    "length": "Length",
    "breadth": "Breadth",
    "draught": "Draught",
    "no of shafts": "No_of_shafts",
    "machinery": "Machinery",
    "power": "Power",
    "max speed": "speed",
    "fuel": "Fuel",
    "endurance": "Endurance",
    "armor": "Armour",
    "armour": "Armour",
    "armament": "Armament",
    "electronic equipment": "Electronic_equipment",
    "complement": "Complement",
}

# 艦級表 (cwdtable) の見出し (正規化) → SHIPS 列。None の見出しは Extra (JSON) に入る。
_SHIP_HEADER_TO_COLUMN = {
    "name": "NAME",
    "no": "NO",
    "yard no": "Yard_No",
    "builder": "Builder",
    "laid down": "Laid_down",
    "launched": "Launched",
    "comp": "Comp",
    "completed": "Comp",
    "commissioned": "Comp",
    "comm": "Comp",
    "compl": "Comp",
    "compl // comm": "Comp",
    "fate": "Fate",
}
_GROUP_HEADERS = ("names", "builders", "losses", "transfers", "discarding")

# 画像系h2セクション
IMAGE_SECTIONS = {"standard scale images", "graphics"}
_SKIP_SECTIONS = {"menu", "photo", "ships", "technical data"}

# SHIPS.* に新設する / CLASS.* に新設する列 (旧スキーマには追加しない)
NEW_CLASS_COLUMNS = {
    "Displacement_standard": "TEXT",
    "WHEN_DATE": "TEXT",
    "HEADING": "TEXT",
    "extra_specs": "TEXT",
    "ships_kind": "TEXT",
    "source_site": "TEXT",
    "parser_version": "TEXT",
}
NEW_SHIPS_COLUMNS = {
    "Comp": "TEXT",
    "Fate": "TEXT",
    "Extra": "TEXT",
    "seq": "INTEGER",
}

# セル値が純数値スペース区切りのとき最大値に正規化するカラム群
_NUMERIC_SPEC_COLS = frozenset({
    'Displacement_normal', 'Displacement_standard', 'Displacement_full',
    'Length', 'Breadth', 'Draught', 'No_of_shafts', 'Power',
})

_MIRROR_NETLOC = urlparse(MIRROR_URL).netloc


# ---------------------------------------------------------------------------
# テキスト・URL ユーティリティ
# ---------------------------------------------------------------------------

def clean_text(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _clean_numeric_spec(col: str, val: str) -> str:
    """spec 値の数値正規化。桁区切りスペースを結合し、複数値の場合は最大値を採用。"""
    if col not in _NUMERIC_SPEC_COLS:
        return val
    stripped = val.strip()
    if not stripped:
        return val
    # 桁区切りスペースを結合（例: "14 394" → "14394"）
    if re.fullmatch(r'\d{1,3}(?:\s+\d{3})+', stripped):
        return stripped.replace(' ', '')
    # 複数の数値がスペース区切りの場合は最大値を採用（例: "71 77" → "77"）
    if re.fullmatch(r'[\d.]+(?:\s+[\d.]+)+', stripped):
        parts = stripped.split()
        try:
            return str(max(parts, key=float))
        except ValueError:
            pass
    return val


def normalize_url(href, base_url=BASE_URL):
    """href を現行サイトの正規 URL (https://www.navypedia.org/...) にする。

    - ミラー/apex ドメインも www.navypedia.org に寄せる (拡張子 .htm/.html はそのまま)。
    - `..` を解決し、空白は `%20` に統一する。クエリ・フラグメントは落とす。
    - サイト側の壊れた href (`file:///C:/seawar/ships/...`) は `/ships/...` に直す (取得は 404 になりうる)。
    """
    href = (href or "").strip()
    m = re.search(r"file:/+[A-Za-z]:/seawar(/ships/.+)$", href)
    if m:
        href = m.group(1)
    joined = urljoin(base_url, href)
    m = re.search(r"file:/+[A-Za-z]:/seawar(/ships/.+)$", joined)
    if m:
        joined = BASE_URL + m.group(1)
    parsed = urlparse(joined)
    netloc = parsed.netloc.lower()
    if netloc.endswith("navypedia.org") or netloc.endswith("myownonpmirror.com"):
        netloc = LIVE_HOST
    path = posixpath.normpath(parsed.path) if parsed.path else "/"
    if parsed.path.endswith("/") and not path.endswith("/"):
        path += "/"
    path = path.replace(" ", "%20")
    return urlunparse(("https", netloc, path, "", "", ""))


def canon_key(url):
    """ホスト・拡張子 (.htm/.html)・%20 の違いを吸収した突き合わせ用キー (path)。"""
    if not url:
        return ""
    p = urlparse(url if "://" in url else urljoin(BASE_URL, url))
    path = unquote(p.path)
    m = re.search(r"/seawar(/ships/.+)$", path)
    if m:
        path = m.group(1)
    path = posixpath.normpath(path)
    return re.sub(r"\.htm$", ".html", path)


def mirror_url_for(url):
    """現行 URL に対応するミラー URL (縮退フォールバック用)。"""
    p = urlparse(url)
    path = re.sub(r"\.htm$", ".html", p.path)
    return urlunparse(("https", _MIRROR_NETLOC, path, "", "", ""))


def title_case_class_name(text):
    text = clean_text(text)
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0] and text.count(text[0]) == 2:
        text = text[1:-1].strip()
    words = []
    for i, word in enumerate(text.split()):
        if word.isupper() or re.fullmatch(r"[A-Z0-9][A-Z0-9/-]*", word):
            words.append(word)
        elif i and word.lower() in _NAME_CONNECTORS:
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def country_short_name(text):
    cleaned = re.sub(r"[()/,]+", " ", clean_text(text))
    return "-".join(word[:1].upper() + word[1:].lower() for word in cleaned.split())


# ---------------------------------------------------------------------------
# 艦級見出し (class_name / ship_type / when_date)
# ---------------------------------------------------------------------------

_NAME_CONNECTORS = frozenset({
    "de", "da", "do", "dos", "das", "du", "di", "del", "della", "van", "von", "der", "den", "la", "le", "el",
    "al", "y", "e", "et", "and", "the", "ex", "of",
})
_NAME_LEADERS = frozenset({"pr", "pr.", "project", "projects", "type", "class", "no", "no.", "mk", "mk."})
_QUOTES = "\"'`\u201c\u201d\u2018\u2019"


def _tokenize_heading(text):
    """括弧の外の空白で区切る。括弧グループは 1 トークン。"""
    toks, cur, depth = [], [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if ch.isspace() and depth == 0:
            if cur:
                toks.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        toks.append("".join(cur))
    return toks


def _is_paren(tok):
    return tok.startswith("(") and tok.endswith(")")


def _is_lower_word(tok):
    """英小文字だけの語 (艦種語・前置詞など)。大文字・数字・引用符を含むものは名前の一部とみなす。"""
    return (any(c.isalpha() for c in tok)
            and not any(c.isupper() or c.isdigit() or c in _QUOTES for c in tok))


_TYPE_STOPWORDS = frozenset({"of", "from", "and", "with", "the", "during", "before", "after", "in", "to", "for", "at", "on"})


def _type_from_tokens(toks):
    words = []
    for t in toks:
        if _is_paren(t) or not _is_lower_word(t):
            continue
        words.extend(re.findall(r"[^\W\d_]+", t.lower()))
    words = [w for w in words if w != "class"]
    while words and words[-1] in _TYPE_STOPWORDS:
        words.pop()
    while words and words[0] in _TYPE_STOPWORDS:
        words.pop(0)
    return "-".join(words)


_YEAR_RE = re.compile(r"(?<!\d)(?:1[5-9]\d{2}|20\d{2})(?!\d)")


def _split_trailing_parens(text):
    """末尾の括弧グループ (入れ子可) を切り離す。返り値は (本体, [括弧グループ…] 左から順)。"""
    groups = []
    text = text.rstrip()
    while text.endswith(")"):
        depth, i = 0, len(text) - 1
        while i >= 0:
            if text[i] == ")":
                depth += 1
            elif text[i] == "(":
                depth -= 1
                if depth == 0:
                    break
            i -= 1
        if i < 0:
            break
        groups.insert(0, text[i:])
        text = text[:i].rstrip()
    return text, groups


def split_class_heading(text):
    """艦級見出しを (class_name, ship_type, when_date) に分解する。

    例: `YAMATO battleships (1941 - 1942)` → ('YAMATO Class', 'battleships', '(1941 - 1942)')
        `gunboats of WWII` → ('WWII Class', 'gunboats', '')
    CLASS_NAME は `<名前> Class`、TYPE は小文字語をハイフンで連結 (従来の形式)。
    原文は CLASS.HEADING に保存するので、ここで落とす括弧書き (project 番号など) も失われない。
    """
    text = re.sub(r"^\d+\.\s+", "", clean_text(text))
    # " - X Navy (Country)" のナビー文脈 (括弧の外・末尾) だけを除去する。年の範囲 "(1941 - 1942)" は触らない。
    text = re.sub(
        r"\s+-\s+[^()]*\b(?:Navy|Fleet|Forces?|Guard|Army|Service)\b[^()]*\([^()]*\)\s*$", "", text, flags=re.I
    )
    text, groups = _split_trailing_parens(text)
    year_groups = [g for g in groups if _YEAR_RE.search(g[1:-1])]
    if year_groups:
        when = " ".join(year_groups)  # 年を含む括弧書き (従来の WHEN_DATE の形式)
    else:
        when = ""
        text = (text + " " + " ".join(groups)).strip() if groups else text

    toks = _tokenize_heading(text)

    def name_like(i):
        return i < len(toks) and not _is_paren(toks[i]) and not _is_lower_word(toks[i])

    def next_name_like(i):
        while i < len(toks) and (_is_paren(toks[i]) is False) and toks[i].lower() in _NAME_CONNECTORS:
            i += 1
        return name_like(i)

    name, i = [], 0
    while i < len(toks):
        t = toks[i]
        if _is_paren(t):
            if name:
                name.append(t)
                i += 1
                continue
            break
        if not _is_lower_word(t):
            name.append(t)
            i += 1
            continue
        low = t.lower()
        if name and low in _NAME_CONNECTORS and next_name_like(i + 1):
            name.append(t)
            i += 1
            continue
        if name and low == "rate" and re.search(r"\d(?:st|nd|rd|th)$", name[-1]):
            name.append(t)  # "2nd rate"
            i += 1
            continue
        if not name and low in _NAME_LEADERS and name_like(i + 1):
            name.append(t)
            i += 1
            continue
        break
    # 名前部の末尾の括弧書きは名前に含める (次が艦種語のときだけ)。それ以外 (名前が空) は下で扱う。
    type_toks = toks[i:]

    if not name:
        # "gunboats of WWII" → 艦種=gunboats, 名前=WWII
        for j, t in enumerate(toks):
            if t.lower() in ("of", "from") and j > 0 and j + 1 < len(toks):
                name = toks[j + 1:]
                type_toks = toks[:j]
                break
        else:
            name = []
            type_toks = toks
    class_source = " ".join(name).strip(" ,")
    if class_source:
        class_name = f"{title_case_class_name(class_source)} Class"
    elif text:
        # 名前の無い説明的な見出し (すべて小文字) は見出し全体を名前に使う
        class_name = f"{text.strip(' ,')[:1].upper()}{text.strip(' ,')[1:]} Class"
    else:
        class_name = ""
    return class_name, _type_from_tokens(type_toks), when


# ---------------------------------------------------------------------------
# HTML パーサ (標準ライブラリのみ・`<tr>` の閉じ忘れに強い)
# ---------------------------------------------------------------------------

_BREAK_TAGS = frozenset({"p", "br", "div", "li", "ul", "ol", "tr", "table", "hr", "h1", "h2", "h3", "h4", "h5", "h6"})
_BR = "\ue000"        # 構造上の改行 (<br>/<p> など)。ソース中の改行は空白として扱う
_CELL_SEP = "\ue001"  # 表のセル区切り


def _finish_flow(buf, cell_sep=" | "):
    """収集した断片 (テキストと改行/セル区切りマーカー) を整形する。インラインタグの境界では空白を足さない。"""
    text = "".join(buf).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\n\f\v]+", " ", text)
    lines = []
    for line in text.split(_BR):
        if line.lstrip().startswith(_CELL_SEP):  # セル区切りは開始タグ側にだけ置く (先頭は空セル)
            line = line.lstrip()[1:]
        cells = [c.strip() for c in line.split(_CELL_SEP)]
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        s = cell_sep.join(cells) if len(cells) > 1 else cells[0]
        if s.strip():
            lines.append(s)
    return "\n".join(lines)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buf = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BREAK_TAGS:
            self.buf.append(_BR)
        elif tag in ("td", "th"):
            self.buf.append(_CELL_SEP)

    def handle_startendtag(self, tag, attrs):
        if tag in _BREAK_TAGS:
            self.buf.append(_BR)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BREAK_TAGS:
            self.buf.append(_BR)

    def handle_data(self, data):
        if not self._skip:
            self.buf.append(data)


def html_to_text(fragment, oneline=False):
    """HTML 断片をテキストにする。段落・<br> は改行、表のセルは ' | '。oneline なら全体を 1 行にする。"""
    p = _TextParser()
    p.feed(fragment or "")
    p.close()
    text = _finish_flow(p.buf)
    if oneline:
        text = clean_text(text.replace(" | ", " "))
    return text


class _TableParser(HTMLParser):
    """全ての <table> を文書順に集める。<tr>/<td>/<th> の閉じ忘れは次の開始タグで暗黙に閉じる。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []   # 文書順 (開始順)
        self._stack = []   # 開いているテーブル
        self._skip = 0

    # -- helpers
    def _cur(self):
        return self._stack[-1] if self._stack else None

    def _close_cell(self, t):
        if t is not None and t["cell"] is not None:
            cell = t["cell"]
            cell["text"] = _finish_flow(cell.pop("buf"))
            t["row"].append(cell)
            t["cell"] = None

    def _close_row(self, t):
        self._close_cell(t)
        if t is not None and t["row"]:
            t["rows"].append(t["row"])
        if t is not None:
            t["row"] = []

    def _open_cell(self, t, tag):
        self._close_cell(t)
        t["cell"] = {"tag": tag, "buf": []}

    # -- events
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag == "table":
            d = dict(attrs)
            t = {"class": (d.get("class") or "").lower(), "rows": [], "row": [], "cell": None}
            self.tables.append(t)
            self._stack.append(t)
            return
        t = self._cur()
        if t is None:
            return
        if tag == "tr":
            self._close_row(t)
        elif tag in ("td", "th"):
            self._open_cell(t, tag)
        elif tag in _BREAK_TAGS and t["cell"] is not None:
            t["cell"]["buf"].append(_BR)

    def handle_startendtag(self, tag, attrs):
        t = self._cur()
        if t is not None and tag in _BREAK_TAGS and t["cell"] is not None:
            t["cell"]["buf"].append(_BR)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        t = self._cur()
        if t is None:
            return
        if tag == "table":
            self._close_row(t)
            self._stack.pop()
        elif tag == "tr":
            self._close_row(t)
        elif tag in ("td", "th"):
            self._close_cell(t)
        elif tag in _BREAK_TAGS and t["cell"] is not None:
            t["cell"]["buf"].append(_BR)

    def handle_data(self, data):
        if self._skip:
            return
        t = self._cur()
        if t is not None and t["cell"] is not None:
            t["cell"]["buf"].append(data)

    def close(self):
        super().close()
        while self._stack:
            self._close_row(self._stack[-1])
            self._stack.pop()


def parse_tables(html_text):
    p = _TableParser()
    p.feed(html_text)
    p.close()
    return p.tables


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._cur = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "a":
            d = dict(attrs)
            self._cur = {"href": d.get("href") or "", "class": (d.get("class") or "").lower().split(), "buf": [], "img": ""}
        elif tag == "img" and self._cur is not None and not self._cur["img"]:
            self._cur["img"] = dict(attrs).get("src") or ""

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._cur is not None:
            cur = self._cur
            self._cur = None
            cur["text"] = clean_text("".join(cur.pop("buf")))
            self.links.append(cur)

    def handle_data(self, data):
        if self._cur is not None and not self._skip:
            self._cur["buf"].append(data)


def decode_html(raw):
    """bytes → str。BOM / <meta charset> を見て、無ければ UTF-8 → cp1252 の順に試す。"""
    if isinstance(raw, str):
        return raw
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    head = raw[:4096].decode("latin-1", errors="ignore").lower()
    m = re.search(r"charset\s*=\s*['\"]?\s*([\w-]+)", head)
    enc = m.group(1) if m else "utf-8"
    try:
        return raw.decode(enc, errors="strict")
    except (LookupError, UnicodeDecodeError):
        pass
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _strip_comments(text):
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


_NAV_CLASSES = frozenset({"blacks2", "reds2", "blacks_new", "reds"})


def parse_index_links(html_text, page_url, *, exclude=()):
    """一覧ページ (国一覧・国ページ・艦種ページ) の内容リンクを [{"name","url"}] で返す。

    - /ships/ 配下の非 `_index` リンク、またはページ自身以外のリンク。ナビゲーション (パンくず・HOME) は class
      (blacks2 / reds2) で除外する。ミラーは class が無いので、`_index` と page_url / exclude 自身を除く。
    - 艦種ラベルの照合はしない (構造だけで拾う)。URL は正規化・重複除去 (拡張子・%20 を吸収)。
    """
    p = _LinkParser()
    p.feed(_strip_comments(html_text))
    p.close()
    self_key = canon_key(normalize_url(page_url))
    skip = {canon_key(normalize_url(u)) for u in exclude}
    out, seen = [], set()
    for ln in p.links:
        href = ln["href"].strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        if _NAV_CLASSES.intersection(ln["class"]):
            continue
        url = normalize_url(href, page_url)
        up = urlparse(url)
        if up.netloc != LIVE_HOST or not up.path.startswith("/ships/"):
            continue
        key = canon_key(url)
        if re.search(r"_index\.html$", key) or key == self_key or key in skip or url in EXCLUSION_LIST:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": ln["text"], "url": url})
    return out


# 既存 DB の COUNTRY 値 (下流が United-Kingdom / United-States / Russian などをキーにしている) を保つための例外。
_COUNTRY_SHORT_OVERRIDES = {"New Zealand": "NewZealand", "Russian Federation": "Russian"}


def _country_short(text, img_src=""):
    """COUNTRY 値 (Short)。従来どおり国旗画像のファイル名 (空白→'-') を使い、無ければ表示名から作る。"""
    m = re.search(r"/flags/([^/]+?)\.[A-Za-z]+$", img_src or "")
    if m:
        stem = unquote(m.group(1))
        return _COUNTRY_SHORT_OVERRIDES.get(stem, stem.replace(" ", "-"))
    return country_short_name(text)


def parse_country_links(html_text, page_url=INDEX_URL):
    """国一覧ページから国 (Jamaica のような `_index` でないものも含む) を返す。"""
    p = _LinkParser()
    p.feed(_strip_comments(html_text))
    p.close()
    out, seen = [], set()
    for ln in p.links:
        href = ln["href"].strip()
        if not href or _NAV_CLASSES.intersection(ln["class"]):
            continue
        url = normalize_url(href, page_url)
        up = urlparse(url)
        if up.netloc != LIVE_HOST or not up.path.startswith("/ships/"):
            continue
        key = canon_key(url)
        if key in seen or not ln["text"]:
            continue
        seen.add(key)
        name = ln["text"]
        out.append({"short": _country_short(name, ln.get("img", "")), "long": name.replace(" ", "-"), "url": url})
    return out


# ---------------------------------------------------------------------------
# 艦級ページのパース
# ---------------------------------------------------------------------------

def _norm_label(label):
    return re.sub(r"\s+", " ", (label or "").replace("\xa0", " ").lower()).strip()


def spec_column_for(label):
    """技術表ラベル → CLASS 列名 (未定義は None)。"""
    exact = SPEC_TO_COLUMN.get(clean_text(label))
    if exact:
        return exact
    base = _norm_label(label).split(",")[0].strip()
    base = re.sub(r"\s*\(.*\)$", "", base)
    return _SPEC_BASE_TO_COLUMN.get(base)


def specs_to_columns(specs):
    """specs {label: value} → {CLASS 列: 値}。

    Displacement_standard は standard の値。Displacement_normal は従来どおり『normal があれば normal、
    無ければ standard』(従来の列の意味を維持する)。潜水艦のように両方ある場合も片方が消えない。
    """
    cols = {}
    for label, val in specs.items():
        col = spec_column_for(label)
        if col and col not in cols:
            cols[col] = _clean_numeric_spec(col, val)
    if "Displacement_normal" not in cols and cols.get("Displacement_standard"):
        cols["Displacement_normal"] = cols["Displacement_standard"]
    return cols


def _spec_value(text):
    val = clean_text(text.replace("\n", " ")).replace(',', '+')
    return re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', val)


def _is_ship_table(t):
    if not t["rows"]:
        return None
    heads = [_norm_label(c["text"]) for c in t["rows"][0]]
    if ("name" in heads or "names" in heads) and ("builder" in heads or "builders" in heads):
        return "group" if "names" in heads else "individual"
    return None


def _is_spec_table(t):
    if "blue" in t["class"].split():
        return True
    hits = sum(1 for r in t["rows"] if len(r) >= 2 and spec_column_for(r[0]["text"]))
    return hits >= 2


def _clean_ship_cell(text):
    return clean_text(text.replace("\n", " ")).replace(',', '-').replace('[', '<').replace(']', '>')


def _ship_column(header):
    """見出し → (SHIPS 列 | None, 正規化見出し)。"""
    h = re.sub(r"[.\s]+$", "", _norm_label(header))
    h = re.sub(r"\.", "", h)
    h = re.sub(r"\s*//\s*", " // ", h)
    return _SHIP_HEADER_TO_COLUMN.get(h), h


def _parse_ship_table(t, kind):
    rows = t["rows"]
    headers = [clean_text(c["text"]) for c in rows[0]]
    if kind == "group":
        groups = []
        for r in rows[1:]:
            cells = [c["text"] for c in r]
            if not any(clean_text(x) for x in cells):
                continue
            row = {headers[j] if j < len(headers) else f"col{j}": cells[j] for j in range(len(cells))}
            groups.append(row)
        return headers, groups
    colmap = [_ship_column(h) for h in headers]
    ships = []
    for r in rows[1:]:
        cells = [_clean_ship_cell(c["text"]) for c in r]
        if not any(cells):
            continue
        if [x.lower() for x in cells] == [h.lower() for h in headers]:
            continue
        ship, extra = {}, {}
        for j, val in enumerate(cells):
            if j < len(colmap):
                col, norm = colmap[j]
                if col is None:
                    if norm == "launched // comm":
                        left, _, right = val.partition("//")
                        ship["Launched"] = left.strip()
                        if right.strip():
                            ship["Comp"] = right.strip()
                        extra[headers[j]] = val
                    elif val:
                        extra[headers[j]] = val
                elif col not in ship:
                    ship[col] = val
                elif val:
                    extra[f"{headers[j]}#{j}"] = val
            elif val:
                extra[f"col{j}"] = val
        ship["_extra"] = extra
        ships.append(ship)
    return headers, ships


def _section_key(title):
    """`Modernizations 9.` のような番号付き見出しを基底名に正規化する。"""
    base = re.sub(r"\s+\d+\.?\s*$", "", title).strip()
    return base or title


def _cut_body(body):
    """節の本文から、次の h2 以降・ページ末尾 (フッター) を落とす。"""
    ends = [len(body)]
    for pat in (r"<div\b[^>]*class=['\"]clear['\"]", r"<div\b[^>]*id=['\"]srapper['\"]",
                r"<div\b[^>]*id=['\"]copyright['\"]", r"<ins\b", r"<script\b"):
        m = re.search(pat, body, flags=re.I)
        if m:
            ends.append(m.start())
    return body[:min(ends)]


def _first_img_src(fragment, prefer_class=None):
    imgs = re.findall(r"<img\b[^>]*>", fragment, flags=re.I)
    best = None
    for tag in imgs:
        m = re.search(r"\bsrc\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", tag, flags=re.I)
        if not m:
            continue
        src = html_lib.unescape(m.group(1) or m.group(2) or m.group(3) or "").strip()
        if not src:
            continue
        if prefer_class and re.search(rf"class\s*=\s*['\"][^'\"]*{prefer_class}", tag, flags=re.I):
            return src
        if best is None:
            best = src
    return best


def extract_heading(text_nc):
    """見出しの原文。現行: `a.reds2` / `div.reds2`、ミラー: <h3>。いずれも無ければ <title>。"""
    m = re.search(r"<(a|div)\b[^>]*class=['\"][^'\"]*\breds2\b[^'\"]*['\"][^>]*>(.*?)</\1\s*>", text_nc, flags=re.S | re.I)
    if m:
        h = html_to_text(m.group(2), oneline=True)
        if h:
            return h
    for tag in ("h3", "title"):
        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", text_nc, flags=re.S | re.I)
        if m:
            h = html_to_text(m.group(1), oneline=True)
            if h:
                return h
    return ""


def parse_class_page(raw, url, country="", site="live"):
    """艦級ページ HTML を detail dict にする (ネットワーク・DB 非接触の純関数)。

    返り値のキー: url, country, heading, class_name, ship_type, when_date, specs {label: value},
    spec_cols {列: 値}, extra_specs {label: 値}, ships [dict], ships_headers, ship_groups [dict], ships_kind,
    sections {名前: テキスト}, image_urls {photo, images, graphics}, photo_base64 / images_base64 / graphics_base64 (空)。
    """
    text = _strip_comments(decode_html(raw))
    result = {
        "url": url, "country": country, "heading": "", "class_name": "", "ship_type": "", "when_date": "",
        "photo_base64": "", "images_base64": "", "graphics_base64": "",
        "image_urls": {"photo": "", "images": "", "graphics": ""},
        "specs": {}, "spec_cols": {}, "extra_specs": {},
        "ships": [], "ships_headers": [], "ship_groups": [], "ships_kind": "none",
        "sections": {}, "source_site": site, "parser_version": PARSER_VERSION,
    }
    heading = extract_heading(text)
    result["heading"] = heading
    result["class_name"], result["ship_type"], result["when_date"] = split_class_heading(heading)

    # --- テーブル (技術表・艦級表)
    spec_seen = 0
    for t in parse_tables(text):
        kind = _is_ship_table(t)
        if kind:
            headers, rows = _parse_ship_table(t, kind)
            if not result["ships_headers"]:
                result["ships_headers"] = headers
            if kind == "group":
                result["ship_groups"].extend(rows)
            else:
                result["ships"].extend(rows)
            continue
        if _is_spec_table(t):
            spec_seen += 1
            for r in t["rows"]:
                if len(r) < 2:
                    continue
                label = clean_text(r[0]["text"].replace("\n", " "))
                if not label:
                    continue
                val = _spec_value(r[1]["text"])
                known = spec_column_for(label) is not None
                if spec_seen == 1 and known and label not in result["specs"]:
                    result["specs"][label] = val
                else:
                    key = label if spec_seen == 1 else f"{label} [table {spec_seen}]"
                    if key in result["extra_specs"] or known and spec_seen == 1:
                        key = f"{key}#{len(result['extra_specs']) + 2}"
                    result["extra_specs"][key] = clean_text(r[1]["text"].replace("\n", " "))
                for n, extra in enumerate(r[2:], start=3):
                    ev = clean_text(extra["text"].replace("\n", " "))
                    if ev:
                        result["extra_specs"][f"{label} [col {n}]"] = ev
    result["ships_kind"] = "individual" if result["ships"] else ("group" if result["ship_groups"] else "none")
    result["spec_cols"] = specs_to_columns(result["specs"])

    # --- 節 (h2 〜 次の h2)。ミラー (2021 HTTrack) の h2 は節ではなく艦種見出しなので節は取らない (縮退)
    parts = [] if site == "mirror" else re.split(r"(<h2\b[^>]*>.*?</h2\s*>)", text, flags=re.S | re.I)
    for i in range(1, len(parts), 2):
        title = html_to_text(parts[i], oneline=True)
        body = _cut_body(parts[i + 1] if i + 1 < len(parts) else "")
        lower = title.lower()
        if lower in _SKIP_SECTIONS:
            if lower == "photo":
                result["image_urls"]["photo"] = _first_img_src(body, "css-adaptive") or ""
            continue
        if any(k in lower for k in IMAGE_SECTIONS):
            src = _first_img_src(body) or ""
            if "graphics" in lower:
                result["image_urls"]["graphics"] = src
            else:
                result["image_urls"]["images"] = src
            continue
        content = html_to_text(body)
        key = _section_key(title)
        if key in result["sections"]:
            content = (result["sections"][key] + "\n" + content).strip()
        result["sections"][key] = content
    return result


# ---------------------------------------------------------------------------
# 取得層: レート制限・HTML キャッシュ・ロック
# ---------------------------------------------------------------------------

class StopFetch(RuntimeError):
    """ブロック・チャレンジ・連続失敗など、回避せず停止すべき状態。"""


class FetchError(RuntimeError):
    def __init__(self, url, status, message=""):
        super().__init__(f"{status} {url} {message}".strip())
        self.url = url
        self.status = status


class OfflineMiss(FetchError):
    """--offline でキャッシュに無かった。"""


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


class RateLimiter:
    """ホストごとの最小リクエスト間隔 (スレッド間で共有)。"""

    def __init__(self, rate=DEFAULT_RATE):
        self.interval = 1.0 / rate if rate and rate > 0 else 0.0
        self._next = {}
        self._lock = threading.Lock()

    def set_interval(self, seconds):
        self.interval = max(self.interval, seconds)

    def wait(self, host):
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next.get(host, 0.0))
            self._next[host] = at + self.interval
        delay = at - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def penalize(self, host, seconds):
        with self._lock:
            self._next[host] = max(self._next.get(host, 0.0), time.monotonic() + seconds)


class HtmlCache:
    """取得した HTML (200) / 404 マーカーをディスクに保存する。再パースはネットワーク無しで行える。"""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, url):
        p = urlparse(url)
        key = hashlib.sha1(url.encode()).hexdigest()[:12]
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", p.path.strip("/"))[-100:]
        host = "m" if "myownonpmirror" in p.netloc else "o"
        return self.dir / f"{host}__{name}__{key}"

    def get(self, url):
        base = str(self._path(url))
        gz = Path(base + ".html.gz")
        if gz.exists():
            try:
                return 200, gzip.decompress(gz.read_bytes())
            except (OSError, EOFError):
                return None
        mark = Path(base + ".status")
        if mark.exists():
            try:
                return int(mark.read_text().strip()), b""
            except ValueError:
                return None
        return None

    def put(self, url, status, content):
        base = str(self._path(url))
        if status == 200:
            tmp = Path(base + ".html.gz.tmp")
            tmp.write_bytes(gzip.compress(content, compresslevel=6))
            os.replace(tmp, Path(base + ".html.gz"))
        elif status in (404, 410):
            Path(base + ".status").write_text(str(status))


class RunLock:
    """DB ごとの二重起動防止 (fcntl.flock)。プロセスが死ねば自動で解放される。"""

    def __init__(self, db_path):
        self.path = Path(str(db_path) + ".lock")
        self._fh = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.seek(0)
            holder = fh.read().strip()
            fh.close()
            raise RuntimeError(f"別のプロセスが実行中です (lock: {self.path}, {holder or 'pid 不明'})")
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()} started={datetime.now().isoformat(timespec='seconds')}\n")
        fh.flush()
        self._fh = fh
        return self

    def release(self):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def _load_ship_types(csv_path=DEFAULT_CSV_PATH):
    """旧来の艦種ラベル一覧 (現在は発見には使わない。互換のため属性として残す)。"""
    ship_types = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    ship_types.append(line.split(']')[0].strip('[').replace(',', '-'))
    except OSError:
        pass
    return ship_types


class LegacyImageSource:
    """旧 DB (mode=ro) から source_url 単位で画像列 (Photo/images/Graphics) を引く。

    旧 DB の source_url はミラー形式 (https://myownonpmirror.com/...html) のことがあるので、canon_key で正規化して突き合わせる。
    起動時は rowid と URL だけを索引し、画像本体 (base64) は必要な行だけ読む。
    """

    def __init__(self, path):
        self.path = str(path)
        uri = f"file:{self.path}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._conn.execute("PRAGMA query_only=1")
        self._lock = threading.Lock()
        self.index = {}
        for rowid, url in self._conn.execute("SELECT rowid, source_url FROM CLASS WHERE source_url IS NOT NULL"):
            self.index.setdefault(canon_key(url), []).append((rowid, "myownonpmirror" not in url))

    def has_live(self, url):
        return any(is_live for _, is_live in self.index.get(canon_key(url), []))

    def has_any(self, url):
        return canon_key(url) in self.index

    def images_for(self, url):
        """{photo, images, graphics} (base64)。複数行があれば列ごとに最長の非空値を採る。"""
        rows = self.index.get(canon_key(url), [])
        best = {"photo": "", "images": "", "graphics": ""}
        for rowid, _ in rows:
            with self._lock:
                r = self._conn.execute("SELECT Photo, images, Graphics FROM CLASS WHERE rowid=?", (rowid,)).fetchone()
            for key, val in zip(("photo", "images", "graphics"), r or ()):
                if val and len(val) > len(best[key]):
                    best[key] = val
        return best

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# SHIPS.Fate の分解
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"(?<![\w.])(?:\d{1,2}[./]){0,2}(?:1[5-9]|20)\d{2}(?!\d)")


def split_fate(fate_raw):
    """Fate 原文 → (理由, 日付)。理由は数字・記号を除いた語 (空白は保持)、日付は日付らしい語を空白区切りで。"""
    fate_raw = fate_raw or ""
    why = clean_text(re.sub(r"[\d_]+|[^\w\s]+", " ", fate_raw))
    dates = _DATE_RE.findall(fate_raw)
    when = " ".join(dates) if dates else re.sub(r'[^\d./\-]', '', fate_raw)
    return why, when


def robots_allows(robots_text, url, user_agent=USER_AGENT):
    """robots.txt の本文に対し url を取得してよいか (rp, crawl_delay) を返す。"""
    from urllib.robotparser import RobotFileParser
    rp = RobotFileParser()
    rp.parse(robots_text.splitlines())
    return rp.can_fetch(user_agent, url), rp.crawl_delay(user_agent)


# ---------------------------------------------------------------------------
# 取得クラス
# ---------------------------------------------------------------------------

_CLASS_COLUMNS = [
    "Photo", "CLASS_NAME", "TYPE", "COUNTRY", "Displacement_normal", "Displacement_full", "Length", "Breadth",
    "Draught", "No_of_shafts", "Machinery", "Power", "speed", "Fuel", "Endurance", "Armour", "Armament",
    "Electronic_equipment", "Complement", "images", "Graphics", "Project_history", "Modernizations", "Naval_service",
    "sections", "source_url", "fetched_at",
]
_SHIPS_COLUMNS = [
    "NAME", "CLASS", "COUNTRY", "NO", "Yard_No", "Builder", "Laid_down", "Launched", "Fate_WHY", "Fate_WHEN",
    "TYPE", "SID", "WHEN_DATE", "source_url",
]
_GROUP_COLUMNS = {
    "COUNTRY": "TEXT", "CLASS": "TEXT", "source_url": "TEXT", "seq": "INTEGER", "Names": "TEXT", "Builders": "TEXT",
    "Completed": "TEXT", "Date_label": "TEXT", "Losses": "TEXT", "Transfers": "TEXT", "Discarding": "TEXT", "Extra": "TEXT",
}


def _col_defs(cols, types=None):
    return ", ".join(f"{c} {(types or {}).get(c, 'TEXT')}" for c in cols)


class NavypediaFetcher:

    def __init__(self, db_path=DEFAULT_DB_PATH, csv_path=DEFAULT_CSV_PATH, max_workers=2, *,
                 rate=DEFAULT_RATE, user_agent=USER_AGENT, cache_dir=None, offline=False,
                 images="fetch", images_from=None, images_missing="fetch",
                 migrate=None, mirror_fallback=True, max_requests=None, retries=3):
        """
        images: 'skip' | 'fetch' (サイトから取得) | 'copy' (images_from の旧 DB からコピー。旧 DB に無い艦級は
                images_missing='fetch' なら取得、'skip' なら空)
        migrate: None=新規 DB のときだけスキーマを作る / True=既存 DB にも列・表を追加 / False=一切追加しない
                 (旧スキーマの DB には存在する列だけを書く)
        """
        self.db_path = Path(db_path)
        self.max_workers = max(1, int(max_workers))
        self.ship_types = _load_ship_types(csv_path)
        self.user_agent = user_agent
        self.offline = offline
        self.retries = retries
        self.max_requests = max_requests
        self.mirror_fallback = mirror_fallback
        self.limiter = RateLimiter(rate)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/html,*/*;q=0.5"})
        self.cache = HtmlCache(cache_dir) if cache_dir else None
        self.images_mode = images
        self.images_missing = images_missing
        self.legacy = LegacyImageSource(images_from) if images == "copy" and images_from else None
        if images == "copy" and self.legacy is None:
            raise ValueError("images='copy' には images_from (旧 DB パス) が必要です")
        self._cancel = False
        self._lock = threading.Lock()
        self._consec_fail = 0
        self.stats = {"net_requests": 0, "cache_hits": 0, "classes_ok": 0, "classes_err": 0, "classes_skipped": 0,
                      "images_copied": 0, "images_fetched": 0, "images_failed": 0, "mirror_fallbacks": 0}
        self._t0 = time.time()
        existed = self.db_path.exists() and self.db_path.stat().st_size > 0
        self.migrate = (not existed) if migrate is None else bool(migrate)
        self._ensure_main_schema()
        self._ensure_cache_tables()

    # ------------------------------------------------------------------
    # スキーマ
    # ------------------------------------------------------------------

    def _get_conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=120)
        return conn

    @staticmethod
    def _columns(conn, table):
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]

    def _ensure_main_schema(self):
        """新規 DB に全テーブルを作る。既存 DB は migrate=True のときだけ列・表を追加する。"""
        conn = self._get_conn()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS COUNTRIES (Short TEXT, Long TEXT, URL TEXT)")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS CLASS (
                    {_col_defs(_CLASS_COLUMNS)}
                )
            """.replace("source_url TEXT", "source_url TEXT UNIQUE"))
            conn.execute(f"CREATE TABLE IF NOT EXISTS SHIPS ({_col_defs(_SHIPS_COLUMNS)})")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS TRANSLATIONS (
                    source_url TEXT NOT NULL,
                    section_name TEXT NOT NULL,
                    translated TEXT,
                    PRIMARY KEY (source_url, section_name)
                )
            """)
            if self.migrate:
                for table, new in (("CLASS", NEW_CLASS_COLUMNS), ("SHIPS", NEW_SHIPS_COLUMNS)):
                    have = set(self._columns(conn, table))
                    for col, typ in new.items():
                        if col not in have:
                            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                conn.execute(f"CREATE TABLE IF NOT EXISTS SHIP_GROUPS ({_col_defs(_GROUP_COLUMNS, _GROUP_COLUMNS)})")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS FETCH_ERRORS (
                        url TEXT PRIMARY KEY, kind TEXT, status INTEGER, message TEXT,
                        attempts INTEGER, last_try TEXT
                    )
                """)
                conn.execute("CREATE TABLE IF NOT EXISTS FETCH_STATE (key TEXT PRIMARY KEY, value TEXT)")
                for ddl in (
                    "CREATE INDEX IF NOT EXISTS idx_class_country ON CLASS(COUNTRY)",
                    "CREATE INDEX IF NOT EXISTS idx_class_type ON CLASS(TYPE)",
                    "CREATE INDEX IF NOT EXISTS idx_class_country_type ON CLASS(COUNTRY, TYPE)",
                    "CREATE INDEX IF NOT EXISTS idx_class_name ON CLASS(CLASS_NAME)",
                    "CREATE INDEX IF NOT EXISTS idx_ships_country ON SHIPS(COUNTRY)",
                    "CREATE INDEX IF NOT EXISTS idx_ships_type ON SHIPS(TYPE)",
                    "CREATE INDEX IF NOT EXISTS idx_ships_class ON SHIPS(CLASS)",
                    "CREATE INDEX IF NOT EXISTS idx_ships_country_type ON SHIPS(COUNTRY, TYPE)",
                    "CREATE INDEX IF NOT EXISTS idx_ships_name ON SHIPS(NAME)",
                    "CREATE INDEX IF NOT EXISTS idx_ships_builder ON SHIPS(Builder)",
                    "CREATE INDEX IF NOT EXISTS idx_ships_laid_down ON SHIPS(Laid_down)",
                    "CREATE INDEX IF NOT EXISTS idx_ships_fate ON SHIPS(Fate_WHY)",
                    "CREATE INDEX IF NOT EXISTS idx_ships_source_url ON SHIPS(source_url)",
                    "CREATE INDEX IF NOT EXISTS idx_groups_source_url ON SHIP_GROUPS(source_url)",
                    "CREATE INDEX IF NOT EXISTS idx_countries_short ON COUNTRIES(Short)",
                ):
                    try:
                        conn.execute(ddl)
                    except sqlite3.OperationalError:  # 既存 DB に無い列の索引は作らない
                        pass
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                except sqlite3.DatabaseError:
                    pass
            conn.commit()
        finally:
            conn.close()

    def _ensure_cache_tables(self):
        """艦種リスト・艦級URLリストのキャッシュテーブルを作成"""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS CACHE_SHIP_TYPES (
                    country_url TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    url         TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS CACHE_CLASS_URLS (
                    type_url    TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    url         TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cst_country ON CACHE_SHIP_TYPES(country_url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ccu_type ON CACHE_CLASS_URLS(type_url)")
            conn.commit()
        finally:
            conn.close()

    def _has_table(self, conn, table):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    def cancel(self):
        self._cancel = True

    def set_state(self, key, value):
        conn = self._get_conn()
        try:
            if self._has_table(conn, "FETCH_STATE"):
                conn.execute("INSERT OR REPLACE INTO FETCH_STATE (key, value) VALUES (?, ?)", (key, str(value)))
                conn.commit()
        finally:
            conn.close()

    def _record_error(self, url, kind, status, message):
        conn = self._get_conn()
        try:
            if self._has_table(conn, "FETCH_ERRORS"):
                conn.execute(
                    """INSERT INTO FETCH_ERRORS (url, kind, status, message, attempts, last_try)
                       VALUES (?, ?, ?, ?, 1, ?)
                       ON CONFLICT(url) DO UPDATE SET kind=excluded.kind, status=excluded.status,
                       message=excluded.message, attempts=attempts+1, last_try=excluded.last_try""",
                    (url, kind, status, str(message)[:500], datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def check_robots(self):
        """robots.txt を確認する (禁止なら StopFetch、Crawl-delay があればレートに反映)。"""
        url = f"{BASE_URL}/robots.txt"
        if self.offline:
            return
        self.limiter.wait(LIVE_HOST)
        r = self.session.get(url, timeout=(10, 30))
        self.stats["net_requests"] += 1
        if r.status_code in (404, 410):
            return
        r.raise_for_status()
        ok, delay = robots_allows(r.text, f"{BASE_URL}/ships/", self.user_agent)
        if not ok:
            raise StopFetch("robots.txt が /ships/ の取得を禁止しています")
        if delay:
            self.limiter.set_interval(float(delay))
        log(f"robots.txt OK (crawl-delay={delay})")

    def _http(self, url, cacheable=True):
        """(status, bytes)。404/410 は (status, b'')。5xx・通信エラーはリトライ後 (status, b'')。"""
        if cacheable and self.cache is not None:
            hit = self.cache.get(url)
            if hit is not None:
                with self._lock:
                    self.stats["cache_hits"] += 1
                return hit
        if self.offline:
            raise OfflineMiss(url, 0, "offline (cache miss)")
        host = urlparse(url).netloc
        status, content, err = 0, b"", ""
        for attempt in range(1, self.retries + 1):
            with self._lock:
                if self.max_requests is not None and self.stats["net_requests"] >= self.max_requests:
                    raise StopFetch(f"max_requests={self.max_requests} に達しました")
                self.stats["net_requests"] += 1
            self.limiter.wait(host)
            status, content, err = 0, b"", ""
            retry_after = None
            try:
                r = self.session.get(url, timeout=(10, 40))
                status, content = r.status_code, r.content
                retry_after = r.headers.get("Retry-After")
            except requests.RequestException as e:
                err = repr(e)[:200]
            head = content[:4000].decode("latin-1", errors="ignore").lower() if content else ""
            if status in (403, 429, 503) or (status == 200 and len(content) < 20000 and (
                    "cf-chl" in head or "just a moment" in head or "captcha" in head or "attention required" in head)):
                if status == 429 and attempt < self.retries:
                    wait = 60.0
                    if retry_after and retry_after.isdigit():
                        wait = max(wait, float(retry_after))
                    log(f"429 Too Many Requests: {wait:.0f}s 待機 ({url})")
                    self.limiter.penalize(host, wait)
                    continue
                raise StopFetch(f"ブロック/チャレンジの疑い status={status} url={url}")
            if status == 200:
                with self._lock:
                    self._consec_fail = 0
                if cacheable and self.cache is not None:
                    self.cache.put(url, status, content)
                return status, content
            if status in (404, 410):
                with self._lock:
                    self._consec_fail = 0
                if cacheable and self.cache is not None:
                    self.cache.put(url, status, b"")
                return status, b""
            # 5xx / 通信エラー
            with self._lock:
                self._consec_fail += 1
                if self._consec_fail >= 30:
                    raise StopFetch(f"連続 {self._consec_fail} 回の失敗 (最後: {status} {err}) url={url}")
            if attempt < self.retries:
                backoff = 3.0 * (2 ** (attempt - 1))
                self.limiter.penalize(host, backoff)
        return status, b""

    @staticmethod
    def _url_variants(url):
        """URL に %20 を含むとき、現行サイトは二重エンコード (%2520) でだけ 200 を返すので先に試す。"""
        if "%20" in url:
            return [url.replace("%20", "%2520"), url]
        return [url]

    def _get_html(self, url):
        """(status, bytes, site)。現行サイト優先。現行が 5xx のときだけミラーに縮退する。"""
        status, raw = 0, b""
        try:
            for cand in self._url_variants(url):
                status, raw = self._http(cand)
                if status == 200:
                    return status, raw, "live"
                if status not in (404, 410):
                    break
        except OfflineMiss:
            if not self.mirror_fallback:
                raise
            status = 0  # オフライン再構築: 現行が 5xx だった艦級はキャッシュ済みのミラー版を探す
        if self.mirror_fallback and (status >= 500 or status == 0):
            try:
                m_status, m_raw = self._http(mirror_url_for(url))
            except OfflineMiss:
                raise FetchError(url, 0, "offline (cache miss)")
            if m_status == 200:
                self.stats["mirror_fallbacks"] += 1
                log(f"現行が {status} のためミラーで取得: {url}")
                return m_status, m_raw, "mirror"
        return status, raw, "live"

    def _get_page_text(self, url):
        status, raw, _ = self._get_html(url)
        if status != 200:
            raise FetchError(url, status)
        return decode_html(raw)

    # ------------------------------------------------------------------
    # 階層取得
    # ------------------------------------------------------------------

    def fetch_countries(self, index_url=INDEX_URL):
        """国家一覧ページから国URLを取得する。"""
        return parse_country_links(self._get_page_text(index_url), index_url)

    def save_countries(self, countries, replace=False):
        """COUNTRIESテーブルへ国家一覧を保存する。replace=Trueなら全置換。"""
        conn = self._get_conn()
        try:
            if replace:
                conn.execute("DELETE FROM COUNTRIES")
            for country in countries:
                conn.execute(
                    "INSERT INTO COUNTRIES (Short, Long, URL) VALUES (?, ?, ?)",
                    (country["short"], country["long"], country["url"]),
                )
            conn.commit()
        finally:
            conn.close()

    def get_cached_ship_types(self, country_url):
        """DBキャッシュから艦種リストを返す。なければNone"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT name, url FROM CACHE_SHIP_TYPES WHERE country_url = ?", (country_url,)
            ).fetchall()
            return [{"name": r[0], "url": r[1]} for r in rows] if rows else None
        finally:
            conn.close()

    def get_cached_class_urls(self, type_url):
        """DBキャッシュから艦級URLリストを返す。なければNone"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT name, url FROM CACHE_CLASS_URLS WHERE type_url = ?", (type_url,)
            ).fetchall()
            return [{"name": r[0], "url": r[1]} for r in rows] if rows else None
        finally:
            conn.close()

    def fetch_ship_types(self, country_url):
        """国ページから艦種(カテゴリ)ページのURLリストを返す（キャッシュ優先）。

        艦種ラベルとの照合はしない。国ページの内容リンク (ナビゲーション以外の /ships/ 配下) を全て拾う。
        """
        country_url = normalize_url(country_url)
        cached = self.get_cached_ship_types(country_url)
        if cached is not None:
            return cached
        results = parse_index_links(self._get_page_text(country_url), country_url)
        if results:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM CACHE_SHIP_TYPES WHERE country_url = ?", (country_url,))
                conn.executemany(
                    "INSERT INTO CACHE_SHIP_TYPES (country_url, name, url) VALUES (?, ?, ?)",
                    [(country_url, r["name"], r["url"]) for r in results],
                )
                conn.commit()
            finally:
                conn.close()
        return results

    def fetch_class_urls(self, type_url, exclude=()):
        """艦種ページから艦級URLリストを返す（キャッシュ優先）。exclude=パンくず等で除外したいURL (国ページなど)。"""
        type_url = normalize_url(type_url)
        cached = self.get_cached_class_urls(type_url)
        if cached is not None:
            return cached
        results = parse_index_links(self._get_page_text(type_url), type_url, exclude=exclude)
        if results:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM CACHE_CLASS_URLS WHERE type_url = ?", (type_url,))
                conn.executemany(
                    "INSERT INTO CACHE_CLASS_URLS (type_url, name, url) VALUES (?, ?, ?)",
                    [(type_url, r["name"], r["url"]) for r in results],
                )
                conn.commit()
            finally:
                conn.close()
        return results

    def discover_country(self, country_url):
        """国の全艦級 [{"name","url"}] を (艦種ページ経由で) 列挙する。重複 (拡張子・%20 違い) は除く。"""
        country_url = normalize_url(country_url)
        items, seen = [], set()
        for st in self.fetch_ship_types(country_url):
            if self._cancel:
                break
            try:
                found = self.fetch_class_urls(st["url"], exclude=[country_url])
            except FetchError as e:
                log(f"  艦種ページ取得失敗 {e}")
                self._record_error(st["url"], "type_page", e.status, e)
                continue
            if not found:
                log(f"  艦級リンク 0 件の艦種ページ: {st['url']} ({st['name']})")
            for it in found:
                key = canon_key(it["url"])
                if key not in seen:
                    seen.add(key)
                    items.append(it)
        return items

    # ------------------------------------------------------------------
    # 艦級ページ
    # ------------------------------------------------------------------

    def fetch_class_detail(self, class_url, country=""):
        """艦級ページから全データを1回のアクセスで抽出 (画像は images 設定に従う)。"""
        class_url = normalize_url(class_url)
        status, raw, site = self._get_html(class_url)
        if status != 200:
            raise FetchError(class_url, status)
        detail = parse_class_page(raw, class_url, country, site=site)
        if not detail["heading"] or (not detail["specs"] and not detail["ships"] and not detail["ship_groups"]
                                      and not detail["sections"]):
            with self._lock:
                self.stats["parse_warnings"] = self.stats.get("parse_warnings", 0) + 1
            log(f"警告: 内容が薄いページ (見出し={detail['heading']!r}): {class_url}")
        self._attach_images(detail)
        return detail

    def _attach_images(self, detail):
        mode = self.images_mode
        if mode == "skip":
            return
        url = detail["url"]
        if mode == "copy" and self.legacy is not None:
            if self.legacy.has_live(url):
                imgs = self.legacy.images_for(url)
                detail["photo_base64"] = imgs["photo"]
                detail["images_base64"] = imgs["images"]
                detail["graphics_base64"] = imgs["graphics"]
                with self._lock:
                    self.stats["images_copied"] += 1
                return
            if self.images_missing != "fetch":
                return
        for key, field in (("photo", "photo_base64"), ("images", "images_base64"), ("graphics", "graphics_base64")):
            src = detail["image_urls"].get(key)
            if src:
                detail[field] = self._download_image_base64(src, url)

    def _download_image_base64(self, img_url, base_url=BASE_URL):
        """画像URLからbase64文字列を返す (失敗は空文字。ブロック検知は伝播)。"""
        try:
            status, content = self._http(normalize_url(img_url, base_url), cacheable=False)
        except OfflineMiss:
            return ""
        if status == 200 and content and len(content) < 12 * 1024 * 1024:
            with self._lock:
                self.stats["images_fetched"] += 1
            return base64.b64encode(content).decode('utf-8')
        with self._lock:
            self.stats["images_failed"] += 1
        return ""

    # ------------------------------------------------------------------
    # DB操作
    # ------------------------------------------------------------------

    def is_cached(self, class_url):
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT 1 FROM CLASS WHERE source_url = ?", (class_url,))
            return cur.fetchone() is not None
        finally:
            conn.close()

    def get_cached_detail(self, class_url):
        """DBキャッシュからdetail dictを復元"""
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT * FROM CLASS WHERE source_url = ?", (class_url,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in cur.description]
            data = dict(zip(cols, row))
            sections_raw = data.get("sections", "")
            if sections_raw:
                try:
                    data["sections_dict"] = json.loads(sections_raw)
                except json.JSONDecodeError:
                    data["sections_dict"] = {}
            else:
                data["sections_dict"] = {}
            cur2 = conn.execute("SELECT * FROM SHIPS WHERE source_url = ?", (class_url,))
            ship_rows = cur2.fetchall()
            ship_cols = [desc[0] for desc in cur2.description]
            data["ships"] = [dict(zip(ship_cols, r)) for r in ship_rows]
            return data
        finally:
            conn.close()

    def save_to_db(self, detail, country):
        """detailをCLASS, SHIPS (, SHIP_GROUPS) に保存する。同じ source_url は置き換える (再実行しても重複しない)。"""
        now = datetime.now(timezone.utc).isoformat()
        cols = detail.get("spec_cols") or specs_to_columns(detail.get("specs", {}))
        sections = detail.get("sections", {})
        lower_sections = {k.lower(): v for k, v in sections.items()}
        url = detail.get("url", "")
        extra_specs = detail.get("extra_specs") or {}
        class_vals = {
            "Photo": detail.get("photo_base64", ""),
            "CLASS_NAME": detail.get("class_name", ""),
            "TYPE": detail.get("ship_type", ""),
            "COUNTRY": country,
            "Displacement_normal": cols.get("Displacement_normal", ""),
            "Displacement_full": cols.get("Displacement_full", ""),
            "Length": cols.get("Length", ""),
            "Breadth": cols.get("Breadth", ""),
            "Draught": cols.get("Draught", ""),
            "No_of_shafts": cols.get("No_of_shafts", ""),
            "Machinery": cols.get("Machinery", ""),
            "Power": cols.get("Power", ""),
            "speed": cols.get("speed", ""),
            "Fuel": cols.get("Fuel", ""),
            "Endurance": cols.get("Endurance", ""),
            "Armour": cols.get("Armour", ""),
            "Armament": cols.get("Armament", ""),
            "Electronic_equipment": cols.get("Electronic_equipment", ""),
            "Complement": cols.get("Complement", ""),
            "images": detail.get("images_base64", ""),
            "Graphics": detail.get("graphics_base64", ""),
            # 後方互換: 個別カラムにも入れる
            "Project_history": lower_sections.get("project history", ""),
            "Modernizations": lower_sections.get("modernizations", ""),
            "Naval_service": lower_sections.get("naval service", ""),
            "sections": json.dumps(sections, ensure_ascii=False),
            "source_url": url,
            "fetched_at": now,
            # 新規列 (旧スキーマの DB には書かれない)
            "Displacement_standard": cols.get("Displacement_standard", ""),
            "WHEN_DATE": detail.get("when_date", ""),
            "HEADING": detail.get("heading", ""),
            "extra_specs": json.dumps(extra_specs, ensure_ascii=False) if extra_specs else "",
            "ships_kind": detail.get("ships_kind", ""),
            "source_site": detail.get("source_site", "live"),
            "parser_version": detail.get("parser_version", PARSER_VERSION),
        }
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            have = set(self._columns(conn, "CLASS"))
            vals = {k: v for k, v in class_vals.items() if k in have}
            conn.execute("DELETE FROM CLASS WHERE source_url = ?", (url,))
            conn.execute("DELETE FROM SHIPS WHERE source_url = ?", (url,))
            has_groups = self._has_table(conn, "SHIP_GROUPS")
            if has_groups:
                conn.execute("DELETE FROM SHIP_GROUPS WHERE source_url = ?", (url,))
            conn.execute(
                f"INSERT INTO CLASS ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                list(vals.values()),
            )
            ship_have = set(self._columns(conn, "SHIPS"))
            for seq, ship in enumerate(detail.get("ships", [])):
                fate_raw = ship.get("Fate", "")
                fate_why, fate_when = split_fate(fate_raw)
                row = {
                    "NAME": ship.get("NAME", ""),
                    "CLASS": detail.get("class_name", ""),
                    "COUNTRY": country,
                    "NO": ship.get("NO", ""),
                    "Yard_No": ship.get("Yard_No", ""),
                    "Builder": ship.get("Builder", ""),
                    "Laid_down": ship.get("Laid_down", ""),
                    "Launched": ship.get("Launched", ""),
                    "Fate_WHY": fate_why,
                    "Fate_WHEN": fate_when,
                    "TYPE": detail.get("ship_type", ""),
                    "WHEN_DATE": detail.get("when_date", ""),
                    "source_url": url,
                    "Comp": ship.get("Comp", ""),
                    "Fate": fate_raw,
                    "Extra": json.dumps(ship["_extra"], ensure_ascii=False) if ship.get("_extra") else "",
                    "seq": seq,
                }
                row = {k: v for k, v in row.items() if k in ship_have}
                conn.execute(
                    f"INSERT INTO SHIPS ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                    list(row.values()),
                )
            if has_groups:
                for seq, g in enumerate(detail.get("ship_groups", [])):
                    heads = list(g.keys())
                    norm = {_norm_label(h): h for h in heads}
                    date_label = next((h for h in heads if _norm_label(h) in ("completed", "commissioned", "comp", "comm")), "")
                    used = {norm.get(k) for k in ("names", "builders", "losses", "transfers", "discarding")} | {date_label}
                    extra = {h: g[h] for h in heads if h not in used and g[h]}
                    conn.execute(
                        """INSERT INTO SHIP_GROUPS (COUNTRY, CLASS, source_url, seq, Names, Builders, Completed, Date_label,
                                                    Losses, Transfers, Discarding, Extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (country, detail.get("class_name", ""), url, seq,
                         g.get(norm.get("names", ""), ""), g.get(norm.get("builders", ""), ""),
                         g.get(date_label, ""), date_label,
                         g.get(norm.get("losses", ""), ""), g.get(norm.get("transfers", ""), ""),
                         g.get(norm.get("discarding", ""), ""),
                         json.dumps(extra, ensure_ascii=False) if extra else ""),
                    )
            if self._has_table(conn, "FETCH_ERRORS"):
                conn.execute("DELETE FROM FETCH_ERRORS WHERE url = ?", (url,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # バッチ取得
    # ------------------------------------------------------------------

    def _fetch_one(self, item, country_name):
        """ワーカー: 取得+パース (DB には触らない)。"""
        if self._cancel:
            return "cancel", item, None
        try:
            return "ok", item, self.fetch_class_detail(item["url"], country_name)
        except StopFetch:
            raise
        except FetchError as e:
            return "err", item, e
        except Exception as e:  # パース不具合などは 1 件の失敗として記録し、継続する
            return "err", item, e

    def process_classes(self, items, country_name, on_progress=None, progress_every=0):
        """艦級リストを (未取得のものだけ) 取得して保存する。done/total を on_progress で通知。"""
        total = len(items)
        done = 0
        todo = []
        for item in items:
            if self.is_cached(item["url"]):
                done += 1
                self.stats["classes_skipped"] += 1
            else:
                todo.append(item)
        if on_progress and done:
            on_progress(done, total)
        window = self.max_workers * 4
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for start in range(0, len(todo), window):
                if self._cancel:
                    return
                futures = [pool.submit(self._fetch_one, item, country_name) for item in todo[start:start + window]]
                try:
                    for future in as_completed(futures):
                        kind, item, payload = future.result()
                        if kind == "ok":
                            try:
                                self.save_to_db(payload, country_name)
                                self.stats["classes_ok"] += 1
                            except Exception as e:
                                self.stats["classes_err"] += 1
                                self._record_error(item["url"], "save", 0, repr(e))
                                log(f"保存失敗 {item['url']}: {e!r}")
                        elif kind == "err":
                            self.stats["classes_err"] += 1
                            status = getattr(payload, "status", 0)
                            self._record_error(item["url"], "class", status, payload)
                            log(f"取得失敗 {item['url']}: {payload}")
                        done += 1
                        if on_progress:
                            on_progress(done, total)
                except BaseException:
                    for f in futures:
                        f.cancel()
                    raise

    def fetch_country_all(self, country_url, country_name, on_progress=None):
        """国全体のデータを取得してDBに保存"""
        self._cancel = False
        items = self.discover_country(country_url)
        self.process_classes(items, country_name, on_progress)

    def fetch_all_countries(self, on_progress=None, on_country_start=None, refresh_country_index=False, only=None):
        """全国のデータを一括取得してDBに保存。

        1) 全国の艦種ページ・艦級リンクを列挙 (総数を確定) → 2) 艦級ページを国ごとに取得 (取得済みはスキップ)。
        on_progress(done, total)       -- 国内の艦級単位の進捗
        on_country_start(name, idx, n) -- 国の処理開始時
        only=国の Short 名のリスト (指定時はその国だけ)
        """
        self._cancel = False
        conn = self._get_conn()
        try:
            count = conn.execute("SELECT COUNT(*) FROM COUNTRIES").fetchone()[0]
        finally:
            conn.close()
        if refresh_country_index or count == 0:
            self.save_countries(self.fetch_countries(), replace=True)
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT Short, Long, URL FROM COUNTRIES ORDER BY Long").fetchall()
        finally:
            conn.close()
        if only:
            wanted = set(only)
            rows = [r for r in rows if r[0] in wanted or r[1] in wanted]

        self.set_state("status", "running")
        self.set_state("started_at", datetime.now().isoformat(timespec="seconds"))
        plan = []
        log(f"[1/2] 艦種・艦級リンクの列挙 ({len(rows)} 国)")
        for i, (short, long_name, url) in enumerate(rows):
            if self._cancel:
                self.set_state("status", "interrupted")
                return False
            try:
                items = self.discover_country(normalize_url(url))
            except FetchError as e:
                log(f"  国ページ取得失敗 {url}: {e}")
                self._record_error(url, "country_page", e.status, e)
                items = []
            plan.append((short, long_name, items))
            if (i + 1) % 20 == 0 or i + 1 == len(rows):
                log(f"  列挙 {i + 1}/{len(rows)} 国 (艦級 {sum(len(p[2]) for p in plan)} 件)")
        total = sum(len(p[2]) for p in plan)
        self.set_state("total_expected", total)
        log(f"[2/2] 艦級ページの取得: 全 {total} 件")
        base_done = 0
        for idx, (short, long_name, items) in enumerate(plan):
            if self._cancel:
                self.set_state("status", "interrupted")
                return False
            if on_country_start:
                on_country_start(long_name, idx, len(plan))

            def prog(done, tot, _base=base_done):
                if on_progress:
                    on_progress(done, tot)
                g = _base + done
                if done % 25 == 0 or done == tot:
                    self.log_progress(g, total)
            self.process_classes(items, short, prog)
            base_done += len(items)
        ok = not self._cancel
        self.set_state("status", "complete" if ok else "interrupted")
        self.set_state("finished_at", datetime.now().isoformat(timespec="seconds"))
        return ok

    def log_progress(self, done, total):
        el = time.time() - self._t0
        s = self.stats
        got = s["classes_ok"] + s["classes_err"]
        eta = ""
        if got > 0 and total > done:
            remain = (total - done) * (el / got)
            eta = f" 残り約 {remain / 3600:.1f}h"
        log(f"進捗 {done}/{total} ({100.0 * done / max(1, total):.1f}%) ok={s['classes_ok']} err={s['classes_err']} "
            f"skip={s['classes_skipped']} 通信={s['net_requests']} キャッシュ={s['cache_hits']} "
            f"警告={s.get('parse_warnings', 0)} ミラー縮退={s['mirror_fallbacks']} "
            f"画像(コピー={s['images_copied']} 取得={s['images_fetched']} 失敗={s['images_failed']}){eta}")

    def finalize_db(self):
        """WAL を DB 本体に反映して通常モードに戻す (配布・入れ替え前)。"""
        conn = self._get_conn()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
        finally:
            conn.close()


def db_status(db_path):
    """進捗確認用: DB の件数・状態を dict で返す (読み取り専用)。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = {}
        for t in ("COUNTRIES", "CLASS", "SHIPS", "SHIP_GROUPS", "FETCH_ERRORS"):
            try:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.DatabaseError:
                out[t] = None
        try:
            out["state"] = dict(conn.execute("SELECT key, value FROM FETCH_STATE").fetchall())
        except sqlite3.DatabaseError:
            out["state"] = {}
        return out
    finally:
        conn.close()
# ---------------------------------------------------------------------------
# インタラクティブ国選択 TUI
# ---------------------------------------------------------------------------

def _select_countries_tui(country_list: list) -> list:
    """curses を使った国選択TUI。選択されたShort名リストを元の順序で返す。
    ESC / q で中断した場合は空リストを返す。
    """
    import curses

    selected: set = set()
    query: list = []
    cursor = [0]
    scroll = [0]

    def _filtered():
        q = "".join(query).lower()
        return [c for c in country_list if q in c.lower()] if q else list(country_list)

    def _draw(stdscr):
        curses.curs_set(0)
        stdscr.keypad(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)  # カーソル行
            curses.init_pair(2, curses.COLOR_GREEN,  -1)                  # チェック済み
            curses.init_pair(3, curses.COLOR_CYAN,   -1)                  # 検索バー

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            visible = _filtered()
            list_h = h - 3  # ヘッダー1行 + 検索バー1行 + フッター1行

            # カーソル範囲クランプ
            if visible:
                cursor[0] = max(0, min(cursor[0], len(visible) - 1))
            else:
                cursor[0] = 0
            # スクロール追従
            if cursor[0] < scroll[0]:
                scroll[0] = cursor[0]
            if cursor[0] >= scroll[0] + list_h:
                scroll[0] = cursor[0] - list_h + 1

            # ── ヘッダー ──────────────────────────────────────────────
            hdr = " [↑↓/ws] 移動  [Space/a] 追加  [d] 削除  [Enter] 確定  [ESC/q] 中断  [Del] フィルタ消去"
            stdscr.addstr(0, 0, hdr[:w - 1])

            # ── 検索バー ─────────────────────────────────────────────
            q_str = "".join(query)
            bar = f" 検索: {q_str}|  ({len(visible)}/{len(country_list)})  選択: {len(selected)} 件"
            attr = curses.color_pair(3) if curses.has_colors() else curses.A_BOLD
            stdscr.addstr(1, 0, bar[:w - 1], attr)

            # ── 国リスト ─────────────────────────────────────────────
            for i, name in enumerate(visible[scroll[0]: scroll[0] + list_h]):
                abs_i = scroll[0] + i
                row = i + 2
                mark = "[x]" if name in selected else "[ ]"
                line = f" {mark} {name}"
                if abs_i == cursor[0]:
                    stdscr.addstr(row, 0, line[:w - 1], curses.color_pair(1) if curses.has_colors() else curses.A_REVERSE)
                elif name in selected and curses.has_colors():
                    stdscr.addstr(row, 0, line[:w - 1], curses.color_pair(2))
                else:
                    stdscr.addstr(row, 0, line[:w - 1])

            # ── フッター ─────────────────────────────────────────────
            foot = " " + "  ".join(f"[{n}]" for n in list(selected)[:8])
            if len(selected) > 8:
                foot += f" …他 {len(selected) - 8} 件"
            stdscr.addstr(h - 1, 0, foot[:w - 1])

            stdscr.refresh()

            # get_wch() は通常文字を str、特殊キーを int で返すので
            # 型で分岐することで ord(' ') とキーコードの混在問題を回避する
            try:
                key = stdscr.get_wch()
            except curses.error:
                continue

            if isinstance(key, str):
                # ── 通常文字 ──────────────────────────────────────────
                if key == ' ' or key == 'a':
                    if visible:
                        name = visible[cursor[0]]
                        if name not in selected:
                            selected.add(name)
                        cursor[0] = min(cursor[0] + 1, len(visible) - 1)
                elif key == 'd':
                    if visible:
                        name = visible[cursor[0]]
                        selected.discard(name)
                        cursor[0] = min(cursor[0] + 1, len(visible) - 1)
                elif key == 'w':
                    cursor[0] = max(0, cursor[0] - 1)
                elif key == 's':
                    cursor[0] = min(max(0, len(visible) - 1), cursor[0] + 1)
                elif key in ('\n', '\r'):
                    return
                elif key == '\x1b':          # ESC
                    selected.clear()
                    return
                elif key == 'q':
                    selected.clear()
                    return
                elif key in ('\x7f', '\b'):  # Backspace
                    if query:
                        query.pop()
                        cursor[0] = 0
                        scroll[0] = 0
                elif key.isprintable():
                    query.append(key)
                    cursor[0] = 0
                    scroll[0] = 0
            else:
                # ── 特殊キー (int) ────────────────────────────────────
                if key == curses.KEY_UP:
                    cursor[0] = max(0, cursor[0] - 1)
                elif key == curses.KEY_DOWN:
                    cursor[0] = min(max(0, len(visible) - 1), cursor[0] + 1)
                elif key == curses.KEY_PPAGE:
                    cursor[0] = max(0, cursor[0] - list_h)
                elif key == curses.KEY_NPAGE:
                    cursor[0] = min(max(0, len(visible) - 1), cursor[0] + list_h)
                elif key in (curses.KEY_ENTER,):
                    return
                elif key in (curses.KEY_BACKSPACE,):
                    if query:
                        query.pop()
                        cursor[0] = 0
                        scroll[0] = 0
                elif key == curses.KEY_DC:
                    query.clear()
                    cursor[0] = 0
                    scroll[0] = 0
                elif key == curses.KEY_RESIZE:
                    pass  # 再描画ループに任せる

    curses.wrapper(_draw)
    # 元リストの順序を保持して返す
    return [c for c in country_list if c in selected]




# ---------------------------------------------------------------------------
# CLI エントリポイント
# ---------------------------------------------------------------------------

def _country_dir_map(db_path):
    """COUNTRIES の URL ディレクトリ (/ships/<dir>/) → Short 名。"""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT Short, URL FROM COUNTRIES").fetchall()
    finally:
        conn.close()
    out = {}
    for short, url in rows:
        parts = urlparse(normalize_url(url)).path.strip("/").split("/")
        if len(parts) >= 2:
            out[parts[1]] = short
    return out


def _parse_images_option(value, db_path):
    """--images の値 → (mode, images_from)。auto / copy-from:PATH / fetch / skip。"""
    if value == "skip":
        return "skip", None
    if value == "fetch":
        return "fetch", None
    if value == "auto":
        if DEFAULT_DB_PATH.exists() and DEFAULT_DB_PATH.resolve() != Path(db_path).resolve():
            return "copy", DEFAULT_DB_PATH
        return "fetch", None
    if value.startswith("copy-from:"):
        path = Path(value.split(":", 1)[1]).expanduser()
        if not path.exists():
            raise SystemExit(f"エラー: --images の旧 DB が見つかりません: {path}")
        if path.resolve() == Path(db_path).resolve():
            raise SystemExit("エラー: --images copy-from の旧 DB と --db が同じファイルです")
        return "copy", path
    raise SystemExit(f"エラー: --images は skip / fetch / auto / copy-from:PATH のいずれかです: {value}")


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Navypedia (www.navypedia.org) データ収集ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "使用例:\n"
            "  # 新規DBに全国家を収集 (ホスト 0.9 req/s、画像は旧 DB からコピー + 新規分のみ取得、中断・再開可)\n"
            "  python fetcher.py --db /path/NEW.db all\n\n"
            "  # 特定国のみ収集\n"
            "  python fetcher.py --db /path/NEW.db country Japan\n\n"
            "  # URL リストの艦級だけ取得 (1 行 1 URL)\n"
            "  python fetcher.py --db /path/NEW.db classes --file urls.txt\n\n"
            "  # 取得済み HTML キャッシュだけで DB を作り直す (ネットワークなし)\n"
            "  python fetcher.py --db /path/NEW.db --offline --cache-dir /path/NEW_htmlcache all\n\n"
            "  # 進捗確認 (読み取り専用)\n"
            "  python fetcher.py --db /path/NEW.db status\n"
        ),
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), metavar="PATH",
                        help=f"SQLite DB パス (default: {DEFAULT_DB_PATH}。正本を誤って書かないよう --allow-canonical-db が必要)")
    parser.add_argument("--workers", type=int, default=2, metavar="N", help="並列ワーカー数 (default: 2。通信はレート制限で直列化される)")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, metavar="R",
                        help=f"ホストごとの最大リクエスト毎秒 (default: {DEFAULT_RATE}。1 を超える値は拒否)")
    parser.add_argument("--user-agent", default=USER_AGENT, help="User-Agent (default: 正直な識別子)")
    parser.add_argument("--cache-dir", default=None, metavar="DIR",
                        help="取得 HTML のキャッシュ先 (default: <DB名>_htmlcache/。再パースをオフラインで可能にする)")
    parser.add_argument("--no-cache", action="store_true", help="HTML をディスクに保存しない")
    parser.add_argument("--offline", action="store_true", help="ネットワークに出ない (キャッシュに無い URL は失敗扱い)")
    parser.add_argument("--images", default="auto", metavar="MODE",
                        help="画像列: auto (既定。旧 DB からコピー、旧 DB に無い艦級だけ取得) / copy-from:PATH / fetch / skip")
    parser.add_argument("--images-missing", choices=["fetch", "skip"], default="fetch",
                        help="copy 時に旧 DB に無い艦級の画像を取得するか (default: fetch)")
    parser.add_argument("--max-requests", type=int, default=None, metavar="N", help="通信回数の上限 (到達で停止。試験用)")
    parser.add_argument("--no-robots-check", action="store_true", help="起動時の robots.txt 確認を省略")
    parser.add_argument("--no-mirror-fallback", action="store_true", help="現行が 5xx のときのミラー縮退を使わない")
    parser.add_argument("--migrate", action="store_true", help="既存 DB にも新しい列・表を追加する (既定は新規 DB のみ)")
    parser.add_argument("--allow-canonical-db", action="store_true", help="正本 nsdb/NSDB.db への書き込みを許可する")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_all = sub.add_parser("all", help="全国家のデータを取得 (中断しても再実行で続きから)")
    p_all.add_argument("--no-refresh-index", action="store_true", help="国家インデックスを再取得せずCOUNTRIESテーブルを使う")
    p_all.add_argument("--only", nargs="+", metavar="NAME", help="指定した国 (Short 名) だけ")

    p_country = sub.add_parser("country", help="指定した国のデータを取得")
    p_country.add_argument("name", help="国の短縮名 (例: Japan, United-Kingdom)")
    p_country.add_argument("--url", metavar="URL", help="国インデックスURL (省略時は COUNTRIES テーブルから検索)")

    p_cl = sub.add_parser("cl", help="複数国をまとめて取得 (引数省略でインタラクティブ選択)")
    p_cl.add_argument("names", nargs="*", metavar="NAME", help="国の短縮名リスト (省略するとTUI選択画面を起動)")

    sub.add_parser("index", help="国家インデックス (COUNTRIES テーブル) のみ取得・更新")

    p_refetch = sub.add_parser("refetch", help="指定したクラスURLを再取得（上書き）")
    p_refetch.add_argument("url", help="クラスページのURL")
    p_refetch.add_argument("--country", default="", help="国コード（省略時は既存レコード/COUNTRIES から推定）")

    p_classes = sub.add_parser("classes", help="URL リストの艦級だけ取得する (取得済みはスキップ。--force で上書き)")
    p_classes.add_argument("urls", nargs="*", metavar="URL")
    p_classes.add_argument("--file", metavar="FILE", help="1 行 1 URL のファイル")
    p_classes.add_argument("--force", action="store_true", help="取得済みでも取り直す")

    sub.add_parser("retry-errors", help="FETCH_ERRORS に残った艦級を再取得する")
    sub.add_parser("status", help="DB の件数・状態を表示 (読み取り専用)")
    return parser


def main(argv=None):
    import signal

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "status":
        st = db_status(args.db)
        print(json.dumps(st, ensure_ascii=False, indent=2))
        return 0

    if args.rate > 1.0 or args.rate <= 0:
        parser.error("--rate は 0 より大きく 1 以下 (ホストごと 1 req/s 以下)")
    db_path = Path(args.db).expanduser()
    if db_path.exists() and DEFAULT_DB_PATH.exists() and db_path.resolve() == DEFAULT_DB_PATH.resolve() \
            and not args.allow_canonical_db:
        print(f"エラー: {db_path} は正本 DB です。新しい DB を --db で指定してください (どうしても必要なら --allow-canonical-db)。")
        return 2
    img_mode, img_from = _parse_images_option(args.images, db_path)
    cache_dir = None
    if not args.no_cache:
        cache_dir = Path(args.cache_dir).expanduser() if args.cache_dir else db_path.with_name(db_path.stem + "_htmlcache")

    start = time.time()

    def elapsed():
        m, s = divmod(int(time.time() - start), 60)
        h, m = divmod(m, 60)
        return f"{h}時間{m}分{s}秒" if h else f"{m}分{s}秒"

    try:
        lock = RunLock(db_path).acquire()
    except RuntimeError as e:
        print(f"エラー: {e}")
        return 3

    fetcher = None
    code = 0
    try:
        fetcher = NavypediaFetcher(
            db_path=db_path, max_workers=args.workers, rate=args.rate, user_agent=args.user_agent,
            cache_dir=cache_dir, offline=args.offline, images=img_mode, images_from=img_from,
            images_missing=args.images_missing, migrate=True if args.migrate else None,
            mirror_fallback=not args.no_mirror_fallback, max_requests=args.max_requests,
        )

        def _on_term(signum, frame):
            log(f"シグナル {signum} を受信: 現在の処理を終えて停止します (再実行で続きから)")
            fetcher.cancel()
        signal.signal(signal.SIGTERM, _on_term)

        log(f"開始 cmd={args.cmd} db={db_path} rate={args.rate} workers={args.workers} images={img_mode}"
            f"{' from=' + str(img_from) if img_from else ''} cache={cache_dir} offline={args.offline}")
        if not args.no_robots_check and not args.offline and args.cmd not in ("index",):
            fetcher.check_robots()

        def resolve_country_url(name):
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute("SELECT URL FROM COUNTRIES WHERE Short = ? OR Long = ?", (name, name)).fetchone()
            finally:
                conn.close()
            return normalize_url(row[0]) if row else None

        def ensure_countries():
            conn = sqlite3.connect(str(db_path))
            try:
                n = conn.execute("SELECT COUNT(*) FROM COUNTRIES").fetchone()[0]
            finally:
                conn.close()
            if n == 0:
                fetcher.save_countries(fetcher.fetch_countries(), replace=True)

        def on_progress(done, total):
            if total >= 50 and (done == total or done % 50 == 0):
                log(f"  {done}/{total}")

        if args.cmd == "index":
            log("国家インデックスを取得中 …")
            countries = fetcher.fetch_countries()
            fetcher.save_countries(countries, replace=True)
            log(f"  {len(countries)} カ国を COUNTRIES に保存しました ({elapsed()})")

        elif args.cmd == "all":
            def on_country_start(name, idx, total):
                log(f"[{idx + 1}/{total}] {name}")
            ok = fetcher.fetch_all_countries(
                on_progress=on_progress, on_country_start=on_country_start,
                refresh_country_index=not args.no_refresh_index, only=args.only,
            )
            s = fetcher.stats
            if ok:
                fetcher.finalize_db()
                log(f"=== ALL DONE ({elapsed()}) ok={s['classes_ok']} err={s['classes_err']} skip={s['classes_skipped']} "
                    f"通信={s['net_requests']} ===")
            else:
                log(f"=== INTERRUPTED ({elapsed()}) 再実行で続きから取得します ===")
                code = 1

        elif args.cmd == "country":
            ensure_countries()
            country_url = normalize_url(args.url) if args.url else resolve_country_url(args.name)
            if not country_url:
                print(f"エラー: '{args.name}' が COUNTRIES テーブルに見つかりません。")
                return 1
            log(f"{args.name} を取得中 ({country_url})")
            fetcher.fetch_country_all(country_url, args.name, on_progress=on_progress)
            log(f"完了 ({elapsed()})")

        elif args.cmd == "cl":
            ensure_countries()
            chosen = list(args.names)
            if not chosen:
                conn = sqlite3.connect(str(db_path))
                try:
                    rows = conn.execute("SELECT Short FROM COUNTRIES ORDER BY Short").fetchall()
                finally:
                    conn.close()
                chosen = _select_countries_tui([r[0] for r in rows])
                if not chosen:
                    print("選択なし。終了します。")
                    return 0
            not_found = [n for n in chosen if not resolve_country_url(n)]
            if not_found:
                print("エラー: 以下の国名が COUNTRIES テーブルに見つかりません:\n" + "\n".join(f"  - {n}" for n in not_found))
                return 1
            for i, name in enumerate(chosen):
                country_url = resolve_country_url(name)
                log(f"[{i + 1}/{len(chosen)}] {name} ({country_url})")
                fetcher.fetch_country_all(country_url, name, on_progress=on_progress)
                log(f"  完了 ({elapsed()})")
            log(f"=== {len(chosen)} カ国完了 ({elapsed()}) ===")

        elif args.cmd in ("refetch", "classes", "retry-errors"):
            ensure_countries()
            dir_map = _country_dir_map(db_path)

            def country_for(url):
                parts = urlparse(normalize_url(url)).path.strip("/").split("/")
                return dir_map.get(parts[1], "") if len(parts) >= 2 else ""

            if args.cmd == "refetch":
                class_url = normalize_url(args.url)
                country = args.country
                if not country:
                    conn = sqlite3.connect(str(db_path))
                    try:
                        row = conn.execute("SELECT COUNTRY FROM CLASS WHERE source_url = ?", (class_url,)).fetchone()
                    finally:
                        conn.close()
                    country = (row[0] if row else "") or country_for(class_url)
                log(f"再取得中: {class_url} (country={country})")
                detail = fetcher.fetch_class_detail(class_url, country)
                fetcher.save_to_db(detail, country)  # 取得に成功してから置き換える (失敗時に既存行を失わない)
                log(f"完了 ({elapsed()}) class_name={detail.get('class_name')!r} ship_type={detail.get('ship_type')!r} "
                    f"ships={len(detail.get('ships', []))} groups={len(detail.get('ship_groups', []))}")
            else:
                if args.cmd == "classes":
                    urls = list(args.urls)
                    if args.file:
                        urls += [ln.strip() for ln in Path(args.file).read_text().splitlines() if ln.strip() and not ln.startswith("#")]
                else:
                    conn = sqlite3.connect(str(db_path))
                    try:
                        urls = [r[0] for r in conn.execute("SELECT url FROM FETCH_ERRORS WHERE kind IN ('class','save')")]
                    finally:
                        conn.close()
                by_country, seen_keys = {}, set()
                for u in urls:
                    nu = normalize_url(u)
                    if canon_key(nu) in seen_keys:
                        continue
                    seen_keys.add(canon_key(nu))
                    if args.cmd == "classes" and args.force:
                        conn = sqlite3.connect(str(db_path))
                        try:
                            conn.execute("DELETE FROM CLASS WHERE source_url = ?", (nu,))
                            conn.execute("DELETE FROM SHIPS WHERE source_url = ?", (nu,))
                            conn.commit()
                        finally:
                            conn.close()
                    by_country.setdefault(country_for(nu), []).append({"name": nu, "url": nu})
                log(f"対象 {sum(len(v) for v in by_country.values())} 件")
                for country, items in by_country.items():
                    fetcher.process_classes(items, country, on_progress=on_progress)
                s = fetcher.stats
                log(f"完了 ({elapsed()}) ok={s['classes_ok']} err={s['classes_err']} skip={s['classes_skipped']} 通信={s['net_requests']}")

    except StopFetch as e:
        log(f"=== STOPPED: {e} === (回避せず停止。再実行で続きから)")
        if fetcher:
            fetcher.set_state("status", "stopped")
            fetcher.set_state("stop_reason", str(e))
        code = 1
    except KeyboardInterrupt:
        log("=== INTERRUPTED: 中断されました (再実行で続きから) ===")
        if fetcher:
            fetcher.set_state("status", "interrupted")
        code = 1
    finally:
        lock.release()
    return code


if __name__ == "__main__":
    sys.exit(main())
