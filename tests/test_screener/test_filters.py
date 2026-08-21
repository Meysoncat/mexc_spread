"""Unit tests for the screener gate predicates, adaptive gates, scorer and
percentile helper (pure functions)."""

from __future__ import annotations


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
        "ev",
        "spread",
        "liquidity",
        "lifetime",
        "stability",
        "volatility",
        "staleness",
        "zscore",
        "volume24h",
        "flow",
        "trade_volume",
        "activity_factor",
    }
    assert breakdown["volatility"] <= 0
    assert breakdown["staleness"] <= 0
    assert breakdown["volume24h"] >= 0
    assert breakdown["ev"] >= 0


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
    # w_stale defaults to 0 (tick_age is snapshot-global, never discriminates);
    # opt in explicitly to check the term still works when enabled.
    fresh = _candidate(tick_age_ms=500.0)
    stale = _candidate(tick_age_ms=9_000.0)
    cfg = _cfg(w_stale=0.05)
    assert score_candidate(fresh, cfg)[0] > score_candidate(stale, cfg)[0]


def test_staleness_default_weight_discriminates():
    # Default w_stale=0.05 again: tick_age_ms is per-symbol now, so fresh
    # ticks must rank above stale ones out of the box.
    fresh = _candidate(tick_age_ms=500.0)
    stale = _candidate(tick_age_ms=9_000.0)
    assert score_candidate(fresh, _cfg())[0] > score_candidate(stale, _cfg())[0]


# ── order-flow / traded-volume terms ────────────────────────────────────────


def test_buy_pressure_scores_higher():
    buy = _candidate(buy_sell_ratio=1.8)
    sell = _candidate(buy_sell_ratio=0.4)
    neutral = _candidate(buy_sell_ratio=None)
    cfg = _cfg()
    assert score_candidate(buy, cfg)[0] > score_candidate(neutral, cfg)[0]
    assert score_candidate(sell, cfg)[0] < score_candidate(neutral, cfg)[0]


def test_flow_term_clipped_at_plus_minus_one():
    extreme = _candidate(buy_sell_ratio=100.0)
    cfg = _cfg(w_flow=0.2)
    _, breakdown = score_candidate(extreme, cfg)
    assert breakdown["flow"] == pytest.approx(0.2)


def test_trade_volume_scores_higher():
    busy = _candidate(trade_volume_quote_60s=50_000.0)
    quiet = _candidate(trade_volume_quote_60s=10.0)
    none = _candidate(trade_volume_quote_60s=None)
    cfg = _cfg()
    assert score_candidate(busy, cfg)[0] > score_candidate(quiet, cfg)[0]
    assert score_candidate(none, cfg)[1]["trade_volume"] == 0.0


def test_higher_zscore_scores_higher():
    low = _candidate(spread_zscore=0.5)
    high = _candidate(spread_zscore=3.5)
    assert score_candidate(high, _cfg())[0] > score_candidate(low, _cfg())[0]


def test_higher_volume_scores_higher():
    thin = _candidate(volume_24h_quote=1_000.0)
    deep = _candidate(volume_24h_quote=5_000_000.0)
    assert score_candidate(deep, _cfg())[0] > score_candidate(thin, _cfg())[0]


# ── EV (realizable edge = net_spread × activity_factor) ─────────────────────


def test_active_wide_spread_beats_dead_wide_spread():
    # Same wide spread, but one has real book activity, the other is dead.
    active = _candidate(net_spread_bps=80.0, book_update_rate_per_min=120.0)
    dead = _candidate(net_spread_bps=80.0, book_update_rate_per_min=2.0)
    cfg = _cfg(w_ev=1.0, min_book_update_rate_per_min=60.0)
    assert score_candidate(active, cfg)[0] > score_candidate(dead, cfg)[0]


def test_trades_activity_preferred_over_book_rate():
    # Real trades density should drive EV (preferred source), and a coin with
    # trades should beat one relying on bookTicker-only at the same spread.
    with_trades = _candidate(net_spread_bps=80.0, trades_per_min=6.0)
    book_only = _candidate(net_spread_bps=80.0, book_update_rate_per_min=60.0)
    cfg = _cfg(w_ev=1.0, min_trades_per_min=3.0, min_book_update_rate_per_min=60.0)
    assert score_candidate(with_trades, cfg)[0] >= score_candidate(book_only, cfg)[0]
    # activity_factor saturates at 1.0 for both (trades 6/3=2→1.0, book 60/60=1.0)
    assert score_candidate(with_trades, cfg)[1]["activity_factor"] == 1.0


def test_dead_trades_sink_ev():
    # Wide spread but (nearly) no trades → low EV even though spread is big.
    hot = _candidate(net_spread_bps=80.0, trades_per_min=10.0)
    dead = _candidate(net_spread_bps=80.0, trades_per_min=0.1)
    cfg = _cfg(w_ev=1.0, min_trades_per_min=3.0)
    assert score_candidate(hot, cfg)[0] > score_candidate(dead, cfg)[0]


