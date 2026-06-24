# Fix: `analyze_ta.py` — Two Live-Robustness Bugs

## TL;DR
> **Quick Summary**: Fix two production crashes — `compute_indicators()` on <40 rows (AttributeError from `ta.macd()` returning `None`) and `load_ta_latest()` on header-only CSV (IndexError from `df.iloc[-1]` on empty DataFrame).
> **Deliverables**: 2 guard lines in `analyze_ta.py`, 3 existing tests updated to expect graceful NaN/None instead of exceptions, 2 new tests for boundary conditions.
> **Estimated Effort**: Quick (30m–1h)
> **Parallel Execution**: NO (T1 → T3 are independent, T2 → T4 are independent; T5 depends on all. All changes are trivial, can be done sequentially.)
> **Critical Path**: T1 → T3 (guard + test update for Bug 1) and T2 → T4 (guard + test update for Bug 2) can be parallelized. T5 (suite) depends on all.

---

## Context

### Original Request
Fix two live-robustness bugs in `trade_scripts/analyze_ta.py` identified by the Oracle bug investigation (`.sisyphus/evidence/oracle-bug-investigation.md`):

1. **Bug 1 — `<40 rows`**: `compute_indicators()` crashes when `ta.macd()` or `ta.bbands()` returns `None`, causing `AttributeError: 'NoneType' object has no attribute 'iloc'`. Triggered in production during startup with limited history, broken exchange responses, low-volume pairs, or network interruptions.
2. **Bug 2 — `header-only CSV`**: `load_ta_latest()` crashes with `IndexError: single positional indexer is out-of-bounds` when `pd.read_csv()` returns an empty DataFrame (valid CSV with header but no data rows). Triggered by interrupted atomic writes or race conditions.

### Interview Summary
**Intent classification**: Mid-sized Task — two scoped, bounded fixes with defined guardrails.

**Scope:**

| Task | Fix | Effort |
|------|-----|--------|
| T1 | Early-exit guard in `compute_indicators()` — MIN_ROWS=40 | Quick |
| T2 | Empty-check guard in `load_ta_latest()` — `if df.empty: return None` | Quick |
| T3 | Add `<40` row test + update 2 existing crash-expectation tests in `test_analyze_ta_core.py` | Quick |
| T4 | Add header-only CSV test + update 1 existing crash-expectation test in `test_analyze_ta_io.py` | Quick |
| T5 | Run full 183-test suite — verify no regressions | Quick |

**Current test baseline** (from `--collect-only`):
- `test_analyze_ta_units.py`: 94 tests
- `test_analyze_ta_io.py`: 25 tests
- `test_analyze_ta_core.py`: 14 tests
- `test_analyze_ta_system.py`: 9 tests
- **Analyze TA subtotal**: 142 tests
- **Pre-existing SMC tests** (unit_tests, causality, structure_engine, streaming_vs_batch): 39 tests
- **Grand total**: 181 tests currently passing

**After changes**: 142 + 2 new = 144 analyze_ta tests, 183 grand total.

### Oracle Investigation Summary
Full investigation at `.sisyphus/evidence/oracle-bug-investigation.md`. Key findings:

**Bug 1 root cause**: `ta.macd()` requires 34 rows minimum, `ta.ebsw()` requires 40. When `pandas_ta` returns `None`, `.iloc[:, N]` crashes. The maximum minimum period across all indicators is **40 (EBSW)**. A single early-exit guard at `len(df) < 40` catches all cases.

| Call | Min rows | Returns None? | Crashes? |
|------|----------|---------------|----------|
| `ta.ema(close, 21)` | 21 | Yes, if <21 | No — `None` → NaN column |
| `ta.macd(close)` | 34 | Yes, if <34 | **Yes** — `.iloc[:, 0]` on None (line 191) |
| `ta.bbands(close)` | 20 | Yes, if <20 | **Yes** — `.iloc[:, 2]` on None (line 201) |
| `ta.ebsw(close)` | 40 | Yes, if <40 | No — `None` → NaN column |
| Others | 15–21 | Yes | No — no `.iloc` chain |

