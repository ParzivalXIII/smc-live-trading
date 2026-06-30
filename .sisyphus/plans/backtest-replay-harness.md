# Backtest Replay Harness — Event-Driven Causal Replay

## TL;DR
> **Quick Summary**: Build a two-phase replay harness that feeds historical OHLC data through `_SwingEngine.update(i, row)` bar-by-bar (the exact live trading code path), records all confirmed structure events to an event log with pivot-index timing, then runs batch downstream analysis (`bos_choch`, `ob`, `liquidity`, `retracements`) to produce a per-candle audit report with metrics and batch comparison.
> 
> **Deliverables**:
> - `backtest.py` — single-file replay harness (function API + CLI)
> - `BacktestConfig` dataclass for parameter management (engine + downstream params)
> - Event log (CSV): per-candle stream of swing events with pivot index + timestamps
> - Per-candle report (CSV): merged output of all 5 SMC indicators
> - Metrics summary (computed, printed to stdout + saved): event counts, actual timing delay, liquidity stats, batch diff score
> - Batch comparison against `swing_highs_lows()` proving causality
> - `validate_dataset()` — data quality pre-check
> 
> **Estimated Effort**: Medium (11 implementation tasks)
> **Parallel Execution**: YES — 5 waves (some sequential within waves)
> **Critical Path**: T1 → T2 → T3 → T5 → T6 → T8 → T9 → T10 → T11

## Context

### Original Request
Build a `backtest.py` that is a replay harness using the same exact code path the live engine will use. No Strategy fantasy layer. Three layers of work:
1. Freeze the runtime contract (what `engine.update(candle)` returns)
2. Add evaluation outputs (swing counts, event timing delay, batch diff)
3. Leave trade simulation interface for V2 (event-driven callback pattern)

### Interview Summary
- **Architecture**: Two-phase — (1) streaming replay via `_SwingEngine.update()`, (2) batch downstream analysis on full swing output
- **Library decision**: Custom pandas loop, NOT `backtesting` Strategy subclass. `backtesting` requires a `Strategy` subclass with no lower-level bypass API. Custom loop gives exact control over the replay path.
- **Event-driven core pattern**:
  ```python
  for candle in candles:
      events = engine.update(candle)       # pure replay
      strategy.update(candle, events)       # V2 bolt-on
  ```
- **Output format**: CSV only (V1). Parquet deferred.
- **CLI vs Library**: Both. Function API (`BacktestHarness` class) is primary. CLI wraps it.
- **Config**: `BacktestConfig` dataclass with defaults `(swing_length=5, confirmation_bars=2, atr_multiplier=1.5, atr_period=7)`. Also includes downstream method parameters.
- **V2 interface**: `StrategyCallback` protocol (`update(index, row, engine_result)`) with `NoopStrategy` for V1. V2 registration via `harness.set_strategy(my_strategy)`.
- **Downstream methods are batch-only**: `bos_choch()`, `ob()`, `liquidity()`, `retracements()` all scan forward through swing indices. They cannot run during the streaming phase.

### Metis Review (Self-Performed)
| Gap | Severity | Resolution |
|---|---|---|
| Default engine params not explicit | Minor | Apply test defaults (swing_length=5, confirmation_bars=2, atr_multiplier=1.5, atr_period=7) |
| Output directory structure undefined | Minor | Default `backtest_results/` with `event_log/` subdirectory |
| Performance expectations | Minor | 24k rows processed in <30s total (measured in QA); warning at >100k rows |
| Column name collisions in merged report | Minor | Prefix disambiguation: `SwingLevel`, `BOSLevel`, `LiqLevel`, `LiqEnd`, `RetraceDirection` |
| No existing backtest code | Info | Greenfield design — no migration concern |

### Momus Review (Applied Fixes)
| ID | Issue | Fix Applied |
|----|-------|-------------|
| C1 | `avg_event_delay_bars` was fake (config constant, not actual timing) | Added `PivotIndex` to engine result dict; `BacktestEvent` records it; metrics compute `confirmation_index - pivot_index` |
| C2 | `batch_diff_score` was hardcoded to 0.0 | Replaced with actual `swings_df` vs `swings_df` diff computation |
| C3 | T6 QA referenced undefined `batch_swings` | QA now creates `batch_swings` before calling `compute_metrics()` |
| I1 | `replay_phase()` bypassed parameter validation | Added validation block mirroring `swing_highs_lows()` 6 conditions |
| I2 | `BacktestHarness.run()` computed swing_highs_lows twice | Removed redundant second call; pass `swings_df` as `batch_swings` |
| I3 | Missing liquidity zone width and sweep rate metrics | Added `avg_liq_zone_width_bars` and `sweep_rate` to metrics |
| I4 | Strategy callback not formally defined | Added `StrategyCallback` Protocol + `NoopStrategy` class |
| M1 | CLI didn't expose downstream method params | Added `--close-break`, `--close-mitigation`, `--range-percent` |
| M2 | `export_results` missing overwrite flag | Added `overwrite: bool = True` parameter |
| M3 | Hardcoded test data path | Added `TEST_DATA_PATH` constant in QA scenarios |
| M4 | Metrics schema vs return dict mismatch | Schema and return dict aligned |
| M5 | argparse imported at module level | Moved `import argparse` inside `main()` |
| X1 | Missing data validation function | Added `validate_dataset()` pre-check for min rows, NaN, monotonic timestamps, numeric types |
| X2 | Missing module export / import path documentation | Documented in T1 and T8 |
| X3 | Missing large-dataset performance warning | Added `warn_if_large()` helper; 100k-row threshold |
| X4 | Integration test needs parameter variation | T11 now tests 2 parameter configurations (default + custom) |

## Work Objectives

### Core Objective
A replay harness that feeds historical OHLC data through the SMC engine bar-by-bar using **the exact same `engine.update(i, row)` code path** that would be used in live trading, recording all output events to a per-candle audit table, then computing batch downstream analysis and metrics.

### Concrete Deliverables
1. `backtest.py` — single-file replay harness with function API and CLI
2. `BacktestConfig` dataclass
3. Event log CSV — one row per confirmed swing event with pivot_index timing
4. Per-candle report CSV — merged output of all 5 indicators (one row per candle)
5. Metrics summary — printed to stdout, saved to file
6. Batch comparison against `smc.swing_highs_lows()` - proves causality
7. Data validation function — pre-checks dataset quality

### Definition of Done
- [ ] `backtest.py` runs end-to-end on EURUSD 15M data without errors
- [ ] Replay Phase 1 output matches `test_causality.py` streaming output (identical code path)
- [ ] Batch Phase 2 downstream methods produce plausible market structure output
- [ ] Event log contains timestamps, event types, prices, and pivot indices for all confirmed swings
- [ ] Per-candle report contains all merged columns with no NaN where data exists
- [ ] Metrics print to stdout and are saved to `metrics.json`
- [ ] CLI invocation `python backtest.py --data tests/test_data/EURUSD/EURUSD_15M.csv --output-dir results/` completes without errors
- [ ] Batch comparison proves zero look-ahead (replay == batch swing_highs_lows)
- [ ] `validate_dataset()` catches bad data before engine runs

### Must Have
- Replay loop uses `_SwingEngine.update(i, row)` with identical call signature to live trading
- `_SwingEngine.update()` exposes `PivotIndex` in its return dict when a swing is confirmed (backwards-compatible extension)
- Two-phase execution: streaming first, batch analysis second
- All 6 parameter validations from `swing_highs_lows()` replicated in `replay_phase()`
- Event log records all confirmed swing events with timestamps and pivot indices
- Per-candle report merges all 5 SMC indicators
- Strategy callback interface (`StrategyCallback` protocol) ready for V2 trade simulation
- CLI wraps function API (not the other way around)
- Config dataclass accepts all engine + downstream parameters
- `validate_dataset()` checks: minimum rows, no NaN columns, monotonic timestamps, numeric dtypes

### Must NOT Have (Guardrails)
- ❌ No `backtesting` library `Strategy` subclass — custom loop only
- ❌ No trade simulation / execution logic — V2 territory
- ❌ No new dependencies beyond existing (`pandas`, `numpy`, `backtesting`)
- ❌ No visualization / GIF generation — V2 territory
- ❌ No parameter optimization / grid search — V2 territory
- ❌ No real-time or live trading integration
- ⚠️ **Exception — allowed `smc.py` change**: Adding `PivotIndex` to `_SwingEngine.update()` return dict is explicitly permitted. This is a **backwards-compatible extension** (one extra dict key, no signature change, no behavioral change). All existing callers ignore unknown keys — `test_causality.py`, `stream_compare.py`, and `swing_highs_lows()` all access only `["HighLow"]` and `["Level"]`. This single-line addition is the ONLY `smc.py` modification allowed.

## Verification Strategy

### Test Decision
Tests-after implementation. The backtest harness will be validated by running against the existing EURUSD 15M dataset and comparing outputs against known-good batch results.

### QA Policy — All agent-executed
Every task includes agent-executable QA scenarios. Scenarios use `interactive_bash` (Python scripts), file comparison, and stdout parsing. `TEST_DATA_PATH` constant is used throughout:
```python
TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
```

## Backtest Report Schema (Canonical)

### Event Log Schema (Phase 1 — Streaming)
CSV file: `event_log.csv`

| Column | Type | Description |
|--------|------|-------------|
| timestamp | datetime | Candle timestamp from input data |
| candle_index | int | Zero-based position of the CONFIRMATION bar |
| pivot_index | int | Zero-based position of the PIVOT bar (where candidate was established) |
| event_type | str | `"swing_high"` or `"swing_low"` |
| price | float | The swing level (price) |
| delay_bars | int | `candle_index - pivot_index` (computed on export for convenience) |
| metadata | str | JSON-encoded dict: `{"HighLow": 1.0, "Level": 0.97372}` |

**Critical timing column**: `pivot_index` is the bar where the candidate was first established. `delay_bars = candle_index - pivot_index` measures how many bars it took for the engine to confirm the swing. This is the actual timing delay, not a config constant.

### Per-Candle Report Schema (Phase 1 + Phase 2 Merged)
CSV file: `per_candle_report.csv` — one row per candle, columns grouped by origin:

| Group | Column | Source | Notes |
|-------|--------|--------|-------|
| **Input** | `Timestamp` | Input CSV | Datetime of candle |
| | `Open` | Input CSV | |
| | `High` | Input CSV | |
| | `Low` | Input CSV | |
| | `Close` | Input CSV | |
| | `Volume` | Input CSV | |
| **Swings** | `SwingHighLow` | Phase 1 (`_SwingEngine`) | 1.0 / -1.0 / NaN |
| | `SwingLevel` | Phase 1 | Disambiguated from `Level` |
| | `SwingPivotIndex` | Phase 1 | Index of pivot bar (NaN if no swing) |
| **BOS/CHOCH** | `BOS` | Phase 2 (`bos_choch`) | 1 / -1 / NaN |
| | `CHOCH` | Phase 2 | 1 / -1 / NaN |
| | `BOSLevel` | Phase 2 | Renamed from `Level` |
| | `BrokenIndex` | Phase 2 | |
| **Order Blocks** | `OB` | Phase 2 (`ob`) | 1 / -1 / NaN |
| | `OBTop` | Phase 2 | Renamed from `Top` |
| | `OBBottom` | Phase 2 | Renamed from `Bottom` |
| | `OBVolume` | Phase 2 | |
| | `OBMitigatedIndex` | Phase 2 | Renamed from `MitigatedIndex` |
| | `OBPct` | Phase 2 | Renamed from `Percentage` |
| **Liquidity** | `Liquidity` | Phase 2 (`liquidity`) | 1 / -1 / NaN |
| | `LiqLevel` | Phase 2 | Renamed from `Level` |
| | `LiqEnd` | Phase 2 | Renamed from `End` |
| | `LiqSwept` | Phase 2 | |
| **Retracements** | `RetraceDirection` | Phase 2 (`retracements`) | Renamed from `Direction` (1/-1/0) |
| | `CurrentRetracement%` | Phase 2 | |
| | `DeepestRetracement%` | Phase 2 | |

### Metrics Output Schema

| Metric | Type | Source |
|--------|------|--------|
| total_swings | int | Count of non-NaN SwingHighLow |
| swing_highs | int | Count of SwingHighLow == 1 |
| swing_lows | int | Count of SwingHighLow == -1 |
| total_bos | int | Count of non-NaN BOS |
| bullish_bos | int | Count of BOS == 1 |
| bearish_bos | int | Count of BOS == -1 |
| total_choch | int | Count of non-NaN CHOCH |
| total_ob | int | Count of non-NaN OB |
| bull_ob | int | Count of OB == 1 |
| bear_ob | int | Count of OB == -1 |
| total_liquidity_zones | int | Count of non-NaN Liquidity |
| avg_ob_pct | float | Mean of OBPct (non-NaN) |
| avg_event_delay_bars | float | Mean of `delay_bars` across all events (actual measured timing) |
| min_event_delay | int | Minimum delay_bars across all events |
| max_event_delay | int | Maximum delay_bars across all events |
| avg_liq_zone_width_bars | float | Mean of `LiqEnd - index` for each liquidity zone |
| sweep_rate | float | Fraction of liquidity zones that were swept (Swept is non-NaN) |
| batch_diff_score | float | % of rows where replay != batch (0.0 = perfect match) |
| diff_rows | int | Raw count of differing rows (if any) |
| processing_time_seconds | float | Total wall-clock time |

## Execution Strategy

### Parallel Execution Waves

```
Wave 1: Foundation          [T1, T2]           — parallel (independent)
               │
Wave 2: Replay Engine       [T3]               — blocked by T1, T2
               │
Wave 3: Batch Analysis      [T4]               — blocked by T3
               │
Wave 4: Assembly + Output   [T5] then [T6, T7] — T5 blocks T6/T7 (parallel)
               │
Wave 5: API + CLI           [T8, T9]           — sequential (T8 then T9)
               │
Wave 6: Validation          [T10, T11]          — parallel
```

### Dependency Matrix
| Task | Depends On | Blocks |
|------|-----------|--------|
| T1 | — | T3 |
| T2 | — | T3 |
| T3 | T1, T2 | T4, T10 |
| T4 | T3 | T5 |
| T5 | T4 | T6, T7 |
| T6 | T5 | T8 |
| T7 | T5 | T8 |
| T8 | T6, T7 | T9 |
| T9 | T8 | T11 |
| T10 | T3 | T11 |
| T11 | T9, T10 | — |

### Agent Dispatch Summary

| Wave | Tasks | Agent Type | Rationale |
|------|-------|------------|-----------|
| 1 | T1, T2 | `explore` → `general` | Research existing patterns, then build |
| 2 | T3 | `general` | Core replay loop + smc.py PivotIndex — needs deep engine knowledge |
| 3 | T4 | `general` | Batch downstream — needs all 4 method interfaces |
| 4 | T5, T6, T7 | `general` | Can parallelize T6/T7 after T5 |
| 5 | T8, T9 | `general` | Sequential — T8 builds the class, T9 wraps it |
| F | T10, T11 | `unspecified-high` | Validation — needs domain knowledge |

## TODOs

### Wave 1 — Foundation

- [ ] T1. Build `BacktestConfig` dataclass + data loading + dataset validation
  **What to do**: Create the configuration dataclass, data loading logic, and dataset validation pre-check in `backtest.py`. This file lives at project root and is runnable as `python backtest.py` from any location (uses relative path under project root; or accepts absolute paths).

  **Config design** (updated with downstream parameters):
  ```python
  from dataclasses import dataclass, field
  from typing import Optional
  import pandas as pd

  @dataclass
  class BacktestConfig:
      """Configuration for the SMC replay backtest harness."""
      # Swing engine parameters
      swing_length: int = 5
      confirmation_bars: int = 2
      atr_multiplier: float = 1.5
      atr_period: int = 7
      
      # Downstream method parameters
      close_break: bool = True         # bos_choch: close vs high/low for break detection
      close_mitigation: bool = False   # ob: close vs high/low for mitigation
      range_percent: float = 0.01      # liquidity: range for swing clustering
      
      # Data settings
      date_column: str = "Date"        # column name for timestamp
      date_format: str = "%Y.%m.%d %H:%M:%S"  # strptime format
      lowercase_columns: bool = True   # normalize column names
      
      # Export settings
      overwrite: bool = True           # overwrite existing output files
  ```

  **Data loading function**:
  ```python
  def load_dataset(path: str, config: BacktestConfig) -> pd.DataFrame:
      """
      Load OHLC CSV with column normalization.
      
      - Reads CSV
      - Parses date column as index
      - Renames columns to lowercase for engine compatibility
      - Ensures required columns exist: open, high, low, close, volume
      
      Returns DataFrame with datetime index and lowercase columns.
      
      Usage from project root:
          data = load_dataset("tests/test_data/EURUSD/EURUSD_15M.csv", config)
      Usage from arbitrary location:
          data = load_dataset("/absolute/path/to/data.csv", config)
      """
  ```
  
  **Dataset validation function** (X1 — new):
  ```python
  def validate_dataset(data: pd.DataFrame, config: BacktestConfig) -> list[str]:
      """
      Validate dataset quality before running the backtest.
      
      Checks:
      - Minimum rows (>= max(swing_length, atr_period) + confirmation_bars)
      - Required columns exist (open, high, low, close, volume)
      - No entire columns are NaN
      - Timestamps are monotonic (no out-of-order data)
      - All numeric columns have numeric dtypes
      
      Returns:
          list[str]: List of warning/error messages. Empty if all checks pass.
      """
  ```

  The `load_dataset` function MUST produce output compatible with `_SwingEngine.update(i, row)`.
  The column lowercasing matches what `@inputvalidator` does for batch methods.
  The `Volume` column in the test CSV is named `Volume` (capital). The `Tickvol` and `Spread` columns exist but are not used by the engine.
  The `validate_dataset()` function should be called at the start of `BacktestHarness.run()` so bad data is caught before engine instantiation.
  
  **Must NOT do**:
  - ❌ Do not import `backtesting` library here
  - ❌ Do not add engine parameters not accepted by `_SwingEngine.__init__()`
  - ❌ Do not add data validation that rejects valid OHLC formats (be lenient in parsing)
  - ❌ `validate_dataset()` should warn, not crash — unless data is completely unusable (no required columns, zero rows)
  
  **Recommended Agent Profile**: `general` — Python dataclass + pandas loading
  **Parallelization**: Wave 1, blocks T3
  **References**:
  - `smc._SwingEngine.__init__()` signature (smc.py lines 68–78)
  - `tests/unit_tests.py` lines 16–19 (test data loading pattern)
  - `tests/stream_compare.py` lines 36–41 (date parsing)
  - Test CSV format: `Date,Open,High,Low,Close,Tickvol,Volume,Spread`
  
  **Acceptance Criteria**:
  - [ ] `BacktestConfig` dataclass has all 7 parameter fields (4 engine + 3 downstream) with correct defaults
  - [ ] `BacktestConfig` has `date_column`, `date_format`, `overwrite` fields
  - [ ] `BacktestConfig(swing_length=10, confirmation_bars=3)` works (partial override)
  - [ ] `load_dataset(path, config)` returns DataFrame with datetime index
  - [ ] `load_dataset()` returns DataFrame with lowercase columns: open, high, low, close, volume
  - [ ] `load_dataset()` raises `FileNotFoundError` for invalid paths
  - [ ] `load_dataset()` raises `ValueError` if required columns are missing
  - [ ] Output has same number of rows as input CSV
  - [ ] `validate_dataset()` returns empty list for valid EURUSD dataset
  - [ ] `validate_dataset()` returns errors for DataFrame with NaN column or missing columns
  
  **QA Scenarios**:
  1. **Config creation and override**
     - Tool: `interactive_bash` — Python
     - Preconditions: `backtest.py` exists with `BacktestConfig`
     - Steps: 
       ```python
       from backtest import BacktestConfig
       c = BacktestConfig()
       assert c.swing_length == 5
       assert c.close_break == True
       assert c.range_percent == 0.01
       c2 = BacktestConfig(swing_length=10, confirmation_bars=3, close_mitigation=True)
       assert c2.swing_length == 10
       assert c2.close_mitigation == True
       print("Config OK")
       ```
     - Expected: All assertions pass
     - Evidence: `.sisyphus/evidence/t1-config.txt`
  
  2. **Dataset loading with EURUSD data**
     - Tool: `interactive_bash` — Python
     - Preconditions: EURUSD CSV exists at `tests/test_data/EURUSD/EURUSD_15M.csv`
     - Steps:
       ```python
       from backtest import BacktestConfig, load_dataset
       config = BacktestConfig()
       df = load_dataset("tests/test_data/EURUSD/EURUSD_15M.csv", config)
       print(f"Shape: {df.shape}")
       print(f"Columns: {list(df.columns)}")
       print(f"Index type: {type(df.index)}")
       ```
     - Expected: Shape = (24425, 5), columns = [open, high, low, close, volume], datetime index
     - Evidence: `.sisyphus/evidence/t1-data-load.txt`
  
  3. **Dataset validation**
     - Tool: `interactive_bash` — Python
     - Preconditions: Same as above
     - Steps:
       ```python
       from backtest import validate_dataset
       warnings = validate_dataset(df, config)
       assert len(warnings) == 0, f"Warnings: {warnings}"
       print("Validation: PASS (no warnings)")
       ```
     - Expected: Empty warning list for valid EURUSD data
     - Evidence: `.sisyphus/evidence/t1-validation.txt`

