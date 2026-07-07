"""Unit tests for LeadLagWSManager.

Tests message parsing, validation, connection status tracking,
and helper methods without requiring real WebSocket connections.
"""

from __future__ import annotations

import time

import pytest

from mexc_monitor.lead_lag.config import LeadLagConfig
from mexc_monitor.lead_lag.models import PriceSnapshot
from mexc_monitor.lead_lag.price_buffer import PriceBuffer
from mexc_monitor.lead_lag.ws_manager import (
    ConnectionStatus,
    LeadLagWSManager,
    _ExchangeState,
    _STALE_THRESHOLD_SEC,
)


@pytest.fixture
def config() -> LeadLagConfig:
    """Create a test configuration."""
    return LeadLagConfig(
        enabled=True,
        leader_exchange="binance",
        lagger_exchanges=["mexc", "bybit", "okx"],
        symbols=["BTCUSDT", "ETHUSDT"],
        market="futures",
        ws_urls={
            "binance_futures": "wss://fstream.binance.com/ws",
            "mexc_futures": "wss://contract.mexc.com/edge",
            "bybit": "wss://stream.bybit.com/v5/public/linear",
            "okx": "wss://ws.okx.com:8443/ws/v5/public",
        },
    )


@pytest.fixture
def price_buffer() -> PriceBuffer:
    """Create a test price buffer."""
    return PriceBuffer(max_history_sec=60.0)


@pytest.fixture
def manager(config: LeadLagConfig, price_buffer: PriceBuffer) -> LeadLagWSManager:
    """Create a WS manager instance (not started)."""
    return LeadLagWSManager(config, price_buffer)


class TestInit:
    """Test initialization and URL resolution."""

    def test_resolves_all_exchange_urls(self, manager: LeadLagWSManager) -> None:
        """All configured exchanges should have resolved URLs."""
        urls = manager._exchange_urls
        assert "binance" in urls
        assert "mexc" in urls
        assert "bybit" in urls
        assert "okx" in urls

    def test_resolves_market_specific_url(self, manager: LeadLagWSManager) -> None:
        """Should prefer market-specific URL (binance_futures over binance)."""
        assert manager._exchange_urls["binance"] == "wss://fstream.binance.com/ws"

    def test_resolves_generic_url(self, manager: LeadLagWSManager) -> None:
        """Should fall back to generic exchange URL when no market-specific one exists."""
        assert manager._exchange_urls["bybit"] == "wss://stream.bybit.com/v5/public/linear"

    def test_initial_states_disconnected(self, manager: LeadLagWSManager) -> None:
        """All exchanges should start as disconnected."""
        for state in manager._states.values():
            assert state.status == ConnectionStatus.DISCONNECTED

    def test_not_running_initially(self, manager: LeadLagWSManager) -> None:
        """Manager should not be running before start()."""
        assert not manager.is_running()


class TestConnectionStatus:
    """Test connection_status() method."""

    def test_all_disconnected_initially(self, manager: LeadLagWSManager) -> None:
        """All exchanges should report disconnected initially."""
        status = manager.connection_status()
        for exchange_status in status.values():
            assert exchange_status["status"] == "disconnected"

    def test_connected_status(self, manager: LeadLagWSManager) -> None:
        """Exchange should report connected when last message is recent."""
        state = manager._states["binance"]
        state.status = ConnectionStatus.CONNECTED
        state.last_message_mono = time.monotonic()
        state.last_message_ms = int(time.time() * 1000)

        status = manager.connection_status()
        assert status["binance"]["status"] == "connected"

    def test_stale_detection(self, manager: LeadLagWSManager) -> None:
        """Exchange should become stale when no data for > 5 seconds."""
        state = manager._states["binance"]
        state.status = ConnectionStatus.CONNECTED
        state.last_message_mono = time.monotonic() - _STALE_THRESHOLD_SEC - 1.0
        state.last_message_ms = int(time.time() * 1000) - 6000

        status = manager.connection_status()
        assert status["binance"]["status"] == "stale"

    def test_discarded_count_reported(self, manager: LeadLagWSManager) -> None:
        """Discarded count should be reported in status."""
        state = manager._states["mexc"]
        state.discarded_count = 42

        status = manager.connection_status()
        assert status["mexc"]["discarded_count"] == 42


