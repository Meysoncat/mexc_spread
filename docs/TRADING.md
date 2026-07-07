# Автоторговля: процесс и функционал

Документ описывает торговый контур, добавленный в проект: как он работает, какие режимы поддерживает, какие ограничения безопасности и как запускать его по шагам.

---

## 1. Что добавлено

В проект добавлен базовый торговый слой в пакете `mexc_monitor/trading`:

- `engine.py` — торговый цикл (`TradingEngine`) и runtime-состояние;
- `private_client.py` — подписанные приватные запросы к MEXC;
- `risk.py` — минимальный риск-контур;
- `backend/main.py` — API управления торговым движком.

По умолчанию контур безопасен:

- `MEXC_TRADING_ENABLED=false` (не стартует автоматически);
- `MEXC_TRADING_MODE=paper` (без реальных ордеров);
- `MEXC_TRADING_KILL_SWITCH=true` (цикл не отправляет ордера, пока явно не выключить kill switch).

---

## 2. Функционал

### 2.1. Режимы

| Режим | Что делает |
|------|------------|
| `paper` | Не отправляет ордера на биржу; пишет события в `data/trading_events.jsonl`. |
| `live` | Отправляет `LIMIT BUY` через приватный API MEXC (`/api/v3/order`). |

### 2.2. Источник сигнала

Текущая версия использует `safe_load_snapshot(market="spot")` и берёт строку по `MEXC_TRADING_SYMBOL` (например, `BTCUSDT`).

Условие входа:

- если `net_spread_bps >= MEXC_TRADING_MIN_NET_SPREAD_BPS`, движок формирует заявку.

### 2.3. Параметры ордера

- тип: `LIMIT`;
- сторона: `BUY`;
- цена: top bid с опциональным сдвигом `MEXC_TRADING_LIMIT_PRICE_OFFSET_BPS`;
- количество: `MEXC_TRADING_ORDER_QUOTE_NOTIONAL / price`;
- `newClientOrderId`: генерируется движком (`sm-<timestamp>-<seq>`).

### 2.4. Риск-ограничения (MVP)

Перед отправкой ордера выполняются проверки:

- лимит ордеров в сутки: `MEXC_TRADING_MAX_ORDERS_PER_DAY`;
- лимит открытых ордеров: `MEXC_TRADING_MAX_OPEN_ORDERS`;
- ограничение по подряд ошибкам: `MEXC_TRADING_MAX_CONSECUTIVE_ERRORS`;
- если число подряд ошибок достигает лимита, kill switch включается автоматически.

---

## 3. Процесс работы (жизненный цикл)

1. На старте backend создаётся singleton `TradingEngine`.
2. Если `MEXC_TRADING_ENABLED=true`, движок запускается автоматически.
3. В цикле с интервалом `MEXC_TRADING_LOOP_INTERVAL_SEC`:
   - проверяется `kill_switch`;
   - читается snapshot;
   - вычисляется условие входа;
   - применяется риск-контур;
   - выполняется paper/live-действие;
   - событие пишется в журнал.
4. На shutdown API движок останавливается.

---

## 4. API управления

Все endpoints доступны через FastAPI:

| Метод | Endpoint | Назначение |
|------|----------|------------|
| `GET` | `/api/trading/status` | Состояние движка + применённые настройки (секреты маскируются). |
| `POST` | `/api/trading/start` | Запустить цикл. |
| `POST` | `/api/trading/stop` | Остановить цикл. |
| `POST` | `/api/trading/kill-switch?enabled=true|false` | Включить/выключить kill switch. |
| `POST` | `/api/trading/run-once` | Выполнить один шаг цикла без постоянного запуска. |

---

## 5. Переменные окружения

### 5.1. Управление циклом

- `MEXC_TRADING_ENABLED` — автостарт движка на startup API (`true/false`);
- `MEXC_TRADING_MODE` — `paper` или `live`;
- `MEXC_TRADING_SYMBOL` — инструмент (`BTCUSDT`);
- `MEXC_TRADING_KILL_SWITCH` — стартовое состояние kill switch;
- `MEXC_TRADING_LOOP_INTERVAL_SEC` — период цикла;
- `MEXC_TRADING_EVENTS_LOG_PATH` — путь к jsonl-журналу событий.

### 5.2. Сигнал и ордер

- `MEXC_TRADING_MIN_NET_SPREAD_BPS` — порог входа;
- `MEXC_TRADING_ORDER_QUOTE_NOTIONAL` — номинал заявки в котировке;
- `MEXC_TRADING_LIMIT_PRICE_OFFSET_BPS` — сдвиг лимит-цены относительно bid.

### 5.3. Риск

- `MEXC_TRADING_MAX_ORDERS_PER_DAY`;
- `MEXC_TRADING_MAX_OPEN_ORDERS`;
- `MEXC_TRADING_MAX_CONSECUTIVE_ERRORS`.

### 5.4. Ключи MEXC (для `live`)

- `MEXC_API_KEY`;
- `MEXC_API_SECRET`;
- `MEXC_RECV_WINDOW_MS`.

---

## 6. Рекомендованный порядок запуска

1. Запустить API/UI как обычно (`run_modern.bat` или вручную).
2. Поставить `MEXC_TRADING_MODE=paper`, `MEXC_TRADING_ENABLED=false`, `MEXC_TRADING_KILL_SWITCH=true`.
3. Проверить `GET /api/trading/status`.
4. Выключить kill switch через `/api/trading/kill-switch?enabled=false`.
5. Выполнить `/api/trading/run-once` и проверить `data/trading_events.jsonl`.
6. Запустить цикл `/api/trading/start`.
7. Только после стабильной paper-работы переходить в `live`.

---

## 7. Ограничения текущей версии

- Текущая стратегия — базовый пример (один сигнал, один инструмент, `BUY LIMIT`);
- нет отдельного хранения `fills/positions` в БД;
- нет reconciliation-цикла с полным восстановлением состояния после рестарта;
- нет массовых действий (`cancel all`) и расширенного риск-контроля по PnL/exposure.

---

## 8. Безопасность

- Не храните ключи в git и в исходниках — только через переменные окружения.
- Для `live` используйте отдельный API key с минимально необходимыми правами.
- Рекомендуется ограничить ключ по IP на стороне MEXC.
- Перед `live` обязательно прогоняйте сценарии в `paper`.
