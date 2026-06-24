"""
live_smc_buffer.py — Streaming SMC accumulator.

Wraps _SwingEngine + StructureEngine. Runs batch OB/liquidity/retracements
on swing confirmation (not every candle). Exposes rolling 26-column report
for SnapshotBuilder.
"""

import numpy as np
import pandas as pd
from smartmoneyconcepts.smc import smc
from smartmoneyconcepts.structures import StructureEngine, StructureEvent, SwingConfirmed


class LiveSmcBuffer:
    """Streaming SMC accumulator maintaining a rolling 26-column report.

    Owns _SwingEngine and StructureEngine. Calls batch smc.ob(),
    smc.liquidity(), smc.retracements() only when a new swing is confirmed.

    Args:
        swing_length: Lookback bars for candidate discovery.
        confirmation_bars: Min bars before swing confirmation.
        atr_multiplier: ATR retracement threshold multiplier.
        atr_period: Period for internal ATR calculation.
        bos_confirmation_window: Bars before provisional BOS is cancelled.
        report_window: Max rows in rolling report.
    """

    REPORT_COLUMNS = [
        "Timestamp", "Open", "High", "Low", "Close", "Volume",
        "SwingHighLow", "SwingLevel", "SwingPivotIndex",
        "BOS", "CHOCH", "BOSLevel", "BrokenIndex",
        "OB", "OBTop", "OBBottom", "OBVolume", "OBMitigatedIndex", "OBPct",
        "Liquidity", "LiqLevel", "LiqEnd", "LiqSwept",
        "RetraceDirection", "CurrentRetracement%", "DeepestRetracement%",
    ]

    def __init__(
        self,
        swing_length: int = 5,
        confirmation_bars: int = 2,
        atr_multiplier: float = 1.5,
        atr_period: int = 7,
        bos_confirmation_window: int = 10,
        report_window: int = 200,
    ):
        self._swing_engine = smc._SwingEngine(
            swing_length, confirmation_bars, atr_multiplier, atr_period
        )
        self._structure_engine = StructureEngine(bos_confirmation_window)
        self._swing_rows: list[dict] = []
        self._ohlcv_buffer: list[dict] = []
        self._report: pd.DataFrame = pd.DataFrame(columns=self.REPORT_COLUMNS)
        self._report_window = report_window
        self._candle_index = 0
        self._batch_results: dict[str, pd.DataFrame] = {}

    def update(self, row: pd.Series) -> dict:
        """Process one candle. Returns engine result dict.

        Args:
            row: pd.Series with lowercase OHLCV columns (open, high, low, close, volume).

        Returns:
            dict with keys "HighLow" (1/-1/NaN) and "Level" (float/NaN).
        """
        idx = self._candle_index
        self._candle_index += 1

        high = float(row.get("high", np.nan))
        low = float(row.get("low", np.nan))
        close = float(row.get("close", np.nan))

        # Store OHLCV for batch recompute
        self._ohlcv_buffer.append({
            "open": float(row.get("open", np.nan)),
            "high": high,
            "low": low,
            "close": close,
            "volume": float(row.get("volume", 0)),
        })

        # Step 1: Update swing engine
        engine_result = self._swing_engine.update(idx, row)
        self._swing_rows.append(dict(engine_result))

        # Step 2: Feed swing confirmation to structure engine
        new_events: list[StructureEvent] = []
        if not np.isnan(engine_result.get("HighLow", np.nan)):
            swing = SwingConfirmed(
                index=idx,
                direction=int(engine_result["HighLow"]),
                level=engine_result["Level"],
                pivot_index=int(engine_result.get("PivotIndex", idx)),
                timestamp=row.name if hasattr(row, "name") else None,
            )
            new_events = self._structure_engine.update(swing)
            self._recompute_downstream()

        # Step 3: Check structure confirmations every bar
        status_changes = self._structure_engine.check_confirmations(idx, high, low)
        new_events.extend(e for e in status_changes if e not in new_events)

        # Step 4: Update rolling report
        self._update_report(idx, row, engine_result, new_events)

        return engine_result

    def _recompute_downstream(self) -> None:
        """Re-run OB, liquidity, retracements on accumulated swing data."""
        min_rows = self._swing_engine._swing_length + self._swing_engine._confirmation_bars
        if len(self._swing_rows) < max(min_rows, self._swing_engine._atr_period):
            return  # Not enough data for meaningful batch output

        swings_df = pd.DataFrame(self._swing_rows)
        ohlc_df = pd.DataFrame(self._ohlcv_buffer)

        try:
            ob_result = smc.ob(ohlc_df, swings_df, close_mitigation=False)
            self._batch_results["ob"] = ob_result
        except Exception:
            pass

        try:
            liq_result = smc.liquidity(ohlc_df, swings_df, range_percent=0.01)
            self._batch_results["liquidity"] = liq_result
        except Exception:
            pass

        try:
            ret_result = smc.retracements(ohlc_df, swings_df)
            self._batch_results["retracements"] = ret_result
        except Exception:
            pass

    def _update_report(
        self,
        idx: int,
        row: pd.Series,
        engine_result: dict,
        events: list[StructureEvent],
    ) -> None:
        """Append one row to the rolling report."""
        # Collect BOS/CHOCH events at this bar
        bos_val = np.nan
        choch_val = np.nan
        bos_level = np.nan
        broken_idx = np.nan
        for ev in events:
            if ev.event_type == "BOS" and ev.status == "confirmed":
                bos_val = float(ev.direction)
                bos_level = float(ev.level)
                broken_idx = float(ev.confirmed_at_index) if ev.confirmed_at_index is not None else np.nan
            elif ev.event_type == "CHOCH" and ev.status == "confirmed":
                choch_val = float(ev.direction)

        # Get latest batch results at current row
        ob_row = {}
        liq_row = {}
        ret_row = {}
        if "ob" in self._batch_results:
            ob_df = self._batch_results["ob"]
            if idx < len(ob_df):
                ob_row = ob_df.iloc[idx]
        if "liquidity" in self._batch_results:
            liq_df = self._batch_results["liquidity"]
            if idx < len(liq_df):
                liq_row = liq_df.iloc[idx]
        if "retracements" in self._batch_results:
            ret_df = self._batch_results["retracements"]
            if idx < len(ret_df):
                ret_row = ret_df.iloc[idx]

        report_row = {
            "Timestamp": row.name if hasattr(row, "name") else idx,
            "Open": row.get("open", np.nan),
            "High": row.get("high", np.nan),
            "Low": row.get("low", np.nan),
            "Close": row.get("close", np.nan),
            "Volume": row.get("volume", 0),
            "SwingHighLow": engine_result.get("HighLow", np.nan),
            "SwingLevel": engine_result.get("Level", np.nan),
            "SwingPivotIndex": engine_result.get("PivotIndex", np.nan),
            "BOS": bos_val,
            "CHOCH": choch_val,
            "BOSLevel": bos_level,
            "BrokenIndex": broken_idx,
            "OB": ob_row.get("OB", np.nan) if not isinstance(ob_row, dict) else np.nan,
            "OBTop": ob_row.get("Top", np.nan) if not isinstance(ob_row, dict) else np.nan,
            "OBBottom": ob_row.get("Bottom", np.nan) if not isinstance(ob_row, dict) else np.nan,
            "OBVolume": ob_row.get("OBVolume", np.nan) if not isinstance(ob_row, dict) else np.nan,
            "OBMitigatedIndex": ob_row.get("MitigatedIndex", np.nan) if not isinstance(ob_row, dict) else np.nan,
            "OBPct": ob_row.get("Percentage", np.nan) if not isinstance(ob_row, dict) else np.nan,
            "Liquidity": liq_row.get("Liquidity", np.nan) if not isinstance(liq_row, dict) else np.nan,
            "LiqLevel": liq_row.get("Level", np.nan) if not isinstance(liq_row, dict) else np.nan,
            "LiqEnd": liq_row.get("End", np.nan) if not isinstance(liq_row, dict) else np.nan,
            "LiqSwept": liq_row.get("Swept", np.nan) if not isinstance(liq_row, dict) else np.nan,
            "RetraceDirection": ret_row.get("Direction", np.nan) if not isinstance(ret_row, dict) else np.nan,
            "CurrentRetracement%": ret_row.get("CurrentRetracement%", np.nan) if not isinstance(ret_row, dict) else np.nan,
            "DeepestRetracement%": ret_row.get("DeepestRetracement%", np.nan) if not isinstance(ret_row, dict) else np.nan,
        }

        row_df = pd.DataFrame([report_row])
        self._report = pd.concat([self._report, row_df], ignore_index=True)

        # Trim to window
        if len(self._report) > self._report_window:
            self._report = self._report.tail(self._report_window).reset_index(drop=True)

    def get_smc_report(self) -> pd.DataFrame:
        """Return the rolling SMC report (up to report_window rows)."""
        return self._report.tail(self._report_window).copy()

    @property
    def events(self) -> list[StructureEvent]:
        """All structure events emitted so far (for journal linking)."""
        return self._structure_engine.events