class TestGetActiveExchanges:
    """Test get_active_exchanges() method."""

    def test_no_active_initially(self, manager: LeadLagWSManager) -> None:
        """No exchanges should be active initially."""
        assert manager.get_active_exchanges() == []

    def test_connected_exchange_is_active(self, manager: LeadLagWSManager) -> None:
        """Connected exchange with recent data should be active."""
        state = manager._states["binance"]
        state.status = ConnectionStatus.CONNECTED
        state.last_message_mono = time.monotonic()

        active = manager.get_active_exchanges()
        assert "binance" in active

    def test_stale_exchange_not_active(self, manager: LeadLagWSManager) -> None:
        """Stale exchange should not be in active list."""
        state = manager._states["binance"]
        state.status = ConnectionStatus.CONNECTED
        state.last_message_mono = time.monotonic() - _STALE_THRESHOLD_SEC - 1.0

        active = manager.get_active_exchanges()
        assert "binance" not in active


class TestMessageParsing:
    """Test message parsing for each exchange."""

    def test_parse_binance_valid(self, manager: LeadLagWSManager) -> None:
        """Valid Binance bookTicker should produce a PriceSnapshot."""
        data = {"s": "BTCUSDT", "b": "67500.50", "a": "67501.00"}
        snapshot = manager._parse_binance(data)

        assert snapshot is not None
        assert snapshot.exchange == "binance"
        assert snapshot.symbol == "BTCUSDT"
        assert snapshot.bid == 67500.50
        assert snapshot.ask == 67501.00
        assert snapshot.mid == pytest.approx((67500.50 + 67501.00) / 2)
        assert snapshot.timestamp_ms > 0

    def test_parse_binance_missing_symbol(self, manager: LeadLagWSManager) -> None:
        """Missing symbol should be discarded."""
        data = {"b": "67500.50", "a": "67501.00"}
        snapshot = manager._parse_binance(data)
        assert snapshot is None
        assert manager._states["binance"].discarded_count == 1

    def test_parse_binance_missing_bid(self, manager: LeadLagWSManager) -> None:
        """Missing bid should be discarded."""
        data = {"s": "BTCUSDT", "a": "67501.00"}
        snapshot = manager._parse_binance(data)
        assert snapshot is None
        assert manager._states["binance"].discarded_count == 1

    def test_parse_binance_invalid_bid(self, manager: LeadLagWSManager) -> None:
        """Bid <= 0 should be discarded."""
        data = {"s": "BTCUSDT", "b": "0", "a": "67501.00"}
        snapshot = manager._parse_binance(data)
        assert snapshot is None
        assert manager._states["binance"].discarded_count == 1

    def test_parse_binance_ask_less_than_bid(self, manager: LeadLagWSManager) -> None:
        """ask < bid should be discarded."""
        data = {"s": "BTCUSDT", "b": "67501.00", "a": "67500.00"}
        snapshot = manager._parse_binance(data)
        assert snapshot is None
        assert manager._states["binance"].discarded_count == 1

    def test_parse_binance_unknown_symbol(self, manager: LeadLagWSManager) -> None:
        """Symbol not in config should be filtered out (not discarded)."""
        data = {"s": "DOGEUSDT", "b": "0.10", "a": "0.11"}
        snapshot = manager._parse_binance(data)
        assert snapshot is None
        # Not counted as discarded since it's valid but not monitored
        assert manager._states["binance"].discarded_count == 0

    def test_parse_bybit_valid(self, manager: LeadLagWSManager) -> None:
        """Valid Bybit tickers message should produce a PriceSnapshot."""
        data = {
            "topic": "tickers.BTCUSDT",
            "data": {
                "symbol": "BTCUSDT",
                "bid1Price": "67500.50",
                "ask1Price": "67501.00",
            },
        }
        snapshot = manager._parse_bybit(data)

        assert snapshot is not None
        assert snapshot.exchange == "bybit"
        assert snapshot.symbol == "BTCUSDT"
        assert snapshot.bid == 67500.50
        assert snapshot.ask == 67501.00

    def test_parse_bybit_wrong_topic(self, manager: LeadLagWSManager) -> None:
        """Non-tickers topic should be ignored."""
        data = {"topic": "orderbook.1.BTCUSDT", "data": {}}
        snapshot = manager._parse_bybit(data)
        assert snapshot is None

    def test_parse_bybit_invalid_values(self, manager: LeadLagWSManager) -> None:
        """Invalid bid/ask values should be discarded."""
        data = {
            "topic": "tickers.BTCUSDT",
            "data": {
                "symbol": "BTCUSDT",
                "bid1Price": "-1",
                "ask1Price": "67501.00",
            },
        }
        snapshot = manager._parse_bybit(data)
        assert snapshot is None
        assert manager._states["bybit"].discarded_count == 1

    def test_parse_okx_valid(self, manager: LeadLagWSManager) -> None:
        """Valid OKX tickers message should produce a PriceSnapshot."""
        data = {
            "arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "bidPx": "67500.50",
                    "askPx": "67501.00",
                }
            ],
        }
        snapshot = manager._parse_okx(data)

        assert snapshot is not None
        assert snapshot.exchange == "okx"
        assert snapshot.symbol == "BTCUSDT"
        assert snapshot.bid == 67500.50
        assert snapshot.ask == 67501.00

    def test_parse_okx_no_data(self, manager: LeadLagWSManager) -> None:
        """OKX message without data field should be ignored."""
        data = {"arg": {"channel": "tickers"}}
        snapshot = manager._parse_okx(data)
        assert snapshot is None

    def test_parse_okx_invalid_values(self, manager: LeadLagWSManager) -> None:
        """OKX message with invalid bid/ask should be discarded."""
        data = {
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "bidPx": "67501.00",
                    "askPx": "67500.00",  # ask < bid
                }
            ],
        }
        snapshot = manager._parse_okx(data)
        assert snapshot is None
        assert manager._states["okx"].discarded_count == 1

    def test_parse_mexc_futures_valid(self, manager: LeadLagWSManager) -> None:
        """Valid MEXC futures tickers message should produce PriceSnapshots."""
        data = {
            "channel": "push.tickers",
            "data": [
                {"symbol": "BTC_USDT", "bid1": "67500.50", "ask1": "67501.00"},
                {"symbol": "ETH_USDT", "bid1": "3500.00", "ask1": "3500.50"},
            ],
        }
        snapshots = manager._parse_mexc(data)

        assert len(snapshots) == 2
        assert snapshots[0].exchange == "mexc"
        assert snapshots[0].symbol == "BTCUSDT"
        assert snapshots[1].symbol == "ETHUSDT"

    def test_parse_mexc_spot_valid(self, manager: LeadLagWSManager) -> None:
        """Valid MEXC spot bookTicker should produce a PriceSnapshot."""
        data = {
            "d": {"s": "BTCUSDT", "b": "67500.50", "a": "67501.00"},
            "c": "spot@public.bookTicker.v3.api@BTCUSDT",
        }
        snapshots = manager._parse_mexc(data)

        assert len(snapshots) == 1
        assert snapshots[0].exchange == "mexc"
        assert snapshots[0].symbol == "BTCUSDT"


