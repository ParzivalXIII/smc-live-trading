"""
orchestrator.py — State machine pipeline owner.

Wires the full trading pipeline: load TA data + update SMC buffer →
build market snapshot → score confluence → generate narrative →
make decision → journal entry.

Pure orchestration — no business logic, no scoring, no strategy.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Optional

from confluence import ConfluenceResult, ConfluenceScorer
from decision_engine import Decision, DecisionEngine
from journal import JournalEntry, JournalWriter, make_run_id
from live_smc_buffer import LiveSmcBuffer
from market_snapshot import MarketSnapshot, SnapshotBuilder
from narrative import MarketNarrative, MarketNarrativeBuilder
from trade_scripts.analyze_ta import load_ta_series


class OrchestrationState(Enum):
    IDLE = auto()
    LOAD = auto()
    ANALYZE = auto()
    DECIDE = auto()
    JOURNAL = auto()
    ERROR = auto()


_TRANSITIONS = {
    OrchestrationState.IDLE: {OrchestrationState.LOAD},
    OrchestrationState.LOAD: {OrchestrationState.ANALYZE, OrchestrationState.ERROR},
    OrchestrationState.ANALYZE: {OrchestrationState.DECIDE, OrchestrationState.ERROR},
    OrchestrationState.DECIDE: {OrchestrationState.JOURNAL, OrchestrationState.ERROR},
    OrchestrationState.JOURNAL: {OrchestrationState.IDLE, OrchestrationState.ERROR},
    OrchestrationState.ERROR: {OrchestrationState.IDLE},
}


@dataclass
class OrchestratorContext:
    """Runtime context for the LiveOrchestrator.

    Holds configuration and pipeline results as they flow through states.

    Attributes:
        symbol: Trading pair symbol (e.g. "BTCUSDT").
        timeframe: Candle timeframe (e.g. "1d", "4h").
        data_dir: Directory containing TA-enriched CSVs.
        db_path: Path to SQLite journal database.
        mode: "live" or "replay". In replay mode, load() skips data fetch
              and uses pre-populated ta_row and smc_report.
        ta_row: Latest TA-enriched row (pd.Series).
        smc_report: Rolling SMC report DataFrame.
        snapshot: Current MarketSnapshot.
        confluence: Scored ConfluenceResult.
        narrative: Generated MarketNarrative.
        decision: Computed Decision.
        entry: JournalEntry ready for persistence.
    """
    symbol: str
    timeframe: str
    data_dir: str = "data"
    db_path: str = "journal.db"
    mode: str = "live"
    # Runtime state — cleared by reset()
    ta_row: Any = None
    smc_report: Any = None
    snapshot: Optional[MarketSnapshot] = None
    confluence: Optional[ConfluenceResult] = None
    narrative: Optional[MarketNarrative] = None
    decision: Optional[Decision] = None
    entry: Optional[JournalEntry] = None


class LiveOrchestrator:
    """State machine that owns the trading pipeline.

    One public method: step() — called by the live loop or replay loop.
    Caller manages retry by calling reset() after ERROR.
    """

    def __init__(
        self,
        context: OrchestratorContext,
        smc_buffer: LiveSmcBuffer | None = None,
    ):
        self.state = OrchestrationState.IDLE
        self.context = context
        self._smc_buffer = smc_buffer or LiveSmcBuffer()
        self._last_error: Exception | None = None

    def _transition(self, next_state: OrchestrationState) -> None:
        """Validate and apply state transition."""
        valid = _TRANSITIONS.get(self.state, set())
        if next_state not in valid:
            raise RuntimeError(
                f"Invalid state transition: {self.state.name} → {next_state.name}. "
                f"Valid targets from {self.state.name}: {[s.name for s in valid]}"
            )
        self.state = next_state

    def reset(self) -> None:
        """Reset orchestrator to IDLE from any state. Clears runtime context.

        Must be called by the caller before retrying after an ERROR.
        Bypasses _transition() intentionally — reset works from ANY state.
        """
        self.state = OrchestrationState.IDLE
        self.context.ta_row = None
        self.context.smc_report = None
        self.context.snapshot = None
        self.context.confluence = None
        self.context.narrative = None
        self.context.decision = None
        self.context.entry = None
        self._last_error = None

    # ------------------------------------------------------------------
    # Pipeline steps — one method per state
    # ------------------------------------------------------------------

    def load(self) -> None:
        """LOAD state: fetch TA data and update SMC buffer.

        In replay mode, ta_row must be pre-populated by the caller.
        Buffer update runs unconditionally in both modes.
        """
        self._transition(OrchestrationState.LOAD)

        if self.context.mode == "live":
            df = load_ta_series(
                self.context.symbol,
                self.context.timeframe,
                self.context.data_dir,
                tail=1,
            )
            if df is not None and not df.empty:
                self.context.ta_row = df.iloc[-1]
            else:
                raise RuntimeError(
                    f"No TA data for {self.context.symbol} {self.context.timeframe} "
                    f"in {self.context.data_dir}"
                )

        # Buffer update and report fetching run in BOTH modes
        if self.context.ta_row is not None:
            self._smc_buffer.update(self.context.ta_row)
            self.context.smc_report = self._smc_buffer.get_smc_report()

    def analyze(self) -> None:
        """ANALYZE state: build snapshot, score confluence, generate narrative."""
        self._transition(OrchestrationState.ANALYZE)

        if self.context.ta_row is None or self.context.smc_report is None:
            raise RuntimeError("Cannot analyze: no data loaded")

        builder = SnapshotBuilder()
        self.context.snapshot = builder.build(
            self.context.symbol,
            self.context.timeframe,
            self.context.ta_row,
            self.context.smc_report,
        )
        self.context.confluence = ConfluenceScorer().score(self.context.snapshot)
        self.context.narrative = MarketNarrativeBuilder().build(
            self.context.snapshot, self.context.confluence
        )

    def decide(self) -> None:
        """DECIDE state: create decision object from snapshot + confluence."""
        self._transition(OrchestrationState.DECIDE)

        if self.context.snapshot is None or self.context.confluence is None:
            raise RuntimeError("Cannot decide: no analysis results")

        self.context.decision = DecisionEngine().decide(
            self.context.snapshot, self.context.confluence
        )

    def journal(self) -> None:
        """JOURNAL state: build JournalEntry from pipeline results.

        The caller is responsible for persisting the entry via
        sync_write_entry() or direct async JournalWriter calls.
        """
        self._transition(OrchestrationState.JOURNAL)

        snap = self.context.snapshot
        conf = self.context.confluence
        narr = self.context.narrative
        dec = self.context.decision
        if not all([snap, conf, narr, dec]):
            raise RuntimeError("Cannot journal: missing pipeline data")

        events = self._smc_buffer.events
        self.context.entry = JournalEntry(
            run_id=make_run_id(snap.symbol, snap.timeframe, snap.timestamp),
            timestamp=snap.timestamp,
            symbol=snap.symbol,
            timeframe=snap.timeframe,
            close=snap.close,
            direction_score=conf.direction_score,
            bias=conf.bias,
            confidence=conf.confidence,
            narrative_summary=narr.conclusion,
            decision_action=dec.action,
            decision_invalidation=dec.invalidation,
            decision_target=dec.target,
            breakout_pending=dec.breakout_pending,
            events=events,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def step(self) -> None:
        """Execute one full pipeline cycle.

        States: IDLE → LOAD → ANALYZE → DECIDE → JOURNAL → IDLE.

        On failure: transitions to ERROR and re-raises.
        Caller must call reset() before retrying.

        Raises:
            RuntimeError: On pipeline failure (missing data, invalid transitions, etc.).
        """
        try:
            self.load()
            self.analyze()
            self.decide()
            self.journal()
            self._transition(OrchestrationState.IDLE)
        except Exception as e:
            self._last_error = e
            self.state = OrchestrationState.ERROR  # Direct set, safe from any state
            raise


# ------------------------------------------------------------------
# Async bridge
# ------------------------------------------------------------------

def sync_write_entry(writer: JournalWriter, entry: JournalEntry) -> None:
    """Synchronous helper to persist a JournalEntry via async JournalWriter.

    Wraps asyncio.run() for callers in synchronous contexts.
    For async callers, use await writer.append(entry) + await writer.flush() directly.

    Args:
        writer: An open JournalWriter instance (must be inside its async context).
        entry: The JournalEntry to persist.

    Raises:
        RuntimeError: If persistence fails.
    """
    try:
        asyncio.run(writer.append(entry))
        asyncio.run(writer.flush())
    except Exception as e:
        raise RuntimeError(f"Failed to write journal entry: {e}") from e
