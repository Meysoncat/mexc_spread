"""Генератор сетапов: реестр, детекторы, движок, журнал."""

from __future__ import annotations

import time
from collections import deque

import pytest

from mexc_monitor.hub.hub import MarketHub, MarketView
from mexc_monitor.hub.models import Candle, InstrumentMeta, PerpQuote, SpotQuote
from mexc_monitor.setups.config import SetupsConfig, load_setups_config
from mexc_monitor.setups.detectors.arb import BasisDetector, CrossPerpDetector, FundingArbDetector
from mexc_monitor.setups.detectors.directional import (
    BreakoutDetector,
    BtcLagDetector,
    FundingSqueezeDetector,
    LeadLagDetector,
    MinuteMids,
    MomentumDetector,
    Series,
)
from mexc_monitor.setups.engine import SetupEngine
from mexc_monitor.setups.model import Candidate, Leg
from mexc_monitor.setups.registry import SetupRegistry, TypePolicy
from mexc_monitor.setups.store import SetupStore

NOW = 1_760_000_000.0


def pq(venue, base, bid, ask, *, qty=10.0, age=0.2, funding=None, interval=8.0, vol=5e7, oi=None, key=None, indicative=False):
    return PerpQuote(
        venue=venue, key=key or f"{base}USDT", native=key or f"{base}USDT", base=base, quote="USDT",
        bid=bid, ask=ask, bid_qty=qty, ask_qty=qty, age_sec=age, indicative=indicative,
        funding_rate=funding, funding_interval_h=interval, volume_24h_usd=vol, open_interest_usd=oi,
    )


def sq(venue, base, bid, ask, qty=10.0):
    return SpotQuote(venue=venue, key=f"{base}USDT", native=f"{base}USDT", base=base, quote="USDT", bid=bid, ask=ask, bid_qty=qty, ask_qty=qty, age_sec=0.5)


def view_of(*quotes, spots=()):
    v = MarketView(ts=NOW)
    for q in quotes:
        v.perps.setdefault(q.base, []).append(q)
    for s in spots:
        v.spots.setdefault(s.base, []).append(s)
    return v


def cand(key="cross_perp:BTC:a>b", score=50.0, edge=12.0, opens=True, **kw):
    t = key.split(":")[0]
    leg = Leg("a", "perp", "BTCUSDT", "buy", 100.0, 100.0)
    return Candidate(key=key, type=t, base="BTC", legs=[leg], score=score, edge_bps=edge, opens=opens, **kw)


# ───────────────────────────────────────────────────────── registry


def test_registry_open_requires_persistence_and_closes_after_grace():
    reg = SetupRegistry({"cross_perp": TypePolicy(min_persist_sec=1.5, grace_sec=3.0, cooldown_sec=10.0)})
    assert reg.sync("cross_perp", [cand()], NOW) == []
    evs = reg.sync("cross_perp", [cand()], NOW + 2)
    assert [e["event"] for e in evs] == ["opened"]
    s = evs[0]["setup"]
    assert s["schema"] == "setup/v1" and s["status"] == "active" and s["category"] == "arbitrage"
    assert reg.sync("cross_perp", [], NOW + 3) == []  # ещё в пределах grace
    evs = reg.sync("cross_perp", [], NOW + 5.5)
    assert evs[0]["event"] == "closed" and evs[0]["setup"]["close_reason"] == "condition_gone"
    assert evs[0]["setup"]["outcome"]["max_edge_bps"] == 12.0
    # cooldown не даёт сразу переоткрыть
    reg.sync("cross_perp", [cand()], NOW + 6)
    assert reg.sync("cross_perp", [cand()], NOW + 8) == []


def test_registry_single_tick_spike_never_opens():
    reg = SetupRegistry()
    reg.sync("cross_perp", [cand()], NOW)
    reg.sync("cross_perp", [], NOW + 0.5)
    assert reg.sync("cross_perp", [cand()], NOW + 1.0) == []
    assert reg.active() == []


def test_registry_keep_threshold_holds_but_does_not_open():
    reg = SetupRegistry({"cross_perp": TypePolicy(min_persist_sec=0)})
    assert reg.sync("cross_perp", [cand(opens=False)], NOW) == []
    reg.sync("cross_perp", [cand()], NOW + 1)
    assert len(reg.active()) == 1
    reg.sync("cross_perp", [cand(opens=False, edge=4.0)], NOW + 2)
    assert len(reg.active()) == 1


