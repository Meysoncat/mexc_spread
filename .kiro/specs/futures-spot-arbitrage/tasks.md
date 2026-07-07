# Implementation Plan: Futures/Spot Arbitrage

## Overview

Реализация модуля арбитража между спотовым и фьючерсным рынками. Модуль включает: расчёт базиса в реальном времени, отслеживание funding rates, движок стратегий (cash-and-carry, reverse, funding arb), управление позициями с PNL-учётом, контроль рисков, REST API, хранение истории базиса в SQLite, и React UI (панель + график). Все компоненты размещаются в `mexc_monitor/futures_arb/` и `tests/test_futures_arb/`.

## Tasks

- [x] 1. Set up project structure, data models, and configuration
  - [x] 1.1 Create module directory and data models
    - Create `mexc_monitor/futures_arb/__init__.py`
    - Create `mexc_monitor/futures_arb/models.py` with dataclasses: `FuturesArbSettings`, `FuturesArbPosition`, `FuturesArbTradeRecord`, `FuturesArbStats`, `BasisSnapshot`, `FundingInfo`, `RiskAlert`
    - Include all fields as specified in the design document
    - _Requirements: 9.1, 8.4, 3.3_

  - [x] 1.2 Create configuration loader
    - Create `mexc_monitor/futures_arb/config.py`
    - Implement `load_futures_arb_settings()` that reads from JSON file (`config/futures_arb.json`) with env variable overrides
    - Implement `validate_settings(settings)` that enforces: `entry_threshold_bps > exit_threshold_bps`, `position_notional_usdt > 0`, `1 <= max_concurrent_positions <= 20`, `1 <= futures_leverage <= 20`
    - _Requirements: 9.1, 9.3_

  - [ ]* 1.3 Write property test for configuration validation (Property 9)
    - **Property 9: Configuration validation**
    - **Validates: Requirements 9.3**
    - Use Hypothesis to generate arbitrary config values and verify validation passes iff all constraints hold

- [x] 2. Implement Basis Calculator
  - [x] 2.1 Implement BasisCalculator class
    - Create `mexc_monitor/futures_arb/basis_calculator.py`
    - Implement `BasisCalculator` with `start()`, `stop()`, `get_current_basis()`, `get_all_basis()` methods
    - Subscribe to Spread Buffer for real-time bid/ask updates for both legs
    - Compute `basis_abs`, `basis_bps`, `executable_cc_bps`, `executable_rcc_bps`, `estimated_apy` per design formulas
    - Mark pairs as "stale" when data for one leg is older than `stale_after_sec`
    - Support all three exchange combos: `mexc_spot+mexc_futures`, `mexc_spot+asterdex_perp`, `asterdex_perp+mexc_futures`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 2.2 Write property test for basis computation (Property 1)
    - **Property 1: Basis computation correctness**
    - **Validates: Requirements 1.1, 1.3, 1.5**
    - Use Hypothesis to generate valid spot/futures bid/ask pairs and verify all formulas produce correct results

  - [ ]* 2.3 Write property test for stale status invariant (Property 2)
    - **Property 2: Stale status invariant**
    - **Validates: Requirements 1.4**
    - Use Hypothesis to generate timestamps and verify stale marking logic

