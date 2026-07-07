# Requirements Document

## Introduction

Расширение MEXC Spread Monitor восемью новыми биржами: Binance, Bybit, OKX, Gate.io, HTX (Huobi), Bitget, dYdX и Hyperliquid. Каждая биржа интегрируется по тому же паттерну, что и существующие AsterDEX/Lighter: Python-клиент с httpx, нормализация в BookTickerRow, конфигурация в external_apis.json, поддержка в /api/snapshot и /api/klines/batch, отображение в ExchangeSwitcher UI. Все биржи используют публичные API для чтения рыночных данных (тикеры, стаканы) без аутентификации.

## Glossary

- **Exchange_Client**: Python-модуль (mexc_monitor/{exchange_name}/client.py), реализующий HTTP-клиент для публичного API конкретной биржи с использованием httpx.
- **BookTickerRow**: Унифицированный dataclass (symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote, funding_rate, observed_at), в который нормализуются данные всех бирж.
- **Exchange_Switcher**: React-компонент с кнопками-табами для переключения активной биржи в UI.
- **Snapshot_Endpoint**: Бэкенд-эндпоинт /api/snapshot?exchange=X, возвращающий данные тикеров выбранной биржи в унифицированном формате.
- **Klines_Batch_Endpoint**: Бэкенд-эндпоинт /api/klines/batch?exchange=X, возвращающий свечные данные для нескольких символов параллельно.
- **Exchange_Config**: Секция в config/external_apis.json, содержащая base_url, timeout_sec и endpoints для конкретной биржи.
- **CEX**: Централизованная биржа (Binance, Bybit, OKX, Gate.io, HTX, Bitget).
- **DEX**: Децентрализованная биржа (dYdX, Hyperliquid).
- **Normalization_Function**: Функция вида {exchange}_snapshot_rows(), преобразующая сырые данные API биржи в список BookTickerRow.

## Requirements

### Requirement 1: Клиент биржи Binance

**User Story:** As a трейдер, I want видеть спреды Binance (spot и futures) в приложении, so that я могу сравнивать ликвидность крупнейшей биржи с другими площадками.

#### Acceptance Criteria

1. THE Exchange_Client SHALL подключаться к публичному REST API Binance для получения данных book ticker (spot: GET /api/v3/ticker/bookTicker, futures: GET /fapi/v1/ticker/bookTicker)
2. THE Exchange_Client SHALL возвращать данные в формате BookTickerRow с заполненными полями: symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote
3. WHEN запрашиваются данные spot-рынка, THE Exchange_Client SHALL использовать base_url "https://api.binance.com"
4. WHEN запрашиваются данные futures-рынка, THE Exchange_Client SHALL использовать base_url "https://fapi.binance.com"
5. THE Exchange_Client SHALL использовать настройки подключения (base_url, timeout_sec, endpoints) из секции "binance" файла config/external_apis.json
6. IF API Binance недоступен или возвращает ошибку, THEN THE Exchange_Client SHALL выбросить исключение с описательным сообщением, содержащим имя биржи и HTTP-статус ответа

### Requirement 2: Клиент биржи Bybit

**User Story:** As a трейдер, I want видеть спреды Bybit (perpetual futures) в приложении, so that я могу оценивать ликвидность на этой площадке.

#### Acceptance Criteria

1. THE Exchange_Client SHALL подключаться к публичному REST API Bybit для получения данных тикеров (GET /v5/market/tickers?category=linear)
2. THE Exchange_Client SHALL возвращать данные в формате BookTickerRow с заполненными полями: symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote, funding_rate
3. THE Exchange_Client SHALL использовать настройки подключения из секции "bybit" файла config/external_apis.json
4. IF API Bybit недоступен или возвращает ошибку, THEN THE Exchange_Client SHALL выбросить исключение с описательным сообщением, содержащим имя биржи и код ошибки

### Requirement 3: Клиент биржи OKX

**User Story:** As a трейдер, I want видеть спреды OKX (spot и derivatives) в приложении, so that я могу сравнивать условия торговли на OKX с другими биржами.