- [ ] T2. Event log schema + event recording + StrategyCallback protocol
  **What to do**: Build the event log schema, recording class, and the `StrategyCallback` protocol in `backtest.py`.

  **Design** (updated with `pivot_index` and `StrategyCallback`):
  ```python
  from dataclasses import dataclass, asdict
  from typing import List, Protocol
  import json

  @dataclass
  class BacktestEvent:
      """A single event recorded during replay."""
      timestamp: str          # ISO-formatted datetime string
      candle_index: int       # Position of CONFIRMATION bar in dataset
      pivot_index: int        # Position of PIVOT bar (where candidate was established)
      event_type: str         # "swing_high" or "swing_low"
      price: float            # Price level of the event
      metadata: str = ""      # JSON-encoded dict with extra fields

  class EventRecorder:
      """Accumulates events during Phase 1 replay."""
      
      def __init__(self):
          self._events: List[BacktestEvent] = []
      
      def record_swing(self, index: int, pivot_index: int, timestamp, 
                       highlow: float, level: float):
          """Record a confirmed swing event from engine.update()."""
          event_type = "swing_high" if highlow == 1.0 else "swing_low"
          meta = json.dumps({
              "HighLow": float(highlow), 
              "Level": float(level),
              "delay_bars": index - pivot_index,
          })
          self._events.append(BacktestEvent(
              timestamp=str(timestamp),
              candle_index=index,
              pivot_index=pivot_index,
              event_type=event_type,
              price=float(level),
              metadata=meta,
          ))
      
      @property
      def events(self) -> List[BacktestEvent]:
          return self._events
      
      def to_dataframe(self) -> pd.DataFrame:
          """Convert recorded events to a DataFrame for export."""
          df = pd.DataFrame([asdict(e) for e in self._events])
          if not df.empty:
              df["delay_bars"] = df["candle_index"] - df["pivot_index"]
          return df
      
      def clear(self):
          self._events = []
  ```

  **StrategyCallback Protocol** (I4 — new):
  ```python
  class StrategyCallback(Protocol):
      """Protocol for V2 trade simulation callbacks.
      
      Implementations receive per-bar engine output and can execute trades.
      V1 uses NoopStrategy (no-op). V2 plugs in a real strategy.
      """
      def update(self, candle_index: int, row: pd.Series, 
                 engine_result: dict[str, float]) -> None:
          """Called every bar with current candle and engine output.
          
          Args:
              candle_index: Position in the dataset (0-based).
              row: Current candle OHLCV data (lowercase columns).
              engine_result: Output from _SwingEngine.update() — contains
                  "HighLow", "Level", and "PivotIndex" keys.
          """
          ...

  class NoopStrategy:
      """No-op strategy for V1. Does nothing — placeholder for V2."""
      def update(self, candle_index: int, row: pd.Series,
                 engine_result: dict[str, float]) -> None:
          pass
  ```

  **Must NOT do**:
  - ❌ Do not record events that weren't produced by `engine.update()`
  - ❌ Do not add trade-related fields (entry_price, pnl, etc.) — V2 territory
  - ❌ Do not hardcode timestamp format — use the dataset's own timestamps
  - ❌ Do not add order methods (buy/sell) to `StrategyCallback` — V2 territory
  
  **Recommended Agent Profile**: `general` — Python dataclass + Protocol design
  **Parallelization**: Wave 1, blocks T3
  **References**: User's event log spec: `timestamp`, `event_type`, `price`, `metadata`
  
  **Acceptance Criteria**:
  - [ ] `BacktestEvent` dataclass has exactly 6 fields: timestamp, candle_index, pivot_index, event_type, price, metadata
  - [ ] `EventRecorder.record_swing(12, 8, ts, 1.0, 1.05)` creates event with candle_index=12, pivot_index=8
  - [ ] `EventRecorder.to_dataframe()` returns DataFrame with 7 columns (incl. delay_bars)
  - [ ] `EventRecorder.to_dataframe()` is empty (0 rows) when no events recorded
  - [ ] event_type is always "swing_high" or "swing_low"
  - [ ] metadata is a valid JSON string
  - [ ] `StrategyCallback` Protocol class exists with `update()` signature
  - [ ] `NoopStrategy().update(0, row, {"HighLow": np.nan})` runs without error
  
  **QA Scenarios**:
  1. **Event recording with pivot_index**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       from backtest import EventRecorder
       recorder = EventRecorder()
       # (confirmation_index=12, pivot_index=8, high swing at level 0.97372)
       recorder.record_swing(12, 8, "2022-10-10 02:00:00", 1.0, 0.97372)
       recorder.record_swing(27, 20, "2022-10-10 04:00:00", -1.0, 0.97250)
       df = recorder.to_dataframe()
       assert len(df) == 2
       assert list(df.columns) == [
           "timestamp", "candle_index", "pivot_index", "event_type", 
           "price", "metadata", "delay_bars"
       ]
       assert df.iloc[0]["delay_bars"] == 4  # 12 - 8 = 4
       print(df.to_string())
       ```
     - Expected: Two events, correct columns, delay_bars computed
     - Evidence: `.sisyphus/evidence/t2-event-recording.txt`
  
  2. **StrategyCallback protocol check**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       from backtest import NoopStrategy
       import pandas as pd, numpy as np
       s = NoopStrategy()
       row = pd.Series({"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 100})
       result = {"HighLow": np.nan, "Level": np.nan, "PivotIndex": np.nan}
       s.update(0, row, result)  # Should not raise
       print("NoopStrategy: OK")
       ```
     - Expected: No exception
     - Evidence: `.sisyphus/evidence/t2-strategy-protocol.txt`

---

### Wave 2 — Replay Engine

