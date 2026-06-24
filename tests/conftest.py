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


# ---------------------------------------------------------------------------
# Market Snapshot test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def sample_ta_row() -> pd.Series:
    """Single TA row with known values for snapshot building tests.

    close=50000, ema21=49000 (above), ema21_slope=0.01 (rising),
    rsi14=60 (>55), mfi14=55 (>50), macd=100 (>macd_signal=90),
    macd_hist=10, atr14=500, bb_width=0.05.
    """
    return pd.Series({
        "timestamp": pd.Timestamp("2024-06-01 00:00:00"),
        "close": 50000.0,
        "ema21": 49000.0,
        "ema21_slope": 0.01,
        "rsi14": 60.0,
        "mfi14": 55.0,
        "macd": 100.0,
        "macd_signal": 90.0,
        "macd_hist": 10.0,
        "atr14": 500.0,
        "bb_width": 0.05,
    })


@pytest.fixture(scope="function")
def sample_smc_report() -> pd.DataFrame:
    """Minimal 26-column per_candle_report with known structure data.

    Contains:
      - A swing high at index 3 (SwingHighLow=1, SwingLevel=51000)
      - A swing low at index 5 (SwingHighLow=-1, SwingLevel=49000)
      - A bullish BOS at index 4 (BOS=1, BrokenIndex=6)
      - A bearish CHOCH at index 3 (CHOCH=-1, BrokenIndex=7)
      - Bullish liquidity at index 2 (Liquidity=1, LiqLevel=51000, LiqSwept=0)
      - Bearish liquidity at index 5 (Liquidity=-1, LiqLevel=48000, LiqSwept=NaN)
      - A bullish OB at index 3 (OB=1, OBBottom=49500, OBMitigatedIndex=0)
      - A bearish OB at index 4 (OB=-1, OBTop=48500, OBMitigatedIndex=NaN)
    """
    n = 20
    data: dict[str, list] = {col: [float("nan")] * n for col in [
        "Timestamp", "Open", "High", "Low", "Close", "Volume",
        "SwingHighLow", "SwingLevel", "SwingPivotIndex",
        "BOS", "CHOCH", "BOSLevel", "BrokenIndex",
        "OB", "OBTop", "OBBottom", "OBVolume", "OBMitigatedIndex", "OBPct",
        "Liquidity", "LiqLevel", "LiqEnd", "LiqSwept",
        "RetraceDirection", "CurrentRetracement%", "DeepestRetracement%",
    ]}

    # Swings
    data["SwingHighLow"][3] = 1.0
    data["SwingLevel"][3] = 51000.0
    data["SwingHighLow"][5] = -1.0
    data["SwingLevel"][5] = 49000.0

    # BOS
    data["BOS"][4] = 1.0
    data["BrokenIndex"][4] = 6.0

    # CHOCH
    data["CHOCH"][3] = -1.0
    data["BrokenIndex"][3] = 7.0

    # Liquidity
    data["Liquidity"][2] = 1.0
    data["LiqLevel"][2] = 51000.0
    data["LiqSwept"][2] = 0.0
    data["Liquidity"][5] = -1.0
    data["LiqLevel"][5] = 48000.0
    data["LiqSwept"][5] = float("nan")

    # Order blocks
    data["OB"][3] = 1.0
    data["OBTop"][3] = 50200.0
    data["OBBottom"][3] = 49500.0
    data["OBMitigatedIndex"][3] = 0.0
    data["OB"][4] = -1.0
    data["OBTop"][4] = 48500.0
    data["OBBottom"][4] = 48000.0
    data["OBMitigatedIndex"][4] = float("nan")

    return pd.DataFrame(data)


@pytest.fixture(scope="function")
def sample_snapshot() -> MarketSnapshot:
    """MarketSnapshot with known values for scorer tests.

    All bullish conditions active:
      - close=50000 > ema21=49000
      - ema21_slope=0.01 > 0
      - macd=100 > macd_signal=90
      - rsi14=60 > 55
      - mfi14=55 > 50
      - last_bos_direction=1 (bullish BOS)
      - nearest_liquidity_above=51000
      - nearest_liquidity_below=None
    """
    from market_snapshot import MarketSnapshot
    return MarketSnapshot(
        symbol="BTC/USDT",
        timeframe="1d",
        timestamp=pd.Timestamp("2024-06-01"),
        close=50000.0,
        trend_direction="above",
        ema21=49000.0,
        ema21_slope=0.01,
        rsi14=60.0,
        mfi14=55.0,
        macd=100.0,
        macd_signal=90.0,
        macd_hist=10.0,
        atr14=500.0,
        bb_width=0.05,
        last_bos_direction=1,
        nearest_liquidity_above=51000.0,
    )


