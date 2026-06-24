# Journal Implementation — SQLite-based Decision Journal

## TL;DR
> **Quick Summary**: Build a Journal module that records every decision cycle (snapshot → confluence → narrative → decision) to a SQLite database via `aiosqlite`. The Journal is a pure observer at the orchestration layer — zero changes to existing pipeline classes.
> **Deliverables**: `journal.py` (JournalEntry + JournalWriter + schema SQL), `tests/test_journal.py` (unit + integration tests), `pyproject.toml` dependency verification
> **Estimated Effort**: Short
> **Parallel Execution**: YES — 3 waves (sequential within each wave)
> **Critical Path**: T1 (Journal module) → T2 (Unit tests) → T3 (Integration test)

---

## Context

### Original Request
Build a journal module that records every decision cycle to persistent storage. The journal must capture: the snapshot state at decision time, the confluence scoring result, the narrative conclusion, the decision action/levels, and the structure events active at that time. All data flows from existing pipeline classes — the journal is a pure consumer, not a modifier.

### Interview Summary
All design decisions resolved via Oracle review + user confirmation:

| Question | Resolution |
|----------|-----------|
| **Q1 — run_id format** | Compound key: `{symbol}_{timeframe}_{timestamp}_{short-uuid}` (e.g. `BTCUSDT_1h_20260624T120000_a1b2c3`) |
| **Q2 — snapshot_id** | Same as `run_id` (1:1 mapping) |
| **Q3 — event_level** | Removed from `journal_events` schema |
| **Q4 — Journal construction** | At orchestration layer — no pipeline changes, journal is a pure observer |
| **Storage format** | SQLite via `aiosqlite` (already in `pyproject.toml`) |

### Metis Review (Gap Analysis)

| Gap | Resolution |
|-----|-----------|
| **Symbol name sanitization in run_id** | `BTC/USDT` contains `/` — sanitize to `BTCUSDT` (strip non-alphanumeric except `-` and `_`). Use a `_sanitize_symbol()` helper. |
| **Timestamp format in run_id** | ISO format without separators: `YYYYMMDDTHHmmss`. Use `pd.Timestamp.strftime("%Y%m%dT%H%M%S")`. |
| **bool ↔ int for breakout_pending** | `JournalEntry` stores `bool`, SQLite stores as `INTEGER` (0/1). `JournalWriter.append()` handles conversion. |
| **pd.Timestamp serialization** | `JournalEntry.timestamp` is `pd.Timestamp` → convert to ISO string `YYYY-MM-DD HH:mm:ss` for SQLite TEXT column. |
| **Event data completeness** | `JournalEntry.event_ids` is a list of strings. For each event_id, the writer looks up the corresponding `StructureEvent` to get `direction`, `status`, `level`. The orchestration code must provide the full `StructureEvent` objects. |
| **SQLite path** | Default to `journal.db` in project root. Configurable via `JournalWriter(db_path)`. For backtesting, an explicit path should be passed per run. |
| **flush() frequency** | Backtest: flush every 100 entries OR at end of run (whichever comes first). Live: flush every bar (after each `append()` to minimize data loss). Configurable via `buffer_size` parameter. |
| **Thread safety** | Backtest harness is single-threaded per run — no concurrency concern for V1. Lock added as safety measure for future live use. |
| **TEXT vs REAL for `close`** | `close` is stored as REAL in SQLite. `MarketSnapshot.close` is `float` — direct cast. |
| **narrative_summary sources** | Maps to `MarketNarrative.conclusion` (not full sections). This matches Oracle's recommendation and keeps the column size reasonable. |

---

## Work Objectives

### Core Objective
Create a reusable Journal module that records every complete decision cycle to a SQLite database. The module must be self-contained, async-first, and require zero modifications to existing pipeline classes (`MarketSnapshot`, `ConfluenceResult`, `MarketNarrative`, `Decision`, `StructureEvent`).

### Concrete Deliverables
1. `journal.py` — `JournalEntry` dataclass, `JournalWriter` async context manager, SQL schema constants
2. `tests/test_journal.py` — Unit tests for append/flush/query/round-trip + integration test
3. Verified `aiosqlite` dependency (already in `pyproject.toml`, no changes needed)

