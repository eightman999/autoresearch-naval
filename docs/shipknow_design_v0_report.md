# shipknow 設計タスク D v0 レポート: 要求 → 設計 JSON → deterministic validator の試作

作成: 2026-10-07 / 前段: [shipknow_v0_plan.md](shipknow_v0_plan.md)「方針の確定」、[shipknow_v1_report.md](shipknow_v1_report.md)
データ: `data/navspec/shipknow_design_v0/`（train / valid / test / scenarios、manifest、validator 閾値表、評価結果）。**学習はしていない**（Mac 上のデータ・検査器・採点器の整備まで。GPU 学習・他マシンへの接続なし）。

## 1. 結論

- 設計タスクの試作データ（train 8,035 / valid 972 / test 989 例、艦級グループ単位の分割は `shipknow_v0_2` のまま）、validator、採点器、非 LLM ベースライン、架空シナリオ 29 件を作った。LLM は年表抽出・要求生成・検索・検査のどこにも使っていない。
- validator は、実在艦の正解設計（test 989 例）の **3.4% を誤検出**（valid 4.0%）。要求層の誤検出は 0。壊した設計の検出率は全体で 79%（要求層を使わず物理・年代・艦種だけなら 62%）。年代外の装備（SAM・SSM・レーダー・ヘリ・ガスタービン・原子力・CIWS）と艦種矛盾（潜水艦にヘリ、小型艦に 305mm 砲）は 98〜100%。一方、**寸法や出力の「ちょっとした」改変（長さ ×1.5、速力 ×1.6）は 44〜58% しか検出できない**（実艦のばらつきが大きい、7 節）。
- 1 例の平均は 1,157 トークン（p95 1,514、最大 1,886）。train 930 万トークン = RTX 3060（約 2,850 tok/s）で **1 epoch 約 0.91 時間**。近傍表示は v1_2 の T2 より約 8% 短い（キー短縮で −13%）が、システム文・要求・年表・設計 JSON（平均 154 トークン）が加わるので、**1 例あたりは v1_2 の各タスク（744〜925）より長い**。最大長は v1_2 の 1,470 を 28% 上回るため、勾配チェックポイントは必要。
- 近傍をそのまま返すだけの非 LLM ベースラインは、要求を満たす率が 32〜36% しかない（排水量上限・最低速力・必須装備のどれかを外す）。学習したモデルが超えるべき基準は「要求充足率 36%、validator 合格率 36%、数値の中央値相対誤差 11〜13%、weapons F1 0.54〜0.58」。

## 2. 作成物

| 種別 | パス |
|---|---|
| 年表抽出 | `tools/shipknow_history.py`（CLI: `stats` / `audit` / `show`） |
| 共通部品 | `tools/shipknow_design_common.py`（設計 JSON、能力語彙、比、任務タグ、要求生成と要求文、近傍の短い表示） |
| データ生成 | `tools/shipknow_build_design.py`（`--verify-only` で独立再検証） |
| validator | `tools/shipknow_design_validator.py`（`validate()`、CLI: `build` / `eval`） |
| 採点器・ベースライン | `tools/shipknow_design_score.py`（`score` / `baselines`） |
| テスト | `tests/test_shipknow_design.py`（32 件）。リポジトリ全体 366 件 PASS（元 334 件 + 32 件） |
| データ | `data/navspec/shipknow_design_v0/{train,valid,test,scenarios}.jsonl, manifest.json, validator_thresholds.json, validator_eval.json, baselines_{test,valid,scenarios}.json, baseline_preds/` |

既存ファイル（`tools/shipknow_*.py` 含む）は変更していない。NSDB は `mode=ro` で読んだだけ。コミットしていない。

## 3. 改装・艦歴の年表抽出（`tools/shipknow_history.py`）

### 3.1 規則

イベント = (年, 短文 ≤200 字, 関係する装備 kind) に、`add` / `remove`（改装で足した / 外した kind）、`src`（mod / svc / proj）、`etype`（refit / loss / transfer / conversion / disposal / capture / commission / damage / service / design）を付けた辞書。

- **Modernizations**（1 行 1 改装）: 行頭の日付を年にする。`7.1941:` `4/1942:` `1923, Politruk:` `(Spring 1942, Bombay DYd), X:` `mid-1990s, all:` `late 1820s,` `1935 - 1939,` `1880s-1890s`。年代は中央値（`mid`=+5、`early`=+2、`late`=+8）で、`prec` に exact / decade / approx、範囲は `year_end`。`PC-1586` `No.1650` `1923DT` のような艦番号・型式は年にしない。`:` の後ろが text で、艦名の接頭辞は外す。`- 撤去; + 追加` を分けて add / remove にする（符号の無い節は「その時点の状態」= add、`was removed` を含む節は remove）。
- **Naval_service / Project_history**: 文に分け、4 桁年を含む文だけを出来事にする（`20.12.1941` / `in March 1942`）。`©`・謝辞・"No significant events" は捨てる。service は最大 10 件、project は装備語を含む文を優先して最大 6 件。
- **kind**: gun / mg / torpedo_tube / asw_weapon / ssm / sam / ciws / ballistic_missile / rocket_launcher / helicopter / aircraft / radar / sonar / esm_ecm / mine / propulsion / armour。service 文は "air torpedo" など攻撃側の語が多いので、kind は conversion の文だけに付ける。
- text は 200 字で切る（Navypedia の長文の丸写しを避ける）。近傍の入力では更に 90 字に切り、年表は最大 3 件。

