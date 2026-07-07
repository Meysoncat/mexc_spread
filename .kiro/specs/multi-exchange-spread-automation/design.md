# Design Document

## Overview

Архитектурный дизайн расширения MEXC Spread Monitor до мультибиржевой платформы автоматизации. Документ описывает компоненты, потоки данных, API-контракты и модели данных для пяти основных подсистем: Telegram-алерты, AsterDEX WebSocket, мультибиржевой Spread Capture, Cross-Exchange Arbitrage Engine и персистентная история кросс-спреда.

## Architecture

### Общая схема компонентов

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Frontend (React)                             │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐ │
│  │ AlertsPanel  │  │ ArbitragePanel│  │ CrossSpreadHistoryChart   │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────────┘ │
└─────────┼──────────────────┼──────────────────────┼─────────────────┘
          │ REST/SSE         │ REST                  │ REST
┌─────────┼──────────────────┼──────────────────────┼─────────────────┐
│         ▼                  ▼                      ▼   Backend (FastAPI)│
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐ │
│  │ /api/alerts  │  │/api/arbitrage│  │ /api/cross-spread/history │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────────┘ │
└─────────┼──────────────────┼──────────────────────┼─────────────────┘
          │                  │                      │
┌─────────┼──────────────────┼──────────────────────┼─────────────────┐
│         ▼                  ▼                      ▼   Core Modules    │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐ │
│  │ AlertService │  │ArbitrageEngine│  │  CrossSpreadStore         │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────────┘ │
│         │                  │                      │                  │
│         │           ┌──────┴───────┐              │                  │
│         │           │              │              │                  │
│         ▼           ▼              ▼              ▼                  │
│  ┌────────────┐ ┌────────┐ ┌────────────┐ ┌──────────┐            │
│  │ Telegram   │ │  MEXC  │ │  AsterDEX  │ │  SQLite  │            │
│  │ Bot API    │ │Private │ │PrivateClient│ │   ORM    │            │
│  └────────────┘ └────────┘ └────────────┘ └──────────┘            │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │              AsterDEX WebSocket Client                         │  │
│  │   (bookTicker stream → Spread_Buffer → SSE → Frontend)        │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │         SpreadCaptureEngine (extended: multi-exchange)         │  │
│  └───────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Потоки данных

```
AsterDEX WS ──bookTicker──► AsterDEX_WS_Client ──push_tick──► Spread_Buffer
                                                                    │
MEXC WS ──bookTicker──► ws_spot_orderbook ──push_tick──► Spread_Buffer
                                                                    │
                                                                    ▼
                                                          ┌─────────────────┐
                                                          │ Cross-Spread    │
                                                          │ Calculator      │
                                                          └────────┬────────┘
                                                                   │
                                              ┌────────────────────┼────────────────────┐
                                              ▼                    ▼                    ▼
                                     ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
                                     │ AlertService │   │ArbitrageEngine│   │CrossSpreadStore│
                                     └──────────────┘   └──────────────┘   └──────────────┘
```

## Components and Interfaces

### 1. Alert Service (`mexc_monitor/alerts/service.py`)

**Ответственность:** Отправка уведомлений в Telegram, rate limiting, конфигурация.

```python
@dataclass
class AlertConfig:
    bot_token: str = ""
    chat_id: str = ""
    enabled: bool = False
    # Типы алертов
    spread_threshold_enabled: bool = True
    spread_threshold_bps: float = 50.0
    arbitrage_enabled: bool = True
    arbitrage_threshold_bps: float = 10.0
    trade_events_enabled: bool = True
    # Rate limiting
    rate_limit_sec: int = 60

class AlertService:
    def __init__(self, config: AlertConfig)
    def send_spread_alert(self, symbol: str, spread_bps: float, threshold_bps: float) -> bool
    def send_arbitrage_alert(self, symbol: str, mexc_mid: float, aster_mid: float, basis_bps: float) -> bool
    def send_trade_alert(self, trade_event: dict) -> bool
    def test_connection(self) -> bool
    def update_config(self, patch: dict) -> AlertConfig
    def get_config(self) -> AlertConfig  # маскирует bot_token
```

