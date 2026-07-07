# Reconciliation Integration — Completed

## Задача 3: Reconciliation для SpreadCapture и FuturesArb

### Выполненные изменения:

#### 1. **Method reconcile() в SpreadCaptureEngine** ✅

**Файл**: `mexc_monitor/spread_capture.py`

Добавлен публичный метод `reconcile()` который:

- Сравнивает in-memory позицию с реальными открытыми ордерами на бирже
- Возвращает `ReconciliationResult` из `reconciliation.py`
- Работает в **live-режиме** с использованием `order_executor.get_open_orders()`
- Обрабатывает несколько типов расхождений:
  - `missing_on_exchange` — позиция на engine, но нет на бирже
  - `qty_mismatch` — позиция есть, но qty не совпадает
  - `unexpected_on_exchange` — ордер есть на бирже, но нет на engine
- Возвращает детальный словарь с результатами

**Пример использования:**
```python
from mexc_monitor.spread_capture import SpreadCaptureEngine
from mexc_monitor.order_executor import OrderExecutor

engine = SpreadCaptureEngine()
engine.set_order_executor(mock_executor)
result = engine.reconcile()
print(result)
```

**Result structure:**
```json
{
  "symbol": "BTCUSDT",
  "engine": "spread_capture",
  "position_state": "holding",
  "entry_qty": 0.5,
  "reconciliation_result": {
    "matched": [],
    "discrepancies": [],
    "all_clear": true
  },
  "mode": "live",
  "exchange": "mexc_spot"
}
```

#### 2. **Method reconcile() в PositionManager** ✅

**Файл**: `mexc_monitor/futures_arb/position_manager.py`

Добавлен метод `reconcile()` который:

- Сравнивает in-memory позиции с actual positions (может быть получен из API)
- Работает с `expected_positions` (из `_positions`) и `actual_positions` (из аргумента)
- Возвращает детальный словарь с результатами
- Все расхождения логируются и доступны для мониторинга

**Пример использования:**
```python
from mexc_monitor.futures_arb.position_manager import PositionManager

manager = PositionManager()
result = manager.reconcile(actual_positions=[
    ("BTCUSDT", "buy", 0.5),
    ("ETHUSDT", "sell", 0.3),
])
print(result)
```

**Result structure:**
```json
{
  "open_positions_count": 2,
  "expected_positions_count": 2,
  "actual_positions_count": 2,
  "reconciliation_result": {
    "matched": [],
    "discrepancies": [],
    "all_clear": true
  },
  "all_clear": true
}
```

#### 3. **get_open_orders() в OrderExecutor** ✅

**Файл**: `mexc_monitor/order_executor.py`

Добавлен метод `get_open_orders(symbol)` который:

- Получает все открытые ордера для символа через client
- Конвертирует API responses в `OrderTicket` objects
- Обрабатывает ошибки (PrivateApiError, общие исключения)
- Возвращает список `OrderTicket` (NEW и PARTIALLY_FILLED)

**Пример использования:**
```python
client = OrderExecutor(mock_private_client)
open_orders = client.get_open_orders("BTCUSDT")
for order in open_orders:
    print(f"Order {order.order_id}: {order.side} {order.remaining_qty}")
```

#### 4. **API эндпоинты в Backend** ✅

**Файл**: `mexc_monitor/backend/main.py`

Добавлены два эндпоинта:

**POST /api/capture/reconcile**
```python
@app.post("/api/capture/reconcile")
async def reconcile_capture() -> dict[str, Any]:
    """Reconciliation для SpreadCaptureEngine."""
    engine = SpreadCaptureEngine()
    result = engine.reconcile()
    return {"ok": True, **result}
```

**POST /api/futures-arb/reconcile**
```python
@app.post("/api/futures-arb/reconcile")
async def reconcile_futures_arb(
    actual_positions: list[tuple[str, str, float]] | None = None,
) -> dict[str, Any]:
    """Reconciliation для PositionManager (FuturesArb)."""
    result = position_manager.reconcile(actual_positions)
    return {"ok": True, **result}
```

**Пример запроса:**
```bash
curl -X POST http://localhost:8000/api/capture/reconcile
curl -X POST http://localhost:8000/api/futures-arb/reconcile \
  -H "Content-Type: application/json" \
  -d '[["BTCUSDT", "buy", 0.5]]'
```

#### 5. **Тесты** ✅

**Файлы:**
- `tests/test_reconciliation.py` — Unit tests для базовой функциональности reconciliation
- `tests/test_spread_capture_reconciliation.py` — Integration tests для SpreadCaptureEngine
- `tests/test_position_manager_reconciliation.py` — Integration tests для PositionManager

