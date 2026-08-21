"""Gate predicates + scorer — the heart of the screener's filter "system".

All functions are pure: they take a :class:`Candidate` and a
:class:`ScreenerConfig` and return a decision/score. This keeps the filtering
logic trivially unit-testable and decoupled from the snapshot/engine plumbing.
"""

from __future__ import annotations

import math

from mexc_monitor.screener.config import ScreenerConfig
from mexc_monitor.screener.models import Candidate


def _is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


_LEVERAGED_SUFFIXES = (
    "3LUSDT", "3SUSDT", "5LUSDT", "5SUSDT", "BULLUSDT", "BEARUSDT",
)


def is_leveraged_token(symbol: str) -> bool:
    """Leveraged tokens (3L/3S/5L/5S/BULL/BEAR) have structurally wide
    spread noise — their "spread" is a rebalancing artefact, not an
    arbitrage opportunity."""
    sym = symbol.strip().upper()
    return sym.endswith(_LEVERAGED_SUFFIXES)


def passes_basic_floors(c: Candidate, cfg: ScreenerConfig) -> bool:
    """Absolute sanity floors only (no percentile/z-score/lifetime gates).

    Used to build the *clean universe* for the adaptive percentile cutoff:
    dead/illiquid/stale rows would otherwise drag the regime reference and
    make the calibration meaningless.
    """
    if c.spread_bps is None or c.net_spread_bps is None:
        return False
    if c.symbol.upper() in cfg.symbol_blacklist:
        return False
    if cfg.leveraged_tokens_filter and is_leveraged_token(c.symbol):
        return False
    if c.spread_bps > cfg.max_spread_bps:
        return False
    if c.l1_notional < cfg.min_l1_notional_usdt:
        return False
    if cfg.volume_gate_mode != "soft" and c.volume_24h_quote < cfg.min_volume_24h_usdt:
        return False
    if c.tick_age_ms > cfg.max_tick_age_ms:
        return False
    return True


def compute_percentile_cutoff(values: list[float], pct: float) -> float:
    """Return the value at ``pct`` (0..100) of the distribution.

    NaN/None values are dropped. Uses linear interpolation between closest
    ranks (NumPy-default style). Returns ``0.0`` for an empty distribution so
    nothing is gated when the universe is unknown.
    """
    clean = [float(v) for v in values if v is not None and not _is_nan(v)]
    if not clean:
        return 0.0
    clean.sort()
    if len(clean) == 1:
        return clean[0]
    p = max(0.0, min(100.0, float(pct)))
    rank = (p / 100.0) * (len(clean) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(clean) - 1)
    frac = rank - lo
    return clean[lo] + (clean[hi] - clean[lo]) * frac


def passes_gates(
    c: Candidate,
    cfg: ScreenerConfig,
    adaptive_ctx: dict | None = None,
    hysteresis_bps: float = 0.0,
) -> tuple[bool, list[str]]:
    """Hard-cut gate filters. Returns (passed, reasons) where ``reasons`` lists
    every failed gate (useful for debugging / "why not" tooltips).

    ``adaptive_ctx`` carries the live-universe percentile cutoff so the
    spread-size decision can be regime-relative. Absolute safety floors
    (max spread, volume, liquidity, lifetime, freshness, blacklist) always
    apply; the percentile / z-score gates run only when their toggles are on.

    ``hysteresis_bps`` relaxes the net-spread floor and the percentile cutoff
    for shortlist incumbents so a coin hovering at the edge doesn't flap
    in/out of the top on every scan (which spams enter/exit history and
    feed re-subscriptions).
    """
    reasons: list[str] = []
    hyst = max(0.0, hysteresis_bps)

    if c.spread_bps is None or c.net_spread_bps is None:
        return False, ["no spread data"]

    # ── Absolute safety floors (always applied) ──────────────────────────────
    if c.symbol.upper() in cfg.symbol_blacklist:
        reasons.append("blacklisted")

    if cfg.leveraged_tokens_filter and is_leveraged_token(c.symbol):
        reasons.append("leveraged token")

    min_net = cfg.min_net_spread_bps - hyst
    if c.net_spread_bps < min_net:
        reasons.append(
            f"net_spread {c.net_spread_bps:.1f} < floor {min_net:.1f}"
        )

    if c.spread_bps > cfg.max_spread_bps:
        reasons.append(f"spread {c.spread_bps:.0f} > {cfg.max_spread_bps} (illiquid)")

    if c.l1_notional < cfg.min_l1_notional_usdt:
        reasons.append(
            f"l1_notional {c.l1_notional:.0f} < {cfg.min_l1_notional_usdt}"
        )

    # 24h volume: in "hard" mode it's a kill gate; in "soft" mode it is NOT —
    # a low-24h coin that is active *now* must survive to the activity tier.
    if cfg.volume_gate_mode != "soft" and c.volume_24h_quote < cfg.min_volume_24h_usdt:
        reasons.append(
            f"volume_24h {c.volume_24h_quote:.0f} < {cfg.min_volume_24h_usdt}"
        )

    if c.lifetime_sec < cfg.min_lifetime_sec:
        reasons.append(
            f"lifetime {c.lifetime_sec:.1f}s < {cfg.min_lifetime_sec}s"
        )

    if c.tick_age_ms > cfg.max_tick_age_ms:
        reasons.append(f"stale {c.tick_age_ms:.0f}ms > {cfg.max_tick_age_ms}ms")

    # ── Adaptive gates (regime-relative) ─────────────────────────────────────
    if cfg.adaptive_mode and adaptive_ctx is not None:
        cutoff = adaptive_ctx.get("percentile_cutoff")
        if cutoff is not None:
            cutoff_eff = cutoff - hyst
            if c.net_spread_bps < cutoff_eff:
                reasons.append(
                    f"net_spread {c.net_spread_bps:.1f} < percentile cutoff {cutoff_eff:.1f}"
                )

    if cfg.use_spread_zscore:
        if c.spread_zscore is None or c.spread_zscore < cfg.min_spread_zscore:
            reasons.append(
                f"z-score {c.spread_zscore} < {cfg.min_spread_zscore}"
            )

    return (len(reasons) == 0, reasons)


