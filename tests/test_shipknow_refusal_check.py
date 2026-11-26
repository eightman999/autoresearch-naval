"""拒否照合フィルタ（tools/shipknow_refusal_check.py）のテスト: 合成の実艦インデックスでの判定、再要求エピソードの形、merge。"""

from __future__ import annotations

import json
from pathlib import Path

from tools import shipknow_design_v1_core as K
from tools import shipknow_infer_hf as INF
from tools import shipknow_refusal_check as RC


def _ship(rid: str, year: int, dn: float, speed: float, guns: list[dict]) -> tuple[dict, dict]:
    rec = {"id": rid, "country": "Japan", "year": year}
    d = {"ship_type": "destroyer", "displacement_normal_t": dn, "speed_kn": speed, "weapons": guns}
    return rec, d


def _index() -> K.DesignIndex:
    ships = [_ship("c1", 1935, 1700, 35, [{"kind": "gun", "n": 3, "per": 2, "cal": 127}]),
             _ship("c2", 1938, 2000, 34, [{"kind": "gun", "n": 2, "cal": 120}]),
             _ship("c3", 1960, 1500, 33, [{"kind": "gun", "n": 2, "cal": 127}])]
    return K.DesignIndex([r for r, _ in ships], {r["id"]: d for r, d in ships})


REQ = {"country": "Japan", "year": 1940, "ship_type": "destroyer", "max_displacement_t": 1800, "displacement_basis": "normal", "min_speed_kn": 34, "min_gun_barrels": 6,
       "min_gun_barrels_cal_mm": 127}


def test_feasible_and_invalid_pass_through():
    idx = _index()
    assert RC.check_output(idx, REQ, {"status": "feasible", "design": {}})["verdict"] == "feasible"
    assert RC.check_output(idx, REQ, None)["verdict"] == "invalid"
    assert RC.check_output(idx, REQ, {"design": {}})["verdict"] == "invalid"


def test_refusal_with_witness_is_unsupported():
    idx = _index()
    obj = {"status": "relaxed", "conflicts": [{"constraint": "min_gun_barrels", "reason": "x"}]}
    v = RC.check_output(idx, REQ, obj)
    assert v["verdict"] == "unsupported" and v["witness_ids"] == ["c1"]


def test_witness_must_not_be_designed_after_year_ahead():
    idx = _index()
    req = {**REQ, "year": 1930}  # c1 (1935) は要求年 + 3 より後
    v = RC.check_output(idx, req, {"status": "infeasible", "conflicts": [{"constraint": "min_speed_kn", "reason": "x"}]})
    assert v["verdict"] != "unsupported" and v["n_witness"] == 0


def test_supported_and_false_claims():
    idx = _index()
    req = {**REQ, "max_displacement_t": 1600, "min_speed_kn": 34}  # 1600 t 以下で 34 kn の艦は無い（c3 は 33 kn かつ 1960）
    # 速力の主張: 排水量だけの条件 + 速力では満たせない -> 主張は正しい
    v = RC.check_output(idx, req, {"status": "relaxed", "conflicts": [{"constraint": "min_speed_kn", "reason": "x"}]})
    assert v["verdict"] == "supported" and v["claims"][0]["alone_ok"] is False
    # 要求に無い制約と、排水量だけなら満たせる制約（砲身数は c3 を除くと 1600 t 以下に無いので別の要求で確認）を挙げた主張は誤り
    req2 = {**REQ, "year": 1965, "max_displacement_t": 1600, "min_speed_kn": 34, "min_gun_barrels": 2, "min_gun_barrels_cal_mm": 127}
    v2 = RC.check_output(idx, req2, {"status": "relaxed", "conflicts": [{"constraint": "min_gun_barrels", "reason": "x"}, {"constraint": "required:sam", "reason": "y"}]})
    assert v2["verdict"] == "false_claims"
    assert [c["in_request"] for c in v2["claims"]] == [True, False] and v2["claims"][0]["alone_ok"] is True


def test_size_claim_is_not_refuted():
    idx = _index()
    req = {**REQ, "max_displacement_t": 1600}
    v = RC.check_output(idx, req, {"status": "relaxed", "conflicts": [{"constraint": "max_displacement_t", "reason": "x"}]})
    assert v["claims"][0]["alone_ok"] is None and v["verdict"] == "supported"


def test_rerequest_episode_is_multi_turn_eval_compatible():
    idx = _index()
    obj = {"status": "relaxed", "conflicts": [{"constraint": "min_gun_barrels", "reason": "x"}]}
    v = RC.check_output(idx, REQ, obj)
    fb = RC.feedback_turn(idx, REQ, v)
    body = json.loads(fb)
    assert body["validator"]["valid"] is False and body["instruction"] == RC.INSTRUCTION
    assert "1 real ship class" in body["validator"]["violations"][0]["detail"]
    problem = {"id": "B01", "category": "B", "req": REQ, "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]}
    ep = RC.rerequest_episode(problem, json.dumps(obj), fb)
    item = INF.multi_turn_item(ep)
    assert item["turn_index"] == 4 and [m["role"] for m in item["messages"]] == ["system", "user", "assistant", "user"]


def test_merge_replaces_only_rerequested_rows(tmp_path: Path):
    def w(name: str, rows: list[dict]) -> Path:
        p = tmp_path / name
        RC.write_jsonl(p, rows)
        return p

    pred = w("pred", [{"id": "B01", "seed": 1, "text": "old"}, {"id": "E01", "seed": 1, "text": "keep"}])
    ver = w("ver", [{"id": "B01", "seed": 1, "verdict": "unsupported"}, {"id": "E01", "seed": 1, "verdict": "supported"}])
    rr = w("rr", [{"id": "B01"}])
    rp = w("rp", [{"id": "B01", "text": "new"}])
    out = tmp_path / "out"
    assert RC.main(["merge", "--pred", str(pred), "--verdicts", str(ver), "--rerequest", str(rr), "--rerequest-pred", str(rp), "--out", str(out)]) == 0
    rows = RC.read_jsonl(out)
    assert [r["text"] for r in rows] == ["new", "keep"] and rows[0]["rerequested"] is True
