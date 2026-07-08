"""
Position Manager — управление арбитражными позициями.

Отвечает за:
- Хранение открытых и закрытых позиций
- Вычисление PNL (basis_pnl + cumulative_funding - fees)
- Сериализация/десериализация состояния в JSON
- Статистика по закрытым сделкам
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Callable

from mexc_monitor.futures_arb.models import (
    FuturesArbPosition,
    FuturesArbStats,
    FuturesArbTradeRecord,
)

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Управление арбитражными позициями (спот + фьючерс).

    Хранит открытые позиции в памяти, закрытые — в списке.
    Поддерживает сериализацию/десериализацию для persistence.
    """

    def __init__(
        self,
        state_file: str | Path = "data/futures_arb_state.json",
        *,
        alert_service: Any | None = None,
        on_alert: Callable[[str, str], None] | None = None,
    ):
        self._state_file = Path(state_file)
        self._alert_service = alert_service
        self._on_alert = on_alert

        self._lock = threading.Lock()
        # Open positions: id -> FuturesArbPosition
        self._positions: dict[str, FuturesArbPosition] = {}
        # Closed positions history
        self._closed: list[FuturesArbPosition] = []

    # --- Position lifecycle ---

    def open_position(self, pos: FuturesArbPosition) -> None:
        """Register a new open position."""
        with self._lock:
            self._positions[pos.id] = pos
        logger.info(
            "Position opened: id=%s symbol=%s strategy=%s notional=%.2f",
            pos.id, pos.symbol, pos.strategy, pos.notional_usdt,
        )

    def close_position(
        self,
        position_id: str,
        reason: str,
        *,
        exit_basis_bps: float = 0.0,
        exit_fees: float = 0.0,
        basis_pnl: float | None = None,
        spot_exit_price: float = 0.0,
        futures_exit_price: float = 0.0,
    ) -> FuturesArbPosition | None:
        """
        Close a position and move it to closed history.

        If spot_exit_price and futures_exit_price are provided, computes basis_pnl
        from actual exit prices. Otherwise uses the provided basis_pnl.

        Returns the closed position or None if not found.
        """
        now_ms = int(time.time() * 1000)

        with self._lock:
            pos = self._positions.get(position_id)
            if pos is None:
                return None

            pos.state = "closed"
            pos.close_time_ms = now_ms
            pos.close_reason = reason
            pos.exit_basis_bps = exit_basis_bps
            pos.exit_fees = exit_fees

            # Compute basis_pnl from exit prices if provided
            if spot_exit_price > 0 and futures_exit_price > 0:
                pos.basis_pnl = self._compute_basis_pnl(
                    pos, spot_exit_price, futures_exit_price
                )
            elif basis_pnl is not None:
                pos.basis_pnl = basis_pnl

            # Compute total PNL
            pos.total_pnl = pos.basis_pnl + pos.cumulative_funding - pos.entry_fees - pos.exit_fees

            # Move to closed
            self._closed.append(pos)
            del self._positions[position_id]

        logger.info(
            "Position closed: id=%s reason=%s pnl=%.4f",
            position_id, reason, pos.total_pnl,
        )
        return pos

    @staticmethod
    def _compute_basis_pnl(
        pos: FuturesArbPosition,
        spot_exit_price: float,
        futures_exit_price: float,
    ) -> float:
        """
        Compute basis PNL from entry and exit prices.

        Cash-and-carry (buy spot + short futures):
          spot_pnl = (spot_exit - spot_entry) * spot_qty
          futures_pnl = (futures_entry - futures_exit) * futures_qty
          basis_pnl = spot_pnl + futures_pnl

        Reverse cash-and-carry (sell spot + long futures):
          spot_pnl = (spot_entry - spot_exit) * spot_qty
          futures_pnl = (futures_exit - futures_entry) * futures_qty
          basis_pnl = spot_pnl + futures_pnl
        """
        if pos.spot_side == "buy":
            # Cash-and-carry or funding_arb (long spot)
            spot_pnl = (spot_exit_price - pos.spot_entry_price) * pos.spot_qty
        else:
            # Reverse: short spot
            spot_pnl = (pos.spot_entry_price - spot_exit_price) * pos.spot_qty

        if pos.futures_side == "short":
            futures_pnl = (pos.futures_entry_price - futures_exit_price) * pos.futures_qty
        else:
            # Long futures
            futures_pnl = (futures_exit_price - pos.futures_entry_price) * pos.futures_qty

        return spot_pnl + futures_pnl

    def update_funding(self, position_id: str, amount: float) -> None:
        """Add a funding payment to a position's cumulative_funding."""
        with self._lock:
            pos = self._positions.get(position_id)
            if pos is None:
                return
            pos.cumulative_funding += amount
            pos.total_pnl = pos.basis_pnl + pos.cumulative_funding - pos.entry_fees - pos.exit_fees

    def update_basis_pnl(self, position_id: str, basis_pnl: float) -> None:
        """Update the basis PNL for a position."""
        with self._lock:
            pos = self._positions.get(position_id)
            if pos is None:
                return
            pos.basis_pnl = basis_pnl
            pos.total_pnl = basis_pnl + pos.cumulative_funding - pos.entry_fees - pos.exit_fees

    def update_margin_ratio(self, position_id: str, margin_ratio: float) -> None:
        """Update the margin ratio for a position."""
        with self._lock:
            pos = self._positions.get(position_id)
            if pos is None:
                return
            pos.margin_ratio = margin_ratio

    # --- Queries ---

    def get_position(self, position_id: str) -> FuturesArbPosition | None:
        """Get a specific position by ID."""
        with self._lock:
            return self._positions.get(position_id)

    def get_open_positions(self) -> list[FuturesArbPosition]:
        """Get all open positions."""
        with self._lock:
            return [
                p for p in self._positions.values()
                if p.state in ("pending_open", "open")
            ]

    def get_all_positions(self) -> list[FuturesArbPosition]:
        """Get all positions (open and pending)."""
        with self._lock:
            return list(self._positions.values())

    def get_closed_positions(self, limit: int = 50, offset: int = 0) -> list[FuturesArbPosition]:
        """Get closed positions (most recent first)."""
        with self._lock:
            trades = list(reversed(self._closed))
            return trades[offset:offset + limit]

    def get_open_count(self) -> int:
        """Get count of open positions."""
        with self._lock:
            return sum(
                1 for p in self._positions.values()
                if p.state in ("pending_open", "open")
            )

    def get_total_exposure(self) -> float:
        """Get total notional exposure of open positions."""
        with self._lock:
            return sum(
                p.notional_usdt for p in self._positions.values()
                if p.state in ("pending_open", "open")
            )

    def get_symbol_exposure(self, symbol: str) -> float:
        """Get total notional exposure for a specific symbol."""
        with self._lock:
            return sum(
                p.notional_usdt for p in self._positions.values()
                if p.state in ("pending_open", "open") and p.symbol == symbol
            )

    # --- PNL computation (static methods) ---

    @staticmethod
    def compute_total_pnl(pos: FuturesArbPosition) -> float:
        """Compute total PNL: basis_pnl + cumulative_funding - entry_fees - exit_fees."""
        return pos.basis_pnl + pos.cumulative_funding - pos.entry_fees - pos.exit_fees

    @staticmethod
    def compute_annualized_return(pos: FuturesArbPosition) -> float:
        """Compute annualized return for a position."""
        if pos.notional_usdt <= 0:
            return 0.0
        if pos.close_time_ms <= pos.open_time_ms:
            return 0.0
        hold_seconds = (pos.close_time_ms - pos.open_time_ms) / 1000.0
        if hold_seconds <= 0:
            return 0.0
        total_pnl = pos.basis_pnl + pos.cumulative_funding - pos.entry_fees - pos.exit_fees
        return (total_pnl / pos.notional_usdt) * (365 * 24 * 3600 / hold_seconds) * 100

    @staticmethod
    def compute_net_pnl_bps(pos: FuturesArbPosition) -> float:
        """Compute net PNL in basis points."""
        if pos.notional_usdt <= 0:
            return 0.0
        total_pnl = pos.basis_pnl + pos.cumulative_funding - pos.entry_fees - pos.exit_fees
        return total_pnl / pos.notional_usdt * 10000

    # --- Statistics ---

    def get_stats(self) -> FuturesArbStats:
        """Compute aggregate statistics from closed trades."""
        with self._lock:
            trades = self._closed[:]

        if not trades:
            return FuturesArbStats()

        winning = [t for t in trades if t.total_pnl > 0]
        losing = [t for t in trades if t.total_pnl <= 0]
        total_pnl = sum(t.total_pnl for t in trades)
        total_funding = sum(t.cumulative_funding for t in trades)
        total_fees = sum(t.entry_fees + t.exit_fees for t in trades)

        durations: list[float] = []
        pnl_bps_list: list[float] = []
        for t in trades:
            if t.close_time_ms > 0 and t.open_time_ms > 0:
                durations.append((t.close_time_ms - t.open_time_ms) / 1000.0)
            if t.notional_usdt > 0:
                pnl_bps_list.append(t.total_pnl / t.notional_usdt * 10000)

        return FuturesArbStats(
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            total_net_pnl_usdt=total_pnl,
            total_funding_earned=total_funding,
            total_fees_usdt=total_fees,
            avg_hold_duration_sec=sum(durations) / len(durations) if durations else 0.0,
            avg_net_pnl_bps=sum(pnl_bps_list) / len(pnl_bps_list) if pnl_bps_list else 0.0,
            win_rate=len(winning) / len(trades) if trades else 0.0,
            max_pnl_usdt=max((t.total_pnl for t in trades), default=0.0),
            min_pnl_usdt=min((t.total_pnl for t in trades), default=0.0),
        )

    # --- Serialization ---

    def serialize_state(self) -> None:
        """Serialize all open positions to JSON file."""
        with self._lock:
            positions_data = [asdict(p) for p in self._positions.values()]

        data = {
            "version": 1,
            "timestamp_ms": int(time.time() * 1000),
            "open_positions": positions_data,
        }

        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self._state_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_file.replace(self._state_file)
            logger.info("State serialized: %d positions to %s", len(positions_data), self._state_file)
        except OSError as e:
            logger.error("Failed to serialize state: %s", e)

    def deserialize_state(self) -> None:
        """
        Deserialize positions from JSON file.

        If file is corrupted or invalid, logs error, starts with empty state,
        and sends alert via alert_service or on_alert callback.
        """
        if not self._state_file.is_file():
            logger.debug("No state file found at %s, starting fresh", self._state_file)
            return

        try:
            text = self._state_file.read_text(encoding="utf-8")
            data = json.loads(text)

            if not isinstance(data, dict) or "open_positions" not in data:
                raise ValueError("Invalid state file format: missing 'open_positions' key")

            positions_data = data["open_positions"]
            if not isinstance(positions_data, list):
                raise ValueError("Invalid state file format: 'open_positions' is not a list")

            loaded = 0
            for pos_dict in positions_data:
                try:
                    pos = self._dict_to_position(pos_dict)
                    if pos.state in ("pending_open", "open"):
                        with self._lock:
                            self._positions[pos.id] = pos
                        loaded += 1
                except (KeyError, TypeError, ValueError) as e:
                    logger.warning("Skipping invalid position entry: %s", e)
                    # If any entry is invalid, treat as corrupted
                    raise ValueError(f"Invalid position entry: {e}") from e

            logger.info("State deserialized: %d positions from %s", loaded, self._state_file)

        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error("Failed to deserialize state from %s: %s", self._state_file, e)
            # Start with empty state
            with self._lock:
                self._positions.clear()
            # Send alert
            self._send_recovery_alert(str(e))

    def _send_recovery_alert(self, error_msg: str) -> None:
        """Send state_recovery_failed alert."""
        alert_text = f"⚠️ state_recovery_failed: {error_msg}. Starting with empty state."

        if self._alert_service:
            try:
                self._alert_service._send(alert_text)
            except Exception:
                pass

        if self._on_alert:
            try:
                self._on_alert("state_recovery_failed", alert_text)
            except Exception:
                pass

    @staticmethod
    def _dict_to_position(d: dict[str, Any]) -> FuturesArbPosition:
        """Convert a dict to FuturesArbPosition, validating required fields."""
        known_fields = {f.name for f in fields(FuturesArbPosition)}
        filtered = {k: v for k, v in d.items() if k in known_fields}

        # Required fields
        required = [
            "id", "symbol", "exchange_combo", "strategy", "state",
            "spot_side", "spot_entry_price", "spot_qty",
            "futures_side", "futures_entry_price", "futures_qty",
            "futures_leverage", "notional_usdt", "entry_basis_bps", "open_time_ms",
        ]
        for field_name in required:
            if field_name not in filtered:
                raise KeyError(f"Missing required field: {field_name}")

        return FuturesArbPosition(**filtered)

    @staticmethod
    def position_to_trade_record(
        pos: FuturesArbPosition,
        mode: str = "paper",
    ) -> FuturesArbTradeRecord:
        """Convert a closed position to a trade record."""
        hold_duration_sec = 0.0
        if pos.close_time_ms > pos.open_time_ms:
            hold_duration_sec = (pos.close_time_ms - pos.open_time_ms) / 1000.0

        net_pnl_bps = 0.0
        annualized_return = 0.0
        if pos.notional_usdt > 0:
            net_pnl_bps = pos.total_pnl / pos.notional_usdt * 10000
            if hold_duration_sec > 0:
                annualized_return = (
                    (pos.total_pnl / pos.notional_usdt)
                    * (365 * 24 * 3600 / hold_duration_sec)
                    * 100
                )

        from datetime import datetime, timezone

        open_time_iso = datetime.fromtimestamp(
            pos.open_time_ms / 1000, tz=timezone.utc
        ).isoformat()
        close_time_iso = datetime.fromtimestamp(
            pos.close_time_ms / 1000, tz=timezone.utc
        ).isoformat() if pos.close_time_ms > 0 else ""

        return FuturesArbTradeRecord(
            id=pos.id,
            symbol=pos.symbol,
            exchange_combo=pos.exchange_combo,
            strategy=pos.strategy,
            mode=mode,
            spot_side=pos.spot_side,
            spot_entry_price=pos.spot_entry_price,
            spot_exit_price=0.0,
            futures_side=pos.futures_side,
            futures_entry_price=pos.futures_entry_price,
            futures_exit_price=0.0,
            qty=pos.spot_qty,
            notional_usdt=pos.notional_usdt,
            futures_leverage=pos.futures_leverage,
            entry_basis_bps=pos.entry_basis_bps,
            exit_basis_bps=pos.exit_basis_bps,
            basis_pnl=pos.basis_pnl,
            funding_earned=pos.cumulative_funding,
            fees_spot_leg=pos.entry_fees / 2,
            fees_futures_leg=pos.entry_fees / 2 + pos.exit_fees,
            net_pnl=pos.total_pnl,
            net_pnl_bps=net_pnl_bps,
            annualized_return=annualized_return,
            hold_duration_sec=hold_duration_sec,
            open_time_iso=open_time_iso,
            close_time_iso=close_time_iso,
            close_reason=pos.close_reason,
        )

    # --- Reconciliation ---

    def reconcile(
        self,
        actual_positions: list[tuple[str, str, float]] | None = None,
    ) -> dict[str, Any]:
        """Reconciliation: compare in-memory positions with actual exchange positions.

        Parameters
        ----------
        actual_positions
            List of tuples (symbol, side, qty) from exchange.
            If None, only checks in-memory state (no external verification).

        Returns
        -------
        dict[str, Any]
            Reconciliation result.
        """
        from mexc_monitor.reconciliation import (
            ReconciliationResult,
            ExpectedPosition,
            ActualPosition,
            reconcile_positions,
        )

        result = ReconciliationResult()
        open_positions = self.get_open_positions()
        expected_positions: list[ExpectedPosition] = []

        # Build expected positions from in-memory state
        for pos in open_positions:
            # Convert strategy to side direction
            side = "buy"
            if pos.strategy in ("reverse_cash_and_carry", "funding_arbitrage"):
                side = "sell"

            expected_positions.append(
                ExpectedPosition(
                    symbol=pos.symbol,
                    qty=pos.spot_qty,
                    side=side,
                    exchange=pos.exchange_combo,
                    engine_name="futures_arb",
                )
            )

        # actual_positions is None → report-only mode: no exchange data to
        # verify against, so no discrepancies can be detected.
        if actual_positions is not None:
            actual_map: dict[tuple[str, str], ActualPosition] = {}
            for (symbol, side, qty) in actual_positions:
                actual_map[(symbol.upper(), side.lower())] = ActualPosition(
                    symbol=symbol,
                    qty=qty,
                    side=side,
                )

            # Check expected positions
            for exp in expected_positions:
                expected_key = (exp.symbol.upper(), exp.side.lower())
                act = actual_map.get(expected_key)

                if act is None:
                    result.discrepancies.append({
                        "type": "missing_on_exchange",
                        "symbol": exp.symbol,
                        "expected_qty": exp.qty,
                        "actual_qty": 0.0,
                        "side": exp.side,
                        "exchange": exp.exchange,
                        "engine_name": "futures_arb",
                        "message": f"FuturesArb expects {exp.qty} {exp.symbol} ({exp.side}) on {exp.exchange}, but exchange has no such position",
                    })
                    result.all_clear = False
                elif abs(act.qty - exp.qty) > 1e-6:
                    result.discrepancies.append({
                        "type": "qty_mismatch",
                        "symbol": exp.symbol,
                        "expected_qty": exp.qty,
                        "actual_qty": act.qty,
                        "side": exp.side,
                        "exchange": exp.exchange,
                        "engine_name": "futures_arb",
                        "message": f"Qty mismatch for {exp.symbol} ({exp.side}): expected {exp.qty}, actual {act.qty}",
                    })
                    result.all_clear = False
                else:
                    result.matched.append({
                        "symbol": exp.symbol,
                        "side": exp.side,
                        "expected_qty": exp.qty,
                        "actual_qty": act.qty,
                        "exchange": exp.exchange,
                    })

            # Check for unexpected positions on exchange
            expected_keys = {(e.symbol.upper(), e.side.lower()) for e in expected_positions}
            for symbol, side, qty in actual_positions:
                key = (symbol.upper(), side.lower())
                if key not in expected_keys:
                    result.discrepancies.append({
                        "type": "unexpected_on_exchange",
                        "symbol": symbol,
                        "expected_qty": 0.0,
                        "actual_qty": qty,
                        "side": side,
                        "exchange": "exchange",
                        "engine_name": "",
                        "message": f"Unexpected position on exchange: {qty} {symbol} ({side})",
                    })
                    result.all_clear = False

        return {
            "open_positions_count": len(open_positions),
            "expected_positions_count": len(expected_positions),
            "actual_positions_count": len(actual_positions) if actual_positions else len(expected_positions),
            "reconciliation_result": asdict(result),
            "all_clear": result.all_clear,
        }
