"""tools/shipknow_design_v1_eval.py（design-v1 の採点器）のテスト。

gold の assistant 出力をそのまま予測にした「自己採点」が（ほぼ）満点になること、代表的な誤り（status 取り違え・黙って緩和・要求未充足・複製・前提外の技術など）が
検出されること、集計・Markdown・CLI が動くことを確認する。実艦レコード / validator 閾値（design_v0）と data/navspec/shipknow_design_v1/test.jsonl が要る。無ければ skip。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import shipknow_design_common as C
from tools import shipknow_design_v1_core as K
from tools import shipknow_design_v1_eval as E

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "data" / "navspec" / "shipknow_design_v1" / "test.jsonl"
needs_data = pytest.mark.skipif(not (C.V0_DIR / "records.jsonl").exists() or not (C.OUT_DIR / "validator_thresholds.json").exists() or not GOLD.exists(),
                                reason="v0_2 records / design_v0 thresholds / design_v1 test.jsonl not generated")


@pytest.fixture(scope="module")
def gold_rows():
    """test.jsonl の先頭から、category ごとに最大 40 件（全 status・subtype を含むよう広めに取る）。"""
    out, cnt = [], {}
    with GOLD.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if cnt.get(r["category"], 0) < 40:
                cnt[r["category"]] = cnt.get(r["category"], 0) + 1
                out.append(r)
            if len(cnt) == 6 and all(v >= 40 for v in cnt.values()):
                break
    return out


def pred_of(ep, text=None, **kw):
    k = max(ep["metadata"]["train_turns"])
    return {"id": ep["id"], "category": ep["category"], "turn_index": k, "text": text if text is not None else ep["messages"][k]["content"], "seed": None, **kw}


def gold_obj(ep):
    return json.loads(ep["messages"][max(ep["metadata"]["train_turns"])]["content"])


def pick(rows, cat, pred=lambda ep, g: True):
    for ep in rows:
        if ep["category"] == cat and pred(ep, gold_obj(ep)):
            return ep
    pytest.skip(f"no {cat} row")


def score(ep, text):
    return E.score_row(pred_of(ep, text), ep)


def dump(o):
    return json.dumps(o, separators=(",", ":"))


@needs_data
def test_gold_as_prediction_scores_perfectly(gold_rows):
    recs, agg, missing = E.evaluate([pred_of(ep) for ep in gold_rows], gold_rows)
    assert not missing and len(recs) == len(gold_rows)
    ov = agg["overall"]
    for key in ("json_ok", "schema_ok", "status_match", "row_ok", "design_req_ok", "validator_ok", "conflict_set_exact", "design_exact_gold", "repair_valid", "context_final_ok",
                "gold_infeasible_design_null", "infeasible_design_null"):
        assert ov[key]["n"] > 0 and ov[key]["rate"] == 1.0, (key, ov[key])
    assert ov["false_feasible"]["k"] == 0 and ov["check_crash_any"]["k"] == 0
    assert ov["context_copy_prev"]["k"] == 0 and ov["modify_changed"]["rate"] == 1.0
    assert ov["modify_target_follow"]["rate"] >= 0.9 and ov["ood_no_unpremised_anachronism"]["rate"] == 1.0  # modify の向きは real 経路で数 % 揺れる（gold 自身でも）
    assert set(agg["by_category"]) == {"single", "modify", "repair", "infeasible", "ood", "context"}
    # 混同行列は gold の status 分布と一致
    conf = ov["status_confusion"]
    assert all(conf[s]["invalid"] == 0 and sum(conf[s].values()) == conf[s][s] for s in K.STATUSES)


@needs_data
def test_unparsable_and_schema_errors(gold_rows):
    ep = pick(gold_rows, "single")
    r = score(ep, "I think a frigate would be good.")
    assert not r["json_ok"] and not r["json_loose_ok"] and r["pred_status"] == "invalid" and not r["status_match"] and not r["row_ok"]
    g = gold_obj(ep)
    r = score(ep, dump(g) + " trailing text")  # 後ろの余計な文字: strict は不可、loose は採点できる
    assert not r["json_ok"] and r["json_loose_ok"] and r["schema_ok"] and r["row_ok"]
    r = score(ep, dump({k: g[k] for k in ("status", "design")}))  # conflicts / relaxations が無い
    assert r["json_ok"] and not r["schema_ok"] and r["pred_status"] == "feasible"
    bad = {**g, "design": None}  # feasible なのに design null
    r = score(ep, dump(bad))
    assert not r["schema_ok"] and "design_null_but_feasible" in r["schema_problems"] and not r["row_ok"]


@needs_data
def test_status_confusion_and_design_null(gold_rows):
    ep = pick(gold_rows, "infeasible", lambda e, g: g["status"] == "infeasible")
    g = gold_obj(ep)
    ok = score(ep, dump(g))
    assert ok["status_match"] and ok["gold_infeasible_design_null"] and ok["row_ok"]
    fake = score(ep, dump({**g, "status": "feasible", "design": gold_obj(pick(gold_rows, "single"))["design"]}))
    assert not fake["status_match"] and fake["pred_status"] == "feasible" and not fake["gold_infeasible_design_null"]
    assert fake["false_feasible"]  # 衝突のある要求に黙って feasible と答える
    inf_with_design = score(ep, dump({**g, "design": gold_obj(pick(gold_rows, "single"))["design"]}))
    assert not inf_with_design["schema_ok"] and inf_with_design["infeasible_design_null"] is False
    _, agg, _ = E.evaluate([pred_of(ep, dump(g)), pred_of(ep, dump({**g, "status": "relaxed"}))], gold_rows)
    assert agg["overall"]["status_confusion"]["infeasible"]["infeasible"] == 1 and agg["overall"]["status_confusion"]["infeasible"]["relaxed"] == 1


@needs_data
def test_feasible_claim_checked_against_requirements_and_validator(gold_rows):
    ep = pick(gold_rows, "single")
    g = gold_obj(ep)
    slow = copy.deepcopy(g)
    slow["design"]["speed_kn"] = 1  # 最低速力を満たさない
    r = score(ep, dump(slow))
    assert r["status_match"] and not r["design_req_ok"] and r["false_feasible"] and not r["row_ok"]
    huge = copy.deepcopy(g)
    huge["design"]["displacement_normal_t"] = huge["design"]["displacement_full_t"] = huge["design"]["displacement_standard_t"] = 10**6
    r = score(ep, dump(huge))
    assert not r["design_req_ok"] and not r["validator_ok"] and r["false_feasible"]
    broken = copy.deepcopy(g)
    broken["design"]["weapons"] = "none"  # 型が壊れていても例外で止まらない
    r = score(ep, dump(broken))
    assert r["validator_ok"] is False and r["false_feasible"] is True


@needs_data
def test_relaxed_conflict_names_and_honest_relaxations(gold_rows):
    ep = pick(gold_rows, "infeasible", lambda e, g: g["status"] == "relaxed")
    g = gold_obj(ep)
    ok = score(ep, dump(g))
    assert ok["conflict_set_exact"] and ok["conflict_relaxed_exact"] and ok["design_req_ok"] and ok["relaxed_hidden_violations"] == 0
    wrong = copy.deepcopy(g)
    wrong["conflicts"] = [{"constraint": "min_speed_kn", "reason": "x"}] if g["conflicts"][0]["constraint"] != "min_speed_kn" else [{"constraint": "max_complement", "reason": "x"}]
    r = score(ep, dump(wrong))
    assert not r["conflict_set_exact"] and r["conflict_recall"] == 0.0 and r["conflict_precision"] == 0.0
    silent = copy.deepcopy(g)
    silent["relaxations"] = []  # 緩和を宣言しない（黙って緩和）
    r = score(ep, dump(silent))
    assert not r["schema_ok"]
    undeclared = {**g, "status": "relaxed", "relaxations": [{"constraint": "max_complement", "requested": 1, "relaxed_to": 2, "reason": "x"}]}
    r = score(ep, dump(undeclared))
    assert r["relaxed_hidden_violations"] >= 1 and not r["design_req_ok"]  # 実際に未充足の制約を宣言していない


@needs_data
def test_repair_checks(gold_rows):
    ep = pick(gold_rows, "repair", lambda e, g: g["status"] == "feasible")
    r = score(ep, ep["messages"][max(ep["metadata"]["train_turns"])]["content"])
    assert r["repair_valid"] and r["repair_changed"] and r["repair_fixed_listed"] and r["repair_new_violations"] == 0
    prev = json.loads(ep["messages"][max(ep["metadata"]["train_turns"]) - 2]["content"])
    r = score(ep, dump({"status": "feasible", "conflicts": [], "relaxations": [], "design": prev["design"]}))  # 直前の（違反を含む）設計をそのまま返す
    assert not r["repair_changed"] and not r["repair_fixed_listed"] and not r["repair_valid"] and r["false_feasible"]


@needs_data
def test_context_copy_and_distractor_type(gold_rows):
    ep = pick(gold_rows, "context", lambda e, g: g["status"] == "feasible" and any(json.loads(m["content"])["design"] for m in e["messages"][2:-1:2]))
    r = score(ep, ep["messages"][-1]["content"])
    assert r["context_final_ok"] and not r["context_copy_prev"] and r["context_type_match"] and r["context_era_ok"]
    prevs = [json.loads(m["content"])["design"] for m in ep["messages"][2:-1:2] if json.loads(m["content"])["design"]]
    r = score(ep, dump({"status": "feasible", "conflicts": [], "relaxations": [], "design": prevs[-1]}))  # 前ターンの設計の複製
    assert r["context_copy_prev"] and not r["context_final_ok"]
    req = E.episode_context(ep, max(ep["metadata"]["train_turns"]))["req"]
    if prevs[-1].get("ship_type") != req["ship_type"]:
        assert r["context_follows_distractor_type"] and not r["context_type_match"]


@needs_data
def test_modify_direction_following(gold_rows):
    ep = pick(gold_rows, "modify", lambda e, g: e["metadata"]["turns"][-1].get("change") == "disp_up" and g["status"] == "feasible")
    k = max(ep["metadata"]["train_turns"])
    r = score(ep, ep["messages"][k]["content"])
    assert r["modify_changed"] and r["modify_target_follow"] is True
    prev = json.loads(ep["messages"][k - 2]["content"])
    same = score(ep, dump({"status": "feasible", "conflicts": [], "relaxations": [], "design": prev["design"]}))  # 変更前の設計を返す
    assert same["modify_changed"] is False and same["modify_target_follow"] is False
    down = copy.deepcopy(gold_obj(ep))
    for f in ("displacement_normal_t", "displacement_full_t", "displacement_standard_t"):
        if f in down["design"]:
            down["design"][f] = prev["design"].get(f, 100) * 0.5
    assert score(ep, dump(down))["modify_target_follow"] is False
    # direction_check の基本（連動項目）
    a = {"speed_kn": 20, "power_hp": 1000}
    assert E.direction_check("speed_up", a, {"speed_kn": 30, "power_hp": 2500}, {}) == (True, True)
    assert E.direction_check("speed_up", a, {"speed_kn": 30, "power_hp": 900}, {}) == (True, False)
    assert E.direction_check("crew_down", {"complement": 100}, {"complement": 80}, {"max_complement": 85}) == (True, None)
    assert E.direction_check("country_change", a, a, {}) == (None, None)


@needs_data
def test_ood_unpremised_anachronism_and_premise_tech(gold_rows):
    ep = pick(gold_rows, "ood", lambda e, g: g["design"] is not None)
    g = gold_obj(ep)
    assert score(ep, dump(g))["ood_no_unpremised_anachronism"] is True
    req = E.episode_context(ep, max(ep["metadata"]["train_turns"]))["req"]
    anach = copy.deepcopy(g)
    anach["design"]["weapons"] = list(anach["design"].get("weapons") or []) + [{"kind": "ciws", "n": 2, "per": 1, "cal": 30}, {"kind": "sam", "n": 8, "per": 1}]
    anach["design"]["sensors"] = list(anach["design"].get("sensors") or []) + ["fire_control_radar"]
    waive = set(K.waivers_for_premise(req.get("premise")))
    r = score(ep, dump(anach))
    if req["year"] < 1960 and not ({"ciws", "sam"} <= waive):
        assert r["ood_no_unpremised_anachronism"] is False and r["ood_anachronisms"]
    assert E.premise_tech_present({"tech": "sam"}, {"weapons": [{"kind": "sam", "n": 2}]}) is True
    assert E.premise_tech_present({"tech": "sam"}, {"weapons": []}) is False
    assert E.premise_tech_present({"tech": "nuclear"}, {"propulsion": ["nuclear"]}) is True and E.premise_tech_present({}, {}) is None


@needs_data
def test_aggregate_markdown_and_cli(gold_rows, tmp_path):
    preds = [pred_of(ep) for ep in gold_rows[:30]] + [pred_of(gold_rows[0], "nonsense", seed=3)]
    (tmp_path / "gold.jsonl").write_text("".join(json.dumps(r) + "\n" for r in gold_rows), encoding="utf-8")
    (tmp_path / "pred.jsonl").write_text("".join(json.dumps(p) + "\n" for p in preds), encoding="utf-8")
    import sys

    argv = sys.argv
    sys.argv = ["x", "--pred", str(tmp_path / "pred.jsonl"), "--gold", str(tmp_path / "gold.jsonl"), "--out-json", str(tmp_path / "o.json"), "--out-md", str(tmp_path / "o.md")]
    try:
        assert E.main() == 0
    finally:
        sys.argv = argv
    rep = json.loads((tmp_path / "o.json").read_text())
    assert rep["meta"]["n_scored"] == 31 and len(rep["rows"]) == 31 and rep["overall"]["status_confusion"]["feasible"]["invalid"] >= 1
    md = (tmp_path / "o.md").read_text()
    for h in ("status 一致率", "validator 合格", "混同行列", "false_feasible"):
        assert h in md
    # 予測にあって gold に無い id は数えない
    recs, _, missing = E.evaluate([{"id": "zzz", "turn_index": 2, "text": "{}"}], gold_rows)
    assert recs == [] and missing == ["zzz"]
