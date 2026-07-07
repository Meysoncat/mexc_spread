"""WebSocket Manager for the Lead-Lag Arbitrage engine.

Manages persistent WebSocket connections to multiple exchanges (Binance, MEXC,
Bybit, OKX), normalizes incoming price data into PriceSnapshot objects, and
feeds the PriceBuffer.

Features:
- Each exchange connection runs in its own asyncio task
- Background thread with dedicated event loop
- Exponential backoff reconnection (1s → 2s → 4s → ... → 60s max)
- Connection status tracking: connected / disconnected / stale
- Message validation and discard counting
- Stale detection (no data > 5 seconds)

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from enum import Enum
from typing import Any

import websockets
import websockets.exceptions

from mexc_monitor.lead_lag.config import LeadLagConfig
from mexc_monitor.lead_lag.models import PriceSnapshot
from mexc_monitor.lead_lag.price_buffer import PriceBuffer

logger = logging.getLogger(__name__)

# Stale threshold: if no valid message for this many seconds, mark as stale
_STALE_THRESHOLD_SEC = 5.0

# Maximum reconnection backoff in seconds
_MAX_BACKOFF_SEC = 60.0

# Initial reconnection delay in seconds
_INITIAL_BACKOFF_SEC = 1.0

# Backoff multiplier
_BACKOFF_MULTIPLIER = 2.0


class ConnectionStatus(str, Enum):
    """Connection status for an exchange."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    STALE = "stale"


class _ExchangeState:
    """Internal state tracking for a single exchange connection."""

    __slots__ = (
        "exchange",
        "status",
        "last_message_mono",
        "last_message_ms",
        "discarded_count",
        "connected_at",
    )

    def __init__(self, exchange: str) -> None:
        self.exchange = exchange
        self.status: ConnectionStatus = ConnectionStatus.DISCONNECTED
        self.last_message_mono: float = 0.0
        self.last_message_ms: int = 0
        self.discarded_count: int = 0
        self.connected_at: float = 0.0


