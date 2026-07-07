"""Unit tests for mexc_monitor.trading.settings_loader module."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from mexc_monitor.trading.exchanges import Exchange, Market
from mexc_monitor.trading.settings_loader import (
    TradingSettings,
    _parse_bool,
    load_trading_settings_for_exchange,
)


class TestParseBool:
    @pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "Yes", "on", "ON"])
    def test_truthy_values(self, value: str):
        assert _parse_bool(value) is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", "anything", ""])
    def test_falsy_values(self, value: str):
        assert _parse_bool(value) is False


class TestTradingSettingsDefaults:
    def test_default_values(self):
        """All defaults should match the design document."""
        s = TradingSettings()
        assert s.enabled is False
        assert s.mode == "paper"
        assert s.symbol == "BTCUSDT"
        assert s.order_type == "LIMIT"
        assert s.order_side == "BUY"
        assert s.min_net_spread_bps == -2.0
        assert s.order_quote_notional == 25.0
        assert s.limit_price_offset_bps == 0.0
        assert s.loop_interval_sec == 3.0
        assert s.max_orders_per_day == 20
        assert s.max_open_orders == 3
        assert s.max_consecutive_errors == 5
        assert s.kill_switch is True
        assert s.api_key == ""
        assert s.api_secret == ""
        assert s.recv_window_ms == 5_000
        assert s.events_log_path == "data/trading_events.jsonl"

    def test_frozen(self):
        s = TradingSettings()
        with pytest.raises(Exception):
            s.symbol = "ETHUSDT"  # type: ignore[misc]


class TestLoadTradingSettingsForExchange:
    """Test load_trading_settings_for_exchange with env var priority."""

    def test_defaults_when_no_env_vars(self):
        """When no env vars set, should return hardcoded defaults."""
        with patch.dict(os.environ, {}, clear=True):
            s = load_trading_settings_for_exchange(Exchange.BINANCE, Market.SPOT)
        assert s.enabled is False
        assert s.mode == "paper"
        assert s.symbol == "BTCUSDT"
        assert s.order_type == "LIMIT"
        assert s.order_side == "BUY"
        assert s.min_net_spread_bps == -2.0
        assert s.order_quote_notional == 25.0
        assert s.limit_price_offset_bps == 0.0
        assert s.loop_interval_sec == 3.0
        assert s.max_orders_per_day == 20
        assert s.max_open_orders == 3
        assert s.max_consecutive_errors == 5
        assert s.kill_switch is True
        assert s.events_log_path == "data/trading_events_binance_spot.jsonl"

    def test_exchange_specific_takes_priority(self):
        """Exchange-specific env var should override MEXC fallback."""
        env = {
            "BINANCE_TRADING_SYMBOL": "ETHUSDT",
            "MEXC_TRADING_SYMBOL": "BTCUSDT",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.BINANCE, Market.SPOT)
        assert s.symbol == "ETHUSDT"

    def test_mexc_fallback_when_exchange_specific_not_set(self):
        """MEXC env var should be used when exchange-specific is not set."""
        env = {
            "MEXC_TRADING_SYMBOL": "SOLUSDT",
            "MEXC_TRADING_MODE": "live",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.BYBIT, Market.SPOT)
        assert s.symbol == "SOLUSDT"
        assert s.mode == "live"

    def test_mexc_exchange_uses_own_prefix(self):
        """For MEXC exchange, the prefix IS MEXC_TRADING_ (no double fallback)."""
        env = {
            "MEXC_TRADING_SYMBOL": "XRPUSDT",
            "MEXC_TRADING_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.MEXC, Market.SPOT)
        assert s.symbol == "XRPUSDT"
        assert s.enabled is True

    def test_events_log_path_includes_exchange_and_market(self):
        """Default events_log_path should include exchange and market."""
        with patch.dict(os.environ, {}, clear=True):
            s = load_trading_settings_for_exchange(Exchange.OKX, Market.FUTURES)
        assert s.events_log_path == "data/trading_events_okx_futures.jsonl"

    def test_events_log_path_from_env(self):
        """Custom events_log_path from env var."""
        env = {"GATEIO_TRADING_EVENTS_LOG_PATH": "/tmp/gateio.jsonl"}
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.GATEIO, Market.SPOT)
        assert s.events_log_path == "/tmp/gateio.jsonl"

    def test_all_numeric_fields_parsed(self):
        """Numeric fields should be correctly parsed from env vars."""
        env = {
            "HTX_TRADING_MIN_NET_SPREAD_BPS": "5.5",
            "HTX_TRADING_ORDER_QUOTE_NOTIONAL": "100.0",
            "HTX_TRADING_LIMIT_PRICE_OFFSET_BPS": "1.5",
            "HTX_TRADING_LOOP_INTERVAL_SEC": "10.0",
            "HTX_TRADING_MAX_ORDERS_PER_DAY": "50",
            "HTX_TRADING_MAX_OPEN_ORDERS": "5",
            "HTX_TRADING_MAX_CONSECUTIVE_ERRORS": "10",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.HTX, Market.SPOT)
        assert s.min_net_spread_bps == 5.5
        assert s.order_quote_notional == 100.0
        assert s.limit_price_offset_bps == 1.5
        assert s.loop_interval_sec == 10.0
        assert s.max_orders_per_day == 50
        assert s.max_open_orders == 5
        assert s.max_consecutive_errors == 10

    def test_boolean_fields_parsed(self):
        """Boolean fields should be correctly parsed from env vars."""
        env = {
            "BITGET_TRADING_ENABLED": "true",
            "BITGET_TRADING_KILL_SWITCH": "false",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.BITGET, Market.SPOT)
        assert s.enabled is True
        assert s.kill_switch is False

    def test_empty_env_var_uses_fallback(self):
        """Empty string env var should be treated as not set."""
        env = {
            "BYBIT_TRADING_SYMBOL": "",
            "MEXC_TRADING_SYMBOL": "ADAUSDT",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.BYBIT, Market.SPOT)
        assert s.symbol == "ADAUSDT"

    def test_whitespace_only_env_var_uses_fallback(self):
        """Whitespace-only env var should be treated as not set."""
        env = {
            "OKX_TRADING_MODE": "   ",
            "MEXC_TRADING_MODE": "live",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.OKX, Market.SPOT)
        assert s.mode == "live"

    def test_symbol_uppercased(self):
        """Symbol should be uppercased regardless of input."""
        env = {"BINANCE_TRADING_SYMBOL": "ethusdt"}
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.BINANCE, Market.SPOT)
        assert s.symbol == "ETHUSDT"

    def test_order_type_uppercased(self):
        """Order type should be uppercased."""
        env = {"BINANCE_TRADING_ORDER_TYPE": "market"}
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.BINANCE, Market.SPOT)
        assert s.order_type == "MARKET"

    def test_order_side_uppercased(self):
        """Order side should be uppercased."""
        env = {"BINANCE_TRADING_ORDER_SIDE": "sell"}
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.BINANCE, Market.SPOT)
        assert s.order_side == "SELL"

    def test_credentials_always_empty(self):
        """Credentials should always be empty (loaded via client factory)."""
        env = {
            "BINANCE_API_KEY": "my-key",
            "BINANCE_API_SECRET": "my-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            s = load_trading_settings_for_exchange(Exchange.BINANCE, Market.SPOT)
        assert s.api_key == ""
        assert s.api_secret == ""
        assert s.recv_window_ms == 5000

    def test_all_exchanges_supported(self):
        """Should work for all supported exchanges."""
        with patch.dict(os.environ, {}, clear=True):
            for exchange in Exchange:
                s = load_trading_settings_for_exchange(exchange, Market.SPOT)
                assert s.events_log_path == f"data/trading_events_{exchange.value}_spot.jsonl"
