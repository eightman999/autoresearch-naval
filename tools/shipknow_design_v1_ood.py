"""design-v1 の ood エピソード（架空国家・技術前倒し・戦争結果の変更・異なるドクトリン・特定技術だけの早期実用化）。

原則: **前提が変えるものだけを変える**。
  * fictional_nation: 国名だけが架空（技術水準・年代の制約は史実のまま。設計は「似ている」と宣言した実在国の実艦）。
  * tech_early: 名指しした技術（例: レーダー）だけを前倒しする。req.premise = {type: tech_early, tech, available_from}。元艦は要求年と同じ年の実艦（その技術をまだ持たない）で、
    正解 = その実艦の設計 + 前提の技術だけ（add_tech: センサーは sensors に追加、兵装は小さな 1 項目、推進は機関の差し替え。重量・排水量・寸法・乗員は足した分だけ再計算）。
    後年の艦から推進・魚雷口径・他の装備を持ち込まない。validator の年代検査（era_early）はその技術の能力語彙（TECH_WAIVERS）だけを免除し、他の技術が年代外なら違反のまま
    （魚雷口径の初出年も check_design が見る）。前提の外の技術を要求に足した例（tech_contrast）は relaxed になる。
  * war_outcome: 国が戦後も海軍を持ち続ける前提（技術は史実のまま。設計は同年代の実艦）。
  * doctrine: 兵装の重点（魚雷・砲・ミサイル・航空・対潜）だけを変える。重点は構造化要求の制約（例: 魚雷発射管数）として明示し、設計はそれを満たす実艦。
  test 専用: 架空国家名（train / valid と互いに素）、ドクトリン air_centric と asw_centric。
"""

from __future__ import annotations

import random
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

SUBTYPES = [("fictional_nation", 36), ("tech_early", 30), ("war_outcome", 12), ("doctrine", 15), ("tech_contrast", 7)]
TECH_CAP = {"radar": "radar_any", "sonar": "sonar", "helicopter": "air:helicopter", "ssm": "ssm", "sam": "sam", "ciws": "ciws", "esm_ecm": "esm_ecm", "gas_turbine": "prop:gas_turbine",
            "nuclear": "prop:nuclear"}
TECH_PHRASE = {"radar": "shipboard radar", "sonar": "active sonar", "helicopter": "shipboard helicopters", "ssm": "anti-ship missiles", "sam": "surface-to-air missiles",
               "ciws": "close-in weapon systems", "esm_ecm": "ESM/ECM equipment", "gas_turbine": "marine gas turbines", "nuclear": "naval nuclear propulsion"}
TECH_RADAR_TOKENS = ["air_search_radar", "fire_control_radar", "surface_radar", "radar"]
WAR_NATIONS = ["Japan", "Germany", "Italy"]
WAR_TEXT = {"Japan": ["Assume Japan won the war and kept its navy.", "Assume Japan ended the war on favourable terms and kept building a modern fleet."],
            "Germany": ["Assume Germany won in Europe and kept building a surface fleet.", "Assume Germany ended the war with its navy intact."],
            "Italy": ["Assume Italy ended the war on the winning side and expanded its navy.", "Assume Italy kept its fleet and its shipyards after the war."]}
DOCTRINES = {"torpedo_centric": "a torpedo-centred", "gun_centric": "a gun-centred", "missile_centric": "a missile-centred", "air_centric": "an air-centred", "asw_centric": "an anti-submarine-centred"}
DOCTRINES_TEST_ONLY = {"air_centric", "asw_centric"}

_TECH_ANCHORS: dict[tuple[str, str], list[dict[str, Any]]] = {}
TECH_WEIGHT = {"radar": 2, "sonar": 2, "helicopter": 2, "ssm": 2, "sam": 2, "gas_turbine": 2, "nuclear": 2, "ciws": 2, "esm_ecm": 2}
# 前提の技術を足せる艦の最小排水量（t）。足せない艦は元艦にしない
TECH_MIN_DISP = {"radar": 0, "sonar": 0, "esm_ecm": 0, "helicopter": 1500, "ssm": 100, "sam": 600, "ciws": 800, "gas_turbine": 0, "nuclear": 2000}
TECH_BAD_TYPES = {"helicopter": {"submarine", "battleship", "battlecruiser", "torpedo_boat", "ironclad", "coastal_defense_monitor", "armored_protected_cruiser"},
                  "sam": {"submarine"}, "ssm": {"submarine"}, "ciws": {"submarine"}}
