"""design-v1 playground の「会話モード」（日本語）。会話役（gemma-4-e4b-it）と設計役（design-v1）の分業。

    ユーザー（日本語） -> 会話役が聞き取り -> JSON のアクション {"action":"design"|"modify", "req": {...}, ...}
      -> オーケストレータ（このモジュール）が req を検証し、要求文・近傍（NB-1）を作って design-v1 に学習データと同じ書式で送る
      -> 返答を check_design で検査。違反があれば repair ターン（validator の違反を返す）で最大 N 回修正させる
      -> relaxed / infeasible は実データで根拠照合し、根拠不一致なら temperature 0.7 + 別 seed で取り直す（最大 M 回）
      -> 艦名（実在国は NSDB の SHIPS、架空国家は会話役が命名）-> 会話役が日本語で説明（数値は JSON と自動照合。不一致なら再生成、最後はテンプレート）

推論サーバは全て OpenAI 互換の /v1/chat/completions。会話役には ``chat_template_kwargs: {"enable_thinking": false}`` を必ず付ける。
このモジュールは shipknow_playground の Playground（実艦データ・検査・design-v1 呼び出し）を使う。
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import numpy as np

import shipknow_design_common as C
import shipknow_design_v1_core as K
import shipknow_playground as P

DEFAULT_GEMMA_URL = "http://127.0.0.1:18810"
DEFAULT_GEMMA_MODEL = "gemma-4-e4b-it"
NSDB_PATH = P.C.ROOT / "nsdb" / "NSDB.db"
MAX_REQ_RETRIES = 2  # 会話役のアクションが不正なとき、再生成させる回数
MAX_EXPLAIN_RETRIES = 2  # 説明文の数値照合に失敗したとき、再生成させる回数
RESAMPLE_SEEDS = (101, 102)
HISTORY_KEEP = 16  # 会話役に渡す過去メッセージの上限

COUNTRY_JA_HINT = {"Japan": "日本", "United-States": "アメリカ", "United-Kingdom": "イギリス", "Germany": "ドイツ", "Italy": "イタリア", "France": "フランス", "Russian": "ソ連/ロシア",
                   "China": "中国", "Netherlands": "オランダ", "Spain": "スペイン", "Austria-Hungary": "オーストリア＝ハンガリー", "South-Korea": "韓国", "North-Korea": "北朝鮮",
                   "Sweden": "スウェーデン", "Norway": "ノルウェー", "Denmark": "デンマーク", "Finland": "フィンランド", "Greece": "ギリシャ", "Turkey": "トルコ", "India": "インド",
                   "Australia": "オーストラリア", "Canada": "カナダ", "Brazil": "ブラジル", "Argentina": "アルゼンチン", "Poland": "ポーランド", "Taiwan": "台湾"}
UNIT_RE = r"(?:t|kn|nm|mm|cm|hp|m|名|人|隻|門|機|基|年|%|％|ノット|トン|馬力|海里|ミリ|メートル|門|発|基|丁)"
NEG_WORDS = ("ない", "ません", "なし", "未搭載", "持たない", "持ちません", "含まれ", "不可", "できません", "無い")
# 説明文に出てきた装備の語 -> 設計に無ければ作り話の疑い（能力語彙）
EQUIP_WORDS = [("ヘリ", {"air:helicopter"}), ("対艦ミサイル", {"ssm"}), ("SSM", {"ssm"}), ("対空ミサイル", {"sam"}), ("SAM", {"sam"}), ("CIWS", {"ciws"}), ("近接防御", {"ciws"}),
               ("魚雷", {"torpedo_tube"}), ("ソナー", {"sonar"}), ("ESM", {"esm_ecm"}), ("ロケット", {"rocket_launcher", "asw_rocket"}), ("機雷", {"mine"}),
               ("弾道ミサイル", {"ballistic_missile"}), ("爆雷", {"depth_charge"})]
REMOVE_WORDS = ("外", "不要", "いらない", "要らない", "なし", "無し", "削除", "やめ", "取り除", "要りません", "必要ない", "なくて", "無くて", "いりません")


# --------------------------------------------------------------------------------------------------
# 会話役 system プロンプト
# --------------------------------------------------------------------------------------------------
def _types_text() -> str:
    return "、".join(f"{t}={C._TYPE_JA.get(t, t)}" for t in C._TYPE_NAME)


def _tokens_text() -> str:
    return "、".join(f"{t}={C._TOKEN_JA.get(t, t)}" for t in P.REQUIRED_ORDER if t != "main_gun") + "、main_gun>=Nmm（口径N mm以上の主砲。例 main_gun>=127mm）"


GEMMA_SYSTEM = """あなたは艦艇設計アシスタントです。ユーザーの日本語の要望を聞き取り、設計AI（design-v1）に渡す要求(req)を作ります。設計そのものは設計AIとシステムが行うので、あなたは数値の設計をしません。

# 出力形式（必ずこの形式）
<say>ユーザーへの短い日本語の返事（1〜2文）</say>
<action>{JSON}</action>
- 質問だけのときは <say> だけを出し、<action> は付けない。
- 国・年・艦種のどれかが分からないときだけ、足りないものを短く質問する。他の項目は聞かずに省略してよい（排水量上限・速力下限・任務を省略するとシステムが実艦データから補う）。
- 要望が揃ったら <say> に「設計AIに依頼します」程度を書き、<action> に JSON を出す。

# アクション
新しい艦の依頼（最初の依頼、または別の艦）:
{"action":"design","req":{...},"premise":null,"notes":"補足"}
いまの設計の変更（「もっと速く」「ヘリを2機に」など）。changes には変えたキーだけを入れる（キーを外すときは null）。現在の要求は下の「現在の状態」にある。req は省略する。
changes で使えるキー: req のキー（min_speed_kn, max_displacement_t, min_aircraft, min_gun_barrels, year, country など）に加えて、
  main_gun_mm（主砲口径の下限 mm）、add_required（足す装備の配列）、remove_required（外す装備の配列）
{"action":"modify","changes":{"min_speed_kn":30,"add_required":["helicopter"],"min_aircraft":2},"notes":"補足"}

# req のキー（これ以外は使わない）
country: 国の英語ID。日本=Japan、アメリカ=United-States、イギリス=United-Kingdom、ドイツ=Germany、イタリア=Italy、フランス=France、ソ連/ロシア=Russian、中国=China、韓国=South-Korea、オランダ=Netherlands（ハイフンでつなぐ）
year: 年（整数、1860以降）
ship_type: 艦種ID（下の一覧から1つ）
ship_type_fine: 細分類（分からなければ省略）
mission: 任務の配列。patrol, asw, aaw, surface_strike, escort, mine_warfare, amphibious, fire_support, coastal_defense, submarine_ops, carrier_air, support, training
displacement_basis: normal(常備) / full(満載) / standard(基準)。既定は normal
max_displacement_t: 排水量の上限(t)。min_displacement_t: 下限(t)
min_speed_kn: 速力の下限(kn)。max_speed_kn: 上限(kn)
max_complement: 乗員の上限。min_endurance_nm: 航続の下限(nm)
min_gun_barrels: 砲門数の下限（口径の下限は min_gun_barrels_cal_mm）
min_aircraft: 搭載機数の下限。min_torpedo_tubes: 魚雷発射管数の下限
required: 必須装備の配列（下の一覧）
required_propulsion: 推進方式の配列。steam_reciprocating, steam_turbine, diesel, electric_motor, petrol_engine, gas_turbine, nuclear
ユーザーが言っていない制約を勝手に足さない。数値は単位換算してこのキーの単位（t, kn, nm, mm）にする。

