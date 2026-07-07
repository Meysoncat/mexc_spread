# Implementation Plan: Multi-Exchange Integration

## Overview

Расширение MEXC Spread Monitor восемью новыми биржами (Binance, Bybit, OKX, Gate.io, HTX, Bitget, dYdX, Hyperliquid) по паттерну AsterDEX/Lighter. Реализация включает: конфигурацию в external_apis.json, Python-клиенты с httpx, нормализацию в BookTickerRow, расширение FastAPI-эндпоинтов, обновление ExchangeSwitcher UI, и property-based тесты для 8 correctness properties.

## Tasks

- [x] 1. Configuration and shared infrastructure
  - [x] 1.1 Add exchange configurations to config/external_apis.json
    - Add sections for "binance", "bybit", "okx", "gateio", "htx", "bitget", "dydx", "hyperliquid" with base_url, timeout_sec, endpoints as specified in design
    - Each section follows the schema from the design document (spot_base_url/futures_base_url for multi-market exchanges)
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [x] 1.2 Create exchange module directory structure and __init__.py files
    - Create directories: mexc_monitor/binance/, mexc_monitor/bybit/, mexc_monitor/okx/, mexc_monitor/gateio/, mexc_monitor/htx/, mexc_monitor/bitget/, mexc_monitor/dydx/, mexc_monitor/hyperliquid/
    - Each __init__.py exports PublicClient class and {exchange}_snapshot_rows function
    - _Requirements: 1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1, 8.1_

- [x] 2. CEX exchange clients (Group 1: Binance, Bybit, OKX)
  - [x] 2.1 Implement Binance client (mexc_monitor/binance/client.py)
    - BinancePublicClient with _get() helper, BinanceApiError, BinanceBookTicker dataclass
    - book_tickers(market="spot"|"futures") method using spot /api/v3/ticker/bookTicker and futures /fapi/v1/ticker/bookTicker
    - klines(symbol, interval, limit) method for both spot and futures
    - binance_snapshot_rows(client, market="futures") normalization function
    - Load config from external_apis.json "binance" section with fallback defaults
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [x] 2.2 Implement Bybit client (mexc_monitor/bybit/client.py)
    - BybitPublicClient with _get() helper, BybitApiError, BybitBookTicker dataclass
    - book_tickers() method using GET /v5/market/tickers?category=linear
    - klines(symbol, interval, limit) method using GET /v5/market/kline
    - bybit_snapshot_rows(client) normalization function with funding_rate
    - Interval mapping: 5m→"5", 15m→"15", 1h→"60", 4h→"240", 1d→"D"
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 2.3 Implement OKX client (mexc_monitor/okx/client.py)
    - OkxPublicClient with _get() helper, OkxApiError, OkxBookTicker dataclass
    - book_tickers(inst_type="SPOT"|"SWAP") method using GET /api/v5/market/tickers
    - klines(symbol, interval, limit) method using GET /api/v5/market/candles
    - okx_snapshot_rows(client, market="futures") normalization function
    - Symbol normalization: "BTC-USDT" / "BTC-USDT-SWAP" → "BTCUSDT"
    - Interval mapping: 5m→"5m", 15m→"15m", 1h→"1H", 4h→"4H", 1d→"1D"
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 2.4 Write property test for normalization validity (Property 1) — CEX Group 1
    - **Property 1: Normalization produces valid BookTickerRow**
    - Generate random raw ticker dicts for Binance/Bybit/OKX, verify all BookTickerRow fields are valid
    - **Validates: Requirements 1.2, 2.2, 3.2**

  - [ ]* 2.5 Write property test for symbol normalization (Property 2) — OKX
    - **Property 2: Symbol normalization correctness**
    - Generate random OKX-format symbols ("X-Y", "X-Y-SWAP"), verify uppercase alphanumeric output with USDT suffix
    - **Validates: Requirements 3.3**

