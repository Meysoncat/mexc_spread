# Requirements Document

## Introduction

Модуль **Lead-Lag Arbitrage** реализует мониторинг и анализ временных задержек между обновлениями цен на разных торговых площадках. Когда один и тот же актив торгуется на нескольких биржах, цены обновляются не синхронно — движение цены на "ведущей" бирже (обычно Binance как наиболее ликвидная) повторяется на "отстающих" биржах (MEXC, Bybit, OKX) с задержкой от миллисекунд до секунд.

Модуль предоставляет:
- Сбор данных order book в реальном времени с нескольких бирж через WebSocket
- Динамическое определение lead-lag зависимостей через кросс-корреляцию
- Генерацию сигналов при расхождении цен (лидер двинулся, отстающий ещё нет)
- Отображение возможностей на выделенной странице дашборда `/lead-lag`
- Хранение исторических данных сигналов для бэктестинга и статистики

Это модуль **только для анализа** — без автоматической торговли.

## Glossary

- **WS_Manager**: Компонент, управляющий WebSocket-соединениями к нескольким биржам, нормализующий входящие данные в единый формат
- **Price_Buffer**: Потокобезопасный кольцевой буфер, хранящий последние N секунд ценовых данных по каждому символу и бирже
- **Lag_Detector**: Компонент, оценивающий задержку (lag) между биржами через кросс-корреляцию ценовых временных рядов
- **Signal_Generator**: Компонент, генерирующий сигналы при значительном расхождении цен между лидером и отстающим
- **Signal_Store**: Компонент, сохраняющий сигналы и статистику в SQLite для исторического анализа
- **Stats_Engine**: Компонент, вычисляющий агрегированную статистику из исторических сигналов
- **LeadLag_Dashboard**: UI-компонент (React), отображающий сигналы, lag-оценки и статистику в реальном времени
- **Lead-lag зависимость**: Временная связь между ценами на двух биржах, где одна биржа (лидер) обновляет цену раньше другой (отстающий)
- **Z-score**: Нормализованное отклонение текущего спреда от среднего, измеренное в стандартных отклонениях
- **Спред (spread)**: Разница mid-цен между лидером и отстающим: spread = leader_mid − lagger_mid
- **Спред в bps**: 10000 × (leader_mid − lagger_mid) / leader_mid
- **Кросс-корреляция**: Статистический метод определения временного сдвига между двумя временными рядами
- **PriceSnapshot**: Единичное наблюдение цены с одной биржи (exchange, symbol, bid, ask, mid, timestamp_ms)
- **LeadLagSignal**: Обнаруженная возможность lead-lag арбитража с полным жизненным циклом (active → resolved/expired)
- **Theoretical PnL**: Теоретическая прибыль сигнала: entry_spread_bps − exit_spread_bps − 2 × assumed_taker_fee_bps

## Requirements

### Requirement 1: Сбор ценовых данных через WebSocket

**User Story:** Как аналитик, я хочу получать данные order book в реальном времени с нескольких бирж через WebSocket, чтобы иметь актуальные цены для анализа lead-lag зависимостей.

#### Acceptance Criteria

1. WHEN Lead-Lag движок запускается, THE WS_Manager SHALL установить WebSocket-соединения ко всем настроенным биржам (Binance, MEXC, Bybit, OKX) и подписаться на bookTicker/ticker потоки для настроенных символов в течение 10 секунд после старта
2. WHEN сообщение поступает от биржи, THE WS_Manager SHALL нормализовать его в объект PriceSnapshot (exchange, symbol, bid, ask, mid, timestamp_ms) независимо от формата конкретной биржи, где bid > 0, ask > 0, ask >= bid, а timestamp_ms содержит время получения сообщения (monotonic clock) в миллисекундах
3. IF полученное сообщение не содержит обязательных полей (bid, ask, symbol) или содержит невалидные значения (bid <= 0, ask < bid), THEN THE WS_Manager SHALL отбросить сообщение без генерации PriceSnapshot и инкрементировать счётчик отброшенных сообщений для данной биржи
4. WHEN WebSocket-соединение разрывается, THE WS_Manager SHALL выполнить переподключение с экспоненциальным backoff (1с → 2с → 4с → ... → 60с максимум), продолжая попытки неограниченно до восстановления соединения
5. THE WS_Manager SHALL предоставлять статус подключения для каждой биржи (connected/disconnected/stale) и timestamp последнего успешно обработанного сообщения в миллисекундах
6. IF данные от биржи не поступают более 5 секунд, THEN THE WS_Manager SHALL пометить соединение как stale и исключить эту биржу из генерации сигналов до получения следующего валидного сообщения, после чего статус SHALL автоматически и немедленно возвращаться в connected при получении любого валидного сообщения
7. IF при запуске соединение к бирже не может быть установлено, THEN THE WS_Manager SHALL применить тот же механизм переподключения с экспоненциальным backoff и пометить биржу как disconnected, не блокируя работу остальных соединений

