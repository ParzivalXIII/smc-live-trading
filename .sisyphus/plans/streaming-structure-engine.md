# Streaming StructureEngine + Cross-Market Baseline

## TL;DR

> **Quick Summary**: Two-part work: (1) Add trade duration metrics and run BOSFlipStrategy as baseline on all 5 datasets, (2) Build a two-stage causal streaming `StructureEngine` that detects BOS/CHOCH patterns provisionally on swing completion, then confirms or cancels them based on price-level breaks within a configurable confirmation window. Replaces Phase 2 batch dependency for the strategy with purely causal streaming events.

> **Deliverables**:
> - `compute_trade_metrics()` extended with `avg_trade_bars` / `median_trade_bars`
> - Cross-market comparison table (5 datasets) saved to `.sisyphus/evidence/bosflip-crossmarket/`
> - `StructureEngine` class with two-stage logic (provisional → confirmed/cancelled)
> - Integration: StructureEngine into Phase 1 replay loop (both `update()` and `check_confirmations()`)
> - `BacktestConfig` with `bos_confirmation_window: int = 10`
> - `BOSFlipStrategy` updated to consume confirmed streaming events (backward-compatible)
> - Unit tests + integration test for streaming vs batch comparison

> **Estimated Effort**: Medium (Part 1 = Quick, 1-2 hrs; Part 2 = Medium, 5-7 hrs)

> **Parallel Execution**: YES — 4 waves (Wave 0: Part 1 metric+baseline; Wave 1: StructureEngine core with two-stage logic; Wave 2: Integration + Strategy; Wave 3: Tests)

> **Critical Path**: T4 (StructureEngine impl) → T5 (integration) → T6 (strategy update) → T7+T8 (tests)

---

## Context

### Original Request
Build a streaming `StructureEngine` that detects BOS/CHOCH causally from swing events (no look-ahead), and before that, gather baseline performance data from the current batch-based BOSFlipStrategy on all 5 datasets.

### Design Decisions (Resolved)

**Q1: BOS emission index** → **Option B (Two-stage: provisional + confirm)**
- Stage 1: When the 4-swing pattern completes, emit a **provisional** BOS/CHOCH event
- Stage 2: When price subsequently breaks through the swing level, **confirm** the BOS/CHOCH
- Mirrors the batch `bos_choch()` BrokenIndex semantics in streaming form
- Status flow: `provisional` → `confirmed` (on level break) OR `provisional` → `cancelled` (on window expiry)

**Q2: Unbroken signals** → **Option B (Confirmation window)**
- If price doesn't break the level within `bos_confirmation_window` bars of provisional emission, the BOS is **cancelled**
- Configurable via `BacktestConfig.bos_confirmation_window: int = 10`
- Keeps signal volume close to batch output

### Known Limitations — Timing Gap

**Break-in-the-gap blind spot**: There is a structurally unavoidable timing gap between batch and streaming BOS detection.

Batch `bos_choch()` scans for price-level breaks starting from `S2 + 2` bars (the 3rd swing + 2). The streaming engine cannot begin scanning until `S3` (the 4th swing completes the pattern and the provisional event is emitted).

```
Timeline:
S0─────S1─────S2╎╎╎╎╎╎S3─────S3+5
                ╎     ^
                ╎     streaming checks from here (S3 onward)
                ╎
                batch checks from here (S2+2 onward)
```

Any price-level break that occurs in the gap `[S2 + 2, S3 - 1]` is **permanently missed** by the streaming engine — the pattern hasn't been detected yet, so there's nothing to check.

**Impact**: Streaming confirmed BOS count will be slightly lower than batch BOS count. The gap is larger when swings are wide (many bars between S2 and S3) and smaller when swings are tight.

The `match_rate >= 0.8` threshold in T8 is a starting estimate. Actual match rate depends on average swing spacing for each dataset and may need tuning per market (e.g., BTCUSDT 4H may have tighter swings than EURUSD 15M).

**End-of-dataset limbo**: Any provisional events emitted within the last `bos_confirmation_window` bars of the dataset will never be confirmed or cancelled — they remain in "provisional" limbo. These are expected and tracked in T8's statistics.

**close_break asymmetry**: Batch `bos_choch()` uses `close_break` (default True): `ohlc["close"] > level`. The streaming engine uses `high > level` unconditionally (more permissive). This means streaming may confirm events that batch would not (because close was below level even though high spiked above). The reverse is also true: if close breaks but high doesn't, batch confirms but streaming misses it. The `high`-based check is the natural choice for causal streaming (you know the high at bar close).

### Interview Summary
- **BOS/CHOCH logic** (smc.py:400-528): 4-swing pattern detector. Bullish BOS = [-1,1,-1,1] with L0<L2<L1<L3. Bearish BOS = [1,-1,1,-1] with L0>L2>L1>L3. Bullish CHOCH = [-1,1,-1,1] with L3>L1>L0>L2. Bearish CHOCH = [1,-1,1,-1] with L3<L1<L0<L2. Level stamped = `level_order[-3]` (2nd swing level). Index stamped = `last_positions[-2]` (3rd swing index).
- **Non-causal part**: `BrokenIndex` forward-scans from `i+2` for price break. Removes unbroken patterns. Two-stage streaming engine replicates this causally.
- **Dataset formats**: Crypto uses `time` (Unix timestamp); EURUSD uses `Date` (`%Y.%m.%d %H:%M:%S`). Different `BacktestConfig` needed.
- **BOSFlipStrategy**: Reads `row["BOS"]` from per_candle_report (batch). Flips position on ±1 signal.
- **compute_trade_metrics()**: Has `entry_index` and `exit_index`. Duration = `exit_index - entry_index`.

### Metis Review (Self Gap Analysis)

| Gap | Type | Resolution |
|-----|------|------------|
| Crypto datasets: `date_column="time"` vs default `"Date"` | **Ambiguous** | Per-dataset config in runner script |
| BOS index delta: batch 3rd swing vs streaming 4th swing | **Critical** | RESOLVED: Two-stage — provisional at 4th swing, confirm on break |
| BrokenIndex forward scan replication | **Critical** | RESOLVED: confirmation window + `check_confirmations()` per bar |
| Phase 2 batch bos_choch() — keep or replace? | **Minor** | Keep for per_candle_report. Streaming is additive. |
| OB/Liquidity streaming — in scope? | **Minor** | Not in scope. Phase 2 batch suffices. |

