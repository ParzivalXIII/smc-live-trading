# Orchestrator State Machine + LiveSmcBuffer — Streaming Accumulator

## TL;DR
> **Quick Summary**: Build two new components: (1) `LiveSmcBuffer` — a streaming SMC accumulator that wraps `_SwingEngine` + `StructureEngine` and runs batch OB/liquidity/retracements on swing confirmation, and (2) `LiveOrchestrator` — a state machine that wires the full pipeline (load → analyze → decide → journal) with no business logic or gating.
> 
> **Deliverables**:
> - `live_smc_buffer.py` — `LiveSmcBuffer` class with rolling 26-column SMC report
> - `orchestrator.py` — `OrchestrationState`, `OrchestratorContext`, `LiveOrchestrator`
> - Unit tests for `LiveSmcBuffer` (swing accumulation, downstream batch, report)
> - Unit tests for `LiveOrchestrator` (state transitions, error handling, mock buffer)
> - Integration test — full cycle with real `LiveSmcBuffer` + `JournalWriter`
> 
> **Estimated Effort**: Medium (5 implementation tasks)
> **Parallel Execution**: YES — 5 waves (sequential within waves)
> **Critical Path**: T1 → T2 → T3 → T4 → T5
> 
> **Note**: Oracle gap review applied — 3 gaps resolved (G1: Replay Mode, G2: Error Recovery, G3: Async Bridge). Plan is ready for execution.

## Context

### Original Request
Wire all existing pipeline components (`SnapshotBuilder`, `ConfluenceScorer`, `MarketNarrativeBuilder`, `DecisionEngine`, `JournalWriter`) together into a state-machine orchestrator for live trading. Build a streaming SMC accumulator (`LiveSmcBuffer`) that wraps `_SwingEngine` + `StructureEngine` and runs batch OB/liquidity/retracements on swing confirmation — no reimplementation of existing SMC logic.

### Interview Summary
**User Decisions (Confirmed):**
| Question | Choice | What it means |
|----------|--------|---------------|
| SMC live data | **LiveSmcBuffer** | New file `live_smc_buffer.py`. Streaming accumulator wrapping `_SwingEngine` + `StructureEngine`. Runs batch OB/liquidity/retracements on swing confirmation. Exposes 26-column rolling report. |
| TA row shape | **load_ta_series(tail=1)** | One-line change — replace `load_ta_latest()` with `load_ta_series(tail=1).iloc[-1]` in the orchestrator's `load()`. |
| Event provenance | **Orchestrator owns StructureEngine** | Constructor parameter. `load()` calls `engine.check_confirmations()`. On swing confirm, calls `engine.update(swing)`. `journal()` reads `engine.events`. |
| Guard strategy | **No gate, always journal** | Every cycle produces a `JournalEntry`. No `can_decide()` method. Query layer filters later. |

**Key Design Constraint:** `_SwingEngine.update()` and `StructureEngine` are owned by `LiveSmcBuffer`. Batch downstream (OB, liquidity, retracements) runs ONLY on swing confirmation — not every candle. BOS/CHOCH is handled by `StructureEngine` (compliments batch `bos_choch`, not a replacement).

### Metis Review (Self-Performed)
| Gap | Severity | Resolution |
|-----|----------|------------|
| `_recompute_downstream()` needs OHLCV buffer | Minor | `LiveSmcBuffer` maintains a rolling `_ohlcv_buffer: list[dict]` of OHLCV rows alongside `_swing_data` |
| Report column schema not pinned down | Minor | Define the exact 26-column schema in T1; map batch output columns to canonical names matching `SnapshotBuilder.build()` expectations |
| `SnapshotBuilder` expects BOS/CHOCH columns from report | Minor | `StructureEngine` events are stamped onto the report at trigger bars; BOS/CHOCH columns derived from event status |
| `LiveSmcBuffer.update()` receives TA-enriched row, not raw OHLCV | Minor | Extract OHLCV subset for `_SwingEngine.update()`; the row has lowercase OHLCV columns from `load_ta_series` |
| Orchestrator journal step is synchronous, but `JournalWriter` is async | Minor | Add `sync_write_entry()` standalone function in `orchestrator.py` — `asyncio.run()` bridge; caller calls after `step()` |
| No existing tests for orchestrator or buffer | Info | Greenfield test creation in T3/T4/T5 |
| Replay mode integration not fully specified | Minor | Add `mode: str = "live"` to `OrchestratorContext`; `load()` guards on `if mode == "replay": return`; caller pre-populates context |
| Orchestrator error state recovery not specified | Minor | Add `_TRANSITIONS` class-level dict + validated `_transition()` + `reset()` method; caller calls `reset()` before retry from ERROR |

## Work Objectives

### Core Objective
Build a streaming SMC accumulator (`LiveSmcBuffer`) and a state-machine orchestrator (`LiveOrchestrator`) that wires the full live trading pipeline end-to-end: load TA data + update SMC buffer → build market snapshot → score confluence → generate narrative → make decision → journal entry.

### Concrete Deliverables
1. `live_smc_buffer.py` — `LiveSmcBuffer` class with streaming `_SwingEngine` + `StructureEngine` ownership, batch downstream triggers, rolling 26-column report
2. `orchestrator.py` — `OrchestrationState` enum, `OrchestratorContext` dataclass, `LiveOrchestrator` class with 5 pipeline methods + `step()` orchestrator
3. `tests/test_live_smc_buffer.py` — Unit tests for LiveSmcBuffer (swing accumulation, downstream batch triggers, report generation, edge cases)
4. `tests/test_orchestrator.py` — Unit tests for LiveOrchestrator (state transitions, pipeline method guards, error handling, mock buffer)
5. `tests/test_orchestrator_integration.py` — Integration test: full cycle with real LiveSmcBuffer + JournalWriter

### Definition of Done
- [ ] `live_smc_buffer.py` exists and is importable
- [ ] `LiveSmcBuffer.update()` processes a candle, updates swing engine + structure engine, and returns engine result dict
- [ ] On swing confirmation, batch OB/liquidity/retracements are run and report columns are updated
- [ ] `LiveSmcBuffer.get_smc_report()` returns a DataFrame with columns compatible with `SnapshotBuilder.build()`
- [ ] `orchestrator.py` exists and is importable
- [ ] `LiveOrchestrator.step()` completes LOAD → ANALYZE → DECIDE → JOURNAL → IDLE without error on valid input
- [ ] `LiveOrchestrator.step()` transitions to ERROR on pipeline failure
- [ ] Unit tests pass: `pytest tests/test_live_smc_buffer.py tests/test_orchestrator.py -v`
- [ ] Integration test completes a full cycle writing to SQLite via JournalWriter
- [ ] No changes to existing pipeline classes (`market_snapshot.py`, `confluence.py`, `narrative.py`, `decision_engine.py`, `journal.py`, `backtest.py`, `smartmoneyconcepts/`)

