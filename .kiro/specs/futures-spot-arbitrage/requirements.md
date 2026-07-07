# Requirements Document

## Introduction

Модуль **Futures/Spot Arbitrage** реализует стратегии межрынкового арбитража между спотовым и фьючерсным (перпетуальным) рынками. В отличие от существующего кросс-биржевого арбитража (один инструмент на двух биржах), данная стратегия работает с **двумя разными инструментами** (спот и перп) и предполагает удержание позиции от минут до дней с накоплением funding-платежей.

Поддерживаемые стратегии:
- **Cash-and-Carry (Basis Trade):** покупка спота + шорт фьючерса при премии перпа
- **Reverse Cash-and-Carry:** шорт спота + лонг фьючерса при дисконте перпа
- **Funding Rate Arbitrage:** дельта-нейтральная позиция для сбора funding-платежей

Поддерживаемые комбинации бирж:
- MEXC Spot + MEXC Futures (одна биржа)
- MEXC Spot + AsterDEX Perp (кросс-биржа)
- AsterDEX Perp + MEXC Futures (два перп-рынка, разница funding)

## Glossary

- **Basis_Calculator**: Компонент, вычисляющий базис (разницу цен) между спотовым и фьючерсным инструментами
- **Position_Manager**: Компонент, управляющий открытыми арбитражными позициями (обе ноги)
- **Funding_Tracker**: Компонент, отслеживающий и учитывающий funding-платежи по фьючерсным позициям
- **Risk_Controller**: Компонент, контролирующий риски арбитражных позиций (маржа, базисный риск, дельта)
- **Strategy_Engine**: Основной движок, координирующий стратегии арбитража спот-фьючерс
- **Arb_Dashboard**: UI-компонент, отображающий текущие базисы, funding rates и позиции
- **Базис (basis)**: Разница цен между фьючерсом и спотом: basis = futures_mid − spot_mid
- **Базис в bps**: 10000 × (futures_mid − spot_mid) / spot_mid
- **Funding rate**: Периодический платёж между лонгами и шортами на перпетуальном контракте
- **Дельта-нейтральность**: Состояние, при котором суммарная экспозиция к цене актива равна нулю (спотовая нога компенсирует фьючерсную)
- **Нога (leg)**: Одна сторона арбитражной позиции (спотовая нога или фьючерсная нога)
- **APY (Annual Percentage Yield)**: Годовая доходность стратегии, рассчитанная из текущего базиса и funding rate
- **Exchange_Combo**: Конкретная комбинация бирж для двух ног арбитража (например, MEXC Spot + AsterDEX Perp)

## Requirements

### Requirement 1: Расчёт базиса в реальном времени

**User Story:** Как трейдер, я хочу видеть текущий базис между спотом и фьючерсом для всех мониторируемых пар, чтобы находить арбитражные возможности.

#### Acceptance Criteria

1. WHEN новые данные bid/ask поступают для спотового или фьючерсного инструмента, THE Basis_Calculator SHALL пересчитать базис в абсолютном значении (USDT) и в bps в течение 500 мс
2. THE Basis_Calculator SHALL поддерживать вычисление базиса для комбинаций: MEXC Spot + MEXC Futures, MEXC Spot + AsterDEX Perp, AsterDEX Perp + MEXC Futures
3. THE Basis_Calculator SHALL вычислять executable basis как (futures_bid − spot_ask) для cash-and-carry и (spot_bid − futures_ask) для reverse cash-and-carry с учётом комиссий обеих ног
4. WHEN базис для пары недоступен из-за отсутствия данных одной из ног, THE Basis_Calculator SHALL пометить пару статусом "stale" и не использовать устаревшее значение для принятия решений
5. THE Basis_Calculator SHALL вычислять estimated APY по формуле: APY = (basis_bps / 10000) × (365 × 24 / expected_hold_hours) × 100 для каждой мониторируемой пары

### Requirement 2: Отслеживание Funding Rate

**User Story:** Как трейдер, я хочу видеть текущие и исторические funding rates для перпетуальных контрактов, чтобы оценивать доходность funding-арбитража.