# 艦種ID
__TYPES__
# 必須装備 required の語
__TOKENS__

# premise（前提。ユーザーが言ったときだけ。無ければ null）
- 架空の国: country に国名（英語表記、空白なし）を入れ、{"type":"fictional_nation","like":"似ている実在国のID"}。似ている国をユーザーが言わなければ、時代と艦種に合う国を選んで notes に書く
- 技術の前倒し: {"type":"tech_early","tech":"radar|sonar|helicopter|ssm|sam|ciws|esm_ecm|gas_turbine|nuclear","available_from":年}
- 戦争結果の変更（日本/ドイツ/イタリアが勝った等）: {"type":"war_outcome","nation":"Japan","like":"United-States または null"}
- ドクトリン: {"type":"doctrine","doctrine":"torpedo_centric|gun_centric|missile_centric|air_centric|asw_centric"}

# 例
ユーザー: 1920年の日本の装甲巡洋艦、魚雷付きで
<say>設計AIに依頼します。</say>
<action>{"action":"design","req":{"country":"Japan","year":1920,"ship_type":"armored_protected_cruiser","required":["torpedo_tubes"]},"premise":null,"notes":""}</action>

ユーザー: 架空の国ノルヴァーンの1975年の防空フリゲート、ヘリ2機
<say>フランスに似た国として設計AIに依頼します。</say>
<action>{"action":"design","req":{"country":"Norvane","year":1975,"ship_type":"frigate","mission":["aaw","escort"],"required":["sam","helicopter"],"min_aircraft":2},"premise":{"type":"fictional_nation","like":"France"},"notes":"似ている国はフランスとした"}</action>

ユーザー: 戦艦を作りたい
<say>どの国の、何年ごろの戦艦ですか？</say>

# modify の例（現在の要求 min_speed_kn=22、直前の設計の速力 23 kn のとき）
ユーザー: 速力をもっと上げて
<say>最低速力を 27 kn に上げて設計AIに依頼します。</say>
<action>{"action":"modify","changes":{"min_speed_kn":27},"notes":"もっと = +5 kn 程度"}</action>
ユーザー: ヘリを2機に
<say>ヘリ2機で依頼します。</say>
<action>{"action":"modify","changes":{"add_required":["helicopter"],"min_aircraft":2},"notes":""}</action>
ユーザー: 主砲をもっと大きく
<say>主砲口径の下限を一段大きくして依頼します。</say>
<action>{"action":"modify","changes":{"main_gun_mm":254},"notes":"現在の最大主砲口径 203 mm の次の標準口径"}</action>

