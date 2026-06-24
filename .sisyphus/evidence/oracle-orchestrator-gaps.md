# Oracle — Gap Solutions for Orchestrator Plan

**Bottom line**: Three targeted changes to the plan before implementation. Each solution is minimal, preserves the existing design, and fits within the existing state machine architecture.

---

## Gap 1: Replay Mode — `step()` always calls `load()`, can't use pre-populated context

### The Problem

`LiveOrchestrator.step()` always calls `self.load()` which unconditionally fetches TA data via `load_ta_series()` and updates the SMC buffer. In replay mode, the caller wants to pre-populate `context.ta_row` and `context.smc_report` with data from a historical batch run, then step through candle-by-candle. But `load()` would overwrite those values.

**Constraint**: Same `step()` method for both modes. No `replay_step()`.

### The Solution

**Add a `mode` field to `OrchestratorContext`**. `load()` checks this field — in replay mode, it's a no-op. The caller pre-populates context before calling `step()`.

### Specific Changes to the Plan

#### 1. `OrchestratorContext` gains a `mode` field

```python
@dataclass
class OrchestratorContext:
    symbol: str
    timeframe: str
    data_dir: str = "data"
    db_path: str = "journal.db"
    mode: str = "live"                    # NEW — "live" or "replay"

    # Runtime state (set during pipeline execution)
    ta_row: pd.Series | None = None
    smc_report: pd.DataFrame | None = None
    snapshot: MarketSnapshot | None = None
    confluence: ConfluenceResult | None = None
    narrative: MarketNarrative | None = None
    decision: Decision | None = None
    entry: JournalEntry | None = None
```

#### 2. `load()` uses `mode` to decide whether to fetch data

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

No other method changes. `analyze()`, `decide()`, `journal()` are mode-agnostic — they work identically because `ta_row` and `smc_report` are set either way.

#### 3. Caller pattern for replay mode

```python
# Replay: pre-computed SMC report + TA rows from backtest
context = OrchestratorContext(symbol="BTC/USDT", timeframe="1h", mode="replay")
orchestrator = LiveOrchestrator(context)

for i in range(len(candles)):
    context.ta_row = ta_rows[i]
    context.smc_report = smc_reports[i]   # pre-built 26-col report slice

    orchestrator.step()

    # Read decision for this candle
    decisions.append(copy(context.decision))
```

#### 4. `step()` stays identical

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

### Why This Approach

- **One byte of state** (`mode: str`) — the smallest possible touch to the context.
- **No new abstraction** (no `DataProvider` protocol, no strategy pattern). The conditional is isolated to one method.
- **Same `step()` method** — satisfies the constraint literally.
- **Buffer is irrelevant in replay** — it's constructed but never updated. Harmless. Tear-down cost is zero.
- **Live mode unchanged** — zero regression risk.

### Watch Out For

- **Caller must reset context fields between replay iterations** if they reuse the same context object. `analyze()`/`decide()`/`journal()` always overwrite their outputs, so this is only a concern if the caller inspects fields mid-cycle. Document in the plan: *"In replay mode, `step()` overwrites snapshot, confluence, narrative, decision, and entry each call. Read `context.decision` after `step()` returns, before the next call."*
- **Replay mode with partial pre-population** (setting `ta_row` but not `smc_report`) will fail in `analyze()` with a clear guard error. This is correct behavior — no silent fallback.

### Effort Estimate

**Quick** (<1h). One field in dataclass, one `if` guard in `load()`, updated docstring.

---

## Gap 2: Error Recovery — no retry path from ERROR state

### The Problem

When `step()` catches an exception, it sets `state = ERROR` and re-raises. If the caller retries by calling `step()` again, `_transition(LOAD)` is called from ERROR state. Currently `_transition()` has no validation — it silently accepts any transition, meaning retry from ERROR would proceed with stale or partial context data.

Additionally, there's no way to clear runtime context between cycles on retry.

**Constraint**: No complex retry logic. Caller decides retry policy.

### The Solution

**Three changes**: (1) add a validated transition matrix, (2) add a `reset()` method, (3) allow ERROR → IDLE as the only exit from ERROR.

### Specific Changes to the Plan

#### 1. Add transition matrix and validate in `_transition()`

