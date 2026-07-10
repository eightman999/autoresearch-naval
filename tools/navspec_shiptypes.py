"""ship_type別の代表性諸元とhull_kindマッピング。

REPRESENTATIVE_SPECS の5分類では粗すぎるため、
艦種ごとに基準値を持たせる。
"""

from __future__ import annotations

from typing import Any


SHIP_TYPE_TO_HULL: dict[str, str] = {
    "battleship": "battleship",
    "pre_dreadnought_battleship": "battleship",
    "dreadnought_battleship": "battleship",
    "fast_battleship": "battleship",
    "battlecruiser": "battlecruiser",
    "large_cruiser": "battlecruiser",
    "light_cruiser": "cruiser",
    "heavy_cruiser": "cruiser",
    "treaty_cruiser": "cruiser",
    "protected_cruiser": "cruiser",
    "armored_cruiser": "cruiser",
    "commerce_raider_cruiser": "cruiser",
    "anti_air_light_cruiser": "cruiser",
    "coastal_defense_ship": "coastal_battleship",
    "destroyer": "destroyer",
    "large_destroyer": "destroyer",
    "anti_air_destroyer": "destroyer",
    "destroyer_escort": "destroyer",
    "patrol_frigate": "destroyer",
    "frigate": "destroyer",
    "corvette": "destroyer",
    "sloop": "destroyer",
    "patrol_boat": "other",
    "torpedo_boat": "other",
    "torpedo_boat_tender": "other",
    "ship_of_the_line": "other",
    "gunboat": "other",
    "minesweeper": "other",
    "minelayer": "other",
    "submarine_tender": "other",
    "seaplane_tender": "other",
    "oiler": "other",
    "repair_ship": "other",
    "training_ship": "other",
    "escort_ship": "destroyer",
}


