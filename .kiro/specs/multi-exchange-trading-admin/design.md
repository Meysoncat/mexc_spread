# Design Document: Multi-Exchange Trading Admin

## Overview

Расширение торгового контура для поддержки нескольких бирж (Binance, Bybit, OKX, Gate.io, HTX, Bitget) помимо MEXC. Архитектура строится на паттерне Engine Registry (singleton-реестр экземпляров TradingEngine), абстрактном базовом классе PrivateClient с exchange-specific реализациями, и едином UI с селекторами биржи/рынка/типа ордера.

## Architecture

### Layered Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Frontend (React/TypeScript)                            │
│  TradingAdminModal + exchange/market/order selectors    │
├─────────────────────────────────────────────────────────┤
│  Backend API (FastAPI)                                  │
│  Multi-engine endpoints with backward-compatible defaults│
├─────────────────────────────────────────────────────────┤
│  Engine Registry (Singleton)                            │
│  get_or_create(exchange, market) → TradingEngine        │
├─────────────────────────────────────────────────────────┤
│  TradingEngine instances (per exchange+market)          │
│  Independent state, risk, event logs                    │
├─────────────────────────────────────────────────────────┤
│  Private Clients (Abstract Base → Exchange Impls)       │
│  MEXC | Binance | Bybit | OKX | Gate.io | HTX | Bitget │
└─────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Engine Registry Pattern** — singleton managing multiple TradingEngine instances indexed by `(exchange, market)` composite key
2. **Abstract Base Client** — `BasePrivateClient` ABC with exchange-specific subclasses
3. **Spot-first, futures-ready** — spot market fully implemented, futures endpoints stubbed
4. **Backward compatible** — all existing endpoints default to MEXC/spot when params omitted
5. **Independent state** — each engine has its own kill_switch, counters, risk manager, event log

## Components and Interfaces

### 1. Exchange Enum and Configuration

```python
# mexc_monitor/trading/exchanges.py
from enum import Enum
from dataclasses import dataclass


class Exchange(str, Enum):
    MEXC = "mexc"
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"
    GATEIO = "gateio"
    HTX = "htx"
    BITGET = "bitget"


class Market(str, Enum):
    SPOT = "spot"
    FUTURES = "futures"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class EngineKey:
    """Composite key for engine registry lookup."""
    exchange: Exchange
    market: Market

    def __str__(self) -> str:
        return f"{self.exchange.value}:{self.market.value}"
```

### 2. Exchange Configuration Registry

```python
# mexc_monitor/trading/exchange_config.py
from dataclasses import dataclass


@dataclass(frozen=True)
class ExchangeConfig:
    """Static configuration for a supported exchange."""
    name: Exchange
    env_prefix: str  # e.g. "BINANCE", "MEXC", "GATEIO"
    spot_base_url: str
    futures_base_url: str
    api_key_header: str  # e.g. "X-MBX-APIKEY", "X-MEXC-APIKEY"
    supports_recv_window: bool
    spot_order_path: str
    futures_order_path: str


EXCHANGE_CONFIGS: dict[Exchange, ExchangeConfig] = {
    Exchange.MEXC: ExchangeConfig(
        name=Exchange.MEXC,
        env_prefix="MEXC",
        spot_base_url="https://api.mexc.com",
        futures_base_url="https://contract.mexc.com",
        api_key_header="X-MEXC-APIKEY",
        supports_recv_window=True,
        spot_order_path="/api/v3/order",
        futures_order_path="/api/v1/private/order/submit",
    ),
    Exchange.BINANCE: ExchangeConfig(
        name=Exchange.BINANCE,
        env_prefix="BINANCE",
        spot_base_url="https://api.binance.com",
        futures_base_url="https://fapi.binance.com",
        api_key_header="X-MBX-APIKEY",
        supports_recv_window=True,
        spot_order_path="/api/v3/order",
        futures_order_path="/fapi/v1/order",
    ),
    # ... similar entries for BYBIT, OKX, GATEIO, HTX, BITGET
}
```

### 3. Abstract Base Private Client

