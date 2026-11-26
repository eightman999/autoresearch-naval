"""design-v1 の single / modify / context エピソード生成。

エピソードの形（全カテゴリ共通）:
  {"category", "subtype", "split", "messages": [system, user, assistant, ...], "meta": {...}}（id と最終的な metadata は orchestrator が付ける）
user の content は JSON 文字列 {"request": 自然文, "req": 構造化要求, ["reference": 過去の設計,] ["nbr": 近傍]}、assistant の content は
{"status", "conflicts", "relaxations", "design"} の JSON 文字列。modify の 2 ターン目以降の user は {"request": 変更の自然文, "req": 更新後の全要求}
（国・年の変更では nbr も付ける）。

modify（物理連動）: 変更後の設計 B は (1) A と近い実艦（同 split・同艦種・近い年・近い規模・A の艦級グループ外）で新しい要求を全て満たすもの（real）、
無ければ (2) A を shipknow_design_v1_ops の物理スケーリング（排水量・速力から寸法・出力・乗員を層の回帰で再計算、武装・航空機・推進の重量を排水量へ）で
作り変えたもの（scaled）。どちらも check_design（validator + 要求充足）に合格したものだけを採る。
"""

from __future__ import annotations

import copy
import math
import random
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402
import shipknow_design_v1_data as DD  # noqa: E402
import shipknow_design_v1_ops as O  # noqa: E402

SEED = 20261008
import collections as _co
FAIL: "_co.Counter[str]" = _co.Counter()  # デバッグ用: 生成失敗の理由（orchestrator が集計して manifest に残す）


def fail(reason: str) -> None:
    FAIL[reason] += 1
REAL_PREF = 0.7  # real が見つかったときに real を使う確率


