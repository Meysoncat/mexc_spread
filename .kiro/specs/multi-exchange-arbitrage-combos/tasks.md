# Implementation Plan: Multi-Exchange Arbitrage Combos

## Overview

Расширение модуля межбиржевого арбитража (`mexc_monitor/arbitrage/`) для поддержки всех комбинаций из 8 бирж (28 уникальных пар). Реализация включает: `ExchangePair` и `PairConfig` value objects, `ArbitrageEngineRegistry` для управления движками, `ExchangeAdapterFactory` для создания адаптеров, расширение `ArbitrageEngine` для работы с произвольными парами, REST API endpoints, конфигурационный файл `config/arb_pairs.json`, и React UI для мультиселекта бирж с управлением парами. Код размещается в `mexc_monitor/arbitrage/`, тесты в `tests/test_arbitrage/`.

## Tasks

- [ ] 1. Create value objects and data models
  - [ ] 1.1 Implement ExchangePair and PositionKey value objects
    - Create `mexc_monitor/arbitrage/pair_models.py`
    - Implement `ExchangePair` frozen dataclass with lexicographic normalization in `__post_init__`
    - Implement `pair_id` property and `from_pair_id` classmethod
    - Implement `PositionKey` frozen dataclass with `(symbol, pair_id)` fields
    - _Requirements: 2.2, 3.1_

  - [ ] 1.2 Implement PairConfig data model
    - Add `PairConfig` dataclass to `mexc_monitor/arbitrage/pair_models.py`
    - Include all fields: enabled, mode, symbols, entry_threshold_bps, exit_threshold_bps, max_position_notional_usdt, max_concurrent_trades, max_pending_sec, max_hold_sec, kill_switch, loop_interval_sec, fee_a_taker_bps, fee_b_taker_bps
    - Implement `from_global_config()` classmethod for default initialization
    - _Requirements: 4.1, 4.4, 2.3_

  - [ ]* 1.3 Write property test for ExchangePair normalization (Property 2)
    - **Property 2: Lexicographic normalization is idempotent and order-independent**
    - **Validates: Requirements 2.2**
    - Use Hypothesis to generate arbitrary exchange name pairs and verify ExchangePair(A,B) == ExchangePair(B,A) and normalization is idempotent

  - [ ]* 1.4 Write property test for pair generation count (Property 1)
    - **Property 1: Pair generation count equals combinatorial formula**
    - **Validates: Requirements 2.1**
    - Use Hypothesis to generate subsets of exchanges (size 2–8) and verify len(generate_pairs(subset)) == N*(N-1)/2

- [ ] 2. Implement ExchangeAdapterFactory and adapter protocol
  - [ ] 2.1 Create ExchangeAdapterFactory
    - Create `mexc_monitor/arbitrage/adapter_factory.py`
    - Define `ExchangeAdapter` Protocol with methods: `exchange_name`, `get_spread_buffer_key(symbol)`, `place_market_order(...)`, `is_configured()`
    - Implement `ExchangeAdapterFactory` class with `create_adapter(exchange)` and `is_configured(exchange)` methods
    - Support all 8 exchanges: asterdex, binance, bitget, bybit, gateio, htx, mexc, okx
    - Use existing exchange clients from `mexc_monitor/{exchange}/client.py` and `mexc_monitor/trading/private_client.py`
    - _Requirements: 5.1, 5.2, 5.3_

  - [ ]* 2.2 Write property test for spread buffer key format (Property 9)
    - **Property 9: Spread buffer key format correctness**
    - **Validates: Requirements 5.4**
    - Use Hypothesis to generate valid symbols and verify each adapter returns correctly formatted key (PREFIX:SYMBOL)

