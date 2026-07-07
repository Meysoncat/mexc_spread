# Implementation Plan: Lead-Lag Arbitrage

## Overview

Реализация модуля Lead-Lag Arbitrage для мониторинга и анализа временных задержек между обновлениями цен на разных биржах. Модуль включает: сбор данных order book через WebSocket с нескольких бирж, кольцевой буфер цен, детектор lag через кросс-корреляцию, генератор сигналов с z-score, хранение в SQLite, агрегированную статистику, REST API и React UI дашборд на `/lead-lag`. Все Python-компоненты размещаются в `mexc_monitor/lead_lag/`, тесты в `tests/test_lead_lag/`, фронтенд в `frontend/src/pages/LeadLagPage.tsx`.

## Tasks

- [x] 1. Set up project structure, data models, and configuration
  - [x] 1.1 Create module directory and data models
    - Create `mexc_monitor/lead_lag/__init__.py`
    - Create `mexc_monitor/lead_lag/models.py` with dataclasses: `PriceSnapshot`, `SpreadSnapshot`, `LagEstimate`, `LeadLagSignal`, `LeadLagStats`, `LeadLagConfig`
    - Include enums: `SignalDirection` (long/short), `SignalStatus` (active/resolved/expired)
    - Include all fields as specified in the design document Data Models section
    - _Requirements: 2.6, 3.4, 4.1, 4.6, 6.1_

  - [x] 1.2 Create configuration loader and validator
    - Create `mexc_monitor/lead_lag/config.py`
    - Implement `load_lead_lag_config()` that reads from `config/external_apis.json` секция `"lead_lag"`
    - Implement `validate_config(config)` enforcing: `z_score_entry_threshold > z_score_exit_threshold`, `z_score_entry_threshold > 0`, `z_score_exit_threshold >= 0`, `signal_timeout_sec > 0`, `rolling_window_sec > 0`, `min_spread_bps >= 0`, `lag_estimation_interval_sec > 0`, `price_buffer_history_sec > 0`, `symbols` непустой, `lagger_exchanges` непустой, `leader_exchange` не в `lagger_exchanges`, `assumed_taker_fee_bps >= 0`, `market` равен "spot" или "futures", `ws_urls` содержит URL для leader и каждого lagger
    - При невалидном JSON или отсутствии секции — использовать значения по умолчанию
    - При ошибке валидации — вернуть сообщение с каждым нарушенным правилом и фактическим значением
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [ ]* 1.3 Write property test for configuration validation (Property 12)
    - **Property 12: Config Validation Correctness**
    - **Validates: Requirements 9.3, 9.4**
    - Use Hypothesis to generate arbitrary config values and verify validation passes iff all constraints hold simultaneously

- [x] 2. Implement Price Buffer
  - [x] 2.1 Implement PriceBuffer class
    - Create `mexc_monitor/lead_lag/price_buffer.py`
    - Implement `PriceBuffer` with thread-safe ring buffer (threading.Lock)
    - Implement `update(exchange, symbol, mid, timestamp_ms)` — O(1) insert into deque per (exchange, symbol) pair, max 18000 записей
    - Implement `get_latest(exchange, symbol)` — O(1) доступ к последнему PriceSnapshot
    - Implement `get_all_latest(symbol)` — текущие цены по всем биржам для символа
    - Implement `get_history(exchange, symbol, last_n_sec)` — упорядоченный список, не более 2000 точек с равномерным прореживанием при превышении
    - Implement `get_spread(symbol, leader, lagger)` — вычисление spread_bps = 10000 × (leader_mid − lagger_mid) / leader_mid; возврат None если leader_mid <= 0 или данные одной биржи отсутствуют
    - Auto-evict записей старше `max_history_sec` при чтении
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [ ]* 2.2 Write property test for Price Buffer boundedness (Property 1)
    - **Property 1: Price Buffer Boundedness**
    - **Validates: Requirements 2.2, 2.4**
    - Use Hypothesis to generate sequences of price updates and verify buffer never stores data older than `max_history_sec`

  - [ ]* 2.3 Write property test for spread computation accuracy (Property 2)
    - **Property 2: Spread Computation Accuracy**
    - **Validates: Requirements 2.6, 4.2**
    - Use Hypothesis to generate valid leader_mid and lagger_mid (both > 0) and verify spread_bps equals exactly `10000 * (leader_mid - lagger_mid) / leader_mid`

