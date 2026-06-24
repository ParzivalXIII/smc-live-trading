"""
Integration tests for I/O functions in analyze_ta.py.

Covers: load_csv, load_ta_latest, load_ta_series, save_enriched_csv,
round-trip integrity, concurrent write safety.
"""

from __future__ import annotations

import csv
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trade_scripts.analyze_ta import (
    load_csv,
    load_ta_latest,
    load_ta_series,
    save_enriched_csv,
)


# =============================================================================
# T6: load_csv tests
# =============================================================================

def _write_csv(path: Path, rows: list[dict]) -> None:
    """Helper: write a list of dict rows as CSV."""
    if not rows:
        path.write_text("")
        return
    with open(str(path), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


class TestLoadCsv:
    def test_normal(self, tmp_csv_dir: Path) -> None:
        """Normal sorted CSV loads correctly."""
        path = tmp_csv_dir / "test.csv"
        rows = [
            {"timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
             "low": "99", "close": "100.5", "volume": "1000"},
            {"timestamp": "2024-01-01 02:00:00", "open": "100.5", "high": "102",
             "low": "99.5", "close": "101", "volume": "2000"},
        ]
        _write_csv(path, rows)
        df = load_csv(path)
        assert df.shape == (2, 6)
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
        assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])

    def test_unsorted(self, tmp_csv_dir: Path) -> None:
        """Unsorted timestamps come back sorted ascending."""
        path = tmp_csv_dir / "unsorted.csv"
        rows = [
            {"timestamp": "2024-01-01 03:00:00", "open": "102", "high": "103",
             "low": "101", "close": "102.5", "volume": "1500"},
            {"timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
             "low": "99", "close": "100.5", "volume": "1000"},
            {"timestamp": "2024-01-01 02:00:00", "open": "101", "high": "102",
             "low": "100", "close": "101.5", "volume": "1200"},
        ]
        _write_csv(path, rows)
        df = load_csv(path)
        timestamps = df["timestamp"].values
        assert all(timestamps[i] <= timestamps[i + 1] for i in range(len(timestamps) - 1))

    def test_duplicate_timestamps(self, tmp_csv_dir: Path) -> None:
        """Duplicate timestamps: keep last wins."""
        path = tmp_csv_dir / "dupes.csv"
        rows = [
            {"timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
             "low": "99", "close": "100.5", "volume": "1000"},
            {"timestamp": "2024-01-01 01:00:00", "open": "200", "high": "201",
             "low": "199", "close": "200.5", "volume": "2000"},
        ]
        _write_csv(path, rows)
        df = load_csv(path)
        assert df.shape == (1, 6)
        assert df["close"].iloc[0] == 200.5

    def test_empty_file(self, tmp_csv_dir: Path) -> None:
        """Empty file (header only) → empty DataFrame."""
        path = tmp_csv_dir / "empty.csv"
        path.write_text("timestamp,open,high,low,close,volume\n")
        df = load_csv(path)
        assert len(df) == 0
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

    def test_missing_timestamp_column(self, tmp_csv_dir: Path) -> None:
        """CSV without timestamp column → ValueError (parse_dates can't find 'timestamp')."""
        path = tmp_csv_dir / "no_ts.csv"
        path.write_text("open,high,low,close,volume\n100,101,99,100.5,1000\n")
        with pytest.raises(ValueError, match="Missing column.*'timestamp'"):
            load_csv(path)

    def test_extra_columns(self, tmp_csv_dir: Path) -> None:
        """Extra columns beyond OHLCV load without error."""
        path = tmp_csv_dir / "extra.csv"
        rows = [
            {"timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
             "low": "99", "close": "100.5", "volume": "1000", "extra": "x"},
        ]
        _write_csv(path, rows)
        df = load_csv(path)
        # load_csv does not filter columns — extra columns are preserved
        assert "timestamp" in df.columns
        assert "extra" in df.columns
        assert df.shape == (1, 7)


# =============================================================================
# T6: load_ta_latest tests
# =============================================================================

