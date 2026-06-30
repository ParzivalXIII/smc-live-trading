# Fix: `analyze_ta.py` — Live Snapshot Readiness

## TL;DR
> **Quick Summary**: Remediate 4 blocking issues in `trade_scripts/analyze_ta.py` identified by the Oracle review to make the enriched CSV output safe for live snapshot consumption. Also add package init file and update stale references.
> **Deliverables**: Updated `trade_scripts/analyze_ta.py` with positional BB column access, sorted/deduped CSV loading, atomic file writes, and a new `trade_scripts/__init__.py`.
> **Estimated Effort**: Short (2–4 hours)
> **Parallel Execution**: YES — 2 waves (infrastructure → logic + verification)
> **Critical Path**: T2 (BB fix) → T4 (atomic write) are independent of each other. T3 (sort+dedup) changes output order — verify after.

---

## Context

### Original Request
Fix `trade_scripts/analyze_ta.py` to be safe for live snapshot consumption. Currently, the script works for offline batch enrichment but has four issues (identified by Oracle review) that make it unreliable when a live layer reads from its output CSV concurrently.

### Interview Summary
**Role**: `analyze_ta.py` serves as:
- **Primary**: Live snapshot support — live layer reads from the latest enriched row
- **Secondary**: Offline TA enrichment — batch computation  
- **NOT**: A live indicator engine

The directory was renamed from `trade-scripts` (hyphen) to `trade_scripts` (underscore) to become a proper Python package. An `__init__.py` is needed.

**Scope determined**:

| # | Fix | Priority | Effort |
|---|-----|----------|--------|
| 1 | BB column names — access by position instead of parameter-encoded name | P1 (blocking live) | Quick |
| 2 | Sort + dedup by timestamp in `load_csv()` | P1 (blocking live) | Quick |
| 3 | Atomic write in `save_enriched_csv()` (write temp → rename) | P1 (blocking live) | Short |
| 4 | Add `trade_scripts/__init__.py` | P1 (package) | Quick |
| 5 | Resolve stale `trade-scripts` references | P2 (cleanup) | Quick |
| 6 | Integration test — end-to-end verification | P0 (verification) | Short |

**Dependency status**: `pandas-ta>=0.4.71b0` and `python-dotenv>=1.2.2` are **already declared** in `pyproject.toml`. The Oracle review flagged this as missing, but it has since been resolved. No action needed.

### Metis Review
*Consultation performed after interview.*

**Gaps identified and addressed:**
1. **Q: Are MACD column names also fragile?** A: Yes — same parameter-encoded pattern (`MACD_12_26_9`, `MACDs_12_26_9`, `MACDh_12_26_9`). Fixing MACD columns by position as well (same scope as T2).
2. **Q: Do `load_ta_latest()` and `load_ta_series()` need widened exception handling?** A: Oracle flagged this as P2. Keeping out of scope to minimize risk, but noted as a follow-up.
3. **Q: Any test infrastructure for this script?** A: No existing tests. Integration test (T6) will serve as regression safety net.
4. **Q: Will T3 change CSV output row order?** A: Yes — previously file-order, now sorted by timestamp. This is intentional but requires callers to verify their consumption code doesn't depend on file-ordering. The integration test validates this.

---

## Work Objectives

### Core Objective
Make `trade_scripts/analyze_ta.py` safe for live snapshot consumption by fixing column-name fragility, data ordering guarantees, and concurrent-write race conditions.

### Concrete Deliverables
1. Updated `trade_scripts/analyze_ta.py` — 3 targeted code changes
2. New `trade_scripts/__init__.py` — empty file for package importability
3. Evidence artifacts in `.sisyphus/evidence/` confirming all fixes work

### Definition of Done
- [ ] BB columns accessed by position (not parameter-encoded name) — no `KeyError` on any `pandas_ta` version
- [ ] `load_csv()` returns timestamp-sorted, deduplicated DataFrame
- [ ] `save_enriched_csv()` uses atomic write (temp file + rename)
- [ ] `trade_scripts/__init__.py` exists
- [ ] No stale `trade-scripts` references remain in code/config files
- [ ] Integration test passes: enriched CSV has all expected columns, sorted timestamps, no NaN in core indicators

