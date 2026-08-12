"""Тесты WS L2-книги фьючерсов (mexc_monitor.ws_futures_depth_book).

Покрывают чистую логику без сети: разбор уровней push.depth.full, применение
снапшота в буфер, свежесть, конверсию contractSize → USDT-notional и декод
gzip/plain WS-фреймов.
"""

from __future__ import annotations

import gzip
import json
import time

import pytest

from mexc_monitor import ws_futures_depth_book as db


@pytest.fixture(autouse=True)
def _clean_state():
    """Изолируем глобальный буфер книг и кэш размеров между тестами."""
    with db._lock:
        db._books.clear()
    with db._contract_sizes_lock:
        db._contract_sizes.clear()
        db._contract_sizes_ts = 0.0
    yield
    with db._lock:
        db._books.clear()
    with db._contract_sizes_lock:
        db._contract_sizes.clear()
        db._contract_sizes_ts = 0.0


def _seed_contract_size(sym: str, size: float) -> None:
    with db._contract_sizes_lock:
        db._contract_sizes[db._norm_futures_symbol(sym)] = size
        db._contract_sizes_ts = time.monotonic()


# ── _parse_levels ──────────────────────────────────────────────────────────


def test_parse_levels_extracts_price_and_volume():
    # Формат MEXC: [price, volume, order_count] → берём индексы 0 и 1.
    raw = [[100.0, 5.0, 3], [99.5, 2.0, 1]]
    assert db._parse_levels(raw) == [(100.0, 5.0), (99.5, 2.0)]


def test_parse_levels_drops_zero_and_malformed():
    raw = [[100.0, 0.0, 1], [0.0, 5.0, 1], ["x", "y", 1], [101.0], [102.0, 3.0, 2]]
    assert db._parse_levels(raw) == [(102.0, 3.0)]


def test_parse_levels_non_list_returns_empty():
    assert db._parse_levels(None) == []
    assert db._parse_levels({"bids": []}) == []


# ── _apply_full_depth ────────────────────────────────────────────────────────


def _full_msg(sym: str, bids, asks, version=1):
    return {
        "channel": "push.depth.full",
        "symbol": sym,
        "data": {"bids": bids, "asks": asks, "version": version},
    }


def test_apply_full_depth_stores_snapshot():
    db._apply_full_depth(_full_msg("BTC_USDT", [[100.0, 5.0, 1]], [[101.0, 4.0, 1]]))
    with db._lock:
        assert "BTC_USDT" in db._books
        version, _, bids, asks = db._books["BTC_USDT"]
    assert version == 1
    assert bids == [(100.0, 5.0)]
    assert asks == [(101.0, 4.0)]


def test_apply_full_depth_ignores_wrong_channel():
    db._apply_full_depth(
        {"channel": "push.depth", "symbol": "BTC_USDT", "data": {"bids": [], "asks": []}}
    )
    with db._lock:
        assert "BTC_USDT" not in db._books


def test_apply_full_depth_ignores_empty_sides():
    # Полный снапшот с пустой стороной бесполезен для density — пропускаем.
    db._apply_full_depth(_full_msg("ETH_USDT", [[10.0, 1.0, 1]], []))
    with db._lock:
        assert "ETH_USDT" not in db._books


def test_apply_full_depth_replaces_prior_snapshot():
    db._apply_full_depth(_full_msg("BTC_USDT", [[100.0, 5.0, 1]], [[101.0, 4.0, 1]], 1))
    db._apply_full_depth(_full_msg("BTC_USDT", [[100.5, 9.0, 1]], [[101.5, 8.0, 1]], 2))
    with db._lock:
        version, _, bids, _ = db._books["BTC_USDT"]
    assert version == 2
    assert bids == [(100.5, 9.0)]  # полная замена, не мёрдж


# ── get_fresh_depth_book + contractSize ──────────────────────────────────────


def test_get_fresh_depth_book_applies_contract_size():
    _seed_contract_size("BTC_USDT", 0.0001)  # 1 контракт = 0.0001 BTC
    db._apply_full_depth(_full_msg("BTC_USDT", [[100000.0, 200.0, 1]], [[100010.0, 100.0, 1]]))
    book = db.get_fresh_depth_book("BTC_USDT", max_age_sec=5.0)
    assert book is not None
    # qty в базовом активе: 200 контрактов × 0.0001 = 0.02 BTC
    assert book["bids"][0]["qty"] == pytest.approx(0.02)
    # notional в USDT: 100000 × 0.02 = 2000
    assert book["bids"][0]["notional"] == pytest.approx(2000.0)
    assert book["contract_size"] == 0.0001
    assert book["source"] == "ws"
    assert book["market"] == "futures"


def test_get_fresh_depth_book_defaults_contract_size_to_one():
    # Размер неизвестен → cs=1.0, notional = price × volume.
    db._apply_full_depth(_full_msg("NEW_USDT", [[2.0, 50.0, 1]], [[2.1, 40.0, 1]]))
    book = db.get_fresh_depth_book("NEW_USDT", max_age_sec=5.0)
    assert book["contract_size"] == 1.0
    assert book["bids"][0]["notional"] == pytest.approx(100.0)


def test_get_fresh_depth_book_stale_returns_none():
    _seed_contract_size("BTC_USDT", 1.0)
    db._apply_full_depth(_full_msg("BTC_USDT", [[100.0, 5.0, 1]], [[101.0, 4.0, 1]]))
    # Искусственно состарим запись.
    with db._lock:
        version, _, bids, asks = db._books["BTC_USDT"]
        db._books["BTC_USDT"] = (version, time.monotonic() - 100.0, bids, asks)
    assert db.get_fresh_depth_book("BTC_USDT", max_age_sec=5.0) is None


def test_get_fresh_depth_book_unknown_symbol_returns_none():
    assert db.get_fresh_depth_book("ZZZ_USDT", max_age_sec=5.0) is None


def test_get_fresh_depth_book_mid_and_best():
    _seed_contract_size("BTC_USDT", 1.0)
    db._apply_full_depth(_full_msg("BTC_USDT", [[100.0, 5.0, 1]], [[102.0, 4.0, 1]]))
    book = db.get_fresh_depth_book("BTC_USDT", max_age_sec=5.0)
    assert book["best_bid"] == 100.0
    assert book["best_ask"] == 102.0
    assert book["mid"] == pytest.approx(101.0)


# ── _decode_ws_payload ───────────────────────────────────────────────────────


def test_decode_plain_text():
    obj = db._decode_ws_payload(json.dumps({"channel": "pong"}))
    assert obj == {"channel": "pong"}


def test_decode_gzip_bytes():
    blob = gzip.compress(json.dumps({"channel": "push.depth.full"}).encode())
    assert db._decode_ws_payload(blob) == {"channel": "push.depth.full"}


def test_decode_invalid_returns_none():
    assert db._decode_ws_payload("not json") is None
    assert db._decode_ws_payload(12345) is None
    assert db._decode_ws_payload(json.dumps([1, 2, 3])) is None  # не dict


# ── health ───────────────────────────────────────────────────────────────────


def test_depth_book_health_reports_fresh():
    db._apply_full_depth(_full_msg("BTC_USDT", [[100.0, 5.0, 1]], [[101.0, 4.0, 1]]))
    h = db.depth_book_health(max_age_sec=5.0)
    assert h["tracked"] == 1
    assert h["fresh"] == 1
    assert h["live"] is True
    assert h["symbols"]["BTC_USDT"]["bid_levels"] == 1
