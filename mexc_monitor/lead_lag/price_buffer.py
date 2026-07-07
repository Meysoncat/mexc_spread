"""Thread-safe ring buffer for storing recent price updates per exchange per symbol.

Provides O(1) insert, O(1) latest lookup, time-windowed history retrieval,
and spread computation between leader and lagger exchanges.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Optional

from mexc_monitor.lead_lag.models import PriceSnapshot, SpreadSnapshot


# Maximum entries per (exchange, symbol) deque
_MAX_ENTRIES_PER_PAIR = 18_000

# Maximum points returned by get_history
_MAX_HISTORY_POINTS = 2000


class PriceBuffer:
    """Thread-safe circular buffer storing recent price updates per symbol per exchange.

    Provides:
    - O(1) insert via deque per (exchange, symbol) pair
    - O(1) access to latest PriceSnapshot
    - Time-windowed history with uniform downsampling when exceeding 2000 points
    - Spread computation between leader and lagger
    - Auto-eviction of entries older than max_history_sec on read
    """

    def __init__(self, max_history_sec: float = 60.0) -> None:
        """Initialize the PriceBuffer.

        Args:
            max_history_sec: Maximum age of data to keep in seconds.
        """
        self._max_history_sec = max_history_sec
        self._lock = threading.Lock()
        # Key: (exchange, symbol) -> deque of PriceSnapshot ordered by timestamp_ms
        self._buffers: dict[tuple[str, str], deque[PriceSnapshot]] = defaultdict(
            lambda: deque(maxlen=_MAX_ENTRIES_PER_PAIR)
        )

    @property
    def max_history_sec(self) -> float:
        """Maximum history duration in seconds."""
        return self._max_history_sec

    def update(self, exchange: str, symbol: str, mid: float, timestamp_ms: int) -> None:
        """Insert a new price snapshot into the buffer. O(1) operation.

        Creates a PriceSnapshot with bid=mid, ask=mid (since we only receive mid)
        and appends it to the deque for the (exchange, symbol) pair.

        Args:
            exchange: Exchange identifier (e.g. "binance", "mexc").
            symbol: Trading pair symbol (e.g. "BTCUSDT").
            mid: Mid price.
            timestamp_ms: Timestamp in milliseconds.
        """
        snapshot = PriceSnapshot(
            exchange=exchange,
            symbol=symbol,
            bid=mid,
            ask=mid,
            mid=mid,
            timestamp_ms=timestamp_ms,
        )
        key = (exchange, symbol)
        with self._lock:
            self._buffers[key].append(snapshot)

    def get_latest(self, exchange: str, symbol: str) -> Optional[PriceSnapshot]:
        """Get the most recent PriceSnapshot for (exchange, symbol). O(1) access.

        Returns None if no data exists for the pair.

        Args:
            exchange: Exchange identifier.
            symbol: Trading pair symbol.

        Returns:
            The latest PriceSnapshot or None.
        """
        key = (exchange, symbol)
        with self._lock:
            buf = self._buffers.get(key)
            if not buf:
                return None
            # Evict stale entries from the left
            self._evict_stale(buf)
            if not buf:
                return None
            return buf[-1]

    def get_all_latest(self, symbol: str) -> dict[str, PriceSnapshot]:
        """Get the latest price for all exchanges that have data for the given symbol.

        Args:
            symbol: Trading pair symbol.

        Returns:
            Dict mapping exchange name to its latest PriceSnapshot.
        """
        result: dict[str, PriceSnapshot] = {}
        with self._lock:
            for (exchange, sym), buf in self._buffers.items():
                if sym != symbol:
                    continue
                if not buf:
                    continue
                self._evict_stale(buf)
                if buf:
                    result[exchange] = buf[-1]
        return result

    def get_history(
        self, exchange: str, symbol: str, last_n_sec: float
    ) -> list[PriceSnapshot]:
        """Get time-windowed history for (exchange, symbol).

        Returns an ordered list of PriceSnapshot within the last `last_n_sec` seconds.
        If more than 2000 points exist in the window, applies uniform downsampling.
        Auto-evicts entries older than max_history_sec.

        Args:
            exchange: Exchange identifier.
            symbol: Trading pair symbol.
            last_n_sec: Number of seconds of history to retrieve.

        Returns:
            Ordered list of PriceSnapshot (oldest first), max 2000 points.
        """
        key = (exchange, symbol)
        with self._lock:
            buf = self._buffers.get(key)
            if not buf:
                return []

            # Evict stale entries
            self._evict_stale(buf)
            if not buf:
                return []

            # Determine cutoff timestamp
            now_ms = self._now_ms()
            cutoff_ms = now_ms - int(last_n_sec * 1000)

            # Collect entries within the time window
            # Since deque is ordered by time, find the start index via linear scan from left
            result: list[PriceSnapshot] = []
            for snapshot in buf:
                if snapshot.timestamp_ms >= cutoff_ms:
                    result.append(snapshot)

        # Downsample if exceeding max points
        if len(result) > _MAX_HISTORY_POINTS:
            result = self._downsample(result, _MAX_HISTORY_POINTS)

        return result

    def get_spread(
        self, symbol: str, leader: str, lagger: str
    ) -> Optional[SpreadSnapshot]:
        """Compute the current spread between leader and lagger for a symbol.

        spread_bps = 10000 * (leader_mid - lagger_mid) / leader_mid

        Returns None if:
        - leader_mid <= 0
        - Data for either exchange is missing

        Args:
            symbol: Trading pair symbol.
            leader: Leader exchange identifier.
            lagger: Lagger exchange identifier.

        Returns:
            SpreadSnapshot or None.
        """
        leader_snapshot = self.get_latest(leader, symbol)
        if leader_snapshot is None:
            return None

        lagger_snapshot = self.get_latest(lagger, symbol)
        if lagger_snapshot is None:
            return None

        leader_mid = leader_snapshot.mid
        lagger_mid = lagger_snapshot.mid

        if leader_mid <= 0:
            return None

        spread_abs = leader_mid - lagger_mid
        spread_bps = 10000.0 * spread_abs / leader_mid
        timestamp_ms = max(leader_snapshot.timestamp_ms, lagger_snapshot.timestamp_ms)

        return SpreadSnapshot(
            symbol=symbol,
            leader_exchange=leader,
            lagger_exchange=lagger,
            leader_mid=leader_mid,
            lagger_mid=lagger_mid,
            spread_abs=spread_abs,
            spread_bps=spread_bps,
            timestamp_ms=timestamp_ms,
        )

    def _evict_stale(self, buf: deque[PriceSnapshot]) -> None:
        """Remove entries older than max_history_sec from the left of the deque.

        Must be called while holding self._lock.
        """
        cutoff_ms = self._now_ms() - int(self._max_history_sec * 1000)
        while buf and buf[0].timestamp_ms < cutoff_ms:
            buf.popleft()

    def _now_ms(self) -> int:
        """Current time in milliseconds."""
        return int(time.time() * 1000)

    @staticmethod
    def _downsample(
        snapshots: list[PriceSnapshot], max_points: int
    ) -> list[PriceSnapshot]:
        """Uniformly downsample a list of snapshots to max_points.

        Always includes the first and last elements.
        Selects evenly spaced indices between them.

        Args:
            snapshots: Ordered list of snapshots.
            max_points: Maximum number of points to return.

        Returns:
            Downsampled list of snapshots.
        """
        n = len(snapshots)
        if n <= max_points:
            return snapshots

        # Generate evenly spaced indices including first and last
        step = (n - 1) / (max_points - 1)
        indices = [int(round(i * step)) for i in range(max_points)]
        # Deduplicate while preserving order (in case of rounding collisions)
        seen: set[int] = set()
        unique_indices: list[int] = []
        for idx in indices:
            if idx not in seen:
                seen.add(idx)
                unique_indices.append(idx)

        return [snapshots[i] for i in unique_indices]
