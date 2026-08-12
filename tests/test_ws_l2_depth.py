"""Тесты сборки L2-книги OKX/Bybit (чистые функции, без сети)."""

from __future__ import annotations

import pytest

from mexc_monitor import ws_l2_depth as l2


@pytest.fixture(autouse=True)
def _reset_state():
    """Изолируем глобальное состояние книг/watchlist между тестами."""
    for st in l2._states.values():
        with st.lock:
            st.books.clear()
            st.watchlist_seen.clear()
            st.active_symbols = ()
    # подставляем известный ctVal, чтобы не ходить в сеть
    with l2._okx_ctval_lock:
        l2._okx_ctval.clear()
        l2._okx_ctval.update({"BTC-USDT-SWAP": 0.01, "ETH-USDT-SWAP": 0.1})
        l2._okx_ctval_ts = 1e18  # «свежий» навсегда в рамках теста
    yield


# ── symbol helpers ─────────────────────────────────────────────────────────
def test_canonical_symbol():
    assert l2.canonical_symbol("BTC-USDT-SWAP") == "BTCUSDT"
    assert l2.canonical_symbol("btc_usdt") == "BTCUSDT"
    assert l2.canonical_symbol("BTCUSDT") == "BTCUSDT"


def test_split_base_quote():
    assert l2.split_base_quote("BTCUSDT") == ("BTC", "USDT")
    assert l2.split_base_quote("ETHUSDC") == ("ETH", "USDC")
    assert l2.split_base_quote("XAUUSD") == ("XAU", "USD")
    assert l2.split_base_quote("GARBAGE") is None


def test_okx_native_mapping():
    assert l2._okx_to_native("BTCUSDT") == "BTC-USDT-SWAP"
    assert l2._okx_from_native("BTC-USDT-SWAP") == "BTCUSDT"


# ── parsers ──────────────────────────────────────────────────────────────
def test_parse_okx_snapshot_and_update():
    snap = {
        "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
        "action": "snapshot",
        "data": [{"bids": [["100", "5", "0", "2"]], "asks": [["101", "3", "0", "1"]]}],
    }
    parsed = l2._parse_okx(snap)
    assert parsed == [("BTCUSDT", "snapshot", [(100.0, 5.0)], [(101.0, 3.0)])]

    upd = {
        "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
        "action": "update",
        "data": [{"bids": [["100", "0", "0", "0"]], "asks": []}],  # qty 0 => удаление
    }
    parsed = l2._parse_okx(upd)
    assert parsed[0][1] == "delta"
    assert parsed[0][2] == [(100.0, 0.0)]


def test_parse_okx_ignores_other_channels():
    assert l2._parse_okx({"arg": {"channel": "tickers"}, "data": []}) == []


def test_parse_bybit_snapshot_and_delta():
    snap = {
        "topic": "orderbook.50.BTCUSDT",
        "type": "snapshot",
        "data": {"s": "BTCUSDT", "b": [["100", "5"]], "a": [["101", "3"]]},
    }
    parsed = l2._parse_bybit(snap)
    assert parsed == [("BTCUSDT", "snapshot", [(100.0, 5.0)], [(101.0, 3.0)])]

    delta = {
        "topic": "orderbook.50.BTCUSDT",
        "type": "delta",
        "data": {"s": "BTCUSDT", "b": [["100", "0"]], "a": [["102", "4"]]},
    }
    parsed = l2._parse_bybit(delta)
    assert parsed[0][1] == "delta"


