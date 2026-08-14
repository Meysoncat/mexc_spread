"""Backtest Engine — replay historical snapshots to evaluate trading strategies.

Reads spread snapshots from SQLite and simulates a simple spread-capture strategy:
- Enter when net_spread_bps >= entry_threshold
- Exit when net_spread_bps <= exit_threshold or after max_hold_sec
- Track PnL, win rate, max drawdown
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mexc_monitor.history_store import query_recent


@dataclass
class BacktestSettings:
    """Parameters for backtest simulation."""
    symbol: str = "BTCUSDT"
    market: str = "futures"
    entry_threshold_bps: float = 30.0
    exit_threshold_bps: float = 5.0
    order_notional_usdt: float = 1000.0
    taker_fee_bps: float = 2.0
    max_hold_sec: int = 300
    min_hold_sec: int = 10


@dataclass
class BacktestTrade:
    """Single trade in backtest."""
    entry_time: str
    exit_time: str
    entry_spread_bps: float
    exit_spread_bps: float
    hold_sec: float
    gross_pnl_bps: float
    net_pnl_bps: float
    net_pnl_usdt: float
    exit_reason: str  # "threshold" | "timeout" | "end"


@dataclass
class BacktestResult:
    """Complete backtest results."""
    settings: BacktestSettings
    total_snapshots: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl_usdt: float
    total_pnl_bps: float
    avg_pnl_bps: float
    max_drawdown_usdt: float
    max_consecutive_losses: int
    avg_hold_sec: float
    trades: list[BacktestTrade] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings": {
                "symbol": self.settings.symbol,
                "market": self.settings.market,
                "entry_threshold_bps": self.settings.entry_threshold_bps,
                "exit_threshold_bps": self.settings.exit_threshold_bps,
                "order_notional_usdt": self.settings.order_notional_usdt,
                "taker_fee_bps": self.settings.taker_fee_bps,
                "max_hold_sec": self.settings.max_hold_sec,
            },
            "summary": {
                "total_snapshots": self.total_snapshots,
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "win_rate": round(self.win_rate, 4),
                "total_pnl_usdt": round(self.total_pnl_usdt, 4),
                "total_pnl_bps": round(self.total_pnl_bps, 2),
                "avg_pnl_bps": round(self.avg_pnl_bps, 2),
                "max_drawdown_usdt": round(self.max_drawdown_usdt, 4),
                "max_consecutive_losses": self.max_consecutive_losses,
                "avg_hold_sec": round(self.avg_hold_sec, 1),
            },
            "trades": [
                {
                    "entry_time": t.entry_time,
                    "exit_time": t.exit_time,
                    "entry_spread_bps": round(t.entry_spread_bps, 2),
                    "exit_spread_bps": round(t.exit_spread_bps, 2),
                    "hold_sec": round(t.hold_sec, 1),
                    "gross_pnl_bps": round(t.gross_pnl_bps, 2),
                    "net_pnl_bps": round(t.net_pnl_bps, 2),
                    "net_pnl_usdt": round(t.net_pnl_usdt, 4),
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ],
        }


def run_backtest(db_path: Path, settings: BacktestSettings) -> BacktestResult:
    """Run backtest on historical snapshots from SQLite."""
    # Load snapshots for symbol
    rows = query_recent(
        db_path,
        market=settings.market,
        symbol=settings.symbol,
        since_iso=None,
        limit=100_000,
    )

    if not rows:
        return BacktestResult(
            settings=settings,
            total_snapshots=0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            total_pnl_usdt=0.0,
            total_pnl_bps=0.0,
            avg_pnl_bps=0.0,
            max_drawdown_usdt=0.0,
            max_consecutive_losses=0,
            avg_hold_sec=0.0,
        )

    fee_bps = settings.taker_fee_bps * 2  # round-trip
    trades: list[BacktestTrade] = []
    in_trade = False
    entry_time = ""
    entry_spread = 0.0
    entry_idx = 0

    for i, row in enumerate(rows):
        net_bps = row.get("net_spread_bps")
        if net_bps is None:
            continue
        observed_at = row.get("observed_at", "")

        if not in_trade:
            # Check entry condition
            if net_bps >= settings.entry_threshold_bps:
                in_trade = True
                entry_time = observed_at
                entry_spread = net_bps
                entry_idx = i
        else:
            # Check exit conditions
            hold_sec = 0
            if i > entry_idx:
                # Estimate hold time from snapshot count (rough)
                hold_sec = (i - entry_idx) * 5  # assume ~5 sec between snapshots

            exit_reason = None
            if net_bps <= settings.exit_threshold_bps:
                exit_reason = "threshold"
            elif hold_sec >= settings.max_hold_sec:
                exit_reason = "timeout"
            elif i == len(rows) - 1:
                exit_reason = "end"

            if exit_reason:
                gross_pnl_bps = entry_spread - net_bps  # spread narrowed = profit
                net_pnl_bps = gross_pnl_bps - fee_bps
                net_pnl_usdt = net_pnl_bps / 10_000 * settings.order_notional_usdt

                trades.append(BacktestTrade(
                    entry_time=entry_time,
                    exit_time=observed_at,
                    entry_spread_bps=entry_spread,
                    exit_spread_bps=net_bps,
                    hold_sec=hold_sec,
                    gross_pnl_bps=gross_pnl_bps,
                    net_pnl_bps=net_pnl_bps,
                    net_pnl_usdt=net_pnl_usdt,
                    exit_reason=exit_reason,
                ))
                in_trade = False

    # Compute statistics
    winning = [t for t in trades if t.net_pnl_usdt > 0]
    losing = [t for t in trades if t.net_pnl_usdt <= 0]
    total_pnl = sum(t.net_pnl_usdt for t in trades)

    # Max drawdown
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.net_pnl_usdt
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Max consecutive losses
    max_consec = 0
    consec = 0
    for t in trades:
        if t.net_pnl_usdt <= 0:
            consec += 1
            max_consec = max(max_consec, consec)
        else:
            consec = 0

    avg_hold = sum(t.hold_sec for t in trades) / len(trades) if trades else 0
    avg_pnl = sum(t.net_pnl_bps for t in trades) / len(trades) if trades else 0

    return BacktestResult(
        settings=settings,
        total_snapshots=len(rows),
        total_trades=len(trades),
        winning_trades=len(winning),
        losing_trades=len(losing),
        win_rate=len(winning) / len(trades) if trades else 0,
        total_pnl_usdt=total_pnl,
        total_pnl_bps=sum(t.net_pnl_bps for t in trades),
        avg_pnl_bps=avg_pnl,
        max_drawdown_usdt=max_dd,
        max_consecutive_losses=max_consec,
        avg_hold_sec=avg_hold,
        trades=trades,
    )