### Requirement 2: Буферизация ценовых данных

**User Story:** Как аналитик, я хочу иметь доступ к последним N секундам ценовых данных по каждой бирже и символу, чтобы вычислять кросс-корреляцию и текущие спреды.

#### Acceptance Criteria

1. WHEN новый PriceSnapshot поступает, THE Price_Buffer SHALL сохранить его в кольцевом буфере для соответствующей пары (exchange, symbol), ограниченном максимальной ёмкостью 18 000 записей на пару
2. WHEN при чтении данных из буфера обнаруживаются записи старше max_history_sec (по умолчанию 60 секунд), THE Price_Buffer SHALL исключить их из результата и удалить из буфера
3. THE Price_Buffer SHALL предоставлять доступ к последнему PriceSnapshot (bid, ask, mid, timestamp_ms) по каждой паре (exchange, symbol) за O(1) времени
4. WHEN запрашивается временной ряд цен за указанный период (от 1 до max_history_sec секунд), THE Price_Buffer SHALL возвращать упорядоченный по времени список PriceSnapshot для заданной пары (exchange, symbol), ограниченный не более чем 2000 точками с равномерным прореживанием при превышении
5. THE Price_Buffer SHALL обеспечивать потокобезопасность через блокировку: одновременная запись из нескольких WebSocket-потоков и чтение из потоков детектора и генератора сигналов не SHALL приводить к повреждению данных или взаимной блокировке
6. WHEN данные обеих бирж (leader и lagger) доступны для одного символа, THE Price_Buffer SHALL вычислять текущий спред по формуле: spread_bps = 10000 × (leader_mid − lagger_mid) / leader_mid; IF leader_mid равен нулю или отрицательному значению, THEN THE Price_Buffer SHALL отклонить данные как невалидные и возвращать None для спреда
7. IF для символа доступны данные только одной биржи из пары, THEN THE Price_Buffer SHALL возвращать None в качестве значения спреда для этого символа

### Requirement 3: Определение Lead-Lag зависимостей

**User Story:** Как аналитик, я хочу динамически определять, какая биржа является лидером, а какая отстающим для каждого символа, чтобы корректно генерировать сигналы.

#### Acceptance Criteria

1. THE Lag_Detector SHALL вычислять кросс-корреляцию между mid-ценовыми рядами всех настроенных бирж для каждого символа, определяя задержку в миллисекундах как сдвиг с максимальным коэффициентом корреляции
2. THE Lag_Detector SHALL обновлять оценки lag с настраиваемым интервалом (по умолчанию каждые 30 секунд, допустимый диапазон: от 5 до 300 секунд)
3. THE Lag_Detector SHALL динамически определять роль лидера как биржу, чьи ценовые изменения предшествуют остальным (максимальная корреляция достигается при положительном сдвиге относительно данной биржи), пересматривая роли при каждом обновлении оценки
4. THE Lag_Detector SHALL предоставлять для каждой оценки: lag_ms, correlation coefficient (от -1.0 до 1.0), confidence score (от 0.0 до 1.0, вычисляемый как отношение количества использованных наблюдений к максимально возможному за окно price_buffer_history_sec), количество использованных наблюдений
5. THE Lag_Detector SHALL гарантировать, что все оценки lag >= 0 мс и <= price_buffer_history_sec × 1000 мс
6. IF количество наблюдений в Price_Buffer для пары (exchange, symbol) менее 20, THEN THE Lag_Detector SHALL пропустить вычисление для данной пары и пометить оценку как unavailable до накопления достаточного количества данных

### Requirement 4: Генерация сигналов

**User Story:** Как аналитик, я хочу получать сигналы, когда лидер значительно двинулся, а отстающий ещё не догнал, чтобы видеть потенциальные арбитражные возможности.

#### Acceptance Criteria