### Definition of Done
- `python -c "from journal import JournalEntry, JournalWriter"` succeeds with zero errors
- `JournalWriter` opens as async context manager (`async with JournalWriter(path) as w:`)
- Schema tables (`journal_runs`, `journal_events`) are auto-created on first open
- `append()` accepts a `JournalEntry` and buffers it in-memory
- `flush()` writes buffered entries to SQLite in a single transaction
- Written entries are queryable — reading back the DB returns the same data
- `JournalEntry` correctly maps all fields from pipeline classes (snapshot, confluence, narrative, decision, events)
- `run_id` is generated as `{symbol}_{timeframe}_{timestamp}_{short-uuid}` with sanitized symbol
- `bool` fields convert correctly to INTEGER and back
- No changes to any existing `.py` file outside `journal.py` and `test_journal.py`
- All tests pass: `python -m pytest tests/test_journal.py -v --tb=short`

### Must Have
- `JournalEntry` dataclass with all specified fields
- `JournalWriter` with `__aenter__` (open DB + create schema + return self), `__aexit__` (flush + close), `append(entry)`, `flush()`
- `journal_runs` table with all columns from schema
- `journal_events` table with FK to `journal_runs` and columns: run_id, event_id, event_type, direction, status, level
- Compound `run_id` generation — `{symbol}_{timeframe}_{timestamp}_{short-uuid}`
- Schema creation idempotent (`CREATE TABLE IF NOT EXISTS`)
- Transactional flush — all buffered entries written in one commit

### Must NOT Have (Guardrails)
- ❌ No scoring logic — journal does not compute direction_score, confidence, or any derived values
- ❌ No strategy logic — journal does not analyze, filter, or modify decisions
- ❌ No backtest internals — journal knows nothing about bars, equity curves, or trade simulation
- ❌ No modifications to existing pipeline classes — `market_snapshot.py`, `confluence.py`, `narrative.py`, `decision_engine.py`, `smartmoneyconcepts/structures.py` remain unchanged
- ❌ No live trading wiring — JournalWriter is a standalone module; wiring into live orchestrator is a future task
- ❌ Not a JSONL writer — the user explicitly chose SQLite over JSONL
- ❌ No ORM — raw SQL via aiosqlite, no SQLAlchemy or similar

---

## Verification Strategy

**Test approach:** Unit tests for core functionality (append/flush/query/round-trip) + one integration test simulating the full pipeline → journal write → verify DB.

**QA is agent-executed** — all scenarios below use `interactive_bash` (via aiosqlite + pytest) with explicit assertions and evidence files.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 [T1]        journal.py — JournalEntry, JournalWriter, schema SQL
Wave 2 [T2–T3]     tests/test_journal.py — unit tests
Wave 3 [T4]         tests/test_journal.py — integration test
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| T1 — Journal module | None | T2, T3, T4 |
| T2 — JournalEntry tests | T1 | None |
| T3 — JournalWriter unit tests | T1 | None |
| T4 — Integration test | T1, T2, T3 | Final verification |

### Agent Dispatch Summary

| Task | Agent Profile | Skills Required |
|------|--------------|----------------|
| T1 | Generalist (python) | async-python-patterns, python-design-patterns, python-type-safety |
| T2–T3 | Generalist (python + testing) | test-driven-development |
| T4 | Generalist (python + integration) | async-python-patterns |

---

## TODOs

