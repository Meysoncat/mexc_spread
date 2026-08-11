"""Integration tests for PortfolioRiskManager engine registration in backend/main.py.

Verifies that all trading engines — including TradingEngine, previously the only
one excluded from portfolio risk monitoring — are registered with the manager,
and that the wrapper adapters correctly expose notional, symbols, kill-switch and
realized PnL.
"""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("MEXC_SNAPSHOT_CACHE_TTL_SEC", "0")

import pytest

import backend.main as main

# All five engines must be registered with the portfolio risk manager.
EXPECTED_ENGINES = {"spread_capture", "arbitrage", "futures_arb", "metascalp", "trading"}


def _registered_names() -> set[str]:
    return {getattr(e, "engine_name", "?") for e in main._portfolio_risk._engines}


def test_all_engines_registered():
    """TradingEngine must now be registered alongside the other four engines."""
    names = _registered_names()
    missing = EXPECTED_ENGINES - names
    assert not missing, f"engines missing from PortfolioRiskManager: {missing}"


def test_portfolio_status_reports_at_least_five_engines():
    status = main._portfolio_risk.get_status()
    assert status.engine_count >= 5


def test_trading_adapter_reports_zero_when_no_open_orders():
    adapter = main._TradingAdapter()
    assert adapter.engine_name == "trading"
    # Default engine has no open orders → no exposure, no symbols.
    assert adapter.get_open_notional() == 0.0
    assert adapter.get_open_symbols() == []


def test_trading_adapter_notional_is_orders_times_per_order_notional():
    """Exposure estimate = open_orders * order_quote_notional (conservative)."""
    adapter = main._TradingAdapter()
    fake_status = {
        "state": {"open_orders": 3},
        "settings": {"order_quote_notional": 25.0, "symbol": "BTCUSDT"},
    }
    with patch.object(main._trading_engine, "status", return_value=fake_status):
        assert adapter.get_open_notional() == pytest.approx(75.0)
        assert adapter.get_open_symbols() == ["BTCUSDT"]


def test_trading_adapter_no_symbols_without_open_orders():
    adapter = main._TradingAdapter()
    fake_status = {
        "state": {"open_orders": 0},
        "settings": {"order_quote_notional": 25.0, "symbol": "BTCUSDT"},
    }
    with patch.object(main._trading_engine, "status", return_value=fake_status):
        assert adapter.get_open_symbols() == []


def test_trading_adapter_kill_switch_delegates_to_engine():
    adapter = main._TradingAdapter()
    with patch.object(main._trading_engine, "set_kill_switch") as mock_kill:
        adapter.trigger_kill_switch()
    mock_kill.assert_called_once_with(True)


def test_futures_arb_adapter_feeds_pnl_into_status():
    """_FuturesArbAdapter.get_status must expose stats.net_pnl_usdt for drawdown."""
    adapter = main._FuturesArbAdapter()
    status = adapter.get_status()
    assert "stats" in status
    assert "net_pnl_usdt" in status["stats"]
    assert isinstance(status["stats"]["net_pnl_usdt"], (int, float))
