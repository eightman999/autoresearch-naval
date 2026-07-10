"""Country-specific naming helpers for fictional naval specifications."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


CLASS_SUFFIX_RE = re.compile(r"\s+class$", re.I)
PLACEHOLDER_RE = re.compile(r"\balternate\s+\d+\b", re.I)
PREFIX_SOURCE_URL = "https://ja.wikipedia.org/wiki/%E8%89%A6%E8%88%B9%E6%8E%A5%E9%A0%AD%E8%BE%9E"


@dataclass(frozen=True)
class NamingRule:
    country: str
    prefix: str | None
    category: str
    description: str
    battleships: tuple[str, ...]
    battlecruisers: tuple[str, ...]
    coastal_battleships: tuple[str, ...] = ()


JAPAN_OLD_PROVINCE_NAMES: tuple[str, ...] = (
    "Yamashiro",
    "Yamato",
    "Kawachi",
    "Izumi",
    "Settsu",
    "Iga",
    "Ise",
    "Shima",
    "Owari",
    "Mikawa",
    "Totomi",
    "Suruga",
    "Kai",
    "Izu",
    "Sagami",
    "Musashi",
    "Awa",
    "Kazusa",
    "Shimosa",
    "Hitachi",
    "Omi",
    "Mino",
    "Hida",
    "Shinano",
    "Kozuke",
    "Shimotsuke",
    "Iwaki",
    "Iwashiro",
    "Rikuzen",
    "Rikuchu",
    "Mutsu",
    "Uzen",
    "Ugo",
    "Wakasa",
    "Echizen",
    "Kaga",
    "Noto",
    "Etchu",
    "Echigo",
    "Sado",
    "Tamba",
    "Tango",
    "Tajima",
    "Inaba",
    "Hoki",
    "Izumo",
    "Iwami",
    "Oki",
    "Harima",
    "Mimasaka",
    "Bizen",
    "Bitchu",
    "Bingo",
    "Aki",
    "Suo",
    "Nagato",
    "Kii",
    "Awaji",
    "Awa",
    "Sanuki",
    "Iyo",
    "Tosa",
    "Chikuzen",
    "Chikugo",
    "Buzen",
    "Bungo",
    "Hizen",
    "Higo",
    "Hyuga",
    "Osumi",
    "Satsuma",
    "Iki",
    "Tsushima",
    "Ryukyu",
    "Ishikari",
    "Iburi",
    "Shiribeshi",
    "Oshima",
    "Teshio",
    "Kitami",
    "Hidaka",
    "Tokachi",
    "Kushiro",
    "Nemuro",
    "Chishima",
)


JAPAN_HYAKUMEIZAN_NAMES: tuple[str, ...] = (
    # Ship-style mountain names: drop generic -yama/-dake where safe, keep
    # lexicalized forms, and add regional qualifiers when stripped names collide.
    "Rishiri",
    "Rausu",
    "Shari",
    "Akan",
    "Daisetsu",
    "Tomuraushi",
    "Tokachi",
    "Poroshiri",
    "Yotei",
    "Iwaki",
    "Hakkoda",
    "Hachimantai",
    "Iwate",
    "Hayachine",
    "Chokai",
    "Gassan",
    "Asahi",
    "Zao",
    "Iide",
    "Azuma",
    "Adatara",
    "Bandai",
    "Aizu-Koma",
    "Nasu",
    "Tsukuba",
    "Oze-Hiuchi",
    "Shibutsu",
    "Joshu-Hotaka",
    "Akagi",
    "Nantai",
    "Nikko-Shirane",
    "Sukai",
    "Echigo-Koma",
    "Hiraga",
    "Makihata",
    "Tanigawa",
    "Naeba",
    "Amakazari",
    "Myoko",
    "Kubiki-Hiuchi",
    "Takatsuma",
    "Kusatsu-Shirane",
    "Azumaya",
    "Asama",
    "Ryokami",
    "Kobushi",
    "Kinpu",
    "Mizugaki",
    "Kumotori",
    "Daibosatsu",
    "Tanzawa",
    "Fuji",
    "Amagi",
    "Shirouma",
    "Goryu",
    "Kashima-Yari",
    "Etchu-Tsurugi",
    "Tateyama",
    "Yakushi",
    "Kurobe-Goro",
    "Suisho",
    "Washiba",
    "Yari",
    "Hida-Hotaka",
    "Jonen",
    "Kasa",
    "Yake",
    "Norikura",
    "Utsukushigahara",
    "Kirigamine",
    "Tateshina",
    "Yatsugatake",
    "Ontake",
    "Kiso-Koma",
    "Utsugi",
    "Ena",
    "Kai-Koma",
    "Senjo",
    "Hoo",
    "Kitadake",
    "Aino",
    "Shiomi",
    "Warusawa",
    "Akaishi",
    "Hijiri",
    "Tekari",
    "Hakusan",
    "Arashima",
    "Ibuki",
    "Odaigahara",
    "Omine",
    "Daisen",
    "Awa-Tsurugi",
    "Ishizuchi",
    "Kuju",
    "Sobo",
    "Aso",
    "Kirishima",
    "Kaimon",
    "Miyanoura",
)


NAMING_RULES: dict[str, NamingRule] = {
    "Japan": NamingRule(
        country="Japan",
        prefix=None,
        category="IJN capital ships: provinces for battleships, mountains for battlecruisers",
        description="Use historical-style IJN geographic names. Avoid personal names.",
        battleships=JAPAN_OLD_PROVINCE_NAMES,
        battlecruisers=JAPAN_HYAKUMEIZAN_NAMES,
    ),
    "United-States": NamingRule(
        country="United-States",
        prefix="USS",
        category="US battleships: states",
        description="Use US state names for battleships.",
        battleships=(
            "Alaska",
            "Arizona",
            "Dakota",
            "Delaware",
            "Florida",
            "Georgia",
            "Illinois",
            "Kentucky",
            "Maine",
            "Montana",
            "Nebraska",
            "Oregon",
            "Pennsylvania",
            "Vermont",
            "Virginia",
            "Wisconsin",
        ),
        battlecruisers=(
            "Alaska",
            "Guam",
            "Hawaii",
            "Puerto Rico",
            "Samoa",
        ),
        coastal_battleships=("Monterey", "Wyoming", "Arkansas", "Texas"),
    ),
    "United-Kingdom": NamingRule(
        country="United-Kingdom",
        prefix="HMS",
        category="Royal Navy capital ships: royal, martial, or virtue names",
        description="Use traditional RN ship names, not US state names.",
        battleships=(
            "Audacious",
            "Benbow",
            "Collingwood",
            "Conqueror",
            "Defiance",
            "Dominion",
            "Emperor",
            "Formidable",
            "Majestic",
            "Monarch",
            "Superb",
            "Thunderer",
            "Vanguard",
        ),
        battlecruisers=(
            "Courageous",
            "Glorious",
            "Indefatigable",
            "Inflexible",
            "Invincible",
            "Lion",
            "Princess Royal",
            "Renown",
            "Repulse",
        ),
    ),
    "Germany": NamingRule(
        country="Germany",
        prefix=None,
        category="German capital ships: states, regions, rulers, or senior figures",
        description="Use German regional or statesman names.",
        battleships=(
            "Baden",
            "Bayern",
            "Brandenburg",
            "Hannover",
            "Hessen",
            "Lothringen",
            "Oldenburg",
            "Pommern",
            "Sachsen",
            "Schwaben",
            "Thueringen",
            "Wuerttemberg",
        ),
        battlecruisers=(
            "Derfflinger",
            "Hindenburg",
            "Lutzow",
            "Mackensen",
            "Moltke",
            "Seydlitz",
            "Von der Tann",
        ),
        coastal_battleships=("Aegir", "Beowulf", "Frithjof", "Hagen", "Hildebrand", "Siegfried"),
    ),
    "France": NamingRule(
        country="France",
        prefix=None,
        category="French capital ships: republican ideals, provinces, or historic names",
        description="Use French political, provincial, or historic names.",
        battleships=(
            "Bretagne",
            "Democratie",
            "France",
            "Gascogne",
            "Justice",
            "Liberte",
            "Lorraine",
            "Normandie",
            "Patrie",
            "Provence",
            "Republique",
            "Verite",
        ),
        battlecruisers=("Dunkerque", "Strasbourg", "Flandre", "Languedoc"),
    ),
    "Italy": NamingRule(
        country="Italy",
        prefix="RN",
        category="Italian capital ships: statesmen, admirals, cities, or national names",
        description="Use Italian historic figures or places.",
        battleships=(
            "Andrea Doria",
            "Caio Duilio",
            "Conte di Cavour",
            "Francesco Caracciolo",
            "Giulio Cesare",
            "Impero",
            "Italia",
            "Lepanto",
            "Littorio",
            "Roma",
            "Sardegna",
            "Sicilia",
        ),
        battlecruisers=("Amalfi", "Brindisi", "Genova", "Venezia"),
    ),
    "Russia": NamingRule(
        country="Russia",
        prefix=None,
        category="Russian capital ships: imperial, saint, city, or victory names",
        description="Use Russian imperial or patriotic names.",
        battleships=(
            "Andrei Pervozvanny",
            "Ekaterina Velikaya",
            "Imperator Aleksandr",
            "Imperator Nikolai",
            "Knyaz Suvorov",
            "Petropavlovsk",
            "Poltava",
            "Sevastopol",
            "Sinop",
            "Slava",
        ),
        battlecruisers=("Borodino", "Izmail", "Kinburn", "Navarin"),
    ),
}


COUNTRY_ALIASES = {
    "USA": "United-States",
    "US": "United-States",
    "United States": "United-States",
    "United States of America": "United-States",
    "UK": "United-Kingdom",
    "Great Britain": "United-Kingdom",
    "Britain": "United-Kingdom",
    "United Kingdom": "United-Kingdom",
    "Soviet-Union": "Russia",
    "Russian-Empire": "Russia",
}


def canonical_country(country: str | None) -> str:
    if not country:
        return ""
    normalized = country.strip().replace("_", "-")
    return COUNTRY_ALIASES.get(normalized, normalized)


def naming_rule_for(country: str | None) -> NamingRule | None:
    return NAMING_RULES.get(canonical_country(country))


def names_for(country: str | None, hull_kind: str | None) -> tuple[str, ...]:
    rule = naming_rule_for(country)
    if rule is None:
        return ()
    if hull_kind == "battlecruiser":
        return rule.battlecruisers or rule.battleships
    if hull_kind == "coastal_battleship":
        return rule.coastal_battleships or rule.battleships
    if hull_kind == "battleship":
        return rule.battleships
    return ()


def naming_category_for(country: str | None, hull_kind: str | None) -> str:
    country = canonical_country(country)
    if hull_kind not in ("battleship", "battlecruiser", "coastal_battleship"):
        return f"{hull_kind}_fallback"
    if country == "Japan":
        if hull_kind == "battlecruiser":
            return "mountain"
        if hull_kind == "coastal_battleship":
            return "old_province"
        return "old_province"
    if hull_kind == "battlecruiser":
        return "battlecruiser_pool"
    if hull_kind == "coastal_battleship":
        return "coastal_battleship_pool"
    return "battleship_pool"


def naming_source_note_for(country: str | None, hull_kind: str | None) -> str:
    country = canonical_country(country)
    if hull_kind not in ("battleship", "battlecruiser", "coastal_battleship"):
        return "No country-specific naming pool for this hull kind; using base class name fallback."
    if country == "Japan":
        if hull_kind == "battlecruiser":
            return "User-supplied romanized list based on the 100 Famous Japanese Mountains."
        return "User-supplied romanized list based on historical Japanese province names."
    return "Country-specific hand-curated naming pool."


def normalize_name(value: str | None) -> str:
    if not value:
        return ""
    value = CLASS_SUFFIX_RE.sub("", value.strip())
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def record_year(record: dict[str, Any]) -> int | None:
    try:
        return int(record.get("year"))
    except (TypeError, ValueError):
        return None


def official_prefix_for(country: str | None, year: int | None, rule: NamingRule | None) -> tuple[str | None, str]:
    country = canonical_country(country)
    if country == "Japan":
        return None, "IJN/HIJMS are writer conveniences; IJN did not use an official ship prefix."
    if country == "Germany":
        if year is not None and year <= 1918:
            return "SMS", "Imperial German Navy capital ships use SMS."
        return None, "German naval prefixes such as FGS/KMS/DKM are NATO or writer conveniences, not internal official prefixes."
    if country == "France":
        return None, "FS is a NATO designation; French naval usage is treated here as no internal official prefix."
    if country == "Italy":
        if year is None or year < 1946:
            return "RN", "Kingdom of Italy ships use RN (Regia Nave)."
        return None, "ITS is a NATO designation; post-kingdom Italian usage is treated as no internal official prefix."
    if country == "Russia":
        return None, "RFS is modern and not used internally; imperial-era Russian capital ships are treated as no official prefix."
    if rule is None:
        return None, "No country-specific official prefix rule available."
    return rule.prefix, "Use the country's official naval ship prefix when one is part of the naming convention."


def naming_plan(record: dict[str, Any], profile_index: int) -> dict[str, Any]:
    country = canonical_country(record.get("country"))
    hull_kind = record.get("hull_kind")
    pool = names_for(country, hull_kind)
    seed = f"{record.get('record_id')}:{profile_index}:{country}:{hull_kind}"
    if pool:
        digest = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16)
        name = pool[digest % len(pool)]
    else:
        base = CLASS_SUFFIX_RE.sub("", str(record.get("class_name") or "Unknown").strip())
        name = f"{base} Variant {profile_index + 1}"
    rule = naming_rule_for(country)
    prefix, prefix_note = official_prefix_for(country, record_year(record), rule)
    return {
        "target_country": country or record.get("country"),
        "target_class_name": f"{name} Class",
        "naming_category": naming_category_for(country, hull_kind),
        "naming_rule": rule.category if rule else "fallback from base class name",
        "naming_description": rule.description if rule else "No country-specific naming rule available.",
        "name_pool": list(pool),
        "name_pool_size": len(pool),
        "name_pool_source_note": naming_source_note_for(country, hull_kind),
        "prefix": prefix,
        "prefix_note": prefix_note,
        "prefix_source": PREFIX_SOURCE_URL,
    }


def validate_naming(record: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if record.get("source_kind") != "fictional_bounded":
        return issues

    country = canonical_country(record.get("country"))
    derived = record.get("derived_from") or {}
    constraints = record.get("fictional_constraints") or {}
    target_country = canonical_country(
        constraints.get("target_country") or derived.get("country") or record.get("country")
    )
    target_name = constraints.get("target_class_name")
    class_name = record.get("class_name") or ""
    expected_category = naming_category_for(target_country, record.get("hull_kind"))
    actual_category = constraints.get("naming_category")
    expected_prefix, _ = official_prefix_for(target_country, record_year(record), naming_rule_for(target_country))
    actual_prefix = constraints.get("prefix")
    if actual_prefix in {"", "None"}:
        actual_prefix = None

    if record.get("source_url") is not None:
        issues.append(
            {"severity": "error", "code": "fictional_source_url", "message": "fictional record must not have source_url"}
        )

    if not str(record.get("record_id") or "").startswith("fictional:"):
        issues.append(
            {"severity": "error", "code": "fictional_record_id", "message": "fictional record_id must start with fictional:"}
        )

    if not isinstance(derived, dict) or not derived.get("record_id"):
        issues.append(
            {"severity": "error", "code": "missing_derived_from", "message": "fictional record needs derived_from.record_id"}
        )
    else:
        if derived.get("record_id") not in str(record.get("record_id") or ""):
            issues.append(
                {
                    "severity": "error",
                    "code": "derived_record_id_mismatch",
                    "message": "record_id does not contain derived_from.record_id",
                }
            )

    if country and target_country and country != target_country:
        issues.append(
            {
                "severity": "error",
                "code": "target_country_mismatch",
                "message": f"country {country} does not match target country {target_country}",
            }
        )

    if "prefix" in constraints and actual_prefix != expected_prefix:
        issues.append(
            {
                "severity": "error",
                "code": "prefix_mismatch",
                "message": f"prefix {actual_prefix!r} does not match expected {expected_prefix!r}",
            }
        )

    if actual_category and actual_category != expected_category:
        issues.append(
            {
                "severity": "error",
                "code": "naming_category_mismatch",
                "message": f"naming category {actual_category!r} does not match expected {expected_category!r}",
            }
        )

    if PLACEHOLDER_RE.search(class_name):
        issues.append(
            {
                "severity": "error",
                "code": "placeholder_class_name",
                "message": f"class_name still looks like a placeholder: {class_name}",
            }
        )

    if target_name and normalize_name(class_name) != normalize_name(target_name):
        issues.append(
            {
                "severity": "error",
                "code": "target_class_name_mismatch",
                "message": f"class_name {class_name!r} does not match target {target_name!r}",
            }
        )
        return issues

    pool = names_for(target_country, record.get("hull_kind"))
    if pool and normalize_name(class_name) not in {normalize_name(f"{name} Class") for name in pool}:
        issues.append(
            {
                "severity": "error",
                "code": "class_name_not_in_pool",
                "message": f"class_name {class_name!r} is outside {target_country} naming pool",
            }
        )

    return issues
