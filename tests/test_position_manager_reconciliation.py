"""Test Reconciliation integration for futures_arb PositionManager."""

import pytest
from unittest.mock import Mock

from mexc_monitor.futures_arb.position_manager import PositionManager


def test_position_manager_reconcile_no_actual_positions():
    """Test reconciliation without actual positions (in-memory only)."""
    manager = PositionManager()

    # Create mock open positions
    from mexc_monitor.futures_arb.models import FuturesArbPosition

    pos1 = FuturesArbPosition(
        id="pos1",
        symbol="BTCUSDT",
        exchange_combo="mexc_spot_binance_perp",
        strategy="cash_and_carry",
        state="open",
        spot_side="buy",
        spot_entry_price=50000.0,
        spot_qty=0.5,
        futures_side="short",
        futures_entry_price=50000.1,
        futures_qty=0.5,
        futures_leverage=10.0,
        notional_usdt=50000.0,
        entry_basis_bps=10.0,
        open_time_ms=1700000000000,
        close_time_ms=0,
        close_reason="",
        basis_pnl=0.0,
        cumulative_funding=0.0,
        entry_fees=0.0,
        exit_fees=0.0,
    )

    with manager._lock:
        manager._positions[pos1.id] = pos1

    # Reconcile
    result = manager.reconcile(actual_positions=None)

    # Should report positions exist
    assert "open_positions_count" in result
    assert result["open_positions_count"] == 1
    assert result["expected_positions_count"] == 1
    assert result["actual_positions_count"] == 1
    assert result["all_clear"] is True


def test_position_manager_reconcile_with_mismatch():
    """Test reconciliation when quantities don't match."""
    manager = PositionManager()

    # Create mock open position (buy position)
    from mexc_monitor.futures_arb.models import FuturesArbPosition

    pos1 = FuturesArbPosition(
        id="pos1",
        symbol="BTCUSDT",
        exchange_combo="mexc_spot_binance_perp",
        strategy="cash_and_carry",
        state="open",
        spot_side="buy",
        spot_entry_price=50000.0,
        spot_qty=0.5,  # Expected 0.5
        futures_side="short",
        futures_entry_price=50000.1,
        futures_qty=0.5,
        futures_leverage=10.0,
        notional_usdt=50000.0,
        entry_basis_bps=10.0,
        open_time_ms=1700000000000,
        close_time_ms=0,
        close_reason="",
        basis_pnl=0.0,
        cumulative_funding=0.0,
        entry_fees=0.0,
        exit_fees=0.0,
    )

    with manager._lock:
        manager._positions[pos1.id] = pos1

    # Actual position with different qty
    actual_positions = [
        ("BTCUSDT", "buy", 0.4),  # 0.4 instead of 0.5
    ]

    # Reconcile
    result = manager.reconcile(actual_positions=actual_positions)

    # Should detect discrepancy
    assert "reconciliation_result" in result
    reconcile_result = result["reconciliation_result"]
    assert len(reconcile_result["discrepancies"]) > 0
    assert reconcile_result["discrepancies"][0].type == "qty_mismatch"
    assert reconcile_result["all_clear"] is False


def test_position_manager_reconcile_unexpected_position():
    """Test reconciliation when unexpected position exists."""
    manager = PositionManager()

    # Create mock open position (buy)
    from mexc_monitor.futures_arb.models import FuturesArbPosition

    pos1 = FuturesArbPosition(
        id="pos1",
        symbol="BTCUSDT",
        exchange_combo="mexc_spot_binance_perp",
        strategy="cash_and_carry",
        state="open",
        spot_side="buy",
        spot_entry_price=50000.0,
        spot_qty=0.5,
        futures_side="short",
        futures_entry_price=50000.1,
        futures_qty=0.5,
        futures_leverage=10.0,
        notional_usdt=50000.0,
        entry_basis_bps=10.0,
        open_time_ms=1700000000000,
        close_time_ms=0,
        close_reason="",
        basis_pnl=0.0,
        cumulative_funding=0.0,
        entry_fees=0.0,
        exit_fees=0.0,
    )

    with manager._lock:
        manager._positions[pos1.id] = pos1

    # Actual position for different symbol
    actual_positions = [
        ("ETHUSDT", "buy", 0.3),  # Unexpected!
    ]

    # Reconcile
    result = manager.reconcile(actual_positions=actual_positions)

    # Should detect unexpected position
    assert "reconciliation_result" in result
    reconcile_result = result["reconciliation_result"]
    assert len(reconcile_result["discrepancies"]) > 0
    assert reconcile_result["discrepancies"][0].type == "unexpected_on_exchange"
    assert reconcile_result["all_clear"] is False