def _activity_factor(c: Candidate, cfg: ScreenerConfig) -> tuple[float, str]:
    """Real-time activity factor in [0, 1] + the source it came from.

    Preference order (first available wins):
      1. **trades_per_min** — honest fill density from the REST trades poller
         (the strongest signal: actual trades, not quote churn).
      2. **book_update_rate_per_min** — bookTicker push rate (quote activity;
         used when trades aren't polled for the symbol yet).
      3. **unknown** → ``activity_unknown_factor`` (neutral, so an unconfirmed
         wide-spread coin can still surface and get promoted to the feeds).
    Returns (factor, source) so the scorer can expose *why* in the breakdown.
    """
    # 1) trades density
    if c.trades_per_min is not None:
        floor = max(cfg.min_trades_per_min, 1e-6)
        return min(max(c.trades_per_min / floor, 0.0), 1.0), "trades"
    # 2) bookTicker rate
    if c.book_update_rate_per_min is not None:
        floor = max(cfg.min_book_update_rate_per_min, 1e-6)
        return min(max(c.book_update_rate_per_min / floor, 0.0), 1.0), "book"
    # 3) unknown
    return cfg.activity_unknown_factor, "unknown"


def score_candidate(
    c: Candidate, cfg: ScreenerConfig
) -> tuple[float, dict[str, float]]:
    """Soft ranker. Returns (score, breakdown) where breakdown names each term
    so the UI can show *why* a coin ranks where it does."""
    net = c.net_spread_bps if c.net_spread_bps is not None else 0.0
    z = c.spread_zscore if c.spread_zscore is not None else 0.0
    activity, activity_source = _activity_factor(c, cfg)

    spread_eff = min(max(net, 0.0), cfg.spread_cap_bps)
    # EV — the realizable $-edge/time proxy: net spread discounted by how active
    # the market is right now (trades density preferred over quote churn). This
    # is the dominant term (a wide-but-dead spread scores low: activity_factor≈0).
    ev_term = cfg.w_ev * spread_eff * activity
    # Raw spread magnitude (opt-in; superseded by EV — default weight 0).
    spread_term = cfg.w_spread * spread_eff
    liq_term = cfg.w_liq * math.log1p(max(c.l1_notional, 0.0) / cfg.liq_ref_usdt)
    life_term = cfg.w_life * math.log1p(max(c.lifetime_sec, 0.0))
    stab_term = cfg.w_stab * (max(c.pct_time_above, 0.0) / 100.0)
    vol_term = -cfg.w_vol * (c.spread_std if c.spread_std is not None else 0.0)
    # tick_age_ms is per-symbol (live-feed tick where available), so this
    # term discriminates between candidates again.
    stale_term = -cfg.w_stale * (max(c.tick_age_ms, 0.0) / 1000.0)
    z_term = cfg.w_zscore * min(max(z, 0.0), cfg.zscore_cap)
    # 24h volume reward — ranks liquid coins higher (soft signal, never a kill).
    vol24_term = cfg.w_volume24h * math.log1p(
        max(c.volume_24h_quote, 0.0) / cfg.volume_ref_usdt
    )
    # Order-flow imbalance: buy_quote/sell_quote over the trades window.
    # Sustained buy pressure is what closes a spread — a direct predictor of
    # the edge being realized. 0 when the trades poller has no data.
    flow_term = 0.0
    if c.buy_sell_ratio is not None:
        flow_term = cfg.w_flow * max(-1.0, min(1.0, c.buy_sell_ratio - 1.0))
    # Recent traded turnover (60s window) — micro-liquidity now, not 24h ago.
    tv_term = 0.0
    if c.trade_volume_quote_60s is not None:
        tv_term = cfg.w_trade_vol * math.log1p(
            max(c.trade_volume_quote_60s, 0.0) / cfg.trade_vol_ref_usdt
        )

    breakdown = {
        "ev": ev_term,
        "spread": spread_term,
        "liquidity": liq_term,
        "lifetime": life_term,
        "stability": stab_term,
        "volatility": vol_term,
        "staleness": stale_term,
        "zscore": z_term,
        "volume24h": vol24_term,
        "flow": flow_term,
        "trade_volume": tv_term,
        "activity_factor": round(activity, 3),
    }
    score = (
        ev_term
        + spread_term
        + liq_term
        + life_term
        + stab_term
        + vol_term
        + stale_term
        + z_term
        + vol24_term
        + flow_term
        + tv_term
    )
    return score, breakdown
