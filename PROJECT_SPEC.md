# Краткая спецификация проекта MEXC Spread Monitor

## 1. Общее назначение

**MEXC Spread Monitor** — это многофункциональная торгово-аналитическая платформа для сканирования спредов, арбитража и автоматизации торговли на криптовалютных биржах. Проект изначально создавался как монитор спредов (spot/futures) на MEXC, но эволюционировал в мультибиржевой терминал с несколькими торговыми стратегиями, современным UI и автоторговлей.

**Ключевые возможности:**
- Мониторинг bid/ask спредов в реальном времени (спот, фьючерсы, кросс-маркет базис)
- Оценка чистого спреда с учётом taker-комиссий
- Анализ ликвидности L1/L2 (объёмы, VWAP, проскальзывание)
- Межбиржевой арбитраж (MEXC ↔ AsterDEX и другие биржи)
- Cash-and-carry / Reverse cash-and-carry арбитраж спот-фьючерс
- Автоматическая торговля (paper/live режимы)
- Алерты и уведомления (Telegram)
- Историческое хранение данных в SQLite

---

## 2. Технический стек

| Компонент | Технология |
|-----------|------------|
| **Backend** | Python 3.10+, FastAPI, Uvicorn |
| **Data & API** | httpx, pandas, numpy, SQLAlchemy 2.0 |
| **WebSocket** | websocket-client (push-фиды MEXC) |
| **Frontend** | React 18, TypeScript, Vite 5, Tailwind CSS |
| **Графики** | Lightweight Charts (TradingView) |
| **База данных** | SQLite (история спредов, позиций) |
| **Конфигурация** | JSON + переменные окружения |
| **Сборка** | setuptools, pyproject.toml |

---

## 3. Архитектура и структура проекта

```
mexc_spread_monitor/
├── backend/main.py                 # FastAPI сервер (REST API)
├── frontend/                       # React + Vite SPA
│   ├── src/App.tsx                 # Главный компонент
│   ├── src/filters.ts              # Клиентская фильтрация (зеркало Python)
│   └── ...                         # Модальные окна, графики, таблицы
├── config/
│   ├── external_apis.json          # URL MEXC, retry, WS, execution model
│   └── futures_arb.json            # Настройки спот-фьючерс арбитража
├── docs/                           # Документация (ARCHITECTURE, BUSINESS, TRADING)
├── mexc_monitor/                   # Ядро бизнес-логики
│   ├── config.py                   # Settings dataclass + загрузка JSON/env
│   ├── models.py                   # BookTickerRow, CrossSpreadRow
│   ├── client.py                   # HTTP клиент MEXC (REST + retry/backoff)
│   ├── pipeline.py                 # Оркестрация: load_snapshot → DataFrame
│   ├── filters.py                  # Локальная фильтрация снимка
│   ├── execution.py                # Net spread, L1 оценка, VWAP
│   ├── metrics.py                  # Расчёт mid, spread bps, комиссий
│   ├── cross_market.py             # Сопоставление спот ↔ фьючерс (базис)
│   ├── history_store.py            # ORM SQLAlchemy для SQLite
│   ├── history_worker.py           # Фоновый сбор истории
│   ├── klines.py                   # Свечи MEXC spot/futures
│   ├── vwap.py                     # Расчёт исполнимого объёма по глубине стакана
│   ├── freshness.py                # Оценка свежести тиков
│   ├── ws_futures.py               # WebSocket фьючерсных тикеров
│   ├── ws_spot_orderbook.py        # WebSocket стакан спота
│   ├── ws_futures_orderbook.py     # WebSocket стакан фьючерсов
│   ├── trading/                    # Торговый контур
│   │   ├── engine.py               # TradingEngine (paper/live)
│   │   ├── private_client.py       # Приватный API MEXC (HMAC)
│   │   ├── risk.py                 # Риск-ограничения
│   │   ├── exchanges.py            # Enum бирж/рынков
│   │   └── clients/                # Клиенты других бирж (Binance, Bybit, OKX...)
│   ├── arbitrage/                  # Межбиржевой арбитраж
│   │   ├── engine.py               # ArbitrageEngine (MEXC ↔ AsterDEX)
│   │   ├── models.py               # ArbPosition, ArbSettings
│   │   └── adapters.py             # Адаптеры к PortfolioRiskManager
│   ├── futures_arb/                # Спот-фьючерс арбитраж
│   │   ├── strategy_engine.py      # FuturesArbStrategyEngine (C&C, Funding)
│   │   ├── basis_calculator.py     # Расчёт базиса
│   │   ├── funding_tracker.py      # История и z-score funding
│   │   ├── position_manager.py     # Управление позициями
│   │   └── risk_controller.py      # Лимиты и kill switch
│   ├── lead_lag/                   # Lead-lag анализ между биржами
│   ├── alerts/                     # Telegram-алерты
│   ├── network/                    # DNS resolver, custom transport
│   └── orm/                        # SQLAlchemy модели
├── tests/                          # Pytest тесты
├── run_modern.bat                  # Запуск FastAPI + Vite
└── pyproject.toml                  # Зависимости Python
```

