# navspec Numeric Specialist Eval

作成日: 2026-07-13
phase: n0

## Summary

- model: `Qwen/Qwen2.5-0.5B-Instruct`
- adapter: `models/navspec-lora-0.5b-v0.1/adapter/stage3-numeric-quality-r8-v1`
- checkpoint: `iter500`
- total (ok cases): 935
- raw JSON成功率: 100.0%
- spec必須フィールド充足率: 99.8%
- 反復崩壊率: 0.00%

## Numeric Quality (Section 11 主指標)

- exact field adoption: 75.2%
- tolerant field adoption: 90.1%
- minor patch rate: 80.5%
- major patch rate: 9.6%
- patch dependency: 9.9%
- median rel patch delta: 0.0276
- p90 rel patch delta: 2.3164
- p95 rel patch delta: 113.1953
- constraint follow: 78.3%
- single constraint follow: 69.3%
- multi constraint follow: 93.3%
- boundary constraint follow: 50.0%
- cross-field consistency: 44.7%
- speed-power consistency: 44.7%

## Numeric Specialist 採用ゲート (Section 12)

| gate | threshold | actual | result |
|---|---|---:|:--:|
| raw_json_rate | >=99% | 100.0% | PASS |
| spec_required_field_rate | >=98% | 99.8% | PASS |
| tolerant_field_adoption | >=88% | 90.1% | PASS |
| major_patch_rate | <=10% | 9.6% | PASS |
| patch_dependency | <=30% | 9.9% | PASS |
| constraint_follow | >=90% | 78.3% | FAIL |
| multi_constraint_follow | >=80% | 93.3% | PASS |
| boundary_constraint_follow | >=80% | 50.0% | FAIL |
| median_relative_patch_delta | <=5% | 0.0276 | PASS |
| p90_relative_patch_delta | <=15% | 2.3164 | FAIL |
| p95_relative_patch_delta | <=20% | 113.1953 | FAIL |
| cross_field_consistency | >=95% | 44.7% | FAIL |
| repetition_collapse | <1% | 0.00% | PASS |
| power_shp_tolerant_adoption | >=75% | 67.3% | FAIL |
| speed_power_consistency | >=95% | 44.7% | FAIL |
| power_major_patch | <=12% | 32.5% | FAIL |
| range_nm_tolerant_adoption | >=75% | 66.5% | FAIL |
| range_major_patch | <=12% | 33.3% | FAIL |

All gates: **FAIL**

## Field-Level Results (Section 13)

| field | tolerant | exact | major | median_delta | p90_delta | adopt_pass | major_pass |
|---|---:|---:|---:|---:|---:|:--:|:--:|
| beam_m | 96.2% | 87.9% | 3.6% | 0.0045 | 0.0106 | PASS | PASS |
| belt_armor_mm | 97.8% | 93.6% | 2.0% | 0.0000 | 0.0509 | PASS | PASS |
| draft_m | 96.0% | 79.1% | 3.7% | 0.0000 | 0.0600 | PASS | PASS |
| full_load_displacement_t | 92.8% | 54.8% | 7.0% | 0.0145 | 0.0375 | PASS | PASS |
| length_m | 96.2% | 81.7% | 3.6% | 0.0060 | 0.0135 | PASS | PASS |
| main_gun_caliber_mm | 91.1% | 88.6% | 8.7% | 0.0059 | 0.0725 | PASS | PASS |
| main_gun_count | 98.9% | 96.7% | 0.9% | 0.0000 | 0.0000 | PASS | PASS |
| power_shp | 67.3% | 54.9% | 32.5% | 0.0190 | 0.4911 | PASS | FAIL |
| range_nm | 66.5% | 47.7% | 33.3% | 0.0340 | 0.3813 | PASS | FAIL |
| speed_kt | 85.7% | 77.4% | 14.1% | 0.0000 | 0.1337 | PASS | PASS |
| standard_displacement_t | 94.0% | 44.3% | 5.8% | 0.0227 | 0.0667 | PASS | PASS |
| torpedo_tube_count | 99.2% | 95.4% | 0.5% | 0.0000 | 0.0000 | PASS | PASS |