- [ ] T1. Create `journal.py` — JournalEntry + JournalWriter + Schema SQL
  **What to do**: Create `/home/parzivalxiii/Projects/smc-live-trading/journal.py` containing:
  1. `SCHEMA_SQL` constant with `CREATE TABLE IF NOT EXISTS` statements for `journal_runs` and `journal_events` (matching the Oracle-approved schema).
  2. `JournalEntry` dataclass with fields:
     - `run_id: str`
     - `timestamp: pd.Timestamp`
     - `symbol: str`
     - `timeframe: str`
     - `close: float`
     - `direction_score: float`
     - `bias: str`
     - `confidence: float`
     - `narrative_summary: str`
     - `decision_action: str`
     - `decision_invalidation: float | None`
     - `decision_target: float | None`
     - `breakout_pending: bool`
     - `event_ids: list[str]`
  3. Helper function `make_run_id(symbol: str, timeframe: str, timestamp: pd.Timestamp) -> str` that:
     - Sanitizes symbol (strip non-alphanumeric except `-` and `_`, e.g. `BTC/USDT` → `BTCUSDT`)
     - Formats timestamp as `YYYYMMDDTHHmmss`
     - Appends 8-char UUID suffix via `uuid.uuid4().hex[:8]`
     - Returns `{sanitized_symbol}_{timeframe}_{timestamp}_{uuid}`
  4. `JournalWriter` class:
     ```python
     class JournalWriter:
         def __init__(self, db_path: str, buffer_size: int = 100): ...
         async def __aenter__(self) -> JournalWriter: ...
         async def __aexit__(self, *args): ...
         async def append(self, entry: JournalEntry, events: list[StructureEvent] | None = None) -> None: ...
         async def flush(self) -> None: ...
     ```
     - `append()` accepts both the `JournalEntry` AND an optional list of `StructureEvent` objects. If `events` is provided, it inserts corresponding `journal_events` rows by matching `entry.event_ids` to `events`.
     - `append()` does NOT commit — entries are buffered in a list.
     - `flush()` writes all buffered entries in a single transaction (one `INSERT INTO journal_runs` per entry + batch `INSERT INTO journal_events`).
     - `__aenter__` opens the connection and creates schema.
     - `__aexit__` calls `flush()` then closes.
     - `buffer_size` auto-flushes when buffer exceeds threshold. `buffer_size=0` means flush every append.
  5. Helper `_entry_to_row(entry: JournalEntry) -> tuple` for serialization (converts pd.Timestamp to str, bool to 0/1).
  6. Helper `_now_iso() -> str` for `created_at` timestamp.

  **Must NOT do**:
  - ❌ Do NOT import or reference `MarketSnapshot`, `ConfluenceResult`, `MarketNarrative`, or `Decision` — journal only uses types from `structres.py` (StructureEvent)
  - ❌ Do NOT add any scoring, filtering, or analysis logic
  - ❌ Do NOT create any files outside `journal.py` and test files
  - ❌ Do NOT modify any existing source files

  **Recommended Agent Profile**: Generalist (python) — async-python-patterns, python-design-patterns, python-type-safety

  **Parallelization**: Wave 1, blocks nothing

  **References**:
  - Oracle review: `.sisyphus/evidence/oracle-journal-design-review.md`
  - `smartmoneyconcepts/structures.py` — `StructureEvent` (event_id, event_type, direction, status, level)
  - `market_snapshot.py` — `MarketSnapshot` (close, timestamp, symbol, timeframe)
  - `confluence.py` — `ConfluenceResult` (direction_score, bias, confidence)
  - `narrative.py` — `MarketNarrative` (conclusion)
  - `decision_engine.py` — `Decision` (action, invalidation, target, breakout_pending)
  - `pyproject.toml` — `aiosqlite>=0.22.1` already listed

  **Acceptance Criteria**:
  ```bash
  python -c "from journal import JournalEntry, JournalWriter, make_run_id, SCHEMA_SQL; print('OK')"
  python -c "from journal import make_run_id; rid = make_run_id('BTC/USDT', '1h', pd.Timestamp('2026-06-24 12:00:00')); print(rid); assert rid.startswith('BTCUSDT_1h_20260624T120000_'); assert len(rid.split('_')[-1]) == 8"
  ```

  **QA Scenarios**:

  1. **Import and basic construction** (interactive_bash):
     - **Preconditions**: `journal.py` exists
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -c "from journal import JournalEntry, JournalWriter; entry = JournalEntry(run_id='t1', timestamp=pd.Timestamp.now(), symbol='TEST', timeframe='1h', close=100.0, direction_score=5.0, bias='bullish', confidence=1.0, narrative_summary='test', decision_action='stand_aside', decision_invalidation=None, decision_target=None, breakout_pending=False, event_ids=['abc123']); print(f'OK run_id={entry.run_id}')"`
     - **Expected Result**: Prints `OK run_id=t1`
     - **Evidence**: `.sisyphus/evidence/journal-T1-import-ok.txt`

  2. **run_id format** (interactive_bash):
     - **Preconditions**: `journal.py` exists
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -c "import pandas as pd; from journal import make_run_id; rid = make_run_id('BTC/USDT', '1h', pd.Timestamp('2026-06-24 12:00:00')); print(f'RUN_ID={rid}'); assert rid.startswith('BTCUSDT_1h_20260624T120000_'), f'Bad prefix: {rid}'; assert len(rid.split('_')[-1]) == 8, f'Bad uuid: {rid}'; print('OK')"`
     - **Expected Result**: Prints `RUN_ID=BTCUSDT_1h_20260624T120000_XXXX` (8-char suffix) and `OK`
     - **Evidence**: `.sisyphus/evidence/journal-T1-runid.txt`

  3. **Schema SQL validity** (interactive_bash):
     - **Preconditions**: `journal.py` exists
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -c "from journal import SCHEMA_SQL; assert 'CREATE TABLE IF NOT EXISTS journal_runs' in SCHEMA_SQL; assert 'CREATE TABLE IF NOT EXISTS journal_events' in SCHEMA_SQL; assert 'run_id' in SCHEMA_SQL; assert 'event_id' in SCHEMA_SQL; print(f'Schema OK ({len(SCHEMA_SQL)} chars)')"`
     - **Expected Result**: Schema SQL contains both tables and key columns
     - **Evidence**: `.sisyphus/evidence/journal-T1-schema.txt`


- [ ] T2. Write `tests/test_journal.py` — JournalEntry unit tests
  **What to do**: Create `/home/parzivalxiii/Projects/smc-live-trading/tests/test_journal.py` with tests for:
  1. `JournalEntry` construction with all fields
  2. `make_run_id()` format correctness (symbol sanitization, timestamp format, UUID suffix)
  3. Symbol sanitization — `/` stripped, dots kept, hyphens kept
  4. `make_run_id()` uniqueness — two calls with same inputs produce different UUID suffixes
  5. `_entry_to_row()` serialization correctness (bool→int, pd.Timestamp→str, None preservation)
  6. `JournalEntry` with minimal fields (all optional/None defaults)
  7. `JournalEntry` with extreme values (very long strings, NaN close — though NaN should be handled at pipeline level, journal should not crash on float('nan'))
  8. Edge case: empty `event_ids` list

  **Must NOT do**:
  - ❌ Do NOT create tests that require a database connection (those go in T3)
  - ❌ Do NOT modify any existing test files
  - ❌ Do NOT add tests that depend on pipeline classes

  **Recommended Agent Profile**: Generalist (python + testing) — test-driven-development

  **Parallelization**: Wave 2 (parallel with T3), blocked-by T1

  **References**:
  - `journal.py` (just created by T1)
  - Existing test pattern: `tests/test_decision_engine.py`, `tests/conftest.py` (for `sys.path.insert(0, ...)` pattern)
  - `smartmoneyconcepts/structures.py` — `StructureEvent` dataclass

  **Acceptance Criteria**:
  ```bash
  python -m pytest tests/test_journal.py -v --tb=short -k "not journalwriter and not integration" 2>&1 | tail -5
  ```
  All journal-entry-level tests pass.

  **QA Scenarios**:

  1. **Run all journal entry tests** (interactive_bash):
     - **Preconditions**: `journal.py` and `tests/test_journal.py` exist
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -m pytest tests/test_journal.py -v --tb=short -k "not journalwriter and not integration" 2>&1 | tee /tmp/journal-entry-tests.txt`
     - **Expected Result**: All entry-level tests PASS
     - **Evidence**: `.sisyphus/evidence/journal-T2-entry-tests.txt`


