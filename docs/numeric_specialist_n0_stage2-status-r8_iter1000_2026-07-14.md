# navspec Numeric Specialist Eval

作成日: 2026-07-14
phase: n0

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter: `models/navspec-lora-0.5b-v0.1/adapter/stage2-status-r8`
- checkpoint: `iter1000`
- total (ok cases): 935
- raw JSON成功率: 100.0%
- spec必須フィールド充足率: 90.0%
- 反復崩壊率: 0.00%

## Numeric Quality (Section 11 主指標)

- exact field adoption: 66.6%
- tolerant field adoption: 74.5%
- minor patch rate: 59.1%
- major patch rate: 15.4%
- patch dependency: 25.5%
- median rel patch delta: 0.0414
- p90 rel patch delta: 1.2082
- p95 rel patch delta: 146.0038
- constraint follow: 69.0%
- single constraint follow: 66.0%
- multi constraint follow: 73.9%
- boundary constraint follow: 68.0%
- cross-field consistency: 24.7%
- speed-power consistency: 24.7%

## Numeric Specialist 採用ゲート (Section 12)

| gate | threshold | actual | result |
|---|---|---:|:--:|
| raw_json_rate | >=99% | 100.0% | PASS |
| spec_required_field_rate | >=98% | 90.0% | FAIL |
| tolerant_field_adoption | >=88% | 74.5% | FAIL |
| major_patch_rate | <=10% | 15.4% | FAIL |
| patch_dependency | <=30% | 25.5% | PASS |
| constraint_follow | >=90% | 69.0% | FAIL |
| multi_constraint_follow | >=80% | 73.9% | FAIL |
| boundary_constraint_follow | >=80% | 68.0% | FAIL |
| median_relative_patch_delta | <=5% | 0.0414 | PASS |
| p90_relative_patch_delta | <=15% | 1.2082 | FAIL |
| p95_relative_patch_delta | <=20% | 146.0038 | FAIL |
| cross_field_consistency | >=95% | 24.7% | FAIL |
| repetition_collapse | <1% | 0.00% | PASS |
| power_shp_tolerant_adoption | >=75% | 46.2% | FAIL |
| speed_power_consistency | >=95% | 24.7% | FAIL |
| power_major_patch | <=12% | 43.7% | FAIL |
| range_nm_tolerant_adoption | >=75% | 49.8% | FAIL |
| range_major_patch | <=12% | 40.1% | FAIL |

All gates: **FAIL**

## Field-Level Results (Section 13)

| field | tolerant | exact | major | median_delta | p90_delta | adopt_pass | major_pass |
|---|---:|---:|---:|---:|---:|:--:|:--:|
| beam_m | 81.3% | 70.9% | 8.7% | 0.0000 | 0.0496 | PASS | PASS |
| belt_armor_mm | 84.5% | 83.7% | 5.5% | 0.0000 | 0.0000 | PASS | PASS |
| draft_m | 82.9% | 71.1% | 7.1% | 0.0000 | 0.0500 | PASS | PASS |
| full_load_displacement_t | 79.1% | 76.5% | 10.8% | 0.0000 | 0.1364 | PASS | PASS |
| length_m | 80.8% | 70.9% | 9.2% | 0.0000 | 0.0902 | PASS | PASS |
| main_gun_caliber_mm | 75.5% | 74.2% | 14.4% | 0.0000 | 0.2700 | PASS | PASS |
| main_gun_count | 89.0% | 87.2% | 1.0% | 0.0000 | 0.0000 | PASS | PASS |
| power_shp | 46.2% | 19.4% | 43.7% | 0.1310 | 0.7904 | FAIL | FAIL |
| range_nm | 49.8% | 43.4% | 40.1% | 0.0769 | 0.5385 | FAIL | FAIL |
| speed_kt | 64.2% | 46.2% | 25.8% | 0.0278 | 0.1500 | PASS | FAIL |
| standard_displacement_t | 77.0% | 76.5% | 12.9% | 0.0000 | 0.1364 | PASS | PASS |
| torpedo_tube_count | 84.0% | 78.8% | 6.0% | 0.0000 | 2.0000 | PASS | PASS |

