# Интеграция: получение сетапов внешними системами

Скринер публикует торговые идеи («сетапы») в едином формате `setup/v1`.
Исполнение — задача потребителя: сетап говорит *что* и *почему*, а не как
ставить ордера.

Базовый URL — адрес бэкенда (по умолчанию `http://127.0.0.1:8000`).

## Доступ

Если задана переменная окружения `SCREENER_API_KEY`, каждый запрос должен
передавать ключ:

- заголовок `X-API-Key: <key>` (REST, WebSocket);
- или параметр `?api_key=<key>` (для `EventSource` в браузере — он не умеет заголовки).

Без переменной API открыт: подходит только для локальной сети.

## Эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/setups` | Активные сетапы (снимок) |
| GET | `/api/v1/setups/{id}` | Один активный сетап |
| GET | `/api/v1/setups/stream` | SSE: снимок + события в реальном времени |
| WS | `/api/v1/setups/ws` | То же через WebSocket |
| GET | `/api/v1/setups/history` | Журнал событий (SQLite, 30 дней) |
| GET | `/api/v1/setups/stats` | Исходы идей по типам: win rate, средний R |
| GET | `/api/v1/schema/setup` | JSON Schema сетапа и события |
| GET | `/api/v1/screener` | Сводка по монетам через все биржи |
| GET | `/api/v1/market/{base}` | Все котировки монеты: perp по биржам и спот |
| GET | `/api/v1/health` | Состояние бирж, задач, движка |

Фильтры для `/setups`, `/setups/stream`, `/setups/ws` (все необязательные, списки через запятую):

- `type` — `cross_perp,funding_arb,basis,lead_lag,breakout,momentum,funding_squeeze,btc_lag`;
- `category` — `arbitrage` или `directional`;
- `base` — `BTC,ETH`;
- `venue` — хотя бы одна нога сетапа на этой бирже;
- `direction` — `long` или `short`;
- `min_score` — 0..100.

## Формат сетапа (`setup/v1`)

```json
{
  "schema": "setup/v1",
  "id": "cross_perp:BTC:mexc>binance",
  "instance": "9f2c4e1a7b3d5e60",
  "type": "cross_perp",
  "category": "arbitrage",
  "status": "active",
  "base": "BTC",
  "direction": null,
  "legs": [
    {"venue": "mexc", "market": "perp", "symbol": "BTC_USDT", "side": "buy",
     "price": 65000.0, "price_per_coin": 65000.0, "l1_qty": 0.8,
     "l1_notional_usd": 52000.0, "taker_fee_bps": 2.0},
    {"venue": "binance", "market": "perp", "symbol": "BTCUSDT", "side": "sell",
     "price": 65195.0, "price_per_coin": 65195.0, "l1_qty": 3.1,
     "l1_notional_usd": 202104.5, "taker_fee_bps": 5.0}
  ],
  "entry": null, "stop": null, "targets": [], "risk_reward": null,
  "edge_bps": 14.6,
  "score": 37.8,
  "horizon_sec": 3600,
  "valid_until": null,
  "opened_at": "2026-10-17T10:00:00.000Z",
  "updated_at": "2026-10-17T10:00:05.500Z",
  "closed_at": null,
  "close_reason": null,
  "data_age_ms": 420,
  "metrics": {"gross_bps": 29.9, "baseline_bps": 0.0, "fees_round_trip_bps": 14.0, "...": "..."},
  "notes": ["Long BTC perp на mexc, short на binance; ..."]
}
```

Ключевые поля:

- `id` — стабильный ключ идеи. Если идея закрылась и открылась снова, `id` тот же, а `instance` новый. **Дедуплицируйте по `instance`.**
- `legs[].symbol` — символ в API биржи (`BTC-USDT-SWAP`, `BTC_USDT`, `1000PEPEUSDT`, `kPEPE`). `price` указан в единицах инструмента, `price_per_coin` — за одну монету.
- `legs[].l1_qty` — монет на лучшей цене; `null`, если биржа не отдаёт объём (MEXC — до догрузки стакана, Hyperliquid).
- `edge_bps` (арбитраж) — ожидаемая прибыль в bps от номинала одной ноги после комиссий входа и выхода и пересечения спреда на выходе.
- `entry` / `stop` / `targets` (направленные) — цены за одну монету.
- `score` 0..100 — ранжирование внутри типа. Это не вероятность.
- `data_age_ms` — возраст самых старых данных среди ног. Отбрасывайте сетапы со слишком старыми данными.

### Жизненный цикл

- **Арбитраж** (`cross_perp`, `funding_arb`, `basis`) живёт, пока выполняется условие. Он закрывается с `close_reason: "condition_gone"`, если edge ушёл, нога пропала или данные устарели. Открывается только после того, как условие продержится 1.5–2 с: одиночные выбросы отсекаются.
- **Направленные** (`lead_lag`, `breakout`, `momentum`, `funding_squeeze`, `btc_lag`) — разовые идеи. Закрываются по `stop_hit`, `target_hit` или `expired` (срок в `valid_until`). У закрытой идеи есть `outcome`: `result`, `r_multiple`, `mfe_pct`, `mae_pct`, `duration_sec`.

### Типы и их `metrics`