### Must Have
- Fix BB column access pattern (lines 192-195) — use `iloc` positional access
- Fix MACD column access pattern (lines 184-186) — use `iloc` positional access  
- Add sort + dedup to `load_csv()` (lines 74-77)
- Add atomic write to `save_enriched_csv()` (lines 502-552)
- Create `trade_scripts/__init__.py`
- Run integration test on a symbol to verify output

### Must NOT Have (Guardrails)
- ❌ Do NOT change the indicator computation logic (EMA, RSI, MFI, etc.) — only access patterns
- ❌ Do NOT change `fmt()` precision or CSV column names — existing consumers depend on them
- ❌ Do NOT refactor the row-by-row loops in `_macd_cross`, `_obv_slope`, `_price_vs_bb` — P3 per Oracle, out of scope
- ❌ Do NOT add new dependencies — existing `pandas-ta` + `python-dotenv` are sufficient
- ❌ Do NOT change function signatures or return types of public functions (`load_ta_latest`, `load_ta_series`)

---

## Verification Strategy

**Test decision**: Tests-after (integration test after fixes, no pre-existing TDD infrastructure).

**QA policy**: All verification is agent-executed — zero human intervention.

| Level | Method | Tool | What |
|-------|--------|------|------|
| Unit-like | Run script on existing data CSV → verify columns | `uv run python trade_scripts/analyze_ta.py BTC/USDT --timeframe 1d` | All columns present, values non-NaN |
| Sort check | Read enriched CSV → check timestamp monotonicity | Bash + Python one-liner | `pd.read_csv().timestamp.is_monotonic_increasing` |
| Column access | Parse enriched CSV column list | Bash with Python | All `fieldnames` columns present |
| Atomic write | Write twice simultaneously → no corruption | Bash with concurrent writes | Verify no partial write artifacts |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (infrastructure — independent):
  ├── T1: Add __init__.py
  └── T5: Fix stale reference to old directory name

Wave 2 (logic changes — T2, T3, T4 are fully independent):
  ├── T2: Fix BB + MACD column access (positional)
  ├── T3: Add sort + dedup to load_csv()
  └── T4: Add atomic write to save_enriched_csv()

Wave 3 (verification — depends on all above):
  └── T6: Integration test
