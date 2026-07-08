"""Test Reconciliation integration for spread_capture."""

import pytest
from unittest.mock import Mock, MagicMock

from mexc_monitor.spread_capture import SpreadCaptureEngine
from mexc_monitor.order_executor import OrderTicket


def test_spread_capture_reconcile_paper_mode():
    """Test reconciliation in paper mode (no exchange verification)."""
    # Create engine with default settings
    engine = SpreadCaptureEngine()

    # Position in holding state (simulated)
    with engine._lock:
        engine._position = Mock(
            state="holding",
            symbol="BTCUSDT",
            entry_price=50000.0,
            entry_qty=0.5,
        )

    # Reconcile
    result = engine.reconcile()

    # In paper mode, reconciliation should be clean (no real checks)
    assert "reconciliation_result" in result
    assert result["reconciliation_result"]["all_clear"] is True


def test_spread_capture_reconcile_live_mode():
    """Test reconciliation in live mode with mocked order executor."""
    from mexc_monitor.spread_capture import Position

    # Create engine with default settings
    engine = SpreadCaptureEngine()

    # Mock order executor
    mock_executor = Mock()
    mock_executor.get_open_orders = Mock(return_value=[])
    engine.set_order_executor(mock_executor)

    # Position in pending_buy state
    with engine._lock:
        engine._position = Position(
            state="pending_buy",
            symbol="BTCUSDT",
            entry_price=50000.0,
            entry_qty=0.5,
        )

    # Reconcile
    result = engine.reconcile()

    # Should check for orders
    assert "reconciliation_result" in result
    mock_executor.get_open_orders.assert_called_once_with("BTCUSDT")


def test_spread_capture_reconcile_with_open_orders():
    """Test reconciliation when open orders exist on exchange."""
    from mexc_monitor.spread_capture import Position

    # Create engine with default settings
    engine = SpreadCaptureEngine()

    # Mock order executor with open order
    mock_ticket = OrderTicket(
        order_id="order123",
        client_order_id="cli123",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        qty=0.5,
        price=50000.0,
        status="NEW",
        created_ms=1700000000000,
    )
    mock_executor = Mock()
    mock_executor.get_open_orders = Mock(return_value=[mock_ticket])
    engine.set_order_executor(mock_executor)

    # Position matches the order
    with engine._lock:
        engine._position = Position(
            state="pending_buy",
            symbol="BTCUSDT",
            entry_price=50000.0,
            entry_qty=0.5,
        )

    # Reconcile
    result = engine.reconcile()

    # Should have matched position with order
    assert "reconciliation_result" in result
    reconcile_result = result["reconciliation_result"]
    assert len(reconcile_result["matched"]) > 0


def test_spread_capture_reconcile_no_position():
    """Test reconciliation when no position exists."""
    # Create engine with default settings
    engine = SpreadCaptureEngine()

    # No position
    with engine._lock:
        engine._position = engine._position  # Keep existing (idle)

    # Reconcile
    result = engine.reconcile()

    # Should handle gracefully
    assert "reconciliation_result" in result


def test_spread_capture_reconcile_with_discrepancies():
    """Test reconciliation when orders and position don't match."""
    from mexc_monitor.spread_capture import Position

    # Create engine with default settings
    engine = SpreadCaptureEngine()

    # Mock order executor with different order
    mock_ticket = OrderTicket(
        order_id="order456",
        client_order_id="cli456",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        qty=0.3,  # Different qty!
        price=50000.0,
        status="NEW",
        created_ms=1700000000000,
    )
    mock_executor = Mock()
    mock_executor.get_open_orders = Mock(return_value=[mock_ticket])
    engine.set_order_executor(mock_executor)

    # Position matches different order
    with engine._lock:
        engine._position = Position(
            state="pending_buy",
            symbol="BTCUSDT",
            entry_price=50000.0,
            entry_qty=0.5,  # 0.5 vs 0.3
        )

    # Reconcile
    result = engine.reconcile()

    # Should detect discrepancy
    assert "reconciliation_result" in result
    reconcile_result = result["reconciliation_result"]
    assert len(reconcile_result["discrepancies"]) > 0
    assert reconcile_result["all_clear"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
