"""Unit tests for ScreenerState (lifetime + rolling persistence tracking)."""

from __future__ import annotations

import pytest

from mexc_monitor.screener.state import ScreenerState


def test_lifetime_starts_zero():
    st = ScreenerState()
    st.update("BTCUSDT", spread_bps=5.0, threshold=3.0, now_ms=1_000_000)
    # lifetime measured from the first observation; ~0 at the moment of first update.
    assert st.get_lifetime("BTCUSDT", now_ms=1_000_000) == 0.0


def test_lifetime_grows_with_wall_clock_while_above_threshold():
    st = ScreenerState()
    st.update("BTCUSDT", spread_bps=5.0, threshold=3.0, now_ms=1_000_000)
    assert st.get_lifetime("BTCUSDT", now_ms=1_008_000) == pytest.approx(8.0)


def test_lifetime_resets_when_spread_falls_below_threshold():
    st = ScreenerState()
    st.update("BTCUSDT", spread_bps=5.0, threshold=3.0, now_ms=1_000_000)
    st.update("BTCUSDT", spread_bps=2.0, threshold=3.0, now_ms=1_010_000)
    assert st.get_lifetime("BTCUSDT", now_ms=1_010_000) == 0.0
    st.update("BTCUSDT", spread_bps=5.0, threshold=3.0, now_ms=1_020_000)
    assert st.get_lifetime("BTCUSDT", now_ms=1_028_000) == pytest.approx(8.0)


def test_lifetime_is_per_symbol():
    st = ScreenerState()
    st.update("BTCUSDT", spread_bps=5.0, threshold=3.0, now_ms=1_000_000)
    st.update("ETHUSDT", spread_bps=2.0, threshold=3.0, now_ms=1_000_000)
    assert st.get_lifetime("BTCUSDT", now_ms=1_010_000) == pytest.approx(10.0)
    assert st.get_lifetime("ETHUSDT", now_ms=1_010_000) == 0.0


def test_rolling_pct_above_and_std():
    st = ScreenerState(rolling_window=10)
    threshold = 3.0
    samples = [5.0, 5.0, 2.0, 5.0, 6.0]  # 4/5 above threshold
    for i, s in enumerate(samples):
        st.update(
            "BTCUSDT", spread_bps=s, threshold=threshold, now_ms=1_000_000 + i * 1000
        )
    pct, std = st.get_rolling("BTCUSDT", threshold)
    assert pct == pytest.approx(80.0)
    assert std is not None and std > 0


def test_rolling_returns_none_for_unknown_symbol():
    st = ScreenerState()
    pct, std = st.get_rolling("NOPE", threshold=3.0)
    assert pct == 0.0
    assert std is None


def test_none_spread_does_not_pollute_rolling():
    st = ScreenerState()
    st.update("BTCUSDT", spread_bps=None, threshold=3.0, now_ms=1_000_000)
    pct, std = st.get_rolling("BTCUSDT", threshold=3.0)
    assert pct == 0.0
    assert std is None


def test_prune_drops_inactive_symbols():
    st = ScreenerState()
    st.update("BTCUSDT", spread_bps=5.0, threshold=3.0, now_ms=1_000_000)
    st.update("ETHUSDT", spread_bps=5.0, threshold=3.0, now_ms=1_000_000)
    st.prune({"BTCUSDT"})
    assert st.get_lifetime("ETHUSDT", now_ms=1_010_000) == 0.0
    assert st.get_lifetime("BTCUSDT", now_ms=1_010_000) == pytest.approx(10.0)


def test_set_rolling_window_shrinks_existing():
    st = ScreenerState(rolling_window=10)
    for i in range(10):
        st.update("BTCUSDT", spread_bps=5.0, threshold=3.0, now_ms=1_000_000 + i)
    st.set_rolling_window(4)
    pct, _ = st.get_rolling("BTCUSDT", threshold=3.0)
    assert pct == pytest.approx(100.0)


def test_reset_clears_symbol():
    st = ScreenerState()
    st.update("BTCUSDT", spread_bps=5.0, threshold=3.0, now_ms=1_000_000)
    st.reset("BTCUSDT")
    assert st.get_lifetime("BTCUSDT", now_ms=1_010_000) == 0.0
    pct, std = st.get_rolling("BTCUSDT", threshold=3.0)
    assert pct == 0.0 and std is None
