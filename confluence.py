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

from dataclasses import dataclass

from market_snapshot import MarketSnapshot


@dataclass(slots=True)
class ConfluenceResult:
    """Result of scoring a snapshot.

    Attributes:
        bias: ``"bullish"``, ``"bearish"``, or ``"neutral"``.
        score: Raw additive score.
        max_score: Maximum possible score for this context.
        reasons: Human-readable reasons for each scoring condition.
    """

    bias: str
    score: int
    max_score: int
    reasons: list[str]


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

        return ConfluenceResult(bias=bias, score=score, max_score=max_score, reasons=reasons)


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

    def composite_score(self) -> ConfluenceResult:
        """Score each active timeframe and combine.

        Returns a single ``ConfluenceResult`` with:
        - Sum of individual scores
        - ``max_score`` = ``len(active) * 10``
        - Bias from alignment (``"mixed"`` if timeframes disagree)
        - Concatenated reasons
        """
        active = self._active_timeframes()
        if not active:
            return ConfluenceResult("neutral", 0, 0, ["No data"])

        scorer = ConfluenceScorer()
        total_score = 0
        max_score = len(active) * 10
        reasons: list[str] = []
        tf_biases: list[str] = []

        for name, tf in active:
            result = scorer.score(tf)
            total_score += result.score
            tf_biases.append(result.bias)
            reasons.append(f"{name}: {result.bias} (score={result.score})")
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

        return ConfluenceResult(bias=bias, score=total_score, max_score=max_score, reasons=reasons)