### Guardrails
- StructureEngine MUST be purely causal — zero forward look-ahead
- StructureEngine MUST NOT modify or replace `_SwingEngine`
- Batch `bos_choch()` MUST continue working for backward compatibility
- BOSFlipStrategy MUST remain usable with batch path (the strategy protocol update is additive)
- Must NOT introduce new dependencies (no additional libraries)

---

## Work Objectives

### Core Objective
Eliminate the Phase 2 batch dependency for the trading strategy by building a two-stage causal streaming BOS/CHOCH detector (provisional → confirmed/cancelled), and establish baseline metrics from the current implementation.

### Concrete Deliverables
1. `compute_trade_metrics()` enhanced with `avg_trade_bars` and `median_trade_bars`
2. Cross-market comparison table (all 5 datasets, 7 metrics per dataset)
3. `SwingConfirmed` and `StructureEvent` (with `status` field) dataclasses in `smartmoneyconcepts/structures.py`
4. `StructureEngine` class with two-stage logic:
   - `update(swing)` → pattern detection, emits provisional events
   - `check_confirmations(index, high, low)` → confirms or cancels provisional events
5. `BacktestConfig.bos_confirmation_window: int = 10`
6. Integration: StructureEngine wired into Phase 1 replay loop (both methods called each bar)
7. `StrategyCallback` protocol updated with `structure_events` parameter
8. `BOSFlipStrategy` updated to consume only **confirmed** `StructureEvent` objects
9. Unit tests for all status transitions (provisional→confirmed, provisional→cancelled, provisional→pending)
10. Integration test: streaming vs batch BOS comparison

### Definition of Done
- [ ] `compute_trade_metrics()` returns `avg_trade_bars` and `median_trade_bars` (non-NaN for any trades)
- [ ] Cross-market comparison table generated with all 5 datasets, 7 metrics each
- [ ] `StructureEngine.update()` emits provisional `StructureEvent` for all 4 pattern types
- [ ] `StructureEngine.check_confirmations()` confirms/cancels events based on price-break / expiry
- [ ] `BacktestConfig` has `bos_confirmation_window: int = 10`
- [ ] StructureEngine is called per-bar in replay loop (both `update()` and `check_confirmations()`)
- [ ] `BOSFlipStrategy` reacts to confirmed streaming events and produces trades
- [ ] All unit tests pass (4 pattern types + 3 status transitions + dedup)
- [ ] Integration test passes (streaming vs batch, end-to-end)

### Must Have
- Part 1: Trade duration metrics, cross-market run with results saved
- Part 2: Two-stage `StructureEngine`, Integration into replay loop, Strategy update, Tests

### Must NOT Have (Guardrails)
- DO NOT modify `_SwingEngine` class
- DO NOT add new third-party dependencies
- DO NOT remove or alter batch `bos_choch()` (it stays for per_candle_report)
- DO NOT implement streaming OB, liquidity, or retracements (not in scope)
- DO NOT change the `BacktestHarness.run()` public API (only internal changes)
- DO NOT use forward-looking information (StructureEngine must be purely causal)
- DO NOT hardcode the confirmation window — must be configurable

---

## Verification Strategy

**Test decision**: Unit tests + Integration test (TDD for StructureEngine)
**QA policy**: All verification is agent-executed. No manual inspection.

- StructureEngine unit tests: pytest, deterministic inputs, test all status transitions
- Integration test: Full backtest on EURUSD 15M, compare streaming confirmed events vs batch BOS
- Cross-market run: Python script + bash, results saved to `.sisyphus/evidence/`

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0 (Quick — Part 1):
  T1 ───┬──> T2 ──> T3
         │
  T0 (prep: verify tests pass)

Wave 1 (Core — Part 2):
  T4: StructureEngine with two-stage logic
  T5: Integration into replay_phase()

Wave 2 (Strategy — Part 2):
  T6: StrategyCallback + BOSFlipStrategy update  (blocks on T5)

Wave 3 (Verification — Part 2):
  T7: Unit tests for StructureEngine (can run parallel with T5)
  T8: Integration test (blocks on T6)
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| T0: Verify current tests pass | — | T1, T4 |
| T1: Add trade duration metrics | T0 | T2 |
| T2: Cross-market runner | T1 | T3 |
| T3: Generate comparison table | T2 | — |
| T4: StructureEngine impl (two-stage) | T0 | T5, T7 |
| T5: Integration into replay | T4 | T6 |
| T6: Strategy update | T5 | T8 |
| T7: Unit tests | T4 | T8 |
| T8: Integration test | T5, T6, T7 | — |
| F1-F4: Final verification | T3, T8 | — |

### Agent Dispatch Summary

| Task | Agent Profile | Rationale |
|------|--------------|-----------|
| T0 | developer | Run test suite |
| T1 | developer | Simple Python edit |
| T2 | developer | Script writing, data processing |
| T3 | deep | Data analysis, table formatting |
| T4 | architect | Two-stage class design, pattern + confirmation logic |
| T5 | developer | Integrate engine into existing loop |
| T6 | developer | Protocol update, strategy update |
| T7 | tester | Unit tests covering all status transitions |
| T8 | deep | Integration test, cross-validation |

---

## TODOs

### Wave 0 — Preparation

---

- [ ] **T0. Verify current test suite passes**

  **What to do**: Run the existing test suite to establish baseline. Ensure all causality tests, unit tests, and the BOSFlipStrategy integration test pass before any changes.

  **Must NOT do**: Do not modify any files.

  **Acceptance Criteria**:
  - `pytest tests/ -v` returns exit code 0, all tests pass
  - Backtest on EURUSD completes without errors

  **QA Scenarios**:
  - **Tool**: `interactive_bash`
  - **Steps**: Run `python -m pytest tests/ -v`
  - **Expected**: Exit code 0, no FAILED or ERROR
  - **Evidence**: `.sisyphus/evidence/t0-test-suite.txt`

---

### Wave 0 — Part 1: Baseline Metrics

---