**Файлы:**
- `mexc_monitor/alerts/__init__.py`
- `mexc_monitor/alerts/service.py` — основная логика
- `mexc_monitor/alerts/telegram.py` — HTTP-клиент для Telegram Bot API
- `mexc_monitor/alerts/config.py` — загрузка/сохранение конфигурации

### 2. AsterDEX WebSocket Client (`mexc_monitor/aster/ws_client.py`)

**Ответственность:** Подключение к AsterDEX WebSocket, подписка на bookTicker, запись в Spread_Buffer.

```python
class AsterWebSocketClient:
    def __init__(self, symbols: list[str], on_tick: Callable | None = None)
    def start(self) -> None  # запуск в фоновом потоке
    def stop(self) -> None
    def subscribe(self, symbol: str) -> None
    def unsubscribe(self, symbol: str) -> None
    def get_subscribed_symbols(self) -> list[str]
    def is_connected(self) -> bool
```

**WebSocket URL:** `wss://fstream.asterdex.com/ws` (Binance-совместимый формат)

**Формат подписки:**
```json
{"method": "SUBSCRIBE", "params": ["btcusdt@bookTicker"], "id": 1}
```

**Формат данных bookTicker:**
```json
{
  "e": "bookTicker",
  "s": "BTCUSDT",
  "b": "65000.00",  // best bid price
  "B": "1.500",     // best bid qty
  "a": "65001.00",  // best ask price
  "A": "2.300"      // best ask qty
}
```

**Интеграция с Spread_Buffer:**
- Символы записываются с префиксом `ASTER:` для различения от MEXC
- Пример: `ASTER:BTCUSDT` vs `BTCUSDT` (MEXC spot) vs `BTC_USDT` (MEXC futures)

### 3. Extended Spread Capture Engine

**Изменения в `mexc_monitor/spread_capture.py`:**

```python
@dataclass
class CaptureSettings:
    # Существующие поля...
    exchange: str = "mexc_spot"  # NEW: "mexc_spot" | "mexc_futures" | "asterdex"

class SpreadCaptureEngine:
    # Новый метод для выбора клиента
    def _get_exchange_client(self) -> ExchangeAdapter
    # Адаптер для унификации интерфейса
```

**Exchange Adapter Pattern:**
```python
class ExchangeAdapter(Protocol):
    def place_limit_order(self, symbol: str, side: str, qty: float, price: float) -> dict
    def cancel_order(self, symbol: str, order_id: str) -> dict
    def get_order_status(self, symbol: str, order_id: str) -> dict
```

**Реализации:**
- `MexcSpotAdapter` — обёртка над существующим `trading/private_client.py`
- `MexcFuturesAdapter` — обёртка над MEXC futures API
- `AsterDexAdapter` — обёртка над `aster/private_client.py`

### 4. Arbitrage Engine (`mexc_monitor/arbitrage/engine.py`)

**Ответственность:** Мониторинг межбиржевого спреда, автоматическое открытие/закрытие арбитражных позиций.

```python
@dataclass
class ArbitrageSettings:
    enabled: bool = False
    mode: Literal["paper", "live"] = "paper"
    symbols: list[str] = field(default_factory=list)  # ["BTCUSDT", "ETHUSDT"]
    entry_threshold_bps: float = 15.0
    exit_threshold_bps: float = 3.0
    max_position_notional_usdt: float = 500.0
    max_concurrent_trades: int = 3
    max_pending_sec: float = 10.0
    max_hold_sec: float = 600.0
    kill_switch: bool = True
    loop_interval_sec: float = 0.5
    # Комиссии
    mexc_taker_fee_bps: float = 1.0
    aster_taker_fee_bps: float = 2.0

@dataclass
class ArbPosition:
    symbol: str
    state: Literal["pending_open", "open", "pending_close", "closed"]
    buy_exchange: str  # "mexc" | "asterdex"
    sell_exchange: str
    buy_price: float
    sell_price: float
    qty: float
    open_time_ms: int
    close_time_ms: int = 0
    buy_close_price: float = 0.0
    sell_close_price: float = 0.0
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0

class ArbitrageEngine:
    def __init__(self, settings: ArbitrageSettings)
    def start(self) -> dict
    def stop(self) -> dict
    def get_status(self) -> dict
    def get_positions(self) -> list[dict]
    def get_trades(self, limit: int = 50) -> list[dict]
    def set_kill_switch(self, enabled: bool) -> dict
    def update_settings(self, patch: dict) -> dict
```

**Алгоритм основного цикла:**
1. Для каждого символа из `settings.symbols`:
   - Получить latest tick из Spread_Buffer для MEXC (`SYMBOL`) и AsterDEX (`ASTER:SYMBOL`)
   - Вычислить executable spread: `(higher_bid - lower_ask) / mid - total_fees_bps`
   - Если spread > entry_threshold и нет открытой позиции по символу и concurrent < max:
     - Определить направление (кто дороже)
     - Открыть позицию (buy на дешёвой, sell на дорогой)
2. Для каждой открытой позиции:
   - Проверить условия выхода (spread сузился, таймаут)
   - Закрыть при выполнении условий

**Файлы:**
- `mexc_monitor/arbitrage/__init__.py`
- `mexc_monitor/arbitrage/engine.py` — основной движок
- `mexc_monitor/arbitrage/models.py` — dataclasses
- `mexc_monitor/arbitrage/adapters.py` — exchange adapters (shared with spread capture)

### 5. Cross-Spread Store (`mexc_monitor/cross_spread_store.py`)

**Ответственность:** Периодическая запись кросс-спреда в SQLite, API для чтения истории.

**ORM-модель (в `mexc_monitor/orm/models.py`):**
```python
class CrossSpreadSnapshot(Base):
    __tablename__ = "cross_spread_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(40), nullable=False, index=True)
    mexc_bid = Column(Float)
    mexc_ask = Column(Float)
    mexc_mid = Column(Float)
    aster_bid = Column(Float)
    aster_ask = Column(Float)
    aster_mid = Column(Float)
    basis_abs = Column(Float)
    basis_bps = Column(Float)
    funding_rate = Column(Float, nullable=True)
    observed_at = Column(String(30), nullable=False, index=True)
```

**Worker:**
```python
class CrossSpreadWorker:
    def __init__(self, interval_sec: float = 60.0, retention_days: int = 30)
    def start(self) -> None
    def stop(self) -> None
```

Worker каждые `interval_sec` секунд:
1. Получает latest тики из Spread_Buffer для всех символов с данными на обеих биржах
2. Вычисляет basis
3. Записывает batch в SQLite
4. Удаляет записи старше `retention_days`

## API Contracts

### Alert Endpoints

```
GET  /api/alerts/settings        → { ok, config: AlertConfig (masked token) }
PATCH /api/alerts/settings       → { ok, config: AlertConfig }
POST /api/alerts/test            → { ok, message: str }
```

### Arbitrage Endpoints

```
GET  /api/arbitrage/status       → { ok, running, settings, positions, stats }
POST /api/arbitrage/start        → { ok, running, ... }
POST /api/arbitrage/stop         → { ok, running, ... }
POST /api/arbitrage/kill-switch?enabled=true → { ok, kill_switch }
GET  /api/arbitrage/trades?limit=50&offset=0 → { ok, count, trades }
GET  /api/arbitrage/positions    → { ok, positions }
PATCH /api/arbitrage/settings    → { ok, settings }
```

### Cross-Spread History Endpoints

```
GET /api/cross-spread/history?symbol=BTCUSDT&since=ISO&until=ISO&limit=2000
    → { ok, symbol, count, rows: [{observed_at, mexc_mid, aster_mid, basis_bps, ...}] }
```

### AsterDEX WS Management

```
GET  /api/aster/ws/status        → { ok, connected, subscribed_symbols }
POST /api/aster/ws/subscribe     → { ok, symbol }
POST /api/aster/ws/unsubscribe   → { ok, symbol }
```

## Data Models

### Alert Event (для rate limiting)

```python
_last_sent: dict[str, float]  # key = f"{alert_type}:{symbol}" → timestamp
```

### Arbitrage Trade Record

```python
@dataclass
class ArbTradeRecord:
    id: str  # UUID
    symbol: str
    mode: str  # "paper" | "live"
    buy_exchange: str
    sell_exchange: str
    buy_entry_price: float
    sell_entry_price: float
    buy_exit_price: float
    sell_exit_price: float
    qty: float
    notional_usdt: float
    entry_basis_bps: float
    exit_basis_bps: float
    open_time_iso: str
    close_time_iso: str
    hold_sec: float
    gross_pnl_usdt: float
    total_fees_usdt: float
    net_pnl_usdt: float
    net_pnl_bps: float
    close_reason: str  # "spread_converged" | "timeout" | "kill_switch" | "manual"
```

## Configuration

### Новые поля в `config/external_apis.json`

```json
{
  "alerts": {
    "enabled": false,
    "bot_token": "",
    "chat_id": "",
    "spread_threshold_enabled": true,
    "spread_threshold_bps": 50.0,
    "arbitrage_enabled": true,
    "arbitrage_threshold_bps": 10.0,
    "trade_events_enabled": true,
    "rate_limit_sec": 60
  },
  "asterdex_ws": {
    "enabled": false,
    "url": "wss://fstream.asterdex.com/ws",
    "symbols": [],
    "ping_interval_sec": 30,
    "reconnect_base_sec": 1.0,
    "reconnect_max_sec": 60.0
  },
  "arbitrage": {
    "enabled": false,
    "mode": "paper",
    "symbols": [],
    "entry_threshold_bps": 15.0,
    "exit_threshold_bps": 3.0,
    "max_position_notional_usdt": 500.0,
    "max_concurrent_trades": 3,
    "max_pending_sec": 10.0,
    "max_hold_sec": 600.0,
    "kill_switch": true,
    "mexc_taker_fee_bps": 1.0,
    "aster_taker_fee_bps": 2.0
  },
  "cross_spread_history": {
    "enabled": false,
    "interval_sec": 60,
    "retention_days": 30,
    "db_path": "data/cross_spread_history.sqlite"
  }
}
```

### Переменные окружения

| Переменная | Назначение |
|-----------|-----------|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота (приоритет над JSON) |
| `TELEGRAM_CHAT_ID` | ID чата для алертов |
| `ASTER_WS_URL` | WebSocket URL AsterDEX (override) |

## Threading Model

```
Main Thread (uvicorn)
├── AsterDEX WS Thread (daemon) — ws_client._run_loop()
├── CrossSpread Worker Thread (daemon) — periodic SQLite writes
├── ArbitrageEngine Thread (daemon) — arb loop
├── SpreadCaptureEngine Thread (daemon) — existing
├── AlertService — sync calls from other threads via queue
└── History Worker Thread (daemon) — existing
```

**Синхронизация:**
- Spread_Buffer: `threading.Lock` (существующий)
- ArbitrageEngine: `threading.Lock` для positions/stats
- AlertService: `threading.Lock` для config + `queue.Queue` для async sends
- CrossSpreadStore: SQLAlchemy session per write (thread-safe)

## Error Handling

| Компонент | Ошибка | Поведение |
|-----------|--------|-----------|
| AlertService | Telegram API timeout | Retry 3x с back-off, лог ошибки |
| AsterDEX WS | Connection lost | Reconnect с exponential back-off |
| ArbitrageEngine | Order rejected | Cancel opposite leg, log, return to idle |
| ArbitrageEngine | One leg filled, other pending | Wait max_pending_sec, then cancel + close filled leg |
| CrossSpreadStore | SQLite write error | Log error, skip snapshot, retry next cycle |