| Тип | Суть | Основные метрики |
|---|---|---|
| `cross_perp` | Купить perp на A, продать на B | `gross_bps`, `baseline_bps` (обычная разница бирж), `exit_cross_bps`, `fees_round_trip_bps`, `funding_carry_8h_bps`, `l1_notional_usd` |
| `funding_arb` | Long на низком funding, short на высоком | `funding_apr_pct`, `income_horizon_bps`, `cost_bps`, `breakeven_h`, ставки, интервалы и `*_next_funding_ms` обеих ног |
| `basis` | Спот↔perp (cash-and-carry) | `mode` (`carry`/`reverse`), `basis_bps`, `baseline_bps`, `funding_carry_bps`, `same_venue`, `requires_spot_borrow` |
| `lead_lag` | Лидер сдвинулся, отстающая биржа нет | `leader`, `leader_move_bps`, `lagger_move_bps`, `gap_bps`, `edge_bps` |
| `breakout` | Пробой диапазона на объёме | `level`, `range_high`, `range_low`, `volume_ratio`, `atr`, `breakout_atr` |
| `momentum` | Импульс: продолжение или откат | `bias` (`continuation`/`reversion`), `change_pct`, `extreme_move_pct`, `pullback`, `volume_ratio` |
| `funding_squeeze` | Перекошенный funding + рост OI | `funding_8h_pct`, `funding_excess_vs_venues_pct`, `oi_change_pct`, `price_change_pct` |
| `btc_lag` | Альт не догнал BTC | `btc_move_pct`, `alt_move_pct`, `expected_move_pct`, `shortfall_pct`, `corr`, `beta` |

## Поток событий

Событие:

```json
{"seq": 1042, "event": "opened", "ts": "2026-10-17T10:00:00.000Z", "setup": { "...": "setup/v1" }}
```

`event` принимает значения `opened`, `updated` и `closed`:

- `updated` шлётся при заметном изменении score/edge, но не чаще раза в секунду, и не реже раза в 15 с, пока идея жива (heartbeat).
- `closed` приходит всегда, даже если идея уже не проходит ваш фильтр `min_score`: так вы узнаете, что её больше нет.

### SSE (`/api/v1/setups/stream`)

1. При подключении приходит `event: snapshot` с `{"seq": N, "setups": [...]}` — всё активное на момент N.
2. Дальше приходят `event: opened|updated|closed`; поле `id:` равно `seq`.
3. Каждые 15 с приходит комментарий `: ping`.
4. При обрыве переподключитесь с заголовком `Last-Event-ID: <последний seq>` (браузерный `EventSource` делает это сам) или с `?since=<seq>`. Пропущенные события придут по порядку. Если буфер (10 000 событий) уже вытеснил их, придёт новый `snapshot`.
5. Если клиент не успевает читать, приходит `event: overflow`, и поток закрывается. Переподключитесь с `Last-Event-ID`.

```bash
curl -N "http://127.0.0.1:8000/api/v1/setups/stream?category=arbitrage&min_score=30"
```

### WebSocket (`/api/v1/setups/ws`)

Параметры те же, что у SSE (`since`, фильтры, `api_key`). Сообщения:

- `{"type":"snapshot","seq":N,"setups":[...]}`;
- `{"type":"event","seq":...,"event":"opened",...}`;
- `{"type":"heartbeat"}` каждые 15 с;
- `{"type":"overflow"}`, после чего соединение закрывается.

Клиент может отправить `{"op":"ping"}` и получит `{"type":"pong"}`.

### Пример потребителя (Python)

```python
import json, httpx

def consume(base="http://127.0.0.1:8000", api_key=None):
    headers = {"X-API-Key": api_key} if api_key else {}
    last_id, active = None, {}
    while True:
        if last_id:
            headers["Last-Event-ID"] = str(last_id)
        try:
            with httpx.stream("GET", f"{base}/api/v1/setups/stream", headers=headers,
                              params={"category": "arbitrage", "min_score": 30}, timeout=None) as r:
                event, data = None, None
                for line in r.iter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("id: "):
                        last_id = int(line[4:])
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                    elif line == "" and event:
                        if event == "snapshot":
                            active = {s["instance"]: s for s in data["setups"]}
                        elif event in ("opened", "updated"):
                            active[data["setup"]["instance"]] = data["setup"]
                            if event == "opened":
                                on_new_setup(data["setup"])  # ваша логика
                        elif event == "closed":
                            active.pop(data["setup"]["instance"], None)
                        event, data = None, None
        except httpx.HTTPError:
            continue  # переподключение с Last-Event-ID
```

## Настройка

`config/setups.json` (или путь в `SETUPS_CONFIG`) накладывается поверх значений по умолчанию из `mexc_monitor/setups/config.py`:

- `perp_taker_bps` / `spot_taker_bps` — **ваши** комиссии taker по биржам. Без них edge считается по базовому тарифу.
- Пороги каждого детектора: `open_*` открывает идею, `keep_*` удерживает уже открытую. Так работает гистерезис.
- `kline_venues` и `kline_universe` — на каких биржах и для скольких монет строятся 1m-свечи для направленных идей.
- `min_score` — не публиковать идеи ниже порога.

Переменные окружения: `SCREENER_ENABLED=0` отключает скринер, `SCREENER_API_KEY` задаёт ключ доступа, `SETUPS_CONFIG` — путь к конфигу.

## Откуда данные

- **Perp, лучшие цены:**
  - WS: Binance, Bybit, OKX, Gate.io, Bitget, HTX, dYdX;
  - REST-опрос: MEXC (1.5 с), Aster (2 с), Hyperliquid (2 с, impact-цены, помечены `indicative`), Lighter (10 с).
- **Funding, OI, 24h-объём:** REST раз в 30–60 с. История OI хранится 3 часа.
- **Справочники контрактов** (размер контракта, множитель цены, статус листинга): раз в 30 мин. Делистинги и неактивные контракты отбрасываются.
- **Спот:** REST раз в 3 с.
- **1m-свечи:** WS (Binance, Bybit, MEXC) для топ-N монет по объёму, история подгружается по REST.

Цены сравниваются за одну монету: `1000PEPE`, `kPEPE` и контракты OKX/Gate/HTX/MEXC приведены к монетам.
