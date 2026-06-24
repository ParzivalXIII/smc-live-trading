"""
decision_engine.py — Decision dataclass and DecisionEngine.

Maps confluence scores to actionable trading decisions with confidence,
invalidation, and target levels following SMC-aligned liquidity-first logic.

This module is pure decision support — it does NOT execute trades.
"""

from __future__ import annotations

from dataclasses import dataclass

from confluence import ConfluenceResult, ConfluenceScorer, MarketContext
from market_snapshot import MarketSnapshot


@dataclass
class Decision:
    """Trading decision produced by ``DecisionEngine.decide()``.

    Attributes:
        bias: Directional bias ("bullish" / "bearish" / "neutral").
        direction_score: Raw HTF regime strength score (-4 to 10).
        confidence: LTF alignment quality multiplier (0.0 to 1.0).
        action: Recommended action ("look_for_longs" / "avoid_shorts" / "stand_aside").
        invalidation: Price level that invalidates the bias (or ``None``).
        target: Price target in the bias direction (or ``None``).
        breakout_pending: If ``True``, a breakout is brewing but unconfirmed.
        breakout_level: Specific level to watch for breakout (or ``None``).
    """

    bias: str
    direction_score: float
    confidence: float
    action: str
    invalidation: float | None = None
    target: float | None = None
    breakout_pending: bool = False
    breakout_level: float | None = None


# ---------------------------------------------------------------------------
# DecisionEngine
# ---------------------------------------------------------------------------


class DecisionEngine:
    """Maps a ``MarketSnapshot`` + ``ConfluenceResult`` to a ``Decision``.

    The decision logic follows:
    - **Action mapping**: HTF bias + confidence threshold combine to produce
      an action. Bullish/bearish bias with confidence > 0.5 produces a
      directional action; confidence <= 0.5 downgrades to stand_aside.
      Neutral bias always produces stand_aside.
    - **Confidence**: Passed through directly from ``ConfluenceResult.confidence``
      (LTF alignment quality, 0.0-1.0).
    - **Invalidation/target**: Unchanged (liquidity-first, SMC-aligned).
    - **breakout_pending**: Modifier flag for confidence 0.3-0.7 near a swing level.
    """

    def decide(
        self,
        snapshot: MarketSnapshot,
        result: ConfluenceResult,
        context: MarketContext | None = None,
    ) -> Decision:
        """Produce a trading decision from market data and confluence scoring.

        Parameters
        ----------
        snapshot : MarketSnapshot
            The market state snapshot.
        result : ConfluenceResult
            The scored confluence result.
        context : MarketContext | None
            Optional multi-timeframe context for hierarchical adjustment.

        Returns
        -------
        Decision
        """
        bias = result.bias

        if context is not None:
            # Use hierarchical composite result from MarketContext
            result = context.composite_score()

        bias = result.bias

        # --- Direct pass-through from ConfluenceResult ---
        direction_score = result.direction_score
        confidence = result.confidence

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

        # Breakout pending: uncertain confidence (0.3-0.7) + directional bias + swing within 1 ATR
        if bias != "neutral" and 0.3 <= confidence <= 0.7:
            if snapshot.last_swing_level is not None and not self._is_nan_or_none(snapshot.atr14):
                if not (self._is_nan_or_none(snapshot.close) or self._is_nan_or_none(snapshot.last_swing_level)):
                    distance = abs(snapshot.last_swing_level - snapshot.close)
                    if distance <= snapshot.atr14:
                        breakout_pending = True
                        breakout_level = snapshot.last_swing_level

        # --- Invalidation (liquidity-first) ---
        invalidation = self._resolve_invalidation(snapshot, bias)

        # --- Target (liquidity-first) ---
        target = self._resolve_target(snapshot, bias)

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

    # ------------------------------------------------------------------
    # Invalidation logic
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_invalidation(snapshot: MarketSnapshot, bias: str) -> float | None:
        """Resolve invalidation level using liquidity-first fallback chain.

        For bullish bias:
            1. ``snapshot.nearest_liquidity_below``
            2. ``snapshot.last_swing_level`` (if swing direction opposite = -1)
            3. ``snapshot.ema21``
            4. ``None``

        For bearish bias:
            1. ``snapshot.nearest_liquidity_above``
            2. ``snapshot.last_swing_level`` (if swing direction opposite = 1)
            3. ``snapshot.ema21``
            4. ``None``

        For neutral: ``None``
        """
        if bias == "neutral":
            return None

        if bias == "bullish":
            if snapshot.nearest_liquidity_below is not None:
                return snapshot.nearest_liquidity_below
            if snapshot.last_swing_direction == -1 and snapshot.last_swing_level is not None:
                return snapshot.last_swing_level
            if DecisionEngine._is_valid_price(snapshot.ema21):
                return snapshot.ema21
            return None

        # Bearish
        if snapshot.nearest_liquidity_above is not None:
            return snapshot.nearest_liquidity_above
        if snapshot.last_swing_direction == 1 and snapshot.last_swing_level is not None:
            return snapshot.last_swing_level
        if DecisionEngine._is_valid_price(snapshot.ema21):
            return snapshot.ema21
        return None

    # ------------------------------------------------------------------
    # Target logic
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_target(snapshot: MarketSnapshot, bias: str) -> float | None:
        """Resolve target level using liquidity-first logic.

        For bullish bias:
            1. ``snapshot.nearest_liquidity_above``
            2. ``snapshot.last_swing_level`` (if swing direction same = 1)
            3. ``None``

        For bearish bias:
            1. ``snapshot.nearest_liquidity_below``
            2. ``snapshot.last_swing_level`` (if swing direction same = -1)
            3. ``None``

        For neutral: ``None``
        """
        if bias == "neutral":
            return None

        if bias == "bullish":
            if snapshot.nearest_liquidity_above is not None:
                return snapshot.nearest_liquidity_above
            if snapshot.last_swing_direction == 1 and snapshot.last_swing_level is not None:
                return snapshot.last_swing_level
            return None

        # Bearish
        if snapshot.nearest_liquidity_below is not None:
            return snapshot.nearest_liquidity_below
        if snapshot.last_swing_direction == -1 and snapshot.last_swing_level is not None:
            return snapshot.last_swing_level
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_nan_or_none(val: object) -> bool:
        """Check if a value is ``None`` or NaN."""
        import math
        if val is None:
            return True
        if isinstance(val, float):
            return math.isnan(val)
        return False

    @staticmethod
    def _is_valid_price(val: object) -> bool:
        """Check if a value is a valid positive price (not None, not NaN, > 0)."""
        if val is None:
            return False
        if isinstance(val, bool):
            return False
        if isinstance(val, (int, float)):
            import math
            if math.isnan(val):
                return False
            return val > 0
        return False
