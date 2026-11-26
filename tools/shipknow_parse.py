"""Field parsers for the shipknow dataset (NSDB CLASS rows -> structured values).

NSDB text conventions that these parsers have to cope with
(see docs/shipknow_v0/README for the measured frequencies):

* items are joined by ``"+ "`` (a ``+`` followed by a space); ``+`` inside
  parentheses is part of one item;
* per-ship / per-batch variants are written ``"Label: value"`` and glued
  together with no separator: ``"Hatsuharu+ Nenohi: 1490others: 1715"``.
  We always take the **first** variant and report its label;
* ``"1533bm 1894 normal"`` -> ``bm`` = gross tonnage (not displacement);
* all lengths are metres, displacement tonnes, power hp, speed knots.

Everything here is pure (no I/O).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

YEAR_LO, YEAR_HI = 1500, 2030

# ----------------------------------------------------------------------------
# class name -> year / base name
# ----------------------------------------------------------------------------

_CLASS_SUFFIX = re.compile(r"\s*\bclass\s*$", re.I)


def _peel_years(digits: str) -> tuple[list[int], str]:
    years: list[int] = []
    while len(digits) >= 4 and YEAR_LO <= int(digits[-4:]) <= YEAR_HI:
        years.append(int(digits[-4:]))
        digits = digits[:-4]
    return years, digits


def parse_class_year(class_name: str | None) -> tuple[int | None, str]:
    """CLASS_NAME -> (year, name without the year run).

    The scraper lost the opening parenthesis: ``Hatsuharu (1935)`` became
    ``HATSUHARU1935)``; ``ME36 (1943-1945)`` became ``ME3619431945)``.  Years are
    peeled from the right in 4-digit chunks, so a name that ends in digits
    (``R125`` + ``2004``) survives.
    """
    s = _CLASS_SUFFIX.sub("", (class_name or "").strip())
    ms = list(re.finditer(r"(\d[\d\-/ ,]*)\)", s))
    if not ms:
        return None, s
    m = ms[-1]
    run = m.group(1)
    years: list[int] = []
    consumed = 0
    for g in reversed(re.findall(r"\d+", run)):
        ys, rem = _peel_years(g)
        years.extend(ys)
        consumed += len(g) - len(rem)
        if rem:
            break
    if not years:
        return None, s
    head = s[: m.start(1) + len(run)]
    removed, i = 0, len(head)
    while i > 0 and removed < consumed:
        if head[i - 1].isdigit():
            removed += 1
        i -= 1
    head = head[:i].rstrip(" -/,")
    if head.count("(") > head.count(")"):
        head = head[: head.rfind("(")]
    return min(years), (head + s[m.end():]).strip()


# ----------------------------------------------------------------------------
# year columns of the re-fetched NSDB (CLASS.WHEN_DATE, SHIPS.Comp, SHIP_GROUPS.Completed)
# ----------------------------------------------------------------------------

_YEAR_TOKEN = re.compile(r"(?<!\d)(1[5-9]\d\d|20[0-3]\d)(?!\d)")


def parse_when_date(text: str | None) -> tuple[int | None, str | None]:
    """``CLASS.WHEN_DATE`` (the date in the heading) -> (year, kind).

    ``"(1941 - 1942)"`` -> (1941, "exact");  ``"(1944/1947)"`` -> (1944, "exact");
    ``"(2018-)"`` -> (2018, "exact");  ``"(1990s)"`` / ``"(1980s-90s)"`` -> (1990 / 1980, "decade").
    The earliest year wins (same rule as ``parse_class_year`` on the old, year-bearing names).
    """
    best: tuple[int, bool] | None = None
    for m in _YEAR_TOKEN.finditer(text or ""):
        y = int(m.group(1))
        if YEAR_LO <= y <= YEAR_HI and (best is None or y < best[0]):
            best = (y, text[m.end(): m.end() + 1] == "s")
    if best is None:
        return None, None
    return best[0], "decade" if best[1] else "exact"


def _years_of(text: str) -> list[tuple[int, bool]]:
    """[(year, followed_by_s)] for every plausible year token in ``text`` ("1970s" -> (1970, True))."""
    return [(int(m.group(1)), text[m.end(): m.end() + 1] == "s") for m in _YEAR_TOKEN.finditer(text)
            if YEAR_LO <= int(m.group(1)) <= YEAR_HI]


def parse_when_date_parts(text: str | None) -> dict[str, Any]:
    """``CLASS.WHEN_DATE`` -> the design/build era and, for ``A/B`` headings, the year the ship entered *this* navy.

    A heading date is ``(A)`` or ``(A/B)``: ``A`` = when the class was designed/built, ``B`` = when this country
    took it over (transfer, purchase, capture).  A page may list several batches separated by commas
    (``"(1941-1942/1942-1944, 1943-1945)"``); a leading count (``"(10, 1944 - 1946)"``, ``"(16(38), 1919 - 1924)"``,
    ``"(project 6631, 1966)"``) carries no year token and is ignored.

    Rules (``year`` = earliest A year; ``navy_year`` = earliest B year, only when some batch has a ``/`` with a year after it):

    ==========================  ======  ==========
    heading                     year    navy_year
    ==========================  ======  ==========
    ``(1941 - 1942)``           1941    None
    ``(1970s/1999)``            1970    1999
    ``(1970s/1992-2006)``       1970    1992
    ``(1944-1948/1953-1954)``   1944    1953
    ``(1929 / ---)``            1929    None   (empty B: no transfer year)
    ``(/1992)``                 None    1992   (A missing)
    ``(1990s)``                 1990    None   (kind "decade")
    ``(1, 1864)`` / ``""``      1864 / None    None
    ==========================  ======  ==========

    Returns ``{"year", "year_kind", "navy_year", "navy_kind"}``; ``*_kind`` is ``"exact"`` or ``"decade"`` (a ``1990s`` token).
    """
    a_best: tuple[int, bool] | None = None
    b_best: tuple[int, bool] | None = None
    for seg in (text or "").split(","):
        a_txt, sep, b_txt = seg.partition("/")
        for y in _years_of(a_txt):
            if a_best is None or y[0] < a_best[0]:
                a_best = y
        if sep:
            for y in _years_of(b_txt):
                if b_best is None or y[0] < b_best[0]:
                    b_best = y

    def kind(v: tuple[int, bool] | None) -> str | None:
        return None if v is None else ("decade" if v[1] else "exact")

    return {"year": a_best[0] if a_best else None, "year_kind": kind(a_best),
            "navy_year": b_best[0] if b_best else None, "navy_kind": kind(b_best)}


def parse_comp_year(text: str | None) -> int | None:
    """``SHIPS.Comp`` / one ``SHIP_GROUPS.Completed`` line -> completion year.

    ``"7.1941"`` -> 1941; ``"1963-69 // 1967-69"`` -> 1963 (what follows ``//`` is a later event);
    ``"1969-90 // 1992: A-01"`` -> 1969; ``"---"`` -> None.
    """
    seg = (text or "").split("//", 1)[0]
    for part in (seg.split(":", 1)[0], seg):
        ys = [int(y) for y in _YEAR_TOKEN.findall(part) if YEAR_LO <= int(y) <= YEAR_HI]
        if ys:
            return min(ys)
    return None


def completed_year(text: str | None) -> int | None:
    """Earliest completion year over the lines of ``SHIP_GROUPS.Completed``."""
    ys = [y for y in (parse_comp_year(line) for line in (text or "").splitlines()) if y is not None]
    return min(ys) if ys else None


_QUAL_TOKENS = ("modified", "improved", "repeat", "type", "enlarged", "ex")


def class_base_name(class_name: str | None) -> str:
    """Normalised class base name used for leakage grouping ('' if unusable)."""
    _, name = parse_class_year(class_name)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"[\"'`´]", "", name).lower()
    for q in _QUAL_TOKENS:
        if name.startswith(q) and len(name) > len(q) + 2:
            name = name[len(q):]
    name = re.sub(r"[^a-z0-9]+", "", name)
    name = re.sub(r"^\d{4}$", "", name)  # a bare year is not a name
    return name


# ----------------------------------------------------------------------------
# scalar numeric fields
# ----------------------------------------------------------------------------

_GLUE = re.compile(r"(?<=[0-9\)>])(?=[A-Za-z\"'])|(?<=[a-z])(?=[A-Z\"])")
_BM = re.compile(r"(\d)\s*(bm|brt|grt|gt)\b|(\d)(bm|brt)(?=[a-z])", re.I)
_FLOAT = r"\d+(?:\.\d+)?"


def _fix_ocr_digits(t: str) -> str:
    """'8 . 1 6' -> '8.16', '28 0 0' -> '2800', '1 6 000' -> '16000' (scraper/OCR spacing)."""
    m = re.match(r"[\d .]+", t)
    if not m:
        return t
    head, tail = m.group(0), t[m.end():]
    trail_space = head.endswith(" ") and tail
    h = head.strip()
    if re.search(r"\d \.|\. \d", h):
        h = h.replace(" ", "")
    elif re.fullmatch(r"\d{1,3}(?: (?:\d|\d{3}))+", h):
        h = h.replace(" ", "")
    else:
        return t
    return h + (" " if trail_space else "") + tail


def _norm_text(text: str | None) -> str:
    if not text:
        return ""
    t = text.replace("\xa0", " ").replace("\u0445", "x").replace("\u00d7", "x").strip()  # cyrillic x, multiplication sign
    t = t.replace("\u0422T", "TT")  # cyrillic Te typed in front of a Latin "TT" (torpedo tubes)
    t = _fix_ocr_digits(t)
    t = re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", t)
    return t


def _label_digits(label: str) -> int | None:
    """Digit-length of the first number in a numeric-looking label ('122-128' -> 3, '01+ 02' -> 2)."""
    m = re.search(r"\d+", label)
    if not m or not re.fullmatch(r"[A-Za-z.\- ]{0,4}\d+(?:\s*[-+/]\s*[A-Za-z]{0,3}\d+)*[A-Za-z]?", label.strip()):
        return None
    return len(m.group(0))


def _cut_value(seg2: str, label1: str | None) -> str:
    """Cut ``value1 + glued label2`` (text between two colons) at the start of label2."""
    ldig = _label_digits(label1) if label1 else None
    m = _GLUE.search(seg2)
    if m and ldig is not None and re.fullmatch(r"[A-Za-z]{1,2}", seg2[m.start():].strip() or "x") and seg2[m.start() - 1].isdigit():
        # numeric labels with a letter suffix: "266756K" = value 2667 + label 56K
        k = m.start() - ldig
        if k >= 1:
            return seg2[:k]
    if m:
        return seg2[: m.start()]
    if ldig is not None:
        rx = re.compile(rf"\d{{{ldig}}}(?:\s*[-+/]\s*\d+)*\s*$")
        for p in range(1, len(seg2)):
            if rx.fullmatch(seg2[p:]) and not (seg2[p - 1:p] == "." ):
                return seg2[:p]
    return seg2


def isolate_first_variant(text: str | None) -> tuple[str, str | None, bool]:
    """Return (segment, first_variant_label, had_variants) for a scalar field.

    ``"Hatsuharu+ Nenohi: 1490others: 1715"`` -> ``("1490", "Hatsuharu+ Nenohi", True)``
    ``"578Garm: 597"``                         -> ``("578", None, True)``
    ``"122-128: 2950129-132: 3050"``          -> ``("2950", "122-128", True)``
    """
    t = _norm_text(text)
    if not t:
        return "", None, False
    t = _BM.sub(lambda m: f"{m.group(1) or m.group(3)}<bm>", t)
    if ":" not in t:
        return t, None, False
    pre, post = t.split(":", 1)
    pre_s = pre.strip()
    # purely numeric label ("721", "122-128", "01+ 02") -> label first
    if re.fullmatch(r"[\d\s\-–+.,/]*", pre_s) or not re.match(r"\s*\d", pre_s):
        label: str | None = pre_s
        seg2 = post.split(":", 1)[0]
        if ":" in post:
            seg = _cut_value(seg2, label)
        else:
            seg = seg2
            m = _GLUE.search(seg)
            if m:
                seg = seg[: m.start()]
        return seg.strip(), label, True
    # value first: "578Garm", "17.0Leander", "4 Szent István", "99.4 pp 102.7 oaGrenville"
    m = _GLUE.search(pre_s)
    if m:
        seg = pre_s[: m.start()]
    else:
        m2 = re.match(r"(\d[\d.,]*)\s+(?=[A-ZÀ-ÝŁŚ])", pre_s)
        seg = m2.group(1) if m2 else pre_s
    return seg.strip(), None, True


def _first_float(seg: str) -> float | None:
    m = re.search(_FLOAT, seg)
    return float(m.group(0)) if m else None


def parse_displacement(text: str | None) -> dict[str, Any]:
    seg, label, multi = isolate_first_variant(text)
    flags: list[str] = []
    out: dict[str, Any] = {"value": None, "submerged": None, "label": label, "multi": multi, "flags": flags}
    if not seg:
        return out
    parts = seg.split("/")
    toks = [(float(m.group(1)), bool(m.group(2))) for m in re.finditer(rf"({_FLOAT})(<bm>)?", parts[0])]
    toks_plain = [v for v, bm in toks if not bm]
    if toks_plain:
        out["value"] = toks_plain[0]
    elif toks:
        flags.append("displacement_gross_tonnage_only")
    if len(parts) > 1:
        t2 = [float(m.group(1)) for m in re.finditer(rf"({_FLOAT})(?!<bm>)", parts[1])]
        if t2:
            out["submerged"] = t2[0]
    if out["value"] is not None and out["value"] <= 0:
        out["value"] = None
    return out


def parse_length_m(text: str | None) -> tuple[float | None, str | None, bool]:
    seg, label, multi = isolate_first_variant(text)
    if not seg:
        return None, label, multi
    oa = [float(m.group(1)) for m in re.finditer(rf"({_FLOAT})\s*(?:oa|overall)\b", seg, re.I)]
    if oa:
        return max(oa), label, multi
    vals = [float(m.group(0)) for m in re.finditer(_FLOAT, seg)]
    return (max(vals) if vals else None), label, multi


def parse_first(text: str | None) -> tuple[float | None, str | None, bool]:
    seg, label, multi = isolate_first_variant(text)
    return _first_float(seg), label, multi


def parse_draught_m(text: str | None) -> tuple[float | None, str | None, bool]:
    seg, label, multi = isolate_first_variant(text)
    if re.search(r"hold|\bdepth\b(?!.*draught)", seg, re.I) and not re.search(r"\b(?:mean|full|deep|max|load|normal|hull)\b", seg, re.I):
        return None, label, multi  # "4.11 hold depth" = moulded depth
    return _first_float(seg), label, multi


def parse_power_hp(text: str | None) -> tuple[float | None, str | None, bool]:
    t = re.sub(r"(\d)\s*(nhp|ihp|shp)", lambda m: f"{m.group(1)}<{m.group(2).lower()}>", _norm_text(text), flags=re.I)
    seg, label, multi = isolate_first_variant(t)
    if not seg:
        return None, label, multi
    ihp = re.search(rf"({_FLOAT})<(?:ihp|shp)>", seg)
    if "=" in seg:
        v = _first_float(seg.split("=", 1)[1])
    elif ihp:
        v = float(ihp.group(1))
    elif "<nhp>" in seg:
        v = None  # nominal horsepower (sailing/paddle era) is not comparable with shp/ihp
    else:
        v = _first_float(seg.split("/")[0])
    if v is not None and v <= 0:
        v = None
    return v, label, multi


def parse_speed_kn(text: str | None) -> dict[str, Any]:
    seg, label, multi = isolate_first_variant(text)
    out: dict[str, Any] = {"value": None, "submerged": None, "label": label, "multi": multi}
    if not seg:
        return out
    parts = seg.split("/")
    first = parts[0]
    rng = re.match(rf"\s*({_FLOAT})\s*-\s*({_FLOAT})", first)
    if rng:
        out["value"] = max(float(rng.group(1)), float(rng.group(2)))  # upper end of a range
    else:
        out["value"] = _first_float(first)
    if len(parts) > 1:
        rng2 = re.match(rf"\s*({_FLOAT})\s*-\s*({_FLOAT})", parts[1])
        out["submerged"] = max(float(rng2.group(1)), float(rng2.group(2))) if rng2 else _first_float(parts[1])
    return out


def parse_endurance(text: str | None) -> dict[str, Any]:
    t = _norm_text(text)
    if not t:
        return {"nm": None, "kn": None, "unlimited": False}
    if re.search(r"unlimited", t, re.I):
        return {"nm": None, "kn": None, "unlimited": True}
    seg, _, _ = isolate_first_variant(t)
    m = re.search(rf"({_FLOAT})\s*\(\s*({_FLOAT})\s*\)", seg)
    if m:
        return {"nm": float(m.group(1)), "kn": float(m.group(2)), "unlimited": False}
    return {"nm": None, "kn": None, "unlimited": False}


_FUEL_TYPES = [
    ("nuclear", r"nuclear"),
    ("coal", r"coal"),
    ("diesel", r"diesel"),
    ("petrol", r"petrol|gasoline"),
    ("oil", r"\boil\b|fuel oil"),
    ("gas_turbine_fuel", r"gas turbine|kerosene|jet"),
]


def parse_fuel(text: str | None) -> dict[str, Any]:
    seg, _, _ = isolate_first_variant(text)
    out: dict[str, Any] = {"type": None, "amount": None, "unit": None}
    if not seg:
        return out
    for name, rx in _FUEL_TYPES:
        if re.search(rx, seg, re.I):
            out["type"] = name
            break
    m = re.search(rf"({_FLOAT})(?:\s*-\s*({_FLOAT}))?\s*(l\b|t\b)?", seg)
    if m:
        out["amount"] = float(m.group(1))
        out["unit"] = "l" if (m.group(3) or "").lower() == "l" else "t"
    return out


def parse_shafts(text: str | None) -> tuple[int | None, str | None]:
    seg, _, _ = isolate_first_variant(text)
    if not seg:
        return None, None
    prop = None
    if re.search(r"water-?jet|pump-?jet", seg, re.I):
        prop = "waterjet"
    elif re.search(r"wheel|paddle", seg, re.I):
        prop = "paddle"
    elif re.search(r"air|fan|lift", seg, re.I):
        prop = "air"
    elif re.search(r"voith|cycloidal|schottel|azi|pod", seg, re.I):
        prop = "azimuth"
    elif re.search(r"propeller|screw|shaft", seg, re.I):
        prop = "screw"
    m = re.match(r"\s*(\d+)\b", seg)
    n = int(m.group(1)) if m else None
    if n is not None and n > 12:
        n = None
    return n, prop


def propulsion_tags(machinery: str | None) -> list[str]:
    """Coarse propulsion tags from the Machinery text (keyword based)."""
    t = (machinery or "").lower()
    if not t.strip():
        return []
    tags: list[str] = []
    if re.search(r"nuclear|reactor|\bpwr\b", t):
        tags.append("nuclear")
    t_nogas = re.sub(r"gas[- ]turbines?", " ", t)
    if t_nogas != t or re.search(r"\bcod[ao]g\b|\bcog[ao][gs]\b|\bcodog\b", t):
        tags.append("gas_turbine")
    if "diesel" in t:
        tags.append("diesel")
    if re.search(r"petrol|gasoline|otto|\bgasolene", t):
        tags.append("petrol_engine")
    if re.search(r"turbine", t_nogas):
        tags.append("steam_turbine")
    if re.search(r"\bvte\b|\bhte\b|\bvc\b|\bhc\b|\bhrcr\b|\bhdcr\b|\bvqe\b|reciprocating|expansion|compound|locomotive boiler|steam engine", t):
        tags.append("steam_reciprocating")
    if re.search(r"electric motor|batter", t):
        tags.append("electric_motor")
    if re.search(r"\bsails?\b|sailing", t):
        tags.append("sail")
    if re.search(r"paddle", t):
        tags.append("paddle_wheel")
    return tags


# ----------------------------------------------------------------------------
# item splitting / variants for Armament and Electronic_equipment
# ----------------------------------------------------------------------------


def split_top(text: str, sep: str = "+") -> list[str]:
    """Split on ``sep`` followed by a space (or end), honouring parentheses."""
    pieces: list[str] = []
    cur: list[str] = []
    depth = 0
    n = len(text)
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0 and (i + 1 == n or text[i + 1] == " "):
            pieces.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    pieces.append("".join(cur).strip())
    if depth > 0:  # unbalanced parentheses: fall back to a naive split
        return [p.strip() for p in re.split(r"\+(?= |$)", text)]
    return [p for p in pieces if p != ""]


def _top_colon(piece: str) -> int | None:
    depth = 0
    for i, ch in enumerate(piece):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0 and (i + 1 == len(piece) or piece[i + 1] == " "):
            return i
    return None


_SIG = re.compile(
    r"\d\s*x\s*\d|\d+\s*/\s*\d+|\bTT(?-i:(?![a-z]))|\bTL(?-i:(?![a-z]))|DCT|DCR|\bDC(?-i:(?![a-z]))|SSM|\bSAM\b|ASWRL|mines?(?-i:(?![a-z]))|aircraft|helicopter|seaplane|airplane|"
    r"gear|catapult|platform|balloon|torpedo|rocket|launcher|hedgehog|mortar|\bgun|radar|sonar|\bE[CS]M(?![a-z])|hydrophone|director|\bUSVs?\b|\bUUVs?\b|sweep|degauss|\bmechanical\b|\bmagnetic\b|\bacoustic\b|helicopter deck|\bhangar|"
    r"suite|\bCCS\b|decoy|\bnone\b|\bRL\b|\bcanno|\b\d+\s*-\s*\d{2,3}\b.*\b(?:TT|T[LCRA]\b)|\btubes?\b",
    re.I,
)


def _is_label_like(piece: str) -> bool:
    return len(piece) <= 45 and not _SIG.search(piece)


def _cut_glue(s: str) -> tuple[str, str]:
    """Split ``content+LabelStart`` into (content, label start)."""
    cands = [m.end() for m in re.finditer(r"\)(?=[A-Za-z\"'0-9])", s)]
    if not cands:
        cands = [m.end() for m in re.finditer(r"(?<=[a-z0-9])(?=[A-Z\"])", s)]
    cands = [c for c in cands if len(s) - c <= 48]
    if not cands:
        return s, ""
    c = cands[-1]
    return s[:c].strip(), s[c:].strip()


# A torpedo-tube item whose keyword is directly followed by the next variant's label:
# "4 x 1 - 533 TTAlmirante Williams", "3 x 2 - 450 TTHelgoland", "2 - 533 TTpr. 125A", "1 x 2 - 350 TT78Ya".
# The keyword is not a word boundary there, so the generic glue heuristics (lower->UPPER, ")"->alnum) never fire.
_TUBE_GLUE_RE = re.compile(
    rf"^\(?\s*(?:\d+\s*x\s*\d+|\d+)\s*-\s*(?P<cal>\d+(?:\.\d+)?)(?:\s*/\s*(?P<cal2>\d+(?:\.\d+)?))?\s*(?:mm\s*)?"
    r"(?:(?:[\w\.\-/]+|\([^()]*\))\s+){0,5}?(?:TT|TL|TC|TR|TA)(?=[A-Za-z0-9])",
    re.S,
)


def _cut_tube_glue(s: str) -> tuple[str, str]:
    """``"3 x 2 - 450 TTHelgoland"`` -> (``"3 x 2 - 450 TT"``, ``"Helgoland"``); (s, "") when there is no such glue.

    Only torpedo-tube shaped items (a torpedo-sized caliber that is not ``caliber/length`` of a gun) are cut, so gun
    names such as ``90/42 G. L/45 K.12 TAG/BAG`` or ``TAK120`` are left alone.
    """
    m = _TUBE_GLUE_RE.match(s)
    if not m or float(m.group("cal")) < 200 or (m.group("cal2") and float(m.group("cal2")) < 100):
        return s, ""
    return s[: m.end()].strip(), s[m.end():].strip()


def _cut_numeric_tail(s: str, prev_label: str | None) -> tuple[str, str]:
    """``"2 x 1 - 12.7/90586"`` with the previous label ``"581+ 582+ 583+ 585"`` -> (``"2 x 1 - 12.7/90"``, ``"586"``).

    Hull-number labels are glued straight onto the calibre length; the new label has as many digits as the previous one.
    """
    if not prev_label or not re.fullmatch(r"[\d\s\-\u2013+.,/]+", prev_label.strip()):
        return s, ""
    ld = len(re.search(r"\d+", prev_label).group(0)) if re.search(r"\d", prev_label) else 0  # type: ignore[union-attr]
    m = re.search(rf"/(\d{{1,3}})(\d{{{ld}}})$", s) if ld >= 2 else None
    if not m:
        return s, ""
    return s[: m.end(1)].strip(), s[m.end(1):].strip()


_NOUN_GLUE_RE = re.compile(r"\b(?:mines?|DC[TR]?|ASWRL|RL|launchers?)(?=\d|[A-Z][A-Za-z0-9]|\(|pr\. |(?:others?|most|some|earlier|later|completed|commissioned|group \d|as (?:designed|completed|built|[A-Z]\w*))\b)")


def _cut_noun_glue(s: str) -> tuple[str, str]:
    """``"38 mines108-110"`` / ``"4 DCMTB227"`` -> the item and the hull-number label glued to its noun."""
    m = _NOUN_GLUE_RE.search(s)
    if not m or len(s) - m.end() > 48 or not _is_label_like(s[m.end():].strip()):
        return s, ""
    return s[: m.end()].strip(), s[m.end():].strip()


def _cut_item_label(s: str, prev_label: str | None = None) -> tuple[str, str]:
    c, lab = _cut_tube_glue(s)
    if lab:
        return c, lab
    c, lab = _cut_noun_glue(s)
    if lab:
        return c, lab
    c, lab = _cut_glue(s)
    return (c, lab) if lab else _cut_numeric_tail(s, prev_label)


def _top_colons(piece: str) -> list[int]:
    depth = 0
    out: list[int] = []
    for i, ch in enumerate(piece):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0 and (i + 1 == len(piece) or piece[i + 1] == " "):
            out.append(i)
    return out


def _valid_weapon_item(c: str) -> bool:
    """True when ``c`` parses as a recognised weapon (or an explicit none) - used to validate a candidate cut."""
    if not c.strip():
        return False
    if re.fullmatch(r"(?i)\s*(?:none|unarmed|nil)\s*", c):
        return True
    w = parse_weapon_piece(c)
    return w is None or w["kind"] != "other"


_LABEL_WORD = re.compile(r"(?<=[a-z0-9A-Z)])(?=\s*(?:others?|most|some|rest|earlier|later|completed|commissioned|group \d|as (?:designed|completed|built|[A-Z]\w*))\b)")


def _cut_tail_label(seg: str, prev_label: str) -> tuple[str, str] | None:
    """Cut ``item + glued next label`` (the text between two colons) into (item, label); None when no safe cut exists.

    Candidate cuts, most reliable first; a cut is accepted only if the left part parses as a weapon and the right
    part is label-like (no weapon signature, <= 48 chars).
    """
    cuts: list[int] = []
    c, lab = _cut_tube_glue(seg)
    if lab:
        cuts.append(len(c))
    ld = _label_digits(prev_label)
    if ld is not None:  # numeric hull-number labels: the next label has as many digits as the previous one
        rx = re.compile(rf"\d{{{ld}}}(?:\s*[-+/]\s*\d+)*\s*$")
        for p in range(1, len(seg)):
            if rx.fullmatch(seg[p:]) and seg[p - 1] != ".":
                cuts.append(p)
                break
    cuts.extend(m.start() for m in _LABEL_WORD.finditer(seg))
    for rx2 in (r"\)(?=[A-Za-z\"'0-9])", r"/\d+(?:\.\d+)?(?=[A-Za-z\"'])", r"(?<=[a-z0-9])(?=[A-ZÀ-Þ\"])", r"(?<=[A-Z])(?=[A-ZÀ-Þ][a-zà-ÿ])"):
        found = [mm.end() if rx2.startswith(r"\)") or rx2.startswith("/") else mm.start() for mm in re.finditer(rx2, seg)]
        if found:
            cuts.append(found[-1])
    for k in cuts:
        content, label = seg[:k].strip(), seg[k:].strip()
        if label and len(label) <= 48 and _is_label_like(label) and _valid_weapon_item(content):
            return content, label
    # last resort: "2 x 1 - 20/70 Mk 10 A3" - a short label after a blank (shortest label first)
    for m in reversed(list(re.finditer(r"\s+", seg))):
        content, label = seg[: m.start()].strip(), seg[m.end():].strip()
        if label and len(label) <= 24 and len(label.split()) <= 2 and _is_label_like(label) and _valid_weapon_item(content):
            return content, label
    return None


def _expand_multi_colon(pieces: list[str]) -> list[str]:
    """Split a ``+``-piece that holds several ``label:`` marks ("84: 1 x 1 - 12.7/90PC85: 1 x 1 - 20/70") into one piece per mark.

    Without this the 2nd variant's first item is swallowed by the 1st variant and every later item of the
    2nd variant is attributed to the 1st.  When a safe cut cannot be found the piece is left as it was.
    """
    out: list[str] = []
    for p in pieces:
        cs = _top_colons(p)
        if len(cs) < 2:
            out.append(p)
            continue
        segs = [p[: cs[0]]] + [p[a + 1: b] for a, b in zip(cs, cs[1:])] + [p[cs[-1] + 1:]]
        new: list[str] = []
        prefix = segs[0]
        ok = True
        for seg in segs[1:-1]:
            cut = _cut_tail_label(seg.strip(), prefix)
            if cut is None:
                ok = False
                break
            new.append(f"{prefix}: {cut[0]}")
            prefix = cut[1]
        if not ok:
            out.append(p)
            continue
        new.append(f"{prefix}: {segs[-1].strip()}")
        out.extend(new)
    return out


def split_variants(text: str | None, *, weapons: bool = False) -> list[tuple[str | None, list[str]]]:
    """All ``(label, items)`` variants of an Armament / Electronic_equipment text (the first one is the one we use)."""
    t = _norm_text(text)
    if not t:
        return []
    pieces = split_top(t)
    if weapons:
        pieces = _expand_multi_colon(pieces)
    variants: list[tuple[str | None, list[str]]] = []
    cur: list[str] = []
    label: str | None = None
    for p in pieces:
        pos = _top_colon(p)
        if pos is None:
            cur.append(p)
            continue
        pre, post = p[:pos], p[pos + 1:].strip()
        label_parts: list[str] = []
        content, lab = _cut_item_label(pre, label)
        if not lab and weapons and not _is_label_like(pre):
            cut = _cut_tail_label(pre.strip(), label or "")
            if cut:
                content, lab = cut
        if lab and content and not _is_label_like(content):
            cur.append(content)  # "...30 minesLubiana": the item tail glued to the next label
            label_parts.append(lab)
        else:
            label_parts.append(pre.strip())
        while cur and _is_label_like(cur[-1]):
            label_parts.insert(0, cur.pop())
        if cur:
            c, l = _cut_item_label(cur[-1], label)
            if l:
                label_parts.insert(0, l)
            cur[-1] = c
            if not c:
                cur.pop()
        if cur:
            variants.append((label, cur))
            cur = []
        label = " ".join(x for x in label_parts if x).strip() or None
        if post:
            cur.append(post)
    if cur or not variants:
        variants.append((label, cur))
    return variants


def first_variant_pieces(text: str | None, *, weapons: bool = False) -> tuple[list[str], dict[str, Any]]:
    """Items of the first variant of an Armament / Electronic_equipment text.

    ``weapons=True`` additionally splits pieces that contain several ``label:`` marks (armament only).
    """
    variants = split_variants(text, weapons=weapons)
    if not variants:
        return [], {"multi": False, "label": None}
    first_label, first_items = variants[0]
    return first_items, {"multi": len(variants) > 1, "label": first_label, "n_variants": len(variants)}


# ----------------------------------------------------------------------------
# armament
# ----------------------------------------------------------------------------

_COUNT = r"(?:(?P<n>\d+)\s*x\s*(?P<m>\d+)|(?P<n2>\d+))"
_GUN_RE = re.compile(rf"^\(?\s*{_COUNT}\s*-\s*(?P<cal>\d+(?:\.\d+)?)\s*/\s*(?P<len>\d+(?:\.\d+)?)\s*(?P<name>.*)$", re.S)
# Torpedo tubes.  Keywords: TT / TL / TC / TR / TA, or "torpedo tubes / catapults / launchers" (Japanese and French texts
# call the tubes "TA" or "torpedo catapults").  The keyword may be directly followed by the next variant's label
# ("533 TTAlmirante Williams"), hence the (?=[A-Z0-9]) alternative.  ``cal2`` is "533/550" (alternative calibres).
_TT_KW = r"(?:TT|TL|TC|TR|TA)(?:\b|(?=[A-Z0-9]))|(?i:torpedo[ -]+(?:tubes?|catapults?|launchers?))\b"
_TT_RE = re.compile(
    rf"^\(?\s*{_COUNT}\s*-\s*(?P<cal>\d+(?:\.\d+)?)(?:\s*/\s*(?P<cal2>\d+(?:\.\d+)?))?\s*(?:mm\s*)?"
    rf"(?:(?:[\w\.\-/]+|\([^()]*\))\s+){{0,5}}?(?P<k>{_TT_KW})(?P<rest>.*)$",
    re.S,
)
_TT2_RE = re.compile(r"^\(?\s*(?P<n>\d+)\s*(?:[\w\.\-]+\s+){0,2}?(?P<k>TT|TL|(?i:torpedo[ -]+(?:tubes?|launchers?)))\b(?P<rest>.*)$", re.S)
# A caliber in the torpedo range plus a torpedo word / tube keyword in the name is not a gun (a 457/40 or 450/20 gun has neither).
_TORPEDO_CAL = (300.0, 700.0)
_TORPEDO_NAME_RE = re.compile(r"(?i:\btorpedo|\bstingray|\bMU-?90\b|\bA244)|\b(?:TT|TL|TC|TR|TA|STWS|DMTS)(?![a-z])")


def _is_torpedo_not_gun(cal: float, length: float | None, name: str | None) -> bool:
    """A ``cal/len`` or ``cal name`` item that is really a torpedo tube / torpedo.

    Not decided by the caliber alone (457/40 and 450/20 are real guns): the caliber must be in the torpedo range *and*
    either the "length" is not a calibre length (>= 100, i.e. "533/550" = alternative calibres) or the name says torpedo / TT.
    """
    if not (_TORPEDO_CAL[0] <= cal <= _TORPEDO_CAL[1]):
        return False
    if length is not None and length >= 100:
        return True
    return bool(name and _TORPEDO_NAME_RE.search(name))


def _tube_item(base: dict[str, Any], m: re.Match[str], cal: float, name: str) -> dict[str, Any]:
    n, per = _counts(m)
    return {**base, "kind": "torpedo_tube", "count": n, "per_mount": per, "caliber_mm": cal, "rounds": _rounds_from(m.group("rest")), "name": name}


def _tube_name(k: str) -> str:
    return k.upper() if len(k) == 2 else re.sub(r"[ -]+", " ", k.lower()).rstrip("s")
_SPAR_RE = re.compile(r"^\(?\s*(?P<n>\d+)?\s*spar torpedo(?:es)?\b(?P<rest>.*)$", re.S | re.I)
_GUN_NOLEN_RE = re.compile(rf"^\(?\s*{_COUNT}\s*-\s*(?P<cal>\d+(?:\.\d+)?)\s*(?P<name>.*)$", re.S)
_GUNS_RE = re.compile(r"^\(?\s*(?P<n>\d+)\s+(?:\w+\s+)?guns?\b", re.I)
_OLDGUN_RE = re.compile(rf"^\(?\s*{_COUNT}\s+(?P<name>.*\b(?:SBML|MLR|RML|RBL|ML|BL|rifled|smoothbore|pdr|pounder)\b.*)$", re.S)
_MISSILE_RE = re.compile(
    rf"^\(?\s*{_COUNT}\s+(?P<name>.*?)\s*(?P<mk>SSM|SAM|ASM|SLBM|SLCM|ASROC|SUGW|ASuM|AShM|SSGW|SAGW|ICBM|SS-N-\d+)\b(?P<rest>.*)$", re.S
)
_MISSILE_NOCOUNT_RE = re.compile(r"^\(?\s*(?P<name>[^()]*?)\s*(?P<mk>SSM|SAM|SLBM|SLCM|SUGW)\b(?P<rest>.*)$", re.S)
_RL_RE = re.compile(rf"^\(?\s*{_COUNT}\s*-\s*(?P<cal>\d+(?:\.\d+)?)\s+(?P<name>.*)$", re.S)
_RL2_RE = re.compile(rf"^\(?\s*{_COUNT}\s+(?P<name>.*?(?:ASWRL|RL\b|rocket|launcher|mortar).*)$", re.S | re.I)
_DC_RE = re.compile(r"^\(?\s*(?:(?P<n>\d+)\s*)?(?:x\s*\d+\s*)?(?P<d>DCT|DCR|DC|depth[- ]charges?)\b(?P<rest>.*)$", re.S | re.I)
_MINE_RE = re.compile(r"^\(?\s*(?:up to |about |circa |approx\.? )?(?:(?P<n>\d+)(?:\s*-\s*\d+)?\s*(?:\w+\s+)?)?mines?\b(?P<rest>.*)$", re.S | re.I)
_AIR_RE = re.compile(
    r"^\(?\s*(?:up to |about |circa |approx\.? |max\.? )?(?:(?P<n>\d+)\s+)?(?:[\w\-\.]+\s+){0,4}?"
    r"(?P<k>aircraft|airplanes?|planes?|helicopters?|seaplanes?|floatplanes?|VSTOL|fighters?|floatplane)\b(?P<rest>.*)$",
    re.S | re.I,
)
_FAC_RE = re.compile(r"helicopter (?:deck|hangar|pad|platform)|flight deck|helipad|flying-off|catapult|crane|hangar|landing (?:deck|platform)|aviation|seaplane (?:deck|crane)", re.I)
_SWEEP_RE = re.compile(r"sweep|degauss|\bUSVs?\b|\bUUVs?\b|\bmechanical\b|\bmagnetic\b|\bacoustic\b|oropesa|^[AM] Mk \d+|\bSA\b gear|sweeping", re.I)
_SMALL_ARMS_RE = re.compile(r"^(?:small|light)\s+arms?$|^rifles?$|^muskets?$|^personal weapons$", re.I)

_CIWS_RE = re.compile(
    r"phalanx|goalkeeper|ak-?630|kashtan|kortik|meroka|sea zenith|type 730|type 1130|type 1030|millennium|sea vulcan|vulcan|daewoo|dardo|sea ?rammer|\bcrotale\b", re.I
)
_MANPADS_RE = re.compile(rf"^\(?\s*(?:{_COUNT}\s+)?(?P<name>(?:Strela|Igla|Stinger|Mistral|Sea Sparrow|Seawolf|Sea Wolf|Sea Dart|Sea Cat|Seacat|Sea Slug|Sea Skua|Tigercat|Bloodhound|Crotale|RAM|Aspide|Albatros|Barak|Sea Chaparral|Chaparral|Redeye|Blowpipe|Javelin|Starburst|Starstreak|Terrier|Tartar|Talos|Standard|Harpoon|Exocet|Otomat|Penguin|Gabriel|Styx|Silkworm)\b.*)$", re.S | re.I)
_ASW_ROCKET_RE = re.compile(r"ASWRL|RBU|hedgehog|squid|limbo|mortar|mousetrap|bofors 375|ASROC|SUGW|ikara|malafon|stingray", re.I)

_JUNK_RE = re.compile(r"^(?:none|n/?a|nil|\?|\(?fitted for\)?|plus|and)$", re.I)


def _rounds_from(rest: str) -> int | None:
    m = re.search(r"\(([^)]*)\)", rest or "")
    if not m:
        return None
    inner = m.group(1)
    parts = [p.strip() for p in re.split(r"\+", inner)]
    ints = [int(p) for p in parts if re.fullmatch(r"\d+", p)]
    if ints:
        return ints[-1]
    m2 = re.match(r"\s*(\d+)\b", inner)
    return int(m2.group(1)) if m2 else None


def _counts(m: re.Match[str]) -> tuple[int, int]:
    d = m.groupdict()
    if d.get("n") is not None and d.get("m") is not None:
        return int(d["n"]), int(d["m"])
    if d.get("n2") is not None:
        return int(d["n2"]), 1
    return 1, 1


def _first_alt(piece: str) -> str:
    """Drop ' or ' alternatives at parenthesis depth 0 (first option wins)."""
    depth = 0
    for i, ch in enumerate(piece):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and piece[i:i + 4] == " or ":
            return piece[:i]
    return piece


def parse_weapon_piece(piece: str) -> dict[str, Any] | None:
    raw = piece.strip()
    p = _first_alt(raw).strip()
    # count ranges "(2 - 4) x 1 - 20/70" -> upper bound; an upper bound of 0 means "not fitted"
    p = re.sub(r"^\(\s*(\d+)\s*-\s*(\d+)\s*\)\s*x", lambda m: f"{m.group(2)} x", p)
    # "(1 x 64 + 1 x 32) Standard SM-2MR SAM (VLS+ 96 RIM-66)": two VLS groups -> one item with the total number of cells
    mg = re.match(r"^\(\s*(\d+)\s*x\s*(\d+)\s*\+\s*(\d+)\s*x\s*(\d+)\s*\)\s*(?P<rest>\S.*)$", p, re.S)
    if mg:
        p = f"1 x {int(mg.group(1)) * int(mg.group(2)) + int(mg.group(3)) * int(mg.group(4))} {mg.group('rest')}"
    whole_group = p.startswith("(") and _group_end(p) == len(p) - 1  # "(something in parentheses)" is a remark, not an item
    if not p or _JUNK_RE.match(p) or whole_group or re.match(r"^0\s*x", p):
        return None
    base: dict[str, Any] = {"kind": "other", "count": None, "per_mount": None, "caliber_mm": None, "caliber_length": None, "rounds": None, "name": None}
    if _SMALL_ARMS_RE.match(p):
        return {**base, "kind": "small_arms", "name": p[:30]}

    m = _TT_RE.match(p)
    if m and not (m.group("cal2") and (float(m.group("cal2")) < 100 or float(m.group("cal")) < 200)):  # "152/50 Armstrong TT" is a gun
        return _tube_item(base, m, float(m.group("cal")), _tube_name(m.group("k")))
    m = _GUN_RE.match(p)
    if m:
        n, per = _counts(m)
        cal = float(m.group("cal"))
        length = float(m.group("len"))
        name = m.group("name").strip() or None
        if _is_torpedo_not_gun(cal, length, name):
            return {**base, "kind": "torpedo_tube", "count": n, "per_mount": per, "caliber_mm": cal, "rounds": _rounds_from(name or ""), "name": "TT"}
        if name and re.search(r"ASWRL|RBU|\bASW\b", name):
            kind = "asw_rocket"
        elif name and re.search(r"\bgrenade\b", name, re.I):
            kind = "grenade_launcher"
        elif (name and _CIWS_RE.search(name)) or (per >= 6 and cal <= 35):
            kind = "ciws"
        elif cal < 20:
            kind = "mg"
        else:
            kind = "gun"
        return {**base, "kind": kind, "count": n, "per_mount": per, "caliber_mm": cal, "caliber_length": length, "name": name}
    m = _TT2_RE.match(p)
    if m and int(m.group("n")) >= 100:  # "533 TT (4)": the number is the caliber, the number of tubes is not given
        return {**base, "kind": "torpedo_tube", "count": None, "per_mount": 1, "caliber_mm": float(m.group("n")), "rounds": _rounds_from(m.group("rest")), "name": _tube_name(m.group("k"))}
    if m:
        return {**base, "kind": "torpedo_tube", "count": int(m.group("n")), "per_mount": 1, "rounds": _rounds_from(m.group("rest")), "name": _tube_name(m.group("k"))}
    m = _SPAR_RE.match(p)
    if m:
        return {**base, "kind": "torpedo_tube", "count": int(m.group("n") or 1), "per_mount": 1, "name": "spar"}
    m = _MISSILE_RE.match(p) or _MISSILE_NOCOUNT_RE.match(p)
    if m:
        mk = m.group("mk").upper()
        if mk in ("SSM", "SLCM", "SSGW", "ASM", "ASHM"):
            kind = "ssm"
        elif mk in ("SAM", "SAGW"):
            kind = "sam"
        elif mk in ("SLBM", "ICBM"):
            kind = "ballistic_missile"
        elif mk in ("ASROC", "SUGW", "ASUM"):
            kind = "asw_rocket"
        else:
            kind = "ssm"
        n, per = _counts(m) if "n" in m.groupdict() or "n2" in m.groupdict() else (1, 1)
        return {**base, "kind": kind, "count": n, "per_mount": per, "rounds": _rounds_from(m.group("rest")), "name": (m.group("name") or "").strip() or None}
    m = _RL_RE.match(p)
    if m and re.search(r"\b(?:RL|ASWRL|rocket|launcher|mortar|hedgehog|squid|limbo|mousetrap|RBU|MLRS|grenade)\b", m.group("name"), re.I):
        n, per = _counts(m)
        name = m.group("name").strip()
        kind = "asw_rocket" if _ASW_ROCKET_RE.search(name) else ("rocket_launcher" if re.search(r"rocket|MLRS|\bRL\b", name, re.I) else ("grenade_launcher" if re.search("grenade", name, re.I) else "other"))
        return {**base, "kind": kind, "count": n, "per_mount": per, "caliber_mm": float(m.group("cal")), "name": name}
    m = _RL2_RE.match(p)
    if m:
        n, per = _counts(m)
        name = m.group("name").strip()
        kind = "asw_rocket" if _ASW_ROCKET_RE.search(name) else "rocket_launcher"
        return {**base, "kind": kind, "count": n, "per_mount": per, "name": name}
    m = _DC_RE.match(p)
    if m:
        d = m.group("d").upper()
        n = int(m.group("n")) if m.group("n") else None
        return {**base, "kind": "depth_charge", "count": n, "per_mount": 1, "rounds": _rounds_from(m.group("rest")), "name": "DCT" if d == "DCT" else ("DCR" if d == "DCR" else "DC")}
    if re.search(r"\bmines?\b", p, re.I) and not _SWEEP_RE.search(p):
        mm = _MINE_RE.match(p)
        n = int(mm.group("n")) if mm and mm.group("n") else None
        return {**base, "kind": "mine", "count": n, "rounds": n}
    if _SWEEP_RE.search(p):
        return {**base, "kind": "minesweeping_gear", "name": p[:60]}
    if _FAC_RE.search(p):
        mm = re.match(r"^\(?\s*(\d+)\b", p)
        return {**base, "kind": "aviation_facility", "count": int(mm.group(1)) if mm else None, "name": p[:60]}
    m = _AIR_RE.match(p)
    if m:
        n = int(m.group("n")) if m.group("n") else 1
        return {**base, "kind": "aircraft_helicopter", "count": n, "name": p[:80]}
    m = _MANPADS_RE.match(p)
    if m:
        n, per = _counts(m) if (m.group("n") or m.group("n2")) else (1, 1)
        nm = m.group("name")
        kind = "ssm" if re.match(r"(?i)harpoon|exocet|otomat|penguin|gabriel|styx|silkworm|sea skua", nm) else "sam"
        return {**base, "kind": kind, "count": n, "per_mount": per, "rounds": _rounds_from(nm), "name": nm[:40]}
    m = _GUNS_RE.match(p) or _OLDGUN_RE.match(p)
    if m:
        n, per = _counts(m) if "m" in m.groupdict() else (int(m.group("n")), 1)
        return {**base, "kind": "gun", "count": n, "per_mount": per, "name": p[:60]}
    m = _GUN_NOLEN_RE.match(p)
    if m:
        n, per = _counts(m)
        cal = float(m.group("cal"))
        name = m.group("name").strip()
        if _is_torpedo_not_gun(cal, None, name):
            return {**base, "kind": "torpedo_tube", "count": n, "per_mount": per, "caliber_mm": cal, "rounds": _rounds_from(name), "name": "TT"}
        if re.search(r"\b(?:GL|grenade)\b", name):
            kind = "grenade_launcher"
        elif re.search(r"\b(?:RFL|RL|rocket|MLRS)\b", name, re.I):
            kind = "rocket_launcher"
        else:
            kind = "mg" if cal < 20 else "gun"
        return {**base, "kind": kind, "count": n, "per_mount": per, "caliber_mm": cal, "name": name[:60] or None}
    return {**base, "kind": "other", "name": p[:80]}


def _group_end(s: str) -> int | None:
    """Index of the ``)`` that closes the ``(`` at position 0, or None."""
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


_GROUP_ITEM_SIG = re.compile(r"\d\s*x\s*\d|\d+\s*/\s*\d+|\bTT\b")


def _unwrap_alt_groups(pieces: list[str]) -> list[str]:
    """``"(A+ B) or C"``: the first alternative is a whole group of items -> its items (the group is not "junk in parentheses")."""
    out: list[str] = []
    for p in pieces:
        alt = _first_alt(p).strip()
        if alt.startswith("(") and _group_end(alt) == len(alt) - 1 and _GROUP_ITEM_SIG.search(alt):
            out.extend(_unwrap_alt_groups(split_top(alt[1:-1])))
        else:
            out.append(p)
    return out


def parse_armament(text: str | None) -> dict[str, Any]:
    """Armament text -> {weapons, parse stats, variant info}; weapons=None if text empty."""
    t = _norm_text(text)
    if not t:
        return {"weapons": None, "n_pieces": 0, "n_other": 0, "variant": {"multi": False, "label": None}, "other_pieces": []}
    if re.fullmatch(r"(?i)none|nil|-", t):
        return {"weapons": [], "n_pieces": 0, "n_other": 0, "variant": {"multi": False, "label": None}, "other_pieces": []}
    pieces, var = first_variant_pieces(t, weapons=True)
    pieces = _unwrap_alt_groups(pieces)
    weapons: list[dict[str, Any]] = []
    other: list[str] = []
    for p in pieces:
        item = parse_weapon_piece(p)
        if item is None:
            continue
        if item["kind"] == "other":
            other.append(p[:80])
        weapons.append(item)
    n_other = len(other)
    return {"weapons": weapons, "n_pieces": len(weapons), "n_other": n_other, "variant": var, "other_pieces": other}


def main_gun(weapons: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Largest-caliber gun summary: caliber, total barrels of that caliber, mounts."""
    guns = [w for w in (weapons or []) if w["kind"] == "gun" and w.get("caliber_mm")]
    if not guns:
        return None
    cal = max(g["caliber_mm"] for g in guns)
    same = [g for g in guns if g["caliber_mm"] == cal]
    return {
        "caliber_mm": cal,
        "barrels": sum((g["count"] or 1) * (g["per_mount"] or 1) for g in same),
        "mounts": sum((g["count"] or 1) for g in same),
    }


# ----------------------------------------------------------------------------
# sensors
# ----------------------------------------------------------------------------

SENSOR_KINDS = [
    "air_search_radar",
    "surface_radar",
    "fire_control_radar",
    "navigation_radar",
    "radar",
    "sonar",
    "esm_ecm",
    "decoy",
    "eo_director",
    "combat_system",
    "other",
]

_KIND_KEYWORDS: list[tuple[str, re.Pattern[str]]] = [
    ("sonar", re.compile(r"sonar|hydrophone|asdic|\bs-?gerät|\bgruppenhorch|\bkdb\b|\bsq[srq]-|\bbq[qrsg]-|\bqcu|\bqhb|\bpuffs\b", re.I)),
    ("decoy", re.compile(r"decoy|chaff|srboc|flare|nixie|\bwdc\b|torpedo decoy", re.I)),
    ("esm_ecm", re.compile(r"\becm\b|\besm\b|\bresm\b|\bcesm\b|intercept|jammer|jamming|\bews\b|\bwlr-|\bwlq-|\bslq-|\bulq-|\bnlq|\bnolr|\bolt-|\bnla-|warning receiver|rwr|sigint|df\b|direction finder", re.I)),
    ("eo_director", re.compile(r"e/o|electro-?optic|optronic|\bflir\b|optical|\bdirector\b|\blaser", re.I)),
    ("combat_system", re.compile(r"\bccs\b|combat (?:data )?system|\bcds\b|\bcic\b|sewaco|tacticos|\bacds\b|data ?link|\bnTDS|\bnTDS\b|command system|\bndbs?\b|\bagis\b|\bssds\b|fire control system|\bfcs\b|\blink\b", re.I)),
    ("air_search_radar", re.compile(r"air[- ]?search|early[- ]warning|\baew\b|\b3d\b|long[- ]range (?:air )?search|air surveillance|volume search|\bspy-|\bsps-(?:12|29|37|39|40|43|48|49|52|65)", re.I)),
    ("surface_radar", re.compile(r"surface[- ]?(?:search|warning)|\bss radar|low[- ]?(?:altitude|level)|\bsps-(?:5|6|10|53|55|58|59|63|64|67)\b|target indication|\bti radar", re.I)),
    ("fire_control_radar", re.compile(r"fire[- ]?control|\bfc\b|gunnery|director radar|tracking|target acquisition|illuminat|\bspg-|\bstir\b|\bsqg|\bwm-?2[0-9]|\bmk ?\d+ (?:radar|fc)|\blirod|\bmirador", re.I)),
    ("navigation_radar", re.compile(r"navigation|\bnav\b|\bspn-|\bi-?band nav|furuno|decca|racal|bridgemaster|\bkelvin[- ]hughes\b.*nav|\bnav radar|pilot", re.I)),
    ("radar", re.compile(r"radar|\bsps-|\bsp[a-z]-\d|\btype \d{3,4}[a-z]?\b|\bops-\d|\baws-\d|\bmw-\d|\bln-\d|\bsmart|\bscanter|\bscout\b|\btamir|\bgiraffe|\blotsiya|\bmerkuriy|\bmius|\bzarnitsa|\bstvor|\breya|\bsap?-?\d|\bkh-|\bnws|\bwm-|\bda-0|\bzw-0|\blw-0|\bdk-?\d", re.I)),
]

_RADAR_WORD = re.compile(r"radars?\b", re.I)
_PLURAL_SUFFIX = re.compile(
    r"(?P<kw>radars?|sonars?|hydrophones?|ecm(?: suites?)?|esm(?: suites?)?|e/o (?:directors?|systems?)|decoy(?: rl| launchers?)?|ccs|director)\s*$", re.I
)


_DESC_RADAR = re.compile(r"air[- ]?search|early[- ]warning|surface[- ]?(?:search|warning)|fire[- ]?control|navigation|gunnery|target (?:indication|acquisition)|3d|tracking|illuminat", re.I)


def _desc_kind(text: str) -> str | None:
    """Kind from descriptive words only (no model-name prefixes)."""
    for kind, rx in _KIND_KEYWORDS:
        if kind in ("air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar"):
            if _DESC_RADAR.search(text) and rx.search(text):
                return kind
        elif kind != "radar" and rx.search(text):
            return kind
    return "radar" if _RADAR_WORD.search(text) else None


def _kind_of_item(text: str) -> str | None:
    for kind, rx in _KIND_KEYWORDS:
        if rx.search(text):
            return kind
    return None


def _refine_radar(kind: str, text: str) -> str:
    if kind == "radar":
        for k in ("air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar"):
            rx = dict(_KIND_KEYWORDS)[k]
            if rx.search(text):
                return k
    return kind


def parse_sensors(text: str | None) -> dict[str, Any]:
    t = _norm_text(text)
    if not t:
        return {"sensors": None, "variant": {"multi": False, "label": None}}
    pieces, var = first_variant_pieces(t)
    sensors: list[dict[str, Any]] = []
    pending: list[int] = []
    for p in pieces:
        p0 = p.strip()  # alternatives ("Donets-2 or Liman radar") are kept: the kind word often follows the "or"
        if not p0 or _JUNK_RE.match(p0):
            continue
        count = None
        m = re.match(r"^(\d+)\s*x?\s+(?=\S)", p0)
        if m:
            count = int(m.group(1))
        kind = _kind_of_item(p0)
        item: dict[str, Any] = {"kind": kind or "other", "name": p0[:80], "count": count}
        if kind is None:
            sensors.append(item)
            pending.append(len(sensors) - 1)
            continue
        item["kind"] = _refine_radar(kind, p0)
        sensors.append(item)
        # a plural suffix ("... radars") applies to the untyped names before it;
        # it conveys the *generic* kind, not the refinement from this item's own model name
        if _PLURAL_SUFFIX.search(p0) and pending:
            generic = _desc_kind(p0) or kind
            if generic in ("surface_radar", "air_search_radar", "fire_control_radar", "navigation_radar") and not _DESC_RADAR.search(p0):
                generic = "radar"
            for idx in pending:
                sensors[idx]["kind"] = generic
        pending = []
    return {"sensors": sensors, "variant": var}


def sensor_kinds(sensors: list[dict[str, Any]] | None) -> list[str] | None:
    if sensors is None:
        return None
    return sorted({s["kind"] for s in sensors if s["kind"] != "other"})


# ----------------------------------------------------------------------------
# columns that the re-fetched NSDB fills: Armour, Displacement_standard, extra_specs
# ----------------------------------------------------------------------------

_ARMOUR_PARTS: list[tuple[str, re.Pattern[str]]] = [
    ("conning_tower", re.compile(r"\bCT\b|conning", re.I)),
    ("barbette", re.compile(r"barbette|redoubt", re.I)),
    ("turret", re.compile(r"turret|gun ?house", re.I)),
    ("casemate", re.compile(r"casemate|battery", re.I)),
    ("belt", re.compile(r"\bbelt|^(?:main |upper |lower )?sides?$|waterline", re.I)),
    ("deck", re.compile(r"deck", re.I)),
    ("bulkhead", re.compile(r"bulkhead|traverse", re.I)),
    ("gun_shield", re.compile(r"shield", re.I)),
]
_ARMOUR_MAX_MM = 1000.0


def parse_armour(text: str | None) -> dict[str, Any]:
    """``CLASS.Armour`` -> {"material", "parts": [{"part", "label", "max_mm"}]}.

    Items are joined by ``"+ "``; an item reads ``"label: 356 - 343"`` (thickest first), optionally
    preceded by ``"<material>; "``.  ``max_mm`` is the largest number of the item (0 < v <= 1000).
    Items without a label or without a number are dropped.
    """
    t = _norm_text(text)
    out: dict[str, Any] = {"material": None, "parts": []}
    if not t:
        return out
    for piece in split_top(t, "+"):
        if ";" in piece and (piece.index(";") < (piece.index(":") if ":" in piece else len(piece))):
            mat, piece = piece.split(";", 1)
            if out["material"] is None and mat.strip():
                out["material"] = mat.strip()[:60]
        colon = _top_colon(piece)
        if colon is None:
            continue
        label, value = piece[:colon].strip(), piece[colon + 1:]
        nums = [float(x) for x in re.findall(_FLOAT, value) if 0 < float(x) <= _ARMOUR_MAX_MM]
        if not label or not nums:
            continue
        part = next((name for name, rx in _ARMOUR_PARTS if rx.search(label)), "other")
        out["parts"].append({"part": part, "label": label[:60], "max_mm": max(nums)})
    return out


def armour_max(parts: list[dict[str, Any]] | None, part: str) -> float | None:
    vals = [p["max_mm"] for p in (parts or []) if p["part"] == part]
    return max(vals) if vals else None


_DWT = re.compile(r"\d\s*dwt\b", re.I)


def parse_displacement_standard(text: str | None) -> dict[str, Any]:
    """``CLASS.Displacement_standard`` -> parse_displacement() result; deadweight ("305dwt") is not a displacement."""
    if _DWT.search(text or ""):
        return {"value": None, "submerged": None, "label": None, "multi": False, "flags": ["displacement_standard_dwt"]}
    return parse_displacement(text)


def parse_diving_depth_m(extra_specs: str | None) -> tuple[float | None, str | None, bool]:
    """``CLASS.extra_specs`` (JSON) -> operational diving depth in metres (first variant), if any."""
    if not extra_specs:
        return None, None, False
    try:
        import json

        d = json.loads(extra_specs)
    except (ValueError, TypeError):
        return None, None, False
    if not isinstance(d, dict):
        return None, None, False
    raw = next((v for k, v in d.items() if str(k).lower().startswith("diving depth")), None)
    if not isinstance(raw, str) or not raw.strip():
        return None, None, False
    v, label, multi = parse_first(raw)
    if v is not None and v <= 0:
        v = None
    return v, label, multi
