"""Bitget exchange private client — HMAC SHA256 → Base64 on timestamp+method+path+body."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Literal

import httpx

from mexc_monitor.trading.exchanges import OrderSide, OrderType
from mexc_monitor.trading.private_client_base import (
    BasePrivateClient,
    OrderRequest,
    OrderResponse,
    PrivateApiError,
)


class BitgetPrivateClient(BasePrivateClient):
    """Bitget private client.

    Signing: HMAC SHA256 → Base64 on `timestamp + method + path + body`.
    Headers: ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE.
    Passphrase from BITGET_PASSPHRASE env var.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        timeout_sec: float = 20.0,
        recv_window_ms: int = 5_000,
        passphrase: str | None = None,
    ):
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            timeout_sec=timeout_sec,
            recv_window_ms=recv_window_ms,
        )
        self._passphrase = passphrase or os.environ.get("BITGET_PASSPHRASE", "")

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign for Bitget.

        Expects __method, __path, __body metadata keys in params.
        Returns dict with __bitget_timestamp and __bitget_sign metadata.
        """
        timestamp = str(int(time.time() * 1000))
        method = params.pop("__method", "GET")
        path = params.pop("__path", "")
        body = params.pop("__body", "")

        pre_sign = f"{timestamp}{method}{path}{body}"
        mac = hmac.new(
            self._api_secret.encode("utf-8"),
            pre_sign.encode("utf-8"),
            hashlib.sha256,
        )
        signature = base64.b64encode(mac.digest()).decode("utf-8")

        signed = dict(params)
        signed["__bitget_timestamp"] = timestamp
        signed["__bitget_sign"] = signature
        return signed

    def _get_api_key_header(self) -> str:
        return "ACCESS-KEY"

    def _request_signed(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Override base to use Bitget-specific header-based auth with JSON body."""
        request_params = dict(params or {})
        body_str = ""
        query_str = ""

        if method in ("POST",) and request_params:
            body_str = json.dumps(request_params)
        elif method == "GET" and request_params:
            from urllib.parse import urlencode
            query_str = "?" + urlencode(sorted(request_params.items()))

        # Inject metadata for signing
        sign_input: dict[str, Any] = {
            "__method": method,
            "__path": path + query_str,
            "__body": body_str,
        }
        signed = self._sign(sign_input)

        timestamp = signed.pop("__bitget_timestamp")
        signature = signed.pop("__bitget_sign")

        url = f"{self._base_url}{path}"
        headers = {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self._passphrase,
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=self._timeout_sec) as client:
            if method == "GET":
                r = client.get(url, params=request_params, headers=headers)
            elif method == "POST":
                r = client.post(url, content=body_str, headers=headers)
            else:
                r = client.delete(url, params=request_params, headers=headers)

        if r.status_code >= 400:
            raise PrivateApiError(f"HTTP {r.status_code}: {r.text[:300]}")

        payload = r.json()
        if isinstance(payload, dict):
            code = payload.get("code")
            if code is not None and str(code) != "00000":
                msg = payload.get("msg") or "unknown error"
                raise PrivateApiError(f"Bitget error code={code} msg={msg}")
            data = payload.get("data")
            if data is not None:
                return data
            return payload
        if isinstance(payload, list):
            return payload
        raise PrivateApiError(f"Unexpected response type: {type(payload)}")

    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order on Bitget via /api/v2/spot/trade/place-order."""
        params: dict[str, Any] = {
            "symbol": request.symbol.upper(),
            "side": "buy" if request.side == OrderSide.BUY else "sell",
            "orderType": "limit" if request.order_type == OrderType.LIMIT else "market",
            "size": self._fmt(request.quantity),
            "clientOid": request.client_order_id,
            "force": "gtc" if request.order_type == OrderType.LIMIT else "normal",
        }
        if request.order_type == OrderType.LIMIT and request.price is not None:
            params["price"] = self._fmt(request.price)

        raw = self._request_signed(
            "POST", "/api/v2/spot/trade/place-order", params=params
        )
        return OrderResponse(
            order_id=str(raw.get("orderId", "")),
            client_order_id=raw.get("clientOid", request.client_order_id),
            symbol=request.symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            status="new",
            raw=raw if isinstance(raw, dict) else {"orderId": str(raw)},
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an order on Bitget."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id:
            params["clientOid"] = client_order_id
        result = self._request_signed(
            "POST", "/api/v2/spot/trade/cancel-order", params=params
        )
        return result if isinstance(result, dict) else {"orderId": str(result)}

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return list of open orders for the given symbol."""
        payload = self._request_signed(
            "GET",
            "/api/v2/spot/trade/unfilled-orders",
            params={"symbol": symbol.upper()},
        )
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            data = payload.get("orderList") or payload.get("data")
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        return []
