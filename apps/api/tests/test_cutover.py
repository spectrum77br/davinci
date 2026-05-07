"""Phase 14 cutover unit tests.

Pure helpers + value translations. The heavier end-to-end migration is exercised
manually via the runbook against a side-loaded `stocksync_legacy` schema; we
keep this test fast and DB-free.
"""

from datetime import time

import pytest

from app.cutover.mappings import (
    DROPPED_PLATFORMS,
    LEGACY_ALERT_SEVERITY,
    LEGACY_ALERT_TYPE,
    LEGACY_LISTING_REQUEST_STATUS,
    LEGACY_LISTING_STATUS,
    LEGACY_PRICING_PLATFORM,
    LEGACY_TO_NEW_PLATFORM,
)
from app.cutover.migrate import _norm_open_id, _parse_daily_time, _to_int_or_none


def test_norm_open_id_passthrough():
    assert _norm_open_id("email:foo@bar.com", None) == "email:foo@bar.com"


def test_norm_open_id_from_email():
    assert _norm_open_id(None, "Foo@Bar.com ") == "email:foo@bar.com"
    assert _norm_open_id("legacy_id_123", "x@y.com") == "email:x@y.com"


def test_norm_open_id_missing_raises():
    with pytest.raises(ValueError):
        _norm_open_id(None, None)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("123", 123),
        ("0042", 42),
        ("-7", -7),
        ("12.0", None),
        ("abc", None),
        (5, 5),
    ],
)
def test_to_int_or_none(raw, expected):
    assert _to_int_or_none(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("garbage", None),
        ("3:5", None),  # missing leading zero in minutes
        ("25:00", None),
        ("00:00", time(0, 0)),
        ("9:30", time(9, 30)),
        ("23:59", time(23, 59)),
    ],
)
def test_parse_daily_time(raw, expected):
    assert _parse_daily_time(raw) == expected


def test_platform_mapping_covers_supported():
    for legacy in ("bling", "mercadolivre", "ml", "shopee", "amazon"):
        assert legacy in LEGACY_TO_NEW_PLATFORM
    assert LEGACY_TO_NEW_PLATFORM["mercadolivre"] == "ml"


def test_dropped_platforms_disjoint_from_supported():
    assert not DROPPED_PLATFORMS & LEGACY_TO_NEW_PLATFORM.keys()


def test_alert_type_mapping_total():
    legacy = {
        "sync_error",
        "low_stock",
        "connection_lost",
        "stock_discrepancy",
        "sync_success",
        "stock_restock",
    }
    assert legacy <= LEGACY_ALERT_TYPE.keys()


def test_alert_severity_critical_to_error():
    assert LEGACY_ALERT_SEVERITY["critical"] == "error"


def test_listing_status_mapping():
    for legacy in ("active", "paused", "closed", "under_review", "inactive"):
        assert legacy in LEGACY_LISTING_STATUS


def test_listing_request_status_mapping():
    for legacy in ("pending", "in_progress", "completed", "rejected"):
        assert legacy in LEGACY_LISTING_REQUEST_STATUS


def test_pricing_platform_mercadolivre_keeps_value():
    assert LEGACY_PRICING_PLATFORM["mercadolivre"] == "mercadolivre"
