"""design-v1 playground（フォーム版 + 会話モード）のテスト。推論サーバ（design-v1 / gemma）無しで動く: 推論は全てモック。

* フォーム -> req / 要求文 / user メッセージ（modify・repair・context を含む）が学習データ（data/navspec/shipknow_design_v1/train.jsonl）と同じ書式になること
* conflicts の実データ照合・出力の検査
* 会話モード: gemma 出力のアクション抽出・スキーマ検証、艦名（NSDB を mode=ro で読む）、説明文の数値照合、repair / resample / modify / context の流れ
学習データ・実艦レコード・validator 閾値・NSDB が無ければ skip。
"""

from __future__ import annotations

import copy
import itertools
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools import shipknow_playground as P
from tools import shipknow_playground_chat as PC

K = P.K
C = P.C
ROOT = Path(__file__).resolve().parent.parent
TRAIN = ROOT / "data" / "navspec" / "shipknow_design_v1" / "train.jsonl"
needs = pytest.mark.skipif(not (C.V0_DIR / "records.jsonl").exists() or not (C.OUT_DIR / "validator_thresholds.json").exists() or not TRAIN.exists(),
                           reason="v0_2 records / design_v0 thresholds / design_v1 train.jsonl not generated")
needs_nsdb = pytest.mark.skipif(not PC.NSDB_PATH.exists(), reason="nsdb/NSDB.db not present")
J = json.loads


@pytest.fixture(scope="module")
def pg():
    return P.Playground(llm_url="http://127.0.0.1:1", log=lambda *_: None)


@pytest.fixture(scope="module")
def eps():
    out = []
    with TRAIN.open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= 3000:
                break
            out.append(J(line))
    return out


def user_turns(ep):
    return [J(m["content"]) for m in ep["messages"] if m["role"] == "user"]


# ==================================================================================================
# フォーム版: req・書式
# ==================================================================================================
@needs
def test_system_prompt_is_the_training_one(eps):
    assert eps[0]["messages"][0]["content"] == K.SYSTEM_V1
    assert P.new_conversation() == [{"role": "system", "content": K.SYSTEM_V1}]


@needs
def test_form_roundtrip_reproduces_training_reqs(eps):
    n = 0
    for ep in eps:
        req = user_turns(ep)[0]["req"]
        got, _ = P.form_to_req(P.req_to_form(req))
        assert json.dumps(got) == json.dumps(req)  # キー順・数値表記まで一致
        n += 1
    assert n >= 2000


@needs
def test_user_and_repair_messages_match_training_format(eps):
    n = 0
    for ep in eps:
        for m in ep["messages"]:
            if m["role"] != "user":
                continue
            u = J(m["content"])
            got = P.repair_message(u["validator"]["violations"]) if "validator" in u else P.user_message(u["request"], u["req"], u.get("nbr"), u.get("reference"))
            assert got == m["content"]
            n += 1
    assert n > 4000


@needs
def test_request_text_for_single_repair_infeasible_is_reproduced(eps):
    for cat in ("single", "repair", "infeasible"):
        rows = [user_turns(e)[0] for e in eps if e["category"] == cat]
        assert rows
        ok = sum(any(K.request_text_v1(u["req"], v) == u["request"] for v in range(6)) for u in rows)
        assert ok == len(rows), cat


@needs
def test_premise_sentences_are_reproduced(eps):
    rows = [(e, user_turns(e)[0]) for e in eps if e["category"] == "ood"]
    assert len(rows) >= 100
    ok = 0
    for _e, u in rows:
        req = u["req"]
        hit = False
        for v, pv in itertools.product(range(6), range(2)):
            r2, pt = P.form_to_req(P.req_to_form(req), pv)
            if K.request_text_v1(r2, v, premise_text=pt) == u["request"]:
                hit = True
                break
        ok += hit
    assert ok / len(rows) >= 0.95


@needs
def test_modify_changes_roundtrip_and_sentence(eps):
    n = ok_rt = ok_txt = 0
    for ep in eps:
        if ep["category"] != "modify":
            continue
        us = user_turns(ep)
        for a, b in zip(us, us[1:]):
            n += 1
            ch = P.diff_changes(a["req"], b["req"])
            ok_rt += P.apply_changes(a["req"], ch) == b["req"]
            en, _ja = P.change_pieces(a["req"], b["req"])
            cands = {P.modify_sentence(list(perm), v) for perm in itertools.permutations(en) for v in range(8)} if en else set()
            ok_txt += b["request"] in cands
    assert n >= 300
    assert ok_rt == n  # 変更の適用は学習データの req と（キー順まで）一致する
    assert ok_txt / n >= 0.9  # 文言（学習データには要求の差分に現れない句もある）


@needs
def test_modify_sentence_matches_generator_forms():
    import random

    from tools import shipknow_design_v1_gen_basic as G

    pieces = ["raise the minimum speed to 30 kn", "add anti-ship missiles"]
    for seed in range(40):
        rng = random.Random(seed)
        want = G._sentence(pieces, rng)
        assert want in {P.modify_sentence(pieces, v) for v in range(8)}


@needs
def test_repair_violations_reproduce_training_feedback(eps):
    n = ok = 0
    for ep in eps:
        if ep["category"] != "repair":
            continue
        ms = ep["messages"]
        req = J(ms[1]["content"])["req"]
        for i, m in enumerate(ms):
            if m["role"] == "user" and "validator" in J(m["content"]):
                design = J(ms[i - 1]["content"])["design"]
                res = K.check_design(req, design, K.waivers_for_premise(req.get("premise")))
                n += 1
                ok += P.RP.violations_for_user(res) == J(m["content"])["validator"]["violations"]
    assert n >= 100 and ok == n


