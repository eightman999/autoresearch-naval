# NSDB 網羅性監査レポート (2026-10-06)

調査のみ。NSDB (`nsdb/NSDB.db` → `/Users/eightman/dev/data/nsdb/NSDB.db`) は `mode=ro` で読み取り、一切書き込んでいない (mtime 2026-06-24 のまま)。既存ファイルの編集・コミットなし。直す作業は次段階。

機械可読な結果は `docs/nsdb_audit/*.json`、再現スクリプトは `tools/nsdb_audit_*.py`。

## 1. 要約

| 観点 | 結果 |
|---|---|
| ページ単位の網羅 | 元サイト(現行 navypedia.org)の艦級ページは **10,928**。NSDB はそのうち **10,716 (98.06%)** を持つ。**取りこぼし 212 件 (1.94%)**、NSDB にだけある 1 件 (Jamaica の国ページを艦級と誤取得した行)。 |
| 取りこぼしの原因 | 197 件 = 艦種ページのラベルが取得時の艦種リストに無かった疑い (沿岸警備・警察・準軍事・揚陸艇・河川艇系)。6 件 = URL に空白 `%20` (現行は 404、ミラーには在る)。4 件 = サイト側の壊れた `file:///` リンク (Sarawak、現行・ミラーとも 404)。1 件 = 現行が HTTP 500。4 件 = 原因不明。 |
| 国単位 | COUNTRIES 181 のうち **CLASS が 0 件の国は 8** (Falkland, Malawi, Marshall, Micronesia, Montserrat, Sarawak, Solomon, Vanuatu)。8 国とも元サイトには艦級が計 33 件ある (Sarawak の 4 件だけはリンク切れで取得不能)。 |
| **Armour** | CLASS.Armour は **11,144 行すべて空**。原因は**パース(列写像)漏れ**。元サイトの技術表には `Armour, mm` 行があり、標本で装甲行を持つ 4/4 ページが DB で空。装甲行を持つ艦級は約 **1,100 (約 10%)**。`sections` には装甲の散文 (`Ship protection` 等) が約 1,025 艦級分あるが、数値表 (Armour 行) は無い。 |
| 項目単位 | 技術表の数値項目は標本で概ね正確 (非 Armour の値 354 セル中 351 が一致)。ただし (a) `Armour` 欠落、(b) 潜水艦の `Displacement standard` と `normal` が同じ列に写像され片方が消える、(c) `Diving depth operational` / `Military load` / `Data variant` など**未定義ラベルは列にも sections にも残らない**。テキスト節は**標本の 37.5% (36/96) で語が連結** (`R-2andR-92were`, `I611.10.1941` など)、表形式の節 (`Air group`, `100 - 1000 t displacement/ BRT tonnage vessels`) は**別節の文章が入っている** (標本 6/112)。 |
| 個艦 (SHIPS) | **SHIPS 108,778 行のうち 46,903 行 (43.1%) は同一 source_url 内の完全重複** (実質 61,875 行)。1 クラス `ru_cf_r141` だけで 30,902 行余分。重複は html.parser が閉じ忘れ `<tr>` をネストして解釈し、行ごとに後続行を再取得することが原因と推定。さらに **2,648 クラス (24.7%) は個艦が 1 行も取れず**、全項目空の行だけが残る (行頭 `Names/Builders/Completed/Losses/Transfers/Discarding` の群れ要約表。実ページ 10/10 件で確認)。標準表の艦自体の取りこぼしは標本で 0.08% (1/1,225)。竣工日 `Comp` は標本の 90% の行にあるが、SHIPS に列が無く常に捨てられる。 |
| CLASS 重複・品質 | CLASS 11,144 行 = 実ページ 10,717 + **重複 418 行** + source_url が NULL の 9 行。重複の 410 行は 2026-06-23 のミラー取得 417 行 (Abkhazia〜Azerbaijan) で、CLASS_NAME/TYPE が崩れ、Project_history 等が 100% 空と**元の行より品質が低い**。CLASS_NAME は 21.6% (2,401 行) で `YAMATO1942) Class` のように括弧が壊れている。 |
| 元サイトの正 | **現行 `https://www.navypedia.org` を正とすべき**。ミラー `myownonpmirror.com` は 2021-01-14 の HTTrack スナップショットで、構造が劣り内容も古い (§3)。現行 `fetcher.py` は**どちらに対しても正しく動かない** (§6)。 |
| アクセス | 約 2,120 リクエスト (目安 3,000 以内、標本調査への切替は不要)、所要は実時間 約 50 分 (実働約 40 分)。各ホスト 1 req/s 以下。robots.txt は禁止なし。Cloudflare・CAPTCHA・ログイン要求・429/403 は一度も出ず。 |

## 2. 方法

