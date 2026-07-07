# Requirements Document

## Introduction

Расширение системы межбиржевого арбитража с единственной пары MEXC↔AsterDEX до всех комбинаций из 8 поддерживаемых бирж (MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget, AsterDEX — 28 уникальных пар). Каждая комбинация бирж получает независимый ArbitrageEngine с собственными настройками, порогами и позициями. Позиции ключатся по (symbol, exchange_pair). UI предоставляет мультиселект бирж с автогенерацией пар и ручным управлением.

## Glossary

- **System**: Система межбиржевого арбитража (Multi-Exchange Arbitrage System)
- **ArbitrageEngine**: Экземпляр движка арбитража, обслуживающий одну конкретную пару бирж
- **Exchange_Pair**: Упорядоченная пара бирж (exchange_a, exchange_b), где exchange_a < exchange_b лексикографически, формирующая уникальный идентификатор комбинации
- **Supported_Exchanges**: Множество из 8 бирж: MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget, AsterDEX
- **Position_Key**: Составной ключ (symbol, exchange_pair) для уникальной идентификации позиции
- **ExchangeAdapter**: Протокол унифицированного интерфейса торговых операций на бирже
- **BasePrivateClient**: Базовый класс приватного клиента из модуля multi-exchange-trading-admin, предоставляющий аутентификацию и HTTP-подпись для каждой биржи
- **Pair_Config**: Набор независимых настроек (пороги, лимиты, комиссии) для конкретной Exchange_Pair
- **Engine_Registry**: Реестр всех активных экземпляров ArbitrageEngine, индексированный по Exchange_Pair
- **UI_Pair_Selector**: Компонент интерфейса для выбора бирж и управления парами

## Requirements

### Requirement 1: Engine Registry Management

**User Story:** As a trader, I want the system to manage multiple independent arbitrage engines for different exchange pairs, so that I can run arbitrage across all desired combinations simultaneously.

#### Acceptance Criteria

1. THE System SHALL maintain an Engine_Registry that maps each active Exchange_Pair to a dedicated ArbitrageEngine instance
2. WHEN a new Exchange_Pair is activated, THE System SHALL create a new ArbitrageEngine instance with the Pair_Config for that Exchange_Pair
3. WHEN an Exchange_Pair is deactivated, THE System SHALL stop the corresponding ArbitrageEngine and remove the instance from Engine_Registry
4. THE System SHALL support up to 28 concurrent ArbitrageEngine instances (all combinations of 8 Supported_Exchanges)
5. WHILE multiple ArbitrageEngine instances are running, THE System SHALL isolate each engine so that positions, statistics, and events of one engine do not affect another engine

### Requirement 2: Exchange Pair Generation

**User Story:** As a trader, I want the system to automatically generate all valid exchange pair combinations from my selected exchanges, so that I do not have to manually configure each pair.

#### Acceptance Criteria

1. WHEN a set of exchanges is selected, THE System SHALL generate all unique Exchange_Pair combinations using the formula N*(N-1)/2 where N is the number of selected exchanges
2. THE System SHALL normalize each Exchange_Pair by ordering exchange names lexicographically to ensure uniqueness
3. THE System SHALL assign a default Pair_Config to each newly generated Exchange_Pair
4. IF fewer than 2 exchanges are selected, THEN THE System SHALL display a validation message indicating that at least 2 exchanges are required

### Requirement 3: Position Keying by Symbol and Exchange Pair

**User Story:** As a trader, I want positions to be uniquely identified by both symbol and exchange pair, so that the same symbol can have independent arbitrage positions across different exchange combinations.

#### Acceptance Criteria

1. THE System SHALL use Position_Key (symbol, exchange_pair) as the unique identifier for each arbitrage position
2. WHILE an ArbitrageEngine is evaluating entry opportunities, THE System SHALL allow the same symbol to have open positions in different Exchange_Pairs simultaneously
3. WHEN displaying positions in the UI, THE System SHALL show the Exchange_Pair alongside each position's symbol
4. THE System SHALL enforce the max_concurrent_trades limit independently per ArbitrageEngine instance

### Requirement 4: Independent Pair Configuration

**User Story:** As a trader, I want to configure thresholds and settings independently for each exchange pair, so that I can optimize parameters based on the specific characteristics of each pair.

#### Acceptance Criteria

1. THE System SHALL store a separate Pair_Config for each Exchange_Pair containing: entry_threshold_bps, exit_threshold_bps, max_position_notional_usdt, max_concurrent_trades, max_hold_sec, taker_fee_bps for each exchange in the pair, mode (paper/live), and enabled flag
2. WHEN a Pair_Config is updated, THE System SHALL apply the changes to the corresponding ArbitrageEngine without restarting other engines
3. THE System SHALL persist Pair_Config values across application restarts
4. WHEN a new Exchange_Pair is created, THE System SHALL initialize Pair_Config with sensible defaults derived from the global arbitrage configuration

