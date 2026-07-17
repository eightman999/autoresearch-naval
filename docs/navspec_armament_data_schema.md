# NavSpec Stage 4 — Armament Data Schema

最終更新: 2026-07-15
対象 branch: `navspec-armament-stage4`
schema version: `navspec.armament.v1` (weapons/mounts/ammunition), `navspec.compatibility.v1` (rules)

---

## 1. 概要

Stage 4 Part II (Armament Specialist) が参照する詳細装備 DB。
ShipSpecRecord v1 の集約 `armament{main_caliber_mm, main_barrels, mounts[]}` /
`equipment{main_battery, ..., fire_control}` とは **別レイヤー**。集約値はこの詳細 DB から
コードで導出する (`main_gun_caliber_mm = weapon.caliber_mm`,
`main_gun_count = mount_count × barrels_per_mount`)。

5ファイルを `data/navspec/armament/` に配置する。

| ファイル | 件数 | 主ソース | 生成方法 |
| --- | --- | --- | --- |
| `weapons.jsonl` | 59 | navweaps.com 個別武器ページ | `tools/navspec_armament_scraper.py` 機械抽出 |
| `mounts.jsonl` | 59 | navweaps.com Mount/Turret Data 表 | 同上 (1 weapon = 1 代表 mount) |
| `ammunition.jsonl` | 59 | navweaps.com Ammunition 表 | 同上 (1 weapon = 1 代表弾) |
| `fire_control.jsonl` | 17 | 公式文献 / Wikipedia | `tools/navspec_armament_static_data.py` 手動定義 |
| `compatibility_rules.json` | 59 entries | weapons.jsonl + mounts.jsonl + 本ファイル定義 | 同上 (自動 + 手動) |

収集範囲: 6カ国 (JP/US/UK/DE/FR/IT) / 1911-1944 / 90-460mm / 代表的主力艦・巡洋艦・駆逐艦主砲・両用砲。初期35件に、各国4件ずつ計24件の巡洋艦砲・駆逐艦砲・両用砲を追加した。

---

## 2. id 体系

progress §3 の方針に従い、国ごとに慣習的な口径表記を使う:

| 国コード | 口径表記 | 例 |
| --- | --- | --- |
| JP / FR / IT | `MM` (メートル法) | `JP_460MM_94SHIKI`, `FR_381MM_M1935`, `IT_381MM_M1934` |
| UK / US | `INCH` (ヤード・ポンド法) | `UK_15INCH_MKI`, `US_16INCH_MK7` |
| DE | `CM` (メートル法) | `DE_38CM_SKC34` |

- `weapon_id`: `{NATION}_{CALIBER}_{FORM}`
- `mount_id`: `{NATION}_{CALIBER}_{MOUNT_TYPE_UPPER}_{FORM}`
  - 例: `JP_460MM_TRIPLE_94SHIKI`, `US_16INCH_TRIPLE_MK7`, `UK_15INCH_TWIN_MKI`
- `fire_control_id`: `{NATION}_{DESIGNATION}` (例: `JP_TYPE94_DIRECTOR`, `UK_AFCT`)
- `ammo_id`: `{WEAPON_ID}_{TYPE}` (例: `JP_460MM_94SHIKI_APC`, `US_16INCH_MK7_APC`)

形式コード (FORM) は国別慣習に従う:
- JP: `94SHIKI` (九四式), `3SHIKI` (三年式), `98SHIKI` (九八式)
- US/UK: `MK7`, `MKI`, `MKVII`, `MKXXIII` (ローマ数字はそのまま大文字)
- DE: `SKC34` (SK C/34), `SKC28`
- FR/IT: `M1935`, `M1934` (Mle 表記)

---

## 3. weapons.jsonl

