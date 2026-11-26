"""shipknow design-v1 の生成パイプライン（shipknow_build_design_v1）と検査器（shipknow_check_design_v1）のテスト。

小さなパイロット（160 エピソード）を tmp に作り、(1) 全カテゴリ・全 split で生成される、(2) 同じ seed で完全に同じ、(3) 検査器が全件を通す、
(4) 検査器が壊した例を検出する（status の矛盾、自然文の混入、要求未充足、repair の違反の再現、context のコピー、split 越境、重複 ...）ことを確認する。
実艦レコード・validator 閾値・NSDB（mode=ro）が要る。無ければ skip。
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path

import pytest

from tools import shipknow_build_design_v1 as BV
from tools import shipknow_check_design_v1 as CK
from tools import shipknow_design_common as C
from tools import shipknow_design_v1_core as K
from tools import shipknow_design_v1_data as DD
from tools import shipknow_design_v1_gen_basic as G
from tools import shipknow_history as H

ROOT = Path(__file__).resolve().parent.parent
needs_all = pytest.mark.skipif(not (C.V0_DIR / "records.jsonl").exists() or not (C.OUT_DIR / "validator_thresholds.json").exists() or not H.DEFAULT_DB.exists(),
                               reason="v0_2 records / design_v0 thresholds / NSDB not available")

N_TOTAL = 160


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("v1pilot")
    res = BV.generate(N_TOTAL, 1, 1.5, log=lambda *a, **k: None)
    args = argparse.Namespace(n_total=N_TOTAL, procs=1, oversample=1.5)
    BV.write_outputs(out, res, args)
    X_ = CK.Ctx(out)
    rows = CK.load_rows(out)
    return {"dir": out, "res": res, "ctx": X_, "rows": rows}


def all_eps(built):
    return [r for s in K.SPLITS for r in built["rows"][s]]


def find(built, pred):
    for r in all_eps(built):
        if pred(r):
            return copy.deepcopy(r)
    raise AssertionError("no matching episode in the test pilot")


def set_asst(ep, idx, fn):
    obj = json.loads(ep["messages"][idx]["content"])
    fn(obj)
    ep["messages"][idx]["content"] = K.jdump(obj)


def codes(ep, built):
    return {c for c, _ in CK.check_episode(ep, built["ctx"])[0]}


# --------------------------------------------------------------------------------------------------
@needs_all
def test_plan_follows_the_target_ratio():
    q = BV.plan(100000)
    tot = {c: sum(q[(c, s)] for s in K.SPLITS) for c in K.CATEGORIES}
    assert tot == {"single": 30000, "modify": 20000, "repair": 25000, "infeasible": 10000, "ood": 5000, "context": 10000}
    assert q[("repair", "valid")] == 2500 and q[("repair", "test")] == 2500 and q[("repair", "train")] == 20000
    assert sum(BV.plan(2000).values()) == 2000


@needs_all
def test_pilot_has_every_category_and_split_with_unique_ids(built):
    counts = built["res"]["quotas"]
    for (c, s), q in counts.items():
        assert len([r for r in built["rows"][s] if r["category"] == c]) == q
    ids = [r["id"] for r in all_eps(built)]
    assert len(ids) == len(set(ids)) == N_TOTAL
    assert all(r["id"].startswith("dv1_") for r in all_eps(built))
    m = json.loads((built["dir"] / "manifest.json").read_text(encoding="utf-8"))
    assert m["n_total"] == N_TOTAL and m["system_prompt"] == K.SYSTEM_V1 and "status_criteria" in m and m["relax_gap_max"]["speed"] == 0.3


@needs_all
def test_messages_are_chat_template_ready(built):
    for r in all_eps(built):
        msgs = r["messages"]
        assert msgs[0]["role"] == "system" and msgs[-1]["role"] == "assistant"
        assert all(isinstance(m["content"], str) and m["content"] for m in msgs)
        assert [m["role"] for m in msgs[1:]] == [("user" if i % 2 == 0 else "assistant") for i in range(len(msgs) - 1)]
        for m in msgs[1:]:
            json.loads(m["content"])  # every user / assistant content is a JSON string
        assert r["metadata"]["train_turns"] and all(msgs[i]["role"] == "assistant" for i in r["metadata"]["train_turns"])


@needs_all
def test_checker_passes_every_generated_episode(built):
    q, _infos = CK.quality_check(built["rows"], built["ctx"])
    assert q["ok"], q["problem_counts"]
    ov = CK.overlap_check(built["rows"], built["ctx"])
    assert ov["ok"], {k: v for k, v in ov.items() if v}


@needs_all
def test_generation_is_deterministic_and_seeded():
    D = DD.Data(limit=3000)
    a = [G.gen_single(D, "train", i) for i in range(5)] + [G.gen_modify(D, "valid", i) for i in range(5)]
    b = [G.gen_single(D, "train", i) for i in range(5)] + [G.gen_modify(D, "valid", i) for i in range(5)]
    assert a == b and any(x for x in a)


@needs_all
def test_train_and_valid_never_contain_test_only_material(built):
    for s in ("train", "valid"):
        for r in built["rows"][s]:
            assert not r["metadata"]["unseen_combo"] and not r["metadata"]["holdout"]
            for _t, q in CK.user_reqs(r):
                assert not K.is_heldout_combo(q)
                if q.get("premise", {}).get("type") == "fictional_nation":
                    assert q["country"] in DD.FICTIONAL[s]


@needs_all
def test_every_source_ship_is_in_the_episode_split(built):
    X_ = built["ctx"]
    for r in all_eps(built):
        assert r["metadata"]["sources"]
        assert all(X_.assign[sid] == r["metadata"]["split"] for sid in r["metadata"]["sources"])


@needs_all
def test_category_shapes(built):
    eps = all_eps(built)
    assert all(r["metadata"]["turns_user"] == 1 for r in eps if r["category"] in ("single", "infeasible", "ood"))
    assert all(r["metadata"]["turns_user"] >= 2 for r in eps if r["category"] == "modify")
    assert all(2 <= r["metadata"]["turns_user"] <= 4 for r in eps if r["category"] == "context")
    rep = [r for r in eps if r["category"] == "repair"]
    assert rep and all(r["metadata"]["turns"][0]["role"] == "initial" and r["metadata"]["turns"][0]["idx"] not in r["metadata"]["train_turns"] for r in rep)
    assert {t["status"] for r in rep for t in r["metadata"]["turns"] if t["role"] == "initial"} == {"feasible"}  # the broken design claims to be feasible
    assert {s for r in eps if r["category"] == "infeasible" for s in r["metadata"]["status_seq"]} <= {"relaxed", "infeasible"}
    assert all(r["metadata"]["train_turns"] == [len(r["messages"]) - 1] for r in eps if r["category"] == "context")


# --------------------------------------------------------------------------------------------------
# negative tests: the checker must catch broken episodes
# --------------------------------------------------------------------------------------------------
@needs_all
def test_checker_flags_unsatisfied_feasible_and_prose_and_status_mix(built):
    ep = find(built, lambda r: r["category"] == "single")
    set_asst(ep, 2, lambda o: o["design"].update(speed_kn=1.5))
    assert {"feasible_unsatisfied", "feasible_validator"} <= codes(ep, built)
    ep = find(built, lambda r: r["category"] == "single")
    ep["messages"][2]["content"] = "Here is the design: " + ep["messages"][2]["content"]
    assert "assistant_json" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "single")
    ep["messages"][2]["content"] = ep["messages"][2]["content"] + "\nThat's it."
    assert "assistant_json" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "single")
    set_asst(ep, 2, lambda o: o.update(status="relaxed"))
    c = codes(ep, built)
    assert "relaxed_empty_relaxations" in c and "status_meta_mismatch" in c
    ep = find(built, lambda r: r["category"] == "single")
    set_asst(ep, 2, lambda o: o.update(conflicts=[{"constraint": "x", "reason": "y"}]))
    assert "feasible_with_conflicts" in codes(ep, built)


@needs_all
def test_checker_flags_infeasible_and_relaxed_violations(built):
    ep = find(built, lambda r: r["category"] == "infeasible" and r["metadata"]["status_seq"] == ["infeasible"])
    set_asst(ep, 2, lambda o: o.update(design={"ship_type": "destroyer"}))
    assert "infeasible_with_design" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "infeasible" and r["metadata"]["status_seq"] == ["infeasible"])
    set_asst(ep, 2, lambda o: o.update(conflicts=[]))
    assert "infeasible_without_conflicts" in codes(ep, built)
    # a hull-type / contradiction conflict made "relaxable with a small gap" contradicts the decision rule
    ep = find(built, lambda r: r["category"] == "infeasible" and r["metadata"]["status_seq"] == ["infeasible"])
    ep["metadata"]["turns"][0]["conflict_info"] = [{"constraint": "x", "family": "speed", "relaxable": True, "gap": 0.1, "mission_core": False}]
    assert "status_rule_mismatch" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "infeasible" and r["metadata"]["status_seq"] == ["relaxed"])
    set_asst(ep, 2, lambda o: o.update(relaxations=[]))
    assert "relaxed_empty_relaxations" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "infeasible" and r["metadata"]["status_seq"] == ["relaxed"])
    set_asst(ep, 2, lambda o: o.update(design=None))
    assert "relaxed_without_design" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "infeasible" and r["metadata"]["status_seq"] == ["relaxed"])
    set_asst(ep, 2, lambda o: o["relaxations"][0].update(constraint="min_speed_kn" if o["relaxations"][0]["constraint"] != "min_speed_kn" else "max_displacement_t"))
    assert codes(ep, built) & {"relaxed_set_mismatch", "relaxed_conflict_relaxation_mismatch"}
    # an infeasible request that real ships can satisfy: remove the injected conflict from the request
    ep = find(built, lambda r: r["category"] == "infeasible" and r["metadata"]["status_seq"] == ["infeasible"] and r["metadata"].get("family") in ("speed", "gun_cal", "gun_barrels", "torpedo", "aircraft"))
    u = json.loads(ep["messages"][1]["content"])
    u["req"]["max_displacement_t"] = 10 ** 6
    for k in ("min_speed_kn", "min_gun_barrels", "min_gun_barrels_cal_mm", "min_aircraft", "min_torpedo_tubes", "max_complement"):
        u["req"].pop(k, None)
    u["req"]["required"] = [t for t in u["req"].get("required", []) if not t.startswith("main_gun")]
    ep["messages"][1]["content"] = K.jdump(u)
    assert "oracle_possible" in codes(ep, built)


@needs_all
def test_checker_flags_design_level_defects(built):
    ep = find(built, lambda r: r["category"] == "single")
    set_asst(ep, 2, lambda o: o["design"].update(power_hp=float("nan")))
    c = codes(ep, built)
    assert "design_nan_inf" in c
    ep = find(built, lambda r: r["category"] == "single")
    set_asst(ep, 2, lambda o: o["design"].update(ship_type_fine=""))
    assert "design_empty_string" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "single")
    set_asst(ep, 2, lambda o: o["design"].update(mystery=1))
    assert any(c.startswith("design_unknown_keys") for c in codes(ep, built))
    ep = find(built, lambda r: r["category"] == "single")
    set_asst(ep, 2, lambda o: o["design"].update(length_m=9999))
    assert any(c.startswith("design_schema_range") for c in codes(ep, built))
    ep = find(built, lambda r: r["category"] == "single")
    set_asst(ep, 2, lambda o: o["design"].update(ship_type="spaceship"))
    assert any("ship_type" in c for c in codes(ep, built))


@needs_all
def test_checker_flags_structure_and_request_defects(built):
    ep = find(built, lambda r: r["category"] == "single")
    ep["messages"][1], ep["messages"][2] = ep["messages"][2], ep["messages"][1]
    assert "roles_order" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "single")
    ep["messages"][0]["content"] += " "
    assert "system_prompt" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "single")
    u = json.loads(ep["messages"][1]["content"])
    u["req"]["max_displacement_t"] = "big"
    u["req"]["year"] = 1700
    ep["messages"][1]["content"] = K.jdump(u)
    c = {x for x in codes(ep, built)} | {d for _c, d in CK.check_episode(ep, built["ctx"])[0]}
    assert "req_type" in c
    ep = find(built, lambda r: r["category"] == "single")
    ep["metadata"]["split"] = "test" if ep["metadata"]["split"] != "test" else "train"
    assert "source_split" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "single")
    ep["metadata"]["train_turns"] = []
    assert "train_turns" in codes(ep, built)


@needs_all
def test_checker_flags_repair_defects(built):
    ep = find(built, lambda r: r["category"] == "repair" and r["metadata"].get("repair_method") in ("rule", "gold_fallback") and r["metadata"]["rounds"] == 1)
    last = len(ep["messages"]) - 1
    ep["messages"][last]["content"] = ep["messages"][2]["content"]  # the "fix" is the broken design again
    c = codes(ep, built)
    assert "repair_unchanged" in c and "feasible_validator" in c
    ep = find(built, lambda r: r["category"] == "repair" and r["metadata"].get("repair_method") in ("rule", "gold_fallback") and r["metadata"]["rounds"] == 1)
    fb = json.loads(ep["messages"][3]["content"])
    fb["validator"]["violations"][0]["detail"] = "made up"
    ep["messages"][3]["content"] = K.jdump(fb)
    assert "repair_violation_detail_differs" in codes(ep, built)
    ep = find(built, lambda r: r["category"] == "repair" and r["metadata"].get("repair_method") in ("rule", "gold_fallback") and r["metadata"]["rounds"] == 1)
    fb = json.loads(ep["messages"][3]["content"])
    fb["validator"]["violations"][0] = {"code": "req_speed_below", "field": "speed_kn", "detail": "x"}
    ep["messages"][3]["content"] = K.jdump(fb)
    assert codes(ep, built) & {"repair_violation_not_reproduced", "repair_violation_count"}
    ep = find(built, lambda r: r["category"] == "repair" and r["metadata"].get("repair_method") in ("rule", "gold_fallback") and r["metadata"]["rounds"] == 1)
    ep["messages"][2]["content"] = ep["messages"][-1]["content"]  # the "broken" design is actually valid
    assert "initial_has_no_violation" in codes(ep, built)


@needs_all
def test_checker_flags_context_and_ood_and_modify_defects(built):
    ep = find(built, lambda r: r["category"] == "context" and r["metadata"]["status_seq"][-1] == "feasible" and r["metadata"]["turns_user"] >= 2)
    ep["messages"][-1]["content"] = ep["messages"][2]["content"]  # the answer copies the first (different) design
    assert codes(ep, built) & {"context_copies_earlier_design", "context_final_type_mismatch", "feasible_unsatisfied"}
    ep = find(built, lambda r: r["category"] == "ood" and r["metadata"]["premise_type"] == "tech_early")
    u = json.loads(ep["messages"][1]["content"])
    u["req"]["premise"] = {"type": "tech_early", "tech": "sonar", "available_from": u["req"]["year"]}  # the waiver no longer covers the technology the design uses
    ep["messages"][1]["content"] = K.jdump(u)
    assert codes(ep, built) & {"waiver_mismatch", "ood_unpremised_anachronism", "feasible_validator", "ood_premise_not_needed"}
    ep = find(built, lambda r: r["category"] == "modify")
    ep["messages"][4]["content"] = ep["messages"][2]["content"]
    assert "modify_design_unchanged" in codes(ep, built)


@needs_all
def test_overlap_check_detects_duplicates_and_leaks(built):
    rows = {s: copy.deepcopy(v) for s, v in built["rows"].items()}
    base = rows["train"][0]
    dup = copy.deepcopy(base)
    dup["id"] = "dv1_ffffffff"
    dup["metadata"]["split"] = "test"
    rows["test"].append(dup)
    ov = CK.overlap_check(rows, built["ctx"])
    assert not ov["ok"]
    assert ov["exact_duplicate_episodes"] >= 1 and ov["normalized_request_text_across_splits"] >= 1 and ov["source_ship_in_multiple_splits"] >= 1
    rows2 = {s: copy.deepcopy(v) for s, v in built["rows"].items()}
    rows2["valid"].append(copy.deepcopy(rows2["valid"][0]))
    assert CK.overlap_check(rows2, built["ctx"])["id_duplicates"] == 1
    # a request identical to an existing design-v0 / eval_v1 request is reported
    v0 = json.loads((C.OUT_DIR / "train.jsonl").read_text(encoding="utf-8").splitlines()[0])
    rows3 = {s: copy.deepcopy(v) for s, v in built["rows"].items()}
    ep = copy.deepcopy(rows3["train"][0])
    u = json.loads(ep["messages"][1]["content"])
    u0 = json.loads(v0["messages"][1]["content"])
    u["request"], u["req"] = u0["request"], u0["req"]
    ep["messages"][1]["content"] = K.jdump(u)
    ep["id"] = "dv1_eeeeeeee"
    rows3["train"].append(ep)
    ov3 = CK.overlap_check(rows3, built["ctx"])
    assert ov3["vs_design_v0"]["episodes_with_identical_request_text"] >= 1 and not ov3["ok"]


@needs_all
def test_tokens_stats_and_full_estimate_are_consistent(built):
    tok = CK.token_stats(built["rows"], K.DEFAULT_TOKENIZER if K.DEFAULT_TOKENIZER.exists() else None)
    assert set(tok["by_category"]) == set(K.CATEGORIES)
    for c, v in tok["by_category"].items():
        assert v["tokens_avg"] > 0 and v["trained_tokens_avg"] <= v["assistant_tokens_avg"] + 1e-6
    sizes = {s: (built["dir"] / f"{s}.jsonl").stat().st_size for s in K.SPLITS}
    est = CK.full_estimate(tok, 100000, sizes, N_TOTAL)
    assert est["n_full"] == 100000 and est["tokens_total"] > 1e7 and est["epoch_hours_train"] > 0
    assert math.isclose(est["tokens_total"], sum(est["tokens_total_by_category"].values()), rel_tol=1e-3)


# --------------------------------------------------------------------------------------------------
# ood tech_early: the era of the source ship and "only the premised technology is added"
# --------------------------------------------------------------------------------------------------
@needs_all
@pytest.mark.parametrize("tech", ["radar", "sonar", "esm_ecm", "helicopter", "ssm", "sam", "ciws", "gas_turbine", "nuclear"])
def test_ood_tech_early_adds_only_the_premised_technology(tech):
    from tools import shipknow_design_v1_ood as OD
    from tools import shipknow_design_v1_ops as O

    D = test_ood_tech_early_adds_only_the_premised_technology.D if hasattr(test_ood_tech_early_adds_only_the_premised_technology, "D") else DD.Data()
    test_ood_tech_early_adds_only_the_premised_technology.D = D
    rng = K.sub_rng(5, tech)
    got = None
    for _ in range(200):
        got = OD._tech_case(D, "train", rng, tech)
        if got:
            break
    assert got, tech
    rec, design, req, premise, _phrase, waivers = got
    src = D.design(rec)
    assert req["year"] == C.request_year(rec)                       # the source ship is of the request year (gap 0, spec: within 3)
    assert 6 <= K.cap_early_by(OD.TECH_CAP[tech], req["year"]) <= OD.MAX_PREMISE_SHIFT
    assert K.check_design(req, design, waivers)["ok"] and not K.check_design(req, design)["ok"]
    changed = set(O.changed_fields(src, design))
    assert changed <= {"sensors", "weapons", "air", "propulsion", "displacement_normal_t", "displacement_full_t", "displacement_standard_t", "length_m", "beam_m", "draught_m",
                       "power_hp", "complement", "endurance_nm"}
    # nothing but the premised technology is new: same propulsion unless it is the premised one, same torpedo tubes, same sensor kinds apart from the added one
    if tech not in ("gas_turbine", "nuclear"):
        assert design.get("propulsion") == src.get("propulsion")
    assert [w for w in design["weapons"] if w["kind"] == "torpedo_tube"] == [w for w in src["weapons"] if w["kind"] == "torpedo_tube"]
    assert len(set(design.get("sensors") or []) - set(src.get("sensors") or [])) <= 1
    assert {w["kind"] for w in design["weapons"]} - {w["kind"] for w in src["weapons"]} <= {"ssm", "sam", "ciws", "aircraft_helicopter", "aviation_facility"}


@needs_all
def test_checker_flags_source_ship_of_the_wrong_era(built):
    ep = find(built, lambda r: r["category"] == "single")
    X_ = built["ctx"]
    req_year = json.loads(ep["messages"][1]["content"])["req"]["year"]
    far = next(rid for rid, r in X_.rec.items() if X_.assign[rid] == ep["metadata"]["split"] and abs(C.request_year(r) - req_year) > 12 and rid in X_.designs)
    ep["metadata"]["turns"][0]["source"] = far
    assert "source_year_gap" in codes(ep, built)


@needs_all
def test_year_gap_summary_is_reported(built):
    q, _ = CK.quality_check(built["rows"], built["ctx"])
    yg = q["year_gap_request_vs_source"]
    assert set(yg) == set(K.CATEGORIES)
    assert all(v["max_abs"] <= 7 for v in yg.values()) and all(yg[c]["max_abs"] <= 5 for c in ("single", "repair", "infeasible", "ood", "context"))