@needs
def test_nbr_rule_nb1_reproduces_training_nbr(pg, eps):
    n = ok = 0
    for ep in eps:
        if ep["category"] != "single":
            continue
        u = user_turns(ep)[0]
        n += 1
        ok += pg.D.neighbors(u["req"], ep["metadata"]["sources"]) == u["nbr"]
        if n >= 25:
            break
    assert ok == n
    # playground は元艦が無いので source_ids=[]。k=3、艦名・id を含まない短いキー
    nb = pg.neighbors(user_turns(eps[0])[0]["req"])
    assert len(nb) == 3 and all({"cn", "y", "ty"} <= set(x) and "id" not in x and "name" not in x for x in nb)


@needs
def test_presets_build_and_form_errors(pg):
    for pr in P.PRESETS:
        b = pg.build_request(pr["form"], 0)
        assert b["req"]["country"] and b["request"].strip() and b["request_ja"] and len(b["nbr"]) == 3
        assert json.dumps(P.form_to_req(P.req_to_form(b["req"]))[0]) == json.dumps(b["req"])
    with pytest.raises(ValueError):
        P.form_to_req({"year": 1900, "ship_type": "patrol"})
    with pytest.raises(ValueError):
        P.form_to_req({"country": "Japan", "year": 1900, "ship_type": "patrol", "premise": {"type": "fictional_nation"}})
    with pytest.raises(ValueError):
        P.apply_changes({"country": "Japan", "year": 1900, "ship_type": "patrol", "max_displacement_t": 100}, [{"field": "max_displacement_t", "value": ""}])
    with pytest.raises(ValueError):
        P.apply_changes({"country": "Japan"}, [{"field": "bogus", "value": 1}])


@needs
def test_unknown_country_warns(pg):
    f = dict(P.PRESETS[7]["form"], country="Atlantis")
    assert any("実艦データにありません" in w for w in pg.build_request(f, 0)["warnings"])


# ==================================================================================================
# 検査・実データ照合
# ==================================================================================================
def _out(status, design=None, conflicts=None, relaxations=None):
    return K.dump_output(status, design, conflicts, relaxations)


@needs
def test_gold_outputs_pass_the_checker(pg, eps):
    n = bad = 0
    for ep in eps[:600]:
        req = None
        for i, m in enumerate(ep["messages"]):
            if m["role"] == "user":
                u = J(m["content"])
                req = u.get("req", req)
            if m["role"] == "assistant" and i in ep["metadata"]["train_turns"]:
                a = pg.analyze(req, m["content"])
                n += 1
                bad += any(x["level"] == "bad" and not x["text"].startswith("根拠不一致") for x in a["messages"])
    assert n > 300 and bad == 0


@needs
def test_analyze_flags_silent_loosening(pg):
    req = P.form_to_req(P.PRESETS[7]["form"])[0]  # 2016 patrol: ciws + radar, 550-860 t, >= 23 kn
    real = pg.idx_all.designs[pg.idx_all.satisfying(req, year_hi=2019)[0]]
    slow = copy.deepcopy(real)
    slow["speed_kn"] = 10
    a = pg.analyze(req, _out("feasible", slow))
    assert a["false_feasible"] and any(m["level"] == "bad" and "min_speed_kn" in m["text"] for m in a["messages"])
    assert a["repair_violations"] and a["repair_violations"][0]["code"] == "req_speed_below"
    # relaxed と宣言していない未充足 = 黙って緩和
    a = pg.analyze(req, _out("relaxed", slow, [{"constraint": "required:radar", "reason": "x"}], [{"constraint": "required:radar", "requested": "radar", "relaxed_to": "none", "reason": "x"}]))
    assert a["relaxed_hidden"] == ["min_speed_kn"]
    # infeasible は design null
    assert any(m["level"] == "bad" for m in pg.analyze(req, _out("infeasible", real))["messages"])
    assert not pg.analyze(req, "not json")["json_loose"]


@needs
def test_grounding_numeric_era_and_existence(pg):
    req = P.form_to_req(P.PRESETS[6]["form"])[0]  # 1942 destroyer, 12 x 127 mm barrels <= 1500 t
    best, _i, _n = pg._best(pg.idx_all, req, "min_gun_barrels", lambda d: float(P.X.gun_barrels(d, 127)), max)
    assert best is not None and best < 12
    good = {"constraint": "min_gun_barrels", "reason": f"requested 12 gun barrels; real destroyer classes up to 1500 t (normal) carry at most {int(best)}"}
    bad = {"constraint": "min_gun_barrels", "reason": f"requested 12 gun barrels; real destroyer classes up to 1500 t (normal) carry at most {int(best) + 4}"}
    assert pg.ground(req, "infeasible", [good], [])["verdict"] == "ok"
    g = pg.ground(req, "infeasible", [bad], [])
    assert g["verdict"] == "mismatch" and g["items"][0]["verdict"] == "mismatch"
    # 年代: 1895 に SAM は無い。主張した初出年が表と違えば不一致、要求年に存在するなら不一致
    req1895 = P.form_to_req(P.PRESETS[3]["form"])[0]
    fy = K.first_year("sam")
    ok = pg.ground_conflict(req1895, {"constraint": "required:sam", "reason": f"surface-to-air missiles first appear around {fy}; none were available in 1895"}, 0)
    assert ok["verdict"] == "ok"
    wrong = pg.ground_conflict(req1895, {"constraint": "required:sam", "reason": "surface-to-air missiles first appear around 1900; none were available in 1895"}, 0)
    assert wrong["verdict"] == "mismatch"
    req1975 = dict(req1895, year=1975)
    assert pg.ground_conflict(req1975, {"constraint": "required:sam", "reason": f"surface-to-air missiles first appear around {fy}; none were available in 1975"}, 0)["verdict"] == "mismatch"
    # 要求を全て満たす実艦が train に存在するのに拒否 -> 不一致
    req16 = P.form_to_req(P.PRESETS[7]["form"])[0]
    g = pg.ground(req16, "infeasible", [{"constraint": "min_speed_kn", "reason": "requested 23 kn exceeds the fastest comparable real patrol vessel, which reached 20 kn"}], [])
    assert g["verdict"] == "mismatch" and g["problems"] and g["real"]["n_train"] >= 1
    # 矛盾（min > max）
    rc = dict(req16, min_displacement_t=900)
    assert pg.ground_conflict(rc, {"constraint": "min_displacement_t + max_displacement_t", "reason": "x"}, 0)["verdict"] == "ok"
    assert pg.ground_conflict(req16, {"constraint": "min_displacement_t + max_displacement_t", "reason": "x"}, 0)["verdict"] == "mismatch"
    # 要求に無い制約を挙げる
    assert pg.ground_conflict(req16, {"constraint": "required:ballistic_missile", "reason": "x"}, 0)["verdict"] in ("mismatch", "weak", "ok")


