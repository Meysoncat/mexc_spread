"""Unit tests for the PriceBuffer class."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from mexc_monitor.lead_lag.price_buffer import PriceBuffer, _MAX_HISTORY_POINTS


class TestPriceBufferUpdate:
    """Tests for PriceBuffer.update()."""

    def test_update_stores_snapshot(self):
        """update() should store a PriceSnapshot retrievable via get_latest."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)
        buf.update("binance", "BTCUSDT", 67500.0, now_ms)

        latest = buf.get_latest("binance", "BTCUSDT")
        assert latest is not None
        assert latest.exchange == "binance"
        assert latest.symbol == "BTCUSDT"
        assert latest.mid == 67500.0
        assert latest.timestamp_ms == now_ms

    def test_update_multiple_entries(self):
        """Multiple updates should all be stored, latest accessible."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 67500.0, now_ms - 1000)
        buf.update("binance", "BTCUSDT", 67510.0, now_ms)

        latest = buf.get_latest("binance", "BTCUSDT")
        assert latest is not None
        assert latest.mid == 67510.0

    def test_update_different_pairs(self):
        """Updates for different (exchange, symbol) pairs are independent."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 67500.0, now_ms)
        buf.update("mexc", "BTCUSDT", 67490.0, now_ms)
        buf.update("binance", "ETHUSDT", 3500.0, now_ms)

        assert buf.get_latest("binance", "BTCUSDT").mid == 67500.0
        assert buf.get_latest("mexc", "BTCUSDT").mid == 67490.0
        assert buf.get_latest("binance", "ETHUSDT").mid == 3500.0


