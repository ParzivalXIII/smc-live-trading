# Oracle Review: `analyze_ta.py`

**File:** `trade-scripts/analyze_ta.py` (619 lines)
**Reviewed:** 2026-06-23
**Scope:** Bollinger column naming, timestamp sorting/dedup, batch enrichment architecture, general code quality

---

## Bottom Line

The script is functional for its primary use case (offline batch enrichment + stdout digest) but has **four blocking issues** that make it unfit for live snapshot support without remediation. The most critical are: (1) `pandas_ta` is not declared as a project dependency in `pyproject.toml` — the import will fail in a fresh environment; (2) `load_csv()` does not sort or deduplicate by timestamp, so all indicator computations operate on potentially out-of-order data; (3) all load functions have no protection against partial writes from concurrent processes; (4) Bollinger Band column names are version-fragile. These are all fixable with `Quick`/`Short` effort, but must be addressed before any live layer depends on the enriched CSV output.

---

## Issue 1: Bollinger Band Column Names

### Current Names

| Column | Current Name |
|--------|-------------|
| Upper | `BBU_20_2.0_2.0` |
| Mid   | `BBM_20_2.0_2.0` |
| Lower | `BBL_20_2.0_2.0` |

The suffix `20_2.0_2.0` encodes `{length}_{std}_{ddof}`. The second `2.0` is the `ddof` (delta degrees of freedom) parameter that `pandas_ta` defaults to `2.0` for sample standard deviation in some versions, and `0` for population standard deviation in others.

### Fragility Assessment

**High.** The column names are parameter-encoded strings. Any of the following silently break extraction:

1. **`pandas_ta` version change** — If a future version alters the default `ddof` from `2.0` to `1.0` or `0`, every column name changes without warning. The script silently gets `KeyError` at startup.
2. **Parameter value change** — If someone changes `length=20` to `length=30` or `std=2` to `std=2.5`, column names change.
3. **Same issue exists for MACD** — `ta.macd()` columns use `MACD_12_26_9`, `MACDs_12_26_9`, `MACDh_12_26_9`. Same fragility if parameters change.

### Recommended Fix

Access BB columns by **position** rather than parameter-encoded name. `pandas_ta.bbands()` returns a DataFrame with exactly 3 columns in order: lower, mid, upper (for most versions; verify once).

```python
bb_df = ta.bbands(df["close"], length=20, std=2)
df["bb_upper"] = bb_df.iloc[:, 2]   # 3rd column: upper band
df["bb_mid"]   = bb_df.iloc[:, 1]   # 2nd column: middle band
df["bb_lower"] = bb_df.iloc[:, 0]   # 1st column: lower band
```

This is version-agnostic as long as the column count (3) and order remain stable — which is far more likely than parameter-encoded naming.

**Alternative** (if position order feels opaque): Match by substring or prefix:

```python
bb_df = ta.bbands(df["close"], length=20, std=2)
for col in bb_df.columns:
    if col.startswith("BBU_"): df["bb_upper"] = bb_df[col]
    elif col.startswith("BBM_"): df["bb_mid"] = bb_df[col]
    elif col.startswith("BBL_"): df["bb_lower"] = bb_df[col]
```

**Effort:** `Quick` (< 1h — change 3 lines, plus MACD for consistency)

---

## Issue 2: Timestamp Sorting and Deduplication

### Current State

`load_csv()` (line 74-77):
```python
def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    return df
```

Zero ordering guarantees. The CSV could contain rows in any order (append-based writes, concurrent modification, out-of-order data source).

### Downstream Impact

Indicator computations in `compute_indicators()` are **all order-sensitive**:

| Function | Depends On | Breaks If Unsorted |
|----------|-----------|-------------------|
| `ta.ema()` | Sequential close prices | Yes |
| `ta.macd()` | Sequential close prices | Yes |
| `ta.rsi()` | Sequential close prices | Yes |
| `ta.bbands()` | Sequential close prices | Yes (window-based) |
| `ta.mfi()` | Sequential OHLCV | Yes |
| `ta.obv()` | Sequential close, volume | Yes |
| `ta.ebsw()` | Sequential close | Yes |
| `ta.atr()` | Sequential OHLC | Yes |
| `_macd_cross()` | Adjacent row comparison | Yes |
| `_obv_slope()` | 5-row rolling window | Yes |
| `df["ema21"].pct_change()` | `shift(1)` | Yes |
| `_price_vs_bb()` | Row-wise (less affected) | No |

