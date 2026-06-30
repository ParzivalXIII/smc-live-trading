# Oracle Bug Investigation: `analyze_ta.py` — Two Live-Robustness Bugs

**Investigator:** Oracle (Strategic Technical Advisor)
**Date:** 2026-06-24
**Scope:** `trade_scripts/analyze_ta.py` — `compute_indicators()` and `load_ta_latest()`
**Sources:** Direct code analysis, `pandas_ta` behavioral tests, test suite evidence

---

## Bug 1: `compute_indicators()` crashes on <40 rows

### Indicator Minimum Row Requirements

All `ta.*` functions return `None` when given a Series shorter than their minimum period. The code only crashes when it tries to call `.iloc[:, N]` on that `None` object (lines 191–193, 201–203).

| Line | Call | Min rows required | Returns `None`? | Crashes? |
|------|------|-------------------|-----------------|----------|
| 186 | `ta.ema(close, length=21)` | **21** | Yes, if <21 | No — `df["col"] = None` creates NaN column |
| 189 | `ta.macd(close, fast=12, slow=26, signal=9)` | **34** | Yes, if <34 | **Yes** — `.iloc[:, 0]` on None (line 191) |
| 196 | `ta.rsi(close, length=14)` | **15** | Yes, if <15 | No — `df["col"] = None` creates NaN column |
| 199 | `ta.bbands(close, length=20, std=2)` | **20** | Yes, if <20 | **Yes** — `.iloc[:, 2]` on None (line 201) |
| 206 | `ta.mfi(high, low, close, volume, length=14)` | **15** | Yes, if <15 | No — `df["col"] = None` creates NaN column |
| 209 | `ta.obv(close, volume)` | **0** | Never (≥1 row) | No — cumulative, no period |
| 212 | `ta.ebsw(close)` | **40** | Yes, if <40 | No — `df["col"] = None` creates NaN column |
| 215 | `ta.atr(high, low, close, length=14)` | **15** | Yes, if <15 | No — `df["col"] = None` creates NaN column |

