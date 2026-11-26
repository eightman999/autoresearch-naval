# shipknow v0_2 抽出差分（新 NSDB 2026-10-07 と v0_1 の比較）

機械可読: `extract_diff.json`。records: `data/navspec/shipknow_v0_2/records.jsonl`。build_tasks はまだ実行していない。

## 件数

- v0_1 は 10,644 件、v0_2 は 10,580 件。source_url で照合して 10,380 件が対応した。
- v0_2 だけにある艦級: 200 件（取り直しで新たに拾えた艦級）。
- v0_1 だけにある艦級: 4 件（Sarawak。サイト側の 404）。
- 件数が減って見えるのは、v0_1 側の URL 重複行 260 件のため。
- 除外: 主要数値（排水量・全長・速力）が全部欠けた 344 件（旧 355 件）。

## 値の変化（対応した 10,380 件）

| 項目 | 同一 | 変化 | 新たに取得 | 失われた |
|---|---:|---:|---:|---:|
| 常備排水量 | 8,504 | 1 | 2 | 0 |
| 満載排水量 | 6,883 | 5 | 0 | 0 |
| 全長 | 10,214 | 15 | 1 | 7 |
| 全幅 | 10,055 | 0 | 1 | 11 |
| 速力 | 9,709 | 0 | **91** | 0 |
| 機関出力 | 9,413 | 0 | 3 | 0 |
| 武装リスト | 10,340 | 38 | 1 | 1 |
| センサー種別 | 10,251 | 125 | 1 | 3 |
| 艦種（coarse） | 10,254 | **126** | — | — |

## 新たに取れた項目（v0_2 の 10,580 件中）

| 項目 | 件数 |
|---|---:|
| 竣工年 | 10,369 |
| 起工・進水年 | 6,732 |
| 基準排水量 | 5,296 |
| 装甲（原文） | 1,100 |
| 甲板装甲 | 908 |
| 舷側装甲 | 744 |
| 司令塔装甲 | 737 |
| 砲塔装甲 | 512 |
| 潜航深度 | 832 |

## 作り直し前に決めること

### 1. 年の意味が変わった

v0_2 の年は、見出しの日付（例 `(1970s/1999)`）の最小値。艦級の建造・就役の開始年にあたる。
v0_1 の年は、艦級名の年か SHIPS の起工・進水年が中心だった。他国からの譲渡艦では「その国に入った年」になっていた。

| v0_1 と v0_2 の年の差 | 件数 |
|---|---:|
| 一致 | 4,607 |
| ±1〜2 年 | 3,234 |
| ±3〜10 年 | 1,949 |
| 10 年超 | 411 |
| v0_1 で年なし → v0_2 で取得 | 178 |

- 例: Albania の R117 は 1999（譲渡年）→ 1970（建造年代）。大和級は 1942 → 1941。
- 譲渡艦は「設計・建造の年代」と「その国での就役年」が大きく違う。
- 案: `year`（設計・建造年代）と `year_in_navy`（その国での就役年）の 2 つを持たせる。

### 2. 帆走戦列艦の艦種が退行している

- ship_of_the_line → early_warship が 85 件（例: Téméraire 級、Bucentaure 級）。
- 原因: 新 DB の TYPE 表記が変わり（fine `sailing` → `sailing-ship`）、艦種マップが戦列艦と見分けられなくなった。
- `shipknow_types.py` の艦種マップの修正が必要。ほかの艦種の変化（escort → mine_warfare 7 件など）も合わせて点検する。

## コード変更（後方互換）

- `tools/navspec_common.py`: `parse_when_date_year` を追加。
- `tools/navspec_extract.py`: `CLASS.WHEN_DATE` を優先する。DB を読み取り専用で開く。
- `tools/navspec_armament_mount_years.py`: 新 DB では `WHEN_DATE` を使い、艦の対応付けは source_url で行う。
- `tools/shipknow_extract.py` / `tools/shipknow_parse.py`: 新しい列を取り込む。
- いずれも旧 DB 形式でも従来どおり動く。テストはリポジトリ全体で 272 件 PASS。

## 追記: 決定の反映後（records / 分割 / タスクを作り直した）

- 年を 2 つに分けた: `year`（見出し日付 A/B の A、設計・建造年代）と `year_in_navy`（A/B の B。無ければ SHIPS.Comp → SHIP_GROUPS.Completed → SHIPS.Launched〈year 以降のみ〉→ year）。出所は `year_in_navy_source`。規則は `tools/shipknow_parse.py::parse_when_date_parts` と `tools/shipknow_extract.py` の docstring、テストは `tests/test_shipknow_v0_2.py`。
- 帆走戦列艦の退行の原因は「TYPE 表記の変化」ではなく `fine_label` のバグだった。新 DB の生 TYPE は `sailing-ships-of-the-line` など戦列艦を明記していて、`ships-of-the-line` の保護置換が直後の `-of-...` 切り落としで消え、fine が `sailing-ship` になっていた。旧 DB では CLASS_NAME の `ships-of-the-line` で救えていた。修正して ship_of_the_line に戻した（87 件）。
- 新カテゴリ `sailing_ship`（戦列艦以外の帆走艦 148 件）を追加し、戦列艦とは併存。ほかのマップ修正は `shipknow_types.py` の RULES / OVERRIDES を参照。
- 分割は v0_1 と対応する艦級（source_url）の割り当てを継承した。詳細は `data/navspec/shipknow_v0_2/splits.json` の `inheritance`。
