# navspec Stage 4 Armament — 進捗・次やることリスト

最終更新: 2026-07-15 (Commit A/B 完了、push 待ち)
現在のbranch: `navspec-armament-stage4`（未push）

---

## 1. 現状サマリー

### Part I（完了・origin push済み）
- branch: `autoresearch/jul9-navspec` @ `ed1a16a`（origin同期済み）
- commit 計8件（feat/exp/chore）+ docs/gui.py 追加
- tag 4つ（すべてorigin push済み）:
  - `navspec-lora-stage2-0.5b-v0.1` → `b7594ab`
  - `navspec-lora-stage3-v1-exp` → `b0452c6`
  - `navspec-lora-stage3.1-r8-exp-rejected` → `b0452c6`
  - `navspec-two-stage-pipeline-v1-candidate` → `b0452c6`
- 巨大jsonl問題: filter-repo で `all.jsonl` 2件(137/136MB)を履歴削除、`--force-with-lease` で push 成功
- backup: `../navspec-backup-pre-lfs-migrate.bundle`（20M、048a2fa、complete history）
- fresh clone検証: fsck/lfs fsck/pytest/tag/merge-base(54ad8a5) すべて正常
- 保護対象不変確認: Stage2 default `navspec-qwen2.5-0.5b-stage2-r8` / sha256 `1b9f5eb8...` / classic validator `artifacts/navspec-classic-v1/`

### Part II Phase 1（Commit A 実装完了・commit 待ち）
- branch: `navspec-armament-stage4`（`ed1a16a` から作成）
- [x] **schema 完成**: `schemas/navspec_armament_candidate.schema.json`（`navspec.armament_candidate.v1`）
  - 指示書§13例パス確認済み、total_barrels不整合検出確認済み
- [x] **DB seed 収集完了**（navweaps.com 機械スクレイピング + 手動定義、35件/6カ国/90-460mm）
  - `data/navspec/armament/weapons.jsonl` (35件, 0 errors)
  - `data/navspec/armament/mounts.jsonl` (35件)
  - `data/navspec/armament/ammunition.jsonl` (35件)
  - `data/navspec/armament/fire_control.jsonl` (17件, 手動定義)
  - `data/navspec/armament/compatibility_rules.json` (35 weapon_to_mount entries + 物理制約)
  - `data/navspec/armament/weapons_manifest.json` (スクレイピング結果 manifest)
- [x] **ツール類**:
  - `tools/navspec_armament_scraper.py` (weapons/mounts/ammunition 機械抽出、HTMLキャッシュ付き)
  - `tools/navspec_armament_static_data.py` (fire_control/compatibility_rules 生成)
  - `tools/navspec_armament_validate_seed.py` (簡易検証: 必須フィールド + 参照整合性)
- [x] **docs**: `docs/navspec_armament_data_schema.md` (5ファイルのスキーマ定義)
- [x] **簡易検証**: `uv run python tools/navspec_armament_validate_seed.py` → 0 errors / 0 warnings
- [x] **commit A**: `fa03d83 feat(navspec): add armament and turret data model` (13 files, +2542)
- [x] **classic armament validator**: `tools/navspec_armament_validator.py` (progress §15 ルール + §16 reason code 14件)
- [x] **tests**: `tests/test_navspec_armament_validator.py` (17件、progress §23 の10テスト網羅、all pass)
- [x] **validator docs**: `docs/navspec_armament_validator_rules.md`
- [ ] commit B + push

---

## 2. 次やること（Phase 1 = Commit A 完了 → Commit B）

### Commit A: DB基盤 ✅ 実装完了（commit 待ち）
- 詳細は上記 Part II Phase 1 サマリー参照
- スクレイピング結果: 35件全取得、0件404、`caliber/barrel_length/gun_weight/shell_weight/mv/rof/max_range` 全フィールド完全抽出
- 注記:
  - **FR_152MM_M1920 → FR_152MM_M1930 に修正**（progress §4 元記載 M1920 は史実 Mle 1930 への誤記と判断）
  - **JP_203MM_3SHIKI2GO**: navweaps表記 20cm → 実口径 203mm に補正 (caliber_override)
  - **FR_130MM_M1932 / IT_135MM_M1938**: max_range は本文中表から手動補完 (20870 / 19600 m)
- commit message: `feat(navspec): add armament and turret data model`

