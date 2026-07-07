from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BookTickerRow:
    """Normalized row: spot (bookTicker+24h) or futures (contract/ticker)."""

    symbol: str
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float
    mid: float
    spread_abs: float
    spread_bps: float | None  # None if mid <= 0
    volume_24h_base: float
    volume_24h_quote: float
    funding_rate: float | None = None  # futures: funding; spot: None
    observed_at: str | None = None  # ISO8601 UTC, выставляется при сборе снимка
    fee_round_trip_bps: float = 0.0  # 2 × taker one-way (bps), модель в config/execution
    net_spread_bps: float | None = None  # gross spread_bps − fee_round_trip_bps
    l1_max_executable_base: float = 0.0  # min(bid_qty, ask_qty) при L1; фьюч. часто 0 — нет qty в тикере
    l1_max_notional_quote: float = 0.0  # l1_max_executable_base × mid
    reference_quote_notional: float | None = None  # из config; если задано — сравнение с L1
    l1_covers_reference_notional: bool | None = None  # l1_max_notional_quote >= reference
    # L2 depth (VWAP-based) — заполняется при наличии orderbook depth
    vwap_buy_price: float | None = None  # VWAP для market-buy на reference_notional
    vwap_sell_price: float | None = None  # VWAP для market-sell на reference_notional
    slippage_buy_bps: float | None = None  # проскальзывание market-buy (bps)
    slippage_sell_bps: float | None = None  # проскальзывание market-sell (bps)
    executable_buy_notional: float = 0.0  # суммарная глубина ask (quote)
    executable_sell_notional: float = 0.0  # суммарная глубина bid (quote)
    depth_levels: int = 0  # доступных уровней (min bid/ask)


@dataclass(frozen=True)
class CrossSpreadRow:
    """Связка спот + USDT-M perp: базис по mid (fut_mid − spot_mid)."""

    symbol_spot: str
    symbol_futures: str
    spot_bid: float
    spot_ask: float
    spot_mid: float
    spot_spread_bps: float | None
    fut_bid: float
    fut_ask: float
    fut_mid: float
    fut_spread_bps: float | None
    basis_mid_abs: float
    basis_mid_bps: float | None
    funding_rate: float | None
    volume_24h_base_spot: float
    volume_24h_quote_spot: float
    volume_24h_base_fut: float
    volume_24h_quote_fut: float
    observed_at: str | None = None
