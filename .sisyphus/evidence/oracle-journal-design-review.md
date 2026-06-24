# Oracle Design Review: Journal Module

**Reviewer**: Oracle (Strategic Technical Advisor)
**Date**: 2026-06-24
**Scope**: Proposed Journal implementation design (schema, storage, writer, event linking, sequencing)

---

## Bottom Line

**Verdict: READY FOR PLANNING — with 4 blocking questions that must be resolved before Prometheus writes a plan.**

The design is sound in philosophy (append-only, no interpretation, no scoring) and the two-table schema covers the core decision cycle. Three issues require resolution before coding starts:

1. **Event linking gap** — the decision pipeline (`DecideEngine.decide()`) currently receives no events. `MarketSnapshot` has no `event_ids` field. Prometheus needs to decide where event_ids flow into the journal.
2. **`snapshot_id` and `run_id` undefined** — these are referenced but not specified. Must pick a concrete strategy.
3. **`journal_events` is underspecified** — `event_level` is mentioned but not defined. The table omits `direction` and `status` (provisional/confirmed/cancelled) which are critical for reconstructing what happened.

---

## Schema Review

### `journal_runs` — Column-by-Column Analysis

| Proposed Field | Source | Status | Notes |
|---|---|---|---|
| `run_id` | Generated | ⚠️ Undefined | Need UUID strategy |
| `timestamp` | `MarketSnapshot.timestamp` | ✅ | Good |
| `symbol` | `MarketSnapshot.symbol` | ✅ | Good |
| `timeframe` | `MarketSnapshot.timeframe` | ✅ | Good |
| `snapshot_id` | Generated | ⚠️ Undefined | Hash or a reference? See blocking questions |
| `confluence_score` | `ConfluenceResult.direction_score` | ⚠️ Name mismatch | Codebase calls it `direction_score`. Using `confluence_score` creates a new term. Recommend renaming to `direction_score` for consistency. |
| `bias` | `ConfluenceResult.bias` / `Decision.bias` | ✅ | These should always match (Decision copies from ConfluenceResult) |
| `confidence` | `ConfluenceResult.confidence` / `Decision.confidence` | ✅ | Same value |
| `narrative_summary` | `MarketNarrative.conclusion` | ✅ Per recommendation | Full narrative is 4 sections + conclusion (~300+ chars). Storing only `conclusion` (one-liner like "Bullish continuation favored while EMA21 remains intact.") is the right call. Full data is reconstructable from snapshot + confluence. |
| `decision_action` | `Decision.action` | ✅ | e.g., "look_for_longs" |
| `decision_invalidation` | `Decision.invalidation` | ✅ | Float or None |
| `decision_target` | `Decision.target` | ✅ | Float or None |
| `event_ids` | `list[str]` | ⚠️ No source pipe | See Event Linking section below |

**Missing fields to consider:**

| Field | Reason to Add | Recommendation |
|---|---|---|
| `decision_breakout_pending` | `Decision.breakout_pending` — useful debug signal | Omit for V1. Breakout is a derived state from confidence + swing proximity. Defer. |
| `decision_breakout_level` | Related to above | Omit for V1. |
| `direction_score` | This IS the numeric score (rename from `confluence_score`) | **Add as a rename** — use `direction_score` not `confluence_score` |
| `max_score` | Available in both `ConfluenceResult` and `MarketNarrative` | Omit for V1. Can be computed from raw data. |
| `close` | `MarketSnapshot.close` — key reference price | **Strongly consider adding.** Without it, analyzing entries/exits relative to price requires reconstructing from snapshot JSON. |

### `journal_events` — Column-by-Column Analysis

| Proposed Field | Source | Status | Notes |
|---|---|---|---|
| `run_id` | Foreign key | ✅ | Links back to journal_runs |
| `event_id` | `StructureEvent.event_id` | ✅ | 8-char UUID already exists |
| `event_type` | `StructureEvent.event_type` | ✅ | "BOS" or "CHOCH" |
| `event_level` | ? | ⚠️ Undefined | What is this? Swing level? Timeframe level? Needs definition. |
| `event_timestamp` | `StructureEvent.timestamp` | ✅ | pd.Timestamp from the trigger swing |

