"""In-memory ring buffer для сделок (MEXC spot deals).

Хранит недавние сделки по каждому символу и считает агрегаты в реальном времени:
плотность сделок, buy/sell-имбаланс, VWAP сделок, оборот. Заполняется из
``ws_spot_deals``. Используется AI-ассессором шорт-листа (и в перспективе —
фильтром скринера) как источник «честной» торговой активности, которой нет в
``volume_24h``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TradeEvent:
    """Одна исполненная сделка."""

    timestamp_ms: int
    price: float
    quantity: float
    side: int  # 1 = buy (taker bought), 2 = sell (taker sold)
    notional: float  # price * quantity, в quote-валюте


@dataclass(frozen=True, slots=True)
class TradeStats:
    """Агрегаты сделок за период."""

    symbol: str
    period_sec: float
    count: int
    buy_count: int
    sell_count: int
    volume_base: float
    volume_quote: float
    buy_volume_quote: float
    sell_volume_quote: float
    vwap: float | None  # средневзвешенная по объёму цена сделок
    buy_sell_ratio: float | None  # buy_quote / sell_quote (None если sell=0)
    trades_per_min: float
    latest_ms: int | None


# По умолчанию храним ~10 минут сделок (при ~5 сделок/сек = ~3000).
_DEFAULT_MAX_EVENTS = 5_000
_DEFAULT_MAX_AGE_SEC = 600.0

_lock = threading.Lock()
_buffers: dict[str, deque[TradeEvent]] = {}
_max_events: int = _DEFAULT_MAX_EVENTS
_max_age_sec: float = _DEFAULT_MAX_AGE_SEC


def configure(
    max_events: int = _DEFAULT_MAX_EVENTS,
    max_age_sec: float = _DEFAULT_MAX_AGE_SEC,
) -> None:
    global _max_events, _max_age_sec
    _max_events = max(100, max_events)
    _max_age_sec = max(10.0, max_age_sec)


def push_trade(
    symbol: str,
    price: float,
    quantity: float,
    side: int,
    ts_ms: int | None = None,
) -> TradeEvent | None:
    """Добавить сделку. Возвращает TradeEvent если валидна, иначе None."""
    if price <= 0 or quantity <= 0 or side not in (1, 2):
        return None
    if ts_ms is None:
        ts_ms = int(time.time() * 1000)
    ev = TradeEvent(
        timestamp_ms=int(ts_ms),
        price=float(price),
        quantity=float(quantity),
        side=int(side),
        notional=float(price) * float(quantity),
    )
    sym = symbol.upper()
    with _lock:
        buf = _buffers.get(sym)
        if buf is None:
            buf = deque(maxlen=_max_events)
            _buffers[sym] = buf
        buf.append(ev)
    return ev


def get_stats(symbol: str, period_sec: float = 60.0) -> TradeStats | None:
    """Агрегаты сделок за последние ``period_sec`` секунд."""
    sym = symbol.upper()
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - int(period_sec * 1000)

    with _lock:
        buf = _buffers.get(sym)
        if buf is None or len(buf) == 0:
            return None
        cutoff_max_age = now_ms - int(_max_age_sec * 1000)
        while buf and buf[0].timestamp_ms < cutoff_max_age:
            buf.popleft()
        window = [e for e in buf if e.timestamp_ms >= since_ms]

    if not window:
        return TradeStats(
            symbol=sym,
            period_sec=period_sec,
            count=0,
            buy_count=0,
            sell_count=0,
            volume_base=0.0,
            volume_quote=0.0,
            buy_volume_quote=0.0,
            sell_volume_quote=0.0,
            vwap=None,
            buy_sell_ratio=None,
            trades_per_min=0.0,
            latest_ms=None,
        )

    count = len(window)
    buy_count = sum(1 for e in window if e.side == 1)
    sell_count = count - buy_count
    volume_base = sum(e.quantity for e in window)
    volume_quote = sum(e.notional for e in window)
    buy_quote = sum(e.notional for e in window if e.side == 1)
    sell_quote = volume_quote - buy_quote
    vwap = volume_quote / volume_base if volume_base > 0 else None
    ratio = (buy_quote / sell_quote) if sell_quote > 0 else None
    latest = max(e.timestamp_ms for e in window)
    trades_per_min = count / max(period_sec / 60.0, 1e-9)

    return TradeStats(
        symbol=sym,
        period_sec=period_sec,
        count=count,
        buy_count=buy_count,
        sell_count=sell_count,
        volume_base=volume_base,
        volume_quote=volume_quote,
        buy_volume_quote=buy_quote,
        sell_volume_quote=sell_quote,
        vwap=vwap,
        buy_sell_ratio=ratio,
        trades_per_min=trades_per_min,
        latest_ms=latest,
    )


def get_tracked_symbols() -> list[str]:
    with _lock:
        return [sym for sym, buf in _buffers.items() if len(buf) > 0]


def clear(symbol: str | None = None) -> None:
    with _lock:
        if symbol is None:
            _buffers.clear()
        else:
            _buffers.pop(symbol.upper(), None)
