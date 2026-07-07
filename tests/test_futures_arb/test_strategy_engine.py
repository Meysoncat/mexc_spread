"""
Unit tests for FuturesArbStrategyEngine.

Tests cover:
- Lifecycle: start/stop/get_status
- Settings update at runtime
- Entry decision logic for cash-and-carry, reverse cash-and-carry, funding arb
- Best exchange_combo selection (highest executable basis)
- Position limit enforcement (max_concurrent, per-symbol, total exposure)
- Kill switch blocks entries
- Stale pairs are skipped
- Paper/live modes use identical decision logic
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from mexc_monitor.futures_arb.models import (
    BasisSnapshot,
    FundingInfo,
    FuturesArbPosition,
    FuturesArbSettings,
)
from mexc_monitor.futures_arb.strategy_engine import (
    FuturesArbStrategyEngine,
    _futures_exchange_from_combo,
    _spot_exchange_from_combo,
)


# --- Helpers ---


def _make_settings(**overrides) -> FuturesArbSettings:
    """Create settings with sensible test defaults."""
    defaults = {
        "enabled": True,
        "mode": "paper",
        "symbols": ["BTCUSDT"],
        "exchange_combos": ["mexc_spot+mexc_futures"],
        "entry_threshold_bps": 30.0,
        "exit_threshold_bps": 5.0,
        "position_notional_usdt": 1000.0,
        "max_concurrent_positions": 5,
        "max_per_symbol_notional_usdt": 3000.0,
        "max_total_exposure_usdt": 10000.0,
        "futures_leverage": 3,
        "loop_interval_sec": 0.1,
        "funding_entry_threshold": 0.001,
        "spot_taker_fee_bps": 1.0,
        "futures_taker_fee_bps": 2.0,
    }
    defaults.update(overrides)
    return FuturesArbSettings(**defaults)


def _make_basis(
    symbol: str = "BTCUSDT",
    exchange_combo: str = "mexc_spot+mexc_futures",
    executable_cc_bps: float = 50.0,
    executable_rcc_bps: float = -10.0,
    basis_bps: float = 45.0,
    spot_mid: float = 50000.0,
    futures_mid: float = 50225.0,
    status: str = "active",
) -> BasisSnapshot:
    return BasisSnapshot(
        symbol=symbol,
        exchange_combo=exchange_combo,
        spot_mid=spot_mid,
        futures_mid=futures_mid,
        basis_abs=futures_mid - spot_mid,
        basis_bps=basis_bps,
        executable_basis_cc_bps=executable_cc_bps,
        executable_basis_rcc_bps=executable_rcc_bps,
        estimated_apy=0.0,
        funding_rate=None,
        status=status,
        timestamp_ms=int(time.time() * 1000),
    )


def _make_funding(
    symbol: str = "BTCUSDT",
    exchange: str = "mexc_futures",
    current_rate: float = 0.002,
    avg_7d: float = 0.0015,
    z_score: float = 2.5,
) -> FundingInfo:
    return FundingInfo(
        symbol=symbol,
        exchange=exchange,
        current_rate=current_rate,
        next_funding_time_ms=int(time.time() * 1000) + 3600_000,
        avg_7d=avg_7d,
        avg_30d=0.001,
        annualized_yield=21.9,
        direction_changed=False,
        std_30d=0.0004,
        z_score=z_score,
    )


def _make_position(
    symbol: str = "BTCUSDT",
    exchange_combo: str = "mexc_spot+mexc_futures",
    notional: float = 1000.0,
    strategy: str = "cash_and_carry",
) -> FuturesArbPosition:
    return FuturesArbPosition(
        id="test-pos-1",
        symbol=symbol,
        exchange_combo=exchange_combo,
        strategy=strategy,
        state="open",
        spot_side="buy",
        spot_entry_price=50000.0,
        spot_qty=0.02,
        futures_side="short",
        futures_entry_price=50225.0,
        futures_qty=0.02,
        futures_leverage=3,
        notional_usdt=notional,
        entry_basis_bps=45.0,
        open_time_ms=int(time.time() * 1000) - 60000,
    )


def _create_engine(
    settings: FuturesArbSettings | None = None,
    basis_snapshots: list[BasisSnapshot] | None = None,
    funding_infos: list[FundingInfo] | None = None,
    open_positions: list[FuturesArbPosition] | None = None,
    kill_switch: bool = False,
) -> tuple[FuturesArbStrategyEngine, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Create engine with mocked dependencies."""
    if settings is None:
        settings = _make_settings()

    basis_calc = MagicMock()
    funding_tracker = MagicMock()
    position_manager = MagicMock()
    risk_controller = MagicMock()

    # Configure basis calculator
    snapshots = basis_snapshots or []
    basis_calc.get_all_basis.return_value = snapshots
    basis_calc.get_current_basis.side_effect = lambda sym, combo: next(
        (s for s in snapshots if s.symbol == sym and s.exchange_combo == combo), None
    )

    # Configure funding tracker
    infos = funding_infos or []
    funding_tracker.get_all_funding.return_value = infos
    funding_tracker.get_funding.side_effect = lambda sym, exch: next(
        (f for f in infos if f.symbol == sym and f.exchange == exch), None
    )

    # Configure position manager
    positions = open_positions or []
    position_manager.get_open_positions.return_value = positions
    position_manager.open_position.return_value = None

    # Configure risk controller
    risk_controller.is_kill_switch_active.return_value = kill_switch

    engine = FuturesArbStrategyEngine(
        settings=settings,
        basis_calculator=basis_calc,
        funding_tracker=funding_tracker,
        position_manager=position_manager,
        risk_controller=risk_controller,
    )

    return engine, basis_calc, funding_tracker, position_manager, risk_controller


