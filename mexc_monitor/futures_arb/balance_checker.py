"""Spot balance checker for FuturesArb reverse cash-and-carry.

FuturesArbStrategyEngine gates reverse cash-and-carry (short spot + long perp)
on actually holding enough of the base asset on spot to sell. The engine's
``BalanceCheckerProtocol`` only declares ``get_available_spot_balance(symbol)``;
this module provides a concrete implementation backed by the MEXC spot account
(``/api/v3/account``).

Wiring this into the engine closes a live-trading gap: previously
``balance_checker`` was ``None`` in production, so ``_check_spot_balance``
unconditionally returned True and reverse C&C could attempt to sell spot the
account does not own.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Quote suffixes to strip when deriving the base asset from a spot symbol.
# Longest first so "USDT" is preferred over "USD".
_QUOTE_SUFFIXES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USD")


def spot_base_asset(symbol: str) -> str | None:
    """Derive the base asset from a spot symbol, e.g. ``"BTCUSDT"`` -> ``"BTC"``.

    Returns ``None`` if the symbol is empty or no known quote suffix matches.
    """
    if not symbol:
        return None
    s = symbol.upper().replace("_", "").replace("-", "")
    for quote in _QUOTE_SUFFIXES:
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return None


class MexcSpotBalanceChecker:
    """``BalanceCheckerProtocol`` implementation backed by the MEXC spot account.

    Returns the available (free) balance of the symbol's base asset. When MEXC
    spot credentials are not configured, returns ``+inf`` so the check is not
    enforced — this preserves paper-mode behaviour (the engine treats a missing
    checker as "always allow"; an unconfigured checker is equivalent). In live
    mode with credentials present, the real balance is returned and reverse
    cash-and-carry is gated on actually holding the base asset.
    """

    def __init__(self, api_key: str | None = None, api_secret: str | None = None) -> None:
        self._api_key = (api_key or os.environ.get("MEXC_API_KEY", "")).strip()
        self._api_secret = (api_secret or os.environ.get("MEXC_API_SECRET", "")).strip()

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def get_available_spot_balance(self, symbol: str) -> float:
        if not self.configured:
            # No credentials → do not enforce (paper mode / unconfigured live).
            return float("inf")

        base = spot_base_asset(symbol)
        if not base:
            return 0.0

        try:
            from mexc_monitor.config import DEFAULT_SETTINGS
            from mexc_monitor.trading.private_client import MexcPrivateClient

            with MexcPrivateClient(
                api_key=self._api_key,
                api_secret=self._api_secret,
                base_url=DEFAULT_SETTINGS.base_url,
                timeout_sec=DEFAULT_SETTINGS.timeout_sec,
            ) as client:
                payload = client.get_account()
        except Exception:
            logger.exception(
                "MexcSpotBalanceChecker: failed to read MEXC spot balance for %s",
                symbol,
            )
            # Fail closed: treat unreadable balance as zero rather than allowing
            # a reverse C&C entry that could short an asset we may not hold.
            return 0.0

        balances = payload.get("balances", []) if isinstance(payload, dict) else []
        for row in balances:
            if isinstance(row, dict) and row.get("asset") == base:
                try:
                    return float(row.get("free", 0) or 0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0
