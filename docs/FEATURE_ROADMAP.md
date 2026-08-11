# План развития MEXC Spread Monitor — фичи от конкурентов

> **Контекст:** Инструмент для личного использования. Уже реализовано: Spread Lifetime, Withdrawal Fee Calculator, L2 Slippage Estimator.

**Цель:** Добавить ключевые фичи, которые есть у конкурентов, но отсутствуют в MEXC Spread Monitor.

**Приоритет:** P1 = критично для арбитража, P2 = сильно улучшает UX, P3 = nice-to-have.

---

## Уже реализовано (эта сессия)

| Фича | Статус |
|-------|--------|
| Spread Lifetime | ✅ Колонка в таблице, трекер на фронтенде |
| Withdrawal Fee Calculator | ✅ Конфиг, endpoint, UI на Multi-Exchange |
| L2 Slippage Estimator | ✅ Endpoint с VWAP walk-the-book, UI на Spread Sniper |

---

## P1 — Критично для арбитража

### 1. Funding Rate Heatmap (от Coinglass) ✅ РЕАЛИЗОВАНО

**Что:** Визуальная карта funding rates по всем биржам и символам. Coinglass делает это лучше всех — heatmap с цветовой кодировкой, history, comparison.

**Статус:** Полностью реализовано:
- Страница `/funding` с таблицей funding rates по 6 биржам (MEXC, Binance, Bybit, OKX, Gate.io, Bitget)
- Цветовая кодировка: зелёный (positive), красный (negative)
- Сортировка по символу, максимальному |rate|, спреду ставок
- Фильтр по минимальному |rate|
- Поиск по символу
- Tooltip с annualized yield

**Почему важно:** Funding rate — ключевой фактор для spot-futures арбитража. Сейчас данные есть, но нет визуализации. Heatmap позволяет мгновенно увидеть, где funding самый высокий/низкий.

**Реализация:**
- Backend: `/api/funding-heatmap` — агрегация funding rates по всем биржам
- Frontend: новая страница `/funding` или секция на Spread Sniper
- Компонент: тепловая карта (symbol × exchange), цвет от зелёного (positive) до красного (negative)
- История: график funding rate за 7/30 дней

**Сложность:** 2-3 дня
**Файлы:** `backend/main.py`, `frontend/src/pages/FundingHeatmapPage.tsx`, `frontend/src/App.tsx`

---

### 2. Spread History Time-Series (от ArbitrageScanner) ✅ РЕАЛИЗОВАНО

**Что:** График изменения спреда во времени для конкретной пары. ArbitrageScanner показывает "spread lifetime" как график — как долго спред был выше порога.

**Статус:** Полностью реализовано:
- `SpreadChartModal` — модальное окно с графиком спреда (открывается кликом на строку в Spread Monitor)
- `SpreadChart` — standalone компонент с SSE real-time обновлениями
- `GET /api/spread/history` — in-memory ring buffer (30 мин)
- `GET /api/spread/stats` — статистика (avg, min, max, std, % above threshold)
- `GET /api/spread/stream` — SSE для real-time обновлений
- Звуковой алерт при пересечении порога

**Почему важно:** Позволяет увидеть паттерны: спреды растут в определённое время, быстро закрываются после новостей, и т.д. Критично для выбора момента входа.

**Реализация:**
- Backend: данные уже собираются в SQLite (`history_worker`). Нужен endpoint `/api/spread-history?symbol=BTCUSDT&hours=24`
- Frontend: график на Lightweight Charts (уже используется) — line chart спреда во времени
- Пороговые линии: горизонтальные линии на уровнях entry/exit threshold
- Интеграция: модальное окно при клике на символ в Spread Monitor

**Сложность:** 1-2 дня
**Файлы:** `backend/main.py`, `frontend/src/SpreadHistoryChart.tsx`, `frontend/src/pages/SpreadMonitorPage.tsx`

---

### 3. Depth Chart Visualization (от Coinglass)

**Что:** Визуализация глубины стакана — bid/ask depth как.area chart. Coinglass показывает это как "Depth Chart" с bid слева (зелёный) и ask справа (красный).

