"""Split / task builder / scorer tests for shipknow v0 (synthetic records, no NSDB needed)."""

from __future__ import annotations

import json

import pytest

from tools import shipknow_build_tasks as B
from tools import shipknow_score as S
from tools import shipknow_split as SP


def make_rec(i: int, **kw):
    base = {
        "id": f"c{i:05d}", "class_name": f"CLASS{i} Class", "class_base": f"class{i:03d}name", "country": "Japan", "source_url": f"u{i}",
        "year": 1935, "year_source": "class_name", "ship_type": "destroyer", "ship_type_fine": "destroyer", "type_source": "type",
        "displacement_normal_t": 1500 + 37 * i, "displacement_full_t": 1800 + 37 * i, "length_m": 100.0 + i, "beam_m": 9.0 + i / 10, "draught_m": 3.0,
        "shafts": 2, "power_hp": 40000 + 100 * i, "speed_kn": 34.0, "endurance_nm": 5000, "complement": 200, "propulsion": ["steam_turbine"],
        "weapons": [{"kind": "gun", "count": 3, "per_mount": 2, "caliber_mm": 127.0, "caliber_length": 50.0, "rounds": None, "name": "x"},
                    {"kind": "torpedo_tube", "count": 2, "per_mount": 4, "caliber_mm": 610.0, "caliber_length": None, "rounds": 16, "name": "TT"}],
        "armament_parse_ok": True, "sensors": [], "sensor_kinds": [], "sensors_known": True,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------
def test_groups_keep_class_variants_and_transfers_together():
    recs = [
        make_rec(1, class_base="hatsuharu", country="Japan", length_m=109.5, beam_m=10.0),
        make_rec(2, class_base="hatsuharu", country="Japan", length_m=118.0, beam_m=11.0, source_url="u2"),  # same country+name
        make_rec(3, class_base="hatsuharu", country="Taiwan", length_m=109.5, beam_m=10.0, source_url="u3"),  # transfer
        make_rec(4, class_base="other", country="Japan", length_m=60.0, beam_m=6.0, displacement_normal_t=400, source_url="u4"),
        make_rec(5, class_base="twin", country="Italy", length_m=60.0, beam_m=6.0, displacement_normal_t=401, source_url="u5"),  # same L x B
    ]
    roots, stats = SP.build_groups(recs)
    assert roots[0] == roots[1] == roots[2]
    assert roots[3] == roots[4] != roots[0]
    assert stats["R2_country_name"] >= 1 and stats["R4_length_beam"] >= 1


def test_split_has_no_group_overlap_and_is_deterministic():
    recs = []
    for i in range(300):
        # every 3rd class has a sister row with the same name -> groups of size 2
        recs.append(make_rec(i, class_base=f"name{i // 2:04d}x", length_m=40 + i * 0.7, beam_m=5 + i * 0.05, displacement_normal_t=100 + i * 13,
                             power_hp=1000 + i * 17, speed_kn=20 + (i % 9), ship_type="patrol" if i % 3 else "destroyer"))
    roots, _ = SP.build_groups(recs)
    a1 = SP.assign(recs, roots, seed=1)
    a2 = SP.assign(recs, roots, seed=1)
    assert a1 == a2
    split_of_group = {}
    for i, r in enumerate(roots):
        split_of_group.setdefault(r, set()).add(a1[i])
    assert all(len(v) == 1 for v in split_of_group.values())
    counts = {s: sum(1 for v in a1.values() if v == s) for s in ("train", "valid", "test")}
    assert counts["train"] > counts["valid"] > 0 and counts["test"] > 0
    ver = SP.verify(recs, a1)
    assert ver["test_vs_train"]["shared_country_class_base"] == 0
    assert ver["test_vs_train"]["shared_length_beam_fingerprint(>=15m)"] == 0


# ---------------------------------------------------------------------------
# task builder
# ---------------------------------------------------------------------------
def test_rounding_rules():
    assert B.rnd("displacement_normal_t", 1494) == 1490
    assert B.rnd("displacement_normal_t", 33.4) == 33
    assert B.rnd("length_m", 109.4) == 109.5 and B.rnd("length_m", 36.34) == 36.3
    assert B.rnd("speed_kn", 35.3) == 35.5
    assert B.rnd("power_hp", 42049) == 42000 and B.rnd("power_hp", 4249) == 4200
    assert B.rnd("beam_m", 10.04) == 10


def test_task_examples_never_contain_names_or_hidden_fields():
    rec = make_rec(7, class_name="HATSUHARU1935) Class", class_base="hatsuharu", source_url="https://secret.example/hatsuharu.html")
    exs = B.build_t1(rec, 1) + B.build_t2(rec, 1) + B.build_t3(rec, 1)
    tasks = {e["task"] for e in exs}
    assert tasks == {"T1", "T2", "T3"} and len(exs) == 6
    for e in exs:
        user = e["messages"][1]["content"]
        assert "HATSUHARU" not in user.upper() and "secret.example" not in user and "class_name" not in user
        assert json.loads(user) is not None
        json.loads(e["messages"][2]["content"])  # output is JSON
        if e["task"] == "T1":
            assert "ship_type" not in json.loads(user) and "ship_type_fine" not in json.loads(user)
        if e["task"] == "T2":
            u = json.loads(user)
            assert "weapons" not in u and "sensors" not in u and u["want"] == ["weapons", "sensors"]
        if e["task"] == "T3":
            u, g = json.loads(user), json.loads(e["messages"][2]["content"])
            assert set(g) == set(u["predict"]) and not (set(u["predict"]) & (set(u) - {"predict"}))
            assert 1 <= len(g) <= 3
    # sort_keys compact JSON
    assert exs[0]["messages"][1]["content"] == json.dumps(json.loads(exs[0]["messages"][1]["content"]), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def test_t2_weapons_target_is_compact_and_unknown_sensors_are_not_asked():
    rec = make_rec(8, sensors=None, sensor_kinds=None, sensors_known=False, year=1960)
    t2 = B.build_t2(rec, 1)
    for e in t2:
        assert json.loads(e["messages"][1]["content"])["want"] == ["weapons"]
        assert set(json.loads(e["messages"][2]["content"])) == {"weapons"}
    w = json.loads(t2[0]["messages"][2]["content"])["weapons"]
    assert w == [{"cal": 127, "kind": "gun", "n": 3, "per": 2}, {"cal": 610, "kind": "torpedo_tube", "n": 2, "per": 4}]


def test_unknown_type_rows_get_t3_only():
    rec = make_rec(9, ship_type="unknown", ship_type_fine=None)
    assert B.build_t1(rec, 1) == [] and B.build_t2(rec, 1) == []
    assert len(B.build_t3(rec, 1)) >= 1


# ---------------------------------------------------------------------------
# scorer
# ---------------------------------------------------------------------------
LABELS = ["destroyer", "patrol", "submarine"]


def row(task, id_, gold, inp=None, variant="full"):
    return {"id": id_, "task": task, "variant": variant, "gold": gold, "inp": inp or {}}


def test_score_t1_accuracy_invalid_and_ranking():
    rows = [row("T1", "a", {"ship_type": "destroyer", "ship_type_fine": "destroyer"}), row("T1", "b", {"ship_type": "patrol", "ship_type_fine": "patrol-boat"}),
            row("T1", "c", {"ship_type": "submarine", "ship_type_fine": "submarine"}), row("T1", "d", {"ship_type": "patrol", "ship_type_fine": "patrol-boat"})]
    preds = {
        "a": {"id": "a", "text": '```json\n{"ship_type":"destroyer","ship_type_fine":"destroyer"}\n```', "ranking": {"destroyer": -0.1, "patrol": -3, "submarine": -4}},
        "b": {"id": "b", "output": {"ship_type": "destroyer", "ship_type_fine": "x"}, "ranking": {"destroyer": -0.2, "patrol": -0.9, "submarine": -5}},
        "c": {"id": "c", "text": "not json at all"},
        # d missing
    }
    res = S.score(rows, preds, LABELS, tasks=("T1",))["T1"]
    assert res["json_invalid_rate"] == 0.5 and res["missing_predictions"] == 1
    m = res["metrics"]
    assert m["coarse_acc"] == pytest.approx(0.25) and m["fine_acc"] == pytest.approx(0.25)
    assert m["ranking"]["n"] == 2 and m["ranking"]["top3_acc"] == 1.0 and m["ranking"]["top1_acc"] == 0.5
    assert 0 <= m["ranking"]["ece"] <= 1


def test_score_t2_matching_rules():
    gold = {"weapons": [{"kind": "gun", "n": 3, "per": 2, "cal": 127}, {"kind": "torpedo_tube", "n": 2, "per": 4, "cal": 610}, {"kind": "depth_charge", "n": 2}],
            "sensors": ["radar", "sonar"]}
    r = row("T2", "x", gold, {"want": ["weapons", "sensors"]})
    good = {"x": {"id": "x", "output": {"weapons": [{"kind": "gun", "n": 4, "per": 2, "cal": 130}, {"kind": "torpedo_tube", "n": 1, "per": 4, "cal": 533}, {"kind": "depth_charge", "n": 8}],
                                         "sensors": ["radar"]}}}
    m = S.score([r], good, LABELS, tasks=("T2",))["T2"]
    assert m["schema_violation_rate"] == 0
    wi = m["metrics"]["weapon_item"]
    assert (wi["tp"], wi["fp"], wi["fn"]) == (2, 1, 1)  # 130 vs 127 within 5 %; 533 vs 610 not; DC matches on kind
    assert m["metrics"]["main_caliber_acc"] == 1.0
    assert m["metrics"]["main_barrels"]["mae"] == 2.0  # 4x2=8 vs 3x2=6
    assert m["metrics"]["sensor_kinds"]["recall"] == 0.5 and m["metrics"]["sensor_kinds"]["precision"] == 1.0
    bad = {"x": {"id": "x", "output": {"weapons": [{"kind": "laser"}], "sensors": []}}}
    mb = S.score([r], bad, LABELS, tasks=("T2",))["T2"]
    assert mb["schema_violation_rate"] == 1.0 and mb["schema_errors"] == {"bad_weapon_kind": 1}


def test_score_t3_errors_and_intervals():
    r = row("T3", "z", {"length_m": 100.0, "speed_kn": 30.0}, {"predict": ["length_m", "speed_kn"]}, variant="k2")
    p = {"z": {"id": "z", "output": {"length_m": 105.0, "speed_kn": 20.0},
               "samples": [{"length_m": v, "speed_kn": 28 + v / 100} for v in (90, 95, 100, 105, 110, 98, 102, 99)]}}
    res = S.score([r], p, LABELS, tasks=("T3",))["T3"]["metrics"]
    assert res["overall"]["n"] == 2
    assert res["per_field"]["length_m"]["mape"] == pytest.approx(0.05)
    assert res["per_field"]["speed_kn"]["within_10pct"] == 0.0
    assert res["interval"]["per_field"]["length_m"]["coverage"] == 1.0
    wrong = {"z": {"id": "z", "output": {"length_m": 105.0}}}
    sc = S.score([r], wrong, LABELS, tasks=("T3",))["T3"]
    assert sc["schema_violation_rate"] == 1.0
