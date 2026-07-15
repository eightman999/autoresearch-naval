# navspec Numeric Specialist Eval

作成日: 2026-07-14
phase: n0

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter: `/tmp/ns_stage31_iter250`
- checkpoint: `iter250`
- total (ok cases): 935
- raw JSON成功率: 100.0%
- spec必須フィールド充足率: 92.7%
- 反復崩壊率: 0.00%

## Numeric Quality (Section 11 主指標)

- exact field adoption: 66.0%
- tolerant field adoption: 75.7%
- minor patch rate: 58.7%
- major patch rate: 17.0%
- patch dependency: 24.3%
- median rel patch delta: 0.0448
- p90 rel patch delta: 1.4242
- p95 rel patch delta: 65.9235
- constraint follow: 81.5%
- single constraint follow: 79.3%
- multi constraint follow: 85.0%
- boundary constraint follow: 72.0%
- cross-field consistency: 34.4%
- speed-power consistency: 34.4%

## Numeric Specialist 採用ゲート (Section 12)

| gate | threshold | actual | result |
|---|---|---:|:--:|
| raw_json_rate | >=99% | 100.0% | PASS |
| spec_required_field_rate | >=98% | 92.7% | FAIL |
| tolerant_field_adoption | >=88% | 75.7% | FAIL |
| major_patch_rate | <=10% | 17.0% | FAIL |
| patch_dependency | <=30% | 24.3% | PASS |
| constraint_follow | >=90% | 81.5% | FAIL |
| multi_constraint_follow | >=80% | 85.0% | PASS |
| boundary_constraint_follow | >=80% | 72.0% | FAIL |
| median_relative_patch_delta | <=5% | 0.0448 | PASS |
| p90_relative_patch_delta | <=15% | 1.4242 | FAIL |
| p95_relative_patch_delta | <=20% | 65.9235 | FAIL |
| cross_field_consistency | >=95% | 34.4% | FAIL |
| repetition_collapse | <1% | 0.00% | PASS |
| power_shp_tolerant_adoption | >=75% | 47.7% | FAIL |
| speed_power_consistency | >=95% | 34.4% | FAIL |
| power_major_patch | <=12% | 45.0% | FAIL |
| range_nm_tolerant_adoption | >=75% | 44.5% | FAIL |
| range_major_patch | <=12% | 48.2% | FAIL |

All gates: **FAIL**

## Field-Level Results (Section 13)

| field | tolerant | exact | major | median_delta | p90_delta | adopt_pass | major_pass |
|---|---:|---:|---:|---:|---:|:--:|:--:|
| beam_m | 85.2% | 68.6% | 7.5% | 0.0000 | 0.0664 | PASS | PASS |
| belt_armor_mm | 89.8% | 85.9% | 2.9% | 0.0000 | 0.0000 | PASS | PASS |
| draft_m | 83.7% | 78.5% | 9.0% | 0.0000 | 0.0900 | PASS | PASS |
| full_load_displacement_t | 76.8% | 64.7% | 15.9% | 0.0022 | 0.6175 | PASS | FAIL |
| length_m | 81.0% | 69.5% | 11.8% | 0.0000 | 0.1177 | PASS | PASS |
| main_gun_caliber_mm | 78.3% | 76.3% | 14.4% | 0.0000 | 0.2095 | PASS | PASS |
| main_gun_count | 90.3% | 86.5% | 2.5% | 0.0000 | 0.0000 | PASS | PASS |
| power_shp | 47.7% | 32.0% | 45.0% | 0.1481 | 0.6335 | FAIL | FAIL |
| range_nm | 44.5% | 37.4% | 48.2% | 0.1765 | 0.5625 | FAIL | FAIL |
| speed_kt | 65.0% | 47.8% | 27.7% | 0.0000 | 0.1571 | PASS | FAIL |
| standard_displacement_t | 73.9% | 61.2% | 18.8% | 0.0084 | 0.6264 | PASS | FAIL |
| torpedo_tube_count | 92.2% | 83.5% | 0.5% | 0.0000 | 0.0000 | PASS | PASS |

