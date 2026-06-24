"""
System tests for analyze_ta.py.

Covers: print_timeframe_block, analyze_timeframe, parse_args.
"""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trade_scripts.analyze_ta import (
    analyze_timeframe,
    parse_args,
    print_timeframe_block,
)


class TestPrintTimeframeBlock:
    def test_normal_dataframe(self, capsys: pytest.CaptureFixture) -> None:
        """Build a DataFrame with valid indicators; verify stdout labels."""
        # Create a minimal DataFrame with all expected columns
        n = 60
        ts = pd.date_range("2024-01-01", periods=n, freq="1h")
        close = np.linspace(100.0, 200.0, n)
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        np.random.seed(42)
        df = pd.DataFrame({
            "timestamp": ts,
            "open": open_,
            "high": np.maximum(open_, close) * 1.002,
            "low": np.minimum(open_, close) * 0.998,
            "close": close,
            "volume": np.full(n, 1000.0),
            "ema21": pd.Series(close).ewm(span=21).mean(),
            "macd": np.linspace(-1, 2, n),
            "macd_signal": np.linspace(-0.5, 1.5, n),
            "macd_hist": np.linspace(-0.5, 0.5, n),
            "rsi14": np.linspace(40, 70, n),
            "bb_upper": close * 1.05,
            "bb_mid": close,
            "bb_lower": close * 0.95,
            "mfi14": np.linspace(30, 70, n),
            "obv": np.cumsum(np.where(np.diff(close, prepend=close[0]) > 0, 1000, -1000)),
            "ebsw": np.linspace(-1, 1, n),
            "atr14": np.full(n, 2.0),
            "macd_cross": ["none"] * n,
            "ema21_slope": np.full(n, 0.001),
            "price_vs_bb": ["inside"] * n,
            "bb_width": np.full(n, 0.05),
            "obv_slope": [0.0] * n,
        })
        print_timeframe_block("1h", df)
        captured = capsys.readouterr()
        assert "H1" in captured.out
        assert "EMA21" in captured.out
        assert "MACD" in captured.out
        assert "RSI14" in captured.out
        assert "BB" in captured.out
        assert "MFI14" in captured.out
        assert "OBV" in captured.out
        assert "EBSW" in captured.out
        assert "ATR14" in captured.out
        assert "OBV Slope" in captured.out
        assert "BB Width" in captured.out
        assert "Price vs BB" in captured.out
        assert "EMA21 Slope" in captured.out
        assert "MACD Cross" in captured.out

    def test_all_nan_dataframe(self, capsys: pytest.CaptureFixture) -> None:
        """All-NaN indicators → stdout contains 'insufficient data'."""
        n = 60
        ts = pd.date_range("2024-01-01", periods=n, freq="1h")
        df = pd.DataFrame({
            "timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0, "volume": 1000.0,
            "ema21": np.full(n, np.nan),
            "macd": np.full(n, np.nan), "macd_signal": np.full(n, np.nan),
            "macd_hist": np.full(n, np.nan),
            "rsi14": np.full(n, np.nan),
            "bb_upper": np.full(n, np.nan), "bb_mid": np.full(n, np.nan),
            "bb_lower": np.full(n, np.nan),
            "mfi14": np.full(n, np.nan),
            "obv": np.full(n, np.nan),
            "ebsw": np.full(n, np.nan), "atr14": np.full(n, np.nan),
            "macd_cross": ["none"] * n,
            "ema21_slope": np.full(n, np.nan),
            "price_vs_bb": ["none"] * n,
            "bb_width": np.full(n, np.nan), "obv_slope": np.full(n, np.nan),
        })
        print_timeframe_block("1h", df)
        captured = capsys.readouterr()
        assert "insufficient data" in captured.out


class TestAnalyzeTimeframe:
    def test_normal(self, tmp_csv_dir: Path) -> None:
        """Write OHLCV CSV, call analyze_timeframe, verify output CSV exists."""
        # Create raw OHLCV CSV
        n = 100
        ts = pd.date_range("2024-01-01", periods=n, freq="1h")
        close = np.linspace(100.0, 200.0, n)
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        csv_path = tmp_csv_dir / "ohlcv_BTCUSDT_1h.csv"
        df = pd.DataFrame({
            "timestamp": ts,
            "open": open_,
            "high": np.maximum(open_, close) * 1.002,
            "low": np.minimum(open_, close) * 0.998,
            "close": close,
            "volume": np.full(n, 1000.0),
        })
        df.to_csv(str(csv_path), index=False)
        result = analyze_timeframe("BTC/USDT", "1h", tmp_csv_dir)
        assert result is True
        out_path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        assert out_path.exists()

    def test_missing_input_csv(self, tmp_csv_dir: Path) -> None:
        """Non-existent CSV path → returns False."""
        result = analyze_timeframe("BTC/USDT", "1h", tmp_csv_dir)
        assert result is False

    def test_output_csv_has_correct_columns(self, tmp_csv_dir: Path) -> None:
        """Verify enriched CSV has all 23 expected columns."""
        n = 100
        ts = pd.date_range("2024-01-01", periods=n, freq="1h")
        close = np.linspace(100.0, 200.0, n)
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        csv_path = tmp_csv_dir / "ohlcv_BTCUSDT_1h.csv"
        df = pd.DataFrame({
            "timestamp": ts,
            "open": open_,
            "high": np.maximum(open_, close) * 1.002,
            "low": np.minimum(open_, close) * 0.998,
            "close": close,
            "volume": np.full(n, 1000.0),
        })
        df.to_csv(str(csv_path), index=False)
        analyze_timeframe("BTC/USDT", "1h", tmp_csv_dir)
        out_path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        import csv
        with open(str(out_path), newline="") as f:
            reader = csv.reader(f)
            header = next(reader)
        expected = [
            "timestamp", "open", "high", "low", "close", "volume",
            "ema21", "macd", "macd_signal", "macd_hist",
            "rsi14", "bb_upper", "bb_mid", "bb_lower",
            "mfi14", "obv", "ebsw", "atr14",
            "macd_cross", "ema21_slope", "price_vs_bb", "bb_width", "obv_slope",
        ]
        assert header == expected, f"Header mismatch:\n{header}"


class TestParseArgs:
    def test_default_timeframes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: symbol required, --timeframe and --data-dir optional."""
        monkeypatch.setattr(sys, "argv", ["prog", "BTC/USDT"])
        args = parse_args()
        assert args.symbol == "BTC/USDT"
        assert args.timeframe is None
        assert args.data_dir == "data"

    def test_single_timeframe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--timeframe 4h overrides default."""
        monkeypatch.setattr(sys, "argv", ["prog", "BTC/USDT", "--timeframe", "4h"])
        args = parse_args()
        assert args.timeframe == "4h"

    def test_custom_data_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--data-dir overrides default."""
        monkeypatch.setattr(sys, "argv", ["prog", "BTC/USDT", "--data-dir", "/custom/path"])
        args = parse_args()
        assert args.data_dir == "/custom/path"

    def test_no_symbol(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No symbol → SystemExit (argparse error)."""
        monkeypatch.setattr(sys, "argv", ["prog"])
        with pytest.raises(SystemExit):
            parse_args()