- 参照: `nsdb/fetcher.py` (現行)、`nsdb/scraper.py` (旧)、`nsdb/README.md`、`SKILL.md`、`SHIP_TYPE_LIST.csv`。
- **A (DB 内部)** `tools/nsdb_audit_internal.py` → `docs/nsdb_audit/internal.json`。URL は `canon_url()` でホスト・拡張子 (`.htm`/`.html`)・`..` を正規化して突き合わせ。
- **B-1 (一覧クロール)** `tools/nsdb_audit_crawl.py`。国一覧 → 各国ページ → 艦種ページ (艦級リンク一覧) だけを取得。ミラーと現行の両方を完全にたどった (各 1,000 件弱)。艦級ページ本体は標本以外取得していない。HTML は scratchpad にキャッシュ (17 MB、上限 500 MB 内)。
- **B-2 (突き合わせ)** `tools/nsdb_audit_compare.py` → `site_vs_nsdb.json`、`site_classes_live.json`、`site_classes_mirror.json`。
- **B-3 (標本)** `tools/nsdb_audit_sample.py` + `nsdb_audit_pages.py` + `nsdb_audit_summarize.py`。40 件を選び (層化ランダム 21 + 異常候補・取りこぼし候補 19)、現行ページとミラーページの両方を取得。現行ページを正規表現で分解した「正解」と比較して、**DB (2026-02 取得行)**、**fetcher.py 現行ロジック × 現行ページ**、**fetcher.py 現行ロジック × ミラーページ** の 3 者を判定。fetcher はキャッシュ済み HTML に対しオフラインで実行 (`__init__` を呼ばず DB を作らない)。
  - 40 件のうち取得できたのは 38 (現行で 404 が 2 件)。DB に在る 34 件で DB 比較、DB に無い 4 件で取りこぼし側の確認。
  - 標本は小さい (層化ランダム 21 ページ)。パーセンテージは目安で、特例ページ (異常候補) は偏りを含むため、率は「層化ランダムのみ」も併記した。
- 追加確認: 全項目空の SHIPS しか持たない 2,648 ページから 10 件、取りこぼし 212 件から 12 件の実ページを確認 (`ship_table_variants.json`、`request_summary.json`)。

### アクセス状況 (`request_summary.json`)

| 項目 | 値 |
|---|---|
| リクエスト総数 | ログ上 2,109 + robots.txt 3 + ログ整備前の初期確認 約 8 = **約 2,120** (ミラー 1,053 / 現行 1,056 + α) |
| 所要 | 実時間 約 50 分 (クロール 約 30 分 + 標本 約 3 分 + 確認 + 作業の合間。実働約 40 分)。2 サイト並列、ホストごとは 1 req/s 以下。平均応答 ミラー 0.16s / 現行 0.82s |
| ステータス | 200 が 2,100、404 が 8 (標本の `%20` 6 件 + Sarawak 2)、500 が 1 (`brit_cv1_furious.htm`)。連続エラー停止の発動なし |
| robots.txt | ミラー: 404 (制限なし)。www/apex navypedia.org: `User-agent: * / Disallow:` (全許可) |
| ブロック | Cloudflare チャレンジ、CAPTCHA、ログイン要求、429/403 は無し。ページ内の `login`/`register` リンクは辿っていない |
| User-Agent | `nsdb-completeness-audit/1.0 (personal research; read-only; <=1 req/s)` |

## 3. 元サイトとミラーの関係

| | ミラー `myownonpmirror.com` | 現行 `www.navypedia.org` |
|---|---|---|
| 正体 | nginx/Ubuntu の第三者サーバ。HTML に `Mirrored from www.old-navypedia.org ... HTTrack ... Thu, 14 Jan 2021` | 本家。`© 2007-2026`、UTF-8、`th`/`td`・`h2` で構造化 |
| 国・艦級の集合 | 国 180 (+Jamaica 特例)、艦級 **10,928** | 国 180 (+Jamaica 特例)、艦級 **10,928** |
| 差分 | UK の `-2` ページ 4 件がミラーだけ | Sarawak の 4 件が現行だけ (ただしリンク切れ) |
| 技術表 | `th` が無く `td` + FrontPage の `span` で、数字が分割される (`5 6 0`、`36.9` が `36`+`.`+`9`) | `table.blue` の `th`/`td` |
| 節 (Project history 等) | `h2` が無く、本文中の太字見出し。fetcher では **100% 取得不能** | `h2` + 本文 |
| 画像 | fetcher が拾えず **Photo/images/Graphics 100% 空** (NSDB_new.db) | `css-adaptive` 画像 |
| 内容の新しさ | 旧い。標本で `Electronic equipment` 行が 8 件中 7 件で無い。NSDB_new.db(ミラー取得) の Electronic_equipment は 4,928/10,921 (45%) で、現行由来の NSDB の 53% より少ない | 最新 |
| robots.txt | 無し (404) | 全許可 |

