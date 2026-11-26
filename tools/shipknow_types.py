"""Ship-type normalisation for the shipknow (艦艇理解) dataset.

NSDB ``CLASS.TYPE`` is a slugified section heading from navypedia (1,400+
distinct strings such as ``patrol-boats``, ``and-destroyers``,
``auxiliary-minesweepers-of-interwar-and-eras``).  This module turns it into

* ``fine``   : a singular, de-noised slug (``patrol-boat``)
* ``coarse`` : one of ``COARSE_TYPES`` (26 classes + ``unknown``)

Resolution order (recorded per row as ``type_source``):
  1. ``type``       - ordered keyword rules on the fine slug (``RULES``)
  2. ``type+year``  - ``YEAR_RULES`` for ambiguous heads (sloop/aviso)
  3. ``url_code``   - the section code in the source URL (``ru_ms_t651`` -> ``ms``)
                      only for rows whose TYPE is empty/generic
  4. ``unknown``    - excluded from T1/T2 targets

The resolved table (every raw TYPE -> fine/coarse/rule) is written to
``data/navspec/shipknow/type_map.json`` by ``shipknow_extract.py``.
"""

from __future__ import annotations

import re
from typing import Any

# ``ship_of_the_line`` : ships of the line of every era (sail and screw; defined by rate, not propulsion).
# ``sailing_ship``      : wind-powered warships that are *not* ships of the line (sailing frigates, corvettes, brigs, sloops,
#                         schooners, cutters, bomb vessels).  Added for v0_2: the re-fetched NSDB spells these as
#                         ``sailing-*`` TYPEs and the old catch-all ``early_warship`` mixed ~190 of them with paddle/screw steamers.
#                         It coexists with ``ship_of_the_line`` (steam-powered screw line ships belong to the latter, not to sail).
# ``early_warship``     : what is left of the pre-1880 catch-all: paddle / screw / composite / iron steamers, avisos, despatch vessels.
COARSE_TYPES = [
    "battleship",
    "battlecruiser",
    "ship_of_the_line",
    "ironclad",
    "coastal_defense_monitor",
    "aircraft_carrier",
    "armored_protected_cruiser",
    "heavy_cruiser",
    "light_cruiser",
    "cruiser",
    "destroyer",
    "frigate",
    "corvette",
    "escort",
    "torpedo_boat",
    "fast_attack_craft",
    "patrol",
    "coast_guard",
    "gunboat_river",
    "submarine",
    "mine_warfare",
    "amphibious",
    "auxiliary",
    "training",
    "early_warship",
    "sailing_ship",
]
UNKNOWN = "unknown"

# --- fine slug normalisation -------------------------------------------------

# "ships-of-the-line" must survive the "-of-..." cutter and the singulariser: it is swapped for a hyphen-free token and back
_SOTL = re.compile(r"ships?-of-the-line")
_SOTL_TOKEN = "shipoftheline"
_DROP_PREFIX = re.compile(r"^(?:(?:class|type|group|new-class|project|modified|follow-up|initial|repeat|repeat-type|later|and|program)-)+")
_CUT_SUFFIX = re.compile(
    r"-(?:of|for|australian-navy-australia|abkhazian-navy-abkhazia|s-abkhazian-navy-abkhazia|s-australian-navy-australia|"
    r"before|since|during|also|in|with|converted-from-merchant-vessels|converted-after)(?:-.*)?$"
)
_IRREG = {"batteries": "battery", "launches": "launch", "lanches": "launch", "boats": "boat", "ships": "ship"}


def _singular(tok: str) -> str:
    if tok in _IRREG:
        return _IRREG[tok]
    if tok in ("craft", "crafts"):
        return "craft"
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    if tok.endswith("ches") or tok.endswith("shes"):
        return tok[:-2]
    if tok.endswith("ss") or tok.endswith("us") or tok.endswith("is"):
        return tok
    if tok.endswith("s") and len(tok) > 3:
        return tok[:-1]
    return tok