#### Acceptance Criteria

1. THE Funding_Tracker SHALL запрашивать текущий funding rate и время следующего платежа для всех мониторируемых перпетуальных контрактов с интервалом не более 60 секунд
2. THE Funding_Tracker SHALL хранить историю funding rates за последние 30 дней для каждого мониторируемого контракта
3. THE Funding_Tracker SHALL вычислять среднюю ставку funding за 7 дней и 30 дней для оценки устойчивости направления
4. WHEN funding rate меняет знак (с положительного на отрицательный или наоборот), THE Funding_Tracker SHALL генерировать событие "funding_direction_changed" с указанием символа и нового значения
5. THE Funding_Tracker SHALL вычислять annualized funding yield по формуле: yield = funding_rate × (365 × 24 / funding_interval_hours) × 100

### Requirement 3: Открытие арбитражной позиции (Cash-and-Carry)

**User Story:** Как трейдер, я хочу автоматически открывать cash-and-carry позиции при достижении порога базиса, чтобы зарабатывать на конвергенции базиса и funding-платежах.

#### Acceptance Criteria

1. WHEN executable basis превышает настроенный entry_threshold_bps И количество открытых позиций меньше max_concurrent_positions, THE Strategy_Engine SHALL открыть позицию: купить спот + открыть шорт фьючерс
2. THE Strategy_Engine SHALL выставлять обе ноги позиции атомарно: если одна нога не исполнена в течение max_leg_pending_sec, Strategy_Engine SHALL отменить неисполненную ногу и закрыть исполненную
3. THE Strategy_Engine SHALL рассчитывать размер позиции в USDT на основе настройки position_notional_usdt и распределять его между ногами с учётом плеча на фьючерсной ноге
4. WHILE позиция открыта, THE Position_Manager SHALL отслеживать текущий unrealized PNL, включающий: изменение базиса + накопленный funding − комиссии
5. THE Strategy_Engine SHALL поддерживать режимы paper (симуляция) и live (реальные ордера) с идентичной логикой принятия решений

### Requirement 4: Открытие арбитражной позиции (Reverse Cash-and-Carry)

**User Story:** Как трейдер, я хочу автоматически открывать reverse cash-and-carry позиции при значительном дисконте фьючерса, чтобы зарабатывать на обратной конвергенции базиса.

#### Acceptance Criteria

1. WHEN executable reverse basis (spot_bid − futures_ask) превышает настроенный entry_threshold_bps И количество открытых позиций меньше max_concurrent_positions, THE Strategy_Engine SHALL открыть позицию: продать спот (или продать имеющийся актив) + открыть лонг фьючерс
2. THE Strategy_Engine SHALL проверять наличие достаточного баланса спотового актива для продажи перед открытием reverse cash-and-carry позиции
3. IF баланс спотового актива недостаточен для продажи, THEN THE Strategy_Engine SHALL пропустить сигнал и записать событие "insufficient_spot_balance"

### Requirement 5: Funding Rate Arbitrage

**User Story:** Как трейдер, я хочу автоматически открывать дельта-нейтральные позиции для сбора funding-платежей, чтобы получать стабильный доход при высоких ставках funding.

#### Acceptance Criteria

1. WHEN абсолютное значение funding rate превышает настроенный funding_entry_threshold И средний funding rate за 7 дней имеет тот же знак, THE Strategy_Engine SHALL открыть дельта-нейтральную позицию: при положительном funding — лонг спот + шорт перп, при отрицательном — шорт спот + лонг перп
2. THE Strategy_Engine SHALL удерживать funding-позицию через события выплаты funding и учитывать каждый полученный платёж в PNL позиции
3. WHEN funding rate меняет знак и удерживается в противоположном направлении более 3 последовательных периодов, THE Strategy_Engine SHALL закрыть funding-позицию с причиной "funding_direction_reversed"

### Requirement 6: Закрытие арбитражной позиции

**User Story:** Как трейдер, я хочу автоматическое закрытие позиций по заданным условиям, чтобы фиксировать прибыль и ограничивать убытки.

