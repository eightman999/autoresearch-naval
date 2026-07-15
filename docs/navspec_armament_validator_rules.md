# NavSpec Stage 4 — Classic Armament Validator Rules

最終更新: 2026-07-15
対象 branch: `navspec-armament-stage4`
validator: `tools/navspec_armament_validator.py`
tests: `tests/test_navspec_armament_validator.py` (17件)

---

## 1. 概要

`navspec.armament_candidate.v1` 形式の candidate を受け取り、
DB seed (`data/navspec/armament/`) を参照して物理的・時代的・構造的矛盾を検出する。

progress §15 の validator ルール (14系統) と progress §16 の reason code (14件) に基づく。

```python
from navspec_armament_validator import validate_armament, load_db

db = load_db("data/navspec/armament")
result = validate_armament(candidate, context, db)
# result = {"status": "ok"|"impossible", "issues": [...], "aggregate": {...}}
```

### 入力

- `candidate`: `navspec.armament_candidate.v1` (`main_batteries[]` を持つ)
- `context`: `{ship_type, year, nation, displacement_t, beam_m}`
- `db`: `load_db()` で読み込んだ weapons/mounts/fire_control/ammunition/compatibility_rules

### 出力

```json
{
  "status": "ok" | "impossible",
  "issues": [
    {"severity": "error"|"warn", "code": "armament.X", "message": "...", "battery_id": "main_1"}
  ],
  "aggregate": {"main_gun_caliber_mm": 460, "main_gun_count": 9, "main_gun_mount_count": 3}
}
```

`status` は1件でも `severity=error` があれば `impossible`。

---

## 2. reason code (progress §16, 全14件)

`ARMAMENT_REASON_CODES` リストで管理。progress §16 の14件すべてが `armament.*` プレフィックスを持つ。

REASON_CODES 正典 (`navspec_lora_stage2_schema.py`) とは別名前空間であり、
本 validator が独自に管理する。将来 pipeline 統合時に `classify_reason_codes` で橋渡しする想定。

| reason code | severity | 検出条件 |
| --- | --- | --- |
| `armament.weapon_unavailable_for_era` | error | `weapon.year_introduced > context.year` |
| `armament.weapon_unavailable_for_nation` | error | `weapon.nation != context.nation` |
| `armament.mount_unavailable_for_era` | error | `mount.year_introduced > context.year` (現状 mount 側は null のため weapon 準拠) |
| `armament.mount_unavailable_for_nation` | error | `mount.nation != context.nation` |
| `armament.weapon_mount_incompatible` | error | `mount_id` が `compatibility_rules.weapon_to_mount[weapon_id]` に無い、または weapon/mount がDBに無い |
| `armament.barbette_too_large_for_beam` | warn | wing 配置で `beam_m < barbette_diameter * 2 * 1.5` (barbette は推定値) |
| `armament.turret_weight_exceeds_hull_limit` | error | 単砲塔重量が `displacement * 0.06 * 2.5` (=15%) を超える |
| `armament.magazine_volume_insufficient` | warn | 要求弾薬庫容積が `displacement * 0.05` (5%) を超える |
| `armament.topweight_limit_exceeded` | warn | 全砲塔重量合計が `displacement * 0.35` (35%) を超える |
| `armament.firing_arc_conflict` | error | 無効な position / 重複 position / 中線位置の多重使用 |
| `armament.blast_interference` | warn | 同一 position に複数 mount が集中 |
| `armament.battery_layout_conflict` | error/warn | `total_barrels != mount_count * barrels_per_mount`、position 数と mount_count 不整合、中線/wing 混在、ship_type と口径不整合 |
| `armament.armament_displacement_conflict` | error | 全砲塔重量合計が `displacement * 0.5` (50%) を超える |
| `armament.fire_control_unavailable` | error/warn | `fire_control_id` がDBに無い、または radar=true で year < 1938 |

---

## 3. positions の意味 (progress schema 記載)

`positions[]` は各 mount の配置位置を示す。

| position | 意味 |
| --- | --- |
| `A`, `B` | 前部 (superfiring: A が最前、B がその後ろ上位) |
| `P`, `Q` | 中部 |
| `X`, `Y` | 後部 (X が前、Y が後ろ上位) |
| `C`, `Z` | 中線 (1 mount のみ) |

検証ルール:
- `len(positions) == mount_count` (中線 C/Z も1 mount 1 position 扱い)
- 同一 battery 内の重複禁止
- 無効な position 文字列の拒否
- 中線 (C/Z) と wing (A/B/P/Q/X/Y) の同一 battery 内混在は警告 (mount_count > 1 の場合)
- 中線 position は全 battery 通しで重複禁止
- 同一 position に複数 mount は blast_interference 警告