## Power/Speed Failure Classes (Section 14)

| class | count |
|---|---:|
| ok | 420 |
| speed_ok_power_high | 224 |
| speed_ok_power_low | 188 |
| mismatch_other | 82 |
| both_high | 10 |
| speed_high_power_ok | 7 |
| both_low | 4 |

## Ship-Type Breakdown (Section 17)

| ship_type | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| destroyer | 146 | 94.9% | 5.1% | 75.0% |
| large_destroyer | 51 | 95.1% | 4.9% | 73.3% |
| destroyer_escort | 51 | 89.9% | 10.1% | 60.0% |
| patrol_frigate | 51 | 91.7% | 8.3% | 53.3% |
| patrol_boat | 51 | 90.4% | 9.6% | 66.7% |
| gunboat | 51 | 92.7% | 7.3% | 73.3% |
| protected_cruiser | 51 | 93.8% | 6.2% | 93.3% |
| light_cruiser | 49 | 87.2% | 12.8% | 80.0% |
| armored_cruiser | 47 | 95.9% | 4.1% | 100.0% |
| dreadnought_battleship | 45 | 95.7% | 4.3% | 86.7% |
| battleship | 45 | 95.4% | 4.6% | 66.7% |
| heavy_cruiser | 45 | 88.0% | 12.0% | 87.5% |
| treaty_cruiser | 45 | 92.0% | 8.0% | 100.0% |
| coastal_defense_ship | 43 | 89.1% | 10.8% | 73.3% |
| pre_dreadnought_battleship | 43 | 95.3% | 4.7% | 63.3% |
| battlecruiser | 43 | 95.7% | 4.3% | 93.3% |
| fast_battleship | 32 | 85.9% | 7.8% | 100.0% |
| minesweeper | 4 | 31.2% | 68.8% | - |
| minelayer | 4 | 39.6% | 60.4% | - |
| ship_of_the_line | 4 | 29.2% | 70.8% | - |
| submarine_tender | 4 | 35.4% | 64.6% | - |
| oiler | 4 | 20.8% | 79.2% | - |
| repair_ship | 4 | 35.4% | 64.6% | - |
| training_ship | 4 | 25.0% | 75.0% | - |
| anti_air_light_cruiser | 4 | 91.7% | 8.3% | - |
| seaplane_tender | 3 | 41.7% | 58.3% | - |
| commerce_raider_cruiser | 3 | 44.4% | 55.6% | - |
| anti_air_destroyer | 3 | 88.9% | 11.1% | - |
| torpedo_boat | 3 | 27.8% | 72.2% | - |
| large_cruiser | 2 | 41.7% | 58.3% | - |

## Era Breakdown (Section 17)

| era | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| 1937_1945 | 330 | 92.0% | 8.0% | 80.4% |
| pre_1860 | 197 | 86.4% | 13.6% | 72.5% |
| 1906_1918 | 190 | 86.2% | 13.2% | 68.0% |
| 1919_1936 | 87 | 95.6% | 4.4% | 93.3% |
| 1890_1905 | 51 | 93.5% | 6.5% | 95.0% |
| 1860_1889 | 47 | 91.5% | 6.4% | - |
| post_1945 | 33 | 95.5% | 4.5% | - |

## Nation Tier Breakdown (Section 17)

| tier | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| major | 691 | 92.3% | 7.4% | 78.3% |
| regional | 109 | 87.8% | 12.2% | - |
| minor | 100 | 79.7% | 20.3% | - |
| limited | 35 | 83.8% | 16.2% | - |

## Constraint Category Breakdown (Section 17)

| category | count | tolerant | major | constraint |
|---|---:|---:|---:|---:|
| none | 695 | 89.8% | 9.9% | - |
| single_min | 100 | 90.3% | 9.7% | 66.0% |
| multi_constraint | 90 | 91.2% | 8.8% | 93.3% |
| single_max | 50 | 92.8% | 7.2% | 76.0% |
