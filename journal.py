"""
journal.py — SQLite-based Decision Journal.

Records every complete decision cycle (snapshot → confluence → narrative → decision)
to a SQLite database via aiosqlite.

The journal is a pure observer at the orchestration layer — zero changes to
existing pipeline classes (MarketSnapshot, ConfluenceResult, MarketNarrative,
Decision, StructureEvent).

Usage::

    async with JournalWriter("journal.db") as writer:
        await writer.append(entry)
        await writer.flush()
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from smartmoneyconcepts.structures import StructureEvent

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS journal_runs (
    run_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    close REAL,
    direction_score REAL,
    bias TEXT,
    confidence REAL,
    narrative_summary TEXT,
    decision_action TEXT,
    decision_invalidation REAL,
    decision_target REAL,
    breakout_pending INTEGER,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_runs_symbol_timeframe ON journal_runs(symbol, timeframe);
CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON journal_runs(timestamp);

CREATE TABLE IF NOT EXISTS journal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT,
    direction INTEGER,
    status TEXT,
    level REAL,
    event_timestamp TEXT,
    FOREIGN KEY (run_id) REFERENCES journal_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_events_run_id ON journal_events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_event_id ON journal_events(event_id);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_symbol(symbol: str) -> str:
    """Strip non-alphanumeric characters except ``-``, ``_``, and ``.``.

    Example: ``"BTC/USDT"`` → ``"BTCUSDT"``, ``"ETH-USD"`` → ``"ETH-USD"``,
    ``"SOL.X"`` → ``"SOL.X"``.
    """
    return "".join(c for c in symbol if c.isalnum() or c in ("-", "_", "."))


def make_run_id(symbol: str, timeframe: str, timestamp: pd.Timestamp) -> str:
    """Generate a compound run ID.

    Format: ``{sanitized_symbol}_{timeframe}_{YYYYMMDDTHHmmss}_{8-char-uuid}``

    Example: ``"BTCUSDT_1h_20260624T120000_a1b2c3d4"``
    """
    safe_symbol = _sanitize_symbol(symbol)
    ts = timestamp.strftime("%Y%m%dT%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"{safe_symbol}_{timeframe}_{ts}_{short}"


def _safe(value: float | None) -> float | None:
    """Convert NaN float values to ``None`` (for SQLite NULL storage).

    Passes ``None`` and non-NaN floats through unchanged.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _now_iso() -> str:
    """Return the current UTC timestamp as ISO 8601 (``YYYY-MM-DDTHH:MM:SSZ``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# JournalEntry
# ---------------------------------------------------------------------------


@dataclass
class JournalEntry:
    """A single decision-cycle record destined for the journal database.

    Attributes:
        run_id: Compound primary key (``{symbol}_{timeframe}_{timestamp}_{uuid}``).
        timestamp: Snapshot timestamp.
        symbol: Trading pair symbol.
        timeframe: Timeframe label (e.g. ``"1d"``, ``"4h"``, ``"1h"``).
        close: Close price at decision time.
        direction_score: HTF regime strength score (from ConfluenceResult).
        bias: Directional bias (``"bullish"`` / ``"bearish"`` / ``"neutral"``).
        confidence: LTF alignment quality multiplier (0.0 – 1.0).
        narrative_summary: Final conclusion from MarketNarrative.
        decision_action: Recommended action (e.g. ``"look_for_longs"``).
        decision_invalidation: Price level that invalidates the bias (or ``None``).
        decision_target: Price target in the bias direction (or ``None``).
        breakout_pending: If ``True``, a breakout is brewing but unconfirmed.
        events: StructureEvent objects active at decision time.
    """

    run_id: str
    timestamp: pd.Timestamp
    symbol: str
    timeframe: str
    close: float
    direction_score: float
    bias: str
    confidence: float
    narrative_summary: str
    decision_action: str
    decision_invalidation: float | None
    decision_target: float | None
    breakout_pending: bool
    events: list[StructureEvent]


# ---------------------------------------------------------------------------
# JournalWriter
# ---------------------------------------------------------------------------


class JournalWriter:
    """Async context manager that writes :class:`JournalEntry` objects to SQLite.

    Usage::

        async with JournalWriter("journal.db") as writer:
            await writer.append(entry)
            # __aexit__ flushes and closes automatically

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.
    buffer_size : int
        Maximum number of buffered entries before an automatic flush.
        ``0`` means flush after every append (no buffering). Default ``100``.
    """

    def __init__(self, db_path: str, buffer_size: int = 100) -> None:
        self.db_path = db_path
        self.buffer_size = buffer_size
        self._conn = None
        self._buffer: list[JournalEntry] = []

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> JournalWriter:
        import aiosqlite

        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.executescript(SCHEMA_SQL)
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._buffer:
            await self.flush()
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _entry_to_row(entry: JournalEntry) -> tuple:
        """Convert a :class:`JournalEntry` to a DB row tuple.

        NaN float values are converted to ``None`` (SQLite NULL).
        :class:`pd.Timestamp` is converted to ISO-format string.
        ``bool`` is converted to ``int`` (0/1).
        """
        return (
            entry.run_id,
            str(entry.timestamp),
            entry.symbol,
            entry.timeframe,
            _safe(entry.close),
            _safe(entry.direction_score),
            entry.bias,
            _safe(entry.confidence),
            entry.narrative_summary,
            entry.decision_action,
            _safe(entry.decision_invalidation),
            _safe(entry.decision_target),
            int(entry.breakout_pending),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def append(self, entry: JournalEntry) -> None:
        """Buffer a :class:`JournalEntry` for writing.

        The entry is not written to the database until :meth:`flush` is
        called (or automatically if the buffer exceeds ``buffer_size``).
        """
        self._buffer.append(entry)
        if self.buffer_size == 0:
            await self.flush()
        elif len(self._buffer) >= self.buffer_size:
            await self.flush()

    async def flush(self) -> None:
        """Write all buffered entries to the database in a single transaction.

        Each entry produces one row in ``journal_runs`` plus one row in
        ``journal_events`` for each associated :class:`StructureEvent`.
        """
        if not self._buffer or self._conn is None:
            return

        for entry in self._buffer:
            row = self._entry_to_row(entry)
            await self._conn.execute(
                """INSERT INTO journal_runs
                   (run_id, timestamp, symbol, timeframe, close,
                    direction_score, bias, confidence, narrative_summary,
                    decision_action, decision_invalidation, decision_target,
                    breakout_pending)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
            for event in entry.events:
                await self._conn.execute(
                    """INSERT INTO journal_events
                       (run_id, event_id, event_type, direction, status,
                        level, event_timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.run_id,
                        event.event_id,
                        event.event_type,
                        event.direction,
                        event.status,
                        event.level,
                        str(event.timestamp) if event.timestamp else None,
                    ),
                )

        await self._conn.commit()
        self._buffer.clear()

    async def query_runs(
        self,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query journal runs with optional filters.

        Parameters
        ----------
        symbol : str, optional
            Filter by symbol.
        timeframe : str, optional
            Filter by timeframe.
        limit : int
            Maximum number of rows to return (default ``100``).

        Returns
        -------
        list[dict]
            List of row dicts with column names as keys.
        """
        query = "SELECT * FROM journal_runs WHERE 1=1"
        params: list = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if timeframe:
            query += " AND timeframe = ?"
            params.append(timeframe)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        assert self._conn is not None
        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
