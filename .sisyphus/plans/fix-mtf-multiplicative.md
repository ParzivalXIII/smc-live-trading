# Fix: Replace Additive MTF Scoring with Multiplicative Confidence Model

## TL;DR
> **Quick Summary**: Replace the current additive LTF confidence adjustment (`±0.3×|score|`) in `confluence.py:composite_score()` with a multiplicative confidence model where LTFs apply factors (1.0/0.7/0.4) to the HTF base score. This fixes the structural bug where LTFs could dilute HTF bias via asymmetric adjustment.
> **Deliverables**: Updated `composite_score()` + `_alignment_factor()` in `confluence.py`; new `TestHierarchicalMTF` class in `test_market_snapshot.py`; all tests green.
> **Estimated Effort**: Quick (< 1 hour)
> **Parallel Execution**: NO (sequential — T1→T2→T3)
> **Critical Path**: T1 (impl) → T2 (tests) → T3 (verification)

## Context
### Original Request
Replace the additive MTF scoring model in `confluence.py:composite_score()` with a multiplicative confidence model. The current model allows LTFs to push against HTF bias through an asymmetric additive adjustment (+30% agreement, -15% disagreement), which is structurally wrong for SMC regime trading where the highest timeframe must set bias immutably.

### Interview Summary
- Current `composite_score()` uses additive: `score += abs(ltf_score) * 0.3` (agree) or `score -= abs(ltf_score) * 0.15` (disagree).
- New model: HTF score × product of LTF alignment factors (1.0 aligned / 0.7 LTF-neutral / 0.4 conflicting).
- Mapping function corrected during interview: `elif ltf_bias == "neutral": return 0.7` (NOT `htf_bias == "neutral"`) — this correctly maps neutral HTF + directional LTF → 0.4 (conflicting).
- No existing `composite_score()` tests exist in `TestMarketContext` (all use `legacy_composite_score()`). New `TestHierarchicalMTF` class will be created.
- MTF fixtures from `conftest.py` (`mtx_bullish_daily`, `mtx_conflicting_h4`, etc.) are unused — will be wired to new tests.
- Existing downstream consumers (`decision_engine.py`, `narrative.py`) consume scores generically and need zero changes.

### Metis Review
- **✅ Passed**: max_score stays 10, confidence formula in decision engine (`abs(score)/10`) works naturally with multiplicative scores.
- **✅ Passed**: No ambiguity in factor mapping (resolved in interview: `elif ltf_bias == "neutral"` only).
- **⚠️ Flagged**: `regime_alignment` property returns "aligned" / conflict list but has no "partially_aligned" concept. The docstring update will clarify that partial alignment (factor=0.7) maps to "aligned" in regime_alignment (no conflict).
- **⚠️ Flagged**: `mtx_all_neutral` fixture does NOT produce all-neutral biases (daily=3/bearish, h4=0/bearish, h1=4/neutral). Tests must compute expected values by running scorer rather than assuming fixture name.
- **✅ Confirmed**: Guardrails respected — no changes to `market_snapshot.py`, `decision_engine.py`, or `narrative.py`.

## Work Objectives
### Core Objective
Replace additive LTF confidence adjustment with multiplicative alignment factors in `confluence.py:composite_score()`.

### Concrete Deliverables
1. Updated `composite_score()` with multiplicative confidence logic
2. New `_alignment_factor()` private method with correct factor mapping
3. Updated docstrings on `composite_score()` and `regime_alignment`
4. `legacy_composite_score()` preserved unchanged
5. New `TestHierarchicalMTF` class with 6+ test cases using pytest.approx()

### Definition of Done
- [ ] T1 complete: `composite_score()` returns multiplicatively-adjusted scores
- [ ] T2 complete: All `TestHierarchicalMTF` tests pass with expected values
- [ ] T3 complete: Full test suite passes (0 failures)
- [ ] No changes to `decision_engine.py`, `narrative.py`, `market_snapshot.py`
- [ ] `legacy_composite_score()` unchanged and its tests still pass

### Must Have
- [ ] Multiplicative model: `final_score = htf_score × prod(all_ltf_factors)`
- [ ] Factor mapping: aligned=1.0, LTF-neutral=0.7, conflicting=0.4
- [ ] HTF bias sets regime immutably (unchanged from current)
- [ ] `pytest.approx()` for all float assertions in new tests
- [ ] Reasons list describes alignment label + factor per LTF

