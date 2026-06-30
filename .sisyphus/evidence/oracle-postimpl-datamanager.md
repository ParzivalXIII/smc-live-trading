# Oracle Post-Implementation Review: DataManager

**Date:** 2026-06-30
**Scope:** Post-implementation verification of `DataManager` against Oracle design review.
**Reviewing:** `trade_scripts/data_manager.py` (127 lines), `tests/test_data_manager.py` (10 tests).

---

## Bottom Line

The DataManager implementation is **solid and correctly addresses both Oracle review fixes**. The architecture is clean — pure composition with zero logic reimplementation. All 10 tests verify the core paths. One minor omission (`DataManager` not exported in `__init__.py`) and one missing test case for `since`-override behavior prevent a clean PASS.

**Verdict: CONDITIONAL** — fix the `__init__.py` export, then it's ready.

---

## Plan Compliance: Oracle Design Review Items

| # | Oracle Item | Status | Notes |
|---|-------------|--------|-------|
| 1 | `load_candles()` raises `FileNotFoundError` — need guard | ✅ Fixed | `csv_path.exists()` check on line 84 prevents the crash. |
| 2 | CSV path inconsistent — must use resolved symbol | ✅ Fixed | `_csv_path()` calls `ExchangeFactory.resolve_symbol()` on line 47. |
| 3 | Last timestamp detection: `datetime → POSIX × 1000` | ✅ Correct | Line 94: `int(existing["timestamp"].iloc[-1].timestamp()) * 1000`. |
| 4 | Merge: concat → sort → dedup → reset_index | ✅ Correct | Lines 117–120 match Oracle's spec exactly. |
| 5 | Existing CSV overrides explicit `since` | ✅ Correct | Line 95: `fetch_since = last_ts + 1` overrides the explicit `since` param. |
| 6 | Corruption handling (EmptyDataError, ParserError) | ✅ Correct | Lines 87–89: catches three exception types, deletes file, re-fetches. |
| 7 | Exchange errors propagate, never silent | ✅ Correct | Only `load_candles()` errors are caught; exchange errors bypass the try/except. |
| 8 | `__init__.py` export DataManager | ❌ Missing | `DataManager` not in `__all__` or imports. |

**7/8 Oracle items resolved.** The missing export is a trivial fix.

---

## Architecture Assessment

### Composition — Correct
```
DataManager
  ├── ExchangeFactory.create()      → exchange instance
  ├── ExchangeFactory.resolve_symbol() → unified symbol
  ├── fetch_ohlcv()                 → raw candle list
  ├── process_candles()             → normalized DataFrame
  ├── load_candles()                → existing CSV data
  └── save_candles()                → atomic CSV write
```

No module logic is reimplemented. DataManager is pure orchestration.

### Separation of Concerns — Clean
- `storage.py` handles CSV I/O — DataManager calls it, doesn't touch files directly
- `fetcher.py` handles exchange pagination — DataManager passes params, doesn't loop
- `processor.py` handles normalization — DataManager delegates
- `exchange.py` handles singleton management — DataManager creates once in `__init__`

### CSV Path Convention — Consistent
```
_csv_path("BTCUSDT", "4h")  → data/ohlcv_BTCUSDT_4h.csv
_csv_path("ETH_USDT", "1d") → data/ohlcv_ETHUSDT_1d.csv
_csv_path("BTC/USDT", "1h") → data/ohlcv_BTCUSDT_1h.csv
```
All three input formats resolve to the same naming convention via `resolve_symbol()`.

---

## Code Quality

### Strengths
1. **Idempotent design** — calling `update()` repeatedly with the same data produces identical CSVs (dedup via `keep="last"`)
2. **Atomic writes** — delegates to `save_candles()` which uses tempfile + rename
3. **Defensive corruption handling** — deletes and re-fetches rather than crashing or returning partial data
4. **Clean return value** — returns full merged DataFrame so callers don't need to re-read CSV

### Issues Found

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 1 | Low | `DataManager` not exported in `trade_scripts/__init__.py` | `__init__.py` |
| 2 | Low | Redundant `resolve_symbol()` call — called in both `_csv_path()` (line 47) and `update()` (line 98) for the same symbol | `data_manager.py` |
| 3 | Low | No test for "explicit `since` is overridden by existing data" behavior | `test_data_manager.py` |

All issues are **Low** severity. None affect correctness.

### Redundant `resolve_symbol()` Detail
`_csv_path()` calls `ExchangeFactory.resolve_symbol(self._exchange, symbol)` to build the file path. Then `update()` calls it again on line 98 with the same arguments. This is a minor inefficiency — could pass the resolved symbol from `_csv_path()` or cache it. Not a bug, just a code smell.

---

## Test Coverage

### 10 Tests — All Pass (Logical Review)

| # | Test | What It Verifies | Verdict |
|---|------|-----------------|---------|
| 1 | `test_csv_path_resolves_symbol` | `BTCUSDT` → `ohlcv_BTCUSDT_4h.csv` | ✅ |
| 2 | `test_csv_path_with_separator` | `ETH_USDT` → `ohlcv_ETHUSDT_1d.csv` | ✅ |
| 3 | `test_csv_path_appends_timeframe` | `BTC/USDT` → `ohlcv_BTCUSDT_1h.csv` | ✅ |
| 4 | `test_first_fetch_creates_csv` | No CSV → fetch → save → verify columns | ✅ |
| 5 | `test_incremental_fetch_appends` | Existing CSV → fetch newer → merge | ✅ |
| 6 | `test_no_new_data_returns_existing` | Empty exchange response → return existing | ✅ |
| 7 | `test_corrupted_csv_recovered` | Corrupted CSV → delete → re-fetch | ✅ |
| 8 | `test_exchange_error_propagates` | Exchange exception → raised to caller | ✅ |
| 9 | `test_since_parameter_used` | Explicit `since` → passed to fetcher | ✅ |
| 10 | `test_full_pipeline_mocked` | End-to-end fetch → process → save → reload | ✅ |

### Coverage Gaps

1. **Missing: `since` override test** — No test verifies that when existing CSV data exists AND an explicit `since` is provided, the CSV's last timestamp takes precedence (line 95). This is the core incremental behavior guarantee.

2. **Missing: `max_pages` passthrough** — The `max_pages` parameter is passed to `fetch_ohlcv()` but no test verifies it.

3. **Mock setup correctness** — The tests mock `ExchangeFactory.create` to return a mock exchange. The real `fetch_ohlcv` from `trade_scripts.fetcher` is called with the mock exchange, which then calls `mock_exchange.fetch_ohlcv()`. This integration path is correct and tests the real fetcher logic.

---

## Verdict

### **CONDITIONAL**

The implementation is correct and production-ready. Two trivial items remain:

1. **Required**: Add `DataManager` to `trade_scripts/__init__.py` exports.
2. **Optional**: Add a test for `since`-override behavior (verifies the key incremental guarantee).

### Effort to PASS
- `__init__.py` export: `Quick(<1h)` — one import line + one `__all__` entry
- Override test: `Quick(<1h)` — create CSV with existing data, call `update(since=old_value)`, assert `fetch_since` uses CSV's last timestamp

---

## Escalation Triggers

None. The issues found are trivial and don't affect correctness or safety.

## Optional Future Considerations

1. **Redundant `resolve_symbol` call** — Could refactor `_csv_path()` to accept the already-resolved symbol, or cache the resolution. Low priority — the current code is clear and the double-call is negligible.

2. **`return_new_only` parameter** — The Oracle review mentioned this as a deferred feature. Not needed until the orchestrator demonstrates a use case for it.
