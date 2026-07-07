"""
Конфигурация Telegram-алертов: загрузка/сохранение из JSON, маскирование токена.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AlertConfig:
    """Настройки Telegram-алертов."""
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    # Типы алертов
    spread_threshold_enabled: bool = True
    spread_threshold_bps: float = 50.0
    arbitrage_enabled: bool = True
    arbitrage_threshold_bps: float = 10.0
    trade_events_enabled: bool = True
    # Rate limiting
    rate_limit_sec: int = 60


def _config_path() -> Path:
    """Путь к файлу конфигурации алертов."""
    custom = os.environ.get("MEXC_ALERTS_CONFIG_PATH")
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent.parent.parent / "config" / "alerts.json"


def load_alert_config() -> AlertConfig:
    """Загрузить конфигурацию из JSON + env overrides."""
    cfg = AlertConfig()
    path = _config_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg = AlertConfig(
                    enabled=bool(raw.get("enabled", cfg.enabled)),
                    bot_token=str(raw.get("bot_token", cfg.bot_token)).strip(),
                    chat_id=str(raw.get("chat_id", cfg.chat_id)).strip(),
                    spread_threshold_enabled=bool(raw.get("spread_threshold_enabled", cfg.spread_threshold_enabled)),
                    spread_threshold_bps=float(raw.get("spread_threshold_bps", cfg.spread_threshold_bps)),
                    arbitrage_enabled=bool(raw.get("arbitrage_enabled", cfg.arbitrage_enabled)),
                    arbitrage_threshold_bps=float(raw.get("arbitrage_threshold_bps", cfg.arbitrage_threshold_bps)),
                    trade_events_enabled=bool(raw.get("trade_events_enabled", cfg.trade_events_enabled)),
                    rate_limit_sec=max(1, int(raw.get("rate_limit_sec", cfg.rate_limit_sec))),
                )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    # Env overrides (приоритет над JSON)
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if env_token:
        cfg.bot_token = env_token
    env_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if env_chat:
        cfg.chat_id = env_chat

    return cfg


def save_alert_config(cfg: AlertConfig) -> None:
    """Сохранить конфигурацию в JSON."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def mask_bot_token(token: str) -> str:
    """Маскировать bot_token: показать только последние 4 символа."""
    if not token or len(token) <= 4:
        return "****"
    return f"{'*' * (len(token) - 4)}{token[-4:]}"


def config_to_safe_dict(cfg: AlertConfig) -> dict[str, Any]:
    """Конфигурация для API-ответа (с маскированным токеном)."""
    d = asdict(cfg)
    d["bot_token"] = mask_bot_token(cfg.bot_token)
    return d
