"""Tests for mexc_monitor/futures_arb/balance_checker.py.

Covers spot_base_asset derivation and MexcSpotBalanceChecker behaviour,
including the fail-closed semantics used to gate reverse cash-and-carry.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from mexc_monitor.futures_arb.balance_checker import (
    MexcSpotBalanceChecker,
    spot_base_asset,
)


# ─── spot_base_asset ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("BTCUSDT", "BTC"),
        ("ETHUSDT", "ETH"),
        ("SOLUSDC", "SOL"),
        ("BTCFDUSD", "BTC"),
        ("btc_usdt", "BTC"),  # case + underscore normalization
        ("BTC-USDT", "BTC"),
        ("USDTUSDT", "USDT"),  # base happens to equal quote suffix
        ("", None),
        ("FOOBAR", None),  # no known quote suffix
    ],
)
def test_spot_base_asset(symbol: str, expected: str | None) -> None:
    assert spot_base_asset(symbol) == expected


# ─── MexcSpotBalanceChecker ─────────────────────────────────────────────────


class _FakeClient:
    """Replaces MexcPrivateClient inside the checker's `with` block."""

    def __init__(self, account_payload, *, raise_on_account: bool = False):
        self._payload = account_payload
        self._raise = raise_on_account

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_account(self):
        if self._raise:
            raise RuntimeError("simulated network / auth failure")
        return self._payload


def test_unconfigured_returns_infinity_no_enforcement():
    """Without MEXC spot credentials the check must not enforce (paper mode)."""
    checker = MexcSpotBalanceChecker(api_key="", api_secret="")
    assert not checker.configured
    assert checker.get_available_spot_balance("BTCUSDT") == float("inf")


def test_configured_returns_free_balance_of_base_asset():
    checker = MexcSpotBalanceChecker(api_key="k", api_secret="s")
    assert checker.configured
    payload = {
        "balances": [
            {"asset": "ETH", "free": "2.5", "locked": "0.1"},
            {"asset": "BTC", "free": "0.123", "locked": "0.0"},
        ]
    }
    with patch(
        "mexc_monitor.trading.private_client.MexcPrivateClient",
        return_value=_FakeClient(payload),
    ):
        assert checker.get_available_spot_balance("BTCUSDT") == pytest.approx(0.123)


def test_configured_returns_zero_when_base_asset_absent():
    checker = MexcSpotBalanceChecker(api_key="k", api_secret="s")
    payload = {"balances": [{"asset": "ETH", "free": "5.0"}]}
    with patch(
        "mexc_monitor.trading.private_client.MexcPrivateClient",
        return_value=_FakeClient(payload),
    ):
        assert checker.get_available_spot_balance("BTCUSDT") == 0.0


def test_configured_returns_zero_for_unrecognized_symbol():
    checker = MexcSpotBalanceChecker(api_key="k", api_secret="s")
    # No known quote suffix → cannot derive base → 0.0 (safe).
    with patch(
        "mexc_monitor.trading.private_client.MexcPrivateClient",
        return_value=_FakeClient({"balances": []}),
    ):
        assert checker.get_available_spot_balance("FOOBAR") == 0.0


def test_configured_fails_closed_on_account_error():
    """If the balance query raises, the checker must report 0.0, not inf.

    Reverse C&C must not be allowed when we cannot confirm the spot balance.
    """
    checker = MexcSpotBalanceChecker(api_key="k", api_secret="s")
    with patch(
        "mexc_monitor.trading.private_client.MexcPrivateClient",
        return_value=_FakeClient({}, raise_on_account=True),
    ):
        assert checker.get_available_spot_balance("BTCUSDT") == 0.0


def test_handles_malformed_balance_row_gracefully():
    checker = MexcSpotBalanceChecker(api_key="k", api_secret="s")
    payload = {
        "balances": [
            {"asset": "BTC", "free": "not-a-number"},  # bad value
            {"asset": "ETH", "free": "3.0"},
        ]
    }
    with patch(
        "mexc_monitor.trading.private_client.MexcPrivateClient",
        return_value=_FakeClient(payload),
    ):
        # Bad BTC row → 0.0; the search does not fall through to other assets.
        assert checker.get_available_spot_balance("BTCUSDT") == 0.0