class LeadLagWSManager:
    """Manages WebSocket connections to multiple exchanges for lead-lag analysis.

    Runs a background thread with its own asyncio event loop. Each exchange
    connection is an independent asyncio task, so one failing connection does
    not block others.

    Usage:
        manager = LeadLagWSManager(config, price_buffer)
        manager.start()
        ...
        manager.stop()
    """

    def __init__(self, config: LeadLagConfig, price_buffer: PriceBuffer) -> None:
        """Initialize the WS Manager.

        Args:
            config: Lead-lag configuration with ws_urls, symbols, market, etc.
            price_buffer: PriceBuffer instance to feed normalized price data into.
        """
        self._config = config
        self._price_buffer = price_buffer
        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._lock = threading.Lock()

        # Build exchange -> ws_url mapping
        self._exchange_urls = self._resolve_exchange_urls()

        # State per exchange
        self._states: dict[str, _ExchangeState] = {}
        for exchange in self._exchange_urls:
            self._states[exchange] = _ExchangeState(exchange)

    def start(self) -> None:
        """Start the WS Manager background thread.

        Idempotent: calling start() when already running is a no-op.
        Establishes WebSocket connections to all configured exchanges.
        """
        with self._lock:
            if self._running:
                return
            self._running = True

        self._thread = threading.Thread(
            target=self._run_event_loop,
            daemon=True,
            name="lead-lag-ws-manager",
        )
        self._thread.start()
        logger.info("LeadLagWSManager started")

    def stop(self) -> None:
        """Stop the WS Manager and close all connections.

        Idempotent: calling stop() when already stopped is a no-op.
        """
        with self._lock:
            if not self._running:
                return
            self._running = False

        # Signal the event loop to stop
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

        # Wait for the thread to finish
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10.0)

        self._thread = None
        self._loop = None
        self._stop_event = None

        # Mark all exchanges as disconnected
        for state in self._states.values():
            state.status = ConnectionStatus.DISCONNECTED

        logger.info("LeadLagWSManager stopped")

    def is_running(self) -> bool:
        """Check if the WS Manager is currently running."""
        return self._running

    def connection_status(self) -> dict[str, dict[str, Any]]:
        """Get connection status for all exchanges.

        Returns a dict mapping exchange name to status info:
        {
            "binance": {
                "status": "connected",
                "last_message_ms": 1234567890123,
                "discarded_count": 5
            },
            ...
        }

        Status is dynamically computed:
        - "connected" if last valid message was within 5 seconds
        - "stale" if connected but no data for > 5 seconds
        - "disconnected" if never connected or connection lost
        """
        now_mono = time.monotonic()
        result: dict[str, dict[str, Any]] = {}

        for exchange, state in self._states.items():
            # Dynamically update stale status
            if state.status == ConnectionStatus.CONNECTED:
                if state.last_message_mono > 0:
                    elapsed = now_mono - state.last_message_mono
                    if elapsed > _STALE_THRESHOLD_SEC:
                        state.status = ConnectionStatus.STALE

            result[exchange] = {
                "status": state.status.value,
                "last_message_ms": state.last_message_ms,
                "discarded_count": state.discarded_count,
            }

        return result

    def get_active_exchanges(self) -> list[str]:
        """Get list of exchanges with 'connected' status (not stale, not disconnected).

        Used by the engine to determine which exchanges to include in signal generation.
        """
        now_mono = time.monotonic()
        active: list[str] = []

        for exchange, state in self._states.items():
            if state.status == ConnectionStatus.CONNECTED:
                if state.last_message_mono > 0:
                    elapsed = now_mono - state.last_message_mono
                    if elapsed <= _STALE_THRESHOLD_SEC:
                        active.append(exchange)
                    else:
                        state.status = ConnectionStatus.STALE

        return active

    # -------------------------------------------------------------------------
    # Internal: Event loop management
    # -------------------------------------------------------------------------

    def _run_event_loop(self) -> None:
        """Run the asyncio event loop in the background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()

        try:
            self._loop.run_until_complete(self._main())
        except Exception:
            logger.exception("LeadLagWSManager event loop crashed")
        finally:
            self._loop.close()

    async def _main(self) -> None:
        """Main coroutine: launch one task per exchange, wait for stop signal."""
        tasks: list[asyncio.Task] = []

        for exchange, url in self._exchange_urls.items():
            task = asyncio.create_task(
                self._exchange_loop(exchange, url),
                name=f"ws-{exchange}",
            )
            tasks.append(task)

        # Wait for stop signal
        assert self._stop_event is not None
        await self._stop_event.wait()

        # Cancel all exchange tasks
        for task in tasks:
            task.cancel()

        # Wait for tasks to finish
        await asyncio.gather(*tasks, return_exceptions=True)

    # -------------------------------------------------------------------------
    # Internal: Per-exchange connection loop
    # -------------------------------------------------------------------------

    async def _exchange_loop(self, exchange: str, url: str) -> None:
        """Connection loop for a single exchange with exponential backoff reconnection."""
        backoff = _INITIAL_BACKOFF_SEC
        state = self._states[exchange]

        while not self._stop_event.is_set():  # type: ignore[union-attr]
            try:
                await self._connect_and_listen(exchange, url, state)
                # If we exit cleanly (stop requested), break
                if self._stop_event.is_set():  # type: ignore[union-attr]
                    break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    "WS %s connection error: %s. Reconnecting in %.1fs",
                    exchange,
                    exc,
                    backoff,
                )

            # Mark as disconnected on any exit from connect_and_listen
            state.status = ConnectionStatus.DISCONNECTED

            # Wait with backoff before reconnecting
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),  # type: ignore[union-attr]
                    timeout=backoff,
                )
                # If stop_event was set, exit
                break
            except asyncio.TimeoutError:
                # Timeout means we should retry
                pass

            # Exponential backoff
            backoff = min(backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF_SEC)

        state.status = ConnectionStatus.DISCONNECTED

    async def _connect_and_listen(
        self, exchange: str, url: str, state: _ExchangeState
    ) -> None:
        """Connect to an exchange WebSocket and listen for messages."""
        logger.info("Connecting to %s at %s", exchange, url)

        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
            max_size=2**20,  # 1MB max message size
        ) as ws:
            state.status = ConnectionStatus.CONNECTED
            state.connected_at = time.monotonic()
            logger.info("Connected to %s", exchange)

            # Reset backoff on successful connection
            # (handled by the caller resetting backoff after successful message)

            # Subscribe to streams
            await self._subscribe(exchange, ws)

            # Listen for messages
            async for raw_message in ws:
                if self._stop_event.is_set():  # type: ignore[union-attr]
                    break

                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8", errors="replace")

                self._process_message(exchange, raw_message, state)

    # -------------------------------------------------------------------------
    # Internal: Subscription logic per exchange
    # -------------------------------------------------------------------------

    async def _subscribe(self, exchange: str, ws: Any) -> None:
        """Send subscription messages based on exchange type."""
        symbols = self._config.symbols
        market = self._config.market

        if exchange == "binance":
            await self._subscribe_binance(ws, symbols, market)
        elif exchange == "mexc":
            await self._subscribe_mexc(ws, symbols, market)
        elif exchange == "bybit":
            await self._subscribe_bybit(ws, symbols)
        elif exchange == "okx":
            await self._subscribe_okx(ws, symbols, market)
        else:
            logger.warning("Unknown exchange %s, no subscription sent", exchange)

    async def _subscribe_binance(
        self, ws: Any, symbols: list[str], market: str
    ) -> None:
        """Subscribe to Binance bookTicker streams.

        Binance combined stream format: symbol@bookTicker
        """
        streams = [f"{s.lower()}@bookTicker" for s in symbols]
        msg = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": 1,
        }
        await ws.send(json.dumps(msg))
        logger.debug("Binance subscribed to %d streams", len(streams))

    async def _subscribe_mexc(
        self, ws: Any, symbols: list[str], market: str
    ) -> None:
        """Subscribe to MEXC bookTicker/ticker streams."""
        if market == "futures":
            # MEXC futures uses a different protocol
            msg = {"method": "sub.tickers", "param": {}}
            await ws.send(json.dumps(msg))
            logger.debug("MEXC futures subscribed to tickers")
        else:
            # MEXC spot: subscribe to individual bookTicker streams
            for symbol in symbols:
                msg = {
                    "method": "SUBSCRIPTION",
                    "params": [f"spot@public.bookTicker.v3.api@{symbol}"],
                }
                await ws.send(json.dumps(msg))
            logger.debug("MEXC spot subscribed to %d symbols", len(symbols))

    async def _subscribe_bybit(self, ws: Any, symbols: list[str]) -> None:
        """Subscribe to Bybit tickers stream.

        Bybit v5 public linear: subscribe to tickers.{symbol}
        """
        args = [f"tickers.{s}" for s in symbols]
        msg = {
            "op": "subscribe",
            "args": args,
        }
        await ws.send(json.dumps(msg))
        logger.debug("Bybit subscribed to %d tickers", len(symbols))

    async def _subscribe_okx(
        self, ws: Any, symbols: list[str], market: str
    ) -> None:
        """Subscribe to OKX tickers.

        OKX uses instId format: BTC-USDT-SWAP for futures, BTC-USDT for spot.
        """
        args = []
        for symbol in symbols:
            # Convert BTCUSDT -> BTC-USDT or BTC-USDT-SWAP
            inst_id = self._symbol_to_okx_inst_id(symbol, market)
            args.append({"channel": "tickers", "instId": inst_id})

        msg = {"op": "subscribe", "args": args}
        await ws.send(json.dumps(msg))
        logger.debug("OKX subscribed to %d tickers", len(symbols))

    # -------------------------------------------------------------------------
    # Internal: Message processing and normalization
    # -------------------------------------------------------------------------

    def _process_message(
        self, exchange: str, raw: str, state: _ExchangeState
    ) -> None:
        """Parse and normalize a raw WebSocket message into PriceSnapshot(s)."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return

        if not isinstance(data, dict):
            return

        snapshots: list[PriceSnapshot] = []

        if exchange == "binance":
            snapshot = self._parse_binance(data)
            if snapshot:
                snapshots.append(snapshot)
        elif exchange == "mexc":
            snapshots = self._parse_mexc(data)
        elif exchange == "bybit":
            snapshot = self._parse_bybit(data)
            if snapshot:
                snapshots.append(snapshot)
        elif exchange == "okx":
            snapshot = self._parse_okx(data)
            if snapshot:
                snapshots.append(snapshot)

        # Feed valid snapshots to the price buffer
        for snapshot in snapshots:
            self._price_buffer.update(
                exchange=snapshot.exchange,
                symbol=snapshot.symbol,
                mid=snapshot.mid,
                timestamp_ms=snapshot.timestamp_ms,
            )

            # Update state on valid message
            state.last_message_mono = time.monotonic()
            state.last_message_ms = snapshot.timestamp_ms
            state.status = ConnectionStatus.CONNECTED

    def _parse_binance(self, data: dict) -> PriceSnapshot | None:
        """Parse Binance bookTicker message.

        Format: {"s": "BTCUSDT", "b": "67500.50", "B": "1.5", "a": "67501.00", "A": "2.0", ...}
        """
        symbol = data.get("s")
        bid_str = data.get("b")
        ask_str = data.get("a")

        if not symbol or bid_str is None or ask_str is None:
            self._states["binance"].discarded_count += 1
            return None

        try:
            bid = float(bid_str)
            ask = float(ask_str)
        except (TypeError, ValueError):
            self._states["binance"].discarded_count += 1
            return None

        if not self._validate_bid_ask(bid, ask):
            self._states["binance"].discarded_count += 1
            return None

        # Filter by configured symbols
        if symbol not in self._config.symbols:
            return None

        mid = (bid + ask) / 2.0
        timestamp_ms = self._now_ms()

        return PriceSnapshot(
            exchange="binance",
            symbol=symbol,
            bid=bid,
            ask=ask,
            mid=mid,
            timestamp_ms=timestamp_ms,
        )

    def _parse_mexc(self, data: dict) -> list[PriceSnapshot]:
        """Parse MEXC messages (futures tickers or spot bookTicker).

        Futures format: {"channel": "push.tickers", "data": [{...}, ...]}
        Spot format: {"d": {"s": "BTCUSDT", "b": "67500.50", "a": "67501.00"}, "c": "spot@public.bookTicker.v3.api@BTCUSDT"}
        """
        snapshots: list[PriceSnapshot] = []

        # MEXC futures: push.tickers channel
        if data.get("channel") == "push.tickers":
            items = data.get("data")
            if isinstance(items, list):
                for item in items:
                    snapshot = self._parse_mexc_futures_item(item)
                    if snapshot:
                        snapshots.append(snapshot)
            return snapshots

        # MEXC spot: bookTicker
        d = data.get("d")
        if isinstance(d, dict):
            snapshot = self._parse_mexc_spot_item(d)
            if snapshot:
                snapshots.append(snapshot)

        return snapshots

    def _parse_mexc_futures_item(self, item: dict) -> PriceSnapshot | None:
        """Parse a single MEXC futures ticker item."""
        if not isinstance(item, dict):
            return None

        symbol = item.get("symbol")
        bid_str = item.get("bid1")
        ask_str = item.get("ask1")

        if not symbol or bid_str is None or ask_str is None:
            self._states["mexc"].discarded_count += 1
            return None

        try:
            bid = float(bid_str)
            ask = float(ask_str)
        except (TypeError, ValueError):
            self._states["mexc"].discarded_count += 1
            return None

        if not self._validate_bid_ask(bid, ask):
            self._states["mexc"].discarded_count += 1
            return None

        # Normalize symbol: MEXC futures uses "BTC_USDT" -> "BTCUSDT"
        normalized_symbol = symbol.replace("_", "")
        if normalized_symbol not in self._config.symbols:
            return None

        mid = (bid + ask) / 2.0
        timestamp_ms = self._now_ms()

        return PriceSnapshot(
            exchange="mexc",
            symbol=normalized_symbol,
            bid=bid,
            ask=ask,
            mid=mid,
            timestamp_ms=timestamp_ms,
        )

    def _parse_mexc_spot_item(self, d: dict) -> PriceSnapshot | None:
        """Parse a single MEXC spot bookTicker item."""
        symbol = d.get("s")
        bid_str = d.get("b")
        ask_str = d.get("a")

        if not symbol or bid_str is None or ask_str is None:
            self._states["mexc"].discarded_count += 1
            return None

        try:
            bid = float(bid_str)
            ask = float(ask_str)
        except (TypeError, ValueError):
            self._states["mexc"].discarded_count += 1
            return None

        if not self._validate_bid_ask(bid, ask):
            self._states["mexc"].discarded_count += 1
            return None

        if symbol not in self._config.symbols:
            return None

        mid = (bid + ask) / 2.0
        timestamp_ms = self._now_ms()

        return PriceSnapshot(
            exchange="mexc",
            symbol=symbol,
            bid=bid,
            ask=ask,
            mid=mid,
            timestamp_ms=timestamp_ms,
        )

    def _parse_bybit(self, data: dict) -> PriceSnapshot | None:
        """Parse Bybit v5 tickers message.

        Format: {"topic": "tickers.BTCUSDT", "data": {"symbol": "BTCUSDT", "bid1Price": "67500.5", "ask1Price": "67501.0", ...}}
        """
        topic = data.get("topic", "")
        if not topic.startswith("tickers."):
            return None

        ticker_data = data.get("data")
        if not isinstance(ticker_data, dict):
            return None

        symbol = ticker_data.get("symbol")
        bid_str = ticker_data.get("bid1Price")
        ask_str = ticker_data.get("ask1Price")

        if not symbol or bid_str is None or ask_str is None:
            self._states["bybit"].discarded_count += 1
            return None

        try:
            bid = float(bid_str)
            ask = float(ask_str)
        except (TypeError, ValueError):
            self._states["bybit"].discarded_count += 1
            return None

        if not self._validate_bid_ask(bid, ask):
            self._states["bybit"].discarded_count += 1
            return None

        if symbol not in self._config.symbols:
            return None

        mid = (bid + ask) / 2.0
        timestamp_ms = self._now_ms()

        return PriceSnapshot(
            exchange="bybit",
            symbol=symbol,
            bid=bid,
            ask=ask,
            mid=mid,
            timestamp_ms=timestamp_ms,
        )

    def _parse_okx(self, data: dict) -> PriceSnapshot | None:
        """Parse OKX tickers message.

        Format: {"arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
                 "data": [{"instId": "BTC-USDT-SWAP", "bidPx": "67500.5", "askPx": "67501.0", ...}]}
        """
        if "data" not in data:
            return None

        items = data.get("data")
        if not isinstance(items, list) or not items:
            return None

        item = items[0]
        if not isinstance(item, dict):
            return None

        inst_id = item.get("instId", "")
        bid_str = item.get("bidPx")
        ask_str = item.get("askPx")

        if not inst_id or bid_str is None or ask_str is None:
            self._states["okx"].discarded_count += 1
            return None

        try:
            bid = float(bid_str)
            ask = float(ask_str)
        except (TypeError, ValueError):
            self._states["okx"].discarded_count += 1
            return None

        if not self._validate_bid_ask(bid, ask):
            self._states["okx"].discarded_count += 1
            return None

        # Convert OKX instId back to normalized symbol
        symbol = self._okx_inst_id_to_symbol(inst_id)
        if symbol not in self._config.symbols:
            return None

        mid = (bid + ask) / 2.0
        timestamp_ms = self._now_ms()

        return PriceSnapshot(
            exchange="okx",
            symbol=symbol,
            bid=bid,
            ask=ask,
            mid=mid,
            timestamp_ms=timestamp_ms,
        )

    # -------------------------------------------------------------------------
    # Internal: Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _now_ms() -> int:
        """Current time in milliseconds (wall clock, compatible with PriceBuffer)."""
        return int(time.time() * 1000)

    @staticmethod
    def _validate_bid_ask(bid: float, ask: float) -> bool:
        """Validate bid/ask values per Requirement 1.3.

        Returns True if valid: bid > 0 and ask >= bid.
        """
        return bid > 0 and ask >= bid

    def _resolve_exchange_urls(self) -> dict[str, str]:
        """Resolve which exchanges to connect to and their WebSocket URLs.

        Maps exchange name -> URL based on config.ws_urls and market type.
        Includes leader + all laggers.
        """
        all_exchanges = [self._config.leader_exchange] + list(
            self._config.lagger_exchanges
        )
        market = self._config.market
        urls: dict[str, str] = {}

        for exchange in all_exchanges:
            if exchange in urls:
                continue
            url = self._find_ws_url(exchange, market)
            if url:
                urls[exchange] = url
            else:
                logger.warning(
                    "No WebSocket URL found for exchange '%s' (market=%s) in ws_urls config",
                    exchange,
                    market,
                )

        return urls

    def _find_ws_url(self, exchange: str, market: str) -> str | None:
        """Find the best matching WebSocket URL for an exchange.

        Priority:
        1. Exact match: "{exchange}_{market}" (e.g. "binance_futures")
        2. Exchange-only match: "{exchange}" (e.g. "bybit")
        3. Any key starting with exchange name
        """
        ws_urls = self._config.ws_urls
        exchange_lower = exchange.lower()

        # Priority 1: exact match with market
        key_with_market = f"{exchange_lower}_{market}"
        if key_with_market in ws_urls:
            return ws_urls[key_with_market]

        # Priority 2: exact exchange name
        if exchange_lower in ws_urls:
            return ws_urls[exchange_lower]

        # Priority 3: any key starting with exchange name
        for key, url in ws_urls.items():
            if key.lower().startswith(exchange_lower):
                return url

        return None

    @staticmethod
    def _symbol_to_okx_inst_id(symbol: str, market: str) -> str:
        """Convert a normalized symbol (BTCUSDT) to OKX instId format.

        Spot: BTC-USDT
        Futures/Swap: BTC-USDT-SWAP
        """
        # Simple heuristic: split before "USDT", "USDC", "BUSD"
        for quote in ("USDT", "USDC", "BUSD"):
            if symbol.endswith(quote):
                base = symbol[: -len(quote)]
                inst_id = f"{base}-{quote}"
                if market == "futures":
                    inst_id += "-SWAP"
                return inst_id

        # Fallback: return as-is
        return symbol

    @staticmethod
    def _okx_inst_id_to_symbol(inst_id: str) -> str:
        """Convert OKX instId (BTC-USDT-SWAP or BTC-USDT) to normalized symbol (BTCUSDT)."""
        parts = inst_id.split("-")
        if len(parts) >= 2:
            # Remove SWAP suffix if present, join base + quote
            return parts[0] + parts[1]
        return inst_id
