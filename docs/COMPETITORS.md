# Анализ конкурентов MEXC Spread Monitor

Контекст: инструмент для **личного использования** — мониторинг спредов, арбитражные сигналы, автоматическое исполнение.

---

## 1. Прямые конкуренты (арбитражные скринеры)

### ArbitrageScanner.io
**Что делает:** Полноценная платформа для крипто-арбитража. CEX + DEX, 40+ бирж.

| Фича | Есть? | Детали |
|-------|-------|--------|
| Спреды между биржами | ✅ | Real-time screener, кросс-спреды |
| Spread lifetime | ✅ | Показывает, как долго спред держится |
| Withdrawal fees | ✅ | Автоматический подбор сетей, комиссии |
| Funding rates | ✅ | Отдельная страница с агрегацией |
| Spot-Futures arb | ✅ | Perpetuals screener с funding |
| DEX арбитраж | ✅ | 25 DEX, 40+ блокчейнов |
| Автоторговля | ❌ | Только ручной бот (безопасность) |
| Telegram-бот | ✅ | Уведомления о спредах |
| Цена | $99-795/мес | |

**Сильные стороны:** Огромный охват бирж и DEX, обучение, кейсы, AI-анализ кошельков.
**Слабые стороны:** Облачный сервис (нет self-hosted), дорогой, перегруженный UI, нет автоторговли.

---

### CoinArbitrageBot
**Что делает:** Базовый арбитражный скринер между биржами.

| Фича | Есть? | Детали |
|-------|-------|--------|
| Спреды между биржами | ✅ | Основная функция |
| Withdrawal fees | ⚠️ | Показывает, но без автоматического подбора сетей |
| Spread lifetime | ❌ | Только моментальный снимок |
| Автоторговля | ❌ | Нет |
| Цена | Бесплатно / $29-99/мес | |

**Сильные стороны:** Простой, дешёвый.
**Слабые стороны:** Минимум фич, нет depth-анализа, нет автоторговли.

---

## 2. Аналитические платформы (не арбитраж, но пересекаются)

### Coinglass
**Что делает:** Аналитика деривативов — funding rates, open interest, ликвидации.

| Фича | Есть? | Детали |
|-------|-------|--------|
| Funding rates | ✅ | Лучший в классе — heatmap, сравнение, история |
| Open Interest | ✅ | По всем биржам |
| Ликвидации | ✅ | Real-time |
| Спреды между биржами | ❌ | Нет |
| Арбитраж | ⚠️ | Только funding rate arbitrage list |
| Depth chart | ✅ | Bid/Ask depth visualization |
| Цена | Бесплатно (базовый) / API платный | |

**Сильные стороны:** Лучший источник данных по деривативам, бесплатный UI.
**Слабые стороны:** Не арбитражный инструмент, нет спред-мониторинга.

---

### Aicoin
**Что делает:** Китайская платформа анализа рынка — графики, алерты, арбитраж.

| Фича | Есть? | Детали |
|-------|-------|--------|
| Графики | ✅ | Профессиональные свечные графики |
| Smart Arbitrage | ✅ | Встроенный арбитражный инструмент |
| Large orders tracking | ✅ | Отслеживание крупных ордеров |
| AI интерпретация | ✅ | AI-анализ графиков |
| Multi-account trading | ✅ | Торговля с нескольких аккаунтов |
| Telegram-алерты | ✅ | |
| Цена | Бесплатно (базовый) / VIP платный | |

**Сильные стороны:** Мощные графики, AI-анализ, мультиаккаунт.
**Слабые стороны:** Китайский фокус, desktop-only, нет self-hosted.

---

### Coinalyze
**Что делает:** Аналитика крипто-деривативов.

| Фича | Есть? | Детали |
|-------|-------|--------|
| Funding rates | ✅ | |
| Open Interest | ✅ | |
| Long/Short ratio | ✅ | |
| Спреды | ❌ | Нет |
| Арбитраж | ❌ | Нет |

---

## 3. Торговые боты (с арбитражными стратегиями)

### Hummingbot

