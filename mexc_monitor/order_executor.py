"""Order executor for live trading — manages real order lifecycle.

Wraps a :class:`BasePrivateClient` to provide a clean, exchange-agnostic
interface for placing orders, polling fill status, handling partial fills,
and managing timeouts/cancellations.

Lifecycle::

    place_limit_order() → OrderTicket(status="NEW")
         ↓ poll_status()
    status: NEW → PARTIALLY_FILLED → FILLED
                       ↘ CANCELED (timeout) → market close remaining

Integrates with ``spread_capture.py`` and ``arbitrage/engine.py`` to replace
the paper-mode :class:`ExecutionSimulator` when ``mode == "live"``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from mexc_monitor.trading.private_client_base import (
    BasePrivateClient,
    OrderRequest,
    OrderResponse,
    PrivateApiError,
)
from mexc_monitor.trading.exchanges import OrderSide, OrderType

logger = logging.getLogger(__name__)

OrderStatus = Literal["NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED", "REJECTED"]


@dataclass
class OrderTicket:
    """Tracks a single order through its lifecycle."""

    order_id: str
    client_order_id: str
    symbol: str
    side: str  # "BUY" / "SELL"
    order_type: str  # "LIMIT" / "MARKET"
    qty: float
    price: float | None
    status: OrderStatus = "NEW"
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    created_ms: int = 0
    updated_ms: int = 0
    exchange: str = ""
    # Raw response from exchange (for debugging)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_filled(self) -> bool:
        return self.status == "FILLED"

    @property
    def is_open(self) -> bool:
        return self.status in ("NEW", "PARTIALLY_FILLED")

    @property
    def is_closed(self) -> bool:
        return self.status in ("FILLED", "CANCELED", "EXPIRED", "REJECTED")

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.qty - self.filled_qty)


@dataclass
class FillResult:
    """Result of waiting for an order to fill."""

    filled: bool
    ticket: OrderTicket
    reason: str  # "filled", "timeout", "canceled", "rejected", "partial"
    elapsed_sec: float


class OrderExecutor:
    """Manages real order placement, polling, and cancellation.

    Thread-safe: uses a lock around client calls (private clients are
    generally not thread-safe).
    """

    def __init__(
        self,
        client: BasePrivateClient,
        *,
        poll_interval_sec: float = 1.0,
        fill_qty_tolerance: float = 0.0001,
    ) -> None:
        self._client = client
        self._poll_interval = max(0.1, poll_interval_sec)
        self._tolerance = fill_qty_tolerance
        self._lock = threading.Lock()
        self._seq = 0

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _gen_client_order_id(self, prefix: str = "sm") -> str:
        self._seq += 1
        return f"{prefix}{int(time.time())}{self._seq:04d}"

    # ─── Order placement ─────────────────────────────────────────────────────

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        *,
        client_order_id: str | None = None,
    ) -> OrderTicket | None:
        """Place a LIMIT order. Returns ticket or None on failure."""
        coid = client_order_id or self._gen_client_order_id()
        side_upper = side.upper()
        if side_upper not in ("BUY", "SELL"):
            logger.error("Invalid side: %s", side)
            return None

        req = OrderRequest(
            symbol=symbol,
            side=OrderSide.BUY if side_upper == "BUY" else OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=qty,
            price=price,
            client_order_id=coid,
        )

        try:
            with self._lock:
                resp: OrderResponse = self._client.place_order(req)
        except PrivateApiError as e:
            logger.error("place_limit_order failed: %s (symbol=%s)", e, symbol)
            return None
        except Exception as e:
            logger.exception("Unexpected error placing order: %s", e)
            return None

        now = self._now_ms()
        return OrderTicket(
            order_id=resp.order_id,
            client_order_id=resp.client_order_id,
            symbol=symbol.upper(),
            side=side_upper,
            order_type="LIMIT",
            qty=qty,
            price=price,
            status="NEW",
            created_ms=now,
            updated_ms=now,
            raw=resp.raw if hasattr(resp, "raw") else {},
        )

    def place_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        *,
        client_order_id: str | None = None,
    ) -> OrderTicket | None:
        """Place a MARKET order. Returns ticket or None on failure."""
        coid = client_order_id or self._gen_client_order_id(prefix="sm-mkt")
        side_upper = side.upper()

        req = OrderRequest(
            symbol=symbol,
            side=OrderSide.BUY if side_upper == "BUY" else OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=qty,
            price=None,
            client_order_id=coid,
        )

        try:
            with self._lock:
                resp = self._client.place_order(req)
        except PrivateApiError as e:
            logger.error("place_market_order failed: %s", e)
            return None
        except Exception as e:
            logger.exception("Unexpected error placing market order: %s", e)
            return None

        now = self._now_ms()
        # Market orders typically fill immediately
        filled_qty = float(resp.raw.get("executedQty", resp.raw.get("filledQty", qty)))
        status: OrderStatus = "FILLED" if filled_qty >= qty - self._tolerance else "PARTIALLY_FILLED"

        return OrderTicket(
            order_id=resp.order_id,
            client_order_id=resp.client_order_id,
            symbol=symbol.upper(),
            side=side_upper,
            order_type="MARKET",
            qty=qty,
            price=None,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=float(resp.raw.get("avgPrice", resp.raw.get("price", 0))),
            created_ms=now,
            updated_ms=now,
            raw=resp.raw,
        )

    # ─── Status polling ──────────────────────────────────────────────────────

    def poll_status(self, ticket: OrderTicket) -> OrderTicket:
        """Poll current order status from exchange.

        Uses ``get_open_orders`` to check if the order is still open.
        If not found in open orders, assumes filled (best case for limit orders).
        """
        try:
            with self._lock:
                open_orders = self._client.get_open_orders(ticket.symbol)
        except PrivateApiError as e:
            logger.warning("poll_status get_open_orders failed: %s", e)
            return ticket
        except Exception as e:
            logger.warning("poll_status error: %s", e)
            return ticket

        now = self._now_ms()
        ticket.updated_ms = now

        # Search for our order in the open orders list
        found_open = False
        for order in open_orders:
            if not isinstance(order, dict):
                continue
            oid = str(order.get("orderId", ""))
            if oid == ticket.order_id or str(order.get("clientOrderId", "")) == ticket.client_order_id:
                found_open = True
                executed = _safe_float(order.get("executedQty", order.get("filledQty", 0)))
                ticket.filled_qty = executed
                if executed > self._tolerance and executed < ticket.qty - self._tolerance:
                    ticket.status = "PARTIALLY_FILLED"
                else:
                    ticket.status = "NEW"
                break

        if not found_open:
            # Order is no longer open — filled, canceled, or expired
            if ticket.filled_qty > 0 and ticket.filled_qty >= ticket.qty - self._tolerance:
                ticket.status = "FILLED"
            elif ticket.filled_qty > 0:
                # Partial fill then canceled/expired
                ticket.status = "CANCELED"
            else:
                # No fills recorded — could be filled (exchange didn't report executedQty
                # in openOrders) or canceled. Assume filled for optimistic behavior.
                ticket.status = "FILLED"
                ticket.filled_qty = ticket.qty

        return ticket

    # ─── Cancellation ────────────────────────────────────────────────────────

    def cancel(self, ticket: OrderTicket) -> bool:
        """Cancel an open order. Returns True on success."""
        if ticket.is_closed:
            return True

        try:
            with self._lock:
                self._client.cancel_order(
                    symbol=ticket.symbol,
                    order_id=ticket.order_id,
                    client_order_id=ticket.client_order_id,
                )
            ticket.status = "CANCELED"
            ticket.updated_ms = self._now_ms()
            logger.info("Order canceled: %s (%s)", ticket.order_id, ticket.symbol)
            return True
        except PrivateApiError as e:
            # Order may have already filled/canceled
            logger.warning("Cancel failed (may already be filled): %s", e)
            return False
        except Exception as e:
            logger.error("Cancel error: %s", e)
            return False

    def get_open_orders(self, symbol: str) -> list[OrderTicket]:
        """Get all open orders for a symbol.

        Returns
        -------
        list[OrderTicket]
            List of open orders (NEW and PARTIALLY_FILLED).
        """
        try:
            with self._lock:
                # Call client's get_open_orders method
                api_orders = self._client.get_open_orders(symbol)
            tickets: list[OrderTicket] = []

            for order in api_orders:
                tickets.append(OrderTicket(
                    order_id=order.get("orderId", ""),
                    client_order_id=order.get("clientOrderId", ""),
                    symbol=order.get("symbol", symbol),
                    side=order.get("side", "BUY"),
                    order_type=order.get("type", "LIMIT"),
                    qty=order.get("origQty", 0),
                    price=order.get("price"),
                    status=order.get("status", "NEW"),
                    filled_qty=order.get("executedQty", 0),
                    avg_fill_price=order.get("avgPrice", 0),
                    created_ms=order.get("time", 0) or self._now_ms(),
                    updated_ms=self._now_ms(),
                    exchange=self._client.__class__.__name__,
                    raw=order,
                ))
            return tickets
        except PrivateApiError as e:
            logger.warning("Failed to get open orders: %s", e)
            return []
        except Exception as e:
            logger.error("Error getting open orders: %s", e)
            return []

    # ─── Wait for fill ───────────────────────────────────────────────────────

    def wait_for_fill(
        self,
        ticket: OrderTicket,
        timeout_sec: float,
    ) -> FillResult:
        """Block until order is filled or timeout. Polls at poll_interval.

        For limit orders: if timeout, cancels remaining qty.
        For market orders: single poll (should be instant).
        """
        start = time.monotonic()

        while True:
            self.poll_status(ticket)
            elapsed = time.monotonic() - start

            if ticket.is_filled:
                return FillResult(filled=True, ticket=ticket, reason="filled", elapsed_sec=elapsed)

            if ticket.status in ("CANCELED", "EXPIRED", "REJECTED"):
                reason = "partial" if ticket.filled_qty > 0 else ticket.status.lower()
                return FillResult(filled=False, ticket=ticket, reason=reason, elapsed_sec=elapsed)

            if elapsed >= timeout_sec:
                # Timeout — cancel remaining
                if ticket.order_type == "LIMIT":
                    self.cancel(ticket)
                    # Final poll to get fill status after cancel
                    self.poll_status(ticket)
                reason = "partial" if ticket.filled_qty > 0 else "timeout"
                return FillResult(filled=False, ticket=ticket, reason=reason, elapsed_sec=elapsed)

            time.sleep(self._poll_interval)


def _safe_float(v: Any) -> float:
    """Parse value to float, returning 0.0 on failure."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