**Bug 2 root cause**: `pd.read_csv()` on a header-only CSV returns an empty DataFrame (no error). `df.iloc[-1]` raises `IndexError`, which is NOT caught by the existing `except (pd.errors.EmptyDataError, KeyError, ValueError)` — `IndexError` is not in the tuple.

### Metis Review
*Gap analysis performed on the Oracle investigation.*

**Gaps identified and addressed:**

1. **Q: Are there other `.iloc` chains in `compute_indicators()` that could crash?** A: No — MACD (lines 191–193) and BB (lines 201–203) are the only two `ta.*` calls that decomposite a returned DataFrame with `.iloc`. All others assign `ta.func(...)` directly to `df["col"]`, which safely creates a NaN column if `ta.func()` returns `None`.

2. **Q: Could the early-exit guard break `load_ta_latest()` or `load_ta_series()` callers?** A: No — both functions access `.iloc[-1]` on the *final enriched CSV*, not on the raw DataFrame passed to `compute_indicators()`. `compute_indicators()` is called inside `analyze_timeframe()` → `save_enriched_csv()`. If `save_enriched_csv()` receives an all-NaN DataFrame, it writes an all-NaN CSV, and `load_ta_latest()` will return `None` because `mfi14` or `obv` will be NaN. This is consistent, safe behavior.

3. **Q: Does `print_timeframe_block()` handle all-NaN indicator columns?** A: Yes — every field access uses `last_valid()` (returns NaN if all NaN), `math.isnan()` guards, or `pd.isna()` checks. The outputs show "insufficient data" or "n/a" for every line. Verified by `test_print_timeframe_block_all_nan` in the system tests.

4. **Q: Could `load_ta_series()` also need the empty fix?** A: The investigation flags it as a known limitation (returns empty DataFrame instead of `None` for header-only CSV). It doesn't crash (`.tail()` is safe on empty), so it's out of scope for this crash-fix pass. Noted for future cleanup.

5. **Q: Will the `macd_cross` column being `np.nan` (float) instead of `"none"` (string) cause issues?** A: In `print_timeframe_block()`, `str(df["macd_cross"].iloc[-1])` would produce `"nan"`. This is technically a behavioral change from the current `"none"`. However, for <40 rows the entire output is "insufficient data" territory anyway, so this cosmetic difference is acceptable. The alternative is to set `df["macd_cross"] = "none"` explicitly — may be worth adopting.

6. **Q: Any environment/setup issues?** A: `pandas_ta>=0.4.71b0` is declared in `pyproject.toml`. `pytest` configured. `tests/conftest.py` provides `tmp_csv_dir`, `constant_price_df`, `linear_trend_df` fixtures. Everything is ready.

---

## Work Objectives

### Core Objective
Eliminate two production crash vectors in `trade_scripts/analyze_ta.py` without changing behavior for normal data volumes (>40 rows, non-empty CSVs).

### Concrete Deliverables
1. Updated `trade_scripts/analyze_ta.py` — 2 guard lines added
2. Updated `tests/test_analyze_ta_core.py` — 2 existing tests modified, 1 new test
3. Updated `tests/test_analyze_ta_io.py` — 1 existing test modified, 1 new test
4. Evidence artifacts in `.sisyphus/evidence/` confirming all fixes + tests pass

### Definition of Done
- [ ] `compute_indicators(df)` with <40 rows returns a DataFrame with all indicator columns filled with NaN (no crash)
- [ ] `load_ta_latest()` on header-only CSV returns `None` (no `IndexError`)
- [ ] All existing tests still pass (no regressions)
- [ ] 2 new boundary tests pass
- [ ] Full 183-test suite passes

### Must Have
- Add `MIN_ROWS = 40` early-exit guard at the top of `compute_indicators()` (before any `ta.*` calls)
- Add `if df.empty: return None` after `pd.read_csv()` in `load_ta_latest()`
- Update `test_insufficient_data_5_rows` — change from `pytest.raises(AttributeError)` to NaN column assertions
- Update `test_single_row` — same change
- Update `test_empty_file` in `TestLoadTaLatest` — change from `pytest.raises(IndexError)` to `assert result is None`
- Add `test_compute_indicators_insufficient_rows` — 30-row DataFrame returns all-NaN indicator columns
- Add `test_load_ta_latest_header_only_csv` — header-only CSV returns `None`

