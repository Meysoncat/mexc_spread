# Деплой (Docker / VPS)

Продакшн-запуск на постоянном хосте. Один Docker-образ содержит и API, и UI:
FastAPI отдаёт собранный React (`/assets` + SPA-fallback на `index.html`), поэтому
и интерфейс, и `/api/*` работают с одного origin на порту **8006** — без CORS и
отдельного веб-сервера.

> **Почему не Vercel/serverless.** Бэкенд stateful: держит долгоживущие
> WebSocket-фиды к биржам и пишет историю в SQLite. Ему нужен процесс, который
> живёт 24/7 и локальный диск — это VPS/Docker, а не serverless-функции.

## Требования

- Docker Engine 24+ и Docker Compose v2 (`docker compose`, не `docker-compose`).
- ~1 ГБ RAM, 1 vCPU для базовой нагрузки (лимит памяти в compose — 1g, правьте под себя).
- Хост в регионе, из которого доступны нужные биржи (иначе — прокси, см. ниже).

## Быстрый старт

```bash
# 1. Настроить окружение
cp .env.docker.example .env
# сгенерировать токен и вписать в .env как ADMIN_TOKEN=...
openssl rand -base64 32

# 2. Собрать и запустить
docker compose up -d --build

# 3. Проверить
curl -fsS http://localhost:8006/api/health     # {"status":"ok",...}
docker compose logs -f                          # следить за логами
```

UI: `http://<host>:8006/`. API: `http://<host>:8006/api/health`.

Остановить: `docker compose down` (данные сохраняются в volume `app-data`).

## Что где хранится

| Путь в контейнере | Что это | Персистентность |
|---|---|---|
| `/app/data` | SQLite-базы (история, lead-lag, basis), JSONL-логи торговли, capture-state | volume `app-data` |
| `/app/config` | JSON-конфиги (пары, комиссии, screener, сеть) | bind-mount `./config` (ro) |
| `/app/frontend/dist` | собранный UI | в образе |

Правки в `./config/*.json` подхватываются перезапуском (`docker compose restart`)
без пересборки образа.

## Переменные окружения

Полный список — в `.env.docker.example`. Ключевые:

| Переменная | Обяз. | Назначение |
|---|---|---|
| `ADMIN_TOKEN` | **да** | Токен для admin/trading-эндпоинтов. Без него приложение попыталось бы записать сгенерированный токен в `/app/.env`, а ФС образа read-only → задайте явно. |
| `APP_PORT` | нет | Порт публикации на хосте (в контейнере всегда 8006). |
| `MEXC_HTTP_PROXY` | нет | Общий egress-прокси для геоблокированных REST (`http(s)://` или `socks5h://`). |
| `MEXC_HTTP_PROXY_<БИРЖА>` | нет | Прокси для конкретной биржи; `direct` — принудительно напрямую. |

## Гео-доступ к биржам

Из некоторых регионов часть REST геоблокируется (Binance `451`, MEXC market-data,
Bybit CloudFront `403`). Проверить фактическую доступность после старта — страница
**Сеть / Прокси** (`/network`) или:

```bash
curl "http://localhost:8006/api/network/test?exchange=binance"
```

Если биржи недоступны напрямую — поднимите egress-прокси в неблокируемом регионе и
задайте `MEXC_HTTP_PROXY[_<БИРЖА>]`. Для VLESS-прокси используйте Xray-мост
(`scripts/xray_bridge.py`, см. `docs/PROXY_XRAY.md`), который превращает VLESS в
локальный SOCKS5. Из контейнера мост на хосте доступен как
`socks5h://host.docker.internal:10808` (на Linux добавьте в сервис
`extra_hosts: ["host.docker.internal:host-gateway"]`).

> Прокси покрывает только REST/HTTP. WS-фиды (в т.ч. L2-глубина MEXC/OKX/Bybit)
> идут напрямую — они не геоблокируются, ради чего и сделаны.

## Reverse proxy и HTTPS (опционально)

Для публичного домена с TLS поставьте перед контейнером Caddy/Nginx/Traefik и
привяжите порт к localhost. Пример: в `docker-compose.yml` замените публикацию порта
на `"127.0.0.1:8006:8006"` и проксируйте на него. Приложение отдаёт всё с одного
origin, поэтому спец-настроек проксирования не нужно — обычный `proxy_pass`.

## Обновление

```bash
git pull
docker compose up -d --build      # пересобрать и перезапустить
docker image prune -f             # подчистить старые слои
```

Данные в volume `app-data` при пересборке не теряются.

## Диагностика

| Симптом | Действие |
|---|---|
| Контейнер `unhealthy` | `docker compose logs app` — смотреть трейс старта; healthcheck бьёт `/api/health`. |
| Пустые данные по биржам | Проверить гео-доступ (`/api/network/test`), при необходимости настроить прокси. |
| `401` на admin/trading | Убедиться, что `ADMIN_TOKEN` задан в `.env` и совпадает с тем, что вводится в UI. |
| Потеря истории после `down` | Проверить, что volume `app-data` не удалён (`docker volume ls`). |

## Проверено

Single-image архитектура проверена без Docker (Docker в CI-песочнице недоступен):
фронт собирается в `frontend/dist`; рантайм-команда образа
`uvicorn backend.main:app --host 0.0.0.0 --port 8006` отдаёт `/api/health` (200),
собранный `index.html` на `/`, SPA-fallback на клиентских маршрутах (`/density` → 200),
статику `/assets/*` с корректным content-type; при заданном `ADMIN_TOKEN` запись
в `.env` не происходит (совместимо с read-only ФС образа).