```python
# mexc_monitor/trading/private_client_base.py
from abc import ABC, abstractmethod
from typing import Any, Literal


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None  # None for MARKET orders
    client_order_id: str
    time_in_force: str = "GTC"


@dataclass
class OrderResponse:
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    status: str
    raw: dict[str, Any]


class BasePrivateClient(ABC):
    """Abstract base for exchange-specific authenticated API clients."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        timeout_sec: float = 20.0,
        recv_window_ms: int = 5_000,
    ):
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._recv_window_ms = recv_window_ms

    @abstractmethod
    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        """Sign request parameters per exchange specification."""
        ...

    @abstractmethod
    def _get_api_key_header(self) -> str:
        """Return the header name for the API key."""
        ...

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place an order on the exchange."""
        ...

    @abstractmethod
    def cancel_order(
        self, *, symbol: str, order_id: str | None = None,
        client_order_id: str | None = None
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        ...

    def has_credentials(self) -> bool:
        """Check if valid credentials are configured."""
        return bool(self._api_key and self._api_secret)
```

### 4. Exchange-Specific Client Implementations

Each exchange implements `BasePrivateClient` with its signing logic:

```python
# mexc_monitor/trading/clients/binance_client.py
class BinancePrivateClient(BasePrivateClient):
    """Binance spot/futures private client. HMAC SHA256 signing."""

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        signed = dict(params)
        signed["timestamp"] = int(time.time() * 1000)
        if self._recv_window_ms:
            signed["recvWindow"] = self._recv_window_ms
        query = urlencode(sorted(signed.items()))
        signature = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        signed["signature"] = signature
        return signed

    def _get_api_key_header(self) -> str:
        return "X-MBX-APIKEY"

    def place_order(self, request: OrderRequest) -> OrderResponse:
        params = {
            "symbol": request.symbol,
            "side": request.side.value,
            "type": request.order_type.value,
            "quantity": self._fmt(request.quantity),
            "newClientOrderId": request.client_order_id,
            "timeInForce": request.time_in_force,
        }
        if request.order_type == OrderType.LIMIT and request.price is not None:
            params["price"] = self._fmt(request.price)
        # MARKET orders: no price param
        raw = self._request_signed("POST", "/api/v3/order", params=params)
        return OrderResponse(
            order_id=str(raw.get("orderId", "")),
            client_order_id=raw.get("clientOrderId", ""),
            symbol=raw.get("symbol", ""),
            side=raw.get("side", ""),
            order_type=raw.get("type", ""),
            status=raw.get("status", ""),
            raw=raw,
        )
```

Signing differences by exchange:

| Exchange | Signing Method | Key Header | Notes |
|----------|---------------|------------|-------|
| MEXC | HMAC SHA256 on sorted query string | X-MEXC-APIKEY | timestamp + recvWindow |
| Binance | HMAC SHA256 on sorted query string | X-MBX-APIKEY | timestamp + recvWindow |
| Bybit | HMAC SHA256 on `timestamp+api_key+recv_window+query` | X-BAPI-API-KEY | X-BAPI-TIMESTAMP, X-BAPI-SIGN headers |
| OKX | HMAC SHA256 → Base64 on `timestamp+method+path+body` | OK-ACCESS-KEY | OK-ACCESS-SIGN, OK-ACCESS-TIMESTAMP, OK-ACCESS-PASSPHRASE |
| Gate.io | HMAC SHA512 on `method\npath\nquery\nhashed_body\ntimestamp` | KEY | SIGN, Timestamp headers |
| HTX | HMAC SHA256 on `method\nhost\npath\nsorted_params` | AccessKeyId param | Signature in query params |
| Bitget | HMAC SHA256 → Base64 on `timestamp+method+path+body` | ACCESS-KEY | ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE |

### 5. Client Factory