**Risk: Severe.** Every derived indicator is silently wrong on shuffled data.

### Duplicate Risk

Duplicate timestamps produce:
- Incorrect rolling computations (same bar counted twice)
- Incorrect `iloc[-1]` in `load_ta_latest()` — might read a stale duplicate as "latest"
- Incorrect `last_valid()` value

### Where to Add Fix

**Location:** Inside `load_csv()` — single responsibility, consistent for all callers.

**What to add:**
1. `df.sort_values("timestamp", inplace=True)` — ensure time order
2. `df.drop_duplicates(subset="timestamp", keep="last", inplace=True)` — dedup, keep latest
3. Optionally: `df["timestamp"].is_monotonic_increasing` check with a warning if violated

```python
def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df.sort_values("timestamp", inplace=True)
    df.drop_duplicates(subset="timestamp", keep="last", inplace=True)
    if not df["timestamp"].is_monotonic_increasing:
        # This should not happen after sort, but guard against NaN timestamps
        pass
    df.reset_index(drop=True, inplace=True)
    return df
```

The `reset_index(drop=True)` is important because `iloc`-based operations in `_macd_cross` and elsewhere assume a clean 0-based index.

**Effort:** `Quick` (< 1h — 4 lines in `load_csv`)

---

## Issue 3: Batch Enrichment Architecture

### 3a. `load_ta_latest()` — Concurrent Write Race

**Risk: High.** If the enriched CSV is being written by another process (or the same process in async context) while `load_ta_latest()` reads it:

1. `pd.read_csv(path)` on a partially-written file succeeds but returns a truncated DataFrame
2. `df.iloc[-1]` returns whichever row was flushed to disk last — likely a stale or partial row
3. The old data is silently served as "latest"

The try/except catches `pd.errors.EmptyDataError` (completely empty file) but not `pd.errors.ParserError` (truncated row) or silent truncation (incomplete but parsable).

**Mitigation options:**
- Write to a temp file, then atomic rename: `write to temp.csv → os.rename(temp.csv, dest.csv)`. This is the standard solution. Requires changing `save_enriched_csv()`.
- Or: add a row count check — if the enriched CSV has fewer rows than the source CSV, warn/retry.
- For reads: add a "recency" check — if the latest timestamp is older than expected, return `None`.

### 3b. `load_ta_latest()` — Empty/Malformed CSV

The try/except catches:
- `pd.errors.EmptyDataError` — empty file → returns `None` ✓
- `KeyError` — missing column → returns `None` ✓
- `ValueError` — parsing failure → returns `None` ✓

**Not caught:** `pd.errors.ParserError`, `OSError`, `PermissionError`, `UnicodeDecodeError`.

**Risk:** Low. These are rare and would crash the caller. A broader `except Exception` (or specific additions) would be safer for a live path.

### 3c. `obv_slope` Parsing — Robustness

The chain:
```python
obv_slope_raw = last.get("obv_slope")
obv_slope = float(obv_slope_raw) if obv_slope_raw not in (None, "", "none") and pd.notna(obv_slope_raw) else 0.0
```

**Assessment: Defensive but not exhaustive.**
- Handles: `None`, `""` (empty string from CSV), `"none"` (str value written by `save_enriched_csv`), `NaN`
- Does **not** handle: whitespace strings like `" "` or `" none"`, non-numeric garbage (will raise `ValueError` from `float()`)
- The string `"none"` check is because `save_enriched_csv` writes `str(row.get("obv_slope", ""))` which produces the string `"none"` for NaN values and `"0.0"` or `"1.0"` or `"-1.0"` for valid values

**Recommended simplification:**
```python
obv_slope_raw = last.get("obv_slope", "")
try:
    obv_slope = float(obv_slope_raw)
except (ValueError, TypeError):
    obv_slope = 0.0
```

This handles all edge cases in 3 lines instead of a complex boolean chain.

