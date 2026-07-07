"""Tests for OrderExecutor and ClockSkewDetector."""

from __future__ import annotations

import pytest

from mexc_monitor.order_executor import (
    OrderExecutor,
    OrderTicket,
    FillResult,
)
from mexc_monitor.clock_skew import ClockSkewDetector


# ─── OrderTicket unit tests ──────────────────────────────────────────────────


class TestOrderTicket:
    def test_is_open(self):
        t = OrderTicket(order_id="1", client_order_id="c1", symbol="BTCUSDT",
                        side="BUY", order_type="LIMIT", qty=1.0, price=100.0)
        assert t.is_open is True
        assert t.is_filled is False
        assert t.is_closed is False

    def test_is_filled(self):
        t = OrderTicket(order_id="1", client_order_id="c1", symbol="BTCUSDT",
                        side="BUY", order_type="LIMIT", qty=1.0, price=100.0,
                        status="FILLED", filled_qty=1.0)
        assert t.is_filled is True
        assert t.is_open is False
        assert t.is_closed is True

    def test_remaining_qty(self):
        t = OrderTicket(order_id="1", client_order_id="c1", symbol="BTCUSDT",
                        side="BUY", order_type="LIMIT", qty=1.0, price=100.0,
                        filled_qty=0.3)
        assert t.remaining_qty == pytest.approx(0.7)


# ─── OrderExecutor with mock client ──────────────────────────────────────────


class FakePrivateClient:
    """Minimal mock for BasePrivateClient."""

    def __init__(self, *, open_orders=None, place_response=None, fail=False):
        self._open_orders = open_orders or []
        self._place_response = place_response
        self._fail = fail
        self.placed_orders: list[dict] = []
        self.cancelled: list[str] = []

    def place_order(self, request):
        if self._fail:
            from mexc_monitor.trading.private_client_base import PrivateApiError
            raise PrivateApiError("mock failure")
        self.placed_orders.append({"symbol": request.symbol, "side": request.side.value})
        if self._place_response:
            return self._place_response
        from mexc_monitor.trading.private_client_base import OrderResponse
        return OrderResponse(
            order_id="test-oid",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            status="NEW",
            raw={},
        )

    def get_open_orders(self, symbol: str):
        return list(self._open_orders)

    def cancel_order(self, *, symbol, order_id=None, client_order_id=None):
        self.cancelled.append(order_id or client_order_id or "")
        return {"status": "CANCELED"}

    def has_credentials(self):
        return True


class TestOrderExecutorPlaceLimit:
    def test_place_limit_success(self):
        client = FakePrivateClient()
        executor = OrderExecutor(client)
        ticket = executor.place_limit_order("BTCUSDT", "BUY", 0.5, 100.0)
        assert ticket is not None
        assert ticket.symbol == "BTCUSDT"
        assert ticket.side == "BUY"
        assert ticket.status == "NEW"
        assert ticket.qty == pytest.approx(0.5)

    def test_place_limit_failure_returns_none(self):
        client = FakePrivateClient(fail=True)
        executor = OrderExecutor(client)
        ticket = executor.place_limit_order("BTCUSDT", "BUY", 0.5, 100.0)
        assert ticket is None


class TestOrderExecutorPollStatus:
    def test_order_still_open(self):
        client = FakePrivateClient(open_orders=[
            {"orderId": "test-oid", "executedQty": "0"}
        ])
        executor = OrderExecutor(client)
        ticket = OrderTicket(
            order_id="test-oid", client_order_id="c1", symbol="BTCUSDT",
            side="BUY", order_type="LIMIT", qty=1.0, price=100.0,
        )
        updated = executor.poll_status(ticket)
        assert updated.status == "NEW"
        assert updated.is_open is True

    def test_order_filled(self):
        client = FakePrivateClient(open_orders=[])
        executor = OrderExecutor(client)
        ticket = OrderTicket(
            order_id="test-oid", client_order_id="c1", symbol="BTCUSDT",
            side="BUY", order_type="LIMIT", qty=1.0, price=100.0,
        )
        updated = executor.poll_status(ticket)
        assert updated.status == "FILLED"
        assert updated.is_filled is True

    def test_order_partially_filled(self):
        client = FakePrivateClient(open_orders=[
            {"orderId": "test-oid", "executedQty": "0.3"}
        ])
        executor = OrderExecutor(client)
        ticket = OrderTicket(
            order_id="test-oid", client_order_id="c1", symbol="BTCUSDT",
            side="BUY", order_type="LIMIT", qty=1.0, price=100.0,
        )
        updated = executor.poll_status(ticket)
        assert updated.status == "PARTIALLY_FILLED"
        assert updated.filled_qty == pytest.approx(0.3)


