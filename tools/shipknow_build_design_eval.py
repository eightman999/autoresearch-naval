"""設計能力の学習前後比較用の評価データ shipknow_design_eval_v1 を作る（学習データではない。v0 の train / valid / test / scenarios と混ぜない）。

    .venv/bin/python tools/shipknow_build_design_eval.py                 # data/navspec/shipknow_design_eval_v1/ に書く
    .venv/bin/python tools/shipknow_build_design_eval.py --verify-only   # 書いたファイルを独立に再検証（重複・近傍の分割・充足例・不可能性）

出力: problems.jsonl（A〜E 各 50 問 = 250）、pairs.jsonl（50 組 = 100 問）、all.jsonl（両者の連結。推論を 1 回で済ませる用）、forget_ids.json（T1〜T3 忘却評価のサブセット）、overlap_check.json、manifest.json。
形式は design_v0/scenarios.jsonl と同じ（messages[system, user]、正解なし。user = {"request", "req", "nbr"} の JSON、system は v0 と同一）。

  A Historical   国×年代×艦種の train の実艦分布から自然な範囲の緩い制約。test に同じ国・艦種の実艦が ±2 年内に無い組み合わせだけ
  B Quantitative 実在の train 艦 1 隻（witness）の値から逆算した強い数値制約 4〜7 個。witness 自身の艦級は近傍から除く
  C Trade-off    小さい船の排水量 + 速い船の速力 + 大きい船の武装を重ねた要求。train の実艦で全充足するのは 0〜4 隻。充足艦の艦級は近傍から除く
  D OOD          架空国家・戦勝国・技術前倒し・実在艦級に対応しない制約（shipknow_design_eval_cases.py）
  E Impossible   物理・年代・矛盾で成立しない要求（同）。train の実艦で全充足する艦が要求年（+5）以前に 0 であることを検証する
  P Pair         同じ基本条件で 1〜2 個の制約だけ変えた A/B。期待方向を機械判定できる形で meta.pair.expected に持つ

近傍は規則 NB-1（shipknow_build_design.select_neighbors をそのまま使う）。train の実艦のみ。拡張キー（砲身数・搭載機数など）は v0 の required 語彙に写して検索する。
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_design as BD  # noqa: E402
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_cases as K  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_history as H  # noqa: E402
import shipknow_retriever as R  # noqa: E402

OUT = C.ROOT / "data" / "navspec" / "shipknow_design_eval_v1"
V1_2_TEST = C.ROOT / "data" / "navspec" / "shipknow_v1_2_rag" / "test.jsonl"
SEED = 20261008
N_PER_CAT = 50
N_PAIRS = 50
ERA5 = [(1860, 1899), (1900, 1919), (1920, 1944), (1945, 1969), (1970, 2100)]

A_COUNTRIES = ["Japan", "United-States", "United-Kingdom", "Germany", "Italy", "France", "Russian", "China", "Netherlands", "Sweden"]
A_TYPES = ["battleship", "battlecruiser", "armored_protected_cruiser", "heavy_cruiser", "light_cruiser", "cruiser", "destroyer", "frigate", "corvette", "escort",
           "torpedo_boat", "fast_attack_craft", "submarine", "aircraft_carrier", "patrol", "mine_warfare", "amphibious"]
SMALL_TYPES = {"torpedo_boat", "fast_attack_craft", "patrol", "mine_warfare", "amphibious", "corvette"}


def sub_rng(*parts: Any) -> random.Random:
    return BD.sub_rng(SEED, *[str(p) for p in parts])


def nice(x: float) -> float | int:
    return C._num(C._nice_round(x))


def quantile(xs: list[float] | np.ndarray, q: float) -> float:
    return float(np.quantile(np.asarray(xs, dtype=float), q))


# --------------------------------------------------------------------------------------------------
# context: train designs, fast satisfaction index, neighbour pool
# --------------------------------------------------------------------------------------------------
class DesignIndex:
    """train 設計の充足判定をベクトル化したもの（req_checks と同じ意味: 数値項目が無い設計は未充足、配列項目に無い装備は未充足）。"""

    def __init__(self, recs: list[dict[str, Any]], designs: list[dict[str, Any]]):
        self.recs = recs
        self.designs = designs
        n = len(recs)
        self.n = n
        self.ids = [r["id"] for r in recs]
        self.country = np.array([r["country"] for r in recs])
        self.year = np.array([C.request_year(r) for r in recs], dtype=float)
        self.type = np.array([d["ship_type"] for d in designs])
        self.fine = np.array([d.get("ship_type_fine") or "" for d in designs])

        def arr(key: str) -> np.ndarray:
            return np.array([d[key] if isinstance(d.get(key), (int, float)) else np.nan for d in designs], dtype=float)

        self.disp = {b: arr(f"displacement_{b}_t") for b in ("normal", "full", "standard")}
        self.speed, self.complement, self.endurance, self.power = arr("speed_kn"), arr("complement"), arr("endurance_nm"), arr("power_hp")
        self.dref = np.array([(C.ref_displacement(d)[0] or np.nan) for d in designs], dtype=float)
        self.aircraft = np.array([X.aircraft_count(d) for d in designs], dtype=float)
        self.tubes = np.array([X.torpedo_tubes(d) for d in designs], dtype=float)
        self.gun_cal = np.array([(C.main_gun_mm(d) or 0.0) for d in designs], dtype=float)
        self.modern = np.array([X.modern_count(d) for d in designs], dtype=float)
        self._tok: dict[str, np.ndarray] = {}
        self._barrels: dict[float | None, np.ndarray] = {}
        self._prop: dict[str, np.ndarray] = {}

    def tok(self, t: str) -> np.ndarray:
        if t not in self._tok:
            self._tok[t] = np.array([bool(C.satisfies(t, d)) for d in self.designs])
        return self._tok[t]

    def barrels(self, cal: float | None) -> np.ndarray:
        if cal not in self._barrels:
            self._barrels[cal] = np.array([X.gun_barrels(d, cal) for d in self.designs], dtype=float)
        return self._barrels[cal]

    def prop(self, p: str) -> np.ndarray:
        if p not in self._prop:
            self._prop[p] = np.array([isinstance(d.get("propulsion"), list) and p in d["propulsion"] for d in self.designs])
        return self._prop[p]

    def mask(self, req: dict[str, Any], ignore_hull: bool = True, base: np.ndarray | None = None) -> np.ndarray:
        m = np.ones(self.n, dtype=bool) if base is None else base.copy()
        if not ignore_hull:
            m &= self.type == req["ship_type"]
            if req.get("ship_type_fine"):
                m &= self.fine == req["ship_type_fine"]
        if req.get("max_displacement_t") is not None:
            with np.errstate(invalid="ignore"):
                m &= self.disp[req.get("displacement_basis", "normal")] <= req["max_displacement_t"]
        for key, arr_, op in (("min_speed_kn", self.speed, ">="), ("max_complement", self.complement, "<="), ("min_endurance_nm", self.endurance, ">=")):
            if req.get(key) is not None:
                with np.errstate(invalid="ignore"):
                    m &= (arr_ >= req[key]) if op == ">=" else (arr_ <= req[key])
        for t in req.get("required") or []:
            m &= self.tok(t)
        if req.get("min_gun_barrels") is not None:
            m &= self.barrels(req.get("min_gun_barrels_cal_mm")) >= req["min_gun_barrels"]
        if req.get("min_aircraft") is not None:
            m &= self.aircraft >= req["min_aircraft"]
        if req.get("min_torpedo_tubes") is not None:
            m &= self.tubes >= req["min_torpedo_tubes"]
        for p in req.get("required_propulsion") or []:
            m &= self.prop(p)
        return m

    def group(self, country: str, ship_type: str, year: int, window: int) -> np.ndarray:
        return np.where((self.country == country) & (self.type == ship_type) & (np.abs(self.year - year) <= window))[0]


class Ctx:
    def __init__(self, limit_names: bool = True):
        self.recs_all = [json.loads(l) for l in (C.V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        sp = json.loads((C.V0_DIR / "splits.json").read_text(encoding="utf-8"))
        self.assign, self.group_of = sp["assignments"], sp["group_of"]
        self.train = [r for r in self.recs_all if self.assign[r["id"]] == "train" and C.design_eligible(r)]
        self.designs = [C.design_from_record(r) for r in self.train]
        self.idx = DesignIndex(self.train, self.designs)
        self.pool = R.Pool(self.train, self.group_of)
        self.id_pos = {r["id"]: i for i, r in enumerate(self.train)}
        conn = H.open_ro()
        texts = H.class_texts(conn)
        names_by_url = H.class_ship_names(conn)
        conn.close()
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.class_name: dict[str, str] = {}
        self.names: dict[str, list[str]] = {}
        for r in self.train:
            row = texts.get(int(r["id"][1:]))
            if row:
                self.events[r["id"]] = H.events_for_class(row)
                self.class_name[r["id"]] = row["CLASS_NAME"]
                self.names[r["id"]] = names_by_url.get(row["source_url"], [])
        # held-out real ships (all test-assigned records, eligible or not) for the "no real test ship in this cell" rule of A
        self.test_cells: dict[tuple[str, str], list[int]] = collections.defaultdict(list)
        for r in self.recs_all:
            if self.assign[r["id"]] == "test" and r.get("year") and r.get("ship_type"):
                self.test_cells[(r["country"], r["ship_type"])].append(C.request_year(r))

    def test_ship_near(self, country: str, ship_type: str, year: int, tol: int = 2) -> int:
        return sum(1 for y in self.test_cells.get((country, ship_type), []) if abs(y - year) <= tol)


# --------------------------------------------------------------------------------------------------
# neighbours (rule NB-1) and rows
# --------------------------------------------------------------------------------------------------
def select_nbr(ctx: Ctx, req: dict[str, Any], exclude_groups: set[str]) -> list[tuple[int, float]]:
    rreq = X.retrieval_req(req)
    pool = ctx.pool
    if not exclude_groups:
        return BD.select_neighbors(pool, rreq, k=3)
    saved = pool.groups
    try:
        pool.groups = np.where(np.isin(saved, list(exclude_groups)), "__EXCLUDED__", saved)
        return BD.select_neighbors(pool, rreq, k=3, exclude_group="__EXCLUDED__")
    finally:
        pool.groups = saved


def make_row(ctx: Ctx, rid: str, category: str, req: dict[str, Any], premise: str | None, rng: random.Random, meta: dict[str, Any],
             extra_phrases: list[str] | None = None, extra_ja: list[str] | None = None, exclude_ids: list[str] | None = None) -> dict[str, Any]:
    req = X.order_req(req)
    exclude_groups = {ctx.group_of[i] for i in (exclude_ids or [])}
    variant = rng.randrange(3)
    text = X.request_text_ext(req, variant, premise, extra_phrases)
    picks = select_nbr(ctx, req, exclude_groups)
    # leak guard for the witness classes whose groups are excluded: their names / own timelines must not appear in the neighbours' timelines
    terms: list[str] = []
    own: set[str] = set()
    for i in exclude_ids or []:
        terms += BD.name_terms(ctx.class_name.get(i, ""), ctx.names.get(i, []))
        own |= H.own_prefixes(ctx.events.get(i, []))
    nbr, n_hist = BD.nbr_block(ctx.pool, picks, ctx.events, req["year"], None, BD.drop_generic(sorted(set(terms)), req), 3, 90, own or None)
    row = {
        "id": rid, "task": "D", "category": category, "fictional": bool(premise), "premise": premise or None, "request": text,
        "request_ja": X.request_text_ja_ext(req, premise, extra_ja), "req": req,
        "messages": [{"role": "system", "content": BD.SYSTEM_D}, {"role": "user", "content": BD.build_user(req, text, nbr)}],
        "meta": {**meta, "variant": f"v{variant}", "nbr": [ctx.pool.ids[j] for j, _ in picks], "nbr_years": [ctx.pool.recs[j]["year"] for j, _ in picks],
                 "nbr_countries": [ctx.pool.recs[j]["country"] for j, _ in picks], "n_hist": n_hist, "rule": "NB-1 (see manifest.neighbor_rule)",
                 "excluded_ids": exclude_ids or [], "n_units": len(X.constraint_units(req))},
    }
    return row


def sat_info(ctx: Ctx, req: dict[str, Any]) -> dict[str, Any]:
    """train の実艦で要求（国・年を除く）を全充足する艦。"""
    ix = ctx.idx
    m_any = ix.mask(req, ignore_hull=True)
    m_type = ix.mask(req, ignore_hull=False)
    m_year = m_any & (ix.year <= req["year"] + 5)
    m_type_year = m_type & (ix.year <= req["year"] + 5)
    near = dict(req)
    if near.get("max_displacement_t"):
        near["max_displacement_t"] = near["max_displacement_t"] * 1.1
    if near.get("min_speed_kn") is not None:
        near["min_speed_kn"] = near["min_speed_kn"] - 2
    m_near = ix.mask(near, ignore_hull=True) & (ix.year <= req["year"] + 5)
    wit = np.where(m_any)[0]
    wit = sorted(wit, key=lambda j: abs(ix.year[j] - req["year"]))[:5]
    return {"n_sat_train_any": int(m_any.sum()), "n_sat_train_same_type": int(m_type.sum()), "n_sat_train_up_to_year": int(m_year.sum()),
            "n_sat_train_same_type_up_to_year": int(m_type_year.sum()), "n_sat_near_relaxed": int(m_near.sum()),
            "witnesses": [ix.ids[j] for j in wit]}


# --------------------------------------------------------------------------------------------------
# overlap registry (v0 train / valid / test / scenarios)
# --------------------------------------------------------------------------------------------------
def main_key(req: dict[str, Any]) -> tuple:
    return (req["country"], req["year"], req["ship_type"], req.get("max_displacement_t"), req.get("min_speed_kn"), tuple(sorted(req.get("required") or [])))


def cell_key(req: dict[str, Any]) -> tuple:
    return (req["country"], req["year"], req["ship_type"])


def canon(o: Any) -> str:
    return json.dumps(o, sort_keys=True, ensure_ascii=False)


class Registry:
    def __init__(self) -> None:
        self.req: dict[str, str] = {}
        self.text: dict[str, str] = {}
        self.main: dict[tuple, str] = {}
        self.cell: dict[tuple, list[str]] = collections.defaultdict(list)
        self.cell_cty_type: dict[tuple, list[int]] = collections.defaultdict(list)
        self.n = 0

    def add(self, src: str, req: dict[str, Any], text: str) -> None:
        self.n += 1
        self.req.setdefault(canon(req), src)
        self.text.setdefault(text, src)
        self.main.setdefault(main_key(req), src)
        self.cell[cell_key(req)].append(src)
        self.cell_cty_type[(req["country"], req["ship_type"])].append(req["year"])

    def collide(self, req: dict[str, Any], text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        if canon(req) in self.req:
            out["req"] = self.req[canon(req)]
        if text in self.text:
            out["text"] = self.text[text]
        if main_key(req) in self.main:
            out["main_key"] = self.main[main_key(req)]
        return out

    def cell_hits(self, req: dict[str, Any]) -> list[str]:
        return sorted(set(self.cell.get(cell_key(req), [])))


def load_v0_registry() -> Registry:
    reg = Registry()
    for name in ("train", "valid", "test", "scenarios"):
        for line in (C.OUT_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            u = json.loads(e["messages"][1]["content"])
            reg.add(f"v0:{name}", u["req"], u["request"])
    return reg


# --------------------------------------------------------------------------------------------------
# A: historical
# --------------------------------------------------------------------------------------------------
def modal(xs: list[Any]) -> Any:
    return collections.Counter(xs).most_common(1)[0][0]


def derive_group_stats(ctx: Ctx, country: str, ship_type: str, year: int, min_n: int = 6, only_with: list[str] | None = None) -> dict[str, Any] | None:
    for w in (10, 15, 20):
        g = ctx.idx.group(country, ship_type, year, w)
        for t in only_with or []:
            g = g[ctx.idx.tok(t)[g]]  # the limits come from the ships that have the capability the pair will ask for
        if len(g) >= min_n:
            return {"idx": g, "window": w}
    return None


def loose_req(ctx: Ctx, country: str, ship_type: str, year: int, rng: random.Random, tight: bool = False,
              only_with: list[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """国×艦種×年代の train の実艦分布から自然な範囲の緩い制約を作る（tight=True はペアテスト用に中央値付近）。"""
    gs = derive_group_stats(ctx, country, ship_type, year, 6 if not only_with else 4, only_with)
    if gs is None:
        return None
    ix = ctx.idx
    g = gs["idx"]
    ds = [ctx.designs[j] for j in g]
    bases = [C.ref_displacement(d)[1] for d in ds]
    basis = modal(bases)
    vals = [d.get(f"displacement_{basis}_t") for d in ds if isinstance(d.get(f"displacement_{basis}_t"), (int, float))]
    if len(vals) < 5:
        return None
    sp = [d["speed_kn"] for d in ds if isinstance(d.get("speed_kn"), (int, float))]
    if len(sp) < 5:
        return None
    req: dict[str, Any] = {"country": country, "year": year, "ship_type": ship_type}
    tags = modal([tuple(C.mission_tags(d)) for d in ds])
    fines = [d.get("ship_type_fine") for d in ds]
    fine = modal(fines)
    if not tight and rng.random() < 0.3 and fines.count(fine) / len(fines) >= 0.5:
        req["ship_type_fine"] = fine
    req["mission"] = list(tags)
    lim = quantile(vals, 0.5) * 1.10 if tight else quantile(vals, 0.80)
    req["max_displacement_t"] = nice(lim)
    req["displacement_basis"] = basis
    req["min_speed_kn"] = C._num(max(2.0, math.floor(quantile(sp, 0.40 if tight else 0.25) / 0.5) * 0.5))
    required: list[str] = []
    cals = [C.main_gun_mm(d) for d in ds if C.main_gun_mm(d)]
    if len(cals) / len(ds) >= 0.7:
        n_ = max((x for x in C.GUN_GRID if x <= quantile(cals, 0.40 if tight else 0.25)), default=None)
        if n_ and n_ >= 20:
            required.append(f"main_gun>={n_}mm")
    prev: collections.Counter = collections.Counter()
    for d in ds:
        for t in C.candidate_tokens(d):
            if not t.startswith("main_gun"):
                prev[t] += 1
    extras = [t for t, c in prev.items() if c / len(ds) >= 0.6]
    rng.shuffle(extras)
    if extras and rng.random() < (0.6 if not tight else 0.4):
        required.append(extras[0])
    req["required"] = required
    cm = [d["complement"] for d in ds if isinstance(d.get("complement"), (int, float))]
    if not tight and len(cm) >= 5 and rng.random() < 0.25:
        req["max_complement"] = int(C._nice_up(quantile(cm, 0.8), 1.0))
    info = {"group_n": int(len(g)), "group_window_years": gs["window"], "group_basis": basis}
    return X.order_req(req), info


def gen_A(ctx: Ctx, reg: Registry, taken: Registry) -> list[dict[str, Any]]:
    cells: list[tuple[str, int, str]] = []
    for c in A_COUNTRIES:
        for t in A_TYPES:
            for y in range(1890, 2011):
                if len(ctx.idx.group(c, t, y, 10)) >= 6 and ctx.test_ship_near(c, t, y, 2) == 0:
                    cells.append((c, y, t))
    rng = sub_rng("A", "cells")
    by_ct: dict[tuple[str, int], list[tuple[str, int, str]]] = collections.defaultdict(list)
    for c, y, t in cells:
        by_ct[(c, next(i for i, (lo, hi) in enumerate(ERA5) if lo <= y <= hi))].append((c, y, t))
    out: list[dict[str, Any]] = []
    used_types: collections.Counter = collections.Counter()
    era_cycle = [1, 2, 3, 4, 0]
    per_country = N_PER_CAT // len(A_COUNTRIES)
    n = 0
    for c in A_COUNTRIES:
        order = era_cycle[:]
        rng.shuffle(order)
        chosen = 0
        # rotate over eras; 1890-1899 (era 0) is last
        for rep in range(3):
            for e in order:
                if chosen >= per_country:
                    break
                cand = [x for x in by_ct.get((c, e), []) if used_types[x[2]] < 6]
                rng.shuffle(cand)
                for (cc, y, t) in cand:
                    rid = f"A{n + 1:02d}"
                    r2 = sub_rng("A", cc, y, t)
                    lr = loose_req(ctx, cc, t, y, r2)
                    if lr is None:
                        continue
                    req, info = lr
                    text0 = X.request_text_ext(req, 0)
                    if reg.collide(req, text0) or taken.collide(req, text0):
                        continue
                    sat = sat_info(ctx, req)
                    if sat["n_sat_train_same_type"] < 3:
                        continue
                    row = make_row(ctx, rid, "A", req, None, r2, {**info, **sat, "cell": [cc, y, t], "test_ships_within_2y": ctx.test_ship_near(cc, t, y, 2)})
                    if reg.collide(req, row["request"]) or taken.collide(req, row["request"]):
                        continue
                    out.append(row)
                    taken.add(rid, req, row["request"])
                    used_types[t] += 1
                    n += 1
                    chosen += 1
                    break
            if chosen >= per_country:
                break
    return out


# --------------------------------------------------------------------------------------------------
# B: quantitative
# --------------------------------------------------------------------------------------------------
def era5(y: int) -> int:
    return next(i for i, (lo, hi) in enumerate(ERA5) if lo <= y <= hi)


def b_unit_pool(d: dict[str, Any], rng: random.Random) -> list[tuple[str, dict[str, Any]]]:
    """witness 設計から作れる制約単位（排水量・速力は必ず入れる）。各項目は (名前, req に足すキー値)。"""
    pool: list[tuple[str, dict[str, Any]]] = []
    if isinstance(d.get("complement"), (int, float)):
        pool.append(("complement", {"max_complement": int(math.ceil(d["complement"] * rng.uniform(1.03, 1.08)))}))
    if isinstance(d.get("endurance_nm"), (int, float)):
        e = d["endurance_nm"]
        step = 50 if e < 1000 else 100
        pool.append(("endurance", {"min_endurance_nm": int(max(step, math.floor(e * (1 - rng.uniform(0, 0.05)) / step) * step))}))
    gun = C.main_gun_mm(d)
    if gun and gun >= 20:
        pool.append(("main_gun", {"required": [f"main_gun>={C._num(gun)}mm"]}))
        nb = X.gun_barrels(d, gun)
        if nb >= 2:
            pool.append(("gun_barrels", {"min_gun_barrels": nb, "min_gun_barrels_cal_mm": C._num(gun)}))
    ac = X.aircraft_count(d)
    if ac >= 1 and "air:helicopter" in C.caps_of_design(d):
        pool.append(("aircraft", {"min_aircraft": ac, "required": ["helicopter"]}))
    elif ac >= 1:
        pool.append(("aircraft", {"min_aircraft": ac}))
    tt = X.torpedo_tubes(d)
    if tt >= 2:
        pool.append(("torpedo_tubes", {"min_torpedo_tubes": tt}))
    toks = C.candidate_tokens(d)
    for t in ("air_search_radar", "fire_control_radar", "surface_radar", "sonar", "esm_ecm"):
        if t in toks:
            pool.append((f"sensor:{t}", {"required": [t]}))
    for t in ("ssm", "sam", "ciws", "asw_weapon", "rocket_launcher"):
        if t in toks:
            pool.append((f"weapon:{t}", {"required": [t]}))
    for p in d.get("propulsion") or []:
        if p in ("gas_turbine", "nuclear", "steam_turbine", "diesel", "electric_motor"):
            pool.append((f"propulsion:{p}", {"required_propulsion": [p]}))
    return pool


def merge_req(req: dict[str, Any], add: dict[str, Any]) -> None:
    for k, v in add.items():
        if k == "required":
            req.setdefault("required", [])
            for t in v:
                if t not in req["required"]:
                    req["required"].append(t)
        elif k == "required_propulsion":
            req.setdefault("required_propulsion", [])
            for t in v:
                if t not in req["required_propulsion"]:
                    req["required_propulsion"].append(t)
        else:
            req[k] = v


def base_witness_req(rec: dict[str, Any], d: dict[str, Any], rng: random.Random, slack: tuple[float, float] = (1.03, 1.08)) -> dict[str, Any]:
    dref, basis = C.ref_displacement(d)
    req: dict[str, Any] = {"country": rec["country"], "year": C.request_year(rec), "ship_type": d["ship_type"]}
    req["mission"] = C.mission_tags(d)
    req["max_displacement_t"] = nice(dref * rng.uniform(*slack)) if dref * slack[1] >= nice(dref * slack[0]) else math.ceil(dref * slack[0])
    if req["max_displacement_t"] < dref:
        req["max_displacement_t"] = math.ceil(dref * slack[0])
    req["displacement_basis"] = basis
    req["min_speed_kn"] = C._num(min(d["speed_kn"], math.floor((d["speed_kn"] - rng.uniform(0, 0.5)) / 0.5) * 0.5))
    return req


def gen_B(ctx: Ctx, reg: Registry, taken: Registry) -> list[dict[str, Any]]:
    rng = sub_rng("B", "pick")
    order = list(range(len(ctx.train)))
    rng.shuffle(order)
    quota_era = {i: N_PER_CAT // 5 for i in range(5)}
    per_country: collections.Counter = collections.Counter()
    per_type: collections.Counter = collections.Counter()
    out: list[dict[str, Any]] = []
    for j in order:
        if len(out) >= N_PER_CAT:
            break
        rec, d = ctx.train[j], ctx.designs[j]
        y = C.request_year(rec)
        e = era5(y)
        if quota_era[e] <= 0 or per_country[rec["country"]] >= 6 or per_type[d["ship_type"]] >= 8 or y < 1880 or (C.ref_displacement(d)[0] or 0) < 30:
            continue
        r = sub_rng("B", rec["id"])
        pool = b_unit_pool(d, r)
        if len(pool) < 2:
            continue
        n_units = r.randint(4, 7)
        req = base_witness_req(rec, d, r)
        r.shuffle(pool)
        names: set[str] = set()
        for name, add in pool:
            trial = dict(req)
            trial["required"] = list(req.get("required") or [])
            trial["required_propulsion"] = list(req.get("required_propulsion") or [])
            merge_req(trial, add)
            if len(X.constraint_units(trial)) > n_units:
                continue
            req = trial
            names.add(name)
        if not req.get("required"):
            req["required"] = []
        req = X.order_req({k: v for k, v in req.items() if not (k == "required_propulsion" and not v)})
        nu = len(X.constraint_units(req))
        if not (4 <= nu <= 7):
            continue
        wmask = ctx.idx.mask(req, ignore_hull=False)
        if not wmask[j]:
            continue
        row_id = f"B{len(out) + 1:02d}"
        text0 = X.request_text_ext(req, 0)
        if reg.collide(req, text0) or taken.collide(req, text0):
            continue
        sat = sat_info(ctx, req)
        row = make_row(ctx, row_id, "B", req, None, r, {**sat, "witness": rec["id"], "witness_class": ctx.class_name.get(rec["id"]), "units": sorted(names)}, exclude_ids=[rec["id"]])
        if reg.collide(req, row["request"]) or taken.collide(req, row["request"]):
            continue
        out.append(row)
        taken.add(row_id, req, row["request"])
        quota_era[e] -= 1
        per_country[rec["country"]] += 1
        per_type[d["ship_type"]] += 1
    return out


# --------------------------------------------------------------------------------------------------
# C: trade-off
# --------------------------------------------------------------------------------------------------
C_TYPES = ["destroyer", "frigate", "corvette", "escort", "light_cruiser", "heavy_cruiser", "cruiser", "fast_attack_craft", "patrol", "submarine", "aircraft_carrier",
           "amphibious", "battleship", "coast_guard", "torpedo_boat", "mine_warfare"]


def stretch_req(ctx: Ctx, rng: random.Random) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """小さい船（排水量の donor S）+ 速い船（速力の donor F）+ 大きい船（武装の donor P）を重ねる。"""
    ix = ctx.idx
    s = rng.randrange(len(ctx.train))
    S, ds = ctx.train[s], ctx.designs[s]
    if ds["ship_type"] not in C_TYPES or C.request_year(S) < 1890 or not isinstance(ds.get("speed_kn"), (int, float)):
        return None
    y = C.request_year(S)
    peers = np.where((ix.type == ds["ship_type"]) & (np.abs(ix.year - y) <= 10) & (np.arange(ix.n) != s))[0]
    if len(peers) < 4:
        return None
    bigger = [p for p in peers if ix.dref[p] > ds["displacement_normal_t"] * 1.1] if ds.get("displacement_normal_t") else []
    if len(bigger) < 2:
        return None
    p1, p2 = int(rng.choice(bigger)), int(rng.choice(bigger))
    P, P2 = ctx.designs[p1], ctx.designs[p2]
    fast = [p for p in peers if isinstance(ctx.designs[p].get("speed_kn"), (int, float))]
    f_idx = int(max(rng.sample(fast, min(4, len(fast))), key=lambda p: ctx.designs[p]["speed_kn"]))
    F = ctx.designs[f_idx]
    dref, basis = C.ref_displacement(ds)
    req: dict[str, Any] = {"country": S["country"], "year": y, "ship_type": ds["ship_type"], "mission": C.mission_tags(ds)}
    req["max_displacement_t"] = nice(dref * rng.uniform(1.0, 1.08)) if nice(dref * 1.0) >= dref else math.ceil(dref)
    req["displacement_basis"] = basis
    req["min_speed_kn"] = C._num(max(ds["speed_kn"], math.floor(F["speed_kn"] / 0.5) * 0.5))
    req["required"] = []
    units: list[str] = []
    for src in (P, P2):
        pool = b_unit_pool(src, rng)
        rng.shuffle(pool)
        for name, add in pool:
            if name in ("complement", "endurance") or name in units:
                continue
            trial = dict(req)
            trial["required"] = list(req["required"])
            trial["required_propulsion"] = list(req.get("required_propulsion") or [])
            merge_req(trial, add)
            if len(X.constraint_units(trial)) > 8:
                continue
            req = trial
            units.append(name)
    if not req.get("required_propulsion"):
        req.pop("required_propulsion", None)
    # donor endurance: sometimes
    if rng.random() < 0.3 and isinstance(P.get("endurance_nm"), (int, float)):
        e = P["endurance_nm"]
        step = 50 if e < 1000 else 100
        req["min_endurance_nm"] = int(max(step, math.floor(e * 0.97 / step) * step))
    req = X.order_req(req)
    nu = len(X.constraint_units(req))
    if not (5 <= nu <= 8):
        return None
    return req, {"donor_small": S["id"], "donor_fast": ix.ids[f_idx], "donor_armament": [ix.ids[p1], ix.ids[p2]], "units": units}


def relaxed_counts(ctx: Ctx, req: dict[str, Any]) -> dict[str, int]:
    """各制約を 1 つずつ外したときの train 実艦の充足数（難しさの根拠）。"""
    out: dict[str, int] = {}
    for u in X.constraint_units(req):
        r2 = json.loads(json.dumps(req))
        if u.startswith("required:"):
            r2["required"].remove(u.split(":", 1)[1])
        elif u.startswith("required_propulsion:"):
            r2["required_propulsion"].remove(u.split(":", 1)[1])
            if not r2["required_propulsion"]:
                del r2["required_propulsion"]
        else:
            key = {"displacement": "max_displacement_t", "speed": "min_speed_kn", "complement": "max_complement", "endurance": "min_endurance_nm",
                   "gun_barrels": "min_gun_barrels", "aircraft_count": "min_aircraft", "torpedo_tubes": "min_torpedo_tubes"}[u]
            r2.pop(key, None)
            if key == "min_gun_barrels":
                r2.pop("min_gun_barrels_cal_mm", None)
        out[u] = int(ctx.idx.mask(r2, ignore_hull=True).sum())
    return out


def gen_C(ctx: Ctx, reg: Registry, taken: Registry) -> list[dict[str, Any]]:
    rng = sub_rng("C", "search")
    out: list[dict[str, Any]] = []
    n_zero_target = 12
    n_zero = 0
    per_type: collections.Counter = collections.Counter()
    tries = 0
    while len(out) < N_PER_CAT and tries < 60000:
        tries += 1
        got = stretch_req(ctx, rng)
        if got is None:
            continue
        req, info = got
        if per_type[req["ship_type"]] >= 8:
            continue
        sat = sat_info(ctx, req)
        n = sat["n_sat_train_any"]
        if n > 4 or sat["n_sat_near_relaxed"] < 1:
            continue
        if (n == 0 and n_zero >= n_zero_target) or (n >= 1 and len(out) - n_zero >= N_PER_CAT - n_zero_target):
            continue
        rid = f"C{len(out) + 1:02d}"
        text0 = X.request_text_ext(req, 0)
        if reg.collide(req, text0) or taken.collide(req, text0):
            continue
        r = sub_rng("C", rid, tries)
        sat_groups = [ctx.idx.ids[j] for j in np.where(ctx.idx.mask(req, ignore_hull=True))[0]]
        row = make_row(ctx, rid, "C", req, None, r, {**sat, "relaxed_counts": relaxed_counts(ctx, req), "stretch": info, "tradeoff_units": X.constraint_units(req)}, exclude_ids=sat_groups)
        if reg.collide(req, row["request"]) or taken.collide(req, row["request"]):
            continue
        out.append(row)
        taken.add(rid, req, row["request"])
        per_type[req["ship_type"]] += 1
        n_zero += n == 0
    return out


# --------------------------------------------------------------------------------------------------
# D / E (hand-written cases) and their checks
# --------------------------------------------------------------------------------------------------
def gen_hand(ctx: Ctx, reg: Registry, taken: Registry, cases: list[dict[str, Any]], cat: str, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for k, cs in enumerate(cases[:limit], 1):
        rid = f"{cat}{k:02d}"
        req = X.order_req(cs["req"])
        r = sub_rng(cat, rid)
        sat = sat_info(ctx, req)
        meta = {**cs["meta"], **sat}
        row = make_row(ctx, rid, cat, req, cs["premise"] or None, r, meta, cs["extra_phrases"], cs["extra_phrases_ja"])
        coll = reg.collide(req, row["request"]) or taken.collide(req, row["request"])
        if coll:
            raise SystemExit(f"{rid}: collides with an existing request {coll}")
        taken.add(rid, req, row["request"])
        out.append(row)
    return out


# --------------------------------------------------------------------------------------------------
# pairs
# --------------------------------------------------------------------------------------------------
PAIR_PLAN = [("combo_sam_heli", 1), ("sam_add", 3), ("heli_add", 6), ("disp_down25", 5), ("speed_up4", 5), ("gun_barrels_up", 5), ("gun_cal_up", 6),
             ("year_earlier20", 4), ("year_later20", 3), ("country_change", 4), ("endurance_x2", 3), ("complement_half", 3), ("combo_disp_speed", 1), ("combo_cal_barrels", 1)]
PAIR_ERA_TYPES = {"heli_add": ["frigate", "destroyer", "corvette", "cruiser", "patrol", "amphibious", "aircraft_carrier", "coast_guard"],
                  "sam_add": ["frigate", "destroyer", "corvette", "cruiser", "aircraft_carrier", "fast_attack_craft", "amphibious", "patrol"],
                  "combo_sam_heli": ["frigate", "destroyer", "corvette", "cruiser"]}
PAIR_COUNTRIES = A_COUNTRIES + ["Spain", "Turkey", "India"]
EXP = {
    "disp_down25": [{"metric": "displacement", "op": "decrease"}],
    "speed_up4": [{"metric": "speed", "op": "increase"}, {"metric": "power_per_disp", "op": "increase"}],
    "gun_barrels_up": [{"metric": "gun_barrels", "op": "increase"}],
    "gun_cal_up": [{"metric": "main_gun_cal", "op": "increase"}],
    "heli_add": [{"metric": "helicopter", "op": "present_B"}, {"metric": "aviation", "op": "present_B"}],
    "sam_add": [{"metric": "sam", "op": "present_B"}, {"metric": "missiles", "op": "present_B"}],
    "year_earlier20": [{"metric": "modern_count", "op": "decrease"}],
    "year_later20": [{"metric": "modern_count", "op": "increase"}],
    "endurance_x2": [{"metric": "endurance", "op": "increase"}],
    "complement_half": [{"metric": "complement", "op": "decrease"}],
    "combo_disp_speed": [{"metric": "displacement", "op": "decrease"}, {"metric": "power_per_disp", "op": "increase"}],
    "combo_cal_barrels": [{"metric": "main_gun_cal", "op": "increase"}, {"metric": "gun_barrels", "op": "increase"}],
    "combo_sam_heli": [{"metric": "sam", "op": "present_B"}, {"metric": "helicopter", "op": "present_B"}],
}


def _prev(ctx: Ctx, g: np.ndarray, token: str) -> float:
    return float(ctx.idx.tok(token)[g].mean()) if len(g) else 0.0


_CELL_CACHE: dict[tuple, list[tuple[str, int, str]]] = {}


def pair_candidates(ctx: Ctx, kind: str) -> list[tuple[str, int, str]]:
    """(国, 年, 艦種): train の同国・同艦種の実艦が ±10 年内に 8 隻以上、test に同国・同艦種の実艦が ±2 年内に無い。
    ヘリ・SAM を足すペアは該当艦が少ないので、全ての国を対象に ±15 年で数え、test の実艦の有無は問わない（v0 の要求とは重複しない）。"""
    cap_kind = kind in ("heli_add", "sam_add", "combo_sam_heli")
    key = (id(ctx), cap_kind)
    types = PAIR_ERA_TYPES.get(kind, A_TYPES)
    if key not in _CELL_CACHE:
        cs = []
        countries = sorted(set(ctx.idx.country)) if cap_kind else PAIR_COUNTRIES
        for c in countries:
            for t in (PAIR_ERA_TYPES["sam_add"] if cap_kind else A_TYPES):
                for y in range(1962 if cap_kind else 1900, 2011):
                    if len(ctx.idx.group(c, t, y, 15 if cap_kind else 10)) >= 8:
                        cs.append((c, y, t))
        _CELL_CACHE[key] = cs
    return [x for x in _CELL_CACHE[key] if x[2] in types and (cap_kind or ctx.test_ship_near(x[0], x[2], x[1], 2) == 0)]


def build_pair(ctx: Ctx, kind: str, cell: tuple[str, int, str], rng: random.Random) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """(reqA, reqB, meta)。条件を満たさなければ None。B が train の実艦で（艦種一致で）1 隻以上充足できることを要求する（実現可能な変更だけを使う）。"""
    c, y, t = cell
    ix = ctx.idx
    changed: list[str] = []
    info: dict[str, Any] = {}
    if kind in ("year_earlier20", "year_later20"):
        yB = y - 20 if kind == "year_earlier20" else y + 20
        gA, gB = ix.group(c, t, y, 10), ix.group(c, t, yB, 10)
        if len(gA) < 8 or len(gB) < 8:
            return None
        mA, mB = float(np.median(ix.modern[gA])), float(np.median(ix.modern[gB]))
        if kind == "year_earlier20" and not (mA - mB >= 1.0):
            return None
        if kind == "year_later20" and not (mB - mA >= 1.0):
            return None
        both = np.concatenate([gA, gB])
        lr = loose_req(ctx, c, t, y, rng)
        if lr is None:
            return None
        reqA, i0 = lr
        reqA = dict(reqA)
        reqA.pop("ship_type_fine", None)
        reqA["required"] = [x for x in reqA["required"] if x.startswith("main_gun")]  # era-neutral equipment only
        basis = reqA["displacement_basis"]
        vals = [ctx.designs[j].get(f"displacement_{basis}_t") for j in both if isinstance(ctx.designs[j].get(f"displacement_{basis}_t"), (int, float))]
        sp = [ix.speed[j] for j in both if not np.isnan(ix.speed[j])]
        if len(vals) < 8 or len(sp) < 8:
            return None
        reqA["max_displacement_t"] = nice(quantile(vals, 0.8))
        reqA["min_speed_kn"] = C._num(max(2.0, math.floor(quantile(sp, 0.25) / 0.5) * 0.5))
        if reqA["required"]:
            cal = float(reqA["required"][0].split(">=")[1][:-2])
            cals = [ix.gun_cal[j] for j in both if ix.gun_cal[j] > 0]
            if len(cals) < 8:
                reqA["required"] = []
            else:
                reqA["required"] = [f"main_gun>={max((x for x in C.GUN_GRID if x <= quantile(cals, 0.2)), default=cal):g}mm"]
        reqB = dict(reqA)
        reqB["year"] = yB
        changed = ["year"]
        info = {"modern_median_A": mA, "modern_median_B": mB, "n_group_A": int(len(gA)), "n_group_B": int(len(gB))}
        return X.order_req(reqA), X.order_req(reqB), {"changed": changed, "data_support": info}
    if kind == "country_change":
        c2 = rng.choice([x for x in PAIR_COUNTRIES if x != c])
        gA, gB = ix.group(c, t, y, 12), ix.group(c2, t, y, 12)
        if len(gA) < 8 or len(gB) < 8:
            return None
        if ctx.test_ship_near(c2, t, y, 2):
            return None
        best = None
        metrics = {"torpedo_tubes": ix.tubes, "complement": ix.complement, "speed": ix.speed}
        with np.errstate(invalid="ignore"):
            metrics["power_per_disp"] = ix.power / ix.dref
        for m, arr_ in metrics.items():
            a, b = arr_[gA][~np.isnan(arr_[gA])], arr_[gB][~np.isnan(arr_[gB])]
            if len(a) < 6 or len(b) < 6:
                continue
            ma, mb = float(np.median(a)), float(np.median(b))
            spread = (quantile(a, 0.75) - quantile(a, 0.25) + quantile(b, 0.75) - quantile(b, 0.25)) / 2 + 1e-9
            z = abs(mb - ma) / spread
            rel = abs(mb - ma) / (abs(ma) + 1e-9)
            if z >= 0.8 and rel >= 0.25 and (best is None or z > best[0]):
                best = (z, m, ma, mb)
        if best is None:
            return None
        _z, m, ma, mb = best
        both = np.concatenate([gA, gB])
        lr = loose_req(ctx, c, t, y, rng)
        if lr is None:
            return None
        reqA, _i0 = lr
        reqA = dict(reqA)
        reqA.pop("ship_type_fine", None)
        reqA["required"] = [x for x in reqA["required"] if x.startswith("main_gun")][:1]
        basis = reqA["displacement_basis"]
        vals = [ctx.designs[j].get(f"displacement_{basis}_t") for j in both if isinstance(ctx.designs[j].get(f"displacement_{basis}_t"), (int, float))]
        sp = [ix.speed[j] for j in both if not np.isnan(ix.speed[j])]
        if len(vals) < 8 or len(sp) < 8:
            return None
        reqA["max_displacement_t"] = nice(quantile(vals, 0.8))
        reqA["min_speed_kn"] = C._num(max(2.0, math.floor(quantile(sp, 0.25) / 0.5) * 0.5))
        cals = [ix.gun_cal[j] for j in both if ix.gun_cal[j] > 0]
        if reqA["required"]:
            reqA["required"] = [f"main_gun>={max((x for x in C.GUN_GRID if x <= quantile(cals, 0.2)), default=20):g}mm"] if len(cals) >= 8 else []
        if m == "complement":
            reqA.pop("max_complement", None)
        reqB = dict(reqA)
        reqB["country"] = c2
        op = "increase" if mb > ma else "decrease"
        info = {"metric": m, "median_A_country": ma, "median_B_country": mb, "n_group_A": int(len(gA)), "n_group_B": int(len(gB))}
        return X.order_req(reqA), X.order_req(reqB), {"changed": ["country"], "data_support": info, "expected_override": [{"metric": m, "op": op}], "country_B": c2}
    need_cap = {"heli_add": ["helicopter"], "sam_add": ["sam"], "combo_sam_heli": ["sam", "helicopter"]}.get(kind)
    lr = loose_req(ctx, c, t, y, rng, tight=True, only_with=need_cap)
    if lr is None:
        return None
    reqA, i0 = lr
    reqA = dict(reqA)
    reqA.pop("ship_type_fine", None)
    reqA.pop("max_complement", None)
    g = ix.group(c, t, y, i0["group_window_years"])
    if need_cap:
        g = ix.group(c, t, y, i0["group_window_years"])
    reqB = json.loads(json.dumps(reqA))
    kinds = [kind]
    if kind.startswith("combo_"):
        kinds = {"combo_disp_speed": ["disp_down25", "speed_up4"], "combo_cal_barrels": ["gun_cal_up", "gun_barrels_up"], "combo_sam_heli": ["sam_add", "heli_add"]}[kind]
    for k in kinds:
        if k == "disp_down25":
            reqB["max_displacement_t"] = nice(reqA["max_displacement_t"] * 0.75)
            changed.append("max_displacement_t")
        elif k == "speed_up4":
            reqB["min_speed_kn"] = C._num(reqA["min_speed_kn"] + 4)
            changed.append("min_speed_kn")
        elif k in ("gun_barrels_up", "gun_cal_up"):
            cals = [ix.gun_cal[j] for j in g if ix.gun_cal[j] > 0]
            if len(cals) / max(1, len(g)) < 0.8:
                return None
            if k == "gun_barrels_up" and kind == "gun_barrels_up":
                cal = max((x for x in C.GUN_GRID if x <= quantile(cals, 0.4)), default=None)
                if not cal or cal < 20:
                    return None
                reqA["required"] = [f"main_gun>={cal}mm"]
                reqB["required"] = [f"main_gun>={cal}mm"]
                nb = [ix.barrels(cal)[j] for j in g if ix.barrels(cal)[j] > 0]
                if len(nb) < 5:
                    return None
                a = int(max(2, round(float(np.median(nb)))))
                for rq, v in ((reqA, a), (reqB, a + 2)):
                    rq["min_gun_barrels"] = v
                    rq["min_gun_barrels_cal_mm"] = cal
                changed.append("min_gun_barrels")
            elif k == "gun_cal_up":
                cal = max((x for x in C.GUN_GRID if x <= quantile(cals, 0.4)), default=None)
                if not cal or cal < 20:
                    return None
                i_cal = C.GUN_GRID.index(cal)
                hi = next((x for x in C.GUN_GRID[i_cal + 1:] if x >= cal * 1.3), None)
                if not hi:
                    return None
                reqA["required"] = [f"main_gun>={cal}mm"]
                reqB["required"] = [f"main_gun>={hi}mm"]
                changed.append("required:main_gun")
            else:  # gun_barrels_up inside combo_cal_barrels
                cal = float(reqB["required"][0].split(">=")[1][:-2]) if reqB.get("required") else None
                calA = float(reqA["required"][0].split(">=")[1][:-2]) if reqA.get("required") else None
                if not calA:
                    return None
                nb = [ix.barrels(calA)[j] for j in g if ix.barrels(calA)[j] > 0]
                if len(nb) < 5:
                    return None
                a = int(max(2, round(float(np.median(nb)))))
                reqA["min_gun_barrels"], reqA["min_gun_barrels_cal_mm"] = a, calA
                reqB["min_gun_barrels"], reqB["min_gun_barrels_cal_mm"] = a + 2, calA
                changed.append("min_gun_barrels")
        elif k == "heli_add":
            if not (0.10 <= _prev(ctx, g, "helicopter") <= 1.0):
                return None
            for rq in (reqA, reqB):
                rq["required"] = [x for x in rq.get("required", []) if x not in ("helicopter", "aircraft")]
            reqB["required"] = list(reqB["required"]) + ["helicopter"]
            changed.append("required:helicopter")
        elif k == "sam_add":
            if not (0.10 <= _prev(ctx, g, "sam") <= 1.0):
                return None
            for rq in (reqA, reqB):
                rq["required"] = [x for x in rq.get("required", []) if x != "sam"]
            reqB["required"] = list(reqB["required"]) + ["sam"]
            changed.append("required:sam")
        elif k == "endurance_x2":
            en = [ix.endurance[j] for j in g if not np.isnan(ix.endurance[j])]
            if len(en) / len(g) < 0.6 or len(en) < 6:
                return None
            q40 = quantile(en, 0.4)
            step = 50 if q40 < 1000 else 100
            a = int(max(step, math.floor(q40 / step) * step))
            if a * 2 > quantile(en, 0.95):
                return None
            reqA["min_endurance_nm"], reqB["min_endurance_nm"] = a, a * 2
            changed.append("min_endurance_nm")
        elif k == "complement_half":
            cm = [ix.complement[j] for j in g if not np.isnan(ix.complement[j])]
            if len(cm) / len(g) < 0.7 or len(cm) < 6:
                return None
            a = int(math.ceil(quantile(cm, 0.6)))
            if a < 8:
                return None
            reqA["max_complement"], reqB["max_complement"] = a, a // 2
            changed.append("max_complement")
        else:
            raise ValueError(k)
    reqA, reqB = X.order_req(reqA), X.order_req(reqB)
    return reqA, reqB, {"changed": changed, "data_support": {"n_group": int(len(g))}}


def gen_pairs(ctx: Ctx, reg: Registry, taken: Registry) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pair_no = 0
    cell_used: set[tuple] = set()
    for kind, quota in PAIR_PLAN:
        rng = sub_rng("P", kind)
        cands = pair_candidates(ctx, kind)
        rng.shuffle(cands)
        got = 0
        per_country: collections.Counter = collections.Counter()
        for cell in cands:
            if got >= quota:
                break
            c, y, t = cell
            cap = kind in ("heli_add", "sam_add", "combo_sam_heli")
            if ((c, t) in {(x[0], x[2]) for x in cell_used} and not cap) or per_country[c] >= (3 if cap else 2):
                continue
            if cap and any(x[0] == c and x[2] == t and abs(x[1] - y) < 6 for x in cell_used):
                continue
            r = sub_rng("P", kind, *cell)
            built = build_pair(ctx, kind, cell, r)
            if built is None:
                continue
            reqA, reqB, pm = built
            mA, mB = ctx.idx.mask(reqA, ignore_hull=False), ctx.idx.mask(reqB, ignore_hull=False)
            if mA.sum() < 1 or mB.sum() < 1:
                continue
            pid = f"pair_{pair_no + 1:02d}"
            exp = pm.get("expected_override") or EXP[kind]
            rowA = make_row(ctx, f"{pid}_A", "P", reqA, None, sub_rng("P", pid, "A"), {})
            rowB = make_row(ctx, f"{pid}_B", "P", reqB, None, sub_rng("P", pid, "B"), {})
            bad = False
            for rq, rw in ((reqA, rowA), (reqB, rowB)):
                if reg.collide(rq, rw["request"]) or taken.collide(rq, rw["request"]):
                    bad = True
            if bad or canon(reqA) == canon(reqB):
                continue
            for role, rw, rq, mm in (("A", rowA, reqA, mA), ("B", rowB, reqB, mB)):
                rw["meta"].update({"pair": {"id": pid, "role": role, "kind": kind, "changed": pm["changed"], "expected": exp, "data_support": pm["data_support"]},
                                   "n_sat_train_same_type": int(mm.sum()), "cell": list(cell), "test_ships_within_2y": ctx.test_ship_near(c, t, y, 2)})
                taken.add(rw["id"], rq, rw["request"])
            rows += [rowA, rowB]
            cell_used.add(cell)
            per_country[c] += 1
            pair_no += 1
            got += 1
        if got < quota:
            print(f"WARNING: pair kind {kind}: got {got}/{quota}", file=sys.stderr)
    return rows


# --------------------------------------------------------------------------------------------------
# forgetting subset (T1 / T2 / T3 of shipknow_v1_2_rag test)
# --------------------------------------------------------------------------------------------------
def forget_subset(per_task: int = 1000, seed: int = SEED) -> dict[str, Any]:
    rows = [json.loads(l) for l in V1_2_TEST.read_text(encoding="utf-8").splitlines() if l.strip()]
    ctype = {r["id"]: r["ship_type"] for r in (json.loads(l) for l in (C.V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
    ids: dict[str, list[str]] = {}
    strata_info: dict[str, Any] = {}
    for task in ("T1", "T2", "T3"):
        tr = [r for r in rows if r["task"] == task]
        cells: dict[tuple, list[str]] = collections.defaultdict(list)
        for r in tr:
            cells[(r.get("variant", ""), ctype.get(r["row"], "?"))].append(r["id"])
        total = len(tr)
        quota = {k: len(v) * per_task / total for k, v in cells.items()}
        take = {k: int(q) for k, q in quota.items()}
        rest = per_task - sum(take.values())
        for k in sorted(cells, key=lambda k: (-(quota[k] - take[k]), str(k)))[:rest]:
            take[k] += 1
        rng = random.Random(f"{seed}|{task}")
        chosen: list[str] = []
        for k in sorted(cells, key=str):
            v = sorted(cells[k])
            rng.shuffle(v)
            chosen += v[: take[k]]
        ids[task] = sorted(chosen)
        strata_info[task] = {"available": total, "selected": len(chosen), "strata": len(cells)}
    return {"seed": seed, "source": "data/navspec/shipknow_v1_2_rag/test.jsonl", "stratified_by": "(task, variant, coarse ship_type of the target class), proportional allocation, largest remainder",
            "per_task": per_task, "counts": {k: len(v) for k, v in ids.items()}, "info": strata_info, "ids": ids,
            "all_ids": sorted(i for v in ids.values() for i in v)}


# --------------------------------------------------------------------------------------------------
# independent verification of the written files (slow paths: X.req_checks on every train design, no index)
# --------------------------------------------------------------------------------------------------
def _slow_satisfiers(req: dict[str, Any], ctx: Ctx, max_year: int | None = None, ignore_hull: bool = True) -> list[str]:
    out = []
    for rec, d in zip(ctx.train, ctx.designs):
        if max_year is not None and C.request_year(rec) > max_year:
            continue
        if X.all_satisfied(req, d, ignore_hull=ignore_hull):
            out.append(rec["id"])
    return out


def verify(out_dir: Path) -> dict[str, Any]:
    ctx = Ctx()
    reg = load_v0_registry()
    probs = [json.loads(l) for l in (out_dir / "problems.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    pairs = [json.loads(l) for l in (out_dir / "pairs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    res: dict[str, Any] = {"n_problems": len(probs), "n_pair_rows": len(pairs), "by_category": dict(collections.Counter(r["category"] for r in probs))}
    errs: collections.Counter = collections.Counter()
    examples: list[str] = []

    def bad(kind: str, rid: str) -> None:
        errs[kind] += 1
        if len(examples) < 20:
            examples.append(f"{kind}:{rid}")

    for r in probs + pairs:
        u = json.loads(r["messages"][1]["content"])
        if r["messages"][0]["content"] != BD.SYSTEM_D or len(r["messages"]) != 2:
            bad("system_or_gold", r["id"])
        if u["req"] != r["req"] or u["request"] != r["request"]:
            bad("req_text_mismatch", r["id"])
        if len(u["nbr"]) != 3:
            bad("nbr_count", r["id"])
        excl = {ctx.group_of[i] for i in r["meta"].get("excluded_ids", [])}
        for nid in r["meta"]["nbr"]:
            if ctx.assign[nid] != "train":
                bad("nbr_not_train", r["id"])
            if ctx.group_of[nid] in excl:
                bad("nbr_in_excluded_group", r["id"])
        for y in r["meta"]["nbr_years"]:
            if y > r["req"]["year"] + 2:
                bad("nbr_future", r["id"])
        for nb in u["nbr"]:
            for hy, _t in nb.get("h", []):
                if hy > r["req"]["year"]:
                    bad("hist_future", r["id"])
        for i in r["meta"].get("excluded_ids", []):
            terms = [t for t in BD.name_terms(ctx.class_name.get(i, ""), ctx.names.get(i, [])) if not re.fullmatch(r"[0-9\s\-]+", t)]
            terms = BD.drop_generic(terms, r["req"])
            rx = BD.term_regex(terms)
            hist = " ".join(t_ for nb in u["nbr"] for _y, t_ in nb.get("h", []))
            if rx and rx.search(hist):
                bad("witness_name_in_history", r["id"])
        if r["category"] in ("A", "B", "C", "P"):
            sat_any = int(sat_info(ctx, r["req"])["n_sat_train_any"])
            if sat_any != r["meta"].get("n_sat_train_any", sat_any):
                bad("n_sat_changed", r["id"])
    for r in probs:
        c = r["category"]
        if c == "A":
            if r["meta"]["test_ships_within_2y"] != 0 or ctx.test_ship_near(*r["meta"]["cell"][:1], r["meta"]["cell"][2], r["meta"]["cell"][1], 2) != 0:
                bad("A_test_ship_in_cell", r["id"])
            if r["meta"]["n_sat_train_same_type"] < 3:
                bad("A_few_satisfiers", r["id"])
        if c == "B":
            w = ctx.train[ctx.id_pos[r["meta"]["witness"]]]
            d = ctx.designs[ctx.id_pos[w["id"]]]
            if not X.all_satisfied(r["req"], d):
                bad("B_witness_not_satisfying", r["id"])
            if not 4 <= len(X.constraint_units(r["req"])) <= 7:
                bad("B_units", r["id"])
            if r["meta"]["witness"] in r["meta"]["nbr"]:
                bad("B_witness_in_nbr", r["id"])
        if c == "C":
            sats = _slow_satisfiers(r["req"], ctx)
            if len(sats) != r["meta"]["n_sat_train_any"] or len(sats) > 4:
                bad("C_n_sat", r["id"])
            if set(sats) & set(r["meta"]["nbr"]):
                bad("C_satisfier_in_nbr", r["id"])
        if c == "E":
            sats = _slow_satisfiers(r["req"], ctx, max_year=r["req"]["year"] + 5, ignore_hull=False)
            if sats or r["meta"]["n_sat_train_same_type_up_to_year"]:
                bad("E_satisfiable_by_real_ship", r["id"])
            if not r["meta"].get("why_impossible", {}).get("reason"):
                bad("E_no_reason", r["id"])
    by_pair: dict[str, dict[str, dict[str, Any]]] = collections.defaultdict(dict)
    for r in pairs:
        by_pair[r["meta"]["pair"]["id"]][r["meta"]["pair"]["role"]] = r
    res["n_pairs"] = len(by_pair)
    for pid, ab in by_pair.items():
        a, b = ab.get("A"), ab.get("B")
        if not a or not b:
            bad("pair_incomplete", pid)
            continue
        diff = sorted(k for k in set(a["req"]) | set(b["req"]) if a["req"].get(k) != b["req"].get(k))
        if not 1 <= len(diff) <= 4:
            bad("pair_diff_size", pid)
        if a["meta"]["n_sat_train_same_type"] < 1 or b["meta"]["n_sat_train_same_type"] < 1:
            bad("pair_infeasible_side", pid)
    ov = overlap_report(probs, pairs, reg)
    res["overlap"] = {k: ov[k] for k in ("external_collisions", "internal_collisions", "same_country_year_type_as_a_v0_request")}
    if any(ov["external_collisions"].values()) or ov["internal_collisions"]:
        errs["overlap"] += 1
    res["errors"] = dict(errs)
    res["error_examples"] = examples
    res["ok"] = not errs and res["n_pairs"] == N_PAIRS and all(v == N_PER_CAT for v in res["by_category"].values()) and len(res["by_category"]) == 5
    return res


# --------------------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------------------
def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cat in sorted({r["category"] for r in rows}):
        rs = [r for r in rows if r["category"] == cat]
        out[cat] = {"n": len(rs), "countries": dict(collections.Counter(r["req"]["country"] for r in rs).most_common()),
                    "ship_types": dict(collections.Counter(r["req"]["ship_type"] for r in rs).most_common()),
                    "era5": dict(collections.Counter(f"{ERA5[era5(r['req']['year'])][0]}-{ERA5[era5(r['req']['year'])][1]}" for r in rs)),
                    "n_units": dict(collections.Counter(r["meta"]["n_units"] for r in rs)),
                    "n_fictional": sum(r["fictional"] for r in rs)}
    return out


def overlap_report(rows_main: list[dict[str, Any]], rows_pairs: list[dict[str, Any]], reg: Registry) -> dict[str, Any]:
    """評価データの全要求を v0 の train / valid / test / scenarios、および評価データ内（problems 同士・pairs 同士・problems と pairs）と突き合わせる。"""
    rep: dict[str, Any] = {"v0_requests_checked": reg.n, "eval_requests": len(rows_main) + len(rows_pairs)}
    allrows = rows_main + rows_pairs
    ev = Registry()
    internal: list[dict[str, Any]] = []
    for r in allrows:
        coll = ev.collide(r["req"], r["request"])
        # the A and B rows of a pair may share the main key (the changed constraint is not part of it); anything else is a duplicate
        if coll.get("main_key", "").startswith("pair_") and coll["main_key"].rsplit("_", 1)[0] == r["id"].rsplit("_", 1)[0]:
            del coll["main_key"]
        if coll:
            internal.append({"id": r["id"], **coll})
        ev.add(r["id"], r["req"], r["request"])
    ext: dict[str, list[Any]] = {"req_equal": [], "text_equal": [], "main_key_equal": []}
    cell_hits = {"same_country_year_type": [], "same_country_type_within_2y_of_v0_test": []}
    for r in allrows:
        c = reg.collide(r["req"], r["request"])
        if "req" in c:
            ext["req_equal"].append([r["id"], c["req"]])
        if "text" in c:
            ext["text_equal"].append([r["id"], c["text"]])
        if "main_key" in c:
            ext["main_key_equal"].append([r["id"], c["main_key"]])
        h = reg.cell_hits(r["req"])
        if h:
            cell_hits["same_country_year_type"].append([r["id"], h])
    rep["external_collisions"] = {k: len(v) for k, v in ext.items()}
    rep["external_collision_ids"] = ext
    rep["internal_collisions"] = internal
    rep["same_country_year_type_as_a_v0_request"] = {"n": len(cell_hits["same_country_year_type"]), "note": "informational: (country, year, ship_type) equal to some v0 request; the main constraints differ (main_key_equal is 0)",
                                                    "by_category": dict(collections.Counter(re.sub(r"[0-9_].*", "", i[0]) if not i[0].startswith("pair") else "P" for i in cell_hits["same_country_year_type"]))}
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    if args.verify_only:
        rep = verify(args.out)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        raise SystemExit(0 if rep["ok"] else 1)
    ctx = Ctx()
    reg = load_v0_registry()
    taken = Registry()
    A = gen_A(ctx, reg, taken)
    B = gen_B(ctx, reg, taken)
    Cc = gen_C(ctx, reg, taken)
    D = gen_hand(ctx, reg, taken, K.cases_D(), "D", N_PER_CAT)
    E = gen_hand(ctx, reg, taken, K.cases_E(), "E", N_PER_CAT)
    problems = A + B + Cc + D + E
    pairs = gen_pairs(ctx, reg, taken)
    args.out.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out / "problems.jsonl", problems)
    write_jsonl(args.out / "pairs.jsonl", pairs)
    write_jsonl(args.out / "all.jsonl", problems + pairs)  # 推論は 1 回で済ませるための連結（problems + pairs。id は重複しない）
    forget = forget_subset()
    (args.out / "forget_ids.json").write_text(json.dumps(forget, ensure_ascii=False, indent=1), encoding="utf-8")
    ov = overlap_report(problems, pairs, reg)
    (args.out / "overlap_check.json").write_text(json.dumps(ov, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = collections.Counter(r["meta"]["pair"]["kind"] for r in pairs if r["meta"]["pair"]["role"] == "A")
    manifest = {
        "name": args.out.name, "seed": SEED, "purpose": "Before/after comparison of design ability (v1_2 vs design-v0). Evaluation only: never train on it, never mix with design_v0 test / scenarios.",
        "files": {"problems": "problems.jsonl", "pairs": "pairs.jsonl", "all": "all.jsonl (problems + pairs, for one inference run)", "forget_ids": "forget_ids.json", "overlap_check": "overlap_check.json"},
        "counts": {"problems": len(problems), "by_category": dict(collections.Counter(r["category"] for r in problems)), "pairs": len(pairs) // 2, "pair_rows": len(pairs)},
        "system_prompt_identical_to_design_v0": all(r["messages"][0]["content"] == BD.SYSTEM_D for r in problems + pairs), "system_prompt": BD.SYSTEM_D,
        "extended_req_keys": X.EXT_KEYS, "req_key_order": X.REQ_KEY_ORDER,
        "neighbor_rule": BD.NEIGHBOR_RULE,
        "neighbor_notes": {"pool": "train real ships only (design_v0 eligible)", "A_D_E": "NB-1 as is", "B": "the class group of the witness is excluded", "C": "the class groups of all train ships that satisfy the request are excluded (0-4 groups)",
                           "D_fictional": "the country matches no real ship, so the same-country lineage slot is empty and the neighbours are contemporaries of other countries (+ overall fill)",
                           "extended_keys_in_retrieval": "min_aircraft -> helicopter, min_torpedo_tubes -> torpedo_tubes, min_gun_barrels_cal_mm -> main_gun>=grid"},
        "pair_kinds": dict(kinds), "category_stats": summarize(problems), "pair_stats": summarize(pairs),
        "overlap": {k: ov[k] for k in ("external_collisions", "internal_collisions")},
        "never_in_input": ["witness / target ship names", "answers (no gold design)"],
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"problems": manifest["counts"], "pair_kinds": manifest["pair_kinds"], "overlap": manifest["overlap"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
