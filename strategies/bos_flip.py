"""
BOS Flip Strategy — V2 Streaming + V1 Batch Fallback

Enters a position when a Break of Structure (BOS) is detected.

V2 Path (preferred): Uses streaming StructureEvent objects (confirmed
    BOS only). Called when structure_events list is non-None.
V1 Fallback: Uses batch row["BOS"] from per_candle_report.

- Bullish BOS (event.direction == 1): Close short → Enter long
- Bearish BOS (event.direction == -1): Close long → Enter short

This is intentionally simple. No filters, no confirmation, no risk management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from trade_simulator import TradeSimulator


class BOSFlipStrategy:
    """Strategy that flips position direction on BOS signals.

    V2: Uses confirmed streaming StructureEvent objects.
    V1: Falls back to batch row["BOS"] for backward compatibility.

    Usage:
        from strategies.bos_flip import BOSFlipStrategy
        strategy = BOSFlipStrategy()
        harness = BacktestHarness(config, strategy_callback=strategy)
    """

    def update(
        self,
        candle_index: int,
        row: pd.Series,
        engine_result: dict[str, float],
        simulator: Optional["TradeSimulator"] = None,
        structure_events: Optional[list] = None,
    ) -> None:
        """Called every bar. Checks BOS signal and acts.

        V2 Path: If structure_events is provided and non-empty, processes
            confirmed BOS events. Returns early (skips V1 fallback).
        V1 Path: Falls back to row["BOS"] batch column.

        Args:
            candle_index: Bar index (0-based).
            row: Full per-candle report row (has "BOS" column among others).
            engine_result: Engine output (HighLow, Level, PivotIndex).
            simulator: TradeSimulator for trade execution.
            structure_events: Optional list of StructureEvent objects for
                this bar (confirmed/cancelled status changes).
        """
        # V2 path: streaming confirmed BOS events
        if structure_events is not None and len(structure_events) > 0:
            if simulator is None:
                return  # No simulator to trade with
            for event in structure_events:
                if event.event_type == "BOS" and event.status == "confirmed":
                    close_price = float(row["Close"])
                    timestamp = row.name
                    if event.direction == 1:  # Bullish BOS
                        if simulator.is_short:
                            simulator.close(
                                candle_index, timestamp, close_price
                            )
                        if simulator.is_flat:
                            simulator.enter_long(
                                candle_index, timestamp, close_price
                            )
                    elif event.direction == -1:  # Bearish BOS
                        if simulator.is_long:
                            simulator.close(
                                candle_index, timestamp, close_price
                            )
                        if simulator.is_flat:
                            simulator.enter_short(
                                candle_index, timestamp, close_price
                            )
            return  # V2 path consumed — skip V1 fallback

        # V1 fallback: batch BOS (only when no streaming events or no simulator)
        if simulator is None:
            return  # No simulator to trade with
        bos = row.get("BOS", np.nan)
        if bos is None or (isinstance(bos, float) and np.isnan(bos)) or bos == 0:
            return  # No BOS signal — nothing to do

        close_price = float(row["Close"])
        timestamp = row.name  # The index is the timestamp

        if bos == 1:
            # Bullish BOS — flip to long
            if simulator.is_short:
                simulator.close(candle_index, timestamp, close_price)
            if simulator.is_flat:
                simulator.enter_long(candle_index, timestamp, close_price)

        elif bos == -1:
            # Bearish BOS — flip to short
            if simulator.is_long:
                simulator.close(candle_index, timestamp, close_price)
            if simulator.is_flat:
                simulator.enter_short(candle_index, timestamp, close_price)