class TestOrderExecutorCancel:
    def test_cancel_open_order(self):
        client = FakePrivateClient()
        executor = OrderExecutor(client)
        ticket = OrderTicket(
            order_id="test-oid", client_order_id="c1", symbol="BTCUSDT",
            side="BUY", order_type="LIMIT", qty=1.0, price=100.0,
        )
        result = executor.cancel(ticket)
        assert result is True
        assert ticket.status == "CANCELED"
        assert len(client.cancelled) == 1

    def test_cancel_already_closed(self):
        client = FakePrivateClient()
        executor = OrderExecutor(client)
        ticket = OrderTicket(
            order_id="test-oid", client_order_id="c1", symbol="BTCUSDT",
            side="BUY", order_type="LIMIT", qty=1.0, price=100.0,
            status="FILLED", filled_qty=1.0,
        )
        result = executor.cancel(ticket)
        assert result is True  # No-op for closed orders
        assert len(client.cancelled) == 0


# ─── ClockSkewDetector tests ─────────────────────────────────────────────────


class TestClockSkew:
    def test_no_data_returns_zero(self):
        detector = ClockSkewDetector()
        assert detector.get_skew_ms("mexc") == 0.0
        assert detector.is_skewed("mexc") is False

    def test_detects_skew_from_date_header(self):
        import time
        from email.utils import formatdate
        detector = ClockSkewDetector(max_skew_ms=500)

        # Server is 2 seconds behind local
        server_time = time.time() - 2.0
        headers = {"Date": formatdate(server_time, usegmt=True)}

        skew = detector.check_from_response("mexc", headers)
        assert skew is not None
        assert skew > 1500  # Local ahead by ~2000ms (allowing tolerance)
        assert detector.is_skewed("mexc") is True

    def test_no_skew_when_synced(self):
        from email.utils import formatdate
        import time
        # Use default 1000ms threshold — Date header rounds to whole seconds
        detector = ClockSkewDetector()
        headers = {"Date": formatdate(time.time(), usegmt=True)}
        skew = detector.check_from_response("binance", headers)
        assert skew is not None
        assert abs(skew) < 1000
        assert detector.is_skewed("binance") is False

    def test_missing_date_header_returns_none(self):
        detector = ClockSkewDetector()
        result = detector.check_from_response("mexc", {"Content-Type": "application/json"})
        assert result is None

    def test_adjust_timestamp(self):
        detector = ClockSkewDetector()
        detector._skews["mexc"] = type("", (), {"exchange": "mexc", "skew_ms": 500.0,
                                                "last_check_ms": 0, "warning_count": 0})
        adjusted = detector.adjust_timestamp("mexc", 1000000)
        assert adjusted == 1000500  # 1000000 + 500

    def test_adjust_timestamp_no_data(self):
        detector = ClockSkewDetector()
        assert detector.adjust_timestamp("unknown", 1000000) == 1000000

    def test_get_all_skews(self):
        from email.utils import formatdate
        import time
        detector = ClockSkewDetector()
        headers = {"Date": formatdate(time.time(), usegmt=True)}
        detector.check_from_response("mexc", headers)
        detector.check_from_response("binance", headers)
        all_skews = detector.get_all_skews()
        assert "mexc" in all_skews
        assert "binance" in all_skews

    def test_get_status(self):
        from email.utils import formatdate
        import time
        detector = ClockSkewDetector()
        headers = {"Date": formatdate(time.time(), usegmt=True)}
        detector.check_from_response("mexc", headers)
        status = detector.get_status()
        assert len(status) >= 1
        assert status[0]["exchange"] == "mexc"
        assert "skew_ms" in status[0]
