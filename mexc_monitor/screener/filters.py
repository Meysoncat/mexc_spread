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
) -> tuple[bool, list[str]]:
    """Hard-cut gate filters. Returns (passed, reasons) where ``reasons`` lists
    every failed gate (useful for debugging / "why not" tooltips).

    ``adaptive_ctx`` carries the live-universe percentile cutoff so the
    spread-size decision can be regime-relative. Absolute safety floors
    (max spread, volume, liquidity, lifetime, freshness, blacklist) always
    apply; the percentile / z-score gates run only when their toggles are on.
    """
    reasons: list[str] = []

    if c.spread_bps is None or c.net_spread_bps is None:
        return False, ["no spread data"]

    # ── Absolute safety floors (always applied) ──────────────────────────────
    if c.symbol.upper() in cfg.symbol_blacklist:
        reasons.append("blacklisted")

    if c.net_spread_bps < cfg.min_net_spread_bps:
        reasons.append(
            f"net_spread {c.net_spread_bps:.1f} < floor {cfg.min_net_spread_bps}"
        )

    if c.spread_bps > cfg.max_spread_bps:
        reasons.append(f"spread {c.spread_bps:.0f} > {cfg.max_spread_bps} (illiquid)")

    if c.l1_notional < cfg.min_l1_notional_usdt:
        reasons.append(
            f"l1_notional {c.l1_notional:.0f} < {cfg.min_l1_notional_usdt}"
        )

    if c.volume_24h_quote < cfg.min_volume_24h_usdt:
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
        if cutoff is not None and c.net_spread_bps < cutoff:
            reasons.append(
                f"net_spread {c.net_spread_bps:.1f} < percentile cutoff {cutoff:.1f}"
            )

    if cfg.use_spread_zscore:
        if c.spread_zscore is None or c.spread_zscore < cfg.min_spread_zscore:
            reasons.append(
                f"z-score {c.spread_zscore} < {cfg.min_spread_zscore}"
            )

    return (len(reasons) == 0, reasons)


def score_candidate(
    c: Candidate, cfg: ScreenerConfig
) -> tuple[float, dict[str, float]]:
    """Soft ranker. Returns (score, breakdown) where breakdown names each term
    so the UI can show *why* a coin ranks where it does."""
    net = c.net_spread_bps if c.net_spread_bps is not None else 0.0
    z = c.spread_zscore if c.spread_zscore is not None else 0.0

    spread_term = cfg.w_spread * min(max(net, 0.0), cfg.spread_cap_bps)
    liq_term = cfg.w_liq * math.log1p(max(c.l1_notional, 0.0) / cfg.liq_ref_usdt)
    life_term = cfg.w_life * math.log1p(max(c.lifetime_sec, 0.0))
    stab_term = cfg.w_stab * (max(c.pct_time_above, 0.0) / 100.0)
    vol_term = -cfg.w_vol * (c.spread_std if c.spread_std is not None else 0.0)
    stale_term = -cfg.w_stale * (max(c.tick_age_ms, 0.0) / 1000.0)
    z_term = cfg.w_zscore * min(max(z, 0.0), cfg.zscore_cap)

    breakdown = {
        "spread": spread_term,
        "liquidity": liq_term,
        "lifetime": life_term,
        "stability": stab_term,
        "volatility": vol_term,
        "staleness": stale_term,
        "zscore": z_term,
    }
    score = (
        spread_term
        + liq_term
        + life_term
        + stab_term
        + vol_term
        + stale_term
        + z_term
    )
    return score, breakdown