- [x] 3. Implement Lag Detector
  - [x] 3.1 Implement LagDetector class
    - Create `mexc_monitor/lead_lag/detector.py`
    - Implement `LagDetector` with: `update_estimate(symbol, price_buffer)`, `get_estimate(symbol)`, `get_leader(symbol)`, `get_all_estimates()`
    - Implement кросс-корреляцию между mid-ценовыми рядами с использованием numpy
    - Определять lag_ms как сдвиг с максимальным коэффициентом корреляции
    - Динамически определять роль лидера (биржа, чьи изменения предшествуют)
    - Предоставлять: lag_ms, correlation coefficient, confidence score (наблюдения / максимум за окно), sample_count
    - Пропускать вычисление если наблюдений < 20 (пометить как unavailable)
    - Гарантировать lag >= 0 и <= price_buffer_history_sec × 1000
    - Обновлять оценки с настраиваемым интервалом (по умолчанию 30 секунд)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ]* 3.2 Write property test for lag estimate bounds (Property 4)
    - **Property 4: Lag Estimate Bounds**
    - **Validates: Requirement 3.5**
    - Use Hypothesis to generate price series and verify all lag estimates are >= 0 and <= `price_buffer_history_sec * 1000`

  - [ ]* 3.3 Write property test for lag detection correctness (Property 3)
    - **Property 3: Lag Detection Correctness**
    - **Validates: Requirements 3.1, 3.3**
    - Use Hypothesis to generate synthetic price series with known delay D and verify detector identifies correct leader and estimates lag within tolerance of D

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement Signal Generator
  - [x] 5.1 Implement SignalGenerator class
    - Create `mexc_monitor/lead_lag/signals.py`
    - Implement `SignalGenerator` with: `tick()`, `get_active_signals()`, `get_recent_signals(limit)`
    - Implement z-score вычисление: rolling mean/std спреда за `rolling_window_sec`
    - Генерация сигнала когда: z-score > z_score_entry_threshold И |spread| > min_spread_bps И нет ACTIVE сигнала для (symbol, lagger_exchange)
    - Фиксация при создании: symbol, leader_exchange, lagger_exchange, direction (long/short), z_score, entry_spread_bps, leader_mid, lagger_mid, estimated_lag_ms, created_at
    - Разрешение (RESOLVED): когда z-score < z_score_exit_threshold — фиксация resolved_at, actual_lag_ms, exit_spread_bps
    - Истечение (EXPIRED): когда время жизни > signal_timeout_sec × 1000 мс — фиксация resolved_at, exit_spread_bps; не применяется к уже RESOLVED сигналам
    - Гарантия: не более одного ACTIVE сигнала на (symbol, lagger_exchange)
    - Вычисление theoretical_pnl_bps = entry_spread_bps − exit_spread_bps − 2 × assumed_taker_fee_bps
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 5.2 Write property test for signal generation threshold (Property 5)
    - **Property 5: Signal Generation Threshold**
    - **Validates: Requirements 4.1, 4.5**
    - Use Hypothesis to generate price states and verify signal created iff z-score > threshold AND spread > min_spread_bps AND no existing ACTIVE signal

  - [ ]* 5.3 Write property test for no duplicate active signals (Property 6)
    - **Property 6: No Duplicate Active Signals**
    - **Validates: Requirement 4.5**
    - Use Hypothesis to generate sequences of price events and verify at most one ACTIVE signal per (symbol, lagger_exchange) at any time

  - [ ]* 5.4 Write property test for signal lifecycle completeness (Property 7)
    - **Property 7: Signal Lifecycle Completeness**
    - **Validates: Requirements 4.3, 4.4**
    - Use Hypothesis to generate signal sequences and verify every signal transitions from ACTIVE to exactly one of RESOLVED or EXPIRED within signal_timeout_sec

  - [ ]* 5.5 Write property test for theoretical PnL correctness (Property 8)
    - **Property 8: Theoretical PnL Correctness**
    - **Validates: Requirement 4.6**
    - Use Hypothesis to generate entry_spread_bps, exit_spread_bps, assumed_taker_fee_bps and verify theoretical_pnl_bps = entry - exit - 2 × fee

