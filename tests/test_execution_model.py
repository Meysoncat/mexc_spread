"""Tests for the execution model (mexc_monitor.execution_model)
and its integration into SpreadCaptureEngine."""

from __future__ import annotations

import pytest

from mexc_monitor.execution_model import (
    ExecutionSimulator,
    ExecutionSettings,
    FillOutcome,
)


# ─── ExecutionSimulator unit tests ────────────────────────────────────────────


class TestFillProbability:
    def test_legacy_mode_always_fills(self):
        """realistic_fills=False → instant fill (backward compat)."""
        sim = ExecutionSimulator(ExecutionSettings(realistic_fills=False))
        outcome = sim.check_limit_fill(
            limit_price=100.0, bid=99.0, ask=101.0,
            elapsed_sec=0.0, side="buy",
        )
        assert outcome.filled is True
        assert outcome.adverse_cost == 0.0

    def test_zero_rate_never_fills(self):
        sim = ExecutionSimulator(ExecutionSettings(fill_rate_per_sec=0.0))
        outcome = sim.check_limit_fill(
            limit_price=100.0, bid=99.0, ask=101.0,
            elapsed_sec=10.0, side="buy",
        )
        assert outcome.filled is False

    def test_high_rate_usually_fills(self):
        """With rate=10/sec and 1 sec elapsed, P(fill) ≈ 0.99995."""
        sim = ExecutionSimulator(ExecutionSettings(
            fill_rate_per_sec=10.0, seed=42,
        ))
        fills = sum(
            sim.check_limit_fill(100.0, 99.0, 101.0, 1.0, "buy").filled
            for _ in range(100)
        )
        assert fills >= 95  # Nearly all should fill

    def test_adverse_selection_cost(self):
        """When filled, adverse_cost = ratio × half_spread."""
        sim = ExecutionSimulator(ExecutionSettings(
            fill_rate_per_sec=100.0,  # Almost instant fill
            adverse_selection_ratio=0.5,
            seed=0,
        ))
        # half_spread = (101 - 99) / 2 = 1.0
        outcome = sim.check_limit_fill(
            limit_price=99.0, bid=99.0, ask=101.0,
            elapsed_sec=1.0, side="buy",
        )
        assert outcome.filled is True
        assert outcome.adverse_cost == pytest.approx(0.5)  # 0.5 × 1.0

    def test_zero_adverse_selection(self):
        sim = ExecutionSimulator(ExecutionSettings(
            fill_rate_per_sec=100.0,
            adverse_selection_ratio=0.0,
            seed=0,
        ))
        outcome = sim.check_limit_fill(99.0, 99.0, 101.0, 1.0, "buy")
        assert outcome.filled is True
        assert outcome.adverse_cost == pytest.approx(0.0)

    def test_seed_reproducibility(self):
        """Same seed → same fill sequence."""
        s1 = ExecutionSimulator(ExecutionSettings(fill_rate_per_sec=0.5, seed=123))
        s2 = ExecutionSimulator(ExecutionSettings(fill_rate_per_sec=0.5, seed=123))
        for _ in range(20):
            o1 = s1.check_limit_fill(100.0, 99.0, 101.0, 1.0, "buy")
            o2 = s2.check_limit_fill(100.0, 99.0, 101.0, 1.0, "buy")
            assert o1.filled == o2.filled


class TestMarketExitPrice:
    def test_sell_below_bid(self):
        sim = ExecutionSimulator(ExecutionSettings(market_slippage_bps=5.0))
        # mid = 100, slippage = 100 * 5/10000 = 0.05
        # sell price = 99 - 0.05 = 98.95
        price = sim.market_exit_price(99.0, 101.0, side="sell")
        assert price == pytest.approx(98.95)

    def test_buy_above_ask(self):
        sim = ExecutionSimulator(ExecutionSettings(market_slippage_bps=5.0))
        price = sim.market_exit_price(99.0, 101.0, side="buy")
        assert price == pytest.approx(101.05)

    def test_zero_slippage(self):
        sim = ExecutionSimulator(ExecutionSettings(market_slippage_bps=0.0))
        assert sim.market_exit_price(99.0, 101.0, "sell") == pytest.approx(99.0)
        assert sim.market_exit_price(99.0, 101.0, "buy") == pytest.approx(101.0)


class TestPnlAdjustment:
    def test_adverse_subtraction(self):
        gross = 10.0
        adverse = 0.5
        qty = 2.0
        adjusted = ExecutionSimulator.adjust_pnl_for_adverse_selection(
            gross, adverse, qty,
        )
        assert adjusted == pytest.approx(10.0 - 0.5 * 2.0)  # 9.0


# ─── SpreadCaptureEngine integration tests ───────────────────────────────────


