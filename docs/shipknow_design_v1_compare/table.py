"""compare_raw_*.json と score_evalv1_<m>/rows_<m>.jsonl から比較表（Markdown）を作る。"""
import json, sys, glob, collections
from pathlib import Path
R = Path("docs/shipknow_design_v1_compare")
raw = {}
for f in sorted(glob.glob(str(R / "compare_raw_*.json"))): raw.update(json.load(open(f)))
ms = [m for m in sys.argv[1:] if m in raw]
v0 = {"forget": json.load(open("docs/shipknow_design_eval_v1/forget/forget_design_best.json"))}
cat = {json.loads(l)["id"]: json.loads(l).get("category") for l in open("data/navspec/shipknow_design_eval_v1/all.jsonl")}
def pct(x): return "-" if x is None else f"{100*x:.1f}"
def rate(k, n): return None if n == 0 else k / n
rows = collections.OrderedDict()
def put(name, m, v): rows.setdefault(name, {})[m] = v
for m in ms:
    o = raw[m]; st = o["status"]
    # eval_v1: 設計を出した行（status=feasible かつ design あり）だけ
    stat = {}
    for l in open(R / "preds" / f"evalv1_{m}_s1001"):
        r = json.loads(l)
        try: s = json.loads(r["text"]).get("status")
        except Exception: s = "invalid"
        stat[r["id"]] = s
    rr = [json.loads(l) for l in open(R / f"score_evalv1_{m}" / f"rows_{m}.jsonl")]
    by = collections.defaultdict(lambda: collections.Counter())
    for r in rr:
        c = r["category"]; f = stat.get(r["id"]) == "feasible" and r.get("design_present")
        by[c]["n"] += 1
        if f:
            by[c]["feas"] += 1; by[c]["all"] += bool(r.get("req_all_sat")); by[c]["cons"] += bool(r.get("cons_pass")); by[c]["plaus"] += bool(r.get("plaus") and r["plaus"].get("all_in") if isinstance(r.get("plaus"), dict) else r.get("plaus"))
    AD = ["A", "B", "C", "D"]
    n_ad = sum(by[c]["n"] for c in AD); feas_ad = sum(by[c]["feas"] for c in AD)
    put("eval_v1 A〜D 設計を返した率（低いと過剰拒否）", m, rate(feas_ad, n_ad))
    put("eval_v1 A〜D 全要求充足（設計を返した行）", m, rate(sum(by[c]["all"] for c in AD), feas_ad))
    put("eval_v1 A〜D 整合性合格（設計を返した行）", m, rate(sum(by[c]["cons"] for c in AD), feas_ad))
    for c in AD: put(f"  {c} 全要求充足（設計を返した行）", m, rate(by[c]["all"], by[c]["feas"]))
    e = st.get("E", {}); ne = sum(e.values())
    put("eval_v1 E 矛盾を認識（relaxed/infeasible）", m, rate(e.get("relaxed", 0) + e.get("infeasible", 0), ne))
    pairs = o["evalv1"]["pairs"]; put("eval_v1 ペア正答率", m, pairs["accuracy"])
    dv = o["dv1"]; ov = dv["overall"]; bc = dv["by_category"]
    g = lambda d, k: (d.get(k) or {}).get("rate")
    put("design-v1 test status 一致", m, g(ov, "status_match"))
    put("design-v1 test 総合 row_ok", m, g(ov, "row_ok"))
    put("design-v1 test false_feasible（低いほど良い）", m, g(ov, "false_feasible"))
    for c in ("single", "modify", "repair", "infeasible", "ood", "context"): put(f"  {c} row_ok", m, g(bc[c], "row_ok"))
    put("  infeasible status 一致", m, g(bc["infeasible"], "status_match"))
    put("  repair 修正版 validator 合格", m, g(bc["repair"], "repair_valid"))
    put("  context 総合", m, g(bc["context"], "context_final_ok"))
    put("  modify 連動項目に追従", m, g(bc["modify"], "modify_linked_follow"))
    put("  ood 前提外の技術なし", m, g(bc["ood"], "ood_no_unpremised_anachronism"))
    if "forget" in o:
        f = o["forget"]
        put("T1 acc", m, f["T1"]["metrics"]["coarse_acc"]); put("T1 ECE（低いほど良い）", m, f["T1"]["metrics"].get("ranking", {}).get("ece"))
        put("T2 武装 F1", m, f["T2"]["metrics"]["weapon_item"]["f1"]); put("T3 MAPE（低いほど良い）", m, f["T3"]["metrics"]["overall"]["mape"])
fv = v0["forget"]
ref = {"T1 acc": fv["T1"]["metrics"]["coarse_acc"], "T1 ECE（低いほど良い）": fv["T1"]["metrics"]["ranking"]["ece"], "T2 武装 F1": fv["T2"]["metrics"]["weapon_item"]["f1"], "T3 MAPE（低いほど良い）": fv["T3"]["metrics"]["overall"]["mape"],
       "eval_v1 E 矛盾を認識（relaxed/infeasible）": 0.0, "eval_v1 ペア正答率": 0.8867}
print("| 指標 | " + " | ".join(ms) + " | 参考: design-v0 |"); print("|---|" + "---:|" * (len(ms) + 1))
for name, d in rows.items(): print(f"| {name} | " + " | ".join(pct(d.get(m)) for m in ms) + f" | {pct(ref.get(name))} |")
