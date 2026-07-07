"""Example usage for Tasks 4 & 5: Backtesting Framework & RNG State Persistence.

Demonstrates how to use the BacktestEngine and ExecutionSimulator RNG state serialization.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from mexc_monitor.backtest import (
    BacktestEngine,
    BacktestSettings,
    create_csv_backtest,
)
from mexc_monitor.execution_model import (
    ExecutionSimulator,
    ExecutionSettings,
    compare_states,
)
from mexc_monitor.spread_capture import CaptureEngine, CaptureSettings
from mexc_monitor.spread_buffer import SpreadTick


def task_4_backtesting_example():
    """Example 1: Run a backtest using CSV data.

    This demonstrates Task 4: Backtesting Framework.
    """
    print("=" * 60)
    print("TASK 4: Backtesting Framework Example")
    print("=" * 60)

    # Create a simple CSV file for testing
    csv_file = "data/test_ticks.csv"
    Path(csv_file).parent.mkdir(parents=True, exist_ok=True)

    # Create some synthetic ticks
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp_ms", "bid", "ask", "mid", "bid_qty", "ask_qty", "spread_abs", "spread_bps"
            ],
        )
        writer.writeheader()

        base_price = 50000.0
        for i in range(100):
            spread_bps = 100.0
            spread_abs = base_price * spread_bps / 10000.0
            timestamp_ms = int(time.time() * 1000) + i * 1000

            bid = base_price
            ask = base_price + spread_abs
            mid = (bid + ask) / 2.0

            writer.writerow({
                "timestamp_ms": timestamp_ms,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "bid_qty": 10.0,
                "ask_qty": 10.0,
                "spread_abs": spread_abs,
                "spread_bps": spread_bps,
            })

    print(f"\nCreated test CSV file: {csv_file}")

    # Create backtest engine from CSV
    engine = create_csv_backtest(
        csv_file=csv_file,
        settings=CaptureSettings(mode="paper"),
        backtest_settings=BacktestSettings(
            replay_tick_rate=0.001,  # Fast replay
        ),
    )

    print(f"Created backtest engine with {len(engine._ticks)} ticks")

    # Run the backtest
    print("\nRunning backtest...")
    result = engine.run()

    # Display results
    print(f"\nTotal Trades:        {result.total_trades}")
    print(f"Win Rate:            {result.win_rate:.2%}")
    print(f"Winning Trades:      {result.winning_trades}")
    print(f"Losing Trades:       {result.losing_trades}")
    print(f"Net PNL:             {result.net_pnl_usdt:,.2f} USDT")
    print(f"Sharpe Ratio:        {result.sharpe_ratio:.2f}")
    print(f"Max Drawdown:        {result.max_drawdown_usdt:,.2f} USDT")
    print(f"Avg Hold Time:       {result.avg_hold_sec:.2f} sec")

    # Export trades
    output_file = "data/backtest_example_trades.csv"
    engine.export_trades(output_file)
    print(f"\nExported trades to: {output_file}")

    print("\n" + "=" * 60)


def task_5_rng_persistence_example():
    """Example 2: Serialize and deserialize ExecutionSimulator state.

    This demonstrates Task 5: RNG State Persistence.
    """
    print("\n" + "=" * 60)
    print("TASK 5: RNG State Persistence Example")
    print("=" * 60)

    # Create ExecutionSimulator with a fixed seed
    settings = ExecutionSettings(seed=42, fill_rate_per_sec=0.5, realistic_fills=True)
    simulator = ExecutionSimulator(settings)

    print(f"\nCreated ExecutionSimulator with seed={settings.seed}")

    # Simulate some fills
    print("\nSimulating 1000 tick intervals...")
    for i in range(1000):
        if i % 100 == 0:
            print(f"  Tick {i}...")
        simulator.check_limit_fill(
            limit_price=50000.0,
            bid=49999.0,
            ask=50001.0,
            elapsed_sec=1.0,
            side="buy",
        )

    # Serialize state
    print("\nSerializing simulator state...")
    state1 = simulator.serialize_state()

    print(f"  Seed: {state1['seed']}")
    print(f"  Fill rate: {state1['fill_rate_per_sec']}")
    print(f"  Adverse selection: {state1['adverse_selection_ratio']}")
    print(f"  Rng state (first 10 bytes): {state1['rng_state'][:10]}...")

    # Create a new simulator and load the state
    print("\nCreating new simulator and loading serialized state...")
    new_simulator = ExecutionSimulator(ExecutionSettings(seed=999))
    new_simulator.deserialize_state(state1)

    # Verify the state matches
    print("\nVerifying state consistency...")
    comparison = compare_states(state1, new_simulator.serialize_state())

    print(f"  Seed equal: {comparison['seed_equal']}")
    print(f"  Rng state equal: {comparison['rng_state_equal']}")
    print(f"  Fill rate equal: {comparison['fill_rate_per_sec_equal']}")
    print(f"  All equal: {comparison['all_equal']}")

    if comparison['all_equal']:
        print("\n✓ State is perfectly consistent!")
    else:
        print("\n✗ State has differences!")

    # Run more simulations to verify reproducibility
    print("\nVerifying reproducibility...")
    simulator2 = ExecutionSimulator(ExecutionSettings(seed=42))
    simulator2.deserialize_state(state1)

    fills1 = []
    fills2 = []

    for i in range(100):
        outcome1 = simulator.check_limit_fill(50000.0, 49999.0, 50001.0, 1.0, "buy")
        outcome2 = simulator2.check_limit_fill(50000.0, 49999.0, 50001.0, 1.0, "buy")
        fills1.append(outcome1.filled)
        fills2.append(outcome2.filled)

    if fills1 == fills2:
        print("✓ Simulations produce identical results!")
        print(f"  Total fills: {sum(fills1)}")
    else:
        print("✗ Simulations produce different results!")
        print(f"  Simulation 1: {fills1}")
        print(f"  Simulation 2: {fills2}")

    print("\n" + "=" * 60)


def task_4_backtest_with_jupyter():
    """Example 3: Backtest usage in Jupyter Notebook.

    This is how you would use the backtest engine in a Jupyter environment.
    """
    print("\n" + "=" * 60)
    print("TASK 4: Jupyter Notebook Usage Example")
    print("=" * 60)

    print("""
