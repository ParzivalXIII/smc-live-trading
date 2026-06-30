# Fix: Split `score` into `direction_score` + `confidence` in Confluence/Decision Pipeline

## TL;DR
> **Quick Summary**: Replace the ambiguous single `score` field in `ConfluenceResult` with semantically distinct `direction_score` (raw HTF regime strength, -4 to 10) and `confidence` (LTF alignment quality, 0.0-1.0). Update `Decision`, `MarketNarrative`, and all tests accordingly. Action mapping shifts from score-thresholds to bias+confidence-gated logic.
> **Deliverables**: Updated `ConfluenceResult`, `Decision`, `MarketNarrative` dataclasses; new action mapping in `DecisionEngine`; updated `composite_score()` to expose raw direction + separate confidence; all 3 test files migrated (~50 assertion changes); full suite green.
> **Estimated Effort**: Medium (2-4 hours)
> **Parallel Execution**: YES — 5 waves (Wave 1→2→3 sequential, Wave 4 tests parallel after 1-3, Wave 5 verification)
> **Critical Path**: T1 (ConfluenceResult) → T2 (Decision) → T3 (Narrative) → T4 (Tests) → T5 (Verification)

## Context
### Original Request
Split the ambiguous `score` field in `ConfluenceResult` into `direction_score` (raw HTF regime strength, -4 to 10) and `confidence` (LTF alignment quality, 0.0-1.0). Currently, `score=-0.96` conflates "strong bearish regime" (direction=-6) with "poor LTF alignment" (confidence=16%), which is misleading on dashboards and in trading decisions. The Oracle review confirms the split is worth doing now.

### Interview Summary
- **Design confirmed**: `ConfluenceResult` gains `direction_score` and `confidence`; `score` removed entirely. No backward-compat property — full migration of all 50+ references.
- **Action mapping confirmed (progressive)**: Actions derive from HTF bias directly, with confidence modulating choice:
  - `bullish + confidence > 0.5` → `look_for_longs`
  - `bullish + confidence ≤ 0.5` → `stand_aside`
  - `bearish + confidence > 0.5` → `avoid_shorts`
  - `bearish + confidence ≤ 0.5` → `stand_aside`
  - `neutral` → always `stand_aside`
- **Breakout pending**: `True` when confidence between 0.3 and 0.7 AND bias != neutral
- **Single-TF confidence**: Always `1.0` (no LTFs to disagree — more honest than old `abs(score)/max_score`)
- **Legacy `composite_score()`**: Keeps additive sum, stores in `direction_score`, sets `confidence=1.0`
- **`slots=True` removed**: From `ConfluenceResult` — needed since we're adding a new field (no compat property needed)
- **No changes to**: `market_snapshot.py`, `ConfluenceScorer.score()` semantics, `_alignment_factor()`

### Metis Review
*(To be filled after Metis consultation — see Post-Plan actions)*

## Work Objectives
### Core Objective
Eliminate the semantic conflation of directional strength and execution quality in `ConfluenceResult.score` by splitting into `direction_score` and `confidence`.

### Concrete Deliverables
1. Updated `ConfluenceResult` dataclass with `direction_score` + `confidence` fields
2. Updated `MarketContext.composite_score()` returning raw HTF score + separate confidence
3. Updated `Decision` dataclass with `direction_score` field
4. Updated `DecisionEngine.decide()` with bias+confidence-gated action mapping
5. Updated `MarketNarrative` dataclass with `direction_score` + `confidence`
6. All test assertions migrated (3 test files, ~50 assertion changes)
7. Full test suite passing (0 failures)

### Definition of Done
- [ ] T1 complete: `ConfluenceResult` has `direction_score` + `confidence`, all internal refs updated
- [ ] T2 complete: `Decision` has `direction_score`, action mapping uses bias+confidence, breakout uses confidence range
- [ ] T3 complete: `MarketNarrative` exposes `direction_score` + `confidence`
- [ ] T4 complete: All test assertions migrated, no failing tests
- [ ] T5 complete: Full suite passes 0 failures
- [ ] No changes to `market_snapshot.py`, `_alignment_factor()`, `ConfluenceScorer.score()` semantics

### Must Have
- [ ] `ConfluenceResult.direction_score` = raw HTF score (not multiplied by confidence)
- [ ] `ConfluenceResult.confidence` = product of LTF alignment factors (clamped 0.0-1.0)
- [ ] Single-TF always has `confidence=1.0`
- [ ] Action from bias+confidence per confirmed design
- [ ] Breakout from `0.3 <= confidence <= 0.7 AND bias != neutral`
- [ ] `legacy_composite_score()` stores additive sum in `direction_score`, sets `confidence=1.0`