**Missing fields in `journal_events`:**

| Field | Reason to Add | Recommendation |
|---|---|---|
| `direction` | `StructureEvent.direction` (1 = bullish, -1 = bearish) | **Add.** Without it, an event_id tells you "BOS" but not which direction — useless for reconstruction. |
| `status` | `StructureEvent.status` ("provisional" / "confirmed" / "cancelled") | **Add.** The whole point of journaling is to record what the engine decided. Without status, you can't tell if a BOS was confirmed or cancelled. |
| `level` | `StructureEvent.level` (the price level) | **Add** (or rename `event_level` to `price_level`). The level is the most important numerical attribute of a structure event. |

**Recommended `journal_events` schema (V1):**

```
run_id: str
event_id: str
event_type: str        # "BOS" or "CHOCH"
direction: int         # 1 = bullish, -1 = bearish
price_level: float     # S1 swing level
status: str            # "provisional" | "confirmed" | "cancelled"
event_timestamp: str   # ISO timestamp of trigger swing
```

---

## Storage Decision

### JSONL for V1 — VERDICT: YES, with naming convention specified.

JSONL is the right call for V1. The reasoning:

- **Append-native**: Each line is a self-contained JSON object. You can append without parsing existing data. Trivial to implement.
- **Schema evolution**: Old rows won't have new fields. Python's `json.loads()` + `.get()` handles this gracefully. Acceptable for V1.
- **Effort**: ~50 lines of writer code vs ~200+ for SQLite schema migrations.
- **Read performance**: Scanning 100k lines of JSONL is ~50ms on modern hardware. Fine for V1 analysis.

**Transition trigger:** When datasets exceed 500k rows OR when point-queries ("give me the entry for run_id X") become the primary access pattern, switch to SQLite/DuckDB.

### File Naming Convention

Use one file per (symbol, timeframe, date) for partitioning:

```
journal/{symbol}/{timeframe}/journal_{symbol}_{timeframe}_{date}.jsonl
```

Example: `journal/BTC-USDT/1h/journal_BTC-USDT_1h_2026-06-24.jsonl`

**Why:** Partitions by symbol → timeframe → date. A single file per run creates thousands of tiny files. A single global file becomes unwieldy for multi-symbol systems. Daily partitions are the sweet spot.

### Append Semantics

- `flush()` always appends to the file. Never overwrites existing data.
- If the file doesn't exist, create it.
- Each line is a JSON representation of `JournalEntry` — must include all nested serialization.

---

## Writer Design

### Core Pattern

The user's `append()` → buffer, `flush()` → disk design is correct. Recommended refinement:

```python
@dataclass
class JournalWriter:
    path: Path
    _buffer: list[dict] = field(default_factory=list)
    _file_handle: IO | None = None
    _lock: Lock = field(default_factory=Lock)  # threading

    def append(self, entry: JournalEntry) -> None:
        """Serialize entry and add to buffer."""
        with self._lock:
            self._buffer.append(self._serialize(entry))

    def flush(self) -> None:
        """Write buffer to disk, then clear buffer."""
        with self._lock:
            if not self._buffer:
                return
            lines = "\n".join(json.dumps(e) for e in self._buffer) + "\n"
            self._ensure_open()
            self._file_handle.write(lines)
            self._file_handle.flush()
            os.fsync(self._file_handle.fileno())
            self._buffer.clear()

    def close(self) -> None:
        """Flush and close file handle."""
        self.flush()
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
```

### Thread Safety

**Required.** Live trading may run multiple symbols concurrently (each with its own timeframe chain). Without a lock, concurrent `append()` calls can cause interleaved writes.

