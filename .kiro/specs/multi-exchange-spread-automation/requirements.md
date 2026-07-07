# Requirements Document

## Introduction

Расширение MEXC Spread Monitor до мультибиржевой платформы автоматизации спредовой торговли. Включает: Telegram-алерты, WebSocket-подключение к AsterDEX, расширение Spread Capture Engine на AsterDEX, автоматический межбиржевой арбитраж (MEXC ↔ AsterDEX) и персистентную историю кросс-спреда.

## Glossary

- **Alert_Service**: Сервис отправки уведомлений в Telegram через Bot API
- **AsterDEX_WS**: WebSocket-клиент для получения real-time данных с AsterDEX
- **Spread_Capture_Engine**: Движок автоматического сбора спреда bid/ask на одном инструменте
- **Arbitrage_Engine**: Движок межбиржевого арбитража, управляющий позициями на MEXC и AsterDEX одновременно
- **Cross_Spread_Store**: Модуль персистентного хранения истории межбиржевого спреда в SQLite
- **Spread_Buffer**: In-memory ring buffer для высокочастотных данных спреда
- **Kill_Switch**: Механизм аварийной остановки всех торговых операций
- **Basis_BPS**: Разница цен между биржами в базисных пунктах (10 000 × разница / цена)
- **Paper_Mode**: Режим симуляции торговли без реальных ордеров
- **Live_Mode**: Режим реальной торговли с размещением ордеров на бирже

## Requirements

### Requirement 1: Telegram Alert Service

**User Story:** As a трейдер, I want получать уведомления в Telegram о значимых событиях, so that я могу оперативно реагировать на торговые возможности без постоянного наблюдения за UI.

#### Acceptance Criteria

1. WHEN спред на мониторируемом символе достигает или превышает настроенный порог (в bps), THE Alert_Service SHALL отправить сообщение в указанный Telegram-чат с символом, текущим спредом и порогом
2. WHEN межбиржевая арбитражная возможность обнаружена (basis_bps превышает порог), THE Alert_Service SHALL отправить сообщение с символом, ценами на обеих биржах и размером базиса
3. WHEN позиция открыта или закрыта в Spread_Capture_Engine, THE Alert_Service SHALL отправить сообщение с деталями сделки (символ, цена входа/выхода, PNL)
4. THE Alert_Service SHALL хранить конфигурацию (bot_token, chat_id, включённые типы алертов, пороги) в JSON-файле конфигурации
5. IF Telegram Bot API возвращает ошибку, THEN THE Alert_Service SHALL записать ошибку в лог и повторить отправку до 3 раз с экспоненциальным back-off
6. WHEN пользователь изменяет настройки алертов через API, THE Alert_Service SHALL применить новые настройки без перезапуска сервера
7. THE Alert_Service SHALL ограничивать частоту отправки сообщений: не более 1 сообщения одного типа для одного символа в 60 секунд (rate limiting)

### Requirement 2: WebSocket-подключение к AsterDEX

**User Story:** As a трейдер, I want получать данные с AsterDEX в реальном времени через WebSocket, so that кросс-биржевые спреды обновляются мгновенно без задержек REST-поллинга.

#### Acceptance Criteria

1. WHEN пользователь подписывается на символ AsterDEX, THE AsterDEX_WS SHALL установить WebSocket-соединение и подписаться на поток bookTicker для указанного символа
2. WHEN AsterDEX_WS получает обновление bookTicker, THE AsterDEX_WS SHALL записать тик в Spread_Buffer с префиксом источника (aster:SYMBOL)
3. WHILE WebSocket-соединение с AsterDEX активно, THE AsterDEX_WS SHALL отправлять ping-фреймы каждые 30 секунд для поддержания соединения
4. IF WebSocket-соединение с AsterDEX разрывается, THEN THE AsterDEX_WS SHALL выполнить переподключение с экспоненциальным back-off (начиная с 1 секунды, максимум 60 секунд)
5. WHEN данные AsterDEX поступают в Spread_Buffer, THE SpreadChartModal SHALL отображать график спреда AsterDEX-символов в реальном времени через существующий SSE-механизм
6. WHEN обновления bookTicker поступают с обеих бирж (MEXC и AsterDEX) для одного актива, THE AsterDEX_WS SHALL вычислять кросс-биржевой спред и публиковать его в отдельный канал Spread_Buffer
7. THE AsterDEX_WS SHALL поддерживать одновременную подписку на не менее 20 символов в одном WebSocket-соединении

