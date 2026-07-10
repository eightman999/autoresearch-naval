import base64
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
import tqdm


BASE_URL = "https://myownonpmirror.com/"
DB_PATH = Path(__file__).resolve().parent / "NSDB.db"
SHIP_TYPE_CSV = Path(__file__).resolve().with_name("SHIP_TYPE_LIST.csv")


def normalize_url(url):
    if not url:
        return ""
    parsed = urlparse(urljoin(BASE_URL, url))
    netloc = parsed.netloc.replace("www.", "")
    path = re.sub(r"\.htm$", ".html", parsed.path)
    return urlunparse(("https", netloc, path, "", parsed.query, parsed.fragment))


def clean_text(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def dash_text(text):
    return clean_text(text).replace(",", "-").replace("[", "<").replace("]", ">")


def title_case_class_name(text):
    text = clean_text(text).strip("'\"")
    words = []
    for word in text.split():
        if word.isupper() or re.fullmatch(r"[A-Z0-9][A-Z0-9/-]*", word):
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def split_class_heading(text):
    text = clean_text(text)
    when = ""
    when_match = re.search(r"\(([^)]*\d{4}[^)]*)\)\s*$", text)
    if when_match:
        when = f"({when_match.group(1)})"
        text = text[:when_match.start()].strip()

    title_match = re.search(r"\(([^()]*?)\s+class\s*,?\s*[^()]*\d{4}[^()]*\)", text, re.IGNORECASE)
    if title_match:
        class_source = title_match.group(1)
    else:
        class_source = text
        class_source = re.sub(r"\btype\b.*$", "", class_source, flags=re.IGNORECASE).strip()
        class_source = re.split(
            r"\b(?:battleships?|cruisers?|destroyers?|frigates?|corvettes?|sloops?|submarines?|"
            r"minelayers?|minesweepers?|monitors?|gunboats?|torpedo-boats?|patrol|escorts?)\b",
            class_source,
            flags=re.IGNORECASE,
        )[0].strip()

    class_source = class_source.strip(" ,")
    class_name = f"{title_case_class_name(class_source)} Class" if class_source else ""

    type_source = text
    if class_source:
        type_source = type_source.replace(class_source, "", 1).strip(" ,")
    type_source = re.sub(r"\btype\b", "", type_source, flags=re.IGNORECASE)
    ship_type = "-".join(re.findall(r"[A-Za-z]+", type_source.lower()))
    return class_name, ship_type, when


def extract_class_metadata(soup):
    for tag_name in ("h3", "h2", "title"):
        tag = soup.find(tag_name)
        if tag:
            class_name, ship_type, when = split_class_heading(tag.get_text(" ", strip=True))
            if class_name or ship_type:
                return class_name, ship_type, when
    return "", "", ""

# データベースに接続
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

offset = 0
limit = 1  # Number of entries to fetch at a time

while True:
    print('offset:', offset)
    cursor.execute(f"SELECT url, Short FROM COUNTRIES LIMIT {limit} OFFSET {offset}")
    result = cursor.fetchone()
    if not result:
        break

    url, country_name = result
    # print('url:', url)
    print('country_name:', country_name)

    # URLにアクセス
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')

    # SHIP_TYPE_LIST.csvのC1の文言を取得
    ship_types = []
    with open(SHIP_TYPE_CSV, 'r', encoding='utf-8') as file:
        for line in file:
            if line.strip():
                ship_type = line.split(']')[0].strip('[').replace(',', '-')
                ship_types.append(ship_type)

    # 除外するURLリスト
    exclusion_list = [
        '/',
        'https://myownonpmirror.com/privacy.html',
        'https://www.paypal.com/paypalme/igogin',
        'https://myownonpmirror.com/ships_index.html'
    ]

    # <a>タグのhref属性のURLを取得
    urls = []
    class_url = []
    for a_tag in tqdm.tqdm(soup.find_all('a', href=True), desc="URLを取得中"):
        full_url = normalize_url(a_tag['href'])
        if any(ship_type in a_tag.text for ship_type in ship_types) and full_url not in exclusion_list and full_url not in urls:
            urls.append(full_url)

    # 取得したURLリストのページにアクセスし、<a>のhrefとテキスト部分を取得
    for url in tqdm.tqdm(urls, desc="クラスURLを取得中"):
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        for a_tag in soup.find_all('a', href=True):
            full_url = normalize_url(a_tag['href'])
            if full_url in exclusion_list:
                continue
            if urlparse(full_url).path.startswith('/ships/') and not re.search(r'_index\.html$', full_url):
                if full_url not in class_url:
                    class_url.append(full_url)
    # print('class_url:', class_url)
    # class_urlのページにアクセスし、<a>のidがreds2の要素をclass_type[]に、classがcwdtableの<table>要素をcwdtable[]に格納
    class_meta = []
    cwdtable = []

    for url in tqdm.tqdm(class_url, desc="クラスを取得中"):
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        class_meta.append(extract_class_metadata(soup))
    blue = []

    S_CLASS = [meta[0] for meta in class_meta]
    S_TYPE = [meta[1] for meta in class_meta]
    S_WHEN = [meta[2] for meta in class_meta]

    # print('S_CLASS:', S_CLASS)
    # print('S_TYPE:', S_TYPE)

    for i, url in enumerate(tqdm.tqdm(class_url, desc="テーブルを取得中")):
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        for table in soup.find_all('table', class_='cwdtable'):
            html = str(table)
            soup2 = BeautifulSoup(html, 'html.parser')
            rows = soup2.find_all('tr')[1:]
            for row in rows:
                cells = [dash_text(cell.get_text(" ", strip=True)) for cell in row.find_all('td')]
                if len(cells) > 8:
                    cells = cells[:8]
                cells.insert(0, S_TYPE[i] if i < len(S_TYPE) else "")
                cells.insert(0, S_CLASS[i] if i < len(S_CLASS) else "")
                cells.insert(0, S_WHEN[i] if i < len(S_WHEN) else "")
                cells.insert(0, url)
                cwdtable.append(cells)

    for i, url in enumerate(tqdm.tqdm(class_url, desc="クラス用のデータを取得中")):
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'html.parser')

        blue_entry = ["", S_CLASS[i] if i < len(S_CLASS) else "", S_TYPE[i] if i < len(S_TYPE) else "", country_name] + [""] * 20

        photo_tag = soup.find(string="Photo")
        if photo_tag:
            img_tag = photo_tag.find_next('img', class_='css-adaptive')
            if img_tag and 'src' in img_tag.attrs:
                img_url = normalize_url(img_tag['src'])
                img_response = requests.get(img_url)
                if img_response.status_code == 200:
                    img_base64 = base64.b64encode(img_response.content).decode('utf-8')
                    blue_entry[0] = img_base64

        blue_table = soup.find('table', class_='blue')
        if blue_table:
            headers = [
                ("Displacement standard, t", 4),
                ("Displacement normal, t", 4),
                ("Displacement full, t", 5),
                ("Length, m", 6),
                ("Breadth, m", 7),
                ("Draught, m", 8),
                ("No of shafts", 9),
                ("Machinery", 10),
                ("Power, h. p.", 11),
                ("Max speed, kts", 12),
                ("Fuel, t", 13),
                ("Endurance, nm(kts)", 14),
                ("Armor", 15),
                ("Armament", 16),
                ("Electronic equipment", 17),
                ("Complement", 18)
            ]

            blue_entry.extend([""] * (len(headers)))

            for row in blue_table.find_all('tr'):
                th = row.find('th')
                td = row.find('td')
                if th and td:
                    th_text = th.get_text(strip=True)
                    td_text = td.get_text(strip=True).replace(',', '+')
                    for header, position in headers:
                        if th_text == header:
                            blue_entry[position] = td_text
                            break

        h2_tags = [
            ("Standard scale images", 19),
            ("Graphics", 20),
            ("Project history", 21),
            ("Modernizations", 22),
            ("Naval service", 23)
        ]

        blue_entry.extend([""] * (max(position for _, position in h2_tags) + 1 - len(blue_entry)))

        for h2_text, position in h2_tags:
            h2_tag = soup.find('h2', string=h2_text)
            if h2_tag:
                if "images" in h2_text.lower() or "graphics" in h2_text.lower():
                    img_tag = h2_tag.find_next('img')
                    if img_tag and 'src' in img_tag.attrs:
                        img_url = normalize_url(img_tag['src'])
                        img_response = requests.get(img_url)
                        if img_response.status_code == 200:
                            img_base64 = base64.b64encode(img_response.content).decode('utf-8')
                            blue_entry[position] = img_base64
                        else:
                            blue_entry[position] = ""
                    else:
                        blue_entry[position] = ""
                elif "history" in h2_text.lower() or "modernizations" in h2_text.lower() or "service" in h2_text.lower():
                    p_tag = h2_tag.find_next('p')
                    if p_tag:
                        p_text = p_tag.get_text(strip=True).replace('[br]', '\n')
                        blue_entry[position] = p_text
                    else:
                        blue_entry[position] = ""
            else:
                blue_entry[position] = ""

        blue.append(blue_entry)

    insert_query = """
    INSERT INTO CLASS (Photo, CLASS_NAME, TYPE, COUNTRY, Displacement_normal, Displacement_full, Length, Breadth, Draught, No_of_shafts, Machinery, Power, speed, Fuel, Endurance, Armour, Armament, Electronic_equipment, Complement, images, Graphics, Project_history, Modernizations, Naval_service, source_url, fetched_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    for i, row in enumerate(blue):
        source_url = class_url[i] if i < len(class_url) else ""
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cursor.execute(insert_query, (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11], row[12], row[13], row[14], row[15], row[16], row[17], row[18], row[19], row[20], row[21], row[22], row[23], source_url, fetched_at))

    insert_query_2 = """
    INSERT INTO SHIPS (SID, NAME, CLASS, COUNTRY, NO, Yard_No, Builder, Laid_down, Launched, Fate_WHY, Fate_WHEN, TYPE, WHEN_DATE, source_url)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    for i, row in enumerate(cwdtable):
        sid = f"{i+1:05X}"  # 16進数の5桁でSIDを生成
        source_url = row[0]
        build_when = row[1]
        class_name = row[2]
        ship_type = row[3]
        name = row[4]
        no = row[5] if len(row) > 5 else ""
        yard_no = row[6] if len(row) > 6 else ""
        builder = row[7] if len(row) > 7 else ""
        laid_down = row[8] if len(row) > 8 else ""
        launched = row[9] if len(row) > 9 else ""
        fate = row[10] if len(row) > 10 else ""
        fate_why = re.sub(r'[0-9\W_]', '', fate)
        fate_when = re.sub(r'[^\d\W_]', '', fate)
        cursor.execute(insert_query_2, (sid, name, class_name, country_name, no, yard_no, builder, laid_down, launched, fate_why, fate_when, ship_type, build_when, source_url))

    # コミットしてデータベース接続を閉じる
    conn.commit()
    print(country_name + 'is Fin')

    offset += limit

# データベース接続を閉じる
conn.close()
