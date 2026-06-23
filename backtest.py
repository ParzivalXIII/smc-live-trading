"""
SMC Replay Backtest Harness — Event-Driven Causal Replay

Two-phase execution:
  Phase 1 (Streaming): Feed OHLC data through _SwingEngine.update(i, row)
                        bar-by-bar — the exact live trading code path.
  Phase 2 (Batch):     Run downstream methods (bos_choch, ob, liquidity,
                        retracements) on the full swing DataFrame.

Usage:
    # Function API
    from backtest import BacktestHarness, BacktestConfig
    harness = BacktestHarness(BacktestConfig())
    result = harness.run("data.csv")

    # CLI
    python backtest.py --data tests/test_data/EURUSD/EURUSD_15M.csv

Import path: `from backtest import BacktestHarness`
Run from project root or ensure PYTHONPATH includes project root.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Protocol

import numpy as np
import pandas as pd

from smartmoneyconcepts.smc import smc
from smartmoneyconcepts.structures import StructureEngine, SwingConfirmed
from trade_simulator import TradeSimulator

# =============================================================================
# T1: BacktestConfig — Configuration dataclass
# =============================================================================


@dataclass
class BacktestConfig:
    """Configuration for the SMC replay backtest harness.

    Attributes:
        swing_length: Lookback bars for swing candidate discovery.
        confirmation_bars: Minimum bars before a swing can be confirmed.
        atr_multiplier: ATR multiplier for retracement threshold.
        atr_period: Period for running ATR calculation.
        close_break: bos_choch: use close vs high/low for break detection.
        close_mitigation: ob: use close vs high/low for mitigation.
        range_percent: liquidity: range percent for swing clustering.
        date_column: Column name for timestamp in input CSV.
        date_format: strptime format for date parsing.
        lowercase_columns: Normalize column names to lowercase.
        overwrite: Overwrite existing output files.
    """

    # Swing engine parameters
    swing_length: int = 5
    confirmation_bars: int = 2
    atr_multiplier: float = 1.5
    atr_period: int = 7

    # Downstream method parameters
    close_break: bool = True
    close_mitigation: bool = False
    range_percent: float = 0.01

    # Data settings
    date_column: str = "Date"
    date_format: str = "%Y.%m.%d %H:%M:%S"
    lowercase_columns: bool = True

    # Export settings
    overwrite: bool = True

    # Streaming StructureEngine parameters
    bos_confirmation_window: int = 10  # Bars before provisional BOS/CHOCH is cancelled

    # V2 placeholders (unused in V1, default 0 = no effect)
    slippage_bps: float = 0.0      # Slippage in basis points
    fee_bps: float = 0.0           # Commission/fee in basis points


# =============================================================================
# T1: Dataset loading and validation
# =============================================================================


def load_dataset(path: str, config: BacktestConfig) -> pd.DataFrame:
    """Load OHLC CSV with column normalization.

    - Reads CSV
    - Parses date column as index
    - Renames columns to lowercase for engine compatibility
    - Ensures required columns exist: open, high, low, close, volume

    Args:
        path: Path to OHLC CSV file.
        config: BacktestConfig with date_column, date_format, lowercase_columns.

    Returns:
        DataFrame with datetime index and lowercase columns.

    Raises:
        FileNotFoundError: If path does not exist.
        ValueError: If required columns are missing.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Data file not found: {path}")

    df = pd.read_csv(path)

    # Parse date column
    if config.date_column in df.columns:
        df = df.set_index(config.date_column)
    else:
        raise ValueError(
            f"Date column '{config.date_column}' not found. "
            f"Available columns: {list(df.columns)}"
        )

    # Preserve original index for fallback parsing
    raw_index = df.index.copy()

    if config.date_format:
        df.index = pd.to_datetime(df.index, format=config.date_format, errors="coerce")
    else:
        # Auto-detect: try Unix timestamps (numeric) then general parse
        if df.index.dtype in ("int64", "float64"):
            df.index = pd.to_datetime(df.index, unit="s", errors="coerce")
        else:
            df.index = pd.to_datetime(df.index, errors="coerce")

    # Check for unparseable dates
    if df.index.isna().any():
        # Fall back to inferring format from raw values
        df.index = pd.to_datetime(raw_index, errors="coerce")
        if df.index.isna().any():
            raise ValueError(
                f"Could not parse date column '{config.date_column}' "
                f"with format '{config.date_format}'"
            )

    df.index.name = config.date_column

    # Lowercase columns for engine compatibility
    if config.lowercase_columns:
        df = df.rename(columns={c: c.lower() for c in df.columns})

    # Ensure required columns exist
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}. "
            f"Available columns: {list(df.columns)}"
        )

    # Keep only OHLCV columns
    ohlcv_columns = ["open", "high", "low", "close", "volume"]
    df = df[[c for c in ohlcv_columns if c in df.columns]]

    return df


