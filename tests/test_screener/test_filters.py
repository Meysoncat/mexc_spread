"""Unit tests for the screener gate predicates and scorer (pure functions)."""

from __future__ import annotations

import pytest

from mexc_monitor.screener.config import ScreenerConfig
from mexc_monitor.screener.filters import passes_gates, score_candidate
from mexc_monitor.screener.models import Candidate


def _candidate(**overrides) -> Candidate:
    base = dict(
        symbol="BTCUSDT",
        bid=100.0,
        ask=100.5,
        mid=100.25,
        spread_bps=50.0,
        net_spread_bps=50.0,
        l1_notional=5_000.0,
        volume_24h_quote=2_000_000.0,
        tick_age_ms=1_000.0,
        observed_at_iso="2026-01-01T00:00:00+00:00",
        lifetime_sec=30.0,
        pct_time_above=90.0,
        spread_std=2.0,
    )
    base.update(overrides)
    return Candidate(**base)


def _cfg(**overrides) -> ScreenerConfig:
    return ScreenerConfig(**overrides)


# ── passes_gates ────────────────────────────────────────────────────────────


def test_clean_candidate_passes():
    c = _candidate()
    passed, reasons = passes_gates(c, _cfg())
    assert passed
    assert reasons == []


def test_below_min_net_spread_fails():
    c = _candidate(net_spread_bps=1.0)
    passed, reasons = passes_gates(c, _cfg(min_net_spread_bps=3.0))
    assert not passed
    assert any("net_spread" in r for r in reasons)


def test_huge_spread_flagged_illiquid():
    c = _candidate(spread_bps=500.0)
    passed, reasons = passes_gates(c, _cfg(max_spread_bps=200.0))
    assert not passed
    assert any("illiquid" in r for r in reasons)


def test_low_l1_liquidity_fails():
    c = _candidate(l1_notional=10.0)
    passed, reasons = passes_gates(c, _cfg(min_l1_notional_usdt=100.0))
    assert not passed
    assert any("l1_notional" in r for r in reasons)


def test_low_volume_fails():
    c = _candidate(volume_24h_quote=1_000.0)
    passed, reasons = passes_gates(c, _cfg(min_volume_24h_usdt=100_000.0))
    assert not passed
    assert any("volume" in r for r in reasons)


def test_short_lifetime_fails():
    c = _candidate(lifetime_sec=2.0)
    passed, reasons = passes_gates(c, _cfg(min_lifetime_sec=8.0))
    assert not passed
    assert any("lifetime" in r for r in reasons)


def test_blacklisted_symbol_fails():
    c = _candidate(symbol="USDCUSDT")
    passed, reasons = passes_gates(c, _cfg())
    assert not passed
    assert any("blacklisted" in r for r in reasons)


def test_stale_tick_fails():
    c = _candidate(tick_age_ms=20_000.0)
    passed, reasons = passes_gates(c, _cfg(max_tick_age_ms=10_000.0))
    assert not passed
    assert any("stale" in r for r in reasons)


def test_missing_spread_data_fails():
    c = _candidate(spread_bps=None, net_spread_bps=None)
    passed, reasons = passes_gates(c, _cfg())
    assert not passed
    assert reasons == ["no spread data"]


def test_multiple_failures_all_reported():
    c = _candidate(net_spread_bps=1.0, l1_notional=1.0, lifetime_sec=1.0)
    passed, reasons = passes_gates(c, _cfg())
    assert not passed
    assert len(reasons) == 3


# ── score_candidate ─────────────────────────────────────────────────────────


def test_score_breakdown_has_all_terms():
    score, breakdown = score_candidate(_candidate(), _cfg())
    assert set(breakdown.keys()) == {
        "spread",
        "liquidity",
        "lifetime",
        "stability",
        "volatility",
        "staleness",
    }
    # Volatility and staleness are penalties (non-positive for non-negative inputs).
    assert breakdown["volatility"] <= 0
    assert breakdown["staleness"] <= 0


def test_higher_net_spread_scores_higher():
    low = _candidate(net_spread_bps=5.0)
    high = _candidate(net_spread_bps=40.0)
    s_low, _ = score_candidate(low, _cfg())
    s_high, _ = score_candidate(high, _cfg())
    assert s_high > s_low


def test_spread_reward_capped():
    cfg = _cfg(spread_cap_bps=20.0, w_spread=1.0)
    # Beyond the cap, the spread term must not grow.
    _, b1 = score_candidate(_candidate(net_spread_bps=20.0), cfg)
    _, b2 = score_candidate(_candidate(net_spread_bps=200.0), cfg)
    assert b1["spread"] == pytest.approx(b2["spread"])


def test_higher_lifetime_scores_higher():
    short = _candidate(lifetime_sec=8.0)
    long_ = _candidate(lifetime_sec=120.0)
    s_short, _ = score_candidate(short, _cfg())
    s_long, _ = score_candidate(long_, _cfg())
    assert s_long > s_short


def test_more_liquid_scores_higher():
    thin = _candidate(l1_notional=100.0)
    deep = _candidate(l1_notional=20_000.0)
    s_thin, _ = score_candidate(thin, _cfg())
    s_deep, _ = score_candidate(deep, _cfg())
    assert s_deep > s_thin


def test_volatility_penalizes():
    calm = _candidate(spread_std=1.0)
    jumpy = _candidate(spread_std=20.0)
    s_calm, _ = score_candidate(calm, _cfg())
    s_jumpy, _ = score_candidate(jumpy, _cfg())
    assert s_calm > s_jumpy


def test_staleness_penalizes():
    fresh = _candidate(tick_age_ms=500.0)
    stale = _candidate(tick_age_ms=9_000.0)
    s_fresh, _ = score_candidate(fresh, _cfg())
    s_stale, _ = score_candidate(stale, _cfg())
    assert s_fresh > s_stale