### Commit B: classic armament validator（次）
1. `tools/navspec_armament_validator.py`（指示書§15-16 ルール）
2. `tests/test_navspec_armament_validator.py`（指示書§23）
3. `docs/navspec_armament_validator_rules.md`
4. commit: `feat(navspec): add classic armament validator`

### push
- `git push -u origin navspec-armament-stage4`

### ※ Phase 2-3（別セッション）
- Commit C: retriever（`tools/navspec_armament_retriever.py`）
- Commit D: teacher生成（`tools/navspec_lora_data_armament_stage4a.py`）
- Commit E: pipeline統合 + 学習（`stage4a-armament-r8`）

---

## 3. 設計メモ（再開時の参照）

### id 体系
- `weapon_id`: `{国}_{口径MM}_{形式}` → `JP_460MM_94SHIKI`, `UK_15INCH_MKI`, `US_16INCH_MK7`, `DE_38CM_SKC34`
- `mount_id`: `{国}_{口径MM}_{砲塔形式}` → `JP_460MM_TRIPLE_A`, `US_16INCH_TRIPLE_MK6`
- `fire_control_id`: `{国}_{形式}` → `JP_TYPE94_DIRECTOR`, `UK_AFKCT`

### DB フィールド（指示書§12 + §17 メタ）
**weapons.jsonl**: weapon_id, name, nation, year_introduced, year_retired, caliber_mm, barrel_length_calibers, gun_weight_t, shell_weight_kg, muzzle_velocity_mps, rate_of_fire_rpm, max_range_m, loading_type, dual_purpose, technology_tier, source, source_type, verified, confidence, notes, fictional

**mounts.jsonl**: mount_id, weapon_id, name, nation, year_introduced, barrels_per_mount, mount_type, turret_weight_t, barbette_diameter_m, deck_penetration_count, minimum_beam_m, minimum_displacement_t, turret_armor, training_speed_deg_s, elevation_range, crew, power_requirement_kw, magazine_volume_m3, technology_tier, source（+ §17 メタ）

**fire_control.jsonl**: fire_control_id, name, nation, year_introduced, system_type, radar(bool), range_finder_m, director_type, technology_tier, source

**ammunition.jsonl**: ammo_id, weapon_id, name, type, shell_weight_kg, muzzle_velocity_mps, explosive_weight_kg, source

**compatibility_rules.json**: weapon/mount 互換性、caliber/ship_type、barbette/beam、turret/displacement 等

### 既存実装との関係（explore調査済み・要再参照）
- `tools/navspec_equipment.py`（470行）: `equipment_plan()` が集約equipment生成。**Part II は別レイヤー**（詳細armament）。置換ではなく並存。
- `schemas/ship_spec_record.schema.json`: `armament{main_caliber_mm, main_barrels, mounts[]}` / `equipment{main_battery,...,fire_control}` 既存
- 集約値導出: `main_gun_caliber_mm = weapon.caliber_mm`, `main_gun_count = mount_count × barrels_per_mount`
- classic validator 既存: `tools/navspec_contradiction.py`（383行）/ `navspec_equipment.validate_equipment`
- reason code 3系統: schema enum / `navspec_lora_stage2_schema.py:REASON_CODES`正典 / `navspec_contradiction.py`日本語リスト。`classify_reason_codes` で橋渡し

### validator ルール（指示書§15）
era/nation availability, weapon/mount compatibility, caliber/ship_type, barbette/beam, turret_weight/displacement, total_armament_weight, magazine_volume, ammo_weight, deck_penetration, forward/aft_balance, position_duplication, superfiring, blast_interference, fire_control_compatibility

### reason code（指示書§16・全14件）
`armament.weapon_unavailable_for_era`, `.weapon_unavailable_for_nation`, `.mount_unavailable_for_era`, `.mount_unavailable_for_nation`, `.weapon_mount_incompatible`, `.barbette_too_large_for_beam`, `.turret_weight_exceeds_hull_limit`, `.magazine_volume_insufficient`, `.topweight_limit_exceeded`, `.firing_arc_conflict`, `.blast_interference`, `.battery_layout_conflict`, `.armament_displacement_conflict`, `.fire_control_unavailable`

### テスト（指示書§23・全10件）
test_weapon_schema, test_mount_schema, test_weapon_mount_compatibility, test_barbette_beam_rule, test_turret_weight_rule, test_magazine_volume_rule, test_position_validation, test_aggregate_main_gun_fields, test_armament_reason_codes, test_armament_pipeline_fallback