class TestPriceBufferGetLatest:
    """Tests for PriceBuffer.get_latest()."""

    def test_returns_none_for_unknown_pair(self):
        """get_latest() returns None for a pair with no data."""
        buf = PriceBuffer(max_history_sec=60.0)
        assert buf.get_latest("binance", "BTCUSDT") is None

    def test_returns_most_recent_snapshot(self):
        """get_latest() returns the most recently inserted snapshot."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 100.0, now_ms - 2000)
        buf.update("binance", "BTCUSDT", 200.0, now_ms - 1000)
        buf.update("binance", "BTCUSDT", 300.0, now_ms)

        latest = buf.get_latest("binance", "BTCUSDT")
        assert latest.mid == 300.0

    def test_evicts_stale_data_on_read(self):
        """get_latest() evicts data older than max_history_sec."""
        buf = PriceBuffer(max_history_sec=5.0)
        old_ms = int(time.time() * 1000) - 10_000  # 10 seconds ago

        buf.update("binance", "BTCUSDT", 100.0, old_ms)

        # All data is stale, should return None
        assert buf.get_latest("binance", "BTCUSDT") is None


class TestPriceBufferGetAllLatest:
    """Tests for PriceBuffer.get_all_latest()."""

    def test_returns_empty_for_unknown_symbol(self):
        """get_all_latest() returns empty dict for unknown symbol."""
        buf = PriceBuffer(max_history_sec=60.0)
        assert buf.get_all_latest("UNKNOWN") == {}

    def test_returns_all_exchanges_for_symbol(self):
        """get_all_latest() returns latest price from each exchange."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 67500.0, now_ms)
        buf.update("mexc", "BTCUSDT", 67490.0, now_ms)
        buf.update("bybit", "BTCUSDT", 67495.0, now_ms)

        result = buf.get_all_latest("BTCUSDT")
        assert len(result) == 3
        assert result["binance"].mid == 67500.0
        assert result["mexc"].mid == 67490.0
        assert result["bybit"].mid == 67495.0

    def test_excludes_other_symbols(self):
        """get_all_latest() only returns data for the requested symbol."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 67500.0, now_ms)
        buf.update("binance", "ETHUSDT", 3500.0, now_ms)

        result = buf.get_all_latest("BTCUSDT")
        assert len(result) == 1
        assert "binance" in result


class TestPriceBufferGetHistory:
    """Tests for PriceBuffer.get_history()."""

    def test_returns_empty_for_unknown_pair(self):
        """get_history() returns empty list for unknown pair."""
        buf = PriceBuffer(max_history_sec=60.0)
        assert buf.get_history("binance", "BTCUSDT", 10.0) == []

    def test_returns_entries_within_window(self):
        """get_history() returns only entries within the time window."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        # Insert entries at different times
        buf.update("binance", "BTCUSDT", 100.0, now_ms - 5000)
        buf.update("binance", "BTCUSDT", 200.0, now_ms - 3000)
        buf.update("binance", "BTCUSDT", 300.0, now_ms - 1000)

        # Request last 4 seconds
        history = buf.get_history("binance", "BTCUSDT", 4.0)
        assert len(history) == 2
        assert history[0].mid == 200.0
        assert history[1].mid == 300.0

    def test_returns_ordered_by_time(self):
        """get_history() returns entries ordered oldest first."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 100.0, now_ms - 3000)
        buf.update("binance", "BTCUSDT", 200.0, now_ms - 2000)
        buf.update("binance", "BTCUSDT", 300.0, now_ms - 1000)

        history = buf.get_history("binance", "BTCUSDT", 60.0)
        timestamps = [s.timestamp_ms for s in history]
        assert timestamps == sorted(timestamps)

    def test_downsamples_when_exceeding_max_points(self):
        """get_history() downsamples to 2000 points when exceeding limit."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        # Insert 3000 entries within the last 30 seconds
        for i in range(3000):
            ts = now_ms - 30000 + i * 10  # 10ms apart
            buf.update("binance", "BTCUSDT", 100.0 + i * 0.01, ts)

        history = buf.get_history("binance", "BTCUSDT", 60.0)
        assert len(history) <= _MAX_HISTORY_POINTS

    def test_downsampled_includes_first_and_last(self):
        """Downsampled history includes the first and last entries."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        entries = []
        for i in range(3000):
            ts = now_ms - 30000 + i * 10
            mid = 100.0 + i
            buf.update("binance", "BTCUSDT", mid, ts)
            entries.append((mid, ts))

        history = buf.get_history("binance", "BTCUSDT", 60.0)
        # First entry should be the oldest in window
        assert history[0].timestamp_ms == entries[0][1]
        # Last entry should be the newest
        assert history[-1].timestamp_ms == entries[-1][1]


class TestPriceBufferGetSpread:
    """Tests for PriceBuffer.get_spread()."""

    def test_computes_spread_correctly(self):
        """get_spread() computes spread_bps = 10000 * (leader - lagger) / leader."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 67500.0, now_ms)
        buf.update("mexc", "BTCUSDT", 67490.0, now_ms)

        spread = buf.get_spread("BTCUSDT", "binance", "mexc")
        assert spread is not None
        assert spread.symbol == "BTCUSDT"
        assert spread.leader_exchange == "binance"
        assert spread.lagger_exchange == "mexc"
        assert spread.leader_mid == 67500.0
        assert spread.lagger_mid == 67490.0
        assert spread.spread_abs == pytest.approx(10.0)
        expected_bps = 10000.0 * 10.0 / 67500.0
        assert spread.spread_bps == pytest.approx(expected_bps)

    def test_returns_none_when_leader_missing(self):
        """get_spread() returns None when leader has no data."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("mexc", "BTCUSDT", 67490.0, now_ms)

        spread = buf.get_spread("BTCUSDT", "binance", "mexc")
        assert spread is None

    def test_returns_none_when_lagger_missing(self):
        """get_spread() returns None when lagger has no data."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 67500.0, now_ms)

        spread = buf.get_spread("BTCUSDT", "binance", "mexc")
        assert spread is None

    def test_returns_none_when_leader_mid_zero(self):
        """get_spread() returns None when leader_mid is 0."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 0.0, now_ms)
        buf.update("mexc", "BTCUSDT", 67490.0, now_ms)

        spread = buf.get_spread("BTCUSDT", "binance", "mexc")
        assert spread is None

    def test_returns_none_when_leader_mid_negative(self):
        """get_spread() returns None when leader_mid is negative."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", -100.0, now_ms)
        buf.update("mexc", "BTCUSDT", 67490.0, now_ms)

        spread = buf.get_spread("BTCUSDT", "binance", "mexc")
        assert spread is None

    def test_negative_spread_when_lagger_higher(self):
        """get_spread() can return negative spread_bps when lagger > leader."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 67490.0, now_ms)
        buf.update("mexc", "BTCUSDT", 67500.0, now_ms)

        spread = buf.get_spread("BTCUSDT", "binance", "mexc")
        assert spread is not None
        assert spread.spread_bps < 0


class TestPriceBufferEviction:
    """Tests for auto-eviction of stale data."""

    def test_stale_entries_evicted_on_get_latest(self):
        """Entries older than max_history_sec are evicted on get_latest."""
        buf = PriceBuffer(max_history_sec=2.0)
        old_ms = int(time.time() * 1000) - 5000  # 5 seconds ago

        buf.update("binance", "BTCUSDT", 100.0, old_ms)

        # Should be evicted
        assert buf.get_latest("binance", "BTCUSDT") is None

    def test_fresh_entries_not_evicted(self):
        """Entries within max_history_sec are not evicted."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 100.0, now_ms - 1000)

        latest = buf.get_latest("binance", "BTCUSDT")
        assert latest is not None
        assert latest.mid == 100.0

    def test_partial_eviction(self):
        """Only stale entries are evicted, fresh ones remain."""
        buf = PriceBuffer(max_history_sec=3.0)
        now_ms = int(time.time() * 1000)

        buf.update("binance", "BTCUSDT", 100.0, now_ms - 5000)  # stale
        buf.update("binance", "BTCUSDT", 200.0, now_ms - 1000)  # fresh

        history = buf.get_history("binance", "BTCUSDT", 60.0)
        assert len(history) == 1
        assert history[0].mid == 200.0


