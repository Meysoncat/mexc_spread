"""Per-symbol freshness in the screener engine (Part A).

tick_age_ms must come from the symbol's own live-feed tick (spread_buffer)
when available, not from the snapshot-global age.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import mexc_monitor.screener.engine as eng
from mexc_monitor.screener.config import ScreenerConfig
from mexc_monitor.screener.engine import ScreenerEngine


def _cfg() -> ScreenerConfig:
    return ScreenerConfig(
        adaptive_mode=False,
        use_spread_zscore=False,
        min_lifetime_sec=0.0,
        min_net_spread_bps=3.0,
        max_tick_age_ms=10_000.0,
        volume_gate_mode="soft",
        top_limit=10,
    )


def _snapshot_df(minutes_old: float) -> pd.DataFrame:
    observed = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_old)
    ).isoformat()
    rows = []
    for sym in ("FRESHUSDT", "STALEUSDT"):
        rows.append(
            {
                "symbol": sym,
                "bid": 99.0,
                "ask": 100.0,
                "mid": 99.5,
                "spread_bps": 100.0,
                "l1_max_notional_quote": 5_000.0,
                "volume_24h_quote": 500_000.0,
                "observed_at": observed,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(eng, "safe_load_snapshot", lambda market: (_snapshot_df(5.0), None))
    # No trades/bookTicker data in tests.
    monkeypatch.setattr(eng, "get_book_update_rate", lambda sym: None)
    monkeypatch.setattr(eng.trade_buffer, "get_stats", lambda sym: None)
    e = ScreenerEngine(config=_cfg())
    return e


def test_fresh_ws_tick_overrides_stale_snapshot(engine, monkeypatch):
    """A symbol with a live-feed tick stays eligible even when the whole
    snapshot is minutes old; a symbol without one is rejected as stale."""
    now_ms = time.time() * 1000.0

    def fake_get_latest(sym: str):
        if sym == "FRESHUSDT":
            return SimpleNamespace(timestamp_ms=now_ms)
        return None  # STALEUSDT: no live feed → snapshot age applies

    monkeypatch.setattr(eng, "sb_get_latest", fake_get_latest)

    engine._scan_once()
    symbols = {o["symbol"] for o in engine.get_opportunities()}
    assert "FRESHUSDT" in symbols
    assert "STALEUSDT" not in symbols

    reasons = dict(engine.get_status()["reject_reasons_top"])
    assert any("tick" in r or "stale" in r or "age" in r for r in reasons)


def test_no_live_ticks_falls_back_to_snapshot_age(engine, monkeypatch):
    """Without any live ticks, the snapshot-global age gates everything out."""
    monkeypatch.setattr(eng, "sb_get_latest", lambda sym: None)
    engine._scan_once()
    assert engine.get_opportunities() == []
