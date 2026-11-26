"""shipknow 設計タスク D の共通部品（データ生成・validator・採点器が共有する純関数）。

* 実艦レコード -> 設計 JSON（`design_from_record`）
* 設計 JSON -> 能力語彙（`caps_of_design`: 要求の必須装備・年代検査・艦種検査が使う）
* 設計 JSON -> 物理的な比（`ratios_of`: L/B、ブロック係数相当、Admiralty 係数相当 ...）
* 任務タグ（`mission_tags`）、要求の生成（`make_requirements`）と要求文（英語 / 日本語）
* 近傍の短い表示（`nbr_payload`）と、その逆変換（`payload_to_design`: 非 LLM ベースライン用）

設計 JSON のキー（出力は説明文なし、JSON のみ。値の丸めは v0 の `rnd` と同じ）:
  ship_type, ship_type_fine, displacement_normal_t, displacement_full_t, displacement_standard_t, length_m, beam_m, draught_m,
  propulsion[], shafts, power_hp, speed_kn, endurance_nm, complement, armour{belt,deck,turret}, weapons[{kind,n,per,cal}],
  sensors[], air[]  （air = 搭載機の種類 helicopter / seaplane / aircraft。weapons の aircraft_helicopter だけでは回転翼かどうか分からないため）
値が無い項目は出さない。
"""

from __future__ import annotations

import math
import random
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shipknow_build_tasks as B  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
V0_DIR = ROOT / "data" / "navspec" / "shipknow_v0_2"
OUT_DIR = ROOT / "data" / "navspec" / "shipknow_design_v0"

# 帆走艦・蒸気過渡期の艦は設計タスクに含めない（「設計して」と頼まれる対象ではなく、年代検査の語彙も無い）
EXCLUDED_TYPES = {"sailing_ship", "ship_of_the_line", "early_warship", "unknown"}
MIN_YEAR = 1860
MAX_GAP_NAVY = 5  # year_in_navy - year がこれを超える艦（他国からの譲渡・取得）は、要求の国と年が元艦の設計と一致しないので除く
DESIGN_NUM_KEYS = ["displacement_normal_t", "displacement_full_t", "displacement_standard_t", "length_m", "beam_m", "draught_m"]
DESIGN_KEY_ORDER = ["ship_type", "ship_type_fine", "displacement_normal_t", "displacement_full_t", "displacement_standard_t", "length_m", "beam_m",
                    "draught_m", "propulsion", "shafts", "power_hp", "speed_kn", "endurance_nm", "complement", "armour", "weapons", "sensors", "air"]
RADAR_SENSORS = ["radar", "air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar"]
ERA_BINS = [(1860, 1899), (1900, 1919), (1920, 1944), (1945, 1969), (1970, 2100)]
ASW_KINDS = ("depth_charge", "asw_rocket")


def era_bin(year: int | float | None) -> str | None:
    if year is None:
        return None
    for lo, hi in ERA_BINS:
        if lo <= year <= hi:
            return f"{lo}-{hi}"
    return f"{ERA_BINS[0][0]}-{ERA_BINS[0][1]}" if year < ERA_BINS[0][0] else None


# --------------------------------------------------------------------------------------------------
# record -> design
# --------------------------------------------------------------------------------------------------
def air_types(rec: dict[str, Any]) -> list[str]:
    out: set[str] = set()
    for w in rec.get("weapons") or []:
        if w.get("kind") not in ("aircraft_helicopter", "aviation_facility"):
            continue
        nm = (w.get("name") or "").lower()
        if re.search(r"helicopter|helo|drone|\bdash\b|vtol", nm):
            out.add("helicopter")
        elif re.search(r"seaplane|floatplane|catapult|flying[- ]boat", nm):
            out.add("seaplane")
        else:
            out.add("aircraft")
    return sorted(out)