SHIP_TYPE_SPECS: dict[str, dict[str, Any]] = {
    "battleship": {"normal_t": 30000, "speed": 25, "main_caliber_mm": 356, "belt_mm": 300, "barrels": 8, "shafts": 4},
    "pre_dreadnought_battleship": {"normal_t": 15000, "speed": 18, "main_caliber_mm": 305, "belt_mm": 280, "barrels": 4, "shafts": 4},
    "dreadnought_battleship": {"normal_t": 22000, "speed": 21, "main_caliber_mm": 305, "belt_mm": 280, "barrels": 10, "shafts": 4},
    "fast_battleship": {"normal_t": 38000, "speed": 28, "main_caliber_mm": 381, "belt_mm": 330, "barrels": 9, "shafts": 4},
    "battlecruiser": {"normal_t": 28000, "speed": 28, "main_caliber_mm": 356, "belt_mm": 250, "barrels": 8, "shafts": 4},
    "large_cruiser": {"normal_t": 30000, "speed": 33, "main_caliber_mm": 305, "belt_mm": 228, "barrels": 9, "shafts": 4},

    "light_cruiser": {"normal_t": 8000, "speed": 32, "main_caliber_mm": 152, "belt_mm": 76, "barrels": 8, "shafts": 4},
    "heavy_cruiser": {"normal_t": 12000, "speed": 32, "main_caliber_mm": 203, "belt_mm": 100, "barrels": 8, "shafts": 4},
    "treaty_cruiser": {"normal_t": 10000, "speed": 32, "main_caliber_mm": 203, "belt_mm": 76, "barrels": 8, "shafts": 4},
    "protected_cruiser": {"normal_t": 4000, "speed": 20, "main_caliber_mm": 152, "belt_mm": None, "barrels": 6, "shafts": 2},
    "armored_cruiser": {"normal_t": 10000, "speed": 22, "main_caliber_mm": 203, "belt_mm": 150, "barrels": 6, "shafts": 4},
    "commerce_raider_cruiser": {"normal_t": 15000, "speed": 30, "main_caliber_mm": 280, "belt_mm": 100, "barrels": 6, "shafts": 4},
    "anti_air_light_cruiser": {"normal_t": 8000, "speed": 32, "main_caliber_mm": 127, "belt_mm": 76, "barrels": 12, "shafts": 4},

    "coastal_defense_ship": {"normal_t": 6000, "speed": 18, "main_caliber_mm": 254, "belt_mm": 200, "barrels": 4, "shafts": 2},

    "destroyer": {"normal_t": 2000, "speed": 36, "main_caliber_mm": 127, "belt_mm": None, "barrels": 4, "shafts": 2},
    "large_destroyer": {"normal_t": 2500, "speed": 37, "main_caliber_mm": 150, "belt_mm": None, "barrels": 4, "shafts": 2},
    "anti_air_destroyer": {"normal_t": 2200, "speed": 34, "main_caliber_mm": 127, "belt_mm": None, "barrels": 6, "shafts": 2},
    "destroyer_escort": {"normal_t": 1500, "speed": 24, "main_caliber_mm": 76, "belt_mm": None, "barrels": 3, "shafts": 2},
    "patrol_frigate": {"normal_t": 1500, "speed": 20, "main_caliber_mm": 105, "belt_mm": None, "barrels": 2, "shafts": 2},
    "frigate": {"normal_t": 1800, "speed": 27, "main_caliber_mm": 127, "belt_mm": None, "barrels": 2, "shafts": 2},
    "corvette": {"normal_t": 1000, "speed": 20, "main_caliber_mm": 100, "belt_mm": None, "barrels": 1, "shafts": 2},
    "sloop": {"normal_t": 1200, "speed": 18, "main_caliber_mm": 100, "belt_mm": None, "barrels": 2, "shafts": 2},
    "escort_ship": {"normal_t": 1400, "speed": 22, "main_caliber_mm": 100, "belt_mm": None, "barrels": 2, "shafts": 2},

    "patrol_boat": {"normal_t": 500, "speed": 20, "main_caliber_mm": 76, "belt_mm": None, "barrels": 2, "shafts": 2},
    "torpedo_boat": {"normal_t": 300, "speed": 30, "main_caliber_mm": 76, "belt_mm": None, "barrels": 2, "shafts": 2},
    "torpedo_boat_tender": {"normal_t": 4000, "speed": 18, "main_caliber_mm": 127, "belt_mm": None, "barrels": 4, "shafts": 2},
    "gunboat": {"normal_t": 800, "speed": 16, "main_caliber_mm": 100, "belt_mm": None, "barrels": 2, "shafts": 2},
    "minesweeper": {"normal_t": 700, "speed": 16, "main_caliber_mm": 76, "belt_mm": None, "barrels": 2, "shafts": 2},
    "minelayer": {"normal_t": 2000, "speed": 18, "main_caliber_mm": 100, "belt_mm": None, "barrels": 2, "shafts": 2},
    "submarine_tender": {"normal_t": 8000, "speed": 18, "main_caliber_mm": 127, "belt_mm": None, "barrels": 4, "shafts": 2},
    "seaplane_tender": {"normal_t": 10000, "speed": 18, "main_caliber_mm": 127, "belt_mm": None, "barrels": 4, "shafts": 2},
    "oiler": {"normal_t": 8000, "speed": 16, "main_caliber_mm": 76, "belt_mm": None, "barrels": 2, "shafts": 2},
    "repair_ship": {"normal_t": 8000, "speed": 16, "main_caliber_mm": 100, "belt_mm": None, "barrels": 2, "shafts": 2},
    "training_ship": {"normal_t": 3000, "speed": 18, "main_caliber_mm": 100, "belt_mm": None, "barrels": 4, "shafts": 2},

    "ship_of_the_line": {"normal_t": 3500, "speed": 10, "main_caliber_mm": 203, "belt_mm": None, "barrels": 30, "shafts": 1},
}


_REPRESENTATIVE_SPECS_LEGACY: dict[str, dict[str, Any]] = {
    "battleship": SHIP_TYPE_SPECS["battleship"],
    "battlecruiser": SHIP_TYPE_SPECS["battlecruiser"],
    "cruiser": SHIP_TYPE_SPECS["light_cruiser"],
    "destroyer": SHIP_TYPE_SPECS["destroyer"],
    "coastal_battleship": SHIP_TYPE_SPECS["coastal_defense_ship"],
    "other": {"normal_t": 1000, "speed": 20, "main_caliber_mm": 100, "belt_mm": None, "barrels": 2, "shafts": 2},
}


