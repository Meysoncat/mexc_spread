"""Направленные детекторы: lead-lag, пробой, импульс, funding/OI-сквиз, отставание от BTC.

Каждая идея — направление, вход, стоп и цели (за одну монету) плюс срок
жизни. Реестр отслеживает исход, так что статистика по типам копится сама.
Детекторы на свечах получают ``CandleSource``: свечи (venue, key) + мета.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

from mexc_monitor.hub.hub import MarketView
from mexc_monitor.hub.models import Candle, InstrumentMeta, PerpQuote
from mexc_monitor.setups.common import clamp, edge_score, perp_leg
from mexc_monitor.setups.config import (
    BreakoutConfig,
    BtcLagConfig,
    FundingSqueezeConfig,
    LeadLagConfig,
    MomentumConfig,
    SetupsConfig,
)
from mexc_monitor.setups.indicators import atr, avg_volume, corr_beta, live_volume, returns, stdev, volume_factor
from mexc_monitor.setups.model import Candidate


@dataclass(slots=True)
class Series:
    venue: str
    meta: InstrumentMeta
    candles: list[Candle]  # по возрастанию t; последняя может быть незакрытой


CandleSource = Callable[[], Iterable[Series]]
OiChange = Callable[[str, str, float], float | None]


def _price(view: MarketView, venue: str, base: str, fallback: float) -> tuple[float, PerpQuote | None]:
    q = view.perp(venue, base)
    if q is not None and q.age_sec <= 10:
        return q.mid, q
    return fallback, None


def _split(candles: list[Candle]) -> tuple[list[Candle], Candle | None]:
    if candles and not candles[-1].closed:
        return candles[:-1], candles[-1]
    return candles, None


def _levels(entry: float, stop: float, targets_r: list[float]) -> list[float]:
    risk = entry - stop
    return [entry + r * risk for r in targets_r]


def _directional(
    *,
    key: str,
    type_: str,
    base: str,
    direction: str,
    entry: float,
    stop: float,
    targets: list[float],
    score: float,
    ttl: int,
    quote: PerpQuote | None,
    venue: str,
    meta: InstrumentMeta | None,
    fee_bps: float,
    metrics: dict,
    notes: list[str],
    horizon: int,
) -> Candidate:
    if quote is not None:
        legs = [perp_leg(quote, "buy" if direction == "long" else "sell", fee_bps)]
        age = quote.age_sec
    else:
        from mexc_monitor.setups.model import Leg

        mult = meta.price_mult if meta else 1.0
        legs = [
            Leg(
                venue=venue,
                market="perp",
                symbol=meta.native if meta else base,
                side="buy" if direction == "long" else "sell",
                price=entry * mult,
                price_per_coin=entry,
                taker_fee_bps=fee_bps,
            )
        ]
        age = 0.0
    risk_pct = abs(entry - stop) / entry * 100 if entry > 0 else None
    metrics = {**metrics, "risk_pct": risk_pct}
    return Candidate(
        key=key,
        type=type_,
        base=base,
        legs=legs,
        score=clamp(score, 0.0, 100.0),
        edge_bps=None,
        direction=direction,
        entry=entry,
        stop=stop,
        targets=targets,
        horizon_sec=horizon,
        ttl_sec=ttl,
        data_age_sec=age,
        metrics=metrics,
        notes=notes,
        track_venue=venue,
    )


# ═══════════════════════════════════════════════════════════════ lead-lag


class LeadLagDetector:
    """Лидер (Binance) резко сдвинулся, отстающая биржа ещё нет — догонит.

    Работает на срезах хаба каждый тик: хранит короткую историю mid.
    edge = разрыв − полный спред отстающего − 2×комиссия.
    """

    type = "lead_lag"

    def __init__(self, cfg: SetupsConfig) -> None:
        self.root = cfg
        self.cfg: LeadLagConfig = cfg.lead_lag
        self._hist: dict[tuple[str, str], deque[tuple[float, float]]] = {}
        self._universe: list[str] = []
        self._universe_ts = 0.0

    def _refresh_universe(self, view: MarketView, now: float) -> None:
        if now - self._universe_ts < 60 and self._universe:
            return
        self._universe_ts = now
        ranked = []
        for base, quotes in view.perps.items():
            vols = [q.volume_24h_usd or 0.0 for q in quotes if q.venue in self.cfg.leaders]
            if vols:
                ranked.append((max(vols), base))
        ranked.sort(reverse=True)
        self._universe = [b for _, b in ranked[: self.cfg.universe]]
        keep = set(self._universe)
        for k in [k for k in self._hist if k[1] not in keep]:
            del self._hist[k]

    def _ret_bps(self, venue: str, base: str, mid: float, now: float) -> float | None:
        hist = self._hist.get((venue, base))
        if not hist:
            return None
        ref = None
        for t, m in reversed(hist):
            if now - t >= self.cfg.window_sec:
                ref = m
                break
        if ref is None or ref <= 0:
            return None
        return (mid / ref - 1.0) * 1e4

    def detect(self, view: MarketView, now: float) -> list[Candidate]:
        cfg = self.cfg
        self._refresh_universe(view, now)
        out: list[Candidate] = []
        maxlen = int(cfg.window_sec * 8) + 20
        for base in self._universe:
            quotes = [q for q in view.perps.get(base, []) if q.age_sec <= cfg.max_lagger_age_sec and not q.indicative]
            rets: dict[str, float | None] = {}
            for q in quotes:
                rets[q.venue] = self._ret_bps(q.venue, base, q.mid, now)
                h = self._hist.setdefault((q.venue, base), deque(maxlen=maxlen))
                h.append((now, q.mid))
            for leader in cfg.leaders:
                r_lead = rets.get(leader)
                if r_lead is None or abs(r_lead) < cfg.move_bps:
                    continue
                sign = 1.0 if r_lead > 0 else -1.0
                for q in quotes:
                    if q.venue == leader:
                        continue
                    r_lag = rets.get(q.venue)
                    if r_lag is None or sign * r_lag > cfg.follow_ratio * abs(r_lead):
                        continue
                    gap = abs(r_lead - r_lag)
                    fee = self.root.perp_fee(q.venue)
                    edge = gap - q.spread_bps - 2 * fee
                    if edge < cfg.open_edge_bps:
                        continue
                    direction = "long" if sign > 0 else "short"
                    entry = q.ask if sign > 0 else q.bid
                    target = entry * (1 + sign * gap * 0.8 / 1e4)
                    stop = entry * (1 - sign * max(gap * 0.5, 2 * q.spread_bps + 5) / 1e4)
                    out.append(
                        _directional(
                            key=f"lead_lag:{base}:{leader}>{q.venue}:{direction}",
                            type_=self.type,
                            base=base,
                            direction=direction,
                            entry=entry,
                            stop=stop,
                            targets=[target],
                            score=edge_score(edge, 15.0),
                            ttl=cfg.ttl_sec,
                            quote=q,
                            venue=q.venue,
                            meta=None,
                            fee_bps=fee,
                            metrics={
                                "leader": leader,
                                "leader_move_bps": r_lead,
                                "lagger_move_bps": r_lag,
                                "gap_bps": gap,
                                "edge_bps": edge,
                                "window_sec": cfg.window_sec,
                            },
                            notes=[f"{leader} сдвинулся на {r_lead:+.1f} bps за {cfg.window_sec:g}с, {q.venue} — на {r_lag:+.1f}; ждём догоняющего движения."],
                            horizon=cfg.ttl_sec,
                        )
                    )
        return out


# ═══════════════════════════════════════════════════════════════ breakout


class BreakoutDetector:
    """Пробой диапазона последних N минут на всплеске объёма."""

    type = "breakout"

    def __init__(self, cfg: SetupsConfig) -> None:
        self.root = cfg
        self.cfg: BreakoutConfig = cfg.breakout

    def detect(self, view: MarketView, series: Iterable[Series], now: float) -> list[Candidate]:
        cfg = self.cfg
        now_ms = int(now * 1000)
        out: list[Candidate] = []
        for s in series:
            closed, cur = _split(s.candles)
            if len(closed) < cfg.lookback + 2 or cur is None:
                continue
            base = s.meta.base
            mult = s.meta.price_mult
            rng = closed[-cfg.lookback - 1 : -1]
            hi = max(c.h for c in rng) / mult
            lo = min(c.l for c in rng) / mult
            price, q = _price(view, s.venue, base, cur.c / mult)
            if price <= 0:
                continue
            range_pct = (hi - lo) / price * 100
            if not cfg.min_range_pct <= range_pct <= cfg.max_range_pct:
                continue
            vol_avg = avg_volume(rng)
            if vol_avg <= 0:
                continue
            vol_ratio = max(live_volume(cur, now_ms), closed[-1].v) / vol_avg
            if vol_ratio < cfg.volume_ratio:
                continue
            a = atr(closed, 14)
            if a is None:
                continue
            a /= mult
            last_close = closed[-1].c / mult
            if price >= hi * (1 + cfg.buffer_pct / 100) and last_close <= hi * (1 + 2 * cfg.buffer_pct / 100):
                direction, level, stop = "long", hi, hi - cfg.stop_atr * a
            elif price <= lo * (1 - cfg.buffer_pct / 100) and last_close >= lo * (1 - 2 * cfg.buffer_pct / 100):
                direction, level, stop = "short", lo, lo + cfg.stop_atr * a
            else:
                continue
            risk_pct = abs(price - stop) / price * 100
            if risk_pct > cfg.max_risk_pct:
                continue
            if risk_pct < cfg.min_risk_pct:
                sign = 1 if direction == "long" else -1
                stop = price * (1 - sign * cfg.min_risk_pct / 100)
            targets = _levels(price, stop, cfg.targets_r)
            strength = abs(price - level) / a if a > 0 else 0.0
            score = (
                edge_score(vol_ratio - 1, cfg.volume_ratio)
                * volume_factor(q.volume_24h_usd if q else None)
                * clamp(0.6 + 0.2 * strength, 0.6, 1.0)
            )
            out.append(
                _directional(
                    key=f"breakout:{base}:{s.venue}:{direction}",
                    type_=self.type,
                    base=base,
                    direction=direction,
                    entry=price,
                    stop=stop,
                    targets=targets,
                    score=score,
                    ttl=cfg.ttl_sec,
                    quote=q,
                    venue=s.venue,
                    meta=s.meta,
                    fee_bps=self.root.perp_fee(s.venue),
                    metrics={
                        "level": level,
                        "range_high": hi,
                        "range_low": lo,
                        "range_pct": range_pct,
                        "lookback_min": cfg.lookback,
                        "volume_ratio": vol_ratio,
                        "atr": a,
                        "breakout_atr": strength,
                    },
                    notes=[
                        f"Пробой {'вверх' if direction == 'long' else 'вниз'} диапазона {cfg.lookback} мин "
                        f"({lo:.6g}–{hi:.6g}) на объёме ×{vol_ratio:.1f}; стоп за уровнем {cfg.stop_atr:g}×ATR."
                    ],
                    horizon=cfg.ttl_sec,
                )
            )
        return out


# ═══════════════════════════════════════════════════════════════ momentum


class MomentumDetector:
    """Резкое движение за несколько минут: продолжение или откат после экстремума."""

    type = "momentum"

    def __init__(self, cfg: SetupsConfig) -> None:
        self.root = cfg
        self.cfg: MomentumConfig = cfg.momentum

    def detect(self, view: MarketView, series: Iterable[Series], now: float) -> list[Candidate]:
        cfg = self.cfg
        now_ms = int(now * 1000)
        out: list[Candidate] = []
        for s in series:
            closed, cur = _split(s.candles)
            if len(closed) < cfg.lookback + cfg.window + 1:
                continue
            base, mult = s.meta.base, s.meta.price_mult
            seq = closed + ([cur] if cur is not None else [])
            recent = seq[-cfg.window :]
            ref = recent[0].o / mult  # открытие первой свечи окна
            price, q = _price(view, s.venue, base, seq[-1].c / mult)
            if ref <= 0 or price <= 0:
                continue
            change = (price / ref - 1) * 100
            if abs(change) < cfg.move_pct:
                continue
            prior = seq[-cfg.window - cfg.lookback : -cfg.window]
            vol_avg = avg_volume(prior)
            if vol_avg <= 0:
                continue
            vols = [live_volume(c, now_ms) for c in recent]
            vol_ratio = (sum(vols) / len(vols)) / vol_avg
            if vol_ratio < cfg.volume_ratio:
                continue
            a = atr(closed, 14)
            if a is None:
                continue
            a /= mult
            sign = 1.0 if change > 0 else -1.0
            ext = (max(c.h for c in recent) if sign > 0 else min(c.l for c in recent)) / mult
            move = abs(ext - ref)
            pullback = abs(ext - price) / move if move > 0 else 0.0
            last = closed[-1]
            against = (last.c < last.o) if sign > 0 else (last.c > last.o)
            ext_move = move / ref * 100  # размах до экстремума: после отката текущий change меньше
            if ext_move >= cfg.extreme_pct and pullback >= cfg.min_pullback_reversion and against:
                bias, direction = "reversion", ("short" if sign > 0 else "long")
                stop = ext + sign * 0.5 * a
            elif pullback <= cfg.max_pullback_continuation:
                bias, direction = "continuation", ("long" if sign > 0 else "short")
                stop = price - sign * cfg.stop_atr * a
            else:
                continue
            if abs(price - stop) / price * 100 > 8:
                continue
            targets = _levels(price, stop, cfg.targets_r)
            score = edge_score(abs(change) / cfg.move_pct, 1.5) * volume_factor(q.volume_24h_usd if q else None) * clamp(vol_ratio / (2 * cfg.volume_ratio), 0.5, 1.0)
            if bias == "reversion":
                score *= 0.85  # контртренд рискованнее
            out.append(
                _directional(
                    key=f"momentum:{base}:{s.venue}:{bias}:{direction}",
                    type_=self.type,
                    base=base,
                    direction=direction,
                    entry=price,
                    stop=stop,
                    targets=targets,
                    score=score,
                    ttl=cfg.ttl_sec,
                    quote=q,
                    venue=s.venue,
                    meta=s.meta,
                    fee_bps=self.root.perp_fee(s.venue),
                    metrics={
                        "bias": bias,
                        "change_pct": change,
                        "window_min": cfg.window,
                        "extreme": ext,
                        "extreme_move_pct": ext_move,
                        "pullback": pullback,
                        "volume_ratio": vol_ratio,
                        "atr": a,
                    },
                    notes=[
                        f"{change:+.2f}% за {cfg.window} мин на объёме ×{vol_ratio:.1f}; "
                        + ("ставка на продолжение." if bias == "continuation" else f"откат {pullback:.0%} от экстремума — ставка на возврат.")
                    ],
                    horizon=cfg.ttl_sec,
                )
            )
        return out


# ═══════════════════════════════════════════════════════════════ funding / OI squeeze


class MinuteMids:
    """Поминутная история mid по (venue, base) для детекторов без свечей."""

    def __init__(self, maxlen: int = 75) -> None:
        self._maxlen = maxlen
        self._data: dict[tuple[str, str], deque[float]] = {}
        self._last = 0.0

    def record(self, view: MarketView, now: float) -> None:
        if now - self._last < 60:
            return
        self._last = now
        seen = set()
        for base, quotes in view.perps.items():
            for q in quotes:
                k = (q.venue, base)
                seen.add(k)
                d = self._data.get(k)
                if d is None:
                    d = self._data[k] = deque(maxlen=self._maxlen)
                d.append(q.mid)
        for k in [k for k in self._data if k not in seen]:
            del self._data[k]

    def get(self, venue: str, base: str) -> list[float]:
        d = self._data.get((venue, base))
        return list(d) if d else []


class FundingSqueezeDetector:
    """Перекошенный funding + растущий OI, а цена за толпой не идёт → контртренд."""

    type = "funding_squeeze"

    def __init__(self, cfg: SetupsConfig, oi_change: OiChange, mids: MinuteMids) -> None:
        self.root = cfg
        self.cfg: FundingSqueezeConfig = cfg.funding_squeeze
        self.oi_change = oi_change
        self.mids = mids

    def detect(self, view: MarketView, now: float) -> list[Candidate]:
        cfg = self.cfg
        window_min = int(cfg.oi_window_sec // 60)
        out: list[Candidate] = []
        for base, quotes in view.perps.items():
            fundings = {q.venue: q.funding_per_hour * 8 * 100 for q in quotes if q.funding_per_hour is not None}
            for q in quotes:
                f8 = fundings.get(q.venue)
                if f8 is None or abs(f8) < cfg.funding_8h_pct or (q.volume_24h_usd or 0) < cfg.min_volume_24h_usd:
                    continue
                oi = self.oi_change(q.venue, q.key, cfg.oi_window_sec)
                if oi is None or oi * 100 < cfg.oi_change_pct:
                    continue
                mids = self.mids.get(q.venue, base)
                if len(mids) < window_min + 1:
                    continue
                price_chg = (q.mid / mids[-window_min - 1] - 1) * 100
                crowd = 1.0 if f8 > 0 else -1.0  # +1: перегреты лонги
                if crowd * price_chg > cfg.max_price_follow_pct:
                    continue  # цена идёт за толпой — сквиза не видно
                others = [v for k, v in fundings.items() if k != q.venue]
                excess = f8 - statistics.median(others) if others else None
                vol_1h = stdev(returns(mids[-window_min - 1 :])) * math.sqrt(60) * 100
                stop_pct = max(cfg.stop_pct_min, 1.5 * vol_1h)
                direction = "short" if crowd > 0 else "long"
                entry = q.bid if direction == "short" else q.ask
                stop = entry * (1 + crowd * stop_pct / 100)
                targets = _levels(entry, stop, cfg.targets_r)
                score = (
                    edge_score(abs(f8) / cfg.funding_8h_pct, 1.5)
                    * clamp(oi * 100 / (2 * cfg.oi_change_pct), 0.5, 1.0)
                    * volume_factor(q.volume_24h_usd)
                )
                out.append(
                    _directional(
                        key=f"funding_squeeze:{base}:{q.venue}:{direction}",
                        type_=self.type,
                        base=base,
                        direction=direction,
                        entry=entry,
                        stop=stop,
                        targets=targets,
                        score=score,
                        ttl=cfg.ttl_sec,
                        quote=q,
                        venue=q.venue,
                        meta=None,
                        fee_bps=self.root.perp_fee(q.venue),
                        metrics={
                            "funding_8h_pct": f8,
                            "funding_excess_vs_venues_pct": excess,
                            "oi_change_pct": oi * 100,
                            "oi_window_min": window_min,
                            "price_change_pct": price_chg,
                            "open_interest_usd": q.open_interest_usd,
                            "volatility_1h_pct": vol_1h,
                        },
                        notes=[
                            f"Funding {f8:+.3f}%/8ч и OI {oi * 100:+.1f}% за {window_min} мин, а цена {price_chg:+.2f}% — "
                            + ("лонги перегреты, риск их ликвидаций." if crowd > 0 else "шорты перегреты, риск шорт-сквиза.")
                        ],
                        horizon=cfg.ttl_sec,
                    )
                )
        return out


# ═══════════════════════════════════════════════════════════════ BTC lag


class BtcLagDetector:
    """Монета с высокой корреляцией к BTC ещё не отыграла его движение."""

    type = "btc_lag"

    def __init__(self, cfg: SetupsConfig) -> None:
        self.root = cfg
        self.cfg: BtcLagConfig = cfg.btc_lag

    def detect(self, view: MarketView, series: Iterable[Series], now: float) -> list[Candidate]:
        cfg = self.cfg
        by_base = {s.meta.base: s for s in series if s.venue == cfg.venue}
        btc = by_base.get("BTC")
        if btc is None:
            return []
        need = cfg.corr_window + cfg.move_window + 2
        btc_closed, btc_cur = _split(btc.candles)
        if len(btc_closed) < need:
            return []
        btc_price, _ = _price(view, cfg.venue, "BTC", (btc_cur or btc_closed[-1]).c)
        ref_candle = btc_closed[-cfg.move_window]
        btc_move = btc_price / ref_candle.c - 1
        if abs(btc_move) * 100 < cfg.btc_move_pct:
            return []
        btc_by_t = {c.t: c.c for c in btc_closed}
        sign = 1.0 if btc_move > 0 else -1.0
        out: list[Candidate] = []
        for base, s in by_base.items():
            if base == "BTC":
                continue
            closed, cur = _split(s.candles)
            if len(closed) < need:
                continue
            mult = s.meta.price_mult
            # корреляция/бета — на окне *до* движения, чтобы само отставание не портило оценку
            pre = [c for c in closed[-need:-cfg.move_window] if c.t in btc_by_t]
            if len(pre) < cfg.corr_window * 0.8:
                continue
            cb = corr_beta(returns([btc_by_t[c.t] for c in pre]), returns([c.c for c in pre]))
            if cb is None:
                continue
            corr, beta = cb
            if corr < cfg.min_corr or beta <= 0:
                continue
            alt_ref = next((c for c in reversed(closed) if c.t == ref_candle.t), None)
            if alt_ref is None:
                continue
            price, q = _price(view, s.venue, base, (cur or closed[-1]).c / mult)
            alt_move = price / (alt_ref.c / mult) - 1
            expected = beta * btc_move
            shortfall = sign * (expected - alt_move)
            if shortfall * 100 < cfg.min_shortfall_pct or shortfall / abs(expected) < cfg.min_shortfall_ratio:
                continue
            a = atr(closed, 14)
            if a is None:
                continue
            a /= mult
            direction = "long" if sign > 0 else "short"
            target = price * (1 + sign * cfg.target_capture * shortfall)
            stop = price - sign * cfg.stop_atr * a
            risk = abs(price - stop)
            rr = abs(target - price) / risk if risk > 0 else 0.0
            if rr < cfg.min_rr:
                continue
            score = edge_score(shortfall * 100, 1.0) * clamp((corr - 0.4) / 0.5, 0.3, 1.0) * volume_factor(q.volume_24h_usd if q else None)
            out.append(
                _directional(
                    key=f"btc_lag:{base}:{s.venue}:{direction}",
                    type_=self.type,
                    base=base,
                    direction=direction,
                    entry=price,
                    stop=stop,
                    targets=[target],
                    score=score,
                    ttl=cfg.ttl_sec,
                    quote=q,
                    venue=s.venue,
                    meta=s.meta,
                    fee_bps=self.root.perp_fee(s.venue),
                    metrics={
                        "btc_move_pct": btc_move * 100,
                        "alt_move_pct": alt_move * 100,
                        "expected_move_pct": expected * 100,
                        "shortfall_pct": shortfall * 100,
                        "corr": corr,
                        "beta": beta,
                        "window_min": cfg.move_window,
                        "atr": a,
                    },
                    notes=[
                        f"BTC {btc_move * 100:+.2f}% за {cfg.move_window} мин; {base} (β={beta:.2f}, corr={corr:.2f}) "
                        f"прошёл {alt_move * 100:+.2f}% из ожидаемых {expected * 100:+.2f}%."
                    ],
                    horizon=cfg.ttl_sec,
                )
            )
        return out
