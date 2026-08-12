"""Юнит-тесты spot-парсеров ws_bookticker (без сети).

Проверяют, что WS-сообщения spot-стримов okx/gateio/htx корректно
преобразуются в _Update-кортежи, включая нормализацию символов и отличие
формата HTX spot (скалярные bid/ask) от futures (массивы).
"""

from __future__ import annotations

from mexc_monitor.ws_bookticker import (
    _parse_gateio_spot,
    _parse_htx_spot,
    _parse_okx,
    _spot_feeds,
)


# ------------------------------------------------------------------ OKX spot


def test_okx_spot_reuses_parser_and_normalizes():
    msg = {
        "arg": {"channel": "tickers", "instId": "BTC-USDT"},
        "data": [
            {
                "instType": "SPOT",
                "instId": "BTC-USDT",
                "bidPx": "63500.1",
                "bidSz": "1.5",
                "askPx": "63500.2",
                "askSz": "2.0",
            }
        ],
    }
    out = _parse_okx(msg)
    assert out == [("BTCUSDT", 63500.1, 1.5, 63500.2, 2.0)]


def test_okx_spot_skips_missing_prices():
    msg = {"data": [{"instId": "X-USDT", "bidPx": "", "askPx": ""}]}
    assert _parse_okx(msg) is None


# --------------------------------------------------------------- Gate.io spot


def test_gateio_spot_parses_update():
    msg = {
        "channel": "spot.book_ticker",
        "event": "update",
        "result": {
            "s": "BTC_USDT",
            "b": "63533.8",
            "B": "0.62",
            "a": "63533.9",
            "A": "0.30",
        },
    }
    out = _parse_gateio_spot(msg)
    assert out == [("BTCUSDT", 63533.8, 0.62, 63533.9, 0.30)]


def test_gateio_spot_ignores_futures_channel():
    # spot-парсер не должен реагировать на futures-канал
    msg = {
        "channel": "futures.book_ticker",
        "event": "update",
        "result": {"s": "BTC_USDT", "b": "1", "a": "2"},
    }
    assert _parse_gateio_spot(msg) is None


def test_gateio_spot_ignores_non_update_event():
    msg = {"channel": "spot.book_ticker", "event": "subscribe", "result": {}}
    assert _parse_gateio_spot(msg) is None


# ------------------------------------------------------------------ HTX spot


def test_htx_spot_parses_scalar_bbo():
    # spot: bid/ask — скаляры, bidSize/askSize — объёмы
    msg = {
        "ch": "market.btcusdt.bbo",
        "tick": {
            "bid": 63523.19,
            "bidSize": 1.24,
            "ask": 63523.2,
            "askSize": 0.70,
        },
    }
    out = _parse_htx_spot(msg)
    assert out == [("BTCUSDT", 63523.19, 1.24, 63523.2, 0.70)]


def test_htx_spot_rejects_non_bbo_channel():
    msg = {"ch": "market.btcusdt.depth", "tick": {"bid": 1, "ask": 2}}
    assert _parse_htx_spot(msg) is None


def test_htx_spot_rejects_missing_prices():
    msg = {"ch": "market.btcusdt.bbo", "tick": {"bidSize": 1, "askSize": 2}}
    assert _parse_htx_spot(msg) is None


# ------------------------------------------------------------------ registry


def test_spot_feeds_registered_for_multi_market_exchanges():
    assert set(_spot_feeds) == {"okx", "gateio", "htx"}
    for feed in _spot_feeds.values():
        assert feed.subscribe is not None
