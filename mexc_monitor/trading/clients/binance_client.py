"""Binance exchange private client — HMAC SHA256 signing on sorted query string."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

from mexc_monitor.trading.exchanges import OrderSide, OrderType
from mexc_monitor.trading.private_client_base import (
    BasePrivateClient,
    OrderRequest,
    OrderResponse,
    PrivateApiError,
)


class BinancePrivateClient(BasePrivateClient):
    """Binance spot/futures private client.

    Signing: HMAC SHA256 on sorted query string.
    Headers: X-MBX-APIKEY with the API key value.
    Params: timestamp + recvWindow added before signing.
    """

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        signed = dict(params)
        signed["timestamp"] = int(time.time() * 1000)
        if self._recv_window_ms:
            signed["recvWindow"] = self._recv_window_ms
        query = urlencode(sorted(signed.items()))
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed["signature"] = signature
        return signed

    def _get_api_key_header(self) -> str:
        return "X-MBX-APIKEY"

    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order on Binance spot via /api/v3/order."""
        params: dict[str, Any] = {
            "symbol": request.symbol.upper(),
            "side": request.side.value,
            "type": request.order_type.value,
            "quantity": self._fmt(request.quantity),
            "newClientOrderId": request.client_order_id,
        }
        if request.order_type == OrderType.LIMIT:
            params["timeInForce"] = request.time_in_force
            if request.price is not None:
                params["price"] = self._fmt(request.price)
        # MARKET orders: no price param

        raw = self._request_signed("POST", "/api/v3/order", params=params)
        return OrderResponse(
            order_id=str(raw.get("orderId", "")),
            client_order_id=raw.get("clientOrderId", request.client_order_id),
            symbol=raw.get("symbol", request.symbol),
            side=raw.get("side", request.side.value),
            order_type=raw.get("type", request.order_type.value),
            status=raw.get("status", "NEW"),
            raw=raw,
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an order by order_id or client_order_id."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = int(order_id)
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return self._request_signed("DELETE", "/api/v3/order", params=params)

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return list of open orders for the given symbol."""
        payload = self._request_signed(
            "GET", "/api/v3/openOrders", params={"symbol": symbol.upper()}
        )
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        return []
