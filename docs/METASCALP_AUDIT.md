# Аудит модуля `mexc_monitor/metascalp/` — готовность к стратегии ProBoyScalp

> Источник: детальный аудит 8 файлов модуля (1333 строк) через два независимых
> Explore-агента + чтение ключевых файлов.
>
> Контекст: оцениваем, насколько текущий модуль MetaScalp-интеграции готов к
> реализации стратегии ProBoyScalp (сбор спреда на алгоритмах MEXC — см.
> `docs/PROBOYSCALP_METHOD.md`).

---

## TL;DR

**Data layer готов на ~80%. Strategy layer готов на ~5-10%.**

Текущий модуль — это **транспорт и кэш**, а не торговая стратегия. Он может
общаться с MetaScalp (REST + WS), кэшировать состояние аккаунта, выставлять
одиночные ордера и авто-срабатывать на внешние signal-levels. Но **вся «мозговая
часть» стратегии ProBoyScalp отсутствует** — детектирование плотностей,
участников, прострелов, каскад лимиток, выход при разъедании плотности,
maker-only enforcement, kill-switch по maker share, дневной лимит PnL — всё
это нужно писать с нуля.

```
┌──────────────────────────────────────────────────────────────────┐
│                  Strategy layer (≈90% нужно дописать)             │
│   • Density detector                                              │
│   • Participant/algo detector          ┌────────────────────────┐ │
│   • Cascade order placer               │   НЕТ (built с нуля)   │ │
│   • Exit-on-density-eaten              └────────────────────────┘ │
│   • Maker-only enforcement                                        │
│   • Kill-switches (maker share, PnL/day, inventory time)          │
├──────────────────────────────────────────────────────────────────┤
│                  Data layer (готово ~80%)                         │
│   • orderbook_snapshot()  ✅  полный стакан                       │
│   • cluster_snapshot()    ✅  volume profile                      │
│   • subscribe_trades()    ✅  лента сделок (WS)                   │
│   • subscribe_orderbook() ✅  дельта стакана (WS)                 │
│   • place_order/cancel    ✅  одиночные ордера                    │
│   • Cache + Poller + WS Bridge  ✅  базовый                       │
└──────────────────────────────────────────────────────────────────┘
```

---

## 1. Что модуль УЖЕ умеет (data layer)

### 1.1. REST-клиент `client.py` (362 строки)

| Метод | Что даёт | Готовность |
|---|---|---|
| `orderbook_snapshot(conn_id, ticker)` | **Полный стакан** (все asks + bids как `MetaScalpOrderbookLevel(price, size, type)`), плюс best_ask/best_bid | ✅ |
| `cluster_snapshot(conn_id, ticker)` | Volume profile по цене (rows — list[dict], нетипизированные) | ✅ |
| `balance(conn_id)` | Баланс по монетам | ✅ |
| `orders(conn_id, ticker=None)` | Открытые ордера | ✅ |
| `positions(conn_id)` | Открытые позиции | ✅ |
| `signal_levels(conn_id, ticker)` | Уровни сигналов (алерты MetaScalp) | ✅ |
| `place_order(conn_id, ticker, side, order_type, size, price=None)` | **Выставление ордера** (Limit или Market) | ✅ |
| `cancel_order` / `cancel_all_orders` | Снятие ордеров | ✅ |
| `place_signal_level` / `remove_signal_level` / `remove_all_signal_levels` | Управление уровнями сигналов | ✅ |
| `connections()` | Список подключений (MEXC/Gate/BingX/...) | ✅ |

**Авто-обнаружение** MetaScalp на портах `17845-17855` локалхоста с
double-checked-locked кэшем + негативный TTL (client.py:28-113) — хорошо.

**Auth — нет.** localhost-only, exchange-ключи живут внутри десктопного
MetaScalp. Этот клиент просто driver'ит MetaScalp.

### 1.2. WS-клиент `ws_client.py` (190 строк)

| Метод | Что даёт |
|---|---|
| `subscribe_connection` | Обновления подключения |
| `subscribe_trades(ticker)` | **Лента сделок** (нужно для детекции участников) |
| `subscribe_orderbook(ticker, depth_levels, depth_percent, zoom_index)` | **Дельта стакана** (дешевле, чем re-poll) |
| `subscribe_mark_price` / `subscribe_funding` | Futures-специфика |
| `subscribe_notifications` / `subscribe_signal_levels` | Алерты и триггеры уровней |

