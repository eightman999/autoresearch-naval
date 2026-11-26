# shipknow 設計評価 v1: 学習前後比較用の評価データ・採点器・推論手順

作成: 2026-10-07。対象: 学習前 v1_2（T1〜T3 のみ学習、D は未学習）と、設計継続学習後 design-v0（best = step 1400、final = step 1567）。
**追加学習はしていない。評価データは学習データに入れない・design_v0 の test / scenarios と混ぜない。** Mac 上で作り、推論は llm_master の RTX 3060 で別途実行する（この文書のコマンドを使う）。

## 1. ファイル

| 種別 | パス |
|---|---|
| 評価データ | `data/navspec/shipknow_design_eval_v1/{problems,pairs,all}.jsonl, manifest.json, overlap_check.json, forget_ids.json` |
| 非 LLM ベースライン・規則の誤検出確認 | `data/navspec/shipknow_design_eval_v1/baselines/`（`preds/` と `summary_*.{json,md}`、`compare.md`、`sanity_gold_v0_test.json`） |
| データ生成 | `tools/shipknow_build_design_eval.py`（`--verify-only` で独立再検証）、手書きケース `tools/shipknow_design_eval_cases.py`、拡張要求 `tools/shipknow_design_eval_req.py` |
| 採点器 | `tools/shipknow_design_eval.py`（`score` / `compare` / `baselines` / `forget-test` / `sanity`） |
| 推論 | `tools/shipknow_infer_hf.py`（`--sample-seed` / `--top-p` / `--sample-tasks` / `--ids` を追加。既定は従来どおり greedy） |
| テスト | `tests/test_shipknow_design_eval.py`（40 件）。リポジトリ全体 406 passed, 1 skipped（元 366 passed, 1 skipped） |

`problems.jsonl` は A〜E 各 50 問 = 250 問、`pairs.jsonl` は 50 組 = 100 問、`all.jsonl` は両者の連結（350 行。推論を 1 回で済ませる用。id は重複しない）。
形式は `design_v0/scenarios.jsonl` と同じ（`messages` = [system, user] で正解なし、user は `{"request", "req", "nbr"}` の JSON、`req`、`request` / `request_ja`、`category`、`meta`）。
system プロンプトは design_v0 と同一（manifest の `system_prompt_identical_to_design_v0` = true）。生成は seed 固定で、2 回生成して問題・ペア・forget_ids がバイト単位で一致することを確認した。

## 2. 評価データ

### 2.1 拡張した要求（`req` の追加キー）

v0 の `req` キーに、評価用の任意キーを足した。v0 の validator は未知キーを無視するので、既存の採点・検査は壊れない（テストで確認）。**design-v0 は拡張キーを学習で見ていない**（要求文と `req` の両方に出るが、モデルが読むのは主に要求文）。

| キー | 意味（採点器の判定） |
|---|---|
| `min_gun_barrels`、`min_gun_barrels_cal_mm` | kind=gun の砲身数 Σ(n × per)。口径指定があればその口径以上の砲だけ数える |
| `min_aircraft` | kind=aircraft_helicopter の Σ n（n 省略は 1）。aviation_facility だけ（n なし）は 0 機 |
| `min_torpedo_tubes` | kind=torpedo_tube の Σ(n × per) |
| `required_propulsion` | 設計の `propulsion` に全て含まれる（gas_turbine / nuclear / diesel など） |

語彙に無いもの（AESA、VLS、Aegis、SLBM）は要求文に書き、`req` では近い kind（air_search_radar、sam、ballistic_missile）にして、元の語を `meta.original_terms` に残した（E の 8 問）。

### 2.2 近傍（規則 NB-1）

