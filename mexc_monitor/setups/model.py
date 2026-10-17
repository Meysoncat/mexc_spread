"""Сетап — торговая идея для внешнего исполнителя. Схема ``setup/v1``.

Сетап описывает *что* делать (ноги: биржа, символ API, сторона, цена), *почему*
(метрики, заметки) и *сколько это стоит* (edge после комиссий, score). Как
исполнять, решает потребитель.

Две политики жизни:
  * ``conditional`` (арбитраж): живёт, пока детектор видит условие; закрывается,
    когда edge ушёл, нога пропала или данные устарели;
  * ``sticky`` (направленные идеи): разовый сигнал со стопом и целями; живёт до
    срока ``valid_until`` или пока цена не дошла до стопа/цели — исход
    фиксируется для статистики.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA = "setup/v1"

ARBITRAGE = "arbitrage"
DIRECTIONAL = "directional"

# type -> (category, policy)
SETUP_TYPES: dict[str, tuple[str, str]] = {
    "cross_perp": (ARBITRAGE, "conditional"),
    "funding_arb": (ARBITRAGE, "conditional"),
    "basis": (ARBITRAGE, "conditional"),
    "lead_lag": (DIRECTIONAL, "sticky"),
    "breakout": (DIRECTIONAL, "sticky"),
    "momentum": (DIRECTIONAL, "sticky"),
    "funding_squeeze": (DIRECTIONAL, "sticky"),
    "btc_lag": (DIRECTIONAL, "sticky"),
}


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _r(x: float | None, nd: int = 4) -> float | None:
    if x is None:
        return None
    return round(float(x), nd)


def _rp(x: float | None) -> float | None:
    """Цена: 8 значащих цифр (для монет за доли цента важны младшие разряды)."""
    if x is None:
        return None
    return float(f"{x:.8g}")


@dataclass(slots=True)
class Leg:
    venue: str
    market: str  # "perp" | "spot"
    symbol: str  # символ в API биржи
    side: str  # "buy" | "sell"
    price: float  # ориентир входа: ask для buy, bid для sell (за единицу инструмента)
    price_per_coin: float  # та же цена за одну монету базового актива
    l1_qty: float | None = None  # монет на лучшей цене
    l1_notional_usd: float | None = None
    taker_fee_bps: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "market": self.market,
            "symbol": self.symbol,
            "side": self.side,
            "price": _rp(self.price),
            "price_per_coin": _rp(self.price_per_coin),
            "l1_qty": _r(self.l1_qty, 6),
            "l1_notional_usd": _r(self.l1_notional_usd, 2),
            "taker_fee_bps": _r(self.taker_fee_bps, 2),
        }


@dataclass(slots=True)
class Candidate:
    """То, что детектор видит прямо сейчас. Реестр решает: открыть/обновить/закрыть."""

    key: str  # стабильный ключ идеи (тип:база:биржи:направление)
    type: str
    base: str
    legs: list[Leg]
    score: float  # 0..100
    edge_bps: float | None  # ожидаемая прибыль после издержек (арбитраж), bps
    opens: bool = True  # проходит порог открытия (иначе — только удерживает уже открытый)
    direction: str | None = None  # "long" | "short" для направленных
    entry: float | None = None  # за одну монету
    stop: float | None = None
    targets: list[float] = field(default_factory=list)
    horizon_sec: int = 3600
    ttl_sec: int | None = None  # sticky: срок жизни идеи
    data_age_sec: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    track_venue: str | None = None  # где отслеживать цену для исхода (sticky)

    @property
    def risk_reward(self) -> float | None:
        if self.entry is None or self.stop is None or not self.targets:
            return None
        risk = abs(self.entry - self.stop)
        return abs(self.targets[0] - self.entry) / risk if risk > 0 else None


@dataclass(slots=True)
class Setup:
    candidate: Candidate
    instance: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    status: str = "active"
    opened_at: float = 0.0
    updated_at: float = 0.0
    closed_at: float | None = None
    close_reason: str | None = None
    valid_until: float | None = None
    # статистика жизни
    max_edge_bps: float | None = None
    max_score: float = 0.0
    mfe_pct: float = 0.0  # максимальное движение в пользу (sticky)
    mae_pct: float = 0.0  # максимальное движение против (sticky)
    targets_hit: int = 0
    last_price: float | None = None
    outcome: dict[str, Any] | None = None

    @property
    def key(self) -> str:
        return self.candidate.key

    @property
    def type(self) -> str:
        return self.candidate.type

    @property
    def category(self) -> str:
        return SETUP_TYPES[self.candidate.type][0]

    @property
    def policy(self) -> str:
        return SETUP_TYPES[self.candidate.type][1]

    def to_dict(self) -> dict[str, Any]:
        c = self.candidate
        d: dict[str, Any] = {
            "schema": SCHEMA,
            "id": c.key,
            "instance": self.instance,
            "type": c.type,
            "category": self.category,
            "status": self.status,
            "base": c.base,
            "direction": c.direction,
            "legs": [leg.to_dict() for leg in c.legs],
            "entry": _rp(c.entry),
            "stop": _rp(c.stop),
            "targets": [_rp(t) for t in c.targets],
            "risk_reward": _r(c.risk_reward, 2),
            "edge_bps": _r(c.edge_bps, 2),
            "score": _r(c.score, 1),
            "horizon_sec": c.horizon_sec,
            "valid_until": iso(self.valid_until),
            "opened_at": iso(self.opened_at),
            "updated_at": iso(self.updated_at),
            "closed_at": iso(self.closed_at),
            "close_reason": self.close_reason,
            "data_age_ms": int(c.data_age_sec * 1000),
            "metrics": {k: (_r(v, 6) if isinstance(v, float) else v) for k, v in c.metrics.items()},
            "notes": list(c.notes),
        }
        if self.outcome is not None:
            d["outcome"] = self.outcome
        return d
