"""Stage 4 Commit A: armament DB seed の簡易検証.

``data/navspec/armament/`` 配下の5ファイル (weapons/mounts/ammunition/fire_control/
compatibility_rules) について、progress §12 の必須フィールドと参照整合性を検証する。

スキーマ駆動ではなくヒューリスティックな検証であり、classic validator
(``tools/navspec_armament_validator.py``, Commit B で実装予定) の前段チェック。

使用法::

    uv run python tools/navspec_armament_validate_seed.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DATA_DIR = Path("data/navspec/armament")

WEAPON_REQUIRED = [
    "weapon_id", "name", "nation", "caliber_mm", "barrel_length_calibers",
    "gun_weight_t", "shell_weight_kg", "muzzle_velocity_mps",
    "rate_of_fire_rpm", "max_range_m", "loading_type", "source",
]
MOUNT_REQUIRED = [
    "mount_id", "weapon_id", "name", "nation", "barrels_per_mount",
    "mount_type", "turret_weight_t", "source",
]
AMMO_REQUIRED = [
    "ammo_id", "weapon_id", "name", "type", "shell_weight_kg", "source",
]
FC_REQUIRED = [
    "fire_control_id", "name", "nation", "year_introduced",
    "system_type", "radar", "technology_tier", "source",
]

VALID_NATIONS = {"JP", "US", "UK", "DE", "FR", "IT"}
VALID_MOUNT_TYPES = {"single", "twin", "triple", "quadruple"}
VALID_LOADING_TYPES = {"bag", "cartridge", "qf_cartridge", "fixed", "unknown"}


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def _check_required(records: list[dict], required: list[str], kind: str) -> list[str]:
    errs: list[str] = []
    for r in records:
        rid = r.get(f"{kind}_id", "<no_id>")
        for f in required:
            if r.get(f) is None and f not in r:
                errs.append(f"{kind} {rid}: missing required field '{f}'")
    return errs


def validate(data_dir: Path) -> int:
    errs: list[str] = []
    warns: list[str] = []

    weapons = _load_jsonl(data_dir / "weapons.jsonl")
    mounts = _load_jsonl(data_dir / "mounts.jsonl")
    ammos = _load_jsonl(data_dir / "ammunition.jsonl")
    fcs = _load_jsonl(data_dir / "fire_control.jsonl")
    compat_path = data_dir / "compatibility_rules.json"
    compat = json.loads(compat_path.read_text(encoding="utf-8")) if compat_path.exists() else {}

    print(f"counts: weapons={len(weapons)} mounts={len(mounts)} "
          f"ammos={len(ammos)} fire_control={len(fcs)}")

    # 必須フィールド
    errs += _check_required(weapons, WEAPON_REQUIRED, "weapon")
    errs += _check_required(mounts, MOUNT_REQUIRED, "mount")
    errs += _check_required(ammos, AMMO_REQUIRED, "ammo")
    errs += _check_required(fcs, FC_REQUIRED, "fire_control")

    # nation / enum
    for r in weapons:
        if r.get("nation") not in VALID_NATIONS:
            errs.append(f"weapon {r.get('weapon_id')}: invalid nation {r.get('nation')!r}")
        if r.get("loading_type") and r["loading_type"] not in VALID_LOADING_TYPES:
            warns.append(f"weapon {r.get('weapon_id')}: unusual loading_type {r['loading_type']!r}")
        if r.get("caliber_mm") is not None and not (50 <= r["caliber_mm"] <= 510):
            warns.append(f"weapon {r.get('weapon_id')}: caliber_mm {r['caliber_mm']} outside 50-510")

    for r in mounts:
        if r.get("mount_type") and r["mount_type"] not in VALID_MOUNT_TYPES:
            errs.append(f"mount {r.get('mount_id')}: invalid mount_type {r['mount_type']!r}")
        bp = r.get("barrels_per_mount")
        mt = r.get("mount_type")
        if bp and mt:
            expected = {"single": 1, "twin": 2, "triple": 3, "quadruple": 4}.get(mt)
            if expected and bp != expected:
                errs.append(f"mount {r.get('mount_id')}: barrels_per_mount {bp} != {expected} for {mt}")

    # 参照整合性
    weapon_ids = {w["weapon_id"] for w in weapons}
    mount_ids = {m["mount_id"] for m in mounts}

    for m in mounts:
        if m["weapon_id"] not in weapon_ids:
            errs.append(f"mount {m['mount_id']}: weapon_id {m['weapon_id']} not in weapons.jsonl")
    for a in ammos:
        if a["weapon_id"] not in weapon_ids:
            errs.append(f"ammo {a['ammo_id']}: weapon_id {a['weapon_id']} not in weapons.jsonl")

    # compatibility_rules の weapon_to_mount 整合性
    w2m = compat.get("weapon_to_mount", {})
    for wid, mlist in w2m.items():
        if wid not in weapon_ids:
            warns.append(f"compatibility weapon_to_mount: weapon_id {wid} not in weapons.jsonl")
        for mid in mlist:
            if mid not in mount_ids:
                errs.append(f"compatibility weapon_to_mount[{wid}]: mount_id {mid} not in mounts.jsonl")

    # レポート
    if warns:
        print(f"\nWARNINGS ({len(warns)}):")
        for w in warns:
            print(f"  WARN: {w}")
    if errs:
        print(f"\nERRORS ({len(errs)}):")
        for e in errs:
            print(f"  ERR:  {e}")
        return 1

    print("\nOK: all armament seed files passed validation.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default=str(DATA_DIR))
    args = p.parse_args(argv)
    return validate(Path(args.data_dir))


if __name__ == "__main__":
    sys.exit(main())