> Данные собраны 2026-07-19 через `web-user-sim` (CDP-парсинг hummingbot.org,
> github.com/hummingbot/hummingbot). См. `scrape_hummingbot.py` и `hb-out/`.

**Что это:** Open-source Python+Cython фреймворк для алгоритмической торговли,
поддерживаемый Hummingbot Foundation (Apache 2.0). Последняя версия на момент
проверки — **v2.15.0 / v2.15.1**. 19.2k★ / 4.8k fork, 27,566 коммитов, 109
релизов. Заявлено $36B совокупного объёма и 100k+ инстансов с 2025-01.

**Архитектура — несколько связанных репозиториев:**

| Репозиторий | Назначение | URL |
|-------------|------------|-----|
| `hummingbot/hummingbot` | Core trading client (CLI, Python/Cython) | https://github.com/hummingbot/hummingbot |
| `hummingbot/hummingbot-api` | **REST API backend** для управления ботами, портфелями, торговлей | https://github.com/hummingbot/hummingbot-api |
| `hummingbot/gateway` | TypeScript DEX-мидлвара (AMM/CLMM/Router — 30+ DEX) | https://github.com/hummingbot/gateway |
| `hummingbot/condor` | Современный Telegram-интерфейс управления ботами | https://github.com/hummingbot/condor |
| `hummingbot/mcp` | **MCP-сервер**: AI-ассистенты (Claude/Gemini/ChatGPT) управляют Hummingbot | https://github.com/hummingbot/mcp |
| `hummingbot/skills` | Скиллы для AI-ассистентов (управление стратегиями/исполнителями) | https://github.com/hummingbot/skills |
| `hummingbot/dashboard` | Web UI управления ботами — **deprecated**, рекомендуют Condor | https://github.com/hummingbot/dashboard |
| `hummingbot/quants-lab` | Jupyter-ноутбуки для research + **backtesting** | https://github.com/hummingbot/quants-lab |

**MEXC-коннектор: поддерживается (spot), но не как Foundation Partner.**

| Exchange | Тип | Sub-Type | Connector ID | Спонсор |
|----------|-----|----------|--------------|---------|
| **MEXC** | CLOB CEX | **Spot** | `mexc` | ❌ нет |

⚠️ **Важно:** `mexc_perpetual` в официальном списке **отсутствует**. Spot-futures
арбитраж через Hummingbot на MEXC не работает «из коробки» — нужен форк или
другая биржа для второй ноги. Для bid/ask захвата на споте это не проблема.

**Стратегии (Strategy V2 — recommended, V1 — legacy):**

V2 Framework — модульная «лего»-архитектура из трёх компонентов:

| Use case | Компонент |
|----------|-----------|
| One-time торговая задача (entry, DCA, hedge) | **Executor** (через API) |
| Обучение / прототип / простой single-pair бот | **Script** (наследует `StrategyV2Base`) |
| Сложная стратегия (multi-pair / multi-config) | **Controller** |
| Несколько независимых стратегий в одном боте | **Multiple Controllers** |

V1 (legacy) включает: **Pure Market Making**, **Avellaneda & Stoikov**,
**Cross-Exchange Market Making**, **Liquidity Mining** и др. —
по-прежнему работают, но нового функционала там не появляется.

**Ключевые фичи для нашей задачи (захват bid/ask на MEXC spot):**

| Фича | Подходит? | Детали |
|------|-----------|--------|
| **Pure Market Making** (V1/V2) | ✅ да | Ровно то, что делалось руками на MEXC |
| **Avellaneda-Stoikov** MM | ✅ да | Динамический bid/ask на основе волатильности |
| **Inventory skew / hedge** | ✅ да | Matured risk-контроль (нет в нашем `SpreadCaptureEngine`) |
| **Backtesting** | ✅ да | Встроенный engine (через Quants Lab / исторические данные) |
| **Paper trading** | ✅ да | Полноценный симулятор с реальным стаканом |
| **Live trading MEXC spot** | ✅ да | `mexc` коннектор готов, sub-second execution |
| **REST API управления** | ✅ да | `hummingbot-api` — можно дёрнуть из нашего бэкенда |
| **MCP / AI integration** | ✅ да | Можно управлять через Claude/Gemini |
| **Spot-Futures arb на MEXC** | ❌ нет | Нет `mexc_perpetual` коннектора |
| **Cross-exchange arb MEXC↔AsterDEX** | ⚠️ | AsterDEX в списке коннекторов Hummingbot **нет** |