def test_registry_updates_throttled_and_heartbeat():
    reg = SetupRegistry({"cross_perp": TypePolicy(min_persist_sec=0, update_min_interval_sec=1.0, heartbeat_sec=15.0)})
    reg.sync("cross_perp", [cand(score=50)], NOW)
    assert reg.sync("cross_perp", [cand(score=51)], NOW + 2) == []  # мелкое изменение
    assert reg.sync("cross_perp", [cand(score=70)], NOW + 2.5)[0]["event"] == "updated"
    assert reg.sync("cross_perp", [cand(score=90)], NOW + 2.7) == []  # слишком скоро
    assert reg.sync("cross_perp", [cand(score=90)], NOW + 20)[0]["event"] == "updated"  # heartbeat


def test_registry_sticky_stop_target_expiry_and_stats():
    reg = SetupRegistry()
    long_ = cand(key="breakout:BTC:a:long", edge=None, direction="long", entry=100.0, stop=98.0, targets=[102.0, 104.0], ttl_sec=600, track_venue="a")
    short = cand(key="momentum:ETH:a:continuation:short", edge=None, direction="short", entry=50.0, stop=51.0, targets=[49.0], ttl_sec=600, track_venue="a")
    short.base = "ETH"
    exp = cand(key="btc_lag:SOL:a:long", edge=None, direction="long", entry=10.0, stop=9.0, targets=[12.0], ttl_sec=60, track_venue="a")
    exp.base = "SOL"
    reg.sync("breakout", [long_], NOW)
    reg.sync("momentum", [short], NOW)
    reg.sync("btc_lag", [exp], NOW)
    assert len(reg.active()) == 3
    # sticky не закрывается, если детектор перестал его видеть
    assert reg.sync("breakout", [], NOW + 1) == []
    prices = {"BTC": 102.5, "ETH": 51.5, "SOL": 10.5}
    evs = reg.track(lambda v, b: prices[b], NOW + 10)
    reasons = {e["setup"]["base"]: e["setup"]["close_reason"] for e in evs}
    assert reasons == {"ETH": "stop_hit"}
    prices["BTC"] = 104.1
    evs = reg.track(lambda v, b: prices[b], NOW + 20)
    out = evs[0]["setup"]["outcome"]
    assert evs[0]["setup"]["close_reason"] == "target_hit" and out["targets_hit"] == 2
    assert out["r_multiple"] == pytest.approx(2.05, abs=0.01)
    evs = reg.track(lambda v, b: prices[b], NOW + 61)
    assert evs[0]["setup"]["close_reason"] == "expired" and evs[0]["setup"]["outcome"]["result"] == "expired"
    st = reg.stats()
    assert st["breakout"]["wins"] == 1 and st["momentum"]["losses"] == 1 and st["btc_lag"]["expired"] == 1


def test_registry_events_since_and_listeners():
    reg = SetupRegistry({"cross_perp": TypePolicy(min_persist_sec=0)})
    got = []
    reg.subscribe(got.append)
    reg.sync("cross_perp", [cand(key="cross_perp:BTC:a>b"), cand(key="cross_perp:ETH:a>b")], NOW)
    assert [e["seq"] for e in got] == [1, 2]
    evs, complete = reg.events_since(1)
    assert [e["seq"] for e in evs] == [2] and complete


# ───────────────────────────────────────────────────────── cross perp


def test_cross_perp_finds_net_edge_after_fees():
    cfg = SetupsConfig()
    det = CrossPerpDetector(cfg)
    # mexc ask 100.00, binance bid 100.30 → gross ≈ 30 bps; fees 2×(2+5)=14; exit cross ≈ 1
    v = view_of(pq("mexc", "BTC", 99.99, 100.00), pq("binance", "BTC", 100.30, 100.31))
    c = det.detect(v, NOW)
    assert len(c) == 1 and c[0].key == "cross_perp:BTC:mexc>binance"
    assert c[0].metrics["gross_bps"] == pytest.approx(29.96, abs=0.1)
    assert c[0].edge_bps == pytest.approx(29.96 - 14 - 1.0, abs=0.2) and c[0].opens
    assert [leg.side for leg in c[0].legs] == ["buy", "sell"]


def test_cross_perp_ignores_spread_covered_by_fees_and_ticker_collisions():
    det = CrossPerpDetector(SetupsConfig())
    small = view_of(pq("mexc", "BTC", 99.99, 100.0), pq("binance", "BTC", 100.10, 100.11))
    assert det.detect(small, NOW) == []
    collision = view_of(pq("mexc", "NEIRO", 1.0, 1.001), pq("binance", "NEIRO", 2.0, 2.001))
    assert det.detect(collision, NOW) == []


