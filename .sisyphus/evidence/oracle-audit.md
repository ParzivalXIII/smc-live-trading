# Oracle Post-Implementation Audit — Swing Engine Rebuild

**Auditor**: Oracle (strategic technical advisor)
**Plan**: `.sisyphus/plans/rebuild-swing-engine.md`
**Implementation**: Smart Money Concepts v0.1.0 (`smartmoneyconcepts/smc.py`)
**Date**: 2026-06-22

---

## Bottom Line

The swing engine rebuild is **substantially complete** with all core requirements met: the causal streaming state machine works correctly, all 13 tasks plus 3 of 4 final verification tasks pass their acceptance criteria, and the full test suite produces zero failures. Two gaps exist: the GIF visualization (F3) was not run, and two QA evidence files from T1/T2 are missing. Three pre-existing issues in downstream methods (`retracements()` extreme values, `liquidity()` swept/end ordering, and `ob()` zero-volume dataset limitation) were correctly left untouched per plan guardrails but represent latent risks.

**Verdict**: ✅ SUBSTANTIALLY COMPLETE — Safe for use with documented caveats.

---

## Action Plan

1. Run `tests/generate_gif.py` and visually inspect, saving output as `.sisyphus/evidence/f3-visual-check.txt` — closes the only missing verification wave item.
2. Review `retracements()` extreme percentage issue (values -10500 to +6788.9) — evaluate if a floor/cap clamp in `retracements()` is warranted or if it's harmless edge-case output.
3. Document the ATR/sizing warmup period requirement (`max(swing_length, atr_period)` bars of NaN output) in the `swing_highs_lows()` docstring so users of the library understand the causal delay.

---

## 1. Plan Compliance

### Wave 1 — Foundation

| Task | Status | Evidence | Notes |
|------|--------|----------|-------|
| **T1. Build `_SwingEngine`** | ✅ COMPLETE | `smc.py` lines 60-219 | Inner class nested in `smc`; all state attributes present; alternation, mutability, and confirmation logic match spec |
| **T2. ATR calculation** | ✅ COMPLETE | `_compute_atr()` at `smc.py` lines 104-132 | Wilder's smoothing; returns 0.0 during warmup; switches to running EMA after `atr_period` bars |
| *T1 QA: basic-swing* | ⚠️ PARTIAL | Evidence file `t1-basic-swing.txt` **missing** | Plan requested `.sisyphus/evidence/t1-basic-swing.txt` — not present in evidence directory |
| *T1 QA: empty-data* | ⚠️ PARTIAL | Evidence file `t1-empty-data.txt` **missing** | Plan requested `.sisyphus/evidence/t1-empty-data.txt` — not present |
| *T2 QA: ATR consistency* | ⚠️ PARTIAL | Evidence file `t2-atr-consistency.txt` **missing** | Plan requested `.sisyphus/evidence/t2-atr-consistency.txt` — not present |

### Wave 2 — Integration

| Task | Status | Evidence | Notes |
|------|--------|----------|-------|
| **T3. Rewrite `swing_highs_lows()`** | ✅ COMPLETE | `smc.py` lines 302-379, `t3-shape-check.txt`, `t3-alternation.txt` | Shape (24424, 2), dtypes float64, perfect alternation (2415 swings), all 6 parameter validators present |
| **T4. Downstream compat** | ✅ COMPLETE | `t4-downstream-compat.txt` | All 4 methods (bos_choch, ob, liquidity, retracements) complete without errors; correct columns and shapes |

### Wave 3 — Downstream Validation

| Task | Status | Evidence | Notes |
|------|--------|----------|-------|
| **T5. bos_choch()** | ✅ COMPLETE | `t5-bos-choch.txt` | 546 BOS, 345 CHoCH; values are ±1 or NaN; valid ranges |
| **T6. ob()** | ✅ COMPLETE | `t6-ob.txt` | 61 OBs detected; Percentage all 100.0; OBVolume 0.0 (see §3 — dataset artifact) |
| **T7. liquidity()** | ✅ COMPLETE | `t7-liquidity.txt` | 501 zones detected; "All Swept > End: False" flagged in evidence (pre-existing condition) |
| **T8. retracements()** | ✅ COMPLETE | `t8-retracements.txt` | Direction 24409 non-zero; Deepest ≥ Current holds; extreme values documented (see §3) |

### Wave 4 — Test Infrastructure

| Task | Status | Evidence | Notes |
|------|--------|----------|-------|
| **T9. 3-pass causality** | ✅ COMPLETE | `test_causality.py`, `t9-causality-run.txt` | Batch == Streaming: PASS; 30 per-bar truncation checks: PASS |
| **T10. stream_compare.py** | ✅ COMPLETE | `stream_compare.py`, `t10-diagnostic.txt` | Diagnostic tool with argparse; reports 0 differences on EURUSD data |
| **T11. Golden CSVs** | ✅ COMPLETE | `t11-regenerated.txt`, `t11-spot-check.txt` | All 5 golden CSVs regenerated; spot check shows correct alternation; shapes verified |
| **T12. Full test suite** | ✅ COMPLETE | `t12-full-suite.txt` | 10/10 unit tests OK; causality and stream_compare both pass |
| **T13. Version bump** | ✅ COMPLETE | `t13-version.txt` | `smc.__version__` = "0.1.0" (line 58), `setup.py VERSION` = "0.1.0" (line 5) |