### Must Have
- `LiveSmcBuffer` reuses `_SwingEngine` and `StructureEngine` directly — no reimplementation of swing/pattern logic
- Batch OB/liquidity/retracements run ONLY on swing confirmation — not every candle
- `LiveOrchestrator` is pure orchestration — no business logic, no scoring, no strategy
- Journal is always written per cycle — no gating
- `LiveOrchestrator` reads `StructureEngine` events for journal entries
- Orchestrator's `load()` uses `load_ta_series(tail=1).iloc[-1]` (the Q2 decision)
- Orchestrator owns `StructureEngine` via `LiveSmcBuffer.events` property (the Q3 decision)
- `OrchestratorContext` has `mode: str = "live"` field (Gap 1: replay mode support)
- `load()` returns early without fetching data when `context.mode == "replay"` (Gap 1)
- `_transition()` validates against a class-level `_TRANSITIONS` matrix, raises `RuntimeError` on invalid (Gap 2)
- `LiveOrchestrator.reset()` clears runtime context fields and returns to IDLE (Gap 2)
- `orchestrator.py` exposes `sync_write_entry(writer, entry)` function for sync async bridge (Gap 3)

### Must NOT Have (Guardrails)
- ❌ Do NOT modify `market_snapshot.py`, `confluence.py`, `narrative.py`, `decision_engine.py`, `journal.py`
- ❌ Do NOT modify `smartmoneyconcepts/smc.py` or `smartmoneyconcepts/structures.py`
- ❌ Do NOT modify `backtest.py` or `trade_scripts/analyze_ta.py`
- ❌ Do NOT add `can_decide()` or any gating logic to the orchestrator
- ❌ Do NOT implement trade execution, order routing, or position management
- ❌ Do NOT add multi-timeframe logic to the orchestrator (that's `MarketContext`'s job)
- ❌ Do NOT attempt to make `LiveSmcBuffer`'s report identical to the batch backtest report — BOS/CHOCH comes from `StructureEngine` events, not batch `bos_choch()`
- ❌ Do NOT add auto-recovery or retry logic inside `step()` — caller owns retry policy (Gap 2)
- ❌ Do NOT make `_transition()` a no-op — validation must catch invalid transitions (Gap 2)
- ❌ Do NOT skip transition validation for ERROR → IDLE via _transition() — use reset() instead (Gap 2)
- ❌ Do NOT import `JournalWriter` inside `LiveOrchestrator` class — use `sync_write_entry()` from caller side (Gap 3)
- ❌ Do NOT make `load()` conditionally call `_smc_buffer.update()` in replay mode — skip entirely (Gap 1)
- ❌ Do NOT add `mode` validation in `analyze()`/`decide()`/`journal()` — they are mode-agnostic (Gap 1)

## Verification Strategy

**Test decision:** Tests-after (write code first, then tests). Existing project pattern.
**QA Policy:** All verification is agent-executed. Each task has QA scenarios using `interactive_bash` or Playwright (if UI exists — none in this wave). All evidence saved to `.sisyphus/evidence/`.

**Custom conftest additions:** A new fixture file `tests/conftest_orchestrator.py` provides shared fixtures for LiveSmcBuffer and Orchestrator tests (mock TA rows, synthetic OHLCV sequences, pre-built swing sequences).

**Automated verification (every task):**
1. Syntax check: `python -c "import ast; ast.parse(open('FILE').read())"` 
2. Import check: `python -c "from FILE import CLASS"`
3. Pytest: specific test file runs
4. Evidence collection for assertion failures

## Execution Strategy

### Parallel Execution Waves
```
Wave 1: T1 — live_smc_buffer.py (no deps)
Wave 2: T2 — orchestrator.py (depends on T1 for LiveSmcBuffer import)
Wave 3: T3 — tests/test_live_smc_buffer.py (depends on T1)
Wave 4: T4 — tests/test_orchestrator.py (depends on T1, T2)
Wave 5: T5 — tests/test_orchestrator_integration.py (depends on T1, T2, T3, T4)
```

### Dependency Matrix
| Task | Depends On | Blocks | Wave |
|------|-----------|--------|------|
| T1 | — | T2, T3 | 1 |
| T2 | T1 | T4, T5 | 2 |
| T3 | T1 | T5 | 3 |
| T4 | T1, T2 | T5 | 4 |
| T5 | T1, T2, T3, T4 | — | 5 |

### Agent Dispatch Summary
| Task | Agent Type | Rationale |
|------|-----------|-----------|
| T1 | fullstack | Streaming class with numpy/pandas + SMC engine wiring |
| T2 | fullstack | State machine + pipeline orchestration + error handling |
| T3 | test | pytest unit tests for LiveSmcBuffer |
| T4 | test | pytest unit tests for LiveOrchestrator with mocks |
| T5 | test | Integration test with real JournalWriter + SQLite |

## TODOs

### T1 — `live_smc_buffer.py` — LiveSmcBuffer streaming accumulator

