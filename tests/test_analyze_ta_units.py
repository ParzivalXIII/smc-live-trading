"""
Unit tests for pure functions in analyze_ta.py.

Covers: normalize_symbol, last_valid, ema_signal, macd_signal_label,
rsi_label, mfi_signal, obv_signal, bb_label, _macd_cross, _obv_slope, _price_vs_bb.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from trade_scripts.analyze_ta import (
    _macd_cross,
    _obv_slope,
    _price_vs_bb,
    bb_label,
    ema_signal,
    last_valid,
    macd_signal_label,
    mfi_signal,
    normalize_symbol,
    obv_signal,
    rsi_label,
)


# =============================================================================
# T3: normalize_symbol, last_valid, ema_signal, macd_signal_label
# =============================================================================

class TestNormalizeSymbol:
    @pytest.mark.parametrize("symbol,expected", [
        ("BTC/USDT", "BTCUSDT"),
        ("BTCUSDT",   "BTCUSDT"),
        ("",          ""),
        ("BTC/USDT/ABC", "BTCUSDTABC"),
    ])
    def test_normalize(self, symbol: str, expected: str) -> None:
        assert normalize_symbol(symbol) == expected


class TestLastValid:
    @pytest.mark.parametrize("values,expected", [
        ([1.0, 2.0, 3.0], 3.0),
        ([1.0, 2.0, math.nan], 2.0),
        ([math.nan, math.nan], math.nan),
        ([42.0], 42.0),
        ([math.nan], math.nan),
        ([math.nan, 5.0], 5.0),
    ])
    def test_last_valid(self, values: list[float], expected: float) -> None:
        result = last_valid(pd.Series(values))
        if math.isnan(expected):
            assert math.isnan(result), f"Expected NaN, got {result}"
        else:
            assert result == expected


class TestEmaSignal:
    @pytest.mark.parametrize("close_price,ema,expected_substrings", [
        (110, 100, ["above", "+10.0%"]),
        (90, 100, ["below", "-10.0%"]),
        (100.05, 100, ["at"]),
        (100, 100, ["at", "+0.0%"]),
    ])
    def test_ema_signal(
        self, close_price: float, ema: float, expected_substrings: list[str]
    ) -> None:
        result = ema_signal(close_price, ema)
        for sub in expected_substrings:
            assert sub in result, f"'{sub}' not in '{result}'"

    def test_ema_signal_zero_ema(self) -> None:
        """Division by zero gracefully crashes — known limitation."""
        with pytest.raises(ZeroDivisionError):
            ema_signal(1, 0)


class TestMacdSignalLabel:
    @pytest.mark.parametrize(
        "macd_vals,signal_vals,hist_vals,expected_prefix",
        [
            # Bullish crossover
            ([-1, 1], [0, 0], [-0.5, 1.5], "bullish crossover"),
            # Bearish crossover
            ([1, -1], [0, 0], [0.5, -1.5], "bearish crossover"),
            # Bullish widening
            ([1.5, 2], [0.5, 1], [1.0, 1.0], None),  # abs(h_c)=1.0 > abs(h_p)=1.0? No, equal → converging
            # Let me use proper values: prev hist=0.5, curr hist=1.0
            ([1.5, 2], [0.5, 1], [0.5, 1.0], "bullish (widening)"),
            # Bullish converging: curr hist=0.5 < prev hist=1.0
            ([1.5, 2], [0.5, 1], [1.0, 0.5], "bullish (converging)"),
            # Bearish widening
            ([1, 0.5], [1.5, 1], [-0.3, -0.8], "bearish (widening)"),
            # Bearish converging
            ([1, 0.5], [1.5, 1], [-0.8, -0.3], "bearish (converging)"),
            # Insufficient data — 1 valid row
            ([1], [0], [0.5], "insufficient data"),
            # All NaN
            ([math.nan], [math.nan], [math.nan], "insufficient data"),
            # Zero hist (converging since abs(0) < abs(1))
            ([1.5, 2], [0.5, 1], [1.0, 0.0], "bullish (converging)"),
            # Sparse valid indices — only indices 0 and 5 are valid
            ([1, math.nan, math.nan, math.nan, math.nan, 2],
             [0, math.nan, math.nan, math.nan, math.nan, 0],
             [0.5, math.nan, math.nan, math.nan, math.nan, 1],
             "bullish (widening)"),
        ],
    )
    def test_macd_signal_label(
        self,
        macd_vals: list[float],
        signal_vals: list[float],
        hist_vals: list[float],
        expected_prefix: str | None,
    ) -> None:
        macd = pd.Series(macd_vals)
        signal = pd.Series(signal_vals)
        hist = pd.Series(hist_vals)
        result = macd_signal_label(macd, signal, hist)
        if expected_prefix is not None:
            assert result.startswith(expected_prefix), (
                f"Expected prefix '{expected_prefix}', got '{result}'"
            )


# =============================================================================
# T4: rsi_label, mfi_signal, obv_signal, bb_label
# =============================================================================

class TestRsiLabel:
    @pytest.mark.parametrize("rsi,expected_zone", [
        (29.9, "oversold"),
        (30.0, "bearish"),
        (39.9, "bearish"),
        (40.0, "neutral-bearish"),
        (49.9, "neutral-bearish"),
        (50.0, "neutral-bullish"),
        (59.9, "neutral-bullish"),
        (60.0, "bullish"),
        (69.9, "bullish"),
        (70.0, "overbought"),
    ])
    def test_rsi_zone(self, rsi: float, expected_zone: str) -> None:
        result = rsi_label(rsi)
        assert expected_zone in result, f"'{expected_zone}' not in '{result}'"

    def test_rsi_nan(self) -> None:
        """NaN RSI should NOT return 'overbought' (marks the latent bug)."""
        result = rsi_label(math.nan)
        assert "insufficient data" in result, (
            f"Expected 'insufficient data' for NaN, got '{result}'"
        )


class TestMfiSignal:
    @pytest.mark.parametrize("mfi,expected_zone", [
        (19.9, "oversold"),
        (20.0, "bearish"),
        (39.9, "bearish"),
        (40.0, "neutral-bearish"),
        (49.9, "neutral-bearish"),
        (50.0, "neutral-bullish"),
        (59.9, "neutral-bullish"),
        (60.0, "bullish"),
        (79.9, "bullish"),
        (80.0, "overbought"),
    ])
    def test_mfi_zone(self, mfi: float, expected_zone: str) -> None:
        result = mfi_signal(mfi)
        assert expected_zone in result, f"'{expected_zone}' not in '{result}'"

    def test_mfi_nan(self) -> None:
        result = mfi_signal(math.nan)
        assert "insufficient data" in result


class TestObvSignal:
    @pytest.mark.parametrize("obv_values,expected_substring", [
        ([100, 110, 120, 130, 140], "confirming uptrend"),
        ([140, 130, 120, 110, 100], "confirming downtrend"),
        ([100, 101, 102, 103, 104], "neutral / choppy"),
    ])
    def test_obv_signal(
        self, obv_values: list[float], expected_substring: str
    ) -> None:
        df = pd.DataFrame({"obv": obv_values})
        result = obv_signal(df)
        assert expected_substring in result, (
            f"'{expected_substring}' not in '{result}'"
        )

    def test_obv_boundary_uptrend(self) -> None:
        """Exactly 5.0% change → confirming uptrend."""
        # prior 3 mean = 100, last 2 mean = 105, change = 5.0%
        df = pd.DataFrame({"obv": [100, 100, 100, 105, 105]})
        result = obv_signal(df)
        assert "confirming uptrend" in result

    def test_obv_boundary_downtrend(self) -> None:
        """Exactly -5.0% change → confirming downtrend."""
        # Prior 3 mean=100, last 2 mean=95 → ((95-100)/100)*100 = -5.0%
        df = pd.DataFrame({"obv": [100, 100, 100, 95, 95]})
        result = obv_signal(df)
        assert "confirming downtrend" in result

    def test_obv_boundary_neutral(self) -> None:
        """Change < 5.0% → neutral."""
        df = pd.DataFrame({"obv": [100, 100, 100, 104, 104]})  # 4% change
        result = obv_signal(df)
        assert "neutral / choppy" in result

    def test_obv_insufficient_data(self) -> None:
        """4 non-NaN rows → insufficient data."""
        df = pd.DataFrame({"obv": [100, 110, 120, 130]})
        result = obv_signal(df)
        assert "insufficient data" in result


class TestBbLabel:
    @pytest.mark.parametrize(
        "close_price,upper,mid,lower,upper_arr,lower_arr,expected_substrings",
        [
            # Near upper
            (99, 100, 50, 0, pd.Series([100.0]), pd.Series([0.0]), ["near upper band"]),
            # Near lower
            (1, 100, 50, 0, pd.Series([100.0]), pd.Series([0.0]), ["near lower band"]),
            # Mid-range
            (50, 100, 50, 0, pd.Series([100.0]), pd.Series([0.0]), ["mid-range"]),
            # Upper half
            (80, 100, 50, 0, pd.Series([100.0]), pd.Series([0.0]), ["upper half"]),
            # Lower half
            (20, 100, 50, 0, pd.Series([100.0]), pd.Series([0.0]), ["lower half"]),
            # Band width zero
            (50, 100, 50, 100, pd.Series([100.0]), pd.Series([100.0]), ["band width zero"]),
            # Boundary pct_b = 0.95
            (95, 100, 50, 0, pd.Series([100.0]), pd.Series([0.0]), ["near upper band"]),
            # Boundary pct_b = 0.05
            (5, 100, 50, 0, pd.Series([100.0]), pd.Series([0.0]), ["near lower band"]),
            # Boundary pct_b = 0.39 → lower half (just below 0.4-0.6 range)
            (39, 100, 50, 0, pd.Series([100.0]), pd.Series([0.0]), ["lower half"]),
            # Boundary pct_b = 0.61 → upper half (just above 0.4-0.6 range)
            (61, 100, 50, 0, pd.Series([100.0]), pd.Series([0.0]), ["upper half"]),
        ],
    )
    def test_bb_position(
        self,
        close_price: float,
        upper: float,
        mid: float,
        lower: float,
        upper_arr: pd.Series,
        lower_arr: pd.Series,
        expected_substrings: list[str],
    ) -> None:
        result = bb_label(close_price, upper, mid, lower, upper_arr, lower_arr)
        for sub in expected_substrings:
            assert sub in result, f"'{sub}' not in '{result}'"

    def test_bb_squeeze(self) -> None:
        """Squeeze when current width < 0.9x average of last 10."""
        # 11 upper values, last 10 widths avg = 10, current = 8 (0.8x)
        upper_arr = pd.Series([100.0] * 11)
        lower_arr = pd.Series([0.0] * 11)
        # Current: upper=100, lower=92 → width=8
        result = bb_label(50, 100, 96, 92, upper_arr, lower_arr)
        assert "bands squeezing" in result

    def test_bb_expansion(self) -> None:
        """Expansion when current width > 1.1x average of last 10."""
        # 11 values: first 10 have upper=100, lower=90 → width=10 each, avg=10
        upper_arr = pd.Series([100.0] * 11)
        lower_arr = pd.Series([90.0] * 11)
        # Current: upper=100, lower=85 → width=15, 15 > 10*1.1=11 → expansion
        result = bb_label(50, 100, 92.5, 85, upper_arr, lower_arr)
        assert "bands expanding" in result

    def test_bb_no_squeeze_normal(self) -> None:
        """Width within 0.9-1.1x → no squeeze/expansion suffix."""
        upper_arr = pd.Series([100.0] * 11)
        lower_arr = pd.Series([10.0] * 11)
        # Current: upper=100, lower=10 → width=90
        # Prev widths: avg = (100-10)=90, current=90, ratio=1.0 → no suffix
        result = bb_label(50, 100, 55, 10, upper_arr, lower_arr)
        assert "squeezing" not in result
        assert "expanding" not in result

    def test_bb_insufficient_squeeze_data(self) -> None:
        """< 11 valid BB rows → no expansion suffix."""
        upper_arr = pd.Series([100.0] * 10)
        lower_arr = pd.Series([0.0] * 10)
        result = bb_label(50, 100, 50, 0, upper_arr, lower_arr)
        assert "squeezing" not in result
        assert "expanding" not in result


# =============================================================================
# T5: _macd_cross, _obv_slope, _price_vs_bb
# =============================================================================

class TestMacdCross:
    @pytest.mark.parametrize(
        "macd_vals,signal_vals,expected",
        [
            # Bullish at index 2
            ([-1, -0.5, 0.5, 1], [0, 0, 0, 0],
             ["none", "none", "bullish_cross", "none"]),
            # Bearish at index 2
            ([1, 0.5, -0.5, -1], [0, 0, 0, 0],
             ["none", "none", "bearish_cross", "none"]),
            # No cross
            ([1, 2, 3], [0, 0, 0], ["none", "none", "none"]),
            # NaN in middle — index 2 should be 'none' (skipped NaN at index 1)
            ([1, math.nan, 3], [0, 0, 0], None),  # skip for NaN test
            # All NaN
            ([math.nan, math.nan], [0, 0], ["none", "none"]),
            # Single row
            ([1], [0], ["none"]),
            # Cross at bar 1
            ([-1, 1], [0, 0], ["none", "bullish_cross"]),
            # Equal touching — no cross
            ([0, 0], [0, 0], ["none", "none"]),
        ],
    )
    def test_macd_cross(
        self,
        macd_vals: list[float],
        signal_vals: list[float],
        expected: list[str] | None,
    ) -> None:
        if expected is None:
            return  # skip NaN-middle case (tested separately)
        macd = pd.Series(macd_vals)
        signal = pd.Series(signal_vals)
        result = _macd_cross(macd, signal)
        assert result.tolist() == expected, f"Got {result.tolist()}"

    def test_macd_cross_nan_middle(self) -> None:
        """NaN in middle should be skipped."""
        macd = pd.Series([1, math.nan, 3])
        signal = pd.Series([0, 0, 0])
        result = _macd_cross(macd, signal)
        assert result.tolist() == ["none", "none", "none"]


class TestObvSlope:
    @pytest.mark.parametrize(
        "obv_vals,expected_last",
        [
            # Rising → 1.0
            ([0, 1, 2, 3, 4], 1.0),
            # Falling → -1.0
            ([4, 3, 2, 1, 0], -1.0),
            # Flat → 0.0
            ([10, 10, 10, 10, 10], 0.0),
            # Low magnitude → 0.0
            ([100, 100, 100, 100, 100.5], 0.0),
            # Large values → 1.0
            ([1e6, 2e6, 3e6, 4e6, 5e6], 1.0),
        ],
    )
    def test_obv_slope(
        self, obv_vals: list[float], expected_last: float
    ) -> None:
        result = _obv_slope(pd.Series(obv_vals))
        if math.isnan(expected_last):
            assert math.isnan(result.iloc[-1])
        else:
            assert result.iloc[-1] == expected_last, (
                f"Got {result.iloc[-1]} at last position"
            )

    def test_obv_slope_all_zero(self) -> None:
        """All zero → NaN (mean = 0)."""
        result = _obv_slope(pd.Series([0, 0, 0, 0, 0]))
        assert math.isnan(result.iloc[-1])

    def test_obv_slope_nan_in_window(self) -> None:
        """NaN in window → NaN result."""
        result = _obv_slope(pd.Series([1, math.nan, 3, 4, 5]))
        assert math.isnan(result.iloc[-1])

    def test_obv_slope_insufficient(self) -> None:
        """4 rows → all NaN."""
        result = _obv_slope(pd.Series([1, 2, 3, 4]))
        assert all(math.isnan(v) for v in result)

    def test_obv_slope_at_threshold(self) -> None:
        """Ratio == 0.01 → 0.0 (not > 0.01)."""
        # Build values where slope/|mean| = 0.01
        # y = [100, 100, 100, 100, 100 + δ]
        # mean ≈ 100, slope ≈ δ/2, ratio = (δ/2)/100 = δ/200
        # For ratio = 0.01: δ/200 = 0.01 → δ = 2.0
        obv = pd.Series([100.0, 100.0, 100.0, 100.0, 102.0])
        result = _obv_slope(obv)
        assert result.iloc[-1] == 0.0, f"Got {result.iloc[-1]}, expected 0.0"


class TestPriceVsBb:
    @pytest.mark.parametrize(
        "close_vals,upper_vals,lower_vals,expected",
        [
            # Above upper
            ([110], [100], [80], ["above_upper"]),
            # Below lower
            ([70], [100], [80], ["below_lower"]),
            # Inside
            ([90], [100], [80], ["inside"]),
            # Exactly on upper
            ([100], [100], [80], ["inside"]),
            # Exactly on lower
            ([80], [100], [80], ["inside"]),
            # Close NaN
            ([math.nan], [100], [80], ["none"]),
            # All NaN
            ([math.nan], [math.nan], [math.nan], ["none"]),
        ],
    )
    def test_price_vs_bb(
        self,
        close_vals: list[float],
        upper_vals: list[float],
        lower_vals: list[float],
        expected: list[str],
    ) -> None:
        close = pd.Series(close_vals)
        upper = pd.Series(upper_vals)
        lower = pd.Series(lower_vals)
        result = _price_vs_bb(close, upper, lower)
        assert result.tolist() == expected, f"Got {result.tolist()}"
