"""
market_snapshot.py — MarketSnapshot dataclass + SnapshotBuilder.

MarketSnapshot is a pure-data record of market state at a single point in time
for one timeframe. It contains NO scoring, opinions, or trading logic — only
factual observations extracted from TA data and the SMC per-candle report.

SnapshotBuilder constructs a MarketSnapshot from a single TA row and a full
26-column per_candle_report DataFrame.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class MarketSnapshot:
    """Frozen record of market state for one symbol/timeframe.

    All optional fields default to ``None``. The only computed field is
    ``trend_direction`` (close vs ema21 comparison).
    """

    # Identity
    symbol: str
    timeframe: str
    timestamp: pd.Timestamp
    close: float

    # Trend
    trend_direction: str  # "above" / "below" / "at" — from ema_signal() logic
    ema21: float
    ema21_slope: float

    # Momentum
    rsi14: float
    mfi14: float
    macd: float
    macd_signal: float
    macd_hist: float

    # Volatility
    atr14: float
    bb_width: float

    # Structure (optional)
    last_swing_direction: int | None = None
    last_swing_level: float | None = None
    last_bos_direction: int | None = None
    last_bos_index: int | None = None
    last_choch_direction: int | None = None
    last_choch_index: int | None = None

    # Liquidity (optional)
    nearest_liquidity_above: float | None = None
    nearest_liquidity_below: float | None = None

    # Order blocks (optional)
    active_bullish_ob: float | None = None
    active_bearish_ob: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_trend_direction(close: float, ema21: float) -> str:
    """Determine trend direction using the same 0.1% threshold as ``ema_signal()``.

    Returns ``"above"``, ``"below"``, or ``"at"``.
    """
    if pd.isna(close) or pd.isna(ema21) or ema21 == 0:
        return "at"
    pct = (close - ema21) / ema21 * 100.0
    if abs(pct) <= 0.1:
        return "at"
    if pct > 0:
        return "above"
    return "below"


def _last_non_nan(series: pd.Series) -> float | None:
    """Return the last non-NaN value in *series*, or ``None``."""
    valid = series.dropna()
    if len(valid) == 0:
        return None
    return float(valid.iloc[-1])


def _last_valid_index(series: pd.Series) -> int | None:
    """Return the integer index of the last non-NaN value, or ``None``."""
    valid = series.dropna()
    if len(valid) == 0:
        return None
    return int(valid.index[-1])


def _safe_int(val: object) -> int | None:
    """Convert a float/int value to int, or return ``None`` if NaN/invalid."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if pd.isna(val):
            return None
        return int(val)
    return None


# ---------------------------------------------------------------------------
# SnapshotBuilder
# ---------------------------------------------------------------------------