- [x] 3. Implement Funding Tracker
  - [x] 3.1 Implement FundingTracker class
    - Create `mexc_monitor/futures_arb/funding_tracker.py`
    - Implement `FundingTracker` with `start()`, `stop()`, `get_funding()`, `get_all_funding()` methods
    - Poll funding rates every 60 seconds via REST API (MEXC and AsterDEX)
    - Store 30-day history in memory (deque)
    - Compute `avg_7d`, `avg_30d`, `annualized_yield`
    - Generate `funding_direction_changed` event when sign flips
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 3.2 Write property test for funding rate computation (Property 3)
    - **Property 3: Funding rate computation correctness**
    - **Validates: Requirements 2.3, 2.4, 2.5**
    - Use Hypothesis to generate funding rate histories and verify avg/annualized/direction-change logic

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implement Strategy Engine
  - [ ] 5.1 Implement FuturesArbStrategyEngine core
    - Create `mexc_monitor/futures_arb/strategy_engine.py`
    - Implement `FuturesArbStrategyEngine` with lifecycle methods: `start()`, `stop()`, `get_status()`, `update_settings()`
    - Implement background thread with `_step()` loop at configurable `loop_interval_sec`
    - Implement `_check_entry_opportunities()` evaluating all symbols × exchange_combos
    - Implement `_evaluate_cash_and_carry()`, `_evaluate_reverse_cash_and_carry()`, `_evaluate_funding_arbitrage()`
    - Support paper/live modes with identical decision logic
    - Select best exchange_combo (highest executable basis) when multiple combos available for same symbol
    - _Requirements: 3.1, 3.5, 4.1, 5.1, 17.1, 17.2_

  - [ ]* 5.2 Write property test for entry decision correctness (Property 4)
    - **Property 4: Entry decision correctness**
    - **Validates: Requirements 3.1, 4.1, 5.1, 7.5, 7.6, 17.3**
    - Use Hypothesis to generate market states and verify entry happens iff all conditions hold

  - [ ]* 5.3 Write property test for position sizing (Property 5)
    - **Property 5: Position sizing correctness**
    - **Validates: Requirements 3.3**
    - Use Hypothesis to generate notional/price/leverage values and verify qty/margin calculations

  - [ ] 5.4 Implement exit logic in Strategy Engine
    - Implement `_check_open_positions()` with all exit conditions: basis_converged, target_reached, stop_loss, max_duration, margin_critical, delta_critical, funding_direction_reversed, kill_switch
    - Implement one-leg protection: cancel unfilled leg, close filled leg at market
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 3.2, 5.3_

  - [ ]* 5.5 Write property test for exit decision correctness (Property 6)
    - **Property 6: Exit decision correctness**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 5.3, 7.2, 7.4, 7.6**
    - Use Hypothesis to generate open positions with market states and verify exit happens iff at least one condition holds

  - [ ]* 5.6 Write property test for best exchange combo selection (Property 11)
    - **Property 11: Best exchange combo selection**
    - **Validates: Requirements 17.2**
    - Use Hypothesis to generate multiple combos with different basis values and verify best is selected

- [ ] 6. Implement Position Manager
  - [ ] 6.1 Implement PositionManager class
    - Create `mexc_monitor/futures_arb/position_manager.py`
    - Implement `PositionManager` with: `open_position()`, `close_position()`, `update_funding()`, `get_open_positions()`, `get_closed_positions()`, `get_stats()`
    - Implement PNL computation: `total_pnl = basis_pnl + cumulative_funding - entry_fees - exit_fees`
    - Implement `annualized_return` and `net_pnl_bps` calculations
    - Implement `serialize_state()` / `deserialize_state()` for JSON persistence
    - Handle corrupted state file: log error, start empty, send Telegram alert
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 18.1, 18.2, 18.3, 18.4_

  - [ ]* 6.2 Write property test for PNL computation (Property 7)
    - **Property 7: PNL computation correctness**
    - **Validates: Requirements 8.1, 8.2, 8.3, 5.2, 6.5**
    - Use Hypothesis to generate positions with prices/funding/fees and verify PNL formulas

  - [ ]* 6.3 Write property test for serialization round-trip (Property 12)
    - **Property 12: Position serialization round-trip**
    - **Validates: Requirements 18.3**
    - Use Hypothesis to generate valid FuturesArbPosition objects and verify JSON round-trip equivalence

- [ ] 7. Implement Risk Controller
  - [ ] 7.1 Implement RiskController class
    - Create `mexc_monitor/futures_arb/risk_controller.py`
    - Implement `RiskController` with: `check_position()`, `check_total_exposure()`, `is_kill_switch_active()`, `activate_kill_switch()`, `deactivate_kill_switch()`
    - Implement margin ratio checks (warning at 50%, critical at 30%)
    - Implement delta-neutrality checks (warning at 5%, critical at 15%)
    - Implement total exposure limit enforcement
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [ ]* 7.2 Write property test for risk alert generation (Property 8)
    - **Property 8: Risk alert generation**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**
    - Use Hypothesis to generate positions with margin/delta values and verify alerts generated iff thresholds breached

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Implement Basis History Store
  - [ ] 9.1 Implement BasisHistoryStore class
    - Create `mexc_monitor/futures_arb/basis_store.py`
    - Implement `BasisHistoryStore` with: `start()`, `stop()`, `query_history()`
    - Create SQLite table `basis_snapshots` with schema from design
    - Implement periodic recording at configurable interval (default 60s)
    - Implement retention policy: delete records older than `retention_days`
    - _Requirements: 16.1, 16.2, 16.3_

  - [ ]* 9.2 Write property test for data retention invariant (Property 10)
    - **Property 10: Data retention invariant**
    - **Validates: Requirements 16.3, 2.2**
    - Use Hypothesis to generate records with timestamps and verify cleanup removes only expired records