### Requirement 3: Spread Capture на AsterDEX

**User Story:** As a трейдер, I want использовать стратегию сбора спреда (buy bid / sell ask) на AsterDEX перпетуалах, so that я могу зарабатывать на спреде на нескольких биржах одновременно.

#### Acceptance Criteria

1. WHEN пользователь выбирает биржу "asterdex" в настройках Spread_Capture_Engine, THE Spread_Capture_Engine SHALL использовать AsterPrivateClient для размещения ордеров на AsterDEX
2. THE Spread_Capture_Engine SHALL поддерживать выбор биржи из трёх вариантов: MEXC spot, MEXC futures, AsterDEX perpetuals
3. WHILE Spread_Capture_Engine работает в режиме live на AsterDEX, THE Spread_Capture_Engine SHALL размещать лимитные ордера через AsterPrivateClient.place_limit_order с корректной подписью HMAC SHA256
4. WHEN позиция закрыта на любой из бирж, THE Spread_Capture_Engine SHALL записать результат в единый журнал сделок с указанием биржи
5. THE Spread_Capture_Engine SHALL вести раздельную статистику PNL по каждой бирже и агрегированную статистику по всем биржам
6. IF AsterDEX API возвращает ошибку при размещении ордера, THEN THE Spread_Capture_Engine SHALL отменить текущую операцию, записать ошибку в лог событий и перейти в состояние idle
7. WHEN пользователь переключает биржу во время активной позиции, THE Spread_Capture_Engine SHALL отклонить переключение и вернуть ошибку с описанием причины

### Requirement 4: Cross-Exchange Arbitrage Engine

**User Story:** As a трейдер, I want автоматически захватывать межбиржевой спред между MEXC и AsterDEX, so that я могу зарабатывать на ценовых расхождениях между биржами без ручного вмешательства.

#### Acceptance Criteria

1. WHILE Arbitrage_Engine активен, THE Arbitrage_Engine SHALL мониторить разницу цен между MEXC и AsterDEX для каждого настроенного символа с частотой обновления данных из WebSocket
2. WHEN исполняемый спред (executable spread) превышает настроенный порог, THE Arbitrage_Engine SHALL открыть позицию: купить на бирже с более низкой ценой и продать на бирже с более высокой ценой
3. THE Arbitrage_Engine SHALL рассчитывать исполняемый спред как: (bid_дорогой_биржи − ask_дешёвой_биржи) / mid − суммарные комиссии обеих бирж (в bps)
4. WHILE позиция арбитража открыта, THE Arbitrage_Engine SHALL мониторить спред и закрывать позицию когда спред сужается до порога выхода или по таймауту
5. THE Arbitrage_Engine SHALL ограничивать максимальный размер позиции на каждой бирже значением из конфигурации (max_position_notional_usdt)
6. THE Arbitrage_Engine SHALL ограничивать количество одновременных арбитражных сделок значением из конфигурации (max_concurrent_trades)
7. WHEN Kill_Switch активирован, THE Arbitrage_Engine SHALL немедленно прекратить открытие новых позиций и отменить все pending-ордера на обеих биржах
8. THE Arbitrage_Engine SHALL поддерживать Paper_Mode, в котором сделки симулируются без реальных ордеров, с записью результатов в журнал
9. THE Arbitrage_Engine SHALL вести журнал арбитражных сделок с полями: символ, биржа покупки, биржа продажи, цены входа/выхода на обеих ногах, объём, gross PNL, комиссии, net PNL, время удержания
10. IF ордер на одной из бирж не исполняется в течение max_pending_sec, THEN THE Arbitrage_Engine SHALL отменить ордер и не открывать вторую ногу (защита от одноногого риска)
11. THE Arbitrage_Engine SHALL предоставлять API-эндпоинты для: получения статуса, запуска, остановки, активации Kill_Switch, просмотра открытых позиций и истории сделок

