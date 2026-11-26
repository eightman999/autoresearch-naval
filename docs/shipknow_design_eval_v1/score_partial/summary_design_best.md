# design_best

行数 1050（seed [1001, 1002, 1003]）。率は % 表記（特記以外）。

## 全体（A〜E、問題×seed）

| 指標 | 値 |
|---|---:|
| JSON parse 成功率 | 100.0 |
| スキーマ合格率（全行基準） | 99.1 |
| 要求充足（問題ごとの 満たした数/判定可能数 の平均） | 92.1 |
| 要求充足（厳密: 欠落・不正は未充足） | 92.0 |
| 要求を全て満たした行 | 64.1 |
| 内部整合性の合格率（JSON 有効行） | 82.8 |
| 警告数 平均 | 0.421 |
| 5-95% に入る率（全項目、判定可能な行） | 47.2 |
| 5-95% に入る項目の割合 平均 | 80.8 |
| 実艦の丸写し（±1%） | 2.5 |
| 　うち入力の近傍艦と一致 | 31.6 |
| 同一問題の seed 間で完全一致 | 0.0 |
| 異なる問題間で同一出力 | 0.0 |

## カテゴリ別

| カテゴリ | n | JSON | schema | 要求充足(判定可能) | 全充足 | 整合性合格 | 警告数 | 5-95%全項目 | 丸写し |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 150 | 100.0 | 100.0 | 99.7 | 98.7 | 97.3 | 0.040 | 82.7 | 0.7 |
| B | 150 | 100.0 | 98.7 | 98.2 | 86.0 | 96.7 | 0.053 | 54.7 | 6.0 |
| C | 150 | 100.0 | 99.3 | 90.8 | 48.7 | 96.0 | 0.053 | 30.7 | 1.3 |
| D | 150 | 100.0 | 98.7 | 94.0 | 68.7 | 90.0 | 0.153 | 55.6 | 2.0 |
| E | 150 | 100.0 | 98.7 | 78.0 | 18.7 | 34.0 | 1.807 | 12.2 | 2.7 |

## seed 別

| seed | n | JSON | 要求充足(判定可能) | 全充足 | 整合性合格 |
|---|---:|---:|---:|---:|---:|
| 1001 | 250 | 100.0 | 92.0 | 63.2 | 82.4 |
| 1002 | 250 | 100.0 | 91.8 | 62.8 | 83.6 |
| 1003 | 250 | 100.0 | 92.6 | 66.4 | 82.4 |

## 制約種別ごとの充足率

| 種別 | 制約数 | 判定可能 | 充足率（判定可能分母） | 充足率（厳密） |
|---|---:|---:|---:|---:|
| aircraft_count | 72 | 72 | 19.4 | 19.4 |
| aircraft_fit | 153 | 153 | 66.7 | 66.7 |
| complement | 168 | 168 | 98.8 | 98.8 |
| displacement | 750 | 748 | 100.0 | 99.7 |
| endurance | 126 | 124 | 100.0 | 98.4 |
| gun_barrels | 150 | 150 | 44.7 | 44.7 |
| main_gun | 450 | 450 | 98.0 | 98.0 |
| propulsion | 216 | 215 | 96.7 | 96.3 |
| sensor | 375 | 375 | 89.3 | 89.3 |
| ship_type | 750 | 750 | 100.0 | 100.0 |
| ship_type_fine | 51 | 51 | 100.0 | 100.0 |
| speed | 750 | 747 | 100.0 | 99.6 |
| torpedo_tubes | 114 | 114 | 71.0 | 71.0 |
| weapon | 462 | 462 | 76.6 | 76.6 |

## フィールド別の欠損率（JSON 有効行）

| フィールド | 欠損率 |
|---|---:|
| ship_type | 0.0 |
| ship_type_fine | 0.0 |
| displacement | 0.0 |
| length_m | 0.0 |
| beam_m | 0.3 |
| draught_m | 0.1 |
| propulsion | 0.7 |
| shafts | 0.4 |
| power_hp | 0.7 |
| speed_kn | 0.4 |
| endurance_nm | 18.4 |
| complement | 1.7 |
| armour | 84.3 |
| weapons | 0.1 |
| sensors | 34.7 |
| air | 74.9 |

警告コード上位: {"ratio_high:fr": 35, "ratio_high:adm": 22, "ratio_low:hp_t": 21, "era_early:air_search_radar": 19, "armament_excess": 16, "ratio_high:cb_full": 15, "ratio_high:bt": 14, "ratio_low:slender": 13, "ratio_low:cb_full": 12, "heli_hull_too_short": 10, "type_equipment_mismatch:air": 10, "ratio_high:slender": 8, "air_without_entry": 8, "ratio_low:lb": 8, "ratio_high:crew_t": 8}

## E（不可能な要求）

| 指標 | 値 |
|---|---:|
| 矛盾を認識（infeasible 系の応答） | 0.0 |
| 緩和（要求を満たさない設計を返した） | 81.3 |
| 数字で埋めた（要求を全部満たすが整合性違反） | 16.7 |
| 要求を満たし整合性も合格（不可能のはずの要求に妥当な設計） | 2.0 |
| JSON 不正 | 0.0 |

## ペアテスト

組×seed 150、正答率 88.7%（A/B の両方が JSON として読めた率 100.0%、A/B が完全に同じ出力 0.0%、seed 多数決の正答率 90.0%）

| 変更種別 | n | 正答率 | A/B 同一出力 |
|---|---:|---:|---:|
| combo_cal_barrels | 3 | 33.3 | 0.0 |
| combo_disp_speed | 3 | 100.0 | 0.0 |
| combo_sam_heli | 3 | 100.0 | 0.0 |
| complement_half | 9 | 100.0 | 0.0 |
| country_change | 12 | 75.0 | 0.0 |
| disp_down25 | 15 | 100.0 | 0.0 |
| endurance_x2 | 9 | 100.0 | 0.0 |
| gun_barrels_up | 15 | 46.7 | 0.0 |
| gun_cal_up | 18 | 100.0 | 0.0 |
| heli_add | 18 | 100.0 | 0.0 |
| sam_add | 9 | 100.0 | 0.0 |
| speed_up4 | 15 | 80.0 | 0.0 |
| year_earlier20 | 12 | 91.7 | 0.0 |
| year_later20 | 9 | 100.0 | 0.0 |

| 期待方向の項目 | n | 正答率 |
|---|---:|---:|
| aviation:present_B | 18 | 100.0 |
| complement:decrease | 9 | 100.0 |
| displacement:decrease | 18 | 100.0 |
| endurance:increase | 9 | 100.0 |
| gun_barrels:increase | 18 | 44.4 |
| helicopter:present_B | 21 | 100.0 |
| main_gun_cal:increase | 21 | 100.0 |
| missiles:present_B | 9 | 100.0 |
| modern_count:decrease | 12 | 91.7 |
| modern_count:increase | 9 | 100.0 |
| power_per_disp:decrease | 3 | 66.7 |
| power_per_disp:increase | 21 | 76.2 |
| sam:present_B | 12 | 100.0 |
| speed:increase | 15 | 100.0 |
| torpedo_tubes:decrease | 6 | 100.0 |

## 多様性

seed 間 完全一致 0.0%、weapons Jaccard 平均 0.355、数値 CV 中央値 0.045、問題間で同一出力 0.0%、主要諸元が同じ（テンプレート） 0.3%、出力の種類数/行数 100.0%