def design_eligible(rec: dict[str, Any]) -> bool:
    if rec.get("ship_type") in EXCLUDED_TYPES or not rec.get("ship_type_fine"):
        return False
    y = rec.get("year")
    if not y or y < MIN_YEAR or not rec.get("country"):
        return False
    yn = rec.get("year_in_navy")
    if yn and yn - y > MAX_GAP_NAVY:
        return False
    if not rec.get("armament_parse_ok") or not rec.get("weapons"):
        return False
    cw = B.compact_weapons(rec["weapons"])
    if not cw or len(cw) > B.MAX_WEAPON_ITEMS:
        return False
    if not rec.get("speed_kn") or not rec.get("length_m"):
        return False
    if not (rec.get("displacement_normal_t") or rec.get("displacement_full_t") or rec.get("displacement_standard_t")):
        return False
    return True


def request_year(rec: dict[str, Any]) -> int:
    """要求の設計年。decade 精度（``1970s``）の艦は十年の中央に置く。"""
    y = int(rec["year"])
    return y + 5 if str(rec.get("year_source") or "").endswith("decade") else y


def design_from_record(rec: dict[str, Any]) -> dict[str, Any]:
    d: dict[str, Any] = {"ship_type": rec["ship_type"], "ship_type_fine": rec["ship_type_fine"]}
    for f in DESIGN_NUM_KEYS:
        if rec.get(f) is not None:
            d[f] = B.rnd(f, rec[f])
    if rec.get("propulsion"):
        d["propulsion"] = list(rec["propulsion"])
    for f in ("shafts", "power_hp", "speed_kn", "endurance_nm", "complement"):
        if rec.get(f) is not None:
            d[f] = B.rnd(f, rec[f])
    arm = {k: int(round(rec[f])) for k, f in (("belt", "armour_belt_mm"), ("deck", "armour_deck_mm"), ("turret", "armour_turret_mm")) if rec.get(f) is not None}
    if arm:
        d["armour"] = arm
    if d.get("displacement_standard_t") is not None and d.get("displacement_standard_t") == d.get("displacement_normal_t"):
        del d["displacement_standard_t"]  # a standard displacement equal to the normal one adds nothing (the scorer / validator treat a missing one as equal)
    d["weapons"] = B.compact_weapons(rec["weapons"])
    if rec.get("sensors_known") and rec.get("sensor_kinds") is not None:
        d["sensors"] = sorted(rec["sensor_kinds"])
    air = air_types(rec)
    if air:
        d["air"] = air
    return {k: d[k] for k in DESIGN_KEY_ORDER if k in d}


def jdump(o: Any) -> str:
    import json
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------------------------------------
# capabilities
# --------------------------------------------------------------------------------------------------
def weapon_kinds(design: dict[str, Any]) -> set[str]:
    return {w.get("kind") for w in (design.get("weapons") or []) if isinstance(w, dict) and w.get("kind")}


def main_gun_mm(design: dict[str, Any]) -> float | None:
    cals = [w["cal"] for w in (design.get("weapons") or []) if isinstance(w, dict) and w.get("kind") == "gun" and isinstance(w.get("cal"), (int, float))]
    return max(cals) if cals else None


def caps_of_design(design: dict[str, Any]) -> set[str]:
    """設計が持つ能力語彙（年代検査・艦種検査・必須装備の判定に使う）。"""
    k = weapon_kinds(design)
    caps: set[str] = set()
    for kind in ("gun", "torpedo_tube", "ssm", "sam", "ciws", "rocket_launcher", "ballistic_missile", "mine", "minesweeping_gear", "mg", "depth_charge", "asw_rocket"):
        if kind in k:
            caps.add(kind)
    if k & set(ASW_KINDS):
        caps.add("asw_weapon")
    air = set(design.get("air") or [])
    if "aircraft_helicopter" in k or "aviation_facility" in k or air:
        caps.add("aircraft")
    if "helicopter" in air:
        caps.add("air:helicopter")
    if "seaplane" in air:
        caps.add("air:seaplane")
    sens = set(design.get("sensors") or [])
    for s in sens:
        caps.add(s)
    if sens & set(RADAR_SENSORS):
        caps.add("radar_any")
    for p in design.get("propulsion") or []:
        caps.add(f"prop:{p}")
    return caps


