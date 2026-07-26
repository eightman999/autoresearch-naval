"""Stage 4 Commit C: armament retriever.

STAGE4_PROGRESS.md §2 Phase 2 (Commit C)。
ship context (year/nation/ship_type/displacement 等) を受け取り、
``data/navspec/armament/`` の DB seed から利用可能な候補を検索し、
``navspec.armament_candidate.v1`` 形式の代表 candidate を生成する。

Commit D (teacher データ生成) の「正解生成器」として使う。
classic の ``navspec_equipment.equipment_plan()`` に相当するが、
詳細装備 DB を参照する点が異なる (置換ではなく並存)。

使用法::

    from navspec_armament_retriever import retrieve_armament
    candidate = retrieve_armament(context, db, strategy="representative")
    variations = retrieve_armament_variations(context, db, n=8)  # teacher データ用
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

try:
    from navspec_armament_validator import load_db
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from navspec_armament_validator import load_db

DEFAULT_DATA_DIR = Path("data/navspec/armament")

SCHEMA_VERSION = "navspec.armament_candidate.v1"


# ----------------------------------------------------------------------------
# フィルタヘルパ
# ----------------------------------------------------------------------------

def _caliber_range_for(ship_type: str, compat: dict[str, Any]) -> tuple[int, int] | None:
    st_range = compat.get("caliber_min_max_by_ship_type", {}).get(ship_type)
    if not st_range:
        return None
    lo = st_range.get("min")
    hi = st_range.get("max")
    if lo is None or hi is None:
        return None
    return int(lo), int(hi)


def retrieve_available_weapons(
    context: dict[str, Any],
    db: dict[str, Any],
    role: str = "main",
) -> list[dict[str, Any]]:
    """context で利用可能な weapons を返す。

    フィルタ:
      - nation == context.nation
      - year_introduced <= context.year (era availability)
      - caliber が ship_type の口径範囲内
      - fictional でない
    """
    nation = context.get("nation")
    year = context.get("year")
    ship_type = context.get("ship_type")
    compat = db.get("compatibility_rules", {})

    cal_range = _caliber_range_for(ship_type, compat) if ship_type else None

    out: list[dict[str, Any]] = []
    for w in db["weapons"].values():
        if w.get("fictional"):
            continue
        if nation is not None and w.get("nation") != nation:
            continue
        if year is not None and w.get("year_introduced") and w["year_introduced"] > year:
            continue
        if cal_range is not None:
            cal = w.get("caliber_mm")
            if cal is None or not (cal_range[0] <= cal <= cal_range[1]):
                continue
        if role == "dual_purpose" and not w.get("dual_purpose"):
            continue
        out.append(w)
    return out


def retrieve_available_mounts(
    weapon_id: str,
    context: dict[str, Any],
    db: dict[str, Any],
) -> list[dict[str, Any]]:
    """指定 weapon_id の互換 mount 一覧を返す (compatibility_rules.weapon_to_mount 参照)。

    フィルタ:
      - weapon_to_mount に載っていること
      - year_introduced <= context.year (era availability)
      - nation == context.nation
      - turret_weight <= displacement * 0.20
    """
    compat = db.get("compatibility_rules", {})
    w2m = compat.get("weapon_to_mount", {})
    mount_ids = w2m.get(weapon_id, [])
    year = context.get("year")
    nation = context.get("nation")
    out = []
    for mid in mount_ids:
        m = db["mounts"].get(mid)
        if not m:
            continue
        # 砲塔の就役年。DB に入っていないものは年で落とさない（判断材料が無いため）
        mi = m.get("year_introduced")
        if year is not None and mi and mi > year:
            continue
        if nation is not None and m.get("nation") and m.get("nation") != nation:
            continue
        # turret_weight が大きすぎる場合は除外 (displacement * 0.20 を上限)
        disp = context.get("displacement_t")
        tw = m.get("turret_weight_t")
        if disp and tw and tw > disp * 0.20:
            continue
        out.append(m)
    return out


def retrieve_available_fire_control(
    context: dict[str, Any],
    db: dict[str, Any],
) -> list[dict[str, Any]]:
    """context で利用可能な射撃指揮装置を返す。

    フィルタ:
      - nation == context.nation
      - year_introduced <= context.year
      - radar=true の場合、year >= 1938
    """
    nation = context.get("nation")
    year = context.get("year")
    out = []
    for fc in db["fire_control"].values():
        if fc.get("fictional"):
            continue
        if nation is not None and fc.get("nation") != nation:
            continue
        if year is not None and fc.get("year_introduced") and fc["year_introduced"] > year:
            continue
        if fc.get("radar") and year is not None and year < 1938:
            continue
        out.append(fc)
    return out


# ----------------------------------------------------------------------------
# 代表選択・ mount_count / positions 推定
# ----------------------------------------------------------------------------

def select_representative_weapon(
    weapons: list[dict[str, Any]],
    context: dict[str, Any],
    strategy: str = "representative",
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    """weapons から代表1件を選ぶ。

    strategy:
      - "representative": 最大口径 (主力艦らしい選択)
      - "mid_caliber": 中央値に近い口径
      - "random": ランダム選択
    """
    if not weapons:
        return None
    rng = rng or random.Random()
    if strategy == "random":
        return rng.choice(weapons)
    if strategy == "mid_caliber":
        sorted_w = sorted(weapons, key=lambda w: w.get("caliber_mm", 0))
        return sorted_w[len(sorted_w) // 2]
    # representative: 最大口径
    return max(weapons, key=lambda w: w.get("caliber_mm", 0))


def estimate_mount_count(
    weapon: dict[str, Any],
    context: dict[str, Any],
) -> int:
    """口径・艦種・排水量から砲塔数を推定。

    大口径ほど少なめ、小型艦・大排水量ほど多め。
    """
    ship_type = context.get("ship_type", "")
    disp = context.get("displacement_t") or 0
    cal = weapon.get("caliber_mm", 0) or 0

    # 主力艦 (battleship/battlecruiser)
    if ship_type in {"battleship", "battlecruiser", "fast_battleship"}:
        if cal >= 380:
            return 3 if disp < 50000 else 3
        if cal >= 305:
            return 4 if disp >= 25000 else 3
        return 4
    # 巡洋艦
    if "cruiser" in ship_type:
        if cal >= 203:
            return 3 if disp >= 10000 else 2
        return 4 if disp >= 8000 else 3
    # 駆逐艦
    if "destroyer" in ship_type or ship_type in {"frigate"}:
        if cal >= 127:
            return 2 if disp < 2000 else 3
        return 3 if disp < 1500 else 4
    # その他 / coastal
    if cal >= 305:
        return 2
    if cal >= 152:
        return 3
    return 4


def generate_positions(
    mount_count: int,
    rng: random.Random | None = None,
) -> list[str]:
    """mount_count から標準的な positions を生成。

    パターン (progress schema の A/B/P/Q/X/Y/C/Z):
      1: ["A"]
      2: ["A", "X"]
      3: ["A", "B", "X"] or ["A", "X", "Y"]
      4: ["A", "B", "X", "Y"]
    """
    rng = rng or random.Random()
    if mount_count <= 0:
        return []
    if mount_count == 1:
        return ["A"]
    if mount_count == 2:
        return ["A", "X"]
    if mount_count == 3:
        # 前部寄せ (A,B,X) と 後部寄せ (A,X,Y) をランダムに
        return rng.choice([["A", "B", "X"], ["A", "X", "Y"]])
    if mount_count == 4:
        return ["A", "B", "X", "Y"]
    # 5 以上は前後・中央に一意な位置を割り当てる。
    # 同じ P/Q を反復すると validator の firing_arc_conflict になる。
    position_order = ["A", "B", "X", "Y", "P", "Q", "C", "Z"]
    return position_order[:mount_count]


def select_fire_control(
    context: dict[str, Any],
    db: dict[str, Any],
    weapon: dict[str, Any],
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    """context で利用可能な fire_control から代表1件を選ぶ。

    主力艦・大口径砲には main_battery_director、DP砲には dual_purpose_director を優先。
    """
    rng = rng or random.Random()
    fcs = retrieve_available_fire_control(context, db)
    if not fcs:
        return None
    desired_type = "main_battery_director"
    if weapon.get("dual_purpose"):
        desired_type = "dual_purpose_director"
    typed = [fc for fc in fcs if fc.get("system_type") == desired_type]
    if typed:
        return rng.choice(typed)
    # fallback: 利用可能なものから
    return rng.choice(fcs)


# ----------------------------------------------------------------------------
# candidate 構築
# ----------------------------------------------------------------------------

def build_candidate(
    weapon: dict[str, Any],
    mount: dict[str, Any],
    mount_count: int,
    positions: list[str],
    fire_control: dict[str, Any] | None,
    battery_id: str = "main_1",
    ammo_per_gun: int = 100,
) -> dict[str, Any]:
    """armament_candidate.v1 形式の candidate を構築する。"""
    barrels_per_mount = mount.get("barrels_per_mount", 1) or 1
    total_barrels = mount_count * barrels_per_mount
    battery: dict[str, Any] = {
        "battery_id": battery_id,
        "weapon_id": weapon["weapon_id"],
        "mount_id": mount["mount_id"],
        "mount_count": mount_count,
        "barrels_per_mount": barrels_per_mount,
        "total_barrels": total_barrels,
        "positions": positions,
        "ammo_per_gun": ammo_per_gun,
    }
    if fire_control:
        battery["fire_control_id"] = fire_control["fire_control_id"]
    return {
        "schema_version": SCHEMA_VERSION,
        "main_batteries": [battery],
        "design_rationale": _rationale(weapon, mount, mount_count),
        "confidence": 0.85,
    }


def _rationale(weapon: dict[str, Any], mount: dict[str, Any], mount_count: int) -> str:
    return (
        f"{mount_count} x {mount.get('mount_type', '?')} mount of "
        f"{weapon.get('caliber_mm', '?')}mm {weapon.get('name', '?')} "
        f"({weapon.get('nation', '?')}, {weapon.get('year_introduced', '?')})"
    )


# ----------------------------------------------------------------------------
# 統合エントリポイント
# ----------------------------------------------------------------------------

def retrieve_armament(
    context: dict[str, Any],
    db: dict[str, Any],
    strategy: str = "representative",
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    """context から代表 armament candidate を1件生成する。

    strategy: "representative" / "mid_caliber" / "random"
    戻り値: armament_candidate.v1 形式、または候補が無ければ None
    """
    rng = rng or random.Random()
    weapons = retrieve_available_weapons(context, db)
    if not weapons:
        return None
    weapon = select_representative_weapon(weapons, context, strategy, rng)
    if not weapon:
        return None
    mounts = retrieve_available_mounts(weapon["weapon_id"], context, db)
    if not mounts:
        return None
    mount = mounts[0]
    mount_count = estimate_mount_count(weapon, context)
    positions = generate_positions(mount_count, rng)
    fc = select_fire_control(context, db, weapon, rng)
    return build_candidate(weapon, mount, mount_count, positions, fc)


def retrieve_armament_variations(
    context: dict[str, Any],
    db: dict[str, Any],
    n: int = 8,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """teacher データ生成用: 同一 context から最大 n 件のバリエーション candidate を生成。

    異なる weapon/mount_count/positions を組み合わせて多様性を出す。
    重複は除外する。
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    weapons = retrieve_available_weapons(context, db)
    if not weapons:
        return []

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    attempts = 0
    max_attempts = n * 6
    while len(out) < n and attempts < max_attempts:
        attempts += 1
        weapon = rng.choice(weapons)
        mounts = retrieve_available_mounts(weapon["weapon_id"], context, db)
        if not mounts:
            continue
        mount = mounts[0]
        # mount_count をばらつかせる
        base_count = estimate_mount_count(weapon, context)
        mount_count = max(1, min(6, base_count + rng.choice([-1, 0, 0, 1])))
        positions = generate_positions(mount_count, rng)
        fc = select_fire_control(context, db, weapon, rng)
        candidate = build_candidate(weapon, mount, mount_count, positions, fc)
        # 重複除外キー: (weapon_id, mount_count)
        key = f"{weapon['weapon_id']}:{mount_count}"
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--nation", required=True)
    p.add_argument("--ship-type", required=True)
    p.add_argument("--displacement-t", type=float)
    p.add_argument("--beam-m", type=float)
    p.add_argument("--strategy", default="representative",
                   choices=["representative", "mid_caliber", "random"])
    p.add_argument("--variations", type=int, default=0,
                   help="0以外なら n 件のバリエーションを生成")
    p.add_argument("--seed", type=int)
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = p.parse_args(argv)

    context = {
        "year": args.year,
        "nation": args.nation,
        "ship_type": args.ship_type,
        "displacement_t": args.displacement_t,
        "beam_m": args.beam_m,
    }
    db = load_db(args.data_dir)

    if args.variations > 0:
        cands = retrieve_armament_variations(context, db, n=args.variations, seed=args.seed)
        for c in cands:
            print(json.dumps(c, ensure_ascii=False))
        return 0

    rng = random.Random(args.seed) if args.seed is not None else None
    candidate = retrieve_armament(context, db, strategy=args.strategy, rng=rng)
    if not candidate:
        print(json.dumps({"status": "no_candidate", "context": context}, ensure_ascii=False))
        return 1
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