`shipknow_build_design.select_neighbors` をそのまま使った（k=3、train の実艦のみ、要求年 +2 年より後の艦を出さない、同国の系譜枠 最大 2 + 同時代の他国艦、要求年以前の改装・艦歴を最大 3 件）。拡張キーは v0 の `required` 語彙に写して検索する（`min_aircraft` → helicopter、`min_torpedo_tubes` → torpedo_tubes、`min_gun_barrels_cal_mm` → `main_gun>=N`）。

- **B**: witness 艦の艦級グループを近傍から除く。witness の艦名・艦級名・艦歴は年表から落とす（v0 の漏れ防止と同じ）。
- **C**: 全充足する train 実艦（0〜4 隻）の艦級グループを近傍から除く。
- **D（架空国家）**: 国名が train のどの艦とも一致しないので、同国の系譜枠は空になり、近傍は「同時代の他国艦」の枠と全体の近い順の補充だけになる。
- **A / D（実在国）/ E**: 規則どおり（近傍に同国の艦が入る。A の近傍に要求とほぼ同じ実艦が入りうる。丸写し判定が見る）。

### 2.3 カテゴリ

| | 作り方 | 規模・内訳 |
|---|---|---|
| A Historical | 国 10（日・米・英・独・伊・仏・露・中・蘭・瑞 各 5）× 年 1890〜2010 × 主要艦種（戦艦〜哨戒艇・潜水艦・空母など 17 種）。制約は train の同国・同艦種・±10 年（足りなければ ±15、±20）の実艦分布から作る: 排水量上限 = p80、最低速力 = p25、主砲口径下限 = p25 以下の格子値、装備は 6 割以上の艦が持つものから 0〜1 個、乗員上限 25%。**test に同じ国・艦種の実艦が ±2 年以内に無い**組み合わせだけ。train の実艦で艦種一致の充足艦が 3 隻以上 | 年代: 1860-99 が 7、1900-19 が 11、1920-44 が 12、1945-69 が 12、1970- が 8 |
| B Quantitative | 実在の train 艦 1 隻（witness）の値から強い制約を逆算: 排水量 +3〜8%、速力 −0〜0.5kn、乗員 +3〜8%、航続 −0〜5%、主砲口径 = 実口径、砲身数 = 実数（その口径以上）、搭載機数、魚雷管数、センサー・武装の必須、推進。**制約単位 4〜7 個**（分布: 4 個 21、5 個 9、6 個 13、7 個 7）。年代 5 区分 × 10 問、国別 ≤ 6。排水量 30t 未満の witness は除外。witness の id は `meta.witness`、train で充足する艦数は `meta.n_sat_train_any`（1 以上） | 全問 witness が充足することを `req_checks` で再確認済み |
| C Trade-off | 小さい船（排水量 donor）+ 速い船（速力 donor、同艦種・±10 年の最速）+ 大きい船（武装 donor 2 隻）の制約を重ねる。制約単位 5〜8 個。train の実艦で全充足するのは 0〜4 隻（全艦種で数えて 0 が 12、1 が 12、2 が 16、3 が 6、4 が 4。艦種一致で数えると 0 が 32 問）。**不可能ではない根拠**: 排水量 +10%・速力 −2kn の緩和で要求年+5 年以内の実艦が 1 隻以上（全問）。難しさの根拠は `meta.n_sat_*`、`meta.relaxed_counts`（各制約を 1 つ外したときの充足艦数）、`meta.stretch`（donor の id） | 例: 排水量 5,200t・31.5kn のインド駆逐艦 1997 に、ヘリ・CIWS・射撃管制レーダー・ESM・搭載機 2・魚雷管 6 |
| D OOD | 手書き 50 問。戦勝国（日・独・伊の戦後艦）12、架空国家 14（Valdoria / Norvane / Kestria / Aurelan / Tarsonia / Meridia / Zephyria / Brannoch / Solmara / Ysbelle の実在しない国名）、技術前倒し 12（レーダー・SSM・SAM・ヘリ・原子力・ガスタービン・ソナーが 10〜20 年早い世界）、実在艦級に対応しない制約セット 12。前提は要求文の先頭に書く（既存シナリオと同じ書式）。技術前倒しは `meta.era_waivers` に許す能力語彙を持ち、採点の年代検査から外す | |
| E Impossible | 手書き 50 問。物理 24、年代 15、矛盾 11。各問に `meta.why_impossible = {category, reason}`。「train の実艦で、要求年+5 年以内に、艦種一致で全充足する艦が 0」を `--verify-only` で確認 | 例: 1895 年のイージス艦、300t で 46cm 砲 9 門、50t・40kn・乗員 5・ヘリ 2 機、1930 年代に VLS と AESA |
| P Pair | 50 組（A/B）。基本条件は A と同じ規則だが、変える制約が効くように中央値付近（排水量上限 = 中央値 +10%、速力 = p40）。A/B とも train の実艦（艦種一致）で 1 隻以上が充足できることを要求。期待方向は `meta.pair.expected` | 3 節 |

