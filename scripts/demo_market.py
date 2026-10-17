"""Демо-режим: синтетический рынок вместо бирж.

Запускает тот же бэкенд и генератор сетапов, но котировки берутся из
случайного блуждания с периодическими расхождениями между биржами, разным
funding, премией perp к споту и пробоями на свечах. Нужен, чтобы проверить UI
и интеграцию потребителей без доступа к биржам.

    python scripts/demo_market.py            # http://127.0.0.1:8000
    python scripts/demo_market.py --port 8010
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mexc_monitor.hub.hub import MarketHub, MarketView  # noqa: E402
from mexc_monitor.hub.models import Candle, InstrumentMeta, PerpQuote, SpotQuote  # noqa: E402

VENUES = ["binance", "bybit", "okx", "gateio", "bitget", "mexc", "hyperliquid"]
SPOT_VENUES = ["binance", "bybit", "okx", "mexc"]
BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "WIF", "SUI", "LINK", "AVAX", "TON", "ARB", "OP", "APT", "NEAR", "TIA", "SEI", "INJ", "ORDI", "JUP"]


class SimHub(MarketHub):
    def __init__(self, seed: int = 42) -> None:
        super().__init__({}, kline_venues=[])
        self.rnd = random.Random(seed)
        self.price = {b: self.rnd.uniform(0.5, 50_000) if b != "PEPE" else 0.00001 for b in BASES}
        self.price.update({"BTC": 65_000.0, "ETH": 3_000.0, "SOL": 150.0})
        self.premium = {(b, v): self.rnd.gauss(0, 3) for b in BASES for v in VENUES}  # bps
        self.funding = {(b, v): self.rnd.gauss(0.00005, 0.0001) for b in BASES for v in VENUES}
        self.funding[("DOGE", "bybit")] = 0.0015  # перегретые лонги
        self.funding[("SUI", "hyperliquid")] = -0.00004
        self.funding[("SUI", "gateio")] = 0.0009
        self.dislocation: tuple[str, str, float, float] | None = None  # base, venue, bps, until
        self.t0 = time.time()
        self.kline_venues = ["binance"]
        self._seed_candles()
        from collections import deque

        for b in BASES:  # OI растёт — для funding/OI-сквиза
            self.oi_hist[("bybit", f"{b}USDT")] = deque([(self.t0 - 3600 + i * 60, 1e7 * (1 + 0.003 * i)) for i in range(61)])

    def _seed_candles(self) -> None:
        now_min = int(time.time() // 60)
        for b in BASES:
            meta = InstrumentMeta("binance", f"{b}USDT", f"{b}USDT", b, "USDT")
            self.meta.setdefault("binance", {})[meta.key] = meta
            p = self.price[b]
            breakout = b in ("SOL", "LINK")
            for i in range(150, 0, -1):
                t = (now_min - i) * 60_000
                o = p
                if breakout and i <= 1:
                    c, v = p * 1.012, 900.0
                else:
                    c, v = p * (1 + self.rnd.gauss(0, 0.0015)), 100.0 * self.rnd.uniform(0.7, 1.3)
                h, l = max(o, c) * 1.0008, min(o, c) * 0.9992
                self.candles.apply("binance", meta.key, Candle(t=t, o=o, h=h, l=l, c=c, v=v, qv=v * c, closed=i > 1))
                p = c
            self.price[b] = p

    def start(self) -> None:  # без сети
        pass

    def stop(self) -> None:
        pass

    def view(self) -> MarketView:
        now = time.time()
        rnd = self.rnd
        for b in BASES:
            self.price[b] *= 1 + rnd.gauss(0, 0.0002)
        if self.dislocation is None or now > self.dislocation[3]:
            if rnd.random() < 0.02:
                self.dislocation = (rnd.choice(BASES), rnd.choice(VENUES), rnd.choice([-1, 1]) * rnd.uniform(25, 60), now + rnd.uniform(5, 20))
            elif self.dislocation is not None and now > self.dislocation[3]:
                self.dislocation = None
        view = MarketView(ts=now)
        for b in BASES:
            mid = self.price[b]
            for v in VENUES:
                prem = self.premium[(b, v)]
                if self.dislocation and self.dislocation[0] == b and self.dislocation[1] == v:
                    prem += self.dislocation[2]
                m = mid * (1 + prem / 1e4)
                half = m * rnd.uniform(0.5, 2.0) / 1e4
                qty = rnd.uniform(2_000, 80_000) / m
                interval = 1.0 if v == "hyperliquid" else 8.0
                view.perps.setdefault(b, []).append(
                    PerpQuote(
                        venue=v, key=f"{b}USDT" if v != "hyperliquid" else b, native=_native(v, b), base=b, quote="USDT",
                        bid=m - half, ask=m + half, bid_qty=qty, ask_qty=qty, age_sec=rnd.uniform(0.05, 0.8),
                        funding_rate=self.funding[(b, v)] * (interval / 8.0), funding_interval_h=interval,
                        next_funding_ms=int((now // 28800 + 1) * 28800 * 1000), open_interest_usd=1e7 * (1 + 0.003 * 60),
                        volume_24h_usd=5e7 * (1 + math.sin(hash(b) % 7)), indicative=v == "hyperliquid",
                    )
                )
            for v in SPOT_VENUES:
                prem = -18.0 if (b == "ETH" and v == "okx") else 0.0
                m = mid * (1 + prem / 1e4)
                half = m * rnd.uniform(0.5, 2.0) / 1e4
                view.spots.setdefault(b, []).append(
                    SpotQuote(venue=v, key=f"{b}USDT", native=f"{b}USDT", base=b, quote="USDT", bid=m - half, ask=m + half, bid_qty=10_000 / m, ask_qty=10_000 / m, age_sec=1.0)
                )
        return view


def _native(venue: str, base: str) -> str:
    return {
        "okx": f"{base}-USDT-SWAP",
        "gateio": f"{base}_USDT",
        "mexc": f"{base}_USDT",
        "hyperliquid": base,
    }.get(venue, f"{base}USDT")


def main() -> None:
    import os

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    os.environ["SCREENER_ENABLED"] = "0"  # реальный рантайм не стартует — подставим демо
    from backend import screener
    from backend.main import app
    from mexc_monitor.setups.config import SetupsConfig

    cfg = SetupsConfig()
    cfg.history_db = "data/demo_setups.sqlite"
    cfg.cross_perp.baseline_warmup_sec = 3600  # в демо базлайн не прогревается
    rt = screener.build_runtime(cfg, SimHub())
    screener.set_runtime(rt)
    rt.store.start()
    rt.engine.start()
    print(f"demo market: http://{args.host}:{args.port}  (UI — после `npm run build` в frontend/)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
