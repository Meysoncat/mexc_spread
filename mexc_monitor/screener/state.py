"""Per-symbol screener state: spread persistence + rolling statistics.

Closes the gap that no backend metric existed for "how long has the spread
stayed above threshold" (lifetime) and "how stable / how often above" (rolling
pct_above / std). Updated from successive spot snapshots at ~2-3 s cadence.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque


class ScreenerState:
    """Thread-safe per-symbol persistence + rolling-spread state."""

    def __init__(self, rolling_window: int = 40) -> None:
        self._lock = threading.Lock()
        self._rolling_window = max(5, int(rolling_window))
        # symbol -> timestamp_ms when spread first reached the threshold
        self._first_above_ms: dict[str, float] = {}
        # symbol -> deque[(ts_ms, spread_bps)]
        self._recent: dict[str, deque] = {}

    @property
    def rolling_window(self) -> int:
        return self._rolling_window

    def set_rolling_window(self, n: int) -> None:
        with self._lock:
            self._rolling_window = max(5, int(n))
            for sym, dq in self._recent.items():
                if len(dq) > self._rolling_window:
                    self._recent[sym] = deque(dq, maxlen=self._rolling_window)

    def update(
        self,
        symbol: str,
        spread_bps: float | None,
        threshold: float,
        now_ms: float,
    ) -> None:
        """Record one observation. Resets lifetime if spread fell below threshold."""
        sym = symbol.upper()
        with self._lock:
            dq = self._recent.get(sym)
            if dq is None:
                dq = deque(maxlen=self._rolling_window)
                self._recent[sym] = dq
            if spread_bps is not None:
                dq.append((now_ms, float(spread_bps)))

            if spread_bps is not None and spread_bps >= threshold:
                self._first_above_ms.setdefault(sym, now_ms)
            else:
                self._first_above_ms.pop(sym, None)

    def get_lifetime(self, symbol: str, now_ms: float) -> float:
        sym = symbol.upper()
        with self._lock:
            first = self._first_above_ms.get(sym)
        if not first:
            return 0.0
        return max(0.0, (now_ms - first) / 1000.0)

    def get_rolling(
        self, symbol: str, threshold: float
    ) -> tuple[float, float | None]:
        """Return (pct_above 0..100, spread_std_bps) over the recent window."""
        sym = symbol.upper()
        with self._lock:
            dq = self._recent.get(sym)
            if not dq:
                return (0.0, None)
            spreads = [s for _, s in dq]
        if not spreads:
            return (0.0, None)
        above = sum(1 for s in spreads if s >= threshold)
        pct = 100.0 * above / len(spreads)
        mean = sum(spreads) / len(spreads)
        var = (
            sum((s - mean) ** 2 for s in spreads) / (len(spreads) - 1)
            if len(spreads) > 1
            else 0.0
        )
        return (pct, math.sqrt(var))

    def prune(self, active_symbols: set[str]) -> None:
        """Drop state for symbols no longer present in the snapshot."""
        active = {s.upper() for s in active_symbols}
        with self._lock:
            for sym in list(self._recent.keys()):
                if sym not in active:
                    self._recent.pop(sym, None)
                    self._first_above_ms.pop(sym, None)

    def reset(self, symbol: str | None = None) -> None:
        with self._lock:
            if symbol is None:
                self._recent.clear()
                self._first_above_ms.clear()
            else:
                sym = symbol.upper()
                self._recent.pop(sym, None)
                self._first_above_ms.pop(sym, None)

    def tracked_count(self) -> int:
        with self._lock:
            return len(self._recent)