- **What to do**: Write `live_smc_buffer.py` containing:
  
  1. **`LiveSmcBuffer` class** with constructor params: `swing_length=5`, `confirmation_bars=2`, `atr_multiplier=1.5`, `atr_period=7`, `bos_confirmation_window=10`.
  
  2. **Internal state**:
     - `self._swing_engine: smc._SwingEngine` — instantiated in `__init__`
     - `self._structure_engine: StructureEngine` — instantiated in `__init__`
     - `self._ohlcv_buffer: list[dict]` — rolling OHLCV rows (each candle appended)
     - `self._swing_rows: list[dict]` — accumulated swing output rows (one per candle, result from `_swing_engine.update()`)
     - `self._report: pd.DataFrame` — rolling report updated incrementally
     - `self._candle_index: int` — counter incremented per update
  
  3. **`update(self, row: pd.Series) -> dict`** method:
     - Extract OHLCV: `high = float(row["high"])`, `low = float(row["low"])`, `close = float(row["close"])`
     - Create OHLCV sub-row dict, append to `_ohlcv_buffer`
     - Call `self._swing_engine.update(self._candle_index, row)` → result dict
     - Append result dict to `_swing_rows`
     - If swing confirmed (`not np.isnan(result.get("HighLow", np.nan))`):
       - Build `SwingConfirmed` dataclass (direction, level, index, pivot_index, timestamp from row if available)
       - Call `self._structure_engine.update(swing)` → new events
       - Call `self._recompute_downstream()`
     - Call `self._structure_engine.check_confirmations(self._candle_index, high, low)` → status changes
     - Call `self._update_report(result, status_changes)`
     - Increment `self._candle_index`
     - Return result dict
  
  4. **`_recompute_downstream(self)`** method:
     - Guard: if `len(self._swing_rows)` < swing_length + confirmation_bars, skip (not enough data)
     - Build `swings_df` from `self._swing_rows` — DataFrame with columns `HighLow`, `Level`, `PivotIndex`
     - Build `ohlc_df` from `self._ohlcv_buffer` — DataFrame with columns `open`, `high`, `low`, `close`, `volume`
     - Run `smc.ob(ohlc_df, swings_df, close_mitigation=False)` → OB result
     - Run `smc.liquidity(ohlc_df, swings_df, range_percent=0.01)` → liquidity result
     - Run `smc.retracements(ohlc_df, swings_df)` → retracements result
     - Store batch results in `self._batch_results` dict for `_update_report` to use
  
  5. **`_update_report(self, engine_result: dict, status_changes: list)`** method:
     - Append a new row to `self._report` with:
       - OHLCV columns (open, high, low, close, volume) from current candle
       - Swing columns (SwingHighLow, SwingLevel, SwingPivotIndex) from engine_result
       - BOS/CHOCH columns from structure events: scan `status_changes` for BOS/CHOCH events, stamp them at this index; forward-fill from previous rows if not changed
       - OB columns from latest batch results (at current index position from `self._batch_results`)
       - Liquidity columns from latest batch results
       - Retracement columns from latest batch results
     - Keep report trimmed to last 200 rows
  
  6. **`get_smc_report(self) -> pd.DataFrame`** method returning last 200 rows of `self._report`
  
  7. **`events` property** returning `self._structure_engine.events`
  
  8. **Column schema for `_report`** (must match what `SnapshotBuilder.build()` expects):

  | Column | Source | Notes |
  |--------|--------|-------|
  | Timestamp | row.name or current candle | pd.Timestamp |
  | Open | ohlcv row | float |
  | High | ohlcv row | float |
  | Low | ohlcv row | float |
  | Close | ohlcv row | float |
  | Volume | ohlcv row | float |
  | SwingHighLow | engine_result["HighLow"] | 1/-1/NaN |
  | SwingLevel | engine_result["Level"] | float/NaN |
  | SwingPivotIndex | engine_result.get("PivotIndex", NaN) | float/NaN |
  | BOS | StructureEngine events | 1/-1/NaN (forward-filled) |
  | CHOCH | StructureEngine events | 1/-1/NaN (forward-filled) |
  | BOSLevel | StructureEvent.level | float/NaN |
  | BrokenIndex | StructureEvent.confirmed_at_index | float/NaN (from event confirmation bar) |
  | OB | batch ob result | 1/-1/NaN |
  | OBTop | batch ob result | float/NaN |
  | OBBottom | batch ob result | float/NaN |
  | OBVolume | batch ob result | float/NaN |
  | OBMitigatedIndex | batch ob result | float/NaN |
  | OBPct | batch ob result | float/NaN |
  | Liquidity | batch liquidity result | 1/-1/NaN |
  | LiqLevel | batch liquidity result | float/NaN |
  | LiqEnd | batch liquidity result | float/NaN |
  | LiqSwept | batch liquidity result | float/NaN |
  | RetraceDirection | batch retracements result | 1/-1/NaN |
  | CurrentRetracement% | batch retracements result | float/NaN |
  | DeepestRetracement% | batch retracements result | float/NaN |

- **Must NOT do**:
  - Do NOT reimplement swing logic — call `_SwingEngine.update()`
  - Do NOT reimplement BOS/CHOCH — use `StructureEngine`
  - Do NOT reimplement OB/liquidity/retracements — use batch `smc.ob()`, `smc.liquidity()`, `smc.retracements()`
  - Do NOT include `bos_choch()` batch call — that's what `StructureEngine` replaces
  - Do NOT modify any existing files

- **Recommended Agent Profile**: `fullstack` — Python with numpy/pandas, SMC engine understanding, streaming data patterns

- **Parallelization**: Wave 1, no blockers, no blocked-by

- **References**:
  - `smartmoneyconcepts/smc.py` — `_SwingEngine` class (lines 60-210), `smc.ob()` (lines 530-725), `smc.liquidity()` (lines 728-853), `smc.retracements()` (lines 1056-1142)
  - `smartmoneyconcepts/structures.py` — `SwingConfirmed`, `StructureEvent`, `StructureEngine`
  - `backtest.py` — `replay_phase()` (lines 344-474) for pattern of how `_SwingEngine` and `StructureEngine` are wired together
  - `backtest.py` — `build_per_candle_report()` (lines 526-588) for the 26-column schema
  - `market_snapshot.py` — `SnapshotBuilder.build()` (lines 130-309) for the column names it reads

- **Acceptance Criteria**:
  - `python -c "from live_smc_buffer import LiveSmcBuffer; buf = LiveSmcBuffer(); print('OK')"` succeeds
  - `LiveSmcBuffer` constructs with all default params without error
  - `update(pd.Series)` returns a dict with "HighLow" key (may be NaN for early candles)
  - After 50+ updates on real OHLCV data, `get_smc_report()` returns a DataFrame with 26 columns
  - No exceptions raised during normal operation

- **QA Scenarios**:
  1. **Happy path — basic construction and update**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: `live_smc_buffer.py` exists
     - **Steps**: 
       1. `python -c "from live_smc_buffer import LiveSmcBuffer; buf = LiveSmcBuffer(); print('construct OK')"`
       2. Create a synthetic 100-row OHLCV DataFrame as CSV, load with pandas, iterate through rows calling `update()`
       3. After all rows, call `get_smc_report()` and check it has 26 columns
     - **Expected Result**: No errors, report has correct columns, last few rows have non-NaN values in swing columns if swings were detected
     - **Evidence**: `.sisyphus/evidence/t1-happy.log`
  
  2. **Edge case — empty buffer before enough data**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: `live_smc_buffer.py` exists
     - **Steps**: 
       1. Create with custom params `swing_length=50, confirmation_bars=10`
       2. Call `update()` with 30 synthetic candles — not enough for swing detection
       3. Check `get_smc_report()` — all SMC columns should be NaN
     - **Expected Result**: Report exists but all SMC columns are NaN (expected — not enough data to form swings)
     - **Evidence**: `.sisyphus/evidence/t1-not-enough-data.log`
  
  3. **Swing confirmation triggers downstream**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: `live_smc_buffer.py` exists
     - **Steps**: 
       1. Build a synthetic OHLCV sequence engineered to produce a swing high (steep rise followed by retracement)
       2. Process all candles through `LiveSmcBuffer.update()`
       3. After the swing confirmation bar, check `get_smc_report()` for non-NaN OB columns
     - **Expected Result**: OB columns have non-NaN values after swing confirmation (downstream ran)
     - **Evidence**: `.sisyphus/evidence/t1-swing-trigger.log`