#### Acceptance Criteria

1. WHEN текущий базис сужается до exit_threshold_bps или ниже, THE Strategy_Engine SHALL закрыть позицию с причиной "basis_converged"
2. WHEN накопленный PNL позиции (базис + funding − комиссии) достигает target_profit_bps, THE Strategy_Engine SHALL закрыть позицию с причиной "target_reached"
3. WHEN базис расширяется против позиции и превышает max_basis_divergence_bps, THE Strategy_Engine SHALL закрыть позицию с причиной "stop_loss"
4. WHEN время удержания позиции превышает max_hold_duration_hours, THE Strategy_Engine SHALL закрыть позицию с причиной "max_duration"
5. THE Strategy_Engine SHALL закрывать обе ноги позиции и записывать итоговый PNL с разбивкой: basis_pnl + funding_earned − total_fees

### Requirement 7: Контроль рисков

**User Story:** Как трейдер, я хочу автоматический контроль рисков арбитражных позиций, чтобы избежать ликвидации и чрезмерных убытков.

#### Acceptance Criteria

1. WHILE позиция открыта, THE Risk_Controller SHALL проверять margin ratio фьючерсной ноги каждые 30 секунд и генерировать алерт при margin ratio ниже настроенного margin_warning_threshold (по умолчанию 50%)
2. IF margin ratio фьючерсной ноги падает ниже margin_critical_threshold (по умолчанию 30%), THEN THE Risk_Controller SHALL принудительно закрыть позицию с причиной "margin_critical"
3. WHILE позиция открыта, THE Risk_Controller SHALL проверять дельта-нейтральность: разница размеров спотовой и фьючерсной ног не превышает max_delta_imbalance_percent (по умолчанию 5%)
4. IF дельта-нейтральность нарушена более чем на max_delta_imbalance_percent, THEN THE Risk_Controller SHALL генерировать алерт "delta_imbalance" и при превышении critical_delta_imbalance_percent (по умолчанию 15%) закрыть позицию
5. THE Risk_Controller SHALL ограничивать суммарный notional всех открытых позиций значением max_total_exposure_usdt
6. THE Risk_Controller SHALL поддерживать kill switch, при активации которого все открытые позиции закрываются и новые не открываются

### Requirement 8: Учёт PNL с funding-платежами

**User Story:** Как трейдер, я хочу видеть полный PNL каждой позиции с учётом funding-платежей, чтобы точно оценивать доходность стратегии.

#### Acceptance Criteria

1. THE Position_Manager SHALL вычислять total PNL позиции по формуле: total_pnl = basis_pnl + cumulative_funding − entry_fees − exit_fees
2. WHEN происходит событие выплаты funding на бирже, THE Funding_Tracker SHALL записать сумму платежа и обновить cumulative_funding для соответствующей позиции
3. THE Position_Manager SHALL вычислять annualized return позиции: annualized = (total_pnl / notional) × (365 × 24 × 3600 / hold_seconds) × 100
4. THE Position_Manager SHALL хранить историю всех закрытых позиций с полной разбивкой PNL: basis_pnl, funding_earned, fees_spot_leg, fees_futures_leg, net_pnl, hold_duration, close_reason

### Requirement 9: Конфигурация стратегии

**User Story:** Как трейдер, я хочу гибко настраивать параметры арбитражной стратегии, чтобы адаптировать её под текущие рыночные условия.

#### Acceptance Criteria

1. THE Strategy_Engine SHALL принимать конфигурацию через JSON-файл и переменные окружения со следующими параметрами: symbols (список пар), exchange_combo (комбинация бирж), entry_threshold_bps, exit_threshold_bps, max_basis_divergence_bps, target_profit_bps, funding_entry_threshold, max_hold_duration_hours, position_notional_usdt, max_concurrent_positions, futures_leverage, mode (paper/live)
2. WHEN конфигурация обновляется через API, THE Strategy_Engine SHALL применить новые параметры без перезапуска движка и без влияния на уже открытые позиции
3. THE Strategy_Engine SHALL валидировать конфигурацию: entry_threshold_bps больше exit_threshold_bps, position_notional_usdt больше 0, max_concurrent_positions от 1 до 20, futures_leverage от 1 до 20