---

## 4. Основные модули ядра

### 4.1. Data Pipeline (`pipeline.py`, `client.py`)
- **Вход:** публичные REST API MEXC (`bookTicker`, `ticker/24hr`, `contract/ticker`)
- **Оптимизация:** два запроса в одной HTTP-сессии, pacing, retry с экспоненциальным backoff
- **Опционально:** WebSocket-фиды для фьючерсов и стаканов (снижение задержек, обход 403)
- **Выход:** `list[BookTickerRow]` → `pd.DataFrame` с меткой `observed_at`

### 4.2. Модель исполнения (`execution.py`, `metrics.py`)
- Расчёт `mid`, `spread_abs`, `spread_bps`
- Модель taker-комиссий round-trip → `net_spread_bps`
- Оценка L1: `l1_max_executable_base`, `l1_max_notional_quote`
- VWAP-оценка по глубине стакана: проскальзывание market-order на заданный notional

### 4.3. Кросс-маркет (`cross_market.py`)
- Параллельная загрузка спот + фьючерсы
- Сопоставление `BTCUSDT ↔ BTC_USDT`
- Метрики базиса: `basis_mid_abs`, `basis_mid_bps`

### 4.4. WebSocket-фиды
- `ws_futures.py` — push-все тикеры фьючерсов (`sub.tickers`)
- `ws_spot_orderbook.py` — L1 обновления спотового стакана
- `ws_futures_orderbook.py` — L1 обновления фьючерсного стакана
- Fallback на REST при протухании или ошибках handshake

---

## 5. Торговые стратегии и движки

### 5.1. TradingEngine (`trading/engine.py`)
**Простая стратегия захвата спреда на одном рынке.**
- **Режимы:** `paper` (логирование в JSONL) / `live` (реальные ордера через MEXC API)
- **Сигнал:** `net_spread_bps >= threshold`
- **Ордера:** LIMIT BUY (или MARKET) по настраиваемым правилам
- **Риск-контур:** лимит ордеров в сутки, max открытых, max ошибок подряд, kill switch
- **API:** `/api/trading/start`, `/stop`, `/kill-switch`, `/run-once`, `/status`

### 5.2. ArbitrageEngine (`arbitrage/engine.py`)
**Межбиржевой арбитраж между MEXC и AsterDEX.**
- Оценка executable spread с учётом комиссий обеих бирж
- Двуногие позиции: BUY на дешёвой, SELL на дорогой
- One-leg protection: отмена и разворот при частичном исполнении
- Состояние сериализуется на диск (`state_store.py`)
- Paper/live режимы с ExecutionSimulator (Poisson-fill)

### 5.3. FuturesArbStrategyEngine (`futures_arb/strategy_engine.py`)
**Спот-фьючерс арбитраж (cash-and-carry, reverse, funding).**
- **Cash-and-Carry:** long spot + short perp при положительном базисе
- **Reverse C&C:** short spot + long perp при отрицательном базисе
- **Funding Arbitrage:** вход по z-score funding rate (|z| ≥ 2.0)
- **Выход:** схождение базиса, таймаут, stop-loss, target profit
- **Риск:** max exposure, max позиций на символ, проверка spot-баланса

### 5.4. PortfolioRiskManager (`portfolio_risk.py`)
- Агрегированный риск-контроль над всеми движками
- Общий exposure, kill switch, мониторинг открытых позиций