```python
# mexc_monitor/trading/client_factory.py

def create_private_client(
    exchange: Exchange,
    market: Market,
) -> BasePrivateClient:
    """Factory: create a configured private client for the given exchange+market."""
    config = EXCHANGE_CONFIGS[exchange]
    env_prefix = config.env_prefix

    api_key = os.environ.get(f"{env_prefix}_API_KEY", "")
    api_secret = os.environ.get(f"{env_prefix}_API_SECRET", "")
    recv_window = int(os.environ.get(f"{env_prefix}_RECV_WINDOW_MS", "5000"))

    base_url = (
        config.spot_base_url if market == Market.SPOT
        else config.futures_base_url
    )

    client_class = CLIENT_CLASSES[exchange]
    return client_class(
        api_key=api_key,
        api_secret=api_secret,
        base_url=base_url,
        recv_window_ms=recv_window,
    )
```

### 6. Engine Registry

```python
# mexc_monitor/trading/engine_registry.py
import threading
from typing import Dict


class EngineRegistry:
    """Singleton registry managing TradingEngine instances by (exchange, market) key."""

    _instance: "EngineRegistry | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "EngineRegistry":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._engines: Dict[EngineKey, TradingEngine] = {}
                cls._instance._engines_lock = threading.Lock()
            return cls._instance

    def get_or_create(
        self, exchange: Exchange, market: Market
    ) -> TradingEngine:
        """Return existing engine or create a new one for the key."""
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

    def list_engines(self) -> list[dict]:
        """Return metadata for all registered engines."""
        with self._engines_lock:
            return [
                {
                    "exchange": key.exchange.value,
                    "market": key.market.value,
                    "running": engine._state.running,
                    "mode": engine._state.mode,
                    "symbol": engine._state.symbol,
                }
                for key, engine in self._engines.items()
            ]

    def shutdown_all(self) -> None:
        """Stop all running engines gracefully."""
        with self._engines_lock:
            for engine in self._engines.values():
                if engine._state.running:
                    engine.stop()

    def get(self, exchange: Exchange, market: Market) -> TradingEngine | None:
        """Return engine if exists, None otherwise."""
        key = EngineKey(exchange=exchange, market=market)
        with self._engines_lock:
            return self._engines.get(key)

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing only)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown_all()
            cls._instance = None
```

### 7. Settings Loader (Multi-Exchange)

```python
# mexc_monitor/trading/settings_loader.py

def load_trading_settings_for_exchange(
    exchange: Exchange, market: Market
) -> TradingSettings:
    """Load settings with exchange-specific prefix, falling back to MEXC defaults."""
    config = EXCHANGE_CONFIGS[exchange]
    prefix = f"{config.env_prefix}_TRADING"

    def _env(name: str, default, parser=str):
        # Try exchange-specific first: e.g. BINANCE_TRADING_SYMBOL
        val = os.environ.get(f"{prefix}_{name}")
        if val is not None and val.strip():
            return parser(val.strip())
        # Fallback to MEXC_TRADING_ prefix
        val = os.environ.get(f"MEXC_TRADING_{name}")
        if val is not None and val.strip():
            return parser(val.strip())
        return default

    return TradingSettings(
        enabled=_env("ENABLED", False, _parse_bool),
        mode=_env("MODE", "paper"),
        symbol=_env("SYMBOL", "BTCUSDT", str.upper),
        order_type=_env("ORDER_TYPE", "LIMIT", str.upper),
        order_side=_env("ORDER_SIDE", "BUY", str.upper),
        min_net_spread_bps=_env("MIN_NET_SPREAD_BPS", -2.0, float),
        order_quote_notional=_env("ORDER_QUOTE_NOTIONAL", 25.0, float),
        limit_price_offset_bps=_env("LIMIT_PRICE_OFFSET_BPS", 0.0, float),
        loop_interval_sec=_env("LOOP_INTERVAL_SEC", 3.0, float),
        max_orders_per_day=_env("MAX_ORDERS_PER_DAY", 20, int),
        max_open_orders=_env("MAX_OPEN_ORDERS", 3, int),
        max_consecutive_errors=_env("MAX_CONSECUTIVE_ERRORS", 5, int),
        kill_switch=_env("KILL_SWITCH", True, _parse_bool),
        # Credentials loaded separately via client factory
        api_key="",
        api_secret="",
        recv_window_ms=5000,
        events_log_path=_env(
            "EVENTS_LOG_PATH",
            f"data/trading_events_{exchange.value}_{market.value}.jsonl"
        ),
    )
```