def test_cross_perp_baseline_removes_persistent_premium():
    cfg = SetupsConfig()
    cfg.cross_perp.baseline_warmup_sec = 0
    det = CrossPerpDetector(cfg)
    v = view_of(pq("mexc", "BTC", 99.99, 100.0), pq("binance", "BTC", 100.30, 100.31))
    for i in range(400):  # binance стабильно дороже на ~30 bps
        det.detect(v, NOW + i * 5)
    assert det.detect(v, NOW + 2005) == []


def test_cross_perp_skips_stale_and_unknown_liquidity_is_flagged():
    det = CrossPerpDetector(SetupsConfig())
    stale = view_of(pq("mexc", "BTC", 99.99, 100.0, age=9), pq("binance", "BTC", 100.30, 100.31))
    assert det.detect(stale, NOW) == []
    requested = []
    det.request_l1 = lambda v, k: requested.append((v, k))
    a = pq("mexc", "BTC", 99.99, 100.0)
    a.ask_qty = None
    c = det.detect(view_of(a, pq("binance", "BTC", 100.30, 100.31)), NOW)[0]
    assert ("mexc", "BTCUSDT") in requested and c.metrics["l1_notional_usd"] == pytest.approx(1003.0, abs=1)


# ───────────────────────────────────────────────────────── funding / basis


def test_funding_arb_normalizes_intervals():
    det = FundingArbDetector(SetupsConfig())
    # bybit +0.1%/8ч (1.25e-4/ч), hyperliquid −0.002%/1ч (−2e-5/ч) → разница 1.45e-4/ч ≈ 127% APR
    v = view_of(
        pq("bybit", "ETH", 3000.0, 3000.3, funding=0.001, interval=8.0, qty=5),
        pq("hyperliquid", "ETH", 2999.9, 3000.2, funding=-0.00002, interval=1.0, qty=5),
    )
    c = det.detect(v, NOW)[0]
    assert c.key == "funding_arb:ETH:hyperliquid>bybit"
    assert c.metrics["funding_apr_pct"] == pytest.approx(127.02, abs=0.1)
    assert c.metrics["income_horizon_bps"] == pytest.approx(69.6, abs=0.1)  # за 48 ч
    assert c.metrics["breakeven_h"] == pytest.approx(15.0, abs=0.5)
    assert c.legs[0].side == "buy" and c.legs[0].venue == "hyperliquid"
    assert c.opens and c.metrics["breakeven_h"] < 24


def test_funding_arb_fees_can_outweigh_moderate_apr():
    det = FundingArbDetector(SetupsConfig())
    det.cfg.horizon_sec = 24 * 3600
    v = view_of(
        pq("bybit", "ETH", 3000.0, 3000.3, funding=0.0005, interval=8.0, qty=5),
        pq("hyperliquid", "ETH", 2999.9, 3000.2, funding=-0.00002, interval=1.0, qty=5),
    )
    c = det.detect(v, NOW)[0]  # 72% APR, но 20 bps комиссий за сутки не отбиваются
    assert c.edge_bps < 0 and not c.opens


def test_funding_arb_below_threshold_ignored():
    det = FundingArbDetector(SetupsConfig())
    v = view_of(pq("bybit", "ETH", 3000.0, 3000.3, funding=0.0001), pq("okx", "ETH", 3000.0, 3000.3, funding=0.00009))
    assert det.detect(v, NOW) == []


def test_basis_cash_and_carry_includes_funding():
    cfg = SetupsConfig()
    det = BasisDetector(cfg)
    # perp на 40 bps выше спота + funding 0.01%/8ч
    v = view_of(pq("bybit", "SOL", 100.40, 100.41, funding=0.0001), spots=[sq("bybit", "SOL", 99.99, 100.00)])
    c = det.detect(v, NOW)[0]
    assert c.metrics["mode"] == "carry" and c.metrics["same_venue"]
    assert c.metrics["funding_carry_bps"] == pytest.approx(1.0)
    # 40 − выход ~1 − 2×(10+5.5)=31 + 1
    assert c.edge_bps == pytest.approx(40.0 - 1.0 - 31.0 + 1.0, abs=0.3)
    assert [(leg.market, leg.side) for leg in c.legs] == [("spot", "buy"), ("perp", "sell")]