def fine_label(raw_type: str | None) -> str:
    """Raw TYPE slug -> singular, de-noised fine label ('' when empty)."""
    slug = (raw_type or "").strip().lower()
    if not slug:
        return ""
    slug = _SOTL.sub(_SOTL_TOKEN, slug)
    slug = _CUT_SUFFIX.sub("", slug)
    slug = _DROP_PREFIX.sub("", slug)
    toks = [t for t in slug.split("-") if t]
    toks = [_singular(t) for t in toks]
    # collapse repeated words/phrases: destroyer-destroyer -> destroyer
    out: list[str] = []
    for t in toks:
        if not out or out[-1] != t:
            out.append(t)
    half = len(out) // 2
    if half and len(out) % 2 == 0 and out[:half] == out[half:]:
        out = out[:half]
    return "-".join(out).replace(_SOTL_TOKEN, "ship-of-the-line")


# --- ordered keyword rules (first match wins) -------------------------------
# Each rule: (coarse, regex searched in the fine slug)
RULES: list[tuple[str, str]] = [
    ("ship_of_the_line", r"ship-of-the-line|^rate(?:-sailing|-screw)?$"),
    ("sailing_ship", r"^sailing-|^(?:brig|schooner|xebec)$"),
    # semi-submersible craft and anti-submarine patrol vessels are not submarines
    ("fast_attack_craft", r"semi-submersible-fast-attack"),
    ("patrol", r"semi-submersible|anti-submarine-vessel"),
    # chasers / depot / decoy are never "submarine" even if the word appears
    ("patrol", r"submarine-chaser|chaser"),
    ("auxiliary", r"submarine-depot|submarine-decoy|decoy-ship|decoy-vessel|torpedo-depot"),
    ("submarine", r"submarine|submersible|submerging"),
    # amphibious carriers before aircraft carriers
    ("amphibious", r"amphibious|landing-aircraft|assault-helicopter|helicopter-carrier(?<!training-helicopter-carrier)|assault-ship|assault-craft|assault-boat|assault-launch|assault-landing"),
    ("training", r"training|aviation-training"),
    ("cruiser", r"aircraft-carrying-cruiser|helicopter-cruiser|missile-cruiser"),
    ("aircraft_carrier", r"carrier|seaplane-tender-light-aircraft"),
    ("battlecruiser", r"battle-?cruiser"),
    ("coastal_defense_monitor", r"coast(?:al)?-defen[cs]e|coastal-battleship|monitor|floating-batter|mortar-vessel|mortar-float|maritime-coastal-defen"),
    ("battleship", r"battle-?ship|batleship"),
    ("ironclad", r"ironclad|armou?red-(?:frigate|corvette|ship|ram|steamship|gun-?vessel|gunboat|turret)|barbette|turret-(?:ship|ram|citadel|barbette)|central-battery|centre-battery|casemate|broadside|breastwork|citadel|torpedo-ram|armou?red-ram"),
    ("coast_guard", r"coast-?guard|border-guard|board-guard|custom|revenue|fishery|fisheries|smuggling|police|rescue-patrol"),
    ("heavy_cruiser", r"heavy-cruiser|light-heavy-cruiser|large-cruiser"),
    ("light_cruiser", r"light-cruiser|scout-cruiser|small-cruiser"),
    ("armored_protected_cruiser", r"armou?red-cruiser|belted-cruiser|protected-cruiser|unprotected|steel-cruiser|wooden-cruiser|torpedo-cruiser|colonial-cruiser|composite-cruiser|dynamite-cruiser|steel-unarmoured|iron-unprotected"),
    ("auxiliary", r"armed-merchant|auxiliary-merchant|auxiliary-cruiser|armed-yacht|armed-steamer|armed-trawler|armed-boarding|armed-river-steamer|armed-dispatch|armed-survey|armed-vessel|blockade-runner|requisitioned|ocean-boarding|converted-fishing|auxiliary-(?:transport|vessel|seaplane|border)|small-auxiliary-cruiser"),
    ("cruiser", r"cruiser"),
    # mine warfare before gunboat/patrol so 'patrol-minesweeper' stays mine warfare
    ("amphibious", r"tank-landing-ship-minelayer"),
    ("mine_warfare", r"minesweep|minehunt|minelay|minefield|mine-(?:vessel|transport|destruction|countermeasure|warfare|depot|motor|route)|netlayer|charge-layer|sweep-tug|sweeping|drone-minesweep|fleet-sweeping|controlled-mine|mine-"),
    ("amphibious", r"landing|amphibious|troop|high-speed-transport|fast-amphibious|attack-cargo|attack-transport|personnel-transport|vehicle-ferry|commando|swimmer-delivery|jetfoil-landing|mobile-landing|lcvp|ground-effect|pinnace"),
    # ---- surface combatants / small craft (order matters) ----
    # escorts that contain the word 'destroyer'
    ("escort", r"destroyer-escort|escort-destroyer|missile-destroyer-escort|^escort|-escort$|escort-ship|convoy-sloop|convoy-escort|convoy-vessel|patrol-escort|coastal-escort|commissioned-escort|fast-escort|guard-ship|guard-vessel"),
    ("destroyer", r"destroy|detsroyer|^leader|flotilla-leader|-leader$|and-leader|torpedo-boat-destroyer"),
    # river motor gunboats are river gunboats (same as the armoured-motor-gunboat overrides), not seagoing fast attack craft
    ("gunboat_river", r"river-(?:armou?red-)?motor-gun|armou?red-river-motor-gun"),
    # fast attack craft (motor torpedo/gun boats, missile boats, hydrofoils)
    ("fast_attack_craft", r"motor-torpedo|motor-gun|torpedo-attack|fast-torpedo|fast-attack|fast-gun-attack|fast-missile|attack-craft|attack-hydrofoil|missile-(?:boat|attack|armed|patrol|hydrofoil|ground)|guided-missile-patrol|gun-hydrofoil|torpedo-hydrofoil|hydrofoil-torpedo|climbing-motor|midget-torpedo|mine-motor|motor-torpedo-gun|air-cushion-motor|experimental-motor|modified-motor|large-motor|small-motor|armou?red-motor-torpedo|small-missile|fast-patrol-boat|hydrofoil|surface-effect"),
    ("gunboat_river", r"gun-?boat|gun-?vessel|gun-launch|gun-ship|river|canal-gun|lake-patrol|riverine|flatiron|support-gunboat|torpedo-gun-boat"),
    ("torpedo_boat", r"torpedo-boat|torpedo-ship|torpedo-vessel|torpedo-steamship|torpedo-dispatch|spar-torpedo|^torpedo$"),
    ("frigate", r"(?<!screw-)(?<!paddle-)(?<!sailing-)(?<!wooden-)(?<!iron-)^(?:(?:light|small|patrol|missile|guided-missile|project|early|later|and-earlier)-)?frigate$|^frigate|and-earlier-frigate|early-frigate|later-frigate"),
    ("corvette", r"^(?:(?:missile|fast-missile|patrol|large|small|modified|project|multimission)-)?corvette$|missile-corvette|patrol-corvette"),
    # naval drifters / trawlers carry "-steel-hull" / "-wooden-hull" suffixes that would otherwise look like early steamers
    ("patrol", r"^(?:drifter|trawler)s?-(?:steel|wooden)-hull"),
    ("early_warship", r"sailing|paddle|screw|composite|brig$|^brig|schooner|xebec|clipper|despatch|dispatch|wooden|^iron-|steel-|colonial|^sloop|^aviso|-aviso|sloop|aviso|frigate|corvette|cutter|steam-gun|gunboat-yacht|^armou?red"),
    ("patrol", r"patrol|partol|launch|interceptor|motor-boat|inshore-patrol|guard-boat|harbou?r-defen|seaward-defen|^craft|-craft$|fast-boat|small-ship|hovercraft|trawler|whaler|^boat|-boat$"),
    ("auxiliary", r"tender|transport|supply|tanker|depot|repair|survey|cable|diving|support-ship|logistic|tug|yacht|auxiliary|converted|decoy|special-service|command|aircraft-transport|catapult|target|fighter-direction|sonobuoy|special-operation|screening|vehicle|frogmen|multi|flexible|theat|multipurpose|support|lighter|water-tank|explosive|gunnery"),
]
_COMPILED = [(c, re.compile(p)) for c, p in RULES]

