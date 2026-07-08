"""Test Reconciliation integration for spread_capture and futures_arb."""

import pytest
from unittest.mock import Mock

from mexc_monitor.reconciliation import (
    ReconciliationResult,
    ExpectedPosition,
    ActualPosition,
    reconcile_positions,
)


def test_reconciliation_result():
    """Test ReconciliationResult basic properties."""
    result = ReconciliationResult()
    assert result.matched == []
    assert result.discrepancies == []
    assert result.all_clear is True
    assert not result.has_issues


def test_reconciliation_result_has_issues():
    """Test ReconciliationResult.has_issues property."""
    result = ReconciliationResult()
    result.discrepancies.append(Mock())

    assert result.has_issues is True


def test_reconciliation_with_no_discrepancies():
    """Test reconciliation when all positions match."""
    expected = [ExpectedPosition(symbol="BTCUSDT", qty=0.5, side="buy", exchange="mexc")]
    actual = [ActualPosition(symbol="BTCUSDT", qty=0.5, side="buy", exchange="mexc")]

    result = reconcile_positions(expected=expected, actual=actual)

    assert len(result.matched) == 1
    assert len(result.discrepancies) == 0
    assert result.all_clear is True


def test_reconciliation_missing_on_exchange():
    """Test reconciliation when position is missing on exchange."""
    expected = [ExpectedPosition(symbol="BTCUSDT", qty=0.5, side="buy", exchange="mexc")]
    actual = []  # No positions on exchange

    result = reconcile_positions(expected=expected, actual=actual)

    assert len(result.matched) == 0
    assert len(result.discrepancies) == 1
    assert result.discrepancies[0].type == "missing_on_exchange"
    assert result.discrepancies[0].expected_qty == 0.5
    assert result.discrepancies[0].actual_qty == 0.0
    assert result.all_clear is False


def test_reconciliation_qty_mismatch():
    """Test reconciliation when quantities differ."""
    expected = [ExpectedPosition(symbol="BTCUSDT", qty=0.5, side="buy", exchange="mexc")]
    actual = [ActualPosition(symbol="BTCUSDT", qty=0.45, side="buy", exchange="mexc")]

    result = reconcile_positions(expected=expected, actual=actual)

    assert len(result.matched) == 0
    assert len(result.discrepancies) == 1
    assert result.discrepancies[0].type == "qty_mismatch"
    assert result.discrepancies[0].expected_qty == 0.5
    assert result.discrepancies[0].actual_qty == 0.45
    assert result.all_clear is False


def test_reconciliation_unexpected_on_exchange():
    """Test reconciliation when unexpected position exists on exchange."""
    expected = [ExpectedPosition(symbol="BTCUSDT", qty=0.5, side="buy", exchange="mexc")]
    actual = [
        ActualPosition(symbol="BTCUSDT", qty=0.5, side="buy", exchange="mexc"),
        ActualPosition(symbol="ETHUSDT", qty=0.3, side="buy", exchange="binance"),
    ]

    result = reconcile_positions(expected=expected, actual=actual)

    assert len(result.matched) == 1
    assert len(result.discrepancies) == 1
    assert result.discrepancies[0].type == "unexpected_on_exchange"
    assert result.discrepancies[0].symbol == "ETHUSDT"
    assert result.discrepancies[0].actual_qty == 0.3
    assert result.all_clear is False


def test_reconciliation_multiple_types():
    """Test reconciliation with multiple types of discrepancies."""
    expected = [
        ExpectedPosition(symbol="BTCUSDT", qty=0.5, side="buy", exchange="mexc"),
        ExpectedPosition(symbol="ETHUSDT", qty=0.3, side="sell", exchange="binance"),
    ]
    actual = [
        ActualPosition(symbol="BTCUSDT", qty=0.5, side="buy", exchange="mexc"),
        ActualPosition(symbol="SOLUSDT", qty=0.2, side="buy", exchange="binance"),
    ]

    result = reconcile_positions(expected=expected, actual=actual)

    assert len(result.matched) == 1
    assert len(result.discrepancies) == 2
    assert result.all_clear is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])