"""Тесты per-exchange прокси-реестра и smart routing."""

from __future__ import annotations

import pytest

from mexc_monitor.config import DEFAULT_SETTINGS, Settings
from mexc_monitor.http_utils import effective_http_proxy
from mexc_monitor.proxy_registry import (
    DIRECT,
    KNOWN_EXCHANGES,
    ProxyRegistry,
    ProxyValidationError,
    REGISTRY,
    normalize_proxy,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Каждый тест стартует с чистым глобальным реестром."""
    REGISTRY.replace_all(default=None, per_exchange={})
    yield
    REGISTRY.replace_all(default=None, per_exchange={})


# --- normalize_proxy -------------------------------------------------------


def test_normalize_empty_is_none():
    assert normalize_proxy(None) is None
    assert normalize_proxy("") is None
    assert normalize_proxy("   ") is None


def test_normalize_direct_sentinel():
    assert normalize_proxy("direct") == DIRECT
    assert normalize_proxy("DIRECT") == DIRECT
    assert normalize_proxy("  Direct  ") == DIRECT


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:7890",
        "https://user:pass@host:8080",
        "socks5://10.0.0.1:1080",
        "socks5h://proxy.example.com:1080",
    ],
)
def test_normalize_valid_schemes(url):
    assert normalize_proxy(url) == url


@pytest.mark.parametrize("url", ["ftp://host:21", "ssh://host", "tcp://1.2.3.4:9"])
def test_normalize_rejects_bad_scheme(url):
    with pytest.raises(ProxyValidationError):
        normalize_proxy(url)


def test_normalize_rejects_missing_host():
    with pytest.raises(ProxyValidationError):
        normalize_proxy("http://")


# --- ProxyRegistry ---------------------------------------------------------


def test_default_applies_to_all():
    r = ProxyRegistry()
    r.set_default("http://proxy:8080")
    assert r.resolve("binance") == "http://proxy:8080"
    assert r.resolve("gateio") == "http://proxy:8080"


def test_exchange_override_beats_default():
    r = ProxyRegistry()
    r.set_default("http://proxy:8080")
    r.set_exchange("bybit", "socks5h://other:1080")
    assert r.resolve("bybit") == "socks5h://other:1080"
    assert r.resolve("binance") == "http://proxy:8080"


def test_direct_override_returns_sentinel():
    r = ProxyRegistry()
    r.set_default("http://proxy:8080")
    r.set_exchange("gateio", "direct")
    # resolve() возвращает сентинел DIRECT (не None), чтобы вызывающий отличил
    # явный direct от «нет мнения». None-семантику даёт effective_http_proxy.
    assert r.resolve("gateio") == DIRECT
    assert r.resolve("binance") == "http://proxy:8080"


def test_default_direct_is_treated_as_reset():
    r = ProxyRegistry()
    r.set_default("http://proxy:8080")
    r.set_default("direct")  # для default DIRECT ≡ сброс
    assert r.resolve("binance") is None


def test_empty_exchange_value_clears_override():
    r = ProxyRegistry()
    r.set_default("http://proxy:8080")
    r.set_exchange("bybit", "socks5://x:1080")
    r.set_exchange("bybit", "")  # сброс override → снова наследует default
    assert r.resolve("bybit") == "http://proxy:8080"


def test_exchange_name_case_insensitive():
    r = ProxyRegistry()
    r.set_exchange("ByBit", "http://p:1")
    assert r.resolve("bybit") == "http://p:1"
    assert r.resolve("BYBIT") == "http://p:1"


def test_replace_all_is_atomic_on_error():
    r = ProxyRegistry()
    r.set_default("http://good:8080")
    r.set_exchange("bybit", "http://keep:1")
    # Невалидный элемент → исключение, старое состояние не меняется.
    with pytest.raises(ProxyValidationError):
        r.replace_all(default="http://new:9", per_exchange={"okx": "ftp://bad"})
    assert r.resolve("generic") == "http://good:8080"
    assert r.resolve("bybit") == "http://keep:1"


def test_snapshot_shape():
    r = ProxyRegistry()
    r.set_default("http://p:8080")
    r.set_exchange("okx", "direct")
    snap = r.snapshot()
    assert snap["default"] == "http://p:8080"
    assert snap["per_exchange"] == {"okx": "direct"}


# --- effective_http_proxy (integration with config fallback) ---------------


def test_effective_falls_back_to_static_config():
    # Реестр пуст → должен использовать Settings.http_proxy_url.
    s = Settings(http_proxy_url="http://config-proxy:3128")
    assert effective_http_proxy(s, "binance") == "http://config-proxy:3128"


def test_effective_registry_beats_config():
    s = Settings(http_proxy_url="http://config-proxy:3128")
    REGISTRY.set_default("http://runtime:8080")
    assert effective_http_proxy(s, "binance") == "http://runtime:8080"


def test_effective_direct_override_returns_none_even_with_config():
    s = Settings(http_proxy_url="http://config-proxy:3128")
    REGISTRY.set_exchange("gateio", "direct")
    # DIRECT override должен вернуть None (прямой), несмотря на config-прокси.
    assert effective_http_proxy(s, "gateio") is None


def test_known_exchanges_nonempty_and_lowercase():
    assert KNOWN_EXCHANGES
    assert all(e == e.lower() for e in KNOWN_EXCHANGES)


def test_default_settings_importable():
    # sanity: DEFAULT_SETTINGS доступен и имеет новое поле.
    assert hasattr(DEFAULT_SETTINGS, "http_proxy_per_exchange")
