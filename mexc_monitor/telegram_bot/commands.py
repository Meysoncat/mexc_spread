"""Telegram Bot — command handlers with rich formatting."""

from __future__ import annotations

from typing import Any

import httpx


class CommandHandler:
    """Handles Telegram bot commands with rich formatting."""

    def __init__(self, api_base_url: str = "http://127.0.0.1:8006"):
        self.api_base_url = api_base_url.rstrip("/")

    def get_market_data(self, symbol: str, exchange: str = "binance") -> dict[str, Any]:
        """Fetch market data for a symbol."""
        try:
            r = httpx.get(
                f"{self.api_base_url}/api/snapshot",
                params={"market": "futures", "exchange": exchange},
                timeout=10,
            )
            data = r.json()
            if not data.get("ok"):
                return {"error": data.get("error")}
            for row in data.get("rows", []):
                if row.get("symbol") == symbol:
                    return row
            return {"error": f"{symbol} not found"}
        except Exception as e:
            return {"error": str(e)}

    def format_analysis(self, symbol: str, data: dict) -> str:
        """Format market data as a rich analysis message."""
        if "error" in data:
            return f"❌ Ошибка: {data['error']}"

        bid = data.get("bid", 0)
        ask = data.get("ask", 0)
        mid = data.get("mid", 0)
        spread = data.get("spread_bps", 0)
        net_spread = data.get("net_spread_bps", 0)
        volume = data.get("volume_24h_quote", 0)
        funding = data.get("funding_rate")

        text = f"📊 <b>Анализ {symbol}</b>\n\n"
        text += f"💰 <b>Bid:</b> {bid:,.8f}\n"
        text += f"💰 <b>Ask:</b> {ask:,.8f}\n"
        text += f"📊 <b>Mid:</b> {mid:,.8f}\n"
        text += f"📈 <b>Спред:</b> {spread:.2f} bps\n"
        text += f"📈 <b>Чистый спред:</b> {net_spread:.2f} bps\n"
        text += f"📊 <b>Объём 24ч:</b> ${volume:,.0f}\n"

        if funding is not None:
            annualized = funding * 3 * 365 * 100
            text += f"💸 <b>Funding:</b> {funding:.4%} ({annualized:.1f}% APY)\n"

        # Recommendation
        text += "\n<b>📋 Рекомендация:</b>\n"
        if net_spread and net_spread > 10:
            text += "✅ Спред выше порога — возможен арбитраж\n"
        elif net_spread and net_spread > 0:
            text += "⚠️ Спред положительный, но ниже порога\n"
        else:
            text += "❌ Спред отрицательный или нулевой\n"

        if funding and abs(funding) > 0.001:
            if funding > 0:
                text += "💸 Высокий положительный funding — longs платят shorts\n"
            else:
                text += "💸 Высокий отрицательный funding — shorts платят longs\n"

        return text

    def format_density(self, symbol: str, data: dict) -> str:
        """Format density data as a rich message."""
        if "error" in data:
            return f"❌ Ошибка: {data['error']}"

        walls = data.get("walls", [])
        count = data.get("count", 0)

        text = f"🧱 <b>Стены в стакане {symbol}</b>\n\n"
        text += f"Найдено: <b>{count}</b> стен\n\n"

        if walls:
            for w in walls[:10]:
                side = "🟢 BID" if w.get("side") == "bid" else "🔴 ASK"
                notional = w.get("notional_usdt", 0)
                price = w.get("price", 0)
                ratio = w.get("ratio_to_median", 0)
                text += f"{side} ${notional:,.0f} @ {price:,.2f} ({ratio:.0f}×)\n"
        else:
            text += "Нет крупных стен\n"

        return text
