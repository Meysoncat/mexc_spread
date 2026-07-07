# Implementation Plan: Multi-Exchange Trading Admin

## Overview

Расширение торгового контура для поддержки нескольких бирж (Binance, Bybit, OKX, Gate.io, HTX, Bitget) помимо MEXC. Реализация включает: Exchange/Market/OrderType/OrderSide enums, абстрактный BasePrivateClient с exchange-specific реализациями, EngineRegistry (singleton), multi-exchange settings loader с fallback, расширение Backend API endpoints с backward-compatible defaults, и обновление React UI (селекторы биржи/рынка/типа ордера/стороны). Все backend-компоненты размещаются в `mexc_monitor/trading/`, frontend — в `frontend/src/`.

## Tasks

- [x] 1. Create exchange enums, data models, and configuration registry
  - [x] 1.1 Create exchange enums and EngineKey
    - Create `mexc_monitor/trading/exchanges.py`
    - Define `Exchange` (MEXC, BINANCE, BYBIT, OKX, GATEIO, HTX, BITGET), `Market` (SPOT, FUTURES), `OrderType` (LIMIT, MARKET), `OrderSide` (BUY, SELL) enums
    - Define `EngineKey` frozen dataclass with `exchange` and `market` fields and `__str__` method
    - _Requirements: 1.1, 6.1, 6.2, 7.1_

  - [x] 1.2 Create exchange configuration registry
    - Create `mexc_monitor/trading/exchange_config.py`
    - Define `ExchangeConfig` frozen dataclass with fields: name, env_prefix, spot_base_url, futures_base_url, api_key_header, supports_recv_window, spot_order_path, futures_order_path
    - Populate `EXCHANGE_CONFIGS: dict[Exchange, ExchangeConfig]` for all 7 exchanges with correct URLs and headers per design
    - _Requirements: 2.1, 3.1, 7.2, 7.3_

  - [x] 1.3 Create abstract base private client and data models
    - Create `mexc_monitor/trading/private_client_base.py`
    - Define `OrderRequest` and `OrderResponse` dataclasses
    - Define `BasePrivateClient` ABC with abstract methods: `_sign`, `_get_api_key_header`, `place_order`, `cancel_order`, `get_open_orders`
    - Implement `has_credentials()` concrete method
    - Implement `_request_signed()` helper for HTTP requests with signing
    - _Requirements: 2.1, 2.5, 6.3_

- [x] 2. Implement exchange-specific private clients
  - [x] 2.1 Refactor existing MexcPrivateClient to extend BasePrivateClient
    - Modify `mexc_monitor/trading/private_client.py` to inherit from `BasePrivateClient`
    - Implement `_sign()` with HMAC SHA256 on sorted query string
    - Implement `place_order()` returning `OrderResponse`
    - Ensure backward compatibility with existing usage
    - _Requirements: 2.1, 2.3, 3.2, 3.3, 9.1_

  - [x] 2.2 Implement BinancePrivateClient
    - Create `mexc_monitor/trading/clients/__init__.py` and `mexc_monitor/trading/clients/binance_client.py`
    - Implement HMAC SHA256 signing on sorted query string with timestamp + recvWindow
    - Implement `place_order()` for spot `/api/v3/order` endpoint
    - _Requirements: 2.1, 2.3, 2.5_

  - [x] 2.3 Implement BybitPrivateClient
    - Create `mexc_monitor/trading/clients/bybit_client.py`
    - Implement HMAC SHA256 signing on `timestamp+api_key+recv_window+query` with X-BAPI-* headers
    - Implement `place_order()` for Bybit spot order endpoint
    - _Requirements: 2.1, 2.3, 2.5_

  - [x] 2.4 Implement OkxPrivateClient
    - Create `mexc_monitor/trading/clients/okx_client.py`
    - Implement HMAC SHA256 → Base64 signing on `timestamp+method+path+body` with OK-ACCESS-* headers
    - Implement `place_order()` for OKX spot order endpoint
    - _Requirements: 2.1, 2.3, 2.5_

  - [x] 2.5 Implement GateioPrivateClient
    - Create `mexc_monitor/trading/clients/gateio_client.py`
    - Implement HMAC SHA512 signing on `method\npath\nquery\nhashed_body\ntimestamp` with KEY/SIGN/Timestamp headers
    - Implement `place_order()` for Gate.io spot order endpoint
    - _Requirements: 2.1, 2.3, 2.5_

  - [x] 2.6 Implement HtxPrivateClient
    - Create `mexc_monitor/trading/clients/htx_client.py`
    - Implement HMAC SHA256 signing on `method\nhost\npath\nsorted_params` with AccessKeyId in query params
    - Implement `place_order()` for HTX spot order endpoint
    - _Requirements: 2.1, 2.3, 2.5_

  - [x] 2.7 Implement BitgetPrivateClient
    - Create `mexc_monitor/trading/clients/bitget_client.py`
    - Implement HMAC SHA256 → Base64 signing on `timestamp+method+path+body` with ACCESS-* headers
    - Implement `place_order()` for Bitget spot order endpoint
    - _Requirements: 2.1, 2.3, 2.5_

  - [ ]* 2.8 Write property test for request signing determinism (Property 4)
    - **Property 4: Request signing determinism**
    - **Validates: Requirements 2.3**
    - Use Hypothesis to generate arbitrary API secrets and request params, verify `_sign()` is deterministic (same inputs → same output)

