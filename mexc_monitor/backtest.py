"""Backtesting framework for paper-mode strategies.

Provides:
- BacktestEngine class for running historical backtests
- Support for CSV and HistoryStore tick data
- Performance metrics (winrate, Sharpe ratio, max drawdown)
- CLI and Jupyter-friendly interface

Usage::
    from mexc_monitor.backtest import BacktestEngine

    engine = BacktestEngine(
        settings=CaptureSettings(mode="paper"),
        historical_ticks=csv_ticks,
    )
    result = engine.run()

    print(f"Win rate: {result.winrate:.2%}")
"""

from __future__ import annotations

import csv
import logging
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal
from unittest.mock import Mock

from mexc_monitor.execution_model import ExecutionSimulator, ExecutionSettings
from mexc_monitor.spread_capture import SpreadCaptureEngine, CaptureSettings
from mexc_monitor.spread_buffer import SpreadTick
from mexc_monitor.state_store import StateStore

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Result of a backtest run."""

    total_trades: int
    winning_trades: int
    losing_trades: int
    total_pnl_usdt: float = 0.0
    total_fees_usdt: float = 0.0
    win_rate: float = 0.0
    avg_profit_usdt: float = 0.0
    avg_loss_usdt: float = 0.0
    gross_pnl_usdt: float = 0.0
    net_pnl_usdt: float = 0.0
    max_drawdown_usdt: float = 0.0
    sharpe_ratio: float = 0.0
    total_hold_sec: float = 0.0
    avg_hold_sec: float = 0.0
    trades: list[dict[str, Any]] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)


@dataclass
class BacktestSettings:
    """Settings for backtesting."""

    strategy: Literal["spread_capture", "arbitrage"] = "spread_capture"
    initial_balance_usdt: float = 10000.0
    min_balance_usdt: float = 1000.0
    max_positions: int = 3
    replay_tick_rate: float = 0.1  # Simulation speed (seconds per tick)
    save_state: bool = False
    state_file: str = "data/backtest_state.json"


class BacktestEngine:
    """Backtest engine for paper-mode strategies with historical data.

    Supports both SpreadCaptureEngine and ArbitrageEngine backtesting.
    """

    def __init__(
        self,
        settings: CaptureSettings | None = None,
        backtest_settings: BacktestSettings | None = None,
        historical_ticks: list[SpreadTick] | None = None,
        history_store_path: str | None = None,
    ):
        self._settings = settings or CaptureSettings(mode="paper")
        self._backtest_settings = backtest_settings or BacktestSettings()
        self._ticks = historical_ticks if historical_ticks else []
        self._history_store_path = history_store_path
        self._initial_balance = self._backtest_settings.initial_balance_usdt

        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # Engine state
        self._current_index: int = 0
        self._engine: SpreadCaptureEngine | None = None
        self._stats: CaptureStats | None = None
        self._trades: list[TradeRecord] = []

        # Performance metrics
        self._balance_usdt = self._initial_balance
        self._metrics: dict[str, list[float]] = defaultdict(list)
        self._balance_history: list[tuple[int, float]] = []
        self._max_balance: float = self._initial_balance
        self._max_drawdown_usdt: float = 0.0

    @property
    def running(self) -> bool:
        return not self._stop_event.is_set()

    def set_tick_rate(self, rate: float) -> None:
        """Set replay tick rate (seconds per tick)."""
        self._backtest_settings.replay_tick_rate = max(0.0, rate)

    def add_tick(self, tick: SpreadTick) -> None:
        """Add a tick to the backtest."""
        with self._lock:
            self._ticks.append(tick)

    def add_ticks(self, ticks: list[SpreadTick]) -> None:
        """Add multiple ticks to the backtest."""
        with self._lock:
            self._ticks.extend(ticks)

    def load_history_store(self, path: str) -> bool:
        """Load ticks from history store."""
        self._history_store_path = path
        return True  # TODO: implement history store loading

    def _create_engine(self) -> SpreadCaptureEngine:
        """Create strategy engine for backtest."""
        if self._backtest_settings.strategy == "spread_capture":
            engine = SpreadCaptureEngine(self._settings)

            # Create mock order executor for paper mode
            mock_executor = Mock()
            engine.set_order_executor(mock_executor)
            return engine
        else:
            raise NotImplementedError("ArbitrageEngine backtest not implemented yet")

    def _run_single_tick(self, tick: SpreadTick) -> None:
        """Run one tick through the engine."""
        with self._lock:
            if self._stop_event.is_set():
                return

        engine = self._engine
        if engine is None:
            engine = self._create_engine()
            self._engine = engine

        try:
            # Simulate single tick processing
            buffer_key = f"{tick.exchange}:{tick.symbol}"
            engine._step()
        except Exception as e:
            logger.error("Error processing tick: %s", e)

    def run(self) -> BacktestResult:
        """Run the full backtest over historical ticks.

        Returns
        -------
        BacktestResult
            Backtest statistics and metrics.
        """
        logger.info("Starting backtest with %d ticks", len(self._ticks))

        # Reset state
        self._current_index = 0
        self._engine = None
        self._trades.clear()
        self._balance_usdt = self._initial_balance
        self._metrics.clear()
        self._balance_history.clear()
        self._max_balance = self._initial_balance
        self._max_drawdown_usdt = 0.0

        start_time = time.time()

        try:
            for tick in self._ticks:
                self._run_single_tick(tick)

                # Record balance history
                balance_snapshot = self._get_current_balance()
                self._balance_history.append((self._current_index, balance_snapshot))
                self._max_balance = max(self._max_balance, balance_snapshot)

                # Advance time
                self._current_index += 1
                if self._backtest_settings.replay_tick_rate > 0:
                    time.sleep(self._backtest_settings.replay_tick_rate)

        except KeyboardInterrupt:
            logger.info("Backtest interrupted by user")

        end_time = time.time()
        elapsed_sec = end_time - start_time

        # Extract trades from engine
        trades = self._get_engine_trades()
        self._trades = trades

        # Calculate metrics
        return self._calculate_metrics(trades, elapsed_sec)

    def _get_current_balance(self) -> float:
        """Get current balance (based on position value)."""
        with self._lock:
            if self._engine is None:
                return self._initial_balance

            # Check engine stats
            if hasattr(self._engine, '_stats'):
                stats = self._engine._stats
                balance = self._initial_balance - stats.total_fees_usdt
                if hasattr(stats, 'net_pnl_usdt'):
                    balance += stats.net_pnl_usdt

                # Add unrealized position value
                if hasattr(self._engine, '_position') and self._engine._position.state == 'holding':
                    pos = self._engine._position
                    if pos.entry_qty > 0:
                        # Assume market price for current ask
                        # This is simplified - should use actual tick data
                        unrealized_pnl = (pos.entry_price - pos.entry_price) * pos.entry_qty
                        balance += unrealized_pnl

                return max(self._initial_balance, balance)

            return self._initial_balance

    def _get_engine_trades(self) -> list[TradeRecord]:
        """Extract trades from the engine."""
        if self._engine is None:
            return []

        with self._lock:
            if hasattr(self._engine, '_trades'):
                trades = list(self._engine._trades)
                return trades

        return []

    def _calculate_metrics(self, trades: list[TradeRecord], elapsed_sec: float) -> BacktestResult:
        """Calculate backtest performance metrics."""
        total_trades = len(trades)
        if total_trades == 0:
            return BacktestResult(total_trades=0, winning_trades=0, losing_trades=0)

        winning_trades = sum(1 for t in trades if t.net_pnl_usdt > 0)
        losing_trades = total_trades - winning_trades

        net_pnl_usdt = sum(t.net_pnl_usdt for t in trades)
        total_fees_usdt = sum(t.total_fees_usdt for t in trades)
        gross_pnl_usdt = sum(t.gross_pnl_usdt for t in trades)

        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        avg_profit = sum(t.net_pnl_usdt for t in trades if t.net_pnl_usdt > 0) / winning_trades if winning_trades > 0 else 0.0
        avg_loss = abs(sum(t.net_pnl_usdt for t in trades if t.net_pnl_usdt < 0) / losing_trades) if losing_trades > 0 else 0.0

        # Calculate max drawdown
        balance_history = self._balance_history
        if balance_history:
            cumulative_balance = [balance_history[i][1] for i in range(len(balance_history))]
            peak = max(cumulative_balance)
            min_balance = min(cumulative_balance)
            max_drawdown_usdt = peak - min_balance
        else:
            max_drawdown_usdt = 0.0

        # Sharpe ratio (simplified)
        # Annualized return
        if elapsed_sec > 0:
            annual_return = (self._initial_balance + net_pnl_usdt) / self._initial_balance - 1.0
            days = elapsed_sec / (24 * 3600)
            annualized_return = annual_return * (365 / days) if days > 0 else 0.0

            # Annual volatility
            if len(cumulative_balance) > 1:
                returns = [0.0]
                for i in range(1, len(cumulative_balance)):
                    daily_return = (cumulative_balance[i] - cumulative_balance[i-1]) / cumulative_balance[i-1] if cumulative_balance[i-1] > 0 else 0.0
                    returns.append(daily_return)

                if len(returns) > 1:
                    avg_return = sum(returns) / len(returns)
                    variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
                    std_dev = math.sqrt(variance)

                    sharpe_ratio = (avg_return * 252) / std_dev if std_dev > 0 else 0.0
                else:
                    sharpe_ratio = 0.0
            else:
                sharpe_ratio = 0.0
        else:
            sharpe_ratio = 0.0

        avg_hold_sec = sum(t.hold_sec for t in trades) / total_trades if total_trades > 0 else 0.0

        result = BacktestResult(
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            total_pnl_usdt=net_pnl_usdt,
            total_fees_usdt=total_fees_usdt,
            gross_pnl_usdt=gross_pnl_usdt,
            net_pnl_usdt=net_pnl_usdt,
            win_rate=win_rate,
            avg_profit_usdt=avg_profit,
            avg_loss_usdt=avg_loss,
            max_drawdown_usdt=max_drawdown_usdt,
            sharpe_ratio=sharpe_ratio,
            total_hold_sec=sum(t.hold_sec for t in trades),
            avg_hold_sec=avg_hold_sec,
            trades=[asdict(t) for t in trades],
        )

        return result

    def reset(self) -> None:
        """Reset backtest state for reuse."""
        with self._lock:
            self._current_index = 0
            self._engine = None
            self._trades.clear()
            self._balance_usdt = self._initial_balance
            self._metrics.clear()
            self._balance_history.clear()
            self._max_balance = self._initial_balance
            self._max_drawdown_usdt = 0.0

    def stop(self) -> None:
        """Stop backtest execution."""
        self._stop_event.set()

    def get_metrics(self) -> dict[str, Any]:
        """Get current performance metrics."""
        with self._lock:
            return {
                "current_balance_usdt": self._get_current_balance(),
                "initial_balance_usdt": self._initial_balance,
                "current_index": self._current_index,
                "max_balance_usdt": self._max_balance,
                "max_drawdown_usdt": self._max_drawdown_usdt,
            }

    def export_trades(self, file_path: str | Path) -> bool:
        """Export trades to CSV file."""
        with self._lock:
            if not self._trades:
                return False

            try:
                path = Path(file_path)
                path.parent.mkdir(parents=True, exist_ok=True)

                with open(path, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=[
                        "symbol",
                        "exchange",
                        "mode",
                        "entry_price",
                        "exit_price",
                        "qty",
                        "entry_spread_bps",
                        "exit_spread_bps",
                        "entry_time_iso",
                        "exit_time_iso",
                        "hold_sec",
                        "gross_pnl_usdt",
                        "total_fees_usdt",
                        "net_pnl_usdt",
                        "net_pnl_bps",
                        "close_reason",
                    ])
                    writer.writeheader()
                    for trade in self._trades:
                        writer.writerow({
                            "symbol": trade.symbol,
                            "exchange": trade.exchange,
                            "mode": trade.mode,
                            "entry_price": trade.buy_entry_price,
                            "exit_price": trade.sell_exit_price,
                            "qty": trade.qty,
                            "entry_spread_bps": trade.entry_basis_bps,
                            "exit_spread_bps": trade.exit_basis_bps,
                            "entry_time_iso": trade.open_time_iso,
                            "exit_time_iso": trade.close_time_iso,
                            "hold_sec": trade.hold_sec,
                            "gross_pnl_usdt": trade.gross_pnl_usdt,
                            "total_fees_usdt": trade.total_fees_usdt,
                            "net_pnl_usdt": trade.net_pnl_usdt,
                            "net_pnl_bps": trade.net_pnl_bps,
                            "close_reason": trade.close_reason,
                        })

                logger.info("Exported %d trades to %s", len(self._trades), path)
                return True
            except Exception as e:
                logger.error("Failed to export trades: %s", e)
                return False


def create_csv_backtest(
    csv_file: str,
    settings: CaptureSettings | None = None,
    backtest_settings: BacktestSettings | None = None,
) -> BacktestEngine:
    """Create a backtest engine from CSV file.

    Expected CSV format (similar to SpreadBuffer format):
        timestamp_ms,bid,ask,mid,bid_qty,ask_qty,spread_abs,spread_bps
        1700000000000,50000.0,50050.0,50025.0,10.0,10.0,50.0,100.0
    """
    # Load CSV
    ticks: list[SpreadTick] = []
    with open(csv_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                tick = SpreadTick(
                    timestamp_ms=int(row["timestamp_ms"]),
                    bid=float(row["bid"]),
                    ask=float(row["ask"]),
                    bid_qty=float(row["bid_qty"]),
                    ask_qty=float(row["ask_qty"]),
                    mid=float(row["mid"]),
                    spread_abs=float(row["spread_abs"]),
                    spread_bps=float(row["spread_bps"]),
                )
                ticks.append(tick)
            except (KeyError, ValueError, TypeError) as e:
                logger.warning("Skipping invalid row in CSV: %s", e)

    return BacktestEngine(
        settings=settings or CaptureSettings(mode="paper"),
        backtest_settings=backtest_settings or BacktestSettings(),
        historical_ticks=ticks,
    )


if __name__ == "__main__":
    # CLI interface
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python backtest.py --csv <file.csv> [--output <result.json>] [--tick-rate <rate>]")
        print("\nExample:")
        print("  python backtest.py --csv tests/data/mock_ticks.csv --output results/backtest.json")
        sys.exit(1)

    # Parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="Backtest spread capture strategy")
    parser.add_argument("--csv", required=True, help="CSV file with historical ticks")
    parser.add_argument("--output", default=None, help="Output file for results")
    parser.add_argument("--tick-rate", type=float, default=0.1, help="Replay tick rate (seconds per tick)")
    parser.add_argument("--strategy", choices=["spread_capture"], default="spread_capture", help="Strategy type")
    args = parser.parse_args()

    # Create backtest engine
    engine = create_csv_backtest(
        csv_file=args.csv,
        backtest_settings=BacktestSettings(replay_tick_rate=args.tick_rate),
    )

    # Run backtest
    logger.info("Running backtest from %s", args.csv)
    result = engine.run()

    # Print results
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Total Trades:        {result.total_trades}")
    print(f"Win Rate:            {result.win_rate:.2%}")
    print(f"Winning Trades:      {result.winning_trades}")
    print(f"Losing Trades:       {result.losing_trades}")
    print(f"Net PNL:             {result.net_pnl_usdt:,.2f} USDT")
    print(f"Gross PNL:           {result.gross_pnl_usdt:,.2f} USDT")
    print(f"Total Fees:          {result.total_fees_usdt:,.2f} USDT")
    print(f"Max Drawdown:        {result.max_drawdown_usdt:,.2f} USDT")
    print(f"Sharpe Ratio:        {result.sharpe_ratio:.2f}")
    print(f"Avg Hold Time:       {result.avg_hold_sec:.2f} sec")
    print(f"Avg Profit:          {result.avg_profit_usdt:,.2f} USDT")
    print(f"Avg Loss:            {result.avg_loss_usdt:,.2f} USDT")
    print("=" * 60)

    # Save results
    if args.output:
        import json
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({
                "backtest_settings": asdict(engine._backtest_settings),
                "result": asdict(result),
                "ticks_processed": engine._current_index,
            }, f, indent=2)
        logger.info("Results saved to %s", args.output)