# ── reassembly: snapshot + delta ───────────────────────────────────────────
def test_reassembly_apply_snapshot_then_delta():
    # snapshot
    l2._apply_parsed(
        "bybit",
        [("BTCUSDT", "snapshot", [(100.0, 5.0), (99.0, 2.0)], [(101.0, 3.0)])],
    )
    book = l2.get_fresh_depth_book("bybit", "BTCUSDT", max_age_sec=60)
    assert [b["price"] for b in book["bids"]] == [100.0, 99.0]  # отсортировано убыв.
    assert book["best_bid"] == 100.0 and book["best_ask"] == 101.0

    # delta: удаляем 100, добавляем 100.5, меняем 99
    l2._apply_parsed(
        "bybit",
        [("BTCUSDT", "delta", [(100.0, 0.0), (100.5, 1.0), (99.0, 7.0)], [])],
    )
    book = l2.get_fresh_depth_book("bybit", "BTCUSDT", max_age_sec=60)
    prices = {b["price"]: b["qty"] for b in book["bids"]}
    assert 100.0 not in prices  # удалён
    assert prices[100.5] == 1.0  # добавлен
    assert prices[99.0] == 7.0  # обновлён
    assert book["best_bid"] == 100.5


def test_delta_before_snapshot_starts_fresh_book():
    # дельта без предшествующего снапшота трактуется как начало книги
    l2._apply_parsed("bybit", [("BTCUSDT", "delta", [(100.0, 5.0)], [(101.0, 2.0)])])
    book = l2.get_fresh_depth_book("bybit", "BTCUSDT", max_age_sec=60)
    assert book is not None
    assert book["best_bid"] == 100.0


# ── notional / multiplier ──────────────────────────────────────────────────
def test_bybit_notional_direct_base_coin():
    l2._apply_parsed("bybit", [("BTCUSDT", "snapshot", [(100.0, 5.0)], [(101.0, 3.0)])])
    book = l2.get_fresh_depth_book("bybit", "BTCUSDT", max_age_sec=60)
    assert book["multiplier"] == 1.0
    assert book["bids"][0]["notional"] == pytest.approx(500.0)  # 100 * 5 * 1


def test_okx_notional_uses_ctval():
    # OKX size в контрактах; ctVal(BTC)=0.01 => notional = price*size*0.01
    l2._apply_parsed("okx", [("BTCUSDT", "snapshot", [(100.0, 50.0)], [(101.0, 30.0)])])
    book = l2.get_fresh_depth_book("okx", "BTCUSDT", max_age_sec=60)
    assert book["multiplier"] == 0.01
    assert book["bids"][0]["qty"] == pytest.approx(0.5)  # 50 контрактов * 0.01
    assert book["bids"][0]["notional"] == pytest.approx(50.0)  # 100 * 50 * 0.01


# ── freshness ──────────────────────────────────────────────────────────────
def test_stale_book_returns_none():
    l2._apply_parsed("bybit", [("BTCUSDT", "snapshot", [(100.0, 5.0)], [(101.0, 3.0)])])
    # искусственно состарим
    with l2._states["bybit"].lock:
        l2._states["bybit"].books["BTCUSDT"]["ts"] -= 100.0
    assert l2.get_fresh_depth_book("bybit", "BTCUSDT", max_age_sec=8.0) is None


def test_unknown_exchange_returns_none():
    assert l2.get_fresh_depth_book("kraken", "BTCUSDT", max_age_sec=8.0) is None


def test_one_sided_book_returns_none():
    l2._apply_parsed("bybit", [("BTCUSDT", "snapshot", [(100.0, 5.0)], [])])
    assert l2.get_fresh_depth_book("bybit", "BTCUSDT", max_age_sec=60) is None


# ── watchlist ──────────────────────────────────────────────────────────────
def test_watchlist_filters_invalid_symbols():
    l2.touch_watchlist("okx", ["BTCUSDT", "ETHUSDT", "GARBAGE", ""])
    desired = l2._desired_symbols("okx")
    assert "BTCUSDT" in desired and "ETHUSDT" in desired
    assert "GARBAGE" not in desired  # не парсится в base/quote


def test_health_reports_tracked_and_fresh():
    l2._apply_parsed("okx", [("BTCUSDT", "snapshot", [(100.0, 5.0)], [(101.0, 3.0)])])
    h = l2.l2_depth_health(max_age_sec=60)
    assert h["okx"]["tracked"] == 1
    assert h["okx"]["fresh"] == 1
    assert h["okx"]["live"] is True
    assert h["bybit"]["tracked"] == 0
