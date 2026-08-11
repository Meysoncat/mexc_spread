# Сравнение подходов к реализации стратегии ProBoyScalp

> Контекст: цель — автоматизировать стратегию сбора спреда на алгоритмах MEXC
> (см. `docs/PROBOYSCALP_METHOD.md`). Data layer (`mexc_monitor/metascalp/`)
> готов на ~80%, strategy layer нужно строить. Вопрос — **где строить**.
>
> Этот документ рассматривает 5 подходов, оценивает каждый по 12 критериям,
> и даёт рекомендации под разные сценарии.

---

## Текущее решение и путь миграции (актуально)

**Решение пользователя (2026-07-19):** начать с **Варианта E** (полуручной,
детекторы сигналов без автоисполнения), стратегически склоняясь к
**Варианту B (Hummingbot)** как следующему шагу после валидации.

```
   Сейчас ────────── через 1-2 недели валидации ────────── следующая фаза
     │                       │                                   │
     ▼                       ▼                                   ▼
  Вариант E              решение B vs A                    Вариант B (Hummingbot)
  (реализован)           на основе данных                  с custom V2 Controller
  docs/.../E             с детекторов                      + backtesting
```

### Принципы на этапе E (с прицелом на B)

1. **Не вкладываться в достройку своего auto-execution layer'а**
   (cascade placer, exit manager, risk module под свой код). Это работа
   Варианта A, которая при переходе на B окажется невостребованной.

2. **Калибровать пороги и watchlist** — они переедут в параметры V2 Controller.

3. **Сохранять историю сигналов и стен** (`density_buffer.py`) — данные
   понадобятся для backtesting в Hummingbot.

### Что мигрирует при переходе E → B

| Артефакт из E | Куда в B (Hummingbot) |
|---|---|
| `min_notional_usdt`, `multiplier`, `min_spread_bps` (из `metascalp_signals.json`) | Параметры custom V2 Controller |
| Watchlist (VIM/LYN/GUA + новые) | Список пар для Pure MM strategy в HB |
| `spike_threshold_usdt`, `domination_ratio` | Конфиг participant detection внутри Controller |
| `density.py` (`detect_walls`, `compute_density_stats`) | Логика переносится в Controller, вызов через `get_order_book()` HB API |
| Telegram-алерты | HB имеет свой Telegram (Condor), но можно оставить свой формат |
| История стен (`density_buffer.py`) | Источник для backtest в HB |

### Что меняется архитектурно при переходе E → B

```
Вариант E (сейчас):                     Вариант B (после миграции):
┌─────────────────────────┐             ┌─────────────────────────┐
│  Твой FastAPI/React     │             │  Твой FastAPI/React     │
│  + density_scanner      │             │  + мониторинг HB статуса│
│  + participant_detector │             │  (через hummingbot-api) │
├─────────────────────────┤             ├─────────────────────────┤
│  MetaScalp desktop      │             │  Hummingbot + V2 Ctrl   │
│  (REST/WS на localhost) │             │  (MEXC connector,       │
├─────────────────────────┤             │   backtest, risk)       │
│  MEXC (через MetaScalp) │             ├─────────────────────────┤
                          │             │  MEXC (напрямую)        │
                          │             └─────────────────────────┘
```

**Ключевая разница:** в E стакан приходит через MetaScalp (т.к. терминал уже
есть). В B Hummingbot сам подключается к MEXC — MetaScalp становится
необязательным (или остаётся только для ручного овервью).

### Критерий перехода E → B

Переходить к B когда:
- ✅ Детекторы сигналов валидированы на 1-2 неделях живого рынка
- ✅ Срабатывают на плюсующих парах (VIM/LYN/GUA), не срабатывают на
  проигравших (OSMO/TROLLSOL/BP/R2)
- ✅ Появилось понимание частоты сигналов и реальной плотности возможностей
- ✅ Готов инвестировать 1-2 недели в изучение Hummingbot V2 framework

### Что изучать параллельно с валидацией E (без спешки)