### Must NOT Have (Guardrails)
- ❌ Do NOT change any logic in `compute_indicators()` beyond the early-exit guard (no refactoring of `_macd_cross`, `_obv_slope`, `_price_vs_bb`, etc.)
- ❌ Do NOT add imports or modify function signatures
- ❌ Do NOT change `load_ta_series()` — known limitation (returns empty DataFrame instead of None), out of scope
- ❌ Do NOT change `print_timeframe_block()`, `save_enriched_csv()`, or any downstream consumer
- ❌ Do NOT add new dependencies
- ❌ Do NOT change the constant-price, BB-ordering, or MACD-identity invariant tests

---

## Verification Strategy

**Test decision**: Tests-after (existing test suite already exists; this adds guards and updates crash-expectation tests).

**QA policy**: All verification is agent-executed — zero human intervention.

| Level | Method | Tool | What |
|-------|--------|------|------|
| Unit-like | Direct call to `compute_indicators()` with <40 rows | `uv run python -c` | Returns DataFrame, no exception, all indicator cols NaN |
| Unit-like | Direct call to `load_ta_latest()` on header-only CSV | `uv run python -c` | Returns `None`, no `IndexError` |
| Test suite | `pytest` on all 4 test files | `pytest` | 144 tests pass (142 existing + 2 new) |
| Full suite | `pytest` on all 7 test files | `pytest` | 183 tests pass (144 analyze + 39 SMC) |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (independent — can run in parallel or sequence):
  ├── T1: Add early-exit guard to compute_indicators()
  └── T2: Add empty-check guard to load_ta_latest()

Wave 2 (independent — can run in parallel or sequence):
  ├── T3: Update core tests (Bug 1)
  └── T4: Update I/O tests (Bug 2)

Wave 3 (depends on all above):
  └── T5: Run full test suite
