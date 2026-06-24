# Oracle Test Plan Scope: `analyze_ta.py`

**Reviewer:** Oracle  
**Date:** 2026-06-23  
**Scope:** Complete component inventory, synthetic data design, edge case catalog, and test file recommendation for `trade_scripts/analyze_ta.py`.

---

## Bottom Line

`analyze_ta.py` has 19 testable components across 4 tiers of complexity. The test suite needs **~60 tests** (40 unit, 15 integration, 5 system) with **35 rows of synthetic OHLCV data** as the minimum fixture for full indicator warmup. Total effort is **Medium (1–2d)** — achievable in a single focused session.

---

## 1. Component Matrix

| # | Function | Type | Deps | Min Rows | Priority | Notes |
|---|----------|------|------|----------|----------|-------|
| 1 | `normalize_symbol` | Unit | none | — | P0 | Trivial, but needed for module correctness |
| 2 | `last_valid` | Unit | pandas | — | P1 | Used in print/output paths |
| 3 | `_macd_cross` | Unit | pandas, numpy | 2 valid | P0 | Core derived column; correctness-critical |
| 4 | `_obv_slope` | Unit | pandas, numpy | 5 valid | P0 | Core derived column; slope logic is subtle |
| 5 | `_price_vs_bb` | Unit | pandas | 1 valid | P0 | Core derived column |
| 6 | `ema_signal` | Unit | none | — | P2 | String formatting, trivial but cheap to test |
| 7 | `macd_signal_label` | Unit | pandas, numpy | 2 valid rows | P0 | Complex branching logic — most bug-prone |
| 8 | `rsi_label` | Unit | none | — | P1 | Zone boundary checks |
| 9 | `mfi_signal` | Unit | math | — | P1 | Zone boundary checks + NaN |
| 10 | `obv_signal` | Integration | pandas, numpy | 5 valid | P1 | DataFrame input; trend vs neutral logic |
| 11 | `bb_label` | Unit | pandas, numpy | 11 valid BB | P1 | Squeeze/expansion; %B position |
| 12 | `load_csv` | Integration | pandas, Path | — | P0 | File-based; sort+dedup correctness |
| 13 | `load_ta_latest` | Integration | pandas, Path | — | P0 | Used by downstream consumers |
| 14 | `load_ta_series` | Integration | pandas, Path | — | P1 | Used by pattern detection |
| 15 | `compute_indicators` | Integration | pandas_ta, numpy | **35** | P0 | All indicator labels; identity checks |
| 16 | `save_enriched_csv` | Integration | csv, os, tempfile | — | P0 | Atomic write correctness |
| 17 | `print_timeframe_block` | System | stdout | 1 | P2 | Snapshot test via capsys |
| 18 | `analyze_timeframe` | System | all of above | 35 | P1 | End-to-end per-timeframe |
| 19 | `main` / `parse_args` | System | argparse, sys | 35 | P2 | CLI integration |

### Priority Definitions

- **P0** — Core correctness: bugs here produce wrong trade signals. Must test first.
- **P1** — Important: wrong output degrades user experience (bad labels, etc.).
- **P2** — Polish: formatting/CLI; low risk of silent failure.

---

## 2. Synthetic Data Fixture Design

### Minimum Row Count: **35 rows**

Derived from maximum indicator warmup requirement:

| Indicator | Period | First Non-NaN Row (0-indexed) | Cumulative Rows |
|-----------|--------|-------------------------------|-----------------|
| OBV | 0 (instant) | 0 | 0 |
| EBSW | unknown (no param) | ~0 | 0 |
| RSI-14 | 14 | 13 | 14 |
| BB(20,2σ) | 20 | 19 | 20 |
| EMA-21 | 21 | 20 | 21 |
| ATR-14 | 14 | 13 | 14 |
| MFI-14 | 14 | 13 | 14 |
| MACD line (12/26/9) | 26 (slow EMA) | 25 | 26 |
| MACD signal | 9 (MACD→EMA) | 33 | **34** |
| MACD histogram | auto (MACD - signal) | 33 | **34** |
| MACD cross | needs previous bar | 34 | **35** |
| OBV slope | 5 | 4 | 5 |
| BB squeeze | 11 BB values | 29 | 30 |

