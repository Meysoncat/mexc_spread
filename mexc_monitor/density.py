"""Order Book Density Analysis — поиск стен, статистика плотности, сравнение.

Анализирует L2 стакан и находит:
- Стены (уровни с аномально крупными ордерами)
- Статистику плотности (total bid/ask, ratio, avg notional)
- Дисбаланс ликвидности
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WallLevel:
    """Обнаруженная стена в стакане."""
    side: str  # "bid" | "ask"
    price: float
    qty: float
    notional_usdt: float
    ratio_to_median: float  # во сколько раз больше медианы


@dataclass(frozen=True)
class DensityStats:
    """Агрегированная статистика плотности стакана."""
    total_bid_notional: float
    total_ask_notional: float
    bid_ask_ratio: float  # total_bid / total_ask (>1 = больше bid)
    avg_level_notional: float
    median_level_notional: float
    levels_count: int
    bid_levels: int
    ask_levels: int
    imbalance: str  # "bid_heavy" | "ask_heavy" | "balanced"


def _notional(price: float, qty: float) -> float:
    """Нотация уровня в USDT."""
    return price * qty


def _median(values: list[float]) -> float:
    """Медиана списка."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _parse_levels(levels: list[Any]) -> list[tuple[float, float]]:
    """Нормализовать уровни стакана к [(price, qty), ...]."""
    out = []
    for lv in levels:
        if isinstance(lv, (list, tuple)) and len(lv) >= 2:
            try:
                p, q = float(lv[0]), float(lv[1])
                if p > 0 and q >= 0:
                    out.append((p, q))
            except (TypeError, ValueError):
                continue
        elif isinstance(lv, dict):
            try:
                p = float(lv.get("price", 0))
                q = float(lv.get("qty", 0))
                if p > 0 and q >= 0:
                    out.append((p, q))
            except (TypeError, ValueError):
                continue
    return out


def detect_walls(
    bids: list[Any],
    asks: list[Any],
    *,
    multiplier: float = 5.0,
    min_notional_usdt: float = 10_000,
) -> list[WallLevel]:
    """Обнаружить стены — уровни с нотацией ≥ median × multiplier.

    Args:
        bids: уровни bid (как из API: [[price, qty], ...] или [{price, qty}, ...])
        asks: уровни ask
        multiplier: порог в разах от медианы
        min_notional_usdt: минимальная нотация в USDT (фильтр шума)

    Returns:
        Список WallLevel, отсортированный по убыванию notional
    """
    bid_levels = _parse_levels(bids)
    ask_levels = _parse_levels(asks)

    all_notional = []
    for p, q in bid_levels + ask_levels:
        n = _notional(p, q)
        if n > 0:
            all_notional.append(n)

    if not all_notional:
        return []

    med = _median(all_notional)
    if med <= 0:
        return []

    threshold = med * multiplier
    walls: list[WallLevel] = []

    for p, q in bid_levels:
        n = _notional(p, q)
        if n >= threshold and n >= min_notional_usdt:
            walls.append(WallLevel(
                side="bid",
                price=p,
                qty=q,
                notional_usdt=n,
                ratio_to_median=n / med,
            ))

    for p, q in ask_levels:
        n = _notional(p, q)
        if n >= threshold and n >= min_notional_usdt:
            walls.append(WallLevel(
                side="ask",
                price=p,
                qty=q,
                notional_usdt=n,
                ratio_to_median=n / med,
            ))

    walls.sort(key=lambda w: w.notional_usdt, reverse=True)
    return walls


def compute_density_stats(
    bids: list[Any],
    asks: list[Any],
) -> DensityStats:
    """Вычислить статистику плотности стакана.

    Args:
        bids: уровни bid
        asks: уровни ask

    Returns:
        DensityStats с агрегированными метриками
    """
    bid_levels = _parse_levels(bids)
    ask_levels = _parse_levels(asks)

    bid_notional = [_notional(p, q) for p, q in bid_levels]
    ask_notional = [_notional(p, q) for p, q in ask_levels]

    total_bid = sum(bid_notional)
    total_ask = sum(ask_notional)

    all_notional = bid_notional + ask_notional
    total = total_bid + total_ask

    ratio = total_bid / total_ask if total_ask > 0 else float("inf") if total_bid > 0 else 1.0
    avg = total / len(all_notional) if all_notional else 0.0
    med = _median(all_notional)

    if ratio > 1.5:
        imbalance = "bid_heavy"
    elif ratio < 0.67:
        imbalance = "ask_heavy"
    else:
        imbalance = "balanced"

    return DensityStats(
        total_bid_notional=total_bid,
        total_ask_notional=total_ask,
        bid_ask_ratio=ratio,
        avg_level_notional=avg,
        median_level_notional=med,
        levels_count=len(all_notional),
        bid_levels=len(bid_levels),
        ask_levels=len(ask_levels),
        imbalance=imbalance,
    )


def wall_to_dict(w: WallLevel) -> dict:
    """WallLevel → dict для JSON-сериализации."""
    return {
        "side": w.side,
        "price": w.price,
        "qty": w.qty,
        "notional_usdt": round(w.notional_usdt, 2),
        "ratio_to_median": round(w.ratio_to_median, 1),
    }


def stats_to_dict(s: DensityStats) -> dict:
    """DensityStats → dict для JSON-сериализации."""
    return {
        "total_bid_notional": round(s.total_bid_notional, 2),
        "total_ask_notional": round(s.total_ask_notional, 2),
        "bid_ask_ratio": round(s.bid_ask_ratio, 3),
        "avg_level_notional": round(s.avg_level_notional, 2),
        "median_level_notional": round(s.median_level_notional, 2),
        "levels_count": s.levels_count,
        "bid_levels": s.bid_levels,
        "ask_levels": s.ask_levels,
        "imbalance": s.imbalance,
    }
