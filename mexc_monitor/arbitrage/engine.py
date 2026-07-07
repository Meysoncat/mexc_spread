"""
Cross-Exchange Arbitrage Engine — автоматический межбиржевой арбитраж MEXC ↔ AsterDEX.

Алгоритм:
1. Для каждого символа получить latest tick с обеих бирж из Spread_Buffer
2. Вычислить executable spread: (higher_bid - lower_ask) / mid - total_fees
3. Если spread > entry_threshold → открыть позицию (buy на дешёвой, sell на дорогой)
4. Мониторить открытые позиции: закрыть при сужении спреда или таймауте
5. One-leg protection: если одна нога не исполнена → отменить и закрыть другую
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from mexc_monitor.arbitrage.models import (
    ArbMode,
    ArbPosition,
    ArbStats,
    ArbTradeRecord,
    ArbitrageSettings,
)
from mexc_monitor.execution_model import ExecutionSimulator, ExecutionSettings
from mexc_monitor.freshness import get_fresh_tick
from mexc_monitor.order_executor import OrderExecutor, OrderTicket
from mexc_monitor.spread_buffer import get_latest, SpreadTick
from mexc_monitor.state_store import StateStore

logger = logging.getLogger(__name__)


class ArbitrageEngine:
    """Движок межбиржевого арбитража."""

    def __init__(self, settings: ArbitrageSettings | None = None):
        self._settings = settings or ArbitrageSettings()
        self._stats = ArbStats()
        self._positions: dict[str, ArbPosition] = {}  # symbol → position
        self._trades: deque[ArbTradeRecord] = deque(maxlen=500)
        self._events: deque[dict[str, Any]] = deque(maxlen=200)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._store = StateStore(self._settings.state_file)
        self._exec_sim = ExecutionSimulator(ExecutionSettings(
            fill_rate_per_sec=0.2,
            adverse_selection_ratio=0.3,
        ))
        self._order_executor: OrderExecutor | None = None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _append_event(self, event: dict[str, Any]) -> None:
        event["ts"] = self._now_iso()
        with self._lock:
            self._events.append(event)

    # ─── Public API ─────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "settings": asdict(self._settings),
                "stats": asdict(self._stats),
                "open_positions": [asdict(p) for p in self._positions.values() if p.state in ("pending_open", "open")],
                "open_count": sum(1 for p in self._positions.values() if p.state in ("pending_open", "open")),
            }

    def get_positions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(p) for p in self._positions.values() if p.state in ("pending_open", "open")]

    def get_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            trades = list(self._trades)
        return [asdict(t) for t in trades[-limit:]]

    def get_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[-limit:]

    def set_order_executor(self, executor: OrderExecutor) -> None:
        """Inject an OrderExecutor for live-mode order placement."""
        self._order_executor = executor

    def set_kill_switch(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self._settings.kill_switch = bool(enabled)
        self._append_event({"type": "kill_switch", "enabled": bool(enabled)})
        return self.get_status()

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            s = self._settings
            if "enabled" in patch:
                s.enabled = bool(patch["enabled"])
            if "mode" in patch:
                m = str(patch["mode"]).strip().lower()
                if m in ("paper", "live"):
                    s.mode = m  # type: ignore[assignment]
            if "symbols" in patch:
                syms = patch["symbols"]
                if isinstance(syms, list):
                    s.symbols = [str(x).strip().upper() for x in syms if str(x).strip()]
                elif isinstance(syms, str):
                    s.symbols = [x.strip().upper() for x in syms.split(",") if x.strip()]
            if "entry_threshold_bps" in patch:
                s.entry_threshold_bps = max(0.0, float(patch["entry_threshold_bps"]))
            if "exit_threshold_bps" in patch:
                s.exit_threshold_bps = max(0.0, float(patch["exit_threshold_bps"]))
            if "max_position_notional_usdt" in patch:
                s.max_position_notional_usdt = max(1.0, float(patch["max_position_notional_usdt"]))
            if "max_concurrent_trades" in patch:
                s.max_concurrent_trades = max(1, int(patch["max_concurrent_trades"]))
            if "max_pending_sec" in patch:
                s.max_pending_sec = max(1.0, float(patch["max_pending_sec"]))
            if "max_hold_sec" in patch:
                s.max_hold_sec = max(5.0, float(patch["max_hold_sec"]))
            if "kill_switch" in patch:
                s.kill_switch = bool(patch["kill_switch"])
            if "loop_interval_sec" in patch:
                s.loop_interval_sec = max(0.1, float(patch["loop_interval_sec"]))
            if "mexc_taker_fee_bps" in patch:
                s.mexc_taker_fee_bps = max(0.0, float(patch["mexc_taker_fee_bps"]))
            if "aster_taker_fee_bps" in patch:
                s.aster_taker_fee_bps = max(0.0, float(patch["aster_taker_fee_bps"]))
            if "max_tick_age_ms" in patch:
                s.max_tick_age_ms = max(0.0, float(patch["max_tick_age_ms"]))
        self._append_event({"type": "settings_updated", "patch": patch})
        return self.get_status()

    def start(self) -> dict[str, Any]:
        self.deserialize_state()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.get_status()
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="arb-engine")
            self._thread.start()
        self._append_event({"type": "engine_started"})
        return self.get_status()

    def stop(self) -> dict[str, Any]:
        self._stop_event.set()
        with self._lock:
            t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=3.0)
        self.serialize_state()
        self._append_event({"type": "engine_stopped"})
        return self.get_status()

    # ─── Persistence ─────────────────────────────────────────────────────────

    def serialize_state(self) -> bool:
        """Save open positions and stats to disk atomically."""
        with self._lock:
            positions_data = []
            for p in self._positions.values():
                if p.state in ("pending_open", "open"):
                    pos_data = {
                        "symbol": p.symbol,
                        "state": p.state,
                        "buy_exchange": p.buy_exchange,
                        "sell_exchange": p.sell_exchange,
                        "buy_price": p.buy_price,
                        "sell_price": p.sell_price,
                        "qty": p.qty,
                        "notional_usdt": p.notional_usdt,
                        "open_time_ms": p.open_time_ms,
                        "entry_basis_bps": p.entry_basis_bps,
                        "buy_ticket_id": p.buy_ticket_id,
                        "sell_ticket_id": p.sell_ticket_id,
                        "buy_leg_filled": p.buy_leg_filled,
                        "sell_leg_filled": p.sell_leg_filled,
                        "pending_since_ms": p.pending_since_ms,
                        "close_time_ms": p.close_time_ms,
                        "buy_close_price": p.buy_close_price,
                        "sell_close_price": p.sell_close_price,
                        "exit_basis_bps": p.exit_basis_bps,
                        "gross_pnl": p.gross_pnl,
                        "fees": p.fees,
                        "net_pnl": p.net_pnl,
                        "close_reason": p.close_reason,
                    }
                    positions_data.append(pos_data)
            data = {
                "version": 1,
                "timestamp_ms": self._now_ms(),
                "positions": positions_data,
                "stats": asdict(self._stats),
            }
        ok = self._store.save(data)
        if ok:
            logger.debug("arbitrage state saved: %d positions", len(positions_data))
        return ok

    def deserialize_state(self) -> None:
        """Load open positions and stats from disk. Starts fresh on corruption."""
        data = self._store.load()
        if data is None:
            return
        try:
            positions_data = data.get("positions", [])
            stats_data = data.get("stats", {})
            restored = 0
            with self._lock:
                for pd in positions_data:
                    pos = ArbPosition(
                        symbol=pd["symbol"],
                        state=pd.get("state", "open"),
                        buy_exchange=pd.get("buy_exchange", ""),
                        sell_exchange=pd.get("sell_exchange", ""),
                        buy_price=float(pd.get("buy_price", 0)),
                        sell_price=float(pd.get("sell_price", 0)),
                        qty=float(pd.get("qty", 0)),
                        notional_usdt=float(pd.get("notional_usdt", 0)),
                        open_time_ms=int(pd.get("open_time_ms", 0)),
                        entry_basis_bps=float(pd.get("entry_basis_bps", 0)),
                        buy_ticket_id=str(pd.get("buy_ticket_id", "")),
                        sell_ticket_id=str(pd.get("sell_ticket_id", "")),
                        buy_leg_filled=bool(pd.get("buy_leg_filled", False)),
                        sell_leg_filled=bool(pd.get("sell_leg_filled", False)),
                        pending_since_ms=int(pd.get("pending_since_ms", 0)),
                        close_time_ms=int(pd.get("close_time_ms", 0)),
                        buy_close_price=float(pd.get("buy_close_price", 0)),
                        sell_close_price=float(pd.get("sell_close_price", 0)),
                        exit_basis_bps=float(pd.get("exit_basis_bps", 0)),
                        gross_pnl=float(pd.get("gross_pnl", 0)),
                        fees=float(pd.get("fees", 0)),
                        net_pnl=float(pd.get("net_pnl", 0)),
                        close_reason=str(pd.get("close_reason", "")),
                    )
                    if pos.state in ("pending_open", "open"):
                        self._positions[pos.symbol] = pos
                        restored += 1
                        # Cancel pending orders on restart
                        if pos.state == "pending_open" and self._order_executor:
                            if pos.buy_ticket_id:
                                buy_ticket = OrderTicket(
                                    order_id=pos.buy_ticket_id,
                                    client_order_id=pos.buy_ticket_id,
                                    symbol=pos.symbol,
                                    side="BUY",
                                    order_type="LIMIT",
                                    qty=pos.qty,
                                    price=pos.buy_price,
                                )
                                self._order_executor.cancel(buy_ticket)
                            if pos.sell_ticket_id:
                                sell_ticket = OrderTicket(
                                    order_id=pos.sell_ticket_id,
                                    client_order_id=pos.sell_ticket_id,
                                    symbol=pos.symbol,
                                    side="SELL",
                                    order_type="LIMIT",
                                    qty=pos.qty,
                                    price=pos.sell_price,
                                )
                                self._order_executor.cancel(sell_ticket)

                self._stats = ArbStats(
                    total_trades=int(stats_data.get("total_trades", 0)),
                    winning_trades=int(stats_data.get("winning_trades", 0)),
                    losing_trades=int(stats_data.get("losing_trades", 0)),
                    total_gross_pnl_usdt=float(stats_data.get("total_gross_pnl_usdt", 0)),
                    total_fees_usdt=float(stats_data.get("total_fees_usdt", 0)),
                    net_pnl_usdt=float(stats_data.get("net_pnl_usdt", 0)),
                    avg_hold_sec=float(stats_data.get("avg_hold_sec", 0)),
                    avg_net_pnl_bps=float(stats_data.get("avg_net_pnl_bps", 0)),
                    max_pnl_usdt=float(stats_data.get("max_pnl_usdt", 0)),
                    min_pnl_usdt=float(stats_data.get("min_pnl_usdt", 0)),
                )
            if restored:
                logger.info("Restored %d arbitrage positions from state file", restored)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Failed to restore arbitrage state: %s", e)

    # ─── Engine loop ────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self._step()
            except Exception as e:
                self._append_event({"type": "error", "message": f"{type(e).__name__}: {e}"})
            spent = time.monotonic() - started
            sleep_for = max(0.0, self._settings.loop_interval_sec - spent)
            self._stop_event.wait(timeout=sleep_for)

    def _step(self) -> None:
        if self._settings.kill_switch:
            return
        if not self._settings.enabled:
            return

        # Check pending positions for leg fills
        self._check_pending_positions()

        # Check existing positions for exit
        self._check_open_positions()

        # Check for new entry opportunities
        self._check_entry_opportunities()

    def _check_entry_opportunities(self) -> None:
        """Проверить все символы на арбитражные возможности."""
        with self._lock:
            open_count = sum(1 for p in self._positions.values() if p.state in ("pending_open", "open"))
            if open_count >= self._settings.max_concurrent_trades:
                return

        for symbol in self._settings.symbols:
            with self._lock:
                if symbol in self._positions and self._positions[symbol].state in ("pending_open", "open"):
                    continue
                open_count = sum(1 for p in self._positions.values() if p.state in ("pending_open", "open"))
                if open_count >= self._settings.max_concurrent_trades:
                    break

            self._evaluate_symbol(symbol)

    def _get_fresh_mexc_tick(self, symbol: str) -> SpreadTick | None:
        """Resolve a fresh MEXC tick, trying spot format then futures format."""
        max_age = self._settings.max_tick_age_ms
        tick = get_fresh_tick(symbol, max_age, exchange="mexc", adjust_for_skew=True)
        if tick is not None:
            return tick
        # Try futures format (BTCUSDT → BTC_USDT)
        if "USDT" in symbol and "_" not in symbol:
            fut_sym = symbol.replace("USDT", "_USDT")
            return get_fresh_tick(fut_sym, max_age, exchange="mexc", adjust_for_skew=True)
        return None

    def _get_fresh_aster_tick(self, symbol: str) -> SpreadTick | None:
        """Resolve a fresh AsterDEX tick."""
        return get_fresh_tick(f"ASTER:{symbol}", self._settings.max_tick_age_ms, exchange="asterdex", adjust_for_skew=True)

    def _evaluate_symbol(self, symbol: str) -> None:
        """Оценить арбитражную возможность для символа."""
        # Get fresh MEXC tick
        mexc_tick = self._get_fresh_mexc_tick(symbol)
        if mexc_tick is None:
            return

        # Get fresh AsterDEX tick
        aster_tick = self._get_fresh_aster_tick(symbol)
        if aster_tick is None:
            return

        # Compute executable spread in both directions
        total_fees_bps = self._settings.mexc_taker_fee_bps + self._settings.aster_taker_fee_bps

        # Direction 1: Buy MEXC (at ask), Sell Aster (at bid)
        spread_buy_mexc = aster_tick.bid - mexc_tick.ask
        mid = (mexc_tick.mid + aster_tick.mid) / 2
        if mid <= 0:
            return
        spread_buy_mexc_bps = 10_000 * spread_buy_mexc / mid - total_fees_bps

        # Direction 2: Buy Aster (at ask), Sell MEXC (at bid)
        spread_buy_aster = mexc_tick.bid - aster_tick.ask
        spread_buy_aster_bps = 10_000 * spread_buy_aster / mid - total_fees_bps

        # Choose best direction
        if spread_buy_mexc_bps >= spread_buy_aster_bps and spread_buy_mexc_bps >= self._settings.entry_threshold_bps:
            self._open_position(
                symbol=symbol,
                buy_exchange="mexc",
                sell_exchange="asterdex",
                buy_price=mexc_tick.ask,
                sell_price=aster_tick.bid,
                basis_bps=spread_buy_mexc_bps,
                mid=mid,
            )
        elif spread_buy_aster_bps >= self._settings.entry_threshold_bps:
            self._open_position(
                symbol=symbol,
                buy_exchange="asterdex",
                sell_exchange="mexc",
                buy_price=aster_tick.ask,
                sell_price=mexc_tick.bid,
                basis_bps=spread_buy_aster_bps,
                mid=mid,
            )

    def _open_position(
        self,
        symbol: str,
        buy_exchange: str,
        sell_exchange: str,
        buy_price: float,
        sell_price: float,
        basis_bps: float,
        mid: float,
    ) -> None:
        """Открыть арбитражную позицию (через pending_open → open)."""
        qty = self._settings.max_position_notional_usdt / buy_price if buy_price > 0 else 0
        if qty <= 0:
            return

        now_ms = self._now_ms()
        buy_ticket: OrderTicket | None = None
        sell_ticket: OrderTicket | None = None

        # Live-режим: размещаем реальные лимитные ордера
        if self._settings.mode == "live" and self._settings.use_real_orders and self._order_executor:
            try:
                buy_ticket = self._order_executor.place_limit_order(
                    symbol=symbol,
                    side="BUY",
                    qty=qty,
                    price=buy_price,
                )
                sell_ticket = self._order_executor.place_limit_order(
                    symbol=symbol,
                    side="SELL",
                    qty=qty,
                    price=sell_price,
                )
                if buy_ticket is None or sell_ticket is None:
                    self._append_event({
                        "type": "order_placement_failed",
                        "reason": f"buy={buy_ticket is None}, sell={sell_ticket is None}",
                    })
                    return
            except Exception as e:
                self._append_event({"type": "order_placement_error", "error": str(e)})
                return

        pos = ArbPosition(
            symbol=symbol,
            state="pending_open",
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            buy_price=buy_price,
            sell_price=sell_price,
            qty=qty,
            notional_usdt=self._settings.max_position_notional_usdt,
            open_time_ms=now_ms,
            entry_basis_bps=basis_bps,
            buy_ticket_id=buy_ticket.order_id if buy_ticket else "",
            sell_ticket_id=sell_ticket.order_id if sell_ticket else "",
            buy_leg_filled=False,
            sell_leg_filled=False,
            pending_since_ms=now_ms,
        )

        with self._lock:
            self._positions[symbol] = pos

        self._append_event({
            "type": "position_pending",
            "symbol": symbol,
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "qty": qty,
            "basis_bps": basis_bps,
            "mode": self._settings.mode,
            "buy_ticket_id": pos.buy_ticket_id,
            "sell_ticket_id": pos.sell_ticket_id,
        })

    def _check_pending_positions(self) -> None:
        """Check pending_open positions: simulate leg fills or poll real orders."""
        now_ms = self._now_ms()
        with self._lock:
            pending = [
                (s, p) for s, p in self._positions.items()
                if p.state == "pending_open"
            ]

        for symbol, pos in pending:
            elapsed_sec = (now_ms - pos.pending_since_ms) / 1000.0

            # Live-режим: polling статуса реальных ордеров
            if self._settings.mode == "live" and self._settings.use_real_orders and self._order_executor:
                updated = False

                # Poll buy ticket
                if not pos.buy_leg_filled and pos.buy_ticket_id:
                    buy_ticket = OrderTicket(
                        order_id=pos.buy_ticket_id,
                        client_order_id=pos.buy_ticket_id,
                        symbol=symbol,
                        side="BUY",
                        order_type="LIMIT",
                        qty=pos.qty,
                        price=pos.buy_price,
                    )
                    poll_result = self._order_executor.poll_status(buy_ticket)
                    if poll_result.is_filled:
                        with self._lock:
                            self._positions[symbol].buy_leg_filled = True
                        self._append_event({"type": "buy_leg_filled", "symbol": symbol})
                        updated = True

                # Poll sell ticket
                if not pos.sell_leg_filled and pos.sell_ticket_id:
                    sell_ticket = OrderTicket(
                        order_id=pos.sell_ticket_id,
                        client_order_id=pos.sell_ticket_id,
                        symbol=symbol,
                        side="SELL",
                        order_type="LIMIT",
                        qty=pos.qty,
                        price=pos.sell_price,
                    )
                    poll_result = self._order_executor.poll_status(sell_ticket)
                    if poll_result.is_filled:
                        with self._lock:
                            self._positions[symbol].sell_leg_filled = True
                        self._append_event({"type": "sell_leg_filled", "symbol": symbol})
                        updated = True

                with self._lock:
                    pos = self._positions.get(symbol)
                if pos is None:
                    continue

                # Timeout — cancel real orders and unwind
                if elapsed_sec > self._settings.max_pending_sec:
                    if pos.buy_ticket_id:
                        buy_ticket = OrderTicket(
                            order_id=pos.buy_ticket_id,
                            client_order_id=pos.buy_ticket_id,
                            symbol=symbol,
                            side="BUY",
                            order_type="LIMIT",
                            qty=pos.qty,
                            price=pos.buy_price,
                        )
                        self._order_executor.cancel(buy_ticket)

                    if pos.sell_ticket_id:
                        sell_ticket = OrderTicket(
                            order_id=pos.sell_ticket_id,
                            client_order_id=pos.sell_ticket_id,
                            symbol=symbol,
                            side="SELL",
                            order_type="LIMIT",
                            qty=pos.qty,
                            price=pos.sell_price,
                        )
                        self._order_executor.cancel(sell_ticket)

                    # Unwind filled leg
                    if pos.buy_leg_filled and not pos.sell_leg_filled:
                        self._unwind_one_leg(symbol, pos, "buy_unwind")
                    elif pos.sell_leg_filled and not pos.buy_leg_filled:
                        self._unwind_one_leg(symbol, pos, "sell_unwind")
                    else:
                        # Neither filled — remove pending
                        with self._lock:
                            del self._positions[symbol]
                        self._append_event({"type": "pending_cancelled", "symbol": symbol})
                    continue

                # Both filled → open
                if pos.buy_leg_filled and pos.sell_leg_filled:
                    with self._lock:
                        pos.state = "open"
                        pos.open_time_ms = now_ms
                    self._append_event({
                        "type": "position_opened",
                        "symbol": symbol,
                        "buy_exchange": pos.buy_exchange,
                        "sell_exchange": pos.sell_exchange,
                        "buy_price": pos.buy_price,
                        "sell_price": pos.sell_price,
                        "basis_bps": pos.entry_basis_bps,
                    })
                    self._notify_arb_alert(pos)
                    continue
                else:
                    # Still pending — continue polling next iteration
                    continue

            # Paper-режим: симуляция (legacy behaviour)
            if self._settings.mode == "paper":
                if not pos.buy_leg_filled:
                    outcome = self._exec_sim.check_limit_fill(
                        pos.buy_price, pos.buy_price * 0.999, pos.buy_price,
                        elapsed_sec, "buy",
                    )
                    if outcome.filled:
                        with self._lock:
                            self._positions[symbol].buy_leg_filled = True
                        self._append_event({"type": "buy_leg_filled", "symbol": symbol})

                if not pos.sell_leg_filled:
                    outcome = self._exec_sim.check_limit_fill(
                        pos.sell_price, pos.sell_price, pos.sell_price * 1.001,
                        elapsed_sec, "sell",
                    )
                    if outcome.filled:
                        with self._lock:
                            self._positions[symbol].sell_leg_filled = True
                        self._append_event({"type": "sell_leg_filled", "symbol": symbol})
            else:
                # Legacy live mode (no real orders yet)
                with self._lock:
                    self._positions[symbol].buy_leg_filled = True
                    self._positions[symbol].sell_leg_filled = True

            with self._lock:
                pos = self._positions.get(symbol)
            if pos is None:
                continue

            # Both filled → open
            if pos.buy_leg_filled and pos.sell_leg_filled:
                with self._lock:
                    pos.state = "open"
                    pos.open_time_ms = now_ms
                self._append_event({
                    "type": "position_opened",
                    "symbol": symbol,
                    "buy_exchange": pos.buy_exchange,
                    "sell_exchange": pos.sell_exchange,
                    "buy_price": pos.buy_price,
                    "sell_price": pos.sell_price,
                    "basis_bps": pos.entry_basis_bps,
                })
                self._notify_arb_alert(pos)
                continue

            # Timeout → one-leg protection
            if elapsed_sec > self._settings.max_pending_sec:
                if pos.buy_leg_filled and not pos.sell_leg_filled:
                    self._unwind_one_leg(symbol, pos, "buy_unwind")
                elif pos.sell_leg_filled and not pos.buy_leg_filled:
                    self._unwind_one_leg(symbol, pos, "sell_unwind")
                else:
                    # Neither filled → cancel
                    with self._lock:
                        del self._positions[symbol]
                    self._append_event({"type": "pending_cancelled", "symbol": symbol})

    def _unwind_one_leg(self, symbol: str, pos: ArbPosition, reason: str) -> None:
        """Unwind a single filled leg when the other leg failed (one-leg risk).

        Records the trade as a loss — the unhedged leg is closed at market price
        with slippage penalty.
        """
        now_ms = self._now_ms()

        if reason == "buy_unwind":
            # Buy filled, sell didn't → we have an unhedged long
            # Unwind: sell at current market bid (with slippage)
            tick = self._get_fresh_mexc_tick(symbol) or self._get_fresh_aster_tick(symbol)
            if tick:
                exit_price = self._exec_sim.market_exit_price(
                    tick.bid, tick.ask, side="sell",
                )
            else:
                exit_price = pos.buy_price * 0.998  # 20 bps penalty fallback
            gross_pnl = (exit_price - pos.buy_price) * pos.qty
        else:
            # Sell filled, buy didn't → we have an unhedged short
            # Unwind: buy at current market ask (with slippage)
            tick = self._get_fresh_mexc_tick(symbol) or self._get_fresh_aster_tick(symbol)
            if tick:
                exit_price = self._exec_sim.market_exit_price(
                    tick.bid, tick.ask, side="buy",
                )
            else:
                exit_price = pos.sell_price * 1.002  # 20 bps penalty fallback
            gross_pnl = (pos.sell_price - exit_price) * pos.qty

        # Fees: one fill + one unwind = 2 taker fees
        fee_rate = (
            self._settings.mexc_taker_fee_bps / 10_000
            if pos.buy_exchange == "mexc"
            else self._settings.aster_taker_fee_bps / 10_000
        )
        fees = pos.notional_usdt * fee_rate * 2
        net_pnl = gross_pnl - fees
        hold_sec = (now_ms - pos.pending_since_ms) / 1000.0

        trade = ArbTradeRecord(
            symbol=symbol,
            mode=self._settings.mode,
            buy_exchange=pos.buy_exchange,
            sell_exchange=pos.sell_exchange,
            buy_entry_price=pos.buy_price,
            sell_entry_price=pos.sell_price,
            buy_exit_price=exit_price if reason == "buy_unwind" else 0.0,
            sell_exit_price=exit_price if reason == "sell_unwind" else 0.0,
            qty=pos.qty,
            notional_usdt=pos.notional_usdt,
            entry_basis_bps=pos.entry_basis_bps,
            exit_basis_bps=0.0,
            open_time_iso=datetime.fromtimestamp(pos.pending_since_ms / 1000, tz=timezone.utc).isoformat(),
            close_time_iso=self._now_iso(),
            hold_sec=hold_sec,
            gross_pnl_usdt=gross_pnl,
            total_fees_usdt=fees,
            net_pnl_usdt=net_pnl,
            net_pnl_bps=(10_000 * net_pnl / pos.notional_usdt) if pos.notional_usdt > 0 else 0.0,
            close_reason=reason,
        )

        with self._lock:
            self._trades.append(trade)
            del self._positions[symbol]
            self._stats.total_trades += 1
            self._stats.net_pnl_usdt += net_pnl
            self._stats.total_fees_usdt += fees
            if net_pnl > 0:
                self._stats.winning_trades += 1
            else:
                self._stats.losing_trades += 1
            if net_pnl > self._stats.max_pnl_usdt:
                self._stats.max_pnl_usdt = net_pnl
            if net_pnl < self._stats.min_pnl_usdt:
                self._stats.min_pnl_usdt = net_pnl

        self._append_event({
            "type": "one_leg_unwind",
            "symbol": symbol,
            "reason": reason,
            "exit_price": exit_price,
            "net_pnl_usdt": net_pnl,
        })

    def _notify_arb_alert(self, pos: ArbPosition) -> None:
        """Send Telegram alert for opened position."""
        try:
            from mexc_monitor.alerts.service import AlertService
            from mexc_monitor.alerts.config import load_alert_config
            if not hasattr(self, "_alert_svc"):
                self._alert_svc = AlertService(load_alert_config())
            self._alert_svc.send_arbitrage_alert(
                symbol=pos.symbol,
                mexc_mid=pos.buy_price if pos.buy_exchange == "mexc" else pos.sell_price,
                aster_mid=pos.buy_price if pos.buy_exchange == "asterdex" else pos.sell_price,
                basis_bps=pos.entry_basis_bps,
            )
        except Exception:
            pass

    def _check_open_positions(self) -> None:
        """Проверить открытые позиции на условия выхода."""
        now_ms = self._now_ms()
        with self._lock:
            open_positions = [
                (sym, pos) for sym, pos in self._positions.items()
                if pos.state == "open"
            ]

        for symbol, pos in open_positions:
            # Get fresh current ticks
            mexc_tick = self._get_fresh_mexc_tick(symbol)
            aster_tick = self._get_fresh_aster_tick(symbol)

            # Timeout check
            hold_ms = now_ms - pos.open_time_ms
            if hold_ms >= self._settings.max_hold_sec * 1000:
                self._close_position(symbol, pos, mexc_tick, aster_tick, reason="timeout")
                continue

            # Spread convergence check
            if mexc_tick and aster_tick:
                mid = (mexc_tick.mid + aster_tick.mid) / 2
                if mid > 0:
                    # Current spread in the same direction as entry
                    if pos.buy_exchange == "mexc":
                        current_spread_bps = 10_000 * (aster_tick.bid - mexc_tick.ask) / mid
                    else:
                        current_spread_bps = 10_000 * (mexc_tick.bid - aster_tick.ask) / mid

                    if current_spread_bps <= self._settings.exit_threshold_bps:
                        self._close_position(symbol, pos, mexc_tick, aster_tick, reason="spread_converged")

    def _close_position(
        self,
        symbol: str,
        pos: ArbPosition,
        mexc_tick: SpreadTick | None,
        aster_tick: SpreadTick | None,
        reason: str,
    ) -> None:
        """Закрыть арбитражную позицию."""
        now_ms = self._now_ms()

        # Determine exit prices
        if pos.buy_exchange == "mexc":
            buy_close = mexc_tick.bid if mexc_tick else pos.buy_price
            sell_close = aster_tick.ask if aster_tick else pos.sell_price
        else:
            buy_close = aster_tick.bid if aster_tick else pos.buy_price
            sell_close = mexc_tick.ask if mexc_tick else pos.sell_price

        # PNL calculation
        # Profit = (sell_entry - buy_entry) * qty + (buy_close - sell_close) * qty
        # Simplified: entry profit + exit cost
        entry_profit = (pos.sell_price - pos.buy_price) * pos.qty
        exit_cost = (sell_close - buy_close) * pos.qty  # cost to close (buy back short, sell long)
        gross_pnl = entry_profit - exit_cost

        # Fees: 2 sides × 2 exchanges × taker
        fee_rate_mexc = self._settings.mexc_taker_fee_bps / 10_000
        fee_rate_aster = self._settings.aster_taker_fee_bps / 10_000
        fees = (
            pos.buy_price * pos.qty * (fee_rate_mexc if pos.buy_exchange == "mexc" else fee_rate_aster)
            + pos.sell_price * pos.qty * (fee_rate_aster if pos.sell_exchange == "asterdex" else fee_rate_mexc)
            + buy_close * pos.qty * (fee_rate_mexc if pos.buy_exchange == "mexc" else fee_rate_aster)
            + sell_close * pos.qty * (fee_rate_aster if pos.sell_exchange == "asterdex" else fee_rate_mexc)
        )
        net_pnl = gross_pnl - fees
        hold_sec = (now_ms - pos.open_time_ms) / 1000.0
        net_pnl_bps = (10_000 * net_pnl / pos.notional_usdt) if pos.notional_usdt > 0 else 0.0

        # Current basis
        exit_basis_bps = 0.0
        if mexc_tick and aster_tick:
            mid = (mexc_tick.mid + aster_tick.mid) / 2
            if mid > 0:
                if pos.buy_exchange == "mexc":
                    exit_basis_bps = 10_000 * (aster_tick.mid - mexc_tick.mid) / mid
                else:
                    exit_basis_bps = 10_000 * (mexc_tick.mid - aster_tick.mid) / mid

        # Record trade
        trade = ArbTradeRecord(
            symbol=symbol,
            mode=self._settings.mode,
            buy_exchange=pos.buy_exchange,
            sell_exchange=pos.sell_exchange,
            buy_entry_price=pos.buy_price,
            sell_entry_price=pos.sell_price,
            buy_exit_price=buy_close,
            sell_exit_price=sell_close,
            qty=pos.qty,
            notional_usdt=pos.notional_usdt,
            entry_basis_bps=pos.entry_basis_bps,
            exit_basis_bps=exit_basis_bps,
            open_time_iso=datetime.fromtimestamp(pos.open_time_ms / 1000, tz=timezone.utc).isoformat(),
            close_time_iso=self._now_iso(),
            hold_sec=hold_sec,
            gross_pnl_usdt=gross_pnl,
            total_fees_usdt=fees,
            net_pnl_usdt=net_pnl,
            net_pnl_bps=net_pnl_bps,
            close_reason=reason,
        )

        with self._lock:
            self._trades.append(trade)
            del self._positions[symbol]
            # Update stats
            self._stats.total_trades += 1
            self._stats.total_gross_pnl_usdt += gross_pnl
            self._stats.total_fees_usdt += fees
            self._stats.net_pnl_usdt += net_pnl
            if net_pnl > 0:
                self._stats.winning_trades += 1
            else:
                self._stats.losing_trades += 1
            if net_pnl > self._stats.max_pnl_usdt:
                self._stats.max_pnl_usdt = net_pnl
            if net_pnl < self._stats.min_pnl_usdt:
                self._stats.min_pnl_usdt = net_pnl
            n = self._stats.total_trades
            self._stats.avg_hold_sec = (self._stats.avg_hold_sec * (n - 1) + hold_sec) / n
            self._stats.avg_net_pnl_bps = (self._stats.avg_net_pnl_bps * (n - 1) + net_pnl_bps) / n

        self._append_event({
            "type": "position_closed",
            "symbol": symbol,
            "reason": reason,
            "net_pnl_usdt": net_pnl,
            "hold_sec": hold_sec,
            "entry_basis_bps": pos.entry_basis_bps,
            "exit_basis_bps": exit_basis_bps,
        })
