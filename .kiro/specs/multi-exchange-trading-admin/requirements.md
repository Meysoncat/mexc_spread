# Requirements Document

## Introduction

Расширение Trading Admin для поддержки мульти-биржевой торговли. Текущая система поддерживает только MEXC (один TradingEngine, один MexcPrivateClient). Данная фича добавляет возможность параллельной работы нескольких торговых движков на разных биржах (Binance, Bybit, OKX, Gate.io, HTX, Bitget) с единым UI-управлением, выбором биржи, рынка (spot/futures), типа ордера и стороны. Архитектура spot-first с futures-ready расширяемостью. Обратная совместимость с текущим MEXC-only режимом сохраняется.

## Glossary

- **Trading_Admin**: React TypeScript UI-компонент для управления торговыми движками
- **Trading_Engine**: Python-класс, реализующий торговый цикл (чтение сигнала, проверка риска, размещение ордера)
- **Engine_Registry**: Реестр активных экземпляров Trading_Engine, индексированный по exchange+market
- **Private_Client**: Класс для подписанных приватных HTTP-запросов к API конкретной биржи
- **Exchange**: Одна из поддерживаемых бирж: MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget
- **Market**: Тип рынка — spot или futures
- **Order_Side**: Сторона ордера — BUY или SELL
- **Order_Type**: Тип ордера — LIMIT или MARKET
- **Backend_API**: FastAPI Python сервер, предоставляющий REST endpoints для управления торговлей
- **Env_Convention**: Единая конвенция именования переменных окружения: {EXCHANGE}_API_KEY, {EXCHANGE}_API_SECRET

## Requirements

### Requirement 1: Multi-Exchange Engine Registry

**User Story:** As a trader, I want to run multiple trading engines on different exchanges simultaneously, so that I can execute strategies across Binance, Bybit, OKX, Gate.io, HTX, Bitget in addition to MEXC.

#### Acceptance Criteria

1. THE Engine_Registry SHALL maintain a collection of Trading_Engine instances indexed by a composite key of exchange name and market type.
2. WHEN a new engine is requested for a specific exchange and market combination, THE Engine_Registry SHALL create and return a new Trading_Engine instance configured for that exchange.
3. WHEN an engine already exists for a given exchange and market combination, THE Engine_Registry SHALL return the existing instance without creating a duplicate.
4. THE Engine_Registry SHALL support concurrent operation of Trading_Engine instances on different exchanges without shared mutable state between engines.
5. WHEN the Backend_API shuts down, THE Engine_Registry SHALL stop all running Trading_Engine instances gracefully.

### Requirement 2: Exchange-Specific Private Clients

**User Story:** As a trader, I want each exchange to have its own authenticated client with proper request signing, so that I can place orders on any supported exchange.

#### Acceptance Criteria

1. THE Backend_API SHALL provide a Private_Client implementation for each supported Exchange (MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget).
2. WHEN a Private_Client is instantiated for an Exchange, THE Private_Client SHALL use the corresponding {EXCHANGE}_API_KEY and {EXCHANGE}_API_SECRET environment variables for authentication.
3. THE Private_Client for each Exchange SHALL implement request signing according to that exchange's API specification (HMAC SHA256 for Binance/MEXC/Bitget, HMAC SHA256 with specific headers for Bybit/OKX/Gate.io/HTX).
4. WHEN {EXCHANGE}_API_KEY or {EXCHANGE}_API_SECRET is missing or empty for a given Exchange, THE Backend_API SHALL report that Exchange as unavailable for live trading without raising an error on startup.
5. THE Private_Client for each Exchange SHALL support placing LIMIT and MARKET orders with BUY and SELL sides on the spot market.

### Requirement 3: Unified Environment Variable Convention

**User Story:** As a system administrator, I want a consistent naming convention for exchange credentials, so that configuration is predictable and manageable.

#### Acceptance Criteria

1. THE Backend_API SHALL read API credentials using the pattern {EXCHANGE}_API_KEY and {EXCHANGE}_API_SECRET where {EXCHANGE} is the uppercase exchange name (MEXC, BINANCE, BYBIT, OKX, GATEIO, HTX, BITGET).
2. THE Backend_API SHALL continue to support the existing MEXC_API_KEY and MEXC_API_SECRET variables without requiring migration.
3. WHEN both legacy MEXC_API_KEY and the new convention MEXC_API_KEY are the same variable, THE Backend_API SHALL use the single MEXC_API_KEY value for MEXC exchange authentication.
4. THE Backend_API SHALL read optional {EXCHANGE}_RECV_WINDOW_MS for exchanges that support receive window configuration.

### Requirement 4: Trading Engine Configuration Per Exchange

**User Story:** As a trader, I want to configure trading parameters independently for each exchange engine, so that I can tune strategies per exchange.

#### Acceptance Criteria

1. WHEN a Trading_Engine is created for a specific Exchange, THE Trading_Engine SHALL load default settings from environment variables prefixed with {EXCHANGE}_TRADING_ (e.g., BINANCE_TRADING_SYMBOL, BYBIT_TRADING_MODE).
2. IF {EXCHANGE}_TRADING_ prefixed variables are not set, THEN THE Trading_Engine SHALL fall back to the generic MEXC_TRADING_ prefixed defaults for backward compatibility.
3. THE Trading_Engine SHALL support runtime configuration updates per instance through the Backend_API without affecting other running engines.
4. EACH Trading_Engine instance SHALL maintain independent state: kill_switch, order counters, error counters, and event log.

