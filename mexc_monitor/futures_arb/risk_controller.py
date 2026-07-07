"""
Risk Controller — контроль рисков арбитражных позиций.

Проверки:
- Margin ratio фьючерсной ноги (warning at 50%, critical at 30%)
- Дельта-нейтральность (warning at 5%, critical at 15%)
- Суммарный exposure (max_total_exposure_usdt)
- Per-symbol exposure (max_per_symbol_notional_usdt)
- Kill switch: закрытие всех позиций, блокировка новых
"""

from __future__ import annotations

import logging
import time
from typing import Any

from mexc_monitor.futures_arb.models import (
    FuturesArbPosition,
    FuturesArbSettings,
    RiskAlert,
)

logger = logging.getLogger(__name__)


class RiskController:
    """
    Контроль рисков арбитражных позиций.

    Проверяет margin ratio, дельта-нейтральность, exposure limits.
    Поддерживает kill switch для экстренного закрытия всех позиций.
    """

    def __init__(self, settings: FuturesArbSettings):
        self._settings = settings
        self._kill_switch = settings.kill_switch

    @property
    def settings(self) -> FuturesArbSettings:
        return self._settings

    @settings.setter
    def settings(self, value: FuturesArbSettings) -> None:
        self._settings = value

    # --- Kill switch ---

    def is_kill_switch_active(self) -> bool:
        """Check if kill switch is active."""
        return self._kill_switch

    def activate_kill_switch(self) -> None:
        """Activate kill switch — all positions should be closed."""
        self._kill_switch = True
        logger.warning("Kill switch ACTIVATED")

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch — allow new positions."""
        self._kill_switch = False
        logger.info("Kill switch deactivated")

    # --- Position risk checks ---

    def check_position(
        self,
        pos: FuturesArbPosition,
        *,
        current_spot_price: float = 0.0,
        current_futures_price: float = 0.0,
    ) -> list[RiskAlert]:
        """
        Check risk for a single position.

        Returns list of alerts (may be empty if all OK).

        When *current_spot_price* and *current_futures_price* are provided
        (> 0), delta imbalance and synthetic margin are computed from **live**
        market values. When 0, falls back to entry-price-based estimate.
        """
        alerts: list[RiskAlert] = []
        now_ms = int(time.time() * 1000)

        # Compute synthetic margin ratio from live prices (if available)
        if current_spot_price > 0 and current_futures_price > 0:
            synthetic_margin = self.compute_synthetic_margin_ratio(
                pos, current_spot_price, current_futures_price,
            )
            # Temporarily update pos for downstream checks
            pos.margin_ratio = synthetic_margin

        # 1. Margin ratio check
        margin_alerts = self._check_margin_ratio(pos, now_ms)
        alerts.extend(margin_alerts)

        # 2. Delta-neutrality check
        delta_alerts = self._check_delta_neutrality(
            pos, now_ms,
            current_spot_price=current_spot_price,
            current_futures_price=current_futures_price,
        )
        alerts.extend(delta_alerts)

        return alerts

    def check_total_exposure(
        self, positions: list[FuturesArbPosition]
    ) -> list[RiskAlert]:
        """
        Check total exposure limits.

        Returns alerts if total or per-symbol exposure exceeds limits.
        """
        alerts: list[RiskAlert] = []
        now_ms = int(time.time() * 1000)

        # Total exposure
        open_positions = [p for p in positions if p.state in ("pending_open", "open")]
        total_notional = sum(p.notional_usdt for p in open_positions)

        if total_notional > self._settings.max_total_exposure_usdt:
            alerts.append(RiskAlert(
                level="critical",
                alert_type="exposure_exceeded",
                symbol="ALL",
                message=(
                    f"Total exposure {total_notional:.2f} USDT exceeds "
                    f"max {self._settings.max_total_exposure_usdt:.2f} USDT"
                ),
                timestamp_ms=now_ms,
            ))

        # Per-symbol exposure
        symbol_exposure: dict[str, float] = {}
        for p in open_positions:
            symbol_exposure[p.symbol] = symbol_exposure.get(p.symbol, 0.0) + p.notional_usdt

        for symbol, exposure in symbol_exposure.items():
            if exposure > self._settings.max_per_symbol_notional_usdt:
                alerts.append(RiskAlert(
                    level="warning",
                    alert_type="per_symbol_exposure_exceeded",
                    symbol=symbol,
                    message=(
                        f"Symbol {symbol} exposure {exposure:.2f} USDT exceeds "
                        f"max {self._settings.max_per_symbol_notional_usdt:.2f} USDT"
                    ),
                    timestamp_ms=now_ms,
                ))

        return alerts

    def can_open_position(
        self,
        positions: list[FuturesArbPosition],
        new_notional: float,
        symbol: str,
    ) -> tuple[bool, str]:
        """
        Check if a new position can be opened given current exposure.

        Returns (allowed, reason) tuple.
        """
        if self._kill_switch:
            return False, "Kill switch is active"

        open_positions = [p for p in positions if p.state in ("pending_open", "open")]

        # Max concurrent positions
        if len(open_positions) >= self._settings.max_concurrent_positions:
            return False, "Position count limit reached"

        # Total exposure
        total_notional = sum(p.notional_usdt for p in open_positions)
        if total_notional + new_notional > self._settings.max_total_exposure_usdt:
            return False, "Total exposure limit exceeded"

        # Per-symbol exposure
        symbol_notional = sum(
            p.notional_usdt for p in open_positions if p.symbol == symbol
        )
        if symbol_notional + new_notional > self._settings.max_per_symbol_notional_usdt:
            return False, "Per-symbol exposure limit exceeded"

        return True, ""

    def should_force_close(self, pos: FuturesArbPosition) -> tuple[bool, str]:
        """
        Check if a position should be force-closed due to critical risk.

        Returns (should_close, reason) tuple.
        """
        # Kill switch
        if self._kill_switch:
            return True, "kill_switch"

        # Margin critical
        if pos.margin_ratio < self._settings.margin_critical_threshold:
            return True, "margin_critical"

        # Delta critical
        delta_pct = self._compute_delta_imbalance(pos)
        if delta_pct > self._settings.critical_delta_imbalance_percent:
            return True, "delta_critical"

        return False, ""

    # --- Internal checks ---

    def _check_margin_ratio(
        self, pos: FuturesArbPosition, now_ms: int
    ) -> list[RiskAlert]:
        """Check margin ratio thresholds."""
        alerts: list[RiskAlert] = []

        if pos.margin_ratio < self._settings.margin_critical_threshold:
            alerts.append(RiskAlert(
                level="critical",
                alert_type="margin_critical",
                symbol=pos.symbol,
                message=(
                    f"Position {pos.id} margin ratio {pos.margin_ratio:.2%} "
                    f"below critical threshold {self._settings.margin_critical_threshold:.2%}"
                ),
                timestamp_ms=now_ms,
            ))
        elif pos.margin_ratio < self._settings.margin_warning_threshold:
            alerts.append(RiskAlert(
                level="warning",
                alert_type="margin_warning",
                symbol=pos.symbol,
                message=(
                    f"Position {pos.id} margin ratio {pos.margin_ratio:.2%} "
                    f"below warning threshold {self._settings.margin_warning_threshold:.2%}"
                ),
                timestamp_ms=now_ms,
            ))

        return alerts

    def _check_delta_neutrality(
        self,
        pos: FuturesArbPosition,
        now_ms: int,
        *,
        current_spot_price: float = 0.0,
        current_futures_price: float = 0.0,
    ) -> list[RiskAlert]:
        """Check delta-neutrality of a position."""
        alerts: list[RiskAlert] = []

        delta_pct = self._compute_delta_imbalance(
            pos,
            current_spot_price=current_spot_price,
            current_futures_price=current_futures_price,
        )

        if delta_pct > self._settings.critical_delta_imbalance_percent:
            alerts.append(RiskAlert(
                level="critical",
                alert_type="delta_critical",
                symbol=pos.symbol,
                message=(
                    f"Position {pos.id} delta imbalance {delta_pct:.1f}% "
                    f"exceeds critical threshold {self._settings.critical_delta_imbalance_percent:.1f}%"
                ),
                timestamp_ms=now_ms,
            ))
        elif delta_pct > self._settings.max_delta_imbalance_percent:
            alerts.append(RiskAlert(
                level="warning",
                alert_type="delta_imbalance",
                symbol=pos.symbol,
                message=(
                    f"Position {pos.id} delta imbalance {delta_pct:.1f}% "
                    f"exceeds warning threshold {self._settings.max_delta_imbalance_percent:.1f}%"
                ),
                timestamp_ms=now_ms,
            ))

        return alerts

    @staticmethod
    def _compute_delta_imbalance(
        pos: FuturesArbPosition,
        *,
        current_spot_price: float = 0.0,
        current_futures_price: float = 0.0,
    ) -> float:
        """
        Compute delta imbalance as percentage.

        Uses **current** market prices when available to reflect real-time
        hedging quality. Falls back to entry prices when current prices
        are not provided.

        Formula::

            spot_value  = spot_qty  × current_spot
            fut_value   = fut_qty   × current_fut
            imbalance = |spot_value - fut_value| / avg(spot_value, fut_value) × 100
        """
        spot_price = (
            current_spot_price
            if current_spot_price > 0
            else pos.spot_entry_price
        )
        fut_price = (
            current_futures_price
            if current_futures_price > 0
            else pos.futures_entry_price
        )

        spot_value = pos.spot_qty * spot_price
        fut_value = pos.futures_qty * fut_price

        avg = (spot_value + fut_value) / 2.0
        if avg <= 0:
            return 0.0

        return abs(spot_value - fut_value) / avg * 100.0

    @staticmethod
    def compute_synthetic_margin_ratio(
        pos: FuturesArbPosition,
        current_spot_price: float = 0.0,
        current_futures_price: float = 0.0,
    ) -> float:
        """
        Compute synthetic margin ratio for paper-mode risk checks.

        .. math::
            \\text{initial\\_margin} = \\frac{\\text{notional}}{\\text{leverage}}

            \\text{equity} = \\text{initial\\_margin} + \\text{unrealized\\_pnl}

            \\text{margin\\_ratio} = \\frac{\\text{equity}}{\\text{initial\\_margin}}

        Returns 1.0 at entry (no PNL change). Decreases toward 0 as the
        position loses money. Below :attr:`margin_critical_threshold`
        (default 0.3) → force-close.
        """
        leverage = max(1, pos.futures_leverage)
        initial_margin = pos.notional_usdt / leverage
        if initial_margin <= 0:
            return 1.0

        spot_price = (
            current_spot_price
            if current_spot_price > 0
            else pos.spot_entry_price
        )
        fut_price = (
            current_futures_price
            if current_futures_price > 0
            else pos.futures_entry_price
        )

        # Unrealized PNL from both legs
        if pos.futures_side == "short":
            fut_pnl = (pos.futures_entry_price - fut_price) * pos.futures_qty
        else:
            fut_pnl = (fut_price - pos.futures_entry_price) * pos.futures_qty

        if pos.spot_side == "buy":
            spot_pnl = (spot_price - pos.spot_entry_price) * pos.spot_qty
        else:
            spot_pnl = (pos.spot_entry_price - spot_price) * pos.spot_qty

        unrealized = fut_pnl + spot_pnl + pos.cumulative_funding

        equity = initial_margin + unrealized
        ratio = equity / initial_margin
        return max(0.0, ratio)
