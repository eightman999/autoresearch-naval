"""design-v1 の repair エピソード（validator のフィードバックで設計を直す）と、その違反生成・自動修正ループ。

エピソード: user 要求 -> assistant 初期設計（status = feasible を名乗るが、意図的に違反を含む）-> user「validator: valid=false, violations=[...]」->
assistant 修正版（要求を維持して validator に合格）。学習対象（metadata.train_turns）は修正版のターンだけで、初期設計のターンには損失を取らない。

初期設計は、正解設計（実艦）を決定論的に壊して作る（違反の種類は KINDS の 10 種: 速力と出力の不整合、排水量に対し兵装過多、艦載機と艦サイズの不整合、
時代不相応な兵装、時代不相応なセンサー、砲門数不足、魚雷管数不足、搭載機数不足、推進と年代の不整合、要求未充足）。壊した結果を check_design にかけ、
狙った種類の違反が実際に検出されるまで強さを上げて作り直す（検出されなければその例は捨てる）。user に見せる violations は check_design の実出力
（code / field / detail。detail は 160 字で切る）。

修正版は `auto_repair`: validator の違反を 1 つずつ、違反の種類ごとの規則（例: 速力と出力の不整合 = 層の回帰から求めた Admiralty 係数で出力を再計算、
兵装過多 = 要求に必要でない小口径砲から減らす、時代外の装備 = 外す、砲門数不足 = 主砲の砲塔を増やして重量を排水量へ反映 ...）で直し、
check_design を再実行して合格するまで繰り返す（最大 12 回）。合格しなければ正解設計（実艦）を修正版にする（metadata の repair_method = gold_fallback）。
2 ラウンドの例（約 8%）は、最初の修正で違反の一部だけを直し（違反を減らし新しい違反は作らない。学習対象外）、validator が残りを指摘してから最終版を出す。
「元の要求が不可能」の例（約 9%）は、infeasible 生成器の不可能な要求に対して、要求を黙って満たさない設計を出す -> validator が req 違反を返す ->
relaxed / infeasible に遷移する。
"""

from __future__ import annotations

import math
import random
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402
import shipknow_design_v1_data as DD  # noqa: E402
import shipknow_design_v1_gen_basic as G  # noqa: E402
import shipknow_design_v1_infeasible as I  # noqa: E402
import shipknow_design_v1_ops as O  # noqa: E402

KINDS = ["speed_power", "armament_excess", "aircraft_size", "era_weapon", "era_sensor", "gun_barrels_short", "torpedo_tubes_short", "aircraft_count_short", "propulsion_era", "req_unmet"]
KIND_WEIGHT = {"speed_power": 14, "armament_excess": 12, "aircraft_size": 10, "era_weapon": 9, "era_sensor": 8, "gun_barrels_short": 11, "torpedo_tubes_short": 9, "aircraft_count_short": 9,
               "propulsion_era": 6, "req_unmet": 12}
KIND_PRED = {"speed_power": "any", "armament_excess": "any", "aircraft_size": "small_postwar", "era_weapon": "old", "era_sensor": "older", "gun_barrels_short": "barrels", "torpedo_tubes_short": "tubes",
             "aircraft_count_short": "aircraft", "propulsion_era": "prop", "req_unmet": "any"}
KIND_FORCE = {"gun_barrels_short": "barrels", "torpedo_tubes_short": "tubes", "aircraft_count_short": "aircraft"}
RADAR_CAPS = {"radar_any", "radar", "air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar"}
SENSOR_CAPS = RADAR_CAPS | {"esm_ecm", "sonar", "eo_director", "combat_system", "decoy"}
HELDOUT_KIND_PAIR_MOD = 6  # 違反の種類の組み合わせ（2 種）の hash の 1/6 は test 専用
INSTRUCTION = "Fix the design. Keep the original request as far as possible."
MAX_VIOL_SHOWN = 6
DETAIL_CHARS = 160


def air_kind_for_year(year: int) -> str:
    """その年に実在した搭載機の種類（air の語彙）。ヘリの初出年（1955）の前は水上機（1922 以降）か一般の航空機。"""
    return "helicopter" if year >= 1950 else ("seaplane" if year >= 1922 else "aircraft")


def _is_target(kind: str, v: dict[str, str]) -> bool:
    c, f = v["code"], v["field"]
    if kind == "speed_power":
        return c in ("ratio_high", "ratio_low") and f in ("adm", "fr", "hp_t")
    if kind == "armament_excess":
        return c in ("armament_excess", "gun_too_big_for_displacement", "gun_too_big_for_type") or (c == "weapon_count_excess" and f != "aircraft") or (c == "type_equipment_mismatch" and f == "weapons")
    if kind == "aircraft_size":
        return c == "heli_hull_too_short" or (c == "weapon_count_excess" and f == "aircraft") or (c == "type_equipment_mismatch" and f == "air")
    if kind == "era_weapon":
        return c == "era_early" and f in ("sam", "ssm", "ciws", "ballistic_missile", "rocket_launcher")
    if kind == "era_sensor":
        return c == "era_early" and f in SENSOR_CAPS
    if kind == "gun_barrels_short":
        return c == "req_gun_barrels_short"
    if kind == "torpedo_tubes_short":
        return c == "req_torpedo_tubes_short"
    if kind == "aircraft_count_short":
        return c == "req_aircraft_short"
    if kind == "propulsion_era":
        return (c == "era_early" and f.startswith("prop:")) or c == "req_propulsion_missing"
    if kind == "req_unmet":
        return c.startswith("req_") and c not in ("req_gun_barrels_short", "req_torpedo_tubes_short", "req_aircraft_short", "req_propulsion_missing")
    return False


