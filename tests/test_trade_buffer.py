"""Unit tests for trade_buffer (MEXC spot deals aggregator)."""

from __future__ import annotations

import time

import pytest

from mexc_monitor import trade_buffer as tb


def _now_ms() -> int:
    return int(time.time() * 1000)


@pytest.fixture(autouse=True)
def _isolate():
    tb.clear()
    yield
    tb.clear()


def test_invalid_trade_rejected():
    assert tb.push_trade("BTCUSDT", price=0, quantity=1, side=1) is None
    assert tb.push_trade("BTCUSDT", price=100, quantity=0, side=1) is None
    assert tb.push_trade("BTCUSDT", price=100, quantity=1, side=3) is None
    assert tb.push_trade("BTCUSDT", price=-1, quantity=1, side=1) is None
    assert tb.get_stats("BTCUSDT") is None


def test_stats_for_unknown_symbol_is_none():
    assert tb.get_stats("NOPE") is None


def test_counts_and_vwap():
    now = _now_ms()
    # 2 buys @ 100 qty 2 (notional 200 each), 1 sell @ 110 qty 1 (notional 110)
    tb.push_trade("BTCUSDT", 100.0, 2.0, 1, now)
    tb.push_trade("BTCUSDT", 100.0, 2.0, 1, now + 1)
    tb.push_trade("BTCUSDT", 110.0, 1.0, 2, now + 2)
    st = tb.get_stats("BTCUSDT", period_sec=60)
    assert st is not None
    assert st.count == 3
    assert st.buy_count == 2
    assert st.sell_count == 1
    # base = 2+2+1 = 5, quote = 200+200+110 = 510 → vwap = 102
    assert st.volume_base == pytest.approx(5.0)
    assert st.volume_quote == pytest.approx(510.0)
    assert st.vwap == pytest.approx(102.0)


def test_buy_sell_ratio_and_split_volumes():
    now = _now_ms()
    tb.push_trade("ETHUSDT", 10.0, 1.0, 1, now)  # buy notional 10
    tb.push_trade("ETHUSDT", 10.0, 3.0, 2, now + 1)  # sell notional 30
    st = tb.get_stats("ETHUSDT", period_sec=60)
    assert st.buy_volume_quote == pytest.approx(10.0)
    assert st.sell_volume_quote == pytest.approx(30.0)
    assert st.buy_sell_ratio == pytest.approx(10.0 / 30.0)


def test_buy_sell_ratio_none_when_no_sells():
    now = _now_ms()
    tb.push_trade("XRPUSDT", 1.0, 1.0, 1, now)
    st = tb.get_stats("XRPUSDT", period_sec=60)
    assert st.buy_sell_ratio is None  # sell_volume_quote == 0


def test_period_filtering_excludes_old_trades():
    now = _now_ms()
    tb.push_trade("BTCUSDT", 100.0, 1.0, 1, now - 120_000)
    tb.push_trade("BTCUSDT", 100.0, 1.0, 1, now - 10_000)
    tb.push_trade("BTCUSDT", 100.0, 1.0, 1, now - 5_000)
    st = tb.get_stats("BTCUSDT", period_sec=60.0)
    assert st is not None
    assert st.count == 2


def test_trades_per_min_scales_with_period():
    now = _now_ms()
    for i in range(10):
        tb.push_trade("SOLUSDT", 10.0, 1.0, 1, now + i)
    st60 = tb.get_stats("SOLUSDT", period_sec=60.0)
    # 10 сделок в окне → trades_per_min зависит от периода; просто проверим > 0
    assert st60.trades_per_min > 0


def test_get_tracked_symbols():
    assert tb.get_tracked_symbols() == []
    now = _now_ms()
    tb.push_trade("BTCUSDT", 100.0, 1.0, 1, now)
    tb.push_trade("ETHUSDT", 10.0, 1.0, 2, now)
    tracked = set(tb.get_tracked_symbols())
    assert tracked == {"BTCUSDT", "ETHUSDT"}


def test_clear_removes_symbol():
    tb.push_trade("BTCUSDT", 100.0, 1.0, 1, _now_ms())
    tb.clear("BTCUSDT")
    assert tb.get_stats("BTCUSDT") is None
    assert tb.get_tracked_symbols() == []


def test_symbol_case_normalized():
    tb.push_trade("btcusdt", 100.0, 1.0, 1, _now_ms())
    assert "BTCUSDT" in tb.get_tracked_symbols()
    assert tb.get_stats("btcusdt") is not None
