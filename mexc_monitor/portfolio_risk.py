"""Portfolio-level risk manager.

Aggregates exposure across all trading engines (spread_capture, arbitrage,
futures_arb) and enforces global limits:

- **Max total exposure** — sum of all open position notionals across engines.
- **Max daily drawdown** — cumulative loss circuit breaker.
- **Max correlated positions** — same symbol on multiple engines.
- **Global kill switch** — closes all engines on breach.

Each engine registers itself via :class:`EngineAdapter` so the portfolio
manager can query positions without coupling to engine internals.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


class EngineAdapter(Protocol):
    """Minimal interface for engines to register with PortfolioRiskManager."""

    @property
    def engine_name(self) -> str: ...

    def get_open_notional(self) -> float: ...
    def get_open_symbols(self) -> list[str]: ...
    def trigger_kill_switch(self) -> None: ...


@dataclass
class PortfolioRiskSettings:
    """Global risk limits."""

    max_total_exposure_usdt: float = 10_000.0
    max_daily_drawdown_usdt: float = 500.0
    max_positions_per_symbol: int = 3
    check_interval_sec: float = 5.0


@dataclass
class PortfolioRiskStatus:
    """Snapshot of portfolio risk state."""

    total_exposure_usdt: float = 0.0
    engine_count: int = 0
    positions_by_symbol: dict[str, int] = field(default_factory=dict)
    daily_drawdown_usdt: float = 0.0
    kill_switch_active: bool = False
    alerts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def all_clear(self) -> bool:
        return not self.kill_switch_active and not self.alerts


class PortfolioRiskManager:
    """Aggregates risk across all registered trading engines.

    Engines register via :meth:`register_engine`. The manager runs a
    background thread that periodically checks global limits and triggers
    a kill switch on all engines when breached.
    """

    def __init__(
        self,
        settings: PortfolioRiskSettings | None = None,
    ) -> None:
        self._settings = settings or PortfolioRiskSettings()
        self._engines: list[Any] = []
        self._lock = threading.Lock()
        self._kill_active = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Daily drawdown tracking
        self._day_start_pnl: float = 0.0
        self._last_pnl: float = 0.0
        self._day_start_time: float = time.time()

    # ─── Registration ────────────────────────────────────────────────────────

    def register_engine(self, engine: Any) -> None:
        """Register a trading engine for portfolio risk monitoring.

        The engine must implement the :class:`EngineAdapter` protocol:
        - ``engine_name`` property
        - ``get_open_notional()`` method
        - ``get_open_symbols()`` method
        - ``trigger_kill_switch()`` method
        """
        with self._lock:
            self._engines.append(engine)
        logger.info("Registered engine: %s", getattr(engine, "engine_name", "?"))

    def unregister_engine(self, engine: Any) -> None:
        with self._lock:
            if engine in self._engines:
                self._engines.remove(engine)

    # ─── Status ──────────────────────────────────────────────────────────────

    def get_status(self) -> PortfolioRiskStatus:
        """Compute current portfolio risk status."""
        with self._lock:
            engines = list(self._engines)

        total_exposure = 0.0
        symbol_counts: dict[str, int] = {}

        for eng in engines:
            try:
                notional = eng.get_open_notional()
                total_exposure += notional
                symbols = eng.get_open_symbols()
                for s in symbols:
                    symbol_counts[s] = symbol_counts.get(s, 0) + 1
            except Exception as e:
                logger.warning("Error querying engine %s: %s", getattr(eng, "engine_name", "?"), e)

        alerts: list[dict[str, Any]] = []

        if total_exposure > self._settings.max_total_exposure_usdt:
            alerts.append({
                "level": "critical",
                "type": "total_exposure_exceeded",
                "value": total_exposure,
                "limit": self._settings.max_total_exposure_usdt,
            })

        for sym, count in symbol_counts.items():
            if count > self._settings.max_positions_per_symbol:
                alerts.append({
                    "level": "warning",
                    "type": "symbol_concentration",
                    "symbol": sym,
                    "count": count,
                    "limit": self._settings.max_positions_per_symbol,
                })

        # Drawdown check
        current_pnl = self._aggregate_pnl(engines)
        drawdown = self._day_start_pnl - current_pnl
        if drawdown > self._settings.max_daily_drawdown_usdt:
            alerts.append({
                "level": "critical",
                "type": "daily_drawdown_exceeded",
                "value": drawdown,
                "limit": self._settings.max_daily_drawdown_usdt,
            })

        return PortfolioRiskStatus(
            total_exposure_usdt=total_exposure,
            engine_count=len(engines),
            positions_by_symbol=symbol_counts,
            daily_drawdown_usdt=drawdown,
            kill_switch_active=self._kill_active,
            alerts=alerts,
        )

    def _aggregate_pnl(self, engines: list[Any]) -> float:
        """Sum net PNL across all engines."""
        total = 0.0
        for eng in engines:
            try:
                status = eng.get_status()
                if isinstance(status, dict):
                    stats = status.get("stats", {})
                    total += float(stats.get("net_pnl_usdt", 0))
            except Exception:
                pass
        return total

    # ─── Kill switch ─────────────────────────────────────────────────────────

    def activate_kill_switch(self, reason: str = "manual") -> None:
        """Activate global kill switch — triggers all engines to stop."""
        with self._lock:
            if self._kill_active:
                return
            self._kill_active = True
            engines = list(self._engines)

        logger.warning("Portfolio kill switch ACTIVATED: %s", reason)
        for eng in engines:
            try:
                eng.trigger_kill_switch()
            except Exception as e:
                logger.error("Failed to kill engine %s: %s", getattr(eng, "engine_name", "?"), e)

    def deactivate_kill_switch(self) -> None:
        with self._lock:
            self._kill_active = False
        logger.info("Portfolio kill switch deactivated")

    # ─── Background monitor ──────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background risk monitoring thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="portfolio-risk"
        )
        self._thread.start()
        logger.info("PortfolioRiskManager started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        self._thread = None

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                status = self.get_status()
                for alert in status.alerts:
                    if alert.get("level") == "critical":
                        self.activate_kill_switch(reason=alert.get("type", "unknown"))
                        break
            except Exception:
                logger.exception("Portfolio risk monitor error")
            self._stop_event.wait(timeout=self._settings.check_interval_sec)
