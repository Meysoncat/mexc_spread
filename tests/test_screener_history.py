"""Tests for the screener opportunity history store (SQLite via ORM)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mexc_monitor.orm import create_schema
from mexc_monitor.screener import history_store as hs


@pytest.fixture
def db() -> Path:
    p = Path(tempfile.mkdtemp()) / "screener_hist.db"
    create_schema(p)
    return p


def _opp(**over) -> dict:
    base = {
        "net_spread_bps": 42.0,
        "l1_notional": 500.0,
        "volume_24h_quote": 12345.0,
        "lifetime_sec": 30.0,
        "spread_zscore": 2.1,
        "book_update_rate_per_min": 120.0,
        "score": 31.5,
        "score_breakdown": {"ev": 25.0, "liquidity": 4.0},
    }
    base.update(over)
    return base


def test_enter_creates_open_event(db):
    rid = hs.record_enter(
        db, symbol="solusdt", found_at_iso="2026-08-12T10:00:00+00:00",
        found_at_ms=1_000_000, opp=_opp(),
    )
    assert rid is not None
    open_events = hs.query_events(db, only_open=True)
    assert len(open_events) == 1
    assert open_events[0]["symbol"] == "SOLUSDT"
    assert open_events[0]["open"] is True
    assert open_events[0]["exited_at"] is None
    assert open_events[0]["score_breakdown"] == {"ev": 25.0, "liquidity": 4.0}


def test_exit_sets_duration_and_closes(db):
    hs.record_enter(
        db, symbol="SOLUSDT", found_at_iso="2026-08-12T10:00:00+00:00",
        found_at_ms=1_000_000, opp=_opp(),
    )
    updated = hs.record_exit(
        db, symbol="solusdt", exited_at_iso="2026-08-12T10:02:00+00:00",
        exited_at_ms=1_000_000 + 120_000,
    )
    assert updated is True
    ev = hs.query_events(db, limit=1)[0]
    assert ev["open"] is False
    assert ev["exited_at"] is not None
    assert ev["duration_sec"] == 120.0


def test_exit_when_no_open_event_is_false(db):
    updated = hs.record_exit(
        db, symbol="NOPE", exited_at_iso="2026-08-12T10:02:00+00:00",
        exited_at_ms=1_000_000,
    )
    assert updated is False


def test_close_all_open_resets_stale(db):
    hs.record_enter(db, symbol="AAA", found_at_iso="t1", found_at_ms=1_000, opp=_opp())
    hs.record_enter(db, symbol="BBB", found_at_iso="t2", found_at_ms=2_000, opp=_opp())
    assert len(hs.query_events(db, only_open=True)) == 2
    n = hs.close_all_open(db, now_iso="now", now_ms=60_000)
    assert n == 2
    assert len(hs.query_events(db, only_open=True)) == 0
    # durations computed from found_at_ms to now_ms
    evs = {e["symbol"]: e for e in hs.query_events(db, limit=10)}
    assert evs["AAA"]["duration_sec"] == 59.0


def test_symbol_filter(db):
    hs.record_enter(db, symbol="AAA", found_at_iso="t", found_at_ms=1, opp=_opp())
    hs.record_enter(db, symbol="BBB", found_at_iso="t", found_at_ms=2, opp=_opp())
    only_a = hs.query_events(db, symbol="aaa")
    assert len(only_a) == 1
    assert only_a[0]["symbol"] == "AAA"


def test_enter_exit_cycle_per_symbol(db):
    # enter → exit → enter again creates two distinct rows.
    hs.record_enter(db, symbol="X", found_at_iso="t1", found_at_ms=1000, opp=_opp())
    hs.record_exit(db, symbol="X", exited_at_iso="t2", exited_at_ms=2000)
    hs.record_enter(db, symbol="X", found_at_iso="t3", found_at_ms=3000, opp=_opp())
    evs = hs.query_events(db, symbol="X")
    assert len(evs) == 2
    assert evs[0]["open"] is True  # latest first
    assert evs[1]["open"] is False
