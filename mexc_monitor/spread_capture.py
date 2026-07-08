"""
Spread Capture Engine — модуль для сбора спреда bid/ask в рамках одного актива.

Стратегия: покупка по bid (лимит), продажа по ask (лимит) на одном инструменте.
Цель — заработать на разнице bid/ask минус комиссии.

Режимы:
  - monitor: только мониторинг, сигналы и PNL-расчёт (без ордеров)
  - paper: симуляция ордеров, запись в журнал
  - live: реальные ордера через MEXC API

Состояния позиции:
  - idle: нет позиции, ждём сигнал входа
  - pending_buy: лимит-ордер на покупку отправлен
  - holding: позиция открыта (купили по bid), ждём сигнал выхода
  - pending_sell: лимит-ордер на продажу отправлен
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from mexc_monitor.execution_model import ExecutionSimulator, ExecutionSettings
from mexc_monitor.freshness import get_fresh_tick
from mexc_monitor.order_executor import OrderExecutor, OrderTicket
from mexc_monitor.reconciliation import reconcile_positions, ReconciliationResult, Discrepancy, ExpectedPosition, ActualPosition
from mexc_monitor.spread_buffer import get_latest, get_stats, SpreadTick
from mexc_monitor.state_store import StateStore

logger = logging.getLogger(__name__)

CaptureMode = Literal["monitor", "paper", "live"]
PositionState = Literal["idle", "pending_buy", "holding", "pending_sell"]


@dataclass
class CaptureSettings:
    """Настройки стратегии сбора спреда."""
    symbol: str = "BTCUSDT"
    market: str = "spot"
    exchange: str = "mexc_spot"  # "mexc_spot" | "mexc_futures" | "asterdex"
    mode: CaptureMode = "monitor"
    # Пороги (bps)
    entry_threshold_bps: float = 5.0  # Вход: spread >= threshold
    exit_threshold_bps: float = 1.0   # Выход: spread <= threshold (или по таймауту)
    # Размер позиции
    order_notional_usdt: float = 50.0  # Размер ордера в USDT
    # Таймауты
    max_hold_sec: float = 300.0  # Макс. время удержания позиции (сек)
    max_pending_sec: float = 30.0  # Макс. время ожидания исполнения ордера
    # Комиссии (для расчёта PNL)
    taker_fee_bps: float = 1.0  # Комиссия taker в одну сторону (bps)
    # Управление
    enabled: bool = False
    kill_switch: bool = True
    loop_interval_sec: float = 1.0
    max_trades_per_hour: int = 60
    # Freshness: максимальный возраст тика (мс). Тики старше — игнорируются.
    max_tick_age_ms: float = 5000.0
    # Execution model (paper mode)
    fill_rate_per_sec: float = 0.1  # Poisson rate для лимитных ордеров
    adverse_selection_ratio: float = 0.3  # доля half-sread, теряемая на adverse selection
    realistic_fills: bool = True  # False = legacy instant-fill (для сравнения)
    # Алерты
    sound_alert: bool = True
    telegram_alert: bool = False
    # Persistence
    state_file: str = "data/spread_capture_state.json"


@dataclass
class Position:
    """Текущая позиция."""
    state: PositionState = "idle"
    symbol: str = ""
    entry_price: float = 0.0
    entry_qty: float = 0.0
    entry_time_ms: int = 0
    entry_spread_bps: float = 0.0
    exit_price: float = 0.0
    exit_time_ms: int = 0
    exit_spread_bps: float = 0.0
    pnl_usdt: float = 0.0
    pnl_bps: float = 0.0
    # Для pending ордеров
    pending_order_id: str = ""
    pending_since_ms: int = 0
    # Adverse selection cost (цена/единица) от buy-fill
    entry_adverse_cost: float = 0.0
    # Live order ticket (when using real OrderExecutor)
    order_ticket_id: str = ""


@dataclass
class CaptureStats:
    """Статистика торговли."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl_usdt: float = 0.0
    total_fees_usdt: float = 0.0
    net_pnl_usdt: float = 0.0
    avg_hold_sec: float = 0.0
    avg_spread_captured_bps: float = 0.0
    max_pnl_usdt: float = 0.0
    min_pnl_usdt: float = 0.0
    trades_this_hour: int = 0
    hour_start_ms: int = 0


