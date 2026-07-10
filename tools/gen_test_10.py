"""10件の生成テスト: JSON成功率、数値破綻率、丸暗記の有無を集計する。"""
import json, subprocess, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = [
    ("Japan", "KONGO", 0),
    ("Japan", "KONGO", 1),
    ("United-States-Of-America", "IOWA", 0),
    ("United-Kingdom", "QUEEN ELIZABETH", 0),
    ("Germany", "BISMARCK", 0),
    ("France", "RICHELIEU", 0),
    ("Italy", "VITTORIO", 0),
    ("Russia-Ussr", "SEVASTOPOL", 0),
    ("Japan", "FUBUKI", 0),
    ("United-Kingdom", "TOWN", 0),
]

checkpoint = sys.argv[1] if len(sys.argv) > 1 else "checkpoints/navspec/latest.json"
records_file = sys.argv[2] if len(sys.argv) > 2 else "data/navspec/battleship_real_specs.jsonl"

results = []
for i, (country, class_like, profile) in enumerate(TESTS):
    raw_out = ROOT / f"data/navspec/generated/test_{i:02d}_raw.txt"
    json_out = ROOT / f"data/navspec/generated/test_{i:02d}.json"
    cmd = [
        "uv", "run", "python", "generate_navspec.py",
        "--checkpoint", checkpoint,
        "--records", records_file,
        "--country", country,
        "--class-like", class_like,
        "--profile", str(profile),
        "--max-new-tokens", "1024",
        "--raw-output", str(raw_out),
        "--json-output", str(json_out),
    ]
    env = dict(os.environ, PYTHONPATH=f"{ROOT}:{ROOT}/tools")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    ok = proc.returncode == 0

    record = None
    json_ok = False
    val_errors = 0
    val_warnings = 0
    numeric_fail = False
    memorized = False

    if json_out.exists():
        try:
            record = json.loads(json_out.read_text())
            json_ok = True
        except Exception:
            pass

    if record:
        issues = record.get("validation_issues", [])
        val_errors = sum(1 for x in issues if x.get("severity") == "error")
        val_warnings = sum(1 for x in issues if x.get("severity") == "warn")
        for code in ("displacement_range", "speed_range", "length_breadth_ratio"):
            if any(x.get("code") == code and x.get("severity") == "error" for x in issues):
                numeric_fail = True
        derived = record.get("derived_from", {})
        if derived.get("class_name") and record.get("class_name"):
            from navspec_naming import normalize_name
            if normalize_name(record["class_name"]) == normalize_name(derived["class_name"]):
                memorized = True

    results.append({
        "test": f"{country}/{class_like}/p{profile}",
        "json_ok": json_ok,
        "val_errors": val_errors,
        "val_warnings": val_warnings,
        "numeric_fail": numeric_fail,
        "memorized": memorized,
        "class_name": record.get("class_name") if record else None,
        "hull_kind": record.get("hull_kind") if record else None,
    })
    status = "OK" if ok else f"EXIT={proc.returncode}"
    print(f"[{i+1}/10] {country}/{class_like}/p{profile}: {status} json={json_ok} err={val_errors} warn={val_warnings} numfail={numeric_fail} memo={memorized} -> {record.get('class_name') if record else 'N/A'}")

print("\n=== SUMMARY ===")
n = len(results)
json_rate = sum(1 for r in results if r["json_ok"]) / n * 100
err_rate = sum(1 for r in results if r["val_errors"] > 0) / n * 100
numfail_rate = sum(1 for r in results if r["numeric_fail"]) / n * 100
memo_rate = sum(1 for r in results if r["memorized"]) / n * 100
print(f"JSON成功率:     {json_rate:.0f}% ({sum(1 for r in results if r['json_ok'])}/{n})")
print(f"validation error率: {err_rate:.0f}% ({sum(1 for r in results if r['val_errors']>0)}/{n})")
print(f"数値破綻率:     {numfail_rate:.0f}% ({sum(1 for r in results if r['numeric_fail'])}/{n})")
print(f"丸暗記率:       {memo_rate:.0f}% ({sum(1 for r in results if r['memorized'])}/{n})")
