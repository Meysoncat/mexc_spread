# P2-фичи конкурентов: Withdrawal Fee, L2 Slippage, Spread Lifetime

Анализ трёх фич, которые есть у конкурентов и отсутствуют в MEXC Spread Monitor.

---

## 1. Withdrawal Fee Calculator

### Что это

Калькулятор комиссий за вывод токена с одной биржи и депозит на другую. Критичен для спот-арбитража, где прибыль = спред − комиссия вывода − комиссия торговли.

### У кого есть

| Конкурент | Реализация |
|-----------|-----------|
| **ArbitrageScanner.io** | Полная интеграция в screener. Показывает: matching withdrawal networks (ERC20/TRC20/BEP20/...), комиссия вывода в USDT, итоговый спред после вычета комиссий. Автоматически подбирает совместимые сети. |
| **CoinArbitrageBot** | Базовая — показывает спред, но без автоматического вычета withdrawal fee. |
| **Aicoin** | Калькулятор вывода по отдельности, не интегрирован в арбитраж. |

### Как работает технически

1. **Данные**: API бирж для withdrawal fees (Binance `GET /sapi/v1/capital/config/getall`, MEXC `GET /api/v3/capital/config/getall`, Bybit и т.д.) + маппинг сетей (ERC20, TRC20, BEP20, Polygon, Arbitrum, Optimism, Solana...).
2. **Matching сетей**: для пары бирж найти общие сети вывода для токена (USDT на Binance поддерживает ERC20, TRC20, BEP20, Polygon, Arbitrum, Optimism, Solana; MEXC — свои сети). Пересечение → доступные пути.
3. **Расчёт**: `net_profit = spread_abs - withdrawal_fee_src - withdrawal_fee_dst - trading_fee_src - trading_fee_dst`.
4. **UI**: таблица с колонками «Сеть», «Комиссия вывода», «Время вывода», «Итоговый спред».

### Ценность для пользователя

**Очень высокая.** Без этого трейдер считает спред 50 bps, а после withdrawal fee остаётся 5 bps или минус. Это главная причина, почему новички теряют деньги на арбитраже.

### Сложность реализации

**Средняя (2-3 дня).**
- Нужно собрать withdrawal fee с 5-10 бирж (REST API или парсинг).
- Маппинг сетей (ERC20 = ETH = Ethereum, TRC20 = TRON, и т.д.) — можно начать с топ-5 сетей.
- Обновление: раз в час (fees rarely change).

---

## 2. L2 Slippage Estimation

### Что это

Оценка проскальзывания на основе глубины стакана (L2 order book data). Вместо «спред 10 bps на L1» показывает «для ордера 1000 USDT реальный спред будет 15 bps, для 5000 USDT — 25 bps».

### У кого есть

| Конкурент | Реализация |
|-----------|-----------|
| **ArbitrageScanner.io** | Показывает liquidity — доступный объём для сделки. Не полный L2 slippage, но индикатор. |
| **CoinGlass** | Depth chart (Bid/Ask depth) — визуализация глубины стакана, но не slippage calculator. |
| **Coinalyze** | Order book depth visualization. |
| **3Commas / Hummingbot** | Полный slippage estimation для ботов — «fill price for N units». |
| **Binance/Bybit UI** | Встроенный slippage indicator при размещении ордера. |

### Как работает технически

1. **Данные**: L2 order book (полный стакан) — `GET /api/v3/depth` (Binance), `GET /api/v3/depth` (MEXC), WS `depth` streams.
2. **Расчёт**:
   ```
   Для объёма V USDT:
     пройти по bid-стакану сверху вниз, суммируя qty * price до V
     effective_bid = V / total_qty
     аналогично для ask
     slippage_bps = (ask_effective - bid_effective) / mid * 10000
   ```
3. **UI**: слайдер «Размер ордера (USDT)» → график slippage или таблица:
   | Размер | Bid Fill | Ask Fill | Slippage (bps) | Spread after slippage |

### Ценность для пользователя

**Высокая.** L1-спред обманчив: для ордера >$500 реальный спред может быть в 2-3 раза хуже. Особенно важно для:
- Арбитража с реальными деньгами (не paper).
- Оценки максимального размера позиции.
- Сравнения ликвидности между биржами.

### Сложность реализации

**Средняя (2-4 дня).**
- L2 данные уже есть (WS depth streams работают для фьючерсов).
- Нужно: REST fallback для спота, кэширование стакана, расчёт effective price.
- UI: слайдер + таблица или sparkline-график slippage curve.

---

## 3. Spread Lifetime / Duration Tracking

### Что это

Отслеживание, как долго спред держится выше порога. Например: «BTCUSDT MEXC↔Binance спред >30 bps держится уже 45 минут, среднее за неделю — 12 минут».

### У кого есть

| Конкурент | Реализация |
|-----------|-----------|
| **ArbitrageScanner.io** | **Spread lifetime** — показывает, как долго пара существует с данным спредом. Встроен в screener как фильтр и колонка. |
| **CoinArbitrageBot** | Нет — только моментальный снимок. |
| **Coinglass** | Нет (фокус на funding/OI, не на spread duration). |
| **Hummingbot** | Spread monitoring с timestamp, но не lifetime tracking. |

### Как работает технически

1. **Данные**: при каждом snapshot вычисляется `spread_bps`. Если `spread_bps > threshold` → записать timestamp начала. При следующем snapshot: если спред всё ещё выше → обновить duration. Если упал → зафиксировать lifetime и начать заново.
2. **Хранение**: in-memory dict `{symbol: {start_ts, current_duration_sec, avg_duration_sec, count}}` + опционально SQLite для истории.
3. **Статистика**: среднее lifetime за период, медиана, % времени выше порога, гистограмма распределения.
4. **UI**:
   - Колонка в таблице «Spread Monitor»: `Lifetime: 45m` (цвет: зелёный >30m, жёлтый 5-30m, серый <5m).
   - Отдельная страница/панель: heatmap «spread duration by symbol» или time-series «spreads > threshold over time».

### Ценность для пользователя

**Высокая для арбитражёров.** Короткий lifetime (30 сек) значит, что спред быстро закрывается — нужно автоматическое исполнение. Долгий lifetime (часы) значит, что можно вручную проверить и execute. Это ключевой фильтр для решения «стоит ли входить».

### Сложность реализации

**Низкая (1-2 дня).**
- Логика простая: timestamp начала + текущая длительность.
- Данные уже собираются (history worker пишет snapshot каждые N секунд).
- UI: одна колонка в таблице + опционально heatmap.

---

## Сводная таблица

| Фича | Ценность | Сложность | Конкуренты с фичей | Приоритет |
|------|----------|-----------|-------------------|-----------|
| **Withdrawal Fee Calculator** | Очень высокая | Средняя (2-3 дня) | ArbitrageScanner | **P1** |
| **L2 Slippage Estimation** | Высокая | Средняя (2-4 дня) | 3Commas, Hummingbot, CoinGlass (depth) | **P2** |
| **Spread Lifetime** | Высокая | Низкая (1-2 дня) | ArbitrageScanner | **P1** |

---

## Рекомендация по реализации

1. **Spread Lifetime** — самый простой и быстрый win. Данные уже есть, нужна только логика timestamp + UI колонка.
2. **Withdrawal Fee** — самый ценный для пользователей. Начать с топ-5 токенов (USDT, BTC, ETH, SOL, XRP) и топ-5 бирж.
3. **L2 Slippage** — требует L2 данных (уже есть для фьючерсов через WS). Начать с простого «effective price for N USDT».
