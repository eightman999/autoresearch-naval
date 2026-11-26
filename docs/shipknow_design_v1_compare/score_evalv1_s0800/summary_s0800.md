# s0800

行数 350（seed [1001]）。率は % 表記（特記以外）。

## 全体（A〜E、問題×seed）

| 指標 | 値 |
|---|---:|
| JSON parse 成功率 | 100.0 |
| スキーマ合格率（全行基準） | 77.2 |
| 要求充足（問題ごとの 満たした数/判定可能数 の平均） | 72.6 |
| 要求充足（厳密: 欠落・不正は未充足） | 72.5 |
| 要求を全て満たした行 | 55.2 |
| 内部整合性の合格率（JSON 有効行） | 92.0 |
| 警告数 平均 | 0.132 |
| 5-95% に入る率（全項目、判定可能な行） | 42.7 |
| 5-95% に入る項目の割合 平均 | 80.2 |
| 実艦の丸写し（±1%） | 0.4 |
| 　うち入力の近傍艦と一致 | 100.0 |
| 同一問題の seed 間で完全一致 | - |
| 異なる問題間で同一出力 | 21.6 |

## カテゴリ別

| カテゴリ | n | JSON | schema | 要求充足(判定可能) | 全充足 | 整合性合格 | 警告数 | 5-95%全項目 | 丸写し |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 50 | 100.0 | 92.0 | 90.8 | 88.0 | 96.0 | 0.040 | 71.7 | 0.0 |
| B | 50 | 100.0 | 86.0 | 84.3 | 70.0 | 96.0 | 0.120 | 50.0 | 0.0 |
| C | 50 | 100.0 | 92.0 | 85.8 | 52.0 | 98.0 | 0.020 | 30.4 | 0.0 |
| D | 50 | 100.0 | 82.0 | 76.3 | 56.0 | 90.0 | 0.160 | 30.8 | 2.0 |
| E | 50 | 100.0 | 34.0 | 25.7 | 10.0 | 80.0 | 0.320 | 5.9 | 0.0 |

## seed 別

| seed | n | JSON | 要求充足(判定可能) | 全充足 | 整合性合格 |
|---|---:|---:|---:|---:|---:|
| 1001 | 250 | 100.0 | 72.6 | 55.2 | 92.0 |

## 制約種別ごとの充足率

| 種別 | 制約数 | 判定可能 | 充足率（判定可能分母） | 充足率（厳密） |
|---|---:|---:|---:|---:|
| aircraft_count | 24 | 24 | 29.2 | 29.2 |
| aircraft_fit | 51 | 51 | 45.1 | 45.1 |
| complement | 56 | 49 | 98.0 | 85.7 |
| displacement | 250 | 194 | 100.0 | 77.6 |
| endurance | 42 | 32 | 100.0 | 76.2 |
| gun_barrels | 50 | 50 | 42.0 | 42.0 |
| main_gun | 150 | 150 | 76.7 | 76.7 |
| propulsion | 72 | 65 | 98.5 | 88.9 |
| sensor | 125 | 125 | 64.0 | 64.0 |
| ship_type | 250 | 250 | 77.6 | 77.6 |
| ship_type_fine | 17 | 17 | 82.3 | 82.3 |
| speed | 250 | 193 | 99.0 | 76.4 |
| torpedo_tubes | 38 | 38 | 71.0 | 71.0 |
| weapon | 154 | 154 | 63.0 | 63.0 |

## フィールド別の欠損率（JSON 有効行）

| フィールド | 欠損率 |
|---|---:|
| ship_type | 22.4 |
| ship_type_fine | 22.4 |
| displacement | 22.4 |
| length_m | 22.4 |
| beam_m | 22.8 |
| draught_m | 22.8 |
| propulsion | 22.8 |
| shafts | 22.8 |
| power_hp | 22.8 |
| speed_kn | 22.8 |
| endurance_nm | 35.2 |
| complement | 24.4 |
| armour | 86.4 |
| weapons | 22.8 |
| sensors | 56.0 |
| air | 83.2 |

警告コード上位: {"armament_excess": 4, "ratio_high:fr": 3, "gun_too_big_for_displacement:weapons": 2, "ratio_low:adm": 2, "ratio_high:hp_t": 2, "era_early:prop:nuclear": 2, "era_early:ciws": 2, "ratio_low:cb_full": 1, "ratio_low:slender": 1, "gun_too_big_for_type:weapons": 1, "type_equipment_mismatch:weapons": 1, "ratio_high:adm": 1, "ratio_low:hp_t": 1, "ratio_high:cb_full": 1, "ratio_high:slender": 1}

## E（不可能な要求）

| 指標 | 値 |
|---|---:|
| 矛盾を認識（infeasible 系の応答） | 2.0 |
| 緩和（要求を満たさない設計を返した） | 88.0 |
| 数字で埋めた（要求を全部満たすが整合性違反） | 8.0 |
| 要求を満たし整合性も合格（不可能のはずの要求に妥当な設計） | 2.0 |
| JSON 不正 | 0.0 |

## ペアテスト

組×seed 50、正答率 76.0%（A/B の両方が JSON として読めた率 100.0%、A/B が完全に同じ出力 6.0%、seed 多数決の正答率 76.0%）

| 変更種別 | n | 正答率 | A/B 同一出力 |
|---|---:|---:|---:|
| combo_cal_barrels | 1 | 0.0 | 0.0 |
| combo_disp_speed | 1 | 0.0 | 0.0 |
| combo_sam_heli | 1 | 100.0 | 0.0 |
| complement_half | 3 | 100.0 | 0.0 |
| country_change | 4 | 75.0 | 0.0 |
| disp_down25 | 5 | 100.0 | 0.0 |
| endurance_x2 | 3 | 66.7 | 33.3 |
| gun_barrels_up | 5 | 60.0 | 0.0 |
| gun_cal_up | 6 | 100.0 | 0.0 |
| heli_add | 6 | 100.0 | 0.0 |
| sam_add | 3 | 100.0 | 0.0 |
| speed_up4 | 5 | 60.0 | 20.0 |
| year_earlier20 | 4 | 25.0 | 25.0 |
| year_later20 | 3 | 66.7 | 0.0 |

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
| modern_count:decrease | 4 | 25.0 |
| modern_count:increase | 3 | 66.7 |
| power_per_disp:decrease | 1 | 0.0 |
| power_per_disp:increase | 7 | 57.1 |
| sam:present_B | 4 | 100.0 |
| speed:increase | 5 | 80.0 |
| torpedo_tubes:decrease | 2 | 100.0 |

## 多様性

seed 間 完全一致 -%、weapons Jaccard 平均 -、数値 CV 中央値 -、問題間で同一出力 21.6%、主要諸元が同じ（テンプレート） 22.4%、出力の種類数/行数 78.8%
