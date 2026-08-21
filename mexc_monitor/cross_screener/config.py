"""Configuration for the cross-exchange screener."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .models import PairSpec


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "cross_screener.json"


def _default_pairs() -> tuple[PairSpec, ...]:
    return (PairSpec("mexc", "aster", 4.0),)


@dataclass(frozen=True)
class CrossScreenerConfig:
    pairs: tuple[PairSpec, ...] = field(default_factory=_default_pairs)

    scan_interval_sec: float = 2.0
    top_limit: int = 10

    # Gates
    min_net_basis_bps: float = 5.0
    max_basis_bps: float = 300.0  # sanity cap: data glitch / symbol mismatch
    max_leg_age_ms: float = 2_000.0  # both legs must be fresh WS ticks
    min_lifetime_sec: float = 4.0
    min_l1_notional_usdt: float = 50.0  # per-leg executable L1
    use_zscore: bool = True
    zscore_min: float = 1.0
    rolling_window: int = 40
    # Incumbent margin (stops enter/exit flapping at the gate edge).
    exit_hysteresis_bps: float = 1.0

    # Scoring weights
    w_ev: float = 1.0  # net_bps × liquidity factor
    w_lifetime: float = 0.5
    w_stability: float = 0.3
    w_zscore: float = 0.3

    # Scorer normalizers
    basis_cap_bps: float = 60.0
    liq_ref_usdt: float = 500.0
    zscore_cap: float = 4.0


_NUM_FIELDS = {
    "scan_interval_sec",
    "min_net_basis_bps",
    "max_basis_bps",
    "max_leg_age_ms",
    "min_lifetime_sec",
    "min_l1_notional_usdt",
    "zscore_min",
    "exit_hysteresis_bps",
    "w_ev",
    "w_lifetime",
    "w_stability",
    "w_zscore",
    "basis_cap_bps",
    "liq_ref_usdt",
    "zscore_cap",
}
_INT_FIELDS = {"top_limit", "rolling_window"}
_BOOL_FIELDS = {"use_zscore"}
_ALLOWED = _NUM_FIELDS | _INT_FIELDS | _BOOL_FIELDS | {"pairs"}


def apply_config_patch(cfg: CrossScreenerConfig, patch: dict) -> CrossScreenerConfig:
    unknown = set(patch) - _ALLOWED
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    kw: dict = {}
    for k, v in patch.items():
        if k in _BOOL_FIELDS:
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be bool")
            kw[k] = v
        elif k in _INT_FIELDS:
            kw[k] = int(v)
        elif k == "pairs":
            if not isinstance(v, list) or not v:
                raise ValueError("pairs must be a non-empty list")
            kw[k] = tuple(
                PairSpec(
                    str(p["exchange_a"]).lower(),
                    str(p["exchange_b"]).lower(),
                    float(p.get("fee_bps_round_trip", 4.0)),
                )
                for p in v
            )
        else:
            kw[k] = float(v)
    if "top_limit" in kw and not 1 <= kw["top_limit"] <= 100:
        raise ValueError("top_limit must be 1..100")
    return replace(cfg, **kw)


def load_config(path: str | Path | None = None) -> CrossScreenerConfig:
    cfg = CrossScreenerConfig()
    p = Path(path) if path else _default_config_path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cfg = apply_config_patch(cfg, {k: v for k, v in raw.items() if k in _ALLOWED})
        except Exception:
            pass
    env_pairs = os.environ.get("CROSS_SCREENER_PAIRS")
    if env_pairs:
        # "mexc:aster:4.0,binance:bybit:6.0"
        parsed = []
        for chunk in env_pairs.split(","):
            parts = chunk.strip().split(":")
            if len(parts) >= 2:
                parsed.append(
                    PairSpec(parts[0].lower(), parts[1].lower(), float(parts[2]) if len(parts) > 2 else 4.0)
                )
        if parsed:
            cfg = replace(cfg, pairs=tuple(parsed))
    return cfg


def save_config(cfg: CrossScreenerConfig, path: str | Path | None = None) -> Path:
    p = Path(path) if path else _default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    d = asdict(cfg)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p
