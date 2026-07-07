"""Bybit exchange private client — HMAC SHA256 on timestamp+api_key+recv_window+query."""

from __future__ import annotations

import hashlib
import hmac
import json
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


class BybitPrivateClient(BasePrivateClient):
    """Bybit V5 private client.

    Signing: HMAC SHA256 on `timestamp + api_key + recv_window + query_or_body`.
    Headers: X-BAPI-API-KEY, X-BAPI-TIMESTAMP, X-BAPI-SIGN, X-BAPI-RECV-WINDOW.
    """

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign params for Bybit V5 API.

        Returns dict with __bybit_timestamp and __bybit_sign metadata keys
        that are used by the request method to set headers.
        """
        timestamp = str(int(time.time() * 1000))
        recv_window = str(self._recv_window_ms)

        # For Bybit, the payload to sign is: timestamp + api_key + recv_window + sorted_query
        sorted_params = sorted(params.items())
        query_string = urlencode(sorted_params) if sorted_params else ""

        pre_sign = f"{timestamp}{self._api_key}{recv_window}{query_string}"
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            pre_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        signed = dict(params)
        signed["__bybit_timestamp"] = timestamp
        signed["__bybit_sign"] = signature
        signed["__bybit_recv_window"] = recv_window
        return signed

    def _get_api_key_header(self) -> str:
        return "X-BAPI-API-KEY"

    def _request_signed(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Override base to use Bybit-specific header-based auth."""
        signed = self._sign(params or {})

        # Extract metadata
        timestamp = signed.pop("__bybit_timestamp")
        signature = signed.pop("__bybit_sign")
        recv_window = signed.pop("__bybit_recv_window")

        url = f"{self._base_url}{path}"
        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": recv_window,
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=self._timeout_sec) as client:
            if method == "GET":
                r = client.get(url, params=signed, headers=headers)
            elif method == "POST":
                r = client.post(url, json=signed, headers=headers)
            else:
                r = client.delete(url, params=signed, headers=headers)

        if r.status_code >= 400:
            raise PrivateApiError(f"HTTP {r.status_code}: {r.text[:300]}")

        payload = r.json()
        if isinstance(payload, dict):
            ret_code = payload.get("retCode")
            if ret_code is not None and ret_code != 0:
                msg = payload.get("retMsg") or "unknown error"
                raise PrivateApiError(
                    f"Bybit error retCode={ret_code} msg={msg}"
                )
            # Bybit wraps results in "result" key
            result = payload.get("result")
            if result is not None:
                return result
            return payload
        if isinstance(payload, list):
            return payload
        raise PrivateApiError(f"Unexpected response type: {type(payload)}")

    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order on Bybit V5 via /v5/order/create."""
        params: dict[str, Any] = {
            "category": "spot",
            "symbol": request.symbol.upper(),
            "side": "Buy" if request.side == OrderSide.BUY else "Sell",
            "orderType": "Limit" if request.order_type == OrderType.LIMIT else "Market",
            "qty": self._fmt(request.quantity),
            "orderLinkId": request.client_order_id,
        }
        if request.order_type == OrderType.LIMIT:
            params["timeInForce"] = request.time_in_force
            if request.price is not None:
                params["price"] = self._fmt(request.price)

        raw = self._request_signed("POST", "/v5/order/create", params=params)
        return OrderResponse(
            order_id=str(raw.get("orderId", "")),
            client_order_id=raw.get("orderLinkId", request.client_order_id),
            symbol=request.symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            status="NEW",
            raw=raw,
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an order on Bybit."""
        params: dict[str, Any] = {
            "category": "spot",
            "symbol": symbol.upper(),
        }
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id:
            params["orderLinkId"] = client_order_id
        return self._request_signed("POST", "/v5/order/cancel", params=params)

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return list of open orders for the given symbol."""
        payload = self._request_signed(
            "GET",
            "/v5/order/realtime",
            params={"category": "spot", "symbol": symbol.upper()},
        )
        if isinstance(payload, dict):
            orders = payload.get("list", [])
            if isinstance(orders, list):
                return [x for x in orders if isinstance(x, dict)]
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        return []
