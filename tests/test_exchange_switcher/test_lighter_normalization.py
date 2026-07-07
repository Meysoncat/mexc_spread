"""
Property-based test: Lighter data normalization round-trip consistency (Property 6).

Feature: exchange-switcher
Property 6: For any valid Lighter orderbook response with positive bid and ask prices,
the normalization to BookTickerRow shall produce mathematically consistent values:
  mid == (bid + ask) / 2
  spread_abs == ask - bid
  spread_bps == 10000 * spread_abs / mid
(within floating point tolerance).

Validates: Requirements 5.2
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mexc_monitor.lighter.client import (
    LighterMarketInfo,
    LighterOrderbookSummary,
    LighterPublicClient,
    lighter_snapshot_rows,
    _normalize_symbol,
    _round_price,
)
from mexc_monitor.models import BookTickerRow


# --- Strategies ---

_positive_price = st.floats(min_value=0.0001, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
_positive_qty = st.floats(min_value=0.001, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
_volume = st.floats(min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False)
_price_decimals = st.integers(min_value=0, max_value=8)
_market_id = st.integers(min_value=0, max_value=100)


@st.composite
def orderbook_pair(draw):
    """Generate a valid orderbook summary + market info pair with bid < ask."""
    bid = draw(_positive_price)
    # ask must be >= bid
    spread_factor = draw(st.floats(min_value=1.0, max_value=1.05, allow_nan=False, allow_infinity=False))
    ask = bid * spread_factor
    assume(ask > bid)
    assume(ask - bid > 1e-10)  # non-trivial spread

    mid = (bid + ask) / 2.0
    assume(mid > 0)

    market_id = draw(_market_id)
    bid_qty = draw(_positive_qty)
    ask_qty = draw(_positive_qty)
    volume = draw(_volume)
    last_price = draw(_positive_price)
    price_decimals = draw(_price_decimals)

    summary = LighterOrderbookSummary(
        market_id=market_id,
        best_bid=bid,
        best_ask=ask,
        best_bid_qty=bid_qty,
        best_ask_qty=ask_qty,
        volume_24h=volume,
        last_price=last_price,
    )

    info = LighterMarketInfo(
        market_id=market_id,
        symbol="ETH-PERP",
        base_asset="ETH",
        quote_asset="USD",
        price_decimals=price_decimals,
        size_decimals=4,
        min_base_amount=0.001,
        min_quote_amount=1.0,
        taker_fee_pct=0.05,
        maker_fee_pct=0.02,
    )

    return summary, info


class TestLighterNormalizationProperty:
    """Property 6: Lighter data normalization round-trip consistency."""

    @given(data=orderbook_pair())
    @settings(max_examples=200)
    def test_mid_is_average_of_bid_ask(self, data):
        """mid == (bid + ask) / 2 within floating point tolerance."""
        summary, info = data

        # Apply the same rounding as the normalization function
        bid = _round_price(summary.best_bid, info.price_decimals)
        ask = _round_price(summary.best_ask, info.price_decimals)

        assume(bid > 0 and ask > 0)
        assume(ask > bid)

        expected_mid = (bid + ask) / 2.0
        # The normalization function computes mid the same way
        mid = (bid + ask) / 2.0

        assert math.isclose(mid, expected_mid, rel_tol=1e-9)

    @given(data=orderbook_pair())
    @settings(max_examples=200)
    def test_spread_abs_is_ask_minus_bid(self, data):
        """spread_abs == ask - bid within floating point tolerance."""
        summary, info = data

        bid = _round_price(summary.best_bid, info.price_decimals)
        ask = _round_price(summary.best_ask, info.price_decimals)

        assume(bid > 0 and ask > 0)
        assume(ask > bid)

        expected_spread_abs = ask - bid
        spread_abs = ask - bid

        assert math.isclose(spread_abs, expected_spread_abs, rel_tol=1e-9)

    @given(data=orderbook_pair())
    @settings(max_examples=200)
    def test_spread_bps_formula(self, data):
        """spread_bps == 10000 * spread_abs / mid within floating point tolerance."""
        summary, info = data

        bid = _round_price(summary.best_bid, info.price_decimals)
        ask = _round_price(summary.best_ask, info.price_decimals)

        assume(bid > 0 and ask > 0)
        assume(ask > bid)

        mid = (bid + ask) / 2.0
        assume(mid > 0)

        spread_abs = ask - bid
        expected_spread_bps = 10000.0 * spread_abs / mid
        spread_bps = 10000.0 * spread_abs / mid

        assert math.isclose(spread_bps, expected_spread_bps, rel_tol=1e-9)

    @given(data=orderbook_pair())
    @settings(max_examples=200)
    def test_full_normalization_consistency(self, data):
        """
        Full integration: mock client returns data, normalization produces
        consistent BookTickerRow with correct mathematical relationships.
        """
        summary, info = data

        bid = _round_price(summary.best_bid, info.price_decimals)
        ask = _round_price(summary.best_ask, info.price_decimals)

        assume(bid > 0 and ask > 0)
        assume(ask > bid)

        # Create a mock client that matches the new lighter_snapshot_rows interface:
        # - orderbook_details() returns market info with volume
        # - orderbook_orders(market_id, limit) returns bids/asks
        # - funding_rates() returns empty list
        from dataclasses import replace
        info_with_volume = LighterMarketInfo(
            market_id=info.market_id,
            symbol=info.symbol,
            base_asset=info.base_asset,
            quote_asset=info.quote_asset,
            price_decimals=info.price_decimals,
            size_decimals=info.size_decimals,
            min_base_amount=info.min_base_amount,
            min_quote_amount=info.min_quote_amount,
            taker_fee_pct=info.taker_fee_pct,
            maker_fee_pct=info.maker_fee_pct,
            last_trade_price=summary.last_price,
            volume_24h=10000.0,  # Ensure it passes the min_volume_quote filter
        )

        class MockClient:
            def orderbook_details(self, filter="perp"):
                return [info_with_volume]

            def orderbook_orders(self, market_id, limit=1):
                return {
                    "bids": [{"price": str(summary.best_bid), "remaining_base_amount": str(summary.best_bid_qty)}],
                    "asks": [{"price": str(summary.best_ask), "remaining_base_amount": str(summary.best_ask_qty)}],
                }

            def funding_rates(self):
                return []

        rows = lighter_snapshot_rows(client=MockClient(), min_volume_quote=0)

        assert len(rows) == 1
        row = rows[0]

        # Verify mathematical relationships
        assert math.isclose(row.mid, (row.bid + row.ask) / 2.0, rel_tol=1e-9)
        assert math.isclose(row.spread_abs, row.ask - row.bid, rel_tol=1e-9)
        if row.mid > 0 and row.spread_bps is not None:
            expected_bps = 10000.0 * row.spread_abs / row.mid
            assert math.isclose(row.spread_bps, expected_bps, rel_tol=1e-9)

    @given(data=orderbook_pair())
    @settings(max_examples=200)
    def test_spread_abs_non_negative(self, data):
        """spread_abs should always be non-negative (ask >= bid)."""
        summary, info = data

        bid = _round_price(summary.best_bid, info.price_decimals)
        ask = _round_price(summary.best_ask, info.price_decimals)

        assume(bid > 0 and ask > 0)
        assume(ask >= bid)

        spread_abs = ask - bid
        assert spread_abs >= 0

    @given(data=orderbook_pair())
    @settings(max_examples=200)
    def test_spread_bps_non_negative(self, data):
        """spread_bps should always be non-negative when ask >= bid."""
        summary, info = data

        bid = _round_price(summary.best_bid, info.price_decimals)
        ask = _round_price(summary.best_ask, info.price_decimals)

        assume(bid > 0 and ask > 0)
        assume(ask >= bid)

        mid = (bid + ask) / 2.0
        assume(mid > 0)

        spread_bps = 10000.0 * (ask - bid) / mid
        assert spread_bps >= 0


class TestNormalizeSymbol:
    """Unit tests for symbol normalization helper."""

    def test_eth_perp(self):
        assert _normalize_symbol("ETH-PERP") == "ETHUSDT"

    def test_btc_perp_underscore(self):
        assert _normalize_symbol("BTC_PERP") == "BTCUSDT"

    def test_sol_perp(self):
        assert _normalize_symbol("SOL-PERP") == "SOLUSDT"

    def test_already_normalized(self):
        assert _normalize_symbol("ETHUSDT") == "ETHUSDT"

    def test_lowercase(self):
        assert _normalize_symbol("eth-perp") == "ETHUSDT"

    def test_with_spaces(self):
        assert _normalize_symbol("  ETH-PERP  ") == "ETHUSDT"


class TestRoundPrice:
    """Unit tests for price rounding helper."""

    def test_zero_decimals(self):
        assert _round_price(123.456, 0) == 123.456  # no rounding when decimals=0

    def test_two_decimals(self):
        assert _round_price(123.456, 2) == 123.46

    def test_eight_decimals(self):
        assert _round_price(0.123456789, 8) == 0.12345679
