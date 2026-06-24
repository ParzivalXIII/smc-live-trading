# Narrative Generation, Decision Engine & Hierarchical MTF Weighting

## TL;DR
> **Quick Summary**: Build three additive features on top of the existing MarketSnapshot + ConfluenceScorer system — a narrative layer that produces structured market summaries, a decision engine that maps scores to actionable decisions with liquidity-based invalidation/target levels, and a hierarchical multi-timeframe weighting approach where the daily sets the regime and lower timeframes only adjust confidence.
> **Deliverables**: `narrative.py`, `decision_engine.py`, updates to `confluence.py` (MarketContext), `tests/test_narrative.py`, `tests/test_decision_engine.py`, conftest updates
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 6 waves (sequential between waves, parallel within Wave 4)
> **Critical Path**: T1 (Narrative) → T2 (Decision) → T3 (MTF) → T4/T5 (Tests) → T6 (Integration)

---

## Context

### Original Request
Build three features extending the existing MarketSnapshot → ConfluenceScorer pipeline:
1. **Narrative Generation** (`narrative.py`): Produce structured market summaries from `MarketSnapshot` + `ConfluenceResult`
2. **Decision Engine** (`decision_engine.py`): Map confluence scores to actionable decisions with confidence, invalidation, and target levels
3. **Hierarchical MTF Weighting** (`confluence.py` update): Rework `MarketContext.composite_score()` so the highest timeframe (daily) sets the bias unconditionally, with lower TFs only adjusting confidence

### Interview Summary
All three design questions (resolved by user before plan generation):
- **Q1 (Narrative format)**: Structured dataclass with `NarrativeSection` and `MarketNarrative`. Sections for trend, momentum, structure, liquidity. Conclusion references invalidation/target from liquidity.
- **Q2 (Invalidation/target)**: Liquidity-first (SMC-aligned). Bullish → target=`nearest_liquidity_above`, invalidation=`nearest_liquidity_below`. Bearish → inverse. Fallback chain: swing levels → EMA21 → None.
- **Q3 (MTF weighting)**: Hierarchical (not weighted average). Daily sets regime/bias unconditionally. H4 and H1 adjust confidence but CANNOT flip the bias. `bias` field in `ConfluenceResult` is always the HTF bias.

Test strategy confirmed:
- **TDD** for core logic (T1 narrative builder, T2 decision engine)
- **Tests-after** for exploratory layers (T3 MTF update, T4/T5 edge cases, T6 integration)

### Metis Review (Gap Analysis)

| Gap | Resolution |
|-----|-----------|
| **Import path for `rsi_label`/`mfi_signal`** | `trade_scripts.analyze_ta` is importable via existing `sys.path.insert(0, ...)` in conftest. Narrative builder uses `from trade_scripts.analyze_ta import rsi_label, mfi_signal`. |
| **Backward compatibility of `composite_score()`** | Old additive behavior kept as `legacy_composite_score()`. New `composite_score()` uses hierarchical logic. Existing tests updated to call legacy method. |
| **max_score in hierarchical mode** | Always 10 (single-TF max) — bias comes from HTF regardless, score reflects confidence in that bias. |
| **`alignment()` vs `regime_alignment()`** | `alignment()` unchanged (still detects bias agreement across TFs). New `regime_alignment` property on MarketContext reports LTF agreement with HTF: `"aligned"`, list of conflicting TFs, or `"neutral_if_no_htf"`. |
| **Decision engine with/without MarketContext** | `decide()` accepts `context: MarketContext \| None = None`. When None, works on single-TF result only (no MTF adjustment). |
| **NaN handling in snapshot values** | Narrative builder handles NaN gracefully — sections report missing data rather than crashing. Uses `math.isnan()` guards. |
| **`watch_breakout` as modifier, not top-level action** | Decision has `breakout_pending: bool` flag + `breakout_level: float \| None`. Action field stays mutually exclusive: `look_for_longs` / `avoid_shorts` / `stand_aside`. |

---

## Work Objectives

### Core Objective
Extend the existing MarketSnapshot → ConfluenceScorer pipeline with three additive, non-intrusive layers: narrative (presentation), decision (actionability), and hierarchical MTF (structural regime awareness). All new code respects the existing separation of concerns: snapshot = state, confluence = scoring, narrative = formatting, decision = action mapping.

### Concrete Deliverables
1. `narrative.py` — `NarrativeSection` dataclass, `MarketNarrative` dataclass, `MarketNarrativeBuilder` class
2. `decision_engine.py` — `Decision` dataclass, `DecisionEngine` class (with `decide()` method)
3. Updated `confluence.py` — Hierarchical `MarketContext.composite_score()` + `regime_alignment` property + `legacy_composite_score()` method
4. `tests/test_narrative.py` — TDD for `MarketNarrativeBuilder`, tests-after for edge cases
5. `tests/test_decision_engine.py` — TDD for `DecisionEngine`, tests-after for edge cases
6. `tests/conftest.py` — additional fixtures for MTF scenarios
7. Integration test in `tests/test_market_snapshot.py` or new integration module

### Definition of Done
- `narrative.py` and `decision_engine.py` pass `python -c "from narrative import ...; from decision_engine import ..."` with zero import errors
- TDD tests for narrative builder pass BEFORE implementation is written (RED phase)
- TDD tests for decision engine pass BEFORE implementation is written (RED phase)
- All narrative builder tests pass with correct section content for known inputs
- All decision engine tests pass with correct action/confidence/invalidation/target for known inputs
- Hierarchical `composite_score()` preserves HTF bias even with extreme lower-TF disagreement
- `regime_alignment` correctly reports aligned/conflicting TFs
- Existing `alignment()` behavior unchanged (regression verified)
- Full integration test: SnapshotBuilder → ConfluenceScorer → MarketNarrativeBuilder → DecisionEngine
- All tests pass: `python -m pytest tests/ -v --tb=short`
- Evidence files saved to `.sisyphus/evidence/` for all QA scenarios

### Must Have
- `MarketNarrative` with all 5 sections (trend, momentum, structure, liquidity, conclusion)
- `MarketNarrativeBuilder` that takes `MarketSnapshot` + `ConfluenceResult` and produces a `MarketNarrative`
- Section content using `rsi_label()` and `mfi_signal()` from `analyze_ta.py` for zone labels
- `Decision` dataclass with bias, confidence, action, invalidation, target, breakout_pending, breakout_level
- `DecisionEngine.decide()` with action mapping from score ranges
- Liquidity-first invalidation/target with documented fallback chains
- Hierarchical MTF: HTF sets bias, LTF adjusts confidence only
- `regime_alignment` property on `MarketContext`
- `watch_breakout` as modifier flag, not top-level action
- TDD for narrative and decision engine core logic
- Tests-after for MTF update, edge cases, and integration

### Must NOT Have (Guardrails)
- ❌ No changes to `market_snapshot.py` (it's pure state, frozen)
- ❌ No changes to `backtest.py`, `smc.py`, `trade_scripts/analyze_ta.py`
- ❌ Decision Engine does NOT execute trades — only decision support
- ❌ No new external dependencies beyond existing (numpy, pandas, pytest)
- ❌ No file I/O in production classes (narrative builder, decision engine)
- ❌ No LLM/ML API calls — all narrative text is rule-based template filling
- ❌ No `watch_breakout` as a top-level action — it's a modifier flag only
- ❌ Bias field in `ConfluenceResult` = HTF bias under hierarchical mode, NOT a re-score

---

## Verification Strategy
- **Test decision**: TDD for narrative builder and decision engine core logic. Tests-after for MTF update, edge cases, and integration.
- **Test infrastructure**: pytest with deterministic fixtures. All tests run via `python -m pytest tests/test_narrative.py` and `tests/test_decision_engine.py`.
- **QA policy**: ALL verification is agent-executed via pytest + Python assertions + manual command verification. Zero human intervention.
- **Every task** has agent-executable QA scenarios with evidence saved to `.sisyphus/evidence/task-{N}-{scenario}.{ext}`.
- **Float comparison note**: After T3b (hierarchical MTF), `ConfluenceResult.score` is widened from `int` to `float`. All test assertions that compare scores numerically should use `pytest.approx()` or explicit rounding (`round(score, 2)`) instead of strict `==` with integers. This applies to narrative builder tests (T1a test 1: `narrative.score`) and decision engine tests (T2a test 1: confidence at boundaries).

---

## Execution Strategy

### Parallel Execution Waves

| Wave | Tasks | Description | Dependencies |
|------|-------|-------------|--------------|
| **1** | T1a → T1b | **Narrative (TDD)**: Write tests → Implement MarketNarrativeBuilder | None |
| **2** | T2a → T2b | **Decision Engine (TDD)**: Write tests → Implement DecisionEngine | None (parallelizable with W1) |
| **3** | T3 | **MTF Confluence update**: conftest fixtures → hierarchical composite_score | T1b, T2b (needs narrative+decision API stable) |
| **4** | T4, T5 | **Edge case tests**: narrative edge cases (T4) + decision engine edge cases (T5) — parallel | T1b, T2b |
| **5** | T6 | **Integration test**: full pipeline end-to-end | T3, T4, T5 |
| **6** | F1-F4 | **Final verification** | T6 |

### Dependency Matrix

```
       T1a     T1b     T2a     T2b     T3      T4      T5      T6
T1a     —    blocks    —       —       —       —       —       —
T1b     —      —       —       —     blocks  blocks   —       —
T2a     —      —       —     blocks   —       —       —       —
T2b     —      —       —       —     blocks   —     blocks    —
T3      —      —       —       —       —      —       —     blocks
T4      —      —       —       —       —      —       —     blocks
T5      —      —       —       —       —      —       —     blocks
T6      —      —       —       —       —      —       —       —
```

**Key insight**: Waves 1 and 2 are independent (narrative and decision don't depend on each other). They could run in parallel if the executing agent supports it.

### Agent Dispatch Summary

| Task | Agent Profile | Skills Required | Justification |
|------|--------------|----------------|---------------|
| T1a/T1b | builder | Python testing, TDD, market data | Narrative is rule-based text generation — tests first ensure correctness on known inputs |
| T2a/T2b | builder | Python testing, TDD, SMC concepts | Decision logic is pure conditional mapping — easy to specify expected outputs before writing |
| T3 | builder | Python, confluence system, MTF | Modifies existing MarketContext — needs understanding of current codebase |
| T4/T5 | builder | Python testing, edge case hunting | Exploratory — tests-after approach suits this |
| T6 | deep | Integration testing, system thinking | Validates full pipeline — needs broad context |

---

## TODOs

### Wave 1 — T1: Narrative Module (TDD)

- [ ] T1a. **Write narrative builder tests (RED phase)**

  **What to do**: Write comprehensive tests for `MarketNarrativeBuilder` BEFORE implementing it. Tests define the expected contract. Create `tests/test_narrative.py`.

  **Test cases required**:
  1. `test_bullish_narrative` — Use `sample_snapshot` + bullish `ConfluenceResult` (score 10/10). Verify:
     - `narrative.bias == "bullish"`
     - `narrative.score == 10`
     - Sections contain expected text: "above EMA21", "EMA21 rising", RSI label, MFI label, BOS confirmation, liquidity target
     - Conclusion contains "Bullish continuation" and the liquidity target level
  2. `test_bearish_narrative` — Use `bearish_snapshot` + bearish `ConfluenceResult` (score -4/10). Verify:
     - `narrative.bias == "bearish"`
     - Sections contain "below EMA21", "EMA21 falling", bearish BOS, invalidation level
  3. `test_neutral_narrative` — Use `neutral_snapshot` + neutral `ConfluenceResult` (score 5/10). Verify:
     - `narrative.bias == "neutral"`
     - Conclusion: "No clear directional edge" or "stand aside"
  4. `test_no_structure_data` — Snapshot with all structure fields None. Verify structure section reports missing data.
  5. `test_no_liquidity_data` — Snapshot with no liquidity levels. Verify liquidity section reports no significant clusters.
  6. `test_zone_labels_used` — Verify `rsi_label()` and `mfi_signal()` output appears in momentum section.
  7. `test_all_sections_present` — Verify all 5 sections (trend, momentum, structure, liquidity, conclusion) are present in every narrative.

  **Fixture needs**: Add to conftest if needed for specific scenarios (e.g., a snapshot with missing structure data).

  **Must NOT do**:
  - ❌ Do not import or call `MarketNarrativeBuilder` from production code yet — tests will fail expectedly
  - ❌ Do not modify existing test files (test_market_snapshot.py)

  **Parallelization**: Wave 1, no blocks.

  **References**:
  - `conftest.py` fixtures: `sample_snapshot`, `bearish_snapshot`, `neutral_snapshot`
  - `confluence.py`: `ConfluenceResult`, `ConfluenceScorer`
  - `analyze_ta.py`: `rsi_label()`, `mfi_signal()` output formats
  - `market_snapshot.py`: `MarketSnapshot` field types
  - Existing test patterns in `test_market_snapshot.py`

  **Acceptance Criteria**:
  - ✅ `python -m pytest tests/test_narrative.py -v --tb=short` FAILS (RED phase — builder not yet implemented)
  - ✅ All test function names follow `test_*` pattern
  - ✅ No implementation code imported from `narrative.py` except the dataclass types that might be needed for type hints

  **QA Scenarios**:
  1. *narrative-tdd-red*:
     - **Tool**: Bash (pytest)
     - **Preconditions**: `tests/test_narrative.py` written, `narrative.py` does not exist yet
     - **Steps**: `python -m pytest tests/test_narrative.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-T1a-red-phase.log`
     - **Expected Result**: Tests fail with `ModuleNotFoundError` or `ImportError` — builder not yet implemented
     - **Evidence**: `.sisyphus/evidence/task-T1a-red-phase.log`

- [ ] T1b. **Implement MarketNarrativeBuilder (GREEN phase)**

  **What to do**: Implement `narrative.py` with:
  1. `NarrativeSection` dataclass: `title: str`, `bullets: list[str]`
  2. `MarketNarrative` dataclass: `symbol`, `timeframe`, `bias`, `score`, `max_score`, `sections: list[NarrativeSection]`, `conclusion: str`
  3. `MarketNarrativeBuilder` class with `build(snapshot: MarketSnapshot, result: ConfluenceResult) -> MarketNarrative`

  **Builder logic by section**:
  - **Trend**: `trend_direction` + EMA21 slope direction. Use f-string: `"Price {above/below/at} EMA21 ({ema21}), EMA21 {rising/falling/flat}"`
  - **Momentum**: MACD vs signal, `rsi_label(rsi14)`, `mfi_signal(mfi14)`. Use zone labels from `analyze_ta.py`.
  - **Structure**: Last swing direction + level, BOS direction + status, CHOCH detection. Handle None values gracefully.
  - **Liquidity**: Nearest liquidity above/below with levels. Active OBs. Handle None.
  - **Conclusion**: Generated from dominant signal:
    - Bullish: `"Bullish continuation favored while EMA21 remains intact. Liquidity target at {target_level}."`
    - Bearish: `"Bearish momentum intact. Invalidation at {invalidation_level}."`
    - Neutral: `"Mixed signals across timeframes. No clear directional edge. Stand aside."`
    - Target/invalidation levels from snapshot (nearest_liquidity_above/below)

  **Import `rsi_label` and `mfi_signal`** from `trade_scripts.analyze_ta`:
  ```python
  from trade_scripts.analyze_ta import rsi_label, mfi_signal
  ```

  **Must NOT do**:
  - ❌ No scoring logic (confluence.py is the only scorer)
  - ❌ No trading decisions (decision_engine.py is the decider)
  - ❌ No file I/O
  - ❌ No LLM/ML narrative generation — all rule-based

  **Parallelization**: Wave 1, blocked-by T1a.

  **References**:
  - T1a test file for expected interface
  - `market_snapshot.py`: `MarketSnapshot` fields
  - `confluence.py`: `ConfluenceResult` fields
  - `analyze_ta.py`: `rsi_label()`, `mfi_signal()` function signatures
  - Oracle review for edge case handling

  **Acceptance Criteria**:
  - ✅ `python -m pytest tests/test_narrative.py -v --tb=short` ALL GREEN
  - ✅ `python -c "from narrative import MarketNarrative, NarrativeSection, MarketNarrativeBuilder"` succeeds
  - ✅ Builder produces correct output for all 3 bias scenarios (bullish/bearish/neutral)
  - ✅ Builder handles missing data (no structure, no liquidity) without crashing

  **QA Scenarios**:
  1. *narrative-tdd-green*:
     - **Tool**: Bash (pytest)
     - **Preconditions**: `narrative.py` implemented, tests from T1a exist
     - **Steps**: `python -m pytest tests/test_narrative.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-T1b-green-phase.log`
     - **Expected Result**: All tests pass (or known expected failures documented)
     - **Evidence**: `.sisyphus/evidence/task-T1b-green-phase.log`

  2. *narrative-manual-bullish*:
     - **Tool**: Bash (python -c)
     - **Preconditions**: narrative.py implemented
     - **Steps**:
       ```bash
       python -c "
       from market_snapshot import MarketSnapshot
       from confluence import ConfluenceResult
       from narrative import MarketNarrativeBuilder
       import pandas as pd
       snap = MarketSnapshot(symbol='BTC/USDT', timeframe='1d', timestamp=pd.Timestamp.now(),
           close=50000, trend_direction='above', ema21=49000, ema21_slope=0.01,
           rsi14=62.3, mfi14=58.1, macd=100, macd_signal=90, macd_hist=10,
           atr14=500, bb_width=0.05, last_bos_direction=1,
           nearest_liquidity_above=51000)
       result = ConfluenceResult('bullish', 10, 10, ['test'])
       builder = MarketNarrativeBuilder()
       narrative = builder.build(snap, result)
       for s in narrative.sections:
           print(f'{s.title}: {s.bullets}')
       print(f'Conclusion: {narrative.conclusion}')
       " 2>&1 | tee .sisyphus/evidence/task-T1b-bullish-output.txt
       ```
     - **Expected Result**: Trend says "above EMA21", momentum has RSI/MFI labels, conclusion references 51000 as target
     - **Evidence**: `.sisyphus/evidence/task-T1b-bullish-output.txt`

---

### Wave 2 — T2: Decision Engine Module (TDD)

- [ ] T2a. **Write decision engine tests (RED phase)**

  **What to do**: Write comprehensive tests for `DecisionEngine` before implementing it. Create `tests/test_decision_engine.py`.

  **Test cases required**:
  1. `test_look_for_longs` — ConfluenceResult with score=10, bullish bias. Verify `action == "look_for_longs"`, `bias == "bullish"`, `confidence == 1.0`
  2. `test_avoid_shorts` — ConfluenceResult with score=-4, bearish bias. Verify `action == "avoid_shorts"`, `bias == "bearish"`, `confidence == 0.4`
  3. `test_stand_aside` — ConfluenceResult with score=4, neutral bias. Verify `action == "stand_aside"`, `bias == "neutral"`
  4. `test_watch_breakout_modifier` — ConfluenceResult with score=5, neutral bias but near a level. Verify `action == "stand_aside"`, `breakout_level` is set from snapshot swing high/low, and `breakout_pending is True`
  5. `test_invalidation_bullish_liquidity_first` — Bullish snapshot with `nearest_liquidity_below=47000`. Verify `invalidation == 47000`
  6. `test_invalidation_bullish_swing_fallback` — Bullish snapshot with no liquidity below, but `last_swing_direction=-1, last_swing_level=47500`. Verify `invalidation == 47500`
  7. `test_invalidation_bullish_ema_fallback` — Bullish snapshot with no liquidity and no swing. Verify `invalidation == snapshot.ema21`
  8. `test_invalidation_bullish_no_fallback` — Bullish snapshot with nothing. Verify `invalidation is None`
  9. `test_target_bullish` — Bullish snapshot with `nearest_liquidity_above=51000`. Verify `target == 51000`
  10. `test_target_bearish` — Bearish snapshot with `nearest_liquidity_below=45000`. Verify `target == 45000`
  11. `test_confidence_formula` — Verify `confidence = abs(score) / max_score` for multiple score values: 10→1.0, 7→0.7, 4→0.4, 0→0.0, -4→0.4
  12. `test_neutral_invalidation_target_none` — Neutral decision has both invalidation=None and target=None
  13. `test_with_market_context` — Pass a `MarketContext` with daily bullish + h4 bearish. Verify hierarchical logic doesn't flip bias (bias stays from daily)
  14. `test_without_market_context` — Call `decide()` with `context=None`. Verify single-TF behavior.

  **Edge cases**:
  - Score=0 → action `stand_aside`
  - Score=7 with no liquidity above → target=None
  - Score=-3 → `avoid_shorts`, confidence=0.3
  - Decision dataclass construction with all fields

  **Must NOT do**:
  - ❌ Do not import DecisionEngine from production code for the tests (they will fail expectedly)
  - ❌ Do not modify existing test files

  **Parallelization**: Wave 2, no blocks (parallel with W1).

  **References**:
  - `confluence.py`: `ConfluenceResult`, `ConfluenceScorer`, `MarketContext`
  - `market_snapshot.py`: `MarketSnapshot` field types
  - `conftest.py` fixtures: `sample_snapshot`, `bearish_snapshot`, `neutral_snapshot`
  - Oracle review: action taxonomy, confidence formula, invalidation fallback chain

  **Acceptance Criteria**:
  - ✅ `python -m pytest tests/test_decision_engine.py -v --tb=short` FAILS (RED phase)
  - ✅ All test function names follow `test_*` pattern

  **QA Scenarios**:
  1. *decision-tdd-red*:
     - **Tool**: Bash (pytest)
     - **Preconditions**: `tests/test_decision_engine.py` written, `decision_engine.py` does not exist yet
     - **Steps**: `python -m pytest tests/test_decision_engine.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-T2a-red-phase.log`
     - **Expected Result**: Tests fail with `ModuleNotFoundError` or `ImportError`
     - **Evidence**: `.sisyphus/evidence/task-T2a-red-phase.log`

- [ ] T2b. **Implement DecisionEngine (GREEN phase)**

  **What to do**: Implement `decision_engine.py` with:

  ```python
  @dataclass
  class Decision:
      bias: str                     # "bullish" / "bearish" / "neutral"
      confidence: float             # 0.0 to 1.0
      action: str                   # "look_for_longs" / "avoid_shorts" / "stand_aside"
      invalidation: float | None    # Price level that invalidates the bias
      target: float | None          # Price target in bias direction
      breakout_pending: bool = False  # Modifier flag: breakout brewing but unconfirmed
      breakout_level: float | None = None  # Specific level to watch for breakout

  class DecisionEngine:
      def decide(
          self,
          snapshot: MarketSnapshot,
          result: ConfluenceResult,
          context: MarketContext | None = None,
      ) -> Decision:
  ```

  **Core logic**:

  1. **Action mapping** from `result.score`:
     - score >= 7 → `look_for_longs`
     - score 4 to 6 → `stand_aside` (+ sets `breakout_pending=True` if near a swing level)
     - score 0 to 3 → `stand_aside`
     - score < 0 → `avoid_shorts`

  2. **`breakout_pending` modifier**: If score is 4-6 AND snapshot has a clear swing high/low within 1 ATR, set `breakout_pending=True` and `breakout_level` to that swing level. `action` remains `stand_aside`.

  3. **Confidence**: `min(1.0, abs(result.score) / result.max_score)`. Clamped to [0.0, 1.0].

  4. **Invalidation (liquidity-first)**:
     - Bullish bias → `snapshot.nearest_liquidity_below` → `last_swing_level` (if swing was in opposite direction) → `snapshot.ema21` → None
     - Bearish bias → `snapshot.nearest_liquidity_above` → `last_swing_level` (if swing was in opposite direction) → `snapshot.ema21` → None
     - Neutral → None

  5. **Target (liquidity-first)**:
     - Bullish bias → `snapshot.nearest_liquidity_above` → `last_swing_level` (if swing was in same direction) → None
     - Bearish bias → `snapshot.nearest_liquidity_below` → `last_swing_level` (if swing was in same direction) → None
     - Neutral → None

  6. **Bias**: From `result.bias` (not computed independently).

  **Must NOT do**:
  - ❌ No trade execution — decisions are advisory only
  - ❌ No modification to MarketSnapshot or ConfluenceResult
  - ❌ No file I/O

  **Parallelization**: Wave 2, blocked-by T2a.

  **References**:
  - T2a test file for expected interface
  - `market_snapshot.py`: `MarketSnapshot` fields (liquidity, swings, ema21, close)
  - `confluence.py`: `ConfluenceResult` fields
  - Oracle review for confidence formula, invalidation chain, action mapping

  **Acceptance Criteria**:
  - ✅ `python -m pytest tests/test_decision_engine.py -v --tb=short` ALL GREEN
  - ✅ `python -c "from decision_engine import Decision, DecisionEngine"` succeeds
  - ✅ Correct outputs for all score boundaries (10, 7, 6, 4, 3, 0, -4)
  - ✅ Invalidation fallback chain works correctly for all 3 fallback levels

  **QA Scenarios**:
  1. *decision-tdd-green*:
     - **Tool**: Bash (pytest)
     - **Preconditions**: `decision_engine.py` implemented, tests from T2a exist
     - **Steps**: `python -m pytest tests/test_decision_engine.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-T2b-green-phase.log`
     - **Expected Result**: All tests pass
     - **Evidence**: `.sisyphus/evidence/task-T2b-green-phase.log`

  2. *decision-manual-output*:
     - **Tool**: Bash (python -c)
     - **Preconditions**: decision_engine.py implemented
     - **Steps**:
       ```bash
       python -c "
       from market_snapshot import MarketSnapshot
       from confluence import ConfluenceResult
       from decision_engine import DecisionEngine
       import pandas as pd
       snap = MarketSnapshot(symbol='BTC/USDT', timeframe='1d', timestamp=pd.Timestamp.now(),
           close=50000, trend_direction='above', ema21=49000, ema21_slope=0.01,
           rsi14=62.3, mfi14=58.1, macd=100, macd_signal=90, macd_hist=10,
           atr14=500, bb_width=0.05, last_bos_direction=1,
           nearest_liquidity_above=51000, nearest_liquidity_below=48500)
       result = ConfluenceResult('bullish', 10, 10, ['test'])
       engine = DecisionEngine()
       decision = engine.decide(snap, result)
       print(f'Bias: {decision.bias}')
       print(f'Confidence: {decision.confidence}')
       print(f'Action: {decision.action}')
       print(f'Invalidation: {decision.invalidation}')
       print(f'Target: {decision.target}')
       " 2>&1 | tee .sisyphus/evidence/task-T2b-decision-output.txt
       ```
     - **Expected Result**: bias=bullish, confidence=1.0, action=look_for_longs, invalidation=48500, target=51000
     - **Evidence**: `.sisyphus/evidence/task-T2b-decision-output.txt`

---

### Wave 3 — T3: Hierarchical MTF Confluence Update (Tests-After)

- [ ] T3a. **Add MTF test fixtures to conftest.py**

  **What to do**: Add fixtures to `tests/conftest.py` for multi-timeframe scenarios to support hierarchical MTF testing.

  **Fixtures to add**:
  1. `daily_bullish_snapshot` — Same as `sample_snapshot` but with timeframe="1d"
  2. `h4_bearish_snapshot` — Bearish conditions with timeframe="4h" (close below EMA21, bearish BOS, etc.)
  3. `h4_bullish_snapshot` — Bullish conditions with timeframe="4h"
  4. `h1_neutral_snapshot` — Neutral conditions with timeframe="1h"
  5. `mtx_bullish_daily` — `MarketContext(daily=daily_bullish, h4=h4_bullish, h1=h1_neutral)` — all aligned
  6. `mtx_conflicting_h4` — `MarketContext(daily=daily_bullish, h4=h4_bearish)` — H4 disagrees with daily
  7. `mtx_conflicting_both` — `MarketContext(daily=daily_bullish, h4=h4_bearish, h1=neutral)` — both LTFs disagree
  8. `mtx_no_daily` — `MarketContext(h4=h4_bullish, h1=h1_neutral)` — missing daily
  9. `mtx_no_h1` — `MarketContext(daily=daily_bullish, h4=h4_bullish)` — missing h1
  10. `mtx_all_neutral` — All three TFs neutral

  **Must NOT do**:
  - ❌ Do not modify existing fixtures — only append new ones
  - ❌ Do not change the behavior of existing tests

  **Parallelization**: Wave 3, no blocks (but ideally after T1b/T2b so interfaces are stable).

  **References**:
  - `conftest.py` existing fixtures for naming/structure conventions
  - `MarketContext` dataclass constructor
  - `MarketSnapshot` dataclass constructor

  **Acceptance Criteria**:
  - ✅ `python -c "from conftest import ..."` or pytest collection picks up new fixtures
  - ✅ All new fixtures return properly typed objects
  - ✅ Test can import and use `mtx_bullish_daily`, `mtx_conflicting_h4`, etc.

  **QA Scenarios**:
  1. *conftest-fixtures-smoke*:
     - **Tool**: Bash (pytest collection)
     - **Preconditions**: conftest.py updated
     - **Steps**: `python -m pytest tests/test_market_snapshot.py --collect-only 2>&1 | grep -E "(mtx_|_snapshot)" | tee .sisyphus/evidence/task-T3a-fixtures.log`
     - **Expected Result**: New fixtures appear in collection
     - **Evidence**: `.sisyphus/evidence/task-T3a-fixtures.log`

- [ ] T3b. **Implement hierarchical composite_score + regime_alignment**

   **What to do**: Update `MarketContext` in `confluence.py`. Also widen `ConfluenceResult.score` from `int` to `float`.

   0. **Widen `ConfluenceResult.score`** from `int` to `float` in `confluence.py` — this is a type widening (all ints are valid floats), so no existing semantics break. This is needed because hierarchical MTF adjustments (`ltf_score * 0.3`) produce fractional scores.
   1. **Rename** current `composite_score()` to `legacy_composite_score()` for backward compatibility
   2. **Implement new `composite_score()`** with hierarchical logic:
     - Daily (if available) sets the regime → `base_bias` and `base_score`
     - If daily missing → H4 is the HTF
     - If only H1 → H1 is the HTF
     - For each LTF: score the LTF with ConfluenceScorer
       - If LTF agrees with HTF bias → boost confidence by `ltf_score * 0.3`
       - If LTF disagrees → reduce confidence by `abs(ltf_score) * 0.3`
     - `bias` in returned ConfluenceResult = HTF bias (NEVER changes)
     - `max_score` = 10 (always — represents single-TF max, score is confidence-adjusted)

  3. **Add `regime_alignment` property**:
     ```python
     @property
     def regime_alignment(self) -> str | list[str]:
         """Report alignment of lower TFs with the HTF regime.
         
         Returns:
             "aligned" — all TFs agree with HTF
             ["h4_conflict", "h1_conflict"] — list of conflicting TFs  
             "neutral_if_no_htf" — no HTF available
         """
     ```
     Implementation: Score all TFs. Find HTF (highest available). Compare each LTF bias to HTF bias. Return accordingly.

  4. **Update tests** in `test_market_snapshot.py`:
     - The `TestMarketContext` tests that use `composite_score()` should be updated to use `legacy_composite_score()`
     - Add new tests for hierarchical `composite_score()` using the new conftest fixtures

   **Must NOT do**:
   - ❌ Do not change `alignment()` method — it still detects bias-alignment across all TFs
   - ❌ Do not modify `ConfluenceScorer` — exception logic untouched
   - ❌ Do not modify `MarketSnapshot`
   - ⚠️ **Allowed exception**: Widen `ConfluenceResult.score` from `int` to `float` to accommodate fractional confidence adjustments from hierarchical MTF (`ltf_score * 0.3`). This is a type widening — all int values remain valid floats.

   **Float migration note**: The hierarchical `composite_score()` produces float scores (e.g., `8.4`, `-2.1`). Update `ConfluenceResult.score` to accept float. Existing tests using integer equality (`==`) should use `pytest.approx()` or explicit rounding. The `ConfluenceResult` dataclass itself will be updated in `confluence.py` — but `score` can accept `int | float` or just `float` (widening, not breaking).

   **Parallelization**: Wave 3, blocked-by T1b/T2b (interfaces stable).

  **References**:
  - `confluence.py` existing `MarketContext`
  - `conftest.py` new MTF fixtures
  - Design decisions: hierarchical MTF, HTF sets bias unconditionally

  **Acceptance Criteria**:
  - ✅ `python -m pytest tests/test_market_snapshot.py -v --tb=short` ALL GREEN (including updated TestMarketContext)
  - ✅ `legacy_composite_score()` returns same results as old `composite_score()` for same inputs
  - ✅ New `composite_score()`: daily bullish + h4 max-bearish → bias=bullish (not flipped)
  - ✅ New `composite_score()`: daily bearish + h4 max-bullish → bias=bearish (not flipped)
  - ✅ New `composite_score()`: only H4 available → H4 is HTF
  - ✅ `regime_alignment` returns correct values for all MTF scenarios
  - ✅ No changes to ConfluenceScorer, ConfluenceResult, or MarketSnapshot

  **QA Scenarios**:
  1. *mtf-hierarchical-regression*:
     - **Tool**: Bash (pytest)
     - **Preconditions**: confluence.py updated, legacy_composite_score() exists
     - **Steps**: `python -m pytest tests/test_market_snapshot.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-T3b-regression.log`
     - **Expected Result**: All existing tests pass (TestMarketContext uses legacy method)
     - **Evidence**: `.sisyphus/evidence/task-T3b-regression.log`

  2. *mtf-htf-not-flipped*:
     - **Tool**: Bash (python -c)
     - **Preconditions**: confluence.py updated, conftest fixtures available
     - **Steps**:
       ```bash
       python -c "
       from tests.conftest import *
       from confluence import ConfluenceScorer, MarketContext
       import pandas as pd
       # Create daily bullish + h4 max-bearish
       from market_snapshot import MarketSnapshot
       daily = MarketSnapshot(symbol='BTC/USDT', timeframe='1d', timestamp=pd.Timestamp.now(),
           close=50000, trend_direction='above', ema21=49000, ema21_slope=0.01,
           rsi14=60, mfi14=55, macd=100, macd_signal=90, macd_hist=10,
           atr14=500, bb_width=0.05, last_bos_direction=1, nearest_liquidity_above=51000)
       h4 = MarketSnapshot(symbol='BTC/USDT', timeframe='4h', timestamp=pd.Timestamp.now(),
           close=48000, trend_direction='below', ema21=49000, ema21_slope=-0.01,
           rsi14=40, mfi14=35, macd=50, macd_signal=90, macd_hist=-5,
           atr14=400, bb_width=0.05, last_bos_direction=-1, nearest_liquidity_below=46000)
       ctx = MarketContext(daily=daily, h4=h4)
       result = ctx.composite_score()
       print(f'Bias: {result.bias} (should be bullish — daily regime)')
       print(f'Score: {result.score}')
       print(f'Reasons: {result.reasons[:3]}')
       assert result.bias == 'bullish', f'HTF bias not preserved: {result.bias}'
       " 2>&1 | tee .sisyphus/evidence/task-T3b-htf-preserved.log
       ```
     - **Expected Result**: Bias is "bullish" (daily regime), score reflects H4 disagreement
     - **Evidence**: `.sisyphus/evidence/task-T3b-htf-preserved.log`

  3. *mtf-regime-alignment*:
     - **Tool**: Bash (python -c)
     - **Preconditions**: confluence.py updated
     - **Steps**:
       ```bash
       python -c "
       ... (similar setup for alignment test)
       ctx = MarketContext(daily=daily_bullish, h4=h4_bearish, h1=h1_neutral)
       print(f'Regime alignment: {ctx.regime_alignment}')
       assert 'h4_conflict' in ctx.regime_alignment
       " 2>&1 | tee .sisyphus/evidence/task-T3b-alignment.log
       ```
     - **Expected Result**: regime_alignment identifies h4_conflict
     - **Evidence**: `.sisyphus/evidence/task-T3b-alignment.log`

---

### Wave 4 — T4 & T5: Edge Case Tests (Tests-After, Parallel)

- [ ] T4. **Narrative edge case tests**

  **What to do**: Add tests-after coverage for narrative edge cases in `tests/test_narrative.py`.

  **Test cases**:
  1. `test_narrative_nan_values` — Snapshot with NaN RSI/MFI. Verify builder handles gracefully (no crash, reasonable fallback text).
  2. `test_narrative_empty_result_reasons` — ConfluenceResult with empty reasons list. Verifies builder doesn't depend on reasons.
  3. `test_narrative_symbol_timeframe_passthrough` — Verifies symbol and timeframe pass through from snapshot.
  4. `test_narrative_score_zero` — Edge case: score=0, bias=bearish. Verifies conclusion reflects weak bearish.
  5. `test_narrative_max_score_integrity` — max_score always equals result.max_score.
  6. `test_narrative_conclusion_format` — Verify conclusion string format matches expected pattern for each bias.
  7. `test_narrative_section_ordering` — Sections are in consistent order: trend, momentum, structure, liquidity.

  **Must NOT do**:
  - ❌ Do not modify MarketNarrativeBuilder or core dataclasses
  - ❌ Tests should pass without any production code changes

  **Parallelization**: Wave 4 (parallel with T5), blocked-by T1b.

  **References**:
  - `test_market_snapshot.py` test patterns
  - `conftest.py` fixtures

  **Acceptance Criteria**:
  - ✅ All new tests pass
  - ✅ Tests cover NaN handling, empty data edge cases

  **QA Scenarios**:
  1. *narrative-edge-tests*:
     - **Tool**: Bash (pytest)
     - **Preconditions**: T1b implemented, tests from T4 written
     - **Steps**: `python -m pytest tests/test_narrative.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-T4-edge-tests.log`
     - **Expected Result**: All tests pass
     - **Evidence**: `.sisyphus/evidence/task-T4-edge-tests.log`

- [ ] T5. **Decision engine edge case tests**

  **What to do**: Add tests-after coverage for decision engine edge cases in `tests/test_decision_engine.py`.

  **Test cases**:
  1. `test_decision_score_zero` — Score=0. Verify action=stand_aside, confidence=0.0.
  2. `test_decision_score_threshold_boundaries` — Scores at exact boundaries: -1, 0, 3, 4, 6, 7, 10.
  3. `test_decision_target_swing_fallback` — Bullish with no liquidity above but swing high exists. Verify target=swing_level.
  4. `test_decision_target_no_fallback` — Bullish with no liquidity and no swing. Verify target=None.
  5. `test_decision_both_liquidity_present` — Both nearest_liquidity_above and below set. Verify both target and invalidation are set.
  6. `test_decision_breakout_pending` — Score=5 with swing level within 1 ATR. Verify `action == "stand_aside"`, `breakout_pending is True`, and `breakout_level` is set.
  7. `test_decision_no_breakout_pending` — Score=5 but no swing level nearby. Verify `breakout_pending is False` and `breakout_level is None`.
  8. `test_decision_context_hierarchical_influence` — With MarketContext where H4 disagrees. Verify bias still from daily (hierarchical). Verify confidence adjusted downward.
  9. `test_decision_all_optional_none` — Snapshot with all structure/liquidity fields None. Verify action=stand_aside, target=None, invalidation=None.

  **Must NOT do**:
  - ❌ Do not modify Decision, DecisionEngine, or core decision logic
  - ❌ Tests should pass without any production code changes

  **Parallelization**: Wave 4 (parallel with T4), blocked-by T2b.

  **References**:
  - `decision_engine.py` — DecisionEngine.decide()
  - T2b QA scenarios for established baseline

  **Acceptance Criteria**:
  - ✅ All new tests pass
  - ✅ Tests cover all boundary conditions and fallback chains

  **QA Scenarios**:
  1. *decision-edge-tests*:
     - **Tool**: Bash (pytest)
     - **Preconditions**: T2b implemented, tests from T5 written
     - **Steps**: `python -m pytest tests/test_decision_engine.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-T5-edge-tests.log`
     - **Expected Result**: All tests pass
     - **Evidence**: `.sisyphus/evidence/task-T5-edge-tests.log`

---

### Wave 5 — T6: Integration Test (Tests-After)

- [ ] T6. **Full pipeline integration test**

  **What to do**: Write an integration test that exercises the full pipeline:
  SnapshotBuilder → ConfluenceScorer → MarketContext → MarketNarrativeBuilder → DecisionEngine

  **Location**: `tests/test_narrative.py` (appended) or a dedicated `tests/test_integration_pipeline.py`.

  **Test scenarios**:
  1. `test_full_pipeline_bullish` — Build daily snapshot from TA row + SMC report → score → build narrative → decide. Verify end-to-end consistent result (bias flows through all layers).
  2. `test_full_pipeline_bearish` — Same with bearish data.
  3. `test_full_pipeline_mtf_hierarchical` — Build 3 snapshots (daily, h4, h1) → MarketContext → composite_score (hierarchical) → narrative → decision. Verify HTF bias is preserved through all layers.
  4. `test_full_pipeline_narrative_decision_consistency` — Verify `Decision.bias` matches `MarketNarrative.bias` for the same inputs.
  5. `test_full_pipeline_snapshot_to_decision` — SnapshotBuilder → ConfluenceScorer → DecisionEngine (single TF, no MarketContext).

  **Must NOT do**:
  - ❌ No integration with live data — use existing fixtures and synthetic data
  - ❌ No network calls

  **Parallelization**: Wave 5, blocked-by T3, T4, T5.

  **References**:
  - All production files: `market_snapshot.py`, `confluence.py`, `narrative.py`, `decision_engine.py`
  - `conftest.py` fixtures
  - `test_market_snapshot.py` for existing integration test pattern (TestIntegration class)

  **Acceptance Criteria**:
  - ✅ Full pipeline test passes: decision.bias == narrative.bias == composite.bias
  - ✅ MTF hierarchical flow preserves daily bias through all layers
  - ✅ Single-TF flow works (context=None in DecisionEngine)

  **QA Scenarios**:
  1. *integration-full-pipeline*:
     - **Tool**: Bash (pytest)
     - **Preconditions**: All T1-T5 complete
     - **Steps**: `python -m pytest tests/test_narrative.py tests/test_decision_engine.py -v --tb=short 2>&1 | tee .sisyphus/evidence/task-T6-integration.log`
     - **Expected Result**: All integration tests pass
     - **Evidence**: `.sisyphus/evidence/task-T6-integration.log`

  2. *integration-pipeline-coherence*:
     - **Tool**: Bash (python -c)
     - **Preconditions**: All implementations complete
     - **Steps**:
       ```bash
       python -c "
       from market_snapshot import SnapshotBuilder
       from confluence import ConfluenceScorer, MarketContext
       from narrative import MarketNarrativeBuilder
       from decision_engine import DecisionEngine
       import pandas as pd
       # Build from known TA data
       ta = pd.Series({'timestamp': pd.Timestamp.now(), 'close': 50000.0,
           'ema21': 49000.0, 'ema21_slope': 0.01, 'rsi14': 62.3, 'mfi14': 58.1,
           'macd': 100.0, 'macd_signal': 90.0, 'macd_hist': 10.0, 'atr14': 500.0,
           'bb_width': 0.05})
       # Minimal SMC report with structure
       smc = pd.DataFrame({'SwingHighLow': [float('nan')]*10, 'SwingLevel': [float('nan')]*10,
           'BOS': [float('nan')]*10, 'CHOCH': [float('nan')]*10, 'BrokenIndex': [float('nan')]*10,
           'Liquidity': [float('nan')]*10, 'LiqLevel': [float('nan')]*10,
           'LiqSwept': [float('nan')]*10, 'OB': [float('nan')]*10,
           'OBTop': [float('nan')]*10, 'OBBottom': [float('nan')]*10,
           'OBMitigatedIndex': [float('nan')]*10})
       smc.loc[5, 'BOS'] = 1.0
       smc.loc[5, 'BrokenIndex'] = 3.0
       smc.loc[8, 'Liquidity'] = 1.0
       smc.loc[8, 'LiqLevel'] = 51500.0
       smc.loc[8, 'LiqSwept'] = 0.0
       # Pipeline
       builder = SnapshotBuilder()
       snap = builder.build('BTC/USDT', '1d', ta, smc)
       scorer = ConfluenceScorer()
       result = scorer.score(snap)
       narr_builder = MarketNarrativeBuilder()
       narrative = narr_builder.build(snap, result)
       engine = DecisionEngine()
       decision = engine.decide(snap, result)
       # Verify coherence
       print(f'Bias: score={result.bias}, narrative={narrative.bias}, decision={decision.bias}')
       print(f'Score: {result.score}/{result.max_score}')
       print(f'Narrative sections: {len(narrative.sections)}')
       print(f'Decision: action={decision.action}, confidence={decision.confidence:.2f}')
       print(f'Invalidation: {decision.invalidation}, Target: {decision.target}')
       assert result.bias == narrative.bias == decision.bias, 'Bias mismatch across layers'
       " 2>&1 | tee .sisyphus/evidence/task-T6-coherence.log
       ```
     - **Expected Result**: bias consistent across all 3 layers (scorer → narrative → decision)
     - **Evidence**: `.sisyphus/evidence/task-T6-coherence.log`

---

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** (oracle)

  **What**: Verify all tasks and deliverables from this plan are complete. Check each acceptance criterion.

  **QA**: Run a comprehensive audit script:
  ```bash
  # Check all files exist
  for f in narrative.py decision_engine.py; do
    python -c "from ${f%.py} import *" && echo "✅ $f importable"
  done
  # Check no forbidden modifications
  grep -c "class MarketSnapshot" market_snapshot.py  # should succeed (file exists)
  git diff --name-only | grep -E "^(backtest|smc|analyze_ta)" && echo "❌ Forbidden change!" || echo "✅ No forbidden changes"
  ```

  **Evidence**: `.sisyphus/evidence/final-F1-compliance.log`

- [ ] F2. **Code Quality Review** (unspecified-high)

  **What**: Review all new files for:
  - Type annotations on all public methods
  - Docstrings on all classes and public methods
  - No debug prints or commented-out code
  - Consistent naming with existing codebase

  **QA**: 
  ```bash
  python -c "
  from narrative import MarketNarrativeBuilder, MarketNarrative, NarrativeSection
  from decision_engine import DecisionEngine, Decision
  print('✅ All classes importable')
  "
  ```

  **Evidence**: `.sisyphus/evidence/final-F2-quality.log`

- [ ] F3. **Real Manual QA** (unspecified-high)

  **What**: End-to-end verification of the complete pipeline with known data, verifying ALL three features work together coherently.

  **QA**: Run T6 coherence test scenario (from T6 QA scenario 2) and verify output matches expected structure.

  **Evidence**: `.sisyphus/evidence/final-F3-manual-qa.log`

- [ ] F4. **Scope Fidelity Check** (deep)

  **What**: Verify guardrails are respected:
  - `market_snapshot.py` unmodified
  - `analyze_ta.py`, `backtest.py`, `smc.py` unmodified
  - Decision engine does not contain any trade execution code
  - No new external dependencies
  - No file I/O in production classes
  - `watch_breakout` is a modifier flag, not a top-level action

  **QA**:
  ```bash
  git diff --name-only | tee .sisyphus/evidence/final-F4-scope.log
  ```

  **Evidence**: `.sisyphus/evidence/final-F4-scope.log`

---

## Commit Strategy

| Wave | Commit Message | Files |
|------|---------------|-------|
| 1 | `feat(narrative): add MarketNarrativeBuilder with TDD` | `narrative.py`, `tests/test_narrative.py` |
| 2 | `feat(decision): add DecisionEngine with TDD` | `decision_engine.py`, `tests/test_decision_engine.py` |
| 3 | `feat(confluence): hierarchical MTF composite_score + regime_alignment` | `confluence.py` |
| 4 | `test(narrative,decision): edge case coverage` | `tests/test_narrative.py`, `tests/test_decision_engine.py`, `tests/conftest.py` |
| 5 | `test(integration): full pipeline snapshot→narrative→decision` | `tests/test_narrative.py` or `tests/test_integration_pipeline.py` |
| Final | `chore(evidence): add QA evidence for narrative-decision-mtf` | `.sisyphus/evidence/*` |

---

## Success Criteria

1. ✅ `narrative.py` + `decision_engine.py` exist, importable, and fully typed
2. ✅ `confluence.py` updated with hierarchical `composite_score()` + `regime_alignment`
3. ✅ All TDD tests pass (RED → GREEN for T1a/T1b, T2a/T2b)
4. ✅ All tests-after pass (T3, T4, T5, T6)
5. ✅ Existing test suite regression-free (77 tests still pass)
6. ✅ HTF bias is NEVER flipped by lower TFs in hierarchical mode
7. ✅ Narrative builder produces correct format for bullish/bearish/neutral
8. ✅ Decision engine maps scores to correct actions with liquidity-based invalidation/target
9. ✅ Full pipeline integration test produces consistent bias across all layers
10. ✅ All guardrails verified (no changes to market_snapshot.py, backtest.py, smc.py, analyze_ta.py)
11. ✅ All QA evidence saved to `.sisyphus/evidence/`
