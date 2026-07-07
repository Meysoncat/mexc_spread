"""
Exchange Adapter Protocol — унифицированный интерфейс для торговли на разных биржах.

Реализации:
  - MexcSpotAdapter: MEXC spot через existing private_client
  - AsterDexAdapter: AsterDEX perpetuals через aster/private_client
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal, Protocol


class ExchangeAdapter(Protocol):
    """Протокол для унификации торговых операций на разных биржах."""

    @property
    def exchange_name(self) -> str:
        """Имя биржи (для логов и UI)."""
        ...

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        quantity: float,
        price: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Разместить лимитный ордер. Возвращает ответ биржи."""
        ...

    def place_market_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        quantity: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Разместить маркет ордер."""
        ...

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Отменить ордер."""
        ...

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Получить открытые ордера по символу."""
        ...

    def get_spread_buffer_key(self, symbol: str) -> str:
        """Ключ символа в Spread_Buffer для этой биржи."""
        ...


class MexcSpotAdapter:
    """Адаптер для MEXC Spot."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        self._api_key = (api_key or os.environ.get("MEXC_API_KEY", "")).strip()
        self._api_secret = (api_secret or os.environ.get("MEXC_API_SECRET", "")).strip()

    @property
    def exchange_name(self) -> str:
        return "mexc_spot"

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def get_spread_buffer_key(self, symbol: str) -> str:
        return symbol.upper()

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        quantity: float,
        price: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        from mexc_monitor.config import DEFAULT_SETTINGS
        from mexc_monitor.trading.private_client import MexcPrivateClient

        cid = client_order_id or f"sc-{int(time.time()*1000)}"
        with MexcPrivateClient(
            api_key=self._api_key,
            api_secret=self._api_secret,
            base_url=DEFAULT_SETTINGS.base_url,
            timeout_sec=DEFAULT_SETTINGS.timeout_sec,
        ) as client:
            return client.place_limit_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                client_order_id=cid,
            )

    def place_market_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        quantity: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        # MEXC spot doesn't have a separate market order in our client — use limit at market price
        raise NotImplementedError("MEXC spot market orders not implemented in adapter")

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        from mexc_monitor.config import DEFAULT_SETTINGS
        from mexc_monitor.trading.private_client import MexcPrivateClient

        with MexcPrivateClient(
            api_key=self._api_key,
            api_secret=self._api_secret,
            base_url=DEFAULT_SETTINGS.base_url,
            timeout_sec=DEFAULT_SETTINGS.timeout_sec,
        ) as client:
            return client.cancel_order(
                symbol=symbol,
                order_id=int(order_id) if order_id else None,
                client_order_id=client_order_id,
            )

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        from mexc_monitor.config import DEFAULT_SETTINGS
        from mexc_monitor.trading.private_client import MexcPrivateClient

        with MexcPrivateClient(
            api_key=self._api_key,
            api_secret=self._api_secret,
            base_url=DEFAULT_SETTINGS.base_url,
            timeout_sec=DEFAULT_SETTINGS.timeout_sec,
        ) as client:
            return client.get_open_orders(symbol)


class AsterDexAdapter:
    """Адаптер для AsterDEX Perpetuals."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
    ):
        self._api_key = (api_key or os.environ.get("ASTER_API_KEY", "")).strip()
        self._api_secret = (api_secret or os.environ.get("ASTER_API_SECRET", "")).strip()

    @property
    def exchange_name(self) -> str:
        return "asterdex"

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def get_spread_buffer_key(self, symbol: str) -> str:
        return f"ASTER:{symbol.upper()}"

    def place_limit_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        quantity: float,
        price: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        from mexc_monitor.aster.private_client import AsterPrivateClient

        with AsterPrivateClient(
            api_key=self._api_key,
            api_secret=self._api_secret,
        ) as client:
            return client.place_limit_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                client_order_id=client_order_id,
            )

    def place_market_order(
        self,
        *,
        symbol: str,
        side: Literal["BUY", "SELL"],
        quantity: float,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        from mexc_monitor.aster.private_client import AsterPrivateClient

        with AsterPrivateClient(
            api_key=self._api_key,
            api_secret=self._api_secret,
        ) as client:
            return client.place_market_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                client_order_id=client_order_id,
            )

    def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        from mexc_monitor.aster.private_client import AsterPrivateClient

        with AsterPrivateClient(
            api_key=self._api_key,
            api_secret=self._api_secret,
        ) as client:
            return client.cancel_order(
                symbol=symbol,
                order_id=int(order_id) if order_id else None,
                client_order_id=client_order_id,
            )

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        from mexc_monitor.aster.private_client import AsterPrivateClient

        with AsterPrivateClient(
            api_key=self._api_key,
            api_secret=self._api_secret,
        ) as client:
            return client.get_open_orders(symbol=symbol)


def get_adapter(exchange: str) -> ExchangeAdapter:
    """Фабрика адаптеров по имени биржи."""
    if exchange == "mexc_spot":
        return MexcSpotAdapter()
    elif exchange == "asterdex":
        return AsterDexAdapter()
    else:
        raise ValueError(f"Unknown exchange: {exchange}. Supported: mexc_spot, asterdex")