# --- Tests ---


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_futures_exchange_from_combo(self):
        assert _futures_exchange_from_combo("mexc_spot+mexc_futures") == "mexc_futures"
        assert _futures_exchange_from_combo("mexc_spot+asterdex_perp") == "asterdex_perp"
        assert _futures_exchange_from_combo("asterdex_perp+mexc_futures") == "mexc_futures"
        assert _futures_exchange_from_combo("unknown+combo") is None

    def test_spot_exchange_from_combo(self):
        assert _spot_exchange_from_combo("mexc_spot+mexc_futures") == "mexc_spot"
        assert _spot_exchange_from_combo("mexc_spot+asterdex_perp") == "mexc_spot"
        assert _spot_exchange_from_combo("asterdex_perp+mexc_futures") == "asterdex_perp"


class TestLifecycle:
    """Tests for engine lifecycle (start/stop/status)."""

    def test_start_and_stop(self):
        engine, *_ = _create_engine()
        status = engine.start()
        assert status["running"] is True

        status = engine.stop()
        assert status["running"] is False

    def test_start_idempotent(self):
        engine, *_ = _create_engine()
        engine.start()
        engine.start()  # Should not raise
        status = engine.get_status()
        assert status["running"] is True
        engine.stop()

    def test_stop_when_not_running(self):
        engine, *_ = _create_engine()
        status = engine.stop()
        assert status["running"] is False

    def test_get_status_includes_settings(self):
        settings = _make_settings(mode="paper", entry_threshold_bps=25.0)
        engine, *_ = _create_engine(settings=settings)
        status = engine.get_status()
        assert status["settings"]["mode"] == "paper"
        assert status["settings"]["entry_threshold_bps"] == 25.0

    def test_get_status_includes_positions(self):
        pos = _make_position()
        engine, *_ = _create_engine(open_positions=[pos])
        status = engine.get_status()
        assert status["open_count"] == 1
        assert len(status["open_positions"]) == 1

    def test_get_status_includes_basis(self):
        basis = _make_basis()
        engine, *_ = _create_engine(basis_snapshots=[basis])
        status = engine.get_status()
        assert len(status["current_basis"]) == 1