### 3.2 抽出率（CLASS 10,924 件）

| 節 | 内容がある艦級 | 1 件以上取れた艦級 | 率 |
|---|---:|---:|---:|
| Modernizations（"None" 等を除く） | 3,781 | 3,733 | 98.7% |
| Naval_service（定型文を除く） | 3,665 | 3,286 | 89.7% |
| Project_history | 10,771 | 4,065 | 37.7%（残りは年のない記述） |

Modernizations は 10,338 行中 10,244 行（99.1%）で年が取れた。出来事は全体で 24,843 件。どれかの節から 1 件以上取れた艦級は 6,609。設計タスクの適格 7,285 艦級では 4,832 艦級（66%）、改装（mod）があるのは 3,035 艦級。

### 3.3 目視確認（ランダム 20 艦 ×2 回）

- seed 11 の 20 艦級（91 件）で原文と並べて確認し、見つけた誤りを直した。直した点: `1923DT`（魚雷の型式）を年と読んだ、"was removed" の節を追加側に入れた、service 文の "torpedo" / "aircraft" を装備と読んだ、`©` が最後の文に付いて文ごと捨てた、出来事の分類（`raised` → capture、`returned to service` → transfer）、ヘッダが 160 字を超える行で艦名が text に残った。
- 修正後に **別の seed 21 の 20 艦級（約 100 件）** を見直した（独立な確認）。**年の誤りは 0 件**。装備 kind の誤りは 3 件（"torpedo boat" を魚雷と読んだ 2 件、"armoured gunboats" を armour と読んだ 1 件。この 3 件の語は直した）。残る既知の取りこぼし: 魚雷の `TC` 表記、複数艦の行（`1970, A, B; 1971, C, D: ...`）は最初の年だけ、`etype`（loss / disposal などの分類）は文頭の最初の語で決めるため、複文では外れることがある。年表は「いつ・何を足した / 外したか」の補助情報で、`etype` は表示の優先順位にしか使っていない。

## 4. データ D

### 4.1 対象

`shipknow_v0_2` の 10,580 艦級のうち **7,285 艦級**を使う。除外: 譲渡・取得艦（`year_in_navy − year > 5`、要求の国・年が元艦の設計と合わない）1,724、帆走艦・戦列艦・初期汽走艦・不明（`sailing_ship / ship_of_the_line / early_warship / unknown`）588、武装が解析できない / 空 584、速力か全長が無い 212、排水量が無い 99、1860 年より前・年不明 88。decade 精度の年（`1970s`）は十年の中央に置く。

| 分割 | 艦級 | 例 |
|---|---:|---:|
| train | 5,833 | 8,035 |
| valid | 719 | 972 |
| test | 733 | 989 |

必須装備の候補が 3 つ以上ある艦級は 2 例（乱数と文型が違う要求）、それ以外は 1 例。分割は `splits.json` の艦級グループ（同名・書き分け・譲渡艦をまとめた単位）をそのまま使い、近傍は train のみ。

### 4.2 要求の逆算規則（元艦の丸めた設計値から）

| 項目 | 規則 |
|---|---|
| nation / year | 元艦の国、設計年（decade 精度は +5） |
| 艦種 | `ship_type`（coarse）は常に、`ship_type_fine` は 40% |
| 任務 `mission` | 4.3 の規則（最大 2 タグ） |
| 排水量上限 | 基準 = 常備 > 基準 > 満載 の順で元艦が持つもの（実際は常備 87%、満載 13%）。上限 = 実値×(1+U[5,20]%) を 2 桁に丸める（帯 +5〜+20% を外れるときは切り上げ。実現値は p5 1.058 / 中央 1.125 / p95 1.195、小型艇では整数化のため最大 1.5） |
| 最低速力 | 実値 − U[0,2] kn を 0.5 kn に切り下げ |
| 必須装備 | 元艦の装備から抽象化した候補（`main_gun>=Nmm`〈N は 20〜406 の格子で実口径以下の最大〉、`torpedo_tubes`、`asw_weapon`、`sonar`、`helicopter`、`aircraft`、`ssm`、`sam`、`ciws`、`rocket_launcher`、`ballistic_missile`、`air_search_radar`、`fire_control_radar`、`surface_radar`、`radar`〈いずれかのレーダー〉、`esm_ecm`、`mines`、`minesweeping`）から、1 個 35% / 2 個 45% / 3 個 20% を、珍しいもの（SAM・SSM・対空捜索レーダー 3.0、ヘリ 2.5、…、主砲 1.0）を出やすくする重みで選ぶ |
| 乗員上限 | 28% の例。実値×(1+U[5,25]%) |
| 航続下限 | 22% の例。実値×(1−U[0,15]%) |
| 要求文 | 英語 1〜2 文（3 通りの文型。必須装備があると 2 文目）。日本語版は `meta.request_ja` に併記（入力には出さない） |