class TestPriceBufferThreadSafety:
    """Tests for thread safety of PriceBuffer."""

    def test_concurrent_writes_no_corruption(self):
        """Concurrent writes from multiple threads should not corrupt data."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)
        num_threads = 4
        updates_per_thread = 500

        def writer(exchange: str):
            for i in range(updates_per_thread):
                buf.update(exchange, "BTCUSDT", 100.0 + i, now_ms + i)

        threads = [
            threading.Thread(target=writer, args=(f"exchange_{i}",))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each exchange should have data
        for i in range(num_threads):
            latest = buf.get_latest(f"exchange_{i}", "BTCUSDT")
            assert latest is not None

    def test_concurrent_read_write(self):
        """Concurrent reads and writes should not deadlock or corrupt."""
        buf = PriceBuffer(max_history_sec=60.0)
        now_ms = int(time.time() * 1000)
        stop_event = threading.Event()
        errors: list[str] = []

        def writer():
            for i in range(200):
                buf.update("binance", "BTCUSDT", 100.0 + i, now_ms + i)
            stop_event.set()

        def reader():
            while not stop_event.is_set():
                try:
                    buf.get_latest("binance", "BTCUSDT")
                    buf.get_history("binance", "BTCUSDT", 10.0)
                    buf.get_all_latest("BTCUSDT")
                except Exception as e:
                    errors.append(str(e))

        writer_thread = threading.Thread(target=writer)
        reader_thread = threading.Thread(target=reader)

        writer_thread.start()
        reader_thread.start()

        writer_thread.join()
        reader_thread.join()

        assert errors == []


class TestPriceBufferCapacity:
    """Tests for buffer capacity limits."""

    def test_max_entries_per_pair(self):
        """Buffer should not exceed 18000 entries per (exchange, symbol) pair."""
        buf = PriceBuffer(max_history_sec=999999.0)  # large to avoid time eviction
        now_ms = int(time.time() * 1000)

        # Insert more than 18000 entries
        for i in range(20000):
            buf.update("binance", "BTCUSDT", 100.0 + i, now_ms + i)

        # The deque maxlen should cap it at 18000
        # We can verify by checking history returns at most 18000
        with buf._lock:
            pair_buf = buf._buffers[("binance", "BTCUSDT")]
            assert len(pair_buf) <= 18000