def resolve_ship_type(ship_type: str | None, hull_kind: str | None) -> str:
    """ship_type が不明なら hull_kind に基づいて推測する。"""
    if ship_type and ship_type in SHIP_TYPE_SPECS:
        return ship_type
    legacy_map = {
        "cruiser": "light_cruiser",
        "coastal_battleship": "coastal_defense_ship",
        "frigate": "patrol_frigate",
    }
    check = ship_type or hull_kind
    if check in legacy_map:
        return legacy_map[check]
    if check and check in SHIP_TYPE_SPECS:
        return check
    if check and check in _REPRESENTATIVE_SPECS_LEGACY:
        return check
    return "other"


def get_ship_type_specs(ship_type: str) -> dict[str, Any]:
    return SHIP_TYPE_SPECS.get(ship_type, SHIP_TYPE_SPECS.get("patrol_boat", _REPRESENTATIVE_SPECS_LEGACY["other"]))


def hull_kind_for_ship_type(ship_type: str) -> str:
    return SHIP_TYPE_TO_HULL.get(ship_type, "other")


ERA_MAIN_GUN_CLAMP: dict[str, tuple[float, float]] = {
    "sail_steam_transition": (100, 230),
    "pre_dreadnought": (100, 305),
    "dreadnought": (280, 356),
    "treaty_interwar": (127, 406),
    "fast_battleship": (127, 460),
    "postwar": (76, 305),
    "unknown": (76, 520),
}

ERA_SPEED_CLAMP: dict[str, tuple[float, float]] = {
    "sail_steam_transition": (6, 14),
    "pre_dreadnought": (10, 24),
    "dreadnought": (14, 28),
    "treaty_interwar": (16, 37),
    "fast_battleship": (16, 42),
    "postwar": (16, 40),
    "unknown": (5, 45),
}


SHIP_TYPE_MAIN_GUN_CLAMP: dict[str, tuple[float, float]] = {
    "patrol_boat": (37, 76),
    "torpedo_boat": (37, 88),
    "gunboat": (47, 120),
    "minesweeper": (37, 76),
    "patrol_frigate": (76, 127),
    "corvette": (76, 102),
    "sloop": (76, 120),
    "destroyer_escort": (76, 127),
    "escort_ship": (76, 127),
    "destroyer": (100, 130),
    "large_destroyer": (100, 155),
    "anti_air_destroyer": (100, 130),
    "frigate": (76, 127),
    "light_cruiser": (127, 155),
    "anti_air_light_cruiser": (100, 139),
    "protected_cruiser": (100, 164),
    "treaty_cruiser": (152, 203),
    "heavy_cruiser": (152, 203),
    "armored_cruiser": (152, 254),
    "commerce_raider_cruiser": (150, 283),
    "ship_of_the_line": (100, 230),
    "torpedo_boat_tender": (76, 152),
    "submarine_tender": (76, 152),
    "seaplane_tender": (76, 152),
    "oiler": (37, 100),
    "repair_ship": (76, 120),
    "training_ship": (76, 152),
    "minelayer": (76, 120),
    "pre_dreadnought_battleship": (254, 305),
    "dreadnought_battleship": (283, 343),
    "battleship": (283, 460),
    "fast_battleship": (330, 460),
    "battlecruiser": (283, 381),
    "large_cruiser": (283, 381),
    "coastal_defense_ship": (203, 305),
}


SHIP_TYPE_SPEED_CLAMP: dict[str, tuple[float, float]] = {
    "patrol_boat": (14, 28),
    "torpedo_boat": (24, 40),
    "gunboat": (10, 20),
    "minesweeper": (10, 20),
    "patrol_frigate": (16, 28),
    "corvette": (14, 24),
    "sloop": (14, 22),
    "destroyer_escort": (18, 28),
    "escort_ship": (16, 26),
    "destroyer": (30, 42),
    "large_destroyer": (32, 42),
    "anti_air_destroyer": (28, 38),
    "frigate": (20, 32),
    "light_cruiser": (28, 37),
    "anti_air_light_cruiser": (28, 36),
    "protected_cruiser": (16, 24),
    "treaty_cruiser": (28, 36),
    "heavy_cruiser": (28, 36),
    "armored_cruiser": (18, 26),
    "commerce_raider_cruiser": (26, 34),
    "ship_of_the_line": (6, 12),
    "torpedo_boat_tender": (14, 22),
    "submarine_tender": (14, 22),
    "seaplane_tender": (14, 22),
    "oiler": (12, 20),
    "repair_ship": (12, 20),
    "training_ship": (14, 22),
    "minelayer": (12, 22),
    "pre_dreadnought_battleship": (15, 20),
    "dreadnought_battleship": (18, 24),
    "battleship": (20, 30),
    "fast_battleship": (26, 33),
    "battlecruiser": (26, 33),
    "large_cruiser": (30, 35),
    "coastal_defense_ship": (14, 22),
}


