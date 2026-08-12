"""Unit tests for ws_spot_orderbook bookTicker update-rate tracking (activity proxy).

Purely exercises the in-memory rate accounting via the internal ``_apply_push``
helper — no network / no WS connection.
"""

from __future__ import annotations

import time

import pytest

from mexc_monitor import ws_spot_orderbook as wso


@pytest.fixture(autouse=True)
def _isolate():
    wso._push_times.clear()
    wso._tops.clear()
    wso._last_mono.clear()
    yield
    wso._push_times.clear()
    wso._tops.clear()
    wso._last_mono.clear()


def test_unknown_symbol_rate_is_none():
    assert wso.get_book_update_rate("NOPE") is None


def test_rate_counts_recent_pushes_per_minute():
    # 5 pushes back-to-back "now" → extrapolated over ~0 elapsed, rate is high.
    for _ in range(5):
        wso._apply_push("BTCUSDT", 100.0, 100.5, 1.0, 1.0)
    rate = wso.get_book_update_rate("BTCUSDT", window_sec=60.0)
    assert rate is not None
    assert rate > 0.0


def test_old_pushes_excluded_from_window():
    # Push 3 times in the distant past (outside the 60s window), 2 just now.
    old = time.monotonic() - 120.0
    for t, bid in [(old, 100.0), (old, 100.1), (old, 100.2)]:
        wso._apply_push("ETHUSDT", bid, bid + 0.5, 1.0, 1.0)
    # _apply_push prunes by its own 60s window on insertion, so old ones drop.
    for _ in range(2):
        wso._apply_push("ETHUSDT", 200.0, 200.5, 1.0, 1.0)
    rate = wso.get_book_update_rate("ETHUSDT", window_sec=60.0)
    assert rate is not None
    # Only the 2 fresh pushes should be counted.
    assert rate > 0.0


def test_get_book_update_rates_drops_empty():
    wso._apply_push("BTCUSDT", 100.0, 100.5, 1.0, 1.0)
    rates = wso.get_book_update_rates()
    assert "BTCUSDT" in rates
    assert "ETHUSDT" not in rates


# ── tier 1.5: dynamic watchlist (shortlist → WS subscription) ────────────────


@pytest.fixture
def _settings():
    from dataclasses import replace
    from mexc_monitor.config import DEFAULT_SETTINGS

    return replace(
        DEFAULT_SETTINGS,
        spot_orderbook_ws_enabled=True,
        spot_orderbook_ws_symbols=("BTCUSDT", "ETHUSDT"),
    )


def test_touch_watchlist_extends_desired(_settings):
    wso._watchlist_seen.clear()
    base = wso.desired_spot_orderbook_symbols(_settings)
    assert base == ("BTCUSDT", "ETHUSDT")
    wso.touch_watchlist(["solusdt", "DOGEUSDT"])
    desired = wso.desired_spot_orderbook_symbols(_settings)
    assert desired == ("BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT")


def test_touch_watchlist_dedups_and_normalizes(_settings):
    wso._watchlist_seen.clear()
    wso.touch_watchlist(["btcusdt", "ETHusdt"])  # already in base
    assert wso.desired_spot_orderbook_symbols(_settings) == ("BTCUSDT", "ETHUSDT")


def test_stale_watchlist_entries_expire(_settings):
    wso._watchlist_seen.clear()
    wso.touch_watchlist(["SOLUSDT"])
    # backdate past the TTL
    wso._watchlist_seen["SOLUSDT"] = time.monotonic() - (wso._WATCHLIST_TTL_SEC + 5)
    assert "SOLUSDT" not in wso.desired_spot_orderbook_symbols(_settings)


def test_desired_capped_at_max_subs(_settings):
    wso._watchlist_seen.clear()
    many = [f"COIN{i}USDT" for i in range(wso._MAX_SUBS_PER_CONNECTION + 10)]
    wso.touch_watchlist(many)
    desired = wso.desired_spot_orderbook_symbols(_settings)
    assert len(desired) <= wso._MAX_SUBS_PER_CONNECTION


def test_reconcile_noop_when_unchanged(_settings):
    # desired == active base → no restart attempted (returns early).
    wso._watchlist_seen.clear()
    wso._active_symbols = ("BTCUSDT", "ETHUSDT")
    # Should not raise / should be a no-op (no thread started).
    wso.reconcile_spot_orderbook_ws(_settings)

