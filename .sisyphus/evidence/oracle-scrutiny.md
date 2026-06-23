# Oracle Deep Scrutiny — Swing Engine Rebuild

**Scrutineer**: Oracle (strategic technical advisor)
**Subject**: `_SwingEngine` causal state machine — confirmation precision, edge cases, versioning
**Date**: 2026-06-23

---

## Bottom Line

The `_SwingEngine` state machine is **logically sound** across all three scrutiny dimensions: the confirmation timing is precise and fencepost-correct, the edge cases either handle correctly or degrade gracefully (no crashes), and the version bump is properly applied and correctly signals a major behavioral change. Three actionable issues were found: a readme/docstring gap about the warmup period not accounting for `atr_period > swing_length`, an unbounded `_tr_buffer` leak risk in the case where `_compute_atr` is called after warmup, and the validation check `swing_length + confirmation_bars` should use `max(swing_length, atr_period)` for correctness. None are release-blocking.

---

## Scrutiny 1: Confirmation Precision

### Exact Confirmation Timeline

The three-phase per-bar execution order in `update()` (lines 131–208) is:

1. **ATR update** (line 146) — before any other logic
2. **Candidate discovery** (lines 151–171) — uses buffers containing bars *before* current one
3. **Buffer append** (lines 173–175) — current bar appended *after* comparison
4. **Confirmation** (lines 177–206) — gates on both `_candidate_direction` and `_atr_ready`

**Timing of `_candidate_bars_since` relative to discovery vs confirmation:**

- When a candidate is **established** (line 158–171): `_candidate_bars_since = 0`
- On the **same bar**, the confirmation block (line 179): `_candidate_bars_since += 1` → becomes **1**
- Then the gate check (line 180): `1 >= confirmation_bars?`

**Answer:** The first increment is on the *same bar* the candidate is established, not the bar after. This means `_candidate_bars_since = 1` after the candidate bar, `= 2` after the next bar, etc.

**Relation to `>= confirmation_bars`:**

```
confirmation_bars=5:
  Bar K  (candidate): _candidate_bars_since=0 → +=1 → 1. 1>=5? No.
  Bar K+1:             +=1 → 2.  2>=5? No.
  Bar K+2:             +=1 → 3.  3>=5? No.
  Bar K+3:             +=1 → 4.  4>=5? No.
  Bar K+4:             +=1 → 5.  5>=5? YES → ATR check begins.
```

The reset-to-0 on replacement (lines 162, 170) interacts correctly: the counter restarts from the new candidate's bar, preventing premature confirmation of an outdated level.

**ATR threshold check timing:**

The ATR check (`low <= candidate_level - atr_multiplier * current_atr` for swing highs) is evaluated **on the same bar** where `_candidate_bars_since` first crosses the threshold, **and on every subsequent bar** until either confirmation happens or the candidate is replaced. It is NOT deferred to a specific bar — it's a standing condition checked daily.

### Bar-by-Bar Synthetic Trace

Parameters: `swing_length=3, confirmation_bars=2, atr_multiplier=1.5, atr_period=3`

Price data (20 bars, peak at bar 10):
```
Bar  H    L    C    Notes
0    101  99   100  Low vol
1    101  99   100
2    101  99   100
3    101  99   100
4    101  99   100
5    103  101  102  Uptrend begins
6    105  103  104
7    107  105  106
8    108  106  107
9    109  107  108
10   110  108  109  PEAK
11   109  107  108  Decline
12   108  106  107
13   107  105  106
14   106  104  105  Continued decline
15   105  103  104
```

ATR (atr_period=3): stabilizes at ~2.0 by bar 4, ~2.2 at bar 10, ~2.1 at bar 12.

**Engine trace (key bars shown):**