def test_unknown_activity_uses_neutral_factor():
    # Unconfirmed coin (no book rate) gets the neutral factor, so it still
    # scores by spread — can surface and get promoted to the WS.
    c = _candidate(net_spread_bps=80.0)  # book_update_rate_per_min is None
    _, b = score_candidate(c, _cfg(activity_unknown_factor=0.5, min_book_update_rate_per_min=60.0))
    # ev = w_ev(1.0) * min(80, 50) * 0.5 = 25.0
    assert b["ev"] == pytest.approx(25.0)


def test_activity_factor_caps_at_one():
    # A very fast book (10× the floor) should cap activity at 1.0, not 10.0.
    c = _candidate(net_spread_bps=40.0, book_update_rate_per_min=600.0)
    _, b = score_candidate(c, _cfg(min_book_update_rate_per_min=60.0))
    # ev = 1.0 * min(40,50) * 1.0 = 40.0
    assert b["ev"] == pytest.approx(40.0)


def test_ev_zero_when_net_spread_zero():
    c = _candidate(net_spread_bps=0.0, book_update_rate_per_min=600.0)
    _, b = score_candidate(c, _cfg())
    assert b["ev"] == 0.0


def test_zscore_reward_capped():
    cfg = _cfg(w_zscore=1.0, zscore_cap=2.0)
    _, b1 = score_candidate(_candidate(spread_zscore=2.0), cfg)
    _, b2 = score_candidate(_candidate(spread_zscore=10.0), cfg)
    assert b1["zscore"] == pytest.approx(b2["zscore"])


# ── leveraged-token filter ──────────────────────────────────────────────────


def test_leveraged_tokens_rejected():
    for sym in ("BTC3LUSDT", "ETH3SUSDT", "SOL5LUSDT", "ADA5SUSDT", "XRPBULLUSDT", "DOGEBEARUSDT"):
        passed, reasons = passes_gates(_candidate(symbol=sym), _cfg())
        assert not passed, sym
        assert any("leveraged" in r for r in reasons), sym


def test_leveraged_filter_can_be_disabled():
    passed, _ = passes_gates(
        _candidate(symbol="BTC3LUSDT"), _cfg(leveraged_tokens_filter=False)
    )
    assert passed


def test_normal_symbol_not_flagged_as_leveraged():
    assert passes_gates(_candidate(symbol="BULLPUMPUSDT"), _cfg())[0]


# ── shortlist hysteresis ────────────────────────────────────────────────────


def test_hysteresis_relaxes_net_floor_for_incumbent():
    # floor 3.0, net 2.5: rejected normally, passes with 1.0 bps hysteresis.
    c = _candidate(net_spread_bps=2.5, spread_bps=2.5, spread_zscore=5.0)
    assert not passes_gates(c, _cfg(min_net_spread_bps=3.0, adaptive_mode=False))[0]
    assert passes_gates(
        c, _cfg(min_net_spread_bps=3.0, adaptive_mode=False), hysteresis_bps=1.0
    )[0]


def test_hysteresis_relaxes_percentile_cutoff():
    c = _candidate(net_spread_bps=9.5, spread_bps=9.5, spread_zscore=5.0)
    cfg = _cfg(min_net_spread_bps=3.0)
    ctx = {"percentile_cutoff": 10.0}
    assert not passes_gates(c, cfg, ctx)[0]
    assert passes_gates(c, cfg, ctx, hysteresis_bps=1.0)[0]


def test_hysteresis_does_not_relax_safety_floors():
    # Illiquid junk stays out even with hysteresis.
    c = _candidate(spread_bps=500.0, net_spread_bps=500.0, spread_zscore=5.0)
    assert not passes_gates(c, _cfg(), hysteresis_bps=100.0)[0]


# ── clean universe for the percentile cutoff ────────────────────────────────


def test_basic_floors_exclude_junk_from_universe():
    from mexc_monitor.screener.filters import passes_basic_floors

    assert passes_basic_floors(_candidate(), _cfg())
    # stale row
    assert not passes_basic_floors(_candidate(tick_age_ms=60_000.0), _cfg())
    # illiquid wide spread
    assert not passes_basic_floors(_candidate(spread_bps=500.0), _cfg())
    # thin L1
    assert not passes_basic_floors(_candidate(l1_notional=1.0), _cfg())
    # blacklisted
    assert not passes_basic_floors(_candidate(symbol="USDCUSDT"), _cfg())
    # leveraged token
    assert not passes_basic_floors(_candidate(symbol="BTC3LUSDT"), _cfg())
    # low net spread is FINE — the percentile gate itself decides that
    assert passes_basic_floors(_candidate(net_spread_bps=0.5), _cfg())