@needs
def test_grounding_of_training_conflicts_is_mostly_consistent(pg, eps):
    n = mism = 0
    for ep in eps:
        if ep["category"] != "infeasible":
            continue
        ms = ep["messages"]
        req = J(ms[1]["content"])["req"]
        obj = J(ms[2]["content"])
        g = pg.ground(req, obj["status"], obj["conflicts"], obj["relaxations"])
        n += 1
        mism += g["verdict"] == "mismatch"
    assert n >= 100 and mism / n < 0.05


# ==================================================================================================
# HTTP（推論はモックの OpenAI 互換サーバ）
# ==================================================================================================
class _Stub(BaseHTTPRequestHandler):
    bodies: list = []

    def log_message(self, *a):  # noqa: D401
        pass

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):  # noqa: N802
        body = J(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).bodies.append(body)
        content = body["messages"][-1]["content"]
        out = {"choices": [{"message": {"role": "assistant", "content": '{"status":"infeasible","conflicts":[{"constraint":"min_speed_kn","reason":"requested 99 kn exceeds the fastest comparable real patrol vessel, which reached 30 kn"}],"relaxations":[],"design":null}'}, "finish_reason": "stop"}],
               "usage": {"completion_tokens": 5}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        _ = content


def _post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, J(r.read())
    except urllib.error.HTTPError as e:
        return e.code, J(e.read())


@needs
def test_http_endpoints_with_stub_llm_and_without(pg):
    stub = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    llm = f"http://127.0.0.1:{stub.server_address[1]}"
    pg2 = P.Playground(llm_url=llm, data=pg.D, log=lambda *_: None)
    httpd = P.serve(pg2, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as r:
            assert b"design-v1 Playground" in r.read()
        with urllib.request.urlopen(base + "/api/meta") as r:
            meta = J(r.read())
        assert meta["system"] == K.SYSTEM_V1 and len(meta["presets"]) >= 5 and meta["countries"]
        with urllib.request.urlopen(base + "/api/health") as r:
            assert J(r.read())["ok"] is True
        code, b = _post(base + "/api/build_request", {"form": P.PRESETS[0]["form"], "variant": 1})
        assert code == 200 and b["req"]["country"] == "Japan" and b["nbr"]
        code, pm = _post(base + "/api/preview_modify", {"req": b["req"], "changes": [{"field": "min_speed_kn", "value": 25}], "variant": 0})
        assert code == 200 and pm["request"] == "Now raise the minimum speed to 25 kn." and "nbr" in pm and pm["nbr"] == []
        code, e = _post(base + "/api/build_request", {"form": {"year": 1}})
        assert code == 400 and not e["ok"]
        # turn: request -> (stub は不可能と答える。模擬の「根拠不一致」)
        turn = {"kind": "request", "request": b["request"], "req": b["req"], "nbr": b["nbr"]}
        code, t1 = _post(base + "/api/turn", {"messages": [], "turn": turn, "gen": {"temperature": 0.7, "seed": 5}})
        assert code == 200 and t1["user_content"] == P.user_message(b["request"], b["req"], b["nbr"])
        assert t1["messages"][0] == {"role": "system", "content": K.SYSTEM_V1} and [m["role"] for m in t1["messages"]] == ["system", "user", "assistant"]
        sent = _Stub.bodies[-1]
        assert sent["temperature"] == 0.7 and sent["seed"] == 5 and sent["messages"][1]["content"] == t1["user_content"]
        assert t1["analysis"]["status"] == "infeasible" and t1["analysis"]["grounding"]["verdict"] == "mismatch"
        # repair の user ターン
        code, t2 = _post(base + "/api/turn", {"messages": t1["messages"], "turn": {"kind": "repair", "req": t1["req"], "validator": [{"code": "req_speed_below", "field": "min_speed_kn", "detail": "x"}]}})
        assert code == 200 and J(t2["user_content"]) == {"validator": {"valid": False, "violations": [{"code": "req_speed_below", "field": "min_speed_kn", "detail": "x"}]},
                                                          "instruction": "Fix the design. Keep the original request as far as possible."}
    finally:
        httpd.shutdown()
        stub.shutdown()
    # 推論サーバ無し: 例外にせず ok=False
    pg3 = P.Playground(llm_url="http://127.0.0.1:1", data=pg.D, log=lambda *_: None)
    r = pg3.turn([], {"kind": "request", "request": "x", "req": b["req"], "nbr": []})
    assert r["ok"] is False and "接続できません" in r["error"]
    assert pg3.health()["ok"] is False


# ==================================================================================================
# 会話モード
# ==================================================================================================
def test_parse_gemma_output_variants():
    a = PC.parse_gemma_output('<say>はい</say>\n<action>{"action":"design","req":{"country":"Japan"}}</action>')
    assert a["say"] == "はい" and a["action"]["action"] == "design" and a["error"] is None
    a = PC.parse_gemma_output('了解です。\n```json\n{"action":"modify","changes":{"min_speed_kn":30}}\n```')
    assert a["action"]["changes"] == {"min_speed_kn": 30} and "了解" in a["say"]
    a = PC.parse_gemma_output('設計します {"action":"design","req":{"country":"Italy","year":1960,"ship_type":"destroyer"}} 以上')
    assert a["action"]["req"]["country"] == "Italy"
    a = PC.parse_gemma_output("<say>どの国ですか？</say>")
    assert a["action"] is None and a["say"] == "どの国ですか？" and a["error"] is None
    a = PC.parse_gemma_output('<say>x</say><action>{"action":"design",}</action>')
    assert a["action"] is None and "JSON が読めません" in a["error"]
    a = PC.parse_gemma_output("<action>[1,2]</action>")
    assert "object" in a["error"]


@needs
def test_sanitize_req_schema_validation(pg):
    r, w = PC.sanitize_req(pg, {"country": "Japan", "year": "1920", "ship_type": "armored_protected_cruiser", "required": ["torpedo_tubes", "main_gun>=203mm"], "max_displacement_t": "11,000", "bogus": 1})
    assert r["year"] == 1920 and r["max_displacement_t"] == 11000 and r["required"] == ["main_gun>=203mm", "torpedo_tubes"] and any("bogus" in x for x in w)
    assert list(r) == list(K.order_req(r))
    for bad in ({"country": "Japan", "year": 1920}, {"country": "Japan", "year": 1700, "ship_type": "destroyer"}, {"country": "Japan", "year": 1920, "ship_type": "spaceship"},
                {"country": "Japan", "year": 1920, "ship_type": "destroyer", "required": ["laser"]}, {"country": "Japan", "year": 1920, "ship_type": "destroyer", "max_displacement_t": "big"},
                {"country": "Japan", "year": 1920, "ship_type": "destroyer", "required_propulsion": ["warp"]}, {"country": "Japan", "year": 1920, "ship_type": "destroyer", "displacement_basis": "wet"},
                {"country": "Atlantis", "year": 1920, "ship_type": "destroyer"},
                {"country": "Atlantis", "year": 1920, "ship_type": "destroyer", "premise": {"type": "fictional_nation", "like": "Atlantis"}},
                {"country": "Atlantis", "year": 1920, "ship_type": "destroyer", "premise": {"type": "tech_early", "tech": "laser", "available_from": 1900}}, "not a dict"):
        with pytest.raises(PC.ActionError):
            PC.sanitize_req(pg, bad)
    r, _ = PC.sanitize_req(pg, {"country": "Norvane", "year": 1975, "ship_type": "frigate"}, {"type": "fictional_nation", "like": "France"})
    assert r["premise"] == {"type": "fictional_nation", "like": "France"}
    r, w = PC.sanitize_req(pg, {"country": "Japan", "year": 1920, "ship_type": "destroyer", "ship_type_fine": "no-such-fine-type"})
    assert "ship_type_fine" not in r and w


@needs
def test_fill_defaults_uses_real_data_and_keeps_given_values(pg):
    r, _ = PC.sanitize_req(pg, {"country": "Japan", "year": 1920, "ship_type": "armored_protected_cruiser", "required": ["torpedo_tubes"]})
    f, notes = PC.fill_defaults(pg, r)
    assert f["max_displacement_t"] > 5000 and f["min_speed_kn"] > 15 and f["mission"] and len(notes) == 3 and f["displacement_basis"] == "normal"
    r2 = dict(r, max_displacement_t=9000, min_speed_kn=20, mission=["patrol"])
    f2, n2 = PC.fill_defaults(pg, r2)
    assert f2 == K.order_req(r2) and n2 == []


@needs
def test_build_modify_action_applies_changes_in_training_order(pg):
    base = P.form_to_req(P.PRESETS[2]["form"])[0]  # it1960 destroyer
    a = PC.build_modify_action(pg, {"action": "modify", "changes": {"min_speed_kn": 33, "add_required": ["ssm"], "min_aircraft": 1, "main_gun_mm": 152}}, base)
    new = a["req"]
    assert new["min_speed_kn"] == 33 and "ssm" in new["required"] and new["min_aircraft"] == 1 and new["required"][-1] == "main_gun>=152mm"
    assert list(new)[-1] == "min_aircraft"  # 新しい制約は末尾
    again = P.apply_changes(base, a["changes"])
    assert {k: sorted(v) if k == "required" else v for k, v in again.items()} == {k: sorted(v) if k == "required" else v for k, v in new.items()}
    # req だけが来たときは、現在と違うキーだけを変更とみなす（落ちたキーで制約が消えない）
    a = PC.build_modify_action(pg, {"action": "modify", "req": {"country": "Italy", "year": 1960, "ship_type": "destroyer", "min_speed_kn": 34}}, base)
    assert a["req"]["max_displacement_t"] == base["max_displacement_t"] and a["req"]["min_speed_kn"] == 34 and a["req"]["required"] == base["required"]
    # null は制約を外す。入力が壊れていれば ActionError
    a = PC.build_modify_action(pg, {"action": "modify", "changes": {"min_speed_kn": None}}, base)
    assert "min_speed_kn" not in a["req"]
    with pytest.raises(PC.ActionError):
        PC.build_modify_action(pg, {"action": "modify", "changes": {"max_displacement_t": None}}, base)
    with pytest.raises(PC.ActionError):
        PC.build_modify_action(pg, {"action": "modify"}, base)
    with pytest.raises(PC.ActionError):
        PC.build_modify_action(pg, {"action": "modify", "changes": {"add_required": ["laser"]}}, base)


def test_parse_ship_name():
    assert PC.parse_ship_name("富士 <Fuji>") == ("Fuji", "富士")
    assert PC.parse_ship_name("鎮遠 <Chin Yen> (ex-鎮遠 Chen Yuan)") == ("Chin Yen", "鎮遠")
    assert PC.parse_ship_name("Impavido (ex-X)") == ("Impavido", None)
    assert PC.parse_ship_name("") == ("", None)


@needs
@needs_nsdb
def test_ship_namer_reads_nsdb_read_only_and_avoids_names_in_use(pg, monkeypatch):
    uris = []
    real = sqlite3.connect

    def spy(target, *a, **kw):
        uris.append(str(target))
        return real(target, *a, **kw)

    monkeypatch.setattr(PC.sqlite3, "connect", spy)
    namer = PC.ShipNamer(pg)
    got = namer.names_for("Japan", "destroyer", 1960)
    assert uris and all("mode=ro" in u for u in uris)
    assert got["source"] == "nsdb" and 3 <= len(got["ships"]) <= 4 and got["class_name"]
    names = [s["name"].lower() for s in got["ships"]]
    assert len(set(names)) == len(names)
    # 要求年に同国・同艦種で就役中の艦の名前は選ばない
    idx = pg.idx_all
    in_use = set()
    for i in range(len(idx)):
        if idx.recs[i]["country"] == "Japan" and idx.type[i] == "destroyer":
            url = namer._cls[idx.recs[i]["id"]]["url"]
            for s in namer._by_url.get(url, []):
                if s["launched"] is not None and s["launched"] <= 1960 and (s["fate"] is None or s["fate"] > 1960):
                    in_use.add(s["name"].lower())
    assert in_use and not (set(names) & in_use)
    assert namer.names_for("Norvane", "frigate", 1975) is None  # 架空国家は NSDB に無い -> 会話役が命名


def test_explanation_number_check():
    src = json.dumps({"design": {"speed_kn": 27.5, "displacement_normal_t": 9510, "weapons": [{"kind": "gun", "n": 2, "cal": 203}]}, "x": "1895"}, ensure_ascii=False)
    assert PC.check_explanation_numbers("最大速力は 27.5 kn、常備排水量は 9,510 t、口径 203 mm の砲 2 基です。1895 年。", src) == []
    assert PC.check_explanation_numbers("速力は 28 kn、排水量は 9510 t です", src) == ["28"]  # JSON に無い数値
    assert PC.check_explanation_numbers("速力は 27.5 kn（約 51 km/h）", src) == ["51"]  # 換算は不可
    assert PC.check_explanation_numbers("1. 速力 27.5 kn\n2. 排水量 9510 t", src) == []  # 箇条書きの番号
    assert PC.check_explanation_numbers("3 つの特徴があります", src) == []  # 単位の付かない小さな整数
    assert PC.check_explanation_numbers("乗員は 7 名です", src) == ["7"]
    d = {"weapons": [{"kind": "gun", "n": 1, "cal": 76}, {"kind": "asw_rocket", "n": 1}], "sensors": ["radar"]}
    assert PC.check_equipment_words("ヘリコプターを搭載しています。", d) == ["ヘリ"]
    assert PC.check_equipment_words("ヘリコプターは搭載していません。対潜ロケットを持ちます。", d) == []
    assert PC.check_equipment_words("魚雷を搭載します。", d) == ["魚雷"]


def test_template_explanation_uses_only_json_values():
    payload = {"status": "relaxed", "design": {"ship_type": "destroyer", "ship_type_fine": "destroyer", "displacement_normal_t": 2800, "speed_kn": 34, "weapons": [{"kind": "gun", "n": 2, "per": 2, "cal": 127}],
                                               "sensors": ["radar"]}, "conflicts": [{"constraint": "required:sam", "reason": "no SAM in 1940"}], "relaxations": [{"constraint": "required:sam", "requested": "sam", "relaxed_to": "none"}],
               "names": {"class_name": "Foo", "ships": ["A", "B"]}, "check": {"unmet": [], "violations": []}, "grounding_problems": ["x"]}
    payload["design_facts"] = PC.design_facts(payload["design"])
    t = PC.template_explanation(payload)
    assert "2800 t" in t and "口径 127 mm" in t and "Foo" in t and "required:sam" in t and "根拠" in t
    assert PC.check_explanation_numbers(t, json.dumps(payload, ensure_ascii=False)) == []


@needs
def test_gemma_client_sends_enable_thinking_false():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):  # noqa: N802
            seen.append(J(self.rfile.read(int(self.headers["Content-Length"]))))
            data = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        g = PC.GemmaClient(f"http://127.0.0.1:{srv.server_address[1]}", "gemma-4-e4b-it")
        assert g.chat([{"role": "user", "content": "hi"}], temperature=0.3) == "ok"
    finally:
        srv.shutdown()
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False} and seen[0]["model"] == "gemma-4-e4b-it" and seen[0]["temperature"] == 0.3


