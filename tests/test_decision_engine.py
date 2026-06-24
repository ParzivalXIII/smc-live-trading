"""
Tests for the Decision Engine module: Decision, DecisionEngine.

Run with:
    python -m pytest tests/test_decision_engine.py -v --tb=short
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from confluence import ConfluenceResult, ConfluenceScorer, MarketContext
from market_snapshot import MarketSnapshot

# =============================================================================
# T2a: RED phase — 14 TDD tests for DecisionEngine
# =============================================================================
# These tests define the expected contract BEFORE implementation.
# They will fail until decision_engine.py is implemented (GREEN phase).


class TestDecisionEngine:
    """DecisionEngine.decide() — TDD core tests."""

    # --- Action mapping tests ---

    def test_look_for_longs(
        self, sample_snapshot: MarketSnapshot
    ) -> None:
        """Score=10, bullish bias → action=look_for_longs, confidence=1.0."""
        from decision_engine import DecisionEngine

        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["All bullish"])
        decision = engine.decide(sample_snapshot, result)

        assert decision.action == "look_for_longs"
        assert decision.bias == "bullish"
        assert decision.confidence == pytest.approx(1.0)

    def test_avoid_shorts(
        self, bearish_snapshot: MarketSnapshot
    ) -> None:
        """Score=-4, bearish bias → action=avoid_shorts, confidence=1.0."""
        from decision_engine import DecisionEngine

        engine = DecisionEngine()
        result = ConfluenceResult("bearish", -4, 1.0, 10, ["All bearish"])
        decision = engine.decide(bearish_snapshot, result)

        assert decision.action == "avoid_shorts"
        assert decision.bias == "bearish"
        assert decision.confidence == pytest.approx(1.0)

    def test_stand_aside(
        self, neutral_snapshot: MarketSnapshot
    ) -> None:
        """Score=4, neutral bias → action=stand_aside."""
        from decision_engine import DecisionEngine

        engine = DecisionEngine()
        result = ConfluenceResult("neutral", 4, 1.0, 10, ["Neutral"])
        decision = engine.decide(neutral_snapshot, result)

        assert decision.action == "stand_aside"
        assert decision.bias == "neutral"

    def test_watch_breakout_modifier(self) -> None:
        """Confidence=0.5, bias=bullish, swing near → breakout_pending=True."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT",
            timeframe="1d",
            timestamp=pd.Timestamp.now(),
            close=50000.0,
            trend_direction="above",
            ema21=49000.0,
            ema21_slope=0.01,
            rsi14=55.0,
            mfi14=52.0,
            macd=100.0,
            macd_signal=90.0,
            macd_hist=10.0,
            atr14=500.0,
            bb_width=0.05,
            last_swing_direction=1,
            last_swing_level=50500.0,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 0.5, 10, ["Uncertain conviction"])
        decision = engine.decide(snap, result)

        assert decision.action == "stand_aside"  # confidence=0.5 ≤ 0.5
        assert decision.breakout_pending is True
        assert decision.breakout_level == 50500.0

    # --- Invalidation tests (bullish, fallback chain) ---

    def test_invalidation_bullish_liquidity_first(
        self, sample_snapshot: MarketSnapshot
    ) -> None:
        """Bullish bias with nearest_liquidity_below → invalidation equals that level."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT",
            timeframe="1d",
            timestamp=pd.Timestamp.now(),
            close=50000.0,
            trend_direction="above",
            ema21=49000.0,
            ema21_slope=0.01,
            rsi14=60.0,
            mfi14=55.0,
            macd=100.0,
            macd_signal=90.0,
            macd_hist=10.0,
            atr14=500.0,
            bb_width=0.05,
            nearest_liquidity_below=47000.0,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        decision = engine.decide(snap, result)

        assert decision.invalidation == 47000.0

    def test_invalidation_bullish_swing_fallback(self) -> None:
        """Bullish with no liquidity below but swing low exists → invalidation=swing_level."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT",
            timeframe="1d",
            timestamp=pd.Timestamp.now(),
            close=50000.0,
            trend_direction="above",
            ema21=49000.0,
            ema21_slope=0.01,
            rsi14=60.0,
            mfi14=55.0,
            macd=100.0,
            macd_signal=90.0,
            macd_hist=10.0,
            atr14=500.0,
            bb_width=0.05,
            last_swing_direction=-1,
            last_swing_level=47500.0,
            nearest_liquidity_below=None,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        decision = engine.decide(snap, result)

        assert decision.invalidation == 47500.0

    def test_invalidation_bullish_ema_fallback(self) -> None:
        """Bullish with no liquidity and no swing → invalidation=ema21."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT",
            timeframe="1d",
            timestamp=pd.Timestamp.now(),
            close=50000.0,
            trend_direction="above",
            ema21=49000.0,
            ema21_slope=0.01,
            rsi14=60.0,
            mfi14=55.0,
            macd=100.0,
            macd_signal=90.0,
            macd_hist=10.0,
            atr14=500.0,
            bb_width=0.05,
            last_swing_direction=None,
            last_swing_level=None,
            nearest_liquidity_below=None,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        decision = engine.decide(snap, result)

        assert decision.invalidation == 49000.0

    def test_invalidation_bullish_no_fallback(self) -> None:
        """Bullish with no data at all → invalidation=None."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT",
            timeframe="1d",
            timestamp=pd.Timestamp.now(),
            close=50000.0,
            trend_direction="above",
            ema21=0.0,
            ema21_slope=0.0,
            rsi14=50.0,
            mfi14=50.0,
            macd=0.0,
            macd_signal=0.0,
            macd_hist=0.0,
            atr14=0.0,
            bb_width=0.0,
            last_swing_direction=None,
            last_swing_level=None,
            nearest_liquidity_below=None,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 7, 1.0, 10, ["Bullish"])
        decision = engine.decide(snap, result)

        assert decision.invalidation is None

    # --- Target tests ---

    def test_target_bullish(
        self, sample_snapshot: MarketSnapshot
    ) -> None:
        """Bullish bias with nearest_liquidity_above → target equals that level."""
        from decision_engine import DecisionEngine

        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        decision = engine.decide(sample_snapshot, result)

        # sample_snapshot has nearest_liquidity_above=51000
        assert decision.target == 51000.0

    def test_target_bearish(self) -> None:
        """Bearish bias with nearest_liquidity_below → target equals that level."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT",
            timeframe="1d",
            timestamp=pd.Timestamp.now(),
            close=48000.0,
            trend_direction="below",
            ema21=49000.0,
            ema21_slope=-0.01,
            rsi14=40.0,
            mfi14=35.0,
            macd=50.0,
            macd_signal=90.0,
            macd_hist=-5.0,
            atr14=500.0,
            bb_width=0.05,
            nearest_liquidity_below=45000.0,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bearish", -4, 1.0, 10, ["Bearish"])
        decision = engine.decide(snap, result)

        assert decision.target == 45000.0

    # --- Confidence passthrough ---

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

    # --- Neutral: invalidation/target = None ---

    def test_neutral_invalidation_target_none(
        self, neutral_snapshot: MarketSnapshot
    ) -> None:
        """Neutral decision has invalidation=None and target=None."""
        from decision_engine import DecisionEngine

        engine = DecisionEngine()
        result = ConfluenceResult("neutral", 4, 1.0, 10, ["Neutral"])
        decision = engine.decide(neutral_snapshot, result)

        assert decision.invalidation is None
        assert decision.target is None

    # --- MarketContext tests ---

    def test_with_market_context(self) -> None:
        """With MarketContext: hierarchical logic doesn't flip bias (stays from daily)."""
        from decision_engine import DecisionEngine

        daily = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above", ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0, macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05, last_bos_direction=1,
            nearest_liquidity_above=51000.0,
        )
        h4 = MarketSnapshot(
            symbol="BTC/USDT", timeframe="4h", timestamp=pd.Timestamp.now(),
            close=48000.0, trend_direction="below", ema21=49000.0, ema21_slope=-0.01,
            rsi14=40.0, mfi14=35.0, macd=50.0, macd_signal=90.0, macd_hist=-5.0,
            atr14=400.0, bb_width=0.05, last_bos_direction=-1,
            nearest_liquidity_below=46000.0,
        )
        ctx = MarketContext(daily=daily, h4=h4)
        scorer = ConfluenceScorer()
        daily_result = scorer.score(daily)
        composite = ctx.composite_score()

        engine = DecisionEngine()
        decision = engine.decide(daily, composite, context=ctx)

        # Bias should stay from daily (bullish), not flip to bearish despite h4 disagreement
        assert decision.bias == "bullish", f"Expected bullish, got {decision.bias}"

    def test_without_market_context(
        self, sample_snapshot: MarketSnapshot
    ) -> None:
        """Call decide() with context=None → single-TF behavior."""
        from decision_engine import DecisionEngine

        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        decision = engine.decide(sample_snapshot, result, context=None)

        assert decision.action == "look_for_longs"
        assert decision.bias == "bullish"
        assert decision.confidence == pytest.approx(1.0)


