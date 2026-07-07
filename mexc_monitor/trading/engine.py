from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from mexc_monitor.config import DEFAULT_SETTINGS
from mexc_monitor.pipeline import safe_load_snapshot
from mexc_monitor.trading.exchanges import Exchange, Market, OrderSide, OrderType
from mexc_monitor.trading.private_client import MexcPrivateClient, PrivateApiError
from mexc_monitor.trading.private_client_base import (
    BasePrivateClient,
    OrderRequest,
    OrderResponse,
)
from mexc_monitor.trading.risk import RiskManager, RiskViolation


TradingMode = Literal["paper", "live"]


@dataclass(frozen=True)
class TradingSettings:
    enabled: bool = False
    mode: TradingMode = "paper"
    symbol: str = "BTCUSDT"
    order_type: str = "LIMIT"
    order_side: str = "BUY"
    min_net_spread_bps: float = -2.0
    order_quote_notional: float = 25.0
    limit_price_offset_bps: float = 0.0
    loop_interval_sec: float = 3.0
    max_orders_per_day: int = 20
    max_open_orders: int = 3
    max_consecutive_errors: int = 5
    kill_switch: bool = True
    api_key: str = ""
    api_secret: str = ""
    recv_window_ms: int = 5_000
    events_log_path: str = "data/trading_events.jsonl"


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_trading_settings() -> TradingSettings:
    mode_raw = str(os.environ.get("MEXC_TRADING_MODE", "paper")).strip().lower()
    mode: TradingMode = "live" if mode_raw == "live" else "paper"
    symbol = str(os.environ.get("MEXC_TRADING_SYMBOL", "BTCUSDT")).strip().upper()
    if not symbol:
        symbol = "BTCUSDT"
    return TradingSettings(
        enabled=_bool_env("MEXC_TRADING_ENABLED", False),
        mode=mode,
        symbol=symbol,
        min_net_spread_bps=_float_env("MEXC_TRADING_MIN_NET_SPREAD_BPS", -2.0),
        order_quote_notional=max(0.0, _float_env("MEXC_TRADING_ORDER_QUOTE_NOTIONAL", 25.0)),
        limit_price_offset_bps=_float_env("MEXC_TRADING_LIMIT_PRICE_OFFSET_BPS", 0.0),
        loop_interval_sec=max(0.5, _float_env("MEXC_TRADING_LOOP_INTERVAL_SEC", 3.0)),
        max_orders_per_day=max(1, _int_env("MEXC_TRADING_MAX_ORDERS_PER_DAY", 20)),
        max_open_orders=max(0, _int_env("MEXC_TRADING_MAX_OPEN_ORDERS", 3)),
        max_consecutive_errors=max(1, _int_env("MEXC_TRADING_MAX_CONSECUTIVE_ERRORS", 5)),
        kill_switch=_bool_env("MEXC_TRADING_KILL_SWITCH", True),
        api_key=str(os.environ.get("MEXC_API_KEY", "")).strip(),
        api_secret=str(os.environ.get("MEXC_API_SECRET", "")).strip(),
        recv_window_ms=max(1_000, _int_env("MEXC_RECV_WINDOW_MS", 5_000)),
        events_log_path=str(
            os.environ.get("MEXC_TRADING_EVENTS_LOG_PATH", "data/trading_events.jsonl")
        ).strip()
        or "data/trading_events.jsonl",
    )


@dataclass
class TradingState:
    running: bool = False
    mode: TradingMode = "paper"
    symbol: str = "BTCUSDT"
    kill_switch: bool = True
    started_at: str | None = None
    stopped_at: str | None = None
    last_error: str | None = None
    consecutive_errors: int = 0
    loop_count: int = 0
    signals_seen: int = 0
    orders_submitted: int = 0
    open_orders: int = 0
    last_signal_net_spread_bps: float | None = None
    last_observed_at: str | None = None
    last_order_client_id: str | None = None