---

### T2 — `orchestrator.py` — State machine pipeline owner

- **What to do**: Write `orchestrator.py` containing:

  1. **`OrchestrationState` enum** (in file `orchestrator.py`):
     ```python
     from enum import Enum, auto
     class OrchestrationState(Enum):
         IDLE = auto()
         LOAD = auto()
         ANALYZE = auto()
         DECIDE = auto()
         JOURNAL = auto()
         ERROR = auto()
     ```
  
  2. **`OrchestratorContext` dataclass** — now includes `mode` field for replay support (Gap 1):
     ```python
     @dataclass
     class OrchestratorContext:
         symbol: str
         timeframe: str
         data_dir: str = "data"
         db_path: str = "journal.db"
         mode: str = "live"                    # "live" or "replay" (Gap 1)

         # Runtime state (set during pipeline execution)
         ta_row: pd.Series | None = None
         smc_report: pd.DataFrame | None = None
         snapshot: MarketSnapshot | None = None
         confluence: ConfluenceResult | None = None
         narrative: MarketNarrative | None = None
         decision: Decision | None = None
         entry: JournalEntry | None = None
     ```
  
  3. **`LiveOrchestrator` class** — now includes transition matrix, `reset()`, validated `_transition()`:
     ```python
     class LiveOrchestrator:
         _TRANSITIONS: dict[OrchestrationState, set[OrchestrationState]] = {
             OrchestrationState.IDLE:    {OrchestrationState.LOAD},
             OrchestrationState.LOAD:    {OrchestrationState.ANALYZE, OrchestrationState.ERROR},
             OrchestrationState.ANALYZE: {OrchestrationState.DECIDE, OrchestrationState.ERROR},
             OrchestrationState.DECIDE:  {OrchestrationState.JOURNAL, OrchestrationState.ERROR},
             OrchestrationState.JOURNAL: {OrchestrationState.IDLE, OrchestrationState.ERROR},
             OrchestrationState.ERROR:   {OrchestrationState.IDLE},   # reset only
         }

         def __init__(self, context: OrchestratorContext,
                      smc_buffer: LiveSmcBuffer | None = None):
             self.state = OrchestrationState.IDLE
             self.context = context
             self._smc_buffer = smc_buffer or LiveSmcBuffer()
             self._last_error: Exception | None = None
         
         def _transition(self, next_state) -> None
         def reset(self) -> None                  # NEW (Gap 2)
         def load(self) -> None                   # replay guard added (Gap 1)
         def analyze(self) -> None
         def decide(self) -> None
         def journal(self) -> None
         def step(self) -> None
     ```
  
  4. **`_transition()` method** — validated against transition matrix (Gap 2):
     ```python
     def _transition(self, next_state: OrchestrationState) -> None:
         allowed = self._TRANSITIONS[self.state]
         if next_state not in allowed:
             raise RuntimeError(
                 f"Invalid transition: {self.state.name} → {next_state.name}. "
                 f"Allowed from {self.state.name}: "
                 f"{[s.name for s in allowed]}"
             )
         self.state = next_state
     ```
     This catches:
     - Caller retrying `step()` from ERROR without reset (ERROR → LOAD invalid)
     - Programming errors (calling `analyze()` before `load()`)
     - Accidental double-transitions

  5. **`reset()` method** — clears runtime context for retry from ERROR (Gap 2):
     ```python
     def reset(self) -> None:
         """Reset orchestrator to IDLE state for retry.
     
         Clears all runtime context fields and last_error.
         Safe to call from any state. Intended for caller-managed
         retry after ERROR.
         """
         self.state = OrchestrationState.IDLE
         self._last_error = None
         self.context.ta_row = None
         self.context.smc_report = None
         self.context.snapshot = None
         self.context.confluence = None
         self.context.narrative = None
         self.context.decision = None
         self.context.entry = None
     ```
     
     Key design:
     - **Not a transition** — sets `self.state` directly, bypassing `_transition()`. Works from ANY state.
     - **Clears runtime fields** — avoids stale-data contamination on retry. Configuration fields (symbol, timeframe, data_dir, mode) are preserved.
     - **No parameters** — one thing, one way.
     - **Idempotent** — multiple `reset()` calls safe.

  6. **`load()` method** — now with replay mode guard (Gap 1):
     ```python
     def load(self) -> None:
         self._transition(OrchestrationState.LOAD)

         if self.context.mode == "replay":
             # Caller pre-populated context.ta_row and context.smc_report.
             # Nothing to fetch — the buffer is unused in replay mode.
             return

         # ── Live mode: fetch fresh data ──
         df = load_ta_series(
             self.context.symbol,
             self.context.timeframe,
             self.context.data_dir,
             tail=1,
         )
         if df is None or df.empty:
             raise RuntimeError(f"No TA data for {self.context.symbol}")
         self.context.ta_row = df.iloc[-1]

         self._smc_buffer.update(self.context.ta_row)
         self.context.smc_report = self._smc_buffer.get_smc_report()
     ```
     
     No other method changes for replay mode. `analyze()`, `decide()`, `journal()` are mode-agnostic — they work identically because `ta_row` and `smc_report` are set either way.
     
     **Replay mode caller pattern** (caller pre-populates context, calls `step()` per candle):
     ```python
     context = OrchestratorContext(symbol="BTC/USDT", timeframe="1h", mode="replay")
     orchestrator = LiveOrchestrator(context)

     for i in range(len(candles)):
         context.ta_row = ta_rows[i]
         context.smc_report = smc_reports[i]   # pre-built 26-col report slice

         orchestrator.step()

         # Read decision for this candle — step() overwrites context each call
         decisions.append(copy(context.decision))
     ```

  7. **`analyze()` method**:
     - Transition to ANALYZE
     - Guard: `ta_row` and `smc_report` must not be None
     - Build snapshot: `SnapshotBuilder().build(symbol, timeframe, ta_row, smc_report)`
     - Score confluence: `ConfluenceScorer().score(snapshot)`
     - Build narrative: `MarketNarrativeBuilder().build(snapshot, confluence)`
  
  8. **`decide()` method**:
     - Transition to DECIDE
     - Guard: `snapshot` and `confluence` must not be None
     - `DecisionEngine().decide(snapshot, confluence)` → `self.context.decision`
  
  9. **`journal()` method** — docstring updated to point to `sync_write_entry()` (Gap 3):
     ```python
     def journal(self) -> None:
         """Build JournalEntry from current context.
     
         Sets ``self.context.entry``. Does NOT write to the database —
         the caller is responsible for persisting the entry via
         ``sync_write_entry()`` or by calling ``JournalWriter.append()``
         directly in an async context.
         """
         self._transition(OrchestrationState.JOURNAL)
         snap = self.context.snapshot
         conf = self.context.confluence
         narr = self.context.narrative
         dec = self.context.decision
         if not all([snap, conf, narr, dec]):
             raise RuntimeError("Cannot journal: missing pipeline data")
         
         events = self._smc_buffer.events
         entry = JournalEntry(
             run_id=make_run_id(snap.symbol, snap.timeframe, snap.timestamp),
             timestamp=snap.timestamp,
             symbol=snap.symbol,
             timeframe=snap.timeframe,
             close=snap.close,
             direction_score=conf.direction_score,
             bias=conf.bias,
             confidence=conf.confidence,
             narrative_summary=narr.conclusion,
             decision_action=dec.action,
             decision_invalidation=dec.invalidation,
             decision_target=dec.target,
             breakout_pending=dec.breakout_pending,
             events=events,
         )
         self.context.entry = entry
     ```

  10. **`step()` method** — uses `sys.exc_info()[1]` for exception capture, no auto-recovery (Gap 2):
      ```python
      def step(self) -> None:
          try:
              self.load()     # no-op in replay mode
              self.analyze()
              self.decide()
              self.journal()
              self._transition(OrchestrationState.IDLE)
          except Exception:
              self._last_error = sys.exc_info()[1]
              self._transition(OrchestrationState.ERROR)
              raise
      ```
      
      **No auto-recovery.** If state is ERROR, the first `_transition(LOAD)` raises "Invalid transition". Caller must call `reset()` before retrying.
      
      **Caller retry pattern** (caller-managed retry — orchestrator has zero retry logic):
      ```python
      orchestrator = LiveOrchestrator(context)

      for attempt in range(3):
          try:
              orchestrator.step()
              break
          except DataAvailabilityError:
              time.sleep(60 * attempt)          # caller decides backoff
              orchestrator.reset()              # must reset before retry
          except Exception:
              orchestrator.reset()
              time.sleep(5)
      ```

  11. **`sync_write_entry()` function** — standalone top-level function in `orchestrator.py` (Gap 3):
      ```python
      import asyncio
      from journal import JournalWriter, JournalEntry

      def sync_write_entry(writer: JournalWriter, entry: JournalEntry) -> None:
          """Write a journal entry synchronously.

          Call after ``orchestrator.step()`` to persist the journal entry
          to SQLite. Uses ``asyncio.run()`` to bridge the async
          ``JournalWriter`` API into synchronous code.

          Usage::

              orchestrator.step()
              sync_write_entry(writer, orchestrator.context.entry)

          For callers already in an async context: use ``await writer.append()`` /
          ``await writer.flush()`` directly instead of this function.
          """
          asyncio.run(writer.append(entry))
          asyncio.run(writer.flush())
      ```
      
      **Caller pattern (live mode)**:
      ```python
      context = OrchestratorContext(symbol="BTC/USDT", timeframe="1h")
      orchestrator = LiveOrchestrator(context)

      writer = JournalWriter("journal.db", buffer_size=0)
      asyncio.run(writer.__aenter__())           # open connection
      try:
          while True:
              orchestrator.step()
              sync_write_entry(writer, orchestrator.context.entry)
              time.sleep(60)                     # candle interval
      finally:
          asyncio.run(writer.flush())
          asyncio.run(writer.__aexit__(None, None, None))
      ```

  12. **Full imports for `orchestrator.py`**:
      ```python
      import asyncio
      import sys
      from copy import copy
      from dataclasses import dataclass
      from enum import Enum, auto

      import pandas as pd

      from live_smc_buffer import LiveSmcBuffer
      from market_snapshot import MarketSnapshot, SnapshotBuilder
      from confluence import ConfluenceResult, ConfluenceScorer
      from narrative import MarketNarrative, MarketNarrativeBuilder
      from decision_engine import Decision, DecisionEngine
      from journal import JournalEntry, JournalWriter, make_run_id
      from trade_scripts.analyze_ta import load_ta_series
      ```