def test_basis_reverse_only_when_allowed():
    cfg = SetupsConfig()
    v = view_of(pq("okx", "SOL", 99.50, 99.51), spots=[sq("okx", "SOL", 100.00, 100.01)])
    assert BasisDetector(cfg).detect(v, NOW) == []
    cfg.basis.allow_reverse = True
    c = BasisDetector(cfg).detect(v, NOW)[0]
    assert c.metrics["mode"] == "reverse" and c.metrics["requires_spot_borrow"]


# ───────────────────────────────────────────────────────── lead-lag


def test_lead_lag_detects_lagging_venue():
    det = LeadLagDetector(SetupsConfig())
    det.detect(view_of(pq("binance", "BTC", 100.0, 100.01, vol=1e9), pq("bybit", "BTC", 100.0, 100.01)), NOW)
    det.detect(view_of(pq("binance", "BTC", 100.0, 100.01, vol=1e9), pq("bybit", "BTC", 100.0, 100.01)), NOW + 1)
    # binance +40 bps за 2 с, bybit стоит
    c = det.detect(view_of(pq("binance", "BTC", 100.40, 100.41, vol=1e9), pq("bybit", "BTC", 100.01, 100.02)), NOW + 2.1)
    assert len(c) == 1 and c[0].key == "lead_lag:BTC:binance>bybit:long"
    assert c[0].direction == "long" and c[0].targets[0] > c[0].entry > c[0].stop
    assert c[0].metrics["gap_bps"] == pytest.approx(39, abs=1.5)


# ───────────────────────────────────────────────────────── candles


def _candles(closes, *, vols=None, start=0, open_last=True, spread=0.001):
    vols = vols or [100.0] * len(closes)
    out = []
    prev = closes[0]
    for i, (c, v) in enumerate(zip(closes, vols)):
        o = prev
        out.append(Candle(t=(start + i) * 60_000, o=o, h=max(o, c) * (1 + spread), l=min(o, c) * (1 - spread), c=c, v=v, closed=True))
        prev = c
    if open_last:
        out[-1].closed = False
    return out


def _series(venue, base, candles):
    return Series(venue=venue, meta=InstrumentMeta(venue, f"{base}USDT", f"{base}USDT", base, "USDT"), candles=candles)


def test_breakout_long_on_volume():
    closes = [100.0 + (0.3 if i % 2 else -0.3) for i in range(70)] + [101.5]
    vols = [100.0] * 70 + [800.0]
    s = _series("binance", "AAA", _candles(closes, vols=vols))
    now = (len(closes) - 1) * 60 + 50  # конец незакрытой свечи
    c = BreakoutDetector(SetupsConfig()).detect(view_of(), [s], now)
    assert len(c) == 1 and c[0].direction == "long"
    assert c[0].stop < c[0].metrics["level"] < c[0].entry < c[0].targets[0]
    assert c[0].metrics["volume_ratio"] >= 3


def test_breakout_needs_volume():
    closes = [100.0 + (0.3 if i % 2 else -0.3) for i in range(70)] + [101.5]
    s = _series("binance", "AAA", _candles(closes))
    assert BreakoutDetector(SetupsConfig()).detect(view_of(), [s], (len(closes) - 1) * 60 + 50) == []


def test_momentum_continuation_and_reversion():
    flat = [100.0] * 70
    up = [101.0, 102.0, 103.0, 104.0, 104.5]
    s = _series("bybit", "BBB", _candles(flat + up, vols=[100.0] * 70 + [600.0] * 5))
    c = MomentumDetector(SetupsConfig()).detect(view_of(), [s], (len(flat) + 4) * 60 + 50)
    assert len(c) == 1 and c[0].metrics["bias"] == "continuation" and c[0].direction == "long"

    pump = [102.0, 105.0, 108.0, 106.0, 105.0]  # +8%, затем откат и красная свеча
    candles = _candles(flat + pump, vols=[100.0] * 70 + [800.0] * 5)
    candles[-2].h = 108.5
    s = _series("bybit", "CCC", candles)
    c = MomentumDetector(SetupsConfig()).detect(view_of(), [s], (len(flat) + 4) * 60 + 50)
    assert len(c) == 1 and c[0].metrics["bias"] == "reversion" and c[0].direction == "short"
    assert c[0].stop > c[0].metrics["extreme"]


