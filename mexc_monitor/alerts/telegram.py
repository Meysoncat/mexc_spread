"""
HTTP-клиент для Telegram Bot API.
Отправка сообщений с retry и exponential back-off.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_RETRIES = 3
INITIAL_BACKOFF_SEC = 1.0


class TelegramError(RuntimeError):
    """Ошибка отправки в Telegram."""
    pass


class TelegramClient:
    """Клиент для отправки сообщений через Telegram Bot API."""

    def __init__(self, bot_token: str, timeout_sec: float = 10.0):
        self._bot_token = bot_token.strip()
        self._timeout = timeout_sec

    @property
    def configured(self) -> bool:
        return bool(self._bot_token)

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: str = "HTML",
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        """
        Отправить сообщение в Telegram с retry.
        Raises TelegramError при неудаче после всех попыток.
        """
        if not self._bot_token:
            raise TelegramError("bot_token is not configured")
        if not chat_id:
            raise TelegramError("chat_id is not configured")

        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text[:4096],  # Telegram limit
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
        }

        last_error: Exception | None = None
        backoff = INITIAL_BACKOFF_SEC

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = httpx.post(url, json=payload, timeout=self._timeout)
                data = r.json()
                if r.status_code == 200 and data.get("ok"):
                    return data
                # Rate limit
                if r.status_code == 429:
                    retry_after = data.get("parameters", {}).get("retry_after", backoff)
                    logger.warning(
                        "Telegram rate limit, retry_after=%s (attempt %s/%s)",
                        retry_after, attempt, MAX_RETRIES,
                    )
                    time.sleep(float(retry_after))
                    backoff *= 2
                    continue
                # Other errors
                error_desc = data.get("description", f"HTTP {r.status_code}")
                last_error = TelegramError(f"Telegram API: {error_desc}")
                if r.status_code >= 400 and r.status_code < 500 and r.status_code != 429:
                    # Client error (not retryable except 429)
                    raise last_error
            except httpx.HTTPError as e:
                last_error = TelegramError(f"HTTP error: {type(e).__name__}: {e}")
                logger.warning(
                    "Telegram send failed (attempt %s/%s): %s",
                    attempt, MAX_RETRIES, last_error,
                )
            except TelegramError:
                raise

            if attempt < MAX_RETRIES:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

        raise last_error or TelegramError("Failed after all retries")

    def test_connection(self, chat_id: str) -> bool:
        """Отправить тестовое сообщение. Возвращает True при успехе."""
        try:
            self.send_message(
                chat_id,
                "✅ <b>MEXC Spread Monitor</b>\nТестовое подключение успешно!",
            )
            return True
        except TelegramError:
            return False