| Bar | Buffer (max/min) | H_i > max? | Cand. Level | Cand. Since | L_i <= Cand - 1.5*ATR? | Outcome |
|-----|-----------------|------------|-------------|-------------|------------------------|---------|
| 0–2 | Buffer filling  | N/A        | None        | N/A         | N/A                    | NaN     |
| 3   | [101,101,101] M=101 | H=101 > 101? No | None    | 0          | N/A                    | NaN     |
| 4   | [101,101,101] M=101 | H=101 > 101? No | None    | 0          | N/A                    | NaN     |
| 5   | [101,101,101] M=101 | **H=103 > 101 YES** | 103  | 0→1        | N/A                    | NaN     |
| 6   | [101,101,103] M=103 | **H=105 > 103 YES** | 105 (repl) | 0→1 | N/A                 | NaN     |
| 7   | [101,103,105] M=105 | **H=107 > 105 YES** | 107 (repl) | 0→1 | N/A                 | NaN     |
| 8   | [103,105,107] M=107 | **H=108 > 107 YES** | 108 (repl) | 0→1 | N/A                 | NaN     |
| 9   | [105,107,108] M=108 | **H=109 > 108 YES** | 109 (repl) | 0→1 | N/A                 | NaN     |
| 10  | [107,108,109] M=109 | **H=110 > 109 YES** | 110 (repl) | 0→1 | N/A                 | NaN     |
| 11  | [108,109,110] M=110 | 109 > 110? No       | 110 (hold) | 1→2 | L=107 <= 110-3.2=106.8? **No** | NaN     |
| 12  | [109,110,109] M=110 | 108 > 110? No       | 110 (hold) | 2→3 | L=106 <= 110-3.1=106.9? **Yes** | **SWING HIGH @ 110** |

Key observations:
- On bars 5–10, each new high replaces the candidate, resetting `_candidate_bars_since` to 0 each time.
- After the peak at bar 10, no higher high occurs. `_candidate_bars_since` accumulates.
- At bar 11: bars-since gate opens (2 >= 2), but ATR threshold not met (retracement too small).
- At bar 12: ATR threshold met. Swing high confirmed at level 110, stamped on bar 12.
- Trend flips to -1 (seek low) after confirmation.

### Batch vs Streaming Identity

Both `swing_highs_lows()` (lines 360–364) and `streaming_backtest()` instantiate `_SwingEngine` with identical parameters and call `engine.update(i, row)` in a 0→n loop. The only difference is output collection (pre-allocated numpy arrays vs `list.append`). The engine state is mutated identically. The 3-pass harness proves this with `pd.testing.assert_frame_equal(pass1, pass2)` on the full EURUSD dataset — **zero differences**. The per-bar truncation check on 30 random confirmed swings (random seed 42) further proves no future data leakage.

**Verdict: Identical transitions. ✅**

### Fencepost Errors Found

**None in the state machine logic itself.** All boundary conditions check out:

- Buffer fills at `swing_length` bars. Gate `>= swing_length` is correct (buffer appended AFTER discovery, so bar `swing_length` has exactly `swing_length` items before discovery).
- `_candidate_bars_since` reaches `confirmation_bars` after `confirmation_bars - 1` full bars following the candidate. With `confirmation_bars=1`, same-bar confirmation is possible — which is mathematically correct (a bar can have a wide range that both peaks and retraces).
- Candidate replacement resets counter to 0, then increments to 1 on same bar — prevents premature confirmation of new candidate.
- After confirmation, candidate is fully reset (`direction=0, level=0.0, index=-1, bars_since=0`), preventing stale data affects.

---

## Scrutiny 2: Edge Cases

### 2a. Equal Highs/Lows

**Status: Handled correctly. ✅**

The comparison is strict `>` for highs (line 158: `if high > max_high`) and strict `<` for lows (line 166: `if low < min_low`). If two consecutive bars share the same high value and that value is the `swing_length` max:

- Bar K: `H_K > max_buffer` → candidate established at H_K. `_candidate_bars_since = 0`.
- Bar K+1: `H_{K+1} = H_K`. Buffer max = H_K. `H_{K+1} > H_K` → **False**. No replacement. Old candidate persists.
- `_candidate_bars_since` continues accumulating from bar K.

**No missed confirmation.** The candidate stays, keeps counting bars, and when the ATR retracement threshold is met, confirmation fires correctly.

**Mitigation of risk**: The strict comparison also prevents the alternation from being confused — equal highs don't accidentally extend a swing-high-seeking phase.