結論: 艦級の URL 集合は同一なので「何があるか」の確認にはどちらも使えるが、**取得元の正は現行 navypedia.org**。ミラー経由の取得 (NSDB_new.db、NSDB の 2026-06-23 の 417 行) は情報が少なく壊れている。**現行 navypedia.org の `file:///C:/seawar/` リンク (Sarawak) と `%20` のリンク (6 件) はサイト側の不具合**。

## 4. A. DB 内部の点検

### 4.1 構成・突き合わせ

- 取得履歴 (`fetch_runs`): 2026-02-05 11:25〜15:58 (UTC) に 10,718 行 (Cuba・Japan・Malaysia の先行試行 + Abkhazia〜Zimbabwe の A→Z 通し)。2026-06-23 16:09〜16:10 に**ミラーから 11 カ国 417 行を追加** (Azerbaijan で止まる。途中で止まった国は 2/5 の通し取得内にはなし)。source_url の内訳: `https://www.navypedia.org/…htm` 10,596 行、同 `….html` (UK) 122 行、ミラー 417 行、NULL 9 行。
- CACHE_CLASS_URLS 417 件は全て CLASS に行がある (15 件は SHIPS 無し)。CACHE_SHIP_TYPES 70 件は Abkhazia〜Azerbaijan 11 カ国 (ミラー) と Japan (現行) だけ。**CACHE は全体の発見済み URL 集合としては使えない**。
- CLASS に行があるのに SHIPS が 0 件の艦級: **144 件 (1.3%)** (Russia 17、Germany 12、Italy 9、UK 9 ほか。`WWI/WWII Class` の群れページが中心。標本 3/3 で、艦は表ではなく節の文章中にある)。SHIPS の source_url が CLASS に無いもの: 0。SHIPS.CLASS が CLASS に無いもの: 0。
- 国別・艦種別の件数: `internal.json` の `countries_table` (国 181 件) / `by_type_top50`。TYPE の異なる値は **1,422 通り** (うち 728 は 1 艦級のみ。`river-gunboats-of` のように切れた値あり)。

### 4.2 COUNTRIES のうち CLASS が 0 件の国

Falkland (元サイトに 12 艦級)、Malawi (1)、Marshall (3)、Micronesia (3)、Montserrat (3)、Sarawak (4。リンク切れ)、Solomon (4)、Vanuatu (3)。いずれも艦種ページが 1 つだけの国 (沿岸警備・警察・陸軍系の艦種ラベル) で、§5.2 の原因に当たる。

### 4.3 列ごとの空率 (全 11,144 行)

| 列 | 空率 | 列 | 空率 |
|---|---|---|---|
| Armour | **100.0%** | Electronic_equipment | 46.8% |
| Displacement_full | 35.2% | Photo / Graphics | 45.9% |
| Endurance | 35.9% | images | 43.5% |
| Displacement_normal | 18.5% | Complement | 12.5% |
| Power | 10.3% | Modernizations / Naval_service / Project_history | 8.0% / 7.6% / 5.1% |
| Length / Breadth / Draught | 4.4 / 6.0 / 7.5% | TYPE / CLASS_NAME | 1.4% / 0.04% |

標本で確認すると、Armour 以外の空は**元ページ自体が空**のものが大半 (標本の値入り 354 セル中 DB が正しいもの 351)。年代別の差は実態を反映している:

| 年代 | n | Displacement_normal 空 | Electronic_equipment 空 | Complement 空 | Photo 空 |
|---|---|---|---|---|---|
| <1860 | 484 | 1.0% | 100% | 11.4% | 97.7% |
| 1860–1899 | 1,404 | 2.8% | 99.9% | 12.0% | 54.8% |
| 1900–1919 | 1,412 | 3.1% | 96.5% | 6.2% | 43.7% |
| 1920–1945 | 2,312 | 8.1% | 42.3% | 6.3% | 42.9% |
| 1946–1969 | 1,313 | 14.8% | 9.5% | 7.1% | 34.7% |
| 1970–1999 | 2,095 | 29.6% | 5.8% | 9.3% | 30.2% |
| 2000+ | 1,080 | 44.5% | 2.6% | 18.9% | 42.0% |
| 年代不明 (SHIPS 無し) | 1,044 | 47.4% | 68.2% | 42.2% | 69.0% |

国別の偏りの多くは**ミラー重複行**由来: Australia は Project_history の 51% が空 (ミラー行 102 が Project_history 全空)。国別の全表は `internal.json` の `empty_rate_by_country`。

### 4.4 Armour と `sections`

