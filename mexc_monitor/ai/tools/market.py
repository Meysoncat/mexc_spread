"""AI Agent Tools — market data, trading, risk, history, education."""

from __future__ import annotations

from typing import Any



def get_market_data(symbol: str, exchange: str = "mexc", market: str = "futures") -> dict[str, Any]:
    """Get current market data for a symbol."""
    import httpx
    try:
        r = httpx.get(
            "http://127.0.0.1:8006/api/snapshot",
            params={"market": market, "exchange": exchange},
            timeout=10,
        )
        data = r.json()
        if not data.get("ok"):
            return {"error": data.get("error", "Unknown error")}
        rows = data.get("rows", [])
        for row in rows:
            if row.get("symbol") == symbol:
                return {
                    "symbol": symbol,
                    "bid": row.get("bid"),
                    "ask": row.get("ask"),
                    "mid": row.get("mid"),
                    "spread_bps": row.get("spread_bps"),
                    "net_spread_bps": row.get("net_spread_bps"),
                    "volume_24h_quote": row.get("volume_24h_quote"),
                    "funding_rate": row.get("funding_rate"),
                }
        return {"error": f"Symbol {symbol} not found on {exchange}/{market}"}
    except Exception as e:
        return {"error": str(e)}


def get_funding_rates(exchange: str = "binance") -> dict[str, Any]:
    """Get funding rates for all symbols on an exchange."""
    import httpx
    try:
        r = httpx.get(
            "http://127.0.0.1:8006/api/snapshot",
            params={"market": "futures", "exchange": exchange},
            timeout=10,
        )
        data = r.json()
        if not data.get("ok"):
            return {"error": data.get("error")}
        rows = data.get("rows", [])
        rates = []
        for row in rows:
            fr = row.get("funding_rate")
            if fr is not None and abs(fr) > 0.0001:
                rates.append({
                    "symbol": row.get("symbol"),
                    "funding_rate": fr,
                    "annualized": fr * 3 * 365 * 100,
                })
        rates.sort(key=lambda x: abs(x["funding_rate"]), reverse=True)
        return {"exchange": exchange, "count": len(rates), "top_rates": rates[:20]}
    except Exception as e:
        return {"error": str(e)}


