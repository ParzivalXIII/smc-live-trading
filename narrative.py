"""
narrative.py — NarrativeSection, MarketNarrative, and MarketNarrativeBuilder.

Converts a MarketSnapshot + ConfluenceResult into a structured market narrative
with sections for Trend, Momentum, Structure, Liquidity, and a Conclusion.

This module is pure text generation — it contains no scoring or decision logic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from confluence import ConfluenceResult
from market_snapshot import MarketSnapshot
from trade_scripts.analyze_ta import mfi_signal, rsi_label


@dataclass
class NarrativeSection:
    """A single section of a market narrative.

    Attributes:
        title: Section title (e.g. "Trend", "Momentum").
        bullets: Bullet points for this section.
    """

    title: str
    bullets: list[str]


@dataclass
class MarketNarrative:
    """Complete market narrative for one symbol/timeframe.

    Attributes:
        symbol: Trading pair symbol.
        timeframe: Timeframe label.
        bias: Overall bias ("bullish" / "bearish" / "neutral").
        direction_score: Raw HTF regime strength score (-4 to 10).
        confidence: LTF alignment quality multiplier (0.0 to 1.0).
        max_score: Maximum possible score.
        sections: Ordered list of narrative sections.
        conclusion: Final verdict / outlook text.
    """

    symbol: str
    timeframe: str
    bias: str
    direction_score: int | float
    confidence: float
    max_score: int | float
    sections: list[NarrativeSection]
    conclusion: str


# ---------------------------------------------------------------------------
# MarketNarrativeBuilder
# ---------------------------------------------------------------------------


class MarketNarrativeBuilder:
    """Build a ``MarketNarrative`` from a ``MarketSnapshot`` + ``ConfluenceResult``.

    Usage::

        builder = MarketNarrativeBuilder()
        narrative = builder.build(snapshot, confluence_result)
    """

    def build(
        self,
        snapshot: MarketSnapshot,
        result: ConfluenceResult,
    ) -> MarketNarrative:
        """Produce a ``MarketNarrative`` from market data and a scoring result.

        Parameters
        ----------
        snapshot : MarketSnapshot
            The market state snapshot.
        result : ConfluenceResult
            The scored confluence result (bias, direction_score, confidence, reasons).

        Returns
        -------
        MarketNarrative
        """
        sections: list[NarrativeSection] = []
        sections.append(self._build_trend(snapshot))
        sections.append(self._build_momentum(snapshot))
        sections.append(self._build_structure(snapshot))
        sections.append(self._build_liquidity(snapshot))
        conclusion = self._build_conclusion(snapshot, result.bias)

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

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_trend(snapshot: MarketSnapshot) -> NarrativeSection:
        """Build the Trend section."""
        bullets: list[str] = []

        # Price vs EMA21
        if not math.isnan(snapshot.close) and not math.isnan(snapshot.ema21):
            pct_diff = ((snapshot.close - snapshot.ema21) / snapshot.ema21) * 100
            bullets.append(
                f"Price {snapshot.trend_direction} EMA21 ({snapshot.close:.2f} vs {snapshot.ema21:.2f}, "
                f"{pct_diff:+.2f}%)"
            )
        else:
            bullets.append("Price vs EMA21: insufficient data")

        # EMA21 slope
        if not math.isnan(snapshot.ema21_slope):
            slope_desc = "rising" if snapshot.ema21_slope > 0 else "falling" if snapshot.ema21_slope < 0 else "flat"
            bullets.append(f"EMA21 slope: {slope_desc} ({snapshot.ema21_slope:+.4f})")
        else:
            bullets.append("EMA21 slope: insufficient data")

        return NarrativeSection(title="Trend", bullets=bullets)

    @staticmethod
    def _build_momentum(snapshot: MarketSnapshot) -> NarrativeSection:
        """Build the Momentum section."""
        bullets: list[str] = []

        # MACD vs signal
        if not math.isnan(snapshot.macd) and not math.isnan(snapshot.macd_signal):
            macd_status = "above" if snapshot.macd > snapshot.macd_signal else "below"
            bullets.append(f"MACD {macd_status} signal line ({snapshot.macd:.2f} vs {snapshot.macd_signal:.2f})")
        else:
            bullets.append("MACD: insufficient data")

        # RSI with zone label
        if not math.isnan(snapshot.rsi14):
            bullets.append(f"RSI-14: {rsi_label(snapshot.rsi14)}")
        else:
            bullets.append("RSI-14: insufficient data")

        # MFI with zone label
        if not math.isnan(snapshot.mfi14):
            bullets.append(f"MFI-14: {mfi_signal(snapshot.mfi14)}")
        else:
            bullets.append("MFI-14: insufficient data")

        return NarrativeSection(title="Momentum", bullets=bullets)

    @staticmethod
    def _build_structure(snapshot: MarketSnapshot) -> NarrativeSection:
        """Build the Structure section."""
        bullets: list[str] = []

        # Last swing
        if snapshot.last_swing_direction is not None and snapshot.last_swing_level is not None:
            swing_type = "high" if snapshot.last_swing_direction == 1 else "low"
            bullets.append(
                f"Last swing {swing_type} at {snapshot.last_swing_level:.2f} "
                f"(direction={snapshot.last_swing_direction})"
            )
        else:
            bullets.append("No recent structure data")

        # BOS
        if snapshot.last_bos_direction == 1:
            bos_text = f"Bullish BOS confirmed"
            if snapshot.last_bos_index is not None:
                bos_text += f" (broken index {snapshot.last_bos_index})"
            bullets.append(bos_text)
        elif snapshot.last_bos_direction == -1:
            bos_text = f"Bearish BOS confirmed"
            if snapshot.last_bos_index is not None:
                bos_text += f" (broken index {snapshot.last_bos_index})"
            bullets.append(bos_text)
        else:
            bullets.append("No BOS detected")

        # CHOCH
        if snapshot.last_choch_direction == 1:
            choch_text = "Bullish CHOCH detected"
            if snapshot.last_choch_index is not None:
                choch_text += f" (index {snapshot.last_choch_index})"
            bullets.append(choch_text)
        elif snapshot.last_choch_direction == -1:
            choch_text = "Bearish CHOCH detected"
            if snapshot.last_choch_index is not None:
                choch_text += f" (index {snapshot.last_choch_index})"
            bullets.append(choch_text)
        else:
            bullets.append("No bearish/bullish CHOCH detected")

        return NarrativeSection(title="Structure", bullets=bullets)

    @staticmethod
    def _build_liquidity(snapshot: MarketSnapshot) -> NarrativeSection:
        """Build the Liquidity section."""
        bullets: list[str] = []

        # Nearest liquidity above
        if snapshot.nearest_liquidity_above is not None:
            bullets.append(f"Nearest liquidity above: {snapshot.nearest_liquidity_above:.2f}")
        else:
            bullets.append("Nearest liquidity above: none")

        # Nearest liquidity below
        if snapshot.nearest_liquidity_below is not None:
            bullets.append(f"Nearest liquidity below: {snapshot.nearest_liquidity_below:.2f}")
        else:
            bullets.append("Nearest liquidity below: none")

        # Active order blocks
        ob_text_parts: list[str] = []
        if snapshot.active_bullish_ob is not None:
            ob_text_parts.append(f"bullish OB at {snapshot.active_bullish_ob:.2f}")
        if snapshot.active_bearish_ob is not None:
            ob_text_parts.append(f"bearish OB at {snapshot.active_bearish_ob:.2f}")

        if ob_text_parts:
            bullets.append("Active OBs: " + ", ".join(ob_text_parts))
        else:
            bullets.append("No significant liquidity clusters")

        return NarrativeSection(title="Liquidity", bullets=bullets)

    @staticmethod
    def _build_conclusion(snapshot: MarketSnapshot, bias: str) -> str:
        """Build the conclusion based on bias and available liquidity levels."""
        if bias == "bullish":
            target_text = (
                f"Liquidity target at {snapshot.nearest_liquidity_above:.2f}."
                if snapshot.nearest_liquidity_above is not None
                else "No clear target level identified."
            )
            return f"Bullish continuation favored while EMA21 remains intact. {target_text}"

        if bias == "bearish":
            invalidation_text = (
                f"Invalidation at {snapshot.nearest_liquidity_below:.2f}."
                if snapshot.nearest_liquidity_below is not None
                else "No clear invalidation level identified."
            )
            return f"Bearish momentum intact. {invalidation_text}"

        # Neutral
        return "Mixed signals across timeframes. No clear directional edge. Stand aside."
