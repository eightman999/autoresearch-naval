# s0500

行数 350（seed [1001]）。率は % 表記（特記以外）。

## 全体（A〜E、問題×seed）

| 指標 | 値 |
|---|---:|
| JSON parse 成功率 | 100.0 |
| スキーマ合格率（全行基準） | 84.4 |
| 要求充足（問題ごとの 満たした数/判定可能数 の平均） | 80.0 |
| 要求充足（厳密: 欠落・不正は未充足） | 80.0 |
| 要求を全て満たした行 | 62.4 |
| 内部整合性の合格率（JSON 有効行） | 88.0 |
| 警告数 平均 | 0.220 |
| 5-95% に入る率（全項目、判定可能な行） | 50.5 |
| 5-95% に入る項目の割合 平均 | 83.2 |
| 実艦の丸写し（±1%） | 1.2 |
| 　うち入力の近傍艦と一致 | 0.0 |
| 同一問題の seed 間で完全一致 | - |
| 異なる問題間で同一出力 | 14.0 |

## カテゴリ別

| カテゴリ | n | JSON | schema | 要求充足(判定可能) | 全充足 | 整合性合格 | 警告数 | 5-95%全項目 | 丸写し |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 50 | 100.0 | 90.0 | 90.0 | 90.0 | 92.0 | 0.100 | 80.0 | 2.0 |
| B | 50 | 100.0 | 100.0 | 98.6 | 90.0 | 96.0 | 0.060 | 54.0 | 2.0 |
| C | 50 | 100.0 | 92.0 | 85.6 | 54.0 | 96.0 | 0.060 | 34.8 | 2.0 |
| D | 50 | 100.0 | 96.0 | 90.0 | 66.0 | 86.0 | 0.220 | 54.4 | 0.0 |
| E | 50 | 100.0 | 44.0 | 36.0 | 12.0 | 70.0 | 0.660 | 4.8 | 0.0 |

## seed 別

| seed | n | JSON | 要求充足(判定可能) | 全充足 | 整合性合格 |
|---|---:|---:|---:|---:|---:|
| 1001 | 250 | 100.0 | 80.0 | 62.4 | 88.0 |

## 制約種別ごとの充足率

| 種別 | 制約数 | 判定可能 | 充足率（判定可能分母） | 充足率（厳密） |
|---|---:|---:|---:|---:|
| aircraft_count | 24 | 24 | 45.8 | 45.8 |
| aircraft_fit | 51 | 51 | 60.8 | 60.8 |
| complement | 56 | 52 | 98.1 | 91.1 |
| displacement | 250 | 211 | 100.0 | 84.4 |
| endurance | 42 | 39 | 100.0 | 92.9 |
| gun_barrels | 50 | 50 | 54.0 | 54.0 |
| main_gun | 150 | 150 | 86.0 | 86.0 |
| propulsion | 72 | 65 | 95.4 | 86.1 |
| sensor | 125 | 125 | 74.4 | 74.4 |
| ship_type | 250 | 250 | 84.4 | 84.4 |
| ship_type_fine | 17 | 17 | 100.0 | 100.0 |
| speed | 250 | 211 | 99.1 | 83.6 |
| torpedo_tubes | 38 | 38 | 71.0 | 71.0 |
| weapon | 154 | 154 | 72.7 | 72.7 |

## フィールド別の欠損率（JSON 有効行）

| フィールド | 欠損率 |
|---|---:|
| ship_type | 15.6 |
| ship_type_fine | 15.6 |
| displacement | 15.6 |
| length_m | 15.6 |
| beam_m | 15.6 |
| draught_m | 15.6 |
| propulsion | 15.6 |
| shafts | 15.6 |
| power_hp | 15.6 |
| speed_kn | 15.6 |
| endurance_nm | 29.2 |
| complement | 15.6 |
| armour | 88.4 |
| weapons | 15.6 |
| sensors | 44.0 |
| air | 78.4 |

警告コード上位: {"ratio_high:fr": 5, "ratio_high:adm": 4, "type_equipment_mismatch:air": 4, "ratio_low:hp_t": 3, "ratio_high:hp_t": 3, "ratio_low:cb_full": 3, "ratio_low:slender": 3, "era_early:air_search_radar": 3, "era_early:sam": 3, "ratio_low:bt": 2, "era_early:ciws": 2, "ratio_low:adm": 2, "ratio_low:cb_standard": 2, "heli_hull_too_short": 2, "era_early:ssm": 2}

## E（不可能な要求）

| 指標 | 値 |
|---|---:|
| 矛盾を認識（infeasible 系の応答） | 2.0 |
| 緩和（要求を満たさない設計を返した） | 86.0 |
| 数字で埋めた（要求を全部満たすが整合性違反） | 10.0 |
| 要求を満たし整合性も合格（不可能のはずの要求に妥当な設計） | 2.0 |
| JSON 不正 | 0.0 |

## ペアテスト

組×seed 50、正答率 74.0%（A/B の両方が JSON として読めた率 100.0%、A/B が完全に同じ出力 2.0%、seed 多数決の正答率 74.0%）

| 変更種別 | n | 正答率 | A/B 同一出力 |
|---|---:|---:|---:|
| combo_cal_barrels | 1 | 0.0 | 0.0 |
| combo_disp_speed | 1 | 0.0 | 0.0 |
| combo_sam_heli | 1 | 100.0 | 0.0 |
| complement_half | 3 | 66.7 | 0.0 |
| country_change | 4 | 50.0 | 0.0 |
| disp_down25 | 5 | 100.0 | 0.0 |
| endurance_x2 | 3 | 66.7 | 33.3 |
| gun_barrels_up | 5 | 40.0 | 0.0 |
| gun_cal_up | 6 | 100.0 | 0.0 |
| heli_add | 6 | 100.0 | 0.0 |
| sam_add | 3 | 100.0 | 0.0 |
| speed_up4 | 5 | 40.0 | 0.0 |
| year_earlier20 | 4 | 75.0 | 0.0 |
| year_later20 | 3 | 100.0 | 0.0 |

| 期待方向の項目 | n | 正答率 |
|---|---:|---:|
| aviation:present_B | 6 | 100.0 |
| complement:decrease | 3 | 66.7 |
| displacement:decrease | 6 | 100.0 |
| endurance:increase | 3 | 66.7 |
| gun_barrels:increase | 6 | 33.3 |
| helicopter:present_B | 7 | 100.0 |
| main_gun_cal:increase | 7 | 85.7 |
| missiles:present_B | 3 | 100.0 |
| modern_count:decrease | 4 | 75.0 |
| modern_count:increase | 3 | 100.0 |
| power_per_disp:decrease | 1 | 0.0 |
| power_per_disp:increase | 7 | 28.6 |
| sam:present_B | 4 | 100.0 |
| speed:increase | 5 | 80.0 |
| torpedo_tubes:decrease | 2 | 100.0 |

## 多様性

seed 間 完全一致 -%、weapons Jaccard 平均 -、数値 CV 中央値 -、問題間で同一出力 14.0%、主要諸元が同じ（テンプレート） 15.6%、出力の種類数/行数 86.4%