- [x] 3. CEX exchange clients (Group 2: Gate.io, HTX, Bitget)
  - [x] 3.1 Implement Gate.io client (mexc_monitor/gateio/client.py)
    - GateioPublicClient with _get() helper, GateioApiError, GateioBookTicker dataclass
    - book_tickers(market="spot"|"futures") using GET /api/v4/spot/tickers and /api/v4/futures/usdt/tickers
    - klines(symbol, interval, limit) for both spot and futures
    - gateio_snapshot_rows(client, market="futures") normalization function
    - Symbol normalization: "BTC_USDT" → "BTCUSDT"
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 3.2 Implement HTX client (mexc_monitor/htx/client.py)
    - HtxPublicClient with _get() helper, HtxApiError, HtxBookTicker dataclass
    - book_tickers(market="spot"|"futures") using GET /market/tickers and /linear-swap-ex/market/detail/batch_merged
    - klines(symbol, interval, limit) for both spot and futures
    - htx_snapshot_rows(client, market="futures") normalization function
    - Symbol normalization: "btcusdt" → "BTCUSDT"
    - Interval mapping: 5m→"5min", 15m→"15min", 1h→"60min", 4h→"4hour", 1d→"1day"
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 3.3 Implement Bitget client (mexc_monitor/bitget/client.py)
    - BitgetPublicClient with _get() helper, BitgetApiError, BitgetBookTicker dataclass
    - book_tickers() using GET /api/v2/mix/market/tickers?productType=USDT-FUTURES
    - klines(symbol, interval, limit) using GET /api/v2/mix/market/candles
    - bitget_snapshot_rows(client) normalization function with funding_rate
    - Symbol normalization: strip contract suffix, keep "BTCUSDT"
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 3.4 Write property test for symbol normalization (Property 2) — Gate.io, HTX, Bitget
    - **Property 2: Symbol normalization correctness**
    - Generate random Gate.io ("X_Y"), HTX ("xy" lowercase), Bitget ("XY" + suffix) symbols
    - Verify uppercase alphanumeric output with USDT suffix, base asset preserved
    - **Validates: Requirements 4.3, 5.3, 6.3**

- [x] 4. DEX exchange clients (dYdX, Hyperliquid)
  - [x] 4.1 Implement dYdX client (mexc_monitor/dydx/client.py)
    - DydxPublicClient with _get() helper, DydxApiError, DydxBookTicker dataclass
    - book_tickers() using GET /v4/perpetualMarkets and GET /v4/orderbooks/perpetualMarket/{market}
    - klines(symbol, interval, limit) using GET /v4/candles/perpetualMarkets/{market}
    - dydx_snapshot_rows(client) normalization function
    - Symbol normalization: "BTC-USD" → "BTCUSD"
    - Interval mapping: 5m→"5MINS", 15m→"15MINS", 1h→"1HOUR", 4h→"4HOURS", 1d→"1DAY"
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 4.2 Implement Hyperliquid client (mexc_monitor/hyperliquid/client.py)
    - HyperliquidPublicClient with _post() helper (POST /info), HyperliquidApiError, HyperliquidBookTicker dataclass
    - book_tickers() using POST /info with {"type": "allMids"} and {"type": "metaAndAssetCtxs"}
    - klines(symbol, interval, limit) — POST /info with {"type": "candleSnapshot"}
    - hyperliquid_snapshot_rows(client) normalization function with funding_rate
    - Symbol normalization: "BTC" → "BTCUSDT"
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 4.3 Write property test for symbol normalization (Property 2) — dYdX, Hyperliquid
    - **Property 2: Symbol normalization correctness**
    - Generate random dYdX ("X-Y") and Hyperliquid ("X") symbols
    - Verify uppercase alphanumeric output with USD/USDT suffix, base asset preserved
    - **Validates: Requirements 7.3, 8.3**

  - [ ]* 4.4 Write property test for error message format (Property 3)
    - **Property 3: Error message contains exchange identity**
    - Generate random HTTP status codes (400-599) and exchange names
    - Verify raised exception message contains exchange name and status code/error type
    - **Validates: Requirements 1.6, 2.4, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5**