class TestProcessMessage:
    """Test full message processing pipeline."""

    def test_valid_message_updates_buffer(
        self, manager: LeadLagWSManager, price_buffer: PriceBuffer
    ) -> None:
        """Valid message should update the price buffer."""
        state = manager._states["binance"]
        raw = '{"s": "BTCUSDT", "b": "67500.50", "a": "67501.00"}'

        manager._process_message("binance", raw, state)

        latest = price_buffer.get_latest("binance", "BTCUSDT")
        assert latest is not None
        assert latest.mid == pytest.approx((67500.50 + 67501.00) / 2)

    def test_valid_message_updates_state(self, manager: LeadLagWSManager) -> None:
        """Valid message should update exchange state to connected."""
        state = manager._states["binance"]
        state.status = ConnectionStatus.STALE
        raw = '{"s": "BTCUSDT", "b": "67500.50", "a": "67501.00"}'

        manager._process_message("binance", raw, state)

        assert state.status == ConnectionStatus.CONNECTED
        assert state.last_message_mono > 0
        assert state.last_message_ms > 0

    def test_invalid_json_ignored(self, manager: LeadLagWSManager) -> None:
        """Invalid JSON should be silently ignored."""
        state = manager._states["binance"]
        manager._process_message("binance", "not json", state)
        # No crash, no state change
        assert state.status == ConnectionStatus.DISCONNECTED

    def test_stale_to_connected_on_valid_message(
        self, manager: LeadLagWSManager
    ) -> None:
        """Stale exchange should return to connected on valid message (Req 1.6)."""
        state = manager._states["binance"]
        state.status = ConnectionStatus.STALE
        state.last_message_mono = time.monotonic() - 10.0

        raw = '{"s": "ETHUSDT", "b": "3500.00", "a": "3500.50"}'
        manager._process_message("binance", raw, state)

        assert state.status == ConnectionStatus.CONNECTED


