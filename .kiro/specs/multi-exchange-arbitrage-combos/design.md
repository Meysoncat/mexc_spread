# Design Document: Multi-Exchange Arbitrage Combos

## Overview

Расширение модуля межбиржевого арбитража для поддержки всех комбинаций из 8 бирж (28 уникальных пар). Архитектура строится вокруг `ArbitrageEngineRegistry` — реестра, управляющего независимыми экземплярами `ArbitrageEngine` для каждой пары бирж. Каждый движок работает в отдельном потоке, имеет собственную конфигурацию и изолированное состояние позиций.

## Architecture

### High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI Backend                            │
│                                                                   │
│  ┌──────────────────────┐    ┌─────────────────────────────┐    │
│  │  REST API Endpoints  │───▶│  ArbitrageEngineRegistry     │    │
│  │  /api/arbitrage/*    │    │                               │    │
│  └──────────────────────┘    │  ┌─────────┐ ┌─────────┐    │    │
│                               │  │Engine(A↔B)│ │Engine(A↔C)│ ...│    │
│                               │  └─────────┘ └─────────┘    │    │
│                               └──────────────┬───────────────┘    │
│                                              │                    │
│  ┌──────────────────────┐    ┌──────────────▼───────────────┐    │
│  │  ConfigPersistence   │◀──▶│  ExchangeAdapterFactory       │    │
│  │  config/arb_pairs.json│    │  (8 adapters via BasePrivate) │    │
│  └──────────────────────┘    └──────────────┬───────────────┘    │
│                                              │                    │
│                               ┌──────────────▼───────────────┐    │
│                               │       SpreadBuffer            │    │
│                               │  (shared, thread-safe)        │    │
│                               └───────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     React Frontend                                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  MultiPairArbitragePage                                     │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐   │  │
│  │  │ExchangeSelect│  │PairGrid      │  │AggregatedStats │   │  │
│  │  │(multi-select)│  │(toggle/config)│  │(summary)       │   │  │
│  │  └──────────────┘  └──────────────┘  └────────────────┘   │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Concurrency Model

Каждый `ArbitrageEngine` запускается в отдельном daemon-потоке (thread-per-engine). Общие ресурсы (`SpreadBuffer`, логирование) уже thread-safe по текущей реализации. `ArbitrageEngineRegistry` использует `threading.Lock` для защиты словаря движков.

```
Main Thread (FastAPI)
  │
  ├── Thread: arb-engine-binance_bybit
  ├── Thread: arb-engine-binance_gateio
  ├── Thread: arb-engine-binance_mexc
  ├── ...
  └── Thread: arb-engine-okx_mexc
```

## Components and Interfaces

### 1. ExchangePair (Value Object)

```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True, order=True)
class ExchangePair:
    """Нормализованная пара бирж (лексикографический порядок)."""
    exchange_a: str  # всегда <= exchange_b лексикографически
    exchange_b: str

    def __post_init__(self) -> None:
        if self.exchange_a > self.exchange_b:
            object.__setattr__(self, "exchange_a", self.exchange_b)
            object.__setattr__(self, "exchange_b", self.exchange_a)

    @property
    def pair_id(self) -> str:
        """Строковый идентификатор: 'binance_mexc'."""
        return f"{self.exchange_a}_{self.exchange_b}"

    @classmethod
    def from_pair_id(cls, pair_id: str) -> ExchangePair:
        parts = pair_id.split("_", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid pair_id: {pair_id}")
        return cls(exchange_a=parts[0], exchange_b=parts[1])
```

### 2. PairConfig (Data Model)

```python
from dataclasses import dataclass, field
from typing import Literal

ArbMode = Literal["paper", "live"]


@dataclass
class PairConfig:
    """Независимая конфигурация для одной пары бирж."""
    enabled: bool = False
    mode: ArbMode = "paper"
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    entry_threshold_bps: float = 15.0
    exit_threshold_bps: float = 3.0
    max_position_notional_usdt: float = 500.0
    max_concurrent_trades: int = 3
    max_pending_sec: float = 10.0
    max_hold_sec: float = 600.0
    kill_switch: bool = True
    loop_interval_sec: float = 0.5
    fee_a_taker_bps: float = 2.0  # комиссия exchange_a
    fee_b_taker_bps: float = 2.0  # комиссия exchange_b

    @classmethod
    def from_global_config(cls, global_cfg: dict) -> PairConfig:
        """Создать дефолтный конфиг из глобальных настроек арбитража."""
        return cls(
            entry_threshold_bps=global_cfg.get("entry_threshold_bps", 15.0),
            exit_threshold_bps=global_cfg.get("exit_threshold_bps", 3.0),
            max_position_notional_usdt=global_cfg.get("max_position_notional_usdt", 500.0),
            max_concurrent_trades=global_cfg.get("max_concurrent_trades", 3),
            max_hold_sec=global_cfg.get("max_hold_sec", 600.0),
            symbols=global_cfg.get("symbols", ["BTCUSDT", "ETHUSDT"]),
        )
```

### 3. PositionKey (Value Object)

```python
@dataclass(frozen=True, slots=True)
class PositionKey:
    """Уникальный идентификатор позиции: (symbol, exchange_pair)."""
    symbol: str
    pair_id: str  # ExchangePair.pair_id

    def __str__(self) -> str:
        return f"{self.symbol}@{self.pair_id}"
```

### 4. ArbitrageEngineRegistry (Core Component)

```python
import threading
from typing import Any


class ArbitrageEngineRegistry:
    """Реестр и менеджер всех ArbitrageEngine экземпляров."""

    SUPPORTED_EXCHANGES: list[str] = [
        "asterdex", "binance", "bitget", "bybit",
        "gateio", "htx", "mexc", "okx",
    ]

    def __init__(self, config_path: str = "config/arb_pairs.json"):
        self._engines: dict[str, ArbitrageEngine] = {}  # pair_id → engine
        self._configs: dict[str, PairConfig] = {}       # pair_id → config
        self._lock = threading.Lock()
        self._config_path = config_path
        self._adapter_factory = ExchangeAdapterFactory()

    def get_available_exchanges(self) -> list[dict[str, Any]]:
        """Список бирж с их доступностью (наличие credentials)."""
        ...

    def generate_pairs(self, exchanges: list[str]) -> list[ExchangePair]:
        """Генерация всех уникальных пар из выбранных бирж."""
        ...

    def activate_pair(self, pair: ExchangePair, config: PairConfig | None = None) -> None:
        """Создать и запустить движок для пары."""
        ...

    def deactivate_pair(self, pair_id: str) -> None:
        """Остановить и удалить движок."""
        ...

    def start_all(self) -> None:
        """Запустить все активные движки."""
        ...

    def stop_all(self) -> None:
        """Остановить все движки."""
        ...

    def get_pair_status(self, pair_id: str) -> dict[str, Any]:
        """Статус конкретного движка."""
        ...

    def get_aggregated_status(self) -> dict[str, Any]:
        """Агрегированная статистика по всем движкам."""
        ...

    def update_pair_config(self, pair_id: str, patch: dict[str, Any]) -> None:
        """Обновить конфигурацию пары без перезапуска других."""
        ...

    def persist(self) -> None:
        """Сохранить текущее состояние в JSON."""
        ...

    def restore(self) -> None:
        """Восстановить состояние из JSON при старте."""
        ...
```

### 5. ExchangeAdapterFactory

```python
class ExchangeAdapterFactory:
    """Фабрика адаптеров для всех поддерживаемых бирж."""

    def create_adapter(self, exchange: str) -> ExchangeAdapter:
        """Создать адаптер для биржи, используя BasePrivateClient."""
        ...

    def is_configured(self, exchange: str) -> bool:
        """Проверить наличие API credentials для биржи."""
        ...
```

Каждый адаптер реализует протокол `ExchangeAdapter` и использует `BasePrivateClient` (из `mexc_monitor/trading/private_client.py` или аналогичный для каждой биржи) для аутентификации:

```python
class BinanceAdapter:
    """Адаптер Binance Futures через BasePrivateClient."""

    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret

    @property
    def exchange_name(self) -> str:
        return "binance"

    def get_spread_buffer_key(self, symbol: str) -> str:
        return f"BINANCE:{symbol.upper()}"

    def place_market_order(self, *, symbol, side, quantity, client_order_id=None):
        # Использует BasePrivateClient с HMAC SHA256 подписью
        ...
```

### 6. Modified ArbitrageEngine

Существующий `ArbitrageEngine` расширяется для работы с произвольной парой бирж:

```python
class ArbitrageEngine:
    """Движок арбитража для конкретной пары бирж."""

    def __init__(
        self,
        pair: ExchangePair,
        config: PairConfig,
        adapter_a: ExchangeAdapter,
        adapter_b: ExchangeAdapter,
    ):
        self._pair = pair
        self._config = config
        self._adapter_a = adapter_a
        self._adapter_b = adapter_b
        # Позиции ключатся по PositionKey
        self._positions: dict[PositionKey, ArbPosition] = {}
        ...
```

Ключевые изменения:
- Позиции хранятся по `PositionKey` вместо просто `symbol`
- Адаптеры передаются при создании (не хардкодятся MEXC/Aster)
- Комиссии берутся из `PairConfig.fee_a_taker_bps` / `fee_b_taker_bps`
- `get_spread_buffer_key` вызывается на адаптерах для получения тиков

### 7. REST API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/arbitrage/pairs` | Список всех пар со статусом и конфигом |
| POST | `/api/arbitrage/pairs/{pair_id}/start` | Запустить движок пары |
| POST | `/api/arbitrage/pairs/{pair_id}/stop` | Остановить движок пары |
| PATCH | `/api/arbitrage/pairs/{pair_id}/settings` | Обновить конфиг пары |
| GET | `/api/arbitrage/summary` | Агрегированная статистика |
| POST | `/api/arbitrage/exchanges` | Обновить выбранные биржи, перегенерировать пары |

### Response Schemas

```python
# GET /api/arbitrage/pairs
{
    "ok": True,
    "pairs": [
        {
            "pair_id": "binance_mexc",
            "exchange_a": "binance",
            "exchange_b": "mexc",
            "running": True,
            "config": { ... },  # PairConfig as dict
            "stats": { ... },   # ArbStats as dict
            "open_positions": 2,
        },
        ...
    ],
    "total_pairs": 28,
    "active_pairs": 5,
}

# GET /api/arbitrage/summary
{
    "ok": True,
    "total_engines": 5,
    "running_engines": 3,
    "total_trades": 42,
    "net_pnl_usdt": 12.34,
    "open_positions": 4,
    "per_pair": [
        {"pair_id": "binance_mexc", "net_pnl_usdt": 5.0, "open_positions": 1, "running": True},
        ...
    ],
}

# POST /api/arbitrage/exchanges
# Request body:
{
    "exchanges": ["binance", "mexc", "bybit", "okx"]
}
# Response:
{
    "ok": True,
    "generated_pairs": 6,
    "pairs": ["binance_bybit", "binance_mexc", "binance_okx", "bybit_mexc", "bybit_okx", "mexc_okx"],
}
```

## Data Models

### Configuration File: `config/arb_pairs.json`

```json
{
  "_comment": "Multi-exchange arbitrage pairs configuration",
  "selected_exchanges": ["binance", "mexc", "bybit", "okx"],
  "pairs": {
    "binance_mexc": {
      "enabled": true,
      "mode": "paper",
      "symbols": ["BTCUSDT", "ETHUSDT"],
      "entry_threshold_bps": 15.0,
      "exit_threshold_bps": 3.0,
      "max_position_notional_usdt": 500.0,
      "max_concurrent_trades": 3,
      "max_pending_sec": 10.0,
      "max_hold_sec": 600.0,
      "kill_switch": true,
      "loop_interval_sec": 0.5,
      "fee_a_taker_bps": 1.0,
      "fee_b_taker_bps": 1.0
    },
    "binance_bybit": {
      "enabled": false,
      "mode": "paper",
      "symbols": ["BTCUSDT"],
      "entry_threshold_bps": 10.0,
      "exit_threshold_bps": 2.0,
      "max_position_notional_usdt": 300.0,
      "max_concurrent_trades": 2,
      "max_pending_sec": 10.0,
      "max_hold_sec": 300.0,
      "kill_switch": true,
      "loop_interval_sec": 1.0,
      "fee_a_taker_bps": 1.0,
      "fee_b_taker_bps": 1.5
    }
  }
}
```

### Spread Buffer Key Convention

Каждая биржа использует уникальный префикс для ключей в `SpreadBuffer`:

| Exchange | Key Format | Example |
|----------|-----------|---------|
| MEXC | `{SYMBOL}` | `BTCUSDT` |
| AsterDEX | `ASTER:{SYMBOL}` | `ASTER:BTCUSDT` |
| Binance | `BINANCE:{SYMBOL}` | `BINANCE:BTCUSDT` |
| Bybit | `BYBIT:{SYMBOL}` | `BYBIT:BTCUSDT` |
| OKX | `OKX:{SYMBOL}` | `OKX:BTCUSDT` |
| Gate.io | `GATEIO:{SYMBOL}` | `GATEIO:BTCUSDT` |
| HTX | `HTX:{SYMBOL}` | `HTX:BTCUSDT` |
| Bitget | `BITGET:{SYMBOL}` | `BITGET:BTCUSDT` |

## Error Handling

### Engine-Level Fault Isolation

```python
def _run_engine_thread(self, pair_id: str) -> None:
    """Обёртка потока движка с fault isolation."""
    engine = self._engines.get(pair_id)
    if engine is None:
        return
    try:
        engine._run_loop()
    except Exception as e:
        logger.error(
            "ArbitrageEngine %s crashed: %s: %s",
            pair_id, type(e).__name__, e,
            exc_info=True,
        )
        # Движок помечается как остановленный, остальные продолжают
        with self._lock:
            engine._stop_event.set()
```

### Configuration Recovery

```python
def restore(self) -> None:
    """Восстановить состояние из JSON."""
    path = Path(self._config_path)
    if not path.is_file():
        logger.warning("Arb pairs config not found: %s. Starting empty.", path)
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Arb pairs config corrupted: %s. Starting empty.", e)
        return
    # Parse and activate pairs from data...
```

### Adapter Unavailability

Если для биржи отсутствуют API credentials, она помечается как `unavailable` и исключается из генерации пар. UI отображает такие биржи серым цветом с подсказкой.

## Testing Strategy

### Property-Based Tests (Hypothesis)

Свойства 1–13 реализуются как property-based тесты с использованием `hypothesis`. Минимум 100 итераций на свойство. Генераторы:
- `st_exchange_subset`: случайное подмножество из 8 бирж (размер 2–8)
- `st_exchange_pair`: случайная нормализованная пара бирж
- `st_pair_config`: случайный валидный `PairConfig` с ограничениями на диапазоны
- `st_symbol`: случайный символ из типичного набора (BTCUSDT, ETHUSDT, etc.)
- `st_global_config`: случайный глобальный конфиг арбитража

### Unit Tests (pytest)

- API endpoints: проверка корректных HTTP-ответов, валидация схем
- Edge cases: 0/1 биржа выбрана, отсутствующий pair_id, corrupted JSON
- Adapter factory: проверка создания всех 8 адаптеров
- UI data contracts: проверка формата данных для фронтенда

### Integration Tests

- Thread safety: запуск нескольких движков, параллельные операции
- Config persistence: запись/чтение файла, recovery после corruption
- Engine lifecycle: start → trade → stop → restart цикл

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Pair generation count equals combinatorial formula

*For any* set of N exchanges (where N ≥ 2) selected from the supported exchanges, the number of generated unique Exchange_Pairs SHALL equal exactly N×(N−1)/2.

**Validates: Requirements 2.1**

### Property 2: Lexicographic normalization is idempotent and order-independent

*For any* two exchange names A and B, creating ExchangePair(A, B) and ExchangePair(B, A) SHALL produce identical pair_id values, and normalizing an already-normalized pair SHALL produce the same result.

**Validates: Requirements 2.2**

### Property 3: Activate/deactivate round-trip preserves registry consistency

*For any* valid ExchangePair, activating it SHALL add exactly one engine to the registry, and subsequently deactivating it SHALL remove that engine, returning the registry to its prior state with all other engines unaffected.

**Validates: Requirements 1.2, 1.3, 1.5**

### Property 4: Engine isolation — operations on one engine do not affect others

*For any* two distinct active engines, performing operations (opening positions, updating config, stopping) on one engine SHALL leave the other engine's positions, statistics, and running state completely unchanged.

**Validates: Requirements 1.5, 4.2, 6.5**

### Property 5: Position key uniqueness by (symbol, exchange_pair)

*For any* symbol and any two distinct Exchange_Pairs, positions opened for that symbol in each pair SHALL be independently tracked and SHALL NOT conflict with or overwrite each other.

**Validates: Requirements 3.1, 3.2**

### Property 6: Max concurrent trades enforcement per engine

*For any* ArbitrageEngine with max_concurrent_trades = N, the number of positions in states "pending_open" or "open" SHALL never exceed N, regardless of how many entry opportunities are presented.

**Validates: Requirements 3.4**

### Property 7: Default config derivation from global configuration

*For any* newly created Exchange_Pair, its PairConfig SHALL contain all required fields (entry_threshold_bps, exit_threshold_bps, max_position_notional_usdt, max_concurrent_trades, max_hold_sec, fee_a_taker_bps, fee_b_taker_bps, mode, enabled) with values derived from the global arbitrage configuration.

**Validates: Requirements 2.3, 4.4**

### Property 8: Unavailable exchanges excluded from pair generation

*For any* set of selected exchanges where some lack API credentials, the generated pairs SHALL only include exchanges that are marked as available (configured), and no pair SHALL contain an unavailable exchange.

**Validates: Requirements 5.3**

### Property 9: Spread buffer key format correctness

*For any* supported exchange and any valid symbol, the adapter's `get_spread_buffer_key(symbol)` SHALL return a string matching the documented key format for that exchange (prefix + symbol in uppercase).

**Validates: Requirements 5.4**

### Property 10: Configuration persistence round-trip

*For any* valid set of active Exchange_Pairs with their PairConfigs, serializing to JSON and deserializing SHALL produce an equivalent set of pairs and configs with all field values preserved.

**Validates: Requirements 4.3, 9.1, 9.2**

### Property 11: Aggregation correctness

*For any* set of active engines with known individual statistics, the aggregated status SHALL report total_trades equal to the sum of individual total_trades, net_pnl_usdt equal to the sum of individual net_pnl_usdt, and open_positions equal to the sum of individual open position counts.

**Validates: Requirements 8.1**

### Property 12: Fault isolation — one engine crash does not affect others

*For any* set of running engines, if one engine raises an unhandled exception, all other engines SHALL continue running with their state unchanged.

**Validates: Requirements 6.3**

### Property 13: Global start/stop affects all engines

*For any* set of active (enabled) engines, calling start_all SHALL result in all engines being in running state, and calling stop_all SHALL result in all engines being in stopped state.

**Validates: Requirements 6.4**
