"""
processor.py — Normalize, sort, deduplicate raw CCXT OHLCV candles.

Converts raw CCXT candle tuples into a clean pandas DataFrame with
canonical columns, sorted ascending by timestamp, duplicates removed
(keeping the last occurrence), and NaN/INF values cleaned.
"""

import numpy as np
import pandas as pd

CANONICAL_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def process_candles(candles: list) -> pd.DataFrame:
    """Convert raw CCXT candles to a clean, sorted, deduplicated DataFrame.

    Steps:
    1. Create DataFrame with canonical columns.
    2. Convert millisecond timestamps to datetime.
    3. Sort by timestamp (ascending).
    4. Drop duplicate timestamps (keep newest).
    5. Drop rows with NaN/INF in OHLCV columns.

    Args:
        candles: Raw CCXT candle list
                 [[timestamp_ms, open, high, low, close, volume], ...].

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume.
        All numeric columns are float64; timestamp is datetime64[ns].
    """
    if not candles:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    df = pd.DataFrame(candles, columns=CANONICAL_COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.sort_values("timestamp").reset_index(drop=True)
    df = df.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)

    # Drop rows with NaN or INF in numeric columns
    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=numeric_cols).reset_index(drop=True)

    return df


def validate_candles(df: pd.DataFrame,
                     expected_interval: pd.Timedelta | None = None) -> list:
    """Validate processed candle data. Returns list of warning messages.

    Checks performed:
        - Empty DataFrame.
        - Monotonically increasing timestamps.
        - Duplicate timestamps (after dedup).
        - Large gaps (> 2x expected interval).
        - Zero-volume rows.

    Args:
        df: Processed candle DataFrame (from process_candles()).
        expected_interval: Expected time between candles (e.g. 4h, 1d).
            If None, inferred from the median of actual timestamp deltas.

    Returns:
        List of warning message strings. Empty list means no issues found.
    """
    warnings: list = []

    if df.empty:
        warnings.append("Empty DataFrame")
        return warnings

    # Check monotonic timestamps
    if not df["timestamp"].is_monotonic_increasing:
        warnings.append("Timestamps are not monotonically increasing")

    # Check for duplicate timestamps
    dups = df["timestamp"].duplicated().sum()
    if dups > 0:
        warnings.append(f"Found {dups} duplicate timestamps after dedup")

    # Check for gaps (> 2x expected interval)
    if len(df) > 1:
        deltas = df["timestamp"].diff().dropna()
        max_gap = deltas.max()
        interval = expected_interval or deltas.median()
        threshold = interval * 2
        if max_gap > threshold:
            warnings.append(f"Max gap: {max_gap} (threshold: {threshold})")

    # Check for zero-volume rows
    zero_vol = (df["volume"] == 0).sum()
    if zero_vol > 0:
        warnings.append(f"Found {zero_vol} zero-volume rows")

    return warnings
