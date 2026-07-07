"""Position reconciliation between in-memory state and exchange.

Compares positions tracked by trading engines with actual exchange-reported
positions, detecting:

- **Missing on exchange** — engine thinks position exists, exchange doesn't.
  Caused by: order that filled/cancelled without engine notification, state
  file from a previous session, API error during order placement.

- **Unexpected on exchange** — position exists on exchange but engine doesn't
  know about it. Caused by: manual trades, orphaned orders from a crash,
  partial fills not tracked.

- **Quantity mismatch** — position exists on both sides but with different
  size. Caused by: partial fills, manual position adjustments.

Usage in live mode::

    result = reconcile_positions(
        expected=[ExpectedPosition(symbol="BTCUSDT", qty=0.5, side="buy", ...)],
        actual=[ActualPosition(symbol="BTCUSDT", qty=0.48, ...)],
    )
    if result.discrepancies:
        alert_service.send(...)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

ReconciliationSide = Literal["buy", "sell", "long", "short"]


@dataclass(frozen=True)
class ExpectedPosition:
    """A position as tracked by the engine in-memory."""

    symbol: str
    qty: float
    side: ReconciliationSide
    exchange: str = ""
    engine_name: str = ""


@dataclass(frozen=True)
class ActualPosition:
    """A position as reported by the exchange API."""

    symbol: str
    qty: float
    side: ReconciliationSide
    exchange: str = ""
    entry_price: float = 0.0


@dataclass
class Discrepancy:
    """A single discrepancy found during reconciliation."""

    type: Literal["missing_on_exchange", "unexpected_on_exchange", "qty_mismatch"]
    symbol: str
    expected_qty: float
    actual_qty: float
    side: str
    exchange: str
    engine_name: str
    message: str


@dataclass
class ReconciliationResult:
    """Result of position reconciliation."""

    matched: list[dict[str, object]] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    all_clear: bool = True

    @property
    def has_issues(self) -> bool:
        return len(self.discrepancies) > 0


def reconcile_positions(
    expected: list[ExpectedPosition],
    actual: list[ActualPosition],
    *,
    qty_tolerance: float = 0.0001,
) -> ReconciliationResult:
    """Compare expected (in-memory) vs actual (exchange) positions.

    Parameters
    ----------
    expected
        Positions tracked by the engine.
    actual
        Positions reported by the exchange.
    qty_tolerance
        Absolute quantity difference below which positions are considered
        matching (handles floating-point rounding).

    Returns
    -------
    ReconciliationResult
        Matched positions and any discrepancies.
    """
    result = ReconciliationResult()

    # Index actual positions by (symbol, side) for quick lookup
    actual_map: dict[tuple[str, str], ActualPosition] = {}
    for a in actual:
        key = (a.symbol.upper(), a.side.lower())
        actual_map[key] = a

    # Check expected positions
    for exp in expected:
        key = (exp.symbol.upper(), exp.side.lower())
        act = actual_map.get(key)

        if act is None:
            result.discrepancies.append(Discrepancy(
                type="missing_on_exchange",
                symbol=exp.symbol,
                expected_qty=exp.qty,
                actual_qty=0.0,
                side=exp.side,
                exchange=exp.exchange,
                engine_name=exp.engine_name,
                message=f"Engine {exp.engine_name} expects {exp.qty} {exp.symbol} ({exp.side}) on {exp.exchange}, but exchange has no such position",
            ))
            result.all_clear = False
        elif abs(act.qty - exp.qty) > qty_tolerance:
            result.discrepancies.append(Discrepancy(
                type="qty_mismatch",
                symbol=exp.symbol,
                expected_qty=exp.qty,
                actual_qty=act.qty,
                side=exp.side,
                exchange=exp.exchange,
                engine_name=exp.engine_name,
                message=f"Qty mismatch for {exp.symbol} ({exp.side}): expected {exp.qty}, actual {act.qty}",
            ))
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
    expected_keys = {(e.symbol.upper(), e.side.lower()) for e in expected}
    for act in actual:
        key = (act.symbol.upper(), act.side.lower())
        if key not in expected_keys:
            result.discrepancies.append(Discrepancy(
                type="unexpected_on_exchange",
                symbol=act.symbol,
                expected_qty=0.0,
                actual_qty=act.qty,
                side=act.side,
                exchange=act.exchange,
                engine_name="",
                message=f"Unexpected position on exchange: {act.qty} {act.symbol} ({act.side}) on {act.exchange}",
            ))
            result.all_clear = False

    return result