def era_for_year(year: int | None) -> str:
    if year is None:
        return "unknown"
    if year < 1870:
        return "sail_steam_transition"
    if year < 1890:
        return "pre_dreadnought"
    if year < 1906:
        return "pre_dreadnought"
    if year <= 1918:
        return "dreadnought"
    if year <= 1935:
        return "treaty_interwar"
    if year <= 1945:
        return "fast_battleship"
    return "postwar"


def clamp_main_caliber(mm: float | None, ship_type: str, year: int | None) -> float | None:
    if mm is None:
        return None
    era = era_for_year(year)
    era_lo, era_hi = ERA_MAIN_GUN_CLAMP.get(era, (76, 520))
    st_lo, st_hi = SHIP_TYPE_MAIN_GUN_CLAMP.get(ship_type, (era_lo, era_hi))
    lo = max(era_lo, st_lo)
    hi = min(era_hi, st_hi)
    if lo > hi:
        lo, hi = st_lo, st_hi
    return round(max(lo, min(hi, mm)), 1)


def clamp_speed(kt: float | None, ship_type: str, year: int | None) -> float | None:
    if kt is None:
        return None
    era = era_for_year(year)
    era_lo, era_hi = ERA_SPEED_CLAMP.get(era, (5, 45))
    st_lo, st_hi = SHIP_TYPE_SPEED_CLAMP.get(ship_type, (era_lo, era_hi))
    lo = max(era_lo, st_lo)
    hi = min(era_hi, st_hi)
    if lo > hi:
        lo, hi = st_lo, st_hi
    return round(max(lo, min(hi, kt)), 1)


DIM_FACTORS_BY_HULL: dict[str, tuple[float, float, float]] = {
    "battleship": (7.2, 1.05, 0.35),
    "battlecruiser": (7.8, 1.05, 0.33),
    "cruiser": (8.5, 0.94, 0.30),
    "destroyer": (9.6, 0.96, 0.28),
    "coastal_battleship": (6.0, 1.05, 0.36),
    "other": (6.0, 1.0, 0.33),
    "submarine": (7.0, 1.0, 0.40),
    "carrier": (8.0, 1.0, 0.30),
}

DIM_FACTORS_BY_SHIP_TYPE: dict[str, tuple[float, float, float]] = {
    "destroyer": (9.6, 0.96, 0.28),
    "destroyer_escort": (8.5, 0.95, 0.28),
    "anti_air_destroyer": (9.6, 0.96, 0.28),
    "large_destroyer": (9.8, 0.98, 0.28),
    "frigate": (8.0, 0.90, 0.28),
    "patrol_frigate": (8.0, 0.90, 0.28),
    "corvette": (6.5, 0.95, 0.30),
    "sloop": (6.5, 0.95, 0.30),
    "escort_ship": (7.5, 0.92, 0.29),
    "light_cruiser": (8.5, 0.94, 0.30),
    "heavy_cruiser": (8.3, 0.96, 0.31),
    "treaty_cruiser": (8.4, 0.95, 0.30),
    "protected_cruiser": (7.5, 0.92, 0.30),
    "armored_cruiser": (7.2, 0.98, 0.33),
    "commerce_raider_cruiser": (7.8, 0.95, 0.31),
    "anti_air_light_cruiser": (8.5, 0.94, 0.30),
    "pre_dreadnought_battleship": (6.5, 1.05, 0.36),
    "dreadnought_battleship": (7.0, 1.05, 0.35),
    "battleship": (7.2, 1.05, 0.35),
    "fast_battleship": (7.5, 1.02, 0.34),
    "battlecruiser": (7.8, 1.05, 0.33),
    "large_cruiser": (7.8, 1.0, 0.33),
    "coastal_defense_ship": (6.0, 1.05, 0.36),
    "patrol_boat": (5.5, 1.0, 0.33),
    "torpedo_boat": (8.0, 0.85, 0.25),
    "gunboat": (5.0, 1.0, 0.33),
    "minesweeper": (5.5, 1.05, 0.33),
    "minelayer": (6.0, 1.0, 0.33),
    "torpedo_boat_tender": (6.0, 1.05, 0.35),
    "submarine_tender": (6.0, 1.1, 0.38),
    "seaplane_tender": (6.5, 1.05, 0.35),
    "oiler": (6.0, 1.1, 0.38),
    "repair_ship": (6.0, 1.1, 0.38),
    "training_ship": (6.5, 1.0, 0.33),
    "ship_of_the_line": (5.0, 1.2, 0.42),
}