### 8. Extended TradingEngine

The existing `TradingEngine` is extended with:
- `exchange: Exchange` and `market: Market` fields
- `order_type: OrderType` and `order_side: OrderSide` in settings
- Accepts a `BasePrivateClient` instead of creating its own `MexcPrivateClient`
- Price calculation logic handles MARKET (no price) vs LIMIT (bid/ask + offset)

```python
# Changes to mexc_monitor/trading/engine.py

class TradingSettings:
    # ... existing fields ...
    order_type: str = "LIMIT"   # NEW: "LIMIT" or "MARKET"
    order_side: str = "BUY"     # NEW: "BUY" or "SELL"


class TradingEngine:
    def __init__(
        self,
        settings: TradingSettings | None = None,
        private_client: BasePrivateClient | None = None,
        exchange: Exchange = Exchange.MEXC,
        market: Market = Market.SPOT,
    ):
        self._settings = settings or load_trading_settings()
        self._client = private_client
        self._exchange = exchange
        self._market = market
        # ... rest of init unchanged ...

    def _place_order_from_row(self, row: dict[str, Any]) -> None:
        """Build and place order using configured type and side."""
        bid = float(row.get("bid", 0))
        ask = float(row.get("ask", 0))

        order_type = OrderType(self._settings.order_type)
        order_side = OrderSide(self._settings.order_side)

        # Price logic
        if order_type == OrderType.MARKET:
            price = None
        else:
            price = self._order_price(bid, ask)

        quantity = self._settings.order_quote_notional / (price or ask or bid)

        request = OrderRequest(
            symbol=self._settings.symbol,
            side=order_side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            client_order_id=self._build_client_order_id(),
        )
        response = self._client.place_order(request)
        # ... handle response ...
```

### 9. Backend API Multi-Engine Endpoints

```python
# backend/main.py — new/modified endpoints

registry = EngineRegistry()


def _resolve_engine(
    exchange: str | None = None, market: str | None = None
) -> TradingEngine:
    """Resolve engine from query params, defaulting to mexc/spot."""
    ex = Exchange(exchange.lower()) if exchange else Exchange.MEXC
    mk = Market(market.lower()) if market else Market.SPOT
    return registry.get_or_create(ex, mk)


@app.get("/api/trading/engines")
def trading_engines(_: None = Depends(_require_admin_token)) -> dict:
    return {"ok": True, "engines": registry.list_engines()}


@app.get("/api/trading/exchanges")
def trading_exchanges() -> dict:
    """Return all supported exchanges with availability status."""
    result = []
    for ex in Exchange:
        config = EXCHANGE_CONFIGS[ex]
        has_creds = bool(
            os.environ.get(f"{config.env_prefix}_API_KEY")
            and os.environ.get(f"{config.env_prefix}_API_SECRET")
        )
        result.append({
            "exchange": ex.value,
            "available": has_creds,
            "paper_only": not has_creds,
            "markets": ["spot", "futures"],
            "spot_base_url": config.spot_base_url,
            "futures_base_url": config.futures_base_url,
            "order_types": ["LIMIT", "MARKET"],
        })
    return {"ok": True, "exchanges": result}


# Existing endpoints remain unchanged — they default to mexc/spot
@app.get("/api/trading/status")
def trading_status(
    exchange: str | None = None,
    market: str | None = None,
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    return {"ok": True, **engine.status()}


@app.patch("/api/trading/settings")
def trading_settings_update(
    payload: dict,
    exchange: str | None = None,
    market: str | None = None,
    _: None = Depends(_require_admin_token),
) -> dict:
    engine = _resolve_engine(exchange, market)
    return {"ok": True, **engine.update_runtime_settings(payload)}
```

### 10. Frontend: TradingAdminModal Extensions

