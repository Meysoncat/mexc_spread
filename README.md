# MEXC Spread Monitor

Спот и фьючерсы MEXC: bid/ask, спред (bps), **оценка чистого спреда** (модель taker-комиссий), **L1-объём**, объёмы 24h, funding, whitelist/blacklist пар, опциональная **история в SQLite** (SQLAlchemy), фильтры, CSV, автообновление.

## Два интерфейса

| Вариант | Стек | Запуск |
|--------|------|--------|
| **Классический** | Streamlit | `run_app.bat` → [http://localhost:8501](http://localhost:8501) |
| **Современный UI** | FastAPI + React + Vite + TypeScript + Tailwind | `run_modern.bat` → [http://localhost:5173](http://localhost:5173) (нужен [Node.js](https://nodejs.org/) LTS) |

Бизнес-логика общая: пакет `mexc_monitor`, фильтры в `mexc_monitor/filters.py` (Streamlit вызывает их из Python; веб-UI дублирует правила в `frontend/src/filters.ts` для мгновенной фильтрации в браузере).

## Документация

- **[Бизнес-процессы и трейдерская логика](docs/BUSINESS.md)** — что измеряется, метрики, ограничения, типовые сценарии (без углубления в код).
- **[Архитектура](docs/ARCHITECTURE.md)** — модули, конфигурация, REST/WebSocket, ORM, API, современный стек.
- **[Автоторговля: процесс и функционал](docs/TRADING.md)** — режимы `paper/live`, риск-ограничения, env-параметры, API управления и порядок запуска.
- **[Запуск](docs/ZAPUSK.md)** — Streamlit, FastAPI+Vite, типичные ошибки.

## Быстрый старт

- Только Python: `run_app.bat`
- Современный интерфейс: установите Node.js, затем `run_modern.bat` — **одно окно** консоли для API и Vite (или вручную: `npm run dev:modern` из корня проекта).
"# mexc_spread" 