def test_btc_lag_catch_up_long():
    import random

    rnd = random.Random(7)
    btc, alt = [60000.0], [10.0]
    for _ in range(140):
        r = rnd.gauss(0, 0.001)
        btc.append(btc[-1] * (1 + r))
        alt.append(alt[-1] * (1 + 1.5 * r + rnd.gauss(0, 0.0002)))
    for _ in range(15):  # BTC +2% за 15 мин, альт почти стоит
        btc.append(btc[-1] * (1 + 0.0013))
        alt.append(alt[-1] * (1 + 0.0002))
    series = [_series("binance", "BTC", _candles(btc)), _series("binance", "ALT", _candles(alt))]
    c = BtcLagDetector(SetupsConfig()).detect(view_of(), series, len(btc) * 60)
    assert len(c) == 1 and c[0].base == "ALT" and c[0].direction == "long"
    assert c[0].metrics["corr"] > 0.6 and c[0].metrics["shortfall_pct"] > 0.5
    assert c[0].targets[0] > c[0].entry > c[0].stop


# ───────────────────────────────────────────────────────── funding squeeze


def test_funding_squeeze_short_when_longs_crowded():
    mids = MinuteMids(maxlen=80)
    for i in range(65):
        mids._data.setdefault(("bybit", "DOGE"), deque(maxlen=80)).append(0.1 * (1 + 0.0005 * ((-1) ** i)))
    q = pq("bybit", "DOGE", 0.1, 0.10001, funding=0.002, vol=5e7, oi=2e7)
    det = FundingSqueezeDetector(SetupsConfig(), lambda v, k, w: 0.12, mids)
    c = det.detect(view_of(q, pq("binance", "DOGE", 0.1, 0.10001, funding=0.0001)), NOW)
    assert len(c) == 1 and c[0].direction == "short" and c[0].stop > c[0].entry
    assert c[0].metrics["funding_8h_pct"] == pytest.approx(0.2)
    assert c[0].metrics["oi_change_pct"] == pytest.approx(12.0)


def test_funding_squeeze_needs_oi_growth():
    mids = MinuteMids(maxlen=80)
    for _ in range(65):
        mids._data.setdefault(("bybit", "DOGE"), deque(maxlen=80)).append(0.1)
    det = FundingSqueezeDetector(SetupsConfig(), lambda v, k, w: 0.01, mids)
    assert det.detect(view_of(pq("bybit", "DOGE", 0.1, 0.10001, funding=0.002)), NOW) == []


# ───────────────────────────────────────────────────────── engine + store


class StaticHub(MarketHub):
    def __init__(self, view):
        super().__init__({}, kline_venues=[])
        self._view = view

    def view(self):
        return self._view


def test_engine_tick_opens_cross_perp_and_persists(tmp_path):
    cfg = SetupsConfig()
    hub = StaticHub(view_of(pq("mexc", "BTC", 99.99, 100.0), pq("binance", "BTC", 100.30, 100.31)))
    store = SetupStore(tmp_path / "s.sqlite")
    engine = SetupEngine(hub, cfg, store=store)
    store.start()
    engine.tick(NOW)
    engine.tick(NOW + 2)
    active = engine.registry.active()
    assert [s.key for s in active] == ["cross_perp:BTC:mexc>binance"]
    hub._view = view_of()
    engine.tick(NOW + 10)
    store.stop()
    store._thread.join(timeout=5)
    rows = store.history(limit=10)
    assert rows and rows[0]["setup"]["close_reason"] == "condition_gone"
    assert engine.status()["timings_ms"]["cross_perp"] >= 0


def test_config_file_merges_over_defaults(tmp_path):
    p = tmp_path / "setups.json"
    p.write_text('{"perp_taker_bps": {"mexc": 0.0}, "cross_perp": {"open_edge_bps": 4}, "_comment": "x"}')
    cfg = load_setups_config(p)
    assert cfg.perp_fee("mexc") == 0.0 and cfg.perp_fee("binance") == 5.0
    assert cfg.cross_perp.open_edge_bps == 4 and cfg.cross_perp.keep_edge_bps == 3.0


def test_engine_handles_detector_crash():
    hub = StaticHub(view_of(pq("mexc", "BTC", 99.99, 100.0)))
    engine = SetupEngine(hub, SetupsConfig())

    class Boom:
        type = "cross_perp"

        def detect(self, view, now):
            raise RuntimeError("boom")

    engine.fast = [Boom()]
    engine.tick(time.time())
    assert engine.ticks == 1
