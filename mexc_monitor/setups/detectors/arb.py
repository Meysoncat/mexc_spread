"""Арбитражные детекторы: кросс-биржевой perp↔perp, funding, базис спот↔perp.

Все edge — ожидаемая прибыль в bps от номинала одной ноги *после* комиссий
входа и выхода и пересечения спреда на выходе. Цены сравниваются за одну
монету (``1000PEPE`` и контракты уже приведены хабом).
"""

from __future__ import annotations

import statistics
from typing import Callable

from mexc_monitor.hub.hub import MarketView
from mexc_monitor.hub.models import PerpQuote
from mexc_monitor.setups.common import (
    Baselines,
    clamp,
    edge_score,
    freshness_factor,
    liquidity_factor,
    min_known,
    perp_leg,
    spot_leg,
)
from mexc_monitor.setups.config import BasisConfig, CrossPerpConfig, FundingArbConfig, SetupsConfig
from mexc_monitor.setups.model import Candidate

RequestL1 = Callable[[str, str], None]


def _drop_outliers(quotes: list, max_dev: float) -> list:
    """Убрать котировки далеко от медианы: разные активы под одним тикером."""
    if len(quotes) < 3:
        if len(quotes) == 2:
            a, b = quotes
            hi, lo = max(a.mid, b.mid), min(a.mid, b.mid)
            return quotes if lo > 0 and hi / lo - 1 <= max_dev else []
        return quotes
    med = statistics.median(q.mid for q in quotes)
    return [q for q in quotes if med > 0 and abs(q.mid / med - 1) <= max_dev]


def _fresh_perps(quotes: list[PerpQuote], max_age: float, min_vol: float) -> list[PerpQuote]:
    return [
        q
        for q in quotes
        if q.age_sec <= max_age and (q.volume_24h_usd is None or q.volume_24h_usd >= min_vol)
    ]


