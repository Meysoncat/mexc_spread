"""Tests for exchange-proxy resolution (runtime override > config > None)."""

from __future__ import annotations

import pytest

from mexc_monitor.config import Settings
from mexc_monitor.http_utils import (
    effective_http_proxy,
    set_runtime_http_proxy,
)


@pytest.fixture(autouse=True)
def _clear_runtime():
    set_runtime_http_proxy(None)
    yield
    set_runtime_http_proxy(None)


def test_no_proxy_returns_none():
    s = Settings(http_proxy_url="")
    assert effective_http_proxy(s) is None


def test_config_proxy_used_when_no_runtime():
    s = Settings(http_proxy_url="http://127.0.0.1:7890")
    assert effective_http_proxy(s) == "http://127.0.0.1:7890"


def test_runtime_overrides_config():
    s = Settings(http_proxy_url="http://from-config:1234")
    set_runtime_http_proxy("socks5://127.0.0.1:1080")
    assert effective_http_proxy(s) == "socks5://127.0.0.1:1080"


def test_runtime_clear_falls_back_to_config():
    s = Settings(http_proxy_url="http://from-config:1234")
    set_runtime_http_proxy("http://runtime:9999")
    set_runtime_http_proxy(None)  # clear
    assert effective_http_proxy(s) == "http://from-config:1234"


def test_whitespace_proxy_normalized():
    s = Settings(http_proxy_url="  http://127.0.0.1:7890  ")
    assert effective_http_proxy(s) == "http://127.0.0.1:7890"


def test_empty_runtime_string_treated_as_none():
    s = Settings(http_proxy_url="http://from-config:1234")
    set_runtime_http_proxy("   ")
    assert effective_http_proxy(s) == "http://from-config:1234"