**Установка (минимум):**

```bash
git clone https://github.com/hummingbot/hummingbot.git
cd hummingbot
make setup         # интерактивный промпт: include Gateway? [y/N]
make deploy        # поднимает docker-compose
docker attach hummingbot   # подключиться к CLI
```

Альтернатива — `make install` + `make run` из исходников (для разработки).

**Сильные стороны:**
- Зрелый фреймворк (7+ лет, активное сообщество, регулярные релизы)
- Полностью open source (Apache 2.0), можно коммерчески использовать
- MEXC spot-коннектор **готов к live-торговле** (в отличие от нашего `ArbitrageEngine`)
- Strategy V2 — продуманная модульная архитектура
- REST API (`hummingbot-api`) и MCP — несколько способов интеграции
- Backtesting engine в комплекте

**Слабые стороны (для нашей задачи):**
- **Нет MEXC perpetual** — spot-futures арбитраж не выйдет на одной бирже
- TUI/CLI-нативный интерфейс; веб-Dashboard deprecated
- Высокий порог входа (Python, Docker, YAML-конфиги)
- Большая кодовая база — сложнее дебажить под капотом, чем свой код
- AsterDEX (наша вторая «нога» в `ArbitrageEngine`) не входит в 50+ коннекторов

---

### 3Commas
**Что делает:** Облачная платформа для автоматической торговли.

| Фича | Есть? | Детали |
|-------|-------|--------|
| DCA боты | ✅ | Основная функция |
| Grid боты | ✅ | |
| Signal bot | ✅ | |
| Арбитраж | ❌ | Нет |
| Спред-мониторинг | ❌ | Нет |
| Цена | $29-99/мес | |

---

## 4. DEX-аналитика

### Dexscreener
**Что делает:** Real-time аналитика DEX-токенов.

| Фича | Есть? | Детали |
|-------|-------|--------|
| DEX пары | ✅ | Все основные DEX |
| Графики | ✅ | Real-time |
| Арбитраж | ❌ | Нет |
| Цена | Бесплатно | |

### Birdeye
**Что делает:** Аналитика Solana/DEX токенов.

| Фича | Есть? | Детали |
|-------|-------|--------|
| Token analytics | ✅ | |
| Арбитраж | ❌ | Нет |

---

## 5. Сравнительная таблица

| Фича | MEXC Monitor | ArbitrageScanner | Coinglass | Aicoin | Hummingbot |
|------|-------------|-----------------|-----------|--------|------------|
| **Self-hosted** | ✅ | ❌ | ❌ | ❌ | ✅ |
| **Бесплатно** | ✅ | ❌ | ✅ | ⚠️ | ✅ |
| **Спред-мониторинг** | ✅ | ✅ | ❌ | ✅ | ⚠️ |
| **10+ бирж** | ✅ | ✅ (40+) | ✅ | ✅ | ✅ (300+) |
| **WS real-time** | ✅ | ⚠️ | ⚠️ | ⚠️ | ✅ |
| **Spread lifetime** | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Withdrawal fees** | ✅ | ✅ | ❌ | ❌ | ❌ |
| **L2 Slippage** | ✅ | ⚠️ | ✅ (depth) | ❌ | ✅ |
| **Funding rates** | ✅ | ✅ | ✅ | ❌ | ⚠️ |
| **Автоторговля** | ✅ | ❌ | ❌ | ✅ | ✅ |
| **Paper trading** | ✅ | ❌ | ❌ | ❌ | ✅ |
| **Telegram алерты** | ✅ | ✅ | ✅ | ✅ | ✅ (Condor) |
| **Arbitrage (cross-exchange)** | ✅ | ✅ | ❌ | ✅ | ✅ |
| **Spot-Futures arb** | ✅ | ✅ | ⚠️ | ❌ | ✅ |
| **Lead-Lag анализ** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **MetaScalp интеграция** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Portfolio risk** | ✅ | ❌ | ❌ | ❌ | ⚠️ |
| **Красивый UI** | ✅ | ⚠️ | ✅ | ✅ | ❌ |

