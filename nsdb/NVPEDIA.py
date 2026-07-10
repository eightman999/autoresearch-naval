


import base64
import io
import json
from pathlib import Path
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from deep_translator import GoogleTranslator
from fetcher import NavypediaFetcher

DB_PATH = str(Path(__file__).resolve().parent / "NSDB.db")


# ======================================================================
# 日本語ラベル辞書
# ======================================================================

# セクション名
SECTION_NAME_JA = {
    "Project history": "計画経緯",
    "Modernizations": "近代化改装",
    "Naval service": "軍歴",
    "Ship protection": "防御",
    "Aircraft facilities": "航空艤装",
    "Air group": "搭載機",
    "Fate Notes": "運命（備考）",
}

# スペック項目名（表Aのヘッダー）
SPEC_LABEL_JA = {
    "Displacement standard, t": "基準排水量 (t)",
    "Displacement normal, t": "常備排水量 (t)",
    "Displacement full, t": "満載排水量 (t)",
    "Displacement normal": "常備排水量",
    "Displacement full": "満載排水量",
    "Length, m": "全長 (m)",
    "Breadth, m": "幅 (m)",
    "Draught, m": "喫水 (m)",
    "No of shafts": "軸数",
    "Machinery": "機関",
    "Power, h. p.": "出力 (hp)",
    "Power, h.p.": "出力 (hp)",
    "Max speed, kts": "最大速力 (kt)",
    "Fuel, t": "燃料 (t)",
    "Endurance, nm(kts)": "航続距離",
    "Endurance": "航続距離",
    "Armor": "装甲",
    "Armour": "装甲",
    "Armament": "兵装",
    "Electronic equipment": "電子装備",
    "Complement": "乗員数",
}

# 艦リスト（表B）ヘッダー
SHIP_HEADER_JA = {
    "Name": "艦名",
    "No": "艦番号",
    "Yard No": "造船所番号",
    "Builder": "建造所",
    "Laid down": "起工",
    "Launched": "進水",
    "Comp": "竣工",
    "Fate": "運命",
}