# exact-slug overrides for odd cases (raw TYPE slug -> coarse)
OVERRIDES: dict[str, str] = {
    "light-battlecruiser-aircraft-carrier": "aircraft_carrier",
    "cruiser-corvette": "early_warship",
    "cruiser-minelayer": "cruiser",
    "cruisers-minelayers": "cruiser",
    "light-cruisers-minelayers": "light_cruiser",
    "minelaying-cruiser": "cruiser",
    "torpedo-boats-destroyers": "destroyer",
    "torpedo-boats-destroyer-escorts": "escort",
    "water-tanker-with-landing-capability": "auxiliary",
    "water-tankers-with-landing-capability": "auxiliary",
    "torpedo-boat-carrier": "torpedo_boat",
    "seaplane-tenders-light-aircraft-carriers": "aircraft_carrier",
    "dock-landing-ship-seaplane-tender": "amphibious",
    "gunboat-seaplane-tender": "gunboat_river",
    "gunboats-seaplane-tenders": "gunboat_river",
    "gunboat-tender": "gunboat_river",
    "protected-cruisers-corvettes": "armored_protected_cruiser",
    "minesweepers-frigates": "mine_warfare",
    "heavy-cruisers-command-ship": "heavy_cruiser",
    "destroyers-minelayers": "destroyer",
    "survey-vessel-minelayer": "mine_warfare",
    "survey-vessels-minelayers": "mine_warfare",
    "cable-ships-with-minelaying-netlaying-capacity": "auxiliary",
    "submarine-depot-ship-minelayer": "auxiliary",
    "tugs-converted-to-minesweepers-during": "mine_warfare",
    "minelayer-cablelayer": "mine_warfare",
    "armoured-motor-gunboats": "gunboat_river",
    "armoured-motor-gunboat": "gunboat_river",
    "armoured-gunboats": "ironclad",
    "armoured-gun-vessels": "ironclad",
    "armoured-river-gunboats": "gunboat_river",
    "river-armoured-motor-gunboats": "gunboat_river",
    "armoured-motor-torpedo-boats": "fast_attack_craft",
    "armoured-motor-boat": "patrol",
    "armoured-motor-boats": "patrol",
    "type-armoured-motor-boats": "patrol",
    "type-armoured-small-submarine-chasers": "patrol",
    "torpedo-gunboat": "gunboat_river",
    "torpedo-gunboats": "gunboat_river",
    "gun-and-torpedo-vessels": "gunboat_river",
    "armed-survey-patrol-vessel": "patrol",
    "gunnery-training-ship": "training",
    "gunnery-training-ship-floating-battery": "training",
    "gunnery-training-ship-minelayer": "training",
    "type-gunnery-lighters": "auxiliary",
    "type-multipurpose-landing-minesweeping-gun-ships": "amphibious",
    "multifunctional-patrol-craft": "patrol",
    "multimission-patrol-ships": "patrol",
    "multirole-warships": "auxiliary",
    "multirole-vessel": "auxiliary",
    "multipurpose-ship": "auxiliary",
    "small-gun-ships": "gunboat_river",
    "large-gun-ships": "gunboat_river",
    "new-class-gun": "gunboat_river",
    "littoral-combat-ships": "corvette",
    "patrol-combatant": "patrol",
    "patrol-combatants": "patrol",
    "patrol-and-training-craft": "patrol",
    "wooden-hull": "early_warship",
    "steel-hull": "early_warship",
    "hydrofoil-patrol-boats": "fast_attack_craft",
    "patrol-hydrofoil": "fast_attack_craft",
    "patrol-hydrofoils": "fast_attack_craft",
    "hydrofoil-border-guard-boats": "coast_guard",
    "attack-craft-hydrofoils-border-guard-boats": "coast_guard",
    "small-submarine-chasers-border-guard-boats": "patrol",
    "motor-launches-border-guard-boats": "patrol",
    "river-patrol-boats": "gunboat_river",
    "river-patrol-craft": "gunboat_river",
    "river-patrol-launches": "gunboat_river",
    "river-patrol-launch": "gunboat_river",
    "river-patrol-boat": "gunboat_river",
    "river-patrol-vessels": "gunboat_river",
    "river-patrol-ships": "gunboat_river",
    "riverine-patrol-boats": "gunboat_river",
    "riverine-patrol-boat": "gunboat_river",
    "river-motor-patrol-boat": "gunboat_river",
    "river-motor-patrol-boats": "gunboat_river",
    "river-patrols-of-early-century": "gunboat_river",
    "river-guard-ship": "gunboat_river",
    "river-guard-ships": "gunboat_river",
    "river-guard-ships-of": "gunboat_river",
    "river-guard-ships-of-war": "gunboat_river",
    "river-troop-transport": "amphibious",
    "river-troop-transports": "amphibious",
    "riverine-troop-transports-gunboats": "amphibious",
    "river-landing-craft": "amphibious",
    "river-assault-infantry-landing-craft": "amphibious",
    "river-assault-support-patrol-boats": "gunboat_river",
    "river-torpedo-boat": "torpedo_boat",
    "river-torpedo-boats": "torpedo_boat",
    "river-minesweepers": "mine_warfare",
    "river-minelaying-boats": "mine_warfare",
    "river-minelayer": "mine_warfare",
    "river-mine-destruction-vessels": "mine_warfare",
    "river-customs-steamer": "coast_guard",
    "river-dispatch-vessels-of-war": "early_warship",
    "river-floating-batteries": "coastal_defense_monitor",
    "river-armed-steamship": "auxiliary",
    "river-vessels-of": "gunboat_river",
    "river-vessels": "gunboat_river",
    "paddle": "early_warship",
    "screw": "early_warship",
    "sailing": "early_warship",
    "type-midget-human-torpedo-carriers": "auxiliary",
    "floating-torpedo-batteries-of": "coastal_defense_monitor",
    "floating-torpedo-batteries": "coastal_defense_monitor",
    "yachts-of": "auxiliary",
    "yachts-of-war": "auxiliary",
}

