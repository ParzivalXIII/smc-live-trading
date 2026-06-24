"""
Integration test for CCXT data layer — real Bybit API fetch.

This test fetches 100 candles of BTCUSDT 4H from Bybit's public API
(no authentication required). It validates the full pipeline:
    ExchangeFactory → resolve_symbol → fetch_ohlcv → process_candles → save_candles

Marked with @pytest.mark.integration to allow skipping in CI.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trade_scripts.exchange import ExchangeFactory
from trade_scripts.fetcher import fetch_ohlcv
from trade_scripts.processor import process_candles, validate_candles
from trade_scripts.storage import load_candles, save_candles

# Check if running in CI — skip if network might be unavailable
_SKIP_INTEGRATION = os.environ.get("CI", "").lower() in ("true", "1")


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_INTEGRATION, reason="Integration test skipped in CI")
class TestRealBybitFetch:
    """Integration tests hitting Bybit's public API."""

    def test_fetch_real_bybit_ohlcv(self, tmp_path: Path) -> None:
        """Fetch 100 BTCUSDT 4H candles from Bybit, process, save, and verify."""
        # 1. Create exchange (no auth required)
        exchange = ExchangeFactory.create("bybit")

        # 2. Resolve symbol
        symbol = ExchangeFactory.resolve_symbol(exchange, "BTCUSDT")
        assert symbol == "BTC/USDT", f"Expected BTC/USDT, got {symbol}"

        # 3. Fetch 100 candles of 4H
        raw = fetch_ohlcv(
            exchange,
            symbol,
            timeframe="4h",
            since=None,
            limit=100,
            max_pages=1,
        )
        assert len(raw) > 0, "No candles returned from Bybit"
        # Bybit may return fewer than 100 if it's a new market, but should have at least 10
        assert len(raw) >= 10, f"Expected at least 10 candles, got {len(raw)}"

        # 4. Process
        df = process_candles(raw)
        assert len(df) > 0
        assert df["timestamp"].is_monotonic_increasing, "Timestamps not sorted"

        # 5. Validate data quality
        warnings = validate_candles(df)
        # Log warnings but don't fail on them (data is real)
        for w in warnings:
            print(f"  [WARN] {w}")

        # 6. Verify OHLCV values
        for col in ["open", "high", "low", "close", "volume"]:
            assert df[col].dtype == np.float64, f"{col} is not float64"
            assert df[col].notna().all(), f"{col} contains NaN"
            assert (df[col] > 0).all(), f"{col} contains non-positive values"

        # 7. Verify financial invariants
        assert (df["high"] >= df["low"]).all(), "High < Low in some rows"
        assert (df["high"] >= df["close"]).all(), "High < Close in some rows"
        assert (df["low"] <= df["close"]).all(), "Low > Close in some rows"

        # 8. Save to temp CSV
        csv_path = tmp_path / "bybit_btcusdt_4h.csv"
        save_candles(df, str(csv_path))
        assert csv_path.exists(), "CSV was not saved"

        # 9. Verify CSV roundtrip with analyze_ta.py's load_csv pattern
        loaded = load_candles(str(csv_path))
        assert len(loaded) == len(df), f"Roundtrip length mismatch: {len(loaded)} vs {len(df)}"
        assert np.issubdtype(loaded["timestamp"].dtype, np.datetime64)

        # 10. Verify CSV can be loaded with pd.read_csv + parse_dates
        loaded_raw = pd.read_csv(csv_path, parse_dates=["timestamp"])
        assert np.issubdtype(loaded_raw["timestamp"].dtype, np.datetime64)
        assert loaded_raw.columns.tolist() == ["timestamp", "open", "high", "low", "close", "volume"]

        print(f"  Integration test passed: {len(df)} candles, saved to {csv_path}")