---

## 4. 閾値 (compatibility_rules.json と既定値)

| ルール | compatibility_rules.json キー | 既定値 |
| --- | --- | --- |
| 単砲塔重量上限 (排水量比) | `turret_weight_to_displacement.max_turret_weight_fraction_of_displacement` | 0.06 |
| topweight 上限 | `topweight.max_full_armament_weight_fraction` | 0.35 |
| 弾薬庫要求量 (1発あたり) | `magazine_volume.min_m3_per_round_default` | 0.05 m3 |
| beam/barbette 比 | `barbette_to_beam.min_beam_over_barbette_diameter` | 1.5 |
| ship_type 別口径範囲 | `caliber_min_max_by_ship_type` | battleship 305-460 / destroyer 100-140 等 |

`barbette_diameter_m` が mount 側で null の場合、`_estimate_barbette_diameter_m()` が
`口径(m) * 28 * barrels補正` で推定する (大和型 460mm triple → 12.88m、実測約13m)。

---

## 5. 集約値導出 (progress §3)

candidate から ShipSpecRecord の集約 `armament/equipment` フィールドを導出する:

```python
main_gun_caliber_mm = max(weapon.caliber_mm for battery in main_batteries)
main_gun_count = sum(mount_count * barrels_per_mount)
main_gun_mount_count = sum(mount_count)
```

`aggregate_main_gun_fields()` (DB 不要) と `aggregate_main_caliber_with_db()` (DB 参照で caliber 取得) の2関数。

---

## 6. 既存 validator との関係

- `tools/navspec_contradiction.py` (383行): 要求制約の矛盾検出 (`check_contradiction`)。
  **armament validator は別レイヤー** で candidate の検証を担当。並存。
- `tools/navspec_equipment.validate_equipment`: equipment_plan() の事後検証。
  armament validator は詳細装備 DB を参照する点が異なる。
- reason code 3系統: schema enum / `navspec_lora_stage2_schema.py:REASON_CODES` 正典 /
  `navspec_contradiction.py` 日本語リスト。armament validator は4系統目 (`armament.*`) を追加。

---

## 7. テスト (progress §23 全10件 + サブテスト)

`tests/test_navspec_armament_validator.py` に17件を実装:

| progress §23 テスト名 | 実装テスト |
| --- | --- |
| `test_weapon_schema` | weapons.jsonl 全レコードの必須フィールド検証 |
| `test_mount_schema` | mounts.jsonl 全レコードの必須フィールド + 参照整合性 |
| `test_weapon_mount_compatibility` | `_ok` (正常系) / `_incompatible` (異なる weapon の mount) |
| `test_barbette_beam_rule` | `_warning` (beam 8m に 460mm 砲塔) |
| `test_turret_weight_rule` | `_exceeds` (5000t 船体に 2730t 砲塔) |
| `test_magazine_volume_rule` | `_warning` (ammo_per_gun=10000 の異常値) |
| `test_position_validation` | `_ok` / `_duplicate` (A,A,X) / `_invalid` (無効 position W) |
| `test_aggregate_main_gun_fields` | `aggregate_main_gun_fields` + `aggregate_main_caliber_with_db` |
| `test_armament_reason_codes` | `_count` (14件) / `_prefix` (armament.* ) / `_emitted` (validator 出力が全て正典に含まれる) |
| `test_armament_pipeline_fallback` | `_unknown_weapon` (DBに無い weapon_id) / `_empty_batteries` (空配列) |

実行: `uv run python -m pytest tests/test_navspec_armament_validator.py -v` → 17 passed

---

## 8. CLI 使用法

```bash
uv run python tools/navspec_armament_validator.py candidate.json \
  --year 1941 --nation JP --ship-type battleship \
  --displacement-t 65000 --beam-m 38.0
```

exit code: 0 (ok) / 1 (impossible)

---

## 9. 既知の制限・今後の拡張 (Commit C-E で対応)

- mount の `barbette_diameter_m` / `turret_armor` / `crew` 等は null。barbette は口径から推定しているが、精度検証が必要。
- `magazine_volume_m3` も null のため、弾薬庫容積は要求量ベースで警告するのみ。
- `fire_control_compatibility` の radar 判定は year < 1938 のみ。国別 radar 導入年 (`tier_limits.radar_from_year`) との連携は今後。
- blast_interference は position 重複ベースの簡易判定。実際の砲塔間隔計算は未実装。
- Commit C (retriever) で candidate を自動生成し、本 validator とのパイプラインを確立する。
