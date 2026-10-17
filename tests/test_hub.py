"""MarketHub: символы, разбор ответов бирж, сборка среза, свечи, OI."""

from __future__ import annotations

import time

import pytest

from mexc_monitor.hub import candles as C
from mexc_monitor.hub import venues as V
from mexc_monitor.hub.hub import MarketHub
from mexc_monitor.hub.models import Candle, InstrumentMeta, PerpStats
from mexc_monitor.hub.symbols import norm_key, parse_symbol, split_multiplier


# ───────────────────────────────────────────────────────── symbols


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("BTCUSDT", ("BTC", "USDT", 1.0)),
        ("BTC-USDT-SWAP", ("BTC", "USDT", 1.0)),
        ("BTC_USDT", ("BTC", "USDT", 1.0)),
        ("btcusdt", ("BTC", "USDT", 1.0)),
        ("1000PEPEUSDT", ("PEPE", "USDT", 1000.0)),
        ("1000000MOGUSDT", ("MOG", "USDT", 1_000_000.0)),
        ("1MBABYDOGEUSDT", ("BABYDOGE", "USDT", 1_000_000.0)),
        ("1INCHUSDT", ("1INCH", "USDT", 1.0)),
        ("ETH-USD", ("ETH", "USD", 1.0)),
    ],
)
def test_parse_symbol(raw, expected):
    assert parse_symbol(raw) == expected


def test_hyperliquid_k_prefix():
    assert norm_key("kPEPE") == "kPEPE"
    assert split_multiplier("kPEPE") == ("PEPE", 1000.0)
    assert split_multiplier("KAVA") == ("KAVA", 1.0)


def test_unknown_quote_is_rejected():
    assert parse_symbol("BTCEUR") is None
    assert parse_symbol("BTC", default_quote="USDC") == ("BTC", "USDC", 1.0)


# ───────────────────────────────────────────────────────── venue parsers


