"""
Funding Tracker — отслеживание funding rates перпетуальных контрактов.

Опрашивает funding rate каждые 60 секунд через REST API:
- MEXC Futures: GET /api/v1/contract/funding_rate/{symbol}
- AsterDEX: GET /fapi/v1/premiumIndex?symbol={symbol}

Хранит историю за 30 дней в памяти (deque).
Вычисляет avg_7d, avg_30d, annualized_yield.
Генерирует событие funding_direction_changed при смене знака.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from mexc_monitor.futures_arb.models import FundingInfo, FuturesArbSettings

logger = logging.getLogger(__name__)

# Default funding intervals (hours between funding payments)
MEXC_FUNDING_INTERVAL_HOURS = 8.0
ASTERDEX_FUNDING_INTERVAL_HOURS = 8.0

# 30 days of history: at 60s polling, that's 30*24*60 = 43200 entries max
_MAX_HISTORY_ENTRIES = 43_200

# Default poll interval
_DEFAULT_POLL_INTERVAL_SEC = 60.0

# HTTP timeout for funding rate requests
_HTTP_TIMEOUT_SEC = 15.0


@dataclass(frozen=True)
class FundingRateEntry:
    """Single funding rate observation."""
    symbol: str
    exchange: str
    rate: float
    timestamp_ms: int
    next_funding_time_ms: int


class FundingTracker:
    """
    Отслеживание funding rates перпетуальных контрактов.

    Опрашивает funding rate каждые 60 секунд через REST API для MEXC Futures
    и AsterDEX. Хранит историю за 30 дней, вычисляет средние и annualized yield.
    """

    def __init__(
        self,
        settings: FuturesArbSettings,
        *,
        poll_interval_sec: float = _DEFAULT_POLL_INTERVAL_SEC,
        mexc_base_url: str = "https://api.mexc.com",
        asterdex_base_url: str = "https://fapi.asterdex.com",
        on_direction_changed: Callable[[FundingInfo], None] | None = None,
    ):
        self._settings = settings
        self._poll_interval_sec = poll_interval_sec
        self._mexc_base_url = mexc_base_url.rstrip("/")
        self._asterdex_base_url = asterdex_base_url.rstrip("/")
        self._on_direction_changed = on_direction_changed

        # History: key = (symbol, exchange), value = deque of FundingRateEntry
        self._history: dict[tuple[str, str], deque[FundingRateEntry]] = {}
        # Last known rate for direction change detection: key = (symbol, exchange)
        self._last_rate: dict[tuple[str, str], float] = {}
        # Current FundingInfo cache
        self._current: dict[tuple[str, str], FundingInfo] = {}
        # Direction change confirmation: key → count of consecutive same-sign readings
        self._dir_confirm_count: dict[tuple[str, str], int] = {}
        self._dir_confirm_threshold = max(1, int(settings.funding_consecutive_periods_exit))

        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the funding rate polling loop in a background thread."""
        if self._running:
            logger.warning("FundingTracker already running")
            return

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="FundingTracker-poll",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "FundingTracker started: symbols=%s, poll_interval=%.0fs",
            self._settings.symbols,
            self._poll_interval_sec,
        )

    def stop(self) -> None:
        """Stop the funding rate polling loop."""
        if not self._running:
            return

        self._stop_event.set()
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval_sec + 5)
            self._thread = None
        logger.info("FundingTracker stopped")

    def get_funding(self, symbol: str, exchange: str) -> FundingInfo | None:
        """Get current funding info for a specific symbol and exchange."""
        with self._lock:
            return self._current.get((symbol, exchange))

    def get_all_funding(self) -> list[FundingInfo]:
        """Get current funding info for all tracked symbols."""
        with self._lock:
            return list(self._current.values())

    def _poll_loop(self) -> None:
        """Background polling loop."""
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                logger.exception("FundingTracker poll error")

            # Wait for next poll interval or stop signal
            self._stop_event.wait(timeout=self._poll_interval_sec)

    def _poll_once(self) -> None:
        """Execute one polling cycle for all symbols and exchanges."""
        symbols = self._settings.symbols
        exchange_combos = self._settings.exchange_combos

        # Determine which exchanges to poll based on configured combos
        exchanges_to_poll: set[str] = set()
        for combo in exchange_combos:
            parts = combo.split("+")
            for part in parts:
                if part in ("mexc_futures", "asterdex_perp"):
                    exchanges_to_poll.add(part)

        now_ms = int(time.time() * 1000)

        for symbol in symbols:
            for exchange in exchanges_to_poll:
                try:
                    entry = self._fetch_funding_rate(symbol, exchange, now_ms)
                    if entry is not None:
                        self._process_entry(entry)
                except Exception:
                    logger.warning(
                        "Failed to fetch funding rate: symbol=%s, exchange=%s",
                        symbol,
                        exchange,
                        exc_info=True,
                    )

    def _fetch_funding_rate(
        self, symbol: str, exchange: str, now_ms: int
    ) -> FundingRateEntry | None:
        """Fetch funding rate from the appropriate exchange API."""
        if exchange == "mexc_futures":
            return self._fetch_mexc_funding(symbol, now_ms)
        elif exchange == "asterdex_perp":
            return self._fetch_asterdex_funding(symbol, now_ms)
        else:
            logger.debug("Unknown exchange for funding: %s", exchange)
            return None

    def _fetch_mexc_funding(self, symbol: str, now_ms: int) -> FundingRateEntry | None:
        """
        Fetch funding rate from MEXC Futures API.

        MEXC Futures contract/ticker already includes fundingRate in its response.
        We use the dedicated funding rate endpoint: GET /api/v1/contract/funding_rate/{symbol}
        """
        # MEXC futures uses underscore-separated symbols (e.g., BTC_USDT)
        mexc_symbol = symbol.replace("USDT", "_USDT")
        url = f"{self._mexc_base_url}/api/v1/contract/funding_rate/{mexc_symbol}"

        try:
            r = httpx.get(url, timeout=_HTTP_TIMEOUT_SEC)
        except httpx.HTTPError as e:
            logger.warning("MEXC funding rate HTTP error: %s", e)
            return None

        if r.status_code >= 400:
            logger.warning("MEXC funding rate HTTP %d: %s", r.status_code, r.text[:200])
            return None

        try:
            payload = r.json()
        except Exception:
            logger.warning("MEXC funding rate invalid JSON")
            return None

        # MEXC response format: {"success": true, "code": 0, "data": {"symbol": ..., "fundingRate": ..., "nextSettleTime": ...}}
        if not isinstance(payload, dict):
            return None

        if not payload.get("success"):
            return None

        data = payload.get("data")
        if not isinstance(data, dict):
            return None

        rate = self._parse_float(data.get("fundingRate"))
        if rate is None:
            return None

        next_time = int(data.get("nextSettleTime", 0))

        return FundingRateEntry(
            symbol=symbol,
            exchange="mexc_futures",
            rate=rate,
            timestamp_ms=now_ms,
            next_funding_time_ms=next_time,
        )

    def _fetch_asterdex_funding(self, symbol: str, now_ms: int) -> FundingRateEntry | None:
        """
        Fetch funding rate from AsterDEX API.

        Uses GET /fapi/v1/premiumIndex?symbol={symbol}
        """
        url = f"{self._asterdex_base_url}/fapi/v1/premiumIndex"
        params = {"symbol": symbol.upper()}

        try:
            r = httpx.get(url, params=params, timeout=_HTTP_TIMEOUT_SEC)
        except httpx.HTTPError as e:
            logger.warning("AsterDEX funding rate HTTP error: %s", e)
            return None

        if r.status_code >= 400:
            logger.warning("AsterDEX funding rate HTTP %d: %s", r.status_code, r.text[:200])
            return None

        try:
            data = r.json()
        except Exception:
            logger.warning("AsterDEX funding rate invalid JSON")
            return None

        # Response can be a single dict or a list
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None

        rate = self._parse_float(data.get("lastFundingRate"))
        if rate is None:
            return None

        next_time = int(data.get("nextFundingTime", 0))

        return FundingRateEntry(
            symbol=symbol,
            exchange="asterdex_perp",
            rate=rate,
            timestamp_ms=now_ms,
            next_funding_time_ms=next_time,
        )

    def _process_entry(self, entry: FundingRateEntry) -> None:
        """Process a new funding rate entry: store, compute stats, detect direction change."""
        key = (entry.symbol, entry.exchange)

        with self._lock:
            # Initialize history deque if needed
            if key not in self._history:
                self._history[key] = deque(maxlen=_MAX_HISTORY_ENTRIES)

            # Add to history
            self._history[key].append(entry)

            # Prune entries older than 30 days
            cutoff_ms = entry.timestamp_ms - (30 * 24 * 3600 * 1000)
            history = self._history[key]
            while history and history[0].timestamp_ms < cutoff_ms:
                history.popleft()

            # Detect direction change with confirmation (debounce).
            # A sign flip is only reported after N consecutive readings with
            # the new sign, preventing noise from triggering false signals.
            direction_changed = False
            prev_rate = self._last_rate.get(key)
            if prev_rate is not None and entry.rate != 0.0 and prev_rate != 0.0:
                # Check for sign flip: positive→negative or negative→positive
                if (prev_rate > 0 and entry.rate < 0) or (prev_rate < 0 and entry.rate > 0):
                    # Reset confirmation counter on new sign flip
                    self._dir_confirm_count[key] = 1
                elif prev_rate * entry.rate > 0:
                    # Same sign as previous — increment confirmation if we're
                    # in a pending direction change
                    cnt = self._dir_confirm_count.get(key, 0)
                    if cnt > 0:
                        self._dir_confirm_count[key] = cnt + 1

                # Check if confirmation threshold is met
                if self._dir_confirm_count.get(key, 0) >= self._dir_confirm_threshold:
                    direction_changed = True
                    self._dir_confirm_count[key] = 0  # Reset after reporting

            self._last_rate[key] = entry.rate

            # Compute averages
            avg_7d = self._compute_avg(history, entry.timestamp_ms, days=7)
            avg_30d = self._compute_avg(history, entry.timestamp_ms, days=30)

            # Compute std_30d and z-score for significance testing
            std_30d = self._compute_std(history, entry.timestamp_ms, days=30)
            z_score = (
                (entry.rate - avg_30d) / std_30d
                if std_30d > 1e-12
                else 0.0
            )

            # Compute annualized yield
            funding_interval_hours = self._get_funding_interval_hours(entry.exchange)
            annualized_yield = entry.rate * (365 * 24 / funding_interval_hours) * 100

            # Build FundingInfo
            info = FundingInfo(
                symbol=entry.symbol,
                exchange=entry.exchange,
                current_rate=entry.rate,
                next_funding_time_ms=entry.next_funding_time_ms,
                avg_7d=avg_7d,
                avg_30d=avg_30d,
                annualized_yield=annualized_yield,
                direction_changed=direction_changed,
                std_30d=std_30d,
                z_score=z_score,
            )

            self._current[key] = info

        # Fire direction changed callback outside the lock
        if direction_changed and self._on_direction_changed is not None:
            try:
                self._on_direction_changed(info)
            except Exception:
                logger.exception("Error in funding direction changed callback")

    def _compute_avg(
        self, history: deque[FundingRateEntry], now_ms: int, days: int
    ) -> float:
        """Compute average funding rate over the last N days."""
        cutoff_ms = now_ms - (days * 24 * 3600 * 1000)
        rates = [e.rate for e in history if e.timestamp_ms >= cutoff_ms]
        if not rates:
            return 0.0
        return sum(rates) / len(rates)

    def _compute_std(
        self, history: deque[FundingRateEntry], now_ms: int, days: int
    ) -> float:
        """Compute standard deviation of funding rate over the last N days."""
        cutoff_ms = now_ms - (days * 24 * 3600 * 1000)
        rates = [e.rate for e in history if e.timestamp_ms >= cutoff_ms]
        if len(rates) < 2:
            return 0.0
        avg = sum(rates) / len(rates)
        variance = sum((r - avg) ** 2 for r in rates) / len(rates)
        return variance ** 0.5

    def _get_funding_interval_hours(self, exchange: str) -> float:
        """Get the funding interval in hours for the given exchange."""
        if exchange == "mexc_futures":
            return MEXC_FUNDING_INTERVAL_HOURS
        elif exchange == "asterdex_perp":
            return ASTERDEX_FUNDING_INTERVAL_HOURS
        return 8.0  # Default

    @staticmethod
    def _parse_float(v: Any) -> float | None:
        """Parse a value to float, returning None on failure."""
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
