# 1shot_design_best

行数 1050（seed [1001, 1002, 1003]）。率は % 表記（特記以外）。

## 全体（A〜E、問題×seed）

| 指標 | 値 |
|---|---:|
| JSON parse 成功率 | 99.9 |
| スキーマ合格率（全行基準） | 99.3 |
| 要求充足（問題ごとの 満たした数/判定可能数 の平均） | 59.1 |
| 要求充足（厳密: 欠落・不正は未充足） | 58.0 |
| 要求を全て満たした行 | 6.8 |
| 内部整合性の合格率（JSON 有効行） | 57.7 |
| 警告数 平均 | 1.414 |
| 5-95% に入る率（全項目、判定可能な行） | 3.4 |
| 5-95% に入る項目の割合 平均 | 34.8 |
| 実艦の丸写し（±1%） | 0.1 |
| 　うち入力の近傍艦と一致 | 0.0 |
| 同一問題の seed 間で完全一致 | 0.0 |
| 異なる問題間で同一出力 | 0.0 |

## カテゴリ別

| カテゴリ | n | JSON | schema | 要求充足(判定可能) | 全充足 | 整合性合格 | 警告数 | 5-95%全項目 | 丸写し |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 150 | 100.0 | 100.0 | 58.8 | 14.0 | 46.7 | 1.940 | 4.0 | 0.0 |
| B | 150 | 100.0 | 100.0 | 66.0 | 10.0 | 46.0 | 1.867 | 6.0 | 0.0 |
| C | 150 | 99.3 | 99.3 | 66.1 | 7.3 | 72.5 | 0.852 | 4.7 | 0.7 |
| D | 150 | 100.0 | 98.7 | 59.5 | 2.0 | 75.3 | 0.560 | 2.1 | 0.0 |
| E | 150 | 100.0 | 98.7 | 44.9 | 0.7 | 48.0 | 1.847 | 0.0 | 0.0 |

## seed 別

| seed | n | JSON | 要求充足(判定可能) | 全充足 | 整合性合格 |
|---|---:|---:|---:|---:|---:|
| 1001 | 250 | 100.0 | 58.7 | 5.6 | 58.0 |
| 1002 | 250 | 100.0 | 59.4 | 7.6 | 58.8 |
| 1003 | 250 | 99.6 | 59.0 | 7.2 | 56.2 |

## 制約種別ごとの充足率

| 種別 | 制約数 | 判定可能 | 充足率（判定可能分母） | 充足率（厳密） |
|---|---:|---:|---:|---:|
| aircraft_count | 72 | 72 | 25.0 | 25.0 |
| aircraft_fit | 153 | 153 | 54.9 | 54.9 |
| complement | 168 | 168 | 55.4 | 55.4 |
| displacement | 749 | 669 | 31.2 | 27.9 |
| endurance | 126 | 126 | 100.0 | 100.0 |
| gun_barrels | 150 | 150 | 50.0 | 50.0 |
| main_gun | 450 | 450 | 66.4 | 66.4 |
| propulsion | 214 | 214 | 72.0 | 72.0 |
| sensor | 373 | 373 | 78.5 | 78.5 |
| ship_type | 749 | 749 | 30.3 | 30.3 |
| ship_type_fine | 51 | 51 | 31.4 | 31.4 |
| speed | 749 | 747 | 85.8 | 85.6 |
| torpedo_tubes | 113 | 113 | 81.4 | 81.4 |
| weapon | 462 | 462 | 77.7 | 77.7 |

## フィールド別の欠損率（JSON 有効行）

| フィールド | 欠損率 |
|---|---:|
| ship_type | 0.3 |
| ship_type_fine | 0.3 |
| displacement | 0.3 |
| length_m | 0.3 |
| beam_m | 0.3 |
| draught_m | 0.3 |
| propulsion | 0.4 |
| shafts | 0.3 |
| power_hp | 0.4 |
| speed_kn | 0.3 |
| endurance_nm | 6.4 |
| complement | 0.4 |
| armour | 84.8 |
| weapons | 0.3 |
| sensors | 7.5 |
| air | 77.3 |

警告コード上位: {"era_early:sonar": 94, "ratio_low:hp_t": 73, "era_early:asw_weapon": 67, "ratio_high:adm": 66, "ratio_low:crew_t": 60, "era_early:depth_charge": 59, "ratio_low:slender": 51, "armament_excess": 49, "gun_too_big_for_type:weapons": 44, "era_early:prop:steam_turbine": 43, "ratio_low:lb": 39, "ratio_high:cb_normal": 33, "ratio_high:fr": 28, "ratio_high:cb_full": 26, "type_displacement_mismatch": 26}

## E（不可能な要求）

| 指標 | 値 |
|---|---:|
| 矛盾を認識（infeasible 系の応答） | 0.0 |
| 緩和（要求を満たさない設計を返した） | 99.3 |
| 数字で埋めた（要求を全部満たすが整合性違反） | 0.7 |
| 要求を満たし整合性も合格（不可能のはずの要求に妥当な設計） | 0.0 |
| JSON 不正 | 0.0 |

## ペアテスト

組×seed 150、正答率 60.7%（A/B の両方が JSON として読めた率 100.0%、A/B が完全に同じ出力 0.0%、seed 多数決の正答率 62.0%）

| 変更種別 | n | 正答率 | A/B 同一出力 |
|---|---:|---:|---:|
| combo_cal_barrels | 3 | 0.0 | 0.0 |
| combo_disp_speed | 3 | 0.0 | 0.0 |
| combo_sam_heli | 3 | 33.3 | 0.0 |
| complement_half | 9 | 66.7 | 0.0 |
| country_change | 12 | 41.7 | 0.0 |
| disp_down25 | 15 | 53.3 | 0.0 |
| endurance_x2 | 9 | 100.0 | 0.0 |
| gun_barrels_up | 15 | 40.0 | 0.0 |
| gun_cal_up | 18 | 55.6 | 0.0 |
| heli_add | 18 | 77.8 | 0.0 |
| sam_add | 9 | 100.0 | 0.0 |
| speed_up4 | 15 | 26.7 | 0.0 |
| year_earlier20 | 12 | 83.3 | 0.0 |
| year_later20 | 9 | 100.0 | 0.0 |

| 期待方向の項目 | n | 正答率 |
|---|---:|---:|
| aviation:present_B | 18 | 77.8 |
| complement:decrease | 9 | 66.7 |
| displacement:decrease | 18 | 44.4 |
| endurance:increase | 9 | 100.0 |
| gun_barrels:increase | 18 | 44.4 |
| helicopter:present_B | 21 | 71.4 |
| main_gun_cal:increase | 21 | 47.6 |
| missiles:present_B | 9 | 100.0 |
| modern_count:decrease | 12 | 83.3 |
| modern_count:increase | 9 | 100.0 |
| power_per_disp:decrease | 3 | 66.7 |
| power_per_disp:increase | 21 | 33.3 |
| sam:present_B | 12 | 100.0 |
| speed:increase | 15 | 40.0 |
| torpedo_tubes:decrease | 6 | 50.0 |

## 多様性

seed 間 完全一致 0.0%、weapons Jaccard 平均 0.197、数値 CV 中央値 0.049、問題間で同一出力 0.0%、主要諸元が同じ（テンプレート） 1.6%、出力の種類数/行数 100.0%
