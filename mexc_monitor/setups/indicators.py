"""Индикаторы по 1m-свечам для направленных детекторов."""

from __future__ import annotations

import math
from collections.abc import Sequence

from mexc_monitor.hub.models import Candle


def atr(candles: Sequence[Candle], n: int = 14) -> float | None:
    if len(candles) < n + 1:
        return None
    trs = []
    for prev, c in zip(candles[-n - 1 : -1], candles[-n:]):
        trs.append(max(c.h - c.l, abs(c.h - prev.c), abs(c.l - prev.c)))
    return sum(trs) / n


def avg_volume(candles: Sequence[Candle]) -> float:
    return sum(c.v for c in candles) / len(candles) if candles else 0.0


def live_volume(c: Candle, now_ms: int) -> float:
    """Объём незакрытой свечи, пересчитанный на полную минуту (не меньше 25% прошедшего)."""
    if c.closed:
        return c.v
    frac = min(max((now_ms - c.t) / 60_000, 0.25), 1.0)
    return c.v / frac


def returns(closes: Sequence[float]) -> list[float]:
    return [b / a - 1.0 for a, b in zip(closes[:-1], closes[1:]) if a > 0]


def corr_beta(x: Sequence[float], y: Sequence[float]) -> tuple[float, float] | None:
    """Корреляция и бета y по x (y = beta·x)."""
    n = min(len(x), len(y))
    if n < 10:
        return None
    x, y = x[-n:], y[-n:]
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = sum((a - mx) ** 2 for a in x)
    vy = sum((b - my) ** 2 for b in y)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy), cov / vx


def stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def volume_factor(volume_24h_usd: float | None) -> float:
    """$1M → 0.3, $100M+ → 1.0; неизвестно → 0.5."""
    if volume_24h_usd is None:
        return 0.5
    if volume_24h_usd <= 1e6:
        return 0.3
    return min(1.0, 0.3 + 0.7 * math.log10(volume_24h_usd / 1e6) / 2.0)