```python
_TRANSITIONS: dict[OrchestrationState, set[OrchestrationState]] = {
    OrchestrationState.IDLE:    {OrchestrationState.LOAD},
    OrchestrationState.LOAD:    {OrchestrationState.ANALYZE, OrchestrationState.ERROR},
    OrchestrationState.ANALYZE: {OrchestrationState.DECIDE, OrchestrationState.ERROR},
    OrchestrationState.DECIDE:  {OrchestrationState.JOURNAL, OrchestrationState.ERROR},
    OrchestrationState.JOURNAL: {OrchestrationState.IDLE, OrchestrationState.ERROR},
    OrchestrationState.ERROR:   {OrchestrationState.IDLE},   # <-- reset only
}

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
- Caller retrying `step()` from ERROR without reset (ERROR → LOAD is invalid → raises immediately, telling the caller to `reset()`)
- Programming errors like calling `analyze()` before `load()`
- Accidental double-transitions

#### 2. Add `reset()` method

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

Key design choices:
- **Not a transition** — `reset()` sets `self.state` directly, bypassing `_transition()`. It's a reinitialization, not a state machine step. This is intentional: reset should work from ANY state (including IDLE), not just ERROR.
- **Clears runtime fields** — avoids stale-data contamination on retry. Configuration fields (symbol, timeframe, data_dir, mode) are preserved.
- **No parameters** — one thing, one way.

#### 3. `step()` stays unchanged (no auto-recovery)

```python
def step(self) -> None:
    try:
        self.load()
        self.analyze()
        self.decide()
        self.journal()
        self._transition(OrchestrationState.IDLE)
    except Exception:
        self._last_error = sys.exc_info()[1]
        self._transition(OrchestrationState.ERROR)
        raise
```

No auto-recovery. `step()` always starts from whatever the current state is. If state is ERROR, the first `_transition(LOAD)` will fail with "Invalid transition: ERROR → LOAD". This forces the caller to explicitly reset.

#### 4. Caller retry pattern (for documentation)

```python
# Caller-managed retry — orchestrator has zero retry logic
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

### Why This Approach

- **Transition matrix is 6 lines of data + 3 lines of logic** — minimal, self-documenting.
- **`reset()` is the only way out of ERROR** — no hidden auto-recovery, no silent retries.
- **Catches programming errors early** — invalid transition raises immediately with a clear message.
- **No retry logic in the orchestrator** — satisfies the constraint. The orchestrator is a "retry-safe" component, not a "retrying" component.
- **`reset()` as blunt reinit vs `_transition()` as state machine** — the distinction is important. `reset()` is a full reinitialization, not a state transition. It's intentionally outside the matrix.

### Watch Out For

- **`reset()` from IDLE is a no-op conceptually** but does clear context fields. In live mode, the next `step()` → `load()` will refetch everything. In replay mode, the caller must re-populate. This is correct but worth documenting.
- **Multiple `reset()` calls are safe** — idempotent. State stays IDLE, context stays cleared.
- **`_last_error` is cleared** — the caller should read it before resetting if they need it for logging. Document: *"Read `_last_error` before calling `reset()`."*

### Effort Estimate

**Short** (1-4h). Matrix dict, `_transition()` validation, `reset()` method, updated tests.

---

## Gap 3: Async Bridge — JournalWriter is async, orchestrator is sync

### The Problem

`JournalWriter.append()` and `flush()` are `async` (they use `aiosqlite`). The orchestrator is synchronous by design. The plan says "orchestrator's `journal()` builds the entry only; the caller manages JournalWriter lifecycle." But there's no documented pattern for how the caller bridges the sync/async gap — they'd have to write `asyncio.run(writer.append(entry))` manually, which is repetitive and error-prone.

**Constraint**: Keep the orchestrator synchronous. JournalWriter lifecycle is the caller's responsibility. But make the integration seamless.

### The Solution

**Provide a standalone `sync_write_entry()` function** in `orchestrator.py`. This is a one-liner convenience wrapper that the caller uses after every `step()`. The orchestrator itself stays pure sync — no async, no JournalWriter import beyond the dataclass.

### Specific Changes to the Plan

#### 1. Add `sync_write_entry()` to `orchestrator.py`

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

#### 2. Caller pattern (both modes)

```python
# ── Construction ──
context = OrchestratorContext(symbol="BTC/USDT", timeframe="1h")
orchestrator = LiveOrchestrator(context)

# Caller manages JournalWriter lifecycle (async context manager)
async def main():
    async with JournalWriter("journal.db", buffer_size=0) as writer:
        while True:
            orchestrator.step()
            sync_write_entry(writer, orchestrator.context.entry)
            time.sleep(60)   # or whatever the candle interval is

# ── Or, if the caller has a sync main loop ──
writer = JournalWriter("journal.db", buffer_size=0)
asyncio.run(writer.__aenter__())           # open connection
try:
    while True:
        orchestrator.step()
        sync_write_entry(writer, orchestrator.context.entry)
        time.sleep(60)
finally:
    asyncio.run(writer.flush())
    asyncio.run(writer.__aexit__(None, None, None))
```

