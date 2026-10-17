"""MarketHub — единое состояние рынка в памяти для скринера и генератора сетапов.

Источники:
  * WS-фиды лучших цен perp (Binance, Bybit, OKX, Gate, Bitget, HTX, dYdX) —
    общие с /api/snapshot, второго соединения не открывается;
  * REST-опрос котировок там, где WS не даёт bid/ask (MEXC, Aster,
    Hyperliquid, Lighter);
  * REST-опрос funding / OI / объёма, справочников контрактов, спота;
  * 1m-свечи по WS для направленных сетапов.

Потребители не ходят в сеть: ``view()`` собирает срез из памяти за
миллисекунды, все цены в нём — за одну монету, объёмы — в монетах и USD.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from mexc_monitor.hub.candles import KLINE_WS_VENUES, CandleStore, build_kline_workers
from mexc_monitor.hub.models import InstrumentMeta, PerpQuote, PerpStats, SourceHealth, SpotQuote
from mexc_monitor.hub.symbols import parse_symbol
from mexc_monitor.hub.venues import Book, SpotBook, Venue, default_venues

logger = logging.getLogger(__name__)

WS_STALE_SEC = 10.0
OI_HISTORY_SEC = 3 * 3600
L1_PROBE_TTL_SEC = 5.0


@dataclass(slots=True)
class MarketView:
    """Срез рынка на момент сборки. Детекторы читают только его."""

    ts: float  # epoch, сек
    perps: dict[str, list[PerpQuote]] = field(default_factory=dict)
    spots: dict[str, list[SpotQuote]] = field(default_factory=dict)

    def perp(self, venue: str, base: str) -> PerpQuote | None:
        for q in self.perps.get(base, ()):
            if q.venue == venue:
                return q
        return None


@dataclass(slots=True)
class _Job:
    name: str
    interval: float
    fn: Callable[[], Any]
    next_mono: float = 0.0
    running: bool = False


class MarketHub:
    def __init__(
        self,
        venues: dict[str, Venue] | None = None,
        *,
        enabled: list[str] | None = None,
        spot_venues: list[str] | None = None,
        kline_venues: list[str] | None = None,
        kline_universe: int = 150,
        kline_history: int = 240,
        max_workers: int = 12,
    ) -> None:
        all_venues = venues if venues is not None else default_venues()
        self.venues = {k: v for k, v in all_venues.items() if enabled is None or k in enabled}
        self.spot_venues = [v for v in (spot_venues if spot_venues is not None else list(self.venues)) if v in self.venues and self.venues[v].has_spot]
        self.kline_venues = [v for v in (kline_venues if kline_venues is not None else list(KLINE_WS_VENUES)) if v in self.venues]
        self.kline_universe = kline_universe
        self.kline_history = kline_history

        self._lock = threading.RLock()
        self.meta: dict[str, dict[str, InstrumentMeta]] = {v: {} for v in self.venues}
        self.stats: dict[str, dict[str, PerpStats]] = {v: {} for v in self.venues}
        self.rest_books: dict[str, tuple[float, Book]] = {}
        self.spot_books: dict[str, tuple[float, SpotBook]] = {}
        self.l1_probe: dict[tuple[str, str], tuple[float, float, float]] = {}
        self._l1_requests: deque[tuple[str, str]] = deque(maxlen=200)
        self.oi_hist: dict[tuple[str, str], deque[tuple[float, float]]] = {}
        self.health: dict[str, SourceHealth] = {}
        self.candles = CandleStore(maxlen=kline_history + 60)
        self._kline_workers: dict[str, list] = {}
        self._kline_natives: dict[str, set[str]] = {}
        self._bootstrap: deque[tuple[str, InstrumentMeta]] = deque()

        self._jobs: list[_Job] = []
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="hub")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._build_jobs()

    # ───────────────────────────────────────────────────────── lifecycle

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        for v in self.venues.values():
            try:
                v.ensure_ws()
            except Exception as e:  # noqa: BLE001
                logger.warning("hub: %s ws start failed: %s", v.name, e)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="market-hub", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for workers in self._kline_workers.values():
            for w in workers:
                w.stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _build_jobs(self) -> None:
        for name, v in self.venues.items():
            self._jobs.append(_Job(f"{name}:meta", v.meta_interval, lambda v=v: self._run_meta(v)))
            self._jobs.append(_Job(f"{name}:stats", v.stats_interval, lambda v=v: self._run_stats(v), next_mono=2.0))
            if v.perp_rest_interval > 0:
                self._jobs.append(_Job(f"{name}:perp", v.perp_rest_interval, lambda v=v: self._run_perp_rest(v)))
            if name in self.spot_venues:
                self._jobs.append(_Job(f"{name}:spot", v.spot_interval, lambda v=v: self._run_spot(v), next_mono=1.0))
        if self.kline_venues:
            self._jobs.append(_Job("klines:universe", 1800.0, self._run_kline_universe, next_mono=45.0))
            self._jobs.append(_Job("klines:bootstrap", 1.0, self._run_kline_bootstrap))
        self._jobs.append(_Job("l1:probe", 0.5, self._run_l1_probe))

    def _loop(self) -> None:
        t0 = time.monotonic()
        for j in self._jobs:
            j.next_mono = t0 + j.next_mono
        while not self._stop.is_set():
            now = time.monotonic()
            for j in self._jobs:
                if j.running or now < j.next_mono:
                    continue
                j.running = True
                self._pool.submit(self._exec, j)
            self._stop.wait(0.1)

    def _exec(self, job: _Job) -> None:
        h = self.health.setdefault(job.name, SourceHealth(name=job.name, kind="rest"))
        try:
            n = job.fn()
            h.ok = True
            h.last_ok_mono = time.monotonic()
            h.last_error = None
            h.consecutive_failures = 0
            if isinstance(n, int):
                h.items = n
            delay = job.interval
        except Exception as e:  # noqa: BLE001 — ошибка одной задачи не роняет хаб
            h.ok = False
            h.last_error = f"{type(e).__name__}: {e}"[:300]
            h.consecutive_failures += 1
            delay = min(job.interval * (2 ** min(h.consecutive_failures, 5)), max(job.interval, 300.0))
            if h.consecutive_failures in (1, 5) or h.consecutive_failures % 50 == 0:
                logger.warning("hub job %s failed (%d): %s", job.name, h.consecutive_failures, h.last_error)
        finally:
            job.next_mono = time.monotonic() + delay
            job.running = False

    # ───────────────────────────────────────────────────────── jobs

    def _run_meta(self, v: Venue) -> int:
        metas = v.meta()
        if not metas:
            return 0
        with self._lock:
            self.meta[v.name] = {m.key: m for m in metas}
        return len(metas)

    def _run_stats(self, v: Venue) -> int:
        with self._lock:
            meta = dict(self.meta.get(v.name, {}))
        stats = v.stats(meta)
        if not stats:
            return 0
        now = time.time()
        with self._lock:
            self.stats[v.name] = stats
            for key, st in stats.items():
                if st.open_interest_usd is None:
                    continue
                hist = self.oi_hist.setdefault((v.name, key), deque())
                if hist and now - hist[-1][0] < 50:
                    continue
                hist.append((now, st.open_interest_usd))
                while hist and now - hist[0][0] > OI_HISTORY_SEC:
                    hist.popleft()
        return len(stats)

    def _run_perp_rest(self, v: Venue) -> int:
        book = v.perp_rest()
        if book is None:
            return 0
        metas = v.meta() if not self.meta.get(v.name) else None  # Lighter/HL: из того же ответа
        with self._lock:
            self.rest_books[v.name] = (time.monotonic(), book)
            if metas:
                self.meta[v.name] = {m.key: m for m in metas}
        return len(book)

    def _run_spot(self, v: Venue) -> int:
        book = v.spot()
        if book is None:
            return 0
        with self._lock:
            self.spot_books[v.name] = (time.monotonic(), book)
        return len(book)

    def request_l1(self, venue: str, key: str) -> None:
        """Попросить догрузить объём на лучшей цене (MEXC отдаёт тикер без него)."""
        if venue != "mexc" or venue not in self.venues:
            return
        probe = self.l1_probe.get((venue, key))
        if probe is not None and time.monotonic() - probe[0] < L1_PROBE_TTL_SEC:
            return
        if (venue, key) not in self._l1_requests:
            self._l1_requests.append((venue, key))

    def _run_l1_probe(self) -> int:
        done = 0
        while self._l1_requests and done < 5:
            venue, key = self._l1_requests.popleft()
            m = self.meta.get(venue, {}).get(key)
            v = self.venues.get(venue)
            if m is None or v is None or not hasattr(v, "depth_l1"):
                continue
            got = v.depth_l1(m.native)
            if got is not None:
                self.l1_probe[(venue, key)] = (time.monotonic(), got[0], got[1])
            done += 1
        return done

    def _run_kline_universe(self) -> int:
        total = 0
        for name in self.kline_venues:
            with self._lock:
                meta = dict(self.meta.get(name, {}))
                stats = dict(self.stats.get(name, {}))
            ranked = sorted(
                (k for k, m in meta.items() if m.active and k in stats),
                key=lambda k: -(stats[k].volume_24h_usd or 0.0),
            )[: self.kline_universe]
            natives = {meta[k].native for k in ranked}
            if not natives or natives == self._kline_natives.get(name):
                total += len(natives)
                continue
            for w in self._kline_workers.get(name, []):
                w.stop.set()
            by_native = {meta[k].native: meta[k] for k in ranked}

            def sink(key: str, c, name=name) -> None:
                self.candles.apply(name, key, c)

            workers = build_kline_workers(name, sorted(natives), sink)
            for w in workers:
                w.start()
            self._kline_workers[name] = workers
            added = natives - self._kline_natives.get(name, set())
            self._kline_natives[name] = natives
            self.candles.drop(name, {meta[k].key for k in ranked})
            for n in sorted(added):
                self._bootstrap.append((name, by_native[n]))
            total += len(natives)
        return total

    def _run_kline_bootstrap(self) -> int:
        done = 0
        while self._bootstrap and done < 4:
            name, m = self._bootstrap.popleft()
            try:
                history = self.venues[name].klines(m, self.kline_history)
            except Exception as e:  # noqa: BLE001
                logger.debug("kline bootstrap %s %s failed: %s", name, m.native, e)
                continue
            if history:
                self.candles.merge_history(name, m.key, history)
            done += 1
        return done

    # ───────────────────────────────────────────────────────── views

    def _perp_book(self, v: Venue) -> tuple[Book, float] | None:
        ws = v.ws_book()
        if ws is not None:
            age, book = ws
            if age is not None and age <= WS_STALE_SEC and book:
                return book, age
        rest = self.rest_books.get(v.name)
        if rest is not None:
            age = time.monotonic() - rest[0]
            if age <= max(3 * v.perp_rest_interval, WS_STALE_SEC):
                return rest[1], age
        return None

    def view(self) -> MarketView:
        now_mono = time.monotonic()
        view = MarketView(ts=time.time())
        with self._lock:
            for name, v in self.venues.items():
                got = self._perp_book(v)
                if got is None:
                    continue
                book, age = got
                meta = self.meta.get(name, {})
                stats = self.stats.get(name, {})
                indicative = bool(getattr(v, "indicative", False))
                for key, (bid, bq, ask, aq) in book.items():
                    m = meta.get(key)
                    if m is None:
                        if meta:
                            continue  # справочник есть, а ключа нет — делистинг/не USDT
                        m = _fallback_meta(name, key, v.quote_ccy)
                        if m is None:
                            continue
                    if not m.active:
                        continue
                    mult = m.price_mult
                    to_base = m.qty_to_base
                    probe = self.l1_probe.get((name, key))
                    if probe is not None and now_mono - probe[0] < L1_PROBE_TTL_SEC:
                        bq, aq = probe[1], probe[2]
                    st = stats.get(key)
                    q = PerpQuote(
                        venue=name,
                        key=key,
                        native=m.native,
                        base=m.base,
                        quote=m.quote,
                        bid=bid / mult,
                        ask=ask / mult,
                        bid_qty=bq * to_base if bq and to_base > 0 else None,
                        ask_qty=aq * to_base if aq and to_base > 0 else None,
                        age_sec=age,
                        price_mult=mult,
                        indicative=indicative,
                    )
                    if st is not None:
                        q.funding_rate = st.funding_rate
                        q.funding_interval_h = st.funding_interval_h or m.funding_interval_h or 8.0
                        q.next_funding_ms = st.next_funding_ms
                        q.open_interest_usd = st.open_interest_usd
                        q.volume_24h_usd = st.volume_24h_usd
                    elif m.funding_interval_h:
                        q.funding_interval_h = m.funding_interval_h
                    view.perps.setdefault(m.base, []).append(q)

            for name in self.spot_venues:
                got = self.spot_books.get(name)
                if got is None:
                    continue
                ts, book = got
                age = now_mono - ts
                if age > 3 * self.venues[name].spot_interval + 5:
                    continue
                for key, (bid, bq, ask, aq, vol) in book.items():
                    parsed = parse_symbol(key)
                    if parsed is None or parsed[1] != "USDT":
                        continue
                    base, quote, mult = parsed
                    view.spots.setdefault(base, []).append(
                        SpotQuote(
                            venue=name,
                            key=key,
                            native=key,
                            base=base,
                            quote=quote,
                            bid=bid / mult,
                            ask=ask / mult,
                            bid_qty=bq * mult if bq else None,
                            ask_qty=aq * mult if aq else None,
                            age_sec=age,
                            volume_24h_usd=vol,
                            price_mult=mult,
                        )
                    )
        return view

    def oi_change(self, venue: str, key: str, window_sec: float) -> float | None:
        """Относительное изменение OI за окно (0.1 = +10%) или None."""
        with self._lock:
            hist = self.oi_hist.get((venue, key))
            if not hist or len(hist) < 2:
                return None
            latest_t, latest = hist[-1]
            ref = None
            for t, v in hist:
                if latest_t - t <= window_sec:
                    ref = v
                    break
            if ref is None or ref <= 0 or latest_t - hist[0][0] < window_sec * 0.8:
                return None
            return latest / ref - 1.0

    def meta_for(self, venue: str, key: str) -> InstrumentMeta | None:
        with self._lock:
            return self.meta.get(venue, {}).get(key)

    def health_report(self) -> dict[str, Any]:
        now = time.monotonic()
        venues: dict[str, Any] = {}
        for name, v in self.venues.items():
            ws = v.ws_book()
            rest = self.rest_books.get(name)
            venues[name] = {
                "instruments": len(self.meta.get(name, {})),
                "stats": len(self.stats.get(name, {})),
                "ws_age_sec": round(ws[0], 1) if ws and ws[0] is not None else None,
                "ws_symbols": len(ws[1]) if ws else 0,
                "rest_age_sec": round(now - rest[0], 1) if rest else None,
                "live": self._perp_book(v) is not None,
            }
        jobs = {
            name: {
                "ok": h.ok,
                "age_sec": round(now - h.last_ok_mono, 1) if h.last_ok_mono else None,
                "items": h.items,
                "failures": h.consecutive_failures,
                "error": h.last_error,
            }
            for name, h in sorted(self.health.items())
        }
        klines = {
            name: {
                "symbols": len(self._kline_natives.get(name, ())),
                "series": len(self.candles.keys(name)),
                "pending_bootstrap": sum(1 for b in self._bootstrap if b[0] == name),
            }
            for name in self.kline_venues
        }
        return {"venues": venues, "jobs": jobs, "klines": klines}


def _fallback_meta(venue: str, key: str, default_quote: str) -> InstrumentMeta | None:
    """Мета до первой загрузки справочника. Объёмы контрактных бирж неизвестны."""
    parsed = parse_symbol(key, default_quote=default_quote)
    if parsed is None:
        return None
    base, quote, mult = parsed
    contract_venues = {"okx", "gateio", "htx", "mexc"}
    m = InstrumentMeta(venue=venue, key=key, native=key, base=base, quote=quote, price_mult=mult)
    if venue in contract_venues:
        m.contract_size = 0.0  # → qty_to_base 0 → объём «неизвестен»
    return m