# --------------------------------------------------------------------------------------------------
# episode assembly
# --------------------------------------------------------------------------------------------------
def msg(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def user_turn(request: str, req: dict[str, Any] | None, nbr: list[dict[str, Any]] | None = None, reference: dict[str, Any] | None = None, **extra: Any) -> str:
    o: dict[str, Any] = {"request": request}
    if req is not None:
        o["req"] = req
    if reference is not None:
        o["reference"] = reference
    if nbr:
        o["nbr"] = nbr
    o.update(extra)
    return K.jdump(o)


def asst_turn(status: str, design: dict[str, Any] | None, conflicts: list[dict[str, Any]] | None = None, relaxations: list[dict[str, Any]] | None = None) -> str:
    return K.dump_output(status, design, conflicts, relaxations)


def new_episode(category: str, subtype: str, split: str) -> dict[str, Any]:
    return {"category": category, "subtype": subtype, "split": split, "messages": [msg("system", K.SYSTEM_V1)],
            "meta": {"sources": [], "turns": [], "train_turns": [], "unseen_combo": False, "holdout": []}}


def add_asst(ep: dict[str, Any], content: str, *, status: str, origin: str, source: str | None, role: str, train: bool, waivers: list[str] | None = None, **extra: Any) -> None:
    ep["messages"].append(msg("assistant", content))
    idx = len(ep["messages"]) - 1
    ep["meta"]["turns"].append({"idx": idx, "status": status, "origin": origin, "source": source, "role": role, "waivers": waivers or [], **extra})
    if train:
        ep["meta"]["train_turns"].append(idx)


def add_source(ep: dict[str, Any], rid: str | None, D: DD.Data) -> None:
    if rid and rid not in ep["meta"]["sources"]:
        ep["meta"]["sources"].append(rid)


def fin_meta(ep: dict[str, Any], D: DD.Data, req: dict[str, Any], **extra: Any) -> dict[str, Any]:
    m = ep["meta"]
    m["groups"] = sorted({D.group_of[s] for s in m["sources"] if s in D.group_of})
    m["turns_user"] = sum(1 for x in ep["messages"] if x["role"] == "user")
    m["country"], m["year"], m["ship_type"] = req.get("country"), req.get("year"), req.get("ship_type")
    m["status_seq"] = [t["status"] for t in m["turns"]]
    m["req_keys"] = sorted(k for k in req if k not in ("country", "year", "ship_type", "ship_type_fine", "mission", "displacement_basis"))
    m.update(extra)
    return ep


def split_check(D: DD.Data, ep: dict[str, Any]) -> bool:
    return all(D.assign.get(s) == ep["split"] for s in ep["meta"]["sources"])


# --------------------------------------------------------------------------------------------------
# single
# --------------------------------------------------------------------------------------------------
SINGLE_FOCUS = [(None, 0.22), ("barrels", 0.30), ("tubes", 0.20), ("aircraft", 0.16), ("prop", 0.12)]
FOCUS_PRED = {None: "any", "barrels": "barrels", "tubes": "tubes", "aircraft": "aircraft", "prop": "prop"}


def pick_focus(rng: random.Random) -> str | None:
    return rng.choices([f for f, _ in SINGLE_FOCUS], weights=[w for _, w in SINGLE_FOCUS])[0]


def make_request(D: DD.Data, split: str, rec: dict[str, Any], rng: random.Random, force: set[str] | None = None, tries: int = 8) -> tuple[dict[str, Any], bool] | None:
    """元艦から要求を作る。train / valid には test 専用の組み合わせ（is_heldout_combo）を出さない。test では半分の例で、組み合わせ未見の要求を探す
    （見つからない艦では普通の要求）。戻り値 (req, unseen)。"""
    d = D.designs[rec["id"]]
    year = C.request_year(rec)
    want_unseen = split == "test" and rng.random() < 0.5
    last = None
    n_try = 40 if want_unseen else tries
    for t in range(n_try):
        f = set(force or ())
        if want_unseen and t >= 8:
            f |= set(rng.sample(["barrels", "tubes", "aircraft", "prop"], 2))
        req = K.make_req_v1(d, rec["country"], year, rng, force=f or None)
        ho = K.is_heldout_combo(req)
        last = (req, ho)
        if split != "test" and not ho:
            return req, False
        if split == "test" and ho == want_unseen:
            return req, ho
    if split == "test" and last:
        return last[0], last[1]
    return None


def gen_single(D: DD.Data, split: str, idx: int) -> dict[str, Any] | None:
    rng = K.sub_rng(SEED, "single", split, idx)
    focus = pick_focus(rng)
    rec = D.pick(split, rng, FOCUS_PRED[focus])
    if rec is None:
        return None
    r = make_request(D, split, rec, rng, force={focus} if focus else None)
    if r is None:
        return None
    req, unseen = r
    design = D.design(rec)
    if not K.check_design(req, design)["ok"]:
        return None
    text = K.request_text_v1(req, rng.randrange(6))
    nbr = D.neighbors(req, [rec["id"]])
    ep = new_episode("single", focus or "plain", split)
    ep["messages"].append(msg("user", user_turn(text, req, nbr)))
    add_asst(ep, asst_turn("feasible", design), status="feasible", origin="real", source=rec["id"], role="answer", train=True)
    add_source(ep, rec["id"], D)
    ep["meta"]["unseen_combo"] = unseen
    return fin_meta(ep, D, req)


# --------------------------------------------------------------------------------------------------
# modify
# --------------------------------------------------------------------------------------------------
def _sentence(pieces: list[str], rng: random.Random) -> str:
    body = pieces[0] if len(pieces) == 1 else ", ".join(pieces[:-1]) + " and " + pieces[-1]
    forms = [("Now {}.", False), ("Change of plan: {}.", True), ("Revise the design: {}. Keep everything else you can.", True), ("Update: {}.", True), ("Same project, but {}.", False),
             ("Please revise it: {}.", False), ("Good. Next iteration: {}.", True), ("Now {}; keep the rest of the design as it is where possible.", False)]
    f, cap = rng.choice(forms)
    body = body[0].upper() + body[1:] if cap else body[0].lower() + body[1:]
    return f.format(body)


class State:
    """modify の 1 時点の状態: 要求・設計・元の実艦 id（real のとき）・排他すべき艦級グループ。"""

    def __init__(self, req: dict[str, Any], design: dict[str, Any], rec_id: str | None, groups: set[str], year: int, country: str):
        self.req, self.design, self.rec_id, self.groups, self.year, self.country = req, design, rec_id, groups, year, country

    @property
    def basis(self) -> str:
        return self.req.get("displacement_basis", "normal")

    @property
    def disp(self) -> float:
        return O.disp_in_basis(self.design, self.basis) or 0.0


class Change:
    def __init__(self, ctype: str, req_B: dict[str, Any], pieces: list[str], scaled: Callable[[], dict[str, Any] | None] | None = None,
                 target_disp: float | None = None, year: int | None = None, country: str | None = None, real_only: bool = False, may_raise_limit: bool = True,
                 relax_tokens: list[str] | None = None, extra: dict[str, Any] | None = None):
        self.ctype, self.req_B, self.pieces, self.scaled = ctype, req_B, pieces, scaled
        self.target_disp, self.year, self.country, self.real_only, self.may_raise_limit = target_disp, year, country, real_only, may_raise_limit
        self.relax_tokens, self.extra = relax_tokens or [], extra or {}


def nice_lim(x: float) -> float | int:
    return K.num(C._nice_round(x))


def _speed_round(v: float) -> float:
    return round(v * 2) / 2


def _grid_above(cal: float, lo_ratio: float = 1.2, hi_ratio: float = 2.2) -> list[int]:
    return [g for g in C.GUN_GRID if lo_ratio * cal <= g <= hi_ratio * cal]


def _cap_ok(cap: str, year: int, margin: int = 2) -> bool:
    fy = K.first_year(cap)
    return fy is not None and year >= fy + margin


def h_disp_down(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    dv = st.disp
    if dv < 30:
        return None
    lim = nice_lim(dv * (1 - rng.uniform(0.12, 0.28)))
    if lim <= (st.req.get("min_displacement_t") or 0) or lim < 15:
        return None
    req_B = {**st.req, "max_displacement_t": lim}

    def scaled() -> dict[str, Any] | None:
        k = lim * rng.uniform(0.93, 0.985) / dv
        d1 = O.shrink_weapons(st.design, k, req_B)
        return O.rederive(d1, k, None, st.year)

    return Change("disp_down", req_B, [f"cut the displacement limit to {lim} t ({st.basis})"], scaled, target_disp=lim * 0.95, may_raise_limit=False)


def h_speed_up(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    v = st.design.get("speed_kn")
    if not K._isnum(v) or v < 6:
        return None
    new_min = _speed_round(v + rng.uniform(3, 8))
    if new_min > v * 1.45 + 2:
        return None
    req_B = {**st.req, "min_speed_kn": K.num(new_min)}
    if req_B.get("max_speed_kn") is not None and req_B["max_speed_kn"] < new_min + 1:
        req_B["max_speed_kn"] = K.num(new_min + rng.choice([2, 3, 4]))
    top = (req_B.get("max_speed_kn") or 1e9)

    def scaled() -> dict[str, Any] | None:
        vn = min(_speed_round(new_min + rng.uniform(0, 1.0)), top)
        for stretch in (1.0, 1.04, 1.08, 1.14):
            d1 = O.rederive(st.design, 1.0, vn, st.year, stretch=stretch)
            P0, P1, D0 = st.design.get("power_hp"), d1.get("power_hp"), O.ref_disp(st.design)
            k = 1.0 + (min(0.12, 0.0016 * (P1 - P0) / D0) if K._isnum(P0) and K._isnum(P1) and D0 else 0.03)
            cand = O.rederive(st.design, k, vn, st.year, stretch=stretch)
            if K.check_design(req_B, cand, th=D.th)["ok"] or stretch == 1.14:
                return cand
        return None

    return Change("speed_up", req_B, [f"raise the minimum speed to {K.num(new_min)} kn"], scaled, target_disp=st.disp * 1.03)


def h_speed_down(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    v = st.design.get("speed_kn")
    if not K._isnum(v) or v < 12:
        return None
    cur_min = st.req.get("min_speed_kn")
    base_v = min(v, cur_min) if K._isnum(cur_min) else v
    new_min = max(5.0, _speed_round(base_v - rng.uniform(4, 10)))
    if new_min >= base_v - 2:
        return None
    req_B = {**st.req, "min_speed_kn": K.num(new_min)}
    req_B.pop("max_speed_kn", None) if st.req.get("max_speed_kn") is not None and st.req["max_speed_kn"] < new_min else None

    def scaled() -> dict[str, Any] | None:
        vn = _speed_round(new_min + rng.uniform(0, 1.5))
        cap = req_B.get("max_speed_kn")
        if cap is not None:
            vn = min(vn, cap)
        return O.rederive(st.design, 0.97, vn, st.year)

    return Change("speed_down", req_B, [f"lower the minimum speed to {K.num(new_min)} kn to save power"], scaled, target_disp=st.disp * 0.95, may_raise_limit=False)


def h_endurance_up(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    e = st.design.get("endurance_nm")
    if not K._isnum(e) or e < 100 or e > 20000:
        return None
    new_min = rng.uniform(1.3, 1.8) * e
    step = 50 if new_min < 1000 else 100
    new_min = int(math.floor(new_min / step) * step)
    req_B = {**st.req, "min_endurance_nm": new_min}

    def scaled() -> dict[str, Any] | None:
        e_new = O.rnd("endurance_nm", new_min * rng.uniform(1.0, 1.08))
        k = 1.0 + 0.05 * (e_new / e - 1)
        d2 = O.rederive(st.design, k, None, st.year)
        d2["endurance_nm"] = e_new
        return d2

    return Change("endurance_up", req_B, [f"require at least {new_min} nm of endurance"], scaled, target_disp=st.disp * 1.04)


def h_gun_cal_up(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    g = O.main_gun_item(st.design)
    if g is None or st.design["ship_type"] == "submarine":
        return None
    opts = _grid_above(g["cal"])
    cap = D.th.get("type_gun_max_mm", {}).get(st.design["ship_type"])
    opts = [o for o in opts if cap is None or o <= cap]
    if not opts:
        return None
    newc = rng.choice(opts)
    req_B = {**st.req}
    req_B["required"] = [t for t in req_B.get("required") or [] if not t.startswith("main_gun")] + [f"main_gun>={newc}mm"]

    def scaled() -> dict[str, Any] | None:
        d1 = O.clone(st.design)
        nb_min = max(req_B.get("min_gun_barrels") or 0, 2 if O.barrels_of(g) >= 2 else 1)
        dW = O.set_main_caliber(d1, newc, barrels=nb_min if O.barrels_of(g) >= nb_min else None)
        return O.scale_weight(d1, max(dW, 0.0), None, st.year, crew_extra=2)

    return Change("gun_cal_up", req_B, [f"require main guns of at least {newc} mm"], scaled, target_disp=st.disp * 1.08)


def h_gun_barrels_up(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    g = O.main_gun_item(st.design)
    if g is None or g["cal"] < 40 or st.design["ship_type"] == "submarine":
        return None
    cal = st.req.get("min_gun_barrels_cal_mm")
    if cal is None:
        grid = [x for x in C.GUN_GRID if x <= g["cal"]]
        cal = grid[-1] if grid and rng.random() < 0.6 else None
    cur = X.gun_barrels(st.design, cal)
    extra = rng.choice([2, 2, 3, 4])
    new_min = cur + extra
    req_B = {**st.req, "min_gun_barrels": new_min}
    if cal:
        req_B["min_gun_barrels_cal_mm"] = cal
    txt = f"carry at least {new_min} gun barrels" + (f" of {cal:g} mm or larger" if cal else "")

    def scaled() -> dict[str, Any] | None:
        d1 = O.clone(st.design)
        dW = O.add_barrels(d1, extra, g["cal"] if cal and g["cal"] >= cal else None)
        return O.scale_weight(d1, dW, None, st.year, crew_extra=4 * extra)

    return Change("gun_barrels_up", req_B, [txt], scaled, target_disp=st.disp * 1.07)


def _add_cap_handler(ctype: str, token: str, cap: str, kind: str, text: str, min_d: float, min_year_margin: int = 2) -> Callable[[State, random.Random, DD.Data], Change | None]:
    def h(st: State, rng: random.Random, D: DD.Data) -> Change | None:
        if st.design["ship_type"] == "submarine" or K.satisfies_all({"required": [token]}, st.design, True) or token in (st.req.get("required") or []):
            return None
        if st.disp < min_d or not _cap_ok(cap, st.year, min_year_margin):
            return None
        req_B = {**st.req, "required": list(st.req.get("required") or []) + [token]}

        def scaled() -> dict[str, Any] | None:
            d1 = O.clone(st.design)
            dW = O.add_launcher(d1, kind, rng.randrange(2))
            return O.scale_weight(d1, dW, None, st.year, crew_extra=max(6, 0.01 * O.ref_disp(st.design)))

        return Change(ctype, req_B, [text], scaled, target_disp=st.disp * 1.06)

    return h


h_add_sam = _add_cap_handler("add_sam", "sam", "sam", "sam", "add surface-to-air missiles", 600)
h_add_ssm = _add_cap_handler("add_ssm", "ssm", "ssm", "ssm", "add anti-ship missiles", 100)
h_add_ciws = _add_cap_handler("add_ciws", "ciws", "ciws", "ciws", "add a close-in weapon system", 800, 1)


def h_add_heli(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    d = st.design
    if X.aircraft_count(d) > 0 or "helicopter" in (d.get("air") or []) or st.disp < 1500 or st.year < 1958 or d["ship_type"] in ("submarine", "battleship", "battlecruiser", "torpedo_boat"):
        return None
    n = 2 if st.disp >= 4000 and rng.random() < 0.4 else 1
    req_B = {**st.req, "required": list(st.req.get("required") or []) + ["helicopter"]}
    if n >= 2 or rng.random() < 0.3:
        req_B["min_aircraft"] = n
    piece = "add a helicopter" if n == 1 and "min_aircraft" not in req_B else f"embark at least {n} helicopter{'s' if n > 1 else ''}"

    def scaled() -> dict[str, Any] | None:
        d1 = O.clone(d)
        dW = O.set_aircraft(d1, n)
        L0 = d.get("length_m") or 0
        return O.scale_weight(d1, dW, None, st.year, crew_extra=8 * n, stretch=1.03 if L0 < 70 else 1.0)

    return Change("add_heli", req_B, [piece], scaled, target_disp=st.disp * 1.07)


def h_aircraft_up(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    d = st.design
    na = X.aircraft_count(d)
    if na < 1 or "helicopter" not in (d.get("air") or []) or st.disp < 1500:
        return None
    new_n = na + rng.choice([1, 1, 2])
    req_B = {**st.req, "min_aircraft": new_n}
    if "helicopter" not in (req_B.get("required") or []) and rng.random() < 0.5:
        req_B["required"] = list(req_B.get("required") or []) + ["helicopter"]

    def scaled() -> dict[str, Any] | None:
        d1 = O.clone(d)
        dW = O.set_aircraft(d1, new_n)
        return O.scale_weight(d1, dW + 8.0, None, st.year, crew_extra=8 * (new_n - na))

    return Change("aircraft_up", req_B, [f"embark at least {new_n} aircraft"], scaled, target_disp=st.disp * 1.05)


def h_crew_down(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    c = st.design.get("complement")
    if not K._isnum(c) or c < 12:
        return None
    lim = int(max(5, C._nice_round(c * rng.uniform(0.72, 0.88))))
    if lim >= c:
        return None
    req_B = {**st.req, "max_complement": lim}

    def scaled() -> dict[str, Any] | None:
        d1 = O.reduce_light_weapons(st.design, 0.4, req_B)
        d2 = O.rederive(d1, 0.985, None, st.year)
        d2["complement"] = O.rnd("complement", max(2, lim * rng.uniform(0.9, 0.99)))
        return d2

    return Change("crew_down", req_B, [f"cut the crew limit to {lim}"], scaled, target_disp=st.disp * 0.98, may_raise_limit=False)


PROP_EFFECT = {"gas_turbine": (0.97, 0.8, 0.95), "diesel": (1.03, 1.35, 0.95), "steam_turbine": (1.0, 0.9, 1.0), "electric_motor": (1.02, 0.7, 1.0), "nuclear": (1.12, None, 1.1)}


def h_propulsion(st: State, rng: random.Random, D: DD.Data) -> Change | None:
    d = st.design
    cur = set(d.get("propulsion") or [])
    if not cur:
        return None
    cands = []
    for p in ("gas_turbine", "diesel", "steam_turbine", "nuclear", "electric_motor"):
        if p in cur or not _cap_ok(f"prop:{p}", st.year, 1):
            continue
        if p == "nuclear" and (st.disp < 3000 or st.year < 1960):
            continue
        if d["ship_type"] == "submarine" and p not in ("diesel", "electric_motor", "nuclear"):
            continue
        if d["ship_type"] != "submarine" and p == "electric_motor":
            continue
        cands.append(p)
    if not cands:
        return None
    p = rng.choice(cands)
    req_B = {**st.req, "required_propulsion": [p]}

    def scaled() -> dict[str, Any] | None:
        k, ef, cf = PROP_EFFECT[p]
        d1 = O.clone(d)
        O.set_propulsion(d1, p)
        d2 = O.rederive(d1, k, None, st.year)
        if ef is None:
            d2["endurance_nm"] = 60000 if K._isnum(d.get("endurance_nm")) else None
        elif K._isnum(d2.get("endurance_nm")):
            d2["endurance_nm"] = O.rnd("endurance_nm", d2["endurance_nm"] * ef)
        if K._isnum(d2.get("complement")):
            d2["complement"] = O.rnd("complement", d2["complement"] * cf)
        return O.normalize({k_: v_ for k_, v_ in d2.items() if v_ is not None})

    return Change("propulsion_change", req_B, [f"use {p.replace('_', ' ')} propulsion"], scaled, target_disp=st.disp * 1.02)


_MAJOR = ["United-Kingdom", "United-States", "France", "Germany", "Italy", "Japan", "Russian", "China", "Netherlands", "Sweden", "Spain", "Turkey", "Greece", "Denmark", "Norway", "India"]


def h_country(st: State, rng: random.Random, D: DD.Data, split: str = "train") -> Change | None:
    """国変更。(元の国, 新しい国) の hash の 1/5 は test 専用（train / valid には出さない）。test は専用の組を優先し、普通の組も半分残す。"""
    pool = [c for c in D.countries_by_split[split] if c != st.country and c in _MAJOR]
    rng.shuffle(pool)
    for newc in pool[:8]:
        ho = DD.heldout_country_pair(st.country, newc)
        if split != "test" and ho:
            continue
        if split == "test" and not ho and rng.random() < 0.5:
            continue
        req_B = {**st.req, "country": newc}
        nat = C._NATION_ADJ.get(newc.replace("-", " "))
        phrase = f"design it for the {nat} navy instead" if nat else f"design it for the navy of {newc.replace('-', ' ')} instead"
        return Change("country_change", req_B, [phrase], None, target_disp=st.disp, country=newc, real_only=True, may_raise_limit=False,
                      extra={"heldout_pair": ho, "heldout_kind": "country_pair"})
    return None


def h_year(st: State, rng: random.Random, D: DD.Data, split_: str = "train") -> Change | None:
    """年代変更。要求の装備が年代的にまだ無い年へ動かすと（約 45%）、その装備は relaxed / infeasible になる（change_to_status）。"""
    sign = rng.choice([-1, 1])
    newy = st.year + sign * rng.randint(10, 25)
    toks = [(t, K.TOKEN_CAP.get(t)) for t in st.req.get("required") or [] if K.TOKEN_CAP.get(t) and (K.first_year(K.TOKEN_CAP[t]) or 0) >= 1920]
    if toks and rng.random() < 0.45:
        t, cap = rng.choice(toks)
        fy = K.first_year(cap)
        target = fy - K.ERA_CLEAR_GAP // 2 - rng.randint(2, 12)
        if 1875 <= target < st.year:
            newy = target
    if newy < 1875 or newy > 2008:
        newy = st.year - sign * rng.randint(10, 25)
        if newy < 1875 or newy > 2008:
            return None
    ho = DD.heldout_year_change(st.country, st.year, newy)
    if split_ != "test" and ho:
        return None
    if split_ == "test" and not ho and rng.random() < 0.5:
        return None
    req_B = {**st.req, "year": newy}
    dropped = []
    for tok in st.req.get("required") or []:
        cap = K.TOKEN_CAP.get(tok)
        if cap and K.cap_early_by(cap, newy) > K.ERA_CLEAR_GAP // 2:
            dropped.append(f"required:{tok}")
    for p in st.req.get("required_propulsion") or []:
        if K.cap_early_by(f"prop:{p}", newy) > K.ERA_CLEAR_GAP // 2:
            dropped.append(f"required_propulsion:{p}")
    return Change("year_change", req_B, [f"move the design date to {newy}"], None, target_disp=st.disp, year=newy, real_only=True, may_raise_limit=False,
                  relax_tokens=dropped, extra={"year_delta": newy - st.year, "heldout_pair": ho, "heldout_kind": "year_pair"})


CHANGE_WEIGHTS = [("disp_down", 9, h_disp_down), ("disp_up", 8, None), ("speed_up", 10, h_speed_up), ("speed_down", 5, h_speed_down), ("endurance_up", 4, h_endurance_up),
                  ("gun_cal_up", 7, h_gun_cal_up), ("gun_barrels_up", 9, h_gun_barrels_up), ("add_sam", 5, h_add_sam), ("add_ssm", 4, h_add_ssm), ("add_ciws", 2, h_add_ciws),
                  ("add_heli", 6, h_add_heli), ("aircraft_up", 4, h_aircraft_up), ("crew_down", 5, h_crew_down), ("country_change", 8, h_country), ("year_change", 8, h_year),
                  ("propulsion_change", 6, h_propulsion)]
LOOSE_TYPES = {"disp_down", "speed_up", "speed_down", "propulsion_change", "endurance_up", "crew_down", "add_ssm", "add_sam", "add_ciws"}
PARTNERS = ["gun_barrels_up", "add_sam", "add_ssm", "add_heli", "speed_up", "endurance_up", "gun_cal_up", "aircraft_up"]
HANDLERS = {n: h for n, _w, h in CHANGE_WEIGHTS if h is not None}
# 変更の種類ごとの元艦の述語（その変更が成り立つ艦を選ぶ）
CHANGE_PRED = {"gun_barrels_up": "barrels", "gun_cal_up": "gun_big", "add_heli": "heli_ok", "aircraft_up": "has_helicopter", "add_sam": "modern", "add_ciws": "modern", "endurance_up": "endurance",
               "propulsion_change": "prop", "crew_down": "any", "add_ssm": "any", "disp_up": "any", "year_change": "any", "country_change": "any"}


def fit_limit(req_B: dict[str, Any], cand: dict[str, Any], pieces: list[str], raise_ok: bool, base_lim: float | None, rng: random.Random) -> dict[str, Any] | None:
    """B が排水量の上限を超えるなら、上限を引き上げる（物理的に必要な増加分。ユーザーの文に「上限を X t まで上げる」を足す）。+60% を超える引き上げは不可。"""
    basis = req_B.get("displacement_basis", "normal")
    val = O.disp_in_basis(cand, basis)
    lim = req_B.get("max_displacement_t")
    if val is None or lim is None or val <= lim:
        return req_B
    if not raise_ok or val > 1.6 * lim:
        return None
    new_lim = nice_lim(val * rng.uniform(1.0, 1.06))
    if new_lim < val:
        new_lim = math.ceil(val)
    out = {**req_B, "max_displacement_t": K.num(new_lim)}
    pieces.append(f"raise the displacement limit to {K.num(new_lim)} t ({basis})")
    return out


def jaccard_kinds(a: dict[str, Any], b: dict[str, Any]) -> float:
    ka, kb = C.weapon_kinds(a), C.weapon_kinds(b)
    return len(ka & kb) / max(1, len(ka | kb))


def real_candidates(D: DD.Data, split: str, st: State, ch: Change, rng: random.Random) -> list[tuple[float, int]]:
    """A に近い実艦（同 split・同艦種・A の艦級グループ外）で、新しい要求を（緩めた上限の範囲で）満たすものを近い順に。"""
    idx = D.idx[split]
    req = {**ch.req_B}
    year = ch.year or st.year
    # the limit may be raised by up to +35 % when the change is allowed to grow the ship
    if ch.may_raise_limit and req.get("max_displacement_t"):
        req_search = {**req, "max_displacement_t": req["max_displacement_t"] * 1.35}
    else:
        req_search = req
    if ch.relax_tokens:
        req_search = K.apply_relaxations(req_search, [{"constraint": c} for c in ch.relax_tokens])
    country = ch.country or st.country
    win = 7 if any(c in ("country_change", "year_change") for c in ch.ctype.split("+")) else 5  # source ship within 5 years of the request year (7 for a country / year change)
    cands = idx.prefilter(req_search, year_lo=year - win, year_hi=year + win, country=country)
    out = []
    dA = st.design
    VA = dA.get("speed_kn")
    target = ch.target_disp or st.disp
    basis = req.get("displacement_basis", "normal")
    for i in cands:
        i = int(i)
        rid = idx.ids[i]
        if D.group_of[rid] in st.groups or rid == st.rec_id:
            continue
        dB = idx.designs[i]
        if not K.satisfies_all(req_search, dB, ignore_hull=True):
            continue
        vb = O.disp_in_basis(dB, basis)
        if vb is None or target <= 0:
            continue
        s = abs(math.log(vb / target)) + (0.8 * abs(math.log(dB["speed_kn"] / VA)) if K._isnum(VA) and K._isnum(dB.get("speed_kn")) else 0.5)
        s += 0.5 * (1 - jaccard_kinds(dA, dB)) + abs(idx.year[i] - st.year) / 25.0 + (0.2 if dB.get("ship_type_fine") != dA.get("ship_type_fine") else 0.0)
        if s <= 1.6:
            out.append((s, i))
    out.sort()
    return out


def attempt_change(D: DD.Data, split: str, st: State, ch: Change, rng: random.Random) -> dict[str, Any] | None:
    if ch.req_B == st.req:
        fail(f"modify:{ch.ctype}:request_unchanged")
        return None
    """変更 ch を state に適用して B を作る。戻り値 {design, req, pieces, route, rec_id, status, conflicts, relaxations, ...} か None。"""
    pieces = list(ch.pieces)
    route_order = ["real", "scaled"] if (rng.random() < REAL_PREF or ch.scaled is None) else ["scaled", "real"]
    if ch.real_only:
        route_order = ["real"]
    if ch.scaled is None:
        route_order = ["real"]
    for route in route_order:
        if route == "scaled":
            if ch.scaled is None:
                continue
            for _try in range(3):
                cand = ch.scaled()
                if cand is None:
                    continue
                cand = O.normalize(cand)
                p2 = list(pieces)
                req_B = fit_limit(ch.req_B, cand, p2, ch.may_raise_limit, None, rng)
                if req_B is None:
                    break
                res = K.check_design(req_B, cand, th=D.th)
                if res["ok"] and O.changed_fields(st.design, cand) and cand != st.design and attainable_ok(D, req_B, st) and coupling_ok(ch.ctype, st.design, cand, req_B):
                    return {"design": cand, "req": req_B, "pieces": p2, "route": "scaled", "rec_id": None, "status": "feasible", "conflicts": [], "relaxations": []}
                break
        else:
            cands = real_candidates(D, split, st, ch, rng)
            if not cands:
                continue
            idx = D.idx[split]
            ok_c = []
            for _s, i in cands[:8]:
                p2 = list(pieces)
                cand = D.design(idx.ids[i])
                req_B = fit_limit(ch.req_B, cand, p2, ch.may_raise_limit, None, rng)
                if req_B is None:
                    continue
                req_eff = K.apply_relaxations(req_B, [{"constraint": c} for c in ch.relax_tokens]) if ch.relax_tokens else req_B
                if K.check_design(req_eff, cand, th=D.th)["ok"] and coupling_ok(ch.ctype, st.design, cand, req_B):
                    ok_c.append((i, cand, req_B, p2))
            if not ok_c:
                continue
            i, cand, req_B, p2 = ok_c[rng.randrange(min(3, len(ok_c)))]
            return {"design": cand, "req": req_B, "pieces": p2, "route": "real", "rec_id": idx.ids[i], "status": "feasible", "conflicts": [], "relaxations": []}
    return None


def attainable_ok(D: DD.Data, req_B: dict[str, Any], st: State) -> bool:
    """scaled の B を feasible と名乗る前の関門: 速力・砲身数・搭載機数・魚雷管数・主砲口径の要求が、実データで到達可能な最良値（train の実艦、A 自身を含む）を
    超えていないこと。超えていると、infeasible / relaxed の「不可能」の定義（その要求を満たす実艦が無い）と矛盾する feasible になる。"""
    import shipknow_design_v1_infeasible as I  # noqa: E402

    reqA, dA = st.req, st.design
    checks: list[tuple[str, Any, Any, float]] = []  # (constraint, A value, best fn, tolerance)
    if req_B.get("min_speed_kn") is not None and req_B["min_speed_kn"] > (reqA.get("min_speed_kn") or 0):
        checks.append(("min_speed_kn", dA.get("speed_kn") or 0, lambda d: d.get("speed_kn") if K._isnum(d.get("speed_kn")) else None, 1.05))
    cal = req_B.get("min_gun_barrels_cal_mm")
    if req_B.get("min_gun_barrels") is not None and req_B["min_gun_barrels"] > (reqA.get("min_gun_barrels") or 0):
        checks.append(("min_gun_barrels", X.gun_barrels(dA, cal), lambda d: float(X.gun_barrels(d, cal)), 1.0))
    if req_B.get("min_aircraft") is not None and req_B["min_aircraft"] > (reqA.get("min_aircraft") or 0):
        checks.append(("min_aircraft", X.aircraft_count(dA), lambda d: float(X.aircraft_count(d)), 1.0))
    if req_B.get("min_torpedo_tubes") is not None and req_B["min_torpedo_tubes"] > (reqA.get("min_torpedo_tubes") or 0):
        checks.append(("min_torpedo_tubes", X.torpedo_tubes(dA), lambda d: float(X.torpedo_tubes(d)), 1.0))
    for cid, a_val, fn, tol in checks:
        best = I.best_of(D, req_B, [cid], fn)
        best = max(best or 0, a_val)
        if req_B[cid] > best * tol + 1e-9:
            return False
    gun_new = [t for t in req_B.get("required") or [] if t.startswith("main_gun>=") and t not in (reqA.get("required") or [])]
    for t in gun_new:
        n = float(t.split(">=")[1].rstrip("mm"))
        best = I.best_of(D, req_B, [f"required:{t}"], lambda d: C.main_gun_mm(d))
        if n > max(best or 0, C.main_gun_mm(dA) or 0):
            return False
    return True


def coupling_ok(ctype: str, dA: dict[str, Any], dB: dict[str, Any], reqB: dict[str, Any]) -> bool:
    """変更の種類ごとの連動の最低条件（real の B にも適用する。scaled は構成上満たす）。shipknow_check_design_v1._coupling_check と同じ規則。"""
    pa, pb = dA.get("power_hp"), dB.get("power_hp")
    for c in ctype.split("+"):
        if c == "speed_up" and K._isnum(pa) and K._isnum(pb) and not pb > pa:
            return False
        if c == "speed_down" and K._isnum(pa) and K._isnum(pb) and not pb < pa:
            return False
        if c == "disp_down":
            basis = reqB.get("displacement_basis", "normal")
            a, b = O.disp_in_basis(dA, basis), O.disp_in_basis(dB, basis)
            if a and b and not b < a:
                return False
            moved = sum(1 for f in ("length_m", "power_hp", "complement") if K._isnum(dA.get(f)) and K._isnum(dB.get(f)) and dB[f] <= dA[f])
            if moved < 2 and len(O.changed_fields(dA, dB)) < 3:
                return False
        if c == "add_heli" and (X.aircraft_count(dB) < 1 or "helicopter" not in (dB.get("air") or [])):
            return False
        if c == "gun_barrels_up" and X.gun_barrels(dB, reqB.get("min_gun_barrels_cal_mm")) <= X.gun_barrels(dA, reqB.get("min_gun_barrels_cal_mm")):
            return False
        if c == "aircraft_up" and X.aircraft_count(dB) <= X.aircraft_count(dA):
            return False
    return True


def change_to_status(D: DD.Data, ch: Change, req_B: dict[str, Any], design: dict[str, Any] | None, st: State) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """year_change で要求の一部が年代的に不可能になったとき: relaxed か infeasible か（判定は shipknow_design_v1_infeasible.decide_status）。"""
    import shipknow_design_v1_infeasible as I  # noqa: E402

    return I.era_conflicts_to_status(D, req_B, ch.relax_tokens, ch.year or req_B["year"], design)


def gen_modify(D: DD.Data, split: str, idx: int) -> dict[str, Any] | None:
    rng = K.sub_rng(SEED, "modify", split, idx)
    names = [n for n, _w, _h in CHANGE_WEIGHTS]
    weights = [w for _n, w, _h in CHANGE_WEIGHTS]
    ctype = rng.choices(names, weights=weights)[0]
    for _try in range(10):  # 元艦・要求を取り直して、その変更が成り立つ組を探す
        ep = _modify_once(D, split, rng, ctype)
        if ep is not None:
            return ep
    return None


def _modify_once(D: DD.Data, split: str, rng: random.Random, ctype: str) -> dict[str, Any] | None:
    partner0 = rng.choice(PARTNERS) if ctype == "disp_up" else None
    first_pred = CHANGE_PRED.get(partner0 or ctype, "any")
    rec = D.pick(split, rng, first_pred)
    if rec is None:
        fail("modify:no_anchor")
        return None
    r = make_request(D, split, rec, rng, force={"barrels"} if ctype in ("gun_barrels_up",) and rng.random() < 0.5 else None)
    if r is None:
        fail("modify:no_request")
        return None
    reqA, unseenA = r
    reqA = {k: v for k, v in reqA.items() if k != "ship_type_fine"}
    designA = D.design(rec)
    if ctype in ("country_change", "year_change") or (ctype in LOOSE_TYPES and rng.random() < 0.5):
        reqA = K.loosen_req(reqA, designA, rng)  # fewer constraints: another real ship can satisfy the changed request (real route)
    year = C.request_year(rec)
    if not K.check_design(reqA, designA)["ok"]:
        return None
    nbr = D.neighbors(reqA, [rec["id"]])
    ep = new_episode("modify", ctype, split)
    textA = K.request_text_v1(reqA, rng.randrange(6))
    ep["messages"].append(msg("user", user_turn(textA, reqA, nbr)))
    add_asst(ep, asst_turn("feasible", designA), status="feasible", origin="real", source=rec["id"], role="answer", train=True)
    add_source(ep, rec["id"], D)
    st = State(reqA, designA, rec["id"], {D.group_of[rec["id"]]}, year, rec["country"])

    # change plan: 1 change (60 %), composite (25 %), chain of 2 turns (15 %)
    mode = rng.choices(["one", "composite", "chain"], weights=[0.60, 0.25, 0.15])[0]
    n_turns = 2 if mode == "chain" else 1
    changes_done: list[str] = []
    cur = st
    coupling_all: list[str] = []
    for turn in range(n_turns):
        if turn == 0:
            ct = ctype
        else:
            ct = rng.choice([n for n in HANDLERS if n not in changes_done and n not in ("country_change", "year_change")])
        ch = _build_change(D, split, cur, ct, rng, composite=(mode == "composite" and turn == 0), partner=partner0 if turn == 0 else None)
        if ch is None:
            fail(f"modify:{ct}:no_change")
            return None
        res = attempt_change(D, split, cur, ch, rng)
        if res is None:
            fail(f"modify:{ct}:no_design")
            return None
        reqB, designB, pieces = res["req"], res["design"], res["pieces"]
        status, conflicts, relaxations, cinfo = "feasible", [], [], []
        out_design: dict[str, Any] | None = designB
        if ch.relax_tokens:
            status, conflicts, relaxations, cinfo = change_to_status(D, ch, reqB, designB, cur)
            if status == "infeasible":
                out_design = None
        textB = _sentence(pieces, rng)
        new_nbr = None
        if ch.ctype in ("country_change", "year_change"):
            new_nbr = D.neighbors(reqB, sorted(set(ep["meta"]["sources"] + ([res["rec_id"]] if res["rec_id"] else []))))
        ep["messages"].append(msg("user", user_turn(textB, reqB, new_nbr)))
        add_asst(ep, asst_turn(status, out_design, conflicts, relaxations), status=status, origin=("real" if res["route"] == "real" else "scaled"), source=res["rec_id"], role="answer",
                 train=True, route=res["route"], change=ch.ctype, changed_fields=O.changed_fields(cur.design, designB), conflict_info=cinfo)
        add_source(ep, res["rec_id"], D)
        coupling_all += coupling_tags(cur.design, designB)
        changes_done.append(ch.ctype)
        if status != "feasible" or out_design is None:
            reqB_final = reqB
            break
        cur = State(reqB, designB, res["rec_id"] or cur.rec_id, cur.groups | ({D.group_of[res["rec_id"]]} if res["rec_id"] else set()), ch.year or cur.year, ch.country or cur.country)
        reqB_final = reqB
    ep["meta"]["unseen_combo"] = unseenA or (split == "test" and K.is_heldout_combo(reqB_final))
    if split != "test" and K.is_heldout_combo(reqB_final):
        return None
    if ch.ctype in ("country_change", "year_change") and ch.extra.get("heldout_pair"):
        ep["meta"]["holdout"].append(ch.extra["heldout_kind"])
    ep["meta"]["changes"] = changes_done
    ep["meta"]["mode"] = mode
    ep["meta"]["coupling"] = sorted(set(coupling_all))
    if not split_check(D, ep):
        return None
    return fin_meta(ep, D, reqB_final)


def _build_change(D: DD.Data, split: str, st: State, ctype: str, rng: random.Random, composite: bool, partner: str | None = None) -> Change | None:
    if ctype == "disp_up":
        ph = HANDLERS[partner or rng.choice(PARTNERS)]
        pc = ph(st, rng, D)
        if pc is None:
            return None
        dv = st.disp
        lim = nice_lim(dv * (1 + rng.uniform(0.18, 0.42)))
        req_B = {**pc.req_B, "max_displacement_t": lim}
        pieces = [f"raise the displacement limit to {lim} t ({st.basis})"] + pc.pieces
        return Change("disp_up", req_B, pieces, pc.scaled, target_disp=(pc.target_disp or dv) * 1.0, may_raise_limit=True)
    if ctype == "country_change":
        return h_country(st, rng, D, split)
    if ctype == "year_change":
        return h_year(st, rng, D, split)
    h = HANDLERS[ctype]
    ch = h(st, rng, D)
    if ch is None or not composite:
        return ch
    # composite: a second, independent change in the same user turn
    others = [n for n in ("speed_up", "gun_barrels_up", "endurance_up", "add_ssm", "crew_down", "disp_down") if n != ctype]
    rng.shuffle(others)
    for o in others:
        c2 = HANDLERS[o](State(ch.req_B, st.design, st.rec_id, st.groups, st.year, st.country), rng, D)
        if c2 is None or o == "disp_down" and ctype in ("speed_up", "gun_barrels_up", "add_sam", "add_ssm", "gun_cal_up", "add_heli", "aircraft_up"):
            continue
        s1, s2 = ch.scaled, c2.scaled

        def both(s1=s1, s2=s2, st=st, c2=c2, ch=ch) -> dict[str, Any] | None:
            a = s1() if s1 else None
            if a is None:
                return None
            st2 = State(ch.req_B, a, st.rec_id, st.groups, st.year, st.country)
            # re-apply the second change on the result of the first one (its closure captured the original state, so rebuild it)
            c3 = HANDLERS[c2.ctype](st2, rng, D)
            return c3.scaled() if c3 and c3.scaled else None

        req_B = {**c2.req_B}
        pieces = ch.pieces + c2.pieces
        return Change(f"{ctype}+{o}", req_B, pieces, both, target_disp=(ch.target_disp or st.disp) * (c2.target_disp or st.disp) / max(st.disp, 1), may_raise_limit=ch.may_raise_limit and c2.may_raise_limit)
    return ch


def coupling_tags(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """設計 A -> B で、関連項目が同じ向きに動いたか（モデルが関連項目を連動して直したことの記録）。"""
    tags: list[str] = []

    def sgn(x: float | None, y: float | None, tol: float = 0.015) -> int:
        if not K._isnum(x) or not K._isnum(y) or x == 0:
            return 0
        r = y / x - 1
        return 0 if abs(r) < tol else (1 if r > 0 else -1)

    da, db = O.ref_disp(a), O.ref_disp(b)
    sd = sgn(da, db, 0.03)
    ss = sgn(a.get("speed_kn"), b.get("speed_kn"), 0.02)
    sp = sgn(a.get("power_hp"), b.get("power_hp"))
    if ss and sp and ss == sp:
        tags.append("speed_power")
    if sd and sd == sgn(a.get("length_m"), b.get("length_m")):
        tags.append("disp_length")
    if sd and sd == sgn(a.get("complement"), b.get("complement")):
        tags.append("disp_crew")
    if sd and sd == sgn(a.get("power_hp"), b.get("power_hp")) and not ss:
        tags.append("disp_power")
    if sd and (X.gun_barrels(a) != X.gun_barrels(b) or X.torpedo_tubes(a) != X.torpedo_tubes(b) or C.weapon_kinds(a) != C.weapon_kinds(b)):
        tags.append("disp_weapons")
    if X.aircraft_count(b) > X.aircraft_count(a) and sd > 0:
        tags.append("aircraft_disp")
    if X.gun_barrels(b) > X.gun_barrels(a) and sd > 0:
        tags.append("barrels_disp")
    if (a.get("propulsion") or []) != (b.get("propulsion") or []):
        tags.append("propulsion")
    if sorted(a.get("sensors") or []) != sorted(b.get("sensors") or []):
        tags.append("sensors")
    if a.get("endurance_nm") != b.get("endurance_nm") and sgn(a.get("endurance_nm"), b.get("endurance_nm")):
        tags.append("endurance")
    return tags