# ---- オーケストレータ（gemma・design-v1 ともモック）
class FakeGemma:
    """会話役のモック。system の先頭で用途（行動 / 説明 / 命名）を見分け、行動はキュー、説明は設計 JSON の値を並べる。"""

    def __init__(self, actions, explain=None, names=None):
        self.actions, self.explain, self.names = list(actions), explain, names
        self.calls = []
        self.url, self.model = "http://gemma.invalid", "fake"

    def health(self):
        return {"ok": True}

    def chat(self, messages, temperature=0.3, max_tokens=900, seed=None):
        self.calls.append(copy.deepcopy(messages))
        sysm = messages[0]["content"]
        if sysm.startswith(PC.EXPLAIN_SYSTEM[:20]):
            if callable(self.explain):
                return self.explain(messages)
            payload = J(messages[1]["content"].split("設計結果 JSON:\n", 1)[1].rsplit("\n\nこの結果", 1)[0])
            return "結果: " + str(payload["status"]) + "\n" + "\n".join(payload.get("design_facts") or [])
        if sysm.startswith(PC.NAMES_SYSTEM[:20]):
            return self.names or '{"class_name":"Aurora","ship_names":["Vanguard","Polaris","Glacier"]}'
        return self.actions.pop(0)


def _real_design_for(pg, req):
    idx = pg.idx_all
    c = idx.satisfying(req, year_hi=req["year"] + 3)
    assert c, "no real ship satisfies the request in the test"
    return K.order_design(copy.deepcopy(idx.designs[c[0]]))


