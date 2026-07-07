"""Tests for RiskController delta imbalance and synthetic margin ratio."""

from __future__ import annotations

import pytest

from mexc_monitor.futures_arb.models import FuturesArbPosition, FuturesArbSettings
from mexc_monitor.futures_arb.risk_controller import RiskController


def _make_cc_pos(
    spot_entry=100.0, fut_entry=100.5, leverage=3, notional=100.0,
    basis_bps=50.0,
) -> FuturesArbPosition:
    """C&C position: buy spot + short futures."""
    return FuturesArbPosition(
        id="cc1", symbol="BTC", exchange_combo="mexc_spot+mexc_futures",
        strategy="cash_and_carry", state="open",
        spot_side="buy", spot_entry_price=spot_entry, spot_qty=notional / spot_entry,
        futures_side="short", futures_entry_price=fut_entry, futures_qty=notional / fut_entry,
        futures_leverage=leverage, notional_usdt=notional,
        entry_basis_bps=basis_bps, open_time_ms=0,
    )


def _make_rcc_pos(
    spot_entry=100.0, fut_entry=99.5, leverage=3, notional=100.0,
    basis_bps=-50.0,
) -> FuturesArbPosition:
    """RCC position: sell spot + long futures."""
    return FuturesArbPosition(
        id="rcc1", symbol="BTC", exchange_combo="mexc_spot+mexc_futures",
        strategy="reverse_cash_and_carry", state="open",
        spot_side="sell", spot_entry_price=spot_entry, spot_qty=notional / spot_entry,
        futures_side="long", futures_entry_price=fut_entry, futures_qty=notional / fut_entry,
        futures_leverage=leverage, notional_usdt=notional,
        entry_basis_bps=basis_bps, open_time_ms=0,
    )


# ─── Delta imbalance ──────────────────────────────────────────────────────────


class TestDeltaImbalance:
    def test_entry_prices_low_imbalance(self):
        """At entry, both legs sized to same notional → small imbalance."""
        rc = RiskController(FuturesArbSettings())
        pos = _make_cc_pos()
        delta = rc._compute_delta_imbalance(pos)
        assert delta < 1.0  # < 1% due to slight price diff (100 vs 100.5)

    def test_live_prices_show_real_imbalance(self):
        """After prices diverge, delta imbalance should be significant."""
        rc = RiskController(FuturesArbSettings())
        pos = _make_cc_pos()
        # Spot up 10%, futures down 5%
        delta = rc._compute_delta_imbalance(
            pos, current_spot_price=110.0, current_futures_price=95.0,
        )
        assert delta > 10.0  # > 10% imbalance

    def test_balanced_prices_zero_imbalance(self):
        """If both legs move proportionally, imbalance stays low."""
        rc = RiskController(FuturesArbSettings())
        pos = _make_cc_pos(spot_entry=100, fut_entry=100)
        # Both up 10%
        delta = rc._compute_delta_imbalance(
            pos, current_spot_price=110.0, current_futures_price=110.0,
        )
        assert delta < 0.1

    def test_fallback_to_entry_prices(self):
        """When current prices not provided, uses entry prices."""
        rc = RiskController(FuturesArbSettings())
        pos = _make_cc_pos()
        delta_no_live = rc._compute_delta_imbalance(pos)
        delta_entry = rc._compute_delta_imbalance(
            pos,
            current_spot_price=pos.spot_entry_price,
            current_futures_price=pos.futures_entry_price,
        )
        assert delta_no_live == pytest.approx(delta_entry)


# ─── Synthetic margin ratio ───────────────────────────────────────────────────


class TestSyntheticMarginRatio:
    def test_at_entry_returns_one(self):
        """No PNL change at entry → ratio = 1.0."""
        rc = RiskController(FuturesArbSettings())
        pos = _make_cc_pos()
        ratio = rc.compute_synthetic_margin_ratio(
            pos,
            current_spot_price=pos.spot_entry_price,
            current_futures_price=pos.futures_entry_price,
        )
        assert ratio == pytest.approx(1.0)

    def test_cc_loses_when_basis_widens(self):
        """C&C with basis widening → margin ratio drops below 1."""
        rc = RiskController(FuturesArbSettings())
        pos = _make_cc_pos(spot_entry=100, fut_entry=102)  # basis +200 bps
        # Basis widens significantly: spot drops 10%, futures rise 8%
        ratio = rc.compute_synthetic_margin_ratio(
            pos, current_spot_price=90.0, current_futures_price=110.0,
        )
        assert ratio < 0.5  # Significant loss (over 50% of margin consumed)

    def test_cc_gains_when_basis_narrows(self):
        """C&C with basis converging → margin ratio above 1."""
        rc = RiskController(FuturesArbSettings())
        pos = _make_cc_pos(spot_entry=100, fut_entry=102)
        # Basis narrows toward zero
        ratio = rc.compute_synthetic_margin_ratio(
            pos, current_spot_price=101.0, current_futures_price=101.5,
        )
        assert ratio > 1.0  # Profit

    def test_higher_leverage_lower_margin_buffer(self):
        """Higher leverage → less margin buffer → ratio drops faster."""
        rc = RiskController(FuturesArbSettings())
        # Leverage 3x
        pos_3x = _make_cc_pos(leverage=3)
        # Leverage 10x
        pos_10x = _make_cc_pos(leverage=10)
        # Both lose 5 USDT unrealized
        for pos in (pos_3x, pos_10x):
            pass  # positions are set up
        ratio_3x = rc.compute_synthetic_margin_ratio(
            pos_3x, current_spot_price=97.0, current_futures_price=102.0,
        )
        ratio_10x = rc.compute_synthetic_margin_ratio(
            pos_10x, current_spot_price=97.0, current_futures_price=102.0,
        )
        # Higher leverage has less buffer, so ratio drops more
        assert ratio_10x < ratio_3x

    def test_margin_ratio_never_negative(self):
        """Even with catastrophic loss, ratio clamps to 0."""
        rc = RiskController(FuturesArbSettings())
        pos = _make_cc_pos(leverage=3)
        ratio = rc.compute_synthetic_margin_ratio(
            pos, current_spot_price=10.0, current_futures_price=200.0,
        )
        assert ratio >= 0.0


# ─── check_position integration ───────────────────────────────────────────────


class TestCheckPositionIntegration:
    def test_check_position_with_live_prices_triggers_margin_alert(self):
        """Large adverse move → margin critical alert."""
        s = FuturesArbSettings(
            margin_warning_threshold=0.5,
            margin_critical_threshold=0.3,
            max_delta_imbalance_percent=5.0,
            critical_delta_imbalance_percent=15.0,
        )
        rc = RiskController(s)
        pos = _make_cc_pos(leverage=3)
        # Catastrophic basis widening
        alerts = rc.check_position(
            pos, current_spot_price=80.0, current_futures_price=120.0,
        )
        alert_types = [a.alert_type for a in alerts]
        assert "margin_critical" in alert_types or "margin_warning" in alert_types

    def test_check_position_no_alerts_when_balanced(self):
        """Small price move → no alerts."""
        s = FuturesArbSettings()
        rc = RiskController(s)
        pos = _make_cc_pos()
        alerts = rc.check_position(
            pos,
            current_spot_price=pos.spot_entry_price * 1.001,
            current_futures_price=pos.futures_entry_price * 1.001,
        )
        assert len(alerts) == 0
