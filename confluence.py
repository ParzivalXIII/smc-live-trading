"""
confluence.py — ConfluenceResult, ConfluenceScorer, and MarketContext.

ConfluenceResult is a pure-data record of a scoring outcome (bias, score,
max_score, reasons).

ConfluenceScorer is a pure function that takes a ``MarketSnapshot`` and returns
a ``ConfluenceResult`` with additive scoring.  It contains the ONLY opinions
about market direction in the system.

MarketContext wraps up to three timeframes (daily, H4, H1) and provides
alignment detection and composite scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from market_snapshot import MarketSnapshot


@dataclass
class ConfluenceResult:
    """Result of scoring a snapshot.

    Attributes:
        bias: ``"bullish"``, ``"bearish"``, or ``"neutral"``.
        direction_score: Raw HTF regime strength score (-4 to 10).
        confidence: LTF alignment quality multiplier (0.0 to 1.0).
        max_score: Maximum possible score for this context.
        reasons: Human-readable reasons for each scoring condition.
    """

    bias: str
    direction_score: float
    confidence: float
    max_score: int | float = 10
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ConfluenceScorer
# ---------------------------------------------------------------------------


class ConfluenceScorer:
    """Additive confluence scorer for a single ``MarketSnapshot``.

    Scoring table (single timeframe):
        +2  close > ema21
        +1  ema21_slope > 0
        +1  macd > macd_signal
        +1  rsi14 > 55
        +1  mfi14 > 50
        +3  last_bos_direction == 1  (bullish BOS)
        -3  last_bos_direction == -1 (bearish BOS)
        +1  nearest_liquidity_above exists
        -1  nearest_liquidity_below exists

    Score range: -4 to 10.

    Bias mapping:
        < 0       → bearish
        0 – 3     → bearish
        4 – 6     → neutral
        7 – 10    → bullish
    """

    def score(self, snapshot: MarketSnapshot) -> ConfluenceResult:
        """Score a single snapshot and return a ``ConfluenceResult``."""
        score = 0
        max_score = 10
        reasons: list[str] = []

        # --- Trend ---
        if snapshot.close > snapshot.ema21:
            score += 2
            reasons.append(
                f"close ({snapshot.close:.2f}) > ema21 ({snapshot.ema21:.2f}): +2"
            )
        else:
            reasons.append(
                f"close ({snapshot.close:.2f}) <= ema21 ({snapshot.ema21:.2f}): +0"
            )

        if snapshot.ema21_slope > 0:
            score += 1
            reasons.append(f"ema21_slope ({snapshot.ema21_slope:.4f}) > 0: +1")
        else:
            reasons.append(f"ema21_slope ({snapshot.ema21_slope:.4f}) <= 0: +0")

        # --- Momentum ---
        if snapshot.macd > snapshot.macd_signal:
            score += 1
            reasons.append(
                f"macd ({snapshot.macd:.2f}) > macd_signal ({snapshot.macd_signal:.2f}): +1"
            )
        else:
            reasons.append(
                f"macd ({snapshot.macd:.2f}) <= macd_signal ({snapshot.macd_signal:.2f}): +0"
            )

        if snapshot.rsi14 > 55:
            score += 1
            reasons.append(f"rsi14 ({snapshot.rsi14:.1f}) > 55: +1")
        else:
            reasons.append(f"rsi14 ({snapshot.rsi14:.1f}) <= 55: +0")

        if snapshot.mfi14 > 50:
            score += 1
            reasons.append(f"mfi14 ({snapshot.mfi14:.1f}) > 50: +1")
        else:
            reasons.append(f"mfi14 ({snapshot.mfi14:.1f}) <= 50: +0")

        # --- Structure ---
        if snapshot.last_bos_direction == 1:
            score += 3
            reasons.append("last_bos_direction == 1: +3 (bullish BOS confirmed)")
        elif snapshot.last_bos_direction == -1:
            score -= 3
            reasons.append("last_bos_direction == -1: -3 (bearish BOS confirmed)")
        else:
            reasons.append("last_bos_direction is None or 0: +0")

        # --- Liquidity ---
        if snapshot.nearest_liquidity_above is not None:
            score += 1
            reasons.append(
                f"nearest_liquidity_above exists at {snapshot.nearest_liquidity_above:.2f}: +1"
            )
        else:
            reasons.append("nearest_liquidity_above is None: +0")

        if snapshot.nearest_liquidity_below is not None:
            score -= 1
            reasons.append(
                f"nearest_liquidity_below exists at {snapshot.nearest_liquidity_below:.2f}: -1"
            )
        else:
            reasons.append("nearest_liquidity_below is None: +0")

        # --- Bias mapping ---
        if score < 0:
            bias = "bearish"
        elif score <= 3:
            bias = "bearish"
        elif score <= 6:
            bias = "neutral"
        else:
            bias = "bullish"

        return ConfluenceResult(bias=bias, direction_score=score, confidence=1.0, max_score=max_score, reasons=reasons)


# ---------------------------------------------------------------------------
# MarketContext
# ---------------------------------------------------------------------------


@dataclass
class MarketContext:
    """Multi-timeframe market context wrapping up to three snapshots.

    Attributes:
        daily: Daily timeframe snapshot (or ``None``).
        h4: 4-hour timeframe snapshot (or ``None``).
        h1: 1-hour timeframe snapshot (or ``None``).
    """

    daily: MarketSnapshot | None = None
    h4: MarketSnapshot | None = None
    h1: MarketSnapshot | None = None

    def _active_timeframes(self) -> list[tuple[str, MarketSnapshot]]:
        """Return list of (name, snapshot) for non-None timeframes."""
        result: list[tuple[str, MarketSnapshot]] = []
        for name, tf in [("daily", self.daily), ("h4", self.h4), ("h1", self.h1)]:
            if tf is not None:
                result.append((name, tf))
        return result

    def alignment(self) -> str:
        """Detect alignment across all active timeframes.

        Returns
        -------
        str
            ``"bullish"`` if all active TFs are bullish,
            ``"bearish"`` if all active TFs are bearish,
            ``"mixed"`` if there is any disagreement,
            ``"neutral"`` if no data available.
        """
        active = self._active_timeframes()
        if not active:
            return "neutral"

        if len(active) == 1:
            return ConfluenceScorer().score(active[0][1]).bias

        biases = [ConfluenceScorer().score(tf).bias for _, tf in active]
        if all(b == "bullish" for b in biases):
            return "bullish"
        if all(b == "bearish" for b in biases):
            return "bearish"
        if all(b == "neutral" for b in biases):
            return "neutral"
        return "mixed"

    def legacy_composite_score(self) -> ConfluenceResult:
        """Score each active timeframe and combine (additive, pre-hierarchical).

        This is the ORIGINAL behavior preserved for backward compatibility.
        Returns a single ``ConfluenceResult`` with:
        - Sum of individual scores
        - ``max_score`` = ``len(active) * 10``
        - Bias from alignment (``"mixed"`` if timeframes disagree)
        - Concatenated reasons
        """
        active = self._active_timeframes()
        if not active:
            return ConfluenceResult("neutral", 0, 1.0, 0, ["No data"])

        scorer = ConfluenceScorer()
        total_score = 0
        max_score = len(active) * 10
        reasons: list[str] = []
        tf_biases: list[str] = []

        for name, tf in active:
            result = scorer.score(tf)
            total_score += result.direction_score
            tf_biases.append(result.bias)
            reasons.append(f"{name}: {result.bias} (score={result.direction_score})")
            reasons.extend(f"  {r}" for r in result.reasons)

        # Determine aggregate bias
        if all(b == "bullish" for b in tf_biases):
            bias = "bullish"
        elif all(b == "bearish" for b in tf_biases):
            bias = "bearish"
        elif all(b == "neutral" for b in tf_biases):
            bias = "neutral"
        elif "bullish" in tf_biases and "bearish" in tf_biases:
            bias = "mixed"
        else:
            # Mixed but with neutral
            bias = "mixed"

        return ConfluenceResult(bias=bias, direction_score=total_score, confidence=1.0, max_score=max_score, reasons=reasons)

    @staticmethod
    def _alignment_factor(htf_bias: str, ltf_bias: str) -> float:
        """Determine LTF alignment as a confidence multiplier.

        aligned (same bias)              → 1.0  (no reduction)
        neutral LTF (no opinion)         → 0.7  (30% confidence reduction)
        conflicting (opposite bias)      → 0.4  (60% confidence reduction)

        Note: When HTF is neutral and LTF is directional, this falls into
        the 'conflicting' case (0.4) — any directional LTF disagrees with
        a neutral HTF by definition.
        """
        if ltf_bias == htf_bias:
            return 1.0
        elif ltf_bias == "neutral":
            return 0.7
        return 0.4

    def composite_score(self) -> ConfluenceResult:
        """Hierarchical MTF: HTF sets bias, LTF modifies confidence multiplicatively.

        HTF regime lock: The highest timeframe's bias is immutable.
        LTFs only degrade confidence — they cannot flip the bias.

        Confidence multiplier = product of LTF alignment factors.
        Final score = htf_score * confidence_multiplier.
        """
        active = self._active_timeframes()
        if not active:
            return ConfluenceResult("neutral", 0.0, 1.0, 0, ["No data available"])

        scorer = ConfluenceScorer()

        # HTF is the highest-priority active timeframe
        htf_name, htf = active[0]
        htf_result = scorer.score(htf)
        base_bias = htf_result.bias
        htf_score = htf_result.direction_score
        reasons = [f"HTF ({htf_name}) sets {base_bias} regime"]

        # LTF confidence multiplier (multiplicative, not additive)
        confidence = 1.0
        for ltf_name, ltf in active[1:]:
            ltf_result = scorer.score(ltf)
            factor = self._alignment_factor(base_bias, ltf_result.bias)
            confidence *= factor
            if factor == 1.0:
                reasons.append(f"LTF ({ltf_name}) aligns")
            elif factor == 0.7:
                reasons.append(f"LTF ({ltf_name}) neutral — partial confidence")
            else:
                reasons.append(f"LTF ({ltf_name}) conflicts — reduced confidence")

        return ConfluenceResult(
            bias=base_bias,
            direction_score=htf_score,
            confidence=round(confidence, 2),
            max_score=10,
            reasons=reasons,
        )

    @property
    def regime_alignment(self) -> str | list[str]:
        """Report alignment of lower TFs with the HTF regime.

        Returns
        -------
        str | list[str]
            ``"aligned"`` — all TFs agree with HTF.
            ``["h4_conflict", "h1_conflict"]`` — list of conflicting TF names.
            ``"neutral_if_no_htf"`` — no HTF available.

        Note
        ----
        This property detects conflicts (opposite biases) only.
        Partial alignment (factor=0.7, when LTF is neutral) does NOT count as
        a conflict — a neutral LTF is simply ignored for conflict detection.
        """
        active = self._active_timeframes()
        if not active:
            return "neutral_if_no_htf"

        scorer = ConfluenceScorer()

        # Find HTF
        htf_name: str | None = None
        htf_snapshot: MarketSnapshot | None = None

        for name, tf in active:
            if htf_name is None:
                htf_name = name
                htf_snapshot = tf

        assert htf_name is not None and htf_snapshot is not None

        htf_bias = scorer.score(htf_snapshot).bias

        if len(active) == 1:
            return "aligned"

        conflicts: list[str] = []
        for name, tf in active:
            if name == htf_name:
                continue
            tf_bias = scorer.score(tf).bias
            if tf_bias != htf_bias and tf_bias != "neutral":
                conflicts.append(f"{name}_conflict")

        if not conflicts:
            return "aligned"
        return conflicts