class TestSpreadCaptureExecutionModel:
    """Test that the engine uses pending states in paper mode."""

    def test_monitor_mode_logs_signal_only(self):
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings
        from mexc_monitor.spread_buffer import push_tick

        eng = SpreadCaptureEngine(CaptureSettings(
            mode="monitor", enabled=True, kill_switch=False,
            entry_threshold_bps=5.0, symbol="TESTMON",
        ))
        push_tick("TESTMON", 99.0, 101.0, 1.0, 1.0)  # 200 bps spread
        eng._step()
        status = eng.get_status()
        # Monitor mode: no position opened
        assert status["position"]["state"] == "idle"
        # But signal was logged
        assert len(eng.get_signals()) >= 1

    def test_paper_legacy_instant_fill(self):
        """realistic_fills=False → instant entry (backward compat)."""
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings
        from mexc_monitor.spread_buffer import push_tick

        eng = SpreadCaptureEngine(CaptureSettings(
            mode="paper", enabled=True, kill_switch=False,
            entry_threshold_bps=5.0, symbol="TESTLEG",
            realistic_fills=False,
        ))
        push_tick("TESTLEG", 99.0, 101.0, 1.0, 1.0)
        eng._step()
        status = eng.get_status()
        assert status["position"]["state"] == "holding"

    def test_paper_realistic_enters_pending_buy(self):
        """realistic_fills=True → entry goes to pending_buy, not holding."""
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings
        from mexc_monitor.spread_buffer import push_tick

        eng = SpreadCaptureEngine(CaptureSettings(
            mode="paper", enabled=True, kill_switch=False,
            entry_threshold_bps=5.0, symbol="TESTRB",
            realistic_fills=True, fill_rate_per_sec=0.0,  # Never fills immediately
        ))
        push_tick("TESTRB", 99.0, 101.0, 1.0, 1.0)
        eng._step()
        status = eng.get_status()
        assert status["position"]["state"] == "pending_buy"

    def test_paper_pending_buy_fills_eventually(self):
        """With high fill rate, pending_buy should transition to holding."""
        import time
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings
        from mexc_monitor.spread_buffer import push_tick

        eng = SpreadCaptureEngine(CaptureSettings(
            mode="paper", enabled=True, kill_switch=False,
            entry_threshold_bps=5.0, symbol="TESTFILL",
            realistic_fills=True, fill_rate_per_sec=100.0,  # Near-instant
        ))
        push_tick("TESTFILL", 99.0, 101.0, 1.0, 1.0)
        eng._step()  # → pending_buy
        assert eng.get_status()["position"]["state"] == "pending_buy"
        time.sleep(0.05)  # Accumulate elapsed time for fill probability
        push_tick("TESTFILL", 99.0, 101.0, 1.0, 1.0)  # Fresh tick
        eng._step()  # → should fill → holding
        assert eng.get_status()["position"]["state"] == "holding"

    def test_paper_pending_buy_timeout_cancels(self):
        """max_pending_sec exceeded → cancel, return to idle."""
        import time
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings
        from mexc_monitor.spread_buffer import push_tick

        eng = SpreadCaptureEngine(CaptureSettings(
            mode="paper", enabled=True, kill_switch=False,
            entry_threshold_bps=5.0, symbol="TESTTO",
            realistic_fills=True, fill_rate_per_sec=0.0,  # Never fills
            max_pending_sec=0.1,  # Very short timeout
        ))
        push_tick("TESTTO", 99.0, 101.0, 1.0, 1.0)
        eng._step()  # → pending_buy
        assert eng.get_status()["position"]["state"] == "pending_buy"
        time.sleep(0.2)  # Wait for timeout
        eng._step()  # → timeout → idle
        assert eng.get_status()["position"]["state"] == "idle"

    def test_adverse_cost_in_trade_record(self):
        """Completed trade should have adverse_cost_usdt > 0."""
        import time
        from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings
        from mexc_monitor.spread_buffer import push_tick

        eng = SpreadCaptureEngine(CaptureSettings(
            mode="paper", enabled=True, kill_switch=False,
            entry_threshold_bps=5.0, exit_threshold_bps=100.0,
            symbol="TESTADV", realistic_fills=True,
            fill_rate_per_sec=100.0,
            adverse_selection_ratio=0.5,
            taker_fee_bps=0.0,  # Isolate adverse cost
        ))
        # Entry: spread 200 bps (wide)
        push_tick("TESTADV", 99.0, 101.0, 1.0, 1.0)
        eng._step()  # → pending_buy
        time.sleep(0.05)
        push_tick("TESTADV", 99.0, 101.0, 1.0, 1.0)
        eng._step()  # → holding (filled, adverse cost recorded)

        # Exit: spread narrows to 100 bps (≤ exit_threshold)
        push_tick("TESTADV", 99.5, 100.5, 1.0, 1.0)
        eng._step()  # → pending_sell
        time.sleep(0.05)
        push_tick("TESTADV", 99.5, 100.5, 1.0, 1.0)
        eng._step()  # → filled → idle

        trades = eng.get_trades(limit=1)
        assert len(trades) >= 1
        t = trades[-1]
        assert t["adverse_cost_usdt"] > 0
