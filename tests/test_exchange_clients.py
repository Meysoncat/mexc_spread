"""Tests for exchange-specific private clients and client factory.

Verifies:
- Each client is a subclass of BasePrivateClient
- Each client's _sign() is deterministic
- Each client's _get_api_key_header() returns the correct header
- Client factory creates correct client types
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from mexc_monitor.trading.exchanges import Exchange, Market
from mexc_monitor.trading.private_client_base import BasePrivateClient
from mexc_monitor.trading.private_client import MexcPrivateClient
from mexc_monitor.trading.clients.binance_client import BinancePrivateClient
from mexc_monitor.trading.clients.bybit_client import BybitPrivateClient
from mexc_monitor.trading.clients.okx_client import OkxPrivateClient
from mexc_monitor.trading.clients.gateio_client import GateioPrivateClient
from mexc_monitor.trading.clients.htx_client import HtxPrivateClient
from mexc_monitor.trading.clients.bitget_client import BitgetPrivateClient
from mexc_monitor.trading.client_factory import (
    CLIENT_CLASSES,
    create_private_client,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COMMON_KWARGS = {
    "api_key": "test_key_123",
    "api_secret": "test_secret_456",
    "base_url": "https://api.example.com",
    "recv_window_ms": 5000,
}


@pytest.fixture
def binance_client():
    return BinancePrivateClient(**COMMON_KWARGS)


@pytest.fixture
def bybit_client():
    return BybitPrivateClient(**COMMON_KWARGS)


@pytest.fixture
def okx_client():
    return OkxPrivateClient(**COMMON_KWARGS, passphrase="test_pass")


@pytest.fixture
def gateio_client():
    return GateioPrivateClient(**COMMON_KWARGS)


@pytest.fixture
def htx_client():
    return HtxPrivateClient(**COMMON_KWARGS)


@pytest.fixture
def bitget_client():
    return BitgetPrivateClient(**COMMON_KWARGS, passphrase="test_pass")


@pytest.fixture
def mexc_client():
    return MexcPrivateClient(**COMMON_KWARGS)


# ---------------------------------------------------------------------------
# Test: Each client is a subclass of BasePrivateClient
# ---------------------------------------------------------------------------

ALL_CLIENT_CLASSES = [
    MexcPrivateClient,
    BinancePrivateClient,
    BybitPrivateClient,
    OkxPrivateClient,
    GateioPrivateClient,
    HtxPrivateClient,
    BitgetPrivateClient,
]


@pytest.mark.parametrize("client_class", ALL_CLIENT_CLASSES)
def test_client_is_subclass_of_base(client_class):
    """Each exchange client must be a subclass of BasePrivateClient."""
    assert issubclass(client_class, BasePrivateClient)


# ---------------------------------------------------------------------------
# Test: _sign() is deterministic (same inputs → same output)
# ---------------------------------------------------------------------------

class TestSigningDeterminism:
    """Verify that _sign() produces identical output for identical inputs."""

    def test_binance_sign_deterministic(self, binance_client):
        params = {"symbol": "BTCUSDT", "side": "BUY", "timestamp": 1700000000000}
        result1 = binance_client._sign(dict(params))
        result2 = binance_client._sign(dict(params))
        assert result1["signature"] == result2["signature"]

    def test_bybit_sign_deterministic(self, bybit_client):
        params = {"symbol": "BTCUSDT", "side": "Buy"}
        result1 = bybit_client._sign(dict(params))
        result2 = bybit_client._sign(dict(params))
        assert result1["__bybit_sign"] == result2["__bybit_sign"]

    def test_okx_sign_deterministic(self, okx_client):
        params = {"__method": "POST", "__path": "/api/v5/trade/order", "__body": '{"instId":"BTC-USDT"}'}
        result1 = okx_client._sign(dict(params))
        result2 = okx_client._sign(dict(params))
        assert result1["__okx_sign"] == result2["__okx_sign"]

    def test_gateio_sign_deterministic(self, gateio_client):
        params = {
            "__method": "POST",
            "__path": "/api/v4/spot/orders",
            "__query": "",
            "__body": '{"currency_pair":"BTC_USDT"}',
        }
        result1 = gateio_client._sign(dict(params))
        result2 = gateio_client._sign(dict(params))
        assert result1["__gateio_sign"] == result2["__gateio_sign"]

    def test_htx_sign_deterministic(self, htx_client):
        params = {"__method": "GET", "__path": "/v1/order/openOrders", "symbol": "btcusdt"}
        result1 = htx_client._sign(dict(params))
        result2 = htx_client._sign(dict(params))
        assert result1["Signature"] == result2["Signature"]

    def test_bitget_sign_deterministic(self, bitget_client):
        params = {"__method": "POST", "__path": "/api/v2/spot/trade/place-order", "__body": '{"symbol":"BTCUSDT"}'}
        result1 = bitget_client._sign(dict(params))
        result2 = bitget_client._sign(dict(params))
        assert result1["__bitget_sign"] == result2["__bitget_sign"]

    def test_mexc_sign_deterministic(self, mexc_client):
        params = {"symbol": "BTCUSDT", "side": "BUY", "timestamp": 1700000000000}
        with patch("mexc_monitor.trading.private_client.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            result1 = mexc_client._sign(dict(params))
            result2 = mexc_client._sign(dict(params))
        assert result1["signature"] == result2["signature"]


# ---------------------------------------------------------------------------
# Test: _get_api_key_header() returns correct header name
# ---------------------------------------------------------------------------

EXPECTED_HEADERS = {
    "mexc": ("X-MEXC-APIKEY", MexcPrivateClient),
    "binance": ("X-MBX-APIKEY", BinancePrivateClient),
    "bybit": ("X-BAPI-API-KEY", BybitPrivateClient),
    "okx": ("OK-ACCESS-KEY", OkxPrivateClient),
    "gateio": ("KEY", GateioPrivateClient),
    "htx": ("AccessKeyId", HtxPrivateClient),
    "bitget": ("ACCESS-KEY", BitgetPrivateClient),
}


@pytest.mark.parametrize(
    "exchange_name,expected",
    [(k, v[0]) for k, v in EXPECTED_HEADERS.items()],
    ids=list(EXPECTED_HEADERS.keys()),
)
def test_api_key_header(exchange_name, expected):
    """Each client returns the correct API key header name."""
    client_class = EXPECTED_HEADERS[exchange_name][1]
    kwargs = dict(COMMON_KWARGS)
    if client_class in (OkxPrivateClient, BitgetPrivateClient):
        kwargs["passphrase"] = "test"
    client = client_class(**kwargs)
    assert client._get_api_key_header() == expected


# ---------------------------------------------------------------------------
# Test: Client factory creates correct client types
# ---------------------------------------------------------------------------

class TestClientFactory:
    """Verify client factory creates the correct client class for each exchange."""

    def test_client_classes_mapping_complete(self):
        """CLIENT_CLASSES has an entry for every Exchange enum member."""
        for exchange in Exchange:
            assert exchange in CLIENT_CLASSES

    @pytest.mark.parametrize("exchange", list(Exchange))
    def test_factory_creates_correct_type(self, exchange):
        """create_private_client returns an instance of the correct class."""
        env_vars = {
            f"{exchange.value.upper()}_API_KEY": "key123",
            f"{exchange.value.upper()}_API_SECRET": "secret456",
        }
        # OKX and Bitget need passphrase
        if exchange == Exchange.OKX:
            env_vars["OKX_PASSPHRASE"] = "pass"
        elif exchange == Exchange.BITGET:
            env_vars["BITGET_PASSPHRASE"] = "pass"

        with patch.dict(os.environ, env_vars, clear=False):
            client = create_private_client(exchange, Market.SPOT)
            assert isinstance(client, CLIENT_CLASSES[exchange])
            assert isinstance(client, BasePrivateClient)

    @pytest.mark.parametrize("exchange", list(Exchange))
    def test_factory_spot_url(self, exchange):
        """Factory uses spot_base_url when market is SPOT."""
        from mexc_monitor.trading.exchange_config import EXCHANGE_CONFIGS

        env_vars = {
            f"{exchange.value.upper()}_API_KEY": "key",
            f"{exchange.value.upper()}_API_SECRET": "secret",
        }
        if exchange == Exchange.OKX:
            env_vars["OKX_PASSPHRASE"] = "pass"
        elif exchange == Exchange.BITGET:
            env_vars["BITGET_PASSPHRASE"] = "pass"

        with patch.dict(os.environ, env_vars, clear=False):
            client = create_private_client(exchange, Market.SPOT)
            expected_url = EXCHANGE_CONFIGS[exchange].spot_base_url
            assert client._base_url == expected_url

    @pytest.mark.parametrize("exchange", list(Exchange))
    def test_factory_futures_url(self, exchange):
        """Factory uses futures_base_url when market is FUTURES."""
        from mexc_monitor.trading.exchange_config import EXCHANGE_CONFIGS

        env_vars = {
            f"{exchange.value.upper()}_API_KEY": "key",
            f"{exchange.value.upper()}_API_SECRET": "secret",
        }
        if exchange == Exchange.OKX:
            env_vars["OKX_PASSPHRASE"] = "pass"
        elif exchange == Exchange.BITGET:
            env_vars["BITGET_PASSPHRASE"] = "pass"

        with patch.dict(os.environ, env_vars, clear=False):
            client = create_private_client(exchange, Market.FUTURES)
            expected_url = EXCHANGE_CONFIGS[exchange].futures_base_url
            assert client._base_url == expected_url

    def test_factory_reads_env_credentials(self):
        """Factory reads API key and secret from environment variables."""
        env_vars = {
            "BINANCE_API_KEY": "my_binance_key",
            "BINANCE_API_SECRET": "my_binance_secret",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            client = create_private_client(Exchange.BINANCE, Market.SPOT)
            assert client._api_key == "my_binance_key"
            assert client._api_secret == "my_binance_secret"

    def test_factory_empty_credentials_when_missing(self):
        """Factory returns client with empty credentials when env vars missing."""
        env_vars = {}
        with patch.dict(os.environ, env_vars, clear=True):
            # Need to ensure the env is clean
            for key in list(os.environ.keys()):
                if key.startswith("BYBIT_"):
                    del os.environ[key]
            client = create_private_client(Exchange.BYBIT, Market.SPOT)
            assert client._api_key == ""
            assert client._api_secret == ""
            assert client.has_credentials() is False
