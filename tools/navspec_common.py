"""Shared helpers for naval specification dataset generation."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from navspec_naming import validate_naming  # direct run: tools/ on sys.path
except ImportError:
    from tools.navspec_naming import validate_naming  # package import from repo root

try:
    from navspec_equipment import validate_equipment  # direct run: tools/ on sys.path
except ImportError:
    from tools.navspec_equipment import validate_equipment  # package import from repo root

try:
    from navspec_shiptypes import lb_ratio_band_for  # direct run: tools/ on sys.path
except ImportError:
    from tools.navspec_shiptypes import lb_ratio_band_for  # package import from repo root


DEFAULT_SSW_ROOT = Path("/Users/eightman/dev/hoi4/SSW_mod")
DEFAULT_NSDB = Path(__file__).resolve().parent.parent / "nsdb" / "NSDB_new.db"


NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
POWER_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:shp|hp|ihp)?", re.I)
CALIBER_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*/\s*(\d{1,3})")
COUNT_MOUNT_RE = re.compile(r"(?:(\d+)\s*x\s*(\d+)|(\d+))\s*-\s*(\d{2,3}(?:\.\d+)?)\s*/\s*(\d{1,3})", re.I)
BELT_RE = re.compile(r"(?:belt|side)\s*:?\s*(\d{2,4})\s*(?:mm\b|[-–]|\+)", re.I)


def first_number(text: str | None) -> float | None:
    if not text:
        return None
    text = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', text)
    match = NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def max_number(text: str | None) -> float | None:
    if not text:
        return None
    text = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', text)
    values: list[float] = []
    for match in NUMBER_RE.finditer(text):
        try:
            values.append(float(match.group(0).replace(",", "")))
        except ValueError:
            pass
    return max(values) if values else None


def parse_length(text: str | None) -> float | None:
    """Prefer overall length when labels are present; otherwise use the max."""
    if not text:
        return None
    text = re.sub(r'(\d)\s*\.\s*(\d)', r'\1.\2', text)
    overall = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(?:oa|overall)", text, re.I)
    if overall:
        val = float(overall.group(1).replace(",", ""))
        if val >= 10:
            return val
    return max_number(text)


def parse_power(text: str | None) -> float | None:
    if not text:
        return None
    values: list[float] = []
    for match in POWER_RE.finditer(text):
        try:
            values.append(float(match.group(1).replace(",", "")))
        except ValueError:
            pass
    return max(values) if values else None


def parse_year(*texts: str | None) -> int | None:
    for text in texts:
        if not text:
            continue
        years = [int(y) for y in re.findall(r"\b(18\d{2}|19\d{2}|20\d{2})\b", text)]
        if years:
            return min(years)
    return None


def parse_belt_mm(armour: str | None, sections: str | None = None) -> float | None:
    candidates: list[int] = []
    for text in [sections, armour]:
        if not text:
            continue
        for match in BELT_RE.finditer(text):
            raw = match.group(1) or match.group(2)
            if raw:
                candidates.append(int(raw))
    return float(max(candidates)) if candidates else None


def parse_armament_summary(text: str | None) -> dict[str, Any]:
    if not text:
        return {"raw": "", "main_caliber_mm": None, "main_barrels": None, "mounts": []}
    text = re.sub(r'(\d)\s+(\d{2})\s*/', r'\1\2/', text)
    mounts: list[dict[str, Any]] = []
    main_caliber = None
    main_barrels = None
    for match in COUNT_MOUNT_RE.finditer(text):
        mount_count = int(match.group(1) or 1)
        barrels_per_mount = int(match.group(2) or match.group(3) or 1)
        caliber_mm = float(match.group(4))
        caliber_length = int(match.group(5))
        barrels = mount_count * barrels_per_mount
        mounts.append(
            {
                "mount_count": mount_count,
                "barrels_per_mount": barrels_per_mount,
                "barrels": barrels,
                "caliber_mm": caliber_mm,
                "caliber_length": caliber_length,
            }
        )
        if main_caliber is None or caliber_mm > main_caliber:
            main_caliber = caliber_mm
            main_barrels = barrels
    if not mounts:
        calibers = [float(a) for a, _ in CALIBER_RE.findall(text)]
        if calibers:
            main_caliber = max(calibers)
    return {
        "raw": text,
        "main_caliber_mm": main_caliber,
        "main_barrels": main_barrels,
        "mounts": mounts,
    }


def normalize_source_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    url = url.replace("https://www.navypedia.org/", "https://myownonpmirror.com/")
    url = url.replace("http://www.navypedia.org/", "https://myownonpmirror.com/")
    if url.endswith(".htm"):
        url = url[:-4] + ".html"
    return url


def ship_hull_kind(ship_type: str | None, class_name: str | None = None) -> str:
    t = f"{ship_type or ''} {class_name or ''}".lower()
    if "battlecruiser" in t or "battle-cruiser" in t:
        return "battlecruiser"
    if "coast" in t or "coastal" in t:
        return "coastal_battleship"
    if "destroyer" in t or "frigate" in t:
        return "destroyer"
    if "cruiser" in t:
        return "cruiser"
    if "submarine" in t:
        return "submarine"
    if "carrier" in t or "aircraft" in t:
        return "carrier"
    if "battleship" in t or "battle" in t:
        return "battleship"
    return "other"


def validate_spec(record: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    disp = record.get("displacement", {}).get("full_t") or record.get("displacement", {}).get("normal_t")
    dims = record.get("dimensions", {})
    propulsion = record.get("propulsion", {})
    speed = propulsion.get("speed_kn")
    power = propulsion.get("power_shp")
    length = dims.get("length_m")
    breadth = dims.get("breadth_m")
    belt = record.get("armor", {}).get("belt_mm")
    arm = record.get("armament", {})
    main_cal = arm.get("main_caliber_mm")
    main_barrels = arm.get("main_barrels")

    hull_kind = record.get("hull_kind")
    if hull_kind in ("destroyer", "frigate", "corvette", "other"):
        min_displacement = 300
    elif hull_kind == "cruiser":
        min_displacement = 1_000
    elif hull_kind == "coastal_battleship":
        min_displacement = 1_000
    else:
        min_displacement = 2_000

    if disp is None:
        issues.append({"severity": "warn", "code": "missing_displacement", "message": "displacement is unknown"})
    elif not (min_displacement <= disp <= 150_000):
        issues.append({"severity": "error", "code": "displacement_range", "message": f"implausible displacement: {disp}"})

    if length is not None and breadth is not None and breadth > 0:
        ratio = length / breadth
        lb_min, lb_max = lb_ratio_band_for(record.get("type"), hull_kind)
        if not (lb_min <= ratio <= lb_max):
            issues.append({"severity": "warn", "code": "length_breadth_ratio", "message": f"L/B ratio {ratio:.2f}"})

    if disp and length and breadth:
        area_ratio = disp / (length * breadth)
        if not (2.0 <= area_ratio <= 18.0):
            issues.append({"severity": "warn", "code": "displacement_area_ratio", "message": f"W/(L*B) {area_ratio:.2f}"})

    if speed is not None and not (5.0 <= speed <= 45.0):
        issues.append({"severity": "error", "code": "speed_range", "message": f"implausible speed: {speed}"})

    if power and disp and speed and disp > 0 and power > 0:
        admiralty = disp ** (2.0 / 3.0) * speed**3 / power
        if isinstance(admiralty, complex):
            admiralty = admiralty.real
        if not (120 <= admiralty <= 900):
            issues.append({"severity": "warn", "code": "admiralty_coefficient", "message": f"AC {admiralty:.1f}"})

    if belt is not None and not (25 <= belt <= 600):
        issues.append({"severity": "warn", "code": "belt_range", "message": f"implausible belt armor: {belt}"})

    if main_cal is not None and not (100 <= main_cal <= 520):
        issues.append({"severity": "warn", "code": "main_caliber_range", "message": f"main caliber {main_cal}"})
    if main_barrels is not None and not (1 <= main_barrels <= 16):
        issues.append({"severity": "warn", "code": "main_barrel_count", "message": f"main barrels {main_barrels}"})

    issues.extend(validate_naming(record))
    issues.extend(validate_equipment(record))
    return issues


def render_wiki_table(record: dict[str, Any]) -> str:
    def value(path: tuple[str, ...], unit: str = "") -> str:
        obj: Any = record
        for key in path:
            if not isinstance(obj, dict):
                return "unknown"
            obj = obj.get(key)
        if obj is None or obj == "":
            return "unknown"
        if isinstance(obj, float) and obj.is_integer():
            obj = int(obj)
        return f"{obj}{unit}"

    rows = [
        ("Class", record.get("class_name") or "unknown"),
        ("Country", record.get("country") or "unknown"),
        ("Type", record.get("type") or "unknown"),
        ("Year", str(record.get("year") or "unknown")),
        ("Normal displacement", value(("displacement", "normal_t"), " t")),
        ("Full displacement", value(("displacement", "full_t"), " t")),
        ("Length", value(("dimensions", "length_m"), " m")),
        ("Breadth", value(("dimensions", "breadth_m"), " m")),
        ("Power", value(("propulsion", "power_shp"), " shp")),
        ("Speed", value(("propulsion", "speed_kn"), " kn")),
        ("Main armament", value(("armament", "main_caliber_mm"), " mm")),
        ("Main barrels", value(("armament", "main_barrels"))),
        ("Belt armor", value(("armor", "belt_mm"), " mm")),
        ("Era", value(("era_profile", "era"))),
        ("Doctrine", value(("design_doctrine", "doctrine"))),
        ("Secondary battery", value(("equipment", "secondary_battery", "caliber_mm"), " mm")),
        ("Anti-air battery", value(("equipment", "anti_air", "caliber_mm"), " mm")),
        ("Armor scheme", value(("armor_scheme", "scheme"))),
        ("Complement", value(("complement", "crew"))),
        ("Source", record.get("source_url") or "unknown"),
    ]
    body = "\n".join(f"| {k} | {v} |" for k, v in rows)
    return "| Item | Value |\n|---|---|\n" + body


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass(frozen=True)
class MutationProfile:
    displacement_scale: float
    speed_delta: float
    armor_scale: float
    power_scale: float
    main_caliber_delta: float
    note: str


MUTATION_PROFILES = [
    MutationProfile(1.12, 1.5, 0.95, 1.22, 0.0, "fast slightly enlarged variant"),
    MutationProfile(1.18, -0.5, 1.18, 1.08, 25.0, "armor and gun enlarged variant"),
    MutationProfile(0.88, 0.8, 0.82, 0.95, -25.0, "treaty-reduced fast variant"),
    MutationProfile(1.05, 0.0, 1.05, 1.05, 0.0, "conservative improved variant"),
]


def finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(float(value), 3)