In a Jupyter notebook, you can use the backtest engine like this:

```python
from mexc_monitor.backtest import create_csv_backtest, BacktestSettings

# Load historical data
engine = create_csv_backtest(
    csv_file="data/mexc_history.csv",
    backtest_settings=BacktestSettings(
        replay_tick_rate=0.05,  # Slow down for easier observation
    ),
)

# Run backtest
result = engine.run()

# Display results
print(f"Total PNL: {result.net_pnl_usdt:,.2f} USDT")
print(f"Win Rate: {result.winrate:.2%}")
print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")

# Plot equity curve
import matplotlib.pyplot as plt
balance_history = [b for _, b in engine._balance_history]

plt.figure(figsize=(12, 6))
plt.plot(balance_history)
plt.title("Backtest Equity Curve")
plt.xlabel("Tick Index")
plt.ylabel("Balance (USDT)")
plt.grid(True)
plt.show()

# Export trades
engine.export_trades("results/jupyter_trades.csv")
```

The replay_tick_rate parameter controls the speed:
- 0.001: Very fast (real-time)
- 0.01: Fast (10x real-time)
- 0.1: Slow (100x real-time)
- 1.0: Very slow (1000x real-time)

Use a slower rate when debugging or analyzing edge cases.
""")

    print("=" * 60)


def task_5_multiple_simulators():
    """Example 4: Running multiple backtest simulations.

    This demonstrates how to use RNG state persistence for multiple parallel simulations.
    """
    print("\n" + "=" * 60)
    print("TASK 5: Multiple Simulations with Different Parameters")
    print("=" * 60)

    # Create multiple execution simulators with different parameters
    simulators = [
        ExecutionSimulator(ExecutionSettings(
            seed=i,
            fill_rate_per_sec=0.3,
            adverse_selection_ratio=0.2,
        )) for i in range(5)
    ]

    print(f"\nCreated {len(simulators)} simulators with different parameters")
    print("\nSimulating 500 intervals...")

    for i, simulator in enumerate(simulators):
        fills_count = 0
        for _ in range(500):
            outcome = simulator.check_limit_fill(
                limit_price=50000.0,
                bid=49999.0,
                ask=50001.0,
                elapsed_sec=1.0,
                side="buy",
            )
            if outcome.filled:
                fills_count += 1

        print(f"  Simulator {i+1} (seed={simulator.settings.seed}): {fills_count} fills")

    # Now test reproducibility
    print("\n" + "-" * 60)
    print("Testing reproducibility across multiple processes...")

    # Simulate starting from same RNG state
    base_seed = 123
    base_sim = ExecutionSimulator(ExecutionSettings(seed=base_seed))
    base_sim.check_limit_fill(50000.0, 49999.0, 50001.0, 1.0, "buy")

    # Save state
    state = base_sim.serialize_state()

    # Create new simulators and load the state
    for i in range(5):
        new_sim = ExecutionSimulator(ExecutionSettings(seed=base_seed + i))
        new_sim.deserialize_state(state)

        # Run more simulations
        same_outcomes = 0
        total_runs = 100

        for _ in range(total_runs):
            outcome1 = base_sim.check_limit_fill(50000.0, 49999.0, 50001.0, 1.0, "buy")
            outcome2 = new_sim.check_limit_fill(50000.0, 49999.0, 50001.0, 1.0, "buy")
            if outcome1.filled == outcome2.filled:
                same_outcomes += 1

        match_rate = same_outcomes / total_runs * 100
        print(f"  Process {i+1} match rate: {match_rate:.1f}%")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    # Run all examples
    task_4_backtesting_example()
    task_5_rng_persistence_example()
    task_4_backtest_with_jupyter()
    task_5_multiple_simulators()

    print("\n" + "=" * 60)
    print("ALL EXAMPLES COMPLETED SUCCESSFULLY!")
    print("=" * 60)