---

## 6. Пользовательские интерфейсы

### 6.1. React SPA (`frontend/`)
- Полноценный терминал с 10+ страницами
- Spread Monitor, MultiExchange, Arbitrage, FuturesArb, SpreadCapture, LeadLag, History, Trading, Alerts
- Графики свечей и спредов на Lightweight Charts
- Мини-графики (sparklines) в таблицах
- Карточки сделок, стаканы, калькулятор объёма
- Интеграция с FastAPI через прокси Vite (`/api/*`)

### 6.2. REST API (`backend/main.py`)
- `GET /api/health` — состояние системы
- `GET /api/snapshot?market=spot|futures|cross` — снимок рынка (с кэшем)
- `GET /api/depth` — стакан L2 с VWAP-оценкой
- `GET /api/klines` / `/api/klines/batch` — свечи (multi-exchange)
- `GET /api/history/recent` — история из SQLite
- `POST /api/trading/*` — управление торговыми движками
- `/api/admin-token` — аутентификация для торговых endpoint'ов

---

## 7. Конфигурация

### 7.1. Файлы конфигурации
- **`config/external_apis.json`** — URL MEXC, retry, WS-настройки, whitelist/blacklist, execution model
- **`config/futures_arb.json`** — пороги базиса, плечо, лимиты exposure

### 7.2. Переменные окружения (ключевые)

| Переменная | Назначение |
|------------|------------|
| `MEXC_API_KEY` / `MEXC_API_SECRET` | Приватный API MEXC (для live-торговли) |
| `MEXC_TRADING_ENABLED` | Автостарт TradingEngine |
| `MEXC_TRADING_MODE` | `paper` или `live` |
| `MEXC_TRADING_SYMBOL` | Символ для TradingEngine |
| `MEXC_TRADING_KILL_SWITCH` | Начальное состояние аварийной остановки |
| `MEXC_SNAPSHOT_CACHE_TTL_SEC` | TTL кэша снимков на backend |
| `ADMIN_TOKEN` | Токен для защиты торговых API |

---

## 8. Поток данных (упрощённо)

```
MEXC REST/WebSocket + 7 бирж WS
        ↓
   client.py / ws_*.py
        ↓
  pipeline.py (load_snapshot)
        ↓
  execution.py (net spread, L1, VWAP)
        ↓
   ┌──────────────┬──────────────┐
   ↓              ↓              ↓
backend/main.py   history_worker   trading engines
  (FastAPI)        (SQLite)
     ↓
frontend (React)
```

---

## 9. Текущее состояние и планы

### Реализовано
- ✅ Полноценный монитор спредов (spot + futures + cross)
- ✅ Net spread с моделью комиссий
- ✅ WebSocket-фиды для снижения задержек
- ✅ История в SQLite
- ✅ UI: React/FastAPI (11 страниц)
- ✅ Три торговых движка (spread capture, cross-exchange arb, futures arb)
- ✅ Paper/live режимы, kill switch, риск-контроль
- ✅ Мультибиржевая поддержка (Binance, Bybit, OKX, Gate.io, HTX, Bitget, dYdX, Hyperliquid, AsterDEX, Lighter)

### В разработке / Roadmap
- 🔴 Унификация UI: единый дизайн-система, тёмная тема, sticky-таблицы
- 🔴 Карточка сделки (единый экран: спред + стаканы + график + калькулятор)
- 🔴 Алерты по клику на ячейку
- 🔴 Метрики lifetime спреда и исполнимого объёма
- 🔴 Единая формула net-spread для всех стратегий
- 🟡 Backtest на кросс-спредах
- 🟡 Kelly/риск-бюджет для позиционирования
- 🟢 Глобальный поиск по `/`

---

## 10. Ограничения и дисклеймер

- Данные публичные, без гарантий со стороны биржи
- Снимки точечные: рынок меняется между опросами
- L1-оценка — верхняя граница, не факт исполнения
- Чистый спред — модельная величина, не гарантированная маржа
- Перед live-торговлей обязателен прогон в paper-режиме

---

*Документ составлен на основе анализа исходного кода, документации `docs/` и roadmap проекта.*