def _write_ta_csv(path: Path, rows: list[dict]) -> None:
    """Helper: write a TA-enriched CSV with all columns."""
    fieldnames = [
        "timestamp", "open", "high", "low", "close", "volume",
        "ema21", "macd", "macd_signal", "macd_hist",
        "rsi14", "bb_upper", "bb_mid", "bb_lower",
        "mfi14", "obv", "ebsw", "atr14",
        "macd_cross", "ema21_slope", "price_vs_bb", "bb_width", "obv_slope",
    ]
    with open(str(path), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


class TestLoadTaLatest:
    def test_normal(self, tmp_csv_dir: Path) -> None:
        """Normal enriched CSV returns dict with all 5 keys."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        rows = [{
            "timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
            "low": "99", "close": "100.5", "volume": "1000",
            "ema21": "100.2", "macd": "0.5", "macd_signal": "0.3", "macd_hist": "0.2",
            "rsi14": "55.0", "bb_upper": "102", "bb_mid": "100", "bb_lower": "98",
            "mfi14": "50.0", "obv": "1000", "ebsw": "0.1", "atr14": "1.5",
            "macd_cross": "none", "ema21_slope": "0.001", "price_vs_bb": "inside",
            "bb_width": "0.04", "obv_slope": "0.0",
        }]
        _write_ta_csv(path, rows)
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is not None
        assert isinstance(result, dict)
        assert set(result.keys()) == {"mfi14", "obv", "obv_slope", "close", "timestamp"}
        assert result["mfi14"] == 50.0
        assert result["obv"] == 1000.0
        assert result["obv_slope"] == 0.0

    def test_missing_file(self, tmp_csv_dir: Path) -> None:
        """Missing file returns None."""
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is None

    def test_empty_file(self, tmp_csv_dir: Path) -> None:
        """Empty CSV (header only) returns None (not IndexError)."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        path.write_text("timestamp,open,high,low,close,volume\n")
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is None

    def test_header_only_csv(self, tmp_csv_dir: Path) -> None:
        """Header-only CSV with all 23 columns returns None."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        fieldnames = [
            "timestamp", "open", "high", "low", "close", "volume",
            "ema21", "macd", "macd_signal", "macd_hist",
            "rsi14", "bb_upper", "bb_mid", "bb_lower",
            "mfi14", "obv", "ebsw", "atr14",
            "macd_cross", "ema21_slope", "price_vs_bb", "bb_width", "obv_slope",
        ]
        with open(str(path), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is None

    def test_mfi14_nan(self, tmp_csv_dir: Path) -> None:
        """mfi14=NaN → returns None."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        _write_ta_csv(path, [{
            "timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
            "low": "99", "close": "100.5", "volume": "1000",
            "ema21": "100", "macd": "0", "macd_signal": "0", "macd_hist": "0",
            "rsi14": "50", "bb_upper": "101", "bb_mid": "100", "bb_lower": "99",
            "mfi14": "", "obv": "1000", "ebsw": "0", "atr14": "1",
            "macd_cross": "none", "ema21_slope": "0", "price_vs_bb": "inside",
            "bb_width": "0.02", "obv_slope": "0",
        }])
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is None

    def test_obv_nan(self, tmp_csv_dir: Path) -> None:
        """obv=NaN → returns None."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        _write_ta_csv(path, [{
            "timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
            "low": "99", "close": "100.5", "volume": "1000",
            "ema21": "100", "macd": "0", "macd_signal": "0", "macd_hist": "0",
            "rsi14": "50", "bb_upper": "101", "bb_mid": "100", "bb_lower": "99",
            "mfi14": "50", "obv": "", "ebsw": "0", "atr14": "1",
            "macd_cross": "none", "ema21_slope": "0", "price_vs_bb": "inside",
            "bb_width": "0.02", "obv_slope": "0",
        }])
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is None

    def test_obv_slope_empty_string(self, tmp_csv_dir: Path) -> None:
        """obv_slope='' → returns 0.0."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        _write_ta_csv(path, [{
            "timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
            "low": "99", "close": "100.5", "volume": "1000",
            "ema21": "100", "macd": "0", "macd_signal": "0", "macd_hist": "0",
            "rsi14": "50", "bb_upper": "101", "bb_mid": "100", "bb_lower": "99",
            "mfi14": "50", "obv": "1000", "ebsw": "0", "atr14": "1",
            "macd_cross": "none", "ema21_slope": "0", "price_vs_bb": "inside",
            "bb_width": "0.02", "obv_slope": "",
        }])
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is not None
        assert result["obv_slope"] == 0.0

    def test_obv_slope_none_string(self, tmp_csv_dir: Path) -> None:
        """obv_slope='none' → returns 0.0."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        _write_ta_csv(path, [{
            "timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
            "low": "99", "close": "100.5", "volume": "1000",
            "ema21": "100", "macd": "0", "macd_signal": "0", "macd_hist": "0",
            "rsi14": "50", "bb_upper": "101", "bb_mid": "100", "bb_lower": "99",
            "mfi14": "50", "obv": "1000", "ebsw": "0", "atr14": "1",
            "macd_cross": "none", "ema21_slope": "0", "price_vs_bb": "inside",
            "bb_width": "0.02", "obv_slope": "none",
        }])
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is not None
        assert result["obv_slope"] == 0.0

    def test_obv_slope_valid_float(self, tmp_csv_dir: Path) -> None:
        """obv_slope=1.0 → returns 1.0."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        _write_ta_csv(path, [{
            "timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
            "low": "99", "close": "100.5", "volume": "1000",
            "ema21": "100", "macd": "0", "macd_signal": "0", "macd_hist": "0",
            "rsi14": "50", "bb_upper": "101", "bb_mid": "100", "bb_lower": "99",
            "mfi14": "50", "obv": "1000", "ebsw": "0", "atr14": "1",
            "macd_cross": "none", "ema21_slope": "0", "price_vs_bb": "inside",
            "bb_width": "0.02", "obv_slope": "1.0",
        }])
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is not None
        assert result["obv_slope"] == 1.0

    def test_missing_mfi14_obv_columns(self, tmp_csv_dir: Path) -> None:
        """CSV without mfi14/obv columns → KeyError caught → returns None."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        path.write_text("timestamp,close\n2024-01-01,100.5\n")
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is None

    def test_close_nan(self, tmp_csv_dir: Path) -> None:
        """close=NaN → returns None in dict."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        _write_ta_csv(path, [{
            "timestamp": "2024-01-01 01:00:00", "open": "", "high": "",
            "low": "", "close": "", "volume": "",
            "ema21": "", "macd": "", "macd_signal": "", "macd_hist": "",
            "rsi14": "", "bb_upper": "", "bb_mid": "", "bb_lower": "",
            "mfi14": "50", "obv": "1000", "ebsw": "", "atr14": "",
            "macd_cross": "none", "ema21_slope": "", "price_vs_bb": "none",
            "bb_width": "", "obv_slope": "0",
        }])
        result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is not None
        assert result["close"] is None


