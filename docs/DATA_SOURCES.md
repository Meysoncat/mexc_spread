# Источники данных по биржам

Документ описывает, **через какие публичные API** приложение получает рыночные данные, как разделены REST и WebSocket, откуда берутся сети депозита/вывода для колонки «Переводимо: сеть», и какие ограничения окружения действуют в песочнице/preview.

Связанные документы: [ARCHITECTURE.md](ARCHITECTURE.md) (потоки данных и модули), [ZAPUSK.md](ZAPUSK.md) (запуск).

> **Аутентификация на биржах не используется.** Весь сбор идёт через **публичные** эндпоинты — API-ключи не требуются и не хранятся. Как следствие, приватные данные (реальные балансы, статус вывода по аккаунту) недоступны.

---

## 1. Общая схема

- **REST** — снимки (snapshot) рынка: лучшие bid/ask, 24h-объёмы, funding, а также `klines`/candles для графиков. Используется в Spread Monitor (снимок), Screener, кросс-скринере, хабе монеты.
- **WebSocket** — live-потоки: bookTicker, сделки, стакан (orderbook). Используется в Spread Monitor (live), Lead-Lag, Density Monitor, MetaScalp.
- **Fallback**: для фьючерсов при недоступности WS монитор откатывается на REST-поллинг.
- Все исходящие запросы идут через общий HTTP-клиент `mexc_monitor/http_utils.py` (`mexc_httpx_client`) с ретраями, таймаутами и опциональным прокси.

---

## 2. Основные биржи (spot + futures)

Биржи, участвующие в мониторе, скринере и кросс-скринере.

| Биржа | REST base | Ключевые REST-эндпоинты | WebSocket |
|---|---|---|---|
| **MEXC** | `api.mexc.com` (spot)<br>`contract.mexc.com` (futures) | bookTicker, `ticker/24hr`, klines; contract tickers | `wss://wbs.mexc.com/ws` (spot)<br>`wss://contract.mexc.com/edge` (futures) |
| **Binance** | `api.binance.com` (spot)<br>`fapi.binance.com` (futures) | `/api/v3/ticker/bookTicker`, `/api/v3/ticker/24hr`, `/api/v3/klines`; `/fapi/v1/...` | `wss://stream.binance.com`<br>`wss://fstream.binance.com/ws` |
| **Bybit** | `api.bybit.com` | `/v5/market/tickers`, `/v5/market/kline` | `wss://stream.bybit.com/v5/public/linear` |
| **OKX** | `www.okx.com` | `/api/v5/market/tickers`, `/api/v5/market/candles` | `wss://ws.okx.com` |
| **Gate.io** | `api.gateio.ws` | `/api/v4/spot/tickers`, `/api/v4/futures/usdt/tickers`, candlesticks | `wss://fx-ws.gateio.ws/v4/ws/usdt` |
| **Bitget** | `api.bitget.com` | `/api/v2/mix/market/tickers`, `/api/v2/mix/market/candles` | `wss://ws.bitget.com/v2/ws/public` |

---

## 3. Дополнительные площадки

Отдельные модули (`mexc_monitor/<биржа>/`), используются в специализированных разделах.

| Биржа | REST base | Особенности |
|---|---|---|
| **HTX (Huobi)** | `api.huobi.pro`, `api.hbdm.com` | линейные свопы `/linear-swap-ex/...`; WS `wss://api.hbdm.com/linear-swap-ws` |
| **AsterDex** | `fapi.asterdex.com` | Binance-совместимый API (`/fapi/v1/...`); WS `wss://fstream.asterdex.com/ws` |
| **Hyperliquid** | `api.hyperliquid.xyz` | единый `/info` (POST) для метаданных, цен, стакана |
| **dYdX** | `indexer.dydx.trade` | `/v4/perpetualMarkets`, orderbooks, candles; WS `wss://indexer.dydx.trade/v4/ws` |
| **Lighter** | `mainnet.zklighter.elliot.ai` | `/api/v1/orderBooks`, `/api/v1/candles`, `/api/v1/funding-rates` |

---

## 4. Сети депозита/вывода (колонка «Переводимо: сеть»)

Отдельный источник, независимый от рыночных фидов. Реализация — `mexc_monitor/coin_networks.py`, эндпоинт — `GET /api/coin-networks?coins=BTC,ETH`.

| Биржа | Эндпоинт (без ключей) | Формат сетей |
|---|---|---|
| **Gate.io** | `api.gateio.ws/api/v4/spot/currencies` | `chains: [{ name, withdraw_disabled, deposit_disabled }]` |
| **Bitget** | `api.bitget.com/api/v2/spot/public/coins` | `chains: [{ chain, withdrawable, rechargeable }]` |

Особенности:

- **Только Gate.io и Bitget** отдают статус сетей без API-ключей. У Binance / Bybit / OKX / MEXC эта информация доступна лишь через **подписанные** эндпоинты (`capital/config`, `asset/coin/query` и т.п.), поэтому в колонке они дают статус `?` (нет данных) — **выдуманные сети не подставляются**.
- Имена сетей **канонизируются** между биржами (напр. `BSC` ↔ `BEP20`, `ETH` ↔ `ERC20`, `TRX` ↔ `TRC20`), чтобы одинаковые сети корректно пересекались.
- Данные кэшируются в процессе с TTL, запрос к биржам выполняется лениво — только для монет, реально присутствующих в текущем кросс-скринере.
- Логика переводимости (`frontend/src/lib/networks.ts`, `computeTransfer`): по маршруту сделки «купить на бирже с лучшим ask → вывести → внести → продать на бирже с лучшим bid» ищется **общая сеть**, где одновременно разрешён вывод с источника и депозит на приёмник.

---

## 5. Ограничения окружения (sandbox / preview)

- Из инфраструктуры песочницы часть бирж отдаёт **HTTP 451 / 403** (геоблокировка): регулярно — **Binance, Bybit, OKX, MEXC**.
- Поэтому в preview данные стабильно приходят с **Gate.io, Bitget, Hyperliquid** и других негеоблокированных площадок. Это ограничение сети окружения, **не** дефект кода — при запуске из региона без блокировок отвечают все биржи.
- **Важно:** для REST-геоблока WebSocket-фиды часто остаются рабочими (напр. Binance REST = 451, но WS отдаёт данные). Это видно на странице **Диагностика источников** (`/diagnostics`, `GET /api/diagnostics/sources`), которая по каждой бирже показывает REST-латентность, статус (`ok`/`geo_blocked`/`rate_limited`/`error`), свежесть WS (futures и spot отдельно) и рекомендуемый источник.
- **WS-покрытие снапшота** (`mexc_monitor/ws_bookticker.py`): futures/perp по WS для всех бирж; spot по WS — для мультирыночных **OKX / Gate.io / HTX** (ленивый старт по первому spot-снапшоту). Binance spot остаётся на REST (его WS геоблокируется так же, как REST, а all-market `!bookTicker` для spot депрекейтнут). Остальные spot-рынки идут по REST. `try_ws_snapshot_rows(exchange, market)` сам выбирает WS или падает на REST, если фид не жив.

---

## 6. Прокси и smart routing

Обход геоблока — **per-exchange smart routing**: прокси применяется только к нужным биржам, остальные ходят напрямую (тот же принцип, что у Smart-DNS сервисов, но на уровне HTTP/SOCKS-прокси).

- **Резолвинг** (`mexc_monitor/proxy_registry.py`): по бирже в порядке приоритета — per-exchange override → общий default → статический config (`external_apis.json`) → `None` (trust_env / прямой). Override `direct` принудительно обходит прокси для конкретно�� биржи и **не** падает на config.
- **Схемы:** `http`, `https`, `socks5`, `socks5h` (для SOCKS нужен `httpx[socks]`, зафиксирован в `requirements.txt`). `socks5h` резолвит DNS на стороне прокси — полезно, если локальный DNS сам заблокирован.
- **Пул клиентов** (`mexc_monitor/http_shared.py`): общие `httpx.Client` группируются по прокси-URL, keep-alive сохраняется; биржи с одинаковым прокси переиспользуют соединения.
- **Управление:** страница **Сеть / Прокси** (`/network`) — общий прокси + таблица переопределений по биржам с индивидуальной проверкой связи. API: `GET/PATCH /api/network/config`, `GET /api/network/test?exchange=<name>`. В конфиге: `mexc.http_proxy_url` (общий) и `mexc.http_proxy_per_exchange` (`{биржа: url|"direct"}`).
- **Про Smart-DNS (xbox-dns.ru и подобные):** для н��шей задачи **не подходят** — это DNS-подмена, а не прокси; наш геоблок по IP, смена DNS его не снимает, и криптобирж нет в их whitelist. Нужен именно HTTP/SOCKS-прокси в неблокируемом регионе.

---

## 7. Где искать в коде

- Клиенты бирж: `mexc_monitor/<биржа>/client.py` (REST) и `ws*.py` (WebSocket).
- Прокси-реестр и резолвинг: `mexc_monitor/proxy_registry.py`, `mexc_monitor/http_utils.py`, `mexc_monitor/http_shared.py`.
- Диагностика источников: `mexc_monitor/source_probes.py` + `GET /api/diagnostics/sources`.
- Сети монет: `mexc_monitor/coin_networks.py` + эндпоинт в `backend/main.py`.
- Сборка снимка и модель исполнения: `mexc_monitor/futures_rows.py`, `backend/main.py`.