### Requirement 10: Telegram-алерты для арбитража

**User Story:** Как трейдер, я хочу получать Telegram-уведомления о ключевых событиях арбитража, чтобы контролировать стратегию без постоянного наблюдения за экраном.

#### Acceptance Criteria

1. WHEN арбитражная позиция открывается, THE Strategy_Engine SHALL отправить Telegram-алерт с указанием: символ, exchange_combo, направление (cash-and-carry / reverse), entry basis bps, notional USDT, estimated APY
2. WHEN арбитражная позиция закрывается, THE Strategy_Engine SHALL отправить Telegram-алерт с указанием: символ, причина закрытия, net PNL (USDT и bps), hold duration, funding earned
3. WHEN Risk_Controller генерирует алерт уровня critical (margin_critical, delta_imbalance critical), THE Strategy_Engine SHALL отправить Telegram-алерт с пометкой "⚠️ RISK"
4. WHEN funding rate для мониторируемого символа превышает funding_alert_threshold, THE Strategy_Engine SHALL отправить Telegram-алерт с текущим funding rate и estimated annual yield

### Requirement 11: REST API для управления арбитражем

**User Story:** Как разработчик фронтенда, я хочу REST API для управления арбитражным движком, чтобы интегрировать его с React UI.

#### Acceptance Criteria

1. THE Strategy_Engine SHALL предоставлять endpoint GET /api/futures-arb/status, возвращающий: running, settings, open_positions, stats, current_basis для всех мониторируемых пар
2. THE Strategy_Engine SHALL предоставлять endpoint GET /api/futures-arb/positions, возвращающий список открытых позиций с real-time PNL, включая basis_pnl, cumulative_funding, unrealized_total
3. THE Strategy_Engine SHALL предоставлять endpoint GET /api/futures-arb/history с параметрами limit и offset, возвращающий закрытые позиции с полной разбивкой PNL
4. THE Strategy_Engine SHALL предоставлять endpoint POST /api/futures-arb/start и POST /api/futures-arb/stop для управления движком
5. THE Strategy_Engine SHALL предоставлять endpoint PATCH /api/futures-arb/settings для обновления конфигурации в runtime
6. THE Strategy_Engine SHALL предоставлять endpoint GET /api/futures-arb/basis-history с параметрами symbol, exchange_combo, interval, limit для получения исторических значений базиса

### Requirement 12: UI — Дашборд базиса и funding

**User Story:** Как трейдер, я хочу видеть дашборд с текущими базисами и funding rates для всех мониторируемых пар, чтобы быстро оценивать рыночную ситуацию.

#### Acceptance Criteria

1. THE Arb_Dashboard SHALL отображать таблицу мониторируемых пар с колонками: символ, exchange_combo, spot_mid, futures_mid, basis_bps, funding_rate, estimated_apy, status (active/stale)
2. THE Arb_Dashboard SHALL поддерживать сортировку таблицы по basis_bps, funding_rate и estimated_apy
3. THE Arb_Dashboard SHALL обновлять данные каждые 3 секунды без перезагрузки страницы
4. THE Arb_Dashboard SHALL выделять цветом строки, где basis_bps превышает entry_threshold_bps (зелёный для cash-and-carry, красный для reverse)

### Requirement 13: UI — Менеджер позиций

**User Story:** Как трейдер, я хочу видеть все открытые арбитражные позиции с real-time PNL, чтобы контролировать текущее состояние стратегии.

#### Acceptance Criteria

1. THE Arb_Dashboard SHALL отображать список открытых позиций с полями: символ, exchange_combo, направление, entry_basis_bps, current_basis_bps, basis_pnl, cumulative_funding, total_pnl, hold_duration, margin_ratio
2. THE Arb_Dashboard SHALL обновлять PNL открытых позиций каждые 3 секунды
3. THE Arb_Dashboard SHALL предоставлять кнопку ручного закрытия для каждой позиции
4. THE Arb_Dashboard SHALL отображать суммарную статистику: total_open_positions, total_exposure_usdt, total_unrealized_pnl, total_funding_earned

