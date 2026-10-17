"""Рантайм скринера: MarketHub + SetupEngine + рассылка событий подписчикам API.

Движок работает в своих потоках; события в asyncio-подписчиков (SSE/WS)
передаются через ``loop.call_soon_threadsafe`` в ограниченные очереди.
Медленный подписчик не тормозит движок: его очередь переполняется, поток
закрывается, клиент переподключается с Last-Event-ID и дочитывает пропуски.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from mexc_monitor.hub.hub import MarketHub
from mexc_monitor.setups.config import SetupsConfig, load_setups_config
from mexc_monitor.setups.engine import SetupEngine
from mexc_monitor.setups.registry import SetupRegistry
from mexc_monitor.setups.store import SetupStore

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(eq=False)
class Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue
    filters: dict[str, Any] = field(default_factory=dict)
    overflowed: bool = False


class Broadcaster:
    def __init__(self, queue_size: int = 2000) -> None:
        self._lock = threading.Lock()
        self._subs: set[Subscriber] = set()
        self.queue_size = queue_size

    def add(self, filters: dict[str, Any]) -> Subscriber:
        sub = Subscriber(loop=asyncio.get_running_loop(), queue=asyncio.Queue(self.queue_size), filters=filters)
        with self._lock:
            self._subs.add(sub)
        return sub

    def remove(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs.discard(sub)

    def __len__(self) -> int:
        return len(self._subs)

    def publish(self, ev: dict[str, Any]) -> None:
        """Вызывается из потока движка."""
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            if sub.overflowed or not matches(ev["setup"], sub.filters):
                continue
            try:
                sub.loop.call_soon_threadsafe(_offer, sub, ev)
            except RuntimeError:  # цикл подписчика уже закрыт
                self.remove(sub)


def _offer(sub: Subscriber, ev: dict[str, Any]) -> None:
    try:
        sub.queue.put_nowait(ev)
    except asyncio.QueueFull:
        sub.overflowed = True
        try:
            sub.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass


def parse_filters(
    *,
    type: str | None = None,
    category: str | None = None,
    base: str | None = None,
    venue: str | None = None,
    direction: str | None = None,
    min_score: float | None = None,
) -> dict[str, Any]:
    def csv(v: str | None, upper: bool = False) -> set[str] | None:
        if not v:
            return None
        items = {x.strip() for x in v.split(",") if x.strip()}
        return {x.upper() for x in items} if upper else {x.lower() for x in items}

    return {
        "type": csv(type),
        "category": csv(category),
        "base": csv(base, upper=True),
        "venue": csv(venue),
        "direction": csv(direction),
        "min_score": min_score,
    }


def matches(setup: dict[str, Any], f: dict[str, Any]) -> bool:
    if not f:
        return True
    if f.get("type") and setup["type"] not in f["type"]:
        return False
    if f.get("category") and setup["category"] not in f["category"]:
        return False
    if f.get("base") and setup["base"] not in f["base"]:
        return False
    if f.get("direction") and (setup.get("direction") or "") not in f["direction"]:
        return False
    if f.get("venue") and not any(leg["venue"] in f["venue"] for leg in setup["legs"]):
        return False
    # закрытие пропускаем всегда: подписчик должен узнать, что идея ушла
    if f.get("min_score") is not None and setup["status"] == "active" and (setup.get("score") or 0) < f["min_score"]:
        return False
    return True


@dataclass
class ScreenerRuntime:
    cfg: SetupsConfig
    hub: MarketHub
    registry: SetupRegistry
    engine: SetupEngine
    store: SetupStore | None
    broadcaster: Broadcaster

    def start(self) -> None:
        self.hub.start()
        self.engine.start()

    def stop(self) -> None:
        self.engine.stop()
        self.hub.stop()


def build_runtime(cfg: SetupsConfig | None = None, hub: MarketHub | None = None, *, with_store: bool = True) -> ScreenerRuntime:
    cfg = cfg or load_setups_config()
    if hub is None:
        hub = MarketHub(
            enabled=cfg.venues,
            kline_venues=cfg.kline_venues,
            kline_universe=cfg.kline_universe,
        )
    registry = SetupRegistry()
    store = None
    if with_store:
        path = cfg.history_db if os.path.isabs(cfg.history_db) else os.path.join(_ROOT, cfg.history_db)
        try:
            store = SetupStore(path, retention_days=cfg.history_retention_days)
        except Exception as e:  # noqa: BLE001 — без журнала скринер всё равно работает
            logger.error("setup store unavailable (%s): %s", path, e)
    engine = SetupEngine(hub, cfg, registry=registry, store=store)
    broadcaster = Broadcaster()
    registry.subscribe(broadcaster.publish)
    return ScreenerRuntime(cfg=cfg, hub=hub, registry=registry, engine=engine, store=store, broadcaster=broadcaster)


_runtime: ScreenerRuntime | None = None


def get_runtime() -> ScreenerRuntime | None:
    return _runtime


def set_runtime(rt: ScreenerRuntime | None) -> None:
    global _runtime
    _runtime = rt


def start_runtime() -> None:
    global _runtime
    if os.environ.get("SCREENER_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        logger.info("screener disabled by SCREENER_ENABLED")
        return
    if _runtime is None:
        _runtime = build_runtime()
    _runtime.start()
    logger.info("screener started: venues=%s detectors=%s", list(_runtime.hub.venues), _runtime.engine.status()["detectors"])


def stop_runtime() -> None:
    if _runtime is not None:
        _runtime.stop()