**Floor = 35 rows** is the minimum to produce a non-NaN `macd_cross` value.

### Fixture Formula

```python
import numpy as np
import pandas as pd

N = 35  # minimum for full warmup

def synthetic_ohlcv(n: int = 35, base: float = 50000.0, step: float = 10.0) -> pd.DataFrame:
    """
    Deterministic uptrend. Open = prior bar's close (no gap).
    Close steps up linearly.
    """
    ts = pd.date_range("2024-01-01", periods=n, freq="1h")
    close = base + np.arange(n, dtype=float) * step
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) * 1.002   # 0.2% above
    low = np.minimum(open_, close) * 0.998    # 0.2% below
    volume = np.full(n, 100.0)
    return pd.DataFrame({
        "timestamp": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })
```

### Known-Value Test Cases

#### A. Constant Price (zero volatility)

```python
close = np.full(N, 50000.0)
```

| Indicator | Expected Value | Rationale |
|-----------|---------------|-----------|
| BB mid | 50000.0 | Mean of constant = constant |
| BB upper | 50000.0 | Std = 0, so bands are flat |
| BB lower | 50000.0 | Same |
| BB width | 0.0 | (upper - lower) / mid = 0 |
| RSI-14 | 50.0 | Zero price change → exactly neutral |
| MACD line | 0.0 | EMA12 - EMA26 = 0 (both = 50000) |
| MACD signal | 0.0 | EMA of zero = zero |
| MACD hist | 0.0 | 0 - 0 = 0 |
| MACD cross | "none" | MACD and signal both zero, never cross |
| OBV | 0.0 | No price change, zero volume flow |
| EMA21 | 50000.0 | Constant → EMA = constant |
| EMA21 slope | 0.0 | No change |
| OBV slope | 0.0 | Flat |
| Price vs BB | "inside" | Close = bands |
| ATR-14 | ~0 | High-low range ≈ 0 |
| MFI-14 | 50.0 | All money flow neutral |

**Caveat:** Some indicators may return NaN for constant price (zero std). RSI-14 in particular may return NaN if all price changes are zero. This is an edge case worth testing — it verifies the code handles NaN gracefully.

#### B. Linear Trend (known MACD/Ratio)

```python
close = 50000.0 + np.arange(N) * 10.0
```

| Indicator | Expected | Rationale |
|-----------|----------|-----------|
| RSI-14 | > 50 (deviation from mid) | Upward trend |
| EMA21 | < close | EMA lags behind uptrend |
| OBV slope | 1.0 (rising) | Consistent uptrend → rising volume/price |

The OBV slope can be computed exactly: with `close` rising at $10/bar, `close.diff()` is always +10, so OBV = `volume * sign(close_diff)` = 100 * 1 summed cumulatively = `100, 200, 300, 400, 500` for the first 5 bars. With `y = [100, 200, 300, 400, 500]`, the slope is exactly 100.0, and normalized ratio is `100 / 300 = 0.333 > 0.01 → 1.0` (rising).

#### C. Step Function (trigger crossovers)

```python
half = N // 2
close = np.concatenate([np.full(half, 50000.0), np.full(N - half, 50100.0)])
```

Creates a sharp price jump at `half`. This forces a MACD cross and tests crossover detection:
- MACD starts below signal → jumps above → produces `bullish_cross` at a known index.

#### D. Sinusoidal (test OBV slope reversal)

```python
t = np.linspace(0, 2 * np.pi, N)
close = 50000.0 + 500.0 * np.sin(t)
```

Tests:
- OBV slope cycling through rising → falling → flat
- MACD cross at inflection points

---

## 3. Edge Cases Per Component

### 3.1 `normalize_symbol(symbol: str) -> str`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Normal pair | `"BTC/USDT"` | `"BTCUSDT"` |
| 2 | No slash | `"BTCUSDT"` | `"BTCUSDT"` |
| 3 | Empty | `""` | `""` |
| 4 | Multiple slashes | `"BTC/USDT/ABC"` | `"BTCUSDTABC"` |

### 3.2 `last_valid(series: pd.Series) -> float`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | All valid | `[1.0, 2.0, 3.0]` | `3.0` |
| 2 | Trailing NaN | `[1.0, 2.0, NaN]` | `2.0` |
| 3 | All NaN | `[NaN, NaN]` | `NaN` |
| 4 | Single element | `[42.0]` | `42.0` |
| 5 | Single NaN | `[NaN]` | `NaN` |
| 6 | Leading NaN | `[NaN, 5.0]` | `5.0` |

### 3.3 `_macd_cross(macd, signal)`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Bullish crossover | MACD=[-1, -0.5, 0.5, 1], Signal=[0, 0, 0, 0] | `["none","none","bullish_cross","none"]` |
| 2 | Bearish crossover | MACD=[1, 0.5, -0.5, -1], Signal=[0, 0, 0, 0] | `["none","none","bearish_cross","none"]` |
| 3 | No crossover | MACD=[1, 2, 3], Signal=[0, 0, 0] | `["none","none","none"]` |
| 4 | NaN in middle | MACD=[1, NaN, 3], Signal=[0, 0, 0] | NaN row skipped |
| 5 | All NaN | All NaN | All "none" |
| 6 | Single row | Only 1 data point | All "none" |
| 7 | MACD above at bar 0, crosses at bar 1 | MACD=[-1, 1], Signal=[0, 0] | `["none","bullish_cross"]` |
| 8 | Equal values (touching but no cross) | MACD=[0, 0], Signal=[0, 0] | m_prev <= s_prev and m_curr > s_curr → false; m_prev >= s_prev and m_curr < s_curr → false; → "none" at both |

**Note for case 8:** The condition `m_prev <= s_prev` with equality and `m_curr > s_curr` would be false since `m_curr == s_curr`. Similarly for bearish. So equal touching is correctly "none".

### 3.4 `_obv_slope(obv)`

| # | Case | Input (5-bar window) | Expected |
|---|------|----------------------|----------|
| 1 | Rising | `[0, 1, 2, 3, 4]` | `1.0` |
| 2 | Falling | `[4, 3, 2, 1, 0]` | `-1.0` |
| 3 | Flat | `[10, 10, 10, 10, 10]` | `0.0` |
| 4 | Low magnitude (below threshold) | `[100, 100, 100, 100, 100.5]` | `0.0` (ratio < 0.01) |
| 5 | All zero | `[0, 0, 0, 0, 0]` | NaN (mean=0 → skip) |
| 6 | NaN in window | `[1, NaN, 3, 4, 5]` | NaN |
| 7 | Insufficient data | 4 rows | All NaN |
| 8 | Very large values | `[1e6, 2e6, 3e6, 4e6, 5e6]` | `1.0` |
| 9 | Ratio exactly at threshold | slope/mean = 0.01 | `0.0` (not `> 0.01`) |

**Verification for case 1:** y=[0,1,2,3,4], μ=2, slope=(-2*(-2)+-1*(-1)+0+1*(1)+2*(2))/10=10/10=1.0, ratio=1.0/2.0=0.5 → 1.0 ✓

### 3.5 `_price_vs_bb(close, upper, lower)`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Above upper | close=110, upper=100, lower=80 | `"above_upper"` |
| 2 | Below lower | close=70, upper=100, lower=80 | `"below_lower"` |
| 3 | Inside | close=90, upper=100, lower=80 | `"inside"` |
| 4 | Exactly on upper | close=100, upper=100, lower=80 | `"inside"` (not >) |
| 5 | Exactly on lower | close=80, upper=100, lower=80 | `"inside"` (not <) |
| 6 | All NaN | close=NaN, upper=100, lower=80 | `"none"` |
| 7 | Any NaN | Any parameter NaN | `"none"` |

### 3.6 `ema_signal(close_price, ema)`