### 2.4 ペアの変更種別と期待方向（50 組）

| 変更種別 | 組数 | 変更 | 期待（B が A に対して） |
|---|---:|---|---|
| disp_down25 | 5 | 排水量上限 −25% | displacement 減 |
| speed_up4 | 5 | 最低速力 +4kn | speed 増、power/displacement 増 |
| gun_barrels_up | 5 | 砲身数下限 +2（同じ口径指定） | gun barrels 増 |
| gun_cal_up | 6 | 主砲口径下限を格子で 1.3 倍以上 | 主砲口径 増 |
| heli_add | 6 | `helicopter` を足す | ヘリ搭載 有、航空装備 有 |
| sam_add | 3 | `sam` を足す | SAM 有、ミサイル 有 |
| year_earlier20 | 4 | 年を 20 年早める | modern_count 減（train の群の中央値で 1 以上差があるものだけ） |
| year_later20 | 3 | 年を 20 年遅らせる | modern_count 増（同上） |
| country_change | 4 | 国を変える | 2 国の train 群の中央値差が大きい指標（魚雷管数・出力/排水量・乗員・速力）の方向。根拠は `meta.pair.data_support` |
| endurance_x2 | 3 | 航続下限 2 倍 | endurance 増 |
| complement_half | 3 | 乗員上限 1/2 | complement 減 |
| combo_disp_speed | 1 | 排水量 −25% + 速力 +4kn | displacement 減、power/displacement 増 |
| combo_cal_barrels | 1 | 口径 + 砲身数 | 主砲口径 増、gun barrels 増 |
| combo_sam_heli | 1 | SAM + ヘリ | SAM 有、ヘリ 有 |

`modern_count` = ssm・sam・ciws・ヘリ・レーダー（任意）・ソナー・ESM/ECM・ガスタービン・原子力・対空捜索レーダー・射撃管制レーダーのうち設計が持つ種類数。sam_add と combo_sam_heli は該当艦が少なく、5 組目を作れなかったので sam_add が 3、heli_add と gun_cal_up が +1 になっている。
ヘリ・SAM を足すペアは該当艦が少ないため、全ての国から選び、test の実艦の有無は問わない（v0 の要求とは重複しない）。

### 2.5 重複検証（`overlap_check.json`、`--verify-only`）

評価データの 350 要求（problems 250 + pairs 100）を、v0 の train / valid / test / scenarios の 10,025 要求と突き合わせた。

| 検査 | 結果 |
|---|---:|
| 要求文の完全一致 | 0 |
| 構造化要求 `req` の完全一致 | 0 |
| 主要制約の一致（国・年・艦種・排水量上限・最低速力・必須装備の集合） | 0 |
| 評価データ内の重複（問題同士、ペア同士、問題とペア。A/B が同じ主要制約になるペアは除く） | 0 |
| （参考）国・年・艦種だけが v0 のどれかの要求と同じ | 177 / 350（A 19、B 50、C 50、D 12、E 13、P 33。B・C は witness の艦自身の v0 train 要求と同じ国・年・艦種になるため全問。主要制約は全て違う） |
| A: test の実艦が同国・同艦種で ±2 年以内にある | 0 / 50 |