- `Armour` は 0/11,144。`sections` (JSON、286 種類のキー) に装甲数値は無い。
- `sections` の構成: Project history 10,571 / Naval service 10,285 / Modernizations 10,250 / **Ship protection 887 / Protection 118** (+異形 20 前後) / Aircraft facilities 118 / Air group 114 / `100 - 1000 t displacement/ BRT tonnage vessels` 473 / `Fate Notes` 9。装甲の散文を持つ艦級は **1,029 行 (重複を除くと 1,025 ページ)**。固定列に入っていない情報は Ship protection・Aircraft facilities・Air group・Protection・`100 - 1000 t …`・Fate Notes。
- **番号付き見出し** `Modernizations 9.` などは 142 艦級。見出しの完全一致で固定列に入るため **Modernizations 列が空**になり、`sections` にだけ残る。
- 種別見出しが節として混入したもの (`TORPEDO SHIPS`、`COASTAL FORCES` など大文字見出し・空キー) が 305 艦級。
- 装甲らしい語が Armament/Machinery に混入している例はほぼ無し (2 件、潜水艦の `minelaying belt` で別意味)。

### 4.5 重複・壊れた行

| 項目 | 件数 |
|---|---|
| CLASS の source_url 重複 | 418 行 (ミラー+現行 410、現行同士 4 = Malaysia の二重取得や `..` を含む URL、`.htm`+`.html` 4) |
| CLASS の source_url が NULL | 9 行 (旧試験行。Abkhazia 5 艦級ぶんなど) |
| CLASS_NAME 空 | 5 行 (すべてミラー行) |
| TYPE 空 | 158 行 (ミラー行 118 + 現行行 40。`DIEZ Y SIETE DE OCTUBRE` など艦種語が無い見出し) |
| CLASS_NAME の括弧崩れ (`…1942) Class`) | **2,401 行 (21.6%)**。現行ページの見出しは `YAMATO battleships (1941 - 1942)` |
| TYPE に海軍名・国名が混入 (`coast-guard-patrol-vessel` 等) | 182 行 |
| 15 の技術列がすべて空 | 312 行 (2.8%)。技術表が無い群れページ |
| 3 つの節がすべて空 | 442 行 (4.0%) |
| SHIPS: 同一 source_url の完全重複 | **46,903 行 (43.1%)**。重複のあるクラス 223。上位 10 クラスで 41,384 行 |
| SHIPS: NAME 空 | 54,608 行 (50.2%)。重複を除いた実質行では 8,039 行 (13.0%) で、名前の無い番号艇 |
| SHIPS: 全行が id 6 列とも空のクラス | **2,648 クラス (24.7%)** |
| SHIPS: ミラーと現行の両方から取り込まれたクラス | 296 クラス (2,226 行) |
| SHIPS.SID | 11 行のみ非 NULL |

ミラー 417 行の「悪化」: 同じ艦級の現行行と比べ、CLASS_NAME が違うもの 266、TYPE が違うもの 261、Project_history を失ったもの 406 (Modernizations 387、Naval_service 394)、TYPE 空 116、Electronic_equipment 空 23。例: `abk_ls_msb12` → `MSB-12 Small Landing Ships (1962-1965/1992) - Abkhazian Navy (Abkhazia) Class`。ミラー行だけの艦級は 7。

### 4.6 fetched_at の分布

2026-02-05 (10,718 行) と 2026-06-23 (417 行) のみ。2/5 の通しは A→Z で Zimbabwe まで完走し、途中で止まった国はない。6/23 は Azerbaijan で止まった部分実行 (76 秒) で、これが重複・品質低下の混入源。

## 5. B. 元サイトとの照合

### 5.1 ページ集合 (艦級 URL)

| 集合 | 件数 |
|---|---|
| 現行サイト 艦級 | 10,928 (艦種ページ 815) |
| ミラー 艦級 | 10,928 (艦種ページ 822) |
| NSDB (正規化後の distinct) | 10,717 |
| 現行 ∩ NSDB | 10,716 (**98.06%**) |
| **現行にあって NSDB に無い** | **212 (1.94%)** |
| NSDB にあって現行に無い | 1 (`jam_cg`: Jamaica の国ページを艦級として取得した行) |

NSDB 側の旧 URL 形式 (`/ships/germany_east/../germany/ger_cf_sassnitz.htm`) は `..` を正規化して一致させた。Jamaica は「index → jam_cg (国ページ相当) → jam_cgg (艦種ページ) → 艦級」と 1 段深い特例で、NSDB の `jam_cg` 行はこの構造の誤取得。

国別の取りこぼし (主なもの、全表は `site_vs_nsdb.json` の `by_country`):