class FakeDesign:
    """設計役のモック: 台本（呼び出し順の応答 or 関数）。無ければ、要求を満たす実艦の設計を feasible で返す（要求を満たす実艦が無ければ infeasible）。"""

    def __init__(self, pg, script=()):
        self.pg, self.script, self.calls = pg, list(script), []

    def __call__(self, messages, temperature=0.0, seed=None, max_tokens=2048, llm_url=None, model=None):
        i = len(self.calls)
        self.calls.append({"messages": copy.deepcopy(messages), "temperature": temperature, "seed": seed, "url": llm_url, "model": model})
        if i < len(self.script):
            s = self.script[i]
            content = s(messages) if callable(s) else s
        else:
            u = J(messages[-1]["content"])
            req = None
            for m in messages:
                if m["role"] == "user" and "req" in J(m["content"]):
                    req = J(m["content"])["req"]
            assert req is not None
            c = self.pg.idx_all.satisfying(req, year_hi=req["year"] + 3)
            content = _out("feasible", K.order_design(copy.deepcopy(self.pg.idx_all.designs[c[0]]))) if c else _out(
                "infeasible", None, [{"constraint": "required:ssm", "reason": "surface-to-air missiles first appear around 1959; none were available in 1900"}])
            _ = u
        return {"content": content, "finish_reason": "stop", "usage": {"completion_tokens": 1}, "elapsed_s": 0.0}