def heldout_kinds(kinds: list[str]) -> bool:
    import hashlib

    if len(kinds) < 2:
        return False
    key = "+".join(sorted(kinds))
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big") % HELDOUT_KIND_PAIR_MOD == 0


# --------------------------------------------------------------------------------------------------
# corruptions (gold design -> design with a violation of the wanted kind)
# --------------------------------------------------------------------------------------------------
def _set_barrels_below(d: dict[str, Any], cal_ge: float | None, target: int) -> None:
    """cal_ge 以上の砲の砲身数の合計を target 以下にする（口径の小さい砲から削る）。"""
    guns = sorted((w for w in d.get("weapons") or [] if w.get("kind") == "gun" and (cal_ge is None or (K._isnum(w.get("cal")) and w["cal"] >= cal_ge))), key=lambda w: (w.get("cal") or 0))
    total = X.gun_barrels(d, cal_ge)
    for w in guns:
        while total > target and X._n(w) > 0:
            w["n"] = int(X._n(w) - 1)
            total -= X._per(w)
        if total <= target:
            break
    d["weapons"] = [w for w in d["weapons"] if not (w.get("kind") == "gun" and X._n(w) <= 0)]


def corrupt(kind: str, req: dict[str, Any], d: dict[str, Any], rng: random.Random, attempt: int, year: int) -> dict[str, Any] | None:
    d2 = O.clone(d)
    esc = 1.0 + 0.3 * attempt
    ws = O.weapons_list(d2)
    if kind == "speed_power":
        v, P = d.get("speed_kn"), d.get("power_hp")
        if not (K._isnum(v) and K._isnum(P)):
            return None
        f = rng.uniform(1.5, 1.9) * esc
        if rng.random() < 0.5 and v * f <= 70 and req.get("max_speed_kn") is None:
            d2["speed_kn"] = O.rnd("speed_kn", v * f)
        else:
            d2["power_hp"] = O.rnd("power_hp", P / f ** 2)
        return d2
    if kind == "armament_excess":
        g = O.main_gun_item(d2)
        mode = rng.choice(["mounts", "mounts", "heavy", "launchers", "launchers"])
        if mode == "launchers":
            opts = [k for k, cap in (("ssm", "ssm"), ("sam", "sam"), ("torpedo_tube", "torpedo_tube")) if K.first_year(cap) is None or year >= K.first_year(cap) + 3 or cap == "torpedo_tube"]
            if not opts:
                mode = "mounts"
            else:
                k = rng.choice(opts)
                if k == "torpedo_tube":
                    ws.append({"kind": "torpedo_tube", "n": int(8 * esc), "per": 3, "cal": 533})
                elif k == "ssm":
                    ws.append({"kind": "ssm", "n": int(16 * esc)})
                else:
                    ws.append({"kind": "sam", "n": int(8 * esc), "per": 4})
        if mode == "heavy" and g is not None:
            cands = [x for x in C.GUN_GRID if x >= 1.5 * g["cal"]]
            if cands:
                ws.append({"kind": "gun", "n": 3, "per": 2, "cal": cands[min(len(cands) - 1, attempt)]})
            else:
                mode = "mounts"
        if mode == "mounts":
            if g is None:
                return None
            g["n"] = int(max(g.get("n", 1) * rng.uniform(2.5, 4.0) * esc, g.get("n", 1) + 3))
        O.sort_weapons(d2)
        return d2
    if kind == "aircraft_size":
        if "helicopter" in (d.get("air") or []) or X.aircraft_count(d) > 0:
            return None
        O.set_aircraft(d2, int(rng.choice([3, 4, 5, 6]) * esc), air_kind_for_year(year))
        return d2
    if kind == "era_weapon":
        opts = [(k, c) for k, c in (("sam", "sam"), ("ssm", "ssm"), ("ciws", "ciws")) if K.cap_early_by(c, year) >= 7 and not any(w.get("kind") == k for w in ws)]
        if not opts:
            return None
        k, _c = rng.choice(opts)
        ws.append({"kind": k, "n": 4 if k == "ssm" else (1 if k == "ciws" else 2), **({"per": 2} if k == "sam" else {})})
        O.sort_weapons(d2)
        return d2
    if kind == "era_sensor":
        have = set(d.get("sensors") or [])
        opts = [s for s in ("air_search_radar", "fire_control_radar", "surface_radar", "esm_ecm", "sonar", "radar", "eo_director", "combat_system") if s not in have
                and K.cap_early_by("radar_any" if s == "radar" else s, year) >= 7]
        if not opts:
            return None
        add = rng.sample(opts, min(len(opts), 1 + (rng.random() < 0.4)))
        d2["sensors"] = sorted(have | set(add))
        return d2
    if kind == "gun_barrels_short":
        k = req.get("min_gun_barrels")
        if k is None:
            return None
        cal = req.get("min_gun_barrels_cal_mm")
        _set_barrels_below(d2, cal, max(0, k - rng.choice([1, 1, 2, k])) if k > 1 else 0)
        return d2
    if kind == "torpedo_tubes_short":
        k = req.get("min_torpedo_tubes")
        if k is None:
            return None
        target = max(0, k - rng.choice([1, 1, 2, k]))
        tubes = [w for w in ws if w.get("kind") == "torpedo_tube"]
        total = X.torpedo_tubes(d2)
        for w in sorted(tubes, key=lambda w: X._n(w)):
            while total > target and X._n(w) > 0:
                w["n"] = int(X._n(w) - 1)
                total -= X._per(w)
        d2["weapons"] = [w for w in ws if not (w.get("kind") == "torpedo_tube" and X._n(w) <= 0)]
        if X.torpedo_tubes(d2) >= k:
            return None
        return d2
    if kind == "aircraft_count_short":
        k = req.get("min_aircraft")
        if k is None:
            return None
        O.set_aircraft(d2, max(0, k - rng.choice([1, 1, 2, k])), air_kind_for_year(year))
        return d2
    if kind == "propulsion_era":
        rp = req.get("required_propulsion")
        if rp and rng.random() < 0.6:
            alts = [p for p in ("diesel", "steam_turbine", "steam_reciprocating", "gas_turbine") if p not in rp and K.cap_early_by(f"prop:{p}", year) <= 0]
            if not alts:
                return None
            d2["propulsion"] = [rng.choice(alts)]
            return d2
        opts = [p for p in ("gas_turbine", "nuclear") if K.cap_early_by(f"prop:{p}", year) >= 7]
        if not opts or rp:
            return None
        d2["propulsion"] = [rng.choice(opts)]
        return d2
    if kind == "req_unmet":
        basis = req.get("displacement_basis", "normal")
        opts = ["disp"]
        if req.get("min_speed_kn") is not None:
            opts.append("speed")
        if req.get("max_complement") is not None:
            opts.append("crew")
        if req.get("min_endurance_nm") is not None:
            opts.append("endurance")
        toks = [t for t in req.get("required") or [] if t not in ("air_search_radar",)]
        if toks:
            opts += ["equip", "equip"]
        o = rng.choice(opts)
        if o == "disp":
            f = rng.uniform(1.25, 1.6) * esc
            for fld in O.DISP_KEYS:
                if K._isnum(d2.get(fld)):
                    d2[fld] = O.rnd(fld, d2[fld] * f)
        elif o == "speed":
            d2["speed_kn"] = O.rnd("speed_kn", max(3.0, req["min_speed_kn"] - rng.uniform(3, 8) * esc))
        elif o == "crew":
            d2["complement"] = O.rnd("complement", req["max_complement"] * rng.uniform(1.3, 2.5))
        elif o == "endurance":
            d2["endurance_nm"] = O.rnd("endurance_nm", req["min_endurance_nm"] * rng.uniform(0.3, 0.7))
        else:
            tok = rng.choice(toks)
            if not drop_token(d2, tok):
                return None
        return d2
    return None