# 年代検査の対象（初出年を train から求める能力語彙）
ERA_KEYS = ["torpedo_tube", "ssm", "sam", "ciws", "asw_weapon", "depth_charge", "asw_rocket", "rocket_launcher", "ballistic_missile", "aircraft",
            "air:helicopter", "air:seaplane", "radar_any", "radar", "air_search_radar", "surface_radar", "fire_control_radar", "navigation_radar", "sonar",
            "esm_ecm", "eo_director", "combat_system", "decoy", "prop:gas_turbine", "prop:nuclear", "prop:diesel", "prop:steam_turbine", "prop:electric_motor"]


def era_keys(design: dict[str, Any]) -> set[str]:
    return caps_of_design(design) & set(ERA_KEYS)


# 履歴の改装イベント（shipknow_history の kind）-> 年代検査キー（誤検出しにくい語彙だけ）
EVENT_KIND_TO_ERA_KEY = {"ssm": "ssm", "sam": "sam", "ciws": "ciws", "helicopter": "air:helicopter", "radar": "radar_any", "sonar": "sonar",
                         "esm_ecm": "esm_ecm", "asw_weapon": "asw_weapon", "ballistic_missile": "ballistic_missile"}

# --------------------------------------------------------------------------------------------------
# ratios (physical plausibility)
# --------------------------------------------------------------------------------------------------
def ref_displacement(design: dict[str, Any]) -> tuple[float | None, str | None]:
    for b in ("normal", "standard", "full"):
        v = design.get(f"displacement_{b}_t")
        if isinstance(v, (int, float)) and v > 0:
            return float(v), b
    return None, None


def _pos(d: dict[str, Any], k: str) -> float | None:
    v = d.get(k)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 and math.isfinite(v) else None


def ratios_of(design: dict[str, Any]) -> dict[str, float]:
    """比の辞書（求まるものだけ）。名前: lb=L/B, bt=B/T, cb_*=Δ/(L·B·T)（基準別）, adm=Δ^(2/3)·V^3/P（Admiralty 係数相当）,
    fr=V/√L, hp_t=P/Δ, crew_t=乗員/Δ, slender=L/Δ^(1/3), full_normal=満載/常備。"""
    r: dict[str, float] = {}
    L, B_, T = _pos(design, "length_m"), _pos(design, "beam_m"), _pos(design, "draught_m")
    V, P, C = _pos(design, "speed_kn"), _pos(design, "power_hp"), _pos(design, "complement")
    D, _basis = ref_displacement(design)
    if L and B_:
        r["lb"] = L / B_
    if B_ and T:
        r["bt"] = B_ / T
    for b in ("normal", "full", "standard"):
        Db = _pos(design, f"displacement_{b}_t")
        if Db and L and B_ and T:
            r[f"cb_{b}"] = Db / (L * B_ * T)
    if D and V and P:
        r["adm"] = D ** (2 / 3) * V ** 3 / P
    if V and L:
        r["fr"] = V / math.sqrt(L)
    if P and D:
        r["hp_t"] = P / D
    if C and D:
        r["crew_t"] = C / D
    if L and D:
        r["slender"] = L / D ** (1 / 3)
    dn, df = _pos(design, "displacement_normal_t"), _pos(design, "displacement_full_t")
    if dn and df:
        r["full_normal"] = df / dn
    return r


RATIO_KEYS = ["lb", "bt", "cb_normal", "cb_full", "cb_standard", "adm", "fr", "hp_t", "crew_t", "slender", "full_normal"]

# --------------------------------------------------------------------------------------------------
# mission tags (rule based)
# --------------------------------------------------------------------------------------------------
_COMBATANT = {"destroyer", "frigate", "corvette", "escort", "light_cruiser", "heavy_cruiser", "cruiser", "battleship", "battlecruiser",
              "armored_protected_cruiser", "ironclad", "coastal_defense_monitor"}
_ESCORT_TYPES = {"frigate", "corvette", "escort"}
MISSION_TAGS = ["patrol", "asw", "aaw", "surface_strike", "escort", "mine_warfare", "amphibious", "fire_support", "coastal_defense", "submarine_ops",
                "carrier_air", "support", "training", "general"]


