"""Unit tests for the screener gate predicates, adaptive gates, scorer and
percentile helper (pure functions)."""

from __future__ import annotations

import math

import pytest

from mexc_monitor.screener.config import ScreenerConfig
from mexc_monitor.screener.filters import (
    compute_percentile_cutoff,
    passes_gates,
    score_candidate,
)
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
        spread_zscore=2.0,
    )
    base.update(overrides)
    return Candidate(**base)


def _cfg(**overrides) -> ScreenerConfig:
    return ScreenerConfig(**overrides)


# ── absolute gates (unchanged behaviour) ────────────────────────────────────


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


def test_low_volume_soft_mode_passes():
    # The whole point of soft mode: a low-24h-but-otherwise-valid coin survives.
    c = _candidate(volume_24h_quote=1_000.0)
    passed, reasons = passes_gates(
        c, _cfg(volume_gate_mode="soft", min_volume_24h_usdt=100_000.0)
    )
    assert passed
    assert not any("volume" in r for r in reasons)


def test_low_volume_hard_mode_explicit_fails():
    c = _candidate(volume_24h_quote=1_000.0)
    passed, reasons = passes_gates(
        c, _cfg(volume_gate_mode="hard", min_volume_24h_usdt=100_000.0)
    )
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


# ── adaptive percentile gate ────────────────────────────────────────────────


def test_adaptive_percentile_gate_passes_above_cutoff():
    c = _candidate(net_spread_bps=50.0)
    passed, _ = passes_gates(c, _cfg(adaptive_mode=True), {"percentile_cutoff": 30.0})
    assert passed


def test_adaptive_percentile_gate_fails_below_cutoff():
    c = _candidate(net_spread_bps=10.0)
    passed, reasons = passes_gates(
        c, _cfg(adaptive_mode=True), {"percentile_cutoff": 30.0}
    )
    assert not passed
    assert any("percentile cutoff" in r for r in reasons)


def test_adaptive_gate_skipped_when_ctx_missing():
    # adaptive_mode on but no ctx → percentile gate must NOT block.
    c = _candidate(net_spread_bps=3.0)
    passed, reasons = passes_gates(c, _cfg(adaptive_mode=True), None)
    assert passed
    assert not any("percentile" in r for r in reasons)


def test_adaptive_disabled_ignores_ctx():
    # adaptive_mode off → even a huge cutoff must not block.
    c = _candidate(net_spread_bps=3.0)
    passed, _ = passes_gates(c, _cfg(adaptive_mode=False), {"percentile_cutoff": 999.0})
    assert passed


# ── z-score gate ────────────────────────────────────────────────────────────


def test_zscore_gate_passes_when_high():
    c = _candidate(spread_zscore=2.5)
    passed, _ = passes_gates(c, _cfg(use_spread_zscore=True, min_spread_zscore=1.0))
    assert passed


def test_zscore_gate_fails_when_low():
    c = _candidate(spread_zscore=0.2)
    passed, reasons = passes_gates(c, _cfg(use_spread_zscore=True, min_spread_zscore=1.0))
    assert not passed
    assert any("z-score" in r for r in reasons)


def test_zscore_gate_fails_when_none():
    c = _candidate(spread_zscore=None)
    passed, reasons = passes_gates(c, _cfg(use_spread_zscore=True, min_spread_zscore=1.0))
    assert not passed
    assert any("z-score" in r for r in reasons)


def test_zscore_gate_disabled():
    c = _candidate(spread_zscore=None)
    passed, _ = passes_gates(c, _cfg(use_spread_zscore=False))
    assert passed


# ── compute_percentile_cutoff ───────────────────────────────────────────────


def test_percentile_empty_returns_zero():
    assert compute_percentile_cutoff([], 95.0) == 0.0


def test_percentile_single_returns_that_value():
    assert compute_percentile_cutoff([7.5], 95.0) == 7.5


def test_percentile_median_interpolation():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert compute_percentile_cutoff(vals, 50.0) == pytest.approx(5.5)


def test_percentile_95_high_end():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    # rank = 0.95 * 9 = 8.55 → between index 8 (9.0) and 9 (10.0) → 9.55
    assert compute_percentile_cutoff(vals, 95.0) == pytest.approx(9.55)


def test_percentile_filters_none_and_nan():
    assert compute_percentile_cutoff([1.0, None, float("nan"), 3.0], 50.0) == pytest.approx(2.0)


def test_percentile_clamps_out_of_range_pct():
    vals = [1.0, 2.0, 3.0]
    assert compute_percentile_cutoff(vals, 200.0) == 3.0  # max
    assert compute_percentile_cutoff(vals, -5.0) == 1.0  # min


# ── scorer ───────────────────────────────────────────────────────────────────


def test_score_breakdown_has_all_terms():
    score, breakdown = score_candidate(_candidate(), _cfg())
    assert set(breakdown.keys()) == {
        "spread",
        "liquidity",
        "lifetime",
        "stability",
        "volatility",
        "staleness",
        "zscore",
        "volume24h",
    }
    assert breakdown["volatility"] <= 0
    assert breakdown["staleness"] <= 0
    assert breakdown["volume24h"] >= 0


def test_higher_net_spread_scores_higher():
    low = _candidate(net_spread_bps=5.0)
    high = _candidate(net_spread_bps=40.0)
    assert score_candidate(high, _cfg())[0] > score_candidate(low, _cfg())[0]


def test_spread_reward_capped():
    cfg = _cfg(spread_cap_bps=20.0, w_spread=1.0)
    _, b1 = score_candidate(_candidate(net_spread_bps=20.0), cfg)
    _, b2 = score_candidate(_candidate(net_spread_bps=200.0), cfg)
    assert b1["spread"] == pytest.approx(b2["spread"])


def test_higher_lifetime_scores_higher():
    short = _candidate(lifetime_sec=8.0)
    long_ = _candidate(lifetime_sec=120.0)
    assert score_candidate(long_, _cfg())[0] > score_candidate(short, _cfg())[0]


def test_more_liquid_scores_higher():
    thin = _candidate(l1_notional=100.0)
    deep = _candidate(l1_notional=20_000.0)
    assert score_candidate(deep, _cfg())[0] > score_candidate(thin, _cfg())[0]


def test_volatility_penalizes():
    calm = _candidate(spread_std=1.0)
    jumpy = _candidate(spread_std=20.0)
    assert score_candidate(calm, _cfg())[0] > score_candidate(jumpy, _cfg())[0]


def test_staleness_penalizes():
    fresh = _candidate(tick_age_ms=500.0)
    stale = _candidate(tick_age_ms=9_000.0)
    assert score_candidate(fresh, _cfg())[0] > score_candidate(stale, _cfg())[0]


def test_higher_zscore_scores_higher():
    low = _candidate(spread_zscore=0.5)
    high = _candidate(spread_zscore=3.5)
    assert score_candidate(high, _cfg())[0] > score_candidate(low, _cfg())[0]


def test_higher_volume_scores_higher():
    thin = _candidate(volume_24h_quote=1_000.0)
    deep = _candidate(volume_24h_quote=5_000_000.0)
    assert score_candidate(deep, _cfg())[0] > score_candidate(thin, _cfg())[0]


def test_zscore_reward_capped():
    cfg = _cfg(w_zscore=1.0, zscore_cap=2.0)
    _, b1 = score_candidate(_candidate(spread_zscore=2.0), cfg)
    _, b2 = score_candidate(_candidate(spread_zscore=10.0), cfg)
    assert b1["zscore"] == pytest.approx(b2["zscore"])
