"""Общие фикстуры и защита тестов от реальной сети.

Раньше часть тестов ходила на биржи по-настоящему: прогон занимал 4 минуты,
результат зависел от гео-блокировок и живости прокси, а «проверка валидации
параметра exchange» стоила 11 реальных снапшотов. Теперь исходящие соединения
запрещены по умолчанию; тесту, которому сеть действительно нужна, достаточно
пометки ``@pytest.mark.network``.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", ""})


class BlockedNetworkCall(RuntimeError):
    """Тест попытался выйти в сеть без пометки @pytest.mark.network."""


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "network: тест обращается к внешним сервисам (по умолчанию сеть запрещена)",
    )


def _host_of(address: Any) -> str:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


@pytest.fixture(autouse=True)
def _block_external_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Рубит исходящие TCP-соединения на уровне сокета.

    Работает и для httpx, и для websocket-client/websockets, и для любой
    библиотеки, которую подключат позже. Локальные адреса разрешены: на них
    держится TestClient и вспомогательные серверы.
    """
    if request.node.get_closest_marker("network"):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard(self: socket.socket, address: Any, *args, **kwargs):
        host = _host_of(address)
        if host in _ALLOWED_HOSTS or host.startswith("127."):
            return real_connect(self, address, *args, **kwargs)
        raise BlockedNetworkCall(
            f"Исходящее соединение с {host} заблокировано. "
            "Замокайте вызов или пометьте тест @pytest.mark.network."
        )

    def guard_ex(self: socket.socket, address: Any, *args, **kwargs):
        host = _host_of(address)
        if host in _ALLOWED_HOSTS or host.startswith("127."):
            return real_connect_ex(self, address, *args, **kwargs)
        return 111  # ECONNREFUSED

    monkeypatch.setattr(socket.socket, "connect", guard, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", guard_ex, raising=True)


@pytest.fixture
def stub_exchange_snapshots(monkeypatch: pytest.MonkeyPatch):
    """Подменяет построители снапшотов и klines в backend.main.

    Нужен тестам, которые проверяют валидацию параметров (400 / не-400), а не
    сами данные: без стабов каждый такой запрос тянет реальный REST биржи.
    """
    import backend.main as bm

    def fake_snapshot(market: str | None = None, *args, **kwargs) -> dict:
        return {
            "ok": True,
            "market": market or "spot",
            "rows": [],
            "count": 0,
            "loaded_at": "2026-01-01T00:00:00+00:00",
        }

    def fake_exchange_snapshot(exchange: str, market: str | None = None, *args, **kwargs) -> dict:
        return {
            "ok": True,
            "exchange": exchange,
            "market": market or "spot",
            "rows": [],
            "count": 0,
            "loaded_at": "2026-01-01T00:00:00+00:00",
        }

    monkeypatch.setattr(bm, "_build_snapshot_payload", fake_snapshot)
    monkeypatch.setattr(bm, "_build_exchange_snapshot_payload", fake_exchange_snapshot)
    monkeypatch.setattr(bm, "_fetch_klines_for_exchange", lambda *a, **k: [])
    return bm