### Must NOT Have (Guardrails)
- ❌ No changes to `market_snapshot.py`
- ❌ No changes to `_alignment_factor()` factor values (1.0/0.7/0.4)
- ❌ No changes to `ConfluenceScorer.score()` additive scoring logic
- ❌ No backward-compat `score` property on `ConfluenceResult`
- ❌ No changes to `MarketContext.alignment()` or `regime_alignment`
- ❌ No behavioral changes to `ConfluenceScorer` single-TF scoring

## Verification Strategy
- **Test decision**: Tests-after for migration (mechanical refactor with verified semantics).
- **QA policy**: All verification is agent-executed. Zero human intervention.
- **Scope**: T1 verified by T4 test migration. T5 runs full suite for regression.

## Execution Strategy
### Parallel Execution Waves

```
Wave 1 [T1]    → ConfluenceResult + composite_score()
Wave 2 [T2]    → Decision + DecisionEngine  (depends on T1)
Wave 3 [T3]    → MarketNarrativeBuilder      (depends on T1)
Wave 4 [T4a-d] → Update test assertions      (depends on T1-3, parallel per file)
Wave 5 [T5]    → Run full suite              (depends on T4)
```

### Dependency Matrix
| Task | Depends On | Blocks |
|------|-----------|--------|
| T1. Update `ConfluenceResult` + `composite_score()` | — | T2, T3, T4 |
| T2. Update `Decision` + `DecisionEngine` | T1 | T4 |
| T3. Update `MarketNarrativeBuilder` | T1 | T4 |
| T4a. Update `test_market_snapshot.py` | T1 | T5 |
| T4b. Update `test_decision_engine.py` | T1, T2 | T5 |
| T4c. Update `test_narrative.py` | T1, T3 | T5 |
| T5. Run full suite | T4a, T4b, T4c | — |

### Agent Dispatch Summary
| Task | Profile | Skills |
|------|---------|--------|
| T1 | python | python-type-safety, python-design-patterns |
| T2 | python | python-design-patterns |
| T3 | python | — |
| T4a-d | python | — |
| T5 | python | — |

## TODOs

### Wave 1 — Core Data Model

