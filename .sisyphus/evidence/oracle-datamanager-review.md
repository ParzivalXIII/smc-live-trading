# Oracle Review: DataManager Design

**Date:** 2026-06-24
**Scope:** DataManager class design review before Prometheus planning.
**Reviewing:** Proposed `DataManager` for incremental OHLCV fetch + merge + save.

---

## Bottom Line

**The design is fundamentally sound but needs two pre-requisite fixes flagged before Prometheus can plan safely.** The proposed DataManager correctly composes the existing four modules and the V1 approach (reading last timestamp from CSV) is the right scope boundary. However:

1. **`load_candles()` raises `FileNotFoundError` for missing files — it never returns `None`.** The proposed `if existing is not None` dead code path means the first fetch (no CSV) would crash. This must be fixed in the design before planning.
2. **CSV path construction is inconsistent with the existing codebase.** The proposed `symbol.replace("/", "")` does not normalize dashes (`BTC-USDT`) and differs from how `analyze_ta.py` constructs paths. Must use the unified symbol for path generation.

Fix those two issues and Prometheus can proceed. Effort: `Short(1-4h)` for the DataManager module plus tests.

---

## Architecture Review

### Module Location: Option A (new `data_manager.py`)

**Verdict: Correct choice.** DataManager is an orchestration concern that composes all four existing modules:

| Module | What DataManager uses |
|--------|----------------------|
| `exchange.py` | `ExchangeFactory` for exchange instance |
| `fetcher.py` | `fetch_ohlcv()` for raw candle data |
| `processor.py` | `process_candles()` for normalization |
| `storage.py` | `save_candles()` / `load_candles()` for CSV I/O |

**Reasons to keep it separate, not in storage.py or fetcher.py:**
- `storage.py` = raw CSV I/O (stateless functions). Adding lifecycle orchestration violates single responsibility.
- `fetcher.py` = raw exchange I/O. File management is outside its concern.
- DataManager owns the "fetch → merge → persist → return" lifecycle as a composable unit.

### Interface Design

```
DataManager.__init__(exchange_id="bybit", data_dir="data", config=None)
DataManager.update(symbol, timeframe, since=None, limit=200, pages=5) → pd.DataFrame
```

The `__init__` takes only config; `update()` takes only per-call params. This is clean. One concern: `ExchangeFactory.create()` stores singletons per exchange_id, so creating multiple DataManager instances with the same exchange_id reuses the same exchange object — correct and intended.

### Pipeline Position

```
CCXT → Fetcher → Processor → DataManager → CSV → analyze_ta → Orchestrator
```

Correct placement. DataManager bridges the data-fetch layer and the TA-analysis layer. The returned DataFrame feeds directly into `analyze_ta.load_csv()` → `compute_indicators()` → or into the Orchestrator's `load_ta_series()` path.

---

## Key Decisions — and Corrections

### 1. ✗ File Path Convention — Needs Fixing

**Proposed code:**
```python
csv_path = self._data_dir / f"ohlcv_{symbol.replace('/', '')}_{timeframe}.csv"
```

**Problem:** If the user passes `"BTC-USDT"` (hyphen), the path becomes `ohlcv_BTC-USDT_1d.csv` — which does not match the existing `ohlcv_BTCUSDT_1d.csv` convention. This causes duplicate fetches and data loss.

**Existing convention (from `analyze_ta.py` line 65 and existing CSV filenames):**
```python
safe = symbol.replace("/", "")    # "BTC/USDT" → "BTCUSDT"
```

**Fix:** Always construct the path from the **resolved unified symbol**, not the raw user input:
```python
unified = ExchangeFactory.resolve_symbol(self._exchange, symbol)
safe_sym = unified.replace("/", "")
csv_path = self._data_dir / f"ohlcv_{safe_sym}_{timeframe}.csv"
```

This ensures consistency regardless of input format. `resolve_symbol` is already called for the exchange fetch — just reuse its result.

### 2. ✗ `load_candles()` Never Returns None — Needs Fixing

**Proposed code:**
```python
existing = load_candles(str(csv_path))
if existing is not None and not existing.empty:
    since = int(existing["timestamp"].iloc[-1].timestamp()) * 1000
else:
    since = None
```

**Problem:** `load_candles()` (in `storage.py` line 62) calls `pd.read_csv()` directly. If the file doesn't exist, it raises `FileNotFoundError`. It **never** returns `None`. The first-branch is dead code — a fresh start with no CSV would crash.

**Fix:** Guard the load call:
```python
try:
    existing = load_candles(str(csv_path))
    has_existing = not existing.empty
except (FileNotFoundError, pd.errors.EmptyDataError):
    existing = None
    has_existing = False

if has_existing:
    since = int(existing["timestamp"].iloc[-1].timestamp()) * 1000
else:
    since = None
```

Or more idiomatically, check `csv_path.exists()` before loading:
```python
if csv_path.exists():
    existing = load_candles(str(csv_path))
    if not existing.empty:
        since = int(existing["timestamp"].iloc[-1].timestamp()) * 1000
```

### 3. Last Timestamp Detection — Correct

The formula `int(df["timestamp"].iloc[-1].timestamp()) * 1000` correctly converts datetime64[ns] → POSIX seconds → CCXT milliseconds. This is the `since` value for `fetch_ohlcv()`.

**Important nuance (documented in the earlier scrutiny):** `since` is inclusive. CCXT returns candles with `timestamp >= since`. This means the last CSV candle is re-fetched. The merge + `drop_duplicates(keep="last")` handles this overlapping candle correctly — any updates to the last candle (e.g., it was incomplete when first saved) are applied. This is **intentional** and should remain.

