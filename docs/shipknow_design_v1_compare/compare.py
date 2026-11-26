"""design-v1 checkpoint 比較の採点と集計。引数: ckpt 名（s0500 s0800 ...）。preds/ に llm_master の出力を置いてから実行。"""
import json, subprocess, sys, collections
from pathlib import Path
R = Path("docs/shipknow_design_v1_compare"); P = R / "preds"
D = "data/navspec/shipknow_design_eval_v1"
PY = ".venv/bin/python"
ref = {"v1_2": "docs/shipknow_design_eval_v1/forget/forget_v1_2.json", "design_v0": "docs/shipknow_design_eval_v1/forget/forget_design_best.json"}
out = {}
for m in sys.argv[1:]:
    o = {}
    pv = P / f"evalv1_{m}_s1001"
    subprocess.run(["python3", str(R / "unwrap_v1.py"), str(pv), f"{D}/all.jsonl"], check=True, capture_output=True)
    subprocess.run([PY, "tools/shipknow_design_eval.py", "score", "--problems", f"{D}/problems.jsonl", "--pairs", f"{D}/pairs.jsonl",
                    "--out-dir", str(R / f"score_evalv1_{m}"), "--pred", f"{m}={pv}_unwrapped"], check=True, capture_output=True)
    s = json.load(open(R / f"score_evalv1_{m}" / f"summary_{m}.json"))
    st = json.load(open(f"{pv}_status.json"))
    o["evalv1"] = s
    o["status"] = st
    subprocess.run([PY, "tools/shipknow_design_v1_eval.py", "--pred", str(P / f"dv1test_{m}"), "--gold", "data/navspec/shipknow_design_v1/test.jsonl",
                    "--out-json", str(R / f"dv1_{m}.json"), "--out-md", str(R / f"dv1_{m}.md"), "--no-rows"], check=True, capture_output=True)
    o["dv1"] = json.load(open(R / f"dv1_{m}.json"))
    fp = P / f"forget_{m}"
    if fp.exists():
        subprocess.run([PY, "tools/shipknow_score.py", "--test", "docs/shipknow_design_eval_v1/forget/test/test.jsonl", "--pred", str(fp), "--out", str(R / f"forget_{m}.json")], check=True, capture_output=True)
        o["forget"] = json.load(open(R / f"forget_{m}.json"))
    out[m] = o
json.dump(out, open(R / f"compare_raw_{'_'.join(sys.argv[1:])}.json", "w"), ensure_ascii=False, default=str)
print("ok", list(out))