- [ ] T1. Update `ConfluenceResult` and `composite_score()` in `confluence.py`

  **What to do**: Apply the following edits to `/home/parzivalxiii/Projects/smc-live-trading/confluence.py`:

  1. **Update import and remove `slots=True`** (lines 17, 22):
     - Line 17: Change `from dataclasses import dataclass` → `from dataclasses import dataclass, field`
     - Line 22: Change `@dataclass(slots=True)` → `@dataclass`

  2. **Replace fields** (lines 33-36):
     ```python
     bias: str
     direction_score: float
     confidence: float
     max_score: int | float = 10
     reasons: list[str] = field(default_factory=list)
     ```
     (Remove `score: float`, add `direction_score` + `confidence`; swap order so confidence comes before max_score; add `field(default_factory=list)` for reasons since `max_score` now has a default value.)

  3. **Update `ConfluenceScorer.score()` return** (line 150):
     ```python
     return ConfluenceResult(bias=bias, direction_score=score, confidence=1.0, max_score=max_score, reasons=reasons)
     ```

  4. **Update `legacy_composite_score()`** — 4 changes:
     - Line 219: `ConfluenceResult("neutral", 0, 0, ["No data"])` → `ConfluenceResult("neutral", 0, 1.0, 0, ["No data"])`
     - Line 229: `total_score += result.score` → `total_score += result.direction_score`
     - Line 231: `reasons.append(f"{name}: {result.bias} (score={result.score})")` → `reasons.append(f"{name}: {result.bias} (score={result.direction_score})")`
     - Line 247: `ConfluenceResult(bias=bias, score=total_score, max_score=max_score, reasons=reasons)` → `ConfluenceResult(bias=bias, direction_score=total_score, confidence=1.0, max_score=max_score, reasons=reasons)`

  5. **Update `composite_score()`** — 3 changes:
     - Line 278: `ConfluenceResult("neutral", 0.0, 0, ["No data available"])` → `ConfluenceResult("neutral", 0.0, 1.0, 0, ["No data available"])`
     - Line 286: `htf_score = htf_result.score` → `htf_score = htf_result.direction_score`
     - Lines 302-308: Replace:
       ```python
       final_score = round(htf_score * confidence, 2)
       return ConfluenceResult(
           bias=base_bias,
           score=final_score,
           max_score=10,
           reasons=reasons,
       )
       ```
       With:
       ```python
       return ConfluenceResult(
           bias=base_bias,
           direction_score=htf_score,
           confidence=round(confidence, 2),
           max_score=10,
           reasons=reasons,
       )
       ```

  **Must NOT do**:
  - ❌ Do NOT change `_alignment_factor()` logic
  - ❌ Do NOT change `ConfluenceScorer.score()` scoring conditions
  - ❌ Do NOT change `alignment()` or `regime_alignment` properties
  - ❌ Do NOT add a backward-compat `@property score`

  **Recommended Agent Profile**: python — type safety + dataclass refactoring

  **Parallelization**: Wave 1, blocks: nothing, blocked-by: nothing

  **References**:
  - Oracle review: `.sisyphus/evidence/oracle-direction-confidence-split.md` lines 46-56 (dataclass design), 88-99 (composite_score changes)
  - Current `ConfluenceResult`: `confluence.py` lines 22-36
  - Current `composite_score()`: `confluence.py` lines 267-308

  **Acceptance Criteria**:
  - [ ] `python -c "from confluence import ConfluenceResult; r = ConfluenceResult('bullish', 10, 1.0, 10, []); print(r.direction_score, r.confidence)"` succeeds
  - [ ] `python -c "from confluence import ConfluenceResult; r = ConfluenceResult('bullish', 10, 1.0, 10, []); assert r.direction_score == 10; assert r.confidence == 1.0"`
  - [ ] No `__slots__` attribute on `ConfluenceResult`: `python -c "from confluence import ConfluenceResult; assert not hasattr(ConfluenceResult, '__slots__')"`

  **QA Scenarios**:
  - **Scenario 1 — Single-TF scorer returns confidence=1.0**:
    - **Tool**: interactive_bash
    - **Preconditions**: T1 changes applied
    - **Steps**: `python -c "from confluence import ConfluenceScorer; from tests.conftest import sample_snapshot; s = sample_snapshot(); r = ConfluenceScorer().score(s); print(f'dir_score={r.direction_score}, conf={r.confidence}')"`
    - **Expected**: `direction_score=10, confidence=1.0`
    - **Evidence**: `.sisyphus/evidence/task-T1-single-tf-conf.txt`

  - **Scenario 2 — MTF composite returns direction_score + confidence separately**:
    - **Tool**: interactive_bash
    - **Preconditions**: T1 changes applied, conftest fixtures available
    - **Steps**: `python -c "from tests.conftest import mtx_conflicting_both; ctx = mtx_conflicting_both(); r = ctx.composite_score(); print(f'dir_score={r.direction_score}, conf={r.confidence}'); assert r.direction_score == 10; assert r.confidence == 0.28"`
    - **Expected**: `direction_score=10.0, confidence=0.28`
    - **Evidence**: `.sisyphus/evidence/task-T1-mtf-conf.txt`

  - **Scenario 3 — Legacy composite still works**:
    - **Tool**: interactive_bash
    - **Preconditions**: T1 changes applied, conftest fixtures available
    - **Steps**: `python -c "from tests.conftest import sample_snapshot; from confluence import MarketContext; ctx = MarketContext(daily=sample_snapshot(), h4=sample_snapshot()); r = ctx.legacy_composite_score(); print(f'dir_score={r.direction_score}, conf={r.confidence}'); assert r.direction_score == 20; assert r.confidence == 1.0"`
    - **Expected**: `direction_score=20, confidence=1.0`
    - **Evidence**: `.sisyphus/evidence/task-T1-legacy-conf.txt`

### Wave 2 — Decision Engine

