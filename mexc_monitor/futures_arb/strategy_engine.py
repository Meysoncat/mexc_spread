"""
Futures/Spot Arbitrage Strategy Engine.

Основной движок, координирующий стратегии арбитража спот-фьючерс:
- Cash-and-Carry: покупка спота + шорт фьючерса при премии перпа
- Reverse Cash-and-Carry: шорт спота + лонг фьючерса при дисконте перпа
- Funding Rate Arbitrage: дельта-нейтральная позиция для сбора funding-платежей

Поддерживает paper/live режимы с идентичной логикой принятия решений.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import asdict, fields
from typing import Any, Callable, Protocol

from mexc_monitor.futures_arb.basis_calculator import BasisCalculator
from mexc_monitor.futures_arb.funding_tracker import FundingTracker
from mexc_monitor.futures_arb.models import (
    BasisSnapshot,
    FundingInfo,
    FuturesArbPosition,
    FuturesArbSettings,
    FuturesArbStats,
)

logger = logging.getLogger(__name__)


# --- Helper functions ---


def _futures_exchange_from_combo(combo: str) -> str | None:
    """Extract the futures/perp exchange from an exchange_combo string."""
    _FUTURES_MAP = {
        "mexc_spot+mexc_futures": "mexc_futures",
        "mexc_spot+asterdex_perp": "asterdex_perp",
        "asterdex_perp+mexc_futures": "mexc_futures",
    }
    return _FUTURES_MAP.get(combo)


def _spot_exchange_from_combo(combo: str) -> str | None:
    """Extract the spot-like exchange from an exchange_combo string."""
    _SPOT_MAP = {
        "mexc_spot+mexc_futures": "mexc_spot",
        "mexc_spot+asterdex_perp": "mexc_spot",
        "asterdex_perp+mexc_futures": "asterdex_perp",
    }
    return _SPOT_MAP.get(combo)


# --- Protocols for dependencies ---


class PositionManagerProtocol(Protocol):
    def get_open_positions(self) -> list[FuturesArbPosition]: ...
    def open_position(self, pos: FuturesArbPosition) -> None: ...
    def close_position(self, position_id: str, reason: str, **kwargs: Any) -> FuturesArbPosition | None: ...
    def update_funding(self, position_id: str, amount: float) -> None: ...
    def update_basis_pnl(self, position_id: str, basis_pnl: float) -> None: ...


class RiskControllerProtocol(Protocol):
    def is_kill_switch_active(self) -> bool: ...
    def check_position(self, pos: FuturesArbPosition) -> list[Any]: ...
    def should_force_close(self, pos: FuturesArbPosition) -> tuple[bool, str]: ...
    def can_open_position(self, positions: list[FuturesArbPosition], new_notional: float, symbol: str) -> tuple[bool, str]: ...


class BalanceCheckerProtocol(Protocol):
    def get_available_spot_balance(self, symbol: str) -> float: ...


class FuturesArbStrategyEngine:
    """
    Движок стратегий Futures/Spot Arbitrage.

    Фоновый поток с настраиваемым loop_interval_sec.
    На каждом шаге: проверка открытых позиций → проверка новых возможностей.
    """

    def __init__(
        self,
        settings: FuturesArbSettings,
        basis_calculator: BasisCalculator | Any | None = None,
        funding_tracker: FundingTracker | Any | None = None,
        position_manager: PositionManagerProtocol | Any | None = None,
        risk_controller: RiskControllerProtocol | Any | None = None,
        *,
        balance_checker: BalanceCheckerProtocol | Any | None = None,
        on_position_opened: Callable[[FuturesArbPosition], None] | None = None,
        on_position_closed: Callable[[FuturesArbPosition], None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ):
        self._settings = settings
        self._basis_calculator = basis_calculator
        self._funding_tracker = funding_tracker
        self._position_manager = position_manager
        self._risk_controller = risk_controller
        self._balance_checker = balance_checker
        self._on_position_opened = on_position_opened
        self._on_position_closed = on_position_closed
        self._on_event = on_event

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Events log
        self._events: list[dict[str, Any]] = []

    # --- Lifecycle ---

    def start(self) -> dict[str, Any]:
        """Start the strategy engine background loop."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.get_status()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="futures-arb-engine",
            )
            self._thread.start()
        self._append_event({"type": "engine_started", "mode": self._settings.mode})
        logger.info("FuturesArbStrategyEngine started (mode=%s)", self._settings.mode)
        return self.get_status()

    def stop(self) -> dict[str, Any]:
        """Stop the strategy engine."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._settings.loop_interval_sec + 5)
            self._thread = None
        self._append_event({"type": "engine_stopped"})
        logger.info("FuturesArbStrategyEngine stopped")
        return self.get_status()

    def is_running(self) -> bool:
        """Check if the engine is currently running."""
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict[str, Any]:
        """Get current engine status."""
        open_positions = self._get_open_positions()
        total_exposure = sum(p.notional_usdt for p in open_positions)

        # Get current basis
        current_basis: list[dict[str, Any]] = []
        if self._basis_calculator:
            for snap in self._basis_calculator.get_all_basis():
                current_basis.append(asdict(snap))

        return {
            "running": self.is_running(),
            "mode": self._settings.mode,
            "kill_switch": self._is_kill_switch_active(),
            "open_count": len(open_positions),
            "open_positions": [asdict(p) for p in open_positions],
            "total_exposure_usdt": total_exposure,
            "current_basis": current_basis,
            "settings": asdict(self._settings),
        }

    # --- Settings ---

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        """
        Update settings at runtime without affecting open positions.

        Clamps values to valid ranges where appropriate.
        Returns the updated status dict.
        """
        current = asdict(self._settings)
        current.update(patch)

        # Clamp values
        if "futures_leverage" in current:
            current["futures_leverage"] = max(1, min(20, int(current["futures_leverage"])))
        if "max_concurrent_positions" in current:
            current["max_concurrent_positions"] = max(1, min(20, int(current["max_concurrent_positions"])))

        # Reconstruct settings
        known_fields = {f.name for f in fields(FuturesArbSettings)}
        filtered = {k: v for k, v in current.items() if k in known_fields}
        self._settings = FuturesArbSettings(**filtered)

        self._append_event({"type": "settings_updated", "patch": patch})
        logger.info("Settings updated: %s", patch)
        return self.get_status()

    @property
    def settings(self) -> FuturesArbSettings:
        return self._settings

    # --- Events ---

    def get_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent events (most recent first)."""
        with self._lock:
            return list(reversed(self._events[-limit:]))

    # --- Internal loop ---

    def _run_loop(self) -> None:
        """Background loop: step at loop_interval_sec."""
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self._step()
            except Exception as e:
                self._append_event({"type": "error", "message": f"{type(e).__name__}: {e}"})
                logger.exception("Strategy engine step error")
            spent = time.monotonic() - started
            sleep_for = max(0.0, self._settings.loop_interval_sec - spent)
            self._stop_event.wait(timeout=sleep_for)

    def _step(self) -> None:
        """One iteration: check exits, then check entries."""
        if not self._settings.enabled:
            return

        if self._is_kill_switch_active():
            self._close_all_positions("kill_switch")
            return

        self._check_open_positions()
        self._check_entry_opportunities()

    # --- Entry logic ---

    def _check_entry_opportunities(self) -> None:
        """Evaluate all symbols × exchange_combos for entry opportunities."""
        if self._is_kill_switch_active():
            return

        open_positions = self._get_open_positions()
        if len(open_positions) >= self._settings.max_concurrent_positions:
            return

        total_exposure = sum(p.notional_usdt for p in open_positions)
        if total_exposure + self._settings.position_notional_usdt > self._settings.max_total_exposure_usdt:
            return

        for symbol in self._settings.symbols:
            # Per-symbol notional check
            symbol_exposure = sum(
                p.notional_usdt for p in open_positions if p.symbol == symbol
            )
            if symbol_exposure + self._settings.position_notional_usdt > self._settings.max_per_symbol_notional_usdt:
                continue

            # Gather candidates across all combos
            candidates: list[tuple[BasisSnapshot, str]] = []

            for combo in self._settings.exchange_combos:
                snapshot = self._get_basis(symbol, combo)
                if snapshot is None or snapshot.status == "stale":
                    continue

                # Check cash-and-carry
                if snapshot.executable_basis_cc_bps >= self._settings.entry_threshold_bps:
                    candidates.append((snapshot, "cash_and_carry"))

                # Check reverse cash-and-carry
                if snapshot.executable_basis_rcc_bps >= self._settings.entry_threshold_bps:
                    candidates.append((snapshot, "reverse_cash_and_carry"))

                # Check funding arbitrage
                futures_exchange = _futures_exchange_from_combo(combo)
                if futures_exchange and self._funding_tracker:
                    funding_info = self._funding_tracker.get_funding(symbol, futures_exchange)
                    if funding_info and self._should_enter_funding_arb(funding_info):
                        candidates.append((snapshot, "funding_arb"))

            if not candidates:
                continue

            # Select best candidate: highest executable basis
            best_snapshot, best_strategy = self._select_best_candidate(candidates)

            # For reverse cash-and-carry, check spot balance
            if best_strategy == "reverse_cash_and_carry":
                if not self._check_spot_balance(symbol, best_snapshot):
                    continue

            # Open position
            self._open_position(best_snapshot, best_strategy)

            # Re-check limits after opening
            open_positions = self._get_open_positions()
            if len(open_positions) >= self._settings.max_concurrent_positions:
                break
            total_exposure = sum(p.notional_usdt for p in open_positions)
            if total_exposure + self._settings.position_notional_usdt > self._settings.max_total_exposure_usdt:
                break

    def _should_enter_funding_arb(self, funding_info: FundingInfo) -> bool:
        """Check if funding arbitrage entry conditions are met.

        Uses z-score significance test: |z| >= 2.0 means the current funding
        rate is 2+ standard deviations from the 30d mean — statistically
        unusual and likely to revert.
        """
        z = funding_info.z_score
        # Require |z| >= 2.0 for statistical significance
        if abs(z) < 2.0:
            return False
        # Direction must be sustained: sign(rate) == sign(avg_7d)
        rate = funding_info.current_rate
        avg_7d = funding_info.avg_7d
        if rate == 0 or avg_7d == 0:
            return False
        if (rate > 0) != (avg_7d > 0):
            return False
        return True

    def _check_spot_balance(self, symbol: str, snapshot: BasisSnapshot) -> bool:
        """Check if sufficient spot balance exists for reverse cash-and-carry."""
        if self._balance_checker is None:
            return True  # No checker = paper mode, always allow

        required_qty = self._settings.position_notional_usdt / snapshot.spot_mid
        available = self._balance_checker.get_available_spot_balance(symbol)

        if available < required_qty:
            self._append_event({
                "type": "insufficient_spot_balance",
                "symbol": symbol,
                "required_qty": required_qty,
                "available_qty": available,
            })
            return False
        return True

    def _select_best_candidate(
        self, candidates: list[tuple[BasisSnapshot, str]]
    ) -> tuple[BasisSnapshot, str]:
        """Select the best entry candidate by expected return."""
        def _score(item: tuple[BasisSnapshot, str]) -> float:
            snap, strategy = item
            if strategy == "cash_and_carry":
                return snap.executable_basis_cc_bps
            elif strategy == "reverse_cash_and_carry":
                return snap.executable_basis_rcc_bps
            else:  # funding_arb — score by annualized yield (bps equivalent)
                futures_exchange = _futures_exchange_from_combo(snap.exchange_combo)
                if futures_exchange and self._funding_tracker:
                    fi = self._funding_tracker.get_funding(snap.symbol, futures_exchange)
                    if fi:
                        # Convert annualized yield to per-trade bps (assume hold = expected_hold_hours)
                        per_trade_bps = fi.annualized_yield * (
                            self._settings.expected_hold_hours / (365 * 24)
                        ) * 100
                        return per_trade_bps
                return 0.0

        return max(candidates, key=_score)

    # --- Position opening ---

    def _open_position(self, snapshot: BasisSnapshot, strategy: str) -> None:
        """Open a new arbitrage position."""
        now_ms = int(time.time() * 1000)
        position_id = str(uuid.uuid4())[:12]

        # Determine sides based on strategy
        if strategy == "cash_and_carry":
            spot_side = "buy"
            futures_side = "short"
            entry_basis_bps = snapshot.executable_basis_cc_bps
        elif strategy == "reverse_cash_and_carry":
            spot_side = "sell"
            futures_side = "long"
            entry_basis_bps = snapshot.executable_basis_rcc_bps
        else:  # funding_arb
            # Positive funding → long spot + short perp
            # Negative funding → short spot + long perp
            futures_exchange = _futures_exchange_from_combo(snapshot.exchange_combo)
            funding_info = None
            if futures_exchange and self._funding_tracker:
                funding_info = self._funding_tracker.get_funding(snapshot.symbol, futures_exchange)

            if funding_info and funding_info.current_rate < 0:
                spot_side = "sell"
                futures_side = "long"
            else:
                spot_side = "buy"
                futures_side = "short"
            entry_basis_bps = snapshot.basis_bps

        # Position sizing
        notional = self._settings.position_notional_usdt
        spot_price = snapshot.spot_mid
        futures_price = snapshot.futures_mid

        if spot_price <= 0 or futures_price <= 0:
            return

        spot_qty = notional / spot_price
        futures_qty = notional / futures_price

        # Compute entry fees
        spot_fee = notional * self._settings.spot_taker_fee_bps / 10000.0
        futures_fee = notional * self._settings.futures_taker_fee_bps / 10000.0
        entry_fees = spot_fee + futures_fee

        position = FuturesArbPosition(
            id=position_id,
            symbol=snapshot.symbol,
            exchange_combo=snapshot.exchange_combo,
            strategy=strategy,
            state="open",
            spot_side=spot_side,
            spot_entry_price=spot_price,
            spot_qty=spot_qty,
            futures_side=futures_side,
            futures_entry_price=futures_price,
            futures_qty=futures_qty,
            futures_leverage=self._settings.futures_leverage,
            notional_usdt=notional,
            entry_basis_bps=entry_basis_bps,
            open_time_ms=now_ms,
            entry_fees=entry_fees,
        )

        # Register with position manager
        if self._position_manager:
            self._position_manager.open_position(position)

        self._append_event({
            "type": "position_opened",
            "position_id": position_id,
            "symbol": snapshot.symbol,
            "exchange_combo": snapshot.exchange_combo,
            "strategy": strategy,
            "entry_basis_bps": round(entry_basis_bps, 2),
            "notional_usdt": notional,
            "mode": self._settings.mode,
        })

        if self._on_position_opened:
            try:
                self._on_position_opened(position)
            except Exception:
                logger.exception("Error in on_position_opened callback")

    # --- Exit logic ---

    def _check_open_positions(self) -> None:
        """Check all open positions for exit conditions."""
        open_positions = self._get_open_positions()
        now_ms = int(time.time() * 1000)

        for pos in open_positions:
            # Resolve current market prices for risk checks
            snapshot = self._get_basis(pos.symbol, pos.exchange_combo)
            cur_spot = snapshot.spot_mid if snapshot and snapshot.status != "stale" else 0.0
            cur_fut = snapshot.futures_mid if snapshot and snapshot.status != "stale" else 0.0

            # Update margin ratio with live prices (paper-mode synthetic)
            if self._risk_controller and cur_spot > 0 and cur_fut > 0:
                alerts = self._risk_controller.check_position(
                    pos,
                    current_spot_price=cur_spot,
                    current_futures_price=cur_fut,
                )
                for alert in alerts:
                    if alert.level == "critical":
                        self._append_event({
                            "type": "risk_alert",
                            "level": alert.level,
                            "alert_type": alert.alert_type,
                            "symbol": alert.symbol,
                            "message": alert.message,
                        })

            # Check risk controller force close
            if self._risk_controller:
                should_close, reason = self._risk_controller.should_force_close(pos)
                if should_close:
                    self._close_position(pos.id, reason)
                    continue

            # Check strategy exit conditions
            reason = self._evaluate_exit(pos, now_ms)
            if reason:
                self._close_position(pos.id, reason)

    def _evaluate_exit(self, pos: FuturesArbPosition, now_ms: int) -> str | None:
        """
        Evaluate exit conditions for a position.
        Returns close reason or None if position should stay open.
        """
        # Max hold duration
        hold_hours = (now_ms - pos.open_time_ms) / (3600 * 1000)
        if hold_hours >= self._settings.max_hold_duration_hours:
            return "max_duration"

        # Get current basis
        snapshot = self._get_basis(pos.symbol, pos.exchange_combo)
        if snapshot is None or snapshot.status == "stale":
            return None  # Can't evaluate without data

        current_basis_bps = snapshot.basis_bps

        # For cash-and-carry: long spot + short futures.
        # Profit when basis narrows (falls toward 0). The convergence check
        # ``current <= exit_threshold`` also catches sign-flip to negative.
        # Adverse = basis grows further positive (diverges upward).
        if pos.strategy == "cash_and_carry":
            if current_basis_bps <= self._settings.exit_threshold_bps:
                return "basis_converged"
            # Stop loss: adverse directional move (basis rising)
            if current_basis_bps - pos.entry_basis_bps > self._settings.max_basis_divergence_bps:
                return "stop_loss"

        # For reverse cash-and-carry: short spot + long futures.
        # Profit when basis rises toward 0 or above (negative basis narrows).
        # Adverse = basis falls further (diverges downward).
        elif pos.strategy == "reverse_cash_and_carry":
            if abs(current_basis_bps) <= self._settings.exit_threshold_bps:
                return "basis_converged"
            # Sign-flip exit: basis crossed through zero — original edge fully
            # captured. Catches overshoots that skip the near-zero band between
            # engine steps (abs() check alone would miss a jump from -50 to +60).
            if pos.entry_basis_bps < 0 and current_basis_bps >= 0:
                return "basis_reversed"
            # Stop loss: adverse directional move (basis falling).
            # NOTE: uses directional difference, NOT abs(), so a favorable
            # overshoot to large-positive basis does NOT trigger a false stop.
            if pos.entry_basis_bps - current_basis_bps > self._settings.max_basis_divergence_bps:
                return "stop_loss"

        # For funding arb: check funding direction reversal or z-score decay
        elif pos.strategy == "funding_arb":
            futures_exchange = _futures_exchange_from_combo(pos.exchange_combo)
            if futures_exchange and self._funding_tracker:
                funding_info = self._funding_tracker.get_funding(pos.symbol, futures_exchange)
                if funding_info:
                    if funding_info.direction_changed:
                        return "funding_direction_reversed"
                    # Exit when z-score decays below 0.5 (funding normalized)
                    if abs(funding_info.z_score) < 0.5:
                        return "funding_decay"

        # Target profit check (all strategies)
        if pos.notional_usdt > 0:
            total_pnl_bps = pos.total_pnl / pos.notional_usdt * 10000
            if total_pnl_bps >= self._settings.target_profit_bps:
                return "target_reached"

        return None

    # --- Position closing ---

    def _close_position(self, position_id: str, reason: str) -> None:
        """Close a position with the given reason."""
        if self._position_manager:
            pos = self._position_manager.close_position(position_id, reason)
            if pos:
                self._append_event({
                    "type": "position_closed",
                    "position_id": position_id,
                    "symbol": pos.symbol,
                    "reason": reason,
                    "total_pnl": round(pos.total_pnl, 4),
                })
                if self._on_position_closed:
                    try:
                        self._on_position_closed(pos)
                    except Exception:
                        logger.exception("Error in on_position_closed callback")

    def _close_all_positions(self, reason: str) -> None:
        """Close all open positions (used by kill switch)."""
        open_positions = self._get_open_positions()
        for pos in open_positions:
            self._close_position(pos.id, reason)

    # --- Helpers ---

    def _get_open_positions(self) -> list[FuturesArbPosition]:
        """Get open positions from position manager."""
        if self._position_manager:
            return self._position_manager.get_open_positions()
        return []

    def _get_basis(self, symbol: str, combo: str) -> BasisSnapshot | None:
        """Get current basis from BasisCalculator."""
        if self._basis_calculator is None:
            return None
        return self._basis_calculator.get_current_basis(symbol, combo)

    def _is_kill_switch_active(self) -> bool:
        """Check kill switch from settings and risk controller."""
        if self._settings.kill_switch:
            return True
        if self._risk_controller:
            return self._risk_controller.is_kill_switch_active()
        return False

    def _append_event(self, event: dict[str, Any]) -> None:
        """Append an event to the log."""
        event["timestamp_ms"] = int(time.time() * 1000)
        with self._lock:
            self._events.append(event)
            # Keep last 1000 events
            if len(self._events) > 1000:
                self._events = self._events[-500:]

        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass
