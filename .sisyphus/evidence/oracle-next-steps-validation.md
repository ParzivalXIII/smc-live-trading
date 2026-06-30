# Oracle Validation — Next Steps: Replay, Continuity, Journal, Alerts

**Date**: 2026-06-24
**Scope**: Validate 4 proposed next steps for the SMC live trading pipeline
**Files analyzed**: `orchestrator.py`, `live_smc_buffer.py`, `decision_engine.py`, `journal.py`, `tests/test_orchestrator.py`, `tests/test_live_smc_buffer.py`

---

## Bottom Line

Steps 1–4 are **directionally correct** but Step 1 hits a concrete blocker: `LiveOrchestrator.load()` in replay mode returns before calling `_smc_buffer.update()`, so the buffer never advances — swings never accumulate, the 26-column report stays empty, and every `step()` produces an identical decision. Fixing `load()` to unconditionally call `_smc_buffer.update()` (regardless of mode) unblocks all four steps. Steps 2–4 are well-specified with no other blockers, though Step 4's alert thresholds (0.7/0.3) are reasonable starting defaults that will likely need tuning against real data.

---

## Step 1: Replay Pattern

### The Correct Pattern

One `OrchestratorContext` with `mode="replay"`, one `LiveOrchestrator`, one `LiveSmcBuffer` — created once before the loop. Inside the loop, the caller sets `ctx.ta_row` to the current candle's OHLCV-enriched series, then calls `orch.step()`. The buffer accumulates state across every call. The orchestrator instance is never recreated.

### The Replay Mode Gap — CONFIRMED

**This is a real bug.** In `orchestrator.py:129–154`:

```python
def load(self) -> None:
    self._transition(OrchestrationState.LOAD)

    if self.context.mode == "replay":
        return  # ← RETURNS EARLY — buffer never updated

    df = load_ta_series(...)
    self.context.ta_row = df.iloc[-1]
    self._smc_buffer.update(self.context.ta_row)       # ← Only reached in live mode
    self.context.smc_report = self._smc_buffer.get_smc_report()
```

**Effect**: In replay mode, `step()` completes the full pipeline (load → analyze → decide → journal) but:
- `_smc_buffer.update()` is never called → buffer internal state never advances
- `_candle_index` stays at 0
- `_swing_rows` stays empty
- `_structure_engine.events` stays empty
- `get_smc_report()` returns the same empty/frozen DataFrame every time
- `SnapshotBuilder` sees identical SMC data every cycle → identical decisions every cycle

**The existing test** `test_replay_mode_skips_load` at `tests/test_orchestrator.py:60–65` explicitly tests that replay mode does NOT call `load_ta_series()` — which is correct — but does not verify that the buffer is updated. The test uses `ta_row = "preloaded"` (a string), so calling `_smc_buffer.update()` on it would fail. This test must be updated when the fix is applied.

### Fix Recommendation

**Short** (under 1 hour). Change `load()` so that `_smc_buffer.update()` and `get_smc_report()` run unconditionally — the only mode-dependent behavior should be the data source for `ta_row`:

```python
def load(self) -> None:
    self._transition(OrchestrationState.LOAD)

    if self.context.mode != "replay":
        df = load_ta_series(
            self.context.symbol, self.context.timeframe, self.context.data_dir, tail=1,
        )
        if df is not None and not df.empty:
            self.context.ta_row = df.iloc[-1]
        else:
            raise RuntimeError(f"No TA data for {self.context.symbol}...")

    # Always advance the buffer — replay and live both need this
    self._smc_buffer.update(self.context.ta_row)
    self.context.smc_report = self._smc_buffer.get_smc_report()
```

