# Oracle Post-Implementation Review: Orchestrator + LiveSmcBuffer

**Review date:** 2026-06-24
**Files reviewed:**
- `live_smc_buffer.py` (218 lines) — LiveSmcBuffer streaming accumulator
- `orchestrator.py` (267 lines) — OrchestrationState, OrchestratorContext, LiveOrchestrator, sync_write_entry
- `.sisyphus/plans/orchestrator-implementation.md` (1016 lines) — Implementation plan
- Reference: `market_snapshot.py`, `confluence.py`, `narrative.py`, `decision_engine.py`, `journal.py`

---

## Bottom Line

**CONDITIONAL PASS.** The two new files (`live_smc_buffer.py`, `orchestrator.py`) correctly implement the design from the plan with zero changes to existing pipeline classes. No business logic, scoring, or SMC reimplementation exists in the orchestrator. The critical blocker is that **no tests exist** (T3, T4, T5 are unimplemented). Additionally, the `step()` error handler has a double-failure bug when called from ERROR state without `reset()`. These issues must be resolved before the implementation can be considered complete.

---

## Plan Compliance

| Requirement | Plan Spec | Actual | Status |
|---|---|---|---|
| LiveSmcBuffer wraps `_SwingEngine` + `StructureEngine` | Constructor instantiates both | Lines 48-51: both instantiated in `__init__` | ✅ |
| Batch OB/liquidity/retracements run on swing confirmation only | Guarded by `not np.isnan(engine_result.get("HighLow"))` | Lines 90-99: triggered by `if not np.isnan(engine_result.get("HighLow"))` | ✅ |
| Orchestrator is pure orchestration, no business logic, no scoring | No scoring or strategy code | Lines 129-244: all pipeline methods call existing class methods only | ✅ |
| `_TRANSITIONS` matrix validates all 6 states | Class-level dict with 6 entries | Lines 36-43: module-level dict with all 6 states | ✅ |
| `mode` field in `OrchestratorContext` with `load()` guard for replay | `mode: str = "live"`; early return in `load()` | Line 71: `mode: str = "live"`; lines 136-137: `if mode == "replay": return` | ✅ |
| `reset()` clears runtime context, works from any state | Sets state to IDLE, clears fields, bypasses `_transition()` | Lines 109-123: sets `self.state = IDLE`, clears 7 fields + `_last_error` | ✅ |
| `sync_write_entry()` function exists for async bridge | Standalone function wrapping `asyncio.run()` | Lines 250-267: `sync_write_entry(writer, entry)` with `asyncio.run()` | ✅ |
| No gating — every cycle journals unconditionally | No `can_decide()`, no guards beyond data presence | `step()` always calls `journal()`; no conditional logic | ✅ |
| `journal()` reads StructureEngine events via buffer property | `self._smc_buffer.events` | Line 201: `events = self._smc_buffer.events` | ✅ |
| `load()` uses `load_ta_series(tail=1).iloc[-1]` | Single row via tail=1 | Lines 139-151: `load_ta_series(tail=1)`, `df.iloc[-1]` | ✅ |
| Replay mode skips buffer update entirely | `if mode == "replay": return` before buffer ops | Line 137: `return` before data fetch and `_smc_buffer.update()` | ✅ |
| Unit tests for LiveSmcBuffer | `tests/test_live_smc_buffer.py` | **File does not exist** | ❌ |
| Unit tests for LiveOrchestrator | `tests/test_orchestrator.py` | **File does not exist** | ❌ |
| Integration test | `tests/test_orchestrator_integration.py` | **File does not exist** | ❌ |
| No changes to existing pipeline classes | `git diff --name-only` shows only new files | Only `live_smc_buffer.py`, `orchestrator.py`, `.sisyphus/` files | ✅ |
| 26-column report schema | Matches `SnapshotBuilder.build()` expectations | All columns match the expected names (verified against `market_snapshot.py`) | ✅ |

---

## Architecture Review

### Issue A1 (Medium): `step()` error handler has a double-failure bug

