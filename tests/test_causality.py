"""
3-Pass Causal Validation Harness

Validates that swing_highs_lows() is fully causal (no look-ahead bias) by:
  Pass 1: Batch mode — call smc.swing_highs_lows() on full dataset
  Pass 2: Streaming mode — call _SwingEngine.update() bar-by-bar
  Pass 3: Compare batch == streaming (proves batch is internally causal)

Additionally runs a per-bar truncation check on a small random sample of
confirmed swings to verify no future data leakage.
"""

import os
import sys
import random
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(__file__)
sys.path.append(os.path.abspath(os.path.join(BASE_DIR, "..")))
from smartmoneyconcepts.smc import smc


def streaming_backtest(df, swing_length, confirmation_bars, atr_multiplier, atr_period):
    """Run the engine in pure streaming mode — each bar sees only past data."""
    engine = smc._SwingEngine(swing_length, confirmation_bars, atr_multiplier, atr_period)
    outputs = []
    for i in range(len(df)):
        row = df.iloc[i]
        result = engine.update(i, row)
        outputs.append({"HighLow": result["HighLow"], "Level": result["Level"]})
    return pd.DataFrame(outputs)


def test_causality_batch_vs_streaming():
    """Pass 1-3: Assert batch output == streaming output."""
    df = pd.read_csv(
        os.path.join(BASE_DIR, "test_data", "EURUSD", "EURUSD_15M.csv")
    )
    df = df.rename(columns={c: c.lower() for c in df.columns})
    
    swing_length = 5
    confirmation_bars = 2
    atr_multiplier = 1.5
    atr_period = 7
    
    # Pass 1: Batch mode
    print("  Pass 1: Batch mode...")
    pass1 = smc.swing_highs_lows(
        df, swing_length=swing_length, confirmation_bars=confirmation_bars,
        atr_multiplier=atr_multiplier, atr_period=atr_period,
    )
    pass1 = pass1.reset_index(drop=True)
    
    # Pass 2: Streaming mode
    print("  Pass 2: Streaming mode...")
    pass2 = streaming_backtest(
        df, swing_length, confirmation_bars, atr_multiplier, atr_period
    )
    
    # Pass 3: Compare
    print("  Pass 3: Comparing batch vs streaming...")
    pd.testing.assert_frame_equal(pass1, pass2, check_dtype=False)
    
    # Save passes for record
    pass1.to_csv(
        os.path.join(BASE_DIR, "test_data", "EURUSD", "stream_pass1.csv"),
        index=False,
    )
    pass2.to_csv(
        os.path.join(BASE_DIR, "test_data", "EURUSD", "stream_pass2.csv"),
        index=False,
    )
    
    return pass1, pass2


def test_per_bar_truncation():
    """
    Strict look-ahead detector.
    
    For a random sample of confirmed swings, truncate the data at the swing bar
    and re-run. Verify the output at that index matches the full-run output.
    """
    df = pd.read_csv(
        os.path.join(BASE_DIR, "test_data", "EURUSD", "EURUSD_15M.csv")
    )
    df = df.rename(columns={c: c.lower() for c in df.columns})
    
    swing_length = 5
    confirmation_bars = 2
    atr_multiplier = 1.5
    atr_period = 7
    
    # Full run
    full_result = smc.swing_highs_lows(
        df, swing_length=swing_length, confirmation_bars=confirmation_bars,
        atr_multiplier=atr_multiplier, atr_period=atr_period,
    )
    
    # Find all confirmed swing indices
    swing_indices = np.where(full_result["HighLow"].notna())[0]
    
    if len(swing_indices) == 0:
        return
    
    # Sample up to 30 random swings (balance between coverage and speed)
    sample_size = min(30, len(swing_indices))
    rng = random.Random(42)
    sampled = sorted(rng.sample(list(swing_indices), sample_size))
    
    print(f"  Sampling {len(sampled)} of {len(swing_indices)} confirmed swings...")
    
    for idx in sampled:
        truncated_df = df.iloc[: idx + 1].copy()
        
        truncated_result = smc.swing_highs_lows(
            truncated_df, swing_length=swing_length,
            confirmation_bars=confirmation_bars,
            atr_multiplier=atr_multiplier, atr_period=atr_period,
        )
        
        full_hl = full_result["HighLow"].iloc[idx]
        trunc_hl = truncated_result["HighLow"].iloc[idx]
        full_lvl = full_result["Level"].iloc[idx]
        trunc_lvl = truncated_result["Level"].iloc[idx]
        
        if pd.isna(full_hl) and pd.isna(trunc_hl):
            continue
        if not pd.isna(full_hl) and not pd.isna(trunc_hl):
            assert full_hl == trunc_hl, (
                f"HighLow mismatch at index {idx}: "
                f"full={full_hl}, truncated={trunc_hl}"
            )
            assert abs(full_lvl - trunc_lvl) < 1e-10, (
                f"Level mismatch at index {idx}: "
                f"full={full_lvl}, truncated={trunc_lvl}"
            )
        else:
            raise AssertionError(
                f"Causality violation at index {idx}: "
                f"full HighLow={full_hl}, truncated HighLow={trunc_hl}"
            )
    
    print(f"  All {len(sampled)} truncation checks passed")


def test_pytest():
    """Wrapper for pytest compatibility."""
    test_causality_batch_vs_streaming()
    test_per_bar_truncation()


if __name__ == "__main__":
    print("3-Pass Causal Validation")
    print("=" * 50)
    
    result_hl, _ = test_causality_batch_vs_streaming()
    n_batch = int(result_hl["HighLow"].notna().sum())
    print(f"  Confirmed swings: {n_batch}")
    print("  Batch == Streaming: PASS")
    
    print("\nPer-bar truncation check...")
    test_per_bar_truncation()
    
    print("\n" + "=" * 50)
    print("ALL CAUSALITY CHECKS PASSED")
