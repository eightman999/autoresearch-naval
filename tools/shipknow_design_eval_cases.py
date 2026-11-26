"""設計評価 v1 の手書きケース: D（OOD / 架空・改変歴史）と E（不可能 / 敵対的）。

構造化要求の語彙は v0 の設計 JSON 語彙（required の kind、センサー kind、武装 kind、搭載機）と拡張キー（shipknow_design_eval_req.py）に合わせる。
AESA / VLS / Aegis など語彙に無いものは要求文（extra_phrases）に書き、構造化要求では近い kind（air_search_radar、sam）にして、元の語を original_terms に残す。

D の架空国家名は実在しない名前を新しく作った（Valdoria / Norvane / Kestria / Aurelan / Tarsonia / Meridia / Zephyria / Brannoch / Solmara / Ysbelle）。
近傍は規則 NB-1 のうち「同時代の他国艦」だけが働く（同国の系譜枠は空になる。国名が train のどの艦とも一致しないため）。
D の era_waivers は、前提（「レーダーが 10 年早く成熟した世界」など）が許す年代外装備の能力語彙。採点器は年代検査（era_early）のうち、これに該当する違反を数えない。
"""

from __future__ import annotations

from typing import Any

import shipknow_design_eval_req as X

FICTIONAL_COUNTRIES = ["Valdoria", "Norvane", "Kestria", "Aurelan", "Tarsonia", "Meridia", "Zephyria", "Brannoch", "Solmara", "Ysbelle"]

P_JP = "Assume Japan won the war and kept its navy; "
P_DE = "Assume Germany won in Europe and kept building a surface fleet; "
P_IT = "Assume Italy ended the war on the winning side and expanded its navy; "
P_RADAR = "Assume search and fire-control radar matured ten years earlier than in reality; "
P_FICT = "{c} is a fictional medium naval power with an industrial base like a European middle power; "


def _req(country: str, year: int, ship_type: str, fine: str | None, mission: list[str], disp: float, basis: str, vmin: float | None, required: list[str],
         **ext: Any) -> dict[str, Any]:
    r: dict[str, Any] = {"country": country, "year": year, "ship_type": ship_type}
    if fine:
        r["ship_type_fine"] = fine
    r["mission"] = mission
    r["max_displacement_t"] = disp
    r["displacement_basis"] = basis
    if vmin is not None:
        r["min_speed_kn"] = vmin
    r["required"] = required
    r.update(ext)
    return X.order_req(r)


def _d(i: int, kind: str, premise: str, req: dict[str, Any], waive: list[str] | None = None, note: str = "") -> dict[str, Any]:
    return {"id": f"d{i:02d}", "category": "D", "premise": premise.strip(), "req": req, "extra_phrases": [], "extra_phrases_ja": [],
            "meta": {"fiction_kind": kind, "era_waivers": waive or [], "note": note}}


def _fict(c: str, tail: str = "") -> str:
    return P_FICT.format(c=c) + tail