def drop_token(d: dict[str, Any], tok: str) -> bool:
    """必須装備 tok を設計から外す（外せたら True）。"""
    ws = d.get("weapons") or []
    sens = d.get("sensors") or []
    before = (len(ws), list(sens), d.get("air"))
    if tok == "torpedo_tubes":
        d["weapons"] = [w for w in ws if w["kind"] != "torpedo_tube"]
    elif tok == "asw_weapon":
        d["weapons"] = [w for w in ws if w["kind"] not in C.ASW_KINDS]
    elif tok in ("ssm", "sam", "ciws", "ballistic_missile", "rocket_launcher"):
        d["weapons"] = [w for w in ws if w["kind"] != tok]
    elif tok == "sonar":
        d["sensors"] = [s for s in sens if s != "sonar"]
    elif tok in ("helicopter", "aircraft"):
        O.set_aircraft(d, 0)
    elif tok == "mines":
        d["weapons"] = [w for w in ws if w["kind"] != "mine"]
    elif tok == "minesweeping":
        d["weapons"] = [w for w in ws if w["kind"] != "minesweeping_gear"]
    elif tok.startswith("main_gun"):
        d["weapons"] = [w for w in ws if w["kind"] != "gun"]
    elif tok in ("air_search_radar", "fire_control_radar", "surface_radar", "esm_ecm"):
        d["sensors"] = [s for s in sens if s != tok]
    elif tok == "radar":
        d["sensors"] = [s for s in sens if s not in C.RADAR_SENSORS]
    else:
        return False
    return (len(d.get("weapons") or []), list(d.get("sensors") or []), d.get("air")) != before