元艦の設計は必ず自分の要求を満たす（検査済み。`gold_req_violations` = 0）。必須装備の出現数は train で主砲 3,593、レーダー 2,097、魚雷 2,024、ソナー 1,143、対潜 1,036、…、ヘリ 346、SSM 300、SAM 241、対空捜索レーダー 76（`manifest.json`）。必須装備が空の例は 2%。

### 4.3 任務タグ（規則）

`amphibious` / `mine_warfare` / `submarine_ops` / `carrier_air` / `support`（補助艦）/ `training` は艦種 coarse で決まる。哨戒艇・沿岸警備艇・河川砲艦は `patrol`（+ 対潜装備とソナーがあれば `asw`、75mm 以上の砲を持つ砲艦は `fire_support`）、水雷艇・高速攻撃艇は `surface_strike`、沿岸防衛艦は `coastal_defense`（+ 200mm 以上で `fire_support`）。駆逐艦から戦艦までの水上戦闘艦は装備から点数をつける: asw = 2·対潜兵装 + 1·ソナー + 1·ヘリ、aaw = 2·SAM + 1.5·CIWS + 1·対空捜索レーダー（+2 if fine に air-defence / aa）、surface_strike = 2·SSM + 1.5·魚雷 + 1·(127mm 以上の砲) + 2·弾道、fire_support = 2·(152mm 以上の砲を持つ大型艦) + 1·ロケット。点数 2 以上の上位 2 個（護衛系の艦種は `escort` を先頭に）、無ければ大型艦は `surface_strike`、他は `general`。train での上位: patrol 1,631、surface_strike 1,509、submarine_ops 946、mine_warfare 772、escort+asw 523、amphibious 453、patrol+asw 403。

### 4.4 近傍の検索（規則 NB-1）と年表

k=3、train の実艦のみ、同じ艦級グループを除外。検索は要求に書いてある項目だけ（艦種、細分類、年、排水量 = 上限/1.125、速力 = 最低 + 1、乗員、航続、必須装備の kind と砲口径）。v1 の距離（`shipknow_retriever.Pool.distances`）を使い、重みは valid で数通りを試して手で決めた（排水量 4.0、年 0.7、細分類 1.2、艦種 3.0、国 0）。

1. 要求年 +2 年より後の艦は出さない（未来の艦を参考にしない）。
2. **同国の系譜枠**: 同国の艦から最大 2 隻。細分類の不一致と年の重みを弱めた距離（細分類 0.2、年 0.3）で近い順、距離 2.0 以内（粗い艦種が違う艦は入らない）。
3. **同時代の他国枠**: 他国の艦（要求年 −10 年以降）から、通常の距離で全体 3 位 +0.8 以内の最も近い艦。
4. 枠が埋まらなければ全体の近い順で補う。表示は近い順。
5. 各近傍に、改装・艦歴の年表を**要求年以前の出来事だけ**、最大 3 件（装備を足した改装 → 引き渡し・改造・就役 → 設計史の順。同年・近似文は 1 件にまとめる）。train で近傍の 55% が同国、45% が年表を持つ。
6. **対象艦自身の艦名・艦級名・艦歴・改装は入れない**。近傍の年表に対象艦の艦名・艦級名（SHIPS.NAME と CLASS_NAME から作る）が出てくる出来事、対象艦自身の出来事と先頭 50 字が同じ出来事（姉妹艦級のページが同じ文を載せている）は捨てる。

同じ規則をシナリオ（7 節）にも使っている（学習と推論で近傍の作り方がずれない）。

### 4.5 入力と正解

- system（352 トークン、全例共通。全文は `manifest.json` の `system_prompt`）: 出力の JSON 仕様、要求を満たし物理的に整合し年代に合う設計にすること、近傍のキー凡例。
- user: `{"request": 英語の要求文, "req": 構造化した要求, "nbr": [近傍 3 隻]}`（JSON 1 つ）。
- 近傍の表示（v1_2 より短くする工夫）: キーを短縮（`cn y ty ft dn df ds L B T v hp sh cm en pr w s air ar h`）、距離 `d`・`year_in_navy`・艦名・id・URL を出さない、`ds` が `dn` と同じなら出さない、weapons は既存の compact 形式（kind / n / per / cal）のまま。
- assistant: 設計 JSON のみ（説明文なし）。`ship_type`、`ship_type_fine`、排水量（常備・満載・基準のあるもの）、全長・全幅・喫水、`propulsion`、`shafts`、`power_hp`、`speed_kn`、`endurance_nm`、`complement`、`armour{belt,deck,turret}`、`weapons`、`sensors`、`air`（`helicopter` / `seaplane` / `aircraft`）。値が無い項目は出さない。`air` は、weapons の `aircraft_helicopter` だけでは回転翼機かどうかが分からず、年代検査と必須装備 `helicopter` の判定に要るため追加した。

### 4.6 漏れ防止の検査（`--verify-only`、全 9,996 例）

| 検査 | 件数 |
|---|---:|
| 近傍が train 外 / 同じ艦級グループ / 自分自身 / 要求年 +2 より後 | 0 / 0 / 0 / 0 |
| 分割の食い違い、JSON 不正 | 0、0 |
| 近傍の年表で要求年より後の出来事 | 0 |
| 対象艦の艦名・艦級名が年表に出る | 0 |
| 対象艦自身の艦歴（先頭 50 字）が入力にある | 0 |
| 元艦の設計が自分の要求を満たさない | 0 |
| （参考）艦名・艦級名と同じ語が年表以外に出る | 15（艦級名が "Weapon" / "Turbine" / 国名のような一般語。艦名の漏れではない） |

