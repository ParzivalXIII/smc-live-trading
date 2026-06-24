# Oracle Analysis — Direction/Confidence Split

**Date:** 2026-06-24
**Requested by:** User (post-MTF-multiplicative-fix)
**Scope:** Separate `direction_score` from `confidence` in `ConfluenceResult`

---

## Bottom Line

**Yes, the separation is worth doing.** The user's analysis is correct — `score=-0.96` conflates two semantically distinct concepts (directional strength vs. execution quality), creating a dashboard readability problem that could lead to mistaken trading judgments. The fix is moderate in scope (~6 files, ~60-80 line changes) and produces a strictly more honest data model. **Do it now** — before any downstream consumers (dashboard, strategy scripts) hardcode the current conflation. This is a window of low migration cost; waiting increases it.

---

## Semantic Analysis

### Why `score=-0.96` Is Misleading

The current `composite_score()` produces:

```
final_score = htf_score × product_of_ltf_alignment_factors
```

This single number encodes **two distinct things**:

| Component | What It Expresses | Example Value |
|-----------|-------------------|---------------|
| `sign(htf_score)` | Directional bias (bullish/bearish) | negative = bearish |
| `abs(htf_score)` | HTF conviction strength | -6.0 = strong bearish |
| `product(factors)` | LTF alignment quality (0.16-1.0) | 0.16 = 2 conflicting LTFs |

When these are multiplied: `-6.0 × 0.16 = -0.96`.

A human reading `score=-0.96` with `max_score=10` naturally interprets this as **"barely bearish"** — roughly 10% conviction. The dashboard will plot it near zero. But the reality is the opposite: **strongly bearish + poor alignment**. These suggest different trading responses:

| Scenario | What to Do |
|----------|-----------|
| Weak bearish (true conviction ~0.96/10) | Stand aside, no edge |
| Strong bearish + poor alignment (direction=-6, conf=16%) | Avoid shorts (regime says down) but don't enter a short (LTFs unclear). Watch for alignment improvement. |

The conflation leads to the same action `avoid_shorts` in both cases, but the **risk assessment** differs. The trader needs to know: *do I stay out because there's no edge, or stay out because the timing sucks?*

### What the Split Fixes

Proposed `ConfluenceResult`:

```python
@dataclass
class ConfluenceResult:
    bias: str               # "bullish" / "bearish" / "neutral"
    direction_score: float  # Raw HTF score (e.g., -6.0)
    confidence: float       # 0.0 to 1.0 — LTF alignment quality
    max_score: int = 10     # Applicable to direction_score
    reasons: list[str] = field(default_factory=list)
```

Dashboard rendering: `Bias: Bearish | Direction: -6.0 | Confidence: 16%`

A trader reads this and immediately understands: *"Daily structure is solidly bearish (direction=-6), but LTFs are fighting it (confidence=16%). I should avoid shorts as a regime trade, but I won't be aggressive — wait for LTF alignment to improve before considering entries."*

Contrast with current: `Score: -0.96 (bearish)` — a trader might miss the nuance entirely.

---

## Impact Assessment

### 1. `ConfluenceScorer.score()` — Single-TF Scoring (unchanged semantics)

Currently returns: `ConfluenceResult(bias, score, max_score, reasons)`

With split: `ConfluenceResult(bias, direction_score=score, confidence=1.0, max_score, reasons)`

- Single-TF always has `confidence=1.0` (no LTFs to disagree)
- `direction_score` is the same raw score as before
- **No behavioral change.** Tests pass without modification (if `score` is kept as a backward-compat property).

### 2. `MarketContext.composite_score()` — MTF Scoring (biggest change)

Currently:

```python
final_score = round(htf_score * confidence, 2)
return ConfluenceResult(bias=base_bias, score=final_score, max_score=10, ...)
```

With split:

```python
return ConfluenceResult(
    bias=base_bias,
    direction_score=htf_score,    # ← raw HTF score, NOT reduced
    confidence=confidence,         # ← product of LTF factors directly
    max_score=10,
    ...
)
```

This is the **core fix**. The HTF score passes through unmutilated. Confidence becomes a separate dimension.

**Key implication:** Everything downstream that currently reads `result.score` will now get the RAW HTF score (e.g., -6.0) instead of the reduced value (e.g., -0.96). This requires updating `DecisionEngine` thresholds and `MarketNarrative` pass-through.

### 3. `DecisionEngine.decide()` — Action Mapping & Confidence

**Changes required:**

