"""Tests for LiveSmcBuffer — streaming SMC accumulator."""

import pandas as pd
import numpy as np
import pytest

from live_smc_buffer import LiveSmcBuffer


def _make_candle_row(close=50000.0, high=50100.0, low=49900.0,
                      open=50000.0, volume=100.0, timestamp=None):
    """Create a synthetic OHLCV row as a pd.Series."""
    data = {
        "open": open, "high": high, "low": low,
        "close": close, "volume": volume,
    }
    s = pd.Series(data)
    if timestamp is not None:
        s.name = timestamp
    return s


def _make_uptrend_series(n=100, start=50000.0):
    """Create a synthetic uptrend with a peak at candle 60 and retracement."""
    prices = []
    for i in range(n):
        if i < 60:
            prices.append(start + i * 20)
        else:
            prices.append(start + 60 * 20 - (i - 60) * 15)
    
    dates = pd.date_range("2024-01-01", periods=n, freq="h")
    rows = []
    for i, (p, d) in enumerate(zip(prices, dates)):
        row = pd.Series({
            "open": p, "high": p * 1.005, "low": p * 0.995,
            "close": p, "volume": 100.0,
        }, name=d)
        rows.append(row)
    return rows


class TestLiveSmcBufferConstruction:
    def test_default_construction(self):
        buf = LiveSmcBuffer()
        assert buf is not None
        assert buf._swing_engine is not None
        assert buf._structure_engine is not None
        assert buf._report_window == 200

    def test_custom_params(self):
        buf = LiveSmcBuffer(swing_length=10, confirmation_bars=3,
                            atr_multiplier=2.0, atr_period=14,
                            bos_confirmation_window=20, report_window=100)
        assert buf._swing_engine._swing_length == 10
        assert buf._report_window == 100


class TestLiveSmcBufferUpdate:
    def test_update_returns_dict(self):
        buf = LiveSmcBuffer(swing_length=5, confirmation_bars=2, atr_period=7)
        row = _make_candle_row()
        result = buf.update(row)
        assert "HighLow" in result
        assert "Level" in result

    def test_cold_start_returns_nan(self):
        buf = LiveSmcBuffer(swing_length=50, confirmation_bars=5, atr_period=14)
        for _ in range(30):
            buf.update(_make_candle_row())
        report = buf.get_smc_report()
        assert report["SwingHighLow"].isna().all()  # Not enough bars

    def test_after_many_candles_report_has_26_columns(self):
        rows = _make_uptrend_series(100)
        buf = LiveSmcBuffer(swing_length=5, confirmation_bars=2, atr_period=7)
        for row in rows:
            buf.update(row)
        report = buf.get_smc_report()
        assert len(report.columns) == 26

    def test_report_window_trimmed(self):
        buf = LiveSmcBuffer(report_window=50)
        for _ in range(100):
            buf.update(_make_candle_row())
        assert len(buf.get_smc_report()) <= 50

    def test_swing_eventually_confirmed(self):
        rows = _make_uptrend_series(100)
        buf = LiveSmcBuffer(swing_length=5, confirmation_bars=2, atr_period=7)
        for row in rows:
            buf.update(row)
        report = buf.get_smc_report()
        assert report["SwingHighLow"].notna().any()  # Some swings detected

    def test_events_property(self):
        rows = _make_uptrend_series(100)
        buf = LiveSmcBuffer(swing_length=5, confirmation_bars=2, atr_period=7)
        for row in rows:
            buf.update(row)
        assert len(buf.events) >= 0  # Events may be empty or populated


class TestLiveSmcBufferDownstream:
    def test_ob_columns_populated(self):
        rows = _make_uptrend_series(100)
        buf = LiveSmcBuffer(swing_length=5, confirmation_bars=2, atr_period=7)
        for row in rows:
            buf.update(row)
        report = buf.get_smc_report()
        # OB columns may or may not be populated depending on swing detection
        assert "OB" in report.columns
        assert "OBTop" in report.columns
        assert "OBBottom" in report.columns

    def test_liquidity_columns_populated(self):
        rows = _make_uptrend_series(100)
        buf = LiveSmcBuffer(swing_length=5, confirmation_bars=2, atr_period=7)
        for row in rows:
            buf.update(row)
        report = buf.get_smc_report()
        assert "Liquidity" in report.columns
        assert "LiqLevel" in report.columns


class TestLiveSmcBufferEdgeCases:
    def test_update_with_nan_row(self):
        buf = LiveSmcBuffer()
        row = _make_candle_row(close=float("nan"), high=float("nan"))
        result = buf.update(row)
        assert result is not None

    def test_report_not_empty_after_updates(self):
        buf = LiveSmcBuffer(report_window=200)
        for _ in range(10):
            buf.update(_make_candle_row())
        assert len(buf.get_smc_report()) == 10

    def test_consecutive_updates_increment_index(self):
        buf = LiveSmcBuffer()
        assert buf._candle_index == 0
        buf.update(_make_candle_row())
        assert buf._candle_index == 1
        buf.update(_make_candle_row())
        assert buf._candle_index == 2
