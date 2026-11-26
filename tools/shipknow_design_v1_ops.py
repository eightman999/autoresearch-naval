"""design-v1 の設計編集操作（modify の物理スケーリング、repair の違反生成と自動修正が共有する）。

核は `rederive`: 排水量 D と速力 V を変えたとき、寸法・出力・乗員を「元の設計が (艦種 × 年代) の層の回帰からどれだけ外れていたか（残差）」を保ったまま
validator と同じ回帰（`validator_thresholds.json` の ratio_strata）から再計算する。つまり
    slender = L/Δ^(1/3)  -> 全長 L,   lb = L/B -> 全幅 B,   cb = Δ/(L·B·T) -> 喫水 T,   adm = Δ^(2/3)·V^3/P -> 出力 P (Admiralty 係数),   crew/Δ -> 乗員
を、新しい (Δ, V) での回帰予測 + 元の残差で決める。D も V も変えなければ元の設計を（丸めの範囲で）再現する。
速力↑ -> 出力は立方、必要なら艦が長くなる。排水量↓ -> 立方根則で寸法が縮み、乗員・出力も連動する。

武装・航空機・推進の編集は、重量増減（砲の口径^2.3 など）を排水量に反映させる（`weight_*`）。すべて決定論。
"""

from __future__ import annotations

import copy
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_validator as V  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402

DISP_KEYS = ["displacement_normal_t", "displacement_full_t", "displacement_standard_t"]
EQUIP_ID_PREFIX = ("required:", "required_propulsion:", "min_gun_barrels", "min_aircraft", "min_torpedo_tubes")
HELI_ENTRY = {"kind": "aircraft_helicopter"}