| 国 | 元サイト | NSDB | 取りこぼし |
|---|---|---|---|
| Italy | 471 | 399 | 72 (全て `it_cg` PARAMILITARY SERVICES) |
| Denmark | 141 | 119 | 22 (`dk_cg` FISHERY PROTECTION SERVICE AND HOME GUARD) |
| Poland | 91 | 76 | 15 (`pl_cg` BOARD GUARD CRAFT) |
| Falklands | 12 | 0 | 12 (`falk_cg` FISHERIES PATROL) |
| Yugoslavia | 73 | 62 | 11 (`yu_ls` AMPHIBIOUS FORCES) |
| Hungary | 17 | 7 | 10 (RIVER BOATS / RIVER FIGHTING SHIPS) |
| Czechoslovakia | 8 | 2 | 6 |
| Dominican Rep. | 39 | 33 | 6 (AMPHIBIOUS CRAFT) |
| Portugal | 110 | 105 | 5 |
| その他 29 カ国 | | | 計 53 (各 1〜4) |

艦種ラベル別 (原因 197 件の内訳): PARAMILITARY SERVICES 72、AMPHIBIOUS CRAFT 31、FISHERY PROTECTION SERVICE AND HOME GUARD 22、BOARD GUARD CRAFT 15、RIVER BOATS 12、FISHERIES PATROL 12、AMPHIBIOUS FORCES 11、POLICE SERVICE 4、RIVER FIGHTING SHIPS 3、MARITIME AUTHORITY 3、DIVISION OF SURVEILLANCE… 3、POLICE FORCE 3、MARINE POLICE 3、RIVER SHIPS 1、ARMY 1、CONVERTED MERCHANT SHIPS 1。

### 5.2 取りこぼし 212 件の原因

| 原因 | 件数 | 根拠・例 |
|---|---|---|
| 艦種ページのラベルが取得時の艦種リストに無かった疑い | 197 | 該当ラベルの艦種ページは (1 ページを除き) **艦級が全滅**し、それ以外のラベルの艦種ページは全て取れている (AMPHIBIOUS CRAFT は 14 ページ中 13 が全滅、残る 1 は別理由)。オフライン再現では**現行 `SHIP_TYPE_LIST.csv` の絞り込みで 10,928/10,928 を発見できる** (ミラー側も同数) ので、現行 CSV には当該ラベルが含まれる。2026-02 取得時の CSV の中身は git 履歴に無く未検証 (推定) |
| URL に空白 `%20` | 6 | 現行は **404**、ミラーは **200** (標本で確認)。例 `baha_cf_yellow%20elder.htm`、`ger_ss_delphin%202.htm`。2/5 の取得は例外を握りつぶす (`except Exception: pass`) ため黙って欠落した可能性 |
| サイト側の壊れた `file:///C:/seawar/…` リンク | 4 | Sarawak の 4 艦級。現行・ミラーとも 404。サイトのリンク切れで取得不能 |
| 現行が HTTP 500 | 1 | `uk/brit_cv1_furious.htm` (ミラーには在る) |
| 原因不明 | 4 | `djibouti/dji_ls_dague`、`romania/ro_cm_amc2`、`ro_cm_ams2`、`ro_cm_es2`。現行・ミラーとも 200 で、現行 fetcher のオフライン実行でも例外なし。取得時の一時エラーの疑い |

### 5.3 項目単位 (標本)

`sample_pages.json` / `sample_summary.json`。判定は「正解 (現行ページ) との英数字正規化一致」。DB に在る 34 ページ (層化ランダム 21 + 異常候補 13)。

**技術表の項目 (非 Armour は 354 セル)**

| 結果 | DB | 内訳 |
|---|---|---|
| 一致 | 351 | |
| **欠落** | 4 | **すべて Armour** (装甲行のあるページ 4/4) |
| 値が違う | 3 | 潜水艦 `Displacement standard` が `Displacement normal` に上書きされる (下記) |
| 切れた値 | 0 | |

層化ランダム 21 ページだけでも同傾向 (一致 230、欠落 2 = Armour、不一致 2)。

- **ラベル衝突**: `Displacement standard, t` と `Displacement normal, t` は両方とも `Displacement_normal` 列へ写像される。潜水艦ページは両方持つため後勝ちで標準排水量が消える (`jap_ss_kd4`: 標準 1635 が消え `1720 / 2300` だけ残る。標本の潜水艦 6 ページ中 3 ページで値が消失、他 3 ページは標準が元から空)。潜水艦 (TYPE に submarine) は 912 艦級。
- **未定義ラベル (列にも sections にも残らない)**: `Diving depth operational, m` (標本の潜水艦 6/6 ページ。潜水艦 912 艦級が該当する見込み)、`Military load` (揚陸艇、標本 3 ページ。揚陸系 TYPE は 808 艦級)、`Data variant` (`ger_mb_kfk2`)。標本が小さく、ラベルの全種類は未確定。
- 改装後の別表・複数艦級の別表: 標本 38 ページに**複数の技術表は 0 件**。改装後の諸元は `Modernizations` 節の散文にしか無く、複数艦 (Yamato/Musashi) は 1 つの表に `Name: …` の行内で併記される。