# 艦種TYPE
TYPE_JA = {
    "Class": "級",
    "class": "級",
    "destroyers": "駆逐艦",
    "destroyer": "駆逐艦",
    "escort-destroyers": "護衛駆逐艦",
    "missile-destroyers": "ミサイル駆逐艦",
    "helicopter-destroyers": "ヘリコプター搭載護衛艦",
    "frigates": "フリゲート",
    "frigate": "フリゲート",
    "escorts": "護衛艦",
    "escort": "護衛艦",
    "auxiliary-escorts": "補助護衛艦",
    "corvettes": "コルベット",
    "screw-corvettes": "スクリューコルベット",
    "battleships": "戦艦",
    "battleship": "戦艦",
    "battlecruisers": "巡洋戦艦",
    "light-cruisers": "軽巡洋艦",
    "light-cruiser": "軽巡洋艦",
    "heavy-cruisers": "重巡洋艦",
    "protected-cruisers": "防護巡洋艦",
    "protected-cruiser": "防護巡洋艦",
    "armoured-cruisers": "装甲巡洋艦",
    "armoured-cruiser": "装甲巡洋艦",
    "unarmoured-cruiser": "非装甲巡洋艦",
    "class-cruiser": "巡洋艦",
    "light-heavy-cruisers": "軽重巡洋艦",
    "armed-merchant-cruisers-of": "特設巡洋艦",
    "armed-merchant-cruisers-of-war": "特設巡洋艦",
    "aircraft-carriers": "航空母艦",
    "aircraft-carrier": "航空母艦",
    "light-aircraft-carrier": "軽空母",
    "escort-aircraft-carrier": "護衛空母",
    "escort-aircraft-carriers-of": "護衛空母",
    "submarines": "潜水艦",
    "submarine": "潜水艦",
    "class-submarines": "潜水艦",
    "class-submarine": "潜水艦",
    "attack-submarines": "攻撃型潜水艦",
    "fast-attack-submarines": "高速攻撃潜水艦",
    "coastal-submarines": "沿岸型潜水艦",
    "cruiser-submarines": "巡洋潜水艦",
    "large-submarines": "大型潜水艦",
    "large-patrol-submarine": "大型哨戒潜水艦",
    "transport-submarines": "輸送潜水艦",
    "small-transport-submarines-of": "小型輸送潜水艦",
    "small-transport-submarines": "小型輸送潜水艦",
    "small-fast-attack-submarines": "小型高速攻撃潜水艦",
    "submarine-aircraft-carriers": "潜水空母",
    "midget-submarines": "特殊潜航艇",
    "midget-submarine": "特殊潜航艇",
    "type-submarines": "潜水艦",
    "type-submarine": "潜水艦",
    "submerging-assault-craft": "水中突撃艇",
    "torpedo-boats": "水雷艇",
    "torpedo-boat": "水雷艇",
    "class-torpedo-boats": "水雷艇",
    "class-torpedo-boat": "水雷艇",
    "type-torpedo-boats": "水雷艇",
    "type-torpedo-boat": "水雷艇",
    "gunboats": "砲艦",
    "gunboat": "砲艦",
    "river-gunboats": "河用砲艦",
    "river-gunboat": "河用砲艦",
    "armoured-gunboats": "装甲砲艦",
    "auxiliary-gunboats-of": "特設砲艦",
    "type-gun-boats": "砲艦",
    "patrol-craft": "哨戒艇",
    "patrol-boats": "哨戒艇",
    "patrol-ship": "哨戒艦",
    "large-patrol-vessels": "大型哨戒艦",
    "large-patrol-vessel": "大型哨戒艦",
    "large-patrol-ships": "大型哨戒艦",
    "large-patrol-craft": "大型哨戒艇",
    "medium-patrol-vessels": "中型哨戒艇",
    "small-patrol-vessels": "小型哨戒艇",
    "auxiliary-patrols": "特設監視艇",
    "coast-guard-patrol-vessel": "沿岸警備哨戒艇",
    "coast-guard-patrol-vessels": "沿岸警備哨戒艇",
    "submarine-chasers": "駆潜艇",
    "submarine-chaser": "駆潜艇",
    "auxiliary-submarine-chasers": "特設駆潜艇",
    "auxiliary-submarine-chaser": "特設駆潜艇",
    "fast-attack-craft": "高速戦闘艇",
    "fast-attack-craft-fast-attack-craft": "高速戦闘艇",
    "fast-attack-hydrofoil": "高速水中翼戦闘艇",
    "minesweepers": "掃海艇",
    "coastal-minesweepers": "沿岸掃海艇",
    "inshore-minesweepers": "近海掃海艇",
    "ocean-minesweepers": "外洋掃海艇",
    "auxiliary-minesweepers": "特設掃海艇",
    "minelayer": "機雷敷設艦",
    "coastal-minelayers-netlayers-coastal-minelayers-netlayers-coastal-minelayer-netlayer": "沿岸機雷敷設艦/防潜網敷設艦",
    "coastal-minelayers-minesweepers": "沿岸機雷敷設・掃海艇",
    "auxiliary-minelayers-of": "特設敷設艦",
    "seaplane-tender": "水上機母艦",
    "seaplane-tenders-converted-from-merchant-vessels": "特設水上機母艦",
    "small-landing-ships": "小型揚陸艦",
    "tank-landing-ships": "戦車揚陸艦",
    "type-tank-landing-ships": "戦車揚陸艦",
    "landing-craft": "上陸用舟艇",
    "type-landing-craft": "上陸用舟艇",
    "landing-craft-transports-landing-craft-transports-landing-craft-transport": "揚陸輸送艇",
    "large-amphibious-fire-support-ships": "大型揚陸火力支援艦",
    "type-fast-amphibious-transports": "高速揚陸輸送艦",
    "small-ships": "小型艦艇",
    "type-motor-launches": "モーターランチ",
    "monitors": "モニター艦",
    "training-ships": "練習艦",
}


def _ja(dictionary, key):
    """辞書からキーの日本語訳を返す。なければ原文をそのまま返す。"""
    return dictionary.get(key, key)


def _ensure_translation_table():
    """翻訳キャッシュテーブルを作成（なければ）"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS TRANSLATIONS (
            source_url  TEXT NOT NULL,
            section_name TEXT NOT NULL,
            translated  TEXT NOT NULL,
            PRIMARY KEY (source_url, section_name)
        )
    """)
    conn.commit()
    conn.close()


_ensure_translation_table()


class NavalApp:
    def __init__(self, root):
        self.root = root
        self.root.title("NECS - Naval Encyclopedia Console Service")
        self.root.geometry('1200x750')

        self.fetcher = NavypediaFetcher(db_path=DB_PATH)
        self.countries = []       # [(Short, Long, URL), ...]
        self.ship_types = []      # [{"name": ..., "url": ...}, ...]
        self.class_urls = []      # [{"name": ..., "url": ...}, ...]
        self.current_detail = None
        self.photo_images = []    # base64リスト（Photo, images, Graphics）
        self.photo_index = 0
        self._tk_photo = None     # PhotoImage参照保持用
        self._showing_translated = False  # 翻訳表示中フラグ
        self._current_source_url = ""     # 現在表示中の艦級URL

        self._build_ui()
        self._load_countries()

    # ------------------------------------------------------------------
    # UI構築
    # ------------------------------------------------------------------
    def _build_ui(self):
        # --- Top bar: 国選択 + 一括DL ---
        top_frame = tk.Frame(self.root)
        top_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

        tk.Label(top_frame, text="国名").pack(side="left", padx=(0, 5))
        self.country_var = tk.StringVar()
        self.country_dropdown = ttk.Combobox(top_frame, textvariable=self.country_var, width=25)
        self.country_dropdown.pack(side="left")
        self.country_dropdown.bind("<<ComboboxSelected>>", self._on_country_selected)

        self.batch_btn = tk.Button(top_frame, text="一括DL", command=self._on_batch_dl)
        self.batch_btn.pack(side="left", padx=10)

        self.batch_all_btn = tk.Button(top_frame, text="全サイト一括DL", command=self._on_batch_all_dl)
        self.batch_all_btn.pack(side="left", padx=5)

        self.cancel_btn = tk.Button(top_frame, text="キャンセル", command=self._on_cancel, state="disabled")
        self.cancel_btn.pack(side="left")

        self.status_var = tk.StringVar(value="")
        tk.Label(top_frame, textvariable=self.status_var, fg="gray").pack(side="left", padx=10)

        self.progress = ttk.Progressbar(top_frame, length=200, mode='determinate')
        self.progress.pack(side="left", padx=5)

        # --- Left: 艦種・艦級リスト ---
        left_frame = tk.Frame(self.root)
        left_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

        tk.Label(left_frame, text="艦種").pack(anchor="w")
        self.ship_type_listbox = tk.Listbox(left_frame, height=15, width=30)
        self.ship_type_listbox.pack(fill="both", expand=True)
        self.ship_type_listbox.bind("<<ListboxSelect>>", self._on_type_selected)

        tk.Label(left_frame, text="艦級").pack(anchor="w", pady=(10, 0))
        self.ship_class_listbox = tk.Listbox(left_frame, height=15, width=30)
        self.ship_class_listbox.pack(fill="both", expand=True)
        self.ship_class_listbox.bind("<<ListboxSelect>>", self._on_class_selected)

        # --- Right: 詳細表示 ---
        right_frame = tk.Frame(self.root)
        right_frame.grid(row=1, column=1, sticky="nsew", padx=5, pady=5)

        # 艦型名
        self.class_name_var = tk.StringVar(value="")
        tk.Label(right_frame, textvariable=self.class_name_var, font=("", 14, "bold")).pack(anchor="w")

        # Photo + 説明
        detail_top = tk.Frame(right_frame)
        detail_top.pack(fill="x", pady=5)

        photo_frame = tk.Frame(detail_top)
        photo_frame.pack(side="left", padx=(0, 10))
        self._placeholder = tk.PhotoImage(width=480, height=360)  # 空画像でサイズ固定
        self.photo_label = tk.Label(photo_frame, text="No Photo", bg="lightgray",
                                    image=self._placeholder, compound="center")
        self.photo_label.pack()
        tk.Button(photo_frame, text="Photo切替", command=self._switch_photo).pack(pady=2)

        explain_frame = tk.Frame(detail_top)
        explain_frame.pack(side="left", fill="both", expand=True)

        explain_top_bar = tk.Frame(explain_frame)
        explain_top_bar.pack(fill="x")
        self.translate_btn = tk.Button(explain_top_bar, text="日本語に翻訳", command=self._on_translate)
        self.translate_btn.pack(side="right")
        self.translate_status_var = tk.StringVar(value="")
        tk.Label(explain_top_bar, textvariable=self.translate_status_var, fg="gray").pack(side="left")

        self.explanation_text = scrolledtext.ScrolledText(explain_frame, width=50, height=14)
        self.explanation_text.pack(fill="both", expand=True)

        # 表A（スペック）+ 表B（艦リスト）
        tables_frame = tk.Frame(right_frame)
        tables_frame.pack(fill="both", expand=True, pady=5)

        # 表A: スペック（key-value）
        table_a_frame = tk.Frame(tables_frame)
        table_a_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))
        tk.Label(table_a_frame, text="スペック").pack(anchor="w")
        self.table_a = ttk.Treeview(table_a_frame, columns=("Item", "Value"), show="headings", height=10)
        self.table_a.heading("Item", text="項目")
        self.table_a.heading("Value", text="値")
        self.table_a.column("Item", width=150)
        self.table_a.column("Value", width=200)
        self.table_a.pack(fill="both", expand=True)

        # 表B: 艦リスト（動的列）
        table_b_frame = tk.Frame(tables_frame)
        table_b_frame.pack(side="left", fill="both", expand=True)
        tk.Label(table_b_frame, text="艦リスト").pack(anchor="w")
        self.table_b_frame = table_b_frame
        self.table_b = None
        self._init_table_b([])

        # grid weights
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=0)
        self.root.grid_columnconfigure(1, weight=1)

    def _init_table_b(self, headers):
        """表Bを動的列で再作成"""
        if self.table_b:
            self.table_b.destroy()
        if not headers:
            headers = [_ja(SHIP_HEADER_JA, h) for h in ["Name", "No", "Yard No", "Builder", "Laid down", "Launched", "Comp", "Fate"]]
        col_ids = [f"c{i}" for i in range(len(headers))]
        self.table_b = ttk.Treeview(self.table_b_frame, columns=col_ids, show="headings", height=10)
        for cid, h in zip(col_ids, headers):
            self.table_b.heading(cid, text=h)
            self.table_b.column(cid, width=max(80, len(h.encode('utf-8')) * 5))
        self.table_b.pack(fill="both", expand=True)
        self._table_b_headers = headers

    # ------------------------------------------------------------------
    # データ読み込み
    # ------------------------------------------------------------------
    def _load_countries(self):
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute("SELECT Short, Long, URL FROM COUNTRIES ORDER BY Long").fetchall()
            self.countries = rows
            self.country_dropdown['values'] = [f"{r[1]} ({r[0]})" for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # イベントハンドラ
    # ------------------------------------------------------------------
    def _on_country_selected(self, event=None):
        idx = self.country_dropdown.current()
        if idx < 0:
            return
        country = self.countries[idx]
        url = country[2]
        self.ship_type_listbox.delete(0, "end")
        self.ship_class_listbox.delete(0, "end")
        self._clear_detail()

        # キャッシュ確認（メインスレッドで即座に）
        cached = self.fetcher.get_cached_ship_types(url)
        if cached is not None:
            self._update_ship_types(cached, from_cache=True)
            return

        self.status_var.set("艦種を取得中...")

        def task():
            try:
                types = self.fetcher.fetch_ship_types(url)
                self.root.after(0, self._update_ship_types, types)
            except Exception as e:
                self.root.after(0, self.status_var.set, f"エラー: {e}")

        threading.Thread(target=task, daemon=True).start()

    def _update_ship_types(self, types, from_cache=False):
        self.ship_types = types
        self.ship_type_listbox.delete(0, "end")
        for t in types:
            self.ship_type_listbox.insert("end", t["name"])
        suffix = "（キャッシュ）" if from_cache else ""
        self.status_var.set(f"{len(types)} 艦種を取得{suffix}")

    def _on_type_selected(self, event=None):
        sel = self.ship_type_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        type_url = self.ship_types[idx]["url"]
        self.ship_class_listbox.delete(0, "end")
        self._clear_detail()

        # キャッシュ確認（メインスレッドで即座に）
        cached = self.fetcher.get_cached_class_urls(type_url)
        if cached is not None:
            self._update_class_urls(cached, from_cache=True)
            return

        self.status_var.set("艦級を取得中...")

        def task():
            try:
                urls = self.fetcher.fetch_class_urls(type_url)
                self.root.after(0, self._update_class_urls, urls)
            except Exception as e:
                self.root.after(0, self.status_var.set, f"エラー: {e}")

        threading.Thread(target=task, daemon=True).start()

    def _update_class_urls(self, urls, from_cache=False):
        self.class_urls = urls
        self.ship_class_listbox.delete(0, "end")
        for u in urls:
            self.ship_class_listbox.insert("end", u["name"])
        suffix = "（キャッシュ）" if from_cache else ""
        self.status_var.set(f"{len(urls)} 艦級を取得{suffix}")

    def _on_class_selected(self, event=None):
        sel = self.ship_class_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        class_info = self.class_urls[idx]
        class_url = class_info["url"]

        # 国名取得
        country_idx = self.country_dropdown.current()
        country_name = self.countries[country_idx][0] if country_idx >= 0 else ""

        # キャッシュ確認
        cached = self.fetcher.get_cached_detail(class_url)
        if cached:
            self.status_var.set("キャッシュから表示")
            self._display_cached(cached)
            return

        self.status_var.set("データを取得中...")

        def task():
            try:
                detail = self.fetcher.fetch_class_detail(class_url, country_name)
                self.fetcher.save_to_db(detail, country_name)
                self.root.after(0, self._display_detail, detail)
            except Exception as e:
                self.root.after(0, self.status_var.set, f"エラー: {e}")

        threading.Thread(target=task, daemon=True).start()

    # ------------------------------------------------------------------
    # 一括DL
    # ------------------------------------------------------------------
    def _on_batch_dl(self):
        idx = self.country_dropdown.current()
        if idx < 0:
            messagebox.showwarning("警告", "国を選択してください")
            return
        country = self.countries[idx]
        country_url = country[2]
        country_name = country[0]

        self.batch_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.progress['value'] = 0
        self.status_var.set("一括DL中...")

        def progress_callback(done, total):
            self.root.after(0, self._update_progress, done, total)

        def task():
            try:
                self.fetcher.fetch_country_all(country_url, country_name, on_progress=progress_callback)
                self.root.after(0, self._batch_done)
            except Exception as e:
                self.root.after(0, self._batch_error, str(e))

        threading.Thread(target=task, daemon=True).start()

    def _update_progress(self, done, total):
        if total > 0:
            self.progress['value'] = (done / total) * 100
        self.status_var.set(f"一括DL: {done}/{total}")

    def _batch_done(self):
        self.batch_btn.config(state="normal")
        self.batch_all_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.status_var.set("一括DL完了")
        self.progress['value'] = 100

    def _batch_error(self, msg):
        self.batch_btn.config(state="normal")
        self.batch_all_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.status_var.set(f"一括DLエラー: {msg}")

    # ------------------------------------------------------------------
    # 全サイト一括DL
    # ------------------------------------------------------------------
    def _on_batch_all_dl(self):
        if not messagebox.askyesno("確認", "全サイトのデータを一括取得します。\n時間がかかりますが続行しますか？"):
            return

        self.batch_btn.config(state="disabled")
        self.batch_all_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.progress['value'] = 0
        self.status_var.set("全サイト一括DL中...")

        def progress_callback(done, total):
            self.root.after(0, self._update_progress, done, total)

        def country_callback(name, idx, n):
            self.root.after(0, self.status_var.set, f"全サイトDL: {name} ({idx + 1}/{n})")

        def task():
            try:
                self.fetcher.fetch_all_countries(
                    on_progress=progress_callback,
                    on_country_start=country_callback,
                )
                self.root.after(0, self._batch_all_done)
            except Exception as e:
                self.root.after(0, self._batch_error, str(e))

        threading.Thread(target=task, daemon=True).start()

    def _batch_all_done(self):
        self.batch_btn.config(state="normal")
        self.batch_all_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.status_var.set("全サイト一括DL完了")
        self.progress['value'] = 100

    def _on_cancel(self):
        self.fetcher.cancel()
        self.cancel_btn.config(state="disabled")
        self.status_var.set("キャンセル中...")

    # ------------------------------------------------------------------
    # 表示
    # ------------------------------------------------------------------
    def _clear_detail(self):
        self.class_name_var.set("")
        self.photo_label.config(image=self._placeholder, text="No Photo", compound="center")
        self._tk_photo = None
        self.explanation_text.delete("1.0", "end")
        for item in self.table_a.get_children():
            self.table_a.delete(item)
        if self.table_b:
            for item in self.table_b.get_children():
                self.table_b.delete(item)
        self._showing_translated = False
        self._current_source_url = ""
        self.translate_btn.config(text="日本語に翻訳", state="normal")
        self.translate_status_var.set("")

    def _display_detail(self, detail):
        """fetch_class_detailの返り値を表示"""
        self._clear_detail()
        self.current_detail = detail
        self._current_source_url = detail.get("url", "") or detail.get("source_url", "")
        self.status_var.set("")

        # 艦型名
        ship_type_raw = detail.get('ship_type', '')
        ship_type_disp = _ja(TYPE_JA, ship_type_raw) if ship_type_raw else ''
        self.class_name_var.set(f"{detail.get('class_name', '')} {ship_type_disp} {detail.get('when_date', '')}")

        # Photo
        self.photo_images = []
        for key in ["photo_base64", "images_base64", "graphics_base64"]:
            b64 = detail.get(key, "")
            if b64:
                self.photo_images.append(b64)
        self.photo_index = 0
        self._show_photo()

        # スペック（表A）
        for key, val in detail.get("specs", {}).items():
            self.table_a.insert("", "end", values=(_ja(SPEC_LABEL_JA, key), val))

        # 艦リスト（表B）
        ships = detail.get("ships", [])
        if ships:
            headers = list(ships[0].keys())
            headers_ja = [_ja(SHIP_HEADER_JA, h) for h in headers]
            self._init_table_b(headers_ja)
            for ship in ships:
                vals = [ship.get(h, "") for h in headers]
                self.table_b.insert("", "end", values=vals)

        # 説明エリア（sections）
        sections = detail.get("sections", {})
        for section_name, text in sections.items():
            self.explanation_text.insert("end", f"【{_ja(SECTION_NAME_JA, section_name)}】\n{text}\n\n")

    def _display_cached(self, cached_data):
        """DBキャッシュからの表示"""
        self._clear_detail()
        self.current_detail = cached_data
        self._current_source_url = cached_data.get("source_url", "")

        # 艦型名
        type_raw = cached_data.get('TYPE', '')
        type_disp = _ja(TYPE_JA, type_raw) if type_raw else ''
        self.class_name_var.set(f"{cached_data.get('CLASS_NAME', '')} {type_disp}")

        # Photo
        self.photo_images = []
        for key in ["Photo", "images", "Graphics"]:
            b64 = cached_data.get(key, "")
            if b64:
                self.photo_images.append(b64)
        self.photo_index = 0
        self._show_photo()

        # スペック（表A）
        spec_keys = [
            ("Displacement_normal", "Displacement normal"),
            ("Displacement_full", "Displacement full"),
            ("Length", "Length, m"),
            ("Breadth", "Breadth, m"),
            ("Draught", "Draught, m"),
            ("No_of_shafts", "No of shafts"),
            ("Machinery", "Machinery"),
            ("Power", "Power, h.p."),
            ("speed", "Max speed, kts"),
            ("Fuel", "Fuel, t"),
            ("Endurance", "Endurance"),
            ("Armour", "Armour"),
            ("Armament", "Armament"),
            ("Electronic_equipment", "Electronic equipment"),
            ("Complement", "Complement"),
        ]
        for col, label in spec_keys:
            val = cached_data.get(col, "")
            if val:
                self.table_a.insert("", "end", values=(_ja(SPEC_LABEL_JA, label), val))

        # 艦リスト（表B）
        ships = cached_data.get("ships", [])
        if ships:
            headers = [k for k in ships[0].keys() if k != "source_url"]
            headers_ja = [_ja(SHIP_HEADER_JA, h) for h in headers]
            self._init_table_b(headers_ja)
            for ship in ships:
                vals = [ship.get(h, "") for h in headers]
                self.table_b.insert("", "end", values=vals)

        # 説明エリア（sections）
        sections_dict = cached_data.get("sections_dict", {})
        if sections_dict:
            for section_name, text in sections_dict.items():
                self.explanation_text.insert("end", f"【{_ja(SECTION_NAME_JA, section_name)}】\n{text}\n\n")
        else:
            # 後方互換: 旧カラムから
            for col, label in [("Project_history", "Project history"), ("Modernizations", "Modernizations"), ("Naval_service", "Naval service")]:
                val = cached_data.get(col, "")
                if val:
                    self.explanation_text.insert("end", f"【{_ja(SECTION_NAME_JA, label)}】\n{val}\n\n")

    def _show_photo(self):
        if not self.photo_images:
            self.photo_label.config(image=self._placeholder, text="No Photo", compound="center")
            self._tk_photo = None
            return
        try:
            b64 = self.photo_images[self.photo_index]
            img_data = base64.b64decode(b64)
            from PIL import Image, ImageTk
            img = Image.open(io.BytesIO(img_data))
            img.thumbnail((480, 360))
            self._tk_photo = ImageTk.PhotoImage(img)
            self.photo_label.config(image=self._tk_photo, text="", compound="none")
        except ImportError:
            self.photo_label.config(image=self._placeholder, text="Pillow未インストール", compound="center")
            self._tk_photo = None
        except Exception:
            self.photo_label.config(image=self._placeholder, text="画像読み込み失敗", compound="center")
            self._tk_photo = None

    def _switch_photo(self):
        if not self.photo_images:
            return
        self.photo_index = (self.photo_index + 1) % len(self.photo_images)
        self._show_photo()

    # ------------------------------------------------------------------
    # 翻訳
    # ------------------------------------------------------------------
    def _get_current_sections(self):
        """現在表示中のsectionsを dict で返す"""
        if not self.current_detail:
            return {}
        # fetch直後のdetail
        sections = self.current_detail.get("sections", {})
        if sections:
            return sections
        # キャッシュからのdetail
        sections_dict = self.current_detail.get("sections_dict", {})
        if sections_dict:
            return sections_dict
        # 旧カラム互換
        result = {}
        for col, label in [("Project_history", "Project history"),
                           ("Modernizations", "Modernizations"),
                           ("Naval_service", "Naval service")]:
            val = self.current_detail.get(col, "")
            if val:
                result[label] = val
        return result

    def _load_cached_translations(self, source_url):
        """DBから翻訳キャッシュを読み込む。{section_name: translated_text}"""
        conn = sqlite3.connect(DB_PATH)
        try:
            rows = conn.execute(
                "SELECT section_name, translated FROM TRANSLATIONS WHERE source_url = ?",
                (source_url,)
            ).fetchall()
            return {r[0]: r[1] for r in rows}
        finally:
            conn.close()

    def _save_translations(self, source_url, translations):
        """翻訳結果をDBに保存"""
        conn = sqlite3.connect(DB_PATH)
        try:
            for section_name, text in translations.items():
                conn.execute(
                    "INSERT OR REPLACE INTO TRANSLATIONS (source_url, section_name, translated) VALUES (?, ?, ?)",
                    (source_url, section_name, text)
                )
            conn.commit()
        finally:
            conn.close()

    def _on_translate(self):
        """翻訳ボタン押下"""
        if self._showing_translated:
            # 原文に戻す
            self._show_sections_text(self._get_current_sections())
            self._showing_translated = False
            self.translate_btn.config(text="日本語に翻訳")
            self.translate_status_var.set("")
            return

        source_url = self._current_source_url
        sections = self._get_current_sections()
        if not sections:
            self.translate_status_var.set("翻訳対象なし")
            return

        # DBキャッシュ確認
        cached = self._load_cached_translations(source_url) if source_url else {}
        all_cached = source_url and all(s in cached for s in sections)

        if all_cached:
            self._show_sections_text(cached)
            self._showing_translated = True
            self.translate_btn.config(text="原文に戻す")
            self.translate_status_var.set("翻訳済み（キャッシュ）")
            return

        # バックグラウンドで翻訳
        self.translate_btn.config(state="disabled")
        self.translate_status_var.set("翻訳中...")

        def task():
            try:
                translator = GoogleTranslator(source='en', target='ja')
                translated = dict(cached)  # キャッシュ済み分を引き継ぐ
                for section_name, text in sections.items():
                    if section_name in translated:
                        continue
                    # Google翻訳は5000文字制限があるので分割
                    translated[section_name] = self._translate_long_text(translator, text)
                if source_url:
                    self._save_translations(source_url, translated)
                self.root.after(0, self._on_translate_done, translated)
            except Exception as e:
                self.root.after(0, self._on_translate_error, str(e))

        threading.Thread(target=task, daemon=True).start()

    def _translate_long_text(self, translator, text):
        """5000文字制限を考慮して分割翻訳"""
        max_len = 4500
        if len(text) <= max_len:
            return translator.translate(text)
        # 段落単位で分割
        paragraphs = text.split('\n')
        chunks = []
        current_chunk = ""
        for p in paragraphs:
            if len(current_chunk) + len(p) + 1 > max_len and current_chunk:
                chunks.append(current_chunk)
                current_chunk = p
            else:
                current_chunk = current_chunk + '\n' + p if current_chunk else p
        if current_chunk:
            chunks.append(current_chunk)
        translated_parts = [translator.translate(c) for c in chunks]
        return '\n'.join(translated_parts)

    def _on_translate_done(self, translated):
        self._show_sections_text(translated)
        self._showing_translated = True
        self.translate_btn.config(text="原文に戻す", state="normal")
        self.translate_status_var.set("翻訳完了")

    def _on_translate_error(self, msg):
        self.translate_btn.config(state="normal")
        self.translate_status_var.set(f"翻訳エラー: {msg}")

    def _show_sections_text(self, sections):
        """説明エリアにsectionsを表示"""
        self.explanation_text.delete("1.0", "end")
        for section_name, text in sections.items():
            self.explanation_text.insert("end", f"【{_ja(SECTION_NAME_JA, section_name)}】\n{text}\n\n")


def main():
    root = tk.Tk()
    NavalApp(root)
    root.mainloop()


def cli_fetch_all():
    """全サイト一括取得をCLIから実行"""
    import sys
    import time

    print("=== NECS - 全サイト一括取得 ===")
    fetcher = NavypediaFetcher(db_path=DB_PATH)

    start = time.time()

    def on_progress(done, total):
        pct = (done / total * 100) if total else 0
        bar = "#" * int(pct // 2) + "-" * (50 - int(pct // 2))
        print(f"\r  [{bar}] {done}/{total} ({pct:.1f}%)", end="", flush=True)

    def on_country_start(name, idx, n):
        print(f"\n[{idx + 1}/{n}] {name} を取得中...")

    try:
        fetcher.fetch_all_countries(
            on_progress=on_progress,
            on_country_start=on_country_start,
        )
    except KeyboardInterrupt:
        print("\n\n中断されました。")
        sys.exit(1)

    elapsed = time.time() - start
    m, s = divmod(int(elapsed), 60)
    print(f"\n\n=== 完了 ({m}分{s}秒) ===")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--fetch-all":
        cli_fetch_all()
    else:
        main()
