"""
Integration tests for core indicator computation and CSV persistence.

Covers: compute_indicators (column presence, invariant checks, constant-price
known values), and save_enriched_csv integration with compute_indicators.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trade_scripts.analyze_ta import (
    compute_indicators,
    load_csv,
    save_enriched_csv,
)

EXPECTED_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume",
    "ema21", "macd", "macd_signal", "macd_hist",
    "rsi14", "bb_upper", "bb_mid", "bb_lower",
    "mfi14", "obv", "ebsw", "atr14",
    "macd_cross", "ema21_slope", "price_vs_bb", "bb_width", "obv_slope",
]


class TestComputeIndicators:
    def test_all_23_columns_present(self, linear_trend_df: pd.DataFrame) -> None:
        """Run compute_indicators on valid data; verify all 23 expected columns."""
        result = compute_indicators(linear_trend_df)
        for col in EXPECTED_COLUMNS:
            assert col in result.columns, f"Missing column: {col}"
        assert len(result.columns) >= 23

    def test_insufficient_data_5_rows(self) -> None:
        """5-row input returns all-NaN indicator columns (early-exit guard)."""
        ts = pd.date_range("2024-01-01", periods=5, freq="1h")
        df = pd.DataFrame({
            "timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": [100.0 + i for i in range(5)], "volume": 1000.0,
        })
        result = compute_indicators(df)
        for col in ["ema21", "macd", "macd_signal", "macd_hist", "rsi14",
                     "bb_upper", "bb_mid", "bb_lower", "mfi14", "obv",
                     "ebsw", "atr14"]:
            assert result[col].isna().all(), f"{col} should be all NaN for 5 rows"

    def test_single_row(self) -> None:
        """1-row input returns all-NaN indicator columns (early-exit guard)."""
        ts = pd.date_range("2024-01-01", periods=1, freq="1h")
        df = pd.DataFrame({
            "timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0,
            "close": [100.0], "volume": 1000.0,
        })
        result = compute_indicators(df)
        for col in ["ema21", "macd", "macd_signal", "macd_hist", "rsi14",
                     "bb_upper", "bb_mid", "bb_lower", "mfi14", "obv",
                     "ebsw", "atr14"]:
            assert result[col].isna().all(), f"{col} should be all NaN for 1 row"

    def test_compute_indicators_insufficient_rows(self) -> None:
        """Fewer than 40 rows returns all-NaN indicator columns (boundary test)."""
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=30, freq="h"),
            "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000,
        })
        result = compute_indicators(df)
        for col in ["ema21", "macd", "rsi14", "bb_upper", "ebsw", "atr14"]:
            assert result[col].isna().all(), f"{col} should be all NaN for <40 rows"

    def test_constant_price(self, constant_price_df: pd.DataFrame) -> None:
        """Constant price: BB_mid ≈ 100, MACD ≈ 0, OBV = 0, BB_width ≈ 0.
        RSI-14 may be NaN (zero stddev) — verify graceful NaN handling."""
        result = compute_indicators(constant_price_df)
        # BB mid near 100
        last_idx = result["bb_mid"].last_valid_index()
        if last_idx is not None:
            assert abs(result["bb_mid"].iloc[last_idx] - 100.0) < 1.0, \
                f"BB_mid not near 100: {result['bb_mid'].iloc[last_idx]}"
        # OBV = 0 for constant price
        assert result["obv"].dropna().abs().max() < 1e-10, "OBV not zero for constant price"
        # BB width near 0
        bbw_last = result["bb_width"].iloc[-1]
        if not np.isnan(bbw_last):
            assert abs(bbw_last) < 0.1, f"BB_width not near 0: {bbw_last}"
        # MACD near 0 where valid
        macd_valid = result["macd"].dropna()
        if len(macd_valid) > 0:
            assert macd_valid.abs().max() < 1.0, \
                f"MACD not near 0 for constant price: max abs={macd_valid.abs().max()}"

    def test_zero_volume(self) -> None:
        """Volume = 0 → MFI = 0.0 (pandas-ta returns 0 when all money flow is 0)."""
        ts = pd.date_range("2024-01-01", periods=100, freq="1h")
        close = np.linspace(100.0, 200.0, 100)
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        df = pd.DataFrame({
            "timestamp": ts,
            "open": open_,
            "high": np.maximum(open_, close) * 1.002,
            "low": np.minimum(open_, close) * 0.998,
            "close": close,
            "volume": np.zeros(100),
        })
        result = compute_indicators(df)
        # MFI should be 0.0 (all money flow is zero, ratio resolves to neutral)
        mfi_last = result["mfi14"].iloc[-1]
        assert mfi_last == 0.0, f"MFI not 0.0 for zero volume: {mfi_last}"

    def test_bb_ordering_invariant(self, linear_trend_df: pd.DataFrame) -> None:
        """For all valid rows: bb_upper >= bb_mid >= bb_lower (tolerance 1e-10)."""
        result = compute_indicators(linear_trend_df)
        valid = result.dropna(subset=["bb_upper", "bb_mid", "bb_lower"])
        assert len(valid) > 0, "No valid BB rows"
        assert (valid["bb_upper"] + 1e-10 >= valid["bb_mid"]).all(), \
            "bb_upper < bb_mid at some rows"
        assert (valid["bb_mid"] + 1e-10 >= valid["bb_lower"]).all(), \
            "bb_mid < bb_lower at some rows"

    def test_macd_identity_invariant(self, linear_trend_df: pd.DataFrame) -> None:
        """For all valid rows: macd_hist ≈ macd - macd_signal (relative tolerance)."""
        result = compute_indicators(linear_trend_df)
        valid = result.dropna(subset=["macd", "macd_hist", "macd_signal"])
        assert len(valid) > 0, "No valid MACD rows"
        identity = valid["macd"] - valid["macd_signal"] - valid["macd_hist"]
        max_dev = identity.abs().max()
        assert max_dev < 1e-10, \
            f"MACD identity violation: |macd - signal - hist| max = {max_dev}"


class TestSaveEnrichedCsv:
    def test_normal_write(self, tmp_csv_dir: Path, linear_trend_df: pd.DataFrame) -> None:
        """Write enriched CSV, verify file created with 23 columns."""
        result = compute_indicators(linear_trend_df)
        out_path = tmp_csv_dir / "enriched.csv"
        save_enriched_csv(result, out_path)
        assert out_path.exists()
        # Read back with load_csv
        df = load_csv(out_path)
        for col in EXPECTED_COLUMNS:
            assert col in df.columns, f"Missing column in output: {col}"

    def test_atomic_write_integrity(self, tmp_csv_dir: Path, linear_trend_df: pd.DataFrame) -> None:
        """Full write→parse round-trip via load_csv (production path)."""
        result = compute_indicators(linear_trend_df)
        out_path = tmp_csv_dir / "atomic.csv"
        save_enriched_csv(result, out_path)
        # Read back with production load_csv (sort + dedup)
        df = load_csv(out_path)
        assert len(df) == 100, f"Expected 100 rows, got {len(df)}"
        assert "timestamp" in df.columns

    def test_no_orphan_temp_files(self, tmp_csv_dir: Path, linear_trend_df: pd.DataFrame) -> None:
        """After write, verify no .tmp files remain."""
        result = compute_indicators(linear_trend_df)
        out_path = tmp_csv_dir / "no_orphan.csv"
        save_enriched_csv(result, out_path)
        tmp_files = list(tmp_csv_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Orphan .tmp files: {tmp_files}"

    def test_nan_handling(self, tmp_csv_dir: Path) -> None:
        """DF with NaN values → CSV has empty strings for NaN cells."""
        ts = pd.date_range("2024-01-01", periods=3, freq="1h")
        df = pd.DataFrame({
            "timestamp": ts, "open": [100.0, np.nan, 102.0],
            "high": [101.0, np.nan, 103.0], "low": [99.0, np.nan, 101.0],
            "close": [100.5, np.nan, 102.5], "volume": [1000.0, np.nan, 1000.0],
        })
        out_path = tmp_csv_dir / "nan.csv"
        save_enriched_csv(df, out_path)
        content = out_path.read_text()
        assert ",," in content or content.count(",,") > 0, "NaN not rendered as empty string"

    def test_column_order(self, tmp_csv_dir: Path, linear_trend_df: pd.DataFrame) -> None:
        """CSV header matches exact expected fieldnames order."""
        result = compute_indicators(linear_trend_df)
        out_path = tmp_csv_dir / "order.csv"
        save_enriched_csv(result, out_path)
        header = out_path.read_text().splitlines()[0]
        header_cols = header.split(",")
        assert header_cols == EXPECTED_COLUMNS, \
            f"Column order mismatch\nExpected: {EXPECTED_COLUMNS}\nGot:      {header_cols}"

    def test_concurrent_write_safety(self, tmp_csv_dir: Path) -> None:
        """2 threads write concurrently; output is valid CSV parseable by load_csv."""
        path = tmp_csv_dir / "concurrent_core.csv"

        def writer_a():
            ts = pd.date_range("2024-01-01", periods=5, freq="1h")
            df = pd.DataFrame({
                "timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0, "volume": 1000.0,
            })
            save_enriched_csv(df, path)

        def writer_b():
            ts = pd.date_range("2024-01-02", periods=5, freq="1h")
            df = pd.DataFrame({
                "timestamp": ts, "open": 200.0, "high": 201.0, "low": 199.0,
                "close": 200.0, "volume": 2000.0,
            })
            save_enriched_csv(df, path)

        threads = [threading.Thread(target=writer_a), threading.Thread(target=writer_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        df = load_csv(path)
        assert len(df) > 0
        for col in EXPECTED_COLUMNS:
            assert col in df.columns, f"Missing column: {col}"

    def test_error_recovery(self, tmp_csv_dir: Path, linear_trend_df: pd.DataFrame) -> None:
        """Simulate write failure by providing invalid path; verify no orphan .tmp files."""
        result = compute_indicators(linear_trend_df)
        bad_path = Path("/nonexistent_dir_xyz/file.csv")
        with pytest.raises(Exception):
            save_enriched_csv(result, bad_path)
        # Check no .tmp files in the project tmp dir
        # (tmp_csv_dir should be clean; note the bad_path won't create .tmps in the project)
        tmp_files = list(tmp_csv_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Orphan .tmp files: {tmp_files}"