**Tests affected** (need updating after fix):
- `test_replay_mode_skips_load` — currently sets `ta_row = "preloaded"` (string); needs a real `pd.Series` with OHLCV columns, and the test name should be updated to reflect that replay mode skips `load_ta_series()` but still updates the buffer.
- `test_default_mode_is_live` — unaffected.
- `test_full_step_with_mock_buffer` — uses a `MagicMock` buffer; unaffected because `mock_buffer.update()` is a no-op mock. But the test currently verifies that `replay` mode works with a mock buffer — after the fix it will also verify that `mock_buffer.update()` was called.
- `test_step_produces_journal_entry` — same as above, uses mock buffer, unaffected.

**New tests needed**:
- A test that creates a real `LiveSmcBuffer`, feeds 100 candles via `orch.step()` in replay mode, and verifies `_candle_index == 100` and swings are detected.

---

## Step 2: Buffer Continuity Verification

### What "Continuity" Means Concretely

| Property | What to Verify | Pass Criterion |
|----------|---------------|----------------|
| `_candle_index` | Increments by 1 on every `update()` call | After N candles, `_candle_index == N` |
| `_swing_rows` | Appends a dict on every `update()` call | `len(_swing_rows) == N` |
| `_ohlcv_buffer` | Appends OHLCV dict on every `update()` call | `len(_ohlcv_buffer) == N` |
| `_report` | Grows to `report_window` (200), then trims | `len(_report) <= 200`, `_report.tail(200).index` is contiguous |
| `get_smc_report()` | Returns `_report.tail(report_window)` (up to 200 rows) | Length never exceeds 200 |
| `_structure_engine.events` | Accumulates all BOS/CHOCH events across the run | `len(events)` grows monotonically (may stay 0 for early candles) |
| OB/Liq/Retrace columns | Populated after sufficient swings established | Non-NaN values appear after initial warmup period (~swing_length + confirmation_bars candles) |
| Determinism | Same dataset → same report | Two runs produce byte-identical reports |

### The `_ohlcv_buffer` Unbounded Growth Concern

**Current behavior**: `_ohlcv_buffer` is a Python `list[dict]` that grows unboundedly — one entry per `update()` call, never trimmed.

**Quantified risk**:
- 19,000 candles of BTCUSDT 4H data: ~19,000 dicts × 5 float fields × 24 bytes ≈ **2.3 MB**
- One year of 1H data (8,760 candles): ~**1.0 MB**
- Five years of 1H data (43,800 candles): ~**5.2 MB**

**Assessment**: Acceptable for replay runs. Not acceptable for a long-running live process (months of 1H data would reach 50+ MB).

**Recommendation**: For now (Step 2), leave it unbounded — the memory footprint is negligible for replay of any reasonable dataset. Add a `maxlen` cap (e.g., `max_buffer_candles: int = 5000`) as a quick fix before live deployment. When trimmed, the downstream batch methods (`smc.ob()`, `smc.liquidity()`, `smc.retracements()`) would recompute on a sliding window instead of all-time data — this is a behavioral change that needs explicit design. Document as a known limitation for now.

### Confirmation Protocol

Run the replay on BTCUSDT 4H (19k rows). After completion, validate:

```python
# All in one script
buf = LiveSmcBuffer()
for i, row in enumerate(dataset):
    buf.update(row)

assert buf._candle_index == len(dataset)
assert len(buf._swing_rows) == len(dataset)
assert len(buf._ohlcv_buffer) == len(dataset)
assert len(buf._report) <= buf._report_window  # 200
assert len(buf._report) == min(len(dataset), buf._report_window)  # report fills up
report = buf.get_smc_report()
assert len(report.columns) == 26
# At least some swings detected (dataset-dependent)
assert report["SwingHighLow"].notna().sum() > 0
```

---

## Step 3: Journal Wiring

### Caller Pattern (Correct as Designed)

The orchestrator produces a `JournalEntry` in its JOURNAL state. The caller persists it. This is the right separation because:

- **`LiveOrchestrator` stays pure sync** — no async context, no dependency on `JournalWriter` or `aiosqlite`
- **`sync_write_entry` exists** in `orchestrator.py:250–267` as a sync bridge wrapping `asyncio.run()` on `writer.append()` + `writer.flush()`
- **The caller manages the `JournalWriter` lifecycle** — create once, enter async context, loop over candles calling `sync_write_entry`, exit on shutdown

The **correct caller pattern** (which the orchestrator docs should document explicitly):

```python
async def replay_loop(journal_path: str, dataset: list[pd.Series]):
    async with JournalWriter(journal_path) as writer:
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="4h", mode="replay")
        buf = LiveSmcBuffer()
        orch = LiveOrchestrator(ctx, smc_buffer=buf)
        
        for candle in dataset:
            ctx.ta_row = candle  # Pre-populate with current candle
            orch.step()          # load → analyze → decide → journal
            sync_write_entry(writer, ctx.entry)  # Persist immediately
```

### Where Flush Happens

Two flush points:

1. **Every `sync_write_entry` call** — calls `writer.flush()` after `append()`. This means every cycle is committed individually. For high-frequency trading (e.g., 1m candles), this could be optimized to flush every N cycles.

2. **On `JournalWriter.__aexit__`** — the context manager's exit flushes any remaining buffer. This is a safety net.

**Recommendation**: The current `sync_write_entry` flushes every entry. This is correct for a first implementation — the I/O overhead is negligible for 4H or 1H data. If down to 1m or tick data, add a `flush_interval` parameter to buffer N entries before flushing.

---

## Step 4: Alert Layer

### Design Spec

Standalone module (`alerts.py`), separate from the orchestrator. The `AlertWatcher` class tracks the previous `Decision` and compares it to the current one on every cycle.

| Aspect | Decision |
|--------|----------|
| **Module** | `alerts.py` — no dependencies on orchestrator or JournalWriter |
| **Class** | `AlertWatcher` — owns `_previous_decision: Decision \| None` |
| **Method** | `check(decision: Decision) -> list[Alert]` — one method, no side effects |
| **Reset** | `reset()` method — clears previous decision for a new run |
| **Thread safety** | Not required — single-threaded sync pipeline |

### Alert Types and Triggers

| Alert Type | Trigger Condition | Severity | Example Message |
|-----------|-------------------|----------|-----------------|
| `bias_change` | `decision.bias != previous.bias` | `warning` | "Bias changed: bullish → bearish" |
| `confidence_cross_high` | `decision.confidence >= 0.7 and previous.confidence < 0.7` | `info` | "Confidence crossed above 0.7 (high conviction)" |
| `confidence_cross_low` | `decision.confidence <= 0.3 and previous.confidence > 0.3` | `info` | "Confidence dropped below 0.3 (low conviction)" |
| `action_change` | `decision.action != previous.action` | `info` | "Action changed: look_for_longs → stand_aside" |
| `breakout_pending` | `decision.breakout_pending and not previous.breakout_pending` | `info` | "Breakout pending at level 51200.0" |

Note: `confidence` is a continuous float (0.0–1.0). The thresholds 0.7 and 0.3 are starting defaults — they should be configurable. The `confidence_cross_high` and `confidence_cross_low` are two separate alerts because the transition direction matters.

### `Alert` Dataclass

```python
@dataclass
class Alert:
    alert_type: str    # e.g. "bias_change", "confidence_cross_high", etc.
    severity: str      # "info", "warning", "critical"
    message: str
    previous: Any      # Previous value of the triggering field
    current: Any       # Current value
    timestamp: pd.Timestamp | None = None  # Optional: set by caller or watcher
```

### Standalone Module Decision — CORRECT

**Why standalone is better than integrating into the orchestrator**:

1. **Single Responsibility** — The orchestrator orchestrates the pipeline. Alerts are a downstream consumer, not part of pipeline logic.
2. **Testability** — `AlertWatcher.check(decision)` is a pure function (given previous state). No mocks needed.
3. **Pluggability** — The alert watcher can be swapped, extended, or removed without touching pipeline code.
4. **Separation of concerns with Decision** — The `Decision` dataclass has no alert-related fields. The watcher observes it from outside, keeping `Decision` clean.