- [ ] T2. Update `Decision` dataclass and `DecisionEngine.decide()` in `decision_engine.py`

  **What to do**: Apply the following edits to `/home/parzivalxiii/Projects/smc-live-trading/decision_engine.py`:

  1. **Add `direction_score` to `Decision`** (after line 32):
     ```python
     bias: str
     direction_score: float
     confidence: float
     action: str
     invalidation: float | None = None
     target: float | None = None
     breakout_pending: bool = False
     breakout_level: float | None = None
     ```
     Also update docstring (lines 22-29) to document `direction_score`.

  2. **Replace confidence formula** (line 88):
     ```python
     # Old: confidence = min(1.0, abs(result.score) / result.max_score)
     # New:
     direction_score = result.direction_score
     confidence = result.confidence  # direct pass-through from ConfluenceResult
     ```

  3. **Replace action mapping** (lines 90-111):
     ```python
     # --- Action mapping (bias + confidence gated) ---
     action: str
     breakout_pending = False
     breakout_level: float | None = None

     if bias == "bullish":
         action = "look_for_longs" if confidence > 0.5 else "stand_aside"
     elif bias == "bearish":
         action = "avoid_shorts" if confidence > 0.5 else "stand_aside"
     else:  # neutral
         action = "stand_aside"

     # Breakout pending: uncertain confidence (0.3-0.7) + directional bias
     if bias != "neutral" and 0.3 <= confidence <= 0.7:
         # Check if swing level is within 1 ATR
         if snapshot.last_swing_level is not None and not self._is_nan_or_none(snapshot.atr14):
             if not (self._is_nan_or_none(snapshot.close) or self._is_nan_or_none(snapshot.last_swing_level)):
                 distance = abs(snapshot.last_swing_level - snapshot.close)
                 if distance <= snapshot.atr14:
                     breakout_pending = True
                     breakout_level = snapshot.last_swing_level
     ```

  4. **Update `Decision(...)` constructor** (lines 119-127):
     ```python
     return Decision(
         bias=bias,
         direction_score=direction_score,
         confidence=confidence,
         action=action,
         invalidation=invalidation,
         target=target,
         breakout_pending=breakout_pending,
         breakout_level=breakout_level,
     )
     ```

  5. **Update docstring** for `DecisionEngine` (lines 49-56) to reflect new action mapping:
     ```
     The decision logic follows:
     - **Action mapping**: HTF bias + confidence threshold combine to produce
       an action. Bullish/bearish bias with confidence > 0.5 produces a
       directional action; confidence ≤ 0.5 downgrades to stand_aside.
       Neutral bias always produces stand_aside.
     - **Confidence**: Passed through directly from ``ConfluenceResult.confidence``
       (LTF alignment quality, 0.0-1.0).
     - **Invalidation/target**: Unchanged (liquidity-first, SMC-aligned).
     - **breakout_pending**: Modifier flag for confidence 0.3-0.7 near a swing level.
     ```

  **Must NOT do**:
  - ❌ Do NOT change invalidation or target resolution logic
  - ❌ Do NOT change `_is_nan_or_none` or `_is_valid_price` helpers

  **Recommended Agent Profile**: python — decision logic with clear control flow

  **Parallelization**: Wave 2, blocks: nothing, blocked-by: T1

  **References**:
  - User-confirmed action mapping (see TL;DR above)
  - Current `Decision`: `decision_engine.py` lines 18-38
  - Current `decide()`: `decision_engine.py` lines 58-127

  **Acceptance Criteria**:
  - [ ] `python -c "from decision_engine import Decision; d = Decision('bullish', 10.0, 1.0, 'look_for_longs'); print(d.direction_score)"` succeeds
  - [ ] `python -c "from decision_engine import DecisionEngine; print(hasattr(DecisionEngine, 'decide'))"`

  **QA Scenarios**:
  - **Scenario 1 — Bullish + high confidence → look_for_longs**:
    - **Tool**: interactive_bash
    - **Steps**: `python -c "from confluence import ConfluenceResult; from decision_engine import DecisionEngine; from tests.conftest import sample_snapshot; r = ConfluenceResult('bullish', 10, 1.0, 10, []); d = DecisionEngine().decide(sample_snapshot(), r); print(f'action={d.action}, conf={d.confidence}'); assert d.action == 'look_for_longs'"`
    - **Expected**: action=look_for_longs
    - **Evidence**: `.sisyphus/evidence/task-T2-bullish-high-conf.txt`

  - **Scenario 2 — Bearish + low confidence → stand_aside**:
    - **Tool**: interactive_bash
    - **Steps**: `python -c "from confluence import ConfluenceResult; from decision_engine import DecisionEngine; from tests.conftest import sample_snapshot; r = ConfluenceResult('bearish', -6, 0.16, 10, []); d = DecisionEngine().decide(sample_snapshot(), r); print(f'action={d.action}, conf={d.confidence}'); assert d.action == 'stand_aside'"`
    - **Expected**: action=stand_aside
    - **Evidence**: `.sisyphus/evidence/task-T2-bearish-low-conf.txt`

  - **Scenario 3 — Breakout pending with confidence in 0.3-0.7 range**:
    - **Tool**: interactive_bash
    - **Steps**: use a snapshot with swing level within 1 ATR, ConfluenceResult with bias=non-neutral, confidence=0.5, then assert breakout_pending=True
    - **Evidence**: `.sisyphus/evidence/task-T2-breakout.txt`

### Wave 3 — Narrative