def cases_D() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = 0

    def add(kind: str, premise: str, req: dict[str, Any], waive: list[str] | None = None, note: str = "") -> None:
        nonlocal i
        i += 1
        out.append(_d(i, kind, premise, req, waive, note))

    # ---- 戦勝国・改変歴史（Japan / Germany / Italy が勝った世界。戦後の海軍の継続）
    add("victor_alt", P_JP + "this is a postwar fleet destroyer.", _req("Japan", 1949, "destroyer", None, ["surface_strike", "escort"], 2700, "standard", 35, ["main_gun>=127mm", "torpedo_tubes", "radar"]))
    add("victor_alt", P_JP + "this is a postwar light cruiser.", _req("Japan", 1956, "light_cruiser", None, ["surface_strike", "aaw"], 8500, "standard", 33, ["main_gun>=152mm", "air_search_radar", "aircraft"]))
    add("victor_alt", P_JP + "this is a postwar fast attack craft.", _req("Japan", 1951, "fast_attack_craft", None, ["surface_strike"], 90, "full", 40, ["torpedo_tubes", "main_gun>=20mm"]))
    add("victor_alt", P_JP + "this is a postwar ocean-going submarine.", _req("Japan", 1960, "submarine", None, ["submarine_ops"], 2200, "normal", 20, ["torpedo_tubes", "sonar"]))
    add("victor_alt", P_JP + "this is a postwar fleet carrier with an air-defence missile.", _req("Japan", 1958, "aircraft_carrier", None, ["carrier_air", "aaw"], 30000, "standard", 32, ["aircraft", "air_search_radar", "sam"]))
    add("victor_alt", P_JP + "this is a postwar coastal minesweeper.", _req("Japan", 1950, "mine_warfare", "minesweeper", ["mine_warfare"], 450, "normal", 14, ["minesweeping", "main_gun>=40mm"], max_complement=60))
    add("victor_alt", P_DE + "this is a postwar battleship.", _req("Germany", 1950, "battleship", None, ["surface_strike", "fire_support"], 60000, "standard", 30, ["main_gun>=380mm", "aircraft", "air_search_radar"]))
    add("victor_alt", P_DE + "this is a postwar destroyer.", _req("Germany", 1952, "destroyer", None, ["surface_strike"], 3500, "standard", 36, ["main_gun>=127mm", "torpedo_tubes", "fire_control_radar"]))
    add("victor_alt", P_DE + "this is a postwar ocean-going submarine.", _req("Germany", 1950, "submarine", None, ["submarine_ops"], 1800, "normal", 17, ["torpedo_tubes", "sonar"]))
    add("victor_alt", P_DE + "this is a postwar escort carrier.", _req("Germany", 1949, "aircraft_carrier", None, ["carrier_air"], 20000, "standard", 30, ["aircraft", "radar"]))
    add("victor_alt", P_IT + "this is a postwar light cruiser.", _req("Italy", 1948, "light_cruiser", None, ["surface_strike"], 9500, "standard", 34, ["main_gun>=152mm", "aircraft", "radar"]))
    add("victor_alt", P_IT + "this is a postwar destroyer.", _req("Italy", 1955, "destroyer", None, ["surface_strike", "asw"], 3100, "standard", 34, ["main_gun>=127mm", "torpedo_tubes", "sonar"]))
    # ---- 架空国家（1960〜1980 年代）
    c = "Valdoria"
    add("fictional_nation", _fict(c, "this is an air-defence destroyer."), _req(c, 1972, "destroyer", None, ["aaw", "asw"], 4800, "full", 32, ["sam", "air_search_radar", "helicopter", "sonar"]))
    c = "Norvane"
    add("fictional_nation", _fict(c, "this is a general-purpose frigate."), _req(c, 1975, "frigate", None, ["escort", "asw"], 3200, "full", 28, ["sam", "ssm", "helicopter"]))
    add("fictional_nation", _fict(c, "this is a light aircraft carrier."), _req(c, 1982, "aircraft_carrier", None, ["carrier_air"], 14000, "full", 28, ["helicopter", "sam", "air_search_radar"]))
    c = "Kestria"
    add("fictional_nation", _fict(c, "this is a missile corvette."), _req(c, 1978, "corvette", None, ["surface_strike"], 900, "full", 30, ["ssm", "main_gun>=76mm", "radar"]))
    add("fictional_nation", _fict(c, "this is an anti-submarine frigate."), _req(c, 1973, "frigate", None, ["escort", "asw"], 1900, "full", 27, ["ssm", "sonar", "asw_weapon"]))
    c = "Aurelan"
    add("fictional_nation", _fict(c, "this is a missile boat."), _req(c, 1971, "fast_attack_craft", None, ["surface_strike"], 260, "full", 38, ["ssm", "main_gun>=40mm"]))
    add("fictional_nation", _fict(c, "this is a missile destroyer."), _req(c, 1968, "destroyer", None, ["aaw", "surface_strike"], 3600, "full", 33, ["sam", "torpedo_tubes", "air_search_radar"]))
    c = "Tarsonia"
    add("fictional_nation", _fict(c, "this is an attack submarine."), _req(c, 1974, "submarine", None, ["submarine_ops"], 2400, "normal", 20, ["torpedo_tubes", "sonar", "esm_ecm"]))
    c = "Meridia"
    add("fictional_nation", _fict(c, "this is an air-defence destroyer with a close-in weapon."), _req(c, 1979, "destroyer", None, ["aaw", "surface_strike"], 5800, "full", 31, ["sam", "ssm", "air_search_radar", "helicopter", "ciws"]))
    c = "Zephyria"
    add("fictional_nation", _fict(c, "this is an anti-submarine frigate."), _req(c, 1962, "frigate", None, ["escort", "asw"], 2200, "full", 28, ["asw_weapon", "sonar", "main_gun>=76mm"]))
    c = "Brannoch"
    add("fictional_nation", _fict(c, "this is a guided-missile cruiser."), _req(c, 1968, "cruiser", None, ["aaw", "surface_strike"], 9500, "full", 32, ["sam", "helicopter", "air_search_radar", "main_gun>=127mm"]))
    add("fictional_nation", _fict(c, "this is a patrol boat for the river and coast."), _req(c, 1965, "patrol", "patrol-boat", ["patrol"], 120, "full", 24, ["main_gun>=20mm", "radar"], max_complement=20))
    c = "Solmara"
    add("fictional_nation", _fict(c, "this is an offshore patrol ship."), _req(c, 1976, "patrol", "patrol-ship", ["patrol"], 1200, "full", 22, ["helicopter", "main_gun>=40mm"]))
    c = "Ysbelle"
    add("fictional_nation", _fict(c, "this is an amphibious assault ship."), _req(c, 1980, "amphibious", None, ["amphibious"], 7000, "full", 20, ["helicopter", "main_gun>=40mm", "sam"]))
    # ---- 技術が前倒しの世界（waive = 前提が許す年代外の能力）
    rd = ["radar_any", "air_search_radar", "fire_control_radar", "surface_radar", "radar"]
    add("tech_shift", P_RADAR + "a destroyer around 1930 already carries them.", _req("United-Kingdom", 1930, "destroyer", None, ["surface_strike", "escort"], 1400, "standard", 35, ["main_gun>=120mm", "torpedo_tubes", "air_search_radar", "fire_control_radar"]), rd)
    add("tech_shift", P_RADAR + "a light cruiser around 1935 already carries them.", _req("United-States", 1935, "light_cruiser", None, ["surface_strike", "aaw"], 9800, "standard", 33, ["main_gun>=152mm", "aircraft", "air_search_radar", "fire_control_radar"]), rd)
    add("tech_shift", P_RADAR + "a heavy cruiser around 1936 already carries them.", _req("Germany", 1936, "heavy_cruiser", None, ["surface_strike"], 13000, "standard", 32, ["main_gun>=203mm", "air_search_radar", "aircraft"]), rd)
    add("tech_shift", P_RADAR + "a destroyer around 1933 already carries them.", _req("Japan", 1933, "destroyer", None, ["surface_strike"], 1800, "standard", 36, ["main_gun>=127mm", "torpedo_tubes", "fire_control_radar"]), rd)
    add("tech_shift", P_RADAR + "a sloop around 1932 already carries them.", _req("France", 1932, "escort", None, ["escort", "aaw"], 1100, "standard", 22, ["main_gun>=100mm", "air_search_radar"]), rd)
    add("tech_shift", "Assume anti-ship missiles matured twenty years earlier than in reality; a destroyer around 1938 carries them.",
        _req("United-Kingdom", 1938, "destroyer", None, ["surface_strike"], 1700, "standard", 34, ["ssm", "main_gun>=102mm", "radar"]), ["ssm", "radar_any", "radar"])
    add("tech_shift", "Assume anti-ship missiles matured twenty years earlier than in reality; a fast attack craft around 1936 carries them.",
        _req("Russian", 1936, "fast_attack_craft", None, ["surface_strike"], 120, "full", 42, ["ssm", "main_gun>=20mm"]), ["ssm"])
    add("tech_shift", "Assume surface-to-air missiles matured twenty years earlier than in reality; a cruiser around 1939 carries them.",
        _req("Italy", 1939, "cruiser", None, ["aaw"], 8000, "standard", 33, ["sam", "main_gun>=152mm", "radar"]), ["sam", "radar_any", "radar"])
    add("tech_shift", "Assume practical helicopters entered naval service ten years earlier than in reality; a destroyer around 1944 operates one.",
        _req("United-States", 1944, "destroyer", None, ["asw", "escort"], 2300, "standard", 33, ["helicopter", "main_gun>=127mm", "sonar"]), ["air:helicopter", "aircraft"])
    add("tech_shift", "Assume nuclear propulsion matured fifteen years earlier than in reality; a submarine around 1945 uses it.",
        _req("Germany", 1945, "submarine", None, ["submarine_ops"], 2500, "normal", 25, ["torpedo_tubes", "sonar"], required_propulsion=["nuclear"]), ["prop:nuclear"])
    add("tech_shift", "Assume marine gas turbines matured fifteen years earlier than in reality; a destroyer around 1950 uses them.",
        _req("United-Kingdom", 1950, "destroyer", None, ["surface_strike", "escort"], 2800, "standard", 34, ["main_gun>=113mm", "radar"], required_propulsion=["gas_turbine"]), ["prop:gas_turbine"])
    add("tech_shift", "Assume sonar and depth-charge throwers matured ten years earlier than in reality; a patrol vessel around 1908 carries them.",
        _req("Japan", 1908, "patrol", "submarine-chaser", ["patrol", "asw"], 300, "normal", 18, ["sonar", "asw_weapon", "main_gun>=40mm"]), ["sonar", "asw_weapon", "depth_charge"])
    # ---- 実在艦級に直接対応しない制約セット
    h = "Hypothetical requirement set (no real class matches it): "
    add("odd_constraints", h + "a small missile patrol boat that also operates a helicopter.", _req("United-States", 1960, "patrol", None, ["patrol", "surface_strike"], 300, "full", 30, ["ssm", "helicopter"]))
    add("odd_constraints", h + "a submarine with a medium gun and a surface-to-surface missile.", _req("Sweden", 1985, "submarine", None, ["submarine_ops", "surface_strike"], 1500, "normal", 22, ["torpedo_tubes", "ssm", "main_gun>=76mm"]))
    add("odd_constraints", h + "a corvette carrying every air-defence layer plus a helicopter.", _req("Netherlands", 1990, "corvette", None, ["aaw", "surface_strike"], 800, "full", 30, ["sam", "ssm", "ciws", "helicopter"]))
    add("odd_constraints", h + "a fast destroyer on a very small hull.", _req("Japan", 1965, "destroyer", None, ["aaw", "asw"], 1800, "normal", 36, ["sam", "ssm", "helicopter"]))
    add("odd_constraints", h + "a cruiser-sized carrier with a cruiser's main battery.", _req("United-Kingdom", 1925, "aircraft_carrier", None, ["carrier_air", "surface_strike"], 9000, "standard", 30, ["aircraft", "main_gun>=152mm"]))
    add("odd_constraints", h + "a fast minelayer with missiles.", _req("Russian", 1978, "mine_warfare", None, ["mine_warfare", "aaw"], 1200, "full", 28, ["sam", "ssm", "mines"]))
    add("odd_constraints", h + "a very fast missile craft with a helicopter pad.", _req("Italy", 1970, "patrol", None, ["patrol", "surface_strike"], 150, "full", 45, ["ssm", "helicopter"]))
    add("odd_constraints", h + "an amphibious ship that is also a fast missile ship.", _req("China", 1995, "amphibious", None, ["amphibious", "aaw"], 1800, "full", 35, ["sam", "ssm"]))
    add("odd_constraints", h + "a submarine that carries a seaplane and a heavy gun.", _req("Spain", 1932, "submarine", None, ["submarine_ops"], 1100, "normal", 18, ["main_gun>=127mm", "aircraft", "torpedo_tubes"]))
    add("odd_constraints", h + "a very fast destroyer that carries a seaplane.", _req("Germany", 1916, "destroyer", None, ["surface_strike"], 800, "normal", 40, ["main_gun>=127mm", "torpedo_tubes", "aircraft"]))
    add("odd_constraints", h + "a destroyer escort with a very long range for its size.", _req("Australia", 1941, "escort", None, ["escort", "asw"], 1200, "standard", 20, ["asw_weapon", "sonar", "main_gun>=102mm"], min_endurance_nm=9000))
    add("odd_constraints", h + "a light cruiser with a gas-turbine plant.", _req("France", 1957, "light_cruiser", None, ["aaw", "surface_strike"], 9000, "standard", 32, ["main_gun>=152mm", "radar"], required_propulsion=["gas_turbine"]))
    return out


