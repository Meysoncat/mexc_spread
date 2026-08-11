"""ScreenerEngine — background loop that scans the spot universe and keeps a
ranked list of tradeable-spread opportunities.

Cadence is bounded by the snapshot cache (~3 s); the engine polls every
``scan_interval_sec`` but only mutates per-symbol state when a *new* snapshot
arrives (so the rolling window reflects real data cadence, not poll cadence).
Lifetime still advances with wall-clock on every poll, since it is
``now − first_above_ms``.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone

from mexc_monitor.pipeline import safe_load_snapshot
from mexc_monitor.screener.config import (
    ScreenerConfig,
    apply_config_patch,
    config_to_dict,
    load_screener_config,
)
from mexc_monitor.screener.filters import passes_gates, score_candidate
from mexc_monitor.screener.models import Candidate, candidate_to_opportunity
from mexc_monitor.screener.state import ScreenerState

logger = logging.getLogger(__name__)


def _is_nan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _num(v, default: float = 0.0) -> float:
    if v is None:
        return default
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return default
    if _is_nan(v):
        return default
    return float(v)


def _maybe(v) -> float | None:
    """Coerce to float-or-None (None for missing/NaN)."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return None
    if _is_nan(v):
        return None
    return float(v)


def _parse_iso_ms(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        return (
            datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000.0
        )
    except Exception:
        return None


class ScreenerEngine:
    def __init__(self, config: ScreenerConfig | None = None) -> None:
        self._cfg = config or load_screener_config()
        self._cfg_lock = threading.Lock()
        self._state = ScreenerState(self._cfg.rolling_window)
        self._opps: list[dict] = []
        self._scanned_at_iso: str | None = None
        self._total_universe = 0
        self._last_loaded_at: str | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # SSE fan-out
        self._subs: list[queue.Queue] = []
        self._sub_lock = threading.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="spread-screener"
        )
        self._thread.start()
        logger.info("ScreenerEngine started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None

    # ── config (hot-reload) ──────────────────────────────────────────────────

    def get_config(self) -> dict:
        with self._cfg_lock:
            return config_to_dict(self._cfg)

    def update_config(self, patch: dict) -> dict:
        with self._cfg_lock:
            self._cfg = apply_config_patch(self._cfg, patch)
            cfg = self._cfg
        self._state.set_rolling_window(cfg.rolling_window)
        return config_to_dict(cfg)

    # ── output ───────────────────────────────────────────────────────────────

    def get_opportunities(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            opps = list(self._opps)
        if limit is not None and limit > 0:
            opps = opps[:limit]
        return opps

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "opportunities": list(self._opps),
                "opportunity_count": len(self._opps),
                "scanned_at": self._scanned_at_iso,
                "total_universe": self._total_universe,
            }

    # ── SSE pub/sub ──────────────────────────────────────────────────────────

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=16)
        with self._sub_lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._sub_lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def _notify_subs(self, payload: dict) -> None:
        with self._sub_lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass

    # ── main loop ────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._scan_once()
            except Exception:
                logger.exception("Screener scan error")
            with self._cfg_lock:
                interval = max(1.0, self._cfg.scan_interval_sec)
            self._stop_event.wait(timeout=interval)

    def _scan_once(self) -> None:
        with self._cfg_lock:
            cfg = self._cfg

        df, err = safe_load_snapshot("spot")
        if df is None or err:
            logger.debug("Screener: snapshot unavailable (%s)", err)
            return
        if len(df) == 0:
            return

        # observed_at is uniform across rows (set at load time).
        observed_at = str(getattr(df.iloc[0], "observed_at", "") or "")
        now_ms = time.time() * 1000.0
        observed_ms = _parse_iso_ms(observed_at)
        tick_age_ms = (
            max(0.0, now_ms - observed_ms) if observed_ms is not None else 0.0
        )

        with self._lock:
            prev_loaded_at = self._last_loaded_at
        is_new_snapshot = observed_at != prev_loaded_at

        threshold = cfg.min_net_spread_bps
        active: set[str] = set()
        scored: list[tuple[float, Candidate]] = []

        for row in df.itertuples(index=False):
            symbol = getattr(row, "symbol", None)
            if not symbol or not isinstance(symbol, str):
                continue
            sym = symbol.strip().upper()
            active.add(sym)

            spread_bps = _maybe(getattr(row, "spread_bps", None))
            bid = _num(getattr(row, "bid", 0.0), 0.0)
            ask = _num(getattr(row, "ask", 0.0), 0.0)
            mid = _num(getattr(row, "mid", 0.0), 0.0)
            l1_notional = _num(getattr(row, "l1_max_notional_quote", 0.0), 0.0)
            vol24 = _num(getattr(row, "volume_24h_quote", 0.0), 0.0)

            net = (
                spread_bps - cfg.maker_fee_round_trip_bps
                if spread_bps is not None
                else None
            )

            if is_new_snapshot:
                self._state.update(sym, spread_bps, threshold, now_ms)

            lifetime = self._state.get_lifetime(sym, now_ms)
            pct_above, spread_std = self._state.get_rolling(sym, threshold)

            c = Candidate(
                symbol=sym,
                bid=bid,
                ask=ask,
                mid=mid,
                spread_bps=spread_bps,
                net_spread_bps=net,
                l1_notional=l1_notional,
                volume_24h_quote=vol24,
                tick_age_ms=tick_age_ms,
                observed_at_iso=observed_at,
                lifetime_sec=lifetime,
                pct_time_above=pct_above,
                spread_std=spread_std,
            )

            passed, _reasons = passes_gates(c, cfg)
            if not passed:
                continue
            score, breakdown = score_candidate(c, cfg)
            scored.append((score, replace(c, score=score, score_breakdown=breakdown)))

        if is_new_snapshot:
            self._state.prune(active)
            with self._lock:
                self._last_loaded_at = observed_at

        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[: cfg.top_limit]
        from mexc_monitor.screener.models import opportunity_to_dict

        opportunities = [
            opportunity_to_dict(candidate_to_opportunity(c)) for _, c in top
        ]

        scanned_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._opps = opportunities
            self._scanned_at_iso = scanned_at
            self._total_universe = len(active)

        self._notify_subs(
            {
                "scanned_at": scanned_at,
                "total_universe": len(active),
                "opportunity_count": len(opportunities),
                "opportunities": opportunities,
            }
        )
