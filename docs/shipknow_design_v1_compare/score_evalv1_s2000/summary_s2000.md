# s2000

行数 350（seed [1001]）。率は % 表記（特記以外）。

## 全体（A〜E、問題×seed）

| 指標 | 値 |
|---|---:|
| JSON parse 成功率 | 100.0 |
| スキーマ合格率（全行基準） | 83.2 |
| 要求充足（問題ごとの 満たした数/判定可能数 の平均） | 77.4 |
| 要求充足（厳密: 欠落・不正は未充足） | 77.3 |
| 要求を全て満たした行 | 54.4 |
| 内部整合性の合格率（JSON 有効行） | 92.8 |
| 警告数 平均 | 0.100 |
| 5-95% に入る率（全項目、判定可能な行） | 53.8 |
| 5-95% に入る項目の割合 平均 | 84.0 |
| 実艦の丸写し（±1%） | 1.6 |
| 　うち入力の近傍艦と一致 | 75.0 |
| 同一問題の seed 間で完全一致 | - |
| 異なる問題間で同一出力 | 16.0 |

## カテゴリ別

| カテゴリ | n | JSON | schema | 要求充足(判定可能) | 全充足 | 整合性合格 | 警告数 | 5-95%全項目 | 丸写し |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 50 | 100.0 | 98.0 | 97.6 | 96.0 | 96.0 | 0.100 | 81.6 | 0.0 |
| B | 50 | 100.0 | 98.0 | 95.4 | 84.0 | 100.0 | 0.000 | 57.1 | 0.0 |
| C | 50 | 100.0 | 94.0 | 84.6 | 36.0 | 94.0 | 0.080 | 41.7 | 4.0 |
| D | 50 | 100.0 | 90.0 | 82.5 | 48.0 | 88.0 | 0.140 | 45.5 | 4.0 |
| E | 50 | 100.0 | 36.0 | 27.0 | 8.0 | 86.0 | 0.180 | 22.2 | 0.0 |

## seed 別

| seed | n | JSON | 要求充足(判定可能) | 全充足 | 整合性合格 |
|---|---:|---:|---:|---:|---:|
| 1001 | 250 | 100.0 | 77.4 | 54.4 | 92.8 |

## 制約種別ごとの充足率

| 種別 | 制約数 | 判定可能 | 充足率（判定可能分母） | 充足率（厳密） |
|---|---:|---:|---:|---:|
| aircraft_count | 24 | 24 | 33.3 | 33.3 |
| aircraft_fit | 51 | 51 | 49.0 | 49.0 |
| complement | 56 | 50 | 100.0 | 89.3 |
| displacement | 250 | 208 | 100.0 | 83.2 |
| endurance | 42 | 35 | 100.0 | 83.3 |
| gun_barrels | 50 | 50 | 52.0 | 52.0 |
| main_gun | 150 | 150 | 80.0 | 80.0 |
| propulsion | 72 | 70 | 87.1 | 84.7 |
| sensor | 125 | 125 | 68.8 | 68.8 |
| ship_type | 250 | 250 | 84.0 | 84.0 |
| ship_type_fine | 17 | 17 | 100.0 | 100.0 |
| speed | 250 | 210 | 94.3 | 79.2 |
| torpedo_tubes | 38 | 38 | 65.8 | 65.8 |
| weapon | 154 | 154 | 64.9 | 64.9 |

## フィールド別の欠損率（JSON 有効行）

| フィールド | 欠損率 |
|---|---:|
| ship_type | 16.0 |
| ship_type_fine | 16.0 |
| displacement | 16.0 |
| length_m | 16.0 |
| beam_m | 16.0 |
| draught_m | 16.4 |
| propulsion | 16.0 |
| shafts | 16.0 |
| power_hp | 16.4 |
| speed_kn | 16.0 |
| endurance_nm | 26.4 |
| complement | 16.4 |
| armour | 87.6 |
| weapons | 16.0 |
| sensors | 49.6 |
| air | 81.6 |

警告コード上位: {"ratio_high:fr": 3, "ratio_low:cb_full": 2, "ratio_low:adm": 2, "ratio_high:hp_t": 2, "ratio_high:slender": 2, "ratio_high:cb_standard": 2, "air_entry_without_air": 2, "armament_excess": 2, "era_early:ciws": 2, "type_equipment_mismatch:air": 2, "ratio_high:lb": 1, "ratio_high:bt": 1, "nuclear_small_hull": 1, "heli_hull_too_short": 1}

## E（不可能な要求）

| 指標 | 値 |
|---|---:|
| 矛盾を認識（infeasible 系の応答） | 0.0 |
| 緩和（要求を満たさない設計を返した） | 92.0 |
| 数字で埋めた（要求を全部満たすが整合性違反） | 6.0 |
| 要求を満たし整合性も合格（不可能のはずの要求に妥当な設計） | 2.0 |
| JSON 不正 | 0.0 |

## ペアテスト

組×seed 50、正答率 82.0%（A/B の両方が JSON として読めた率 100.0%、A/B が完全に同じ出力 2.0%、seed 多数決の正答率 82.0%）

| 変更種別 | n | 正答率 | A/B 同一出力 |
|---|---:|---:|---:|
| combo_cal_barrels | 1 | 0.0 | 0.0 |
| combo_disp_speed | 1 | 0.0 | 0.0 |
| combo_sam_heli | 1 | 100.0 | 0.0 |
| complement_half | 3 | 100.0 | 0.0 |
| country_change | 4 | 75.0 | 0.0 |
| disp_down25 | 5 | 80.0 | 0.0 |
| endurance_x2 | 3 | 66.7 | 0.0 |
| gun_barrels_up | 5 | 100.0 | 0.0 |
| gun_cal_up | 6 | 100.0 | 0.0 |
| heli_add | 6 | 100.0 | 0.0 |
| sam_add | 3 | 100.0 | 0.0 |
| speed_up4 | 5 | 40.0 | 20.0 |
| year_earlier20 | 4 | 75.0 | 0.0 |
| year_later20 | 3 | 100.0 | 0.0 |

| 期待方向の項目 | n | 正答率 |
|---|---:|---:|
| aviation:present_B | 6 | 100.0 |
| complement:decrease | 3 | 100.0 |
| displacement:decrease | 6 | 83.3 |
| endurance:increase | 3 | 66.7 |
| gun_barrels:increase | 6 | 83.3 |
| helicopter:present_B | 7 | 100.0 |
| main_gun_cal:increase | 7 | 100.0 |
| missiles:present_B | 3 | 100.0 |
| modern_count:decrease | 4 | 75.0 |
| modern_count:increase | 3 | 100.0 |
| power_per_disp:decrease | 1 | 100.0 |
| power_per_disp:increase | 7 | 28.6 |
| sam:present_B | 4 | 100.0 |
| speed:increase | 5 | 80.0 |
| torpedo_tubes:decrease | 2 | 100.0 |

## 多様性

seed 間 完全一致 -%、weapons Jaccard 平均 -、数値 CV 中央値 -、問題間で同一出力 16.0%、主要諸元が同じ（テンプレート） 16.0%、出力の種類数/行数 84.4%
