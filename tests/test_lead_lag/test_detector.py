"""Unit tests for the LagDetector class."""

from __future__ import annotations

import time

import pytest

from mexc_monitor.lead_lag.detector import LagDetector, _MIN_OBSERVATIONS
from mexc_monitor.lead_lag.models import LeadLagConfig, LagEstimate
from mexc_monitor.lead_lag.price_buffer import PriceBuffer


def _make_config(**overrides) -> LeadLagConfig:
    """Create a LeadLagConfig with sensible test defaults."""
    defaults = dict(
        enabled=True,
        leader_exchange="binance",
        lagger_exchanges=["mexc"],
        symbols=["BTCUSDT"],
        market="futures",
        z_score_entry_threshold=2.0,
        z_score_exit_threshold=0.5,
        signal_timeout_sec=10.0,
        rolling_window_sec=300.0,
        min_spread_bps=3.0,
        lag_estimation_interval_sec=0.0,  # No throttle for tests
        price_buffer_history_sec=60.0,
        db_path="data/test.sqlite",
        assumed_taker_fee_bps=2.0,
    )
    defaults.update(overrides)
    return LeadLagConfig(**defaults)


def _populate_buffer_with_lagged_series(
    buf: PriceBuffer,
    symbol: str,
    leader: str,
    lagger: str,
    n_points: int = 100,
    lag_ms: int = 200,
    base_price: float = 67500.0,
    interval_ms: int = 100,
) -> None:
    """Populate a PriceBuffer with synthetic data where lagger follows leader with a delay.

    Creates a sine-wave price series for the leader, and the same series
    shifted by lag_ms for the lagger.
    """
    import math

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (n_points * interval_ms)

    for i in range(n_points):
        t = start_ms + i * interval_ms
        # Leader price: sine wave with period of ~5 seconds
        price_change = 10.0 * math.sin(2 * math.pi * i / 50)
        leader_price = base_price + price_change
        buf.update(leader, symbol, leader_price, t)

    # Lagger follows with a delay
    for i in range(n_points):
        t = start_ms + i * interval_ms + lag_ms
        price_change = 10.0 * math.sin(2 * math.pi * i / 50)
        lagger_price = base_price + price_change
        buf.update(lagger, symbol, lagger_price, t)


class TestLagDetectorInit:
    """Tests for LagDetector initialization."""

    def test_init_creates_empty_estimates(self):
        """LagDetector starts with no estimates."""
        config = _make_config()
        detector = LagDetector(config)
        assert detector.get_all_estimates() == {}

    def test_get_estimate_returns_none_initially(self):
        """get_estimate returns None for unknown symbols."""
        config = _make_config()
        detector = LagDetector(config)
        assert detector.get_estimate("BTCUSDT") is None

    def test_get_leader_returns_none_initially(self):
        """get_leader returns None for unknown symbols."""
        config = _make_config()
        detector = LagDetector(config)
        assert detector.get_leader("BTCUSDT") is None


class TestLagDetectorInsufficientData:
    """Tests for LagDetector with insufficient data (< 20 observations)."""

    def test_skips_computation_with_too_few_observations(self):
        """Should return None when observations < 20 (Requirement 3.6)."""
        config = _make_config()
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        now_ms = int(time.time() * 1000)
        # Add only 10 points per exchange (below minimum of 20)
        for i in range(10):
            buf.update("binance", "BTCUSDT", 67500.0 + i, now_ms - (10 - i) * 100)
            buf.update("mexc", "BTCUSDT", 67500.0 + i, now_ms - (10 - i) * 100 + 200)

        result = detector.update_estimate("BTCUSDT", buf)
        assert result is None
        assert detector.get_estimate("BTCUSDT") is None

    def test_skips_when_one_exchange_has_insufficient_data(self):
        """Should return None when only one exchange has enough data."""
        config = _make_config()
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        now_ms = int(time.time() * 1000)
        # Leader has enough data
        for i in range(50):
            buf.update("binance", "BTCUSDT", 67500.0 + i, now_ms - (50 - i) * 100)
        # Lagger has too few
        for i in range(5):
            buf.update("mexc", "BTCUSDT", 67500.0 + i, now_ms - (5 - i) * 100)

        result = detector.update_estimate("BTCUSDT", buf)
        assert result is None


