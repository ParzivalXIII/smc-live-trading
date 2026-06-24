"""
Integration test: streaming confirmed BOS vs batch BOS comparison.

Validates that the two-stage streaming StructureEngine produces results
that align with the batch bos_choch() method.

Key metrics:
  - match_rate: fraction of batch BOS that have a streaming counterpart
  - cancelled_streaming: events cancelled by window expiry
  - avg_confirm_delay: bars from trigger to confirmation
  - pending: events still provisional at end of dataset
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from backtest import BacktestConfig, BacktestHarness
from strategies.bos_flip import BOSFlipStrategy

# Dataset path (relative to project root)
DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"

# How close levels must match (floating point tolerance)
LEVEL_TOLERANCE = 0.001  # 0.1%


def test_streaming_vs_batch():
    """Compare streaming confirmed BOS events against batch BOS."""
    config = BacktestConfig(
        bos_confirmation_window=10,
    )
    strategy = BOSFlipStrategy()
    harness = BacktestHarness(config, strategy_callback=strategy)

    # Resolve data path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    full_path = os.path.join(project_root, DATA_PATH)

    result = harness.run(full_path)

    report = result.report
    structure_events = result.structure_events
    structure_engine = result.structure_engine

    # ---- Collect batch BOS events ----
    batch_bos_indices = []
    for i in range(len(report)):
        bos_val = report.iloc[i]["BOS"]
        if not (np.isnan(bos_val) if isinstance(bos_val, float) else bos_val is None or bos_val == 0):
            bos_dir = 1 if bos_val == 1 else -1
            level = report.iloc[i]["BOSLevel"]
            if not np.isnan(level):
                batch_bos_indices.append({
                    "index": i,
                    "direction": bos_dir,
                    "level": float(level),
                })

    # ---- Collect streaming confirmed BOS events ----
    streaming_confirmed = [
        e for e in structure_engine.events
        if e.event_type == "BOS" and e.status == "confirmed"
    ]
    streaming_cancelled = [
        e for e in structure_engine.events
        if e.event_type == "BOS" and e.status == "cancelled"
    ]
    streaming_provisional = [
        e for e in structure_engine.events
        if e.event_type == "BOS" and e.status == "provisional"
    ]

    # ---- Match batch BOS to streaming confirmed ----
    matched_batch_indices = set()
    matched_streaming_indices = set()

    for bi, batch in enumerate(batch_bos_indices):
        for si, stream in enumerate(streaming_confirmed):
            if si in matched_streaming_indices:
                continue
            # Match direction
            if stream.direction != batch["direction"]:
                continue
            # Match level within tolerance (0.1%)
            if abs(stream.level - batch["level"]) / max(abs(batch["level"]), 1e-10) > LEVEL_TOLERANCE:
                continue
            # Match swing_index roughly (batch stamp index ± some tolerance)
            # In batch, BOS is stamped at S1's position = last_positions[-2]
            # In streaming, swing_index = S1's index
            index_diff = abs(stream.swing_index - batch["index"])
            # There can be an offset of ~2 bars due to batch vs streaming timing
            if index_diff > 5:
                continue
            matched_batch_indices.add(bi)
            matched_streaming_indices.add(si)
            break

    # ---- Compute statistics ----
    total_batch = len(batch_bos_indices)
    total_streaming = len(streaming_confirmed)
    match_count = len(matched_batch_indices)
    match_rate = match_count / total_batch if total_batch > 0 else 0.0

    # Cancelled stats
    cancelled_count = len(streaming_cancelled)
    provisional_count = len(streaming_provisional)

    # Confirm delay
    delays = [
        e.confirmed_at_index - e.trigger_index
        for e in streaming_confirmed
        if e.confirmed_at_index is not None
    ]
    avg_confirm_delay = float(np.mean(delays)) if delays else 0.0
    max_confirm_delay = int(np.max(delays)) if delays else 0
    min_confirm_delay = int(np.min(delays)) if delays else 0

    # ---- Print statistics ----
    print(f"\n{'=' * 60}")
    print("Streaming vs Batch BOS Comparison")
    print(f"{'=' * 60}")
    print(f"Dataset: EURUSD-15M ({len(report)} rows)")
    print(f"Confirmation window: {config.bos_confirmation_window} bars")
    print(f"\n-- Batch BOS --")
    print(f"  Total batch BOS:     {total_batch}")
    print(f"\n-- Streaming Confirmed BOS --")
    print(f"  Total confirmed:     {total_streaming}")
    print(f"  Cancelled:           {cancelled_count}")
    print(f"  Provisional (end):   {provisional_count}")
    print(f"\n-- Cross-validation --")
    print(f"  Matched:             {match_count}")
    print(f"  Match rate:          {match_rate:.4f} ({match_rate*100:.1f}%)")
    print(f"  Unmatched batch:     {total_batch - match_count}")
    print(f"  Unmatched streaming: {total_streaming - len(matched_streaming_indices)}")
    print(f"\n-- Confirmation delay (bars) --")
    if delays:
        print(f"  Mean delay:          {avg_confirm_delay:.2f}")
        print(f"  Min delay:           {min_confirm_delay}")
        print(f"  Max delay:           {max_confirm_delay}")

    # ---- Assertions ----
    assert total_batch > 0, "Should have batch BOS events"
    assert total_streaming > 0, "Should have streaming confirmed events"
    assert match_rate >= 0.40, (
        f"Match rate {match_rate:.4f} is too low. "
        f"Expected >= 0.40. This may indicate a logic error or "
        f"the break-in-the-gap blind spot is larger than expected."
    )
    assert cancelled_count > 0, (
        "Expected some streaming events to expire (cancelled). "
        "Zero cancellations means window is too large."
    )
    assert avg_confirm_delay >= 0, f"Avg confirm delay should be >= 0, got {avg_confirm_delay}"
    assert provisional_count >= 0, "Provisional count should be >= 0"

    print(f"\n{'=' * 60}")
    print("ALL STREAMING VS BATCH CHECKS PASSED")
    print(f"{'=' * 60}")

    return {
        "total_batch_bos": total_batch,
        "total_streaming_confirmed": total_streaming,
        "match_count": match_count,
        "match_rate": match_rate,
        "cancelled_streaming": cancelled_count,
        "avg_confirm_delay": avg_confirm_delay,
        "min_confirm_delay": min_confirm_delay,
        "max_confirm_delay": max_confirm_delay,
        "provisional_end": provisional_count,
    }


if __name__ == "__main__":
    test_streaming_vs_batch()
