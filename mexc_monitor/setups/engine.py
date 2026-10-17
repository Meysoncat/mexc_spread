"""SetupEngine: цикл «срез рынка → детекторы → реестр».

Быстрые детекторы (кросс-perp, базис, lead-lag) — каждый такт (0.5 с),
funding-детекторы — раз в ``funding_every_sec``, детекторы на свечах — раз в
``directional_every_sec``. Сеть в цикле не используется: всё берётся из хаба.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from mexc_monitor.hub.hub import MarketHub, MarketView
from mexc_monitor.setups.config import SetupsConfig
from mexc_monitor.setups.detectors.arb import BasisDetector, CrossPerpDetector, FundingArbDetector
from mexc_monitor.setups.detectors.directional import (
    BreakoutDetector,
    BtcLagDetector,
    FundingSqueezeDetector,
    LeadLagDetector,
    MinuteMids,
    MomentumDetector,
    Series,
)
from mexc_monitor.setups.model import Candidate
from mexc_monitor.setups.registry import SetupRegistry
from mexc_monitor.setups.store import SetupStore

logger = logging.getLogger(__name__)


class SetupEngine:
    def __init__(
        self,
        hub: MarketHub,
        cfg: SetupsConfig,
        registry: SetupRegistry | None = None,
        store: SetupStore | None = None,
    ) -> None:
        self.hub = hub
        self.cfg = cfg
        self.registry = registry or SetupRegistry()
        self.store = store
        if store is not None:
            self.registry.subscribe(store.on_event)
        self.mids = MinuteMids(maxlen=int(cfg.funding_squeeze.oi_window_sec // 60) + 15)
        self.fast = [
            d
            for d, on in (
                (CrossPerpDetector(cfg, hub.request_l1), cfg.cross_perp.enabled),
                (BasisDetector(cfg, hub.request_l1), cfg.basis.enabled),
                (LeadLagDetector(cfg), cfg.lead_lag.enabled),
            )
            if on
        ]
        self.funding = [
            d
            for d, on in (
                (FundingArbDetector(cfg), cfg.funding_arb.enabled),
                (FundingSqueezeDetector(cfg, hub.oi_change, self.mids), cfg.funding_squeeze.enabled),
            )
            if on
        ]
        self.candle_based = [
            d
            for d, on in (
                (BreakoutDetector(cfg), cfg.breakout.enabled),
                (MomentumDetector(cfg), cfg.momentum.enabled),
                (BtcLagDetector(cfg), cfg.btc_lag.enabled),
            )
            if on
        ]
        self._every = {"basis": cfg.basis.every_sec}
        self._last_run: dict[str, float] = {}
        self._last_funding = 0.0
        self._last_directional = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.timings_ms: dict[str, float] = {}
        self.ticks = 0
        self.last_tick_epoch: float | None = None
        self.last_error: str | None = None
        self.view: MarketView | None = None

    def start(self) -> None:
        if self.store is not None:
            self.store.start()
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="setup-engine", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.store is not None:
            self.store.stop()

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self.tick()
                self.last_error = None
            except Exception as e:  # noqa: BLE001 — цикл не должен умирать
                self.last_error = f"{type(e).__name__}: {e}"
                logger.exception("setup engine tick failed")
            elapsed = time.monotonic() - t0
            self._stop.wait(max(self.cfg.tick_sec - elapsed, 0.05))

    def _run(self, name: str, fn, *args) -> list[Candidate]:
        t0 = time.perf_counter()
        try:
            cands = fn(*args)
        except Exception:  # noqa: BLE001 — ошибка одного детектора не роняет остальные
            logger.exception("detector %s failed", name)
            cands = []
        self.timings_ms[name] = round((time.perf_counter() - t0) * 1000, 2)
        if self.cfg.min_score > 0:
            cands = [c for c in cands if c.score >= self.cfg.min_score]
        return cands

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        t0 = time.perf_counter()
        view = self.hub.view()
        self.timings_ms["view"] = round((time.perf_counter() - t0) * 1000, 2)
        self.view = view
        self.mids.record(view, now)

        for d in self.fast:
            every = self._every.get(d.type, 0.0)
            if every and now - self._last_run.get(d.type, 0.0) < every:
                continue
            self._last_run[d.type] = now
            self.registry.sync(d.type, self._run(d.type, d.detect, view, now), now)

        if now - self._last_funding >= self.cfg.funding_every_sec:
            self._last_funding = now
            for d in self.funding:
                self.registry.sync(d.type, self._run(d.type, d.detect, view, now), now)

        if self.candle_based and now - self._last_directional >= self.cfg.directional_every_sec:
            self._last_directional = now
            series = self._series()
            for d in self.candle_based:
                self.registry.sync(d.type, self._run(d.type, d.detect, view, series, now), now)

        def price(venue: str, base: str) -> float | None:
            q = view.perp(venue, base)
            return q.mid if q is not None and q.age_sec <= 30 else None

        self.registry.track(price, now)
        self.ticks += 1
        self.last_tick_epoch = now
        self.timings_ms["tick"] = round((time.perf_counter() - t0) * 1000, 2)

    def _series(self) -> list[Series]:
        out: list[Series] = []
        for venue, key in self.hub.candles.keys():
            meta = self.hub.meta_for(venue, key)
            if meta is None or not meta.active:
                continue
            candles = self.hub.candles.get(venue, key)
            if candles:
                out.append(Series(venue=venue, meta=meta, candles=candles))
        return out

    def status(self) -> dict[str, Any]:
        view = self.view
        return {
            "ticks": self.ticks,
            "last_tick_epoch": self.last_tick_epoch,
            "last_error": self.last_error,
            "timings_ms": dict(self.timings_ms),
            "detectors": [d.type for d in (*self.fast, *self.funding, *self.candle_based)],
            "bases_perp": len(view.perps) if view else 0,
            "bases_spot": len(view.spots) if view else 0,
            "quotes_perp": sum(len(v) for v in view.perps.values()) if view else 0,
            "active_setups": len(self.registry.active()),
            "event_seq": self.registry.seq,
        }
