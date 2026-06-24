"""
Shared fixtures and path setup for the analyze_ta test suite.

Fixtures provide deterministic synthetic OHLCV DataFrames with 100 rows each.
All fixtures have function scope (fresh copy per test).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Make project root importable (matches existing test pattern)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------
def _date_range(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="1h")


def _ohlcv_from_close(close: np.ndarray, *, fixed_high: float | None = None,
                       fixed_low: float | None = None) -> pd.DataFrame:
    """Build a full OHLCV DataFrame from a close-price array.

    - open = roll of close (prior bar's close, first equals close[0])
    - high = fixed_high if given else max(open, close) * 1.002
    - low  = fixed_low if given else min(open, close) * 0.998
    - volume = 1000 (constant)
    """
    n = len(close)
    ts = _date_range(n)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    if fixed_high is not None:
        high = np.full(n, float(fixed_high))
    else:
        high = np.maximum(open_, close) * 1.002
    if fixed_low is not None:
        low = np.full(n, float(fixed_low))
    else:
        low = np.minimum(open_, close) * 0.998
    volume = np.full(n, 1000.0)
    return pd.DataFrame({
        "timestamp": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def constant_price_df() -> pd.DataFrame:
    """100 rows, close=100, high=101, low=99, open=100, volume=1000."""
    close = np.full(100, 100.0)
    return _ohlcv_from_close(close, fixed_high=101.0, fixed_low=99.0)


@pytest.fixture(scope="function")
def linear_trend_df() -> pd.DataFrame:
    """100 rows, close from 100 to 200 linearly."""
    close = np.linspace(100.0, 200.0, 100)
    return _ohlcv_from_close(close)


@pytest.fixture(scope="function")
def step_function_df() -> pd.DataFrame:
    """100 rows, step change at row 50 (close 100 → 110)."""
    close = np.concatenate([np.full(50, 100.0), np.full(50, 110.0)])
    return _ohlcv_from_close(close)


@pytest.fixture(scope="function")
def sinusoidal_df() -> pd.DataFrame:
    """100 rows, sin wave oscillation around 100."""
    t = np.linspace(0, 2 * np.pi, 100)
    close = 100.0 + 10.0 * np.sin(t)
    return _ohlcv_from_close(close)


@pytest.fixture(scope="function")
def tmp_csv_dir(tmp_path: Path) -> Path:
    """Temporary directory for CSV I/O tests."""
    return tmp_path
