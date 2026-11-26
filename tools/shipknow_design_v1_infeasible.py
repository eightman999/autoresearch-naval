"""design-v1 の infeasible / relaxed（不可能・緩和）エピソード生成と、relaxed / infeasible の判定基準。

■ 判定基準（`decide_status`、コードで明文化）
  要求に「衝突」（満たせない制約）が 1 つ以上あるとき、次の全てを満たすときだけ status = relaxed、1 つでも外れれば infeasible。
    R1. 全ての衝突が緩和可能（relaxable）。次は緩和不可: hull_type（その年に存在しない艦種）、contradiction（min と max が矛盾）、
        aircraft_zero（その大きさの艦で搭載機を持つ実艦が 1 隻も無い）、type_size（艦種の排水量帯の外）。
    R2. 衝突が 3 つ未満、かつ衝突数 × 2 <= 制約の総数（半分を超える制約が衝突するなら、緩和後の設計は元の要求と別物になる）。
    R3. 任務の中核の制約を緩和しない（主任務 aaw の SAM、asw の対潜兵装・ソナー、carrier_air の航空機）。
    R4. 数値の衝突は、要求値が「実データで到達可能な最良値」を超える割合（gap）が RELAX_GAP_MAX 以内:
        speed 0.30、gun_barrels 1.0、gun_cal 0.6、aircraft 1.0、torpedo 1.0、crew 0.30（crew は下限側の割合）。
  relaxed の design は、緩和した制約だけを満たさず、他の全ての制約と validator（check_design）を満たす実艦ベースの設計。緩和後の値（relaxed_to）は設計の実際の値。
  infeasible の design は null、relaxations は空、conflicts に衝突を全て挙げる。
■ 「不可能」の判定（oracle）
  年代: 能力の初出年（validator の閾値表、train の艦級から）より 10 年以上前の年の要求（validator の許容 5 年より厳しい）。
  数値・装備: train の実艦（同じ coarse 艦種、要求年 + 3 年以内に設計）のうち、その要求の全制約を満たすものが 0 隻。
  生成は「元艦から作った満たせる要求に、1 つ以上の衝突を注入する」方式で、元艦（同じ split）が緩和後の設計になる。注入の大きさは gap が relaxed の帯か
  infeasible の帯のどちらかに入るものだけを使い、中間（0.30〜0.6 など）の曖昧な例は捨てる。
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402
import shipknow_design_v1_data as DD  # noqa: E402
import shipknow_design_v1_gen_basic as G  # noqa: E402

RELAX_GAP_MAX = {"speed": 0.30, "gun_barrels": 1.0, "gun_cal": 0.6, "aircraft": 1.0, "torpedo": 1.0, "crew": 0.30}
NON_RELAXABLE = {"hull_type", "contradiction", "aircraft_zero", "type_size"}
MAX_RELAXED_CONFLICTS = 2
MISSION_CORE = {"aaw": {"sam"}, "asw": {"asw_weapon", "sonar"}, "carrier_air": {"aircraft", "helicopter"}}
# 衝突の「家族」（生成のカテゴリ。test 専用の家族は HOLDOUT_FAMILIES）
FAMILIES = ["era_cap", "era_multi", "era_core", "hull_type", "speed", "gun_barrels", "gun_cal", "aircraft", "torpedo", "crew", "contradiction", "extreme", "type_size"]
FAMILY_WEIGHT = {"era_cap": 16, "era_multi": 6, "era_core": 5, "hull_type": 6, "speed": 14, "gun_barrels": 8, "gun_cal": 8, "aircraft": 10, "torpedo": 6, "crew": 5, "contradiction": 8,
                 "extreme": 5, "type_size": 4}
HOLDOUT_FAMILIES = {"crew", "extreme"}  # test にだけ出す家族（未見の矛盾への一般化）


@dataclass
class Conflict:
    constraint: str
    family: str
    reason: str
    relaxable: bool = True
    gap: float | None = None
    mission_core: bool = False
    requested: Any = None
    relaxed_to: Any = None
    relax_reason: str = ""

    def info(self) -> dict[str, Any]:
        return {"constraint": self.constraint, "family": self.family, "relaxable": self.relaxable, "gap": None if self.gap is None else round(self.gap, 3), "mission_core": self.mission_core}


def decide_status(conflicts: list[Conflict], n_constraints: int) -> str:
    if not conflicts:
        return "feasible"
    if any(not c.relaxable or c.family in NON_RELAXABLE for c in conflicts):
        return "infeasible"
    if len(conflicts) > MAX_RELAXED_CONFLICTS or len(conflicts) * 2 > n_constraints:
        return "infeasible"
    if any(c.mission_core for c in conflicts):
        return "infeasible"
    for c in conflicts:
        lim = RELAX_GAP_MAX.get(c.family)
        if lim is not None and (c.gap is None or round(c.gap, 3) > lim):  # gap is compared at the 3 decimals stored in metadata (conflict_info)
            return "infeasible"
    return "relaxed"


def mission_core(req: dict[str, Any], token: str) -> bool:
    m = (req.get("mission") or [None])[0]
    return token in MISSION_CORE.get(m, set())


def out_design(case: "Case") -> dict[str, Any] | None:
    """assistant の出力に載せる設計（infeasible は null。元艦の設計は relaxed のときだけ載せる）。"""
    return case.design if case.status != "infeasible" else None


def conflict_outputs(conflicts: list[Conflict], status: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    confs = [{"constraint": c.constraint, "reason": c.reason} for c in conflicts]
    rel = [{"constraint": c.constraint, "requested": c.requested, "relaxed_to": c.relaxed_to, "reason": c.relax_reason} for c in conflicts] if status == "relaxed" else []
    return confs, rel


# --------------------------------------------------------------------------------------------------
# era conflicts (also used by modify's year change)
# --------------------------------------------------------------------------------------------------
_ALT = {"sam": "air defence rests on guns and fighters", "ssm": "surface strike rests on guns and torpedoes", "ciws": "close defence uses ordinary light guns",
        "air:helicopter": "aircraft support, if any, is limited to floatplanes", "air_search_radar": "air search is visual", "fire_control_radar": "gunnery uses optical fire control",
        "esm_ecm": "no electronic warfare equipment", "sonar": "no sonar; hydrophones at most", "asw_weapon": "no dedicated anti-submarine weapon", "radar_any": "no radar",
        "rocket_launcher": "no rocket launcher", "ballistic_missile": "no ballistic missiles", "surface_radar": "surface search is visual", "radar": "no radar", "aircraft": "no embarked aircraft"}


def _prop_phrase(p: str) -> str:
    return p.replace("_", " ") + " propulsion"


def era_conflict(req: dict[str, Any], cid: str, year: int, design: dict[str, Any] | None) -> Conflict:
    if cid.startswith("required_propulsion:"):
        p = cid.split(":", 1)[1]
        cap = f"prop:{p}"
        fy = K.first_year(cap)
        actual = ", ".join((design or {}).get("propulsion") or []) or "conventional machinery"
        return Conflict(cid, "era_capability", f"{_prop_phrase(p)} first appears around {fy}; it did not exist in {year}", True, None, False, p, actual,
                        f"uses {actual}, the practical plant of {year}")
    tok = cid.split(":", 1)[1]
    cap = K.TOKEN_CAP.get(tok, tok)
    fy = K.first_year(cap)
    name = K.CAP_NAME.get(cap, tok)
    return Conflict(cid, "era_capability", f"{name} first appear around {fy}; none were available in {year}", True, None, mission_core(req, tok), name, "none", _ALT.get(cap, "dropped"))


def era_conflicts_to_status(D: DD.Data, req: dict[str, Any], cids: list[str], year: int, design: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    confs = [era_conflict(req, c, year, design) for c in cids]
    status = decide_status(confs, len(K.constraint_ids(req)))
    out_c, out_r = conflict_outputs(confs, status)
    return status, out_c, out_r, [c.info() for c in confs]


# --------------------------------------------------------------------------------------------------
# oracle (train real ships)
# --------------------------------------------------------------------------------------------------
YEAR_AHEAD = 3


def real_satisfying(D: DD.Data, req: dict[str, Any]) -> list[int]:
    """train の実艦（同じ coarse 艦種、要求年 + YEAR_AHEAD 年以内）で、要求の全制約を満たすもの。"""
    return D.idx_train.satisfying(req, year_hi=req["year"] + YEAR_AHEAD)


def best_of(D: DD.Data, req: dict[str, Any], drop: list[str], fn) -> float | None:
    """制約 drop を外した要求を満たす train 実艦の fn(design) の最大値（無ければ None）。"""
    r0 = K.apply_relaxations(req, [{"constraint": c} for c in drop])
    xs = [fn(D.idx_train.designs[i]) for i in real_satisfying(D, r0)]
    xs = [x for x in xs if x is not None]
    return max(xs) if xs else None


@dataclass
class Case:
    family: str
    req: dict[str, Any]
    status: str
    conflicts: list[Conflict]
    design: dict[str, Any] | None
    anchor: dict[str, Any] | None
    extra: dict[str, Any] = field(default_factory=dict)
    premise_text: str | None = None


def _anchor_req(D: DD.Data, split: str, rng: random.Random, pred: str, force: set[str] | None = None, drop_fine: bool = True, types: set[str] | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    rec = D.pick(split, rng, pred, types=types)
    if rec is None:
        return None
    design = D.design(rec)
    year = C.request_year(rec)
    req = K.make_req_v1(design, rec["country"], year, rng, force=force)
    req = {k: v for k, v in req.items() if not (drop_fine and k == "ship_type_fine")}
    if not K.check_design(req, design)["ok"]:
        return None
    return rec, design, req


def _tname(req: dict[str, Any]) -> str:
    return C._TYPE_NAME.get(req["ship_type"], req["ship_type"].replace("_", " "))


def _lim_txt(req: dict[str, Any]) -> str:
    return f"{req['max_displacement_t']} t ({req.get('displacement_basis', 'normal')})"


def _rate(rng: random.Random, lo: float, hi: float) -> float:
    return rng.uniform(lo, hi)


# ---- families -------------------------------------------------------------------------------------
ERA_TOKENS = ["sam", "ssm", "ciws", "helicopter", "air_search_radar", "fire_control_radar", "esm_ecm", "sonar", "asw_weapon", "rocket_launcher", "ballistic_missile"]


def _era_candidates(req: dict[str, Any], design: dict[str, Any], year: int) -> list[str]:
    out = []
    for tok in ERA_TOKENS:
        cap = K.TOKEN_CAP[tok]
        if K.cap_early_by(cap, year) >= K.ERA_CLEAR_GAP and tok not in (req.get("required") or []):
            out.append(f"required:{tok}")
    for p in ("gas_turbine", "nuclear"):
        if K.cap_early_by(f"prop:{p}", year) >= K.ERA_CLEAR_GAP and not req.get("required_propulsion"):
            out.append(f"required_propulsion:{p}")
    return out


def _cid_satisfied(cid: str, design: dict[str, Any]) -> bool:
    tok = cid.split(":", 1)[1]
    probe = {"required": [tok]} if cid.startswith("required:") else {"required_propulsion": [tok]}
    return K.satisfies_all(probe, design, True)


def _inject_era(req: dict[str, Any], cids: list[str]) -> dict[str, Any]:
    r = {**req, "required": list(req.get("required") or [])}
    for c in cids:
        if c.startswith("required:"):
            r["required"].append(c.split(":", 1)[1])
        else:
            r["required_propulsion"] = list(r.get("required_propulsion") or []) + [c.split(":", 1)[1]]
    return K.order_req(r)


def fam_era(D: DD.Data, split: str, rng: random.Random, family: str) -> Case | None:
    got = _anchor_req(D, split, rng, "old")
    if got is None:
        return None
    rec, design, req = got
    year = req["year"]
    cands = _era_candidates(req, design, year)
    # tokens that the anchor already carries (e.g. a modern ship) are not conflicts
    cands = [c for c in cands if not _cid_satisfied(c, design)]
    if family == "era_core":
        pick = None
        if year <= 1948:
            req = {**req, "mission": ["aaw"] + [m for m in req.get("mission", []) if m != "aaw"][:1]}
            pick = "required:sam" if "required:sam" in cands else None
        if pick is None:
            return None
        cids = [pick]
    else:
        n = 1 if family == "era_cap" and rng.random() < 0.7 else (2 if family == "era_cap" else rng.choice([3, 3, 4]))
        if len(cands) < n:
            return None
        cids = rng.sample(cands, n)
    req2 = _inject_era(req, cids)
    if K.satisfies_all(req2, design, ignore_hull=True):
        return None
    confs = [era_conflict(req2, c, year, design) for c in cids]
    status = decide_status(confs, len(K.constraint_ids(req2)))
    want = "infeasible" if family in ("era_multi", "era_core") else "relaxed"
    if status != want:
        return None
    return Case(family, req2, status, confs, design if status == "relaxed" else None, rec)


def fam_hull_type(D: DD.Data, split: str, rng: random.Random) -> Case | None:
    types = [t for t, y in D.type_first_year.items() if y >= 1895 and y - 15 >= 1862]
    if not types:
        return None
    t = rng.choice(sorted(types))
    got = _anchor_req(D, split, rng, "any", types={t})
    if got is None:
        return None
    rec, design, req = got
    fy = D.type_first_year[t]
    year = min(fy - rng.randint(15, 40), D.type_min_year.get(t, fy) - YEAR_AHEAD - 1 - rng.randint(0, 10))  # earlier than every real class of this type in train
    if year < 1862:
        return None
    req = {**req, "year": year, "required": [x for x in req.get("required") or [] if x.startswith("main_gun")]}
    for k in ("min_gun_barrels", "min_gun_barrels_cal_mm", "min_aircraft", "min_torpedo_tubes", "required_propulsion"):
        req.pop(k, None)
    req = K.order_req(req)
    name = C._TYPE_NAME.get(t, t.replace("_", " "))
    conf = Conflict("ship_type", "hull_type", f"no {name} existed in {year}; the first real {name} classes appear around {fy}", False, None, False)
    return Case("hull_type", req, "infeasible", [conf], None, rec, {"first_year": fy})


def _drop_all_ext(req: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in req.items() if k not in ("min_gun_barrels", "min_gun_barrels_cal_mm", "min_aircraft", "min_torpedo_tubes", "required_propulsion", "min_displacement_t", "max_speed_kn")}


def fam_speed(D: DD.Data, split: str, rng: random.Random, severe: bool) -> Case | None:
    got = _anchor_req(D, split, rng, "any")
    if got is None:
        return None
    rec, design, req = got
    v0 = design.get("speed_kn")
    if not K._isnum(v0):
        return None
    req = {k: v for k, v in req.items() if k != "max_speed_kn"}
    best = best_of(D, req, ["min_speed_kn"], lambda d: d.get("speed_kn") if K._isnum(d.get("speed_kn")) else None)
    best = max(best or 0, v0)
    if not severe and v0 < 0.92 * best:
        return None
    r = _rate(rng, 1.10, 1.27) if not severe else _rate(rng, 1.8, 2.6)
    vreq = math.ceil(best * r * 2) / 2
    gap = vreq / best - 1
    req2 = K.order_req({**req, "min_speed_kn": K.num(vreq)})
    if real_satisfying(D, req2) or K.satisfies_all(req2, design, True):
        return None
    reason = f"requested {K.num(vreq)} kn exceeds the fastest comparable real {_tname(req)} (up to {_lim_txt(req)}, designs through {req['year'] + YEAR_AHEAD}), which reached {K.num(best)} kn"
    conf = Conflict("min_speed_kn", "speed", reason, True, gap, False, K.num(vreq), v0, f"closest attainable is {K.num(v0)} kn, like the fastest comparable ships")
    return Case("speed", req2, decide_status([conf], len(K.constraint_ids(req2))), [conf], design, rec, {"best": best})


def _count_family(D: DD.Data, split: str, rng: random.Random, severe: bool, fam: str) -> Case | None:
    """gun_barrels / torpedo: 砲身数・魚雷管数の下限が、その大きさの実艦の最大を超える。"""
    pred = "barrels" if fam == "gun_barrels" else "tubes"
    got = _anchor_req(D, split, rng, pred, force={"barrels" if fam == "gun_barrels" else "tubes"})
    if got is None:
        return None
    rec, design, req = got
    cid = "min_gun_barrels" if fam == "gun_barrels" else "min_torpedo_tubes"
    cal = None
    if fam == "gun_barrels":
        g = C.main_gun_mm(design)
        grid = [x for x in C.GUN_GRID if g and x <= g]
        cal = grid[-1] if grid else None
        req = {k: v for k, v in req.items() if k not in ("min_gun_barrels", "min_gun_barrels_cal_mm")}
        n0 = X.gun_barrels(design, cal)
        count = lambda d: float(X.gun_barrels(d, cal))  # noqa: E731
    else:
        req = {k: v for k, v in req.items() if k != "min_torpedo_tubes"}
        n0 = X.torpedo_tubes(design)
        count = lambda d: float(X.torpedo_tubes(d))  # noqa: E731
    if n0 < 2:
        return None
    best = best_of(D, req, [], count)
    best = max(best or 0, n0)
    if not severe and n0 < 0.7 * best:
        return None
    r = _rate(rng, 1.34, 1.95) if not severe else _rate(rng, 3.0, 5.0)
    nreq = max(int(math.ceil(best * r)), int(best) + 2)
    gap = nreq / best - 1
    req2 = {**req, cid: nreq}
    if cal and fam == "gun_barrels":
        req2["min_gun_barrels_cal_mm"] = cal
    req2 = K.order_req(req2)
    if real_satisfying(D, req2) or K.satisfies_all(req2, design, True):
        return None
    what = f"gun barrels of {cal:g} mm or larger" if fam == "gun_barrels" and cal else ("gun barrels" if fam == "gun_barrels" else "torpedo tubes")
    reason = f"requested {nreq} {what}; real {_tname(req)} classes up to {_lim_txt(req)} carry at most {int(best)}"
    conf = Conflict(cid, "gun_barrels" if fam == "gun_barrels" else "torpedo", reason, True, gap, False, nreq, int(n0), f"closest attainable is {int(n0)} (the most carried at this size)")
    return Case(fam, req2, decide_status([conf], len(K.constraint_ids(req2))), [conf], design, rec, {"best": best})


def fam_gun_cal(D: DD.Data, split: str, rng: random.Random, severe: bool) -> Case | None:
    got = _anchor_req(D, split, rng, "gun_big")
    if got is None:
        return None
    rec, design, req = got
    c0 = C.main_gun_mm(design)
    req = {k: v for k, v in req.items() if k not in ("min_gun_barrels", "min_gun_barrels_cal_mm")}
    req["required"] = [t for t in req.get("required") or [] if not t.startswith("main_gun")]
    best = best_of(D, req, [], lambda d: C.main_gun_mm(d))
    best = max(best or 0, c0)
    if not severe and c0 < 0.85 * best:
        return None
    lo, hi = (1.2, 1.55) if not severe else (2.2, 4.0)
    cands = [g for g in C.GUN_GRID if lo * best <= g <= hi * best]
    if not cands:
        return None
    newc = rng.choice(cands)
    gap = newc / best - 1
    req2 = K.order_req({**req, "required": list(req["required"]) + [f"main_gun>={newc}mm"]})
    if real_satisfying(D, req2) or K.satisfies_all(req2, design, True):
        return None
    reason = f"requested guns of at least {newc} mm; real {_tname(req)} classes up to {_lim_txt(req)} carry at most {K.num(best)} mm"
    conf = Conflict("required:main_gun>=%dmm" % newc, "gun_cal", reason, True, gap, False, f"{newc} mm", f"{K.num(c0)} mm", f"largest practical main gun at this size is about {K.num(c0)} mm")
    return Case("gun_cal", req2, decide_status([conf], len(K.constraint_ids(req2))), [conf], design, rec, {"best": best})


def fam_aircraft(D: DD.Data, split: str, rng: random.Random, mode: str) -> Case | None:
    if mode == "zero":
        got = _anchor_req(D, split, rng, "tiny")
        if got is None:
            return None
        rec, design, req = got
        if X.aircraft_count(design) > 0 or req["year"] < 1950:
            return None
        n = rng.choice([2, 3, 4])
        req2 = {k: v for k, v in req.items() if k != "min_aircraft"}
        req2["required"] = list(req2.get("required") or []) + ["helicopter"]
        req2["min_aircraft"] = n
        req2 = K.order_req(req2)
        best = best_of(D, req2, ["min_aircraft", "required:helicopter"], lambda d: float(X.aircraft_count(d)))
        if (best or 0) > 0 or real_satisfying(D, req2):
            return None
        reason = f"requested {n} embarked helicopters on at most {_lim_txt(req)}; no real {_tname(req)} of that size has ever carried an aircraft"
        conf = Conflict("min_aircraft", "aircraft_zero", reason, False, None, False)
        return Case("aircraft", req2, "infeasible", [conf], None, rec, {"mode": mode})
    got = _anchor_req(D, split, rng, "aircraft", force={"aircraft"})
    if got is None:
        return None
    rec, design, req = got
    n0 = X.aircraft_count(design)
    req = {k: v for k, v in req.items() if k != "min_aircraft"}
    best = best_of(D, req, [], lambda d: float(X.aircraft_count(d)))
    best = max(best or 0, n0)
    severe = mode == "severe"
    if not severe and n0 < 0.7 * best:
        return None
    r = _rate(rng, 1.34, 1.95) if not severe else _rate(rng, 3.0, 5.0)
    nreq = max(int(math.ceil(best * r)), int(best) + 1)
    gap = nreq / best - 1
    req2 = K.order_req({**req, "min_aircraft": nreq})
    if real_satisfying(D, req2) or K.satisfies_all(req2, design, True):
        return None
    reason = f"requested {nreq} embarked aircraft; real {_tname(req)} classes up to {_lim_txt(req)} carry at most {int(best)}"
    conf = Conflict("min_aircraft", "aircraft", reason, True, gap, False, nreq, n0, f"closest attainable is {n0} aircraft, the most carried at this size")
    return Case("aircraft", req2, decide_status([conf], len(K.constraint_ids(req2))), [conf], design, rec, {"mode": mode, "best": best})


def fam_crew(D: DD.Data, split: str, rng: random.Random, severe: bool) -> Case | None:
    got = _anchor_req(D, split, rng, "any")
    if got is None:
        return None
    rec, design, req = got
    c0 = design.get("complement")
    if not K._isnum(c0) or c0 < 8:
        return None
    req = {k: v for k, v in req.items() if k != "max_complement"}
    r0 = K.apply_relaxations(req, [])
    xs = [D.idx_train.designs[i].get("complement") for i in real_satisfying(D, r0)]
    xs = [x for x in xs if K._isnum(x)]
    best = min(xs + [c0]) if xs else c0
    if not severe and c0 > 1.12 * best:
        return None
    r = _rate(rng, 0.72, 0.92) if not severe else _rate(rng, 0.15, 0.35)
    creq = int(max(1, math.floor(best * r)))
    gap = (best - creq) / best
    req2 = K.order_req({**req, "max_complement": creq})
    if real_satisfying(D, req2) or K.satisfies_all(req2, design, True):
        return None
    reason = f"crew limit {creq} is below the smallest crew of any comparable real {_tname(req)} ({int(best)} on up to {_lim_txt(req)})"
    conf = Conflict("max_complement", "crew", reason, True, gap, False, creq, K.num(c0), f"closest attainable crew is {K.num(c0)}, the smallest seen at this size")
    return Case("crew", req2, decide_status([conf], len(K.constraint_ids(req2))), [conf], design, rec, {"best": best})


def fam_contradiction(D: DD.Data, split: str, rng: random.Random) -> Case | None:
    got = _anchor_req(D, split, rng, "any")
    if got is None:
        return None
    rec, design, req = got
    req = _drop_all_ext(req)
    if rng.random() < 0.6:
        mx = req["max_displacement_t"]
        mn = int(C._nice_round(mx * rng.uniform(1.25, 2.2)))
        req2 = {**req, "min_displacement_t": mn}
        cid, reason = "min_displacement_t + max_displacement_t", f"minimum displacement {mn} t exceeds the maximum {mx} t ({req.get('displacement_basis', 'normal')})"
    else:
        lo = req.get("min_speed_kn")
        if lo is None:
            return None
        hi = K.num(max(3, math.floor(lo * rng.uniform(0.5, 0.85))))
        req2 = {**req, "max_speed_kn": hi}
        cid, reason = "min_speed_kn + max_speed_kn", f"minimum speed {lo} kn exceeds the maximum speed {hi} kn"
    req2 = K.order_req(req2)
    conf = Conflict(cid, "contradiction", reason, False, None, False)
    return Case("contradiction", req2, "infeasible", [conf], None, rec)


def fam_extreme(D: DD.Data, split: str, rng: random.Random) -> Case | None:
    got = _anchor_req(D, split, rng, "tiny")
    if got is None:
        return None
    rec, design, req = got
    if req["year"] < 1940:
        return None
    req = _drop_all_ext(req)
    req2 = {**req, "required": [t for t in req.get("required") or [] if not t.startswith("main_gun")] + ["main_gun>=203mm", "helicopter"], "min_gun_barrels": rng.choice([6, 8, 9]),
            "min_gun_barrels_cal_mm": 203, "min_aircraft": rng.choice([2, 3])}
    req2 = K.order_req(req2)
    if real_satisfying(D, req2):
        return None
    lim = _lim_txt(req)
    name = _tname(req)
    confs = [
        Conflict("required:main_gun>=203mm", "gun_cal", f"203 mm guns are impossible on a {name} of at most {lim}; real ships this small carry far lighter guns", True, 9.0, False),
        Conflict("min_gun_barrels", "gun_barrels", f"{req2['min_gun_barrels']} barrels of 203 mm or larger would outweigh the whole {lim} hull", True, 9.0, False),
        Conflict("min_aircraft", "aircraft_zero", f"{req2['min_aircraft']} helicopters cannot be embarked on at most {lim}", False, None, False),
    ]
    return Case("extreme", req2, "infeasible", confs, None, rec)


def fam_type_size(D: DD.Data, split: str, rng: random.Random) -> Case | None:
    got = _anchor_req(D, split, rng, "big")
    if got is None:
        return None
    rec, design, req = got
    t = req["ship_type"]
    if t not in ("battleship", "aircraft_carrier", "heavy_cruiser", "light_cruiser", "battlecruiser", "armored_protected_cruiser"):
        return None
    req = _drop_all_ext(req)
    basis = req.get("displacement_basis", "normal")
    sizes = sorted(D.idx_train.disp(basis)[D.idx_train.type == t])
    sizes = [s for s in sizes if s == s]
    if len(sizes) < 10:
        return None
    lo = sizes[0]
    lim = int(C._nice_round(max(40, lo * rng.uniform(0.08, 0.3))))
    req2 = K.order_req({**req, "max_displacement_t": lim, "min_speed_kn": req.get("min_speed_kn", 20)})
    req2.pop("min_displacement_t", None)
    if real_satisfying(D, req2):
        return None
    reason = f"a {_tname(req)} of at most {lim} t ({basis}) is not possible; the smallest real {_tname(req)} displaced about {int(lo)} t"
    conf = Conflict("max_displacement_t", "type_size", reason, False, None, False)
    return Case("type_size", req2, "infeasible", [conf], None, rec)


def build_case(D: DD.Data, split: str, rng: random.Random, family: str | None = None) -> Case | None:
    fams = [f for f in FAMILIES if split == "test" or f not in HOLDOUT_FAMILIES]
    if family is None:
        family = rng.choices(fams, weights=[FAMILY_WEIGHT[f] * (4 if f in HOLDOUT_FAMILIES else 1) for f in fams])[0]  # test-only families are over-sampled in test
    for _ in range(12):
        c = _build_family(D, split, rng, family)
        if c is not None:
            return c
    return None


def _build_family(D: DD.Data, split: str, rng: random.Random, f: str) -> Case | None:
    if f in ("era_cap", "era_multi", "era_core"):
        return fam_era(D, split, rng, f)
    if f == "hull_type":
        return fam_hull_type(D, split, rng)
    if f == "speed":
        return fam_speed(D, split, rng, severe=rng.random() < 0.4)
    if f in ("gun_barrels", "torpedo"):
        return _count_family(D, split, rng, rng.random() < 0.4, f)
    if f == "gun_cal":
        return fam_gun_cal(D, split, rng, rng.random() < 0.4)
    if f == "aircraft":
        return fam_aircraft(D, split, rng, rng.choice(["zero", "relaxed", "severe"]))
    if f == "crew":
        return fam_crew(D, split, rng, rng.random() < 0.4)
    if f == "contradiction":
        return fam_contradiction(D, split, rng)
    if f == "extreme":
        return fam_extreme(D, split, rng)
    if f == "type_size":
        return fam_type_size(D, split, rng)
    return None


def case_episode(D: DD.Data, split: str, case: Case, rng: random.Random, subtype: str | None = None) -> dict[str, Any]:
    req = case.req
    text = K.request_text_v1(req, rng.randrange(6))
    srcs = [case.anchor["id"]] if case.anchor else []
    nbr = D.neighbors(req, srcs)
    ep = G.new_episode("infeasible", subtype or case.family, split)
    ep["messages"].append(G.msg("user", G.user_turn(text, req, nbr)))
    confs, rels = conflict_outputs(case.conflicts, case.status)
    od = out_design(case)
    G.add_asst(ep, G.asst_turn(case.status, od, confs, rels), status=case.status, origin="real" if od else "none",
               source=case.anchor["id"] if od else None, role="answer", train=True, family=case.family, conflict_info=[c.info() for c in case.conflicts])
    for s in srcs:
        G.add_source(ep, s, D)
    ep["meta"]["family"] = case.family
    if case.family in HOLDOUT_FAMILIES:
        ep["meta"]["holdout"].append("family:" + case.family)
    return G.fin_meta(ep, D, req)


def gen_infeasible(D: DD.Data, split: str, idx: int) -> dict[str, Any] | None:
    rng = K.sub_rng(G.SEED, "infeasible", split, idx)
    case = build_case(D, split, rng)
    if case is None:
        return None
    if split != "test" and K.is_heldout_combo(case.req):
        return None
    ep = case_episode(D, split, case, rng)
    if not G.split_check(D, ep):
        return None
    return ep