- **Must NOT do**:
  - Do NOT add any business logic, scoring, or strategy to the orchestrator
  - Do NOT add `can_decide()` or any gating
  - Do NOT import or use `MarketContext` (multi-timeframe) in the orchestrator
  - Do NOT call `JournalWriter.append()` directly — use `sync_write_entry()` or caller-managed async
  - Do NOT modify any existing files
  - Do NOT add auto-recovery logic in `step()` — retry is caller's responsibility
  - Do NOT make `_transition()` a no-op — validation must catch invalid transitions

- **Recommended Agent Profile**: `fullstack` — Python state machine patterns, pipeline orchestration, error handling, async bridging

- **Parallelization**: Wave 2, blocked by T1, blocks T4, T5

- **References**:
  - `live_smc_buffer.py` (T1) — `LiveSmcBuffer` class
  - `market_snapshot.py` — `SnapshotBuilder.build()` signature
  - `confluence.py` — `ConfluenceScorer.score()` signature
  - `narrative.py` — `MarketNarrativeBuilder.build()` signature
  - `decision_engine.py` — `DecisionEngine.decide()` signature
  - `journal.py` — `JournalEntry` dataclass, `JournalWriter`, `make_run_id()` function
  - `trade_scripts/analyze_ta.py` — `load_ta_series()` function

- **Acceptance Criteria**:
  - `python -c "from orchestrator import LiveOrchestrator, OrchestratorContext, OrchestrationState, sync_write_entry; print('OK')"` succeeds
  - `LiveOrchestrator(context).state == OrchestrationState.IDLE`
  - `_transition()` raises `RuntimeError` on invalid transitions (e.g., directly from ERROR → LOAD)
  - `reset()` clears all runtime context and returns state to IDLE
  - `step()` completes all 4 pipeline methods and returns to IDLE
  - `step()` transitions to ERROR and raises on pipeline failure
  - `journal()` produces a valid `JournalEntry` with all fields set
  - `sync_write_entry()` writes an entry to a `JournalWriter` without error