### Integration Point

In the live loop, after `orch.step()` and before/after `sync_write_entry`:

```python
watcher = AlertWatcher()
for candle in dataset:
    orch.step()
    alerts = watcher.check(ctx.decision)
    for alert in alerts:
        logger.warning(f"[ALERT] {alert.severity}: {alert.message}")
    sync_write_entry(writer, ctx.entry)
```

---

## Action Plan

### Phase 1: Fix Replay Mode Gap (Quick — <1h)

- [ ] 1.1. In `orchestrator.py`: move `_smc_buffer.update()` and `get_smc_report()` out of the `if mode != "replay"` guard so they run unconditionally.
- [ ] 1.2. Update `test_replay_mode_skips_load` to use a real `pd.Series` with OHLCV columns and verify the buffer is updated (calls `mock_buffer.update()`).
- [ ] 1.3. Add a new test: replay mode with a real `LiveSmcBuffer`, 100 candles → verify `_candle_index == 100` and swings detected.
- [ ] 1.4. Run existing full test suite to confirm no regressions.

### Phase 2: Buffer Continuity Verification (Short — 1–4h)

- [ ] 2.1. Write a one-off verification script that loads BTCUSDT 4H data, creates one `LiveSmcBuffer`, feeds all 19k candles via `update()`, and asserts: `_candle_index == N`, `len(_swing_rows) == N`, `_report` ≤ 200 rows, at least some swings detected, `len(events)` > 0.
- [ ] 2.2. Verify `get_smc_report()` has all 26 columns and non-NaN values in swing/OB/liquidity columns after warmup.
- [ ] 2.3. Run the same verification through `LiveOrchestrator` (with the Phase 1 fix) in replay mode to confirm continuity is preserved through the full pipeline.
- [ ] 2.4. Document the `_ohlcv_buffer` unbounded growth as a known limitation; add a `max_buffer_candles` parameter stub (default `None` = unbounded) for future sliding-window design.

### Phase 3: Journal Wiring (Short — 1–4h)

- [ ] 3.1. Write an end-to-end test: create `JournalWriter`, run 10 replay cycles with `sync_write_entry` after each, verify 10 rows in `journal_runs` and matching rows in `journal_events`.
- [ ] 3.2. Flush verification: call `sync_write_entry`, reconnect with a new `JournalWriter`, query `SELECT COUNT(*) FROM journal_runs` — confirm persistence is durable.
- [ ] 3.3. Error handling test: verify `sync_write_entry` raises `RuntimeError` on persistence failure (e.g., closed writer).

### Phase 4: Alert Layer (Short — 1–4h)

- [ ] 4.1. Create `alerts.py` with `Alert` dataclass and `AlertWatcher` class.
- [ ] 4.2. Unit tests for each alert trigger: bias change, confidence crossings, action change, breakout_pending transition.
- [ ] 4.3. Edge case tests: first call (no previous decision → no alerts), identical consecutive decisions → no alerts, simultaneous triggers (bias + action change same cycle → both emitted).
- [ ] 4.4. Integration wiring: add watcher to the replay loop (Phase 2) and verify alerts are produced for a known dataset.
- [ ] 4.5. Optional: make confidence thresholds (`high_conviction: float = 0.7`, `low_conviction: float = 0.3`) configurable in `AlertWatcher.__init__`.

---

## Escalation Triggers

| Condition | What It Means | Action |
|-----------|--------------|--------|
| `_ohlcv_buffer` exceeds 100k entries in live mode | Live process has been running for years without restart | Add sliding-window trim or periodic buffer reset |
| More than 2 of the 4 phases have architectural blockers | The plan's assumptions are wrong | Stop, re-interview, produce revised plan |
| Alert volume exceeds 1 alert per 10 candles on real data | Thresholds too sensitive (0.7/0.3 may be wrong for the instrument) | Make thresholds configurable; set per-instrument defaults via config |