**テキスト節 (112 節)**

| 結果 | DB 固定列 | DB (固定列+sections) |
|---|---|---|
| 完全一致 | 60 | 66 |
| **語の連結 (空白が消える)** | 36 (37.5%) | 40 |
| 固定列に無い (Ship protection 等) | 16 | — |
| **別節の文章が入っている** | — | 6 |

- 連結の例: `jap_bb_yamato` Naval service `Musashi29.3.1944 was damaged by a torpedo from American submarineTunny` (正: `Musashi 29.3.1944 … submarine Tunny`)。`jap_ss_kd4` `I611.10.1941` (正: `I61 1.10.1941`)。ru_cf_r141 `R-2andR-92were lost`。標本ページの 23/34 (68%) が少なくとも 1 節で連結。層化ランダムのみでも 18/66 節 (27%)。原因は節を `get_text()` (区切り無し) で取るため `<i>` 等の境界で空白が落ちること。**下流 (shipknow_parse 等) が日付・艦名で分割するなら影響しうる。**
- 別節の文章: `in_cv_vikrant` の `Air group` は年別の表だが DB には `Aircraft facilities` の文章が入り、`fi_cm_pg2` の `100 - 1000 t displacement/ BRT tonnage vessels` は艦の一覧なのに DB は `Former trawlers.` (次節の本文)。`h2.find_next('p')` が表を飛ばして次の `<p>` を拾うため。該当キーは `Air group` 114 + `100 - 1000 t…` 473 = 587 艦級 (標本 6/6 が誤り)。
- 切れた節 (先頭 1 段落のみ) は標本で 0 件。現行の節は 1 つの `<p>` + `<br>` で構成される。
- 番号付き `Modernizations N.`: 標本 2 ページ。固定列が空で `sections` にだけ残る (142 艦級)。

**個艦 (SHIPS)** (標本 38 ページ、正解 1,231 行。DB 比較は 34 ページ 1,225 行)

| 項目 | 値 |
|---|---|
| DB 行数 (重複込み) | 37,633 (ru_cf_r141 31,180、ch_cf_2201 3,403、ger_mb_kfk2 2,828 など 5 ページが重複) |
| DB の distinct 行 | 1,235 |
| 正解にあって DB に無い | **1 行 (0.08%)** (ru_cf_r141 の `Р-73`。この表は 9 列目 `Modification` がある) |
| DB にあって正解に無い (列ずれのゴミ行) | 13 行 (ger_mb_kfk2) |
| 竣工日 `Comp`/`Comm` を持つ正解行 | 1,111/1,231 (90%)。**SHIPS に列が無く常に捨てられる** |
| 標準でない表ヘッダ | `Names/Builders/Completed|Commissioned/Losses/Transfers/Discarding` 2 ページ、`Modification` 列付き 2 ページ、`Launched // Comm.`+`Tonnage…` 1 ページ |

層化ランダムのみ: 正解 109 行に対し DB 178 行 (distinct 109)、欠落 0。つまり標準表の艦そのものは取れているが、重複と非標準表が問題。

- Fate の理由は `[0-9\W_]` 除去で空白が消え `completedascarrier`、`BUincomplete` のように連結 (例 `jap_bb_yamato`)。
- 画像: DB は節の**最初の 1 枚だけ**。標本の Standard scale images で 5/19 ページ、Graphics で 3/25 ページが複数枚。

### 5.4 fetcher.py 現行ロジックのオフライン再現 (3 者比較)

| | DB (2026-02 取得、34 ページ) | fetcher 現行 × **現行ページ** (38 ページ) | fetcher 現行 × **ミラー** (38 ページ) |
|---|---|---|---|
| 技術表 (値入りセル。DB 358 / fetcher 407) | 351 一致 / Armour 4 欠落 / 3 不一致 | **404 一致 (Armour 4/4 取得)** / 3 不一致 | 392 一致 + 4 数字分割 / 欠落 7 (`Electronic equipment`・`Endurance` ラベル差) / 1 部分一致 / 3 不一致 |
| テキスト節 (DB 112 / fetcher 123) | 66 一致 + 40 連結 + 6 別節混入 | 101 一致 + 16 余分な空白 + 6 別節混入 | **123/123 欠落** |
| SHIPS | 重複込みで取得 | **33/33 ページで 0 行** (閉じ忘れ `<tr>` のネストで列数判定に失敗) | 1,228 行 (欠落 3、群れ表 2 ページ 0 行) |
| class_name | 括弧崩れ | `YAMATO (1941 Class` (見出しが `<a class=reds2>` でなく `<div>` のため後続処理に落ちる) | `YAMATO Class` / `battleships` で概ね正しいが `LSS"- Class` など崩れあり |
| 画像 | 最初の 1 枚 | 取得可 | **0%** (NSDB_new.db) |