### Wave F — Final Verification

| Task | Status | Evidence | Notes |
|------|--------|----------|-------|
| **F1. Compliance audit** | ✅ COMPLETE | `f1-compliance-audit.txt` | All 12 checks PASS |
| **F2. Code quality review** | ✅ COMPLETE | `f2-code-quality.txt` | All 6 checks PASS |
| **F3. Visual QA (GIF)** | ❌ MISSING | No evidence file found; `ls .sisyphus/evidence/f3*` returns no matches | `tests/generate_gif.py` exists but was not executed. **This is the single missing F-wave task.** |
| **F4. Scope fidelity** | ✅ COMPLETE | `f4-scope-fidelity.txt` | 8 modified files confirmed; no changes to fvg/sessions/previous_high_low; monolithic intact |

---

## 2. Guardrail Verification

| Guardrail | Status | Evidence |
|-----------|--------|----------|
| No new production files outside `smc.py` | ✅ PASS | Only `tests/` files were created/updated; `f4-scope-fidelity.txt` confirms |
| No new external dependencies | ✅ PASS | Dependencies unchanged (`numpy`, `pandas`, `numba`); only `collections.deque` (stdlib) added |
| No changes to FVG, sessions, previous_high_low | ✅ PASS | `git diff --stat` shows those methods unmodified; `f4-scope-fidelity.txt` confirms |
| Monolithic structure preserved | ✅ PASS | `_SwingEngine` is an inner class inside `class smc` in `smc.py` (line 60) |
| No look-ahead bias | ✅ PASS | 3-pass causality test proves batch == streaming; 30 per-bar truncation checks prove no future data leakage |

---

## 3. Critical Analysis

### Gaps Between Plan Spec and Implementation

1. **Candidate discovery gating (T1 edge case)**: Plan states *"don't attempt candidate discovery until both `atr_period` and `swing_length` bars have been processed"* (line 225 of plan). Code gates on `swing_length` only (line 156: `if len(self._high_buffer) >= self._swing_length`). If `atr_period > swing_length`, candidates can be discovered before ATR is ready. **Mitigation**: Confirmation is still gated by `_atr_ready` (line 181), so no swing would be confirmed early. The trend direction could flip prematurely during this window, which is a minor behavioral deviation under extreme parameter configurations.

2. **QA evidence gaps (T1, T2)**: Three evidence files requested by the plan were not produced: `t1-basic-swing.txt`, `t1-empty-data.txt`, `t2-atr-consistency.txt`. The T2 QA scenario specifically asked for ATR values matching within 0.1% of a pandas-ta reference — this was never evidenced.

3. **F3 not executed**: The GIF visualization was specified as a Wave F deliverable with explicit acceptance criteria ("GIF generates without errors", "Visual inspection confirms plausible market structure"). Neither was done.

### Code Quality Issues

1. **Unused return value** (`smc.py` line 149): `atr = self._compute_atr(high, low, close)  # noqa: F841` — the return value is immediately discarded. The ATR is stored in `self._current_atr` internally, making the return value dead code. The `# noqa: F841` suppression is a signal this was known. Minor.

2. **Unused state attribute** (`smc.py` line 83): `self._last_confirmed_index = -1` is initialized but never read or updated anywhere in the implementation. Should either be used (to track which bar the last confirmed swing was stamped on) or removed.

3. **`_last_confirmed_direction` type** (`smc.py` line 82): Initialized to `0` but plan specifies it should be `int (1 or -1)`. While this doesn't cause runtime issues (it gets set on first confirmation), it's a minor type contract violation.

4. **ATR buffer inefficiency** (`smc.py` lines 94, 122-127): Uses a `list` for `_tr_buffer` rather than pre-allocating. For typical usage (`atr_period=14`) this is negligible, but it's an O(1) allocation pattern that could use `list.append()` pre-allocation.

### Test Coverage Gaps

1. **No parameter validation tests**: The plan specifies 6 `ValueError` guardrails in T3 acceptance criteria. The `unit_tests.py` suite contains zero tests that exercise these validations. If a parameter validation is accidentally weakened or removed, no test would catch it.

2. **Single-instrument test data**: All tests use only `EURUSD_15M.csv` (24,424 rows, 15-minute timeframe). No cross-instrument validation (e.g., crypto, stocks, different timeframes) exists. The causal engine's behavior on 1-minute or daily data is untested.

3. **No edge-case data scenarios**: No tests with gaps, missing timestamps, duplicate indices, extreme volatility, or very short datasets (beyond the 3-candle `test_ob_early_data`).

4. **Per-bar truncation samples only 30/2415 swings (1.2%)**: With a fixed seed (`random.Random(42)`), this is reproducible but provides limited statistical coverage. A 100% sweep would be more rigorous (though slower).