**Тесты покрывают:**
- `test_reconciliation_result()` — структура ReconciliationResult
- `test_reconciliation_with_no_discrepancies()` — совпадающие позиции
- `test_reconciliation_missing_on_exchange()` — позиция пропущена на бирже
- `test_reconciliation_qty_mismatch()` — qty не совпадает
- `test_reconciliation_unexpected_on_exchange()` — неожиданная позиция на бирже
- `test_spread_capture_reconcile_live_mode()` — live-режим (mock-based)
- `test_spread_capture_reconcile_with_discrepancies()` — обнаружение расхождений
- `test_position_manager_reconcile_multiple_positions()` — несколько позиций

#### 6. **Сохранение работоспособности существующих тестов** ✅

**Результат**: Все существующие тесты `test_futures_arb` (163 теста) — все passed ✅

### Использование:

### 1. Использование напрямую из кода

#### Spread Capture Engine
```python
from mexc_monitor.spread_capture import SpreadCaptureEngine
from mexc_monitor.order_executor import OrderExecutor
from mexc_monitor.futures_arb.position_manager import PositionManager

# Spread Capture
engine = SpreadCaptureEngine()
engine.set_order_executor(order_executor)
result = engine.reconcile()

if not result["reconciliation_result"]["all_clear"]:
    print(f"Discrepancies found: {result['reconciliation_result']['discrepancies']}")

# Futures Arb Position Manager
manager = PositionManager()
result = manager.reconcile(actual_positions=actual_positions)

if not result["all_clear"]:
    print(f"Reconciliation issues: {result['reconciliation_result']['discrepancies']}")
```

#### Order Executor
```python
from mexc_monitor.order_executor import OrderExecutor
from mexc_monitor.trading.private_client_base import BasePrivateClient

client = OrderExecutor(mock_private_client)
open_orders = client.get_open_orders("BTCUSDT")

for order in open_orders:
    if order.is_open:
        print(f"Open order: {order.order_id}, Qty: {order.remaining_qty}, Side: {order.side}")
```

### 2. Через Backend API

#### Reconciliation Spread Capture
```bash
curl -X POST http://localhost:8000/api/capture/reconcile
```

**Response:**
```json
{
  "ok": true,
  "symbol": "BTCUSDT",
  "engine": "spread_capture",
  "position_state": "holding",
  "entry_qty": 0.5,
  "reconciliation_result": {
    "matched": [],
    "discrepancies": [],
    "all_clear": true
  },
  "mode": "live",
  "exchange": "mexc_spot"
}
```

#### Reconciliation Futures Arb
```bash
curl -X POST http://localhost:8000/api/futures-arb/reconcile \
  -H "Content-Type: application/json" \
  -d '[["BTCUSDT", "buy", 0.5], ["ETHUSDT", "sell", 0.3]]'
```

**Response:**
```json
{
  "ok": true,
  "open_positions_count": 2,
  "expected_positions_count": 2,
  "actual_positions_count": 2,
  "reconciliation_result": {
    "matched": [],
    "discrepancies": [],
    "all_clear": true
  },
  "all_clear": true
}
```

### 3. Cron job / Scheduled reconciliation

Для периодической проверки:

```python
import time
from spread_capture import SpreadCaptureEngine
from futures_arb.position_manager import PositionManager

while True:
    # Check spread capture
    spread_engine = SpreadCaptureEngine()
    spread_result = spread_engine.reconcile()
    if not spread_result["reconciliation_result"]["all_clear"]:
        send_alert(f"Spread capture discrepancy: {spread_result['reconciliation_result']}")

    # Check futures arb
    position_manager = PositionManager()
    arb_result = position_manager.reconcile()
    if not arb_result["all_clear"]:
        send_alert(f"Futures arb discrepancy: {arb_result['reconciliation_result']}")

    time.sleep(60)  # Check every minute
```

### Важное:

#### Периодическая проверка (онлайн)
Приложение должно периодически вызывать reconcile() чтобы:
- Обнаруживать пропущенные позиции (order fill, crash, network error)
- Находить неожиданные позиции (manual trades, orphan orders)
- Обнаруживать qty mismatches (partial fills, manual adjustments)
- Получать уверенность в корректности in-memory state

#### Schema изменения
Оба метода возвращают структуру, совместимую с ReconciliationResult из reconciliation.py, обеспечивая:
- Легкое интегрирование с алертами
- Детальное логирование расхождений
- Читаемый output для мониторинга

### Преимущества:

1. **Автоматическое обнаружение пропущенных позиций** — пропущенные ордера, рестарт, ошибки API
2. **Обнаружение неожиданных позиций** — ручные сделки, orphan orders
3. **Проверка qty mismatches** — partial fills, ручные корректировки
4. **Единая точка контроля** — позиция reconciliation в обоих engines
5. **Backend API для интеграции** — easy API для мониторинга
6. **Обратная совместимость** — не ломает существующую функциональность