ミラー経由で別に取得済みの試験 DB `nsdb/NSDB_new.db` (2026-07-10、10,921 艦級、5 分) は、Armour 1,102 件を取れる代わりに Project_history 0 件・Photo 0 件。本番に使える品質ではない。

## 6. Armour の件 (結論)

- **取得漏れではなくパース漏れ**。元の現行ページには `Armour, mm` 行があり (例: `https://www.navypedia.org/ships/japan/jap_bb_yamato.htm` の `belt: 410, bulkheads: 300, …`)、2026-02 取得の DB では空。標本の装甲行ありページ 4/4 (`jap_bb_yamato`、`us_cv_independence`、`fr_bb_rochambeau`、`it_cr_libia`) で DB 欠落、現行 fetcher のロジックでは 4/4 取得できる。
- 原因の推定: 2026-02 取得時の版は列写像が `Armor` 系の綴りだけ (旧 `scraper.py` は `Armor` のみ)。現行 `SPEC_TO_COLUMN` は `Armour, mm` を持つ。
- 装甲行を持つ艦級: ミラー経由の試験取得で **1,102 件 (10.1%)**。1,085 件が NSDB の艦級 (残りは未取得の艦級)。標本でも 4/34 (12%)。国別: UK 206、France 122、Russia 121、Germany 89、Japan 87、USA 86、Italy 55。一覧は `docs/nsdb_audit/armour_targets.json`。
- `sections` の `Ship protection`/`Protection` 約 1,025 艦級は散文のみ。数値表 (Armour 行) の代替にならず、うち 995 件は Armour 行も持つ。

## 7. 原因の推定 (まとめ)

| # | 症状 | 原因 | 確度 |
|---|---|---|---|
| 1 | Armour 全空 | 取得時の列写像が `Armour, mm` 非対応 | 高 (標本で現行ロジックなら取得可を確認) |
| 2 | 艦級 212 件欠落 | 艦種リスト (CSV) 不足 197 / `%20` 6 / リンク切れ 4 / 500 1 / 不明 4 | CSV 不足は中 (CSV の旧版が不明)。他は高 |
| 3 | SHIPS 重複 43% | `html.parser` が閉じ忘れ `<tr>` をネストし、行ごとに後続行も取る + 全体を 8 列幅で切り出す処理 | 高 (`ru_cf_r141` の HTML は `<tr` 296 個に対し `</tr` が 14 個。html.parser では表の直下 `tr` が 1 個になり、先頭 `tr` から 2,538 セルが取れる。278 行の表が 31,180 行に膨らむ規模は N(N+1)/2 = 38,781 前後で、ネストによる二次的な重複で説明できる) |
| 4 | 2,648 クラスの個艦が空 | 非標準の群れ要約表 (`Names/Builders/…`) を `Name`・`Builder` ヘッダ前提のコードが扱えない | 高 (10/10 ページ確認) |
| 5 | 節の語連結・別節混入 | `get_text` の区切り無し、`find_next('p')` が表節を飛ばす | 高 (標本で再現) |
| 6 | 417 行の重複・低品質 | 2026-06-23 の途中停止したミラー取得 (UNIQUE 制約が既存 DB に無く二重挿入) | 高 |
| 7 | CLASS_NAME 括弧崩れ 21.6% | 見出しの正規表現分解の失敗 | 高 |
| 8 | 潜水艦の標準排水量消失 | 2 ラベルを同一列に写像 | 高 |
| 9 | 未定義ラベル (潜水深度ほか) | 写像表に無い行を `sections` にも保存しない | 高 |
| 10 | 竣工日 Comp 喪失 | SHIPS に列が無い | 高 |

## 8. 直すとしたら

方針: **取得元を現行 navypedia.org に統一し、1 回取得 → HTML をディスクに保存 → オフラインで何度でもパース**する形に作り直す。NSDB 本体 (共有正本) は列追加・差分適用で、バックアップを取って承認の上で更新する。以下は提案で未実施。

