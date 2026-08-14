"""Regression tests for admin-token exposure and auth on mutating endpoints.

Почему это тесты, а не ручная проверка: `GET /api/admin-token` отдавал токен
всем, у кого `request.client.host == 127.0.0.1`. За reverse-proxy на том же
хосте (документированный сценарий деплоя: nginx → 127.0.0.1:8006) так выглядит
любой внешний запрос, то есть весь торговый API открывался наружу.

TestClient создаётся без контекстного менеджера: startup-хендлеры не нужны,
а поднимать WS-фиды и префетч в тестах незачем.
"""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("MEXC_SNAPSHOT_CACHE_TTL_SEC", "0")

import pytest

TEST_TOKEN = "test-admin-token"


@pytest.fixture(scope="module")
def backend_main():
    import backend.main as bm

    with patch.object(bm, "_ADMIN_TOKEN", TEST_TOKEN):
        yield bm


def _client(backend_main, host: str = "127.0.0.1"):
    from fastapi.testclient import TestClient

    return TestClient(backend_main.app, raise_server_exceptions=False, client=(host, 45678))


class TestAdminTokenBootstrap:
    def test_local_dev_request_gets_token(self, backend_main, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN_LOCAL_BOOTSTRAP", raising=False)
        resp = _client(backend_main).get("/api/admin-token")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "token": TEST_TOKEN}

    @pytest.mark.parametrize(
        "header",
        ["X-Forwarded-For", "X-Real-IP", "Forwarded", "X-Forwarded-Host", "CF-Connecting-IP"],
    )
    def test_forwarded_request_is_rejected(self, backend_main, monkeypatch, header):
        """Петлевой client.host + forwarded-заголовок = запрос пришёл через прокси."""
        monkeypatch.delenv("ADMIN_TOKEN_LOCAL_BOOTSTRAP", raising=False)
        resp = _client(backend_main).get(
            "/api/admin-token", headers={header: "203.0.113.7"}
        )
        assert resp.status_code == 403
        assert TEST_TOKEN not in resp.text

    def test_remote_client_is_rejected(self, backend_main, monkeypatch):
        monkeypatch.delenv("ADMIN_TOKEN_LOCAL_BOOTSTRAP", raising=False)
        resp = _client(backend_main, host="203.0.113.7").get("/api/admin-token")
        assert resp.status_code == 403
        assert TEST_TOKEN not in resp.text

    def test_env_kill_switch_disables_bootstrap(self, backend_main, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN_LOCAL_BOOTSTRAP", "0")
        resp = _client(backend_main).get("/api/admin-token")
        assert resp.status_code == 403
        assert TEST_TOKEN not in resp.text


class TestMutatingEndpointsRequireToken:
    @pytest.mark.parametrize(
        ("method", "path", "kwargs"),
        [
            ("patch", "/api/network/config", {"json": {}}),
            ("post", "/api/density/watcher/start?symbols=BTCUSDT", {}),
            ("post", "/api/density/watcher/stop", {}),
            ("post", "/api/ai/chat", {"json": {"message": "hi"}}),
            # GET'ы отдают прокси-URL, а в них обычно логин:пароль.
            ("get", "/api/network/config", {}),
            ("get", "/api/network/test?exchange=mexc", {}),
        ],
    )
    def test_without_token_is_401(self, backend_main, method, path, kwargs):
        resp = getattr(_client(backend_main), method)(path, **kwargs)
        assert resp.status_code == 401

    def test_wrong_token_is_401(self, backend_main):
        resp = _client(backend_main).patch(
            "/api/network/config", json={}, headers={"X-Admin-Token": "nope"}
        )
        assert resp.status_code == 401

    def test_valid_token_passes_auth(self, backend_main):
        """Проверяем только проход авторизации: пустой payload ничего не меняет."""
        resp = _client(backend_main).patch(
            "/api/network/config", json={}, headers={"X-Admin-Token": TEST_TOKEN}
        )
        assert resp.status_code == 200