- **QA Scenarios**:
  1. **Happy path — state machine transitions**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: `orchestrator.py` exists, `live_smc_buffer.py` exists, test TA data available
     - **Steps**:
       1. `python -c "from orchestrator import LiveOrchestrator, OrchestratorContext, OrchestrationState; ctx = OrchestratorContext('BTC/USDT', '1d'); o = LiveOrchestrator(ctx); print(o.state)"` 
       2. Verify initial state is IDLE
       3. Run `step()` (will fail because no TA data file exists) — verify ERROR state
     - **Expected Result**: Initial state is IDLE, step transitions to ERROR when data is missing
     - **Evidence**: `.sisyphus/evidence/t2-state-transitions.log`
  
  2. **Error propagation — missing data**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: orchestrator exists
     - **Steps**:
       1. Create context with non-existent data_dir
       2. Call `step()`
       3. Assert `RuntimeError` is raised
       4. Assert `state == OrchestrationState.ERROR`
       5. Assert `_last_error` is not None
     - **Expected Result**: Error is properly caught, state transitions to ERROR
     - **Evidence**: `.sisyphus/evidence/t2-error-handling.log`
  
  3. **Invalid transition rejection**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: orchestrator exists
     - **Steps**:
       1. Create orchestrator in ERROR state (simulate by calling `step()` on broken data)
       2. Call `_transition(OrchestrationState.LOAD)` directly (bypass step)
       3. Assert `RuntimeError` is raised with "Invalid transition: ERROR → LOAD"
       4. Call `reset()` — state becomes IDLE
       5. Now `_transition(LOAD)` succeeds
     - **Expected Result**: Invalid transition raises, reset fixes it
     - **Evidence**: `.sisyphus/evidence/t2-invalid-transition.log`
  
  4. **Reset clears context**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: orchestrator exists
     - **Steps**:
       1. Set context fields manually (snapshot, confluence, etc.)
       2. Call `reset()`
       3. Assert all runtime fields are None
       4. Assert `state == IDLE`
       5. Assert `_last_error` is None
     - **Expected Result**: All runtime state cleared, IDLE
     - **Evidence**: `.sisyphus/evidence/t2-reset.log`
  
  5. **Replay mode guard**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: orchestrator exists
     - **Steps**:
       1. Create `OrchestratorContext` with `mode="replay"`
       2. Pre-populate `context.ta_row` and `context.smc_report`
       3. Call `load()` directly
       4. Assert `ta_row` and `smc_report` unchanged (no overwrite)
       5. Call `step()` — should complete without fetching data
     - **Expected Result**: Replay mode skips data fetch, uses pre-populated context
     - **Evidence**: `.sisyphus/evidence/t2-replay-mode.log`
  
  6. **Journal entry construction**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: orchestrator exists
     - **Steps**:
       1. Manually construct a `LiveOrchestrator` with a mocked `LiveSmcBuffer` (one that returns empty events)
       2. Set up all context fields manually (simulate completed pipeline)
       3. Call `journal()` directly
       4. Verify `JournalEntry` has all required fields populated
     - **Expected Result**: JournalEntry is valid, no exceptions
     - **Evidence**: `.sisyphus/evidence/t2-journal-entry.log`
  
  7. **sync_write_entry bridge**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: orchestrator exists, journal module available
     - **Steps**:
       1. Create temp SQLite database
       2. Create `JournalWriter` and open connection via `asyncio.run()`
       3. Create a `JournalEntry` manually
       4. Call `sync_write_entry(writer, entry)`
       5. Query `journal_runs` table to verify write
     - **Expected Result**: Entry written to DB, queryable
     - **Evidence**: `.sisyphus/evidence/t2-sync-write.log`

---

### T3 — Unit tests for `LiveSmcBuffer`

- **What to do**: Write `tests/test_live_smc_buffer.py` with pytest tests covering:

  1. **Construction tests**:
     - Default constructor creates all engines
     - Custom parameters passed through to `_SwingEngine`
     - Custom `bos_confirmation_window` passed to `StructureEngine`
  
  2. **Update tests**:
     - Single update with fake OHLCV row returns dict with expected keys
     - Multiple updates accumulate `_candle_index` correctly
     - Early updates (before enough data) return NaN swing values
     - OHLCV extraction from TA-enriched row works (row has extra columns beyond OHLCV)
  
  3. **Swing confirmation triggers**:
     - Use known swing sequence (from `test_structure_engine.py` helper patterns) 
     - When swing is confirmed, `_recompute_downstream()` is called
     - After downstream, OB columns in report have non-NaN values
  
  4. **Structure engine integration**:
     - On swing confirmation, `StructureEngine.update()` is called
     - `StructureEngine.check_confirmations()` is called every bar
     - `events` property returns accumulated StructureEvents
  
  5. **Report tests**:
     - `get_smc_report()` returns a DataFrame
     - Report has correct number of columns (26)
     - Report has expected column names
     - Report is trimmed to 200 rows max
     - Forward-fill behavior for SMC columns works correctly
  
  6. **Edge cases**:
     - Row with missing columns raises helpful error
     - `update()` before enough data doesn't crash
     - Buffer handles empty constructor (no swings ever confirmed)
     - Report with no data returns empty DataFrame

- **Must NOT do**:
  - Do NOT test SnapshotBuilder or any other pipeline class — only LiveSmcBuffer
  - Do NOT test async JournalWriter — that's for integration test
  - Do NOT use real exchange data — use synthetic OHLCV sequences
  - Do NOT modify `conftest.py` or any existing test file

- **Recommended Agent Profile**: `test` — pytest, mocking, numpy/pandas test data generation

- **Parallelization**: Wave 3, blocked by T1, blocks T5

- **References**:
  - `tests/conftest.py` — `constant_price_df`, `linear_trend_df` fixture patterns
  - `tests/test_structure_engine.py` — swing sequence patterns for test data
  - `live_smc_buffer.py` (T1) — the class under test

- **Acceptance Criteria**:
  - `pytest tests/test_live_smc_buffer.py -v` passes all tests
  - All test classes and methods follow existing project test patterns
  - Tests complete in < 30 seconds

- **QA Scenarios**:
  1. **Full test suite pass**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: `test_live_smc_buffer.py` exists, `live_smc_buffer.py` exists
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -m pytest tests/test_live_smc_buffer.py -v 2>&1`
     - **Expected Result**: All tests pass (PASSED/OK), 0 failures, 0 errors
     - **Evidence**: `.sisyphus/evidence/t3-test-pass.log`
  
  2. **Coverage check**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: Tests exist
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -m pytest tests/test_live_smc_buffer.py --coverage 2>&1 || python -m pytest tests/test_live_smc_buffer.py -x --tb=short 2>&1`
     - **Expected Result**: Tests cover construction, update, swing trigger, report, edge cases
     - **Evidence**: `.sisyphus/evidence/t3-coverage.log`