```

### Dependency Matrix
| Task | Depends On | Blocks |
|------|-----------|--------|
| T1: `__init__.py` | — | T6 (package import) |
| T2: BB columns | — | T6 |
| T3: Sort+dedup | — | T6 |
| T4: Atomic write | — | T6 |
| T5: Stale refs | — | — |
| T6: Integration test | T1, T2, T3, T4 | — |

### Agent Dispatch Summary
| Task | Recommended Agent | Skills Required |
|------|------------------|-----------------|
| T1 | general | File creation |
| T2 | python + pandas-ta knowledge | pandas DataFrame column access, `pandas_ta` API |
| T3 | python | pandas sort/dedup |
| T4 | python | OS-level atomic file operations (`tempfile`, `os.rename`/`shutil.move`) |
| T5 | general | grep, file editing |
| T6 | python + bash | Running scripts, parsing CSV output, validating results |

---

## TODOs

### Wave 1 — Infrastructure

- [ ] **T1. Add `trade_scripts/__init__.py`**
  **What to do**: Create an empty `__init__.py` file at `trade_scripts/__init__.py`.
  **Must NOT do**: Do not add any code or imports to this file — it should be empty (a plain package marker).
  **Recommended Agent Profile**: `general` — file creation only.
  **Parallelization**: Wave 1, unblocked.
  **References**: `trade_scripts/` directory (already exists).
  **Acceptance Criteria**:
  - `trade_scripts/__init__.py` exists (verified with `ls`)
  - `uv run python -c "import trade_scripts; print('OK')"` succeeds
  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: Working directory is project root
    *Steps*: `ls trade_scripts/__init__.py`
    *Expected Result*: File exists
    *Evidence*: `.sisyphus/evidence/t1-initpy-exists.txt`
  - *Tool*: `interactive_bash`
    *Preconditions*: venv activated
    *Steps*: `uv run python -c "import trade_scripts; print('package_ok')"`
    *Expected Result*: stdout contains `package_ok`
    *Evidence*: `.sisyphus/evidence/t1-initpy-import.txt`

- [ ] **T5. Resolve stale `trade-scripts` references**
  **What to do**: Search the entire codebase (excluding `.git/`, `.venv/`, `__pycache__/`) for references to the old directory name `trade-scripts` (with hyphen). If found in source code, configuration, or documentation files, update them to `trade_scripts` (underscore). Do NOT modify the Oracle review markdown file (`.sisyphus/evidence/oracle-review-analyze-ta.md`) — that is a historical record.
  **Must NOT do**: Do not modify files in `.git/`, `.venv/`, `node_modules/`, or binary files. Do not modify the Oracle review.
  **Recommended Agent Profile**: `general` — grep + edit.
  **Parallelization**: Wave 1, unblocked.
  **References**: Research shows only 1 stale reference: Oracle review line 3. No source code files reference `trade-scripts`. Verify this is still true before acting.
  **Acceptance Criteria**:
  - `grep -r "trade-scripts" --include="*.py" --include="*.toml" --include="*.md" --include="*.yml" --include="*.yaml" --include="*.json" .` returns ONLY the Oracle review file (which should be excluded from edits).
  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: Working directory is project root
    *Steps*: `grep -rn 'trade-scripts' --include='*.py' --include='*.toml' --include='*.md' --include='*.yml' --include='*.yaml' --include='*.json' . | grep -v '.sisyphus/evidence/oracle-review' | grep -v '.git/'`
    *Expected Result*: No output (no stale references outside the Oracle review)
    *Evidence*: `.sisyphus/evidence/t5-no-stale-refs.txt`

---

### Wave 2 — Logic Changes

- [ ] **T2. Fix BB + MACD column access — positional**
  **What to do**: In `compute_indicators()`, change Bollinger Band and MACD column access from parameter-encoded names to positional access.
  **BB change (lines 192-195)**:
  ```python
  # OLD:
  bb_df = ta.bbands(df["close"], length=20, std=2)
  df["bb_upper"] = bb_df["BBU_20_2.0_2.0"]
  df["bb_mid"] = bb_df["BBM_20_2.0_2.0"]
  df["bb_lower"] = bb_df["BBL_20_2.0_2.0"]

  # NEW:
  bb_df = ta.bbands(df["close"], length=20, std=2)
  # pandas_ta returns: [BBL (lower), BBM (mid), BBU (upper), BBB (bandwidth), BBP (percent)]
  df["bb_upper"] = bb_df.iloc[:, 2]  # BBU → upper band
  df["bb_mid"]   = bb_df.iloc[:, 1]  # BBM → middle band
  df["bb_lower"] = bb_df.iloc[:, 0]  # BBL → lower band
  ```
  **MACD change (lines 184-186)**:
  ```python
  # OLD:
  macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
  df["macd"] = macd_df["MACD_12_26_9"]
  df["macd_signal"] = macd_df["MACDs_12_26_9"]
  df["macd_hist"] = macd_df["MACDh_12_26_9"]

  # NEW:
  macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
  # pandas_ta returns: [MACD (line), MACDh (histogram), MACDs (signal)]
  df["macd"]        = macd_df.iloc[:, 0]  # MACD line
  df["macd_hist"]   = macd_df.iloc[:, 1]  # Histogram
  df["macd_signal"] = macd_df.iloc[:, 2]  # Signal line
  ```
  **Verify column order**: Run a quick Python one-liner to confirm `ta.bbands()` and `ta.macd()` return columns in the expected order with the installed version.
  **Must NOT do**: Do not change any other part of `compute_indicators()`. Do not rename the existing output columns (`bb_upper`, `bb_mid`, `bb_lower`, `macd`, `macd_signal`, `macd_hist`).
  **Recommended Agent Profile**: `python` + pandas-ta experience — understands how `pandas_ta` returns DataFrames and will verify column order before editing.
  **Parallelization**: Wave 2, unblocked (independent of all other tasks).
  **References**:
  - `trade_scripts/analyze_ta.py`, lines 184-186 (MACD) and 192-195 (BB)
  - `pandas_ta.bbands` docs: returns DataFrame with columns in order (lower, mid, upper, bandwidth, percent) — verify with installed version
  - `pandas_ta.macd` docs: returns DataFrame with columns in order (MACD, histogram, signal) — verify with installed version
  **Acceptance Criteria**:
  - Script runs without `KeyError` on any existing OHLCV CSV
  - Output enriched CSV has `bb_upper`, `bb_mid`, `bb_lower`, `macd`, `macd_signal`, `macd_hist` columns with non-NaN values (for rows past the warmup period)
  - `ta.bbands()` and `ta.macd()` column order verified with installed `pandas_ta` version
  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: Project root, a data CSV exists for at least one symbol
    *Steps*: `uv run python -c "import pandas_ta as ta; import pandas as pd; import numpy as np; close = pd.Series(np.random.randn(100).cumsum() + 50000); bb = ta.bbands(close, length=20, std=2); print('BB columns:', list(bb.columns)); macd = ta.macd(close); print('MACD columns:', list(macd.columns))"`
    *Expected Result*: BB returns 5 columns (bands + bandwidth + percent); MACD returns 3 columns. Print the column names for documentation.
    *Evidence*: `.sisyphus/evidence/t2-column-order.txt`
  - *Tool*: `interactive_bash`
    *Preconditions*: After the fix is applied
    *Steps*: `uv run python trade_scripts/analyze_ta.py BTC/USDT --timeframe 1d 2>&1`
    *Expected Result*: Script completes without `KeyError`, prints digest and `→ data/ohlcv_BTCUSDT_1d_ta.csv`
    *Evidence*: `.sisyphus/evidence/t2-runs-without-keyerror.txt`
  - *Tool*: `interactive_bash`
    *Preconditions*: After script run, enriched CSV exists
    *Steps*: `uv run python -c "import pandas as pd; df = pd.read_csv('data/ohlcv_BTCUSDT_1d_ta.csv'); print('bb_upper ok:', df['bb_upper'].notna().sum() > 0); print('macd ok:', df['macd'].notna().sum() > 0)"`
    *Expected Result*: Both prints `True`
    *Evidence*: `.sisyphus/evidence/t2-bb-macd-columns-valid.txt`

- [ ] **T3. Add timestamp sort + dedup to `load_csv()`**
  **What to do**: Update the `load_csv()` function (lines 74-77) to sort by timestamp and deduplicate immediately after loading.
  ```python
  # OLD:
  def load_csv(path: Path) -> pd.DataFrame:
      df = pd.read_csv(path, parse_dates=["timestamp"])
      return df

  # NEW:
  def load_csv(path: Path) -> pd.DataFrame:
      df = pd.read_csv(path, parse_dates=["timestamp"])
      df = df.sort_values("timestamp")
      df = df.drop_duplicates(subset=["timestamp"], keep="last")
      df = df.reset_index(drop=True)
      return df
  ```
  **Important**: This changes the row order of the output CSV from file-order to timestamp-order. For append-based data sources where newer rows are appended at the end, the order was already chronological. But for data sources that write out-of-order, this will reorder rows. Verify no consumer depends on file-order.
  **Must NOT do**: Do not add any other transformations to `load_csv()`. Do not log/warn about the sort or dedup. Do not modify `load_ta_latest()` or `load_ta_series()` — those read the enriched CSV which is already sorted at write-time by this function (sort is deterministic, same input → same output).
  **Recommended Agent Profile**: `python` — comfortable with pandas operations.
  **Parallelization**: Wave 2, unblocked.
  **References**: `trade_scripts/analyze_ta.py`, lines 74-77.
  **Acceptance Criteria**:
  - `load_csv()` returns a DataFrame with `timestamp` column that is monotonically increasing
  - No duplicate timestamps exist in the returned DataFrame
  - Row count after dedup is ≤ row count before dedup
  - Index is clean 0-based (`reset_index(drop=True)`)
  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: After the fix is applied, first run the script to generate enriched output
    *Steps*: `uv run python -c "import pandas as pd; df = pd.read_csv('data/ohlcv_BTCUSDT_1d_ta.csv', parse_dates=['timestamp']); assert len(df) >= 50, f'Too few rows: {len(df)}'; print('monotonic:', df['timestamp'].is_monotonic_increasing); print('dups:', df['timestamp'].duplicated().sum()); print('rows:', len(df))"`
    *Expected Result*: `monotonic: True`, `dups: 0`, row count ≥ 50 (assert does not fire)
    *Evidence*: `.sisyphus/evidence/t3-sorted-deduped.txt`

- [ ] **T4. Add concurrent-write protection to `save_enriched_csv()`**
  **What to do**: Rewrite `save_enriched_csv()` to write to a temporary file first, then atomically rename to the target path. This prevents partial-write corruption when `load_ta_latest()` reads concurrently.
  Import needed modules at the top of the file:
  ```python
  import os
  import tempfile
  import shutil
  ```
  Rewrite `save_enriched_csv()`:
  ```python
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
  ```
  **Must NOT do**: Do not change the CSV format, column names, `fmt()` behavior, or any logic inside the writer loop. Only change the file writing mechanism.
  **Recommended Agent Profile**: `python` — understands `tempfile.mkstemp()`, `shutil.move()` for atomic renames, and proper cleanup in `except` blocks.
  **Parallelization**: Wave 2, unblocked.
  **References**: `trade_scripts/analyze_ta.py`, lines 502-552. Also note `load_ta_latest()` at lines 83-130 and `load_ta_series()` at lines 133-168 are the readers that benefit from this fix.
  **Acceptance Criteria**:
  - `save_enriched_csv()` writes to a `.tmp` file first, then renames to the target path
  - If write fails mid-way, the original target file is NOT corrupted
  - Running the script twice produces identical output (idempotent)
  - `load_ta_latest()` can read the enriched CSV without issues
  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: After the fix, project root, a data CSV exists
    *Steps*: `rm -f data/ohlcv_BTCUSDT_1d_ta.csv && uv run python trade_scripts/analyze_ta.py BTC/USDT --timeframe 1d`
    *Expected Result*: Script completes, file at `data/ohlcv_BTCUSDT_1d_ta.csv` exists
    *Evidence*: `.sisyphus/evidence/t4-atomic-write-basic.txt`
  - *Tool*: `interactive_bash`
    *Preconditions*: After first run, enriched CSV exists
    *Steps*: `ls -la data/ohlcv_BTCUSDT_1d_ta.csv && uv run python -c "import pandas as pd; df = pd.read_csv('data/ohlcv_BTCUSDT_1d_ta.csv'); print('rows:', len(df)); print('cols:', list(df.columns))"`
    *Expected Result*: File exists and is parsable as CSV
    *Evidence*: `.sisyphus/evidence/t4-csv-readable.txt`
  - *Tool*: `interactive_bash`
    *Preconditions*: After first run
    *Steps*: `find data/ -name '*.tmp' 2>/dev/null; echo "tmp_check_done"`
    *Expected Result*: No `.tmp` files left behind (temp file cleaned up)
    *Evidence*: `.sisyphus/evidence/t4-no-orphan-tmp.txt`
  - *Tool*: `interactive_bash`
    *Preconditions*: After the fix is applied, source CSV exists
    *Steps*: |
      ```
      uv run python trade_scripts/analyze_ta.py BTC/USDT --timeframe 1d &
      uv run python trade_scripts/analyze_ta.py BTC/USDT --timeframe 1d &
      wait
      uv run python -c "
      import pandas as pd
      df = pd.read_csv('data/ohlcv_BTCUSDT_1d_ta.csv')
      assert len(df) > 0, 'Empty CSV after concurrent writes'
      assert df['timestamp'].is_monotonic_increasing, 'Timestamps not sorted'
      assert df['timestamp'].duplicated().sum() == 0, 'Duplicate timestamps'
      print(f'PASS: {len(df)} rows, sorted, no dups')
      "
      ```
    *Expected Result*: Both script instances complete without error. CSV is valid, non-empty, sorted, deduplicated.
    *Evidence*: `.sisyphus/evidence/t4-concurrent-write.txt`

---

### Wave 3 — Verification

- [ ] **T6. Integration test — run on a symbol, verify output**
  **What to do**: Run `analyze_ta.py` end-to-end on BTC/USDT for one timeframe (1d), then rigorously verify the enriched CSV output.
  **Steps**:
  1. Ensure a source CSV exists. If `data/ohlcv_BTCUSDT_1d.csv` is not available, generate a synthetic one:
     ```python
     uv run python -c "
     import pandas as pd, numpy as np
     dates = pd.date_range('2024-01-01', periods=1000, freq='h')
     price = 100 + np.cumsum(np.random.randn(1000) * 0.5)
     df = pd.DataFrame({
         'timestamp': dates,
         'open': price,
         'high': price * 1.02,
         'low': price * 0.98,
         'close': price,
         'volume': np.random.rand(1000) * 1000,
     })
     df.to_csv('data/ohlcv_BTCUSDT_1d.csv', index=False)
     print('Synthetic CSV generated: 1000 rows')
     "
     ```
  2. Run: `uv run python trade_scripts/analyze_ta.py BTC/USDT --timeframe 1d`
  3. Read the enriched CSV: `data/ohlcv_BTCUSDT_1d_ta.csv`
  4. Verify all expected columns are present (match against `fieldnames` list)
  5. Verify timestamp is monotonically increasing
  6. Verify no NaN in core indicators (ema21, macd, rsi14, bb_upper, bb_mid, bb_lower, mfi14, obv) for warmup-passed rows
  7. Verify `load_ta_latest()` can read the latest values
  8. Verify `load_ta_series()` can read the last N rows
  **Must NOT do**: Do not modify the script during this task. Do not skip verification steps even if the script runs without errors.
  **Recommended Agent Profile**: `python` + `bash` — comfortable running scripts and validating data programmatically.
  **Parallelization**: Wave 3, blocked on T1+T2+T3+T4.
  **References**:
  - `data/ohlcv_BTCUSDT_1d.csv` (source CSV — generated synthetically if not found)
  - `fieldnames` list from `save_enriched_csv()`
  - `load_ta_latest()` and `load_ta_series()` functions
  **Acceptance Criteria**:
  - All 22 columns from the `fieldnames` list present
  - `timestamp.is_monotonic_increasing == True`
  - Zero duplicate timestamps
  - For rows past indicator warmup (>50): no NaN in `close`, `ema21`, `rsi14`, `bb_upper`, `bb_mid`, `bb_lower`, `mfi14`, `obv`
  - `load_ta_latest("BTC/USDT", "1d")` returns a non-None dict with all expected keys
  **QA Scenarios**:
  - *Tool*: `interactive_bash`
    *Preconditions*: Project root, all fixes applied. Source CSV generated automatically if missing.
    *Steps*: Comprehensive validation script:
    ```
    uv run python -c "
    import pandas as pd, numpy as np, sys, os
    from pathlib import Path
    
    # 0. Generate synthetic CSV if real one doesn't exist
    src = Path('data/ohlcv_BTCUSDT_1d.csv')
    if not src.exists():
        dates = pd.date_range('2024-01-01', periods=1000, freq='h')
        price = 100 + np.cumsum(np.random.randn(1000) * 0.5)
        pd.DataFrame({
            'timestamp': dates,
            'open': price,
            'high': price * 1.02,
            'low': price * 0.98,
            'close': price,
            'volume': np.random.rand(1000) * 1000,
        }).to_csv(src, index=False)
        print(f'Generated synthetic CSV: {src} ({1000} rows)')
    
    # 1. Run the script
    import subprocess
    result = subprocess.run(
        [sys.executable, 'trade_scripts/analyze_ta.py', 'BTC/USDT', '--timeframe', '1d'],
        capture_output=True, text=True
    )
    print('STDOUT:', result.stdout[:500])
    print('STDERR:', result.stderr[:500])
    print('RC:', result.returncode)
    
    # 2. Load enriched CSV
    csv_path = Path('data/ohlcv_BTCUSDT_1d_ta.csv')
    if not csv_path.exists():
        print('FAIL: enriched CSV not found')
        sys.exit(1)
    
    df = pd.read_csv(csv_path, parse_dates=['timestamp'])
    
    # 3. Check columns
    expected = ['timestamp','open','high','low','close','volume',
                'ema21','macd','macd_signal','macd_hist',
                'rsi14','bb_upper','bb_mid','bb_lower',
                'mfi14','obv','ebsw','atr14',
                'macd_cross','ema21_slope','price_vs_bb','bb_width','obv_slope']
    missing = [c for c in expected if c not in df.columns]
    extra = [c for c in df.columns if c not in expected]
    print('Missing cols:', missing)
    print('Extra cols:', extra)
    
    # 4. Sort check
    print('Sorted:', df['timestamp'].is_monotonic_increasing)
    print('Duplicates:', df['timestamp'].duplicated().sum())
    
    # 5. NaN check on core indicators (rows 50+ for warmup)
    core = ['close','ema21','rsi14','bb_upper','bb_mid','bb_lower','mfi14','obv']
    core_df = df[core].iloc[50:]
    nan_counts = core_df.isna().sum()
    print('NaN after warmup:', dict(nan_counts[nan_counts > 0]))
    
    # 6. Test load_ta_latest
    from trade_scripts.analyze_ta import load_ta_latest
    latest = load_ta_latest('BTC/USDT', '1d')
    print('load_ta_latest:', latest is not None)
    if latest:
        print('  keys:', list(latest.keys()))
        print('  mfi14:', latest.get('mfi14'))
    
    # 7. Test load_ta_series
    from trade_scripts.analyze_ta import load_ta_series
    series = load_ta_series('BTC/USDT', '1d', tail=5)
    print('load_ta_series:', series is not None, 'shape:', series.shape if series is not None else 'N/A')
    
    all_ok = (not missing and not extra and df['timestamp'].is_monotonic_increasing
              and df['timestamp'].duplicated().sum() == 0
              and latest is not None
              and series is not None)
    print('\\nALL CHECKS PASSED:', all_ok)
    sys.exit(0 if all_ok else 1)
    "
    ```
    *Expected Result*: `ALL CHECKS PASSED: True`, RC=0
    *Evidence*: `.sisyphus/evidence/t6-integration-test.txt`

---

## Final Verification Wave

| ID | Check | Agent | Description |
|----|-------|-------|-------------|
| F1 | Plan Compliance Audit | `oracle` | Verify all TODOs completed, no scope creep, plan followed exactly |
| F2 | Code Quality Review | `unspecified-high` | Check the 3 edited functions for correctness: edge cases in sort/dedup, temp file cleanup on failure, positional column access |
| F3 | Real Manual QA | `unspecified-high` | Run the full integration test (T6) and verify all assertions pass |
| F4 | Scope Fidelity Check | `deep` | Confirm no changes outside the 3 targeted function edits + `__init__.py` |

---

## Commit Strategy

1. **Single atomic commit** with message:
   ```
   fix(analyze-ta): live snapshot readiness — bb positional, sort+dedup, atomic write, __init__.py
   
   - Access BB/MACD columns by position instead of parameter-encoded names
     (fragile to pandas_ta version changes)
   - Sort + deduplicate by timestamp in load_csv()
     (silently broken indicators on unsorted data)
   - Atomic write via temp file + rename in save_enriched_csv()
     (prevents partial-write corruption for concurrent readers)
   - Add trade_scripts/__init__.py for package importability
   - Resolve stale directory name references
   ```
2. **Files to stage**:
   - `trade_scripts/analyze_ta.py` (modified)
   - `trade_scripts/__init__.py` (new)
   - Possibly other files if T5 finds stale references

---

## Success Criteria

- [ ] `trade_scripts/analyze_ta.py` runs without errors on any existing OHLCV CSV
- [ ] Enriched CSV is safe for concurrent live reading (atomic write)
- [ ] Enriched CSV has monotonically increasing timestamps with no duplicates
- [ ] Bollinger Band and MACD column access works regardless of `pandas_ta` version
- [ ] `trade_scripts` is importable as a Python package
- [ ] All verification artifacts saved to `.sisyphus/evidence/`

---

## Appendix: Oracle Review Items — Scope Decisions

The Oracle review identified 10 issues. Here is the disposition of each:

| # | Issue | Oracle Priority | Status |
|---|-------|----------------|--------|
| 1 | Add `pandas-ta` and `python-dotenv` to dependencies | P1 | ✅ Already done (in `pyproject.toml`) |
| 2 | Sort + dedup by timestamp in `load_csv()` | P1 | ✅ **In scope (T3)** |
| 3 | Read BB columns by position | P1 | ✅ **In scope (T2)** |
| 4 | Atomic write in `save_enriched_csv()` | P1 | ✅ **In scope (T4)** |
| 5 | Widen exception handling in `load_ta_latest/series` | P2 | ❌ Out of scope — not blocking live snapshot; revisit if `ParserError` observed |
| 6 | Simplify `obv_slope` parsing with try/except | P2 | ❌ Out of scope — cosmetic improvement; Oracle reviewer recommended but not blocking |
| 7 | Add `close is None` check to `load_ta_latest()` | P2 | ❌ Out of scope — minor edge case; not blocking |
| 8 | Vectorize loops (`_macd_cross`, `_obv_slope`, `_price_vs_bb`) | P3 | ❌ Out of scope — not needed at current data volumes |
| 9 | Add type hints to public functions | P3 | ❌ Out of scope — nice-to-have, separate PR |
| 10 | Soft import guard for `pandas_ta` | P3 | ❌ Out of scope — dependency is now declared in `pyproject.toml` |

**Total items in scope**: 4 (P1 issues 2, 3, 4 + package init)
**Total items deferred**: 5 (P2 issues 5, 6, 7 + P3 issues 8, 9, 10)
**Total items already resolved**: 1 (P1 issue 1)