#### Acceptance Criteria

1. THE Exchange_Client SHALL подключаться к публичному REST API OKX для получения данных тикеров (GET /api/v5/market/tickers?instType=SPOT и GET /api/v5/market/tickers?instType=SWAP)
2. THE Exchange_Client SHALL возвращать данные в формате BookTickerRow с заполненными полями: symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote
3. THE Exchange_Client SHALL нормализовать символы OKX (формат "BTC-USDT" или "BTC-USDT-SWAP") в формат "BTCUSDT" для совместимости с UI
4. THE Exchange_Client SHALL использовать настройки подключения из секции "okx" файла config/external_apis.json
5. IF API OKX недоступен или возвращает ошибку, THEN THE Exchange_Client SHALL выбросить исключение с описательным сообщением, содержащим имя биржи и код ошибки

### Requirement 4: Клиент биржи Gate.io

**User Story:** As a трейдер, I want видеть спреды Gate.io (spot и futures) в приложении, so that я могу оценивать ликвидность на этой площадке.

#### Acceptance Criteria

1. THE Exchange_Client SHALL подключаться к публичному REST API Gate.io для получения данных тикеров (spot: GET /api/v4/spot/tickers, futures: GET /api/v4/futures/usdt/tickers)
2. THE Exchange_Client SHALL возвращать данные в формате BookTickerRow с заполненными полями: symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote
3. THE Exchange_Client SHALL нормализовать символы Gate.io (формат "BTC_USDT") в формат "BTCUSDT" для совместимости с UI
4. THE Exchange_Client SHALL использовать настройки подключения из секции "gateio" файла config/external_apis.json
5. IF API Gate.io недоступен или возвращает ошибку, THEN THE Exchange_Client SHALL выбросить исключение с описательным сообщением, содержащим имя биржи и HTTP-статус ответа

### Requirement 5: Клиент биржи HTX (Huobi)

**User Story:** As a трейдер, I want видеть спреды HTX (spot и futures) в приложении, so that я могу сравнивать условия торговли на HTX с другими площадками.

#### Acceptance Criteria

1. THE Exchange_Client SHALL подключаться к публичному REST API HTX для получения данных тикеров (spot: GET /market/tickers, futures: GET /linear-swap-ex/market/detail/batch_merged)
2. THE Exchange_Client SHALL возвращать данные в формате BookTickerRow с заполненными полями: symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote
3. THE Exchange_Client SHALL нормализовать символы HTX (формат "btcusdt" в нижнем регистре) в формат "BTCUSDT" для совместимости с UI
4. THE Exchange_Client SHALL использовать настройки подключения из секции "htx" файла config/external_apis.json
5. IF API HTX недоступен или возвращает ошибку, THEN THE Exchange_Client SHALL выбросить исключение с описательным сообщением, содержащим имя биржи и код ошибки

### Requirement 6: Клиент биржи Bitget

**User Story:** As a трейдер, I want видеть спреды Bitget (futures) в приложении, so that я могу оценивать ликвидность на этой площадке.

#### Acceptance Criteria

1. THE Exchange_Client SHALL подключаться к публичному REST API Bitget для получения данных тикеров (GET /api/v2/mix/market/tickers?productType=USDT-FUTURES)
2. THE Exchange_Client SHALL возвращать данные в формате BookTickerRow с заполненными полями: symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote, funding_rate
3. THE Exchange_Client SHALL нормализовать символы Bitget (формат "BTCUSDT" с суффиксом контракта) в формат "BTCUSDT" для совместимости с UI
4. THE Exchange_Client SHALL использовать настройки подключения из секции "bitget" файла config/external_apis.json
5. IF API Bitget недоступен или возвращает ошибку, THEN THE Exchange_Client SHALL выбросить исключение с описательным сообщением, содержащим имя биржи и код ошибки

### Requirement 7: Клиент биржи dYdX

**User Story:** As a трейдер, I want видеть спреды dYdX (DEX perpetual futures) в приложении, so that я могу сравнивать децентрализованную площадку с CEX.

#### Acceptance Criteria

