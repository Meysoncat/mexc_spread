"""MetaScalp Density Scanner — связка стакана MetaScalp с density.py.

Превращает снимок стакана MetaScalp (через :class:`MetaScalpClient`) в
структурированный :class:`DensityScan` с стенами/плотностью/спредом — ровно
те метрики, которые нужны для стратегии ProBoyScalp (см.
``docs/PROBOYSCALP_METHOD.md``).

Это тонкий слой над СУЩЕСТВУЮЩИМ ``mexc_monitor/density.py`` (exchange-agnostic)
и ``mexc_monitor/density_buffer.py`` (история стен). Density.py не модифицируется
— сюда только прокидываются bids/asks в формате ``[{"price":..,"qty":..}, ...]``.

Пороги по умолчанию под стратегию ProBoyScalp:
- ``min_notional_usdt=1000`` — он ищет плотности 1-2k USDT (не 10k как для blue-chips)
- ``multiplier=5.0`` — аномальный уровень относительно медианы стакана

Используется из :mod:`mexc_monitor.metascalp.signal_worker` и из эндпоинтов
``/api/metascalp/density/*`` в ``backend/main.py``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mexc_monitor.density import (
    DensityStats,
    WallLevel,
    compute_density_stats,
    detect_walls,
)
from mexc_monitor.density_buffer import (
    DensitySnapshot,
    detect_changes,
    push_snapshot,
    push_wall_change,
)

if TYPE_CHECKING:
    from mexc_monitor.metascalp.client import MetaScalpClient
    from mexc_monitor.metascalp.models import MetaScalpOrderbookSnapshot

logger = logging.getLogger(__name__)


# Пороги по умолчанию для стратегии ProBoyScalp (low-cap MEXC alts)
DEFAULT_MIN_NOTIONAL_USDT: float = 1_000.0
DEFAULT_MULTIPLIER: float = 5.0
# Ниже этого спреда (bps) собирать нечего — его уже съели
DEFAULT_MIN_SPREAD_BPS: float = 5.0


@dataclass
class DensityScan:
    """Результат сканирования одного тикера под стратегию ProBoyScalp."""

    conn_id: str
    ticker: str
    timestamp_ms: int
    best_bid: float
    best_ask: float
    mid: float
    spread_bps: float
    walls: list[WallLevel] = field(default_factory=list)
    stats: DensityStats | None = None
    # Top-3 стен по notional (для UI / алерта)
    top_walls: list[dict[str, Any]] = field(default_factory=list)
    # True, если сканер счёл тикер «кандидатом» (есть стены + достаточный спред)
    is_candidate: bool = False
    # Причина, если не кандидат (для отладки)
    skip_reason: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-сериализация для эндпоинтов."""
        from mexc_monitor.density import stats_to_dict, wall_to_dict

        return {
            "conn_id": self.conn_id,
            "ticker": self.ticker,
            "timestamp_ms": self.timestamp_ms,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid": round(self.mid, 8),
            "spread_bps": round(self.spread_bps, 2),
            "walls": [wall_to_dict(w) for w in self.walls],
            "stats": stats_to_dict(self.stats) if self.stats else None,
            "top_walls": self.top_walls,
            "is_candidate": self.is_candidate,
            "skip_reason": self.skip_reason,
            "error": self.error,
        }


