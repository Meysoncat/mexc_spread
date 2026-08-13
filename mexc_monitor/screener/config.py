"""Screener configuration — all tunable thresholds and scorer weights.

Loaded from ``config/screener.json`` with ``SCREENER_*`` env overrides, and
hot-reloadable at runtime via :func:`apply_config_patch` (PATCH endpoint).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_blacklist() -> tuple[str, ...]:
    # Stablecoins / FX-like pairs whose wide "spread" is noise, not opportunity.
    return (
        "USDCUSDT",
        "FDUSDUSDT",
        "TUSDUSDT",
        "BUSDUSDT",
        "USDPUSDT",
        "PAXGUSDT",
        "XAUTUSDT",
        "EURUSDT",
        "GBPUSDT",
        "WBTCUSDT",
    )


@dataclass
class ScreenerConfig:
    # ── Gate thresholds (hard cuts) ──────────────────────────────────────────
    min_net_spread_bps: float = 3.0
    max_spread_bps: float = 200.0  # sanity ceiling — wider = illiquid junk
    min_l1_notional_usdt: float = 100.0
    min_volume_24h_usdt: float = 100_000.0
    # volume gate mode: "hard" = kill on low 24h volume (legacy);
    # "soft" = 24h volume is NOT a hard cut — it only feeds the scorer, so a
    # low-24h coin that is active *right now* survives to the activity tier.
    volume_gate_mode: str = "hard"
    min_lifetime_sec: float = 8.0  # spread must persist above threshold
    max_tick_age_ms: float = 10_000.0
    symbol_blacklist: tuple[str, ...] = field(default_factory=_default_blacklist)

    # ── Economics ────────────────────────────────────────────────────────────
    # Round-trip maker fee in bps (MEXC spot = 0). net_spread = spread - this.
    maker_fee_round_trip_bps: float = 0.0

    # ── Engine cadence / output ──────────────────────────────────────────────
    scan_interval_sec: float = 2.0
    top_limit: int = 10  # genuine spread opportunities are rare (~a few at a time)
    rolling_window: int = 40  # recent spread samples kept per symbol

    # ── Scorer weights ───────────────────────────────────────────────────────
    w_ev: float = 1.0  # EV (realizable edge = net_spread × activity_factor) — dominant
    w_spread: float = 0.0  # raw spread reward (kept for opt-in; EV supersedes it)
    w_liq: float = 0.6
    w_life: float = 0.8
    w_stab: float = 0.5
    w_vol: float = 0.4
    w_stale: float = 0.05
    w_zscore: float = 0.3
    w_volume24h: float = 0.3  # 24h-volume reward weight (soft mode ranking signal)
    # Scorer normalizers
    spread_cap_bps: float = 50.0  # cap on spread reward
    liq_ref_usdt: float = 1000.0  # log1p(l1_notional / liq_ref)
    volume_ref_usdt: float = 50_000.0  # log1p(volume_24h / volume_ref)
    zscore_cap: float = 4.0  # cap on z-score reward

    # ── Adaptive thresholds ──────────────────────────────────────────────────
    # When on, the spread-size decision is made relative to the live universe
    # (percentile gate) and per-symbol (z-score gate) — so the screener
    # self-scales to market regime without manual threshold tuning.
    adaptive_mode: bool = True
    spread_percentile: float = 95.0  # gate: net_spread in top (100-pct)% of universe
    spread_percentile_min: float = 80.0  # calibration clamp
    spread_percentile_max: float = 99.0  # calibration clamp
    calibration_interval_sec: float = 120.0
    target_opportunity_min: int = 2
    target_opportunity_max: int = 5
    calibration_step: float = 1.0  # percentile nudge per calibration tick
    use_spread_zscore: bool = True
    min_spread_zscore: float = 1.0

    # ── Tier 1.5: real-time activity confirmation (bookTicker update rate) ────
    # After the snapshot gate (tier 1), candidates are confirmed "active now"
    # via the bookTicker stream update rate. Below this → flagged not-active.
    # Sourced from ws_spot_orderbook.get_book_update_rate() (pushes per minute).
    min_book_update_rate_per_min: float = 60.0
    # When a candidate's bookTicker rate is unknown (symbol not subscribed yet),
    # the EV scorer uses this neutral factor in [0, 1] (0.5 = half credit, so an
    # unconfirmed wide-spread coin can still surface and get promoted to the WS).
    activity_unknown_factor: float = 0.5
    # Real trades (REST trades poller) — the preferred activity signal when
    # available (honest fill density). trades_per_min ≥ this → factor 1.0.
    min_trades_per_min: float = 3.0


DEFAULT_CONFIG = ScreenerConfig()


def _default_config_path() -> Path:
    return _repo_root() / "config" / "screener.json"


def config_to_dict(cfg: ScreenerConfig) -> dict:
    d = asdict(cfg)
    d["symbol_blacklist"] = list(d["symbol_blacklist"])
    return d


def load_screener_config(path: str | Path | None = None) -> ScreenerConfig:
    """Load config from JSON (if present) with env overrides applied."""
    cfg = ScreenerConfig()
    p = Path(path) if path else _default_config_path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            cfg = _config_from_dict(raw, cfg)
        except Exception:
            logger.exception("Failed to load screener config %s; using defaults", p)
            cfg = ScreenerConfig()
    return _apply_env_overrides(cfg)


def _config_from_dict(raw: dict, base: ScreenerConfig) -> ScreenerConfig:
    """Build a ScreenerConfig from a JSON dict, ignoring unknown keys."""
    fields_k = {f for f in base.__dataclass_fields__}
    kw: dict = {}
    for k, v in raw.items():
        if k not in fields_k:
            continue
        if k == "symbol_blacklist":
            kw[k] = tuple(str(s).strip().upper() for s in v if str(s).strip())
        else:
            kw[k] = v
    return replace(base, **kw)


def _apply_env_overrides(cfg: ScreenerConfig) -> ScreenerConfig:
    """Apply SCREENER_* env vars on top of the file-loaded config."""
    overrides: dict = {}

    def _num(key: str, attr: str) -> None:
        val = os.environ.get(key)
        if val is None or val.strip() == "":
            return
        try:
            overrides[attr] = float(val)
        except ValueError:
            logger.warning("SCREENER env %s=%r is not a number", key, val)

    _num("SCREENER_MIN_NET_SPREAD_BPS", "min_net_spread_bps")
    _num("SCREENER_MAX_SPREAD_BPS", "max_spread_bps")
    _num("SCREENER_MIN_L1_NOTIONAL_USDT", "min_l1_notional_usdt")
    _num("SCREENER_MIN_VOLUME_24H_USDT", "min_volume_24h_usdt")
    _num("SCREENER_MIN_LIFETIME_SEC", "min_lifetime_sec")
    _num("SCREENER_MAKER_FEE_ROUND_TRIP_BPS", "maker_fee_round_trip_bps")
    _num("SCREENER_SCAN_INTERVAL_SEC", "scan_interval_sec")
    _num("SCREENER_TOP_LIMIT", "top_limit")  # type: ignore[arg-type]

    bl = os.environ.get("SCREENER_SYMBOL_BLACKLIST")
    if bl and bl.strip():
        overrides["symbol_blacklist"] = tuple(
            s.strip().upper() for s in bl.split(",") if s.strip()
        )

    if not overrides:
        return cfg
    return replace(cfg, **overrides)


def apply_config_patch(cfg: ScreenerConfig, patch: dict) -> ScreenerConfig:
    """Return a new config with a validated patch applied (used by PATCH endpoint)."""
    bool_fields = {"adaptive_mode", "use_spread_zscore"}
    str_fields = {"volume_gate_mode"}
    int_fields = {
        "top_limit",
        "rolling_window",
        "target_opportunity_min",
        "target_opportunity_max",
    }
    fields_k = set(cfg.__dataclass_fields__)
    kw: dict = {}
    for k, v in patch.items():
        if k not in fields_k:
            continue
        if k == "symbol_blacklist":
            if isinstance(v, str):
                items = [s.strip() for s in v.split(",") if s.strip()]
            else:
                items = list(v)
            kw[k] = tuple(str(s).upper() for s in items)
        elif k in bool_fields:
            kw[k] = (
                v if isinstance(v, bool) else str(v).strip().lower() in ("true", "1", "yes")
            )
        elif k in str_fields:
            val = str(v).strip().lower()
            if k == "volume_gate_mode" and val not in ("hard", "soft"):
                raise ValueError(f"invalid value for {k}: {v!r} (expected 'hard' or 'soft')")
            kw[k] = val
        elif k in int_fields:
            kw[k] = int(v)
        else:
            try:
                kw[k] = float(v)
            except (TypeError, ValueError) as e:
                raise ValueError(f"invalid value for {k}: {v!r}") from e
    return replace(cfg, **kw)
