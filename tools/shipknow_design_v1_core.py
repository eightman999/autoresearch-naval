"""shipknow 設計データ design-v1 の共通部品（生成器・検査器・テストが共有する純関数）。

design-v1 は design-v0（要求 -> 設計 JSON）の弱点（砲門数・搭載機数・魚雷管数を学習していない、速力と出力の連動、不可能要求を黙って緩和する、
1-shot で手本の艦種に引きずられる）を直すための、会話エピソード形式のデータ。この module には次を置く。

  * 出力 schema（status / conflicts / relaxations / design）と、design-v1 用 system プロンプト
  * 要求 req の語彙（v0 のキー + 評価 v1 の拡張キー + min_displacement_t / max_speed_kn / premise）、制約 id、制約ごとの判定 `req_checks_v1`
  * 要求の生成（拡張キー込み）と要求文（6 通りの文型）
  * `check_design`: 既存の決定論 validator（v0 validator + 評価 v1 の整合性規則 + 拡張要求）を 1 つにまとめた検査器。違反は {code, field, detail}
  * 能力語彙と初出年（年代検査）、前提（premise）が許す年代外の能力 = 免除（waivers）
  * `DesignIndex`: 実艦の設計の配列インデックス（numpy）。要求を満たす実艦の探索、「その要求を満たす実艦が無い」= 実データ上の不可能の判定に使う

LLM は一切使わない。乱数は呼び出し側が渡す random.Random（seed 固定）だけ。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval as DE  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_validator as V  # noqa: E402

ROOT = C.ROOT
V0_DIR = C.V0_DIR
DESIGN_V0_DIR = C.OUT_DIR
EVAL_V1_DIR = ROOT / "data" / "navspec" / "shipknow_design_eval_v1"
OUT_DIR = ROOT / "data" / "navspec" / "shipknow_design_v1"
PILOT_DIR = ROOT / "data" / "navspec" / "shipknow_design_v1_pilot"
DEFAULT_TOKENIZER = Path("/private/tmp/claude-501/-Users-eightman-dev/0ec7c03e-08af-473b-871d-d48b22f6efb4/scratchpad/Qwen3.5-0.8B/tokenizer.json")

CATEGORIES = ["single", "modify", "repair", "infeasible", "ood", "context"]
# 全量（約 10 万）の目標比率。厳密ではないが、repair と context を十分多く。
CATEGORY_RATIO = {"single": 0.30, "modify": 0.20, "repair": 0.25, "infeasible": 0.10, "ood": 0.05, "context": 0.10}
SPLITS = ["train", "valid", "test"]
STATUSES = ("feasible", "relaxed", "infeasible")
OUT_KEYS = ["status", "conflicts", "relaxations", "design"]

# --------------------------------------------------------------------------------------------------
# system prompt
# --------------------------------------------------------------------------------------------------
# design-v1 の system プロンプト（250 トークン以下に短縮。design-v0 の system 352 + 追記の v1 初版 592 トークンから）。
# 設計 JSON のキー、出力 schema、status の定義、最新の要求に答える、黙って無視しない、nbr はヒント（略語は省略: 近傍のキーは短縮形のまま）を残す。
SYSTEM_V1 = (
    'Warship design from "req" (limits, required equipment, premise) and "nbr" (similar real ships, hints only; short keys). '
    'Reply with ONE JSON object only: {"status":"feasible|relaxed|infeasible","conflicts":[{"constraint","reason"}],"relaxations":[{"constraint","requested","relaxed_to","reason"}],'
    '"design":{ship_type,ship_type_fine,displacement_normal_t/full_t/standard_t,length_m,beam_m,draught_m,propulsion[],shafts,power_hp,speed_kn,endurance_nm,complement,armour{belt,deck,turret},'
    'weapons[{kind,n,per,cal}],sensors[],air[]}|null}. '
    "feasible: all requirements met. relaxed: some cannot be met together; list each in conflicts and relaxations, give the closest consistent design. "
    "infeasible: no reasonable relaxation; design null. "
    "Never silently ignore or loosen a requirement; never invent numbers. Answer the latest user message; earlier designs and references are context. "
    "The design must be physically consistent and use only technology of the request year (a premise changes only what it names). After a validator report, fix the design."
)
SYSTEM_V1_PREVIOUS_TOKENS = 592  # 初版（design-v0 の system + 追記）のトークン数（Qwen3.5-0.8B tokenizer、ChatML 抜き）


def jdump(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def sub_rng(seed: int, *parts: Any) -> random.Random:
    h = hashlib.sha256("|".join([str(seed), *map(str, parts)]).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def _isnum(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def num(x: float) -> float | int:
    x = round(float(x), 1)
    return int(x) if x.is_integer() else x


# --------------------------------------------------------------------------------------------------
# output schema
# --------------------------------------------------------------------------------------------------
def order_design(d: dict[str, Any]) -> dict[str, Any]:
    out = {k: d[k] for k in C.DESIGN_KEY_ORDER if k in d and d[k] is not None}
    return out


def make_output(status: str, design: dict[str, Any] | None, conflicts: list[dict[str, Any]] | None = None,
                relaxations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"status": status, "conflicts": conflicts or [], "relaxations": relaxations or [], "design": order_design(design) if design is not None else None}


def dump_output(status: str, design: dict[str, Any] | None, conflicts: list[dict[str, Any]] | None = None, relaxations: list[dict[str, Any]] | None = None) -> str:
    return jdump(make_output(status, design, conflicts, relaxations))


def parse_output(text: Any) -> tuple[dict[str, Any] | None, str]:
    """assistant の content を厳密に parse する。戻り値 (obj, error)。余計な文章（JSON の前後の自然文）があれば error。"""
    if not isinstance(text, str) or not text.strip():
        return None, "empty"
    try:
        obj = json.loads(text)
    except Exception as e:  # noqa: BLE001
        return None, f"json: {e}"
    if not isinstance(obj, dict):
        return None, "not an object"
    if list(obj.keys()) != OUT_KEYS:
        return None, f"keys {list(obj.keys())}"
    return obj, ""


# --------------------------------------------------------------------------------------------------
# requirement vocabulary
# --------------------------------------------------------------------------------------------------
REQ_ORDER = ["country", "year", "ship_type", "ship_type_fine", "mission", "max_displacement_t", "min_displacement_t", "displacement_basis", "min_speed_kn", "max_speed_kn",
             "required", "max_complement", "min_endurance_nm", "min_gun_barrels", "min_gun_barrels_cal_mm", "min_aircraft", "min_torpedo_tubes", "required_propulsion", "premise"]
PROPULSION_KINDS = ["steam_reciprocating", "steam_turbine", "diesel", "electric_motor", "petrol_engine", "gas_turbine", "nuclear"]
REQ_NUM_KEYS = ["max_displacement_t", "min_displacement_t", "min_speed_kn", "max_speed_kn", "max_complement", "min_endurance_nm", "min_gun_barrels", "min_gun_barrels_cal_mm",
                "min_aircraft", "min_torpedo_tubes", "year"]
REQ_INT_KEYS = ["max_complement", "min_endurance_nm", "min_gun_barrels", "min_gun_barrels_cal_mm", "min_aircraft", "min_torpedo_tubes", "year"]


def order_req(req: dict[str, Any]) -> dict[str, Any]:
    out = {k: req[k] for k in REQ_ORDER if k in req and req[k] is not None}
    out.update({k: v for k, v in req.items() if k not in out and v is not None})
    return out


def constraint_ids(req: dict[str, Any]) -> list[str]:
    """要求の制約の id（国・年・艦種・任務・前提は数えない）。"""
    ids: list[str] = []
    for k in ("max_displacement_t", "min_displacement_t", "min_speed_kn", "max_speed_kn", "max_complement", "min_endurance_nm", "min_gun_barrels", "min_aircraft", "min_torpedo_tubes"):
        if req.get(k) is not None:
            ids.append(k)
    ids += [f"required:{t}" for t in req.get("required") or []]
    ids += [f"required_propulsion:{p}" for p in req.get("required_propulsion") or []]
    return ids


def req_checks_v1(req: dict[str, Any], design: dict[str, Any]) -> list[dict[str, Any]]:
    """制約ごとの判定 [{id, ok, detail}]（ok = True / False / None〈判定不能〉）。拡張評価の req_checks に min_displacement_t / max_speed_kn を足し、id を正規化する。"""
    out: list[dict[str, Any]] = []
    if not isinstance(design, dict):
        return out
    for c in X.req_checks(req, design):
        key = c["key"]
        if key.startswith("max_displacement_t("):
            key = "max_displacement_t"
        out.append({"id": key, "ok": c["ok"], "detail": c["detail"]})
    basis = req.get("displacement_basis", "normal")
    if req.get("min_displacement_t") is not None:
        val = design.get(f"displacement_{basis}_t")
        out.append({"id": "min_displacement_t", "ok": (val >= req["min_displacement_t"]) if _isnum(val) else None, "detail": f"{val}"})
    if req.get("max_speed_kn") is not None:
        val = design.get("speed_kn")
        out.append({"id": "max_speed_kn", "ok": (val <= req["max_speed_kn"]) if _isnum(val) else None, "detail": f"{val}"})
    return out


def failed_constraints(req: dict[str, Any], design: dict[str, Any], ignore_hull: bool = False) -> list[str]:
    return [c["id"] for c in req_checks_v1(req, design) if c["ok"] is not True and not (ignore_hull and c["id"] in ("ship_type", "ship_type_fine"))]


def satisfies_all(req: dict[str, Any], design: dict[str, Any], ignore_hull: bool = False) -> bool:
    return not failed_constraints(req, design, ignore_hull)


# --------------------------------------------------------------------------------------------------
# capabilities, era
# --------------------------------------------------------------------------------------------------
TOKEN_CAP = {"torpedo_tubes": "torpedo_tube", "asw_weapon": "asw_weapon", "sonar": "sonar", "helicopter": "air:helicopter", "aircraft": "aircraft", "ssm": "ssm", "sam": "sam",
             "ciws": "ciws", "rocket_launcher": "rocket_launcher", "ballistic_missile": "ballistic_missile", "air_search_radar": "air_search_radar",
             "fire_control_radar": "fire_control_radar", "surface_radar": "surface_radar", "radar": "radar_any", "esm_ecm": "esm_ecm"}
CAP_NAME = {"torpedo_tube": "torpedo tubes", "asw_weapon": "anti-submarine weapons", "sonar": "sonar", "air:helicopter": "shipboard helicopters", "aircraft": "embarked aircraft",
            "ssm": "anti-ship missiles", "sam": "surface-to-air missiles", "ciws": "close-in weapon systems", "rocket_launcher": "rocket launchers",
            "ballistic_missile": "ballistic missiles", "air_search_radar": "air-search radar", "fire_control_radar": "fire-control radar", "surface_radar": "surface-search radar",
            "radar_any": "radar", "radar": "radar", "esm_ecm": "ESM/ECM", "prop:gas_turbine": "gas-turbine propulsion", "prop:nuclear": "nuclear propulsion",
            "prop:diesel": "diesel propulsion", "prop:steam_turbine": "steam-turbine propulsion", "prop:electric_motor": "electric propulsion"}
RADAR_FAMILY = ["radar_any", "radar", "air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar"]
# 技術前倒しの前提 -> 年代検査を免除する能力語彙（前提が名指しした技術だけ）
TECH_WAIVERS = {"radar": RADAR_FAMILY, "sonar": ["sonar"], "helicopter": ["air:helicopter", "aircraft"], "ssm": ["ssm"], "sam": ["sam"], "gas_turbine": ["prop:gas_turbine"],
                "nuclear": ["prop:nuclear"], "ciws": ["ciws"], "esm_ecm": ["esm_ecm"]}
TECH_TOKEN = {"radar": "air_search_radar", "sonar": "sonar", "helicopter": "helicopter", "ssm": "ssm", "sam": "sam", "ciws": "ciws", "esm_ecm": "esm_ecm"}
TECH_PROP = {"gas_turbine": "gas_turbine", "nuclear": "nuclear"}
ERA_CLEAR_GAP = 10  # 年代の不可能 = 初出年より 10 年以上前（validator の許容 5 年より厳しく、曖昧な境界の例を使わない）


def waivers_for_premise(premise: dict[str, Any] | None) -> list[str]:
    if not premise:
        return []
    out: list[str] = []
    if premise.get("type") == "tech_early":
        out += TECH_WAIVERS.get(premise.get("tech"), [])
    for t in premise.get("also_tech") or []:
        out += TECH_WAIVERS.get(t, [])
    return sorted(set(out))


_TH: dict[str, Any] = {}


def thresholds() -> dict[str, Any]:
    if "th" not in _TH:
        _TH["th"] = V.load_thresholds()
    return _TH["th"]


def reference() -> DE.Ref:
    return DE.default_ref()


def first_year(cap: str) -> int | None:
    fy = thresholds()["first_year"].get(cap)
    return fy["year"] if fy else None


def cap_early_by(cap: str, year: int) -> int:
    """能力 cap が year の何年先に初出か（正 = year ではまだ無い）。初出年が不明なら -999。"""
    fy = first_year(cap)
    return (fy - year) if fy else -999


# --------------------------------------------------------------------------------------------------
# validator wrapper
# --------------------------------------------------------------------------------------------------
_V_PREFIXES = ("ratio_", "gun_", "era_early", "type_", "schema_", "req_")


def viol(code: str, field: str, detail: str) -> dict[str, str]:
    return {"code": code, "field": field, "detail": detail}


_TCAL: dict[str, dict[int, int]] = {}
TORPEDO_CAL_TOL_YEARS = 5


def torpedo_cal_first_year() -> dict[int, int]:
    """魚雷口径（mm、整数）ごとの初出年: train の艦級グループごとの最初の年の 3 番目に早い年（5 グループ以上ある口径だけ）。validator の年代表が見ない項目。"""
    if "t" not in _TCAL:
        recs = [json.loads(l) for l in (V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        sp = json.loads((V0_DIR / "splits.json").read_text(encoding="utf-8"))
        by: dict[int, dict[str, int]] = {}
        for r in recs:
            if sp["assignments"].get(r["id"]) != "train" or not C.design_eligible(r):
                continue
            y, g = C.request_year(r), sp["group_of"][r["id"]]
            for w in C.design_from_record(r).get("weapons") or []:
                if w.get("kind") == "torpedo_tube" and _isnum(w.get("cal")):
                    c = int(round(w["cal"]))
                    by.setdefault(c, {})
                    by[c][g] = min(y, by[c].get(g, 9999))
        _TCAL["t"] = {c: sorted(gy.values())[2] for c, gy in by.items() if len(gy) >= 5}
    return _TCAL["t"]


def _era_torpedo_violations(design: dict[str, Any], year: Any) -> list[dict[str, str]]:
    if not _isnum(year):
        return []
    table = torpedo_cal_first_year()
    out = []
    for w in design.get("weapons") or []:
        if isinstance(w, dict) and w.get("kind") == "torpedo_tube" and _isnum(w.get("cal")):
            c = int(round(w["cal"]))
            if c in table and year < table[c] - TORPEDO_CAL_TOL_YEARS:
                out.append(viol("era_early", f"torpedo_cal:{c}", f"{c} mm torpedo tubes first appear around {table[c]} in real classes; design year is {int(year)}"))
    return out


def _extended_req_violations(req: dict[str, Any], design: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if req.get("min_gun_barrels") is not None:
        cal = req.get("min_gun_barrels_cal_mm")
        n = X.gun_barrels(design, cal)
        if n < req["min_gun_barrels"]:
            out.append(viol("req_gun_barrels_short", "min_gun_barrels", f"{n} gun barrels" + (f" of {cal:g} mm or larger" if cal else "") + f" < required {req['min_gun_barrels']}"))
    if req.get("min_aircraft") is not None:
        n = X.aircraft_count(design)
        if n < req["min_aircraft"]:
            out.append(viol("req_aircraft_short", "min_aircraft", f"{n} embarked aircraft < required {req['min_aircraft']}"))
    if req.get("min_torpedo_tubes") is not None:
        n = X.torpedo_tubes(design)
        if n < req["min_torpedo_tubes"]:
            out.append(viol("req_torpedo_tubes_short", "min_torpedo_tubes", f"{n} torpedo tubes < required {req['min_torpedo_tubes']}"))
    props = design.get("propulsion") if isinstance(design.get("propulsion"), list) else []
    for p in req.get("required_propulsion") or []:
        if p not in props:
            out.append(viol("req_propulsion_missing", f"required_propulsion:{p}", f"propulsion {props or 'unspecified'} does not include required {p}"))
    basis = req.get("displacement_basis", "normal")
    if req.get("min_displacement_t") is not None:
        val = design.get(f"displacement_{basis}_t")
        if _isnum(val) and val < req["min_displacement_t"]:
            out.append(viol("req_displacement_below", f"displacement_{basis}_t", f"{val} t < minimum {req['min_displacement_t']} t"))
    if req.get("max_speed_kn") is not None:
        val = design.get("speed_kn")
        if _isnum(val) and val > req["max_speed_kn"]:
            out.append(viol("req_speed_exceeds", "speed_kn", f"{val} kn > maximum {req['max_speed_kn']} kn"))
    return out


def _consistency_extra(design: dict[str, Any], req: dict[str, Any], waivers: list[str]) -> list[dict[str, str]]:
    """評価 v1 の整合性規則のうち v0 validator に無いもの（armament_excess など）を violation にする。"""
    ref, th = reference(), thresholds()
    hard, _soft = DE.consistency(design, req, ref, th, waivers)
    out: list[dict[str, str]] = []
    dref, _b = C.ref_displacement(design)
    for h in hard:
        code, _, field = h.partition(":")
        if any(code.startswith(p) for p in ("ratio_", "gun_", "era_early", "type_equipment")):
            continue
        if code == "armament_excess":
            out.append(viol(code, "weapons", f"main-gun firepower index {DE.gun_index(design):.1f} on {dref:g} t is far above comparable ships of this type and era"))
        elif code == "weapon_count_excess":
            fam = field
            cnt = DE.family_count(design, fam)
            out.append(viol(code, fam, f"{cnt:g} x {fam} on {dref:g} t; comparable ships of up to {1.5 * dref:.0f} t carry at most {ref.count_envelope(fam, 1.5 * dref):g}"))
        elif code == "type_displacement_mismatch":
            out.append(viol(code, "displacement", f"{dref:g} t is outside the range of real {design.get('ship_type')} classes"))
        elif code == "displacement_order":
            out.append(viol(code, "displacement", "displacement fields are not ordered (standard <= normal <= full)"))
        elif code == "air_without_entry":
            out.append(viol(code, "air", "air lists aircraft but weapons has no aircraft_helicopter / aviation_facility entry"))
        elif code == "air_entry_without_air":
            out.append(viol(code, "air", "weapons has an aircraft entry but air is empty"))
        elif code == "heli_hull_too_short":
            out.append(viol(code, "length_m", f"a helicopter on a {design.get('length_m')} m hull (shortest real helicopter ship: {ref.heli_len_min:.0f} m)"))
        elif code == "nuclear_small_hull":
            out.append(viol(code, "propulsion", f"nuclear propulsion on {dref:g} t (smallest real nuclear ship: {ref.nuclear_min_disp:.0f} t)"))
        elif code == "beam_ge_length":
            out.append(viol(code, "beam_m", "beam is not smaller than length"))
        elif code == "sub_many_big_guns":
            out.append(viol(code, "weapons", "a submarine with more than 3 guns of 100 mm or larger"))
        else:
            out.append(viol(code, field, h))
    return out


def check_design(req: dict[str, Any] | None, design: Any, waivers: Iterable[str] = (), *, th: dict[str, Any] | None = None) -> dict[str, Any]:
    """design-v1 の決定論 validator。v0 validator（schema / req / ratio / gun / era / type）+ 拡張要求 + 評価 v1 の整合性規則。
    waivers = 前提が許す年代外の能力語彙（era_early だけ免除）。戻り値 {"ok", "violations": [{code, field, detail}], "warnings": [...]}。"""
    th = th if th is not None else thresholds()
    waive = set(waivers or [])
    req = req or {}
    res = V.validate(req, design, th)
    violations = [x for x in res["violations"] if not (x["code"] == "era_early" and x["field"] in waive)]
    warnings = list(res["warnings"])
    if isinstance(design, dict) and not any(x["code"] == "schema_not_object" for x in res["violations"]):
        violations += _extended_req_violations(req, design)
        if design.get("ship_type") in V.T.COARSE_TYPES:
            violations += _consistency_extra(design, req, sorted(waive))
            violations += _era_torpedo_violations(design, req.get("year"))
    return {"ok": not violations, "violations": violations, "warnings": warnings}


VIOLATION_CONSTRAINT = {"req_displacement_exceeds": "max_displacement_t", "req_displacement_below": "min_displacement_t", "req_speed_below": "min_speed_kn", "req_speed_exceeds": "max_speed_kn",
                        "req_complement_exceeds": "max_complement", "req_endurance_below": "min_endurance_nm", "req_gun_barrels_short": "min_gun_barrels",
                        "req_aircraft_short": "min_aircraft", "req_torpedo_tubes_short": "min_torpedo_tubes"}


def violation_constraint(v: dict[str, str]) -> str | None:
    code = v["code"]
    if code in VIOLATION_CONSTRAINT:
        return VIOLATION_CONSTRAINT[code]
    if code == "req_equipment_missing":
        return f"required:{v['field']}"
    if code == "req_propulsion_missing":
        return v["field"]
    return None


def apply_relaxations(req: dict[str, Any], relaxations: list[dict[str, Any]]) -> dict[str, Any]:
    """緩和した制約を要求から外した要求（relaxed の設計は、これを満たし、緩和した制約だけを満たさない）。"""
    out = copy.deepcopy(req)
    for r in relaxations:
        cid = r.get("constraint", "")
        if cid.startswith("required:"):
            tok = cid.split(":", 1)[1]
            out["required"] = [t for t in out.get("required") or [] if t != tok]
        elif cid.startswith("required_propulsion:"):
            p = cid.split(":", 1)[1]
            out["required_propulsion"] = [x for x in out.get("required_propulsion") or [] if x != p]
            if not out["required_propulsion"]:
                out.pop("required_propulsion")
        elif cid in out and cid not in ("country", "year"):
            out.pop(cid)
            if cid == "min_gun_barrels":
                out.pop("min_gun_barrels_cal_mm", None)
    return out


# --------------------------------------------------------------------------------------------------
# request generation (with extended keys)
# --------------------------------------------------------------------------------------------------
GEN_CFG = {"p_barrels": 0.42, "p_barrels_cal": 0.6, "p_aircraft": 0.75, "p_tubes": 0.55, "p_prop": 0.14, "p_min_disp": 0.06, "p_max_speed": 0.05, "p_endurance_sparse": 0.0}


def _barrels_ge(design: dict[str, Any], cal: float | None) -> int:
    return X.gun_barrels(design, cal)


def make_req_v1(design: dict[str, Any], country: str, year: int, rng: random.Random, cfg: dict[str, float] | None = None, force: set[str] | None = None) -> dict[str, Any]:
    """元艦の設計から要求を逆算する（v0 の make_requirements + 拡張キー）。拡張キーは必ず元艦が満たす（砲身数・搭載機数・魚雷管数は元艦の実数以下）。
    force = 必ず入れる拡張キー（"barrels" / "aircraft" / "tubes" / "prop"）。入れられない艦（砲が無い等）ではそのキーは出ない。"""
    cfg = {**GEN_CFG, **(cfg or {})}
    force = force or set()
    req = C.make_requirements(design, country, year, rng)
    gun = C.main_gun_mm(design)
    nb = X.gun_barrels(design)
    if (("barrels" in force) or rng.random() < cfg["p_barrels"]) and gun and nb >= 2:
        cal = None
        if rng.random() < cfg["p_barrels_cal"]:
            grid = [g for g in C.GUN_GRID if g <= gun]
            if grid:
                cal = float(grid[-1]) if rng.random() < 0.7 or len(grid) == 1 else float(grid[-2])
                cal = int(cal) if float(cal).is_integer() else cal
        n_ge = X.gun_barrels(design, cal)
        if n_ge >= 2:
            k = n_ge - rng.choices([0, 1, 2], weights=[0.55, 0.3, 0.15])[0]
            req["min_gun_barrels"] = max(2, k) if n_ge >= max(2, k) else n_ge
            if cal:
                req["min_gun_barrels_cal_mm"] = cal
    na = X.aircraft_count(design)
    if na >= 1 and (("aircraft" in force) or rng.random() < cfg["p_aircraft"]):
        req["min_aircraft"] = int(max(1, na - rng.choices([0, 1, 2], weights=[0.6, 0.25, 0.15])[0] if na > 2 else na))
    nt = X.torpedo_tubes(design)
    if nt >= 2 and (("tubes" in force) or rng.random() < cfg["p_tubes"]):
        req["min_torpedo_tubes"] = int(max(1, nt - rng.choices([0, 1, 2], weights=[0.6, 0.25, 0.15])[0] if nt > 3 else nt))
    props = [p for p in design.get("propulsion") or [] if p in PROPULSION_KINDS]
    if props and (("prop" in force) or rng.random() < cfg["p_prop"]):
        req["required_propulsion"] = [rng.choice(props)]
    basis = req.get("displacement_basis", "normal")
    dv = design.get(f"displacement_{basis}_t")
    if _isnum(dv) and rng.random() < cfg["p_min_disp"] and dv >= 40:
        req["min_displacement_t"] = int(C._nice_round(dv * rng.uniform(0.6, 0.9)))
        if req["min_displacement_t"] > dv or req["min_displacement_t"] >= req["max_displacement_t"]:
            req.pop("min_displacement_t")
    v = design.get("speed_kn")
    if _isnum(v) and rng.random() < cfg["p_max_speed"] and v >= 8:
        req["max_speed_kn"] = num(math.ceil(v * rng.uniform(1.06, 1.25) * 2) / 2)
    # req がキーを足しても、元艦は必ず満たす
    assert satisfies_all(req, design), (req, failed_constraints(req, design))
    return order_req(req)


def loosen_req(req: dict[str, Any], design: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """国変更・年代変更の元の要求用: 別の実艦でも満たせるように、排水量の上限と最低速力を緩め、装備の要求を 1〜2 個に絞り、拡張キーを外す（元艦は必ず満たす）。"""
    r = {k: v for k, v in req.items() if k not in ("min_gun_barrels", "min_gun_barrels_cal_mm", "min_aircraft", "min_torpedo_tubes", "required_propulsion", "min_displacement_t", "max_speed_kn",
                                                    "max_complement", "min_endurance_nm")}
    basis = r.get("displacement_basis", "normal")
    dv = design.get(f"displacement_{basis}_t")
    if _isnum(dv):
        r["max_displacement_t"] = num(C._nice_round(dv * rng.uniform(1.25, 1.6)))
    v = design.get("speed_kn")
    if _isnum(v) and r.get("min_speed_kn") is not None:
        r["min_speed_kn"] = num(max(2.0, math.floor((v - rng.uniform(2.5, 6.0)) * 2) / 2))
    toks = list(r.get("required") or [])
    if len(toks) > 2:
        toks = toks[: rng.choice([1, 2])]
    r["required"] = toks
    assert satisfies_all(r, design), (r, failed_constraints(r, design))
    return order_req(r)


def combo_signature(req: dict[str, Any]) -> str:
    """要求の「組み合わせ」の署名（艦種 + 数値制約の種別 + 装備 token の種別）。test 専用の未見の組み合わせの判定に使う。"""
    toks = sorted(re.sub(r">=\d+(\.\d+)?mm", ">=Nmm", t) for t in req.get("required") or [])
    feats = [k for k in ("min_gun_barrels", "min_aircraft", "min_torpedo_tubes", "required_propulsion", "min_displacement_t", "max_speed_kn", "max_complement", "min_endurance_nm") if req.get(k)]
    return req.get("ship_type", "?") + "|" + ",".join(feats) + "|" + ",".join(toks)


HELDOUT_COMBO_MOD = 8


def is_heldout_combo(req: dict[str, Any]) -> bool:
    """署名の hash で決まる test 専用の組み合わせ（1/8）。train / valid の要求には出さない。拡張キーか装備 token を 2 つ以上含む署名だけが対象。"""
    sig = combo_signature(req)
    feats, toks = sig.split("|")[1], sig.split("|")[2]
    if len([x for x in (feats + "," + toks).split(",") if x]) < 3:
        return False
    return int.from_bytes(hashlib.sha256(sig.encode()).digest()[:4], "big") % HELDOUT_COMBO_MOD == 0


# --------------------------------------------------------------------------------------------------
# request text (6 sentence patterns, English)
# --------------------------------------------------------------------------------------------------
def _lim_parts(req: dict[str, Any]) -> list[str]:
    basis = req.get("displacement_basis", "normal")
    parts: list[str] = []
    mx, mn = req.get("max_displacement_t"), req.get("min_displacement_t")
    if mn is not None and mx is not None and mn > mx:
        parts.append(f"displacement at least {mn} t and at most {mx} t ({basis})")
    elif mn is not None and mx is not None:
        parts.append(f"displacement between {mn} and {mx} t ({basis})")
    elif mx is not None:
        parts.append(f"displacement at most {mx} t ({basis})")
    elif mn is not None:
        parts.append(f"displacement at least {mn} t ({basis})")
    lo, hi = req.get("min_speed_kn"), req.get("max_speed_kn")
    if lo is not None and hi is not None and lo > hi:
        parts.append(f"speed at least {lo} kn and at most {hi} kn")
    elif lo is not None and hi is not None:
        parts.append(f"speed between {lo} and {hi} kn")
    elif lo is not None:
        parts.append(f"speed at least {lo} kn")
    elif hi is not None:
        parts.append(f"speed at most {hi} kn")
    if req.get("max_complement"):
        parts.append(f"crew at most {req['max_complement']}")
    if req.get("min_endurance_nm"):
        parts.append(f"endurance at least {req['min_endurance_nm']} nm")
    if req.get("required_propulsion"):
        parts.append("propulsion by " + C._en_list([p.replace("_", " ") for p in req["required_propulsion"]]))
    return parts


def _need_parts(req: dict[str, Any], extra: list[str] | None = None) -> list[str]:
    need = [C._req_item_en(t) for t in req.get("required") or [] if not (t == "torpedo_tubes" and req.get("min_torpedo_tubes") is not None)]
    if req.get("min_gun_barrels") is not None:
        cal = req.get("min_gun_barrels_cal_mm")
        need.append(f"at least {req['min_gun_barrels']} gun barrels" + (f" of {cal:g} mm or larger" if cal else ""))
    if req.get("min_aircraft") is not None:
        n = req["min_aircraft"]
        need.append(f"at least {n} embarked aircraft" if n != 1 else "an embarked aircraft")
    if req.get("min_torpedo_tubes") is not None:
        need.append(f"at least {req['min_torpedo_tubes']} torpedo tubes")
    return need + list(extra or [])


def request_text_v1(req: dict[str, Any], variant: int = 0, premise_text: str | None = None, extra: list[str] | None = None) -> str:
    t = req["ship_type_fine"].replace("-", " ") if req.get("ship_type_fine") else C._TYPE_NAME.get(req["ship_type"], req["ship_type"].replace("_", " "))
    art = "an" if t[0] in "aeiou" else "a"
    nation = req["country"].replace("-", " ")
    adj = C._NATION_ADJ.get(nation)
    nation_adj = f"the {adj} navy" if adj else f"the navy of {nation}"
    nation_short = adj if adj else nation
    mission = C._en_list([C._MISSION_EN.get(m, m) for m in req.get("mission", [])]) or "general service"
    lim = C._en_list(_lim_parts(req)) or "no numeric limits"
    need = C._en_list(_need_parts(req, extra))
    year = req["year"]
    pre = (premise_text.strip() + " ") if premise_text else ""
    v = variant % 6
    need_s = f" It must carry {need}." if need else ""
    if v == 0:
        s = f"Design {art} {t} for {nation_adj} around {year}, for {mission}: {lim}.{need_s}"
    elif v == 1:
        s = f"{nation_adj[0].upper() + nation_adj[1:]} wants {art} {t} designed in {year}; role: {mission}. Limits: {lim}.{need_s}"
    elif v == 2:
        s = f"Propose {art} {t} design ({nation}, {year}) for {mission} with {lim}.{need_s}"
    elif v == 3:
        s = f"I need {art} {t} for {nation_adj}, {year}. Mission: {mission}. Constraints: {lim}." + (f" Equipment: {need}." if need else "")
    elif v == 4:
        s = f"{year} {nation_short} {t} ({mission}). {lim[0].upper() + lim[1:]}." + (f" Required equipment: {need}." if need else "")
    else:
        s = f"Please draft {art} {t} (year {year}, {nation}) that stays within these limits: {lim}." + (f" It must have {need}." if need else "") + f" Role: {mission}."
    return pre + s


# --------------------------------------------------------------------------------------------------
# DesignIndex: numpy index of real designs (candidates for "nearby real ship" and the "no real ship satisfies it" oracle)
# --------------------------------------------------------------------------------------------------
class DesignIndex:
    def __init__(self, recs: list[dict[str, Any]], designs: dict[str, dict[str, Any]]):
        self.recs = recs
        self.ids = [r["id"] for r in recs]
        self.pos = {rid: i for i, rid in enumerate(self.ids)}
        self.designs = [designs[r["id"]] for r in recs]
        self.type = np.array([d["ship_type"] for d in self.designs])
        self.fine = np.array([d.get("ship_type_fine") or "" for d in self.designs])
        self.year = np.array([C.request_year(r) for r in recs], dtype=float)
        self.country = np.array([r["country"] for r in recs])

        def col(f: str) -> np.ndarray:
            return np.array([d[f] if _isnum(d.get(f)) else np.nan for d in self.designs], dtype=float)

        self.dn, self.df, self.ds = col("displacement_normal_t"), col("displacement_full_t"), col("displacement_standard_t")
        self.speed, self.comp, self.endu = col("speed_kn"), col("complement"), col("endurance_nm")

    def __len__(self) -> int:
        return len(self.recs)

    def disp(self, basis: str) -> np.ndarray:
        return {"normal": self.dn, "full": self.df, "standard": self.ds}[basis]

    def prefilter(self, req: dict[str, Any], *, year_lo: float | None = None, year_hi: float | None = None, country: str | None = None,
                  same_type: bool = True, ignore: set[str] | None = None) -> np.ndarray:
        """vectorised な粗い絞り込み（艦種・年・国・排水量・速力）。戻り値は index の配列。本判定は satisfies_all。"""
        ignore = ignore or set()
        m = np.ones(len(self), dtype=bool)
        if same_type and req.get("ship_type"):
            m &= self.type == req["ship_type"]
        if year_lo is not None:
            m &= self.year >= year_lo
        if year_hi is not None:
            m &= self.year <= year_hi
        if country is not None:
            m &= self.country == country
        basis = req.get("displacement_basis", "normal")
        dd = self.disp(basis)
        with np.errstate(invalid="ignore"):
            if req.get("max_displacement_t") is not None and "max_displacement_t" not in ignore:
                m &= dd <= req["max_displacement_t"]
            if req.get("min_displacement_t") is not None and "min_displacement_t" not in ignore:
                m &= dd >= req["min_displacement_t"]
            if req.get("min_speed_kn") is not None and "min_speed_kn" not in ignore:
                m &= self.speed >= req["min_speed_kn"]
            if req.get("max_speed_kn") is not None and "max_speed_kn" not in ignore:
                m &= self.speed <= req["max_speed_kn"]
        return np.where(m)[0]

    def satisfying(self, req: dict[str, Any], *, ignore_hull: bool = True, exclude: set[str] | None = None, **kw: Any) -> list[int]:
        out = []
        for i in self.prefilter(req, **kw):
            if exclude and self.ids[int(i)] in exclude:
                continue
            if satisfies_all(req, self.designs[int(i)], ignore_hull=ignore_hull):
                out.append(int(i))
        return out


def drop_constraint(req: dict[str, Any], cid: str) -> dict[str, Any]:
    return apply_relaxations(req, [{"constraint": cid}])