# (fine-slug regex, year predicate) -> coarse; applied when year is known
YEAR_RULES: list[tuple[str, int, str, str]] = [
    # (regex on fine slug, cutoff year, coarse if year < cutoff, coarse otherwise)
    (r"^(?:colonial-)?sloop$", 1900, "early_warship", "escort"),
    (r"^aviso$|^colonial-aviso$|^class-aviso$", 1900, "early_warship", "escort"),
    (r"^corvette$", 1880, "early_warship", "corvette"),
    (r"^frigate$", 1880, "early_warship", "frigate"),
    (r"^gunboat$", 0, "gunboat_river", "gunboat_river"),
]

# unmapped / generic TYPE -> URL section code fallback
URL_CODE_MAP: dict[str, str] = {
    "ss": "submarine",
    "cv": "aircraft_carrier",
    "ms": "mine_warfare",
    "ml": "mine_warfare",
    "mw": "mine_warfare",
    "ls": "amphibious",
    "aws": "amphibious",
    "am": "amphibious",
    "cg": "coast_guard",
    "bg": "coast_guard",
    "pf": "coast_guard",
    "cf": "patrol",
    "sc": "patrol",
    "pc": "patrol",
    "mb": "patrol",
    "pt": "fast_attack_craft",
    "tb": "torpedo_boat",
    "dd": "destroyer",
    "es": "escort",
    "esc": "escort",
    "de": "escort",
    "cr": "cruiser",
    "fr": "frigate",
    "ff": "frigate",
    "pg": "gunboat_river",
    "rb": "gunboat_river",
    "mon": "coastal_defense_monitor",
    "aux": "auxiliary",
}
# TYPE slugs that carry no usable information (fall through to URL code)
GENERIC_TYPES = {
    "", "order", "follow-up-order", "initial-order", "program", "purchased", "ships", "ship",
    "vessel", "vessels", "boats", "boat", "craft", "type", "coastal", "landing", "patrol", "patrols", "inshore",
    "amphibious", "large-ships", "small-ships", "light-craft", "medium-craft", "type-group", "ships-of",
    "boats-of", "built-small-boats", "unnamed-ordered-in", "purchased-on-the-stocks", "program-and-submarines",
    "war-and-submarines", "vessels-of", "patrol-of", "wooden-hull",
    "steel-hull", "large-ship", "small-ship", "medium-craft", "craft-border-guard-boats", "crafts", "series-submarines",
}


