#!/usr/bin/env python3
"""design-v1（艦艇設計モデル）をブラウザで試すローカル Web アプリ（playground）。標準ライブラリの http.server だけで動く（新規パッケージ無し）。

    .venv/bin/python tools/shipknow_playground.py --port 8765 --llm-url http://127.0.0.1:18800
    # -> http://127.0.0.1:8765/

推論サーバは OpenAI 互換の ``/v1/chat/completions``（llama.cpp llama-server など）。このアプリは推論サーバ以外へ通信しない。127.0.0.1 だけで待ち受ける。

役割:
  * フォーム -> 構造化要求 req -> 要求文 request（英語。日本語訳を併記）-> 近傍艦 nbr（規則 NB-1、train の実艦）-> user メッセージ。
    user / system の書式は学習データ（data/navspec/shipknow_design_v1）と同じ。生成コード（shipknow_design_v1_core / _gen_basic / _repair / _ood）の関数を再利用する。
  * 続きの会話: modify（「Now ...」形式 + 更新後の全 req）、repair（validator の違反 + 修正指示）、context（別の新しい要求）。
  * 返ってきた design の検査（check_design: 要求充足・validator・整合性）と、conflicts の主張の実データ照合
    （同艦種・要求年 + 3 年以内の実艦で、その制約を外した要求を満たすものの最良値、能力の初出年、艦種の初出年など。要求を全部満たす実艦が存在するかも）。

API（JSON）: GET /api/meta, /api/health  POST /api/build_request, /api/preview_modify, /api/turn, /api/analyze
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_design_common as C  # noqa: E402
import shipknow_design_eval_req as X  # noqa: E402
import shipknow_design_v1_core as K  # noqa: E402
import shipknow_design_v1_data as DD  # noqa: E402
import shipknow_design_v1_eval as EV  # noqa: E402
import shipknow_design_v1_gen_basic as G  # noqa: E402
import shipknow_design_v1_infeasible as INF  # noqa: E402
import shipknow_design_v1_ood as OD  # noqa: E402
import shipknow_design_v1_repair as RP  # noqa: E402

HTML_PATH = Path(__file__).with_name("shipknow_playground.html")
DEFAULT_LLM_URL = "http://127.0.0.1:18800"
DEFAULT_MODEL = "design_v1_step1200"
YEAR_AHEAD = INF.YEAR_AHEAD  # 実艦照合は要求年 + 3 年以内に設計された艦（学習データの oracle と同じ）

# 必須装備 token の標準順（C.candidate_tokens と同じ順。main_gun は先頭）
REQUIRED_ORDER = ["main_gun", "torpedo_tubes", "asw_weapon", "sonar", "helicopter", "aircraft", "ssm", "sam", "ciws", "rocket_launcher", "ballistic_missile", "air_search_radar",
                  "fire_control_radar", "surface_radar", "radar", "esm_ecm", "mines", "minesweeping"]
TECH_CHOICES = ["radar", "sonar", "helicopter", "ssm", "sam", "ciws", "esm_ecm", "gas_turbine", "nuclear"]
DOCTRINE_NEED = {"torpedo_centric": "torpedo tubes first", "gun_centric": "heavy gun batteries first", "missile_centric": "missiles first",
                 "air_centric": "embarked aircraft first", "asw_centric": "anti-submarine equipment first"}
DOCTRINE_JA = {"torpedo_centric": "魚雷重視", "gun_centric": "砲重視", "missile_centric": "ミサイル重視", "air_centric": "航空重視", "asw_centric": "対潜重視"}
TECH_JA = {"radar": "レーダー", "sonar": "アクティブソナー", "helicopter": "艦載ヘリ", "ssm": "対艦ミサイル", "sam": "対空ミサイル", "ciws": "近接防御火器", "esm_ecm": "ESM/ECM",
           "gas_turbine": "舶用ガスタービン", "nuclear": "原子力推進"}
STATUS_JA = {"feasible": "実現可能", "relaxed": "緩和して実現", "infeasible": "実現不可能"}
BASIS_JA = {"normal": "常備", "full": "満載", "standard": "基準"}
# modify の文型（G._sentence と同じ 8 通り。(書式, 先頭を大文字にするか)）
MODIFY_FORMS = [("Now {}.", False), ("Change of plan: {}.", True), ("Revise the design: {}. Keep everything else you can.", True), ("Update: {}.", True),
                ("Same project, but {}.", False), ("Please revise it: {}.", False), ("Good. Next iteration: {}.", True), ("Now {}; keep the rest of the design as it is where possible.", False)]
NUM_FIELDS = ["max_displacement_t", "min_displacement_t", "min_speed_kn", "max_speed_kn", "max_complement", "min_endurance_nm", "min_gun_barrels", "min_gun_barrels_cal_mm",
              "min_aircraft", "min_torpedo_tubes"]
MODIFY_FIELDS = NUM_FIELDS + ["main_gun_mm", "add_required", "remove_required", "required_propulsion", "country", "year", "displacement_basis"]


# --------------------------------------------------------------------------------------------------
# form -> req
# --------------------------------------------------------------------------------------------------
def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v)


def _to_num(v: Any, key: str) -> int | float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: 数値として読めません: {v!r}") from None
    if not math.isfinite(x):
        raise ValueError(f"{key}: 有限の数値ではありません: {v!r}")
    return int(round(x)) if key in K.REQ_INT_KEYS else K.num(x)


def _gun_token(mm: Any) -> str:
    x = _to_num(mm, "main_gun_mm")
    return f"main_gun>={x:g}mm"


def normalize_req(req: dict[str, Any]) -> dict[str, Any]:
    """req の数値を学習データと同じ表記（整数は整数、小数は 1 桁）にする。キー順は変えない
    （学習データの modify の 2 ターン目以降は、追加された制約のキーが末尾に付く。最初の要求は form_to_req が標準順にする）。"""
    out = copy.deepcopy(req)
    for k in K.REQ_NUM_KEYS:
        if out.get(k) is not None:
            out[k] = _to_num(out[k], k)
    return out


def _hyphen(country: str) -> str:
    return re.sub(r"\s+", "-", country.strip())


def build_premise(p: dict[str, Any] | None, country: str, variant: int = 0) -> tuple[dict[str, Any] | None, str | None]:
    """前提フォーム -> (req.premise, 要求文の先頭に付ける前提文)。書式は shipknow_design_v1_ood と同じ。"""
    if not p or _blank(p.get("type")):
        return None, None
    t = p["type"]
    v = variant % 2
    if t == "fictional_nation":
        like = _hyphen(p.get("like") or "")
        if not like:
            raise ValueError("架空国家の前提には「似ている国」が必要です")
        name = country.replace("-", " ")
        forms = [f"{name} is a fictional naval power; its shipbuilding and doctrine resemble those of {OD._adj(like)}.",
                 f"Assume {name}, an invented maritime state, whose navy follows {OD._adj(like)}'s practice and industrial level."]
        return {"type": "fictional_nation", "like": like}, forms[v]
    if t == "tech_early":
        tech = p.get("tech")
        if tech not in OD.TECH_PHRASE:
            raise ValueError(f"技術前倒しの技術が不明です: {tech!r}")
        y = _to_num(p.get("available_from"), "year")
        phrase = OD.TECH_PHRASE[tech]
        forms = [f"Assume {phrase} entered service in {y}; nothing else changes.", f"In this world {phrase} became practical already in {y}. All other technology follows the real timeline."]
        return {"type": "tech_early", "tech": tech, "available_from": y}, forms[v]
    if t == "war_outcome":
        nation = _hyphen(p.get("nation") or "")
        if not nation:
            raise ValueError("戦争結果変更の前提には国が必要です")
        like = _hyphen(p.get("like") or "") or None
        txt = OD.WAR_TEXT.get(nation, [f"Assume {nation.replace('-', ' ')} won the war and kept its navy.", f"Assume {nation.replace('-', ' ')} ended the war with its navy intact."])[v]
        if like:
            txt += f" Its designers follow {OD._adj(like)}'s postwar practice."
        return {"type": "war_outcome", "nation": nation, "like": like}, txt
    if t == "doctrine":
        doc = p.get("doctrine")
        if doc not in OD.DOCTRINES:
            raise ValueError(f"ドクトリンが不明です: {doc!r}")
        return {"type": "doctrine", "doctrine": doc}, f"Assume the navy follows {OD.DOCTRINES[doc]} doctrine: it spends its displacement on {DOCTRINE_NEED[doc]}; nothing else about the technology changes."
    raise ValueError(f"前提の種類が不明です: {t!r}")


def form_to_req(f: dict[str, Any], variant: int = 0) -> tuple[dict[str, Any], str | None]:
    """フォーム -> (req, 前提文)。req のキー順・数値表記は学習データと同じ。"""
    if _blank(f.get("country")):
        raise ValueError("国が未入力です")
    if _blank(f.get("year")):
        raise ValueError("年が未入力です")
    if _blank(f.get("ship_type")):
        raise ValueError("艦種が未選択です")
    req: dict[str, Any] = {"country": _hyphen(str(f["country"])), "year": _to_num(f["year"], "year"), "ship_type": f["ship_type"]}
    if not _blank(f.get("ship_type_fine")):
        req["ship_type_fine"] = f["ship_type_fine"]
    if not _blank(f.get("mission")):
        req["mission"] = list(f["mission"])
    for k in NUM_FIELDS:
        if not _blank(f.get(k)):
            req[k] = _to_num(f[k], k)
    if not _blank(f.get("displacement_basis")):
        req["displacement_basis"] = f["displacement_basis"]
    elif "max_displacement_t" in req or "min_displacement_t" in req:
        req["displacement_basis"] = "normal"
    toks = [t for t in (f.get("required") or [])]
    if not _blank(f.get("main_gun_mm")):
        toks = [_gun_token(f["main_gun_mm"])] + [t for t in toks if not t.startswith("main_gun")]
    req["required"] = toks  # 学習データの req は required を常に持つ（装備の指定が無ければ空配列）
    if not _blank(f.get("required_propulsion")):
        props = f["required_propulsion"]
        req["required_propulsion"] = [props] if isinstance(props, str) else list(props)
    if req.get("min_gun_barrels_cal_mm") is not None and req.get("min_gun_barrels") is None:
        req.pop("min_gun_barrels_cal_mm")
    premise, premise_text = build_premise(f.get("premise"), req["country"], variant)
    if premise:
        req["premise"] = premise
    return K.order_req(req), premise_text


def req_to_form(req: dict[str, Any]) -> dict[str, Any]:
    """form_to_req の逆（テストとプリセット用）。先頭の main_gun token だけ main_gun_mm に出す。"""
    f: dict[str, Any] = {k: req[k] for k in ("country", "year", "ship_type", "ship_type_fine", "mission", "displacement_basis", *NUM_FIELDS, "required_propulsion") if k in req}
    toks = list(req.get("required") or [])
    if toks and toks[0].startswith("main_gun"):
        f["main_gun_mm"] = float(toks[0][len("main_gun>="):-2])
        toks = toks[1:]
    f["required"] = toks
    p = req.get("premise")
    if p:
        f["premise"] = dict(p)
    return f


# --------------------------------------------------------------------------------------------------
# 日本語訳（表示用。モデルには送らない）
# --------------------------------------------------------------------------------------------------
def _country_ja(c: str) -> str:
    return C._COUNTRY_JA.get(c, c.replace("-", " "))


def request_ja(req: dict[str, Any]) -> str:
    t = C._TYPE_JA.get(req.get("ship_type"), req.get("ship_type", "?"))
    if req.get("ship_type_fine"):
        t += f"（{req['ship_type_fine']}）"
    basis = BASIS_JA.get(req.get("displacement_basis", "normal"), "")
    lim: list[str] = []
    mx, mn = req.get("max_displacement_t"), req.get("min_displacement_t")
    if mx is not None and mn is not None:
        lim.append(f"{basis}排水量 {mn}〜{mx} t" if mn <= mx else f"{basis}排水量 {mn} t 以上かつ {mx} t 以下")
    elif mx is not None:
        lim.append(f"{basis}排水量 {mx} t 以下")
    elif mn is not None:
        lim.append(f"{basis}排水量 {mn} t 以上")
    lo, hi = req.get("min_speed_kn"), req.get("max_speed_kn")
    if lo is not None and hi is not None:
        lim.append(f"速力 {lo}〜{hi} kn" if lo <= hi else f"速力 {lo} kn 以上かつ {hi} kn 以下")
    elif lo is not None:
        lim.append(f"速力 {lo} kn 以上")
    elif hi is not None:
        lim.append(f"速力 {hi} kn 以下")
    if req.get("max_complement"):
        lim.append(f"乗員 {req['max_complement']} 名以下")
    if req.get("min_endurance_nm"):
        lim.append(f"航続 {req['min_endurance_nm']} nm 以上")
    if req.get("required_propulsion"):
        lim.append("推進: " + "・".join(p.replace("_", " ") for p in req["required_propulsion"]))
    need = [C._req_item_ja(x) for x in req.get("required") or []]
    if req.get("min_gun_barrels") is not None:
        cal = req.get("min_gun_barrels_cal_mm")
        need.append(f"砲門数 {req['min_gun_barrels']} 以上" + (f"（口径 {cal:g} mm 以上）" if cal else ""))
    if req.get("min_aircraft") is not None:
        need.append(f"搭載機 {req['min_aircraft']} 機以上")
    if req.get("min_torpedo_tubes") is not None:
        need.append(f"魚雷発射管 {req['min_torpedo_tubes']} 門以上")
    mission = "・".join(C._MISSION_JA.get(m, m) for m in req.get("mission") or []) or "一般任務"
    s = f"{req.get('year')} 年ごろの{_country_ja(req.get('country', '?'))}海軍の{t}を設計。任務: {mission}。" + ("、".join(lim) + "。" if lim else "") + (f"必須装備: {'、'.join(need)}。" if need else "")
    p = req.get("premise")
    if p:
        ty = p.get("type")
        if ty == "fictional_nation":
            s = f"【前提】{req.get('country')} は架空国家で、造船・ドクトリンは {_country_ja(p.get('like', '?'))} に似る。 " + s
        elif ty == "tech_early":
            s = f"【前提】{TECH_JA.get(p.get('tech'), p.get('tech'))}は {p.get('available_from')} 年に実用化（他の技術は史実どおり）。 " + s
        elif ty == "war_outcome":
            s = f"【前提】{_country_ja(p.get('nation', '?'))}が戦勝国となり海軍を維持" + (f"（設計は {_country_ja(p['like'])} の戦後の流儀）" if p.get("like") else "") + "。 " + s
        elif ty == "doctrine":
            s = f"【前提】ドクトリンは{DOCTRINE_JA.get(p.get('doctrine'), p.get('doctrine'))}。 " + s
    return s


# --------------------------------------------------------------------------------------------------
# modify（変更 -> 新しい req と変更文）
# --------------------------------------------------------------------------------------------------
def apply_changes(req: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    """changes = [{"field", "value"}]。値が空なら制約を外す（max_displacement_t は外せない）。戻り値は更新後の全 req。
    既存のキーは位置を保ち、新しい制約のキーは末尾に付く（学習データの modify と同じ）。"""
    new = copy.deepcopy(req)
    for ch in changes:
        f, v = ch.get("field"), ch.get("value")
        blank = _blank(v)
        toks = [t for t in new.get("required") or []]
        if f in NUM_FIELDS:
            if blank:
                if f == "max_displacement_t":
                    raise ValueError("排水量上限は外せません（学習データでは常に指定されます）")
                new.pop(f, None)
                if f == "min_gun_barrels":
                    new.pop("min_gun_barrels_cal_mm", None)
            else:
                new[f] = _to_num(v, f)
        elif f == "main_gun_mm":
            toks = [t for t in toks if not t.startswith("main_gun")]
            if not blank:
                toks.append(_gun_token(v))
            new["required"] = toks
        elif f == "add_required":
            if blank:
                raise ValueError("追加する装備が未選択です")
            if v not in toks:
                toks.append(v)
            new["required"] = toks
        elif f == "remove_required":
            new["required"] = [t for t in toks if t != v and not (v == "main_gun" and t.startswith("main_gun"))]
        elif f == "required_propulsion":
            if blank:
                new.pop("required_propulsion", None)
            else:
                new["required_propulsion"] = [v] if isinstance(v, str) else list(v)
        elif f == "country":
            if blank:
                raise ValueError("国が空です")
            new["country"] = _hyphen(str(v))
        elif f == "year":
            new["year"] = _to_num(v, "year")
        elif f == "displacement_basis":
            if v not in BASIS_JA:
                raise ValueError(f"排水量の基準が不明です: {v!r}")
            new["displacement_basis"] = v
        else:
            raise ValueError(f"変更できない項目です: {f!r}")
    return new


def diff_changes(prev: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """prev -> new を apply_changes の changes で表す（テストと「現在の要求との差分」用）。"""
    out: list[dict[str, Any]] = []
    for k in ("country", "year", "displacement_basis"):
        if prev.get(k) != new.get(k):
            out.append({"field": k, "value": new.get(k)})
    for k in NUM_FIELDS:
        if prev.get(k) != new.get(k):
            out.append({"field": k, "value": new.get(k)})
    pt, nt = list(prev.get("required") or []), list(new.get("required") or [])
    pg = next((t for t in pt if t.startswith("main_gun")), None)
    ng = next((t for t in nt if t.startswith("main_gun")), None)
    if pg != ng:
        out.append({"field": "main_gun_mm", "value": float(ng[len("main_gun>="):-2]) if ng else None})
    for t in nt:
        if t not in pt and not t.startswith("main_gun"):
            out.append({"field": "add_required", "value": t})
    for t in pt:
        if t not in nt and not t.startswith("main_gun"):
            out.append({"field": "remove_required", "value": t})
    if prev.get("required_propulsion") != new.get("required_propulsion"):
        out.append({"field": "required_propulsion", "value": new.get("required_propulsion")})
    return out


_ADD_EN = {"sam": "add surface-to-air missiles", "ssm": "add anti-ship missiles", "ciws": "add a close-in weapon system", "helicopter": "add a helicopter"}


def change_pieces(prev: dict[str, Any], new: dict[str, Any]) -> tuple[list[str], list[str]]:
    """prev -> new の変更を (英語の句, 日本語の句) にする。句の文言は学習データ（shipknow_design_v1_gen_basic の変更ハンドラ）と同じ。"""
    en: list[str] = []
    ja: list[str] = []

    def add(e: str, j: str) -> None:
        en.append(e)
        ja.append(j)

    basis = new.get("displacement_basis", "normal")
    bj = BASIS_JA.get(basis, "")
    if prev.get("country") != new.get("country"):
        nc = new["country"].replace("-", " ")
        nat = C._NATION_ADJ.get(nc)
        add(f"design it for the {nat} navy instead" if nat else f"design it for the navy of {nc} instead", f"{_country_ja(new['country'])}海軍向けに変更")
    if prev.get("year") != new.get("year"):
        add(f"move the design date to {new['year']}", f"設計年を {new['year']} 年に変更")
    if prev.get("displacement_basis") != new.get("displacement_basis") and (prev.get("displacement_basis") or "normal") != basis:
        add(f"measure displacement on the {basis} basis", f"排水量の基準を{bj}に変更")
    a, b = prev.get("max_displacement_t"), new.get("max_displacement_t")
    if a != b and b is not None:
        verb = "raise" if (a is None or b > a) else "cut"
        add(f"{verb} the displacement limit to {b} t ({basis})", f"排水量上限を {b} t（{bj}）に{'引き上げ' if verb == 'raise' else '引き下げ'}")
    a, b = prev.get("min_displacement_t"), new.get("min_displacement_t")
    if a != b:
        add(f"set the minimum displacement to {b} t ({basis})" if b is not None else "drop the minimum displacement", f"排水量下限を {b} t に設定" if b is not None else "排水量下限を外す")
    a, b = prev.get("min_speed_kn"), new.get("min_speed_kn")
    if a != b:
        if b is None:
            add("drop the minimum speed requirement", "最低速力の要求を外す")
        else:
            up = a is None or b > a
            add(f"raise the minimum speed to {b} kn" if up else f"lower the minimum speed to {b} kn to save power", f"最低速力を {b} kn に{'引き上げ' if up else '引き下げ'}")
    a, b = prev.get("max_speed_kn"), new.get("max_speed_kn")
    if a != b:
        add(f"limit the maximum speed to {b} kn" if b is not None else "drop the maximum speed limit", f"最高速力を {b} kn 以下に制限" if b is not None else "最高速力の上限を外す")
    a, b = prev.get("max_complement"), new.get("max_complement")
    if a != b:
        add(f"{'cut' if (a is None or (b is not None and b < a)) else 'raise'} the crew limit to {b}" if b is not None else "drop the crew limit",
            f"乗員上限を {b} 名に変更" if b is not None else "乗員上限を外す")
    a, b = prev.get("min_endurance_nm"), new.get("min_endurance_nm")
    if a != b:
        add(f"require at least {b} nm of endurance" if b is not None else "drop the endurance requirement", f"航続 {b} nm 以上を要求" if b is not None else "航続の要求を外す")
    pg = next((t for t in prev.get("required") or [] if t.startswith("main_gun")), None)
    ng = next((t for t in new.get("required") or [] if t.startswith("main_gun")), None)
    if pg != ng:
        if ng:
            mm = ng[len("main_gun>="):-2]
            add(f"require main guns of at least {mm} mm", f"主砲口径 {mm} mm 以上を要求")
        else:
            add("drop the main gun requirement", "主砲の要求を外す")
    a, b, ca, cb = prev.get("min_gun_barrels"), new.get("min_gun_barrels"), prev.get("min_gun_barrels_cal_mm"), new.get("min_gun_barrels_cal_mm")
    if (a, ca) != (b, cb):
        if b is not None:
            add(f"carry at least {b} gun barrels" + (f" of {cb:g} mm or larger" if cb else ""), f"砲門数 {b} 以上" + (f"（口径 {cb:g} mm 以上）" if cb else "") + "を要求")
        else:
            add("drop the gun barrel requirement", "砲門数の要求を外す")
    a, b = prev.get("min_aircraft"), new.get("min_aircraft")
    if a != b:
        add(f"embark at least {b} aircraft" if b is not None else "drop the embarked aircraft requirement", f"搭載機 {b} 機以上を要求" if b is not None else "搭載機の要求を外す")
    a, b = prev.get("min_torpedo_tubes"), new.get("min_torpedo_tubes")
    if a != b:
        add(f"fit at least {b} torpedo tubes" if b is not None else "drop the torpedo tube requirement", f"魚雷発射管 {b} 門以上を要求" if b is not None else "魚雷発射管の要求を外す")
    pt, nt = list(prev.get("required") or []), list(new.get("required") or [])
    for t in nt:
        if t not in pt and not t.startswith("main_gun"):
            add(_ADD_EN.get(t, f"add {C._TOKEN_EN.get(t, t)}"), f"{C._TOKEN_JA.get(t, t)}を追加")
    for t in pt:
        if t not in nt and not t.startswith("main_gun"):
            add(f"drop the {C._TOKEN_EN.get(t, t).removeprefix('a ').removeprefix('an ')} requirement", f"{C._TOKEN_JA.get(t, t)}の要求を外す")
    if prev.get("required_propulsion") != new.get("required_propulsion"):
        ps = new.get("required_propulsion")
        add("use " + " and ".join(p.replace("_", " ") for p in ps) + " propulsion" if ps else "drop the propulsion requirement",
            "推進方式: " + "・".join(ps) if ps else "推進方式の指定を外す")
    return en, ja


def modify_sentence(pieces: list[str], variant: int = 0) -> str:
    """句を 1 つの変更要求文にする（G._sentence と同じ文型。variant で選ぶ）。"""
    body = pieces[0] if len(pieces) == 1 else ", ".join(pieces[:-1]) + " and " + pieces[-1]
    form, cap = MODIFY_FORMS[variant % len(MODIFY_FORMS)]
    body = body[0].upper() + body[1:] if cap else body[0].lower() + body[1:]
    return form.format(body)


# --------------------------------------------------------------------------------------------------
# user メッセージ（学習データと同じ書式）
# --------------------------------------------------------------------------------------------------
def user_message(request: str, req: dict[str, Any], nbr: list[dict[str, Any]] | None = None, reference: dict[str, Any] | None = None) -> str:
    """{"request","req",["reference"],["nbr"]} の compact JSON 文字列（G.user_turn と同じ）。"""
    return G.user_turn(request, normalize_req(req), nbr or None, reference)


def repair_message(violations: list[dict[str, str]]) -> str:
    """repair の feedback ターン（{"validator":{"valid":false,"violations":[...]},"instruction":...}）。"""
    return RP.feedback_turn(violations)


def new_conversation() -> list[dict[str, str]]:
    return [G.msg("system", K.SYSTEM_V1)]


def compose_turn(turn: dict[str, Any]) -> str:
    kind = turn.get("kind")
    if kind == "repair":
        v = turn.get("validator")
        if not isinstance(v, list) or not v:
            raise ValueError("repair には validator の違反（violations）が必要です")
        return repair_message(v)
    if kind in ("request", "modify", "context"):
        if not isinstance(turn.get("req"), dict) or _blank(turn.get("request")):
            raise ValueError("request と req が必要です")
        return user_message(turn["request"], turn["req"], turn.get("nbr"), turn.get("reference"))
    raise ValueError(f"turn.kind が不明です: {kind!r}")


# --------------------------------------------------------------------------------------------------
# 実データ照合・検査
# --------------------------------------------------------------------------------------------------
def _fmt(x: Any) -> str:
    return f"{x:g}" if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x)


class Playground:
    def __init__(self, llm_url: str = DEFAULT_LLM_URL, model: str = DEFAULT_MODEL, data: DD.Data | None = None, timeout: float = 300.0, log=print):
        t0 = time.time()
        self.llm_url, self.model, self.timeout = llm_url.rstrip("/"), model, timeout
        self.D = data or DD.Data()
        recs = [r for s in K.SPLITS for r in self.D.by_split[s]]
        self.idx_all = K.DesignIndex(recs, self.D.designs)
        self.idx_train = self.D.idx_train
        self._cap_cache: dict[str, list[tuple[int, int]]] | None = None
        self._lock = threading.Lock()
        log(f"playground data ready in {time.time() - t0:.1f}s: real ships {len(self.idx_all)} (train {len(self.idx_train)})")

    # ---------------- meta
    def meta(self) -> dict[str, Any]:
        cnt: dict[str, int] = {}
        for i in range(len(self.idx_all)):
            c = self.idx_all.recs[i]["country"]
            cnt[c] = cnt.get(c, 0) + 1
        countries = [{"name": c, "n": n} for c, n in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))]
        fine: dict[str, dict[str, int]] = {}
        for d in self.idx_all.designs:
            t = d["ship_type"]
            ft = d.get("ship_type_fine")
            if ft:
                fine.setdefault(t, {})
                fine[t][ft] = fine[t].get(ft, 0) + 1
        types = sorted({d["ship_type"] for d in self.idx_all.designs} & set(C._TYPE_NAME))
        years = [int(y) for y in self.idx_all.year]
        return {
            "system": K.SYSTEM_V1, "llm_url": self.llm_url, "model": self.model, "countries": countries,
            "ship_types": [{"id": t, "ja": C._TYPE_JA.get(t, t), "en": C._TYPE_NAME.get(t, t), "fine": [f for f, _ in sorted(fine.get(t, {}).items(), key=lambda kv: -kv[1])]} for t in types],
            "missions": [{"id": m, "ja": C._MISSION_JA.get(m, m)} for m in C.MISSION_TAGS],
            "required": [{"id": t, "ja": C._TOKEN_JA.get(t, t)} for t in REQUIRED_ORDER if t != "main_gun"],
            "propulsion": K.PROPULSION_KINDS, "techs": [{"id": t, "ja": TECH_JA[t]} for t in TECH_CHOICES],
            "doctrines": [{"id": d, "ja": DOCTRINE_JA[d]} for d in OD.DOCTRINES], "war_nations": list(OD.WAR_NATIONS),
            "year_range": [min(years), max(years)], "modify_fields": MODIFY_FIELDS, "presets": PRESETS,
            "n_ships": len(self.idx_all), "n_train": len(self.idx_train),
        }

    # ---------------- request building
    def neighbors(self, req: dict[str, Any]) -> list[dict[str, Any]]:
        """規則 NB-1（train の実艦、k=3）。エピソード元の艦は無い（source_ids=[]）ので除外する艦級は無い。"""
        try:
            with self._lock:
                return self.D.neighbors(normalize_req(req), [])
        except Exception:  # noqa: BLE001 - 検索の失敗は nbr 無しで続ける
            return []

    def warnings_for(self, req: dict[str, Any]) -> list[str]:
        w: list[str] = []
        known = {r["country"] for r in self.idx_all.recs}
        if req.get("country") not in known and (req.get("premise") or {}).get("type") != "fictional_nation":
            w.append(f"国 {req.get('country')!r} は実艦データにありません。架空国家なら前提「架空国家」を付けてください（学習データはその形）。")
        y = req.get("year")
        if isinstance(y, (int, float)) and not (1860 <= y <= 2030):
            w.append(f"年 {y} は学習データの範囲（1860〜）外です。")
        if req.get("max_displacement_t") is None:
            w.append("排水量上限が無い要求は学習データにありません（常に指定されています）。")
        if req.get("min_speed_kn") is None:
            w.append("最低速力が無い要求は学習データにほぼありません。")
        ft = req.get("ship_type_fine")
        if ft:
            fines = {d.get("ship_type_fine") for d in self.idx_all.designs if d["ship_type"] == req.get("ship_type")}
            if ft not in fines:
                w.append(f"細分類 {ft!r} は艦種 {req.get('ship_type')} の実艦にありません。")
        return w

    def build_request(self, form: dict[str, Any], variant: int = 0, with_nbr: bool = True) -> dict[str, Any]:
        req, premise_text = form_to_req(form, variant)
        text = K.request_text_v1(req, variant, premise_text=premise_text)
        return {"req": req, "request": text, "request_ja": request_ja(req), "premise_text": premise_text, "variant": variant % 6,
                "nbr": self.neighbors(req) if with_nbr else [], "warnings": self.warnings_for(req)}

    def preview_modify(self, prev_req: dict[str, Any], changes: list[dict[str, Any]], variant: int = 0) -> dict[str, Any]:
        prev = normalize_req(prev_req)
        new = apply_changes(prev, changes)
        en, ja = change_pieces(prev, new)
        if not en:
            raise ValueError("変更がありません（現在の要求と同じです）")
        nbr = self.neighbors(new) if (prev.get("country") != new.get("country") or prev.get("year") != new.get("year")) else []
        return {"req": new, "request": modify_sentence(en, variant), "request_ja": "変更: " + "、".join(ja), "pieces": en, "nbr": nbr, "variant": variant % len(MODIFY_FORMS),
                "full_request_ja": request_ja(new), "warnings": self.warnings_for(new)}

    # ---------------- 実艦の集計
    def cap_years(self) -> dict[str, list[tuple[int, int]]]:
        """能力語彙 cap -> [(実艦の年, 艦の index)]（年の昇順）。"""
        if self._cap_cache is None:
            m: dict[str, list[tuple[int, int]]] = {}
            for i, d in enumerate(self.idx_all.designs):
                y = int(self.idx_all.year[i])
                for cap in C.caps_of_design(d):
                    m.setdefault(cap, []).append((y, i))
            self._cap_cache = {k: sorted(v) for k, v in m.items()}
        return self._cap_cache

    def ship_label(self, i: int, idx: K.DesignIndex | None = None) -> dict[str, Any]:
        idx = idx or self.idx_all
        d = idx.designs[i]
        rec = idx.recs[i]
        disp = {b: d.get(f"displacement_{b}_t") for b in ("normal", "full", "standard")}
        return {"id": rec["id"], "split": self.D.assign.get(rec["id"]), "country": rec["country"], "year": int(idx.year[i]), "type": d.get("ship_type"), "fine": d.get("ship_type_fine"),
                "disp": {k: v for k, v in disp.items() if v is not None}, "speed": d.get("speed_kn"), "crew": d.get("complement"),
                "barrels": X.gun_barrels(d), "aircraft": X.aircraft_count(d), "tubes": X.torpedo_tubes(d)}

    def real_satisfying(self, req: dict[str, Any]) -> dict[str, Any]:
        y = req.get("year")
        yh = (y + YEAR_AHEAD) if isinstance(y, (int, float)) else None
        out: dict[str, Any] = {"year_hi": yh}
        try:
            ia = self.idx_all.satisfying(req, year_hi=yh)
            it = self.idx_train.satisfying(req, year_hi=yh)
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}", "n_all": None, "n_train": None, "examples": []}
        ia.sort(key=lambda i: (self.D.assign.get(self.idx_all.ids[i]) != "train", abs(self.idx_all.year[i] - (y or 0)), i))  # train の艦を先に、年が近い順
        out.update({"n_all": len(ia), "n_train": len(it), "examples": [self.ship_label(i) for i in ia[:5]]})
        return out

    def _best(self, idx: K.DesignIndex, req: dict[str, Any], cid: str, fn, pick) -> tuple[float | None, int | None, int]:
        r0 = K.drop_constraint(req, cid)
        cands = idx.satisfying(r0, year_hi=req["year"] + YEAR_AHEAD)
        vals = [(fn(idx.designs[i]), i) for i in cands]
        vals = [(v, i) for v, i in vals if v is not None]
        if not vals:
            return None, None, len(cands)
        b = pick(vals, key=lambda t: t[0])
        return float(b[0]), b[1], len(cands)

    # ---------------- conflicts の照合
    def ground_conflict(self, req: dict[str, Any], c: dict[str, Any], n_full: int | None) -> dict[str, Any]:
        cid = str(c.get("constraint", "")).strip()
        reason = str(c.get("reason", ""))
        it: dict[str, Any] = {"constraint": cid, "reason": reason, "verdict": "unverified", "detail": "", "claimed": None, "actual": None, "examples": []}
        year = req.get("year")
        if not isinstance(year, (int, float)):
            it["detail"] = "要求に年が無く照合できません"
            return it
        try:
            self._ground(req, cid, reason, it, n_full)
        except Exception as e:  # noqa: BLE001
            it["verdict"], it["detail"] = "unverified", f"照合中の例外: {type(e).__name__}: {e}"
        return it

    def _ground(self, req: dict[str, Any], cid: str, reason: str, it: dict[str, Any], n_full: int | None) -> None:
        year = req["year"]
        ids = K.constraint_ids(req)
        tname = C._TYPE_NAME.get(req.get("ship_type"), req.get("ship_type"))

        def done(verdict: str, detail: str) -> None:
            it["verdict"], it["detail"] = verdict, detail

        # ---- 矛盾（min と max）
        if cid == "min_displacement_t + max_displacement_t":
            mn, mx = req.get("min_displacement_t"), req.get("max_displacement_t")
            ok = mn is not None and mx is not None and mn > mx
            return done("ok" if ok else "mismatch", f"要求は 下限 {mn} t / 上限 {mx} t" + ("（確かに矛盾）" if ok else "（矛盾していない）"))
        if cid == "min_speed_kn + max_speed_kn":
            lo, hi = req.get("min_speed_kn"), req.get("max_speed_kn")
            ok = lo is not None and hi is not None and lo > hi
            return done("ok" if ok else "mismatch", f"要求は 下限 {lo} kn / 上限 {hi} kn" + ("（確かに矛盾）" if ok else "（矛盾していない）"))
        # ---- 艦種そのものが無い年
        if cid in ("ship_type", "ship_type_fine"):
            t = req.get("ship_type")
            hi = year + YEAR_AHEAD
            m_all = (self.idx_all.type == t) & (self.idx_all.year <= hi)
            n_tr = int(((self.idx_train.type == t) & (self.idx_train.year <= hi)).sum())
            ys = sorted(int(y) for y in self.idx_all.year[self.idx_all.type == t])
            claimed = [int(m) for m in re.findall(r"around (\d{4})", reason)]
            it["claimed"], it["actual"] = (claimed[0] if claimed else None), {"earliest_real": ys[0] if ys else None, "train_3rd_group": self.D.type_first_year.get(t), "n_train_up_to_year": n_tr}
            if n_tr >= 3:
                it["examples"] = [self.ship_label(i) for i in np.where(m_all)[0][:3]]
                return done("mismatch", f"{hi} 年までに設計された {tname} の train の実艦が {n_tr} 隻ある（最古 {ys[0]} 年）")
            ref = {self.D.type_first_year.get(t), ys[0] if ys else None} - {None}
            if claimed and ref and min(abs(claimed[0] - r) for r in ref) > 2:
                return done("mismatch", f"主張した初出年 {claimed[0]} は実データ（最古 {ys[0] if ys else '?'}、train の 3 番目の艦級 {self.D.type_first_year.get(t)}）と合わない")
            if m_all.any():
                it["examples"] = [self.ship_label(i) for i in np.where(m_all)[0][:3]]
                return done("weak", f"{hi} 年までの {tname} の実艦が {int(m_all.sum())} 隻だけある（最古 {ys[0]} 年。試作艦・単独艦のみで、艦級として現れるのは {self.D.type_first_year.get(t) or '?'} 年ごろ）")
            return done("ok", f"{hi} 年までに {tname} の実艦は無い（最古 {ys[0] if ys else '?'} 年）" + (f"。主張した初出年 {claimed[0]} と整合" if claimed else ""))
        # ---- 艦種の排水量帯（type_size）
        if cid == "max_displacement_t":
            basis = req.get("displacement_basis", "normal")
            sizes = [v for v in self.idx_all.disp(basis)[self.idx_all.type == req.get("ship_type")] if v == v]
            tr = [v for v in self.idx_train.disp(basis)[self.idx_train.type == req.get("ship_type")] if v == v]
            if not sizes:
                return done("unverified", f"{tname} の {basis} 排水量が実艦に無い")
            lo_all, lo_tr = min(sizes), (min(tr) if tr else min(sizes))
            m = re.search(r"displaced about (\d+)", reason)
            it["claimed"], it["actual"] = (int(m.group(1)) if m else None), {"min_all": lo_all, "min_train": lo_tr}
            lim = req.get("max_displacement_t")
            if m and not (min(lo_all, lo_tr) * 0.95 - 1 <= int(m.group(1)) <= max(lo_all, lo_tr) * 1.05 + 1):
                return done("mismatch", f"主張した最小 {m.group(1)} t は実データの最小 {lo_all:g} t（train {lo_tr:g} t）と合わない")
            if lim is not None and lim >= lo_tr:
                return done("mismatch", f"上限 {lim} t 以上の {tname} が train にある（最小 {lo_tr:g} t）")
            if lim is not None and lim >= lo_all:
                return done("weak", f"上限 {lim} t 以上の {tname} が valid/test の実艦にある（最小 {lo_all:g} t。train の最小は {lo_tr:g} t）")
            return done("ok", f"{tname} の実艦の最小は {lo_all:g} t（train {lo_tr:g} t）で、上限 {lim} t はそれ未満")
        # ---- 数値制約
        num = self._numeric_spec(req, cid)
        if num is not None:
            return self._ground_numeric(req, cid, reason, it, n_full, num, tname)
        # ---- 年代（能力の初出年）
        if cid.startswith("required:") or cid.startswith("required_propulsion:"):
            tok = cid.split(":", 1)[1]
            if cid.startswith("required_propulsion:"):
                cap = f"prop:{tok}"
            else:
                cap = K.TOKEN_CAP.get(tok)
            fy = K.first_year(cap) if cap else None
            if cap is None or fy is None:
                if cid not in ids:
                    return done("mismatch", "要求に無い制約を挙げている")
                return done("unverified", f"{tok} には年代の初出年データが無く照合できません（要求にはある制約）")
            claimed = [int(m) for m in re.findall(r"around (\d{4})", reason)]
            real = [(y, i) for y, i in self.cap_years().get(cap, []) if y <= year]
            real_tr = [(y, i) for y, i in real if self.D.assign.get(self.idx_all.ids[i]) == "train"]
            first_real = self.cap_years().get(cap, [(None, None)])[0][0]
            waived = cap in K.waivers_for_premise(req.get("premise"))
            it["claimed"], it["actual"] = (claimed[0] if claimed else None), {"first_year_table": fy, "earliest_real": first_real, "real_ships_up_to_request_year": len(real), "train": len(real_tr)}
            early = fy - year
            if waived:
                return done("mismatch", f"前提（技術前倒し）が {tok} を許しているのに拒否している")
            if early <= 0:
                it["examples"] = [self.ship_label(i) for _, i in real[:3]]
                return done("mismatch", f"表の初出年は {fy} 年で、要求年 {year} 年にはすでに存在する")
            if len(real_tr) >= 3:
                it["examples"] = [self.ship_label(i) for _, i in real_tr[:3]]
                return done("mismatch", f"{year} 年までにこの能力を持つ train の実艦が {len(real_tr)} 隻ある（最古 {first_real} 年）")
            if claimed and claimed[0] != fy:
                return done("mismatch", f"主張した初出年 {claimed[0]} は実データ（表: {fy} 年、最古の実艦 {first_real} 年）と違う")
            if early < K.V.ERA_TOL_YEARS:
                return done("weak", f"初出年 {fy} 年との差が {early} 年で、validator の許容（{K.V.ERA_TOL_YEARS} 年）以内。拒否は強すぎる可能性（最古の実艦 {first_real} 年）")
            return done("ok", f"初出年は {fy} 年（最古の実艦 {first_real} 年）で、要求年 {year} 年は {early} 年前" + (f"。主張した {claimed[0]} 年と一致" if claimed else ""))
        if cid not in ids:
            return done("mismatch", "この制約は要求に含まれていない")
        return done("unverified", "この種類の制約は照合の対象外です")

    @staticmethod
    def _numeric_spec(req: dict[str, Any], cid: str) -> dict[str, Any] | None:
        """数値制約 cid の (測る関数, 最良の向き, 主張の正規表現, 許容差, 単位)。"""
        isnum = K._isnum
        if cid == "min_speed_kn":
            return {"fn": lambda d: d["speed_kn"] if isnum(d.get("speed_kn")) else None, "pick": max, "re": r"reached ([\d.]+) kn", "tol": 0.5, "unit": "kn", "name": "最高速力", "dir": "max"}
        if cid == "min_gun_barrels":
            cal = req.get("min_gun_barrels_cal_mm")
            return {"fn": lambda d: float(X.gun_barrels(d, cal)), "pick": max, "re": r"carry at most (\d+)", "tol": 0.0, "unit": "砲門" + (f"（{cal:g} mm 以上）" if cal else ""), "name": "砲門数", "dir": "max"}
        if cid == "min_torpedo_tubes":
            return {"fn": lambda d: float(X.torpedo_tubes(d)), "pick": max, "re": r"carry at most (\d+)", "tol": 0.0, "unit": "門", "name": "魚雷管数", "dir": "max"}
        if cid == "min_aircraft":
            return {"fn": lambda d: float(X.aircraft_count(d)), "pick": max, "re": r"carry at most (\d+)", "tol": 0.0, "unit": "機", "name": "搭載機数", "dir": "max"}
        if cid == "max_complement":
            return {"fn": lambda d: d["complement"] if isnum(d.get("complement")) else None, "pick": min, "re": r"\((\d+) on", "tol": 1.0, "unit": "名", "name": "最小乗員", "dir": "min"}
        if cid.startswith("required:main_gun"):
            return {"fn": lambda d: C.main_gun_mm(d), "pick": max, "re": r"at most ([\d.]+) mm", "tol": 0.5, "unit": "mm", "name": "最大主砲口径", "dir": "max"}
        if cid == "min_endurance_nm":
            return {"fn": lambda d: d["endurance_nm"] if isnum(d.get("endurance_nm")) else None, "pick": max, "re": r"(\d+) nm", "tol": 50.0, "unit": "nm", "name": "最大航続", "dir": "max"}
        return None

    def _ground_numeric(self, req: dict[str, Any], cid: str, reason: str, it: dict[str, Any], n_full: int | None, sp: dict[str, Any], tname: str) -> None:
        fn, pick = sp["fn"], sp["pick"]
        b_all, i_all, n_all = self._best(self.idx_all, req, cid, fn, pick)
        b_tr, _i_tr, _n_tr = self._best(self.idx_train, req, cid, fn, pick)
        m = re.search(sp["re"], reason)
        claimed = float(m.group(1)) if m else None
        it["claimed"], it["actual"] = claimed, {"best_all": b_all, "best_train": b_tr, "n_candidates": n_all}
        if i_all is not None:
            it["examples"] = [self.ship_label(i_all)]
        if n_full:
            full = self.real_satisfying(req)
            it["examples"] = full["examples"][:3]
            it["verdict"], it["detail"] = "mismatch", f"要求を全て満たす実艦が train に {full['n_train']} 隻ある（全 split で {full['n_all']} 隻）"
            return
        if b_all is None:
            it["verdict"], it["detail"] = "unverified", "この制約を外しても、他の制約を全て満たす同艦種の実艦が無く、最良値を計算できない（要求全体が厳しい）"
            return
        tol = sp["tol"]
        lo, hi = min(b_all, b_tr if b_tr is not None else b_all), max(b_all, b_tr if b_tr is not None else b_all)
        unit = sp["unit"]
        base = f"同艦種・{req['year'] + YEAR_AHEAD} 年までの実艦で他の制約を満たすもの {n_all} 隻の{sp['name']}の{'最大' if sp['dir'] == 'max' else '最小'}は {_fmt(b_all)} {unit}（train のみ {_fmt(b_tr)}）"
        if claimed is not None and not (lo - tol <= claimed <= hi + tol):
            it["verdict"], it["detail"] = "mismatch", f"モデルが主張した {_fmt(claimed)} {unit} は実データと合わない。{base}"
            return
        it["verdict"], it["detail"] = "ok", base + (f"。主張した {_fmt(claimed)} と一致" if claimed is not None else "。要求値には届かない")

    def ground(self, req: dict[str, Any], status: str | None, confs: list[dict[str, Any]], rels: list[dict[str, Any]]) -> dict[str, Any]:
        full = self.real_satisfying(req)
        n_full = full.get("n_train")
        items = [self.ground_conflict(req, c, n_full) for c in confs]
        problems: list[str] = []
        notes: list[str] = []
        if status in ("relaxed", "infeasible") and n_full:
            problems.append(f"要求を全て満たす実艦が train に {n_full} 隻あるのに「{STATUS_JA[status]}」と答えている（全 split で {full['n_all']} 隻）")
        elif status in ("relaxed", "infeasible") and full.get("n_all"):
            notes.append(f"要求を全て満たす実艦は train には無いが、valid/test に {full['n_all']} 隻ある（モデルの学習範囲外の艦）")
        if status in ("relaxed", "infeasible") and not confs:
            problems.append("conflicts が空のまま実現不可能／緩和を主張している")
        cset = {str(c.get("constraint", "")).strip() for c in confs}
        rset = {str(r.get("constraint", "")).strip() for r in rels if isinstance(r, dict)}
        if status == "relaxed" and cset != rset:
            problems.append(f"conflicts の制約 {sorted(cset)} と relaxations の制約 {sorted(rset)} が一致しない")
        if status == "infeasible" and rels:
            problems.append("infeasible なのに relaxations がある")
        mism = [i for i in items if i["verdict"] == "mismatch"]
        verdict = "mismatch" if (mism or problems) else ("none" if not items and status in (None, "feasible") else "ok")
        return {"verdict": verdict, "items": items, "real": full, "problems": problems, "notes": notes, "n_mismatch": len(mism)}

    # ---------------- 出力の検査
    def analyze(self, req: dict[str, Any], text: str) -> dict[str, Any]:
        req = normalize_req(req)
        obj, strict, loose = EV.parse_text(text)
        a: dict[str, Any] = {"json_strict": strict, "json_loose": loose, "schema_problems": [], "status": None, "messages": []}

        def say(level: str, msg: str) -> None:
            a["messages"].append({"level": level, "text": msg})

        if not loose or not isinstance(obj, dict):
            say("bad", "出力が JSON object として解釈できません")
            return a
        if not strict:
            say("warn", "JSON の後ろに余計な文字があります（先頭の JSON だけを解析）")
        a["schema_problems"] = EV.schema_problems(obj)
        if a["schema_problems"]:
            say("bad", "出力 schema の違反: " + ", ".join(a["schema_problems"]))
        status = obj.get("status") if obj.get("status") in K.STATUSES else None
        a["status"] = status
        confs = [c for c in (obj.get("conflicts") if isinstance(obj.get("conflicts"), list) else []) if isinstance(c, dict)]
        rels = [r for r in (obj.get("relaxations") if isinstance(obj.get("relaxations"), list) else []) if isinstance(r, dict)]
        design = obj.get("design")
        waivers = K.waivers_for_premise(req.get("premise"))
        a["waivers"] = waivers
        a["checks"], a["violations"], a["warnings"], a["repair_violations"] = [], [], [], []
        if isinstance(design, dict):
            a["design_stats"] = {"gun_barrels": X.gun_barrels(design), "aircraft": X.aircraft_count(design), "torpedo_tubes": X.torpedo_tubes(design),
                                 "main_gun_mm": C.main_gun_mm(design), "ref_displacement": C.ref_displacement(design)[0]}
        if isinstance(design, dict) and status in ("feasible", "relaxed"):
            try:
                checks = K.req_checks_v1(req, design)
                failed = [c["id"] for c in checks if c["ok"] is not True]
                declared = [str(r.get("constraint")) for r in rels]
                eff = req if status == "feasible" else K.apply_relaxations(req, rels)
                res = K.check_design(eff, design, waivers)
            except Exception as e:  # noqa: BLE001
                say("bad", f"検査中の例外: {type(e).__name__}: {e}")
                return a
            a["checks"], a["declared"] = checks, declared
            a["violations"], a["warnings"] = res["violations"], res["warnings"]
            a["validator_ok"] = res["ok"]
            a["repair_violations"] = RP.violations_for_user(res) if res["violations"] else []
            if status == "feasible":
                a["false_feasible"] = bool(failed) or not res["ok"]
                if failed:
                    say("bad", "feasible と答えたが要求を満たしていない: " + ", ".join(failed))
                elif not res["ok"]:
                    say("bad", f"feasible と答えたが validator に {len(res['violations'])} 件の違反がある")
                else:
                    say("ok", "要求を全て満たし、validator に合格")
            else:
                hidden = sorted(set(failed) - set(declared))
                unneeded = sorted(set(declared) - set(failed))
                a["relaxed_hidden"], a["relaxed_unneeded"] = hidden, unneeded
                if hidden:
                    say("bad", "宣言していない要求を満たしていない（黙って緩和）: " + ", ".join(hidden))
                if unneeded:
                    say("warn", "緩和を宣言したが、設計は実際にはその制約を満たしている: " + ", ".join(unneeded))
                if not res["ok"]:
                    say("bad", f"緩和後の要求に対し validator に {len(res['violations'])} 件の違反がある")
                if not hidden and res["ok"]:
                    say("ok", "宣言した緩和以外は満たし、validator に合格")
        elif status == "infeasible":
            if design is not None:
                say("bad", "infeasible なのに design が null ではない")
            else:
                say("ok", "infeasible で design は null（schema どおり）")
        elif status in ("feasible", "relaxed") and not isinstance(design, dict):
            say("bad", f"{status} なのに design がありません")
        elif status is None:
            say("bad", "status が feasible / relaxed / infeasible のどれでもありません")
        if a["warnings"]:
            say("warn", f"validator の警告 {len(a['warnings'])} 件")
        a["grounding"] = self.ground(req, status, confs, rels)
        g = a["grounding"]
        if g["verdict"] == "mismatch":
            say("bad", "根拠不一致: conflicts の主張が実データと合わない")
        elif g["items"]:
            say("ok" if all(i["verdict"] == "ok" for i in g["items"]) else "warn", "conflicts の根拠照合: " + ", ".join(f"{i['constraint']}={i['verdict']}" for i in g["items"]))
        return a

    # ---------------- 推論サーバ
    def health(self, llm_url: str | None = None) -> dict[str, Any]:
        url = (llm_url or self.llm_url).rstrip("/")
        try:
            with urllib.request.urlopen(url + "/health", timeout=3) as r:
                return {"ok": r.status == 200, "detail": r.read().decode("utf-8", "replace")[:200], "llm_url": url, "model": self.model}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(e).__name__}: {e}", "llm_url": url, "model": self.model}

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0, seed: int | None = None, max_tokens: int = 2048,
             llm_url: str | None = None, model: str | None = None) -> dict[str, Any]:
        """設計役（design-v1）への 1 回の呼び出し。llm_url / model で、リクエストごとに設計役を切り替えられる。"""
        body: dict[str, Any] = {"model": model or self.model, "messages": messages, "temperature": float(temperature), "max_tokens": int(max_tokens), "stream": False}
        if seed is not None and temperature > 0:
            body["seed"] = int(seed)
        req = urllib.request.Request((llm_url or self.llm_url).rstrip("/") + "/v1/chat/completions", json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            o = json.load(r)
        ch = o["choices"][0]
        return {"content": ch["message"]["content"], "finish_reason": ch.get("finish_reason"), "usage": o.get("usage"), "elapsed_s": round(time.time() - t0, 2)}

    def turn(self, messages: list[dict[str, str]], turn: dict[str, Any], gen: dict[str, Any] | None = None) -> dict[str, Any]:
        """messages（system から始まる、ここまでの会話）に turn の user メッセージを足して推論し、検査まで返す。"""
        gen = gen or {}
        if not messages or messages[0].get("role") != "system":
            messages = new_conversation() + [m for m in messages if m.get("role") != "system"]
        if messages[0]["content"] != K.SYSTEM_V1:
            messages = [G.msg("system", K.SYSTEM_V1)] + messages[1:]
        content = compose_turn(turn)
        req = normalize_req(turn["req"]) if isinstance(turn.get("req"), dict) else None
        if req is None:
            raise ValueError("turn.req が必要です（repair は現在の要求を渡す）")
        sent = messages + [G.msg("user", content)]
        out: dict[str, Any] = {"ok": True, "user_content": content, "req": req, "kind": turn.get("kind")}
        try:
            r = self.chat(sent, float(gen.get("temperature", 0.0)), gen.get("seed"), int(gen.get("max_tokens", 2048)), gen.get("llm_url"), gen.get("model"))
        except urllib.error.URLError as e:
            return {**out, "ok": False, "error": f"推論サーバに接続できません（{self.llm_url}）: {e}"}
        except Exception as e:  # noqa: BLE001
            return {**out, "ok": False, "error": f"推論サーバのエラー: {type(e).__name__}: {e}"}
        out.update({"assistant_content": r["content"], "finish_reason": r["finish_reason"], "usage": r["usage"], "elapsed_s": r["elapsed_s"],
                    "messages": sent + [G.msg("assistant", r["content"])]})
        out["analysis"] = self.analyze(req, r["content"])
        return out


# --------------------------------------------------------------------------------------------------
# プリセット（フォームの値）
# --------------------------------------------------------------------------------------------------
PRESETS: list[dict[str, Any]] = [
    {"id": "jp1950_patrol", "title": "戦勝国日本 1950 巡視艇", "note": "前提: 日本が戦勝国。巡視艇クラス（排水量 300 t 以下、レーダー）",
     "form": {"country": "Japan", "year": 1950, "ship_type": "patrol", "mission": ["patrol"], "displacement_basis": "normal", "max_displacement_t": 300, "min_speed_kn": 18,
              "required": ["radar"], "premise": {"type": "war_outcome", "nation": "Japan", "like": "United-States"}}},
    {"id": "uk1938_cruiser", "title": "1938 英軽巡洋艦", "note": "152 mm 砲 12 門以上・魚雷発射管・速力 32 kn（Southampton/Arethusa 級の世界）",
     "form": {"country": "United-Kingdom", "year": 1938, "ship_type": "light_cruiser", "mission": ["surface_strike"], "displacement_basis": "standard", "max_displacement_t": 10000,
              "min_speed_kn": 32, "main_gun_mm": 152, "min_gun_barrels": 12, "min_gun_barrels_cal_mm": 152, "required": ["torpedo_tubes"]}},
    {"id": "it1960_dd", "title": "1960 伊ミサイル駆逐艦", "note": "SAM と 127 mm 砲、速力 30 kn、対艦・対空任務",
     "form": {"country": "Italy", "year": 1960, "ship_type": "destroyer", "mission": ["aaw", "escort"], "displacement_basis": "full", "max_displacement_t": 5200, "min_speed_kn": 30,
              "main_gun_mm": 127, "required": ["sam", "air_search_radar", "sonar"]}},
    {"id": "jp1895_aegis", "title": "1895 年のイージス艦（不可能例）", "note": "1895 年に SAM・ヘリ・CIWS・対空レーダー: 年代的に不可能なはず",
     "form": {"country": "Japan", "year": 1895, "ship_type": "destroyer", "mission": ["aaw"], "displacement_basis": "normal", "max_displacement_t": 8000, "min_speed_kn": 30,
              "required": ["helicopter", "sam", "ciws", "air_search_radar", "fire_control_radar"]}},
    {"id": "fict1975_fftd", "title": "架空国家 1975 防空フリゲート", "note": "Norvane（架空国家。学習データに出ない名前）。フランスに似る、SAM・対空レーダー",
     "form": {"country": "Norvane", "year": 1975, "ship_type": "frigate", "mission": ["aaw", "escort"], "displacement_basis": "full", "max_displacement_t": 3500, "min_speed_kn": 28,
              "main_gun_mm": 100, "required": ["sam", "air_search_radar", "sonar"], "premise": {"type": "fictional_nation", "like": "France"}}},
    {"id": "uk1935_heli", "title": "技術前倒し: 1935 英駆逐艦 + ヘリ", "note": "前提: 艦載ヘリが 1935 年に実用化（他は史実どおり）",
     "form": {"country": "United-Kingdom", "year": 1935, "ship_type": "destroyer", "mission": ["escort", "asw"], "displacement_basis": "normal", "max_displacement_t": 2000, "min_speed_kn": 34,
              "required": ["helicopter"], "premise": {"type": "tech_early", "tech": "helicopter", "available_from": 1935}}},
    {"id": "us1942_barrels", "title": "砲門数が不可能: 1942 米駆逐艦（12 門）", "note": "1500 t 以下で 127 mm 砲 12 門以上: 実艦の最大を超える（根拠照合の確認用）",
     "form": {"country": "United-States", "year": 1942, "ship_type": "destroyer", "mission": ["surface_strike"], "displacement_basis": "normal", "max_displacement_t": 1500, "min_speed_kn": 34,
              "main_gun_mm": 127, "min_gun_barrels": 12, "min_gun_barrels_cal_mm": 127}},
    {"id": "jp2016_patrol", "title": "2016 日本の哨戒艦（学習データの例）", "note": "学習データ single の 1 例と同じ要求。素直に feasible になるはず",
     "form": {"country": "Japan", "year": 2016, "ship_type": "patrol", "mission": ["patrol"], "displacement_basis": "normal", "max_displacement_t": 860, "min_displacement_t": 550,
              "min_speed_kn": 23, "required": ["ciws", "radar"]}},
]


# --------------------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------------------
def make_handler(pg: Playground, chat: Any | None = None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ShipknowPlayground/1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            sys.stderr.write("[playground] " + (fmt % args) + "\n")

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/meta":
                self._json(200, pg.meta())
            elif path == "/api/health":
                q = urllib.parse.parse_qs(self.path.partition("?")[2])
                self._json(200, pg.health((q.get("url") or [None])[0]))
            elif path == "/api/chat/meta" and chat is not None:
                self._json(200, {**chat.meta(), "design": pg.health(), "design_presets": DESIGN_PRESETS(pg)})
            elif path == "/api/chat/session" and chat is not None:
                q = urllib.parse.parse_qs(self.path.partition("?")[2])
                self._json(200, chat.session((q.get("id") or [None])[0]).snapshot())
            elif path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if path == "/api/build_request":
                    self._json(200, pg.build_request(body["form"], int(body.get("variant", 0)), bool(body.get("with_nbr", True))))
                elif path == "/api/preview_modify":
                    self._json(200, pg.preview_modify(body["req"], body["changes"], int(body.get("variant", 0))))
                elif path == "/api/turn":
                    r = pg.turn(body.get("messages") or [], body["turn"], body.get("gen"))
                    self._json(200 if r.get("ok") else 502, r)
                elif path == "/api/analyze":
                    self._json(200, pg.analyze(body["req"], body["text"]))
                elif path == "/api/chat/send" and chat is not None:
                    self._json(200, chat.handle(body.get("session_id"), body["text"], body.get("settings")))
                elif path == "/api/chat/reset" and chat is not None:
                    self._json(200, chat.reset(body.get("session_id")))
                else:
                    self._json(404, {"error": "not found"})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
                self._json(400, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    return Handler


def DESIGN_PRESETS(pg: Playground) -> list[dict[str, str]]:
    """設計役の選択肢（画面のプルダウン）。起動引数の --llm-url / --model が先頭。--design-extra で追加できる。"""
    return [{"label": f"{pg.model}（{pg.llm_url}）", "url": pg.llm_url, "model": pg.model}] + list(getattr(pg, "extra_designs", []))


def serve(pg: Playground, host: str = "127.0.0.1", port: int = 8765, chat: Any | None = None) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(pg, chat))
    httpd.daemon_threads = True
    return httpd


def main() -> None:
    ap = argparse.ArgumentParser(description="design-v1 playground（ローカル Web アプリ）")
    ap.add_argument("--host", default="127.0.0.1", help="待ち受けアドレス（既定 127.0.0.1。ローカル以外は推奨しない）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--llm-url", default=DEFAULT_LLM_URL, help="推論サーバ（OpenAI 互換）の URL")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="chat/completions に渡す model 名")
    ap.add_argument("--timeout", type=float, default=300.0, help="推論 1 回のタイムアウト秒")
    ap.add_argument("--gemma-url", default="http://127.0.0.1:18810", help="会話モードの会話役（gemma-4-e4b-it）の URL")
    ap.add_argument("--gemma-model", default="gemma-4-e4b-it")
    ap.add_argument("--design-extra", action="append", default=[], metavar="LABEL=URL[,MODEL]",
                    help="会話モードの設計役の選択肢を追加（例: 'LoRA0.7=http://127.0.0.1:18801,design_v1_step1200'）。何度でも指定できる")
    args = ap.parse_args()
    pg = Playground(args.llm_url, args.model, timeout=args.timeout)
    pg.extra_designs = []
    for e in args.design_extra:
        label, _, rest = e.partition("=")
        url, _, model = rest.partition(",")
        pg.extra_designs.append({"label": f"{label}（{url}）", "url": url, "model": model or args.model})
    import shipknow_playground_chat as PC  # noqa: E402

    chat = PC.ChatEngine(pg, PC.GemmaClient(args.gemma_url, args.gemma_model))
    httpd = serve(pg, args.host, args.port, chat)
    print(f"playground: http://{args.host}:{args.port}/  (design {args.llm_url} {args.model}; gemma {args.gemma_url})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