**Location:** `orchestrator.py`, lines 240-243

**Problem:** When `step()` is called from `ERROR` state (caller forgot `reset()`), the exception handler fails to clean up because it tries `self._transition(OrchestrationState.ERROR)` — but `ERROR` state's transition matrix only allows `{IDLE}`. The `ERROR → ERROR` transition triggers a second `RuntimeError`, masking the original error.

**Observed behavior:**
- Original error: `"Invalid transition: ERROR → LOAD"` (correctly stored in `_last_error`)
- Re-raised error (what caller sees): `"Invalid transition: ERROR → ERROR"` (misleading)

**Root cause:** The `except` block assumes `self.state` is always one of the pipeline states (LOAD/ANALYZE/DECIDE/JOURNAL), all of which permit `{ERROR}` as a valid transition. When `step()` is called from ERROR or IDLE, this assumption breaks.

**Impact:** Low. The caller sees a confusing error message, but `_last_error` preserves the original. Since the plan states "Caller must call `reset()` before retrying from ERROR," this edge case only manifests when the caller violates the documented pattern.

**Recommended fix:** Use direct state assignment instead of `_transition()` in the error handler:
```python
except Exception as e:
    self._last_error = e
    self.state = OrchestrationState.ERROR  # direct, no validation needed
    raise
```

### Issue A2 (Low): BOS/CHOCH columns are not forward-filled

**Location:** `live_smc_buffer.py`, lines 145-156

**Problem:** The plan's column schema specifies BOS/CHOCH as "forward-filled" from their trigger bars. The actual implementation stamps values only on the bar where the StructureEvent occurs and leaves `NaN` on all subsequent bars. While `SnapshotBuilder.build()` correctly uses `_last_non_nan()` to find the last value, the raw report has gaps.

**Impact:** Low. Does not affect `SnapshotBuilder`, `ConfluenceScorer`, or `DecisionEngine` — they scan backwards. Only affects manual report inspection and any downstream consumers that expect row-local semantics.

### Issue A3 (Low): `step()` docstring over-constrains exception type

**Location:** `orchestrator.py`, line 233

**Problem:** The docstring says `Raises RuntimeError: On pipeline failure`, but the `except` block uses `raise` (bare re-raise), which propagates the *original* exception type. If `load_ta_series()` raises `FileNotFoundError` or `pandas` raises `ValueError`, that's what the caller sees, not `RuntimeError`.

**Impact:** Low. All explicit `raise` statements in pipeline methods use `RuntimeError`, but wrapped third-party code may not.

---

## Code Quality

### Issue C1 (Low): Unused import `field`

**Location:** `orchestrator.py`, line 13

`from dataclasses import dataclass, field` — `field` is imported but never used. The `OrchestratorContext` dataclass uses default values directly without `field()`.

### Issue C2 (Low): Type hints use `Any` instead of concrete types

**Location:** `orchestrator.py`, lines 73-79

```python
ta_row: Any = None
smc_report: Any = None
...
decision: Any = None  # Decision
```

The plan specified `pd.Series | None`, `pd.DataFrame | None`, and `Decision | None`. Using `Any` disables static type checking for these fields. This is a regression from the plan's type precision.

### Issue C3 (Info): `_recompute_downstream` accesses private members of `_SwingEngine`

**Location:** `live_smc_buffer.py`, line 112

```python
min_rows = self._swing_engine._swing_length + self._swing_engine._confirmation_bars
if len(self._swing_rows) < max(min_rows, self._swing_engine._atr_period):
```

Reads `_swing_length`, `_confirmation_bars`, and `_atr_period` from the private `_SwingEngine` class. This is acceptable since `_SwingEngine` is itself a private class within `smc`, but creates coupling to internal names. An alternative would be passing these as constructor params, but the current approach mirrors `backtest.py`'s pattern.

### Issue C4 (Info): `_TRANSITIONS` is module-level instead of class variable

**Location:** `orchestrator.py`, lines 36-43 vs plan lines 323-330