**Почему важно:** Позволяет визуально оценить ликвидность — где стены ордеров, где пустоты. Дополняет L2 Slippage Estimator визуалом.

**Реализация:**
- Backend: `/api/depth` уже существует. Нужно только добавить кэширование.
- Frontend: компонент `DepthChart` на Lightweight Charts (area chart)
- Интеграция: модальное окно при клике на "Depth" в Spread Monitor (как DOM Modal уже есть)

**Сложность:** 1-2 дня
**Файлы:** `frontend/src/DepthChart.tsx`, `frontend/src/pages/SpreadMonitorPage.tsx`

---

## P2 — Сильно улучшает UX

### 4. Open Interest Tracking (от Coinglass)

**Что:** Отслеживание Open Interest (OI) по фьючерсным контрактам. Coinglass показывает OI по всем биржам с историей.

**Почему важно:** Рост OI + рост цены = сильный тренд. Падение OI + рост цены = шорт-сквиз. Для арбитража важно понимать контекст рынка.

**Реализация:**
- Backend: данные OI доступны через MEXC API (`/api/v3/openInterest`). Нужен endpoint `/api/open-interest?symbol=BTCUSDT`
- Frontend: виджет OI на Spread Sniper странице + история OI на графике
- Алерты: уведомление при резком росте/падении OI

**Сложность:** 2-3 дня
**Файлы:** `backend/main.py`, `frontend/src/components/OIWidget.tsx`

---

### 5. Spread Alert Rules (от Aicoin/ArbitrageScanner)

**Что:** Настраиваемые правила алертов: "уведомить когда спред BTCUSDT между MEXC и Binance > 50 bps дольше 5 минут".

**Почему важно:** Сейчас алерты работают только по порогу. Нужны комбинированные правила: спред + lifetime + объём.

**Реализация:**
- Backend: `AlertRule` model (symbol, exchanges, spread_threshold_bps, min_lifetime_sec, min_volume_usdt)
- Frontend: UI для создания/редактирования правил на странице Alerts
- Engine: проверка правил при каждом snapshot, отправка в Telegram

**Сложность:** 2-3 дня
**Файлы:** `mexc_monitor/alerts/rules.py`, `backend/main.py`, `frontend/src/pages/AlertsPage.tsx`

---

### 6. Multi-Account Trading (от Aicoin)

**Что:** Торговля с нескольких аккаунтов на одной бирже (или разных биржах) одновременно.

**Почему важно:** Для арбитража часто нужно иметь балансы на нескольких биржах. Управление из одного места удобнее.

**Реализация:**
- Backend: расширить `EngineRegistry` — несколько движков на одну биржу с разными API ключами
- Frontend: выбор аккаунта в Trading Admin
- Risk: агрегированный риск по всем аккаунтам

**Сложность:** 3-5 дней
**Файлы:** `mexc_monitor/trading/engine_registry.py`, `backend/main.py`, `frontend/src/pages/TradingPage.tsx`

---

## P3 — Nice-to-have

### 7. Backtest Engine (от Hummingbot)

**Что:** Тестирование стратегий на исторических данных. "Какой была бы прибыль, если бы я торговал BTCUSDT с порогом 30 bps за последний месяц?"

**Почему важно:** Позволяет оптимизировать параметры стратегий без риска реальных денег.

**Реализация:**
- Backend: модуль `backtest/engine.py` — replay исторических snapshots с параметрами стратегии
- Frontend: UI для запуска бэктеста + отчёт (PnL, win rate, max drawdown)
- Данные: используются snapshot'ы из SQLite

**Сложность:** 5-7 дней
**Файлы:** `mexc_monitor/backtest/engine.py`, `backend/main.py`, `frontend/src/pages/BacktestPage.tsx`

---

### 8. Funding Rate Arbitrage Screener (от ArbitrageScanner)

**Что:** Автоматический поиск пар с самым высоким funding rate для funding arbitrage (long spot + short perp).

**Почему важно:** Funding arbitrage — одна из самых прибыльных стратегий. Сейчас данные есть, но нет автоматического скринера.