def mission_tags(design: dict[str, Any]) -> list[str]:
    """任務タグ（最大 2 個）。規則: 艦種 coarse が決める型（amphibious / mine_warfare / submarine_ops / carrier_air / support / training /
    coastal_defense / patrol / surface_strike〈魚雷艇・高速攻撃艇〉）。水上戦闘艦（駆逐艦〜戦艦・護衛艦）は装備から点数をつけ、
    asw = 2·対潜兵装 + 1·ソナー + 1·ヘリ、aaw = 2·SAM + 1.5·CIWS + 1·対空捜索レーダー（+2 if fine に air-defence/aa）、
    surface_strike = 2·SSM + 1.5·魚雷 + 1·(口径≥127mm 砲) + 2·弾道、fire_support = 2·(口径≥152mm 砲 かつ 大型艦)＋1·ロケット。
    点数 ≥2 の上位 2 個（護衛系の艦種は escort を先頭に付ける）。無ければ surface_strike（大型艦）/ general。"""
    t = design.get("ship_type", "")
    f = design.get("ship_type_fine", "") or ""
    caps = caps_of_design(design)
    gun = main_gun_mm(design) or 0
    if t == "amphibious":
        return ["amphibious"]
    if t == "mine_warfare":
        return ["mine_warfare"]
    if t == "submarine":
        return ["submarine_ops"]
    if t == "aircraft_carrier":
        return ["carrier_air"]
    if t == "auxiliary":
        return ["support"]
    if t == "training":
        return ["training"]
    if t in ("patrol", "coast_guard", "gunboat_river"):
        tags = ["patrol"]
        if ("asw_weapon" in caps and "sonar" in caps) or re.search(r"submarine-chaser|asw", f):
            tags.append("asw")
        elif t == "gunboat_river" and gun >= 75:
            tags.append("fire_support")
        return tags
    if t in ("fast_attack_craft", "torpedo_boat"):
        return ["surface_strike"]
    if t == "coastal_defense_monitor":
        return ["coastal_defense"] + (["fire_support"] if gun >= 200 else [])
    score = {
        "asw": 2 * ("asw_weapon" in caps) + 1 * ("sonar" in caps) + 1 * ("air:helicopter" in caps),
        "aaw": 2 * ("sam" in caps) + 1.5 * ("ciws" in caps) + 1 * ("air_search_radar" in caps) + 2 * bool(re.search(r"air-defen|anti-air|\baa\b", f)),
        "surface_strike": 2 * ("ssm" in caps) + 1.5 * ("torpedo_tube" in caps) + 1 * (gun >= 127) + 2 * ("ballistic_missile" in caps),
        "fire_support": 2 * (gun >= 152 and t in ("battleship", "battlecruiser", "heavy_cruiser", "light_cruiser", "cruiser", "armored_protected_cruiser", "ironclad"))
        + 1 * ("rocket_launcher" in caps),
    }
    top = sorted((k for k, v in score.items() if v >= 2), key=lambda k: (-score[k], k))[:2]
    tags = (["escort"] if t in _ESCORT_TYPES else []) + top
    if not tags:
        tags = ["surface_strike"] if t in ("battleship", "battlecruiser", "heavy_cruiser", "light_cruiser", "cruiser", "armored_protected_cruiser", "destroyer", "ironclad") else ["general"]
    return tags[:2]


# --------------------------------------------------------------------------------------------------
# requirements
# --------------------------------------------------------------------------------------------------
GUN_GRID = [20, 25, 30, 37, 40, 45, 57, 75, 76, 88, 100, 105, 120, 127, 152, 190, 203, 230, 254, 280, 305, 320, 340, 356, 381, 406]
REQ_WEIGHTS = {"main_gun": 1.0, "torpedo_tubes": 1.5, "asw_weapon": 1.5, "sonar": 1.5, "helicopter": 2.5, "aircraft": 1.5, "ssm": 3.0, "sam": 3.0,
               "ciws": 2.5, "air_search_radar": 3.0, "fire_control_radar": 2.0, "surface_radar": 1.5, "radar": 1.0, "esm_ecm": 1.5, "mines": 1.5,
               "minesweeping": 2.0, "rocket_launcher": 2.0, "ballistic_missile": 3.0}
_GUN_TOKEN = re.compile(r"^main_gun>=(\d+(?:\.\d+)?)mm$")