1. WHEN z-score спреда между лидером и отстающим превышает z_score_entry_threshold (по умолчанию 2.0) И абсолютное значение спреда превышает min_spread_bps (по умолчанию 3.0 bps) И для пары (symbol, lagger_exchange) не существует ACTIVE сигнала, THE Signal_Generator SHALL создать новый LeadLagSignal со статусом ACTIVE, зафиксировав: symbol, leader_exchange, lagger_exchange, direction (long если leader_mid > lagger_mid, short если leader_mid < lagger_mid), z_score, entry_spread_bps, leader_mid_at_signal, lagger_mid_at_signal, estimated_lag_ms, created_at
2. THE Signal_Generator SHALL вычислять entry_spread_bps сигнала по формуле: 10000 × (leader_mid − lagger_mid) / leader_mid в момент создания сигнала
3. WHEN z-score спреда для ACTIVE сигнала падает ниже z_score_exit_threshold (по умолчанию 0.5), THE Signal_Generator SHALL перевести сигнал в статус RESOLVED, зафиксировав: resolved_at (текущий timestamp), actual_lag_ms (разница resolved_at − created_at в миллисекундах), exit_spread_bps (текущий спред в bps на момент разрешения)
4. WHEN время жизни активного сигнала превышает signal_timeout_sec × 1000 миллисекунд (по умолчанию 10 секунд = 10000 мс, сравнение выполняется в миллисекундах), THE Signal_Generator SHALL перевести сигнал в статус EXPIRED, зафиксировав: resolved_at (текущий timestamp), exit_spread_bps (текущий спред в bps на момент истечения); сигналы, уже перешедшие в статус RESOLVED, SHALL не подлежать истечению
5. THE Signal_Generator SHALL гарантировать, что для пары (symbol, lagger_exchange) существует не более одного ACTIVE сигнала одновременно; IF условие входа выполняется при наличии существующего ACTIVE сигнала для той же пары, THEN THE Signal_Generator SHALL пропустить генерацию нового сигнала
6. THE Signal_Generator SHALL вычислять theoretical_pnl_bps для сигналов в статусе RESOLVED и EXPIRED по формуле: entry_spread_bps − exit_spread_bps − 2 × assumed_taker_fee_bps (по умолчанию 2.0 bps за сторону)

### Requirement 5: Хранение сигналов

**User Story:** Как аналитик, я хочу сохранять все сигналы в базу данных, чтобы анализировать историю и проводить бэктестинг.

#### Acceptance Criteria

1. WHEN новый сигнал создаётся, THE Signal_Store SHALL сохранить его в SQLite с полями: id, symbol, leader_exchange, lagger_exchange, direction, z_score, entry_spread_bps, leader_mid_at_signal, lagger_mid_at_signal, estimated_lag_ms, status, created_at
2. WHEN булевый флаг signal_resolved или signal_expired становится true для сигнала, THE Signal_Store SHALL обновить запись: resolved_at, actual_lag_ms, exit_spread_bps, theoretical_pnl_bps
3. THE Signal_Store SHALL поддерживать запросы с фильтрами: по символу, временному диапазону, статусу, направлению — и возвращать не более 1000 записей на один запрос, отсортированных по created_at DESC
4. IF запись в SQLite не удаётся (диск заполнен, повреждение), THEN THE Signal_Store SHALL записать ошибку в лог, продолжить генерацию сигналов в памяти (буфер до 1000 сигналов) и повторять попытки записи каждые 30 секунд
5. IF буфер в памяти достигает 1000 сигналов и запись в SQLite по-прежнему недоступна, THEN THE Signal_Store SHALL отбрасывать самые старые сигналы из буфера при поступлении новых (FIFO) и записать предупреждение в лог

### Requirement 6: Агрегированная статистика

**User Story:** Как аналитик, я хочу видеть агрегированную статистику по сигналам за настраиваемый период, чтобы оценивать эффективность обнаружения lead-lag возможностей.

#### Acceptance Criteria

1. THE Stats_Engine SHALL вычислять за настраиваемое окно (от 1 до 168 часов, по умолчанию 24 часа): total_signals, resolved_signals, expired_signals, win_rate, avg_lag_ms, median_lag_ms, avg_theoretical_pnl_bps, total_theoretical_pnl_bps, signals_per_hour, где win_rate = количество resolved-сигналов с theoretical_pnl_bps > 0 / общее количество resolved-сигналов
2. THE Stats_Engine SHALL предоставлять статистику в разбивке по символам: количество сигналов, средний lag, средний PnL для каждого символа
3. THE Stats_Engine SHALL гарантировать консистентность: total_signals == resolved_signals + expired_signals + active_signals
4. THE Stats_Engine SHALL предоставлять распределение lag-времени в виде гистограммы с фиксированными бакетами по 50 мс (0-50, 50-100, ..., до максимального lag в окне) с количеством сигналов в каждом бакете
5. IF в запрошенном окне отсутствуют сигналы, THEN THE Stats_Engine SHALL возвращать нулевые значения для всех счётчиков, null для avg/median метрик и пустой массив для распределения lag-времени

