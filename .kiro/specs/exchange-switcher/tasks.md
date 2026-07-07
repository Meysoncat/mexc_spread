# Implementation Plan: Exchange Switcher

## Overview

Реализация переключателя бирж (Exchange Switcher) в основном окне MEXC Spread Monitor. Включает: создание Python-клиента для Lighter DEX, расширение бэкенд-эндпоинта `/api/snapshot` параметром `exchange`, нормализацию данных AsterDEX/Lighter в `BookTickerRow`, создание React-компонента `ExchangeSwitcher`, интеграцию двухуровневой навигации (биржа → рынок) в `App.tsx`, и обновление автообновления для работы с активной биржей.

## Tasks

- [x] 1. Lighter client and configuration
  - [x] 1.1 Add Lighter configuration to `config/external_apis.json`
    - Add section `"lighter"` with `base_url`, `timeout_sec`, and `endpoints` (orderbooks, orderbook_details, orderbook_orders, funding_rates, candles)
    - Use base_url: `https://mainnet.zklighter.elliot.ai`, timeout_sec: 15
    - _Requirements: 6.1, 6.2_

  - [x] 1.2 Create `mexc_monitor/lighter/__init__.py` and `mexc_monitor/lighter/client.py`
    - Create `mexc_monitor/lighter/__init__.py` exporting `LighterPublicClient`, `LighterApiError`
    - Implement `LighterPublicClient` class following the pattern of `AsterPublicClient`
    - Implement `__init__(self, base_url, timeout_sec)` reading defaults from `config/external_apis.json`
    - Implement `_get(path, params)` helper with httpx, error handling, and `LighterApiError` exception
    - Implement `orderbooks(filter="perp")` → `GET /api/v1/orderBooks` returning `list[LighterOrderbookSummary]`
    - Implement `orderbook_details(filter="perp")` → `GET /api/v1/orderBookDetails` returning `list[LighterMarketInfo]`
    - Implement `orderbook_orders(market_id, limit=5)` → `GET /api/v1/orderBookOrders`
    - Implement `funding_rates()` → `GET /api/v1/funding-rates` returning `list[LighterFundingRate]`
    - Define dataclasses: `LighterMarketInfo`, `LighterOrderbookSummary`, `LighterFundingRate`
    - Handle missing "lighter" config section with defaults (base_url, timeout_sec)
    - _Requirements: 5.1, 5.3, 5.4, 6.1, 6.2, 6.3_

  - [x] 1.3 Implement `lighter_snapshot_rows()` normalization function
    - Create normalization function in `mexc_monitor/lighter/client.py` (or separate `snapshot.py`)
    - Call `orderbooks(filter="perp")` and `orderbook_details(filter="perp")`
    - Map `market_id` → symbol name using details, normalize symbol format (e.g. "ETH-PERP" → "ETHUSDT")
    - Normalize prices using `supported_price_decimals` from details
    - Compute `mid = (bid + ask) / 2`, `spread_abs = ask - bid`, `spread_bps = 10000 * spread_abs / mid`
    - Return `list[BookTickerRow]` compatible with existing pipeline
    - _Requirements: 5.1, 5.2_

  - [x]* 1.4 Write property test for Lighter normalization (Property 6)
    - **Property 6: Lighter data normalization round-trip consistency**
    - **Validates: Requirements 5.2**
    - Use Hypothesis to generate random orderbook data with positive bid/ask prices
    - Verify `mid == (bid + ask) / 2`, `spread_abs == ask - bid`, `spread_bps == 10000 * spread_abs / mid` within floating point tolerance

