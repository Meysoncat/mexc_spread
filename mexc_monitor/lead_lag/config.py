"""Configuration loader and validator for the Lead-Lag Arbitrage engine.

Reads configuration from config/external_apis.json section "lead_lag".
Falls back to defaults if the file is missing, invalid JSON, or the section is absent.
Validates all constraints and returns detailed error messages on failure.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LeadLagConfig:
    """Configuration for the lead-lag engine."""

    enabled: bool = False
    leader_exchange: str = "binance"
    lagger_exchanges: list[str] = field(default_factory=lambda: ["mexc"])
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    market: str = "futures"
    z_score_entry_threshold: float = 2.0
    z_score_exit_threshold: float = 0.5
    signal_timeout_sec: float = 10.0
    rolling_window_sec: float = 300.0
    min_spread_bps: float = 3.0
    lag_estimation_interval_sec: float = 30.0
    price_buffer_history_sec: float = 60.0
    db_path: str = "data/lead_lag_signals.sqlite"
    assumed_taker_fee_bps: float = 2.0
    ws_urls: dict[str, str] = field(default_factory=dict)


def _default_config() -> LeadLagConfig:
    """Return the default configuration as specified in Requirement 9.2."""
    return LeadLagConfig()


def _config_file_path() -> Path:
    """Resolve the path to config/external_apis.json relative to project root."""
    return Path(__file__).resolve().parent.parent.parent / "config" / "external_apis.json"


def load_lead_lag_config(config_path: Path | None = None) -> LeadLagConfig:
    """Load lead-lag configuration from config/external_apis.json section 'lead_lag'.

    If the file doesn't exist, is not valid JSON, or lacks the 'lead_lag' section,
    returns default configuration values as per Requirement 9.2.

    Args:
        config_path: Optional override for the config file path (useful for testing).

    Returns:
        LeadLagConfig populated from the JSON or defaults.
    """
    path = config_path or _config_file_path()

    if not path.is_file():
        logger.warning("Config file not found at %s, using defaults", path)
        return _default_config()

    try:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read/parse config file %s: %s. Using defaults.", path, exc)
        return _default_config()

    if not isinstance(raw, dict):
        logger.warning("Config file root is not a JSON object, using defaults")
        return _default_config()

    lead_lag_section = raw.get("lead_lag")
    if not isinstance(lead_lag_section, dict):
        logger.warning("Section 'lead_lag' not found or not a dict in config, using defaults")
        return _default_config()

    return _parse_config_section(lead_lag_section)


def _parse_config_section(section: dict[str, Any]) -> LeadLagConfig:
    """Parse the lead_lag section into a LeadLagConfig dataclass."""
    defaults = _default_config()

    def _get_bool(key: str, default: bool) -> bool:
        val = section.get(key)
        if val is None:
            return default
        return bool(val)

    def _get_str(key: str, default: str) -> str:
        val = section.get(key)
        if val is None:
            return default
        return str(val).strip() or default

    def _get_float(key: str, default: float) -> float:
        val = section.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _get_str_list(key: str, default: list[str]) -> list[str]:
        val = section.get(key)
        if not isinstance(val, list):
            return default
        result = [str(x).strip() for x in val if str(x).strip()]
        return result if result else default

    def _get_str_dict(key: str, default: dict[str, str]) -> dict[str, str]:
        val = section.get(key)
        if not isinstance(val, dict):
            return default
        return {str(k).strip(): str(v).strip() for k, v in val.items() if str(k).strip()}

    return LeadLagConfig(
        enabled=_get_bool("enabled", defaults.enabled),
        leader_exchange=_get_str("leader_exchange", defaults.leader_exchange),
        lagger_exchanges=_get_str_list("lagger_exchanges", defaults.lagger_exchanges),
        symbols=_get_str_list("symbols", defaults.symbols),
        market=_get_str("market", defaults.market),
        z_score_entry_threshold=_get_float("z_score_entry_threshold", defaults.z_score_entry_threshold),
        z_score_exit_threshold=_get_float("z_score_exit_threshold", defaults.z_score_exit_threshold),
        signal_timeout_sec=_get_float("signal_timeout_sec", defaults.signal_timeout_sec),
        rolling_window_sec=_get_float("rolling_window_sec", defaults.rolling_window_sec),
        min_spread_bps=_get_float("min_spread_bps", defaults.min_spread_bps),
        lag_estimation_interval_sec=_get_float("lag_estimation_interval_sec", defaults.lag_estimation_interval_sec),
        price_buffer_history_sec=_get_float("price_buffer_history_sec", defaults.price_buffer_history_sec),
        db_path=_get_str("db_path", defaults.db_path),
        assumed_taker_fee_bps=_get_float("assumed_taker_fee_bps", defaults.assumed_taker_fee_bps),
        ws_urls=_get_str_dict("ws_urls", defaults.ws_urls),
    )


def validate_config(config: LeadLagConfig) -> list[str]:
    """Validate a LeadLagConfig against all constraints from Requirement 9.3.

    Returns a list of validation error messages. An empty list means the config is valid.
    Each error message includes the violated rule and the actual value.

    Args:
        config: The configuration to validate.

    Returns:
        List of error strings. Empty if valid.
    """
    errors: list[str] = []

    # z_score_entry_threshold > 0
    if not (config.z_score_entry_threshold > 0):
        errors.append(
            f"z_score_entry_threshold must be > 0, got {config.z_score_entry_threshold}"
        )

    # z_score_exit_threshold >= 0
    if not (config.z_score_exit_threshold >= 0):
        errors.append(
            f"z_score_exit_threshold must be >= 0, got {config.z_score_exit_threshold}"
        )

    # z_score_entry_threshold > z_score_exit_threshold
    if not (config.z_score_entry_threshold > config.z_score_exit_threshold):
        errors.append(
            f"z_score_entry_threshold ({config.z_score_entry_threshold}) "
            f"must be > z_score_exit_threshold ({config.z_score_exit_threshold})"
        )

    # signal_timeout_sec > 0
    if not (config.signal_timeout_sec > 0):
        errors.append(
            f"signal_timeout_sec must be > 0, got {config.signal_timeout_sec}"
        )

    # rolling_window_sec > 0
    if not (config.rolling_window_sec > 0):
        errors.append(
            f"rolling_window_sec must be > 0, got {config.rolling_window_sec}"
        )

    # min_spread_bps >= 0
    if not (config.min_spread_bps >= 0):
        errors.append(
            f"min_spread_bps must be >= 0, got {config.min_spread_bps}"
        )

    # lag_estimation_interval_sec > 0
    if not (config.lag_estimation_interval_sec > 0):
        errors.append(
            f"lag_estimation_interval_sec must be > 0, got {config.lag_estimation_interval_sec}"
        )

    # price_buffer_history_sec > 0
    if not (config.price_buffer_history_sec > 0):
        errors.append(
            f"price_buffer_history_sec must be > 0, got {config.price_buffer_history_sec}"
        )

    # symbols непустой
    if not config.symbols:
        errors.append("symbols must be non-empty, got empty list")

    # lagger_exchanges непустой
    if not config.lagger_exchanges:
        errors.append("lagger_exchanges must be non-empty, got empty list")

    # leader_exchange не в lagger_exchanges
    if config.leader_exchange in config.lagger_exchanges:
        errors.append(
            f"leader_exchange '{config.leader_exchange}' must not be in "
            f"lagger_exchanges {config.lagger_exchanges}"
        )

    # assumed_taker_fee_bps >= 0
    if not (config.assumed_taker_fee_bps >= 0):
        errors.append(
            f"assumed_taker_fee_bps must be >= 0, got {config.assumed_taker_fee_bps}"
        )

    # market равен "spot" или "futures"
    if config.market not in ("spot", "futures"):
        errors.append(
            f"market must be 'spot' or 'futures', got '{config.market}'"
        )

    # ws_urls содержит URL для leader и каждого lagger
    _validate_ws_urls(config, errors)

    return errors


def _validate_ws_urls(config: LeadLagConfig, errors: list[str]) -> None:
    """Validate that ws_urls contains entries for leader and all laggers."""
    if not config.ws_urls:
        errors.append(
            f"ws_urls must contain URL for leader_exchange '{config.leader_exchange}' "
            f"and each lagger_exchange {config.lagger_exchanges}, got empty ws_urls"
        )
        return

    # Check leader exchange has a URL
    # The ws_urls keys may use format like "binance_futures" or "binance_spot" or just "binance"
    # We check if any key starts with the exchange name
    leader = config.leader_exchange
    if not _has_ws_url_for_exchange(config.ws_urls, leader):
        errors.append(
            f"ws_urls must contain URL for leader_exchange '{leader}', "
            f"available keys: {list(config.ws_urls.keys())}"
        )

    # Check each lagger exchange has a URL
    for lagger in config.lagger_exchanges:
        if not _has_ws_url_for_exchange(config.ws_urls, lagger):
            errors.append(
                f"ws_urls must contain URL for lagger_exchange '{lagger}', "
                f"available keys: {list(config.ws_urls.keys())}"
            )


def _has_ws_url_for_exchange(ws_urls: dict[str, str], exchange: str) -> bool:
    """Check if ws_urls contains at least one URL for the given exchange.

    Keys can be exact match (e.g. "binance") or prefixed (e.g. "binance_futures").
    """
    exchange_lower = exchange.lower()
    for key in ws_urls:
        if key.lower() == exchange_lower or key.lower().startswith(exchange_lower + "_"):
            return True
    return False
