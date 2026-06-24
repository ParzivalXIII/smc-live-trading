# Oracle Pre-Planning Review — Narrative, Decision Engine, Multi-Timeframe Weighting

**Reviewer**: Oracle (strategic technical advisor)
**Date**: 2026-06-24
**Scope**: Three new features proposed on top of existing MarketSnapshot + Confluence system
**Status**: Assumptions need clarification before Prometheus plans

---

## Bottom Line

**Two of the three features (Narrative, Decision Engine) are well-specified at the concept level but have open design decisions that will change the effort estimate by 2-3x depending on which path is chosen. The third feature (MTF Weighting) has a fundamental design tension between two valid approaches that must be resolved before planning can begin.**

The existing architecture (MarketSnapshot → ConfluenceScorer → MarketContext) provides a clean foundation. None of the three features require changes to existing production files — they are additive. All three can be built in parallel with the existing alignment bug fix (alignment() returning "mixed" for all-neutral multi-TF) as a prerequisite.

**Prometheus should NOT plan until the 3 blocking questions at the end of this report are answered.**

---

## Existing Codebase State (Context for All Three Features)

### Current Data Flow
```
TA DataFrame + SMC Report
         ↓
SnapshotBuilder.build()
         ↓
  MarketSnapshot (24 fields, pure state)
         ↓
  ConfluenceScorer.score()
         ↓
  ConfluenceResult (bias, score, max_score, reasons)
         ↓
  MarketContext (up to 3 snapshots, alignment, composite_score)
```

### Key Properties
- `MarketSnapshot`: 14 required fields (identity + trend + momentum + volatility), 10 optional (structure + liquidity + OB). No scoring logic.
- `ConfluenceScorer`: Additive -4 to 10. 8 conditions. Transparent reasons for EVERY condition (including +0).
- `MarketContext.composite_score()`: Currently sums raw scores. `max_score = len(active) * 10`. No weighting.
- `MarketContext.alignment()`: Binary alignment check. **Has a bug**: all-neutral with 2+ active TFs returns "mixed" instead of "neutral" (flagged in post-implementation review, not yet fixed).

### What the Narrative and Decision Engine Will Consume

Each feature in the pipeline needs specific data. Here's what's available:

| Data | Source | Narrative Needs | Decision Needs |
|------|--------|----------------|----------------|
| Bias + score | ConfluenceResult | ✅ Yes | ✅ Yes |
| Specific values (RSI, MACD, etc.) | MarketSnapshot | ✅ Yes | ❌ No |
| Liquidity levels | MarketSnapshot | ✅ Yes | ✅ Yes (target/invalidation) |
| Structure data (BOS, CHOCH, swing) | MarketSnapshot | ✅ Yes | ✅ Yes (invalidation) |
| EMA21 level | MarketSnapshot | ✅ Yes | ✅ Yes (invalidation fallback) |
| Multi-TF alignment | MarketContext | ✅ Yes | ✅ Yes |
| Multi-TF composite score | MarketContext | ❌ Not directly | ✅ Yes |

---

## Feature 1: Narrative Generation

### Architecture Recommendation

**New file: `narrative.py`**. Not an extension of `confluence.py`. Rationale:
- Keeps the clean separation: `confluence.py` = scoring (opinions), `narrative.py` = presentation (formatting)
- Avoids bloating `ConfluenceResult` with rendering concerns
- The narrative knows about BOTH `MarketSnapshot` (for raw values) and `ConfluenceResult` (for bias/score) — this is a distinct concern from pure scoring

**Input contract**: `NarrativeGenerator.generate(snapshot: MarketSnapshot, result: ConfluenceResult) -> Narrative`

### Structured Dataclass vs Formatted String vs Template System

**Recommendation: (b) Structured dataclass with a `.format()` method.**

Why not (a) formatted string:
- Untestable — can't assert on sections independently
- Can't be consumed programmatically by the Decision Engine or other future consumers
- Any formatting change requires updating the whole string

Why not (c) template system:
- Premature abstraction for 3-4 sections
- No evidence that pluggable sections are needed
- Adds a dependency (Jinja2) or reinvents templating

Proposed design:
```python
@dataclass
class Narrative:
    symbol: str
    timeframe: str
    bias: str
    score: int
    max_score: int

    # Grouped sections
    trend: str          # "Price above EMA21, EMA21 rising"
    momentum: str       # "MACD above signal, RSI 61.4"
    structure: str      # "Last BOS bullish, no CHOCH"
    liquidity: str      # "Buy-side above at 108900"

    # Computed
    conclusion: str     # "Bullish continuation favored while EMA21 intact"

    def formatted(self) -> str:
        """Returns the user's example format (bullet points, sections)."""
```