# --------------------------------------------------------------------------------------------------
# fixers (violation -> repaired design). Each returns a new design or None.
# --------------------------------------------------------------------------------------------------
def _pred(th: dict[str, Any], metric: str, d: dict[str, Any], year: int) -> float | None:
    return O._pred_log(th, metric, d["ship_type"], C.era_bin(year), d)


def typical_power(d: dict[str, Any], year: int, th: dict[str, Any]) -> dict[str, Any]:
    """出力を、層の回帰が予測する Admiralty 係数（残差 0）から再計算する。"""
    d2 = O.clone(d)
    D, V_ = O.ref_disp(d2), d2.get("speed_kn")
    p = _pred(th, "adm", d2, year)
    if p is None or not K._isnum(V_) or not D:
        return d2
    d2["power_hp"] = O.rnd("power_hp", D ** (2 / 3) * V_ ** 3 / math.exp(p))
    return d2


def _vset(res: dict[str, Any]) -> set[tuple[str, str]]:
    return {(v["code"], v["field"]) for v in res["violations"]}


def fix_speed_power(req: dict[str, Any], d: dict[str, Any], year: int, th: dict[str, Any], check) -> dict[str, Any] | None:
    """速力と出力の不整合: 速力を出力と整合する値へ（要求の最低 / 最高速力の範囲内なら）、そうでなければ出力を速力に合わせる（それでも足りなければ全長・全幅・喫水を
    回帰の予測値へ）。候補は、狙った違反が消え、かつ元の設計に無かった新しい違反（範囲外の全長など）を作らないものだけを採る。"""
    base = _vset(check(d))
    cands = []
    D, V_, P = O.ref_disp(d), d.get("speed_kn"), d.get("power_hp")
    if K._isnum(V_) and K._isnum(P) and D:
        v = V_
        for _ in range(5):  # adm depends on V/sqrt(L): iterate to the consistent speed
            probe = {**d, "speed_kn": v}
            p = _pred(th, "adm", probe, year)
            if p is None:
                break
            v = (math.exp(p) * P / D ** (2 / 3)) ** (1 / 3)
        else:
            vmin, vmax = req.get("min_speed_kn") or 0, req.get("max_speed_kn") or 1e9
            if vmin <= v <= vmax:
                d2 = O.clone(d)
                d2["speed_kn"] = O.rnd("speed_kn", max(vmin, v))
                cands.append(d2)
    cands.append(typical_power(d, year, th))
    for stretch in (1.05, 1.1, 1.2):
        cands.append(O.rederive(typical_power(d, year, th), 1.0, d.get("speed_kn"), year, stretch=stretch))
    chain = typical_power(d, year, th)
    for metric in ("fr", "lb", "cb_normal", "cb_standard", "cb_full"):
        nxt = snap_ratio(chain, metric, year, th) if metric in C.ratios_of(chain) else None
        chain = nxt or chain
    cands.append(chain)
    for c in cands:
        res = _vset(check(c))
        if not any(_is_target("speed_power", {"code": cd, "field": f, "detail": ""}) for cd, f in res) and not (res - base):
            return c
    return None


def snap_ratio(d: dict[str, Any], metric: str, year: int, th: dict[str, Any]) -> dict[str, Any] | None:
    """寸法・乗員の比が範囲外のとき、その比を層の回帰の予測値（残差 0）にする。"""
    p = _pred(th, metric, d, year)
    if p is None:
        return None
    d2 = O.clone(d)
    r = math.exp(p)
    D = O.ref_disp(d2)
    L, B_, T = d2.get("length_m"), d2.get("beam_m"), d2.get("draught_m")
    if metric == "lb" and K._isnum(L):
        d2["beam_m"] = L / r
    elif metric == "bt" and K._isnum(B_):
        d2["draught_m"] = B_ / r
    elif metric.startswith("cb_") and K._isnum(L) and K._isnum(B_):
        Db = d2.get(f"displacement_{metric.split('_')[1]}_t")
        d2["draught_m"] = Db / (r * L * B_)
    elif metric == "slender" and D:
        d2["length_m"] = r * D ** (1 / 3)
    elif metric == "crew_t" and D:
        d2["complement"] = r * D
    elif metric == "full_normal" and K._isnum(d2.get("displacement_normal_t")):
        d2["displacement_full_t"] = d2["displacement_normal_t"] * r
    elif metric == "fr" and K._isnum(d2.get("speed_kn")):
        d2["length_m"] = (d2["speed_kn"] / r) ** 2
    elif metric == "hp_t" and D:
        d2["power_hp"] = r * D
    else:
        return None
    return O.normalize(d2)


