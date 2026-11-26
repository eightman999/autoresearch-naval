"""design-v1 生成器が共有するデータ層: 実艦レコード・設計・分割・検索インデックス・近傍選択（規則 NB-1）。

分割は shipknow_v0_2 の艦級グループ割り当て（splits.json）をそのまま継承する。1 つのエピソードに関わる実艦（元艦・modify の B・context の過去の艦）は
すべて同じ split の艦だけから選ぶ（同一元艦・同一 pair / repair 派生は同じ split に閉じ込める）。近傍（nbr）は train の実艦だけから、
エピソードに関わる艦級グループを除いて検索する。

LLM は使わない。NSDB は mode=ro（shipknow_history.open_ro）で艦歴・艦名を読むだけ。
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_design as BD  # noqa: E402
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402
import shipknow_history as H  # noqa: E402
import shipknow_retriever as R  # noqa: E402

# 架空国家名。eval_v1 の D（Valdoria / Norvane / ...）と重複しない新しい名前。train / valid / test で互いに素（test の名前は train / valid に出さない）。
FICTIONAL = {
    "train": ["Varnia", "Orlenland", "Castellane", "Drovia", "Helmark", "Isandria", "Kovaria", "Lunaria", "Montrevin", "Nordheim", "Pelagia", "Quorin", "Rhosland",
              "Sarvonia", "Talmeria", "Ulvenia", "Westmarch", "Xandria"],
    "valid": ["Yarrowen", "Corvania", "Belmora", "Ostravia"],
    "test": ["Auralia", "Bastavia", "Dalmoria", "Eskarra", "Ferrovia", "Gallimar", "Hadrovia", "Ilvaria", "Jorvania", "Kharnak"],
}
EVAL_FICTIONAL = ["Valdoria", "Norvane", "Kestria", "Aurelan", "Tarsonia", "Meridia", "Zephyria", "Brannoch", "Solmara", "Ysbelle"]
HELDOUT_COUNTRY_PAIR_MOD = 5  # modify の国変更: (元の国, 新しい国) の hash の 1/5 は test 専用


def heldout_country_pair(a: str, b: str) -> bool:
    return int.from_bytes(hashlib.sha256(f"{a}->{b}".encode()).digest()[:4], "big") % HELDOUT_COUNTRY_PAIR_MOD == 0


HELDOUT_YEAR_PAIR_MOD = 5  # modify の年代変更: (国, 元の十年, 新しい十年) の hash の 1/5 は test 専用


def heldout_year_change(country: str, y0: int, y1: int) -> bool:
    return int.from_bytes(hashlib.sha256(f"{country}|{y0 // 10}->{y1 // 10}".encode()).digest()[:4], "big") % HELDOUT_YEAR_PAIR_MOD == 0


def sane_geometry(d: dict[str, Any]) -> bool:
    """NSDB の単位の誤り（全長が feet 表記など）で、全長/Δ^(1/3) が 16 超（99.9 パーセンタイル 36 の外れ値。validator の閾値には外れ値が含まれるので落ちない）、
    または L/B が 18 超の実艦は gold に使わない。"""
    r = C.ratios_of(d)
    return r.get("slender", 0) <= 16 and r.get("lb", 0) <= 18


def norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def canon_req(req: dict[str, Any]) -> str:
    return json.dumps(req, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# 元艦の選び方の述語（名前 -> f(rec, design)）。split ごとに述語を満たす艦の一覧をキャッシュする。
def _nb(d: dict[str, Any]) -> int:
    return X.gun_barrels(d)


PREDICATES: dict[str, Callable[[dict[str, Any], dict[str, Any]], bool]] = {
    "any": lambda r, d: True,
    "barrels": lambda r, d: X.gun_barrels(d) >= 2,
    "tubes": lambda r, d: X.torpedo_tubes(d) >= 2,
    "aircraft": lambda r, d: X.aircraft_count(d) >= 1,
    "prop": lambda r, d: bool(d.get("propulsion")),
    "small": lambda r, d: C.ref_displacement(d)[0] is not None and C.ref_displacement(d)[0] < 1500,
    "tiny": lambda r, d: C.ref_displacement(d)[0] is not None and C.ref_displacement(d)[0] < 300,
    "old": lambda r, d: C.request_year(r) <= 1948,
    "older": lambda r, d: C.request_year(r) <= 1936,
    "modern": lambda r, d: C.request_year(r) >= 1962,
    "heli_ok": lambda r, d: (C.ref_displacement(d)[0] or 0) >= 1500 and C.request_year(r) >= 1958 and d["ship_type"] in ("destroyer", "frigate", "cruiser", "escort", "corvette", "patrol", "coast_guard", "amphibious", "aircraft_carrier", "auxiliary", "light_cruiser", "heavy_cruiser"),
    "endurance": lambda r, d: K._isnum(d.get("endurance_nm")),
    "gun_big": lambda r, d: (C.main_gun_mm(d) or 0) >= 76 and d["ship_type"] not in ("submarine",),
    "combatant": lambda r, d: d["ship_type"] in ("destroyer", "frigate", "light_cruiser", "heavy_cruiser", "cruiser", "battleship", "battlecruiser", "corvette", "escort", "aircraft_carrier", "fast_attack_craft"),
    "has_helicopter": lambda r, d: "helicopter" in (d.get("air") or []),
    "big": lambda r, d: (C.ref_displacement(d)[0] or 0) >= 4000,
    "multi_weapon": lambda r, d: len(d.get("weapons") or []) >= 7,
    "small_postwar": lambda r, d: C.ref_displacement(d)[0] is not None and C.ref_displacement(d)[0] < 1500 and C.request_year(r) >= 1960 and d["ship_type"] != "submarine",
    "postwar_major": lambda r, d: 1946 <= C.request_year(r) <= 1966 and r["country"] in ("United-States", "United-Kingdom", "France", "Russian", "Italy", "Japan", "Germany"),
}
TYPE_BALANCE_EXP = 0.6  # 元艦の艦種は 件数^0.6 に比例して選ぶ（patrol 偏重を弱める）


class Data:
    def __init__(self, limit: int | None = None):
        self.recs_all = [json.loads(l) for l in (C.V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        sp = json.loads((C.V0_DIR / "splits.json").read_text(encoding="utf-8"))
        self.assign: dict[str, str] = sp["assignments"]
        self.group_of: dict[str, str] = sp["group_of"]
        self.rec_by_id = {r["id"]: r for r in self.recs_all}
        elig = [r for r in self.recs_all if C.design_eligible(r)]
        if limit:
            elig = elig[:limit]
        self.th = K.thresholds()
        self.designs: dict[str, dict[str, Any]] = {r["id"]: C.design_from_record(r) for r in elig}
        # 元艦の設計が（要求なしで）validator に合格するものだけを gold の候補にする
        self.gold_ok: dict[str, bool] = {}
        for r in elig:
            d = self.designs[r["id"]]
            self.gold_ok[r["id"]] = K.check_design({"year": C.request_year(r), "country": r["country"]}, d)["ok"] and sane_geometry(d)
        self.elig = elig
        self.n_gold_rejected = sum(1 for r in elig if not self.gold_ok[r["id"]])
        self.by_split: dict[str, list[dict[str, Any]]] = {s: [r for r in elig if self.assign[r["id"]] == s and self.gold_ok[r["id"]]] for s in K.SPLITS}
        self.idx = {s: K.DesignIndex(self.by_split[s], self.designs) for s in K.SPLITS}
        self.idx_train = self.idx["train"]
        self.pool = R.Pool([r for r in elig if self.assign[r["id"]] == "train"], self.group_of)
        self._anchor_cache: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
        # NSDB（mode=ro）: 艦歴・艦名
        conn = H.open_ro()
        texts = H.class_texts(conn)
        ship_names = H.class_ship_names(conn)
        conn.close()
        self.events_by_id: dict[str, list[dict[str, Any]]] = {}
        self.terms: dict[str, list[str]] = {}
        for r in elig:
            row = texts.get(int(r["id"][1:]))
            if row:
                self.events_by_id[r["id"]] = H.events_for_class(row)
                self.terms[r["id"]] = BD.name_terms(row["CLASS_NAME"], ship_names.get(row["source_url"], []))
        self.countries_by_split = {s: sorted({r["country"] for r in self.by_split[s]}) for s in K.SPLITS}
        # ship_type の初出年（train の 3 番目に早い艦級グループの年。艦種そのものが年代的に不可能かの判定）
        self.type_first_year = self._type_first_year()
        self.type_min_year: dict[str, int] = {}
        for r in self.by_split["train"]:
            t = self.designs[r["id"]]["ship_type"]
            self.type_min_year[t] = min(C.request_year(r), self.type_min_year.get(t, 9999))

    # ---------------- anchors
    def _type_first_year(self) -> dict[str, int]:
        by: dict[str, dict[str, int]] = {}
        for r in self.by_split["train"]:
            t = self.designs[r["id"]]["ship_type"]
            g = self.group_of[r["id"]]
            y = C.request_year(r)
            by.setdefault(t, {})
            by[t][g] = min(y, by[t].get(g, 9999))
        out = {}
        for t, gy in by.items():
            ys = sorted(gy.values())
            if len(ys) >= 5:
                out[t] = ys[2]
        return out

    def anchors(self, split: str, pred: str) -> dict[str, list[dict[str, Any]]]:
        key = (split, pred)
        if key not in self._anchor_cache:
            f = PREDICATES[pred]
            by_type: dict[str, list[dict[str, Any]]] = {}
            for r in self.by_split[split]:
                d = self.designs[r["id"]]
                if f(r, d):
                    by_type.setdefault(d["ship_type"], []).append(r)
            self._anchor_cache[key] = by_type
        return self._anchor_cache[key]

    def pick(self, split: str, rng: random.Random, pred: str = "any", exclude_groups: set[str] | None = None, avoid_ids: set[str] | None = None,
             types: set[str] | None = None, balance: bool = True) -> dict[str, Any] | None:
        by_type = self.anchors(split, pred)
        ts = [t for t in sorted(by_type) if (types is None or t in types)]
        if not ts:
            return None
        for _ in range(20):
            w = [len(by_type[t]) ** (TYPE_BALANCE_EXP if balance else 1.0) for t in ts]
            t = rng.choices(ts, weights=w)[0]
            r = rng.choice(by_type[t])
            if exclude_groups and self.group_of[r["id"]] in exclude_groups:
                continue
            if avoid_ids and r["id"] in avoid_ids:
                continue
            return r
        return None

    def design(self, rec_or_id: Any) -> dict[str, Any]:
        rid = rec_or_id if isinstance(rec_or_id, str) else rec_or_id["id"]
        return K.order_design(__import__("copy").deepcopy(self.designs[rid]))

    # ---------------- neighbours (rule NB-1, train pool, several excluded groups)
    def neighbors(self, req: dict[str, Any], source_ids: list[str], k: int = 3, max_hist: int = 3, hist_chars: int = 90) -> list[dict[str, Any]]:
        if k <= 0:
            return []
        rq = X.retrieval_req(req)
        rq.setdefault("max_displacement_t", req.get("max_displacement_t") or 100)
        groups = {self.group_of[s] for s in source_ids if s in self.group_of}
        picks = self._select(rq, groups, set(source_ids), k)
        terms: list[str] = []
        own: set[str] = set()
        for s in source_ids:
            terms += self.terms.get(s, [])
            own |= H.own_prefixes(self.events_by_id.get(s, []))
        terms = BD.drop_generic(sorted(set(terms)), req)
        nbr, _n = BD.nbr_block(self.pool, picks, self.events_by_id, req["year"], None, terms, max_hist, hist_chars, own)
        return nbr

    def _select(self, req: dict[str, Any], exclude_groups: set[str], exclude_ids: set[str], k: int) -> list[tuple[int, float]]:
        """BD.select_neighbors と同じ規則 NB-1。除外する艦級グループ・id を複数指定できる。"""
        pool = self.pool
        prm = BD.REQ_PARAMS
        view = BD.req_view(req)
        d = pool.distances(view, "T3", prm)
        d_lin = pool.distances(view, "T3", BD.lineage_params(prm))
        years = np.array([r["year"] for r in pool.recs], dtype=float)
        ok = years <= req["year"] + 2
        if exclude_groups:
            ok &= ~np.isin(pool.groups, list(exclude_groups))
        if exclude_ids:
            if not hasattr(pool, "_ids_arr"):
                pool._ids_arr = np.array(pool.ids)
            ok &= ~np.isin(pool._ids_arr, list(exclude_ids))
        nation = req["country"].replace("-", " ")
        same = pool.country == nation
        dm = np.where(ok, d, np.inf)
        order = np.argsort(dm, kind="stable")
        cutoff = (dm[order[min(k, len(order)) - 1]] if len(order) else np.inf) + BD.SLOT_SLACK
        dl = np.where(ok & same, d_lin, np.inf)
        order_lin = np.argsort(dl, kind="stable")
        seen: set[str] = set()
        chosen: list[int] = []

        def take(ord_: np.ndarray, dist: np.ndarray, mask: np.ndarray, limit: int, cap: float | None) -> None:
            got = 0
            for j in ord_:
                if got >= limit or len(chosen) >= k:
                    return
                if not (ok[j] and mask[j]) or int(j) in chosen:
                    continue
                if cap is not None and dist[j] > cap:
                    return
                key = json.dumps(C.nbr_payload(pool.recs[int(j)], None), sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                chosen.append(int(j))
                got += 1

        take(order_lin, dl, same, 2, BD.LINEAGE_CAP)
        take(order, dm, ~same & (years >= req["year"] - 10), k - len(chosen), cutoff)
        take(order, dm, np.ones(len(years), dtype=bool), k - len(chosen), None)
        chosen.sort(key=lambda j: d[j])
        return [(j, float(d[j])) for j in chosen]


# --------------------------------------------------------------------------------------------------
# forbidden request sets (existing datasets) for the overlap guard
# --------------------------------------------------------------------------------------------------
def load_forbidden() -> dict[str, set[str]]:
    """既存データ（design-v0 の train / valid / test / scenarios、shipknow_design_eval_v1 の all）の正規化要求文と構造化要求の集合。"""
    texts: set[str] = set()
    reqs: set[str] = set()
    for p in [C.OUT_DIR / f"{s}.jsonl" for s in ("train", "valid", "test", "scenarios")] + [K.EVAL_V1_DIR / "all.jsonl"]:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            u = json.loads(e["messages"][1]["content"])
            if "request" in u:
                texts.add(norm_text(u["request"]))
            if "req" in u:
                reqs.add(canon_req(u["req"]))
    return {"texts": texts, "reqs": reqs}