### 4.7 トークン長と学習時間（Qwen3.5-0.8B tokenizer、ChatML + 6）

| | 例 | 平均 | p95 | 最大 | assistant 平均 | 近傍表示 平均（年表なし / あり） |
|---|---:|---:|---:|---:|---:|---:|
| train | 8,035 | 1,157 | 1,514 | 1,886 | 154 | 410 / 475 |
| valid | 972 | 1,171 | 1,573 | 1,800 | 155 | 423 / 512 |
| test | 989 | 1,151 | 1,496 | 1,787 | 150 | 416 / 498 |

- 内訳（平均）: system 352、要求（文 + 構造化）130、近傍 475、assistant 154、ChatML 等の残り。
- **v1_2 との比較**（同じ tokenizer）: 1 例の平均は T1 925 / T2 902 / T3 744（p95 1,214 / 1,170 / 899、最大 1,470 / 1,431 / 1,078）。近傍の JSON だけを測ると v1_2 は T2 447 / T3 400、D は年表なしで 410。D の近傍表示は T2 より 8% 短いが、装備・寸法・センサー・aircraft・装甲を全部載せている。キー短縮の効果は 474 → 410（−13%）。weapons を配列形式（`["gun",1,2,23]`）にすると更に −7% だが、出力の dict 形式との変換をモデルに学ばせることになるので採らなかった。年表は +65（45% の近傍だけが持つ）。
- つまり**近傍表示は短くなったが、1 例は v1_2 より長い**（system と設計 JSON 出力のため）。メモリは長い順（最大 1,886）で決まるので、勾配チェックポイントの導入が前提。system を 150 トークンに削る（−200）、年表を落とす（−65）で合わせて約 23% 減らせる。
- **学習時間の見積もり**: train 9,298,500 トークン ÷ 2,850 tok/s = 3,262 秒 ≈ **0.91 時間 / epoch**（v1_2 の 4,296 万トークンは約 4.2 時間）。3 epoch で約 2.7 時間。valid / test の推論は、1 例の出力が約 154 トークンなので、test 989 例でも短時間。

## 5. validator（`tools/shipknow_design_validator.py`）

### 5.1 検査

`validate(requirements, design) -> {"ok", "violations": [{code, field, detail}], "warnings"}`。`requirements=None` なら要求以外の層だけ。

| 層（code 接頭辞） | 内容 |
|---|---|
| `schema_*` | JSON オブジェクト、艦種ラベル、weapons の kind / n / per / cal、センサー kind、正の数と値域、排水量・全長・速力の存在 |
| `req_*` | 排水量（要求が指す基準の項目）≤ 上限、速力 ≥ 最低、必須装備、艦種（coarse / 書いてあれば fine）の一致、乗員上限、航続下限、設計に `country` / `year` があれば要求との一致 |
| `ratio_*` | L/B、B/T、Δ/(L·B·T)（常備・満載・基準別のブロック係数相当）、Δ^(2/3)·V³/P（Admiralty 係数相当）、V/√L、P/Δ、乗員/Δ、L/Δ^(1/3)、満載/常備 |
| `gun_*` | 主砲口径が、艦種または排水量帯の train 最大の 1.25 倍を超える |
| `era_early` | 装備・センサー・機関の初出年より 5 年以上前の採用 |
| `type_*` | 潜水艦にヘリ・回転翼以外の航空機（1945 年以降は水上機も）、小型艦種（patrol / coast_guard / gunboat_river / 高速攻撃艇 / 水雷艇 / 掃海 / 揚陸 / 補助 / 練習 / コルベット）に 203mm 超の砲（19 世紀の砲艦は 320mm）、水上艦以外に弾道ミサイル、小型艇に回転翼機 |
| warning | `[p2, p98]` 外れ（`ratio_*_soft`）、**国別初出年より 5 年以上早い**（`era_nation_early`。その国の train の艦級が持たない技術。架空シナリオでは起こりうるので違反にしない） |

### 5.2 閾値表（`validator_thresholds.json`、train の実艦 5,833 設計から）

- 比の物理的な目安は、(艦種 × 年代) の層（年代は 1860-99 / 1900-19 / 1920-44 / 1945-69 / 1970-）ごとに、`log(比) = a + Σ b·log(説明変数)`（説明変数は排水量、必要なら速力か V/√L）を回帰し、**残差の [p0.5, p99.5] を幅の 25% だけ広げた範囲**を外れたら違反、`[p2, p98]` を外れたら warning。サイズと速力に依存する分を除くので、単純な分位点より検出力が高い（同じ誤検出率で検出率が約 +4〜6pt）。層は 67（n<40 の層は使わず、(艦種, 全年代) → (全艦種, 年代) → 全体の順にフォールバック）。駆逐艦 1920-44 年（n=104）では、残差の p0.5〜p99.5 が L/B で −12%〜+11%、Admiralty 係数で −24%〜+22%、ブロック係数（常備）で −22%〜+33%。残差幅の下限を 0.2（対数）にして、ほぼ定数の項目で丸めが違反にならないようにしてある。
- 初出年は、train の艦級グループごとに最初の年を取り、**3 番目に早い年**（CIWS だけ 5 番目。パーサが 19 世紀の多連装機砲を ciws と分類する 4 グループがあるため）。装備は艦級の武装、改装履歴（add に入った kind）からも求めて、早い方を採る。

