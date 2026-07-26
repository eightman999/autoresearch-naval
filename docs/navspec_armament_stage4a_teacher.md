# navspec Stage 4a armament teacher データ (Commit D)

生成器: `tools/navspec_lora_data_armament_stage4a.py`
出力契約: `schemas/navspec_armament_teacher_stage4a.schema.json` (`navspec.armament_teacher.v1`)
テスト: `tests/test_navspec_lora_data_armament_stage4a.py` (17件)
既定の出力先: `data/navspec/lora/stage4a_armament_v0/`

## 1. teacher の作り方

Stage 2/3 と同じく **LLM はループに入らない**。classic ルールエンジンがそのまま teacher。

1. `navspec_armament_retriever.retrieve_armament()` が armament DB から candidate を作る
2. `navspec_armament_validator.validate_armament()` がそれを判定する
3. `severity=error` が1件でも出たら `status=impossible` + その reason code
4. `severity=warn` は `warnings[]` に載せ、`confidence` を warn 1件につき 0.1 下げる

生徒モデルが学ぶのは「ルールエンジンの入出力関係」であり、
新しい史実知識ではない。

## 2. 出力契約

トップレベルは Stage 2 の `navspec.lora_candidate.v1` に揃えてある。
`spec` → `armament`、`design_concept` → `design_rationale` に置き換え、
`warnings` を追加したもの。

| キー | 内容 |
|---|---|
| `schema_version` | `navspec.armament_teacher.v1` 固定 |
| `status` | `ok` / `impossible` |
| `armament` | `main_batteries[]` + `aggregate`。`impossible` のとき `null` |
| `reason` | error 由来。`impossible` のとき1件以上、`ok` のとき空 |
| `warnings` | warn 由来。**`ok` でも空とは限らない** |
| `suggested_revision` | どこを緩めれば成立するかの提案。`ok` のとき `null` |
| `design_rationale` | retriever が付ける1行の設計根拠 |
| `confidence` | warn の件数で下がる (0.85 → 0.75 → 0.65 …) |

`warnings` を分けているのは、validator の閾値設計上「砲塔重量が排水量の
35〜50%」のような余裕の乏しい設計が warn 止まりで `ok` になるため。
これを捨てると **「7,356t の戦艦に 41cm 砲6門で問題なし」という teacher** ができる。

## 3. 語彙

armament DB 側の語彙をそのまま使う。Stage 2 の `SHIP_TYPES` へのエイリアス表は
**意図的に作っていない**（対応付けを勝手に決めるとデータの意味が変わるため）。

- 国 (6): `JP` `UK` `US` `DE` `FR` `IT`
- 艦種 (6): `battleship` `battlecruiser` `heavy_cruiser` `light_cruiser` `destroyer` `coastal_defense`
- 砲: 35門 / 1911–1944年

DB に無い国・艦種・年代は、そのまま不成立ケースの材料として使う。

## 4. ケース構成

| category | 比率 | 意図 |
|---|---|---|
| `normal` | 0.34 | DB 上で成立する組み合わせ。全件 `ok` になる |
| `variation` | 0.20 | 同一 context から別の砲・門数を引く（多様性） |
| `ship_type_contrast` | 0.10 | 同一国・同一年で艦種だけ振り、口径帯の違いを見せる |
| `era_unavailable` | 0.10 | DB 最古の砲(1911)より前の年 → `weapon_unavailable_for_era` |
| `nation_unavailable` | 0.08 | DB に砲を持たない国 → `weapon_unavailable_for_nation` |
| `ship_type_uncovered` | 0.06 | 口径範囲が未定義の艦種 → `battery_layout_conflict` |
| `hull_too_small` | 0.12 | 船体を 10〜28% に縮めて重量・幅の制約を突く |

`normal` を名前どおり成立させるため、`feasible_combos()` が DB を走査して
実際に主砲が引ける `(nation, ship_type, year)` を先に列挙する。
これをやらないと、たとえば US の最古の主砲が1926年なので
`normal` の2〜3割が `impossible` になってしまう（判定自体は正しいが、
カテゴリの意味が壊れる）。

## 5. 実装上の注意点（作り込んだ理由）

### 5.1 誤った `impossible` を作らない

`retrieve_armament()` は代表砲（最大口径）を1門だけ選び、その砲に載る砲塔が
無ければ即 `None` を返す。船体が小さいケースでこれをそのまま使うと
**「小口径砲なら成立するのに impossible」という誤った teacher** ができる。

`retrieve_with_fallback()` が口径の小さい順に総当たりし、
本当に1門も載らないときだけ諦める。
（retriever 本体は Commit C で確定済みなので触っていない）

### 5.2 口径範囲が未定義の艦種を retriever に渡さない

`retrieve_available_weapons()` は `caliber_min_max_by_ship_type` に
エントリが無いと**口径フィルタを丸ごと素通り**させる。そのまま渡すと
`gunboat` に戦艦主砲が載った teacher ができるので、`make_output()` の先頭で止める。

### 5.3 候補が引けなかった理由を推測で決めない

`diagnose_no_candidate()` はフィルタを1段ずつ外して切り分ける:

1. その国の砲が DB に1門も無い → `weapon_unavailable_for_nation`
2. 全部その年より後に就役 → `weapon_unavailable_for_era`（提案年は実在する最古の年）
3. 艦種の口径範囲が未定義 → `battery_layout_conflict`
4. 口径範囲に入る砲が無い → `battery_layout_conflict`
5. 砲はあるが砲塔が1つも載らない → `turret_weight_exceeds_hull_limit`
6. 砲塔が DB に登録されていない → `weapon_mount_incompatible`

## 6. 生成結果 (count=1200, seed=42)