```

### Dependency Matrix
| Task | Depends On | Blocks |
|------|-----------|--------|
| T1: Bug 1 guard | — | T3 (tests verify new behavior) |
| T2: Bug 2 guard | — | T4 (tests verify new behavior) |
| T3: Core tests | T1 | T5 |
| T4: I/O tests | T2 | T5 |
| T5: Full suite | T1, T2, T3, T4 | — |

### Agent Dispatch Summary
| Task | Recommended Agent | Skills Required |
|------|------------------|-----------------|
| T1 | `python` + `pandas` | pandas DataFrame column assignment, understanding of indicator column names |
| T2 | `python` + `pandas` | pandas `df.empty` check, understanding of `load_ta_latest()` return contract |
| T3 | `python` + `pytest` | pytest assertions, pandas NaN checks, understanding of existing test structure |
| T4 | `python` + `pytest` | pytest assertions, CSV file creation, understanding of `load_ta_latest()` behavior |
| T5 | `python` + `pytest` | Running pytest, interpreting test output |

---

## TODOs

### Wave 1 — Production Code Fixes

- [ ] **T1. Add early-exit guard to `compute_indicators()`**
  **What to do**: Add an early-exit guard at the top of `compute_indicators()` (before the first `ta.*` call at line 186). If `len(df) < 40`, initialize all indicator/derived columns to `np.nan` and return the DataFrame immediately.

  **Exact code to insert** (after the docstring at line 183, before `# ---- Core indicators via pandas-ta ----` at line 184):

  ```python
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
  ```

  **Note on `macd_cross`**: Setting this to `np.nan` (float) instead of `"none"` (string) is acceptable — for <40 rows, all downstream output is "insufficient data" territory. The column shows `"nan"` in `print_timeframe_block()` stdout, which is consistent with the "n/a" convention used elsewhere for NaN indicators.

  **Must NOT do**: Do NOT change any code below the guard. Do NOT modify the rest of `compute_indicators()`. Do NOT add or remove imports.

  **Recommended Agent Profile**: `python` + `pandas` — comfortable with DataFrame column assignment, understands column name list must match `save_enriched_csv()` fieldnames.

  **Parallelization**: Wave 1, unblocked.

  **References**:
  - `trade_scripts/analyze_ta.py`, lines 180–233
  - `.sisyphus/evidence/oracle-bug-investigation.md` lines 10–104 (full analysis)
  - `save_enriched_csv()` fieldnames at lines 515–522 (canonical column list)
  - `EXPECTED_COLUMNS` in `tests/test_analyze_ta_core.py` lines 23–29

  **Acceptance Criteria**:
  - `compute_indicators(df_30_rows)` returns a DataFrame (no crash)
  - Returned DataFrame has all expected indicator columns
  - All indicator/derived columns are all-NaN (for rows ≤ 40)
  - `compute_indicators(df_100_rows)` still works normally (no regression)

  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: Project root, fix applied
    *Steps*: `uv run python -c "
    import pandas as pd, numpy as np
    from trade_scripts.analyze_ta import compute_indicators
    ts = pd.date_range('2024-01-01', periods=30, freq='1h')
    df = pd.DataFrame({'timestamp': ts, 'open': 100.0, 'high': 101.0, 'low': 99.0, 'close': 100.0, 'volume': 1000.0})
    result = compute_indicators(df)
    assert len(result) == 30, f'Expected 30 rows, got {len(result)}'
    nan_cols = ['ema21','macd','macd_signal','macd_hist','rsi14','bb_upper','bb_mid','bb_lower','mfi14','obv','ebsw','atr14','macd_cross','obv_slope','ema21_slope','price_vs_bb','bb_width']
    for col in nan_cols:
        assert col in result.columns, f'Missing column: {col}'
        assert result[col].isna().all(), f'{col} not all NaN for 30 rows'
    print('PASS: 30 rows returns all-NaN indicators, no crash')
    "
    `
    *Expected Result*: `PASS: 30 rows returns all-NaN indicators, no crash`
    *Evidence*: `.sisyphus/evidence/t1-compute-30-rows.txt`

  - *Tool*: `interactive_bash`
    *Preconditions*: Project root, fix applied
    *Steps*: `uv run python -c "
    import pandas as pd, numpy as np
    from trade_scripts.analyze_ta import compute_indicators
    ts = pd.date_range('2024-01-01', periods=100, freq='1h')
    close = np.linspace(100.0, 200.0, 100)
    open_ = np.roll(close, 1); open_[0] = close[0]
    df = pd.DataFrame({'timestamp': ts, 'open': open_, 'high': np.maximum(open_, close)*1.002, 'low': np.minimum(open_, close)*0.998, 'close': close, 'volume': 1000.0})
    result = compute_indicators(df)
    assert len(result) == 100
    assert result['ema21'].notna().sum() > 0, 'ema21 all NaN for 100 rows'
    print('PASS: 100 rows computes indicators normally')
    "
    `
    *Expected Result*: `PASS: 100 rows computes indicators normally`
    *Evidence*: `.sisyphus/evidence/t1-compute-100-rows.txt`

  - *Tool*: `interactive_bash`
    *Preconditions*: Project root, fix applied
    *Steps*: `uv run python -c "
    import pandas as pd, numpy as np
    from trade_scripts.analyze_ta import compute_indicators
    ts = pd.date_range('2024-01-01', periods=1, freq='1h')
    df = pd.DataFrame({'timestamp': ts, 'open': 100.0, 'high': 101.0, 'low': 99.0, 'close': 100.0, 'volume': 1000.0})
    result = compute_indicators(df)
    assert len(result) == 1
    assert result['macd'].isna().all(), 'macd not NaN for 1 row'
    print('PASS: 1 row returns all-NaN indicators, no crash')
    "
    `
    *Expected Result*: `PASS: 1 row returns all-NaN indicators, no crash`
    *Evidence*: `.sisyphus/evidence/t1-compute-1-row.txt`

  - *Tool*: `interactive_bash`
    *Preconditions*: Project root, fix applied
    *Steps*: `uv run python -c "
    import pandas as pd, numpy as np
    from trade_scripts.analyze_ta import compute_indicators
    ts = pd.date_range('2024-01-01', periods=39, freq='1h')
    df = pd.DataFrame({'timestamp': ts, 'open': 100.0, 'high': 101.0, 'low': 99.0, 'close': 100.0, 'volume': 1000.0})
    result = compute_indicators(df)
    assert len(result) == 39
    assert result['ebsw'].isna().all(), 'ebsw not all NaN for 39 rows'
    print('PASS: 39 rows (boundary) returns all-NaN, no crash')
    "
    `
    *Expected Result*: `PASS: 39 rows (boundary) returns all-NaN, no crash`
    *Evidence*: `.sisyphus/evidence/t1-compute-39-rows.txt`

