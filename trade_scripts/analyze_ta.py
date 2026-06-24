#!/usr/bin/env python3
"""
analyze_ta.py — Compute technical indicators on OHLCV CSVs using pandas-ta.

Indicators computed:
    EMA-21          (21-period Exponential Moving Average)
    MACD (12/26/9)  (Moving Average Convergence/Divergence)
    RSI-14          (14-period Relative Strength Index)
    BB (20, 2σ)     (Bollinger Bands — 20-period, 2 standard deviations)
    MFI-14          (14-period Money Flow Index)
    OBV             (On-Balance Volume)
    EBSW            (Ehlers Bandpass Super Smoother Wave)
    ATR-14          (14-period Average True Range)

Derived columns:
    MACD Cross      (bullish_cross / bearish_cross / none)
    OBV Slope       (rising / falling / flat → 1.0 / -1.0 / 0.0)
    EMA21 Slope     (percentage change from prior bar)
    Price vs BB     (above_upper / below_lower / inside / none)
    BB Width        (normalized band width)

Usage:
    # Top-down analysis (Daily → H4 → H1) — default when no --timeframe given
    uv run python scripts/analyze_ta.py BTC/USDT

    # Single timeframe
    uv run python scripts/analyze_ta.py BTC/USDT --timeframe 4h

    # Custom data directory
    uv run python scripts/analyze_ta.py BTC/USDT --data-dir data

Output:
    stdout  : digest with last value + derived signal per indicator per timeframe
    files   : data/ohlcv_BTCUSDT_{TF}_ta.csv  (enriched with all indicator columns)

Note:
    Run scripts/fetch_ohlcv.py first to ensure CSVs are present and up to date.
"""

import argparse
import csv
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv

load_dotenv()

