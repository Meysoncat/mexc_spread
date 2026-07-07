"""Stats Engine for the Lead-Lag Arbitrage module.

Computes aggregate statistics from historical signals for dashboard display.
Provides summary stats, per-symbol breakdowns, and lag time distributions.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from mexc_monitor.lead_lag.models import (
    LeadLagSignal,
    LeadLagStats,
    SignalStatus,
)
from mexc_monitor.lead_lag.store import LeadLagStore, SignalQuery


# ---------------------------------------------------------------------------
# Additional data models for stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolStats:
    """Per-symbol statistics breakdown."""

    symbol: str
    total_signals: int
    resolved_signals: int
    expired_signals: int
    active_signals: int
    avg_lag_ms: Optional[float]         # None if no resolved signals with lag data
    avg_theoretical_pnl_bps: Optional[float]  # None if no resolved signals


@dataclass(frozen=True)
class LagDistribution:
    """Histogram of lag times with fixed 50ms buckets."""

    symbol: Optional[str]               # None means all symbols
    buckets: list[tuple[int, int, int]] = field(default_factory=list)
    # Each tuple: (bucket_start_ms, bucket_end_ms, count)


# ---------------------------------------------------------------------------
# StatsEngine
# ---------------------------------------------------------------------------


class StatsEngine:
    """Computes aggregate statistics from historical signals.

    Uses the LeadLagStore to query signals within a time window and
    computes various metrics for dashboard display.

    Requirement 6.1: Aggregate stats over configurable window
    Requirement 6.2: Per-symbol breakdown
    Requirement 6.3: Consistency guarantee (total == resolved + expired + active)
    Requirement 6.4: Lag distribution histogram with 50ms buckets
    Requirement 6.5: Zero/null values when no signals
    """

    def __init__(self, store: LeadLagStore) -> None:
        self._store = store

    def summary(self, window_hours: int = 24) -> LeadLagStats:
        """Compute aggregate statistics over a time window.

        Requirement 6.1: total_signals, resolved_signals, expired_signals,
        win_rate, avg_lag_ms, median_lag_ms, avg_theoretical_pnl_bps,
        total_theoretical_pnl_bps, signals_per_hour.

        Requirement 6.5: Zero counters and null for avg/median when no signals.
        """
        signals = self._query_window(window_hours)

        if not signals:
            return LeadLagStats(
                window_hours=window_hours,
                total_signals=0,
                resolved_signals=0,
                expired_signals=0,
                win_rate=None,
                avg_lag_ms=None,
                median_lag_ms=None,
                avg_theoretical_pnl_bps=None,
                total_theoretical_pnl_bps=0.0,
                signals_per_hour=0.0,
                top_symbols=[],
            )

        total_signals = len(signals)
        resolved_signals = sum(
            1 for s in signals if s.status == SignalStatus.RESOLVED
        )
        expired_signals = sum(
            1 for s in signals if s.status == SignalStatus.EXPIRED
        )

        # Win rate: resolved with pnl > 0 / total resolved
        resolved_with_pnl = [
            s for s in signals
            if s.status == SignalStatus.RESOLVED and s.theoretical_pnl_bps is not None
        ]
        if resolved_with_pnl:
            wins = sum(1 for s in resolved_with_pnl if s.theoretical_pnl_bps > 0)
            win_rate = wins / len(resolved_with_pnl)
        else:
            win_rate = None

        # Avg/median lag from resolved signals with actual_lag_ms
        lag_values = [
            s.actual_lag_ms for s in signals
            if s.actual_lag_ms is not None
        ]
        if lag_values:
            avg_lag_ms = statistics.mean(lag_values)
            median_lag_ms = statistics.median(lag_values)
        else:
            avg_lag_ms = None
            median_lag_ms = None

        # Theoretical PnL stats
        pnl_values = [
            s.theoretical_pnl_bps for s in signals
            if s.theoretical_pnl_bps is not None
        ]
        if pnl_values:
            avg_theoretical_pnl_bps = statistics.mean(pnl_values)
            total_theoretical_pnl_bps = sum(pnl_values)
        else:
            avg_theoretical_pnl_bps = None
            total_theoretical_pnl_bps = 0.0

        # Signals per hour
        signals_per_hour = total_signals / max(window_hours, 1)

        # Top symbols by signal count
        symbol_counts: dict[str, int] = {}
        for s in signals:
            symbol_counts[s.symbol] = symbol_counts.get(s.symbol, 0) + 1
        top_symbols = sorted(symbol_counts, key=symbol_counts.get, reverse=True)[:5]

        return LeadLagStats(
            window_hours=window_hours,
            total_signals=total_signals,
            resolved_signals=resolved_signals,
            expired_signals=expired_signals,
            win_rate=win_rate,
            avg_lag_ms=avg_lag_ms,
            median_lag_ms=median_lag_ms,
            avg_theoretical_pnl_bps=avg_theoretical_pnl_bps,
            total_theoretical_pnl_bps=total_theoretical_pnl_bps,
            signals_per_hour=signals_per_hour,
            top_symbols=top_symbols,
        )

    def per_symbol_stats(self, window_hours: int = 24) -> list[SymbolStats]:
        """Compute per-symbol statistics breakdown.

        Requirement 6.2: signal count, avg lag, avg PnL per symbol.
        Requirement 6.5: Empty list when no signals.
        """
        signals = self._query_window(window_hours)

        if not signals:
            return []

        # Group signals by symbol
        by_symbol: dict[str, list[LeadLagSignal]] = {}
        for s in signals:
            by_symbol.setdefault(s.symbol, []).append(s)

        result: list[SymbolStats] = []
        for symbol, sym_signals in sorted(by_symbol.items()):
            total = len(sym_signals)
            resolved = sum(
                1 for s in sym_signals if s.status == SignalStatus.RESOLVED
            )
            expired = sum(
                1 for s in sym_signals if s.status == SignalStatus.EXPIRED
            )
            active = sum(
                1 for s in sym_signals if s.status == SignalStatus.ACTIVE
            )

            # Avg lag from signals with actual_lag_ms
            lag_values = [
                s.actual_lag_ms for s in sym_signals
                if s.actual_lag_ms is not None
            ]
            avg_lag = statistics.mean(lag_values) if lag_values else None

            # Avg PnL from resolved signals
            pnl_values = [
                s.theoretical_pnl_bps for s in sym_signals
                if s.theoretical_pnl_bps is not None
            ]
            avg_pnl = statistics.mean(pnl_values) if pnl_values else None

            result.append(SymbolStats(
                symbol=symbol,
                total_signals=total,
                resolved_signals=resolved,
                expired_signals=expired,
                active_signals=active,
                avg_lag_ms=avg_lag,
                avg_theoretical_pnl_bps=avg_pnl,
            ))

        return result

    def lag_distribution(self, symbol: str | None = None) -> LagDistribution:
        """Compute lag time distribution as a histogram with 50ms buckets.

        Requirement 6.4: Fixed 50ms buckets from 0 to max lag.
        Requirement 6.5: Empty buckets list when no signals with lag data.
        """
        signals = self._query_window_all(symbol)

        # Collect actual_lag_ms values
        lag_values = [
            s.actual_lag_ms for s in signals
            if s.actual_lag_ms is not None
        ]

        if not lag_values:
            return LagDistribution(symbol=symbol, buckets=[])

        # Build histogram with 50ms buckets
        max_lag = max(lag_values)
        bucket_size = 50  # ms

        # Determine number of buckets needed
        num_buckets = int(max_lag // bucket_size) + 1

        # Count signals in each bucket
        bucket_counts: list[int] = [0] * num_buckets
        for lag in lag_values:
            bucket_idx = min(int(lag // bucket_size), num_buckets - 1)
            bucket_counts[bucket_idx] += 1

        # Build bucket tuples: (start_ms, end_ms, count)
        buckets: list[tuple[int, int, int]] = []
        for i in range(num_buckets):
            start_ms = i * bucket_size
            end_ms = (i + 1) * bucket_size
            count = bucket_counts[i]
            if count > 0:
                buckets.append((start_ms, end_ms, count))

        return LagDistribution(symbol=symbol, buckets=buckets)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _query_window(self, window_hours: int) -> list[LeadLagSignal]:
        """Query signals within the specified time window."""
        from datetime import datetime, timedelta, timezone

        time_from = (
            datetime.now(timezone.utc) - timedelta(hours=window_hours)
        ).isoformat()

        query = SignalQuery(time_from=time_from)
        return self._store.query_signals(query)

    def _query_window_all(self, symbol: str | None = None) -> list[LeadLagSignal]:
        """Query all signals, optionally filtered by symbol.

        Uses a large window (168 hours = 7 days) to get all relevant data.
        """
        from datetime import datetime, timedelta, timezone

        # Use 7 days as the max window for distribution
        time_from = (
            datetime.now(timezone.utc) - timedelta(hours=168)
        ).isoformat()

        query = SignalQuery(time_from=time_from, symbol=symbol)
        return self._store.query_signals(query)