### Requirement 14: UI — График базиса

**User Story:** Как трейдер, я хочу видеть график базиса во времени с маркерами входа и выхода, чтобы анализировать поведение стратегии.

#### Acceptance Criteria

1. THE Arb_Dashboard SHALL отображать график базиса (bps) во времени для выбранной пары с использованием библиотеки lightweight-charts
2. THE Arb_Dashboard SHALL отображать на графике маркеры: зелёные стрелки для входа в позицию, красные стрелки для выхода
3. THE Arb_Dashboard SHALL отображать горизонтальные линии на уровнях entry_threshold_bps и exit_threshold_bps
4. THE Arb_Dashboard SHALL поддерживать выбор временного интервала: 1 час, 4 часа, 24 часа, 7 дней

### Requirement 15: UI — История сделок

**User Story:** Как трейдер, я хочу видеть историю закрытых арбитражных сделок с полной разбивкой PNL, чтобы оценивать эффективность стратегии.

#### Acceptance Criteria

1. THE Arb_Dashboard SHALL отображать таблицу закрытых сделок с колонками: символ, exchange_combo, направление, entry_basis_bps, exit_basis_bps, basis_pnl, funding_earned, total_fees, net_pnl, hold_duration, close_reason
2. THE Arb_Dashboard SHALL отображать суммарную статистику: total_trades, win_rate, total_net_pnl, avg_hold_duration, avg_net_pnl_bps, total_funding_earned
3. THE Arb_Dashboard SHALL поддерживать фильтрацию истории по символу, exchange_combo, направлению и периоду

### Requirement 16: Хранение истории базиса

**User Story:** Как трейдер, я хочу хранить историю базиса для анализа и бэктестов, чтобы оценивать устойчивость арбитражных возможностей.

#### Acceptance Criteria

1. THE Basis_Calculator SHALL записывать значения базиса в SQLite с настраиваемым интервалом (по умолчанию 60 секунд) для каждой мониторируемой пары
2. THE Basis_Calculator SHALL хранить в каждой записи: timestamp, symbol, exchange_combo, spot_mid, futures_mid, basis_abs, basis_bps, funding_rate, spot_spread_bps, futures_spread_bps
3. THE Basis_Calculator SHALL поддерживать retention policy: автоматическое удаление записей старше настроенного retention_days (по умолчанию 90 дней)

### Requirement 17: Поддержка нескольких Exchange Combo

**User Story:** Как трейдер, я хочу одновременно мониторить один символ на разных комбинациях бирж, чтобы выбирать оптимальную площадку для арбитража.

#### Acceptance Criteria

1. THE Strategy_Engine SHALL поддерживать одновременный мониторинг одного символа на нескольких exchange_combo (например, BTCUSDT на MEXC Spot + MEXC Futures и MEXC Spot + AsterDEX Perp)
2. THE Strategy_Engine SHALL открывать позицию на exchange_combo с наибольшим executable basis при прочих равных условиях
3. THE Strategy_Engine SHALL ограничивать суммарный notional по одному символу через параметр max_per_symbol_notional_usdt, независимо от exchange_combo

### Requirement 18: Сериализация и десериализация состояния позиций

**User Story:** Как трейдер, я хочу сохранять состояние открытых позиций при перезапуске приложения, чтобы не терять контроль над позициями.

#### Acceptance Criteria

1. THE Position_Manager SHALL сериализовать все открытые позиции в JSON-файл при остановке движка или по сигналу shutdown
2. WHEN Strategy_Engine запускается, THE Position_Manager SHALL десериализовать сохранённые позиции из JSON-файла и возобновить их мониторинг
3. FOR ALL валидных объектов позиции, сериализация с последующей десериализацией SHALL восстановить эквивалентный объект (round-trip свойство)
4. IF файл состояния повреждён или содержит невалидные данные, THEN THE Position_Manager SHALL записать ошибку в лог, начать с пустого состояния и отправить Telegram-алерт "state_recovery_failed"