| Concern | Current (`result.score`) | With Split (`result.direction_score` + `result.confidence`) |
|---------|------------------------|-------------------------------------------------------------|
| Confidence formula | `abs(result.score) / max_score` | `min(1.0, result.confidence)` — just pass it through |
| Action: bullish | `>= 7` → `look_for_longs` | `direction_score >= 7` AND `confidence > threshold` → `look_for_longs` |
| Action: neutral (4-6) | `stand_aside` + breakout check | Same, from `direction_score` |
| Action: weak (0-3) | `stand_aside` | Same, from `direction_score` |
| Action: bearish (< 0) | `avoid_shorts` | `direction_score < 0` → `avoid_shorts` (may optionally be modulated by confidence) |

**Critical design decision:** Should confidence modulate actions?

- **Conservative approach (recommended):** Use `direction_score` for action thresholds (as now, just with raw values). Confidence modulates only the `Decision.confidence` field, leaving action selection unchanged. This is the minimal behavioral delta.
  - Pro: Zero surprise in action output. `avoid_shorts` still fires for any bearish regime.
  - Con: A bearish(-6)/conf(0.16) and bearish(-6)/conf(1.0) both produce `avoid_shorts`. The difference is only in the confidence number.

- **Progressive approach:** Add confidence gates. E.g., `look_for_longs` requires `confidence > 0.3` even with `direction_score >= 7`. Bearish with `confidence < 0.2` becomes `stand_aside` instead of `avoid_shorts`.
  - Pro: More nuanced decisions. No trade recommendation when alignment is garbage.
  - Con: Behavioral regression. Tests break. The user must decide on new thresholds.

**Recommendation:** Start with **conservative** (match existing behavior). Add confidence gates as a separate follow-up if desired.

### 4. `MarketNarrativeBuilder.build()` — Display

Currently:

```python
MarketNarrative(
    bias=result.bias,
    score=result.score,
    max_score=result.max_score,
    sections=sections,
    conclusion=conclusion,
)
```

With split, `MarketNarrative` gains a `confidence` field. The `score` field can either:
- **(a)** Stay as-is, renamed to `direction_score` (breaking, requires test updates)
- **(b)** Stay as `score`, with `MarketNarrative.direction_score` as an alias/property

Option (a) is cleaner. `MarketNarrative` becomes:

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

The conclusion generator uses only `bias` — unchanged.

### 5. Backward Compatibility

**Direct `result.score` references (counted):**

| File | Lines | Usage |
|------|-------|-------|
| `confluence.py` | 3 (229, 231, 286) | Self-references in legacy_composite_score and composite_score |
| `decision_engine.py` | 4 (88, 95, 97, 108) | Confidence formula + action thresholds |
| `narrative.py` | 1 (99) | Pass-through to MarketNarrative |
| `test_market_snapshot.py` | 27 | Assertions on `result.score` |
| `test_narrative.py` | 3 | `narrative.score` assertions |
| **Total** | **38** | |

**All other `ConfluenceResult` field accesses:**

| Field | References | Impact |
|-------|-----------|--------|
| `.bias` | 15+ (prod + tests) | Unchanged |
| `.max_score` | ~5 | Unchanged, stays paired with direction_score |
| `.reasons` | ~3 | Unchanged |
| `Decision.confidence` | 10 (prod + tests) | **Will change value** — currently `abs(score)/max_score`, will become `result.confidence` directly |

**Deprecation path:** Adding `@property score(self) -> float: return self.direction_score` to `ConfluenceResult` requires removing `slots=True`. This is a one-line change. The property preserves the signed return value (but now returns raw HTF score, not the reduced product — so consumers *will* see different magnitudes).

**Migration strategy:** The cleanest approach is to **not use a compat property at all**. Migrate all 38 references in one pass. This is a ~15-minute mechanical change with no behavioral ambiguity.

---

## Confidence Normalization Analysis

### Current Confidence Floor

| # LTFs | Best Case | Worst Case |
|--------|-----------|------------|
| 0 | 1.0 | 1.0 |
| 1 | 1.0 | 0.4 |
| 2 | 1.0 | 0.16 |
| 3 | 1.0 | 0.064 |

### Is This a Problem?

**No, it's fine.** The product model is inherently meaningful: each additional conflicting LTF exponentially reduces confidence. The absolute floor doesn't need to hit 0.0 because:

1. **Relative comparison is what matters.** 0.16 vs 1.0 is a clear signal of "much worse."
2. **Normalizing would hide information.** If you normalize to 0.0, you lose the distinction between "1 LTF conflicts" (0.4 → normalized to something) and "2 LTFs conflict" (0.16 → also normalized to something). Better to keep the raw product.
3. **The DecisionEngine already clamps confidence to 0.0-1.0.** Just pass the product through. It naturally lives in the right range.

### One Subtle Issue: Decision.confidence Will Now Be Higher