| # | Case | Input | Expected suffix |
|---|------|-------|-----------------|
| 1 | Price above | close=110, ema=100 | `"above (+10.0%)"` |
| 2 | Price below | close=90, ema=100 | `"below (-10.0%)"` |
| 3 | At (within 0.1%) | close=100.05, ema=100 | `"at (+0.0%)"` |
| 4 | Exactly equal | close=100, ema=100 | `"at (+0.0%)"` |
| 5 | Zero EMA | close=1, ema=0 | Division by zero → `"above (+inf%)"` (or NaN string) |

### 3.7 `macd_signal_label(macd, signal, hist)`

| # | Case | Input (last 2 valid rows) | Expected prefix |
|---|------|---------------------------|-----------------|
| 1 | Bullish crossover | prev: m=-1, s=0; curr: m=1, s=0 | `"bullish crossover —"` |
| 2 | Bearish crossover | prev: m=1, s=0; curr: m=-1, s=0 | `"bearish crossover —"` |
| 3 | Bullish widening | curr: m=2 > s=1, hist=+1; prev: hist=+0.5; abs(curr) > abs(prev) | `"bullish (widening) —"` |
| 4 | Bullish converging | curr: m=2 > s=1, hist=+0.5; prev: hist=+1; abs(curr) < abs(prev) | `"bullish (converging) —"` |
| 5 | Bearish widening | curr: m=0.5 < s=1, hist=-0.8; prev: hist=-0.3; abs(curr) > abs(prev) | `"bearish (widening) —"` |
| 6 | Bearish converging | curr: m=0.5 < s=1, hist=-0.3; prev: hist=-0.8; abs(curr) < abs(prev) | `"bearish (converging) —"` |
| 7 | Insufficient data | 0 or 1 valid row | `"insufficient data"` |
| 8 | All NaN | All NaN | `"insufficient data"` |
| 9 | Zero hist | curr: m=2 > s=1, hist=0 | `"bullish (converging) —"` (abs(0) < abs(0.5)) |

### 3.8 `rsi_label(rsi)`

| # | Input | Expected zone |
|---|-------|---------------|
| 1 | 29.9 | `"oversold"` |
| 2 | 30.0 | `"bearish"` |
| 3 | 39.9 | `"bearish"` |
| 4 | 40.0 | `"neutral-bearish"` |
| 5 | 49.9 | `"neutral-bearish"` |
| 6 | 50.0 | `"neutral-bullish"` |
| 7 | 59.9 | `"neutral-bullish"` |
| 8 | 60.0 | `"bullish"` |
| 9 | 69.9 | `"bullish"` |
| 10 | 70.0 | `"overbought"` |
| 11 | NaN | Python `NaN < 30` is `False` → falls through to "overbought" **— BUG** |

**Critical finding:** `rsi_label` does NOT handle NaN. `float("nan") < 30` evaluates to `False`, so NaN RSI falls through to the final `else` and returns `"overbought"`. This applies to `mfi_signal` as well. The print wrapper in `print_timeframe_block` catches this for RSI (line 474: `if not math.isnan(rsi_val) else "insufficient data"`), but the function itself is not NaN-safe. **This is a latent bug** if `rsi_label` or `mfi_signal` are called directly without the guard.

### 3.9 `mfi_signal(mfi)`

| # | Input | Expected zone |
|---|-------|---------------|
| 1 | 19.9 | `"oversold"` |
| 2 | 20.0 | `"bearish"` |
| 3 | 39.9 | `"bearish"` |
| 4 | 40.0 | `"neutral-bearish"` |
| 5 | 49.9 | `"neutral-bearish"` |
| 6 | 50.0 | `"neutral-bullish"` |
| 7 | 59.9 | `"neutral-bullish"` |
| 8 | 60.0 | `"bullish"` |
| 9 | 79.9 | `"bullish"` |
| 10 | 80.0 | `"overbought"` |
| 11 | NaN | `math.isnan(mfi)` returns True → `"insufficient data"` |

Note: `mfi_signal` IS NaN-safe (uses `math.isnan`), unlike `rsi_label`.

