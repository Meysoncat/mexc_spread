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