**Реализация:**
- Backend: `/api/funding-arb/screener` — ранжирование пар по funding rate с учётом спреда и ликвидности
- Frontend: таблица на Funding странице с колонками: symbol, exchange, funding_rate, annualized_apy, spread_bps, recommendation

**Сложность:** 2-3 дня
**Файлы:** `backend/main.py`, `frontend/src/pages/FundingPage.tsx`

---

### 9. Responsive Mobile UI (от Coinglass/Aicoin)

**Что:** Адаптивный дизайн для мобильных устройств. Сейчас sidebar скрывается на мобильных, но таблицы не оптимизированы.

**Почему важно:** Быстрая проверка спредов с телефона, не открывая ноутбук.

**Реализация:**
- Карточки вместо таблиц на мобильных (tiles view уже есть)
- Bottom navigation вместо sidebar
- Swipe-жесты для навигации

**Сложность:** 3-5 дней
**Файлы:** `frontend/src/components/Layout.tsx`, `frontend/src/components/MobileDrawer.tsx`, `frontend/src/pages/*.tsx`

---

## Сводная таблица

| # | Фича | Источник | Ценность | Сложность | Приоритет |
|---|------|----------|----------|-----------|-----------|
| 1 | Funding Rate Heatmap | Coinglass | Высокая | ✅ РЕАЛИЗОВАНО | **P1** |
| 2 | Spread History Time-Series | ArbitrageScanner | Высокая | ✅ РЕАЛИЗОВАНО | **P1** |
| 3 | Depth Chart Visualization | Coinglass | Средняя | ✅ РЕАЛИЗОВАНО | **P1** |
| 4 | Open Interest Tracking | Coinglass | Средняя | ✅ РЕАЛИЗОВАНО | P2 |
| 5 | Spread Alert Rules | Aicoin | Средняя | ✅ РЕАЛИЗОВАНО | P2 |
| 6 | Multi-Account Trading | Aicoin | Средняя | ✅ РЕАЛИЗОВАНО | P2 |
| 7 | Backtest Engine | Hummingbot | Высокая | ✅ РЕАЛИЗОВАНО | P3 |
| 8 | Funding Arb Screener | ArbitrageScanner | Высокая | ✅ РЕАЛИЗОВАНО | P3 |
| 9 | Responsive Mobile UI | Coinglass | Низкая | ✅ РЕАЛИЗОВАНО | P3 |
| 10 | Order Book Density Analysis | Coinglass/Aicoin | Высокая | ✅ РЕАЛИЗОВАНО | **P2** |

---

## Сводная таблица

| # | Фича | Источник | Ценность | Сложность | Приоритет |
|---|------|----------|----------|-----------|-----------|
| 1 | Funding Rate Heatmap | Coinglass | Высокая | ✅ РЕАЛИЗОВАНО | **P1** |
| 2 | Spread History Time-Series | ArbitrageScanner | Высокая | ✅ РЕАЛИЗОВАНО | **P1** |
| 3 | Depth Chart Visualization | Coinglass | Средняя | ✅ РЕАЛИЗОВАНО | **P1** |
| 4 | Open Interest Tracking | Coinglass | Средняя | ✅ РЕАЛИЗОВАНО | P2 |
| 5 | Spread Alert Rules | Aicoin | Средняя | ✅ РЕАЛИЗОВАНО | P2 |
| 6 | Multi-Account Trading | Aicoin | Средняя | ✅ РЕАЛИЗОВАНО | P2 |
| 7 | Backtest Engine | Hummingbot | Высокая | ✅ РЕАЛИЗОВАНО | P3 |
| 8 | Funding Arb Screener | ArbitrageScanner | Высокая | ✅ РЕАЛИЗОВАНО | P3 |
| 9 | Responsive Mobile UI | Coinglass | Низкая | ✅ РЕАЛИЗОВАНО | P3 |
| 10 | Order Book Density Analysis | Coinglass/Aicoin | Высокая | ✅ РЕАЛИЗОВАНО | **P2** |

---

## Рекомендуемый порядок реализации