---

## 6. Вывод под реальную задачу: $200, спред-торговля на MEXC, личный рост депозита

> Контекст (сменился): цель — **не SaaS на продажу**, а **личный инструмент**,
> чтобы системно растить депозит ~$200 через захват bid/ask спреда на MEXC,
> который уже получался руками. Все выводы ниже — под этот сценарий.

### Честное сравнение под задачу

| Критерий | MEXC Monitor (наш) | Hummingbot |
|----------|-------------------|------------|
| Live-исполнение на MEXC spot | ⚠️ только `TradingEngine`, остальные симуляторы | ✅ готовый `mexc`-коннектор, sub-second |
| Pure Market Making стратегия | базовая (`SpreadCaptureEngine`) | ✅ Pure MM + **Avellaneda-Stoikov** (мат-обоснованная) |
| Inventory risk control | базовый | ✅ matured (skew, hedge, reorder logic) |
| Backtesting | ⚠️ `backtest.py` (минимальный) | ✅ полноценный engine + Quants Lab |
| Paper trading | ✅ | ✅ |
| MEXC spot | ✅ | ✅ |
| MEXC perpetual | ✅ (свой клиент) | ❌ нет в официальном |
| Красивый личный UI | ✅ React/FastAPI | ❌ TUI/Telegram |
| Self-hosted, бесплатно | ✅ | ✅ |
| Свой код под капотом | ✅ | ❌ чужой фреймворк |

### Три варианта взаимодействия (плюсы/минусы под $200)

| Вариант | Суть | Когда подходит | Риск |
|---------|------|----------------|------|
| **A. Гибрид** | Hummingbot торгует live, наш backend мониторит + UI читает статус через `hummingbot-api` | После того как докажешь edge на бэктесте Hummingbot и захочешь красивый UI поверх | Написать адаптер к REST API Hummingbot |
| **B. Перенос идей** | Не подключаем Hummingbot, а **учимся** по их Pure MM / Avellaneda-Stoikov коду и переносим паттерны в `SpreadCaptureEngine` | Если хочется держать свой код и понимать каждую строчку | Дольше, чем «взять готовое» |
| **C. Полный переход** | Временно забыть про свой код, поставить Hummingbot, торговать на нём | На этапе проверки edge (первые недели) | Теряем UI, но получаем зрелый бот быстро |

### Рекомендация (по фазам)

1. **Фаза 0 (аудит сделок) — Hummingbot не нужен.** Сначала выгрузить
   историю своих ручных сделок из MEXC и посчитать win-rate / PnL / инвентарный
   риск. Без цифр любые выводы — фантазия.
2. **Фаза 1 (доказать edge) — Вариант C.** Поставить Hummingbot локально,
   настроить **Pure Market Making** на 2-3 парах MEXC spot, прогнать
   **backtest** на истории. Сравнить paper-PnL бота с тем, что было руками.
3. **Фаза 2 (масштаб) — Вариант A или B.** Если edge подтверждён — либо
   оставить Hummingbot как исполнителя и сделать к нему UI через
   `hummingbot-api` (Вариант A), либо перенести наработки в свой
   `SpreadCaptureEngine` (Вариант B).

### Категорически НЕ делать под $200

- ❌ Cross-exchange арбитраж (MEXC↔AsterDEX и пр.) — нужны переводы, капитал на
  двух биржах, latency-инфраструктура. Депозит слишком мал.
- ❌ Spot-futures / cash-and-carry арбитраж — нужен капитал на двух рынках,
  funding risk, риск ликвидации. Hummingbot это на MEXC и не умеет (нет
  perpetual-коннектора).
