"""Тесты диагностики источников (mexc_monitor/source_probes.py).

Сетевые вызовы замоканы — проверяем классификацию статусов, полноту реестра
и обработку ошибок probe_one без реальных запросов к биржам.
"""

from __future__ import annotations

import contextlib
from typing import Any

import pytest

from mexc_monitor import source_probes as sp


# --- classify -------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (200, sp.STATUS_OK),
        (204, sp.STATUS_OK),
        (299, sp.STATUS_OK),
        (429, sp.STATUS_RATE_LIMITED),
        (451, sp.STATUS_GEO_BLOCKED),
        (403, sp.STATUS_GEO_BLOCKED),
        (500, sp.STATUS_ERROR),
        (404, sp.STATUS_ERROR),
        (None, sp.STATUS_ERROR),
    ],
)
def test_classify(code: int | None, expected: str) -> None:
    assert sp.classify(code) == expected


# --- PROBES registry ------------------------------------------------------


def test_probes_registry_covers_all_exchanges() -> None:
    expected = {
        "mexc",
        "binance",
        "bybit",
        "okx",
        "gateio",
        "bitget",
        "htx",
        "asterdex",
        "dydx",
        "lighter",
        "hyperliquid",
    }
    assert set(sp.PROBES) == expected


def test_probes_have_valid_https_urls_and_methods() -> None:
    for name, probe in sp.PROBES.items():
        assert probe.url.startswith("https://"), name
        assert probe.method in ("GET", "POST"), name
        # POST-пробы обязаны нести тело (Hyperliquid /info).
        if probe.method == "POST":
            assert probe.json_body is not None, name


# --- probe_one (mocked client) -------------------------------------------


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    """Мок httpx-клиента: возвращает заданный код или бросает исключение."""

    def __init__(self, status_code: int | None = None, exc: Exception | None = None):
        self._code = status_code
        self._exc = exc

    def get(self, *_a: Any, **_k: Any) -> _FakeResp:
        if self._exc:
            raise self._exc
        assert self._code is not None
        return _FakeResp(self._code)

    def post(self, *_a: Any, **_k: Any) -> _FakeResp:
        return self.get()


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    @contextlib.contextmanager
    def _factory(*_a: Any, **_k: Any):
        yield client

    monkeypatch.setattr(sp, "mexc_httpx_client", _factory)


def test_probe_one_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(status_code=200))
    res = sp.probe_one("okx", settings=object())
    assert res["ok"] is True
    assert res["status"] == sp.STATUS_OK
    assert res["status_code"] == 200
    assert res["elapsed_ms"] >= 0
    assert res["url"] == sp.PROBES["okx"].url


def test_probe_one_geo_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(status_code=451))
    res = sp.probe_one("binance", settings=object())
    assert res["ok"] is False
    assert res["status"] == sp.STATUS_GEO_BLOCKED
    assert res["status_code"] == 451


def test_probe_one_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(exc=ConnectionError("boom")))
    res = sp.probe_one("gateio", settings=object())
    assert res["ok"] is False
    assert res["status"] == sp.STATUS_ERROR
    assert res["status_code"] is None
    assert "ConnectionError" in res["error"]


def test_probe_one_unknown_exchange() -> None:
    res = sp.probe_one("does-not-exist", settings=object())
    assert res["status"] == sp.STATUS_ERROR
    assert res["error"] == "no probe defined"


def test_probe_all_filters_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _FakeClient(status_code=200))
    out = sp.probe_all(settings=object(), exchanges=["okx", "bogus", "gateio"])
    assert set(out) == {"okx", "gateio"}
    assert all(r["status"] == sp.STATUS_OK for r in out.values())
