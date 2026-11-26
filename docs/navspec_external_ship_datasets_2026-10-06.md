# 公開艦艇データセット調査（2026-10-06）

目的: navspec の学習・DB 拡張に使える公開データの棚卸し。件数は API 実測、ライセンスはページ記載のみを根拠にする（不明は不明のまま）。

## 既存データとの関係

- NSDB（Navypedia 由来）: CLASS 11,144 / SHIPS 108,778 / COUNTRIES 181。艦級諸元はほぼ網羅済み。
- armament DB: weapons / mounts / ammunition 各71、fire_control 17（11か国）。
- 公開データで NSDB を超える艦級数値 DB は見つからなかった。拡張の価値は「別出典での検算」「条約ルール」「設計意図テキスト」の3方向。
- navweaps 以外の構造化された兵装 DB はほぼ存在しない。

## 優先度上位

| # | ソース | 規模・内容 | ライセンス | 用途・注意 |
|---|---|---|---|---|
| 1 | Wikipedia 艦級記事の infobox（wikitext / dump を直接パース） | Battleship classes 126記事、Cruiser 218、Destroyer 367、Carrier 28（各サブカテゴリ別） | CC BY-SA（今回は再確認していない） | NSDB とは独立した出典で検算に使う。`{{convert}}` の展開が必要。DBpedia は数値が欠落するので使わない |
| 2 | 条約原文（ワシントン 1922 / ロンドン 1930 / 第二次ロンドン 1936） | 例: 第XI条 主力艦・空母以外は基準排水量 10,000t 以下 | 未確認（政府公文書・条約） | ルール違反判定の教師にいちばん直結する。条項が少ないので手でルール化する。ibiblio / history.state.gov FRUS / navweaps |
| 3 | HF `wfzimmerman/fleets-of-wwii-design-commentary` | 1,009艦級・19海軍、自由文の諸元と設計論評 | CC BY-NC-4.0（商用は Nimble Books の許諾が必要とカードに記載） | 設計意図のテキスト。書籍からの抽出で、艦級分割は LLM。数値は NSDB と照合して使う |
| 4 | Wikidata SPARQL | 艦級 4,570（1900〜1950 は 791）。排水量 84、兵装 228 しか値が無い | CC0（今回は再確認していない） | 諸元の供給源には薄い。NSDB・Wikipedia・Commons を結ぶ ID 軸として使う |
| 5 | Ships' Data, U.S. Naval Vessels（1911〜1949 各版、shipscribe.com） | 米艦の一次資料級の諸元 | 再配布条件は不明（サイトは © S. S. Roberts） | スキャン PDF で OCR が必要。米艦の検算と改装前後の確認に使う |

次点: Internet Archive の Jane's / Brassey's（1898・1913・1914 版など）。スキャンごとに権利状況を確認する必要がある。

## その他（使いにくい・対象外）

- DBpedia: dbo:Ship 46,327件だが、数値と単位がテンプレート展開で欠落（例: Nagato 級の排水量が空）。
- HF `wfzimmerman/wwii-naval-armament`: 艦砲93・魚雷42・条約19行。CC BY-NC-4.0。表の抽出が荒い。
- Navbase（WWII 以降中心、個人利用のみ）、NavSource（全権利留保）、naval-history.net（出典明記で利用可、諸元 DB ではない）。
- Dreadnought Project: Cloudflare のチャレンジで閲覧できず、ライセンス不明。
- Kaggle の warship 系: 現代艦中心。GitHub はゲーム（World of Warships 等）由来の値が中心で、実艦データとして使うと改変値が混ざる。
- 画像（ShipRSImageNet、FGSC-23、USNSSD 等）: 現代艦の衛星・写真が中心で、多くは研究用途限定または NC。