### Parameter Validation Completeness

All 6 validators from the plan are present (lines 333-359):
- `swing_length < 2` → ValueError ✅
- `confirmation_bars < 1` → ValueError ✅
- `atr_multiplier <= 0` → ValueError ✅
- `atr_period < 1` → ValueError ✅
- `swing_length + confirmation_bars > len(ohlc)` → ValueError ✅
- `atr_period > len(ohlc)` → ValueError ✅

### Edge Cases Not Handled (Documented Deviations)

1. **Non-contiguous data gaps**: Engine processes by integer index, not by timestamp. Gaps in time between consecutive rows are invisible to the ATR calculation, which assumes continuous bars. This could cause slightly distorted ATR values after weekend gaps or data dropouts.

2. **OBVolume all zeros in test data**: EURUSD test CSV has `Volume` column all zeros (int64, min=0, max=0, 24,424 entries). All 61 detected OBs have OBVolume=0.0 as a result. This is a test dataset limitation — production data with real volume would produce non-zero values.

---

## 4. Risk Assessment

### What Could Go Wrong in Production?

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Swing confirmation never triggers** for instruments with low ATR relative to tick size | Low | Medium | User tunes `atr_multiplier` per instrument. Document that this is expected. |
| **Excessive NaN warmup** on short lookback windows (e.g., 1-hour chart with swing_length=50 = 50 hours of NaN) | Medium | Medium | Ensure users understand the `max(swing_length, atr_period)` warmup window. Currently not documented in the method docstring. |
| **`retracements()` producing values outside 0-100** (evidence shows -10500 to +6788.9) causing downstream errors | High | Low | Pre-existing bug, not introduced by this change. Values only affect percentage-based calculations, not core market structure. |
| **`liquidity()` producing Swept <= End entries** confusing downstream consumers | Medium | Low | Pre-existing condition. The evidence flags "All Swept > End: False" but 501/501 are listed as "Valid End/Swept pairs", suggesting this was the baseline behavior. |
| **OBVolume formula may diverge** if production volume data has different scale/units than expected | Low | Medium | OBVolume = sum of three bars' volume. Tested only against zero-volume test data. |

### Conditions That Would Warrant Revisiting This Implementation

1. A user reports that swings are confirmed on obviously wrong bars (not at local peaks/troughs) for their specific instrument/timeframe.
2. Performance profiling shows the per-candle loop as a bottleneck for very large historical datasets (100k+ rows).
3. A new downstream indicator depends on swing-level metadata not currently exposed (e.g., ATR-at-confirmation, bars-since-last-swing).

### Hidden Assumptions

1. **Price buffer adequacy**: The last `swing_length` bars in the buffer are sufficient for candidate discovery. In strong monotonic trends, candidates will keep being replaced (each resetting `_candidate_bars_since` to 0), potentially delaying confirmation indefinitely until a pullback of sufficient magnitude occurs. This is by design but may surprise users expecting more frequent swing labels.
2. **EMU regime recency**: The averaged ATR assumes recent volatility is representative. A sudden volatility regime change will take `atr_period` bars to fully propagate through the EMA.
3. **Single-file maintainability**: As of v0.1.0, `smc.py` is 1,147 lines. The `_SwingEngine` inner class adds ~160 lines. This remains manageable but the trend is toward a file that would benefit from modularization at a future threshold (~2,000 lines).

---

## 5. Recommendations

### R1: Execute F3 — GIF Visualization (`Quick`, <1h)
Run `tests/generate_gif.py`, capture output, and save as `.sisyphus/evidence/f3-visual-check.txt`. This is the only missing Wave F deliverable. The GIF provides critical visual confirmation that swings, BOS/CHoCH, OBs, and liquidity zones are plotted at plausible price levels. Without this, there is no evidence that market structure looks correct to a human eye.

### R2: Document ATR Warmup and Causal Delay in Docstring (`Quick`, <1h)
Add a note to the `swing_highs_lows()` docstring (around line 311) explaining that the first `max(swing_length, atr_period)` bars return NaN due to the causal warmup period. Users migrating from the old centered-window engine may not expect this initial NaN window. Example text: *"Note: The first `max(swing_length, atr_period)` candles produce NaN output while the internal ATR and price buffers fill. This is expected and is the cost of zero look-ahead bias."*

### R3: Add Parameter Validation Unit Tests (`Short`, 1-4h)
Add dedicated test methods in `tests/unit_tests.py` for each of the 6 `ValueError` conditions in `swing_highs_lows()`:
- `swing_length < 2`
- `confirmation_bars < 1`
- `atr_multiplier <= 0`
- `atr_period < 1`
- `swing_length + confirmation_bars > len(ohlc)`
- `atr_period > len(ohlc)`

These are the only critical acceptance criteria from the plan that lack automated test coverage. A regression here could silently produce incorrect output instead of failing fast.

---

## Optional Future Consideration (not in scope)

The unused `_last_confirmed_index` attribute (smc.py line 83) and the dead `atr` return value (line 149) are minor code hygiene issues worth cleaning up in a future maintenance pass to keep the codebase clean.