## Power/Speed Failure Classes (Section 14)

| class | count |
|---|---:|
| ok | 390 |
| speed_ok_power_high | 186 |
| speed_ok_power_low | 186 |
| mismatch_other | 131 |
| both_high | 19 |
| speed_high_power_ok | 13 |
| both_low | 10 |

## Ship-Type Breakdown (Section 17)

| ship_type | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| destroyer | 146 | 88.0% | 10.7% | 66.7% |
| large_destroyer | 51 | 77.8% | 22.2% | 73.3% |
| destroyer_escort | 51 | 88.9% | 11.1% | 66.7% |
| patrol_frigate | 51 | 90.0% | 10.0% | 66.7% |
| patrol_boat | 51 | 74.2% | 25.8% | 73.3% |
| gunboat | 51 | 70.4% | 25.7% | 66.7% |
| protected_cruiser | 51 | 75.5% | 24.5% | 100.0% |
| light_cruiser | 49 | 79.1% | 16.8% | 83.3% |
| armored_cruiser | 47 | 92.0% | 8.0% | 100.0% |
| dreadnought_battleship | 45 | 58.3% | 3.9% | 73.3% |
| battleship | 45 | 57.0% | 5.2% | 86.7% |
| heavy_cruiser | 45 | 85.7% | 14.3% | 100.0% |
| treaty_cruiser | 45 | 84.8% | 8.5% | 72.7% |
| coastal_defense_ship | 43 | 81.0% | 12.0% | 100.0% |
| pre_dreadnought_battleship | 43 | 78.3% | 5.4% | 80.0% |
| battlecruiser | 43 | 54.1% | 43.6% | 100.0% |
| fast_battleship | 32 | 49.7% | 12.8% | 76.9% |
| minesweeper | 4 | 27.1% | 72.9% | - |
| minelayer | 4 | 35.4% | 64.6% | - |
| ship_of_the_line | 4 | 43.8% | 6.2% | - |
| submarine_tender | 4 | 31.2% | 68.8% | - |
| oiler | 4 | 31.2% | 68.8% | - |
| repair_ship | 4 | 27.1% | 72.9% | - |
| training_ship | 4 | 29.2% | 70.8% | - |
| anti_air_light_cruiser | 4 | 85.4% | 14.6% | - |
| seaplane_tender | 3 | 36.1% | 63.9% | - |
| commerce_raider_cruiser | 3 | 36.1% | 63.9% | - |
| anti_air_destroyer | 3 | 63.9% | 36.1% | - |
| torpedo_boat | 3 | 22.2% | 77.8% | - |
| large_cruiser | 2 | 37.5% | 62.5% | - |

## Era Breakdown (Section 17)

| era | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| 1937_1945 | 330 | 78.4% | 14.6% | 83.3% |
| pre_1860 | 197 | 70.8% | 22.1% | 78.2% |
| 1906_1918 | 190 | 73.9% | 17.2% | 80.0% |
| 1919_1936 | 87 | 74.3% | 14.2% | 81.7% |
| 1890_1905 | 51 | 78.6% | 19.4% | 90.0% |
| 1860_1889 | 47 | 77.1% | 16.5% | - |
| post_1945 | 33 | 86.1% | 13.9% | - |

## Nation Tier Breakdown (Section 17)

| tier | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| major | 691 | 75.9% | 16.5% | 81.5% |
| regional | 109 | 76.8% | 12.2% | - |
| minor | 100 | 74.2% | 22.8% | - |
| limited | 35 | 73.1% | 26.9% | - |

## Constraint Category Breakdown (Section 17)

| category | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| none | 695 | 76.8% | 15.7% | - |
| single_min | 100 | 78.7% | 19.3% | 81.0% |
| multi_constraint | 90 | 65.4% | 25.7% | 85.0% |
| single_max | 50 | 72.5% | 15.5% | 76.0% |