1. THE Exchange_Client SHALL подключаться к публичному REST API dYdX v4 для получения данных тикеров (GET /v4/perpetualMarkets и GET /v4/orderbooks/{market})
2. THE Exchange_Client SHALL возвращать данные в формате BookTickerRow с заполненными полями: symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote
3. THE Exchange_Client SHALL нормализовать символы dYdX (формат "BTC-USD") в формат "BTCUSD" для совместимости с UI
4. THE Exchange_Client SHALL использовать настройки подключения из секции "dydx" файла config/external_apis.json
5. IF API dYdX недоступен или возвращает ошибку, THEN THE Exchange_Client SHALL выбросить исключение с описательным сообщением, содержащим имя биржи и код ошибки

### Requirement 8: Клиент биржи Hyperliquid

**User Story:** As a трейдер, I want видеть спреды Hyperliquid (DEX perps на L1) в приложении, so that я могу сравнивать эту DEX-площадку с другими биржами.

#### Acceptance Criteria

1. THE Exchange_Client SHALL подключаться к публичному REST API Hyperliquid для получения данных тикеров (POST /info с body {"type": "allMids"} и POST /info с body {"type": "metaAndAssetCtxs"})
2. THE Exchange_Client SHALL возвращать данные в формате BookTickerRow с заполненными полями: symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps, volume_24h_base, volume_24h_quote, funding_rate
3. THE Exchange_Client SHALL нормализовать символы Hyperliquid (формат "BTC") в формат "BTCUSDT" для совместимости с UI
4. THE Exchange_Client SHALL использовать настройки подключения из секции "hyperliquid" файла config/external_apis.json
5. IF API Hyperliquid недоступен или возвращает ошибку, THEN THE Exchange_Client SHALL выбросить исключение с описательным сообщением, содержащим имя биржи и код ошибки

### Requirement 9: Конфигурация новых бирж

**User Story:** As a разработчик, I want чтобы параметры подключения ко всем новым биржам хранились в config/external_apis.json, so that их можно менять без изменения кода.

#### Acceptance Criteria

1. THE Exchange_Config SHALL содержать секции для каждой новой биржи: "binance", "bybit", "okx", "gateio", "htx", "bitget", "dydx", "hyperliquid"
2. THE Exchange_Config SHALL включать для каждой биржи поля: base_url, timeout_sec, endpoints (словарь путей API)
3. WHEN биржа поддерживает spot и futures, THE Exchange_Config SHALL содержать отдельные base_url для каждого рынка (spot_base_url, futures_base_url)
4. IF секция биржи отсутствует в конфигурации, THEN THE Exchange_Client SHALL использовать значения по умолчанию, зашитые в коде клиента

### Requirement 10: Интеграция с эндпоинтом /api/snapshot

**User Story:** As a фронтенд-разработчик, I want получать данные любой из 11 бирж через единый эндпоинт /api/snapshot?exchange=X, so that переключение бирж на клиенте требует минимальных изменений.

#### Acceptance Criteria

1. THE Snapshot_Endpoint SHALL принимать параметр exchange со значениями: "mexc", "asterdex", "lighter", "binance", "bybit", "okx", "gateio", "htx", "bitget", "dydx", "hyperliquid"
2. WHEN запрос содержит валидное значение exchange, THE Snapshot_Endpoint SHALL вернуть данные тикеров выбранной биржи в формате BookTickerRow
3. IF указана неизвестная биржа, THEN THE Snapshot_Endpoint SHALL вернуть HTTP 400 с полным списком поддерживаемых бирж
4. WHEN биржа поддерживает несколько рынков (spot, futures), THE Snapshot_Endpoint SHALL учитывать параметр market для выбора рынка
5. THE Snapshot_Endpoint SHALL кэшировать ответы каждой биржи с тем же TTL, что используется для существующих бирж

### Requirement 11: Интеграция с эндпоинтом /api/klines/batch

**User Story:** As a фронтенд-разработчик, I want получать свечные данные для графиков с любой из 11 бирж через /api/klines/batch?exchange=X, so that графики работают для всех бирж.

