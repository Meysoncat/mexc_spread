"""Lag Detector for estimating lead-lag delays between exchanges.

Uses cross-correlation of mid-price time series to determine which exchange
leads and by how many milliseconds for each symbol.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""

from __future__ import annotations

import math
import time
import threading
from typing import Optional

import numpy as np

from mexc_monitor.lead_lag.models import LagEstimate, LeadLagConfig
from mexc_monitor.lead_lag.price_buffer import PriceBuffer


# Minimum number of observations required to compute cross-correlation
_MIN_OBSERVATIONS = 20
# Significance level for correlation test
_ALPHA = 0.05


class LagDetector:
    """Estimates lead-lag delay between exchanges using cross-correlation.

    Dynamically identifies which exchange leads for each symbol by computing
    cross-correlation between mid-price time series from different exchanges.

    The detector:
    - Computes cross-correlation between all configured exchange pairs
    - Identifies the leader as the exchange whose price changes precede others
    - Estimates lag_ms as the shift with maximum correlation coefficient
    - Provides confidence score based on observation count vs maximum possible
    - Skips computation when observations < 20 (marks as unavailable)
    - Guarantees lag >= 0 and <= price_buffer_history_sec * 1000
    - Updates estimates at a configurable interval (default 30 seconds)
    """

    def __init__(self, config: LeadLagConfig) -> None:
        """Initialize the LagDetector.

        Args:
            config: Lead-lag configuration with estimation parameters.
        """
        self._config = config
        self._lock = threading.Lock()
        # Key: symbol -> LagEstimate (best estimate across all exchange pairs)
        self._estimates: dict[str, LagEstimate] = {}
        # Track last update time per symbol to enforce interval
        self._last_update_time: dict[str, float] = {}
        # Maximum lag in ms based on buffer history
        self._max_lag_ms = config.price_buffer_history_sec * 1000.0
        # Update interval in seconds
        self._update_interval_sec = config.lag_estimation_interval_sec

    def update_estimate(self, symbol: str, price_buffer: PriceBuffer) -> Optional[LagEstimate]:
        """Recompute the lag estimate for a symbol using price buffer data.

        Computes cross-correlation between the leader exchange and each lagger
        exchange, then selects the pair with the highest correlation. Dynamically
        determines which exchange actually leads based on the sign of the optimal lag.

        Args:
            symbol: Trading pair symbol (e.g. "BTCUSDT").
            price_buffer: PriceBuffer containing recent price history.

        Returns:
            Updated LagEstimate, or None if insufficient data.
        """
        now = time.time()

        # Check if enough time has passed since last update
        last_update = self._last_update_time.get(symbol, 0.0)
        if now - last_update < self._update_interval_sec:
            with self._lock:
                return self._estimates.get(symbol)

        # Gather all exchanges to compare
        all_exchanges = [self._config.leader_exchange] + list(self._config.lagger_exchanges)

        # Count number of pairwise comparisons for Bonferroni correction
        num_comparisons = len(all_exchanges) * (len(all_exchanges) - 1) // 2
        bonferroni_alpha = _ALPHA / max(1, num_comparisons)

        best_estimate: Optional[LagEstimate] = None
        best_correlation = -2.0  # Start below minimum possible correlation

        # Compare each pair of exchanges
        for i, exchange_a in enumerate(all_exchanges):
            for exchange_b in all_exchanges[i + 1:]:
                estimate = self._compute_cross_correlation(
                    symbol, exchange_a, exchange_b, price_buffer,
                    alpha=bonferroni_alpha,
                )
                if estimate is not None and estimate.correlation > best_correlation:
                    best_correlation = estimate.correlation
                    best_estimate = estimate

        # Store the best estimate
        with self._lock:
            if best_estimate is not None:
                self._estimates[symbol] = best_estimate
            self._last_update_time[symbol] = now

        return best_estimate

    def get_estimate(self, symbol: str) -> Optional[LagEstimate]:
        """Get the current lag estimate for a symbol.

        Args:
            symbol: Trading pair symbol.

        Returns:
            Current LagEstimate or None if unavailable.
        """
        with self._lock:
            return self._estimates.get(symbol)

    def get_leader(self, symbol: str) -> Optional[str]:
        """Get the dynamically determined leader exchange for a symbol.

        Args:
            symbol: Trading pair symbol.

        Returns:
            Leader exchange name or None if no estimate available.
        """
        with self._lock:
            estimate = self._estimates.get(symbol)
            if estimate is None:
                return None
            return estimate.leader_exchange

    def get_all_estimates(self) -> dict[str, LagEstimate]:
        """Get all current lag estimates.

        Returns:
            Dict mapping symbol to its current LagEstimate.
        """
        with self._lock:
            return dict(self._estimates)

    def _compute_cross_correlation(
        self,
        symbol: str,
        exchange_a: str,
        exchange_b: str,
        price_buffer: PriceBuffer,
        *,
        alpha: float = _ALPHA,
    ) -> Optional[LagEstimate]:
        """Compute cross-correlation between two exchanges for a symbol.

        Retrieves price history from both exchanges, aligns them by timestamp,
        computes normalized cross-correlation, and finds the lag with maximum
        correlation.

        Args:
            symbol: Trading pair symbol.
            exchange_a: First exchange name.
            exchange_b: Second exchange name.
            price_buffer: PriceBuffer with price history.

        Returns:
            LagEstimate if sufficient data, None otherwise.
        """
        history_sec = self._config.price_buffer_history_sec

        # Get price histories
        history_a = price_buffer.get_history(exchange_a, symbol, history_sec)
        history_b = price_buffer.get_history(exchange_b, symbol, history_sec)

        # Check minimum observations (Requirement 3.6)
        if len(history_a) < _MIN_OBSERVATIONS or len(history_b) < _MIN_OBSERVATIONS:
            return None

        # Extract timestamps and mid prices
        times_a = np.array([s.timestamp_ms for s in history_a], dtype=np.float64)
        prices_a = np.array([s.mid for s in history_a], dtype=np.float64)
        times_b = np.array([s.timestamp_ms for s in history_b], dtype=np.float64)
        prices_b = np.array([s.mid for s in history_b], dtype=np.float64)

        # Align time series to common time grid via interpolation
        # Use the union of timestamps as the common grid
        t_min = max(times_a[0], times_b[0])
        t_max = min(times_a[-1], times_b[-1])

        if t_max <= t_min:
            return None

        # Create a uniform time grid within the overlapping period
        # Use the average sample interval as the grid spacing
        avg_interval_a = (times_a[-1] - times_a[0]) / (len(times_a) - 1) if len(times_a) > 1 else 100.0
        avg_interval_b = (times_b[-1] - times_b[0]) / (len(times_b) - 1) if len(times_b) > 1 else 100.0
        grid_interval = max(min(avg_interval_a, avg_interval_b), 1.0)  # At least 1ms

        n_points = int((t_max - t_min) / grid_interval) + 1
        # Cap grid points to avoid excessive computation
        n_points = min(n_points, 2000)
        if n_points < _MIN_OBSERVATIONS:
            return None

        common_times = np.linspace(t_min, t_max, n_points)

        # Interpolate both series onto the common grid
        interp_a = np.interp(common_times, times_a, prices_a)
        interp_b = np.interp(common_times, times_b, prices_b)

        # Compute price changes (first differences) for better correlation
        diff_a = np.diff(interp_a)
        diff_b = np.diff(interp_b)

        if len(diff_a) < _MIN_OBSERVATIONS:
            return None

        # Normalize (zero mean, unit variance)
        std_a = np.std(diff_a)
        std_b = np.std(diff_b)

        if std_a < 1e-12 or std_b < 1e-12:
            # No price variation — cannot determine lag
            return None

        norm_a = (diff_a - np.mean(diff_a)) / std_a
        norm_b = (diff_b - np.mean(diff_b)) / std_b

        # Compute cross-correlation using numpy correlate
        # Full mode gives correlation for all possible lags.
        # We normalize by n (constant) rather than overlap count because the
        # overlap normalization amplifies noise at low-overlap lags, creating
        # spurious peaks. The ÷n bias is acceptable for peak-finding; the
        # overlap-corrected value is reported separately as needed.
        correlation = np.correlate(norm_a, norm_b, mode='full')
        n = len(norm_a)
        correlation = correlation / n

        # The lag index: correlation[n-1] corresponds to zero lag
        # Positive lag index means A leads B (A's changes appear first)
        # Negative lag index means B leads A
        lags = np.arange(-(n - 1), n)

        # Limit search to lags within max_lag_ms
        max_lag_samples = int(self._max_lag_ms / grid_interval)
        valid_mask = np.abs(lags) <= max_lag_samples
        if not np.any(valid_mask):
            return None

        valid_correlation = correlation[valid_mask]
        valid_lags = lags[valid_mask]

        # Find the lag with maximum correlation
        best_idx = int(np.argmax(valid_correlation))
        best_lag_samples = valid_lags[best_idx]
        best_corr_value = float(valid_correlation[best_idx])

        # Convert lag from samples to milliseconds
        lag_ms_raw = best_lag_samples * grid_interval

        # np.correlate(a, b, mode='full') convention:
        # When best lag < 0: b is delayed relative to a → a leads b
        # When best lag > 0: a is delayed relative to b → b leads a
        if lag_ms_raw <= 0:
            # A leads B (B is delayed)
            leader = exchange_a
            lagger = exchange_b
            lag_ms = -lag_ms_raw
        else:
            # B leads A (A is delayed)
            leader = exchange_b
            lagger = exchange_a
            lag_ms = lag_ms_raw

        # Clamp lag to valid bounds (Requirement 3.5)
        lag_ms = max(0.0, min(lag_ms, self._max_lag_ms))

        # Compute confidence as ratio of observations to maximum possible
        # Maximum possible = price_buffer_history_sec * expected_rate
        # We use the actual sample count vs the grid size as a proxy
        sample_count = min(len(history_a), len(history_b))
        # Max possible observations in the buffer window
        max_possible = n_points
        confidence = min(1.0, sample_count / max_possible) if max_possible > 0 else 0.0

        # Statistical significance: t-statistic and p-value
        # t = |r| × √(n-2) / √(1-r²)
        # For large n, t-distribution ≈ standard normal
        overlap_n = max(n - abs(best_lag_samples), 2)
        r_val = best_corr_value
        if abs(r_val) >= 1.0:
            r_val = 0.9999 if r_val > 0 else -0.9999
        t_stat = abs(r_val) * math.sqrt(overlap_n - 2) / math.sqrt(1.0 - r_val * r_val)
        # Two-tailed p-value via normal approximation (erfc)
        p_value = math.erfc(t_stat / math.sqrt(2.0))
        significant = p_value < alpha

        return LagEstimate(
            symbol=symbol,
            leader_exchange=leader,
            lagger_exchange=lagger,
            lag_ms=lag_ms,
            correlation=best_corr_value,
            confidence=confidence,
            sample_count=sample_count,
            updated_at=_iso_now(),
            significant=significant,
            p_value=p_value,
        )


def _iso_now() -> str:
    """Return current time as ISO8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