- [Hummingbot V2 Strategies architecture](https://hummingbot.org/strategies/v2-strategies/) — Controllers / Executors / Scripts
- [Custom V2 Controller examples](https://www.botcamp.xyz/strategies) (Botcamp)
- [Pure Market Making](https://hummingbot.org/strategies/v1-strategies/pure-market-making/) — post-only / maker-only
- [Avellaneda-Stoikov source](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/strategy/avellaneda_market_making/avellaneda_market_making_config_map_pydantic.py) — inventory skew математика
- [Hummingbot API](https://github.com/hummingbot/hummingbot-api) — для опционального моста к твоему UI (Вариант C позже)

---

## Подходы (5 вариантов)

### A. Доработать текущий код: новый `mexc_monitor/spread_collector/`

Использует `MetaScalpClient` + `ws_client` как data layer. Strategy layer
пишется с нуля поверх: density detector, participant detector, cascade
placer, exit logic, risk module.

```
┌──────────────────────────────────────┐
│  SpreadCollectorEngine (новый)       │  ← стратегия ProBoyScalp
│   • density_detector.py              │
│   • participant_detector.py          │
│   • cascade_placer.py                │
│   • exit_manager.py                  │
│   • risk.py                          │
├──────────────────────────────────────┤
│  MetaScalpClient + ws_client (есть)  │  ← data layer
├──────────────────────────────────────┤
│  MetaScalp desktop app               │  ← execution
└──────────────────────────────────────┘
```

**Исполнение** идёт через MetaScalp (который сам подключён к MEXC).

### B. Перейти на Hummingbot, писать Custom V2 Controller

Использует Hummingbot как execution + data layer. Strategy layer = кастомный
V2 Controller (унаследованный от `StrategyV2Base`), который читает стакан
через `get_order_book()`, реализует детекторы плотностей и каскад лимиток.

```
┌──────────────────────────────────────┐
│  Custom V2 Controller (новый)        │  ← стратегия ProBoyScalp
│   • on_tick() → detect density       │
│   • place cascade via Executors      │
├──────────────────────────────────────┤
│  Hummingbot framework                │  ← execution + data + risk + backtest
│   • MEXC connector (spot)            │
│   • OrderBook, TradeTick             │
│   • Backtesting engine               │
│   • Risk, Inventory skew             │
├──────────────────────────────────────┤
│  MEXC API (прямо)                    │  ← execution
└──────────────────────────────────────┘
```

**Исполнение** идёт напрямую через MEXC connector Hummingbot (минуя MetaScalp).

### C. Hummingbot + твой UI (гибрид)

Hummingbot торгует, твой FastAPI/React мониторит статус через
`hummingbot-api` (REST backend Hummingbot). По сути Вариант B + тонкая
обёртка для красивого UI.

### D. Гибрид: MetaScalp как data + Hummingbot как execution

Использовать MetaScalp только для **наблюдения** (детекция плотностей и
участников), а ордера выставлять через Hummingbot. Сложно: два процесса,
синхронизация состояния, две точки отказа.

### E. Ручная торговля + твой код как помощник-анализатор

Не автоматизировать исполнение. Твой код **только обнаруживает сигналы**
(density, participant, maker share drop) и алертит в Telegram/UI.
Ордера ставишь руками в MetaScalp. Это **минимально-рискованный** путь
для $200.

---

## Сравнение по 12 критериям

Оценки: ✅ хорошо / ⚠️ средне или с оговорками / ❌ плохо или нет.

| Критерий | A. Своий код | B. Hummingbot | C. HB + UI | D. Гибрид | E. Полуручной |
|---|---|---|---|---|---|
| **1. Бесшовный ProBoy-сценарий** (плотности, каскад, выход) | ✅ полный контроль | ⚠️ Controller пишет custom | ⚠️ Controller + bridge | ⚠️ сложно | ✅ руками, гибко |
| **2. MEXC execution** | ✅ через MetaScalp | ✅ native connector | ✅ | ⚠️ двойной путь | ✅ через MetaScalp |
| **3. MetaScalp как источник** (плотности, кластеры, лента) | ✅ уже встроен | ❌ не использует | ❌ | ✅ | ✅ |
| **4. Backtest на истории** | ⚠️ свой `backtest.py` минимальный | ✅ зрелый engine | ✅ | ⚠️ частично | ❌ нет |
| **5. Risk management (kill-switch, inventory skew, PnL/день)** | ❌ писать с нуля | ✅ есть (Avellaneda AS) | ✅ | ⚠️ | ❌ дисциплина руками |
| **6. WS reconnect / heartbeat** | ❌ нет сейчас | ✅ зрелый | ✅ | ⚠️ двойной | n/a |
| **7. Порог входа для тебя** | ✅ твой код, ты знаешь | ⚠️ учить V2 framework | ⚠️ + bridge | ❌ двойная кривая | ✅ минимальный |
| **8. Поддержка / community** | ❌ только ты | ✅ Discord, docs, Botcamp | ✅ | ⚠️ | n/a |
| **9. Скорость до работающего MVP** | ⚠️ 2-3 недели | ⚠️ 1-2 недели + learning | ⚠️ 3-4 недели | ❌ 4-6 недель | ✅ 1-2 дня |
| **10. Зависимости / внешний код** | ✅ только твой | ❌ большой фреймворк | ❌ | ❌ | ✅ |
| **11. Обучающий эффект** (понимать каждую строчку) | ✅ максимальный | ⚠️ чёрный ящик местами | ⚠️ | ⚠️ | ✅ |
| **12. Масштабируемость** ($1000+, новые пары, новые стратегии) | ⚠️ свой код = свой потолок | ✅ зрелый | ✅ | ⚠️ | ❌ потолок рук |

---

## Подробности по каждому подходу

### A. Своий код: `mexc_monitor/spread_collector/`

**Плюсы:**
- Ты **уже знаешь кодовую базу**, не нужно переучиваться.
- `MetaScalpClient` уже работает с тем терминалом, которым пользуется ProBoyScalp.
- Полный контроль над каждой строчкой → лёгкий дебаг.
- Не нужно тащить гигантский фреймворк Hummingbot.
- Архитектура уже слоистая (REST/WS/cache/poller/bridge), просто добавляется
  strategy layer сверху.

**Минусы:**
- WS reconnect, heartbeat, backtest engine, risk module — **всё с нуля**.
- Нет community-поддержки (только ты сам).
- Риск изобрести велосипед (Avellaneda-Stoikov, inventory skew — уже решённые
  проблемы в Hummingbot).

**Когда выбрать:** если хочешь максимум контроля, готов инвестировать 2-3
недели, и для тебя важен **обучающий эффект** (понимать, как работает
каждый компонент стратегии).

### B. Hummingbot + Custom V2 Controller

**Плюсы:**
- **Зрелая инфраструктура**: MEXC connector, OrderBook, TradeTick, Backtesting,
  Risk, Inventory skew, WS reconnect — всё есть.
- Avellaneda-Stoikov уже реализован → можно **наследоваться или
  заимствовать** математику inventory risk.
- Pure MM с **post-only / maker-only** из коробки.
- **Community + Discord + Botcamp** — можно спросить, посмотреть примеры.
- Backtesting engine позволяет **проверить edge на истории** до live-торговли.

**Минусы:**
- ⚠️ **Strategies V2 — это Python+Cython фреймворк**, нужно выучить API
  (`StrategyV2Base`, `Executor`, `Controller`, `get_order_book()`).
- ⚠️ **В Hummingbot НЕТ готовой density detection** — её нужно писать
  через custom Controller (как и в своём коде). Это **главный подвох**:
  Hummingbot не даёт ProBoy-стратегию «из коробки».
- ❌ **MetaScalp не используется** — Hummingbot сам тянет стакан с MEXC.
  Потеряешь кластеры и те данные, которые у тебя уже есть.
- ❌ Большой внешний dependency (27k+ коммитов, сложная кодовая база).

**Когда выбрать:** если хочешь **зрелую инфраструктуру и backtesting**,
готов потратить 1-2 недели на обучение V2 framework, и тебя не смущает
что MetaScalp останется в стороне.

### C. Hummingbot + твой UI (гибрид)

**Плюсы:**
- Лучшее из обоих: **исполнение Hummingbot** + **твой красивый UI**.
- Можно добавить визуализацию сигналов (density, participant) в React.

**Минусы:**
- ❌ **Двойная работа**: писать и V2 Controller, и bridge к `hummingbot-api`.
- ❌ Самый долгий путь (3-4 недели до MVP).
- ❌ Двойная точка отказа (твой backend + Hummingbot).

**Когда выбрать:** только если для тебя критичен красивый личный UI, и
ты готов инвестировать максимум времени. **Не рекомендуется как первый
шаг.**

### D. Гибрид: MetaScalp + Hummingbot

**Плюсы:**
- Использует сильные стороны обоих (MetaScalp-данные + HB-execution).

**Минусы:**
- ❌ **Самый сложный архитектурно**: синхронизация между двумя процессами.
- ❌ 4-6 недель работы, много отладки edge cases.
- ❌ Несоответствие модели: MetaScalp мыслит connections+tickers, Hummingbot —
  connectors+strategies.

**Когда выбрать:** **никогда как первый шаг.** Только если у тебя будет
специфическое требование, которое нельзя решить иначе. По факту это
оверинжиниринг для $200 депозита.

### E. Полуручной: твой код = детектор сигналов

**Плюсы:**
- ✅ **Самый быстрый старт** (1-2 дня): дописать density + participant
  детекторы поверх уже работающего MetaScalp-модуля, вывести в UI/Telegram.
- ✅ **Минимальный риск**: ордера руками, ты видишь каждую сделку.
- ✅ Можно валидировать **детекторы** на реальном рынке, прежде чем
  автоматизировать исполнение.
- ✅ Обучающий эффект максимальный: ты руками проходишь каждую сделку,
  формируешь интуицию.
- ✅ Не нужен ни Hummingbot, ни достройка auto_trader.

**Минусы:**
- ❌ Не масштабируется (руки — потолок).
- ❌ Дисциплина убивается эмоциями (твой аудит это и показал: 11 мая −17 USDT
  за один день, потому что не остановился).

**Когда выбрать:** **как первый шаг в любом случае.** Это даёт быстрый
feedback loop, валидирует детекторы и снижает риск. Дальше переходить
к A или B.

---

## Честные выводы

### Что я бы НЕ рекомендовал

- **D (полный гибрид MetaScalp+HB)** — оверинжиниринг для $200.
- **C (HB+UI)** как первый шаг — слишком долго, двойная работа.

### Реальная дилемма: A vs B

Если убрать крайние варианты, остаётся **A (своий код)** vs **B (Hummingbot)**.
Это ключевой выбор, и **правильного ответа для всех нет** — зависит от твоих
приоритетов.

| Если для тебя важнее... | Выбирай |
|---|---|
| Быстрый старт, минимальный риск, валидация детекторов | **E** (обязательно как первый шаг) |
| Максимальный контроль, понимание каждой строчки, минимум зависимостей | **A** |
| Зрелая инфраструктура, backtesting, community, готовая risk-математика | **B** |
| Красивый личный UI | **C** (после B) |
| Гибрид данных и исполнения | **D** (никогда) |

### Моя рекомендация (пошаговая)

**Шаг 1 (1-2 дня): Вариант E** — дописать density + participant детекторы
поверх MetaScalp, выводить в UI/Telegram. Валидировать на живом рынке, что
сигналы коррелируют с реальными возможностями (и НЕ срабатывают на парах
типа OSMO/TROLLSOL).

**Шаг 2 (после валидации детекторов, 1 неделя):** принять решение A vs B.
К тому моменту у тебя будет:
- Работающие детекторы (исходник для обоих вариантов).
- Понимание, какие данные критичны (cluster? depth? trade tape?).
- Реальная обратная связь от нескольких торговых сессий.

**Шаг 3 (2-3 недели):** реализовать стратегию в выбранном подходе (A или B),
сначала paper, потом live на $5-10.

### Почему сначала E, а не сразу A или B

- **Дешёвая валидация**: 2 дня vs 2-3 недели.
- **Снимает риск «строить на неверной модели»**: если детекторы не работают
  на живом рынке, не придётся выбрасывать 3 недели работы.
- **Даёт критическую информацию для выбора A vs B**: поймёшь, насколько тебе
  реально нужен backtest (если данные летучие и редкие — backtest на
  истории может быть бесполезен → A). Если данные регулярные и
  воспроизводимые → B с его backtesting становится сильнее.

---

## Сводная матрица принятия решения

```
                  Быстрый старт ────────────────── Зрелость/масштаб
                       │                                  │
                       E ─── A ───────────── B ─── C ─── D
                       │    │                │     │     │
                       │    │                │     │     ❌ оверинжиниринг
                       │    │                │     ❌ долго как первый шаг
                       │    │                ✅ если важен backtest + risk
                       │    ✅ если важен контроль + минимум зависимостей
                       ✅ первый шаг для любого пути
```

## Источники

- [Hummingbot V2 Strategies](https://hummingbot.org/strategies/v2-strategies/) — архитектура Controllers/Executors
- [Pure Market Making](https://hummingbot.org/strategies/v1-strategies/pure-market-making/) — bid/ask spread capture, post-only
- [Avellaneda Market Making](https://hummingbot.org/strategies/v1-strategies/avellaneda-market-making/) — inventory skew, order book liquidity estimator
- [Botcamp Strategies](https://www.botcamp.xyz/strategies) — примеры custom Controllers с order book depth analysis
- [Hummingbot API (для Варианта C)](https://github.com/hummingbot/hummingbot-api) — REST backend
- [Hanging Orders Tracker](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/strategy/hanging_orders_tracker.py) — готовый код трекинга висящих ордеров
- [Avellaneda MM config (GitHub)](https://github.com/hummingbot/hummingbot/blob/master/hummingbot/strategy/avellaneda_market_making/avellaneda_market_making_config_map_pydantic.py) — параметры inventory skew