個別の砲 (gun) の性能。`weapon_id` が他ファイルの参照キー。

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `weapon_id` | str | ✓ | `{NATION}_{CALIBER}_{FORM}` |
| `name` | str | ✓ | 人間向け名称 |
| `nation` | str | ✓ | JP / US / UK / DE / FR / IT |
| `year_introduced` | int | | 就役年 (navweaps "Date In Service") |
| `year_retired` | int\|null | | 退役年 (現状 null; 運用で補完可能) |
| `caliber_mm` | int | ✓ | 実口径。navweaps "Actual Size" 優先、なければ Designation の cm 表記、最後に URL 推定 |
| `barrel_length_calibers` | float | ✓ | 口径長 (例: 45.0, 50.0, 54.5) |
| `gun_weight_t` | float | ✓ | 砲体重 (kg → t 換算, MT/CMY 単位も吸収) |
| `shell_weight_kg` | float | ✓ | 代表弾 (APC優先) の弾量 |
| `muzzle_velocity_mps` | float | ✓ | 初速 (mps 優先、fps は mps 換算) |
| `rate_of_fire_rpm` | float | ✓ | 発射速度 (rounds/min、範囲表記なら最初の値) |
| `max_range_m` | int | ✓ | 最大射程 (Range 表から最大 m、本文中表は target.max_range_override で補完) |
| `loading_type` | str | ✓ | bag / cartridge / qf_cartridge / fixed / unknown |
| `dual_purpose` | bool | | 対空両用か (target.dual_purpose) |
| `technology_tier` | str | | mainstream / legacy |
| `source` | str | ✓ | navweaps URL |
| `source_type` | str | ✓ | `navweaps` |
| `verified` | bool | ✓ | 目視照合済みか (初期 false) |
| `confidence` | float | ✓ | 機械抽出は 0.8、手動補完は同値で notes に出典明記 |
| `notes` | str | | 補足 |
| `fictional` | bool | ✓ | 架空装備か (現状全件 false) |

デバッグ用に `_navweaps_title` / `_ship_class` を含む (最終 JSONL から除去してもよい)。

---

## 4. mounts.jsonl

砲塔・ mounting のデータ。各 `weapon_id` につき代表砲塔1件。

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `mount_id` | str | ✓ | `{NATION}_{CALIBER}_{TYPE}_{FORM}` |
| `weapon_id` | str | ✓ | weapons.jsonl への参照 |
| `name` | str | ✓ | 人間向け名称 |
| `nation` | str | ✓ | |
| `year_introduced` | int\|null | | 砲塔としての就役年 (現状 null) |
| `barrels_per_mount` | int | ✓ | 1 / 2 / 3 / 4 |
| `mount_type` | str | ✓ | single / twin / triple / quadruple |
| `turret_weight_t` | float | ✓ | 砲塔重量 (tons 優先、mt/kg 換算) |
| `barbette_diameter_m` | float\|null | | バーベット直径 (現状 null; validator 用に推定補完予定) |
| `deck_penetration_count` | int\|null | | 甲板貫通数 (現状 null) |
| `minimum_beam_m` | float\|null | | 要求ビーム (現状 null) |
| `minimum_displacement_t` | float\|null | | 要求排水量 (現状 null) |
| `turret_armor` | str\|null | | 装甲厚 (現状 null) |
| `training_speed_deg_s` | float\|null | | 旋回速度 (deg/s) |
| `elevation_range` | str\|null | | 仰角範囲 "lo/hi" (例: "-5/+45") |
| `crew` | int\|null | | |
| `power_requirement_kw` | float\|null | | |
| `magazine_volume_m3` | float\|null | | |
| `technology_tier` | str | | |
| `source` | str | ✓ | navweaps URL |
| `source_type`, `verified`, `confidence`, `notes`, `fictional` | | | weapons と同じ運用 |

`barbette_diameter_m` 等の null フィールドは、Commit B の classic validator 実装時に
`turret_weight_t` / `caliber_mm` から経験則で補完する想定。

---

## 5. ammunition.jsonl

各 weapon の代表弾。APC > SAPBC > SAP > AP > Common > HE の順で代表を選ぶ。

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `ammo_id` | str | ✓ | `{WEAPON_ID}_{TYPE}` |
| `weapon_id` | str | ✓ | weapons.jsonl への参照 |
| `name` | str | ✓ | 弾種名 (例: "APC Type 91") |
| `type` | str | ✓ | APC / SAPBC / SAP / AP / Common / HE |
| `shell_weight_kg` | float | ✓ | |
| `muzzle_velocity_mps` | float | | 弾種固有の初速 |
| `explosive_weight_kg` | float\|null | | 装薬量 (Bursting Charge 行から抽出; 一部 null) |
| `source` | str | ✓ | navweaps URL |
| `source_type`, `verified`, `confidence`, `notes`, `fictional` | | | 同上 |

---

## 6. fire_control.jsonl

