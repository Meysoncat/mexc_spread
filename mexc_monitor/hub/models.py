"""Модели рыночного состояния MarketHub.

Все цены в представлениях (PerpQuote/SpotQuote) — за одну монету базового
актива, все объёмы — в монетах базового актива или в USD. Биржевые единицы
(контракты, «1000PEPE») переводятся при сборке представления.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class InstrumentMeta:
    """Справочник инструмента биржи (обновляется редко)."""

    venue: str
    key: str  # нормализованный ключ (BTCUSDT), см. symbols.norm_key
    native: str  # символ в API биржи (BTC-USDT-SWAP) — для внешних исполнителей
    base: str
    quote: str
    price_mult: float = 1.0  # цена инструмента = цена монеты × price_mult
    contract_size: float = 1.0  # сколько «единиц инструмента» в одном контракте
    active: bool = True
    funding_interval_h: float | None = None

    @property
    def qty_to_base(self) -> float:
        """Множитель: количество в стакане → монеты базового актива."""
        return self.contract_size * self.price_mult


@dataclass(slots=True)
class PerpStats:
    """Медленные метрики perp: funding, OI, объём (REST-опрос)."""

    funding_rate: float | None = None  # доля за один период funding
    funding_interval_h: float | None = None
    next_funding_ms: int | None = None
    open_interest_usd: float | None = None
    volume_24h_usd: float | None = None
    mark_price: float | None = None  # за единицу инструмента (до price_mult)


@dataclass(slots=True)
class PerpQuote:
    """Лучшие цены perp в общих единицах + метрики для детекторов."""

    venue: str
    key: str
    native: str
    base: str
    quote: str
    bid: float
    ask: float
    bid_qty: float | None  # монет базового актива на лучшей цене; None — неизвестно
    ask_qty: float | None
    age_sec: float  # возраст данных источника
    price_mult: float = 1.0  # цена инструмента = bid/ask × price_mult
    indicative: bool = False  # цены не из стакана (например, impact-цены)
    funding_rate: float | None = None
    funding_interval_h: float = 8.0
    next_funding_ms: int | None = None
    open_interest_usd: float | None = None
    volume_24h_usd: float | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        m = self.mid
        return (self.ask - self.bid) / m * 1e4 if m > 0 else 0.0

    @property
    def bid_notional(self) -> float | None:
        return self.bid_qty * self.bid if self.bid_qty is not None else None

    @property
    def ask_notional(self) -> float | None:
        return self.ask_qty * self.ask if self.ask_qty is not None else None

    @property
    def funding_per_hour(self) -> float | None:
        if self.funding_rate is None or self.funding_interval_h <= 0:
            return None
        return self.funding_rate / self.funding_interval_h


@dataclass(slots=True)
class SpotQuote:
    venue: str
    key: str
    native: str
    base: str
    quote: str
    bid: float
    ask: float
    bid_qty: float | None
    ask_qty: float | None
    age_sec: float
    volume_24h_usd: float | None = None
    price_mult: float = 1.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        m = self.mid
        return (self.ask - self.bid) / m * 1e4 if m > 0 else 0.0

    @property
    def bid_notional(self) -> float | None:
        return self.bid_qty * self.bid if self.bid_qty is not None else None

    @property
    def ask_notional(self) -> float | None:
        return self.ask_qty * self.ask if self.ask_qty is not None else None


@dataclass(slots=True)
class Candle:
    """Свеча; цены за единицу инструмента, объём — в монетах (после qty_to_base)."""

    t: int  # открытие, мс UTC
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    v: float  # объём в монетах базового актива
    qv: float = 0.0  # объём в quote (USD)
    closed: bool = True


@dataclass(slots=True)
class SourceHealth:
    """Состояние одного источника данных (WS-фида или REST-задачи)."""

    name: str
    kind: str  # "ws" | "rest"
    ok: bool = False
    last_ok_mono: float = 0.0
    last_error: str | None = None
    consecutive_failures: int = 0
    items: int = 0
    extra: dict = field(default_factory=dict)
