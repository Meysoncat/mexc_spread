"""dYdX v4 indexer WebSocket: живые стаканы всех ACTIVE рынков.

REST-сборка снапшота — это ~114 запросов стакана и упирается в rate limit
индексера (~15 секунд). WS-стрим `v4_orderbook` даёт те же данные пушем:
после подписки поддерживаем локальные книги и отдаём top-of-book мгновенно.

Метаданные (volume24H, nextFundingRate, статус) берём одним REST-запросом
/v4/perpetualMarkets и кэшируем (60 сек).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from mexc_monitor.models import BookTickerRow

logger = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://indexer.dydx.trade/v4/ws"
_STALE_AFTER_SEC = 30.0
_MARKETS_CACHE_TTL_SEC = 60.0
_RECONNECT_BACKOFF_SEC = [1, 2, 5, 10, 30]
# Индексер ограничивает v4_orderbook 32 подписками на соединение —
# шардируем рынки по нескольким соединениям.
_MARKETS_PER_CONNECTION = 30
_SUBSCRIBE_PAUSE_SEC = 0.05


def _parse_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class _DydxOrderbookFeed:
    def __init__(self, url: str = DEFAULT_WS_URL):
        self.url = url
        self.lock = threading.Lock()
        # market -> ({price: size} bids, {price: size} asks)
        self.books: dict[str, tuple[dict[float, float], dict[float, float]]] = {}
        self.last_msg_mono = 0.0
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.markets_meta: dict[str, dict[str, Any]] = {}
        self.markets_meta_expires = 0.0

    def ensure_started(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(
            target=self._run_manager, name="ws-dydx-orderbook", daemon=True
        )
        self.thread.start()

    # -- markets meta (REST, кэш) ------------------------------------------

    def _refresh_markets_meta(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        with self.lock:
            if self.markets_meta and self.markets_meta_expires > now:
                return dict(self.markets_meta)
        from mexc_monitor.dydx.client import DydxPublicClient

        try:
            markets = DydxPublicClient().perpetual_markets()
        except Exception as e:  # noqa: BLE001
            logger.warning("dydx ws: perpetualMarkets failed: %s", e)
            with self.lock:
                return dict(self.markets_meta)
        meta = {
            name: info
            for name, info in markets.items()
            if isinstance(info, dict) and info.get("status") == "ACTIVE"
        }
        with self.lock:
            self.markets_meta = meta
            self.markets_meta_expires = time.monotonic() + _MARKETS_CACHE_TTL_SEC
        return dict(meta)

    # -- ws loop -------------------------------------------------------------

    def _apply_levels(
        self,
        side: dict[float, float],
        levels: Any,
        *,
        initial: bool,
    ) -> None:
        if not isinstance(levels, list):
            return
        for lvl in levels:
            if isinstance(lvl, dict):
                price = _parse_float(lvl.get("price"))
                size = _parse_float(lvl.get("size"))
            elif isinstance(lvl, list) and len(lvl) >= 2:
                price = _parse_float(lvl[0])
                size = _parse_float(lvl[1])
            else:
                continue
            if price <= 0:
                continue
            if size <= 0 and not initial:
                side.pop(price, None)
            elif size > 0:
                side[price] = size

    def _handle_message(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        market = msg.get("id")
        contents = msg.get("contents")
        if not market or not isinstance(contents, dict):
            return
        if mtype == "subscribed":
            bids: dict[float, float] = {}
            asks: dict[float, float] = {}
            self._apply_levels(bids, contents.get("bids"), initial=True)
            self._apply_levels(asks, contents.get("asks"), initial=True)
            with self.lock:
                self.books[market] = (bids, asks)
                self.last_msg_mono = time.monotonic()
        elif mtype == "channel_data":
            with self.lock:
                book = self.books.get(market)
                if book is None:
                    return
                self._apply_levels(book[0], contents.get("bids"), initial=False)
                self._apply_levels(book[1], contents.get("asks"), initial=False)
                self.last_msg_mono = time.monotonic()

    def _run_manager(self) -> None:
        """Шардирует ACTIVE-рынки по соединениям (лимит 32 подписки/соединение)."""
        meta: dict[str, dict[str, Any]] = {}
        while not self.stop.is_set() and not meta:
            meta = self._refresh_markets_meta()
            if not meta and self.stop.wait(5):
                return
        names = sorted(meta.keys())
        shards = [
            names[i : i + _MARKETS_PER_CONNECTION]
            for i in range(0, len(names), _MARKETS_PER_CONNECTION)
        ]
        workers = [
            threading.Thread(
                target=self._run_shard,
                args=(shard,),
                name=f"ws-dydx-orderbook-{i}",
                daemon=True,
            )
            for i, shard in enumerate(shards)
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

    def _run_shard(self, names: list[str]) -> None:
        import websocket

        attempt = 0
        while not self.stop.is_set():
            try:
                ws = websocket.create_connection(self.url, timeout=15)
                attempt = 0
                logger.info("dydx ws shard connected, subscribing %s markets", len(names))
                with self.lock:
                    for name in names:
                        self.books.pop(name, None)
                for name in names:
                    ws.send(
                        json.dumps(
                            {
                                "type": "subscribe",
                                "channel": "v4_orderbook",
                                "id": name,
                                "batched": True,
                            }
                        )
                    )
                    if self.stop.wait(_SUBSCRIBE_PAUSE_SEC):
                        ws.close()
                        return
                ws.settimeout(30)
                while not self.stop.is_set():
                    raw = ws.recv()
                    if not raw:
                        continue
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(msg, dict):
                        if msg.get("channel") == "v4_orderbook":
                            self._handle_message(msg)
                        elif msg.get("type") == "error":
                            logger.warning("dydx ws error msg: %s", msg.get("message"))
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001 — reconnect loop
                if self.stop.is_set():
                    return
                delay = _RECONNECT_BACKOFF_SEC[
                    min(attempt, len(_RECONNECT_BACKOFF_SEC) - 1)
                ]
                attempt += 1
                logger.warning(
                    "dydx ws shard error (%s: %s), reconnect in %ss",
                    type(e).__name__,
                    e,
                    delay,
                )
                if self.stop.wait(delay):
                    return

    # -- snapshot --------------------------------------------------------------

    def snapshot_rows(self) -> list[BookTickerRow] | None:
        with self.lock:
            if not self.books:
                return None
            if (time.monotonic() - self.last_msg_mono) > _STALE_AFTER_SEC:
                return None
            tops: dict[str, tuple[float, float, float, float]] = {}
            for market, (bids, asks) in self.books.items():
                if not bids or not asks:
                    continue
                bid_price = max(bids)
                ask_price = min(asks)
                if bid_price <= 0 or ask_price <= 0:
                    continue
                tops[market] = (
                    bid_price,
                    bids[bid_price],
                    ask_price,
                    asks[ask_price],
                )
        if len(tops) < 10:
            return None

        meta = self._refresh_markets_meta()
        now_iso = datetime.now(timezone.utc).isoformat()
        rows: list[BookTickerRow] = []
        for market, (bid, bq, ask, aq) in tops.items():
            info = meta.get(market, {})
            volume_24h_quote = _parse_float(info.get("volume24H"))
            oracle_price = _parse_float(info.get("oraclePrice"))
            volume_24h_base = (
                volume_24h_quote / oracle_price if oracle_price > 0 else 0.0
            )
            funding = info.get("nextFundingRate")
            mid = (bid + ask) / 2
            spread_abs = ask - bid
            rows.append(
                BookTickerRow(
                    symbol=market.replace("-", ""),
                    bid=bid,
                    ask=ask,
                    bid_qty=bq,
                    ask_qty=aq,
                    mid=mid,
                    spread_abs=spread_abs,
                    spread_bps=(10_000 * spread_abs / mid) if mid > 0 else None,
                    volume_24h_base=volume_24h_base,
                    volume_24h_quote=volume_24h_quote,
                    funding_rate=_parse_float(funding) if funding is not None else None,
                    observed_at=now_iso,
                )
            )
        return rows


_feed = _DydxOrderbookFeed()


def ensure_dydx_ws_started() -> None:
    """Запускает WS-стрим заранее (прогрев при старте бэкенда)."""
    _feed.ensure_started()


def try_dydx_ws_snapshot_rows() -> list[BookTickerRow] | None:
    """Строки снапшота из WS-книг, если стрим жив; иначе None (REST fallback).

    Первый вызов запускает стрим — тёплые данные появятся через несколько секунд.
    """
    _feed.ensure_started()
    return _feed.snapshot_rows()


def dydx_ws_health() -> dict[str, Any]:
    """Состояние dYdX-фида (для /api/health)."""
    with _feed.lock:
        age = (
            round(time.monotonic() - _feed.last_msg_mono, 1)
            if _feed.last_msg_mono
            else None
        )
        return {
            "running": _feed.thread is not None and _feed.thread.is_alive(),
            "symbols": len(_feed.books),
            "last_message_age_sec": age,
            "live": bool(
                _feed.books and age is not None and age <= _STALE_AFTER_SEC
            ),
        }


def stop_dydx_ws() -> None:
    _feed.stop.set()
