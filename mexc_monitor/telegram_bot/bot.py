"""Telegram Bot — main entry point for AI Trading Assistant."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramBot:
    """Telegram bot for AI Trading Assistant."""

    def __init__(
        self,
        token: str,
        chat_id: str = "",
        ai_chat_url: str = "http://127.0.0.1:8006/api/ai/chat",
        api_base_url: str = "http://127.0.0.1:8006",
    ):
        self.token = token
        self.chat_id = chat_id
        self.ai_chat_url = ai_chat_url
        self.api_base_url = api_base_url.rstrip("/")
        self._api = _TELEGRAM_API.format(token=token)
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._offset = 0
        self._user_sessions: dict[int, list[dict]] = {}  # chat_id -> message history

    def start(self) -> None:
        """Start polling for messages in a background thread."""
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="TelegramBot",
            daemon=True,
        )
        self._thread.start()
        logger.info("TelegramBot started")

    def stop(self) -> None:
        """Stop the bot."""
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("TelegramBot stopped")

    def send_message(self, chat_id: str | int, text: str, reply_markup: dict | None = None) -> bool:
        """Send a message to a chat."""
        url = f"{self._api}/sendMessage"
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup:
            body["reply_markup"] = reply_markup
        try:
            r = httpx.post(url, json=body, timeout=10)
            return r.status_code == 200
        except Exception as e:
            logger.warning("Failed to send message: %s", e)
            return False

    def send_alert(self, text: str, reply_markup: dict | None = None) -> bool:
        """Send an alert to the configured chat."""
        if not self.chat_id:
            return False
        return self.send_message(self.chat_id, text, reply_markup)

    def _poll_loop(self) -> None:
        """Long-poll for updates."""
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as e:
                logger.warning("TelegramBot poll error: %s", e)
            if self._stop_event.wait(1):
                break

    def _poll_once(self) -> None:
        """Fetch and process one batch of updates."""
        url = f"{self._api}/getUpdates"
        params = {"offset": self._offset, "timeout": 30}
        try:
            r = httpx.get(url, params=params, timeout=35)
            if r.status_code != 200:
                return
            data = r.json()
            if not data.get("ok"):
                return
            for update in data.get("result", []):
                self._offset = update["update_id"] + 1
                self._process_update(update)
        except httpx.TimeoutException:
            pass
        except Exception as e:
            logger.warning("TelegramBot getUpdates error: %s", e)

    def _process_update(self, update: dict) -> None:
        """Process a single update."""
        # Handle callback queries (inline keyboard buttons)
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return

        # Handle text messages
        msg = update.get("message")
        if not msg or "text" not in msg:
            return

        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        user = msg.get("from", {}).get("first_name", "User")

        # Auto-register chat_id if not set
        if not self.chat_id:
            self.chat_id = str(chat_id)
            self.send_message(chat_id, f"✅ Chat ID сохранён: {chat_id}")

        # Route commands
        if text.startswith("/"):
            self._handle_command(chat_id, text, user)
        else:
            self._handle_chat(chat_id, text, user)

    def _handle_command(self, chat_id: int, text: str, user: str) -> None:
        """Handle bot commands."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "/start": self._cmd_start,
            "/help": self._cmd_help,
            "/analyze": self._cmd_analyze,
            "/signals": self._cmd_signals,
            "/risk": self._cmd_risk,
            "/status": self._cmd_status,
            "/portfolio": self._cmd_portfolio,
            "/funding": self._cmd_funding,
            "/density": self._cmd_density,
            "/trade": self._cmd_trade,
            "/mode": self._cmd_mode,
            "/ask": self._cmd_ask,
        }

        handler = handlers.get(cmd)
        if handler:
            handler(chat_id, args, user)
        else:
            # Unknown command — treat as chat
            self._handle_chat(chat_id, text, user)

    def _handle_chat(self, chat_id: int, text: str, user: str) -> None:
        """Handle natural language chat — forward to AI agent."""
        self.send_message(chat_id, "🤔 Думаю...")

        try:
            r = httpx.post(
                self.ai_chat_url,
                json={"message": text, "autonomy": "confirm"},
                timeout=60,
            )
            data = r.json()
            if data.get("ok"):
                response = data.get("response", "Нет ответа.")
                tool_calls = data.get("tool_calls", [])

                # Format response
                reply = response
                if tool_calls:
                    tools_text = "\n\n🔧 <i>Инструменты: " + ", ".join(
                        tc.get("name", "?") for tc in tool_calls
                    ) + "</i>"
                    reply += tools_text

                self.send_message(chat_id, reply)
            else:
                self.send_message(chat_id, f"❌ Ошибка: {data.get('error', 'Unknown')}")
        except Exception as e:
            self.send_message(chat_id, f"❌ Ошибка: {e}")

    # ─── Commands ────────────────────────────────────────────────────────────

    def _cmd_start(self, chat_id: int, args: str, user: str) -> None:
        """Welcome message."""
        self.send_message(chat_id, (
            f"👋 Привет, <b>{user}</b>!\n\n"
            "Я — <b>AI Trading Assistant</b> для MEXC Spread Monitor.\n\n"
            "📊 <b>Что я умею:</b>\n"
            "• Анализировать рынки и спреды\n"
            "• Находить сигналы для входа\n"
            "• Рассчитывать риск-менеджмент\n"
            "• Управлять торговлей\n"
            "• Отвечать на вопросы о трейдинге\n\n"
            "💬 <b>Просто напиши мне</b> на естественном языке!\n\n"
            "📋 <b>Команды:</b>\n"
            "/analyze BTCUSDT — анализ символа\n"
            "/signals — текущие сигналы\n"
            "/risk — отчёт по рискам\n"
            "/status — статус системы\n"
            "/portfolio — портфель\n"
            "/funding — funding rates\n"
            "/density — плотности стакана\n"
            "/trade — начать сделку\n"
            "/ask вопрос — вопрос AI\n"
            "/help — подробная справка"
        ))

    def _cmd_help(self, chat_id: int, args: str, user: str) -> None:
        """Detailed help."""
        self.send_message(chat_id, (
            "📖 <b>Справка по командам</b>\n\n"
            "<b>Анализ:</b>\n"
            "/analyze BTCUSDT — полный анализ символа (спред, funding, density)\n"
            "/signals — показать активные сигналы\n"
            "/funding — топ funding rates\n"
            "/density — крупные стены в стакане\n\n"
            "<b>Торговля:</b>\n"
            "/trade — начать сделку (с подтверждением)\n"
            "/status — статус торговых движков\n"
            "/portfolio — текущие позиции и P&L\n"
            "/risk — отчёт по рискам портфеля\n\n"
            "<b>Риск-менеджмент:</b>\n"
            "/ask Рассчитай размер позиции для $10000 с 2% риском\n"
            "/ask Рассчитай стоп-лосс для BTCUSDT long @ 64000\n\n"
            "<b>Обучение:</b>\n"
            "/ask Что такое funding rate?\n"
            "/ask Объясни density wall\n"
            "/ask Как работает position sizing?\n\n"
            "<b>Режимы:</b>\n"
            "/mode suggest — только советы\n"
            "/mode confirm — советы + подтверждение\n"
            "/mode auto — автоматическое исполнение\n\n"
            "💬 <b>Или просто напиши</b> любый вопрос на естественном языке!"
        ))

    def _cmd_analyze(self, chat_id: int, args: str, user: str) -> None:
        """Analyze a symbol."""
        symbol = args.upper() if args else "BTCUSDT"
        self._handle_chat(chat_id, f"Проанализируй {symbol} — покажи спред, funding, density walls, объём. Дай рекомендацию.", user)

    def _cmd_signals(self, chat_id: int, args: str, user: str) -> None:
        """Show active signals."""
        self._handle_chat(chat_id, "Покажи текущие активные сигналы — спреды выше порога, экстремальные funding rates, крупные стены в стакане.", user)

    def _cmd_risk(self, chat_id: int, args: str, user: str) -> None:
        """Portfolio risk report."""
        try:
            r = httpx.get(f"{self.api_base_url}/api/portfolio-risk/status", timeout=5)
            data = r.json()
            if data.get("ok"):
                exposure = data.get("total_exposure_usdt", 0)
                engines = data.get("engine_count", 0)
                positions = data.get("positions_by_symbol", {})
                dd = data.get("daily_drawdown_usdt", 0)

                text = (
                    "🛡️ <b>Portfolio Risk Report</b>\n\n"
                    f"💰 Экспозиция: <b>${exposure:,.2f}</b>\n"
                    f"📊 Движков: <b>{engines}</b>\n"
                    f"📉 Drawdown: <b>${dd:,.2f}</b>\n"
                )
                if positions:
                    text += "\n<b>Позиции:</b>\n"
                    for sym, pos in positions.items():
                        text += f"  • {sym}: ${pos:,.2f}\n"
                self.send_message(chat_id, text)
            else:
                self.send_message(chat_id, "❌ Не удалось получить данные о рисках")
        except Exception as e:
            self.send_message(chat_id, f"❌ Ошибка: {e}")

    def _cmd_status(self, chat_id: int, args: str, user: str) -> None:
        """System status."""
        try:
            r = httpx.get(f"{self.api_base_url}/api/health", timeout=5)
            data = r.json()
            feeds = data.get("ws_feeds", {})
            text = "⚡ <b>Статус системы</b>\n\n"
            for name, info in feeds.items():
                status = "🟢" if info.get("live") else "🔴"
                syms = info.get("symbols", 0)
                text += f"{status} {name}: {syms} символов\n"
            self.send_message(chat_id, text)
        except Exception as e:
            self.send_message(chat_id, f"❌ Ошибка: {e}")

    def _cmd_portfolio(self, chat_id: int, args: str, user: str) -> None:
        """Portfolio positions."""
        self._handle_chat(chat_id, "Покажи текущий портфель — открытые позиции, P&L, размеры.", user)

    def _cmd_funding(self, chat_id: int, args: str, user: str) -> None:
        """Top funding rates."""
        self._handle_chat(chat_id, "Покажи топ-10 самых высоких и низких funding rates. Укажи annualized yield.", user)

    def _cmd_density(self, chat_id: int, args: str, user: str) -> None:
        """Density walls."""
        symbol = args.upper() if args else "BTCUSDT"
        self._handle_chat(chat_id, f"Покажи крупные стены в стакане для {symbol}. Какие уровни поддерживают/сдерживают цену?", user)

    def _cmd_trade(self, chat_id: int, args: str, user: str) -> None:
        """Start a trade with confirmation."""
        if not args:
            self.send_message(chat_id, (
                "📊 <b>Начать сделку</b>\n\n"
                "Используй: /trade BTCUSDT long 64000\n"
                "Или просто напиши: «Купи BTCUSDT по 64000»"
            ))
            return

        parts = args.split()
        if len(parts) < 2:
            self.send_message(chat_id, "❌ Формат: /trade SYMBOL SIDE [PRICE]")
            return

        symbol = parts[0].upper()
        side = parts[1].lower()
        price = parts[2] if len(parts) > 2 else "market"

        markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Подтвердить", "callback_data": f"trade_confirm:{symbol}:{side}:{price}"},
                    {"text": "❌ Отменить", "callback_data": "trade_cancel"},
                ],
                [
                    {"text": "📊 Анализ", "callback_data": f"analyze:{symbol}"},
                    {"text": "⚙️ Риск", "callback_data": f"risk:{symbol}"},
                ],
            ]
        }
        self.send_message(
            chat_id,
            f"🔔 <b>Подтверждение сделки</b>\n\n"
            f"Символ: <b>{symbol}</b>\n"
            f"Сторона: <b>{side.upper()}</b>\n"
            f"Цена: <b>{price}</b>\n\n"
            f"Подтвердите исполнение:",
            reply_markup=markup,
        )

    def _cmd_mode(self, chat_id: int, args: str, user: str) -> None:
        """Change autonomy mode."""
        mode = args.lower()
        if mode not in ("suggest", "confirm", "auto"):
            self.send_message(chat_id, (
                "⚙️ <b>Режим автономности</b>\n\n"
                "/mode suggest — только советы\n"
                "/mode confirm — подтверждение сделок\n"
                "/mode auto — автоматическое исполнение"
            ))
            return

        # Update config
        config_path = Path(__file__).parent.parent.parent / "config" / "ai_config.json"
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                cfg["autonomy"] = mode
                config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
                self.send_message(chat_id, f"✅ Режим изменён: <b>{mode}</b>")
            except Exception as e:
                self.send_message(chat_id, f"❌ Ошибка: {e}")
        else:
            self.send_message(chat_id, "❌ Конфиг не найден")

    def _cmd_ask(self, chat_id: int, args: str, user: str) -> None:
        """Ask AI a question."""
        if not args:
            self.send_message(chat_id, "❓ Используй: /ask Как работает funding rate?")
            return
        self._handle_chat(chat_id, args, user)

    def _handle_callback(self, callback: dict) -> None:
        """Handle inline keyboard callbacks."""
        chat_id = callback["message"]["chat"]["id"]
        data = callback.get("data", "")

        if data.startswith("trade_confirm:"):
            parts = data.split(":")
            symbol = parts[1]
            side = parts[2]
            price = parts[3]
            self.send_message(chat_id, f"✅ Сделка подтверждена: {symbol} {side} @ {price}")
            # Here you would call the trading engine
            self._handle_chat(chat_id, f"Исполни сделку {symbol} {side} по цене {price}", "System")

        elif data == "trade_cancel":
            self.send_message(chat_id, "❌ Сделка отменена")

        elif data.startswith("analyze:"):
            symbol = data.split(":")[1]
            self._cmd_analyze(chat_id, symbol, "User")

        elif data.startswith("risk:"):
            symbol = data.split(":")[1]
            self._handle_chat(chat_id, f"Рассчитай риск для позиции {symbol}", "User")

        # Answer callback query to remove loading state
        try:
            httpx.post(
                f"{self._api}/answerCallbackQuery",
                json={"callback_query_id": callback["id"]},
                timeout=5,
            )
        except Exception:
            pass


def load_telegram_bot() -> TelegramBot | None:
    """Load Telegram bot from config."""
    config_path = Path(__file__).parent.parent.parent / "config" / "ai_config.json"
    if not config_path.exists():
        return None

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not cfg.get("telegram_enabled"):
        return None

    token_env = cfg.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN")
    chat_env = cfg.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID")

    token = os.environ.get(token_env, "").strip()
    chat_id = os.environ.get(chat_env, "").strip()

    if not token:
        logger.warning("Telegram bot token not set (%s)", token_env)
        return None

    return TelegramBot(token=token, chat_id=chat_id)
