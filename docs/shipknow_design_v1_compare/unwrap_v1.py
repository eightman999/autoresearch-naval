"""design-v1 の出力 {status, conflicts, relaxations, design} を eval_v1 採点器用に変換し、status をカテゴリ別に集計する。
出力: <pred>_unwrapped（{"id","seed","output": design または {}}）と <pred>_status.json。"""
import json, sys, collections
pred_path, problems_path = sys.argv[1], sys.argv[2]
cat = {}
for l in open(problems_path):
    r = json.loads(l); cat[r["id"]] = r.get("category") or ("PAIR" if r["id"].startswith("pair_") else "?")
rows = [json.loads(l) for l in open(pred_path)]
out = []; st = collections.defaultdict(collections.Counter)
for r in rows:
    t = r.get("text", ""); c = cat.get(r["id"], "?")
    try:
        o = json.loads(t)
    except Exception:
        s = t.find("{"); o = None
        if s >= 0:
            try: o = json.JSONDecoder().raw_decode(t[s:])[0]
            except Exception: o = None
    if not isinstance(o, dict):
        st[c]["json_invalid"] += 1; out.append({"id": r["id"], "seed": r.get("seed"), "text": t}); continue
    status = o.get("status", "missing"); st[c][status] += 1
    d = o.get("design") if "design" in o else o
    out.append({"id": r["id"], "seed": r.get("seed"), "output": d if isinstance(d, dict) else {}})
open(pred_path + "_unwrapped", "w").write("\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n")
json.dump({k: dict(v) for k, v in st.items()}, open(pred_path + "_status.json", "w"), ensure_ascii=False, indent=1)
print({k: dict(v) for k, v in sorted(st.items())})
