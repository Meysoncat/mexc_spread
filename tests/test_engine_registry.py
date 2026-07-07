"""Tests for EngineRegistry singleton and TradingEngine multi-exchange extensions."""

from __future__ import annotations

import os
import threading
from unittest.mock import patch

import pytest

from mexc_monitor.trading.engine import TradingEngine, TradingSettings
from mexc_monitor.trading.engine_registry import EngineRegistry
from mexc_monitor.trading.exchanges import Exchange, Market, OrderSide, OrderType


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the singleton before and after each test."""
    EngineRegistry.reset()
    yield
    EngineRegistry.reset()


class TestEngineRegistrySingleton:
    """Test singleton pattern and thread safety."""

    def test_singleton_returns_same_instance(self):
        r1 = EngineRegistry()
        r2 = EngineRegistry()
        assert r1 is r2

    def test_singleton_thread_safe(self):
        instances = []

        def create():
            instances.append(EngineRegistry())

        threads = [threading.Thread(target=create) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(inst is instances[0] for inst in instances)

    def test_reset_clears_singleton(self):
        r1 = EngineRegistry()
        EngineRegistry.reset()
        r2 = EngineRegistry()
        assert r1 is not r2


class TestGetOrCreate:
    """Test get_or_create idempotence and uniqueness."""

    def test_creates_engine_for_new_key(self):
        registry = EngineRegistry()
        engine = registry.get_or_create(Exchange.MEXC, Market.SPOT)
        assert engine is not None
        assert isinstance(engine, TradingEngine)

    def test_returns_same_engine_for_same_key(self):
        registry = EngineRegistry()
        e1 = registry.get_or_create(Exchange.MEXC, Market.SPOT)
        e2 = registry.get_or_create(Exchange.MEXC, Market.SPOT)
        assert e1 is e2

    def test_returns_different_engines_for_different_keys(self):
        registry = EngineRegistry()
        e1 = registry.get_or_create(Exchange.MEXC, Market.SPOT)
        e2 = registry.get_or_create(Exchange.BINANCE, Market.SPOT)
        assert e1 is not e2

    def test_different_markets_same_exchange_are_distinct(self):
        registry = EngineRegistry()
        e1 = registry.get_or_create(Exchange.MEXC, Market.SPOT)
        e2 = registry.get_or_create(Exchange.MEXC, Market.FUTURES)
        assert e1 is not e2


class TestListEngines:
    """Test list_engines metadata."""

    def test_empty_registry(self):
        registry = EngineRegistry()
        assert registry.list_engines() == []

    def test_lists_created_engines(self):
        registry = EngineRegistry()
        registry.get_or_create(Exchange.MEXC, Market.SPOT)
        registry.get_or_create(Exchange.BINANCE, Market.SPOT)
        engines = registry.list_engines()
        assert len(engines) == 2
        exchanges = {e["exchange"] for e in engines}
        assert exchanges == {"mexc", "binance"}
        for e in engines:
            assert "exchange" in e
            assert "market" in e
            assert "running" in e
            assert "mode" in e
            assert "symbol" in e


class TestGet:
    """Test get() returning engine or None."""

    def test_get_nonexistent_returns_none(self):
        registry = EngineRegistry()
        assert registry.get(Exchange.BINANCE, Market.SPOT) is None

    def test_get_existing_returns_engine(self):
        registry = EngineRegistry()
        created = registry.get_or_create(Exchange.MEXC, Market.SPOT)
        found = registry.get(Exchange.MEXC, Market.SPOT)
        assert found is created


class TestShutdownAll:
    """Test shutdown_all stops running engines."""

    def test_shutdown_all_stops_started_engine(self):
        registry = EngineRegistry()
        engine = registry.get_or_create(Exchange.MEXC, Market.SPOT)
        # Simulate a running state without actually starting the loop thread
        # (to avoid dependency on safe_load_snapshot in tests)
        with engine._lock:
            engine._state.running = True
            engine._stop_event.clear()
        # shutdown_all should set running=False
        registry.shutdown_all()
        assert engine._state.running is False

    def test_shutdown_all_on_empty_registry(self):
        registry = EngineRegistry()
        # Should not raise
        registry.shutdown_all()


class TestTradingEngineExtensions:
    """Test TradingEngine multi-exchange extensions."""

    def test_default_params_backward_compatible(self):
        """Engine without explicit params defaults to MEXC/SPOT."""
        engine = TradingEngine()
        assert engine._exchange == Exchange.MEXC
        assert engine._market == Market.SPOT
        assert engine._client is None  # No client injected

    def test_accepts_exchange_and_market(self):
        settings = TradingSettings()
        engine = TradingEngine(
            settings=settings,
            exchange=Exchange.BINANCE,
            market=Market.FUTURES,
        )
        assert engine._exchange == Exchange.BINANCE
        assert engine._market == Market.FUTURES

    def test_accepts_private_client(self):
        """Engine accepts a BasePrivateClient instance."""
        from mexc_monitor.trading.private_client_base import BasePrivateClient

        class FakeClient(BasePrivateClient):
            def _sign(self, params):
                return params

            def _get_api_key_header(self):
                return "X-FAKE"

            def place_order(self, request):
                pass

            def cancel_order(self, *, symbol, order_id=None, client_order_id=None):
                return {}

            def get_open_orders(self, symbol):
                return []

        client = FakeClient(api_key="k", api_secret="s", base_url="http://x")
        engine = TradingEngine(
            settings=TradingSettings(),
            private_client=client,
            exchange=Exchange.OKX,
            market=Market.SPOT,
        )
        assert engine._client is client

    def test_order_type_and_side_in_settings(self):
        settings = TradingSettings(order_type="MARKET", order_side="SELL")
        assert settings.order_type == "MARKET"
        assert settings.order_side == "SELL"

    def test_update_runtime_settings_order_type(self):
        engine = TradingEngine(settings=TradingSettings())
        engine.update_runtime_settings({"order_type": "MARKET"})
        assert engine._settings.order_type == "MARKET"

    def test_update_runtime_settings_order_side(self):
        engine = TradingEngine(settings=TradingSettings())
        engine.update_runtime_settings({"order_side": "SELL"})
        assert engine._settings.order_side == "SELL"

    def test_update_runtime_settings_invalid_order_type(self):
        engine = TradingEngine(settings=TradingSettings())
        with pytest.raises(ValueError, match="order_type"):
            engine.update_runtime_settings({"order_type": "FOK"})

    def test_update_runtime_settings_invalid_order_side(self):
        engine = TradingEngine(settings=TradingSettings())
        with pytest.raises(ValueError, match="order_side"):
            engine.update_runtime_settings({"order_side": "SHORT"})

    def test_independent_state_per_instance(self):
        """Two engines have independent state."""
        e1 = TradingEngine(settings=TradingSettings(kill_switch=False))
        e2 = TradingEngine(settings=TradingSettings(kill_switch=True))
        assert e1._state.kill_switch is False
        assert e2._state.kill_switch is True
        # Modifying one doesn't affect the other
        e1.set_kill_switch(True)
        assert e1._state.kill_switch is True
        assert e2._state.kill_switch is True  # unchanged (was already True)
        e2.set_kill_switch(False)
        assert e1._state.kill_switch is True
        assert e2._state.kill_switch is False


class TestPlaceOrderFromRow:
    """Test _place_order_from_row with MARKET vs LIMIT logic."""

    def _make_engine(self, **kwargs) -> TradingEngine:
        """Create engine with a unique temp events log path."""
        import tempfile
        import uuid

        log_path = os.path.join(
            tempfile.gettempdir(), f"test_events_{uuid.uuid4().hex}.jsonl"
        )
        defaults = {
            "mode": "paper",
            "kill_switch": False,
            "order_quote_notional": 100.0,
            "events_log_path": log_path,
        }
        defaults.update(kwargs)
        settings = TradingSettings(**defaults)
        return TradingEngine(settings=settings)

    def test_paper_market_order_no_price(self):
        """MARKET order in paper mode should log price=None."""
        engine = self._make_engine(order_type="MARKET", order_side="BUY")
        row = {"bid": 50000.0, "ask": 50100.0}
        engine._place_order_from_row(row)
        assert engine._state.orders_submitted == 1
        events = engine.read_recent_events(10)
        paper_events = [e for e in events if e.get("type") == "paper_order"]
        assert len(paper_events) == 1
        assert paper_events[0]["price"] is None
        assert paper_events[0]["order_type"] == "MARKET"
        assert paper_events[0]["side"] == "BUY"

    def test_paper_limit_order_has_price(self):
        """LIMIT order in paper mode should have a calculated price."""
        engine = self._make_engine(
            order_type="LIMIT", order_side="BUY", limit_price_offset_bps=0.0
        )
        row = {"bid": 50000.0, "ask": 50100.0}
        engine._place_order_from_row(row)
        events = engine.read_recent_events(10)
        paper_events = [e for e in events if e.get("type") == "paper_order"]
        assert len(paper_events) == 1
        assert paper_events[0]["price"] == 50000.0  # bid with 0 offset
        assert paper_events[0]["order_type"] == "LIMIT"

    def test_paper_sell_order(self):
        """SELL order uses correct side."""
        engine = self._make_engine(order_type="LIMIT", order_side="SELL")
        row = {"bid": 50000.0, "ask": 50100.0}
        engine._place_order_from_row(row)
        events = engine.read_recent_events(10)
        paper_events = [e for e in events if e.get("type") == "paper_order"]
        assert paper_events[0]["side"] == "SELL"
        # SELL LIMIT uses ask as reference
        assert paper_events[0]["price"] == 50100.0

    def test_paper_market_sell_order(self):
        """MARKET SELL order has no price."""
        engine = self._make_engine(order_type="MARKET", order_side="SELL")
        row = {"bid": 50000.0, "ask": 50100.0}
        engine._place_order_from_row(row)
        events = engine.read_recent_events(10)
        paper_events = [e for e in events if e.get("type") == "paper_order"]
        assert paper_events[0]["price"] is None
        assert paper_events[0]["side"] == "SELL"