射撃指揮装置。navweaps 個別武器ページには載らない情報のため、
`tools/navspec_armament_static_data.py` で手動定義している (verified: false)。

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `fire_control_id` | str | ✓ | `{NATION}_{DESIGNATION}` |
| `name` | str | ✓ | |
| `nation` | str | ✓ | |
| `year_introduced` | int | ✓ | |
| `system_type` | str | ✓ | `main_battery_director` / `dual_purpose_director` / `aa_director` / `mechanical_analog_computer` |
| `radar` | bool | ✓ | レーダー連動か (year < 1938 で true なら validator 警告) |
| `range_finder_m` | float\|null | | 測距儀基線長 |
| `director_type` | str | | 内部用タグ |
| `technology_tier` | str | | mainstream / legacy |
| `source`, `source_type`, `verified`, `confidence`, `notes`, `fictional` | | | |

17件を収録。progress §3 の `JP_TYPE94_DIRECTOR` / `UK_AFKCT` (AFCT の alias 扱い) を含む。

---

## 7. compatibility_rules.json

classic validator (`tools/navspec_armament_validator.py`, Commit B) が参照する
互換性ルール。`weapon_to_mount` は scraper/static_data の出力から自動構築、
それ以外は本スキーマで定義する初期値。

```json
{
  "rules_version": "navspec.compatibility.v1",
  "weapon_to_mount": { "<weapon_id>": ["<mount_id>"] },
  "caliber_min_max_by_ship_type": {
    "battleship":     { "min": 305, "max": 460 },
    "battlecruiser":  { "min": 280, "max": 381 },
    "heavy_cruiser":  { "min": 203, "max": 203 },
    "light_cruiser":  { "min": 127, "max": 155 },
    "destroyer":      { "min": 100, "max": 140 },
    "coastal_defense":{ "min": 76,  "max": 283 }
  },
  "barbette_to_beam": { "min_beam_over_barbette_diameter": 1.5 },
  "turret_weight_to_displacement": {
    "max_turret_weight_fraction_of_displacement": 0.06,
    "min_displacement_t_for_heaviest_turret": 25000
  },
  "topweight": { "max_full_armament_weight_fraction": 0.35 },
  "magazine_volume": { "min_m3_per_round_default": 0.05 },
  "era_availability": { ... },
  "fire_control_compatibility": { ... }
}
```

各閾値は progress §15 の validator ルールに基づく保守的な初期値。
Commit B で classic validator 実装時にキャリブレーションする。

---

## 8. データ収集の来歴と verified 運用

- **weapons / mounts / ammunition**: `tools/navspec_armament_scraper.py` が
  navweaps.com の個別武器ページ (sitemap.xml で URL を機械特定) から抽出。
  ページ構造の揺らぎ (Actual Size 行の有無、インチ/cm 表記、Range 表の有無) に対し
  複数フォールバックを持つ。
  - `target.caliber_override`: navweaps 表記ゆれを補正 (例: JP 20cm → 実 203mm)
  - `target.max_range_override`: 本文中の表 (`<table>` 外) の値を補完
    (FR_130MM_M1932=20,870m, IT_135MM_M1938=19,600m)
- **fire_control**: 公式文献からの代表例を手動定義。`verified: false` 運用。
- 全レコード初期 `verified: false` / `confidence: 0.7-0.8`。
  目視照合が進んだ段階で `verified: true` に昇格させる。

---

## 9. 再生成コマンド

```bash
# weapons / mounts / ammunition を navweaps から機械抽出
# (初回はフェッチ、2回目以降は --cache でキャッシュ利用)
uv run python tools/navspec_armament_scraper.py \
  --cache data/navspec/armament/_cache_html

# fire_control / compatibility_rules を生成 (静的データ + weapon_to_mount 自動構築)
uv run python tools/navspec_armament_static_data.py

# 簡易検証 (必須フィールド + 参照整合性)
uv run python tools/navspec_armament_validate_seed.py
```

---

## 10. 既存レイヤーとの関係

- `tools/navspec_equipment.py`: `equipment_plan()` が集約 equipment を生成する。
  **Part II は別レイヤー** であり、置換ではなく並存。
- `schemas/ship_spec_record.schema.json`: `armament{main_caliber_mm, main_barrels, mounts[]}` /
  `equipment{main_battery,...,fire_control}` が既存。
- 集約値導出: `main_gun_caliber_mm = weapon.caliber_mm`,
  `main_gun_count = mount_count × barrels_per_mount`
- classic validator 既存: `tools/navspec_contradiction.py` (383行) /
  `navspec_equipment.validate_equipment`。Commit B で armament 専用 validator を追加。
- reason code 3系統: schema enum / `navspec_lora_stage2_schema.py:REASON_CODES` 正典 /
  `navspec_contradiction.py` 日本語リスト。`classify_reason_codes` で橋渡し。