```typescript
// frontend/src/TradingAdminModal.tsx — new state and selectors

type SupportedExchange = "mexc" | "binance" | "bybit" | "okx" | "gateio" | "htx" | "bitget";
type MarketType = "spot" | "futures";
type OrderType = "LIMIT" | "MARKET";
type OrderSide = "BUY" | "SELL";

interface ExchangeAvailability {
  exchange: SupportedExchange;
  available: boolean;
  paper_only: boolean;
  markets: MarketType[];
  spot_base_url: string;
  futures_base_url: string;
  order_types: OrderType[];
}

// New state in TradingAdminModal
const [selectedExchange, setSelectedExchange] = useState<SupportedExchange>("mexc");
const [selectedMarket, setSelectedMarket] = useState<MarketType>("spot");
const [exchanges, setExchanges] = useState<ExchangeAvailability[]>([]);

// Extended RuntimePatch
type RuntimePatch = {
  // ... existing fields ...
  order_type: OrderType;   // NEW
  order_side: OrderSide;   // NEW
};

// API calls include exchange/market params
const loadStatus = useCallback(async () => {
  const params = new URLSearchParams({
    exchange: selectedExchange,
    market: selectedMarket,
  });
  const r = await fetch(apiUrl(`/api/trading/status?${params}`), { headers });
  // ...
}, [selectedExchange, selectedMarket, headers]);
```

UI Layout additions:
- **Exchange selector**: dropdown/tabs at top of modal showing all 7 exchanges with availability badges
- **Market selector**: spot/futures toggle below exchange selector
- **Order Type selector**: LIMIT/MARKET radio in the settings form
- **Order Side selector**: BUY/SELL radio in the settings form
- **Credential indicator**: green dot for live-available, yellow for paper-only

## Data Models

### TradingSettings (Extended)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| enabled | bool | False | Auto-start on backend startup |
| mode | "paper" \| "live" | "paper" | Trading mode |
| symbol | str | "BTCUSDT" | Trading symbol |
| order_type | "LIMIT" \| "MARKET" | "LIMIT" | Order type |
| order_side | "BUY" \| "SELL" | "BUY" | Order side |
| min_net_spread_bps | float | -2.0 | Entry threshold |
| order_quote_notional | float | 25.0 | Order size in quote |
| limit_price_offset_bps | float | 0.0 | Limit price offset |
| loop_interval_sec | float | 3.0 | Loop interval |
| max_orders_per_day | int | 20 | Daily order limit |
| max_open_orders | int | 3 | Max concurrent orders |
| max_consecutive_errors | int | 5 | Error threshold |
| kill_switch | bool | True | Safety kill switch |
| events_log_path | str | varies | Per-engine event log |

### EngineKey

| Field | Type | Description |
|-------|------|-------------|
| exchange | Exchange | Exchange enum value |
| market | Market | "spot" or "futures" |

### OrderRequest

| Field | Type | Description |
|-------|------|-------------|
| symbol | str | Trading pair |
| side | OrderSide | BUY or SELL |
| order_type | OrderType | LIMIT or MARKET |
| quantity | float | Order quantity |
| price | float \| None | Price (None for MARKET) |
| client_order_id | str | Client-generated ID |
| time_in_force | str | GTC default |

### OrderResponse

| Field | Type | Description |
|-------|------|-------------|
| order_id | str | Exchange-assigned ID |
| client_order_id | str | Client-generated ID |
| symbol | str | Trading pair |
| side | str | BUY or SELL |
| order_type | str | LIMIT or MARKET |
| status | str | Order status |
| raw | dict | Full exchange response |

## Interfaces

### Backend API Endpoints (Complete)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /api/trading/exchanges | List all exchanges with availability |
| GET | /api/trading/engines | List all registered engine instances |
| GET | /api/trading/status?exchange=&market= | Engine status (defaults: mexc, spot) |
| POST | /api/trading/start?exchange=&market= | Start engine |
| POST | /api/trading/stop?exchange=&market= | Stop engine |
| POST | /api/trading/kill-switch?exchange=&market=&enabled= | Toggle kill switch |
| POST | /api/trading/run-once?exchange=&market= | Single step execution |
| PATCH | /api/trading/settings?exchange=&market= | Update runtime settings |
| GET | /api/trading/events?exchange=&market=&limit= | Read event log |

### Environment Variables Convention