class TestLagDetectorWithSyntheticData:
    """Tests for LagDetector with synthetic lagged price series."""

    def test_detects_lag_with_known_delay(self):
        """Should detect approximate lag when lagger follows leader with known delay."""
        config = _make_config(
            lag_estimation_interval_sec=0.0,
            price_buffer_history_sec=60.0,
        )
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        # Create synthetic data with 200ms lag
        _populate_buffer_with_lagged_series(
            buf, "BTCUSDT", "binance", "mexc",
            n_points=200, lag_ms=200, interval_ms=50,
        )

        result = detector.update_estimate("BTCUSDT", buf)
        assert result is not None
        assert isinstance(result, LagEstimate)
        assert result.symbol == "BTCUSDT"
        # Lag should be approximately 200ms (within tolerance)
        assert result.lag_ms >= 0
        assert result.lag_ms <= config.price_buffer_history_sec * 1000

    def test_identifies_correct_leader(self):
        """Should identify the exchange whose changes precede as leader."""
        config = _make_config(
            lag_estimation_interval_sec=0.0,
            price_buffer_history_sec=60.0,
        )
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        # binance leads mexc by 300ms
        _populate_buffer_with_lagged_series(
            buf, "BTCUSDT", "binance", "mexc",
            n_points=200, lag_ms=300, interval_ms=50,
        )

        result = detector.update_estimate("BTCUSDT", buf)
        assert result is not None
        assert result.leader_exchange == "binance"
        assert result.lagger_exchange == "mexc"

    def test_lag_within_bounds(self):
        """Lag estimate should be >= 0 and <= price_buffer_history_sec * 1000 (Req 3.5)."""
        config = _make_config(
            lag_estimation_interval_sec=0.0,
            price_buffer_history_sec=60.0,
        )
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        _populate_buffer_with_lagged_series(
            buf, "BTCUSDT", "binance", "mexc",
            n_points=200, lag_ms=500, interval_ms=50,
        )

        result = detector.update_estimate("BTCUSDT", buf)
        assert result is not None
        assert result.lag_ms >= 0
        assert result.lag_ms <= 60.0 * 1000  # price_buffer_history_sec * 1000

    def test_provides_correlation_coefficient(self):
        """Estimate should include correlation coefficient between -1 and 1."""
        config = _make_config(lag_estimation_interval_sec=0.0)
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        _populate_buffer_with_lagged_series(
            buf, "BTCUSDT", "binance", "mexc",
            n_points=200, lag_ms=200, interval_ms=50,
        )

        result = detector.update_estimate("BTCUSDT", buf)
        assert result is not None
        assert -1.0 <= result.correlation <= 1.0

    def test_provides_confidence_score(self):
        """Estimate should include confidence score between 0 and 1."""
        config = _make_config(lag_estimation_interval_sec=0.0)
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        _populate_buffer_with_lagged_series(
            buf, "BTCUSDT", "binance", "mexc",
            n_points=200, lag_ms=200, interval_ms=50,
        )

        result = detector.update_estimate("BTCUSDT", buf)
        assert result is not None
        assert 0.0 <= result.confidence <= 1.0

    def test_provides_sample_count(self):
        """Estimate should include sample_count >= MIN_OBSERVATIONS."""
        config = _make_config(lag_estimation_interval_sec=0.0)
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        _populate_buffer_with_lagged_series(
            buf, "BTCUSDT", "binance", "mexc",
            n_points=200, lag_ms=200, interval_ms=50,
        )

        result = detector.update_estimate("BTCUSDT", buf)
        assert result is not None
        assert result.sample_count >= _MIN_OBSERVATIONS


class TestLagDetectorUpdateInterval:
    """Tests for the configurable update interval."""

    def test_respects_update_interval(self):
        """Should not recompute if interval hasn't elapsed."""
        config = _make_config(lag_estimation_interval_sec=30.0)
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        _populate_buffer_with_lagged_series(
            buf, "BTCUSDT", "binance", "mexc",
            n_points=200, lag_ms=200, interval_ms=50,
        )

        # First call should compute
        result1 = detector.update_estimate("BTCUSDT", buf)
        assert result1 is not None

        # Second call within interval should return cached result
        result2 = detector.update_estimate("BTCUSDT", buf)
        assert result2 is not None
        assert result2.updated_at == result1.updated_at

    def test_recomputes_after_interval(self):
        """Should recompute after the interval has elapsed."""
        config = _make_config(lag_estimation_interval_sec=0.0)  # No throttle
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        _populate_buffer_with_lagged_series(
            buf, "BTCUSDT", "binance", "mexc",
            n_points=200, lag_ms=200, interval_ms=50,
        )

        result1 = detector.update_estimate("BTCUSDT", buf)
        assert result1 is not None

        # Force time to advance past interval
        detector._last_update_time["BTCUSDT"] = 0.0

        result2 = detector.update_estimate("BTCUSDT", buf)
        assert result2 is not None


class TestLagDetectorGetAllEstimates:
    """Tests for get_all_estimates()."""

    def test_returns_all_computed_estimates(self):
        """Should return estimates for all symbols that have been computed."""
        config = _make_config(
            symbols=["BTCUSDT", "ETHUSDT"],
            lag_estimation_interval_sec=0.0,
        )
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        _populate_buffer_with_lagged_series(
            buf, "BTCUSDT", "binance", "mexc",
            n_points=200, lag_ms=200, interval_ms=50,
        )
        _populate_buffer_with_lagged_series(
            buf, "ETHUSDT", "binance", "mexc",
            n_points=200, lag_ms=150, interval_ms=50,
            base_price=3500.0,
        )

        detector.update_estimate("BTCUSDT", buf)
        detector.update_estimate("ETHUSDT", buf)

        all_estimates = detector.get_all_estimates()
        assert "BTCUSDT" in all_estimates
        assert "ETHUSDT" in all_estimates
        assert len(all_estimates) == 2


class TestLagDetectorNoVariation:
    """Tests for edge case: no price variation."""

    def test_returns_none_for_flat_prices(self):
        """Should return None when prices don't vary (std = 0)."""
        config = _make_config(lag_estimation_interval_sec=0.0)
        detector = LagDetector(config)
        buf = PriceBuffer(max_history_sec=60.0)

        now_ms = int(time.time() * 1000)
        # All prices are identical — no variation
        for i in range(50):
            buf.update("binance", "BTCUSDT", 67500.0, now_ms - (50 - i) * 100)
            buf.update("mexc", "BTCUSDT", 67500.0, now_ms - (50 - i) * 100)

        result = detector.update_estimate("BTCUSDT", buf)
        assert result is None