**Maximum period: 40** (EBSW — Ehler's Bandpass Super Smoother Wave). This is the minimum safe row count for all indicators to produce non-NaN values.

### Crash Path

```
compute_indicators(df_with_30_rows)
  → ta.ema(close, 21)       → OK (30 ≥ 21)        → df["ema21"] = Series
  → ta.macd(close, 12,26,9) → None (30 < 34)      → *** CRASH ***
  →   macd_df.iloc[:, 0]    → AttributeError: 'NoneType' object has no attribute 'iloc'
```

The identical crash would occur at line 201 if `bbands` returned `None` first (which would happen with <20 rows before MACD's threshold of 34).

Scenarios that trigger this in live production:
- **Startup with limited history** — first 25–39 bars after system launch
- **Broken exchange response** — exchange returns partial data (e.g., rate-limited response with only recent candles)
- **Low-volume pairs** — some pairs may not have 40+ hourly candles
- **Network interruption during data fetch** — gap in historical data

### Test Coverage

`tests/test_analyze_ta_core.py` lines 40–58 explicitly tests the crash:
- `test_insufficient_data_5_rows` — expects `AttributeError` (line 47: `pytest.raises(AttributeError)`)
- `test_single_row` — expects `AttributeError` (line 57: `pytest.raises(AttributeError)`)

The `constant_price_df` fixture uses 100 rows (well above all thresholds), so the constant-price test never encounters the issue.

These tests are **documented as "Known Production Code Limitations"** in `.sisyphus/evidence/unified reports/analyze-ta-imp.txt` line 170.

### Fix Recommendation

**Add an early-exit guard at the top of `compute_indicators()` that initializes all indicator columns with NaN and returns early.**

**Location:** `trade_scripts/analyze_ta.py`, line 180–184 (before the first `ta.*` call)

```python
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all TA indicators using pandas-ta and return enriched DataFrame.
    """
    # ── Guard: insufficient data for any indicator ──
    MIN_ROWS = 40  # EBSW requires 40 rows (highest period)
    if len(df) < MIN_ROWS:
        # Initialize all derived columns as NaN to maintain column contract.
        # Downstream code (print_timeframe_block, save_enriched_csv)
        # expects these columns to exist and handles NaN gracefully.
        for col in ["ema21", "macd", "macd_hist", "macd_signal", "rsi14",
                     "bb_upper", "bb_mid", "bb_lower", "mfi14", "ebsw",
                     "atr14", "ema21_slope", "bb_width"]:
            df[col] = np.nan
        # OBV works with any row count (cumulative, no period)
        df["obv"] = ta.obv(df["close"], df["volume"])
        # Derived string columns
        df["macd_cross"] = "none"
        df["price_vs_bb"] = "none"
        # OBV slope (derived from OBV, which we just computed)
        df["obv_slope"] = 0.0
        # BB width (all NaN since BB columns are NaN)
        return df

    # ... rest of existing code unchanged ...
```

**Why this approach:**
- Single guard point — catches ALL cases at once, not just the two `.iloc` crashes
- Maintains column contract — downstream code always finds all 23 expected columns
- Downstream code already handles NaN everywhere (`math.isnan()` guards, `last_valid()` returns NaN, `_macd_cross` skips NaN rows, `_price_vs_bb` returns "none" for NaN)
- OBV is still computed (it works with any row count)

**Alternative considered — guard each `ta.*` call individually:**
More granular but requires 6–8 separate None checks + NaN defaults, making the code harder to read. Rejected in favor of the single guard.

**Tests to update:**
- `test_insufficient_data_5_rows` — change from `pytest.raises(AttributeError)` to asserting NaN-filled columns returned
- `test_single_row` — same change
- Add test: `test_39_rows_returns_nan_filled_dataframe` to verify the exact boundary

**Effort:** `Short` (1–4h) — guard code, test changes, run full suite

---

## Bug 2: `load_ta_latest()` crashes on empty CSV

### Crash Path

```
load_ta_latest("BTC/USDT", "1h", "data")
  → path.exists()           → True (file with header only)
  → pd.read_csv(path)       → returns DataFrame with 0 rows (columns exist)
  → df.iloc[-1]             → IndexError: single positional indexer is out-of-bounds
  → try/except (EmptyDataError, KeyError, ValueError)
    → IndexError NOT caught → propagates to caller → CRASH
```

**Two distinct empty-file scenarios:**

| Scenario | File content | `pd.read_csv()` | Crash? |
|----------|-------------|------------------|--------|
| Truly empty | 0 bytes | Raises `pd.errors.EmptyDataError` | No — caught by existing except (line 135) |
| Header-only | `"timestamp,open,...\n"` | Returns empty DataFrame | **Yes** — `IndexError` on `.iloc[-1]` |

The header-only case is the dangerous one: it's a valid CSV with a complete header (produced by a system that writes the header but hasn't yet written data rows), so `path.exists()` passes and `pd.read_csv()` succeeds.

### Why the existing try/except doesn't catch it

Line 135: `except (pd.errors.EmptyDataError, KeyError, ValueError):`

`IndexError` is **not** in the caught tuple. Python's exception handling only catches explicitly listed types.

### How a header-only CSV is created in practice

- `save_enriched_csv()` runs atomic write via `tempfile.mkstemp()` + `shutil.move()`. If the write is interrupted after the header but before data rows, the final CSV will be header-only.
- A race condition: another process creates the CSV with a header but hasn't written data yet.
- Manual or automated truncation: a CSV file that has been truncated to remove data rows but retained the header.

### Test Coverage

`tests/test_analyze_ta_io.py` line 163–168 explicitly tests this:
```python
def test_empty_file(self, tmp_csv_dir):
    """Empty CSV (header only) — IndexError (known limitation: empty rows not handled)."""
    path.write_text("timestamp,open,high,low,close,volume\n")
    with pytest.raises(IndexError):
        load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
```

This is documented in `.sisyphus/evidence/unified reports/analyze-ta-imp.txt` line 171 as a known limitation.

### Fix Recommendation

**Add an `if df.empty: return None` guard immediately after `pd.read_csv()`.**

**Location:** `trade_scripts/analyze_ta.py`, lines 114–116

```python
    try:
        df = pd.read_csv(path)
        if df.empty:             # ← new guard (header-only CSV, zero data rows)
            return None
        last = df.iloc[-1]       # ← was line 116, now safe
        ...
```

**Why this approach:**
- Single line, zero ripple effects
- Handles both truly-empty (though that's already caught by `EmptyDataError`) and header-only cases
- Consistent with the function's contract: returns `None` when data isn't available

**Edge case: `load_ta_series()` (lines 139–174) has the same empty-file vulnerability.** It calls `df.tail(tail)` which safely returns an empty DataFrame on empty input. This is documented as a "known limitation" (line 172 in the imp report) but doesn't crash. No change needed for the crash fix, but it's worth noting.

**Tests to update:**
- `test_empty_file` — change from `pytest.raises(IndexError)` to `assert result is None`

**Effort:** `Quick` (<1h) — one-line fix, one test update, run suite

---

## Action Plan

| # | Fix | File | Lines | Effort | Dependencies |
|---|-----|------|-------|--------|-------------|
| 1 | **Bug 2**: Add `if df.empty: return None` in `load_ta_latest()` | `analyze_ta.py` | 114–116 | `Quick` (<1h) | None |
| 2 | **Bug 1**: Add early-exit guard in `compute_indicators()` with `MIN_ROWS = 40` | `analyze_ta.py` | 180–184 | `Short` (1–4h) | None |
| 3 | Update `test_insufficient_data_5_rows` and `test_single_row` to expect NaN columns, not `AttributeError` | `test_analyze_ta_core.py` | 40–58 | Tied to #2 | #2 |
| 4 | Update `test_empty_file` in `TestLoadTaLatest` to expect `None`, not `IndexError` | `test_analyze_ta_io.py` | 163–168 | `Quick` | #1 |
| 5 | Run full 181-test suite to confirm no regressions | — | — | `Quick` | #1–4 |

**Total effort:** `Short` (2–4h)

---

## Optional Future Considerations

1. **`load_ta_series()`** (line 139) — Returns empty DataFrame for header-only CSV instead of `None`. Doesn't crash, but producing an empty DataFrame silently may confuse callers. Worth aligning with `load_ta_latest()` in a future cleanup pass.

2. **Derived-column NaN handling in `print_timeframe_block()`** — If `compute_indicators()` returns NaN-filled columns (post-fix), `print_timeframe_block()` accesses `.iloc[-1]` on those columns which is safe (NaN). All label functions have `math.isnan()` guards. Already correct, no change needed.
