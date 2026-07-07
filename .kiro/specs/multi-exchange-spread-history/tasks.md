# Implementation Plan: Multi-Exchange Spread History

## Overview

Расширение модуля записи истории спредов для параллельного опроса множества бирж (MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget) с сохранением в единую SQLite таблицу с колонкой `exchange`. Включает: миграцию схемы БД, конфигурацию бирж и retention policy, параллельный опрос через ThreadPoolExecutor, API-эндпоинты фильтрации/сравнения, и фронтенд-компоненты (фильтр по бирже, ComparisonChart).

## Tasks

- [ ] 1. Schema migration and ORM model extension
  - [ ] 1.1 Add `exchange` column to SpreadSnapshot ORM model
    - Add `exchange: Mapped[str] = mapped_column(String(32), nullable=False, default="mexc")` to SpreadSnapshot in `mexc_monitor/orm/models.py`
    - Add composite index `idx_spread_exchange_market_sym_time` on (exchange, market, symbol, observed_at) in `__table_args__`
    - _Requirements: 1.1, 1.3_

  - [ ] 1.2 Extend schema migration in `mexc_monitor/orm/engine.py`
    - Add migration logic in `_migrate_spread_snapshots_columns` to ALTER TABLE adding `exchange VARCHAR(32) NOT NULL DEFAULT 'mexc'`
    - Create index `idx_spread_exchange_market_sym_time` via `CREATE INDEX IF NOT EXISTS`
    - Existing records automatically get default "mexc" value preserving data
    - _Requirements: 1.2, 1.4_

- [ ] 2. Configuration extension
  - [ ] 2.1 Add history configuration fields to `mexc_monitor/config.py`
    - Add fields: `history_exchanges: tuple[str, ...]`, `history_retention_days: int`, `history_max_workers: int`
    - Set defaults: `history_exchanges=("mexc",)`, `history_retention_days=30`, `history_max_workers=4`
    - Parse `history.exchanges`, `history.retention_days`, `history.max_workers` from `external_apis.json`
    - _Requirements: 3.1, 3.4, 4.1, 7.1_

  - [ ] 2.2 Update `config/external_apis.json` with history section
    - Add `"history"` section with fields: `exchanges`, `retention_days`, `max_workers`, `interval_sec`
    - Set exchanges list to `["mexc", "binance", "bybit", "okx", "gateio", "htx", "bitget"]`
    - Set `retention_days: 30`, `max_workers: 4`, `interval_sec: 60`
    - _Requirements: 4.1, 3.1, 7.1_

- [ ] 3. History store extension
  - [ ] 3.1 Extend `append_snapshot` in `mexc_monitor/history_store.py`
    - Add `exchange: str = "mexc"` keyword parameter to `append_snapshot`
    - Pass `exchange` value to each `SpreadSnapshot` constructor in the batch
    - _Requirements: 1.2, 2.5_

  - [ ] 3.2 Extend `query_recent` in `mexc_monitor/history_store.py`
    - Add `exchange: str | None = None` and `exchanges: list[str] | None = None` parameters
    - When `exchange` is set, filter with `.where(SpreadSnapshot.exchange == exchange)`
    - When `exchanges` list is set, filter with `.where(SpreadSnapshot.exchange.in_(exchanges))`
    - Include `exchange` field in returned dict rows
    - _Requirements: 5.2, 5.3, 6.2_

- [ ] 4. History worker rewrite for multi-exchange parallel polling
  - [ ] 4.1 Add exchange validation and interval clamping in `mexc_monitor/history_worker.py`
    - Implement `_validate_exchanges(configured)` filtering against `SUPPORTED_EXCHANGES` frozenset
    - Log warning for unsupported exchange names
    - Implement `_effective_interval(configured)` returning `max(10.0, configured)`
    - _Requirements: 4.2, 4.3, 4.4, 7.2, 7.3, 7.4_

  - [ ] 4.2 Implement exchange snapshot loaders in `mexc_monitor/history_store.py`
    - Add `_load_exchange_snapshot(exchange, market, settings)` dispatcher function
    - Route to existing `safe_load_snapshot` for MEXC
    - Route to `_load_binance_snapshot`, `_load_bybit_snapshot`, `_load_okx_snapshot`, `_load_gateio_snapshot`, `_load_htx_snapshot`, `_load_bitget_snapshot` for other exchanges
    - Each loader calls the respective exchange client's snapshot function and returns `(DataFrame | None, error_str | None)`
    - _Requirements: 2.1, 2.5_

  - [ ] 4.3 Implement parallel polling cycle in `mexc_monitor/history_worker.py`
    - Rewrite `_run_snapshot_cycle` to use `ThreadPoolExecutor(max_workers=s.history_max_workers)`
    - Submit `_poll_exchange(exchange, markets, settings)` for each validated exchange
    - Collect results via `as_completed`, call `append_snapshot(path, market, df, exchange=exchange)` for each
    - Catch and log exceptions per-exchange without interrupting others
    - Log total rows and elapsed time at debug level after cycle
    - _Requirements: 2.2, 2.3, 2.4_

  - [ ] 4.4 Implement retention policy in `mexc_monitor/history_worker.py`
    - Add `_apply_retention(path, retention_days)` function
    - When `retention_days > 0`: delete records where `observed_at < (now - retention_days)`
    - When `retention_days == 0`: skip deletion entirely
    - Call `_apply_retention` at end of each snapshot cycle
    - _Requirements: 3.2, 3.3_

  - [ ] 4.5 Update main `_loop` in `mexc_monitor/history_worker.py`
    - Read `history_exchanges` from settings, validate with `_validate_exchanges`
    - Use `_effective_interval` for sleep duration
    - Call `_run_snapshot_cycle` with validated exchanges
    - _Requirements: 2.1, 4.2, 7.2_

