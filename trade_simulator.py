"""
Trade Simulator — Position tracking and trade logging for backtest strategies.

Tracks 0-or-1 positions (single position). No portfolio, no sizing, no slippage.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal, Optional

import numpy as np
import pandas as pd


@dataclass
class Trade:
    """A single completed trade.

    Attributes:
        side: "LONG" or "SHORT"
        entry_index: Bar index (0-based) where the trade was entered.
        entry_time: Timestamp of the entry bar.
        entry_price: Price at entry (close of signal bar).
        exit_index: Bar index where the trade was closed (None until closed).
        exit_time: Timestamp of the exit bar (None until closed).
        exit_price: Price at exit (close of signal bar).
        pnl: Realized PnL (None until closed). Positive for profit.
    """
    side: Literal["LONG", "SHORT"]
    entry_index: int
    entry_time: pd.Timestamp
    entry_price: float
    exit_index: Optional[int] = None
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None


class TradeSimulator:
    """Position manager for backtest trade simulation.

    Manages a single position (0 or 1). Tracks all closed trades.
    Provides convenience properties for strategy logic.

    Usage:
        sim = TradeSimulator()
        sim.enter_long(10, ts, 1.05)
        sim.close(25, ts, 1.10)
        assert sim.is_flat
        assert len(sim.closed_trades) == 1
        df = sim.to_dataframe()
    """

    def __init__(self) -> None:
        self._position: Optional[Trade] = None
        self._closed_trades: list[Trade] = []

    @property
    def position(self) -> Optional[Trade]:
        """Currently open position, or None if flat."""
        return self._position

    @property
    def closed_trades(self) -> list[Trade]:
        """List of all completed trades (copy)."""
        return list(self._closed_trades)

    @property
    def is_flat(self) -> bool:
        return self._position is None

    @property
    def is_long(self) -> bool:
        return self._position is not None and self._position.side == "LONG"

    @property
    def is_short(self) -> bool:
        return self._position is not None and self._position.side == "SHORT"

    def _close_existing(self, index: int, time: pd.Timestamp,
                        price: float) -> None:
        """Close any open position before opening a new one."""
        if self._position is not None:
            self.close(index, time, price)

    def enter_long(self, index: int, time: pd.Timestamp,
                   price: float) -> None:
        """Enter a long position. Closes any existing position first."""
        self._close_existing(index, time, price)
        self._position = Trade(
            side="LONG",
            entry_index=index,
            entry_time=time,
            entry_price=price,
        )

    def enter_short(self, index: int, time: pd.Timestamp,
                    price: float) -> None:
        """Enter a short position. Closes any existing position first."""
        self._close_existing(index, time, price)
        self._position = Trade(
            side="SHORT",
            entry_index=index,
            entry_time=time,
            entry_price=price,
        )

    def close(self, index: int, time: pd.Timestamp,
              price: float) -> None:
        """Close the current position (if any) and record the trade."""
        if self._position is None:
            return  # No-op: nothing to close

        trade = self._position
        trade.exit_index = index
        trade.exit_time = time
        trade.exit_price = price

        if trade.side == "LONG":
            trade.pnl = price - trade.entry_price
        else:
            trade.pnl = trade.entry_price - price

        self._closed_trades.append(trade)
        self._position = None

    def to_dataframe(self) -> pd.DataFrame:
        """Convert all closed trades to a DataFrame.

        Returns:
            DataFrame with columns: side, entry_index, entry_time, entry_price,
            exit_index, exit_time, exit_price, pnl.
            Empty DataFrame (0 rows) if no trades.
        """
        if not self._closed_trades:
            return pd.DataFrame(columns=[
                "side", "entry_index", "entry_time", "entry_price",
                "exit_index", "exit_time", "exit_price", "pnl",
            ])
        return pd.DataFrame([asdict(t) for t in self._closed_trades])

    def equity_curve(self) -> pd.Series:
        """Cumulative realized PnL after each trade close.

        Returns:
            Series indexed by exit_index (int). Cumulative sum of PnL.
            Empty Series if no trades.
        """
        if not self._closed_trades:
            return pd.Series(dtype=np.float64, name="equity_curve")

        pnls = [t.pnl for t in self._closed_trades if t.pnl is not None]
        indices = [t.exit_index for t in self._closed_trades
                   if t.exit_index is not None]
        return pd.Series(
            np.cumsum(pnls),
            index=indices,
            name="equity_curve",
            dtype=np.float64,
        )