# =============================================================================
# T5: Edge case tests (tests-after)
# =============================================================================


class TestDecisionEdgeCases:
    """Edge case coverage for DecisionEngine."""

    def test_decision_score_zero(self) -> None:
        """Score=0, bearish bias with confidence=1.0 → action=avoid_shorts."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="X", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=100.0, trend_direction="below",
            ema21=110.0, ema21_slope=0.0, rsi14=50.0, mfi14=50.0,
            macd=0.0, macd_signal=0.0, macd_hist=0.0,
            atr14=0.0, bb_width=0.0,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bearish", 0, 1.0, 10, ["Zero score"])
        decision = engine.decide(snap, result)

        assert decision.action == "avoid_shorts"
        assert decision.confidence == pytest.approx(1.0)

    @pytest.mark.parametrize("score_val,expected_action,expected_bias", [
        (-1, "avoid_shorts", "bearish"),
        (0, "avoid_shorts", "bearish"),       # bearish + confidence=1.0
        (3, "avoid_shorts", "bearish"),       # bearish + confidence=1.0
        (4, "stand_aside", "neutral"),
        (6, "stand_aside", "neutral"),
        (7, "look_for_longs", "bullish"),
        (10, "look_for_longs", "bullish"),
    ])
    def test_decision_score_threshold_boundaries(
        self, score_val: int, expected_action: str, expected_bias: str
    ) -> None:
        """Score boundaries map to correct actions with bias+confidence gating."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="X", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=100.0, trend_direction="above",
            ema21=90.0, ema21_slope=0.0, rsi14=50.0, mfi14=50.0,
            macd=0.0, macd_signal=0.0, macd_hist=0.0,
            atr14=0.0, bb_width=0.0,
        )
        engine = DecisionEngine()
        result = ConfluenceResult(expected_bias, score_val, 1.0, 10, ["test"])
        decision = engine.decide(snap, result)

        assert decision.action == expected_action, (
            f"score={score_val}, bias={expected_bias}: expected action={expected_action}, got {decision.action}"
        )

    def test_decision_target_swing_fallback(self) -> None:
        """Bullish with no liquidity above but swing high exists → target=swing_level."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0,
            macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05,
            last_swing_direction=1, last_swing_level=51000.0,
            nearest_liquidity_above=None,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        decision = engine.decide(snap, result)

        assert decision.target == 51000.0

    def test_decision_target_no_fallback(self) -> None:
        """Bullish with no liquidity and no swing → target=None."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0,
            macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05,
            last_swing_direction=None, last_swing_level=None,
            nearest_liquidity_above=None,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        decision = engine.decide(snap, result)

        assert decision.target is None

    def test_decision_both_liquidity_present(self) -> None:
        """Both liquidity levels present → both target and invalidation set."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0,
            macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05,
            nearest_liquidity_above=52000.0,
            nearest_liquidity_below=48000.0,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        decision = engine.decide(snap, result)

        assert decision.target == 52000.0
        assert decision.invalidation == 48000.0

    def test_decision_breakout_pending(self) -> None:
        """Confidence=0.5, bullish bias, swing within 1 ATR → breakout_pending=True."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=55.0, mfi14=52.0,
            macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05,
            last_swing_direction=1, last_swing_level=50400.0,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 0.5, 10, ["Uncertain"])
        decision = engine.decide(snap, result)

        assert decision.action == "stand_aside"
        assert decision.breakout_pending is True
        assert decision.breakout_level == 50400.0

    def test_decision_no_breakout_pending(self) -> None:
        """Confidence=0.8, bullish bias → no breakout (conf outside 0.3-0.7 range)."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=55.0, mfi14=52.0,
            macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05,
            last_swing_direction=1, last_swing_level=52000.0,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bullish", 10, 0.8, 10, ["High confidence"])
        decision = engine.decide(snap, result)

        assert decision.action == "look_for_longs"
        assert decision.breakout_pending is False
        assert decision.breakout_level is None

    def test_decision_context_hierarchical_influence(self) -> None:
        """With MarketContext where H4 disagrees. Bias stays from daily (hierarchical)."""
        from decision_engine import DecisionEngine

        daily = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above", ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0, macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05, last_bos_direction=1,
            nearest_liquidity_above=51000.0,
        )
        h4 = MarketSnapshot(
            symbol="BTC/USDT", timeframe="4h", timestamp=pd.Timestamp.now(),
            close=48000.0, trend_direction="below", ema21=49000.0, ema21_slope=-0.01,
            rsi14=40.0, mfi14=35.0, macd=50.0, macd_signal=90.0, macd_hist=-5.0,
            atr14=400.0, bb_width=0.05, last_bos_direction=-1,
            nearest_liquidity_below=46000.0,
        )
        ctx = MarketContext(daily=daily, h4=h4)

        engine = DecisionEngine()
        composite = ctx.composite_score()
        decision = engine.decide(daily, composite, context=ctx)

        assert decision.bias == "bullish"
        # Confidence should be reduced due to H4 disagreement
        assert decision.confidence < 1.0

    def test_decision_all_optional_none(self) -> None:
        """Snapshot with all structure/liquidity None → action=avoid_shorts (bearish+conf=1.0)."""
        from decision_engine import DecisionEngine

        snap = MarketSnapshot(
            symbol="X", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=100.0, trend_direction="below",
            ema21=0.0, ema21_slope=0.0, rsi14=50.0, mfi14=50.0,
            macd=0.0, macd_signal=0.0, macd_hist=0.0,
            atr14=0.0, bb_width=0.0,
            last_swing_direction=None, last_swing_level=None,
            last_bos_direction=None, last_bos_index=None,
            last_choch_direction=None, last_choch_index=None,
            nearest_liquidity_above=None, nearest_liquidity_below=None,
            active_bullish_ob=None, active_bearish_ob=None,
        )
        engine = DecisionEngine()
        result = ConfluenceResult("bearish", 0, 1.0, 10, ["All none"])
        decision = engine.decide(snap, result)

        assert decision.action == "avoid_shorts"
        assert decision.confidence == pytest.approx(1.0)
        assert decision.target is None
        assert decision.invalidation is None