def candidate_tokens(design: dict[str, Any]) -> list[str]:
    """設計（元艦）が満たす抽象必須装備の候補。"""
    caps = caps_of_design(design)
    out: list[str] = []
    gun = main_gun_mm(design)
    if gun and gun >= 20:
        n = max((g for g in GUN_GRID if g <= gun), default=None)
        if n:
            out.append(f"main_gun>={n}mm")
    if "torpedo_tube" in caps:
        out.append("torpedo_tubes")
    if "asw_weapon" in caps:
        out.append("asw_weapon")
    if "sonar" in caps:
        out.append("sonar")
    if "air:helicopter" in caps:
        out.append("helicopter")
    elif "aircraft" in caps:
        out.append("aircraft")
    for k in ("ssm", "sam", "ciws", "rocket_launcher", "ballistic_missile"):
        if k in caps:
            out.append(k)
    if "air_search_radar" in caps:
        out.append("air_search_radar")
    if "fire_control_radar" in caps:
        out.append("fire_control_radar")
    if "surface_radar" in caps:
        out.append("surface_radar")
    if "radar_any" in caps and not ({"air_search_radar", "fire_control_radar", "surface_radar"} & caps):
        out.append("radar")
    if "esm_ecm" in caps:
        out.append("esm_ecm")
    if "mine" in caps:
        out.append("mines")
    if "minesweeping_gear" in caps:
        out.append("minesweeping")
    return out


def token_weight(tok: str) -> float:
    return REQ_WEIGHTS["main_gun"] if tok.startswith("main_gun") else REQ_WEIGHTS.get(tok, 1.0)


def satisfies(token: str, design: dict[str, Any]) -> bool:
    caps = caps_of_design(design)
    m = _GUN_TOKEN.match(token)
    if m:
        g = main_gun_mm(design)
        return g is not None and g >= float(m.group(1))
    return {
        "torpedo_tubes": "torpedo_tube" in caps, "asw_weapon": "asw_weapon" in caps, "sonar": "sonar" in caps,
        "helicopter": "air:helicopter" in caps, "aircraft": "aircraft" in caps, "ssm": "ssm" in caps, "sam": "sam" in caps, "ciws": "ciws" in caps,
        "rocket_launcher": "rocket_launcher" in caps, "ballistic_missile": "ballistic_missile" in caps,
        "air_search_radar": "air_search_radar" in caps, "fire_control_radar": "fire_control_radar" in caps, "surface_radar": "surface_radar" in caps,
        "radar": "radar_any" in caps, "esm_ecm": "esm_ecm" in caps, "mines": "mine" in caps, "minesweeping": "minesweeping_gear" in caps,
    }.get(token, False)


def _nice_round(x: float, sig: int = 2) -> float:
    if x <= 0:
        return x
    e = math.floor(math.log10(x)) - (sig - 1)
    return round(x / 10 ** e) * 10 ** e


def _nice_up(x: float, floor_ratio: float) -> float:
    """2 桁に丸める。ただし元の値の floor_ratio 倍を下回らない（上限は元艦を必ず満たす）。"""
    y = _nice_round(x)
    return float(y if y >= floor_ratio * x else math.ceil(x))


def _num(x: float) -> float | int:
    return int(x) if float(x).is_integer() else round(x, 1)