- [ ] 3. Implement ArbitrageEngineRegistry
  - [ ] 3.1 Create ArbitrageEngineRegistry core
    - Create `mexc_monitor/arbitrage/registry.py`
    - Implement `ArbitrageEngineRegistry` with `threading.Lock` for thread safety
    - Implement `generate_pairs(exchanges)` — generate all unique ExchangePair combinations
    - Implement `get_available_exchanges()` — list exchanges with availability status
    - Implement `activate_pair(pair, config)` — create and start engine for pair
    - Implement `deactivate_pair(pair_id)` — stop and remove engine
    - Implement `start_all()` / `stop_all()` — global engine control
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 6.1, 6.4, 6.5_

  - [ ] 3.2 Implement per-pair status and aggregation
    - Add `get_pair_status(pair_id)` — return status dict for specific engine
    - Add `get_aggregated_status()` — return combined stats across all engines
    - Add `update_pair_config(pair_id, patch)` — update config without restarting others
    - _Requirements: 8.1, 8.2, 4.2_

  - [ ]* 3.3 Write property test for activate/deactivate round-trip (Property 3)
    - **Property 3: Activate/deactivate round-trip preserves registry consistency**
    - **Validates: Requirements 1.2, 1.3, 1.5**
    - Use Hypothesis to generate valid ExchangePairs, activate then deactivate, verify registry returns to prior state

  - [ ]* 3.4 Write property test for engine isolation (Property 4)
    - **Property 4: Engine isolation — operations on one engine do not affect others**
    - **Validates: Requirements 1.5, 4.2, 6.5**
    - Use Hypothesis to generate two distinct pairs, perform operations on one, verify other is unchanged

  - [ ]* 3.5 Write property test for aggregation correctness (Property 11)
    - **Property 11: Aggregation correctness**
    - **Validates: Requirements 8.1**
    - Use Hypothesis to generate engines with known stats, verify aggregated totals equal sums

  - [ ]* 3.6 Write property test for global start/stop (Property 13)
    - **Property 13: Global start/stop affects all engines**
    - **Validates: Requirements 6.4**
    - Use Hypothesis to generate sets of active engines, verify start_all/stop_all affects all

- [ ] 4. Implement configuration persistence
  - [ ] 4.1 Implement persist/restore for ArbitrageEngineRegistry
    - Add `persist()` method — serialize active pairs and configs to `config/arb_pairs.json`
    - Add `restore()` method — read JSON, recreate pairs and configs on startup
    - Handle missing/corrupted file gracefully (log warning, start empty)
    - Auto-persist on config changes (within 5 seconds)
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [ ]* 4.2 Write property test for config persistence round-trip (Property 10)
    - **Property 10: Configuration persistence round-trip**
    - **Validates: Requirements 4.3, 9.1, 9.2**
    - Use Hypothesis to generate valid pairs with configs, serialize/deserialize, verify equivalence

- [ ] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Extend ArbitrageEngine for multi-pair support
  - [ ] 6.1 Refactor ArbitrageEngine to accept arbitrary exchange adapters
    - Modify `mexc_monitor/arbitrage/engine.py` to accept `ExchangePair`, `PairConfig`, `adapter_a`, `adapter_b` in constructor
    - Replace hardcoded MEXC/Aster references with adapter calls
    - Use `PositionKey` for position tracking instead of plain symbol
    - Read fees from `PairConfig.fee_a_taker_bps` / `fee_b_taker_bps`
    - Use `adapter.get_spread_buffer_key(symbol)` for SpreadBuffer lookups
    - _Requirements: 1.2, 3.1, 3.2, 3.4, 5.4, 6.1_

  - [ ]* 6.2 Write property test for position key uniqueness (Property 5)
    - **Property 5: Position key uniqueness by (symbol, exchange_pair)**
    - **Validates: Requirements 3.1, 3.2**
    - Use Hypothesis to generate symbols and distinct pairs, verify positions are independently tracked

  - [ ]* 6.3 Write property test for max concurrent trades enforcement (Property 6)
    - **Property 6: Max concurrent trades enforcement per engine**
    - **Validates: Requirements 3.4**
    - Use Hypothesis to generate engines with max_concurrent_trades=N, present >N opportunities, verify never exceeds N

  - [ ]* 6.4 Write property test for default config derivation (Property 7)
    - **Property 7: Default config derivation from global configuration**
    - **Validates: Requirements 2.3, 4.4**
    - Use Hypothesis to generate global configs, verify PairConfig.from_global_config produces valid configs with all required fields