def get_density_walls(symbol: str, exchange: str = "binance") -> dict[str, Any]:
    """Get density walls for a symbol."""
    import httpx
    try:
        r = httpx.get(
            "http://127.0.0.1:8006/api/density/walls",
            params={"symbol": symbol, "market": "spot", "multiplier": 5, "min_notional": 50000},
            timeout=15,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def get_portfolio_status() -> dict[str, Any]:
    """Get current portfolio risk status."""
    import httpx
    try:
        r = httpx.get(
            "http://127.0.0.1:8006/api/portfolio-risk/status",
            timeout=5,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def get_trade_history(limit: int = 20) -> dict[str, Any]:
    """Get recent trade events."""
    import httpx
    try:
        r = httpx.get(
            "http://127.0.0.1:8006/api/trading/events",
            params={"limit": limit},
            timeout=5,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def calculate_position_size(
    capital: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
) -> dict[str, Any]:
    """Calculate position size based on risk management."""
    if entry_price <= 0 or stop_price <= 0:
        return {"error": "Prices must be positive"}
    if risk_pct <= 0 or risk_pct > 100:
        return {"error": "Risk percentage must be between 0 and 100"}

    risk_amount = capital * (risk_pct / 100)
    stop_distance = abs(entry_price - stop_price)
    stop_distance_pct = (stop_distance / entry_price) * 100
    position_size = risk_amount / stop_distance if stop_distance > 0 else 0
    position_notional = position_size * entry_price

    return {
        "capital": capital,
        "risk_pct": risk_pct,
        "risk_amount": round(risk_amount, 2),
        "entry_price": entry_price,
        "stop_price": stop_price,
        "stop_distance_pct": round(stop_distance_pct, 2),
        "position_size": round(position_size, 6),
        "position_notional": round(position_notional, 2),
    }


def calculate_stop_loss(
    entry_price: float,
    side: str,
    atr: float | None = None,
    stop_pct: float | None = None,
) -> dict[str, Any]:
    """Calculate stop-loss and take-profit levels."""
    if entry_price <= 0:
        return {"error": "Entry price must be positive"}

    if atr and atr > 0:
        multiplier = 2.0
        if side == "long":
            stop = entry_price - atr * multiplier
            tp1 = entry_price + atr * 2
            tp2 = entry_price + atr * 3
        else:
            stop = entry_price + atr * multiplier
            tp1 = entry_price - atr * 2
            tp2 = entry_price - atr * 3
        return {
            "method": "ATR-based",
            "entry": entry_price,
            "stop_loss": round(stop, 8),
            "take_profit_1": round(tp1, 8),
            "take_profit_2": round(tp2, 8),
            "risk_reward_1": 2.0,
            "risk_reward_2": 3.0,
        }

    if stop_pct and stop_pct > 0:
        if side == "long":
            stop = entry_price * (1 - stop_pct / 100)
            tp1 = entry_price * (1 + stop_pct * 2 / 100)
        else:
            stop = entry_price * (1 + stop_pct / 100)
            tp1 = entry_price * (1 - stop_pct * 2 / 100)
        return {
            "method": "Percentage-based",
            "entry": entry_price,
            "stop_loss": round(stop, 8),
            "take_profit_1": round(tp1, 8),
            "risk_reward_1": 2.0,
        }

    return {"error": "Provide either atr or stop_pct"}


def explain_concept(concept: str) -> dict[str, Any]:
    """Explain a trading concept."""
    concepts = {
        "funding_rate": {
            "title": "Funding Rate",
            "explanation": "Funding rate — это платежи между длинными и короткими позициями на perpetual futures. "
                          "Положительный funding означает, что longs платят shorts (рынок бычий). "
                          "Отрицательный — shorts платят longs (рынок медвежий). "
                          "Обычно выплачивается каждые 8 часов.",
            "usage": "Используйте funding rate для funding arbitrage: купите спот + шортите фьючерс "
                    "при положительном funding. Получаете funding каждые 8 часов.",
        },
        "spread_bps": {
            "title": "Spread (bps)",
            "explanation": "Spread — разница между лучшей ценой покупки (bid) и продажи (ask). "
                          "В bps (basis points): spread_bps = (ask - bid) / mid * 10000. "
                          "1 bps = 0.01% от цены.",
            "usage": "Чем меньше spread, тем ликвиднее инструмент. "
                    "Для арбитража ищите спреды > порога (обычно 10-50 bps).",
        },
        "position_sizing": {
            "title": "Position Sizing",
            "explanation": "Определение размера позиции на основе риска. "
                          "Формула: position = (capital * risk_pct) / stop_distance. "
                          "Например: $10000, 2% риск, стоп 5% от входа = $4000 позиция.",
            "usage": "Никогда не рискуйте более 1-2% капитала на сделку. "
                    "Это позволяет пережить серию убытков.",
        },
        "density_wall": {
            "title": "Density Wall",
            "explanation": "Крупный ордер в стакане (order book). Стена показывает уровень "
                          "с аномально большой нотацией (обычно > 5x от медианы). "
                          "Стена может быть поддержкой (bid) или сопротивлением (ask).",
            "usage": "Если крупная bid-стена стоит ниже цены — это поддержка. "
                    "Если пробивается — сигнал на продажу.",
        },
    }

    concept_lower = concept.lower().replace(" ", "_")
    if concept_lower in concepts:
        return concepts[concept_lower]
    return {
        "title": concept,
        "explanation": f"Концепция '{concept}' не найдена в базе знаний. "
                      "Попробуйте: funding_rate, spread_bps, position_sizing, density_wall",
    }


# Tool definitions for LLM function calling
MARKET_TOOLS = [
    {
        "name": "get_market_data",
        "description": "Получить текущие рыночные данные для символа (цены, спред, объём, funding rate)",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Символ, например BTCUSDT"},
                "exchange": {"type": "string", "description": "Биржа: mexc, binance, bybit, okx", "default": "mexc"},
                "market": {"type": "string", "description": "Рынок: spot или futures", "default": "futures"},
            },
            "required": ["symbol"],
        },
        "handler": get_market_data,
    },
    {
        "name": "get_funding_rates",
        "description": "Получить топ funding rates по всем символам на бирже",
        "parameters": {
            "type": "object",
            "properties": {
                "exchange": {"type": "string", "description": "Биржа", "default": "binance"},
            },
        },
        "handler": get_funding_rates,
    },
    {
        "name": "get_density_walls",
        "description": "Получить крупные ордера (стены) в стакане для символа",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Символ"},
                "exchange": {"type": "string", "description": "Биржа", "default": "binance"},
            },
            "required": ["symbol"],
        },
        "handler": get_density_walls,
    },
    {
        "name": "get_portfolio_status",
        "description": "Получить статус портфеля: открытые позиции, P&L, риск-метрики",
        "parameters": {"type": "object", "properties": {}},
        "handler": get_portfolio_status,
    },
    {
        "name": "calculate_position_size",
        "description": "Рассчитать размер позиции на основе риск-менеджмента",
        "parameters": {
            "type": "object",
            "properties": {
                "capital": {"type": "number", "description": "Общий капитал в USDT"},
                "risk_pct": {"type": "number", "description": "Риск на сделку в % (обычно 1-2%)"},
                "entry_price": {"type": "number", "description": "Цена входа"},
                "stop_price": {"type": "number", "description": "Цена стоп-лосса"},
            },
            "required": ["capital", "risk_pct", "entry_price", "stop_price"],
        },
        "handler": calculate_position_size,
    },
    {
        "name": "calculate_stop_loss",
        "description": "Рассчитать уровни стоп-лосса и тейк-профита",
        "parameters": {
            "type": "object",
            "properties": {
                "entry_price": {"type": "number", "description": "Цена входа"},
                "side": {"type": "string", "description": "Сторона: long или short"},
                "atr": {"type": "number", "description": "ATR (Average True Range) — опционально"},
                "stop_pct": {"type": "number", "description": "Стоп в % от входа — опционально"},
            },
            "required": ["entry_price", "side"],
        },
        "handler": calculate_stop_loss,
    },
    {
        "name": "explain_concept",
        "description": "Объяснить трейдерскую концепцию (funding rate, spread, position sizing, density wall)",
        "parameters": {
            "type": "object",
            "properties": {
                "concept": {"type": "string", "description": "Концепция для объяснения"},
            },
            "required": ["concept"],
        },
        "handler": explain_concept,
    },
]