class MetaScalpDensityScanner:
    """Сканер плотностей стакана MetaScalp.

    Потокобезопасный только в плане вызова stateless-функций density.py;
    сам сканер не имеет состояния (он просто перегоняет snapshot → DensityScan).
    История хранится в глобальном density_buffer.
    """

    def __init__(
        self,
        client: "MetaScalpClient",
        *,
        min_notional_usdt: float = DEFAULT_MIN_NOTIONAL_USDT,
        multiplier: float = DEFAULT_MULTIPLIER,
        min_spread_bps: float = DEFAULT_MIN_SPREAD_BPS,
    ) -> None:
        self._client = client
        self._min_notional_usdt = min_notional_usdt
        self._multiplier = multiplier
        self._min_spread_bps = min_spread_bps

    # ─── Публичный API ────────────────────────────────────────────────────

    def scan_ticker(
        self,
        conn_id: str,
        ticker: str,
        *,
        push_history: bool = True,
    ) -> DensityScan:
        """Отсканировать один тикер: стакан → стены + статистика + спред.

        Args:
            conn_id: ID подключения MetaScalp.
            ticker: Тикер (например, ``"LYN_USDT"``).
            push_history: Если True — сохранить snapshot в density_buffer и
                сравнить с предыдущим (детекция appeared/disappeared/grew/shrunk).
        """
        ticker_norm = ticker.strip().upper()
        now_ms = int(time.time() * 1000)
        empty = DensityScan(
            conn_id=conn_id,
            ticker=ticker_norm,
            timestamp_ms=now_ms,
            best_bid=0.0,
            best_ask=0.0,
            mid=0.0,
            spread_bps=0.0,
        )

        # 1. Стянуть стакан из MetaScalp
        try:
            snap = self._client.orderbook_snapshot(conn_id, ticker_norm)
        except Exception as e:  # client возвращает error dict, но подстрахуемся
            logger.warning("density_scanner: orderbook fetch failed for %s: %s", ticker_norm, e)
            empty.error = f"orderbook fetch failed: {e}"
            return empty

        if snap is None:
            empty.error = "orderbook snapshot is None"
            return empty

        return self._analyze_snapshot(
            conn_id=conn_id,
            ticker=ticker_norm,
            snap=snap,
            now_ms=now_ms,
            push_history=push_history,
        )

    def scan_watchlist(
        self,
        conn_id: str,
        tickers: list[str],
        *,
        only_candidates: bool = True,
        push_history: bool = True,
    ) -> list[DensityScan]:
        """Прогнать список тикеров, вернуть отсортированный по «интересности».

        Args:
            conn_id: ID подключения.
            tickers: Список тикеров.
            only_candidates: Если True — вернуть только кандидатов (с стенами
                и достаточным спредом). Иначе — все, включая пропущенные.
            push_history: Сохранять в density_buffer.
        """
        results: list[DensityScan] = []
        for tk in tickers:
            tk = tk.strip()
            if not tk:
                continue
            try:
                scan = self.scan_ticker(conn_id, tk, push_history=push_history)
            except Exception as e:
                logger.warning("density_scanner: scan failed for %s: %s", tk, e)
                scan = DensityScan(
                    conn_id=conn_id, ticker=tk.upper(),
                    timestamp_ms=int(time.time() * 1000),
                    best_bid=0.0, best_ask=0.0, mid=0.0, spread_bps=0.0,
                    error=f"scan exception: {e}",
                )
            results.append(scan)

        if only_candidates:
            results = [r for r in results if r.is_candidate]

        # Сортировка: больше стен → выше; при равенстве — шире спред
        results.sort(
            key=lambda s: (len(s.walls), s.spread_bps, s.largest_wall_notional()),
            reverse=True,
        )
        return results

    # ─── Внутренние ───────────────────────────────────────────────────────

    def _analyze_snapshot(
        self,
        *,
        conn_id: str,
        ticker: str,
        snap: "MetaScalpOrderbookSnapshot",
        now_ms: int,
        push_history: bool,
    ) -> DensityScan:
        # Преобразовать MetaScalpOrderbookLevel → формат density.py
        # density._parse_levels понимает словари {"price":..,"qty":..}
        bids = [{"price": lv.price, "qty": lv.size} for lv in snap.bids]
        asks = [{"price": lv.price, "qty": lv.size} for lv in snap.asks]

        best_bid = snap.best_bid or (bids[0]["price"] if bids else 0.0)
        best_ask = snap.best_ask or (asks[0]["price"] if asks else 0.0)
        mid = (best_bid + best_ask) / 2.0 if (best_bid + best_ask) > 0 else 0.0
        spread_bps = (
            10_000.0 * (best_ask - best_bid) / mid if mid > 0 else 0.0
        )

        walls = detect_walls(
            bids,
            asks,
            multiplier=self._multiplier,
            min_notional_usdt=self._min_notional_usdt,
        )
        stats = compute_density_stats(bids, asks)
        top_walls = [self._wall_summary(w) for w in walls[:3]]

        # Кандидат ли: есть стены + достаточный спред
        is_candidate = bool(walls) and spread_bps >= self._min_spread_bps
        skip_reason = ""
        if not walls:
            skip_reason = "no walls above threshold"
        elif spread_bps < self._min_spread_bps:
            skip_reason = f"spread {spread_bps:.1f}bps < {self._min_spread_bps}bps"

        scan = DensityScan(
            conn_id=conn_id,
            ticker=ticker,
            timestamp_ms=now_ms,
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread_bps=spread_bps,
            walls=walls,
            stats=stats,
            top_walls=top_walls,
            is_candidate=is_candidate,
            skip_reason=skip_reason,
        )

        if push_history:
            self._push_history(scan)

        return scan

    def _push_history(self, scan: DensityScan) -> None:
        """Сохранить snapshot + задетектить изменения стен."""
        largest_bid = max(
            (w.notional_usdt for w in scan.walls if w.side == "bid"),
            default=0.0,
        )
        largest_ask = max(
            (w.notional_usdt for w in scan.walls if w.side == "ask"),
            default=0.0,
        )
        bid_total = scan.stats.total_bid_notional if scan.stats else 0.0
        ask_total = scan.stats.total_ask_notional if scan.stats else 0.0
        ratio = scan.stats.bid_ask_ratio if scan.stats else 1.0

        snap = DensitySnapshot(
            timestamp_ms=scan.timestamp_ms,
            symbol=scan.ticker,
            total_bid_notional=bid_total,
            total_ask_notional=ask_total,
            bid_ask_ratio=ratio,
            wall_count=len(scan.walls),
            largest_bid_notional=largest_bid,
            largest_ask_notional=largest_ask,
        )
        push_snapshot(snap)

        # Сравнить с предыдущим snapshot → WallChange events
        from mexc_monitor.density import wall_to_dict

        walls_as_dicts = [wall_to_dict(w) for w in scan.walls]
        try:
            changes = detect_changes(
                scan.ticker,
                walls_as_dicts,
                min_notional_usdt=self._min_notional_usdt,
            )
            for ch in changes:
                push_wall_change(ch)
        except Exception as e:
            logger.debug("density_scanner: detect_changes failed for %s: %s", scan.ticker, e)

    @staticmethod
    def _wall_summary(w: WallLevel) -> dict[str, Any]:
        """Краткая сводка стены для UI/алерта."""
        return {
            "side": w.side,
            "price": w.price,
            "notional_usdt": round(w.notional_usdt, 2),
            "ratio_to_median": round(w.ratio_to_median, 1),
        }


def largest_wall_notional(self: DensityScan) -> float:
    """Хелпер: нотация крупнейшей стены (для сортировки)."""
    if not self.walls:
        return 0.0
    return max(w.notional_usdt for w in self.walls)


# Добавляем метод в DensityScan (monkeypatch — чтобы не менять сам dataclass)
DensityScan.largest_wall_notional = largest_wall_notional  # type: ignore[attr-defined]
