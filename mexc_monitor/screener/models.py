"""Data shapes for the spread screener."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candidate:
    """A single symbol evaluated by the screener on one scan.

    Holds the raw metrics (from the spot snapshot) plus the state-derived
    persistence/rolling metrics. ``score`` is filled in by the scorer after
    gate-checking.
    """

    symbol: str
    bid: float
    ask: float
    mid: float
    spread_bps: float | None
    net_spread_bps: float | None
    l1_notional: float
    volume_24h_quote: float
    tick_age_ms: float
    observed_at_iso: str
    # state-derived
    lifetime_sec: float
    pct_time_above: float  # 0..100 over the rolling window
    spread_std: float | None  # bps, over the rolling window
    # filled by scorer
    score: float = 0.0
    score_breakdown: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class ScreenerOpportunity:
    """An opportunity that passed all gates, ready for the API/UI."""

    symbol: str
    bid: float
    ask: float
    mid: float
    spread_bps: float | None
    net_spread_bps: float | None
    l1_notional: float
    volume_24h_quote: float
    lifetime_sec: float
    pct_time_above: float
    spread_std: float | None
    tick_age_ms: float
    score: float
    score_breakdown: dict[str, float]
    observed_at_iso: str


def candidate_to_opportunity(c: Candidate) -> ScreenerOpportunity:
    return ScreenerOpportunity(
        symbol=c.symbol,
        bid=c.bid,
        ask=c.ask,
        mid=c.mid,
        spread_bps=c.spread_bps,
        net_spread_bps=c.net_spread_bps,
        l1_notional=c.l1_notional,
        volume_24h_quote=c.volume_24h_quote,
        lifetime_sec=c.lifetime_sec,
        pct_time_above=c.pct_time_above,
        spread_std=c.spread_std,
        tick_age_ms=c.tick_age_ms,
        score=c.score,
        score_breakdown=c.score_breakdown or {},
        observed_at_iso=c.observed_at_iso,
    )


def opportunity_to_dict(o: ScreenerOpportunity) -> dict:
    """Flat dict for JSON serialization (FastAPI / SSE)."""
    return {
        "symbol": o.symbol,
        "bid": o.bid,
        "ask": o.ask,
        "mid": o.mid,
        "spread_bps": o.spread_bps,
        "net_spread_bps": o.net_spread_bps,
        "l1_notional": o.l1_notional,
        "volume_24h_quote": o.volume_24h_quote,
        "lifetime_sec": round(o.lifetime_sec, 1),
        "pct_time_above": round(o.pct_time_above, 1),
        "spread_std": o.spread_std,
        "tick_age_ms": round(o.tick_age_ms, 0),
        "score": round(o.score, 3),
        "score_breakdown": {k: round(v, 3) for k, v in o.score_breakdown.items()},
        "observed_at": o.observed_at_iso,
    }
