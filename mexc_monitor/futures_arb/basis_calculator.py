"""
Basis Calculator — вычисление базиса между спотовым и фьючерсным инструментами в реальном времени.

Подписывается на Spread Buffer для получения bid/ask обоих ног,
пересчитывает базис при каждом обновлении любой ноги.
Помечает пару как "stale" если данные одной ноги старше stale_after_sec.

Поддерживаемые exchange combos:
  - mexc_spot+mexc_futures
  - mexc_spot+asterdex_perp
  - asterdex_perp+mexc_futures
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from mexc_monitor.futures_arb.models import BasisSnapshot, FuturesArbSettings

logger = logging.getLogger(__name__)


@dataclass
class _LegData:
    """Internal state for one leg (spot or futures) of a pair."""

    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    last_update_ms: int = 0


# --- Exchange combo leg resolution ---

# Maps exchange_combo to (first_leg_exchange, second_leg_exchange)
# Convention: first leg is "spot-like" (denominator for basis), second is "futures-like" (numerator)
_COMBO_LEGS: dict[str, tuple[str, str]] = {
    "mexc_spot+mexc_futures": ("mexc_spot", "mexc_futures"),
    "mexc_spot+asterdex_perp": ("mexc_spot", "asterdex_perp"),
    "asterdex_perp+mexc_futures": ("asterdex_perp", "mexc_futures"),
}


def _spread_buffer_key(exchange: str, symbol: str) -> str:
    """
    Convert (exchange, symbol) to the key used in Spread Buffer.

    Naming conventions:
      - mexc_spot: "BTCUSDT"
      - mexc_futures: "BTC_USDT"
      - asterdex_perp: "ASTER:BTCUSDT"
    """
    sym = symbol.upper()
    if exchange == "mexc_spot":
        return sym
    elif exchange == "mexc_futures":
        # Convert BTCUSDT -> BTC_USDT
        # Find the quote currency (USDT, USDC, etc.) and insert underscore
        for quote in ("USDT", "USDC", "BUSD", "BTC", "ETH"):
            if sym.endswith(quote):
                base = sym[: -len(quote)]
                return f"{base}_{quote}"
        # Fallback: just return as-is with underscore before last 4 chars
        return f"{sym[:-4]}_{sym[-4:]}"
    elif exchange == "asterdex_perp":
        return f"ASTER:{sym}"
    else:
        raise ValueError(f"Unknown exchange: {exchange}")


class BasisCalculator:
    """
    Вычисляет базис между спотовым и фьючерсным инструментами в реальном времени.

    Подписывается на Spread Buffer для получения bid/ask обоих ног.
    Пересчитывает базис при каждом обновлении любой ноги.
    """

    def __init__(
        self,
        settings: FuturesArbSettings,
        *,
        stale_after_sec: float = 30.0,
    ) -> None:
        self._settings = settings
        self._stale_after_sec = stale_after_sec
        self._running = False
        self._lock = threading.Lock()

        # State: (symbol, exchange_combo) -> {"spot": _LegData, "futures": _LegData}
        self._legs: dict[tuple[str, str], dict[str, _LegData]] = {}

        # Cached basis snapshots: (symbol, exchange_combo) -> BasisSnapshot
        self._snapshots: dict[tuple[str, str], BasisSnapshot] = {}

        # Track subscriptions for cleanup
        self._subscriptions: list[tuple[str, Any]] = []

    @property
    def stale_after_sec(self) -> float:
        return self._stale_after_sec

    @stale_after_sec.setter
    def stale_after_sec(self, value: float) -> None:
        self._stale_after_sec = max(1.0, value)

    def start(self) -> None:
        """Start the basis calculator: subscribe to Spread Buffer for all configured pairs."""
        if self._running:
            return

        self._running = True
        self._subscribe_all()
        logger.info(
            "BasisCalculator started: symbols=%s, combos=%s, stale_after_sec=%.1f",
            self._settings.symbols,
            self._settings.exchange_combos,
            self._stale_after_sec,
        )

    def stop(self) -> None:
        """Stop the basis calculator: unsubscribe from all Spread Buffer updates."""
        if not self._running:
            return

        self._running = False
        self._unsubscribe_all()
        logger.info("BasisCalculator stopped")

    def get_current_basis(self, symbol: str, exchange_combo: str) -> BasisSnapshot | None:
        """Get the current basis snapshot for a specific symbol and exchange combo."""
        with self._lock:
            snapshot = self._snapshots.get((symbol, exchange_combo))
            if snapshot is None:
                return None
            # Re-check staleness at query time
            return self._with_updated_status(snapshot)

    def get_all_basis(self) -> list[BasisSnapshot]:
        """Get all current basis snapshots (with updated stale status)."""
        with self._lock:
            return [
                self._with_updated_status(snap)
                for snap in self._snapshots.values()
            ]

    def _with_updated_status(self, snapshot: BasisSnapshot) -> BasisSnapshot:
        """Return snapshot with status updated based on current staleness check."""
        key = (snapshot.symbol, snapshot.exchange_combo)
        legs = self._legs.get(key)
        if legs is None:
            return snapshot

        now_ms = int(time.time() * 1000)
        status = self._compute_status(legs, now_ms)

        if status != snapshot.status:
            # Return a new snapshot with updated status
            return BasisSnapshot(
                symbol=snapshot.symbol,
                exchange_combo=snapshot.exchange_combo,
                spot_mid=snapshot.spot_mid,
                futures_mid=snapshot.futures_mid,
                basis_abs=snapshot.basis_abs,
                basis_bps=snapshot.basis_bps,
                executable_basis_cc_bps=snapshot.executable_basis_cc_bps,
                executable_basis_rcc_bps=snapshot.executable_basis_rcc_bps,
                estimated_apy=snapshot.estimated_apy,
                funding_rate=snapshot.funding_rate,
                status=status,
                timestamp_ms=snapshot.timestamp_ms,
            )
        return snapshot

    def _subscribe_all(self) -> None:
        """Subscribe to Spread Buffer for all configured symbol × exchange_combo pairs."""
        from mexc_monitor.spread_buffer import subscribe

        for symbol in self._settings.symbols:
            for combo in self._settings.exchange_combos:
                if combo not in _COMBO_LEGS:
                    logger.warning("Unknown exchange_combo: %s, skipping", combo)
                    continue

                spot_exchange, futures_exchange = _COMBO_LEGS[combo]
                spot_key = _spread_buffer_key(spot_exchange, symbol)
                futures_key = _spread_buffer_key(futures_exchange, symbol)

                # Initialize leg data
                key = (symbol, combo)
                with self._lock:
                    if key not in self._legs:
                        self._legs[key] = {
                            "spot": _LegData(),
                            "futures": _LegData(),
                        }

                # Create callbacks that capture the pair context
                spot_cb = self._make_callback(symbol, combo, "spot")
                futures_cb = self._make_callback(symbol, combo, "futures")

                subscribe(spot_key, spot_cb)
                subscribe(futures_key, futures_cb)

                self._subscriptions.append((spot_key, spot_cb))
                self._subscriptions.append((futures_key, futures_cb))

                logger.debug(
                    "Subscribed: %s [%s] spot_key=%s, futures_key=%s",
                    symbol, combo, spot_key, futures_key,
                )

    def _unsubscribe_all(self) -> None:
        """Unsubscribe from all Spread Buffer updates."""
        from mexc_monitor.spread_buffer import unsubscribe

        for buffer_key, callback in self._subscriptions:
            unsubscribe(buffer_key, callback)
        self._subscriptions.clear()

    def _make_callback(self, symbol: str, combo: str, leg: str) -> Any:
        """Create a callback for Spread Buffer subscription."""

        def _on_tick(_sym: str, tick: Any) -> None:
            if not self._running:
                return
            self._on_leg_update(symbol, combo, leg, tick)

        return _on_tick

    def _on_leg_update(self, symbol: str, combo: str, leg: str, tick: Any) -> None:
        """Handle a new tick from Spread Buffer for one leg of a pair."""
        if not self._running:
            return

        key = (symbol, combo)

        with self._lock:
            legs = self._legs.get(key)
            if legs is None:
                return

            leg_data = legs[leg]
            leg_data.bid = tick.bid
            leg_data.ask = tick.ask
            leg_data.mid = tick.mid
            leg_data.last_update_ms = tick.timestamp_ms

            # Recompute basis
            self._recompute_basis(symbol, combo, legs)

    def _recompute_basis(
        self,
        symbol: str,
        combo: str,
        legs: dict[str, _LegData],
    ) -> None:
        """Recompute basis snapshot for a pair. Must be called under self._lock."""
        spot = legs["spot"]
        futures = legs["futures"]

        now_ms = int(time.time() * 1000)
        status = self._compute_status(legs, now_ms)

        # If either leg has no data yet, we can't compute basis
        if spot.mid <= 0 or futures.mid <= 0:
            return

        snapshot = compute_basis_snapshot(
            symbol=symbol,
            exchange_combo=combo,
            spot_bid=spot.bid,
            spot_ask=spot.ask,
            futures_bid=futures.bid,
            futures_ask=futures.ask,
            spot_fee_bps=self._settings.spot_taker_fee_bps,
            futures_fee_bps=self._settings.futures_taker_fee_bps,
            expected_hold_hours=self._settings.expected_hold_hours,
            status=status,
            timestamp_ms=now_ms,
        )

        key = (symbol, combo)
        self._snapshots[key] = snapshot

    def _compute_status(self, legs: dict[str, _LegData], now_ms: int) -> str:
        """Determine if the pair is active or stale based on leg data freshness."""
        spot = legs["spot"]
        futures = legs["futures"]

        stale_threshold_ms = int(self._stale_after_sec * 1000)

        # No data at all → stale
        if spot.last_update_ms == 0 or futures.last_update_ms == 0:
            return "stale"

        # Check if either leg is too old
        spot_age_ms = now_ms - spot.last_update_ms
        futures_age_ms = now_ms - futures.last_update_ms

        if spot_age_ms > stale_threshold_ms or futures_age_ms > stale_threshold_ms:
            return "stale"

        return "active"


def compute_basis_snapshot(
    *,
    symbol: str,
    exchange_combo: str,
    spot_bid: float,
    spot_ask: float,
    futures_bid: float,
    futures_ask: float,
    spot_fee_bps: float,
    futures_fee_bps: float,
    expected_hold_hours: float,
    status: str = "active",
    timestamp_ms: int | None = None,
    funding_rate: float | None = None,
) -> BasisSnapshot:
    """
    Pure function to compute a BasisSnapshot from raw market data.

    Formulas:
      - spot_mid = (spot_bid + spot_ask) / 2
      - futures_mid = (futures_bid + futures_ask) / 2
      - basis_abs = futures_mid - spot_mid
      - basis_bps = 10000 * basis_abs / spot_mid
      - executable_cc_bps = (futures_bid - spot_ask) / spot_mid * 10000 - (spot_fee_bps + futures_fee_bps)
      - executable_rcc_bps = (spot_bid - futures_ask) / spot_mid * 10000 - (spot_fee_bps + futures_fee_bps)
      - realistic_pnl_bps = max(executable_cc, executable_rcc) - exit_fees
      - estimated_apy = (realistic_pnl_bps / 10000) * (365 * 24 / expected_hold_hours) * 100
    """
    spot_mid = (spot_bid + spot_ask) / 2.0
    futures_mid = (futures_bid + futures_ask) / 2.0

    basis_abs = futures_mid - spot_mid
    basis_bps = 10000.0 * basis_abs / spot_mid if spot_mid > 0 else 0.0

    # Executable basis for cash-and-carry: sell futures (at bid) + buy spot (at ask)
    executable_cc_bps = (
        (futures_bid - spot_ask) / spot_mid * 10000.0 - (spot_fee_bps + futures_fee_bps)
        if spot_mid > 0
        else 0.0
    )

    # Executable basis for reverse cash-and-carry: sell spot (at bid) + buy futures (at ask)
    executable_rcc_bps = (
        (spot_bid - futures_ask) / spot_mid * 10000.0 - (spot_fee_bps + futures_fee_bps)
        if spot_mid > 0
        else 0.0
    )

    # Estimated APY from basis — uses executable basis (after fees) minus
    # exit fees, not raw mid-mid basis, to avoid overstating returns.
    # Full round-trip: entry fees (in executable) + exit fees = 2 × (spot+fut).
    best_exec_bps = max(executable_cc_bps, executable_rcc_bps)
    exit_fees_bps = spot_fee_bps + futures_fee_bps
    realistic_pnl_bps = best_exec_bps - exit_fees_bps
    estimated_apy = (
        (realistic_pnl_bps / 10000.0) * (365.0 * 24.0 / expected_hold_hours) * 100.0
        if expected_hold_hours > 0
        else 0.0
    )

    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)

    return BasisSnapshot(
        symbol=symbol,
        exchange_combo=exchange_combo,
        spot_mid=spot_mid,
        futures_mid=futures_mid,
        basis_abs=basis_abs,
        basis_bps=basis_bps,
        executable_basis_cc_bps=executable_cc_bps,
        executable_basis_rcc_bps=executable_rcc_bps,
        estimated_apy=estimated_apy,
        funding_rate=funding_rate,
        status=status,
        timestamp_ms=timestamp_ms,
    )