### Must NOT Have (Guardrails)
- ❌ No changes to `market_snapshot.py`
- ❌ No changes to `decision_engine.py`
- ❌ No changes to `narrative.py`
- ❌ No changes to `legacy_composite_score()`
- ❌ No changes to existing `TestMarketContext` tests
- ❌ No changes to `conftest.py` MTF fixtures

## Verification Strategy
- **Test decision**: TDD for new model — tests written AFTER implementation (tests-after).
- **QA policy**: All verification is agent-executed. Zero human intervention.
- **Scope**: T1 verified via T2 tests. T3 verifies no regressions downstream.

## Execution Strategy
### Parallel Execution Waves
N/A — Sequential execution only (T1 → T2 → T3).

### Dependency Matrix
| Task | Depends On | Blocks |
|------|-----------|--------|
| T1. Update `composite_score()` | — | T2 |
| T2. Add `TestHierarchicalMTF` | T1 | T3 |
| T3. Run full test suite | T2 | — |

### Agent Dispatch Summary
| Task | Profile | Skills Required |
|------|---------|----------------|
| T1 | python-engineer | Python, SMC trading concepts, multiplicative math |
| T2 | python-tester | pytest, approx(), conftest fixture usage |
| T3 | unspecified-high | Test runner, triage |

## TODOs

- [ ] **T1. Replace `composite_score()` body with multiplicative logic + add `_alignment_factor()`**

  **What to do**: Edit `confluence.py` (lines 249–323). Replace the additive LTF adjustment loop with multiplicative confidence factors. Add a private static method `_alignment_factor()`. Update docstrings.

  **Detailed spec**:

  1. **Add `_alignment_factor` method** (before `composite_score`, e.g., at ~line 248):
     ```python
     @staticmethod
     def _alignment_factor(htf_bias: str, ltf_bias: str) -> float:
         """Return multiplicative factor for LTF alignment with HTF.

         Factor mapping (confirmed during Prometheus interview):
             ltf_bias == htf_bias            → 1.0  (aligned)
             ltf_bias == "neutral"           → 0.7  (LTF has no opinion)
             else                            → 0.4  (conflicting)
         """
         if ltf_bias == htf_bias:
             return 1.0
         elif ltf_bias == "neutral":
             return 0.7
         return 0.4
     ```

  2. **Replace the LTF adjustment loop** inside `composite_score()`. Lines 293–316 (current additive loop) get replaced with:
     ```python
         confidence_multiplier = 1.0
         factor_labels = {1.0: "aligned", 0.7: "partially aligned", 0.4: "conflicting"}

         for ltf_name, ltf_snapshot in ltf_pairs:
             ltf_result = scorer.score(ltf_snapshot)
             ltf_bias = ltf_result.bias
             factor = self._alignment_factor(base_bias, ltf_bias)
             confidence_multiplier *= factor
             reasons.append(
                 f"{ltf_name}: {ltf_bias} (score={ltf_result.score}) "
                 f"— {factor_labels[factor]}, factor={factor}"
             )

         final_score = float(htf_result.score) * confidence_multiplier

         return ConfluenceResult(
             bias=base_bias,
             score=final_score,
             max_score=10,
             reasons=reasons,
         )
     ```

  3. **Update `composite_score` docstring** (line 252): Change LTF description to "apply multiplicative alignment factors (1.0/0.7/0.4) via `_alignment_factor()`".

  4. **Update `regime_alignment` docstring** (line 331): Add note: "This property detects conflicts (opposite biases) only. Partial alignment (factor=0.7, when LTF is neutral) does NOT count as a conflict."

  **Must NOT do**:
  - ❌ Do NOT modify `legacy_composite_score()` (lines 207–247)
  - ❌ Do NOT modify `ConfluenceResult` dataclass or `ConfluenceScorer.score()`
  - ❌ Do NOT change the HTF selection logic (daily > h4 > h1 priority)
  - ❌ Do NOT change `max_score` from 10
  - ❌ Do NOT change `regime_alignment` behavior — only its docstring

  **Recommended Agent Profile**: python-engineer — experienced with SMC hierarchical scoring

  **Parallelization**: Wave 1 (no deps)

  **References**:
  - `confluence.py` lines 249–323 (current `composite_score()`)
  - `confluence.py` line 326+ (`regime_alignment` property)
  - Interview-confirmed mapping: `elif ltf_bias == "neutral": return 0.7`

  **Acceptance Criteria** (agent-executed):
  ```bash
  # 1. No syntax errors
  python -c "from confluence import MarketContext; ctx = MarketContext(); print('Import OK')"
  # 2. legacy_composite_score still works
  python -m pytest tests/test_market_snapshot.py::TestMarketContext -v --tb=short -x
  # 3. ConfluenceScorer unchanged (single-TF tests pass)
  python -m pytest tests/test_market_snapshot.py::TestConfluenceScorer -v --tb=short -x
  ```

  **QA Scenarios**:

  1. **HTF only (no LTFs)** → `score == 10.0`, `bias == "bullish"`
     - **Tool**: `interactive_bash`
     - **Steps**: `python -c "from confluence import MarketContext; from tests.conftest import _make_daily_bullish; ctx = MarketContext(daily=_make_daily_bullish()); r = ctx.composite_score(); assert r.bias == 'bullish' and r.score == 10.0 and r.max_score == 10"`
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t1-htf-only.txt`

  2. **Conflicting LTF** → `score == 4.0` (10 × 0.4)
     - **Tool**: `interactive_bash`
     - **Steps**: `python -c "from confluence import MarketContext; from tests.conftest import _make_daily_bullish, _make_h4_bearish; ctx = MarketContext(daily=_make_daily_bullish(), h4=_make_h4_bearish()); r = ctx.composite_score(); assert r.bias == 'bullish' and r.score == 4.0 and 'conflicting' in str(r.reasons)"`
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t1-conflicting-LTF.txt`

  3. **Regression — legacy_composite_score unchanged**
     - **Tool**: `interactive_bash`
     - **Steps**: `python -m pytest tests/test_market_snapshot.py::TestMarketContext -v --tb=short -x`
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t1-legacy-regression.txt`

  4. **Reasons format** — reasons contain "conflicting" + "factor=0.4"
     - **Tool**: `interactive_bash`
     - **Steps**: `python -c "from confluence import MarketContext; from tests.conftest import _make_daily_bullish, _make_h4_bearish; ctx = MarketContext(daily=_make_daily_bullish(), h4=_make_h4_bearish()); r = ctx.composite_score(); assert any('conflicting' in x for x in r.reasons) and any('factor=0.4' in x for x in r.reasons)"`
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t1-reasons.txt`

