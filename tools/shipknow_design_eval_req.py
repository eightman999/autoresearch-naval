"""設計評価 v1（shipknow_design_eval_v1）の拡張要求: 追加キー、充足判定、要求文、ペアテストの指標。

v0 の ``req`` のキー（country, year, ship_type, ship_type_fine, mission, max_displacement_t, displacement_basis, min_speed_kn, required[], max_complement,
min_endurance_nm）に、評価用の**追加キー**（すべて任意）を足す。v0 の validator は未知キーを無視するので、既存の採点・検査は壊れない。

  min_gun_barrels             砲身数の下限 = Σ(n × per) over kind=gun。min_gun_barrels_cal_mm があれば、その口径以上の砲だけ数える
  min_gun_barrels_cal_mm
  min_aircraft                搭載機数の下限 = Σ n over kind=aircraft_helicopter（n 省略は 1）。aviation_facility だけ（n なし）は 0 機
  min_torpedo_tubes           魚雷発射管数の下限 = Σ(n × per) over kind=torpedo_tube
  required_propulsion         推進の必須種別（設計の propulsion に全て含まれる。gas_turbine / nuclear / diesel / steam_turbine / electric_motor ...）

``required`` の語彙は v0 のまま（main_gun>=Nmm、sam、ssm、helicopter、air_search_radar ...）。語彙に無いもの（AESA、VLS など）は要求文に書き、
構造化要求では近い kind（air_search_radar、sam）にして、元の語を problem の meta.original_terms に残す。
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_design_common as C  # noqa: E402

EXT_KEYS = ["min_gun_barrels", "min_gun_barrels_cal_mm", "min_aircraft", "min_torpedo_tubes", "required_propulsion"]
BASE_KEYS = ["country", "year", "ship_type", "ship_type_fine", "mission", "max_displacement_t", "displacement_basis", "min_speed_kn", "required",
             "max_complement", "min_endurance_nm"]
REQ_KEY_ORDER = ["country", "year", "ship_type", "ship_type_fine", "mission", "max_displacement_t", "displacement_basis", "min_speed_kn", "required",
                 "max_complement", "min_endurance_nm", "min_gun_barrels", "min_gun_barrels_cal_mm", "min_aircraft", "min_torpedo_tubes", "required_propulsion"]

SENSOR_TOKENS = {"air_search_radar", "fire_control_radar", "surface_radar", "radar", "sonar", "esm_ecm"}
WEAPON_TOKENS = {"torpedo_tubes", "asw_weapon", "ssm", "sam", "ciws", "rocket_launcher", "ballistic_missile", "mines", "minesweeping"}
AIR_TOKENS = {"helicopter", "aircraft"}
CONSTRAINT_TYPES = ["displacement", "speed", "complement", "endurance", "main_gun", "gun_barrels", "aircraft_count", "torpedo_tubes", "sensor", "weapon",
                    "aircraft_fit", "propulsion", "ship_type", "ship_type_fine"]
# 型・艦種ラベルの一致は制約の「個数」には数えない
HULL_TYPES = {"ship_type", "ship_type_fine"}


def order_req(req: dict[str, Any]) -> dict[str, Any]:
    out = {k: req[k] for k in REQ_KEY_ORDER if k in req}
    out.update({k: v for k, v in req.items() if k not in out})
    return out


def token_type(tok: str) -> str:
    if tok.startswith("main_gun"):
        return "main_gun"
    if tok in SENSOR_TOKENS:
        return "sensor"
    if tok in AIR_TOKENS:
        return "aircraft_fit"
    if tok in WEAPON_TOKENS:
        return "weapon"
    return "weapon"


# --------------------------------------------------------------------------------------------------
# design helpers
# --------------------------------------------------------------------------------------------------
def _isnum(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def weapon_items(design: dict[str, Any]) -> list[dict[str, Any]]:
    return [w for w in (design.get("weapons") or []) if isinstance(w, dict)] if isinstance(design, dict) else []


def _n(w: dict[str, Any], default: int = 1) -> float:
    return w["n"] if _isnum(w.get("n")) else default


def _per(w: dict[str, Any]) -> float:
    return w["per"] if _isnum(w.get("per")) and w["per"] > 0 else 1


def gun_barrels(design: dict[str, Any], cal_ge: float | None = None) -> int:
    tot = 0.0
    for w in weapon_items(design):
        if w.get("kind") != "gun":
            continue
        cal = w.get("cal")
        if cal_ge is not None and not (_isnum(cal) and cal >= cal_ge):
            continue
        tot += _n(w) * _per(w)
    return int(tot)


def aircraft_count(design: dict[str, Any]) -> int:
    return int(sum(_n(w) for w in weapon_items(design) if w.get("kind") == "aircraft_helicopter"))


def torpedo_tubes(design: dict[str, Any]) -> int:
    return int(sum(_n(w) * _per(w) for w in weapon_items(design) if w.get("kind") == "torpedo_tube"))


def kind_count(design: dict[str, Any], kind: str) -> int:
    return int(sum(_n(w) * _per(w) for w in weapon_items(design) if w.get("kind") == kind))


def has_helicopter(design: dict[str, Any]) -> bool:
    return "air:helicopter" in C.caps_of_design(design)


# --------------------------------------------------------------------------------------------------
# requirement check
# --------------------------------------------------------------------------------------------------
def _chk(typ: str, key: str, ok: bool | None, detail: str = "") -> dict[str, Any]:
    return {"type": typ, "key": key, "ok": ok, "detail": detail}


def req_checks(req: dict[str, Any], design: dict[str, Any]) -> list[dict[str, Any]]:
    """要求の各制約を個別に判定する。ok は True / False / None（判定不能: 数値項目が設計に無い）。

    数値項目（排水量・速力・乗員・航続・推進）が設計に無いときは None（判定不能。厳密集計では未充足に数える）。
    配列項目（weapons / sensors）に無い装備は False（出していない = 満たしていない）。"""
    out: list[dict[str, Any]] = []
    if not isinstance(design, dict):
        return out
    if req.get("ship_type"):
        out.append(_chk("ship_type", "ship_type", design.get("ship_type") == req["ship_type"], f"{design.get('ship_type')!r}"))
    if req.get("ship_type_fine"):
        out.append(_chk("ship_type_fine", "ship_type_fine", design.get("ship_type_fine") == req["ship_type_fine"], f"{design.get('ship_type_fine')!r}"))
    if req.get("max_displacement_t") is not None:
        basis = req.get("displacement_basis", "normal")
        val = design.get(f"displacement_{basis}_t")
        out.append(_chk("displacement", f"max_displacement_t({basis})", (val <= req["max_displacement_t"]) if _isnum(val) else None, f"{val}"))
    if req.get("min_speed_kn") is not None:
        val = design.get("speed_kn")
        out.append(_chk("speed", "min_speed_kn", (val >= req["min_speed_kn"]) if _isnum(val) else None, f"{val}"))
    if req.get("max_complement") is not None:
        val = design.get("complement")
        out.append(_chk("complement", "max_complement", (val <= req["max_complement"]) if _isnum(val) else None, f"{val}"))
    if req.get("min_endurance_nm") is not None:
        val = design.get("endurance_nm")
        out.append(_chk("endurance", "min_endurance_nm", (val >= req["min_endurance_nm"]) if _isnum(val) else None, f"{val}"))
    for tok in req.get("required") or []:
        out.append(_chk(token_type(tok), f"required:{tok}", bool(C.satisfies(tok, design))))
    if req.get("min_gun_barrels") is not None:
        cal = req.get("min_gun_barrels_cal_mm")
        n = gun_barrels(design, cal)
        out.append(_chk("gun_barrels", "min_gun_barrels", n >= req["min_gun_barrels"], f"{n}"))
    if req.get("min_aircraft") is not None:
        n = aircraft_count(design)
        out.append(_chk("aircraft_count", "min_aircraft", n >= req["min_aircraft"], f"{n}"))
    if req.get("min_torpedo_tubes") is not None:
        n = torpedo_tubes(design)
        out.append(_chk("torpedo_tubes", "min_torpedo_tubes", n >= req["min_torpedo_tubes"], f"{n}"))
    for p in req.get("required_propulsion") or []:
        props = design.get("propulsion")
        out.append(_chk("propulsion", f"required_propulsion:{p}", (p in props) if isinstance(props, list) and props else None, f"{props}"))
    return out


def all_satisfied(req: dict[str, Any], design: dict[str, Any], ignore_hull: bool = False) -> bool:
    for c in req_checks(req, design):
        if ignore_hull and c["type"] in HULL_TYPES:
            continue
        if c["ok"] is not True:
            return False
    return True


def constraint_units(req: dict[str, Any]) -> list[str]:
    """数値・装備の制約の単位（国・年・艦種・任務は数えない）。B（4〜7 個）・C の個数の定義。"""
    u: list[str] = []
    for k, name in (("max_displacement_t", "displacement"), ("min_speed_kn", "speed"), ("max_complement", "complement"), ("min_endurance_nm", "endurance"),
                    ("min_gun_barrels", "gun_barrels"), ("min_aircraft", "aircraft_count"), ("min_torpedo_tubes", "torpedo_tubes")):
        if req.get(k) is not None:
            u.append(name)
    u += [f"required:{t}" for t in req.get("required") or []]
    u += [f"required_propulsion:{p}" for p in req.get("required_propulsion") or []]
    return u


# --------------------------------------------------------------------------------------------------
# request text (English / Japanese)
# --------------------------------------------------------------------------------------------------
def _ext_need_en(req: dict[str, Any]) -> list[str]:
    need: list[str] = []
    if req.get("min_gun_barrels") is not None:
        cal = req.get("min_gun_barrels_cal_mm")
        need.append(f"at least {req['min_gun_barrels']} gun barrels" + (f" of {cal:g} mm or larger" if cal else ""))
    if req.get("min_aircraft") is not None:
        n = req["min_aircraft"]
        need.append(f"at least {n} embarked aircraft" if n != 1 else "an embarked aircraft")
    if req.get("min_torpedo_tubes") is not None:
        need.append(f"at least {req['min_torpedo_tubes']} torpedo tubes")
    if req.get("required_propulsion"):
        need.append("propulsion by " + C._en_list([p.replace("_", " ") for p in req["required_propulsion"]]))
    return need


def request_text_ext(req: dict[str, Any], variant: int = 0, premise: str | None = None, extra_phrases: list[str] | None = None) -> str:
    """C.request_text と同じ 3 文型。拡張キー（砲身数・搭載機数・魚雷管数・推進）は 'It must carry ...' の列挙に足し、extra_phrases（AESA / VLS など語彙外の語）も足す。"""
    t = req["ship_type_fine"].replace("-", " ") if req.get("ship_type_fine") else C._TYPE_NAME.get(req["ship_type"], req["ship_type"].replace("_", " "))
    art = "an" if t[0] in "aeiou" else "a"
    nation = req["country"].replace("-", " ")
    adj = C._NATION_ADJ.get(nation)
    nation_adj = f"the {adj} navy" if adj else f"the navy of {nation}"
    basis = req.get("displacement_basis", "normal")
    parts_lim = [f"displacement at most {req['max_displacement_t']} t ({basis})"]
    if req.get("min_speed_kn") is not None:
        parts_lim.append(f"speed at least {req['min_speed_kn']} kn")
    if req.get("max_complement"):
        parts_lim.append(f"crew at most {req['max_complement']}")
    if req.get("min_endurance_nm"):
        parts_lim.append(f"endurance at least {req['min_endurance_nm']} nm")
    need = [C._req_item_en(t_) for t_ in req.get("required", [])] + _ext_need_en(req) + list(extra_phrases or [])
    if req.get("required_propulsion"):  # propulsion is a design limit, not something the ship "carries"
        parts_lim.append("propulsion by " + C._en_list([p_.replace("_", " ") for p_ in req["required_propulsion"]]))
        need = [x for x in need if not x.startswith("propulsion by ")]
    mission = C._en_list([C._MISSION_EN.get(m, m) for m in req.get("mission", [])])
    lim = C._en_list(parts_lim)
    need_s = f" It must carry {C._en_list(need)}." if need else ""
    pre = (premise.strip() + " ") if premise else ""
    if variant % 3 == 0:
        s = f"{pre}Design {art} {t} for {nation_adj} around {req['year']}, for {mission}: {lim}.{need_s}"
    elif variant % 3 == 1:
        s = f"{pre}{nation_adj[0].upper() + nation_adj[1:]} wants {art} {t} designed in {req['year']}; role: {mission}. Limits: {lim}.{need_s}"
    else:
        s = f"{pre}Propose {art} {t} design ({nation}, {req['year']}) for {mission} with {lim}.{need_s}"
    return s


def request_text_ja_ext(req: dict[str, Any], premise: str | None = None, extra_phrases_ja: list[str] | None = None) -> str:
    t = C._TYPE_JA.get(req["ship_type"], req["ship_type"])
    if req.get("ship_type_fine"):
        t = f"{t}（{req['ship_type_fine']}）"
    nation = C._COUNTRY_JA.get(req["country"], req["country"].replace("-", " "))
    basis = {"normal": "常備", "full": "満載", "standard": "基準"}.get(req.get("displacement_basis", "normal"), "")
    parts = [f"{basis}排水量{req['max_displacement_t']}t以下"]
    if req.get("min_speed_kn") is not None:
        parts.append(f"速力{req['min_speed_kn']}kn以上")
    if req.get("max_complement"):
        parts.append(f"乗員{req['max_complement']}名以下")
    if req.get("min_endurance_nm"):
        parts.append(f"航続{req['min_endurance_nm']}nm以上")
    need = [C._req_item_ja(x) for x in req.get("required", [])]
    if req.get("min_gun_barrels") is not None:
        cal = req.get("min_gun_barrels_cal_mm")
        need.append((f"口径{cal:g}mm以上の" if cal else "") + f"砲{req['min_gun_barrels']}門以上")
    if req.get("min_aircraft") is not None:
        need.append(f"搭載機{req['min_aircraft']}機以上")
    if req.get("min_torpedo_tubes") is not None:
        need.append(f"魚雷発射管{req['min_torpedo_tubes']}門以上")
    if req.get("required_propulsion"):
        need.append("推進: " + "・".join(req["required_propulsion"]))
    need += list(extra_phrases_ja or [])
    mission = "・".join(C._MISSION_JA.get(m, m) for m in req.get("mission", []))
    pre = (premise + " ") if premise else ""
    return f"{pre}{req['year']}年ごろの{nation}海軍の{t}を設計してください。任務: {mission}。" + "、".join(parts) + "。" + ("必須装備: " + "、".join(need) + "。" if need else "")


def retrieval_req(req: dict[str, Any]) -> dict[str, Any]:
    """近傍検索（NB-1、v0 の ``req_view``）に渡す要求。拡張キーは v0 の ``required`` 語彙に写す（検索は要求に書いてある項目だけを使う）。"""
    r = {k: req[k] for k in BASE_KEYS if k in req}
    r["required"] = list(req.get("required") or [])
    if req.get("min_aircraft") and "helicopter" not in r["required"] and "aircraft" not in r["required"]:
        r["required"].append("helicopter")
    if req.get("min_torpedo_tubes") and "torpedo_tubes" not in r["required"]:
        r["required"].append("torpedo_tubes")
    if req.get("min_gun_barrels_cal_mm") and not any(t.startswith("main_gun") for t in r["required"]):
        g = max((x for x in C.GUN_GRID if x <= req["min_gun_barrels_cal_mm"]), default=None)
        if g:
            r["required"].append(f"main_gun>={g}mm")
    return r


# --------------------------------------------------------------------------------------------------
# pair-test metrics
# --------------------------------------------------------------------------------------------------
MODERN_KEYS = ["ssm", "sam", "ciws", "air:helicopter", "radar_any", "sonar", "esm_ecm", "prop:gas_turbine", "prop:nuclear", "air_search_radar", "fire_control_radar"]


def modern_count(design: dict[str, Any]) -> int:
    caps = C.caps_of_design(design)
    return sum(1 for k in MODERN_KEYS if k in caps)


def design_metrics(design: dict[str, Any], req: dict[str, Any] | None = None) -> dict[str, float | bool | None]:
    """ペアテストの指標。displacement は req の基準（常備 / 満載 / 基準）、無ければ常備 > 基準 > 満載。"""
    req = req or {}
    basis = req.get("displacement_basis")
    disp = design.get(f"displacement_{basis}_t") if basis else None
    if not _isnum(disp):
        disp, _ = C.ref_displacement(design)
    caps = C.caps_of_design(design)
    pw = design.get("power_hp")
    gw = [w.get("cal") for w in weapon_items(design) if w.get("kind") == "gun" and _isnum(w.get("cal"))]
    return {
        "displacement": disp if _isnum(disp) else None,
        "speed": design.get("speed_kn") if _isnum(design.get("speed_kn")) else None,
        "power_per_disp": (pw / disp) if _isnum(pw) and _isnum(disp) and disp > 0 else None,
        "gun_barrels": float(gun_barrels(design, req.get("min_gun_barrels_cal_mm"))),
        "main_gun_cal": float(max(gw)) if gw else 0.0,
        "endurance": design.get("endurance_nm") if _isnum(design.get("endurance_nm")) else None,
        "complement": design.get("complement") if _isnum(design.get("complement")) else None,
        "torpedo_tubes": float(torpedo_tubes(design)),
        "modern_count": float(modern_count(design)),
        "helicopter": bool(has_helicopter(design) or aircraft_count(design) > 0 and "air:seaplane" not in caps),
        "aviation": "aircraft" in caps,
        "sam": "sam" in caps,
        "missiles": bool({"ssm", "sam", "ballistic_missile"} & caps),
    }


def eval_direction(exp: dict[str, Any], mA: dict[str, Any], mB: dict[str, Any]) -> bool | None:
    """期待方向 1 項目の判定。op: increase / decrease（B が A より厳密に大 / 小）、present_B / absent_B（B にある / 無い）。値が無ければ None。"""
    m, op = exp["metric"], exp["op"]
    if op in ("present_B", "absent_B"):
        b = mB.get(m)
        if b is None:
            return None
        return bool(b) == (op == "present_B")
    a, b = mA.get(m), mB.get(m)
    if a is None or b is None:
        return None
    if op == "increase":
        return b > a
    if op == "decrease":
        return b < a
    raise ValueError(op)