### Requirement 5: Exchange Adapter Extension

**User Story:** As a developer, I want each supported exchange to have an ExchangeAdapter implementation backed by BasePrivateClient, so that the arbitrage engine can trade uniformly across all exchanges.

#### Acceptance Criteria

1. THE System SHALL provide an ExchangeAdapter implementation for each of the 8 Supported_Exchanges
2. THE System SHALL instantiate each ExchangeAdapter using the BasePrivateClient from multi-exchange-trading-admin for authentication and request signing
3. WHEN an ExchangeAdapter is not configured (missing API credentials), THE System SHALL mark the corresponding exchange as unavailable and exclude the exchange from pair generation
4. THE System SHALL expose a get_spread_buffer_key method on each ExchangeAdapter that returns the correct key format for retrieving price data from SpreadBuffer

### Requirement 6: Concurrent Engine Execution

**User Story:** As a trader, I want all active arbitrage engines to run concurrently without blocking each other, so that opportunities are not missed due to sequential processing.

#### Acceptance Criteria

1. THE System SHALL run each active ArbitrageEngine in a separate thread with its own event loop
2. WHILE multiple engines are running, THE System SHALL ensure that shared resources (SpreadBuffer, logging) are accessed in a thread-safe manner
3. WHEN an ArbitrageEngine encounters an unhandled exception, THE System SHALL log the error and continue running other engines without interruption
4. THE System SHALL provide a global start/stop mechanism that starts or stops all active engines simultaneously
5. THE System SHALL provide per-pair start/stop controls that affect only the targeted ArbitrageEngine

### Requirement 7: UI Exchange Multi-Select and Pair Management

**User Story:** As a trader, I want a UI component to select multiple exchanges and manage the generated pairs, so that I can easily configure which exchange combinations to monitor.

#### Acceptance Criteria

1. THE UI_Pair_Selector SHALL display a multi-select control listing all Supported_Exchanges with their availability status
2. WHEN exchanges are selected in the multi-select, THE UI_Pair_Selector SHALL display the auto-generated list of Exchange_Pairs with toggle controls for enabling/disabling each pair
3. THE UI_Pair_Selector SHALL display the total number of generated pairs and the number of currently active pairs
4. WHEN a user toggles an Exchange_Pair, THE System SHALL start or stop the corresponding ArbitrageEngine within 2 seconds
5. THE UI_Pair_Selector SHALL visually indicate exchanges that are unavailable due to missing credentials

### Requirement 8: Aggregated Status and Per-Pair Monitoring

**User Story:** As a trader, I want to see both aggregated statistics across all pairs and detailed per-pair status, so that I can monitor overall performance and drill into specific pairs.

#### Acceptance Criteria

1. THE System SHALL provide an aggregated status endpoint that returns combined statistics (total trades, net PNL, open positions count) across all active engines
2. THE System SHALL provide a per-pair status endpoint that returns the full status of a specific ArbitrageEngine identified by Exchange_Pair
3. WHEN displaying the arbitrage dashboard, THE System SHALL show a summary row for each active Exchange_Pair with key metrics (open positions, net PNL, running state)
4. THE System SHALL update the aggregated status within 3 seconds of any position change in any engine

### Requirement 9: Configuration Persistence

**User Story:** As a trader, I want my exchange pair configurations and active pairs to be saved, so that the system restores my setup after restart.

#### Acceptance Criteria

1. THE System SHALL persist the list of active Exchange_Pairs and their Pair_Configs to a configuration file
2. WHEN the application starts, THE System SHALL restore all previously active Exchange_Pairs and their configurations from the persisted state
3. WHEN a Pair_Config is modified via the API or UI, THE System SHALL persist the change within 5 seconds
4. IF the persisted configuration file is missing or corrupted, THEN THE System SHALL start with an empty Engine_Registry and log a warning

### Requirement 10: API Endpoints for Multi-Pair Arbitrage

**User Story:** As a frontend developer, I want REST API endpoints for managing multi-pair arbitrage, so that the UI can control and monitor all exchange pairs.

#### Acceptance Criteria

1. THE System SHALL expose GET /api/arbitrage/pairs returning the list of all Exchange_Pairs with their status and configuration
2. THE System SHALL expose POST /api/arbitrage/pairs/{pair_id}/start to start a specific ArbitrageEngine
3. THE System SHALL expose POST /api/arbitrage/pairs/{pair_id}/stop to stop a specific ArbitrageEngine
4. THE System SHALL expose PATCH /api/arbitrage/pairs/{pair_id}/settings to update the Pair_Config for a specific Exchange_Pair
5. THE System SHALL expose GET /api/arbitrage/summary returning aggregated statistics across all active engines
6. THE System SHALL expose POST /api/arbitrage/exchanges to update the set of selected exchanges and trigger pair regeneration