- [ ] **T2. Add `TestHierarchicalMTF` class in `tests/test_market_snapshot.py`**

  **What to do**: Append a new test class `TestHierarchicalMTF` at the end of `tests/test_market_snapshot.py`. Wire in MTF fixtures from `conftest.py`. Test the multiplicative model with `pytest.approx()`.

  **DO NOT create a new file**.

  **Test matrix** (exact expected values computed from fixture data — implementing agent MUST verify by running `ConfluenceScorer().score()` on each fixture):

  | Test method | Fixture | Multiplier | Expected score | Expected bias |
  |------------|---------|-----------|----------------|---------------|
  | `test_all_aligned` | `mtx_no_h1` (daily bullish, h4 bullish) | 1.0 | 10.0 | bullish |
  | `test_one_neutral_ltf` | `mtx_bullish_daily` (daily bullish, h4 bullish, h1 neutral) | 0.7 | 7.0 | bullish |
  | `test_one_conflicting_ltf` | `mtx_conflicting_h4` (daily bullish, h4 bearish) | 0.4 | 4.0 | bullish |
  | `test_both_conflicting` | `mtx_conflicting_both` (daily bullish, h4 bearish, h1 neutral) | 0.28 | 2.8 | bullish |
  | `test_no_daily_htf_is_h4` | `mtx_no_daily` (h4 bullish, h1 neutral) | 0.7 | 7.0 | bullish |
  | `test_no_ltfs_single_tf` | `MarketContext(daily=_make_daily_bullish())` | 1.0 | 10.0 | bullish |

  **Additional test for neutral HTF case**: Create inline MarketSnapshot with score=5 (neutral), add conflicting LTF:
  ```python
  daily_neutral = MarketSnapshot(
      symbol="BTC/USDT", timeframe="1d", timestamp=x,
      close=50000.0, trend_direction="above",
      ema21=49000.0, ema21_slope=0.01, rsi14=50.0, mfi14=50.0,
      macd=100.0, macd_signal=90.0, macd_hist=10.0,
      atr14=500.0, bb_width=0.05, nearest_liquidity_above=51000.0,
  )
  # Score = 5 → neutral
  ctx = MarketContext(daily=daily_neutral, h4=_make_h4_bearish())
  # Multiplier = 0.4 (h4 bearish conflicts with neutral HTF)
  # Expected: bias="neutral", score=2.0 (5*0.4)
  ```

  **Must NOT do**:
  - ❌ Do NOT modify any existing test methods in `TestMarketContext`
  - ❌ Do NOT modify `legacy_composite_score()` tests
  - ❌ Do NOT hardcode expected scores without verifying against actual scorer output

  **Recommended Agent Profile**: python-tester — pytest fixture expert

  **Parallelization**: Wave 2 (blocked by T1)

  **References**:
  - `tests/test_market_snapshot.py` (append after `TestIntegration`)
  - `tests/conftest.py` lines 409–478 (MTF fixtures)

  **Acceptance Criteria**:
  ```bash
  python -m pytest tests/test_market_snapshot.py::TestHierarchicalMTF -v --tb=short -x
  ```

  **QA Scenarios**:

  1. **All new tests pass**
     - **Tool**: `interactive_bash`
     - **Steps**: `python -m pytest tests/test_market_snapshot.py::TestHierarchicalMTF -v --tb=short`
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t2-all-tests-pass.txt`

  2. **Existing MarketContext tests unaffected**
     - **Tool**: `interactive_bash`
     - **Steps**: `python -m pytest tests/test_market_snapshot.py::TestMarketContext -v --tb=short`
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t2-legacy-regression.txt`

  3. **Uses pytest.approx for float assertions**
     - **Tool**: `interactive_bash`
     - **Steps**: `grep -c "approx" tests/test_market_snapshot.py`
     - **Expected**: At least 6 occurrences in TestHierarchicalMTF
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t2-approx-usage.txt`

- [ ] **T3. Run full test suite and verify no regressions**

  **What to do**: Execute the full test suite. Fix any failures (none expected). Create evidence files.

  **Must NOT do**:
  - ❌ Do NOT modify any source code at this step (if tests fail, revert to earlier tasks)

  **Recommended Agent Profile**: unspecified-high — test runner / triage

  **Parallelization**: Wave 3 (blocked by T1, T2)

  **Acceptance Criteria**:
  ```bash
  python -m pytest tests/ -v 2>&1 | tail -20
  ```

  **QA Scenarios**:

  1. **Full suite passes** → 0 failed, 0 errors (pre-existing skips allowed)
     - **Tool**: `interactive_bash`
     - **Steps**: `python -m pytest tests/ -v 2>&1`
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t3-full-suite.txt`

  2. **Decision engine tests still pass**
     - **Tool**: `interactive_bash`
     - **Steps**: `python -m pytest tests/test_decision_engine.py -v --tb=short`
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t3-decision-engine.txt`

  3. **Narrative tests still pass**
     - **Tool**: `interactive_bash`
     - **Steps**: `python -m pytest tests/test_narrative.py -v --tb=short`
     - **Evidence**: `.sisyphus/evidence/fix-mtf-multiplicative/t3-narrative.txt`

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** (oracle)
  - Verify all guardrails respected: no changes to `market_snapshot.py`, `decision_engine.py`, `narrative.py`
  - Verify `legacy_composite_score()` unchanged
  - Verify all 3 tasks completed per spec

- [ ] F2. **Code Quality Review** (unspecified-high)
  - Read final `confluence.py:composite_score()` and `_alignment_factor()`
  - Verify factor mapping matches spec: 1.0/0.7/0.4
  - Verify reasons mention alignment label + factor

- [ ] F3. **Real Manual QA** (unspecified-high)
  - Execute all QA scenarios from T1 and T2 evidence paths
  - Confirm `.sisyphus/evidence/fix-mtf-multiplicative/` contains all evidence files

- [ ] F4. **Scope Fidelity Check** (deep)
  - Confirm no downstream files were touched (`git diff --stat`)
  - Confirm only `confluence.py` and `tests/test_market_snapshot.py` have changes

## Commit Strategy
- Single commit after all 3 tasks + final verification pass.
- Message: `feat(confluence): replace additive MTF scoring with multiplicative confidence model`
- Scope: `confluence.py` + `tests/test_market_snapshot.py` only

## Success Criteria
- [ ] `python -m pytest tests/ -v` → 0 failed, 0 errors
- [ ] `python -m pytest tests/test_market_snapshot.py::TestHierarchicalMTF -v` → all new tests pass
- [ ] `python -m pytest tests/test_market_snapshot.py::TestConfluenceScorer -v` → all pass (unchanged)
- [ ] `python -m pytest tests/test_market_snapshot.py::TestMarketContext -v` → all pass (unchanged)
- [ ] `python -m pytest tests/test_decision_engine.py -v` → all pass
- [ ] `python -m pytest tests/test_narrative.py -v` → all pass
- [ ] `git diff --stat` shows changes only in `confluence.py` and `tests/test_market_snapshot.py`
- [ ] Evidence directory populated: `.sisyphus/evidence/fix-mtf-multiplicative/` with 10+ files