```
{EXCHANGE}_API_KEY          — API key (MEXC_API_KEY, BINANCE_API_KEY, etc.)
{EXCHANGE}_API_SECRET       — API secret
{EXCHANGE}_RECV_WINDOW_MS   — Receive window (optional)
{EXCHANGE}_TRADING_ENABLED  — Auto-start engine
{EXCHANGE}_TRADING_MODE     — paper/live
{EXCHANGE}_TRADING_SYMBOL   — Trading symbol
{EXCHANGE}_TRADING_ORDER_TYPE — LIMIT/MARKET
{EXCHANGE}_TRADING_ORDER_SIDE — BUY/SELL
{EXCHANGE}_TRADING_MIN_NET_SPREAD_BPS
{EXCHANGE}_TRADING_ORDER_QUOTE_NOTIONAL
{EXCHANGE}_TRADING_LIMIT_PRICE_OFFSET_BPS
{EXCHANGE}_TRADING_LOOP_INTERVAL_SEC
{EXCHANGE}_TRADING_MAX_ORDERS_PER_DAY
{EXCHANGE}_TRADING_MAX_OPEN_ORDERS
{EXCHANGE}_TRADING_MAX_CONSECUTIVE_ERRORS
{EXCHANGE}_TRADING_KILL_SWITCH
{EXCHANGE}_TRADING_EVENTS_LOG_PATH
```

Where {EXCHANGE} ∈ {MEXC, BINANCE, BYBIT, OKX, GATEIO, HTX, BITGET}

## Error Handling

### Client Errors

- `PrivateApiError` raised for HTTP errors, invalid responses, exchange-specific error codes
- Each exchange client normalizes errors into `PrivateApiError` with exchange name prefix
- Missing credentials: client reports `has_credentials() == False`, engine operates in paper mode only

### Engine Errors

- Consecutive error counter per engine instance (independent)
- Kill switch auto-activates when `max_consecutive_errors` reached (per engine)
- Error counter resets on successful order placement

### Registry Errors

- Invalid exchange/market values: raise `ValueError` with descriptive message
- Engine creation failure: propagate exception, do not store failed engine in registry

### API Errors

- Invalid exchange param: HTTP 400 with `{"detail": "Unsupported exchange: ..."}`
- Invalid market param: HTTP 400 with `{"detail": "Invalid market: ..."}`
- Engine not found (for GET without auto-create): HTTP 404
- Missing admin token: HTTP 401 (unchanged)

## File Structure

```
mexc_monitor/trading/
├── __init__.py
├── exchanges.py           # NEW: Exchange, Market, OrderType, OrderSide enums, EngineKey
├── exchange_config.py     # NEW: ExchangeConfig dataclass, EXCHANGE_CONFIGS dict
├── private_client_base.py # NEW: BasePrivateClient ABC, OrderRequest, OrderResponse
├── private_client.py      # EXISTING: MexcPrivateClient (refactored to extend base)
├── clients/               # NEW: exchange-specific client implementations
│   ├── __init__.py
│   ├── binance_client.py
│   ├── bybit_client.py
│   ├── okx_client.py
│   ├── gateio_client.py
│   ├── htx_client.py
│   └── bitget_client.py
├── client_factory.py      # NEW: create_private_client factory function
├── settings_loader.py     # NEW: load_trading_settings_for_exchange
├── engine_registry.py     # NEW: EngineRegistry singleton
├── engine.py              # MODIFIED: accepts BasePrivateClient, exchange, market
└── risk.py                # UNCHANGED
```

## Testing Strategy

### Unit Tests (Example-Based)
- Verify each exchange client class can be instantiated (Requirement 2.1)
- Verify MEXC backward compatibility with existing env vars (Requirements 3.2, 3.3)
- Verify order type/side enum values accepted (Requirements 6.1, 6.2)
- Verify market parameter acceptance at engine creation (Requirement 7.1)
- Verify UI component renders selectors (Requirements 8.1–8.4)
- Verify exchange availability response structure (Requirement 10.1, 10.4)

### Property Tests (Hypothesis)
- Registry idempotence and uniqueness (Property 1)
- Engine state isolation (Property 2)
- Credential env var convention (Property 3)
- Signing determinism (Property 4)
- Availability detection (Property 5)
- Order type → price handling (Property 6)
- Settings fallback chain (Property 7)
- API default resolution (Property 8)
- Market-based URL routing (Property 9)