def validate_dataset(data: pd.DataFrame, config: BacktestConfig) -> list[str]:
    """Validate dataset quality before running the backtest.

    Checks:
    - Minimum rows (>= max(swing_length, atr_period) + confirmation_bars)
    - Required columns exist (open, high, low, close, volume)
    - No entire columns are NaN
    - Timestamps are monotonic (no out-of-order data)
    - All numeric columns have numeric dtypes

    Args:
        data: OHLC DataFrame with lowercase columns.
        config: BacktestConfig with engine parameters.

    Returns:
        list[str]: List of warning/error messages. Empty if all checks pass.
    """
    warnings: list[str] = []

    # 1. Minimum rows
    min_rows = max(config.swing_length, config.atr_period) + config.confirmation_bars
    if len(data) < min_rows:
        warnings.append(
            f"Insufficient data: {len(data)} rows < {min_rows} required "
            f"(max(swing_length, atr_period) + confirmation_bars)"
        )

    # 2. Required columns
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(data.columns)
    if missing:
        warnings.append(f"Missing required columns: {sorted(missing)}")
        return warnings  # Cannot proceed with missing columns

    # 3. Check for entirely NaN columns
    for col in data.columns:
        if data[col].isna().all():
            warnings.append(f"Column '{col}' is entirely NaN")

    # 4. Monotonic timestamps
    if isinstance(data.index, pd.DatetimeIndex):
        if not data.index.is_monotonic_increasing:
            warnings.append("Timestamps are not monotonically increasing")
    else:
        warnings.append("Index is not a DatetimeIndex")

    # 5. Numeric dtypes
    for col in data.columns:
        if not pd.api.types.is_numeric_dtype(data[col]):
            warnings.append(f"Column '{col}' has non-numeric dtype: {data[col].dtype}")

    return warnings


# =============================================================================
# T2: Event schema + EventRecorder + StrategyCallback protocol
# =============================================================================


@dataclass
class BacktestEvent:
    """A single event recorded during replay."""

    timestamp: str  # ISO-formatted datetime string
    candle_index: int  # Position of CONFIRMATION bar in dataset
    pivot_index: int  # Position of PIVOT bar (where candidate was established)
    event_type: str  # "swing_high" or "swing_low"
    price: float  # Price level of the event
    metadata: str = ""  # JSON-encoded dict with extra fields


class EventRecorder:
    """Accumulates events during Phase 1 replay."""

    def __init__(self) -> None:
        self._events: List[BacktestEvent] = []

    def record_swing(
        self,
        index: int,
        pivot_index: int,
        timestamp: object,
        highlow: float,
        level: float,
    ) -> None:
        """Record a confirmed swing event from engine.update()."""
        event_type = "swing_high" if highlow == 1.0 else "swing_low"
        meta = json.dumps({
            "HighLow": float(highlow),
            "Level": float(level),
            "delay_bars": index - pivot_index,
        })
        self._events.append(
            BacktestEvent(
                timestamp=str(timestamp),
                candle_index=index,
                pivot_index=pivot_index,
                event_type=event_type,
                price=float(level),
                metadata=meta,
            )
        )

    @property
    def events(self) -> List[BacktestEvent]:
        return self._events

    def to_dataframe(self) -> pd.DataFrame:
        """Convert recorded events to a DataFrame for export."""
        df = pd.DataFrame([asdict(e) for e in self._events])
        if not df.empty:
            df["delay_bars"] = df["candle_index"] - df["pivot_index"]
        return df

    def clear(self) -> None:
        self._events = []


class StrategyCallback(Protocol):
    """Protocol for trade simulation callbacks.

    Implementations receive per-bar data and a TradeSimulator to execute trades.
    Called once per candle during Phase 3 (after batch analysis completes).
    The `row` parameter contains the full per-candle report (all indicators).

    Note: The `simulator` parameter is optional to support the Phase 1 call
    from `replay_phase()`, which passes only 3 args (candle_index, row, engine_result).
    """

    def update(
        self,
        candle_index: int,
        row: pd.Series,
        engine_result: dict[str, float],
        simulator: TradeSimulator | None = None,
        structure_events: list | None = None,
    ) -> None:
        """Called every bar with current candle, all indicators, and the simulator.

        Args:
            candle_index: Position in the dataset (0-based).
            row: Full per-candle report row (OHLCV + all indicators including
                BOS, OB, etc.).
            engine_result: Output from _SwingEngine.update() — contains
                "HighLow", "Level", and "PivotIndex" keys.
            simulator: TradeSimulator instance for entering/closing trades.
                Optional — None when called from Phase 1 (engine replay
                without trade sim).
            structure_events: Optional list of StructureEvent objects for this
                bar. Contains confirmed/cancelled status changes. Streaming
                strategies should use this instead of row["BOS"].
        """
        ...