### 1.3. Кэш + Poller + Bridge

- `cache.py` — TTL-кэш (default 10s, orderbook 2s, cluster 5s, signal_levels 5s)
- `poller.py` — каждые 5s тянет balance/orders/positions (но НЕ стакан — он on-demand)
- `ws_bridge.py` — подписывается на connection/signal_levels/notifications, **инвалидирует кэш** при WS-пуше

### 1.4. Что готово ВЫВОДИТЬ для стратегии ProBoyScalp

| Нужный сигнал | Источник в модуле | Статус |
|---|---|---|
| Стакан (для поиска плотностей) | `orderbook_snapshot` / `subscribe_orderbook` | ✅ есть |
| Volume profile (для кластеров) | `cluster_snapshot` | ✅ есть |
| Лента сделок (для участников) | `subscribe_trades` | ✅ есть |
| Single order placement | `place_order(Limit, price, size)` | ✅ есть |
| Cancel / cancel-all | есть | ✅ есть |

**Вывод:** **данных достаточно** для реализации стратегии. Их **обработки** нет.

---

## 2. Что ОТСУТСТВУЕТ (strategy layer)

Это и есть та работа, которую нужно сделать. Сводная таблица:

| Компонент ProBoyScalp | Статус в коде | Где взять / что писать |
|---|---|---|
| **Density detector** (плотности ≥ 1-2k USDT) | ❌ нет нигде | Обход `MetaScalpOrderbookLevel`, порог `price*size ≥ N`, трекинг add/move/cancel между snapshot'ами |
| **Participant/algo detector** (кто-то прокидывает по рынку, переставляет лимитки) | ❌ нет | Агрегация `subscribe_trades`, детекция всплесков market-ордеров + трекинг перемещения плотностей |
| **Shooting/wick detector** (длинные тени свечей) | ❌ нет | OHLCV нет; либо собирать из `subscribe_trades`, либо использовать `cluster_snapshot` |
| **Cascade order placer** (3 лимитки каскадом) | ❌ нет | Wrapper над `place_order`, сплит размера на 3, менеджер отмены неисполненного остатка |
| **Exit-on-density-eaten** | ❌ нет | Мониторинг скорости «поедания» плотности + хук на снятие/cancel-all |
| **Maker-only enforcement** | ❌ нет (default = Market!) | `default_type="Market"` (auto_trader.py:28) — это ровно противоположность стратегии |
| **Maker-share kill-switch** | ❌ нет | Счётчик fill-rate per-pair в реальном времени |
| **Kill-switch (PnL/день, время инвентаря)** | ❌ нет | Риск-модуль |
| **WS reconnect** | ❌ нет | Если MetaScalp перезапустится — стратегия тихо умрёт |
| **История стакана за 15 мин** (для ответа «какие плотности были») | ❌ нет | cache.py хранит только latest; нужен ring buffer или SQLite |

---

## 3. Качество кода — конкретные баги и пробелы

### Серьёзные

1. **`auto_trader.py:107-133` — `_process_connection` это `pass`.**
   Фоновый поток крутится каждые 2s и **ничего не делает**. Заголовок
   «MetaScalpAutoTrader» вводит в заблуждение — это не trader, это каркас.

2. **`auto_trader.py:28` — `default_type = "Market"`.** Это taker, который на
   MEXC = 5 bps убытка. Прямо противоречит edge-тезису стратегии.

3. **`ws_client.py` — нет reconnect / heartbeat.** Любой рестарт MetaScalp
   молча убивает ленту сделок.

4. **`ws_bridge.py` только инвалидирует кэш, не заполняет его.** На WS-пуш
   ордеров/позиций просто снимается кэш, и следующий poller (через 5s)
   перезаполняет. Для дашборда — ок, для sub-second стратегии — **недопустимо**.

5. **`client.py:361` `remove_triggered_signal_levels` — глобальное.** Снимает
   все триггернутые уровни по ВСЕМ подключениям, не по конкретному тикеру.
   В multi-account сетапе это баг.

### Средние

6. **`cache.py` — нет eviction.** Старые записи копятся вечно → медленная
   утечка памяти для долго работающего bridge.

7. **`models.py:44` `time` у ордеров — строка.** Любая time-based логика
   должна его парсить заново.

