"""Test clock skew detection integration."""

import httpx
import pytest
from unittest.mock import Mock

from mexc_monitor.clock_skew_middleware import ClockSkewClient, _get_detector
from mexc_monitor.clock_skew import ClockSkewDetector


def test_get_detector_lazy_init():
    """Test lazy initialization of global detector."""
    from mexc_monitor.backend.main import detector as main_detector

    detector_instance = _get_detector()
    assert detector_instance is main_detector


def test_clock_skew_client_integration():
    """Test that ClockSkewClient properly detects skew from Date header."""
    from mexc_monitor.backend.main import detector as global_detector

    detector = global_detector
    detector._skews.clear()

    client = ClockSkewClient()

    # Create a mock response with Date header - use a past date that's reasonable
    import datetime
    past_dt = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    date_str = past_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")

    headers = {
        "Date": date_str,
        "Content-Type": "application/json",
        "Server": "MEXC",
    }

    mock_response = Mock(spec=httpx.Response)
    mock_response.headers = headers
    mock_response.status_code = 200
    mock_response.text = "OK"

    # Use internal method to handle response
    result = client.handle_response(mock_response)

    assert result is mock_response
    assert "generic" in detector._skews


def test_clock_skew_client_different_exchanges():
    """Test clock skew detection with different exchange identifiers."""
    from mexc_monitor.backend.main import detector as global_detector

    detector = global_detector
    detector._skews.clear()

    # Create a client for MEXC
    client_mexc = ClockSkewClient(exchange="mexc")

    # Create a client for Binance
    client_binance = ClockSkewClient(exchange="binance")

    # Test different exchanges with a past date
    import datetime
    past_dt = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    date_str = past_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")

    headers = {
        "Date": date_str,
        "Server": "EXCHANGE",
    }

    mock_response = Mock(spec=httpx.Response)
    mock_response.headers = headers
    mock_response.status_code = 200
    mock_response.text = "OK"

    # Test MEXC
    client_mexc.handle_response(mock_response)
    assert "mexc" in detector._skews

    # Test Binance
    client_binance.handle_response(mock_response)
    assert "binance" in detector._skews

    # Verify both have timestamps (skew)
    assert detector.get_skew_ms("mexc") > 0
    assert detector.get_skew_ms("binance") > 0


def test_clock_skew_detection_disabled():
    """Test that detector works when Date header is missing."""
    detector = ClockSkewDetector()
    detector._skews.clear()

    client = ClockSkewClient()

    # Response without Date header
    headers = {
        "Content-Type": "application/json",
        "Server": "TestServer",
    }

    mock_response = Mock(spec=httpx.Response)
    mock_response.headers = headers
    mock_response.status_code = 200
    mock_response.text = "OK"

    result = client.handle_response(mock_response)

    assert result is mock_response
    # Should not raise exception, just skip
    assert len(detector._skews) == 0


def test_clock_skew_detector_methods():
    """Test basic ClockSkewDetector methods."""
    detector = ClockSkewDetector()

    # Initial state
    assert detector.get_skew_ms("test") == 0.0
    assert detector.is_skewed("test") is False

    # Add some skew
    detector._skews["test"] = Mock(skew_ms=1500.0, last_check_ms=0, warning_count=0)

    assert detector.get_skew_ms("test") == 1500.0
    assert detector.is_skewed("test") is True

    # Get all skews
    all_skews = detector.get_all_skews()
    assert "test" in all_skews
    assert all_skews["test"] == 1500.0

    # Get status
    status = detector.get_status()
    assert len(status) > 0
    for entry in status:
        assert "exchange" in entry
        assert "skew_ms" in entry
        assert "skewed" in entry


def test_freshness_with_skew_adjustment():
    """Test that get_fresh_tick can adjust timestamps for skew."""
    import time

    detector = ClockSkewDetector()
    detector._skews.clear()

    # Simulate clock skew: local clock is 500ms ahead
    skew_ms = 500.0
    now_ms = int(time.time() * 1000)
    detector._skews["generic"] = Mock(
        skew_ms=skew_ms,
        last_check_ms=now_ms,
        warning_count=0,
    )

    # Test adjust_timestamp method directly
    tick_time = 1000000  # Some past timestamp
    adjusted_timestamp = detector.adjust_timestamp("generic", tick_time)

    # The adjusted timestamp should be greater than original (local clock ahead)
    assert adjusted_timestamp > tick_time
    # The difference should be close to 500ms (the skew)
    assert abs(adjusted_timestamp - tick_time - skew_ms) < 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])