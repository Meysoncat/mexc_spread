"""HTTP tests for /api/cross-screener/* (engine mocked, no network)."""

from __future__ import annotations

import queue

import pytest
from fastapi.testclient import TestClient

import backend.main as bm


class _FakeEngine:
    def get_opportunities(self, limit=None):
        opps = [{"symbol": "BTCUSDT", "pair": "MEXC×ASTER", "net_bps": 12.0}]
        return opps[:limit] if limit else opps

    def get_status(self):
        return {
            "running": False,
            "scanned_at": "2026-01-01T00:00:00+00:00",
            "opportunities": [{"symbol": "BTCUSDT"}],
            "opportunity_count": 1,
            "pairs": ["MEXC×ASTER"],
            "reject_reasons_top": [["net_floor", 3]],
            "last_error": None,
        }

    def get_config(self):
        return {"min_net_basis_bps": 5.0}

    def update_config(self, patch):
        if "bad_field" in patch:
            raise ValueError("unknown fields: ['bad_field']")
        return {"min_net_basis_bps": patch.get("min_net_basis_bps", 5.0)}

    def get_history(self, **kw):
        return []

    def subscribe(self):
        return queue.Queue()

    def unsubscribe(self, q):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(bm, "_cross_screener_engine", _FakeEngine())
    return TestClient(bm.app)


def test_opportunities_endpoint(client):
    r = client.get("/api/cross-screener/opportunities")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["opportunities"][0]["symbol"] == "BTCUSDT"
    assert body["pairs"] == ["MEXC×ASTER"]


def test_status_endpoint(client):
    r = client.get("/api/cross-screener/status")
    assert r.status_code == 200
    assert r.json()["reject_reasons_top"] == [["net_floor", 3]]


def test_config_get_and_patch(client, monkeypatch):
    monkeypatch.setattr(bm, "_ADMIN_TOKEN", "test-token")
    headers = {"X-Admin-Token": "test-token"}
    assert client.get("/api/cross-screener/config").status_code == 200
    r = client.patch(
        "/api/cross-screener/config", json={"min_net_basis_bps": 8.0}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["config"]["min_net_basis_bps"] == 8.0
    r = client.patch("/api/cross-screener/config", json={"bad_field": 1}, headers=headers)
    assert r.status_code == 400
    # No token → 401
    assert client.patch("/api/cross-screener/config", json={}).status_code == 401


def test_history_endpoint(client):
    r = client.get("/api/cross-screener/history")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 0, "events": []}