def test_binance_exchange_info_filters_perpetual_usdt():
    raw = {
        "symbols": [
            {"symbol": "BTCUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING"},
            {"symbol": "ETHUSDT_250926", "contractType": "CURRENT_QUARTER", "quoteAsset": "USDT", "status": "TRADING"},
            {"symbol": "BTCUSDC", "contractType": "PERPETUAL", "quoteAsset": "USDC", "status": "TRADING"},
            {"symbol": "1000PEPEUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "SETTLING"},
        ]
    }
    metas = V.parse_binance_exchange_info("binance", raw, {"BTCUSDT": 4.0})
    assert [m.key for m in metas] == ["BTCUSDT", "1000PEPEUSDT"]
    assert metas[0].funding_interval_h == 4.0 and metas[0].active
    assert metas[1].price_mult == 1000.0 and not metas[1].active


def test_binance_premium_and_klines():
    stats = V.parse_binance_premium(
        [{"symbol": "BTCUSDT", "markPrice": "65000.1", "lastFundingRate": "0.0001", "nextFundingTime": 1760000000000}]
    )
    assert stats["BTCUSDT"].funding_rate == pytest.approx(0.0001)
    assert stats["BTCUSDT"].next_funding_ms == 1760000000000
    kl = V.parse_binance_klines([[1000, "1", "2", "0.5", "1.5", "10", 1999, "15", 5, "1", "1", "0"]])
    assert kl[0].t == 1000 and kl[0].h == 2.0 and kl[0].qv == 15.0


def test_bybit_instruments_and_stats():
    raw = {
        "result": {
            "list": [
                {"symbol": "BTCUSDT", "contractType": "LinearPerpetual", "quoteCoin": "USDT", "status": "Trading", "fundingInterval": 480},
                {"symbol": "BTC-26DEC25", "contractType": "LinearFutures", "quoteCoin": "USDT", "status": "Trading"},
            ]
        }
    }
    metas = V.parse_bybit_instruments(raw)
    assert len(metas) == 1 and metas[0].funding_interval_h == 8.0
    stats = V.parse_bybit_tickers_stats(
        {"result": {"list": [{"symbol": "BTCUSDT", "fundingRate": "-0.0002", "nextFundingTime": "1760000000000", "openInterestValue": "5e9", "turnover24h": "1e10", "markPrice": "65000"}]}}
    )
    st = stats["BTCUSDT"]
    assert st.funding_rate == pytest.approx(-0.0002) and st.open_interest_usd == 5e9 and st.volume_24h_usd == 1e10


def test_bybit_klines_sorted_ascending():
    raw = {"result": {"list": [["2000", "2", "3", "1", "2.5", "5", "12"], ["1000", "1", "2", "0.5", "2", "4", "8"]]}}
    kl = V.parse_bybit_klines(raw)
    assert [c.t for c in kl] == [1000, 2000]


def test_okx_instruments_contract_size_and_funding_interval():
    metas = V.parse_okx_instruments(
        {"data": [
            {"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "settleCcy": "USDT", "state": "live", "ctType": "linear"},
            {"instId": "BTC-USD-SWAP", "ctVal": "100", "settleCcy": "BTC", "state": "live", "ctType": "inverse"},
        ]}
    )
    assert len(metas) == 1
    assert metas[0].key == "BTCUSDT" and metas[0].native == "BTC-USDT-SWAP" and metas[0].contract_size == 0.01
    f = V.parse_okx_funding({"data": [{"instId": "BTC-USDT-SWAP", "fundingRate": "0.0003", "fundingTime": "1760000000000", "nextFundingTime": "1760014400000"}]})
    rate, nxt, interval = f["BTCUSDT"]
    assert rate == pytest.approx(0.0003) and nxt == 1760000000000 and interval == pytest.approx(4.0)


def test_gate_contracts_multiplier_and_funding():
    metas, stats = V.parse_gate_contracts(
        [{"name": "BTC_USDT", "quanto_multiplier": "0.0001", "funding_rate": "0.0001", "funding_interval": 28800,
          "funding_next_apply": 1760000000, "in_delisting": False, "mark_price": "65000"}]
    )
    assert metas[0].contract_size == 0.0001 and metas[0].funding_interval_h == 8.0
    assert stats["BTCUSDT"].next_funding_ms == 1760000000000


def test_bitget_tickers_oi_in_usd():
    stats = V.parse_bitget_tickers({"data": [{"symbol": "BTCUSDT", "fundingRate": "0.0001", "holdingAmount": "100", "usdtVolume": "1e9", "markPrice": "65000"}]})
    assert stats["BTCUSDT"].open_interest_usd == pytest.approx(6.5e6)


def test_htx_contracts_and_spot():
    metas = V.parse_htx_contracts({"data": [{"contract_code": "BTC-USDT", "contract_size": 0.001, "contract_status": 1, "business_type": "swap"}]})
    assert metas[0].key == "BTCUSDT" and metas[0].contract_size == 0.001
    spot = V.parse_htx_spot({"data": [{"symbol": "btcusdt", "bid": 100.0, "bidSize": 1.5, "ask": 100.5, "askSize": 2.0, "vol": 1e8}]})
    assert spot["BTCUSDT"] == (100.0, 1.5, 100.5, 2.0, 1e8)


def test_mexc_ticker_without_l1_qty():
    meta = {"BTCUSDT": InstrumentMeta("mexc", "BTCUSDT", "BTC_USDT", "BTC", "USDT", contract_size=0.0001)}
    book, stats = V.parse_mexc_ticker(
        {"data": [{"symbol": "BTC_USDT", "bid1": 64999.5, "ask1": 65000.0, "fundingRate": 0.0001, "holdVol": 1e6, "amount24": 2e9, "fairPrice": 65000}]},
        meta,
    )
    assert book["BTCUSDT"] == (64999.5, None, 65000.0, None)
    assert stats["BTCUSDT"].open_interest_usd == pytest.approx(1e6 * 0.0001 * 65000)


def test_mexc_klines_columnar_seconds():
    raw = {"data": {"time": [60, 120], "open": [1, 2], "high": [2, 3], "low": [0.5, 1], "close": [2, 2.5], "vol": [10, 20], "amount": [15, 45]}}
    kl = V.parse_mexc_klines(raw)
    assert [c.t for c in kl] == [60_000, 120_000] and kl[1].qv == 45


def test_hyperliquid_impact_quotes_are_indicative_and_qty_unknown():
    raw = [
        {"universe": [{"name": "BTC", "szDecimals": 5}, {"name": "kPEPE", "szDecimals": 0}, {"name": "OLD", "isDelisted": True}]},
        [
            {"funding": "0.0000125", "openInterest": "100", "dayNtlVlm": "1e9", "markPx": "65000", "impactPxs": ["64990", "65010"]},
            {"funding": "0.00002", "openInterest": "1e9", "dayNtlVlm": "1e7", "markPx": "0.012", "impactPxs": ["0.0119", "0.0121"]},
            {"funding": "0", "openInterest": "0", "dayNtlVlm": "0", "markPx": "1", "impactPxs": None},
        ],
    ]
    metas, book, stats = V.parse_hyperliquid(raw)
    assert {m.key: (m.base, m.price_mult) for m in metas} == {"BTC": ("BTC", 1.0), "kPEPE": ("PEPE", 1000.0), "OLD": ("OLD", 1.0)}
    assert not [m for m in metas if m.key == "OLD"][0].active
    assert book["BTC"] == (64990.0, None, 65010.0, None)
    assert stats["BTC"].open_interest_usd == pytest.approx(6.5e6)


# ───────────────────────────────────────────────────────── hub view


class FakeVenue(V.Venue):
    def __init__(self, name, *, meta=None, stats=None, book=None, spot=None, contract=False, rest=True):
        self.name = name
        self.has_spot = spot is not None
        self.perp_rest_interval = 1.0 if rest else 0.0
        self._meta, self._stats, self._book, self._spot = meta, stats, book, spot

    def meta(self):
        return self._meta

    def stats(self, meta):
        return self._stats

    def perp_rest(self):
        return self._book

    def spot(self):
        return self._spot


def _hub_with(*venues: FakeVenue) -> MarketHub:
    hub = MarketHub({v.name: v for v in venues}, kline_venues=[])
    for v in venues:
        hub._run_meta(v)
        hub._run_stats(v)
        hub._run_perp_rest(v)
        if v.has_spot:
            hub._run_spot(v)
    return hub


def test_view_converts_contracts_and_price_multiplier():
    okx = FakeVenue(
        "okx",
        meta=[InstrumentMeta("okx", "PEPEUSDT", "PEPE-USDT-SWAP", "PEPE", "USDT", contract_size=10_000_000)],
        stats={"PEPEUSDT": PerpStats(funding_rate=0.0004, funding_interval_h=4.0, volume_24h_usd=5e7)},
        book={"PEPEUSDT": (0.00001, 3.0, 0.0000101, 4.0)},
    )
    binance = FakeVenue(
        "binance",
        meta=[InstrumentMeta("binance", "1000PEPEUSDT", "1000PEPEUSDT", "PEPE", "USDT", price_mult=1000.0)],
        stats={},
        book={"1000PEPEUSDT": (0.0102, 50_000.0, 0.01021, 60_000.0)},
    )
    view = _hub_with(okx, binance).view()
    by_venue = {q.venue: q for q in view.perps["PEPE"]}
    o, b = by_venue["okx"], by_venue["binance"]
    assert o.bid_qty == pytest.approx(30_000_000) and o.funding_per_hour == pytest.approx(0.0001)
    assert b.bid == pytest.approx(0.0000102) and b.bid_qty == pytest.approx(50_000_000)
    assert b.native == "1000PEPEUSDT"


def test_view_skips_inactive_and_unknown_when_meta_loaded():
    v = FakeVenue(
        "bybit",
        meta=[InstrumentMeta("bybit", "AAAUSDT", "AAAUSDT", "AAA", "USDT", active=False)],
        stats={},
        book={"AAAUSDT": (1.0, 1.0, 1.01, 1.0), "BBBUSDT": (2.0, 1.0, 2.01, 1.0)},
    )
    assert _hub_with(v).view().perps == {}


def test_view_fallback_meta_hides_contract_qty():
    v = FakeVenue("gateio", meta=None, stats={}, book={"BTCUSDT": (100.0, 7.0, 100.1, 8.0)})
    q = _hub_with(v).view().perps["BTC"][0]
    assert q.bid == 100.0 and q.bid_qty is None


def test_view_spot_quotes():
    v = FakeVenue("bybit", meta=[], stats={}, book={}, spot={"ETHUSDT": (3000.0, 2.0, 3000.5, 1.0, 1e9), "ETHBTC": (0.05, 1, 0.051, 1, None)})
    spots = _hub_with(v).view().spots
    assert list(spots) == ["ETH"] and spots["ETH"][0].ask_notional == pytest.approx(3000.5)


def test_stale_rest_book_is_dropped():
    v = FakeVenue("mexc", meta=None, stats={}, book={"BTCUSDT": (100.0, None, 100.1, None)})
    hub = _hub_with(v)
    ts, book = hub.rest_books["mexc"]
    hub.rest_books["mexc"] = (ts - 3600, book)
    assert hub.view().perps == {}


def test_oi_change_window():
    hub = MarketHub({}, kline_venues=[])
    now = time.time()
    from collections import deque

    hub.oi_hist[("bybit", "BTCUSDT")] = deque([(now - 3600, 100.0), (now - 1800, 110.0), (now, 125.0)])
    assert hub.oi_change("bybit", "BTCUSDT", 3600) == pytest.approx(0.25)
    assert hub.oi_change("bybit", "BTCUSDT", 7200) is None  # истории меньше окна


def test_job_failure_backoff_and_health():
    hub = MarketHub({}, kline_venues=[])
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("down")

    from mexc_monitor.hub.hub import _Job

    job = _Job("x:stats", 10.0, boom)
    hub._exec(job)
    hub._exec(job)
    h = hub.health["x:stats"]
    assert not h.ok and h.consecutive_failures == 2 and "down" in h.last_error
    assert job.next_mono - time.monotonic() > 30  # экспоненциальный backoff


# ───────────────────────────────────────────────────────── candles


def test_candle_series_closes_previous_and_merges_history():
    s = C.CandleSeries(maxlen=10)
    s.apply(Candle(t=120_000, o=1, h=1, l=1, c=1, v=1, closed=False))
    s.apply(Candle(t=120_000, o=1, h=2, l=1, c=2, v=3, closed=False))
    s.apply(Candle(t=180_000, o=2, h=2, l=2, c=2, v=1, closed=False))
    assert [c.t for c in s.candles] == [120_000, 180_000]
    assert s.candles[0].closed and s.candles[0].v == 3
    s.merge_history([Candle(t=60_000, o=1, h=1, l=1, c=1, v=1), Candle(t=120_000, o=9, h=9, l=9, c=9, v=9)])
    assert [c.t for c in s.candles] == [60_000, 120_000, 180_000]
    assert s.candles[1].v == 3  # живая свеча приоритетнее REST-истории


def test_kline_ws_parsers():
    b = C.parse_binance_kline_msg({"stream": "btcusdt@kline_1m", "data": {"e": "kline", "s": "BTCUSDT", "k": {"t": 60000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "10", "q": "15", "x": True}}})
    assert b[0][0] == "BTCUSDT" and b[0][1].closed
    y = C.parse_bybit_kline_msg({"topic": "kline.1.BTCUSDT", "data": [{"start": 60000, "open": "1", "high": "2", "low": "0.5", "close": "1.5", "volume": "10", "turnover": "15", "confirm": False}]})
    assert y[0][0] == "BTCUSDT" and not y[0][1].closed
    m = C.parse_mexc_kline_msg({"channel": "push.kline", "data": {"symbol": "BTC_USDT", "t": 60, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "q": 10, "a": 15}})
    assert m[0][0] == "BTCUSDT" and m[0][1].t == 60_000


def test_build_kline_workers_chunks():
    natives = [f"S{i}USDT" for i in range(320)]
    ws = C.build_kline_workers("binance", natives, lambda k, c: None)
    assert len(ws) == 3 and "s0usdt@kline_1m" in ws[0].url
    ws = C.build_kline_workers("bybit", natives[:25], lambda k, c: None)
    assert len(ws) == 1 and len(ws[0].subscribe) == 3
