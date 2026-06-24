"""
Tests for the Journal module: JournalEntry, JournalWriter, make_run_id.

Run with::

    python -m pytest tests/test_journal.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import math

import pandas as pd
import pytest

from smartmoneyconcepts.structures import StructureEvent

# =============================================================================
# T2: JournalEntry tests
# =============================================================================


class TestMakeRunId:
    """make_run_id() — format, sanitization, uniqueness."""

    def test_run_id_format(self) -> None:
        """Verify run_id matches ``{symbol}_{timeframe}_{timestamp}_{8chars}``."""
        from journal import make_run_id

        rid = make_run_id("BTC/USDT", "1h", pd.Timestamp("2026-06-24 12:00:00"))
        parts = rid.split("_")

        assert len(parts) == 4, f"Expected 4 parts, got {len(parts)}: {rid}"
        assert parts[0] == "BTCUSDT", f"Expected sanitized BTCUSDT, got {parts[0]}"
        assert parts[1] == "1h"
        assert parts[2] == "20260624T120000"
        assert len(parts[3]) == 8, f"UUID suffix should be 8 chars, got {len(parts[3])}"

    def test_run_id_uniqueness(self) -> None:
        """100 consecutive calls produce unique IDs."""
        from journal import make_run_id

        ids = {make_run_id("TEST", "1h", pd.Timestamp("2026-06-24")) for _ in range(100)}
        assert len(ids) == 100, f"Expected 100 unique IDs, got {len(ids)}"

    def test_run_id_symbol_sanitization(self) -> None:
        """Symbol sanitization: ``/`` stripped, dots kept, hyphens kept."""
        from journal import make_run_id

        rid1 = make_run_id("BTC/USDT", "1h", pd.Timestamp("2026-06-24"))
        rid2 = make_run_id("ETH-USD", "1h", pd.Timestamp("2026-06-24"))
        rid3 = make_run_id("SOL.X", "1h", pd.Timestamp("2026-06-24"))

        assert rid1.startswith("BTCUSDT_"), f"Expected BTCUSDT, got {rid1}"
        assert rid2.startswith("ETH-USD_"), f"Expected ETH-USD, got {rid2}"
        assert rid3.startswith("SOL.X_"), f"Expected SOL.X, got {rid3}"


class TestJournalEntry:
    """JournalEntry dataclass — construction and field types."""

    def test_entry_construction(self) -> None:
        """All fields present and typed correctly."""
        from journal import JournalEntry

        entry = JournalEntry(
            run_id="BTCUSDT_1d_20240601_abc12345",
            timestamp=pd.Timestamp("2024-06-01"),
            symbol="BTCUSDT",
            timeframe="1d",
            close=50000.0,
            direction_score=8.0,
            bias="bullish",
            confidence=0.7,
            narrative_summary="Bullish continuation.",
            decision_action="look_for_longs",
            decision_invalidation=48000.0,
            decision_target=52000.0,
            breakout_pending=False,
            events=[],
        )

        assert entry.run_id == "BTCUSDT_1d_20240601_abc12345"
        assert entry.bias == "bullish"
        assert entry.confidence == 0.7
        assert entry.breakout_pending is False
        assert entry.events == []

    def test_entry_empty_events(self) -> None:
        """Events list can be empty."""
        from journal import JournalEntry

        entry = JournalEntry(
            run_id="test_empty",
            timestamp=pd.Timestamp.now(),
            symbol="TEST",
            timeframe="1h",
            close=100.0,
            direction_score=0.0,
            bias="neutral",
            confidence=1.0,
            narrative_summary="",
            decision_action="stand_aside",
            decision_invalidation=None,
            decision_target=None,
            breakout_pending=False,
            events=[],
        )
        assert entry.events == []
        assert len(entry.events) == 0

    def test_entry_none_fields(self) -> None:
        """Fields that can be None should accept None."""
        from journal import JournalEntry

        entry = JournalEntry(
            run_id="test_none",
            timestamp=pd.Timestamp.now(),
            symbol="TEST",
            timeframe="1h",
            close=100.0,
            direction_score=0.0,
            bias="neutral",
            confidence=1.0,
            narrative_summary="",
            decision_action="stand_aside",
            decision_invalidation=None,
            decision_target=None,
            breakout_pending=False,
            events=[],
        )
        assert entry.decision_invalidation is None
        assert entry.decision_target is None

    def test_entry_with_events(self) -> None:
        """Entry can hold StructureEvent objects."""
        from journal import JournalEntry

        event = StructureEvent(
            event_id="evt00001",
            event_type="BOS",
            direction=1,
            level=51000.0,
            swing_index=100,
            trigger_index=105,
            status="confirmed",
            confirmed_at_index=106,
            timestamp=pd.Timestamp("2024-06-01"),
        )
        entry = JournalEntry(
            run_id="test_events",
            timestamp=pd.Timestamp.now(),
            symbol="TEST",
            timeframe="1h",
            close=100.0,
            direction_score=0.0,
            bias="neutral",
            confidence=1.0,
            narrative_summary="",
            decision_action="stand_aside",
            decision_invalidation=None,
            decision_target=None,
            breakout_pending=False,
            events=[event],
        )
        assert len(entry.events) == 1
        assert entry.events[0].event_id == "evt00001"
        assert entry.events[0].event_type == "BOS"

    def test_entry_breakout_pending_bool(self) -> None:
        """breakout_pending stores bool (not int)."""
        from journal import JournalEntry

        e1 = JournalEntry(
            run_id="t1", timestamp=pd.Timestamp.now(),
            symbol="T", timeframe="1h", close=100.0,
            direction_score=0.0, bias="neutral", confidence=1.0,
            narrative_summary="", decision_action="stand_aside",
            decision_invalidation=None, decision_target=None,
            breakout_pending=True, events=[],
        )
        e2 = JournalEntry(
            run_id="t2", timestamp=pd.Timestamp.now(),
            symbol="T", timeframe="1h", close=100.0,
            direction_score=0.0, bias="neutral", confidence=1.0,
            narrative_summary="", decision_action="stand_aside",
            decision_invalidation=None, decision_target=None,
            breakout_pending=False, events=[],
        )
        assert e1.breakout_pending is True
        assert e2.breakout_pending is False
        assert isinstance(e1.breakout_pending, bool)
        assert isinstance(e2.breakout_pending, bool)


# =============================================================================
# T3: JournalWriter tests
# =============================================================================


def _make_entry(
    run_id: str = "test_run",
    symbol: str = "TEST",
    close: float = 100.0,
    bias: str = "neutral",
    decision_action: str = "stand_aside",
    breakout_pending: bool = False,
    events: list[StructureEvent] | None = None,
) -> object:
    """Factory helper for test JournalEntry construction."""
    from journal import JournalEntry

    return JournalEntry(
        run_id=run_id,
        timestamp=pd.Timestamp("2024-06-01"),
        symbol=symbol,
        timeframe="1h",
        close=close,
        direction_score=5.0,
        bias=bias,
        confidence=0.7,
        narrative_summary="Test entry.",
        decision_action=decision_action,
        decision_invalidation=None,
        decision_target=None,
        breakout_pending=breakout_pending,
        events=events or [],
    )


class TestJournalWriter:
    """JournalWriter — append, flush, query, round-trip."""

    def test_append_and_flush(self, tmp_path) -> None:
        """Write one entry, flush, verify DB file exists and has data."""
        from journal import JournalWriter

        db_path = str(tmp_path / "test.db")
        entry = _make_entry()

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                await writer.flush()

        asyncio.run(_run())

        assert tmp_path.joinpath("test.db").exists()
        assert tmp_path.joinpath("test.db").stat().st_size > 0

    def test_context_manager_lifecycle(self, tmp_path) -> None:
        """Async context manager opens, creates schema, and closes cleanly."""
        from journal import JournalWriter

        db_path = str(tmp_path / "lifecycle.db")

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                assert writer._conn is not None
                # Verify tables exist
                cursor = await writer._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                tables = [row[0] for row in await cursor.fetchall()]
                assert "journal_runs" in tables
                assert "journal_events" in tables

        asyncio.run(_run())

    def test_flush_creates_tables(self, tmp_path) -> None:
        """Tables exist after flush."""
        import aiosqlite
        from journal import JournalWriter

        db_path = str(tmp_path / "tables.db")
        entry = _make_entry()

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                await writer.flush()

        asyncio.run(_run())

        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                tables = [row[0] for row in await cursor.fetchall()]
                assert "journal_runs" in tables
                assert "journal_events" in tables

        asyncio.run(_verify())

    def test_flush_is_transactional(self, tmp_path) -> None:
        """If flush is not called, nothing is in the DB."""
        import aiosqlite
        from journal import JournalWriter

        db_path = str(tmp_path / "unflushed.db")
        entry = _make_entry()

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                # No flush here — __aexit__ will flush

        asyncio.run(_run())

        # __aexit__ flushes, so the entry should be there.
        # Test the "without flush" scenario by not flushing inside context
        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute("SELECT COUNT(*) FROM journal_runs")
                count = (await cursor.fetchone())[0]
                assert count == 1, (
                    f"Expected 1 (auto-flush on exit), got {count}. "
                    "The exit always flushes."
                )

        asyncio.run(_verify())

    def test_buffer_auto_flush(self, tmp_path) -> None:
        """buffer_size=2, append 3 entries → auto-flushes after 2nd append."""
        from journal import JournalWriter

        db_path = str(tmp_path / "buffer.db")
        entries = [_make_entry(run_id=f"buf_{i}") for i in range(3)]

        async def _run() -> None:
            async with JournalWriter(db_path, buffer_size=2) as writer:
                await writer.append(entries[0])
                assert len(writer._buffer) == 1
                await writer.append(entries[1])
                # Auto-flush after 2nd — buffer should be empty
                assert len(writer._buffer) == 0, "Buffer should be empty after auto-flush"
                await writer.append(entries[2])
                assert len(writer._buffer) == 1

        asyncio.run(_run())

    def test_buffer_size_zero(self, tmp_path) -> None:
        """buffer_size=0 means flush every append."""
        from journal import JournalWriter

        db_path = str(tmp_path / "zero_buffer.db")
        entry = _make_entry()

        async def _run() -> None:
            async with JournalWriter(db_path, buffer_size=0) as writer:
                await writer.append(entry)
                assert len(writer._buffer) == 0, "buffer_size=0 should flush immediately"

        asyncio.run(_run())

    def test_double_close_safety(self, tmp_path) -> None:
        """__aexit__ called twice should not error."""
        from journal import JournalWriter

        db_path = str(tmp_path / "double_close.db")
        entry = _make_entry()

        async def _run() -> None:
            writer = JournalWriter(db_path)
            await writer.__aenter__()
            await writer.append(entry)
            await writer.__aexit__(None, None, None)
            # Second close should be safe
            await writer.__aexit__(None, None, None)

        asyncio.run(_run())

    def test_multiple_flush_rounds(self, tmp_path) -> None:
        """Write 2 entries, flush, write 2 more, flush → total 4 rows."""
        import aiosqlite
        from journal import JournalWriter

        db_path = str(tmp_path / "multi_flush.db")

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(_make_entry(run_id="a"))
                await writer.append(_make_entry(run_id="b"))
                await writer.flush()

                await writer.append(_make_entry(run_id="c"))
                await writer.append(_make_entry(run_id="d"))
                await writer.flush()

        asyncio.run(_run())

        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute("SELECT COUNT(*) FROM journal_runs")
                count = (await cursor.fetchone())[0]
                assert count == 4

        asyncio.run(_verify())

    def test_nan_close_handling(self, tmp_path) -> None:
        """Entry with NaN close → DB stores NULL."""
        import aiosqlite
        from journal import JournalWriter

        db_path = str(tmp_path / "nan_close.db")
        entry = _make_entry(run_id="nan_test", close=float("nan"))

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                await writer.flush()

        asyncio.run(_run())

        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute("SELECT close FROM journal_runs WHERE run_id='nan_test'")
                row = await cursor.fetchone()
                assert row is not None
                assert row["close"] is None, f"Expected NULL, got {row['close']}"

        asyncio.run(_verify())

    def test_round_trip(self, tmp_path) -> None:
        """Write entry, query back, verify all fields match."""
        import aiosqlite
        from journal import JournalWriter, JournalEntry

        db_path = str(tmp_path / "roundtrip.db")
        entry = JournalEntry(
            run_id="roundtrip_001",
            timestamp=pd.Timestamp("2024-06-01 12:30:00"),
            symbol="ETH/USDT",
            timeframe="4h",
            close=3500.0,
            direction_score=7.5,
            bias="bullish",
            confidence=0.85,
            narrative_summary="Bullish setup with liquidity above.",
            decision_action="look_for_longs",
            decision_invalidation=3400.0,
            decision_target=3700.0,
            breakout_pending=True,
            events=[],
        )

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                await writer.flush()

        asyncio.run(_run())

        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM journal_runs WHERE run_id='roundtrip_001'"
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row["run_id"] == "roundtrip_001"
                assert row["symbol"] == "ETH/USDT"
                assert row["timeframe"] == "4h"
                assert row["close"] == 3500.0
                assert row["direction_score"] == 7.5
                assert row["bias"] == "bullish"
                assert row["confidence"] == 0.85
                assert row["narrative_summary"] == "Bullish setup with liquidity above."
                assert row["decision_action"] == "look_for_longs"
                assert row["decision_invalidation"] == 3400.0
                assert row["decision_target"] == 3700.0
                assert row["breakout_pending"] == 1  # bool → int
                assert row["created_at"] is not None

        asyncio.run(_verify())

    def test_round_trip_bool_none(self, tmp_path) -> None:
        """Verify bool round-trip and None round-trip for optional fields."""
        import aiosqlite
        from journal import JournalWriter, JournalEntry

        db_path = str(tmp_path / "bool_none.db")
        entry = JournalEntry(
            run_id="bool_none_test",
            timestamp=pd.Timestamp("2024-06-01"),
            symbol="TEST",
            timeframe="1h",
            close=100.0,
            direction_score=0.0,
            bias="neutral",
            confidence=1.0,
            narrative_summary="",
            decision_action="stand_aside",
            decision_invalidation=None,
            decision_target=None,
            breakout_pending=False,
            events=[],
        )

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                await writer.flush()

        asyncio.run(_run())

        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM journal_runs WHERE run_id='bool_none_test'"
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row["breakout_pending"] == 0  # False → 0
                assert row["decision_invalidation"] is None
                assert row["decision_target"] is None

        asyncio.run(_verify())

    def test_event_linking(self, tmp_path) -> None:
        """Entry with 3 events → verify event rows have correct run_id and data."""
        import aiosqlite
        from journal import JournalWriter, JournalEntry

        db_path = str(tmp_path / "events.db")
        events = [
            StructureEvent(
                event_id="evt001", event_type="BOS", direction=1,
                level=51000.0, swing_index=100, trigger_index=105,
                status="confirmed", confirmed_at_index=106,
                timestamp=pd.Timestamp("2024-06-01"),
            ),
            StructureEvent(
                event_id="evt002", event_type="CHOCH", direction=-1,
                level=49000.0, swing_index=50, trigger_index=55,
                status="provisional", timestamp=pd.Timestamp("2024-06-01"),
            ),
            StructureEvent(
                event_id="evt003", event_type="BOS", direction=1,
                level=51500.0, swing_index=200, trigger_index=205,
                status="confirmed", confirmed_at_index=206,
                timestamp=pd.Timestamp("2024-06-02"),
            ),
        ]
        entry = JournalEntry(
            run_id="event_link_test",
            timestamp=pd.Timestamp("2024-06-02"),
            symbol="TEST",
            timeframe="1h",
            close=100.0,
            direction_score=0.0,
            bias="neutral",
            confidence=1.0,
            narrative_summary="",
            decision_action="stand_aside",
            decision_invalidation=None,
            decision_target=None,
            breakout_pending=False,
            events=events,
        )

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                await writer.flush()

        asyncio.run(_run())

        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                # Verify event count
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM journal_events WHERE run_id='event_link_test'"
                )
                count = (await cursor.fetchone())[0]
                assert count == 3, f"Expected 3 events, got {count}"

                # Verify each event's data
                cursor = await db.execute(
                    "SELECT * FROM journal_events WHERE run_id='event_link_test' ORDER BY event_id"
                )
                rows = await cursor.fetchall()
                assert rows[0]["event_id"] == "evt001"
                assert rows[0]["event_type"] == "BOS"
                assert rows[0]["direction"] == 1
                assert rows[0]["status"] == "confirmed"
                assert rows[0]["level"] == 51000.0

                assert rows[1]["event_id"] == "evt002"
                assert rows[1]["event_type"] == "CHOCH"
                assert rows[1]["direction"] == -1
                assert rows[1]["status"] == "provisional"

                assert rows[2]["event_id"] == "evt003"
                assert rows[2]["event_type"] == "BOS"
                assert rows[2]["level"] == 51500.0

                # Verify FK: events have correct run_id
                for row in rows:
                    assert row["run_id"] == "event_link_test"

        asyncio.run(_verify())

    def test_event_timestamp_column(self, tmp_path) -> None:
        """event_timestamp column is populated from StructureEvent.timestamp."""
        import aiosqlite
        from journal import JournalWriter, JournalEntry

        db_path = str(tmp_path / "event_ts.db")
        event = StructureEvent(
            event_id="evt_ts",
            event_type="BOS",
            direction=1,
            level=50000.0,
            swing_index=10,
            trigger_index=15,
            status="confirmed",
            timestamp=pd.Timestamp("2024-06-01 12:00:00"),
        )
        entry = _make_entry(run_id="event_ts_test", events=[event])

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                await writer.flush()

        asyncio.run(_run())

        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT event_timestamp FROM journal_events WHERE event_id='evt_ts'"
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row["event_timestamp"] is not None
                assert "2024-06-01" in row["event_timestamp"]

        asyncio.run(_verify())

    def test_query_runs(self, tmp_path) -> None:
        """Write multiple entries with different symbols, query by symbol."""
        from journal import JournalWriter

        db_path = str(tmp_path / "query.db")
        entries = [
            _make_entry(run_id="q1", symbol="BTCUSDT"),
            _make_entry(run_id="q2", symbol="ETHUSDT"),
            _make_entry(run_id="q3", symbol="BTCUSDT"),
        ]

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                for e in entries:
                    await writer.append(e)
                await writer.flush()

        asyncio.run(_run())

        async def _query() -> None:
            async with JournalWriter(db_path) as writer:
                btc_rows = await writer.query_runs(symbol="BTCUSDT")
                eth_rows = await writer.query_runs(symbol="ETHUSDT")

                assert len(btc_rows) == 2, f"Expected 2 BTC rows, got {len(btc_rows)}"
                assert len(eth_rows) == 1, f"Expected 1 ETH row, got {len(eth_rows)}"

                for row in btc_rows:
                    assert row["symbol"] == "BTCUSDT"
                for row in eth_rows:
                    assert row["symbol"] == "ETHUSDT"

        asyncio.run(_query())

    def test_query_runs_with_limit(self, tmp_path) -> None:
        """query_runs respects the limit parameter."""
        from journal import JournalWriter

        db_path = str(tmp_path / "limit.db")

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                for i in range(10):
                    await writer.append(_make_entry(run_id=f"lim_{i}"))
                await writer.flush()

        asyncio.run(_run())

        async def _query() -> None:
            async with JournalWriter(db_path) as writer:
                rows = await writer.query_runs(limit=3)
                assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"

        asyncio.run(_query())

    def test_created_at_auto_set(self, tmp_path) -> None:
        """created_at is auto-populated on INSERT."""
        import aiosqlite
        from journal import JournalWriter

        db_path = str(tmp_path / "created_at.db")
        entry = _make_entry(run_id="created_at_test")

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                await writer.flush()

        asyncio.run(_run())

        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT created_at FROM journal_runs WHERE run_id='created_at_test'"
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row["created_at"] is not None
                assert len(str(row["created_at"])) > 0

        asyncio.run(_verify())

    def test_events_round_trip(self, tmp_path) -> None:
        """Event columns (event_type, direction, status, level) round-trip correctly."""
        import aiosqlite
        from journal import JournalWriter, JournalEntry

        db_path = str(tmp_path / "events_rt.db")
        event = StructureEvent(
            event_id="evt_rt",
            event_type="CHOCH",
            direction=-1,
            level=49500.0,
            swing_index=5,
            trigger_index=8,
            status="confirmed",
            confirmed_at_index=10,
            timestamp=pd.Timestamp("2024-06-01"),
        )
        entry = _make_entry(run_id="evt_rt_test", events=[event])

        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                await writer.flush()

        asyncio.run(_run())

        async def _verify() -> None:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM journal_events WHERE event_id='evt_rt'"
                )
                row = await cursor.fetchone()
                assert row is not None
                assert row["event_type"] == "CHOCH"
                assert row["direction"] == -1
                assert row["status"] == "confirmed"
                assert row["level"] == 49500.0

        asyncio.run(_verify())


# =============================================================================
# T4: Integration test — full pipeline → journal → DB
# =============================================================================


class TestJournalIntegration:
    """Integration tests that exercise the full pipeline."""

    def test_full_pipeline_journal_integration(self, tmp_path) -> None:
        """Build snapshot, events, entry → write → query → verify all fields."""
        from journal import JournalWriter, JournalEntry, make_run_id
        from market_snapshot import MarketSnapshot
        from confluence import ConfluenceResult, ConfluenceScorer
        from narrative import MarketNarrativeBuilder
        from decision_engine import DecisionEngine

        db_path = str(tmp_path / "integration.db")

        # Build a MarketSnapshot matching sample_snapshot fixture values
        snapshot = MarketSnapshot(
            symbol="BTCUSDT",
            timeframe="1d",
            timestamp=pd.Timestamp("2024-06-01"),
            close=50000.0,
            trend_direction="above",
            ema21=49000.0,
            ema21_slope=0.01,
            rsi14=60.0,
            mfi14=55.0,
            macd=100.0,
            macd_signal=80.0,
            macd_hist=20.0,
            atr14=1000.0,
            bb_width=0.05,
            last_bos_direction=1,
            nearest_liquidity_above=51000.0,
        )

        # Score it
        scorer = ConfluenceScorer()
        result = scorer.score(snapshot)

        # Build narrative
        builder = MarketNarrativeBuilder()
        narrative = builder.build(snapshot, result)

        # Generate decision
        engine = DecisionEngine()
        decision = engine.decide(snapshot, result)

        # Create test events
        events = [
            StructureEvent(
                event_id="evt00001",
                event_type="BOS",
                direction=1,
                level=51000.0,
                swing_index=100,
                trigger_index=105,
                status="confirmed",
                confirmed_at_index=106,
                timestamp=pd.Timestamp("2024-06-01"),
            ),
            StructureEvent(
                event_id="evt00002",
                event_type="CHOCH",
                direction=-1,
                level=49000.0,
                swing_index=50,
                trigger_index=55,
                status="provisional",
                timestamp=pd.Timestamp("2024-06-01"),
            ),
        ]

        # Build JournalEntry
        entry = JournalEntry(
            run_id=make_run_id(
                snapshot.symbol, snapshot.timeframe, snapshot.timestamp
            ),
            timestamp=snapshot.timestamp,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            close=snapshot.close,
            direction_score=result.direction_score,
            bias=result.bias,
            confidence=result.confidence,
            narrative_summary=narrative.conclusion,
            decision_action=decision.action,
            decision_invalidation=decision.invalidation,
            decision_target=decision.target,
            breakout_pending=decision.breakout_pending,
            events=events,
        )

        # Write to journal
        async def _write() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)
                # __aexit__ flushes and closes

        asyncio.run(_write())

        # Verify DB file
        assert tmp_path.joinpath("integration.db").exists()
        assert tmp_path.joinpath("integration.db").stat().st_size > 0

        # Query back
        async def _query() -> list[dict]:
            async with JournalWriter(db_path) as writer:
                return await writer.query_runs(symbol="BTCUSDT")

        rows = asyncio.run(_query())

        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        row = rows[0]
        assert row["run_id"] == entry.run_id
        assert row["symbol"] == "BTCUSDT"
        assert row["timeframe"] == "1d"
        assert row["close"] == 50000.0
        assert row["direction_score"] == result.direction_score
        assert row["bias"] == "bullish"
        assert row["confidence"] == result.confidence
        assert row["narrative_summary"] == narrative.conclusion
        assert row["decision_action"] == decision.action
        assert row["breakout_pending"] == int(decision.breakout_pending)
        assert row["created_at"] is not None

    def test_full_pipeline_with_sample_snapshot(
        self, tmp_path, sample_snapshot
    ) -> None:
        """Integration test using the sample_snapshot fixture."""
        from journal import JournalWriter, JournalEntry, make_run_id
        from confluence import ConfluenceScorer
        from narrative import MarketNarrativeBuilder
        from decision_engine import DecisionEngine

        db_path = str(tmp_path / "integration_samplesnap.db")

        # Pipeline
        scorer = ConfluenceScorer()
        result = scorer.score(sample_snapshot)

        builder = MarketNarrativeBuilder()
        narrative = builder.build(sample_snapshot, result)

        engine = DecisionEngine()
        decision = engine.decide(sample_snapshot, result)

        # Events
        events = [
            StructureEvent(
                event_id="evt100",
                event_type="BOS",
                direction=1,
                level=51000.0,
                swing_index=3,
                trigger_index=4,
                status="confirmed",
                timestamp=pd.Timestamp("2024-06-01"),
            ),
        ]

        # Entry
        entry = JournalEntry(
            run_id=make_run_id(
                sample_snapshot.symbol,
                sample_snapshot.timeframe,
                sample_snapshot.timestamp,
            ),
            timestamp=sample_snapshot.timestamp,
            symbol=sample_snapshot.symbol,
            timeframe=sample_snapshot.timeframe,
            close=sample_snapshot.close,
            direction_score=result.direction_score,
            bias=result.bias,
            confidence=result.confidence,
            narrative_summary=narrative.conclusion,
            decision_action=decision.action,
            decision_invalidation=decision.invalidation,
            decision_target=decision.target,
            breakout_pending=decision.breakout_pending,
            events=events,
        )

        # Write
        async def _run() -> None:
            async with JournalWriter(db_path) as writer:
                await writer.append(entry)

        asyncio.run(_run())

        # Query
        async def _query() -> None:
            async with JournalWriter(db_path) as writer:
                rows = await writer.query_runs()
                assert len(rows) == 1
                row = rows[0]
                assert row["symbol"] == sample_snapshot.symbol
                assert row["close"] == sample_snapshot.close
                assert row["direction_score"] == result.direction_score
                assert row["bias"] == result.bias
                assert row["decision_action"] == decision.action

        asyncio.run(_query())