### 3d. Strictness on `mfi14`/`obv` being `None`

The function returns `None` if either `mfi14` or `obv` is `None` (line 119-120):
```python
if mfi14 is None or obv is None:
    return None
```

**Assessment: Reasonable.** The documented return type is `dict | None`, and callers must handle `None` anyway. If a caller needs partial data (e.g., close price is available even when MFI isn't), they can call the function differently. This strictness is correct for a live snapshot consumer — partial data is better signalled as "unavailable" than silently served.

**Note:** `close` being `None` does **not** trigger the early return. If close is NaN but mfi14 and obv happen to be present (unlikely but possible), the dict would contain `"close": None`. This is a minor inconsistency — if close is unreadable, the row is likely corrupt and should also trigger `None`.

### 3e. `load_ta_series()` — Same Race Condition

Identical issue with partial writes. Same temp-file + atomic rename fix protects both readers.

**Effort:** `Short` (1–4h — atomic write in `save_enriched_csv`, plus try/except widening in load functions)

---

## Issue 4: Code Quality

### 4a. Missing Dependency Declaration

**Critical.** `pandas_ta` is imported at line 49 but is not listed in `pyproject.toml` dependencies (only `backtesting`, `numba`, `numpy`, `pandas`, `python-statemachine` are declared). A fresh `uv sync` or `pip install` will produce `ModuleNotFoundError: No module named 'pandas_ta'` at import time.

**Additionally:** `python-dotenv` is imported (line 49) and used (`load_dotenv()` at line 51) but also not declared.

**Effort:** `Quick` — add `"pandas-ta>=0.3.0"` and `"python-dotenv>=1.0.0"` to `pyproject.toml`.

### 4b. Import Guard for `pandas_ta`

Even with the dependency declared, the script crashes at import if the environment is missing the package. A soft import with a clear error message would be better for a script that also exports importable functions (`load_ta_latest`, `load_ta_series`):

```python
try:
    import pandas_ta as ta
except ImportError:
    raise ImportError(
        "pandas_ta is required. Install it with: uv add pandas-ta"
    )
```

### 4c. For-Loop Performance

Three functions iterate row-by-row in Python:
- **`_macd_cross()`** — loop over all rows, O(n)
- **`_obv_slope()`** — loop with 5-bar rolling window, O(n)
- **`_price_vs_bb()`** — loop over all rows, O(n)

For the declared use case (batch enrichment of daily/4h/1h CSV files), a typical file might have 500–2000 rows. At this scale, the Python loops are **not a concern** (each takes <10ms). 

**However**, if the script is repurposed for large datasets (100k+ rows of 1m data), these loops become a bottleneck:
- `_macd_cross`: ~0.5–1s per 100k rows
- `_obv_slope`: ~2–3s per 100k rows (inner NumPy ops per window)
- `_price_vs_bb`: ~0.5–1s per 100k rows

**Recommendation:** Leave as-is for now. The loops are clear and correct. If performance becomes an issue, vectorize:

```python
# Vectorized MACD cross:
prev_macd = macd.shift(1)
prev_signal = signal.shift(1)
bullish = (prev_macd <= prev_signal) & (macd > signal)
bearish = (prev_macd >= prev_signal) & (macd < signal)
result = pd.Series("none", index=macd.index)
result[bullish] = "bullish_cross"
result[bearish] = "bearish_cross"
```

### 4d. Float Precision in CSV Output

`save_enriched_csv()` uses `f"{float(v):.8f}"` for all numeric columns. Assessment:

| Column Type | Value Range | 8 Decimals | Verdict |
|------------|-------------|------------|---------|
| Price (BTC) | ~100,000 | 8 decimals → 0.00001 precision | Overkill — 2–4 decimals sufficient |
| Price (EURUSD) | ~1.0 | 8 decimals → 0.00000001 | Reasonable (pip = 0.0001) |
| Volume | varies | 8 decimals | Reasonable |
| RSI | 0–100 | 8 decimals | Overkill — 2 decimals sufficient |
| EMA | follows price | same as price | See price |
| MACD | varies (~±100) | 8 decimals | Overkill — 4–6 decimals sufficient |
| OBV | varies (millions) | 8 decimals | Overkill — 0–2 decimals sufficient |
| BB Width | ratio (~0–1) | 8 decimals | Reasonable |
| OBV Slope | -1.0/0.0/1.0 | 8 decimals | Overkill — stored as string anyway |

**Risk:** Low. Extra precision costs disk space (~2x file size) but doesn't affect correctness. If disk space or read speed matters, reduce precision per column type. Otherwise, acceptable.

The string "none" for `macd_cross` and `price_vs_bb` is consistent with the `_macd_cross()` default value and the `_price_vs_bb()` default, so the write/read round-trip preserves semantics. ✓

### 4e. Column Name Consistency

| CSV Column | DataFrame Column | Match? |
|------------|-----------------|--------|
| `macd_cross` | `df["macd_cross"]` | ✓ |
| `ema21_slope` | `df["ema21_slope"]` | ✓ |
| `price_vs_bb` | `df["price_vs_bb"]` | ✓ |
| `bb_width` | `df["bb_width"]` | ✓ |
| `obv_slope` | `df["obv_slope"]` | ✓ |

All consistent. The `fieldnames` list in `save_enriched_csv()` matches the DataFrame columns exactly. ✓

### 4f. Type Hint Gaps

Functions missing return type annotations:
- `_macd_cross()` → `pd.Series`
- `_obv_slope()` → `pd.Series`
- `_price_vs_bb()` → `pd.Series`
- `ema_signal()` → `str`
- `macd_signal_label()` → `str`
- `rsi_label()` → `str`
- `mfi_signal()` → `str`
- `obv_signal()` → `str`
- `bb_label()` → `str`
- `print_timeframe_block()` → `None` (return type)
- `fmt()` (nested in `save_enriched_csv`) → `str`

Low priority but recommended for `load_ta_latest` and `load_ta_series` since they're importable by other modules.

---

## Action Plan

Ordered by priority (P1 = blocking live snapshot, P2 = serious, P3 = nice-to-have).

| # | Fix | Location | Effort | Priority |
|---|-----|----------|--------|----------|
| 1 | Add `pandas-ta` and `python-dotenv` to `pyproject.toml` | `pyproject.toml` | `Quick` | **P1** |
| 2 | Sort + dedup by timestamp in `load_csv()` | Line 74–77 | `Quick` | **P1** |
| 3 | Read BB columns by position instead of parameter-encoded name | Lines 193–195 | `Quick` | **P1** |
| 4 | Atomic write in `save_enriched_csv()` (write temp → rename) | Lines 502–552 | `Short` | **P1** |
| 5 | Widen exception handling in `load_ta_latest()` and `load_ta_series()` | Lines 129, 167 | `Quick` | **P2** |
| 6 | Simplify `obv_slope` parsing with try/except in `load_ta_latest()` | Line 115 | `Quick` | **P2** |
| 7 | Add `close is None` check to `load_ta_latest()` early return | Line 119 | `Quick` | **P2** |
| 8 | Optional: vectorize `_macd_cross`, `_obv_slope`, `_price_vs_bb` | Lines 228–308 | `Medium` | **P3** |
| 9 | Optional: add type hints to all public functions | Throughout | `Short` | **P3** |
| 10 | Optional: add soft import guard with clear error message | Line 48 | `Quick` | **P3** |

### Total Effort to Reach Live-Snapshot Ready

**`Short`** — Items 1–6 are the critical path. All are Quick/Short and independent of each other. Estimated 2–4 hours total including testing the round-trip (batch enrich → live read → correct latest values).

---

## Escalation Triggers

Consider a more complex solution (e.g., SQLite database instead of CSV, or a dedicated indicator service) if:

1. **Concurrent readers/writers beyond 2 processes** — atomic rename handles 1-writer/N-readers. If multiple writers exist, a proper lock or database is needed.
2. **Dataset exceeds 1M rows** — at that scale, pandas_ta performance degrades and CSV round-trips become slow. Consider Parquet format or a database.
3. **Real-time indicator computation needed** — if the live layer ever needs sub-1m indicator updates (not just reading pre-computed values), batch enrichment is the wrong architecture entirely.

---

*End of Oracle review.*
