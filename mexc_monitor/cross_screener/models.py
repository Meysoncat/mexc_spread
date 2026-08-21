"""Models for the cross-exchange screener."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class PairSpec:
    """One exchange pair to screen, e.g. mexc × aster."""

    exchange_a: str = "mexc"
    exchange_b: str = "aster"
    # Total round-trip maker/taker fees across both legs, in bps.
    fee_bps_round_trip: float = 4.0

    @property
    def label(self) -> str:
        return f"{self.exchange_a.upper()}×{self.exchange_b.upper()}"


@dataclass(frozen=True)
class CrossOpportunity:
    """A symbol whose cross-venue basis exceeds the net threshold."""

    symbol: str
    exchange_a: str
    exchange_b: str
    # Direction: buy on `buy_on`, sell on `sell_on`.
    buy_on: str
    sell_on: str
    basis_bps: float  # realizable basis (bid_sell − ask_buy), positive
    net_bps: float  # basis − round-trip fees
    mid_a: float
    mid_b: float
    l1_notional_a: float  # ask-side notional of the buy leg, USDT
    l1_notional_b: float  # bid-side notional of the sell leg, USDT
    tick_age_a_ms: float
    tick_age_b_ms: float
    lifetime_sec: float
    zscore: float | None
    spread_std: float | None = None
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pair"] = f"{self.exchange_a.upper()}×{self.exchange_b.upper()}"
        return d