class TestHelpers:
    """Test helper methods."""

    def test_validate_bid_ask_valid(self) -> None:
        """Valid bid/ask should pass."""
        assert LeadLagWSManager._validate_bid_ask(100.0, 100.5) is True

    def test_validate_bid_ask_equal(self) -> None:
        """bid == ask should be valid (ask >= bid)."""
        assert LeadLagWSManager._validate_bid_ask(100.0, 100.0) is True

    def test_validate_bid_ask_zero_bid(self) -> None:
        """bid == 0 should be invalid."""
        assert LeadLagWSManager._validate_bid_ask(0.0, 100.0) is False

    def test_validate_bid_ask_negative_bid(self) -> None:
        """Negative bid should be invalid."""
        assert LeadLagWSManager._validate_bid_ask(-1.0, 100.0) is False

    def test_validate_bid_ask_ask_less_than_bid(self) -> None:
        """ask < bid should be invalid."""
        assert LeadLagWSManager._validate_bid_ask(100.0, 99.0) is False

    def test_symbol_to_okx_inst_id_futures(self) -> None:
        """BTCUSDT -> BTC-USDT-SWAP for futures."""
        result = LeadLagWSManager._symbol_to_okx_inst_id("BTCUSDT", "futures")
        assert result == "BTC-USDT-SWAP"

    def test_symbol_to_okx_inst_id_spot(self) -> None:
        """BTCUSDT -> BTC-USDT for spot."""
        result = LeadLagWSManager._symbol_to_okx_inst_id("BTCUSDT", "spot")
        assert result == "BTC-USDT"

    def test_okx_inst_id_to_symbol_swap(self) -> None:
        """BTC-USDT-SWAP -> BTCUSDT."""
        result = LeadLagWSManager._okx_inst_id_to_symbol("BTC-USDT-SWAP")
        assert result == "BTCUSDT"

    def test_okx_inst_id_to_symbol_spot(self) -> None:
        """BTC-USDT -> BTCUSDT."""
        result = LeadLagWSManager._okx_inst_id_to_symbol("BTC-USDT")
        assert result == "BTCUSDT"


class TestStartStop:
    """Test start/stop lifecycle."""

    def test_start_sets_running(self, manager: LeadLagWSManager) -> None:
        """start() should set is_running to True."""
        manager.start()
        try:
            assert manager.is_running()
        finally:
            manager.stop()

    def test_stop_clears_running(self, manager: LeadLagWSManager) -> None:
        """stop() should set is_running to False."""
        manager.start()
        manager.stop()
        assert not manager.is_running()

    def test_start_idempotent(self, manager: LeadLagWSManager) -> None:
        """Calling start() twice should be safe."""
        manager.start()
        try:
            manager.start()  # Should not raise
            assert manager.is_running()
        finally:
            manager.stop()

    def test_stop_idempotent(self, manager: LeadLagWSManager) -> None:
        """Calling stop() when not running should be safe."""
        manager.stop()  # Should not raise
        assert not manager.is_running()

    def test_stop_marks_all_disconnected(self, manager: LeadLagWSManager) -> None:
        """stop() should mark all exchanges as disconnected."""
        manager.start()
        # Simulate a connected state
        manager._states["binance"].status = ConnectionStatus.CONNECTED
        manager.stop()

        for state in manager._states.values():
            assert state.status == ConnectionStatus.DISCONNECTED
