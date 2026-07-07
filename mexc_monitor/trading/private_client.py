"""MEXC exchange private client — extends BasePrivateClient.

Backward-compatible: retains context-manager usage, place_limit_order(),
get_account(), and the original cancel_order() signature used by engine.py
and adapters.py.
"""

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
    PrivateApiError,  # re-export for backward compatibility
)

# Re-export so `from mexc_monitor.trading.private_client import PrivateApiError` still works
__all__ = ["MexcPrivateClient", "PrivateApiError"]


class MexcPrivateClient(BasePrivateClient):
    """MEXC spot private client with HMAC SHA256 signing.

    Supports both:
    - New abstract interface (place_order, cancel_order, get_open_orders)
    - Legacy context-manager interface used by engine.py and adapters.py
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        timeout_sec: float = 20.0,
        recv_window_ms: int = 5_000,
    ):
        super().__init__(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            timeout_sec=timeout_sec,
            recv_window_ms=recv_window_ms,
        )
        self._recv_window_ms = max(1_000, int(recv_window_ms))
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------------
    # Context manager (backward compatibility for engine.py / adapters.py)
    # ------------------------------------------------------------------

    def __enter__(self) -> "MexcPrivateClient":
        self._client = httpx.Client(
            timeout=self._timeout_sec,
            headers={self._get_api_key_header(): self._api_key},
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign request params: add timestamp + recvWindow, HMAC SHA256 sorted query."""
        signed = dict(params)
        signed["timestamp"] = int(time.time() * 1000)
        signed["recvWindow"] = self._recv_window_ms
        query = urlencode(sorted((k, v) for k, v in signed.items()))
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed["signature"] = signature
        return signed

    def _get_api_key_header(self) -> str:
        return "X-MEXC-APIKEY"

    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order on MEXC spot via /api/v3/order."""
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

        raw = self._do_signed_request("POST", "/api/v3/order", params=params)
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
        order_id: str | int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an order by order_id or client_order_id."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = int(order_id)
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return self._do_signed_request("DELETE", "/api/v3/order", params=params)

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return list of open orders for the given symbol."""
        payload = self._do_signed_request(
            "GET",
            "/api/v3/openOrders",
            params={"symbol": symbol.upper()},
        )
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        # Some gateways may wrap list in a dict
        if isinstance(payload, dict):
            rows = payload.get("data")
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
        return []

    # ------------------------------------------------------------------
    # Legacy methods (backward compatibility)
    # ------------------------------------------------------------------

    def get_account(self) -> dict[str, Any]:
        """Get account information (legacy method)."""
        payload = self._do_signed_request("GET", "/api/v3/account")
        if not isinstance(payload, dict):
            raise PrivateApiError("Unexpected account payload type")
        return payload

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        quantity: float,
        price: float,
        client_order_id: str,
        time_in_force: str = "GTC",
    ) -> dict[str, Any]:
        """Place a limit order (legacy method used by engine.py and adapters.py).

        Returns the raw exchange response dict for backward compatibility.
        """
        return self._do_signed_request(
            "POST",
            "/api/v3/order",
            params={
                "symbol": symbol.upper(),
                "side": side.upper(),
                "type": "LIMIT",
                "quantity": self._fmt(quantity),
                "price": self._fmt(price),
                "timeInForce": time_in_force,
                "newClientOrderId": client_order_id,
            },
        )

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _do_signed_request(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a signed HTTP request.

        Uses the context-manager httpx.Client if available (legacy path),
        otherwise falls back to the base class _request_signed() method.
        """
        if self._client is not None:
            # Legacy context-manager path
            signed = self._sign(params or {})
            url = f"{self._base_url}{path}"
            if method == "GET":
                r = self._client.get(url, params=signed)
            elif method == "POST":
                r = self._client.post(url, params=signed)
            else:
                r = self._client.delete(url, params=signed)
            if r.status_code >= 400:
                raise PrivateApiError(f"HTTP {r.status_code}: {r.text[:300]}")
            payload = r.json()
            if isinstance(payload, dict):
                if "code" in payload and payload.get("code") not in (0, "0", None):
                    msg = payload.get("msg") or payload.get("message") or "unknown error"
                    raise PrivateApiError(
                        f"MEXC error code={payload.get('code')} msg={msg}"
                    )
                return payload
            if isinstance(payload, list):
                return payload
            raise PrivateApiError(f"Unexpected response type: {type(payload)}")
        else:
            # New path: use base class _request_signed (creates a fresh httpx.Client)
            return self._request_signed(method, path, params=params)
