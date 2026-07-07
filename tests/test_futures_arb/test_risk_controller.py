"""
Unit tests for RiskController.

Tests margin ratio checks, delta-neutrality checks,
total exposure enforcement, and kill switch functionality.
"""

import time

import pytest

from mexc_monitor.futures_arb.models import (
    FuturesArbPosition,
    FuturesArbSettings,
    RiskAlert,
)
from mexc_monitor.futures_arb.risk_controller import RiskController


def _make_position(
    symbol: str = "BTCUSDT",
    exchange_combo: str = "mexc_spot+mexc_futures",
    spot_entry_price: float = 50000.0,
    spot_qty: float = 0.02,
    futures_entry_price: float = 50100.0,
    futures_qty: float = 0.02,
    notional_usdt: float = 1000.0,
    margin_ratio: float = 1.0,
    **kwargs,
) -> FuturesArbPosition:
    """Helper to create a test position."""
    defaults = dict(
        id="test-pos-1",
        strategy="cash_and_carry",
        state="open",
        spot_side="buy",
        futures_side="short",
        futures_leverage=3,
        entry_basis_bps=20.0,
        open_time_ms=int(time.time() * 1000),
    )
    defaults.update(kwargs)
    return FuturesArbPosition(
        symbol=symbol,
        exchange_combo=exchange_combo,
        spot_entry_price=spot_entry_price,
        spot_qty=spot_qty,
        futures_entry_price=futures_entry_price,
        futures_qty=futures_qty,
        notional_usdt=notional_usdt,
        margin_ratio=margin_ratio,
        **defaults,
    )


def _make_settings(**overrides) -> FuturesArbSettings:
    """Helper to create test settings."""
    return FuturesArbSettings(**overrides)