- [ ] T3. Update `MarketNarrative` and builder in `narrative.py`

  **What to do**: Apply the following edits to `/home/parzivalxiii/Projects/smc-live-trading/narrative.py`:

  1. **Update `MarketNarrative` dataclass** (lines 33-53):
     Replace `score: int | float` with `direction_score: int | float` and add `confidence: float`:
     ```python
     @dataclass
     class MarketNarrative:
         symbol: str
         timeframe: str
         bias: str
         direction_score: int | float
         confidence: float
         max_score: int | float
         sections: list[NarrativeSection]
         conclusion: str
     ```
     Update docstring to document `direction_score` and `confidence`.

  2. **Update `build()` return** (lines 95-103):
     ```python
     return MarketNarrative(
         symbol=snapshot.symbol,
         timeframe=snapshot.timeframe,
         bias=result.bias,
         direction_score=result.direction_score,
         confidence=result.confidence,
         max_score=result.max_score,
         sections=sections,
         conclusion=conclusion,
     )
     ```

  3. **Update `build()` docstring** (line 82): Change `bias, score, reasons` → `bias, direction_score, confidence, reasons`

  **Must NOT do**:
  - ❌ Do NOT change section builders (`_build_trend`, `_build_momentum`, etc.)
  - ❌ Do NOT change `_build_conclusion` logic

  **Recommended Agent Profile**: python

  **Parallelization**: Wave 3, blocks: nothing, blocked-by: T1

  **References**:
  - Current `MarketNarrative`: `narrative.py` lines 33-53
  - Current `build()`: `narrative.py` lines 70-103

  **Acceptance Criteria**:
  - [ ] `python -c "from narrative import MarketNarrative; n = MarketNarrative('X', '1d', 'bullish', 10, 1.0, 10, [], 'test'); print(n.direction_score, n.confidence); assert n.direction_score == 10; assert n.confidence == 1.0"`
  - [ ] `python -c "from narrative import MarketNarrative; n = MarketNarrative('X', '1d', 'bullish', 10, 1.0, 10, [], 'test'); assert not hasattr(n, 'score')"`

  **QA Scenarios**:
  - **Scenario 1 — Narrative passes direction_score and confidence**:
    - **Tool**: interactive_bash
    - **Preconditions**: T1 + T3 changes applied
    - **Steps**: `python -c "from narrative import MarketNarrativeBuilder; from confluence import ConfluenceResult; from tests.conftest import sample_snapshot; b = MarketNarrativeBuilder(); r = ConfluenceResult('bullish', 10, 1.0, 10, []); n = b.build(sample_snapshot(), r); print(f'dir_score={n.direction_score}, conf={n.confidence}'); assert n.direction_score == 10; assert n.confidence == 1.0"`
    - **Expected**: direction_score=10, confidence=1.0
    - **Evidence**: `.sisyphus/evidence/task-T3-narrative-pass.txt`

### Wave 4 — Test Migration

