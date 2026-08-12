# MEXC Spread Monitor

Мультибиржевой терминал мониторинга и арбитража крипто-спредов. Спот и фьючерсы 10+ бирж: bid/ask, спред (bps), **оценка чистого спреда** (модель taker-комиссий), **L1-объём**, объёмы 24h, funding, whitelist/blacklist пар, **история в SQLite**, фильтры, CSV, автообновление.

## Запуск

| Действие | Команда |
|----------|---------|
| **Быстрый старт** | `run_modern.bat` → [http://localhost:5173](http://localhost:5173) |
| Из терминала | `npm run dev:modern` в корне (нужен [Node.js](https://nodejs.org/) LTS) |
| **Продакшн (Docker)** | `cp .env.docker.example .env` → `docker compose up -d --build` → [http://localhost:8006](http://localhost:8006) |

Требования: Python 3.10+, Node.js LTS. Для деплоя — Docker 24+ / Compose v2.

## Документация

- **[Архитектура](docs/ARCHITECTURE.md)** — модули, конфигурация, REST/WebSocket, ORM, API.
- **[Запуск](docs/ZAPUSK.md)** — установка, запуск, типичные ошибки.
- **[Деплой](docs/DEPLOY.md)** — Docker/VPS, single-image, персистентность, прокси, reverse proxy.
- **[Бизнес-процессы](docs/BUSINESS.md)** — метрики, ограничения, трейдерские сценарии.
- **[Автоторговля](docs/TRADING.md)** — режимы paper/live, риск-ограничения, API управления.

## Стек

| Слой | Технологии |
|------|-----------|
| Frontend | React 18, React Router v6, TypeScript, Tailwind CSS, Vite 5, TradingView Lightweight Charts |
| Backend | Python 3.10+, FastAPI, uvicorn, WebSocket-фиды |
| Данные | SQLite (SQLAlchemy ORM), REST API + WS feeds (7 бирж) |

## Подключённые биржи

Binance, OKX, Bybit, Gate.io, Bitget, HTX, dYdX (WS-фиды), MEXC, AsterDEX, Hyperliquid (REST).