class SnapshotBuilder:
    """Constructs a ``MarketSnapshot`` from a TA row + full SMC report.

    Design notes:
      - TA fields are read directly from *ta_row*.
      - ``trend_direction`` is computed via ``_compute_trend_direction``.
      - Structure fields (swing, BOS, CHOCH) are the last non-NaN value
        in the corresponding column of *smc_report*.
      - Liquidity and OB fields scan the entire *smc_report* for unmitigated
        zones and pick the levels closest to current price.
    """

    def build(
        self,
        symbol: str,
        timeframe: str,
        ta_row: pd.Series,
        smc_report: pd.DataFrame,
    ) -> MarketSnapshot:
        """Build a ``MarketSnapshot`` from a single TA row + SMC report.

        Parameters
        ----------
        symbol : str
            Trading pair symbol (e.g. ``"BTC/USDT"``).
        timeframe : str
            Timeframe label (e.g. ``"1d"``, ``"4h"``, ``"1h"``).
        ta_row : pd.Series
            Single row from a TA-enriched DataFrame. Must contain columns:
            ``close``, ``ema21``, ``ema21_slope``, ``rsi14``, ``mfi14``,
            ``macd``, ``macd_signal``, ``macd_hist``, ``atr14``, ``bb_width``.
        smc_report : pd.DataFrame
            Full 26-column per-candle report from
            ``backtest.build_per_candle_report()``.

        Returns
        -------
        MarketSnapshot
        """
        # Safely extract float values from ta_row
        def _f(col: str) -> float:
            val = ta_row.get(col)
            if val is None or pd.isna(val):
                return float("nan")
            return float(val)

        close = _f("close")
        ema21 = _f("ema21")
        ema21_slope = _f("ema21_slope")
        rsi14 = _f("rsi14")
        mfi14 = _f("mfi14")
        macd = _f("macd")
        macd_signal = _f("macd_signal")
        macd_hist = _f("macd_hist")
        atr14 = _f("atr14")
        bb_width = _f("bb_width")

        # Timestamp
        ts_val = ta_row.get("timestamp")
        if isinstance(ts_val, pd.Timestamp):
            timestamp = ts_val
        elif isinstance(ts_val, str):
            timestamp = pd.Timestamp(ts_val)
        else:
            timestamp = pd.Timestamp.now()

        # Trend direction
        trend_direction = _compute_trend_direction(close, ema21)

        # Structure fields: last non-NaN scan (tie BrokenIndex to its row)
        swing_col = smc_report.get("SwingHighLow", pd.Series(dtype=float))
        swing_level_col = smc_report.get("SwingLevel", pd.Series(dtype=float))
        bos_col = smc_report.get("BOS", pd.Series(dtype=float))
        choch_col = smc_report.get("CHOCH", pd.Series(dtype=float))
        broken_index_col = smc_report.get("BrokenIndex", pd.Series(dtype=float))

        last_swing_direction = _last_non_nan(swing_col)
        last_swing_level = _last_non_nan(swing_level_col)

        # BOS: find last non-NaN row and get its BrokenIndex
        bos_last_idx = _last_valid_index(bos_col)
        if bos_last_idx is not None and not broken_index_col.empty:
            last_bos_direction = _safe_int(bos_col.iloc[bos_last_idx])
            last_bos_index = _safe_int(broken_index_col.iloc[bos_last_idx])
        else:
            last_bos_direction = None
            last_bos_index = None

        # CHOCH: find last non-NaN row and get its BrokenIndex
        choch_last_idx = _last_valid_index(choch_col)
        if choch_last_idx is not None and not broken_index_col.empty:
            last_choch_direction = _safe_int(choch_col.iloc[choch_last_idx])
            last_choch_index = _safe_int(broken_index_col.iloc[choch_last_idx])
        else:
            last_choch_direction = None
            last_choch_index = None

        # Liquidity: scan for unmitigated zones
        liq_col = smc_report.get("Liquidity", pd.Series(dtype=float))
        liq_level_col = smc_report.get("LiqLevel", pd.Series(dtype=float))
        liq_swept_col = smc_report.get("LiqSwept", pd.Series(dtype=float))

        nearest_liquidity_above: float | None = None
        nearest_liquidity_below: float | None = None

        if not liq_col.empty and not liq_level_col.empty:
            for i in range(len(smc_report)):
                liq_val = liq_col.iloc[i]
                liq_level = liq_level_col.iloc[i]
                liq_swept = liq_swept_col.iloc[i] if not liq_swept_col.empty else float("nan")

                if pd.isna(liq_val) or pd.isna(liq_level):
                    continue
                # Skip swept zones
                if not pd.isna(liq_swept) and liq_swept != 0:
                    continue

                level = float(liq_level)
                if liq_val == 1:  # Bullish liquidity (above)
                    if level > close:
                        if nearest_liquidity_above is None or level < nearest_liquidity_above:
                            nearest_liquidity_above = level
                elif liq_val == -1:  # Bearish liquidity (below)
                    if level < close:
                        if nearest_liquidity_below is None or level > nearest_liquidity_below:
                            nearest_liquidity_below = level

        # Order Blocks: scan for unmitigated
        ob_col = smc_report.get("OB", pd.Series(dtype=float))
        ob_top_col = smc_report.get("OBTop", pd.Series(dtype=float))
        ob_bottom_col = smc_report.get("OBBottom", pd.Series(dtype=float))
        ob_mitigated_col = smc_report.get("OBMitigatedIndex", pd.Series(dtype=float))

        active_bullish_ob: float | None = None
        active_bearish_ob: float | None = None

        if not ob_col.empty:
            for i in range(len(smc_report)):
                ob_val = ob_col.iloc[i]
                ob_mitigated = ob_mitigated_col.iloc[i] if not ob_mitigated_col.empty else float("nan")

                if pd.isna(ob_val):
                    continue
                # Skip mitigated OBs
                if not pd.isna(ob_mitigated) and ob_mitigated != 0:
                    continue

                if ob_val == 1:  # Bullish OB — need OBBottom
                    if ob_bottom_col.empty:
                        continue
                    ob_bottom = ob_bottom_col.iloc[i]
                    if pd.isna(ob_bottom):
                        continue
                    level = float(ob_bottom)
                    if active_bullish_ob is None or abs(level - close) < abs(active_bullish_ob - close):
                        active_bullish_ob = level
                elif ob_val == -1:  # Bearish OB — need OBTop
                    if ob_top_col.empty:
                        continue
                    ob_top = ob_top_col.iloc[i]
                    if pd.isna(ob_top):
                        continue
                    level = float(ob_top)
                    if active_bearish_ob is None or abs(level - close) < abs(active_bearish_ob - close):
                        active_bearish_ob = level

        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
            close=close,
            trend_direction=trend_direction,
            ema21=ema21,
            ema21_slope=ema21_slope,
            rsi14=rsi14,
            mfi14=mfi14,
            macd=macd,
            macd_signal=macd_signal,
            macd_hist=macd_hist,
            atr14=atr14,
            bb_width=bb_width,
            last_swing_direction=int(last_swing_direction) if last_swing_direction is not None else None,
            last_swing_level=last_swing_level,
            last_bos_direction=int(last_bos_direction) if last_bos_direction is not None else None,
            last_bos_index=last_bos_index,
            last_choch_direction=int(last_choch_direction) if last_choch_direction is not None else None,
            last_choch_index=last_choch_index,
            nearest_liquidity_above=nearest_liquidity_above,
            nearest_liquidity_below=nearest_liquidity_below,
            active_bullish_ob=active_bullish_ob,
            active_bearish_ob=active_bearish_ob,
        )
