# s1200

行数 350（seed [1001]）。率は % 表記（特記以外）。

## 全体（A〜E、問題×seed）

| 指標 | 値 |
|---|---:|
| JSON parse 成功率 | 100.0 |
| スキーマ合格率（全行基準） | 81.2 |
| 要求充足（問題ごとの 満たした数/判定可能数 の平均） | 76.2 |
| 要求充足（厳密: 欠落・不正は未充足） | 76.1 |
| 要求を全て満たした行 | 56.4 |
| 内部整合性の合格率（JSON 有効行） | 89.6 |
| 警告数 平均 | 0.148 |
| 5-95% に入る率（全項目、判定可能な行） | 53.7 |
| 5-95% に入る項目の割合 平均 | 83.9 |
| 実艦の丸写し（±1%） | 1.2 |
| 　うち入力の近傍艦と一致 | 33.3 |
| 同一問題の seed 間で完全一致 | - |
| 異なる問題間で同一出力 | 16.4 |

## カテゴリ別

| カテゴリ | n | JSON | schema | 要求充足(判定可能) | 全充足 | 整合性合格 | 警告数 | 5-95%全項目 | 丸写し |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 50 | 100.0 | 98.0 | 98.0 | 98.0 | 94.0 | 0.080 | 83.7 | 2.0 |
| B | 50 | 100.0 | 94.0 | 92.6 | 84.0 | 98.0 | 0.020 | 55.3 | 2.0 |
| C | 50 | 100.0 | 88.0 | 81.0 | 44.0 | 92.0 | 0.100 | 31.8 | 0.0 |
| D | 50 | 100.0 | 86.0 | 79.1 | 50.0 | 84.0 | 0.240 | 56.1 | 2.0 |
| E | 50 | 100.0 | 40.0 | 30.1 | 6.0 | 80.0 | 0.300 | 20.0 | 0.0 |

## seed 別

| seed | n | JSON | 要求充足(判定可能) | 全充足 | 整合性合格 |
|---|---:|---:|---:|---:|---:|
| 1001 | 250 | 100.0 | 76.2 | 56.4 | 89.6 |

## 制約種別ごとの充足率

| 種別 | 制約数 | 判定可能 | 充足率（判定可能分母） | 充足率（厳密） |
|---|---:|---:|---:|---:|
| aircraft_count | 24 | 24 | 16.7 | 16.7 |
| aircraft_fit | 51 | 51 | 51.0 | 51.0 |
| complement | 56 | 50 | 100.0 | 89.3 |
| displacement | 250 | 202 | 100.0 | 80.8 |
| endurance | 42 | 37 | 100.0 | 88.1 |
| gun_barrels | 50 | 50 | 56.0 | 56.0 |
| main_gun | 150 | 150 | 78.7 | 78.7 |
| propulsion | 72 | 66 | 86.4 | 79.2 |
| sensor | 125 | 125 | 64.0 | 64.0 |
| ship_type | 250 | 250 | 81.2 | 81.2 |
| ship_type_fine | 17 | 17 | 94.1 | 94.1 |
| speed | 250 | 203 | 97.0 | 78.8 |
| torpedo_tubes | 38 | 38 | 73.7 | 73.7 |
| weapon | 154 | 154 | 61.0 | 61.0 |

## フィールド別の欠損率（JSON 有効行）

| フィールド | 欠損率 |
|---|---:|
| ship_type | 18.8 |
| ship_type_fine | 18.8 |
| displacement | 18.8 |
| length_m | 18.8 |
| beam_m | 18.8 |
| draught_m | 18.8 |
| propulsion | 19.2 |
| shafts | 18.8 |
| power_hp | 18.8 |
| speed_kn | 18.8 |
| endurance_nm | 27.2 |
| complement | 21.2 |
| armour | 86.8 |
| weapons | 18.8 |
| sensors | 48.4 |
| air | 80.8 |

警告コード上位: {"ratio_high:fr": 5, "ratio_high:cb_full": 3, "ratio_high:cb_normal": 3, "air_without_entry": 3, "era_early:air:helicopter": 3, "ratio_high:adm": 2, "ratio_high:hp_t": 2, "type_equipment_mismatch:air": 2, "armament_excess": 2, "ratio_low:lb": 2, "era_early:ssm": 2, "ratio_low:hp_t": 1, "era_early:combat_system": 1, "era_early:decoy": 1, "ratio_low:cb_standard": 1}

## E（不可能な要求）

| 指標 | 値 |
|---|---:|
| 矛盾を認識（infeasible 系の応答） | 0.0 |
| 緩和（要求を満たさない設計を返した） | 94.0 |
| 数字で埋めた（要求を全部満たすが整合性違反） | 4.0 |
| 要求を満たし整合性も合格（不可能のはずの要求に妥当な設計） | 2.0 |
| JSON 不正 | 0.0 |

## ペアテスト

組×seed 50、正答率 80.0%（A/B の両方が JSON として読めた率 100.0%、A/B が完全に同じ出力 4.0%、seed 多数決の正答率 80.0%）

| 変更種別 | n | 正答率 | A/B 同一出力 |
|---|---:|---:|---:|
| combo_cal_barrels | 1 | 100.0 | 0.0 |
| combo_disp_speed | 1 | 0.0 | 0.0 |
| combo_sam_heli | 1 | 100.0 | 0.0 |
| complement_half | 3 | 100.0 | 0.0 |
| country_change | 4 | 75.0 | 0.0 |
| disp_down25 | 5 | 100.0 | 0.0 |
| endurance_x2 | 3 | 66.7 | 33.3 |
| gun_barrels_up | 5 | 40.0 | 0.0 |
| gun_cal_up | 6 | 100.0 | 0.0 |
| heli_add | 6 | 100.0 | 0.0 |
| sam_add | 3 | 100.0 | 0.0 |
| speed_up4 | 5 | 60.0 | 20.0 |
| year_earlier20 | 4 | 50.0 | 0.0 |
| year_later20 | 3 | 100.0 | 0.0 |

| 期待方向の項目 | n | 正答率 |
|---|---:|---:|
| aviation:present_B | 6 | 100.0 |
| complement:decrease | 3 | 100.0 |
| displacement:decrease | 6 | 100.0 |
| endurance:increase | 3 | 66.7 |
| gun_barrels:increase | 6 | 50.0 |
| helicopter:present_B | 7 | 100.0 |
| main_gun_cal:increase | 7 | 100.0 |
| missiles:present_B | 3 | 100.0 |
| modern_count:decrease | 4 | 50.0 |
| modern_count:increase | 3 | 100.0 |
| power_per_disp:decrease | 1 | 100.0 |
| power_per_disp:increase | 7 | 42.9 |
| sam:present_B | 4 | 100.0 |
| speed:increase | 5 | 80.0 |
| torpedo_tubes:decrease | 2 | 100.0 |

## 多様性

seed 間 完全一致 -%、weapons Jaccard 平均 -、数値 CV 中央値 -、問題間で同一出力 16.4%、主要諸元が同じ（テンプレート） 18.8%、出力の種類数/行数 84.0%
