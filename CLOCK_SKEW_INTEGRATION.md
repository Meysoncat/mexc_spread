# Clock Skew Detection Integration — Completed

## Задача 2: Интеграция Clock Skew Detection в рантайм

### Выполненные изменения:

#### 1. Singleton ClockSkewDetector в backend/main.py ✅

**Файл**: `mexc_monitor/backend/main.py`

- Создан singleton `detector: ClockSkewDetector` для глобального использования
- Добавлен контекстный менеджер `lifespan` для инициализации при старте приложения
- Создан middleware для автоматического обнаружения skew на всех HTTP-ответах

**API endpoint**:
```python
@app.get("/api/clock-skew/status")
async def clock_skew_status():
    """Возвращает статус clock skew для всех бирж."""
    return {
        "ok": True,
        "max_skew_ms": detector._max_skew_ms,
        "skews": detector.get_status(),
    }
```

#### 2. Middleware для автоматического обнаружения Clock Skew ✅

**Файл**: `mexc_monitor/backend/main.py`

- `clock_skew_middleware` — FastAPI middleware для мониторинга Date header
- Автоматически проверяет все HTTP-ответы от бирж
- Вызывает `detector.check_from_response(exchange_name, response.headers)` для каждого ответа

#### 3. Кастомный httpx.Client с Clock Skew Detection ✅

**Файл**: `mexc_monitor/clock_skew_middleware.py`

- `ClockSkewClient(httpx.Client)` — подкласс с автоматической обработкой
- Переопределены методы `request`, `get`, `post`, `put`, `delete`
- Обрабатывает `Date header` из всех HTTP-ответов
- Сохраняет `exchange` для корректной идентификации источника

#### 4. Обновление freshness.py с adjust_for_skew ✅

**Файл**: `mexc_monitor/freshness.py`

- Добавлен параметр `exchange: str = ""` в `get_fresh_tick`, `get_fresh_tick_multi`, `get_fresh_ticks`
- Добавлен параметр `adjust_for_skew: bool = True` в все функции
- Добавлен глобальный детектор `_get_detector()` для ленивой инициализации
- Функция `tick_age_ms()` теперь может корректировать timestamp для skew
- `detector.adjust_timestamp(exchange, timestamp_ms)` — коррекция времени сервера

**Пример использования**:
```python
# Базовый вызов с коррекцией по умолчанию
tick = get_fresh_tick("BTCUSDT", max_age_ms=5000, exchange="mexc")

# Отключить коррекцию для тестов
tick = get_fresh_tick("BTCUSDT", max_age_ms=5000, exchange="mexc", adjust_for_skew=False)

# Получить все свежие тики с exchange параметрами
ticks = get_fresh_ticks(
    symbols=["BTCUSDT", "ETHUSDT"],
    exchanges=["mexc", "binance"],
    adjust_for_skew=True
)
```

#### 5. Обновление spread_capture.py ✅

**Файл**: `mexc_monitor/spread_capture.py`

- Обновлены все вызовы `get_fresh_tick`:
  - `_step()`: добавлен `exchange=self._settings.exchange`
  - `get_current_pnl()`: добавлен `exchange=self._settings.exchange`

**Пример**:
```python
def _step(self) -> None:
    buffer_key = self._get_buffer_key()
    tick = get_fresh_tick(
        buffer_key,
        self._settings.max_tick_age_ms,
        exchange=self._settings.exchange,
        adjust_for_skew=True
    )
    # ...
```

#### 6. Обновление arbitrage/engine.py ✅

**Файл**: `mexc_monitor/arbitrage/engine.py`

- Обновлены все вызовы `get_fresh_tick`:
  - `_get_fresh_mexc_tick()`: добавлен `exchange="mexc"`
  - `_get_fresh_aster_tick()`: добавлен `exchange="asterdex"`

**Пример**:
```python
def _get_fresh_mexc_tick(self, symbol: str) -> SpreadTick | None:
    max_age = self._settings.max_tick_age_ms
    tick = get_fresh_tick(
        symbol,
        max_age,
        exchange="mexc",
        adjust_for_skew=True
    )
    if tick is not None:
        return tick
    # ... fallback logic
```

#### 7. Обновление http_utils.py ✅

**Файл**: `mexc_monitor/http_utils.py`

- `mexc_httpx_client()` теперь принимает параметр `exchange: str = "generic"`
- Передает `exchange` в `ClockSkewClient`
- Использует сахарный синтаксис для совместимости с существующими вызовами

**Пример**:
```python
def mexc_httpx_client(settings: Settings, exchange: str = "generic"):
    kwargs: dict[str, Any] = {"timeout": settings.timeout_sec}
    if settings.http_extra_headers:
        kwargs["headers"] = dict(settings.http_extra_headers)

    with ClockSkewClient(**kwargs, exchange=exchange) as client:
        yield client
```

#### 8. Тесты ✅

**Файл**: `tests/test_clock_skew_integration.py`

- `test_get_detector_lazy_init()` — проверка lazy initialization
- `test_clock_skew_client_integration()` — интеграция ClockSkewClient
- `test_clock_skew_client_different_exchanges()` — разные биржи
- `test_clock_skew_detection_disabled()` — отсутствие Date header
- `test_clock_skew_detector_methods()` — основные методы ClockSkewDetector
- `test_freshness_with_skew_adjustment()` — коррекция timestamp

**Результат**: Все 6 тестов passed ✅

#### 9. Гарантия работоспособности существующих тестов ✅

**Тесты**: `tests/test_futures_arb` (163 теста) — все passed ✅

## Использование:

### Backend API
```python
from mexc_monitor.backend.main import detector, app

# Глобальный доступ к детектору
skew_ms = detector.get_skew_ms("mexc")
is_skewed = detector.is_skewed("mexc")
status = detector.get_status()

# HTTP запрос к API
response = requests.get("http://localhost:8000/api/clock-skew/status")
print(response.json())
```

### Trading Engines
```python
from mexc_monitor.freshness import get_fresh_tick

# Обновленный вызов с автоматической коррекцией timestamp
tick = get_fresh_tick(
    symbol="BTCUSDT",
    max_age_ms=5000,
    exchange="mexc",
    adjust_for_skew=True  # по умолчанию True
)
```

### HTTP Client
```python
from mexc_monitor.http_utils import mexc_httpx_client
from mexc_monitor.config import Settings

settings = Settings()

# Exchange будет использован для clock skew detection
with mexc_httpx_client(settings, exchange="mexc") as client:
    response = client.get("https://api.mexc.com/api/v3/ticker/price")
    # Clock skew автоматически определяется из Date header
```

### Direct API Call
```python
from mexc_monitor.clock_skew_middleware import ClockSkewClient

client = ClockSkewClient(exchange="mexc")
response = client.get("https://api.mexc.com/api/v3/ticker/price")
# Clock skew автоматически определяется из Date header
```

## Преимущества:

1. **Автоматическая детекция** — не нужно вручную вызывать `check_from_response`
2. **Единая точка контроля** — singleton detector используется во всем приложении
3. **Контекстная коррекция** — freshness guard автоматически корректирует timestamp
4. **Обратная совместимость** — старые вызовы `get_fresh_tick` продолжают работать
5. **Высокая производительность** — ленивая инициализация и кэширование

## Значение:

Значительное улучшение надежности live-трейдинга:
- Фиксация фантом-арбитражных сигналов
- Корректный расчет времени удержания позиций
- Профилактика проблем с recvWindow нарушений
- Оптимизация времени обработки тиков