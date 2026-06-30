# Oracle Review: Orchestrator State Machine Design

**Reviewer**: Oracle (Strategic Technical Advisor)
**Date**: 2026-06-24
**Scope**: Proposed `LiveOrchestrator` state machine design for wiring the live trading pipeline

---

## Bottom Line

**Verdict: CONDITIONALLY READY FOR PLANNING — but ${\color{red} \text{4 blocking questions}}$ must be resolved before Prometheus writes a plan.**

The state machine shape (IDLE → LOAD → ANALYZE → DECIDE → JOURNAL → IDLE) is structurally sound and aligns with the pipeline's natural data flow. The design gets the big things right: single `step()` entry point, explicit state transitions, pure observer journal, and external retry wrapping.

However, the proposal has **3 correctness issues** and **4 data-flow gaps** that will cause the first implementation attempt to stall:

**Correctness issues:**
1. `can_decide()` checks `decision.action` — but `decision` is created *inside* `decide()`. The guard cannot reference its own output.
2. No valid transition matrix — `_transition()` blindly sets state without enforcing which transitions are legal. A retry from ERROR state would silently succeed (or silently fail).
3. ANALYZE collapses 3 distinct pipeline steps (build snapshot, score confluence, build narrative) into one state — not inherently wrong, but the state name conceals the real sequencing.

**Data-flow gaps:**
4. There is **no live SMC report pipeline** — `SnapshotBuilder.build()` requires a full 26-column SMC per-candle DataFrame. The backtest produces this via batch analysis (not streaming). No component currently produces this incrementally for live mode.
5. `load_ta_latest()` returns only `{mfi14, obv, obv_slope, close, timestamp}` — not the full TA row (`ema21`, `rsi14`, `macd`, `atr14`, `bb_width`, etc.) that `SnapshotBuilder.build()` requires.
6. `JournalEntry.events` (list of `StructureEvent`) has no defined provenance in the orchestrator — the events are produced by `StructureEngine` which isn't mentioned in the design.
7. Context schema is undefined — intermediate pipeline products (snapshot, confluence, narrative, events) have no storage contract between states.

**Live vs replay ambiguity:**
8. The same pipeline must handle two fundamentally different data sources, but the design doesn't specify how they are distinguished or configured.

---

## State Machine Review

### Completeness

The proposed states cover the high-level cycle, but the internal structure of `ANALYZE` hides real work:

| State | Pipeline Step | Input | Output |
|-------|--------------|-------|--------|
| `LOAD` | Fetch raw market data | — | `ta_row`, `smc_report` **(see blocking question 2)** |
| `ANALYZE` step a | `SnapshotBuilder.build()` | `ta_row`, `smc_report` | `MarketSnapshot` |
| `ANALYZE` step b | `ConfluenceScorer.score()` | `MarketSnapshot` | `ConfluenceResult` |
| `ANALYZE` step c | `MarketNarrativeBuilder.build()` | `MarketSnapshot`, `ConfluenceResult` | `MarketNarrative` |
| `DECIDE` | `DecisionEngine.decide()` | `MarketSnapshot`, `ConfluenceResult` | `Decision` |
| `JOURNAL` | `JournalWriter.append()` | `JournalEntry` (assembled from all above) | SQLite row |

**Judgment**: Bundling (a), (b), and (c) into a single `ANALYZE` state is fine for a first implementation. Splitting them would add ceremony without benefit — the intermediate products are always created together. If monitoring granularity is needed, add instrumentation inside the method rather than more states. **Keep ANALYZE as one state.**

**Missing states?** None. The flat sequence is correct. Add states only if you need to fail/recover between specific pipeline stages (e.g., "snapshot succeeded but confluence failed"). That level of granular recovery doesn't match the current requirements — any failure restarts the whole cycle.

### The `can_decide()` Guard Problem

The proposed guard:

```python
def can_decide(self, decision) -> bool:
    return decision.action != "stand_aside" or decision.confidence >= 0.7
```

**This cannot work as written.** `decision` is produced by `DecisionEngine.decide()`, which runs inside the `DECIDE` state. By the time `decision` exists, the guard's question has already been answered.

Your intent is clear — skip the `DECIDE` state when the confluence result doesn't warrant a decision. The fix is: **the guard should inspect the ConfluenceResult, not the Decision.**