- [ ] T4a. Update `test_market_snapshot.py` assertions

  **What to do**: Apply mechanical find-and-replace and constructor updates to `/home/parzivalxiii/Projects/smc-live-trading/tests/test_market_snapshot.py`

  **Detailed changes**:

  1. **TestConfluenceResult class** (lines 341-368):
     - `test_construct_minimal`: 
       ```python
       r = ConfluenceResult("bullish", 7, 1.0, 10, ["reason1"])
       assert r.direction_score == 7
       assert r.confidence == pytest.approx(1.0)
       ```
     - `test_all_attributes`:
       ```python
       r = ConfluenceResult("bearish", -2, 1.0, 10, ["a", "b"])
       assert r.direction_score == -2
       assert r.confidence == pytest.approx(1.0)
       ```
     - `test_empty_reasons`:
       ```python
       r = ConfluenceResult("neutral", 0, 1.0, 0, [])
       ```
     - `test_slots`: **Delete this test entirely** — `ConfluenceResult` no longer uses `slots=True`.

  2. **TestConfluenceScorer class** (lines 375-641):
     - All `result.score == X` → `result.direction_score == X` (mechanical replace)
     - All `result.score` in docstrings/f-strings → `result.direction_score`
     - Affected methods: `test_scorer_returns_confluenceresult`, `test_all_bullish_score_10`, `test_all_bearish_score_neg4`, `test_isolated_*` (8 tests), `test_boundary_*` (3 tests), `test_none_handling`, `test_both_liquidity_net_zero`

  3. **TestMarketContext class** (lines 648-744):
     - `test_composite_score_two_active`: 
       ```python
       assert result.direction_score == 20
       assert result.confidence == pytest.approx(1.0)
       ```
     - `test_composite_score_three_active`:
       ```python
       assert result.direction_score == 30
       assert result.confidence == pytest.approx(1.0)
       ```
     - `test_composite_score_zero_active`:
       ```python
       assert result.direction_score == 0
       ```

  4. **TestIntegration class** (lines 751-833):
     - `test_real_ta_csv_with_scoring`: `result.score` → `result.direction_score`

  5. **TestHierarchicalMTF class** (lines 841-907):
     - `test_all_aligned`:
       ```python
       assert result.direction_score == pytest.approx(10.0, abs=0.01)
       assert result.confidence == pytest.approx(0.7, abs=0.01)
       ```
     - `test_one_conflicting_ltf`:
       ```python
       assert result.direction_score == pytest.approx(10.0, abs=0.01)
       assert result.confidence == pytest.approx(0.4, abs=0.01)
       ```
     - `test_both_conflicting`:
       ```python
       assert result.direction_score == pytest.approx(10.0, abs=0.01)
       assert result.confidence == pytest.approx(0.28, abs=0.01)
       ```
     - `test_no_ltfs_single_tf`:
       ```python
       assert result.direction_score == pytest.approx(10.0, abs=0.01)
       assert result.confidence == pytest.approx(1.0, abs=0.01)
       ```
     - `test_no_daily`:
       ```python
       assert result.direction_score == pytest.approx(10.0, abs=0.01)
       assert result.confidence == pytest.approx(0.7, abs=0.01)
       ```
     - `test_neutral_htf_with_conflicting_ltf`:
       ```python
       assert result.direction_score == pytest.approx(5.0, abs=0.01)
       assert result.confidence == pytest.approx(0.4, abs=0.01)
       ```

  **Must NOT do**:
  - ❌ Do NOT delete or modify MTF test fixtures in `conftest.py`
  - ❌ Do NOT modify `TestMarketSnapshot` or `TestSnapshotBuilder` classes (they don't reference `ConfluenceResult`)

  **Recommended Agent Profile**: python — mechanical test migration

  **Parallelization**: Wave 4 (can run in parallel with T4b, T4c), blocked-by: T1

  **References**: Current test file at `tests/test_market_snapshot.py` (907 lines)

  **Acceptance Criteria**:
  - [ ] `python -m pytest tests/test_market_snapshot.py -v --tb=short -x` passes (after T1 + T4a applied)

  **QA Scenarios**:
  - **Scenario 1 — Full MTF test suite passes**:
    - **Tool**: interactive_bash
    - **Preconditions**: T1 + T4a applied
    - **Steps**: `python -m pytest tests/test_market_snapshot.py::TestHierarchicalMTF -v --tb=short`
    - **Expected**: All 6 MTF tests pass
    - **Evidence**: `.sisyphus/evidence/task-T4a-mtf-tests.txt`

- [ ] T4b. Update `test_decision_engine.py` assertions

  **What to do**: Apply changes to `/home/parzivalxiii/Projects/smc-live-trading/tests/test_decision_engine.py`

  **Detailed changes**:

  1. **All `ConfluenceResult(...)` constructors**: Add `1.0` as confidence parameter (3rd positional arg). Affected: lines 37, 51, 65, 94, 127, 156, 186, 214, 228, 256, 284, 298, 342, 370, 399, 421, 441, 461, 481, 502, 554.

  2. **`test_look_for_longs`** (lines 30-42): Constructor change only. Assertions unchanged (confidence=1.0 still matches).

  3. **`test_avoid_shorts`** (lines 44-56): 
     - Change expected confidence from `0.4` to `1.0`:
       ```python
       assert decision.confidence == pytest.approx(1.0)
       ```

  4. **`test_confidence_formula`** (lines 263-287): **Delete this parametrized test entirely** and replace with:
     ```python
     def test_confidence_passthrough(self) -> None:
         """Confidence passes through directly from ConfluenceResult."""
         from decision_engine import DecisionEngine
         snap = MarketSnapshot(
             symbol="X", timeframe="1d", timestamp=pd.Timestamp.now(),
             close=100.0, trend_direction="above",
             ema21=90.0, ema21_slope=0.0, rsi14=50.0, mfi14=50.0,
             macd=0.0, macd_signal=0.0, macd_hist=0.0,
             atr14=0.0, bb_width=0.0,
         )
         engine = DecisionEngine()
         result = ConfluenceResult("bullish", 10, 0.75, 10, ["test"])
         decision = engine.decide(snap, result)
         assert decision.confidence == pytest.approx(0.75)
         assert decision.direction_score == pytest.approx(10.0)
     ```

  5. **`test_decision_score_zero`** (lines 358-374):
     - Change expected action from `stand_aside` to `avoid_shorts`:
       ```python
       result = ConfluenceResult("bearish", 0, 1.0, 10, ["Zero score"])
       ...
       assert decision.action == "avoid_shorts"
       assert decision.confidence == pytest.approx(1.0)
       ```

  6. **`test_decision_score_threshold_boundaries`** (lines 376-404):
     - Update parametrized values to reflect new bias+confidence action mapping:
       ```python
       @pytest.mark.parametrize("score_val,expected_action,expected_bias", [
           (-1, "avoid_shorts", "bearish"),
           (0, "avoid_shorts", "bearish"),     # was stand_aside
           (3, "avoid_shorts", "bearish"),     # was stand_aside
           (4, "stand_aside", "neutral"),
           (6, "stand_aside", "neutral"),
           (7, "look_for_longs", "bullish"),
           (10, "look_for_longs", "bullish"),
       ])
       ```
     - Constructor must add confidence=1.0 for single-TF case.

  7. **`test_watch_breakout_modifier`** (lines 71-99):
     - This test uses `ConfluenceResult("neutral", 5, 10, ...)` with bias=neutral.
     - With new rules: neutral bias → always `stand_aside`, breakout_pending depends on `bias != neutral` condition. Since bias=neutral, breakout_pending=False.
     - **Updated test**:
       ```python
       def test_watch_breakout_modifier(self) -> None:
           """Confidence=0.5, bias=bullish, swing near → breakout_pending=True."""
           from decision_engine import DecisionEngine
           snap = MarketSnapshot(
               symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
               close=50000.0, trend_direction="above",
               ema21=49000.0, ema21_slope=0.01,
               rsi14=55.0, mfi14=52.0,
               macd=100.0, macd_signal=90.0, macd_hist=10.0,
               atr14=500.0, bb_width=0.05,
               last_swing_direction=1, last_swing_level=50500.0,
           )
           engine = DecisionEngine()
           result = ConfluenceResult("bullish", 10, 0.5, 10, ["Uncertain conviction"])
           decision = engine.decide(snap, result)
           assert decision.action == "stand_aside"  # confidence=0.5 ≤ 0.5
           assert decision.breakout_pending is True
           assert decision.breakout_level == 50500.0
       ```

  8. **`test_decision_breakout_pending`** (lines 467-486):
     - Uses `ConfluenceResult("neutral", 5, 10, ...)` with bias=neutral. Same issue.
     - **Rewrite to use a non-neutral bias**:
       ```python
       def test_decision_breakout_pending(self) -> None:
           """Confidence=0.5, bullish bias, swing within 1 ATR → breakout_pending=True."""
           ...
           result = ConfluenceResult("bullish", 10, 0.5, 10, ["Uncertain"])
           ...
           assert decision.action == "stand_aside"
           assert decision.breakout_pending is True
           assert decision.breakout_level == 50400.0
       ```

  9. **`test_decision_no_breakout_pending`** (lines 488-507):
     - Uses `ConfluenceResult("neutral", 5, 10, ...)`. With new rules, bias=neutral → breakout_pending=False which still matches assertion. But the confidence=0.5 would be in the breakout range if bias weren't neutral.
     - **Rewrite** to use bullish bias with confidence outside 0.3-0.7 range:
       ```python
       def test_decision_no_breakout_pending(self) -> None:
           """Confidence=0.8, bullish bias → no breakout (conf outside 0.3-0.7 range)."""
           ...
           result = ConfluenceResult("bullish", 10, 0.8, 10, ["High confidence"])
           ...
           assert decision.breakout_pending is False
           assert decision.breakout_level is None
       ```

  10. **`test_decision_all_optional_none`** (lines 537-559):
      - Constructs `ConfluenceResult("bearish", 0, 10, ["All none"])` → with split: `ConfluenceResult("bearish", 0, 1.0, 10, ["All none"])`
      - This gives `bias=bearish, confidence=1.0` → action=`avoid_shorts` (was `stand_aside`):
        ```python
        result = ConfluenceResult("bearish", 0, 1.0, 10, ["All none"])
        ...
        assert decision.action == "avoid_shorts"
        assert decision.confidence == pytest.approx(1.0)
        ```

   **Must NOT do**:
   - ❌ Do NOT modify invalidation/target test assertions (they remain unchanged)
   - ❌ Do NOT remove `test_with_market_context` or `test_without_market_context`

  **Recommended Agent Profile**: python — test assertion migration

  **Parallelization**: Wave 4, blocked-by: T1, T2

  **References**: Current test file at `tests/test_decision_engine.py` (559 lines)

  **QA Scenarios**:
  - **Scenario 1 — Decision engine tests pass**:
    - **Tool**: interactive_bash
    - **Preconditions**: T1 + T2 + T4b applied
    - **Steps**: `python -m pytest tests/test_decision_engine.py -v --tb=short -x`
    - **Expected**: All tests pass
    - **Evidence**: `.sisyphus/evidence/task-T4b-de-tests.txt`

- [ ] T4c. Update `test_narrative.py` assertions

  **What to do**: Apply changes to `/home/parzivalxiii/Projects/smc-live-trading/tests/test_narrative.py`

  **Detailed changes**:

  1. **All `ConfluenceResult(...)` constructors**: Add `1.0` as confidence parameter. Affected: lines 35, 80, 108, 141, 172, 187, 204, 229, 235, 262, 283, 302, 321, 340, 344, 362, 380.

  2. **All `narrative.score == X` assertions** (3 occurrences):
     - Line 39: `assert narrative.score == 10` → `assert narrative.direction_score == 10`
     - Line 84: `assert narrative.score == -4` → `assert narrative.direction_score == -4`
     - Line 325: `assert narrative.score == 0` → `assert narrative.direction_score == 0`

  3. **Add confidence assertion** to `test_bullish_narrative_all_sections` and `test_bearish_narrative`:
     - Add `assert narrative.confidence == pytest.approx(1.0)`

  **Must NOT do**:
  - ❌ Do NOT change section builders or conclusion text assertions
  - ❌ Do NOT modify `TestIntegrationPipeline` class (uses `scorer.score()` which is a method call, not a field access)

  **Recommended Agent Profile**: python — mechanical test migration

  **Parallelization**: Wave 4, blocked-by: T1, T3

  **References**: Current test file at `tests/test_narrative.py` (554 lines)

  **QA Scenarios**:
  - **Scenario 1 — Narrative tests pass**:
    - **Tool**: interactive_bash
    - **Preconditions**: T1 + T3 + T4c applied
    - **Steps**: `python -m pytest tests/test_narrative.py -v --tb=short -x`
    - **Expected**: All tests pass
    - **Evidence**: `.sisyphus/evidence/task-T4c-narrative-tests.txt`

### Wave 5 — Verification

- [ ] T5. Run full test suite and verify 0 failures

  **What to do**:
  1. Run `python -m pytest tests/ -v --tb=short`
  2. Confirm 0 failed, 0 errors
  3. If failures exist, diagnose and fix (likely assertion value mismatches in migrated tests)

  **Must NOT do**:
  - ❌ Do NOT skip failures — all 3 test files must pass completely

  **Recommended Agent Profile**: unspecified-high — final verification

  **Parallelization**: Wave 5, blocked-by: T4a, T4b, T4c

  **Acceptance Criteria**:
  - [ ] `python -m pytest tests/ -v --tb=short` exits with code 0
  - [ ] No warnings about deprecated field access (no trace of `result.score` or `narrative.score` in output)

  **QA Scenarios**:
  - **Scenario 1 — Full suite green**:
    - **Tool**: interactive_bash
    - **Preconditions**: All T1-T4c applied
    - **Steps**: `python -m pytest tests/ -v --tb=short 2>&1`
    - **Expected**: Exit code 0, all tests pass
    - **Evidence**: `.sisyphus/evidence/task-T5-full-suite.txt`

## Final Verification Wave

**F1. Plan Compliance Audit (oracle)**
- Verify all 5 deliverables exist and are correct
- Verify `market_snapshot.py` has zero changes
- Verify `_alignment_factor()` is untouched
- Verify `score` field is completely removed (not even as a property)

**F2. Data Model Integrity (deep)**
- `ConfluenceResult` has `direction_score`, `confidence`, no `score`, no `__slots__`
- `Decision` has `direction_score`, `confidence`, action maps correctly
- `MarketNarrative` has `direction_score`, `confidence`, no `score`

**F3. Semantic Correctness Verification (unspecified-high)**
- Run a script that constructs a known MTF scenario and verifies the split:
  - Bearish(-6) + H4 conflicts + H1 neutral → direction_score=-6, confidence=0.16
  - Action: bearish + 0.16 ≤ 0.5 → `stand_aside`
  - Decision.direction_score == -6, Decision.confidence == 0.16

**F4. Scope Fidelity Check (deep)**
- `grep -rn '\.score' --include='*.py' . | grep -v '__pycache__' | grep -v 'scorer\.score'` → only method calls to `scorer.score()` remain, no field accesses

## Commit Strategy
- Single commit with message:
  ```
  fix: split score into direction_score + confidence in confluence/decision pipeline
  
  - ConfluenceResult: removed score, added direction_score + confidence
  - composite_score(): returns raw HTF direction + separate LTF confidence
  - Decision: added direction_score, action from bias+confidence gates
  - MarketNarrative: added direction_score + confidence field
  - All tests migrated (~50 assertion changes)
  - No changes to market_snapshot.py, _alignment_factor(), scorer logic
  ```

## Success Criteria
- [ ] All 5 waves complete (T1 → T2 → T3 → T4a,b,c → T5)
- [ ] Full test suite passes with 0 failures
- [ ] `grep` for `.score` field access (not method call) returns zero results
- [ ] `ConfluenceResult` has exactly `bias, direction_score, confidence, max_score, reasons` fields
- [ ] Decision action mapping matches user-confirmed table
- [ ] All evidence artifacts saved to `.sisyphus/evidence/`
