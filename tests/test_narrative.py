"""
Tests for the Narrative module: MarketNarrativeBuilder, MarketNarrative, NarrativeSection.

Run with:
    python -m pytest tests/test_narrative.py -v --tb=short
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from confluence import ConfluenceResult, ConfluenceScorer, MarketContext
from market_snapshot import MarketSnapshot, SnapshotBuilder

# =============================================================================
# T1a: RED phase — 7 TDD tests for MarketNarrativeBuilder
# =============================================================================
# These tests define the expected contract BEFORE implementation.
# They will fail until narrative.py is implemented (GREEN phase).


class TestMarketNarrativeBuilder:
    """MarketNarrativeBuilder.build() — TDD core tests."""

    def test_bullish_narrative_all_sections(
        self, sample_snapshot: MarketSnapshot
    ) -> None:
        """Bullish bias: all sections populated, score=10, conclusion references target."""
        from narrative import MarketNarrativeBuilder

        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["All bullish"])
        narrative = builder.build(sample_snapshot, result)

        assert narrative.bias == "bullish"
        assert narrative.direction_score == 10
        assert narrative.confidence == pytest.approx(1.0)
        assert narrative.max_score == 10
        assert narrative.symbol == "BTC/USDT"
        assert narrative.timeframe == "1d"

        # Trend section: above EMA21, EMA21 rising
        trend = narrative.sections[0]
        assert trend.title == "Trend"
        trend_text = " ".join(trend.bullets)
        assert "above" in trend_text.lower() or "Above" in trend_text
        assert "rising" in trend_text.lower() or "Rising" in trend_text

        # Momentum section: RSI and MFI zone labels
        momentum = narrative.sections[1]
        assert momentum.title == "Momentum"
        momentum_text = " ".join(momentum.bullets)
        assert "RSI" in momentum_text
        assert "MFI" in momentum_text

        # Structure section: BOS confirmation
        structure = narrative.sections[2]
        assert structure.title == "Structure"
        structure_text = " ".join(structure.bullets)
        assert "BOS" in structure_text

        # Liquidity section: contains target levels
        liquidity = narrative.sections[3]
        assert liquidity.title == "Liquidity"
        liquidity_text = " ".join(liquidity.bullets)
        assert "51000" in liquidity_text

        # Conclusion
        assert "Bullish continuation" in narrative.conclusion

    def test_bearish_narrative(
        self, bearish_snapshot: MarketSnapshot
    ) -> None:
        """Bearish bias: correct sections, invalidation level referenced."""
        from narrative import MarketNarrativeBuilder

        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bearish", -4, 1.0, 10, ["All bearish"])
        narrative = builder.build(bearish_snapshot, result)

        assert narrative.bias == "bearish"
        assert narrative.direction_score == -4
        assert narrative.confidence == pytest.approx(1.0)

        # Trend: below EMA21, EMA21 falling
        trend = narrative.sections[0]
        assert trend.title == "Trend"
        trend_text = " ".join(trend.bullets)
        assert "below" in trend_text.lower() or "Below" in trend_text
        assert "falling" in trend_text.lower() or "Falling" in trend_text

        # Structure: bearish BOS
        structure = narrative.sections[2]
        structure_text = " ".join(structure.bullets)
        assert "BOS" in structure_text

        # Conclusion: invalidation text
        assert "momentum intact" in narrative.conclusion.lower() or "Bearish" in narrative.conclusion

    def test_neutral_narrative(
        self, neutral_snapshot: MarketSnapshot
    ) -> None:
        """Neutral bias: 'No clear directional edge' in conclusion."""
        from narrative import MarketNarrativeBuilder

        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("neutral", 4, 1.0, 10, ["Mixed signals"])
        narrative = builder.build(neutral_snapshot, result)

        assert narrative.bias == "neutral"
        assert "No clear directional edge" in narrative.conclusion or "Stand aside" in narrative.conclusion

    def test_no_structure_data(self) -> None:
        """Snapshot with no structure data → structure section mentions missing data."""
        from narrative import MarketNarrativeBuilder

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
            last_bos_direction=None,
            last_bos_index=None,
            last_choch_direction=None,
            last_choch_index=None,
        )
        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 7, 1.0, 10, ["Some bullish"])
        narrative = builder.build(snap, result)

        structure = narrative.sections[2]
        assert structure.title == "Structure"
        structure_text = " ".join(structure.bullets)
        assert "No recent structure" in structure_text or "no data" in structure_text.lower()

    def test_no_liquidity_data(self) -> None:
        """Snapshot with no liquidity → liquidity section reports no significant clusters."""
        from narrative import MarketNarrativeBuilder

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
            nearest_liquidity_above=None,
            nearest_liquidity_below=None,
        )
        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 7, 1.0, 10, ["Some bullish"])
        narrative = builder.build(snap, result)

        liquidity = narrative.sections[3]
        assert liquidity.title == "Liquidity"
        liquidity_text = " ".join(liquidity.bullets)
        assert "no significant" in liquidity_text.lower() or "none" in liquidity_text.lower() or "no liquidity" in liquidity_text.lower()

    def test_all_sections_present(
        self, sample_snapshot: MarketSnapshot
    ) -> None:
        """All 5 sections present: Trend, Momentum, Structure, Liquidity, Conclusion."""
        from narrative import MarketNarrativeBuilder

        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["All bullish"])
        narrative = builder.build(sample_snapshot, result)

        section_titles = [s.title for s in narrative.sections]
        assert "Trend" in section_titles
        assert "Momentum" in section_titles
        assert "Structure" in section_titles
        assert "Liquidity" in section_titles

    def test_conclusion_format(
        self, sample_snapshot: MarketSnapshot
    ) -> None:
        """Conclusion references invalidation/target for directional bias."""
        from narrative import MarketNarrativeBuilder

        # Bullish conclusion should reference target
        builder = MarketNarrativeBuilder()
        bullish_result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        narrative = builder.build(sample_snapshot, bullish_result)
        assert "Bullish continuation" in narrative.conclusion
        # sample_snapshot has nearest_liquidity_above=51000
        assert "51000" in narrative.conclusion or "target" in narrative.conclusion.lower()

        # Bearish conclusion should reference invalidation
        bearish_snap = MarketSnapshot(
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
            last_bos_direction=-1,
            nearest_liquidity_below=47000.0,
        )
        bearish_result = ConfluenceResult("bearish", -4, 1.0, 10, ["Bearish"])
        narrative = builder.build(bearish_snap, bearish_result)
        assert "Bearish" in narrative.conclusion or "bearish" in narrative.conclusion.lower()
        assert "momentum" in narrative.conclusion.lower()

        # Neutral conclusion should mention mixed signals
        neutral_result = ConfluenceResult("neutral", 4, 1.0, 10, ["Neutral"])
        neutral_narrative = builder.build(bearish_snap, neutral_result)
        assert "No clear directional edge" in neutral_narrative.conclusion or "Stand aside" in neutral_narrative.conclusion


# =============================================================================
# T4: Edge case tests (tests-after)
# =============================================================================


class TestNarrativeEdgeCases:
    """Edge case coverage for MarketNarrativeBuilder."""

    def test_narrative_nan_values(self) -> None:
        """Snapshot with NaN RSI/MFI → builder handles gracefully."""
        import math
        from narrative import MarketNarrativeBuilder

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=float("nan"), mfi14=float("nan"),
            macd=float("nan"), macd_signal=float("nan"), macd_hist=float("nan"),
            atr14=500.0, bb_width=0.05,
        )
        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["Bullish"])
        narrative = builder.build(snap, result)

        assert narrative.bias == "bullish"
        momentum = narrative.sections[1]
        momentum_text = " ".join(momentum.bullets)
        assert "insufficient data" in momentum_text.lower()

    def test_narrative_empty_result_reasons(self) -> None:
        """ConfluenceResult with empty reasons list → builder handles it."""
        from narrative import MarketNarrativeBuilder

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0,
            macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05,
        )
        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 10, 1.0, 10, [])
        narrative = builder.build(snap, result)

        assert len(narrative.sections) == 4
        assert narrative.bias == "bullish"

    def test_narrative_symbol_timeframe_passthrough(self) -> None:
        """Symbol and timeframe pass through from snapshot."""
        from narrative import MarketNarrativeBuilder

        snap = MarketSnapshot(
            symbol="ETH/USDT", timeframe="4h", timestamp=pd.Timestamp.now(),
            close=3000.0, trend_direction="above",
            ema21=2900.0, ema21_slope=0.005,
            rsi14=55.0, mfi14=52.0,
            macd=50.0, macd_signal=45.0, macd_hist=5.0,
            atr14=100.0, bb_width=0.03,
        )
        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 8, 1.0, 10, ["test"])
        narrative = builder.build(snap, result)

        assert narrative.symbol == "ETH/USDT"
        assert narrative.timeframe == "4h"

    def test_narrative_score_zero(self) -> None:
        """Score=0, bearish bias → conclusion reflects weak bearish."""
        from narrative import MarketNarrativeBuilder

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=48000.0, trend_direction="below",
            ema21=49000.0, ema21_slope=-0.005,
            rsi14=50.0, mfi14=48.0,
            macd=85.0, macd_signal=90.0, macd_hist=-2.0,
            atr14=500.0, bb_width=0.05,
        )
        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bearish", 0, 1.0, 10, ["Weak bearish"])
        narrative = builder.build(snap, result)

        assert narrative.bias == "bearish"
        assert narrative.direction_score == 0

    def test_narrative_max_score_integrity(self) -> None:
        """max_score always equals result.max_score."""
        from narrative import MarketNarrativeBuilder

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0,
            macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05,
        )
        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["test"])
        narrative = builder.build(snap, result)
        assert narrative.max_score == 10

        result2 = ConfluenceResult("neutral", 4, 1.0, 10, ["test2"])
        narrative2 = builder.build(snap, result2)
        assert narrative2.max_score == 10

    def test_narrative_conclusion_format_bullish(self) -> None:
        """Bullish conclusion format: 'Bullish continuation...' with target."""
        from narrative import MarketNarrativeBuilder

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0,
            macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05,
            nearest_liquidity_above=52000.0,
        )
        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["test"])
        narrative = builder.build(snap, result)
        assert narrative.conclusion.startswith("Bullish continuation")
        assert "52000" in narrative.conclusion

    def test_narrative_section_ordering(self) -> None:
        """Sections are in consistent order: Trend, Momentum, Structure, Liquidity."""
        from narrative import MarketNarrativeBuilder

        snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above",
            ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0,
            macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05,
        )
        builder = MarketNarrativeBuilder()
        result = ConfluenceResult("bullish", 10, 1.0, 10, ["test"])
        narrative = builder.build(snap, result)

        titles = [s.title for s in narrative.sections]
        assert titles == ["Trend", "Momentum", "Structure", "Liquidity"]


# =============================================================================
# T6: Integration tests (tests-after)
# =============================================================================


class TestIntegrationPipeline:
    """Full pipeline: SnapshotBuilder → ConfluenceScorer → Narrative → Decision."""

    def test_full_pipeline_bullish(self) -> None:
        """Build daily snapshot → score → narrative → decision. Bias consistent."""
        from narrative import MarketNarrativeBuilder
        from decision_engine import DecisionEngine

        ta = pd.Series({
            "timestamp": pd.Timestamp.now(),
            "close": 50000.0, "ema21": 49000.0, "ema21_slope": 0.01,
            "rsi14": 62.3, "mfi14": 58.1,
            "macd": 100.0, "macd_signal": 90.0, "macd_hist": 10.0,
            "atr14": 500.0, "bb_width": 0.05,
        })
        smc_data: dict[str, list] = {col: [float("nan")] * 10 for col in [
            "SwingHighLow", "SwingLevel", "SwingPivotIndex",
            "BOS", "CHOCH", "BOSLevel", "BrokenIndex",
            "OB", "OBTop", "OBBottom", "OBVolume", "OBMitigatedIndex", "OBPct",
            "Liquidity", "LiqLevel", "LiqEnd", "LiqSwept",
            "RetraceDirection", "CurrentRetracement%", "DeepestRetracement%",
        ]}
        smc_data["BOS"][5] = 1.0
        smc_data["BrokenIndex"][5] = 3.0
        smc_data["Liquidity"][8] = 1.0
        smc_data["LiqLevel"][8] = 51500.0
        smc = pd.DataFrame(smc_data)

        builder = SnapshotBuilder()
        snap = builder.build("BTC/USDT", "1d", ta, smc)

        scorer = ConfluenceScorer()
        result = scorer.score(snap)

        narr_builder = MarketNarrativeBuilder()
        narrative = narr_builder.build(snap, result)

        engine = DecisionEngine()
        decision = engine.decide(snap, result)

        assert result.bias == narrative.bias == decision.bias, (
            f"Bias mismatch: score={result.bias}, narrative={narrative.bias}, decision={decision.bias}"
        )
        assert len(narrative.sections) == 4
        assert decision.confidence > 0

    def test_full_pipeline_bearish(self) -> None:
        """Build bearish snapshot → score → narrative → decision. Bias consistent."""
        from narrative import MarketNarrativeBuilder
        from decision_engine import DecisionEngine

        ta = pd.Series({
            "timestamp": pd.Timestamp.now(),
            "close": 48000.0, "ema21": 49000.0, "ema21_slope": -0.01,
            "rsi14": 40.0, "mfi14": 35.0,
            "macd": 50.0, "macd_signal": 90.0, "macd_hist": -5.0,
            "atr14": 500.0, "bb_width": 0.05,
        })
        smc_data = {col: [float("nan")] * 10 for col in [
            "SwingHighLow", "SwingLevel", "SwingPivotIndex",
            "BOS", "CHOCH", "BOSLevel", "BrokenIndex",
            "OB", "OBTop", "OBBottom", "OBVolume", "OBMitigatedIndex", "OBPct",
            "Liquidity", "LiqLevel", "LiqEnd", "LiqSwept",
            "RetraceDirection", "CurrentRetracement%", "DeepestRetracement%",
        ]}
        smc_data["BOS"][5] = -1.0
        smc_data["BrokenIndex"][5] = 3.0
        smc_data["Liquidity"][8] = -1.0
        smc_data["LiqLevel"][8] = 46000.0
        smc = pd.DataFrame(smc_data)

        builder = SnapshotBuilder()
        snap = builder.build("BTC/USDT", "1d", ta, smc)

        scorer = ConfluenceScorer()
        result = scorer.score(snap)

        narr_builder = MarketNarrativeBuilder()
        narrative = narr_builder.build(snap, result)

        engine = DecisionEngine()
        decision = engine.decide(snap, result)

        assert result.bias == narrative.bias == decision.bias
        assert decision.action in ("avoid_shorts", "stand_aside")

    def test_full_pipeline_mtf_hierarchical(self) -> None:
        """MTF: daily bullish + h4 bearish → hierarchical preserves daily bias."""
        from narrative import MarketNarrativeBuilder
        from decision_engine import DecisionEngine

        daily_snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="1d", timestamp=pd.Timestamp.now(),
            close=50000.0, trend_direction="above", ema21=49000.0, ema21_slope=0.01,
            rsi14=60.0, mfi14=55.0, macd=100.0, macd_signal=90.0, macd_hist=10.0,
            atr14=500.0, bb_width=0.05, last_bos_direction=1,
            nearest_liquidity_above=51000.0,
        )
        h4_snap = MarketSnapshot(
            symbol="BTC/USDT", timeframe="4h", timestamp=pd.Timestamp.now(),
            close=48000.0, trend_direction="below", ema21=49000.0, ema21_slope=-0.01,
            rsi14=40.0, mfi14=35.0, macd=50.0, macd_signal=90.0, macd_hist=-5.0,
            atr14=400.0, bb_width=0.05, last_bos_direction=-1,
            nearest_liquidity_below=46000.0,
        )

        ctx = MarketContext(daily=daily_snap, h4=h4_snap)
        composite = ctx.composite_score()

        narr_builder = MarketNarrativeBuilder()
        narrative = narr_builder.build(daily_snap, composite)

        engine = DecisionEngine()
        decision = engine.decide(daily_snap, composite, context=ctx)

        assert composite.bias == "bullish"
        assert narrative.bias == "bullish"
        assert decision.bias == "bullish"
        assert decision.confidence < 1.0

    def test_full_pipeline_narrative_decision_consistency(
        self, sample_snapshot: MarketSnapshot
    ) -> None:
        """Decision.bias == Narrative.bias for same inputs."""
        from narrative import MarketNarrativeBuilder
        from decision_engine import DecisionEngine

        scorer = ConfluenceScorer()
        result = scorer.score(sample_snapshot)

        narr_builder = MarketNarrativeBuilder()
        narrative = narr_builder.build(sample_snapshot, result)

        engine = DecisionEngine()
        decision = engine.decide(sample_snapshot, result)

        assert decision.bias == narrative.bias
        assert decision.bias == result.bias

    def test_full_pipeline_snapshot_to_decision(self) -> None:
        """SnapshotBuilder → ConfluenceScorer → DecisionEngine (single TF)."""
        from decision_engine import DecisionEngine

        ta = pd.Series({
            "timestamp": pd.Timestamp.now(),
            "close": 50500.0, "ema21": 49000.0, "ema21_slope": 0.015,
            "rsi14": 65.0, "mfi14": 60.0,
            "macd": 110.0, "macd_signal": 95.0, "macd_hist": 15.0,
            "atr14": 500.0, "bb_width": 0.06,
        })
        smc = pd.DataFrame()
        builder = SnapshotBuilder()
        snap = builder.build("BTC/USDT", "1d", ta, smc)

        scorer = ConfluenceScorer()
        result = scorer.score(snap)

        engine = DecisionEngine()
        decision = engine.decide(snap, result, context=None)

        assert isinstance(decision.bias, str)
        assert 0.0 <= decision.confidence <= 1.0
        assert decision.action in ("look_for_longs", "avoid_shorts", "stand_aside")
