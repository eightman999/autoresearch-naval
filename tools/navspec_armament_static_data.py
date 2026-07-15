"""Stage 4 Commit A: fire_control / compatibility_rules の生成.

STAGE4_PROGRESS.md §2 Commit A の DB seed のうち、navweaps 個別武器ページから
機械抽出できない「射撃指揮装置」と「互換性ルール」を手動定義して出力する。

- ``data/navspec/armament/fire_control.jsonl``: 各国・時代の代表的 director 17件
- ``data/navspec/armament/compatibility_rules.json``: validator が使うルール表

これらは ``navspec_armament_scraper.py`` の機械抽出結果 (weapons/mounts/ammunition)
を補完する静的データであり、再生成可能にするためスクリプト化している。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ----------------------------------------------------------------------------
# fire_control.jsonl
# ----------------------------------------------------------------------------

FIRE_CONTROL_RECORDS: list[dict] = [
    # --- Japan ---
    {
        "fire_control_id": "JP_TYPE94_DIRECTOR",
        "name": "Type 94 Gunnery Director (main battery)",
        "nation": "JP",
        "year_introduced": 1941,
        "system_type": "main_battery_director",
        "radar": False,
        "range_finder_m": 15.0,
        "director_type": "type94_main",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "15m coincident rangefinder; installed on Yamato class.",
        "fictional": False,
    },
    {
        "fire_control_id": "JP_TYPE94_KOSHAKI",
        "name": "Type 94 High-Angle Fire Control Device",
        "nation": "JP",
        "year_introduced": 1934,
        "system_type": "aa_director",
        "radar": False,
        "range_finder_m": 4.5,
        "director_type": "type94_aa",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "",
        "fictional": False,
    },
    {
        "fire_control_id": "JP_TYPE98_DIRECTOR",
        "name": "Type 98 High-Angle Fire Control Device",
        "nation": "JP",
        "year_introduced": 1942,
        "system_type": "dual_purpose_director",
        "radar": False,
        "range_finder_m": 4.5,
        "director_type": "type98_dp",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Akizuki class main-battery DP director.",
        "fictional": False,
    },
    # --- United States ---
    {
        "fire_control_id": "US_MK38_GFCS",
        "name": "Mk 38 Gun Fire Control System",
        "nation": "US",
        "year_introduced": 1943,
        "system_type": "main_battery_director",
        "radar": True,
        "range_finder_m": 14.0,
        "director_type": "mk38",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Iowa class; pairs Mk 8 rangekeeper with Mk 13 radar.",
        "fictional": False,
    },
    {
        "fire_control_id": "US_MK37_GFCS",
        "name": "Mk 37 Gun Fire Control System",
        "nation": "US",
        "year_introduced": 1939,
        "system_type": "dual_purpose_director",
        "radar": True,
        "range_finder_m": 3.66,
        "director_type": "mk37",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Dual-purpose director for 5\"/38; pairs with Mk 1 computer.",
        "fictional": False,
    },
    {
        "fire_control_id": "US_MK34_GFCS",
        "name": "Mk 34 Gun Fire Control System",
        "nation": "US",
        "year_introduced": 1933,
        "system_type": "main_battery_director",
        "radar": False,
        "range_finder_m": 7.92,
        "director_type": "mk34",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "",
        "fictional": False,
    },
    {
        "fire_control_id": "US_MK8_RANGEKEEPER",
        "name": "Ford Mk 8 Rangekeeper",
        "nation": "US",
        "year_introduced": 1934,
        "system_type": "mechanical_analog_computer",
        "radar": False,
        "range_finder_m": None,
        "director_type": "mk8_rangekeeper",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Analog fire-control computer for main battery.",
        "fictional": False,
    },
    # --- United Kingdom ---
    {
        "fire_control_id": "UK_AFCT",
        "name": "Admiralty Fire Control Table (AFCT)",
        "nation": "UK",
        "year_introduced": 1924,
        "system_type": "main_battery_director",
        "radar": False,
        "range_finder_m": 15.0,
        "director_type": "afct",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Nelson, KGV, Vanguard main-battery FC table. progress §3 alias UK_AFKCT.",
        "fictional": False,
    },
    {
        "fire_control_id": "UK_DREYER_TABLE",
        "name": "Dreyer Fire Control Table",
        "nation": "UK",
        "year_introduced": 1911,
        "system_type": "main_battery_director",
        "radar": False,
        "range_finder_m": 9.0,
        "director_type": "dreyer",
        "technology_tier": "legacy",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "WWI-era table used on Iron Duke, QE, R class.",
        "fictional": False,
    },
    {
        "fire_control_id": "UK_HACS",
        "name": "High Angle Control System (HACS)",
        "nation": "UK",
        "year_introduced": 1931,
        "system_type": "aa_director",
        "radar": False,
        "range_finder_m": 4.0,
        "director_type": "hacs",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "KGV and cruiser AA director; radar retrofitted later.",
        "fictional": False,
    },
    {
        "fire_control_id": "UK_AFCC",
        "name": "Admiralty Fire Control Clock (AFCC)",
        "nation": "UK",
        "year_introduced": 1926,
        "system_type": "mechanical_analog_computer",
        "radar": False,
        "range_finder_m": None,
        "director_type": "afcc",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Cruiser-scale analog FC clock.",
        "fictional": False,
    },
    # --- Germany ---
    {
        "fire_control_id": "DE_C38_DIRECTOR",
        "name": "C/38 Leitstand (main-battery director)",
        "nation": "DE",
        "year_introduced": 1938,
        "system_type": "main_battery_director",
        "radar": True,
        "range_finder_m": 10.5,
        "director_type": "c38",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Bismarck / Scharnhorst; FuMO radar retrofitted.",
        "fictional": False,
    },
    {
        "fire_control_id": "DE_C33_DIRECTOR",
        "name": "C/33 Leitstand (AA director)",
        "nation": "DE",
        "year_introduced": 1933,
        "system_type": "aa_director",
        "radar": False,
        "range_finder_m": 4.0,
        "director_type": "c33",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "",
        "fictional": False,
    },
    # --- France ---
    {
        "fire_control_id": "FR_M1935_PC",
        "name": "Poste Central Mle 1935 (PC Mle 1935)",
        "nation": "FR",
        "year_introduced": 1940,
        "system_type": "main_battery_director",
        "radar": False,
        "range_finder_m": 14.0,
        "director_type": "pc_m1935",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Richelieu class main-battery central director.",
        "fictional": False,
    },
    {
        "fire_control_id": "FR_M1922_FC",
        "name": "Mle 1922 Fire Control Table",
        "nation": "FR",
        "year_introduced": 1922,
        "system_type": "main_battery_director",
        "radar": False,
        "range_finder_m": 8.0,
        "director_type": "m1922",
        "technology_tier": "legacy",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "",
        "fictional": False,
    },
    # --- Italy ---
    {
        "fire_control_id": "IT_EC3_TRIPLEX",
        "name": "EC-3 Triplex (Galileo)",
        "nation": "IT",
        "year_introduced": 1940,
        "system_type": "main_battery_director",
        "radar": True,
        "range_finder_m": 7.2,
        "director_type": "ec3_triples",
        "technology_tier": "mainstream",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Littorio class; paired with Gufo radar.",
        "fictional": False,
    },
    {
        "fire_control_id": "IT_RM2_DIRECTOR",
        "name": "RM-2 Director (Galileo)",
        "nation": "IT",
        "year_introduced": 1929,
        "system_type": "main_battery_director",
        "radar": False,
        "range_finder_m": 5.0,
        "director_type": "rm2",
        "technology_tier": "legacy",
        "source": "https://www.navweaps.com/index_tech/firecontrol.php",
        "source_type": "curated",
        "verified": False,
        "confidence": 0.7,
        "notes": "Zara and earlier cruiser director.",
        "fictional": False,
    },
]


def write_fire_control(out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in FIRE_CONTROL_RECORDS:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote: {out_path} ({len(FIRE_CONTROL_RECORDS)} records)")
    return len(FIRE_CONTROL_RECORDS)


# ----------------------------------------------------------------------------
# compatibility_rules.json
# ----------------------------------------------------------------------------

def build_compatibility(
    weapons_path: Path,
    mounts_path: Path,
) -> dict:
    """weapons.jsonl / mounts.jsonl から weapon_to_mount を自動構築し、
    静的ルール (口径/艦種, 物理制約) と結合する。"""
    weapon_to_mount: dict[str, list[str]] = {}
    if weapons_path.exists() and mounts_path.exists():
        weapons = {json.loads(l)["weapon_id"] for l in weapons_path.open(encoding="utf-8")}
        mounts = [json.loads(l) for l in mounts_path.open(encoding="utf-8")]
        for w in weapons:
            weapon_to_mount[w] = [
                m["mount_id"] for m in mounts if m["weapon_id"] == w
            ]

    rules = {
        "rules_version": "navspec.compatibility.v1",
        "description": (
            "Stage 4 classic armament validator が参照する互換性ルール。"
            "weapon_to_mount は navspec_armament_scraper.py と本スクリプトの出力から自動生成。"
            "caliber_min_max_by_ship_type 等は progress §15 の validator ルールに基づく保守的な初期値。"
        ),
        "weapon_to_mount": weapon_to_mount,
        "caliber_min_max_by_ship_type": {
            "battleship": {"min": 305, "max": 460},
            "battlecruiser": {"min": 280, "max": 381},
            "heavy_cruiser": {"min": 203, "max": 203},
            "light_cruiser": {"min": 127, "max": 155},
            "destroyer": {"min": 100, "max": 140},
            "coastal_defense": {"min": 76, "max": 283},
        },
        "barbette_to_beam": {
            "min_beam_over_barbette_diameter": 1.5,
            "notes": "船体ビームはバーべット直径の1.5倍以上が必要 (validator で厳格には適用しない)。"
        },
        "turret_weight_to_displacement": {
            "max_turret_weight_fraction_of_displacement": 0.06,
            "min_displacement_t_for_heaviest_turret": 25000,
            "notes": "単一砲塔重量が排水量の6%を超える、または25000t未満の船体に最重砲塔を載せる場合は警告。"
        },
        "topweight": {
            "max_full_armament_weight_fraction": 0.35,
            "notes": "全武装重量が排水量の35%を超える場合は topweight 警告。"
        },
        "magazine_volume": {
            "min_m3_per_round_default": 0.05,
            "notes": "弾薬庫容積が 1 発あたり 0.05 m3 未満の場合は magazine 警告。"
        },
        "era_availability": {
            "notes": "weapon/mount の year_introduced が艦の year より未来の場合は unavailable エラー。",
        },
        "fire_control_compatibility": {
            "notes": "fire_control_id の radar が true で、艦の year < 1938 の場合は radar_too_early 警告。",
        },
    }
    return rules


def write_compatibility(out_path: Path, weapons_path: Path, mounts_path: Path) -> int:
    rules = build_compatibility(weapons_path, mounts_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    w_count = len(rules["weapon_to_mount"])
    print(f"wrote: {out_path} (weapon_to_mount entries={w_count})")
    return w_count


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fire-control", default="data/navspec/armament/fire_control.jsonl")
    p.add_argument("--compatibility", default="data/navspec/armament/compatibility_rules.json")
    p.add_argument("--weapons", default="data/navspec/armament/weapons.jsonl")
    p.add_argument("--mounts", default="data/navspec/armament/mounts.jsonl")
    args = p.parse_args(argv)
    write_fire_control(Path(args.fire_control))
    write_compatibility(Path(args.compatibility), Path(args.weapons), Path(args.mounts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