- [ ] T3. Write `tests/test_journal.py` — JournalWriter unit tests
  **What to do**: Append to `tests/test_journal.py` (or create if T2 hasn't yet) with tests for `JournalWriter`:
  1. **Context manager lifecycle**: `async with JournalWriter(tmp_path / "test.db") as w:` — opens, creates schema, returns `JournalWriter`
  2. **Append + flush**: Write 3 entries, flush, verify DB has 3 rows in `journal_runs`
  3. **Append + flush with events**: Write 1 entry with 2 event_ids + list of StructureEvents, flush, verify 1 row in `journal_runs` + 2 rows in `journal_events`
  4. **Flush creates tables**: Verify `journal_runs` and `journal_events` exist after flush
  5. **Flush is transactional**: If flush is not called, nothing in DB (buffer not yet committed)
  6. **Multiple flush rounds**: Write 2 entries, flush, write 2 more, flush → total 4 rows
  7. **Buffer auto-flush**: Set `buffer_size=2`, append 3 entries → auto-flushes after 2nd append
  8. **Event FK constraint**: Verify that attempting to insert a `journal_events` row with a non-existent `run_id` fails (though this shouldn't happen in normal usage since we always insert `journal_runs` first)
  9. **Double close safety**: `__aexit__` called twice should not error
  10. **Round-trip fidelity**: After flush, query the DB and verify each column equals the original `JournalEntry` data (including `breakout_pending` bool round-trip, `decision_invalidation` None round-trip, etc.)
  11. **Events round-trip**: Verify event columns (event_type, direction, status, level) round-trip correctly
  12. **Created_at auto-set**: Verify `created_at` is auto-populated on INSERT (not None, not empty)

  Use `tmp_path` fixture for temporary database files. Use `aiosqlite.connect()` for querying the DB to verify writes.

  Test helper pattern:
  ```python
  async def _verify_db(db_path: str, expected_runs: int, expected_events: int) -> tuple[list, list]:
      import aiosqlite
      async with aiosqlite.connect(db_path) as db:
          db.row_factory = aiosqlite.Row
          runs = await db.execute_fetchall("SELECT * FROM journal_runs")
          events = await db.execute_fetchall("SELECT * FROM journal_events")
          assert len(runs) == expected_runs, f"Expected {expected_runs} runs, got {len(runs)}"
          assert len(events) == expected_events, f"Expected {expected_events} events, got {len(events)}"
          return runs, events
  ```

  **Must NOT do**:
  - ❌ Do NOT write tests that depend on pipeline classes (`MarketSnapshot`, etc.) — those go in T4
  - ❌ Do NOT use real file paths — always use `tmp_path`
  - ❌ Do NOT leave database files behind — use `tmp_path` which auto-cleans

  **Recommended Agent Profile**: Generalist (python + testing)

  **Parallelization**: Wave 2 (parallel with T2), blocked-by T1

  **References**:
  - `journal.py` — JournalWriter API
  - `smartmoneyconcepts/structures.py` — `StructureEvent` (for creating test event data)
  - `tests/conftest.py` — `tmp_path` fixture example (already used in `tmp_csv_dir`)

  **Acceptance Criteria**:
  ```bash
  python -m pytest tests/test_journal.py -v --tb=short -k "journalwriter" 2>&1 | tail -5
  ```
  All JournalWriter tests pass.

  **QA Scenarios**:

  1. **Run all JournalWriter tests** (interactive_bash):
     - **Preconditions**: `journal.py` and `tests/test_journal.py` exist
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -m pytest tests/test_journal.py -v --tb=short -k "journalwriter" 2>&1 | tee /tmp/journal-writer-tests.txt`
     - **Expected Result**: All writer tests PASS
     - **Evidence**: `.sisyphus/evidence/journal-T3-writer-tests.txt`

  2. **Round-trip fidelity verification** (interactive_bash):
     - **Preconditions**: T3 tests written with round-trip assertions
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -m pytest tests/test_journal.py -v --tb=short -k "round_trip or fidelity" 2>&1`
     - **Expected Result**: Round-trip tests verify all columns match original data
     - **Evidence**: `.sisyphus/evidence/journal-T3-roundtrip.txt`


- [ ] T4. Integration test — Full pipeline → Journal entry → Verify DB
  **What to do**: Append an integration test to `tests/test_journal.py` that:
  1. Builds a `MarketSnapshot` (use `sample_snapshot` fixture from conftest or construct inline)
  2. Scores with `ConfluenceScorer`
  3. Builds `MarketNarrative` via `MarketNarrativeBuilder`
  4. Generates a `Decision` via `DecisionEngine`
  5. Creates a few `StructureEvent` objects inline (matching what the orchestration layer would provide)
  6. Generates a `run_id` via `make_run_id()`
  7. Constructs a `JournalEntry` from all of the above
  8. Opens `JournalWriter` → `append()` → `flush()`
  9. Queries the DB back and verifies:
     - 1 row in `journal_runs` with correct values for every column
     - N rows in `journal_events` with correct FK and event data
     - `created_at` is populated
     - `breakout_pending` round-trips as boolean
     - `decision_invalidation` and `decision_target` match decision values
  10. Cleanup: `tmp_path` handles automatic DB file removal

  The test must be a synchronous test that wraps async code in `asyncio.run()`. This avoids adding a `pytest-asyncio` dependency (not currently in the project).

  ```python
  def test_integration_full_pipeline(sample_snapshot, tmp_path):
      # Build pipeline
      scorer = ConfluenceScorer()
      result = scorer.score(sample_snapshot)
      
      builder = MarketNarrativeBuilder()
      narrative = builder.build(sample_snapshot, result)
      
      engine = DecisionEngine()
      decision = engine.decide(sample_snapshot, result)
      
      # Create test events
      events = [
          StructureEvent(event_type="BOS", direction=1, level=51000.0, 
                         swing_index=3, trigger_index=4, timestamp=pd.Timestamp.now(),
                         status="confirmed", event_id="evt00001"),
          StructureEvent(event_type="CHOCH", direction=-1, level=49000.0,
                         swing_index=5, trigger_index=6, timestamp=pd.Timestamp.now(),
                         status="provisional", event_id="evt00002"),
      ]
      
      # Build journal entry
      entry = JournalEntry(
          run_id=make_run_id(sample_snapshot.symbol, sample_snapshot.timeframe, sample_snapshot.timestamp),
          timestamp=sample_snapshot.timestamp,
          symbol=sample_snapshot.symbol,
          timeframe=sample_snapshot.timeframe,
          close=sample_snapshot.close,
          direction_score=result.direction_score,
          bias=result.bias,
          confidence=result.confidence,
          narrative_summary=narrative.conclusion,
          decision_action=decision.action,
          decision_invalidation=decision.invalidation,
          decision_target=decision.target,
          breakout_pending=decision.breakout_pending,
          event_ids=[e.event_id for e in events],
      )
      
      # Write to journal (wrap async in asyncio.run)
      db_path = str(tmp_path / "integration.db")
      async def _write():
          async with JournalWriter(db_path) as writer:
              await writer.append(entry, events)
              # __aexit__ flushes and closes
      asyncio.run(_write())
      
      # Verify
      import aiosqlite
      async with aiosqlite.connect(db_path) as db:
          db.row_factory = aiosqlite.Row
          rows = await db.execute_fetchall("SELECT * FROM journal_runs")
          assert len(rows) == 1
          row = rows[0]
          assert row["run_id"] == entry.run_id
          assert row["symbol"] == sample_snapshot.symbol
          assert row["close"] == sample_snapshot.close
          assert row["direction_score"] == result.direction_score
          # ... more assertions ...
          
          event_rows = await db.execute_fetchall("SELECT * FROM journal_events")
          assert len(event_rows) == 2
          assert event_rows[0]["run_id"] == entry.run_id
  ```

  **Must NOT do**:
  - ❌ Do NOT modify any pipeline classes
  - ❌ Do NOT create a real `BacktestHarness` or `SnapshotBuilder` for this test — the integration is about piping existing working classes through the journal
  - ❌ Do NOT use a global/static DB path — always use `tmp_path`

  **Recommended Agent Profile**: Generalist (python + integration) — async-python-patterns

  **Parallelization**: Wave 3 (after T1, T2, T3 complete)

  **References**:
  - `journal.py` — JournalEntry, JournalWriter, make_run_id
  - `market_snapshot.py` — MarketSnapshot
  - `confluence.py` — ConfluenceScorer, ConfluenceResult
  - `narrative.py` — MarketNarrativeBuilder, MarketNarrative
  - `decision_engine.py` — DecisionEngine, Decision
  - `smartmoneyconcepts/structures.py` — StructureEvent
  - `tests/conftest.py` — sample_snapshot fixture
  - `tests/test_decision_engine.py` — existing test pattern for async tests

  **Async note**: The test uses `asyncio.run()` to wrap async JournalWriter calls. This is a simpler approach than adding a `pytest-asyncio` dependency. Ensure `import asyncio` is at the top of the test file.

  **Acceptance Criteria**:
  ```bash
  python -m pytest tests/test_journal.py -v --tb=short -k "integration" 2>&1
  ```
  Integration test passes with all assertions.

  **QA Scenarios**:

  1. **Run integration test** (interactive_bash):
     - **Preconditions**: All prior tasks complete
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -m pytest tests/test_journal.py -v --tb=short -k "integration" 2>&1 | tee /tmp/journal-integration.txt`
     - **Expected Result**: Integration test PASSES — all pipeline → journal → DB assertions hold
     - **Evidence**: `.sisyphus/evidence/journal-T4-integration.txt`

  2. **Full test suite** (interactive_bash):
     - **Preconditions**: All tasks complete
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && python -m pytest tests/test_journal.py -v --tb=short 2>&1 | tee /tmp/journal-all-tests.txt`
     - **Expected Result**: ALL tests PASS (entries + writer + integration)
     - **Evidence**: `.sisyphus/evidence/journal-T4-all-tests.txt`

  3. **Verify no pipeline changes** (interactive_bash):
     - **Preconditions**: All tasks complete
     - **Steps**: `cd /home/parzivalxiii/Projects/smc-live-trading && git diff --name-only 2>&1 | tee /tmp/journal-git-diff.txt`
     - **Expected Result**: Only `journal.py`, `tests/test_journal.py` and possibly `.sisyphus/evidence/*` are shown (no pipeline files modified)
     - **Evidence**: `.sisyphus/evidence/journal-T4-no-pipeline-changes.txt`

---

## Final Verification Wave

F1. **Plan Compliance Audit** (oracle):
   - Verify all TODOs completed
   - Verify no pipeline classes modified
   - Verify schema matches Oracle review
   - Verify no scoring/logic leaked into journal

F2. **Code Quality Review** (unspecified-high):
   - Type annotations on all public functions
   - Async context manager pattern correct
   - No bare except clauses
   - No hardcoded paths
   - Docstrings on all public classes/methods

F3. **Real Manual QA** (unspecified-high + bash):
   - `python -c "from journal import JournalEntry, JournalWriter, make_run_id, SCHEMA_SQL; print('all imports OK')"`
   - `python -m pytest tests/test_journal.py -v --tb=short` — ALL GREEN
   - `git diff --name-only` — verify no pipeline files touched

F4. **Scope Fidelity Check** (deep):
   - Confirm `journal.py` does NOT import from `market_snapshot`, `confluence`, `narrative`, `decision_engine`
   - Confirm `JournalWriter` does NOT contain any scoring, filtering, or analysis logic
   - Confirm no new files created outside agreed scope

---

## Commit Strategy

1. **Stage**: `git add journal.py tests/test_journal.py`
2. **Commit message**: `feat: Add SQLite-based decision journal module`
3. **Details in commit body**:
   ```
   - JournalEntry dataclass with full decision cycle fields
   - JournalWriter async context manager (aiosqlite)
   - Compound run_id format: {symbol}_{timeframe}_{timestamp}_{uuid}
   - Two-table schema: journal_runs + journal_events
   - Unit tests for entry construction, writer lifecycle, round-trip
   - Integration test: full pipeline → journal → DB verification
   - No changes to existing pipeline classes
   ```
4. **Evidence**: Stage `.sisyphus/evidence/journal-*.txt`
5. **No force push**, no `-i` flags

---

## Success Criteria

1. `journal.py` passes import with zero errors
2. `JournalWriter` correctly writes and flushes entries to SQLite
3. Data round-trips with full fidelity (bool ↔ int, pd.Timestamp ↔ str, None preservation)
4. Events are linked to runs via foreign key
5. All unit tests pass
6. Integration test passes — full pipeline → journal → DB verify
7. Zero changes to existing pipeline classes
8. Schema creation is idempotent (safe to run multiple times)
9. Evidence files saved for all QA scenarios