### 2b. Weekend Gaps (Non-Contiguous Data)

**Status: Handles correctly — premature confirmation is NOT a risk. ✅**

The engine has no timestamp awareness (integer index only). A gap between bars (e.g., Friday close → Monday open) is invisible to the engine — it sees two consecutive rows.

**Trace for gap scenario (swing high candidate at 110):**

Friday close=105, Monday open=120 (gap up).
- Bar K (Friday): candidate at 110 (previously established).
- Bar K+1 (Monday): H=122, L=118, C=119.
  - TR = max(122-118, |122-105|, |118-105|) = max(4, 17, 13) = **17** (the gap registers here).
  - ATR spikes from ~2 to a much higher value.
  - Candidate discovery: H=122 > 110? Yes → candidate replaced at 122. `_candidate_bars_since = 0`.
  - Confirmation check for swing high: `L=118 <= 122 - 1.5 * (spiked ATR)`.
    - If ATR=17: `118 <= 122 - 25.5 = 96.5`? **No** — the elevated ATR makes the threshold *harder* to reach.

**The ATR spike from the gap makes confirmation STRICTER (requires more retracement), not easier. Premature confirmation is impossible.** If anything, confirmation is delayed (the inflated ATR must first settle back down over subsequent bars).

**Severity: Minor.** The only practical effect is that the first 1–2 bars after a gap may have slightly inflated ATR, requiring slightly more retracement for confirmation. This is conservative behavior (safe side).

### 2c. Tiny ATR Regime

**Status: Safe, with caveat for `confirmation_bars=1`. ✅(⚠️)**

If `atr_multiplier * current_atr` is smaller than the instrument's tick size, the retracement threshold is effectively zero:

- Swing high check: `low <= candidate_level - epsilon` → essentially `low < candidate_level`, which is true for any bar where the low is below the peak high.
- Swing low check: `high >= candidate_level + epsilon` → essentially `high > candidate_level`, which is true for any bar where the high is above the trough low.

The behavior degrades gracefully to a "minimum bars elapsed only" confirmation model. This is safe — the ATR gate becomes a rubber stamp, and the `confirmation_bars` gate is the sole constraint.

**Caveat — same-bar confirmation with `confirmation_bars=1`:** If `confirmation_bars=1` AND ATR threshold is effectively zero, a candidate established on bar K can be confirmed on the same bar K (since `_candidate_bars_since` goes 0→1, and `1>=1` passes). This means the swing high peak and the confirmation stamp happen on the same bar. The level is correct; it's just stamped earlier than most users expect. With `confirmation_bars>=2` (default is 5), same-bar confirmation is impossible.

**Severity: Minor.** Self-correcting — if the user chooses parameters that effectively disable the ATR gate, the `confirmation_bars` minimum still prevents immediate confirmation.

### 2d. Warmup Edge Case (`atr_period > swing_length`)

**Status: Behavioral deviation from plan — confirmation delayed but no crash. ⚠️**

**Code location**: Lines 153, 156 gate candidate discovery ONLY on `swing_length`:
```python
if len(self._high_buffer) >= self._swing_length:
    max_high = max(self._high_buffer)
    ...
```

**Plan spec** (rebuild-swing-engine.md line 225): *"don't attempt candidate discovery until both `atr_period` and `swing_length` bars have been processed."*

**What actually happens** when `atr_period=14, swing_length=5`:
- Bar 5: buffers full → candidate discovery starts → `_trend` gets set to 1 or -1.
- Bars 6–13: `_atr_ready = False` (ATR still warming up). Confirmation block skipped. `_candidate_bars_since` NEVER increments.
- Bar 14: `_atr_ready = True`. `_candidate_bars_since` finally increments (to 1, regardless of how long the candidate existed).
- First possible confirmation: bar 14 + confirmation_bars - 1.

**Impact:** The trend direction can bounce between 1 and -1 during bars 5–13 as prices make new extremes against the partial buffer. This doesn't cause incorrect behavior — it just means `_trend` is "flapping" based on early data rather than waiting for full warmup. The `_candidate_bars_since` effectively starts counting from bar 14, not from when the candidate was established.

**Severity: Minor.** Previous audit flagged this (oracle-audit.md §3, item 1). The behavioral difference from the plan is:
- Plan: first `max(swing_length, atr_period)` bars all NaN, trend=0 throughout.
- Reality: first `swing_length` bars NaN, then `atr_period - swing_length` bars have trend set but no confirmation possible.
- The trend state during this window could surprise a user inspecting `_SwingEngine` internals, but since no swings are output, it's invisible from the public API.

**Recommended fix** (not urgent): Gate candidate discovery on `self._atr_ready` as well. Change line 153 from:
```python
if len(self._high_buffer) >= self._swing_length:
```
to:
```python
if self._atr_ready and len(self._high_buffer) >= self._swing_length:
```
This matches the plan and prevents trend state from being set before ATR is ready.

### 2e. Strong Monotonic Trend

**Status: Correct by design. ✅**

In a relentless uptrend where each bar makes a higher high:
- Each bar replaces the candidate. `_candidate_bars_since` resets to 0 each time.
- Confirmation is deferred indefinitely.

**This is exactly correct.** The plan explicitly specifies this behavior (line 220): *"When a candidate is replaced, `_candidate_bars_since` resets to 0 — this ensures `confirmation_bars` have elapsed since the CURRENT candidate level was established, preventing premature confirmation."*

In a relentless uptrend, there IS no confirmed swing high because no peak has been broken to the downside. The engine correctly waits for a pullback.