---

### T4 — Unit tests for `LiveOrchestrator`

- **What to do**: Write `tests/test_orchestrator.py` with pytest tests covering:

  1. **Construction & state tests**:
     - Default construction has IDLE state
     - Custom `LiveSmcBuffer` is accepted
     - `OrchestratorContext` defaults are applied
  
  2. **Mock fixture**: Provide `mock_smc_buffer` fixture that returns a `LiveSmcBuffer`-like object with:
     - `update()` returning `{"HighLow": np.nan, "Level": np.nan}`
     - `get_smc_report()` returning a minimal 26-column DataFrame
     - `events` property returning empty list
  
  3. **Pipeline method guard tests**:
     - `analyze()` before `load()` raises RuntimeError
     - `decide()` before `analyze()` raises RuntimeError
     - `journal()` before `decide()` raises RuntimeError
  
  4. **State transition tests**:
     - Each method correctly transitions to its state
     - `step()` completes all 4 transitions and returns to IDLE
     - `step()` transitions to ERROR on exception
  
  5. **End-to-end with mock buffer**:
     - Create orchestrator with mock buffer + mock TA data
     - Call `step()` 
     - Verify all context fields are populated after step
     - Verify journal entry is created with correct fields
  
  6. **Error handling**:
     - `load()` with missing data file raises RuntimeError
     - Exception in any pipeline step propagates to `step()` caller
     - `_last_error` is set after error
     - State is ERROR after error

  7. **Transition validation tests (Gap 2)**:
     - Direct transition from ERROR → LOAD raises RuntimeError
     - Direct transition from IDLE → DECIDE raises RuntimeError (must go through LOAD → ANALYZE)
     - Direct transition from JOURNAL → LOAD raises RuntimeError (must go through IDLE first)
     - Error message contains current state, attempted next state, and allowed transitions
  
  8. **`reset()` tests (Gap 2)**:
     - `reset()` from ERROR clears `_last_error`, sets state to IDLE
     - `reset()` clears all runtime context fields (ta_row, smc_report, snapshot, etc.)
     - `reset()` preserves configuration fields (symbol, timeframe, data_dir, mode)
     - `reset()` from IDLE is safe (no-op on state, clears already-None fields)
     - Multiple `reset()` calls are idempotent
     - `reset()` bypasses `_transition()` validation (works from any state)
  
  9. **Replay mode tests (Gap 1)**:
     - `OrchestratorContext` with `mode="replay"` does not crash
     - `load()` with `mode="replay"` does not call `load_ta_series()` or `_smc_buffer.update()`
     - `step()` with `mode="replay"` and pre-populated context works without TA data file
     - `step()` with `mode="replay"` but missing `ta_row` still raises in `analyze()` (correct behavior)
  
  10. **`sync_write_entry()` tests (Gap 3)**:
      - `sync_write_entry()` is importable from `orchestrator`
      - Function accepts `JournalWriter` and `JournalEntry` without error
      - Function successfully writes entry to SQLite (uses temp file, verifies via `query_runs()`)
      - Function handles empty `JournalEntry.events` list correctly

- **Must NOT do**:
  - Do NOT test with real `LiveSmcBuffer` — use mock (that's T5's job)
  - Do NOT test async JournalWriter except via `sync_write_entry()` (which wraps it)
  - Do NOT test any pipeline component (SnapshotBuilder, etc.) — those are tested elsewhere

- **Recommended Agent Profile**: `test` — pytest, mocking, state machine testing, error path testing

- **Parallelization**: Wave 4, blocked by T1 + T2, blocks T5

- **References**:
  - `orchestrator.py` (T2) — the class under test
  - `live_smc_buffer.py` (T1) — for mock interface
  - `tests/conftest.py` — for fixture patterns
  - `tests/test_market_snapshot.py` — for pipeline test patterns

- **Acceptance Criteria**:
  - `pytest tests/test_orchestrator.py -v` passes all tests
  - Mock buffer tests verify state transitions without real data
  - Error path tests verify all guard conditions
  - Tests complete in < 15 seconds