| 能力 | 初出年 | 能力 | 初出年 | 能力 | 初出年 |
|---|---:|---|---:|---|---:|
| 魚雷発射管 | 1875 | 対潜（爆雷等） | 1915 | 水上機 | 1922 |
| 航空機 | 1917 | ソナー | 1917 | レーダー（いずれか） | 1935 |
| ESM/ECM | 1940 | 対潜ロケット | 1941 | ロケット発射機 | 1940 |
| 射撃管制レーダー | 1942 | 航法レーダー | 1944 | 対水上レーダー | 1948 |
| ヘリ | 1955 | 対空捜索レーダー | 1955 | SSM | 1958 |
| SAM | 1959 | 弾道ミサイル | 1960 | 原子力 | 1959 |
| ガスタービン | 1943 | CIWS | 1969 | EO 指揮装置 | 1975 |

（ヘリの 1955 は train の艦級の設計年で、実世界の初搭載より遅い。5 年の許容を引いて、1950 年より前のヘリ搭載は違反になる）

### 5.3 品質指標

**誤検出率**（実在艦の正解設計に、その要求と一緒にかけた）:

| | n | 違反あり | 要求層 | 要求を除く検査 | warning あり |
|---|---:|---:|---:|---:|---:|
| valid | 972 | 4.0% | 0% | 4.0% | 35% |
| **test** | 989 | **3.4%** | 0% | 3.4% | 40% |

test の違反（34 設計、46 件）は全て `ratio_*`（ブロック係数 15、V/√L 11、P/Δ 8、Admiralty 係数 7、L/Δ^(1/3) 2、乗員/Δ 2、満載/常備 1）。DB の数値の誤り・極端な実艦（valid の例: 全長/全幅 4 の空母）が混じっており、数 % 以下には下がるが 0 にはならない。誤検出を valid で調整した（margin 0〜0.4 の掃引、valid）:

| margin | 0.0 | 0.1 | 0.2 | **0.25（採用）** | 0.3 | 0.4 |
|---|---:|---:|---:|---:|---:|---:|
| 誤検出率 | 16.9% | 7.8% | 4.9% | 4.0% | 2.9% | 1.8% |
| 物理のみの検出率（全体） | 81.4% | 74.3% | 67.2% | 63.1% | 59.9% | 52.9% |

**検出率**（test の正解設計を機械的に壊す。項目ごとに 989 例に 1 回）:

| 壊し方 | n | 検出（全検査） | 要求を使わない検査だけ |
|---|---:|---:|---:|
| 排水量 ×3（全項目） | 989 | 100% | 74.6% |
| 排水量 ×1.5 | 989 | 100% | 22.6% |
| 全長 ×1.5（排水量そのまま） | 989 | 58.4% | 58.4% |
| 全幅 ×2 | 989 | 70.6% | 70.6% |
| 喫水 ×3 | 989 | 79.6% | 79.6% |
| 速力 ×1.6（出力そのまま） | 989 | 44.3% | 44.3% |
| 速力 +15 kn（出力そのまま） | 989 | 55.6% | 55.6% |
| 出力 ×5（速力そのまま） | 989 | 72.4% | 72.4% |
| 乗員 ×8 | 989 | 88.5% | 87.0% |
| 年代外の SAM / SSM / ヘリ / ガスタービン / 原子力 / CIWS / レーダー | 31〜265 | 100% | 100% |
| 潜水艦にヘリ | 133 | 100% | 100% |
| 小型艦種に 305mm 砲 | 582 | 97.8% | 97.8% |
| 必須装備を 1 つ外す | 961 | 97.4%（要求層） | 3.5% |
| 全体（11,056 件） | | **79.0%** | **61.5%** |

読み方: 年代と艦種の矛盾は 98〜100% 捕まえる（年代外は設計年が初出年より 10 年以上前の例だけに足したので、易しい条件）。排水量を大きく変える壊し方は要求の上限で必ず捕まる（物理だけだと ×3 で 75%、×1.5 で 23%）。**寸法・速力・出力の中程度の改変は半分前後しか捕まらない**。実艦の (比) の幅が広く、誤検出を 3〜4% に抑えると狭められないため。LLM の出力が「物理的にあり得ない」ほど外れたときの歯止めとしては使えるが、「もっともらしいが少しずれた」数値は validator では守れない。

## 6. 採点器とベースライン（test、n=989）

`tools/shipknow_design_score.py`: 予測 JSONL（`id` と `text` または `output`）を受け、JSON 不正率、schema 不正率、要求充足率（項目別つき）、validator 合格率（全検査 / 要求を除く）、正解との近さ（艦種・細分類の一致、数値の相対誤差、weapons F1、センサー F1、推進方式の一致）を出す。正解のないシナリオは JSON・要求・validator だけ採点する。