```python
def should_decide(self) -> bool:
    """Skip DECIDE if confluence shows no actionable signal."""
    return not (self.confluence.bias == "neutral"
                and self.confluence.confidence < 0.5)
```

But even this raises a question: **should you skip JOURNAL too?** The journal is meant to record every cycle. If you skip DECIDE + JOURNAL for neutral confluences, the journal has gaps. If you still journal, you're journaling a snapshot+confluence but no decision — which may be fine, but it's a design choice. See [Blocking Question 4](#blocking-question-4-guard-strategy).

### Transition Matrix

The current `_transition()` method silently accepts any state change. Here is the **valid transition matrix** the design implies but doesn't enforce:

```
Current State  →  Allowed Next States
─────────────────────────────────────────
IDLE           →  LOAD
LOAD           →  ANALYZE, ERROR
ANALYZE        →  DECIDE, ERROR
DECIDE         →  JOURNAL, ERROR
JOURNAL        →  IDLE, ERROR
ERROR          →  IDLE              (retry: go back to start)
ERROR          →  (terminal)        (abort: stay in ERROR)
```

**Known invalid transitions** — `_transition()` should reject these at runtime:
- IDLE → DECIDE (skip loading)
- ANALYZE → JOURNAL (skip decision)
- JOURNAL → ANALYZE (go backward without reinitializing)

**Recommendation**: Define transitions as a `dict[OrchestrationState, set[OrchestrationState]]` in `__init__` and validate in `_transition()`. This catches programming errors early and makes the flow explicit.

---

## Pipeline Integration

### How Each State Maps to Existing Components

| State | Component Called | Method Signature | What's Missing |
|-------|-----------------|-----------------|----------------|
| `LOAD` | `load_ta_latest()` or `load_ta_series()` | `load_ta_latest(symbol, timeframe, data_dir) → dict \| None` | Returns only `{mfi14, obv, obv_slope, close, timestamp}` — insufficient for `SnapshotBuilder.build()` which needs the full `pd.Series` with all TA columns |
| `LOAD` | (no existing component for live SMC) | — | **No component** produces a live `smc_report` DataFrame. The backtest does this via `batch_analysis_phase()` + `build_per_candle_report()`, neither of which is streaming |
| `ANALYZE` | `SnapshotBuilder().build()` | `build(symbol, timeframe, ta_row: pd.Series, smc_report: pd.DataFrame) → MarketSnapshot` | Requires `ta_row` as `pd.Series` (not the subset dict returned by `load_ta_latest`) |
| `ANALYZE` | `ConfluenceScorer().score()` | `score(snapshot: MarketSnapshot) → ConfluenceResult` | No gaps — direct snapshot → score |
| `ANALYZE` | `MarketNarrativeBuilder().build()` | `build(snapshot, result: ConfluenceResult) → MarketNarrative` | No gaps — direct snapshot+score → narrative |
| `DECIDE` | `DecisionEngine().decide()` | `decide(snapshot, result, context: MarketContext \| None) → Decision` | `MarketContext` is optional multi-TF; single-TF mode passes `None` |
| `JOURNAL` | `JournalWriter().append()` | `append(entry: JournalEntry)` | `JournalEntry` requires `events: list[StructureEvent]` — see [data flow gap](#the-events-flow-gap) |

### The Events Flow Gap

`JournalEntry.events` requires a `list[StructureEvent]`. These are produced by `StructureEngine.check_confirmations()` on every candle and `StructureEngine.update()` on each confirmed swing. In the backtest, they accumulate in `all_structure_events`. In live mode, `StructureEngine` maintains `self._all_events: list[StructureEvent]`. The orchestrator needs to:

1. Own or reference a `StructureEngine` instance
2. Call `engine.check_confirmations(i, high, low)` and `engine.update(swing)` during the `LOAD` or `ANALYZE` state
3. Read `engine.events` (or a filtered sub-list) when building the `JournalEntry`

**This is not mentioned in the proposed design.** The orchestrator must either integrate the `StructureEngine` or accept events as a pre-built input (e.g., passed in context from a higher-level loop).

---

## Context Schema

The proposal uses `self.context` but leaves it undefined. Here is the **minimum schema** the orchestrator needs, based on the actual component interfaces:

```python
@dataclass
class OrchestratorContext:
    # ── Configuration (read-only, set at construction) ──
    symbol: str
    timeframe: str
    data_dir: str                # for load_ta_latest() etc.
    mode: Literal["live", "replay"]

    # ── LOAD state outputs ──
    ta_row: pd.Series | None = None          # Full row with all TA indicators
    smc_report: pd.DataFrame | None = None   # 26-column per-candle report (or None in live mode)
    events: list[StructureEvent] | None = None  # Active structure events this cycle

    # ── ANALYZE state outputs ──
    snapshot: MarketSnapshot | None = None
    confluence: ConfluenceResult | None = None
    narrative: MarketNarrative | None = None

    # ── DECIDE state outputs ──
    decision: Decision | None = None
```

**Key requirements:**
- `mode` determines how `load()` populates `ta_row` and `smc_report`
- `events` is populated by the `StructureEngine` (or passed in from a higher-level loop)
- All pipeline outputs are `Optional` — any state can fail, leaving its outputs `None`
- The context is mutated in-place by each state. No deep-copying needed (these are pure-data objects).

---

## Live vs Replay

The user's claim that "same pipeline for live and replay" is **mostly true but with critical caveats**.

### Data Source Differences

| Aspect | Live | Replay |
|--------|------|--------|
| TA data | `load_ta_latest()` reads last row from `ohlcv_{sym}_{tf}_ta.csv` | Pre-loaded DataFrame row from backtest data |
| SMC data | Requires streaming `StructureEngine` — no batch analysis is possible (no future data) | Full 26-column `per_candle_report` available (computed in Phase 2 batch) |
| `smc_report` | `SnapshotBuilder.build()` receives a rolling window of the SMC report (last N candles), built incrementally by `StructureEngine` | `SnapshotBuilder.build()` receives the full per_candle_report sliced up to current candle |
| TA row columns | `load_ta_latest()` returns only 5 fields — insufficient for `SnapshotBuilder.build()` | Full DataFrame row with all TA indicators |

### Recommended Resolution

**Separate the data-loading strategy from the pipeline.** The `Orchestrator` class should receive a `DataProvider` (or similar abstraction) that encapsulates the difference:

```python
class DataProvider(Protocol):
    def load(self, context: OrchestratorContext) -> None: ...

class LiveDataProvider:
    def load(self, context: OrchestratorContext) -> None:
        # load_ta_series() for full TA row
        # maintain streaming StructureEngine for SMC data
        pass

class ReplayDataProvider:
    def __init__(self, iterator):
        self._iterator = iterator
    def load(self, context: OrchestratorContext) -> None:
        # next(self._iterator) for the next candle's data
        pass
```

The orchestrator's `load()` method delegates to `self.provider.load(self.context)`. The `ReplayDataProvider` wraps the `for candle in candles` loop and yields one candle per `load()` call. This keeps the orchestrator clean and testable.

### Simpler Alternative (Preferred for V1)

If you want to avoid the abstraction overhead, use a single conditional in `load()`:

```python
def load(self):
    if self.context.mode == "live":
        df = load_ta_series(self.context.symbol, self.context.timeframe,
                            self.context.data_dir, tail=1)
        self.context.ta_row = df.iloc[-1] if df is not None else None
        # SMC: update streaming StructureEngine, build rolling smc_report
    else:  # replay
        candle = next(self._candle_iterator)
        self.context.ta_row = candle  # candle IS the full ta_row
        self.context.smc_report = self._build_rolling_report(candle)
```

**This is fine for V1.** The conditional is isolated to `load()`, and the rest of the pipeline is identical.

---

## Error Handling

### Current Design Gaps

**1. No recovery from ERROR state.** After `step()` catches an exception, `self.state = OrchestrationState.ERROR`. The next call to `step()` will try to transition from ERROR to LOAD — but no transition validation exists, so this silently succeeds. The orchestrator proceeds as if nothing happened, which may mask persistent failures.

**2. `_transition()` has no guard.** It's currently `self.state = next_state`. No validation, no logging, no side-effects. A mis-coded state machine will silently produce invalid state sequences.

**3. Stale context data on retry.** After an error in ANALYZE, `self.context.snapshot` may be partially populated (or contain data from the previous successful cycle). A retry from LOAD would overwrite it, but if the retry path skips LOAD and goes directly to ANALYZE, stale data corrupts the pipeline.

### Recommended Recovery Semantics

```python
def step(self):
    if self.state == OrchestrationState.ERROR:
        # Require explicit reset or let step() auto-reset to IDLE
        self._transition(OrchestrationState.IDLE)
        # Re-initialize context fields for the new cycle
        self._reset_context()
```

**Valid transition enforcement** (minimum):

```python
_TRANSITIONS: dict[OrchestrationState, set[OrchestrationState]] = {
    OrchestrationState.IDLE:    {OrchestrationState.LOAD},
    OrchestrationState.LOAD:    {OrchestrationState.ANALYZE, OrchestrationState.ERROR},
    OrchestrationState.ANALYZE: {OrchestrationState.DECIDE, OrchestrationState.ERROR},
    OrchestrationState.DECIDE:  {OrchestrationState.JOURNAL, OrchestrationState.ERROR},
    OrchestrationState.JOURNAL: {OrchestrationState.IDLE, OrchestrationState.ERROR},
    OrchestrationState.ERROR:   {OrchestrationState.IDLE},
}

def _transition(self, next_state: OrchestrationState) -> None:
    allowed = self._TRANSITIONS[self.state]
    if next_state not in allowed:
        raise RuntimeError(
            f"Invalid transition: {self.state.name} → {next_state.name}. "
            f"Allowed: {[s.name for s in allowed]}"
        )
    self.state = next_state
```

### Retry Wrapping

The user proposed keeping retries outside the orchestrator. This is **correct** — the orchestrator is a single-cycle unit. Retry logic is a separate concern:

```python
# Outer loop (not inside orchestrator)
orchestrator = LiveOrchestrator(context)
while True:
    try:
        orchestrator.step()
    except LoadError:
        time.sleep(60)  # backoff for data availability
    except Exception:
        orchestrator.reset()  # reset to IDLE
        continue
```

The retry wrapper should call `orchestrator.reset()` (which sets state back to IDLE and clears context) before retrying. This prevents stale-context corruption.

---

## File Organization

**Recommendation: Single file — `orchestrator.py`**

Contents:
- `OrchestrationState(Enum)` — 6 values, trivially small
- `OrchestratorContext` — dataclass, ~15 fields
- `LiveOrchestrator` — the class itself
- `DataProvider(Protocol)` — if using the abstraction; otherwise skip

**Rationale:**
- All three types are tightly coupled — splitting them across files creates import cycles
- ~250 lines total at most — far below the threshold where splitting is justified
- Single file means Prometheus can create it in one write

**Exception**: If a `ReplayDataProvider` or `LiveDataProvider` class grows beyond ~50 lines of non-trivial logic, extract it to `data_providers.py`. But start with everything in `orchestrator.py`.

---

## Testing Review

The proposed test strategy is correct. Here are the concrete test cases mapped to states:

| Test | Sequence | Validates |
|------|----------|-----------|
| `test_step_full_cycle` | IDLE → LOAD → ANALYZE → DECIDE → JOURNAL → IDLE | Happy path, all states transition correctly |
| `test_step_load_failure` | LOAD → ERROR, step raises | Error path, state stuck at ERROR |
| `test_step_analyze_failure` | LOAD → ANALYZE → ERROR | Mid-pipeline failure |
| `test_step_journal_failure` | ... → JOURNAL → ERROR | Late-stage failure |
| `test_retry_from_error` | ERROR → IDLE → LOAD → ... → IDLE | Recovery path |
| `test_invalid_transition` | IDLE → DECIDE raises `RuntimeError` | Transition guard works |
| `test_guard_skips_decide` | LOAD → ANALYZE → JOURNAL (skip DECIDE) | Guard correctly skips states |
| `test_context_persistence` | Verify `context.snapshot` set after ANALYZE | Intermediate data flows |

**Mocking strategy**: Mock `load_ta_series()`, `SnapshotBuilder.build()`, `ConfluenceScorer.score()`, `MarketNarrativeBuilder.build()`, `DecisionEngine.decide()`, and `JournalWriter.append()`. The orchestrator should never need real data in unit tests.

---

## Blocking Questions

### Blocking Question 1: Live SMC Data

SnapshotBuilder.build() requires an `smc_report: pd.DataFrame` with 26 specific columns (SwingHighLow, SwingLevel, BOS, CHOCH, BrokenIndex, Liquidity, LiqLevel, OB, OBTop, OBBottom, etc.). In the backtest, this is built via `build_per_candle_report()` which runs batch analysis (Phase 2) on the full dataset. **There is no live streaming equivalent.**

**Decision needed:** In live mode, how is the SMC report produced for `SnapshotBuilder.build()`?

- Option A: Run the streaming `StructureEngine` in the LOAD state and build an in-memory rolling report (~last 100 candles) with SMC columns. This requires implementing a live equivalent of `build_per_candle_report()` that operates on an accumulating window.
- Option B: Delay the orchestrator until a full SMC report can be built from a completed window (e.g., every N candles, after batch analysis). This changes the real-time semantics.
- Option C: Modify `SnapshotBuilder.build()` to accept `StructureEngine` events directly instead of requiring the 26-column DataFrame (a refactor of the snapshot builder).

**This is the highest-impact unresolved question.** Prometheus cannot code `load()` without knowing the SMC data source in live mode.

### Blocking Question 2: TA Row Source

`load_ta_latest()` returns only `{mfi14, obv, obv_slope, close, timestamp}` — a 5-field dict. `SnapshotBuilder.build()` needs a `pd.Series` with all TA columns: `close, ema21, ema21_slope, rsi14, mfi14, macd, macd_signal, macd_hist, atr14, bb_width`.

`load_ta_series()` already reads the full enriched CSV and returns a `pd.DataFrame`, which can provide the full `ta_row` as a `pd.Series`.

**Decision needed:** Should `load()` use `load_ta_series(tail=1).iloc[-1]` instead of `load_ta_latest()`? This is a trivial fix but must be decided before coding.

### Blocking Question 3: Events Flow

`JournalEntry.events: list[StructureEvent]` has no source in the proposed design. The `StructureEngine` that produces these events must be instantiated and managed somewhere.

**Decision needed:** Does the orchestrator own a `StructureEngine` instance, or are events passed in from outside?

- If **owned by orchestrator**: `load()` calls `self._structure_engine.check_confirmations(...)` on each candle/update. The events are collected in `self.context.events` and passed to journal.
- If **passed in**: The orchestrator is a pure pipeline that expects `context.events` to be pre-populated. A higher-level loop manages the engine.

The answer affects the orchestrator's constructor, the `load()` implementation, and whether the orchestrator has a heartbeat/candle callback.

### Blocking Question 4: Guard Strategy

The guard concept needs a concrete specification:

**Decision needed:** What states can the guards skip, and what is the criterion?

- Option A: No guards — every cycle runs LOAD → ANALYZE → DECIDE → JOURNAL, unconditionally. Simplest. The decision content ("stand_aside") captures the lack of action.
- Option B: Guard between ANALYZE and DECIDE — skip DECIDE and JOURNAL if confluence is neutral **and** low confidence. This creates gaps in the journal but saves work on non-events.
- Option C: Guard between ANALYZE and DECIDE but always JOURNAL — journal the snapshot+confluence even without a decision. This records "I looked but saw nothing" without needing a decision object.

---

## Summary Table

| Issue | Severity | Effort to Fix | Blocks Planning? |
|-------|----------|---------------|-----------------|
| No live SMC report pipeline | **Blocking** | Medium (refactor snapshot builder or build rolling report) | **Yes (Q1)** |
| `load_ta_latest()` returns insufficient columns | **Blocking** | Quick (<1h): switch to `load_ta_series(tail=1)` | **Yes (Q2)** |
| Events flow undefined | **Blocking** | Quick: add StructureEngine to orchestrator | **Yes (Q3)** |
| Guard strategy undefined | **Blocking** | Quick: decide on option and implement | **Yes (Q4)** |
| `can_decide()` references nonexistent `decision` | Critical | Quick: change to `should_decide()` checking `self.confluence` | No (clear fix) |
| No transition validation | Medium | Short (1-4h): add transition matrix | No (clear fix) |
| Context schema undefined | Medium | Short: define dataclass | No (recommended above) |
| No ERROR recovery | Medium | Short: reset() method + transition from ERROR→IDLE | No (clear fix) |
| ANALYZE bundles 3 sub-steps | Low (ok) | None | No |