RADAR_KINDS = ["radar", "air_search_radar", "fire_control_radar", "surface_radar"]
MAX_PREMISE_SHIFT = 25  # 前提の技術の前倒しは、データ上の初出年の 6〜25 年前（それ以上は現実離れ）
SOURCE_YEAR_TOL = 3  # 元艦の年は要求年そのもの（差 0）。仕様の ±3 年以内


def tech_anchors(D: DD.Data, split: str, tech: str) -> list[dict[str, Any]]:
    """前提の技術がまだ無い年（初出年の 6 年以上前）の実艦で、その技術を持たず、足せる大きさのもの。要求年 = 元艦の年。"""
    key = (split, tech)
    if key not in _TECH_ANCHORS:
        cap = TECH_CAP[tech]
        out = []
        for r in D.by_split[split]:
            d = D.designs[r["id"]]
            y = C.request_year(r)
            gap = K.cap_early_by(cap, y)
            if cap in C.caps_of_design(d) or not (6 <= gap <= MAX_PREMISE_SHIFT) or y < 1875:
                continue
            if tech == "helicopter" and ("aircraft" in C.caps_of_design(d) or d.get("air")):
                continue  # a ship that already carries (sea)planes: the helicopter would replace them rather than be added
            if tech == "radar" and "radar_any" in C.caps_of_design(d):
                continue
            if (C.ref_displacement(d)[0] or 0) < TECH_MIN_DISP[tech] or d["ship_type"] in TECH_BAD_TYPES.get(tech, set()):
                continue
            out.append(r)
        _TECH_ANCHORS[key] = out
    return _TECH_ANCHORS[key]


def add_tech(d: dict[str, Any], tech: str, year: int, rng: random.Random) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """元艦の設計に前提の技術だけを最小限足す（センサーは sensors に追加、兵装は小さな 1 項目、推進は機関の差し替え）。排水量・乗員・寸法は足した分だけ再計算。
    戻り値 (新しい設計, 要求に足す制約 {"required": tok} または {"required_propulsion": p})。"""
    import shipknow_design_v1_gen_basic as G  # noqa: E402

    d2 = O.clone(d)
    D = O.ref_disp(d)
    sens = set(d2.get("sensors") or [])
    if tech in ("radar", "sonar", "esm_ecm"):
        kind = rng.choice(RADAR_KINDS) if tech == "radar" else tech
        sens.add(kind)
        d2["sensors"] = sorted(sens)
        out = O.scale_weight(d2, max(2.0, 0.004 * D), None, year, crew_extra=max(1, round(0.004 * (d.get("complement") or 20))))
        return out, {"required": kind}
    if tech == "helicopter":
        dW = O.set_aircraft(d2, 1, "helicopter")
        return O.scale_weight(d2, dW, None, year, crew_extra=8), {"required": "helicopter"}
    if tech in ("ssm", "sam", "ciws"):
        dW = O.add_launcher(d2, tech, rng.randrange(2), radar=False)
        return O.scale_weight(d2, dW, None, year, crew_extra=max(3, round(0.01 * D))), {"required": tech}
    if tech in ("gas_turbine", "nuclear"):
        k, ef, cf = G.PROP_EFFECT[tech]
        O.set_propulsion(d2, tech)
        out = O.rederive(d2, k, None, year)
        if ef is None:
            out["endurance_nm"] = 60000 if K._isnum(d.get("endurance_nm")) else None
        elif K._isnum(out.get("endurance_nm")):
            out["endurance_nm"] = O.rnd("endurance_nm", out["endurance_nm"] * ef)
        if K._isnum(out.get("complement")):
            out["complement"] = O.rnd("complement", out["complement"] * cf)
        return O.normalize({a: b for a, b in out.items() if b is not None}), {"required_propulsion": tech}
    return None


def _tech_case(D: DD.Data, split: str, rng: random.Random, tech: str | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], str, list[str]] | None:
    """前提の技術だけを足した設計の例。元艦は要求年（= 元艦の年）の、その技術をまだ持たない実艦。その年代の実艦 + 前提の技術だけ（推進・魚雷口径などを後年の艦から持ち込まない）。"""
    tech = tech or rng.choice(sorted(TECH_CAP))
    anchors = tech_anchors(D, split, tech)
    if not anchors:
        return None
    rec = rng.choice(anchors)
    design = D.design(rec)
    year = C.request_year(rec)
    got = add_tech(design, tech, year, rng)
    if got is None:
        return None
    new_design, add = got
    req = K.make_req_v1(new_design, rec["country"], year, rng)
    if "required" in add and add["required"] not in req.get("required", []):
        req["required"] = list(req.get("required", [])) + [add["required"]]
    if "required_propulsion" in add:
        req["required_propulsion"] = [add["required_propulsion"]]
    req = K.order_req(req)
    premise = {"type": "tech_early", "tech": tech, "available_from": year - rng.randint(0, 3)}
    waivers = K.waivers_for_premise(premise)
    if not K.satisfies_all(req, new_design) or not K.check_design(req, new_design, waivers)["ok"]:
        return None
    if K.check_design(req, new_design)["ok"]:  # the premise must matter
        return None
    return rec, new_design, req, premise, TECH_PHRASE[tech], waivers