# =============================================================================
# T6: load_ta_series tests
# =============================================================================

class TestLoadTaSeries:
    def test_normal(self, tmp_csv_dir: Path) -> None:
        """tail=3 on 10-row CSV returns 3 rows."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        rows = []
        for i in range(10):
            rows.append({
                "timestamp": f"2024-01-01 {i:02d}:00:00",
                "open": "100", "high": "101", "low": "99", "close": "100", "volume": "1000",
                "ema21": "", "macd": "", "macd_signal": "", "macd_hist": "",
                "rsi14": "", "bb_upper": "", "bb_mid": "", "bb_lower": "",
                "mfi14": "50", "obv": "1000", "ebsw": "", "atr14": "",
                "macd_cross": "none", "ema21_slope": "", "price_vs_bb": "none",
                "bb_width": "", "obv_slope": "0",
            })
        _write_ta_csv(path, rows)
        result = load_ta_series("BTC/USDT", "1h", str(tmp_csv_dir), tail=3)
        assert result is not None
        assert len(result) == 3

    def test_tail_greater_than_length(self, tmp_csv_dir: Path) -> None:
        """tail=100 on 5-row CSV returns 5 rows."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        rows = []
        for i in range(5):
            rows.append({
                "timestamp": f"2024-01-01 {i:02d}:00:00",
                "open": "100", "high": "101", "low": "99", "close": "100", "volume": "1000",
                "ema21": "", "macd": "", "macd_signal": "", "macd_hist": "",
                "rsi14": "", "bb_upper": "", "bb_mid": "", "bb_lower": "",
                "mfi14": "50", "obv": "1000", "ebsw": "", "atr14": "",
                "macd_cross": "none", "ema21_slope": "", "price_vs_bb": "none",
                "bb_width": "", "obv_slope": "0",
            })
        _write_ta_csv(path, rows)
        result = load_ta_series("BTC/USDT", "1h", str(tmp_csv_dir), tail=100)
        assert result is not None
        assert len(result) == 5

    def test_missing_file(self, tmp_csv_dir: Path) -> None:
        """Missing file returns None."""
        result = load_ta_series("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is None

    def test_empty_file(self, tmp_csv_dir: Path) -> None:
        """Empty CSV (header only) returns empty DataFrame (known limitation)."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        path.write_text("timestamp,open,high,low,close,volume\n")
        result = load_ta_series("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is not None
        assert len(result) == 0

    def test_timestamp_parse(self, tmp_csv_dir: Path) -> None:
        """Timestamps are parsed as datetime."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        _write_ta_csv(path, [{
            "timestamp": "2024-01-01 01:00:00", "open": "100", "high": "101",
            "low": "99", "close": "100.5", "volume": "1000",
            "ema21": "", "macd": "", "macd_signal": "", "macd_hist": "",
            "rsi14": "", "bb_upper": "", "bb_mid": "", "bb_lower": "",
            "mfi14": "50", "obv": "1000", "ebsw": "", "atr14": "",
            "macd_cross": "none", "ema21_slope": "", "price_vs_bb": "none",
            "bb_width": "", "obv_slope": "0",
        }])
        result = load_ta_series("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is not None
        assert pd.api.types.is_datetime64_any_dtype(result["timestamp"])

    def test_malformed_csv(self, tmp_csv_dir: Path) -> None:
        """Malformed CSV returns None."""
        path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
        path.write_text("not,csv,content\nno,proper,columns\n")
        result = load_ta_series("BTC/USDT", "1h", str(tmp_csv_dir))
        assert result is None


# =============================================================================
# T6: save_enriched_csv round-trip and concurrent write
# =============================================================================

class TestSaveEnrichedCsv:
    def test_save_load_roundtrip(self, tmp_csv_dir: Path, constant_price_df: pd.DataFrame) -> None:
        """Write with save_enriched_csv, read back with load_csv, verify columns match."""
        out_path = tmp_csv_dir / "roundtrip.csv"
        save_enriched_csv(constant_price_df, out_path)
        assert out_path.exists()
        df = load_csv(out_path)
        # load_csv returns 6 base columns
        assert "timestamp" in df.columns
        assert len(df) == 100

    def test_concurrent_write_safety(self, tmp_csv_dir: Path) -> None:
        """2 threads write different data concurrently, output is valid CSV parseable by load_csv."""
        path = tmp_csv_dir / "concurrent.csv"

        def writer_a():
            df = pd.DataFrame({
                "timestamp": pd.date_range("2024-01-01", periods=5, freq="1h"),
                "open": [100.0] * 5, "high": [101.0] * 5, "low": [99.0] * 5,
                "close": [100.0] * 5, "volume": [1000.0] * 5,
                "ema21": [101.0] * 5, "macd": [0.1] * 5, "macd_signal": [0.05] * 5,
                "macd_hist": [0.05] * 5, "rsi14": [50.0] * 5,
                "bb_upper": [102.0] * 5, "bb_mid": [100.0] * 5, "bb_lower": [98.0] * 5,
                "mfi14": [50.0] * 5, "obv": [1000.0] * 5, "ebsw": [0.0] * 5,
                "atr14": [1.0] * 5, "macd_cross": ["none"] * 5,
                "ema21_slope": [0.001] * 5, "price_vs_bb": ["inside"] * 5,
                "bb_width": [0.04] * 5, "obv_slope": [0.0] * 5,
            })
            save_enriched_csv(df, path)

        def writer_b():
            df = pd.DataFrame({
                "timestamp": pd.date_range("2024-01-02", periods=5, freq="1h"),
                "open": [200.0] * 5, "high": [201.0] * 5, "low": [199.0] * 5,
                "close": [200.0] * 5, "volume": [2000.0] * 5,
                "ema21": [201.0] * 5, "macd": [0.2] * 5, "macd_signal": [0.1] * 5,
                "macd_hist": [0.1] * 5, "rsi14": [60.0] * 5,
                "bb_upper": [202.0] * 5, "bb_mid": [200.0] * 5, "bb_lower": [198.0] * 5,
                "mfi14": [60.0] * 5, "obv": [2000.0] * 5, "ebsw": [0.0] * 5,
                "atr14": [2.0] * 5, "macd_cross": ["none"] * 5,
                "ema21_slope": [0.001] * 5, "price_vs_bb": ["inside"] * 5,
                "bb_width": [0.02] * 5, "obv_slope": [0.0] * 5,
            })
            save_enriched_csv(df, path)

        threads = [threading.Thread(target=writer_a), threading.Thread(target=writer_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify output is parseable by load_csv with all 23 columns
        df = load_csv(path)
        assert len(df) > 0
        # Check 23 expected columns
        expected_cols = [
            "timestamp", "open", "high", "low", "close", "volume",
            "ema21", "macd", "macd_signal", "macd_hist",
            "rsi14", "bb_upper", "bb_mid", "bb_lower",
            "mfi14", "obv", "ebsw", "atr14",
            "macd_cross", "ema21_slope", "price_vs_bb", "bb_width", "obv_slope",
        ]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"

    def test_no_orphan_temp_files(self, tmp_csv_dir: Path, constant_price_df: pd.DataFrame) -> None:
        """Normal write leaves no .tmp files."""
        out_path = tmp_csv_dir / "clean.csv"
        save_enriched_csv(constant_price_df, out_path)
        tmp_files = list(tmp_csv_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Orphan .tmp files: {tmp_files}"