- [x] 3. Implement client factory and settings loader
  - [x] 3.1 Create client factory
    - Create `mexc_monitor/trading/client_factory.py`
    - Implement `create_private_client(exchange, market)` that reads env vars, selects base_url by market, instantiates correct client class
    - Define `CLIENT_CLASSES: dict[Exchange, type[BasePrivateClient]]` mapping
    - _Requirements: 2.2, 2.4, 3.1, 7.2, 7.3_

  - [x] 3.2 Create multi-exchange settings loader
    - Create `mexc_monitor/trading/settings_loader.py`
    - Implement `load_trading_settings_for_exchange(exchange, market)` with exchange-specific prefix and MEXC fallback
    - Support all settings fields: enabled, mode, symbol, order_type, order_side, min_net_spread_bps, order_quote_notional, limit_price_offset_bps, loop_interval_sec, max_orders_per_day, max_open_orders, max_consecutive_errors, kill_switch, events_log_path
    - _Requirements: 4.1, 4.2_

  - [ ]* 3.3 Write property test for credential env var convention (Property 3)
    - **Property 3: Credential environment variable convention**
    - **Validates: Requirements 2.2, 3.1**
    - Use Hypothesis to generate exchange names, verify credential lookup uses `{E.upper()}_API_KEY` and `{E.upper()}_API_SECRET`

  - [ ]* 3.4 Write property test for settings fallback chain (Property 7)
    - **Property 7: Settings loading with fallback**
    - **Validates: Requirements 4.1, 4.2**
    - Use Hypothesis to generate combinations of exchange-specific and MEXC env vars, verify correct priority resolution

  - [ ]* 3.5 Write property test for market-based URL routing (Property 9)
    - **Property 9: Market-based endpoint routing**
    - **Validates: Requirements 7.2, 7.3**
    - Use Hypothesis to generate exchange+market combinations, verify base_url matches EXCHANGE_CONFIGS

- [x] 4. Implement Engine Registry
  - [x] 4.1 Create EngineRegistry singleton
    - Create `mexc_monitor/trading/engine_registry.py`
    - Implement singleton pattern with thread-safe `__new__`
    - Implement `get_or_create(exchange, market)` — creates engine with settings + client if not exists, returns existing otherwise
    - Implement `list_engines()` returning metadata for all registered engines
    - Implement `shutdown_all()` stopping all running engines
    - Implement `get(exchange, market)` returning engine or None
    - Implement `reset()` class method for testing
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 4.2 Write property test for registry idempotence (Property 1)
    - **Property 1: Registry get-or-create idempotence**
    - **Validates: Requirements 1.1, 1.2, 1.3**
    - Use Hypothesis to generate sequences of get_or_create calls, verify same key → same instance, distinct keys → distinct instances

  - [ ]* 4.3 Write property test for engine state isolation (Property 2)
    - **Property 2: Engine state isolation**
    - **Validates: Requirements 1.4, 4.3, 4.4**
    - Use Hypothesis to create multiple engines, modify state of one, verify others unchanged

- [x] 5. Extend TradingEngine for multi-exchange support
  - [x] 5.1 Extend TradingEngine to accept BasePrivateClient and exchange/market params
    - Modify `mexc_monitor/trading/engine.py`
    - Add `exchange: Exchange` and `market: Market` constructor params
    - Accept `private_client: BasePrivateClient` instead of creating MexcPrivateClient internally
    - Add `order_type` and `order_side` to TradingSettings
    - Implement `_place_order_from_row()` with MARKET (no price) vs LIMIT (bid/ask + offset) logic
    - Maintain independent state per instance: kill_switch, order counters, error counters, event log
    - _Requirements: 4.3, 4.4, 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 5.2 Write property test for order type price handling (Property 6)
    - **Property 6: Order type determines price handling**
    - **Validates: Requirements 2.5, 6.3, 6.4**
    - Use Hypothesis to generate order requests with MARKET/LIMIT types, verify MARKET has no price, LIMIT has calculated price