### 3.10 `obv_signal(df)`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Uptrend | OBV = [100, 110, 120, 130, 140] (last 2 mean=135, prior 3 mean=110, change=22.7%) | `"confirming uptrend"` |
| 2 | Downtrend | OBV = [140, 130, 120, 110, 100] (last 2 mean=105, prior 3 mean=130, change=-19.2%) | `"confirming downtrend"` |
| 3 | Neutral | OBV = [100, 101, 102, 103, 104] (last 2 mean=103.5, prior 3 mean=101, change=2.5%) | `"neutral / choppy"` |
| 4 | Boundary (exactly 5%) | Change = 5.0% | `"confirming uptrend"` (>= 5.0) |
| 5 | Boundary (exactly -5%) | Change = -5.0% | `"confirming downtrend"` |
| 6 | Insufficient data | OBV has 4 non-NaN values | `"insufficient data"` |
| 7 | Large values with small change | OBV = [1e9, 1e9, 1e9, 1e9+1, 1e9+1] | Depends on denominator (max) |

**Note on denominator:** `denominator = max(abs(mean_prior_3), 1.0)`. For near-zero prior means, this avoids division by zero.

### 3.11 `bb_label(close_price, upper, mid, lower, upper_arr, lower_arr)`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Near upper | close=99, upper=100, lower=0, pct_b=0.99 | `"near upper band..."` |
| 2 | Near lower | close=1, upper=100, lower=0, pct_b=0.01 | `"near lower band..."` |
| 3 | Mid-range | close=50, upper=100, lower=0, pct_b=0.5 | `"mid-range..."` |
| 4 | Upper half | close=80, upper=100, lower=0, pct_b=0.8 | `"upper half..."` |
| 5 | Lower half | close=20, upper=100, lower=0, pct_b=0.2 | `"lower half..."` |
| 6 | Band width zero | upper=100, lower=100 | `"band width zero"` |
| 7 | Squeeze | 10 prev widths = 10.0, current = 8.0 (0.8x < 0.9) | `"...bands squeezing"` |
| 8 | Expansion | 10 prev widths = 10.0, current = 12.0 (1.2x > 1.1) | `"...bands expanding"` |
| 9 | No squeeze | 10 prev widths = 10.0, current = 10.5 (1.05x, within 0.9-1.1) | No expansion suffix |
| 10 | Insufficient squeeze data | < 11 valid BB rows | No expansion suffix |
| 11 | Boundary pct_b = 0.95 | close=95, upper=100, lower=0, pct_b=0.95 | `"near upper band..."` (>= 0.95) |
| 12 | Boundary pct_b = 0.05 | close=5, upper=100, lower=0, pct_b=0.05 | `"near lower band..."` (<= 0.05) |
| 13 | Boundary pct_b = 0.4 | close=40, upper=100, lower=0 | `"lower half"` (pct_b=0.4, not between 0.4-0.6 inclusive) |
| 14 | Boundary pct_b = 0.6 | close=60, upper=100, lower=0 | `"upper half"` (pct_b=0.6, not between 0.4-0.6 inclusive) |

### 3.12 `load_csv(path)`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Well-formed, sorted | timestamps ascending | Same ordering |
| 2 | Unsorted | timestamps random | Sorted ascending |
| 3 | Duplicate timestamps | 2 rows with same timestamp, different values | Keep last |
| 4 | Empty file | Just header or totally empty | Empty DataFrame (columns parsed) |
| 5 | Missing `timestamp` column | No timestamp column | KeyError |
| 6 | Extra columns | Has extra columns beyond OHLCV | Ignores extras |
| 7 | Invalid timestamp format | Can't parse | `pd.read_csv` failure |

### 3.13 `load_ta_latest(...)`