- **QA Scenarios**:
  1. **Full test suite pass**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: `test_orchestrator.py` exists
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -m pytest tests/test_orchestrator.py -v 2>&1`
     - **Expected Result**: All tests pass
     - **Evidence**: `.sisyphus/evidence/t4-test-pass.log`
  
  2. **Mock buffer isolation**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: Tests exist
     - **Steps**: Run pytest verifying that mock buffer is used (no real LiveSmcBuffer instantiation)
     - **Expected Result**: No real file I/O or LiveSmcBuffer dependency in unit tests
     - **Evidence**: `.sisyphus/evidence/t4-isolation.log`

---

### T5 — Integration test — full cycle with `LiveSmcBuffer` + `Journal`

- **What to do**: Write `tests/test_orchestrator_integration.py` with:

  1. **Synthetic TA data fixture**: Build a CSV file with 200 rows of OHLCV data + TA indicator columns (ema21, rsi14, macd, etc.) at a temp path. Use `conftest.py` patterns to generate close prices that produce a known swing pattern.

  2. **Integration test: full cycle (live mode)**:
     - Write a 200-row TA CSV with OHLCV + indicator columns to temp directory
     - Create `OrchestratorContext` pointing to temp data (`mode="live"`)
     - Create `LiveSmcBuffer` with fast params (swing_length=5, confirmation_bars=2)
     - Create `LiveOrchestrator`
     - Call `step()` once — loads last row of TA data via `load_ta_series(tail=1)`, updates buffer
     - Call `sync_write_entry()` to persist the journal entry
     - Verify:
       - `context.entry` is not None after step
       - `sync_write_entry()` writes to SQLite successfully
       - `journal_runs` table has 1 row matching the decision
       - `journal_events` table has entries if StructureEngine produced events

  3. **Integration test: replay mode cycle**:
     - Pre-compute a 200-row TA DataFrame + SMC report slices
     - Create `OrchestratorContext(symbol, timeframe, mode="replay")`
     - Create `LiveOrchestrator` with the same `LiveSmcBuffer`
     - For each candle `i`:
       - Set `context.ta_row = ta_rows[i]`
       - Set `context.smc_report = smc_reports[i]`
       - Call `orchestrator.step()` (load() is no-op in replay mode)
       - Verify `context.decision` is populated
       - Call `sync_write_entry(writer, context.entry)` to persist
     - Verify all entries were written to SQLite

  4. **Journal write verification**:
     - Use `sync_write_entry()` (the function from orchestrator.py) as the bridge
     - Create `JournalWriter` pointing to temp SQLite DB
     - Call `sync_write_entry(writer, context.entry)` after each step
     - Call `query_runs()` to verify entries were written
     - Verify `journal_events` table has entries for StructureEngine events
     - Verify multiple entries are written correctly (no cross-contamination)

  4. **Cleanup**: Remove temp files after test (use `tmp_path` fixture)

- **Must NOT do**:
  - Do NOT test with real exchange data — use synthetic data only
  - Do NOT modify any existing pipeline component
  - Do NOT leave temp files behind

- **Recommended Agent Profile**: `test` — integration testing, asyncio, SQLite, full pipeline verification

- **Parallelization**: Wave 5, blocked by T1-T4

- **References**:
  - `live_smc_buffer.py` (T1) — LiveSmcBuffer
  - `orchestrator.py` (T2) — LiveOrchestrator, OrchestratorContext
  - `journal.py` — JournalWriter, JournalEntry
  - `tests/conftest.py` — fixture patterns (constant_price_df, linear_trend_df)
  - `tests/test_journal.py` — async test patterns

- **Acceptance Criteria**:
  - `pytest tests/test_orchestrator_integration.py -v` passes all tests
  - Full cycle test (live mode): buffer processes 200 candles → snapshot built → decision made → entry written via `sync_write_entry()` → SQLite verified
  - Full cycle test (replay mode): pre-populated context processes 200 candles with `mode="replay"`, each step produces decision, all entries written to SQLite
  - Integration test cleans up temp files
  - Tests complete in < 60 seconds

- **QA Scenarios**:
  1. **Full cycle integration (live mode)**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: All T1-T4 files exist, temp TA CSV available
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -m pytest tests/test_orchestrator_integration.py -v -x --tb=long 2>&1`
     - **Expected Result**: All integration tests pass
     - **Evidence**: `.sisyphus/evidence/t5-integration-pass.log`
  
  2. **SQLite journal verification**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: Integration test has run and created a journal.db
     - **Steps**: Verify the SQLite DB exists and has correct schema
     - **Expected Result**: Database has `journal_runs` and `journal_events` tables with at least 1 row each
     - **Evidence**: `.sisyphus/evidence/t5-db-verify.log`
  
  3. **Replay mode integration**:
     - **Tool**: `interactive_bash`
     - **Preconditions**: All T1-T4 files exist, pre-computed TA + SMC report data
     - **Steps**: Run integration replay mode test
     - **Expected Result**: All replay mode tests pass, no TA data file reads occur
     - **Evidence**: `.sisyphus/evidence/t5-replay-mode.log`

---

## Final Verification Wave

All tasks below run AFTER all 5 tasks are complete and passing.

### F1 — Plan Compliance Audit (oracle)
- Verify all 5 deliverables match the plan spec
- Verify no unintended modifications to existing files (`git diff --name-only`)
- Verify guardrails: no business logic in orchestrator, no reimplementation of SMC
- **Evidence**: `.sisyphus/evidence/f1-compliance.log`

### F2 — Code Quality Review (unspecified-high)
- Read all new files for: type annotations, docstrings, error handling
- Check import cleanliness (no unused imports)
- Verify `_SwingEngine` and `StructureEngine` are used, not duplicated
- **Evidence**: `.sisyphus/evidence/f2-quality.log`

### F3 — Real Manual QA (unspecified-high)
- Create a real OHLCV sequence (EURUSD 15M or synthetic) and process through full pipeline
- Verify the 26-column report is parseable by `SnapshotBuilder.build()`
- Verify `JournalEntry` fields are correctly populated from pipeline results
- **Evidence**: `.sisyphus/evidence/f3-manual-qa.log`

### F4 — Scope Fidelity Check (deep)
- Verify no scope creep: no strategy, no gating, no MTF in orchestrator
- Verify no changes to `backtest.py`, `smartmoneyconcepts/`, or other existing files
- Verify the one-line change explicitly: `load_ta_series(tail=1).iloc[-1]`
- **Evidence**: `.sisyphus/evidence/f4-scope.log`

## Commit Strategy

Single commit with message:
```
feat: add LiveSmcBuffer streaming accumulator + LiveOrchestrator state machine

- Add LiveSmcBuffer wrapping _SwingEngine + StructureEngine with batch
  OB/liquidity/retracements on swing confirmation
- Add rolling 26-column SMC report compatible with SnapshotBuilder
- Add OrchestrationState enum and OrchestratorContext dataclass
- Add LiveOrchestrator with 5-phase pipeline (load → analyze → decide → journal)
- Add validated transition matrix (_TRANSITIONS dict + _transition() guard)
- Add reset() method for caller-managed retry from ERROR state
- Add mode="live|replay" to OrchestratorContext with replay no-op in load()
- Add sync_write_entry() function bridging sync orchestrator to async JournalWriter
- Add unit tests for LiveSmcBuffer (swing accumulation, batch triggers, report)
- Add unit tests for LiveOrchestrator (state transitions, error recovery,
  transition validation, reset, replay mode, sync_write_entry)
- Add integration test for full cycle with sync_write_entry + SQLite

No changes to existing pipeline classes or smartmoneyconcepts modules.
```

## Success Criteria

1. **All tests pass**: `pytest tests/test_live_smc_buffer.py tests/test_orchestrator.py tests/test_orchestrator_integration.py -v` — 0 failures, 0 errors
2. **No existing code modified**: `git diff --name-only` shows only new files
3. **Guardrails enforced**: LiveSmcBuffer uses `_SwingEngine` + `StructureEngine` directly; Orchestrator has no business logic
4. **Report compatible**: `get_smc_report()` output can be passed to `SnapshotBuilder.build()` without errors
5. **Journal functional**: Full pipeline produces valid JournalEntry; `sync_write_entry()` persists it to SQLite
6. **State machine correct**: Orchestrator follows IDLE → LOAD → ANALYZE → DECIDE → JOURNAL → IDLE cycle, ERROR on failure
7. **Transition validation**: Invalid transitions (e.g., ERROR → LOAD without reset) raise RuntimeError; callers must `reset()` first
8. **Replay mode**: `mode="replay"` skips data fetch; pre-populated context flows through step() without file I/O
9. **Async bridge**: `sync_write_entry()` writes JournalEntry synchronously using `asyncio.run()`; orchestrator remains pure sync
