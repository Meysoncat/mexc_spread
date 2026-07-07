"""
In-memory ring buffer для высокочастотных данных спреда.

Хранит историю bid/ask/spread по каждому символу за последние N минут.
Заполняется из WS (spot bookTicker) при каждом push-обновлении.
Используется для:
  - графиков спреда в реальном времени
  - SSE/WS push в фронтенд
  - расчёта статистики спреда (avg, min, max, std)
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SpreadTick:
    """Одна точка данных спреда."""
    timestamp_ms: int  # unix ms
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float
    mid: float
    spread_abs: float
    spread_bps: float | None


@dataclass(frozen=True, slots=True)
class SpreadStats:
    """Статистика спреда за период."""
    symbol: str
    period_sec: float
    ticks_count: int
    avg_spread_bps: float | None
    min_spread_bps: float | None
    max_spread_bps: float | None
    std_spread_bps: float | None
    current_spread_bps: float | None
    current_bid: float
    current_ask: float
    current_mid: float
    pct_above_threshold: float | None  # % времени спред > порога


# Максимальная длина буфера на символ (при ~10 тиков/сек = ~30 мин)
_DEFAULT_MAX_TICKS = 18_000
# Максимальное время хранения (секунды)
_DEFAULT_MAX_AGE_SEC = 1800.0  # 30 минут

_lock = threading.Lock()
_buffers: dict[str, deque[SpreadTick]] = {}
_max_ticks: int = _DEFAULT_MAX_TICKS
_max_age_sec: float = _DEFAULT_MAX_AGE_SEC

# Callbacks для SSE/WS push (symbol -> list[callback])
_subscribers: dict[str, list[Any]] = {}
_sub_lock = threading.Lock()


def configure(max_ticks: int = _DEFAULT_MAX_TICKS, max_age_sec: float = _DEFAULT_MAX_AGE_SEC) -> None:
    """Настройка параметров буфера."""
    global _max_ticks, _max_age_sec
    _max_ticks = max(100, max_ticks)
    _max_age_sec = max(10.0, max_age_sec)


def push_tick(
    symbol: str,
    bid: float,
    ask: float,
    bid_qty: float,
    ask_qty: float,
) -> SpreadTick | None:
    """
    Добавить новый тик в буфер. Вызывается из WS-обработчика.
    Возвращает SpreadTick если данные валидны, None иначе.
    """
    if bid <= 0 or ask <= 0 or ask < bid:
        return None

    mid = (bid + ask) / 2.0
    spread_abs = ask - bid
    spread_bps = (10_000.0 * spread_abs / mid) if mid > 0 else None

    tick = SpreadTick(
        timestamp_ms=int(time.time() * 1000),
        bid=bid,
        ask=ask,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        mid=mid,
        spread_abs=spread_abs,
        spread_bps=spread_bps,
    )

    sym = symbol.upper()
    with _lock:
        buf = _buffers.get(sym)
        if buf is None:
            buf = deque(maxlen=_max_ticks)
            _buffers[sym] = buf
        buf.append(tick)

    # Notify subscribers
    _notify_subscribers(sym, tick)
    return tick


def _lttb_downsample(ticks: list[SpreadTick], threshold: int) -> list[SpreadTick]:
    """Largest Triangle Three Buckets (LTTB) downsampling.

    Preserves the visual shape of the time series better than stride sampling
    by keeping points that form the largest triangles between adjacent buckets.

    Uses ``(timestamp_ms, spread_bps or mid)`` as the (x, y) pair.
    """
    n = len(ticks)
    if n <= threshold or threshold < 3:
        return ticks

    def _y(t: SpreadTick) -> float:
        return t.spread_bps if t.spread_bps is not None else t.mid

    out: list[SpreadTick] = [ticks[0]]

    # Bucket size (excluding first and last point)
    bucket_size = (n - 2) / (threshold - 2)
    prev_selected = 0  # index of previously selected point

    for i in range(threshold - 2):
        # Bucket boundaries
        bucket_start = int(1 + i * bucket_size)
        bucket_end = int(1 + (i + 1) * bucket_size)
        if bucket_end > n - 1:
            bucket_end = n - 1
        if bucket_start >= bucket_end:
            bucket_start = bucket_end - 1

        # Average point of the NEXT bucket (for triangle area calculation)
        next_start = bucket_end
        next_end = int(1 + (i + 2) * bucket_size)
        if next_end > n - 1:
            next_end = n - 1
        if next_start >= next_end:
            next_start = next_end - 1

        next_avg_x = sum(t.timestamp_ms for t in ticks[next_start:next_end]) / max(1, next_end - next_start)
        next_avg_y = sum(_y(t) for t in ticks[next_start:next_end]) / max(1, next_end - next_start)

        # Find the point in the current bucket that forms the largest triangle
        max_area = -1.0
        best_idx = bucket_start
        px = ticks[prev_selected].timestamp_ms
        py = _y(ticks[prev_selected])

        for j in range(bucket_start, bucket_end):
            # Triangle area = 0.5 * |x1(y2-y3) + x2(y3-y1) + x3(y1-y2)|
            x = ticks[j].timestamp_ms
            y = _y(ticks[j])
            area = abs(
                px * (next_avg_y - y)
                + x * (y - py)
                + next_avg_x * (py - next_avg_y)
            )
            if area > max_area:
                max_area = area
                best_idx = j

        out.append(ticks[best_idx])
        prev_selected = best_idx

    # Always keep the last point
    out.append(ticks[-1])
    return out


def get_history(
    symbol: str,
    *,
    last_n: int | None = None,
    since_ms: int | None = None,
    max_points: int = 2000,
) -> list[SpreadTick]:
    """
    Получить историю спреда для символа.
    last_n: последние N тиков
    since_ms: тики с указанного timestamp_ms
    max_points: максимум точек в ответе (downsampling если больше)
    """
    sym = symbol.upper()
    with _lock:
        buf = _buffers.get(sym)
        if buf is None:
            return []
        # Копируем для безопасности
        all_ticks = list(buf)

    # Фильтрация по времени
    if since_ms is not None:
        all_ticks = [t for t in all_ticks if t.timestamp_ms >= since_ms]
    elif last_n is not None:
        all_ticks = all_ticks[-last_n:]

    # Удаляем протухшие
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - int(_max_age_sec * 1000)
    all_ticks = [t for t in all_ticks if t.timestamp_ms >= cutoff_ms]

    # Downsampling если слишком много точек
    if len(all_ticks) > max_points:
        all_ticks = _lttb_downsample(all_ticks, max_points)

    return all_ticks


def get_latest(symbol: str) -> SpreadTick | None:
    """Последний тик для символа."""
    sym = symbol.upper()
    with _lock:
        buf = _buffers.get(sym)
        if buf is None or len(buf) == 0:
            return None
        return buf[-1]


def get_stats(
    symbol: str,
    *,
    period_sec: float = 300.0,
    threshold_bps: float | None = None,
) -> SpreadStats | None:
    """
    Статистика спреда за последние period_sec секунд.
    threshold_bps: если задан, считает % времени когда spread > threshold.
    """
    sym = symbol.upper()
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - int(period_sec * 1000)

    with _lock:
        buf = _buffers.get(sym)
        if buf is None or len(buf) == 0:
            return None
        ticks = [t for t in buf if t.timestamp_ms >= since_ms]

    if not ticks:
        return None

    spreads = [t.spread_bps for t in ticks if t.spread_bps is not None]
    last = ticks[-1]

    if not spreads:
        return SpreadStats(
            symbol=sym,
            period_sec=period_sec,
            ticks_count=len(ticks),
            avg_spread_bps=None,
            min_spread_bps=None,
            max_spread_bps=None,
            std_spread_bps=None,
            current_spread_bps=last.spread_bps,
            current_bid=last.bid,
            current_ask=last.ask,
            current_mid=last.mid,
            pct_above_threshold=None,
        )

    avg = sum(spreads) / len(spreads)
    min_s = min(spreads)
    max_s = max(spreads)
    # Sample variance (÷(n-1)) — unbiased estimator
    if len(spreads) > 1:
        variance = sum((s - avg) ** 2 for s in spreads) / (len(spreads) - 1)
    else:
        variance = 0.0
    std = math.sqrt(variance)

    pct_above: float | None = None
    if threshold_bps is not None:
        above_count = sum(1 for s in spreads if s >= threshold_bps)
        pct_above = 100.0 * above_count / len(spreads)

    return SpreadStats(
        symbol=sym,
        period_sec=period_sec,
        ticks_count=len(ticks),
        avg_spread_bps=avg,
        min_spread_bps=min_s,
        max_spread_bps=max_s,
        std_spread_bps=std,
        current_spread_bps=last.spread_bps,
        current_bid=last.bid,
        current_ask=last.ask,
        current_mid=last.mid,
        pct_above_threshold=pct_above,
    )


def get_tracked_symbols() -> list[str]:
    """Список символов с данными в буфере."""
    with _lock:
        return [sym for sym, buf in _buffers.items() if len(buf) > 0]


def clear(symbol: str | None = None) -> None:
    """Очистить буфер (один символ или все)."""
    with _lock:
        if symbol is None:
            _buffers.clear()
        else:
            _buffers.pop(symbol.upper(), None)


# --- Подписки (для SSE push) ---

def subscribe(symbol: str, callback: Any) -> None:
    """Подписаться на обновления спреда для символа."""
    sym = symbol.upper()
    with _sub_lock:
        if sym not in _subscribers:
            _subscribers[sym] = []
        _subscribers[sym].append(callback)


def unsubscribe(symbol: str, callback: Any) -> None:
    """Отписаться от обновлений."""
    sym = symbol.upper()
    with _sub_lock:
        subs = _subscribers.get(sym)
        if subs:
            try:
                subs.remove(callback)
            except ValueError:
                pass
            if not subs:
                del _subscribers[sym]


def _notify_subscribers(symbol: str, tick: SpreadTick) -> None:
    """Уведомить подписчиков о новом тике."""
    with _sub_lock:
        subs = _subscribers.get(symbol)
        if not subs:
            return
        # Копируем список чтобы не держать лок при вызове callbacks
        subs_copy = list(subs)
    for cb in subs_copy:
        try:
            cb(symbol, tick)
        except Exception:
            pass