- [ ] 10. Implement REST API endpoints
  - [ ] 10.1 Create API router for futures-arb
    - Add endpoints to `backend/main.py` under `/api/futures-arb/` prefix:
      - `GET /api/futures-arb/status` — engine status + current basis for all pairs
      - `GET /api/futures-arb/positions` — open positions with real-time PNL
      - `GET /api/futures-arb/history` — closed positions (paginated with limit/offset)
      - `POST /api/futures-arb/start` — start engine
      - `POST /api/futures-arb/stop` — stop engine
      - `PATCH /api/futures-arb/settings` — update config at runtime
      - `GET /api/futures-arb/basis-history` — historical basis data for chart
      - `POST /api/futures-arb/close-position` — manual position close
    - Wire endpoints to `FuturesArbStrategyEngine`, `PositionManager`, `BasisHistoryStore`
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 9.2_

  - [ ]* 10.2 Write unit tests for API endpoints
    - Test response structure for each endpoint
    - Test settings validation via PATCH
    - Test error handling (engine not running, invalid params)
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

- [ ] 11. Implement Telegram alerts integration
  - [ ] 11.1 Extend alert service for futures-arb events
    - Add methods to `mexc_monitor/alerts/service.py` for: position opened, position closed, risk alert (critical), funding rate alert
    - Format messages with: symbol, exchange_combo, direction, basis, PNL, funding, hold duration
    - Handle Telegram send failures gracefully (log warning, continue)
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [ ] 12. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Implement Frontend — FuturesArbPanel
  - [ ] 13.1 Create FuturesArbPanel.tsx
    - Create `frontend/src/FuturesArbPanel.tsx` as modal panel (similar to existing `ArbitragePanel.tsx`)
    - Implement tabs: Дашборд | Позиции | История | График | Настройки
    - Implement basis dashboard table: symbol, exchange_combo, spot_mid, futures_mid, basis_bps, funding_rate, estimated_apy, status
    - Support sorting by basis_bps, funding_rate, estimated_apy
    - Highlight rows where basis exceeds entry_threshold (green for CC, red for RCC)
    - Implement positions tab: open positions with real-time PNL, manual close button
    - Implement history tab: closed trades table with full PNL breakdown, filtering by symbol/combo/period
    - Implement settings tab: editable configuration fields
    - Poll API every 3 seconds for updates
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 13.1, 13.2, 13.3, 13.4, 15.1, 15.2, 15.3_

  - [ ] 13.2 Create BasisChart.tsx
    - Create `frontend/src/BasisChart.tsx` using `lightweight-charts` library
    - Display basis (bps) over time for selected pair
    - Show entry markers (green arrows) and exit markers (red arrows)
    - Show horizontal lines at entry_threshold_bps and exit_threshold_bps levels
    - Support time interval selection: 1h, 4h, 24h, 7d
    - Fetch data from `GET /api/futures-arb/basis-history`
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

- [ ] 14. Wire frontend panel into App
  - [ ] 14.1 Integrate FuturesArbPanel into App.tsx
    - Add button/menu item to open FuturesArbPanel in `App.tsx`
    - Add summary stats bar (total positions, unrealized PNL, total funding earned)
    - Wire start/stop/kill-switch controls
    - _Requirements: 13.4_

- [ ] 15. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties using Hypothesis (12 properties total)
- Unit tests validate specific examples and edge cases
- The existing `ArbitrageEngine` (cross-exchange) serves as a reference pattern for the new module
- Frontend follows the same modal panel pattern as `ArbitragePanel.tsx`
- All Python code goes under `mexc_monitor/futures_arb/`, tests under `tests/test_futures_arb/`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1", "3.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2"] },
    { "id": 3, "tasks": ["5.1", "6.1", "7.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "5.4", "6.2", "6.3", "7.2"] },
    { "id": 5, "tasks": ["5.5", "5.6", "9.1"] },
    { "id": 6, "tasks": ["9.2", "10.1", "11.1"] },
    { "id": 7, "tasks": ["10.2", "13.1", "13.2"] },
    { "id": 8, "tasks": ["14.1"] }
  ]
}
```