# 守ること
- 設計済みの状態でユーザーが設計の変更を言ったら、質問せずに changes を出す。
- 「もっと」「増やして」など程度が曖昧な変更は、「現在の状態」の値を基準に具体的な数値を決める（速力は +3〜5 kn、排水量上限は +15%、砲口径は一段大きい標準口径、砲門数・搭載機数は +2 程度）。聞き返さずに決めてよい。
- changes の値を null にするのは、ユーザーが「外して」「不要」と言った制約だけ。それ以外で null を使わない。
- <say> は日本語で短く。設計の数値や装備を自分で作って話さない（設計結果は後でシステムから渡される）。
- JSON は厳密（キーは二重引用符、末尾カンマなし、コメントなし）。
__STATE__"""


def build_gemma_system(state_text: str) -> str:
    return (GEMMA_SYSTEM.replace("__TYPES__", _types_text()).replace("__TOKENS__", _tokens_text()).replace("__STATE__", state_text))


EXPLAIN_SYSTEM = """あなたは艦艇設計アシスタントです。システムが渡す「設計結果 JSON」をユーザーに日本語で説明します。
厳守:
- 数値（排水量、寸法、速力、出力、乗員、航続、砲の数・口径、年 など）は JSON に書かれた値と単位をそのまま使う。design_facts の表記（単位つき）を写すのが安全。砲の口径は mm（「120口径」ではなく「口径 120 mm」）。換算（km/h など）・丸め・足し算・引き算・推測をしない。
- JSON に無い装備・数値・性能・歴史的事実を書かない。装備が JSON の design に無ければ「搭載しない」とだけ言う。
- 艦級名・艦名は JSON の names にあるものだけを使う。
- status が feasible なら要求どおりの設計、relaxed なら緩和した要求を最初に明示、infeasible なら実現できない理由（conflicts の reason）を説明する。
- check の unmet や violations が残っているときは、満たせていない点を正直に書く。grounding_problems があれば「設計AIの拒否理由は実データと合わない可能性がある」と書く。
- 番号付きリストは使わず、短い段落か「・」の箇条書きにする。10行程度まで。装飾（**太字**など）は使わない。"""

NAMES_SYSTEM = """架空の国の軍艦の艦級名と艦名を考えます。国名・時代・艦種を踏まえ、その国らしい響きの名前にします。
JSON だけを出力: {"class_name":"艦級名","ship_names":["艦名1","艦名2","艦名3"]}
艦名は3〜4個、英語表記の固有名詞（数字を含めない）。実在の艦名は避ける。"""


# --------------------------------------------------------------------------------------------------
# 会話役の出力の解析・アクション検証
# --------------------------------------------------------------------------------------------------
_SAY = re.compile(r"<say>(.*?)</say>", re.S)
_ACT = re.compile(r"<action>(.*?)</action>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_gemma_output(text: str) -> dict[str, Any]:
    """会話役の出力 -> {"say": str, "action": dict | None, "error": str | None, "raw": text}。
    <say>...</say> と <action>{json}</action> が基本形。タグが無くても、```json フェンスか、"action" を含む最初の JSON object を拾う。"""
    raw = text or ""
    out: dict[str, Any] = {"say": "", "action": None, "error": None, "raw": raw}
    say_m = _SAY.findall(raw)
    act_m = _ACT.search(raw)
    body: str | None = None
    rest = raw
    if act_m:
        body = act_m.group(1)
        rest = raw.replace(act_m.group(0), "")
    else:
        fm = _FENCE.search(raw)
        if fm and '"action"' in fm.group(1):
            body = fm.group(1)
            rest = raw.replace(fm.group(0), "")
        else:
            i = raw.find('{"action"')
            if i < 0:
                m = re.search(r"\{\s*\"action\"", raw)
                i = m.start() if m else -1
            if i >= 0:
                try:
                    obj, end = json.JSONDecoder().raw_decode(raw[i:])
                    body = json.dumps(obj, ensure_ascii=False)
                    rest = raw[:i] + raw[i + end:]
                except json.JSONDecodeError:
                    body = raw[i:]
                    rest = raw[:i]
    say = "\n".join(s.strip() for s in say_m) if say_m else re.sub(r"</?say>", "", rest).strip()
    out["say"] = say.strip()
    if body is not None:
        b = body.strip()
        fm = _FENCE.search(b)
        if fm:
            b = fm.group(1).strip()
        try:
            obj = json.loads(b)
        except json.JSONDecodeError as e:
            out["error"] = f"<action> の JSON が読めません: {e.msg}（位置 {e.pos}）"
            return out
        if not isinstance(obj, dict):
            out["error"] = "<action> は JSON object である必要があります"
            return out
        out["action"] = obj
    return out


class ActionError(ValueError):
    pass


def _num_field(v: Any, key: str) -> int | float:
    if isinstance(v, bool) or v is None:
        raise ActionError(f"{key} は数値にしてください: {v!r}")
    if isinstance(v, str):
        v = v.replace(",", "").strip()
    try:
        return P._to_num(v, key)
    except ValueError:
        raise ActionError(f"{key} は数値にしてください: {v!r}") from None


def sanitize_req(pg: P.Playground, req: Any, premise: Any = None) -> tuple[dict[str, Any], list[str]]:
    """会話役の req を検証して design-v1 の req（学習データと同じキー・型・順序）にする。戻り値 (req, 警告)。不正は ActionError。"""
    if not isinstance(req, dict):
        raise ActionError("req は JSON object にしてください")
    warns: list[str] = []
    r: dict[str, Any] = {}
    for k in req:
        if k not in K.REQ_ORDER:
            warns.append(f"未知のキー {k!r} は無視しました")
    miss = [k for k in ("country", "year", "ship_type") if _is_blank(req.get(k))]
    if miss:
        raise ActionError("req に " + ", ".join(miss) + " がありません（国・年・艦種は必須）")
    if not isinstance(req["country"], str):
        raise ActionError("country は文字列にしてください")
    r["country"] = P._hyphen(req["country"])
    r["year"] = int(_num_field(req["year"], "year"))
    if not (1860 <= r["year"] <= 2030):
        raise ActionError(f"year {r['year']} は 1860〜2030 の範囲にしてください")
    st = req["ship_type"]
    if st not in C._TYPE_NAME:
        raise ActionError(f"ship_type {st!r} は艦種IDにありません。候補: {', '.join(C._TYPE_NAME)}")
    r["ship_type"] = st
    ft = req.get("ship_type_fine")
    if ft:
        fines = {d.get("ship_type_fine") for d in pg.idx_all.designs if d["ship_type"] == st}
        if ft in fines:
            r["ship_type_fine"] = ft
        else:
            warns.append(f"細分類 {ft!r} は {st} の実艦に無いので外しました")
    ms = req.get("mission")
    if ms:
        ms = [ms] if isinstance(ms, str) else ms
        ok = [m for m in ms if m in C.MISSION_TAGS]
        if len(ok) != len(ms):
            warns.append("任務の語彙外の値を外しました: " + ", ".join(str(m) for m in ms if m not in C.MISSION_TAGS))
        if ok:
            r["mission"] = ok
    for k in P.NUM_FIELDS:
        if not _is_blank(req.get(k)):
            r[k] = _num_field(req[k], k)
            if r[k] <= 0 and k != "min_displacement_t":
                raise ActionError(f"{k} は正の数にしてください: {req[k]!r}")
    if r.get("min_gun_barrels_cal_mm") is not None and r.get("min_gun_barrels") is None:
        r.pop("min_gun_barrels_cal_mm")
    basis = req.get("displacement_basis") or "normal"
    if basis not in P.BASIS_JA:
        raise ActionError(f"displacement_basis は normal / full / standard のどれか: {basis!r}")
    if basis != "normal" or "max_displacement_t" in r or "min_displacement_t" in r:
        r["displacement_basis"] = basis
    toks = req.get("required") or []
    if not isinstance(toks, list):
        raise ActionError("required は文字列の配列にしてください")
    clean: list[str] = []
    for t in toks:
        if isinstance(t, str) and re.fullmatch(r"main_gun>=\d+(?:\.\d+)?mm", t):
            clean.append(t)
        elif isinstance(t, str) and t in P.REQUIRED_ORDER and t != "main_gun":
            clean.append(t)
        else:
            raise ActionError(f"required の語 {t!r} は使えません。使える語: {', '.join(x for x in P.REQUIRED_ORDER if x != 'main_gun')}, main_gun>=Nmm")

    def pos(t: str) -> int:
        return P.REQUIRED_ORDER.index("main_gun" if t.startswith("main_gun") else t)

    r["required"] = sorted(dict.fromkeys(clean), key=pos)
    rp = req.get("required_propulsion")
    if rp:
        rp = [rp] if isinstance(rp, str) else rp
        bad = [p for p in rp if p not in K.PROPULSION_KINDS]
        if bad:
            raise ActionError(f"required_propulsion の値 {bad} は使えません。使える値: {', '.join(K.PROPULSION_KINDS)}")
        r["required_propulsion"] = list(rp)
    prem = premise if premise is not None else req.get("premise")
    if prem:
        if not isinstance(prem, dict):
            raise ActionError("premise は JSON object か null にしてください")
        form = dict(prem)
        if form.get("type") == "fictional_nation":
            like = P._hyphen(str(form.get("like") or ""))
            known = {rec["country"] for rec in pg.idx_all.recs}
            if like not in known:
                raise ActionError(f"fictional_nation の like {like!r} は実在国のIDではありません（例: Japan, France, United-Kingdom）")
        try:
            pm, _txt = P.build_premise(form, r["country"], 0)
        except ValueError as e:
            raise ActionError(f"premise が不正です: {e}") from None
        r["premise"] = pm
    known_c = {rec["country"] for rec in pg.idx_all.recs}
    if r["country"] not in known_c and (r.get("premise") or {}).get("type") != "fictional_nation":
        raise ActionError(f"country {r['country']!r} は実艦データに無い国です。実在国のIDにするか、架空国家なら premise に fictional_nation（like 付き）を入れてください")
    return K.order_req(r), warns


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def fill_defaults(pg: P.Playground, req: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """会話役が省略した排水量上限・最低速力・任務を、実艦データから補う（同艦種で年が近い実艦。同じ国が 3 隻以上あればその国）。
    戻り値 (req, 補った項目の説明)。"""
    notes: list[str] = []
    r = dict(req)
    need = [k for k in ("max_displacement_t", "min_speed_kn", "mission") if r.get(k) is None]
    if not need:
        return K.order_req(r), notes
    idx = pg.idx_all
    basis = r.get("displacement_basis", "normal")
    near = None
    for span, same_country in ((10, True), (10, False), (25, False), (80, False)):
        m = (idx.type == r["ship_type"]) & (abs(idx.year - r["year"]) <= span)
        if same_country:
            m &= idx.country == r["country"]
        sel = np.where(m)[0]
        if len(sel) >= 3:
            near = sorted(sel, key=lambda i: (abs(idx.year[i] - r["year"]), i))
            where = f"{r['country'] if same_country else '各国'}の同艦種・{r['year'] - span}〜{r['year'] + span} 年の実艦 {len(sel)} 隻"
            break
    if near is None:
        return K.order_req(r), notes
    if r.get("max_displacement_t") is None:
        vals = sorted(float(v) for v in idx.disp(basis)[near] if v == v)
        if vals:
            r["max_displacement_t"] = P._to_num(C._nice_round(vals[len(vals) // 2] * 1.15), "max_displacement_t")
            r["displacement_basis"] = basis
            notes.append(f"排水量上限 {r['max_displacement_t']} t（{P.BASIS_JA[basis]}）は{where}の中央値 × 1.15 で補いました")
    if r.get("min_speed_kn") is None:
        vs = sorted(float(v) for v in idx.speed[near] if v == v)
        if vs:
            r["min_speed_kn"] = K.num(max(2.0, int((vs[len(vs) // 2] - 1) * 2) / 2))
            notes.append(f"最低速力 {r['min_speed_kn']} kn は{where}の中央値 − 1 で補いました")
    if r.get("mission") is None:
        tags = C.mission_tags(idx.designs[near[0]])
        if tags:
            r["mission"] = tags
            notes.append("任務 " + "・".join(tags) + " は最も近い実艦から補いました")
    return K.order_req(r), notes


def build_design_action(pg: P.Playground, action: dict[str, Any]) -> dict[str, Any]:
    req, warns = sanitize_req(pg, action.get("req"), action.get("premise"))
    req, fills = fill_defaults(pg, req)
    return {"req": req, "warnings": warns, "fills": fills, "notes": str(action.get("notes") or "")}


def _changes_from_raw(base: dict[str, Any], raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """会話役の changes（キー -> 値）を P.apply_changes の変更リストにする。使えるキー: req のキー（null で外す）、required（配列全体 -> 差分にする）、
    main_gun_mm（主砲口径の下限）、add_required / remove_required（装備の追加・削除）。"""
    out: list[dict[str, Any]] = []
    warns: list[str] = []
    for k, v in raw.items():
        if k == "required":
            if not isinstance(v, list):
                raise ActionError("changes.required は配列にしてください（変更後の必須装備の全体）")
            have = list(base.get("required") or [])
            hg = next((t for t in have if t.startswith("main_gun")), None)
            ng = next((t for t in v if isinstance(t, str) and t.startswith("main_gun")), None)
            if hg != ng:
                out.append({"field": "main_gun_mm", "value": float(ng[len("main_gun>="):-2]) if ng else None})
            out += [{"field": "add_required", "value": t} for t in v if t not in have and not str(t).startswith("main_gun")]
            out += [{"field": "remove_required", "value": t} for t in have if t not in v and not t.startswith("main_gun")]
        elif k in ("add_required", "remove_required"):
            for t in ([v] if isinstance(v, str) else (v or [])):
                out.append({"field": k, "value": t})
        elif k == "main_gun_mm":
            out.append({"field": k, "value": v})
        elif k == "required_propulsion":
            out.append({"field": k, "value": v or None})
        elif k in P.NUM_FIELDS or k in ("country", "year", "displacement_basis"):
            out.append({"field": k, "value": v})
        elif k in ("mission", "ship_type", "ship_type_fine", "premise"):
            warns.append(f"changes.{k} は modify では変更できないので無視しました（艦種などを変えるなら新しい設計として依頼してください）")
        else:
            warns.append(f"未知のキー {k!r} は無視しました")
    return out, warns


def build_modify_action(pg: P.Playground, action: dict[str, Any], current_req: dict[str, Any]) -> dict[str, Any]:
    """modify アクション -> 更新後の req。現在の要求に変更だけを適用する（会話役が req のキーを落としても、勝手に制約が消えない）。
    変更は action["changes"] が優先。無ければ action["req"] が現在の要求と違うキーだけを変更とみなす（req に無いキーは「変更なし」）。
    キー順は学習データの modify と同じ（既存キーは位置を保ち、新しい制約は末尾）。"""
    base = P.normalize_req(current_req)
    ch = action.get("changes")
    if isinstance(ch, dict) and ch:
        raw = dict(ch)
    elif isinstance(action.get("req"), dict):
        raw = {k: v for k, v in action["req"].items() if k in K.REQ_ORDER and k not in ("ship_type", "ship_type_fine", "mission", "premise") and base.get(k) != v}
    else:
        raise ActionError("modify には changes（変えたキーと新しい値）か req（変更後の全要求）が必要です")
    changes, warns = _changes_from_raw(base, raw)
    try:
        applied = P.apply_changes(base, changes)
    except ValueError as e:
        raise ActionError(str(e)) from None
    _checked, w2 = sanitize_req(pg, applied, applied.get("premise"))  # 検証だけ（キー順は applied を使う）
    diff = P.diff_changes(base, applied)
    return {"req": applied, "changes": diff, "warnings": warns + w2, "fills": [], "notes": str(action.get("notes") or "")}


# --------------------------------------------------------------------------------------------------
# 艦名（NSDB SHIPS、mode=ro）
# --------------------------------------------------------------------------------------------------
_YEAR = re.compile(r"(?<!\d)(1[5-9]\d\d|20[0-3]\d)(?!\d)")
_NAME_RE = re.compile(r"^(?P<native>.*?)\s*<(?P<rom>[^>]+)>")


def _year_of(s: Any) -> int | None:
    m = _YEAR.search(str(s or ""))
    return int(m.group(1)) if m else None


def parse_ship_name(raw: str) -> tuple[str, str | None]:
    """'富士 <Fuji>' -> ('Fuji', '富士')。'Chin Yen (ex-...)' -> ('Chin Yen', None)。"""
    raw = (raw or "").strip()
    m = _NAME_RE.match(raw)
    if m:
        rom = m.group("rom").strip()
        nat = m.group("native").strip() or None
    else:
        rom, nat = re.sub(r"\s*\(.*?\)\s*$", "", raw).strip(), None
    rom = re.sub(r"\s+", " ", rom)
    return rom, nat


class ShipNamer:
    """実在国の艦名を NSDB の SHIPS から引く。艦級（records の class）を要求の艦種・年に近い順に選び、その艦級の艦名を 3〜4 隻返す。
    要求年に同じ国・同じ艦種で就役中だった艦の名前は避ける（同じ名前は使われていない）。"""

    def __init__(self, pg: P.Playground, db_path: Path = NSDB_PATH):
        self.pg, self.db_path = pg, Path(db_path)
        self._lock = threading.Lock()
        self._by_url: dict[str, list[dict[str, Any]]] | None = None
        self._cls: dict[str, dict[str, Any]] = {}

    def _load(self) -> None:
        with self._lock:
            if self._by_url is not None:
                return
            conn = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True)
            by: dict[str, list[dict[str, Any]]] = {}
            for name, cls, country, laid, launched, fate_when, url, seq in conn.execute(
                    "SELECT NAME, CLASS, COUNTRY, Laid_down, Launched, Fate_WHEN, source_url, seq FROM SHIPS WHERE NAME IS NOT NULL AND NAME != '' ORDER BY seq"):
                rom, nat = parse_ship_name(name)
                if not rom:
                    continue
                by.setdefault(url, []).append({"name": rom, "native": nat, "class": cls, "country": country, "launched": _year_of(launched) or _year_of(laid), "fate": _year_of(fate_when)})
            cls_url = {f"c{rid:05d}": url for rid, url in conn.execute("SELECT rowid, source_url FROM CLASS")}
            conn.close()
            for rec_id, url in cls_url.items():
                self._cls[rec_id] = {"url": url}
            self._by_url = by

    def names_for(self, country: str, ship_type: str, year: int, k: int = 4) -> dict[str, Any] | None:
        self._load()
        assert self._by_url is not None
        idx = self.pg.idx_all
        cands = [i for i in range(len(idx)) if idx.recs[i]["country"] == country and idx.type[i] == ship_type]
        if not cands:
            return None
        # 要求年に同国・同艦種で就役中だった艦の名前
        in_use: set[str] = set()
        class_of: dict[int, tuple[str, list[dict[str, Any]]]] = {}
        for i in cands:
            url = (self._cls.get(idx.recs[i]["id"]) or {}).get("url")
            ships = self._by_url.get(url or "", [])
            class_of[i] = (url or "", ships)
            for s in ships:
                if s["launched"] is not None and s["launched"] <= year and (s["fate"] is None or s["fate"] > year):
                    in_use.add(s["name"].lower())
        order = sorted(cands, key=lambda i: (abs(int(idx.year[i]) - year), i))
        picked: list[dict[str, Any]] = []
        class_name: str | None = None
        seen: set[str] = set()
        used_urls: set[str] = set()
        for i in order:
            url, ships = class_of[i]
            if not ships or url in used_urls:
                continue
            used_urls.add(url)
            fresh = [s for s in ships if s["name"].lower() not in in_use and s["name"].lower() not in seen]
            if not fresh:
                continue
            if class_name is None:
                class_name = re.sub(r"\s*class\s*$", "", ships[0]["class"] or idx.recs[i].get("class_name") or "", flags=re.I).strip().title() or None
            for s in fresh:
                if len(picked) >= k:
                    break
                seen.add(s["name"].lower())
                picked.append({"name": s["name"], "native": s["native"], "launched": s["launched"], "class": s["class"]})
            if len(picked) >= k:
                break
        if not picked:
            return {"source": "nsdb", "class_name": None, "ships": [], "excluded_in_use": len(in_use), "note": "この国・艦種では使える艦名が見つかりませんでした"}
        return {"source": "nsdb", "class_name": class_name, "ships": picked, "excluded_in_use": len(in_use),
                "note": f"{country} の {C._TYPE_NAME.get(ship_type, ship_type)} の実艦名から（{year} 年に就役中の同艦種の艦名 {len(in_use)} 件は除外）"}


# --------------------------------------------------------------------------------------------------
# 説明文の照合
# --------------------------------------------------------------------------------------------------
_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")


def numbers_in(text: str) -> list[tuple[float, str, int, int]]:
    out = []
    for m in _NUM.finditer(text):
        s = m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else "")
        out.append((float(s), s, m.start(), m.end()))
    return out


def check_explanation_numbers(text: str, source_json: str) -> list[str]:
    """説明文に出てくる数値のうち、source_json（渡した設計結果 JSON）に無いものを返す。行頭の番号（1. 2.）と、単位の付かない 0〜10 の整数は許す。"""
    allowed = {round(v, 4) for v, _s, _a, _b in numbers_in(source_json)}
    bad: list[str] = []
    for v, s, a, b in numbers_in(text):
        line_start = text.rfind("\n", 0, a) + 1
        if re.fullmatch(r"\s*[（(]?\s*", text[line_start:a]) and text[b:b + 1] in (".", ")", "）", "．", "、"):
            continue  # 箇条書きの番号
        if round(v, 4) in allowed:
            continue
        if float(v).is_integer() and 0 <= v <= 10 and not re.match(r"\s*" + UNIT_RE, text[b:b + 5]):
            continue
        bad.append(s)
    return sorted(set(bad), key=lambda x: float(x))


def check_equipment_words(text: str, design: dict[str, Any] | None) -> list[str]:
    """説明文が、設計に無い装備に肯定的に言及していないか。否定文（ない・ません など）は無視。戻り値は疑わしい語。"""
    if not isinstance(design, dict):
        return []
    caps = set(C.caps_of_design(design))
    bad: list[str] = []
    for sent in re.split(r"[。\n]", text):
        if any(n in sent for n in NEG_WORDS):
            continue
        for w, need in EQUIP_WORDS:
            if w in sent and not (need & caps) and w not in bad:
                bad.append(w)
    return bad


KIND_JA = {"gun": "砲", "mg": "機関銃", "torpedo_tube": "魚雷発射管", "ssm": "対艦ミサイル", "sam": "対空ミサイル", "ciws": "近接防御火器", "asw_rocket": "対潜ロケット",
           "depth_charge": "爆雷", "mine": "機雷", "rocket_launcher": "ロケット発射機", "aircraft_helicopter": "搭載ヘリ", "aircraft_plane": "搭載機", "aircraft_seaplane": "搭載水上機",
           "aviation_facility": "航空設備", "minesweeping_gear": "掃海具", "ballistic_missile": "弾道ミサイル", "asw_mortar": "対潜迫撃砲", "asw_weapon": "対潜兵装"}


def design_facts(d: dict[str, Any] | None) -> list[str]:
    """設計 JSON の各値を、単位つきの日本語の 1 行にしたもの（会話役が数値と単位をそのまま写せるように渡す。計算はしない）。"""
    if not isinstance(d, dict):
        return []
    f: list[str] = []
    f.append(f"艦種: {d.get('ship_type')}（細分類 {d.get('ship_type_fine')}）")
    for k, lab in (("displacement_normal_t", "常備排水量"), ("displacement_full_t", "満載排水量"), ("displacement_standard_t", "基準排水量")):
        if d.get(k) is not None:
            f.append(f"{lab}: {d[k]} t")
    for k, lab in (("length_m", "全長"), ("beam_m", "幅"), ("draught_m", "喫水")):
        if d.get(k) is not None:
            f.append(f"{lab}: {d[k]} m")
    if d.get("propulsion"):
        f.append("推進: " + "、".join(d["propulsion"]) + (f"（{d['shafts']} 軸）" if d.get("shafts") is not None else ""))
    for k, lab, u in (("power_hp", "出力", "hp"), ("speed_kn", "最大速力", "kn"), ("endurance_nm", "航続距離", "nm"), ("complement", "乗員", "名")):
        if d.get(k) is not None:
            f.append(f"{lab}: {d[k]} {u}")
    for w in d.get("weapons") or []:
        lab = KIND_JA.get(w.get("kind"), w.get("kind"))
        parts = [lab]
        if w.get("n") is not None:
            parts.append(f"{w['n']} 基" if w.get("kind") in ("gun", "mg", "torpedo_tube", "ciws", "ssm", "sam", "rocket_launcher", "asw_rocket") else f"{w['n']}")
        if w.get("per"):
            parts.append(f"（1 基あたり {w['per']}）")
        if w.get("cal"):
            parts.append(f"口径 {w['cal']} mm")
        f.append("武装: " + " ".join(parts))
    f.append(f"砲門数の合計: {P.X.gun_barrels(d)}、魚雷発射管数の合計: {P.X.torpedo_tubes(d)}、搭載機数: {P.X.aircraft_count(d)}")
    f.append("センサー: " + ("、".join(d["sensors"]) if d.get("sensors") else "なし"))
    if d.get("air"):
        f.append("航空機の種類: " + "、".join(d["air"]))
    arm = d.get("armour")
    if isinstance(arm, dict) and arm:
        f.append("装甲: " + "、".join(f"{k} {v} mm" for k, v in arm.items()))
    return f


def template_explanation(payload: dict[str, Any]) -> str:
    """会話役の説明が数値照合に通らなかったときの、コードだけで作る説明（JSON の値をそのまま並べる）。"""
    st = payload.get("status")
    d = payload.get("design")
    lines: list[str] = []
    ja = {"feasible": "要求どおりに設計できました。", "relaxed": "一部の要求を緩和して設計しました。", "infeasible": "この要求は実現できないと判定されました。"}
    lines.append(ja.get(st, f"status: {st}"))
    for c in payload.get("conflicts") or []:
        lines.append(f"・衝突: {c.get('constraint')} — {c.get('reason')}")
    for r in payload.get("relaxations") or []:
        lines.append(f"・緩和: {r.get('constraint')}（要求 {r.get('requested')} → {r.get('relaxed_to')}）")
    if isinstance(d, dict):
        lines += ["・" + x for x in design_facts(d)]
    nm = payload.get("names") or {}
    if nm.get("ships"):
        lines.append(f"・艦級名: {nm.get('class_name')}、艦名: " + "、".join(s["name"] if isinstance(s, dict) else str(s) for s in nm["ships"]))
    chk = payload.get("check") or {}
    if chk.get("unmet"):
        lines.append("・満たせていない要求: " + ", ".join(chk["unmet"]))
    if chk.get("violations"):
        lines.append("・validator の違反: " + "; ".join(f"{v['code']}（{v['detail']}）" for v in chk["violations"][:4]))
    if payload.get("grounding_problems"):
        lines.append("・注意: 設計AIの拒否の根拠が実データと合いません — " + "; ".join(payload["grounding_problems"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------------
# クライアント
# --------------------------------------------------------------------------------------------------
class GemmaClient:
    """会話役（OpenAI 互換）。enable_thinking=false を必ず付ける。"""

    def __init__(self, url: str = DEFAULT_GEMMA_URL, model: str = DEFAULT_GEMMA_MODEL, timeout: float = 180.0):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout

    def health(self) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=3) as r:
                return {"ok": r.status == 200, "url": self.url, "model": self.model}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "url": self.url, "model": self.model, "detail": f"{type(e).__name__}: {e}"}

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.3, max_tokens: int = 900, seed: int | None = None) -> str:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False,
                                "chat_template_kwargs": {"enable_thinking": False}}
        if seed is not None:
            body["seed"] = seed
        req = urllib.request.Request(self.url + "/v1/chat/completions", json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            o = json.load(r)
        return o["choices"][0]["message"]["content"] or ""


# --------------------------------------------------------------------------------------------------
# セッションとオーケストレータ
# --------------------------------------------------------------------------------------------------
def _short(x: Any, n: int = 400) -> str:
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


class ChatSession:
    def __init__(self, sid: str):
        self.id = sid
        self.lock = threading.Lock()
        self.created = time.time()
        self.gemma_history: list[dict[str, str]] = []  # 会話役の履歴（user / assistant 交互）
        self.dv1_messages: list[dict[str, str]] = P.new_conversation()  # 設計役の会話（学習データの書式）
        self.req: dict[str, Any] | None = None
        self.latest: dict[str, Any] | None = None  # 直前の設計結果（UI 用）
        self.display: list[dict[str, Any]] = []  # 画面のチャット欄 [{role, text, log?}]
        self.dv1_log: list[dict[str, Any]] = []  # 設計役との往復ログ
        self.names: dict[str, Any] | None = None
        self.n_designs = 0

    def snapshot(self) -> dict[str, Any]:
        return {"session_id": self.id, "display": self.display, "latest": self.latest, "dv1_log": self.dv1_log, "req": self.req, "dv1_messages": self.dv1_messages,
                "gemma_history": self.gemma_history, "names": self.names}


class ChatEngine:
    def __init__(self, pg: P.Playground, gemma: Any | None = None, namer: ShipNamer | None = None, log=print):
        self.pg = pg
        self.gemma = gemma or GemmaClient()
        self.namer = namer or ShipNamer(pg)
        self.sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()
        self.log = log

    # ------------- sessions
    def session(self, sid: str | None) -> ChatSession:
        with self._lock:
            if sid and sid in self.sessions:
                return self.sessions[sid]
            sid = sid or f"s{int(time.time() * 1000):x}"
            s = ChatSession(sid)
            self.sessions[sid] = s
            return s

    def reset(self, sid: str | None) -> dict[str, Any]:
        with self._lock:
            if sid:
                self.sessions.pop(sid, None)
        return self.session(None).snapshot()

    def meta(self) -> dict[str, Any]:
        g = self.gemma.health() if hasattr(self.gemma, "health") else {"ok": True}
        return {"gemma": g, "gemma_url": getattr(self.gemma, "url", None), "gemma_model": getattr(self.gemma, "model", None)}

    # ------------- gemma helpers
    def _state_text(self, s: ChatSession) -> str:
        if not s.req:
            return "\n# 現在の状態\nまだ設計の依頼はありません。"
        lat = s.latest or {}
        d = ((lat.get("obj") or {}).get("design")) if isinstance(lat.get("obj"), dict) else None
        cur = ""
        if isinstance(d, dict):
            cur = (f"\n直前の設計の主な値（程度が曖昧な変更の基準にする）: 速力 {d.get('speed_kn')} kn、排水量 {d.get('displacement_normal_t') or d.get('displacement_full_t') or d.get('displacement_standard_t')} t、"
                   f"最大主砲口径 {C.main_gun_mm(d)} mm、砲門数 {P.X.gun_barrels(d)}、魚雷発射管 {P.X.torpedo_tubes(d)}、搭載機 {P.X.aircraft_count(d)}、乗員 {d.get('complement')}")
        return ("\n# 現在の状態（modify のときの元）\n現在の要求 req: " + json.dumps(s.req, ensure_ascii=False) + cur +
                f"\n直前の設計の status: {lat.get('status')}。変更の依頼では changes に「変えたキーだけ」を出す。別の艦の依頼なら design を出す。")

    def _gemma_call(self, s: ChatSession, messages: list[dict[str, str]], temperature: float, max_tokens: int, log: list[dict[str, Any]], tag: str) -> str:
        t0 = time.time()
        text = self.gemma.chat(messages, temperature=temperature, max_tokens=max_tokens)
        log.append({"step": tag, "elapsed_s": round(time.time() - t0, 2), "output": _short(text, 1200)})
        return text

    def _get_action(self, s: ChatSession, user_text: str, log: list[dict[str, Any]]) -> dict[str, Any]:
        """会話役にユーザー発話を渡し、(say, 検証済みアクション or None) を得る。不正なら理由を返して再生成させる。"""
        sys_msg = {"role": "system", "content": build_gemma_system(self._state_text(s))}
        hist = s.gemma_history[-HISTORY_KEEP:]
        while hist and hist[0]["role"] != "user":
            hist = hist[1:]
        msgs = [sys_msg] + hist + [{"role": "user", "content": user_text}]
        last_err = ""
        for attempt in range(MAX_REQ_RETRIES + 1):
            text = self._gemma_call(s, msgs, 0.3 if attempt == 0 else 0.2, 900, log, "gemma_action" if attempt == 0 else f"gemma_action_retry{attempt}")
            parsed = parse_gemma_output(text)
            err = parsed["error"]
            built = None
            if not err and parsed["action"] is not None:
                act = parsed["action"]
                kind = act.get("action")
                try:
                    if kind in ("design", "new"):
                        built = {"kind": "design", **build_design_action(self.pg, act)}
                    elif kind == "modify":
                        if not s.req:
                            raise ActionError("まだ設計がないので modify はできません。action は design を使ってください")
                        built = {"kind": "modify", **build_modify_action(self.pg, act, s.req)}
                    else:
                        raise ActionError(f"action は design か modify にしてください: {kind!r}")
                except ActionError as e:
                    err = str(e)
            if not err and built and built["kind"] == "modify" and any(c["value"] is None for c in built["changes"]) and not any(w in user_text for w in REMOVE_WORDS):
                err = "ユーザーは制約を外すよう言っていないので、changes で null（制約を外す）にしないでください。具体的な数値に変更してください"
            if err:
                last_err = err
                log.append({"step": "gemma_action_invalid", "error": err})
                msgs = msgs + [{"role": "assistant", "content": text}, {"role": "user", "content": f"[システム] 出力に問題があります: {err}\n同じ形式（<say> と <action>）で出し直してください。"}]
                continue
            return {"say": parsed["say"], "action": built, "raw": text, "history_user": user_text, "history_assistant": text}
        return {"say": "", "action": None, "error": last_err, "raw": text, "history_user": user_text, "history_assistant": text}

    # ------------- design-v1
    def _dv1_turn(self, messages: list[dict[str, str]], user_content: str, req: dict[str, Any], gen: dict[str, Any], st: dict[str, Any], log: list[dict[str, Any]], label: str):
        sent = messages + [P.G.msg("user", user_content)]
        t0 = time.time()
        r = self.pg.chat(sent, gen["temperature"], gen.get("seed"), gen.get("max_tokens", 2048), st.get("design_url"), st.get("design_model"))
        msgs = sent + [P.G.msg("assistant", r["content"])]
        an = self.pg.analyze(req, r["content"])
        self.pg_log(log, label, user_content, r, an, gen, time.time() - t0)
        return msgs, r["content"], an

    @staticmethod
    def pg_log(log: list[dict[str, Any]], label: str, user_content: str, r: dict[str, Any], an: dict[str, Any], gen: dict[str, Any], dt: float) -> None:
        g = an.get("grounding") or {}
        log.append({"step": "dv1", "label": label, "temperature": gen["temperature"], "seed": gen.get("seed"), "elapsed_s": round(dt, 2), "user": _short(user_content, 3000),
                    "assistant": r["content"], "status": an.get("status"), "violations": len(an.get("violations") or []), "validator_ok": an.get("validator_ok"),
                    "grounding": g.get("verdict"), "messages": [m["text"] for m in an.get("messages", [])]})

    def _run_attempt(self, base_msgs: list[dict[str, str]], user_content: str, req: dict[str, Any], gen: dict[str, Any], st: dict[str, Any], log: list[dict[str, Any]], label: str):
        """1 回の試行: design-v1 に送り、違反があれば repair ターンで最大 max_repairs 回修正させる。"""
        msgs, content, an = self._dv1_turn(base_msgs, user_content, req, gen, st, log, label)
        repairs = 0
        while repairs < int(st.get("max_repairs", 2)) and an.get("status") in ("feasible", "relaxed") and an.get("repair_violations"):
            fb = P.repair_message(an["repair_violations"])
            repairs += 1
            msgs, content, an = self._dv1_turn(msgs, fb, req, gen, st, log, f"{label}/repair{repairs}")
        return msgs, content, an, repairs

    @staticmethod
    def _refusal_ungrounded(an: dict[str, Any]) -> bool:
        return an.get("status") in ("relaxed", "infeasible") and (an.get("grounding") or {}).get("verdict") == "mismatch"

    def _design_with_resample(self, base_msgs, user_content, req, st, log):
        gens = [{"temperature": 0.0, "seed": None}] + [{"temperature": 0.7, "seed": sd} for sd in RESAMPLE_SEEDS[: int(st.get("max_resamples", 2))]]
        attempts: list[dict[str, Any]] = []
        for i, g in enumerate(gens):
            msgs, content, an, repairs = self._run_attempt(base_msgs, user_content, req, g, st, log, f"attempt{i}")
            attempts.append({"i": i, "gen": g, "status": an.get("status"), "grounding": (an.get("grounding") or {}).get("verdict"), "repairs": repairs})
            bad = an.get("status") is None or self._refusal_ungrounded(an)
            if not bad:
                return msgs, content, an, repairs, attempts, False
        return msgs, content, an, repairs, attempts, self._refusal_ungrounded(an)

    # ------------- names / explanation
    def _names(self, s: ChatSession, req: dict[str, Any], design: dict[str, Any] | None, st: dict[str, Any], log: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not design:
            return None
        country = req["country"]
        prem = req.get("premise") or {}
        fictional = prem.get("type") == "fictional_nation"
        if not fictional:
            try:
                got = self.namer.names_for(country, design.get("ship_type") or req["ship_type"], req["year"])
            except Exception as e:  # noqa: BLE001
                got = None
                log.append({"step": "names_error", "error": f"{type(e).__name__}: {e}"})
            if got and got.get("ships"):
                log.append({"step": "names", "source": "nsdb", "class_name": got["class_name"], "ships": [x["name"] for x in got["ships"]], "excluded_in_use": got["excluded_in_use"]})
                return got
            if got is not None:
                log.append({"step": "names", "source": "nsdb", "empty": True})
        msgs = [{"role": "system", "content": NAMES_SYSTEM},
                {"role": "user", "content": f"国: {country.replace('-', ' ')}（{'架空国家' if fictional else '実在国'}）、年: {req['year']}、艦種: {C._TYPE_NAME.get(req['ship_type'], req['ship_type'])}"}]
        for attempt in range(3):
            try:
                text = self._gemma_call(s, msgs, 0.7, 200, log, "gemma_names")
                m = re.search(r"\{.*\}", text, re.S)
                o = json.loads(m.group(0)) if m else None
                ships = [str(x).strip() for x in (o or {}).get("ship_names", []) if isinstance(x, str) and x.strip() and not re.search(r"\d", x)]
                cn = str((o or {}).get("class_name") or "").strip()
                if cn and 3 <= len(ships) <= 6:
                    return {"source": "gemma", "class_name": cn, "ships": [{"name": n, "native": None, "launched": None} for n in ships[:4]],
                            "note": "架空国家のため会話役が命名しました"}
            except Exception as e:  # noqa: BLE001
                log.append({"step": "names_error", "error": f"{type(e).__name__}: {e}"})
        c = country.replace("-", " ")
        return {"source": "fallback", "class_name": f"{c} {req['ship_type']}", "ships": [{"name": f"{c} {i + 1}", "native": None, "launched": None} for i in range(3)], "note": "命名に失敗したので仮の名前です"}

    def _payload(self, req, kind, an, names, repairs, attempts, ungrounded, design, obj, fills) -> dict[str, Any]:
        g = an.get("grounding") or {}
        unmet = [c["id"] for c in an.get("checks", []) if c["ok"] is not True] if obj and obj.get("status") == "feasible" else (an.get("relaxed_hidden") or [])
        return {"status": obj.get("status") if obj else None, "req": req, "design": design, "design_facts": design_facts(design), "conflicts": (obj or {}).get("conflicts"), "relaxations": (obj or {}).get("relaxations"),
                "check": {"requirements_met": bool(obj) and obj.get("status") == "feasible" and not unmet and an.get("validator_ok", False), "validator_ok": an.get("validator_ok"),
                          "unmet": unmet, "violations": [{"code": v["code"], "detail": v["detail"]} for v in (an.get("violations") or [])][:6], "auto_repairs": repairs,
                          "resamples": max(0, len(attempts) - 1)},
                "grounding_problems": (g.get("problems") or []) + [f"{i['constraint']}: {i['detail']}" for i in g.get("items", []) if i.get("verdict") == "mismatch"] if ungrounded else [],
                "names": {"class_name": (names or {}).get("class_name"), "ships": [s["name"] for s in (names or {}).get("ships", [])]} if names else None,
                "filled_by_system": fills}

    def _explain(self, s: ChatSession, payload: dict[str, Any], design: dict[str, Any] | None, log: list[dict[str, Any]]) -> tuple[str, str]:
        src = json.dumps(payload, ensure_ascii=False)
        msgs = [{"role": "system", "content": EXPLAIN_SYSTEM}, {"role": "user", "content": "設計結果 JSON:\n" + src + "\n\nこの結果を日本語で説明してください。"}]
        for attempt in range(MAX_EXPLAIN_RETRIES + 1):
            try:
                text = self._gemma_call(s, msgs, 0.2 if attempt else 0.3, 700, log, "gemma_explain" if attempt == 0 else f"gemma_explain_retry{attempt}").strip()
            except Exception as e:  # noqa: BLE001
                log.append({"step": "gemma_explain_error", "error": f"{type(e).__name__}: {e}"})
                break
            bad_nums = check_explanation_numbers(text, src)
            bad_eq = check_equipment_words(text, design)
            log.append({"step": "explain_check", "attempt": attempt, "bad_numbers": bad_nums, "bad_equipment": bad_eq})
            if not bad_nums and not bad_eq and text:
                return text, "gemma"
            fb = []
            if bad_nums:
                fb.append("JSON に無い数値があります: " + ", ".join(bad_nums) + "。JSON の値をそのまま使ってください（換算・計算をしない）")
            if bad_eq:
                fb.append("設計に無い装備に触れています: " + ", ".join(bad_eq))
            msgs = msgs + [{"role": "assistant", "content": text}, {"role": "user", "content": "[システム] " + "。".join(fb) + "。直して説明し直してください。"}]
        return template_explanation(payload), "template"

    # ------------- main entry
    def handle(self, sid: str | None, text: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        s = self.session(sid)
        st = {"max_repairs": 2, "max_resamples": 2, **(settings or {})}
        with s.lock:
            return self._handle(s, text.strip(), st)

    def _handle(self, s: ChatSession, text: str, st: dict[str, Any]) -> dict[str, Any]:
        if not text:
            raise ValueError("メッセージが空です")
        log: list[dict[str, Any]] = []
        s.display.append({"role": "user", "text": text})
        try:
            if st.get("gemma_url") or st.get("gemma_model"):
                self.gemma.url = (st.get("gemma_url") or self.gemma.url).rstrip("/")
                self.gemma.model = st.get("gemma_model") or self.gemma.model
            res = self._get_action(s, text, log)
        except (urllib.error.URLError, OSError) as e:
            s.display.append({"role": "assistant", "text": f"会話役（gemma）に接続できません: {e}", "log": log, "error": True})
            return s.snapshot()
        act = res.get("action")
        if act is None:
            say = res["say"] or ("（会話役の出力を解釈できませんでした: " + str(res.get("error")) + "）" if res.get("error") else "")
            s.gemma_history += [{"role": "user", "content": text}, {"role": "assistant", "content": res["history_assistant"]}]
            s.display.append({"role": "assistant", "text": say, "log": log, "error": bool(res.get("error"))})
            return s.snapshot()
        try:
            final = self._run_pipeline(s, act, st, log, res)
        except (urllib.error.URLError, OSError) as e:
            s.display.append({"role": "assistant", "text": f"設計AI（design-v1）に接続できません: {e}", "log": log, "error": True})
            return s.snapshot()
        s.gemma_history += [{"role": "user", "content": text}, {"role": "assistant", "content": res["history_assistant"]},
                            {"role": "user", "content": "[システム通知] 設計結果をユーザーに説明しました。status=" + str(final["status"]) + "。要約: " + _short(final["summary"], 300)},
                            {"role": "assistant", "content": "<say>" + _short(final["explanation"], 400) + "</say>"}]
        s.gemma_history = s.gemma_history[-HISTORY_KEEP * 2:]
        say = res["say"]
        s.display.append({"role": "assistant", "say": say, "text": final["explanation"], "explanation_source": final["explanation_source"], "log": log, "status": final["status"],
                          "grounding_notes": final.get("notes") or [], "check_notes": final.get("check_notes") or []})
        return s.snapshot()

    def _run_pipeline(self, s: ChatSession, act: dict[str, Any], st: dict[str, Any], log: list[dict[str, Any]], res: dict[str, Any]) -> dict[str, Any]:
        pg = self.pg
        kind = act["kind"]
        req = act["req"]
        variant = 0
        log.append({"step": "action", "kind": kind, "req": req, "warnings": act.get("warnings"), "fills": act.get("fills"), "changes": act.get("changes")})
        if kind == "modify":
            prev = s.req
            en, _ja = P.change_pieces(prev, req)
            if not en:
                msg = "変更がありませんでした（要求は現在と同じです）"
                log.append({"step": "no_change"})
                return {"status": (s.latest or {}).get("status"), "summary": msg, "explanation": msg, "explanation_source": "system"}
            request = P.modify_sentence(en, variant)
            nbr = pg.neighbors(req) if (prev.get("country") != req.get("country") or prev.get("year") != req.get("year")) else []
            turn_kind = "modify"
            user_content = P.user_message(request, req, nbr)
        else:
            premise_text = None
            if req.get("premise"):
                _pm, premise_text = P.build_premise(dict(req["premise"]), req["country"], variant)
            request = K.request_text_v1(req, variant, premise_text=premise_text)
            nbr = pg.neighbors(req)
            turn_kind = "request" if len(s.dv1_messages) <= 1 else "context"
            user_content = P.user_message(request, req, nbr)
        log.append({"step": "dv1_request", "kind": turn_kind, "request": request, "nbr": [P_nbr_desc(n) for n in nbr]})
        msgs, content, an, repairs, attempts, ungrounded = self._design_with_resample(s.dv1_messages, user_content, req, st, log)
        s.dv1_messages = msgs
        s.req = req
        obj = EV_parse(content)
        design = obj.get("design") if obj else None
        names = None
        keep_names = (kind == "modify" and s.names and not any(c["field"] in ("country", "year") for c in (act.get("changes") or [])))
        if keep_names:
            names = s.names
        else:
            names = self._names(s, req, design if isinstance(design, dict) else None, st, log)
        s.names = names
        payload = self._payload(req, turn_kind, an, names, repairs, attempts, ungrounded, design, obj, act.get("fills"))
        explanation, source = self._explain(s, payload, design if isinstance(design, dict) else None, log)
        s.n_designs += 1
        s.latest = {"kind": turn_kind, "request": request, "req": req, "nbr": nbr, "status": an.get("status"), "obj": obj, "analysis": an, "names": names,
                    "content": content, "repairs": repairs, "attempts": attempts, "ungrounded_refusal": ungrounded, "fills": act.get("fills"), "warnings": act.get("warnings"),
                    "notes": act.get("notes")}
        return {"status": an.get("status"), "summary": payload, "explanation": explanation, "explanation_source": source, "notes": payload.get("grounding_problems") or [], "check_notes": _check_notes(payload)}


def _check_notes(payload: dict[str, Any]) -> list[str]:
    """修正（repair）の上限後も残った未充足・違反を、説明文とは別に必ず画面へ出す。"""
    chk = payload.get("check") or {}
    out = []
    if chk.get("unmet"):
        out.append("満たせていない要求: " + ", ".join(chk["unmet"]))
    if chk.get("violations"):
        out.append("validator の違反が残っています: " + "; ".join(f"{v['code']}（{v['detail']}）" for v in chk["violations"][:4]))
    return out


def P_nbr_desc(n: dict[str, Any]) -> str:
    return f"{n.get('cn')} {n.get('y')} {n.get('ft') or n.get('ty')} dn={n.get('dn')} v={n.get('v')}"


def EV_parse(content: str) -> dict[str, Any] | None:
    obj, _s, loose = P.EV.parse_text(content)
    return obj if loose and isinstance(obj, dict) else None