class CrossPerpDetector:
    """Купить perp на бирже A по ask, продать на бирже B по bid.

    edge = gross − базлайн − спреды на выходе − 2×(fee_A + fee_B), где gross =
    (bid_B − ask_A)/mid, а базлайн — обычная разница mid этих бирж (EWMA):
    устойчивую премию одной площадки не выдаём за возможность.
    """

    type = "cross_perp"

    def __init__(self, cfg: SetupsConfig, request_l1: RequestL1 | None = None) -> None:
        self.root = cfg
        self.cfg: CrossPerpConfig = cfg.cross_perp
        self.baselines = Baselines(self.cfg.baseline_halflife_sec)
        self._last_baseline = 0.0
        self.request_l1 = request_l1

    def _update_baselines(self, base: str, qs: list[PerpQuote], now: float) -> None:
        for i, a in enumerate(qs):
            for b in qs[i + 1 :]:
                va, vb = (a, b) if a.venue < b.venue else (b, a)
                mid = (va.mid + vb.mid) / 2
                if mid > 0:
                    self.baselines.update((base, va.venue, vb.venue), (vb.mid - va.mid) / mid * 1e4, now)

    def _baseline(self, base: str, buy: str, sell: str, now: float) -> float:
        """Обычная разница mid(sell) − mid(buy), bps; 0 до прогрева."""
        lo, hi = (buy, sell) if buy < sell else (sell, buy)
        e = self.baselines.get((base, lo, hi))
        if e is None or not e.warm(now, self.cfg.baseline_warmup_sec):
            return 0.0
        v = clamp(e.value, -self.cfg.baseline_cap_bps, self.cfg.baseline_cap_bps)
        return v if lo == buy else -v

    def detect(self, view: MarketView, now: float) -> list[Candidate]:
        cfg = self.cfg
        out: list[Candidate] = []
        update_baselines = now - self._last_baseline >= 5.0
        if update_baselines:
            self._last_baseline = now
            self.baselines.gc(now)
        for base, quotes in view.perps.items():
            qs = _drop_outliers(_fresh_perps(quotes, cfg.max_age_sec, cfg.min_volume_24h_usd), cfg.max_price_deviation)
            if len(qs) < 2:
                continue
            if update_baselines:
                self._update_baselines(base, qs, now)
            for a in qs:
                fa = self.root.perp_fee(a.venue)
                for b in qs:
                    if a is b or a.venue == b.venue:
                        continue
                    fb = self.root.perp_fee(b.venue)
                    mid = (a.mid + b.mid) / 2
                    gross = (b.bid - a.ask) / mid * 1e4
                    fees_rt = 2 * (fa + fb)
                    if gross - fees_rt < cfg.keep_edge_bps or gross > cfg.max_gross_bps:
                        continue
                    baseline = self._baseline(base, a.venue, b.venue, now)
                    exit_cross = (a.spread_bps + b.spread_bps) / 2
                    edge = gross - baseline - exit_cross - fees_rt
                    if edge < cfg.keep_edge_bps:
                        continue
                    out.append(self._candidate(base, a, b, fa, fb, gross, baseline, exit_cross, edge))
        return out

    def _candidate(self, base, a, b, fa, fb, gross, baseline, exit_cross, edge) -> Candidate:
        cfg = self.cfg
        l1 = min_known(a.ask_notional, b.bid_notional)
        for q, qty in ((a, a.ask_qty), (b, b.bid_qty)):
            if qty is None and self.request_l1 is not None:
                self.request_l1(q.venue, q.key)
        age = max(a.age_sec, b.age_sec)
        indicative = a.indicative or b.indicative
        score = edge_score(edge, 30.0) * liquidity_factor(l1) * freshness_factor(age, cfg.max_age_sec)
        if indicative:
            score *= 0.7
        carry_8h = None
        if a.funding_per_hour is not None and b.funding_per_hour is not None:
            carry_8h = (b.funding_per_hour - a.funding_per_hour) * 8 * 1e4
        notes = [f"Long {base} perp на {a.venue}, short на {b.venue}; закрывать, когда разница вернётся к обычной ({baseline:+.1f} bps)."]
        if indicative:
            notes.append("Цены одной из ног индикативные (impact), не L1.")
        if l1 is None:
            notes.append("Объём на лучшей цене неизвестен — проверьте стакан.")
        return Candidate(
            key=f"cross_perp:{base}:{a.venue}>{b.venue}",
            type=self.type,
            base=base,
            legs=[perp_leg(a, "buy", fa), perp_leg(b, "sell", fb)],
            score=score,
            edge_bps=edge,
            opens=edge >= cfg.open_edge_bps and (l1 is None or l1 >= cfg.min_l1_usd),
            horizon_sec=cfg.horizon_sec,
            data_age_sec=age,
            metrics={
                "gross_bps": gross,
                "baseline_bps": baseline,
                "exit_cross_bps": exit_cross,
                "fees_round_trip_bps": 2 * (fa + fb),
                "l1_notional_usd": l1,
                "funding_carry_8h_bps": carry_8h,
                "volume_24h_usd_min": min_known(a.volume_24h_usd, b.volume_24h_usd),
                "indicative": indicative,
            },
            notes=notes,
        )


