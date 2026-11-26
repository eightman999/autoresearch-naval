"""shipknow v1 retriever / RAG builder tests (synthetic records, no NSDB needed)."""

from __future__ import annotations

import json

from tools import shipknow_build_tasks as B
from tools import shipknow_build_tasks_rag as RB
from tools import shipknow_retriever as R


def make_rec(i: int, **kw):
    base = {
        "id": f"c{i:05d}", "class_name": f"CLASS{i} Class", "class_base": f"class{i:03d}name", "country": "Japan", "source_url": f"http://u{i}",
        "year": 1935 + i % 5, "ship_type": "destroyer", "ship_type_fine": "destroyer",
        "displacement_normal_t": 1500 + 37 * i, "displacement_full_t": 1800 + 37 * i, "length_m": 100.0 + i, "beam_m": 9.0 + i / 10, "draught_m": 3.0,
        "shafts": 2, "power_hp": 40000 + 100 * i, "speed_kn": 34.0, "endurance_nm": 5000, "complement": 200, "propulsion": ["steam_turbine"],
        "weapons": [{"kind": "gun", "count": 3, "per_mount": 2, "caliber_mm": 127.0, "caliber_length": 50.0, "rounds": None, "name": "x"},
                    {"kind": "torpedo_tube", "count": 2, "per_mount": 4, "caliber_mm": 610.0, "caliber_length": None, "rounds": 16, "name": "TT"}],
        "armament_parse_ok": True, "sensors": [], "sensor_kinds": ["radar"], "sensors_known": True,
    }
    base.update(kw)
    return base


def make_pool(n: int = 40):
    recs = [make_rec(i, country="Japan" if i % 2 else "Italy") for i in range(1, n + 1)]
    group_of = {r["id"]: f"g{(i // 4):03d}" for i, r in enumerate(recs)}  # groups of 4
    return R.Pool(recs, group_of), group_of


def view_of(rec, **drop):
    v = B.full_view(rec)
    for k in drop:
        v.pop(k, None)
    return v


def test_search_excludes_own_group_and_respects_k():
    pool, group_of = make_pool()
    rec = pool.recs[10]
    view = view_of(rec)
    own = group_of[rec["id"]]
    free = pool.search(view, "T1", exclude_group=None, k=3)
    assert pool.ids[free[0][0]] == rec["id"]  # without the exclusion the example itself is the nearest
    nb = pool.search(view, "T1", exclude_group=own, k=3)
    assert len(nb) == 3
    assert all(group_of[pool.ids[j]] != own for j, _ in nb)


def test_distance_uses_only_the_given_view():
    """the retriever sees the view only: hiding fields in the view changes the result, the record's hidden values cannot."""
    pool, group_of = make_pool()
    rec = pool.recs[7]
    v1 = view_of(rec)
    v2 = dict(v1)
    d1 = pool.distances(v1, "T3", R.DEFAULT_PARAMS["T3"])
    d2 = pool.distances(v2, "T3", R.DEFAULT_PARAMS["T3"])
    assert (d1 == d2).all()
    v3 = {k: v for k, v in v1.items() if k not in ("length_m", "beam_m")}  # T3-style hiding
    d3 = pool.distances(v3, "T3", R.DEFAULT_PARAMS["T3"])
    assert not (d1 == d3).all()


def test_t3_neighbors_have_the_hidden_fields():
    recs = [make_rec(i, complement=None if i % 3 == 0 else 200) for i in range(1, 40)]
    group_of = {r["id"]: f"g{i}" for i, r in enumerate(recs)}
    pool = R.Pool(recs, group_of)
    view = view_of(recs[5], complement=None)
    view["predict"] = ["complement"]
    nb = pool.search(view, "T3", k=5)
    assert nb and all(pool.recs[j].get("complement") for j, _ in nb)


def test_payload_has_no_names_urls_or_ids_and_is_task_specific():
    rec = make_rec(3)
    for task in ("T1", "T2", "T3"):
        p = R.neighbor_payload(rec, task, {"want": ["weapons", "sensors"]}, 0.123)
        text = json.dumps(p)
        assert "CLASS3" not in text and "class003name" not in text and "http" not in text and "c00003" not in text
        assert set(p) <= RB.ALLOWED_NBR_KEYS
        assert p["d"] == 0.12 and p["ship_type"] == "destroyer"
    assert "weapons" in R.neighbor_payload(rec, "T1") and "sensors" in R.neighbor_payload(rec, "T1")
    assert "weapons" in R.neighbor_payload(rec, "T2") and "armament" not in R.neighbor_payload(rec, "T2")
    t3 = R.neighbor_payload(rec, "T3")
    assert "weapons" not in t3 and t3["armament"]["main_gun_mm"] == 127 and t3["armament"]["main_barrels"] == 6
    # T2 without sensors in "want": sensors are not shown
    assert "sensors" not in R.neighbor_payload(rec, "T2", {"want": ["weapons"]})


def test_build_example_keeps_target_and_prefix():
    pool, group_of = make_pool()
    rec = pool.recs[12]
    view = view_of(rec)
    ex = {"id": f"{rec['id']}:T1:full", "task": "T1", "variant": "full", "row": rec["id"], "group": group_of[rec["id"]],
          "messages": [{"role": "system", "content": B.SYSTEM["T1"]}, {"role": "user", "content": B.jdump(view)},
                       {"role": "assistant", "content": '{"ship_type":"destroyer","ship_type_fine":"destroyer"}'}], "meta": {}}
    out = RB.build_example(ex, pool, R.DEFAULT_PARAMS, group_of, 3)
    assert out["messages"][2] == ex["messages"][2]
    assert out["messages"][1]["content"].startswith(ex["messages"][1]["content"][:-1] + ',"neighbors":')
    parsed = json.loads(out["messages"][1]["content"])
    assert len(parsed["neighbors"]) == 3
    assert all(group_of[i] != group_of[rec["id"]] for i in out["meta"]["nbr"])
    assert "neighbors" in out["messages"][0]["content"]
