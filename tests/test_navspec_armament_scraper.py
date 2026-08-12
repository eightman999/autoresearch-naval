"""NavWeaps 武装データ抽出ヘルパの回帰テスト。"""

from __future__ import annotations

from tools.navspec_armament_scraper import (
    _extract_ammo_type,
    _first_angular_rate,
    _first_rate_of_fire,
)


def test_rate_of_fire_ignores_model_year() -> None:
    text = "Model 1929: 12 rounds per minute maximum, 7 rounds per minute practical"
    assert _first_rate_of_fire(text) == 12.0


def test_rate_of_fire_ignores_mark_number() -> None:
    text = "Mark 7 and Mark 11: About 1.75 rounds per minute Mark B: 2 rounds per minute"
    assert _first_rate_of_fire(text) == 1.75


def test_rate_of_fire_range_uses_lower_bound() -> None:
    assert _first_rate_of_fire("about 1.25 - 1.5 rounds / minute") == 1.25


def test_rate_of_fire_keeps_type_value_not_type_number() -> None:
    text = "Type 88: 8 rounds per minute Type 89: 14 rounds per minute"
    assert _first_rate_of_fire(text) == 8.0


def test_angular_rate_ignores_model_year() -> None:
    text = "Model 1929: 6 degrees per second Model 1934: 10 degrees per second"
    assert _first_angular_rate(text) == 6.0


def test_semi_ap_is_normalized_to_sap() -> None:
    assert _extract_ammo_type("Semi-AP mod 1928: 33.5 kg HE mod 1911: 36.86 kg") == (
        "SAP",
        "Semi-AP mod 1928",
    )