TOP_DOWN_TIMEFRAMES = ["1d", "4h", "1h"]
TF_LABELS = {"1d": "DAILY", "4h": "H4", "1h": "H1"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def normalize_symbol(symbol: str) -> str:
    """BTC/USDT → BTCUSDT"""
    return symbol.replace("/", "")


def last_valid(series: pd.Series) -> float:
    """Return the last non-NaN value in series, or NaN if all are NaN."""
    valid = series.dropna()
    return float(valid.iloc[-1]) if len(valid) > 0 else float("nan")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_csv(path: Path) -> pd.DataFrame:
    """Load an OHLCV CSV into a pandas DataFrame (sorted, deduplicated)."""
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp")
    df = df.drop_duplicates(subset=["timestamp"], keep="last")
    df = df.reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Importable data-loading functions (for other scripts)
# ---------------------------------------------------------------------------
def load_ta_latest(symbol: str, timeframe: str,
                   data_dir: str = "data") -> dict | None:
    """Load the latest MFI-14 / OBV / OBV-slope values from enriched TA CSV.

    Parameters
    ----------
    symbol : str
        Spot-format symbol, e.g. ``"BTC/USDT"``.
    timeframe : str
        Timeframe, e.g. ``"4h"``.
    data_dir : str
        Directory containing the enriched TA CSV (default: ``"data"``).

    Returns
    -------
    dict | None
        ``{"mfi14": float, "obv": float, "obv_slope": float,
        "close": float, "timestamp": str}`` or ``None`` if file missing.
    """
    safe = symbol.replace("/", "")
    path = Path(data_dir) / f"ohlcv_{safe}_{timeframe}_ta.csv"

    if not path.exists():
        return None

    try:
        df = pd.read_csv(path)
        if df.empty:             # ← header-only CSV, no data rows
            return None
        last = df.iloc[-1]

        mfi14 = float(last.get("mfi14")) if pd.notna(last.get("mfi14")) else None
        obv = float(last.get("obv")) if pd.notna(last.get("obv")) else None
        obv_slope_raw = last.get("obv_slope")
        obv_slope = float(obv_slope_raw) if obv_slope_raw not in (None, "", "none") and pd.notna(obv_slope_raw) else 0.0
        close = float(last.get("close")) if pd.notna(last.get("close")) else None
        ts = str(last.get("timestamp", ""))

        if mfi14 is None or obv is None:
            return None

        return {
            "mfi14": mfi14,
            "obv": obv,
            "obv_slope": obv_slope,
            "close": close,
            "timestamp": ts,
        }
    except (pd.errors.EmptyDataError, KeyError, ValueError):
        return None


def load_ta_series(symbol: str, timeframe: str,
                   data_dir: str = "data",
                   tail: int = 10) -> pd.DataFrame | None:
    """Load the last N rows of enriched TA OHLCV data.

    Returns a DataFrame with columns ``timestamp``, ``open``, ``high``,
    ``low``, ``close``, ``volume`` plus all indicator columns.  Useful
    for ta-lib CDL pattern detection which needs multiple candles.

    Parameters
    ----------
    symbol : str
        Spot-format symbol, e.g. ``"BTC/USDT"``.
    timeframe : str
        Timeframe, e.g. ``"4h"``.
    data_dir : str
        Directory containing the enriched TA CSV (default: ``"data"``).
    tail : int
        Number of recent rows to return (default: 10).

    Returns
    -------
    pd.DataFrame | None
        Last ``tail`` rows of the enriched CSV, or ``None`` if missing.
    """
    safe = symbol.replace("/", "")
    path = Path(data_dir) / f"ohlcv_{safe}_{timeframe}_ta.csv"

    if not path.exists():
        return None

    try:
        df = pd.read_csv(path, parse_dates=["timestamp"])
        return df.tail(tail).reset_index(drop=True)
    except (pd.errors.EmptyDataError, KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Indicator computation
# ---------------------------------------------------------------------------
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all TA indicators using pandas-ta and return enriched DataFrame.
    """
    # ── Guard: not enough rows for the highest-period indicator (EBSW needs 40) ──
    MIN_ROWS = 40
    if len(df) < MIN_ROWS:
        indicator_cols = [
            "ema21", "macd", "macd_signal", "macd_hist",
            "rsi14", "bb_upper", "bb_mid", "bb_lower",
            "mfi14", "obv", "ebsw", "atr14",
            "macd_cross", "obv_slope", "ema21_slope",
            "price_vs_bb", "bb_width",
        ]
        for col in indicator_cols:
            df[col] = np.nan
        return df

    # ---- Core indicators via pandas-ta ----
    # EMA-21
    df["ema21"] = ta.ema(df["close"], length=21)

    # MACD (12/26/9)
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    # pandas_ta returns: [MACD (line), MACDh (histogram), MACDs (signal)]
    df["macd"] = macd_df.iloc[:, 0]  # MACD line
    df["macd_hist"] = macd_df.iloc[:, 1]  # Histogram
    df["macd_signal"] = macd_df.iloc[:, 2]  # Signal line

    # RSI-14
    df["rsi14"] = ta.rsi(df["close"], length=14)

    # Bollinger Bands (20, 2σ)
    bb_df = ta.bbands(df["close"], length=20, std=2)
    # pandas_ta returns: [BBL (lower), BBM (mid), BBU (upper), BBB (bandwidth), BBP (percent)]
    df["bb_upper"] = bb_df.iloc[:, 2]  # BBU → upper band
    df["bb_mid"] = bb_df.iloc[:, 1]  # BBM → middle band
    df["bb_lower"] = bb_df.iloc[:, 0]  # BBL → lower band

    # MFI-14
    df["mfi14"] = ta.mfi(df["high"], df["low"], df["close"], df["volume"], length=14)

    # OBV
    df["obv"] = ta.obv(df["close"], df["volume"])

    # EBSW (Ehlers Bandpass Super Smoother Wave)
    df["ebsw"] = ta.ebsw(df["close"])

    # ATR-14 (Average True Range)
    df["atr14"] = ta.atr(df["high"], df["low"], df["close"], length=14, mamode="rma")

    # ---- Derived columns ----
    # MACD Cross detection
    df["macd_cross"] = _macd_cross(df["macd"], df["macd_signal"])

    # OBV Slope (5-bar linear regression, normalized)
    df["obv_slope"] = _obv_slope(df["obv"])

    # EMA21 Slope (percentage change from prior bar)
    df["ema21_slope"] = df["ema21"].pct_change()

    # Price vs BB classification
    df["price_vs_bb"] = _price_vs_bb(df["close"], df["bb_upper"], df["bb_lower"])

    # BB Width (normalized)
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    return df


def _macd_cross(macd: pd.Series, signal: pd.Series) -> pd.Series:
    """Detect MACD crossovers: bullish_cross, bearish_cross, or none."""
    result = pd.Series("none", index=macd.index, dtype=object)

    for i in range(1, len(macd)):
        m_prev, m_curr = macd.iloc[i - 1], macd.iloc[i]
        s_prev, s_curr = signal.iloc[i - 1], signal.iloc[i]

        if pd.isna(m_prev) or pd.isna(s_prev) or pd.isna(m_curr) or pd.isna(s_curr):
            continue

        # Bullish cross: MACD crosses above signal
        if m_prev <= s_prev and m_curr > s_curr:
            result.iloc[i] = "bullish_cross"
        # Bearish cross: MACD crosses below signal
        elif m_prev >= s_prev and m_curr < s_curr:
            result.iloc[i] = "bearish_cross"

    return result


def _obv_slope(obv: pd.Series) -> pd.Series:
    """
    Compute OBV slope over 5-bar window using linear regression.
    Returns: 1.0 (rising), -1.0 (falling), 0.0 (flat), or NaN.
    """
    result = pd.Series(np.nan, index=obv.index, dtype=float)

    for i in range(4, len(obv)):
        window = obv.iloc[i - 4:i + 1]
        if window.isna().any():
            continue

        x = np.arange(5, dtype=float)
        y = window.values
        mean_x = x.mean()
        mean_y = y.mean()

        num = np.sum((x - mean_x) * (y - mean_y))
        denom = np.sum((x - mean_x) ** 2)

        if denom == 0:
            continue

        slope = num / denom
        mean_obv = np.mean(y)

        if abs(mean_obv) < 1e-9:
            continue

        ratio = slope / abs(mean_obv)

        if ratio > 0.01:
            result.iloc[i] = 1.0  # rising
        elif ratio < -0.01:
            result.iloc[i] = -1.0  # falling
        else:
            result.iloc[i] = 0.0  # flat

    return result


def _price_vs_bb(close: pd.Series, upper: pd.Series, lower: pd.Series) -> pd.Series:
    """Classify price position relative to Bollinger Bands."""
    result = pd.Series("none", index=close.index, dtype=object)

    for i in range(len(close)):
        c = close.iloc[i]
        u = upper.iloc[i]
        l = lower.iloc[i]

        if pd.isna(c) or pd.isna(u) or pd.isna(l):
            result.iloc[i] = "none"
        elif c > u:
            result.iloc[i] = "above_upper"
        elif c < l:
            result.iloc[i] = "below_lower"
        else:
            result.iloc[i] = "inside"

    return result


# ---------------------------------------------------------------------------
# Derived signal labels (for stdout digest)
# ---------------------------------------------------------------------------
def ema_signal(close_price: float, ema: float) -> str:
    pct = (close_price - ema) / ema * 100
    if abs(pct) <= 0.1:
        return f"at ({pct:+.1f}%)"
    elif pct > 0:
        return f"above (+{pct:.1f}%)"
    else:
        return f"below ({pct:.1f}%)"


def macd_signal_label(macd: pd.Series, signal: pd.Series, hist: pd.Series) -> str:
    valid_mask = ~(macd.isna() | signal.isna() | hist.isna())
    valid_idx = valid_mask[valid_mask].index.tolist()

    if len(valid_idx) < 2:
        return "insufficient data"

    prev_i, curr_i = valid_idx[-2], valid_idx[-1]
    m_c = float(macd.loc[curr_i])
    s_c = float(signal.loc[curr_i])
    h_c = float(hist.loc[curr_i])
    m_p = float(macd.loc[prev_i])
    s_p = float(signal.loc[prev_i])
    h_p = float(hist.loc[prev_i])

    crossed_up = m_p <= s_p and m_c > s_c
    crossed_dn = m_p >= s_p and m_c < s_c

    hist_sign = "+" if h_c >= 0 else ""
    suffix = f"{m_c:.2f} / Signal {s_c:.2f} / Hist {hist_sign}{h_c:.2f}"

    if crossed_up:
        return f"bullish crossover — {suffix}"
    if crossed_dn:
        return f"bearish crossover — {suffix}"

    if m_c > s_c:
        direction = "bullish (widening)" if abs(h_c) > abs(h_p) else "bullish (converging)"
    else:
        direction = "bearish (widening)" if abs(h_c) > abs(h_p) else "bearish (converging)"

    return f"{direction} — {suffix}"


def rsi_label(rsi: float) -> str:
    if math.isnan(rsi):
        return "insufficient data"
    if rsi < 30:
        zone = "oversold"
    elif rsi < 40:
        zone = "bearish"
    elif rsi < 50:
        zone = "neutral-bearish"
    elif rsi < 60:
        zone = "neutral-bullish"
    elif rsi < 70:
        zone = "bullish"
    else:
        zone = "overbought"
    return f"{rsi:.1f} — {zone}"


def mfi_signal(mfi: float) -> str:
    if math.isnan(mfi):
        return "insufficient data"
    if mfi < 20:
        zone = "oversold"
    elif mfi < 40:
        zone = "bearish"
    elif mfi < 50:
        zone = "neutral-bearish"
    elif mfi < 60:
        zone = "neutral-bullish"
    elif mfi < 80:
        zone = "bullish"
    else:
        zone = "overbought"
    return f"{mfi:.1f} — {zone}"


def obv_signal(df: pd.DataFrame) -> str:
    obv_arr = df["obv"]
    obv_valid = obv_arr.dropna()

    if len(obv_valid) < 5:
        return "insufficient data"

    mean_last_2 = np.mean(obv_valid.iloc[-2:])
    mean_prior_3 = np.mean(obv_valid.iloc[-5:-2])
    denominator = max(abs(mean_prior_3), 1.0)
    change_pct = ((mean_last_2 - mean_prior_3) / denominator) * 100

    if abs(change_pct) < 5.0:
        return "neutral / choppy"
    elif change_pct >= 5.0:
        return "confirming uptrend"
    else:
        return "confirming downtrend"


def bb_label(
    close_price: float,
    upper: float, mid: float, lower: float,
    upper_arr: pd.Series, lower_arr: pd.Series,
) -> str:
    band_width = upper - lower
    if band_width == 0:
        return "band width zero"

    pct_b = (close_price - lower) / band_width

    if pct_b >= 0.95:
        position = "near upper band"
    elif pct_b <= 0.05:
        position = "near lower band"
    elif 0.4 <= pct_b <= 0.6:
        position = "mid-range"
    elif pct_b > 0.6:
        position = "upper half"
    else:
        position = "lower half"

    # Squeeze / expansion vs rolling average of last 10 valid widths
    valid_u = upper_arr.dropna()
    valid_l = lower_arr.dropna()

    if len(valid_u) >= 11:
        prev_widths = valid_u.iloc[-11:-1].values - valid_l.iloc[-11:-1].values
        avg_width = float(np.mean(prev_widths))
        if band_width > avg_width * 1.1:
            expansion = ", bands expanding"
        elif band_width < avg_width * 0.9:
            expansion = ", bands squeezing"
        else:
            expansion = ""
    else:
        expansion = ""

    return f"{position}{expansion} — upper {upper:,.2f} / mid {mid:,.2f} / lower {lower:,.2f}"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_timeframe_block(timeframe: str, df: pd.DataFrame) -> None:
    close_price = float(df["close"].iloc[-1])
    ema_val = last_valid(df["ema21"])
    rsi_val = last_valid(df["rsi14"])
    bb_u = last_valid(df["bb_upper"])
    bb_m = last_valid(df["bb_mid"])
    bb_l = last_valid(df["bb_lower"])

    ema_sig = ema_signal(close_price, ema_val) if not math.isnan(ema_val) else "insufficient data"
    macd_sig = macd_signal_label(df["macd"], df["macd_signal"], df["macd_hist"])
    rsi_sig = rsi_label(rsi_val) if not math.isnan(rsi_val) else "insufficient data"
    bb_sig = bb_label(close_price, bb_u, bb_m, bb_l, df["bb_upper"], df["bb_lower"]) if not math.isnan(bb_u) else "insufficient data"
    mfi_val = last_valid(df["mfi14"])
    mfi_sig = mfi_signal(mfi_val) if not math.isnan(mfi_val) else "insufficient data"
    obv_sig = obv_signal(df)
    atr14_val = last_valid(df["atr14"])

    # Derived labels
    obv_slope_last = str(df["obv_slope"].iloc[-1]) if not pd.isna(df["obv_slope"].iloc[-1]) else "none"
    bb_width_last = float(df["bb_width"].iloc[-1])
    bb_width_disp = f"{bb_width_last:.3f}" if not pd.isna(bb_width_last) else "n/a"
    price_vs_bb_disp = str(df["price_vs_bb"].iloc[-1])
    macd_cross_disp = str(df["macd_cross"].iloc[-1])
    ema_slope_last = float(df["ema21_slope"].iloc[-1])
    ema_slope_disp = f"{ema_slope_last:.4f}" if not pd.isna(ema_slope_last) else "n/a"
    ebsw_val = last_valid(df["ebsw"])
    ebsw_disp = f"{ebsw_val:.2f}" if not math.isnan(ebsw_val) else "n/a"

    label = TF_LABELS.get(timeframe, timeframe.upper())
    print(f"\n{label}")
    print(f"  EMA21       : {ema_val:,.2f} — price {ema_sig}")
    print(f"  MACD        : {macd_sig}")
    print(f"  RSI14       : {rsi_sig}")
    print(f"  BB          : {bb_sig}")
    print(f"  MFI14       : {mfi_sig}")
    print(f"  OBV         : {obv_sig}")
    print(f"  EBSW        : {ebsw_disp}")
    atr14_disp = f"{atr14_val:.2f}" if not math.isnan(atr14_val) else "n/a"
    print(f"  ATR14       : {atr14_disp}")
    print(f"  OBV Slope   : {obv_slope_last}")
    print(f"  BB Width    : {bb_width_disp}")
    print(f"  Price vs BB : {price_vs_bb_disp}")
    print(f"  EMA21 Slope : {ema_slope_disp}")
    print(f"  MACD Cross  : {macd_cross_disp}")


def save_enriched_csv(df: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "timestamp", "open", "high", "low", "close", "volume",
        "ema21", "macd", "macd_signal", "macd_hist",
        "rsi14", "bb_upper", "bb_mid", "bb_lower",
        "mfi14", "obv", "ebsw",
        "atr14",
        "macd_cross", "ema21_slope", "price_vs_bb", "bb_width", "obv_slope",
    ]

    def fmt(v) -> str:
        if pd.isna(v):
            return ""
        return f"{float(v):.8f}"

    # Ensure timestamp is string formatted
    df_out = df.copy()
    if "timestamp" in df_out.columns:
        df_out["timestamp"] = df_out["timestamp"].astype(str)

    # Write to temp file, then atomic rename
    fd, tmp_path = tempfile.mkstemp(dir=str(out_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for _, row in df_out.iterrows():
                writer.writerow({
                    "timestamp": row.get("timestamp", ""),
                    "open": fmt(row.get("open")),
                    "high": fmt(row.get("high")),
                    "low": fmt(row.get("low")),
                    "close": fmt(row.get("close")),
                    "volume": fmt(row.get("volume")),
                    "ema21": fmt(row.get("ema21")),
                    "macd": fmt(row.get("macd")),
                    "macd_signal": fmt(row.get("macd_signal")),
                    "macd_hist": fmt(row.get("macd_hist")),
                    "rsi14": fmt(row.get("rsi14")),
                    "bb_upper": fmt(row.get("bb_upper")),
                    "bb_mid": fmt(row.get("bb_mid")),
                    "bb_lower": fmt(row.get("bb_lower")),
                    "mfi14": fmt(row.get("mfi14")),
                    "obv": fmt(row.get("obv")),
                    "ebsw": fmt(row.get("ebsw")),
                    "atr14": fmt(row.get("atr14")),
                    "macd_cross": str(row.get("macd_cross", "none")) if row.get("macd_cross") != "none" else "none",
                    "ema21_slope": fmt(row.get("ema21_slope")),
                    "price_vs_bb": str(row.get("price_vs_bb", "none")),
                    "bb_width": fmt(row.get("bb_width")),
                    "obv_slope": str(row.get("obv_slope", "")),
                })
        shutil.move(tmp_path, str(out_path))
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Per-timeframe orchestration
# ---------------------------------------------------------------------------
def analyze_timeframe(symbol: str, timeframe: str, data_dir: Path) -> bool:
    norm_sym = normalize_symbol(symbol)
    csv_path = data_dir / f"ohlcv_{norm_sym}_{timeframe}.csv"

    if not csv_path.exists():
        print(
            f"  [WARN] {csv_path} not found — run fetch_ohlcv.py first",
            file=sys.stderr,
        )
        return False

    df = load_csv(csv_path)
    df = compute_indicators(df)

    print_timeframe_block(timeframe, df)

    out_path = data_dir / f"ohlcv_{norm_sym}_{timeframe}_ta.csv"
    save_enriched_csv(df, out_path)
    print(f"  → {out_path}")

    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute TA indicators (EMA-21, MACD, RSI-14, BB, MFI, OBV, EBSW, ATR-14) on OHLCV CSVs using pandas-ta."
    )
    parser.add_argument("symbol", help="Trading pair, e.g. BTC/USDT")
    parser.add_argument(
        "--timeframe",
        metavar="TF",
        help="Single timeframe to analyze (default: all 3 — 1d, 4h, 1h)",
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        metavar="DIR",
        help="Directory containing OHLCV CSVs (default: data)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    timeframes = [args.timeframe] if args.timeframe else TOP_DOWN_TIMEFRAMES
    data_dir = Path(args.data_dir)

    tf_str = " → ".join(tf.upper() for tf in timeframes)
    print(f"\n{args.symbol} | TA INDICATORS | {tf_str}")
    print("=" * 60)

    for tf in timeframes:
        analyze_timeframe(args.symbol, tf, data_dir)

    print()


if __name__ == "__main__":
    main()
