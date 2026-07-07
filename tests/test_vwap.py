"""Tests for the VWAP execution model (mexc_monitor.vwap)."""

from __future__ import annotations

import pytest

from mexc_monitor.vwap import (
    VwapResult,
    compute_depth_summary,
    compute_executable_notional,
    compute_vwap,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

BIDS = [
    {"price": 99.0, "qty": 2.0},
    {"price": 98.0, "qty": 3.0},
    {"price": 97.0, "qty": 5.0},
]

ASKS = [
    {"price": 101.0, "qty": 1.0},
    {"price": 102.0, "qty": 2.0},
    {"price": 103.0, "qty": 7.0},
]

MID = 100.0  # (99 + 101) / 2


# ─── compute_vwap: buy side ───────────────────────────────────────────────────


class TestVwapBuy:
    def test_single_level_fill(self):
        """Order fully filled at best ask — VWAP = best price, zero slippage."""
        r = compute_vwap(BIDS, ASKS, "buy", 0.5, mid=MID)
        assert r is not None
        assert r.vwap_price == pytest.approx(101.0)
        assert r.filled_qty == pytest.approx(0.5)
        assert r.unfilled_qty == pytest.approx(0.0)
        assert r.fully_filled is True
        assert r.slippage_bps == pytest.approx(0.0, abs=1e-9)
        assert r.levels_consumed == 1

    def test_multi_level_fill(self):
        """Order eats level 1 (1 unit @ 101) + part of level 2 (1 unit @ 102).

        VWAP = (101*1 + 102*1) / 2 = 101.5
        Slippage = (101.5 - 101.0) / 100 * 10000 = 50 bps
        """
        r = compute_vwap(BIDS, ASKS, "buy", 2.0, mid=MID)
        assert r is not None
        assert r.vwap_price == pytest.approx(101.5)
        assert r.filled_qty == pytest.approx(2.0)
        assert r.fully_filled is True
        assert r.slippage_bps == pytest.approx(50.0, abs=1e-6)
        assert r.levels_consumed == 2

    def test_partial_fill_insufficient_depth(self):
        """Order larger than total ask depth — partial fill, unfilled > 0."""
        r = compute_vwap(BIDS, ASKS, "buy", 100.0, mid=MID)
        assert r is not None
        assert r.fully_filled is False
        assert r.filled_qty == pytest.approx(10.0)  # 1 + 2 + 7
        assert r.unfilled_qty == pytest.approx(90.0)
        # VWAP = (101*1 + 102*2 + 103*7) / 10 = (101 + 204 + 721) / 10 = 102.6
        assert r.vwap_price == pytest.approx(102.6)
        assert r.levels_consumed == 3

    def test_zero_qty_returns_none(self):
        assert compute_vwap(BIDS, ASKS, "buy", 0.0) is None

    def test_empty_asks_returns_none(self):
        assert compute_vwap(BIDS, [], "buy", 1.0) is None
        assert compute_vwap(BIDS, None, "buy", 1.0) is None


# ─── compute_vwap: sell side ──────────────────────────────────────────────────


class TestVwapSell:
    def test_single_level_fill(self):
        """Sell fully filled at best bid — VWAP = best bid, zero slippage."""
        r = compute_vwap(BIDS, ASKS, "sell", 1.0, mid=MID)
        assert r is not None
        assert r.vwap_price == pytest.approx(99.0)
        assert r.filled_qty == pytest.approx(1.0)
        assert r.fully_filled is True
        assert r.slippage_bps == pytest.approx(0.0, abs=1e-9)

    def test_multi_level_fill(self):
        """Sell 4 units: 2 @ 99 + 2 @ 98.

        VWAP = (99*2 + 98*2) / 4 = 98.5
        Slippage = (99.0 - 98.5) / 100 * 10000 = 50 bps
        """
        r = compute_vwap(BIDS, ASKS, "sell", 4.0, mid=MID)
        assert r is not None
        assert r.vwap_price == pytest.approx(98.5)
        assert r.fully_filled is True
        assert r.slippage_bps == pytest.approx(50.0, abs=1e-6)
        assert r.levels_consumed == 2


# ─── compute_executable_notional ──────────────────────────────────────────────


class TestExecutableNotional:
    def test_buy_side(self):
        # 101*1 + 102*2 + 103*7 = 101 + 204 + 721 = 1026
        n = compute_executable_notional(BIDS, ASKS, "buy")
        assert n == pytest.approx(1026.0)

    def test_sell_side(self):
        # 99*2 + 98*3 + 97*5 = 198 + 294 + 485 = 977
        n = compute_executable_notional(BIDS, ASKS, "sell")
        assert n == pytest.approx(977.0)

    def test_max_levels_limit(self):
        n_all = compute_executable_notional(BIDS, ASKS, "buy")
        n_1 = compute_executable_notional(BIDS, ASKS, "buy", max_levels=1)
        assert n_1 == pytest.approx(101.0)  # only first level
        assert n_all > n_1


# ─── compute_depth_summary ────────────────────────────────────────────────────


class TestDepthSummary:
    def test_basic_summary(self):
        s = compute_depth_summary(BIDS, ASKS, reference_notional=0.0)
        assert s["depth_levels"] == 3
        assert s["executable_buy_notional"] == pytest.approx(1026.0)
        assert s["executable_sell_notional"] == pytest.approx(977.0)
        # No reference notional → no VWAP computed
        assert s["vwap_buy_price"] is None
        assert s["vwap_sell_price"] is None

    def test_with_reference_notional(self):
        """Reference notional = 200 USDT → order_qty = 200/100 = 2 BTC.

        Buy VWAP: 1 @ 101 + 1 @ 102 = 101.5
        Sell VWAP: 2 @ 99 = 99.0 (fits in level 1)
        """
        s = compute_depth_summary(BIDS, ASKS, reference_notional=200.0)
        assert s["vwap_buy_price"] == pytest.approx(101.5)
        assert s["vwap_sell_price"] == pytest.approx(99.0)
        assert s["slippage_buy_bps"] == pytest.approx(50.0, abs=1e-6)
        assert s["slippage_sell_bps"] == pytest.approx(0.0, abs=1e-9)

    def test_empty_depth(self):
        s = compute_depth_summary(None, None)
        assert s["depth_levels"] == 0
        assert s["executable_buy_notional"] == 0.0
        assert s["vwap_buy_price"] is None

    def test_invalid_levels_filtered(self):
        """Levels with zero/negative price or qty are skipped."""
        bad_bids = [{"price": -1, "qty": 5}, {"price": 99, "qty": 0}, {"price": 99, "qty": 2}]
        bad_asks = [{"price": 101, "qty": 2}]
        s = compute_depth_summary(bad_bids, bad_asks)
        assert s["depth_levels"] == 1  # only the valid bid + valid ask


# ─── Integration: enrich_row_vwap ─────────────────────────────────────────────


class TestEnrichRowVwap:
    def test_enrichment_fills_vwap_fields(self):
        from mexc_monitor.execution import enrich_row_vwap
        from mexc_monitor.models import BookTickerRow
        from mexc_monitor.config import DEFAULT_SETTINGS

        row = BookTickerRow(
            symbol="BTCUSDT",
            bid=99.0, ask=101.0, bid_qty=2.0, ask_qty=1.0,
            mid=100.0, spread_abs=2.0, spread_bps=200.0,
            volume_24h_base=1000.0, volume_24h_quote=100000.0,
            funding_rate=None,
        )
        enriched = enrich_row_vwap(row, BIDS, ASKS, DEFAULT_SETTINGS)
        assert enriched.depth_levels == 3
        assert enriched.executable_buy_notional > 0
        assert enriched.executable_sell_notional > 0

    def test_enrichment_no_depth_returns_unchanged(self):
        from mexc_monitor.execution import enrich_row_vwap
        from mexc_monitor.models import BookTickerRow
        from mexc_monitor.config import DEFAULT_SETTINGS

        row = BookTickerRow(
            symbol="BTCUSDT",
            bid=99.0, ask=101.0, bid_qty=2.0, ask_qty=1.0,
            mid=100.0, spread_abs=2.0, spread_bps=200.0,
            volume_24h_base=1000.0, volume_24h_quote=100000.0,
        )
        enriched = enrich_row_vwap(row, None, None, DEFAULT_SETTINGS)
        assert enriched.depth_levels == 0
        assert enriched.vwap_buy_price is None