- [x] 2. Backend: extend `/api/snapshot` with exchange parameter
  - [x] 2.1 Implement AsterDEX snapshot normalization
    - Create `aster_snapshot_rows()` function (in `mexc_monitor/aster/client.py` or new `snapshot.py`)
    - Call `_aster_public.book_ticker()` and `_aster_public.ticker_24h()`
    - Normalize AsterDEX data into `list[BookTickerRow]` format (symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote)
    - Merge book_ticker with 24h volume data by symbol
    - _Requirements: 4.3_

  - [x] 2.2 Extend `/api/snapshot` endpoint with `exchange` parameter
    - Add `exchange: str = Query("mexc", description="mexc, asterdex или lighter")` parameter to `snapshot()` in `backend/main.py`
    - When `exchange="mexc"` — use existing `_build_snapshot_payload(market)` logic unchanged
    - When `exchange="asterdex"` — call `aster_snapshot_rows()`, convert to DataFrame, return in same response format
    - When `exchange="lighter"` — call `lighter_snapshot_rows()`, convert to DataFrame, return in same response format
    - For DEX exchanges, set `market="perp"` in response, skip execution_model enrichment
    - Return HTTP 400 with `{"ok": false, "error": "Unknown exchange: X", "supported": ["mexc", "asterdex", "lighter"]}` for unknown exchange values
    - Apply caching per exchange key (not just market)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x]* 2.3 Write property test for unknown exchange validation (Property 5)
    - **Property 5: Unknown exchange validation**
    - **Validates: Requirements 4.5**
    - Use Hypothesis to generate random strings not in {"mexc", "asterdex", "lighter"}
    - Verify endpoint returns HTTP 400 with supported exchanges list

  - [x]* 2.4 Write property test for unified format normalization (Property 2)
    - **Property 2: Unified format normalization**
    - **Validates: Requirements 2.3, 5.2**
    - Use Hypothesis to generate valid raw ticker data (positive bid/ask)
    - Verify normalization produces valid BookTickerRow with correct mathematical relationships

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Frontend: ExchangeSwitcher component and type updates
  - [x] 4.1 Update `frontend/src/types.ts` with Exchange type
    - Add `export type Exchange = "mexc" | "asterdex" | "lighter";`
    - Add optional `exchange?: Exchange` field to `SnapshotResponse` interface
    - _Requirements: 1.1_

  - [x] 4.2 Create `frontend/src/ExchangeSwitcher.tsx` component
    - Implement `ExchangeSwitcher` component with props: `active: Exchange`, `onChange: (exchange: Exchange) => void`, `disabled?: boolean`
    - Render three button-tabs: "MEXC", "AsterDEX", "Lighter"
    - Visually highlight the active exchange (accent color, border)
    - Style consistently with existing UI (Tailwind classes matching the app's design system)
    - Support `disabled` prop to prevent switching during loading
    - _Requirements: 1.1, 1.2, 1.4_

  - [x] 4.3 Integrate ExchangeSwitcher into `App.tsx`
    - Add state: `const [exchange, setExchange] = useState<Exchange>("mexc")`
    - Place `ExchangeSwitcher` in the top control bar, before the market switcher (spot/futures/cross)
    - When `exchange !== "mexc"` — hide the market switcher (spot/futures/cross), use fixed market type "perp"
    - When `exchange === "mexc"` — show market switcher as before
    - Update `fetchSnapshot()` to include `exchange` parameter in URL: `/api/snapshot?market=${market}&exchange=${exchange}`
    - On exchange change: clear `rows`, reset `loadedAt`, call `load()` immediately
    - Preserve filter state (`search`, `sortBy`, `ascending`) across exchange switches
    - Reset `quoteRaw` to "USDT" when switching to AsterDEX/Lighter
    - _Requirements: 1.2, 1.3, 2.1, 2.4, 3.1, 3.2, 3.3_

  - [x] 4.4 Add loading indicator and error handling for exchange switch
    - Show loading spinner in table area while data loads after exchange switch
    - Display error message with exchange name if fetch fails (e.g. "Ошибка загрузки данных Lighter: ...")
    - Handle empty response (rows: []) with "Нет данных для {exchange}" message
    - Use `AbortController` to cancel in-flight requests when switching exchanges (existing pattern)
    - _Requirements: 2.2, 2.5_

- [x] 5. Frontend: auto-refresh integration
  - [x] 5.1 Update auto-refresh logic for active exchange
    - Modify the existing auto-refresh interval to pass `exchange` parameter in fetch URL
    - When exchange changes with auto-refresh enabled: immediately fetch new exchange data and restart the refresh cycle
    - Ensure the same refresh interval is used for all exchanges
    - _Requirements: 7.1, 7.2, 7.3_

  - [x]* 5.2 Write unit tests for ExchangeSwitcher component
    - Test that ExchangeSwitcher renders three options (MEXC, AsterDEX, Lighter)
    - Test that clicking a tab calls onChange with correct exchange value
    - Test that active exchange is visually highlighted
    - Test disabled state prevents interaction
    - _Requirements: 1.1, 1.4_

  - [x]* 5.3 Write unit tests for exchange-aware fetch logic
    - Test that URL includes correct exchange parameter
    - Test that filter state (search, sortBy, ascending) is preserved across exchange switches
    - Test that rows are cleared on exchange switch
    - _Requirements: 2.1, 3.1, 3.2, 3.3_

- [x] 6. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties using Hypothesis (Python) and fast-check (TypeScript)
- The Lighter client follows the same pattern as `mexc_monitor/aster/client.py` (httpx, dataclasses, error handling)
- The `BookTickerRow` dataclass from `mexc_monitor/models.py` serves as the unified format for all exchanges
- Frontend changes are concentrated in `App.tsx` (state + fetch) and a new `ExchangeSwitcher.tsx` component
- The existing `AsterDexPanel.tsx` remains available as a separate detailed view; the switcher provides quick access in the main table

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "4.1"] },
    { "id": 1, "tasks": ["1.2", "4.2"] },
    { "id": 2, "tasks": ["1.3", "2.1"] },
    { "id": 3, "tasks": ["1.4", "2.2"] },
    { "id": 4, "tasks": ["2.3", "2.4", "4.3"] },
    { "id": 5, "tasks": ["4.4", "5.1"] },
    { "id": 6, "tasks": ["5.2", "5.3"] }
  ]
}
```