def act(**kw):
    return "<say>依頼します。</say><action>" + json.dumps(kw, ensure_ascii=False) + "</action>"


@pytest.fixture()
def engine(pg, monkeypatch):
    def make(actions, script=(), explain=None, names=None):
        fd = FakeDesign(pg, script)
        monkeypatch.setattr(pg, "chat", fd)
        eng = PC.ChatEngine(pg, FakeGemma(actions, explain, names), PC.ShipNamer(pg))
        return eng, fd
    return make


JP1920 = {"action": "design", "req": {"country": "Japan", "year": 1920, "ship_type": "armored_protected_cruiser", "required": ["torpedo_tubes"]}, "premise": None}


@needs
@needs_nsdb
def test_chat_design_then_modify_then_context_use_training_formats(pg, engine):
    eng, fd = engine([act(**JP1920), act(action="design", req={"country": "Italy", "year": 1960, "ship_type": "destroyer", "required": ["sam"]})])
    s1 = eng.handle(None, "1920年の日本の装甲巡洋艦、魚雷付きで")
    sid = s1["session_id"]
    lat = s1["latest"]
    assert lat["kind"] == "request" and lat["status"] == "feasible" and lat["names"]["source"] == "nsdb" and lat["fills"]
    c0 = fd.calls[0]["messages"]
    assert c0[0] == {"role": "system", "content": K.SYSTEM_V1} and [m["role"] for m in c0] == ["system", "user"]
    u0 = J(c0[1]["content"])
    assert list(u0) == ["request", "req", "nbr"] and u0["request"] == K.request_text_v1(u0["req"], 0) and len(u0["nbr"]) == 3
    assert c0[1]["content"] == P.user_message(u0["request"], u0["req"], u0["nbr"])
    assert s1["display"][-1]["explanation_source"] == "gemma" and "結果: feasible" in s1["display"][-1]["text"]
    # modify: 学習データと同じ「Now ...」形式 + 更新後の全 req（国・年が変わらないので nbr なし）。直前の設計が文脈に入っている
    v0 = lat["req"]["min_speed_kn"]
    eng.gemma.actions.insert(0, act(action="modify", changes={"min_speed_kn": v0 + 3}))
    s2 = eng.handle(sid, "速力をもっと上げて")
    c1 = fd.calls[1]["messages"]
    assert [m["role"] for m in c1] == ["system", "user", "assistant", "user"]
    u1 = J(c1[3]["content"])
    assert list(u1) == ["request", "req"] and u1["request"] == f"Now raise the minimum speed to {v0 + 3:g} kn." and u1["req"]["min_speed_kn"] == v0 + 3
    assert u1["req"]["required"] == u0["req"]["required"] and c1[2]["content"] == s1["dv1_messages"][2]["content"]
    assert s2["latest"]["kind"] == "modify" and s2["names"] == s1["names"]  # 国・年・艦種が同じなので艦名を引き継ぐ
    # context: 会話を保ったまま別の艦（nbr 付き）
    s3 = eng.handle(sid, "今度は1960年のイタリアの駆逐艦を、対空ミサイル付きで")
    c2 = fd.calls[2]["messages"]
    assert [m["role"] for m in c2][-1] == "user" and len(c2) == 6 and s3["latest"]["kind"] == "context" and "nbr" in J(c2[-1]["content"])
    assert s3["latest"]["names"]["class_name"] and s3["latest"]["req"]["country"] == "Italy"
    # 会話役の履歴は user / assistant が交互
    roles = [m["role"] for m in s3["gemma_history"]]
    assert all(a != b for a, b in zip(roles, roles[1:]))


