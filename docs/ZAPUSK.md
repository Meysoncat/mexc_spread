# Запуск

## Требования

- Python 3.10+.
- Node.js LTS (чтобы собрать UI).
- Доступ в интернет к API бирж. Где биржа недоступна (гео-блок, 403), её задачи просто падают с backoff, остальные биржи работают. Посмотреть, что живо: `/api/v1/health`.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
```

## Запуск

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

UI и API работают на одном порту: http://127.0.0.1:8000 (собранный `frontend/dist` раздаёт бэкенд).

Режим разработки фронтенда с hot reload:

```bash
npm install            # в корне — пакет concurrently
npm run dev:modern     # API :8000 + Vite :5173 в одном окне
```

На Windows: `run_modern.bat` сам создаёт venv, ставит зависимости и запускает оба процесса.

### Демо без бирж

```bash
python scripts/demo_market.py --port 8000
```

Синтетический рынок с расхождениями между биржами, разным funding и
пробоями. Подходит, чтобы проверить UI и своего потребителя сетапов.

## Настройка

| Что | Где |
|---|---|
| Комиссии и пороги детекторов | `config/setups.json` (или путь в `SETUPS_CONFIG`) |
| Ключ доступа к `/api/v1` | `SCREENER_API_KEY` |
| Выключить генератор (оставить Spread Monitor) | `SCREENER_ENABLED=0` |
| URL и таймауты API MEXC для Spread Monitor | `config/external_apis.json` |
| API на другом хосте для UI | `frontend/.env`: `VITE_API_BASE_URL=http://host:8000` |

Обязательно впишите в `config/setups.json` свои комиссии (`perp_taker_bps`,
`spot_taker_bps`). По умолчанию стоит базовый тариф, и edge может быть
занижен.

Если задан `SCREENER_API_KEY`, а UI открыт с того же хоста, сохраните ключ в
браузере: в консоли выполните `localStorage.setItem("screener-api-key", "<ключ>")`.

## Проверки

```bash
python -m pytest tests -q
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```

## Типичные проблемы

| Симптом | Причина и решение |
|---|---|
| `/api/v1/health` → `venues_live: []` | Нет доступа к биржам (VPN, прокси, гео-блок). Ошибки по задачам видны в `hub.jobs` |
| Сетапов нет минуту-две после старта | Нормально. Справочники и funding грузятся за 30–60 с, базлайны прогреваются 10–15 мин, свечам нужна история |
| Мало направленных идей | Их строят только `kline_venues` для топ-`kline_universe` монет. Пороги — в `config/setups.json` |
| `l1_qty: null` у MEXC | Тикер MEXC не отдаёт объём на лучшей цене; он догружается по стакану только для кандидатов |
| UI пустой на :8000 | Не собран фронтенд: `cd frontend && npm run build` |