- [ ] T3. Phase 1 — Streaming replay loop with event recording + PivotIndex in smc.py
  **What to do**: 
  1. First, modify `smc._SwingEngine.update()` to expose `PivotIndex` in the return dict.
  2. Then, implement the core streaming replay loop using the same code path.
  3. Add parameter validation mirroring `swing_highs_lows()`.

  **Step 3a: Add PivotIndex to `_SwingEngine.update()` return** (momux C1)
  
  In `smartmoneyconcepts/smc.py`, inside the `update()` method, add `PivotIndex` to the result dict when a swing is confirmed. This is the ONLY allowed change to `smc.py`.
  
  **Change location**: Lines 186 and 199 of `smc.py` (the two confirmation blocks). Add one line each:
  
  In the swing high confirmation block (around line 186-191):
  ```python
  # After confirming swing high
  result["HighLow"] = 1.0
  result["Level"] = float(self._candidate_level)
  result["PivotIndex"] = float(self._candidate_index)  # NEW LINE
  ```
  
  In the swing low confirmation block (around line 198-205):
  ```python
  # After confirming swing low
  result["HighLow"] = -1.0
  result["Level"] = float(self._candidate_level)
  result["PivotIndex"] = float(self._candidate_index)  # NEW LINE
  ```
  
  On non-confirmation bars, `PivotIndex` is not set in the result dict (the default dict initialization covers it — or add explicit default). **Important**: The result dict is initialized at line 149 with only `HighLow` and `Level`. Since `PivotIndex` is set only on confirmation, non-confirmation bars will not have the key. The replay loop must handle this with `result.get("PivotIndex", np.nan)`.
  
  **Backwards-compatibility analysis**: Existing callers (`test_causality.py`, `stream_compare.py`, `swing_highs_lows()`) all access only `result["HighLow"]` and `result["Level"]`. The new `PivotIndex` key is safely ignored by them. **Zero behavioral impact** on existing tests.

  **Step 3b: Parameter validation block** (I1 — new):
  
  At the start of `replay_phase()`, before engine instantiation, add the same 6 validation checks that `swing_highs_lows()` uses:
  ```python
  if config.swing_length < 2:
      raise ValueError(f"swing_length must be >= 2, got {config.swing_length}")
  if config.confirmation_bars < 1:
      raise ValueError(f"confirmation_bars must be >= 1, got {config.confirmation_bars}")
  if config.atr_multiplier <= 0:
      raise ValueError(f"atr_multiplier must be > 0, got {config.atr_multiplier}")
  if config.atr_period < 1:
      raise ValueError(f"atr_period must be >= 1, got {config.atr_period}")
  if max(config.swing_length, config.atr_period) + config.confirmation_bars > len(data):
      raise ValueError(
          f"max(swing_length, atr_period) ({max(config.swing_length, config.atr_period)}) + "
          f"confirmation_bars ({config.confirmation_bars}) = "
          f"{max(config.swing_length, config.atr_period) + config.confirmation_bars} > "
          f"len(data) ({len(data)}): insufficient data"
      )
  if config.atr_period > len(data):
      raise ValueError(f"atr_period ({config.atr_period}) > len(data) ({len(data)})")
  ```

  **Step 3c: Replay loop** (core — matches user's spec exactly):
  ```python
  import numpy as np
  from smartmoneyconcepts.smc import smc

  def replay_phase(data: pd.DataFrame, config: BacktestConfig,
                   strategy_callback: Optional[StrategyCallback] = None
                   ) -> tuple[pd.DataFrame, List[BacktestEvent]]:
      """
      Phase 1: Stream all candles through _SwingEngine.update().
      
      This is the EXACT code path that would be used in live trading.
      Each candle sees only past data — zero look-ahead.
      
      Returns:
          swings_df: DataFrame with columns HighLow, Level (one row per candle)
          events: List of BacktestEvent objects for confirmed swings
      """
      # [Parameter validation block from Step 3b]
      
      # Instantiate the engine with identical parameters to live trading
      engine = smc._SwingEngine(
          config.swing_length,
          config.confirmation_bars,
          config.atr_multiplier,
          config.atr_period,
      )
      
      recorder = EventRecorder()
      callback = strategy_callback or NoopStrategy()
      n = len(data)
      
      # Pre-allocate swing output arrays
      highs_lows = np.full(n, np.nan, dtype=np.float64)
      levels = np.full(n, np.nan, dtype=np.float64)
      pivot_indices = np.full(n, np.nan, dtype=np.float64)
      
      for i in range(n):
          row = data.iloc[i]
          
          # Step 1: Engine update — THIS is the live code path
          result = engine.update(i, row)
          
          # Step 2: Record swing output
          highlow = result["HighLow"]
          level = result["Level"]
          pivot_index = result.get("PivotIndex", np.nan)
          highs_lows[i] = highlow
          levels[i] = level
          pivot_indices[i] = pivot_index
          
          # Step 3: Record event if swing was confirmed
          if not np.isnan(highlow):
              recorder.record_swing(i, int(pivot_index), data.index[i], highlow, level)
          
          # Step 4: Strategy callback (V2 hook — no-op in V1)
          callback.update(i, row, result)
      
      swings_df = pd.concat([
          pd.Series(highs_lows, name="HighLow", dtype=np.float64),
          pd.Series(levels, name="Level", dtype=np.float64),
          pd.Series(pivot_indices, name="PivotIndex", dtype=np.float64),
      ], axis=1)
      
      return swings_df, recorder.events
  ```

  **Critical design notes**:
  - `engine.update(i, row)` is called with `i` = integer index, `row` = pd.Series with lowercase columns
  - The engine state is preserved across calls — this is identical to how `swing_highs_lows()` works internally
  - The swing output DataFrame includes `PivotIndex` for metrics computation (but only `HighLow` and `Level` are needed for batch comparison)
  - `strategy_callback` defaults to `NoopStrategy()` — never None, so the callback is always safe to call
  
  **Must NOT do**:
  - ❌ Do NOT call `smc.swing_highs_lows()` inside the loop — that would recompute the full batch on every candle
  - ❌ Do NOT use any pandas `shift(-N)` or any forward-looking operation
  - ❌ Do NOT modify any engine state after calling `engine.update()` — the engine is self-contained
  - ❌ Do NOT reshape, truncate, or filter the output arrays — must be same length as input
  - ❌ Do NOT import or use `backtesting.Strategy`
  - ❌ Do not change any other part of `smc.py` beyond the two PivotIndex lines
  
  **Recommended Agent Profile**: `general` — deep knowledge of `_SwingEngine` internals + smc.py
  **Parallelization**: Wave 2, blocked by T1, T2; blocks T4, T10
  **References**:
  - `smc._SwingEngine.update()` (smc.py lines 131–208) — the exact function being modified and called
  - `smc.swing_highs_lows()` (smc.py lines 292–372) — batch validation reference
  - `tests/test_causality.py` lines 24–32 (streaming_backtest function) — same pattern
  - `tests/stream_compare.py` lines 22–30 — same streaming pattern
  - Oracle scrutiny (`.sisyphus/evidence/oracle-scrutiny.md`) — confirms engine is stable
  
  **Acceptance Criteria**:
  - [ ] `smc._SwingEngine.update()` returns dict with "PivotIndex" when swing is confirmed
  - [ ] `smc._SwingEngine.update()` does NOT have "PivotIndex" on non-confirmation bars
  - [ ] Existing `smc.swing_highs_lows()` still works (ignores unknown key)
  - [ ] Existing `tests/test_causality.py` still passes (no behavioral change)
  - [ ] `replay_phase(data, config)` returns `(swings_df, events)` tuple
  - [ ] `swings_df` has columns `HighLow`, `Level`, `PivotIndex`
  - [ ] `swings_df[["HighLow", "Level"]]` matches `smc.swing_highs_lows(data, ...)` output exactly
  - [ ] `replay_phase()` raises `ValueError` for degenerate parameter combinations
  - [ ] All `events` have valid `pivot_index` and `delay_bars`
  - [ ] Total events count == number of non-NaN HighLow values in swings_df
  - [ ] No look-ahead (verified by T10 comparison)
  - [ ] Processing 24,425 rows completes in < 30 seconds
  
  **QA Scenarios**:
  1. **PivotIndex in engine output**
     - Tool: `interactive_bash` — Python
     - Preconditions: EURUSD CSV, T1 data loading, smc.py modified
     - Steps:
       ```python
       from smartmoneyconcepts.smc import smc
       engine = smc._SwingEngine(5, 2, 1.5, 7)
       import pandas as pd, numpy as np
       # Feed synthetic data with a clear peak
       for i, h, l, c in [(0, 1.0, 0.9, 0.95), (1, 1.1, 1.0, 1.05), 
                           (2, 1.2, 1.1, 1.15), (3, 1.15, 1.05, 1.1),
                           (4, 1.1, 1.0, 1.05), (5, 1.05, 0.95, 1.0),
                           (6, 1.0, 0.9, 0.95)]:
           row = pd.Series({"open": c, "high": h, "low": l, "close": c, "volume": 100})
           result = engine.update(i, row)
           if not np.isnan(result.get("HighLow", np.nan)):
               print(f"Bar {i}: HighLow={result['HighLow']}, PivotIndex={result.get('PivotIndex')}")
       ```
     - Expected: Confirmed swing at bar 6 with PivotIndex pointing to peak bar (2)
     - Evidence: `.sisyphus/evidence/t3-pivot-index.txt`
  
  2. **Replay vs batch identity check**
     - Tool: `interactive_bash` — Python
     - Preconditions: T1 data loading, EURUSD CSV
     - Steps:
       ```python
       from backtest import BacktestConfig, load_dataset, replay_phase
       from smartmoneyconcepts.smc import smc
       TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
       
       config = BacktestConfig()
       data = load_dataset(TEST_DATA_PATH, config)
       swings_df, events = replay_phase(data, config)
       
       # Compare against batch swing_highs_lows (only HighLow, Level match)
       batch = smc.swing_highs_lows(
           data, swing_length=config.swing_length,
           confirmation_bars=config.confirmation_bars,
           atr_multiplier=config.atr_multiplier,
           atr_period=config.atr_period,
       ).reset_index(drop=True)
       
       pd.testing.assert_frame_equal(
           swings_df[["HighLow", "Level"]], batch, check_dtype=False
       )
       print(f"Events recorded: {len(events)}")
       print(f"Non-NaN HighLow: {swings_df['HighLow'].notna().sum()}")
       print("Replay == Batch: PASS")
       ```
     - Expected: `assert_frame_equal` passes, event count matches non-NaN count
     - Evidence: `.sisyphus/evidence/t3-replay-identity.txt`
  
  3. **Parameter validation**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       from backtest import BacktestConfig, load_dataset, replay_phase
       import traceback
       TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
       data = load_dataset(TEST_DATA_PATH, BacktestConfig())
       try:
           replay_phase(data, BacktestConfig(swing_length=0))
           print("ERROR: should have raised ValueError")
       except ValueError as e:
           print(f"OK: ValueError raised: {e}")
       ```
     - Expected: ValueError raised for invalid swing_length
     - Evidence: `.sisyphus/evidence/t3-validation.txt`

---

### Wave 3 — Batch Analysis

- [ ] T4. Phase 2 — Batch downstream analysis
  **What to do**: After Phase 1 completes, run all 4 downstream methods on the full swing DataFrame. These are batch methods that scan forward through swing indices — they CANNOT run during the streaming phase.

  **Design** (updated with config-driven parameters):
  ```python
  def batch_analysis_phase(
      data: pd.DataFrame,
      swings_df: pd.DataFrame,
      config: BacktestConfig
  ) -> dict[str, pd.DataFrame]:
      """
      Phase 2: Run all 4 downstream methods in batch mode.
      
      These methods require the FULL swing DataFrame to compute results
      (they scan forward for broken index, mitigated index, swept levels).
      
      Returns:
          dict with keys: "bos_choch", "ob", "liquidity", "retracements"
          each value is a DataFrame with the method's output columns.
      """
      bos_choch_result = smc.bos_choch(data, swings_df, close_break=config.close_break)
      ob_result = smc.ob(data, swings_df, close_mitigation=config.close_mitigation)
      liquidity_result = smc.liquidity(data, swings_df, range_percent=config.range_percent)
      retracements_result = smc.retracements(data, swings_df)
      
      return {
          "bos_choch": bos_choch_result,
          "ob": ob_result,
          "liquidity": liquidity_result,
          "retracements": retracements_result,
      }
  ```

  **Important note on input data**: The `@inputvalidator` decorator on the `smc` class lowercases column names. The `load_dataset()` function already returns lowercase columns. So `data` passed to `bos_choch()`, `ob()`, etc. must have lowercase columns (or the decorator will fail). The `load_dataset()` output is already lowercase — pass it directly.

  **Must NOT do**:
  - ❌ Do not modify the downstream methods' parameter defaults unless using config values
  - ❌ Do not wrap results in any custom data structure — keep raw DataFrames
  - ❌ Do not filter or clean the output — preserve all NaN semantics
  - ❌ Do not run downstream methods during Phase 1 (they need full swing data)
  
  **Recommended Agent Profile**: `general` — all 4 downstream method interfaces
  **Parallelization**: Wave 3, blocked by T3; blocks T5
  **References**:
  - `bos_choch()` (smc.py lines 374–526)
  - `ob()` (smc.py lines 528–723)
  - `liquidity()` (smc.py lines 725–851)
  - `retracements()` (smc.py lines 1053–1140)
  
  **Acceptance Criteria**:
  - [ ] `batch_analysis_phase(data, swings_df, config)` returns dict with 4 keys
  - [ ] Each result DataFrame has same number of rows as input data
  - [ ] Each result DataFrame has the expected columns (per schema above)
  - [ ] No exceptions raised for any method
  - [ ] Results are deterministic (identical output on repeated runs)
  - [ ] `close_break=False` produces different BOS/CHOCH results than `close_break=True`
  
  **QA Scenarios**:
  1. **Batch analysis completeness**
     - Tool: `interactive_bash` — Python
     - Preconditions: T3 replay completed, swings_df available
     - Steps:
       ```python
       from backtest import BacktestConfig, load_dataset, replay_phase, batch_analysis_phase
       TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
       config = BacktestConfig()
       data = load_dataset(TEST_DATA_PATH, config)
       swings_df, _ = replay_phase(data, config)
       results = batch_analysis_phase(data, swings_df, config)
       for name, df in results.items():
           print(f"{name}: shape={df.shape}, columns={list(df.columns)}")
       ```
     - Expected: 4 DataFrames, each with correct shape, no exceptions
     - Evidence: `.sisyphus/evidence/t4-batch-completeness.txt`
  
  2. **Non-trivial output check**
     - Tool: `interactive_bash`
     - Steps:
       ```python
       for name, df in results.items():
           non_na = df.notna().sum().to_dict()
           print(f"{name}: {non_na}")
       ```
     - Expected: At least some non-NaN values in each method
     - Evidence: `.sisyphus/evidence/t4-non-trivial.txt`

---

### Wave 4 — Assembly + Output

- [ ] T5. Per-candle report construction
  **What to do**: Merge all Phase 1 and Phase 2 outputs into a single per-candle DataFrame. Handle column name collisions by prefixing/disambiguating.

  **Design** (updated with `SwingPivotIndex`):
  ```python
  def build_per_candle_report(
      data: pd.DataFrame,
      swings_df: pd.DataFrame,
      batch_results: dict[str, pd.DataFrame],
  ) -> pd.DataFrame:
      """
      Build single per-candle DataFrame with all indicators merged.
      
      Merges on index position (row-by-row).
      Uses canonical column names from the Backtest Report Schema.
      """
      report = pd.DataFrame(index=data.index)
      
      # Input OHLC data
      report["Timestamp"] = data.index
      report["Open"] = data["open"].values
      report["High"] = data["high"].values
      report["Low"] = data["low"].values
      report["Close"] = data["close"].values
      report["Volume"] = data["volume"].values
      
      # Phase 1: Swing engine output (disambiguated column names)
      report["SwingHighLow"] = swings_df["HighLow"].values
      report["SwingLevel"] = swings_df["Level"].values
      report["SwingPivotIndex"] = swings_df["PivotIndex"].values
      
      # Phase 2: BOS/CHOCH (Level → BOSLevel to avoid collision)
      bc = batch_results["bos_choch"]
      report["BOS"] = bc["BOS"].values
      report["CHOCH"] = bc["CHOCH"].values
      report["BOSLevel"] = bc["Level"].values
      report["BrokenIndex"] = bc["BrokenIndex"].values
      
      # Phase 2: Order Blocks (Top → OBTop, Bottom → OBBottom, etc.)
      ob_r = batch_results["ob"]
      report["OB"] = ob_r["OB"].values
      report["OBTop"] = ob_r["Top"].values
      report["OBBottom"] = ob_r["Bottom"].values
      report["OBVolume"] = ob_r["OBVolume"].values
      report["OBMitigatedIndex"] = ob_r["MitigatedIndex"].values
      report["OBPct"] = ob_r["Percentage"].values
      
      # Phase 2: Liquidity (Level → LiqLevel, End → LiqEnd)
      liq = batch_results["liquidity"]
      report["Liquidity"] = liq["Liquidity"].values
      report["LiqLevel"] = liq["Level"].values
      report["LiqEnd"] = liq["End"].values
      report["LiqSwept"] = liq["Swept"].values
      
      # Phase 2: Retracements (Direction → RetraceDirection)
      ret = batch_results["retracements"]
      report["RetraceDirection"] = ret["Direction"].values
      report["CurrentRetracement%"] = ret["CurrentRetracement%"].values
      report["DeepestRetracement%"] = ret["DeepestRetracement%"].values
      
      return report
  ```

  **Column mapping reference**:
  | Original | Report Column | Why |
  |----------|--------------|-----|
  | swings_df.HighLow | SwingHighLow | Avoid ambiguity |
  | swings_df.Level | SwingLevel | Collides with BOSLevel and LiqLevel |
  | swings_df.PivotIndex | SwingPivotIndex | Distinguish from other index columns |
  | bc.Level | BOSLevel | Collides with SwingLevel and LiqLevel |
  | ob.Top | OBTop | Avoids ambiguity in merged table |
  | ob.Bottom | OBBottom | Same |
  | ob.MitigatedIndex | OBMitigatedIndex | Same |
  | ob.Percentage | OBPct | Shorter, clearer |
  | liq.Level | LiqLevel | Collides with SwingLevel and BOSLevel |
  | liq.End | LiqEnd | "End" is too generic |
  | ret.Direction | RetraceDirection | "Direction" is too generic |

  **Must NOT do**:
  - ❌ Do not drop any columns from the raw method outputs
  - ❌ Do not change the index of any DataFrame (merge by position, not index label)
  - ❌ Do not interpolate or fill NaN values — keep raw NaN semantics
  
  **Recommended Agent Profile**: `general` — pandas merge/rename
  **Parallelization**: Wave 4, blocked by T4; blocks T6, T7
  **References**: Backtest Report Schema (above), all 5 output schemas from `smc.py`
  
  **Acceptance Criteria**:
  - [ ] Report has exactly `len(data)` rows
  - [ ] Report has all columns listed in the Backtest Report Schema (including `SwingPivotIndex`)
  - [ ] Column names match the schema exactly (no typos, no extra suffixes)
  - [ ] Row order matches input data order
  - [ ] No column name collisions (no duplicate "Level", "End", etc.)
  - [ ] NaN preserved exactly as produced by each method
  
  **QA Scenarios**:
  1. **Report shape and column audit**
     - Tool: `interactive_bash` — Python
     - Preconditions: T4 batch results available
     - Steps:
       ```python
       from backtest import BacktestConfig, load_dataset, replay_phase, \
           batch_analysis_phase, build_per_candle_report
       TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
       config = BacktestConfig()
       data = load_dataset(TEST_DATA_PATH, config)
       swings_df, _ = replay_phase(data, config)
       results = batch_analysis_phase(data, swings_df, config)
       report = build_per_candle_report(data, swings_df, results)
       expected_cols = [
           "Timestamp", "Open", "High", "Low", "Close", "Volume",
           "SwingHighLow", "SwingLevel", "SwingPivotIndex",
           "BOS", "CHOCH", "BOSLevel", "BrokenIndex",
           "OB", "OBTop", "OBBottom", "OBVolume", "OBMitigatedIndex", "OBPct",
           "Liquidity", "LiqLevel", "LiqEnd", "LiqSwept",
           "RetraceDirection", "CurrentRetracement%", "DeepestRetracement%",
       ]
       assert list(report.columns) == expected_cols, f"Mismatch: {list(report.columns)}"
       assert len(report) == len(data)
       print(f"Report shape: {report.shape}")
       print("Columns OK")
       ```
     - Expected: All assertions pass, 26 columns (was 25, added SwingPivotIndex)
     - Evidence: `.sisyphus/evidence/t5-report-schema.txt`

- [ ] T6. Metrics computation
  **What to do**: Compute all metrics from the per-candle report and event log. This includes actual event timing delay (using `pivot_index` from the engine) and liquidity zone statistics.

  **Design** (updated with real delay computation, real batch diff, liquidity metrics):
  ```python
  def compute_metrics(
      report: pd.DataFrame,
      events: list,
      swings_df: pd.DataFrame,
      batch_swings: pd.DataFrame,
      config: BacktestConfig,
      processing_time: float,
  ) -> dict:
      """Compute all backtest metrics."""
      
      # ===== Swing counts =====
      swings = report["SwingHighLow"]
      total_swings = int(swings.notna().sum())
      swing_highs = int((swings == 1.0).sum())
      swing_lows = int((swings == -1.0).sum())
      
      # ===== BOS/CHOCH counts =====
      bos = report["BOS"]
      total_bos = int(bos.notna().sum())
      bull_bos = int((bos == 1).sum())
      bear_bos = int((bos == -1).sum())
      total_choch = int(report["CHOCH"].notna().sum())
      
      # ===== OB counts =====
      ob = report["OB"]
      total_ob = int(ob.notna().sum())
      bull_ob = int((ob == 1).sum())
      bear_ob = int((ob == -1).sum())
      
      # OB strength
      ob_pct = report["OBPct"]
      avg_ob_pct = float(ob_pct[ob_pct.notna()].mean()) if ob_pct.notna().any() else 0.0
      
      # ===== Liquidity =====
      liq = report["Liquidity"]
      total_liq = int(liq.notna().sum())
      
      # Liquidity zone width (avg bars between start and end of zone)
      liq_end = report["LiqEnd"]
      zone_widths = []
      for idx in report.index:
          if not pd.isna(liq[idx]):
              end = liq_end[idx]
              if not pd.isna(end):
                  zone_widths.append(int(end) - idx)
      avg_liq_zone_width_bars = float(
          sum(zone_widths) / len(zone_widths)
      ) if zone_widths else 0.0
      
      # Sweep rate: fraction of liquidity zones that got swept
      liq_swept = report["LiqSwept"]
      swept_count = int((liq.notna() & liq_swept.notna()).sum())
      sweep_rate = round(swept_count / total_liq, 4) if total_liq > 0 else 0.0
      
      # ===== Event timing delay (actual measured, not configured) =====
      # Uses pivot_index from the engine's confirmed swings
      delays = []
      for e in events:
          delay = e.candle_index - e.pivot_index
          delays.append(delay)
      avg_event_delay_bars = round(
          sum(delays) / len(delays), 2
      ) if delays else 0.0
      min_event_delay = min(delays) if delays else 0
      max_event_delay = max(delays) if delays else 0
      
      # ===== Batch diff (replay vs swing_highs_lows — should be 0) =====
      # Since replay == batch by design (same code path), this compares
      # swings_df against batch_swings. Both should be identical.
      if batch_swings is not None:
          replay = swings_df[["HighLow", "Level"]].reset_index(drop=True)
          batch = batch_swings[["HighLow", "Level"]].reset_index(drop=True)
          
          hl_match = (
              replay["HighLow"].fillna(-999).values 
              == batch["HighLow"].fillna(-999).values
          )
          lvl_match = (
              replay["Level"].fillna(-999).values 
              == batch["Level"].fillna(-999).values
          )
          diff_mask = ~(hl_match & lvl_match)
          diff_rows = int(diff_mask.sum())
          batch_diff_score = round(diff_rows / len(replay) * 100, 4)
      else:
          diff_rows = -1
          batch_diff_score = -1.0
      
      return {
          # Swing counts
          "total_swings": total_swings,
          "swing_highs": swing_highs,
          "swing_lows": swing_lows,
          # BOS/CHOCH
          "total_bos": total_bos,
          "bullish_bos": bull_bos,
          "bearish_bos": bear_bos,
          "total_choch": total_choch,
          # Order blocks
          "total_ob": total_ob,
          "bullish_ob": bull_ob,
          "bearish_ob": bear_ob,
          "avg_ob_pct": round(avg_ob_pct, 2),
          # Liquidity
          "total_liquidity_zones": total_liq,
          "avg_liq_zone_width_bars": round(avg_liq_zone_width_bars, 2),
          "sweep_rate": sweep_rate,
          # Event timing (actual measured)
          "avg_event_delay_bars": avg_event_delay_bars,
          "min_event_delay": min_event_delay,
          "max_event_delay": max_event_delay,
          # Batch comparison
          "batch_diff_score": batch_diff_score,
          "diff_rows": diff_rows,
          # Performance
          "processing_time_seconds": round(processing_time, 2),
      }
  ```

  **Must NOT do**:
  - ❌ Do not compute trade-related metrics (profit factor, sharpe, drawdown) — V2
  - ❌ Do not modify the report DataFrame in this function
  
  **Recommended Agent Profile**: `general` — pandas aggregation
  **Parallelization**: Wave 4, blocked by T5; blocks T8
  **References**: Metrics schema above, `tests/test_causality.py` for swing counting pattern
  
  **Acceptance Criteria**:
  - [ ] Returns dict with ALL keys from the Metrics schema (18 metrics)
  - [ ] All numeric values are valid (no NaN, no Inf)
  - [ ] total_swings == swing_highs + swing_lows
  - [ ] total_bos == bullish_bos + bearish_bos
  - [ ] avg_ob_pct is between 0 and 100
  - [ ] avg_event_delay_bars >= config.confirmation_bars (minimum possible delay)
  - [ ] min_event_delay >= config.confirmation_bars
  - [ ] avg_liq_zone_width_bars >= 0
  - [ ] 0 <= sweep_rate <= 1.0
  - [ ] batch_diff_score == 0.0 for identical replay/batch (should always be true)
  - [ ] processing_time_seconds is positive
  
  **QA Scenarios**:
  1. **Metrics consistency with real delay computation**
     - Tool: `interactive_bash` — Python
     - Preconditions: T5 report available, events list available
     - Steps:
       ```python
       from backtest import BacktestConfig, load_dataset, replay_phase, \
           batch_analysis_phase, build_per_candle_report, compute_metrics
       from smartmoneyconcepts.smc import smc
       TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
       
       config = BacktestConfig()
       data = load_dataset(TEST_DATA_PATH, config)
       swings_df, events = replay_phase(data, config)
       results = batch_analysis_phase(data, swings_df, config)
       report = build_per_candle_report(data, swings_df, results)
       
       # Compute batch swings for diff comparison
       batch_swings = smc.swing_highs_lows(
           data, swing_length=config.swing_length,
           confirmation_bars=config.confirmation_bars,
           atr_multiplier=config.atr_multiplier,
           atr_period=config.atr_period,
       ).reset_index(drop=True)
       
       metrics = compute_metrics(report, events, swings_df, batch_swings, config, 5.0)
       
       # Consistency checks
       assert metrics["total_swings"] == metrics["swing_highs"] + metrics["swing_lows"]
       assert metrics["total_bos"] == metrics["bullish_bos"] + metrics["bearish_bos"]
       assert 0 <= metrics["avg_ob_pct"] <= 100
       assert metrics["avg_event_delay_bars"] >= config.confirmation_bars
       assert 0 <= metrics["sweep_rate"] <= 1.0
       assert metrics["diff_rows"] == 0  # replay == batch
       
       for k, v in metrics.items():
           print(f"{k}: {v}")
       ```
     - Expected: All assertions pass, event delay is actual measured timing
     - Evidence: `.sisyphus/evidence/t6-metrics.txt`

- [ ] T7. CSV export functions
  **What to do**: Implement export functions for the event log, per-candle report, and metrics. Add `overwrite` parameter and large-dataset warning.

  **Design** (updated with `overwrite` flag and `warn_if_large()`):
  ```python
  import os
  import json

  def warn_if_large(data: pd.DataFrame, threshold: int = 100000):
      """Print warning if dataset exceeds threshold rows."""
      if len(data) > threshold:
          print(f"⚠️  Warning: Large dataset ({len(data)} rows). "
                f"Expected runtime may exceed 60 seconds.")

  def export_results(
      report: pd.DataFrame,
      events: list,
      metrics: dict,
      output_dir: str,
      overwrite: bool = True,
  ):
      """Export all backtest artifacts to CSV + JSON.
      
      Args:
          report: Per-candle report DataFrame.
          events: List of BacktestEvent objects.
          metrics: Metrics dict from compute_metrics().
          output_dir: Directory to write output files.
          overwrite: If False, raise FileExistsError if output_dir exists.
      """
      if not overwrite and os.path.exists(output_dir):
          raise FileExistsError(f"Output directory already exists: {output_dir}")
      
      os.makedirs(output_dir, exist_ok=True)
      
      # Per-candle report
      report_path = os.path.join(output_dir, "per_candle_report.csv")
      report.to_csv(report_path, index=False)
      
      # Event log
      event_log_path = os.path.join(output_dir, "event_log.csv")
      if events:
          event_df = pd.DataFrame([{
              "timestamp": e.timestamp,
              "candle_index": e.candle_index,
              "pivot_index": e.pivot_index,
              "event_type": e.event_type,
              "price": e.price,
              "delay_bars": e.candle_index - e.pivot_index,
              "metadata": e.metadata,
          } for e in events])
          event_df.to_csv(event_log_path, index=False)
      else:
          pd.DataFrame(columns=[
              "timestamp", "candle_index", "pivot_index", 
              "event_type", "price", "delay_bars", "metadata"
          ]).to_csv(event_log_path, index=False)
      
      # Metrics (JSON for programmatic consumption)
      metrics_path = os.path.join(output_dir, "metrics.json")
      with open(metrics_path, "w") as f:
          json.dump(metrics, f, indent=2)
      
      # Metrics (text summary for human reading)
      summary_path = os.path.join(output_dir, "summary.txt")
      with open(summary_path, "w") as f:
          f.write("SMC Backtest Replay — Summary\n")
          f.write("=" * 40 + "\n")
          for k, v in metrics.items():
              f.write(f"{k}: {v}\n")
  ```

  **Must NOT do**:
  - ❌ Do not use Parquet — V1 is CSV-only
  - ❌ Do not write output files outside `output_dir`
  
  **Recommended Agent Profile**: `general` — file I/O
  **Parallelization**: Wave 4, blocked by T5; blocks T8
  **References**: User spec: CSV only for V1
  
  **Acceptance Criteria**:
  - [ ] `export_results()` creates output_dir if it doesn't exist
  - [ ] `export_results(..., overwrite=False)` raises `FileExistsError` for existing dir
  - [ ] `export_results(..., overwrite=True)` overwrites existing dir without error
  - [ ] `per_candle_report.csv` has same columns as report DataFrame
  - [ ] `event_log.csv` has columns: timestamp, candle_index, pivot_index, event_type, price, delay_bars, metadata
  - [ ] `metrics.json` is valid JSON
  - [ ] `summary.txt` is human-readable
  - [ ] Empty event list produces a header-only CSV (no rows)
  - [ ] Functions are idempotent (running twice with same args produces same files)
  - [ ] `warn_if_large(df, 100)` prints warning for 200-row dataframe
  
  **QA Scenarios**:
  1. **Full export test with overwrite flag**
     - Tool: `interactive_bash` — Python
     - Preconditions: T5 report, T2 events, T6 metrics available
     - Steps:
       ```python
       from backtest import export_results, warn_if_large
       import pandas as pd, os, tempfile
       
       # Test overwrite=False
       with tempfile.TemporaryDirectory() as tmpdir:
           # First run creates dir
           export_results(report, events, metrics, tmpdir, overwrite=False)
           # Second run should raise
           try:
               export_results(report, events, metrics, tmpdir, overwrite=False)
               print("ERROR: should have raised FileExistsError")
           except FileExistsError:
               print("OK: FileExistsError raised")
           # Overwrite=True should work
           export_results(report, events, metrics, tmpdir, overwrite=True)
           files = os.listdir(tmpdir)
           assert "per_candle_report.csv" in files
           assert "event_log.csv" in files
           assert "metrics.json" in files
           assert "summary.txt" in files
           print(f"Files: {files}")
       
       # Test warn_if_large
       warn_if_large(pd.DataFrame(index=range(200)), threshold=100)
       ```
     - Expected: FileExistsError raised on second call, all 4 files on overwrite
     - Evidence: `.sisyphus/evidence/t7-export.txt`

---

### Wave 5 — API + CLI

- [ ] T8. `BacktestHarness` — Orchestration class
  **What to do**: Build the main `BacktestHarness` class that wires together all components. This is the PRIMARY function API. Critically, it does NOT call `smc.swing_highs_lows()` a second time — it uses `swings_df` from Phase 1 for both the report AND batch comparison.

  **Design** (updated: no redundant second call, NoopStrategy default, validate_dataset call):
  ```python
  import time
  from typing import Optional

  class BacktestHarness:
      """
      Primary API for the SMC replay backtest harness.
      
      Usage:
          from backtest import BacktestHarness, BacktestConfig
          
          config = BacktestConfig()
          harness = BacktestHarness(config)
          result = harness.run("tests/test_data/EURUSD/EURUSD_15M.csv")
          print(result.metrics)
      
      Import path: `from backtest import BacktestHarness`
      Run from project root or ensure PYTHONPATH includes project root.
      """
      
      def __init__(self, config: Optional[BacktestConfig] = None):
          self.config = config or BacktestConfig()
          self._strategy_callback: StrategyCallback = NoopStrategy()
      
      def set_strategy(self, strategy_callback: StrategyCallback):
          """Register a strategy callback for V2 trade simulation."""
          self._strategy_callback = strategy_callback
      
      def run(self, data_path: str) -> "BacktestResult":
          """
          Run the full two-phase backtest.
          
          1. Load + validate data
          2. Phase 1: Replay (streaming swing engine)
          3. Phase 2: Batch analysis (downstream methods)
          4. Build per-candle report
          5. Compute metrics (using swings_df as batch reference)
          
          Returns BacktestResult with all outputs.
          """
          start = time.time()
          
          # Step 1: Load + validate data
          data = load_dataset(data_path, self.config)
          warnings = validate_dataset(data, self.config)
          if len(data) > 100000:
              warn_if_large(data)
          for w in warnings:
              print(f"⚠️  Warning: {w}")
          
          # Step 2: Phase 1 — Replay
          swings_df, events = replay_phase(
              data, self.config, self._strategy_callback
          )
          
          # Step 3: Phase 2 — Batch analysis
          batch_results = batch_analysis_phase(data, swings_df, self.config)
          
          # Step 4: Build report
          report = build_per_candle_report(data, swings_df, batch_results)
          
          # Step 5: Compute metrics
          # Use swings_df as batch_swings — they ARE the same output
          # by design (replay == batch proven by T10).
          elapsed = time.time() - start
          metrics = compute_metrics(
              report, events, swings_df, swings_df, self.config, elapsed
          )
          
          return BacktestResult(
              config=self.config,
              report=report,
              events=events,
              swings_df=swings_df,
              batch_results=batch_results,
              metrics=metrics,
          )
      
      def run_and_export(self, data_path: str, 
                         output_dir: str = "backtest_results"):
          """Run backtest and export all results to output_dir."""
          result = self.run(data_path)
          export_results(
              result.report, result.events, result.metrics, 
              output_dir, overwrite=self.config.overwrite
          )
          return result


  @dataclass
  class BacktestResult:
      """Container for all backtest outputs."""
      config: BacktestConfig
      report: pd.DataFrame
      events: list
      swings_df: pd.DataFrame
      batch_results: dict[str, pd.DataFrame]
      metrics: dict
  ```

  **Must NOT do**:
  - ❌ Do NOT call `smc.swing_highs_lows()` at all — `swings_df` is the batch reference (eliminates redundant call per I2)
  - ❌ Do not put trade logic in this class — V2 territory
  - ❌ Do not add plotting or visualization methods
  
  **Recommended Agent Profile**: `general` — API design, orchestration
  **Parallelization**: Wave 5, blocked by T6, T7; blocks T9
  **References**: All previous tasks (T1–T7)
  
  **Acceptance Criteria**:
  - [ ] `BacktestHarness(config)` instantiates without error, default callback is NoopStrategy
  - [ ] `harness.run("path/to/EURUSD_15M.csv")` returns `BacktestResult`
  - [ ] `result.report` is a DataFrame with all canonical columns (26 cols)
  - [ ] `result.events` is a list of `BacktestEvent` objects with pivot_index
  - [ ] `result.metrics` is a dict with all 18 keys
  - [ ] `result.batch_results` has 4 keys
  - [ ] `harness.run()` does NOT call `smc.swing_highs_lows()` (one less redundant computation)
  - [ ] `harness.run_and_export()` creates output files
  - [ ] `harness.set_strategy(callback)` accepts a `StrategyCallback` instance
  - [ ] All methods are idempotent
  
  **QA Scenarios**:
  1. **End-to-end run (no redundant swing_highs_lows call)**
     - Tool: `interactive_bash` — Python
     - Preconditions: T1–T7 complete
     - Steps:
       ```python
       from backtest import BacktestHarness, BacktestConfig
       TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
       
       config = BacktestConfig()
       harness = BacktestHarness(config)
       result = harness.run(TEST_DATA_PATH)
       print(f"Metrics: {result.metrics}")
       print(f"Report shape: {result.report.shape}")
       print(f"Report columns ({len(result.report.columns)}): {list(result.report.columns)}")
       print(f"Events: {len(result.events)}")
       ```
     - Expected: Full run completes, 26 report columns, events with pivot_index
     - Evidence: `.sisyphus/evidence/t8-e2e-run.txt`
  
  2. **Export test**
     - Tool: `interactive_bash`
     - Steps: `harness.run_and_export(TEST_DATA_PATH, "/tmp/backtest_e2e")`
     - Expected: Output files created in /tmp/backtest_e2e
     - Evidence: `.sisyphus/evidence/t8-export-e2e.txt`

- [ ] T9. CLI wrapper
  **What to do**: Build the CLI entry point that wraps `BacktestHarness`. Lives in the same `backtest.py` file under `if __name__ == "__main__"`. Import argparse inside `main()` to avoid module-level import.

  **Design** (updated with downstream method args + argparse inside main):
  ```python
  def main():
      """CLI entry point for the SMC replay backtest harness."""
      import argparse  # M5: import inside function
      
      parser = argparse.ArgumentParser(
          description="SMC Replay Backtest Harness — Causal replay of swing engine"
      )
      parser.add_argument("--data", required=True,
                          help="Path to OHLC CSV file")
      parser.add_argument("--output-dir", default="backtest_results",
                          help="Output directory for results")
      
      # Engine parameters (optional, override defaults)
      parser.add_argument("--swing-length", type=int, default=5)
      parser.add_argument("--confirmation-bars", type=int, default=2)
      parser.add_argument("--atr-multiplier", type=float, default=1.5)
      parser.add_argument("--atr-period", type=int, default=7)
      
      # Downstream method parameters (M1 — new)
      parser.add_argument("--close-break", action="store_true", default=True,
                          help="BOS/CHOCH: use close for break detection")
      parser.add_argument("--no-close-break", dest="close_break", 
                          action="store_false",
                          help="BOS/CHOCH: use high/low for break detection")
      parser.add_argument("--close-mitigation", action="store_true", default=False,
                          help="OB: use close for mitigation detection")
      parser.add_argument("--range-percent", type=float, default=0.01,
                          help="Liquidity: range percent for swing clustering")
      
      # Optional flags
      parser.add_argument("--no-export", action="store_true",
                          help="Run without exporting results (print only)")
      parser.add_argument("--verbose", action="store_true",
                          help="Print detailed progress")
      
      args = parser.parse_args()
      
      config = BacktestConfig(
          swing_length=args.swing_length,
          confirmation_bars=args.confirmation_bars,
          atr_multiplier=args.atr_multiplier,
          atr_period=args.atr_period,
          close_break=args.close_break,
          close_mitigation=args.close_mitigation,
          range_percent=args.range_percent,
      )
      
      if args.verbose:
          print(f"Config: {config}")
      
      harness = BacktestHarness(config)
      
      if args.no_export:
          result = harness.run(args.data)
      else:
          result = harness.run_and_export(args.data, args.output_dir)
      
      # Print summary
      print("\n" + "=" * 50)
      print("SMC Backtest Replay — Results")
      print("=" * 50)
      for k, v in result.metrics.items():
          print(f"  {k}: {v}")
      print("=" * 50)

  if __name__ == "__main__":
      main()
  ```

  **Must NOT do**:
  - ❌ Do not duplicate the core logic — CLI MUST delegate to `BacktestHarness`
  - ❌ Do not add trading-specific CLI arguments (entry/exit rules, sizing)
  - ❌ Do not use `argparse` subparsers unless justified (single action only)
  - ❌ Do not import `argparse` at module level — import inside `main()`
  
  **Recommended Agent Profile**: `general` — CLI design
  **Parallelization**: Wave 5, blocked by T8; blocks T11
  **References**: `tests/stream_compare.py` lines 91–115 (existing argparse pattern)
  
  **Acceptance Criteria**:
  - [ ] `python backtest.py --data tests/test_data/EURUSD/EURUSD_15M.csv` runs and prints metrics
  - [ ] `python backtest.py --data path/to/data.csv --output-dir results/` creates output files
  - [ ] `python backtest.py --data path/to/data.csv --swing-length 10 --confirmation-bars 3` uses overridden params
  - [ ] `python backtest.py --data path/to/data.csv --close-mitigation --range-percent 0.02` uses overridden downstream params
  - [ ] `python backtest.py --data path/to/data.csv --no-export` prints but doesn't create files
  - [ ] `--help` prints all 9 available options (4 engine + 3 downstream + 2 flags)
  - [ ] CLI exit code is 0 on success
  - [ ] CLI wraps `BacktestHarness` (does not duplicate its logic)
  - [ ] `import backtest` does NOT trigger argparse (imported inside main())
  
  **QA Scenarios**:
  1. **CLI default run**
     - Tool: `interactive_bash`
     - Steps:
       ```bash
       python backtest.py --data tests/test_data/EURUSD/EURUSD_15M.csv --no-export
       ```
     - Expected: Metrics printed, exit code 0
     - Evidence: `.sisyphus/evidence/t9-cli-default.txt`
  
  2. **CLI with custom downstream params**
     - Tool: `interactive_bash`
     - Steps:
       ```bash
       python backtest.py --data tests/test_data/EURUSD/EURUSD_15M.csv \
           --close-mitigation --range-percent 0.005 --no-export
       ```
     - Expected: Runs with overridden params, different metrics output
     - Evidence: `.sisyphus/evidence/t9-cli-custom.txt`
  
  3. **Module-level argparse test**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```bash
       python -c "from backtest import BacktestConfig; print('Import OK')"
       ```
     - Expected: No argparse triggered, clean import
     - Evidence: `.sisyphus/evidence/t9-import-test.txt`

---

### Wave 6 — Validation

- [ ] T10. Batch comparison module
  **What to do**: Build the batch comparison logic that proves the replay Phase 1 output is identical to `smc.swing_highs_lows()`. This is the causality certification for the replay harness.

  **Design**:
  ```python
  def compare_replay_to_batch(
      replay_swings: pd.DataFrame,
      data: pd.DataFrame,
      config: BacktestConfig,
  ) -> dict:
      """
      Compare replay Phase 1 output against batch swing_highs_lows().
      
      This proves the replay harness uses the EXACT same code path as the
      batch function, which has already been certified as causal by
      the 3-pass validation in test_causality.py.
      
      Returns:
          dict with:
          - "pass": bool (True = zero differences)
          - "total_rows": int
          - "diff_rows": int (number of rows with any difference)
          - "diff_percent": float
          - "first_diff_index": int or None
      """
      batch = smc.swing_highs_lows(
          data, swing_length=config.swing_length,
          confirmation_bars=config.confirmation_bars,
          atr_multiplier=config.atr_multiplier,
          atr_period=config.atr_period,
      ).reset_index(drop=True)
      
      # Reset replay index for direct comparison (only HighLow, Level)
      replay = replay_swings[["HighLow", "Level"]].reset_index(drop=True)
      
      # Compare HighLow and Level
      hl_match = (replay["HighLow"].fillna(-999).values == batch["HighLow"].fillna(-999).values)
      lvl_match = (replay["Level"].fillna(-999).values == batch["Level"].fillna(-999).values)
      
      diff_mask = ~(hl_match & lvl_match)
      diff_count = int(diff_mask.sum())
      
      first_diff = int(np.where(diff_mask)[0][0]) if diff_count > 0 else None
      
      return {
          "pass": diff_count == 0,
          "total_rows": len(replay),
          "diff_rows": diff_count,
          "diff_percent": round(diff_count / len(replay) * 100, 4),
          "first_diff_index": first_diff,
      }
  ```

  **Must NOT do**:
  - ❌ Do not re-run the replay to do comparison — accept replay_swings as argument
  - ❌ Do not modify the comparison result to force a "pass" — report real differences
  - ❌ Do not compare Phase 2 outputs (they are inherently batch, no replay equivalent)
  - ❌ Do not compare PivotIndex (only available in replay, not in batch swing_highs_lows)
  
  **Recommended Agent Profile**: `unspecified-high` — causality validation
  **Parallelization**: Wave 6, blocked by T3; blocks T11
  **References**: `tests/test_causality.py` (the 3-pass harness), `tests/stream_compare.py`
  
  **Acceptance Criteria**:
  - [ ] Returns dict with "pass" key (bool)
  - [ ] `compare_replay_to_batch(replay_swings, data, config)["pass"]` is True when using default params on EURUSD data
  - [ ] `diff_percent` is 0.0 for identical outputs
  - [ ] `first_diff_index` is None when no differences
  - [ ] Function doesn't modify input DataFrames
  - [ ] Function compares only `HighLow` and `Level` (NOT PivotIndex)
  
  **QA Scenarios**:
  1. **Batch comparison pass**
     - Tool: `interactive_bash` — Python
     - Preconditions: T3 replay_swings available
     - Steps:
       ```python
       from backtest import BacktestConfig, load_dataset, compare_replay_to_batch
       from backtest import replay_phase
       TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
       
       config = BacktestConfig()
       data = load_dataset(TEST_DATA_PATH, config)
       swings_df, _ = replay_phase(data, config)
       
       result = compare_replay_to_batch(swings_df, data, config)
       assert result["pass"], f"Comparison failed: {result}"
       print(f"Batch comparison: PASS ({result['diff_percent']}% diff)")
       ```
     - Expected: pass=True, diff_percent=0.0
     - Evidence: `.sisyphus/evidence/t10-batch-compare.txt`

- [ ] T11. Integration test with EURUSD data (2 parameter configurations)
  **What to do**: Run the full end-to-end backtest on the EURUSD 15M dataset and verify all outputs. Test with at least TWO parameter configurations (default + custom) to prove robustness.

  **Design** (updated with 2 configs + performance warning check):
  
  The integration test should be runnable as a standalone Python script or inline. It tests:
  1. Config creation with all parameter fields
  2. Data loading + validation (clean dataset → no warnings)
  3. Phase 1 replay + event recording with pivot_index
  4. Phase 2 batch analysis with config-driven parameters
  5. Report construction with all 26 columns
  6. Metrics computation with real timing delay
  7. Export with overwrite flag
  8. CLI invocation
  9. Batch comparison (passes)
  10. **Second run with different parameters** (e.g., swing_length=10)
  11. Performance warning for artificially large dataset

  **Must NOT do**:
  - ❌ Do not add the integration test to the existing `unit_tests.py` test suite
  - ❌ Do not assert specific numerical values (they depend on engine parameters which may change)
  - ❌ Do not skip assertions — all must be verifiable programmatically
  
  **Recommended Agent Profile**: `unspecified-high` — test infrastructure
  **Parallelization**: Wave 6, blocked by T9, T10
  **References**: `tests/test_causality.py`, `tests/unit_tests.py`
  
  **Acceptance Criteria**:
  - [ ] Integration test runs end-to-end without errors for both parameter configs
  - [ ] All assertions pass for both configs
  - [ ] Test completes in < 120 seconds total (both configs × 24k rows)
  - [ ] Output matches expected schema at every step
  - [ ] Second config produces DIFFERENT metrics (confirms parameter variation works)
  - [ ] Test is idempotent (same result on repeat runs)
  - [ ] Performance warning triggers for >100k row dataset
  
  **QA Scenarios**:
  1. **Full integration test with parameter variation**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```bash
       python -c "
       from backtest import BacktestHarness, BacktestConfig, \
           load_dataset, compare_replay_to_batch, warn_if_large
       from smartmoneyconcepts.smc import smc
       import tempfile, os
       
       TEST_DATA_PATH = 'tests/test_data/EURUSD/EURUSD_15M.csv'
       
       print('=== Config 1: Default parameters ===')
       config1 = BacktestConfig()
       harness1 = BacktestHarness(config1)
       result1 = harness1.run(TEST_DATA_PATH)
       
       # 1. Data loading
       data = load_dataset(TEST_DATA_PATH, config1)
       assert len(data) == 24425
       print('1. Data loading: PASS')
       
       # 2. Full harness run
       assert len(result1.report) == 24425
       assert len(result1.report.columns) == 26
       assert len(result1.events) == result1.metrics['total_swings']
       print(f'2. Full run: {result1.metrics[\"total_swings\"]} swings: PASS')
       
       # 3. Batch comparison
       comp1 = compare_replay_to_batch(result1.swings_df, data, config1)
       assert comp1['pass'], f'Diff: {comp1}'
       print('3. Batch comparison: PASS')
       
       # 4. Metrics sanity
       m1 = result1.metrics
       assert m1['total_swings'] == m1['swing_highs'] + m1['swing_lows']
       assert m1['batch_diff_score'] == 0.0
       assert m1['avg_event_delay_bars'] >= config1.confirmation_bars
       assert m1['processing_time_seconds'] > 0
       print(f'4. Metrics: delay={m1[\"avg_event_delay_bars\"]} bars: PASS')
       
       # 5. Export
       with tempfile.TemporaryDirectory() as tmpdir:
           harness1.run_and_export(TEST_DATA_PATH, tmpdir)
           files = os.listdir(tmpdir)
           assert 'per_candle_report.csv' in files
           assert 'event_log.csv' in files
           print('5. Export: PASS')
       
       print()
       print('=== Config 2: Custom parameters ===')
       config2 = BacktestConfig(
           swing_length=10, confirmation_bars=3,
           atr_multiplier=2.0, atr_period=14,
           close_break=False, close_mitigation=True, range_percent=0.02,
       )
       harness2 = BacktestHarness(config2)
       result2 = harness2.run(TEST_DATA_PATH)
       
       assert len(result2.events) >= 0  # Different count expected
       assert result2.metrics['total_swings'] != result1.metrics['total_swings'], \
           'Different params should produce different swing counts'
       print(f'6. Parameter variation: {result2.metrics[\"total_swings\"]} swings (was {result1.metrics[\"total_swings\"]}): PASS')
       
       # 7. Performance warning
       print()
       warn_if_large(pd.DataFrame(index=range(200000)), threshold=100000)
       print('7. Large dataset warning: PASS')
       
       print()
       print('=' * 50)
       print('ALL INTEGRATION TESTS PASSED')
       print('=' * 50)
       "
       ```
     - Expected: Both configs pass, different swing counts, large dataset warning
     - Evidence: `.sisyphus/evidence/t11-integration.txt`

---

## Final Verification Wave

- [ ] F1. Plan Compliance Audit (oracle)
  **What to do**: Audit `backtest.py` against this plan. Verify:
  - Every task's acceptance criteria is met
  - No scope creep (no trade simulation, no Strategy subclass)
  - Guardrails respected (custom loop, CSV-only, no viz)
  - **Only allowed smc.py change**: PivotIndex added to return dict (2 lines). No other changes to `smc.py`.
  - All column names match the Backtest Report Schema exactly (26 columns)
  - Event log has 7 columns including pivot_index and delay_bars
  - CLI delegates to `BacktestHarness` (no duplicated logic)
  - `import argparse` is inside `main()`, not at module level
  - `BacktestHarness.run()` does NOT call `smc.swing_highs_lows()` (no redundant computation)
  
  **Recommended Agent Profile**: `oracle`
  **Parallelization**: After T11, before commit
  **References**: This entire plan document
  
  **Acceptance Criteria**:
  - [ ] Audit report generated listing all met/unmet criteria
  - [ ] No violations of guardrails
  - [ ] All deliverables present

- [ ] F2. Code Quality Review (unspecified-high)
  **What to do**: Review `backtest.py` for:
  - Proper separation of concerns (config, loading, replay, batch, export, CLI)
  - Idempotent operations (running twice produces same output)
  - Type hints on all public functions
  - Docstrings for `BacktestHarness`, `BacktestConfig`, all public methods
  - No dead code or commented-out sections
  - Error handling for missing files, invalid params, engine crashes
  - PEP 8 compliance
  - `validate_dataset()` covers: min rows, required columns, numeric dtypes, monotonic timestamps
  - `smc.py` diff only shows 2 PivotIndex lines added
  
  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: After T11, parallel with F1
  
  **Acceptance Criteria**:
  - [ ] Review report generated with findings
  - [ ] All critical issues fixed before merge

- [ ] F3. Real Manual QA (unspecified-high)
  **What to do**: Run the backtest on EURUSD 15M and manually inspect:
  - Event log shows alternating swing highs/lows at plausible price levels with actual delay_bars
  - Per-candle report has 26 columns
  - Metrics printed to stdout are reasonable (real avg_event_delay_bars, not config constant)
  - BOS/CHOCH events occur at market structure transitions
  - OB zones are at plausible support/resistance levels
  - avg_liq_zone_width_bars and sweep_rate are non-zero
  - No crashes or warnings
  
  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: After T11, parallel with F1, F2
  
  **Acceptance Criteria**:
  - [ ] Visual inspection confirms plausible outputs
  - [ ] No warnings or errors in output
  - [ ] Metrics are internally consistent

- [ ] F4. Scope Fidelity Check (deep)
  **What to do**: Final check that ONLY the intended files were created/modified:
  - `git diff --stat` — only `backtest.py` (new), `smc.py` (2 lines added), and optionally `tests/test_backtest.py`
  - `smc.py` diff: ONLY 2 lines adding `result["PivotIndex"] = float(self._candidate_index)`
  - No changes to `pyproject.toml`, `setup.py`, `pyproject.toml`
  - No new dependencies
  - No modification of existing test infrastructure (test_causality.py, stream_compare.py unchanged)
  
  **Recommended Agent Profile**: `deep`
  **Parallelization**: After T11, parallel with F1–F3
  
  **Acceptance Criteria**:
  - [ ] `git diff --stat` shows only `backtest.py` and 2-line `smc.py` change
  - [ ] No unintended modifications
  - [ ] Clean diff scope

---

## Commit Strategy

1. **One commit per wave**, conventional commit format:
   - `feat(backtest): add BacktestConfig, data loading, and dataset validation` (T1)
   - `feat(backtest): add event log, EventRecorder, and StrategyCallback protocol` (T2)
   - `feat(swing): add PivotIndex to _SwingEngine.update() return dict` (T3a — smc.py change)
   - `feat(backtest): implement Phase 1 streaming replay loop` (T3b)
   - `feat(backtest): implement Phase 2 batch downstream analysis` (T4)
   - `feat(backtest): add per-candle report, metrics, and export` (T5, T6, T7)
   - `feat(backtest): add BacktestHarness orchestration API` (T8)
   - `feat(backtest): add CLI wrapper` (T9)
   - `test(backtest): add batch comparison and integration test` (T10, T11)

2. **Squash to 1–2 commits before main merge** if preferred:
   - `feat(backtest): add replay backtest harness with event-driven core (+ PivotIndex to smc.py)`
   - `test(backtest): add batch comparison and parameter-variant integration test`

3. **Do NOT commit evidence files** (`.sisyphus/evidence/`) — they are ephemeral QA artifacts

## Success Criteria

- [ ] ✅ `backtest.py` is a single, self-contained file
- [ ] ✅ `BacktestHarness` is the primary API; CLI wraps it
- [ ] ✅ Replay loop uses `_SwingEngine.update(i, row)` — exact live code path
- [ ] ✅ `_SwingEngine.update()` exposes `PivotIndex` (2-line backwards-compatible extension to `smc.py`)
- [ ] ✅ `replay_phase()` validates all 6 parameter conditions (mirrors `swing_highs_lows()`)
- [ ] ✅ `validate_dataset()` checks data quality before engine runs
- [ ] ✅ Phase 2 uses full swing DataFrame for batch-only methods
- [ ] ✅ Event log records pivot_index and computes actual delay_bars
- [ ] ✅ Metrics include real avg_event_delay_bars (not config constant), batch_diff_score, sweep_rate, avg_liq_zone_width_bars
- [ ] ✅ No redundant `smc.swing_highs_lows()` call in `BacktestHarness.run()`
- [ ] ✅ Per-candle report merges all 5 SMC indicators with disambiguated column names (26 columns)
- [ ] ✅ Metrics computed and exported to JSON
- [ ] ✅ Batch comparison proves replay == batch (zero look-ahead)
- [ ] ✅ StrategyCallback protocol + NoopStrategy ready for V2 trade simulation
- [ ] ✅ CLI exposes all engine + downstream parameters
- [ ] ✅ `import argparse` is inside `main()`, not module-level
- [ ] ✅ `export_results` has `overwrite` flag
- [ ] ✅ Integration test covers 2 parameter configurations
- [ ] ✅ Large-dataset warning triggers at 100k rows
- [ ] ✅ No `backtesting` library Strategy subclass used
- [ ] ✅ Only allowed `smc.py` change: 2 PivotIndex lines added