#### Acceptance Criteria

1. THE Klines_Batch_Endpoint SHALL принимать параметр exchange со значениями всех 11 поддерживаемых бирж
2. WHEN запрос содержит валидное значение exchange, THE Klines_Batch_Endpoint SHALL вернуть свечные данные в унифицированном формате (time, open, high, low, close, volume)
3. THE Klines_Batch_Endpoint SHALL поддерживать интервалы 5m, 15m, 1h, 4h, 1d для всех бирж
4. THE Klines_Batch_Endpoint SHALL маппить стандартные интервалы в формат, специфичный для каждой биржи
5. IF биржа не поддерживает запрошенный интервал, THEN THE Klines_Batch_Endpoint SHALL вернуть пустой массив свечей для соответствующего символа

### Requirement 12: Расширение ExchangeSwitcher UI

**User Story:** As a трейдер, I want видеть все 11 бирж в переключателе, so that я могу быстро переключаться между любыми площадками.

#### Acceptance Criteria

1. THE Exchange_Switcher SHALL отображать 11 вариантов выбора: "MEXC", "AsterDEX", "Lighter", "Binance", "Bybit", "OKX", "Gate.io", "HTX", "Bitget", "dYdX", "Hyperliquid"
2. THE Exchange_Switcher SHALL группировать биржи визуально: CEX (MEXC, Binance, Bybit, OKX, Gate.io, HTX, Bitget) и DEX (AsterDEX, Lighter, dYdX, Hyperliquid)
3. WHEN пользователь выбирает биржу с несколькими рынками (Binance, OKX, Gate.io, HTX), THE Exchange_Switcher SHALL отображать переключатель рынков (spot/futures)
4. WHEN пользователь выбирает биржу с единственным рынком (Bybit, Bitget, AsterDEX, Lighter, dYdX, Hyperliquid), THE Exchange_Switcher SHALL скрывать переключатель рынков
5. THE Exchange_Switcher SHALL адаптировать layout для отображения 11 бирж без горизонтального переполнения (компактные табы или выпадающий список при нехватке места)

### Requirement 13: Нормализация данных в BookTickerRow

**User Story:** As a разработчик, I want чтобы все биржи возвращали данные в едином формате BookTickerRow, so that фронтенд работает одинаково для любой биржи.

#### Acceptance Criteria

1. THE Normalization_Function SHALL вычислять mid как (bid + ask) / 2 для всех бирж
2. THE Normalization_Function SHALL вычислять spread_abs как ask - bid для всех бирж
3. WHEN mid больше нуля, THE Normalization_Function SHALL вычислять spread_bps как 10000 * spread_abs / mid
4. WHEN mid равен нулю или отрицателен, THE Normalization_Function SHALL устанавливать spread_bps в None
5. THE Normalization_Function SHALL устанавливать observed_at в текущее время UTC в формате ISO8601 при каждом вызове
6. FOR ALL валидных данных тикера от любой биржи, нормализация в BookTickerRow и обратная сериализация в JSON SHALL сохранять все числовые значения с точностью до 8 знаков после запятой (round-trip свойство)

### Requirement 14: Обработка ошибок для новых бирж

**User Story:** As a трейдер, I want видеть понятные сообщения об ошибках при недоступности биржи, so that я понимаю причину отсутствия данных.

#### Acceptance Criteria

1. IF Exchange_Client выбрасывает исключение, THEN THE Snapshot_Endpoint SHALL вернуть JSON с ok=false, error содержащим имя биржи и описание ошибки, и пустым массивом rows
2. IF Exchange_Client получает HTTP-ответ с кодом 429 (rate limit), THEN THE Exchange_Client SHALL включить в сообщение об ошибке информацию о rate limiting
3. IF Exchange_Client получает таймаут при запросе, THEN THE Exchange_Client SHALL включить в сообщение об ошибке значение timeout и имя биржи
4. THE Snapshot_Endpoint SHALL возвращать HTTP 200 с ok=false при ошибках отдельных бирж (аналогично существующему поведению для AsterDEX и Lighter)