---

## 4. 代表的主砲候補リスト（30-50件収集の参考）

| 国 | 口径 | 形式 | 代表艦 |
|---|---|---|---|
| JP | 460mm | 45口径 九四式 | 大和型 |
| JP | 410mm | 45口径 三年式 | 長門型 |
| JP | 203mm | 50口径 三年式2号 | 最上型/高雄型 |
| JP | 155mm | 60口径 三年式 | 最上(改装前)/大和副砲 |
| JP | 127mm | 50口径 三年式 | 秋月型 |
| JP | 100mm | 65口径 九八式 | 大淀型/秋月型 |
| JP | 140mm | 50口径 三年式 | 金剛型副砲 |
| UK | 15inch | 42口径 MkI | QE/復仇/フッド/ヴァンガード |
| UK | 16inch | 45口径 MkI | ネルソン級 |
| UK | 14inch | 45口径 MkVII | KGV級 |
| UK | 13.5inch | 45口径 MkV | 鉄公爵級 |
| UK | 6inch | 50口径 MkXXIII | サーチフィッシュ/エディンバラ |
| UK | 5.25inch | 50口径 MkI | KGV/Dido |
| US | 16inch | 50口径 Mk7 | アイオワ/モンタナ |
| US | 16inch | 45口径 Mk6 | サウスダコタ/ノースカロライナ |
| US | 14inch | 50口径 Mk11 | ニューメキシコ/テネシー |
| US | 12inch | 50口径 Mk8 | ワイオミング/アーカンソー |
| US | 8inch | 55口径 Mk12 | ニューオリンズ/ウィチタ |
| US | 6inch | 47口径 Mk16 | クリーブランド/ブルックリン |
| US | 5inch | 38口径 Mk12 | 汎用(駆逐/主力艦副砲) |
| DE | 38cm | 52口径 SK C/34 | ビスマルク級 |
| DE | 28cm | 54.5口径 SK C/34 | シャルンホルスト(改装想定) |
| DE | 28cm | 52口径 SK C/28 | ドイッチュラント級 |
| DE | 15cm | 55口径 SK C/28 | 主力艦副砲 |
| DE | 10.5cm | 65口径 SK C/33 | 重対空 |
| FR | 381mm | 45口径 M1935 | リシュリュー級 |
| FR | 330mm | 50口径 M1931 | ダンケルク級 |
| FR | 340mm | 45口径 M1912 | ブルターニュ級 |
| FR | 152mm | 55口径 M1930 | ラ・ガリソニエール（§4 元記載 M1920 は誤記、史実 Mle 1930 に修正） |
| FR | 130mm | 45口径 M1932 | ル・ファンタスク |
| IT | 381mm | 50口径 M1934 | ヴィットリオ・ヴェネト級 |
| IT | 320mm | 44口径 M1934 | コント・ディ・カブール(改装) |
| IT | 152mm | 53口径 M1926/1929 | ザラ/コンドッティエーリ |
| IT | 135mm | 45口径 M1938 | カピターニ・ロマーニ |
| IT | 90mm | 50口径 M1939 | 重対空 |

> 収集時は各砲について navweaps.com を主ソースに: caliber_mm / barrel_length_calibers / gun_weight_t / shell_weight_kg / muzzle_velocity_mps / rate_of_fire_rpm / max_range_m / loading_type を記録。source フィールドにURL明記、verified は初期 false（照合完了で true）。

---

## 5. 再開時の手順

1. `git switch navspec-armament-stage4`（既存branch）
2. 本ファイル（`STAGE4_PROGRESS.md`）と指示書§9-28 を確認
3. DB seed 収集から再開（Commit A）
4. 既存 `navspec_equipment.py` / `navspec_contradiction.py` を再参照してvalidator設計
5. Commit A → Commit B → push

## 6. 注意点
- `tools/navspec_gui.py` は既にcommit済み（Part I）。`.git/info/exclude` に `docs/stage3_numeric_*` 追加済み
- 50MB超JSONL(train/tasks.train)は技術的負債として維持（将来 maintenance-navspec-dataset-history で整理）
- Stage 2 default / classic validator は絶対に触らない（保護対象）
- DB レコードの架空装備は `fictional: true` で区別
