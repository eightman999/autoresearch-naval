## T1 艦種判定

| model | n | acc | macro-F1 | fine acc | top-3 | ECE | JSON不正 |
|---|---|---|---|---|---|---|---|
| zeroshot | 200 | 0.010 | 0.006 | 0.000 | 0.045 | 0.182 | 0.000 |
| pilot | 200 | 0.795 | 0.635 | 0.580 | 0.950 | 0.051 | 0.000 |
| t1_era_country_majority | 200 | 0.340 | 0.148 | 0.155 | - | - | 0.000 |
| t1_hist_gbm | 200 | 0.800 | 0.616 | 0.480 | 0.935 | 0.048 | 0.000 |
| t1_knn15 | 200 | 0.675 | 0.438 | 0.400 | 0.865 | 0.051 | 0.000 |
| t1_majority | 200 | 0.240 | 0.016 | 0.095 | - | - | 0.000 |

## T2 武装・センサー

| model | n | weapon F1 | 主砲口径acc | 主砲門数MAE | kind-presence macro F1 | sensor F1 | sensor exact | JSON不正 |
|---|---|---|---|---|---|---|---|---|
| zeroshot | 200 | 0.007 | 0.000 | - | 0.124 | 0.160 | 0.000 | 0.000 |
| pilot | 200 | 0.516 | 0.484 | 1.328 | 0.699 | 0.690 | 0.714 | 0.000 |
| t2_empty | 200 | 0.000 | 0.000 | - | 0.000 | 0.000 | 0.472 | 0.000 |
| t2_knn1 | 200 | 0.523 | 0.516 | 1.415 | 0.665 | 0.660 | 0.689 | 0.000 |
| t2_knn5_medoid | 200 | 0.504 | 0.468 | 1.051 | 0.675 | 0.640 | 0.652 | 0.000 |

## T3 欠損諸元

| model | n | MAPE | median rel err | ±10%以内率 | 区間被覆率(10-90%) | 区間相対幅(中央値) | JSON不正 |
|---|---|---|---|---|---|---|---|
| zeroshot | 200 | - | - | - | - | - | 0.735 |
| pilot | 200 | 0.223 | 0.116 | 0.453 | 0.526 | 0.221 | 0.000 |
| t3_knn8 | 200 | 0.224 | 0.119 | 0.444 | 0.679 | 0.371 | 0.000 |
| t3_type_era_median | 200 | 1.876 | 0.398 | 0.159 | - | - | 0.000 |

### zeroshot: T1 variant 別 accuracy

- full: n=100 acc=0.010
- partial: n=100 acc=0.010

### zeroshot: T1 混同上位

- patrol -> heavy_cruiser: 27
- amphibious -> heavy_cruiser: 18
- submarine -> heavy_cruiser: 16
- torpedo_boat -> heavy_cruiser: 16
- patrol -> destroyer: 12
- mine_warfare -> heavy_cruiser: 11
- fast_attack_craft -> heavy_cruiser: 9
- gunboat_river -> heavy_cruiser: 8
- destroyer -> heavy_cruiser: 8
- submarine -> destroyer: 7
- patrol -> coastal_defense_monitor: 6
- armored_protected_cruiser -> heavy_cruiser: 6

### zeroshot: T2 年代別

| era | n | weapon F1 | P | R | 主砲口径acc | sensor F1 |
|---|---|---|---|---|---|---|
| <=1905 | 48 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 1906-1945 | 66 | 0.009 | 0.024 | 0.006 | 0.000 | 0.072 |
| >=1946 | 81 | 0.009 | 0.014 | 0.007 | 0.000 | 0.304 |
| unknown | 5 | 0.000 | 0.000 | 0.000 | 0.000 | 0.182 |

### zeroshot: T3 項目別

| field | n | MAPE | median | ±10% | 被覆率 | 区間相対幅 |
|---|---|---|---|---|---|---|

per k (同時に隠した項目数): 

### pilot: T1 variant 別 accuracy

- full: n=100 acc=0.850
- partial: n=100 acc=0.740

### pilot: T1 混同上位

- patrol -> amphibious: 3
- mine_warfare -> patrol: 3
- fast_attack_craft -> torpedo_boat: 2
- patrol -> fast_attack_craft: 2
- fast_attack_craft -> patrol: 2
- coastal_defense_monitor -> gunboat_river: 2
- escort -> patrol: 1
- escort -> mine_warfare: 1
- frigate -> patrol: 1
- corvette -> early_warship: 1
- patrol -> corvette: 1
- coast_guard -> gunboat_river: 1

### pilot: T2 年代別

| era | n | weapon F1 | P | R | 主砲口径acc | sensor F1 |
|---|---|---|---|---|---|---|
| <=1905 | 48 | 0.547 | 0.540 | 0.555 | 0.561 | 0.000 |
| 1906-1945 | 66 | 0.582 | 0.548 | 0.621 | 0.560 | 0.765 |
| >=1946 | 81 | 0.424 | 0.410 | 0.438 | 0.281 | 0.669 |
| unknown | 5 | 0.333 | 0.375 | 0.300 | 0.333 | 1.000 |

### pilot: T3 項目別

| field | n | MAPE | median | ±10% | 被覆率 | 区間相対幅 |
|---|---|---|---|---|---|---|
| beam_m | 43 | 0.122 | 0.083 | 0.581 | 0.465 | 0.120 |
| complement | 45 | 0.341 | 0.200 | 0.244 | 0.622 | 0.391 |
| displacement_full_t | 37 | 0.190 | 0.125 | 0.459 | 0.432 | 0.173 |
| displacement_normal_t | 42 | 0.331 | 0.153 | 0.405 | 0.571 | 0.244 |
| draught_m | 38 | 0.175 | 0.117 | 0.421 | 0.579 | 0.195 |
| length_m | 48 | 0.103 | 0.086 | 0.604 | 0.417 | 0.166 |
| power_hp | 40 | 0.406 | 0.179 | 0.325 | 0.675 | 0.553 |
| speed_kn | 40 | 0.119 | 0.081 | 0.575 | 0.450 | 0.153 |

per k (同時に隠した項目数): k1: MAPE 0.230 / ±10% 0.490 (n=104), k2: MAPE 0.249 / ±10% 0.381 (n=118), k3: MAPE 0.187 / ±10% 0.495 (n=111)
