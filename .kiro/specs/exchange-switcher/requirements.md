# Requirements Document

## Introduction

Функция переключения бирж в основном окне приложения MEXC Spread Monitor. Позволяет пользователю переключаться между биржами MEXC (spot), AsterDEX и Lighter прямо в главной таблице данных, вместо использования отдельных панелей. Также включает интеграцию новой биржи Lighter (DEX перпетуальных фьючерсов).

## Glossary

- **Exchange_Switcher**: UI-компонент (группа кнопок/табов) в основном окне, позволяющий выбрать активную биржу для отображения данных в главной таблице.
- **Main_Table**: Основная таблица данных в React SPA (`App.tsx`), отображающая тикеры с bid/ask, спредами и метриками.
- **Active_Exchange**: Текущая выбранная биржа, данные которой отображаются в Main_Table.
- **Lighter**: DEX-биржа перпетуальных фьючерсов (аналогична AsterDEX), подлежащая интеграции.
- **Lighter_Client**: Python-клиент для публичного API биржи Lighter.
- **Exchange_Data_Provider**: Бэкенд-компонент, возвращающий данные тикеров в унифицированном формате для выбранной биржи.
- **Unified_Ticker_Row**: Единый формат строки данных (symbol, bid, ask, mid, spread_abs, spread_bps, volume и т.д.), используемый Main_Table независимо от источника биржи.

## Requirements

### Requirement 1: Отображение переключателя бирж

**User Story:** As a трейдер, I want видеть переключатель бирж в основном окне, so that я могу быстро переключаться между MEXC, AsterDEX и Lighter без перехода на отдельные панели.

#### Acceptance Criteria

1. THE Exchange_Switcher SHALL отображать три варианта выбора: "MEXC", "AsterDEX", "Lighter"
2. THE Exchange_Switcher SHALL располагаться в верхней части основного окна рядом с существующими элементами управления
3. WHEN пользователь открывает приложение, THE Exchange_Switcher SHALL отображать "MEXC" как Active_Exchange по умолчанию
4. THE Exchange_Switcher SHALL визуально выделять текущую Active_Exchange (активная кнопка/таб)

### Requirement 2: Переключение данных в таблице

**User Story:** As a трейдер, I want чтобы при переключении биржи таблица показывала данные выбранной биржи, so that я могу анализировать спреды на разных площадках.

#### Acceptance Criteria

1. WHEN пользователь выбирает биржу в Exchange_Switcher, THE Main_Table SHALL отобразить данные тикеров выбранной биржи
2. WHILE данные загружаются после переключения биржи, THE Main_Table SHALL отображать индикатор загрузки
3. WHEN переключение биржи завершено, THE Main_Table SHALL отображать данные в формате Unified_Ticker_Row независимо от выбранной биржи
4. WHEN пользователь переключает биржу, THE Main_Table SHALL очистить предыдущие данные перед отображением новых
5. IF загрузка данных выбранной биржи завершается ошибкой, THEN THE Main_Table SHALL отобразить сообщение об ошибке с указанием имени биржи

### Requirement 3: Сохранение состояния фильтров

**User Story:** As a трейдер, I want чтобы фильтры и настройки сортировки сохранялись при переключении бирж, so that мне не нужно перенастраивать интерфейс каждый раз.

#### Acceptance Criteria

1. WHEN пользователь переключает Active_Exchange, THE Main_Table SHALL сохранить текущие настройки текстового поиска
2. WHEN пользователь переключает Active_Exchange, THE Main_Table SHALL сохранить текущий порядок сортировки
3. WHEN пользователь переключает Active_Exchange, THE Main_Table SHALL применить сохранённые фильтры к новым данным

### Requirement 4: Бэкенд-эндпоинт для данных бирж

**User Story:** As a фронтенд-разработчик, I want единый API-эндпоинт для получения данных любой биржи, so that переключение бирж на клиенте требует минимальных изменений.

#### Acceptance Criteria

1. THE Exchange_Data_Provider SHALL предоставлять эндпоинт, принимающий параметр выбора биржи ("mexc", "asterdex", "lighter")
2. WHEN запрос содержит параметр exchange="mexc", THE Exchange_Data_Provider SHALL вернуть данные спот-тикеров MEXC в формате Unified_Ticker_Row
3. WHEN запрос содержит параметр exchange="asterdex", THE Exchange_Data_Provider SHALL вернуть данные тикеров AsterDEX в формате Unified_Ticker_Row
4. WHEN запрос содержит параметр exchange="lighter", THE Exchange_Data_Provider SHALL вернуть данные тикеров Lighter в формате Unified_Ticker_Row
5. IF указана неизвестная биржа, THEN THE Exchange_Data_Provider SHALL вернуть ошибку с HTTP-статусом 400 и списком поддерживаемых бирж

### Requirement 5: Интеграция биржи Lighter

**User Story:** As a трейдер, I want видеть данные биржи Lighter в приложении, so that я могу сравнивать спреды на этой площадке с другими.

#### Acceptance Criteria

1. THE Lighter_Client SHALL подключаться к публичному API биржи Lighter для получения данных тикеров
2. THE Lighter_Client SHALL возвращать данные в формате, совместимом с Unified_Ticker_Row (symbol, bid, ask, bid_qty, ask_qty, mid, spread_abs, spread_bps)
3. IF API Lighter недоступен, THEN THE Lighter_Client SHALL выбросить исключение с описательным сообщением об ошибке
4. THE Lighter_Client SHALL использовать настройки подключения (base_url, timeout) из конфигурационного файла config/external_apis.json

### Requirement 6: Конфигурация биржи Lighter

**User Story:** As a разработчик, I want чтобы параметры подключения к Lighter хранились в конфигурации, so that их можно менять без изменения кода.

#### Acceptance Criteria

1. THE Exchange_Data_Provider SHALL читать конфигурацию Lighter из секции "lighter" в файле config/external_apis.json
2. THE Exchange_Data_Provider SHALL поддерживать настройки: base_url, timeout_sec, endpoints (book_ticker, ticker_24hr)
3. IF секция "lighter" отсутствует в конфигурации, THEN THE Exchange_Data_Provider SHALL использовать значения по умолчанию для base_url и timeout_sec

### Requirement 7: Автообновление данных для активной биржи

**User Story:** As a трейдер, I want чтобы данные автоматически обновлялись для выбранной биржи, so that я вижу актуальную информацию без ручного обновления.

#### Acceptance Criteria

1. WHILE автообновление включено, THE Main_Table SHALL периодически запрашивать данные только для Active_Exchange
2. WHEN пользователь переключает Active_Exchange при включённом автообновлении, THE Main_Table SHALL немедленно запросить данные новой биржи и перезапустить цикл автообновления
3. THE Main_Table SHALL использовать тот же интервал автообновления для всех бирж