8. **`models.py:99` `MetaScalpClusterSnapshot.rows` — untyped `list[dict]`.**
   Схема volume profile не описана; callers должны знать её неявно.

9. **Тестов нет.** В каталоге `metascalp/` только `.py` файлы.

10. **`# TODO` / `# FIXME` отсутствуют** при наличии очевидно незаконченной
    логики. `pass` с комментарием вместо `NotImplementedError` — статический
    ревью это не подсветит.

---

## 4. Что перекрывает стратегия ProBoyScalp vs текущий auto_trader

Это **главное несоответствие**, которое надо осознать:

| | ProBoyScalp (что он делает руками) | Текущий `auto_trader.py` |
|---|---|---|
| Решение о входе | «Вижу плотность + participant в стакане» | Никакое (`_process_connection = pass`) |
| Тип ордера | LIMIT always (maker) | **Market** по умолчанию |
| Размер позиции | Делю на 3, каскад лимиток | Один ордер фиксированного размера |
| Выход | Руками, в плотность, при её разъедании | Нет |
| Stop-loss | **Без стопа** (MEXC не исполняет на неликвиде) | Не применимо |
| Риск-менеджмент | «Депозит ≤ $500», «20-40 стаканов» | `enabled: bool` |

**То есть текущий auto_trader — это не «недоделанный ProBoyScalp», это вообще
другая абстракция.** Он заточен под «внешний trigger → один ордер»
(сценарий signal-level → market order), а не под проактивную торговлю на
основе анализа стакана.

---

## 5. Сильные стороны (что можно переиспользовать)

1. **`MetaScalpClient` — solid.** Дискавери портов с негативным TTL,
   double-checked-locking, толерантность к PascalCase/camelCase, единая
   обработка ошибок. Это production-grade код.

2. **`ws_client.py` покрывает все нужные потоки** — trades, orderbook,
  signal_levels, notifications. Нужно только научиться их обрабатывать.

3. **`cache.py` — простой и потокобезопасный.** Достаточен как база, нужно
   только добавить ring buffer / историю для стратегии.

4. **Чёткая分层ная архитектура:** REST / WS / cache / poller / bridge / trader
   — каждый со своей ответственностью. Это **упрощает добавление strategy
   layer'а**, не нужно переписывать нижние слои.

---

## 6. Оценка «сколько работы» до работающей ProBoyScalp-стратегии

По компонентам (грубая оценка в «идеальных днях», без отладки под живой MetaScalp):

| Компонент | Оценка |
|---|---|
| Density detector (на основе orderbook_snapshot + WS-дельт) | 1-2 дня |
| Participant detector (агрегация trades, всплески) | 2-3 дня |
| Cascade order placer + менеджер | 1-2 дня |
| Exit logic (density-eaten monitor) | 1 день |
| Maker-only enforcement + maker-share kill-switch | 1 день |
| WS reconnect + heartbeat | 0.5 дня |
| Риск-модуль (PnL/день, inventory time-stop) | 1-2 дня |
| История стакана (ring buffer / SQLite) | 1 день |
| Backtest движок под эту стратегию (на исторических плотностях) | 2-3 дня |
| Тестирование + отладка на живом MetaScalp | 3-5 дней |
| **Итого до MVP** | **~2-3 недели сфокусированной работы** |

---

## 7. Рекомендация

Не пытаться **достроить** `auto_trader.py` — он концептуально не для этого.
Лучше:

1. **Создать новый модуль `mexc_monitor/spread_collector/`** (или
   `metascalp_strategy/`), который использует `MetaScalpClient` + `ws_client`
   как data layer, но реализует стратегию ProBoyScalp сверху:
   - `density_detector.py`
   - `participant_detector.py`
   - `cascade_placer.py`
   - `exit_manager.py`
   - `risk.py` (с kill-switch'ами)
   - `engine.py` (оркестратор)

2. **Сначала — на бумаге (paper), потом live на $5-10.** Проверить, что
   сигналы детекторов коррелируют с реальными сделками из твоего аудита
   (на парах VIM/LYN/GUA детекторы должны срабатывать; на OSMO/TROLLSOL — нет).

3. **Backtest на сохранённой истории стакана** — это критично для
   валидации без риска деньгами. Нужен ring buffer / SQLite-лог стакана за
   хотя бы неделю.

4. **Оставить `auto_trader.py` как есть** (signal-level → market order) —
   это отдельный use case, не конфликтующий с новым spread_collector.
