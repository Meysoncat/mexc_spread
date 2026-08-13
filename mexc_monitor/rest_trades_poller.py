"""REST trades poller — the working trade-feed for ``trade_buffer``.

Polls MEXC ``/api/v3/trades`` per symbol on a background thread and pushes the
parsed trades into :mod:`mexc_monitor.trade_buffer`. This is the practical
trade source: the spot deals WebSocket (``spot@public.deals.v3.api``) is
server-side blocked even through a proxy, whereas the REST endpoint works
through the per-exchange proxy.

Lifecycle mirrors :class:`mexc_monitor.metascalp.poller.MetaScalpPoller`.
The screener feeds its shortlist via :meth:`set_watch_symbols` (called each
scan, like ``touch_watchlist`` for the bookTicker WS).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from mexc_monitor import trade_buffer
from mexc_monitor.client import fetch_recent_trades
from mexc_monitor.config import DEFAULT_SETTINGS, Settings
from mexc_monitor.http_utils import RequestPacer, mexc_httpx_client

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SEC = 5.0
_MIN_INTERVAL_SEC = 1.0


def _norm(s: str) -> str:
    return str(s).strip().upper()


def _side_from_trade(t: dict[str, Any]) -> int:
    """Map a MEXC trade to trade_buffer side (1=buy, 2=sell).

    ``isBuyerMaker=true`` ⇒ the taker hit a resting bid ⇒ taker SOLD (2).
    ``isBuyerMaker=false`` ⇒ taker lifted the ask ⇒ taker BOUGHT (1).
    Falls back to ``tradeType`` (``ASK``=buy, ``BID``=sell) when absent.
    """
    if "isBuyerMaker" in t:
        try:
            return 2 if bool(t["isBuyerMaker"]) else 1
        except (TypeError, ValueError):
            pass
    tt = str(t.get("tradeType", "") or t.get("TradeType", "")).strip().upper()
    if tt == "BID":
        return 2
    if tt == "ASK":
        return 1
    return 0


class RestTradesPoller:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        interval_sec: float | None = None,
        limit: int | None = None,
        max_symbols: int | None = None,
    ) -> None:
        self._cfg = settings or DEFAULT_SETTINGS
        self._interval = max(
            _MIN_INTERVAL_SEC, float(interval_sec if interval_sec is not None else self._cfg.rest_trades_poller_interval_sec)
        )
        self._limit = max(1, min(int(limit if limit is not None else self._cfg.rest_trades_poller_limit), 1000))
        self._max_symbols = max(0, int(max_symbols if max_symbols is not None else self._cfg.rest_trades_poller_max_symbols))
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._symbols: list[str] = []
        # per-symbol dedup: last ingested trade time_ms
        self._last_seen_ms: dict[str, int] = {}
        self._last_tick_ok: bool | None = None
        self._last_tick_iso: str | None = None
        self._ingested_total: int = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="mexc-rest-trades-poller"
        )
        self._thread.start()
        logger.info(
            "RestTradesPoller started (interval=%.1fs, limit=%d, max_symbols=%d)",
            self._interval,
            self._limit,
            self._max_symbols,
        )

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10.0)
        self._thread = None

    def set_watch_symbols(self, symbols) -> None:
        """Update the polled symbol set (capped to max_symbols). Truncation
        keeps the first N (caller passes them priority-ordered, e.g. the
        screener shortlist already sorted by score)."""
        seen: set[str] = set()
        out: list[str] = []
        for s in symbols or ():
            sym = _norm(s)
            if sym and sym not in seen:
                seen.add(sym)
                out.append(sym)
            if len(out) >= self._max_symbols:
                break
        with self._lock:
            self._symbols = out

    def status(self) -> dict[str, Any]:
        with self._lock:
            syms = list(self._symbols)
            running = self._running
        return {
            "running": running,
            "interval_sec": self._interval,
            "limit": self._limit,
            "max_symbols": self._max_symbols,
            "watch_symbols": syms,
            "tracked_in_buffer": trade_buffer.get_tracked_symbols(),
            "last_tick_ok": self._last_tick_ok,
            "last_tick_at": self._last_tick_iso,
            "ingested_total": self._ingested_total,
        }

    # ── loop ─────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("RestTradesPoller tick failed")
            if self._stop_event.wait(timeout=self._interval):
                break

    def _tick(self) -> None:
        with self._lock:
            symbols = list(self._symbols)
        if not symbols:
            return
        cfg = self._cfg
        pacer = RequestPacer(cfg.http_min_request_interval_sec)
        ingested = 0
        with mexc_httpx_client(cfg, exchange="mexc") as c:
            for sym in symbols:
                if self._stop_event.is_set():
                    break
                try:
                    trades = fetch_recent_trades(
                        sym, cfg, limit=self._limit, client=c, pacer=pacer
                    )
                except Exception:
                    logger.debug("RestTradesPoller: fetch failed for %s", sym, exc_info=True)
                    continue
                ingested += self._ingest(sym, trades)
        self._ingested_total += ingested
        self._last_tick_ok = True
        self._last_tick_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _ingest(self, symbol: str, trades: list[dict[str, Any]]) -> int:
        """Push only trades newer than the last seen ``time`` for the symbol.
        Returns the count of newly ingested trades."""
        last = self._last_seen_ms.get(symbol, 0)
        new_max = last
        count = 0
        for t in trades:
            try:
                ts = int(t.get("time") or t.get("Time") or 0)
            except (TypeError, ValueError):
                continue
            if ts <= last:
                continue
            price = _to_float(t.get("price"))
            qty = _to_float(t.get("qty") or t.get("quantity"))
            if price is None or qty is None:
                continue
            side = _side_from_trade(t)
            if side not in (1, 2):
                continue
            ev = trade_buffer.push_trade(symbol, price, qty, side, ts_ms=ts)
            if ev is not None:
                count += 1
            if ts > new_max:
                new_max = ts
        if new_max > last:
            self._last_seen_ms[symbol] = new_max
        return count


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f