def _ood_tech(D: DD.Data, split: str, rng: random.Random, tech: str | None = None) -> dict[str, Any] | None:
    got = _tech_case(D, split, rng, tech)
    if got is None:
        return None
    rec, design, req, premise, phrase, waivers = got
    forms = [f"Assume {phrase} entered service in {premise['available_from']}; nothing else changes.",
             f"In this world {phrase} became practical already in {premise['available_from']}. All other technology follows the real timeline."]
    return _finish(D, split, rng, "tech_early", req, premise, rng.choice(forms), "feasible", design, rec, waivers, origin="tech_added", extra={"tech": premise["tech"]})


def _ood_contrast(D: DD.Data, split: str, rng: random.Random, tech: str | None = None) -> dict[str, Any] | None:
    got = _tech_case(D, split, rng, tech)
    if got is None:
        return None
    rec, design, req, premise, phrase, waivers = got
    year = req["year"]
    cands = [c for c in I._era_candidates(req, design, year) if not I._cid_satisfied(c, design)]
    waive = set(waivers)
    cands = [c for c in cands if c.startswith("required:") and K.TOKEN_CAP.get(c.split(":", 1)[1], "") not in waive and c.split(":", 1)[1] != "radar"]
    if not cands:
        return None
    cid = rng.choice(cands)
    req2 = I._inject_era(req, [cid])
    conf = I.era_conflict(req2, cid, year, design)
    status = I.decide_status([conf], len(K.constraint_ids(req2)))
    if status != "relaxed":
        return None
    text = f"Assume {phrase} entered service in {premise['available_from']}; nothing else changes."
    return _finish(D, split, rng, "tech_contrast", req2, premise, text, "relaxed", design, rec, waivers, [conf], origin="tech_added", extra={"tech": premise["tech"], "contrast": cid})


def _adj(country: str) -> str:
    n = country.replace("-", " ")
    a = C._NATION_ADJ.get(n)
    return f"the {a} navy" if a else f"the navy of {n}"


def _finish(D: DD.Data, split: str, rng: random.Random, subtype: str, req: dict[str, Any], premise: dict[str, Any], premise_text: str, status: str, design: dict[str, Any] | None,
            rec: dict[str, Any], waivers: list[str], conflicts: list[Any] | None = None, extra: dict[str, Any] | None = None, origin: str = "real") -> dict[str, Any] | None:
    req = K.order_req({**req, "premise": premise})
    if split != "test" and K.is_heldout_combo(req):
        return None
    text = K.request_text_v1(req, rng.randrange(6), premise_text=premise_text)
    nbr = D.neighbors(req, [rec["id"]])
    ep = G.new_episode("ood", subtype, split)
    ep["messages"].append(G.msg("user", G.user_turn(text, req, nbr)))
    confs, rels = I.conflict_outputs(conflicts or [], status)
    G.add_asst(ep, G.asst_turn(status, design, confs, rels), status=status, origin=origin if design else "none", source=rec["id"] if design else None, role="answer", train=True,
               waivers=waivers, conflict_info=[c.info() for c in conflicts or []])
    G.add_source(ep, rec["id"], D)
    ep["meta"]["premise_type"] = premise["type"]
    ep["meta"]["premise"] = premise
    ep["meta"]["waivers"] = waivers
    if extra:
        ep["meta"].update(extra)
    return G.fin_meta(ep, D, req)


def _ood_fictional(D: DD.Data, split: str, rng: random.Random) -> dict[str, Any] | None:
    rec = D.pick(split, rng, "any")
    if rec is None:
        return None
    design = D.design(rec)
    year = C.request_year(rec)
    name = rng.choice(DD.FICTIONAL[split])
    req = K.make_req_v1(design, name, year, rng)
    if not K.check_design(req, design)["ok"]:
        return None
    like = rec["country"]
    forms = [f"{name} is a fictional naval power; its shipbuilding and doctrine resemble those of {_adj(like)}.",
             f"Assume {name}, an invented maritime state, whose navy follows {_adj(like)}'s practice and industrial level."]
    return _finish(D, split, rng, "fictional_nation", req, {"type": "fictional_nation", "like": like}, rng.choice(forms), "feasible", design, rec, [], extra={"fictional_name": name})