class TestCheckPosition:
    """Tests for RiskController.check_position()."""

    def test_no_alerts_when_healthy(self):
        """No alerts when margin and delta are within thresholds."""
        rc = RiskController(_make_settings())
        pos = _make_position(margin_ratio=0.8)
        alerts = rc.check_position(pos)
        assert alerts == []

    def test_margin_warning_alert(self):
        """Warning alert when margin_ratio < margin_warning_threshold but >= critical."""
        rc = RiskController(_make_settings(margin_warning_threshold=0.5, margin_critical_threshold=0.3))
        pos = _make_position(margin_ratio=0.4)
        alerts = rc.check_position(pos)
        assert len(alerts) == 1
        assert alerts[0].level == "warning"
        assert alerts[0].alert_type == "margin_warning"
        assert alerts[0].symbol == "BTCUSDT"

    def test_margin_critical_alert(self):
        """Critical alert when margin_ratio < margin_critical_threshold."""
        rc = RiskController(_make_settings(margin_warning_threshold=0.5, margin_critical_threshold=0.3))
        pos = _make_position(margin_ratio=0.2)
        alerts = rc.check_position(pos)
        assert len(alerts) == 1
        assert alerts[0].level == "critical"
        assert alerts[0].alert_type == "margin_critical"

    def test_margin_critical_takes_precedence_over_warning(self):
        """When margin is below critical, only critical alert is generated (not both)."""
        rc = RiskController(_make_settings(margin_warning_threshold=0.5, margin_critical_threshold=0.3))
        pos = _make_position(margin_ratio=0.1)
        alerts = rc.check_position(pos)
        alert_types = [a.alert_type for a in alerts]
        assert "margin_critical" in alert_types
        assert "margin_warning" not in alert_types

    def test_delta_imbalance_warning(self):
        """Warning when delta imbalance exceeds max_delta_imbalance_percent."""
        rc = RiskController(
            _make_settings(max_delta_imbalance_percent=5.0, critical_delta_imbalance_percent=15.0)
        )
        # spot_notional = 50000 * 0.02 = 1000
        # futures_notional = 50000 * 0.0212 = 1060
        # avg = 1030, imbalance = 60/1030*100 ≈ 5.83% > 5%
        pos = _make_position(
            spot_entry_price=50000.0,
            spot_qty=0.02,
            futures_entry_price=50000.0,
            futures_qty=0.0212,
        )
        alerts = rc.check_position(pos)
        delta_alerts = [a for a in alerts if "delta" in a.alert_type]
        assert len(delta_alerts) == 1
        assert delta_alerts[0].level == "warning"
        assert delta_alerts[0].alert_type == "delta_imbalance"

    def test_delta_critical_alert(self):
        """Critical alert when delta imbalance exceeds critical threshold."""
        rc = RiskController(
            _make_settings(max_delta_imbalance_percent=5.0, critical_delta_imbalance_percent=15.0)
        )
        # spot_notional = 50000 * 0.02 = 1000
        # futures_notional = 50000 * 0.024 = 1200
        # avg = 1100, imbalance = 200/1100*100 ≈ 18.18% > 15%
        pos = _make_position(
            spot_entry_price=50000.0,
            spot_qty=0.02,
            futures_entry_price=50000.0,
            futures_qty=0.024,
        )
        alerts = rc.check_position(pos)
        delta_alerts = [a for a in alerts if "delta" in a.alert_type]
        assert len(delta_alerts) == 1
        assert delta_alerts[0].level == "critical"
        assert delta_alerts[0].alert_type == "delta_critical"

    def test_both_margin_and_delta_alerts(self):
        """Both margin and delta alerts can be generated simultaneously."""
        rc = RiskController(
            _make_settings(
                margin_warning_threshold=0.5,
                margin_critical_threshold=0.3,
                max_delta_imbalance_percent=5.0,
                critical_delta_imbalance_percent=15.0,
            )
        )
        pos = _make_position(
            margin_ratio=0.4,
            spot_entry_price=50000.0,
            spot_qty=0.02,
            futures_entry_price=50000.0,
            futures_qty=0.024,
        )
        alerts = rc.check_position(pos)
        alert_types = {a.alert_type for a in alerts}
        assert "margin_warning" in alert_types
        assert "delta_critical" in alert_types

    def test_no_delta_alert_when_balanced(self):
        """No delta alert when spot and futures notionals are equal."""
        rc = RiskController(_make_settings(max_delta_imbalance_percent=5.0))
        pos = _make_position(
            spot_entry_price=50000.0,
            spot_qty=0.02,
            futures_entry_price=50000.0,
            futures_qty=0.02,
        )
        alerts = rc.check_position(pos)
        delta_alerts = [a for a in alerts if "delta" in a.alert_type]
        assert delta_alerts == []

    def test_margin_at_exact_threshold_no_alert(self):
        """No alert when margin_ratio equals the warning threshold exactly."""
        rc = RiskController(_make_settings(margin_warning_threshold=0.5, margin_critical_threshold=0.3))
        pos = _make_position(margin_ratio=0.5)
        alerts = rc.check_position(pos)
        margin_alerts = [a for a in alerts if "margin" in a.alert_type]
        assert margin_alerts == []


class TestCheckTotalExposure:
    """Tests for RiskController.check_total_exposure()."""

    def test_no_alert_within_limit(self):
        """No alert when total exposure is within limit."""
        rc = RiskController(_make_settings(max_total_exposure_usdt=10000.0, max_per_symbol_notional_usdt=10000.0))
        positions = [
            _make_position(notional_usdt=3000.0, id="p1"),
            _make_position(notional_usdt=3000.0, id="p2"),
        ]
        alerts = rc.check_total_exposure(positions)
        assert alerts == []

    def test_alert_when_exceeds_limit(self):
        """Critical alert when total exposure exceeds max_total_exposure_usdt."""
        rc = RiskController(_make_settings(max_total_exposure_usdt=5000.0, max_per_symbol_notional_usdt=10000.0))
        positions = [
            _make_position(notional_usdt=3000.0, id="p1"),
            _make_position(notional_usdt=3000.0, id="p2"),
        ]
        alerts = rc.check_total_exposure(positions)
        assert len(alerts) == 1
        assert alerts[0].level == "critical"
        assert alerts[0].alert_type == "exposure_exceeded"

    def test_empty_positions_no_alert(self):
        """No alert with empty position list."""
        rc = RiskController(_make_settings(max_total_exposure_usdt=10000.0))
        alerts = rc.check_total_exposure([])
        assert alerts == []

    def test_at_exact_limit_no_alert(self):
        """No alert when total exposure equals the limit exactly."""
        rc = RiskController(_make_settings(max_total_exposure_usdt=5000.0, max_per_symbol_notional_usdt=10000.0))
        positions = [
            _make_position(notional_usdt=2500.0, id="p1"),
            _make_position(notional_usdt=2500.0, id="p2"),
        ]
        alerts = rc.check_total_exposure(positions)
        assert alerts == []