### Requirement 7: REST API

**User Story:** Как разработчик фронтенда, я хочу REST API для получения данных lead-lag анализа, чтобы отображать их в React UI.

#### Acceptance Criteria

1. THE API SHALL предоставлять endpoint GET /api/lead-lag/status, возвращающий JSON с полями: running (bool), connections (объект: ключ — имя биржи, значение — {connected: bool, last_message_ms: int}), symbols_monitored (список строк), active_signals_count (int >= 0), uptime_sec (float >= 0)
2. THE API SHALL предоставлять endpoint GET /api/lead-lag/signals с параметрами: active (bool, по умолчанию false), symbol (string, опционально), limit (int, от 1 до 1000, по умолчанию 50), возвращающий список сигналов отсортированных по created_at DESC
3. THE API SHALL предоставлять endpoint GET /api/lead-lag/stats с параметром window_hours (int, от 1 до 168, по умолчанию 24), возвращающий агрегированную статистику в формате LeadLagStats
4. THE API SHALL предоставлять endpoint GET /api/lead-lag/prices?symbol=X, возвращающий текущие mid-цены по всем подключённым биржам для указанного символа; IF символ не найден в мониторинге, THEN SHALL возвращать HTTP 404 с описанием ошибки; endpoint SHALL возвращать цены для индивидуально отслеживаемых символов даже если глобальный счётчик мониторинга равен нулю
5. THE API SHALL предоставлять endpoint GET /api/lead-lag/lag-estimates, возвращающий текущие оценки lag для всех символов в формате списка LagEstimate
6. THE API SHALL предоставлять endpoints POST /api/lead-lag/start и POST /api/lead-lag/stop для управления движком; IF движок уже запущен и вызван start (или уже остановлен и вызван stop), THEN SHALL возвращать HTTP 200 с текущим статусом без повторного запуска/остановки (идемпотентность)
7. IF параметры запроса невалидны (limit < 1, limit > 1000, window_hours < 1, window_hours > 168), THEN THE API SHALL всегда возвращать HTTP 422 с описанием ошибки валидации, независимо от состояния системы или доступности других endpoints

### Requirement 8: Фронтенд — Дашборд Lead-Lag

**User Story:** Как аналитик, я хочу видеть дашборд с live-сигналами, lag-оценками и статистикой, чтобы в реальном времени отслеживать lead-lag возможности.

#### Acceptance Criteria

1. THE LeadLag_Dashboard SHALL отображать ленту активных сигналов с полями: symbol, direction, z_score, entry_spread_bps, estimated_lag_ms, время жизни (в формате секунд с момента создания сигнала), отсортированную по времени создания (новейшие сверху), максимум 50 записей
2. THE LeadLag_Dashboard SHALL отображать heatmap lag-оценок (матрица exchange × symbol с lag в мс), где ячейки окрашены пропорционально значению lag от 0 до price_buffer_history_sec × 1000 мс
3. THE LeadLag_Dashboard SHALL отображать панель статистики: win_rate, avg_lag_ms, theoretical_pnl (суммарный в bps), signal_count за выбранный период из набора: 1 час, 6 часов, 24 часа (по умолчанию 24 часа)
4. THE LeadLag_Dashboard SHALL отображать график сравнения mid-цен по выбранному символу на всех подключённых биржах, используя данные из endpoint GET /api/lead-lag/prices
5. THE LeadLag_Dashboard SHALL отображать индикаторы статуса подключения для каждой биржи с тремя визуальными состояниями: connected (последнее сообщение < 5 секунд назад), stale (последнее сообщение 5-30 секунд назад), disconnected (нет соединения или последнее сообщение > 30 секунд назад)
6. THE LeadLag_Dashboard SHALL обновлять данные сигналов и статусов через polling с интервалом 3 секунды, а статистику — с интервалом 10 секунд
7. IF lead-lag движок не запущен (GET /api/lead-lag/status возвращает running=false), THEN THE LeadLag_Dashboard SHALL отображать сообщение о неактивном состоянии движка и кнопку запуска
8. IF лента сигналов пуста (нет активных сигналов), THEN THE LeadLag_Dashboard SHALL отображать пустое состояние с текстовым указанием об отсутствии активных сигналов
9. IF запрос к API завершается ошибкой или таймаутом (> 10 секунд), THEN THE LeadLag_Dashboard SHALL отображать индикатор ошибки соединения с backend и продолжать polling