Use `threading.Lock` for the Python-level guard. JSONL's per-line independence means the lock only needs to protect `_buffer.append()` and `_file_handle.write()` — it doesn't need to be a mutex around the entire serialization.

### File Handle Management

- **Lazy open**: Open file on first `flush()` (not on `append()` or `__init__`). This avoids leaving file handles open when the writer is configured but not yet used.
- **Context manager**: Support `with JournalWriter(path) as jw:` — flush on exit.
- **Auto-flush threshold**: Optional `buffer_size` parameter — flush when buffer exceeds N entries (e.g., 100). This prevents data loss on crash without sacrificing batching.

### Error Handling

The user said "boring" — so keep it simple:

- On write error during `flush()`: Log the error, skip the batch, clear the buffer, continue. Do NOT crash the trading system for a journal write failure.
- On partial file write: Accept it. JSONL is line-oriented — each line is independently parseable. A partial last line will fail `json.loads()` on read, which is detectable (`.get()` won't crash).
- No temp-file/rename atomicity for V1. That optimization belongs at "too many partial writes" → then add it.

---

## Event Linking

### The Problem

The current pipeline flow is:

```
StructureEngine → StructureEvent (event_id, event_type, direction, status, level, ...)
     ↓
SnapshotBuilder → MarketSnapshot (NO event_ids field)
     ↓
ConfluenceScorer → ConfluenceResult (pure scoring, no events)
     ↓
MarketNarrativeBuilder → MarketNarrative (text generation, no events)
     ↓
DecisionEngine.decide(snapshot, result) → Decision (NO events parameter)
```

`DecisionEngine.decide()` receives `MarketSnapshot` + `ConfluenceResult` — neither carries event_ids. The events exist at the orchestration level (in `BacktestHarness.run()` they're grouped by bar), but they never reach the decision cycle.

**The core question:** How do event_ids get into the journal?

### Recommended Solution: Journal at the Orchestration Level

The cleanest fix is to **not push events deeper into the pipeline**. Instead, build the journal entry at the orchestration level where all data is already available:

```
Phase 3 (strategy loop): For each bar:
  1. Build MarketSnapshot (via SnapshotBuilder)
  2. Score with ConfluenceScorer → ConfluenceResult
  3. Build MarketNarrative → MarketNarrative
  4. Decide with DecisionEngine → Decision
  5. Collect event_ids from bar_events (already available here)
  6. Build JournalEntry → append to JournalWriter
```

In `BacktestHarness.run()`, the orchestration loop at lines 1092-1101 already has access to `bar_events` (line 1098). Step 6 would be added after the strategy callback.

This requires **zero changes to existing pipeline classes** — no new parameters, no new fields. The journal is purely an orchestration-level concern.

### For Live Trading

A future `LiveOrchestrator` (step 4 in the user's sequence) would similarly construct the journal entry after each decision cycle, collecting event_ids from whatever event accumulator is active during that candle.

### What This Means for the Design

- **No need to add `event_ids` to `MarketSnapshot`** — the orchestration layer has them.
- **No need to modify `DecisionEngine.decide()` signature** — events don't flow through it.
- **The journal is a pure observer** at the orchestration level — exactly as the user intended.

---

## Sequencing

The user's proposed sequence is sound. One adjustment:

### Proposed Build Order

1. **Journal module (`journal.py`)** — JournalEntry dataclass, JournalWriter, JSONL serialization. Test with synthetic data. *Effort: Short (1-4h)*
2. **Wire into `BacktestHarness.run()`** — Add journal creation in Phase 3 loop. One new line after the strategy callback. *Effort: Quick (<1h)*
3. **Wire into live trading path** — Add journal creation to the live orchestrator (when it exists). *Effort: Quick (<1h)*
4. **Optional outcome fields** — Forward return, trade result columns. Only after trade tracking is stable. *Effort: Quick (<1h)*
5. **Review orchestration layer with state machine** — Live orchestrator design covering multi-symbol, multi-timeframe. *Effort: Medium (1-2d)*
6. **Tailor `analyze_ta.py`** — After comparing pre-merge vs current version. *Effort: Short (1-4h)*

### Key Decision: Wire into Harness vs Standalone

**Recommendation: Wire into `BacktestHarness.run()` for V1.**

The harness is the one place where all data converges per bar. A standalone wrapper adds a class with one method that calls the harness — not worth the abstraction at this stage. The harness can accept an optional `JournalWriter` parameter:

```python
class BacktestHarness:
    def run(self, data_path: str, journal: JournalWriter | None = None) -> BacktestResult:
        ...
        for i in range(n):
            # existing logic
            self._strategy_callback.update(...)
            # NEW: journal entry
            if journal is not None:
                entry = JournalEntry(...)
                journal.append(entry)
```

When `journal` is `None`, no journaling overhead. When provided, every bar is recorded.

---

## Blocking Questions for the User

Before Prometheus writes a plan, resolve these:

### Q1: `run_id` — What is the format?

**Options:**
- **A**: Pure UUID — `"a1b2c3d4-e5f6-..."` (no human-readable info, but globally unique)
- **B**: Compound key — `"{symbol}_{timeframe}_{timestamp}_{uuid-short}"` — e.g., `"BTCUSDT_1h_20260624T120000_a1b2c3"` (human-readable, self-describing, partitionable)
- **C**: Sequential integer + date prefix — `"20260624-0001"` (needs a counter, fragile across restarts)

**Recommendation:** B — compound key. Gives immediate readability and enables file-level partitioning. The short UUID suffix prevents collisions within the same millisecond.

### Q2: `snapshot_id` — What is it?

**Options:**
- **A**: Same as `run_id` (one-to-one mapping between snapshot and decision cycle). Simplest.
- **B**: Deterministic hash of `(symbol, timeframe, timestamp)` — e.g., `hashlib.md5(f"{symbol}:{timeframe}:{timestamp}".encode()).hexdigest()[:12]`. Enables deduplication of identical snapshots.
- **C**: Sequential auto-increment. Simple but meaningless across restarts.

**Recommendation:** A — use the same as `run_id`. A decision cycle always corresponds to exactly one snapshot. There is no world where you need to join a snapshot to multiple decision cycles. B adds complexity without benefit.

### Q3: What is `event_level` in `journal_events`?

This term appears in the proposed schema but is not defined anywhere in the codebase. The `StructureEvent` has no `level` field other than the price `level`. If `event_level` means the price level, rename to `price_level`. If it means something else (timeframe level? severity level?), define it.

**Recommendation:** Remove `event_level` from the schema unless a clear definition exists. Replace with fields that exist: `direction`, `status`, `price_level`.

### Q4: Where in the pipeline should the journal entry be constructed — inside the pipeline classes or at the orchestration level?

This determines the entire JournalWriter integration strategy.

**Recommendation:** At the orchestration level (see Event Linking section above). This keeps the journal a pure observer with zero changes to existing pipeline classes. Confirm this approach before Prometheus plans.

---

## Summary of Action Items for Prometheus

| # | Item | Status |
|---|---|---|
| 1 | Create `journal.py` with `JournalEntry`, `JournalWriter`, JSONL serialization | Ready |
| 2 | Define `journal_events` schema with direction, status, price_level (not event_level) | Needs Q3 resolution |
| 3 | Implement thread-safe buffered writer with lazy file open + context manager | Ready |
| 4 | Create `tests/test_journal.py` | Ready |
| 5 | Wire JournalWriter into BacktestHarness.run() as optional parameter | Ready |
| 6 | Add `direction_score` column to `journal_runs` (or rename from `confluence_score`) | Needs confirmation |
| 7 | Add `close` column to `journal_runs` | Consider |
| 8 | Define `run_id` format | Needs Q1 resolution |
| 9 | Define `snapshot_id` = `run_id` | Needs Q2 resolution |
