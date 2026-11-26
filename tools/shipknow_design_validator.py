"""設計 JSON の deterministic validator（LLM に物理・数値の整合性を任せないための検査器）。

    from shipknow_design_validator import validate
    validate(requirements, design) -> {"ok": bool, "violations": [{"code","field","detail"}], "warnings": [...]}

    .venv/bin/python tools/shipknow_design_validator.py build   # train の実艦から閾値表 validator_thresholds.json を作る
    .venv/bin/python tools/shipknow_design_validator.py eval    # 誤検出率（test / valid の実艦の正解設計）と、壊した設計の検出率

検査の層（violation の code 接頭辞）
  schema_*        設計 JSON の型・語彙・値域（艦種ラベル、weapons の kind、センサー kind、正の数）
  req_*           要求充足: 排水量上限（基準別）、最低速力、必須装備、艦種一致、乗員上限、航続下限、国・年（設計 JSON に書いてあれば）
  ratio_*         物理・統計的な妥当性。L/B、B/T、Δ/(L·B·T)（ブロック係数相当、満載/常備/基準の別）、Admiralty 係数相当 Δ^(2/3)·V^3/P、
                  V/√L、P/Δ、乗員/Δ、L/Δ^(1/3)、満載/常備。train の実艦から (艦種 × 年代) ごとに分位点を取り、
                  [p0.5, p99.5] を対数スケールで margin だけ広げた範囲を外れたら違反、[p2, p98] を外れたら warning。
  gun_*           主砲口径が（艦種の）train 最大×1.25、または排水量帯の train 最大×1.25 を超える
  era_early       装備・センサー・機関の初出年（train の艦級と、その艦級の改装履歴から求めた「3 番目に早い艦級グループ」の年）より 5 年以上前の採用
  type_*          艦種と装備の矛盾（潜水艦に回転翼機、小型艦艇種に 203mm 超の砲、水上艦以外の弾道ミサイル、小型艦に航空機）
warning: 国別の初出年より 5 年以上早い（その国が train 上で持っていない技術。架空シナリオでは起こりうるので違反にしない）、[p2,p98] 外れ。

閾値表が無い・層が小さい（n<min_n）ときは、(艦種, 年代) → (艦種, 全年代) → (全艦種, 年代) → 全体 の順にフォールバックする。
"""

from __future__ import annotations

import argparse
import collections
import copy
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402
import shipknow_design_common as C  # noqa: E402
import shipknow_types as T  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
THRESHOLDS_PATH = C.OUT_DIR / "validator_thresholds.json"

QUANTILES = [0.005, 0.01, 0.02, 0.98, 0.99, 0.995]
MIN_N = 40
MARGIN = 0.25         # 残差（対数）の [p0.5, p99.5] の幅の 25% だけ広げる（valid で誤検出率と検出率の釣り合いを見て決めた。レポート参照）
ERA_TOL_YEARS = 5
ROBUST_RANK = 3        # 初出年 = 3 番目に早い艦級グループの年
RANK_OVERRIDE = {"ciws": 5}  # パーサが 19 世紀の多連装機砲（Nordenfelt の n x 10 - 11.4 など）を ciws と分類する艦級が 4 グループあるため
MIN_GROUPS_FIRST = 5
MIN_SPREAD = 0.2       # 残差の幅（対数）の下限。ほぼ定数の項目（合成データや小さな層）で、丸め誤差が違反にならないように
GUN_MARGIN = 1.25
SANE = {"displacement_normal_t": (1, 150000), "displacement_full_t": (1, 150000), "displacement_standard_t": (1, 150000), "length_m": (3, 400), "beam_m": (0.8, 80),
        "draught_m": (0.1, 20), "power_hp": (5, 500000), "speed_kn": (2, 75), "complement": (1, 8000), "endurance_nm": (10, 60000), "shafts": (1, 8)}