class TestUpdateSettings:
    """Tests for runtime settings update."""

    def test_update_mode(self):
        engine, *_ = _create_engine()
        result = engine.update_settings({"mode": "live"})
        assert result["settings"]["mode"] == "live"

    def test_update_entry_threshold(self):
        engine, *_ = _create_engine()
        result = engine.update_settings({"entry_threshold_bps": 50.0})
        assert result["settings"]["entry_threshold_bps"] == 50.0

    def test_update_symbols(self):
        engine, *_ = _create_engine()
        result = engine.update_settings({"symbols": ["ETHUSDT", "SOLUSDT"]})
        assert result["settings"]["symbols"] == ["ETHUSDT", "SOLUSDT"]

    def test_update_leverage_clamped(self):
        engine, *_ = _create_engine()
        result = engine.update_settings({"futures_leverage": 50})
        assert result["settings"]["futures_leverage"] == 20  # Clamped to max

    def test_update_does_not_affect_open_positions(self):
        pos = _make_position()
        engine, _, _, pm, _ = _create_engine(open_positions=[pos])
        engine.update_settings({"entry_threshold_bps": 100.0})
        # Position manager should not have been called to modify positions
        pm.close_position.assert_not_called()


class TestCashAndCarryEntry:
    """Tests for cash-and-carry entry logic."""

    def test_opens_when_threshold_met(self):
        basis = _make_basis(executable_cc_bps=50.0)
        settings = _make_settings(entry_threshold_bps=30.0)
        engine, _, _, pm, _ = _create_engine(settings=settings, basis_snapshots=[basis])

        engine._check_entry_opportunities()

        pm.open_position.assert_called_once()
        pos = pm.open_position.call_args[0][0]
        assert pos.strategy == "cash_and_carry"
        assert pos.spot_side == "buy"
        assert pos.futures_side == "short"
        assert pos.symbol == "BTCUSDT"

    def test_does_not_open_below_threshold(self):
        basis = _make_basis(executable_cc_bps=20.0)
        settings = _make_settings(entry_threshold_bps=30.0)
        engine, _, _, pm, _ = _create_engine(settings=settings, basis_snapshots=[basis])

        engine._check_entry_opportunities()

        pm.open_position.assert_not_called()

    def test_position_sizing(self):
        basis = _make_basis(executable_cc_bps=50.0, spot_mid=50000.0, futures_mid=50225.0)
        settings = _make_settings(position_notional_usdt=1000.0)
        engine, _, _, pm, _ = _create_engine(settings=settings, basis_snapshots=[basis])

        engine._check_entry_opportunities()

        pos = pm.open_position.call_args[0][0]
        assert pos.spot_qty == pytest.approx(1000.0 / 50000.0)
        assert pos.futures_qty == pytest.approx(1000.0 / 50225.0)
        assert pos.notional_usdt == 1000.0