### Requirement 5: Персистентная история кросс-спреда

**User Story:** As a трейдер, I want видеть историю межбиржевого спреда за длительный период, so that я могу выявлять паттерны и оптимальное время для арбитражных сделок.

#### Acceptance Criteria

1. WHILE Cross_Spread_Store активен, THE Cross_Spread_Store SHALL записывать снимки кросс-спреда (MEXC ↔ AsterDEX) в SQLite с настраиваемым интервалом (по умолчанию 60 секунд)
2. THE Cross_Spread_Store SHALL хранить для каждого снимка: символ, bid/ask/mid на обеих биржах, basis_abs, basis_bps, funding_rate, timestamp
3. WHEN клиент запрашивает GET /api/cross-spread/history, THE Cross_Spread_Store SHALL вернуть исторические данные с фильтрацией по символу, временному диапазону и лимиту записей
4. THE Cross_Spread_Store SHALL автоматически удалять записи старше настроенного периода хранения (по умолчанию 30 дней) при каждом цикле записи
5. WHEN фронтенд запрашивает данные для графика, THE Cross_Spread_Store SHALL поддерживать downsampling (агрегацию) для периодов более 24 часов, возвращая не более 2000 точек
6. THE Cross_Spread_Store SHALL использовать существующую инфраструктуру SQLAlchemy ORM (mexc_monitor/orm/) для определения модели и миграций

### Requirement 6: UI-конфигурация алертов

**User Story:** As a трейдер, I want настраивать параметры Telegram-алертов через веб-интерфейс, so that я могу управлять уведомлениями без редактирования конфигурационных файлов.

#### Acceptance Criteria

1. THE Frontend SHALL предоставить панель настроек алертов с полями: bot_token, chat_id, включение/выключение каждого типа алерта, пороги в bps
2. WHEN пользователь сохраняет настройки алертов в UI, THE Frontend SHALL отправить PATCH-запрос на /api/alerts/settings и отобразить результат (успех или ошибку)
3. WHEN панель настроек алертов открывается, THE Frontend SHALL загрузить текущие настройки через GET /api/alerts/settings и отобразить их в форме
4. THE Frontend SHALL маскировать поле bot_token при отображении (показывать только последние 4 символа) для защиты от случайного раскрытия
5. WHEN пользователь нажимает кнопку "Тест", THE Frontend SHALL отправить POST /api/alerts/test и отобразить результат отправки тестового сообщения в Telegram

### Requirement 7: API управления арбитражным движком

**User Story:** As a трейдер, I want управлять арбитражным движком через UI, so that я могу запускать, останавливать и мониторить арбитраж без доступа к серверу.

#### Acceptance Criteria

1. THE Backend SHALL предоставить GET /api/arbitrage/status, возвращающий: состояние движка (running/stopped), текущие открытые позиции, статистику PNL, настройки
2. THE Backend SHALL предоставить POST /api/arbitrage/start для запуска Arbitrage_Engine
3. THE Backend SHALL предоставить POST /api/arbitrage/stop для остановки Arbitrage_Engine с закрытием всех открытых позиций
4. THE Backend SHALL предоставить POST /api/arbitrage/kill-switch для немедленной аварийной остановки
5. THE Backend SHALL предоставить GET /api/arbitrage/trades с пагинацией для просмотра истории арбитражных сделок
6. THE Backend SHALL предоставить PATCH /api/arbitrage/settings для обновления параметров (пороги, размеры, лимиты) без перезапуска
7. WHEN запрос к эндпоинтам арбитража поступает без валидного admin-токена (при настроенном ADMIN_TOKEN), THE Backend SHALL вернуть HTTP 401
