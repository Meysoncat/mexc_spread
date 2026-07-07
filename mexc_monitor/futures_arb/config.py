"""
Configuration loader for Futures/Spot Arbitrage module.

Reads settings from config/futures_arb.json with environment variable overrides.
Provides validation to enforce business constraints.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

from mexc_monitor.futures_arb.models import FuturesArbSettings

logger = logging.getLogger(__name__)

# Environment variable prefix for all futures-arb settings
_ENV_PREFIX = "FUTURES_ARB_"


def default_config_path() -> Path:
    """Return the default path to futures_arb.json config file."""
    custom = os.environ.get("FUTURES_ARB_CONFIG_PATH")
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent.parent.parent / "config" / "futures_arb.json"


def _parse_list(val: Any) -> list[str]:
    """Parse a JSON list or comma-separated env string into a list of strings."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip() for x in val.split(",") if x.strip()]
    return []


def _coerce_value(field_name: str, raw_value: Any, target_type: type) -> Any:
    """Coerce a raw value to the target type expected by FuturesArbSettings."""
    if target_type == bool:
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw_value)
    elif target_type == int:
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None
    elif target_type == float:
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return None
    elif target_type == str:
        return str(raw_value)
    elif target_type == list:
        return _parse_list(raw_value)
    return raw_value


def _get_field_type(field_name: str) -> type:
    """Get the expected type for a FuturesArbSettings field."""
    type_map = {
        "enabled": bool,
        "mode": str,
        "symbols": list,
        "exchange_combos": list,
        "entry_threshold_bps": float,
        "exit_threshold_bps": float,
        "max_basis_divergence_bps": float,
        "target_profit_bps": float,
        "funding_entry_threshold": float,
        "funding_consecutive_periods_exit": int,
        "position_notional_usdt": float,
        "max_concurrent_positions": int,
        "max_per_symbol_notional_usdt": float,
        "max_total_exposure_usdt": float,
        "futures_leverage": int,
        "max_hold_duration_hours": float,
        "max_leg_pending_sec": float,
        "loop_interval_sec": float,
        "expected_hold_hours": float,
        "margin_warning_threshold": float,
        "margin_critical_threshold": float,
        "max_delta_imbalance_percent": float,
        "critical_delta_imbalance_percent": float,
        "kill_switch": bool,
        "spot_taker_fee_bps": float,
        "futures_taker_fee_bps": float,
        "basis_history_interval_sec": float,
        "basis_history_retention_days": int,
        "funding_alert_threshold": float,
    }
    return type_map.get(field_name, str)


def _load_from_json(path: Path) -> dict[str, Any]:
    """Load raw settings dict from JSON file. Returns empty dict on failure."""
    if not path.is_file():
        logger.debug("Config file not found: %s, using defaults", path)
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("Config file %s does not contain a JSON object", path)
            return {}
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read config file %s: %s", path, e)
        return {}


def _apply_json_to_settings(raw: dict[str, Any], settings: FuturesArbSettings) -> FuturesArbSettings:
    """Apply JSON dict values to settings, coercing types as needed."""
    known_fields = {f.name for f in fields(settings)}
    updates: dict[str, Any] = {}

    for key, value in raw.items():
        if key.startswith("_"):
            continue  # Skip comments
        if key not in known_fields:
            continue
        target_type = _get_field_type(key)
        coerced = _coerce_value(key, value, target_type)
        if coerced is not None:
            updates[key] = coerced

    if updates:
        return replace(settings, **updates)
    return settings


def _apply_env_overrides(settings: FuturesArbSettings) -> FuturesArbSettings:
    """Apply environment variable overrides. Env vars use FUTURES_ARB_ prefix + uppercase field name."""
    known_fields = {f.name for f in fields(settings)}
    updates: dict[str, Any] = {}

    for field_name in known_fields:
        env_key = f"{_ENV_PREFIX}{field_name.upper()}"
        env_value = os.environ.get(env_key)
        if env_value is None:
            continue

        target_type = _get_field_type(field_name)
        coerced = _coerce_value(field_name, env_value, target_type)
        if coerced is not None:
            updates[field_name] = coerced

    if updates:
        return replace(settings, **updates)
    return settings


class ConfigValidationError(ValueError):
    """Raised when settings fail validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Configuration validation failed: {'; '.join(errors)}")


def validate_settings(settings: FuturesArbSettings) -> list[str]:
    """
    Validate FuturesArbSettings and return a list of error messages.

    Returns an empty list if all constraints are satisfied.

    Constraints:
    - entry_threshold_bps > exit_threshold_bps
    - position_notional_usdt > 0
    - 1 <= max_concurrent_positions <= 20
    - 1 <= futures_leverage <= 20
    """
    errors: list[str] = []

    if settings.entry_threshold_bps <= settings.exit_threshold_bps:
        errors.append(
            f"entry_threshold_bps ({settings.entry_threshold_bps}) must be greater than "
            f"exit_threshold_bps ({settings.exit_threshold_bps})"
        )

    if settings.position_notional_usdt <= 0:
        errors.append(
            f"position_notional_usdt ({settings.position_notional_usdt}) must be greater than 0"
        )

    if not (1 <= settings.max_concurrent_positions <= 20):
        errors.append(
            f"max_concurrent_positions ({settings.max_concurrent_positions}) must be between 1 and 20"
        )

    if not (1 <= settings.futures_leverage <= 20):
        errors.append(
            f"futures_leverage ({settings.futures_leverage}) must be between 1 and 20"
        )

    return errors


def load_futures_arb_settings(
    config_path: Path | None = None,
    *,
    validate: bool = True,
) -> FuturesArbSettings:
    """
    Load FuturesArbSettings from JSON file with environment variable overrides.

    Loading order:
    1. Start with dataclass defaults
    2. Override with values from config/futures_arb.json (or custom path)
    3. Override with FUTURES_ARB_* environment variables

    Args:
        config_path: Optional custom path to JSON config file.
        validate: If True, raise ConfigValidationError on invalid settings.

    Returns:
        Loaded and optionally validated FuturesArbSettings.

    Raises:
        ConfigValidationError: If validate=True and settings fail validation.
    """
    path = config_path or default_config_path()

    # Start with defaults
    settings = FuturesArbSettings()

    # Apply JSON config
    raw = _load_from_json(path)
    if raw:
        settings = _apply_json_to_settings(raw, settings)

    # Apply env overrides
    settings = _apply_env_overrides(settings)

    # Validate
    if validate:
        errors = validate_settings(settings)
        if errors:
            raise ConfigValidationError(errors)

    return settings
