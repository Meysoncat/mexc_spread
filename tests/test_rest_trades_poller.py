"""Tests for the REST trades poller + fetch_recent_trades (no network)."""

from __future__ import annotations

import json
import time

import pytest

from mexc_monitor import trade_buffer as tb
from mexc_monitor.client import MexcApiError, fetch_recent_trades
from mexc_monitor.rest_trades_poller import RestTradesPoller, _side_from_trade


@pytest.fixture(autouse=True)
def _isolate_buffer():
    tb.clear()
    yield
    tb.clear()


# ── side mapping ────────────────────────────────────────────────────────────


def test_side_isbuyer_maker_true_is_sell():
    assert _side_from_trade({"isBuyerMaker": True}) == 2


def test_side_isbuyer_maker_false_is_buy():
    assert _side_from_trade({"isBuyerMaker": False}) == 1


def test_side_tradetype_fallback():
    assert _side_from_trade({"tradeType": "BID"}) == 2
    assert _side_from_trade({"tradeType": "ASK"}) == 1


def test_side_unknown_is_zero():
    assert _side_from_trade({}) == 0


# ── set_watch_symbols ───────────────────────────────────────────────────────


def test_set_watch_symbols_dedups_and_normalizes():
    p = RestTradesPoller(max_symbols=10)
    p.set_watch_symbols(["btcusdt", "BTCUSDT", " ethusdt "])
    assert p.status()["watch_symbols"] == ["BTCUSDT", "ETHUSDT"]


def test_set_watch_symbols_caps_to_max():
    p = RestTradesPoller(max_symbols=3)
    p.set_watch_symbols(["AAA", "BBB", "CCC", "DDD", "EEE"])
    assert p.status()["watch_symbols"] == ["AAA", "BBB", "CCC"]


# ── _ingest dedup + push ────────────────────────────────────────────────────


def _trades(base: int, *pairs):
    # pairs: (delta_ms, isBuyerMaker). base = epoch-ms anchor (use now()).
    return [
        {"price": "100.0", "qty": "1.0", "time": base + dt, "isBuyerMaker": b}
        for dt, b in pairs
    ]


def test_ingest_pushes_new_trades_to_buffer():
    base = int(time.time() * 1000)
    p = RestTradesPoller()
    n = p._ingest("BTCUSDT", _trades(base, (0, False), (1, True)))
    assert n == 2
    st = tb.get_stats("BTCUSDT", period_sec=600)
    assert st is not None
    assert st.count == 2
    assert st.buy_count == 1 and st.sell_count == 1


def test_ingest_dedups_by_time_on_second_call():
    base = int(time.time() * 1000)
    p = RestTradesPoller()
    p._ingest("BTCUSDT", _trades(base, (0, False), (1, True)))
    # same trades again → nothing new
    n = p._ingest("BTCUSDT", _trades(base, (0, False), (1, True)))
    assert n == 0
    # one new trade → only that one
    n = p._ingest("BTCUSDT", _trades(base, (0, False), (1, True), (2, False)))
    assert n == 1


def test_ingest_skips_zero_price_qty_and_bad_side():
    base = int(time.time() * 1000)
    p = RestTradesPoller()
    trades = [
        {"price": "0", "qty": "1.0", "time": base, "isBuyerMaker": False},  # bad price
        {"price": "100.0", "qty": "0", "time": base + 1, "isBuyerMaker": False},  # bad qty
        {"price": "100.0", "qty": "1.0", "time": base + 2},  # unknown side → skip
        {"price": "100.0", "qty": "1.0", "time": base + 3, "isBuyerMaker": True},  # ok
    ]
    n = p._ingest("BTCUSDT", trades)
    assert n == 1
    st = tb.get_stats("BTCUSDT", period_sec=600)
    assert st.count == 1 and st.sell_count == 1


# ── fetch_recent_trades (monkeypatch get_with_retry) ────────────────────────


class _FakeResp:
    def __init__(self, payload, raise_exc=None):
        self._payload = payload
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise:
            raise self._raise

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_fetch_recent_trades_parses_list(monkeypatch):
    captured = {}

    def fake_get(c, cfg, url, *, pacer=None, params=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp([{"price": "1", "qty": "2", "time": 5, "isBuyerMaker": True}])

    monkeypatch.setattr("mexc_monitor.client.get_with_retry", fake_get)
    out = fetch_recent_trades("btcusdt", limit=50, client=object())
    assert out == [{"price": "1", "qty": "2", "time": 5, "isBuyerMaker": True}]
    assert captured["params"]["symbol"] == "BTCUSDT"
    assert captured["params"]["limit"] == 50


def test_fetch_recent_trades_clamps_limit(monkeypatch):
    captured = {}

    def fake_get(c, cfg, url, *, pacer=None, params=None):
        captured["limit"] = params["limit"]
        return _FakeResp([])

    monkeypatch.setattr("mexc_monitor.client.get_with_retry", fake_get)
    fetch_recent_trades("BTCUSDT", limit=99999, client=object())
    assert captured["limit"] == 1000


def test_fetch_recent_trades_non_list_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "mexc_monitor.client.get_with_retry",
        lambda *a, **k: _FakeResp({"not": "a list"}),
    )
    assert fetch_recent_trades("BTCUSDT", client=object()) == []


def test_fetch_recent_trades_bad_json_raises(monkeypatch):
    monkeypatch.setattr(
        "mexc_monitor.client.get_with_retry",
        lambda *a, **k: _FakeResp(json.JSONDecodeError("x", "doc", 0)),
    )
    with pytest.raises(MexcApiError):
        fetch_recent_trades("BTCUSDT", client=object())