def remove_cap(d: dict[str, Any], cap: str, req: dict[str, Any], year: int) -> dict[str, Any] | None:
    d2 = O.clone(d)
    ws, sens = d2.get("weapons") or [], list(d2.get("sensors") or [])
    if cap in ("sam", "ssm", "ciws", "ballistic_missile", "rocket_launcher", "asw_rocket", "depth_charge"):
        d2["weapons"] = [w for w in ws if w["kind"] != cap]
    elif cap == "asw_weapon":
        d2["weapons"] = [w for w in ws if w["kind"] not in C.ASW_KINDS]
    elif cap in RADAR_CAPS:
        keep = [] if cap == "radar_any" else [s for s in sens if s != cap]
        d2["sensors"] = keep if cap != "radar_any" else [s for s in sens if s not in C.RADAR_SENSORS]
    elif cap in ("esm_ecm", "sonar", "eo_director", "combat_system", "decoy"):
        d2["sensors"] = [s for s in sens if s != cap]
    elif cap.startswith("prop:"):
        for p in ("steam_turbine", "steam_reciprocating", "diesel", "petrol_engine", "electric_motor"):
            if p in (req.get("required_propulsion") or []) or K.cap_early_by(f"prop:{p}", year) > 0:
                continue
            d2["propulsion"] = [p]
            break
        else:
            return None
    elif cap in ("aircraft", "air:helicopter", "air:seaplane"):
        keep = max(req.get("min_aircraft") or 0, 1 if "helicopter" in (req.get("required") or []) else 0)
        if cap == "air:helicopter" and keep == 0:
            O.set_aircraft(d2, 0)
        else:
            return None
    else:
        return None
    if not d2.get("sensors") and "sensors" in d2:
        d2["sensors"] = []
    return O.normalize(d2)


def add_token(d: dict[str, Any], tok: str, year: int) -> dict[str, Any] | None:
    d2 = O.clone(d)
    ws = O.weapons_list(d2)
    sens = set(d2.get("sensors") or [])
    if tok == "torpedo_tubes":
        ws.append({"kind": "torpedo_tube", "n": 2, "per": 2, "cal": 450 if year < 1920 else 533})
    elif tok == "asw_weapon":
        ws.append({"kind": "depth_charge", "n": 2})
    elif tok in ("ssm", "sam", "ciws"):
        O.add_launcher(d2, tok)
    elif tok == "rocket_launcher":
        ws.append({"kind": "rocket_launcher", "n": 1, "per": 12, "cal": 127})
    elif tok == "sonar":
        sens.add("sonar")
    elif tok in ("air_search_radar", "fire_control_radar", "surface_radar", "esm_ecm"):
        sens.add(tok)
    elif tok == "radar":
        sens.add("radar")
    elif tok in ("helicopter", "aircraft"):
        O.set_aircraft(d2, 1, air_kind_for_year(year))
    elif tok == "mines":
        ws.append({"kind": "mine", "n": 20})
    elif tok == "minesweeping":
        ws.append({"kind": "minesweeping_gear"})
    elif tok.startswith("main_gun>="):
        cal = float(tok.split(">=")[1].rstrip("mm"))
        g = O.main_gun_item(d2)
        if g is None:
            ws.append({"kind": "gun", "n": 1, "cal": O.num_cal(cal)})
        else:
            O.set_main_caliber(d2, cal)
    else:
        return None
    if sens:
        d2["sensors"] = sorted(sens)
    return O.normalize(d2)


