"""Gate.io exchange private client — HMAC SHA512 on method\\npath\\nquery\\nhashed_body\\ntimestamp."""

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


class GateioPrivateClient(BasePrivateClient):
    """Gate.io private client.

    Signing: HMAC SHA512 on `method\\npath\\nquery\\nhashed_body\\ntimestamp`.
    Headers: KEY, SIGN, Timestamp.
    """

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign for Gate.io.

        Expects __method, __path, __query, __body metadata keys in params.
        Returns dict with __gateio_sign and __gateio_timestamp metadata.
        """
        method = params.pop("__method", "GET")
        path = params.pop("__path", "")
        query = params.pop("__query", "")
        body = params.pop("__body", "")

        timestamp = str(int(time.time()))

        # Hash the body with SHA512
        hashed_body = hashlib.sha512(body.encode("utf-8")).hexdigest()

        # Build the signing string
        pre_sign = f"{method}\n{path}\n{query}\n{hashed_body}\n{timestamp}"
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            pre_sign.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        signed = dict(params)
        signed["__gateio_sign"] = signature
        signed["__gateio_timestamp"] = timestamp
        return signed

    def _get_api_key_header(self) -> str:
        return "KEY"

    def _request_signed(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Override base to use Gate.io-specific header-based auth with JSON body."""
        request_params = dict(params or {})
        body_str = ""
        query_str = ""

        if method in ("POST",) and request_params:
            body_str = json.dumps(request_params)
        elif method == "GET" and request_params:
            query_str = urlencode(sorted(request_params.items()))

        # Inject metadata for signing
        sign_input: dict[str, Any] = {
            "__method": method,
            "__path": path,
            "__query": query_str,
            "__body": body_str,
        }
        signed = self._sign(sign_input)

        signature = signed.pop("__gateio_sign")
        timestamp = signed.pop("__gateio_timestamp")

        url = f"{self._base_url}{path}"
        headers = {
            "KEY": self._api_key,
            "SIGN": signature,
            "Timestamp": timestamp,
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
            label = payload.get("label")
            if label:
                msg = payload.get("message") or "unknown error"
                raise PrivateApiError(f"Gate.io error label={label} msg={msg}")
            return payload
        if isinstance(payload, list):
            return payload
        raise PrivateApiError(f"Unexpected response type: {type(payload)}")

    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order on Gate.io via /api/v4/spot/orders."""
        # Gate.io uses underscore-separated pairs like BTC_USDT
        currency_pair = request.symbol.upper()
        if "_" not in currency_pair and "USDT" in currency_pair:
            # Convert BTCUSDT → BTC_USDT
            base = currency_pair.replace("USDT", "")
            currency_pair = f"{base}_USDT"

        params: dict[str, Any] = {
            "currency_pair": currency_pair,
            "side": "buy" if request.side == OrderSide.BUY else "sell",
            "type": "limit" if request.order_type == OrderType.LIMIT else "market",
            "amount": self._fmt(request.quantity),
            "text": f"t-{request.client_order_id}",
        }
        if request.order_type == OrderType.LIMIT and request.price is not None:
            params["price"] = self._fmt(request.price)
            params["time_in_force"] = request.time_in_force.lower()

        raw = self._request_signed("POST", "/api/v4/spot/orders", params=params)
        return OrderResponse(
            order_id=str(raw.get("id", "")),
            client_order_id=raw.get("text", request.client_order_id),
            symbol=request.symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            status=raw.get("status", "open"),
            raw=raw,
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an order on Gate.io."""
        currency_pair = symbol.upper()
        if "_" not in currency_pair and "USDT" in currency_pair:
            base = currency_pair.replace("USDT", "")
            currency_pair = f"{base}_USDT"

        oid = order_id or client_order_id or ""
        return self._request_signed(
            "DELETE",
            f"/api/v4/spot/orders/{oid}",
            params={"currency_pair": currency_pair},
        )

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return list of open orders for the given symbol."""
        currency_pair = symbol.upper()
        if "_" not in currency_pair and "USDT" in currency_pair:
            base = currency_pair.replace("USDT", "")
            currency_pair = f"{base}_USDT"

        payload = self._request_signed(
            "GET",
            "/api/v4/spot/orders",
            params={"currency_pair": currency_pair, "status": "open"},
        )
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        return []
