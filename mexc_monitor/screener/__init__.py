"""Spread Screener — auto-discovery of tradeable bid/ask-spread opportunities.

Continuously evaluates the MEXC spot universe and surfaces coins where capturing
the bid/ask spread (maker market-making: buy@bid / sell@ask) is profitable
*right now*, ranked by a tunable filter pipeline.

Public API:
    ScreenerConfig, load_screener_config, apply_config_patch
    ScreenerEngine
    Candidate, ScreenerOpportunity
"""

from mexc_monitor.screener.config import (
    DEFAULT_CONFIG,
    ScreenerConfig,
    apply_config_patch,
    load_screener_config,
)
from mexc_monitor.screener.engine import ScreenerEngine
from mexc_monitor.screener.models import Candidate, ScreenerOpportunity
from mexc_monitor.screener.state import ScreenerState

__all__ = [
    "DEFAULT_CONFIG",
    "ScreenerConfig",
    "apply_config_patch",
    "load_screener_config",
    "ScreenerEngine",
    "ScreenerState",
    "Candidate",
    "ScreenerOpportunity",
]