@pytest.fixture(scope="function")
def bearish_snapshot() -> MarketSnapshot:
    """MarketSnapshot with all bearish conditions.

      - close=48000 < ema21=49000
      - ema21_slope=-0.01 < 0
      - macd=50 < macd_signal=90
      - rsi14=40 <= 55
      - mfi14=35 <= 50
      - last_bos_direction=-1 (bearish BOS)
      - nearest_liquidity_below=47000
      - nearest_liquidity_above=None
    """
    from market_snapshot import MarketSnapshot
    return MarketSnapshot(
        symbol="BTC/USDT",
        timeframe="1d",
        timestamp=pd.Timestamp("2024-06-01"),
        close=48000.0,
        trend_direction="below",
        ema21=49000.0,
        ema21_slope=-0.01,
        rsi14=40.0,
        mfi14=35.0,
        macd=50.0,
        macd_signal=90.0,
        macd_hist=-5.0,
        atr14=500.0,
        bb_width=0.05,
        last_bos_direction=-1,
        nearest_liquidity_below=47000.0,
    )


@pytest.fixture(scope="function")
def neutral_snapshot() -> MarketSnapshot:
    """MarketSnapshot with neutral conditions (score in 4-6 range).

      - close=49500 > ema21=49000 (just above)
      - ema21_slope=-0.005 <= 0
      - macd=85 < macd_signal=90
      - rsi14=50 <= 55
      - mfi14=45 <= 50
      - last_bos_direction=None
      - nearest_liquidity_above=51000
      - nearest_liquidity_below=None
    """
    from market_snapshot import MarketSnapshot
    return MarketSnapshot(
        symbol="BTC/USDT",
        timeframe="1d",
        timestamp=pd.Timestamp("2024-06-01"),
        close=49500.0,
        trend_direction="above",
        ema21=49000.0,
        ema21_slope=-0.005,
        rsi14=50.0,
        mfi14=45.0,
        macd=85.0,
        macd_signal=90.0,
        macd_hist=-2.0,
        atr14=500.0,
        bb_width=0.05,
        nearest_liquidity_above=51000.0,
    )


# ---------------------------------------------------------------------------
# Multi-timeframe fixture helpers
# ---------------------------------------------------------------------------


def _make_daily_bullish() -> MarketSnapshot:
    """Bullish daily snapshot (score=10, like sample_snapshot but distinct timestamp)."""
    from market_snapshot import MarketSnapshot
    return MarketSnapshot(
        symbol="BTC/USDT",
        timeframe="1d",
        timestamp=pd.Timestamp("2024-06-01"),
        close=50000.0,
        trend_direction="above",
        ema21=49000.0,
        ema21_slope=0.01,
        rsi14=60.0,
        mfi14=55.0,
        macd=100.0,
        macd_signal=90.0,
        macd_hist=10.0,
        atr14=500.0,
        bb_width=0.05,
        last_bos_direction=1,
        nearest_liquidity_above=51000.0,
    )


def _make_h4_bearish() -> MarketSnapshot:
    """Bearish 4h snapshot (score=-4, like bearish_snapshot but timeframe='4h')."""
    from market_snapshot import MarketSnapshot
    return MarketSnapshot(
        symbol="BTC/USDT",
        timeframe="4h",
        timestamp=pd.Timestamp("2024-06-01 04:00:00"),
        close=48000.0,
        trend_direction="below",
        ema21=49000.0,
        ema21_slope=-0.01,
        rsi14=40.0,
        mfi14=35.0,
        macd=50.0,
        macd_signal=90.0,
        macd_hist=-5.0,
        atr14=400.0,
        bb_width=0.05,
        last_bos_direction=-1,
        nearest_liquidity_below=47000.0,
    )


def _make_h4_bullish() -> MarketSnapshot:
    """Bullish 4h snapshot."""
    from market_snapshot import MarketSnapshot
    return MarketSnapshot(
        symbol="BTC/USDT",
        timeframe="4h",
        timestamp=pd.Timestamp("2024-06-01 04:00:00"),
        close=50200.0,
        trend_direction="above",
        ema21=49500.0,
        ema21_slope=0.008,
        rsi14=62.0,
        mfi14=56.0,
        macd=120.0,
        macd_signal=100.0,
        macd_hist=15.0,
        atr14=400.0,
        bb_width=0.05,
        last_bos_direction=1,
        nearest_liquidity_above=51500.0,
    )