- [ ] 5. Checkpoint - Ensure data layer works
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. API endpoints for exchange filtering and comparison
  - [ ] 6.1 Extend `/api/history` endpoint in `backend/main.py`
    - Add optional `exchange: str | None = Query(None)` parameter
    - Pass `exchange` to `query_recent` call
    - _Requirements: 5.1, 5.2, 5.3_

  - [ ] 6.2 Add `/api/history/compare` endpoint in `backend/main.py`
    - Accept `symbol` (required), `market`, `exchanges` (comma-separated), `since`, `limit`
    - Parse comma-separated exchanges into list
    - Call `query_recent` with `exchanges=exchange_list`
    - Group results by exchange in response dict
    - _Requirements: 6.1, 6.2_

  - [ ] 6.3 Add `/api/history/exchanges` endpoint in `backend/main.py`
    - Query `SELECT DISTINCT exchange FROM spread_snapshots`
    - Return sorted list of exchanges with available data
    - Handle missing DB file gracefully (return empty list)
    - _Requirements: 5.4_

- [ ] 7. Frontend: Exchange filter and comparison chart
  - [ ] 7.1 Add exchange filter dropdown to history page
    - Fetch available exchanges from `/api/history/exchanges`
    - Add dropdown/select component for filtering by exchange
    - Pass selected exchange to history data fetch calls
    - _Requirements: 5.1, 5.2_

  - [ ] 7.2 Implement ComparisonChart component in `frontend/src/components/ComparisonChart.tsx`
    - Accept `symbol`, `market`, `exchanges` props
    - Fetch data from `/api/history/compare` endpoint
    - Render one line per exchange using lightweight-charts
    - Assign unique colors per exchange from `EXCHANGE_COLORS` map
    - Display legend with exchange names and colors
    - _Requirements: 6.3, 6.4_

  - [ ] 7.3 Wire ComparisonChart into history page
    - Add multi-exchange selector (checkboxes or multi-select) for comparison mode
    - Toggle between single-exchange filter view and comparison view
    - Pass selected exchanges and symbol to ComparisonChart
    - _Requirements: 6.1, 6.3, 6.4_

- [ ] 8. Checkpoint - Ensure API and frontend work
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Property-based tests
  - [ ]* 9.1 Write property test for exchange field integrity (Property 1)
    - **Property 1: Exchange field integrity**
    - Generate random exchange names from SUPPORTED_EXCHANGES and random ticker DataFrames
    - Call `append_snapshot` with generated exchange, query back, verify all records have correct exchange value
    - Verify default "mexc" when no exchange specified
    - **Validates: Requirements 1.1, 1.2**

  - [ ]* 9.2 Write property test for fault isolation (Property 2)
    - **Property 2: Fault isolation in parallel polling**
    - Generate random subsets of exchanges, mock some to raise exceptions
    - Run `_run_snapshot_cycle`, verify healthy exchanges still produce stored rows
    - Verify row count from healthy exchanges is independent of failures
    - **Validates: Requirements 2.3**

  - [ ]* 9.3 Write property test for retention policy (Property 3)
    - **Property 3: Retention policy correctness**
    - Generate random SpreadSnapshot records with various `observed_at` timestamps
    - Generate random positive `retention_days` values
    - Run `_apply_retention`, verify no records older than cutoff remain
    - Test with `retention_days=0`, verify all records preserved
    - **Validates: Requirements 3.2, 3.3**

  - [ ]* 9.4 Write property test for exchange configuration validation (Property 4)
    - **Property 4: Exchange configuration validation**
    - Generate random lists of strings (mix of valid/invalid exchange names)
    - Call `_validate_exchanges`, verify output contains only members of SUPPORTED_EXCHANGES
    - Verify all valid names from input are preserved in output
    - **Validates: Requirements 4.2, 4.3, 4.4**

  - [ ]* 9.5 Write property test for API exchange filtering (Property 5)
    - **Property 5: API exchange filtering correctness**
    - Populate DB with records from random exchanges
    - Query with single exchange filter: verify all returned records match
    - Query without filter: verify records from all exchanges returned
    - Query with comma-separated list: verify all returned records in requested set
    - **Validates: Requirements 5.2, 5.3, 6.2**

  - [ ]* 9.6 Write property test for interval minimum enforcement (Property 6)
    - **Property 6: Interval minimum enforcement**
    - Generate random float values (including negatives, zero, small positives, large values)
    - Call `_effective_interval`, verify result is always >= 10
    - Verify values >= 10 are returned unchanged
    - **Validates: Requirements 7.3**

- [ ] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- Exchange clients already exist (from multi-exchange-integration spec); this spec wires them into the history recording pipeline
- Tests use pytest + hypothesis, located in tests/ directory
- The design uses Python with SQLAlchemy ORM, FastAPI backend, and TypeScript/React frontend

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "2.2"] },
    { "id": 1, "tasks": ["1.2", "3.1", "3.2"] },
    { "id": 2, "tasks": ["4.1", "4.2"] },
    { "id": 3, "tasks": ["4.3", "4.4", "4.5"] },
    { "id": 4, "tasks": ["6.1", "6.2", "6.3"] },
    { "id": 5, "tasks": ["7.1", "7.2"] },
    { "id": 6, "tasks": ["7.3"] },
    { "id": 7, "tasks": ["9.1", "9.2", "9.3", "9.4", "9.5", "9.6"] }
  ]
}
```