```
rows=1200  ok=855  impossible=345
split: train=860 validation=178 test=162
```

| reason code | 件数 |
|---|---|
| `armament.weapon_unavailable_for_era` | 136 |
| `armament.weapon_unavailable_for_nation` | 96 |
| `armament.battery_layout_conflict` | 79 |
| `armament.turret_weight_exceeds_hull_limit` | 34 |
| `armament.armament_displacement_conflict` | 15 |

| warning code | 件数 |
|---|---|
| `armament.barbette_too_large_for_beam` | 125 |
| `armament.topweight_limit_exceeded` | 25 |

warn 付きの行は 125/1200。assistant 側の平均文字数は 551。

## 7. 既知の限界（Commit E の設計判断が要る）

**14個の reason code のうち7個がこのデータセットに1件も出ない。**

未出現: `mount_unavailable_for_era` / `mount_unavailable_for_nation` /
`weapon_mount_incompatible` / `magazine_volume_insufficient` /
`firing_arc_conflict` / `blast_interference` / `fire_control_unavailable`

理由は retriever が**常に整合した candidate しか構築しない**ため:

- 互換 mount しか選ばない → `weapon_mount_incompatible` が出ない
- `generate_positions()` が妥当な配置しか作らない → `firing_arc_conflict` / `blast_interference` が出ない
- 利用可能な fire control しか選ばない → `fire_control_unavailable` が出ない
- DB 実測で、砲より後年の砲塔ペアは **0件** → `mount_unavailable_for_era` は構造的に到達不能

つまりこれらは「生成タスク」では原理的に出ない code であり、
**候補を与えて批評させる検証タスク**に属する。

→ **方針決定 (2026-07-26): 批評タスクを別データセットとして足す。**
詳細は次章。

## 8. 批評タスクデータ (`stage4a_armament_critique_v0`)

生成器: `tools/navspec_lora_data_armament_critique_stage4a.py`
テスト: `tests/test_navspec_lora_data_armament_critique_stage4a.py` (16件)
出力先: `data/navspec/lora/stage4a_armament_critique_v0/`

タスクを裏返す。**候補を与えて validator の判定を当てさせる**ことで、
候補側を壊せば全 code に到達できる。

- 入力: context + armament candidate（健全なものと、意図的に壊したものが混在）
- 出力: validator の判定。契約は生成タスクと**同じ** `navspec.armament_teacher.v1`
  （request の `task` が `critique_navspec_armament` になり `candidate` が入るだけ）
- teacher は生成タスクと同じく LLM 不使用。`validate_armament()` の出力をそのまま写す

### 摂動の種類

| category | 比率 | 壊し方 | 狙う code |
|---|---|---|---|
| `clean` | 0.36 | 壊さない | （`ok` になること） |
| `incompatible_mount` | 0.12 | `weapon_to_mount` に無い砲塔へ差し替え | `weapon_mount_incompatible` |
| `foreign_mount` | 0.12 | 他国の砲塔へ差し替え | `mount_unavailable_for_nation` |
| `bad_positions` | 0.12 | 存在しない位置記号・重複位置 | `firing_arc_conflict` |
| `stacked_mounts` | 0.10 | 同一位置に複数砲塔 | `blast_interference` |
| `unknown_fire_control` | 0.10 | DB に無い `fire_control_id` | `fire_control_unavailable` (error) |
| `early_radar_fire_control` | 0.08 | 1938年より前にレーダー射撃指揮装置 | `fire_control_unavailable` (warn) |
| `oversized_magazine` | 0.08 | `ammo_per_gun` を過大に | `magazine_volume_insufficient` |

摂動が validator のどの分岐を踏むかは実装で確認済みだが、
teacher は必ず `validate_armament()` の実際の出力から作る（推測で code を書かない）。
1つの摂動が複数 code を同時に踏むことはある（例: 同一位置への複数砲塔は
重複位置でもあるので `firing_arc_conflict` も同時に出る）。

### 生成結果 (count=900, seed=42)

```
rows=972  ok=463  impossible=509
split: train=675 validation=150 test=147
skipped_no_material=0
```

| reason code | 件数 | | warning code | 件数 |
|---|---|---|---|---|
| `firing_arc_conflict` | 241 | | `blast_interference` | 152 |
| `weapon_mount_incompatible` | 216 | | `barbette_too_large_for_beam` | 80 |
| `mount_unavailable_for_nation` | 108 | | `fire_control_unavailable` | 69 |
| `fire_control_unavailable` | 90 | | `magazine_volume_insufficient` | 68 |
| `turret_weight_exceeds_hull_limit` | 43 | | `topweight_limit_exceeded` | 54 |
| `armament_displacement_conflict` | 42 | | | |

### カバレッジ: 13/14

生成タスク + 批評タスクで **14個中13個**の reason code が出る。

残る1つ `armament.mount_unavailable_for_era` は **DB の欠落が原因で到達不能**。

- `mounts.jsonl` は `year_introduced` キーを持つが、**全59件が null**（実測）
- validator は `mi = mount.year_introduced or weapon.year_introduced` と
  フォールバックするので、砲が年代的に妥当な限りこの分岐は踏まれない

摂動関数 `perturb_anachronistic_mount()` は実装済みで `PERTURBATIONS` に残してあるが、
`RATIOS` からは外してある。**`mounts.jsonl` に就役年を入れれば
`RATIOS` に1行戻すだけでカバーできる。**
`tests/test_navspec_lora_data_armament_critique_stage4a.py::test_mount_year_is_missing_in_the_db`
が、就役年が入った時点で落ちて気づけるようにしてある。

砲塔の就役年は史実データなので、推測で埋めずに navweaps 等から採取すること。
