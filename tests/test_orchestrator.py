"""Tests for LiveOrchestrator — state machine and pipeline."""

import pytest
from unittest.mock import MagicMock

from orchestrator import (
    LiveOrchestrator, OrchestratorContext, OrchestrationState, _TRANSITIONS,
    sync_write_entry,
)


class TestOrchestratorStateMachine:
    def test_initial_state(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
        orch = LiveOrchestrator(ctx)
        assert orch.state == OrchestrationState.IDLE

    def test_transitions_complete(self):
        all_states = set(OrchestrationState)
        covered = set(_TRANSITIONS.keys())
        assert covered == all_states, f"Missing: {all_states - covered}"

    def test_valid_transition(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
        orch = LiveOrchestrator(ctx)
        orch._transition(OrchestrationState.LOAD)
        assert orch.state == OrchestrationState.LOAD

    def test_invalid_transition_raises(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
        orch = LiveOrchestrator(ctx)
        with pytest.raises(RuntimeError, match="Invalid state transition"):
            orch._transition(OrchestrationState.DECIDE)  # IDLE → DECIDE invalid

    def test_reset_clears_context(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
        ctx.ta_row = "dummy"
        ctx.smc_report = "dummy"
        ctx.snapshot = "dummy"
        orch = LiveOrchestrator(ctx)
        orch.state = OrchestrationState.ERROR
        orch.reset()
        assert orch.state == OrchestrationState.IDLE
        assert orch.context.ta_row is None
        assert orch.context.smc_report is None
        assert orch.context.snapshot is None
        assert orch._last_error is None

    def test_reset_from_error_state(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
        orch = LiveOrchestrator(ctx)
        orch.state = OrchestrationState.ERROR
        orch._last_error = RuntimeError("test")
        orch.reset()
        assert orch.state == OrchestrationState.IDLE
        assert orch._last_error is None


class TestOrchestratorReplayMode:
    def test_replay_mode_skips_load(self):
        import pandas as pd
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d", mode="replay")
        ctx.ta_row = pd.Series({  # Pre-populated by caller
            "open": 50000.0, "high": 50100.0, "low": 49900.0,
            "close": 50000.0, "volume": 100.0,
        })
        orch = LiveOrchestrator(ctx)
        orch.load()  # Should not call load_ta_series
        assert orch.context.ta_row is not None  # Still pre-populated

    def test_replay_mode_sets_correct_state(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d", mode="replay")
        orch = LiveOrchestrator(ctx)
        orch.load()
        assert orch.state == OrchestrationState.LOAD

    def test_default_mode_is_live(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
        assert ctx.mode == "live"


class TestOrchestratorPipelineGuards:
    def test_analyze_without_load_raises(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
        orch = LiveOrchestrator(ctx)
        orch.state = type(orch.state).LOAD  # Set valid starting state
        with pytest.raises(RuntimeError, match="Cannot analyze"):
            orch.analyze()

    def test_decide_without_analyze_raises(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
        orch = LiveOrchestrator(ctx)
        orch.state = type(orch.state).ANALYZE  # Set valid starting state
        with pytest.raises(RuntimeError, match="Cannot decide"):
            orch.decide()

    def test_journal_without_decide_raises(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
        orch = LiveOrchestrator(ctx)
        orch.state = type(orch.state).DECIDE  # Set valid starting state
        with pytest.raises(RuntimeError, match="Cannot journal"):
            orch.journal()


class TestOrchestratorMockPipeline:
    def test_full_step_with_mock_buffer(self):
        """Test step() completes IDLE→LOAD→ANALYZE→DECIDE→JOURNAL→IDLE with mocks."""
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d", mode="replay")
        
        # Pre-populate context
        import pandas as pd
        ctx.ta_row = pd.Series({
            "close": 50000.0, "high": 50100.0, "low": 49900.0,
            "open": 50000.0, "volume": 100.0,
            "ema21": 49000.0, "ema21_slope": 0.01,
            "rsi14": 60.0, "mfi14": 55.0,
            "macd": 100.0, "macd_signal": 80.0, "macd_hist": 20.0,
            "atr14": 1000.0, "bb_width": 0.05,
        })
        ctx.smc_report = pd.DataFrame({
            "Timestamp": pd.date_range("2024-01-01", periods=50, freq="h"),
            "Close": 50000.0, "SwingHighLow": 1.0, "SwingLevel": 51000.0,
            "OB": 1.0, "Liquidity": 1.0, "LiqLevel": 51500.0,
        })
        
        # Mock buffer
        from unittest.mock import MagicMock
        mock_buffer = MagicMock()
        mock_buffer.events = []
        mock_buffer.get_smc_report.return_value = ctx.smc_report
        
        orch = LiveOrchestrator(ctx, smc_buffer=mock_buffer)
        orch.step()
        assert orch.state == OrchestrationState.IDLE
        assert orch.context.snapshot is not None
        assert orch.context.confluence is not None
        assert orch.context.narrative is not None
        assert orch.context.decision is not None
        assert orch.context.entry is not None

    def test_step_produces_journal_entry(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d", mode="replay")
        import pandas as pd
        ctx.ta_row = pd.Series({
            "close": 50000.0, "high": 50100.0, "low": 49900.0,
            "open": 50000.0, "volume": 100.0,
            "ema21": 49000.0, "ema21_slope": 0.01,
            "rsi14": 60.0, "mfi14": 55.0,
            "macd": 100.0, "macd_signal": 80.0, "macd_hist": 20.0,
            "atr14": 1000.0, "bb_width": 0.05,
        })
        ctx.smc_report = pd.DataFrame({
            "Timestamp": pd.date_range("2024-01-01", periods=50, freq="h"),
            "Close": 50000.0, "SwingHighLow": 1.0, "SwingLevel": 51000.0,
            "OB": 1.0, "Liquidity": 1.0, "LiqLevel": 51500.0,
        })
        mock_buffer = MagicMock()
        mock_buffer.events = []
        mock_buffer.get_smc_report.return_value = ctx.smc_report
        
        orch = LiveOrchestrator(ctx, smc_buffer=mock_buffer)
        orch.step()
        entry = orch.context.entry
        assert entry.symbol == "BTCUSDT"
        assert entry.timeframe == "1d"
        assert entry.bias in ("bullish", "bearish", "neutral")
        assert entry.decision_action in (
            "look_for_longs", "avoid_shorts", "stand_aside"
        )


class TestOrchestratorErrorHandling:
    def test_step_with_bad_data_raises(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d", mode="replay")
        # Missing ta_row — load should fail in replay with pre-populated...
        # Actually in replay mode load() returns early, so this won't fail.
        # Let's test with live mode and no data file instead.
        ctx2 = OrchestratorContext(symbol="NONEXISTENT", timeframe="1d", mode="live")
        orch = LiveOrchestrator(ctx2)
        with pytest.raises(RuntimeError):
            orch.step()
        assert orch.state == OrchestrationState.ERROR

    def test_step_transitions_to_error_on_failure(self):
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d", mode="replay")
        # Will fail because no ta_row pre-populated
        orch = LiveOrchestrator(ctx)
        try:
            orch.step()
        except RuntimeError:
            pass
        assert orch.state == OrchestrationState.ERROR
        assert orch._last_error is not None

    def test_step_error_state_doesnt_double_fail(self):
        """Verify that _transition in except block doesn't crash."""
        ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d", mode="replay")
        orch = LiveOrchestrator(ctx)
        # First step fails
        try:
            orch.step()
        except RuntimeError:
            pass
        assert orch.state == OrchestrationState.ERROR
        # reset() should work from ERROR
        orch.reset()
        assert orch.state == OrchestrationState.IDLE


class TestSyncWriteEntry:
    def test_sync_write_entry_function_exists(self):
        """sync_write_entry should be a callable function."""
        assert callable(sync_write_entry)