### Requirement 5: Backend API Multi-Engine Endpoints

**User Story:** As a frontend developer, I want REST API endpoints that support multi-exchange engine management, so that the Trading_Admin UI can control engines on any exchange.

#### Acceptance Criteria

1. THE Backend_API SHALL expose GET /api/trading/engines endpoint returning a list of all registered engine instances with their exchange, market, and running status.
2. THE Backend_API SHALL expose GET /api/trading/status?exchange={exchange}&market={market} endpoint returning the status of a specific engine instance.
3. THE Backend_API SHALL expose POST /api/trading/start?exchange={exchange}&market={market} endpoint to start a specific engine instance.
4. THE Backend_API SHALL expose POST /api/trading/stop?exchange={exchange}&market={market} endpoint to stop a specific engine instance.
5. THE Backend_API SHALL expose POST /api/trading/kill-switch?exchange={exchange}&market={market}&enabled={bool} endpoint to toggle the kill switch of a specific engine.
6. THE Backend_API SHALL expose POST /api/trading/run-once?exchange={exchange}&market={market} endpoint to execute a single step of a specific engine.
7. THE Backend_API SHALL expose PATCH /api/trading/settings?exchange={exchange}&market={market} endpoint to update runtime settings of a specific engine.
8. WHEN the exchange parameter is omitted from any endpoint, THE Backend_API SHALL default to "mexc" and market to "spot" for backward compatibility.

### Requirement 6: Order Type and Side Selection

**User Story:** As a trader, I want to choose between LIMIT and MARKET orders and between BUY and SELL sides, so that I can execute different trading strategies.

#### Acceptance Criteria

1. THE Trading_Engine SHALL support Order_Type values of LIMIT and MARKET for order placement.
2. THE Trading_Engine SHALL support Order_Side values of BUY and SELL for order placement.
3. WHEN Order_Type is MARKET, THE Trading_Engine SHALL omit the price parameter from the order request.
4. WHEN Order_Type is LIMIT, THE Trading_Engine SHALL calculate the order price based on the current bid/ask and the configured limit_price_offset_bps.
5. THE Trading_Engine SHALL include order_type and order_side in the runtime settings that can be updated via the Backend_API.

### Requirement 7: Market Selection (Spot/Futures)

**User Story:** As a trader, I want to select between spot and futures markets for each engine, so that I can trade on the appropriate market per exchange.

#### Acceptance Criteria

1. THE Trading_Engine SHALL accept a market parameter with values "spot" or "futures" at creation time.
2. WHEN market is "spot", THE Private_Client SHALL use the spot API endpoints of the corresponding Exchange.
3. WHEN market is "futures", THE Private_Client SHALL use the futures API endpoints of the corresponding Exchange.
4. THE Trading_Admin UI SHALL display the market selection (spot/futures) for each engine instance.
5. WHILE market is "futures", THE Trading_Engine SHALL use futures-specific symbol conventions for the selected Exchange (e.g., linear perpetual contract symbols).

### Requirement 8: Frontend Trading Admin Multi-Exchange UI

**User Story:** As a trader, I want the Trading Admin UI to support selecting and managing engines across multiple exchanges, so that I can monitor and control all trading activity from one interface.

#### Acceptance Criteria

1. THE Trading_Admin SHALL display an exchange selector allowing the user to choose from available exchanges (MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget).
2. THE Trading_Admin SHALL display a market selector (spot/futures) for the selected exchange.
3. THE Trading_Admin SHALL display Order_Type selector (LIMIT/MARKET) in the trading configuration form.
4. THE Trading_Admin SHALL display Order_Side selector (BUY/SELL) in the trading configuration form.
5. WHEN the user selects a different exchange or market, THE Trading_Admin SHALL load and display the status and settings of the corresponding engine instance.
6. THE Trading_Admin SHALL indicate which exchanges have valid API credentials configured (available for live trading) versus those without credentials (paper-only).
7. THE Trading_Admin SHALL display the running/stopped state of each engine independently.

### Requirement 9: Backward Compatibility

**User Story:** As an existing user, I want the system to work exactly as before when I don't configure additional exchanges, so that my current MEXC-only setup continues to function without changes.

#### Acceptance Criteria

1. WHEN no additional exchange credentials are configured, THE Backend_API SHALL operate with a single MEXC engine instance identical to the current behavior.
2. THE Backend_API SHALL maintain all existing endpoint signatures (GET /api/trading/status, POST /api/trading/start, POST /api/trading/stop, POST /api/trading/kill-switch, POST /api/trading/run-once) with their current behavior when called without exchange/market parameters.
3. THE Trading_Admin SHALL default to showing the MEXC spot engine when opened, matching the current user experience.
4. WHEN MEXC_TRADING_ENABLED=true is set, THE Backend_API SHALL auto-start the MEXC engine on startup as it does currently.

### Requirement 10: Exchange Availability Detection

**User Story:** As a trader, I want to see which exchanges are available for trading based on configured credentials, so that I know where I can trade.

#### Acceptance Criteria

1. THE Backend_API SHALL expose GET /api/trading/exchanges endpoint returning a list of all supported exchanges with their availability status.
2. WHEN an Exchange has valid API credentials configured, THE Backend_API SHALL report that Exchange as available with supported markets (spot, futures).
3. WHEN an Exchange lacks API credentials, THE Backend_API SHALL report that Exchange as available for paper mode only.
4. THE Backend_API SHALL include the exchange base URLs and supported order types in the exchange availability response.