### 4. Merge Strategy — Correct

Proposed concat → sort → dedup matches the approach used in `analyze_ta.load_csv()` (lines 79-83):

```python
combined = pd.concat([existing, new_df], ignore_index=True)
combined = combined.sort_values("timestamp").drop_duplicates(
    subset=["timestamp"], keep="last").reset_index(drop=True)
```

Areas of compatibility concern:
- Both DataFrames have datetime64 timestamps (from `process_candles` and `load_candles`)
- Both have identical column sets (`timestamp, open, high, low, close, volume`)
- `keep="last"` ensures new data replaces old for the overlapping candle

### 5. `since` Parameter for Initial Backfill — Correct Pattern

```python
def update(self, symbol, timeframe, since=None, limit=200, pages=5):
    # since: optional explicit start (for backfill)
    # If existing CSV has data, always use CSV's last timestamp
    # (overrides explicit since to prevent gaps)
```

The design should clarify priority: **existing CSV data always takes precedence** over an explicit `since` for `since` parameter. The explicit `since` only applies when there's no existing CSV. This prevents accidentally creating gaps:

```python
if has_existing:
    since = existing_last_ms  # CSV data is source of truth
elif since is None:
    since = None              # exchange default (earliest available)
# else: use provided `since`
```

### 6. Return Value — Correct

Returning the full merged DataFrame (not just new candles) is the right choice. It lets the caller immediately pass it to `analyze_ta` without re-reading the file, and it's consistent with how `analyze_timeframe()` reads the CSV.

**But consider adding a `return_new_only` parameter** if the orchestrator only needs the most recent candle:
```python
def update(self, ..., return_new_only=False) -> pd.DataFrame:
```

This is low priority — defer unless there's a demonstrated need.

---

## Error Handling

### What should happen for each failure mode:

| Failure Mode | Behavior | Rationale |
|---|---|---|
| **File not found (first run)** | `since=None`, fetch from earliest | Handled by `try/except FileNotFoundError` or `csv_path.exists()` check |
| **CSV corrupted (parse error)** | Delete corrupted file, re-fetch from scratch | Safer than partial recovery; `pd.errors.ParserError` / `EmptyDataError` catch |
| **Exchange unreachable** | Raise exception (do NOT return stale data) | The caller (orchestrator) handles retry via `reset()` |
| **Exchange returns empty data** | Return existing DataFrame unchanged | No new data available — stale data is acceptable |
| **Partial write (crash during save)** | Atomic write handles this (tempfile + rename) | Already implemented in `save_candles()` |

### Missing from the proposed design:

1. **No corruption handling.** Add a `try` around `load_candles()` that catches `pd.errors.EmptyDataError` and `pd.errors.ParserError`, deletes the corrupted file, and continues with `since=None`.

2. **Exchange error propagation is assumed but not explicit.** The design should state: "Exchange errors (network, rate limit, auth) propagate to the caller unmodified."

---

## Implementation Scope: V1 vs V2

### V1 (this implementation — `Short(1-4h)`)

| Component | Scope |
|-----------|-------|
| `data_manager.py` | New module with `DataManager` class |
| CSV reading | Last timestamp from existing file |
| `since` | Auto-detect from CSV, or explicit for initial backfill |
| Merge | Concat + sort + dedup |
| Error handling | FileNotFound, empty data, exchange errors propagate |
| `__init__.py` | Export `DataManager` |
| Tests | `test_data_manager.py` — unit tests with mock exchange + temp CSVs |

### V2 (deferred — tracker.py from earlier scrutiny)

| Component | Scope |
|-----------|-------|
| `tracker.py` | SQLite `FetchTracker` with `(symbol, timeframe, last_ms)` table |
| `since` | Read from SQLite instead of CSV |
| Performance | Avoids loading entire CSV just to find the last timestamp |
| Concurrent safety | SQLite handles read/write contention |

**Decision: V1 is the right scope.** Reading last timestamp from CSV is O(n) on file size, but CSV files with millions of rows are unlikely for a retail trading bot. Revisit at V2 only if files exceed ~100k rows and the load time becomes noticeable.

---

## Blocking Questions (for Prometheus)

1. **Input symbol format:** Does the caller pass unified symbols (`"BTC/USDT"`) or raw strings (`"BTCUSDT"`)? The DataManager should accept both, but the test plan needs to cover all input formats. The `resolve_symbol` call handles normalization for the exchange call; path construction needs the same.

2. **`config` for ExchangeFactory:** What config values might be passed (e.g., `"type": "spot"`, rate limit overrides)? The existing `ExchangeFactory.create()` accepts an optional `config` dict. The DataManager should pass it through. Default behavior (no config) should work for public OHLCV.

3. **Should `update()` accept multiple timeframes at once?** The current design is single-(symbol, timeframe). If the pipeline needs to update both "1d" and "4h" in one call, the caller loops. This is fine for V1 — don't over-engineer.

4. **Test granularity:** Should `data_manager_test.py` mock `fetch_ohlcv`, `process_candles`, `save_candles` individually, or test the full integration with temp files? Recommend **unit tests with all four dependencies mocked** plus **one integration test** that creates a real temp CSV.

5. **Existing `analyze_ta.load_csv()` does its own sort+dedup.** If DataManager already sorts and deduplicates on write, the sort+dedup in `load_csv()` is redundant but harmless. Document that this is intentional double-checking, not a bug.

---

## Optional Future Consideration

1. **DataManager doesn't need to be a class yet** — a module-level `update_data()` function would suffice if no state is needed beyond the exchange instance and data directory. The class form is justified if future V2 features (e.g., per-symbol config overrides, caching) require state. Prometheus can decide to start with a function and refactor later.
