"""Адаптеры бирж для MarketHub.

У каждой биржи — набор независимых REST-задач (справочник, funding/OI/объём,
REST-котировки perp, спот, свечи). Задачи запускает планировщик хаба; ошибка
одной задачи не мешает остальным. Разбор ответов вынесен в функции
``parse_*`` — они чистые и покрыты тестами на образцах ответов API.

Ключи инструментов везде — ``symbols.norm_key`` (``BTCUSDT``), они же ключи
WS-фидов из ``mexc_monitor.ws_bookticker``. Количества в котировках — в
единицах стакана биржи (контракты), перевод в монеты делает хаб по
``InstrumentMeta.qty_to_base``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from mexc_monitor.hub.http import fnum, get_json, inum, post_json
from mexc_monitor.hub.models import Candle, InstrumentMeta, PerpStats
from mexc_monitor.hub.symbols import norm_key, parse_symbol, split_multiplier

logger = logging.getLogger(__name__)

# key -> (bid, bid_qty, ask, ask_qty); qty в единицах стакана, None — неизвестно
Book = dict[str, tuple[float, float | None, float, float | None]]
# key -> (bid, bid_qty, ask, ask_qty, volume_24h_usd)
SpotBook = dict[str, tuple[float, float | None, float, float | None, float | None]]


def _meta(
    venue: str,
    native: str,
    *,
    contract_size: float = 1.0,
    active: bool = True,
    funding_interval_h: float | None = None,
    default_quote: str | None = None,
    quote_filter: tuple[str, ...] = ("USDT",),
) -> InstrumentMeta | None:
    parsed = parse_symbol(native, default_quote=default_quote)
    if parsed is None:
        return None
    base, quote, mult = parsed
    if quote not in quote_filter:
        return None
    return InstrumentMeta(
        venue=venue,
        key=norm_key(native),
        native=native,
        base=base,
        quote=quote,
        price_mult=mult,
        contract_size=contract_size if contract_size > 0 else 1.0,
        active=active,
        funding_interval_h=funding_interval_h,
    )


def _book_entry(bid: Any, bq: Any, ask: Any, aq: Any):
    b, a = fnum(bid), fnum(ask)
    if not b or not a or b <= 0 or a <= 0 or a < b:
        return None
    return (b, fnum(bq), a, fnum(aq))


def _items(raw: Any, *path: str) -> list:
    cur = raw
    for p in path:
        if not isinstance(cur, dict):
            return []
        cur = cur.get(p)
    return cur if isinstance(cur, list) else []


class Venue:
    """Базовый адаптер. Методы возвращают None, если биржа это не умеет."""

    name: str = ""
    quote_ccy: str = "USDT"
    has_spot: bool = False
    # Интервалы опроса, сек
    meta_interval: float = 1800.0
    stats_interval: float = 30.0
    perp_rest_interval: float = 0.0  # 0 — котировки perp идут из WS
    spot_interval: float = 3.0

    def meta(self) -> list[InstrumentMeta] | None:
        return None

    def stats(self, meta: dict[str, InstrumentMeta]) -> dict[str, PerpStats] | None:
        return None

    def perp_rest(self) -> Book | None:
        return None

    def spot(self) -> SpotBook | None:
        return None

    def klines(self, meta: InstrumentMeta, limit: int) -> list[Candle]:
        return []

    def ws_book(self) -> tuple[float | None, Book] | None:
        """(возраст последнего сообщения, книга) из WS-фида или None."""
        return None

    def ensure_ws(self) -> None:
        return None


# ═══════════════════════════════════════════════════════════════ Binance-like


def parse_binance_exchange_info(venue: str, raw: Any, intervals: dict[str, float]) -> list[InstrumentMeta]:
    out: list[InstrumentMeta] = []
    for s in _items(raw, "symbols"):
        if not isinstance(s, dict):
            continue
        if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT":
            continue
        sym = s.get("symbol")
        if not sym:
            continue
        m = _meta(
            venue,
            sym,
            active=s.get("status") == "TRADING",
            funding_interval_h=intervals.get(sym, 8.0),
        )
        if m is not None:
            out.append(m)
    return out


def parse_binance_premium(raw: Any) -> dict[str, PerpStats]:
    out: dict[str, PerpStats] = {}
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        out[norm_key(it["symbol"])] = PerpStats(
            funding_rate=fnum(it.get("lastFundingRate")),
            next_funding_ms=inum(it.get("nextFundingTime")) or None,
            mark_price=fnum(it.get("markPrice")),
        )
    return out


def parse_binance_book_ticker(raw: Any) -> Book:
    out: Book = {}
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        e = _book_entry(it.get("bidPrice"), it.get("bidQty"), it.get("askPrice"), it.get("askQty"))
        if e is not None:
            out[norm_key(it["symbol"])] = e
    return out


def parse_binance_klines(raw: Any) -> list[Candle]:
    out: list[Candle] = []
    for k in raw if isinstance(raw, list) else []:
        if not isinstance(k, list) or len(k) < 8:
            continue
        o, h, l, c, v = (fnum(k[i]) for i in range(1, 6))
        if None in (o, h, l, c, v):
            continue
        out.append(Candle(t=int(k[0]), o=o, h=h, l=l, c=c, v=v, qv=fnum(k[7]) or 0.0))
    return out


class BinanceLike(Venue):
    """USDT-M perp Binance и совместимые API (Aster)."""

    def __init__(self, name: str, fapi: str, spot_api: str | None, feed_name: str | None):
        self.name = name
        self._fapi = fapi
        self._spot_api = spot_api
        self._feed_name = feed_name
        self.has_spot = spot_api is not None
        self.perp_rest_interval = 0.0 if feed_name else 2.0
        self._oi_cursor = 0
        self._oi_cache: dict[str, float] = {}

    def meta(self) -> list[InstrumentMeta]:
        intervals: dict[str, float] = {}
        try:
            for it in get_json(f"{self._fapi}/fapi/v1/fundingInfo") or []:
                if isinstance(it, dict) and it.get("symbol"):
                    h = fnum(it.get("fundingIntervalHours"))
                    if h:
                        intervals[it["symbol"]] = h
        except Exception as e:  # noqa: BLE001 — интервалы необязательны
            logger.debug("%s fundingInfo failed: %s", self.name, e)
        return parse_binance_exchange_info(self.name, get_json(f"{self._fapi}/fapi/v1/exchangeInfo"), intervals)

    def stats(self, meta: dict[str, InstrumentMeta]) -> dict[str, PerpStats]:
        stats = parse_binance_premium(get_json(f"{self._fapi}/fapi/v1/premiumIndex"))
        vols: dict[str, float] = {}
        for it in get_json(f"{self._fapi}/fapi/v1/ticker/24hr") or []:
            if isinstance(it, dict) and it.get("symbol"):
                v = fnum(it.get("quoteVolume"))
                if v is not None:
                    vols[norm_key(it["symbol"])] = v
        for k, st in stats.items():
            st.volume_24h_usd = vols.get(k)
            m = meta.get(k)
            if m is not None:
                st.funding_interval_h = m.funding_interval_h
        self._refresh_open_interest(stats, vols, meta)
        return stats

    def _refresh_open_interest(self, stats, vols, meta, batch: int = 40, top: int = 200) -> None:
        """OI у Binance отдаётся только по символу — обходим топ по объёму порциями."""
        ranked = sorted((k for k in vols if k in meta), key=lambda k: -vols[k])[:top]
        if ranked:
            start = self._oi_cursor % len(ranked)
            for k in (ranked + ranked)[start : start + min(batch, len(ranked))]:
                try:
                    r = get_json(f"{self._fapi}/fapi/v1/openInterest", {"symbol": meta[k].native}, timeout=5)
                    oi = fnum(r.get("openInterest")) if isinstance(r, dict) else None
                    mark = stats.get(k).mark_price if k in stats else None
                    if oi is not None and mark:
                        self._oi_cache[k] = oi * mark
                except Exception as e:  # noqa: BLE001
                    logger.debug("%s OI %s failed: %s", self.name, k, e)
            self._oi_cursor = start + batch
        for k, v in self._oi_cache.items():
            if k in stats:
                stats[k].open_interest_usd = v

    def perp_rest(self) -> Book | None:
        if self._feed_name:
            return None
        return parse_binance_book_ticker(get_json(f"{self._fapi}/fapi/v1/ticker/bookTicker"))

    def spot(self) -> SpotBook | None:
        if not self._spot_api:
            return None
        book = parse_binance_book_ticker(get_json(f"{self._spot_api}/api/v3/ticker/bookTicker"))
        return {k: (*v, None) for k, v in book.items()}

    def klines(self, meta: InstrumentMeta, limit: int) -> list[Candle]:
        raw = get_json(
            f"{self._fapi}/fapi/v1/klines",
            {"symbol": meta.native, "interval": "1m", "limit": limit},
        )
        return parse_binance_klines(raw)

    def ws_book(self):
        return _feed_book(self._feed_name) if self._feed_name else None

    def ensure_ws(self) -> None:
        if self._feed_name:
            _ensure_feed(self._feed_name)


# ═══════════════════════════════════════════════════════════════ Bybit


def parse_bybit_instruments(raw: Any) -> list[InstrumentMeta]:
    out: list[InstrumentMeta] = []
    for it in _items(raw, "result", "list"):
        if not isinstance(it, dict):
            continue
        if it.get("contractType") != "LinearPerpetual" or it.get("quoteCoin") != "USDT":
            continue
        minutes = fnum(it.get("fundingInterval"))
        m = _meta(
            "bybit",
            it.get("symbol", ""),
            active=it.get("status") == "Trading",
            funding_interval_h=(minutes / 60.0) if minutes else 8.0,
        )
        if m is not None:
            out.append(m)
    return out


def parse_bybit_tickers_stats(raw: Any) -> dict[str, PerpStats]:
    out: dict[str, PerpStats] = {}
    for it in _items(raw, "result", "list"):
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        out[norm_key(it["symbol"])] = PerpStats(
            funding_rate=fnum(it.get("fundingRate")),
            next_funding_ms=inum(it.get("nextFundingTime")) or None,
            open_interest_usd=fnum(it.get("openInterestValue")),
            volume_24h_usd=fnum(it.get("turnover24h")),
            mark_price=fnum(it.get("markPrice")),
        )
    return out


def parse_bybit_spot(raw: Any) -> SpotBook:
    out: SpotBook = {}
    for it in _items(raw, "result", "list"):
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        e = _book_entry(it.get("bid1Price"), it.get("bid1Size"), it.get("ask1Price"), it.get("ask1Size"))
        if e is not None:
            out[norm_key(it["symbol"])] = (*e, fnum(it.get("turnover24h")))
    return out


def parse_bybit_klines(raw: Any) -> list[Candle]:
    out: list[Candle] = []
    for k in _items(raw, "result", "list"):
        if not isinstance(k, list) or len(k) < 7:
            continue
        vals = [fnum(x) for x in k[:7]]
        if None in vals:
            continue
        t, o, h, l, c, v, qv = vals
        out.append(Candle(t=int(t), o=o, h=h, l=l, c=c, v=v, qv=qv))
    out.sort(key=lambda x: x.t)  # Bybit отдаёт от новых к старым
    return out


class Bybit(Venue):
    name = "bybit"
    has_spot = True
    _API = "https://api.bybit.com"

    def meta(self) -> list[InstrumentMeta]:
        out: list[InstrumentMeta] = []
        cursor = ""
        for _ in range(10):
            params = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            raw = get_json(f"{self._API}/v5/market/instruments-info", params)
            out.extend(parse_bybit_instruments(raw))
            cursor = (raw.get("result") or {}).get("nextPageCursor") or "" if isinstance(raw, dict) else ""
            if not cursor:
                break
        return out

    def stats(self, meta):
        stats = parse_bybit_tickers_stats(get_json(f"{self._API}/v5/market/tickers", {"category": "linear"}))
        for k, st in stats.items():
            if k in meta:
                st.funding_interval_h = meta[k].funding_interval_h
        return stats

    def spot(self) -> SpotBook:
        return parse_bybit_spot(get_json(f"{self._API}/v5/market/tickers", {"category": "spot"}))

    def klines(self, meta, limit):
        raw = get_json(
            f"{self._API}/v5/market/kline",
            {"category": "linear", "symbol": meta.native, "interval": "1", "limit": limit},
        )
        return parse_bybit_klines(raw)

    def ws_book(self):
        return _feed_book("bybit")

    def ensure_ws(self) -> None:
        _ensure_feed("bybit")


# ═══════════════════════════════════════════════════════════════ OKX


def parse_okx_instruments(raw: Any) -> list[InstrumentMeta]:
    out: list[InstrumentMeta] = []
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or it.get("settleCcy") != "USDT":
            continue
        if it.get("ctType") not in (None, "", "linear"):
            continue
        m = _meta(
            "okx",
            it.get("instId", ""),
            contract_size=fnum(it.get("ctVal")) or 1.0,
            active=it.get("state") == "live",
            funding_interval_h=8.0,
        )
        if m is not None:
            out.append(m)
    return out


def parse_okx_funding(raw: Any) -> dict[str, tuple[float | None, int | None, float | None]]:
    """instId-ключ → (rate, next_funding_ms, interval_h)."""
    out = {}
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or not it.get("instId"):
            continue
        t1, t2 = inum(it.get("fundingTime")), inum(it.get("nextFundingTime"))
        interval = (t2 - t1) / 3_600_000 if t1 and t2 and t2 > t1 else None
        out[norm_key(it["instId"])] = (fnum(it.get("fundingRate")), t1, interval)
    return out


def parse_okx_spot(raw: Any) -> SpotBook:
    out: SpotBook = {}
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or not it.get("instId"):
            continue
        e = _book_entry(it.get("bidPx"), it.get("bidSz"), it.get("askPx"), it.get("askSz"))
        if e is not None:
            # SPOT: volCcy24h — объём в валюте котировки
            out[norm_key(it["instId"])] = (*e, fnum(it.get("volCcy24h")))
    return out


class Okx(Venue):
    name = "okx"
    has_spot = True
    _API = "https://www.okx.com"
    _FUNDING_BATCH = 20

    def __init__(self) -> None:
        self._funding: dict[str, tuple[float | None, int | None, float | None]] = {}
        self._funding_cursor = 0
        self._funding_any_supported: bool | None = None

    def meta(self):
        return parse_okx_instruments(get_json(f"{self._API}/api/v5/public/instruments", {"instType": "SWAP"}))

    def stats(self, meta):
        stats: dict[str, PerpStats] = {}
        for it in _items(get_json(f"{self._API}/api/v5/market/tickers", {"instType": "SWAP"}), "data"):
            if not isinstance(it, dict) or not it.get("instId"):
                continue
            k = norm_key(it["instId"])
            last = fnum(it.get("last"))
            vol_base = fnum(it.get("volCcy24h"))  # SWAP: объём в монетах
            stats[k] = PerpStats(
                volume_24h_usd=vol_base * last if vol_base is not None and last else None,
                mark_price=last,
            )
        try:
            for it in _items(get_json(f"{self._API}/api/v5/public/open-interest", {"instType": "SWAP"}), "data"):
                if not isinstance(it, dict) or not it.get("instId"):
                    continue
                k = norm_key(it["instId"])
                if k not in stats:
                    continue
                oi_usd = fnum(it.get("oiUsd"))
                if oi_usd is None:
                    oi_ccy = fnum(it.get("oiCcy"))
                    mark = stats[k].mark_price
                    oi_usd = oi_ccy * mark if oi_ccy is not None and mark else None
                stats[k].open_interest_usd = oi_usd
        except Exception as e:  # noqa: BLE001
            logger.debug("okx open-interest failed: %s", e)
        self._refresh_funding(stats, meta)
        for k, st in stats.items():
            rate, nxt, interval = self._funding.get(k, (None, None, None))
            st.funding_rate, st.next_funding_ms = rate, nxt
            st.funding_interval_h = interval or 8.0
        return stats

    def _refresh_funding(self, stats, meta) -> None:
        # Новый API принимает instId=ANY (все контракты сразу); если не поддержан —
        # обходим топ по объёму порциями, чтобы не упереться в лимит 10 req/2s.
        if self._funding_any_supported is not False:
            try:
                got = parse_okx_funding(get_json(f"{self._API}/api/v5/public/funding-rate", {"instId": "ANY"}))
                if len(got) > 1:
                    self._funding = got
                    self._funding_any_supported = True
                    return
                self._funding_any_supported = False
            except Exception:  # noqa: BLE001
                self._funding_any_supported = False
        ranked = sorted(
            (k for k in stats if k in meta),
            key=lambda k: -(stats[k].volume_24h_usd or 0.0),
        )[:120]
        if not ranked:
            return
        start = self._funding_cursor % len(ranked)
        for k in (ranked + ranked)[start : start + min(self._FUNDING_BATCH, len(ranked))]:
            try:
                got = parse_okx_funding(get_json(f"{self._API}/api/v5/public/funding-rate", {"instId": meta[k].native}, timeout=5))
                self._funding.update(got)
            except Exception as e:  # noqa: BLE001
                logger.debug("okx funding %s failed: %s", k, e)
            time.sleep(0.2)
        self._funding_cursor = start + self._FUNDING_BATCH

    def spot(self):
        return parse_okx_spot(get_json(f"{self._API}/api/v5/market/tickers", {"instType": "SPOT"}))

    def ws_book(self):
        return _feed_book("okx")

    def ensure_ws(self) -> None:
        _ensure_feed("okx")


# ═══════════════════════════════════════════════════════════════ Gate.io


def parse_gate_contracts(raw: Any) -> tuple[list[InstrumentMeta], dict[str, PerpStats]]:
    metas: list[InstrumentMeta] = []
    stats: dict[str, PerpStats] = {}
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not it.get("name"):
            continue
        interval_s = fnum(it.get("funding_interval"))
        interval_h = interval_s / 3600 if interval_s else 8.0
        m = _meta(
            "gateio",
            it["name"],
            contract_size=fnum(it.get("quanto_multiplier")) or 1.0,
            active=not it.get("in_delisting") and it.get("status", "trading") == "trading",
            funding_interval_h=interval_h,
        )
        if m is None:
            continue
        metas.append(m)
        nxt = inum(it.get("funding_next_apply"))
        stats[m.key] = PerpStats(
            funding_rate=fnum(it.get("funding_rate")),
            funding_interval_h=interval_h,
            next_funding_ms=nxt * 1000 if nxt else None,
            mark_price=fnum(it.get("mark_price")),
        )
    return metas, stats


def parse_gate_spot(raw: Any) -> SpotBook:
    out: SpotBook = {}
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict) or not it.get("currency_pair"):
            continue
        e = _book_entry(it.get("highest_bid"), it.get("highest_size"), it.get("lowest_ask"), it.get("lowest_size"))
        if e is not None:
            out[norm_key(it["currency_pair"])] = (*e, fnum(it.get("quote_volume")))
    return out


class Gate(Venue):
    name = "gateio"
    has_spot = True
    _API = "https://api.gateio.ws/api/v4"

    def meta(self):
        return parse_gate_contracts(get_json(f"{self._API}/futures/usdt/contracts"))[0]

    def stats(self, meta):
        _, stats = parse_gate_contracts(get_json(f"{self._API}/futures/usdt/contracts"))
        for it in get_json(f"{self._API}/futures/usdt/tickers") or []:
            if not isinstance(it, dict) or not it.get("contract"):
                continue
            k = norm_key(it["contract"])
            st = stats.get(k)
            if st is None:
                continue
            st.volume_24h_usd = fnum(it.get("volume_24h_quote"))
            size, mark, m = fnum(it.get("total_size")), st.mark_price, meta.get(k)
            if size is not None and mark and m is not None:
                st.open_interest_usd = size * m.contract_size * mark
        return stats

    def spot(self):
        return parse_gate_spot(get_json(f"{self._API}/spot/tickers"))

    def ws_book(self):
        return _feed_book("gateio")

    def ensure_ws(self) -> None:
        _ensure_feed("gateio")


# ═══════════════════════════════════════════════════════════════ Bitget


def parse_bitget_contracts(raw: Any) -> list[InstrumentMeta]:
    out: list[InstrumentMeta] = []
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or it.get("quoteCoin") != "USDT":
            continue
        m = _meta(
            "bitget",
            it.get("symbol", ""),
            active=it.get("symbolStatus", "normal") == "normal",
            funding_interval_h=fnum(it.get("fundInterval")) or 8.0,
        )
        if m is not None:
            out.append(m)
    return out


def parse_bitget_tickers(raw: Any) -> dict[str, PerpStats]:
    out: dict[str, PerpStats] = {}
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        mark = fnum(it.get("markPrice")) or fnum(it.get("lastPr"))
        hold = fnum(it.get("holdingAmount"))
        out[norm_key(it["symbol"])] = PerpStats(
            funding_rate=fnum(it.get("fundingRate")),
            open_interest_usd=hold * mark if hold is not None and mark else None,
            volume_24h_usd=fnum(it.get("usdtVolume")) or fnum(it.get("quoteVolume")),
            mark_price=mark,
        )
    return out


def parse_bitget_spot(raw: Any) -> SpotBook:
    out: SpotBook = {}
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        e = _book_entry(it.get("bidPr"), it.get("bidSz"), it.get("askPr"), it.get("askSz"))
        if e is not None:
            out[norm_key(it["symbol"])] = (*e, fnum(it.get("quoteVolume")))
    return out


class Bitget(Venue):
    name = "bitget"
    has_spot = True
    _API = "https://api.bitget.com"

    def meta(self):
        return parse_bitget_contracts(
            get_json(f"{self._API}/api/v2/mix/market/contracts", {"productType": "USDT-FUTURES"})
        )

    def stats(self, meta):
        stats = parse_bitget_tickers(
            get_json(f"{self._API}/api/v2/mix/market/tickers", {"productType": "USDT-FUTURES"})
        )
        try:
            raw = get_json(f"{self._API}/api/v2/mix/market/current-fund-rate", {"productType": "USDT-FUTURES"})
            for it in _items(raw, "data"):
                if not isinstance(it, dict) or not it.get("symbol"):
                    continue
                st = stats.get(norm_key(it["symbol"]))
                if st is not None:
                    st.funding_interval_h = fnum(it.get("fundingRateInterval")) or None
                    st.next_funding_ms = inum(it.get("nextUpdate")) or None
        except Exception as e:  # noqa: BLE001 — интервал необязателен
            logger.debug("bitget current-fund-rate failed: %s", e)
        for k, st in stats.items():
            if st.funding_interval_h is None and k in meta:
                st.funding_interval_h = meta[k].funding_interval_h
        return stats

    def spot(self):
        return parse_bitget_spot(get_json(f"{self._API}/api/v2/spot/market/tickers"))

    def ws_book(self):
        return _feed_book("bitget")

    def ensure_ws(self) -> None:
        _ensure_feed("bitget")


# ═══════════════════════════════════════════════════════════════ HTX


def parse_htx_contracts(raw: Any) -> list[InstrumentMeta]:
    out: list[InstrumentMeta] = []
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or not it.get("contract_code"):
            continue
        if str(it.get("business_type", "swap")) not in ("swap", "all"):
            continue
        m = _meta(
            "htx",
            it["contract_code"],
            contract_size=fnum(it.get("contract_size")) or 1.0,
            active=it.get("contract_status") == 1,
            funding_interval_h=8.0,
        )
        if m is not None:
            out.append(m)
    return out


def parse_htx_spot(raw: Any) -> SpotBook:
    out: SpotBook = {}
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        e = _book_entry(it.get("bid"), it.get("bidSize"), it.get("ask"), it.get("askSize"))
        if e is not None:
            out[norm_key(it["symbol"])] = (*e, fnum(it.get("vol")))
    return out


class Htx(Venue):
    name = "htx"
    has_spot = True
    _API = "https://api.hbdm.com"
    _SPOT = "https://api.huobi.pro"

    def meta(self):
        return parse_htx_contracts(get_json(f"{self._API}/linear-swap-api/v1/swap_contract_info"))

    def stats(self, meta):
        stats: dict[str, PerpStats] = {}
        for it in _items(get_json(f"{self._API}/linear-swap-api/v1/swap_batch_funding_rate"), "data"):
            if not isinstance(it, dict) or not it.get("contract_code"):
                continue
            stats[norm_key(it["contract_code"])] = PerpStats(
                funding_rate=fnum(it.get("funding_rate")),
                funding_interval_h=8.0,
                next_funding_ms=inum(it.get("funding_time")) or None,
            )
        try:
            raw = get_json(f"{self._API}/linear-swap-api/v1/swap_open_interest", {"business_type": "swap"})
            for it in _items(raw, "data"):
                if isinstance(it, dict) and it.get("contract_code"):
                    st = stats.setdefault(norm_key(it["contract_code"]), PerpStats())
                    st.open_interest_usd = fnum(it.get("value"))
        except Exception as e:  # noqa: BLE001
            logger.debug("htx open interest failed: %s", e)
        try:
            raw = get_json(f"{self._API}/linear-swap-ex/market/detail/batch_merged", {"business_type": "swap"})
            for it in _items(raw, "ticks"):
                if isinstance(it, dict) and it.get("contract_code"):
                    st = stats.setdefault(norm_key(it["contract_code"]), PerpStats())
                    st.volume_24h_usd = fnum(it.get("trade_turnover"))
        except Exception as e:  # noqa: BLE001
            logger.debug("htx volumes failed: %s", e)
        return stats

    def spot(self):
        return parse_htx_spot(get_json(f"{self._SPOT}/market/tickers"))

    def ws_book(self):
        return _feed_book("htx")

    def ensure_ws(self) -> None:
        _ensure_feed("htx")


# ═══════════════════════════════════════════════════════════════ MEXC


def parse_mexc_contract_detail(raw: Any) -> list[InstrumentMeta]:
    out: list[InstrumentMeta] = []
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or it.get("quoteCoin") != "USDT":
            continue
        m = _meta(
            "mexc",
            it.get("symbol", ""),
            contract_size=fnum(it.get("contractSize")) or 1.0,
            active=it.get("state", 0) == 0,
            funding_interval_h=8.0,
        )
        if m is not None:
            out.append(m)
    return out


def parse_mexc_ticker(raw: Any, meta: dict[str, InstrumentMeta]) -> tuple[Book, dict[str, PerpStats]]:
    book: Book = {}
    stats: dict[str, PerpStats] = {}
    for it in _items(raw, "data"):
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        k = norm_key(it["symbol"])
        e = _book_entry(it.get("bid1"), None, it.get("ask1"), None)
        if e is not None:
            book[k] = e
        mark = fnum(it.get("fairPrice")) or fnum(it.get("lastPrice"))
        hold = fnum(it.get("holdVol"))
        m = meta.get(k)
        stats[k] = PerpStats(
            funding_rate=fnum(it.get("fundingRate")),
            funding_interval_h=m.funding_interval_h if m else 8.0,
            open_interest_usd=hold * m.contract_size * mark if hold is not None and mark and m else None,
            volume_24h_usd=fnum(it.get("amount24")),
            mark_price=mark,
        )
    return book, stats


def parse_mexc_klines(raw: Any) -> list[Candle]:
    d = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(d, dict):
        return []
    cols = [d.get(c) or [] for c in ("time", "open", "high", "low", "close", "vol", "amount")]
    out: list[Candle] = []
    for t, o, h, l, c, v, a in zip(*cols):
        vals = [fnum(x) for x in (t, o, h, l, c, v)]
        if None in vals:
            continue
        t, o, h, l, c, v = vals
        out.append(Candle(t=int(t) * 1000, o=o, h=h, l=l, c=c, v=v, qv=fnum(a) or 0.0))
    return out


def parse_mexc_spot(raw: Any) -> SpotBook:
    book = parse_binance_book_ticker(raw)  # формат как у Binance /api/v3/ticker/bookTicker
    return {k: (*v, None) for k, v in book.items()}


class Mexc(Venue):
    """MEXC: WS-тикеры фьючерсов без лучших bid/ask, поэтому — быстрый REST-опрос.

    /api/v1/contract/ticker отдаёт bid1/ask1 всех контрактов одним запросом
    (без объёма на лучшей цене — его докачивает liquidity probe по запросу).
    """

    name = "mexc"
    has_spot = True
    perp_rest_interval = 1.5
    _API = "https://contract.mexc.com"
    _SPOT = "https://api.mexc.com"

    def __init__(self) -> None:
        self._last_stats: dict[str, PerpStats] = {}
        self._meta: dict[str, InstrumentMeta] = {}

    def meta(self):
        metas = parse_mexc_contract_detail(get_json(f"{self._API}/api/v1/contract/detail"))
        self._meta = {m.key: m for m in metas}
        return metas

    def perp_rest(self):
        book, stats = parse_mexc_ticker(get_json(f"{self._API}/api/v1/contract/ticker"), self._meta)
        self._last_stats = stats
        return book

    def stats(self, meta):
        # funding/OI/объём приходят в том же тикере, что и цены
        return dict(self._last_stats) if self._last_stats else None

    def spot(self):
        return parse_mexc_spot(get_json(f"{self._SPOT}/api/v3/ticker/bookTicker"))

    def klines(self, meta, limit):
        end = int(time.time())
        raw = get_json(
            f"{self._API}/api/v1/contract/kline/{meta.native}",
            {"interval": "Min1", "start": end - limit * 60, "end": end},
        )
        return parse_mexc_klines(raw)

    def depth_l1(self, native: str) -> tuple[float, float] | None:
        """(bid_qty, ask_qty) в контрактах по REST-стакану."""
        raw = get_json(f"{self._API}/api/v1/contract/depth/{native}", {"limit": 5}, timeout=5)
        d = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(d, dict):
            return None
        bids, asks = d.get("bids") or [], d.get("asks") or []
        if not bids or not asks:
            return None
        bq, aq = fnum(bids[0][1]), fnum(asks[0][1])
        return (bq, aq) if bq is not None and aq is not None else None


# ═══════════════════════════════════════════════════════════════ dYdX


class Dydx(Venue):
    name = "dydx"
    quote_ccy = "USD"
    stats_interval = 60.0

    def meta(self):
        from mexc_monitor.dydx.ws_feed import _feed

        out: list[InstrumentMeta] = []
        for market, info in _feed._refresh_markets_meta().items():
            m = _meta(
                "dydx",
                market,
                active=info.get("status") == "ACTIVE",
                funding_interval_h=1.0,
                quote_filter=("USD",),
            )
            if m is not None:
                out.append(m)
        return out

    def stats(self, meta):
        from mexc_monitor.dydx.ws_feed import _feed

        out: dict[str, PerpStats] = {}
        for market, info in _feed._refresh_markets_meta().items():
            oracle = fnum(info.get("oraclePrice"))
            oi = fnum(info.get("openInterest"))
            out[norm_key(market)] = PerpStats(
                funding_rate=fnum(info.get("nextFundingRate")),
                funding_interval_h=1.0,
                open_interest_usd=oi * oracle if oi is not None and oracle else None,
                volume_24h_usd=fnum(info.get("volume24H")),
                mark_price=oracle,
            )
        return out

    def ws_book(self):
        from mexc_monitor.dydx.ws_feed import _feed

        with _feed.lock:
            age = time.monotonic() - _feed.last_msg_mono if _feed.last_msg_mono else None
            book: Book = {}
            for market, (bids, asks) in _feed.books.items():
                if not bids or not asks:
                    continue
                b, a = max(bids), min(asks)
                if 0 < b <= a:
                    book[norm_key(market)] = (b, bids[b], a, asks[a])
        return age, book

    def ensure_ws(self) -> None:
        from mexc_monitor.dydx.ws_feed import ensure_dydx_ws_started

        ensure_dydx_ws_started()


# ═══════════════════════════════════════════════════════════════ Hyperliquid


def parse_hyperliquid(raw: Any) -> tuple[list[InstrumentMeta], Book, dict[str, PerpStats]]:
    """metaAndAssetCtxs → справочник, impact-котировки, funding/OI/объём.

    Лучших bid/ask в этом ответе нет; impactPxs — цены исполнения на
    impact-объём (консервативнее L1). Объём на лучшей цене неизвестен.
    """
    metas: list[InstrumentMeta] = []
    book: Book = {}
    stats: dict[str, PerpStats] = {}
    if not isinstance(raw, list) or len(raw) < 2:
        return metas, book, stats
    universe = raw[0].get("universe", []) if isinstance(raw[0], dict) else []
    ctxs = raw[1] if isinstance(raw[1], list) else []
    for asset, ctx in zip(universe, ctxs):
        if not isinstance(asset, dict) or not isinstance(ctx, dict) or not asset.get("name"):
            continue
        # У монет Hyperliquid нет суффикса котировки (BTC, kPEPE) — не угадываем его
        name = asset["name"]
        base, mult = split_multiplier(name)
        m = InstrumentMeta(
            venue="hyperliquid",
            key=norm_key(name),
            native=name,
            base=base.upper(),
            quote="USDC",
            price_mult=mult,
            active=not asset.get("isDelisted", False),
            funding_interval_h=1.0,
        )
        metas.append(m)
        mark = fnum(ctx.get("markPx"))
        impact = ctx.get("impactPxs") or []
        if isinstance(impact, list) and len(impact) == 2:
            e = _book_entry(impact[0], None, impact[1], None)
            if e is not None:
                book[m.key] = e
        oi = fnum(ctx.get("openInterest"))
        stats[m.key] = PerpStats(
            funding_rate=fnum(ctx.get("funding")),
            funding_interval_h=1.0,
            open_interest_usd=oi * mark if oi is not None and mark else None,
            volume_24h_usd=fnum(ctx.get("dayNtlVlm")),
            mark_price=mark,
        )
    return metas, book, stats


class Hyperliquid(Venue):
    name = "hyperliquid"
    quote_ccy = "USDC"
    perp_rest_interval = 2.0
    indicative = True
    _API = "https://api.hyperliquid.xyz/info"

    def __init__(self) -> None:
        self._metas: list[InstrumentMeta] = []
        self._stats: dict[str, PerpStats] = {}

    def _poll(self) -> Book:
        metas, book, stats = parse_hyperliquid(post_json(self._API, {"type": "metaAndAssetCtxs"}))
        self._metas, self._stats = metas, stats
        return book

    def meta(self):
        if not self._metas:
            self._poll()
        return self._metas

    def perp_rest(self):
        return self._poll()

    def stats(self, meta):
        return dict(self._stats) if self._stats else None


# ═══════════════════════════════════════════════════════════════ Lighter


class Lighter(Venue):
    name = "lighter"
    quote_ccy = "USD"
    perp_rest_interval = 10.0  # стакан по каждому рынку отдельным запросом

    def __init__(self) -> None:
        self._stats: dict[str, PerpStats] = {}
        self._metas: list[InstrumentMeta] = []

    def perp_rest(self):
        from mexc_monitor.lighter.client import lighter_snapshot_rows

        book: Book = {}
        stats: dict[str, PerpStats] = {}
        metas: list[InstrumentMeta] = []
        for row in lighter_snapshot_rows():
            # lighter_snapshot_rows нормализует символ к «XXXUSDT», хотя расчёты в USD
            m = _meta("lighter", row.symbol, funding_interval_h=1.0)
            if m is None:
                continue
            m.quote = "USD"
            metas.append(m)
            book[m.key] = (row.bid, row.bid_qty or None, row.ask, row.ask_qty or None)
            stats[m.key] = PerpStats(
                funding_rate=row.funding_rate,
                funding_interval_h=1.0,
                volume_24h_usd=row.volume_24h_quote or None,
            )
        self._stats, self._metas = stats, metas
        return book

    def meta(self):
        return self._metas or None

    def stats(self, meta):
        return dict(self._stats) if self._stats else None


# ═══════════════════════════════════════════════════════════════ WS feeds


def _feed_book(name: str) -> tuple[float | None, Book] | None:
    from mexc_monitor.ws_bookticker import _feeds

    feed = _feeds.get(name)
    if feed is None:
        return None
    with feed.lock:
        age = time.monotonic() - feed.last_msg_mono if feed.last_msg_mono else None
        return age, dict(feed.book)


def _ensure_feed(name: str) -> None:
    from mexc_monitor.ws_bookticker import _feeds

    feed = _feeds.get(name)
    if feed is not None:
        feed.ensure_started()


def default_venues() -> dict[str, Venue]:
    venues: list[Venue] = [
        BinanceLike("binance", "https://fapi.binance.com", "https://api.binance.com", "binance"),
        Bybit(),
        Okx(),
        Gate(),
        Bitget(),
        Htx(),
        Mexc(),
        BinanceLike("asterdex", "https://fapi.asterdex.com", None, None),
        Dydx(),
        Hyperliquid(),
        Lighter(),
    ]
    return {v.name: v for v in venues}


VenueFactory = Callable[[], dict[str, Venue]]
