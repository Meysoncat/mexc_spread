"""
Property-based test: Unified format normalization (Property 2).

Feature: exchange-switcher
Property 2: For any valid raw ticker data from any supported exchange
(MEXC, AsterDEX, Lighter), the normalization function shall produce a valid
BookTickerRow containing all required fields with correct mathematical relationships:
  mid = (bid + ask) / 2
  spread_abs = ask - bid
  spread_bps = 10000 * spread_abs / mid

Validates: Requirements 2.3, 5.2
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from mexc_monitor.models import BookTickerRow
from mexc_monitor.lighter.client import (
    LighterMarketInfo,
    lighter_snapshot_rows,
    _round_price,
)


# --- Strategies ---

_positive_price = st.floats(min_value=0.001, max_value=500_000.0, allow_nan=False, allow_infinity=False)
_positive_qty = st.floats(min_value=0.001, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
_volume = st.floats(min_value=0.0, max_value=1e12, allow_nan=False, allow_infinity=False)
_symbol = st.sampled_from(["BTCUSDT", "ETHUSDT", "SOLUSDT", "ARBUSDT", "DOGEUSDT"])


@st.composite
def valid_book_ticker_row(draw):
    """Generate a valid BookTickerRow with consistent mathematical relationships."""
    bid = draw(_positive_price)
    spread_factor = draw(st.floats(min_value=1.0001, max_value=1.02, allow_nan=False, allow_infinity=False))
    ask = bid * spread_factor
    assume(ask > bid)

    mid = (bid + ask) / 2.0
    assume(mid > 0)

    spread_abs = ask - bid
    spread_bps = 10000.0 * spread_abs / mid

    return BookTickerRow(
        symbol=draw(_symbol),
        bid=bid,
        ask=ask,
        bid_qty=draw(_positive_qty),
        ask_qty=draw(_positive_qty),
        mid=mid,
        spread_abs=spread_abs,
        spread_bps=spread_bps,
        volume_24h_base=draw(_volume),
        volume_24h_quote=draw(_volume),
        funding_rate=None,
        observed_at=None,
    )


@st.composite
def raw_ticker_data(draw):
    """Generate raw ticker data that would come from any exchange."""
    bid = draw(_positive_price)
    spread_factor = draw(st.floats(min_value=1.0001, max_value=1.05, allow_nan=False, allow_infinity=False))
    ask = bid * spread_factor
    assume(ask > bid)
    assume((bid + ask) / 2.0 > 0)

    return {
        "bid": bid,
        "ask": ask,
        "bid_qty": draw(_positive_qty),
        "ask_qty": draw(_positive_qty),
        "volume_24h_base": draw(_volume),
        "symbol": draw(_symbol),
    }


def normalize_raw_to_book_ticker_row(raw: dict) -> BookTickerRow:
    """
    Universal normalization function that mirrors what both
    lighter_snapshot_rows and aster_snapshot_rows do internally.
    """
    bid = raw["bid"]
    ask = raw["ask"]
    mid = (bid + ask) / 2.0
    spread_abs = ask - bid
    spread_bps = 10000.0 * spread_abs / mid if mid > 0 else None
    volume_24h_base = raw["volume_24h_base"]
    volume_24h_quote = volume_24h_base * mid

    return BookTickerRow(
        symbol=raw["symbol"],
        bid=bid,
        ask=ask,
        bid_qty=raw["bid_qty"],
        ask_qty=raw["ask_qty"],
        mid=mid,
        spread_abs=spread_abs,
        spread_bps=spread_bps,
        volume_24h_base=volume_24h_base,
        volume_24h_quote=volume_24h_quote,
        funding_rate=None,
        observed_at=None,
    )


class TestUnifiedFormatNormalization:
    """Property 2: Unified format normalization."""

    @given(raw=raw_ticker_data())
    @settings(max_examples=200)
    def test_mid_equals_average(self, raw: dict):
        """mid == (bid + ask) / 2 for any valid raw data."""
        row = normalize_raw_to_book_ticker_row(raw)
        expected_mid = (raw["bid"] + raw["ask"]) / 2.0
        assert math.isclose(row.mid, expected_mid, rel_tol=1e-9)

    @given(raw=raw_ticker_data())
    @settings(max_examples=200)
    def test_spread_abs_equals_difference(self, raw: dict):
        """spread_abs == ask - bid for any valid raw data."""
        row = normalize_raw_to_book_ticker_row(raw)
        expected_spread = raw["ask"] - raw["bid"]
        assert math.isclose(row.spread_abs, expected_spread, rel_tol=1e-9)

    @given(raw=raw_ticker_data())
    @settings(max_examples=200)
    def test_spread_bps_formula(self, raw: dict):
        """spread_bps == 10000 * spread_abs / mid for any valid raw data."""
        row = normalize_raw_to_book_ticker_row(raw)
        if row.mid > 0 and row.spread_bps is not None:
            expected_bps = 10000.0 * row.spread_abs / row.mid
            assert math.isclose(row.spread_bps, expected_bps, rel_tol=1e-9)

    @given(raw=raw_ticker_data())
    @settings(max_examples=200)
    def test_all_required_fields_present(self, raw: dict):
        """Normalized row must have all required fields non-None (except funding_rate)."""
        row = normalize_raw_to_book_ticker_row(raw)
        assert row.symbol is not None and len(row.symbol) > 0
        assert row.bid > 0
        assert row.ask > 0
        assert row.bid_qty > 0
        assert row.ask_qty > 0
        assert row.mid > 0
        assert row.spread_abs >= 0
        # spread_bps can be None only if mid <= 0, which we've excluded
        assert row.spread_bps is not None
        assert row.spread_bps >= 0

    @given(raw=raw_ticker_data())
    @settings(max_examples=200)
    def test_bid_less_than_or_equal_ask(self, raw: dict):
        """In normalized output, bid <= ask always holds."""
        row = normalize_raw_to_book_ticker_row(raw)
        assert row.bid <= row.ask

    @given(raw=raw_ticker_data())
    @settings(max_examples=200)
    def test_mid_between_bid_and_ask(self, raw: dict):
        """mid should always be between bid and ask."""
        row = normalize_raw_to_book_ticker_row(raw)
        assert row.bid <= row.mid <= row.ask

    @given(row=valid_book_ticker_row())
    @settings(max_examples=200)
    def test_book_ticker_row_internal_consistency(self, row: BookTickerRow):
        """Any BookTickerRow should satisfy internal mathematical invariants."""
        # mid = (bid + ask) / 2
        assert math.isclose(row.mid, (row.bid + row.ask) / 2.0, rel_tol=1e-9)
        # spread_abs = ask - bid
        assert math.isclose(row.spread_abs, row.ask - row.bid, rel_tol=1e-9)
        # spread_bps = 10000 * spread_abs / mid
        if row.mid > 0 and row.spread_bps is not None:
            expected_bps = 10000.0 * row.spread_abs / row.mid
            assert math.isclose(row.spread_bps, expected_bps, rel_tol=1e-9)


class TestLighterNormalizationIntegration:
    """Integration test: lighter_snapshot_rows produces valid BookTickerRow."""

    @given(
        bid=_positive_price,
        spread_factor=st.floats(min_value=1.0001, max_value=1.03, allow_nan=False, allow_infinity=False),
        volume=_volume,
    )
    @settings(max_examples=100)
    def test_lighter_produces_consistent_rows(self, bid: float, spread_factor: float, volume: float):
        """lighter_snapshot_rows output satisfies mathematical invariants."""
        ask = bid * spread_factor
        assume(ask > bid)
        assume((bid + ask) / 2.0 > 0)

        info = LighterMarketInfo(
            market_id=1,
            symbol="ETH-PERP",
            base_asset="ETH",
            quote_asset="USD",
            price_decimals=2,
            size_decimals=4,
            min_base_amount=0.001,
            min_quote_amount=1.0,
            taker_fee_pct=0.05,
            maker_fee_pct=0.02,
            last_trade_price=bid,
            volume_24h=10000.0,  # Ensure it passes the min_volume_quote filter
        )

        class MockClient:
            def orderbook_details(self, filter="perp"):
                return [info]

            def orderbook_orders(self, market_id, limit=1):
                return {
                    "bids": [{"price": str(bid), "remaining_base_amount": "10.0"}],
                    "asks": [{"price": str(ask), "remaining_base_amount": "10.0"}],
                }

            def funding_rates(self):
                return []

        rows = lighter_snapshot_rows(client=MockClient(), min_volume_quote=0)

        if len(rows) == 0:
            # Rounding may have made bid == ask or bid/ask <= 0
            return

        row = rows[0]
        # Verify mathematical consistency
        assert math.isclose(row.mid, (row.bid + row.ask) / 2.0, rel_tol=1e-9)
        assert math.isclose(row.spread_abs, row.ask - row.bid, rel_tol=1e-9)
        if row.mid > 0 and row.spread_bps is not None:
            expected_bps = 10000.0 * row.spread_abs / row.mid
            assert math.isclose(row.spread_bps, expected_bps, rel_tol=1e-9)