ベースラインは近傍の入力表示だけから作る（モデルが見られるものだけ）。top1 = 近傍で最も近い 1 隻の設計をそのまま。median_vote = 近傍 3 隻の数値の中央値、艦種・推進・センサー・weapons の項目は過半数の近傍にあるものだけ（n / per は中央値）。`*_req_type` は艦種（と指定があれば細分類）だけ要求からコピーしたもの。

| | JSON 不正 | 要求充足 | validator 合格 | 同（要求を除く） | 艦種 / 細分類 | 数値 中央値相対誤差 / ±10% 以内 | weapons F1 | センサー F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| top1 | 0 | 36.4% | 36.2% | 99.3% | 99.9 / 69.4% | 0.111 / 47.2% | 0.539 | 0.628 |
| median_vote | 0 | 32.3% | 31.0% | 93.3% | 99.7 / 70.6% | 0.132 / 42.7% | 0.580 | 0.632 |
| top1_req_type | 0 | 36.7% | 36.5% | 99.3% | 100 / 72.8% | 0.111 / 47.2% | 0.539 | 0.628 |
| median_vote_req_type | 0 | 33.3% | 32.0% | 93.4% | 100 / 76.4% | 0.132 / 42.7% | 0.580 | 0.632 |
| 正解（確認用） | 0 | 100% | 96.6% | 96.6% | 100 / 100% | 0 / 100% | 1.0 | 1.0 |

- top1 の要求項目別充足: 排水量 72%、速力 72%、必須装備 73%、艦種 97%、航続 65%、乗員 77%。**3 つの上限・下限・装備をすべて満たすのは 36% しかない**。排水量の基準の項目を持たない（常備が無く満載だけ等）近傍が 97 例ある。
- 艦種はほぼ要求から決まるので、艦種（coarse）の一致は意味を持たない（ベースラインでも 99.7% 以上）。細分類の一致、数値、装備で見る。
- median_vote は数値の中央値は良いが、中央値を取ると近傍のばらつきの外れ値が出て物理比が崩れる例がある（要求を除く合格 93%）。
- 数値の項目別（top1）の中央値相対誤差: 全長 0.07、速力 0.06、全幅 0.08、常備 0.09、喫水 0.13、出力 0.25、乗員 0.18、航続 0.28。モデルは少なくともこれを超えるべき。
- 近傍の検索は、ベースラインの得点を最大にするようには作っていない（系譜枠のため、要求に最も近い艦より遠い同国の艦も入る）。系譜枠を入れる前（全体の近い順だけ）の top1 は、細分類一致 74%、weapons F1 0.59、数値の中央値相対誤差 0.14 だった。valid / test / シナリオの全指標は `baselines_*.json`。

## 7. 架空シナリオ（`scenarios.jsonl`、29 件）

正解なし。要求と近傍を用意し、モデルの出力を validator と要求充足で採点する。日本・米・英・独・伊・仏・ソ（`Russian`）の 1912〜1990 年、艦種は戦艦・巡洋戦艦・重巡・軽巡・空母・駆逐艦・フリゲート・護衛艦・高速攻撃艇・哨戒艇・沿岸警備艇・潜水艦・掃海・敷設・揚陸。架空前提（戦勝国など）のあるものは 5 件（s01 戦勝国日本の哨戒艇 / s14 ドイツ勝利後の重巡 / s15 戦勝国日本の護衛艦 / s16 戦勝国日本の空母 / s25 戦勝国日本の沿岸警備艇）で、前提は要求文の先頭に書く（`premise`）。validator には国と年だけが入るので、前提が検査に影響することはない（国別初出年の warning が出るだけ）。

**近傍の検索規則**は 4.4 の NB-1 と同じ（学習と同じ関数）。ただし候補は train だけでなく適格な全分割の実艦（正解を使わないので）。「国の技術系譜」= 同国の過去艦（要求年 +2 年以内、細分類を問わず、粗い艦種は一致）から最大 2 隻、「同時代の他国艦」= 他国の要求年 −10 年以降から 1 隻。例（s01 1950 年・日本・哨戒艇）: 日本の 1937 年の駆潜艇 2 隻（年表: 1934 年建造計画、1943 年に爆雷搭載数増加・補助駆潜艇に類別変更）＋ 東ドイツの 1950 年の哨戒艇。s03（1960 年・イタリア・ミサイル駆逐艦）: イタリア 1958 年・1942 年の駆逐艦 ＋ 英国 1962 年のミサイル駆逐艦。s16（1946 年・日本・空母）: 日本の 1944・1941 年の空母 ＋ 米国 1942 年の空母。

集計: 29 件のうち、同国の近傍 2 隻が入ったもの 26 件、同国の候補が無く 0 隻のもの 3 件（s05 ドイツの高速攻撃艇 1942、s07 ソ連のフリゲート 1955、s25 日本の沿岸警備艇 1953。データに該当する同国の艦が無い）。近傍 87 隻のうち年表を持つのは 66 隻（76%）。

非 LLM ベースライン（近傍 top1 / 中央値＋多数決）の要求充足は 34.5% / 20.7%（物理・年代・艦種は 100% / 97%）。

### 入力全文 1: s01（戦勝国日本・1950 年・哨戒艇）

system は 4.5 の共通文（352 トークン）。user（615 トークン）:

```json
{"request":"Assume Japan won the war and kept its navy; this is a postwar coastal patrol vessel. The Japanese navy wants a patrol boat designed in 1950; role: patrol. Limits: displacement at most 150 t (normal), speed at least 22 kn and crew at most 25. It must carry a main gun of at least 20 mm and a radar.","req":{"country":"Japan","year":1950,"ship_type":"patrol","ship_type_fine":"patrol-boat","mission":["patrol"],"max_displacement_t":150,"displacement_basis":"normal","min_speed_kn":22,"required":["main_gun>=20mm","radar"],"max_complement":25},"nbr":[{"cn":"German respublic","y":1950,"ty":"patrol","ft":"patrol-boat","dn":65,"df":70,"L":26.8,"B":4.6,"T":1.4,"v":20,"hp":1600,"sh":3,"cm":20,"en":400,"pr":["diesel"],"w":[{"kind":"gun","n":2,"cal":20}]},{"cn":"Japan","y":1937,"ty":"patrol","ft":"submarine-chaser","dn":170,"df":175,"L":45,"B":4.8,"T":1.7,"v":23,"hp":3000,"sh":2,"cm":41,"pr":["diesel"],"w":[{"kind":"gun","n":1,"cal":40},{"kind":"depth_charge","n":2}],"s":["sonar"],"h":[[1934,"Built under 2nd Supplementary programme of 1934."],[1943,"DC stowage was increased to 20 - 30."],[1943,"In May, 1943 Ch51-52 were re-rated to auxiliary submarine chasers Cha251-252…"]]},{"cn":"Japan","y":1937,"ty":"patrol","ft":"submarine-chaser","dn":170,"df":180,"L":45,"B":4.8,"T":1.75,"v":23,"hp":3000,"sh":2,"cm":41,"pr":["steam_turbine"],"w":[{"kind":"gun","n":1,"cal":40},{"kind":"depth_charge","n":2}],"s":["sonar"],"h":[[1934,"Built under 2nd Supplementary programme of 1934."],[1943,"DC stowage was increased to 20 - 30."]]}]}
```

日本語版（`request_ja`、入力には出さない）: Assume Japan won the war and kept its navy; this is a postwar coastal patrol vessel. 1950年ごろの日本海軍の哨戒艇・巡視艇（patrol-boat）を設計してください。任務: 哨戒。常備排水量150t以下、速力22kn以上、乗員25名以下。必須装備: 口径20mm以上の主砲、レーダー。

### 入力全文 2: s03（1960 年・イタリア・ミサイル駆逐艦）

user（826 トークン）:

```json
{"request":"Design a missile destroyer for the Italian navy around 1960, for air defence and anti-submarine warfare: displacement at most 4200 t (full) and speed at least 32 kn. It must carry surface-to-air missiles, a helicopter and sonar.","req":{"country":"Italy","year":1960,"ship_type":"destroyer","ship_type_fine":"missile-destroyer","mission":["aaw","asw"],"max_displacement_t":4200,"displacement_basis":"full","min_speed_kn":32,"required":["sam","helicopter","sonar"]},"nbr":[{"cn":"United Kingdom","y":1962,"ty":"destroyer","ft":"missile-destroyer","dn":5440,"df":6800,"L":159,"B":16.4,"T":6.2,"v":32.5,"hp":60000,"sh":2,"cm":440,"en":3500,"pr":["gas_turbine","steam_turbine"],"w":[{"kind":"gun","n":2,"per":2,"cal":120},{"kind":"gun","n":2,"cal":20},{"kind":"sam","n":1,"per":2},{"kind":"sam","n":2,"per":4},{"kind":"aircraft_helicopter","n":1}],"s":["esm_ecm","radar","sonar"],"air":["helicopter"],"h":[[1955,"Four ships were voted under the 1955-56 and 1956-57 Estimates, followed by another four…"]]},{"cn":"Italy","y":1958,"ty":"destroyer","ft":"destroyer","dn":2780,"df":3810,"L":127.5,"B":13.2,"T":4.5,"v":34,"hp":65000,"sh":2,"cm":315,"en":3000,"pr":["steam_turbine"],"w":[{"kind":"gun","n":2,"per":2,"cal":127},{"kind":"gun","n":2,"per":4,"cal":40},{"kind":"gun","n":4,"per":2,"cal":40},{"kind":"torpedo_tube","n":2,"cal":533},{"kind":"asw_rocket","n":1,"per":3,"cal":305},{"kind":"depth_charge","n":5}],"s":["fire_control_radar","sonar","surface_radar"],"h":[[1950,"These two ships were ordered in February 1950, and were the first postwar Italian…"]]},{"cn":"Italy","y":1942,"ty":"destroyer","ft":"destroyer","dn":2070,"df":2900,"L":120.5,"B":12.3,"T":3.6,"v":35,"hp":60000,"sh":2,"cm":270,"pr":["steam_turbine"],"w":[{"kind":"gun","n":4,"cal":135},{"kind":"gun","n":12,"cal":37},{"kind":"torpedo_tube","n":2,"per":3,"cal":533},{"kind":"depth_charge","n":4}],"s":["radar","sonar"],"h":[[1940,"Design works have started late 1940, and to the mid-1942 new destroyer was transformed…"],[1943,"All laid down ships have been captured by German troops in September, 1943 on slipways."]]}]}
```