def clone(d: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(d)


def rnd(field: str, v: float) -> float | int:
    return B.rnd(field, v)


def ref_disp(d: dict[str, Any]) -> float:
    return C.ref_displacement(d)[0] or 0.0


def sort_weapons(d: dict[str, Any]) -> None:
    ws = d.get("weapons") or []
    ws.sort(key=lambda w: (B.KIND_ORDER.index(w["kind"]), -(w.get("cal") or 0), w.get("n", 0)))
    d["weapons"] = ws


def normalize(d: dict[str, Any]) -> dict[str, Any]:
    """丸め（v0 と同じ解像度）・満載 >= 常備 >= 基準の順序・キー順を整える。"""
    for f in DISP_KEYS + ["length_m", "beam_m", "draught_m", "power_hp", "speed_kn", "endurance_nm", "complement", "shafts"]:
        if K._isnum(d.get(f)):
            d[f] = rnd(f, d[f])
    if d.get("displacement_standard_t") is not None and d.get("displacement_standard_t") == d.get("displacement_normal_t"):
        del d["displacement_standard_t"]
    sort_weapons(d)
    return K.order_design(d)


# --------------------------------------------------------------------------------------------------
# regression prediction (same strata as the validator)
# --------------------------------------------------------------------------------------------------
def _pred_log(th: dict[str, Any], metric: str, st: str, era: str | None, design: dict[str, Any]) -> float | None:
    look = V._lookup_metric(th, metric, st, era)
    if not look:
        return None
    _key, met = look
    xs = V.regressor_values(design, met["vars"])
    if xs is None:
        return None
    return met["coef"][0] + sum(c * x for c, x in zip(met["coef"][1:], xs))


def _resid(th: dict[str, Any], metric: str, st: str, era: str | None, design: dict[str, Any], ratios: dict[str, float]) -> tuple[float, float | None]:
    """(元の比の対数, 予測の対数 or None)。予測が無ければ、比を一定に保つ（残差 = 0 で比そのまま）。"""
    r = ratios.get(metric)
    if r is None or r <= 0:
        return float("nan"), None
    return math.log(r), _pred_log(th, metric, st, era, design)


def rederive(d: dict[str, Any], k_disp: float, v_new: float | None = None, year: int | None = None, crew_extra: float = 0.0, th: dict[str, Any] | None = None,
             stretch: float = 1.0) -> dict[str, Any]:
    """排水量を k_disp 倍、速力を v_new にした設計。寸法・出力・乗員は元の残差を保って層の回帰から再計算する（本文参照）。stretch > 1 で全長を余分に伸ばす。"""
    th = th or K.thresholds()
    st = d["ship_type"]
    era = C.era_bin(year) if year is not None else None
    r0 = C.ratios_of(d)
    d2 = clone(d)
    for f in DISP_KEYS:
        if K._isnum(d2.get(f)):
            d2[f] = d2[f] * k_disp
    V0 = d.get("speed_kn")
    V1 = v_new if v_new is not None else V0
    if K._isnum(V1):
        d2["speed_kn"] = V1
    D2 = ref_disp(d2)
    # --- slender -> L
    L0, B0, T0, P0, C0 = (d.get(f) for f in ("length_m", "beam_m", "draught_m", "power_hp", "complement"))
    if K._isnum(L0) and "slender" in r0:
        lr, pr = _resid(th, "slender", st, era, d, r0)
        if pr is not None:
            p2 = _pred_log(th, "slender", st, era, d2)
            lr2 = (p2 + (lr - pr)) if p2 is not None else lr
        else:
            lr2 = lr
        d2["length_m"] = math.exp(lr2) * D2 ** (1 / 3) * stretch
    L2 = d2.get("length_m") if K._isnum(d2.get("length_m")) else L0
    # --- lb -> B
    if K._isnum(B0) and K._isnum(L2) and "lb" in r0:
        lr, pr = _resid(th, "lb", st, era, d, r0)
        p2 = _pred_log(th, "lb", st, era, d2) if pr is not None else None
        lb2 = math.exp(p2 + (lr - pr)) if p2 is not None else r0["lb"]
        d2["beam_m"] = L2 / lb2
    # --- cb -> T
    Bn = d2.get("beam_m") if K._isnum(d2.get("beam_m")) else B0
    cbkey = next((k for k in ("cb_normal", "cb_standard", "cb_full") if k in r0), None)
    if cbkey and K._isnum(T0) and K._isnum(L2) and K._isnum(Bn):
        lr, pr = _resid(th, cbkey, st, era, d, r0)
        p2 = _pred_log(th, cbkey, st, era, d2) if pr is not None else None
        cb2 = math.exp(p2 + (lr - pr)) if p2 is not None else r0[cbkey]
        Db = d2.get(f"displacement_{cbkey.split('_')[1]}_t")
        d2["draught_m"] = Db / (cb2 * L2 * Bn)
    # --- adm -> P
    if K._isnum(P0) and "adm" in r0 and K._isnum(V1):
        lr, pr = _resid(th, "adm", st, era, d, r0)
        p2 = _pred_log(th, "adm", st, era, d2) if pr is not None else None
        adm2 = math.exp(p2 + (lr - pr)) if p2 is not None else r0["adm"]
        d2["power_hp"] = D2 ** (2 / 3) * V1 ** 3 / adm2
    # --- crew
    if K._isnum(C0) and "crew_t" in r0:
        lr, pr = _resid(th, "crew_t", st, era, d, r0)
        p2 = _pred_log(th, "crew_t", st, era, d2) if pr is not None else None
        ct2 = math.exp(p2 + (lr - pr)) if p2 is not None else r0["crew_t"]
        d2["complement"] = ct2 * D2 + crew_extra
    elif K._isnum(C0):
        d2["complement"] = C0 * k_disp ** 0.6 + crew_extra
    # --- endurance: fuel scales with size, little with speed
    if K._isnum(d.get("endurance_nm")):
        d2["endurance_nm"] = d["endurance_nm"] * k_disp ** 0.12 * ((V0 / V1) ** 0.6 if K._isnum(V0) and K._isnum(V1) and V1 > 0 else 1.0)
    return normalize(d2)


# --------------------------------------------------------------------------------------------------
# weights (tonnes)
# --------------------------------------------------------------------------------------------------
def gun_weight_t(cal: float, barrels: float) -> float:
    """砲 1 門あたり ≈ 0.0005·cal^2.3 t（127mm 連装 ≈ 35 t/門、203mm ≈ 100 t/門、305mm ≈ 260 t/門）に支持構造 1.5 倍。"""
    return 0.0005 * cal ** 2.3 * barrels * 1.5


def barrels_of(w: dict[str, Any]) -> int:
    return int(X._n(w) * X._per(w))


def main_gun_item(d: dict[str, Any]) -> dict[str, Any] | None:
    guns = [w for w in d.get("weapons") or [] if w.get("kind") == "gun" and K._isnum(w.get("cal"))]
    return max(guns, key=lambda w: (w["cal"], barrels_of(w))) if guns else None


def weapons_list(d: dict[str, Any]) -> list[dict[str, Any]]:
    d.setdefault("weapons", [])
    return d["weapons"]


def add_barrels(d: dict[str, Any], extra: int, cal: float | None = None) -> float:
    """主砲（cal 指定ならその口径の砲）に砲身を extra 以上足す。戻り値 = 追加重量 t。"""
    ws = weapons_list(d)
    tgt = None
    if cal is not None:
        c = [w for w in ws if w.get("kind") == "gun" and w.get("cal") == cal]
        tgt = max(c, key=barrels_of) if c else None
    else:
        tgt = main_gun_item(d)
    if tgt is None:
        return 0.0
    per = X._per(tgt)
    mounts = max(1, math.ceil(extra / per))
    tgt["n"] = int(X._n(tgt) + mounts)
    return gun_weight_t(tgt["cal"], mounts * per)


def set_main_caliber(d: dict[str, Any], new_cal: float, barrels: int | None = None) -> float:
    """主砲の口径を new_cal にする（砲身数は barrels、無指定なら重量が極端に増えないように 口径比で減らす）。戻り値 = 重量変化 t。"""
    g = main_gun_item(d)
    if g is None:
        return 0.0
    old_w = gun_weight_t(g["cal"], barrels_of(g))
    nb = barrels if barrels is not None else max(2 if barrels_of(g) >= 2 else 1, round(barrels_of(g) * min(1.0, (g["cal"] / new_cal) ** 1.2)))
    per = X._per(g)
    g["cal"] = num_cal(new_cal)
    g["n"] = int(max(1, round(nb / per)))
    return gun_weight_t(g["cal"], barrels_of(g)) - old_w


def num_cal(c: float) -> float | int:
    return int(c) if float(c).is_integer() else round(c, 1)


def add_launcher(d: dict[str, Any], kind: str, rng_choice: int = 0, radar: bool = True) -> float:
    """sam / ssm / ciws / asw_rocket / torpedo_tube の発射機を足す。戻り値 = 追加重量（排水量比 %）。"""
    ws = weapons_list(d)
    D = ref_disp(d)
    if kind == "sam":
        ws.append({"kind": "sam", "n": 1 + rng_choice % 2, "per": 2 if D < 3000 else 4})
        if radar:
            sens = set(d.get("sensors") or [])
            sens.add("air_search_radar")
            d["sensors"] = sorted(sens)
        return max(40.0, 0.055 * D)
    if kind == "ssm":
        ws.append({"kind": "ssm", "n": 2 if D < 1000 else (4 if D < 4000 else 8)})
        return max(15.0, 0.03 * D)
    if kind == "ciws":
        ws.append({"kind": "ciws", "n": 1 + rng_choice % 2})
        return max(8.0, 0.012 * D)
    if kind == "torpedo_tube":
        ws.append({"kind": "torpedo_tube", "n": 2, "per": 3, "cal": 533})
        return max(10.0, 0.012 * D)
    raise ValueError(kind)


def set_aircraft(d: dict[str, Any], n: int, kind: str | None = None) -> float:
    """搭載機を n 機にする。航空設備・air を整える（air の種類は、既にあればそのまま、無ければ helicopter）。戻り値 = 追加重量 t（格納庫・燃料・設備）。"""
    ws = weapons_list(d)
    old = X.aircraft_count(d)
    hel = [w for w in ws if w.get("kind") == "aircraft_helicopter"]
    if n <= 0:
        d["weapons"] = [w for w in ws if w.get("kind") not in ("aircraft_helicopter", "aviation_facility")]
        d.pop("air", None)
        return -4.0 * old
    if hel:
        hel[0]["n"] = n
        for extra in hel[1:]:
            ws.remove(extra)
    else:
        ws.append({"kind": "aircraft_helicopter", "n": n})
    if not any(w.get("kind") == "aviation_facility" for w in ws):
        ws.append({"kind": "aviation_facility"})
    air = set(d.get("air") or [])
    if not air:
        air.add(kind or "helicopter")
    elif kind:
        air.add(kind)
    d["air"] = sorted(air)
    return 5.0 * (n - old) + (0 if old else 40.0)


def set_propulsion(d: dict[str, Any], p: str) -> None:
    d["propulsion"] = [p]


def scale_weight(d: dict[str, Any], delta_t: float, v_new: float | None = None, year: int | None = None, crew_extra: float = 0.0, stretch: float = 1.0) -> dict[str, Any]:
    """重量 delta_t（t、艦の支持構造込み）を排水量に足して rederive。"""
    D = ref_disp(d)
    k = max(0.2, (D + delta_t) / D)
    return rederive(d, k, v_new, year, crew_extra, stretch=stretch)


# --------------------------------------------------------------------------------------------------
# shrinking the armament with the hull (modify: displacement down / crew down)
# --------------------------------------------------------------------------------------------------
GUN_PRIORITY_LOW_FIRST = lambda w: (w.get("cal") or 0)  # noqa: E731


def equip_ok(req: dict[str, Any] | None, d: dict[str, Any]) -> bool:
    """装備系の制約（required / 砲身 / 魚雷管 / 搭載機 / 推進）だけを見る。"""
    if not req:
        return True
    for c in K.req_checks_v1(req, d):
        if c["id"].startswith(EQUIP_ID_PREFIX) and c["ok"] is not True:
            return False
    return True


def shrink_weapons(d: dict[str, Any], k: float, req: dict[str, Any] | None = None, floor_index_ratio: float = 1.0) -> dict[str, Any]:
    """排水量が k 倍（< 1）になった設計の武装を、主砲力指標/排水量 が元の floor_index_ratio 倍以下になるまで減らす（装備系の要求は守る）。"""
    d2 = clone(d)
    D0 = ref_disp(d)
    D2 = D0 * k
    idx0 = _gun_idx(d)
    target = idx0 / D0 * floor_index_ratio
    guard = 0
    while _gun_idx(d2) / D2 > target * 1.0001 and guard < 40:
        guard += 1
        guns = sorted((w for w in weapons_list(d2) if w.get("kind") == "gun" and K._isnum(w.get("cal"))), key=lambda w: (w["cal"], -barrels_of(w)))
        done = False
        for w in guns:  # smallest caliber first
            trial = clone(d2)
            tw = next(x for x in trial["weapons"] if x == w)
            if X._n(tw) > 1:
                tw["n"] = int(X._n(tw) - 1)
            else:
                trial["weapons"].remove(tw)
            if equip_ok(req, trial):
                d2 = trial
                done = True
                break
        if not done:
            break
    # count-type weapons follow the hull when it shrinks by more than 12 %
    if k < 0.88:
        for w in list(weapons_list(d2)):
            if w.get("kind") in ("torpedo_tube", "ssm", "sam", "depth_charge", "asw_rocket", "mine") and w.get("n", 0) > 1:
                trial = clone(d2)
                tw = next(x for x in trial["weapons"] if x == w)
                tw["n"] = int(max(1, round(X._n(tw) * k ** 0.8)))
                if tw["n"] != w["n"] and equip_ok(req, trial):
                    d2 = trial
    sort_weapons(d2)
    return d2


def _gun_idx(d: dict[str, Any]) -> float:
    from shipknow_design_eval import gun_index
    return gun_index(d)


def reduce_light_weapons(d: dict[str, Any], frac: float, req: dict[str, Any] | None = None) -> dict[str, Any]:
    """乗員削減のため、口径 <= 40mm の砲・機銃・小火器の数を frac だけ減らす（装備系の要求は守る）。"""
    d2 = clone(d)
    for w in list(weapons_list(d2)):
        if (w.get("kind") == "gun" and (w.get("cal") or 999) <= 40) or w.get("kind") in ("mg", "small_arms", "grenade_launcher"):
            if X._n(w) <= 1:
                continue
            trial = clone(d2)
            tw = next(x for x in trial["weapons"] if x == w)
            tw["n"] = int(max(1, round(X._n(tw) * (1 - frac))))
            if equip_ok(req, trial):
                d2 = trial
    sort_weapons(d2)
    return d2


# --------------------------------------------------------------------------------------------------
# misc queries used by generators
# --------------------------------------------------------------------------------------------------
def changed_fields(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """設計 a と b で値が違うトップレベルのキー。"""
    keys = set(a) | set(b)
    return sorted(k for k in keys if a.get(k) != b.get(k))


def disp_in_basis(d: dict[str, Any], basis: str) -> float | None:
    v = d.get(f"displacement_{basis}_t")
    return float(v) if K._isnum(v) else None
