# Rebuild Swing Engine — Causal State Machine

## TL;DR
> **Quick Summary**: Replace the non-causal centered-window `swing_highs_lows()` with a streaming state machine that uses ATR-based hybrid confirmation (min bars + ATR retracement). Keeps monolithic class, reorganizes internally. Revalidates all 4 downstream indicators and regenerates golden test files via 3-pass causal validation.
> **Deliverables**:
> - `_SwingEngine` inner class with state machine
> - Rewritten `smc.swing_highs_lows()` with new parameters
> - Revalidated `bos_choch()`, `ob()`, `liquidity()`, `retracements()` against new swing output
> - 3-pass test harness proving causality
> - Regenerated golden CSV files for all affected tests
> - Version bump `0.0.27` → `0.1.0`
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 5 waves (mostly sequential within waves)
> **Critical Path**: T1 → T3 → T5→T8 → T9→T13

## Context
### Original Request
Rebuild `swing_highs_lows()` to be causal (live-safe). The current implementation uses a centered rolling window (`shift(-swing_length//2).rolling(swing_length).max()`) that peeks at future candles. Replace with a delayed confirmation pivot model.

### Interview Summary
- **Architecture**: Streaming state machine (`_SwingEngine` inner class) — not batch vectorized
- **Candidate discovery**: `swing_length` = number of lookback bars; a new high is a candidate if it's the highest in the last N bars
- **Confirmation**: Hybrid — BOTH `confirmation_bars` elapsed AND price retraced `atr_multiplier` × ATR from the candidate
- **ATR**: Computed internally from OHLC data using standard rolling ATR (`atr_period`)
- **State behavior**: Candidates are mutable (can be overtaken); confirmed pivots are immutable; alternation enforced by state machine; dedup eliminated by design
- **Output shape**: Same as current — `HighLow` (1/-1/NaN) + `Level` (float/NaN), stamped on the **confirmation bar**, not the pivot bar
- **Test strategy**: 3-pass — (1) batch CSV output, (2) streaming pass, (3) compare both and assert no look-ahead
- **Monolithic**: Keep `smc.py` as one file; private inner class for the engine

### Metis Review
*Skipped — Metis consultation not executed. Gap analysis performed by Momus (below).*

### Momus Review
**Verdict: REJECT → FIXED**. 2 CRITICAL, 5 HIGH, 5 MEDIUM, 5 MISSING items identified and resolved in plan v1.1.

**Critical fixes applied:**
- C1: Added T13 version bump task (`smc.__version__` + `setup.py VERSION`)
- C2: Added `retracements()` zeroing-logic audit to T8; acceptance criteria now check for position-dependent artifacts

**High fixes applied:**
- H1: Added parameter validation + `ValueError` guard in T3
- H2: Specified that `_candidate_bars_since` resets to 0 on candidate replacement
- H3: Documented `@inputvalidator` column-lowercasing contract in T1 and T9
- H4: Added per-bar truncated-data causality verification to T9
- H5: Added explicit `_atr_ready` flag to state machine spec, gating confirmation

## Work Objectives
### Core Objective
Replace the non-causal swing detection algorithm with a causal streaming state machine that produces identical *semantics* (swing highs/lows) but without look-ahead bias, making it suitable for live trading and honest backtesting.

### Concrete Deliverables
1. `_SwingEngine` — private inner class in `smc.py` with causal state machine
2. Updated `smc.swing_highs_lows()` — new signature, delegates to engine
3. Re-validated `bos_choch()`, `ob()`, `liquidity()`, `retracements()` — compatible with new swing output
4. 3-pass test harness in `tests/test_causality.py`
5. Regenerated golden CSVs for all 5 affected tests
6. Version bump `0.0.27` → `0.1.0`

