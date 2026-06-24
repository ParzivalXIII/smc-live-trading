"""
Streaming Pass Wrapper — Regression Detection Tool

Usage:
    python tests/stream_compare.py <path_to_csv> [options]

Runs both batch and streaming modes on any OHLC dataset and reports differences.
This is a diagnostic tool for verifying causality of swing_highs_lows().
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(__file__)
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "..")))
from smartmoneyconcepts.smc import smc


def streaming_backtest(df, swing_length, confirmation_bars, atr_multiplier, atr_period):
    """Run the engine in pure streaming mode."""
    engine = smc._SwingEngine(swing_length, confirmation_bars, atr_multiplier, atr_period)
    outputs = []
    for i in range(len(df)):
        row = df.iloc[i]
        result = engine.update(i, row)
        outputs.append({"HighLow": result["HighLow"], "Level": result["Level"]})
    return pd.DataFrame(outputs)


def compare(csv_path, swing_length=5, confirmation_bars=2, atr_multiplier=1.5, atr_period=7):
    """Run comparison and report results."""
    # Load data
    df = pd.read_csv(csv_path)
    if "Date" in df.columns:
        df = df.set_index("Date")
    
    # Lowercase columns for engine
    df_lower = df.rename(columns={c: c.lower() for c in df.columns})
    
    print(f"Dataset: {os.path.basename(csv_path)}")
    print(f"Rows: {len(df_lower)}")
    print(f"Parameters: swing_length={swing_length}, confirmation_bars={confirmation_bars}, "
          f"atr_multiplier={atr_multiplier}, atr_period={atr_period}")
    print()
    
    # Batch mode
    print("Running batch mode...")
    batch = smc.swing_highs_lows(
        df_lower,
        swing_length=swing_length,
        confirmation_bars=confirmation_bars,
        atr_multiplier=atr_multiplier,
        atr_period=atr_period,
    )
    batch = batch.reset_index(drop=True)
    
    # Streaming mode
    print("Running streaming mode...")
    stream = streaming_backtest(df_lower, swing_length, confirmation_bars, atr_multiplier, atr_period)
    
    # Compare
    print("Comparing...")
    diff_mask = (batch["HighLow"].fillna(-999) != stream["HighLow"].fillna(-999)) | \
                (batch["Level"].fillna(-999) != stream["Level"].fillna(-999))
    
    n_diff = int(diff_mask.sum())
    
    if n_diff == 0:
        print("\n" + "=" * 50)
        print("PASS: 0 differences — implementation is causal")
        print("=" * 50)
        return True
    else:
        diff_rows = np.where(diff_mask)[0]
        print(f"\nFAIL: {n_diff} differences at rows:")
        for row_idx in diff_rows[:20]:  # Show first 20
            print(f"  Row {row_idx}:")
            print(f"    Batch:   HighLow={batch.iloc[row_idx]['HighLow']}, "
                  f"Level={batch.iloc[row_idx]['Level']}")
            print(f"    Stream:  HighLow={stream.iloc[row_idx]['HighLow']}, "
                  f"Level={stream.iloc[row_idx]['Level']}")
        if len(diff_rows) > 20:
            print(f"  ... and {len(diff_rows) - 20} more differences")
        print("=" * 50)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Compare batch vs streaming swing detection"
    )
    parser.add_argument("csv_path", help="Path to OHLC CSV file")
    parser.add_argument("--swing-length", type=int, default=5)
    parser.add_argument("--confirmation-bars", type=int, default=2)
    parser.add_argument("--atr-multiplier", type=float, default=1.5)
    parser.add_argument("--atr-period", type=int, default=7)
    
    args = parser.parse_args()
    
    success = compare(
        args.csv_path,
        swing_length=args.swing_length,
        confirmation_bars=args.confirmation_bars,
        atr_multiplier=args.atr_multiplier,
        atr_period=args.atr_period,
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
