"""Cross-exchange screener engine.

Scans the shared spread_buffer for symbols with live WS ticks on both venues
of each configured pair (default MEXC × AsterDEX), computes the realizable
basis in both directions, gates on freshness / net edge / lifetime / z-score
and ranks the survivors. Runs as a daemon thread, mirroring ScreenerEngine.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from mexc_monitor.screener import history_store
from mexc_monitor.screener.state import ScreenerState

from .config import CrossScreenerConfig, apply_config_patch, load_config
from .models import CrossOpportunity, PairSpec
from .symbols import cross_pairs

logger = logging.getLogger(__name__)


def _basis_bps(bid: float, ask: float) -> float:
    """Realizable edge of buying at `ask` and selling at `bid`, in bps."""
    if ask <= 0:
        return float("-inf")
    return 10_000.0 * (bid - ask) / ask


def score_opportunity(
    net_bps: float,
    min_l1: float,
    lifetime_sec: float,
    spread_std: float | None,
    zscore: float | None,
    cfg: CrossScreenerConfig,
) -> tuple[float, dict]:
    """Multi-factor score for a cross-venue opportunity. Higher is better."""
    liq_factor = math.log1p(max(min_l1, 0.0) / cfg.liq_ref_usdt)
    ev_term = cfg.w_ev * min(net_bps, cfg.basis_cap_bps) * max(liq_factor, 0.1)
    life_term = cfg.w_lifetime * math.log1p(max(lifetime_sec, 0.0))
    stab_term = -cfg.w_stability * (spread_std if spread_std is not None else 0.0)
    z_term = cfg.w_zscore * min(max(zscore or 0.0, 0.0), cfg.zscore_cap)
    breakdown = {
        "ev": ev_term,
        "lifetime": life_term,
        "stability": stab_term,
        "zscore": z_term,
    }
    return ev_term + life_term + stab_term + z_term, breakdown


class CrossScreenerEngine:
    """Background scanner for cross-exchange basis opportunities."""

    def __init__(
        self,
        config: CrossScreenerConfig | None = None,
        history_db_path: str | Path | None = None,
    ) -> None:
        self._cfg = config or load_config()
        self._state = ScreenerState(rolling_window=self._cfg.rolling_window)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._opps: list[dict] = []
        self._scanned_at_iso: str | None = None
        self._reject_reasons_top: list[list] = []
        self._prev_top_keys: set[str] = set()
        self._prev_opp_keys: set[str] = set()
        self._history_path: Path | None = (
            Path(history_db_path) if history_db_path is not None else None
        )
        self._last_error: str | None = None
        self._sub_lock = threading.Lock()
        self._subs: list[queue.Queue] = []

    # ── lifecycle ────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="cross-screener"
        )
        self._thread.start()
        logger.info("CrossScreenerEngine started (%d pairs)", len(self._cfg.pairs))

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        if self._history_path is not None:
            now = datetime.now(timezone.utc)
            history_store.close_all_open(
                self._history_path,
                now_iso=now.isoformat(),
                now_ms=int(now.timestamp() * 1000),
            )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self._scan_once()
            except Exception as exc:
                logger.exception("cross-screener scan failed")
                with self._lock:
                    self._last_error = str(exc)
            elapsed = time.monotonic() - started
            self._stop_event.wait(max(0.1, self._cfg.scan_interval_sec - elapsed))

    # ── config / status ──────────────────────────────────────────────────

    def get_config(self) -> dict:
        return asdict(self._cfg)

    def update_config(self, patch: dict) -> dict:
        with self._lock:
            self._cfg = apply_config_patch(self._cfg, patch)
            cfg = self._cfg
        self._state.set_rolling_window(cfg.rolling_window)
        return asdict(cfg)

    def get_history(
        self, *, limit: int = 100, symbol: str | None = None, only_open: bool = False
    ) -> list[dict]:
        if self._history_path is None:
            return []
        try:
            return history_store.query_events(
                self._history_path, limit=limit, symbol=symbol, only_open=only_open
            )
        except Exception:
            logger.exception("cross-screener history query failed")
            return []

    # ── subscriptions (SSE fanout) ───────────────────────────────────────

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
                pass

    def get_opportunities(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            opps = list(self._opps)
        return opps[:limit] if limit else opps

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "scanned_at": self._scanned_at_iso,
                "opportunities": list(self._opps),
                "opportunity_count": len(self._opps),
                "pairs": [p.label for p in self._cfg.pairs],
                "reject_reasons_top": [list(r) for r in self._reject_reasons_top],
                "last_error": self._last_error,
            }

    # ── scan ─────────────────────────────────────────────────────────────

    def _scan_once(self) -> None:
        cfg = self._cfg
        now_ms = time.time() * 1000.0
        reject: Counter[str] = Counter()
        scored: list[tuple[float, CrossOpportunity]] = []

        for pair in cfg.pairs:
            scored.extend(self._scan_pair(pair, cfg, now_ms, reject))

        scored.sort(key=lambda t: t[0], reverse=True)
        top = [o for _, o in scored[: cfg.top_limit]]
        opportunities = [o.to_dict() for o in top]
        scanned_at = datetime.now(timezone.utc).isoformat()
        top_keys = {self._key(o) for o in top}

        with self._lock:
            prev_opp = self._prev_opp_keys
            entered = top_keys - prev_opp
            exited = prev_opp - top_keys
            self._opps = opportunities
            self._scanned_at_iso = scanned_at
            self._reject_reasons_top = [[r, c] for r, c in reject.most_common(5)]
            self._prev_top_keys = top_keys
            self._prev_opp_keys = top_keys
            self._last_error = None

        self._notify_subs(
            {
                "scanned_at": scanned_at,
                "opportunity_count": len(opportunities),
                "opportunities": opportunities,
            }
        )

        if self._history_path is not None:
            now_iso = scanned_at
            now_ms_int = int(now_ms)
            for o in top:
                if self._key(o) in entered:
                    history_store.record_enter(
                        self._history_path,
                        symbol=self._key(o),
                        found_at_iso=now_iso,
                        found_at_ms=now_ms_int,
                        opp=o.to_dict(),
                    )
            for key in exited:
                history_store.record_exit(
                    self._history_path,
                    symbol=key,
                    exited_at_iso=now_iso,
                    exited_at_ms=now_ms_int,
                )

    def _scan_pair(
        self,
        pair: PairSpec,
        cfg: CrossScreenerConfig,
        now_ms: float,
        reject: Counter[str],
    ) -> list[tuple[float, CrossOpportunity]]:
        out: list[tuple[float, CrossOpportunity]] = []
        prev_top = self._prev_top_keys
        threshold = cfg.min_net_basis_bps

        for base, tick_a, tick_b in cross_pairs(pair.exchange_a, pair.exchange_b):
            key = f"{pair.label}:{base}"
            age_a = max(0.0, now_ms - float(tick_a.timestamp_ms))
            age_b = max(0.0, now_ms - float(tick_b.timestamp_ms))
            if age_a > cfg.max_leg_age_ms or age_b > cfg.max_leg_age_ms:
                reject["stale_leg"] += 1
                continue

            # Both directions; take the better realizable one.
            edge_ab = _basis_bps(tick_b.bid, tick_a.ask)  # buy A, sell B
            edge_ba = _basis_bps(tick_a.bid, tick_b.ask)  # buy B, sell A
            if edge_ab >= edge_ba:
                edge, buy_on, sell_on = edge_ab, pair.exchange_a, pair.exchange_b
                l1_buy = tick_a.ask * max(tick_a.ask_qty, 0.0)
                l1_sell = tick_b.bid * max(tick_b.bid_qty, 0.0)
            else:
                edge, buy_on, sell_on = edge_ba, pair.exchange_b, pair.exchange_a
                l1_buy = tick_b.ask * max(tick_b.ask_qty, 0.0)
                l1_sell = tick_a.bid * max(tick_a.bid_qty, 0.0)

            net = edge - pair.fee_bps_round_trip
            state_key = key
            self._state.update(state_key, net, threshold, now_ms)
            lifetime = self._state.get_lifetime(state_key, now_ms)
            _pct_above, spread_std = self._state.get_rolling(state_key, threshold)
            z = self._state.get_zscore(state_key, abs(edge))

            hyst = cfg.exit_hysteresis_bps if key in prev_top else 0.0
            if edge > cfg.max_basis_bps:
                reject["basis_sanity"] += 1
                continue
            if net < cfg.min_net_basis_bps - hyst:
                reject["net_floor"] += 1
                continue
            if min(l1_buy, l1_sell) < cfg.min_l1_notional_usdt:
                reject["l1_liquidity"] += 1
                continue
            if lifetime < cfg.min_lifetime_sec:
                reject["lifetime"] += 1
                continue
            if cfg.use_zscore and z is not None and z < cfg.zscore_min:
                reject["zscore"] += 1
                continue

            opp = CrossOpportunity(
                symbol=base,
                exchange_a=pair.exchange_a,
                exchange_b=pair.exchange_b,
                buy_on=buy_on,
                sell_on=sell_on,
                basis_bps=edge,
                net_bps=net,
                mid_a=tick_a.mid,
                mid_b=tick_b.mid,
                l1_notional_a=l1_buy,
                l1_notional_b=l1_sell,
                tick_age_a_ms=age_a,
                tick_age_b_ms=age_b,
                lifetime_sec=lifetime,
                zscore=z,
                spread_std=spread_std,
            )
            score, breakdown = score_opportunity(
                net, min(l1_buy, l1_sell), lifetime, spread_std, z, cfg
            )
            out.append((score, replace(opp, score=score, score_breakdown=breakdown)))

        return out

    @staticmethod
    def _key(o: CrossOpportunity) -> str:
        return f"{o.exchange_a.upper()}×{o.exchange_b.upper()}:{o.symbol}"