| # | Case | Input file | Expected |
|---|------|------------|----------|
| 1 | Normal file | Complete TA CSV with all columns | Dict with all 5 keys |
| 2 | Missing file | File doesn't exist | `None` |
| 3 | Empty file | Empty CSV | `None` (catches EmptyDataError) |
| 4 | mfi14 = None | mfi14 column is NaN | `None` |
| 5 | obv = None | obv column is NaN | `None` |
| 6 | obv_slope = empty string | obv_slope="" | Returns 0.0 |
| 7 | obv_slope = "none" | obv_slope="none" | Returns 0.0 |
| 8 | obv_slope = valid float | obv_slope=1.0 | Returns 1.0 |
| 9 | Missing columns | CSV without mfi14/obv | `None` (KeyError caught) |
| 10 | close = None | close column NaN | Returns None in dict |

### 3.14 `load_ta_series(...)`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Normal | tail=3, 10 rows CSV | 3 rows |
| 2 | tail > len(df) | tail=100, 10 rows CSV | 10 rows |
| 3 | Missing file | File doesn't exist | `None` |
| 4 | Empty file | Empty CSV | `None` |
| 5 | Timestamp parse | String timestamps | `datetime` dtype |
| 6 | Malformed | Corrupt CSV | `None` |

### 3.15 `compute_indicators(df)`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Normal (35 rows, linear trend) | Synthetic OHLCV | All 23 output columns present |
| 2 | Insufficient data (5 rows) | Short DataFrame | All indicator columns NaN |
| 3 | Single row | 1-row DataFrame | No crash, all NaN |
| 4 | Constant price | All close = 50000 | BB mid = 50000, MACD = 0, RSI = NaN (no change), OBV = 0 |
| 5 | Zero volume | Volume = 0 | MFI = NaN (division by zero), OBV = 0 |
| 6 | BB ordering invariant | Any valid input | `bb_upper >= bb_mid >= bb_lower` for all rows where BB is valid |
| 7 | MACD identity invariant | Any valid input | `macd_hist == macd - macd_signal` for all rows where both are valid |

**The 23 output columns:**
`timestamp, open, high, low, close, volume, ema21, macd, macd_hist, macd_signal, rsi14, bb_upper, bb_mid, bb_lower, mfi14, obv, ebsw, atr14, macd_cross, ema21_slope, price_vs_bb, bb_width, obv_slope`

### 3.16 `save_enriched_csv(df, out_path)`

| # | Case | Input | Expected |
|---|------|-------|----------|
| 1 | Normal write | Valid DataFrame | File created at path, 23 columns |
| 2 | Atomic rename | Concurrent writes | No corruption (file has complete rows) |
| 3 | No orphan temp files | Normal completion | No `.tmp` files left behind |
| 4 | Error recovery | Write failure simulated | No orphan temp file |
| 5 | NaN values | DataFrame with NaN | Empty string in CSV for NaN cells |
| 6 | Column order | DataFrame with correct columns | Output CSV has exact fieldnames order |

### 3.17 `print_timeframe_block(timeframe, df)`

Snapshot test via `capsys` — assert that specific string patterns appear in stdout.

| # | Case | Expected stdout contains |
|---|------|-------------------------|
| 1 | Normal DataFrame | Timeframe label, "EMA21", "MACD", "RSI14", "BB", etc. |
| 2 | All-NaN DataFrame | "insufficient data" multiple times |

### 3.18 `analyze_timeframe(symbol, timeframe, data_dir)`

| # | Case | Expected |
|---|-------|----------|
| 1 | Normal | Returns True, output CSV created |
| 2 | Missing input CSV | Returns False, warning printed to stderr |
| 3 | Output CSV has correct columns | All 23 expected columns |

### 3.19 `main()` / `parse_args()`

| # | Case | Expected |
|---|------|----------|
| 1 | Default (no --timeframe) | Runs 3 timeframes (1d, 4h, 1h) |
| 2 | Single timeframe | Runs only specified TF |
| 3 | Custom data-dir | Uses overridden path |
| 4 | No symbol | argparse error |

---

## 4. Test File Recommendation

**Recommendation: Option A — `tests/test_analyze_ta.py`**

Rationale (in order of weight):

1. **Existing convention** — All 3 existing test files (`test_causality.py`, `test_structure_engine.py`, `test_streaming_vs_batch.py`) live in `tests/`. Consistency matters more than colocation.