- [x] 5. Checkpoint - Ensure all exchange clients work
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Backend endpoint extensions
  - [x] 6.1 Extend _SUPPORTED_EXCHANGES and refactor _build_dex_snapshot_payload in backend/main.py
    - Update _SUPPORTED_EXCHANGES list to include all 11 exchanges
    - Create _EXCHANGE_SNAPSHOT_MAP dict mapping exchange → (snapshot_rows_function, default_market)
    - Refactor _build_dex_snapshot_payload into generic _build_exchange_snapshot_payload(exchange, market)
    - Handle multi-market exchanges (binance, okx, gateio, htx) with market parameter
    - Update /api/snapshot endpoint to use new dispatch mechanism
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [x] 6.2 Extend /api/klines/batch endpoint for new exchanges
    - Add interval mappings for all 8 new exchanges (_INTERVAL_TO_{EXCHANGE} dicts)
    - Extend _fetch_klines_for_exchange function with cases for each new exchange
    - Support multi-market klines for Binance, OKX, Gate.io, HTX
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [ ]* 6.3 Write property test for invalid exchange rejection (Property 6)
    - **Property 6: Invalid exchange rejection**
    - Generate random strings not in _SUPPORTED_EXCHANGES
    - Verify /api/snapshot returns HTTP 400 with full supported exchanges list
    - **Validates: Requirements 10.3**

  - [ ]* 6.4 Write property test for interval mapping completeness (Property 7)
    - **Property 7: Interval mapping completeness**
    - For all (exchange, interval) pairs from {5m, 15m, 1h, 4h, 1d}, verify non-empty mapped string
    - **Validates: Requirements 11.3, 11.4**

  - [ ]* 6.5 Write property test for error propagation (Property 8)
    - **Property 8: Error propagation to endpoint**
    - Generate random exceptions from exchange clients, verify /api/snapshot returns ok=false with exchange name in error and empty rows
    - **Validates: Requirements 14.1, 14.4**

- [x] 7. Checkpoint - Ensure backend endpoints work
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Frontend ExchangeSwitcher updates
  - [x] 8.1 Update Exchange type and ExchangeSwitcher component
    - Extend Exchange type in types.ts with all 8 new exchanges
    - Refactor ExchangeSwitcher to display CEX/DEX groups with compact layout
    - Add EXCHANGE_GROUPS constant with CEX (MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget) and DEX (AsterDEX, Lighter, dYdX, Hyperliquid)
    - Implement responsive layout (compact tabs or dropdown for overflow)
    - _Requirements: 12.1, 12.2, 12.5_

  - [x] 8.2 Implement market switcher logic for multi-market exchanges
    - Add MULTI_MARKET_EXCHANGES constant: ["mexc", "binance", "okx", "gateio", "htx"]
    - Show spot/futures toggle when selected exchange supports multiple markets
    - Hide market switcher for single-market exchanges (Bybit, Bitget, AsterDEX, Lighter, dYdX, Hyperliquid)
    - _Requirements: 12.3, 12.4_

  - [ ]* 8.3 Write unit tests for ExchangeSwitcher component
    - Test rendering of all 11 exchanges in correct groups
    - Test market switcher visibility logic
    - Test responsive layout behavior
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

- [ ] 9. Property-based tests for data integrity
  - [ ]* 9.1 Write property test for spread metric invariants (Property 4)
    - **Property 4: Spread metric invariants**
    - Generate random bid/ask pairs (bid > 0, ask > 0), verify mid = (bid+ask)/2, spread_abs = ask-bid, spread_bps = 10000*spread_abs/mid within 1e-8 tolerance
    - **Validates: Requirements 13.1, 13.2, 13.3**

  - [ ]* 9.2 Write property test for BookTickerRow serialization round-trip (Property 5)
    - **Property 5: BookTickerRow serialization round-trip**
    - Generate random valid BookTickerRow instances, serialize via dataclasses.asdict → json.dumps → json.loads
    - Verify all numeric fields preserved to 8 decimal places
    - **Validates: Requirements 13.6**

- [x] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All exchange clients follow the AsterDEX pattern (see mexc_monitor/aster/client.py)
- Config loading uses config/external_apis.json with fallback defaults hardcoded in each client
- Tests use pytest + hypothesis, located in tests/ directory

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "3.1", "3.2", "3.3", "4.1", "4.2"] },
    { "id": 2, "tasks": ["2.4", "2.5", "3.4", "4.3", "4.4"] },
    { "id": 3, "tasks": ["6.1", "6.2", "8.1"] },
    { "id": 4, "tasks": ["6.3", "6.4", "6.5", "8.2"] },
    { "id": 5, "tasks": ["8.3", "9.1", "9.2"] }
  ]
}
```