- [x] 6. Implement Signal Store
  - [x] 6.1 Implement LeadLagStore class
    - Create `mexc_monitor/lead_lag/store.py`
    - Implement SQLite schema: таблица `lead_lag_signals` с полями id, symbol, leader_exchange, lagger_exchange, direction, z_score, entry_spread_bps, leader_mid_at_signal, lagger_mid_at_signal, estimated_lag_ms, status, created_at, resolved_at, actual_lag_ms, exit_spread_bps, theoretical_pnl_bps
    - Implement `save_signal(signal)` — вставка нового сигнала
    - Implement `update_signal(signal_id, resolution)` — обновление resolved_at, actual_lag_ms, exit_spread_bps, theoretical_pnl_bps
    - Implement `query_signals(filters)` — фильтры по symbol, time range, status, direction; лимит 1000, сортировка created_at DESC
    - Implement in-memory buffer (до 1000 сигналов) при ошибке записи в SQLite
    - Retry записи каждые 30 секунд; FIFO eviction при переполнении буфера
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 10.4, 10.5_

  - [ ]* 6.2 Write property test for signal store round-trip (Property 9)
    - **Property 9: Signal Store Round-Trip**
    - **Validates: Requirement 5.1**
    - Use Hypothesis to generate valid LeadLagSignal objects, save and query back, verify all fields preserved

  - [ ]* 6.3 Write property test for signal store filter correctness (Property 10)
    - **Property 10: Signal Store Filter Correctness**
    - **Validates: Requirement 5.3**
    - Use Hypothesis to generate sets of signals and filter combinations, verify all returned signals match criteria and no matching signals omitted

- [x] 7. Implement Stats Engine
  - [x] 7.1 Implement StatsEngine class
    - Create `mexc_monitor/lead_lag/stats.py`
    - Implement `StatsEngine` with: `summary(window_hours)`, `per_symbol_stats(window_hours)`, `lag_distribution(symbol)`
    - Вычислять: total_signals, resolved_signals, expired_signals, win_rate, avg_lag_ms, median_lag_ms, avg_theoretical_pnl_bps, total_theoretical_pnl_bps, signals_per_hour
    - win_rate = resolved с pnl > 0 / total resolved
    - Per-symbol breakdown: количество сигналов, средний lag, средний PnL
    - Lag distribution: гистограмма с бакетами по 50 мс
    - При отсутствии сигналов: нулевые счётчики, null для avg/median, пустой массив для распределения
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 7.2 Write property test for stats consistency (Property 11)
    - **Property 11: Stats Consistency**
    - **Validates: Requirements 6.1, 6.2, 6.3**
    - Use Hypothesis to generate sets of signals and verify total_signals == resolved + expired + active, and per-symbol counts sum to total

- [x] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Implement WS Manager
  - [x] 9.1 Implement LeadLagWSManager class
    - Create `mexc_monitor/lead_lag/ws_manager.py`
    - Implement `LeadLagWSManager` with: `start()`, `stop()`, `is_running()`, `connection_status()`
    - Установка WebSocket-соединений ко всем настроенным биржам (Binance, MEXC, Bybit, OKX) в течение 10 секунд
    - Подписка на bookTicker/ticker потоки для настроенных символов
    - Нормализация сообщений в PriceSnapshot (exchange, symbol, bid, ask, mid, timestamp_ms с monotonic clock)
    - Валидация: отбрасывать сообщения без bid/ask/symbol или с bid <= 0, ask < bid; инкрементировать счётчик отброшенных
    - Переподключение с экспоненциальным backoff (1с → 2с → 4с → ... → 60с max)
    - Статус подключения: connected/disconnected/stale; stale если нет данных > 5 секунд
    - Автоматический возврат в connected при получении валидного сообщения
    - Исключение stale бирж из генерации сигналов
    - Не блокировать остальные соединения при ошибке одного
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 9.2 Implement engine orchestrator
    - Create `mexc_monitor/lead_lag/engine.py`
    - Implement `LeadLagEngine` orchestrating: WS Manager → Price Buffer → Lag Detector → Signal Generator → Store
    - Implement engine status: running, degraded (все laggers disconnected/stale), no_leader (Binance disconnected/stale)
    - Приостановка генерации сигналов при degraded/no_leader
    - Возобновление при восстановлении (5 секунд непрерывных данных)
    - Background threads для: lag estimation (каждые N секунд), signal tick (continuous), stale check
    - _Requirements: 10.1, 10.2, 10.3_