_CODE_ALIAS = {"amph": "ls", "mine": "ms", "cf": "cf", "c_f": "cf"}
_KNOWN_CODES = set(URL_CODE_MAP) | {"of", "bb", "cm", "cs", "gc", "co", "oth", "av", "ma", "se", "sl"}


def url_code(source_url: str | None) -> str | None:
    """'.../ru_ms_t651.htm' -> 'ms'; 'brit_o_esc_basset' -> 'esc'; 'brit_c_f_rona' -> 'cf'."""
    if not source_url:
        return None
    base = source_url.rsplit("/", 1)[-1]
    parts = base.split("_")
    if len(parts) < 3:
        return None
    for cand in (parts[1], parts[1] + "_" + parts[2] if len(parts) > 3 else "", parts[2]):
        cand = _CODE_ALIAS.get(cand, cand)
        if cand in _KNOWN_CODES:
            return cand
    return None


def type_from_slug(raw_type: str | None) -> tuple[str, str, str]:
    """Return (fine, coarse_or_unknown, rule_id) using TYPE only."""
    raw = (raw_type or "").strip().lower()
    fine = fine_label(raw)
    if raw in GENERIC_TYPES or fine in GENERIC_TYPES:
        return fine, UNKNOWN, "generic"
    if raw in OVERRIDES:
        return fine, OVERRIDES[raw], f"override:{raw}"
    for coarse, rx in _COMPILED:
        if rx.search(fine):
            return fine, coarse, f"rule:{coarse}:{rx.pattern[:40]}"
    return fine, UNKNOWN, "unmapped"