1. ~~**Spread History Time-Series**~~ ✅ РЕАЛИЗОВАНО
2. ~~**Funding Rate Heatmap**~~ ✅ РЕАЛИЗОВАНО
3. ~~**Depth Chart Visualization**~~ ✅ РЕАЛИЗОВАНО
4. ~~**Open Interest Tracking**~~ ✅ РЕАЛИЗОВАНО
5. ~~**Spread Alert Rules**~~ ✅ РЕАЛИЗОВАНО
6. ~~**Multi-Account Trading**~~ ✅ РЕАЛИЗОВАНО
7. ~~**Backtest Engine**~~ ✅ РЕАЛИЗОВАНО
8. ~~**Funding Arb Screener**~~ ✅ РЕАЛИЗОВАНО
9. ~~**Responsive Mobile UI**~~ ✅ РЕАЛИЗОВАНО
10. ~~**Order Book Density Analysis**~~ ✅ РЕАЛИЗОВАНО

**Все фичи реализованы!**

---

### 10. Order Book Density Analysis — детальные подзадачи

Анализ концентрации ликвидности в стакане. Автоматический поиск стен, визуализация плотности, сравнение между биржами.

---

#### 10.1. Wall Detection — авто-поиск стен в стакане

**Что:** Алгоритм обнаружения уровней с аномально крупными ордерами (нотация ≥ N× медианы).

**Реализация:**
- Backend: функция `detect_walls(bids, asks, multiplier=5)` в `mexc_monitor/density.py`
- Алгоритм: вычислить медиану нотации по всем уровням, вернуть уровни где нотация ≥ median × multiplier
- Результат: список `{side, price, qty, notional, ratio_to_median}`
- Endpoint: `GET /api/density/walls?symbol=BTCUSDT&market=spot&multiplier=5`

**Файлы:** `mexc_monitor/density.py`, `backend/main.py`
**Сложность:** 0.5 дня

---

#### 10.2. Density Stats — статистика плотности стакана

**Что:** Агрегированные метрики плотности: общая ликвидность bid/ask, коэффициент дисбаланса, средняя нотация уровня.

**Реализация:**
- Backend: функция `compute_density_stats(bids, asks)` в `mexc_monitor/density.py`
- Метрики: `total_bid_notional`, `total_ask_notional`, `bid_ask_ratio`, `avg_level_notional`, `median_level_notional`, `levels_count`
- Endpoint: `GET /api/density/stats?symbol=BTCUSDT&market=spot`

**Файлы:** `mexc_monitor/density.py`, `backend/main.py`
**Сложность:** 0.5 дня

---

#### 10.3. Density Chart — тепловая карта плотности по ценовым уровням

**Что:** Визуализация нотации на каждом ценовом уровне как горизонтальные полосы (bar chart). Зелёные — bid, красные — ask.

**Реализация:**
- Frontend: компонент `DensityChart` на основе HorizontalBarSeries из lightweight-charts (или кастомный SVG)
- Каждый уровень = полоса, ширина = нотация в USDT
- Цвет: зелёный (bid) / красный (ask), насыщенность пропорциональна нотации
- Интеграция: в DomModal как третий вид (Table / Depth / Density)

**Файлы:** `frontend/src/components/charts/DensityChart.tsx`, `frontend/src/DomModal.tsx`
**Сложность:** 1 день

---

#### 10.4. Cross-Exchange Density Comparison — сравнение ликвидности между биржами

**Что:** Для одного символа показать плотность стакана на разных биржах рядом. Позволяет выбрать биржу с лучшей ликвидностью.

**Реализация:**
- Backend: `GET /api/density/compare?symbol=BTCUSDT&exchanges=mexc,binance,bybit`
- Параллельный запрос depth к нескольким биржам (аналогично `/api/snapshot/multi`)
- Результат: для каждой биржи `{exchange, total_bid, total_ask, bid_ask_ratio, wall_count}`
- Frontend: таблица сравнения на Multi-Exchange странице или отдельная секция

**Файлы:** `backend/main.py`, `frontend/src/pages/MultiExchangePage.tsx`
**Сложность:** 0.5 дня

---

#### 10.5. Density Alerts — уведомления о появлении/исчезновении стен

