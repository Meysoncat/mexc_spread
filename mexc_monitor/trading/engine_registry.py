"""Engine Registry — singleton managing multiple TradingEngine instances.

Provides thread-safe get-or-create semantics indexed by (exchange, market) composite key.
Each engine instance is independently configured with its own settings and private client.
"""

from __future__ import annotations

import threading
from typing import Any

from mexc_monitor.trading.client_factory import create_private_client
from mexc_monitor.trading.exchanges import EngineKey, Exchange, Market
from mexc_monitor.trading.settings_loader import load_trading_settings_for_exchange


class EngineRegistry:
    """Singleton registry managing TradingEngine instances by (exchange, market) key.

    Thread-safe: all mutations are protected by internal locks.
    Use `get_or_create()` to obtain an engine — creates on first access.
    """

    _instance: "EngineRegistry | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "EngineRegistry":
        with cls._lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._engines: dict[EngineKey, Any] = {}
                inst._engines_lock = threading.Lock()
                cls._instance = inst
            return cls._instance

    def get_or_create(self, exchange: Exchange, market: Market) -> Any:
        """Return existing engine or create a new one for the given exchange+market.

        On first call for a key, loads settings and creates a private client,
        then instantiates a TradingEngine configured for that exchange/market.
        Subsequent calls with the same key return the same instance.
        """
        # Import here to avoid circular imports (engine imports from this package)
        from mexc_monitor.trading.engine import TradingEngine

        key = EngineKey(exchange=exchange, market=market)
        with self._engines_lock:
            if key not in self._engines:
                settings = load_trading_settings_for_exchange(exchange, market)
                client = create_private_client(exchange, market)
                engine = TradingEngine(
                    settings=settings,
                    private_client=client,
                    exchange=exchange,
                    market=market,
                )
                self._engines[key] = engine
            return self._engines[key]

    def get(self, exchange: Exchange, market: Market) -> Any | None:
        """Return engine if it exists for the given key, None otherwise."""
        key = EngineKey(exchange=exchange, market=market)
        with self._engines_lock:
            return self._engines.get(key)

    def list_engines(self) -> list[dict[str, Any]]:
        """Return metadata for all registered engines."""
        with self._engines_lock:
            result = []
            for key, engine in self._engines.items():
                result.append({
                    "exchange": key.exchange.value,
                    "market": key.market.value,
                    "running": engine._state.running,
                    "mode": engine._state.mode,
                    "symbol": engine._state.symbol,
                })
            return result

    def shutdown_all(self) -> None:
        """Stop all running engines gracefully."""
        with self._engines_lock:
            for engine in self._engines.values():
                if engine._state.running:
                    engine.stop()

    @classmethod
    def reset(cls) -> None:
        """Reset singleton instance (for testing only).

        Shuts down all running engines before clearing the registry.
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown_all()
                cls._instance._engines.clear()
            cls._instance = None