def make_requirements(design: dict[str, Any], country: str, year: int, rng: random.Random) -> dict[str, Any]:
    """元艦の設計から要求を逆算する。上限は元艦を必ず満たす（排水量 = 実値×(1+U[5,20]%)、速力 = 実値−U[0,2]kn）。"""
    dref, basis = ref_displacement(design)
    assert dref is not None
    req: dict[str, Any] = {"country": country, "year": year, "ship_type": design["ship_type"]}
    if rng.random() < 0.4:
        req["ship_type_fine"] = design["ship_type_fine"]
    req["mission"] = mission_tags(design)
    lim = dref * (1 + rng.uniform(0.05, 0.20))
    cand = _nice_round(lim)  # 2 significant figures, kept only if it stays inside the +5..+20 % band
    req["max_displacement_t"] = _num(cand if 1.05 * dref <= cand <= 1.20 * dref else math.ceil(lim))
    req["displacement_basis"] = basis
    v = design.get("speed_kn")
    if v:
        req["min_speed_kn"] = max(2.0, math.floor((v - rng.uniform(0, 2)) / 0.5) * 0.5)
        req["min_speed_kn"] = _num(min(req["min_speed_kn"], v))
    cands = candidate_tokens(design)
    chosen: list[str] = []
    if cands:
        n = rng.choices([1, 2, 3], weights=[0.35, 0.45, 0.20])[0]
        pool = list(cands)
        while pool and len(chosen) < n:
            w = [token_weight(t) for t in pool]
            t = rng.choices(pool, weights=w)[0]
            pool.remove(t)
            if t == "aircraft" and "helicopter" in chosen or t == "helicopter" and "aircraft" in chosen:
                continue
            if t == "radar" and any(c.endswith("_radar") for c in chosen):
                continue
            chosen.append(t)
    req["required"] = sorted(chosen, key=lambda t: cands.index(t))
    if design.get("complement") and rng.random() < 0.3:
        c = design["complement"]
        req["max_complement"] = int(_nice_up(c * (1 + rng.uniform(0.05, 0.25)), 1.03))
    if design.get("endurance_nm") and rng.random() < 0.3:
        e = design["endurance_nm"]
        step = 50 if e < 1000 else 100
        req["min_endurance_nm"] = int(max(step, math.floor(e * (1 - rng.uniform(0, 0.15)) / step) * step))
    return req


_TYPE_NAME = {
    "battleship": "battleship", "battlecruiser": "battlecruiser", "ironclad": "ironclad warship", "coastal_defense_monitor": "coastal defence monitor",
    "aircraft_carrier": "aircraft carrier", "armored_protected_cruiser": "protected cruiser", "heavy_cruiser": "heavy cruiser", "light_cruiser": "light cruiser",
    "cruiser": "cruiser", "destroyer": "destroyer", "frigate": "frigate", "corvette": "corvette", "escort": "escort vessel", "torpedo_boat": "torpedo boat",
    "fast_attack_craft": "fast attack craft", "patrol": "patrol vessel", "coast_guard": "coast guard vessel", "gunboat_river": "gunboat",
    "submarine": "submarine", "mine_warfare": "mine warfare vessel", "amphibious": "amphibious warfare vessel", "auxiliary": "auxiliary vessel",
    "training": "training ship",
}
_TYPE_JA = {
    "battleship": "戦艦", "battlecruiser": "巡洋戦艦", "ironclad": "装甲艦", "coastal_defense_monitor": "沿岸防衛艦（モニター）", "aircraft_carrier": "航空母艦",
    "armored_protected_cruiser": "防護巡洋艦", "heavy_cruiser": "重巡洋艦", "light_cruiser": "軽巡洋艦", "cruiser": "巡洋艦", "destroyer": "駆逐艦", "frigate": "フリゲート",
    "corvette": "コルベット", "escort": "護衛艦", "torpedo_boat": "水雷艇", "fast_attack_craft": "高速攻撃艇", "patrol": "哨戒艇・巡視艇", "coast_guard": "沿岸警備艇",
    "gunboat_river": "砲艦", "submarine": "潜水艦", "mine_warfare": "掃海・敷設艦艇", "amphibious": "揚陸艦艇", "auxiliary": "補助艦", "training": "練習艦",
}
_TOKEN_EN = {"torpedo_tubes": "torpedo tubes", "asw_weapon": "an anti-submarine weapon", "sonar": "sonar", "helicopter": "a helicopter", "aircraft": "aircraft facilities",
             "ssm": "anti-ship missiles", "sam": "surface-to-air missiles", "ciws": "a close-in weapon system", "rocket_launcher": "a rocket launcher",
             "ballistic_missile": "ballistic missiles", "air_search_radar": "an air-search radar", "fire_control_radar": "a fire-control radar",
             "surface_radar": "a surface-search radar", "radar": "a radar", "esm_ecm": "ESM/ECM equipment", "mines": "mine-laying capability",
             "minesweeping": "minesweeping gear"}
