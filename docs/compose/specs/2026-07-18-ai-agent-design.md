# AI Trading Agent — Design Specification

## [S1] Problem

Трейдеру нужен AI-помощник, который:
- Автоматически мониторит все активные сделки и позиции
- Находит сигналы для входа на основе данных приложения
- Рассчитывает риск-менеджмент (position sizing, stop-loss, exposure)
- Помогает с обучением и объяснением рыночных ситуаций
- Работает через Telegram и Web UI
- Поддерживает разные уровни автономности

## [S2] Solution Overview

AI-агент с архитектурой "мозг + инструменты":
- **Мозг**: LLM (OpenAI/Anthropic/Ollama) с контекстом рынка
- **Инструменты**: функции для анализа, торговли, риск-менеджмента
- **Интерфейсы**: Telegram бот + Web UI чат
- **Автономность**: советы → подтверждение → авто-исполнение

## [S3] LLM Provider Layer

Абстракция над несколькими LLM API:

```python
class LLMProvider(ABC):
    async def chat(self, messages, tools=None) -> Response
    async def stream(self, messages, tools=None) -> AsyncIterator[str]

class OpenAIProvider(LLMProvider): ...      # GPT-4o, GPT-4o-mini
class AnthropicProvider(LLMProvider): ...   # Claude 3.5 Sonnet
class OllamaProvider(LLMProvider): ...      # Локальные модели
```

Конфигурация в `config/ai_config.json`:
```json
{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "api_key_env": "OPENAI_API_KEY",
  "ollama_url": "http://localhost:11434",
  "max_tokens": 4096,
  "temperature": 0.3
}
```

## [S4] Agent Core

### Контекст агента
При каждом вызове агент получает:
- Текущие спреды и funding rates
- Открытые позиции и их P&L
- Portfolio risk metrics
- Последние сигналы (density walls, lead-lag, arbitrage)
- Историю разговора (последние N сообщений)

### Инструменты (function calling)
LLM вызывает инструменты через function calling:

| Инструмент | Описание |
|-----------|----------|
| `get_market_data` | Получить спреды, funding, density для символа |
| `analyze_signal` | Проанализировать сигнал (spread, funding, density) |
| `calculate_risk` | Рассчитать position sizing, stop-loss, take-profit |
| `execute_trade` | Исполнить сделку (с подтверждением) |
| `get_portfolio` | Текущие позиции и P&L |
| `get_history` | История сделок |
| `set_alert` | Создать алерт |
| `explain_concept` | Объяснить концепцию |
| `backtest` | Запустить бэктест |

### Уровни автономности

| Уровень | Описание | Поведение |
|---------|----------|-----------|
| `suggest` | Только советы | AI анализирует и предлагает, но не исполняет |
| `confirm` | Советы + подтверждение | AI предлагает сделку, ждёт подтверждения пользователя |
| `auto` | Полная автоматизация | AI сам принимает решения и исполняет |

## [S5] Signal Engine

Анализирует рыночные данные и генерирует сигналы:

| Сигнал | Источник | Условие |
|--------|----------|---------|
| Spread anomaly | Spread Monitor | spread_bps > threshold |
| Funding extreme | Funding Heatmap | funding_rate > 0.1% или < -0.1% |
| Density wall | Density Screener | wall > $100K появляется/исчезает |
| Lead-lag | Lead-Lag Page | lag > threshold, signal confidence > 0.7 |
| Cross-exchange arb | Multi-Exchange | cross_spread_bps > threshold |

Каждый сигнал включает:
- Символ, биржа, тип сигнала
- Уверенность (0-1)
- Рекомендуемое действие
- Risk/reward оценку

## [S6] Risk Manager

### Position Sizing
- **Kelly Criterion**: `f = (bp - q) / b` где b=odds, p=win probability, q=1-p
- **Fixed Fractional**: `position = capital * risk_per_trade / stop_loss_distance`
- **Volatility-based**: `position = risk_budget / (ATR * multiplier)`

### Stop-Loss / Take-Profit
- ATR-based: `stop = entry - ATR * multiplier`
- Percentage-based: `stop = entry * (1 - stop_pct)`
- Spread-based: `exit when spread_bps < threshold`

### Portfolio Limits
- Max exposure per symbol
- Max total exposure
- Max drawdown limit
- Max concurrent positions

## [S7] Telegram Bot

### Команды
| Команда | Описание |
|---------|----------|
| `/start` | Приветствие, настройка |
| `/analyze BTCUSDT` | Анализ символа |
| `/signals` | Текущие сигналы |
| `/risk` | Portfolio risk report |
| `/trade` | Начать сделку (с подтверждением) |
| `/status` | Статус позиций |
| `/ask <question>` | Вопрос AI |
| `/mode suggest\|confirm\|auto` | Сменить режим |

### Inline Keyboards
Для подтверждения сделок:
```
[✅ Подтвердить] [❌ Отменить]
[📊 Анализ] [⚙️ Настройки]
```

### Proactive Alerts
AI сам отправляет уведомления при:
- Обнаружении сигнала
- Изменении позиции
- Превышении риск-лимита
- Аномалии на рынке

## [S8] Web UI Chat

### Расположение
Панель чата в правой части экрана (collapsible sidebar) или отдельная страница `/ai`.

### Компоненты
- Chat messages (user + AI)
- Rich cards: trade proposals, risk reports, signal alerts
- Confirmation dialogs for trades
- Streaming responses
- Markdown rendering

### Интеграция
- Кнопка "Ask AI" на каждой странице (передаёт контекст)
- Автоматические предложения при обнаружении сигналов
- Inline risk calculator

## [S9] Data Flow

```
User (Telegram/Web) → Agent Core → LLM Provider
                          ↓
                    Tool Execution
                    ├── Market Data API
                    ├── Trade Engine
                    ├── Risk Calculator
                    └── Alert System
                          ↓
                    Response → User (Telegram/Web)
```

## [S10] File Structure

```
mexc_monitor/
├── ai/
│   ├── __init__.py
│   ├── agent.py          # Agent Core — orchestrator
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py       # LLMProvider ABC
│   │   ├── openai.py     # OpenAI provider
│   │   ├── anthropic.py  # Anthropic provider
│   │   └── ollama.py     # Ollama provider
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── market.py     # get_market_data, analyze_signal
│   │   ├── trading.py    # execute_trade, get_portfolio
│   │   ├── risk.py       # calculate_risk
│   │   ├── history.py    # get_history, backtest
│   │   └── education.py  # explain_concept
│   ├── signals/
│   │   ├── __init__.py
│   │   └── engine.py     # Signal detection engine
│   └── risk/
│       ├── __init__.py
│       └── manager.py    # Position sizing, stop-loss
├── telegram_bot/
│   ├── __init__.py
│   ├── bot.py            # Telegram bot main
│   ├── commands.py       # Command handlers
│   └── alerts.py         # Proactive alerts
config/
└── ai_config.json        # LLM provider config
```

## [S11] Implementation Order

1. **LLM Provider Layer** — абстракция + OpenAI provider
2. **Agent Core** — оркестратор с function calling
3. **Tools** — market, trading, risk, history
4. **Signal Engine** — детектор сигналов
5. **Risk Manager** — position sizing, stop-loss
6. **Web UI Chat** — чат-интерфейс в приложении
7. **Telegram Bot** — бот с командами
8. **Education Module** — объяснение концепций
