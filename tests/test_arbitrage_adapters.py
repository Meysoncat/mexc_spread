"""Tests for the arbitrage exchange adapters (mexc_monitor/arbitrage/adapters.py).

Covers the MEXC spot market-order path that previously raised
NotImplementedError. The adapter must now build a correct MARKET
OrderRequest and return the raw exchange dict, matching the contract of
place_limit_order.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from mexc_monitor.arbitrage.adapters import (
    AsterDexAdapter,
    MexcSpotAdapter,
    get_adapter,
)
from mexc_monitor.trading.exchanges import OrderSide, OrderType
from mexc_monitor.trading.private_client_base import OrderResponse


class _FakeClient:
    """Replaces MexcPrivateClient inside the adapter's `with` block.

    Records the OrderRequest handed to place_order() and returns a
    deterministic OrderResponse without touching the network.
    """

    def __init__(self, *args, **kwargs):
        self.captured_request = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def place_order(self, request):
        self.captured_request = request
        return OrderResponse(
            order_id="fake-123",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            status="NEW",
            raw={"orderId": "fake-123", "status": "NEW"},
        )


def test_mexc_spot_place_market_order_builds_market_request():
    """place_market_order must emit OrderType.MARKET with no price (not raise)."""
    adapter = MexcSpotAdapter(api_key="k", api_secret="s")
    fake = _FakeClient()

    with patch(
        "mexc_monitor.trading.private_client.MexcPrivateClient",
        return_value=fake,
    ):
        result = adapter.place_market_order(
            symbol="BTCUSDT", side="BUY", quantity=0.01
        )

    req = fake.captured_request
    assert req is not None
    assert req.order_type == OrderType.MARKET
    assert req.side == OrderSide.BUY
    assert req.price is None
    assert req.quantity == pytest.approx(0.01)
    assert req.symbol == "BTCUSDT"
    # Returns the raw exchange dict (same contract as place_limit_order).
    assert result == {"orderId": "fake-123", "status": "NEW"}


def test_mexc_spot_place_market_order_sell_side():
    adapter = MexcSpotAdapter(api_key="k", api_secret="s")
    fake = _FakeClient()
    with patch(
        "mexc_monitor.trading.private_client.MexcPrivateClient",
        return_value=fake,
    ):
        adapter.place_market_order(symbol="ETHUSDT", side="sell", quantity=0.5)
    assert fake.captured_request.side == OrderSide.SELL
    assert fake.captured_request.order_type == OrderType.MARKET


def test_mexc_spot_market_order_honors_client_order_id():
    adapter = MexcSpotAdapter(api_key="k", api_secret="s")
    fake = _FakeClient()
    with patch(
        "mexc_monitor.trading.private_client.MexcPrivateClient",
        return_value=fake,
    ):
        adapter.place_market_order(
            symbol="BTCUSDT", side="BUY", quantity=1.0, client_order_id="my-coid"
        )
    assert fake.captured_request.client_order_id == "my-coid"


def test_mexc_spot_market_order_auto_generates_coid_when_omitted():
    adapter = MexcSpotAdapter(api_key="k", api_secret="s")
    fake = _FakeClient()
    with patch(
        "mexc_monitor.trading.private_client.MexcPrivateClient",
        return_value=fake,
    ):
        adapter.place_market_order(symbol="BTCUSDT", side="BUY", quantity=1.0)
    assert fake.captured_request.client_order_id  # non-empty, auto-generated


def test_get_adapter_returns_supported_exchanges():
    assert get_adapter("mexc_spot").exchange_name == "mexc_spot"
    assert get_adapter("asterdex").exchange_name == "asterdex"
    with pytest.raises(ValueError):
        get_adapter("unknown-exchange")


def test_adapters_report_configured_state():
    assert not MexcSpotAdapter().configured  # no creds in env by default
    assert MexcSpotAdapter(api_key="k", api_secret="s").configured
    assert AsterDexAdapter(api_key="k", api_secret="s").configured