`--verify-only` はさらに、近傍が全て train・要求年 +2 年以内・除外した艦級グループ外であること、B の witness が要求を満たし近傍に入っていないこと、C の充足艦数の再計算（全 train 設計に `req_checks`）、E で艦種一致の実艦が要求年+5 年以内に 0 であること、ペアの A/B の差が 1〜4 キーであること、を再計算する（現在 errors = 0）。

## 3. 採点器（`tools/shipknow_design_eval.py`）の判定規則

入力は予測 JSONL（`{"id", "seed"(省略時 0), "text" か "output"}`）と問題ファイル。出力は問題×seed 単位の行（`rows_<model>.jsonl`）、集計（`summary_<model>.json` / `.md`）、複数モデルなら `compare.md`（基準モデルとの差分。率は %、差は pp、警告数だけ実数）。
予測に出てくる seed の全てについて、全問題に行を作る（予測が無い（id, seed）は JSON 不正）。`compare_models(summaries)` が比較表の関数。

- **Format**: JSON parse 成功率（` ``` ` や前後の文を剥がして最初の JSON オブジェクトを取る）。スキーマ合格率 = validator の schema 検査（艦種ラベル、kind、値域、必須の排水量・全長・速力）。フィールド別の欠損率（ship_type / ship_type_fine / displacement〈常備・満載・基準のどれか〉 / length / beam / draught / propulsion / shafts / power / speed / endurance / complement / armour / weapons / sensors / air）。
- **Requirement**: `req` の制約を個別に判定（`req_checks`）。種別 = ship_type、ship_type_fine、displacement、speed、complement、endurance、main_gun、gun_barrels、aircraft_count、torpedo_tubes、sensor、weapon、aircraft_fit、propulsion。**数値項目（排水量・速力・乗員・航続・推進）が設計に無い制約は判定不能（None）**、weapons / sensors の配列に無い装備は未充足。問題ごとの 満たした数 / 判定可能数 の平均（`req_sat_mean_determinable`）と、判定不能・JSON 不正を 0 に数える厳密版（`req_sat_mean_strict`）、全制約を満たした行の率、種別ごとの充足率（判定可能分母と厳密分母）を出す。
- **Internal consistency**（合格 = hard 警告が 0）:
  1. validator の物理比（ratio_*: L/B、B/T、ブロック係数、Admiralty 係数、V/√L、出力/排水量、乗員/排水量、満載/常備）、砲（gun_*）、年代（era_early。`meta.era_waivers` に挙げた語彙は数えない）、艦種（type_*）の違反 = hard、warnings = soft
  2. `armament_excess`: log(Σ(口径^3 × 砲身数) / 排水量) が、train の（艦種 × 年代）層の [p0.5, p99.5] を幅の 25% 広げた上限を超える（validator と同じフォールバック）
  3. `weapon_count_excess:<ssm|sam|ciws|torpedo_tube|aircraft>`: 数が、排水量の 1.5 倍以下の train 実艦のその装備の最大数の 1.5 倍 + 1 を超える（その大きさまでの実艦に装備が無ければ 2 以上で超過）
  4. `type_displacement_mismatch`: log 排水量が train の艦種ごとの [p0.5, p99.5] を幅の 25% 広げた範囲の外
  5. `displacement_order`: 満載 < 常備 × 0.97、基準 > 常備 × 1.03、満載 < 基準 × 0.97
  6. `air_without_entry` / `air_entry_without_air`: `air` にあるのに weapons に aircraft_helicopter / aviation_facility が無い（または逆）
  7. `heli_hull_too_short`: ヘリ搭載で全長が train のヘリ搭載艦の p1（53m）の 0.8 倍未満
  8. `nuclear_small_hull`: 原子力推進で排水量が train の原子力艦の最小（3,070t）の 0.5 倍未満
  9. `beam_ge_length`: 全幅 ≥ 全長
  10. `sub_many_big_guns`: 潜水艦に口径 100mm 以上の砲が 3 門より多い
  - 規則の誤検出: v0 test の実艦 989 隻の正解設計を予測として採点した（`sanity`）。**合格率 96.6%（誤検出 3.4%。全て validator の ratio_* で、追加規則 2〜10 の誤検出は 0）**。主な違反は ratio_high:fr 7、ratio_high:adm 5、ratio_low:hp_t 5、ratio_low:cb_normal 4 など（`baselines/sanity_gold_v0_test.json`）。
- **Historical plausibility**: 出力の排水量・全長・速力・出力・主砲口径・乗員が、train の 同国 × ±10 年 × 要求の艦種 の実艦（n ≥ 8）の 5-95 パーセンタイルに入る割合。参照群は 同国 → 同地域 ±10 年 → 同地域 ±20 年 → 世界 ±10 年 → 世界 ±20 年 の順に、8 艦以上ある最初のもの（架空国家は地域が無く世界になる）。排水量は要求の基準（常備 / 満載 / 基準）で比べる。参照が 8 艦未満の項目と、設計に値が無い項目は判定しない。問題ごとに「判定した全項目が範囲内」と「範囲内の項目の割合」。
  - **基準線**: 実艦の正解設計（v0 test）でも、項目ごとの範囲内率は 78〜88%（排水量 78.5、全長 79.9、速力 81.3、出力 81.8、主砲口径 88.2、乗員 79.2）、6 項目すべてが範囲内に入る率は 53.6%（群が小さく、実艦の裾が広いため）。**モデルの値は 100% ではなくこの基準線と比べる。**
- **実在艦の丸写し**: 出力の排水量（常備・満載・基準のどれか 1 つが同じ基準で一致）・全長・速力が、全 records（10,580 艦級）のある実艦と ±1% 以内で全て一致し、全幅は両方にあれば一致する率（`copy_any_rate`。±3% の `copy_near_any_rate` も出す）。入力の近傍艦（`nbr` の dn / df / ds / L / B / v）と一致する率と、丸写しのうち近傍と一致する割合。注意: **B・C の witness 実艦は train にあるので、design-v0 はその艦自身を学習している**（B/C の高い丸写し率は記憶の可能性がある。`copy_train` を行に残す）。
- **多様性**: 同一問題の seed 間で出力が完全一致する率、weapons の Jaccard、ship_type_fine の一致率、主要数値（排水量・全長・速力・出力）の変動係数（問題ごとに 4 項目の平均 → 問題間の中央値）。異なる問題間で同一出力（`canon` が複数問題で同じ）になる率、主要諸元（細分類・排水量・全長・速力・出力）が同じになる率、出力の種類数 / 行数。
- **E（不可能な要求）**: 応答を 1 つに分類する。
  - `refused`（矛盾を認識）: 次のどれか。(a) `status` / `result` / `verdict` 系のキーが infeasible・impossible・cannot・fail 等、`feasible` = false、`infeasible` / `impossible` = true、`conflicts` / `unmet*` / `violations` / `relaxed*` / `errors` 系のキーが空でない、(b) `notes` / `reason` / `warning` / `comment` / `explanation` / `remarks` / `caveats` 系の文字列に cannot / impossible / infeasible / conflict / did not exist / anachronism などがある、(c) `ship_type` が impossible・infeasible・invalid・none・unknown・n/a など、(d) JSON の外の文章（または JSON が無い応答）にそれらの語がある。**現状の学習データに拒否応答は無いので、この規則は事前に定義したもの**（`marker_kinds` に根拠の内訳を記録）。
  - `relaxed`（緩和）: 認識せず、JSON は有効で、要求を全部は満たさない設計（要求を黙って緩めた）。
  - `filled`（数字で埋めた）: 要求を全て満たし、かつ整合性の hard 違反がある。
  - `ok`: 要求を全て満たし整合性も合格（不可能なはずの要求に妥当な設計。E の作りの誤りか、実際に可能だったことを示す）。
  - `invalid`: JSON が無く、認識もしていない。
- **Pair**: 組×seed ごとに、A と B（同じ seed）の両方が JSON として読めたときだけ、`meta.pair.expected` の全項目を判定（増減は厳密、A/B が同じ値は「増えていない」）。判定に必要な値が無い項目と、読めない側がある組は不正解。出力は 組×seed の正答率、seed 多数決の正答率、変更種別ごとの正答率、期待項目ごとの正答率、A/B が完全に同じ出力だった率。
- **集計単位**: モデル別（`--pred NAME=PATH`）、カテゴリ別（A〜E。P は `pair_rows` として別）、seed 別。`--pred` は同じ NAME を繰り返すと合算する（problems と pairs を別ファイルで推論したときなど）。greedy の予測（seed なし = seed 0）は別の NAME（例 `best_greedy`）にしておくと、サンプリングの seed と混ざらない。

### 非 LLM ベースライン（近傍だけから作る設計。seed 1 本）

| | 要求充足（判定可能） | 全制約を満たした行 | 整合性合格 | 5-95% 全項目 | 丸写し | ペア正答率 |
|---|---:|---:|---:|---:|---:|---:|
| top1（最近傍をそのまま） | 68.3% | 16.0% | 97.2% | 58.7% | 88.0% | 56.0% |
| median_vote（近傍 3 隻の中央値・多数決） | 63.9% | 15.2% | 94.4% | 71.7% | 34.4% | 54.0% |

学習済みモデルが超えるべき基準。詳細は `data/navspec/shipknow_design_eval_v1/baselines/summary_*.md`、`compare.md`。

## 4. 再現コマンド

### 4.1 Mac（データ生成・検証・テスト）

```bash
cd ~/dev/apps/autoresearch-naval
# データ生成（NSDB は mode=ro で読む）。出力: data/navspec/shipknow_design_eval_v1/
.venv/bin/python tools/shipknow_build_design_eval.py
# 独立再検証（重複・近傍の分割・充足例・不可能性）。ok: true になること
.venv/bin/python tools/shipknow_build_design_eval.py --verify-only
# 規則の誤検出率（実艦の正解設計を予測として採点）
.venv/bin/python tools/shipknow_design_eval.py sanity
# 非 LLM ベースライン
.venv/bin/python tools/shipknow_design_eval.py baselines --out-dir data/navspec/shipknow_design_eval_v1/baselines
# テスト（全体）
.venv/bin/python -m pytest tests -q
```

### 4.2 llm_master へ配置（ユーザーが実行。想定する配置）

```
~/navspec-qwen35/shipknow/tools/shipknow_infer_hf.py
~/navspec-qwen35/shipknow/design_eval_v1/data/{all.jsonl, manifest.json, forget_ids.json}   # all.jsonl は problems + pairs
~/navspec-qwen35/shipknow/v1_2/data/{test.jsonl, manifest.json}                              # T1〜T3 の test（忘却評価用。既存）
```

```bash
# Mac から（例。ホスト名は環境に合わせる）
rsync -av tools/shipknow_infer_hf.py llm_master:~/navspec-qwen35/shipknow/tools/
rsync -av data/navspec/shipknow_design_eval_v1/{all.jsonl,manifest.json,forget_ids.json} llm_master:~/navspec-qwen35/shipknow/design_eval_v1/data/
```

### 4.3 llm_master で推論（RTX 3060 固定）

```bash
cd ~/navspec-qwen35
export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=<RTX 3060 の UUID>
mkdir -p shipknow/design_eval_v1/preds shipknow/design_eval_v1/bs_test