_TOKEN_JA = {"torpedo_tubes": "魚雷発射管", "asw_weapon": "対潜兵装", "sonar": "ソナー", "helicopter": "ヘリコプター", "aircraft": "航空機運用設備",
             "ssm": "対艦ミサイル", "sam": "対空ミサイル", "ciws": "近接防御火器", "rocket_launcher": "ロケット発射機", "ballistic_missile": "弾道ミサイル",
             "air_search_radar": "対空捜索レーダー", "fire_control_radar": "射撃管制レーダー", "surface_radar": "対水上レーダー", "radar": "レーダー",
             "esm_ecm": "ESM/ECM", "mines": "機雷敷設能力", "minesweeping": "掃海具"}
_MISSION_EN = {"patrol": "patrol", "asw": "anti-submarine warfare", "aaw": "air defence", "surface_strike": "surface strike", "escort": "escort", "mine_warfare": "mine warfare",
               "amphibious": "amphibious operations", "fire_support": "fire support", "coastal_defense": "coastal defence", "submarine_ops": "submarine operations",
               "carrier_air": "carrier aviation", "support": "fleet support", "training": "training", "general": "general service"}
_MISSION_JA = {"patrol": "哨戒", "asw": "対潜戦", "aaw": "防空", "surface_strike": "水上打撃", "escort": "護衛", "mine_warfare": "対機雷戦", "amphibious": "揚陸",
               "fire_support": "火力支援", "coastal_defense": "沿岸防衛", "submarine_ops": "潜水艦作戦", "carrier_air": "航空作戦", "support": "艦隊支援", "training": "練習", "general": "一般任務"}
_COUNTRY_JA = {"Japan": "日本", "United-States": "アメリカ", "United-Kingdom": "イギリス", "Germany": "ドイツ", "Italy": "イタリア", "France": "フランス", "Russian": "ソ連／ロシア"}


_NATION_ADJ = {"Japan": "Japanese", "United States": "US", "United Kingdom": "British", "Germany": "German", "Italy": "Italian", "France": "French",
               "Russian": "Soviet/Russian", "China": "Chinese", "Spain": "Spanish", "Netherlands": "Dutch", "Sweden": "Swedish", "Norway": "Norwegian",
               "Denmark": "Danish", "Greece": "Greek", "Turkey": "Turkish", "Poland": "Polish", "Finland": "Finnish", "Canada": "Canadian", "Australia": "Australian"}


def _en_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _req_item_en(tok: str) -> str:
    m = _GUN_TOKEN.match(tok)
    if m:
        return f"a main gun of at least {m.group(1)} mm"
    return _TOKEN_EN.get(tok, tok)


def _req_item_ja(tok: str) -> str:
    m = _GUN_TOKEN.match(tok)
    if m:
        return f"口径{m.group(1)}mm以上の主砲"
    return _TOKEN_JA.get(tok, tok)


def request_text(req: dict[str, Any], variant: int = 0, premise: str | None = None) -> str:
    """要求の自然文（英語）。variant で文型を変える。premise は架空前提（シナリオ用: 'Assume Japan won the war.' など）。"""
    t = req["ship_type_fine"].replace("-", " ") if req.get("ship_type_fine") else _TYPE_NAME.get(req["ship_type"], req["ship_type"].replace("_", " "))
    art = "an" if t[0] in "aeiou" else "a"
    nation = req["country"].replace("-", " ")
    adj = _NATION_ADJ.get(nation)
    nation_adj = f"the {adj} navy" if adj else f"the navy of {nation}"
    basis = req.get("displacement_basis", "normal")
    parts_lim = [f"displacement at most {req['max_displacement_t']} t ({basis})"]
    if req.get("min_speed_kn") is not None:
        parts_lim.append(f"speed at least {req['min_speed_kn']} kn")
    if req.get("max_complement"):
        parts_lim.append(f"crew at most {req['max_complement']}")
    if req.get("min_endurance_nm"):
        parts_lim.append(f"endurance at least {req['min_endurance_nm']} nm")
    need = [_req_item_en(t_) for t_ in req.get("required", [])]
    mission = _en_list([_MISSION_EN.get(m, m) for m in req.get("mission", [])])
    lim = _en_list(parts_lim)
    need_s = f" It must carry {_en_list(need)}." if need else ""
    pre = (premise.strip() + " ") if premise else ""
    if variant % 3 == 0:
        s = f"{pre}Design {art} {t} for {nation_adj} around {req['year']}, for {mission}: {lim}.{need_s}"
    elif variant % 3 == 1:
        s = f"{pre}{nation_adj[0].upper() + nation_adj[1:]} wants {art} {t} designed in {req['year']}; role: {mission}. Limits: {lim}.{need_s}"
    else:
        s = f"{pre}Propose {art} {t} design ({nation}, {req['year']}) for {mission} with {lim}.{need_s}"
    return s


