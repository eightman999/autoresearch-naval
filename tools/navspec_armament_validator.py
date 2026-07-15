"""Stage 4 Commit B: classic armament validator.

STAGE4_PROGRESS.md §15 のルールと §16 の reason code (14件) に基づき、
``navspec.armament_candidate.v1`` 形式の candidate を検証する。

入力:
- ``candidate``: armament_candidate.v1 (main_batteries[] を持つ)
- ``context``: {ship_type, year, nation, displacement_t, beam_m}
- ``db``: weapons/mounts/fire_control/compatibility_rules を dict 化したもの

出力:
- ``{"status": "ok" | "impossible", "issues": [{"severity","code","message","battery_id"}]}``

reason code は progress §16 の14件。REASON_CODES 正典 (navspec_lora_stage2_schema.py)
とは別名前空間 ("armament.*") であり、本 validator が独自に管理する。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# reason code (progress §16, 全14件)
# ----------------------------------------------------------------------------

ARMAMENT_REASON_CODES: list[str] = [
    "armament.weapon_unavailable_for_era",
    "armament.weapon_unavailable_for_nation",
    "armament.mount_unavailable_for_era",
    "armament.mount_unavailable_for_nation",
    "armament.weapon_mount_incompatible",
    "armament.barbette_too_large_for_beam",
    "armament.turret_weight_exceeds_hull_limit",
    "armament.magazine_volume_insufficient",
    "armament.topweight_limit_exceeded",
    "armament.firing_arc_conflict",
    "armament.blast_interference",
    "armament.battery_layout_conflict",
    "armament.armament_displacement_conflict",
    "armament.fire_control_unavailable",
]

# positions の意味 (progress schema 記載):
#   A, B = 前部 (superfiring, Aが前・Bがその後ろ上位)
#   P, Q = 中部
#   X, Y = 後部 (Xが前・Yが後ろ上位)
#   C, Z = 中線 (1位置のみ)
VALID_POSITIONS = {"A", "B", "P", "Q", "X", "Y", "C", "Z"}
FORE_POSITIONS = {"A", "B"}
AFT_POSITIONS = {"X", "Y"}
MID_POSITIONS = {"P", "Q"}
CENTERLINE_POSITIONS = {"C", "Z"}

# Default data dir
DEFAULT_DATA_DIR = Path("data/navspec/armament")

# 制約の既定値 (compatibility_rules.json に無い場合のフォールバック)
DEFAULT_RULES = {
    "max_turret_weight_fraction_of_displacement": 0.06,
    "min_displacement_t_for_heaviest_turret": 25000,
    "max_full_armament_weight_fraction": 0.35,
    "min_m3_per_round_default": 0.05,
    "min_beam_over_barbette_diameter": 1.4,
}


# ----------------------------------------------------------------------------
# DB load
# ----------------------------------------------------------------------------

def load_db(data_dir: Path | str | None = None) -> dict[str, Any]:
    """data/navspec/armament/ から weapons/mounts/fire_control/compatibility_rules を読み込む。"""
    d = Path(data_dir) if data_dir else DEFAULT_DATA_DIR

    def _load_jsonl(name: str, key: str) -> dict[str, dict]:
        p = d / name
        if not p.exists():
            return {}
        return {r[key]: r for r in (json.loads(l) for l in p.open(encoding="utf-8") if l.strip())}

    weapons = _load_jsonl("weapons.jsonl", "weapon_id")
    mounts = _load_jsonl("mounts.jsonl", "mount_id")
    fire_control = _load_jsonl("fire_control.jsonl", "fire_control_id")
    ammunition = _load_jsonl("ammunition.jsonl", "ammo_id")

    compat_path = d / "compatibility_rules.json"
    compatibility = json.loads(compat_path.read_text(encoding="utf-8")) if compat_path.exists() else {}

    return {
        "weapons": weapons,
        "mounts": mounts,
        "fire_control": fire_control,
        "ammunition": ammunition,
        "compatibility_rules": compatibility,
    }


# ----------------------------------------------------------------------------
# 集約値導出 (progress §3 メモ)
# ----------------------------------------------------------------------------

def aggregate_main_gun_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    """candidate の main_batteries[] から集約 armament/equipment フィールドを導出。

    main_gun_caliber_mm = 最大口径の weapon の caliber_mm
    main_gun_count = sum(mount_count * barrels_per_mount)
    main_gun_mount_count = sum(mount_count)
    """
    batteries = candidate.get("main_batteries", [])
    if not batteries:
        return {"main_gun_caliber_mm": None, "main_gun_count": 0, "main_gun_mount_count": 0}

    max_caliber = None
    total_barrels = 0
    total_mounts = 0
    for b in batteries:
        bc = b.get("barrels_per_mount", 0) or 0
        mc = b.get("mount_count", 0) or 0
        total_barrels += bc * mc
        total_mounts += mc
    return {
        "main_gun_caliber_mm": max_caliber,
        "main_gun_count": total_barrels,
        "main_gun_mount_count": total_mounts,
    }


def aggregate_main_caliber_with_db(candidate: dict[str, Any], db: dict[str, Any]) -> dict[str, Any]:
    """DB を参照して main_gun_caliber_mm を正確に導出する版。"""
    agg = aggregate_main_gun_fields(candidate)
    max_cal = 0
    found = False
    for b in candidate.get("main_batteries", []):
        w = db["weapons"].get(b.get("weapon_id"))
        if w and w.get("caliber_mm"):
            max_cal = max(max_cal, w["caliber_mm"])
            found = True
    agg["main_gun_caliber_mm"] = max_cal if found else None
    return agg


# ----------------------------------------------------------------------------
# validator 本体
# ----------------------------------------------------------------------------

def _issue(severity: str, code: str, message: str, battery_id: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    if battery_id:
        d["battery_id"] = battery_id
    return d


def _get_rule(rules: dict[str, Any], key: str, default: float) -> float:
    """compatibility_rules から閾値を取得するヘルパ。

    トップレベルの数値、またはネストした dict (例: barbette_to_beam.min_beam_over_barbette_diameter)
    のいずれかから値を探す。
    """
    val = rules.get(key)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    for section in rules.values():
        if isinstance(section, dict):
            inner = section.get(key)
            if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                return float(inner)
    return default


def _estimate_barbette_diameter_m(mount: dict[str, Any]) -> float | None:
    """barbette_diameter_m が null の場合、mount_type と caliber から推定。

    大和型 460mm triple の実バーベット直径約13m を基準に、口径(m)*28 を
    ベースに barrels 数で補正する経験則。
    """
    bd = mount.get("barbette_diameter_m")
    if bd is not None:
        return float(bd)
    weapon_cal = mount.get("_weapon_caliber_mm")
    if not weapon_cal:
        return None
    barrels = mount.get("barrels_per_mount", 1) or 1
    base = (weapon_cal / 1000.0) * 28.0
    factor = {1: 0.70, 2: 0.85, 3: 1.00, 4: 1.15}.get(barrels, 1.0)
    return round(base * factor, 2)


def _estimate_magazine_volume_m3(
    battery: dict[str, Any],
    weapon: dict[str, Any],
    rules: dict[str, Any],
) -> float | None:
    """magazine_volume が必要量を満たすか推定検証。"""
    ammo = battery.get("ammo_per_gun")
    if not ammo:
        return None
    barrels = battery.get("barrels_per_mount", 0) or 0
    mount_count = battery.get("mount_count", 0) or 0
    rounds = ammo * barrels * mount_count
    min_per_round = _get_rule(rules, "min_m3_per_round_default", 0.05)
    return round(rounds * min_per_round, 2)


def validate_armament(
    candidate: dict[str, Any],
    context: dict[str, Any],
    db: dict[str, Any],
) -> dict[str, Any]:
    """candidate (armament_candidate.v1) を検証する。

    Returns:
        {"status": "ok"|"impossible", "issues": [...], "aggregate": {...}}
    """
    issues: list[dict[str, Any]] = []

    schema_version = candidate.get("schema_version")
    if schema_version != "navspec.armament_candidate.v1":
        issues.append(_issue(
            "error", "armament.battery_layout_conflict",
            f"schema_version {schema_version!r} != 'navspec.armament_candidate.v1'",
        ))

    batteries = candidate.get("main_batteries", [])
    if not batteries:
        issues.append(_issue(
            "error", "armament.battery_layout_conflict",
            "main_batteries is empty",
        ))
        return {"status": "impossible", "issues": issues, "aggregate": {}}

    weapons = db["weapons"]
    mounts = db["mounts"]
    fire_control = db["fire_control"]
    compat = db.get("compatibility_rules", {})
    w2m = compat.get("weapon_to_mount", {})

    year = context.get("year")
    nation = context.get("nation")
    ship_type = context.get("ship_type")
    disp_t = context.get("displacement_t")
    beam_m = context.get("beam_m")

    total_turret_weight_t = 0.0
    total_barrels_all = 0
    centerline_positions_used: set[str] = set()
    # 全 battery を通じた位置衝突検出用
    all_positions_by_battery: list[tuple[str, list[str]]] = []

    for battery in batteries:
        bid = battery.get("battery_id", "?")
        weapon_id = battery.get("weapon_id")
        mount_id = battery.get("mount_id")
        mount_count = battery.get("mount_count", 0) or 0
        barrels_per_mount = battery.get("barrels_per_mount", 0) or 0
        total_barrels = battery.get("total_barrels", 0) or 0
        positions = battery.get("positions", [])

        # --- weapon 参照整合性 ---
        weapon = weapons.get(weapon_id) if weapon_id else None
        if not weapon:
            issues.append(_issue(
                "error", "armament.weapon_mount_incompatible",
                f"weapon_id {weapon_id!r} not found in weapons.jsonl",
                bid,
            ))

        # --- mount 参照整合性 ---
        mount = mounts.get(mount_id) if mount_id else None
        if not mount:
            issues.append(_issue(
                "error", "armament.weapon_mount_incompatible",
                f"mount_id {mount_id!r} not found in mounts.jsonl",
                bid,
            ))
        if mount and weapon:
            # weapon の caliber を mount の推定用にメモ
            mount = {**mount, "_weapon_caliber_mm": weapon.get("caliber_mm")}

        # --- total_barrels 整合性 ---
        expected_barrels = mount_count * barrels_per_mount
        if total_barrels != expected_barrels:
            issues.append(_issue(
                "error", "armament.battery_layout_conflict",
                f"total_barrels {total_barrels} != mount_count {mount_count} * barrels_per_mount {barrels_per_mount}",
                bid,
            ))

        # --- era availability (weapon) ---
        if weapon and year is not None:
            wi = weapon.get("year_introduced")
            if wi and wi > year:
                issues.append(_issue(
                    "error", "armament.weapon_unavailable_for_era",
                    f"weapon {weapon_id} introduced {wi} > year {year}",
                    bid,
                ))

        # --- nation availability (weapon) ---
        if weapon and nation is not None and weapon.get("nation") != nation:
            issues.append(_issue(
                "error", "armament.weapon_unavailable_for_nation",
                f"weapon {weapon_id} nation {weapon.get('nation')} != context nation {nation}",
                bid,
            ))

        # --- mount era/nation (mount.year_introduced は現状 null のため weapon に準ずる) ---
        if mount and year is not None and weapon:
            mi = mount.get("year_introduced") or weapon.get("year_introduced")
            if mi and mi > year:
                issues.append(_issue(
                    "error", "armament.mount_unavailable_for_era",
                    f"mount {mount_id} introduced {mi} > year {year}",
                    bid,
                ))
        if mount and nation is not None and mount.get("nation") != nation:
            issues.append(_issue(
                "error", "armament.mount_unavailable_for_nation",
                f"mount {mount_id} nation {mount.get('nation')} != context nation {nation}",
                bid,
            ))

        # --- weapon/mount compatibility (compatibility_rules.weapon_to_mount) ---
        if weapon_id and mount_id:
            allowed = w2m.get(weapon_id, [])
            if allowed and mount_id not in allowed:
                issues.append(_issue(
                    "error", "armament.weapon_mount_incompatible",
                    f"mount {mount_id} not in compatibility_rules.weapon_to_mount[{weapon_id}]",
                    bid,
                ))

        # --- turret weight vs displacement ---
        if mount and disp_t:
            tw = mount.get("turret_weight_t")
            if tw:
                total_turret_weight_t += tw * mount_count
                # 単一砲塔が大きすぎる場合 (排水量比)
                max_frac = _get_rule(compat, "max_turret_weight_fraction_of_displacement", 0.06)
                if tw > disp_t * max_frac * 2.5:  # 単砲塔で15%超
                    issues.append(_issue(
                        "error", "armament.turret_weight_exceeds_hull_limit",
                        f"single turret weight {tw}t exceeds {max_frac*2.5*100:.0f}% of displacement {disp_t}t",
                        bid,
                    ))

        # --- barbette vs beam ---
        if mount and beam_m:
            bd = _estimate_barbette_diameter_m(mount)
            if bd:
                min_ratio = _get_rule(compat, "min_beam_over_barbette_diameter", 1.5)
                # wing 配置の場合、beam は barbette 直径×2 以上必要
                if any(p in (FORE_POSITIONS | AFT_POSITIONS | MID_POSITIONS) for p in positions):
                    if beam_m < bd * 2 * min_ratio:
                        issues.append(_issue(
                            "warn", "armament.barbette_too_large_for_beam",
                            f"beam {beam_m}m < 2 x barbette {bd}m x {min_ratio} for wing mount",
                            bid,
                        ))

        # --- positions の妥当性 ---
        if positions:
            # 数が mount_count と整合するか (中線配置 C/Z は1 mount 1 position)
            if len(positions) != mount_count:
                issues.append(_issue(
                    "warn", "armament.battery_layout_conflict",
                    f"positions count {len(positions)} != mount_count {mount_count}",
                    bid,
                ))
            # 無効な position
            invalid = [p for p in positions if p not in VALID_POSITIONS]
            if invalid:
                issues.append(_issue(
                    "error", "armament.firing_arc_conflict",
                    f"invalid positions {invalid}; valid={sorted(VALID_POSITIONS)}",
                    bid,
                ))
            # 重複
            if len(positions) != len(set(positions)):
                dup = [p for p in positions if positions.count(p) > 1]
                issues.append(_issue(
                    "error", "armament.firing_arc_conflict",
                    f"duplicate positions {dup}",
                    bid,
                ))
            # C/Z と他を混在 (1 battery 内で中線と wing は矛盾)
            has_center = any(p in CENTERLINE_POSITIONS for p in positions)
            has_wing = any(p in (FORE_POSITIONS | AFT_POSITIONS | MID_POSITIONS) for p in positions)
            if has_center and has_wing and mount_count > 1:
                issues.append(_issue(
                    "warn", "armament.battery_layout_conflict",
                    f"mixed centerline {CENTERLINE_POSITIONS} and wing positions in one battery",
                    bid,
                ))
            # blast interference: A と B は superfiring で OK、だが A 単独で mount_count=2 は不可
            if mount_count >= 2 and len(set(positions)) == 1:
                issues.append(_issue(
                    "warn", "armament.blast_interference",
                    f"multiple mounts ({mount_count}) on same position {positions[0]} causes blast interference",
                    bid,
                ))
            # 中線位置の衝突 (全 battery 通す)
            for p in positions:
                if p in CENTERLINE_POSITIONS:
                    if p in centerline_positions_used:
                        issues.append(_issue(
                            "error", "armament.battery_layout_conflict",
                            f"centerline position {p} used by multiple batteries",
                            bid,
                        ))
                    centerline_positions_used.add(p)
            all_positions_by_battery.append((bid, positions))

        # --- magazine volume ---
        if weapon:
            req_vol = _estimate_magazine_volume_m3(battery, weapon, compat)
            # magazine_volume_m3 フィールドが candidate 側に無いため、要求量が異常に大きい場合のみ警告。
            # 閾値は排水量の5% (通常の主力艦の弾薬庫は排水量の1-3%程度)。
            if req_vol and disp_t and req_vol > disp_t * 0.05:
                issues.append(_issue(
                    "warn", "armament.magazine_volume_insufficient",
                    f"required magazine volume ~{req_vol}m3 exceeds 5% of displacement {disp_t}t",
                    bid,
                ))

        total_barrels_all += total_barrels

        # --- fire_control ---
        fc_id = battery.get("fire_control_id")
        if fc_id:
            fc = fire_control.get(fc_id)
            if not fc:
                issues.append(_issue(
                    "error", "armament.fire_control_unavailable",
                    f"fire_control_id {fc_id!r} not found in fire_control.jsonl",
                    bid,
                ))
            elif fc.get("radar") and year is not None and year < 1938:
                issues.append(_issue(
                    "warn", "armament.fire_control_unavailable",
                    f"fire_control {fc_id} has radar=true but year {year} < 1938",
                    bid,
                ))

    # --- 全 battery 通しの topweight ---
    if disp_t and total_turret_weight_t > 0:
        max_topweight = _get_rule(compat, "max_full_armament_weight_fraction", 0.35)
        # 砲塔重量だけで 35% 超 (副砲・装甲を含まないので閾値を緩めに)
        if total_turret_weight_t > disp_t * max_topweight:
            issues.append(_issue(
                "warn", "armament.topweight_limit_exceeded",
                f"total turret weight {total_turret_weight_t:.0f}t > {max_topweight*100:.0f}% of displacement {disp_t}t",
            ))
        # armament_displacement_conflict: 砲塔だけで排水量の半分を超える
        if total_turret_weight_t > disp_t * 0.5:
            issues.append(_issue(
                "error", "armament.armament_displacement_conflict",
                f"total turret weight {total_turret_weight_t:.0f}t exceeds 50% of displacement {disp_t}t",
            ))

    # --- ship_type と口径の整合 (caliber/ship_type) ---
    if ship_type:
        caliber_ranges = compat.get("caliber_min_max_by_ship_type", {})
        st_range = caliber_ranges.get(ship_type)
        if st_range and weapons:
            lo, hi = st_range.get("min"), st_range.get("max")
            for b in batteries:
                w = weapons.get(b.get("weapon_id"))
                if w and w.get("caliber_mm"):
                    cal = w["caliber_mm"]
                    if (lo and cal < lo) or (hi and cal > hi):
                        issues.append(_issue(
                            "warn", "armament.battery_layout_conflict",
                            f"weapon {b.get('weapon_id')} caliber {cal}mm outside {ship_type} range [{lo},{hi}]",
                            b.get("battery_id", "?"),
                        ))

    aggregate = aggregate_main_caliber_with_db(candidate, db)
    has_error = any(i["severity"] == "error" for i in issues)
    status = "impossible" if has_error else "ok"
    return {"status": status, "issues": issues, "aggregate": aggregate}


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("candidate", help="path to armament_candidate.v1 JSON file")
    p.add_argument("--year", type=int)
    p.add_argument("--nation")
    p.add_argument("--ship-type")
    p.add_argument("--displacement-t", type=float)
    p.add_argument("--beam-m", type=float)
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    args = p.parse_args(argv)

    candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
    context = {
        "year": args.year,
        "nation": args.nation,
        "ship_type": args.ship_type,
        "displacement_t": args.displacement_t,
        "beam_m": args.beam_m,
    }
    db = load_db(args.data_dir)
    result = validate_armament(candidate, context, db)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
