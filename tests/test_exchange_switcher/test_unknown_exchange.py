"""
Property-based test: Unknown exchange validation (Property 5).

Feature: exchange-switcher
Property 5: For any string that is not in the set of supported exchanges,
the `/api/snapshot` endpoint shall return HTTP 400 with a response body containing
the list of supported exchanges.

Validates: Requirements 4.5, 10.3
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

_SUPPORTED_EXCHANGES = {
    "mexc", "asterdex", "lighter",
    "binance", "bybit", "okx", "gateio", "htx", "bitget", "dydx", "hyperliquid",
}


# Strategy: generate strings that are NOT valid exchange names
_invalid_exchange = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip().lower() not in _SUPPORTED_EXCHANGES)


class TestUnknownExchangeValidation:
    """Property 5: Unknown exchange validation."""

    @given(exchange=_invalid_exchange)
    @settings(max_examples=100, deadline=None)
    def test_unknown_exchange_returns_400(self, exchange: str):
        """Any string not in supported set should return HTTP 400."""
        assume(exchange.strip().lower() not in _SUPPORTED_EXCHANGES)

        response = client.get("/api/snapshot", params={"exchange": exchange})

        assert response.status_code == 400
        body = response.json()
        assert body["ok"] is False
        assert "supported" in body
        assert set(body["supported"]) == _SUPPORTED_EXCHANGES

    @given(exchange=_invalid_exchange)
    @settings(max_examples=100, deadline=None)
    def test_error_message_contains_exchange_name(self, exchange: str):
        """Error message should reference the invalid exchange name."""
        assume(exchange.strip().lower() not in _SUPPORTED_EXCHANGES)

        response = client.get("/api/snapshot", params={"exchange": exchange})

        assert response.status_code == 400
        body = response.json()
        assert exchange in body["error"]

    def test_known_exchanges_do_not_return_400(self):
        """Sanity check: valid exchanges should not return 400 (only checks status code)."""
        for ex in _SUPPORTED_EXCHANGES:
            response = client.get("/api/snapshot", params={"exchange": ex, "nocache": "true"})
            # May fail for other reasons (API unavailable), but should NOT be 400
            # Response could be 200 with ok=false if external API is down
            assert response.status_code != 400

    def test_empty_string_returns_400(self):
        """Empty string is not a valid exchange."""
        response = client.get("/api/snapshot", params={"exchange": ""})
        # Empty string after strip/lower is "" which is not in supported set
        assert response.status_code == 400

    @given(exchange=st.sampled_from(["MEXC", "Mexc", "ASTERDEX", "AsterDex", "LIGHTER", "Lighter", "BINANCE", "Binance", "BYBIT", "OKX", "GATEIO", "HTX", "BITGET", "DYDX", "HYPERLIQUID"]))
    @settings(max_examples=15, deadline=None)
    def test_case_insensitive_valid_exchanges(self, exchange: str):
        """Valid exchanges in different cases should NOT return 400 (case-insensitive)."""
        response = client.get("/api/snapshot", params={"exchange": exchange, "nocache": "true"})
        # The endpoint lowercases the input, so these should be valid
        assert response.status_code != 400