class NoopStrategy:
    """No-op strategy. Accepts the new parameters but ignores them."""

    def update(
        self,
        candle_index: int,
        row: pd.Series,
        engine_result: dict[str, float],
        simulator = None,
        structure_events: list | None = None,
    ) -> None:
        pass


# =============================================================================
# T3: Phase 1 — Streaming replay loop
# =============================================================================


def replay_phase(
    data: pd.DataFrame,
    config: BacktestConfig,
    strategy_callback: Optional[StrategyCallback] = None,
) -> tuple[pd.DataFrame, List[BacktestEvent], list, object]:
    """Phase 1: Stream all candles through _SwingEngine.update().

    This is the EXACT code path that would be used in live trading.
    Each candle sees only past data — zero look-ahead.

    Args:
        data: OHLC DataFrame with lowercase columns.
        config: BacktestConfig with engine parameters.
        strategy_callback: Optional StrategyCallback for V2 trade simulation.

    Returns:
        swings_df: DataFrame with columns HighLow, Level, PivotIndex
                   (one row per candle).
        events: List of BacktestEvent objects for confirmed swings.
        structure_events: List of all StructureEvent objects (provisional,
                         confirmed, cancelled).
        structure_engine: StructureEngine instance (for optional post-hoc
                         inspection).

    Raises:
        ValueError: If any parameter is invalid.
    """
    # ---- Parameter validation (mirrors swing_highs_lows() 6 conditions) ----
    if config.swing_length < 2:
        raise ValueError(
            f"swing_length must be >= 2, got {config.swing_length}"
        )
    if config.confirmation_bars < 1:
        raise ValueError(
            f"confirmation_bars must be >= 1, got {config.confirmation_bars}"
        )
    if config.atr_multiplier <= 0:
        raise ValueError(
            f"atr_multiplier must be > 0, got {config.atr_multiplier}"
        )
    if config.atr_period < 1:
        raise ValueError(
            f"atr_period must be >= 1, got {config.atr_period}"
        )
    if max(config.swing_length, config.atr_period) + config.confirmation_bars > len(data):
        raise ValueError(
            f"max(swing_length, atr_period) ({max(config.swing_length, config.atr_period)}) + "
            f"confirmation_bars ({config.confirmation_bars}) = "
            f"{max(config.swing_length, config.atr_period) + config.confirmation_bars} > "
            f"len(data) ({len(data)}): insufficient data"
        )
    if config.atr_period > len(data):
        raise ValueError(
            f"atr_period ({config.atr_period}) > len(data) ({len(data)})"
        )

    # ---- Instantiate engines ----
    engine = smc._SwingEngine(
        config.swing_length,
        config.confirmation_bars,
        config.atr_multiplier,
        config.atr_period,
    )

    structure_engine = StructureEngine(
        confirmation_window=config.bos_confirmation_window,
    )

    recorder = EventRecorder()
    callback = strategy_callback or NoopStrategy()
    n = len(data)

    # Pre-allocate swing output arrays
    highs_lows = np.full(n, np.nan, dtype=np.float64)
    levels = np.full(n, np.nan, dtype=np.float64)
    pivot_indices = np.full(n, np.nan, dtype=np.float64)

    # Accumulate all structure events
    all_structure_events: list = []

    for i in range(n):
        row = data.iloc[i]
        high = float(row["high"])
        low = float(row["low"])

        # Step 1: Engine update — THIS is the live code path
        result = engine.update(i, row)

        # Step 2: Record swing output
        highlow = result["HighLow"]
        level = result["Level"]
        pivot_index = result.get("PivotIndex", np.nan)
        highs_lows[i] = highlow
        levels[i] = level
        pivot_indices[i] = pivot_index

        # Step 3: Record event if swing was confirmed → feed to StructureEngine
        new_structure_events: list = []
        if not np.isnan(highlow):
            recorder.record_swing(i, int(pivot_index), data.index[i], highlow, level)
            swing = SwingConfirmed(
                index=i,
                direction=int(highlow),
                level=float(level),
                timestamp=data.index[i],
                pivot_index=int(pivot_index),
            )
            new_structure_events = structure_engine.update(swing)
            all_structure_events.extend(new_structure_events)

        # Step 4: Check confirmations EVERY bar (not just on swings)
        status_changes = structure_engine.check_confirmations(i, high, low)
        # Dedup: if a provisional event was just emitted (step 3) AND confirmed
        # on the same bar, don't add it twice.
        all_structure_events.extend(
            e for e in status_changes if e not in new_structure_events
        )

        # Step 5: Strategy callback (V2 hook — no-op in V1)
        callback.update(i, row, result)

    swings_df = pd.concat(
        [
            pd.Series(highs_lows, name="HighLow", dtype=np.float64),
            pd.Series(levels, name="Level", dtype=np.float64),
            pd.Series(pivot_indices, name="PivotIndex", dtype=np.float64),
        ],
        axis=1,
    )

    return swings_df, recorder.events, all_structure_events, structure_engine