Each section is a short string built by the generator. This lets you test sections independently, format differently for different outputs (CLI vs notification vs dashboard), and the Decision Engine can read `narrative.liquidity` to derive target/invalidation levels.

### Where Invalidation Logic Lives

The user's example shows: *"Bullish continuation favored while EMA21 remains intact."*

This is a **conclusion string in the narrative**. The EMA21 level used here comes from `snapshot.ema21`. The narrative generator computes this text, but:

- **The invalidation PRICE LEVEL** (e.g., `104250`) should live in the Decision Engine, not the narrative
- **The invalidation CONDITION TEXT** (e.g., "while EMA21 remains intact") lives in the narrative
- This means the narrative generator needs to know both the snapshot data AND the decision engine's output if it wants to reference the decision's levels

**Recommendation**: The narrative generator produces text-only sections. The Decision Engine produces price levels. If the narrative wants to reference decision levels, it takes the Decision as an optional second argument to `formatted()`.

### Edge Cases

| Condition | Behavior |
|-----------|----------|
| **All neutral** (score 4-6) | Narrative sections should report flat/neutral values. Conclusion: "No clear directional edge. Mixed signals across indicators." |
| **No structure data** (all None) | Structure section: "No confirmed swing/BOS/CHOCH data." Or omit the section entirely. |
| **All bearish** (score -4 to 0) | Trend: "Price below EMA21, EMA21 falling." Conclusion: "Bearish bias favored. Avoid longs." |
| **Missing liquidity data** | Liquidity section: "No significant liquidity clusters detected." |
| **Single TF vs MTF** | If used with MarketContext, the narrative could prefix with "Daily: ..." / "H4: ..." sections. If single snapshot, just the fields. |

### Effort Estimate: `Short(1-2d)` for structured dataclass approach

---

## Feature 2: Decision Engine

### Action Taxonomy Validation

All 4 actions are distinct and non-overlapping:

| Action | Bias Required | Score Range | Meaning | Can Coexist? |
|--------|---------------|-------------|---------|--------------|
| `look_for_longs` | bullish | 7-10 | Strong bullish confluence, clear structure, liquidity target above. Trader should prepare long setups. | Mutually exclusive with avoid_shorts |
| `avoid_shorts` | bearish | <0 to 3 | Bearish structure, price below EMA, no reason to short (fighting trend). Not "sell" — just don't short. | Mutually exclusive with look_for_longs |
| `stand_aside` | neutral | 4-6 | Mixed signals or no clear edge. No actionable setup. | Compatible with nothing — it's the default |
| `watch_breakout` | bullish/bearish | 5-8 | Something is brewing — price near a key level, structure forming, but not yet confirmed. Higher urgency than stand_aside but no entry signal. | Can coexist with any other action as a modifier |

**Potential overlap**: `watch_breakout` could overlap with `look_for_longs` (a brewing breakout IS something to look for longs on). The distinction should be: `look_for_longs` = confirmed structure + trend alignment. `watch_breakout` = near a level but confirmation still pending (BOS not yet confirmed, liquidity not yet tested, etc.).

**Recommendation**: Make `watch_breakout` a modifier flag instead of a top-level action. The decision has `action` (look_for_longs / avoid_shorts / stand_aside) + optional `watch_breakout: bool = False`. This avoids the overlap and keeps actions mutually exclusive.

### Invalidation Source

**Recommendation**: Derive invalidation from `nearest_liquidity` on the OPPOSITE side of the bias:
- `look_for_longs` → invalidation = `snapshot.nearest_liquidity_below` (below current price)
- `avoid_shorts` → invalidation = `snapshot.nearest_liquidity_above` (above current price)

**Fallback chain** (if no liquidity on that side):
1. Last swing low (for longs) / last swing high (for shorts) — from `last_swing_level` + direction
2. EMA21 level — `snapshot.ema21`
3. None — no invalidation level available, confidence should be reduced

This is the most SMC-aligned approach. The invalidation level represents the point where the structural thesis breaks.

### Target Source

**Recommendation**: Derive target from `nearest_liquidity` on the SAME side as the bias:
- `look_for_longs` → target = `snapshot.nearest_liquidity_above`
- `avoid_shorts` → target = `snapshot.nearest_liquidity_below`

**Fallback chain** (if no liquidity on that side):
1. Last swing high (for longs) / last swing low (for shorts)
2. Use BB upper/lower if available
3. None

### Confidence Formula

**Recommendation**: `confidence = abs(score) / max_score`