---

- [ ] **T2. Add empty-check guard to `load_ta_latest()`**
  **What to do**: Add `if df.empty: return None` immediately after `df = pd.read_csv(path)`, before `last = df.iloc[-1]`.

  **Location**: `trade_scripts/analyze_ta.py`, lines 114–116. Insert after line 115.

  **Exact change**:
  ```python
      try:
          df = pd.read_csv(path)
          if df.empty:             # ← new: header-only CSV, no data rows
              return None
          last = df.iloc[-1]       # ← was line 116, now safe
          ...
  ```

  **Must NOT do**: Do NOT modify any other line in `load_ta_latest()`. Do NOT update the `except` clause. Do NOT change `load_ta_series()`.

  **Recommended Agent Profile**: `python` + `pandas` — understands `pd.read_csv()` behavior on empty CSVs.

  **Parallelization**: Wave 1, unblocked (independent of T1).

  **References**:
  - `trade_scripts/analyze_ta.py`, lines 89–136
  - `.sisyphus/evidence/oracle-bug-investigation.md` lines 108–180

  **Acceptance Criteria**:
  - `load_ta_latest()` on header-only CSV returns `None` (no crash)
  - `load_ta_latest()` on normally-populated CSV still returns a dict
  - `load_ta_latest()` on missing file still returns `None`
  - `load_ta_latest()` on truly empty file (0 bytes) still returns `None` (via existing `EmptyDataError` catch)

  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: Project root, fix applied, a temporary directory for CSV files
    *Steps*: `uv run python -c "
    import tempfile, os
    from pathlib import Path
    from trade_scripts.analyze_ta import load_ta_latest
    d = Path(tempfile.mkdtemp())
    # Header-only CSV
    p = d / 'ohlcv_BTCUSDT_1h_ta.csv'
    p.write_text('timestamp,open,high,low,close,volume,ema21\n')
    result = load_ta_latest('BTC/USDT', '1h', str(d))
    assert result is None, f'Expected None, got {result}'
    print('PASS: header-only CSV returns None')
    "
    `
    *Expected Result*: `PASS: header-only CSV returns None`
    *Evidence*: `.sisyphus/evidence/t2-load-empty-header.txt`

  - *Tool*: `interactive_bash`
    *Preconditions*: Project root, fix applied
    *Steps*: `uv run python -c "
    import tempfile, csv
    from pathlib import Path
    from trade_scripts.analyze_ta import load_ta_latest
    d = Path(tempfile.mkdtemp())
    p = d / 'ohlcv_BTCUSDT_1h_ta.csv'
    fieldnames = ['timestamp','open','high','low','close','volume','ema21','macd','macd_signal','macd_hist','rsi14','bb_upper','bb_mid','bb_lower','mfi14','obv','ebsw','atr14','macd_cross','ema21_slope','price_vs_bb','bb_width','obv_slope']
    with open(str(p), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        w.writerow({'timestamp':'2024-01-01','open':'100','high':'101','low':'99','close':'100.5','volume':'1000','ema21':'100.2','macd':'0.5','macd_signal':'0.3','macd_hist':'0.2','rsi14':'55','bb_upper':'102','bb_mid':'100','bb_lower':'98','mfi14':'50','obv':'1000','ebsw':'0.1','atr14':'1.5','macd_cross':'none','ema21_slope':'0.001','price_vs_bb':'inside','bb_width':'0.04','obv_slope':'0'})
    result = load_ta_latest('BTC/USDT', '1h', str(d))
    assert result is not None
    assert result['mfi14'] == 50.0
    assert result['obv'] == 1000.0
    print('PASS: normal CSV returns dict')
    "
    `
    *Expected Result*: `PASS: normal CSV returns dict`
    *Evidence*: `.sisyphus/evidence/t2-load-normal.txt`

  - *Tool*: `interactive_bash`
    *Preconditions*: Project root, fix applied
    *Steps*: `uv run python -c "
    import tempfile
    from pathlib import Path
    from trade_scripts.analyze_ta import load_ta_latest
    d = Path(tempfile.mkdtemp())
    # Missing file (no CSV created)
    result = load_ta_latest('BTC/USDT', '1h', str(d))
    assert result is None, f'Expected None for missing file, got {result}'
    print('PASS: missing file returns None')
    "
    `
    *Expected Result*: `PASS: missing file returns None`
    *Evidence*: `.sisyphus/evidence/t2-load-missing.txt`

---

### Wave 2 — Test Updates

- [ ] **T3. Update core tests — Bug 1 (compute_indicators <40 rows)**
  **What to do**: Make 3 changes to `tests/test_analyze_ta_core.py`:

  **Change 1 — `test_insufficient_data_5_rows`** (lines 40–48):
  Replace the `pytest.raises(AttributeError)` block with a NaN-column assertion:
  ```python
  def test_insufficient_data_5_rows(self) -> None:
      """5-row input returns all-NaN indicator columns (early-exit guard)."""
      ts = pd.date_range("2024-01-01", periods=5, freq="1h")
      df = pd.DataFrame({
          "timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0,
          "close": [100.0 + i for i in range(5)], "volume": 1000.0,
      })
      result = compute_indicators(df)
      for col in ["ema21", "macd", "macd_signal", "macd_hist", "rsi14",
                   "bb_upper", "bb_mid", "bb_lower", "mfi14", "obv",
                   "ebsw", "atr14"]:
          assert result[col].isna().all(), f"{col} should be all NaN for 5 rows"
  ```

  **Change 2 — `test_single_row`** (lines 50–58):
  Same pattern — replace `pytest.raises(AttributeError)` with NaN assertions:
  ```python
  def test_single_row(self) -> None:
      """1-row input returns all-NaN indicator columns (early-exit guard)."""
      ts = pd.date_range("2024-01-01", periods=1, freq="1h")
      df = pd.DataFrame({
          "timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0,
          "close": [100.0], "volume": 1000.0,
      })
      result = compute_indicators(df)
      for col in ["ema21", "macd", "macd_signal", "macd_hist", "rsi14",
                   "bb_upper", "bb_mid", "bb_lower", "mfi14", "obv",
                   "ebsw", "atr14"]:
          assert result[col].isna().all(), f"{col} should be all NaN for 1 row"
  ```

  **Change 3 — Add new test `test_compute_indicators_insufficient_rows`**:
  Insert after `test_single_row` (after the closing of that method, around line 59):
  ```python
  def test_compute_indicators_insufficient_rows(self) -> None:
      """Fewer than 40 rows returns all-NaN indicator columns (boundary test)."""
      df = pd.DataFrame({
          "timestamp": pd.date_range("2024-01-01", periods=30, freq="h"),
          "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000,
      })
      result = compute_indicators(df)
      for col in ["ema21", "macd", "rsi14", "bb_upper", "ebsw", "atr14"]:
          assert result[col].isna().all(), f"{col} should be all NaN for <40 rows"
  ```

  **Must NOT do**: Do NOT change any other tests. Do NOT remove the `constant_price_df` test or the invariant tests. Do NOT add imports — `numpy` and `pytest` are already imported.

  **Recommended Agent Profile**: `python` + `pytest` — understands test file structure, pytest assertion patterns, pandas `.isna()`.

  **Parallelization**: Wave 2, blocked on T1 (but safe to implement after T1 is applied).

  **References**:
  - `tests/test_analyze_ta_core.py`, lines 40–58 (existing tests to modify)
  - `EXPECTED_COLUMNS` at lines 23–29 (full column list)
  - The early-exit guard code from T1

  **Acceptance Criteria**:
  - `test_insufficient_data_5_rows` passes (no longer expects crash)
  - `test_single_row` passes (no longer expects crash)
  - `test_compute_indicators_insufficient_rows` passes
  - All other core tests still pass (no regressions)

  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: T1 applied, all test changes applied
    *Steps*: `uv run python -m pytest tests/test_analyze_ta_core.py::TestComputeIndicators -v 2>&1`
    *Expected Result*: All TestComputeIndicators tests pass (including the 3 modified/added)
    *Evidence*: `.sisyphus/evidence/t3-core-tests-pass.txt`

---

- [ ] **T4. Update I/O tests — Bug 2 (load_ta_latest empty CSV)**
  **What to do**: Make 2 changes to `tests/test_analyze_ta_io.py`:

  **Change 1 — `test_empty_file` in `TestLoadTaLatest`** (lines 163–168):
  Replace the `pytest.raises(IndexError)` block with a `None` assertion:
  ```python
  def test_empty_file(self, tmp_csv_dir: Path) -> None:
      """Empty CSV (header only) returns None (not IndexError)."""
      path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
      path.write_text("timestamp,open,high,low,close,volume\n")
      result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
      assert result is None
  ```

  **Change 2 — Add new test `test_header_only_csv`**:
  Insert after `test_empty_file` (after line 168, before `test_mfi14_nan` at line 170):
  ```python
  def test_header_only_csv(self, tmp_csv_dir: Path) -> None:
      """Header-only CSV with all 23 columns returns None."""
      path = tmp_csv_dir / "ohlcv_BTCUSDT_1h_ta.csv"
      fieldnames = [
          "timestamp", "open", "high", "low", "close", "volume",
          "ema21", "macd", "macd_signal", "macd_hist",
          "rsi14", "bb_upper", "bb_mid", "bb_lower",
          "mfi14", "obv", "ebsw", "atr14",
          "macd_cross", "ema21_slope", "price_vs_bb", "bb_width", "obv_slope",
      ]
      with open(str(path), "w", newline="") as f:
          w = csv.DictWriter(f, fieldnames=fieldnames)
          w.writeheader()
      result = load_ta_latest("BTC/USDT", "1h", str(tmp_csv_dir))
      assert result is None
  ```
  Note: `csv` is already imported at line 10 of the test file — no import needed.

  **Must NOT do**: Do NOT change any other tests. Do NOT change `TestLoadTaSeries.test_empty_file` (line 320–326) — that test documents a known limitation (returns empty DataFrame), not a crash.

  **Recommended Agent Profile**: `python` + `pytest` — understands `csv.DictWriter`, `tmp_csv_dir` fixture.

  **Parallelization**: Wave 2, blocked on T2 (but safe to implement after T2 is applied).

  **References**:
  - `tests/test_analyze_ta_io.py`, lines 163–168 (existing test to modify)
  - `_write_ta_csv` helper at lines 121–133 (for reference on fieldnames)
  - `csv` import already at line 10

  **Acceptance Criteria**:
  - `test_empty_file` passes (no longer expects `IndexError`)
  - `test_header_only_csv` passes
  - All other I/O tests still pass (no regressions)
  - `TestLoadTaSeries.test_empty_file` still passes (unchanged)

  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: T2 applied, all test changes applied
    *Steps*: `uv run python -m pytest tests/test_analyze_ta_io.py::TestLoadTaLatest -v 2>&1`
    *Expected Result*: All TestLoadTaLatest tests pass (including the 2 modified/added)
    *Evidence*: `.sisyphus/evidence/t4-io-tests-pass.txt`

---

### Wave 3 — Full Verification

- [ ] **T5. Run full test suite — verify no regressions**
  **What to do**: Run all 7 test files. Verify all tests pass.

  **Steps**:
  1. Run analyze_ta tests: `python -m pytest tests/test_analyze_ta_units.py tests/test_analyze_ta_io.py tests/test_analyze_ta_core.py tests/test_analyze_ta_system.py -v`
  2. Run pre-existing SMC tests: `python -m pytest tests/unit_tests.py tests/test_causality.py tests/test_structure_engine.py tests/test_streaming_vs_batch.py -v`
  3. Combine: `python -m pytest tests/ -v`

  **Must NOT do**: Do NOT skip any test file. Do NOT modify any test to make it pass — if a test fails, diagnose the root cause.

  **Recommended Agent Profile**: `python` + `pytest` — comfortable running test suites and interpreting results.

  **Parallelization**: Wave 3, blocked on T1, T2, T3, T4.

  **Acceptance Criteria**:
  - All 144 analyze_ta tests pass (94 units + 25 io + 14 core + 2 new + 9 system)
  - All 39 pre-existing SMC tests pass
  - Grand total: 183 tests pass

  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: All previous tasks (T1–T4) applied
    *Steps*: `uv run python -m pytest tests/test_analyze_ta_units.py tests/test_analyze_ta_io.py tests/test_analyze_ta_core.py tests/test_analyze_ta_system.py -v 2>&1`
    *Expected Result*: All tests pass, no failures
    *Evidence*: `.sisyphus/evidence/t5-analyze-ta-suite.txt`

  - *Tool*: `interactive_bash`
    *Preconditions*: All previous tasks applied
    *Steps*: `uv run python -m pytest tests/ -v 2>&1`
    *Expected Result*: All 183 tests pass (144 analyze + 39 SMC)
    *Evidence*: `.sisyphus/evidence/t5-full-suite.txt`