class FundingArbDetector:
    """Дельта-нейтрально: long там, где funding ниже, short — где выше.

    Ставки нормируются на час (биржи платят раз в 1/4/8 ч). edge — доход за
    горизонт (24 ч по умолчанию) минус стоимость входа/выхода.
    """

    type = "funding_arb"

    def __init__(self, cfg: SetupsConfig) -> None:
        self.root = cfg
        self.cfg: FundingArbConfig = cfg.funding_arb

    def detect(self, view: MarketView, now: float) -> list[Candidate]:
        cfg = self.cfg
        out: list[Candidate] = []
        horizon_h = cfg.horizon_sec / 3600
        for base, quotes in view.perps.items():
            qs = [q for q in _fresh_perps(quotes, cfg.max_age_sec, cfg.min_volume_24h_usd) if q.funding_per_hour is not None]
            qs = _drop_outliers(qs, 0.2)
            if len(qs) < 2:
                continue
            lo = min(qs, key=lambda q: q.funding_per_hour)
            hi = max(qs, key=lambda q: q.funding_per_hour)
            if lo.venue == hi.venue:
                continue
            diff_ph = hi.funding_per_hour - lo.funding_per_hour
            apr = diff_ph * 24 * 365 * 100
            if apr < cfg.keep_apr_pct:
                continue
            fl, fh = self.root.perp_fee(lo.venue), self.root.perp_fee(hi.venue)
            mid = (lo.mid + hi.mid) / 2
            entry_cross = (lo.ask - hi.bid) / mid * 1e4  # отрицательное — вход с бонусом
            exit_cross = (lo.spread_bps + hi.spread_bps) / 2
            cost = entry_cross + exit_cross + 2 * (fl + fh)
            income = diff_ph * horizon_h * 1e4
            edge = income - cost
            breakeven_h = cost / (diff_ph * 1e4) if diff_ph > 0 and cost > 0 else 0.0
            l1 = min_known(lo.ask_notional, hi.bid_notional)
            age = max(lo.age_sec, hi.age_sec)
            score = edge_score(edge, 40.0) * liquidity_factor(l1) * clamp(1.5 - breakeven_h / (2 * cfg.max_breakeven_h), 0.3, 1.0)
            notes = [
                f"Long {base} на {lo.venue} (funding {lo.funding_rate * 100:+.4f}%/{lo.funding_interval_h:g}ч), "
                f"short на {hi.venue} ({hi.funding_rate * 100:+.4f}%/{hi.funding_interval_h:g}ч)."
            ]
            if lo.funding_interval_h != hi.funding_interval_h:
                notes.append("Интервалы funding различаются — выплаты приходят в разное время.")
            notes.append("Ставка funding меняется каждый период; edge — оценка при текущих ставках.")
            out.append(
                Candidate(
                    key=f"funding_arb:{base}:{lo.venue}>{hi.venue}",
                    type=self.type,
                    base=base,
                    legs=[perp_leg(lo, "buy", fl), perp_leg(hi, "sell", fh)],
                    score=score,
                    edge_bps=edge,
                    opens=apr >= cfg.open_apr_pct and edge > 0 and breakeven_h <= cfg.max_breakeven_h,
                    horizon_sec=cfg.horizon_sec,
                    data_age_sec=age,
                    metrics={
                        "funding_apr_pct": apr,
                        "funding_diff_per_hour_bps": diff_ph * 1e4,
                        "income_horizon_bps": income,
                        "entry_cross_bps": entry_cross,
                        "cost_bps": cost,
                        "breakeven_h": breakeven_h,
                        "long_funding_rate": lo.funding_rate,
                        "long_funding_interval_h": lo.funding_interval_h,
                        "long_next_funding_ms": lo.next_funding_ms,
                        "short_funding_rate": hi.funding_rate,
                        "short_funding_interval_h": hi.funding_interval_h,
                        "short_next_funding_ms": hi.next_funding_ms,
                        "l1_notional_usd": l1,
                    },
                    notes=notes,
                )
            )
        return out