LB_RATIO_BANDS_BY_HULL: dict[str, tuple[float, float]] = {
    "destroyer": (9.0, 11.5),
    "cruiser": (8.5, 10.5),
    "battleship": (6.5, 8.5),
    "battlecruiser": (7.0, 9.5),
    "coastal_battleship": (5.5, 7.5),
    "carrier": (7.0, 10.5),
    "submarine": (7.0, 13.0),
    "other": (5.0, 8.0),
}


LB_RATIO_BANDS_BY_SHIP_TYPE: dict[str, tuple[float, float]] = {
    "destroyer": (9.0, 11.5),
    "large_destroyer": (9.0, 11.5),
    "anti_air_destroyer": (9.0, 11.5),
    "destroyer_escort": (9.0, 11.5),
    "frigate": (9.0, 11.5),
    "patrol_frigate": (9.0, 11.5),
    "escort_ship": (9.0, 11.5),
    "light_cruiser": (8.5, 10.5),
    "heavy_cruiser": (8.5, 10.5),
    "treaty_cruiser": (8.5, 10.5),
    "protected_cruiser": (8.5, 10.5),
    "armored_cruiser": (8.5, 10.5),
    "commerce_raider_cruiser": (8.5, 10.5),
    "anti_air_light_cruiser": (8.5, 10.5),
    "pre_dreadnought_battleship": (6.5, 8.5),
    "dreadnought_battleship": (6.5, 8.5),
    "battleship": (6.5, 8.5),
    "fast_battleship": (6.5, 8.5),
    "battlecruiser": (7.0, 9.5),
    "large_cruiser": (7.0, 9.5),
    "coastal_defense_ship": (5.5, 7.5),
    "patrol_boat": (5.0, 8.0),
    "torpedo_boat": (5.0, 8.0),
    "gunboat": (5.0, 8.0),
    "minesweeper": (5.0, 8.0),
    "minelayer": (5.0, 8.0),
    "corvette": (5.0, 8.0),
    "sloop": (5.0, 8.0),
    "torpedo_boat_tender": (5.0, 8.0),
    "submarine_tender": (5.0, 8.0),
    "seaplane_tender": (5.0, 8.0),
    "oiler": (5.0, 8.0),
    "repair_ship": (5.0, 8.0),
    "training_ship": (5.0, 8.0),
    "ship_of_the_line": (3.5, 5.0),
}


def get_dim_factors(ship_type: str | None, hull_kind: str | None) -> tuple[float, float, float]:
    if ship_type and ship_type in DIM_FACTORS_BY_SHIP_TYPE:
        return DIM_FACTORS_BY_SHIP_TYPE[ship_type]
    if hull_kind and hull_kind in DIM_FACTORS_BY_HULL:
        return DIM_FACTORS_BY_HULL[hull_kind]
    return DIM_FACTORS_BY_HULL["other"]


def lb_ratio_band_for(ship_type: str | None, hull_kind: str | None) -> tuple[float, float]:
    if ship_type and ship_type in LB_RATIO_BANDS_BY_SHIP_TYPE:
        return LB_RATIO_BANDS_BY_SHIP_TYPE[ship_type]
    if hull_kind and hull_kind in LB_RATIO_BANDS_BY_HULL:
        return LB_RATIO_BANDS_BY_HULL[hull_kind]
    return LB_RATIO_BANDS_BY_HULL["other"]
