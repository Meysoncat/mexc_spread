"""Tests for refactored MexcPrivateClient extending BasePrivateClient."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

import pytest

from mexc_monitor.trading.exchanges import OrderSide, OrderType
from mexc_monitor.trading.private_client import MexcPrivateClient, PrivateApiError
from mexc_monitor.trading.private_client_base import (
    BasePrivateClient,
    OrderRequest,
    OrderResponse,
)


class TestMexcPrivateClientInheritance:
    """Verify MexcPrivateClient properly extends BasePrivateClient."""

    def test_is_subclass_of_base(self):
        assert issubclass(MexcPrivateClient, BasePrivateClient)

    def test_isinstance_check(self):
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        assert isinstance(client, BasePrivateClient)

    def test_has_credentials_from_base(self):
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        assert client.has_credentials() is True

    def test_has_credentials_false_when_empty(self):
        client = MexcPrivateClient(
            api_key="", api_secret="secret", base_url="https://api.mexc.com"
        )
        assert client.has_credentials() is False


class TestMexcSigning:
    """Verify HMAC SHA256 signing on sorted query string."""

    def test_sign_adds_timestamp(self):
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        signed = client._sign({"symbol": "BTCUSDT"})
        assert "timestamp" in signed
        assert isinstance(signed["timestamp"], int)

    def test_sign_adds_recv_window(self):
        client = MexcPrivateClient(
            api_key="key",
            api_secret="secret",
            base_url="https://api.mexc.com",
            recv_window_ms=7000,
        )
        signed = client._sign({"symbol": "BTCUSDT"})
        assert signed["recvWindow"] == 7000

    def test_sign_adds_signature(self):
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        signed = client._sign({"symbol": "BTCUSDT"})
        assert "signature" in signed
        assert len(signed["signature"]) == 64  # SHA256 hex digest

    def test_sign_deterministic(self):
        """Same inputs (including timestamp) produce same signature."""
        client = MexcPrivateClient(
            api_key="key", api_secret="mysecret", base_url="https://api.mexc.com"
        )
        # Manually compute expected signature
        params = {"symbol": "BTCUSDT", "side": "BUY", "timestamp": 1700000000000, "recvWindow": 5000}
        query = urlencode(sorted(params.items()))
        expected = hmac.new(
            b"mysecret", query.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        # Patch time to get deterministic timestamp
        with patch("mexc_monitor.trading.private_client.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            signed = client._sign({"symbol": "BTCUSDT", "side": "BUY"})

        assert signed["signature"] == expected

    def test_sign_sorted_query_string(self):
        """Params are sorted alphabetically before signing."""
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        with patch("mexc_monitor.trading.private_client.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            signed1 = client._sign({"b": "2", "a": "1"})
            signed2 = client._sign({"a": "1", "b": "2"})

        assert signed1["signature"] == signed2["signature"]

    def test_sign_preserves_original_params(self):
        """Original params dict is not mutated."""
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        original = {"symbol": "BTCUSDT"}
        client._sign(original)
        assert "timestamp" not in original
        assert "signature" not in original

    def test_recv_window_minimum_enforced(self):
        """recv_window_ms is clamped to minimum 1000."""
        client = MexcPrivateClient(
            api_key="key",
            api_secret="secret",
            base_url="https://api.mexc.com",
            recv_window_ms=100,
        )
        signed = client._sign({})
        assert signed["recvWindow"] == 1000


class TestMexcApiKeyHeader:
    def test_returns_mexc_header(self):
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        assert client._get_api_key_header() == "X-MEXC-APIKEY"


class TestMexcContextManager:
    """Verify backward-compatible context manager usage."""

    def test_enter_creates_httpx_client(self):
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        assert client._client is None
        with client:
            assert client._client is not None

    def test_exit_closes_client(self):
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        with client:
            pass
        assert client._client is None

    def test_enter_returns_self(self):
        client = MexcPrivateClient(
            api_key="key", api_secret="secret", base_url="https://api.mexc.com"
        )
        with client as c:
            assert c is client


class TestPrivateApiErrorReExport:
    """Verify PrivateApiError is properly re-exported for backward compatibility."""

    def test_importable_from_private_client(self):
        from mexc_monitor.trading.private_client import PrivateApiError as PAE
        assert issubclass(PAE, RuntimeError)

    def test_same_class_as_base(self):
        from mexc_monitor.trading.private_client import PrivateApiError as PAE
        from mexc_monitor.trading.private_client_base import PrivateApiError as PAE_Base
        assert PAE is PAE_Base