- [x] 10. Implement REST API endpoints
  - [x] 10.1 Create API endpoints for lead-lag
    - Add endpoints to `backend/main.py`:
      - `GET /api/lead-lag/status` — running, connections, symbols_monitored, active_signals_count, uptime_sec
      - `GET /api/lead-lag/signals` — params: active (bool), symbol (str), limit (1-1000, default 50); sorted by created_at DESC
      - `GET /api/lead-lag/stats` — param: window_hours (1-168, default 24)
      - `GET /api/lead-lag/prices?symbol=X` — mid-цены по всем биржам; 404 если символ не найден
      - `GET /api/lead-lag/lag-estimates` — текущие оценки lag для всех символов
      - `POST /api/lead-lag/start` — запуск движка (идемпотентно)
      - `POST /api/lead-lag/stop` — остановка движка (идемпотентно)
    - Валидация параметров: HTTP 422 при limit < 1 или > 1000, window_hours < 1 или > 168
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [ ]* 10.2 Write unit tests for API endpoints
    - Test response structure for each endpoint
    - Test parameter validation (422 responses)
    - Test idempotent start/stop
    - Test 404 for unknown symbol in /prices
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

- [x] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Implement Frontend — LeadLagPage
  - [x] 12.1 Create LeadLagPage.tsx and sub-components
    - Create `frontend/src/pages/LeadLagPage.tsx`
    - Implement `SignalFeed` — лента активных сигналов: symbol, direction, z_score, entry_spread_bps, estimated_lag_ms, время жизни (секунды), max 50, sorted by created_at DESC
    - Implement `LagHeatmap` — матрица exchange × symbol с lag в мс, цвет пропорционален значению
    - Implement `StatsPanel` — win_rate, avg_lag_ms, theoretical_pnl (суммарный bps), signal_count; выбор периода: 1h, 6h, 24h
    - Implement `PriceComparisonChart` — график mid-цен по выбранному символу на всех биржах
    - Implement `ConnectionStatus` — индикаторы: connected (< 5с), stale (5-30с), disconnected (> 30с или нет соединения)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 12.2 Implement polling and state management
    - Polling сигналов и статусов каждые 3 секунды
    - Polling статистики каждые 10 секунд
    - Отображение сообщения о неактивном движке + кнопка запуска при running=false
    - Пустое состояние при отсутствии активных сигналов
    - Индикатор ошибки соединения при таймауте > 10 секунд; продолжение polling
    - _Requirements: 8.6, 8.7, 8.8, 8.9_

  - [x] 12.3 Wire LeadLagPage into router
    - Add route `/lead-lag` to React Router configuration
    - Add navigation link to LeadLagPage in sidebar/menu
    - _Requirements: 8.1_

- [x] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties using Hypothesis (12 properties total)
- Unit tests validate specific examples and edge cases
- The existing `ws_futures.py` and `mexc_monitor/binance/client.py` serve as reference patterns for WebSocket management
- Frontend follows the same page pattern as existing pages in `frontend/src/pages/`
- All Python code goes under `mexc_monitor/lead_lag/`, tests under `tests/test_lead_lag/`
- numpy is required as a new dependency for cross-correlation computation

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "5.4", "5.5", "6.1"] },
    { "id": 5, "tasks": ["6.2", "6.3", "7.1"] },
    { "id": 6, "tasks": ["7.2", "9.1"] },
    { "id": 7, "tasks": ["9.2", "10.1"] },
    { "id": 8, "tasks": ["10.2", "12.1"] },
    { "id": 9, "tasks": ["12.2", "12.3"] }
  ]
}
```