class TradingEngine:
    def __init__(
        self,
        settings: TradingSettings | None = None,
        private_client: BasePrivateClient | None = None,
        exchange: Exchange = Exchange.MEXC,
        market: Market = Market.SPOT,
    ):
        self._settings = settings or load_trading_settings()
        self._exchange = exchange
        self._market = market
        self._client: BasePrivateClient | None = private_client
        self._state = TradingState(
            running=False,
            mode=self._settings.mode,
            symbol=self._settings.symbol,
            kill_switch=self._settings.kill_switch,
        )
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._risk = RiskManager(
            max_orders_per_day=self._settings.max_orders_per_day,
            max_open_orders=self._settings.max_open_orders,
            max_consecutive_errors=self._settings.max_consecutive_errors,
        )
        self._seq = 0

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _events_path(self) -> Path:
        root = Path(__file__).resolve().parent.parent.parent
        p = Path(self._settings.events_log_path)
        if p.is_absolute():
            return p
        return root / p

    def _append_event(self, event: dict[str, Any]) -> None:
        payload = {
            "ts": self._now_iso(),
            "symbol": self._settings.symbol,
            "mode": self._settings.mode,
            **event,
        }
        path = self._events_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True) + "\n")
        except OSError:
            # Журнал не должен останавливать торговый цикл.
            pass

    def status(self) -> dict[str, Any]:
        with self._lock:
            s = asdict(self._state)
            settings_safe = asdict(self._settings)
            settings_safe["api_key"] = "***" if settings_safe["api_key"] else ""
            settings_safe["api_secret"] = "***" if settings_safe["api_secret"] else ""
            return {"state": s, "settings": settings_safe}

    def update_runtime_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")

        with self._lock:
            s = self._settings
            kw: dict[str, Any] = {}

            if "enabled" in patch:
                kw["enabled"] = bool(patch["enabled"])
            if "mode" in patch:
                mode_raw = str(patch["mode"]).strip().lower()
                if mode_raw not in ("paper", "live"):
                    raise ValueError("mode must be 'paper' or 'live'")
                kw["mode"] = mode_raw
            if "symbol" in patch:
                symbol = str(patch["symbol"]).strip().upper()
                if not symbol:
                    raise ValueError("symbol cannot be empty")
                kw["symbol"] = symbol
            if "order_type" in patch:
                ot = str(patch["order_type"]).strip().upper()
                if ot not in ("LIMIT", "MARKET"):
                    raise ValueError("order_type must be 'LIMIT' or 'MARKET'")
                kw["order_type"] = ot
            if "order_side" in patch:
                os_val = str(patch["order_side"]).strip().upper()
                if os_val not in ("BUY", "SELL"):
                    raise ValueError("order_side must be 'BUY' or 'SELL'")
                kw["order_side"] = os_val
            if "min_net_spread_bps" in patch:
                kw["min_net_spread_bps"] = float(patch["min_net_spread_bps"])
            if "order_quote_notional" in patch:
                v = float(patch["order_quote_notional"])
                if v <= 0:
                    raise ValueError("order_quote_notional must be > 0")
                kw["order_quote_notional"] = v
            if "limit_price_offset_bps" in patch:
                kw["limit_price_offset_bps"] = float(patch["limit_price_offset_bps"])
            if "loop_interval_sec" in patch:
                v = float(patch["loop_interval_sec"])
                kw["loop_interval_sec"] = max(0.5, v)
            if "max_orders_per_day" in patch:
                v = int(patch["max_orders_per_day"])
                if v < 1:
                    raise ValueError("max_orders_per_day must be >= 1")
                kw["max_orders_per_day"] = v
            if "max_open_orders" in patch:
                v = int(patch["max_open_orders"])
                if v < 0:
                    raise ValueError("max_open_orders must be >= 0")
                kw["max_open_orders"] = v
            if "max_consecutive_errors" in patch:
                v = int(patch["max_consecutive_errors"])
                if v < 1:
                    raise ValueError("max_consecutive_errors must be >= 1")
                kw["max_consecutive_errors"] = v
            if "kill_switch" in patch:
                kw["kill_switch"] = bool(patch["kill_switch"])
            if "recv_window_ms" in patch:
                v = int(patch["recv_window_ms"])
                kw["recv_window_ms"] = max(1_000, v)
            if "events_log_path" in patch:
                p = str(patch["events_log_path"]).strip()
                if not p:
                    raise ValueError("events_log_path cannot be empty")
                kw["events_log_path"] = p
            if "api_key" in patch:
                kw["api_key"] = str(patch["api_key"]).strip()
            if "api_secret" in patch:
                kw["api_secret"] = str(patch["api_secret"]).strip()

            if not kw:
                return self.status()

            self._settings = replace(s, **kw)
            self._risk = RiskManager(
                max_orders_per_day=self._settings.max_orders_per_day,
                max_open_orders=self._settings.max_open_orders,
                max_consecutive_errors=self._settings.max_consecutive_errors,
            )
            self._state.mode = self._settings.mode
            self._state.symbol = self._settings.symbol
            if "kill_switch" in kw:
                self._state.kill_switch = bool(kw["kill_switch"])

        event = dict(kw)
        if "api_key" in event:
            event["api_key"] = "***" if event["api_key"] else ""
        if "api_secret" in event:
            event["api_secret"] = "***" if event["api_secret"] else ""
        self._append_event({"type": "settings_updated", "patch": event})
        return self.status()

    def read_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        n = max(1, min(1000, int(limit)))
        path = self._events_path()
        if not path.is_file():
            return []
        out: deque[dict[str, Any]] = deque(maxlen=n)
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        out.append(payload)
        except OSError:
            return []
        return list(out)

    def set_kill_switch(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self._state.kill_switch = bool(enabled)
        self._append_event({"type": "kill_switch", "enabled": bool(enabled)})
        return self.status()

    def reconcile(self) -> dict[str, Any]:
        """Reconcile in-memory state with exchange positions (live mode only).

        For paper mode: no-op (no real positions on exchange).
        For live mode: queries open orders via private client and compares
        with the engine's tracked state.
        """
        if self._settings.mode != "live":
            return {"ok": True, "mode": "paper", "message": "Reconciliation skipped (paper mode)"}

        if self._client is None or not self._client.has_credentials():
            return {"ok": False, "error": "No private client or credentials configured"}

        from mexc_monitor.reconciliation import (
            ExpectedPosition,
            ActualPosition,
            reconcile_positions,
        )

        try:
            raw_orders = self._client.get_open_orders(self._settings.symbol)
        except Exception as e:
            self._append_event({"type": "reconciliation_error", "error": str(e)})
            return {"ok": False, "error": str(e)}

        # Build expected positions from engine state
        expected: list[ExpectedPosition] = []
        with self._lock:
            if self._state.open_orders > 0 and self._state.last_order_client_id:
                expected.append(ExpectedPosition(
                    symbol=self._settings.symbol,
                    qty=0.0,  # We don't track exact qty in TradingState
                    side=self._settings.order_side.lower(),
                    exchange=self._exchange.value,
                    engine_name="trading",
                ))

        # Build actual positions from exchange response
        actual: list[ActualPosition] = []
        for order in raw_orders:
            if not isinstance(order, dict):
                continue
            sym = str(order.get("symbol", "")).upper()
            side = str(order.get("side", "")).lower()
            qty = float(order.get("origQty", order.get("quantity", 0)))
            if sym and qty > 0:
                actual.append(ActualPosition(
                    symbol=sym, qty=qty, side=side,
                    exchange=self._exchange.value,
                ))

        result = reconcile_positions(expected, actual)
        if result.has_issues:
            for d in result.discrepancies:
                self._append_event({
                    "type": "reconciliation_discrepancy",
                    "discrepancy_type": d.type,
                    "symbol": d.symbol,
                    "message": d.message,
                })

        return {
            "ok": True,
            "all_clear": result.all_clear,
            "matched": len(result.matched),
            "discrepancies": len(result.discrepancies),
            "discrepancy_details": [
                {"type": d.type, "symbol": d.symbol, "message": d.message}
                for d in result.discrepancies
            ],
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._state.running:
                already_running = True
            else:
                already_running = False
                self._state.running = True
                self._state.stopped_at = None
                self._state.started_at = self._now_iso()
                self._state.last_error = None
                self._stop_event.clear()
                self._thread = threading.Thread(target=self._run_loop, daemon=True)
                self._thread.start()
        if already_running:
            return self.status()
        self._append_event({"type": "engine_started"})
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._state.running:
                was_running = False
                thread = None
            else:
                was_running = True
                self._state.running = False
                self._state.stopped_at = self._now_iso()
                self._stop_event.set()
                thread = self._thread
        if not was_running:
            return self.status()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._append_event({"type": "engine_stopped"})
        return self.status()

    def run_once(self) -> dict[str, Any]:
        self._step()
        return self.status()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            self._step()
            spent = time.monotonic() - started
            sleep_for = max(0.0, self._settings.loop_interval_sec - spent)
            self._stop_event.wait(timeout=sleep_for)

    def _step(self) -> None:
        with self._lock:
            self._state.loop_count += 1
            kill_switch = self._state.kill_switch
        if kill_switch:
            return
        try:
            row = self._load_symbol_row()
            if row is None:
                return
            net = row.get("net_spread_bps")
            observed_at = row.get("observed_at")
            with self._lock:
                self._state.last_signal_net_spread_bps = net if isinstance(net, (int, float)) else None
                self._state.last_observed_at = str(observed_at) if observed_at else None
                self._state.signals_seen += 1
            if not isinstance(net, (int, float)):
                return
            if float(net) < self._settings.min_net_spread_bps:
                return
            self._place_order_from_row(row)
            with self._lock:
                self._state.consecutive_errors = 0
                self._state.last_error = None
        except RiskViolation as e:
            self._handle_error(f"RiskViolation: {e}")
        except PrivateApiError as e:
            self._handle_error(f"PrivateApiError: {e}")
        except Exception as e:
            self._handle_error(f"{type(e).__name__}: {e}")

    def _handle_error(self, message: str) -> None:
        with self._lock:
            self._state.last_error = message
            self._state.consecutive_errors += 1
            errors = self._state.consecutive_errors
            max_errors = self._settings.max_consecutive_errors
            if errors >= max_errors:
                self._state.kill_switch = True
        self._append_event({"type": "error", "message": message})

    def _load_symbol_row(self) -> dict[str, Any] | None:
        df, err = safe_load_snapshot(market="spot")
        if err:
            raise RuntimeError(err)
        if df is None or df.empty:
            return None
        if "symbol" not in df.columns:
            return None
        view = df[df["symbol"].astype(str).str.upper() == self._settings.symbol]
        if view.empty:
            return None
        records = json.loads(view.head(1).to_json(orient="records"))
        return records[0] if records else None

    def _build_client_order_id(self) -> str:
        self._seq += 1
        return f"sm-{int(time.time() * 1000)}-{self._seq}"

    def _order_price(self, bid: float, ask: float) -> float:
        px = bid
        if self._settings.limit_price_offset_bps != 0:
            px = px * (1.0 + self._settings.limit_price_offset_bps / 10_000.0)
        return max(0.0, px)

    def _order_price_sell(self, bid: float, ask: float) -> float:
        """Calculate limit price for SELL orders using ask as reference."""
        px = ask
        if self._settings.limit_price_offset_bps != 0:
            px = px * (1.0 + self._settings.limit_price_offset_bps / 10_000.0)
        return max(0.0, px)

    def _place_order_from_row(self, row: dict[str, Any]) -> None:
        bid = float(row.get("bid") or 0.0)
        ask = float(row.get("ask") or 0.0)
        if bid <= 0 or ask <= 0:
            raise RuntimeError("invalid top of book in snapshot")

        order_type = OrderType(self._settings.order_type)
        order_side = OrderSide(self._settings.order_side)

        # Price logic: MARKET orders have no price, LIMIT uses bid/ask + offset
        if order_type == OrderType.MARKET:
            price: float | None = None
            # For quantity calculation, use ask for BUY, bid for SELL
            ref_price = ask if order_side == OrderSide.BUY else bid
        else:
            # LIMIT: apply offset to reference price
            if order_side == OrderSide.BUY:
                price = self._order_price(bid, ask)
            else:
                # For SELL limit, use ask as reference with offset
                price = self._order_price_sell(bid, ask)
            ref_price = price

        qty = self._settings.order_quote_notional / ref_price if ref_price and ref_price > 0 else 0.0
        if qty <= 0:
            raise RiskViolation("computed quantity is zero")
        client_order_id = self._build_client_order_id()

        with self._lock:
            open_orders = self._state.open_orders
            consecutive_errors = self._state.consecutive_errors
        self._risk.check(
            requested_quote_notional=self._settings.order_quote_notional,
            open_orders=open_orders,
            consecutive_errors=consecutive_errors,
        )

        if self._settings.mode == "paper":
            self._append_event(
                {
                    "type": "paper_order",
                    "client_order_id": client_order_id,
                    "side": order_side.value,
                    "order_type": order_type.value,
                    "symbol": self._settings.symbol,
                    "price": price,
                    "quantity": qty,
                    "source": "snapshot_net_spread",
                }
            )
            with self._lock:
                self._state.orders_submitted += 1
                self._state.last_order_client_id = client_order_id
            self._risk.mark_submitted_order()
            return

        # Live mode: use BasePrivateClient if provided, else legacy MexcPrivateClient
        if self._client is not None:
            # New multi-exchange path via BasePrivateClient
            if not self._client.has_credentials():
                raise PrivateApiError(
                    f"{self._exchange.value.upper()}_API_KEY/"
                    f"{self._exchange.value.upper()}_API_SECRET are required in live mode"
                )

            open_orders_remote = self._client.get_open_orders(
                symbol=self._settings.symbol
            )
            open_count = len(open_orders_remote)
            self._risk.check(
                requested_quote_notional=self._settings.order_quote_notional,
                open_orders=open_count,
                consecutive_errors=consecutive_errors,
            )

            request = OrderRequest(
                symbol=self._settings.symbol,
                side=order_side,
                order_type=order_type,
                quantity=qty,
                price=price,
                client_order_id=client_order_id,
            )
            response = self._client.place_order(request)

            with self._lock:
                self._state.orders_submitted += 1
                self._state.open_orders = open_count + 1
                self._state.last_order_client_id = client_order_id
            self._risk.mark_submitted_order()
            self._append_event(
                {
                    "type": "live_order",
                    "client_order_id": client_order_id,
                    "symbol": self._settings.symbol,
                    "side": order_side.value,
                    "order_type": order_type.value,
                    "price": price,
                    "quantity": qty,
                    "exchange": self._exchange.value,
                    "market": self._market.value,
                    "exchange_response": response.raw if response else {},
                }
            )
        else:
            # Legacy MEXC-only path (backward compatibility)
            if not self._settings.api_key or not self._settings.api_secret:
                raise PrivateApiError(
                    "MEXC_API_KEY/MEXC_API_SECRET are required in live mode"
                )

            with MexcPrivateClient(
                api_key=self._settings.api_key,
                api_secret=self._settings.api_secret,
                base_url=DEFAULT_SETTINGS.base_url,
                timeout_sec=DEFAULT_SETTINGS.timeout_sec,
                recv_window_ms=self._settings.recv_window_ms,
            ) as client:
                open_orders_remote = client.get_open_orders(
                    symbol=self._settings.symbol
                )
                open_count = len(open_orders_remote)
                self._risk.check(
                    requested_quote_notional=self._settings.order_quote_notional,
                    open_orders=open_count,
                    consecutive_errors=consecutive_errors,
                )

                if order_type == OrderType.MARKET:
                    # Use the new place_order interface for MARKET orders
                    request = OrderRequest(
                        symbol=self._settings.symbol,
                        side=order_side,
                        order_type=order_type,
                        quantity=qty,
                        price=None,
                        client_order_id=client_order_id,
                    )
                    resp = client.place_order(request)
                    response_raw = resp.raw
                else:
                    # Legacy LIMIT path
                    response_raw = client.place_limit_order(
                        symbol=self._settings.symbol,
                        side=order_side.value,
                        quantity=qty,
                        price=price,  # type: ignore[arg-type]
                        client_order_id=client_order_id,
                        time_in_force="GTC",
                    )

            with self._lock:
                self._state.orders_submitted += 1
                self._state.open_orders = open_count + 1
                self._state.last_order_client_id = client_order_id
            self._risk.mark_submitted_order()
            self._append_event(
                {
                    "type": "live_order",
                    "client_order_id": client_order_id,
                    "symbol": self._settings.symbol,
                    "side": order_side.value,
                    "order_type": order_type.value,
                    "price": price,
                    "quantity": qty,
                    "exchange_response": response_raw,
                }
            )
