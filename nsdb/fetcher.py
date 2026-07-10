import base64
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://myownonpmirror.com"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "NSDB.db"
DEFAULT_CSV_PATH = Path(__file__).resolve().with_name("SHIP_TYPE_LIST.csv")

EXCLUSION_LIST = {
    '/',
    'https://myownonpmirror.com/privacy.html',
    'https://www.paypal.com/paypalme/igogin',
    'https://myownonpmirror.com/ships_index.html',
}

# blueテーブルのth → DBカラム名マッピング
SPEC_TO_COLUMN = {
    "Displacement standard, t": "Displacement_normal",
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

# cwdtableのth → DBカラム名マッピング
SHIP_HEADER_TO_COLUMN = {
    "Name": "NAME",
    "No": "NO",
    "Yard No": "Yard_No",
    "Builder": "Builder",
    "Laid down": "Laid_down",
    "Launched": "Launched",
    "Comp": "Comp",
    "Fate": "Fate",
}

# 画像系h2セクション
IMAGE_SECTIONS = {"standard scale images", "graphics"}
# テキスト系h2セクション（これ以外でも動的に拾う）
TEXT_SECTIONS = {"project history", "ship protection", "modernizations", "naval service"}


_MIRROR_NETLOC = urlparse(BASE_URL).netloc  # "myownonpmirror.com"

# reds2 / heading テキストに含まれる合法な艦種キーワード
# これ以外の全小文字トークン (国名・海軍名等) は TYPE に混入させない
_SHIP_TYPE_KEYWORDS = frozenset({
    'battleship', 'battleships', 'battlecruiser', 'battlecruisers',
    'cruiser', 'cruisers', 'destroyer', 'destroyers',
    'frigate', 'frigates', 'corvette', 'corvettes',
    'sloop', 'sloops', 'submarine', 'submarines',
    'gunboat', 'gunboats', 'monitor', 'monitors',
    'minelayer', 'minelayers', 'minesweeper', 'minesweepers',
    'escort', 'escorts', 'carrier', 'carriers',
    'ironclad', 'ironclads', 'torpedo', 'patrol', 'mine',
    'armoured', 'armored', 'protected', 'unarmoured', 'unarmored',
    'missile', 'nuclear', 'motor', 'steam', 'sailing', 'screw', 'paddle',
    'river', 'riverine', 'coastal', 'landing', 'amphibious', 'attack',
    'anti', 'guided', 'light', 'heavy', 'fast',
    'boat', 'boats', 'ship', 'ships', 'craft', 'vessel', 'vessels',
    'aircraft', 'helicopter', 'type',
    'small', 'large', 'medium', 'fleet', 'sea', 'ocean',
})

# セル値が純数値スペース区切りのとき最大値に正規化するカラム群
_NUMERIC_SPEC_COLS = frozenset({
    'Displacement_normal', 'Displacement_full',
    'Length', 'Breadth', 'Draught', 'No_of_shafts', 'Power',
})


def _clean_numeric_spec(col: str, val: str) -> str:
    """spec 値の数値正規化。桁区切りスペースを結合し、複数値の場合は最大値を採用。
    """
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
    parsed = urlparse(urljoin(base_url, href or ""))
    netloc = parsed.netloc.replace("www.", "")
    if "navypedia.org" in netloc:
        netloc = _MIRROR_NETLOC
    path = re.sub(r"\.htm$", ".html", parsed.path)
    return urlunparse(("https", netloc, path, "", parsed.query, parsed.fragment))


def clean_text(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def title_case_class_name(text):
    text = clean_text(text).strip("'\"")
    words = []
    for word in text.split():
        if word.isupper() or re.fullmatch(r"[A-Z0-9][A-Z0-9/-]*", word):
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def country_short_name(text):
    cleaned = re.sub(r"[()/,]+", " ", clean_text(text))
    return "-".join(word[:1].upper() + word[1:].lower() for word in cleaned.split())


def split_class_heading(text):
    text = re.sub(r"^\d+\.\s*", "", clean_text(text))
    # " - X Navy (Country)" のナビー文脈を除去
    text = re.split(r'\s+-\s+', text)[0].strip()
    when = ""
    when_match = re.search(r"\(([^)]*\d{4}[^)]*)\)\s*$", text)
    if when_match:
        when = f"({when_match.group(1)})"
        text = text[:when_match.start()].strip()

    title_match = re.search(r"\(([^()]*?)\s+class\s*,?\s*[^()]*\d{4}[^()]*\)", text, re.IGNORECASE)
    if title_match:
        class_source = title_match.group(1)
    else:
        quoted_match = re.match(r"(['\"][^'\"]+['\"])\s+(.+)$", text)
        if quoted_match:
            class_source = quoted_match.group(1)
        else:
            class_source = re.sub(r"\btype\b.*$", "", text, flags=re.IGNORECASE).strip()
            class_source = re.split(
                r"\b(?:battleships?|battlecruisers?|cruisers?|destroyers?|frigates?|corvettes?|sloops?|submarines?|"
                r"minelayers?|minesweepers?|monitors?|gunboats?|torpedo-boats?|patrol|escorts?|ship-of-the-line|ships-of-the-line)\b",
                class_source,
                flags=re.IGNORECASE,
            )[0].strip()

    class_name = f"{title_case_class_name(class_source.strip(' ,'))} Class" if class_source else ""
    type_source = text.replace(class_source, "", 1).strip(" ,") if class_source else text
    type_source = re.sub(r"\btype\b", "", type_source, flags=re.IGNORECASE)
    ship_type = "-".join(re.findall(r"[A-Za-z]+", type_source.lower()))
    return class_name, ship_type, when


def _load_ship_types(csv_path=DEFAULT_CSV_PATH):
    ship_types = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                ship_type = line.split(']')[0].strip('[').replace(',', '-')
                ship_types.append(ship_type)
    return ship_types


class NavypediaFetcher:

    def __init__(self, db_path=DEFAULT_DB_PATH, csv_path=DEFAULT_CSV_PATH, max_workers=8):
        self.db_path = Path(db_path)
        self.max_workers = max_workers
        self.ship_types = _load_ship_types(csv_path)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "NECS/1.0 (Naval Encyclopedia Console Service)"
        })
        adapter = requests.adapters.HTTPAdapter(
            max_retries=requests.adapters.Retry(
                total=3, backoff_factor=0.5,
                status_forcelist=[500, 502, 503, 504]
            )
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._cancel = False
        self._ensure_main_schema()
        self._ensure_cache_tables()

    def _ensure_main_schema(self):
        """新規DBに全テーブルを作成する。既存DBは変更しない(CREATE TABLE IF NOT EXISTS)。"""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS COUNTRIES (
                    Short TEXT, Long TEXT, URL TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS CLASS (
                    Photo TEXT, CLASS_NAME TEXT, TYPE TEXT, COUNTRY TEXT,
                    Displacement_normal TEXT, Displacement_full TEXT,
                    Length TEXT, Breadth TEXT, Draught TEXT,
                    No_of_shafts TEXT, Machinery TEXT, Power TEXT,
                    speed TEXT, Fuel TEXT, Endurance TEXT,
                    Armour TEXT, Armament TEXT, Electronic_equipment TEXT,
                    Complement TEXT, images TEXT, Graphics TEXT,
                    Project_history TEXT, Modernizations TEXT, Naval_service TEXT,
                    sections TEXT,
                    source_url TEXT UNIQUE,
                    fetched_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS SHIPS (
                    NAME TEXT, CLASS TEXT, COUNTRY TEXT, NO TEXT,
                    Yard_No TEXT, Builder TEXT, Laid_down TEXT, Launched TEXT,
                    Fate_WHY TEXT, Fate_WHEN TEXT, TYPE TEXT,
                    SID TEXT, WHEN_DATE TEXT, source_url TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS TRANSLATIONS (
                    source_url TEXT NOT NULL,
                    section_name TEXT NOT NULL,
                    translated TEXT,
                    PRIMARY KEY (source_url, section_name)
                )
            """)
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

    def cancel(self):
        self._cancel = True

    def _get(self, url, timeout=30):
        resp = self.session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp

    def _get_soup(self, url):
        resp = self._get(url)
        return BeautifulSoup(resp.content, 'html.parser')

    # ------------------------------------------------------------------
    # 階層取得
    # ------------------------------------------------------------------

    def fetch_countries(self, index_url=f"{BASE_URL}/ships_index.html"):
        """国家一覧ページから国URLを取得する。"""
        soup = self._get_soup(index_url)
        results = []
        seen = set()
        for a_tag in soup.find_all('a', href=True):
            full_url = normalize_url(a_tag['href'], index_url)
            path = urlparse(full_url).path
            if not path.startswith('/ships/') or not re.search(r'_index\.html$', path):
                continue
            name = clean_text(a_tag.get_text(" ", strip=True))
            if not name or full_url in seen:
                continue
            seen.add(full_url)
            results.append({
                "short": country_short_name(name),
                "long": name.replace(" ", "-"),
                "url": full_url,
            })
        return results

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
                "SELECT name, url FROM CACHE_SHIP_TYPES WHERE country_url = ?",
                (country_url,)
            ).fetchall()
            if rows:
                return [{"name": r[0], "url": r[1]} for r in rows]
            return None
        finally:
            conn.close()

    def get_cached_class_urls(self, type_url):
        """DBキャッシュから艦級URLリストを返す。なければNone"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT name, url FROM CACHE_CLASS_URLS WHERE type_url = ?",
                (type_url,)
            ).fetchall()
            if rows:
                return [{"name": r[0], "url": r[1]} for r in rows]
            return None
        finally:
            conn.close()

    def fetch_ship_types(self, country_url):
        """国ページから艦種(カテゴリ)URLリストを返す（キャッシュ優先）"""
        cached = self.get_cached_ship_types(country_url)
        if cached is not None:
            return cached

        soup = self._get_soup(country_url)
        results = []
        seen = set()
        for a_tag in soup.find_all('a', href=True):
            text = clean_text(a_tag.get_text(" ", strip=True))
            full_url = normalize_url(a_tag['href'], country_url)
            if full_url in EXCLUSION_LIST or full_url in seen:
                continue
            if any(st in text for st in self.ship_types):
                seen.add(full_url)
                results.append({"name": text, "url": full_url})

        # キャッシュに保存
        if results:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM CACHE_SHIP_TYPES WHERE country_url = ?", (country_url,))
                conn.executemany(
                    "INSERT INTO CACHE_SHIP_TYPES (country_url, name, url) VALUES (?, ?, ?)",
                    [(country_url, r["name"], r["url"]) for r in results]
                )
                conn.commit()
            finally:
                conn.close()

        return results

    def fetch_class_urls(self, type_url):
        """カテゴリページから艦級URLリストを返す（キャッシュ優先）"""
        cached = self.get_cached_class_urls(type_url)
        if cached is not None:
            return cached

        soup = self._get_soup(type_url)
        results = []
        seen = set()
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            full_url = normalize_url(href, type_url)
            if full_url in EXCLUSION_LIST:
                continue
            path = urlparse(full_url).path
            if path.startswith('/ships/') and not re.search(r'_index\.html$', path) and full_url != normalize_url(type_url):
                if full_url not in seen:
                    seen.add(full_url)
                    parent = a_tag.find_parent("p")
                    name_source = parent if parent else a_tag
                    name = clean_text(name_source.get_text(" ", strip=True))
                    results.append({"name": name, "url": full_url})

        # キャッシュに保存
        if results:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM CACHE_CLASS_URLS WHERE type_url = ?", (type_url,))
                conn.executemany(
                    "INSERT INTO CACHE_CLASS_URLS (type_url, name, url) VALUES (?, ?, ?)",
                    [(type_url, r["name"], r["url"]) for r in results]
                )
                conn.commit()
            finally:
                conn.close()

        return results

    def fetch_class_detail(self, class_url, country=""):
        """艦級ページから全データを1回のアクセスで抽出"""
        soup = self._get_soup(class_url)

        result = {
            "url": class_url,
            "country": country,
            "class_name": "",
            "ship_type": "",
            "when_date": "",
            "photo_base64": "",
            "images_base64": "",
            "graphics_base64": "",
            "specs": {},
            "ships": [],
            "sections": {},
        }

        # --- class_type ---
        reds2 = soup.find('a', class_='reds2')
        if reds2:
            raw = clean_text(reds2.get_text(" ", strip=True)).replace(',', '-')
            self._parse_class_type(raw, result)
        else:
            self._parse_class_heading(soup, result)

        # --- Photo ---
        photo_tag = soup.find(string="Photo")
        if photo_tag:
            img = photo_tag.find_next('img', class_='css-adaptive')
            if img and img.get('src'):
                result["photo_base64"] = self._download_image_base64(img['src'], class_url)

        self._extract_specs(soup, result)
        self._extract_ships(soup, result)

        # --- h2セクション（動的検出） ---
        for h2 in soup.find_all('h2'):
            section_name = h2.get_text(strip=True)
            lower = section_name.lower()

            if lower == "photo" or lower == "ships" or lower == "technical data":
                continue  # 既に個別処理済み

            if any(img_key in lower for img_key in IMAGE_SECTIONS):
                img = h2.find_next('img')
                if img and img.get('src'):
                    b64 = self._download_image_base64(img['src'], class_url)
                    if "standard" in lower or "images" in lower:
                        result["images_base64"] = b64
                    elif "graphics" in lower:
                        result["graphics_base64"] = b64
            else:
                # テキスト系セクション（Project history, Ship protection, etc.）
                p = h2.find_next('p')
                if p:
                    text = clean_text(p.get_text(" ", strip=True)).replace('[br]', '\n')
                    result["sections"][section_name] = text

        return result

    # ------------------------------------------------------------------
    # パースヘルパー
    # ------------------------------------------------------------------

    def _parse_class_heading(self, soup, result):
        for tag_name in ("h3", "h2", "title"):
            tag = soup.find(tag_name)
            if not tag:
                continue
            class_name, ship_type, when_date = split_class_heading(tag.get_text(" ", strip=True))
            if class_name:
                result["class_name"] = class_name
            if ship_type:
                result["ship_type"] = ship_type
            if when_date:
                result["when_date"] = when_date
            if class_name or ship_type:
                return

    def _extract_specs(self, soup, result):
        for table in soup.find_all('table'):
            for row in table.find_all('tr'):
                cells = row.find_all(['th', 'td'])
                if len(cells) < 2:
                    continue
                key = clean_text(cells[0].get_text(" ", strip=True))
                val = clean_text(cells[1].get_text(" ", strip=True)).replace(',', '+')
                val = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', val)
                if key in SPEC_TO_COLUMN:
                    if key not in result["specs"]:
                        result["specs"][key] = val

    def _extract_ships(self, soup, result):
        seen_names = set()
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if not rows:
                continue
            headers = [clean_text(c.get_text(" ", strip=True)) for c in rows[0].find_all(['th', 'td'])]
            if "Name" not in headers or "Builder" not in headers:
                continue
            num_cols = len(headers)
            for row in rows[1:]:
                cells = [
                    clean_text(c.get_text(" ", strip=True)).replace(',', '-').replace('[', '<').replace(']', '>')
                    for c in row.find_all('td')
                ]
                if len(cells) < num_cols:
                    continue
                for offset in range(0, len(cells), num_cols):
                    chunk = cells[offset:offset + num_cols]
                    if len(chunk) != num_cols:
                        continue
                    ship = {headers[j]: val for j, val in enumerate(chunk)}
                    name_key = ship.get("Name", "")
                    if name_key and name_key in seen_names:
                        continue
                    if name_key:
                        seen_names.add(name_key)
                    result["ships"].append(ship)

    def _parse_class_type(self, raw, result):
        """reds2テキストからclass_name, ship_type, when_dateを抽出。

        - " - X Navy (Country)" 形式のナビー文脈を除去してから処理する
        - 全小文字トークンは _SHIP_TYPE_KEYWORDS に含まれる場合のみ TYPE に採用
          (国名・海軍名・形容詞などが TYPE に混入するのを防ぐ)
        - キーワード出現前の小文字トークンは前置詞・冠詞としてクラス名に残す
        """
        # " - X Navy (Country)" のナビー文脈を除去
        raw = re.split(r'\s+-\s+', raw)[0].strip()

        tokens = re.findall(r'\([^)]*\)|\S+', raw)

        class_parts = []
        type_part = ""
        when_part = ""
        seen_type = False  # 艦種キーワードを1つでも確認したか

        for token in tokens:
            if token.startswith('(') and token.endswith(')'):
                inner = token[1:-1].strip()
                if re.search(r'\d{4}', inner):
                    when_part += token
                else:
                    class_parts.append(token)
            elif re.fullmatch(r'[a-z]+', token):
                if token in _SHIP_TYPE_KEYWORDS:
                    seen_type = True
                    type_part += ('-' if type_part else '') + token
                elif not seen_type:
                    # 艦種キーワード出現前の小文字 = 前置詞・冠詞 (de, le, van 等)
                    class_parts.append(token)
                # 艦種キーワード出現後の非キーワード小文字は無視 (国名・海軍名等)
            else:
                class_parts.append(token)

        class_name = (' '.join(class_parts).strip() + ' Class') if class_parts else ''
        result["class_name"] = class_name
        result["ship_type"] = type_part.rstrip('-')
        result["when_date"] = when_part

    def _download_image_base64(self, img_url, base_url=BASE_URL):
        """画像URLからbase64文字列を返す"""
        try:
            resp = self._get(normalize_url(img_url, base_url))
            if resp.status_code == 200:
                return base64.b64encode(resp.content).decode('utf-8')
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # DB操作
    # ------------------------------------------------------------------

    def _get_conn(self):
        return sqlite3.connect(str(self.db_path))

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

            # sectionsをJSONからdictに復元
            sections_raw = data.get("sections", "")
            if sections_raw:
                try:
                    data["sections_dict"] = json.loads(sections_raw)
                except json.JSONDecodeError:
                    data["sections_dict"] = {}
            else:
                data["sections_dict"] = {}

            # 対応するSHIPSも取得
            cur2 = conn.execute("SELECT * FROM SHIPS WHERE source_url = ?", (class_url,))
            ship_rows = cur2.fetchall()
            ship_cols = [desc[0] for desc in cur2.description]
            data["ships"] = [dict(zip(ship_cols, r)) for r in ship_rows]

            return data
        finally:
            conn.close()

    def save_to_db(self, detail, country):
        """detailをCLASS, SHIPSテーブルに保存"""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        try:
            specs = detail.get("specs", {})

            # specキーをDBカラム名に変換し、数値フィールドを正規化
            db_specs = {}
            for spec_key, spec_val in specs.items():
                col = SPEC_TO_COLUMN.get(spec_key)
                if col:
                    db_specs[col] = _clean_numeric_spec(col, spec_val)

            sections_json = json.dumps(detail.get("sections", {}), ensure_ascii=False)

            conn.execute("""
                INSERT OR IGNORE INTO CLASS (
                    Photo, CLASS_NAME, TYPE, COUNTRY,
                    Displacement_normal, Displacement_full, Length, Breadth, Draught,
                    No_of_shafts, Machinery, Power, speed, Fuel, Endurance,
                    Armour, Armament, Electronic_equipment, Complement,
                    images, Graphics,
                    Project_history, Modernizations, Naval_service,
                    sections, source_url, fetched_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                detail.get("photo_base64", ""),
                detail.get("class_name", ""),
                detail.get("ship_type", ""),
                country,
                db_specs.get("Displacement_normal", ""),
                db_specs.get("Displacement_full", ""),
                db_specs.get("Length", ""),
                db_specs.get("Breadth", ""),
                db_specs.get("Draught", ""),
                db_specs.get("No_of_shafts", ""),
                db_specs.get("Machinery", ""),
                db_specs.get("Power", ""),
                db_specs.get("speed", ""),
                db_specs.get("Fuel", ""),
                db_specs.get("Endurance", ""),
                db_specs.get("Armour", ""),
                db_specs.get("Armament", ""),
                db_specs.get("Electronic_equipment", ""),
                db_specs.get("Complement", ""),
                detail.get("images_base64", ""),
                detail.get("graphics_base64", ""),
                # 後方互換: 個別カラムにも入れる
                detail.get("sections", {}).get("Project history", ""),
                detail.get("sections", {}).get("Modernizations", ""),
                detail.get("sections", {}).get("Naval service", ""),
                sections_json,
                detail.get("url", ""),
                now,
            ))

            # SHIPSテーブル
            for ship in detail.get("ships", []):
                # Fateから reason と date を分離
                fate_raw = ship.get("Fate", "")
                fate_why = re.sub(r'[0-9\W_]', '', fate_raw)
                fate_when = re.sub(r'[^\d./\-]', '', fate_raw)

                conn.execute("""
                    INSERT INTO SHIPS (
                        NAME, CLASS, COUNTRY, NO, Yard_No, Builder,
                        Laid_down, Launched, Fate_WHY, Fate_WHEN,
                        TYPE, WHEN_DATE, source_url
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    ship.get("Name", ""),
                    detail.get("class_name", ""),
                    country,
                    ship.get("No", ""),
                    ship.get("Yard No", ""),
                    ship.get("Builder", ""),
                    ship.get("Laid down", ""),
                    ship.get("Launched", ""),
                    fate_why,
                    fate_when,
                    detail.get("ship_type", ""),
                    detail.get("when_date", ""),
                    detail.get("url", ""),
                ))

            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # バッチ取得
    # ------------------------------------------------------------------

    def fetch_country_all(self, country_url, country_name, on_progress=None):
        """国全体のデータを並列取得してDBに保存"""
        self._cancel = False

        # 1. 艦種リスト取得
        ship_types = self.fetch_ship_types(country_url)

        # 2. 各艦種から艦級URLを並列取得
        all_class_urls = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self.fetch_class_urls, st["url"]): st for st in ship_types}
            for future in as_completed(futures):
                if self._cancel:
                    return
                try:
                    urls = future.result()
                    all_class_urls.extend(urls)
                except Exception:
                    pass

        # 重複除去
        seen = set()
        unique_urls = []
        for item in all_class_urls:
            if item["url"] not in seen:
                seen.add(item["url"])
                unique_urls.append(item)

        total = len(unique_urls)
        done = 0

        # 3. 各艦級ページを並列取得
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {}
            for item in unique_urls:
                if self._cancel:
                    return
                if self.is_cached(item["url"]):
                    done += 1
                    if on_progress:
                        on_progress(done, total)
                    continue
                futures[pool.submit(self.fetch_class_detail, item["url"], country_name)] = item

            for future in as_completed(futures):
                if self._cancel:
                    return
                done += 1
                try:
                    detail = future.result()
                    self.save_to_db(detail, country_name)
                except Exception:
                    pass
                if on_progress:
                    on_progress(done, total)

    def fetch_all_countries(self, on_progress=None, on_country_start=None, refresh_country_index=False):
        """全国のデータを一括取得してDBに保存。

        on_progress(done, total)       -- 艦級単位の進捗
        on_country_start(name, idx, n) -- 国の処理開始時
        """
        self._cancel = False
        conn = sqlite3.connect(str(self.db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM COUNTRIES").fetchone()[0]
        finally:
            conn.close()
        if refresh_country_index or count == 0:
            self.save_countries(self.fetch_countries(), replace=True)

        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute("SELECT Short, Long, URL FROM COUNTRIES ORDER BY Long").fetchall()
        finally:
            conn.close()

        for i, (short, long_name, url) in enumerate(rows):
            if self._cancel:
                return
            if on_country_start:
                on_country_start(long_name, i, len(rows))
            self.fetch_country_all(normalize_url(url), short, on_progress=on_progress)


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

if __name__ == "__main__":
    import argparse
    import sys
    import time

    parser = argparse.ArgumentParser(
        description="Navypedia ミラーサイト データ収集ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "使用例:\n"
            "  # 新規DBに全国家を収集\n"
            "  python fetcher.py --db nvpda-db/NSDB_new.db all\n\n"
            "  # 特定国のみ収集\n"
            "  python fetcher.py country Japan\n\n"
            "  # 複数国をまとめて収集\n"
            "  python fetcher.py cl Japan Germany France United-Kingdom\n\n"
            "  # 国家インデックスのみ更新\n"
            "  python fetcher.py index\n"
        ),
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB_PATH), metavar="PATH",
        help=f"SQLite DB パス (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--workers", type=int, default=8, metavar="N",
        help="並列ワーカー数 (default: 8)",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_all = sub.add_parser("all", help="全国家のデータを取得")
    p_all.add_argument(
        "--no-refresh-index", action="store_true",
        help="国家インデックスを再取得せずCOUNTRIESテーブルを使う",
    )

    p_country = sub.add_parser("country", help="指定した国のデータを取得")
    p_country.add_argument("name", help="国の短縮名 (例: Japan, United-Kingdom)")
    p_country.add_argument(
        "--url", metavar="URL",
        help="国インデックスURL (省略時は COUNTRIES テーブルから検索)",
    )

    p_cl = sub.add_parser("cl", help="複数国をまとめて取得 (引数省略でインタラクティブ選択)")
    p_cl.add_argument("names", nargs="*", metavar="NAME", help="国の短縮名リスト (省略するとTUI選択画面を起動)")

    sub.add_parser("index", help="国家インデックス (COUNTRIES テーブル) のみ取得・更新")

    p_refetch = sub.add_parser("refetch", help="指定したクラスURLを再取得（上書き）")
    p_refetch.add_argument("url", help="クラスページのURL")
    p_refetch.add_argument("--country", default="", help="国コード（省略時は既存レコードから推定）")

    args = parser.parse_args()

    fetcher = NavypediaFetcher(db_path=args.db, max_workers=args.workers)
    start = time.time()

    def _elapsed():
        m, s = divmod(int(time.time() - start), 60)
        return f"{m}分{s}秒"

    def _resolve_country_url(name):
        """COUNTRIESテーブルからShort名でURLを返す。見つからなければNone。"""
        conn = sqlite3.connect(str(fetcher.db_path))
        try:
            row = conn.execute(
                "SELECT URL FROM COUNTRIES WHERE Short = ? OR Long = ?",
                (name, name),
            ).fetchone()
        finally:
            conn.close()
        return normalize_url(row[0]) if row else None

    if args.cmd == "index":
        print("国家インデックスを取得中 …")
        countries = fetcher.fetch_countries()
        fetcher.save_countries(countries, replace=True)
        print(f"  {len(countries)} カ国を COUNTRIES に保存しました ({_elapsed()})")

    elif args.cmd == "all":
        refresh = not args.no_refresh_index

        def on_country_start(name, idx, total):
            print(f"\n[{idx + 1}/{total}] {name}", flush=True)

        def on_progress(done, total):
            print(f"\r  {done}/{total}", end="", flush=True)

        try:
            fetcher.fetch_all_countries(
                on_progress=on_progress,
                on_country_start=on_country_start,
                refresh_country_index=refresh,
            )
        except KeyboardInterrupt:
            print("\n中断されました")
            sys.exit(1)

        print(f"\n=== 全件完了 ({_elapsed()}) ===")

    elif args.cmd == "country":
        if args.url:
            country_url = normalize_url(args.url)
        else:
            country_url = _resolve_country_url(args.name)
            if not country_url:
                print(
                    f"エラー: '{args.name}' が COUNTRIES テーブルに見つかりません。\n"
                    "先に  python fetcher.py index  を実行してください。"
                )
                sys.exit(1)

        def on_progress(done, total):
            print(f"\r  {done}/{total}", end="", flush=True)

        print(f"{args.name} を取得中 ({country_url})", flush=True)
        try:
            fetcher.fetch_country_all(country_url, args.name, on_progress=on_progress)
        except KeyboardInterrupt:
            print("\n中断されました")
            sys.exit(1)

        print(f"\n完了 ({_elapsed()})")

    elif args.cmd == "cl":
        chosen = list(args.names)

        if not chosen:
            # インタラクティブ選択
            conn = sqlite3.connect(str(fetcher.db_path))
            try:
                rows = conn.execute("SELECT Short FROM COUNTRIES ORDER BY Short").fetchall()
            finally:
                conn.close()
            if not rows:
                print("エラー: COUNTRIES テーブルが空です。先に  python fetcher.py index  を実行してください。")
                sys.exit(1)

            all_names = [r[0] for r in rows]
            chosen = _select_countries_tui(all_names)

            if not chosen:
                print("選択なし。終了します。")
                sys.exit(0)

        not_found = [n for n in chosen if not _resolve_country_url(n)]
        if not_found:
            print(
                "エラー: 以下の国名が COUNTRIES テーブルに見つかりません:\n"
                + "\n".join(f"  - {n}" for n in not_found)
                + "\n先に  python fetcher.py index  を実行してください。"
            )
            sys.exit(1)

        def on_progress(done, total):
            print(f"\r  {done}/{total}", end="", flush=True)

        for i, name in enumerate(chosen):
            country_url = _resolve_country_url(name)
            print(f"\n[{i + 1}/{len(chosen)}] {name} ({country_url})", flush=True)
            try:
                fetcher.fetch_country_all(country_url, name, on_progress=on_progress)
            except KeyboardInterrupt:
                print("\n中断されました")
                sys.exit(1)
            print(f"  完了 ({_elapsed()})")

        print(f"\n=== {len(chosen)} カ国完了 ({_elapsed()}) ===")

    elif args.cmd == "refetch":
        class_url = normalize_url(args.url)
        conn = sqlite3.connect(str(fetcher.db_path))
        try:
            if not args.country:
                row = conn.execute("SELECT COUNTRY FROM CLASS WHERE source_url = ?", (class_url,)).fetchone()
                country = row[0] if row else ""
            else:
                country = args.country
            conn.execute("DELETE FROM CLASS WHERE source_url = ?", (class_url,))
            conn.execute("DELETE FROM SHIPS WHERE source_url = ?", (class_url,))
            conn.commit()
        finally:
            conn.close()
        print(f"再取得中: {class_url} (country={country})", flush=True)
        detail = fetcher.fetch_class_detail(class_url, country)
        fetcher.save_to_db(detail, country)
        print(f"完了 ({_elapsed()})")
        print(f"  class_name: {detail.get('class_name')}")
        print(f"  ship_type: {detail.get('ship_type')}")
        print(f"  ships: {len(detail.get('ships', []))}")