2. **No conftest.py yet** — A `conftest.py` in `tests/` can declare the `synthetic_ohlcv` fixture once and have it shared across all test functions. No need for duplication or imports across directories.

3. **Separation of concerns** — `trade_scripts/` is for runtime-executable scripts. Tests are developer-only artifacts. Keeping them in `tests/` makes it easy to exclude them from production deployments.

4. **No pytest infrastructure needed** — The existing `test_structure_engine.py` already imports pytest and uses plain functions + assert. Tests can be run with `uv run pytest tests/test_analyze_ta.py` once pytest is installed.

**What to create:**

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Shared fixtures: `synthetic_ohlcv()`, temp dir, symbol configs |
| `tests/test_analyze_ta.py` | All test functions organized by component |

**Optional (later):**
| `tests/test_data/analyze_ta_fixtures.csv` | Only if pre-computed indicator values are needed for snapshot testing |

**Trade-off acknowledged:** If `trade_scripts/` is later split into a separate package, the tests would need to move too. But that's a future concern — cross that bridge when the split happens.

---

## 5. Effort Estimate

**Overall: Medium (1–2 days)**

Breakdown:

| Phase | Tasks | Est. Time |
|-------|-------|-----------|
| Setup | `conftest.py` with `synthetic_ohlcv` fixture, temp dir fixture, import path | 30 min |
| Unit tests (P0) | `_macd_cross`, `_obv_slope`, `_price_vs_bb`, `macd_signal_label` (~18 tests) | 2h |
| Unit tests (P1-P2) | `normalize_symbol`, `last_valid`, `ema_signal`, `rsi_label`, `mfi_signal`, `obv_signal`, `bb_label` (~18 tests) | 1.5h |
| Integration tests | `load_csv`, `load_ta_latest`, `load_ta_series`, `save_enriched_csv` (~15 tests) | 2h |
| Integration (P0) | `compute_indicators` — column labels, BB/MACD invariants, known values (~8 tests) | 1.5h |
| System tests | `analyze_timeframe`, `print_timeframe_block` (~4 tests) | 1h |
| CLI | `main`/`parse_args` (~4 tests) | 30 min |
| Review + polish | Edge case review, documentation, run full suite | 30 min |
| **Total** | **~60 tests** | **~8–10h** |

### Bottlenecks

1. **`compute_indicators` known-value tests** — Requires understanding pandas-ta's exact output to assert precise values. May need to run once to capture actual output, then assert against that snapshot.

2. **`bb_label` squeeze/expansion** — Requires constructing a Series with enough valid BB rows (≥ 11) where widths are known. This is doable with synthetic data but requires precise construction.

3. **Concurrent write test for `save_enriched_csv`** — Requires `multiprocessing` or `threading` in tests. Existing post-fix test used concurrent `uv run`, but a unit test version needs careful orchestration.

---

## 6. Handoff to Prometheus

Next step: generate the actual test plan at `.sisyphus/plans/prometheus-test-plan-analyze-ta.md` with:

1. **Exact test structure** — Test classes/functions with docstrings, pytest markers
2. **conftest.py design** — Fixture factories for synthetic data, temp paths, parametrized scenarios
3. **Implementation order** — Path dependency graph (which tests to write first)
4. **Coverage target** — Line and branch coverage thresholds (target: ≥ 90% for P0 functions)
5. **Continuous integration** — How to integrate with existing test runner (uv/CI)

**Key findings to incorporate:**

- The `rsi_label` function is NOT NaN-safe — NaN falls through to "overbought". The caller in `print_timeframe_block` guards against this, but the function itself is a trap for future callers. Flag this as a fix candidate during testing.
- The `load_ta_series` function does NOT use `parse_dates=["timestamp"]` in its `pd.read_csv` call (line 171). The post-fix review noted this as a minor consistency issue. Consider adding it during test development.
- No pytest configuration exists yet in `pyproject.toml` — add `[tool.pytest.ini_options]` section.

---

*Report generated by Oracle. Handing off to Prometheus for test plan generation.*
