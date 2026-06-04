"""Tests for sensor value functions that need the Home Assistant import.

Unlike test_api.py (which loads the HA-free api.py directly), these import the
sensor module, so they require Home Assistant to be installed in the test env.
"""
from __future__ import annotations

import custom_components.myfuelportal.sensor as sensor


def test_avg_daily_usage_latest_cycle():
    # Newest-first: 300 gal delivered, 100 days since the prior delivery -> 3.0/day.
    deliveries = [
        {"date": "2026-05-20", "gallons": 300.0},
        {"date": "2026-02-09", "gallons": 250.0},
    ]
    assert sensor._avg_daily_usage(deliveries) == 3.0


def test_avg_daily_usage_skips_zero_fill():
    # Latest is a 0-gallon "no-fill" visit -> fall through to the next real cycle.
    deliveries = [
        {"date": "2026-06-01", "gallons": 0.0},
        {"date": "2026-05-20", "gallons": 300.0},
        {"date": "2026-02-09", "gallons": 250.0},
    ]
    assert sensor._avg_daily_usage(deliveries) == 3.0


def test_avg_daily_usage_none_without_a_cycle():
    assert sensor._avg_daily_usage([{"date": "2026-05-20", "gallons": 300.0}]) is None
    assert sensor._avg_daily_usage([]) is None