class TestReverseCashAndCarryEntry:
    """Tests for reverse cash-and-carry entry logic."""

    def test_opens_when_threshold_met(self):
        basis = _make_basis(executable_cc_bps=-10.0, executable_rcc_bps=40.0)
        settings = _make_settings(entry_threshold_bps=30.0)
        engine, _, _, pm, _ = _create_engine(settings=settings, basis_snapshots=[basis])

        engine._check_entry_opportunities()

        pm.open_position.assert_called_once()
        pos = pm.open_position.call_args[0][0]
        assert pos.strategy == "reverse_cash_and_carry"
        assert pos.spot_side == "sell"
        assert pos.futures_side == "long"

    def test_does_not_open_below_threshold(self):
        basis = _make_basis(executable_cc_bps=-10.0, executable_rcc_bps=20.0)
        settings = _make_settings(entry_threshold_bps=30.0)
        engine, _, _, pm, _ = _create_engine(settings=settings, basis_snapshots=[basis])

        engine._check_entry_opportunities()

        pm.open_position.assert_not_called()

    def test_insufficient_spot_balance_blocks_entry(self):
        """Reverse C&C should be blocked when spot balance is insufficient."""
        basis = _make_basis(executable_cc_bps=-10.0, executable_rcc_bps=40.0, spot_mid=50000.0)
        settings = _make_settings(entry_threshold_bps=30.0, position_notional_usdt=1000.0)

        # Create engine with balance checker that reports insufficient balance
        basis_calc = MagicMock()
        basis_calc.get_all_basis.return_value = [basis]
        basis_calc.get_current_basis.side_effect = lambda sym, combo: basis if sym == "BTCUSDT" else None

        funding_tracker = MagicMock()
        funding_tracker.get_funding.return_value = None

        position_manager = MagicMock()
        position_manager.get_open_positions.return_value = []

        risk_controller = MagicMock()
        risk_controller.is_kill_switch_active.return_value = False

        balance_checker = MagicMock()
        # Required qty = 1000 / 50000 = 0.02, available = 0.01 (insufficient)
        balance_checker.get_available_spot_balance.return_value = 0.01

        engine = FuturesArbStrategyEngine(
            settings=settings,
            basis_calculator=basis_calc,
            funding_tracker=funding_tracker,
            position_manager=position_manager,
            risk_controller=risk_controller,
            balance_checker=balance_checker,
        )

        engine._check_entry_opportunities()

        position_manager.open_position.assert_not_called()
        # Verify the event was logged
        events = engine.get_events()
        insufficient_events = [e for e in events if e.get("type") == "insufficient_spot_balance"]
        assert len(insufficient_events) == 1
        assert insufficient_events[0]["symbol"] == "BTCUSDT"

    def test_sufficient_spot_balance_allows_entry(self):
        """Reverse C&C should proceed when spot balance is sufficient."""
        basis = _make_basis(executable_cc_bps=-10.0, executable_rcc_bps=40.0, spot_mid=50000.0)
        settings = _make_settings(entry_threshold_bps=30.0, position_notional_usdt=1000.0)

        basis_calc = MagicMock()
        basis_calc.get_all_basis.return_value = [basis]
        basis_calc.get_current_basis.side_effect = lambda sym, combo: basis if sym == "BTCUSDT" else None

        funding_tracker = MagicMock()
        funding_tracker.get_funding.return_value = None

        position_manager = MagicMock()
        position_manager.get_open_positions.return_value = []

        risk_controller = MagicMock()
        risk_controller.is_kill_switch_active.return_value = False

        balance_checker = MagicMock()
        # Required qty = 1000 / 50000 = 0.02, available = 0.05 (sufficient)
        balance_checker.get_available_spot_balance.return_value = 0.05

        engine = FuturesArbStrategyEngine(
            settings=settings,
            basis_calculator=basis_calc,
            funding_tracker=funding_tracker,
            position_manager=position_manager,
            risk_controller=risk_controller,
            balance_checker=balance_checker,
        )

        engine._check_entry_opportunities()

        position_manager.open_position.assert_called_once()
        pos = position_manager.open_position.call_args[0][0]
        assert pos.strategy == "reverse_cash_and_carry"

    def test_no_balance_checker_allows_entry(self):
        """Without balance checker, reverse C&C should proceed (paper mode)."""
        basis = _make_basis(executable_cc_bps=-10.0, executable_rcc_bps=40.0)
        settings = _make_settings(entry_threshold_bps=30.0)
        engine, _, _, pm, _ = _create_engine(settings=settings, basis_snapshots=[basis])

        engine._check_entry_opportunities()

        pm.open_position.assert_called_once()
        pos = pm.open_position.call_args[0][0]
        assert pos.strategy == "reverse_cash_and_carry"