| 段 | 内容 | 規模 |
|---|---|---|
| 0. DB だけでできる整理 (ネットワーク無し) | SHIPS の完全重複除去 (`DISTINCT`) で約 4.7 万行削減。ミラー由来 CLASS 417 行 (+SHIPS 1,014 行) の除去または現行行への統合 (ミラーだけの 7 艦級は現行 URL で再取得)。source_url NULL の 9 行の整理。複数日取得の整合確認 | SQL 数十行、半日未満。**復元できるよう .db のバックアップ必須** |
| 1. 取りこぼし 212 件の補完 | 艦種ページの発見を「ラベル一致」から「国ページ配下の非 index リンク全て」に変更 (現行 CSV でも発見できるが、新しい艦種ラベルの再発を防ぐ)。`%20`・500 はミラーまたはリトライで補う。Sarawak 4 件は取得不能として記録 | 約 250 リクエスト、約 5 分 |
| 2. パーサ修正 | (a) 取得元を現行 navypedia.org に。(b) 表は正規表現または `lxml`/`html5lib` で `<tr>` ごとに分割 (ネスト回避)。(c) 技術表: Armour 反映、`standard`/`normal`、`Diving depth`、`Military load`、`Data variant` などを `specs_extra` (JSON) 列に保存して**未定義ラベルを捨てない**。(d) 節: h2〜次の h2 全文、タグ境界に空白、番号付き `Modernizations N.` を正規化、表形式の節はテキスト化。(e) SHIPS: 見出し名で写像 (`No`/`Name` 逆順、`Comp`/`Comm`/`Completed`、`Modification`)、`Comp` 列追加、群れ要約表は別テーブル (または JSON) へ。(f) 見出しは `div.reds2` から CLASS_NAME・TYPE・WHEN を再導出。(g) 例外を握りつぶさず失敗 URL を表に記録 | 実装・テストで 1〜2 日。標本 40 件 (`tools/nsdb_audit_sample.py`) を回帰テストに流用可 |
| 3. 全面再取得 | 10,928 ページ + 艦種ページ・国ページ 約 1,000 ページ。HTML のみ (画像なし) で 1 req/s なら約 **3.3 時間**、キャッシュ約 0.25 GB。画像は既存 DB (Photo/images/Graphics 計約 1.8 万枚) を流用すれば不要 (取り直すと +約 5 時間) | ほぼ待ち時間 |
| 3'. 最小ルート | Armour だけ: `armour_targets.json` の約 1,100 ページのみ再取得 (約 20 分) し、Armour 列だけ更新 | 小。ただし語連結・SHIPS 問題は残る |
| 4. 差分検証・適用 | 新旧 CLASS/SHIPS の差分レポート → 承認 → 列追加で適用 (下流の `shipknow_*`/`navspec_*` は CLASS_NAME/節の形式に依存しうるので、既存列は上書きでなく別列併設で影響確認) | 半日 |

注意:
- 下流 (`tools/shipknow_extract.py`、`navspec_*` の SQL) は CLASS/SHIPS を `ORDER BY rowid` で全件読むため、重複行と連結テキストの影響を受けている可能性がある (影響範囲の調査は今回対象外)。
- `nsdb/README.md` は DB を `nvpda-db/NSDB.db` と記載、`SKILL.md` は 10,727 艦級と記載しており、現状 (11,144 行 / 10,717 ページ) と食い違う。

## 9. 未検証・限界

- 標本は 40 件 (取得 38、比較 34、層化ランダム 21)。項目別の率は目安。未定義ラベルは標本内の 3 種だけを確認。全 10,928 ページの全ラベル目録は未作成。
- 2026-02 取得時の CSV・fetcher の版は git 履歴に無く、原因 (艦種リスト不足、Armour 写像漏れ) は現行コードとの比較による推定。
- 全ページ本文の比較はしていない (標本のみ)。群れ要約表ページの内容 (名前・建造所・喪失の情報量) は 10 件のみ確認。
- 画像の全数比較は未実施。
- 下流データ (navspec/shipknow) の品質影響は評価していない。
- 標本比較の「正解」は現行ページの HTML から正規表現で取り出したもので、サイト側の誤記までは検証しない。

## 10. 成果物

- レポート: `docs/nsdb_completeness_audit_2026-10-06.md` (本書)
- JSON: `docs/nsdb_audit/` — `summary.json` (見出し数値)、`internal.json` (A)、`site_vs_nsdb.json` (B-2: 取りこぼし 212 件の一覧と原因・国別・艦種別)、`site_classes_live.json` / `site_classes_mirror.json` (発見した艦級 URL 全集合)、`sample_pages.json` / `sample_summary.json` (B-3)、`ship_table_variants.json`、`armour_targets.json`、`request_summary.json`
- スクリプト: `tools/nsdb_audit_lib.py` (読み取り専用接続・1 req/s・キャッシュ・停止条件)、`nsdb_audit_internal.py`、`nsdb_audit_crawl.py`、`nsdb_audit_compare.py`、`nsdb_audit_pages.py`、`nsdb_audit_sample.py`、`nsdb_audit_summarize.py`、`nsdb_audit_ship_variants.py`、`nsdb_audit_finalize.py`
- 再実行順: `internal` → `crawl --site origin` / `--site mirror` → `compare` → `sample` → `summarize` → `ship_variants` → `finalize`。取得キャッシュは本セッションの scratchpad にあり、無い場合は再取得 (約 40 分)。