class BasisDetector:
    """Спот↔perp: купить спот, продать perp, когда premium perp выше обычного.

    edge = (basis − базлайн) − спреды на выходе − комиссии + funding за
    ``include_funding_hours`` (short perp получает положительный funding).
    Обратная сделка (шорт спота) — только при ``allow_reverse``.
    """

    type = "basis"

    def __init__(self, cfg: SetupsConfig, request_l1: RequestL1 | None = None) -> None:
        self.root = cfg
        self.cfg: BasisConfig = cfg.basis
        self.baselines = Baselines(self.cfg.baseline_halflife_sec)
        self._last_baseline = 0.0
        self.request_l1 = request_l1

    def detect(self, view: MarketView, now: float) -> list[Candidate]:
        cfg = self.cfg
        out: list[Candidate] = []
        update = now - self._last_baseline >= 5.0
        if update:
            self._last_baseline = now
            self.baselines.gc(now)
        for base, spots in view.spots.items():
            perps = _fresh_perps(view.perps.get(base, []), cfg.max_age_sec, cfg.min_volume_24h_usd)
            if not perps:
                continue
            spots = [s for s in spots if s.age_sec <= cfg.max_age_sec]
            kept = {id(q) for q in _drop_outliers([*spots, *perps], 0.2)}
            spots = [s for s in spots if id(s) in kept]
            perps = [p for p in perps if id(p) in kept]
            if update:
                for s in spots:
                    for p in perps:
                        mid = (s.mid + p.mid) / 2
                        if mid > 0:
                            self.baselines.update((base, s.venue, p.venue), (p.mid - s.mid) / mid * 1e4, now)
            if not spots:
                continue
            # Пары: спот той же биржи (без перевода между биржами) и лучший спот по цене
            best_buy = min(spots, key=lambda q: q.ask)
            best_sell = max(spots, key=lambda q: q.bid)
            spot_by_venue = {q.venue: q for q in spots}
            for p in perps:
                pairs = {id(q): q for q in (spot_by_venue.get(p.venue),) if q is not None}
                if not cfg.same_venue_only:
                    pairs.setdefault(id(best_buy), best_buy)
                    if cfg.allow_reverse:
                        pairs.setdefault(id(best_sell), best_sell)
                for s in pairs.values():
                    self._evaluate(base, s, p, now, out)
        return out

    def _evaluate(self, base, s, p, now, out) -> None:
        cfg = self.cfg
        fs, fp = self.root.spot_fee(s.venue), self.root.perp_fee(p.venue)
        e = self.baselines.get((base, s.venue, p.venue))
        baseline = 0.0
        if e is not None and e.warm(now, cfg.baseline_warmup_sec):
            baseline = clamp(e.value, -cfg.baseline_cap_bps, cfg.baseline_cap_bps)
        exit_cross = (s.spread_bps + p.spread_bps) / 2
        fees_rt = 2 * (fs + fp)
        carry = (p.funding_per_hour or 0.0) * cfg.include_funding_hours * 1e4
        gross = (p.bid - s.ask) / s.ask * 1e4
        edge = gross - baseline - exit_cross - fees_rt + carry
        if gross <= cfg.max_gross_bps and edge >= cfg.keep_edge_bps:
            out.append(self._candidate(base, s, p, fs, fp, "carry", gross, baseline, exit_cross, carry, edge))
        if cfg.allow_reverse:
            gross_r = (s.bid - p.ask) / p.ask * 1e4
            edge_r = gross_r + baseline - exit_cross - fees_rt - carry
            if gross_r <= cfg.max_gross_bps and edge_r >= cfg.keep_edge_bps:
                out.append(self._candidate(base, s, p, fs, fp, "reverse", gross_r, -baseline, exit_cross, -carry, edge_r))

    def _candidate(self, base, s, p, fs, fp, mode, gross, baseline, exit_cross, carry, edge) -> Candidate:
        cfg = self.cfg
        if mode == "carry":
            legs = [spot_leg(s, "buy", fs), perp_leg(p, "sell", fp)]
            l1 = min_known(s.ask_notional, p.bid_notional)
            note = f"Купить спот {base} на {s.venue}, продать perp на {p.venue}."
        else:
            legs = [spot_leg(s, "sell", fs), perp_leg(p, "buy", fp)]
            l1 = min_known(s.bid_notional, p.ask_notional)
            note = f"Продать спот {base} на {s.venue} (нужен займ/наличие монеты), купить perp на {p.venue}."
        if p.ask_qty is None and self.request_l1 is not None:
            self.request_l1(p.venue, p.key)
        age = max(s.age_sec, p.age_sec)
        same = s.venue == p.venue
        score = edge_score(edge, 40.0) * liquidity_factor(l1) * freshness_factor(age, cfg.max_age_sec) * (1.0 if same else 0.85)
        return Candidate(
            key=f"basis:{base}:{s.venue}>{p.venue}:{mode}",
            type=self.type,
            base=base,
            legs=legs,
            score=score,
            edge_bps=edge,
            opens=edge >= cfg.open_edge_bps and (l1 is None or l1 >= cfg.min_l1_usd),
            horizon_sec=cfg.horizon_sec,
            data_age_sec=age,
            metrics={
                "mode": mode,
                "same_venue": same,
                "basis_bps": gross,
                "baseline_bps": baseline,
                "exit_cross_bps": exit_cross,
                "funding_carry_bps": carry,
                "funding_hours": cfg.include_funding_hours,
                "fees_round_trip_bps": 2 * (fs + fp),
                "l1_notional_usd": l1,
                "requires_spot_borrow": mode == "reverse",
            },
            notes=[note],
        )
