"""
AsterDEX Private API Client — торговля (HMAC SHA256).

Аутентификация идентична Binance Futures:
  - Header: X-MBX-APIKEY
  - Подпись: HMAC SHA256 от query string
  - Параметры: timestamp + recvWindow + signature

Base URL: https://fapi.asterdex.com
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Literal
from urllib.parse import urlencode

import httpx


class AsterPrivateApiError(RuntimeError):
    """Ошибка приватного API AsterDEX."""
    pass


class AsterPrivateClient:
    """Приватный клиент AsterDEX для торговли."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = "https://fapi.asterdex.com",
        timeout_sec: float = 15.0,
        recv_window_ms: int = 5_000,
    ):
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._recv_window_ms = max(1_000, int(recv_window_ms))
        self._client: httpx.Client | None = None

    def __enter__(self) -> "AsterPrivateClient":
        self._client = httpx.Client(
            timeout=self._timeout_sec,
            headers={"X-MBX-APIKEY": self._api_key},
        )
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None

    def _require_client(self) -> httpx.Client:
        if self._client is None:
            raise AsterPrivateApiError("AsterPrivateClient is not opened (use 'with' statement)")
        return self._client

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Добавляет timestamp, recvWindow и signature к параметрам."""
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

    def _request_signed(
        self,
        method: Literal["GET", "POST", "DELETE", "PUT"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        client = self._require_client()
        signed = self._sign(params or {})
        url = f"{self._base_url}{path}"
        if method == "GET":
            r = client.get(url, params=signed)
        elif method == "POST":
            r = client.post(url, params=signed)
        elif method == "DELETE":
            r = client.delete(url, params=signed)
        else:
            r = client.put(url, params=signed)
        if r.status_code >= 400:
            raise AsterPrivateApiError(f"HTTP {r.status_code}: {r.text[:500]}")
        try:
            payload = r.json()
        except Exception as e:
            raise AsterPrivateApiError(f"Invalid JSON response: {e}") from e
        if isinstance(payload, dict):
            code = payload.get("code")
            if code is not None and code not in (0, "0", 200, "200"):
                msg = payload.get("msg") or payload.get("message") or "unknown error"
                raise AsterPrivateApiError(f"Aster error code={code} msg={msg}")
        return payload

    # ─── Account ────────────────────────────────────────────────────────────

    def get_account(self) -> dict[str, Any]:
        """Информация об аккаунте (балансы, позиции)."""
        data = self._request_signed("GET", "/fapi/v1/account")
        if not isinstance(data, dict):
            raise AsterPrivateApiError(f"Unexpected account response: {type(data)}")
        return data

    def get_balance(self) -> list[dict[str, Any]]:
        """Балансы аккаунта."""
        data = self._request_signed("GET", "/fapi/v1/balance")
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "assets" in data:
            return data["assets"]
        return []

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Открытые позиции."""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        data = self._request_signed("GET", "/fapi/v1/positionRisk", params=params)
        if isinstance(data, list):
            return data
        return []

    # ─── Orders ─────────────────────────────────────────────────────────────

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Открытые ордера."""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        data = self._request_signed("GET", "/fapi/v1/openOrders", params=params)
        if isinstance(data, list):
            return data
        return []

    def place_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        order_type: str = "LIMIT",
        quantity: float,
        price: float | None = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        client_order_id: str | None = None,
        position_side: str = "BOTH",
    ) -> dict[str, Any]:
        """
        Размещение ордера.
        
        order_type: LIMIT, MARKET, STOP, STOP_MARKET, TAKE_PROFIT, TAKE_PROFIT_MARKET
        """
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": self._fmt(quantity),
            "positionSide": position_side.upper(),
        }
        if price is not None and order_type.upper() != "MARKET":
            params["price"] = self._fmt(price)
        if order_type.upper() in ("LIMIT",):
            params["timeInForce"] = time_in_force
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return self._request_signed("POST", "/fapi/v1/order", params=params)

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        quantity: float,
        price: float,
        time_in_force: str = "GTC",
        client_order_id: str | None = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """Лимитный ордер (shortcut)."""
        return self.place_order(
            symbol=symbol,
            side=side,
            order_type="LIMIT",
            quantity=quantity,
            price=price,
            time_in_force=time_in_force,
            client_order_id=client_order_id,
            reduce_only=reduce_only,
        )

    def place_market_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        quantity: float,
        client_order_id: str | None = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """Маркет ордер (shortcut)."""
        return self.place_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=quantity,
            client_order_id=client_order_id,
            reduce_only=reduce_only,
        )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Отмена ордера."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = int(order_id)
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        return self._request_signed("DELETE", "/fapi/v1/order", params=params)

    def cancel_all_orders(self, symbol: str) -> dict[str, Any]:
        """Отмена всех ордеров по символу."""
        return self._request_signed(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            params={"symbol": symbol.upper()},
        )

    def get_order(
        self,
        *,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Статус ордера."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = int(order_id)
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        data = self._request_signed("GET", "/fapi/v1/order", params=params)
        if not isinstance(data, dict):
            raise AsterPrivateApiError(f"Unexpected order response: {type(data)}")
        return data

    # ─── Leverage & Margin ──────────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        """Установить плечо."""
        return self._request_signed("POST", "/fapi/v1/leverage", params={
            "symbol": symbol.upper(),
            "leverage": max(1, int(leverage)),
        })

    def set_margin_type(self, symbol: str, margin_type: Literal["ISOLATED", "CROSSED"]) -> dict[str, Any]:
        """Установить тип маржи."""
        return self._request_signed("POST", "/fapi/v1/marginType", params={
            "symbol": symbol.upper(),
            "marginType": margin_type.upper(),
        })

    # ─── Trade History ──────────────────────────────────────────────────────

    def get_trades(self, symbol: str, limit: int = 50) -> list[dict[str, Any]]:
        """История сделок."""
        data = self._request_signed("GET", "/fapi/v1/userTrades", params={
            "symbol": symbol.upper(),
            "limit": min(limit, 1000),
        })
        return data if isinstance(data, list) else []

    def get_income_history(
        self,
        symbol: str | None = None,
        income_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """История доходов (funding, realized PNL и т.д.)."""
        params: dict[str, Any] = {"limit": min(limit, 1000)}
        if symbol:
            params["symbol"] = symbol.upper()
        if income_type:
            params["incomeType"] = income_type
        data = self._request_signed("GET", "/fapi/v1/income", params=params)
        return data if isinstance(data, list) else []

    @staticmethod
    def _fmt(value: float) -> str:
        """Форматирование числа без научной нотации."""
        out = f"{value:.12f}".rstrip("0").rstrip(".")
        return out if out else "0"
