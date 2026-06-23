"""
Streaming Structure Engine — Two-Stage BOS/CHOCH Detection

Two-stage causal detection:
  Stage 1 (provisional): On each new confirmed swing, check if the last 4 swings
    form a BOS/CHOCH pattern. If yes, emit a provisional StructureEvent.
  Stage 2 (confirm/cancel): On every bar, check all provisional events:
    - Price breaks the level → confirm
    - Bars since trigger >= confirmation_window → cancel

Level ordering and swing indexing exactly match batch smc.bos_choch() semantics.

Import path: ``from smartmoneyconcepts.structures import StructureEngine``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


@dataclass
class SwingConfirmed:
    """Event emitted by _SwingEngine when a swing is confirmed."""

    index: int
    direction: int  # 1 = swing high, -1 = swing low
    level: float
    timestamp: pd.Timestamp
    pivot_index: int


@dataclass
class StructureEvent:
    """Event emitted by StructureEngine when BOS/CHOCH is detected.

    Status flow: provisional → confirmed (on level break)
                           OR provisional → cancelled (on window expiry).
    """

    event_type: str  # "BOS" or "CHOCH"
    direction: int  # 1 = bullish, -1 = bearish
    level: float
    swing_index: int  # Index of S1 (level-defining swing, matches batch stamp index)
    trigger_index: int  # Index of S3 (4th swing, when pattern was detected)
    timestamp: pd.Timestamp  # Timestamp of the trigger swing
    status: Literal["provisional", "confirmed", "cancelled"] = "provisional"
    confirmed_at_index: int | None = None  # Bar index when confirmed


class StructureEngine:
    """Two-stage causal streaming BOS/CHOCH detector.

    Stage 1 (update): On each new confirmed swing, check if the last 4 swings
        form a BOS/CHOCH pattern. If yes, emit a provisional StructureEvent.

    Stage 2 (check_confirmations): On each bar (every candle), check all
        provisional events:
        - If price has broken the level → confirm
        - If bars_since >= confirmation_window → cancel

    Mirrors semantics of batch bos_choch() BrokenIndex, but causally.

    Usage:
        engine = StructureEngine(confirmation_window=10)
        new_events = engine.update(swing)
        status_changes = engine.check_confirmations(index, high, low)
    """

    def __init__(self, confirmation_window: int = 10) -> None:
        self._confirmation_window = confirmation_window
        self._swings: list[SwingConfirmed] = []
        self._provisional_events: list[StructureEvent] = []
        self._all_events: list[StructureEvent] = []
        self._emitted_keys: set[tuple] = set()

    # ------------------------------------------------------------------
    # Stage 1: Pattern detection
    # ------------------------------------------------------------------

    def update(self, swing: SwingConfirmed) -> list[StructureEvent]:
        """Stage 1: Process a confirmed swing.

        Checks the last 4 swings for pattern completion. If a pattern is
        found that hasn't been emitted before, creates a provisional
        StructureEvent.

        Args:
            swing: The newly confirmed swing.

        Returns:
            List of newly emitted provisional StructureEvent objects
            (empty if no pattern detected or duplicate).
        """
        self._swings.append(swing)
        new_events: list[StructureEvent] = []

        if len(self._swings) >= 4:
            last_4 = self._swings[-4:]
            directions = [s.direction for s in last_4]
            levels = [s.level for s in last_4]

            # S1 is the 2nd swing in the 4-window (= _swings[-3])
            s1_index = last_4[1].index
            s1_level = levels[1]

            # Bullish BOS: pattern [-1, 1, -1, 1] with L0 < L2 < L1 < L3
            if (directions == [-1, 1, -1, 1]
                    and levels[0] < levels[2] < levels[1] < levels[3]):
                key = ("BOS", 1, s1_index)
                if key not in self._emitted_keys:
                    self._emitted_keys.add(key)
                    event = StructureEvent(
                        event_type="BOS",
                        direction=1,
                        level=s1_level,
                        swing_index=s1_index,
                        trigger_index=swing.index,
                        timestamp=swing.timestamp,
                        status="provisional",
                    )
                    self._provisional_events.append(event)
                    self._all_events.append(event)
                    new_events.append(event)

            # Bearish BOS: pattern [1, -1, 1, -1] with L0 > L2 > L1 > L3
            if (directions == [1, -1, 1, -1]
                    and levels[0] > levels[2] > levels[1] > levels[3]):
                key = ("BOS", -1, s1_index)
                if key not in self._emitted_keys:
                    self._emitted_keys.add(key)
                    event = StructureEvent(
                        event_type="BOS",
                        direction=-1,
                        level=s1_level,
                        swing_index=s1_index,
                        trigger_index=swing.index,
                        timestamp=swing.timestamp,
                        status="provisional",
                    )
                    self._provisional_events.append(event)
                    self._all_events.append(event)
                    new_events.append(event)

            # Bullish CHOCH: pattern [-1, 1, -1, 1] with L3 > L1 > L0 > L2
            if (directions == [-1, 1, -1, 1]
                    and levels[3] > levels[1] > levels[0] > levels[2]):
                key = ("CHOCH", 1, s1_index)
                if key not in self._emitted_keys:
                    self._emitted_keys.add(key)
                    event = StructureEvent(
                        event_type="CHOCH",
                        direction=1,
                        level=s1_level,
                        swing_index=s1_index,
                        trigger_index=swing.index,
                        timestamp=swing.timestamp,
                        status="provisional",
                    )
                    self._provisional_events.append(event)
                    self._all_events.append(event)
                    new_events.append(event)

            # Bearish CHOCH: pattern [1, -1, 1, -1] with L3 < L1 < L0 < L2
            if (directions == [1, -1, 1, -1]
                    and levels[3] < levels[1] < levels[0] < levels[2]):
                key = ("CHOCH", -1, s1_index)
                if key not in self._emitted_keys:
                    self._emitted_keys.add(key)
                    event = StructureEvent(
                        event_type="CHOCH",
                        direction=-1,
                        level=s1_level,
                        swing_index=s1_index,
                        trigger_index=swing.index,
                        timestamp=swing.timestamp,
                        status="provisional",
                    )
                    self._provisional_events.append(event)
                    self._all_events.append(event)
                    new_events.append(event)

        return new_events

    # ------------------------------------------------------------------
    # Stage 2: Confirmation / cancellation
    # ------------------------------------------------------------------

    def check_confirmations(
        self, index: int, high: float, low: float
    ) -> list[StructureEvent]:
        """Stage 2: Check all provisional events for break or expiry.

        Called EVERY CANDLE (not just on swing confirmations).

        Args:
            index: Current bar index.
            high: Current bar's high price.
            low: Current bar's low price.

        Returns:
            List of StructureEvent objects that changed status this bar
            (confirmed or cancelled).
        """
        status_changes: list[StructureEvent] = []
        still_provisional: list[StructureEvent] = []

        for event in self._provisional_events:
            bars_since = index - event.trigger_index

            # Check for level break
            broke = self._check_break(event, high, low)
            if broke:
                event.status = "confirmed"
                event.confirmed_at_index = index
                status_changes.append(event)
                continue

            # Check for window expiry
            if bars_since >= self._confirmation_window:
                event.status = "cancelled"
                status_changes.append(event)
                continue

            still_provisional.append(event)

        self._provisional_events = still_provisional
        return status_changes

    @staticmethod
    def _check_break(event: StructureEvent, high: float, low: float) -> bool:
        """Check if the current bar's price breaks the event's level.

        Bullish (direction=1): break when high > level
        Bearish (direction=-1): break when low < level
        """
        if event.direction == 1:
            return high > event.level
        else:  # direction == -1
            return low < event.level

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def events(self) -> list[StructureEvent]:
        """All events ever emitted (provisional, confirmed, cancelled)."""
        return list(self._all_events)

    @property
    def swings(self) -> list[SwingConfirmed]:
        """All swings received."""
        return list(self._swings)

    @property
    def confirmed_events(self) -> list[StructureEvent]:
        """Only confirmed events."""
        return [e for e in self._all_events if e.status == "confirmed"]

    @property
    def provisional_events(self) -> list[StructureEvent]:
        """Currently active (not yet confirmed or cancelled) events."""
        return list(self._provisional_events)
