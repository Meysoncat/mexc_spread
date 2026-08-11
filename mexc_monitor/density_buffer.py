"""Density Buffer — in-memory ring buffer для истории плотности стакана.

Хранит snapshots стен (wall levels) по каждому символу за последние N минут.
Используется для:
  - графиков изменения нотации уровня во времени
  - детекции появления/исчезновения стен
  - статистики плотности за период
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DensitySnapshot:
    """Один snapshot плотности стакана."""
    timestamp_ms: int
    symbol: str
    total_bid_notional: float
    total_ask_notional: float
    bid_ask_ratio: float
    wall_count: int
    largest_bid_notional: float
    largest_ask_notional: float


@dataclass(frozen=True, slots=True)
class WallChange:
    """Обнаруженное изменение стены."""
    timestamp_ms: int
    symbol: str
    side: str  # "bid" | "ask"
    price: float
    notional_usdt: float
    change_type: str  # "appeared" | "disappeared" | "grew" | "shrunk"
    ratio_to_median: float


# Максимальная длина буфера на символ
_MAX_SNAPSHOTS = 360  # ~30 мин при polling каждые 5 сек
_MAX_AGE_SEC = 1800.0  # 30 минут

_lock = threading.Lock()
_buffers: dict[str, deque[DensitySnapshot]] = {}
_wall_buffers: dict[str, deque[WallChange]] = {}


def push_snapshot(snapshot: DensitySnapshot) -> None:
    """Добавить snapshot в буфер."""
    with _lock:
        buf = _buffers.get(snapshot.symbol)
        if buf is None:
            buf = deque(maxlen=_MAX_SNAPSHOTS)
            _buffers[snapshot.symbol] = buf
        buf.append(snapshot)
        # Очистка старых
        cutoff = int((time.time() - _MAX_AGE_SEC) * 1000)
        while buf and buf[0].timestamp_ms < cutoff:
            buf.popleft()


def push_wall_change(change: WallChange) -> None:
    """Добавить событие изменения стены."""
    with _lock:
        buf = _wall_buffers.get(change.symbol)
        if buf is None:
            buf = deque(maxlen=500)
            _wall_buffers[change.symbol] = buf
        buf.append(change)
        # Очистка старых
        cutoff = int((time.time() - _MAX_AGE_SEC) * 1000)
        while buf and buf[0].timestamp_ms < cutoff:
            buf.popleft()


def get_history(
    symbol: str,
    *,
    since_ms: int | None = None,
    max_points: int = 1000,
) -> list[DensitySnapshot]:
    """Получить историю плотности для символа."""
    with _lock:
        buf = _buffers.get(symbol)
        if not buf:
            return []
        out = list(buf)
        if since_ms is not None:
            out = [s for s in out if s.timestamp_ms >= since_ms]
        if len(out) > max_points:
            out = out[-max_points:]
        return out


def get_wall_changes(
    symbol: str,
    *,
    since_ms: int | None = None,
    limit: int = 100,
) -> list[WallChange]:
    """Получить историю изменений стен."""
    with _lock:
        buf = _wall_buffers.get(symbol)
        if not buf:
            return []
        out = list(buf)
        if since_ms is not None:
            out = [c for c in out if c.timestamp_ms >= since_ms]
        return out[-limit:]


def get_latest(symbol: str) -> DensitySnapshot | None:
    """Получить последний snapshot."""
    with _lock:
        buf = _buffers.get(symbol)
        return buf[-1] if buf else None


def detect_changes(
    symbol: str,
    current_walls: list[dict],
    *,
    min_notional_usdt: float = 50_000,
) -> list[WallChange]:
    """Сравнить текущие стены с предыдущим snapshot и обнаружить изменения.

    Args:
        symbol: символ
        current_walls: текущие стены (как из detect_walls)
        min_notional_usdt: минимальная нотация для отслеживания

    Returns:
        Список обнаруженных изменений
    """
    prev = get_latest(symbol)
    if prev is None:
        return []

    now_ms = int(time.time() * 1000)
    changes: list[WallChange] = []

    # Простая логика: сравниваем largest_bid/ask с текущими
    for w in current_walls:
        if w.get("notional_usdt", 0) < min_notional_usdt:
            continue

        notional = w["notional_usdt"]
        side = w.get("side", "bid")

        if side == "bid":
            prev_notional = prev.largest_bid_notional
        else:
            prev_notional = prev.largest_ask_notional

        if prev_notional <= 0 and notional > 0:
            change_type = "appeared"
        elif prev_notional > 0 and notional <= 0:
            change_type = "disappeared"
        elif notional > prev_notional * 1.5:
            change_type = "grew"
        elif notional < prev_notional * 0.5:
            change_type = "shrunk"
        else:
            continue

        changes.append(WallChange(
            timestamp_ms=now_ms,
            symbol=symbol,
            side=side,
            price=w.get("price", 0),
            notional_usdt=notional,
            change_type=change_type,
            ratio_to_median=w.get("ratio_to_median", 0),
        ))

    return changes


def snapshot_to_dict(s: DensitySnapshot) -> dict:
    """DensitySnapshot → dict для JSON."""
    return {
        "timestamp_ms": s.timestamp_ms,
        "symbol": s.symbol,
        "total_bid_notional": round(s.total_bid_notional, 2),
        "total_ask_notional": round(s.total_ask_notional, 2),
        "bid_ask_ratio": round(s.bid_ask_ratio, 3),
        "wall_count": s.wall_count,
        "largest_bid_notional": round(s.largest_bid_notional, 2),
        "largest_ask_notional": round(s.largest_ask_notional, 2),
    }


def wall_change_to_dict(c: WallChange) -> dict:
    """WallChange → dict для JSON."""
    return {
        "timestamp_ms": c.timestamp_ms,
        "symbol": c.symbol,
        "side": c.side,
        "price": c.price,
        "notional_usdt": round(c.notional_usdt, 2),
        "change_type": c.change_type,
        "ratio_to_median": round(c.ratio_to_median, 1),
    }
