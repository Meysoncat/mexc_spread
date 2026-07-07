"""Tests for PortfolioRiskManager."""

from __future__ import annotations

import pytest

from mexc_monitor.portfolio_risk import (
    PortfolioRiskManager,
    PortfolioRiskSettings,
    PortfolioRiskStatus,
)


class FakeEngine:
    """Minimal engine adapter for testing."""

    def __init__(
        self,
        name: str,
        notional: float = 0.0,
        symbols: list[str] | None = None,
        net_pnl: float = 0.0,
    ):
        self._name = name
        self._notional = notional
        self._symbols = symbols or []
        self._net_pnl = net_pnl
        self.kill_switched = False

    @property
    def engine_name(self) -> str:
        return self._name

    def get_open_notional(self) -> float:
        return self._notional

    def get_open_symbols(self) -> list[str]:
        return self._symbols

    def trigger_kill_switch(self) -> None:
        self.kill_switched = True

    def get_status(self) -> dict:
        return {"stats": {"net_pnl_usdt": self._net_pnl}}


class TestPortfolioRisk:
    def test_empty_status(self):
        pm = PortfolioRiskManager()
        status = pm.get_status()
        assert status.total_exposure_usdt == 0.0
        assert status.engine_count == 0
        assert len(status.alerts) == 0

    def test_aggregates_exposure(self):
        pm = PortfolioRiskManager()
        pm.register_engine(FakeEngine("capture", notional=100))
        pm.register_engine(FakeEngine("arbitrage", notional=200))
        pm.register_engine(FakeEngine("futures_arb", notional=300))
        status = pm.get_status()
        assert status.total_exposure_usdt == pytest.approx(600)
        assert status.engine_count == 3

    def test_total_exposure_alert(self):
        s = PortfolioRiskSettings(max_total_exposure_usdt=500)
        pm = PortfolioRiskManager(s)
        pm.register_engine(FakeEngine("e1", notional=400))
        pm.register_engine(FakeEngine("e2", notional=200))
        status = pm.get_status()
        assert any(a["type"] == "total_exposure_exceeded" for a in status.alerts)

    def test_symbol_concentration_alert(self):
        s = PortfolioRiskSettings(max_positions_per_symbol=2)
        pm = PortfolioRiskManager(s)
        pm.register_engine(FakeEngine("e1", symbols=["BTCUSDT", "ETHUSDT"]))
        pm.register_engine(FakeEngine("e2", symbols=["BTCUSDT"]))
        pm.register_engine(FakeEngine("e3", symbols=["BTCUSDT"]))
        status = pm.get_status()
        btc_count = status.positions_by_symbol.get("BTCUSDT", 0)
        assert btc_count == 3
        assert any(
            a["type"] == "symbol_concentration" and a["symbol"] == "BTCUSDT"
            for a in status.alerts
        )

    def test_kill_switch_triggers_all(self):
        pm = PortfolioRiskManager()
        e1 = FakeEngine("e1", notional=100)
        e2 = FakeEngine("e2", notional=200)
        pm.register_engine(e1)
        pm.register_engine(e2)
        pm.activate_kill_switch("test")
        assert e1.kill_switched is True
        assert e2.kill_switched is True
        assert pm.get_status().kill_switch_active is True

    def test_deactivate_kill_switch(self):
        pm = PortfolioRiskManager()
        pm.activate_kill_switch()
        assert pm.get_status().kill_switch_active is True
        pm.deactivate_kill_switch()
        assert pm.get_status().kill_switch_active is False

    def test_drawdown_alert(self):
        s = PortfolioRiskSettings(max_daily_drawdown_usdt=50)
        pm = PortfolioRiskManager(s)
        # Engine started with +100 PNL, now at +20 → drawdown = 80 > 50
        pm._day_start_pnl = 100.0
        pm.register_engine(FakeEngine("e1", net_pnl=20))
        status = pm.get_status()
        assert status.daily_drawdown_usdt == pytest.approx(80)
        assert any(a["type"] == "daily_drawdown_exceeded" for a in status.alerts)

    def test_unregister_engine(self):
        pm = PortfolioRiskManager()
        e = FakeEngine("e1", notional=100)
        pm.register_engine(e)
        assert pm.get_status().engine_count == 1
        pm.unregister_engine(e)
        assert pm.get_status().engine_count == 0

    def test_no_exposure_no_alerts(self):
        s = PortfolioRiskSettings(max_total_exposure_usdt=1000)
        pm = PortfolioRiskManager(s)
        pm.register_engine(FakeEngine("e1", notional=100))
        status = pm.get_status()
        assert len(status.alerts) == 0

    def test_engine_error_handled_gracefully(self):
        pm = PortfolioRiskManager()

        class BrokenEngine:
            engine_name = "broken"
            def get_open_notional(self): raise RuntimeError("boom")
            def get_open_symbols(self): return []
            def trigger_kill_switch(self): pass
            def get_status(self): return {}

        pm.register_engine(BrokenEngine())
        pm.register_engine(FakeEngine("ok", notional=50))
        status = pm.get_status()
        # Broken engine skipped, good engine still counted
        assert status.total_exposure_usdt == pytest.approx(50)