# --- (a) バッチ試験: design-v0 best で seed 1001、バッチ 8 / 16 / 32。VRAM とトークン/秒を meta に記録する
BEST=~/navspec-qwen35/shipknow/design_v0/out/cont_v1_2/best
for BS in 8 16 32; do
  python shipknow/tools/shipknow_infer_hf.py --adapter $BEST --test shipknow/design_eval_v1/data/all.jsonl --tasks D \
    --sample-seed 1001 --temperature 0.7 --top-p 1.0 --batch-size $BS --out shipknow/design_eval_v1/bs_test/best_bs${BS}.jsonl
done
grep -H -E '"(batch_size|peak_gib|peak_gib_after_load|total_sec|generated_tokens|generated_tokens_per_sec)"' shipknow/design_eval_v1/bs_test/*_meta.json

# --- (b) 本番: 3 モデル × seed 1001 / 1002 / 1003（サンプリング、温度 0.7）。バッチは (a) で決めた 1 値を全モデルで共通にする（BS）
BS=16
V12=~/navspec-qwen35/shipknow/v1_2/out/rag_k3_resume/best
FINAL=~/navspec-qwen35/shipknow/design_v0/out/cont_v1_2/final
for M in v1_2:$V12 design_best:$BEST design_final:$FINAL; do
  NAME=${M%%:*}; AD=${M#*:}
  for S in 1001 1002 1003; do
    python shipknow/tools/shipknow_infer_hf.py --adapter $AD --test shipknow/design_eval_v1/data/all.jsonl --tasks D \
      --sample-seed $S --temperature 0.7 --top-p 1.0 --batch-size $BS --out shipknow/design_eval_v1/preds/${NAME}_s${S}.jsonl
  done
  # 参考: greedy（seed なし）。採点では別名（${NAME}_greedy）で読む
  python shipknow/tools/shipknow_infer_hf.py --adapter $AD --test shipknow/design_eval_v1/data/all.jsonl --tasks D \
    --batch-size $BS --out shipknow/design_eval_v1/preds/${NAME}_greedy.jsonl
done

# --- (c) T1〜T3 の忘却評価: forget_ids.json の 3,000 件（T1/T2/T3 各 1,000）
for M in v1_2:$V12 design_best:$BEST design_final:$FINAL; do
  NAME=${M%%:*}; AD=${M#*:}
  python shipknow/tools/shipknow_infer_hf.py --adapter $AD --test shipknow/v1_2/data/test.jsonl --tasks T1,T2,T3 \
    --ids shipknow/design_eval_v1/data/forget_ids.json --batch-size 32 --out shipknow/design_eval_v1/preds/forget_${NAME}.jsonl
done
```

出力ファイル名にドット（`.`）を入れない（meta は `<out の名前の最初のドットまで>_meta.json`）。`all.jsonl` を使うので problems と pairs の予測は 1 つのファイルに入る。
サンプリング結果は入力の並び（プロンプト長の安定ソート。モデルによらず同一）とバッチ分割に依存するので、モデル間比較は同じ `--batch-size` で行う（バッチ試験の出力は比較に使わない）。

### 4.4 Mac で採点

```bash
cd ~/dev/apps/autoresearch-naval
D=data/navspec/shipknow_design_eval_v1
mkdir -p out/design_eval && rsync -av llm_master:~/navspec-qwen35/shipknow/design_eval_v1/preds/ out/design_eval/preds/
.venv/bin/python tools/shipknow_design_eval.py score --problems $D/problems.jsonl --pairs $D/pairs.jsonl --out-dir out/design_eval/score \
  --pred v1_2=out/design_eval/preds/v1_2_s1001.jsonl --pred v1_2=out/design_eval/preds/v1_2_s1002.jsonl --pred v1_2=out/design_eval/preds/v1_2_s1003.jsonl \
  --pred design_best=out/design_eval/preds/design_best_s1001.jsonl --pred design_best=out/design_eval/preds/design_best_s1002.jsonl --pred design_best=out/design_eval/preds/design_best_s1003.jsonl \
  --pred design_final=out/design_eval/preds/design_final_s1001.jsonl --pred design_final=out/design_eval/preds/design_final_s1002.jsonl --pred design_final=out/design_eval/preds/design_final_s1003.jsonl
# -> out/design_eval/score/{rows_<model>.jsonl, summary_<model>.{json,md}, compare.md}（基準 = 最初のモデル = v1_2）

# 忘却評価: 採点用の test を 3,000 件に絞り（manifest を同じ場所に置く）、既存の採点器で採点
.venv/bin/python tools/shipknow_design_eval.py forget-test --test <v1_2 の test.jsonl> --ids $D/forget_ids.json --out-dir out/design_eval/forget_test
for M in v1_2 design_best design_final; do
  .venv/bin/python tools/shipknow_score.py --test out/design_eval/forget_test/test.jsonl --pred out/design_eval/preds/forget_${M}.jsonl --out out/design_eval/score/forget_${M}.json
done
```

### 4.5 忘却評価サブセット（`forget_ids.json`）

`data/navspec/shipknow_v1_2_rag/test.jsonl` から T1 / T2 / T3 各 1,000 件を、(task, variant, 対象艦級の coarse 艦種) で層化し（比例配分、端数は最大剰余法）、seed 20261008 で抽出した id。`ids`（タスク別）と `all_ids`（3,000 件）を持つ。推論の `--ids` は `all_ids`（または `ids`）を読む。利用可能数は T1 2,082、T2 2,032、T3 2,032、層は 52 / 52 / 76。

## 5. 限界・注意

- **B・C の witness は train の実艦**。design-v0 はその艦自身を（別の要求で）学習している。B・C の丸写し率・要求充足は記憶の影響を受けうる。A（艦種・国・年の分布から作る）と D・E（手書き）には単一の witness が無い。
- 拡張キー（砲身数・搭載機数・魚雷管数・推進）は design-v0 の学習データに無かった。これらの種別の充足率が低いときは、「設計能力」より「見たことのない要求形式」の影響がある。
- A の要求は実艦分布の分位から機械的に作った（実際の依頼より整っている）。要求文は v0 と同じ 3 文型。
- 近傍検索は架空前提を知らない（D の「戦勝国日本」でも近傍は史実の日本艦）。D の架空国家は近傍が他国艦だけになる。
- 整合性・plausibility の閾値は train の実艦分布から作った統計的な歯止めで、設計の正しさの保証ではない。実艦でも誤検出 3.4%、6 項目が全て範囲内に入る率は 53.6%。
- ペアの期待方向は、モデルが近傍と要求文の両方から読み取れる単調な変化だけを選んだが、設計として唯一の正解ではない（例: 排水量上限が 25% 下がっても、元から上限より小さい設計は変わらなくてよい）。各組は A / B とも「train の実艦で充足できる」ことを確認してある。
- E の「矛盾を認識」の検出規則は、現状の学習データに拒否応答が無いため事前に定義した。モデルが別の言い回しで認識を示した場合は拾えない（行の `marker` と生出力で確認する）。
- 学習前 v1_2 は D を学習していないので、`req` / `nbr` の読み方も出力形式（設計 JSON）も知らない可能性が高い。JSON 成功率・スキーマ合格率が先に効く。要求充足率は、JSON 有効行の判定可能分母（`req_sat_mean_determinable`）と、全行基準（`req_sat_mean_strict`）の両方を見る。
- Navypedia の文章を 90 字に切って入力に使う点は v0 と同じ。評価データ・予測を外部に出すときは出典と利用条件を別途確認する。