@needs
@needs_nsdb
def test_chat_modify_country_change_adds_nbr_and_null_requires_removal_words(pg, engine):
    eng, fd = engine([act(**JP1920), act(action="modify", changes={"min_speed_kn": None}), act(action="modify", changes={"country": "Italy"})])
    sid = eng.handle(None, "1920年の日本の装甲巡洋艦")["session_id"]
    s = eng.handle(sid, "速力をもっと上げて")  # null を出したが外す指示ではない -> 会話役に再生成させる（モックは次の応答に country 変更を返す）
    log = s["display"][-1]["log"]
    assert any(l["step"] == "gemma_action_invalid" and "null" in l["error"] for l in log)
    last = J(fd.calls[-1]["messages"][-1]["content"])
    assert last["request"].startswith("Now design it for the Italian navy") or "Italian" in last["request"]
    assert "nbr" in last  # 国の変更では nbr が付く（学習データと同じ）


@needs
@needs_nsdb
def test_chat_repair_loop_sends_training_feedback_up_to_limit(pg, engine):
    req = PC.sanitize_req(pg, JP1920["req"])[0]
    req, _ = PC.fill_defaults(pg, req)
    good = _real_design_for(pg, req)
    slow = copy.deepcopy(good)
    slow["speed_kn"] = 3
    eng, fd = engine([act(**JP1920)], script=[_out("feasible", slow), _out("feasible", slow), _out("feasible", good)])
    s = eng.handle(None, "1920年の日本の装甲巡洋艦、魚雷付きで", {"max_repairs": 2})
    assert len(fd.calls) == 3 and s["latest"]["repairs"] == 2 and s["latest"]["analysis"]["validator_ok"]
    fb = J(fd.calls[1]["messages"][-1]["content"])
    assert list(fb) == ["validator", "instruction"] and fb["validator"]["valid"] is False and fb["instruction"] == "Fix the design. Keep the original request as far as possible."
    assert fb["validator"]["violations"][0]["code"] == "req_speed_below"
    assert [m["role"] for m in fd.calls[2]["messages"]] == ["system", "user", "assistant", "user", "assistant", "user"]
    # 上限 0 回なら修正を送らない（違反は報告に残る）
    eng, fd = engine([act(**JP1920)], script=[_out("feasible", slow)])
    s = eng.handle(None, "1920年の日本の装甲巡洋艦、魚雷付きで", {"max_repairs": 0})
    assert len(fd.calls) == 1 and not s["latest"]["analysis"]["validator_ok"]
    assert any("min_speed_kn" in n or "req_speed_below" in n for n in s["display"][-1]["check_notes"])  # 残った違反は説明とは別に必ず出る


@needs
@needs_nsdb
def test_chat_resamples_ungrounded_refusal_with_temperature_and_new_seed(pg, engine):
    # 1942 の駆逐艦（127 mm × 5 門）: 「4 門が最大」は実データ（train で 5 門）と合わない拒否
    req = {"country": "United-States", "year": 1942, "ship_type": "destroyer", "max_displacement_t": 1500, "min_speed_kn": 34, "required": ["main_gun>=127mm"], "min_gun_barrels": 5, "min_gun_barrels_cal_mm": 127}
    wrong = _out("infeasible", None, [{"constraint": "min_gun_barrels", "reason": "requested 5 gun barrels of 127 mm or larger; real destroyer classes up to 1500 t (normal) carry at most 2"}])
    r5 = PC.sanitize_req(pg, req)[0]
    assert pg.real_satisfying(r5)["n_train"] >= 1
    good = _real_design_for(pg, r5)
    action = act(action="design", req=req)
    eng, fd = engine([action], script=[wrong, wrong, _out("feasible", good)])
    s = eng.handle(None, "1942年のアメリカ駆逐艦、127mm砲5門以上")
    assert [(c["temperature"], c["seed"]) for c in fd.calls] == [(0.0, None), (0.7, 101), (0.7, 102)]
    lat = s["latest"]
    assert lat["status"] == "feasible" and not lat["ungrounded_refusal"] and [a["grounding"] for a in lat["attempts"]] == ["mismatch", "mismatch", "none"]
    # 取り直しの会話は、失敗した試行を引きずらない（user / assistant 1 往復だけ）
    assert [m["role"] for m in fd.calls[2]["messages"]] == ["system", "user"]
    # 取り直しても拒否のままなら、根拠不一致を付けてユーザーに伝える
    eng, fd = engine([action], script=[wrong, wrong, wrong])
    s = eng.handle(None, "1942年のアメリカ駆逐艦、127mm砲5門以上")
    assert s["latest"]["ungrounded_refusal"] and s["display"][-1]["grounding_notes"]
    # max_resamples=0 なら取り直さない
    eng, fd = engine([action], script=[wrong])
    s = eng.handle(None, "1942年のアメリカ駆逐艦、127mm砲5門以上", {"max_resamples": 0})
    assert len(fd.calls) == 1 and s["latest"]["ungrounded_refusal"]


@needs
@needs_nsdb
def test_chat_grounded_infeasible_is_not_resampled(pg, engine):
    action = act(action="design", req={"country": "Japan", "year": 1895, "ship_type": "destroyer", "max_displacement_t": 400, "min_speed_kn": 25, "required": ["sam"]})
    fy = K.first_year("sam")
    ok = _out("infeasible", None, [{"constraint": "required:sam", "reason": f"surface-to-air missiles first appear around {fy}; none were available in 1895"}])
    eng, fd = engine([action], script=[ok])
    s = eng.handle(None, "1895年の日本の駆逐艦に対空ミサイル")
    assert len(fd.calls) == 1 and s["latest"]["status"] == "infeasible" and s["latest"]["names"] is None and not s["latest"]["ungrounded_refusal"]
    assert s["latest"]["analysis"]["grounding"]["items"][0]["verdict"] == "ok"


