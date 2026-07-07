"""Lead-Lag Arbitrage Engine orchestrator.

Orchestrates: WS Manager → Price Buffer → Lag Detector → Signal Generator → Store.
Provides a unified interface for starting/stopping the engine and querying state.

Requirements: 10.1, 10.2, 10.3
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Optional

from mexc_monitor.lead_lag.config import LeadLagConfig, load_lead_lag_config, validate_config
from mexc_monitor.lead_lag.models import (
    LagEstimate,
    LeadLagSignal,
    LeadLagStats,
    SignalStatus,
)
from mexc_monitor.lead_lag.price_buffer import PriceBuffer
from mexc_monitor.lead_lag.detector import LagDetector
from mexc_monitor.lead_lag.signals import SignalGenerator
from mexc_monitor.lead_lag.stats import StatsEngine
from mexc_monitor.lead_lag.store import LeadLagStore
from mexc_monitor.lead_lag.ws_manager import LeadLagWSManager

logger = logging.getLogger(__name__)

# Recovery requires 5 seconds of continuous data
_RECOVERY_DATA_SEC = 5.0


class EngineStatus(str, enum.Enum):
    """Engine operational status."""

    STOPPED = "stopped"
    RUNNING = "running"
    DEGRADED = "degraded"       # All laggers disconnected/stale
    NO_LEADER = "no_leader"     # Leader (Binance) disconnected/stale


class LeadLagEngine:
    """Orchestrates all lead-lag components.

    Provides start/stop lifecycle, status reporting, and data access methods
    for the REST API layer.
    """

    def __init__(self, config: Optional[LeadLagConfig] = None) -> None:
        self._config = config or load_lead_lag_config()
        self._status = EngineStatus.STOPPED
        self._started_at: Optional[float] = None
        self._lock = threading.Lock()
        self._signals_paused = False

        # Recovery tracking: {exchange: time_when_recovery_started}
        self._recovery_start: dict[str, float] = {}

        # Initialize components
        self._price_buffer = PriceBuffer(
            max_history_sec=self._config.price_buffer_history_sec
        )
        self._store = LeadLagStore(db_path=self._config.db_path)
        self._lag_detector = LagDetector(self._config)
        self._signal_generator = SignalGenerator(
            config=self._config,
            price_buffer=self._price_buffer,
            lag_detector=self._lag_detector,
        )
        self._stats_engine = StatsEngine(self._store)
        self._ws_manager = LeadLagWSManager(self._config, self._price_buffer)

        # Background threads
        self._lag_thread: Optional[threading.Thread] = None
        self._signal_thread: Optional[threading.Thread] = None
        self._status_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> LeadLagConfig:
        return self._config

    @property
    def status(self) -> EngineStatus:
        return self._status

    @property
    def uptime_sec(self) -> float:
        if self._started_at is None or self._status == EngineStatus.STOPPED:
            return 0.0
        return time.time() - self._started_at

    @property
    def price_buffer(self) -> PriceBuffer:
        return self._price_buffer

    @property
    def lag_detector(self) -> LagDetector:
        return self._lag_detector

    @property
    def signal_generator(self) -> SignalGenerator:
        return self._signal_generator

    @property
    def store(self) -> LeadLagStore:
        return self._store

    @property
    def ws_manager(self) -> LeadLagWSManager:
        return self._ws_manager

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> Optional[str]:
        """Start the engine. Idempotent — returns None if already running.

        Returns None on success, or an error message string on failure.
        """
        with self._lock:
            if self._status != EngineStatus.STOPPED:
                return None

            # Validate config
            errors = validate_config(self._config)
            if errors:
                return "Validation failed: " + "; ".join(errors)

            # Start store retry loop
            self._store.start_retry_loop()

            # Start WS connections
            self._ws_manager.start()

            # Start background threads
            self._stop_event.clear()
            self._lag_thread = threading.Thread(
                target=self._lag_estimation_loop,
                daemon=True,
                name="lead-lag-detector",
            )
            self._lag_thread.start()

            self._signal_thread = threading.Thread(
                target=self._signal_tick_loop,
                daemon=True,
                name="lead-lag-signals",
            )
            self._signal_thread.start()

            self._status_thread = threading.Thread(
                target=self._status_check_loop,
                daemon=True,
                name="lead-lag-status",
            )
            self._status_thread.start()

            self._status = EngineStatus.RUNNING
            self._started_at = time.time()
            self._signals_paused = False
            logger.info("LeadLagEngine started")

            return None

    def stop(self) -> None:
        """Stop the engine. Idempotent — no-op if already stopped."""
        with self._lock:
            if self._status == EngineStatus.STOPPED:
                return

            self._stop_event.set()
            self._ws_manager.stop()
            if self._lag_thread:
                self._lag_thread.join(timeout=5.0)
            if self._signal_thread:
                self._signal_thread.join(timeout=5.0)
            if self._status_thread:
                self._status_thread.join(timeout=5.0)
            self._store.stop()

            self._status = EngineStatus.STOPPED
            self._started_at = None
            self._signals_paused = False
            self._recovery_start.clear()
            logger.info("LeadLagEngine stopped")

    def is_running(self) -> bool:
        """Check if the engine is currently running (any non-stopped state)."""
        return self._status != EngineStatus.STOPPED

    # ------------------------------------------------------------------
    # Status & Data Access (for API layer)
    # ------------------------------------------------------------------

    def get_status_info(self) -> dict:
        """Return current engine status info for the API.

        Returns dict with: running, status, connections, symbols_monitored,
        active_signals_count, uptime_sec.
        """
        if self._status == EngineStatus.STOPPED:
            return {
                "running": False,
                "status": "stopped",
                "connections": {},
                "symbols_monitored": [],
                "active_signals_count": 0,
                "uptime_sec": 0.0,
            }

        connections: dict = {}
        conn_status = self._ws_manager.connection_status()
        for exchange, cs in conn_status.items():
            connections[exchange] = {
                "connected": cs.get("status") == "connected",
                "last_message_ms": cs.get("last_message_ms", 0),
            }

        active_signals_count = len(self._signal_generator.get_active_signals())

        return {
            "running": True,
            "status": self._status.value,
            "connections": connections,
            "symbols_monitored": list(self._config.symbols),
            "active_signals_count": active_signals_count,
            "uptime_sec": round(self.uptime_sec, 1),
        }

    def get_active_signals(self) -> list[LeadLagSignal]:
        """Get currently active signals."""
        if self._status == EngineStatus.STOPPED:
            return []
        return self._signal_generator.get_active_signals()

    def get_recent_signals(self, limit: int = 50) -> list[LeadLagSignal]:
        """Get recent signals (active + resolved + expired), sorted by created_at DESC."""
        if self._status == EngineStatus.STOPPED:
            return []
        return self._signal_generator.get_recent_signals(limit)

    def get_stats(self, window_hours: int = 24) -> Optional[LeadLagStats]:
        """Get aggregate statistics for the given time window."""
        return self._stats_engine.summary(window_hours)

    def get_prices(self, symbol: str) -> Optional[dict[str, float]]:
        """Get current mid-prices for a symbol across all exchanges.

        Returns dict of {exchange: mid_price} or None if symbol not monitored.
        """
        if self._status == EngineStatus.STOPPED:
            return None
        if symbol not in self._config.symbols:
            return None
        all_latest = self._price_buffer.get_all_latest(symbol)
        if not all_latest:
            return None
        return {exchange: snap.mid for exchange, snap in all_latest.items()}

    def get_lag_estimates(self) -> list[dict]:
        """Get current lag estimates for all symbols."""
        if self._status == EngineStatus.STOPPED:
            return []
        estimates = self._lag_detector.get_all_estimates()
        result = []
        for symbol, estimate in estimates.items():
            result.append({
                "symbol": estimate.symbol,
                "leader_exchange": estimate.leader_exchange,
                "lagger_exchange": estimate.lagger_exchange,
                "lag_ms": estimate.lag_ms,
                "correlation": estimate.correlation,
                "confidence": estimate.confidence,
                "sample_count": estimate.sample_count,
                "updated_at": estimate.updated_at,
            })
        return result

    # ------------------------------------------------------------------
    # Status checking (Requirement 10.1, 10.2, 10.3)
    # ------------------------------------------------------------------

    def _check_and_update_status(self) -> None:
        """Check connection health and update engine status accordingly."""
        if self._status == EngineStatus.STOPPED:
            return

        conn_status = self._ws_manager.connection_status()
        leader = self._config.leader_exchange
        laggers = self._config.lagger_exchanges

        # Check leader health
        leader_healthy = self._is_exchange_healthy(conn_status, leader)

        # Check lagger health (at least one must be healthy)
        any_lagger_healthy = any(
            self._is_exchange_healthy(conn_status, lagger)
            for lagger in laggers
        )

        # Determine target status
        if not leader_healthy:
            target_status = EngineStatus.NO_LEADER
        elif not any_lagger_healthy:
            target_status = EngineStatus.DEGRADED
        else:
            target_status = EngineStatus.RUNNING

        # Handle recovery logic
        if target_status == EngineStatus.RUNNING:
            if self._status in (EngineStatus.DEGRADED, EngineStatus.NO_LEADER):
                # Need 5 seconds of continuous data before recovery
                if self._check_recovery_complete(conn_status, leader, laggers):
                    self._status = EngineStatus.RUNNING
                    self._signals_paused = False
                    self._recovery_start.clear()
                else:
                    # Start/continue recovery timer
                    self._update_recovery_timers(conn_status, leader, laggers)
                    # Stay in current degraded/no_leader status
            else:
                # Already running, stay running
                self._status = EngineStatus.RUNNING
                self._signals_paused = False
                self._recovery_start.clear()
        else:
            # Degraded or no_leader
            self._status = target_status
            self._signals_paused = True
            # Reset recovery timers for disconnected exchanges
            self._reset_recovery_for_disconnected(conn_status, leader, laggers)

    def _is_exchange_healthy(self, conn_status: dict, exchange: str) -> bool:
        """Check if an exchange is healthy (connected, not stale)."""
        for key, cs in conn_status.items():
            if key.lower().startswith(exchange.lower()):
                status_val = cs.get("status", "disconnected")
                return status_val == "connected"
        return False

    def _check_recovery_complete(
        self, conn_status: dict, leader: str, laggers: list[str]
    ) -> bool:
        """Check if all required exchanges have had 5+ seconds of continuous data."""
        now = time.time()

        # Leader must have been recovering for 5+ seconds
        if leader not in self._recovery_start:
            return False
        if now - self._recovery_start[leader] < _RECOVERY_DATA_SEC:
            return False

        # At least one lagger must have been recovering for 5+ seconds
        any_lagger_recovered = False
        for lagger in laggers:
            if lagger in self._recovery_start:
                if now - self._recovery_start[lagger] >= _RECOVERY_DATA_SEC:
                    any_lagger_recovered = True
                    break

        return any_lagger_recovered

    def _update_recovery_timers(
        self, conn_status: dict, leader: str, laggers: list[str]
    ) -> None:
        """Start recovery timers for healthy exchanges that don't have one yet."""
        now = time.time()
        all_exchanges = [leader] + laggers

        for exchange in all_exchanges:
            if self._is_exchange_healthy(conn_status, exchange):
                if exchange not in self._recovery_start:
                    self._recovery_start[exchange] = now
            else:
                # Not healthy, remove recovery timer
                self._recovery_start.pop(exchange, None)

    def _reset_recovery_for_disconnected(
        self, conn_status: dict, leader: str, laggers: list[str]
    ) -> None:
        """Remove recovery timers for exchanges that are not healthy."""
        all_exchanges = [leader] + laggers
        for exchange in all_exchanges:
            if not self._is_exchange_healthy(conn_status, exchange):
                self._recovery_start.pop(exchange, None)

    # ------------------------------------------------------------------
    # Signal tick (called from background thread or directly)
    # ------------------------------------------------------------------

    def _run_signal_tick(self) -> None:
        """Run one signal generation tick, respecting pause state."""
        if self._signals_paused or self._status in (
            EngineStatus.DEGRADED,
            EngineStatus.NO_LEADER,
            EngineStatus.STOPPED,
        ):
            return

        new_signals = self._signal_generator.tick()
        if new_signals:
            for sig in new_signals:
                self._store.save_signal(sig)

    def _run_lag_estimation(self) -> None:
        """Run lag estimation for all symbols."""
        for symbol in self._config.symbols:
            try:
                self._lag_detector.update_estimate(symbol, self._price_buffer)
            except Exception as exc:
                logger.error("Lag estimation error for %s: %s", symbol, exc)

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    def _lag_estimation_loop(self) -> None:
        """Background thread: update lag estimates periodically."""
        interval = self._config.lag_estimation_interval_sec
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=interval)
            if self._stop_event.is_set():
                break
            try:
                self._run_lag_estimation()
            except Exception as exc:
                logger.error("Lag estimation loop error: %s", exc)

    def _signal_tick_loop(self) -> None:
        """Background thread: run signal generator tick continuously."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=0.5)
            if self._stop_event.is_set():
                break
            try:
                self._run_signal_tick()
            except Exception as exc:
                logger.error("Signal tick loop error: %s", exc)

    def _status_check_loop(self) -> None:
        """Background thread: check connection health periodically."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=1.0)
            if self._stop_event.is_set():
                break
            try:
                self._check_and_update_status()
            except Exception as exc:
                logger.error("Status check error: %s", exc)
