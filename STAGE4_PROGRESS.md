# navspec Stage 4 Armament — 進捗・次やることリスト

最終更新: 2026-07-15 (Commit A/B/C 完了・origin push 済み)
現在のbranch: `navspec-armament-stage4`（origin 同期済み、最新は Commit C で更新予定）

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
- [x] **DB seed 収集完了**（navweaps.com 機械スクレイピング + 手動定義、6カ国/90-460mm）
  - 件数は下記「35件」が初回収集時の値。`a2e1528 Expand navspec armament coverage` で
    増えた分が反映されていなかったため、**2026-07-26 に実測値へ修正**した。
  - `data/navspec/armament/weapons.jsonl` (**59件**, 0 errors)
  - `data/navspec/armament/mounts.jsonl` (**59件**)
    - `year_introduced` は当初全59件 null だったが、2026-07-26 に
      `tools/navspec_armament_mount_years.py` で NSDB から導出して**56件を充填**した
      （未決定3件は null のまま）。出典は各レコードの `year_source`、`verified` は false
  - `data/navspec/armament/ammunition.jsonl` (**59件**)
  - `data/navspec/armament/fire_control.jsonl` (17件, 手動定義)
  - `data/navspec/armament/compatibility_rules.json` (**59** weapon_to_mount entries + 物理制約)
    - 艦種の口径範囲 `caliber_min_max_by_ship_type` は6艦種のみ
      (battleship / battlecruiser / heavy_cruiser / light_cruiser / destroyer / coastal_defense)
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
- [x] **commit B**: `02c9c5b feat(navspec): add classic armament validator` (4 files, +1032)
- [x] **push**: `git push -u origin navspec-armament-stage4` 完了 (PR URL: https://github.com/eightman999/autoresearch-mlx/pull/new/navspec-armament-stage4)

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

### ※ Phase 2-3
- [x] **Commit C: retriever** `tools/navspec_armament_retriever.py`
  - `retrieve_armament(context, db, strategy)`: ship context から代表 candidate を生成
  - `retrieve_armament_variations(context, db, n)`: teacher データ用の多様性バリエーション
  - 戦略: representative (最大口径) / mid_caliber / random
  - 17 tests pass (tests/test_navspec_armament_retriever.py)
  - 副作用修正: validator `_get_rule` のネスト dict 対応、barbette 閾値 1.5→1.4 (大和型38m/460mm砲塔12.88m で警告解消)
- [x] **Commit D: teacher生成** `tools/navspec_lora_data_armament_stage4a.py`（2026-07-26 実装・未commit）
  - 出力契約: `schemas/navspec_armament_teacher_stage4a.schema.json`（`navspec.armament_teacher.v1`）
    - トップレベルは Stage 2 の `navspec.lora_candidate.v1` に揃え、
      `spec`→`armament` / `design_concept`→`design_rationale`、`warnings` を追加
  - teacher は LLM 不使用。retriever が candidate を作り validator が判定、
    error→`impossible` / warn→`warnings[]` + confidence 減点
  - 生成実測（count=1200, seed=42）: rows=1200 / ok=855 / impossible=345 /
    train=860 validation=178 test=162 → `data/navspec/lora/stage4a_armament_v0/`
  - tests: `tests/test_navspec_lora_data_armament_stage4a.py` 17件 all pass。
    リポジトリ全体は51 passed（`tests/test_navspec_armament_scraper.py` は
    `ModuleNotFoundError: No module named 'tools'` で収集エラーだが、これは
    `a2e1528` 時点からの**既存問題**であり Commit D とは無関係）
  - docs: `docs/navspec_armament_stage4a_teacher.md`
  - 副作用修正なし（Commit B/C のコードは未変更。retriever の
    「代表砲1門しか試さない」制約は stage4a 側の `retrieve_with_fallback()` で回避）
  - commit: `25db1a2 feat(navspec): add stage4a armament teacher dataset builder`（未push）
- [x] **Commit D-2: 批評タスクデータ** `tools/navspec_lora_data_armament_critique_stage4a.py`
      （2026-07-26 実装）
  - Commit D の生成タスクでは14 reason code 中7個が1件も出なかったため、
    **候補を与えて validator の判定を当てさせる批評タスク**を別データセットとして追加。
    出力契約は生成タスクと同じ `navspec.armament_teacher.v1` を使い回す
    （request の `task` が `critique_navspec_armament` になり `candidate` が入るだけ）
  - 摂動8種（incompatible_mount / foreign_mount / bad_positions / stacked_mounts /
    unknown_fire_control / early_radar_fire_control / oversized_magazine / clean）
  - 生成実測（count=900, seed=42）: rows=972 / ok=463 / impossible=509 /
    train=675 validation=150 test=147 / skipped_no_material=0
    → `data/navspec/lora/stage4a_armament_critique_v0/`
  - tests: 16件 all pass。リポジトリ全体 67 passed
  - **reason code カバレッジ: 生成+批評で 14/14**（砲塔就役年の充填後）
- [x] **Commit D-3: 砲塔の就役年を充填** `tools/navspec_armament_mount_years.py`
      （2026-07-26 実装）
  - NSDB の `CLASS.Armament`（`3 x 3 - 406/45 Mk 6+ ...`）と armament DB を
    **口径・連装数・砲身長・形式記号**で突合。艦級名は一切使わないので
    同名異艦（扶桑 1877/1915、Tennessee 1904/1919）の罠を踏まない
  - 59件中**56件**を充填、うち**43件は形式記号まで一致**。未決定3件は null のまま
  - NSDB の艦級年と navweaps の砲の年は ±2年のノイズがある（実測56件の66%）。
    +3年以上だけを実信号として採用する `settle_year` を入れた
  - 結果 **15件が砲より後年**になり `armament.mount_unavailable_for_era` が踏めるように
  - 出典は全件 `year_source` に記録。`verified` は false のまま（人手照合待ち）
  - レビュー用一覧: `docs/navspec_armament_mount_years_review.tsv`
  - **副作用修正**: `retrieve_available_mounts()` が砲塔の就役年・国籍で
    フィルタしていない欠陥が露見（年が入っていなかったため今まで見えなかった）。
    Commit C 自身のテスト2件が落ちたので era/nation フィルタを追加した
- [x] **砲の就役年のバグ修正** `tools/navspec_armament_scraper.py`（2026-07-26）
  - `Date In Service` に形式ごとの年が並ぶページで、先頭の年を無条件に採っていた。
    14"/50 のページは `Mark 7: About 1935 Mark 11: About 1940 Mark B: N/A` で、
    `US_14INCH_MK11` に Mark 7 の1935が入っていた（正しくは1940）
  - `year_introduced_for()` を追加し、weapon_id の形式に一致するラベルの年を採る。
    形式で分かれていないページは従来どおり最初の年
  - キャッシュ全59件で再計算し、**変わったのは `US_14INCH_MK11` の1件のみ**（1935→1940）。
    砲塔の年も `navspec_armament_mount_years.py --apply` で再導出済み
- [~] **Commit E: 学習は実施したが判定は学習できていない** `stage4a-armament-r8`（2026-07-26）
  - 学習データ: 生成 + 批評を結合した
    `data/navspec/lora/stage4a_armament_combined_v0/`（train 1558 / valid 309 / test 323）。
    契約が同一なので混ぜられる。Stage 4a は契約が別なので stage2-status-r8 からは resume せず
    ベースモデルから学習した
  - 設定: rank 8 / 16層 / iters 500 / lr 5e-6 / mask-prompt。
    Val loss 2.047 → 0.093 (iter 400) → 0.127 (iter 500)
  - **評価結果（test 120件を実際に生成させた実測）**:
    | 指標 | 値 |
    |---|---|
    | JSON として読める | 0.967 |
    | 出力契約を満たす | 0.967 |
    | **status 正解率** | **0.567**（多数派ベースライン 0.517） |
    | reason code F1 | **0.177** |
  - **JSON の形は学べたが、ルールエンジンの判定は学べていない。**
    status 正解率が多数派ベースラインをほぼ上回らない。**採用可否の判断はまだ出せない**
  - 原因の仮説: assistant 出力の大部分が定型 JSON で、判定に効くトークン
    （status の1語・reason code）が全体のごく一部しかないため損失が薄まる
  - 次に試すこと: status/reason だけを予測させる補助タスクの追加 / rank・層数を上げる /
    より大きいベースモデル。詳細は `docs/navspec_armament_stage4a_teacher.md` §11
  - 評価ツール: `tools/navspec_armament_stage4a_eval.py`、
    結果: `docs/navspec_armament_stage4a_eval.json`

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

---

## 7. 2026-08-12 継続実験（現行DB v1・verdict補助タスク）

### 7.1 実験概要・DB実測値
- **Armament DB 実測値**:
  - `weapons`: 71件 / `mounts`: 71件 / `ammunition`: 71件 / `fire_control`: 17件
  - 11 nations、weapon years: 1910–1949、mount year null: 4件
- **保護対象不変**: 既存 v0 adapter / Stage 2 default (`navspec-qwen2.5-0.5b-stage2-r8`) / classic validator は一切変更していない。

### 7.2 データセット実測値 (v1)

| データセット | 総行数 (rows) | ok 件数 | impossible 件数 | train | valid | test | 備考 |
|---|---|---|---|---|---|---|---|
| **generation v1** | 1,200 | 832 | 368 | 866 | 177 | 157 | 教師生成タスク |
| **critique v1** | 980 | 437 | 543 | 679 | 159 | 142 | 批評タスク (requested: 900, skipped_no_material: 10) |
| **combined v1** | 2,180 | 1,269 | 911 | 1,545 | 336 | 299 | generation + critique 結合 |

- **Reason / Warning コード関係**:
  - **Combined v1 (generation v1 + critique v1)** の `reason[]` カバレッジ: 全14種中 **10/14 種**。
  - **Critique-only v1** の `reason[]` カバレッジ: 全14種中 **7/14 種**（Combined v1 の 10/14 とは母集団が異なる）。
  - `barbette_too_large_for_beam`, `magazine_volume_insufficient`, `topweight_limit_exceeded`, `blast_interference` は warning-only の4種であり、`reason[]` の 14/14 とは表記しない（ただし warning code として学習データ内には存在する）。

### 7.3 Full Armament Adapter (stage4a-armament-r8-v1) 評価結果
- **ハイパーパラメータ**: Qwen2.5-0.5B-Instruct, 500 iter, 16 layers, lr = 5e-6, mask-prompt, final Val loss = 0.102
- **評価対象**: `docs/navspec_armament_stage4a_eval_v1.json` (実生成 299件 test 全件)

| 指標 | 実測値 | 備考 |
|---|---|---|
| `json_valid` | 0.9599 | JSONパース成功率 (287/299) |
| `contract_ok` | 0.9498 | 契約遵守率 (284/299) |
| `status_accuracy` | 0.5552 | status 一致率 (166/299, 多数派ベースライン 0.5452 近傍) |
| `reason F1` | 0.2331 | Precision: 0.1906 / Recall: 0.3000 |
| `warning F1` | 0.0444 | Precision: 0.5000 / Recall: 0.0233 |
| `reason_set_exact` | 0.2676 | (80/299) |
| `warning_set_exact` | 0.6990 | (209/299) |

- **予測 status 分布**: `impossible`: 181件 / `ok`: 105件
- **未知 Reason Code**: 以下の3種（定義外）が発生。
  - `armament.armament_count_incompatible` (1件)
  - `armament.armament_weight_limit_exceeded` (1件)
  - `armament.nation_unavailable` (1件)

### 7.4 Verdict Auxiliary Task 評価結果 (診断用)
critique v1 全980件を使用し、判定特化補助タスクとして検証を実施。

| バージョン | 特徴 | Final Val Loss | Test 142件 JSON/Contract | Status Accuracy | Reason F1 | 予測 status 分布 | 結果 |
|---|---|---|---|---|---|---|---|
| **v1** | `status` が JSON 末尾 | 0.070 | 1.0 (142/142) | 0.4296 (61/142) | N/A (0.0) | `ok` 142件固定 / reason 0 | 診断用・採用不可 |
| **v2** | `status` キーを JSON 先頭に変更 | 0.078 | 1.0 (142/142) | 0.4296 (61/142) | N/A (0.0) | `ok` 142件固定 / reason 0 | 診断用・採用不可 |

### 7.5 テスト・検証および結論
- **検証**: seed validator OK。Stage4a関連・combiner・verdictテスト群を含む `PYTHONPATH=. pytest` 全体テスト実行によりパスを確認（`PYTHONPATH=. pytest` 全体 82 passed）。full v1 評価は実生成 299件。
- **結論**:
  1. データ再生成・結合・学習・評価の全工程を完了。
  2. Full JSON モデルはフォーマット/形式を約95-96%で高精度に習得するが、ルール判定器としては多数派ベースライン(0.5452)付近に留まる。Verdict 補助タスクにおいても判定ロジック未学習。
  3. 既存の v0 adapter / default / Stage 2 等のプロダクション環境には一切影響なし（未変更）。
- **次ステップ候補**:
  - クラス別オーバーサンプリングおよび損失設計の変更
  - より大きいベースモデル (3B / 7B等) の採用
  - 生成タスクと批評タスク間のスプリット漏洩防止・厳格分割
  - Warning 評価の継続