- [ ] 7. Implement fault isolation and thread management
  - [ ] 7.1 Implement engine thread wrapper with fault isolation
    - Add `_run_engine_thread(pair_id)` to registry with try/except around engine loop
    - On crash: log error, mark engine as stopped, other engines continue
    - Implement daemon threads with naming convention `arb-engine-{pair_id}`
    - _Requirements: 6.2, 6.3_

  - [ ]* 7.2 Write property test for fault isolation (Property 12)
    - **Property 12: Fault isolation — one engine crash does not affect others**
    - **Validates: Requirements 6.3**
    - Use Hypothesis to generate sets of engines, simulate crash in one, verify others continue

  - [ ]* 7.3 Write property test for unavailable exchanges exclusion (Property 8)
    - **Property 8: Unavailable exchanges excluded from pair generation**
    - **Validates: Requirements 5.3**
    - Use Hypothesis to generate exchange sets with some unavailable, verify generated pairs exclude unavailable

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Implement REST API endpoints
  - [ ] 9.1 Create API router for multi-pair arbitrage
    - Add endpoints to `backend/main.py` under `/api/arbitrage/` prefix:
      - `GET /api/arbitrage/pairs` — list all pairs with status and config
      - `POST /api/arbitrage/pairs/{pair_id}/start` — start specific engine
      - `POST /api/arbitrage/pairs/{pair_id}/stop` — stop specific engine
      - `PATCH /api/arbitrage/pairs/{pair_id}/settings` — update pair config
      - `GET /api/arbitrage/summary` — aggregated statistics
      - `POST /api/arbitrage/exchanges` — update selected exchanges, regenerate pairs
    - Wire endpoints to `ArbitrageEngineRegistry`
    - Return response schemas as defined in design document
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [ ]* 9.2 Write unit tests for API endpoints
    - Test response structure for each endpoint
    - Test validation (invalid pair_id, missing fields in PATCH)
    - Test error handling (engine not found, fewer than 2 exchanges)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

- [ ] 10. Implement Frontend — MultiPairArbitragePage
  - [ ] 10.1 Create MultiPairArbitragePage component
    - Create `frontend/src/MultiPairArbitragePage.tsx`
    - Implement exchange multi-select control showing all 8 exchanges with availability status
    - Implement pair grid displaying auto-generated pairs with toggle controls (enable/disable)
    - Show total pairs count and active pairs count
    - Visually indicate unavailable exchanges (greyed out with tooltip)
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

  - [ ] 10.2 Create PairConfigPanel and AggregatedStats components
    - Create `frontend/src/PairConfigPanel.tsx` — per-pair settings editor (thresholds, fees, mode)
    - Create `frontend/src/AggregatedStats.tsx` — summary row per active pair (open positions, net PNL, running state)
    - Poll `GET /api/arbitrage/pairs` and `GET /api/arbitrage/summary` every 3 seconds
    - Wire start/stop toggle to POST endpoints
    - _Requirements: 7.4, 8.1, 8.3, 4.1, 4.2_

- [ ] 11. Wire frontend into App navigation
  - [ ] 11.1 Integrate MultiPairArbitragePage into App.tsx
    - Add navigation entry/button for multi-pair arbitrage page
    - Wire page into existing routing/navigation structure
    - _Requirements: 7.1_

- [ ] 12. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate 13 universal correctness properties using Hypothesis
- Unit tests validate specific examples and edge cases
- The existing `mexc_monitor/arbitrage/engine.py` is refactored in-place (task 6.1), not rewritten from scratch
- All new Python code goes under `mexc_monitor/arbitrage/`, tests under `tests/test_arbitrage/`
- Frontend follows existing React patterns from the project

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4", "2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.4", "3.5", "3.6"] },
    { "id": 4, "tasks": ["4.1"] },
    { "id": 5, "tasks": ["4.2", "6.1"] },
    { "id": 6, "tasks": ["6.2", "6.3", "6.4", "7.1"] },
    { "id": 7, "tasks": ["7.2", "7.3", "9.1"] },
    { "id": 8, "tasks": ["9.2", "10.1"] },
    { "id": 9, "tasks": ["10.2", "11.1"] }
  ]
}
```