def fix_one(D: DD.Data, req: dict[str, Any], d: dict[str, Any], v: dict[str, str], year: int, check) -> dict[str, Any] | None:
    code, field = v["code"], v["field"]
    th = D.th
    basis = req.get("displacement_basis", "normal")
    if code == "req_displacement_exceeds":
        val = O.disp_in_basis(d, basis)
        k = req["max_displacement_t"] * 0.97 / val
        return O.rederive(O.shrink_weapons(d, k, req), k, None, year)
    if code == "req_displacement_below":
        val = O.disp_in_basis(d, basis)
        return O.rederive(d, req["min_displacement_t"] * 1.03 / val, None, year)
    if code == "req_speed_below":
        v2 = math.ceil((req["min_speed_kn"] + 0.3) * 2) / 2
        return typical_power({**d, "speed_kn": O.rnd("speed_kn", v2)}, year, th)
    if code == "req_speed_exceeds":
        v2 = math.floor((req["max_speed_kn"] - 0.3) * 2) / 2
        return typical_power({**d, "speed_kn": O.rnd("speed_kn", v2)}, year, th)
    if code == "req_complement_exceeds":
        return {**d, "complement": O.rnd("complement", req["max_complement"] * 0.97)}
    if code == "req_endurance_below":
        return {**d, "endurance_nm": O.rnd("endurance_nm", req["min_endurance_nm"] * 1.06 + 50)}
    if code == "req_equipment_missing":
        return add_token(d, field, year)
    if code == "req_gun_barrels_short":
        cal = req.get("min_gun_barrels_cal_mm")
        d2 = O.clone(d)
        deficit = req["min_gun_barrels"] - X.gun_barrels(d2, cal)
        g = O.main_gun_item(d2)
        if g is not None and (cal is None or g["cal"] >= cal):
            dW = O.add_barrels(d2, deficit, cal if cal and any(w.get("cal") == cal for w in d2["weapons"] if w.get("kind") == "gun") else None)
        else:  # no gun of the filter caliber is left (the corruption removed them): add a new gun item
            tok_cal = max((float(t.split(">=")[1].rstrip("mm")) for t in req.get("required") or [] if t.startswith("main_gun>=")), default=None)
            c_new = cal or tok_cal or 40
            per = 2 if deficit % 2 == 0 and c_new >= 40 else 1
            d2.setdefault("weapons", []).append({"kind": "gun", "n": deficit // per, **({"per": per} if per > 1 else {}), "cal": O.num_cal(c_new)})
            dW = O.gun_weight_t(c_new, deficit)
        if X.gun_barrels(d2, cal) < req["min_gun_barrels"]:
            return None
        return O.scale_weight(d2, dW, None, year, crew_extra=3 * deficit)
    if code == "req_torpedo_tubes_short":
        d2 = O.clone(d)
        deficit = req["min_torpedo_tubes"] - X.torpedo_tubes(d2)
        tubes = [w for w in O.weapons_list(d2) if w.get("kind") == "torpedo_tube"]
        if tubes:
            w = max(tubes, key=lambda w: X._n(w))
            w["n"] = int(X._n(w) + max(1, math.ceil(deficit / X._per(w))))
        else:
            d2["weapons"].append({"kind": "torpedo_tube", "n": max(1, math.ceil(deficit / 2)), "per": 2, "cal": 450 if year < 1920 else 533})
        return O.scale_weight(d2, 6.0 * deficit, None, year, crew_extra=2 * deficit)
    if code == "req_aircraft_short":
        d2 = O.clone(d)
        dW = O.set_aircraft(d2, req["min_aircraft"], air_kind_for_year(year))
        return O.scale_weight(d2, max(dW, 0.0), None, year, crew_extra=6)
    if code == "req_propulsion_missing":
        d2 = O.clone(d)
        O.set_propulsion(d2, field.split(":", 1)[1])
        return O.normalize(d2)
    if code == "era_early":
        return remove_cap(d, field, req, year)
    if code in ("ratio_high", "ratio_low"):
        if field in ("adm", "fr", "hp_t"):
            return fix_speed_power(req, d, year, th, check)
        return snap_ratio(d, field, year, th)
    if code == "heli_hull_too_short" or (code == "weapon_count_excess" and field == "aircraft") or (code == "type_equipment_mismatch" and field == "air"):
        keep = max(req.get("min_aircraft") or 0, 1 if "helicopter" in (req.get("required") or []) else 0)
        if keep:
            return None
        d2 = O.clone(d)
        O.set_aircraft(d2, 0)
        return O.normalize(d2)
    if code == "weapon_count_excess":
        kind = {"ssm": "ssm", "sam": "sam", "ciws": "ciws", "torpedo_tube": "torpedo_tube"}.get(field)
        d2 = O.clone(d)
        for w in d2.get("weapons") or []:
            if w.get("kind") == kind and X._n(w) > 1:
                trial = O.clone(d2)
                tw = next(x for x in trial["weapons"] if x == w)
                tw["n"] = int(max(1, math.ceil(X._n(tw) / 2)))
                if O.equip_ok(req, trial):
                    d2 = trial
        return O.normalize(d2) if d2 != d else None
    if code in ("armament_excess", "gun_too_big_for_displacement", "gun_too_big_for_type") or (code == "type_equipment_mismatch" and field == "weapons"):
        d2 = O.clone(d)
        for _ in range(30):
            res = check(d2)
            if not any(_is_target("armament_excess", x) for x in res["violations"]):
                return O.normalize(d2)
            guns = sorted((w for w in d2.get("weapons") or [] if w.get("kind") == "gun" and K._isnum(w.get("cal"))), key=lambda w: (w["cal"], -X._n(w)))
            if code != "armament_excess":
                g = O.main_gun_item(d2)
                if g is None:
                    return None
                cap = 203 if code == "type_equipment_mismatch" else (D.th.get("type_gun_max_mm", {}).get(d["ship_type"]) or 1000)
                O.set_main_caliber(d2, min(g["cal"], cap / 1.3 if code != "type_equipment_mismatch" else cap), barrels=O.barrels_of(g))
                continue
            done = False
            for w in guns:
                trial = O.clone(d2)
                tw = next(x for x in trial["weapons"] if x == w)
                if X._n(tw) > 1:
                    tw["n"] = int(X._n(tw) - 1)
                else:
                    trial["weapons"].remove(tw)
                if O.equip_ok(req, trial):
                    d2, done = trial, True
                    break
            if not done:
                return None
        return None
    return None


PRIORITY = ["schema", "req_", "era_early", "type_", "heli", "weapon_count", "armament", "gun_", "ratio_"]


def _prio(v: dict[str, str]) -> int:
    c = v["code"]
    for i, p in enumerate(PRIORITY):
        if c.startswith(p):
            return i
    return len(PRIORITY)


def auto_repair(D: DD.Data, req: dict[str, Any], bad: dict[str, Any], gold: dict[str, Any], year: int, max_iter: int = 12,
                stop_after: int | None = None) -> tuple[dict[str, Any], str, list[str]]:
    """違反を 1 つずつ直して check_design が合格するまで繰り返す。戻り値 (設計, 'rule' | 'gold_fallback' | 'partial', 直した違反の列)。
    stop_after = n なら、n 回直した時点の設計を返す（2 ラウンドの 1 回目の修正用。method は 'partial'）。"""
    check = lambda x: K.check_design(req, x, th=D.th)  # noqa: E731
    cur = bad
    trace: list[str] = []
    for _ in range(max_iter):
        res = check(cur)
        if res["ok"]:
            return cur, "rule", trace
        if stop_after is not None and len(trace) >= stop_after:
            return cur, "partial", trace
        fixed, used = None, None
        for v in sorted(res["violations"], key=_prio):
            try:
                fixed = fix_one(D, req, cur, v, year, check)
            except (KeyError, ValueError, ZeroDivisionError, StopIteration):
                fixed = None
            if fixed is not None and fixed != cur:
                used = v
                break
        if fixed is None or used is None:
            break
        cur = O.normalize(fixed)
        trace.append(f"{used['code']}:{used['field']}")
    return gold, "gold_fallback", trace


# --------------------------------------------------------------------------------------------------
# episode
# --------------------------------------------------------------------------------------------------
def violations_for_user(res: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for v in sorted(res["violations"], key=_prio)[:MAX_VIOL_SHOWN]:
        out.append({"code": v["code"], "field": v["field"], "detail": re.sub(r"\s+", " ", v["detail"])[:DETAIL_CHARS]})
    return out


def feedback_turn(viols: list[dict[str, str]]) -> str:
    return K.jdump({"validator": {"valid": False, "violations": viols}, "instruction": INSTRUCTION})


def pick_kinds(rng: random.Random, split: str) -> list[str]:
    n = rng.choices([1, 2, 3], weights=[0.62, 0.28, 0.10])[0]
    for _ in range(8):
        kinds = []
        pool = list(KINDS)
        while pool and len(kinds) < n:
            k = rng.choices(pool, weights=[KIND_WEIGHT[x] for x in pool])[0]
            pool.remove(k)
            kinds.append(k)
        # incompatible pairs
        s = set(kinds)
        if ("aircraft_size" in s and "aircraft_count_short" in s) or ("gun_barrels_short" in s and "armament_excess" in s) or ("era_weapon" in s and "armament_excess" in s) or \
                ("speed_power" in s and "req_unmet" in s):
            continue
        ho = heldout_kinds(kinds)
        if split == "test":
            if len(kinds) < 2 or ho or rng.random() < 0.5:  # test favours the test-only kind pairs (half of the 2+ kind episodes)
                return kinds
            continue
        if not ho:
            return kinds
    return kinds[:1]


def build_bad(D: DD.Data, req: dict[str, Any], gold: dict[str, Any], kinds: list[str], rng: random.Random, year: int) -> tuple[dict[str, Any], dict[str, Any], list[str]] | None:
    """gold を kinds の順に壊し、各 kind の違反が検出されるまで強さを上げる。戻り値 (bad, check 結果, 実際に入った kinds)。"""
    cur = gold
    done: list[str] = []
    for kind in kinds:
        ok = False
        for attempt in range(4):
            cand = corrupt(kind, req, cur, rng, attempt, year)
            if cand is None:
                break
            cand = O.normalize(cand) if kind not in ("speed_power",) else K.order_design(cand)
            res = K.check_design(req, cand, th=D.th)
            if any(_is_target(kind, v) for v in res["violations"]):
                cur, ok = cand, True
                break
        if ok:
            done.append(kind)
        elif kind == kinds[0]:
            return None
    if not done:
        return None
    res = K.check_design(req, cur, th=D.th)
    if res["ok"]:
        return None
    return cur, res, done


def gen_repair(D: DD.Data, split: str, idx: int) -> dict[str, Any] | None:
    rng = K.sub_rng(G.SEED, "repair", split, idx)
    if rng.random() < 0.12:
        return gen_repair_transition(D, split, rng)
    kinds = pick_kinds(rng, split)
    first = kinds[0]
    pred = KIND_PRED[first]
    force = KIND_FORCE.get(first)
    prop_variant = None
    if first == "propulsion_era":
        prop_variant = rng.random() < 0.55
        pred = "prop" if prop_variant else "older"
        force = "prop" if prop_variant else None
    rec = D.pick(split, rng, pred)
    if rec is None:
        G.fail("repair:no_anchor")
        return None
    design = D.design(rec)
    year = C.request_year(rec)
    need = {KIND_FORCE[k] for k in kinds if k in KIND_FORCE}
    r = G.make_request(D, split, rec, rng, force=need or None)
    if r is None:
        return None
    req, unseen = r
    req = {k: v for k, v in req.items() if not (k == "ship_type_fine" and rng.random() < 0.5)}
    if first == "propulsion_era" and not prop_variant:
        req.pop("required_propulsion", None)
    if not K.check_design(req, design)["ok"]:
        return None
    got = build_bad(D, req, design, kinds, rng, year)
    if got is None:
        G.fail("repair:no_corruption:" + first)
        return None
    bad, res_bad, done = got
    if split != "test" and heldout_kinds(done):
        return None
    viols = violations_for_user(res_bad)
    two_round = len(res_bad["violations"]) >= 2 and rng.random() < 0.3
    partial = None
    if two_round:
        partial, method, _tr = auto_repair(D, req, bad, design, year, stop_after=1)
        res_p = K.check_design(req, partial, th=D.th)
        if method != "partial" or res_p["ok"] or partial == bad or not (_vset(res_p) < _vset(res_bad)):
            partial = None  # a partial fix must remove at least one violation and introduce none
    fixed, method, trace = auto_repair(D, req, bad if partial is None else partial, design, year)
    if not K.check_design(req, fixed, th=D.th)["ok"]:
        return None
    text = K.request_text_v1(req, rng.randrange(6))
    nbr = D.neighbors(req, [rec["id"]])
    ep = G.new_episode("repair", "+".join(done), split)
    ep["messages"].append(G.msg("user", G.user_turn(text, req, nbr)))
    G.add_asst(ep, G.asst_turn("feasible", bad), status="feasible", origin="corrupted", source=rec["id"], role="initial", train=False,
               violations=[f"{v['code']}:{v['field']}" for v in res_bad["violations"]], corruption=done)
    ep["messages"].append(G.msg("user", feedback_turn(viols)))
    if partial is not None:
        G.add_asst(ep, G.asst_turn("feasible", partial), status="feasible", origin="partial_fix", source=rec["id"], role="partial_fix", train=False)
        res_p = K.check_design(req, partial, th=D.th)
        ep["messages"].append(G.msg("user", feedback_turn(violations_for_user(res_p))))
    G.add_asst(ep, G.asst_turn("feasible", fixed), status="feasible", origin="gold" if method == "gold_fallback" else "repaired", source=rec["id"], role="fix", train=True,
               repair_method=method, repair_trace=trace)
    G.add_source(ep, rec["id"], D)
    ep["meta"]["violation_kinds"] = done
    ep["meta"]["violation_codes"] = sorted({f"{v['code']}:{v['field']}" for v in res_bad["violations"]})
    ep["meta"]["repair_method"] = method
    ep["meta"]["rounds"] = 2 if partial is not None else 1
    ep["meta"]["unseen_combo"] = unseen
    if heldout_kinds(done):
        ep["meta"]["holdout"].append("violation_pair")
    return G.fin_meta(ep, D, req)


def gen_repair_transition(D: DD.Data, split: str, rng: random.Random) -> dict[str, Any] | None:
    """元の要求が不可能な repair: 要求を黙って満たさない設計 -> validator が req 違反 -> relaxed / infeasible に遷移する。"""
    case = I.build_case(D, split, rng)
    if case is None or case.anchor is None:
        return None
    if case.family in ("hull_type", "type_size", "contradiction"):
        return None  # the validator has no requirement-layer violation to report for these
    req = case.req
    if split != "test" and K.is_heldout_combo(req):
        return None
    anchor_design = D.design(case.anchor)
    res_bad = K.check_design(req, anchor_design, th=D.th)
    if res_bad["ok"] or not any(v["code"].startswith("req_") for v in res_bad["violations"]):
        return None
    viols = violations_for_user(res_bad)
    text = K.request_text_v1(req, rng.randrange(6))
    nbr = D.neighbors(req, [case.anchor["id"]])
    ep = G.new_episode("repair", "request_impossible", split)
    ep["messages"].append(G.msg("user", G.user_turn(text, req, nbr)))
    G.add_asst(ep, G.asst_turn("feasible", anchor_design), status="feasible", origin="silent_relax", source=case.anchor["id"], role="initial", train=False,
               violations=[f"{v['code']}:{v['field']}" for v in res_bad["violations"]], corruption=["request_impossible"])
    ep["messages"].append(G.msg("user", feedback_turn(viols)))
    confs, rels = I.conflict_outputs(case.conflicts, case.status)
    od = I.out_design(case)
    G.add_asst(ep, G.asst_turn(case.status, od, confs, rels), status=case.status, origin="real" if od else "none",
               source=case.anchor["id"] if od else None, role="fix", train=True, repair_method="transition", family=case.family, conflict_info=[c.info() for c in case.conflicts])
    G.add_source(ep, case.anchor["id"], D)
    ep["meta"]["violation_kinds"] = ["request_impossible"]
    ep["meta"]["violation_codes"] = sorted({f"{v['code']}:{v['field']}" for v in res_bad["violations"]})
    ep["meta"]["repair_method"] = "transition"
    ep["meta"]["rounds"] = 1
    ep["meta"]["family"] = case.family
    return G.fin_meta(ep, D, req)