日本語版: 1960年ごろのイタリア海軍の駆逐艦（missile-destroyer）を設計してください。任務: 防空・対潜戦。満載排水量4200t以下、速力32kn以上。必須装備: 対空ミサイル、ヘリコプター、ソナー。

## 8. 限界と残課題

- **学習・推論は未実施**。D を学習したモデルの出力を validator にかけて初めて、このデータ・検査器の意味（要求を満たす設計を出せるか、近傍の改装年表を使えるか）が分かる。次: 1 epoch（約 0.9 時間）を回し、`score` を test とシナリオにかける。
- **validator は物理の「あり得なさ」の歯止めで、設計の正しさの保証ではない**（5.3）。中程度の改変の検出率は半分前後。装備の重量・載せられるかどうかの検査（武装重量 / 排水量、主砲と船体、機関の重量）は入っていない。入れるなら、艦種ごとの武装重量のデータが必要。
- **年代検査の初出年は train の艦級の設計年**で、実世界の初出より遅い（ヘリ 1955）。5 年の許容を引いても、1946〜49 年のヘリ搭載（実在した）は違反になる。国別の初出年は warning のみ。
- 要求は元艦から逆算した合成で、実際の依頼より整っている（必ず元艦で満たせる）。要求の言い回しは 3 文型だけ。必須装備の抽象化は粗い（`radar` はどのレーダーでも、`main_gun>=76mm` は 76mm 以上のどれでも満たす）。
- 譲渡艦（要求の国・年が合わない）を 1,724 艦級除いた。帆走艦・戦列艦も除いた。1860 年より前の設計は扱わない。
- 近傍の年表は短文（90 字）で、`etype` の分類や kind の抽出は規則ベースなので誤りが残る（3.3）。複数艦の行の年、魚雷の `TC` 表記などは未対応。ローカル LLM での補完は未実施。Navypedia の文章を 90 字に切って入力に使う。**学習済みモデルや派生データの外部公開では、出典と利用条件を別途確認する**（v0 計画書と同じ注意）。
- 近傍の検索重みは valid で数通りを手で比べただけ。系譜枠（同国の過去艦）は「その国の技術系譜」を見せる意図だが、近いほど良いとは限らず、top1 ベースラインの細分類一致・weapons F1 は系譜枠で下がる（74% → 69%、0.59 → 0.54。数値の誤差は 0.14 → 0.11 に改善）。k=5 や年表の長さの比較は未実施。
- 架空前提の扱いは要求文に書くだけで、近傍検索は前提を知らない（例: 「戦勝国日本」でも近傍は史実の日本艦）。前提を反映した近傍の選び方（例: 敗戦後の制約を外す）は人が `scenarios.jsonl` を編集する運用になる。
- メモリ: 最大 1,886 トークンで v1_2（1,470）より長い。勾配チェックポイント・system の短縮・年表を落とす選択肢が要る（4.7）。実測は 3060 が空いてから。

## 追記: 継続学習の結果（2026-10-07）

- 出発点: v1_2 の最終アダプタ（`shipknow/v1_2/out/rag_k3_resume/best`。valid 0.207）。
- データ: 設計 D 8,035 件と、再生 T1〜T3 各 1,500 件。
- 設定: 2 epoch = 1,567 step。勾配チェックポイントあり（学習中のピーク 3.9GiB、評価込みの全体 9.1GiB）。2,020 tok/s で 4.0 時間。
- best は step 1400（D の valid loss 0.2532）。

| step | D | T1 | T2 | T3 |
|---|---:|---:|---:|---:|
| 0 | 1.148 | 0.109 | 0.192 | 0.350 |
| 600 | 0.254 | 0.113 | 0.199 | 0.353 |
| 1400（best） | **0.253** | 0.115 | 0.214 | 0.359 |
| 1567（final） | 0.255 | 0.116 | 0.216 | 0.360 |

- D は 200 step で 1.15 → 0.27 まで下がり、600 step 以降はほぼ横ばい。2 epoch 目はほとんど効いていない。
- 再生しても忘却は残る。T2 は 0.192 → 0.214（+11%）。T1 と T3 は +3〜5%。
- best を D の loss で選ぶため、T1〜T3 の忘却が少ない step 600 の重みは上書きされて残っていない。
- 次に回すなら次のどれか:
  - 1 epoch で止める。
  - 再生の件数を増やす。
  - best を `--best-on all` で選ぶ。
  - 途中の checkpoint を残す。
- 推論スクリプト `tools/shipknow_infer_hf.py` を D に対応させた。`max_new_tokens` は D=448。正解のない行（scenarios.jsonl）は最後のメッセージを外さない。
- 動作確認として架空シナリオ 3 件だけ推論した。3 件とも要求をすべて満たし、validator にも合格した（警告 2 件）。本番の推論はユーザーが実施する。予測は `shipknow_design_v0/smoke/`。
- 成果物:
  - llm_master `~/navspec-qwen35/shipknow/design_v0/out/cont_v1_2/{best,final}`
  - NAS `Share/OTHER/navspec/navspec-qwen35/shipknow/design_v0/`（45 ファイル、sha256 照合済み）