### Integration Tests
- Backend shutdown stops all engines (Requirement 1.5)
- MEXC auto-start on MEXC_TRADING_ENABLED=true (Requirement 9.4)
- Exchange selection triggers correct API call in UI (Requirement 8.5)

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Registry get-or-create idempotence

*For any* sequence of `get_or_create(exchange, market)` calls, calling with the same `(exchange, market)` key multiple times SHALL always return the same `TradingEngine` instance (object identity), and calling with distinct keys SHALL return distinct instances. The total number of engines in the registry SHALL equal the number of unique keys requested.

**Validates: Requirements 1.1, 1.2, 1.3**

### Property 2: Engine state isolation

*For any* two distinct `TradingEngine` instances in the registry (with different `EngineKey`), modifying the state of one engine (kill_switch, order counters, error counters, settings) SHALL NOT change the state of the other engine. Each engine's state is fully independent.

**Validates: Requirements 1.4, 4.3, 4.4**

### Property 3: Credential environment variable convention

*For any* supported exchange name `E`, the system SHALL resolve API credentials by reading environment variables named `{E.upper()}_API_KEY` and `{E.upper()}_API_SECRET`. The env prefix used for credential lookup SHALL always equal the exchange's configured `env_prefix` in uppercase.

**Validates: Requirements 2.2, 3.1**

### Property 4: Request signing determinism

*For any* exchange client, any API secret, and any set of request parameters, the `_sign()` method SHALL produce a deterministic HMAC signature. Calling `_sign()` twice with the same inputs (including timestamp) SHALL produce identical output. The signature SHALL be a valid hex-encoded (or base64-encoded, per exchange spec) HMAC-SHA256 (or SHA512 for Gate.io) of the canonical message.

**Validates: Requirements 2.3**

### Property 5: Credential-based availability detection

*For any* supported exchange, when `{EXCHANGE}_API_KEY` and `{EXCHANGE}_API_SECRET` are both non-empty, the exchange availability endpoint SHALL report `available=True` and `paper_only=False`. When either credential is missing or empty, it SHALL report `available=False` and `paper_only=True`. No exception SHALL be raised regardless of credential state.

**Validates: Requirements 2.4, 10.2, 10.3**

### Property 6: Order type determines price handling

*For any* order request where `order_type == MARKET`, the constructed exchange API request SHALL NOT contain a price parameter. *For any* order request where `order_type == LIMIT`, the constructed request SHALL contain a price parameter calculated as `reference_price * (1 + limit_price_offset_bps / 10000)` where reference_price is derived from the current bid/ask.

**Validates: Requirements 2.5, 6.3, 6.4**

### Property 7: Settings loading with fallback

*For any* exchange and any setting name, `load_trading_settings_for_exchange(exchange, market)` SHALL first check `{EXCHANGE}_TRADING_{SETTING}`. If that environment variable is unset or empty, it SHALL fall back to `MEXC_TRADING_{SETTING}`. If both are unset, it SHALL use the hardcoded default. The resulting settings object SHALL always contain valid values regardless of which env vars are present.

**Validates: Requirements 4.1, 4.2**

### Property 8: API backward compatibility defaults

*For any* trading API endpoint, when the `exchange` query parameter is omitted or empty, the endpoint SHALL resolve to the `Exchange.MEXC` engine. When the `market` query parameter is omitted or empty, it SHALL resolve to `Market.SPOT`. The response format SHALL be identical to the pre-multi-exchange API response structure.

**Validates: Requirements 5.8, 9.2**

### Property 9: Market-based endpoint routing

*For any* exchange and market combination, the `create_private_client(exchange, market)` factory SHALL configure the client's `base_url` to the exchange's `spot_base_url` when `market == SPOT`, and to the exchange's `futures_base_url` when `market == FUTURES`. The selected URL SHALL always match the corresponding field in `EXCHANGE_CONFIGS[exchange]`.

**Validates: Requirements 7.2, 7.3**