def request_text_ja(req: dict[str, Any], premise: str | None = None) -> str:
    t = _TYPE_JA.get(req["ship_type"], req["ship_type"])
    if req.get("ship_type_fine"):
        t = f"{t}（{req['ship_type_fine']}）"
    nation = _COUNTRY_JA.get(req["country"], req["country"].replace("-", " "))
    basis = {"normal": "常備", "full": "満載", "standard": "基準"}.get(req.get("displacement_basis", "normal"), "")
    parts = [f"{basis}排水量{req['max_displacement_t']}t以下"]
    if req.get("min_speed_kn") is not None:
        parts.append(f"速力{req['min_speed_kn']}kn以上")
    if req.get("max_complement"):
        parts.append(f"乗員{req['max_complement']}名以下")
    if req.get("min_endurance_nm"):
        parts.append(f"航続{req['min_endurance_nm']}nm以上")
    need = "、".join(_req_item_ja(x) for x in req.get("required", []))
    mission = "・".join(_MISSION_JA.get(m, m) for m in req.get("mission", []))
    pre = (premise + " ") if premise else ""
    return f"{pre}{req['year']}年ごろの{nation}海軍の{t}を設計してください。任務: {mission}。" + "、".join(parts) + "。" + (f"必須装備: {need}。" if need else "")


# --------------------------------------------------------------------------------------------------
# neighbour display (short keys)
# --------------------------------------------------------------------------------------------------
NBR_KEYMAP = {"displacement_normal_t": "dn", "displacement_full_t": "df", "displacement_standard_t": "ds", "length_m": "L", "beam_m": "B", "draught_m": "T",
              "speed_kn": "v", "power_hp": "hp", "shafts": "sh", "complement": "cm", "endurance_nm": "en", "propulsion": "pr", "weapons": "w", "sensors": "s",
              "armour": "ar", "air": "air"}
NBR_INV = {v: k for k, v in NBR_KEYMAP.items()}
NBR_LEGEND = ("nbr keys: cn country, y year, ty type, ft fine type, dn/df/ds displacement normal/full/standard t, L/B/T length/beam/draught m, v kn, hp, sh shafts, "
              "cm crew, en endurance nm, pr propulsion, w weapons, s sensors, air aircraft, ar armour mm, h history [year, change].")


def nbr_payload(rec: dict[str, Any], history: list[list[Any]] | None) -> dict[str, Any]:
    """近傍 1 艦の短い表示。艦名・艦級名・URL・id・距離・year_in_navy は出さない。"""
    d = design_from_record(rec)
    p: dict[str, Any] = {"cn": rec["country"].replace("-", " "), "y": rec["year"], "ty": d["ship_type"], "ft": d["ship_type_fine"]}
    for k in ("displacement_normal_t", "displacement_full_t", "displacement_standard_t", "length_m", "beam_m", "draught_m", "speed_kn", "power_hp", "shafts",
              "complement", "endurance_nm", "propulsion", "weapons", "sensors", "armour", "air"):
        if k in d:
            p[NBR_KEYMAP[k]] = d[k]
    if history:
        p["h"] = history
    return p


def payload_to_design(p: dict[str, Any]) -> dict[str, Any]:
    d: dict[str, Any] = {"ship_type": p.get("ty"), "ship_type_fine": p.get("ft")}
    for short, long in NBR_INV.items():
        if short in p:
            d[long] = p[short]
    return {k: d[k] for k in DESIGN_KEY_ORDER if k in d and d[k] is not None}