@dataclass
class TradeRecord:
    """Запись о завершённой сделке."""
    symbol: str
    exchange: str  # "mexc_spot" | "mexc_futures" | "asterdex"
    mode: CaptureMode
    entry_price: float
    exit_price: float
    qty: float
    entry_spread_bps: float
    exit_spread_bps: float
    entry_time_iso: str
    exit_time_iso: str
    hold_sec: float
    gross_pnl_usdt: float
    fees_usdt: float
    adverse_cost_usdt: float
    net_pnl_usdt: float
    net_pnl_bps: float


class SpreadCaptureEngine:
    """Движок сбора спреда bid/ask."""

    def __init__(self, settings: CaptureSettings | None = None):
        self._settings = settings or CaptureSettings()
        self._position = Position()
        self._stats = CaptureStats()
        self._trades: deque[TradeRecord] = deque(maxlen=500)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._events: deque[dict[str, Any]] = deque(maxlen=200)
        self._signals: deque[dict[str, Any]] = deque(maxlen=100)
        self._exec_sim = ExecutionSimulator(ExecutionSettings(
            fill_rate_per_sec=self._settings.fill_rate_per_sec,
            adverse_selection_ratio=self._settings.adverse_selection_ratio,
            realistic_fills=self._settings.realistic_fills,
        ))
        self._store = StateStore(self._settings.state_file)
        self._order_executor: OrderExecutor | None = None

    def set_order_executor(self, executor: OrderExecutor) -> None:
        """Inject an OrderExecutor for live-mode order placement."""
        self._order_executor = executor

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _append_event(self, event: dict[str, Any]) -> None:
        event["ts"] = self._now_iso()
        with self._lock:
            self._events.append(event)

    def _notify_alert(self, event: dict[str, Any]) -> None:
        """Отправить алерт через AlertService (best-effort, не блокирует)."""
        try:
            from mexc_monitor.alerts.service import AlertService
            from mexc_monitor.alerts.config import load_alert_config
            # Lazy singleton — не создаём зависимость при импорте
            if not hasattr(self, "_alert_service"):
                self._alert_service = AlertService(load_alert_config())
            self._alert_service.send_trade_alert(event)
        except Exception:
            pass  # Алерты не должны ломать торговый цикл

    # ─── Public API ─────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "settings": asdict(self._settings),
                "position": asdict(self._position),
                "stats": asdict(self._stats),
                "running": self._thread is not None and self._thread.is_alive(),
            }

    def get_trades(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            trades = list(self._trades)
        return [asdict(t) for t in trades[-limit:]]

    def get_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)[-limit:]

    def get_signals(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._signals)[-limit:]

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            s = self._settings
            if "symbol" in patch:
                s.symbol = str(patch["symbol"]).strip().upper() or s.symbol
            if "market" in patch:
                m = str(patch["market"]).strip().lower()
                if m in ("spot", "futures"):
                    s.market = m
            if "exchange" in patch:
                ex = str(patch["exchange"]).strip().lower()
                if ex in ("mexc_spot", "mexc_futures", "asterdex"):
                    # Reject if position is open
                    if self._position.state != "idle" and ex != s.exchange:
                        pass  # Silently ignore — can't switch with open position
                    else:
                        s.exchange = ex
            if "mode" in patch:
                m = str(patch["mode"]).strip().lower()
                if m in ("monitor", "paper", "live"):
                    s.mode = m  # type: ignore[assignment]
            if "entry_threshold_bps" in patch:
                s.entry_threshold_bps = max(0.0, float(patch["entry_threshold_bps"]))
            if "exit_threshold_bps" in patch:
                s.exit_threshold_bps = max(0.0, float(patch["exit_threshold_bps"]))
            if "order_notional_usdt" in patch:
                s.order_notional_usdt = max(1.0, float(patch["order_notional_usdt"]))
            if "max_hold_sec" in patch:
                s.max_hold_sec = max(5.0, float(patch["max_hold_sec"]))
            if "max_pending_sec" in patch:
                s.max_pending_sec = max(1.0, float(patch["max_pending_sec"]))
            if "taker_fee_bps" in patch:
                s.taker_fee_bps = max(0.0, float(patch["taker_fee_bps"]))
            if "enabled" in patch:
                s.enabled = bool(patch["enabled"])
            if "kill_switch" in patch:
                s.kill_switch = bool(patch["kill_switch"])
            if "loop_interval_sec" in patch:
                s.loop_interval_sec = max(0.2, float(patch["loop_interval_sec"]))
            if "max_trades_per_hour" in patch:
                s.max_trades_per_hour = max(1, int(patch["max_trades_per_hour"]))
            if "max_tick_age_ms" in patch:
                s.max_tick_age_ms = max(0.0, float(patch["max_tick_age_ms"]))
            if "fill_rate_per_sec" in patch:
                s.fill_rate_per_sec = max(0.0, float(patch["fill_rate_per_sec"]))
                self._exec_sim.update_settings(fill_rate_per_sec=s.fill_rate_per_sec)
            if "adverse_selection_ratio" in patch:
                s.adverse_selection_ratio = max(0.0, min(1.0, float(patch["adverse_selection_ratio"])))
                self._exec_sim.update_settings(adverse_selection_ratio=s.adverse_selection_ratio)
            if "realistic_fills" in patch:
                s.realistic_fills = bool(patch["realistic_fills"])
                self._exec_sim.update_settings(realistic_fills=s.realistic_fills)
            if "sound_alert" in patch:
                s.sound_alert = bool(patch["sound_alert"])
                self._exec_sim.update_settings(realistic_fills=s.realistic_fills)
            self._append_event({"type": "settings_updated", "patch": patch})
            return self.get_status()

    def start(self) -> dict[str, Any]:
        self.deserialize_state()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.get_status()
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="spread-capture")
            self._thread.start()
        self._append_event({"type": "engine_started", "symbol": self._settings.symbol})
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
        """Save current position and stats to disk atomically."""
        with self._lock:
            data = {
                "version": 1,
                "timestamp_ms": self._now_ms(),
                "position": asdict(self._position),
                "stats": asdict(self._stats),
                "exec_sim_state": self._exec_sim.serialize_state(),
            }
        ok = self._store.save(data)
        if ok:
            logger.debug("spread_capture state saved to %s", self._store.path)
        return ok

    def deserialize_state(self) -> None:
        """Load position and stats from disk. Starts fresh on corruption."""
        data = self._store.load()
        if data is None:
            return
        try:
            pos_data = data.get("position", {})
            stats_data = data.get("stats", {})
            exec_sim_data = data.get("exec_sim_state")

            if exec_sim_data is not None:
                self._exec_sim.deserialize_state(exec_sim_data)

            if pos_data.get("state") in ("pending_buy", "holding", "pending_sell"):
                # Restore position — but pending orders are cancelled
                # since we can't know fill status after restart
                state = pos_data.get("state", "idle")
                if state == "holding":
                    self._position = Position(
                        state="holding",
                        symbol=str(pos_data.get("symbol", "")),
                        entry_price=float(pos_data.get("entry_price", 0)),
                        entry_qty=float(pos_data.get("entry_qty", 0)),
                        entry_time_ms=int(pos_data.get("entry_time_ms", 0)),
                        entry_spread_bps=float(pos_data.get("entry_spread_bps", 0)),
                        entry_adverse_cost=float(pos_data.get("entry_adverse_cost", 0)),
                    )
                    logger.info("Restored holding position from state file")
                else:
                    # pending orders → cancel (safer than assuming fill)
                    logger.info("Cancelling pending %s on restart", state)
                    self._position = Position()
            else:
                self._position = Position()

            self._stats = CaptureStats(
                total_trades=int(stats_data.get("total_trades", 0)),
                winning_trades=int(stats_data.get("winning_trades", 0)),
                losing_trades=int(stats_data.get("losing_trades", 0)),
                total_pnl_usdt=float(stats_data.get("total_pnl_usdt", 0)),
                total_fees_usdt=float(stats_data.get("total_fees_usdt", 0)),
                net_pnl_usdt=float(stats_data.get("net_pnl_usdt", 0)),
                avg_hold_sec=float(stats_data.get("avg_hold_sec", 0)),
                avg_spread_captured_bps=float(stats_data.get("avg_spread_captured_bps", 0)),
                max_pnl_usdt=float(stats_data.get("max_pnl_usdt", 0)),
                min_pnl_usdt=float(stats_data.get("min_pnl_usdt", 0)),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Failed to restore spread_capture state: %s", e)
            self._position = Position()

    def reset_position(self) -> dict[str, Any]:
        """Сброс позиции (аварийный выход)."""
        with self._lock:
            if self._position.state != "idle":
                self._position = Position()
        self._append_event({"type": "position_reset"})
        return self.get_status()

    def reset_stats(self) -> dict[str, Any]:
        """Сброс статистики."""
        with self._lock:
            self._stats = CaptureStats()
            self._trades.clear()
        self._append_event({"type": "stats_reset"})
        return self.get_status()

    def reconcile(self) -> dict[str, Any]:
        """Reconciliation: сравнение in-memory позиции с реальными ордерами на бирже.

        Returns
        -------
        dict[str, Any]
            Результат reconciliation с details.
        """
        result = ReconciliationResult()

        with self._lock:
            pos = self._position

        symbol = pos.symbol or self._settings.symbol

        if self._order_executor is not None:
            expected_positions: list[ExpectedPosition] = []
            if pos.state in ("holding", "pending_buy", "pending_sell") and pos.entry_qty > 0:
                expected_positions.append(
                    ExpectedPosition(
                        symbol=symbol,
                        qty=pos.entry_qty,
                        side="sell" if pos.state == "pending_sell" else "buy",
                        exchange=self._settings.exchange,
                        engine_name="spread_capture",
                    )
                )

            try:
                # Get actual open orders from exchange
                open_orders = self._order_executor.get_open_orders(symbol)
                actual_positions: list[ActualPosition] = []
                for order in open_orders:
                    if not order.is_open:
                        continue
                    if order.side in ("BUY", "BID"):
                        side = "buy"
                    elif order.side in ("SELL", "ASK"):
                        side = "sell"
                    else:
                        continue
                    actual_positions.append(
                        ActualPosition(
                            symbol=symbol,
                            qty=order.remaining_qty,
                            side=side,
                            exchange=self._settings.exchange,
                            entry_price=order.price or pos.entry_price,
                        )
                    )

                reconcile_result = reconcile_positions(
                    expected=expected_positions,
                    actual=actual_positions,
                    qty_tolerance=1e-6,
                )
                result.matched = reconcile_result.matched
                result.discrepancies = reconcile_result.discrepancies
                result.all_clear = reconcile_result.all_clear
            except Exception as e:
                logger.error("Failed to reconcile spread_capture position: %s", e)
                result.all_clear = False
                result.discrepancies.append(Discrepancy(
                    type="missing_on_exchange",
                    symbol=symbol,
                    expected_qty=pos.entry_qty,
                    actual_qty=0.0,
                    side="buy",
                    exchange=self._settings.exchange,
                    engine_name="spread_capture",
                    message=f"Error during reconciliation: {e}",
                ))
        # Без OrderExecutor (paper/monitor) сверять не с чем — all_clear остаётся True

        return {
            "symbol": self._settings.symbol,
            "engine": "spread_capture",
            "position_state": pos.state,
            "entry_qty": pos.entry_qty if pos.state == "holding" else 0.0,
            "reconciliation_result": asdict(result),
            "mode": self._settings.mode,
            "exchange": self._settings.exchange,
        }

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

    def _get_buffer_key(self) -> str:
        """Ключ символа в Spread_Buffer в зависимости от биржи."""
        sym = self._settings.symbol
        ex = self._settings.exchange
        if ex == "asterdex":
            return f"ASTER:{sym}"
        elif ex == "mexc_futures":
            # Futures format: BTC_USDT
            if "_" not in sym and sym.endswith("USDT"):
                return sym.replace("USDT", "_USDT")
            return sym
        else:
            return sym  # mexc_spot

    def _step(self) -> None:
        if self._settings.kill_switch:
            return
        if not self._settings.enabled:
            return

        buffer_key = self._get_buffer_key()
        tick = get_fresh_tick(buffer_key, self._settings.max_tick_age_ms, exchange=self._settings.exchange, adjust_for_skew=True)
        if tick is None:
            return

        with self._lock:
            state = self._position.state

        if state == "idle":
            self._check_entry(tick)
        elif state == "holding":
            self._check_exit(tick)
        elif state == "pending_buy":
            self._check_pending_buy(tick)
        elif state == "pending_sell":
            self._check_pending_sell(tick)

    def _check_entry(self, tick: SpreadTick) -> None:
        """Проверка условия входа. В paper режиме — размещение pending buy."""
        if tick.spread_bps is None:
            return
        if tick.spread_bps < self._settings.entry_threshold_bps:
            return

        # Проверка лимита сделок в час
        now_ms = self._now_ms()
        with self._lock:
            if now_ms - self._stats.hour_start_ms > 3_600_000:
                self._stats.trades_this_hour = 0
                self._stats.hour_start_ms = now_ms
            if self._stats.trades_this_hour >= self._settings.max_trades_per_hour:
                return

        # Сигнал входа
        signal = {
            "type": "entry_signal",
            "symbol": self._settings.symbol,
            "spread_bps": tick.spread_bps,
            "bid": tick.bid,
            "ask": tick.ask,
            "threshold": self._settings.entry_threshold_bps,
        }
        with self._lock:
            self._signals.append({**signal, "ts": self._now_iso()})

        if self._settings.mode == "monitor":
            self._append_event(signal)
            return

        # Paper/Live: размещаем лимит-ордер на покупку по bid
        qty = self._settings.order_notional_usdt / tick.bid if tick.bid > 0 else 0.0
        if qty <= 0:
            return

        if self._settings.realistic_fills and self._settings.mode == "paper":
            # Paper: симуляция pending_buy через ExecutionSimulator
            with self._lock:
                self._position = Position(
                    state="pending_buy",
                    symbol=self._settings.symbol,
                    entry_price=tick.bid,
                    entry_qty=qty,
                    entry_time_ms=tick.timestamp_ms,
                    entry_spread_bps=tick.spread_bps,
                    pending_since_ms=now_ms,
                )
            self._append_event({
                "type": "limit_buy_placed",
                "mode": self._settings.mode,
                "limit_price": tick.bid,
                "qty": qty,
                "spread_bps": tick.spread_bps,
            })
        elif self._settings.mode == "live" and self._order_executor:
            # Live: размещаем реальный лимитный ордер
            ticket = self._order_executor.place_limit_order(
                symbol=self._settings.symbol,
                side="BUY",
                qty=qty,
                price=tick.bid,
            )
            if ticket is None:
                self._append_event({"type": "order_placement_failed", "side": "buy"})
                return
            with self._lock:
                self._position = Position(
                    state="pending_buy",
                    symbol=self._settings.symbol,
                    entry_price=tick.bid,
                    entry_qty=qty,
                    entry_time_ms=tick.timestamp_ms,
                    entry_spread_bps=tick.spread_bps,
                    pending_since_ms=now_ms,
                    order_ticket_id=ticket.order_id,
                )
            self._append_event({
                "type": "limit_buy_placed",
                "mode": "live",
                "order_id": ticket.order_id,
                "limit_price": tick.bid,
                "qty": qty,
            })
        else:
            # Legacy (realistic_fills=False): мгновенный вход
            self._open_holding(tick, qty)

    def _open_holding(self, tick: SpreadTick, qty: float) -> None:
        """Переход в holding — позиция открыта."""
        with self._lock:
            self._position = Position(
                state="holding",
                symbol=self._settings.symbol,
                entry_price=tick.bid,
                entry_qty=qty,
                entry_time_ms=tick.timestamp_ms,
                entry_spread_bps=tick.spread_bps or 0.0,
            )
        self._append_event({
            "type": "position_opened",
            "mode": self._settings.mode,
            "entry_price": tick.bid,
            "qty": qty,
            "spread_bps": tick.spread_bps,
            "notional_usdt": self._settings.order_notional_usdt,
        })
        self._notify_alert({
            "type": "position_opened",
            "symbol": self._settings.symbol,
            "entry_price": tick.bid,
            "notional_usdt": self._settings.order_notional_usdt,
            "spread_bps": tick.spread_bps,
        })

    def _check_exit(self, tick: SpreadTick) -> None:
        """Проверка условия выхода. При сигнале — размещение pending sell."""
        now_ms = self._now_ms()
        with self._lock:
            pos = self._position
            hold_ms = now_ms - pos.entry_time_ms

        # Условия выхода:
        # 1. Спред сузился до порога выхода
        # 2. Таймаут удержания
        spread_exit = (
            tick.spread_bps is not None
            and tick.spread_bps <= self._settings.exit_threshold_bps
        )
        timeout_exit = hold_ms >= self._settings.max_hold_sec * 1000

        if not spread_exit and not timeout_exit:
            return

        reason = "spread_exit" if spread_exit else "timeout_exit"

        if self._settings.realistic_fills and self._settings.mode == "paper":
            # Реалистичная модель: размещаем лимит-ордер на продажу по ask
            with self._lock:
                self._position.state = "pending_sell"
                self._position.pending_since_ms = now_ms
                self._position.exit_spread_bps = tick.spread_bps or 0.0
            self._append_event({
                "type": "limit_sell_placed",
                "limit_price": tick.ask,
                "reason": reason,
            })
        else:
            # Legacy/live: мгновенное закрытие
            self._close_and_record(tick, reason)

    def _close_and_record(self, tick: SpreadTick, reason: str) -> None:
        """Закрытие позиции и запись сделки с учётом adverse selection."""
        now_ms = self._now_ms()
        with self._lock:
            pos = self._position

        exit_price = tick.ask
        hold_ms = now_ms - pos.entry_time_ms

        gross_pnl = (exit_price - pos.entry_price) * pos.entry_qty
        fee_per_side = self._settings.taker_fee_bps / 10_000.0
        fees = pos.entry_price * pos.entry_qty * fee_per_side + exit_price * pos.entry_qty * fee_per_side

        # Adverse selection cost (от entry fill)
        adverse_total = pos.entry_adverse_cost * pos.entry_qty
        net_pnl = gross_pnl - adverse_total - fees
        net_pnl_bps = (10_000.0 * net_pnl / (pos.entry_price * pos.entry_qty)) if pos.entry_price > 0 else 0.0
        hold_sec = hold_ms / 1000.0

        trade = TradeRecord(
            symbol=self._settings.symbol,
            exchange=self._settings.exchange,
            mode=self._settings.mode,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=pos.entry_qty,
            entry_spread_bps=pos.entry_spread_bps,
            exit_spread_bps=tick.spread_bps or 0.0,
            entry_time_iso=datetime.fromtimestamp(pos.entry_time_ms / 1000, tz=timezone.utc).isoformat(),
            exit_time_iso=self._now_iso(),
            hold_sec=hold_sec,
            gross_pnl_usdt=gross_pnl,
            fees_usdt=fees,
            adverse_cost_usdt=adverse_total,
            net_pnl_usdt=net_pnl,
            net_pnl_bps=net_pnl_bps,
        )

        with self._lock:
            self._trades.append(trade)
            self._position = Position()
            self._stats.total_trades += 1
            self._stats.trades_this_hour += 1
            self._stats.total_pnl_usdt += gross_pnl
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
            self._stats.avg_hold_sec = (
                (self._stats.avg_hold_sec * (n - 1) + hold_sec) / n
            )
            self._stats.avg_spread_captured_bps = (
                (self._stats.avg_spread_captured_bps * (n - 1) + net_pnl_bps) / n
            )

        self._append_event({
            "type": "position_closed",
            "reason": reason,
            "mode": self._settings.mode,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "qty": pos.entry_qty,
            "hold_sec": hold_sec,
            "gross_pnl_usdt": gross_pnl,
            "fees_usdt": fees,
            "adverse_cost_usdt": adverse_total,
            "net_pnl_usdt": net_pnl,
            "net_pnl_bps": net_pnl_bps,
        })
        self._notify_alert({
            "type": "position_closed",
            "symbol": self._settings.symbol,
            "reason": reason,
            "net_pnl_usdt": net_pnl,
            "hold_sec": hold_sec,
        })

    def _check_pending_buy(self, tick: SpreadTick) -> None:
        """Обработка pending buy: симуляция (paper) или поллинг (live)."""
        now_ms = self._now_ms()
        with self._lock:
            pos = self._position
            elapsed_sec = (now_ms - pos.pending_since_ms) / 1000.0

        # Таймаут — отменяем
        if elapsed_sec > self._settings.max_pending_sec:
            # Live: отменяем реальный ордер
            if self._settings.mode == "live" and self._order_executor and pos.order_ticket_id:
                temp_ticket = OrderTicket(
                    order_id=pos.order_ticket_id,
                    client_order_id="",
                    symbol=self._settings.symbol,
                    side="BUY",
                    order_type="LIMIT",
                    qty=pos.entry_qty,
                    price=pos.entry_price,
                )
                self._order_executor.cancel(temp_ticket)
            with self._lock:
                self._position = Position()
            self._append_event({"type": "pending_buy_timeout"})
            return

        # Live-режим: поллинг статуса реального ордера
        if self._settings.mode == "live" and self._order_executor and pos.order_ticket_id:
            temp_ticket = OrderTicket(
                order_id=pos.order_ticket_id,
                client_order_id="",
                symbol=self._settings.symbol,
                side="BUY",
                order_type="LIMIT",
                qty=pos.entry_qty,
                price=pos.entry_price,
            )
            updated = self._order_executor.poll_status(temp_ticket)
            if updated.is_filled:
                with self._lock:
                    self._position = Position(
                        state="holding",
                        entry_price=pos.entry_price,
                        entry_qty=pos.entry_qty,
                        entry_time_ms=now_ms,
                        entry_spread_bps=pos.entry_spread_bps,
                    )
                self._append_event({
                    "type": "buy_filled",
                    "mode": "live",
                    "entry_price": pos.entry_price,
                    "order_id": pos.order_ticket_id,
                })
            return

        # Paper-режим: симуляция fill probability
        outcome = self._exec_sim.check_limit_fill(
            limit_price=pos.entry_price,
            bid=tick.bid,
            ask=tick.ask,
            elapsed_sec=elapsed_sec,
            side="buy",
        )
        if not outcome.filled:
            return

        # Filled — переходим в holding с adverse cost
        with self._lock:
            self._position = Position(
                state="holding",
                entry_price=pos.entry_price,
                entry_qty=pos.entry_qty,
                entry_time_ms=now_ms,
                entry_spread_bps=pos.entry_spread_bps,
                entry_adverse_cost=outcome.adverse_cost,
            )
        self._append_event({
            "type": "buy_filled",
            "entry_price": pos.entry_price,
            "adverse_cost": outcome.adverse_cost,
            "elapsed_sec": elapsed_sec,
        })

    def _check_pending_sell(self, tick: SpreadTick) -> None:
        """Симуляция исполнения pending sell ордера."""
        now_ms = self._now_ms()
        with self._lock:
            pos = self._position
            elapsed_sec = (now_ms - pos.pending_since_ms) / 1000.0

        # Таймаут — закрываем по market (с проскальзыванием)
        if elapsed_sec > self._settings.max_pending_sec:
            market_tick = SpreadTick(
                timestamp_ms=tick.timestamp_ms,
                bid=tick.bid,
                ask=tick.ask,
                bid_qty=tick.bid_qty,
                ask_qty=tick.ask_qty,
                mid=tick.mid,
                spread_abs=tick.spread_abs,
                spread_bps=tick.spread_bps,
            )
            # Эмулируем market sell: цена = bid − slippage
            market_price = self._exec_sim.market_exit_price(
                tick.bid, tick.ask, side="sell",
            )
            # Подменяем ask на market price для _close_and_record
            from dataclasses import replace as _replace
            market_tick = _replace(market_tick, ask=market_price)
            self._close_and_record(market_tick, reason="market_exit_timeout")
            return

        # Проверка fill probability
        outcome = self._exec_sim.check_limit_fill(
            limit_price=tick.ask,
            bid=tick.bid,
            ask=tick.ask,
            elapsed_sec=elapsed_sec,
            side="sell",
        )
        if not outcome.filled:
            return

        # Filled — закрываем позицию
        # Добавляем exit adverse cost к entry adverse cost
        with self._lock:
            self._position.entry_adverse_cost += outcome.adverse_cost
        self._close_and_record(tick, reason="limit_sell_filled")

    # ─── PNL calculation (real-time) ────────────────────────────────────────

    def get_current_pnl(self) -> dict[str, Any] | None:
        """Текущий PNL открытой позиции (если есть)."""
        with self._lock:
            pos = self._position
        if pos.state != "holding":
            return None

        buffer_key = self._get_buffer_key()
        tick = get_fresh_tick(buffer_key, self._settings.max_tick_age_ms, exchange=self._settings.exchange, adjust_for_skew=True)
        if tick is None:
            return None

        # Unrealized PNL: если бы продали сейчас по ask
        exit_price = tick.ask
        gross_pnl = (exit_price - pos.entry_price) * pos.entry_qty
        fee_per_side = self._settings.taker_fee_bps / 10_000.0
        fees = pos.entry_price * pos.entry_qty * fee_per_side + exit_price * pos.entry_qty * fee_per_side
        net_pnl = gross_pnl - fees
        hold_ms = self._now_ms() - pos.entry_time_ms

        return {
            "state": "holding",
            "symbol": self._settings.symbol,
            "entry_price": pos.entry_price,
            "current_ask": tick.ask,
            "current_bid": tick.bid,
            "current_spread_bps": tick.spread_bps,
            "qty": pos.entry_qty,
            "notional_usdt": pos.entry_price * pos.entry_qty,
            "unrealized_gross_pnl_usdt": gross_pnl,
            "unrealized_fees_usdt": fees,
            "unrealized_net_pnl_usdt": net_pnl,
            "hold_sec": hold_ms / 1000.0,
            "entry_spread_bps": pos.entry_spread_bps,
        }
