"""
AlertService — основной сервис алертов с rate limiting.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from mexc_monitor.alerts.config import (
    AlertConfig,
    config_to_safe_dict,
    load_alert_config,
    save_alert_config,
)
from mexc_monitor.alerts.telegram import TelegramClient, TelegramError

logger = logging.getLogger(__name__)


class AlertService:
    """Сервис отправки Telegram-алертов с rate limiting."""

    def __init__(self, config: AlertConfig | None = None):
        self._config = config or load_alert_config()
        self._client = TelegramClient(self._config.bot_token)
        self._lock = threading.Lock()
        # Rate limiting: key = f"{alert_type}:{symbol}" → last_sent_timestamp
        self._last_sent: dict[str, float] = {}

    def _is_rate_limited(self, key: str) -> bool:
        """Проверить rate limit для ключа."""
        now = time.time()
        with self._lock:
            last = self._last_sent.get(key, 0.0)
            if now - last < self._config.rate_limit_sec:
                return True
            self._last_sent[key] = now
            return False

    def _send(self, text: str) -> bool:
        """Отправить сообщение (внутренний метод)."""
        if not self._config.enabled:
            return False
        if not self._config.bot_token or not self._config.chat_id:
            return False
        try:
            self._client.send_message(self._config.chat_id, text)
            return True
        except TelegramError as e:
            logger.warning("Alert send failed: %s", e)
            return False

    # ─── Public methods ─────────────────────────────────────────────────────

    def send_spread_alert(self, symbol: str, spread_bps: float, threshold_bps: float) -> bool:
        """Алерт: спред превысил порог."""
        if not self._config.spread_threshold_enabled:
            return False
        key = f"spread:{symbol}"
        if self._is_rate_limited(key):
            return False
        text = (
            f"📊 <b>Spread Alert</b>\n"
            f"Символ: <code>{symbol}</code>\n"
            f"Спред: <b>{spread_bps:.2f} bps</b>\n"
            f"Порог: {threshold_bps:.2f} bps"
        )
        return self._send(text)

    def send_arbitrage_alert(
        self,
        symbol: str,
        mexc_mid: float,
        aster_mid: float,
        basis_bps: float,
    ) -> bool:
        """Алерт: межбиржевая арбитражная возможность."""
        if not self._config.arbitrage_enabled:
            return False
        key = f"arbitrage:{symbol}"
        if self._is_rate_limited(key):
            return False
        direction = "Aster > MEXC" if basis_bps > 0 else "MEXC > Aster"
        text = (
            f"⚡ <b>Arbitrage Alert</b>\n"
            f"Символ: <code>{symbol}</code>\n"
            f"Базис: <b>{basis_bps:.2f} bps</b> ({direction})\n"
            f"MEXC mid: {mexc_mid:.6f}\n"
            f"Aster mid: {aster_mid:.6f}"
        )
        return self._send(text)

    def send_trade_alert(self, event: dict[str, Any]) -> bool:
        """Алерт: позиция открыта/закрыта."""
        if not self._config.trade_events_enabled:
            return False
        event_type = event.get("type", "unknown")
        symbol = event.get("symbol", "?")
        key = f"trade:{symbol}:{event_type}"
        if self._is_rate_limited(key):
            return False

        if event_type == "position_opened":
            text = (
                f"🟢 <b>Position Opened</b>\n"
                f"Символ: <code>{symbol}</code>\n"
                f"Цена входа: {event.get('entry_price', 0):.6f}\n"
                f"Размер: {event.get('notional_usdt', 0):.2f} USDT\n"
                f"Спред: {event.get('spread_bps', 0):.2f} bps"
            )
        elif event_type == "position_closed":
            net_pnl = event.get("net_pnl_usdt", 0)
            emoji = "🟢" if net_pnl >= 0 else "🔴"
            text = (
                f"{emoji} <b>Position Closed</b>\n"
                f"Символ: <code>{symbol}</code>\n"
                f"Net PNL: <b>{net_pnl:+.4f} USDT</b>\n"
                f"Удержание: {event.get('hold_sec', 0):.1f}с\n"
                f"Причина: {event.get('reason', '?')}"
            )
        else:
            text = f"📋 <b>Trade Event</b>: {event_type}\nСимвол: <code>{symbol}</code>"

        return self._send(text)

    def test_connection(self) -> bool:
        """Тестовое сообщение."""
        if not self._config.bot_token or not self._config.chat_id:
            return False
        client = TelegramClient(self._config.bot_token)
        return client.test_connection(self._config.chat_id)

    def get_config(self) -> dict[str, Any]:
        """Получить конфигурацию (с маскированным токеном)."""
        return config_to_safe_dict(self._config)

    def update_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Обновить конфигурацию и сохранить."""
        with self._lock:
            cfg = self._config
            if "enabled" in patch:
                cfg.enabled = bool(patch["enabled"])
            if "bot_token" in patch:
                token = str(patch["bot_token"]).strip()
                # Не перезаписывать маскированным значением
                if token and not token.startswith("*"):
                    cfg.bot_token = token
                    self._client = TelegramClient(cfg.bot_token)
            if "chat_id" in patch:
                cfg.chat_id = str(patch["chat_id"]).strip()
            if "spread_threshold_enabled" in patch:
                cfg.spread_threshold_enabled = bool(patch["spread_threshold_enabled"])
            if "spread_threshold_bps" in patch:
                cfg.spread_threshold_bps = max(0.0, float(patch["spread_threshold_bps"]))
            if "arbitrage_enabled" in patch:
                cfg.arbitrage_enabled = bool(patch["arbitrage_enabled"])
            if "arbitrage_threshold_bps" in patch:
                cfg.arbitrage_threshold_bps = max(0.0, float(patch["arbitrage_threshold_bps"]))
            if "trade_events_enabled" in patch:
                cfg.trade_events_enabled = bool(patch["trade_events_enabled"])
            if "rate_limit_sec" in patch:
                cfg.rate_limit_sec = max(1, int(patch["rate_limit_sec"]))
            save_alert_config(cfg)
        return config_to_safe_dict(self._config)

    # ─── Futures/Spot Arbitrage alerts ──────────────────────────────────────

    def send_futures_arb_position_opened(
        self,
        symbol: str,
        exchange_combo: str,
        strategy: str,
        entry_basis_bps: float,
        notional_usdt: float,
        estimated_apy: float = 0.0,
    ) -> bool:
        """Алерт: арбитражная позиция открыта."""
        key = f"futures_arb_open:{symbol}"
        if self._is_rate_limited(key):
            return False

        direction_map = {
            "cash_and_carry": "Cash-and-Carry",
            "reverse_cash_and_carry": "Reverse C&C",
            "funding_arb": "Funding Arb",
        }
        direction = direction_map.get(strategy, strategy)

        text = (
            f"🟢 <b>Futures Arb Opened</b>\n"
            f"Символ: <code>{symbol}</code>\n"
            f"Combo: {exchange_combo}\n"
            f"Стратегия: <b>{direction}</b>\n"
            f"Entry basis: {entry_basis_bps:.2f} bps\n"
            f"Notional: {notional_usdt:.2f} USDT\n"
            f"Est. APY: {estimated_apy:.1f}%"
        )
        return self._send(text)

    def send_futures_arb_position_closed(
        self,
        symbol: str,
        exchange_combo: str,
        reason: str,
        net_pnl: float,
        net_pnl_bps: float = 0.0,
        hold_duration_sec: float = 0.0,
        funding_earned: float = 0.0,
    ) -> bool:
        """Алерт: арбитражная позиция закрыта."""
        key = f"futures_arb_close:{symbol}"
        if self._is_rate_limited(key):
            return False

        emoji = "🟢" if net_pnl >= 0 else "🔴"
        hold_hours = hold_duration_sec / 3600.0

        text = (
            f"{emoji} <b>Futures Arb Closed</b>\n"
            f"Символ: <code>{symbol}</code>\n"
            f"Combo: {exchange_combo}\n"
            f"Причина: {reason}\n"
            f"Net PNL: <b>{net_pnl:+.4f} USDT</b> ({net_pnl_bps:+.1f} bps)\n"
            f"Удержание: {hold_hours:.1f}ч\n"
            f"Funding earned: {funding_earned:.4f} USDT"
        )
        return self._send(text)

    def send_futures_arb_risk_alert(
        self,
        symbol: str,
        alert_type: str,
        message: str,
    ) -> bool:
        """Алерт: критический риск арбитражной позиции."""
        key = f"futures_arb_risk:{symbol}:{alert_type}"
        if self._is_rate_limited(key):
            return False

        text = (
            f"⚠️ <b>RISK — Futures Arb</b>\n"
            f"Символ: <code>{symbol}</code>\n"
            f"Тип: {alert_type}\n"
            f"{message}"
        )
        return self._send(text)

    def send_futures_arb_funding_alert(
        self,
        symbol: str,
        exchange: str,
        funding_rate: float,
        annualized_yield: float,
    ) -> bool:
        """Алерт: высокий funding rate."""
        key = f"futures_arb_funding:{symbol}:{exchange}"
        if self._is_rate_limited(key):
            return False

        text = (
            f"💰 <b>High Funding Rate</b>\n"
            f"Символ: <code>{symbol}</code>\n"
            f"Биржа: {exchange}\n"
            f"Funding rate: <b>{funding_rate:.4%}</b>\n"
            f"Annual yield: {annualized_yield:.1f}%"
        )
        return self._send(text)