### Definition of Done
- [x] All tests pass with regenerated golden files
- [x] 3-pass causality test proves zero look-ahead (batch output == streaming output)
- [x] `smc.swing_highs_lows(ohlc, swing_length=50)` returns same column names as before
- [x] All 4 downstream methods produce plausible market structure output
- [x] No regressions in `fvg()`, `sessions()`, `previous_high_low()` (they don't depend on swings)

### Must Have
- Causal (zero future data used at any point)
- Same output schema: `DataFrame` with `HighLow` (int) and `Level` (float), NaN where no swing
- Alternation enforced (swing high → swing low → swing high → ...)
- State machine approach: mutable candidates, immutable confirmed pivots

### Must NOT Have (Guardrails)
- ❌ No new files outside `smc.py` for core logic (keep monolithic)
- ❌ No external dependencies beyond existing (`numpy`, `pandas`, `numba`)
- ❌ No decorator or subclass-based architecture changes
- ❌ No changes to FVG, sessions, previous_high_low (out of scope)

## Verification Strategy
### Test Decision
Tests-after implementation. Existing golden-file tests will be regenerated. New causality tests will be written.

### QA Policy — All agent-executed
Every implementation task includes `QA Scenarios` that the executing agent runs directly (no human in loop). Scenarios use `interactive_bash` (Python scripts), `Bash` (curl for data), and file comparison.

## Execution Strategy
### Parallel Execution Waves

```
Wave 1: Foundation       [T1, T2]        — parallel (independent)
            │
Wave 2: Integration      [T3, T4]        — sequential (T3 then T4)
            │
Wave 3: Downstream       [T5, T6, T7, T8] — parallel (independent of each other)
            │
Wave 4: Tests            [T9, T10, T11, T12, T13] — sequential
            │
Wave F: Final Review     [F1, F2, F3, F4] — parallel (independent)
```

### Dependency Matrix
| Task | Depends On | Blocks |
|------|-----------|--------|
| T1 | — | T3 |
| T2 | — | T3 |
| T3 | T1, T2 | T4, T5, T6, T7, T8 |
| T4 | T3 | T9 |
| T5 | T3 | T11 |
| T6 | T3 | T11 |
| T7 | T3 | T11 |
| T8 | T3 | T11 |
| T9 | T4 | T10 |
| T10 | T9 | T11 |
| T11 | T5, T6, T7, T8, T10 | T12 |
| T12 | T11 | T13 |
| T13 | T12 | F1 |
| F1–F4 | T13 | — |

### Agent Dispatch Summary

| Wave | Tasks | Agent Type | Rationale |
|------|-------|------------|-----------|
| 1 | T1, T2 | `explore` → `general` | Research ATR patterns, then build |
| 2 | T3 | `general` | Core implementation, needs multi-file awareness |
| 3 | T4 | `general` | Validation, integration testing |
| 4 | T5–T8 | `general` (can parallelize) | Independent downstream checks |
| 5 | T9–T12 | `general` | Test infrastructure, sequential dependency |
| F | F1–F4 | `oracle`, `general` | Final verification |

## TODOs

### Wave 1 — Foundation

- [ ] T1. Build `_SwingEngine` inner class — state machine core
  **What to do**: Create a private inner class `_SwingEngine` inside `smc.py` (nested under `class smc`). This class encapsulates a causal streaming state machine for swing high/low detection.

  **State machine specification:**
  ```
  trend:
    0  = NEUTRAL   (no established direction)
    1  = UPTREND   (seeking to confirm a swing high)
    -1 = DOWNTREND (seeking to confirm a swing low)
  ```

  **Constructor parameters:**
  ```python
  def __init__(self, swing_length=50, confirmation_bars=5,
               atr_multiplier=1.5, atr_period=14):
  ```

  **Internal state:** (all private attributes)
  - `_trend`: int (0, 1, or -1)
  - `_last_confirmed_direction`: int (1 or -1)
  - `_last_confirmed_index`: int
  - `_candidate_direction`: int (the direction of the current candidate pivot)
  - `_candidate_level`: float (the price level of the candidate)
  - `_candidate_index`: int (index where candidate was established)
  - `_candidate_bars_since`: int (candles elapsed since candidate established)
  - `_atr_ready`: bool (False until `atr_period` bars processed; gates confirmation)
  - `_current_atr`: float
  - `_high_buffer`: deque of last `swing_length` highs
  - `_low_buffer`: deque of last `swing_length` lows
  - `_price_buffer`: list/array for ATR calculation (TR values, then EMA state)
  - `_prev_close`: float (for TR calculation)
  - `_confirmed_swings`: list of (index, direction, level) tuples stamped so far

  **Main method:**
  ```python
  def update(self, index: int, row: pd.Series) -> dict:
      # row has: open, high, low, close, volume (already validated)
      
      # Step 1: Update ATR (causal, running)
      #   TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
      #   ATR = EMA of TR over atr_period
      #   First ATR = mean of first atr_period TR values
      
      # Step 2: Maintain price buffers
      #   Append high to _high_buffer, keep last swing_length
      #   Append low to _low_buffer, keep last swing_length
      
       # Step 3: Candidate discovery
      #   If trend == NEUTRAL or trend == UPTREND:
      #     if high > max(_high_buffer):  # new swing_length high
      #       _candidate_level = high
      #       _candidate_index = index
      #       _candidate_direction = 1
      #       _candidate_bars_since = 0  # RESET on replacement (prevents infinite deferral)
      #       _trend = UPTREND
      #   If trend == NEUTRAL or trend == DOWNTREND:
      #     if low < min(_low_buffer):  # new swing_length low
      #       _candidate_level = low
      #       _candidate_index = index
      #       _candidate_direction = -1
      #       _candidate_bars_since = 0  # RESET on replacement (prevents infinite deferral)
      #       _trend = DOWNTREND
      
       # Step 4: Confirmation (gated by _atr_ready)
      #   If candidate exists AND _atr_ready:
      #     _candidate_bars_since += 1
      #     if _candidate_bars_since >= confirmation_bars:
      #       if candidate_direction == 1 and low <= _candidate_level - atr_multiplier * _current_atr:
      #         CONFIRM: stamp swing high at current index
      #         _last_confirmed_direction = 1
      #         _trend = DOWNTREND (flip)
      #         reset candidate
      #       elif candidate_direction == -1 and high >= _candidate_level + atr_multiplier * _current_atr:
      #         CONFIRM: stamp swing low at current index
      #         _last_confirmed_direction = -1
      #         _trend = UPTREND (flip)
      #         reset candidate
      
      # Step 5: Return current output row
      #   return {
      #       "HighLow": direction or np.nan,
      #       "Level": level or np.nan,
      #   }
  ```

  **Alternation enforcement**: Built into the state machine. When a swing high is confirmed, trend flips to DOWNTREND (seeking low). When a swing low is confirmed, trend flips to UPTREND (seeking high). Only one candidate exists at a time.

  **Candidate mutability**: If trend is UPTREND and price makes a higher high than the current candidate, the candidate is replaced (mutable). Only confirmed swings are immutable. This naturally prevents consecutive same-direction swings without explicit dedup logic. **When a candidate is replaced, `_candidate_bars_since` resets to 0** — this ensures `confirmation_bars` have elapsed since the CURRENT candidate level was established, preventing premature confirmation.

  **Column naming contract**: The `update()` method receives rows AFTER the `@inputvalidator` decorator has lowercased column names. Always access `row['high']`, `row['low']`, `row['close']` (lowercase). Tests that instantiate `_SwingEngine` directly (e.g., T9) MUST use lowercase column names or pass data through `swing_highs_lows()`.

  **Edge cases:**
  - First `swing_length` candles: buffers not full; don't attempt candidate discovery until both `atr_period` and `swing_length` bars have been processed
  - ATR not stable yet: `_atr_ready` flag remains `False` until `atr_period` TR values collected; **confirmation step is entirely skipped until `_atr_ready` is `True`**
  - No candidate established yet: return (NaN, NaN)
  - Warmup period: first `max(swing_length, atr_period)` candles return all-NaN output; this is expected and correct

  **Must NOT do**:
  - ❌ Do not import any external libraries beyond `numpy`, `pandas`, `collections.deque`
  - ❌ Do not create utility functions outside the inner class
  - ❌ Do not use any look-ahead (e.g., shift with negative values, slicing into future data)
  - ❌ Do not add any public methods or expose the state machine

  **Recommended Agent Profile**: `general` — Python state machine + streaming algorithm
  **Parallelization**: Wave 1, blocks T3
  **References**:
  - Current implementation lines 136-219 in `smc.py`
  - `collections.deque` for maintaining rolling buffers
  - Standard ATR formula: `TR = max(H-L, |H-pC|, |L-pC|)`, then EMA
  
  **Acceptance Criteria**:
  - [ ] `_SwingEngine` is a nested class inside `smc` in `smc.py`
  - [ ] `engine = smc._SwingEngine(swing_length=5, confirmation_bars=2, atr_multiplier=1.5, atr_period=3)` instantiates without error
  - [ ] `engine.update(0, candle_row)` returns dict with keys "HighLow" and "Level"
  - [ ] Engine processes all candles without exception
  - [ ] No future data used (verified by unit test)

  **QA Scenarios**:
  1. **Happy path — Basic swing detection**
     - Tool: `interactive_bash` — Python unit test
     - Preconditions: OHLC data with clear trends (use a small synthetic series: e.g., 30 candles with a peak at candle 10)
     - Steps: Create engine, stream all candles, collect outputs
     - Expected: A swing high is confirmed after the price drops by `atr_multiplier * ATR` below the peak
     - Evidence: `.sisyphus/evidence/t1-basic-swing.txt` — printed outputs

  2. **Edge case — Engine with empty data**
     - Tool: `interactive_bash`
     - Preconditions: DataFrame with only 2 candles
     - Steps: Instantiate, stream both candles
     - Expected: No confirmed swings (both NaN), no crashes
     - Evidence: `.sisyphus/evidence/t1-empty-data.txt`

- [ ] T2. Internal ATR calculation engine
  **What to do**: Implement the running ATR calculation inside `_SwingEngine` as a private helper method `_compute_atr(self, high, low, close)`. This is called every `update()` call.

  **ATR formula (standard):**
  ```
  TR = max(high - low, abs(high - prev_close), abs(low - prev_close))
  First ATR = mean of first `atr_period` TR values
  Subsequent ATR = (prev_ATR * (atr_period - 1) + TR) / atr_period
  ```

  The implementation uses an exponential moving average (Wilder's smoothing), computed causally. Maintain `_tr_buffer` until `atr_period` values are collected, then switch to running EMA.

  **Note**: This task is part of `_SwingEngine` (T1) but separated for clarity. The implementing agent builds it as a single integrated implementation.

  **Must NOT do**:
  - ❌ Do not use pandas `ta` or any TA library
  - ❌ Do not use future data (no shift(-1), no slicing beyond current index)
  
  **Recommended Agent Profile**: `general`
  **Parallelization**: Wave 1, blocks T3
  **References**: Standard ATR formula

  **Acceptance Criteria**:
  - [ ] `_compute_atr` returns a float for each call
  - [ ] First `atr_period - 1` calls return 0.0 (insufficient data)
  - [ ] After `atr_period` calls, ATR stabilizes to a reasonable value
  - [ ] ATR calculation matches pandas-ta `atr()` for the same data (verified externally)

  **QA Scenarios**:
  1. **ATR value consistency**
     - Tool: `interactive_bash` — Python verification script
     - Preconditions: Known OHLC data, compute reference ATR with pandas-ta
     - Steps: Stream data through engine, collect ATR values after stabilization
     - Expected: ATR values match within 0.1% of reference after warmup
     - Evidence: `.sisyphus/evidence/t2-atr-consistency.txt`

---

### Wave 2 — Integration

- [ ] T3. Rewrite `smc.swing_highs_lows()` using `_SwingEngine`
  **What to do**: Replace the body of `smc.swing_highs_lows()` classmethod. The new implementation:
  1. Instantiates `_SwingEngine` with the provided parameters
  2. Iterates through `ohlc` rows in order (0 → len-1)
  3. Calls `engine.update(i, row)` for each row
  4. Collects the returned (HighLow, Level) pairs
  5. Returns a DataFrame with columns `HighLow` (int64) and `Level` (float64), same shape as current

  **New function signature:**
  ```python
  @classmethod
  def swing_highs_lows(
      cls,
      ohlc: DataFrame,
      swing_length: int = 50,
      confirmation_bars: int = 5,
      atr_multiplier: float = 1.5,
      atr_period: int = 14,
  ) -> DataFrame:
  ```

  **Implementation approach:**
  ```python
  @classmethod
  def swing_highs_lows(cls, ohlc, swing_length=50, confirmation_bars=5,
                       atr_multiplier=1.5, atr_period=14):
      engine = cls._SwingEngine(swing_length, confirmation_bars, atr_multiplier, atr_period)
      highs_lows = np.full(len(ohlc), np.nan)
      levels = np.full(len(ohlc), np.nan)
      
      for i in range(len(ohlc)):
          row = ohlc.iloc[i]
          result = engine.update(i, row)
          highs_lows[i] = result["HighLow"]
          levels[i] = result["Level"]
      
      return pd.concat([
          pd.Series(highs_lows, name="HighLow"),
          pd.Series(levels, name="Level"),
      ], axis=1)
  ```

  **Keep the `@inputvalidator` decorator** — the method still validates OHLC columns the same way.

  **Backward compatibility:**
  - Old callers: `smc.swing_highs_lows(ohlc, swing_length=50)` still works
  - Output column names are identical: `HighLow`, `Level`
  - Output shape is identical: same number of rows as input
  - **Values WILL differ** because the algorithm is now causal — that's expected and correct

  **Parameter validation** (`ValueError` guardrails — add to method body before engine instantiation):
  - `swing_length < 2` → error (need at least 2 bars for candlestick comparison)
  - `confirmation_bars < 1` → error (cannot confirm on the same bar as candidate)
  - `atr_multiplier <= 0` → error (must be positive)
  - `atr_period < 1` → error (need at least 1 bar for ATR)
  - `swing_length + confirmation_bars > len(ohlc)` → error (insufficient data to produce swings)
  - `atr_period > len(ohlc)` → error (ATR cannot be computed)

  **Must NOT do**:
  - ❌ Do not change the decorators or class structure
  - ❌ Do not remove import statements
  - ❌ Do not change the `@apply(inputvalidator(input_="ohlc"))` decorator on the class
  - ❌ Do not modify any other methods in the `smc` class
  - ❌ Do not change the return type or column names

  **Recommended Agent Profile**: `general` — integration work, single file
  **Parallelization**: Wave 2, blocked by T1, T2; blocks T4, T5, T6, T7, T8
  **References**:
  - Current `swing_highs_lows()` lines 136-219 in `smc.py`
  - `_SwingEngine` class (T1)

  **Acceptance Criteria**:
  - [ ] Function runs without error on the EURUSD test data
  - [ ] Returns DataFrame with columns "HighLow" and "Level"
  - [ ] Output has same length as input
  - [ ] HighLow values are only 1, -1, or NaN
  - [ ] Level values are float and NaN where HighLow is NaN
  - [ ] HighLow column dtype is `float64` (NaN is used as absence signal; `int64` with sentinel would fail golden comparison)
  - [ ] Parameter validation raises `ValueError` for degenerate combinations (see above)
  - [ ] No future data used at any point (confirmed by T9/T10)
  - [ ] Maximum parameter strictness: `ValueError` when `swing_length + confirmation_bars > len(ohlc)` or `atr_period > len(ohlc)` or `swing_length < 2`

  **QA Scenarios**:
  1. **Shape and type check**
     - Tool: `interactive_bash` — Python
     - Preconditions: EURUSD test data loaded
     - Steps: `result = smc.swing_highs_lows(df, swing_length=5, confirmation_bars=2, atr_multiplier=1.5, atr_period=3)`
     - Expected: `result.shape == (len(df), 2)`, columns are "HighLow" and "Level"
     - Evidence: `.sisyphus/evidence/t3-shape-check.txt`

  2. **Alternation check**
     - Tool: `interactive_bash`
     - Preconditions: Same as above
     - Steps: Extract non-NaN HighLow values, check they alternate 1, -1, 1, -1, ...
     - Expected: No two consecutive same-direction swings
     - Evidence: `.sisyphus/evidence/t3-alternation.txt`

- [ ] T4. Verify output shape and column compatibility
  **What to do**: Validate that the new swing output can be consumed by all 4 downstream methods without errors. This is a compatibility smoke test — correctness is for T5–T8.

  Run each downstream method with the new swing output and assert:
  - No exceptions raised
  - Expected column names present in output
  - Output length matches input length
  - Output dtypes are correct

  **Methods to test:**
  - `smc.bos_choch(ohlc, swings)`
  - `smc.ob(ohlc, swings)`
  - `smc.liquidity(ohlc, swings)`
  - `smc.retracements(ohlc, swings)`

  **Must NOT do**:
  - ❌ Do not modify any downstream method code
  - ❌ Do not compare against golden CSVs (those need regeneration — T11)

  **Recommended Agent Profile**: `general`
  **Parallelization**: Wave 2, blocked by T3; blocks T5–T8
  **References**: All 4 downstream methods in `smc.py`

  **Acceptance Criteria**:
  - [ ] All 4 downstream methods complete without exceptions
  - [ ] Each returns the expected columns
  - [ ] Each output length equals input length

  **QA Scenarios**:
  1. **Downstream compatibility sweep**
     - Tool: `interactive_bash` — Python script
     - Preconditions: EURUSD data, new swing output computed
     - Steps: Call each downstream method, print shape/columns/dtypes
     - Expected: No errors, correct shapes
     - Evidence: `.sisyphus/evidence/t4-downstream-compat.txt`

---

### Wave 3 — Downstream Validation

- [ ] T5. Revalidate `bos_choch()` against new swing output
  **What to do**: Run `smc.bos_choch(ohlc, new_swings)` on EURUSD test data. Review output for plausibility:
  - BOS/CHoCH events occur at plausible market structure transitions
  - Levels are reasonable price points
  - BrokenIndex is within valid range
  - Compare qualitatively against old output (understand the differences due to causality)

  **No golden file comparison yet** — that happens in T11.

  **Must NOT do**:
  - ❌ Do not modify `bos_choch()` code
  - ❌ Do not expect identical results to old implementation (they WILL differ — that's the point)

  **Recommended Agent Profile**: `general`
  **Parallelization**: Wave 3, blocked by T3; blocks T11
  **References**: `bos_choch()` lines 221-373 in `smc.py`

  **Acceptance Criteria**:
  - [ ] Output columns: BOS, CHOCH, Level, BrokenIndex
  - [ ] All values are within valid ranges
  - [ ] No index errors or array bounds violations
  - [ ] BOS and CHOCH are 1, -1, or NaN

  **QA Scenarios**:
  1. **Plausibility check**
     - Tool: `interactive_bash` — Python
     - Preconditions: EURUSD data, new swings
     - Steps: Run bos_choch, print summary stats (count of BOS/CHoCH events, mean levels)
     - Expected: At least some BOS/CHoCH events detected, no NaN outputs where expected
     - Evidence: `.sisyphus/evidence/t5-bos-choch.txt`

- [ ] T6. Revalidate `ob()` against new swing output
  **What to do**: Run `smc.ob(ohlc, new_swings)` on EURUSD test data. Review for:
  - Order blocks detected at plausible price levels
  - Volume and percentage values are non-negative
  - MitigatedIndex points to valid candle indices
  - No index errors (especially important — OB uses `np.searchsorted` on swing indices)

  **Must NOT do**:
  - ❌ Do not modify `ob()` code

  **Recommended Agent Profile**: `general`
  **Parallelization**: Wave 3, blocked by T3; blocks T11
  **References**: `ob()` lines 375-570 in `smc.py`

  **Acceptance Criteria**:
  - [ ] Output columns: OB, Top, Bottom, OBVolume, MitigatedIndex, Percentage
  - [ ] OBVolume always positive or NaN
  - [ ] Percentage between 0 and 100
  - [ ] No IndexError from `np.searchsorted` on empty arrays

  **QA Scenarios**:
  1. **OB integrity check**
     - Tool: `interactive_bash`
     - Preconditions: EURUSD data, new swings
     - Steps: Run ob(), check bounds, print stats
     - Expected: Valid ranges, no exceptions
     - Evidence: `.sisyphus/evidence/t6-ob.txt`

- [ ] T7. Revalidate `liquidity()` against new swing output
  **What to do**: Run `smc.liquidity(ohlc, new_swings)` on EURUSD test data. Review for:
  - Liquidity zones detected at swing clusters
  - Swept index is within valid range
  - End index >= Start index
  - No index errors

  **Must NOT do**:
  - ❌ Do not modify `liquidity()` code

  **Recommended Agent Profile**: `general`
  **Parallelization**: Wave 3, blocked by T3; blocks T11
  **References**: `liquidity()` lines 572-698 in `smc.py`

  **Acceptance Criteria**:
  - [ ] Output columns: Liquidity, Level, End, Swept
  - [ ] Swept index > End index where both are non-NaN
  - [ ] At least some liquidity zones detected

  **QA Scenarios**:
  1. **Liquidity validity check**
     - Tool: `interactive_bash`
     - Preconditions: EURUSD data, new swings
     - Steps: Run liquidity(), check index ordering
     - Expected: No reversed indexes, no exceptions
     - Evidence: `.sisyphus/evidence/t7-liquidity.txt`

- [ ] T8. Revalidate `retracements()` against new swing output
  **What to do**: Run `smc.retracements(ohlc, new_swings)` on EURUSD test data. Review for:
  - Direction alternates correctly based on swing input
  - Retracement percentages are between 0 and 100
  - DeepestRetracement% tracks correctly (non-decreasing between direction changes)
  - No index errors

  **Must NOT do**:
  - ❌ Do not modify `retracements()` code

  **Important — "Remove first 3 direction changes" zeroing logic**: `retracements()` (lines 967–982) contains position-dependent zeroing that walks through the `Direction` array and zeroes out everything until it has observed 3 direction flips. This logic is tightly coupled to WHERE swings occur, not just their levels. Since the causal engine stamps swings at confirmation bars (different indices than the centered-window engine), the zeroed prefix will have a different length. This is expected collateral change — the new golden file will differ here.

  **Recommended Agent Profile**: `general`
  **Parallelization**: Wave 3, blocked by T3; blocks T11
  **References**: `retracements()` lines 900-987 in `smc.py`

  **Acceptance Criteria**:
  - [ ] Output columns: Direction, CurrentRetracement%, DeepestRetracement%
  - [ ] Retracement % between 0 and 100
  - [ ] DeepestRetracement% >= CurrentRetracement% where both are non-zero
  - [ ] Direction matches swing polarity
  - [ ] **Audit**: Compare the first 50 non-zero `DeepestRetracement%` values between old and new engines; document the differences (they should differ due to shifted zeroing prefix from causality + different swing positions, not algorithmic error)
  - [ ] At least 50% of old engine's retracement direction-change count (signals output density comparable)

  **QA Scenarios**:
  1. **Retracement reasonability check**
     - Tool: `interactive_bash`
     - Preconditions: EURUSD data, new swings
     - Steps: Run retracements(), check bounds and monotonicity
     - Expected: Valid percentages, correct tracking
      - Evidence: `.sisyphus/evidence/t8-retracements.txt`

---

### Wave 4 — Test Infrastructure

- [ ] T9. Build 3-pass causal validation harness
  **What to do**: Create `tests/test_causality.py` containing a 3-pass validation pipeline.

  **Pass 1 — Batch mode**: Call `smc.swing_highs_lows(df, ...)` on the full EURUSD CSV dataset. Save the output DataFrame as `tests/test_data/EURUSD/stream_pass1.csv` (all columns: HighLow, Level).

  **Pass 2 — Streaming mode**: Write a `streaming_backtest` function that:
  ```python
  def streaming_backtest(df, swing_length, confirmation_bars, atr_multiplier, atr_period):
      engine = smc._SwingEngine(swing_length, confirmation_bars, atr_multiplier, atr_period)
      outputs = []
      for i in range(len(df)):
          row = df.iloc[i]
          result = engine.update(i, row)  # ONLY sees current + past data
          outputs.append(result)
      return pd.DataFrame(outputs)
  ```
  Run this streaming pass and save to `tests/test_data/EURUSD/stream_pass2.csv`.

  **Pass 3 — Causality comparison**: Compare Pass 1 and Pass 2 outputs. Assert:
  ```python
  pd.testing.assert_frame_equal(pass1, pass2, check_dtype=False)
  ```
  If they match, the batch API is internally using the streaming engine correctly (no look-ahead).

  Also write a **strict look-ahead detector** that validates no future data is used:
  - For each confirmed swing at index `i`, verify the output depends only on data through `i`
  - **Per-bar truncation check**: For a random sample of 10% of confirmed swings at index `i`, re-run `engine.update()` on the truncated dataset `ohlc.iloc[:i+1]` and verify the output at index `i` matches the full-run output at `i`. If future data leaked, the full-run output would differ from the truncated-run output.
  - Verify all rolling calculations use only past data (not `shift(-N)`, not `iloc[i+1:]`, etc.)

  **Must NOT do**:
  - ❌ Do not import any external testing framework beyond `unittest` and `pandas.testing`
  - ❌ Do not modify `smc.py` from this task

  **Recommended Agent Profile**: `general` — test infrastructure
  **Parallelization**: Wave 4, blocked by T4; blocks T10
  **References**: 
  - Existing `tests/unit_tests.py` structure
  - `_SwingEngine.update()` contract
  
  **Acceptance Criteria**:
  - [ ] `tests/test_causality.py` exists and is importable
  - [ ] Pass 1 output matches Pass 2 output exactly
  - [ ] Script runs from project root via `python -m pytest tests/test_causality.py`
  - [ ] Zero look-ahead confirmed programmatically

  **QA Scenarios**:
  1. **Causality test execution**
     - Tool: `interactive_bash`
     - Preconditions: T1-T4 complete, EURUSD data exists
     - Steps: `python -m pytest tests/test_causality.py -v`
     - Expected: All assertions pass
     - Evidence: `.sisyphus/evidence/t9-causality-run.txt`

- [ ] T10. Implement streaming pass wrapper for regression detection
  **What to do**: Write a helper script `tests/stream_compare.py` that runs both batch and streaming modes on any given OHLC dataset and reports differences. This serves as a diagnostic tool for future development.

  The script should:
  1. Accept a CSV file path as argument
  2. Run batch mode and streaming mode
  3. Compute the diff: which rows differ, by how much
  4. Report summary: "PASS: 0 differences" or "FAIL: N differences at rows [...]"

  **Must NOT do**:
  - ❌ Do not add this to the unit test suite — it's a diagnostic tool

  **Recommended Agent Profile**: `general`
  **Parallelization**: Wave 4, blocked by T9; blocks T11
  **References**: Same as T9

  **Acceptance Criteria**:
  - [ ] `tests/stream_compare.py` runs without error
  - [ ] `python tests/stream_compare.py tests/test_data/EURUSD/EURUSD_15M.csv` produces report
  - [ ] Report correctly identifies any differences

  **QA Scenarios**:
  1. **Diagnostic run**
     - Tool: `interactive_bash`
     - Steps: Run the script on EURUSD data
     - Expected: Clean report showing zero differences
     - Evidence: `.sisyphus/evidence/t10-diagnostic.txt`

- [ ] T11. Regenerate golden CSV files for all affected tests
  **What to do**: After T5–T8 confirm downstream methods work correctly with new swings, regenerate all golden CSV files. Use the batch API to compute new outputs, then save to CSV overwriting the old golden files.

  **Files to regenerate:**
  - `tests/test_data/EURUSD/swing_highs_lows_result_data.csv`
  - `tests/test_data/EURUSD/bos_choch_result_data.csv`
  - `tests/test_data/EURUSD/ob_result_data.csv`
  - `tests/test_data/EURUSD/liquidity_result_data.csv`
  - `tests/test_data/EURUSD/retracements_result_data.csv`

  **Use the same `swing_length=5` parameter** that the existing tests use (line 45 of `unit_tests.py`). For new parameters, use:
  - `confirmation_bars=2` (small value to see results on 15m data)
  - `atr_multiplier=1.5`
  - `atr_period=7` (smaller period for 15m data)

  **Important**: Ensure the new golden files make semantic sense. Review a few confirmed swing points manually:
  - Print row index, date, HighLow direction, Level
  - Verify with common sense: is a swing high actually at a local peak?

  **Must NOT do**:
  - ❌ Do not overwrite `fvg_result_data.csv`, `fvg_consecutive_result_data.csv`, `sessions_result_data.csv`, or `previous_high_low_*_result_data.csv` (these don't depend on swings)

  **Recommended Agent Profile**: `general`
  **Parallelization**: Wave 4, blocked by T5, T6, T7, T8, T10; blocks T12
  **References**: `tests/unit_tests.py` commented-out `generate_results_data()` function (lines 151-214)

  **Acceptance Criteria**:
  - [ ] All 5 golden CSVs updated
  - [ ] Each CSV has the correct columns and shape
  - [ ] A manual spot-check confirms swing points are at plausible price levels
  - [ ] Each CSV is non-empty (has at least some non-NaN swing detections)

  **QA Scenarios**:
  1. **Golden file regeneration**
     - Tool: `interactive_bash`
     - Preconditions: All T5-T8 pass
     - Steps: Write and run a regeneration script
     - Expected: 5 CSV files updated, git status shows changes
     - Evidence: `.sisyphus/evidence/t11-regenerated.txt`

  2. **Plausibility spot-check**
     - Tool: `interactive_bash`
     - Steps: Load the new swing CSV, print first 20 non-NaN rows with timestamps
     - Expected: Swings alternate high/low, levels are at price extremes
     - Evidence: `.sisyphus/evidence/t11-spot-check.txt`

- [ ] T12. Run full test suite and verify zero regressions
  **What to do**: Run `python -m pytest tests/unit_tests.py -v` (or `python tests/unit_tests.py`) and confirm all tests pass. This includes:
  - `test_fvg` — unchanged, must still pass
  - `test_fvg_consecutive` — unchanged, must still pass
  - `test_swing_highs_lows` — uses NEW golden file, must pass
  - `test_bos_choch` — uses NEW golden file, must pass  
  - `test_ob` — uses NEW golden file, must pass
  - `test_liquidity` — uses NEW golden file, must pass
  - `test_ob_early_data` — must still pass
  - `test_previous_high_low_4h/1D/W` — unchanged, must still pass
  - `test_sessions` — unchanged, must still pass
  - `test_retracements` — uses NEW golden file, must pass
  - `test_causality` (T9) — must pass

  Also run `tests/test_causality.py` to confirm zero look-ahead.

  **If any test fails**, diagnose and fix. Likely culprits:
  - OB early data test: the short 3-candle DataFrame may not produce swings, causing OB edge case
  - FVG tests: if swing changes somehow affected FVG (shouldn't, but verify)
  - Session tests: unaffected, but verify

  **Must NOT do**:
  - ❌ Do not skip failing tests — fix them
  - ❌ Do not lower assertions to make tests pass

  **Recommended Agent Profile**: `general` — test debugging
  **Parallelization**: Wave 4, blocked by T11; blocks F1
  **References**: `tests/unit_tests.py`, `tests/test_causality.py`

  **Acceptance Criteria**:
  - [ ] All tests pass with zero failures
  - [ ] `python tests/unit_tests.py` exits with code 0
  - [ ] `python -m pytest tests/test_causality.py` passes

  **QA Scenarios**:
  1. **Full test suite run**
     - Tool: `interactive_bash`
     - Steps: `python tests/unit_tests.py -v 2>&1`
     - Expected: OK for all tests
      - Evidence: `.sisyphus/evidence/t12-full-suite.txt`

- [ ] T13. Version bump to 0.1.0
  **What to do**: Update version string in both locations:
  1. `smartmoneyconcepts/smc.py` line ~53: `__version__ = "0.1.0"` (change from `"0.0.27"`)
  2. `setup.py` line ~5: `VERSION = "0.1.0"` (change from `"0.0.27"`)

  **Must NOT do**:
  - ❌ Do not change any import paths, PyPI metadata, or author fields

  **Recommended Agent Profile**: `general`
  **Parallelization**: Wave 4, blocked by T12; no downstream blockers
  **References**: Current `smc.py` line 53, `setup.py` line 5

  **Acceptance Criteria**:
  - [ ] `smc.__version__` returns `"0.1.0"` when imported
  - [ ] `setup.py VERSION` reads `"0.1.0"`

  **QA Scenarios**:
  1. **Version check**
     - Tool: `interactive_bash`
     - Steps: `python -c "from smartmoneyconcepts.smc import smc; print(smc.__version__)"`
     - Expected: Output is `0.1.0`
     - Evidence: `.sisyphus/evidence/t13-version.txt`

---

### Wave F — Final Verification

- [ ] F1. Plan Compliance Audit (oracle)
  **What to do**: Audit the implementation against this plan. Verify:
  - Every task's acceptance criteria is met
  - No scope creep (no changes outside swing engine and downstream validation)
  - Guardrails respected (no new files beyond tests, no new dependencies)
  - Monolithic structure preserved
  - Version bumped to 0.1.0

  **Recommended Agent Profile**: `oracle`
  **Parallelization**: Wave F, blocked by T12
  **References**: This entire plan document

  **Acceptance Criteria**:
  - [ ] Audit report generated listing all met/unmet criteria
  - [ ] No violations of guardrails
  - [ ] All scope items accounted for

- [ ] F2. Code Quality Review (unspecified-high)
  **What to do**: Review the `_SwingEngine` implementation for:
  - Proper encapsulation (all engine internals are private)
  - No dead code or commented-out sections
  - Idiomatic Python (PEP 8)
  - Type hints used consistently
  - Docstrings for all public methods
  - No performance bottlenecks (O(n) per-candle operations)

  **Recommended Agent Profile**: `unspecified-high` (general code review)
  **Parallelization**: Wave F, blocked by T12

  **Acceptance Criteria**:
  - [ ] Review report generated with findings
  - [ ] All critical issues fixed before merge

- [ ] F3. Real Manual QA (unspecified-high + generate_gif visualization)
  **What to do**: Run the existing `tests/generate_gif.py` with the new swing engine. The GIF should show:
  - Swing highs/lows overlaid on candlestick chart
  - BOS/CHoCH markers
  - Order blocks
  - Liquidity zones
  - All markers at plausible positions

  Generate a new GIF and visually inspect that the swing points look reasonable.

  **Recommended Agent Profile**: `unspecified-high` (domain knowledge)
  **Parallelization**: Wave F, blocked by T12

  **Acceptance Criteria**:
  - [ ] GIF generates without errors
  - [ ] Visual inspection confirms plausible market structure

- [ ] F4. Scope Fidelity Check (deep)
  **What to do**: Final check that ONLY the intended changes were made:
  - `git diff --stat` — only `smc.py` and `tests/` files modified
  - No accidental changes to FVG, sessions, previous_high_low
  - No new production files created outside `smc.py`

  **Recommended Agent Profile**: `deep` (thoroughness)
  **Parallelization**: Wave F, blocked by T12

  **Acceptance Criteria**:
  - [ ] `git diff --stat` shows limited, expected file changes
  - [ ] No unintended modifications
  - [ ] Clean commit history

---

## Commit Strategy
1. **One commit per logical group**, conventional commit format:
   - `feat(swing): add _SwingEngine state machine` (T1, T2)
   - `feat(swing): rewrite swing_highs_lows with causal engine` (T3, T4)
   - `test(swing): add causality test harness` (T9, T10)
   - `test(swing): regenerate golden files` (T5–T8, T11, T12, T13)
2. Squash to 1–2 commits before merge if preferred
3. Version bump included in the final test commit

## Success Criteria
- [x] ✅ `swing_highs_lows()` uses zero future data under any call path
- [x] ✅ State machine produces alternating swing highs/lows with no look-ahead
- [x] ✅ All 4 downstream methods work correctly with new swing output
- [x] ✅ 3-pass causality test passes (batch == streaming)
- [x] ✅ Full test suite passes with regenerated golden CSVs
- [x] ⬜ `tests/generate_gif.py` produces visually plausible chart (blocked — missing optional deps)
- [x] ✅ Version bumped to 0.1.0
- [x] ✅ Monolithic architecture preserved (single `smc.py` file)



