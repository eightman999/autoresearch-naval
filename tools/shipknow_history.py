"""NSDB の Modernizations / Naval_service / Project_history から艦歴・改装の年表を規則ベースで抽出する（LLM 不使用）。

    .venv/bin/python tools/shipknow_history.py stats                 # 全 CLASS の抽出率（Modernizations 行・節ごと）
    .venv/bin/python tools/shipknow_history.py audit --n 20 --seed 1 # ランダム N 艦級の原文と抽出結果を並べて表示（目視用）
    .venv/bin/python tools/shipknow_history.py show --id c00123      # 1 艦級

イベント 1 件:
    {"year": 1942, "year_lo": 1942, "year_end": None, "prec": "exact|decade|approx",
     "src": "mod|svc|proj", "etype": str, "text": <1 文・最大 200 字>, "kinds": [装備 kind ...],
     "add": [kind ...], "remove": [kind ...]}

* mod  : Modernizations は 1 行 1 改装。行頭の日付（``7.1941:`` ``mid-1990s, all:`` ``(Spring 1942, Bombay DYd), Ship:`` ``1935 - 1939, X:``）
         を年に、``:`` の後ろを text にする。``- 撤去; + 追加`` の形なら add / remove を分ける（接頭辞が無ければ「その時点の状態」
         なので kinds すべてを add 側とみなす）。艦名の接頭辞は text から外す（艦名で来歴を引かないため + 短くするため）。
* svc  : Naval_service は文に分け、4 桁年を含む文だけを出来事にする（``20.12.1941`` ``in March 1942`` ``1942``）。
* proj : Project_history も同様。最大 ``MAX_PROJ`` 件、装備語を含む文を優先。
kinds の語彙: gun mg torpedo_tube asw_weapon ssm sam ciws ballistic_missile rocket_launcher helicopter aircraft radar sonar esm_ecm mine
              propulsion armour
Navypedia の文章の丸写しを避けるため text は 200 字で切る（neighbor 表示用には更に ``shorten`` で短くする）。
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "nsdb" / "NSDB.db"

MAX_TEXT = 200
MAX_PROJ = 6
MAX_SVC = 10
MIN_SENT = 14
PREFIX_LEN = 50
Y_MIN, Y_MAX = 1500, 2035

KIND_VOCAB = ["gun", "mg", "torpedo_tube", "asw_weapon", "ssm", "sam", "ciws", "ballistic_missile", "rocket_launcher", "helicopter",
              "aircraft", "radar", "sonar", "esm_ecm", "mine", "propulsion", "armour"]

# --------------------------------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------------------------------
_YEAR = re.compile(r"(?<!\d)(1[5-9]\d\d|20[0-3]\d)(s?)(?![\dA-Za-z])")  # "1923DT" (a torpedo model) is not a year
_MOD = re.compile(r"\b(early|mid|late)[-\s]*$", re.I)
_NONE_LINE = re.compile(r"^\s*(none|no significant events|no modernizations?)\.?\s*$", re.I)
_NOISE = re.compile(r"(©|many thanks|\bI thank\b|additional information on this page|correction of information on this page|^\s*:?\s*no significant events)", re.I)


def _year_point(y: int, decade: bool, modifier: str | None) -> int:
    if not decade:
        return y
    off = {"early": 2, "mid": 5, "late": 8}.get((modifier or "").lower(), 5)
    return y + off


def _year_matches(head: str) -> list[re.Match]:
    """4 桁年の候補。``PC-1586`` ``SC1042`` ``No.1650`` のような艦番号は年と見なさない（``mid-1990s`` は許可）。"""
    out = []
    for m in _YEAR.finditer(head):
        if not (Y_MIN <= int(m.group(1)) <= Y_MAX):
            continue
        before = head[max(0, m.start() - 6): m.start()]
        if re.search(r"[A-Za-z]$", before) or re.search(r"(?i)(?:No|Nr|Pr|Type)\.?\s*$", before):
            continue
        if re.search(r"[A-Za-z]-$", before) and not re.search(r"(?i)(?:early|mid|late)-$|\ds-$", before):
            continue
        out.append(m)
    return out


def parse_year_head(head: str) -> dict[str, Any] | None:
    """日付を含む見出し（行頭〜最初の ``:``）から年を取る。無ければ None。範囲 ``1935 - 1939`` は year_end も返す。"""
    ms = _year_matches(head)
    if not ms:
        return None
    m0 = ms[0]
    y0, dec0 = int(m0.group(1)), bool(m0.group(2))
    mod = _MOD.search(head[: m0.start()])
    out: dict[str, Any] = {"year": _year_point(y0, dec0, mod.group(1) if mod else None), "year_lo": y0, "year_end": None,
                          "prec": "decade" if dec0 else ("approx" if mod else "exact")}
    if len(ms) >= 2:
        m1 = ms[1]
        between = head[m0.end(): m1.start()]
        if re.fullmatch(r"\s*(?:[-–—/&]|to|and)\s*", between):
            y1, dec1 = int(m1.group(1)), bool(m1.group(2))
            out["year_end"] = y1 + 9 if dec1 else y1
            if dec0 and not dec1:
                out["prec"] = "decade"
    return out


# --------------------------------------------------------------------------------------------------
# kinds
# --------------------------------------------------------------------------------------------------
_GUN_FMT = re.compile(r"(?<![\d./])\d+\s*(?:x\s*\d+\s*)?-\s*(\d+(?:\.\d+)?)\s*/\s*\d+")  # "2 x 1 - 20/70", "9 - 20/65"
_RE = {
    "torpedo_tube": re.compile(r"\bTT\b|\bTA\b|torpedo[- ]?tubes?|\d\s*-\s*\d{3}\s*TT|torpedoes?\b(?![- ]?boats?|[- ]?destroyers?)", re.I),
    "asw_weapon": re.compile(r"\bDCT\b|\bDCR\b|\bDC\b|depth[- ]?charges?|hedgehog|\bsquid\b|\blimbo\b|\bASW\b|ASWRL|\bRBU\b|\bMILAS\b|\bASROC\b|\bMk\s*32\b", re.I),
    "ssm": re.compile(r"\bSSM\b|exocet|harpoon|otomat|\bstyx\b|sea eagle|anti-ship missiles?|\bSS-N|\bTeseo\b|\bGabriel\b|\bC-80\d\b|\bYJ-\d+", re.I),
    "sam": re.compile(r"\bSAM\b|sea sparrow|sea dart|sea ?cat\b|terrier|tartar|crotale|\bASTER\b|\bRAM\b|\bSA-N|\bMistral\b|\bSimbad\b|\bSea Wolf\b", re.I),
    "ciws": re.compile(r"\bCIWS\b|phalanx|goalkeeper|kashtan|\bAK-630|sea zenith|\bDARDO\b", re.I),
    "ballistic_missile": re.compile(r"\bSLBM\b|\bICBM\b|ballistic missiles?|\bPolaris\b|\bTrident\b", re.I),
    "rocket_launcher": re.compile(r"rocket[- ]?launchers?|\bMRL\b|\bRL\b", re.I),
    "helicopter": re.compile(r"helicopters?|\bhelos?\b|\bLynx\b|sea king|\bKa-2\d|helideck|helicopter deck", re.I),
    "aircraft": re.compile(r"seaplanes?|floatplanes?|catapults?|aircraft|\bplanes?\b|hangar|flight deck|\bWalrus\b|\bKingfisher\b|\bSeafox\b", re.I),
    "radar": re.compile(r"radars?\b|\bSG\b|\bSC\b|\bSK\b|\bSA\b|\bSF\b|\bSX\b|\bSJ\b|\bSO\b|\bSD\b|\bSR\b|\bCXAM\b|\bCXAS\b|\bFuMO\b|\btype \d{3}\b(?=.*radar)", re.I),
    "sonar": re.compile(r"sonars?\b|asdic|hydrophones?|\bhull[- ]mounted\b.*\bsonar\b", re.I),
    "esm_ecm": re.compile(r"\bECM\b|\bESM\b|\bFuMB\b|\bMetox\b|jammers?|\bchaff\b|\bdecoys?\b", re.I),
    "mine": re.compile(r"\bmines?\b|minelay|minesweep", re.I),
    "propulsion": re.compile(r"re-?engined|re-?boilered|\bboilers?\b|\bturbines?\b|\bdiesels?\b|\bengines?\b|machinery|reciprocating|\bshafts?\b|\bscrews?\b|\bpropellers?\b|"
                             r"gas turbine|\bnuclear\b|reactor|\bmotors?\b", re.I),
    "armour": re.compile(r"\barmou?r\b|\bbelt\b|\bplating\b|\bbulges?\b|\bblisters?\b|conning tower", re.I),
}
_GUN_WORDS = re.compile(r"\bguns?\b|carronades?|\bpdr\b|\bSBML\b|\bMLR\b|\bBL\b|\bQF\b|\bhowitzers?\b|\d+\s*mm\s+(?:guns?|dp|aa)\b|\bAA\b|\bDP\b", re.I)


def kinds_in(text: str) -> list[str]:
    ks: set[str] = set()
    for m in _GUN_FMT.finditer(text):
        cal = float(m.group(1))
        ks.add("gun" if cal >= 20 else "mg")
    if "gun" not in ks and _GUN_WORDS.search(text):
        ks.add("gun")
    for k, rx in _RE.items():
        if rx.search(text):
            ks.add(k)
    # 'SA' / 'SC' / ... only mean radar next to the word radar; bare letters in a title-like text are noise
    if "radar" in ks and not re.search(r"radars?\b|\bCXAM\b|\bCXAS\b|\bFuMO\b", text, re.I):
        ks.discard("radar")
    if "mine" in ks and not re.search(r"\bmines?\b|minelay|minesweep", text, re.I):
        ks.discard("mine")
    return [k for k in KIND_VOCAB if k in ks]


# --------------------------------------------------------------------------------------------------
# text utilities
# --------------------------------------------------------------------------------------------------
def shorten(text: str, n: int = MAX_TEXT) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[: n - 1]
    sp = cut.rfind(" ")
    if sp > n * 0.6:
        cut = cut[:sp]
    return cut.rstrip(" ,;:-") + "…"


def _lines(text: str | None) -> list[str]:
    if not text:
        return []
    return [l.strip() for l in re.split(r"\n+", text) if l.strip()]


# --------------------------------------------------------------------------------------------------
# Modernizations
# --------------------------------------------------------------------------------------------------
def _split_head_body(line: str) -> tuple[str, str]:
    """``7.1941: + 2 x 4 ...`` -> ("7.1941", "+ 2 x 4 ...").  ``(Spring 1942, Bombay DYd), X, Y: ...`` -> head up to the first ':' outside parentheses."""
    depth = 0
    for i, ch in enumerate(line[:400]):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            return line[:i], line[i + 1:].strip()
    return "", line


def _ops(body: str) -> tuple[list[str], list[str]]:
    """``- A; + B`` -> (add kinds of B, remove kinds of A).  Chunks without a sign are the state after the change (-> add)."""
    add: set[str] = set()
    rem: set[str] = set()
    chunks = [c.strip() for c in body.split(";") if c.strip()]
    signed = False
    for c in chunks:
        if not (c.startswith("+") or c.startswith("-")) and re.search(r"\b(?:was|were|been)\s+(?:removed|disarmed|dismounted|landed|deleted)\b|\bremoved\b", c):
            signed = True
            rem.update(kinds_in(c))
        elif c.startswith("+"):
            signed = True
            add.update(kinds_in(c[1:]))
        elif re.match(r"^-\s", c) or re.match(r"^-\s*\d", c):
            signed = True
            rem.update(kinds_in(c[1:]))
        else:
            add.update(kinds_in(c))
    if not signed:
        rem = set()
    return [k for k in KIND_VOCAB if k in add], [k for k in KIND_VOCAB if k in rem]


def parse_modernizations(text: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in _lines(text):
        if _NONE_LINE.match(line) or _NOISE.search(line):
            continue
        head, body = _split_head_body(line)
        yd = parse_year_head(head) if head else None
        if yd is None:  # no dated head: a year in the first 70 characters ("Re-armed in 1943: ...")
            yd = parse_year_head(line[:70])
            body = line
        if yd is None:
            continue
        body = body.strip()
        if not body:
            continue
        add, rem = _ops(body)
        kinds = [k for k in KIND_VOCAB if k in set(add) | set(rem)]
        out.append({**yd, "src": "mod", "etype": "refit", "text": shorten(body), "kinds": kinds, "add": add, "remove": rem})
    return out


# --------------------------------------------------------------------------------------------------
# Naval_service / Project_history (sentence level)
# --------------------------------------------------------------------------------------------------
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-ZА-Я№«\"(\[0-9])")
_ETYPE = [
    ("loss", re.compile(r"\b(sunk|lost|mined|scuttled|torpedoed|blown up|foundered|wrecked|ran aground|capsized|destroyed|bombed)\b", re.I)),
    ("capture", re.compile(r"\b(captured|seized|interned|salvaged)\b", re.I)),
    ("transfer", re.compile(r"\b(transferred|handed over|sold to|purchased|acquired|delivered|leased|loaned|donated|gifted|returned to (?:the )?(?:US|USA|USSR|owner|owners|Britain|Germany|France))\b", re.I)),
    ("conversion", re.compile(r"\b(converted|reclassified|re-?rated|renamed|re-?designated|rebuilt|became|used as|reduced to)\b", re.I)),
    ("disposal", re.compile(r"\b(stricken|scrapped|broken up|discarded|paid off|decommissioned|sold|hulked|laid up|reserve|retired|disposed)\b", re.I)),
    ("commission", re.compile(r"\b(commissioned|launched|laid down|completed|entered service|re-?commissioned|ordered|approved|designed)\b", re.I)),
    ("damage", re.compile(r"\b(damaged|collision|collided|fire)\b", re.I)),
]


def classify_event(sentence: str, default: str = "service") -> str:
    """最初に現れる出来事語で分類する（"X was sunk ... later salvaged" -> loss）。"""
    best: tuple[int, str] | None = None
    for name, rx in _ETYPE:
        m = rx.search(sentence)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else default


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for para in _lines(text):
        para = para.split("©")[0]  # "... 17.8.1944. © Ivan Gogin, 2015": the credit is glued to the last sentence
        for s in _SENT.split(para):
            s = s.strip().lstrip(":;, ")
            if s:
                out.append(s)
    return out


def _dated_sentences(text: str | None, src: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not text or _NONE_LINE.match(text.strip()):
        return out
    for s in _sentences(text):
        if _NOISE.search(s) or _NONE_LINE.match(s) or len(s) < MIN_SENT:
            continue
        yd = parse_year_head(s)
        if yd is None:
            continue
        et = classify_event(s, "design" if src == "proj" else "service")
        ks = kinds_in(s) if (src == "proj" or et == "conversion") else []  # service sentences mention torpedoes / aircraft as attackers, not as equipment
        out.append({**yd, "src": src, "etype": et, "text": shorten(s), "kinds": ks, "add": [], "remove": []})
    return out


def parse_naval_service(text: str | None) -> list[dict[str, Any]]:
    return _dated_sentences(text, "svc")[:MAX_SVC]


def parse_project_history(text: str | None) -> list[dict[str, Any]]:
    ev = _dated_sentences(text, "proj")
    ev.sort(key=lambda e: (-(len(e["kinds"]) > 0), e["year"]))  # equipment sentences first, then chronological
    return sorted(ev[:MAX_PROJ], key=lambda e: e["year"])


def extract_events(modernizations: str | None, naval_service: str | None, project_history: str | None) -> list[dict[str, Any]]:
    ev = parse_modernizations(modernizations) + parse_naval_service(naval_service) + parse_project_history(project_history)
    order = {"proj": 0, "mod": 1, "svc": 2}
    ev.sort(key=lambda e: (e["year"], order[e["src"]]))
    return ev


# --------------------------------------------------------------------------------------------------
# neighbour display
# --------------------------------------------------------------------------------------------------
_DISPLAY_PREF = {"mod": 0, "svc": 1, "proj": 2}


def timeline(events: list[dict[str, Any]], max_n: int = 3, max_year: int | None = None, max_chars: int = 90,
             forbidden: list[str] | None = None, exclude_prefixes: set[str] | None = None) -> list[list[Any]]:
    """neighbour 用の短い年表 [[year, text], ...]（年順）。

    * max_year を超える出来事は出さない（その年の要求から見て未来の情報は見せない）。
    * 装備を足した改装（mod, add あり）を最優先 → 引き渡し・改造・就役などの service → 設計史の順で max_n 件選ぶ。
    * forbidden: 出してはいけない語（対象艦の艦名・艦級名など。大文字小文字無視、単語境界）。含む出来事は捨てる。
    * exclude_prefixes: 先頭 PREFIX_LEN 字（小文字）がこの集合にある出来事は捨てる（姉妹艦級のページが対象艦と同じ文をそのまま載せていることがあるため）。
    """
    pats = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", re.I) for w in (forbidden or []) if len(w) >= 3]

    def score(e: dict[str, Any]) -> tuple[int, int, int]:
        has_equip = 0 if (e["src"] == "mod" and e["add"]) else 1
        et = 0 if e["etype"] in ("transfer", "conversion", "commission") else (2 if e["etype"] in ("loss", "disposal") else 1)
        return (has_equip if e["src"] == "mod" else 1 + _DISPLAY_PREF[e["src"]], et, e["year"])

    pool = []
    for e in events:
        if max_year is not None and (e["year"] > max_year or e.get("year_lo", e["year"]) > max_year):
            continue
        if exclude_prefixes and e["text"][:PREFIX_LEN].lower() in exclude_prefixes:
            continue
        if pats and any(p.search(e["text"]) for p in pats):
            continue
        pool.append(e)
    pool.sort(key=score)
    chosen: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for e in pool:  # sister ships' refit lines are often near-identical; show one
        key = (e["year"], re.sub(r"\d+", "#", e["text"][:40]))
        if key in seen:
            continue
        seen.add(key)
        chosen.append(e)
        if len(chosen) >= max_n:
            break
    chosen.sort(key=lambda e: e["year"])
    return [[e["year"], shorten(e["text"], max_chars)] for e in chosen]


# --------------------------------------------------------------------------------------------------
# DB access + CLI
# --------------------------------------------------------------------------------------------------
def open_ro(db: Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def class_texts(conn: sqlite3.Connection, rowids: list[int] | None = None) -> dict[int, dict[str, Any]]:
    sql = "SELECT rowid AS rid, CLASS_NAME, COUNTRY, source_url, Project_history, Modernizations, Naval_service FROM CLASS"
    rows = conn.execute(sql).fetchall()
    keep = set(rowids) if rowids is not None else None
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        if keep is not None and r["rid"] not in keep:
            continue
        out[r["rid"]] = dict(r)
    return out


def class_ship_names(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """source_url -> ship names (SHIPS.NAME).  漏れ検査用（艦名が入力に現れていないか）。"""
    names: dict[str, set[str]] = {}
    for url, nm in conn.execute("SELECT source_url, NAME FROM SHIPS WHERE NAME IS NOT NULL AND NAME != ''"):
        names.setdefault(url, set()).add(nm)
    return {u: sorted(v) for u, v in names.items()}


def own_prefixes(events: list[dict[str, Any]]) -> set[str]:
    """ある艦級自身の出来事の先頭 PREFIX_LEN 字（小文字）。他艦級の年表に同じ文が出たら捨てるのに使う。"""
    return {e["text"][:PREFIX_LEN].lower() for e in events if len(e["text"]) >= 30}


def events_for_class(row: dict[str, Any] | sqlite3.Row) -> list[dict[str, Any]]:
    return extract_events(row["Modernizations"], row["Naval_service"], row["Project_history"])


def cmd_stats(args: argparse.Namespace) -> None:
    conn = open_ro(args.db)
    rows = class_texts(conn)
    n = len(rows)
    tot = {"mod": [0, 0], "svc": [0, 0], "proj": [0, 0]}  # [classes with content, classes with >=1 event]
    mod_lines = mod_ok = 0
    any_content = any_event = 0
    ev_total = 0
    per_kind: dict[str, int] = {}
    for r in rows.values():
        contents = {"mod": bool(_lines(r["Modernizations"])) and any(not _NONE_LINE.match(l) for l in _lines(r["Modernizations"])),
                    "svc": any(not (_NONE_LINE.match(l) or _NOISE.search(l)) for l in _lines(r["Naval_service"])),
                    "proj": any(not _NONE_LINE.match(l) for l in _lines(r["Project_history"]))}
        evs = {"mod": parse_modernizations(r["Modernizations"]), "svc": parse_naval_service(r["Naval_service"]),
               "proj": parse_project_history(r["Project_history"])}
        for k in tot:
            if contents[k]:
                tot[k][0] += 1
                if evs[k]:
                    tot[k][1] += 1
        for l in _lines(r["Modernizations"]):
            if not (_NONE_LINE.match(l) or _NOISE.search(l)):
                mod_lines += 1
        mod_ok += len(evs["mod"])
        any_content += any(contents.values())
        allev = [e for v in evs.values() for e in v]
        any_event += bool(allev)
        ev_total += len(allev)
        for e in evs["mod"]:
            for k in e["add"]:
                per_kind[k] = per_kind.get(k, 0) + 1
    print(json.dumps({"classes": n, "classes_with_any_content": any_content, "classes_with_any_event": any_event,
                      "by_section": {k: {"with_content": v[0], "with_event": v[1], "rate": round(v[1] / max(1, v[0]), 3)} for k, v in tot.items()},
                      "mod_lines": mod_lines, "mod_events": mod_ok, "mod_line_rate": round(mod_ok / max(1, mod_lines), 3),
                      "events_total": ev_total, "mod_add_kind_counts": dict(sorted(per_kind.items(), key=lambda kv: -kv[1]))},
                     ensure_ascii=False, indent=1))


def _print_class(r: dict[str, Any]) -> None:
    print(f"===== rowid {r['rid']}  {r['CLASS_NAME']}  [{r['COUNTRY']}]")
    for k in ("Modernizations", "Naval_service", "Project_history"):
        t = (r[k] or "").strip()
        print(f"--- {k}: {shorten(t, 700) if t else '(empty)'}")
    print("--- EXTRACTED:")
    for e in events_for_class(r):
        print(f"  {e['year']}{'-' + str(e['year_end']) if e['year_end'] else ''} [{e['src']}/{e['etype']}/{e['prec']}] kinds={e['kinds']} +{e['add']} -{e['remove']}  {e['text'][:140]}")


def cmd_audit(args: argparse.Namespace) -> None:
    conn = open_ro(args.db)
    rows = class_texts(conn)
    cand = [r for r in rows.values() if any(not _NONE_LINE.match(l) for l in _lines(r["Modernizations"])) or _dated_sentences(r["Naval_service"], "svc")]
    rng = random.Random(args.seed)
    for r in rng.sample(cand, args.n):
        _print_class(r)


def cmd_show(args: argparse.Namespace) -> None:
    conn = open_ro(args.db)
    rid = int(args.id.lstrip("c"))
    rows = class_texts(conn, [rid])
    _print_class(rows[rid])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    a = sub.add_parser("audit")
    a.add_argument("--n", type=int, default=20)
    a.add_argument("--seed", type=int, default=1)
    a.set_defaults(fn=cmd_audit)
    s = sub.add_parser("show")
    s.add_argument("--id", required=True)
    s.set_defaults(fn=cmd_show)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