def test_position_manager_reconcile_missing_position():
    """Test reconciliation when position is missing on exchange."""
    manager = PositionManager()

    # Create mock open position
    from mexc_monitor.futures_arb.models import FuturesArbPosition

    pos1 = FuturesArbPosition(
        id="pos1",
        symbol="BTCUSDT",
        exchange_combo="mexc_spot_binance_perp",
        strategy="cash_and_carry",
        state="open",
        spot_side="buy",
        spot_entry_price=50000.0,
        spot_qty=0.5,
        futures_side="short",
        futures_entry_price=50000.1,
        futures_qty=0.5,
        futures_leverage=10.0,
        notional_usdt=50000.0,
        entry_basis_bps=10.0,
        open_time_ms=1700000000000,
        close_time_ms=0,
        close_reason="",
        basis_pnl=0.0,
        cumulative_funding=0.0,
        entry_fees=0.0,
        exit_fees=0.0,
    )

    with manager._lock:
        manager._positions[pos1.id] = pos1

    # No actual positions (order cancelled or not found)
    actual_positions = []

    # Reconcile
    result = manager.reconcile(actual_positions=actual_positions)

    # Should detect missing position
    assert "reconciliation_result" in result
    reconcile_result = result["reconciliation_result"]
    assert len(reconcile_result["discrepancies"]) > 0
    assert reconcile_result["discrepancies"][0].type == "missing_on_exchange"
    assert reconcile_result["all_clear"] is False


def test_position_manager_reconcile_multiple_positions():
    """Test reconciliation with multiple positions."""
    manager = PositionManager()

    # Create multiple mock open positions
    from mexc_monitor.futures_arb.models import FuturesArbPosition

    pos1 = FuturesArbPosition(
        id="pos1",
        symbol="BTCUSDT",
        exchange_combo="mexc_spot_binance_perp",
        strategy="cash_and_carry",
        state="open",
        spot_side="buy",
        spot_entry_price=50000.0,
        spot_qty=0.5,
        futures_side="short",
        futures_entry_price=50000.1,
        futures_qty=0.5,
        futures_leverage=10.0,
        notional_usdt=50000.0,
        entry_basis_bps=10.0,
        open_time_ms=1700000000000,
        close_time_ms=0,
        close_reason="",
        basis_pnl=0.0,
        cumulative_funding=0.0,
        entry_fees=0.0,
        exit_fees=0.0,
    )

    pos2 = FuturesArbPosition(
        id="pos2",
        symbol="ETHUSDT",
        exchange_combo="mexc_spot_binance_perp",
        strategy="cash_and_carry",
        state="open",
        spot_side="buy",
        spot_entry_price=3000.0,
        spot_qty=10.0,
        futures_side="short",
        futures_entry_price=3000.1,
        futures_qty=10.0,
        futures_leverage=10.0,
        notional_usdt=30000.0,
        entry_basis_bps=10.0,
        open_time_ms=1700000000000,
        close_time_ms=0,
        close_reason="",
        basis_pnl=0.0,
        cumulative_funding=0.0,
        entry_fees=0.0,
        exit_fees=0.0,
    )

    with manager._lock:
        manager._positions[pos1.id] = pos1
        manager._positions[pos2.id] = pos2

    # Actual positions match expected
    actual_positions = [
        ("BTCUSDT", "buy", 0.5),
        ("ETHUSDT", "buy", 10.0),
    ]

    # Reconcile
    result = manager.reconcile(actual_positions=actual_positions)

    # Should have no discrepancies
    assert "reconciliation_result" in result
    reconcile_result = result["reconciliation_result"]
    assert len(reconcile_result["matched"]) == 2
    assert len(reconcile_result["discrepancies"]) == 0
    assert reconcile_result["all_clear"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])