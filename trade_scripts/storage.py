"""
storage.py — CSV writer for OHLCV data compatible with analyze_ta.py.

Output CSVs match the format consumed by ``analyze_ta.py``:
    timestamp, open, high, low, close, volume
with ISO datetime strings in the timestamp column.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pandas as pd


def save_candles(df: pd.DataFrame, path: str) -> Path:
    """Save processed candles to CSV matching analyze_ta.py input format.

    Format: timestamp, open, high, low, close, volume
    Timestamp format: ISO datetime string (e.g. "2024-01-01 00:00:00").
    Uses atomic write (temp file + rename) to prevent partial reads.

    Args:
        df: Processed candle DataFrame (from process_candles()).
        path: Output CSV file path.

    Returns:
        The Path of the saved file.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    df_out = df.copy()
    if not df_out.empty and pd.api.types.is_datetime64_any_dtype(df_out["timestamp"]):
        df_out["timestamp"] = df_out["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    fd, tmp_path = tempfile.mkstemp(dir=str(out.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            df_out.to_csv(f, index=False)
        shutil.move(tmp_path, str(out))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return out


def load_candles(path: str) -> pd.DataFrame:
    """Load CSV saved by save_candles() — roundtrip verification.

    Args:
        path: Path to CSV file saved by save_candles().

    Returns:
        DataFrame with datetime index and numeric OHLCV columns.
    """
    return pd.read_csv(path, parse_dates=["timestamp"])
