"""
Checkpoint 7 verification: Backend endpoints work for all 11 exchanges.

Tests:
1. Backend can start without import errors
2. All exchange client modules import correctly
3. Supported exchanges list contains all 11 exchanges
4. Interval mappings are complete for all exchanges
5. /api/snapshot endpoint accepts all 11 exchange values (no 400)
6. /api/klines/batch endpoint accepts all 11 exchange values (no 400)
7. Unknown exchanges return HTTP 400
"""
from __future__ import annotations

import os
from unittest.mock import patch

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
        from backend.main import app
        yield app


@pytest.fixture(scope="module")
def client(patched_app):
    """Create a TestClient with startup events mocked."""
    from fastapi.testclient import TestClient
    with TestClient(patched_app, raise_server_exceptions=False) as c:
        yield c


def test_backend_imports_without_errors():
    """1. The backend can start without import errors."""
    from backend.main import app, _SUPPORTED_EXCHANGES
    assert app is not None
    assert len(_SUPPORTED_EXCHANGES) == 11


def test_exchange_client_imports():
    """2. All exchange client modules can be imported."""
    from mexc_monitor.binance import binance_snapshot_rows
    from mexc_monitor.bybit import bybit_snapshot_rows
    from mexc_monitor.okx import okx_snapshot_rows
    from mexc_monitor.gateio import gateio_snapshot_rows
    from mexc_monitor.htx import htx_snapshot_rows
    from mexc_monitor.bitget import bitget_snapshot_rows
    from mexc_monitor.dydx import dydx_snapshot_rows
    from mexc_monitor.hyperliquid import hyperliquid_snapshot_rows

    assert callable(binance_snapshot_rows)
    assert callable(bybit_snapshot_rows)
    assert callable(okx_snapshot_rows)
    assert callable(gateio_snapshot_rows)
    assert callable(htx_snapshot_rows)
    assert callable(bitget_snapshot_rows)
    assert callable(dydx_snapshot_rows)
    assert callable(hyperliquid_snapshot_rows)


def test_supported_exchanges_list():
    """3. Supported exchanges list contains all 11 exchanges."""
    from backend.main import _SUPPORTED_EXCHANGES
    expected = {"mexc", "asterdex", "lighter", "binance", "bybit", "okx", "gateio", "htx", "bitget", "dydx", "hyperliquid"}
    assert set(_SUPPORTED_EXCHANGES) == expected


def test_interval_mappings_complete():
    """4. Interval mappings exist for all exchanges."""
    from backend.main import (
        _INTERVAL_TO_BINANCE,
        _INTERVAL_TO_BYBIT,
        _INTERVAL_TO_OKX,
        _INTERVAL_TO_GATEIO,
        _INTERVAL_TO_HTX,
        _INTERVAL_TO_BITGET,
        _INTERVAL_TO_DYDX,
        _INTERVAL_TO_HYPERLIQUID,
    )

    intervals = ["5m", "15m", "1h", "4h", "1d"]
    mappings = {
        "binance": _INTERVAL_TO_BINANCE,
        "bybit": _INTERVAL_TO_BYBIT,
        "okx": _INTERVAL_TO_OKX,
        "gateio": _INTERVAL_TO_GATEIO,
        "htx": _INTERVAL_TO_HTX,
        "bitget": _INTERVAL_TO_BITGET,
        "dydx": _INTERVAL_TO_DYDX,
        "hyperliquid": _INTERVAL_TO_HYPERLIQUID,
    }

    for exchange, mapping in mappings.items():
        for interval in intervals:
            assert interval in mapping, f"Missing interval '{interval}' for {exchange}"
            assert mapping[interval], f"Empty mapping for interval '{interval}' on {exchange}"


def test_unknown_exchange_returns_400_snapshot(client):
    """5. Unknown exchanges return HTTP 400 on /api/snapshot."""
    response = client.get("/api/snapshot", params={"exchange": "nonexistent_exchange"})
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert "supported" in body
    assert len(body["supported"]) == 11
    assert "nonexistent_exchange" in body.get("error", "")


def test_unknown_exchange_returns_400_klines_batch(client):
    """6. Unknown exchanges return HTTP 400 on /api/klines/batch."""
    response = client.get("/api/klines/batch", params={
        "exchange": "nonexistent_exchange",
        "symbols": "BTCUSDT",
        "interval": "1h",
        "limit": "10",
    })
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert "supported" in body


def test_all_exchanges_not_400_snapshot(client):
    """7. All 11 exchanges are accepted by /api/snapshot (no 400)."""
    exchanges = ["mexc", "asterdex", "lighter", "binance", "bybit", "okx", "gateio", "htx", "bitget", "dydx", "hyperliquid"]
    for ex in exchanges:
        response = client.get("/api/snapshot", params={"exchange": ex, "nocache": "true"})
        # Should NOT return 400 (may return 200 with ok=false if API is down, that's fine)
        assert response.status_code != 400, f"Exchange '{ex}' returned 400 unexpectedly"


def test_all_exchanges_not_400_klines_batch(client):
    """8. All 11 exchanges are accepted by /api/klines/batch (no 400)."""
    exchanges = ["mexc", "asterdex", "lighter", "binance", "bybit", "okx", "gateio", "htx", "bitget", "dydx", "hyperliquid"]
    for ex in exchanges:
        response = client.get("/api/klines/batch", params={
            "exchange": ex,
            "symbols": "BTCUSDT",
            "interval": "1h",
            "limit": "10",
        })
        # Should NOT return 400
        assert response.status_code != 400, f"Exchange '{ex}' returned 400 on klines/batch"


def test_case_insensitive_exchange(client):
    """9. Exchange parameter is case-insensitive."""
    for ex in ["MEXC", "Binance", "BYBIT", "OKX"]:
        response = client.get("/api/snapshot", params={"exchange": ex, "nocache": "true"})
        assert response.status_code != 400, f"Exchange '{ex}' (uppercase) returned 400"