- [ ] **T1. Add `avg_trade_bars` and `median_trade_bars` to `compute_trade_metrics()`**

  **What to do**: Edit `backtest.py`, function `compute_trade_metrics()` (lines 697-768).

  1. After the `max_drawdown` computation block, add:
     ```python
     # Trade duration in bars
     if not trades_df.empty and "entry_index" in trades_df.columns and "exit_index" in trades_df.columns:
         durations = trades_df["exit_index"].values - trades_df["entry_index"].values
         avg_trade_bars = round(float(np.mean(durations)), 2)
         median_trade_bars = round(float(np.median(durations)), 2)
     else:
         avg_trade_bars = float("nan")
         median_trade_bars = float("nan")
     ```
  2. Add `"avg_trade_bars"` and `"median_trade_bars"` to the returned dict (after `max_drawdown`)
  3. In the empty trades return block (lines 722-733), add `"avg_trade_bars": float("nan")` and `"median_trade_bars": float("nan")`

  **Must NOT do**: Do not change any existing metric computations. Do not change function signature.

  **References**: `backtest.py:697-768`

  **Acceptance Criteria**:
  - Returns dict with keys `avg_trade_bars` and `median_trade_bars`
  - With trades: values are non-NaN, correctly computed
  - Without trades: values are `float("nan")`

  **QA Scenarios**:
  - **Tool**: `interactive_bash`
  - **Steps**:
    ```python
    import pandas as pd, numpy as np
    from backtest import compute_trade_metrics
    df = pd.DataFrame({"entry_index": [0, 10, 5], "exit_index": [5, 20, 15], "pnl": [10, -5, 3]})
    result = compute_trade_metrics(df, pd.Series([10, 5, 8]))
    assert result["avg_trade_bars"] == 8.33  # mean of [5, 10, 10]
    assert result["median_trade_bars"] == 10.0  # median of [5, 10, 10]
    empty = compute_trade_metrics(pd.DataFrame(), pd.Series(dtype=float))
    assert np.isnan(empty["avg_trade_bars"])
    assert np.isnan(empty["median_trade_bars"])
    print("ALL PASSED")
    ```
  - **Evidence**: `.sisyphus/evidence/t1-trade-duration-metrics.txt`

---

