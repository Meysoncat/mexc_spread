"""Настройки генератора сетапов: комиссии и пороги детекторов.

Значения по умолчанию — разумная отправная точка, не истина. Комиссии —
базовый (VIP0) taker-тариф; задайте свои в ``config/setups.json`` или через
``SETUPS_CONFIG=/path/to/file.json``. Файл мёржится поверх дефолтов, так что
указывать нужно только то, что меняете.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# taker, bps. Источник — публичные тарифы бирж для базового уровня; проверьте свой.
DEFAULT_PERP_TAKER_BPS: dict[str, float] = {
    "binance": 5.0,
    "bybit": 5.5,
    "okx": 5.0,
    "gateio": 5.0,
    "bitget": 6.0,
    "htx": 5.0,
    "mexc": 2.0,
    "asterdex": 3.5,
    "dydx": 5.0,
    "hyperliquid": 4.5,
    "lighter": 0.0,
}
DEFAULT_SPOT_TAKER_BPS: dict[str, float] = {
    "binance": 10.0,
    "bybit": 10.0,
    "okx": 10.0,
    "gateio": 10.0,
    "bitget": 10.0,
    "htx": 20.0,
    "mexc": 5.0,
}


@dataclass
class CrossPerpConfig:
    enabled: bool = True
    open_edge_bps: float = 10.0  # ожидаемая прибыль после комиссий входа/выхода
    keep_edge_bps: float = 3.0
    max_gross_bps: float = 300.0  # больше — почти всегда сломанные данные/закрытый вывод
    max_age_sec: float = 5.0
    min_volume_24h_usd: float = 500_000.0
    min_l1_usd: float = 200.0
    baseline_halflife_sec: float = 1800.0
    baseline_warmup_sec: float = 600.0
    baseline_cap_bps: float = 50.0
    max_price_deviation: float = 0.2  # отбрасывать котировки дальше 20% от медианы (коллизии тикеров)
    horizon_sec: int = 3600


@dataclass
class FundingArbConfig:
    enabled: bool = True
    open_apr_pct: float = 30.0  # годовая разница funding
    keep_apr_pct: float = 15.0
    max_breakeven_h: float = 24.0  # за сколько часов funding окупит вход
    min_volume_24h_usd: float = 1_000_000.0
    max_age_sec: float = 10.0
    horizon_sec: int = 48 * 3600  # edge — доход funding за этот срок минус издержки


@dataclass
class BasisConfig:
    enabled: bool = True
    every_sec: float = 2.0  # расхождения базиса живут секундами — чаще считать незачем
    open_edge_bps: float = 15.0
    keep_edge_bps: float = 5.0
    include_funding_hours: float = 8.0  # учитывать funding за столько часов удержания
    allow_reverse: bool = False  # шорт спота требует займа — по умолчанию выключено
    same_venue_only: bool = False
    max_gross_bps: float = 500.0
    max_age_sec: float = 10.0
    min_volume_24h_usd: float = 500_000.0
    min_l1_usd: float = 200.0
    baseline_halflife_sec: float = 3600.0
    baseline_warmup_sec: float = 900.0
    baseline_cap_bps: float = 100.0
    horizon_sec: int = 8 * 3600


@dataclass
class LeadLagConfig:
    enabled: bool = True
    leaders: list[str] = field(default_factory=lambda: ["binance"])
    window_sec: float = 2.0
    move_bps: float = 20.0  # движение лидера за окно
    follow_ratio: float = 0.3  # отстающий прошёл не больше этой доли движения
    open_edge_bps: float = 6.0  # разрыв минус спред и комиссии отстающего
    max_lagger_age_sec: float = 1.0
    universe: int = 60  # топ баз по объёму у лидера
    ttl_sec: int = 15


@dataclass
class BreakoutConfig:
    enabled: bool = True
    lookback: int = 60  # закрытых 1m свечей для диапазона
    buffer_pct: float = 0.05  # пробой выше/ниже уровня минимум на столько %
    volume_ratio: float = 3.0
    max_range_pct: float = 8.0
    min_range_pct: float = 0.4
    stop_atr: float = 1.0
    min_risk_pct: float = 0.2
    max_risk_pct: float = 3.0
    targets_r: list[float] = field(default_factory=lambda: [1.0, 2.0, 3.0])
    ttl_sec: int = 1800


@dataclass
class MomentumConfig:
    enabled: bool = True
    window: int = 5  # минут
    move_pct: float = 3.0
    extreme_pct: float = 6.0  # от этого движения ищем откат (reversion)
    volume_ratio: float = 3.0
    lookback: int = 60
    max_pullback_continuation: float = 0.25
    min_pullback_reversion: float = 0.3
    stop_atr: float = 1.5
    targets_r: list[float] = field(default_factory=lambda: [1.0, 2.0])
    ttl_sec: int = 1800


@dataclass
class FundingSqueezeConfig:
    enabled: bool = True
    funding_8h_pct: float = 0.1  # |funding| за 8ч, %
    oi_change_pct: float = 5.0  # рост OI за окно
    oi_window_sec: float = 3600.0
    max_price_follow_pct: float = 1.0  # цена почти не идёт за толпой
    min_volume_24h_usd: float = 2_000_000.0
    stop_pct_min: float = 1.5
    targets_r: list[float] = field(default_factory=lambda: [1.0, 2.0])
    ttl_sec: int = 4 * 3600


@dataclass
class BtcLagConfig:
    enabled: bool = True
    venue: str = "binance"
    corr_window: int = 120  # минут для корреляции/беты
    move_window: int = 15  # минут движения BTC
    btc_move_pct: float = 0.8
    min_corr: float = 0.6
    min_shortfall_ratio: float = 0.5  # отставание как доля ожидаемого движения
    min_shortfall_pct: float = 0.5
    target_capture: float = 0.7  # цель — такая доля недостающего движения
    stop_atr: float = 1.2
    min_rr: float = 1.2
    ttl_sec: int = 1800


@dataclass
class SetupsConfig:
    perp_taker_bps: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PERP_TAKER_BPS))
    spot_taker_bps: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SPOT_TAKER_BPS))
    venues: list[str] | None = None  # None — все поддерживаемые
    kline_venues: list[str] = field(default_factory=lambda: ["binance", "bybit", "mexc"])
    kline_universe: int = 150
    tick_sec: float = 0.5
    directional_every_sec: float = 5.0
    funding_every_sec: float = 10.0
    min_score: float = 0.0  # ниже — не публиковать
    history_db: str = "data/setups.sqlite"
    history_retention_days: int = 30
    cross_perp: CrossPerpConfig = field(default_factory=CrossPerpConfig)
    funding_arb: FundingArbConfig = field(default_factory=FundingArbConfig)
    basis: BasisConfig = field(default_factory=BasisConfig)
    lead_lag: LeadLagConfig = field(default_factory=LeadLagConfig)
    breakout: BreakoutConfig = field(default_factory=BreakoutConfig)
    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    funding_squeeze: FundingSqueezeConfig = field(default_factory=FundingSqueezeConfig)
    btc_lag: BtcLagConfig = field(default_factory=BtcLagConfig)

    def perp_fee(self, venue: str) -> float:
        return float(self.perp_taker_bps.get(venue, 5.0))

    def spot_fee(self, venue: str) -> float:
        return float(self.spot_taker_bps.get(venue, 10.0))


def _merge(obj: Any, data: dict[str, Any], path: str = "") -> None:
    known = {f.name: f for f in fields(obj)}
    for k, v in data.items():
        if k.startswith("_"):
            continue  # комментарии в JSON
        if k not in known:
            logger.warning("setups config: unknown key %s%s", path, k)
            continue
        cur = getattr(obj, k)
        if is_dataclass(cur) and isinstance(v, dict):
            _merge(cur, v, f"{path}{k}.")
        elif isinstance(cur, dict) and isinstance(v, dict):
            cur.update(v)
        else:
            setattr(obj, k, v)


def default_config_path() -> Path:
    custom = os.environ.get("SETUPS_CONFIG")
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parents[2] / "config" / "setups.json"


def load_setups_config(path: Path | None = None) -> SetupsConfig:
    cfg = SetupsConfig()
    path = path or default_config_path()
    if path.is_file():
        try:
            _merge(cfg, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            logger.error("setups config %s unreadable: %s — using defaults", path, e)
    return cfg