class TestKillSwitch:
    """Tests for kill switch functionality."""

    def test_initially_inactive(self):
        """Kill switch is inactive by default."""
        rc = RiskController(_make_settings(kill_switch=False))
        assert rc.is_kill_switch_active() is False

    def test_initially_active_from_settings(self):
        """Kill switch can be active from settings."""
        rc = RiskController(_make_settings(kill_switch=True))
        assert rc.is_kill_switch_active() is True

    def test_activate(self):
        """Activating kill switch sets it to active."""
        rc = RiskController(_make_settings(kill_switch=False))
        rc.activate_kill_switch()
        assert rc.is_kill_switch_active() is True

    def test_deactivate(self):
        """Deactivating kill switch sets it to inactive."""
        rc = RiskController(_make_settings(kill_switch=True))
        rc.deactivate_kill_switch()
        assert rc.is_kill_switch_active() is False

    def test_activate_idempotent(self):
        """Activating an already active kill switch is a no-op."""
        rc = RiskController(_make_settings(kill_switch=True))
        rc.activate_kill_switch()
        assert rc.is_kill_switch_active() is True

    def test_deactivate_idempotent(self):
        """Deactivating an already inactive kill switch is a no-op."""
        rc = RiskController(_make_settings(kill_switch=False))
        rc.deactivate_kill_switch()
        assert rc.is_kill_switch_active() is False


