"""Client factory — creates configured private clients for any supported exchange."""

from __future__ import annotations

import os
from typing import Type

from mexc_monitor.trading.exchanges import Exchange, Market
from mexc_monitor.trading.exchange_config import EXCHANGE_CONFIGS
from mexc_monitor.trading.private_client_base import BasePrivateClient
from mexc_monitor.trading.private_client import MexcPrivateClient
from mexc_monitor.trading.clients.binance_client import BinancePrivateClient
from mexc_monitor.trading.clients.bybit_client import BybitPrivateClient
from mexc_monitor.trading.clients.okx_client import OkxPrivateClient
from mexc_monitor.trading.clients.gateio_client import GateioPrivateClient
from mexc_monitor.trading.clients.htx_client import HtxPrivateClient
from mexc_monitor.trading.clients.bitget_client import BitgetPrivateClient


CLIENT_CLASSES: dict[Exchange, Type[BasePrivateClient]] = {
    Exchange.MEXC: MexcPrivateClient,
    Exchange.BINANCE: BinancePrivateClient,
    Exchange.BYBIT: BybitPrivateClient,
    Exchange.OKX: OkxPrivateClient,
    Exchange.GATEIO: GateioPrivateClient,
    Exchange.HTX: HtxPrivateClient,
    Exchange.BITGET: BitgetPrivateClient,
}


def create_private_client(
    exchange: Exchange,
    market: Market,
) -> BasePrivateClient:
    """Factory: create a configured private client for the given exchange+market.

    Reads credentials from environment variables following the convention:
      {EXCHANGE}_API_KEY, {EXCHANGE}_API_SECRET

    Selects spot_base_url or futures_base_url based on market parameter.
    """
    config = EXCHANGE_CONFIGS[exchange]
    env_prefix = config.env_prefix

    api_key = os.environ.get(f"{env_prefix}_API_KEY", "")
    api_secret = os.environ.get(f"{env_prefix}_API_SECRET", "")
    recv_window = int(os.environ.get(f"{env_prefix}_RECV_WINDOW_MS", "5000"))

    base_url = (
        config.spot_base_url if market == Market.SPOT
        else config.futures_base_url
    )

    client_class = CLIENT_CLASSES[exchange]

    # OKX and Bitget need passphrase
    kwargs: dict = {
        "api_key": api_key,
        "api_secret": api_secret,
        "base_url": base_url,
        "recv_window_ms": recv_window,
    }

    if exchange == Exchange.OKX:
        kwargs["passphrase"] = os.environ.get("OKX_PASSPHRASE", "")
    elif exchange == Exchange.BITGET:
        kwargs["passphrase"] = os.environ.get("BITGET_PASSPHRASE", "")

    return client_class(**kwargs)