@needs
@needs_nsdb
def test_chat_invalid_action_is_regenerated_with_the_reason(pg, engine):
    bad1 = '<say>x</say><action>{"action":"design","req":{"country":"Japan","year":1920,"ship_type":"flying_fortress"}}</action>'
    bad2 = '<say>x</say><action>{"action":"design",}</action>'
    eng, fd = engine([bad1, bad2, act(**JP1920)])
    s = eng.handle(None, "1920年の日本の装甲巡洋艦")
    steps = [l["step"] for l in s["display"][-1]["log"]]
    assert steps.count("gemma_action_invalid") == 2 and s["latest"]["status"] == "feasible"
    g = eng.gemma.calls
    assert "flying_fortress" in g[1][-1]["content"] and g[1][-1]["role"] == "user" and "JSON が読めません" in g[2][-1]["content"]
    # 3 回とも不正なら、アクション無しの返答（設計は走らない）
    eng, fd = engine([bad1, bad1, bad1])
    s = eng.handle(None, "1920年の日本の装甲巡洋艦")
    assert s["latest"] is None and not fd.calls and s["display"][-1].get("error")


@needs
@needs_nsdb
def test_chat_question_only_runs_no_design(pg, engine):
    eng, fd = engine(["<say>どの国の、何年ごろの戦艦ですか？</say>"])
    s = eng.handle(None, "戦艦を作りたい")
    assert s["latest"] is None and not fd.calls and s["display"][-1]["text"] == "どの国の、何年ごろの戦艦ですか？"
    # modify が来ても設計が無ければ会話役に直させる
    eng, fd = engine([act(action="modify", changes={"min_speed_kn": 30}), "<say>先に設計しましょう</say>", "<say>先に設計しましょう</say>"])
    s = eng.handle(None, "速力を上げて")
    assert s["latest"] is None and not fd.calls


@needs
@needs_nsdb
def test_chat_explanation_number_mismatch_retries_then_falls_back_to_template(pg, engine):
    seq = []

    def bad(messages):
        seq.append(messages[-1]["content"])
        return "最大速力は 999 kn です。"  # JSON に無い数値

    eng, fd = engine([act(**JP1920)], explain=bad)
    s = eng.handle(None, "1920年の日本の装甲巡洋艦、魚雷付きで")
    m = s["display"][-1]
    assert m["explanation_source"] == "template" and len(seq) == PC.MAX_EXPLAIN_RETRIES + 1 and "999" in seq[1] and "999" not in m["text"]
    checks = [l for l in m["log"] if l["step"] == "explain_check"]
    assert all(c["bad_numbers"] == ["999"] for c in checks)
    # 2 回目で直れば gemma の説明を採る
    calls = []

    def fix(messages):
        calls.append(1)
        if len(calls) == 1:
            return "最大速力は 999 kn です。"
        payload = J(messages[1]["content"].split("設計結果 JSON:\n", 1)[1].rsplit("\n\nこの結果", 1)[0])
        return f"最大速力は {payload['design']['speed_kn']} kn です。"

    eng, fd = engine([act(**JP1920)], explain=fix)
    m = eng.handle(None, "1920年の日本の装甲巡洋艦、魚雷付きで")["display"][-1]
    assert m["explanation_source"] == "gemma" and len(calls) == 2


@needs
def test_chat_fictional_nation_names_by_gemma_and_validation(pg, engine):
    action = act(action="design", req={"country": "Norvane", "year": 1975, "ship_type": "frigate", "required": ["sam"], "max_displacement_t": 4000, "min_speed_kn": 25},
                 premise={"type": "fictional_nation", "like": "France"})
    eng, fd = engine([action], names='{"class_name":"Aurora","ship_names":["Vanguard","Polaris","Glacier"]}')
    s = eng.handle(None, "架空の国ノルヴァーンの1975年の防空フリゲート")
    lat = s["latest"]
    assert lat["names"]["source"] == "gemma" and [x["name"] for x in lat["names"]["ships"]] == ["Vanguard", "Polaris", "Glacier"]
    u0 = J(fd.calls[0]["messages"][1]["content"])
    assert u0["req"]["premise"] == {"type": "fictional_nation", "like": "France"} and u0["request"].startswith("Norvane is a fictional naval power; its shipbuilding and doctrine resemble those of the French navy.")
    # 命名が壊れていたら仮の名前
    eng, fd = engine([action], names="わかりません")
    assert eng.handle(None, "架空国")["latest"]["names"]["source"] == "fallback"


@needs
@needs_nsdb
def test_chat_http_endpoints(pg, engine):
    eng, fd = engine([act(**JP1920)])
    httpd = P.serve(pg, "127.0.0.1", 0, eng)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/chat/meta") as r:
            m = J(r.read())
        assert m["gemma"]["ok"] and m["design_presets"][0]["url"] == pg.llm_url
        code, snap = _post(base + "/api/chat/send", {"text": "1920年の日本の装甲巡洋艦、魚雷付きで", "settings": {"design_url": "http://127.0.0.1:18801", "design_model": "m2"}})
        assert code == 200 and snap["latest"]["status"] == "feasible" and snap["display"][0]["role"] == "user"
        assert fd.calls[0]["url"] == "http://127.0.0.1:18801" and fd.calls[0]["model"] == "m2"  # 設計役を画面から切り替えられる
        json.dumps(snap)
        code, snap2 = _post(base + "/api/chat/reset", {"session_id": snap["session_id"]})
        assert code == 200 and snap2["display"] == [] and snap2["session_id"] != snap["session_id"]
        code, e = _post(base + "/api/chat/send", {"text": "  "})
        assert code == 400
    finally:
        httpd.shutdown()
