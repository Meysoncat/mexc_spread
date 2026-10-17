"""Генератор торговых сетапов поверх MarketHub."""

from mexc_monitor.setups.config import SetupsConfig, load_setups_config
from mexc_monitor.setups.engine import SetupEngine
from mexc_monitor.setups.model import SCHEMA, SETUP_TYPES, Candidate, Leg, Setup
from mexc_monitor.setups.registry import SetupRegistry
from mexc_monitor.setups.store import SetupStore

__all__ = [
    "SCHEMA",
    "SETUP_TYPES",
    "Candidate",
    "Leg",
    "Setup",
    "SetupEngine",
    "SetupRegistry",
    "SetupStore",
    "SetupsConfig",
    "load_setups_config",
]