**Что:** Мониторинг стакана в реальном времени. Уведомление в Telegram когда:
- Появляется стена (нотация ≥ порога)
- Стена исчезает (ордер снят)
- Стена пробита (ордер исполнен)

**Реализация:**
- Backend: фоновый поток `DensityWatcher` в `mexc_monitor/density_watcher.py`
- Polling depth каждые 5-10 сек, сравнение с предыдущим состоянием
- Интеграция с `alerts/service.py` для отправки в Telegram
- Конфиг: `density_alert_enabled`, `density_alert_min_notional_usdt`, `density_alert_symbols`
- Endpoint: `POST /api/density/alerts/start`, `POST /api/density/alerts/stop`

**Файлы:** `mexc_monitor/density_watcher.py`, `mexc_monitor/alerts/service.py`, `backend/main.py`
**Сложность:** 1 день

---

#### 10.6. Density History — история изменения плотности уровня

**Что:** Трекинг как нотация на конкретном ценовом уровне меняется во времени. Позволяет увидеть: стена растёт (накопление), стена тает (распределение), стена исчезла (снятие ордера).

**Реализация:**
- Backend: in-memory ring buffer для density snapshots (аналогично spread_buffer)
- Хранить `{timestamp_ms, price, side, notional}` для топ-N уровней
- Endpoint: `GET /api/density/history?symbol=BTCUSDT&price=64000&side=bid&last_n=100`
- Frontend: sparkline-график нотации уровня во времени

**Файлы:** `mexc_monitor/density_buffer.py`, `backend/main.py`
**Сложность:** 0.5 дня

---

#### 10.7. Density в Spread Monitor — интеграция в главную таблицу

**Что:** Добавить колонки плотности в таблицу Spread Monitor: "Wall Bid" (крупнейший bid-уровень в USDT), "Wall Ask" (крупнейший ask-уровень в USDT).

**Реализация:**
- Backend: обогащение snapshot rows данными о стенах (опционально, только если depth доступен)
- Frontend: две новые колонки в `SPOTFUT_HIDEABLE_COLS`
- Цветовая кодировка: зелёный если стена > $100K, жёлтый > $50K, серый < $50K

**Файлы:** `frontend/src/pages/SpreadMonitorPage.tsx`, `backend/main.py`
**Сложность:** 0.5 дня

---

#### Сводная таблица подзадач

| # | Подзадача | Описание | Сложность |
|---|-----------|----------|-----------|
| 10.1 | Wall Detection | Алгоритм поиска стен | 0.5 дня |
| 10.2 | Density Stats | Статистика плотности | 0.5 дня |
| 10.3 | Density Chart | Тепловая карта | 1 день |
| 10.4 | Cross-Exchange Compare | Сравнение между биржами | 0.5 дня |
| 10.5 | Density Alerts | Уведомления о стенах | ✅ РЕАЛИЗОВАНО |
| 10.6 | Density History | История плотности | ✅ РЕАЛИЗОВАНО |
| 10.7 | Spread Monitor Integration | Колонки в таблице | ✅ РЕАЛИЗОВАНО |
| | **Итого** | | **4.5 дней** |

---

## Рекомендуемый порядок реализации

1. ~~**Spread History Time-Series**~~ ✅ РЕАЛИЗОВАНО
2. ~~**Funding Rate Heatmap**~~ ✅ РЕАЛИЗОВАНО
3. ~~**Depth Chart Visualization**~~ ✅ РЕАЛИЗОВАНО
4. ~~**Open Interest Tracking**~~ ✅ РЕАЛИЗОВАНО
5. ~~**Spread Alert Rules**~~ ✅ РЕАЛИЗОВАНО
6. ~~**Multi-Account Trading**~~ ✅ РЕАЛИЗОВАНО
7. ~~**Backtest Engine**~~ ✅ РЕАЛИЗОВАНО
8. ~~**Funding Arb Screener**~~ ✅ РЕАЛИЗОВАНО
9. ~~**Order Book Density Analysis**~~ ✅ РЕАЛИЗОВАНО
10. ~~**Responsive Mobile UI**~~ ✅ РЕАЛИЗОВАНО

**Все фичи реализованы!**
