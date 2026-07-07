"""Tests for spread typology metrics and funding model improvements."""

from __future__ import annotations

import pytest

from mexc_monitor.metrics import (
    adverse_selection_bps,
    compute_mid_spread,
    effective_spread_bps,
    realized_spread_bps,
)
from mexc_monitor.futures_arb.models import FundingInfo


# ─── Effective / Realized / Adverse selection spread ─────────────────────────


class TestEffectiveSpread:
    def test_buy_at_ask(self):
        """Market buy at ask → effective = quoted spread."""
        mid = 100.0
        trade_price = 101.0
        assert effective_spread_bps(trade_price, mid) == pytest.approx(200.0)

    def test_sell_at_bid(self):
        """Market sell at bid → effective = quoted spread."""
        mid = 100.0
        trade_price = 99.0
        assert effective_spread_bps(trade_price, mid) == pytest.approx(200.0)

    def test_zero_mid_returns_none(self):
        assert effective_spread_bps(100, 0) is None

    def test_with_slippage(self):
        """Buy above ask → effective > quoted spread."""
        mid = 100.0
        trade_price = 101.5  # 50 bps slippage above ask
        assert effective_spread_bps(trade_price, mid) == pytest.approx(300.0)


class TestRealizedSpread:
    def test_price_stable(self):
        """If mid doesn't move, realized = effective."""
        assert realized_spread_bps(101, 100, 100) == pytest.approx(200.0)

    def test_price_up_after_buy(self):
        """Bought at 101, mid moved to 101 → realized = 0 (MM made nothing)."""
        assert realized_spread_bps(101, 101, 100) == pytest.approx(0.0)

    def test_price_down_after_buy(self):
        """Bought at 101, mid moved to 99 → realized = 400 (MM profited)."""
        assert realized_spread_bps(101, 99, 100) == pytest.approx(400.0)

    def test_zero_mid_returns_none(self):
        assert realized_spread_bps(101, 100, 0) is None


class TestAdverseSelection:
    def test_no_movement(self):
        """Mid unchanged → no adverse selection."""
        assert adverse_selection_bps(101, 100, 100) == pytest.approx(0.0)

    def test_price_up_after_buy(self):
        """Bought at 101 (above mid), mid then rose to 101.
        Adverse selection = 2 × (101 - 100) / 100 × 10000 = 200 bps.
        This means the fill was favorable (price moved toward us)."""
        result = adverse_selection_bps(101, 100, 101)
        assert result == pytest.approx(200.0)

    def test_price_down_after_buy(self):
        """Bought at 101 (above mid), mid then fell to 99.
        Adverse selection = 2 × (99 - 100) / 100 × 10000 = -200 bps.
        Negative = adverse (price moved against us)."""
        result = adverse_selection_bps(101, 100, 99)
        assert result == pytest.approx(-200.0)


class TestComputeMidSpreadDocstring:
    """Verify the docstring fix (P3.2): full spread, not half-spread."""

    def test_docstring_says_full(self):
        import inspect
        from mexc_monitor.metrics import compute_mid_spread
        doc = compute_mid_spread.__doc__ or ""
        assert "full" in doc.lower()
        assert "NOT half" in doc or "not half" in doc.lower()


# ─── Funding model: z-score and std ──────────────────────────────────────────


class TestFundingInfo:
    def test_new_fields_exist(self):
        fi = FundingInfo(
            symbol="BTC", exchange="mexc_futures",
            current_rate=0.001, next_funding_time_ms=0,
            avg_7d=0.0008, avg_30d=0.0005,
            annualized_yield=10.95, direction_changed=False,
            std_30d=0.0003, z_score=1.67,
        )
        assert fi.std_30d == pytest.approx(0.0003)
        assert fi.z_score == pytest.approx(1.67)

    def test_default_values(self):
        fi = FundingInfo(
            symbol="BTC", exchange="mexc_futures",
            current_rate=0.001, next_funding_time_ms=0,
            avg_7d=0.0008, avg_30d=0.0005,
            annualized_yield=10.95, direction_changed=False,
        )
        assert fi.std_30d == 0.0
        assert fi.z_score == 0.0


# ─── Funding tracker std computation ─────────────────────────────────────────


class TestFundingStdComputation:
    def test_compute_std(self):
        from mexc_monitor.futures_arb.funding_tracker import FundingTracker
        from mexc_monitor.futures_arb.models import FuturesArbSettings
        from mexc_monitor.futures_arb.funding_tracker import FundingRateEntry
        from collections import deque

        tracker = FundingTracker(FuturesArbSettings())
        now_ms = 1000000000000
        history = deque([
            FundingRateEntry(symbol="BTC", exchange="mexc_futures", rate=0.001, timestamp_ms=now_ms - 86400000, next_funding_time_ms=0),
            FundingRateEntry(symbol="BTC", exchange="mexc_futures", rate=0.002, timestamp_ms=now_ms - 172800000, next_funding_time_ms=0),
            FundingRateEntry(symbol="BTC", exchange="mexc_futures", rate=0.000, timestamp_ms=now_ms - 259200000, next_funding_time_ms=0),
        ])
        std = tracker._compute_std(history, now_ms, days=30)
        # mean = 0.001, variance = ((0.001-0.001)^2 + (0.002-0.001)^2 + (0-0.001)^2) / 3
        # = (0 + 0.000001 + 0.000001) / 3 = 0.000000667
        # std = 0.000816
        assert std == pytest.approx(0.0008165, rel=0.01)

    def test_std_empty_history(self):
        from mexc_monitor.futures_arb.funding_tracker import FundingTracker
        from mexc_monitor.futures_arb.models import FuturesArbSettings
        from collections import deque

        tracker = FundingTracker(FuturesArbSettings())
        std = tracker._compute_std(deque([]), 1000000000000, days=30)
        assert std == 0.0

    def test_std_single_entry(self):
        from mexc_monitor.futures_arb.funding_tracker import FundingTracker
        from mexc_monitor.futures_arb.models import FuturesArbSettings
        from mexc_monitor.futures_arb.funding_tracker import FundingRateEntry
        from collections import deque

        tracker = FundingTracker(FuturesArbSettings())
        history = deque([
            FundingRateEntry(symbol="BTC", exchange="mexc_futures", rate=0.001, timestamp_ms=0, next_funding_time_ms=0),
        ])
        std = tracker._compute_std(history, 1000000000000, days=30)
        assert std == 0.0  # Need at least 2 entries
