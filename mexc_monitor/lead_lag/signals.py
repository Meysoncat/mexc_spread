"""Signal Generator for lead-lag arbitrage opportunities.

Monitors price divergence between leader and lagger exchanges, computes
z-scores of the spread, and generates signals when thresholds are exceeded.
Tracks signal lifecycle: ACTIVE → RESOLVED or EXPIRED.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from mexc_monitor.lead_lag.config import LeadLagConfig
from mexc_monitor.lead_lag.detector import LagDetector
from mexc_monitor.lead_lag.models import (
    LeadLagSignal,
    SignalDirection,
    SignalStatus,
    SpreadSnapshot,
)
from mexc_monitor.lead_lag.price_buffer import PriceBuffer


class SignalGenerator:
    """Generates trading signals when leader-lagger spread diverges significantly.

    Monitors all configured (symbol, lagger_exchange) pairs. For each pair:
    - Computes rolling mean and std of spread over rolling_window_sec
    - Computes z-score = (current_spread - mean) / std
    - Creates ACTIVE signal when z-score > entry threshold AND |spread| > min_spread_bps
    - Resolves signal when z-score < exit threshold
    - Expires signal when lifetime > signal_timeout_sec

    Guarantees at most one ACTIVE signal per (symbol, lagger_exchange) pair.
    """

    def __init__(
        self,
        config: LeadLagConfig,
        price_buffer: PriceBuffer,
        lag_detector: LagDetector,
    ) -> None:
        """Initialize the SignalGenerator.

        Args:
            config: Lead-lag configuration with thresholds and timing.
            price_buffer: PriceBuffer for current and historical prices.
            lag_detector: LagDetector for lag estimates.
        """
        self._config = config
        self._price_buffer = price_buffer
        self._lag_detector = lag_detector
        self._lock = threading.Lock()

        # Active signals: key = (symbol, lagger_exchange) -> LeadLagSignal
        self._active_signals: dict[tuple[str, str], LeadLagSignal] = {}

        # All signals (recent history for get_recent_signals)
        self._all_signals: deque[LeadLagSignal] = deque(maxlen=1000)

        # Spread history for z-score computation:
        # key = (symbol, lagger_exchange) -> deque of (timestamp_ms, spread_bps)
        self._spread_history: dict[tuple[str, str], deque[tuple[int, float]]] = {}

    def tick(self) -> list[LeadLagSignal]:
        """Process one tick: check all pairs for signal generation/resolution/expiry.

        Should be called periodically (e.g., on every price update or at a fixed interval).

        Returns:
            List of newly created or state-changed signals during this tick.
        """
        changed_signals: list[LeadLagSignal] = []
        now_ms = _now_ms()

        with self._lock:
            # First: expire and resolve existing active signals
            for key in list(self._active_signals.keys()):
                signal = self._active_signals[key]
                symbol = signal.symbol
                lagger = signal.lagger_exchange

                # Get current spread for this pair
                leader = signal.leader_exchange
                spread_snapshot = self._price_buffer.get_spread(symbol, leader, lagger)

                # Check expiry first (Requirement 4.4)
                # signal_timeout_sec × 1000 ms
                created_at_ms = _iso_to_ms(signal.created_at)
                lifetime_ms = now_ms - created_at_ms

                if lifetime_ms > self._config.signal_timeout_sec * 1000:
                    # Expire the signal
                    signal.status = SignalStatus.EXPIRED
                    signal.resolved_at = _iso_now()

                    if spread_snapshot is not None:
                        signal.exit_spread_bps = spread_snapshot.spread_bps
                    else:
                        signal.exit_spread_bps = 0.0

                    signal.theoretical_pnl_bps = (
                        signal.entry_spread_bps
                        - (signal.exit_spread_bps or 0.0)
                        - 2 * self._config.assumed_taker_fee_bps
                    )

                    del self._active_signals[key]
                    changed_signals.append(signal)
                    continue

                # Check resolution (Requirement 4.3)
                if spread_snapshot is not None:
                    z_score = self._compute_z_score(symbol, lagger, spread_snapshot.spread_bps)

                    if z_score is not None and abs(z_score) < self._config.z_score_exit_threshold:
                        signal.status = SignalStatus.RESOLVED
                        signal.resolved_at = _iso_now()
                        signal.actual_lag_ms = float(now_ms - created_at_ms)
                        signal.exit_spread_bps = spread_snapshot.spread_bps
                        signal.theoretical_pnl_bps = (
                            signal.entry_spread_bps
                            - signal.exit_spread_bps
                            - 2 * self._config.assumed_taker_fee_bps
                        )

                        del self._active_signals[key]
                        changed_signals.append(signal)
                        continue

            # Second: check for new signal generation
            for symbol in self._config.symbols:
                for lagger in self._config.lagger_exchanges:
                    key = (symbol, lagger)

                    # Requirement 4.5: no duplicate active signals
                    if key in self._active_signals:
                        continue

                    # Get leader from lag detector or use configured default
                    leader = self._lag_detector.get_leader(symbol)
                    if leader is None:
                        leader = self._config.leader_exchange

                    # Skip if leader == lagger
                    if leader == lagger:
                        continue

                    # Get current spread
                    spread_snapshot = self._price_buffer.get_spread(symbol, leader, lagger)
                    if spread_snapshot is None:
                        continue

                    spread_bps = spread_snapshot.spread_bps

                    # Record spread in history for z-score computation
                    self._record_spread(symbol, lagger, now_ms, spread_bps)

                    # Compute z-score
                    z_score = self._compute_z_score(symbol, lagger, spread_bps)
                    if z_score is None:
                        continue

                    # Requirement 4.1: check entry conditions
                    if (
                        abs(z_score) > self._config.z_score_entry_threshold
                        and abs(spread_bps) > self._config.min_spread_bps
                    ):
                        # Determine direction (Requirement 4.1)
                        if spread_snapshot.leader_mid > spread_snapshot.lagger_mid:
                            direction = SignalDirection.LONG
                        else:
                            direction = SignalDirection.SHORT

                        # Get lag estimate
                        lag_estimate = self._lag_detector.get_estimate(symbol)
                        estimated_lag_ms = lag_estimate.lag_ms if lag_estimate else 0.0

                        # Create signal
                        signal = LeadLagSignal(
                            id=str(uuid.uuid4()),
                            symbol=symbol,
                            leader_exchange=leader,
                            lagger_exchange=lagger,
                            direction=direction,
                            z_score=z_score,
                            entry_spread_bps=spread_bps,
                            leader_mid_at_signal=spread_snapshot.leader_mid,
                            lagger_mid_at_signal=spread_snapshot.lagger_mid,
                            estimated_lag_ms=estimated_lag_ms,
                            status=SignalStatus.ACTIVE,
                            created_at=_iso_now(),
                        )

                        self._active_signals[key] = signal
                        self._all_signals.append(signal)
                        changed_signals.append(signal)

        return changed_signals

    def get_active_signals(self) -> list[LeadLagSignal]:
        """Get all currently active signals.

        Returns:
            List of signals with status ACTIVE.
        """
        with self._lock:
            return list(self._active_signals.values())

    def get_recent_signals(self, limit: int = 50) -> list[LeadLagSignal]:
        """Get recent signals (all statuses), sorted by created_at DESC.

        Args:
            limit: Maximum number of signals to return.

        Returns:
            List of recent signals, newest first.
        """
        with self._lock:
            signals = list(self._all_signals)
        # Return newest first, limited
        signals.reverse()
        return signals[:limit]

    def _record_spread(
        self, symbol: str, lagger: str, timestamp_ms: int, spread_bps: float
    ) -> None:
        """Record a spread observation for z-score computation.

        Must be called while holding self._lock.
        """
        key = (symbol, lagger)
        if key not in self._spread_history:
            self._spread_history[key] = deque(maxlen=50000)

        history = self._spread_history[key]
        history.append((timestamp_ms, spread_bps))

        # Evict entries older than rolling_window_sec
        cutoff_ms = timestamp_ms - int(self._config.rolling_window_sec * 1000)
        while history and history[0][0] < cutoff_ms:
            history.popleft()

    def _compute_z_score(
        self, symbol: str, lagger: str, current_spread_bps: float
    ) -> Optional[float]:
        """Compute z-score of the current spread relative to rolling window.

        z_score = (current_spread - rolling_mean) / rolling_std

        Must be called while holding self._lock.

        Args:
            symbol: Trading pair symbol.
            lagger: Lagger exchange name.
            current_spread_bps: Current spread in basis points.

        Returns:
            Z-score value, or None if insufficient data for computation.
        """
        key = (symbol, lagger)
        history = self._spread_history.get(key)

        if not history or len(history) < 2:
            return None

        # Compute rolling mean and std from history
        spreads = [s for _, s in history]
        n = len(spreads)
        mean = sum(spreads) / n
        variance = sum((s - mean) ** 2 for s in spreads) / n
        std = variance ** 0.5

        if std < 1e-12:
            # No variation in spread — cannot compute meaningful z-score
            return None

        z_score = (current_spread_bps - mean) / std
        return z_score


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


def _iso_now() -> str:
    """Return current time as ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _iso_to_ms(iso_str: str) -> int:
    """Convert ISO8601 string to milliseconds since epoch.

    Args:
        iso_str: ISO8601 formatted datetime string.

    Returns:
        Timestamp in milliseconds.
    """
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