CODE_PREFERRED = {"cutter": {"cg": "coast_guard", "bg": "coast_guard"}, "cutters": {"cg": "coast_guard", "bg": "coast_guard"}}
# modern cutter TYPEs the re-fetched NSDB spells with extra words ("fast-response-cutters", "cutter-icebreaker", "cutters-also"):
# a cutter on a coast-guard URL is a coast-guard vessel; sailing cutters (pre-1860 naval cutters) are not.
_CUTTER = re.compile(r"(?:^|-)cutters?(?:-|$)")


def classify(raw_type: str | None, source_url: str | None = None, year: int | None = None, class_name: str | None = None) -> dict[str, Any]:
    fine, coarse, rule = type_from_slug(raw_type)
    source = "type"
    code = url_code(source_url)
    pref = CODE_PREFERRED.get((raw_type or "").strip().lower())
    if pref is None and _CUTTER.search(fine) and not fine.startswith("sailing-"):
        pref = CODE_PREFERRED["cutter"]
    if pref and code in pref:
        return {"fine": fine or None, "coarse": pref[code], "type_source": "type+url_code", "type_rule": f"code_preferred:{code}"}
    if coarse != UNKNOWN:
        for rx, cutoff, before, after in YEAR_RULES:
            if year is not None and re.search(rx, fine):
                new = before if year < cutoff else after
                if new != coarse:
                    coarse, source, rule = new, "type+year", f"year_rule:{rx}<{cutoff}"
                break
    if coarse in (UNKNOWN, "early_warship") and class_name and re.search(r"ships?-of-the-line", class_name):
        coarse, source, rule = "ship_of_the_line", "type+class_name", "class_name:ship-of-the-line"
    if coarse == UNKNOWN:
        if code in URL_CODE_MAP:
            coarse, source, rule = URL_CODE_MAP[code], "url_code", f"url_code:{code}"
        else:
            source = "unknown"
    return {"fine": fine or None, "coarse": coarse, "type_source": source, "type_rule": rule}
