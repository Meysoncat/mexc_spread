# Requirements Document

## Introduction

Расширение системы записи истории спредов для поддержки множества бирж (Binance, Bybit, OKX, Gate.io, HTX, Bitget) помимо текущей MEXC. Система выполняет параллельный опрос всех бирж через ThreadPoolExecutor, сохраняет полные снимки USDT-символов каждой биржи в единую SQLite БД с колонкой exchange, поддерживает настраиваемый retention policy и предоставляет фильтрацию по бирже в API и UI с возможностью сравнения спредов между биржами на одном графике.

## Glossary

- **History_Worker**: Фоновый поток, выполняющий периодический опрос бирж и запись снимков спредов в SQLite базу данных
- **SpreadSnapshot**: ORM-модель строки снимка спреда в таблице spread_snapshots
- **Exchange_Client**: Существующий клиент для конкретной биржи (BinancePublicClient, BybitPublicClient, OkxPublicClient, GateioPublicClient, HtxPublicClient, BitgetPublicClient)
- **Retention_Policy**: Механизм автоматического удаления записей старше заданного количества дней
- **Snapshot_Cycle**: Один цикл опроса всех активных бирж и записи результатов в БД
- **USDT_Symbol**: Торговая пара с котировкой в USDT (например BTCUSDT, ETHUSDT)
- **Spread_BPS**: Спред в базисных пунктах (basis points), рассчитанный как 10000 × (ask − bid) / mid
- **API_Server**: FastAPI backend, обслуживающий REST-запросы от фронтенда
- **Comparison_Chart**: UI-компонент для отображения спредов нескольких бирж на одном графике

## Requirements

### Requirement 1: Расширение схемы БД колонкой exchange

**User Story:** As a developer, I want the spread_snapshots table to include an exchange column, so that snapshots from different exchanges can be stored and queried independently.

#### Acceptance Criteria

1. THE SpreadSnapshot SHALL include a non-nullable column "exchange" of type String(32) with default value "mexc"
2. WHEN the History_Worker creates a new SpreadSnapshot record, THE SpreadSnapshot SHALL store the identifier of the source exchange in the "exchange" column
3. THE SpreadSnapshot SHALL have a composite index on columns (exchange, market, symbol, observed_at) for efficient filtered queries
4. WHEN the database schema is migrated, THE History_Worker SHALL preserve all existing records by setting their "exchange" column to "mexc"

### Requirement 2: Параллельный опрос множества бирж

**User Story:** As a system operator, I want the history worker to poll multiple exchanges in parallel, so that snapshot cycles complete within a reasonable time window.

#### Acceptance Criteria

1. THE History_Worker SHALL poll Binance, Bybit, OKX, Gate.io, HTX, and Bitget exchanges in addition to MEXC during each Snapshot_Cycle
2. THE History_Worker SHALL execute exchange polling concurrently using ThreadPoolExecutor with a configurable max_workers parameter
3. IF an Exchange_Client raises an exception during polling, THEN THE History_Worker SHALL log the error and continue polling remaining exchanges without interruption
4. WHEN a Snapshot_Cycle completes, THE History_Worker SHALL record the total number of rows stored and the elapsed time in debug-level logs
5. THE History_Worker SHALL collect all USDT_Symbol tickers from each exchange in a single API call per exchange per market

### Requirement 3: Настраиваемый Retention Policy

**User Story:** As a system operator, I want to configure how long historical data is retained, so that the database does not grow unbounded.

#### Acceptance Criteria

1. THE History_Worker SHALL read a "retention_days" parameter from the history configuration section in external_apis.json
2. WHEN a Snapshot_Cycle completes, THE Retention_Policy SHALL delete all SpreadSnapshot records where observed_at is older than the configured retention_days
3. WHILE retention_days is set to 0, THE Retention_Policy SHALL skip deletion and retain all records indefinitely
4. THE Retention_Policy SHALL default to 30 days when the retention_days parameter is absent from configuration

### Requirement 4: Конфигурация списка бирж

**User Story:** As a system operator, I want to configure which exchanges are polled for history, so that I can enable or disable exchanges without code changes.

#### Acceptance Criteria

1. THE History_Worker SHALL read an "exchanges" list from the history configuration section in external_apis.json
2. WHILE an exchange name is absent from the "exchanges" list, THE History_Worker SHALL skip polling that exchange during Snapshot_Cycle
3. THE History_Worker SHALL validate each exchange name against the set of supported exchanges (mexc, binance, bybit, okx, gateio, htx, bitget) at startup
4. IF an unsupported exchange name is present in the configuration, THEN THE History_Worker SHALL log a warning and ignore the unsupported entry

### Requirement 5: Фильтрация по бирже в API

**User Story:** As a frontend developer, I want to filter history data by exchange via the API, so that the UI can display exchange-specific spread history.

#### Acceptance Criteria

1. THE API_Server SHALL accept an optional "exchange" query parameter on the history endpoint
2. WHEN the "exchange" parameter is provided, THE API_Server SHALL return only SpreadSnapshot records matching the specified exchange
3. WHEN the "exchange" parameter is omitted, THE API_Server SHALL return SpreadSnapshot records from all exchanges
4. THE API_Server SHALL provide a GET endpoint that returns the list of exchanges with available history data

### Requirement 6: Сравнение спредов между биржами

**User Story:** As a trader, I want to compare spreads for the same symbol across multiple exchanges on one chart, so that I can identify arbitrage opportunities.

#### Acceptance Criteria

1. THE API_Server SHALL accept a comma-separated "exchanges" parameter to query history for multiple exchanges simultaneously
2. WHEN multiple exchanges are requested, THE API_Server SHALL return SpreadSnapshot records grouped by exchange for the specified symbol and time range
3. THE Comparison_Chart SHALL display spread_bps time series for each requested exchange as separate lines on a single chart
4. THE Comparison_Chart SHALL visually distinguish each exchange line using unique colors and a legend

### Requirement 7: Интервал опроса

**User Story:** As a system operator, I want to configure the polling interval for multi-exchange history, so that I can balance data granularity against API rate limits.

#### Acceptance Criteria

1. THE History_Worker SHALL read an "interval_sec" parameter from the history configuration section
2. THE History_Worker SHALL wait the configured interval_sec between consecutive Snapshot_Cycles
3. WHILE interval_sec is less than 10, THE History_Worker SHALL enforce a minimum interval of 10 seconds to avoid API rate limiting
4. THE History_Worker SHALL default to 60 seconds when the interval_sec parameter is absent from configuration