def _ood_war(D: DD.Data, split: str, rng: random.Random) -> dict[str, Any] | None:
    rec = D.pick(split, rng, "postwar_major")
    if rec is None:
        return None
    design = D.design(rec)
    year = C.request_year(rec)
    nation = rng.choice(WAR_NATIONS)
    req = K.make_req_v1(design, nation, year, rng)
    if not K.check_design(req, design)["ok"]:
        return None
    like = rec["country"] if rec["country"] != nation else None
    txt = rng.choice(WAR_TEXT[nation])
    if like:
        txt += f" Its designers follow {_adj(like)}'s postwar practice."
    return _finish(D, split, rng, "war_outcome", req, {"type": "war_outcome", "nation": nation, "like": like}, txt, "feasible", design, rec, [])


def _ood_doctrine(D: DD.Data, split: str, rng: random.Random) -> dict[str, Any] | None:
    pool = [d for d in DOCTRINES if split == "test" or d not in DOCTRINES_TEST_ONLY]
    doc = rng.choices(pool, weights=[3 if d in DOCTRINES_TEST_ONLY else 1 for d in pool])[0]
    pred = {"torpedo_centric": "tubes", "gun_centric": "barrels", "missile_centric": "modern", "air_centric": "aircraft", "asw_centric": "any"}[doc]
    rec = D.pick(split, rng, pred)
    if rec is None:
        return None
    design = D.design(rec)
    year = C.request_year(rec)
    force = {"torpedo_centric": {"tubes"}, "gun_centric": {"barrels"}, "air_centric": {"aircraft"}}.get(doc)
    req = K.make_req_v1(design, rec["country"], year, rng, force=force)
    req = {k: v for k, v in req.items() if k not in ("min_gun_barrels", "min_gun_barrels_cal_mm", "min_torpedo_tubes", "min_aircraft")} if doc in ("missile_centric", "asw_centric") else req
    toks = list(req.get("required", []))
    need: str
    if doc == "torpedo_centric":
        if "min_torpedo_tubes" not in req:
            return None
        if "torpedo_tubes" not in toks:
            toks.append("torpedo_tubes")
        need = "torpedo tubes first"
    elif doc == "gun_centric":
        if "min_gun_barrels" not in req or X.gun_barrels(design) < 4:
            return None
        need = "heavy gun batteries first"
    elif doc == "missile_centric":
        have = [t for t in ("ssm", "sam") if K.satisfies_all({"required": [t]}, design, True)]
        if not have or year < 1958:
            return None
        for t in have:
            if t not in toks:
                toks.append(t)
        need = "missiles first"
    elif doc == "air_centric":
        if "min_aircraft" not in req or req["min_aircraft"] < 2:
            return None
        need = "embarked aircraft first"
    else:
        have = [t for t in ("asw_weapon", "sonar") if K.satisfies_all({"required": [t]}, design, True)]
        if len(have) < 2:
            return None
        for t in have:
            if t not in toks:
                toks.append(t)
        need = "anti-submarine equipment first"
    req["required"] = toks
    req = K.order_req(req)
    if not K.check_design(req, design)["ok"]:
        return None
    txt = f"Assume the navy follows {DOCTRINES[doc]} doctrine: it spends its displacement on {need}; nothing else about the technology changes."
    return _finish(D, split, rng, "doctrine", req, {"type": "doctrine", "doctrine": doc}, txt, "feasible", design, rec, [],
                   extra={"doctrine": doc, "holdout_doctrine": doc in DOCTRINES_TEST_ONLY})


def gen_ood(D: DD.Data, split: str, idx: int) -> dict[str, Any] | None:
    rng = K.sub_rng(G.SEED, "ood", split, idx)
    names = [n for n, _ in SUBTYPES]
    sub = rng.choices(names, weights=[w for _, w in SUBTYPES])[0]
    fn = {"fictional_nation": _ood_fictional, "tech_early": _ood_tech, "war_outcome": _ood_war, "doctrine": _ood_doctrine, "tech_contrast": _ood_contrast}[sub]
    tech = rng.choices(sorted(TECH_WEIGHT), weights=[TECH_WEIGHT[t] for t in sorted(TECH_WEIGHT)])[0] if sub in ("tech_early", "tech_contrast") else None
    for _ in range(40 if tech else 12):
        ep = fn(D, split, rng, tech) if tech else fn(D, split, rng)
        if ep is not None:
            if sub == "doctrine" and ep["meta"].get("holdout_doctrine"):
                ep["meta"]["holdout"].append("doctrine")
            if sub == "fictional_nation" and split == "test":
                ep["meta"]["holdout"].append("fictional_name")
            if G.split_check(D, ep):
                return ep
    G.fail("ood:" + sub)
    return None