Current: `confidence = abs(-0.96) / 10 = 0.096`
Proposed: `confidence = 0.16`

The proposed value (0.16) is **more correct** — it reflects the actual LTF alignment (2 conflicting LTFs = 0.4×0.4 = 0.16). The old formula was double-penalizing: first by multiplying the score by 0.16, then by dividing by max_score again.

**Test impact:** `test_decision_engine.py` lines 42, 56, 287, 347, 374, 535 and `test_narrative.py` lines 436, 510, 553 assert `decision.confidence` values. These will change.

### Concrete Mapping (Old → New Confidence Values)

| Scenario | Old `result.score` | Old `conf` (abs(s)/max) | New `result.confidence` |
|----------|-------------------|------------------------|------------------------|
| Single-TF bearish (-4) | -4 | 0.4 | 1.0* |
| Single-TF bullish (10) | 10 | 1.0 | 1.0* |
| Bullish(10), H4 conflicts | 4.0 | 0.4 | 0.4 |
| Bullish(10), both conflict | 2.8 | 0.28 | 0.28 |
| Bearish(-6), both conflict | -0.96 | 0.096 | 0.16 |

\*Single-TF: currently `conf = abs(score)/10`. With split, all single-TF gets `confidence=1.0` because there are no LTFs to disagree. This is actually **more honest** — the single-TF signal is pure direction with no alignment penalty. The old formula penalized single-TF scores below 10 for no reason.

---

## Migration Scope

### Files to Change

| File | Changes | Lines Touched |
|------|---------|---------------|
| `confluence.py` | ConfluenceResult: rename `score`, add `confidence`, remove `slots=True` (if compat property), update `composite_score()` return, update `legacy_composite_score()` self-refs, update `ConfluenceScorer.score()` return | ~10 |
| `decision_engine.py` | Confidence formula (L88), action threshold refs (L95-111) | ~6 |
| `narrative.py` | MarketNarrative: add `confidence`, update builder | ~6 |
| `test_market_snapshot.py` | 27 assertions: `result.score` → `result.direction_score` | ~27 |
| `test_narrative.py` | 3 assertions + confidence test updates | ~5 |
| `test_decision_engine.py` | 7 confidence assertions change values | ~7 |

### Total

- **6 files**
- **~60 line changes** (mostly mechanical find-and-replace in tests)
- **Effort estimate:** `Short(1-4h)` — 90% is mechanical. The only thoughtful decisions are:
  1. Whether to use compat property or full migration
  2. Whether confidence modulates actions or just passes through

---

## Recommendations

### 1. Do the Split Now

**Rationale:** The MTF multiplicative fix (completed yesterday) introduced the semantic gap. The system is still young — only 3 test files, no real dashboard, no strategy scripts consuming `score`. The migration cost is at its absolute minimum. Every day the conflation persists, more code will be written against the wrong mental model.

### 2. Use the Conservative Action Approach

Keep action mapping based on `direction_score` thresholds (matching current behavior after adjusting for raw values). Let confidence be a pure signal in `Decision.confidence`. Do not add confidence-gated actions in this pass. That is a separate design discussion with user sign-off needed on thresholds.

### 3. Skip the Backward-Compatible `score` Property

Remove `slots=True` from `ConfluenceResult`, add `@property score → self.direction_score` if you want zero test breakage. **But:** this property will return RAW HTF values (e.g., -6.0) instead of reduced values (e.g., -0.96), so the tests that currently assert `result.score == 2.8` (MTF) would still break. The property only helps for single-TF tests — and those tests should just be migrated to `.direction_score` anyway.

**Better approach:** Just migrate everything. 27 test assertions with a find-and-replace is faster than designing a compat layer.

### 4. Open Question for the User

> Should `Decision.action` be modulated by confidence, or should confidence live purely in the `Decision.confidence` field?

Current behavior (with old conflation) effectively reduced actions for low-confidence scenarios. The split breaks that implicit coupling. The user should explicitly choose:

- **Conservative (match existing):** Action from `direction_score` thresholds, confidence in separate field. A bearish regime always yields `avoid_shorts` regardless of confidence.
- **Progressive (confidence-gated):** Low confidence can downgrade actions (e.g., bearish + conf<0.2 → `stand_aside` instead of `avoid_shorts`).

This is the one decision that requires user input before implementation.

---

## Optional Future Consideration

The `max_score` field maps naturally to `direction_score` (range -4 to 10). `confidence` is self-range 0.0-1.0. No changes needed there. If a normalized "combined conviction" score is ever needed for a chart widget, it can be derived as `direction_score * confidence` — the old formula — at display time without storing it.