def _make_h1_neutral() -> MarketSnapshot:
    """Neutral 1h snapshot."""
    from market_snapshot import MarketSnapshot
    return MarketSnapshot(
        symbol="BTC/USDT",
        timeframe="1h",
        timestamp=pd.Timestamp("2024-06-01 05:00:00"),
        close=49800.0,
        trend_direction="above",
        ema21=49600.0,
        ema21_slope=0.002,
        rsi14=53.0,
        mfi14=48.0,
        macd=80.0,
        macd_signal=82.0,
        macd_hist=-1.0,
        atr14=300.0,
        bb_width=0.04,
        nearest_liquidity_above=50500.0,
    )


@pytest.fixture(scope="function")
def daily_bullish_snapshot() -> MarketSnapshot:
    """Bullish daily snapshot for MTF testing."""
    return _make_daily_bullish()


@pytest.fixture(scope="function")
def h4_bearish_snapshot() -> MarketSnapshot:
    """Bearish 4h snapshot for MTF testing."""
    return _make_h4_bearish()


@pytest.fixture(scope="function")
def h4_bullish_snapshot() -> MarketSnapshot:
    """Bullish 4h snapshot for MTF testing."""
    return _make_h4_bullish()


@pytest.fixture(scope="function")
def h1_neutral_snapshot() -> MarketSnapshot:
    """Neutral 1h snapshot for MTF testing."""
    return _make_h1_neutral()


@pytest.fixture(scope="function")
def mtx_bullish_daily() -> MarketContext:
    """All aligned: daily bullish + h4 bullish + h1 neutral."""
    from confluence import MarketContext
    return MarketContext(
        daily=_make_daily_bullish(),
        h4=_make_h4_bullish(),
        h1=_make_h1_neutral(),
    )


@pytest.fixture(scope="function")
def mtx_conflicting_h4() -> MarketContext:
    """H4 disagrees with daily: daily bullish + h4 bearish."""
    from confluence import MarketContext
    return MarketContext(
        daily=_make_daily_bullish(),
        h4=_make_h4_bearish(),
    )


@pytest.fixture(scope="function")
def mtx_conflicting_both() -> MarketContext:
    """Both LTFs disagree: daily bullish + h4 bearish + h1 neutral."""
    from confluence import MarketContext
    return MarketContext(
        daily=_make_daily_bullish(),
        h4=_make_h4_bearish(),
        h1=_make_h1_neutral(),
    )


@pytest.fixture(scope="function")
def mtx_no_daily() -> MarketContext:
    """Missing daily: only h4 bullish + h1 neutral."""
    from confluence import MarketContext
    return MarketContext(
        h4=_make_h4_bullish(),
        h1=_make_h1_neutral(),
    )


@pytest.fixture(scope="function")
def mtx_no_h1() -> MarketContext:
    """Missing h1: daily bullish + h4 bullish."""
    from confluence import MarketContext
    return MarketContext(
        daily=_make_daily_bullish(),
        h4=_make_h4_bullish(),
    )


@pytest.fixture(scope="function")
def mtx_all_neutral() -> MarketContext:
    """All three TFs neutral."""
    from confluence import MarketContext
    neutral_1d = MarketSnapshot(
        symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp("2024-06-01"),
        close=49500.0, trend_direction="above", ema21=49000.0, ema21_slope=-0.005,
        rsi14=50.0, mfi14=45.0, macd=85.0, macd_signal=90.0, macd_hist=-2.0,
        atr14=500.0, bb_width=0.05, nearest_liquidity_above=51000.0,
    )
    neutral_4h = MarketSnapshot(
        symbol="BTC/USDT", timeframe="4h", timestamp=pd.Timestamp("2024-06-01 04:00:00"),
        close=49500.0, trend_direction="at", ema21=49500.0, ema21_slope=0.0,
        rsi14=52.0, mfi14=48.0, macd=70.0, macd_signal=72.0, macd_hist=-0.5,
        atr14=400.0, bb_width=0.04,
    )
    neutral_1h = _make_h1_neutral()
    return MarketContext(daily=neutral_1d, h4=neutral_4h, h1=neutral_1h)
