from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone


class RiskViolation(RuntimeError):
    pass


@dataclass
class RiskManager:
    max_orders_per_day: int
    max_open_orders: int
    max_consecutive_errors: int

    def __post_init__(self) -> None:
        self._orders_today = 0
        self._day = self._utc_day()

    def _utc_day(self) -> date:
        return datetime.now(timezone.utc).date()

    def _rollover_day(self) -> None:
        today = self._utc_day()
        if today != self._day:
            self._day = today
            self._orders_today = 0

    def check(
        self,
        *,
        requested_quote_notional: float,
        open_orders: int,
        consecutive_errors: int,
    ) -> None:
        self._rollover_day()
        if requested_quote_notional <= 0:
            raise RiskViolation("requested quote notional must be > 0")
        if open_orders >= self.max_open_orders:
            raise RiskViolation(
                f"open orders limit reached: {open_orders} >= {self.max_open_orders}"
            )
        if self._orders_today >= self.max_orders_per_day:
            raise RiskViolation(
                f"daily order limit reached: {self._orders_today} >= {self.max_orders_per_day}"
            )
        if consecutive_errors >= self.max_consecutive_errors:
            raise RiskViolation(
                f"too many consecutive errors: {consecutive_errors} >= {self.max_consecutive_errors}"
            )

    def mark_submitted_order(self) -> None:
        self._rollover_day()
        self._orders_today += 1