- [ ] **T2. Create cross-market runner script**

  **What to do**: Write a Python script at `scripts/run_bosflip_crossmarket.py` that:

  1. Defines 5 datasets with per-dataset config:
     ```python
     DATASETS = [
         {"path": "tests/test_data/cryptocurrencies/binance_api_BTCUSDT_4h.csv", "name": "BTCUSDT-4H", "date_column": "time", "date_format": None},
         {"path": "tests/test_data/cryptocurrencies/binance_api_SOLUSDT_4h.csv", "name": "SOLUSDT-4H", "date_column": "time", "date_format": None},
         {"path": "tests/test_data/cryptocurrencies/binance_api_ADAUSDT_4h.csv", "name": "ADAUSDT-4H", "date_column": "time", "date_format": None},
         {"path": "tests/test_data/cryptocurrencies/binance_api_BNBUSDT_4h.csv", "name": "BNBUSDT-4H", "date_column": "time", "date_format": None},
         {"path": "tests/test_data/EURUSD/EURUSD_15M.csv", "name": "EURUSD-15M", "date_column": "Date", "date_format": "%Y.%m.%d %H:%M:%S"},
     ]
     ```
  2. For each dataset:
     - Create `BacktestConfig(date_column=d["date_column"], date_format=d["date_format"])`
     - Create `BacktestHarness(config, strategy_callback=BOSFlipStrategy())`
     - Call `harness.run(d["path"])`, extract `result.metrics`
  3. Save metrics to `.sisyphus/evidence/bosflip-crossmarket/metrics.csv` and `metrics.json`
  4. Handle errors per-dataset (don't abort the whole run)

  **Must NOT do**: Do not change any existing files. No new dependencies.

  **References**: `backtest.py:BacktestConfig`, `backtest.py:BacktestHarness.run()`, `strategies/bos_flip.py:BOSFlipStrategy`

  **Acceptance Criteria**:
  - Runs to completion on all 5 datasets
  - Output files at `.sisyphus/evidence/bosflip-crossmarket/`
  - metrics.csv contains 5 rows × 9 columns

  **QA Scenarios**:
  - **Tool**: `interactive_bash`
  - **Steps**: `python scripts/run_bosflip_crossmarket.py`
  - **Expected**: Exit code 0, files created
  - **Verification**: `python -c "import pandas as pd; df=pd.read_csv('.sisyphus/evidence/bosflip-crossmarket/metrics.csv'); assert df.shape[0]==5; print('OK')"`
  - **Evidence**: `.sisyphus/evidence/t2-crossmarket-run.txt`

---

- [ ] **T3. Generate cross-market comparison table**

  **What to do**: Format the cross-market comparison as a markdown table. Write to `.sisyphus/evidence/bosflip-crossmarket/comparison_table.md`.

  Columns: Dataset | Total Trades | Win Rate | Profit Factor | Net PnL | Avg Trade Bars | Median Trade Bars | Max DD

  **Must NOT do**: Do not fabricate data — read from saved metrics.

  **Acceptance Criteria**:
  - Markdown table exists at `.sisyphus/evidence/bosflip-crossmarket/comparison_table.md`
  - 5 data rows + header, values match T2 metrics

  **QA Scenarios**:
  - **Tool**: `interactive_bash`
  - **Steps**:
    ```python
    import pandas as pd
    df = pd.read_csv('.sisyphus/evidence/bosflip-crossmarket/metrics.csv')
    md = df.to_markdown(index=False)
    with open('.sisyphus/evidence/bosflip-crossmarket/comparison_table.md','w') as f: f.write(md)
    ```
  - **Evidence**: `.sisyphus/evidence/t3-comparison-table.txt`

---

### Wave 1 — Part 2: StructureEngine Core (Two-Stage)

---

- [ ] **T4. Implement two-stage `StructureEngine` with dataclasses**

  **What to do**: Create `smartmoneyconcepts/structures.py` with:

  #### 1. Dataclasses

  ```python
  from __future__ import annotations
  from dataclasses import dataclass
  from typing import Literal
  import pandas as pd

  @dataclass
  class SwingConfirmed:
      """Event emitted by _SwingEngine when a swing is confirmed."""
      index: int
      direction: int  # 1 = swing high, -1 = swing low
      level: float
      timestamp: pd.Timestamp
      pivot_index: int

  @dataclass
  class StructureEvent:
      """Event emitted by StructureEngine when BOS/CHOCH detected.
      
      Status flow: provisional → confirmed (on break) OR cancelled (on expiry).
      """
      event_type: str  # "BOS" or "CHOCH"
      direction: int   # 1 = bullish, -1 = bearish
      level: float
      swing_index: int          # Index of the 2nd swing (level-defining swing, same as batch's level)
      trigger_index: int        # Index of the 4th swing (when pattern was detected)
      timestamp: pd.Timestamp   # Timestamp of the trigger swing
      status: Literal["provisional", "confirmed", "cancelled"] = "provisional"
      confirmed_at_index: int | None = None  # Bar index when confirmed (null until confirmed)
  ```

  #### 2. `StructureEngine` class

  ```python
  class StructureEngine:
      """Two-stage causal streaming BOS/CHOCH detector.
      
      Stage 1 (update): On each new confirmed swing, check if the last 4 swings
          form a BOS/CHOCH pattern. If yes, emit a provisional StructureEvent.
      
      Stage 2 (check_confirmations): On each bar (called every candle), check
          all provisional events:
          - If price has broken the level → confirm (status="confirmed")
          - If bars_since >= confirmation_window → cancel (status="cancelled")
      
      Mirrors semantics of batch bos_choch() BrokenIndex, but causally.
      """
      
      def __init__(self, confirmation_window: int = 10):
          self._confirmation_window = confirmation_window
          self._swings: list[SwingConfirmed] = []
          self._provisional_events: list[StructureEvent] = []  # Not yet confirmed/cancelled
          self._all_events: list[StructureEvent] = []           # All events ever emitted
          self._emitted_keys: set[tuple] = set()                # Dedup: (type, dir, swing_idx)
      
      def update(self, swing: SwingConfirmed) -> list[StructureEvent]:
          """Stage 1: Process a confirmed swing. Return any NEWLY emitted provisional events.
          
          Checks the last 4 swings for pattern completion. If a pattern is found
          that hasn't been emitted before, creates a provisional StructureEvent.
          """
          self._swings.append(swing)
          new_events: list[StructureEvent] = []
          
          if len(self._swings) >= 4:
              last_4 = self._swings[-4:]
              directions = [s.direction for s in last_4]
              levels = [s.level for s in last_4]
              
              # Bullish BOS: pattern [-1, 1, -1, 1] with L0 < L2 < L1 < L3
              if (directions == [-1, 1, -1, 1] and
                  levels[0] < levels[2] < levels[1] < levels[3]):
                  key = ("BOS", 1, last_4[-3].index)  # dedup on (type, dir, swing_index)
                  if key not in self._emitted_keys:
                      self._emitted_keys.add(key)
                      event = StructureEvent(
                          event_type="BOS", direction=1,
                          level=levels[1],  # S1's level = level_order[-3]
                          swing_index=last_4[-3].index,  # S1's index
                          trigger_index=swing.index,     # S3's index
                          timestamp=swing.timestamp,
                          status="provisional",
                      )
                      self._provisional_events.append(event)
                      self._all_events.append(event)
                      new_events.append(event)
              
              # Bearish BOS: pattern [1, -1, 1, -1] with L0 > L2 > L1 > L3
              if (directions == [1, -1, 1, -1] and
                  levels[0] > levels[2] > levels[1] > levels[3]):
                  key = ("BOS", -1, last_4[-3].index)
                  if key not in self._emitted_keys:
                      self._emitted_keys.add(key)
                      event = StructureEvent(
                          event_type="BOS", direction=-1,
                          level=levels[1],  # S1's level
                          swing_index=last_4[-3].index,
                          trigger_index=swing.index,
                          timestamp=swing.timestamp,
                          status="provisional",
                      )
                      self._provisional_events.append(event)
                      self._all_events.append(event)
                      new_events.append(event)
              
              # Bullish CHOCH: pattern [-1, 1, -1, 1] with L3 > L1 > L0 > L2
              if (directions == [-1, 1, -1, 1] and
                  levels[3] > levels[1] > levels[0] > levels[2]):
                  key = ("CHOCH", 1, last_4[-3].index)
                  if key not in self._emitted_keys:
                      self._emitted_keys.add(key)
                      event = StructureEvent(
                          event_type="CHOCH", direction=1,
                          level=levels[1],
                          swing_index=last_4[-3].index,
                          trigger_index=swing.index,
                          timestamp=swing.timestamp,
                          status="provisional",
                      )
                      self._provisional_events.append(event)
                      self._all_events.append(event)
                      new_events.append(event)
              
              # Bearish CHOCH: pattern [1, -1, 1, -1] with L3 < L1 < L0 < L2
              if (directions == [1, -1, 1, -1] and
                  levels[3] < levels[1] < levels[0] < levels[2]):
                  key = ("CHOCH", -1, last_4[-3].index)
                  if key not in self._emitted_keys:
                      self._emitted_keys.add(key)
                      event = StructureEvent(
                          event_type="CHOCH", direction=-1,
                          level=levels[1],
                          swing_index=last_4[-3].index,
                          trigger_index=swing.index,
                          timestamp=swing.timestamp,
                          status="provisional",
                      )
                      self._provisional_events.append(event)
                      self._all_events.append(event)
                      new_events.append(event)
          
          return new_events
      
      def check_confirmations(self, index: int, high: float, low: float) -> list[StructureEvent]:
          """Stage 2: Check all provisional events for break confirmation or expiry.
          
          Called EVERY CANDLE (not just on swing confirmations).
          
          Args:
              index: Current bar index.
              high: Current bar's high price.
              low: Current bar's low price.
          
          Returns:
              List of StructureEvent objects that changed status THIS BAR.
              (confirmed or cancelled)
          """
          status_changes: list[StructureEvent] = []
          still_provisional: list[StructureEvent] = []
          
          for event in self._provisional_events:
              bars_since = index - event.trigger_index
              
              # Check for level break
              if event.event_type == "BOS":
                  if event.direction == 1:  # Bullish: price must break ABOVE the level
                      if high > event.level:
                          event.status = "confirmed"
                          event.confirmed_at_index = index
                          status_changes.append(event)
                          continue
                  elif event.direction == -1:  # Bearish: price must break BELOW the level
                      if low < event.level:
                          event.status = "confirmed"
                          event.confirmed_at_index = index
                          status_changes.append(event)
                          continue
              elif event.event_type == "CHOCH":
                  if event.direction == 1:  # Bullish CHOCH: break above level
                      if high > event.level:
                          event.status = "confirmed"
                          event.confirmed_at_index = index
                          status_changes.append(event)
                          continue
                  elif event.direction == -1:  # Bearish CHOCH: break below level
                      if low < event.level:
                          event.status = "confirmed"
                          event.confirmed_at_index = index
                          status_changes.append(event)
                          continue
              
              # Check for window expiry
              if bars_since >= self._confirmation_window:
                  event.status = "cancelled"
                  status_changes.append(event)
                  continue
              
              still_provisional.append(event)
          
          self._provisional_events = still_provisional
          return status_changes
      
      @property
      def events(self) -> list[StructureEvent]:
          """All events ever emitted by this engine (provisional, confirmed, and cancelled)."""
          return list(self._all_events)
  ```

  #### 3. Pattern detection: swing index mapping

  In the 4-swing window `_swings[-4:]`:
  - S0 = `_swings[-4]` (oldest)
  - S1 = `_swings[-3]` → `level_order[-3]` in batch AND `last_positions[-2]` in batch (where BOS is stamped) → `swing_index` in StructureEvent
  - S2 = `_swings[-2]` → batch's S2 (not the stamp index)
  - S3 = `_swings[-1]` (newest) → `trigger_index` in StructureEvent

  | Batch concept | Streaming equivalent |
  |---|---|
  | `last_positions[-2]` (stamp index) = S1 | `StructureEvent.swing_index` (S1's index) |
  | `level_order[-3]` (level) = S1's level | `StructureEvent.level` (S1's level) |
  | `broken[i] = j` (break index) | `StructureEvent.confirmed_at_index` |
  | S3 (4th swing, only used for pattern detection) | `StructureEvent.trigger_index` |

  **Dedup**: Use `("BOS", 1, s1_index)` tuple in `_emitted_keys`. Skip if already emitted. This prevents re-emitting the same BOS on overlapping 4-swing windows.

  **Must NOT do**:
  - Do NOT import from `smc` module
  - Do NOT use numpy (pure Python list operations suffice)
  - Do NOT scan forward in time — `check_confirmations()` only looks at the CURRENT bar's prices
  - Do NOT add any state beyond `_swings`, `_provisional_events`, `_all_events`, `_emitted_keys`
  - Do NOT modify any existing file

  **References**:
  - `smc.py:400-528` — pattern detection logic to replicate
  - `smc.py:131-210` — `_SwingEngine.update()` output format

  **Acceptance Criteria**:
  - `StructureEngine(confirmation_window=10)` creates instance
  - `engine.update(swing)` with <4 swings returns `[]`
  - `engine.update(swing)` with a valid 4-swing pattern returns `[StructureEvent(status="provisional")]`
  - `engine.update(swing)` with a non-pattern returns `[]`
  - All 4 pattern types (bullish/bearish BOS, bullish/bearish CHOCH) produce correct provisional events
  - Same pattern twice does NOT emit duplicates
  - `engine.check_confirmations(trigger_index+5, high=level+1, low=level-1)` confirms bullish event on high break
  - `engine.check_confirmations(trigger_index+5, high=level-1, low=level-2)` does NOT confirm (bearish needs low break)
  - `engine.check_confirmations(trigger_index+confirmation_window, high=level-1, low=level-1)` cancels on window expiry
  - `engine.check_confirmations(trigger_index+1, high=level-1, low=level-1)` keeps provisional (too early)
  - Pattern level ordering matches batch `bos_choch()` EXACTLY

  **QA Scenarios**:

  - **Tool**: `interactive_bash`
  - **Preconditions**: `smartmoneyconcepts/structures.py` exists

  - **Scenario 1: Bullish BOS → confirmed**
    ```python
    from smartmoneyconcepts.structures import StructureEngine, SwingConfirmed
    import pandas as pd
    engine = StructureEngine(confirmation_window=10)
    ts = pd.Timestamp("2024-01-01")
    # S0: swing low at 100, S1: swing high at 120, S2: swing low at 110, S3: swing high at 130
    engine.update(SwingConfirmed(10, -1, 100.0, ts, 5))
    engine.update(SwingConfirmed(20, 1, 120.0, ts, 15))
    engine.update(SwingConfirmed(30, -1, 110.0, ts, 25))
    result = engine.update(SwingConfirmed(40, 1, 130.0, ts, 35))
    assert len(result) == 1
    assert result[0].event_type == "BOS"
    assert result[0].direction == 1
    assert result[0].level == 120.0  # S1's level
    assert result[0].status == "provisional"
    assert result[0].trigger_index == 40
    # Confirm by price break: bar 42, high > 120
    confirmed = engine.check_confirmations(42, high=125.0, low=120.0)
    assert len(confirmed) == 1
    assert confirmed[0].status == "confirmed"
    assert confirmed[0].confirmed_at_index == 42
    print("Bullish BOS → confirmed: PASSED")
    ```

  - **Scenario 2: Bullish BOS → cancelled (window expiry)**
    ```python
    engine2 = StructureEngine(confirmation_window=5)
    engine2.update(SwingConfirmed(10, -1, 100.0, ts, 5))
    engine2.update(SwingConfirmed(20, 1, 120.0, ts, 15))
    engine2.update(SwingConfirmed(30, -1, 110.0, ts, 25))
    result2 = engine2.update(SwingConfirmed(40, 1, 130.0, ts, 35))
    assert result2[0].status == "provisional"
    # 5 bars later with no break → should cancel
    cancelled = engine2.check_confirmations(45, high=119.0, low=115.0)
    assert len(cancelled) == 1
    assert cancelled[0].status == "cancelled"
    print("Bullish BOS → cancelled: PASSED")
    ```

  - **Scenario 3: Provisional still pending (before expiry, no break)**
    ```python
    engine3 = StructureEngine(confirmation_window=10)
    engine3.update(SwingConfirmed(10, -1, 100.0, ts, 5))
    engine3.update(SwingConfirmed(20, 1, 120.0, ts, 15))
    engine3.update(SwingConfirmed(30, -1, 110.0, ts, 25))
    engine3.update(SwingConfirmed(40, 1, 130.0, ts, 35))
    # Bar 42: no break yet, within window
    pending = engine3.check_confirmations(42, high=119.0, low=115.0)
    assert len(pending) == 0  # No status changes
    print("Provisional still pending: PASSED")
    ```

  - **Evidence**: `.sisyphus/evidence/t4-two-stage-engine.txt`

---

- [ ] **T5. Integrate two-stage engine into `replay_phase()` and `BacktestConfig`**

  **What to do**: Edit `backtest.py`:

  **Edit 1** — Add `bos_confirmation_window` to `BacktestConfig` (line 42-82):
  ```python
  @dataclass
  class BacktestConfig:
      # ... existing fields ...
      bos_confirmation_window: int = 10
  ```

  **Edit 2** — Add import at top:
  ```python
  from smartmoneyconcepts.structures import StructureEngine, SwingConfirmed
  ```

  **Edit 3** — Modify `replay_phase()` (lines 335-434):

  The replay loop must call BOTH:
  - `structure_engine.update(swing)` when a swing is confirmed (line 419-420)
  - `structure_engine.check_confirmations(i, high, low)` **every bar** (unconditionally)

  Updated loop structure:
  ```python
  # Before loop: create engine
  structure_engine = StructureEngine(
      confirmation_window=config.bos_confirmation_window
  )
  all_structure_events: list[StructureEvent] = []

  for i in range(n):
      row = data.iloc[i]
      high = float(row["high"])
      low = float(row["low"])

      # Step 1: Engine update
      result = engine.update(i, row)

      # Step 2: Record swing output
      highlow = result["HighLow"]
      level = result["Level"]
      pivot_index = result.get("PivotIndex", np.nan)
      highs_lows[i] = highlow
      levels[i] = level
      pivot_indices[i] = pivot_index

      # Step 3: Record event if swing confirmed → push to StructureEngine
      if not np.isnan(highlow):
          recorder.record_swing(i, int(pivot_index), data.index[i], highlow, level)
          swing = SwingConfirmed(
              index=i,
              direction=int(highlow),
              level=float(level),
              timestamp=data.index[i],
              pivot_index=int(pivot_index),
          )
          new_events = structure_engine.update(swing)
          all_structure_events.extend(new_events)

      # Step 4: Check confirmations EVERY bar (not just on swings)
      # Uses current bar's high/low to check for level breaks
      status_changes = structure_engine.check_confirmations(i, high, low)
      # Dedup: if a provisional event was just emitted (step 3) AND confirmed
      # on the same bar (e.g., break at S3), don't add it twice.
      all_structure_events.extend(e for e in status_changes if e not in new_events)

      # Step 5: Strategy callback (unchanged for now)
      callback.update(i, row, result)
  ```

  **Edit 4** — Update return type:
  ```python
  return swings_df, recorder.events, all_structure_events, structure_engine
  ```

  **Edit 5** — Update `BacktestResult` (lines 927-938):
  ```python
  @dataclass
  class BacktestResult:
      config: BacktestConfig
      report: pd.DataFrame
      events: list
      swings_df: pd.DataFrame
      batch_results: dict[str, pd.DataFrame]
      metrics: dict
      trades: Optional[pd.DataFrame] = None
      equity_curve: Optional[pd.Series] = None
      structure_events: list = field(default_factory=list)   # NEW
      structure_engine: Optional[object] = None              # NEW
  ```

  **Edit 6** — Update `BacktestHarness.run()` (lines 968-1036) to unpack new values:
  ```python
  swings_df, events, structure_events, structure_engine = replay_phase(data, self.config)
  # ... rest unchanged until the result construction:
  return BacktestResult(
      # ... existing fields ...
      structure_events=structure_events,
      structure_engine=structure_engine,
  )
  ```

  **Must NOT do**:
  - Do not modify `_SwingEngine`
  - Do not change Phase 2 or Phase 3 logic (those are T6)
  - Do not remove any existing functionality
  - Do not break the existing `StrategyCallback.update()` signature

  **References**:
  - `backtest.py:42-82` — `BacktestConfig`
  - `backtest.py:335-434` — `replay_phase()`
  - `backtest.py:927-938` — `BacktestResult`
  - `backtest.py:968-1036` — `BacktestHarness.run()`

  **Acceptance Criteria**:
  - `BacktestConfig` has `bos_confirmation_window: int = 10`
  - `replay_phase()` returns 4 values (backward compatible)
  - `BacktestResult` has `structure_events` and `structure_engine` fields
  - StructureEngine receives swings AND `check_confirmations` is called every bar
  - Running backtest on EURUSD produces non-empty `structure_events` list

  **QA Scenarios**:
  - **Tool**: `interactive_bash`
  - **Preconditions**: T4 complete
  - **Steps**:
    ```python
    from backtest import replay_phase, BacktestConfig, load_dataset
    config = BacktestConfig()
    data = load_dataset("tests/test_data/EURUSD/EURUSD_15M.csv", config)
    swings_df, events, structure_events, engine = replay_phase(data, config)
    # Count by status
    provisional = sum(1 for e in structure_events if e.status == "provisional")
    confirmed = sum(1 for e in structure_events if e.status == "confirmed")
    cancelled = sum(1 for e in structure_events if e.status == "cancelled")
    print(f"Total structure events: {len(structure_events)}")
    print(f"  Provisional: {provisional}")
    print(f"  Confirmed: {confirmed}")
    print(f"  Cancelled: {cancelled}")
    assert confirmed > 0 or cancelled > 0  # At least some transitions happened
    ```
  - **Evidence**: `.sisyphus/evidence/t5-two-stage-integration.txt`

---

### Wave 2 — Strategy Update

---

- [ ] **T6. Update `StrategyCallback` protocol + `BOSFlipStrategy` for confirmed streaming events**

  **What to do**: Three edits:

  **Edit 1** — `StrategyCallback` protocol (backtest.py:284-314) and `NoopStrategy` (lines 317-327):
  - Add `structure_events: list = None` parameter to `update()` with docstring
  - Update `NoopStrategy` to match

  **Edit 2** — `BacktestHarness.run()` Phase 3 (backtest.py:1006-1014):
  - Build per-bar events lookup:
    ```python
    # Build per-bar events lookup (all status changes indexed by the bar they happen on)
    structure_events_by_bar: dict[int, list[StructureEvent]] = {}
    for evt in structure_events:
        bar = evt.confirmed_at_index if evt.status == "confirmed" else evt.trigger_index
        if bar not in structure_events_by_bar:
            structure_events_by_bar[bar] = []
        structure_events_by_bar[bar].append(evt)
    ```
  - In the Phase 3 loop:
    ```python
    for i in range(n):
        bar_events = structure_events_by_bar.get(i, [])
        self._strategy_callback.update(
            i, report.iloc[i], engine_result, simulator, bar_events
        )
    ```

  **Edit 3** — `BOSFlipStrategy.update()` (strategies/bos_flip.py):
  - Add `structure_events: list = None` parameter (default `None` for backward compat)
  - **V2 path**: React to **confirmed** streaming events only:
    ```python
    def update(self, candle_index, row, engine_result, simulator, structure_events=None):
        # V2 path: streaming events (confirmed only)
        if structure_events and simulator is not None:
            for event in structure_events:
                if event.event_type == "BOS" and event.status == "confirmed":
                    close_price = float(row["Close"])
                    timestamp = row.name
                    if event.direction == 1:  # Bullish BOS
                        if simulator.is_short:
                            simulator.close(candle_index, timestamp, close_price)
                        if simulator.is_flat:
                            simulator.enter_long(candle_index, timestamp, close_price)
                    elif event.direction == -1:  # Bearish BOS
                        if simulator.is_long:
                            simulator.close(candle_index, timestamp, close_price)
                        if simulator.is_flat:
                            simulator.enter_short(candle_index, timestamp, close_price)
            return  # V2 path consumed — skip batch fallback
        
        # V1 fallback: batch BOS (only when no streaming events or no simulator)
        bos = row.get("BOS", np.nan)
        if bos is None or (isinstance(bos, float) and np.isnan(bos)) or bos == 0:
            return
        # ... existing V1 logic ...
    ```

  **Important**: The V2 path returns early after processing streaming events, skipping the V1 `row["BOS"]` check. This prevents double-trading when both paths produce signals.

  **Must NOT do**:
  - Do not remove the V1 `row["BOS"]` fallback code path
  - Do not change the `StrategyCallback` import in other modules
  - Do not break existing strategies that don't pass `structure_events`

  **References**:
  - `backtest.py:284-314` — StrategyCallback protocol
  - `backtest.py:317-327` — NoopStrategy
  - `backtest.py:968-1036` — BacktestHarness.run() Phase 3
  - `strategies/bos_flip.py` — BOSFlipStrategy

  **Acceptance Criteria**:
  - `StrategyCallback` protocol accepts optional `structure_events` parameter
  - `NoopStrategy.update()` accepts optional `structure_events`
  - `BOSFlipStrategy` reacts to confirmed streaming BOS events
  - `BOSFlipStrategy` does NOT react to provisional or cancelled events
  - `BOSFlipStrategy` falls back to `row["BOS"]` when no streaming events provided
  - Existing backtests pass without modification

  **QA Scenarios**:
  - **Tool**: `interactive_bash`
  - **Steps**:
    ```python
    from strategies.bos_flip import BOSFlipStrategy
    from smartmoneyconcepts.structures import StructureEvent
    from trade_simulator import TradeSimulator
    import pandas as pd, numpy as np

    ts = pd.Timestamp("2024-01-01")
    
    # V2: Confirmed event → should enter long
    s = BOSFlipStrategy()
    sim = TradeSimulator()
    confirmed = StructureEvent("BOS", 1, 120.0, 20, 40, ts, status="confirmed", confirmed_at_index=42)
    s.update(42, pd.Series({"Close": 125.0, "BOS": np.nan}), {}, sim, [confirmed])
    assert not sim.is_flat, "Should have entered long on confirmed BOS"
    print("V2 confirmed → long: PASSED")

    # V2: Provisional event → should NOT enter
    s2 = BOSFlipStrategy()
    sim2 = TradeSimulator()
    provisional = StructureEvent("BOS", -1, 80.0, 20, 40, ts, status="provisional")
    s2.update(42, pd.Series({"Close": 75.0, "BOS": np.nan}), {}, sim2, [provisional])
    assert sim2.is_flat, "Should NOT enter on provisional event"
    print("V2 provisional → no action: PASSED")

    # V2: Cancelled event → should NOT enter
    s3 = BOSFlipStrategy()
    sim3 = TradeSimulator()
    cancelled = StructureEvent("BOS", 1, 120.0, 20, 40, ts, status="cancelled")
    s3.update(42, pd.Series({"Close": 125.0, "BOS": np.nan}), {}, sim3, [cancelled])
    assert sim3.is_flat, "Should NOT enter on cancelled event"
    print("V2 cancelled → no action: PASSED")

    # V1 fallback: batch BOS → should enter
    s4 = BOSFlipStrategy()
    sim4 = TradeSimulator()
    s4.update(0, pd.Series({"Close": 100.0, "BOS": 1}), {"HighLow": np.nan, "Level": np.nan}, sim4)
    assert not sim4.is_flat, "V1 fallback should enter long"
    print("V1 fallback batch → long: PASSED")
    ```
  - **Evidence**: `.sisyphus/evidence/t6-strategy-two-stage.txt`

---

### Wave 3 — Verification

---

- [ ] **T7. Write unit tests for two-stage `StructureEngine`**

  **What to do**: Create `tests/test_structure_engine.py` with pytest tests covering:

  1. **Test pattern detection** (4 tests: bullish/bearish BOS, bullish/bearish CHOCH)
     - Verify provisional event emitted with correct type, direction, level, status
  2. **Test non-pattern**: wrong level ordering → no event
  3. **Test partial**: <4 swings → empty
  4. **Test dedup**: same swings twice → only first emits
  5. **Test provisional → confirmed**: price break within window
     - Bullish: high > level → confirmed
     - Bearish: low < level → confirmed
  6. **Test provisional → cancelled**: window expiry without break
     - After `confirmation_window` bars with no break → cancelled
  7. **Test provisional still pending**: within window, no break → no status change
  8. **Test confirmation at exact boundary**: break on the last bar of the window → confirmed
  9. **Test multiple confirmations**: multiple provisional events, some confirmed, some cancelled
  10. **Test over data lifecycle**: engine with a real-ish sequence of 20+ swings

  **Must NOT do**:
  - Do not modify the implementation file as part of testing
  - Do not test `BrokenIndex` (not in streaming scope)
  - Do not add integration-level tests (those are T8)

  **References**:
  - `smartmoneyconcepts/structures.py` — StructureEngine, SwingConfirmed, StructureEvent
  - Reference data: `tests/test_data/EURUSD/bos_choch_result_data.csv`, `tests/test_data/EURUSD/swing_highs_lows_result_data.csv`

  **Acceptance Criteria**:
  - All 10+ test cases pass with pytest
  - Pattern detection matches batch `bos_choch()` level ordering EXACTLY
  - All 3 status transitions (provisional→confirmed, provisional→cancelled, provisional→pending) covered
  - Test coverage > 85% on StructureEngine class

  **QA Scenarios**:
  - **Tool**: `interactive_bash`
  - **Steps**: `python -m pytest tests/test_structure_engine.py -v`
  - **Expected**: All passed
  - **Evidence**: `.sisyphus/evidence/t7-two-stage-tests.txt`

---

- [ ] **T8. Integration test: streaming confirmed vs batch BOS comparison**

  **What to do**: Create `tests/test_streaming_vs_batch.py` that:

  1. Runs the full backtest on EURUSD 15M (both batch and streaming paths)
  2. Collects batch BOS events (from `per_candle_report["BOS"]`)
  3. Collects streaming **confirmed** BOS events (from `structure_events` where `status=="confirmed"`)
  4. For each batch BOS, finds the matching streaming confirmed BOS by:
     - Matching direction (both bullish/bearish)
     - Matching level (within 0.1% tolerance for floating point)
     - `swing_index` should match batch stamp index (±1 bar)
  5. Reports:
     - `total_batch_bos`: count from batch
     - `total_streaming_confirmed`: count from streaming
     - `match_count`: how many batch BOS have a streaming counterpart
     - `match_rate`: match_count / total_batch_bos
     - `cancelled_streaming`: streaming events that were cancelled (and have no batch counterpart)
     - `avg_confirm_delay`: mean bars from trigger_index to confirmed_at_index
  6. Verifies the two-stage semantics:
     - Every confirmed streaming event corresponds to a batch BOS
     - Cancelled streaming events have NO batch counterpart (they were filtered out)
     - Provisional events still pending at end of data have NO batch counterpart

  **Must NOT do**:
  - Do not hardcode expected values (data-dependent)
  - Do not assert exact index equality (indices differ between causal streaming and batch)
  - Do not modify production code as part of testing

  **References**:
  - `tests/test_data/EURUSD/EURUSD_15M.csv`
  - `tests/test_data/EURUSD/bos_choch_result_data.csv`
  - `backtest.py:BacktestHarness.run()`

  **Acceptance Criteria**:
  - Integration test runs without errors
  - Reports all statistics (no blind assertions)
  - `match_rate >= 0.8` (at least 80% of batch BOS have streaming counterparts — see Known Limitations: this may need tuning per dataset due to the break-in-the-gap blind spot)
  - `cancelled_streaming > 0` (some events were cancelled due to window expiry)
  - `avg_confirm_delay > 0` (confirmations happen after trigger)
  - Reports pending events count (provisional events in last `confirmation_window` bars of dataset)

  **QA Scenarios**:
  - **Tool**: `interactive_bash`
  - **Steps**: `python -m pytest tests/test_streaming_vs_batch.py -v -s`
  - **Expected**: Exit code 0, prints statistics
  - **Evidence**: `.sisyphus/evidence/t8-streaming-vs-batch.txt`

---

## Final Verification Wave

- [ ] **F1. Plan Compliance Audit** (oracle profile)
  - All tasks completed and verified
  - Two-stage logic present (provisional → confirmed/cancelled)
  - `BacktestConfig.bos_confirmation_window` is configurable
  - No `_SwingEngine` modifications; batch `bos_choch()` intact
  - StructureEngine is purely causal — no forward scan

- [ ] **F2. Code Quality Review** (unspecified-high)
  - Pattern level ordering matches `smc.bos_choch()` exactly
  - `check_confirmations()` does not peek into future bars
  - Confirmation/cancellation uses exact bar comparison (no >= where > should be used)
  - No hardcoded thresholds; all configurable
  - No new third-party dependencies
  - Error handling for unexpected swing orderings

- [ ] **F3. Full Test Suite** (unspecified-high)
  - `python -m pytest tests/ -v` — 100% pass rate
  - Cross-market run still produces stable results

- [ ] **F4. Scope Fidelity Check** (deep)
  - Part 1 baseline results complete and saved
  - Part 2 streaming engine used by BOSFlipStrategy (confirmed events only)
  - Phase 2 batch bos_choch still available for per_candle_report
  - No scope creep (no streaming OB, liquidity, retracements)

---

## Commit Strategy

### Branch naming
```
feature/streaming-structure-engine
```

### Commit sequence

1. **`feat: Add avg_trade_bars and median_trade_bars metrics`**
   - Files: `backtest.py`
   - Scope: `compute_trade_metrics()` with duration metrics

2. **`feat: Add cross-market BOSFlipStrategy baseline results`**
   - Files: `scripts/run_bosflip_crossmarket.py`, `.sisyphus/evidence/bosflip-crossmarket/`

3. **`feat: Add two-stage streaming StructureEngine with confirmation window`**
   - Files: `smartmoneyconcepts/structures.py` (NEW)
   - Scope: StructureEngine, SwingConfirmed, StructureEvent with status field, two-stage logic

4. **`feat: Integrate two-stage StructureEngine into backtest replay`**
   - Files: `backtest.py`
   - Scope: `BacktestConfig.bos_confirmation_window`, `replay_phase()` integration, `BacktestResult` update

5. **`feat: Update BOSFlipStrategy for confirmed streaming StructureEvents`**
   - Files: `backtest.py`, `strategies/bos_flip.py`
   - Scope: StrategyCallback protocol, strategy V2 path (confirmed only), V1 fallback

6. **`test: Add unit and integration tests for two-stage StructureEngine`**
   - Files: `tests/test_structure_engine.py`, `tests/test_streaming_vs_batch.py`
   - Scope: Pattern detection, status transitions, cross-validation

---

## Success Criteria

1. **Part 1 Complete**: Trade duration metrics added, cross-market baseline generated with all 5 datasets, comparison table saved
2. **Part 2 Complete**: Two-stage StructureEngine detects patterns provisionally, confirms/cancels based on price break within configurable window
3. **Integration**: BOSFlipStrategy reacts to confirmed streaming events only, producing trades
4. **Verification**: Unit tests cover all 3 status transitions; integration test confirms streaming matches batch with ≥80% match rate
5. **No Regressions**: Existing tests pass, batch bos_choch intact, no `_SwingEngine` changes