## Power/Speed Failure Classes (Section 14)

| class | count |
|---|---:|
| ok | 325 |
| speed_ok_power_low | 247 |
| speed_ok_power_high | 162 |
| mismatch_other | 120 |
| speed_high_power_ok | 51 |
| both_high | 26 |
| both_low | 4 |

## Ship-Type Breakdown (Section 17)

| ship_type | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| destroyer | 146 | 89.2% | 10.8% | 83.3% |
| large_destroyer | 51 | 79.9% | 20.1% | 80.0% |
| destroyer_escort | 51 | 85.5% | 14.5% | 66.7% |
| patrol_frigate | 51 | 88.4% | 11.6% | 66.7% |
| patrol_boat | 51 | 87.9% | 12.1% | 100.0% |
| gunboat | 51 | 83.3% | 12.8% | 86.7% |
| protected_cruiser | 51 | 94.6% | 5.4% | 100.0% |
| light_cruiser | 49 | 90.5% | 9.5% | 93.3% |
| armored_cruiser | 47 | 92.0% | 8.0% | 100.0% |
| dreadnought_battleship | 45 | 69.6% | 14.8% | 53.3% |
| battleship | 45 | 26.9% | 2.0% | 6.7% |
| heavy_cruiser | 45 | 76.3% | 23.7% | 95.8% |
| treaty_cruiser | 45 | 86.5% | 6.9% | 72.7% |
| coastal_defense_ship | 43 | 85.9% | 9.5% | 86.7% |
| pre_dreadnought_battleship | 43 | 71.3% | 3.1% | 33.3% |
| battlecruiser | 43 | 20.7% | 60.7% | 46.7% |
| fast_battleship | 32 | 6.2% | 3.1% | 7.7% |
| minesweeper | 4 | 25.0% | 75.0% | - |
| minelayer | 4 | 50.0% | 50.0% | - |
| ship_of_the_line | 4 | 83.3% | 16.7% | - |
| submarine_tender | 4 | 25.0% | 75.0% | - |
| oiler | 4 | 29.2% | 70.8% | - |
| repair_ship | 4 | 25.0% | 75.0% | - |
| training_ship | 4 | 12.5% | 87.5% | - |
| anti_air_light_cruiser | 4 | 85.4% | 14.6% | - |
| seaplane_tender | 3 | 30.6% | 69.4% | - |
| commerce_raider_cruiser | 3 | 25.0% | 75.0% | - |
| anti_air_destroyer | 3 | 86.1% | 13.9% | - |
| torpedo_boat | 3 | 19.4% | 80.6% | - |
| large_cruiser | 2 | 25.0% | 75.0% | - |

## Era Breakdown (Section 17)

| era | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| 1937_1945 | 330 | 81.0% | 13.2% | 73.9% |
| pre_1860 | 197 | 66.3% | 20.0% | 64.1% |
| 1906_1918 | 190 | 71.8% | 17.1% | 64.0% |
| 1919_1936 | 87 | 76.2% | 10.0% | 75.0% |
| 1890_1905 | 51 | 69.8% | 14.5% | 72.5% |
| 1860_1889 | 47 | 72.2% | 17.2% | - |
| post_1945 | 33 | 80.3% | 13.6% | - |

## Nation Tier Breakdown (Section 17)

| tier | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| major | 691 | 72.8% | 13.9% | 69.0% |
| regional | 109 | 84.2% | 13.9% | - |
| minor | 100 | 76.2% | 23.8% | - |
| limited | 35 | 73.8% | 26.2% | - |

## Constraint Category Breakdown (Section 17)

| category | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| none | 695 | 78.2% | 16.6% | - |
| single_min | 100 | 67.1% | 12.9% | 69.0% |
| multi_constraint | 90 | 62.5% | 13.1% | 73.9% |
| single_max | 50 | 60.0% | 8.0% | 60.0% |
