# design_final

行数 1050（seed [1001, 1002, 1003]）。率は % 表記（特記以外）。

## 全体（A〜E、問題×seed）

| 指標 | 値 |
|---|---:|
| JSON parse 成功率 | 100.0 |
| スキーマ合格率（全行基準） | 98.8 |
| 要求充足（問題ごとの 満たした数/判定可能数 の平均） | 92.3 |
| 要求充足（厳密: 欠落・不正は未充足） | 92.1 |
| 要求を全て満たした行 | 64.8 |
| 内部整合性の合格率（JSON 有効行） | 83.1 |
| 警告数 平均 | 0.431 |
| 5-95% に入る率（全項目、判定可能な行） | 47.2 |
| 5-95% に入る項目の割合 平均 | 80.4 |
| 実艦の丸写し（±1%） | 2.5 |
| 　うち入力の近傍艦と一致 | 15.8 |
| 同一問題の seed 間で完全一致 | 0.0 |
| 異なる問題間で同一出力 | 0.0 |

## カテゴリ別

| カテゴリ | n | JSON | schema | 要求充足(判定可能) | 全充足 | 整合性合格 | 警告数 | 5-95%全項目 | 丸写し |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 150 | 100.0 | 100.0 | 99.6 | 98.0 | 96.7 | 0.047 | 83.3 | 4.0 |
| B | 150 | 100.0 | 98.0 | 98.3 | 85.3 | 99.3 | 0.007 | 54.7 | 4.7 |
| C | 150 | 100.0 | 99.3 | 91.1 | 49.3 | 96.7 | 0.073 | 34.0 | 0.7 |
| D | 150 | 100.0 | 99.3 | 94.6 | 72.7 | 88.0 | 0.193 | 48.6 | 2.0 |
| E | 150 | 100.0 | 97.3 | 77.8 | 18.7 | 34.7 | 1.833 | 14.4 | 1.3 |

## seed 別

| seed | n | JSON | 要求充足(判定可能) | 全充足 | 整合性合格 |
|---|---:|---:|---:|---:|---:|
| 1001 | 250 | 100.0 | 92.2 | 64.8 | 83.6 |
| 1002 | 250 | 100.0 | 92.4 | 65.2 | 84.0 |
| 1003 | 250 | 100.0 | 92.3 | 64.4 | 81.6 |

## 制約種別ごとの充足率

| 種別 | 制約数 | 判定可能 | 充足率（判定可能分母） | 充足率（厳密） |
|---|---:|---:|---:|---:|
| aircraft_count | 72 | 72 | 20.8 | 20.8 |
| aircraft_fit | 153 | 153 | 66.7 | 66.7 |
| complement | 168 | 167 | 98.2 | 97.6 |
| displacement | 750 | 748 | 100.0 | 99.7 |
| endurance | 126 | 123 | 100.0 | 97.6 |
| gun_barrels | 150 | 150 | 43.3 | 43.3 |
| main_gun | 450 | 450 | 97.8 | 97.8 |
| propulsion | 216 | 215 | 95.8 | 95.4 |
| sensor | 375 | 375 | 93.6 | 93.6 |
| ship_type | 750 | 750 | 99.9 | 99.9 |
| ship_type_fine | 51 | 51 | 100.0 | 100.0 |
| speed | 750 | 746 | 100.0 | 99.5 |
| torpedo_tubes | 114 | 114 | 67.5 | 67.5 |
| weapon | 462 | 462 | 77.3 | 77.3 |

## フィールド別の欠損率（JSON 有効行）

| フィールド | 欠損率 |
|---|---:|
| ship_type | 0.1 |
| ship_type_fine | 0.1 |
| displacement | 0.1 |
| length_m | 0.1 |
| beam_m | 0.1 |
| draught_m | 0.1 |
| propulsion | 1.1 |
| shafts | 0.5 |
| power_hp | 0.5 |
| speed_kn | 0.5 |
| endurance_nm | 15.6 |
| complement | 1.7 |
| armour | 83.3 |
| weapons | 0.5 |
| sensors | 33.9 |
| air | 73.9 |

警告コード上位: {"ratio_high:fr": 39, "era_early:air_search_radar": 19, "ratio_high:adm": 18, "ratio_low:hp_t": 18, "armament_excess": 17, "ratio_low:slender": 15, "ratio_low:lb": 14, "ratio_low:cb_full": 12, "heli_hull_too_short": 11, "ratio_high:bt": 11, "type_equipment_mismatch:air": 9, "ratio_high:crew_t": 8, "air_without_entry": 8, "era_early:ssm": 8, "ratio_low:adm": 7}

## E（不可能な要求）

| 指標 | 値 |
|---|---:|
| 矛盾を認識（infeasible 系の応答） | 0.0 |
| 緩和（要求を満たさない設計を返した） | 81.3 |
| 数字で埋めた（要求を全部満たすが整合性違反） | 17.3 |
| 要求を満たし整合性も合格（不可能のはずの要求に妥当な設計） | 1.3 |
| JSON 不正 | 0.0 |

## ペアテスト

組×seed 150、正答率 83.3%（A/B の両方が JSON として読めた率 100.0%、A/B が完全に同じ出力 0.0%、seed 多数決の正答率 86.0%）

| 変更種別 | n | 正答率 | A/B 同一出力 |
|---|---:|---:|---:|
| combo_cal_barrels | 3 | 33.3 | 0.0 |
| combo_disp_speed | 3 | 100.0 | 0.0 |
| combo_sam_heli | 3 | 100.0 | 0.0 |
| complement_half | 9 | 100.0 | 0.0 |
| country_change | 12 | 41.7 | 0.0 |
| disp_down25 | 15 | 100.0 | 0.0 |
| endurance_x2 | 9 | 100.0 | 0.0 |
| gun_barrels_up | 15 | 33.3 | 0.0 |
| gun_cal_up | 18 | 100.0 | 0.0 |
| heli_add | 18 | 100.0 | 0.0 |
| sam_add | 9 | 100.0 | 0.0 |
| speed_up4 | 15 | 66.7 | 0.0 |
| year_earlier20 | 12 | 91.7 | 0.0 |
| year_later20 | 9 | 100.0 | 0.0 |

| 期待方向の項目 | n | 正答率 |
|---|---:|---:|
| aviation:present_B | 18 | 100.0 |
| complement:decrease | 9 | 100.0 |
| displacement:decrease | 18 | 100.0 |
| endurance:increase | 9 | 100.0 |
| gun_barrels:increase | 18 | 33.3 |
| helicopter:present_B | 21 | 100.0 |
| main_gun_cal:increase | 21 | 100.0 |
| missiles:present_B | 9 | 100.0 |
| modern_count:decrease | 12 | 91.7 |
| modern_count:increase | 9 | 100.0 |
| power_per_disp:decrease | 3 | 33.3 |
| power_per_disp:increase | 21 | 61.9 |
| sam:present_B | 12 | 100.0 |
| speed:increase | 15 | 100.0 |
| torpedo_tubes:decrease | 6 | 66.7 |

## 多様性

seed 間 完全一致 0.0%、weapons Jaccard 平均 0.357、数値 CV 中央値 0.045、問題間で同一出力 0.0%、主要諸元が同じ（テンプレート） 0.0%、出力の種類数/行数 100.0%
