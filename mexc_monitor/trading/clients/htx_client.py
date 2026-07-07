"""HTX (Huobi) exchange private client — HMAC SHA256 on method\\nhost\\npath\\nsorted_params."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

import httpx

from mexc_monitor.trading.exchanges import OrderSide, OrderType
from mexc_monitor.trading.private_client_base import (
    BasePrivateClient,
    OrderRequest,
    OrderResponse,
    PrivateApiError,
)


class HtxPrivateClient(BasePrivateClient):
    """HTX (Huobi) private client.

    Signing: HMAC SHA256 → Base64 on `method\\nhost\\npath\\nsorted_params`.
    Auth: AccessKeyId, SignatureMethod, SignatureVersion, Timestamp in query params.
    Signature appended to query params.
    """

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign for HTX.

        Expects __method, __path metadata keys in params.
        Adds AccessKeyId, SignatureMethod, SignatureVersion, Timestamp, Signature.
        """
        method = params.pop("__method", "GET")
        path = params.pop("__path", "")

        # Parse host from base_url
        parsed = urlparse(self._base_url)
        host = parsed.hostname or "api.huobi.pro"

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        # Build sorted params including auth params
        sign_params = dict(params)
        sign_params["AccessKeyId"] = self._api_key
        sign_params["SignatureMethod"] = "HmacSHA256"
        sign_params["SignatureVersion"] = "2"
        sign_params["Timestamp"] = timestamp

        sorted_query = urlencode(sorted(sign_params.items()))

        # Build the pre-sign string
        pre_sign = f"{method}\n{host}\n{path}\n{sorted_query}"
        mac = hmac.new(
            self._api_secret.encode("utf-8"),
            pre_sign.encode("utf-8"),
            hashlib.sha256,
        )
        signature = base64.b64encode(mac.digest()).decode("utf-8")

        sign_params["Signature"] = signature
        return sign_params

    def _get_api_key_header(self) -> str:
        return "AccessKeyId"

    def _request_signed(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Override base to use HTX-specific query-param-based auth."""
        request_params = dict(params or {})
        body_params: dict[str, Any] = {}

        # For POST, body goes as JSON, auth params go in query
        if method == "POST":
            body_params = request_params.copy()
            request_params = {}

        # Inject metadata for signing
        request_params["__method"] = method
        request_params["__path"] = path
        signed_query = self._sign(request_params)

        url = f"{self._base_url}{path}"

        with httpx.Client(timeout=self._timeout_sec) as client:
            if method == "GET":
                r = client.get(url, params=signed_query)
            elif method == "POST":
                r = client.post(
                    url,
                    params=signed_query,
                    json=body_params,
                    headers={"Content-Type": "application/json"},
                )
            else:
                r = client.delete(url, params=signed_query)

        if r.status_code >= 400:
            raise PrivateApiError(f"HTTP {r.status_code}: {r.text[:300]}")

        payload = r.json()
        if isinstance(payload, dict):
            status = payload.get("status")
            if status == "error":
                msg = payload.get("err-msg") or payload.get("message") or "unknown error"
                code = payload.get("err-code", "unknown")
                raise PrivateApiError(f"HTX error code={code} msg={msg}")
            data = payload.get("data")
            if data is not None:
                return data
            return payload
        if isinstance(payload, list):
            return payload
        raise PrivateApiError(f"Unexpected response type: {type(payload)}")

    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order on HTX via /v1/order/orders/place."""
        # HTX uses lowercase symbols like btcusdt
        symbol = request.symbol.lower().replace("_", "").replace("-", "")

        order_type_str = (
            "buy-limit" if request.side == OrderSide.BUY and request.order_type == OrderType.LIMIT
            else "sell-limit" if request.side == OrderSide.SELL and request.order_type == OrderType.LIMIT
            else "buy-market" if request.side == OrderSide.BUY
            else "sell-market"
        )

        params: dict[str, Any] = {
            "account-id": "",  # Will need to be resolved; placeholder
            "symbol": symbol,
            "type": order_type_str,
            "amount": self._fmt(request.quantity),
            "client-order-id": request.client_order_id,
        }
        if request.order_type == OrderType.LIMIT and request.price is not None:
            params["price"] = self._fmt(request.price)

        raw = self._request_signed("POST", "/v1/order/orders/place", params=params)
        # HTX returns order ID as the data field directly (string)
        order_id = str(raw) if not isinstance(raw, dict) else str(raw.get("data", raw.get("order-id", "")))
        return OrderResponse(
            order_id=order_id,
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            status="submitted",
            raw=raw if isinstance(raw, dict) else {"order_id": order_id},
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an order on HTX."""
        if order_id:
            result = self._request_signed(
                "POST", f"/v1/order/orders/{order_id}/submitcancel", params={}
            )
        elif client_order_id:
            result = self._request_signed(
                "POST",
                "/v1/order/orders/submitCancelClientOrder",
                params={"client-order-id": client_order_id},
            )
        else:
            raise PrivateApiError("HTX cancel requires order_id or client_order_id")
        return result if isinstance(result, dict) else {"order_id": str(result)}

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return list of open orders for the given symbol."""
        htx_symbol = symbol.lower().replace("_", "").replace("-", "")
        payload = self._request_signed(
            "GET",
            "/v1/order/openOrders",
            params={"symbol": htx_symbol},
        )
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        return []
