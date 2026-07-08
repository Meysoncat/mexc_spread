"""Unit tests for Lead-Lag Arbitrage REST API endpoints.

Tests response structure, parameter validation (422 responses),
idempotent start/stop, and 404 for unknown symbols.

Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7
"""

from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

os.environ.setdefault("MEXC_SNAPSHOT_CACHE_TTL_SEC", "0")

import pytest


@pytest.fixture(scope="module")
def patched_app():
    """Import app with startup events mocked to avoid WebSocket connections."""
    with patch("mexc_monitor.ws_futures.ensure_started_from_settings"), \
         patch("mexc_monitor.ws_futures_orderbook.ensure_futures_orderbook_ws_started"), \
         patch("mexc_monitor.ws_spot_orderbook.ensure_spot_orderbook_ws_started"), \
         patch("mexc_monitor.history_worker.start_history_worker"), \
         patch("mexc_monitor.history_worker.stop_history_worker"), \
         patch("mexc_monitor.ws_spot_orderbook.stop_spot_orderbook_ws"):
        import backend.main as backend_main
        with patch.object(backend_main, "_ADMIN_TOKEN", "test-admin-token"):
            yield backend_main.app


@pytest.fixture(scope="module")
def client(patched_app):
    """Create a TestClient with startup events mocked."""
    from fastapi.testclient import TestClient
    with TestClient(patched_app, raise_server_exceptions=False) as c:
        c.headers.update({"X-Admin-Token": "test-admin-token"})
        yield c


class TestLeadLagStatus:
    """Test GET /api/lead-lag/status endpoint (Requirement 7.1)."""

    def test_status_returns_expected_fields(self, client):
        """Status response contains all required fields."""
        resp = client.get("/api/lead-lag/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "connections" in data
        assert "symbols_monitored" in data
        assert "active_signals_count" in data
        assert "uptime_sec" in data

    def test_status_types(self, client):
        """Status fields have correct types."""
        resp = client.get("/api/lead-lag/status")
        data = resp.json()
        assert isinstance(data["running"], bool)
        assert isinstance(data["connections"], dict)
        assert isinstance(data["symbols_monitored"], list)
        assert isinstance(data["active_signals_count"], int)
        assert isinstance(data["uptime_sec"], (int, float))
        assert data["active_signals_count"] >= 0
        assert data["uptime_sec"] >= 0


class TestLeadLagSignals:
    """Test GET /api/lead-lag/signals endpoint (Requirement 7.2)."""

    def test_signals_default_params(self, client):
        """Signals endpoint returns list with default params."""
        resp = client.get("/api/lead-lag/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_signals_with_active_filter(self, client):
        """Signals endpoint accepts active=true filter."""
        resp = client.get("/api/lead-lag/signals", params={"active": True})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_signals_with_symbol_filter(self, client):
        """Signals endpoint accepts symbol filter."""
        resp = client.get("/api/lead-lag/signals", params={"symbol": "BTCUSDT"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_signals_with_valid_limit(self, client):
        """Signals endpoint accepts valid limit."""
        resp = client.get("/api/lead-lag/signals", params={"limit": 100})
        assert resp.status_code == 200

    def test_signals_limit_too_low(self, client):
        """Signals endpoint returns 422 for limit < 1."""
        resp = client.get("/api/lead-lag/signals", params={"limit": 0})
        assert resp.status_code == 422

    def test_signals_limit_too_high(self, client):
        """Signals endpoint returns 422 for limit > 1000."""
        resp = client.get("/api/lead-lag/signals", params={"limit": 1001})
        assert resp.status_code == 422

    def test_signals_limit_negative(self, client):
        """Signals endpoint returns 422 for negative limit."""
        resp = client.get("/api/lead-lag/signals", params={"limit": -5})
        assert resp.status_code == 422


class TestLeadLagStats:
    """Test GET /api/lead-lag/stats endpoint (Requirement 7.3)."""

    def test_stats_default_window(self, client):
        """Stats endpoint returns data with default window."""
        resp = client.get("/api/lead-lag/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "window_hours" in data
        assert "total_signals" in data
        assert "resolved_signals" in data
        assert "expired_signals" in data
        assert "win_rate" in data
        assert "avg_lag_ms" in data
        assert "median_lag_ms" in data
        assert "avg_theoretical_pnl_bps" in data
        assert "total_theoretical_pnl_bps" in data
        assert "signals_per_hour" in data
        assert "top_symbols" in data

    def test_stats_custom_window(self, client):
        """Stats endpoint accepts custom window_hours."""
        resp = client.get("/api/lead-lag/stats", params={"window_hours": 6})
        assert resp.status_code == 200
        data = resp.json()
        assert data["window_hours"] == 6

    def test_stats_window_too_low(self, client):
        """Stats endpoint returns 422 for window_hours < 1."""
        resp = client.get("/api/lead-lag/stats", params={"window_hours": 0})
        assert resp.status_code == 422

    def test_stats_window_too_high(self, client):
        """Stats endpoint returns 422 for window_hours > 168."""
        resp = client.get("/api/lead-lag/stats", params={"window_hours": 169})
        assert resp.status_code == 422

    def test_stats_window_negative(self, client):
        """Stats endpoint returns 422 for negative window_hours."""
        resp = client.get("/api/lead-lag/stats", params={"window_hours": -1})
        assert resp.status_code == 422


class TestLeadLagPrices:
    """Test GET /api/lead-lag/prices endpoint (Requirement 7.4)."""

    def test_prices_unknown_symbol_404(self, client):
        """Prices endpoint returns 404 for unknown symbol."""
        resp = client.get("/api/lead-lag/prices", params={"symbol": "UNKNOWNXYZ"})
        assert resp.status_code == 404

    def test_prices_requires_symbol(self, client):
        """Prices endpoint requires symbol parameter."""
        resp = client.get("/api/lead-lag/prices")
        assert resp.status_code == 422


class TestLeadLagLagEstimates:
    """Test GET /api/lead-lag/lag-estimates endpoint (Requirement 7.5)."""

    def test_lag_estimates_returns_list(self, client):
        """Lag estimates endpoint returns a list."""
        resp = client.get("/api/lead-lag/lag-estimates")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestLeadLagStartStop:
    """Test POST /api/lead-lag/start and /api/lead-lag/stop (Requirement 7.6)."""

    def test_stop_when_not_running(self, client):
        """Stop when already stopped returns 200 with running=false (idempotent)."""
        resp = client.post("/api/lead-lag/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False

    def test_stop_idempotent(self, client):
        """Multiple stop calls return same result."""
        resp1 = client.post("/api/lead-lag/stop")
        resp2 = client.post("/api/lead-lag/stop")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["running"] == resp2.json()["running"]