#### 3. Update the `journal()` docstring to point to `sync_write_entry()`

```python
def journal(self) -> None:
    """Build JournalEntry from current context.

    Sets ``self.context.entry``. Does NOT write to the database —
    the caller is responsible for persisting the entry via
    ``sync_write_entry()`` or by calling ``JournalWriter.append()``
    directly in an async context.
    """
```

#### 4. Alternative: SyncJournalWriter class (if caller prefers an object)

If the function pattern feels too loose, a thin wrapper class achieves the same goal:

```python
class SyncJournalWriter:
    """Synchronous wrapper around async JournalWriter.

    Wraps ``append()`` and ``flush()`` with ``asyncio.run()``.
    Create once, call ``append()`` / ``flush()`` from sync code.

    Usage::

        writer = SyncJournalWriter("journal.db")
        writer.append(entry)
        writer.flush()
        writer.close()
    """

    def __init__(self, db_path: str, buffer_size: int = 0):
        self._async = JournalWriter(db_path, buffer_size)

    def open(self) -> None:
        asyncio.run(self._async.__aenter__())

    def append(self, entry: JournalEntry) -> None:
        asyncio.run(self._async.append(entry))

    def flush(self) -> None:
        asyncio.run(self._async.flush())

    def close(self) -> None:
        asyncio.run(self._async.__aexit__(None, None, None))
```

**Recommendation**: Start with the **function** (`sync_write_entry()`). It's simpler and the caller already constructs/manages the `JournalWriter` — they just need a bridge. The class adds lifecycle management that duplicates what `JournalWriter.__aexit__` already does. Add the class only if callers consistently struggle with the async context manager.

### Why This Approach

- **Zero changes to `LiveOrchestrator`** — the orchestrator remains pure sync, pure stateless orchestration.
- **Zero changes to `JournalWriter`** — it stays async, no sync wrapper leak into the library.
- **One extra function import** — `sync_write_entry` is findable, testable, and obvious.
- **No event loop lifecycle management** — `asyncio.run()` creates and tears down a loop per call. For per-candle latency (60s+ intervals), this is negligible.
- **Works for both sync and async callers** — async callers just use `await writer.append()` directly.

### Watch Out For

- **`asyncio.run()` inside an async context** fails with "asyncio.run() cannot be called from a running event loop". Document this: *"If your caller is already async, use `await writer.append(entry)` directly instead of `sync_write_entry()`."*
- **`buffer_size=0`** is recommended for synchronous callers. This causes `append()` to flush immediately, avoiding data loss if the process crashes between `append()` and a manual `flush()` call.
- **T5 integration test** should use `sync_write_entry()` or `asyncio.run()` — both are valid. The test verifies the full async path anyway (because `JournalWriter` is always async underneath).

### Effort Estimate

**Quick** (<1h). One function in `orchestrator.py`, one import, updated docstring.

---

## Summary of Plan Changes

| What | Where in plan | Change |
|------|--------------|--------|
| `mode` field | T2 — `OrchestratorContext` | Add `mode: str = "live"` |
| `load()` replay guard | T2 — `load()` | Add `if mode == "replay": return` at top |
| Replay caller pattern | T2 — `step()` docstring or new section | Document pre-populate + call pattern |
| Transition matrix | T2 — `_transition()` | Add `_TRANSITIONS` dict + validation |
| `reset()` method | T2 — `LiveOrchestrator` | Add method, clear runtime fields |
| Caller retry pattern | T2 — `step()` docstring or new section | Document `reset()` before retry |
| `sync_write_entry()` | T2 — `orchestrator.py` (top-level) | Add function |
| Updated journal docstring | T2 — `journal()` | Point to `sync_write_entry()` |
| SyncJournalWriter (optional) | T2 — `orchestrator.py` | Class wrapper, only if needed |

**No changes to T1 (LiveSmcBuffer), T3, T4, or T5** except that T5's integration test should demonstrate `sync_write_entry()`.

**No changes to existing project files** (`journal.py`, `market_snapshot.py`, etc.).