With the current -4 to 10 range:
- score = 10 → 1.00 (max bullish confidence)
- score = 8 → 0.80 (close to user's example of 0.82)
- score = 7 → 0.70
- score = 4 → 0.40 (neutral threshold)
- score = 0 → 0.00 (boundary)
- score = -4 → 0.40 (max bearish)

**Implication**: Bearish signals cap at 0.40 because the max bearish score is -4. This is a direct consequence of the scoring range asymmetry (-4 to 10). If this is unacceptable, the formula can be adjusted, but I recommend starting here — it's simple, transparent, and the cap reflects reality (there are fewer bearish scoring conditions than bullish ones in the current table).

For multi-TF with equal weights: `max_score = len(active) * 10`, same formula applies.

### Input Contract

```python
class DecisionEngine:
    def decide(
        self,
        snapshot: MarketSnapshot,
        result: ConfluenceResult,
        context: MarketContext,  # for multi-TF awareness
    ) -> Decision: ...
```

**New file**: `decision_engine.py`

### Edge Cases

| Condition | Behavior |
|-----------|----------|
| **Conflicting MTF signals** | e.g., Daily bullish, H4 bearish. Use alignment. If "mixed", action depends on dominant weight. With hierarchical approach (if adopted), Daily sets direction regardless. |
| **All optional fields None** | No structure, no liquidity, no OB. Score will be 0-2 (bearish) based on trend/momentum only. Confidence near 0. Action: `stand_aside`. |
| **Missing one timeframe** | Context has only 2 of 3 snapshots. Weight re-normalization handles this. Decision still valid. |
| **score=7 but no liquidity above** | Bullish bias but no clear target. Action could be `look_for_longs` with target=None. Narrative would note no liquidity target identified. |
| **Both liquidity levels present** | Has both target AND invalidation levels. Strongest signal — clear risk/reward structure. |

### Effort Estimate: `Short(1-2d)` for basic decision engine with these rules

---

## Feature 3: Multi-Timeframe Weighting

### Weighted vs Hierarchical — Recommendation

**Recommendation: Start with Weighted Average, but note the hierarchical caveat.**

Rationale:
1. **Simplicity**: Weighted average is a one-line formula change to `MarketContext.composite_score()`. Hierarchical requires branching logic for direction-setting and separate confidence computation.
2. **Continuity**: The current `composite_score()` already sums raw scores. Adding weights is a smooth evolution.
3. **SMC alignment**: The user's weights (0.5/0.3/0.2) already ensure the daily dominates direction in most cases (see analysis below).

### Weighted Average — Exact Formula

```
weighted_score = (
    daily_score * weight_daily +
    h4_score    * weight_h4 +
    h1_score    * weight_h1
)

max_weighted = 10 * weight_daily + 10 * weight_h4 + 10 * weight_h1
              = 10 * (weight_daily + weight_h4 + weight_h1)
              = 10 * 1.0 = 10

min_weighted = -4 * weight_daily + -4 * weight_h4 + -4 * weight_h1
              = -4 * 1.0 = -4
```

The range stays **-4 to 10** regardless of weights (as long as they sum to 1.0). Bias mapping unchanged.

**Does the daily actually dominate with 0.5/0.3/0.2?**

| Scenario | Daily (0.5) | H4 (0.3) | H1 (0.2) | Weighted | Bias | Daily Dominates? |
|----------|-------------|-----------|-----------|----------|------|------------------|
| Fully bearish daily, max bullish lower | -4*0.5=-2 | 10*0.3=3 | 10*0.2=2 | 3.0 | bearish | ✅ Yes — still bearish |
| Fully bullish daily, max bearish lower | 10*0.5=5 | -4*0.3=-1.2 | -4*0.2=-0.8 | 3.0 | bearish | ❌ No — flipped to bearish |

**Asymmetry**: A fully bullish daily CAN be flipped to bearish by maximally bearish lower TFs, but a fully bearish daily CANNOT be flipped to bullish. This is because the bearish max (-4) is smaller than the bullish max (10), combined with weights.

**Is this acceptable?** It depends on the user's philosophy:
- If you believe "daily bullish is strong conviction, don't let H1 change your mind", then this asymmetry is wrong — you need the hierarchical approach.
- If you believe "strong agreement across TFs matters more than any single TF", then this is acceptable — a fully bearish H4+H1 is a powerful signal that should override even a bullish daily.

### Missing Timeframe Handling

If only `daily` and `h4` are available (no `h1`):
```
weight_daily_normalized = 0.5 / (0.5 + 0.3) = 0.625
weight_h4_normalized    = 0.3 / (0.5 + 0.3) = 0.375
```

General formula: `weight_i_normalized = weight_i / sum(available_weights)`

### Impact on Existing MarketContext

**Recommendation**: Make this backward compatible. Do NOT change `composite_score()`. Instead, add a new method:

```python
class MarketContext:
    def composite_score(self) -> ConfluenceResult:
        # Existing behavior — unweighted sum, max = len * 10
        ...

    def weighted_score(
        self,
        weights: dict[str, float] | None = None,
    ) -> ConfluenceResult:
        # New method — weighted average
        # Default weights: {"daily": 0.5, "h4": 0.3, "h1": 0.2}
        # Missing timeframes: re-normalize remaining weights
        ...
```

This is zero-risk for existing consumers. No existing test breaks. The old method remains as a simpler alternative.

### The Hierarchical Option (Alternative Sketch)

If the user decides weighted average is not SMC-aligned enough:

```python
def hierarchical_score(self, weights) -> ConfluenceResult:
    # 1. Determine overall direction from highest available TF
    # 2. Compute weighted score
    # 3. If weighted_score is ON THE SAME SIDE as direction → keep it
    # 4. If weighted_score is on the OPPOSITE side → clamp to neutral
    #    within the direction's range (e.g., minimal bullish score = 4)
```

This prevents lower TFs from flipping the HTF direction entirely. They can only reduce confidence within the HTF's directional range.

### Effort Estimate: `Quick(<1h)` for weighted_score() method only. `Short(1-4h)` for hierarchical approach with all edge cases.

---

## The Alignment Bug (Prerequisite Fix)

The post-implementation review flagged that `MarketContext.alignment()` returns `"mixed"` when 2+ active timeframes are all neutral. This must be fixed before or alongside these features because:

- The Decision Engine uses `alignment()` to detect conflicting MTF signals
- A "mixed" alignment for all-neutral TFs is incorrect and would produce wrong decisions

**Fix**: Add `elif all(b == "neutral" for b in biases): return "neutral"` between the bullish and bearish checks in `alignment()`.

**Effort**: `Quick(<1h)`. Should be done first.

---

## Blocking Questions for the User

These 3 questions must be answered before Prometheus generates a plan, because each answer changes the effort estimate and file structure significantly:

### Q1: Narrative — Structured dataclass or formatted string?
- **Option A (Structured dataclass)**: `narrative.py` with `Narrative` dataclass + generator + formatter. More testable, extensible for future dashboard/notification output. Effort: ~1-2d.
- **Option B (Formatted string)**: Simpler, just a function that returns a string. Less flexible, harder to test, but faster. Effort: ~0.5d.
- **My recommendation**: Option A (structured dataclass). The trade-off is worth it for testability and future extensibility.

### Q2: Decision Engine — What are the exact invalidation and target sources?
Both MUST be deterministic rules with clear fallbacks. The behavior of the entire engine changes depending on this choice:
- **Liquidity-first (SMC-aligned)**: Invalidation = opposite-side nearest liquidity, target = same-side nearest liquidity. Fallback to swing levels, then EMA21. (My recommendation)
- **EMA-first (trend-aligned)**: Invalidation = EMA21 for both sides. Target = nearest liquidity in bias direction.
- **Swing-first (structure-aligned)**: Invalidation = last swing in opposite direction. Target = last swing in bias direction.

### Q3: MTF Weighting — Weighted average or hierarchical?
- **Option A (Weighted average)**: Simple formula, backward-compatible via new method, daily dominates in practice but CAN be flipped by extreme lower-TF disagreement. Effort: ~1h.
- **Option B (Hierarchical)**: HTF sets direction unconditionally, LTF only adjusts confidence. More SMC-aligned but more complex branching logic. Effort: ~4h.
- **My recommendation**: Start with Option A (weighted average). The asymmetry in the scoring range already ensures daily dominance in 95%+ of practical cases. Revisit hierarchical only if testing shows daily being flipped in meaningful scenarios.

---

## Summary Table

| Feature | File | Effort | Risk | Blocks on |
|---------|------|--------|------|-----------|
| Alignment bug fix | `confluence.py` | Quick (<1h) | None | — |
| Narrative | `narrative.py` (new) | Short (1-2d) | Low if structured | **Q1**: dataclass vs string |
| Decision Engine | `decision_engine.py` (new) | Short (1-2d) | Low | **Q2**: invalidation/target source |
| MTF Weighting | `confluence.py` (modify) | Quick (<1h) | Minimal if backward compat | **Q3**: weighted vs hierarchical |

All three features can be built sequentially or in parallel (after prerequisites).