SENSOR_KINDS = ["air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar", "radar", "sonar", "esm_ecm", "decoy", "eo_director", "combat_system"]
SMALL_TYPES = {"patrol", "coast_guard", "gunboat_river", "fast_attack_craft", "torpedo_boat", "mine_warfare", "amphibious", "auxiliary", "training", "corvette"}
SMALL_TYPE_MAX_GUN = 203
DISP_EDGES = [0, 10, 30, 100, 300, 1000, 3000, 10000, 30000, 1e9]


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------
def quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    pos = q * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = min(len(xs) - 1, lo + 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


REGRESSORS = {"lb": ["D", "V"], "bt": ["D"], "cb_normal": ["D", "V"], "cb_full": ["D", "V"], "cb_standard": ["D", "V"], "adm": ["D", "Fr"], "fr": ["D"],
              "hp_t": ["D", "V"], "crew_t": ["D"], "slender": ["D", "V"], "full_normal": ["D"]}


def regressor_values(design: dict[str, Any], names: list[str]) -> list[float] | None:
    """回帰の説明変数（対数）: D = 基準排水量、V = 速力、Fr = V/√L。"""
    out = []
    dref, _b = C.ref_displacement(design)
    for nm in names:
        if nm == "D":
            x = dref
        elif nm == "V":
            x = design.get("speed_kn")
        elif nm == "Fr":
            L, Vv = design.get("length_m"), design.get("speed_kn")
            x = Vv / math.sqrt(L) if _isnum(L) and _isnum(Vv) and L > 0 and Vv > 0 else None
        else:
            x = None
        if not _isnum(x) or x <= 0:
            return None
        out.append(math.log(x))
    return out


def ols(rows: list[tuple[list[float], float]]) -> list[float]:
    """log(metric) = a + Σ b_i·x_i の最小二乗（正規方程式、微小な ridge 付き）。戻り値 [a, b_1, ...]。"""
    k = len(rows[0][0]) + 1
    A = [[0.0] * k for _ in range(k)]
    bvec = [0.0] * k
    for xs, y in rows:
        z = [1.0] + xs
        for i in range(k):
            bvec[i] += z[i] * y
            for j in range(k):
                A[i][j] += z[i] * z[j]
    for i in range(1, k):
        A[i][i] += 1e-9 * (A[i][i] + 1.0)
    # gaussian elimination
    for i in range(k):
        piv = max(range(i, k), key=lambda r_: abs(A[r_][i]))
        A[i], A[piv] = A[piv], A[i]
        bvec[i], bvec[piv] = bvec[piv], bvec[i]
        if abs(A[i][i]) < 1e-12:
            continue
        for r_ in range(i + 1, k):
            f = A[r_][i] / A[i][i]
            for c_ in range(i, k):
                A[r_][c_] -= f * A[i][c_]
            bvec[r_] -= f * bvec[i]
    coef = [0.0] * k
    for i in range(k - 1, -1, -1):
        coef[i] = (bvec[i] - sum(A[i][j] * coef[j] for j in range(i + 1, k))) / A[i][i] if abs(A[i][i]) >= 1e-12 else 0.0
    return coef


def _isnum(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def v(code: str, field: str, detail: str) -> dict[str, str]:
    return {"code": code, "field": field, "detail": detail}


_TH_CACHE: dict[str, Any] = {}


def load_thresholds(path: Path | None = None) -> dict[str, Any]:
    path = Path(path or THRESHOLDS_PATH)
    key = str(path)
    if key not in _TH_CACHE:
        _TH_CACHE[key] = json.loads(path.read_text(encoding="utf-8"))
    return _TH_CACHE[key]


def _lookup_metric(th: dict[str, Any], metric: str, ship_type: str, era: str | None) -> tuple[str, dict[str, Any]] | None:
    order = [f"{ship_type}|{era}", f"{ship_type}|*", f"*|{era}", "*|*"]
    for k in order:
        st = th["ratio_strata"].get(k)
        if st and metric in st["metrics"]:
            return k, st["metrics"][metric]
    return None


def _bounds(q: dict[str, float], margin: float) -> tuple[float, float, float, float]:
    """残差（対数）の分位点 -> (hard_lo, hard_hi, soft_lo, soft_hi)。hard は [p0.5, p99.5] を幅の margin 倍だけ広げる。"""
    lo5, hi5 = q["0.005"], q["0.995"]
    spread = max(hi5 - lo5, MIN_SPREAD)
    return lo5 - margin * spread, hi5 + margin * spread, q["0.02"], q["0.98"]


# --------------------------------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------------------------------
def _schema(design: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(design, dict):
        return [v("schema_not_object", "", "design is not a JSON object")]
    st = design.get("ship_type")
    if st not in T.COARSE_TYPES:
        out.append(v("schema_ship_type", "ship_type", f"{st!r} is not one of the coarse ship types"))
    if "ship_type_fine" in design and not isinstance(design["ship_type_fine"], str):
        out.append(v("schema_ship_type_fine", "ship_type_fine", "must be a string"))
    for f, (lo, hi) in SANE.items():
        if f in design:
            x = design[f]
            if not _isnum(x):
                out.append(v("schema_number", f, f"{x!r} is not a number"))
            elif not (lo <= x <= hi):
                out.append(v("schema_range", f, f"{x} outside the sane range [{lo}, {hi}]"))
    if not any(isinstance(design.get(f), (int, float)) for f in ("displacement_normal_t", "displacement_full_t", "displacement_standard_t")):
        out.append(v("schema_missing", "displacement", "no displacement field"))
    for f in ("length_m", "speed_kn"):
        if f not in design:
            out.append(v("schema_missing", f, "missing"))
    w = design.get("weapons")
    if w is not None:
        if not isinstance(w, list):
            out.append(v("schema_weapons", "weapons", "must be a list"))
        else:
            for i, it in enumerate(w):
                if not isinstance(it, dict) or it.get("kind") not in B.KIND_ORDER:
                    out.append(v("schema_weapons", f"weapons[{i}]", f"bad kind {it.get('kind') if isinstance(it, dict) else it!r}"))
                elif "cal" in it and not (_isnum(it["cal"]) and it["cal"] > 0):
                    out.append(v("schema_weapons", f"weapons[{i}].cal", "bad caliber"))
                elif any(k in it and not (_isnum(it[k]) and it[k] >= 0) for k in ("n", "per")):
                    out.append(v("schema_weapons", f"weapons[{i}]", "bad n/per"))
    s = design.get("sensors")
    if s is not None and (not isinstance(s, list) or any(x not in SENSOR_KINDS for x in s)):
        out.append(v("schema_sensors", "sensors", "must be a list of known sensor kinds"))
    p = design.get("propulsion")
    if p is not None and (not isinstance(p, list) or any(not isinstance(x, str) for x in p)):
        out.append(v("schema_propulsion", "propulsion", "must be a list of strings"))
    return out


def _requirements(req: dict[str, Any], design: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if req.get("ship_type") and design.get("ship_type") != req["ship_type"]:
        out.append(v("req_type_mismatch", "ship_type", f"requested {req['ship_type']}, designed {design.get('ship_type')}"))
    if req.get("ship_type_fine") and design.get("ship_type_fine") != req["ship_type_fine"]:
        out.append(v("req_type_mismatch", "ship_type_fine", f"requested {req['ship_type_fine']}, designed {design.get('ship_type_fine')}"))
    if "country" in design and req.get("country") and design["country"] != req["country"]:
        out.append(v("req_country_mismatch", "country", f"requested {req['country']}, design says {design['country']}"))
    if "year" in design and req.get("year") is not None and design["year"] != req["year"]:
        out.append(v("req_year_mismatch", "year", f"requested {req['year']}, design says {design['year']}"))
    lim = req.get("max_displacement_t")
    if lim is not None:
        basis = req.get("displacement_basis", "normal")
        val = design.get(f"displacement_{basis}_t")
        if not _isnum(val):
            out.append(v("req_displacement_missing", f"displacement_{basis}_t", f"requirement is on {basis} displacement but the design has none"))
        elif val > lim:
            out.append(v("req_displacement_exceeds", f"displacement_{basis}_t", f"{val} t > limit {lim} t"))
    vmin = req.get("min_speed_kn")
    if vmin is not None:
        val = design.get("speed_kn")
        if not _isnum(val):
            out.append(v("req_speed_missing", "speed_kn", "no speed"))
        elif val < vmin:
            out.append(v("req_speed_below", "speed_kn", f"{val} kn < minimum {vmin} kn"))
    for tok in req.get("required", []) or []:
        if not C.satisfies(tok, design):
            out.append(v("req_equipment_missing", tok, f"required {tok} is not in the design"))
    if req.get("max_complement") is not None:
        val = design.get("complement")
        if _isnum(val) and val > req["max_complement"]:
            out.append(v("req_complement_exceeds", "complement", f"{val} > limit {req['max_complement']}"))
    if req.get("min_endurance_nm") is not None:
        val = design.get("endurance_nm")
        if _isnum(val) and val < req["min_endurance_nm"]:
            out.append(v("req_endurance_below", "endurance_nm", f"{val} nm < minimum {req['min_endurance_nm']} nm"))
    return out


def _physics(design: dict[str, Any], th: dict[str, Any], margin: float) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """各比 m を log m = a + Σ b_i·log x_i（x = Δ、速力、V/√L。(艦種, 年代) の層ごとに train で回帰）の残差で見る。
    サイズ・速力に依存する分を除くので、同じ分位幅でも検出力が高い。"""
    viol: list[dict[str, str]] = []
    warn: list[dict[str, str]] = []
    st = design.get("ship_type")
    yr = design.get("_year")
    era = C.era_bin(yr) if yr is not None else None
    for m, val in C.ratios_of(design).items():
        look = _lookup_metric(th, m, st, era)
        if not look:
            continue
        key, met = look
        xs = regressor_values(design, met["vars"])
        if xs is None:
            continue
        pred_log = met["coef"][0] + sum(c_ * x_ for c_, x_ in zip(met["coef"][1:], xs))
        res = math.log(val) - pred_log
        lo, hi, slo, shi = _bounds(met["q"], margin)
        pred = math.exp(pred_log)
        if res < lo:
            viol.append(v("ratio_low", m, f"{val:.3g} is {math.exp(res):.2f}x the expected {pred:.3g} (lower limit {math.exp(lo):.2f}x; stratum {key}, n={met['n']})"))
        elif res > hi:
            viol.append(v("ratio_high", m, f"{val:.3g} is {math.exp(res):.2f}x the expected {pred:.3g} (upper limit {math.exp(hi):.2f}x; stratum {key}, n={met['n']})"))
        elif res < slo:
            warn.append(v("ratio_low_soft", m, f"{val:.3g} below p2 ({key})"))
        elif res > shi:
            warn.append(v("ratio_high_soft", m, f"{val:.3g} above p98 ({key})"))
    return viol, warn


def _guns(design: dict[str, Any], th: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    g = C.main_gun_mm(design)
    if g is None:
        return out
    st = design.get("ship_type")
    cap = th.get("type_gun_max_mm", {}).get(st)
    if cap and g > cap * GUN_MARGIN:
        out.append(v("gun_too_big_for_type", "weapons", f"{g} mm gun on a {st} (largest in train: {cap} mm)"))
    d, _ = C.ref_displacement(design)
    if d:
        for b in th.get("gun_by_disp", []):
            if b["lo"] <= d < b["hi"]:
                if b["n"] >= 15 and g > b["max_cal"] * GUN_MARGIN:
                    out.append(v("gun_too_big_for_displacement", "weapons", f"{g} mm gun on {d:g} t (largest in train for {b['lo']}-{b['hi']} t: {b['max_cal']} mm)"))
                break
    return out


def _era(design: dict[str, Any], year: int | None, th: dict[str, Any], country: str | None) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    viol: list[dict[str, str]] = []
    warn: list[dict[str, str]] = []
    if year is None:
        return viol, warn
    cfy = th.get("country_first_year", {}).get(country or "", {})
    for key in sorted(C.era_keys(design)):
        fy = th["first_year"].get(key)
        if fy and year < fy["year"] - ERA_TOL_YEARS:
            viol.append(v("era_early", key, f"{key} first appears around {fy['year']} in real classes; design year is {year}"))
        elif key in cfy and year < cfy[key] - ERA_TOL_YEARS:
            warn.append(v("era_nation_early", key, f"{country} has no real class with {key} before {cfy[key]}; design year is {year}"))
    return viol, warn


def _type_rules(design: dict[str, Any], year: int | None = None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    st = design.get("ship_type")
    caps = C.caps_of_design(design)
    air = set(design.get("air") or [])
    g = C.main_gun_mm(design)
    if st == "submarine":
        if "air:helicopter" in caps or ("aircraft" in caps and "air:seaplane" not in caps):
            out.append(v("type_equipment_mismatch", "air", "a submarine with helicopters / non-seaplane aircraft"))
        elif "air:seaplane" in caps and year is not None and year > 1945:
            out.append(v("type_equipment_mismatch", "air", f"a submarine with a seaplane in {year} (only the 1920s-40s submarine cruisers carried one)"))
    lim = SMALL_TYPE_MAX_GUN
    if st == "gunboat_river" and year is not None and year < 1900:
        lim = 320  # 19th-century coastal / flat-iron gunboats with 240-280 mm guns
    if st in SMALL_TYPES and g is not None and g > lim:
        out.append(v("type_equipment_mismatch", "weapons", f"{g} mm gun on a {st} (limit {lim} mm)"))
    if "ballistic_missile" in caps and st not in ("submarine", "battleship", "cruiser", "heavy_cruiser", "light_cruiser", "destroyer", "frigate", "aircraft_carrier", "auxiliary"):
        out.append(v("type_equipment_mismatch", "weapons", f"ballistic missiles on a {st}"))
    if st in ("fast_attack_craft", "torpedo_boat", "patrol", "gunboat_river", "coast_guard", "mine_warfare") and "air:helicopter" in caps:
        d, _ = C.ref_displacement(design)
        if d is not None and d < 150:
            out.append(v("type_equipment_mismatch", "air", f"a helicopter on a {st} of {d:g} t"))
    return out


def validate(requirements: dict[str, Any] | None, design: Any, thresholds: dict[str, Any] | None = None, *, margin: float = MARGIN,
             layers: tuple[str, ...] = ("schema", "req", "ratio", "gun", "era", "type")) -> dict[str, Any]:
    """requirements=None: 物理・年代・艦種の検査だけ（年は design["year"] があれば使う）。layers で検査層を選べる。"""
    th = thresholds if thresholds is not None else load_thresholds()
    violations: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    sch = _schema(design)
    if "schema" in layers:
        violations += sch
    if not isinstance(design, dict) or any(x["code"] == "schema_not_object" for x in sch):
        return {"ok": False, "violations": violations, "warnings": warnings}
    req = requirements or {}
    if "req" in layers and requirements:
        violations += _requirements(req, design)
    # numeric checks only on a design whose numbers are usable
    usable = {k: x for k, x in design.items() if k not in SANE or _isnum(x)}
    if design.get("ship_type") in T.COARSE_TYPES:
        year = req.get("year", design.get("year"))
        year = year if _isnum(year) else None
        d2 = dict(usable)
        d2["_year"] = year
        if "ratio" in layers:
            pv, pw = _physics(d2, th, margin)
            violations += pv
            warnings += pw
        if "gun" in layers:
            violations += _guns(usable, th)
        if "era" in layers:
            ev, ew = _era(usable, year, th, req.get("country", design.get("country")))
            violations += ev
            warnings += ew
        if "type" in layers:
            violations += _type_rules(usable, year)
    return {"ok": not violations, "violations": violations, "warnings": warnings}


# --------------------------------------------------------------------------------------------------
# thresholds
# --------------------------------------------------------------------------------------------------
def build_thresholds(recs: list[dict[str, Any]], group_of: dict[str, str], events_by_id: dict[str, list[dict[str, Any]]] | None = None,
                     min_n: int = MIN_N) -> dict[str, Any]:
    """recs: train の艦級レコードだけ（呼び出し側が保証）。events_by_id: id -> shipknow_history のイベント。"""
    elig = [r for r in recs if C.design_eligible(r)]
    designs = [(r, C.design_from_record(r)) for r in elig]

    # ---- ratio strata
    cells: dict[str, dict[str, list[tuple[list[float], float]]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for r, d in designs:
        era = C.era_bin(r["year"])
        rat = C.ratios_of(d)
        for m, x in rat.items():
            xs = regressor_values(d, REGRESSORS[m])
            if xs is None:
                continue
            for key in (f"{d['ship_type']}|{era}", f"{d['ship_type']}|*", f"*|{era}", "*|*"):
                cells[key][m].append((xs, math.log(x)))
    strata: dict[str, Any] = {}
    for key, ms in sorted(cells.items()):
        metrics = {}
        for m, pts in ms.items():
            if len(pts) >= min_n:
                coef = ols(pts)
                res_ = [y - coef[0] - sum(c_ * x_ for c_, x_ in zip(coef[1:], xs)) for xs, y in pts]
                metrics[m] = {"n": len(pts), "coef": [round(c_, 5) for c_ in coef], "vars": REGRESSORS[m], "q": {f"{q:g}": round(quantile(res_, q), 5) for q in QUANTILES}}
        if metrics:
            strata[key] = {"n": max(x["n"] for x in metrics.values()), "metrics": metrics}

    # ---- first year (records, events) per key; per country
    rec_years: dict[str, dict[str, int]] = collections.defaultdict(dict)       # key -> group -> min year
    ev_years: dict[str, dict[str, int]] = collections.defaultdict(dict)
    c_years: dict[tuple[str, str], dict[str, int]] = collections.defaultdict(dict)
    country_n: collections.Counter = collections.Counter()
    for r, d in designs:
        g = group_of[r["id"]]
        y = r["year"]
        country_n[r["country"]] += 1
        for key in C.era_keys(d):
            rec_years[key][g] = min(y, rec_years[key].get(g, 9999))
            c_years[(r["country"], key)][g] = min(y, c_years[(r["country"], key)].get(g, 9999))
        for e in (events_by_id or {}).get(r["id"], []):
            if e["src"] != "mod":
                continue
            for k in e["add"]:
                key = C.EVENT_KIND_TO_ERA_KEY.get(k)
                if key:
                    ev_years[key][g] = min(e.get("year_lo", e["year"]), ev_years[key].get(g, 9999))
                    c_years[(r["country"], key)][g] = min(e.get("year_lo", e["year"]), c_years[(r["country"], key)].get(g, 9999))

    def robust(gy: dict[str, int], key: str) -> int | None:
        ys = sorted(gy.values())
        rank = RANK_OVERRIDE.get(key, ROBUST_RANK)
        return ys[rank - 1] if len(ys) >= max(MIN_GROUPS_FIRST, rank + 2) else None

    first: dict[str, Any] = {}
    for key in C.ERA_KEYS:
        a, b = robust(rec_years.get(key, {}), key), robust(ev_years.get(key, {}), key)
        cand = [x for x in (a, b) if x is not None]
        if cand:
            first[key] = {"year": min(cand), "from_records": a, "from_events": b, "n_groups_records": len(rec_years.get(key, {})), "n_groups_events": len(ev_years.get(key, {}))}
    cfirst: dict[str, dict[str, int]] = collections.defaultdict(dict)
    for (country, key), gy in c_years.items():
        if country_n[country] >= 25 and len(gy) >= 3:
            cfirst[country][key] = sorted(gy.values())[1]  # 2 番目に早い

    # ---- guns
    tmax: dict[str, float] = {}
    by_type: dict[str, list[float]] = collections.defaultdict(list)
    bins = [{"lo": lo, "hi": hi, "cals": []} for lo, hi in zip(DISP_EDGES[:-1], DISP_EDGES[1:])]
    for r, d in designs:
        g = C.main_gun_mm(d)
        if g is None:
            continue
        by_type[d["ship_type"]].append(g)
        dref, _ = C.ref_displacement(d)
        if dref:
            for b in bins:
                if b["lo"] <= dref < b["hi"]:
                    b["cals"].append(g)
    for t, xs in by_type.items():
        if len(xs) >= 10:
            tmax[t] = max(xs)
    gun_by_disp = [{"lo": b["lo"], "hi": b["hi"], "n": len(b["cals"]), "max_cal": max(b["cals"]) if b["cals"] else 0} for b in bins]

    return {
        "version": 1,
        "built_from": {"records": "shipknow_v0_2 train, design_eligible", "n_designs": len(designs), "n_groups": len({group_of[r['id']] for r, _ in designs}),
                       "events": events_by_id is not None},
        "params": {"margin": MARGIN, "era_tol_years": ERA_TOL_YEARS, "min_n": min_n, "quantiles": QUANTILES, "robust_rank": ROBUST_RANK, "rank_override": RANK_OVERRIDE,
                   "min_groups_first": MIN_GROUPS_FIRST, "gun_margin": GUN_MARGIN, "small_types": sorted(SMALL_TYPES), "small_type_max_gun_mm": SMALL_TYPE_MAX_GUN},
        "ratio_strata": strata,
        "first_year": first,
        "country_first_year": cfirst,
        "type_gun_max_mm": tmax,
        "gun_by_disp": gun_by_disp,
    }


# --------------------------------------------------------------------------------------------------
# corruptions (for the detection-rate measurement)
# --------------------------------------------------------------------------------------------------
HIST_FIRST = {"sam": 1940, "ssm": 1940, "radar_any": 1925, "air:helicopter": 1930, "prop:gas_turbine": 1930, "prop:nuclear": 1940, "ciws": 1955}


def _scale(d: dict[str, Any], keys: list[str], k: float) -> dict[str, Any]:
    d = copy.deepcopy(d)
    for f in keys:
        if f in d:
            d[f] = B.rnd(f, d[f] * k)
    return d


def corruptions(rng: random.Random, req: dict[str, Any], gold: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    """(name, corrupted design, layer) のリスト。layer は壊しがどの層で検出されるべきかの主張（集計用）。"""
    out: list[tuple[str, dict[str, Any], str]] = []
    DISP = ["displacement_normal_t", "displacement_full_t", "displacement_standard_t"]
    out.append(("disp_x3", _scale(gold, DISP, 3.0), "ratio"))
    out.append(("disp_x1.5", _scale(gold, DISP, 1.5), "ratio"))
    out.append(("length_x1.5", _scale(gold, ["length_m"], 1.5), "ratio"))
    out.append(("beam_x2", _scale(gold, ["beam_m"], 2.0), "ratio"))
    out.append(("draught_x3", _scale(gold, ["draught_m"], 3.0), "ratio"))
    out.append(("speed_x1.6", _scale(gold, ["speed_kn"], 1.6), "ratio"))
    d = copy.deepcopy(gold)
    d["speed_kn"] = d["speed_kn"] + 15
    out.append(("speed_plus15kn", d, "ratio"))
    out.append(("power_x5", _scale(gold, ["power_hp"], 5.0), "ratio"))
    out.append(("crew_x8", _scale(gold, ["complement"], 8.0), "ratio"))
    # era: add equipment that did not exist yet
    year = req["year"]
    cands = [k for k, y0 in HIST_FIRST.items() if year <= y0 - 10]
    if cands:
        key = rng.choice(cands)
        d = copy.deepcopy(gold)
        if key == "sam":
            d.setdefault("weapons", []).append({"kind": "sam", "n": 2})
        elif key == "ssm":
            d.setdefault("weapons", []).append({"kind": "ssm", "n": 4})
        elif key == "ciws":
            d.setdefault("weapons", []).append({"kind": "ciws", "n": 1})
        elif key == "radar_any":
            d["sensors"] = sorted(set(d.get("sensors", [])) | {"air_search_radar"})
        elif key == "air:helicopter":
            d.setdefault("weapons", []).append({"kind": "aircraft_helicopter", "n": 1})
            d["air"] = ["helicopter"]
        elif key.startswith("prop:"):
            d["propulsion"] = [key.split(":", 1)[1]]
        out.append((f"era_{key}", d, "era"))
    # type mismatch
    if gold["ship_type"] == "submarine":
        d = copy.deepcopy(gold)
        d.setdefault("weapons", []).append({"kind": "aircraft_helicopter", "n": 1})
        d["air"] = ["helicopter"]
        out.append(("type_submarine_helicopter", d, "type"))
    if gold["ship_type"] in SMALL_TYPES:
        d = copy.deepcopy(gold)
        d.setdefault("weapons", []).append({"kind": "gun", "n": 2, "per": 3, "cal": 305})
        out.append(("type_small_305mm", d, "type"))
    # drop a required capability
    for tok in req.get("required", []):
        d = copy.deepcopy(gold)
        w = d.get("weapons", [])
        s = d.get("sensors", [])
        if tok == "torpedo_tubes":
            d["weapons"] = [x for x in w if x["kind"] != "torpedo_tube"]
        elif tok == "asw_weapon":
            d["weapons"] = [x for x in w if x["kind"] not in C.ASW_KINDS]
        elif tok == "sonar":
            d["sensors"] = [x for x in s if x != "sonar"]
        elif tok in ("ssm", "sam", "ciws", "ballistic_missile", "rocket_launcher"):
            d["weapons"] = [x for x in w if x["kind"] != tok]
        elif tok == "helicopter":
            d["weapons"] = [x for x in w if x["kind"] not in ("aircraft_helicopter", "aviation_facility")]
            d.pop("air", None)
        elif tok == "mines":
            d["weapons"] = [x for x in w if x["kind"] != "mine"]
        elif tok == "minesweeping":
            d["weapons"] = [x for x in w if x["kind"] != "minesweeping_gear"]
        elif tok.startswith("main_gun"):
            d["weapons"] = [x for x in w if x["kind"] != "gun"]
        else:
            d["sensors"] = [x for x in s if x not in C.RADAR_SENSORS]
        out.append(("drop_required", d, "req"))
        break
    return out


# --------------------------------------------------------------------------------------------------
# evaluation (false-positive rate on real ships, detection rate on corrupted designs)
# --------------------------------------------------------------------------------------------------
def _load_examples(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            r["gold"] = json.loads(r["messages"][2]["content"])
            r["req"] = json.loads(r["messages"][1]["content"])["req"]
            rows.append(r)
    return rows


def evaluate(rows: list[dict[str, Any]], th: dict[str, Any], margin: float = MARGIN, seed: int = 7) -> dict[str, Any]:
    n = len(rows)
    fp_any = fp_phys = fp_req = 0
    codes: collections.Counter = collections.Counter()
    warn_any = 0
    examples_fp: list[dict[str, Any]] = []
    for r in rows:
        res = validate(r["req"], r["gold"], th, margin=margin)
        phys = validate(None, {**r["gold"], "year": r["req"]["year"]}, th, margin=margin)
        if not res["ok"]:
            fp_any += 1
            for x in res["violations"]:
                codes[x["code"] + ":" + x["field"]] += 1
            if len(examples_fp) < 12:
                examples_fp.append({"id": r["id"], "violations": res["violations"][:3]})
        fp_phys += not phys["ok"]
        fp_req += any(x["code"].startswith("req_") for x in res["violations"])
        warn_any += bool(res["warnings"])
    rng = random.Random(seed)
    det: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0, 0])  # n, any, physics-only(no req), expected-layer
    layer_prefix = {"ratio": ("ratio_", "gun_"), "era": ("era_",), "type": ("type_", "gun_"), "req": ("req_",)}
    for r in rows:
        for name, d, layer in corruptions(rng, r["req"], r["gold"]):
            res = validate(r["req"], d, th, margin=margin)
            phys = validate(None, {**d, "year": r["req"]["year"]}, th, margin=margin)
            rec = det[name]
            rec[0] += 1
            rec[1] += not res["ok"]
            rec[2] += not phys["ok"]
            rec[3] += any(x["code"].startswith(layer_prefix[layer]) for x in res["violations"])
    det_out = {k: {"n": a[0], "detected_any": round(a[1] / a[0], 4), "detected_without_requirements": round(a[2] / a[0], 4), "detected_by_expected_layer": round(a[3] / a[0], 4)}
               for k, a in sorted(det.items())}
    tot = [sum(a[i] for a in det.values()) for i in range(4)]
    return {
        "n_designs": n,
        "false_positive": {"any_violation": round(fp_any / n, 4), "physics_era_type_only": round(fp_phys / n, 4), "requirement_layer": round(fp_req / n, 4),
                           "any_warning": round(warn_any / n, 4), "violation_codes": dict(codes.most_common(15)), "examples": examples_fp},
        "detection": det_out,
        "detection_overall": {"n": tot[0], "any": round(tot[1] / tot[0], 4), "without_requirements": round(tot[2] / tot[0], 4), "expected_layer": round(tot[3] / tot[0], 4)},
    }


def cmd_build(args: argparse.Namespace) -> None:
    import shipknow_history as H  # noqa: E402

    recs = [json.loads(l) for l in (C.V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    sp = json.loads((C.V0_DIR / "splits.json").read_text(encoding="utf-8"))
    assign, group_of = sp["assignments"], sp["group_of"]
    train = [r for r in recs if assign[r["id"]] == "train"]
    events_by_id = None
    if not args.no_events:
        conn = H.open_ro()
        texts = H.class_texts(conn, [int(r["id"][1:]) for r in train])
        conn.close()
        events_by_id = {f"c{rid:05d}": H.events_for_class(row) for rid, row in texts.items()}
    th = build_thresholds(train, group_of, events_by_id, min_n=args.min_n)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(th, ensure_ascii=False, indent=1, sort_keys=False), encoding="utf-8")
    print(json.dumps({"out": str(out), "n_designs": th["built_from"]["n_designs"], "strata": len(th["ratio_strata"]), "first_year": {k: v_["year"] for k, v_ in th["first_year"].items()}},
                     ensure_ascii=False))


def cmd_eval(args: argparse.Namespace) -> None:
    th = load_thresholds(Path(args.thresholds))
    res: dict[str, Any] = {}
    for sp in args.splits:
        rows = _load_examples(Path(args.dir) / f"{sp}.jsonl")
        res[sp] = evaluate(rows, th, margin=args.margin)
        print(sp, json.dumps({k: res[sp][k] for k in ("n_designs", "false_positive", "detection_overall")}, ensure_ascii=False, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default=str(THRESHOLDS_PATH))
    b.add_argument("--min-n", type=int, default=MIN_N)
    b.add_argument("--no-events", action="store_true")
    b.set_defaults(fn=cmd_build)
    e = sub.add_parser("eval")
    e.add_argument("--dir", default=str(C.OUT_DIR))
    e.add_argument("--thresholds", default=str(THRESHOLDS_PATH))
    e.add_argument("--splits", nargs="+", default=["test"])
    e.add_argument("--margin", type=float, default=MARGIN)
    e.add_argument("--out", default=None)
    e.set_defaults(fn=cmd_eval)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