---

## Final Verification Wave

| ID | Check | Agent | Description |
|----|-------|-------|-------------|
| F1 | Plan Compliance Audit | `oracle` | Verify all TODOs completed, no scope creep, both bugs fixed, no changes outside scope |
| F2 | Code Quality Review | `unspecified-high` | Review the 2 guard additions for correctness: edge cases with all-NaN DataFrames, string vs float dtype for `macd_cross`, proper `None` return for empty CSVs |
| F3 | Real Manual QA | `unspecified-high` | Run the full 183-test suite and verify all pass. Optionally run `analyze_ta.py` on a live symbol with <40 rows to confirm no crash |
| F4 | Scope Fidelity Check | `deep` | Confirm no changes outside `compute_indicators()`, `load_ta_latest()`, `test_analyze_ta_core.py`, `test_analyze_ta_io.py` |

---

## Commit Strategy

1. **Single atomic commit** with message:
   ```
   fix(analyze-ta): add guards for <40 rows and empty CSV crashes
   
   - Early-exit guard in compute_indicators(): return all-NaN when <40 rows
     (prevents AttributeError from ta.macd()/ta.bbands() returning None)
   - Empty-check guard in load_ta_latest(): return None on header-only CSV
     (prevents IndexError from df.iloc[-1] on empty DataFrame)
   - Update 3 crash-expectation tests to expect graceful NaN/None
   - Add 2 boundary tests: 30-row input, header-only CSV
   - Full suite: 183 tests pass
   ```

2. **Files to stage**:
   - `trade_scripts/analyze_ta.py` (modified — 2 guard additions)
   - `tests/test_analyze_ta_core.py` (modified — 2 tests updated, 1 test added)
   - `tests/test_analyze_ta_io.py` (modified — 1 test updated, 1 test added)

---

## Success Criteria

- [ ] `compute_indicators(df)` with <40 rows returns all-NaN indicator columns (no crash)
- [ ] `compute_indicators(df)` with ≥40 rows computes indicators normally (no regression)
- [ ] `load_ta_latest()` on header-only CSV returns `None` (no crash)
- [ ] `load_ta_latest()` on normally-populated CSV returns a dict (no regression)
- [ ] `load_ta_latest()` on missing file returns `None` (no regression)
- [ ] All 144 analyze_ta tests pass (94 + 25 + 14 + 2 new + 9)
- [ ] All 39 pre-existing SMC tests pass
- [ ] Grand total: 183 tests pass
- [ ] All evidence artifacts saved to `.sisyphus/evidence/`

---

## Appendix: Oracle Investigation — Gap Closure

The Oracle investigation identified two bugs (both verified as live-production crashes). This plan closes both:

| Bug | Root Cause | Fix | Tests Affected |
|-----|-----------|-----|----------------|
| 1 | `ta.macd()` returns `None` for <34 rows → `.iloc[:, 0]` crashes | Early-exit guard at `MIN_ROWS=40` (catches EBSW's 40-row requirement) | `test_insufficient_data_5_rows`, `test_single_row` (updated), `test_compute_indicators_insufficient_rows` (new) |
| 2 | `pd.read_csv()` on header-only CSV returns empty DataFrame → `df.iloc[-1]` crashes | `if df.empty: return None` after `pd.read_csv()` | `test_empty_file` (updated), `test_header_only_csv` (new) |

Both fixes are:
- **Minimal**: single guard points, no structural changes
- **Safe**: downstream code already handles NaN indicators gracefully (verified by Oracle)
- **Consistent**: return `None` for unavailable data matches existing patterns
