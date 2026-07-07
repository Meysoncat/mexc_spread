"""Freshness guards for spread buffer ticks.

Prevents trading engines from acting on stale market data. All engines that
read from ``spread_buffer.get_latest`` should go through these helpers instead
so that network hiccups or a stalled WebSocket feed cannot produce phantom
trades on minutes-old quotes.

Integration with Clock Skew Detection:
- add exchange= parameter to specify source exchange (mexc, binance, etc.)
- enable adjust_for_skew=True to correct timestamps using clock skew detector
"""

from __future__ import annotations

import time

from mexc_monitor.clock_skew import ClockSkewDetector
from mexc_monitor.spread_buffer import SpreadTick, get_latest

DEFAULT_MAX_TICK_AGE_MS: float = 5000.0
detector: ClockSkewDetector | None = None


def _get_detector() -> ClockSkewDetector:
    """Get global clock skew detector (lazy initialization)."""
    global detector
    if detector is None:
        from mexc_monitor.backend.main import detector as global_detector
        detector = global_detector
    return detector


def now_ms() -> int:
    """Current wall-clock time in unix milliseconds."""
    return int(time.time() * 1000)


def tick_age_ms(tick: SpreadTick, adjust_for_skew: bool = True) -> int:
    """Age of *tick* relative to now, in milliseconds.

    Optionally corrects timestamp for clock skew using exchange detector.
    """
    timestamp = tick.timestamp_ms
    if adjust_for_skew:
        detector = _get_detector()
        if detector:
            timestamp = detector.adjust_timestamp(tick.exchange or "generic", timestamp)
    return now_ms() - timestamp


def is_fresh(tick: SpreadTick | None, max_age_ms: float, adjust_for_skew: bool = True) -> bool:
    """Return ``True`` when *tick* is present and within *max_age_ms* of now.

    ``max_age_ms <= 0`` disables the check (always fresh) for backward
    compatibility and testing.
    """
    if tick is None:
        return False
    if max_age_ms <= 0:
        return True
    return tick_age_ms(tick, adjust_for_skew=adjust_for_skew) <= max_age_ms


def get_fresh_tick(
    symbol: str,
    max_age_ms: float = DEFAULT_MAX_TICK_AGE_MS,
    *,
    exchange: str = "",
    adjust_for_skew: bool = True,
) -> SpreadTick | None:
    """Return the latest tick for *symbol* only if it is fresher than *max_age_ms*.

    Parameters
    ----------
    symbol
        Trading pair symbol (e.g. "BTCUSDT").
    max_age_ms
        Maximum allowed tick age in milliseconds. 0 disables the check.
    exchange
        Source exchange name (e.g. "mexc", "binance"). Used for clock skew detection.
    adjust_for_skew
        If True, adjust timestamp for known clock skew before age comparison.

    Returns ``None`` when:
      - no tick exists for the symbol, or
      - the latest tick is older than *max_age_ms* (stale).
    """
    tick = get_latest(symbol)
    if tick is None:
        return None

    if not is_fresh(tick, max_age_ms, adjust_for_skew=adjust_for_skew):
        return None

    return tick


def get_fresh_tick_multi(
    symbols: list[str],
    max_age_ms: float = DEFAULT_MAX_TICK_AGE_MS,
    *,
    exchanges: list[str] | None = None,
    adjust_for_skew: bool = True,
) -> SpreadTick | None:
    """Return fresh ticks for *all* symbols, or ``None`` if any is stale/missing.

    Useful for multi-leg strategies (arbitrage) where acting on a fresh tick
    for one leg combined with a stale tick for another would produce phantom
    opportunities.

    Parameters
    ----------
    symbols
        List of trading pair symbols to check.
    max_age_ms
        Maximum allowed tick age in milliseconds.
    exchanges
        List of exchange names for each symbol (must match length of symbols).
    adjust_for_skew
        If True, adjust timestamps for clock skew.

    Returns ``None`` if any symbol is missing or stale.
    """
    ticks: list[SpreadTick] = []
    for i, sym in enumerate(symbols):
        ex = exchanges[i] if exchanges else "generic"
        tick = get_fresh_tick(sym, max_age_ms, exchange=ex, adjust_for_skew=adjust_for_skew)
        if tick is None:
            return None
        ticks.append(tick)
    if not ticks:
        return None
    return ticks[0]


def get_fresh_ticks(
    symbols: list[str],
    max_age_ms: float = DEFAULT_MAX_TICK_AGE_MS,
    *,
    exchanges: list[str] | None = None,
    adjust_for_skew: bool = True,
) -> dict[str, SpreadTick] | None:
    """Return a ``{symbol: tick}`` dict if *all* symbols have fresh ticks.

    Returns ``None`` if any symbol is missing or stale, so callers can treat
    the result as all-or-nothing.

    Parameters
    ----------
    symbols
        List of trading pair symbols.
    max_age_ms
        Maximum allowed tick age in milliseconds.
    exchanges
        List of exchange names for each symbol.
    adjust_for_skew
        If True, adjust timestamps for clock skew.
    """
    result: dict[str, SpreadTick] = {}
    for i, sym in enumerate(symbols):
        ex = exchanges[i] if exchanges else "generic"
        tick = get_fresh_tick(sym, max_age_ms, exchange=ex, adjust_for_skew=adjust_for_skew)
        if tick is None:
            return None
        result[sym] = tick
    return result if result else None
