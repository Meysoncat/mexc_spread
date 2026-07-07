"""OKX exchange private client — HMAC SHA256 → Base64 on timestamp+method+path+body."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from mexc_monitor.trading.exchanges import OrderSide, OrderType
from mexc_monitor.trading.private_client_base import (
    BasePrivateClient,
    OrderRequest,
    OrderResponse,
    PrivateApiError,
)


class OkxPrivateClient(BasePrivateClient):
    """OKX private client.

    Signing: HMAC SHA256 → Base64 on `timestamp + method + path + body`.
    Headers: OK-ACCESS-KEY, OK-ACCESS-SIGN, OK-ACCESS-TIMESTAMP, OK-ACCESS-PASSPHRASE.
    Passphrase from OKX_PASSPHRASE env var.
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
        self._passphrase = passphrase or os.environ.get("OKX_PASSPHRASE", "")

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign for OKX.

        For OKX, signing is done on `timestamp + method + requestPath + body`.
        This method stores signing metadata in the returned dict.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
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
        signed["__okx_timestamp"] = timestamp
        signed["__okx_sign"] = signature
        return signed

    def _get_api_key_header(self) -> str:
        return "OK-ACCESS-KEY"

    def _request_signed(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Override base to use OKX-specific header-based auth with JSON body."""
        request_params = dict(params or {})
        body_str = ""

        if method in ("POST",) and request_params:
            body_str = json.dumps(request_params)

        # Inject method/path/body for signing
        sign_input: dict[str, Any] = {
            "__method": method,
            "__path": path,
            "__body": body_str,
        }
        signed = self._sign(sign_input)

        timestamp = signed.pop("__okx_timestamp")
        signature = signed.pop("__okx_sign")

        url = f"{self._base_url}{path}"
        headers = {
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
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
            if code is not None and str(code) != "0":
                msg = payload.get("msg") or "unknown error"
                raise PrivateApiError(f"OKX error code={code} msg={msg}")
            data = payload.get("data")
            if isinstance(data, list) and len(data) == 1:
                return data[0]
            if data is not None:
                return data
            return payload
        if isinstance(payload, list):
            return payload
        raise PrivateApiError(f"Unexpected response type: {type(payload)}")

    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order on OKX via /api/v5/trade/order."""
        params: dict[str, Any] = {
            "instId": request.symbol.upper().replace("/", "-"),
            "tdMode": "cash",
            "side": "buy" if request.side == OrderSide.BUY else "sell",
            "ordType": "limit" if request.order_type == OrderType.LIMIT else "market",
            "sz": self._fmt(request.quantity),
            "clOrdId": request.client_order_id,
        }
        if request.order_type == OrderType.LIMIT and request.price is not None:
            params["px"] = self._fmt(request.price)

        raw = self._request_signed("POST", "/api/v5/trade/order", params=params)
        return OrderResponse(
            order_id=str(raw.get("ordId", "")),
            client_order_id=raw.get("clOrdId", request.client_order_id),
            symbol=request.symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            status=raw.get("sCode", "0"),
            raw=raw,
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an order on OKX."""
        params: dict[str, Any] = {
            "instId": symbol.upper().replace("/", "-"),
        }
        if order_id is not None:
            params["ordId"] = order_id
        if client_order_id:
            params["clOrdId"] = client_order_id
        return self._request_signed("POST", "/api/v5/trade/cancel-order", params=params)

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return list of open orders for the given symbol."""
        payload = self._request_signed(
            "GET",
            "/api/v5/trade/orders-pending",
            params={"instId": symbol.upper().replace("/", "-")},
        )
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        return []