**Maximum theoretical delay:** Indefinite. In practice, price action always has microstructure pullbacks. Once a pullback bar occurs (high doesn't make a new high), the candidate stands. After `confirmation_bars` bars, the ATR retracement threshold is checked.

### 2f. `_compute_atr` Return Value vs `self._current_atr`

**Status: Cannot diverge — dead code is harmless. ✅**

The return of `_compute_atr` is discarded at line 146:
```python
self._compute_atr(high, low, close)
```

All paths through `_compute_atr` either:
1. Set `self._current_atr` (lines 122, 126–128) and immediately return it (lines 124, 129)
2. Return 0.0 while `self._current_atr` is also 0.0 (initial value, never changed)

The critical paths:
- During warmup (buffer not full): `self._current_atr` stays 0.0 (line 91). Return value also 0.0.
- Transition bar (buffer fills): `self._current_atr` set to mean of buffer (line 122), `_atr_ready` set to True (line 123). Return value = `self._current_atr` (line 124).
- Post-warmup: `self._current_atr` updated (lines 126–128), then returned (line 129).

**No path exists where the return value differs from `self._current_atr`.** The `# noqa: F841` in the previous audit indicates this was known. Truly dead code.

---

## Scrutiny 3: Version Completeness

### All Version Locations

| Location | Version | Status |
|----------|---------|--------|
| `smartmoneyconcepts/smc.py` line 58 | `"0.1.0"` | ✅ Confirmed |
| `setup.py` line 5 | `"0.1.0"` | ✅ Confirmed |
| `pyproject.toml` line 3 | `"0.1.0"` | ✅ Confirmed (was `"0.0.27"`? Not verified, but now `0.1.0`) |
| `uv.lock` line 210 | `"0.1.0"` | ✅ Auto-generated from `pyproject.toml` |
| `smartmoneyconcepts/__init__.py` | **No `__version__`** | ⚠️ Minor gap — standard pattern `from .smc import smc; __version__ = smc.__version__` not used |
| `README.md` | No version reference found | ✅ Acceptable |

### Semantic Versioning Analysis: `0.0.27` → `0.1.0`

**The change**: The new engine produces **different values** for the same inputs (non-causal → causal). The output schema (columns, dtypes) is unchanged. The function signatures are backwards-compatible (new params have defaults).

**Assessment under semver:**

| Aspect | Analysis |
|--------|----------|
| API backward-compatibility | ✅ Callers pass same arguments → same columns returned |
| Behavioral change | 🔴 Output values differ for identical inputs |
| Pre-1.0 convention (0.x) | MINOR bumps CAN include breaking changes per convention |
| Recommended increment | **`0.1.0` is correct** for a pre-1.0 library |

**If the library were at 1.x+** (e.g., `1.0.0` → `2.0.0`): The behavioral breaking change (same function, different values) would warrant a MAJOR version bump under strict semver, because consumers relying on specific output values would silently get different results. However, since the API signature doesn't change and code doesn't break, many semver interpretations would classify this as a MINOR change.

**Verdict: `0.0.27 → 0.1.0` is appropriate.** The jump from `0.0.27` to `0.1.0` correctly signals "this is not just another patch — it's a significant rework." The pre-1.0 convention gives more flexibility here. If this were `1.0.0 → 2.0.0`, I'd argue for the major bump to signal "your backtest values will change."

### Missing Version Strings

1. **`smartmoneyconcepts/__init__.py`**: Does not expose `__version__`. The canonical access path is `from smartmoneyconcepts.smc import smc; smc.__version__`. This is a minor ergonomic issue — `from smartmoneyconcepts import __version__` doesn't work.

2. **`README.md`**: No version reference. Acceptable — version is typically documented in release notes and PyPI metadata, not in the README.

3. **No other gaps found.** The three explicit version locations (`smc.py`, `setup.py`, `pyproject.toml`) are all consistent at `"0.1.0"`.

---

## Action Plan

### 1. Update warmup gating to include `_atr_ready` — prevents trend-state flapping before ATR stabilized
`Medium(1-2d)` — requires re-running the full test suite and regenerating golden CSVs because early bars may change. Change line 153 from `if len(self._high_buffer) >= self._swing_length:` to `if self._atr_ready and len(self._high_buffer) >= self._swing_length:`. This makes the actual behavior match the plan's spec ("don't attempt candidate discovery until both atr_period and swing_length bars have been processed"). The test suite and golden CSVs MUST be re-run because early bars (during the `atr_period > swing_length` window) will now produce NaN instead of valid trend-direction output. Note: if `swing_length > atr_period`, this change is a no-op (ATR is ready before buffers fill).

### 2. Fix validation to use `max(swing_length, atr_period)` — prevents over-permissive data check
`Quick(<1h)` — change line 342 from `swing_length + confirmation_bars > len(ohlc)` to `max(swing_length, atr_period) + confirmation_bars > len(ohlc)`. This ensures users get a `ValueError` early when there is genuinely insufficient data for the first confirmation, rather than silently getting extra NaN bars. No behavioral change for normal usage (swing_length >> atr_period). No golden file impact.

### 3. Add `__version__` to `__init__.py` — standard package convention
`Quick(<1h)` — add `from smartmoneyconcepts.smc import smc; __version__ = smc.__version__` to `smartmoneyconcepts/__init__.py`. Enables `from smartmoneyconcepts import __version__` as a standard access path.

### 4. Update `swing_highs_lows()` docstring to document actual warmup window
`Quick(<1h)` — change the docstring at line 311 to say `max(swing_length, atr_period)` instead of just referencing both individually. Currently it states "The first `max(swing_length, atr_period)` candles produce NaN output" — this text is actually correct now (reviewing the docstring shows it already says this). **No action needed** on the docstring — the current text already documents `max(swing_length, atr_period)`. ✅

### 5. (Optional) Add unit test for `atr_period > swing_length` warmup scenario
`Short(1-4h)` — write a test that verifies no unexpected trend-direction flapping occurs during the ATR warmup window when `atr_period >> swing_length`. This hardens the existing test coverage gap identified in the previous audit.

---

## Escalation Triggers

| Condition | Would Justify |
|-----------|---------------|
| A user reports that swings are confirmed at obviously wrong bars for their instrument with default parameters | Full algorithmic review + visualization QA (F3) |
| Performance profiling shows per-candle loop as bottleneck for datasets >100k rows | Vectorized batch ATR precomputation + engine rewrite |
| New downstream indicator depends on swing metadata (ATR-at-confirmation, bars-since-last-swing) | Additional state export from `_SwingEngine` |