# =============================================================================
# T4: Phase 2 — Batch downstream analysis
# =============================================================================


def batch_analysis_phase(
    data: pd.DataFrame,
    swings_df: pd.DataFrame,
    config: BacktestConfig,
) -> dict[str, pd.DataFrame]:
    """Phase 2: Run all 4 downstream methods in batch mode.

    These methods require the FULL swing DataFrame to compute results
    (they scan forward for broken index, mitigated index, swept levels).

    Args:
        data: OHLC DataFrame with lowercase columns.
        swings_df: Swing DataFrame from replay_phase (columns: HighLow,
                   Level, PivotIndex).
        config: BacktestConfig with downstream method parameters.

    Returns:
        dict with keys: "bos_choch", "ob", "liquidity", "retracements"
        each value is a DataFrame with the method's output columns.
    """
    bos_choch_result = smc.bos_choch(
        data, swings_df, close_break=config.close_break
    )
    ob_result = smc.ob(
        data, swings_df, close_mitigation=config.close_mitigation
    )
    liquidity_result = smc.liquidity(
        data, swings_df, range_percent=config.range_percent
    )
    retracements_result = smc.retracements(data, swings_df)

    return {
        "bos_choch": bos_choch_result,
        "ob": ob_result,
        "liquidity": liquidity_result,
        "retracements": retracements_result,
    }


# =============================================================================
# T5: Per-candle report construction
# =============================================================================