The plan specifies `_TRANSITIONS` as a class attribute of `LiveOrchestrator`; the implementation places it at module level. Functionally equivalent — the matrix is immutable and `_transition()` references it correctly. Minor stylistic deviation.

### Issue C5 (OK): No dead code or commented-out sections

Both files are clean — no commented-out code, no `TODO` stubs, no unreachable branches beyond the intended guard patterns.

### Issue C6 (OK): No circular imports

The import chain is: `orchestrator.py → live_smc_buffer.py → smc/structures`. No cycles.

### Issue C7 (OK): Docstrings on public methods

Every public method in both files has a docstring. Private methods (`_recompute_downstream`, `_update_report`, `_transition`) are missing docstrings but have inline comments.

---

## Test Coverage

### Critical Gap: No tests exist at all.

The plan specifies three test files (T3, T4, T5), none of which have been created:

| Test file | Plan reference | Status | Coverage target |
|---|---|---|---|
| `tests/test_live_smc_buffer.py` | T3 (lines 692-763) | **Missing** | LiveSmcBuffer construction, update, swing triggers, structure engine integration, report schema, edge cases |
| `tests/test_orchestrator.py` | T4 (lines 767-862) | **Missing** | State transitions, pipeline guards, error handling, transition validation, `reset()`, replay mode, `sync_write_entry()` |
| `tests/test_orchestrator_integration.py` | T5 (lines 867-951) | **Missing** | Full cycle with real LiveSmcBuffer + JournalWriter, live mode, replay mode, SQLite verification |

**Without these tests, the following cannot be verified:**
- `_transition()` correctly rejects invalid state changes
- `_recompute_downstream()` guard thresholds work correctly
- Report column schema matches `SnapshotBuilder.build()` expectations at runtime
- `sync_write_entry()` successfully persists entries to SQLite
- Replay mode actually skips file I/O
- Error recovery via `reset()` works end-to-end

---

## Verdict

**CONDITIONAL PASS**

### Passes:
- ✅ Both files exist, import without errors, and export the correct API surface
- ✅ Zero modifications to existing pipeline classes (verified via `git status`)
- ✅ Pure orchestration — no business logic, no scoring, no SMC reimplementation in `orchestrator.py`
- ✅ LiveSmcBuffer correctly wraps `_SwingEngine` + `StructureEngine`; batch downstream runs on swing confirmation only
- ✅ All 6 states in transition matrix; validation raises `RuntimeError` on invalid transitions
- ✅ `reset()` clears runtime context, bypasses `_transition()`, works from any state
- ✅ Replay mode guard (`mode == "replay"` → early return in `load()`)
- ✅ `sync_write_entry()` standalone function exists and wraps `asyncio.run()`
- ✅ 26-column report schema matches `SnapshotBuilder.build()` expectations
- ✅ No gating — every cycle journals unconditionally

### Conditions (must resolve before final sign-off):

1. **Create test files** — T3 (`test_live_smc_buffer.py`), T4 (`test_orchestrator.py`), T5 (`test_orchestrator_integration.py`) as specified in the plan. All tests must pass.

2. **Fix double-failure in `step()` error handler** — See Issue A1. Use direct state assignment in the `except` block instead of `_transition()`.

3. **(Optional) Forward-fill BOS/CHOCH columns** — See Issue A2. Low priority since `SnapshotBuilder` handles gaps correctly via `_last_non_nan()`, but the plan specified forward-filling.

4. **(Optional) Tighten type hints** — Replace `Any` with `pd.Series | None`, `pd.DataFrame | None`, `Decision | None` in `OrchestratorContext`.

5. **(Optional) Remove unused `field` import** — Line 13 of `orchestrator.py`.

---

## Optional Future Considerations (out of scope for this review)

- **Error handler hardening in `step()`:** Consider using direct assignment (`self.state = OrchestrationState.ERROR`) in the except block to make the error path robust regardless of current state.
- **Test conftest:** The plan called for `tests/conftest_orchestrator.py` with shared fixtures. Not blocking, but would reduce test duplication.
