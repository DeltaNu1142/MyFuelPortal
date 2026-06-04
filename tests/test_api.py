"""Unit tests for the MyFuelPortal HTML parsers.

These exercise the pure parsing functions against saved (synthetic) fixtures —
no network and no Home Assistant import. `api.py` is deliberately HA-free, so we
load it directly here, which keeps the parser tests fast and dependency-light
(only `requests` + `beautifulsoup4`, the integration's own runtime deps).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from bs4 import BeautifulSoup

_ROOT = Path(__file__).resolve().parents[1]
_API_PATH = _ROOT / "custom_components" / "myfuelportal" / "api.py"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_api():
    """Import api.py in isolation (it has no relative/HA imports)."""
    spec = importlib.util.spec_from_file_location("mfp_api", _API_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = _load_api()


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_parse_deliveries_basic():
    soup = BeautifulSoup(_fixture("delivery_history.html"), "html.parser")
    deliveries = api._parse_deliveries(soup)

    assert len(deliveries) == 2

    first = deliveries[0]
    assert first["date"] == "2026-05-20"
    assert first["ticket"] == "100001"
    assert first["product"] == "Propane"
    assert first["gallons"] == 300.0
    assert first["cost"] == 900.0
    assert first["price_per_gallon"] == 3.0  # 900 / 300
    assert first["detail_id"] == "600001"


def test_parse_deliveries_handles_comma_grouped_numbers():
    soup = BeautifulSoup(_fixture("delivery_history.html"), "html.parser")
    second = api._parse_deliveries(soup)[1]

    # "1,250.500" gallons and "$4,001.60" total must survive the commas.
    assert second["gallons"] == 1250.5
    assert second["cost"] == 4001.6
    assert second["price_per_gallon"] == 3.2  # 4001.60 / 1250.5
    assert second["detail_id"] == "600000"


def test_parse_deliveries_empty_on_no_table():
    soup = BeautifulSoup("<html><body><p>no table here</p></body></html>", "html.parser")
    assert api._parse_deliveries(soup) == []


def test_parse_account():
    soup = BeautifulSoup(_fixture("home.html"), "html.parser")
    account = api._parse_account(soup)
    assert account["customer_since"] == "2020-01-15"
    assert account["account_balance"] == 1234.56
    assert account["status"] == "Active"


def test_group_deliveries_by_tank_splits_and_preserves_order():
    # Two tanks interleaved; each bucket keeps insertion order.
    deliveries = [
        {"tank": "TANK 1", "date": "2026-01-01"},
        {"tank": "TANK 2", "date": "2026-01-02"},
        {"tank": "TANK 1", "date": "2026-02-01"},
    ]
    grouped = api.group_deliveries_by_tank(deliveries)
    assert set(grouped) == {"TANK 1", "TANK 2"}
    assert [d["date"] for d in grouped["TANK 1"]] == ["2026-01-01", "2026-02-01"]
    assert [d["date"] for d in grouped["TANK 2"]] == ["2026-01-02"]


def test_group_deliveries_by_tank_skips_unattributed_rows():
    # Rows with no tank (None, missing, or empty string) can't be keyed -> dropped.
    deliveries = [
        {"tank": "TANK 1", "date": "2026-01-01"},
        {"tank": None, "date": "2026-01-02"},
        {"date": "2026-01-03"},
        {"tank": "", "date": "2026-01-04"},
    ]
    grouped = api.group_deliveries_by_tank(deliveries)
    assert list(grouped) == ["TANK 1"]
    assert len(grouped["TANK 1"]) == 1


def test_group_deliveries_by_tank_empty():
    assert api.group_deliveries_by_tank([]) == {}