- [x] 6. Checkpoint - Ensure all backend core tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Implement Backend API multi-engine endpoints
  - [x] 7.1 Add exchange availability endpoint
    - Add `GET /api/trading/exchanges` endpoint to `backend/main.py`
    - Return list of all supported exchanges with: exchange name, available (bool), paper_only (bool), markets, spot_base_url, futures_base_url, order_types
    - Check env vars for credential availability
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x] 7.2 Add engine list endpoint and modify existing endpoints
    - Add `GET /api/trading/engines` endpoint returning all registered engine instances
    - Add `_resolve_engine(exchange, market)` helper defaulting to mexc/spot
    - Modify `GET /api/trading/status`, `POST /api/trading/start`, `POST /api/trading/stop`, `POST /api/trading/kill-switch`, `POST /api/trading/run-once`, `PATCH /api/trading/settings` to accept optional `exchange` and `market` query params
    - Default to "mexc"/"spot" when params omitted
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 9.2_

  - [x] 7.3 Add shutdown hook and auto-start logic
    - Register `EngineRegistry.shutdown_all()` on FastAPI shutdown event
    - On startup, auto-start engines for exchanges with `{EXCHANGE}_TRADING_ENABLED=true`
    - Maintain MEXC auto-start backward compatibility
    - _Requirements: 1.5, 9.1, 9.4_

  - [ ]* 7.4 Write property test for API backward compatibility defaults (Property 8)
    - **Property 8: API backward compatibility defaults**
    - **Validates: Requirements 5.8, 9.2**
    - Use Hypothesis to generate API calls with/without exchange/market params, verify defaults resolve to MEXC/SPOT

  - [ ]* 7.5 Write property test for credential-based availability detection (Property 5)
    - **Property 5: Credential-based availability detection**
    - **Validates: Requirements 2.4, 10.2, 10.3**
    - Use Hypothesis to generate combinations of present/missing env vars, verify availability response correctness

- [x] 8. Implement Frontend Trading Admin multi-exchange UI
  - [x] 8.1 Add exchange and market selectors to TradingAdminModal
    - Add `SupportedExchange`, `MarketType`, `OrderType`, `OrderSide` TypeScript types
    - Add `ExchangeAvailability` interface
    - Add state: `selectedExchange`, `selectedMarket`, `exchanges`
    - Fetch `/api/trading/exchanges` on mount and populate exchange list
    - Render exchange selector (dropdown/tabs) with availability badges (green=live, yellow=paper-only)
    - Render market selector (spot/futures toggle)
    - _Requirements: 8.1, 8.2, 8.6_

  - [x] 8.2 Add order type and side selectors
    - Add `OrderType` selector (LIMIT/MARKET radio buttons) in settings form
    - Add `OrderSide` selector (BUY/SELL radio buttons) in settings form
    - Include `order_type` and `order_side` in `RuntimePatch` type
    - Wire selectors to PATCH /api/trading/settings calls
    - _Requirements: 8.3, 8.4, 6.5_

  - [x] 8.3 Wire exchange/market selection to API calls
    - Update all API calls (status, start, stop, kill-switch, run-once, settings) to include `exchange` and `market` query params
    - Reload status when exchange or market selection changes
    - Display running/stopped state independently per engine
    - Default to MEXC/spot on initial load
    - _Requirements: 8.5, 8.7, 9.3_

- [x] 9. Final checkpoint - Ensure all tests pass and integration works
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- Backend uses Python (FastAPI), frontend uses TypeScript (React)
- All existing MEXC-only behavior preserved via backward-compatible defaults

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["2.1", "3.2"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "3.1"] },
    { "id": 3, "tasks": ["2.8", "3.3", "3.4", "3.5"] },
    { "id": 4, "tasks": ["4.1", "5.1"] },
    { "id": 5, "tasks": ["4.2", "4.3", "5.2"] },
    { "id": 6, "tasks": ["7.1", "7.2", "7.3"] },
    { "id": 7, "tasks": ["7.4", "7.5", "8.1"] },
    { "id": 8, "tasks": ["8.2", "8.3"] }
  ]
}
```
