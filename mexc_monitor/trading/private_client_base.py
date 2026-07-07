"""Abstract base private client and data models for multi-exchange trading."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from mexc_monitor.trading.exchanges import OrderSide, OrderType


class PrivateApiError(RuntimeError):
    """Raised on HTTP errors or exchange-specific error codes."""

    pass


@dataclass
class OrderRequest:
    """Unified order request for all exchanges."""

    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None  # None for MARKET orders
    client_order_id: str
    time_in_force: str = "GTC"


@dataclass
class OrderResponse:
    """Unified order response from any exchange."""

    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    status: str
    raw: dict[str, Any]


class BasePrivateClient(ABC):
    """Abstract base for exchange-specific authenticated API clients."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        timeout_sec: float = 20.0,
        recv_window_ms: int = 5_000,
    ):
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._recv_window_ms = recv_window_ms

    @abstractmethod
    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign request parameters per exchange specification.

        Returns a new dict with signature fields added.
        """
        ...

    @abstractmethod
    def _get_api_key_header(self) -> str:
        """Return the header name for the API key (e.g. 'X-MBX-APIKEY')."""
        ...

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order on the exchange."""
        ...

    @abstractmethod
    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel an order by order_id or client_order_id."""
        ...

    @abstractmethod
    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Return list of open orders for the given symbol."""
        ...

    def has_credentials(self) -> bool:
        """Check if valid credentials are configured."""
        return bool(self._api_key and self._api_secret)

    def _request_signed(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a signed HTTP request against the exchange API.

        Signs the parameters using the exchange-specific _sign() method,
        attaches the API key header, and handles error responses.
        """
        signed = self._sign(params or {})
        url = f"{self._base_url}{path}"
        headers = {self._get_api_key_header(): self._api_key}

        with httpx.Client(timeout=self._timeout_sec) as client:
            if method == "GET":
                r = client.get(url, params=signed, headers=headers)
            elif method == "POST":
                r = client.post(url, params=signed, headers=headers)
            else:
                r = client.delete(url, params=signed, headers=headers)

        if r.status_code >= 400:
            raise PrivateApiError(
                f"HTTP {r.status_code}: {r.text[:300]}"
            )

        payload = r.json()
        if isinstance(payload, dict):
            code = payload.get("code")
            if code is not None and code not in (0, "0"):
                msg = payload.get("msg") or payload.get("message") or "unknown error"
                raise PrivateApiError(
                    f"Exchange error code={code} msg={msg}"
                )
            return payload
        if isinstance(payload, list):
            return payload
        raise PrivateApiError(f"Unexpected response type: {type(payload)}")

    @staticmethod
    def _fmt(value: float) -> str:
        """Format a float without scientific notation for exchange APIs."""
        out = f"{value:.12f}".rstrip("0").rstrip(".")
        return out if out else "0"
