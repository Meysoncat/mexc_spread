"""Tests for state persistence (StateStore, spread_capture, arbitrage)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from mexc_monitor.state_store import StateStore


# ─── StateStore unit tests ────────────────────────────────────────────────────


class TestStateStore:
    def test_save_and_load_roundtrip(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        data = {"version": 1, "positions": [{"symbol": "BTCUSDT", "qty": 0.5}]}
        assert store.save(data) is True
        loaded = store.load()
        assert loaded is not None
        assert loaded["version"] == 1
        assert loaded["positions"][0]["symbol"] == "BTCUSDT"

    def test_load_nonexistent_returns_none(self, tmp_path):
        store = StateStore(tmp_path / "nonexistent.json")
        assert store.load() is None

    def test_atomic_write_no_tmp_left(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        store.save({"x": 1})
        assert not (tmp_path / "state.tmp").exists()
        assert (tmp_path / "state.json").exists()

    def test_corrupt_file_returns_none(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{invalid json", encoding="utf-8")
        store = StateStore(path)
        assert store.load() is None

    def test_alert_callback_on_corruption(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("garbage", encoding="utf-8")
        errors: list[str] = []
        store = StateStore(path, alert_callback=errors.append)
        store.load()
        assert len(errors) == 1
        assert len(errors[0]) > 0  # Non-empty error message

    def test_clear_removes_file(self, tmp_path):
        path = tmp_path / "state.json"
        store = StateStore(path)
        store.save({"x": 1})
        assert path.exists()
        store.clear()
        assert not path.exists()

    def test_creates_parent_dirs(self, tmp_path):
        store = StateStore(tmp_path / "deep" / "nested" / "state.json")
        store.save({"x": 1})
        assert (tmp_path / "deep" / "nested" / "state.json").exists()

    def test_overwrite_existing(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        store.save({"version": 1})
        store.save({"version": 2})
        loaded = store.load()
        assert loaded["version"] == 2


# ─── SpreadCaptureEngine persistence ─────────────────────────────────────────


class TestSpreadCapturePersistence:
    def test_serialize_and_deserialize_stats(self, tmp_path):
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings, CaptureStats

        state_file = str(tmp_path / "sc_state.json")
        eng = SpreadCaptureEngine(CaptureSettings(state_file=state_file))
        eng._stats = CaptureStats(
            total_trades=10, winning_trades=7, losing_trades=3,
            net_pnl_usdt=15.5,
        )
        assert eng.serialize_state() is True

        eng2 = SpreadCaptureEngine(CaptureSettings(state_file=state_file))
        eng2.deserialize_state()
        assert eng2._stats.total_trades == 10
        assert eng2._stats.winning_trades == 7
        assert eng2._stats.net_pnl_usdt == pytest.approx(15.5)

    def test_deserialize_no_file_starts_fresh(self, tmp_path):
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings

        eng = SpreadCaptureEngine(CaptureSettings(
            state_file=str(tmp_path / "noexist.json"),
        ))
        eng.deserialize_state()
        assert eng._position.state == "idle"
        assert eng._stats.total_trades == 0

    def test_holding_position_restored(self, tmp_path):
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings, Position

        state_file = str(tmp_path / "sc_state.json")
        eng = SpreadCaptureEngine(CaptureSettings(state_file=state_file))
        eng._position = Position(
            state="holding",
            entry_price=100.0, entry_qty=0.5,
            entry_time_ms=12345, entry_spread_bps=10.0,
        )
        eng.serialize_state()

        eng2 = SpreadCaptureEngine(CaptureSettings(state_file=state_file))
        eng2.deserialize_state()
        assert eng2._position.state == "holding"
        assert eng2._position.entry_price == pytest.approx(100.0)
        assert eng2._position.entry_qty == pytest.approx(0.5)

    def test_pending_buy_cancelled_on_restart(self, tmp_path):
        """Pending orders are cancelled on restart (safer than assuming fill)."""
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings, Position

        state_file = str(tmp_path / "sc_state.json")
        eng = SpreadCaptureEngine(CaptureSettings(state_file=state_file))
        eng._position = Position(
            state="pending_buy",
            entry_price=100.0, entry_qty=0.5,
            entry_time_ms=12345, pending_since_ms=12345,
        )
        eng.serialize_state()

        eng2 = SpreadCaptureEngine(CaptureSettings(state_file=state_file))
        eng2.deserialize_state()
        assert eng2._position.state == "idle"


# ─── ArbitrageEngine persistence ──────────────────────────────────────────────


class TestArbitragePersistence:
    def test_serialize_and_deserialize_positions(self, tmp_path):
        from mexc_monitor.arbitrage.engine import ArbitrageEngine
        from mexc_monitor.arbitrage.models import ArbitrageSettings, ArbPosition

        state_file = str(tmp_path / "arb_state.json")
        eng = ArbitrageEngine(ArbitrageSettings(state_file=state_file))
        eng._positions["BTCUSDT"] = ArbPosition(
            symbol="BTCUSDT", state="open",
            buy_exchange="mexc", sell_exchange="asterdex",
            buy_price=100.0, sell_price=101.0,
            qty=0.5, notional_usdt=50.0,
            open_time_ms=12345, entry_basis_bps=15.0,
        )
        assert eng.serialize_state() is True

        eng2 = ArbitrageEngine(ArbitrageSettings(state_file=state_file))
        eng2.deserialize_state()
        positions = eng2.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTCUSDT"
        assert positions[0]["buy_price"] == pytest.approx(100.0)

    def test_deserialize_no_file_starts_fresh(self, tmp_path):
        from mexc_monitor.arbitrage.engine import ArbitrageEngine
        from mexc_monitor.arbitrage.models import ArbitrageSettings

        eng = ArbitrageEngine(ArbitrageSettings(
            state_file=str(tmp_path / "noexist.json"),
        ))
        eng.deserialize_state()
        assert len(eng._positions) == 0