def build_per_candle_report(
    data: pd.DataFrame,
    swings_df: pd.DataFrame,
    batch_results: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Build single per-candle DataFrame with all indicators merged.

    Merges on index position (row-by-row).
    Uses canonical column names from the Backtest Report Schema.

    Args:
        data: OHLC DataFrame with lowercase columns.
        swings_df: Swing DataFrame from replay_phase.
        batch_results: dict from batch_analysis_phase.

    Returns:
        DataFrame with 26 columns — one row per candle.
    """
    report = pd.DataFrame(index=data.index)

    # Input OHLC data
    report["Timestamp"] = data.index
    report["Open"] = data["open"].values
    report["High"] = data["high"].values
    report["Low"] = data["low"].values
    report["Close"] = data["close"].values
    report["Volume"] = data["volume"].values

    # Phase 1: Swing engine output (disambiguated column names)
    report["SwingHighLow"] = swings_df["HighLow"].values
    report["SwingLevel"] = swings_df["Level"].values
    report["SwingPivotIndex"] = swings_df["PivotIndex"].values

    # Phase 2: BOS/CHOCH (Level → BOSLevel to avoid collision)
    bc = batch_results["bos_choch"]
    report["BOS"] = bc["BOS"].values
    report["CHOCH"] = bc["CHOCH"].values
    report["BOSLevel"] = bc["Level"].values
    report["BrokenIndex"] = bc["BrokenIndex"].values

    # Phase 2: Order Blocks (Top → OBTop, Bottom → OBBottom, etc.)
    ob_r = batch_results["ob"]
    report["OB"] = ob_r["OB"].values
    report["OBTop"] = ob_r["Top"].values
    report["OBBottom"] = ob_r["Bottom"].values
    report["OBVolume"] = ob_r["OBVolume"].values
    report["OBMitigatedIndex"] = ob_r["MitigatedIndex"].values
    report["OBPct"] = ob_r["Percentage"].values

    # Phase 2: Liquidity (Level → LiqLevel, End → LiqEnd)
    liq = batch_results["liquidity"]
    report["Liquidity"] = liq["Liquidity"].values
    report["LiqLevel"] = liq["Level"].values
    report["LiqEnd"] = liq["End"].values
    report["LiqSwept"] = liq["Swept"].values

    # Phase 2: Retracements (Direction → RetraceDirection)
    ret = batch_results["retracements"]
    report["RetraceDirection"] = ret["Direction"].values
    report["CurrentRetracement%"] = ret["CurrentRetracement%"].values
    report["DeepestRetracement%"] = ret["DeepestRetracement%"].values

    return report


# =============================================================================
# T6: Metrics computation
# =============================================================================


def compute_metrics(
    report: pd.DataFrame,
    events: list,
    swings_df: pd.DataFrame,
    batch_swings: pd.DataFrame,
    config: object,
    processing_time: float,
) -> dict:
    """Compute all backtest metrics.

    Args:
        report: Per-candle report DataFrame from build_per_candle_report.
        events: List of BacktestEvent objects.
        swings_df: Swing DataFrame from replay_phase.
        batch_swings: Batch swing DataFrame for diff comparison.
        config: BacktestConfig (unused, kept for API compatibility).
        processing_time: Wall-clock time in seconds.

    Returns:
        dict with all metrics keys from the Backtest Report Schema.
    """
    _ = config  # unused but kept for API stability

    # ===== Swing counts =====
    swings = report["SwingHighLow"]
    total_swings = int(swings.notna().sum())
    swing_highs = int((swings == 1.0).sum())
    swing_lows = int((swings == -1.0).sum())

    # ===== BOS/CHOCH counts =====
    bos = report["BOS"]
    total_bos = int(bos.notna().sum())
    bull_bos = int((bos == 1).sum())
    bear_bos = int((bos == -1).sum())
    total_choch = int(report["CHOCH"].notna().sum())

    # ===== OB counts =====
    ob_col = report["OB"]
    total_ob = int(ob_col.notna().sum())
    bull_ob = int((ob_col == 1).sum())
    bear_ob = int((ob_col == -1).sum())

    # OB strength
    ob_pct = report["OBPct"]
    avg_ob_pct = (
        float(ob_pct[ob_pct.notna()].mean())
        if ob_pct.notna().any()
        else 0.0
    )

    # ===== Liquidity =====
    liq = report["Liquidity"]
    total_liq = int(liq.notna().sum())

    # Liquidity zone width (avg bars between start and end of zone)
    liq_end = report["LiqEnd"]
    zone_widths: list[float] = []
    for pos in range(len(report)):
        if not pd.isna(liq.iloc[pos]):
            end = liq_end.iloc[pos]
            if not pd.isna(end):
                zone_widths.append(float(int(end) - pos))
    avg_liq_zone_width_bars = (
        float(sum(zone_widths) / len(zone_widths)) if zone_widths else 0.0
    )

    # Sweep rate: fraction of liquidity zones that got swept
    # LiqSwept is 0.0 for unswept zones (end-of-dataset), >0 for swept, NaN for no zone
    liq_swept = report["LiqSwept"]
    swept_count = int((liq.notna() & (liq_swept > 0)).sum())
    unswept_zones = int(total_liq - swept_count)
    sweep_rate = round(swept_count / total_liq, 4) if total_liq > 0 else 0.0

    # ===== Event timing delay (actual measured, not configured) =====
    delays: list[int] = []
    for e in events:
        delay = e.candle_index - e.pivot_index
        delays.append(delay)
    avg_event_delay_bars = (
        round(sum(delays) / len(delays), 2) if delays else 0.0
    )
    min_event_delay = min(delays) if delays else 0
    max_event_delay = max(delays) if delays else 0

    # ===== Batch diff (replay vs swing_highs_lows — should be 0) =====
    if batch_swings is not None:
        replay = swings_df[["HighLow", "Level"]].reset_index(drop=True)
        batch = batch_swings[["HighLow", "Level"]].reset_index(drop=True)

        hl_match = (
            replay["HighLow"].fillna(-999).values
            == batch["HighLow"].fillna(-999).values
        )
        lvl_match = (
            replay["Level"].fillna(-999).values
            == batch["Level"].fillna(-999).values
        )
        diff_mask = ~(hl_match & lvl_match)
        diff_rows = int(diff_mask.sum())
        batch_diff_score = round(diff_rows / len(replay) * 100, 4)
    else:
        diff_rows = -1
        batch_diff_score = -1.0

    return {
        # Swing counts
        "total_swings": total_swings,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        # BOS/CHOCH
        "total_bos": total_bos,
        "bullish_bos": bull_bos,
        "bearish_bos": bear_bos,
        "total_choch": total_choch,
        # Order blocks
        "total_ob": total_ob,
        "bullish_ob": bull_ob,
        "bearish_ob": bear_ob,
        "avg_ob_pct": round(avg_ob_pct, 2),
        # Liquidity
        "total_liquidity_zones": total_liq,
        "avg_liq_zone_width_bars": round(avg_liq_zone_width_bars, 2),
        "sweep_rate": sweep_rate,
        "unswept_zones": unswept_zones,
        # Event timing (actual measured)
        "avg_event_delay_bars": avg_event_delay_bars,
        "min_event_delay": min_event_delay,
        "max_event_delay": max_event_delay,
        # Batch comparison
        "batch_diff_score": batch_diff_score,
        "diff_rows": diff_rows,
        # Performance
        "processing_time_seconds": round(processing_time, 2),
    }


# =============================================================================
# T2: Trade metrics computation
# =============================================================================


def compute_trade_metrics(
    trades_df: pd.DataFrame,
    equity_curve: pd.Series,
) -> dict[str, float | int]:
    """Compute V1 trade simulation metrics from closed trades.

    Args:
        trades_df: DataFrame from TradeSimulator.to_dataframe().
            Must have columns: side, entry_index, entry_time, entry_price,
            exit_index, exit_time, exit_price, pnl.
        equity_curve: Series from TradeSimulator.equity_curve().
            Cumulative PnL indexed by exit_index.

    Returns:
        dict with keys:
        - total_trades: int
        - wins: int
        - losses: int
        - win_rate: float (0.0 to 1.0, NaN if total_trades==0)
        - gross_profit: float
        - gross_loss: float (positive number)
        - profit_factor: float (gross_profit / gross_loss, 1e9 sentinel if gross_loss==0)
        - net_pnl: float
        - max_drawdown: float (positive number, NaN if no trades)
        - avg_trade_bars: float (mean trade duration in bars)
        - median_trade_bars: float (median trade duration in bars)
        - avg_win: float (mean PnL of winning trades, 0.0 if no wins)
        - avg_loss: float (mean absolute PnL of losing trades, 0.0 if no losses)
        - expectancy: float (win_rate * avg_win - (1 - win_rate) * avg_loss)
    """
    if trades_df.empty:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": float("nan"),
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": float("nan"),
            "net_pnl": 0.0,
            "max_drawdown": float("nan"),
        "avg_trade_bars": float("nan"),
        "median_trade_bars": float("nan"),
        }

    pnls = trades_df["pnl"].values
    total = len(pnls)
    wins = int((pnls > 0).sum())
    losses = int((pnls <= 0).sum())
    win_rate = round(wins / total, 4) if total > 0 else float("nan")
    gross_profit = float(pnls[pnls > 0].sum()) if wins > 0 else 0.0
    gross_loss = float(abs(pnls[pnls <= 0].sum())) if losses > 0 else 0.0
    profit_factor = (
        round(gross_profit / gross_loss, 4)
        if gross_loss > 0
        else 1e9 if gross_profit > 0
        else float("nan")
    )
    net_pnl = float(pnls.sum())

    # Max drawdown from equity curve
    if len(equity_curve) > 0:
        running_max = np.maximum.accumulate(equity_curve.values)
        drawdown = equity_curve.values - running_max
        max_drawdown = round(abs(float(drawdown.min())), 4)
    else:
        max_drawdown = float("nan")

    # Trade duration in bars (exclude open trades with no exit_index)
    closed = trades_df["exit_index"].notna()
    if closed.any():
        closed_durations = trades_df.loc[closed, "exit_index"].values - trades_df.loc[closed, "entry_index"].values
        avg_trade_bars = round(float(np.mean(closed_durations)), 2)
        median_trade_bars = round(float(np.median(closed_durations)), 2)
    else:
        avg_trade_bars = float("nan")
        median_trade_bars = float("nan")

    # Avg win / avg loss / expectancy (Fix 2) — use closed trades only (Fix 4)
    closed_pnls = trades_df.loc[closed, "pnl"].values if closed.any() else np.array([])
    closed_wins = int((closed_pnls > 0).sum()) if len(closed_pnls) > 0 else 0
    closed_losses = int((closed_pnls < 0).sum()) if len(closed_pnls) > 0 else 0
    avg_win = float(np.mean(closed_pnls[closed_pnls > 0])) if closed_wins > 0 else 0.0
    avg_loss = float(np.mean(abs(closed_pnls[closed_pnls < 0]))) if closed_losses > 0 else 0.0
    expectancy = round(win_rate * avg_win - (1 - win_rate) * avg_loss, 6) if total > 0 else 0.0

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": profit_factor,
        "net_pnl": round(net_pnl, 4),
        "max_drawdown": max_drawdown,
        "avg_trade_bars": avg_trade_bars,
        "median_trade_bars": median_trade_bars,
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "expectancy": expectancy,
    }


# =============================================================================
# T7: Export functions
# =============================================================================


def warn_if_large(data: pd.DataFrame, threshold: int = 100000) -> None:
    """Print warning if dataset exceeds threshold rows."""
    if len(data) > threshold:
        print(
            f"\u26a0\ufe0f  Warning: Large dataset ({len(data)} rows). "
            f"Expected runtime may exceed 60 seconds."
        )


def export_results(
    report: pd.DataFrame,
    events: list,
    metrics: dict,
    output_dir: str,
    overwrite: bool = True,
) -> None:
    """Export all backtest artifacts to CSV + JSON.

    Args:
        report: Per-candle report DataFrame.
        events: List of BacktestEvent objects.
        metrics: Metrics dict from compute_metrics().
        output_dir: Directory to write output files.
        overwrite: If False, raise FileExistsError if output_dir exists.

    Raises:
        FileExistsError: If output_dir exists and overwrite is False.
    """
    if not overwrite and os.path.exists(output_dir):
        raise FileExistsError(f"Output directory already exists: {output_dir}")

    os.makedirs(output_dir, exist_ok=True)

    # Per-candle report
    report_path = os.path.join(output_dir, "per_candle_report.csv")
    report.to_csv(report_path, index=False)

    # Event log
    event_log_path = os.path.join(output_dir, "event_log.csv")
    if events:
        event_df = pd.DataFrame([
            {
                "timestamp": e.timestamp,
                "candle_index": e.candle_index,
                "pivot_index": e.pivot_index,
                "event_type": e.event_type,
                "price": e.price,
                "delay_bars": e.candle_index - e.pivot_index,
                "metadata": e.metadata,
            }
            for e in events
        ])
        event_df.to_csv(event_log_path, index=False)
    else:
        pd.DataFrame(
            columns=[
                "timestamp",
                "candle_index",
                "pivot_index",
                "event_type",
                "price",
                "delay_bars",
                "metadata",
            ]
        ).to_csv(event_log_path, index=False)

    # Metrics (JSON for programmatic consumption)
    metrics_path = os.path.join(output_dir, "metrics.json")
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    # Metrics (text summary for human reading)
    summary_path = os.path.join(output_dir, "summary.txt")
    with open(summary_path, "w") as f:
        f.write("SMC Backtest Replay \u2014 Summary\n")
        f.write("=" * 40 + "\n")
        for k, v in metrics.items():
            f.write(f"{k}: {v}\n")


# =============================================================================
# T10: Batch comparison module (causality certification)
# =============================================================================


def compare_replay_to_batch(
    replay_swings: pd.DataFrame,
    data: pd.DataFrame,
    config: BacktestConfig,
) -> dict:
    """Compare replay Phase 1 output against batch swing_highs_lows().

    This proves the replay harness uses the EXACT same code path as the
    batch function, which has already been certified as causal by
    the 3-pass validation in test_causality.py.

    Args:
        replay_swings: Swing DataFrame from replay_phase.
        data: OHLC DataFrame with lowercase columns.
        config: BacktestConfig with engine parameters.

    Returns:
        dict with:
        - "pass": bool (True = zero differences)
        - "total_rows": int
        - "diff_rows": int (number of rows with any difference)
        - "diff_percent": float
        - "first_diff_index": int or None
    """
    batch = smc.swing_highs_lows(
        data,
        swing_length=config.swing_length,
        confirmation_bars=config.confirmation_bars,
        atr_multiplier=config.atr_multiplier,
        atr_period=config.atr_period,
    ).reset_index(drop=True)

    # Reset replay index for direct comparison (only HighLow, Level)
    replay = replay_swings[["HighLow", "Level"]].reset_index(drop=True)

    # Compare HighLow and Level
    hl_match = (
        replay["HighLow"].fillna(-999).values
        == batch["HighLow"].fillna(-999).values
    )
    lvl_match = (
        replay["Level"].fillna(-999).values
        == batch["Level"].fillna(-999).values
    )

    diff_mask = ~(hl_match & lvl_match)
    diff_count = int(diff_mask.sum())

    first_diff: int | None = (
        int(np.where(diff_mask)[0][0]) if diff_count > 0 else None
    )

    return {
        "pass": diff_count == 0,
        "total_rows": len(replay),
        "diff_rows": diff_count,
        "diff_percent": round(diff_count / len(replay) * 100, 4),
        "first_diff_index": first_diff,
    }


# =============================================================================
# T8: BacktestHarness — Orchestration class + BacktestResult
# =============================================================================


@dataclass
class BacktestResult:
    """Container for all backtest outputs."""

    config: BacktestConfig
    report: pd.DataFrame
    events: list
    swings_df: pd.DataFrame
    batch_results: dict[str, pd.DataFrame]
    metrics: dict
    trades: Optional[pd.DataFrame] = None       # NEW: trade log
    equity_curve: Optional[pd.Series] = None    # NEW: cumulative PnL
    structure_events: list = field(default_factory=list)
    structure_engine: object = None


class BacktestHarness:
    """Primary API for the SMC replay backtest harness.

    Usage:
        from backtest import BacktestHarness, BacktestConfig

        config = BacktestConfig()
        harness = BacktestHarness(config)
        result = harness.run("tests/test_data/EURUSD/EURUSD_15M.csv")
        print(result.metrics)

    Import path: ``from backtest import BacktestHarness``
    Run from project root or ensure PYTHONPATH includes project root.
    """

    def __init__(
        self,
        config: Optional[BacktestConfig] = None,
        strategy_callback: Optional[StrategyCallback] = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self._strategy_callback = strategy_callback or NoopStrategy()

    def set_strategy(self, strategy_callback: StrategyCallback) -> None:
        """Register a strategy callback for V2 trade simulation."""
        self._strategy_callback = strategy_callback

    def run(self, data_path: str) -> BacktestResult:
        """Run the full three-phase backtest.

        1. Load + validate data
        2. Phase 1: Replay (streaming swing engine)
        3. Phase 2: Batch analysis (downstream methods)
        4. Build per-candle report
        5. Phase 3: Strategy simulation
        6. Compute metrics (indicator + trade metrics)

        Args:
            data_path: Path to OHLC CSV file.

        Returns:
            BacktestResult with all outputs.
        """
        start = time.time()

        # Step 1: Load + validate data
        data = load_dataset(data_path, self.config)
        warnings = validate_dataset(data, self.config)
        if len(data) > 100000:
            warn_if_large(data)
        for w in warnings:
            print(f"\u26a0\ufe0f  Warning: {w}")

        # Step 2: Phase 1 — Replay (always use NoopStrategy here; strategy runs in Phase 3)
        swings_df, events, structure_events, structure_engine = replay_phase(
            data, self.config
        )

        # Step 3: Phase 2 — Batch analysis
        batch_results = batch_analysis_phase(data, swings_df, self.config)

        # Step 4: Build report
        report = build_per_candle_report(data, swings_df, batch_results)

        # Step 5: Phase 3 — Strategy simulation
        # Build per-bar events lookup
        structure_events_by_bar: dict[int, list] = {}
        for evt in structure_events:
            bar = (
                evt.confirmed_at_index
                if evt.status == "confirmed"
                else evt.trigger_index
            )
            if bar not in structure_events_by_bar:
                structure_events_by_bar[bar] = []
            structure_events_by_bar[bar].append(evt)

        simulator = TradeSimulator()
        n = len(data)
        for i in range(n):
            engine_result: dict[str, float] = {
                "HighLow": swings_df.iloc[i]["HighLow"],
                "Level": swings_df.iloc[i]["Level"],
                "PivotIndex": swings_df.iloc[i]["PivotIndex"],
            }
            bar_events = structure_events_by_bar.get(i, [])
            self._strategy_callback.update(
                i, report.iloc[i], engine_result, simulator, bar_events
            )

        trades_df = simulator.to_dataframe(include_open=True)
        equity_curve_series = simulator.equity_curve()
        trade_metrics = compute_trade_metrics(trades_df, equity_curve_series)

        # Step 6: Compute existing metrics + merge trade metrics
        elapsed = time.time() - start
        metrics = compute_metrics(
            report, events, swings_df, swings_df, self.config, elapsed
        )
        metrics.update(trade_metrics)

        return BacktestResult(
            config=self.config,
            report=report,
            events=events,
            swings_df=swings_df,
            batch_results=batch_results,
            metrics=metrics,
            trades=trades_df,
            equity_curve=equity_curve_series,
            structure_events=structure_events,
            structure_engine=structure_engine,
        )

    def run_and_export(
        self,
        data_path: str,
        output_dir: str = "backtest_results",
    ) -> BacktestResult:
        """Run backtest and export all results to output_dir.

        Args:
            data_path: Path to OHLC CSV file.
            output_dir: Directory to write output files.

        Returns:
            BacktestResult with all outputs.
        """
        result = self.run(data_path)
        export_results(
            result.report,
            result.events,
            result.metrics,
            output_dir,
            overwrite=self.config.overwrite,
        )
        return result


# =============================================================================
# T9: CLI wrapper
# =============================================================================


def main() -> None:
    """CLI entry point for the SMC replay backtest harness."""
    import argparse  # import inside function to avoid module-level trigger

    parser = argparse.ArgumentParser(
        description="SMC Replay Backtest Harness \u2014 "
        "Causal replay of swing engine"
    )
    parser.add_argument(
        "--data", required=True, help="Path to OHLC CSV file"
    )
    parser.add_argument(
        "--output-dir",
        default="backtest_results",
        help="Output directory for results",
    )

    # Engine parameters (optional, override defaults)
    parser.add_argument("--swing-length", type=int, default=5)
    parser.add_argument("--confirmation-bars", type=int, default=2)
    parser.add_argument("--atr-multiplier", type=float, default=1.5)
    parser.add_argument("--atr-period", type=int, default=7)

    # Downstream method parameters
    parser.add_argument(
        "--close-break",
        action="store_true",
        default=True,
        help="BOS/CHOCH: use close for break detection",
    )
    parser.add_argument(
        "--no-close-break",
        dest="close_break",
        action="store_false",
        help="BOS/CHOCH: use high/low for break detection",
    )
    parser.add_argument(
        "--close-mitigation",
        action="store_true",
        default=False,
        help="OB: use close for mitigation detection",
    )
    parser.add_argument(
        "--range-percent",
        type=float,
        default=0.01,
        help="Liquidity: range percent for swing clustering",
    )

    # Optional flags
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Run without exporting results (print only)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed progress",
    )

    args = parser.parse_args()

    config = BacktestConfig(
        swing_length=args.swing_length,
        confirmation_bars=args.confirmation_bars,
        atr_multiplier=args.atr_multiplier,
        atr_period=args.atr_period,
        close_break=args.close_break,
        close_mitigation=args.close_mitigation,
        range_percent=args.range_percent,
    )

    if args.verbose:
        print(f"Config: {config}")

    harness = BacktestHarness(config)

    if args.no_export:
        result = harness.run(args.data)
    else:
        result = harness.run_and_export(args.data, args.output_dir)

    # Print summary
    print("\n" + "=" * 50)
    print("SMC Backtest Replay \u2014 Results")
    print("=" * 50)
    for k, v in result.metrics.items():
        print(f"  {k}: {v}")
    print("=" * 50)


if __name__ == "__main__":
    main()