- ❌ Гнаться за «уникальными фичами» (Lead-Lag, MetaScalp, Portfolio Risk) до
  того, как доказан edge базовой стратегии. Это преждевременная оптимизация.

### Что убрать из текущего проекта (когда дойдёт до чистки)

Под фокус «bid/ask захват на MEXC spot» лишним становится:

| Модуль | Статус | Действие |
|--------|--------|----------|
| `arbitrage/` (MEXC↔AsterDEX) | мёртвый (нет OrderExecutor в проде, см. `backend/main.py:2187`) | выкинуть |
| `futures_arb/` | paper-only by design | выкинуть |
| 7 внешних бирж (binance, bybit, okx, gateio, htx, bitget, dydx, hyperliquid) | не нужны под задачу | вынести в отдельную ветку/архив |
| AsterDEX интеграция | не нужна | выкинуть |
| `AIChatPanel`, `DensityMonitorPage`, `FundingHeatmapPage`, `VSPage` | шум под задачу | отложить |
| `SpreadCaptureEngine` | ✅ ключевой | оставить и докрутить (или заменить на Hummingbot) |
| Spread Monitor (UI) | ✅ ключевой | оставить |
| History SQLite | ✅ нужен для анализа | оставить |

### Ссылки (для следующих шагов)

- Установка: https://hummingbot.org/installation/hummingbot-client/
- MEXC connector doc: https://hummingbot.org/exchanges/mexc/
- Стратегии V2: https://hummingbot.org/strategies/v2-strategies/
- Hummingbot API (для Варианта A): https://github.com/hummingbot/hummingbot-api
- MCP-сервер (AI-управление): https://github.com/hummingbot/mcp
- Quants Lab (backtesting): https://github.com/hummingbot/quants-lab
- Discord (support #support канал): https://discord.gg/hummingbot

---

## 7. Прежние выводы (для контекста «как продукт», не под $200)

> Этот раздел оставлен как исторический — он описывает MEXC Monitor как
> «продукт с уникальными фичами». Под реальную задачу ($200, личный рост)
> эти выводы **вторичны** — первичен раздел 6 выше.

### Что у MEXC Monitor **лучше** конкурентов:

1. **Self-hosted + бесплатно** — данные не утекают, нет подписки. ArbitrageScanner стоит $99-795/мес.
2. **Комбинированный подход** — мониторинг + арбитраж + автоторговля в одном инструменте. У конкурентов это разные продукты.
3. **Lead-Lag анализ** — уникальная фича, нет ни у одного конкурента.
4. **MetaScalp интеграция** — уникальная фича.
5. **Portfolio risk manager** — агрегированный риск по всем движкам. У конкурентов нет.
6. **Paper trading** — безопасная отработка стратегий.
7. **WS real-time** — 7 бирж через WebSocket, мгновенные обновления.

### Что у конкурентов **лучше**:

1. **Охват бирж** — ArbitrageScanner: 40+ CEX + 25 DEX. У вас 10.
2. **DEX арбитраж** — ArbitrageScanner поддерживает DEX. У вас только CEX (кроме AsterDEX).
3. **Funding rate heatmap** — Coinglass делает это лучше всех.
4. **AI-анализ** — Aicoin и ArbitrageScanner имеют AI-инсайты.
5. **Mobile app** — Aicoin, Coinglass, 3Commas имеют мобильные приложения.
6. **Обучение** — ArbitrageScanner — 70+ кейсов и обучение.

### Рекомендации для развития:

1. **Не гнаться за охватом** — для личного использования 10 бирж достаточно. Лучше глубина, чем ширина.
2. **Усилить уникальные фичи** — Lead-Lag, MetaScalp, Portfolio Risk — это ваше конкурентное преимущество.
3. **Добавить DEX** — хотя бы 1-2 DEX (Uniswap, Raydium) для полноты картины.
4. **Mobile notifications** — Telegram уже есть, но можно добавить push-уведомления.
5. **Backtest** — возможность протестировать стратегию на исторических данных.
