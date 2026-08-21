"""ScreenerEngine — background loop that scans the spot universe and keeps a
ranked list of tradeable-spread opportunities.

Cadence is bounded by the snapshot cache (~3 s); the engine polls every
``scan_interval_sec`` but only mutates per-symbol state when a *new* snapshot
arrives (so the rolling window reflects real data cadence, not poll cadence).
Lifetime still advances with wall-clock on every poll, since it is
``now − first_above_ms``.

When ``adaptive_mode`` is on, the spread-size decision is regime-relative:
a percentile cutoff of the live universe (``compute_percentile_cutoff``) and a
per-symbol z-score gate replace manual threshold tuning. A slow controller
(``_calibrate`` / ``_calibrate_step``) nudges the percentile so the opportunity
count stays within a target band (genuine opportunities are rare — a few at a
time).
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections import Counter, deque
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from mexc_monitor.pipeline import safe_load_snapshot
from mexc_monitor.spread_buffer import get_latest as sb_get_latest
from mexc_monitor import trade_buffer
from mexc_monitor.ws_spot_orderbook import (
    get_book_update_rate,
    reconcile_spot_orderbook_ws,
    touch_watchlist,
)
from mexc_monitor.screener.history_store import (
    close_all_open,
    query_events as query_history_events,
    record_enter,
    record_exit,
)
from mexc_monitor.screener.config import (
    ScreenerConfig,
    apply_config_patch,
    config_to_dict,
    load_screener_config,
)
from mexc_monitor.screener.filters import (
    compute_percentile_cutoff,
    passes_basic_floors,
    passes_gates,
    score_candidate,
)
from mexc_monitor.screener.models import Candidate, candidate_to_opportunity
from mexc_monitor.screener.state import ScreenerState

if TYPE_CHECKING:
    from mexc_monitor.rest_trades_poller import RestTradesPoller

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


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 == 1 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _calibrate_step(
    current_percentile: float,
    median_count: float,
    cfg: ScreenerConfig,
) -> float:
    """Pure controller: nudge the spread percentile so the opportunity count
    stays within ``[target_opportunity_min, target_opportunity_max]``.

    Too many opportunities → raise the percentile (stricter cutoff → fewer
    pass); too few → lower it; in-band → unchanged. Clamps to the configured
    ``[spread_percentile_min, spread_percentile_max]`` range.

    The step is proportional to how far the median sits outside the target
    band (in units of band width), so a badly mis-tuned percentile converges
    quickly while a near-band one nudges gently instead of oscillating.
    """
    band = max(1.0, float(cfg.target_opportunity_max - cfg.target_opportunity_min))
    if median_count > cfg.target_opportunity_max:
        excess = (median_count - cfg.target_opportunity_max) / band
        nxt = current_percentile + cfg.calibration_step * max(1.0, excess)
    elif median_count < cfg.target_opportunity_min:
        deficit = (cfg.target_opportunity_min - median_count) / band
        nxt = current_percentile - cfg.calibration_step * max(1.0, deficit)
    else:
        nxt = current_percentile
    return max(
        cfg.spread_percentile_min, min(cfg.spread_percentile_max, nxt)
    )


class ScreenerEngine:
    def __init__(
        self,
        config: ScreenerConfig | None = None,
        *,
        history_db_path: "Path | None" = None,
        rest_trades_poller: "RestTradesPoller | None" = None,
    ) -> None:
        self._cfg = config or load_screener_config()
        self._cfg_lock = threading.Lock()
        self._state = ScreenerState(self._cfg.rolling_window)
        self._history_db_path = history_db_path
        self._rest_trades_poller = rest_trades_poller
        self._prev_opp_symbols: set[str] = set()
        self._prev_top_symbols: set[str] = set()  # shortlist incumbents (hysteresis)
        self._reject_reasons_top: list[list] = []  # [[reason, count], ...]
        self._opps: list[dict] = []
        self._scanned_at_iso: str | None = None
        self._total_universe = 0
        self._last_loaded_at: str | None = None
        self._percentile_cutoff: float | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Calibration state
        self._count_history: deque[int] = deque(maxlen=120)
        self._last_calibrate_ms: float = 0.0
        self._calibrated_at_iso: str | None = None

        # SSE fan-out
        self._subs: list[queue.Queue] = []
        self._sub_lock = threading.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        # Close any opportunity events left open by a previous run.
        if self._history_db_path is not None:
            try:
                now_ms = int(time.time() * 1000.0)
                close_all_open(
                    self._history_db_path,
                    now_iso=datetime.now(timezone.utc).isoformat(),
                    now_ms=now_ms,
                )
            except Exception:
                logger.warning("Screener: close_all_open history failed", exc_info=True)
        self._prev_opp_symbols = set()
        self._prev_top_symbols = set()
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

    def get_history(
        self, *, limit: int = 100, symbol: str | None = None, only_open: bool = False
    ) -> list[dict]:
        """Recent opportunity discovery events from the persistent history."""
        if self._history_db_path is None:
            return []
        try:
            return query_history_events(
                self._history_db_path,
                limit=limit,
                symbol=symbol,
                only_open=only_open,
            )
        except Exception:
            logger.debug("Screener: history query failed", exc_info=True)
            return []

    def _record_history(self, opportunities: list[dict], scanned_at_iso: str) -> None:
        """Log enter/exit events: a coin entering the shortlist is 'found';
        on exit we record duration. Dedup'd — no row while it stays."""
        if self._history_db_path is None:
            return
        try:
            now_ms = int(time.time() * 1000.0)
            current = {str(o.get("symbol", "")).upper() for o in opportunities}
            opp_by_sym = {
                str(o.get("symbol", "")).upper(): o for o in opportunities
            }
            entered = current - self._prev_opp_symbols
            exited = self._prev_opp_symbols - current
            for sym in entered:
                opp = opp_by_sym.get(sym)
                if not opp:
                    continue
                record_enter(
                    self._history_db_path,
                    symbol=sym,
                    found_at_iso=scanned_at_iso,
                    found_at_ms=now_ms,
                    opp=opp,
                )
            for sym in exited:
                record_exit(
                    self._history_db_path,
                    symbol=sym,
                    exited_at_iso=scanned_at_iso,
                    exited_at_ms=now_ms,
                )
            self._prev_opp_symbols = current
        except Exception:
            logger.debug("Screener: history record failed", exc_info=True)

    def get_status(self) -> dict:
        with self._lock:
            with self._cfg_lock:
                cfg = self._cfg
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "opportunities": list(self._opps),
                "opportunity_count": len(self._opps),
                "scanned_at": self._scanned_at_iso,
                "total_universe": self._total_universe,
                "adaptive_mode": cfg.adaptive_mode,
                "spread_percentile": cfg.spread_percentile,
                "percentile_cutoff": self._percentile_cutoff,
                "calibrated_at": self._calibrated_at_iso,
                "target_opportunity_min": cfg.target_opportunity_min,
                "target_opportunity_max": cfg.target_opportunity_max,
                "reject_reasons_top": [list(r) for r in self._reject_reasons_top],
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
                with self._cfg_lock:
                    cal_interval_ms = self._cfg.calibration_interval_sec * 1000.0
                if (time.time() * 1000.0 - self._last_calibrate_ms) >= cal_interval_ms:
                    try:
                        self._calibrate()
                    except Exception:
                        logger.exception("Screener calibration error")
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

        # Phase A — build candidates with state metrics; update rolling state
        # only on genuinely new snapshots.
        candidates: list[Candidate] = []
        all_nets: list[float] = []
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
                # Lifetime / pct-above must measure the *net* spread against
                # the net floor — comparing gross to a net threshold inflates
                # persistence whenever a non-zero fee is configured.
                self._state.update(sym, net, threshold, now_ms)

            lifetime = self._state.get_lifetime(sym, now_ms)
            pct_above, spread_std = self._state.get_rolling(sym, threshold)
            zscore = self._state.get_zscore(sym, spread_bps)
            # Tier 1.5 activity signal (None for symbols not on the bookTicker WS).
            try:
                book_rate = get_book_update_rate(sym)
            except Exception:
                book_rate = None
            # Real-time trades (REST trades poller → trade_buffer). None if not polled.
            try:
                tstat = trade_buffer.get_stats(sym)
            except Exception:
                tstat = None
            trades_per_min = tstat.trades_per_min if tstat else None
            buy_sell_ratio = tstat.buy_sell_ratio if tstat else None
            trade_vol_60 = tstat.volume_quote if tstat else None

            # Per-symbol freshness: when the symbol is on a live feed, its
            # own last-tick timestamp decides freshness instead of the
            # snapshot-global age (which was identical for every row).
            sym_age_ms = tick_age_ms
            try:
                _tick = sb_get_latest(sym)
            except Exception:
                _tick = None
            if _tick is not None:
                sym_age_ms = max(0.0, now_ms - float(_tick.timestamp_ms))

            c = Candidate(
                symbol=sym,
                bid=bid,
                ask=ask,
                mid=mid,
                spread_bps=spread_bps,
                net_spread_bps=net,
                l1_notional=l1_notional,
                volume_24h_quote=vol24,
                tick_age_ms=sym_age_ms,
                observed_at_iso=observed_at,
                lifetime_sec=lifetime,
                pct_time_above=pct_above,
                spread_std=spread_std,
                spread_zscore=zscore,
                book_update_rate_per_min=book_rate,
                trades_per_min=trades_per_min,
                buy_sell_ratio=buy_sell_ratio,
                trade_volume_quote_60s=trade_vol_60,
            )
            candidates.append(c)
            # Clean universe for the percentile gate: only sane, tradeable
            # rows feed the regime reference — dead/illiquid/stale junk must
            # not drag the cutoff the calibration steers.
            if net is not None and passes_basic_floors(c, cfg):
                all_nets.append(net)

        if is_new_snapshot:
            self._state.prune(active)
            with self._lock:
                self._last_loaded_at = observed_at

        # Phase B — adaptive percentile cutoff over the live universe.
        if cfg.adaptive_mode:
            percentile_cutoff = compute_percentile_cutoff(
                all_nets, cfg.spread_percentile
            )
            adaptive_ctx: dict | None = {"percentile_cutoff": percentile_cutoff}
        else:
            percentile_cutoff = None
            adaptive_ctx = None

        # Phase C — gate + score. Shortlist incumbents get a small hysteresis
        # margin so a coin hovering at the gate edge doesn't flap in/out of
        # the top on every scan.
        with self._lock:
            prev_top = set(self._prev_top_symbols)
        reject_counts: Counter[str] = Counter()
        scored: list[tuple[float, Candidate]] = []
        for c in candidates:
            hyst = cfg.exit_hysteresis_bps if c.symbol in prev_top else 0.0
            passed, reasons = passes_gates(c, cfg, adaptive_ctx, hysteresis_bps=hyst)
            if not passed:
                # Count the first (primary) reason per candidate.
                if reasons:
                    reject_counts[reasons[0].split(" ")[0]] += 1
                continue
            score, breakdown = score_candidate(c, cfg)
            scored.append((score, replace(c, score=score, score_breakdown=breakdown)))

        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[: cfg.top_limit]

        # Tier 1.5: promote the shortlist into the bookTicker WS so their
        # update-rate (activity) matures. Reconcile self-throttles internally.
        try:
            reconcile_spot_orderbook_ws()
            touch_watchlist([c.symbol for _, c in top])
        except Exception:
            logger.debug("Screener: bookTicker watchlist reconcile skipped", exc_info=True)

        # Feed the same shortlist to the REST trades poller (→ trade_buffer:
        # buy/sell imbalance, trades/min, VWAP).
        if self._rest_trades_poller is not None:
            try:
                self._rest_trades_poller.set_watch_symbols([c.symbol for _, c in top])
            except Exception:
                logger.debug("Screener: rest-trades watch set skipped", exc_info=True)

        from mexc_monitor.screener.models import opportunity_to_dict

        opportunities = [
            opportunity_to_dict(candidate_to_opportunity(c)) for _, c in top
        ]

        scanned_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._opps = opportunities
            self._scanned_at_iso = scanned_at
            self._total_universe = len(active)
            self._percentile_cutoff = percentile_cutoff
            self._count_history.append(len(opportunities))
            self._prev_top_symbols = {c.symbol for _, c in top}
            self._reject_reasons_top = [
                [reason, count] for reason, count in reject_counts.most_common(5)
            ]

        self._record_history(opportunities, scanned_at)

        self._notify_subs(
            {
                "scanned_at": scanned_at,
                "total_universe": len(active),
                "opportunity_count": len(opportunities),
                "spread_percentile": cfg.spread_percentile,
                "percentile_cutoff": percentile_cutoff,
                "opportunities": opportunities,
            }
        )

    # ── calibration ──────────────────────────────────────────────────────────

    def _calibrate(self) -> None:
        with self._cfg_lock:
            cfg = self._cfg
        if not cfg.adaptive_mode:
            self._last_calibrate_ms = time.time() * 1000.0
            return
        with self._lock:
            counts = [float(x) for x in self._count_history]
        if not counts:
            self._last_calibrate_ms = time.time() * 1000.0
            return
        med = _median(counts)
        with self._cfg_lock:
            cur = self._cfg.spread_percentile
            new_pct = _calibrate_step(cur, med, self._cfg)
            if new_pct != cur:
                self._cfg = replace(self._cfg, spread_percentile=new_pct)
                logger.info(
                    "Screener calibration: percentile %.1f -> %.1f (median count %.1f, target %d-%d)",
                    cur,
                    new_pct,
                    med,
                    self._cfg.target_opportunity_min,
                    self._cfg.target_opportunity_max,
                )
        self._last_calibrate_ms = time.time() * 1000.0
        self._calibrated_at_iso = datetime.now(timezone.utc).isoformat()