### Requirement 9: Конфигурация движка

**User Story:** Как аналитик, я хочу гибко настраивать параметры lead-lag движка, чтобы адаптировать анализ под текущие рыночные условия.

#### Acceptance Criteria

1. THE WS_Manager SHALL принимать конфигурацию из JSON-файла (config/external_apis.json, секция "lead_lag"), проверяя только наличие и структуру файла (валидный JSON с секцией "lead_lag"), со следующими параметрами: enabled (boolean), leader_exchange (string), lagger_exchanges (list of strings), symbols (list of strings), market (string: "spot" или "futures"), z_score_entry_threshold (float > 0), z_score_exit_threshold (float >= 0), signal_timeout_sec (float > 0), rolling_window_sec (float > 0), min_spread_bps (float >= 0), lag_estimation_interval_sec (float > 0), price_buffer_history_sec (float > 0), db_path (string), assumed_taker_fee_bps (float >= 0), ws_urls (object: mapping exchange names to WSS URLs)
2. IF секция "lead_lag" отсутствует в config/external_apis.json или файл не является валидным JSON, THEN THE WS_Manager SHALL использовать значения по умолчанию: enabled=false, leader_exchange="binance", lagger_exchanges=["mexc"], symbols=["BTCUSDT","ETHUSDT"], market="futures", z_score_entry_threshold=2.0, z_score_exit_threshold=0.5, signal_timeout_sec=10.0, rolling_window_sec=300.0, min_spread_bps=3.0, lag_estimation_interval_sec=30.0, price_buffer_history_sec=60.0, db_path="data/lead_lag_signals.sqlite", assumed_taker_fee_bps=2.0
3. THE Signal_Generator SHALL валидировать конфигурацию по следующим правилам: z_score_entry_threshold > z_score_exit_threshold, z_score_entry_threshold > 0, z_score_exit_threshold >= 0, signal_timeout_sec > 0, rolling_window_sec > 0, min_spread_bps >= 0, lag_estimation_interval_sec > 0, price_buffer_history_sec > 0, symbols непустой, lagger_exchanges непустой, leader_exchange не входит в lagger_exchanges, assumed_taker_fee_bps >= 0, market равен "spot" или "futures", ws_urls содержит URL для leader_exchange и каждого из lagger_exchanges
4. IF конфигурация не проходит валидацию, THEN THE Signal_Generator SHALL отклонить запуск движка и вернуть сообщение об ошибке, указывающее каждое нарушенное правило валидации и фактическое значение параметра

### Requirement 10: Обработка ошибок и отказоустойчивость

**User Story:** Как аналитик, я хочу, чтобы движок корректно обрабатывал сбои соединений и продолжал работу, чтобы не терять данные при временных проблемах.

#### Acceptance Criteria

1. WHEN все отстающие биржи теряют соединение или помечены как stale (нет данных более 5 секунд), THE Signal_Generator SHALL немедленно приостановить генерацию сигналов и установить статус движка "degraded", не используя данные только Binance для генерации сигналов
2. WHEN лидер (Binance) теряет соединение или помечен как stale (нет данных более 5 секунд), THE Signal_Generator SHALL приостановить всю генерацию сигналов и установить статус движка "no_leader"
3. WHEN соединение восстанавливается и Price_Buffer накапливает не менее 5 секунд непрерывных данных от восстановленной биржи, THE WS_Manager SHALL перевести статус движка обратно в "running" и возобновить нормальную генерацию сигналов, независимо от наличия других проблем в системе (например, проблем с базой данных)
4. IF запись в базу данных не удаётся, THEN THE Signal_Store SHALL буферизовать сигналы в памяти (до 1000 записей) и повторять попытки записи каждые 30 секунд без остановки движка
5. IF буфер в памяти достигает 1000 записей и запись в базу данных по-прежнему невозможна, THEN THE Signal_Store SHALL отбрасывать самые старые сигналы из буфера при поступлении новых и записывать предупреждение в лог
