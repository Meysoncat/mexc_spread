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
        for quote in ("USDT", "USDC", "BUSD", "BTC", "ETH"):
            if sym.endswith(quote):
                base = sym[: -len(quote)]
                return f"{base}_{quote}"
        return f"{sym[:-4]}_{sym[-4:]}"
    elif exchange == "asterdex_perp":
        return f"ASTER:{sym}"
    else:
        raise ValueError(f"Unknown exchange: {exchange}")


def _norm_futures_symbol(s: str) -> str:
    x = s.strip().upper()
    if "_" not in x:
        return x
    return "_".join(p for p in x.split("_") if p)


def spot_to_futures_symbol(spot_symbol: str) -> str | None:
    """BTCUSDT → BTC_USDT. Только *USDT спот-пары (как на MEXC)."""
    s = spot_symbol.strip().upper()
    if not s.endswith("USDT"):
        return None
    base = s[:-4]
    if not base:
        return None
    return f"{base}_USDT"


class BasisCalculator:
    """
    Вычисляет базис между спотовым и фьючерсным инструментами в реальном времени.

    Подписывается на Spread Buffer для получения bid/ask обоих ног.
    Пересчитывает базис при каждом обновлении любой ноги.

    REST fallback: если WS-данных нет rest_stale_threshold_sec секунд,
    автоматически опрашивает REST API MEXC и обновляет ноги.
    """

    def __init__(
        self,
        settings: FuturesArbSettings,
        *,
        stale_after_sec: float = 30.0,
        rest_fallback_enabled: bool = True,
        rest_poll_interval_sec: float = 5.0,
        rest_stale_threshold_sec: float = 5.0,
    ) -> None:
        self._settings = settings
        self._stale_after_sec = stale_after_sec
        self._rest_fallback_enabled = rest_fallback_enabled
        self._rest_poll_interval_sec = max(1.0, rest_poll_interval_sec)
        self._rest_stale_threshold_sec = max(1.0, rest_stale_threshold_sec)
        self._running = False
        self._lock = threading.Lock()

        # State: (symbol, exchange_combo) -> {"spot": _LegData, "futures": _LegData}
        self._legs: dict[tuple[str, str], dict[str, _LegData]] = {}

        # Cached basis snapshots: (symbol, exchange_combo) -> BasisSnapshot
        self._snapshots: dict[tuple[str, str], BasisSnapshot] = {}

        # Track subscriptions for cleanup
        self._subscriptions: list[tuple[str, Any]] = []

        # REST fallback thread
        self._rest_thread: threading.Thread | None = None
        self._rest_stop = threading.Event()

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
        if self._rest_fallback_enabled:
            self._start_rest_fallback()
        logger.info(
            "BasisCalculator started: symbols=%s, combos=%s, stale_after_sec=%.1f, rest_fallback=%s",
            self._settings.symbols,
            self._settings.exchange_combos,
            self._stale_after_sec,
            self._rest_fallback_enabled,
        )

    def stop(self) -> None:
        """Stop the basis calculator: unsubscribe from all Spread Buffer updates."""
        if not self._running:
            return

        self._running = False
        self._unsubscribe_all()
        self._stop_rest_fallback()
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

                key = (symbol, combo)
                with self._lock:
                    if key not in self._legs:
                        self._legs[key] = {
                            "spot": _LegData(),
                            "futures": _LegData(),
                        }

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
        def _on_tick(_sym: str, tick: Any) -> None:
            if not self._running:
                return
            self._on_leg_update(symbol, combo, leg, tick)
        return _on_tick

    def _on_leg_update(self, symbol: str, combo: str, leg: str, tick: Any) -> None:
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

            self._recompute_basis(symbol, combo, legs)

    def _recompute_basis(
        self,
        symbol: str,
        combo: str,
        legs: dict[str, _LegData],
    ) -> None:
        spot = legs["spot"]
        futures = legs["futures"]

        now_ms = int(time.time() * 1000)
        status = self._compute_status(legs, now_ms)

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
        spot = legs["spot"]
        futures = legs["futures"]

        stale_threshold_ms = int(self._stale_after_sec * 1000)

        if spot.last_update_ms == 0 or futures.last_update_ms == 0:
            return "stale"

        spot_age_ms = now_ms - spot.last_update_ms
        futures_age_ms = now_ms - futures.last_update_ms

        if spot_age_ms > stale_threshold_ms or futures_age_ms > stale_threshold_ms:
            return "stale"

        return "active"

    # --- REST Fallback ---

    def _start_rest_fallback(self) -> None:
        if self._rest_thread is not None and self._rest_thread.is_alive():
            return
        self._rest_stop.clear()
        self._rest_thread = threading.Thread(
            target=self._rest_poll_loop,
            daemon=True,
            name="basis-rest-fallback",
        )
        self._rest_thread.start()
        logger.info(
            "BasisCalculator REST fallback started (interval=%.1fs, stale_threshold=%.1fs)",
            self._rest_poll_interval_sec, self._rest_stale_threshold_sec,
        )

    def _stop_rest_fallback(self) -> None:
        self._rest_stop.set()
        if self._rest_thread is not None:
            self._rest_thread.join(timeout=2.0)
            self._rest_thread = None

    def _rest_poll_loop(self) -> None:
        while not self._rest_stop.wait(self._rest_poll_interval_sec):
            if not self._running:
                continue
            try:
                self._poll_rest_once()
            except Exception:
                logger.exception("REST fallback poll failed")

    def _poll_rest_once(self) -> None:
        now_ms = int(time.time() * 1000)
        stale_threshold_ms = int(self._rest_stale_threshold_sec * 1000)

        stale_pairs: list[tuple[str, str]] = []
        with self._lock:
            for (symbol, combo), legs in self._legs.items():
                spot_age = now_ms - legs["spot"].last_update_ms
                fut_age = now_ms - legs["futures"].last_update_ms
                if spot_age > stale_threshold_ms or fut_age > stale_threshold_ms:
                    stale_pairs.append((symbol, combo))

        if not stale_pairs:
            return

        for symbol, combo in stale_pairs:
            if combo != "mexc_spot+mexc_futures":
                continue
            try:
                self._fetch_rest_mexc_pair(symbol, combo)
            except Exception as e:
                logger.debug("REST fallback for %s/%s failed: %s", symbol, combo, e)

    def _fetch_rest_mexc_pair(self, symbol: str, combo: str) -> None:
        from mexc_monitor.client import fetch_futures_snapshot_rows, fetch_merged_snapshot_rows
        from mexc_monitor.config import DEFAULT_SETTINGS

        cfg = DEFAULT_SETTINGS
        spot_sym = symbol.upper()
        fut_sym = _norm_futures_symbol(spot_to_futures_symbol(spot_sym) or "")

        if not fut_sym:
            return

        spot_rows = fetch_merged_snapshot_rows(cfg)
        spot_row = next((r for r in spot_rows if r.symbol == spot_sym), None)

        fut_rows = fetch_futures_snapshot_rows(cfg)
        fut_row = next((r for r in fut_rows if r.symbol == fut_sym), None)

        if spot_row is None or fut_row is None:
            return

        now_ms = int(time.time() * 1000)
        key = (symbol, combo)

        with self._lock:
            legs = self._legs.get(key)
            if legs is None:
                return

            legs["spot"].bid = spot_row.bid
            legs["spot"].ask = spot_row.ask
            legs["spot"].mid = spot_row.mid
            legs["spot"].last_update_ms = now_ms

            legs["futures"].bid = fut_row.bid
            legs["futures"].ask = fut_row.ask
            legs["futures"].mid = fut_row.mid
            legs["futures"].last_update_ms = now_ms

            self._recompute_basis(symbol, combo, legs)

        logger.debug(
            "REST fallback updated %s/%s: spot_mid=%.4f fut_mid=%.4f",
            symbol, combo, spot_row.mid, fut_row.mid,
        )


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

    executable_cc_bps = (
        (futures_bid - spot_ask) / spot_mid * 10000.0 - (spot_fee_bps + futures_fee_bps)
        if spot_mid > 0
        else 0.0
    )

    executable_rcc_bps = (
        (spot_bid - futures_ask) / spot_mid * 10000.0 - (spot_fee_bps + futures_fee_bps)
        if spot_mid > 0
        else 0.0
    )

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
