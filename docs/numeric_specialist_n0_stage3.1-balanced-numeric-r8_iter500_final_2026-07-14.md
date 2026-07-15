# navspec Numeric Specialist Eval

作成日: 2026-07-14
phase: n0

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter: `models/navspec-lora-0.5b-v0.1/adapter/stage3.1-balanced-numeric-r8`
- checkpoint: `iter500_final`
- total (ok cases): 935
- raw JSON成功率: 100.0%
- spec必須フィールド充足率: 88.8%
- 反復崩壊率: 0.00%

## Numeric Quality (Section 11 主指標)

- exact field adoption: 65.5%
- tolerant field adoption: 76.4%
- minor patch rate: 64.0%
- major patch rate: 12.4%
- patch dependency: 23.6%
- median rel patch delta: 0.0307
- p90 rel patch delta: 1.4143
- p95 rel patch delta: 53.9257
- constraint follow: 70.2%
- single constraint follow: 70.7%
- multi constraint follow: 69.4%
- boundary constraint follow: 74.0%
- cross-field consistency: 35.8%
- speed-power consistency: 35.8%

## Numeric Specialist 採用ゲート (Section 12)

| gate | threshold | actual | result |
|---|---|---:|:--:|
| raw_json_rate | >=99% | 100.0% | PASS |
| spec_required_field_rate | >=98% | 88.8% | FAIL |
| tolerant_field_adoption | >=88% | 76.4% | FAIL |
| major_patch_rate | <=10% | 12.4% | FAIL |
| patch_dependency | <=30% | 23.6% | PASS |
| constraint_follow | >=90% | 70.2% | FAIL |
| multi_constraint_follow | >=80% | 69.4% | FAIL |
| boundary_constraint_follow | >=80% | 74.0% | FAIL |
| median_relative_patch_delta | <=5% | 0.0307 | PASS |
| p90_relative_patch_delta | <=15% | 1.4143 | FAIL |
| p95_relative_patch_delta | <=20% | 53.9257 | FAIL |
| cross_field_consistency | >=95% | 35.8% | FAIL |
| repetition_collapse | <1% | 0.00% | PASS |
| power_shp_tolerant_adoption | >=75% | 49.3% | FAIL |
| speed_power_consistency | >=95% | 35.8% | FAIL |
| power_major_patch | <=12% | 39.5% | FAIL |
| range_nm_tolerant_adoption | >=75% | 47.3% | FAIL |
| range_major_patch | <=12% | 41.5% | FAIL |

All gates: **FAIL**

## Field-Level Results (Section 13)

| field | tolerant | exact | major | median_delta | p90_delta | adopt_pass | major_pass |
|---|---:|---:|---:|---:|---:|:--:|:--:|
| beam_m | 84.5% | 69.7% | 4.3% | 0.0000 | 0.0451 | PASS | PASS |
| belt_armor_mm | 87.4% | 85.1% | 1.4% | 0.0000 | 0.0000 | PASS | PASS |
| draft_m | 83.2% | 77.8% | 5.6% | 0.0000 | 0.0600 | PASS | PASS |
| full_load_displacement_t | 81.3% | 48.0% | 7.5% | 0.0056 | 0.0996 | PASS | PASS |
| length_m | 84.1% | 69.2% | 4.7% | 0.0000 | 0.0451 | PASS | PASS |
| main_gun_caliber_mm | 79.9% | 77.9% | 8.9% | 0.0000 | 0.0940 | PASS | PASS |
| main_gun_count | 87.7% | 86.1% | 1.1% | 0.0000 | 0.0000 | PASS | PASS |
| power_shp | 49.3% | 32.2% | 39.5% | 0.0938 | 0.4874 | FAIL | FAIL |
| range_nm | 47.3% | 41.3% | 41.5% | 0.1295 | 0.5385 | FAIL | FAIL |
| speed_kt | 66.7% | 51.4% | 22.0% | 0.0000 | 0.1399 | PASS | FAIL |
| standard_displacement_t | 77.0% | 63.0% | 11.8% | 0.0070 | 0.1320 | PASS | PASS |
| torpedo_tube_count | 88.4% | 84.3% | 0.3% | 0.0000 | 0.0000 | PASS | PASS |

