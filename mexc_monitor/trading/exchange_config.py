"""Exchange configuration registry.

Static configuration for all supported exchanges including API base URLs,
authentication headers, and order endpoint paths.
"""

from __future__ import annotations

from dataclasses import dataclass

from mexc_monitor.trading.exchanges import Exchange


@dataclass(frozen=True)
class ExchangeConfig:
    """Static configuration for a supported exchange."""

    name: Exchange
    env_prefix: str  # e.g. "BINANCE", "MEXC", "GATEIO"
    spot_base_url: str
    futures_base_url: str
    api_key_header: str  # e.g. "X-MBX-APIKEY", "X-MEXC-APIKEY"
    supports_recv_window: bool
    spot_order_path: str
    futures_order_path: str


EXCHANGE_CONFIGS: dict[Exchange, ExchangeConfig] = {
    Exchange.MEXC: ExchangeConfig(
        name=Exchange.MEXC,
        env_prefix="MEXC",
        spot_base_url="https://api.mexc.com",
        futures_base_url="https://contract.mexc.com",
        api_key_header="X-MEXC-APIKEY",
        supports_recv_window=True,
        spot_order_path="/api/v3/order",
        futures_order_path="/api/v1/private/order/submit",
    ),
    Exchange.BINANCE: ExchangeConfig(
        name=Exchange.BINANCE,
        env_prefix="BINANCE",
        spot_base_url="https://api.binance.com",
        futures_base_url="https://fapi.binance.com",
        api_key_header="X-MBX-APIKEY",
        supports_recv_window=True,
        spot_order_path="/api/v3/order",
        futures_order_path="/fapi/v1/order",
    ),
    Exchange.BYBIT: ExchangeConfig(
        name=Exchange.BYBIT,
        env_prefix="BYBIT",
        spot_base_url="https://api.bybit.com",
        futures_base_url="https://api.bybit.com",
        api_key_header="X-BAPI-API-KEY",
        supports_recv_window=True,
        spot_order_path="/v5/order/create",
        futures_order_path="/v5/order/create",
    ),
    Exchange.OKX: ExchangeConfig(
        name=Exchange.OKX,
        env_prefix="OKX",
        spot_base_url="https://www.okx.com",
        futures_base_url="https://www.okx.com",
        api_key_header="OK-ACCESS-KEY",
        supports_recv_window=False,
        spot_order_path="/api/v5/trade/order",
        futures_order_path="/api/v5/trade/order",
    ),
    Exchange.GATEIO: ExchangeConfig(
        name=Exchange.GATEIO,
        env_prefix="GATEIO",
        spot_base_url="https://api.gateio.ws",
        futures_base_url="https://api.gateio.ws",
        api_key_header="KEY",
        supports_recv_window=False,
        spot_order_path="/api/v4/spot/orders",
        futures_order_path="/api/v4/futures/usdt/orders",
    ),
    Exchange.HTX: ExchangeConfig(
        name=Exchange.HTX,
        env_prefix="HTX",
        spot_base_url="https://api.huobi.pro",
        futures_base_url="https://api.hbdm.com",
        api_key_header="AccessKeyId",
        supports_recv_window=False,
        spot_order_path="/v1/order/orders/place",
        futures_order_path="/linear-swap-api/v1/swap_order",
    ),
    Exchange.BITGET: ExchangeConfig(
        name=Exchange.BITGET,
        env_prefix="BITGET",
        spot_base_url="https://api.bitget.com",
        futures_base_url="https://api.bitget.com",
        api_key_header="ACCESS-KEY",
        supports_recv_window=False,
        spot_order_path="/api/v2/spot/trade/place-order",
        futures_order_path="/api/v2/mix/order/place-order",
    ),
}