def _e(i: int, req: dict[str, Any], category: str, reason: str, extra: list[str] | None = None, extra_ja: list[str] | None = None, original: list[str] | None = None,
       premise: str = "") -> dict[str, Any]:
    return {"id": f"e{i:02d}", "category": "E", "premise": premise, "req": req, "extra_phrases": extra or [], "extra_phrases_ja": extra_ja or [],
            "meta": {"why_impossible": {"category": category, "reason": reason}, "original_terms": original or []}}


def cases_E() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = 0

    def add(req: dict[str, Any], cat: str, reason: str, extra: list[str] | None = None, extra_ja: list[str] | None = None, original: list[str] | None = None) -> None:
        nonlocal i
        i += 1
        out.append(_e(i, req, cat, reason, extra, extra_ja, original))

    # ---- 年代（era）: その年に存在しない技術
    add(_req("United-States", 1895, "destroyer", None, ["aaw", "surface_strike"], 9000, "full", 30, ["sam", "air_search_radar", "ssm", "ciws"]), "era",
        "In 1895 there were no radar, guided missiles or CIWS (and no destroyers as such); the Aegis combat system needs digital computers and phased-array radar (1970s).",
        ["an Aegis combat system"], ["イージス戦闘システム"], ["Aegis"])
    add(_req("United-Kingdom", 1938, "destroyer", None, ["aaw"], 3000, "full", 32, ["sam", "air_search_radar"]), "era",
        "A vertical launching system and an AESA radar are 1980s-2000s technology; in the 1930s there were neither guided SAMs nor electronically scanned radars.",
        ["a vertical launching system", "an AESA radar"], ["垂直発射システム", "AESAレーダー"], ["VLS", "AESA"])
    add(_req("Japan", 1936, "heavy_cruiser", None, ["aaw", "surface_strike"], 12000, "standard", 33, ["sam", "ssm", "air_search_radar"]), "era",
        "1930s ships had neither SAMs nor SSMs; AESA radar and vertical launchers are decades later.", ["an AESA radar", "a vertical launching system"], ["AESAレーダー", "垂直発射システム"], ["AESA", "VLS"])
    add(_req("Germany", 1932, "light_cruiser", None, ["aaw"], 7000, "standard", 32, ["sam", "air_search_radar", "ciws"], max_complement=300), "era",
        "No guided missiles, search radar or CIWS existed in 1932.", ["an AESA radar"], ["AESAレーダー"], ["AESA"])
    add(_req("Italy", 1912, "battleship", None, ["surface_strike"], 25000, "normal", 24, ["ssm", "helicopter"], required_propulsion=["nuclear"]), "era",
        "In 1912 there were no anti-ship missiles, helicopters or nuclear reactors (first nuclear ship 1954).")
    add(_req("France", 1925, "destroyer", None, ["aaw", "asw"], 2500, "standard", 34, ["helicopter", "air_search_radar", "sonar"], min_aircraft=2), "era",
        "Helicopters (1940s-50s) and air-search radar (late 1930s) did not exist in 1925.")
    add(_req("United-States", 1950, "frigate", None, ["aaw"], 4000, "full", 28, ["sam", "ciws", "air_search_radar"]), "era",
        "CIWS (1970s) and AESA radar did not exist in 1950, and shipborne SAMs were only just emerging.", ["an AESA radar"], ["AESAレーダー"], ["AESA"])
    add(_req("Russian", 1915, "cruiser", None, ["surface_strike"], 8000, "normal", 29, ["ssm", "ciws", "main_gun>=130mm"]), "era",
        "Anti-ship missiles and CIWS did not exist in 1915.")
    add(_req("Japan", 1890, "torpedo_boat", None, ["surface_strike"], 150, "normal", 22, ["sonar", "air_search_radar", "helicopter"]), "era",
        "In 1890 there were no sonar, air-search radar or helicopters.")
    add(_req("United-Kingdom", 1860, "ironclad", None, ["surface_strike"], 9000, "normal", 14, ["main_gun>=230mm"], required_propulsion=["gas_turbine"]), "era",
        "Marine gas turbines first appeared in the 1940s-50s.")
    add(_req("Germany", 1905, "battleship", None, ["surface_strike"], 20000, "normal", 20, ["main_gun>=280mm"], required_propulsion=["nuclear"]), "era",
        "Nuclear reactors did not exist before the 1940s; the first nuclear-powered ship ran in 1954.")
    add(_req("Spain", 1930, "submarine", None, ["submarine_ops"], 1200, "normal", 18, ["torpedo_tubes", "ballistic_missile"], min_torpedo_tubes=6), "era",
        "Submarine-launched ballistic missiles appeared in the late 1950s.", ["16 submarine-launched ballistic missiles"], ["潜水艦発射弾道ミサイル16基"], ["SLBM"])
    add(_req("United-States", 1943, "destroyer", None, ["aaw"], 3000, "full", 33, ["sam", "air_search_radar", "ciws"]), "era",
        "A phased-array radar and vertical launch cells are 1970s-80s technology; no SAM or CIWS existed in 1943.", ["a phased-array radar", "a vertical launching system"], ["フェーズドアレイレーダー", "垂直発射システム"], ["phased array", "VLS"])
    add(_req("France", 1948, "frigate", None, ["aaw", "surface_strike"], 3500, "full", 30, ["sam", "ssm", "ciws"]), "era",
        "In 1948 there were no shipborne guided missiles in service and no CIWS (1970s).")
    add(_req("China", 1960, "corvette", None, ["aaw"], 900, "full", 28, ["sam", "ciws", "air_search_radar"]), "era",
        "A CIWS and an AESA radar did not exist in 1960.", ["an AESA radar"], ["AESAレーダー"], ["AESA"])
    # ---- 物理（physics）: 寸法・出力・搭載量が両立しない
    add(_req("Japan", 1940, "coastal_defense_monitor", None, ["coastal_defense", "fire_support"], 300, "full", 10, ["main_gun>=460mm"], min_gun_barrels=9, min_gun_barrels_cal_mm=460), "physics",
        "Nine 46 cm guns weigh thousands of tonnes (the Yamato's three triple turrets weigh about 2,500 t each); a 300 t hull cannot carry or survive them.")
    add(_req("United-Kingdom", 1944, "patrol", None, ["patrol"], 50, "normal", 40, ["helicopter"], max_complement=5, min_aircraft=2), "physics",
        "A 50 t hull cannot carry two helicopters (decks and hangars need 1,000+ t), and 40 kn on 50 t with a crew of 5 is not possible.")
    add(_req("Russian", 1975, "submarine", None, ["submarine_ops"], 100, "normal", 12, ["torpedo_tubes", "helicopter"], min_torpedo_tubes=8, min_aircraft=4), "physics",
        "A 100 t submarine cannot carry four helicopters or eight torpedo tubes (a pressure hull needs far more volume).")
    add(_req("Germany", 1938, "destroyer", None, ["surface_strike"], 500, "normal", 36, ["main_gun>=203mm"], min_gun_barrels=8, min_gun_barrels_cal_mm=203, min_endurance_nm=8000), "physics",
        "Eight 203 mm guns on 500 t at 36 kn with 8,000 nm: weight, stability and fuel volume cannot fit.")
    add(_req("France", 1992, "corvette", None, ["aaw", "surface_strike"], 400, "full", 35, ["sam", "ssm", "ciws", "helicopter"], min_aircraft=2, min_endurance_nm=6000), "physics",
        "400 t cannot hold a SAM system, SSMs, a CIWS and two helicopters; 35 kn and 6,000 nm on such a hull is also beyond the fuel and power volume.")
    add(_req("United-States", 1960, "aircraft_carrier", None, ["carrier_air"], 2000, "full", 30, ["aircraft", "air_search_radar"], min_aircraft=40), "physics",
        "A carrier with 40 aircraft needs a flight deck and hangar of tens of thousands of tonnes; 2,000 t is a sloop.")
    add(_req("Japan", 1975, "fast_attack_craft", None, ["surface_strike"], 80, "full", 50, ["torpedo_tubes", "ssm", "main_gun>=100mm"], min_torpedo_tubes=12, min_gun_barrels=2), "physics",
        "12 torpedo tubes, SSMs and 100 mm guns at 50 kn on 80 t exceed the payload and the power the hull can take.")
    add(_req("Italy", 1938, "battleship", None, ["surface_strike"], 3000, "standard", 30, ["main_gun>=406mm"], min_gun_barrels=9, min_gun_barrels_cal_mm=406), "physics",
        "Nine 406 mm guns need a 30,000+ t hull.")
    add(_req("United-Kingdom", 1965, "frigate", None, ["escort"], 1000, "full", 45, ["sam"], required_propulsion=["nuclear"]), "physics",
        "A nuclear plant of that era weighed thousands of tonnes; 45 kn on a 1,000 t monohull needs several times the power that fits.")
    add(_req("Sweden", 1980, "submarine", None, ["submarine_ops"], 200, "normal", 30, ["torpedo_tubes"], min_endurance_nm=20000, min_torpedo_tubes=6), "physics",
        "Conventional 200 t submarines make 5-10 kn submerged; 30 kn with 20,000 nm needs a nuclear plant that cannot fit in 200 t.")
    add(_req("Netherlands", 1970, "patrol", None, ["patrol"], 120, "full", 25, ["sam"], max_complement=3, min_endurance_nm=15000), "physics",
        "15,000 nm at 25 kn on 120 t, with a SAM system and a crew of 3, is beyond the fuel volume and the manning needed to run the weapons.")
    add(_req("Russian", 1982, "aircraft_carrier", None, ["carrier_air"], 1000, "full", 28, ["aircraft", "helicopter"], min_aircraft=20), "physics",
        "Twenty aircraft need a hangar and deck far larger than a 1,000 t hull.")
    add(_req("Germany", 1938, "battleship", None, ["surface_strike"], 8000, "standard", 35, ["main_gun>=400mm"], min_gun_barrels=12, min_gun_barrels_cal_mm=400), "physics",
        "Twelve 400 mm guns on 8,000 t at 35 kn: the battery alone outweighs the hull.")
    add(_req("Japan", 1940, "mine_warfare", None, ["mine_warfare"], 150, "normal", 14, ["main_gun>=203mm", "aircraft"], min_gun_barrels=2, min_gun_barrels_cal_mm=203, min_aircraft=10), "physics",
        "Two 203 mm guns and ten aircraft on a 150 t minesweeper cannot be fitted.")
    add(_req("United-States", 1970, "amphibious", None, ["amphibious"], 500, "full", 35, ["helicopter"], min_aircraft=10, min_endurance_nm=8000), "physics",
        "Ten helicopters need a 15,000+ t amphibious ship; 35 kn on 500 t with 8,000 nm is also not possible.")
    add(_req("Sweden", 1900, "submarine", None, ["submarine_ops"], 200, "normal", 12, ["main_gun>=305mm"], min_gun_barrels=4, min_gun_barrels_cal_mm=305), "physics",
        "Four 305 mm guns on a 200 t submarine in 1900: impossible (weight, recoil, pressure hull).")
    add(_req("United-Kingdom", 1999, "corvette", None, ["surface_strike"], 300, "full", 30, ["ssm"], required_propulsion=["nuclear"]), "physics",
        "No shipborne nuclear plant fits a 300 t hull (the smallest reactors sit in 2,000+ t submarines).")
    add(_req("China", 1978, "fast_attack_craft", None, ["surface_strike"], 40, "full", 60, ["ssm", "main_gun>=76mm"], min_gun_barrels=3, min_gun_barrels_cal_mm=76), "physics",
        "60 kn on 40 t with three 76 mm guns and SSMs: a planing hull of that size cannot carry the payload and keep the speed in a seaway.")
    add(_req("Japan", 1930, "gunboat_river", None, ["patrol"], 20, "normal", 20, ["main_gun>=127mm"], min_gun_barrels=2, min_gun_barrels_cal_mm=127), "physics",
        "Two 127 mm guns on a 20 t boat exceed its stability and structure.")
    # ---- 矛盾（contradiction）: 要求同士が両立しない・艦種と要求が矛盾
    add(_req("United-States", 1985, "destroyer", None, ["aaw"], 2000, "normal", 25, ["sam", "ssm", "helicopter"], max_complement=10, min_aircraft=6), "contradiction",
        "A crew of 10 cannot run a destroyer's sensors, weapons and six helicopters.")
    add(_req("France", 1910, "torpedo_boat", None, ["surface_strike"], 100, "normal", 70, ["torpedo_tubes"]), "contradiction",
        "No steam-powered 100 t hull of 1910 reached 70 kn (the fastest torpedo boats made about 30 kn).")
    add(_req("Germany", 1880, "gunboat_river", None, ["fire_support"], 400, "normal", 12, ["sam", "radar"]), "contradiction",
        "No SAM or radar existed in 1880.")
    add(_req("Russian", 1990, "submarine", None, ["submarine_ops"], 3000, "normal", 20, ["torpedo_tubes", "helicopter", "main_gun>=76mm"], min_aircraft=2), "contradiction",
        "A submarine cannot carry helicopters or a deck gun in 1990 (submarines did not carry helicopters).")
    add(_req("Japan", 1952, "frigate", None, ["escort"], 1500, "full", 20, ["helicopter", "ciws", "ssm"], required_propulsion=["gas_turbine"]), "contradiction",
        "1952 has neither CIWS nor SSMs on frigates; helicopters and gas turbines were only just appearing.")
    add(_req("United-Kingdom", 2005, "patrol", None, ["patrol"], 60, "full", 40, ["helicopter", "sam", "ssm"], max_complement=4, min_aircraft=2), "contradiction",
        "A 60 t patrol boat with 4 crew cannot embark two helicopters, SAMs and SSMs.")
    add(_req("Italy", 2000, "aircraft_carrier", None, ["carrier_air"], 3000, "full", 30, ["aircraft"], min_aircraft=30, required_propulsion=["nuclear"]), "contradiction",
        "A nuclear-powered carrier carrying 30 aircraft cannot be built on 3,000 t.")
    add(_req("United-States", 1930, "heavy_cruiser", None, ["surface_strike"], 10000, "standard", 32, ["helicopter", "sam", "air_search_radar"], min_aircraft=4), "contradiction",
        "No helicopters, SAMs or air-search radar in 1930.")
    add(_req("Netherlands", 1935, "frigate", None, ["escort"], 1200, "full", 22, ["ssm", "sam", "helicopter"]), "contradiction",
        "No missiles or helicopters in 1935.")
    add(_req("Japan", 1920, "destroyer", None, ["asw"], 1300, "normal", 34, ["sonar", "helicopter", "esm_ecm"]), "contradiction",
        "No helicopters or ESM/ECM equipment in 1920 (active sonar entered service in 1920s but helicopters were decades later).")
    add(_req("Brazil", 1970, "submarine", None, ["submarine_ops"], 800, "normal", 18, ["torpedo_tubes", "sam", "helicopter", "main_gun>=100mm"], min_aircraft=1), "contradiction",
        "A submarine with a helicopter, a SAM system and a 100 mm gun in 1970: no such design existed and the hull cannot carry them.")
    add(_req("Germany", 1944, "aircraft_carrier", None, ["carrier_air"], 800, "full", 25, ["aircraft"], min_aircraft=24), "physics",
        "24 aircraft in 800 t: even the smallest escort carriers displaced 7,000+ t.")
    add(_req("France", 1925, "submarine", None, ["submarine_ops"], 500, "normal", 25, ["torpedo_tubes", "main_gun>=140mm"], min_gun_barrels=2, min_gun_barrels_cal_mm=140), "physics",
        "25 kn on the surface with two 140 mm guns and torpedo tubes on a 500 t submarine of 1925: the diesels of that era gave about 15-17 kn on that size.")
    add(_req("Japan", 1955, "escort", None, ["escort", "asw"], 300, "normal", 28, ["asw_weapon", "sonar", "main_gun>=127mm"], min_gun_barrels=2, min_gun_barrels_cal_mm=127), "physics",
        "Two 127 mm guns plus ASW weapons and sonar at 28 kn on 300 t: the weight and power do not fit.")
    add(_req("Sweden", 1990, "coast_guard", None, ["patrol"], 100, "full", 30, ["helicopter", "sam"], min_aircraft=1), "physics",
        "A 100 t cutter cannot operate a helicopter (about 1,000+ t needed), let alone with a SAM system.")
    add(_req("Spain", 1985, "mine_warfare", None, ["mine_warfare"], 200, "full", 20, ["minesweeping", "ssm", "sam", "helicopter"], min_aircraft=1), "physics",
        "A 200 t minesweeper cannot carry minesweeping gear plus SSMs, a SAM system and a helicopter.")
    return out
