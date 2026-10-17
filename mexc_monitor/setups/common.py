"""Общие кирпичи детекторов: скоринг, ноги, EWMA-базлайны."""

from __future__ import annotations

import math
from dataclasses import dataclass

from mexc_monitor.hub.models import PerpQuote, SpotQuote
from mexc_monitor.setups.model import Leg


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def edge_score(edge: float, scale: float) -> float:
    """0..100, насыщается: edge = scale → 63, 2·scale → 86, 3·scale → 95."""
    if edge <= 0:
        return 0.0
    return 100.0 * (1.0 - math.exp(-edge / scale))


def liquidity_factor(l1_usd: float | None) -> float:
    """$100 → 0.3, $100k+ → 1.0; неизвестно → 0.6."""
    if l1_usd is None:
        return 0.6
    return clamp(0.3 + 0.7 * math.log10(max(l1_usd, 1.0) / 100.0) / 3.0, 0.3, 1.0)


def freshness_factor(age_sec: float, max_age_sec: float) -> float:
    return 1.0 - 0.5 * clamp(age_sec / max_age_sec if max_age_sec > 0 else 0.0, 0.0, 1.0)


def min_known(*vals: float | None) -> float | None:
    known = [v for v in vals if v is not None]
    return min(known) if known else None


def perp_leg(q: PerpQuote, side: str, fee_bps: float) -> Leg:
    per_coin = q.ask if side == "buy" else q.bid
    qty = q.ask_qty if side == "buy" else q.bid_qty
    return Leg(
        venue=q.venue,
        market="perp",
        symbol=q.native,
        side=side,
        price=per_coin * q.price_mult,
        price_per_coin=per_coin,
        l1_qty=qty,
        l1_notional_usd=qty * per_coin if qty is not None else None,
        taker_fee_bps=fee_bps,
    )


def spot_leg(q: SpotQuote, side: str, fee_bps: float) -> Leg:
    per_coin = q.ask if side == "buy" else q.bid
    qty = q.ask_qty if side == "buy" else q.bid_qty
    return Leg(
        venue=q.venue,
        market="spot",
        symbol=q.native,
        side=side,
        price=per_coin * q.price_mult,
        price_per_coin=per_coin,
        l1_qty=qty,
        l1_notional_usd=qty * per_coin if qty is not None else None,
        taker_fee_bps=fee_bps,
    )


@dataclass(slots=True)
class Ewma:
    """EWMA по времени (нерегулярные отсчёты), с периодом прогрева."""

    halflife_sec: float
    value: float = 0.0
    first_ts: float = 0.0
    last_ts: float = 0.0
    n: int = 0

    def update(self, x: float, now: float) -> None:
        if self.n == 0:
            self.value, self.first_ts = x, now
        else:
            dt = max(now - self.last_ts, 0.0)
            alpha = 1.0 - 0.5 ** (dt / self.halflife_sec) if self.halflife_sec > 0 else 1.0
            self.value += alpha * (x - self.value)
        self.last_ts = now
        self.n += 1

    def warm(self, now: float, warmup_sec: float) -> bool:
        return self.n > 0 and now - self.first_ts >= warmup_sec


class Baselines:
    """Набор EWMA по ключу с периодической уборкой давно не обновлявшихся."""

    def __init__(self, halflife_sec: float) -> None:
        self.halflife_sec = halflife_sec
        self._items: dict[tuple, Ewma] = {}
        self._last_gc = 0.0

    def update(self, key: tuple, x: float, now: float) -> Ewma:
        e = self._items.get(key)
        if e is None:
            e = self._items[key] = Ewma(self.halflife_sec)
        e.update(x, now)
        return e

    def get(self, key: tuple) -> Ewma | None:
        return self._items.get(key)

    def gc(self, now: float, max_idle_sec: float = 7200.0) -> None:
        if now - self._last_gc < 300:
            return
        self._last_gc = now
        for k in [k for k, e in self._items.items() if now - e.last_ts > max_idle_sec]:
            del self._items[k]

    def __len__(self) -> int:
        return len(self._items)
