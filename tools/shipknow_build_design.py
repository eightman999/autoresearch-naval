"""設計タスク D の試作データ（要求 -> 設計 JSON）を作る。

    .venv/bin/python tools/shipknow_build_design.py --tokenizer <Qwen3.5 tokenizer.json>
    .venv/bin/python tools/shipknow_build_design.py --verify-only        # 出力を独立に再検証（漏れ・要求充足・近傍の分割）

入力: data/navspec/shipknow_v0_2/{records.jsonl, splits.json}（分割は艦級グループ単位のまま）、nsdb/NSDB.db（mode=ro。艦歴・艦名）
出力: data/navspec/shipknow_design_v0/{train,valid,test}.jsonl, manifest.json, scenarios.jsonl

例: system / user(JSON: request 自然文, req 構造化要求, nbr 近傍 3 艦) / assistant(設計 JSON のみ)。
  * 1 艦級 1〜2 例（必須装備の候補が 3 つ以上ある艦級は 2 例: 要求の乱数と文型が違う）。
  * 要求は元艦から逆算（shipknow_design_common.make_requirements）。
  * 近傍 k=3（train の実艦のみ。同じ艦級グループを除外。要求に書いてある項目だけで検索）。規則 NB-1:
      - 要求の年 +2 年より後の艦は出さない（未来の艦を参考にしない）。
      - 同国の艦から最大 2 隻（系譜枠: 細分類の不一致と年の重みを弱めた距離で近い順、距離 2.0 以内＝粗い艦種が違う艦は入らない）
        ＋他国の同時代艦（要求年−10 年以降、全体 3 位の距離+0.8 以内）から残り。足りなければ全体の近い順で埋める。
      - 近傍の改装・艦歴の年表は、要求年以前の出来事だけ、1 艦あたり max_hist 件。
  * 対象艦自身の艦名・艦級名・艦歴・改装は入力に入れない。近傍の年表に対象艦の艦名・艦級名が現れる出来事は捨てる。
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import statistics
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402
import shipknow_design_common as C  # noqa: E402
import shipknow_design_validator as V  # noqa: E402
import shipknow_history as H  # noqa: E402
import shipknow_retriever as R  # noqa: E402

DEFAULT_TOKENIZER = Path("/private/tmp/claude-501/-Users-eightman-dev/0ec7c03e-08af-473b-871d-d48b22f6efb4/scratchpad/Qwen3.5-0.8B/tokenizer.json")
V1_2_DIR = C.ROOT / "data" / "navspec" / "shipknow_v1_2_rag"
SEED = 20261007

SYSTEM_D = (
    "Task D (warship design). The input has \"req\" (a design request: country, year, hull type, mission, limits, required equipment) and \"nbr\": "
    "real warship classes similar to the request, each with a short timeline \"h\" of its refits and service events up to the request year. "
    "Reply with ONE design as JSON only, no text: {\"ship_type\": coarse type, \"ship_type_fine\": fine type, \"displacement_normal_t\", \"displacement_full_t\", "
    "\"displacement_standard_t\" (only those that exist), \"length_m\", \"beam_m\", \"draught_m\", \"propulsion\": [..], \"shafts\", \"power_hp\", \"speed_kn\", "
    "\"endurance_nm\", \"complement\", \"armour\": {belt,deck,turret mm}, \"weapons\": [{\"kind\", \"n\": mounts, \"per\": barrels per mount (omit if 1), \"cal\": mm}], "
    "\"sensors\": [..], \"air\": [helicopter|seaplane|aircraft]} (omit unknown keys). "
    "The design must meet every limit and required item in \"req\", stay physically consistent (hull form, power vs speed, crew vs size) and use only technology "
    "that existed by the request year. The nbr are hints, not the answer. " + C.NBR_LEGEND
)

# retrieval weights for requests. Only request fields enter (no hull dimensions / power). Hand-tuned on valid with a few variants of the T3 params of the
# v1 retriever by the top-1 / median-vote baseline score (numeric median error, weapons F1, fine type accuracy); the country term is 0 because the same-country
# slot of rule NB-1 already prefers the country lineage.
REQ_PARAMS: dict[str, Any] = {
    "w": {"logd": 4.0, "logL": 0.0, "logB": 0.0, "logT": 0.0, "speed": 0.5, "logP": 0.0, "year": 0.7, "logC": 0.3, "logE": 0.2, "shafts": 0.0},
    "pen_missing": 1.0, "country": 0.0, "type": 3.0, "fine": 1.2, "weapons": 0.5, "cal": 0.2, "propulsion": 0.0,
}

SLOT_SLACK = 0.8
LINEAGE_FINE = 0.2
LINEAGE_YEAR_W = 0.3
LINEAGE_CAP = 2.0
_TOKEN_TO_KINDS = {"torpedo_tubes": ["torpedo_tube"], "asw_weapon": ["depth_charge"], "helicopter": ["aircraft_helicopter"], "aircraft": ["aircraft_helicopter"],
                   "ssm": ["ssm"], "sam": ["sam"], "ciws": ["ciws"], "rocket_launcher": ["rocket_launcher"], "ballistic_missile": ["ballistic_missile"],
                   "mines": ["mine"], "minesweeping": ["minesweeping_gear"]}


def sub_rng(seed: int, *parts: str) -> random.Random:
    h = hashlib.sha256("|".join([str(seed), *parts]).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


# --------------------------------------------------------------------------------------------------
# names (leak guard)
# --------------------------------------------------------------------------------------------------
def name_terms(class_name: str, ship_names: list[str]) -> list[str]:
    terms: set[str] = set()
    cn = re.sub(r"\(.*?\)", "", re.sub(r"\s*\bClass\b.*$", "", class_name or "", flags=re.I)).strip()
    if len(cn) >= 4:
        terms.add(cn)
    for nm in ship_names:
        for part in re.split(r"[<>()\[\]/,;]", nm):
            part = re.sub(r"^\s*ex[-\s]?", "", part.strip(), flags=re.I).strip()
            if len(part) >= 4 and not part.isdigit():
                terms.add(part)
    return sorted(terms)


def term_regex(terms: list[str]) -> re.Pattern | None:
    if not terms:
        return None
    return re.compile("|".join(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])" for t in terms), re.I)


# --------------------------------------------------------------------------------------------------
# retrieval (rule NB-1)
# --------------------------------------------------------------------------------------------------
def req_view(req: dict[str, Any]) -> dict[str, Any]:
    view: dict[str, Any] = {"year": req["year"], "country": req["country"].replace("-", " "), "ship_type": req["ship_type"]}
    if req.get("ship_type_fine"):
        view["ship_type_fine"] = req["ship_type_fine"]
    basis = req.get("displacement_basis", "normal")
    view[f"displacement_{basis}_t"] = req["max_displacement_t"] / 1.125
    if req.get("min_speed_kn") is not None:
        view["speed_kn"] = req["min_speed_kn"] + 1.0
    if req.get("max_complement"):
        view["complement"] = req["max_complement"] / 1.15
    if req.get("min_endurance_nm"):
        view["endurance_nm"] = req["min_endurance_nm"] * 1.07
    ws: list[dict[str, Any]] = []
    for tok in req.get("required", []):
        m = re.match(r"^main_gun>=(\d+(?:\.\d+)?)mm$", tok)
        if m:
            ws.append({"kind": "gun", "cal": float(m.group(1))})
        for k in _TOKEN_TO_KINDS.get(tok, []):
            ws.append({"kind": k})
    if ws:
        view["weapons"] = ws
    return view


def lineage_params(prm: dict[str, Any]) -> dict[str, Any]:
    """同国の系譜枠の距離: 細分類の不一致を弱く（0.2）、年の重みも弱く（0.3）して、同国の過去艦を広く拾う。粗い艦種の不一致（3.0）は残す。"""
    p = json.loads(json.dumps(prm))
    p["fine"] = LINEAGE_FINE
    p["w"]["year"] = min(p["w"].get("year", 0.0), LINEAGE_YEAR_W)
    return p


def select_neighbors(pool: R.Pool, req: dict[str, Any], k: int = 3, exclude_group: str | None = None, exclude_id: str | None = None,
                     params: dict[str, Any] | None = None) -> list[tuple[int, float]]:
    """規則 NB-1（モジュール docstring 参照）。戻り値は (pool 内の index, 距離)。同国の系譜枠の距離は lineage_params、他国枠・補充は通常の距離。"""
    prm = params or REQ_PARAMS
    view = req_view(req)
    d = pool.distances(view, "T3", prm)
    d_lin = pool.distances(view, "T3", lineage_params(prm))
    years = np.array([r["year"] for r in pool.recs], dtype=float)
    ok = years <= req["year"] + 2
    if exclude_group is not None:
        ok &= pool.groups != exclude_group
    if exclude_id is not None:
        if not hasattr(pool, "_ids_arr"):
            pool._ids_arr = np.array(pool.ids)
        ok &= pool._ids_arr != exclude_id
    nation = req["country"].replace("-", " ")
    same = pool.country == nation
    dm = np.where(ok, d, np.inf)
    order = np.argsort(dm, kind="stable")
    cutoff = (dm[order[min(k, len(order)) - 1]] if len(order) else np.inf) + SLOT_SLACK  # the foreign slot only takes candidates not far worse than the overall k-th best
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

    take(order_lin, dl, same, 2, LINEAGE_CAP)
    take(order, dm, ~same & (years >= req["year"] - 10), k - len(chosen), cutoff)
    take(order, dm, np.ones(len(years), dtype=bool), k - len(chosen), None)
    chosen.sort(key=lambda j: d[j])  # shown nearest first (distance of the request-based ranking), like the v1 neighbours
    return [(j, float(d[j])) for j in chosen]


def drop_generic(terms: list[str], req: dict[str, Any]) -> list[str]:
    """艦名・艦級名のうち、要求に正当に出てくる語（国名・艦種名。例: 艦級名 "Australia"）は漏れ検査の対象から外す。"""
    generic = {req["country"].replace("-", " ").lower(), req["country"].lower(), req["ship_type"].replace("_", " ").lower()}
    if req.get("ship_type_fine"):
        generic |= {req["ship_type_fine"].lower(), req["ship_type_fine"].replace("-", " ").lower()}
    return [t for t in terms if t.lower() not in generic]


def nbr_block(pool: R.Pool, picks: list[tuple[int, float]], events_by_id: dict[str, list[dict[str, Any]]], req_year: int, forbid: re.Pattern | None,
              forbidden_terms: list[str], max_hist: int, max_chars: int, own: set[str] | None = None) -> tuple[list[dict[str, Any]], int]:
    out = []
    n_hist = 0
    for j, _d in picks:
        rec = pool.recs[j]
        hist = H.timeline(events_by_id.get(rec["id"], []), max_n=max_hist, max_year=req_year, max_chars=max_chars, forbidden=forbidden_terms, exclude_prefixes=own)
        n_hist += len(hist)
        out.append(C.nbr_payload(rec, hist))
    return out, n_hist


# --------------------------------------------------------------------------------------------------
# examples
# --------------------------------------------------------------------------------------------------
def n_examples(design: dict[str, Any]) -> int:
    return 2 if len(C.candidate_tokens(design)) >= 3 else 1


def build_user(req: dict[str, Any], text: str, nbr: list[dict[str, Any]]) -> str:
    return C.jdump({"request": text, "req": req, "nbr": nbr})


def build_examples(rec: dict[str, Any], pool: R.Pool, group_of: dict[str, str], events_by_id: dict[str, list[dict[str, Any]]], terms: list[str],
                   k: int, max_hist: int, max_chars: int) -> list[dict[str, Any]]:
    design = C.design_from_record(rec)
    out = []
    rx = term_regex(terms)
    for i in range(n_examples(design)):
        rng = sub_rng(SEED, rec["id"], str(i))
        req = C.make_requirements(design, rec["country"], C.request_year(rec), rng)
        variant = rng.randrange(3)
        text = C.request_text(req, variant)
        picks = select_neighbors(pool, req, k=k, exclude_group=group_of[rec["id"]], exclude_id=rec["id"])
        nbr, n_hist = nbr_block(pool, picks, events_by_id, req["year"], rx, drop_generic(terms, req), max_hist, max_chars, H.own_prefixes(events_by_id.get(rec["id"], [])))
        user = build_user(req, text, nbr)
        out.append({
            "id": f"{rec['id']}:D:{i}", "task": "D", "row": rec["id"], "group": group_of[rec["id"]], "variant": f"v{variant}",
            "messages": [{"role": "system", "content": SYSTEM_D}, {"role": "user", "content": user}, {"role": "assistant", "content": C.jdump(design)}],
            "meta": {"request_ja": C.request_text_ja(req), "nbr": [pool.ids[j] for j, _ in picks], "nbr_d": [round(d, 4) for _, d in picks], "n_hist": n_hist,
                     "year_source": rec.get("year_source")},
        })
    return out


def pct(xs: list[int], p: float) -> int:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0


# --------------------------------------------------------------------------------------------------
# scenarios (no gold)
# --------------------------------------------------------------------------------------------------
def _sc(i: str, country: str, year: int, ship_type: str, fine: str | None, mission: list[str], disp: float, basis: str, vmin: float | None,
        required: list[str], premise: str | None = None, **extra: Any) -> dict[str, Any]:
    req: dict[str, Any] = {"country": country, "year": year, "ship_type": ship_type}
    if fine:
        req["ship_type_fine"] = fine
    req["mission"] = mission
    req["max_displacement_t"] = disp
    req["displacement_basis"] = basis
    if vmin is not None:
        req["min_speed_kn"] = vmin
    req["required"] = required
    for kk, vv in extra.items():
        req[kk] = vv
    return {"id": i, "premise": premise, "req": req}


SCENARIOS: list[dict[str, Any]] = [
    _sc("s01", "Japan", 1950, "patrol", "patrol-boat", ["patrol"], 150, "normal", 22, ["main_gun>=20mm", "radar"],
        "Assume Japan won the war and kept its navy; this is a postwar coastal patrol vessel.", max_complement=25),
    _sc("s02", "United-Kingdom", 1938, "light_cruiser", None, ["surface_strike"], 9000, "standard", 32, ["main_gun>=152mm", "aircraft", "torpedo_tubes"]),
    _sc("s03", "Italy", 1960, "destroyer", "missile-destroyer", ["aaw", "asw"], 4200, "full", 32, ["sam", "helicopter", "sonar"]),
    _sc("s04", "United-States", 1925, "submarine", None, ["submarine_ops"], 1800, "normal", 17, ["torpedo_tubes", "main_gun>=102mm"]),
    _sc("s05", "Germany", 1942, "fast_attack_craft", "motor-torpedo-boat", ["surface_strike"], 110, "full", 38, ["torpedo_tubes", "main_gun>=20mm"]),
    _sc("s06", "Japan", 1944, "escort", None, ["escort", "asw"], 1100, "normal", 16, ["asw_weapon", "sonar", "main_gun>=120mm"]),
    _sc("s07", "Russian", 1955, "frigate", None, ["escort", "asw"], 1400, "normal", 28, ["asw_weapon", "torpedo_tubes", "main_gun>=76mm"]),
    _sc("s08", "France", 1936, "destroyer", None, ["surface_strike"], 2700, "standard", 36, ["main_gun>=127mm", "torpedo_tubes"]),
    _sc("s09", "United-Kingdom", 1970, "frigate", None, ["escort", "asw"], 3200, "full", 28, ["helicopter", "sam", "sonar"]),
    _sc("s10", "Germany", 1980, "fast_attack_craft", None, ["surface_strike"], 420, "full", 34, ["ssm", "main_gun>=76mm", "radar"]),
    _sc("s11", "United-Kingdom", 1915, "destroyer", None, ["surface_strike"], 1100, "normal", 30, ["main_gun>=102mm", "torpedo_tubes"]),
    _sc("s12", "United-States", 1941, "escort", "destroyer-escort", ["escort", "asw"], 1300, "standard", 24, ["asw_weapon", "sonar", "main_gun>=76mm"]),
    _sc("s13", "Italy", 1930, "heavy_cruiser", None, ["surface_strike"], 10500, "standard", 34, ["main_gun>=203mm", "aircraft"]),
    _sc("s14", "Germany", 1948, "heavy_cruiser", None, ["surface_strike"], 16000, "standard", 32, ["main_gun>=203mm", "air_search_radar", "aircraft"],
        "Assume Germany won in Europe and kept building a surface fleet."),
    _sc("s15", "Japan", 1952, "escort", None, ["escort", "asw"], 1700, "standard", 25, ["asw_weapon", "sonar", "radar"],
        "Assume Japan won the war; the navy builds postwar convoy escorts."),
    _sc("s16", "Japan", 1946, "aircraft_carrier", None, ["carrier_air"], 32000, "standard", 31, ["aircraft", "air_search_radar"],
        "Assume Japan won the war and kept its carrier fleet; this is a postwar fleet carrier."),
    _sc("s17", "Russian", 1962, "destroyer", "missile-destroyer", ["aaw", "surface_strike"], 4500, "full", 34, ["sam", "ssm", "air_search_radar"]),
    _sc("s18", "United-States", 1945, "amphibious", None, ["amphibious"], 4500, "full", 15, ["main_gun>=76mm"]),
    _sc("s19", "France", 1958, "submarine", None, ["submarine_ops"], 1800, "normal", 15, ["torpedo_tubes", "sonar"]),
    _sc("s20", "Japan", 1975, "fast_attack_craft", None, ["surface_strike"], 250, "full", 40, ["ssm", "radar"]),
    _sc("s21", "Russian", 1935, "mine_warfare", "minelayer", ["mine_warfare"], 2500, "normal", 18, ["mines", "main_gun>=100mm"]),
    _sc("s22", "United-Kingdom", 1940, "mine_warfare", "minesweeper", ["mine_warfare"], 800, "normal", 16, ["minesweeping", "main_gun>=76mm"]),
    _sc("s23", "United-States", 1968, "frigate", None, ["escort", "asw"], 4300, "full", 27, ["helicopter", "sonar", "asw_weapon"]),
    _sc("s24", "Italy", 1985, "aircraft_carrier", None, ["carrier_air"], 12000, "full", 26, ["helicopter", "sam"]),
    _sc("s25", "Japan", 1953, "coast_guard", None, ["patrol"], 900, "normal", 18, ["main_gun>=76mm", "radar"],
        "Assume Japan won the war and operates a large postwar coast guard / maritime patrol service."),
    _sc("s26", "Germany", 1944, "patrol", "submarine-chaser", ["patrol", "asw"], 250, "normal", 20, ["asw_weapon", "main_gun>=37mm"]),
    _sc("s27", "Japan", 1921, "battleship", None, ["surface_strike", "fire_support"], 40000, "standard", 23, ["main_gun>=406mm", "torpedo_tubes"]),
    _sc("s28", "United-Kingdom", 1912, "battlecruiser", None, ["surface_strike"], 27000, "normal", 27, ["main_gun>=340mm"]),
    _sc("s29", "United-Kingdom", 1990, "patrol", "patrol-ship", ["patrol"], 1800, "full", 20, ["helicopter", "main_gun>=30mm"]),
]

NEIGHBOR_RULE = (
    "NB-1: candidates = real classes with year <= request year + 2 (no future designs), not excluded as the same class group. Rank by the v1 retriever distance on the request "
    "(type, fine type, year, displacement estimate = limit / 1.125, speed = minimum + 1, crew, endurance, required equipment kinds and gun caliber). "
    "Take up to 2 classes of the same country (its technology lineage, ranked with a distance that weakens the fine-type and year terms; coarse type must match: distance <= 2.0), "
    "then the nearest classes of other countries designed within 10 years before the request (contemporaries; distance at most 0.8 above the overall 3rd best); "
    "empty slots are filled from the overall ranking. Each neighbour carries up to max_hist events of its refit/service timeline dated up to the request year."
)


def build_scenarios(pool: R.Pool, events_by_id: dict[str, list[dict[str, Any]]], k: int, max_hist: int, max_chars: int) -> list[dict[str, Any]]:
    rows = []
    for sc in SCENARIOS:
        req = sc["req"]
        text = C.request_text(req, variant=int(sc["id"][1:]) % 3, premise=sc["premise"])
        picks = select_neighbors(pool, req, k=k)
        nbr, n_hist = nbr_block(pool, picks, events_by_id, req["year"], None, [], max_hist, max_chars)
        rows.append({"id": sc["id"], "fictional": bool(sc["premise"]), "premise": sc["premise"], "request": text, "request_ja": C.request_text_ja(req, sc["premise"]),
                     "req": req, "messages": [{"role": "system", "content": SYSTEM_D}, {"role": "user", "content": build_user(req, text, nbr)}],
                     "meta": {"nbr": [pool.ids[j] for j, _ in picks], "nbr_years": [pool.recs[j]["year"] for j, _ in picks],
                              "nbr_countries": [pool.recs[j]["country"] for j, _ in picks], "n_hist": n_hist, "rule": "NB-1 (see manifest.neighbor_rule)"}})
    return rows


# --------------------------------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------------------------------
def verify(out_dir: Path, names_by_id: dict[str, list[str]], class_name_by_id: dict[str, str], events_by_id: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    sp = json.loads((C.V0_DIR / "splits.json").read_text(encoding="utf-8"))
    assign, group_of = sp["assignments"], sp["group_of"]
    recs = {r["id"]: r for r in (json.loads(l) for l in (C.V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip())}
    th = V.load_thresholds()
    res: dict[str, Any] = {"examples": 0, "name_leaks": 0, "nbr_not_train": 0, "nbr_same_group": 0, "nbr_self": 0, "nbr_future": 0, "split_mismatch": 0,
                           "gold_req_violations": 0, "own_history_in_input": 0, "bad_json": 0, "bad_hist_year": 0, "generic_word_matches_outside_history": 0}
    leak_examples: list[dict[str, Any]] = []
    for sp_name in ("train", "valid", "test"):
        for line in (out_dir / f"{sp_name}.jsonl").read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            res["examples"] += 1
            row = e["row"]
            if assign[row] != sp_name or e["group"] != group_of[row]:
                res["split_mismatch"] += 1
            try:
                user = json.loads(e["messages"][1]["content"])
                gold = json.loads(e["messages"][2]["content"])
            except Exception:
                res["bad_json"] += 1
                continue
            terms = drop_generic(name_terms(class_name_by_id.get(row, ""), names_by_id.get(row, [])), user["req"])
            rx = term_regex([t for t in terms if not re.fullmatch(r"[0-9\s\-]+", t)])  # a class named "1940" would match every year
            hist_text = " ".join(t_ for nb in user["nbr"] for _y, t_ in nb.get("h", []))
            if rx and rx.search(hist_text):
                res["name_leaks"] += 1
                if len(leak_examples) < 5:
                    leak_examples.append({"id": e["id"], "match": rx.search(hist_text).group(0)})
            # the name also must not be in the non-history parts; matches there are generic words (a class named "Weapon" / "Turbine") and are only counted
            rx_all = term_regex(terms)
            rest = e["messages"][1]["content"]
            if rx_all and rx_all.search(rest) and not (rx and rx.search(hist_text)):
                res["generic_word_matches_outside_history"] += 1
            for nid in e["meta"]["nbr"]:
                if assign[nid] != "train":
                    res["nbr_not_train"] += 1
                if group_of[nid] == group_of[row]:
                    res["nbr_same_group"] += 1
                if nid == row:
                    res["nbr_self"] += 1
                if recs[nid]["year"] > user["req"]["year"] + 2:
                    res["nbr_future"] += 1
            for nb in user["nbr"]:
                for y, _t in nb.get("h", []):
                    if y > user["req"]["year"]:
                        res["bad_hist_year"] += 1
            own = [ev["text"][:50] for ev in events_by_id.get(row, []) if len(ev["text"]) >= 50]
            u_text = e["messages"][1]["content"]
            res["own_history_in_input"] += sum(1 for t in own if t in u_text)
            vr = V.validate(user["req"], gold, th, layers=("req",))
            res["gold_req_violations"] += not vr["ok"]
    res["name_leak_examples"] = leak_examples
    return res


# --------------------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=C.OUT_DIR)
    ap.add_argument("--tokenizer", type=Path, default=DEFAULT_TOKENIZER)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--max-hist", type=int, default=3)
    ap.add_argument("--hist-chars", type=int, default=90)
    ap.add_argument("--tok-per-sec", type=float, default=2850.0)
    ap.add_argument("--limit", type=int, default=None, help="smoke test: first N eligible records")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    conn = H.open_ro()
    texts = H.class_texts(conn)
    ship_names_by_url = H.class_ship_names(conn)
    conn.close()
    recs = [json.loads(l) for l in (C.V0_DIR / "records.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    sp = json.loads((C.V0_DIR / "splits.json").read_text(encoding="utf-8"))
    assign, group_of = sp["assignments"], sp["group_of"]
    names_by_id: dict[str, list[str]] = {}
    class_name_by_id: dict[str, str] = {}
    for r in recs:
        row = texts.get(int(r["id"][1:]))
        if row:
            class_name_by_id[r["id"]] = row["CLASS_NAME"]
            names_by_id[r["id"]] = ship_names_by_url.get(row["source_url"], [])
    elig = [r for r in recs if C.design_eligible(r)]
    if args.limit:
        elig = elig[: args.limit]
    events_by_id = {r["id"]: H.events_for_class(texts[int(r["id"][1:])]) for r in elig if int(r["id"][1:]) in texts}
    if args.verify_only:
        print(json.dumps(verify(args.out, names_by_id, class_name_by_id, events_by_id), ensure_ascii=False, indent=1))
        return
    train = [r for r in elig if assign[r["id"]] == "train"]
    pool = R.Pool(train, group_of)
    tok, tok_note = B.load_tokenizer(args.tokenizer)

    examples: dict[str, list[dict[str, Any]]] = {"train": [], "valid": [], "test": []}
    for r in elig:
        terms = name_terms(class_name_by_id.get(r["id"], ""), names_by_id.get(r["id"], []))
        for ex in build_examples(r, pool, group_of, events_by_id, terms, args.k, args.max_hist, args.hist_chars):
            examples[assign[r["id"]]].append(ex)
    args.out.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {}
    for spn, exs in examples.items():
        exs.sort(key=lambda e: e["id"])
        with (args.out / f"{spn}.jsonl").open("w", encoding="utf-8") as fh:
            for e in exs:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        toks = [B.count_tokens(tok, e) for e in exs]
        tt, at = [a for a, _ in toks], [b for _, b in toks]
        nbr_t = []
        for e in exs[:1500]:
            u = json.loads(e["messages"][1]["content"])
            nbr_t.append(len(tok.encode(C.jdump(u["nbr"])).ids) if tok else len(C.jdump(u["nbr"])) // 3)
        nbr_nohist = []
        for e in exs[:1500]:
            u = json.loads(e["messages"][1]["content"])
            nbr_nohist.append(len(tok.encode(C.jdump([{k: v for k, v in n.items() if k != "h"} for n in u["nbr"]])).ids) if tok else 0)
        stats[spn] = {"examples": len(exs), "classes": len({e["row"] for e in exs}), "tokens_total": sum(tt), "tokens_avg": round(statistics.mean(tt), 1), "tokens_p50": pct(tt, 0.5),
                      "tokens_p95": pct(tt, 0.95), "tokens_max": max(tt), "assistant_tokens_avg": round(statistics.mean(at), 1), "assistant_tokens_max": max(at),
                      "nbr_tokens_avg": round(statistics.mean(nbr_t), 1), "nbr_tokens_without_history_avg": round(statistics.mean(nbr_nohist), 1)}
    # neighbour / request statistics (train)
    tr = examples["train"]
    req_tokens = collections.Counter()
    missions = collections.Counter()
    n_nbr = collections.Counter()
    same_country = 0
    tot_nbr = 0
    hist_nbr = 0
    for e in tr:
        u = json.loads(e["messages"][1]["content"])
        for t in u["req"]["required"]:
            req_tokens[re.sub(r">=\d+(\.\d+)?mm", ">=Nmm", t)] += 1
        missions["+".join(u["req"]["mission"])] += 1
        n_nbr[len(u["nbr"])] += 1
        for nb in u["nbr"]:
            tot_nbr += 1
            same_country += nb["cn"] == u["req"]["country"].replace("-", " ")
            hist_nbr += bool(nb.get("h"))
    # v1_2 comparison (same tokenizer)
    cmp: dict[str, Any] = {}
    if (V1_2_DIR / "manifest.json").exists():
        m = json.loads((V1_2_DIR / "manifest.json").read_text(encoding="utf-8"))
        cmp["v1_2_train_tokens_avg_by_task"] = {t: m["token_stats"]["train"][t]["tokens_avg"] for t in ("T1", "T2", "T3")}
        cmp["v1_2_train_tokens_total"] = sum(m["token_stats"]["train"][t]["tokens_total"] for t in ("T1", "T2", "T3"))
        sample = []
        for line in (V1_2_DIR / "test.jsonl").read_text(encoding="utf-8").splitlines()[:6000:6]:
            e = json.loads(line)
            if e["task"] not in ("T2", "T3"):
                continue
            nb = json.loads(e["messages"][1]["content"])["neighbors"]
            sample.append((e["task"], len(tok.encode(json.dumps(nb, separators=(",", ":"), sort_keys=True, ensure_ascii=False)).ids) if tok else 0))
        for t in ("T2", "T3"):
            xs = [n for tt_, n in sample if tt_ == t]
            cmp[f"v1_2_neighbors_json_tokens_avg_{t}"] = round(statistics.mean(xs), 1) if xs else None
    train_tokens = stats["train"]["tokens_total"]
    scen = build_scenarios(R.Pool([r for r in elig], group_of), events_by_id, args.k, args.max_hist, args.hist_chars)
    with (args.out / "scenarios.jsonl").open("w", encoding="utf-8") as fh:
        for s in scen:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    manifest = {
        "name": args.out.name, "seed": SEED, "source": "shipknow_v0_2 records + splits (class-group split reused as is) + nsdb/NSDB.db (read-only; refit / service timelines, ship names for the leak guard)",
        "files": {"train": "train.jsonl", "valid": "valid.jsonl", "test": "test.jsonl", "scenarios": "scenarios.jsonl", "validator_thresholds": "validator_thresholds.json"},
        "eligibility": {"excluded_types": sorted(C.EXCLUDED_TYPES), "min_year": C.MIN_YEAR, "max_gap_year_in_navy_minus_year": C.MAX_GAP_NAVY,
                        "requires": "armament parsed, speed, length, a displacement, fine type", "eligible_records": len(elig), "of_records": len(recs)},
        "examples": {s: stats[s]["examples"] for s in stats}, "classes": {s: stats[s]["classes"] for s in stats},
        "token_stats": stats, "tokenizer": tok_note,
        "train_time_estimate": {"train_tokens_per_epoch": train_tokens, "assumed_tok_per_sec": args.tok_per_sec,
                                "epoch_hours": round(train_tokens / args.tok_per_sec / 3600, 2), "note": "RTX 3060, same setting as shipknow v1 (2,850 tok/s, bs2 x GA8)"},
        "v1_2_comparison": cmp,
        "retrieval": {"k": args.k, "rule": NEIGHBOR_RULE, "params": REQ_PARAMS, "max_hist_per_neighbor": args.max_hist, "hist_chars": args.hist_chars,
                      "same_country_share": round(same_country / max(1, tot_nbr), 3), "neighbors_with_history_share": round(hist_nbr / max(1, tot_nbr), 3),
                      "neighbors_per_example": dict(n_nbr)},
        "neighbor_rule": NEIGHBOR_RULE,
        "required_token_counts_train": dict(req_tokens.most_common()), "mission_counts_train": dict(missions.most_common(20)),
        "design_keys": C.DESIGN_KEY_ORDER, "nbr_key_legend": C.NBR_LEGEND, "system_prompt": SYSTEM_D,
        "never_in_input": ["target ship / class names", "target Naval_service / Project_history / Modernizations", "source_url", "year_in_navy"],
        "scenarios": len(scen),
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"examples": manifest["examples"], "classes": manifest["classes"], "train_tokens": train_tokens, "epoch_hours": manifest["train_time_estimate"]["epoch_hours"]}, ensure_ascii=False))
    for spn in ("train", "valid", "test"):
        print(spn, {k: stats[spn][k] for k in ("examples", "tokens_avg", "tokens_p95", "tokens_max", "assistant_tokens_avg", "nbr_tokens_avg", "nbr_tokens_without_history_avg")})
    print(json.dumps(cmp, ensure_ascii=False))
    print("retrieval", json.dumps(manifest["retrieval"]["same_country_share"]), manifest["retrieval"]["neighbors_with_history_share"], dict(n_nbr))


if __name__ == "__main__":
    main()