class TestCheckTotalExposurePerSymbol:
    """Tests for per-symbol exposure checks in check_total_exposure()."""

    def test_per_symbol_alert_when_exceeds_limit(self):
        """Critical alert when a single symbol's notional exceeds max_per_symbol_notional_usdt."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=20000.0,
                max_per_symbol_notional_usdt=3000.0,
            )
        )
        positions = [
            _make_position(symbol="BTCUSDT", notional_usdt=2000.0, id="p1"),
            _make_position(symbol="BTCUSDT", notional_usdt=2000.0, id="p2"),
        ]
        alerts = rc.check_total_exposure(positions)
        per_symbol_alerts = [a for a in alerts if a.alert_type == "per_symbol_exposure_exceeded"]
        assert len(per_symbol_alerts) == 1
        assert per_symbol_alerts[0].symbol == "BTCUSDT"

    def test_per_symbol_no_alert_within_limit(self):
        """No per-symbol alert when each symbol is within its limit."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=20000.0,
                max_per_symbol_notional_usdt=3000.0,
            )
        )
        positions = [
            _make_position(symbol="BTCUSDT", notional_usdt=1500.0, id="p1"),
            _make_position(symbol="ETHUSDT", notional_usdt=1500.0, id="p2"),
        ]
        alerts = rc.check_total_exposure(positions)
        per_symbol_alerts = [a for a in alerts if a.alert_type == "per_symbol_exposure_exceeded"]
        assert per_symbol_alerts == []

    def test_multiple_symbols_one_exceeds(self):
        """Only the symbol exceeding the limit gets an alert."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=20000.0,
                max_per_symbol_notional_usdt=3000.0,
            )
        )
        positions = [
            _make_position(symbol="BTCUSDT", notional_usdt=2000.0, id="p1"),
            _make_position(symbol="BTCUSDT", notional_usdt=2000.0, id="p2"),
            _make_position(symbol="ETHUSDT", notional_usdt=1000.0, id="p3"),
        ]
        alerts = rc.check_total_exposure(positions)
        per_symbol_alerts = [a for a in alerts if a.alert_type == "per_symbol_exposure_exceeded"]
        assert len(per_symbol_alerts) == 1
        assert per_symbol_alerts[0].symbol == "BTCUSDT"


class TestCanOpenPosition:
    """Tests for RiskController.can_open_position()."""

    def test_allowed_when_all_conditions_met(self):
        """Position allowed when all limits are within bounds."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=10000.0,
                max_per_symbol_notional_usdt=3000.0,
                max_concurrent_positions=5,
                kill_switch=False,
            )
        )
        positions = [_make_position(symbol="BTCUSDT", notional_usdt=1000.0, id="p1")]
        allowed, reason = rc.can_open_position(positions, 1000.0, "BTCUSDT")
        assert allowed is True
        assert reason == ""

    def test_rejected_when_kill_switch_active(self):
        """Position rejected when kill switch is active."""
        rc = RiskController(_make_settings(kill_switch=True))
        allowed, reason = rc.can_open_position([], 1000.0, "BTCUSDT")
        assert allowed is False
        assert "Kill switch" in reason

    def test_rejected_when_total_exposure_exceeded(self):
        """Position rejected when total exposure would exceed limit."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=5000.0,
                max_per_symbol_notional_usdt=10000.0,
                max_concurrent_positions=10,
            )
        )
        positions = [_make_position(notional_usdt=4500.0, id="p1")]
        allowed, reason = rc.can_open_position(positions, 1000.0, "ETHUSDT")
        assert allowed is False
        assert "Total exposure" in reason

    def test_rejected_when_per_symbol_exposure_exceeded(self):
        """Position rejected when per-symbol exposure would exceed limit."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=20000.0,
                max_per_symbol_notional_usdt=3000.0,
                max_concurrent_positions=10,
            )
        )
        positions = [_make_position(symbol="BTCUSDT", notional_usdt=2500.0, id="p1")]
        allowed, reason = rc.can_open_position(positions, 1000.0, "BTCUSDT")
        assert allowed is False
        assert "Per-symbol" in reason

    def test_rejected_when_max_positions_reached(self):
        """Position rejected when max concurrent positions reached."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=50000.0,
                max_per_symbol_notional_usdt=50000.0,
                max_concurrent_positions=2,
            )
        )
        positions = [
            _make_position(notional_usdt=1000.0, id="p1"),
            _make_position(notional_usdt=1000.0, id="p2"),
        ]
        allowed, reason = rc.can_open_position(positions, 1000.0, "ETHUSDT")
        assert allowed is False
        assert "Position count" in reason

    def test_allowed_with_empty_positions(self):
        """Position allowed when no existing positions."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=10000.0,
                max_per_symbol_notional_usdt=3000.0,
                max_concurrent_positions=5,
                kill_switch=False,
            )
        )
        allowed, reason = rc.can_open_position([], 1000.0, "BTCUSDT")
        assert allowed is True
        assert reason == ""

    def test_per_symbol_check_only_counts_same_symbol(self):
        """Per-symbol check only considers positions with the same symbol."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=20000.0,
                max_per_symbol_notional_usdt=3000.0,
                max_concurrent_positions=10,
            )
        )
        positions = [
            _make_position(symbol="ETHUSDT", notional_usdt=2500.0, id="p1"),
        ]
        # Adding BTCUSDT should be fine even though ETHUSDT is near limit
        allowed, reason = rc.can_open_position(positions, 2500.0, "BTCUSDT")
        assert allowed is True
        assert reason == ""

    def test_kill_switch_checked_first(self):
        """Kill switch rejection takes priority over other checks."""
        rc = RiskController(
            _make_settings(
                max_total_exposure_usdt=100.0,  # Would also fail
                max_concurrent_positions=1,  # Would also fail
                kill_switch=True,
            )
        )
        positions = [_make_position(notional_usdt=200.0, id="p1")]
        allowed, reason = rc.can_open_position(positions, 1000.0, "BTCUSDT")
        assert allowed is False
        assert "Kill switch" in reason