## Power/Speed Failure Classes (Section 14)

| class | count |
|---|---:|
| ok | 440 |
| speed_ok_power_high | 197 |
| speed_ok_power_low | 187 |
| mismatch_other | 71 |
| both_high | 21 |
| speed_high_power_ok | 13 |
| both_low | 6 |

## Ship-Type Breakdown (Section 17)

| ship_type | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| destroyer | 146 | 88.2% | 10.4% | 66.7% |
| large_destroyer | 51 | 81.9% | 18.1% | 76.7% |
| destroyer_escort | 51 | 89.9% | 10.1% | 86.7% |
| patrol_frigate | 51 | 91.3% | 8.7% | 73.3% |
| patrol_boat | 51 | 77.8% | 20.3% | 80.0% |
| gunboat | 51 | 86.8% | 13.2% | 86.7% |
| protected_cruiser | 51 | 89.7% | 8.3% | 93.3% |
| light_cruiser | 49 | 85.9% | 12.1% | 86.7% |
| armored_cruiser | 47 | 93.6% | 4.3% | 91.7% |
| dreadnought_battleship | 45 | 55.0% | 2.8% | 46.7% |
| battleship | 45 | 30.7% | 2.6% | 13.3% |
| heavy_cruiser | 45 | 88.9% | 11.1% | 100.0% |
| treaty_cruiser | 45 | 79.1% | 5.4% | 45.5% |
| coastal_defense_ship | 43 | 78.7% | 9.7% | 86.7% |
| pre_dreadnought_battleship | 43 | 61.1% | 4.1% | 40.0% |
| battlecruiser | 43 | 60.7% | 18.4% | 53.3% |
| fast_battleship | 32 | 54.2% | 8.3% | 69.2% |
| minesweeper | 4 | 31.2% | 68.8% | - |
| minelayer | 4 | 39.6% | 60.4% | - |
| ship_of_the_line | 4 | 14.6% | 35.4% | - |
| submarine_tender | 4 | 37.5% | 62.5% | - |
| oiler | 4 | 29.2% | 70.8% | - |
| repair_ship | 4 | 31.2% | 68.8% | - |
| training_ship | 4 | 41.7% | 58.3% | - |
| anti_air_light_cruiser | 4 | 72.9% | 27.1% | - |
| seaplane_tender | 3 | 36.1% | 63.9% | - |
| commerce_raider_cruiser | 3 | 50.0% | 50.0% | - |
| anti_air_destroyer | 3 | 63.9% | 36.1% | - |
| torpedo_boat | 3 | 19.4% | 80.6% | - |
| large_cruiser | 2 | 45.8% | 54.2% | - |

## Era Breakdown (Section 17)

| era | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| 1937_1945 | 330 | 79.0% | 10.7% | 73.2% |
| pre_1860 | 197 | 70.2% | 17.6% | 65.5% |
| 1906_1918 | 190 | 73.6% | 13.2% | 70.0% |
| 1919_1936 | 87 | 77.1% | 8.0% | 71.7% |
| 1890_1905 | 51 | 77.6% | 10.6% | 75.0% |
| 1860_1889 | 47 | 81.7% | 11.9% | - |
| post_1945 | 33 | 91.9% | 8.1% | - |

## Nation Tier Breakdown (Section 17)

| tier | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| major | 691 | 76.6% | 10.4% | 70.2% |
| regional | 109 | 76.0% | 12.1% | - |
| minor | 100 | 76.0% | 22.0% | - |
| limited | 35 | 74.8% | 25.2% | - |

## Constraint Category Breakdown (Section 17)

| category | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| none | 695 | 79.6% | 12.5% | - |
| single_min | 100 | 75.9% | 11.1% | 73.0% |
| multi_constraint | 90 | 58.3% | 15.0% | 69.4% |
| single_max | 50 | 65.7% | 8.3% | 66.0% |
