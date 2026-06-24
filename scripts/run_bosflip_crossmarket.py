"""
Cross-Market BOSFlipStrategy Baseline Runner.

Runs BOSFlipStrategy on all 5 datasets and saves metrics.
Usage:
    /usr/bin/python3.12 scripts/run_bosflip_crossmarket.py
"""

import json
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from backtest import BacktestConfig, BacktestHarness
from strategies.bos_flip import BOSFlipStrategy

# Output directory
OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".sisyphus",
    "evidence",
    "bosflip-crossmarket",
)

DATASETS = [
    {
        "path": "tests/test_data/cryptocurrencies/binance_api_BTCUSDT_4h.csv",
        "name": "BTCUSDT-4H",
        "date_column": "time",
        "date_format": None,
    },
    {
        "path": "tests/test_data/cryptocurrencies/binance_api_SOLUSDT_4h.csv",
        "name": "SOLUSDT-4H",
        "date_column": "time",
        "date_format": None,
    },
    {
        "path": "tests/test_data/cryptocurrencies/binance_api_ADAUSDT_4h.csv",
        "name": "ADAUSDT-4H",
        "date_column": "time",
        "date_format": None,
    },
    {
        "path": "tests/test_data/cryptocurrencies/binance_api_BNBUSDT_4h.csv",
        "name": "BNBUSDT-4H",
        "date_column": "time",
        "date_format": None,
    },
    {
        "path": "tests/test_data/EURUSD/EURUSD_15M.csv",
        "name": "EURUSD-15M",
        "date_column": "Date",
        "date_format": "%Y.%m.%d %H:%M:%S",
    },
]

# Metrics to include in the output
METRICS_KEYS = [
    "total_trades",
    "wins",
    "losses",
    "win_rate",
    "profit_factor",
    "net_pnl",
    "max_drawdown",
    "avg_trade_bars",
    "median_trade_bars",
]


def run_single_dataset(dataset: dict) -> dict | None:
    """Run BOSFlipStrategy on a single dataset. Returns metrics dict or None on error."""
    name = dataset["name"]
    path = dataset["path"]
    print(f"\n{'=' * 60}")
    print(f"Running {name}...")
    print(f"  Data: {path}")

    # Resolve path relative to project root
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(project_root, path)

    if not os.path.exists(full_path):
        print(f"  ERROR: File not found: {full_path}")
        return None

    try:
        config = BacktestConfig(
            date_column=dataset["date_column"],
            date_format=dataset["date_format"],
        )
        strategy = BOSFlipStrategy()
        harness = BacktestHarness(config, strategy_callback=strategy)

        print(f"  Running backtest...")
        result = harness.run(full_path)

        metrics = result.metrics
        print(f"  Results:")
        for key in METRICS_KEYS:
            val = metrics.get(key, "N/A")
            if isinstance(val, float) and np.isnan(val):
                val = "NaN"
            print(f"    {key}: {val}")

        return {key: _serialize(metrics.get(key)) for key in METRICS_KEYS}

    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return None


def _serialize(val):
    """Convert a value to JSON-safe type."""
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        if np.isnan(val) or np.isinf(val):
            return None
        return float(val)
    if isinstance(val, (np.bool_,)):
        return bool(val)
    return val


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_results = {}
    metrics_rows = []

    for dataset in DATASETS:
        name = dataset["name"]
        result = run_single_dataset(dataset)
        if result is not None:
            all_results[name] = result
            row = {"dataset": name}
            row.update(result)
            metrics_rows.append(row)
        else:
            all_results[name] = {"error": "Failed to run"}
            row = {"dataset": name}
            for k in METRICS_KEYS:
                row[k] = None
            metrics_rows.append(row)

    # Save JSON
    json_path = os.path.join(OUTPUT_DIR, "metrics.json")
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nSaved JSON: {json_path}")

    # Save CSV
    csv_path = os.path.join(OUTPUT_DIR, "metrics.csv")
    df = pd.DataFrame(metrics_rows)
    df.to_csv(csv_path, index=False)
    print(f"Saved CSV: {csv_path}")

    # Summary
    print(f"\n{'=' * 60}")
    print("CROSS-MARKET BASELINE COMPLETE")
    print(f"{'=' * 60}")
    print(f"Datasets processed: {len([r for r in all_results.values() if 'error' not in r])}/{len(DATASETS)}")
    print(f"Output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
