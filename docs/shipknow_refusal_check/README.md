# design-v1 拒否照合フィルタ（2026-10-15）

`tools/shipknow_refusal_check.py`。design-v1 の拒否（status = relaxed / infeasible）を、train 実艦で照合する。照合で根拠のない拒否を見つけたら、validator 形式で再要求する。
oracle は design-v1 の学習データで「不可能」を決めた基準と同じで、train の gold 候補 5,767 隻から探す（`Data.idx_train` と id 列で一致を確認済み）。
条件は、同じ coarse 艦種で、要求年 + 3 年以内に設計され、要求の全制約を満たす実艦があるかどうか。

## 判定

| verdict | 意味 | 既定の扱い |
|---|---|---|
| unsupported | 全制約を満たす実艦（witness）がある | 再要求する |
| false_claims | witness は無いが、conflicts の主張が全て反証できる（要求に無い制約、または「艦種・年代・排水量 + その制約」なら実艦が満たせる） | 受け入れる（`--reject-false-claims` で再要求） |
| supported | 上のどちらでもない拒否 | 受け入れる |

## eval_v1（seed 1001）での照合結果

拒否のうち、各 verdict に入った件数。

| ckpt | A〜D unsupported | A〜D false_claims | E unsupported | E false_claims | P unsupported |
|---|---:|---:|---:|---:|---:|
| s0500 | 3 | 2 | 0 | 0 | 5 |
| s0800 | 17 | 7 | 0 | 6 | 5 |
| **s1200** | **15**（B 8 / C 3 / D 4） | 17（A 1 / C 10 / D 6） | **0** | 5 | 4 |
| s2000 | 19 | 13 | 0 | 6 | 1 |

- **unsupported は E の正しい拒否を 1 件も捨てない。** 全 ckpt で 0 件だった。そのため既定にした。
- s1200 の B の拒否 8 件は全て unsupported になった（B は witness から作った問題なので、期待どおり）。
- C / D の拒否の多くは supported になる。C は「train の実艦で全充足するのが 0〜4 隻」になるよう作った問題で、D は架空設定の問題なので、witness が見つからないのは自然。
- false_claims を足すと C / D の拒否をさらに 17 件捨てられる。ただし E の正しい拒否も 5 件（11%）巻き込む。この場合の再要求文は「全部満たせる」とは言わず、主張の誤りだけを指摘する。
- witness の無い拒否（supported + false_claims）の主張 118 件のうち、要求にある制約への主張は 107 件だった。そのうち 97 件は「その制約だけ外しても witness が出ない」もので、制約を 1 つ緩めるだけでは解決しない。拒否する結論は正しくても、挙げている原因が足りないかずれていることが多い。

再要求が全て正しい設計になった場合、s1200 の「A〜D 正しく設計」の上限は次のとおり。
- unsupported だけ: 67.5 → 75.0（+15 / 200）
- false_claims も含める: 83.5
実際の値は、llm_master で再要求を生成してから測る（下記）。

## 再要求の生成と再採点（未実施）

```bash
# 1) 照合（ローカル）: verdicts.jsonl / rerequest.jsonl / summary.json
.venv/bin/python tools/shipknow_refusal_check.py check --problems data/navspec/shipknow_design_eval_v1/all_v1sys.jsonl \
    --pred docs/shipknow_design_v1_compare/preds/evalv1_s1200_s1001 --out-dir docs/shipknow_refusal_check/s1200
# 2) 再要求の生成（llm_master, RTX 3060）
python shipknow/tools/shipknow_infer_hf.py --multi-turn-eval --test rerequest.jsonl \
    --adapter shipknow/design_v1/out/selected/design_v1_step1200 --out rerequest_preds \
    --sample-seed 1001 --temperature 0.7 --batch-size 16 --max-new-tokens 448
# 3) 差し戻し → unwrap → eval_v1 採点（ローカル）
.venv/bin/python tools/shipknow_refusal_check.py merge --pred docs/shipknow_design_v1_compare/preds/evalv1_s1200_s1001 \
    --verdicts docs/shipknow_refusal_check/s1200/verdicts.jsonl --rerequest docs/shipknow_refusal_check/s1200/rerequest.jsonl \
    --rerequest-pred docs/shipknow_refusal_check/s1200/rerequest_preds --out docs/shipknow_refusal_check/s1200/merged
python3 docs/shipknow_design_v1_compare/unwrap_v1.py docs/shipknow_refusal_check/s1200/merged data/navspec/shipknow_design_eval_v1/all.jsonl
.venv/bin/python tools/shipknow_design_eval.py score --problems data/navspec/shipknow_design_eval_v1/problems.jsonl \
    --pairs data/navspec/shipknow_design_eval_v1/pairs.jsonl --out-dir docs/shipknow_refusal_check/s1200/score \
    --pred s1200_filtered=docs/shipknow_refusal_check/s1200/merged_unwrapped
```