class TestFundingArbitrageEntry:
    """Tests for funding rate arbitrage entry logic."""

    def test_opens_when_conditions_met(self):
        basis = _make_basis(executable_cc_bps=10.0, executable_rcc_bps=-5.0)
        funding = _make_funding(current_rate=0.002, avg_7d=0.0015)
        settings = _make_settings(funding_entry_threshold=0.001)
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], funding_infos=[funding]
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_called_once()
        pos = pm.open_position.call_args[0][0]
        assert pos.strategy == "funding_arb"
        # Positive funding → long spot + short perp
        assert pos.spot_side == "buy"
        assert pos.futures_side == "short"

    def test_negative_funding_opens_reverse(self):
        basis = _make_basis(executable_cc_bps=10.0, executable_rcc_bps=-5.0)
        funding = _make_funding(current_rate=-0.002, avg_7d=-0.0015)
        settings = _make_settings(funding_entry_threshold=0.001)
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], funding_infos=[funding]
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_called_once()
        pos = pm.open_position.call_args[0][0]
        assert pos.strategy == "funding_arb"
        # Negative funding → short spot + long perp
        assert pos.spot_side == "sell"
        assert pos.futures_side == "long"

    def test_does_not_open_below_threshold(self):
        basis = _make_basis(executable_cc_bps=10.0, executable_rcc_bps=-5.0)
        # z_score < 2.0 → not statistically significant → no entry
        funding = _make_funding(current_rate=0.0005, avg_7d=0.0003, z_score=1.0)
        settings = _make_settings(funding_entry_threshold=0.001)
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], funding_infos=[funding]
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_not_called()

    def test_does_not_open_when_direction_inconsistent(self):
        basis = _make_basis(executable_cc_bps=10.0, executable_rcc_bps=-5.0)
        # Current rate positive but avg_7d negative → inconsistent
        funding = _make_funding(current_rate=0.002, avg_7d=-0.001)
        settings = _make_settings(funding_entry_threshold=0.001)
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], funding_infos=[funding]
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_not_called()


class TestEntryLimits:
    """Tests for position limit enforcement."""

    def test_max_concurrent_positions(self):
        basis = _make_basis(executable_cc_bps=50.0)
        positions = [_make_position() for _ in range(5)]
        settings = _make_settings(max_concurrent_positions=5)
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], open_positions=positions
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_not_called()

    def test_per_symbol_notional_limit(self):
        basis = _make_basis(executable_cc_bps=50.0, symbol="BTCUSDT")
        # Already have 2500 USDT in BTCUSDT, limit is 3000, new would be 1000 → 3500 > 3000
        positions = [
            _make_position(symbol="BTCUSDT", notional=2500.0),
        ]
        settings = _make_settings(
            max_per_symbol_notional_usdt=3000.0,
            position_notional_usdt=1000.0,
        )
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], open_positions=positions
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_not_called()

    def test_total_exposure_limit(self):
        basis = _make_basis(executable_cc_bps=50.0)
        # Already have 9500 USDT total, limit is 10000, new would be 1000 → 10500 > 10000
        positions = [_make_position(symbol="ETHUSDT", notional=9500.0)]
        settings = _make_settings(
            max_total_exposure_usdt=10000.0,
            position_notional_usdt=1000.0,
        )
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], open_positions=positions
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_not_called()

    def test_kill_switch_blocks_entries(self):
        basis = _make_basis(executable_cc_bps=50.0)
        settings = _make_settings()
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], kill_switch=True
        )

        engine._step()

        pm.open_position.assert_not_called()

    def test_disabled_engine_blocks_entries(self):
        basis = _make_basis(executable_cc_bps=50.0)
        settings = _make_settings(enabled=False)
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis]
        )

        engine._step()

        pm.open_position.assert_not_called()

    def test_stale_pairs_skipped(self):
        basis = _make_basis(executable_cc_bps=50.0, status="stale")
        settings = _make_settings()
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis]
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_not_called()


class TestBestComboSelection:
    """Tests for selecting best exchange_combo per symbol."""

    def test_selects_highest_executable_basis(self):
        basis1 = _make_basis(
            symbol="BTCUSDT",
            exchange_combo="mexc_spot+mexc_futures",
            executable_cc_bps=40.0,
        )
        basis2 = _make_basis(
            symbol="BTCUSDT",
            exchange_combo="mexc_spot+asterdex_perp",
            executable_cc_bps=60.0,
        )
        settings = _make_settings(
            exchange_combos=["mexc_spot+mexc_futures", "mexc_spot+asterdex_perp"],
            entry_threshold_bps=30.0,
        )
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis1, basis2]
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_called_once()
        pos = pm.open_position.call_args[0][0]
        assert pos.exchange_combo == "mexc_spot+asterdex_perp"

    def test_selects_best_across_strategies(self):
        # CC basis is 35 bps, funding arb is 20 bps (0.002 * 10000) → CC wins
        basis = _make_basis(executable_cc_bps=35.0, executable_rcc_bps=-5.0)
        funding = _make_funding(current_rate=0.002, avg_7d=0.0015)
        settings = _make_settings(
            entry_threshold_bps=30.0,
            funding_entry_threshold=0.001,
        )
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], funding_infos=[funding]
        )

        engine._check_entry_opportunities()

        pm.open_position.assert_called_once()
        pos = pm.open_position.call_args[0][0]
        assert pos.strategy == "cash_and_carry"


