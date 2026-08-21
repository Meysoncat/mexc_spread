"""Cross-screener: symbol resolution, gates, scoring, engine scan."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import mexc_monitor.cross_screener.symbols as symbols_mod
from mexc_monitor.cross_screener.config import (
    CrossScreenerConfig,
    apply_config_patch,
)
from mexc_monitor.cross_screener.engine import (
    CrossScreenerEngine,
    _basis_bps,
    score_opportunity,
)


def _tick(bid: float, ask: float, qty: float = 100.0, age_ms: float = 0.0):
    now = time.time() * 1000.0
    return SimpleNamespace(
        timestamp_ms=now - age_ms,
        bid=bid,
        ask=ask,
        bid_qty=qty,
        ask_qty=qty,
        mid=(bid + ask) / 2,
    )


def _cfg(**kw) -> CrossScreenerConfig:
    base = dict(
        min_net_basis_bps=5.0,
        min_lifetime_sec=0.0,
        min_l1_notional_usdt=10.0,
        use_zscore=False,
        exit_hysteresis_bps=1.0,
    )
    base.update(kw)
    return CrossScreenerConfig(**base)


# ── basis math ──────────────────────────────────────────────────────────────


def test_basis_bps_direction():
    # buy at 100, sell at 100.10 → +10 bps
    assert _basis_bps(100.10, 100.0) == pytest.approx(10.0, abs=0.01)
    # crossed the wrong way → negative
    assert _basis_bps(99.9, 100.0) < 0


def test_score_prefers_higher_net_and_liquidity():
    cfg = _cfg()
    s_hi, _ = score_opportunity(20.0, 5000.0, 10.0, None, None, cfg)
    s_lo, _ = score_opportunity(8.0, 100.0, 10.0, None, None, cfg)
    assert s_hi > s_lo


# ── symbol resolution ───────────────────────────────────────────────────────


def test_resolve_tick_spot_and_futures(monkeypatch):
    ticks = {"BTCUSDT": _tick(99, 100), "ETH_USDT": _tick(49, 50)}
    monkeypatch.setattr(symbols_mod, "get_latest", lambda k: ticks.get(k))

    key, _ = symbols_mod.resolve_tick("BTCUSDT", "mexc")
    assert key == "BTCUSDT"
    key, _ = symbols_mod.resolve_tick("ETHUSDT", "mexc")
    assert key == "ETH_USDT"  # futures form fallback
    key, tick = symbols_mod.resolve_tick("NOPEUSDT", "mexc")
    assert key is None and tick is None


def test_cross_pairs_matches_both_legs(monkeypatch):
    tracked = ["ASTER:BTCUSDT", "ASTER:DOGEUSDT", "BTCUSDT", "CROSS:X"]
    ticks = {
        "ASTER:BTCUSDT": _tick(100, 100.2),
        "BTCUSDT": _tick(99.9, 100.0),
        "ASTER:DOGEUSDT": _tick(0.1, 0.1001),
        # DOGEUSDT missing on MEXC → pair excluded
    }
    monkeypatch.setattr(symbols_mod, "get_tracked_symbols", lambda: tracked)
    monkeypatch.setattr(symbols_mod, "get_latest", lambda k: ticks.get(k))

    pairs = symbols_mod.cross_pairs("mexc", "aster")
    assert [p[0] for p in pairs] == ["BTCUSDT"]


# ── engine gates ────────────────────────────────────────────────────────────


def _engine(monkeypatch, ticks, tracked, cfg=None) -> CrossScreenerEngine:
    monkeypatch.setattr(symbols_mod, "get_tracked_symbols", lambda: tracked)
    monkeypatch.setattr(symbols_mod, "get_latest", lambda k: ticks.get(k))
    return CrossScreenerEngine(config=cfg or _cfg())


def _fresh_pair_ticks(edge_bps: float = 20.0, qty: float = 100.0, age_ms: float = 0.0):
    """MEXC cheaper than Aster by `edge_bps` (Aster bid above MEXC ask)."""
    mexc_ask = 100.0
    aster_bid = mexc_ask * (1 + edge_bps / 10_000.0)
    return {
        "BTCUSDT": _tick(mexc_ask - 0.05, mexc_ask, qty=qty, age_ms=age_ms),
        "ASTER:BTCUSDT": _tick(aster_bid, aster_bid + 0.05, qty=qty, age_ms=age_ms),
    }


_TRACKED = ["ASTER:BTCUSDT", "BTCUSDT"]


def test_engine_finds_opportunity(monkeypatch):
    e = _engine(monkeypatch, _fresh_pair_ticks(edge_bps=30.0), _TRACKED)
    e._scan_once()
    opps = e.get_opportunities()
    assert len(opps) == 1
    o = opps[0]
    assert o["symbol"] == "BTCUSDT"
    assert o["buy_on"] == "mexc"
    assert o["sell_on"] == "aster"
    assert o["net_bps"] == pytest.approx(30.0 - 4.0, abs=0.5)


def test_engine_rejects_stale_leg(monkeypatch):
    ticks = _fresh_pair_ticks(edge_bps=30.0)
    ticks["ASTER:BTCUSDT"] = _tick(100.3, 100.35, age_ms=60_000.0)
    e = _engine(monkeypatch, ticks, _TRACKED)
    e._scan_once()
    assert e.get_opportunities() == []
    reasons = dict(e.get_status()["reject_reasons_top"])
    assert reasons.get("stale_leg", 0) == 1


def test_engine_rejects_below_net_floor(monkeypatch):
    # edge 6 bps − fee 4 → net 2 < floor 5
    e = _engine(monkeypatch, _fresh_pair_ticks(edge_bps=6.0), _TRACKED)
    e._scan_once()
    assert e.get_opportunities() == []
    assert dict(e.get_status()["reject_reasons_top"]).get("net_floor") == 1


def test_engine_rejects_thin_liquidity(monkeypatch):
    # qty 0.05 → L1 notional ≈ 5 USDT < 10 min
    e = _engine(monkeypatch, _fresh_pair_ticks(edge_bps=30.0, qty=0.05), _TRACKED)
    e._scan_once()
    assert e.get_opportunities() == []
    assert dict(e.get_status()["reject_reasons_top"]).get("l1_liquidity") == 1


def test_engine_lifetime_gate(monkeypatch):
    e = _engine(monkeypatch, _fresh_pair_ticks(edge_bps=30.0), _TRACKED,
              cfg=_cfg(min_lifetime_sec=60.0))
    e._scan_once()
    assert e.get_opportunities() == []
    assert dict(e.get_status()["reject_reasons_top"]).get("lifetime") == 1


def test_engine_opposite_direction(monkeypatch):
    # MEXC richer than Aster → buy on aster, sell on mexc.
    ticks = {
        "BTCUSDT": _tick(100.30, 100.35),          # mexc bid 100.30
        "ASTER:BTCUSDT": _tick(99.95, 100.00),     # aster ask 100.00
    }
    e = _engine(monkeypatch, ticks, _TRACKED)
    e._scan_once()
    opps = e.get_opportunities()
    assert len(opps) == 1
    assert opps[0]["buy_on"] == "aster"
    assert opps[0]["sell_on"] == "mexc"


def test_engine_hysteresis_keeps_incumbent(monkeypatch):
    # net = 4.5 < floor 5: newcomer rejected, incumbent kept with 1.0 hysteresis.
    e = _engine(monkeypatch, _fresh_pair_ticks(edge_bps=8.5), _TRACKED)
    e._prev_top_keys = {"MEXC×ASTER:BTCUSDT"}
    e._scan_once()
    assert len(e.get_opportunities()) == 1


def test_engine_empty_when_no_aster_ticks(monkeypatch):
    e = _engine(monkeypatch, {}, [])
    e._scan_once()
    assert e.get_opportunities() == []
    assert e.get_status()["last_error"] is None


def test_config_patch_validation():
    cfg = _cfg()
    new = apply_config_patch(cfg, {"min_net_basis_bps": 7.5, "use_zscore": True})
    assert new.min_net_basis_bps == 7.5
    assert new.use_zscore is True
    with pytest.raises(ValueError):
        apply_config_patch(cfg, {"nope": 1})
    with pytest.raises(ValueError):
        apply_config_patch(cfg, {"top_limit": 0})