## Security

- `bot_token` маскируется в API-ответах (показываются только последние 4 символа)
- Все торговые эндпоинты защищены `ADMIN_TOKEN` (существующий механизм)
- API-ключи бирж хранятся только в переменных окружения
- Kill switch доступен через API и автоматически активируется при критических ошибках (3+ последовательных ошибки ордеров)


## Correctness Properties

### Property 1: Rate Limiting Invariant
AlertService never sends more than 1 message per (alert_type, symbol) pair within `rate_limit_sec` seconds.
**Validates: Requirements 1.7**

### Property 2: Kill Switch Guarantee
When kill_switch is activated, ArbitrageEngine opens no new positions and closes all existing positions within one loop iteration.
**Validates: Requirements 4.7**

### Property 3: One-Leg Protection
If one leg of an arbitrage trade is filled and the other is not filled within `max_pending_sec`, the unfilled order is cancelled and the filled leg is closed.
**Validates: Requirements 4.10**

### Property 4: Exchange Switching Safety
CaptureSettings.exchange cannot be changed while a position is open — update_settings rejects the change.
**Validates: Requirements 3.7**

### Property 5: Spread Buffer Prefix Consistency
All AsterDEX ticks are stored with `ASTER:` prefix; MEXC spot ticks use bare symbol; MEXC futures use `SYMBOL` format. No prefix collision occurs.
**Validates: Requirements 2.2**

### Property 6: Cross-Spread Retention
CrossSpreadWorker deletes all records older than `retention_days` on each cycle, ensuring bounded storage growth.
**Validates: Requirements 5.4**

### Property 7: Token Masking
AlertConfig.bot_token is never returned in full via API responses — only the last 4 characters are visible.
**Validates: Requirements 6.4**

### Property 8: Concurrent Position Limit
ArbitrageEngine never opens more than `max_concurrent_trades` positions simultaneously.
**Validates: Requirements 4.6**

### Property 9: WebSocket Reconnection
AsterWebSocketClient reconnects with exponential back-off (1s base, 60s max) and never exceeds the max delay.
**Validates: Requirements 2.4**

### Property 10: Data Consistency
CrossSpreadSnapshot is only written when both MEXC and AsterDEX ticks are available for a symbol — no partial snapshots.
**Validates: Requirements 5.2**
**Validates: Requirement 5.2**

## Testing Strategy

### Unit Tests
- **AlertService:** Test rate limiting logic, config masking, retry behavior with mocked Telegram API.
- **ArbitrageEngine:** Test entry/exit logic, kill switch, one-leg protection, concurrent position limits with mocked adapters.
- **AsterWebSocketClient:** Test message parsing, subscription management, reconnection logic with mocked WebSocket.
- **CrossSpreadWorker:** Test snapshot creation, retention cleanup, downsampling with in-memory SQLite.
- **ExchangeAdapters:** Test each adapter's order placement/cancellation with mocked HTTP clients.

### Integration Tests
- **End-to-end arbitrage flow:** Simulate ticks on both exchanges, verify position lifecycle from entry to exit.
- **Alert integration:** Verify SpreadCaptureEngine triggers AlertService on trade events.
- **API endpoints:** Test all REST endpoints with FastAPI TestClient, verify auth, validation, and response formats.
- **WebSocket lifecycle:** Test connect → subscribe → receive ticks → unsubscribe → disconnect flow.

### Property-Based Tests
- Rate limiter never allows messages faster than configured interval (fuzz with random timestamps).
- Arbitrage PNL calculation is consistent: net_pnl = gross_pnl - fees for all generated trade records.
- Cross-spread basis_bps calculation matches manual formula for random price inputs.

### Manual / Exploratory
- Paper mode arbitrage with live market data to validate spread detection accuracy.
- Telegram alert delivery under various network conditions.
- Frontend panel usability and real-time data updates.