class TestPaperLiveMode:
    """Tests that paper and live modes use identical decision logic."""

    def test_paper_mode_opens_position(self):
        basis = _make_basis(executable_cc_bps=50.0)
        settings = _make_settings(mode="paper")
        engine, _, _, pm, _ = _create_engine(settings=settings, basis_snapshots=[basis])

        engine._check_entry_opportunities()

        pm.open_position.assert_called_once()

    def test_live_mode_opens_position(self):
        basis = _make_basis(executable_cc_bps=50.0)
        settings = _make_settings(mode="live")
        engine, _, _, pm, _ = _create_engine(settings=settings, basis_snapshots=[basis])

        engine._check_entry_opportunities()

        pm.open_position.assert_called_once()

    def test_same_decision_both_modes(self):
        """Both modes should produce the same entry decision for the same market state."""
        basis = _make_basis(executable_cc_bps=50.0)

        # Paper mode
        settings_paper = _make_settings(mode="paper")
        engine_paper, _, _, pm_paper, _ = _create_engine(
            settings=settings_paper, basis_snapshots=[basis]
        )
        engine_paper._check_entry_opportunities()

        # Live mode
        settings_live = _make_settings(mode="live")
        engine_live, _, _, pm_live, _ = _create_engine(
            settings=settings_live, basis_snapshots=[basis]
        )
        engine_live._check_entry_opportunities()

        # Both should open
        pm_paper.open_position.assert_called_once()
        pm_live.open_position.assert_called_once()

        # Same strategy and sides
        pos_paper = pm_paper.open_position.call_args[0][0]
        pos_live = pm_live.open_position.call_args[0][0]
        assert pos_paper.strategy == pos_live.strategy
        assert pos_paper.spot_side == pos_live.spot_side
        assert pos_paper.futures_side == pos_live.futures_side


class TestBackgroundLoop:
    """Tests for the background thread loop."""

    def test_step_called_periodically(self):
        settings = _make_settings(loop_interval_sec=0.05, enabled=True)
        engine, _, _, pm, _ = _create_engine(settings=settings)

        engine.start()
        time.sleep(0.2)
        engine.stop()

        # Engine was running but no basis data → no positions opened
        pm.open_position.assert_not_called()

    def test_step_respects_kill_switch(self):
        basis = _make_basis(executable_cc_bps=50.0)
        settings = _make_settings(loop_interval_sec=0.05, enabled=True)
        engine, _, _, pm, _ = _create_engine(
            settings=settings, basis_snapshots=[basis], kill_switch=True
        )

        engine.start()
        time.sleep(0.15)
        engine.stop()

        pm.open_position.assert_not_called()


class TestEntryFees:
    """Tests for entry fee calculation."""

    def test_entry_fees_computed_correctly(self):
        basis = _make_basis(executable_cc_bps=50.0)
        settings = _make_settings(
            position_notional_usdt=1000.0,
            spot_taker_fee_bps=1.0,
            futures_taker_fee_bps=2.0,
        )
        engine, _, _, pm, _ = _create_engine(settings=settings, basis_snapshots=[basis])

        engine._check_entry_opportunities()

        pos = pm.open_position.call_args[0][0]
        # spot_fee = 1000 * 1/10000 = 0.1
        # futures_fee = 1000 * 2/10000 = 0.2
        # total = 0.3
        assert pos.entry_fees == pytest.approx(0.3)
