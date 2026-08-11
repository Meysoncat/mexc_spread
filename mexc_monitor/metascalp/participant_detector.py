"""Participant Detector — детектор «участника» в ленте сделок MetaScalp.

Стратегия ProBoyScalp (см. ``docs/PROBOYSCALP_METHOD.md``) требует видеть
«участника» — кого-то, кто систематически прокидывает через стакан рыночные
ордера или переставляет крупные лимитки. Этот детектор ловит **первый паттерн**
(всплески однонаправленных рыночных ордеров) на основе потока сделок из
``ws_client.subscribe_trades``.

Концепция:
    - При каждой сделке считаем её «сторону» (buy / sell) и USDT-объём
      (price × qty).
    - Внутри скользящего окна ``window_sec`` агрегируем buy_vol и sell_vol.
    - Если一侧 доминирует с превышением порога → это сигнал участника.

Пороги по умолчанию (взяты из видео ProBoyScalp «Фулл гайд»):
    - ``spike_threshold_usdt=1000`` — всплеск за 60 сек = кандидат-сигнал
    - ``cluster_threshold_usdt=5000`` — кластер за 15 мин = устойчивый участник
      (по ProBoy: «если за 15 минут проходит объём от 5-10.000 долларов —
      это хороший минимум для торговли спреда»)

Точные имена полей trade-сообщения от MetaScalp неизвестны (нет живого
сэмпла), поэтому парсинг defensive: пробуем PascalCase / camelCase / lowercase.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Пороги по умолчанию для стратегии ProBoyScalp
DEFAULT_SPIKE_THRESHOLD_USDT: float = 1_000.0
DEFAULT_SPIKE_WINDOW_SEC: float = 60.0
DEFAULT_CLUSTER_THRESHOLD_USDT: float = 5_000.0
DEFAULT_CLUSTER_WINDOW_SEC: float = 900.0  # 15 минут
# Максимальный размер буфера сделок на тикер (защита от memory leak)
_MAX_TRADES_PER_TICKER = 5_000
# Минимальная доминация стороны, чтобы считать всплеск «участником»
# (иначе любой обычный поток заявок qualifies)
DEFAULT_DOMINATION_RATIO: float = 0.65


@dataclass
class ParticipantSignal:
    """Сигнал обнаруженного участника."""

    ticker: str
    timestamp_ms: int
    direction: str  # "buy" | "sell"
    spike_volume_usdt: float  # объём за короткое окно (spike)
    spike_window_sec: float
    cluster_volume_usdt: float  # объём за длинное окно (cluster)
    cluster_window_sec: float
    buy_vol_usdt: float
    sell_vol_usdt: float
    domination_ratio: float  # какая доля доминирующей стороны
    trade_count: int
    is_strong: bool  # True если cluster_threshold превышен

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "timestamp_ms": self.timestamp_ms,
            "direction": self.direction,
            "spike_volume_usdt": round(self.spike_volume_usdt, 2),
            "spike_window_sec": self.spike_window_sec,
            "cluster_volume_usdt": round(self.cluster_volume_usdt, 2),
            "cluster_window_sec": self.cluster_window_sec,
            "buy_vol_usdt": round(self.buy_vol_usdt, 2),
            "sell_vol_usdt": round(self.sell_vol_usdt, 2),
            "domination_ratio": round(self.domination_ratio, 3),
            "trade_count": self.trade_count,
            "is_strong": self.is_strong,
        }


class ParticipantDetector:
    """Детектор участников по потоку сделок.

    Потокобезопасный. Вызывай :meth:`on_trade` на каждое WS-сообщение
    типа trade; опрашивай :meth:`detect` для снятия сигнала.
    """

    def __init__(
        self,
        *,
        spike_threshold_usdt: float = DEFAULT_SPIKE_THRESHOLD_USDT,
        spike_window_sec: float = DEFAULT_SPIKE_WINDOW_SEC,
        cluster_threshold_usdt: float = DEFAULT_CLUSTER_THRESHOLD_USDT,
        cluster_window_sec: float = DEFAULT_CLUSTER_WINDOW_SEC,
        domination_ratio: float = DEFAULT_DOMINATION_RATIO,
    ) -> None:
        self._spike_thr = spike_threshold_usdt
        self._spike_win = spike_window_sec
        self._cluster_thr = cluster_threshold_usdt
        self._cluster_win = cluster_window_sec
        self._dom_ratio = domination_ratio
        # ticker → deque[(ts, side_norm, vol_usdt)]
        self._trades: dict[str, deque[tuple[float, str, float]]] = defaultdict(
            lambda: deque(maxlen=_MAX_TRADES_PER_TICKER)
        )
        self._lock = threading.Lock()

    # ─── Public API ───────────────────────────────────────────────────────

    def on_trade(self, ticker: str, side: str, price: float, qty: float) -> None:
        """Зарегистрировать одну сделку.

        Args:
            ticker: Тикер (LYN_USDT).
            side: Сторона ("Buy" / "Sell" / "buy" / "sell" / "BUY" / ...).
            price: Цена сделки.
            qty: Объём в базовой валюте.
        """
        if not ticker or price <= 0 or qty <= 0:
            return
        ticker_norm = ticker.strip().upper()
        side_norm = self._normalize_side(side)
        if side_norm not in ("buy", "sell"):
            return
        vol_usdt = price * qty
        now = time.time()
        with self._lock:
            buf = self._trades[ticker_norm]
            buf.append((now, side_norm, vol_usdt))

    def on_trade_payload(self, data: dict[str, Any]) -> None:
        """Колбэк для прямого использования в ws_bridge.

        Принимает payload trade-сообщения от MetaScalp (как есть из WS) и
        пытается извлечь поля defensive'но.
        """
        ticker = self._extract(data, "Ticker", "ticker", "Symbol", "symbol")
        side = self._extract(data, "Side", "side", "Type", "type")
        price = self._extract_float(
            data, "Price", "price", "TradePrice", "tradePrice"
        )
        qty = self._extract_float(
            data,
            "Qty",
            "qty",
            "Quantity",
            "quantity",
            "Size",
            "size",
            "Amount",
            "amount",
            "Volume",
            "volume",
        )
        if ticker is None or side is None or price is None or qty is None:
            logger.debug("participant_detector: incomplete trade payload: %s", data)
            return
        self.on_trade(str(ticker), str(side), float(price), float(qty))

    def detect(self, ticker: str) -> ParticipantSignal | None:
        """Вернуть сигнал участника, если есть, иначе None.

        Сигнал формируется, если:
        - за ``spike_window_sec`` прошло ≥ ``spike_threshold_usdt`` в одну сторону
        - И domination_ratio ≥ порога (одна сторона доминирует)
        """
        ticker_norm = ticker.strip().upper()
        now = time.time()
        spike_cutoff = now - self._spike_win
        cluster_cutoff = now - self._cluster_win

        with self._lock:
            buf = self._trades.get(ticker_norm)
            if not buf:
                return None
            # Копируем snapshot под локом, считаем без лока
            trades_snapshot = list(buf)

        # Чистим устаревшее прямо здесь (одновременно с подсчётом)
        buy_spike = sell_spike = 0.0
        buy_cluster = sell_cluster = 0.0
        spike_count = 0
        cluster_count = 0
        for ts, side, vol in trades_snapshot:
            if ts >= spike_cutoff:
                if side == "buy":
                    buy_spike += vol
                else:
                    sell_spike += vol
                spike_count += 1
            if ts >= cluster_cutoff:
                if side == "buy":
                    buy_cluster += vol
                else:
                    sell_cluster += vol
                cluster_count += 1

        # Определяем сторону-доминанта в коротком окне
        if buy_spike >= sell_spike:
            dom_side = "buy"
            dom_vol_spike = buy_spike
            other_vol_spike = sell_spike
        else:
            dom_side = "sell"
            dom_vol_spike = sell_spike
            other_vol_spike = buy_spike

        total_spike = buy_spike + sell_spike
        dom_ratio = (dom_vol_spike / total_spike) if total_spike > 0 else 0.0

        # Условие сигнала
        is_spike = dom_vol_spike >= self._spike_thr and dom_ratio >= self._dom_ratio
        if not is_spike:
            return None

        dom_vol_cluster = buy_cluster if dom_side == "buy" else sell_cluster
        is_strong = dom_vol_cluster >= self._cluster_thr

        return ParticipantSignal(
            ticker=ticker_norm,
            timestamp_ms=int(now * 1000),
            direction=dom_side,
            spike_volume_usdt=dom_vol_spike,
            spike_window_sec=self._spike_win,
            cluster_volume_usdt=dom_vol_cluster,
            cluster_window_sec=self._cluster_win,
            buy_vol_usdt=buy_spike,
            sell_vol_usdt=sell_spike,
            domination_ratio=dom_ratio,
            trade_count=spike_count,
            is_strong=is_strong,
        )

    def detect_all(self, tickers: list[str] | None = None) -> list[ParticipantSignal]:
        """Опросить список тикеров или все известные. Вернуть активные сигналы."""
        with self._lock:
            target_tickers = tickers if tickers is not None else list(self._trades.keys())
        out: list[ParticipantSignal] = []
        for tk in target_tickers:
            sig = self.detect(tk)
            if sig is not None:
                out.append(sig)
        # Сильные (cluster) — выше
        out.sort(key=lambda s: (s.is_strong, s.spike_volume_usdt), reverse=True)
        return out

    def get_volume_summary(self, ticker: str) -> dict[str, Any]:
        """Сводка объёмов по тикеру без формирования сигнала (для UI)."""
        ticker_norm = ticker.strip().upper()
        now = time.time()
        spike_cutoff = now - self._spike_win
        cluster_cutoff = now - self._cluster_win
        with self._lock:
            buf = self._trades.get(ticker_norm)
            trades_snapshot = list(buf) if buf else []

        buy_spike = sell_spike = buy_cluster = sell_cluster = 0.0
        for ts, side, vol in trades_snapshot:
            if ts >= spike_cutoff:
                if side == "buy":
                    buy_spike += vol
                else:
                    sell_spike += vol
            if ts >= cluster_cutoff:
                if side == "buy":
                    buy_cluster += vol
                else:
                    sell_cluster += vol
        return {
            "ticker": ticker_norm,
            "buy_vol_60s_usdt": round(buy_spike, 2),
            "sell_vol_60s_usdt": round(sell_spike, 2),
            "buy_vol_15m_usdt": round(buy_cluster, 2),
            "sell_vol_15m_usdt": round(sell_cluster, 2),
            "trade_count_60s": sum(
                1 for ts, _, _ in trades_snapshot if ts >= spike_cutoff
            ),
        }

    def cleanup_old(self) -> int:
        """Удалить сделки старше cluster_window. Вернуть кол-во удалённых."""
        cutoff = time.time() - self._cluster_win
        removed = 0
        with self._lock:
            for ticker, buf in list(self._trades.items()):
                while buf and buf[0][0] < cutoff:
                    buf.popleft()
                    removed += 1
                if not buf:
                    self._trades.pop(ticker, None)
        return removed

    def stats(self) -> dict[str, Any]:
        """Сводка по состоянию детектора (для /status)."""
        with self._lock:
            return {
                "tickers_tracked": len(self._trades),
                "total_trades_buffered": sum(len(b) for b in self._trades.values()),
                "spike_threshold_usdt": self._spike_thr,
                "spike_window_sec": self._spike_win,
                "cluster_threshold_usdt": self._cluster_thr,
                "cluster_window_sec": self._cluster_win,
                "domination_ratio": self._dom_ratio,
            }

    # ─── Internal helpers ─────────────────────────────────────────────────

    @staticmethod
    def _normalize_side(side: str | None) -> str:
        if not side:
            return ""
        s = str(side).strip().lower()
        if s in ("buy", "bid", "long", "b"):
            return "buy"
        if s in ("sell", "ask", "short", "s"):
            return "sell"
        return ""

    @staticmethod
    def _extract(data: dict[str, Any], *keys: str) -> Any:
        for k in keys:
            if k in data and data[k] not in (None, ""):
                return data[k]
        return None

    @staticmethod
    def _extract_float(data: dict[str, Any], *keys: str) -> float | None:
        v = ParticipantDetector._extract(data, *keys)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
