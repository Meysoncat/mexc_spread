"""
Unit tests for futures_arb PositionManager.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mexc_monitor.futures_arb.models import FuturesArbPosition, FuturesArbStats
from mexc_monitor.futures_arb.position_manager import PositionManager


def _make_position(
    pos_id: str = "test-pos-1",
    symbol: str = "BTCUSDT",
    exchange_combo: str = "mexc_spot+mexc_futures",
    strategy: str = "cash_and_carry",
    state: str = "open",
    spot_side: str = "buy",
    spot_entry_price: float = 50000.0,
    spot_qty: float = 0.02,
    futures_side: str = "short",
    futures_entry_price: float = 50100.0,
    futures_qty: float = 0.02,
    futures_leverage: int = 3,
    notional_usdt: float = 1000.0,
    entry_basis_bps: float = 20.0,
    open_time_ms: int = 0,
    entry_fees: float = 0.3,
    **kwargs,
) -> FuturesArbPosition:
    """Helper to create a test position."""
    if open_time_ms == 0:
        open_time_ms = int(time.time() * 1000) - 3600_000  # 1 hour ago
    return FuturesArbPosition(
        id=pos_id,
        symbol=symbol,
        exchange_combo=exchange_combo,
        strategy=strategy,
        state=state,
        spot_side=spot_side,
        spot_entry_price=spot_entry_price,
        spot_qty=spot_qty,
        futures_side=futures_side,
        futures_entry_price=futures_entry_price,
        futures_qty=futures_qty,
        futures_leverage=futures_leverage,
        notional_usdt=notional_usdt,
        entry_basis_bps=entry_basis_bps,
        open_time_ms=open_time_ms,
        entry_fees=entry_fees,
        **kwargs,
    )


class TestOpenPosition:
    """Tests for PositionManager.open_position()."""

    def test_open_adds_to_list(self, tmp_path):
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        pos = _make_position()
        pm.open_position(pos)
        assert len(pm.get_open_positions()) == 1
        assert pm.get_open_positions()[0].id == "test-pos-1"

    def test_open_multiple_positions(self, tmp_path):
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        pm.open_position(_make_position(pos_id="pos-1"))
        pm.open_position(_make_position(pos_id="pos-2"))
        pm.open_position(_make_position(pos_id="pos-3"))
        assert len(pm.get_open_positions()) == 3


class TestClosePosition:
    """Tests for PositionManager.close_position()."""

    def test_close_moves_to_closed(self, tmp_path):
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        pos = _make_position()
        pm.open_position(pos)

        closed = pm.close_position(
            "test-pos-1",
            reason="basis_converged",
            exit_basis_bps=5.0,
            spot_exit_price=50050.0,
            futures_exit_price=50060.0,
        )

        assert closed is not None
        assert closed.state == "closed"
        assert closed.close_reason == "basis_converged"
        assert closed.exit_basis_bps == 5.0
        assert closed.close_time_ms > 0
        assert len(pm.get_open_positions()) == 0
        assert len(pm.get_closed_positions()) == 1

    def test_close_nonexistent_returns_none(self, tmp_path):
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        result = pm.close_position("nonexistent", reason="test")
        assert result is None

    def test_close_computes_basis_pnl_cash_and_carry(self, tmp_path):
        """Cash-and-carry: buy spot + short futures."""
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        pos = _make_position(
            spot_entry_price=50000.0,
            futures_entry_price=50100.0,
            spot_qty=0.02,
            futures_qty=0.02,
            spot_side="buy",
            futures_side="short",
            entry_fees=0.3,
        )
        pm.open_position(pos)

        closed = pm.close_position(
            "test-pos-1",
            reason="target_reached",
            spot_exit_price=50050.0,
            futures_exit_price=50050.0,
        )

        # spot_pnl = (50050 - 50000) * 0.02 = 1.0
        # futures_pnl = (50100 - 50050) * 0.02 = 1.0
        # basis_pnl = 2.0
        assert closed is not None
        assert abs(closed.basis_pnl - 2.0) < 1e-10

    def test_close_computes_basis_pnl_reverse(self, tmp_path):
        """Reverse cash-and-carry: sell spot + long futures."""
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        pos = _make_position(
            spot_entry_price=50100.0,
            futures_entry_price=50000.0,
            spot_qty=0.02,
            futures_qty=0.02,
            spot_side="sell",
            futures_side="long",
            entry_fees=0.3,
        )
        pm.open_position(pos)

        closed = pm.close_position(
            "test-pos-1",
            reason="basis_converged",
            spot_exit_price=50050.0,
            futures_exit_price=50050.0,
        )

        # spot_pnl = (50100 - 50050) * 0.02 = 1.0
        # futures_pnl = (50050 - 50000) * 0.02 = 1.0
        # basis_pnl = 2.0
        assert closed is not None
        assert abs(closed.basis_pnl - 2.0) < 1e-10


class TestUpdateFunding:
    """Tests for PositionManager.update_funding()."""

    def test_update_funding_adds_to_cumulative(self, tmp_path):
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        pos = _make_position(cumulative_funding=0.0)
        pm.open_position(pos)

        pm.update_funding("test-pos-1", 0.5)
        positions = pm.get_open_positions()
        assert abs(positions[0].cumulative_funding - 0.5) < 1e-10

        pm.update_funding("test-pos-1", 0.3)
        positions = pm.get_open_positions()
        assert abs(positions[0].cumulative_funding - 0.8) < 1e-10

    def test_update_funding_nonexistent_does_nothing(self, tmp_path):
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        # Should not raise
        pm.update_funding("nonexistent", 1.0)

    def test_update_funding_recomputes_total_pnl(self, tmp_path):
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        pos = _make_position(
            basis_pnl=1.0,
            cumulative_funding=0.0,
            entry_fees=0.3,
            exit_fees=0.0,
        )
        pm.open_position(pos)

        pm.update_funding("test-pos-1", 0.5)
        positions = pm.get_open_positions()
        # total_pnl = 1.0 + 0.5 - 0.3 - 0.0 = 1.2
        assert abs(positions[0].total_pnl - 1.2) < 1e-10


class TestPNLComputation:
    """Tests for PNL computation formulas."""

    def test_total_pnl_formula(self):
        """total_pnl = basis_pnl + cumulative_funding - entry_fees - exit_fees"""
        pos = _make_position(
            basis_pnl=5.0,
            cumulative_funding=2.0,
            entry_fees=0.3,
            exit_fees=0.2,
        )
        result = PositionManager.compute_total_pnl(pos)
        # 5.0 + 2.0 - 0.3 - 0.2 = 6.5
        assert abs(result - 6.5) < 1e-10

    def test_annualized_return_formula(self):
        """annualized_return = (total_pnl / notional) * (365*24*3600 / hold_seconds) * 100"""
        now_ms = int(time.time() * 1000)
        one_day_ms = 24 * 3600 * 1000
        pos = _make_position(
            notional_usdt=1000.0,
            basis_pnl=1.0,
            cumulative_funding=0.5,
            entry_fees=0.1,
            exit_fees=0.1,
            open_time_ms=now_ms - one_day_ms,
        )
        pos.close_time_ms = now_ms

        result = PositionManager.compute_annualized_return(pos)
        # total_pnl = 1.0 + 0.5 - 0.1 - 0.1 = 1.3
        # hold_seconds = 86400
        # annualized = (1.3 / 1000) * (365 * 24 * 3600 / 86400) * 100
        #            = 0.0013 * 365 * 100 = 47.45
        expected = (1.3 / 1000.0) * (365 * 24 * 3600 / 86400.0) * 100
        assert abs(result - expected) < 1e-6

    def test_annualized_return_zero_hold(self):
        """Zero hold time returns 0."""
        pos = _make_position(open_time_ms=1000)
        pos.close_time_ms = 1000  # same time
        result = PositionManager.compute_annualized_return(pos)
        assert result == 0.0

    def test_annualized_return_zero_notional(self):
        """Zero notional returns 0."""
        pos = _make_position(notional_usdt=0.0)
        pos.close_time_ms = pos.open_time_ms + 86400_000
        result = PositionManager.compute_annualized_return(pos)
        assert result == 0.0

    def test_net_pnl_bps_formula(self):
        """net_pnl_bps = total_pnl / notional * 10000"""
        pos = _make_position(
            notional_usdt=1000.0,
            basis_pnl=2.0,
            cumulative_funding=1.0,
            entry_fees=0.2,
            exit_fees=0.1,
        )
        result = PositionManager.compute_net_pnl_bps(pos)
        # total_pnl = 2.0 + 1.0 - 0.2 - 0.1 = 2.7
        # net_pnl_bps = 2.7 / 1000 * 10000 = 27.0
        assert abs(result - 27.0) < 1e-10

    def test_net_pnl_bps_zero_notional(self):
        """Zero notional returns 0."""
        pos = _make_position(notional_usdt=0.0)
        result = PositionManager.compute_net_pnl_bps(pos)
        assert result == 0.0


class TestGetStats:
    """Tests for PositionManager.get_stats()."""

    def test_empty_stats(self, tmp_path):
        pm = PositionManager(state_file=str(tmp_path / "state.json"))
        stats = pm.get_stats()
        assert stats.total_trades == 0
        assert stats.win_rate == 0.0

    def test_stats_with_closed_positions(self, tmp_path):
        pm = PositionManager(state_file=str(tmp_path / "state.json"))

        # Create and close two positions
        now_ms = int(time.time() * 1000)

        pos1 = _make_position(
            pos_id="pos-1",
            notional_usdt=1000.0,
            basis_pnl=3.0,
            cumulative_funding=1.0,
            entry_fees=0.2,
            exit_fees=0.1,
            open_time_ms=now_ms - 7200_000,
        )
        pm.open_position(pos1)
        pm.close_position("pos-1", reason="target_reached")

        pos2 = _make_position(
            pos_id="pos-2",
            notional_usdt=1000.0,
            basis_pnl=-1.0,
            cumulative_funding=0.5,
            entry_fees=0.2,
            exit_fees=0.1,
            open_time_ms=now_ms - 3600_000,
        )
        pm.open_position(pos2)
        pm.close_position("pos-2", reason="stop_loss")

        stats = pm.get_stats()
        assert stats.total_trades == 2
        assert stats.winning_trades == 1  # pos1: 3.0+1.0-0.2-0.1 = 3.7 > 0
        assert stats.losing_trades == 1  # pos2: -1.0+0.5-0.2-0.1 = -0.8 <= 0
        assert stats.win_rate == 0.5
        assert stats.total_funding_earned == 1.5  # 1.0 + 0.5


class TestSerializeDeserialize:
    """Tests for state serialization and deserialization."""

    def test_serialize_creates_file(self, tmp_path):
        state_file = tmp_path / "state.json"
        pm = PositionManager(state_file=str(state_file))
        pos = _make_position()
        pm.open_position(pos)
        pm.serialize_state()

        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["version"] == 1
        assert len(data["open_positions"]) == 1
        assert data["open_positions"][0]["id"] == "test-pos-1"

    def test_deserialize_restores_positions(self, tmp_path):
        state_file = tmp_path / "state.json"

        # First: serialize
        pm1 = PositionManager(state_file=str(state_file))
        pm1.open_position(_make_position(pos_id="pos-a"))
        pm1.open_position(_make_position(pos_id="pos-b"))
        pm1.serialize_state()

        # Second: deserialize in new instance
        pm2 = PositionManager(state_file=str(state_file))
        pm2.deserialize_state()

        positions = pm2.get_open_positions()
        assert len(positions) == 2
        ids = {p.id for p in positions}
        assert ids == {"pos-a", "pos-b"}

    def test_round_trip_preserves_all_fields(self, tmp_path):
        """Serialization round-trip preserves all position fields."""
        state_file = tmp_path / "state.json"

        pos = _make_position(
            pos_id="round-trip-test",
            symbol="ETHUSDT",
            exchange_combo="mexc_spot+asterdex_perp",
            strategy="funding_arb",
            state="open",
            spot_side="buy",
            spot_entry_price=3000.0,
            spot_qty=0.5,
            futures_side="short",
            futures_entry_price=3010.0,
            futures_qty=0.5,
            futures_leverage=5,
            notional_usdt=1500.0,
            entry_basis_bps=33.3,
            open_time_ms=1700000000000,
            entry_fees=0.45,
            cumulative_funding=1.23,
            basis_pnl=2.5,
        )

        pm1 = PositionManager(state_file=str(state_file))
        pm1.open_position(pos)
        pm1.serialize_state()

        pm2 = PositionManager(state_file=str(state_file))
        pm2.deserialize_state()

        restored = pm2.get_open_positions()[0]
        assert restored.id == pos.id
        assert restored.symbol == pos.symbol
        assert restored.exchange_combo == pos.exchange_combo
        assert restored.strategy == pos.strategy
        assert restored.state == pos.state
        assert restored.spot_side == pos.spot_side
        assert abs(restored.spot_entry_price - pos.spot_entry_price) < 1e-10
        assert abs(restored.spot_qty - pos.spot_qty) < 1e-10
        assert restored.futures_side == pos.futures_side
        assert abs(restored.futures_entry_price - pos.futures_entry_price) < 1e-10
        assert abs(restored.futures_qty - pos.futures_qty) < 1e-10
        assert restored.futures_leverage == pos.futures_leverage
        assert abs(restored.notional_usdt - pos.notional_usdt) < 1e-10
        assert abs(restored.entry_basis_bps - pos.entry_basis_bps) < 1e-10
        assert restored.open_time_ms == pos.open_time_ms
        assert abs(restored.entry_fees - pos.entry_fees) < 1e-10
        assert abs(restored.cumulative_funding - pos.cumulative_funding) < 1e-10
        assert abs(restored.basis_pnl - pos.basis_pnl) < 1e-10

    def test_deserialize_missing_file_starts_empty(self, tmp_path):
        state_file = tmp_path / "nonexistent.json"
        pm = PositionManager(state_file=str(state_file))
        pm.deserialize_state()
        assert len(pm.get_open_positions()) == 0

    def test_deserialize_corrupted_json_starts_empty(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text("not valid json {{{", encoding="utf-8")

        alert_service = MagicMock()
        pm = PositionManager(
            state_file=str(state_file), alert_service=alert_service
        )
        pm.deserialize_state()

        assert len(pm.get_open_positions()) == 0
        # Verify alert was sent
        alert_service._send.assert_called_once()
        call_text = alert_service._send.call_args[0][0]
        assert "state_recovery_failed" in call_text

    def test_deserialize_invalid_structure_starts_empty(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        alert_service = MagicMock()
        pm = PositionManager(
            state_file=str(state_file), alert_service=alert_service
        )
        pm.deserialize_state()

        assert len(pm.get_open_positions()) == 0
        alert_service._send.assert_called_once()

    def test_deserialize_missing_required_fields_starts_empty(self, tmp_path):
        state_file = tmp_path / "state.json"
        data = {
            "version": 1,
            "open_positions": [{"id": "incomplete"}],
        }
        state_file.write_text(json.dumps(data), encoding="utf-8")

        alert_service = MagicMock()
        pm = PositionManager(
            state_file=str(state_file), alert_service=alert_service
        )
        pm.deserialize_state()

        assert len(pm.get_open_positions()) == 0
        alert_service._send.assert_called_once()

    def test_serialize_creates_parent_dirs(self, tmp_path):
        state_file = tmp_path / "nested" / "dir" / "state.json"
        pm = PositionManager(state_file=str(state_file))
        pm.open_position(_make_position())
        pm.serialize_state()
        assert state_file.exists()
