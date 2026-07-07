"""Tests for BasePrivateClient ABC and data models."""

from __future__ import annotations

from typing import Any, Literal

import pytest

from mexc_monitor.trading.exchanges import OrderSide, OrderType
from mexc_monitor.trading.private_client_base import (
    BasePrivateClient,
    OrderRequest,
    OrderResponse,
    PrivateApiError,
)


class ConcreteClient(BasePrivateClient):
    """Minimal concrete implementation for testing the ABC."""

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        signed = dict(params)
        signed["signature"] = "test_sig"
        return signed

    def _get_api_key_header(self) -> str:
        return "X-TEST-KEY"

    def place_order(self, request: OrderRequest) -> OrderResponse:
        return OrderResponse(
            order_id="123",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            status="NEW",
            raw={},
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        return {"symbol": symbol, "status": "CANCELED"}

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return []


class TestOrderRequest:
    def test_create_limit_order_request(self):
        req = OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.001,
            price=50000.0,
            client_order_id="test-123",
        )
        assert req.symbol == "BTCUSDT"
        assert req.side == OrderSide.BUY
        assert req.order_type == OrderType.LIMIT
        assert req.quantity == 0.001
        assert req.price == 50000.0
        assert req.client_order_id == "test-123"
        assert req.time_in_force == "GTC"

    def test_create_market_order_request(self):
        req = OrderRequest(
            symbol="ETHUSDT",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=1.5,
            price=None,
            client_order_id="mkt-456",
            time_in_force="IOC",
        )
        assert req.price is None
        assert req.order_type == OrderType.MARKET
        assert req.time_in_force == "IOC"


class TestOrderResponse:
    def test_create_order_response(self):
        resp = OrderResponse(
            order_id="ord-789",
            client_order_id="cli-001",
            symbol="BTCUSDT",
            side="BUY",
            order_type="LIMIT",
            status="FILLED",
            raw={"origQty": "0.001"},
        )
        assert resp.order_id == "ord-789"
        assert resp.client_order_id == "cli-001"
        assert resp.status == "FILLED"
        assert resp.raw == {"origQty": "0.001"}


class TestBasePrivateClient:
    def test_has_credentials_true(self):
        client = ConcreteClient(
            api_key="my_key",
            api_secret="my_secret",
            base_url="https://api.example.com",
        )
        assert client.has_credentials() is True

    def test_has_credentials_false_empty_key(self):
        client = ConcreteClient(
            api_key="",
            api_secret="my_secret",
            base_url="https://api.example.com",
        )
        assert client.has_credentials() is False

    def test_has_credentials_false_empty_secret(self):
        client = ConcreteClient(
            api_key="my_key",
            api_secret="",
            base_url="https://api.example.com",
        )
        assert client.has_credentials() is False

    def test_has_credentials_false_whitespace_only(self):
        client = ConcreteClient(
            api_key="   ",
            api_secret="  ",
            base_url="https://api.example.com",
        )
        assert client.has_credentials() is False

    def test_init_strips_credentials(self):
        client = ConcreteClient(
            api_key="  key  ",
            api_secret="  secret  ",
            base_url="https://api.example.com/",
        )
        assert client._api_key == "key"
        assert client._api_secret == "secret"
        assert client._base_url == "https://api.example.com"

    def test_default_params(self):
        client = ConcreteClient(
            api_key="k",
            api_secret="s",
            base_url="https://api.example.com",
        )
        assert client._timeout_sec == 20.0
        assert client._recv_window_ms == 5000

    def test_custom_params(self):
        client = ConcreteClient(
            api_key="k",
            api_secret="s",
            base_url="https://api.example.com",
            timeout_sec=10.0,
            recv_window_ms=3000,
        )
        assert client._timeout_sec == 10.0
        assert client._recv_window_ms == 3000

    def test_cannot_instantiate_abc_directly(self):
        with pytest.raises(TypeError):
            BasePrivateClient(
                api_key="k",
                api_secret="s",
                base_url="https://api.example.com",
            )

    def test_place_order_via_concrete(self):
        client = ConcreteClient(
            api_key="k",
            api_secret="s",
            base_url="https://api.example.com",
        )
        req = OrderRequest(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.01,
            price=50000.0,
            client_order_id="test-id",
        )
        resp = client.place_order(req)
        assert resp.order_id == "123"
        assert resp.symbol == "BTCUSDT"
        assert resp.status == "NEW"

    def test_fmt_helper(self):
        assert ConcreteClient._fmt(0.001) == "0.001"
        assert ConcreteClient._fmt(100.0) == "100"
        assert ConcreteClient._fmt(0.000000000001) == "0.000000000001"
        assert ConcreteClient._fmt(1234.5) == "1234.5"
