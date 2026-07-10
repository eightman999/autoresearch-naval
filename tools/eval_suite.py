"""eval_suite.py — 総合テストスイート: generate_navspec.py × 22ケースを実行し、
era境界・制約・矛盾・plausibilityを自動評価する。

Usage:
    uv run python -m tools.eval_suite
    uv run python -m tools.eval_suite --checkpoint checkpoints/navspec/latest.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from navspec_contradiction import check_contradiction
from navspec_equipment import era_profile_for
from navspec_nation_tier import get_nation_tier, get_tier_limits, is_capital_ship
from navspec_plausibility import evaluate_record
from navspec_shiptypes import SHIP_TYPE_SPEED_CLAMP, get_ship_type_specs, hull_kind_for_ship_type, lb_ratio_band_for
from validate_navspec_output import extract_json_object, validate_schema

DEFAULT_CHECKPOINT = ROOT / "checkpoints/navspec/latest.json"
SCHEMA_PATH = ROOT / "schemas/ship_spec_record.schema.json"
TEST_CASES_PATH = ROOT / "tools/eval_test_cases.json"
OUT_DIR = ROOT / "data/navspec/generated/eval"


def run_generation(test: dict[str, Any], checkpoint: Path, profile: int, max_new_tokens: int) -> dict[str, Any]:
    test_id = test["test_id"]
    raw_out = OUT_DIR / f"{test_id}_raw.txt"
    json_out = OUT_DIR / f"{test_id}.json"
    cmd = [
        sys.executable, str(ROOT / "generate_navspec.py"),
        "--checkpoint", str(checkpoint),
        "--country", test["nation"],
        "--year", str(test["year"]),
        "--ship-type", test["ship_type"],
        "--profile", str(profile),
        "--prompt", test["prompt"],
        "--max-new-tokens", str(max_new_tokens),
        "--temperature", "0.3",
        "--raw-output", str(raw_out),
        "--json-output", str(json_out),
    ]
    constraints = test.get("constraints")
    if constraints:
        cmd.extend(["--constraints-json", json.dumps(constraints)])
    env = dict(os.environ, PYTHONPATH=f"{ROOT}:{ROOT}/tools")
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=180)
    elapsed = time.time() - t0
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "elapsed_s": round(elapsed, 1),
        "raw_out": raw_out,
        "json_out": json_out,
    }


def check_raw_model_output(raw_path: Path) -> dict[str, Any]:
    raw_text = ""
    if raw_path.exists():
        raw_text = raw_path.read_text(encoding="utf-8")
    raw_json_ok = False
    raw_fields: dict[str, Any] = {}
    if raw_text.strip():
        try:
            obj = extract_json_object(raw_text)
            raw_json_ok = True
            for k in ("displacement", "dimensions", "propulsion", "armor", "armament"):
                if isinstance(obj.get(k), dict):
                    raw_fields[k] = obj[k]
        except (ValueError, Exception):
            pass
    return {
        "raw_text_len": len(raw_text),
        "raw_json_valid": raw_json_ok,
        "raw_fields_present": list(raw_fields.keys()),
        "raw_text_preview": raw_text[:200] if raw_text else "",
    }


def check_era_compliance(record: dict[str, Any], test: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    year = test["year"]
    eq = record.get("equipment", {})
    fc = eq.get("fire_control", {}) if isinstance(eq, dict) else {}
    has_radar = bool(fc.get("radar"))
    if has_radar and year < 1938:
        issues.append({"check": "radar_before_1938", "status": "FAIL", "detail": f"レーダーが{year}年に存在（1938年以前はNG）"})
    aviation = eq.get("aviation", {}) if isinstance(eq, dict) else {}
    has_catapult = bool(aviation.get("catapult"))
    if has_catapult and year < 1925:
        issues.append({"check": "catapult_before_1925", "status": "FAIL", "detail": f"カタパルトが{year}年に存在（1925年以前はNG）"})

    expected_era = era_profile_for(year, None)
    actual_era = record.get("era_profile", {}).get("era", "unknown")
    if actual_era != expected_era["era"]:
        issues.append({"check": "era_mismatch", "status": "WARN", "detail": f"era={actual_era} vs expected={expected_era['era']}"})

    main_cal = record.get("armament", {}).get("main_caliber_mm")
    hull = record.get("hull_kind", "")
    if year < 1906 and hull in ("battleship", "battlecruiser") and main_cal and main_cal > 340:
        issues.append({"check": "guns_too_big_pre_dreadnought", "status": "FAIL", "detail": f"前弩級時代({year})に{main_cal}mm砲は過大"})
    if year < 1906 and main_cal and main_cal > 356 and hull not in ("battleship", "battlecruiser"):
        pass
    if year <= 1885 and main_cal and main_cal > 305:
        issues.append({"check": "guns_too_big_1880s", "status": "FAIL", "detail": f"1880年代({year})に{main_cal}mm砲は過大"})
    if year >= 1944:
        aa = eq.get("anti_air", {}) if isinstance(eq, dict) else {}
        aa_cal = aa.get("caliber_mm")
        if aa_cal and aa_cal < 20:
            issues.append({"check": "aa_too_thin_late_war", "status": "WARN", "detail": f"1944年以降の対空兵装が薄い({aa_cal}mm)"})

    if not issues:
        issues.append({"check": "era_clean", "status": "PASS", "detail": "era境界違反なし"})
    return issues


def check_constraints(record: dict[str, Any], test: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    c = test.get("constraints", {})
    if not c:
        issues.append({"check": "no_constraints", "status": "N/A", "detail": "制約なし"})
        return issues

    disp = record.get("displacement", {}).get("normal_t") or record.get("displacement", {}).get("full_t")
    speed = record.get("propulsion", {}).get("speed_kn")
    main_cal_mm = record.get("armament", {}).get("main_caliber_mm")
    belt = record.get("armor", {}).get("belt_mm")
    torpedoes = record.get("equipment", {}).get("torpedoes", {})
    has_torps = bool(torpedoes.get("carried")) if isinstance(torpedoes, dict) else False

    if "standard_displacement_t_max" in c and disp is not None:
        lim = c["standard_displacement_t_max"]
        ok = disp <= lim
        issues.append({"check": "disp_max", "status": "PASS" if ok else "FAIL",
                        "detail": f"排水量 {disp}t vs 上限 {lim}t"})
    if "standard_displacement_t_max" in c and disp is None:
        issues.append({"check": "disp_max", "status": "WARN", "detail": "排水量が生成されず検証不可"})

    if "speed_kt_min" in c and speed is not None:
        lim = c["speed_kt_min"]
        ok = speed >= lim
        issues.append({"check": "speed_min", "status": "PASS" if ok else "FAIL",
                        "detail": f"速力 {speed}kt vs 下限 {lim}kt"})
    if "speed_kt_max" in c and speed is not None:
        lim = c["speed_kt_max"]
        ok = speed <= lim
        issues.append({"check": "speed_max", "status": "PASS" if ok else "FAIL",
                        "detail": f"速力 {speed}kt vs 上限 {lim}kt"})

    if "main_gun_caliber_cm_max" in c and main_cal_mm is not None:
        lim_mm = c["main_gun_caliber_cm_max"] * 10
        ok = main_cal_mm <= lim_mm + 0.5
        issues.append({"check": "gun_max", "status": "PASS" if ok else "FAIL",
                        "detail": f"主砲 {main_cal_mm}mm vs 上限 {lim_mm}mm"})
    if "main_gun_caliber_cm_min" in c and main_cal_mm is not None:
        lim_mm = c["main_gun_caliber_cm_min"] * 10
        ok = main_cal_mm >= lim_mm - 0.5
        issues.append({"check": "gun_min", "status": "PASS" if ok else "FAIL",
                        "detail": f"主砲 {main_cal_mm}mm vs 下限 {lim_mm}mm"})

    if "armor_belt_mm_max" in c and belt is not None:
        lim = c["armor_belt_mm_max"]
        ok = belt <= lim
        issues.append({"check": "belt_max", "status": "PASS" if ok else "FAIL",
                        "detail": f"装甲帯 {belt}mm vs 上限 {lim}mm"})
    if "armor_belt_mm_min" in c and belt is not None:
        lim = c["armor_belt_mm_min"]
        ok = belt >= lim
        issues.append({"check": "belt_min", "status": "PASS" if ok else "FAIL",
                        "detail": f"装甲帯 {belt}mm vs 下限 {lim}mm"})

    if c.get("torpedo_required") and not has_torps:
        issues.append({"check": "torpedo_required", "status": "FAIL", "detail": "魚雷必須だが搭載なし"})
    elif c.get("torpedo_required") and has_torps:
        issues.append({"check": "torpedo_required", "status": "PASS", "detail": "魚雷搭載あり"})

    return issues


def expected_impossible(test: dict[str, Any]) -> bool:
    if test.get("expect_status") == "impossible":
        return True
    if str(test.get("test_id", "")).startswith("contradiction_"):
        return True
    predicted = check_contradiction(
        test["ship_type"],
        test.get("year"),
        test.get("nation"),
        constraints=test.get("constraints"),
        raw_prompt=test.get("prompt"),
    )
    return predicted.get("status") == "impossible" and bool(test.get("expect_detector_impossible"))


def check_lb_ratio(record: dict[str, Any], test: dict[str, Any]) -> list[dict[str, str]]:
    if record.get("status") == "impossible":
        return [{"check": "lb_ratio", "status": "N/A", "detail": "impossible応答のため寸法なし"}]
    dims = record.get("dimensions", {}) if isinstance(record, dict) else {}
    length = dims.get("length_m")
    breadth = dims.get("breadth_m")
    if not isinstance(length, (int, float)) or not isinstance(breadth, (int, float)) or breadth <= 0:
        return [{"check": "lb_ratio", "status": "WARN", "detail": "寸法不足でL/B検証不可"}]
    ratio = length / breadth
    lo, hi = lb_ratio_band_for(test.get("ship_type") or record.get("type"), record.get("hull_kind") or test.get("hull_kind"))
    ok = lo <= ratio <= hi
    return [{
        "check": "lb_ratio",
        "status": "PASS" if ok else "FAIL",
        "detail": f"L/B={ratio:.2f} expected {lo:.1f}-{hi:.1f}",
    }]


def check_contradiction_detection(record: dict[str, Any], test: dict[str, Any]) -> list[dict[str, str]]:
    expected = expected_impossible(test)
    actual = record.get("status") == "impossible"
    if expected:
        reasons = record.get("reason") or record.get("reasons") or []
        suggestion = record.get("suggested_revision")
        if actual and reasons and isinstance(suggestion, dict) and suggestion:
            return [{"check": "impossible_detected", "status": "PASS", "detail": f"reason={len(reasons)} suggested_revisionあり"}]
        return [{"check": "impossible_detected", "status": "FAIL", "detail": "impossible期待だが reason/suggested_revision 付きで返らない"}]
    if actual:
        return [{"check": "unexpected_impossible", "status": "FAIL", "detail": "通常生成ケースがimpossibleになった"}]
    return [{"check": "normal_generation", "status": "PASS", "detail": "通常生成として処理"}]


def check_nation_tier(record: dict[str, Any], test: dict[str, Any]) -> list[dict[str, str]]:
    nation = test.get("nation")
    ship_type = test.get("ship_type")
    year = test.get("year")
    limits = get_tier_limits(nation)
    tier = get_nation_tier(nation)
    checks: list[dict[str, str]] = []

    if record.get("status") == "impossible":
        status = "PASS" if expected_impossible(test) else "FAIL"
        checks.append({"check": "tier_impossible_status", "status": status, "detail": f"tier-{tier} impossible応答"})
        return checks

    disp = record.get("displacement", {}).get("normal_t") or record.get("displacement", {}).get("full_t")
    max_disp = limits["max_displacement_t"]
    if max_disp is not None and isinstance(disp, (int, float)):
        ok = disp <= max_disp + 0.5
        checks.append({"check": "tier_displacement", "status": "PASS" if ok else "FAIL",
                       "detail": f"tier-{tier} disp={disp}t max={max_disp}t"})
    elif max_disp is None:
        checks.append({"check": "tier_displacement", "status": "N/A", "detail": f"tier-{tier} 排水量上限なし"})

    gun = record.get("armament", {}).get("main_caliber_mm")
    max_gun = limits["max_main_caliber_mm"]
    if max_gun is not None and isinstance(gun, (int, float)):
        ok = gun <= max_gun + 0.5
        checks.append({"check": "tier_main_gun", "status": "PASS" if ok else "FAIL",
                       "detail": f"tier-{tier} gun={gun}mm max={max_gun}mm"})
    elif max_gun is None:
        checks.append({"check": "tier_main_gun", "status": "N/A", "detail": f"tier-{tier} 主砲上限なし"})

    speed = record.get("propulsion", {}).get("speed_kn")
    st_speed_hi = SHIP_TYPE_SPEED_CLAMP.get(ship_type, (5, 45))[1]
    tier_speed_hi = st_speed_hi * limits["max_speed_multiplier"]
    if isinstance(speed, (int, float)):
        ok = speed <= tier_speed_hi + 0.6
        checks.append({"check": "tier_engine_speed", "status": "PASS" if ok else "FAIL",
                       "detail": f"tier-{tier} speed={speed}kt max≈{tier_speed_hi:.1f}kt"})

    fc = ((record.get("equipment") or {}).get("fire_control") or {})
    has_radar = bool(fc.get("radar")) if isinstance(fc, dict) else False
    radar_from = limits["radar_from_year"]
    if year is not None and year < radar_from:
        checks.append({"check": "tier_radar_year", "status": "PASS" if not has_radar else "FAIL",
                       "detail": f"tier-{tier} radar_from={radar_from}, year={year}, radar={has_radar}"})
    else:
        checks.append({"check": "tier_radar_year", "status": "N/A", "detail": f"tier-{tier} radar利用可能時期"})

    if is_capital_ship(ship_type) and not limits["battleship_allowed"]:
        checks.append({"check": "tier_capital_ship", "status": "FAIL",
                       "detail": f"tier-{tier}は自主主力艦不可だが通常生成された"})
    else:
        checks.append({"check": "tier_capital_ship", "status": "PASS" if is_capital_ship(ship_type) else "N/A",
                       "detail": f"tier-{tier} 大型艦可否反映"})

    return checks


def extract_key_values(record: dict[str, Any]) -> dict[str, Any]:
    length = (record.get("dimensions") or {}).get("length_m")
    breadth = (record.get("dimensions") or {}).get("breadth_m")
    lb_ratio = round(length / breadth, 3) if isinstance(length, (int, float)) and isinstance(breadth, (int, float)) and breadth > 0 else None
    return {
        "status": record.get("status", "generated"),
        "class_name": record.get("class_name"),
        "country": record.get("country"),
        "hull_kind": record.get("hull_kind"),
        "year": record.get("year"),
        "displacement_normal_t": (record.get("displacement") or {}).get("normal_t"),
        "displacement_full_t": (record.get("displacement") or {}).get("full_t"),
        "length_m": (record.get("dimensions") or {}).get("length_m"),
        "breadth_m": (record.get("dimensions") or {}).get("breadth_m"),
        "lb_ratio": lb_ratio,
        "speed_kn": (record.get("propulsion") or {}).get("speed_kn"),
        "power_shp": (record.get("propulsion") or {}).get("power_shp"),
        "belt_mm": (record.get("armor") or {}).get("belt_mm"),
        "main_caliber_mm": (record.get("armament") or {}).get("main_caliber_mm"),
        "main_barrels": (record.get("armament") or {}).get("main_barrels"),
        "era": (record.get("era_profile") or {}).get("era"),
        "fire_control_radar": ((record.get("equipment") or {}).get("fire_control") or {}).get("radar"),
        "has_torpedoes": ((record.get("equipment") or {}).get("torpedoes") or {}).get("carried"),
        "has_catapult": ((record.get("equipment") or {}).get("aviation") or {}).get("catapult"),
        "nation_tier": (record.get("nation_capability_tier") or {}).get("tier"),
    }


def evaluate_test(test: dict[str, Any], gen_result: dict[str, Any]) -> dict[str, Any]:
    test_id = test["test_id"]
    result: dict[str, Any] = {
        "test_id": test_id,
        "category": test_id.rsplit("_", 1)[0],
        "nation": test["nation"],
        "year": test["year"],
        "ship_type": test.get("ship_type"),
        "hull_kind": test["hull_kind"],
        "gen_returncode": gen_result["returncode"],
        "gen_elapsed_s": gen_result["elapsed_s"],
    }

    raw_info = check_raw_model_output(gen_result["raw_out"])
    result["raw_json_valid"] = raw_info["raw_json_valid"]
    result["raw_text_len"] = raw_info["raw_text_len"]
    result["raw_fields_present"] = raw_info["raw_fields_present"]

    record = None
    json_path = gen_result["json_out"]
    if json_path.exists():
        try:
            record = json.loads(json_path.read_text(encoding="utf-8"))
            result["patched_json_valid"] = True
        except Exception:
            result["patched_json_valid"] = False
    else:
        result["patched_json_valid"] = False

    if record:
        result["values"] = extract_key_values(record)
        result["output_status"] = record.get("status", "generated")
        result["contradiction_checks"] = check_contradiction_detection(record, test)
        result["lb_ratio_checks"] = check_lb_ratio(record, test)
        result["nation_tier_checks"] = check_nation_tier(record, test)

        if record.get("status") == "impossible":
            result["schema_errors"] = [] if expected_impossible(test) else ["unexpected impossible response"]
            result["plausibility_score"] = None
            result["plausibility_verdict"] = "impossible"
            result["plausibility_findings"] = []
            result["era_checks"] = [{"check": "impossible", "status": "N/A", "detail": "impossible応答"}]
            result["constraint_checks"] = [{"check": "impossible", "status": "N/A", "detail": "impossible応答"}]
        else:
            schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
            schema_errors = validate_schema(record, schema)
            result["schema_errors"] = schema_errors

            plaus = evaluate_record(record)
            result["plausibility_score"] = plaus["score"]
            result["plausibility_verdict"] = plaus["verdict"]
            result["plausibility_findings"] = [
                {"sev": f["severity"], "code": f["code"], "msg": f["message"]}
                for f in plaus["findings"] if f["severity"] in ("error", "warn")
            ]

            result["era_checks"] = check_era_compliance(record, test)
            result["constraint_checks"] = check_constraints(record, test)
    else:
        result["values"] = None
        result["output_status"] = "no_output"
        result["schema_errors"] = ["no patched JSON"]
        result["plausibility_score"] = None
        result["plausibility_verdict"] = "no_output"
        result["era_checks"] = [{"check": "no_output", "status": "FAIL", "detail": "JSON出力なし"}]
        result["constraint_checks"] = [{"check": "no_output", "status": "FAIL", "detail": "JSON出力なし"}]
        result["lb_ratio_checks"] = [{"check": "no_output", "status": "FAIL", "detail": "JSON出力なし"}]
        result["contradiction_checks"] = [{"check": "no_output", "status": "FAIL", "detail": "JSON出力なし"}]
        result["nation_tier_checks"] = [{"check": "no_output", "status": "FAIL", "detail": "JSON出力なし"}]

    return result


def count_status(checks: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {"PASS": 0, "FAIL": 0, "WARN": 0, "N/A": 0}
    for c in checks:
        s = c.get("status", "N/A")
        counts[s] = counts.get(s, 0) + 1
    return counts


def print_report(results: list[dict[str, Any]]) -> None:
    print("=" * 80)
    print("総合評価レポート — navspec generation eval suite")
    print("=" * 80)

    for r in results:
        tid = r["test_id"]
        cat = r["category"]
        print(f"\n{'─' * 70}")
        print(f"[{tid}] ({cat})  {r['nation']} / {r['year']} / {r.get('ship_type','')} / hull={r['hull_kind']}")
        print(f"  生成: rc={r['gen_returncode']} elapsed={r['gen_elapsed_s']}s")
        print(f"  応答status: {r.get('output_status')}")
        print(f"  モデルraw出力: JSON有効={r['raw_json_valid']} 長={r['raw_text_len']}ch フィールド={r['raw_fields_present']}")
        print(f"  パッチ後JSON: 有効={r['patched_json_valid']} schema_errors={len(r.get('schema_errors',[]))}")
        if r["values"]:
            v = r["values"]
            print(f"  主要値: disp={v.get('displacement_normal_t')}t spd={v.get('speed_kn')}kt "
                  f"gun={v.get('main_caliber_mm')}mm barrels={v.get('main_barrels')} "
                  f"belt={v.get('belt_mm')}mm era={v.get('era')}")
            print(f"         len={v.get('length_m')}m beam={v.get('breadth_m')}m "
                  f"L/B={v.get('lb_ratio')} radar={v.get('fire_control_radar')} "
                  f"torp={v.get('has_torpedoes')} cat={v.get('has_catapult')} tier={v.get('nation_tier')}")

        score = r.get("plausibility_score")
        verdict = r.get("plausibility_verdict")
        print(f"  Plausibility: score={score} verdict={verdict}")
        for f in r.get("plausibility_findings", [])[:5]:
            print(f"    {f['sev'].upper():4} {f['code']}: {f['msg']}")

        era_counts = count_status(r.get("era_checks", []))
        con_counts = count_status(r.get("constraint_checks", []))
        print(f"  Era検査: PASS={era_counts['PASS']} FAIL={era_counts['FAIL']} WARN={era_counts['WARN']}")
        for c in r.get("era_checks", []):
            if c["status"] in ("FAIL", "WARN"):
                print(f"    {c['status']:4} {c['check']}: {c['detail']}")
        print(f"  制約検査: PASS={con_counts['PASS']} FAIL={con_counts['FAIL']} WARN={con_counts['WARN']}")
        for c in r.get("constraint_checks", []):
            if c["status"] in ("FAIL", "WARN"):
                print(f"    {c['status']:4} {c['check']}: {c['detail']}")
        lb_counts = count_status(r.get("lb_ratio_checks", []))
        contradiction_counts = count_status(r.get("contradiction_checks", []))
        tier_counts = count_status(r.get("nation_tier_checks", []))
        print(f"  L/B検査: PASS={lb_counts['PASS']} FAIL={lb_counts['FAIL']} WARN={lb_counts['WARN']}")
        for c in r.get("lb_ratio_checks", []):
            if c["status"] in ("FAIL", "WARN"):
                print(f"    {c['status']:4} {c['check']}: {c['detail']}")
        print(f"  矛盾検査: PASS={contradiction_counts['PASS']} FAIL={contradiction_counts['FAIL']}")
        for c in r.get("contradiction_checks", []):
            if c["status"] in ("FAIL", "WARN"):
                print(f"    {c['status']:4} {c['check']}: {c['detail']}")
        print(f"  Tier検査: PASS={tier_counts['PASS']} FAIL={tier_counts['FAIL']} WARN={tier_counts['WARN']}")
        for c in r.get("nation_tier_checks", []):
            if c["status"] in ("FAIL", "WARN"):
                print(f"    {c['status']:4} {c['check']}: {c['detail']}")

    print(f"\n{'=' * 80}")
    print("サマリー")
    print(f"{'=' * 80}")
    n = len(results)
    cats = {}
    for r in results:
        cat = r["category"]
        if cat not in cats:
            cats[cat] = {"total": 0, "raw_json_ok": 0, "patched_ok": 0, "schema_ok": 0,
                         "plaus_pass": 0, "era_fail": 0, "constraint_fail": 0,
                         "lb_fail": 0, "contradiction_fail": 0, "tier_fail": 0,
                         "lb_pass": 0, "contradiction_pass": 0, "tier_pass": 0,
                         "scores": []}
        c = cats[cat]
        c["total"] += 1
        if r["raw_json_valid"]:
            c["raw_json_ok"] += 1
        if r["patched_json_valid"]:
            c["patched_ok"] += 1
        if not r.get("schema_errors"):
            c["schema_ok"] += 1
        v = r.get("plausibility_verdict", "")
        if v == "accepted":
            c["plaus_pass"] += 1
        if r.get("plausibility_score") is not None:
            c["scores"].append(r["plausibility_score"])
        era = count_status(r.get("era_checks", []))
        c["era_fail"] += era.get("FAIL", 0)
        con = count_status(r.get("constraint_checks", []))
        c["constraint_fail"] += con.get("FAIL", 0)
        lb = count_status(r.get("lb_ratio_checks", []))
        c["lb_fail"] += lb.get("FAIL", 0)
        c["lb_pass"] += lb.get("PASS", 0)
        contradiction = count_status(r.get("contradiction_checks", []))
        c["contradiction_fail"] += contradiction.get("FAIL", 0)
        c["contradiction_pass"] += contradiction.get("PASS", 0)
        tier = count_status(r.get("nation_tier_checks", []))
        c["tier_fail"] += tier.get("FAIL", 0)
        c["tier_pass"] += tier.get("PASS", 0)

    for cat, c in sorted(cats.items()):
        avg = sum(c["scores"]) / len(c["scores"]) if c["scores"] else 0
        print(f"\n  [{cat}] {c['total']}件")
        print(f"    モデルraw JSON成功率: {c['raw_json_ok']}/{c['total']}")
        print(f"    パッチ後JSON成功率:  {c['patched_ok']}/{c['total']}")
        print(f"    schemaエラー0件率:   {c['schema_ok']}/{c['total']}")
        print(f"    plausibility accepted: {c['plaus_pass']}/{c['total']}  avg_score={avg:.1f}")
        print(f"    era境界FAIL数:       {c['era_fail']}")
        print(f"    制約FAIL数:          {c['constraint_fail']}")
        print(f"    L/B FAIL数:          {c['lb_fail']} (PASS={c['lb_pass']})")
        print(f"    矛盾検出FAIL数:      {c['contradiction_fail']} (PASS={c['contradiction_pass']})")
        print(f"    tier反映FAIL数:      {c['tier_fail']} (PASS={c['tier_pass']})")

    total_raw = sum(c["raw_json_ok"] for c in cats.values())
    total_patch = sum(c["patched_ok"] for c in cats.values())
    total_schema = sum(c["schema_ok"] for c in cats.values())
    total_era_fail = sum(c["era_fail"] for c in cats.values())
    total_con_fail = sum(c["constraint_fail"] for c in cats.values())
    total_lb_fail = sum(c["lb_fail"] for c in cats.values())
    total_lb_pass = sum(c["lb_pass"] for c in cats.values())
    total_contradiction_fail = sum(c["contradiction_fail"] for c in cats.values())
    total_contradiction_pass = sum(c["contradiction_pass"] for c in cats.values())
    total_tier_fail = sum(c["tier_fail"] for c in cats.values())
    total_tier_pass = sum(c["tier_pass"] for c in cats.values())
    all_scores = [r["plausibility_score"] for r in results if r.get("plausibility_score") is not None]
    print(f"\n  [全体] {n}件")
    print(f"    モデルraw JSON成功率: {total_raw}/{n} ({total_raw/n*100:.0f}%)")
    print(f"    パッチ後JSON成功率:  {total_patch}/{n} ({total_patch/n*100:.0f}%)")
    print(f"    schemaエラー0件率:   {total_schema}/{n} ({total_schema/n*100:.0f}%)")
    if all_scores:
        print(f"    plausibility score:  avg={sum(all_scores)/len(all_scores):.1f} min={min(all_scores):.1f} max={max(all_scores):.1f}")
    print(f"    era境界FAIL総数:     {total_era_fail}")
    print(f"    制約FAIL総数:        {total_con_fail}")
    print(f"    L/B FAIL総数:        {total_lb_fail} (PASS={total_lb_pass})")
    print(f"    矛盾検出FAIL総数:    {total_contradiction_fail} (PASS={total_contradiction_pass})")
    print(f"    tier反映FAIL総数:    {total_tier_fail} (PASS={total_tier_pass})")


def build_extended_tests(count: int) -> list[dict[str, Any]]:
    normal_cases = [
        ("United States", 1944, "anti_air_destroyer", "carrier_screen"),
        ("Japan", 1938, "heavy_cruiser", "night_battle_scout"),
        ("United Kingdom", 1907, "dreadnought_battleship", "fleet_battle"),
        ("France", 1888, "protected_cruiser", "overseas_station"),
        ("Argentina", 1942, "light_cruiser", "regional_fleet_scout"),
        ("Netherlands", 1939, "coastal_defense_ship", "coastal_defense"),
        ("Thailand", 1950, "destroyer_escort", "convoy_escort"),
        ("Portugal", 1931, "patrol_boat", "colonial_patrol"),
        ("Brazil", 1950, "patrol_frigate", "long_range_patrol"),
        ("Egypt", 1944, "gunboat", "river_and_coastal_patrol"),
        ("Qing China", 1885, "protected_cruiser", "regional_flagship"),
        ("Norway", 1940, "sloop", "escort_and_patrol"),
        ("Sweden", 1935, "destroyer", "neutrality_patrol"),
    ]
    tests: list[dict[str, Any]] = []
    for i in range(count):
        idx = i + 1
        mod = i % 10
        if mod == 0:
            constraints = {
                "standard_displacement_t_max": 1500,
                "speed_kt_min": 40,
                "main_gun_caliber_mm_min": 203,
                "main_gun_barrels_min": 6,
                "belt_armor_mm_min": 150,
                "range_nm_min": 10000,
            }
            ship_type = "destroyer"
            tests.append({
                "test_id": f"extended_contradiction_{idx:03d}",
                "nation": "Japan",
                "year": 1937,
                "ship_type": ship_type,
                "hull_kind": hull_kind_for_ship_type(ship_type),
                "role": "impossible_super_destroyer",
                "constraints": constraints,
                "expect_status": "impossible",
                "prompt": "小型駆逐艦に大口径砲、重装甲、高速、長航続を同時要求する矛盾検出テスト。",
            })
        elif mod == 1:
            ship_type = "protected_cruiser"
            tests.append({
                "test_id": f"extended_era_{idx:03d}",
                "nation": "United Kingdom",
                "year": 1890,
                "ship_type": ship_type,
                "hull_kind": hull_kind_for_ship_type(ship_type),
                "role": "impossible_early_electronics",
                "constraints": {"radar_required": True, "diesel_required": True, "dual_purpose_gun_required": True, "speed_kt_min": 40},
                "expect_status": "impossible",
                "prompt": "1890年艦にレーダー、両用砲、ディーゼル機関、40kt速力を要求する年代不可能装備テスト。",
            })
        elif mod == 2:
            ship_type = "treaty_cruiser"
            tests.append({
                "test_id": f"extended_treaty_{idx:03d}",
                "nation": "United States",
                "year": 1930,
                "ship_type": ship_type,
                "hull_kind": hull_kind_for_ship_type(ship_type),
                "role": "impossible_treaty_cruiser",
                "constraints": {"standard_displacement_t_min": 16000, "main_gun_caliber_mm_min": 254, "treaty_limited": True},
                "expect_status": "impossible",
                "prompt": "条約期の巡洋艦に16,000t以上と254mm砲を要求する条約違反テスト。",
            })
        elif mod == 3:
            ship_type = "battleship"
            tests.append({
                "test_id": f"extended_tier_{idx:03d}",
                "nation": "Chile",
                "year": 1940,
                "ship_type": ship_type,
                "hull_kind": hull_kind_for_ship_type(ship_type),
                "role": "impossible_minor_power_battleship",
                "constraints": {},
                "expect_status": "impossible",
                "prompt": "限定工業力国が1940年に新造戦艦を自主建造するティア制限テスト。",
            })
        else:
            nation, year, ship_type, role = normal_cases[i % len(normal_cases)]
            specs = get_ship_type_specs(ship_type)
            constraints: dict[str, Any] = {}
            if mod in {4, 8}:
                constraints["standard_displacement_t_max"] = round(specs["normal_t"] * 1.25)
            if mod in {6, 9}:
                constraints["main_gun_caliber_mm_max"] = specs["main_caliber_mm"]
            tests.append({
                "test_id": f"extended_lb_{idx:03d}",
                "nation": nation,
                "year": year,
                "ship_type": ship_type,
                "hull_kind": hull_kind_for_ship_type(ship_type),
                "role": role,
                "constraints": constraints,
                "prompt": f"{year}年の{nation}向けに{role}用途の{ship_type}を設計し、艦種相応のL/B比と国家ティア制約を守る。",
            })
    return tests


def main() -> None:
    parser = argparse.ArgumentParser(description="Run comprehensive navspec generation eval suite")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--test-cases", type=Path, default=TEST_CASES_PATH)
    parser.add_argument("--profile", type=int, default=0)
    parser.add_argument("--skip-existing", action="store_true", help="既存の生成結果を再利用")
    parser.add_argument("--json-report", type=Path, default=None)
    parser.add_argument("--extended-count", type=int, default=100, help="既存ケースに追加するdeterministic拡張テスト数")
    parser.add_argument("--max-new-tokens", type=int, default=256)
    args = parser.parse_args()

    tests = json.loads(args.test_cases.read_text(encoding="utf-8"))
    if args.extended_count > 0:
        tests = tests + build_extended_tests(args.extended_count)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for i, test in enumerate(tests):
        tid = test["test_id"]
        json_out = OUT_DIR / f"{tid}.json"
        raw_out = OUT_DIR / f"{tid}_raw.txt"
        print(f"[{i+1}/{len(tests)}] {tid} ({test['nation']}/{test['year']}/{test['ship_type']})...", flush=True)

        if args.skip_existing and json_out.exists() and raw_out.exists():
            gen_result = {
                "returncode": 0, "stdout": "", "stderr": "", "elapsed_s": 0.0,
                "raw_out": raw_out, "json_out": json_out,
            }
        else:
            gen_result = run_generation(test, args.checkpoint, args.profile, args.max_new_tokens)

        if gen_result["returncode"] != 0:
            err_lines = gen_result["stderr"].strip().split("\n")[-5:]
            print(f"  生成エラー (rc={gen_result['returncode']}): {' | '.join(err_lines)}")

        result = evaluate_test(test, gen_result)
        results.append(result)

        v = result.get("values")
        if v:
            print(f"  -> {v.get('class_name')} disp={v.get('displacement_normal_t')}t "
                  f"spd={v.get('speed_kn')}kt gun={v.get('main_caliber_mm')}mm "
                  f"score={result.get('plausibility_score')} {result.get('plausibility_verdict')}")
        else:
            print(f"  -> JSON出力なし (raw_json={result['raw_json_valid']})")

    print_report(results)

    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSONレポート: {args.json_report}")


if __name__ == "__main__":
    main()
