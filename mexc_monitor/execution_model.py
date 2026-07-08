"""Execution model for paper trading simulation.

Replaces the unrealistic "instant fill at quoted price" assumption with:

1. **Fill probability** — limit orders fill according to a Poisson process
   with configurable rate :math:`\\lambda`. The probability of at least one
   matching trade in interval :math:`\\Delta t` is::

        P(fill) = 1 - e^{-\\lambda \\Delta t}

2. **Adverse selection** — when a limit order fills, the counterparty often
   has directional information. The *captured* spread is reduced by a
   fraction :math:`\\alpha` (empirically 0.3–0.7)::

        captured = quoted \\times (1 - \\alpha)

3. **Market-order slippage** — when a position is force-closed via market
   order (timeout), VWAP from L2 depth (if available) or a simple
   half-spread penalty is applied.

These models make paper-mode PNL pessimistic relative to the old instant-fill
assumption, bringing it closer to live trading reality.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

Side = Literal["buy", "sell"]


@dataclass
class ExecutionSettings:
    """Parameters for the paper-trading execution model."""

    # Poisson fill rate for limit orders (fills per second).
    # 0.1 ≈ 10% chance per second, ~50% within 7 sec, ~95% within 30 sec.
    fill_rate_per_sec: float = 0.1

    # Adverse selection ratio: fraction of half-spread lost on each fill.
    # 0.0 = no adverse selection (optimistic); 0.5 = lose half the edge.
    adverse_selection_ratio: float = 0.3

    # Market-order slippage penalty (bps) when no L2 depth is available.
    # Applied on top of the half-spread when force-closing via market.
    market_slippage_bps: float = 2.0

    # RNG seed for reproducible paper-mode results (None = nondeterministic).
    seed: int | None = None

    # Master switch: when False, reverts to legacy instant-fill behaviour.
    realistic_fills: bool = True


@dataclass(frozen=True)
class FillOutcome:
    """Result of a single fill-probability check.

    Attributes
    ----------
    filled
        Whether the limit order was filled in this interval.
    fill_price
        Effective fill price (entry bid/ask, unadjusted — adverse selection
        is tracked separately in :attr:`adverse_cost`).
    adverse_cost
        Adverse selection cost in **quote currency per unit**, to be
        subtracted from the captured spread when computing PNL.
    """

    filled: bool
    fill_price: float
    adverse_cost: float


class ExecutionSimulator:
    """Thread-safe simulator for limit-order fills in paper mode.

    Instantiate once per engine; call :meth:`check_limit_fill` on every tick
    while a limit order is pending.
    """

    def __init__(self, settings: ExecutionSettings | None = None) -> None:
        self._settings = settings or ExecutionSettings()
        self._rng = random.Random(self._settings.seed)
        self._lock = threading.Lock()

    @property
    def settings(self) -> ExecutionSettings:
        return self._settings

    def serialize_state(self) -> dict[str, Any]:
        """Serialize simulator state including RNG seed and state.

        Returns
        -------
        dict[str, Any]
            State dictionary with seed and RNG state.
        """
        return {
            "seed": self._settings.seed,
            "rng_state": self._rng.getstate(),
            "fill_rate_per_sec": self._settings.fill_rate_per_sec,
            "adverse_selection_ratio": self._settings.adverse_selection_ratio,
            "market_slippage_bps": self._settings.market_slippage_bps,
            "realistic_fills": self._settings.realistic_fills,
        }

    def deserialize_state(self, state: dict[str, Any]) -> None:
        """Deserialize simulator state including RNG state.

        Parameters
        ----------
        state
            State dictionary from serialize_state().
        """
        with self._lock:
            if "seed" in state:
                self._settings.seed = state["seed"]
                self._rng = random.Random(state["seed"])

            if "rng_state" in state:
                rng_state = state["rng_state"]
                # JSON round-trip turns tuples into lists; setstate needs tuples
                if isinstance(rng_state, list):
                    rng_state = tuple(
                        tuple(x) if isinstance(x, list) else x for x in rng_state
                    )
                self._rng.setstate(rng_state)

            if "fill_rate_per_sec" in state:
                self._settings.fill_rate_per_sec = max(0.0, float(state["fill_rate_per_sec"]))
            if "adverse_selection_ratio" in state:
                self._settings.adverse_selection_ratio = max(
                    0.0, min(1.0, float(state["adverse_selection_ratio"]))
                )
            if "market_slippage_bps" in state:
                self._settings.market_slippage_bps = max(0.0, float(state["market_slippage_bps"]))
            if "realistic_fills" in state:
                self._settings.realistic_fills = bool(state["realistic_fills"])

    def update_settings(self, **kwargs: float | int | None | bool) -> None:
        """Patch one or more settings at runtime."""
        s = self._settings
        if "fill_rate_per_sec" in kwargs:
            s.fill_rate_per_sec = max(0.0, float(kwargs["fill_rate_per_sec"]))
        if "adverse_selection_ratio" in kwargs:
            s.adverse_selection_ratio = max(
                0.0, min(1.0, float(kwargs["adverse_selection_ratio"]))
            )
        if "market_slippage_bps" in kwargs:
            s.market_slippage_bps = max(0.0, float(kwargs["market_slippage_bps"]))
        if "seed" in kwargs:
            seed = kwargs["seed"]
            self._rng = random.Random(seed)
            s.seed = seed  # type: ignore[misc]
        if "realistic_fills" in kwargs:
            s.realistic_fills = bool(kwargs["realistic_fills"])

    # ─── Core fill-probability model ────────────────────────────────────────

    def _fill_probability(self, elapsed_sec: float) -> float:
        """P(at least one fill in *elapsed_sec* seconds)."""
        rate = self._settings.fill_rate_per_sec
        if rate <= 0.0:
            return 0.0
        return 1.0 - math.exp(-rate * max(0.0, elapsed_sec))

    def check_limit_fill(
        self,
        limit_price: float,
        bid: float,
        ask: float,
        elapsed_sec: float,
        side: Side,
    ) -> FillOutcome:
        """Check whether a pending limit order would fill on this tick.

        Parameters
        ----------
        limit_price
            The limit price of the order (bid for buy, ask for sell).
        bid, ask
            Current best bid/ask from the spread buffer tick.
        elapsed_sec
            Seconds since the order was placed.
        side
            ``"buy"`` or ``"sell"``.

        Returns
        -------
        FillOutcome
            Whether filled, the fill price, and the adverse selection cost.
        """
        if not self._settings.realistic_fills:
            # Legacy mode: instant fill, no adverse selection.
            return FillOutcome(filled=True, fill_price=limit_price, adverse_cost=0.0)

        prob = self._fill_probability(elapsed_sec)
        filled = self._rng.random() < prob

        if not filled:
            return FillOutcome(filled=False, fill_price=limit_price, adverse_cost=0.0)

        # Filled — compute adverse selection cost.
        half_spread = (ask - bid) / 2.0
        adverse = self._settings.adverse_selection_ratio * half_spread

        return FillOutcome(
            filled=True,
            fill_price=limit_price,
            adverse_cost=adverse,
        )

    # ─── Market-order exit penalty ──────────────────────────────────────────

    def market_exit_price(
        self,
        bid: float,
        ask: float,
        side: Side,
    ) -> float:
        """Simulate a market-order exit price with slippage.

        For a **sell** (closing a long): receive ``bid`` minus slippage.
        For a **buy** (closing a short): pay ``ask`` plus slippage.

        The slippage is :attr:`ExecutionSettings.market_slippage_bps` applied
        to the mid-price. If L2 VWAP data is available, prefer that — this
        method is a fallback.
        """
        mid = (bid + ask) / 2.0
        if mid <= 0:
            return bid if side == "sell" else ask

        slippage_abs = mid * self._settings.market_slippage_bps / 10_000.0

        if side == "sell":
            return bid - slippage_abs
        else:
            return ask + slippage_abs

    # ─── PNL adjustment ─────────────────────────────────────────────────────

    @staticmethod
    def adjust_pnl_for_adverse_selection(
        gross_pnl: float,
        adverse_cost: float,
        qty: float,
    ) -> float:
        """Subtract adverse selection cost from gross PNL.

        .. math::
            \\text{PNL}_{\\text{adj}} = \\text{PNL}_{\\text{gross}} - \\alpha \\cdot \\frac{A - B}{2} \\cdot Q
        """
        return gross_pnl - adverse_cost * qty


# ─── Persistence ────────────────────────────────────────────────────────────────

def compare_states(state1: dict[str, Any], state2: dict[str, Any]) -> dict[str, Any]:
    """Compare two state dictionaries to check if execution simulators are in sync.

    Useful for backtesting when running multiple processes simultaneously.
    """
    result = {
        "seed_equal": state1.get("seed") == state2.get("seed"),
        "rng_state_equal": state1.get("rng_state") == state2.get("rng_state"),
        "fill_rate_per_sec_equal": state1.get("fill_rate_per_sec") == state2.get("fill_rate_per_sec"),
        "adverse_selection_ratio_equal": state1.get("adverse_selection_ratio") == state2.get("adverse_selection_ratio"),
        "market_slippage_bps_equal": state1.get("market_slippage_bps") == state2.get("market_slippage_bps"),
        "realistic_fills_equal": state1.get("realistic_fills") == state2.get("realistic_fills"),
    }
    result["all_equal"] = all(result.values())
    return result
