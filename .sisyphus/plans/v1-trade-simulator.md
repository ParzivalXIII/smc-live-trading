# V1 Trade Simulator — Event-Driven Replay Strategy Layer

## TL;DR
> **Quick Summary**: Bolt a trade simulator onto the existing replay harness so any strategy can answer: *"If I acted on these events, what would have happened?"* The simulator tracks 0-or-1 positions, logs closed trades, and computes 8 trade metrics (total_trades, win_rate, profit_factor, max_drawdown, etc.). The first strategy — BOS Flip — enters on BOS signals and closes on opposite-side BOS or same-side swing confirmation.
>
> **Deliverables**:
> - `trade_simulator.py` — `Trade` dataclass + `TradeSimulator` class (position management, trade log, equity curve)
> - `strategies/bos_flip.py` — `BOSFlipStrategy` (first concrete strategy)
> - `strategies/__init__.py` — package marker
> - Updated `backtest.py` — expanded `StrategyCallback` Protocol, updated `NoopStrategy`, updated `BacktestResult` (adds `trades`, `equity_curve` fields), restructured `BacktestHarness.run()` (three-phase: engine → batch → strategy)
> - `compute_trade_metrics()` — 8 trade-specific metrics (separate from existing `compute_metrics()`)
>
> **Estimated Effort**: Medium (6 implementation tasks + 1 integration + 3 final verification)
> **Parallel Execution**: YES — 3 waves
> **Critical Path**: T1 → T4 → T5 → T6 → F1–F4

## Context

### Research Findings

**1. StrategyCallback Protocol (backtest.py:279–300)**
```python
class StrategyCallback(Protocol):
    def update(self, candle_index: int, row: pd.Series,
               engine_result: dict[str, float]) -> None: ...
```
Current signature receives `row` = OHLCV-only series (`data.iloc[i]`) and `engine_result` = dict with `HighLow`, `Level`, (and `PivotIndex` on confirmation bars). **No batch indicators (BOS, OB, etc.) available during this call.**

**2. NoopStrategy (backtest.py:303–312)**
```python
class NoopStrategy:
    def update(self, candle_index: int, row: pd.Series,
               engine_result: dict[str, float]) -> None: pass
```

**3. BacktestResult (backtest.py:833–842)**
```python
@dataclass
class BacktestResult:
    config: BacktestConfig
    report: pd.DataFrame
    events: list
    swings_df: pd.DataFrame
    batch_results: dict[str, pd.DataFrame]
    metrics: dict
```

**4. BacktestHarness.run() flow (backtest.py:868–919)**
```
load data → Phase 1 (engine replay with callback) → Phase 2 (batch) → build report → compute metrics
```
The strategy callback is called inside `replay_phase()` during the engine loop (Phase 1). It only receives engine results — BOS/CHOCH/OB/liquidity are not computed yet.

**5. Engine vs Batch indicator timing**
| Indicator | Available When | Source |
|-----------|---------------|--------|
| HighLow (swing) | Phase 1 (streaming) | `engine.update()` |
| Level, PivotIndex | Phase 1 (streaming) | `engine.update()` |
| BOS, CHOCH | Phase 2 (batch) | `bos_choch()` |
| OB | Phase 2 (batch) | `ob()` |
| Liquidity | Phase 2 (batch) | `liquidity()` |
| Retracements | Phase 2 (batch) | `retracements()` |

**Key insight**: BOS signals are stamped at swing confirmation indices. The same bar that produces a `HighLow` value in Phase 1 will have a corresponding `BOS` value in Phase 2's `bos_choch()` output. This means a BOS-based strategy **must run after batch analysis completes**.

**⚠️ Column naming note**: Phase 3 passes `report.iloc[i]` to the strategy, which uses **uppercase** column names (`Close`, `BOS`, `SwingHighLow`, `OB`, etc.) per the per-candle report schema. Phase 1's `data.iloc[i]` uses **lowercase** columns (`open`, `high`, `low`, `close`, `volume`). Strategies should reference `row["Close"]` (uppercase) in Phase 3, not `row["close"]` (lowercase).

**6. Existing test data**: `tests/test_data/` contains EURUSD 15M (24425 rows). No BTCUSDT test file exists yet for the success criterion — will use EURUSD as default.

### Interview Decisions (Auto-Resolved)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Strategy execution phase** | Phase 3 (after batch) | BOS/OB/etc only available after Phase 2. Cannot run BOS Flip strategy during Phase 1. |
| **Row content for strategy** | Full report row (`report.iloc[i]` — 26 cols) | Gives strategy access to ALL indicators (BOS, SwingHighLow, OB, etc.) without adding new parameters. |
| **File organization** | Separate `trade_simulator.py` + `strategies/` | Keeps `backtest.py` manageable (already 1044 lines). Trade domain is distinct from replay infrastructure. |
| **Entry/exit price** | Close of signal bar | V1 simplicity — immediate execution. OK for "what would have happened" analysis. |
| **Equity curve** | Realized PnL only (no mark-to-market) | V1 simplicity — only account for closed trades. |
| **Metrics** | Separate `compute_trade_metrics()` | Existing `compute_metrics()` computes indicator-level stats (swing counts, BOS counts, etc.). Trade metrics are a separate concern. |
| **Implicit close on entry** | Yes — close existing position before opening new one | Prevents orphan positions. BOS Flip strategy already does explicit close-before-enter, so this is a safety net. |
| **Per-bar engine results** | Reconstruct from `swings_df` | No need to store a separate list — `swings_df` already has HighLow, Level, PivotIndex at every index. |
| **Phase 1 callback** | Pass `NoopStrategy()` to `replay_phase()` | Avoids calling the user's strategy twice (once in Phase 1 with no data, once in Phase 3 with full data). |

### Metis Review

| Gap | Severity | Resolution |
|-----|----------|------------|
| BOS not available in Phase 1 → strategy can't check BOS | **Critical** | Strategy runs in Phase 3 (after batch). `row` parameter becomes full report row with all indicators. |
| Existing `NoopStrategy.update()` has wrong arity for new Protocol | Minor | Update `NoopStrategy` to accept `simulator` parameter (ignored). |
| `BacktestResult` users accessing fields by position | Minor | Add `trades` and `equity_curve` as optional fields with `None` default — backward compatible. |
| The `BacktestHarness.run()` currently calls strategy in `replay_phase()` | Minor | `BacktestHarness.run()` passes `NoopStrategy()` to `replay_phase()`. Real strategy runs in Phase 3. |
| No BTCUSDT test file exists for the success criterion | Minor | Use EURUSD for integration tests. BTCUSDT can be added later. The strategy is market-agnostic. |
| BOSFlipStrategy creates and holds a `TradeSimulator` — who owns it? | Resolved | `BacktestHarness.run()` creates the `TradeSimulator`, passes it to the strategy. Harness owns it, strategy mutates it. |

## Work Objectives

### Core Objective
Add a V1 trade simulation layer to the existing replay harness so strategies can enter/close positions based on SMC events and answer: *"If I acted on these events, what would have happened?"* — without position sizing, slippage, risk management, or portfolio logic.

### Concrete Deliverables
1. `trade_simulator.py` — `Trade` dataclass + `TradeSimulator` class
2. `strategies/__init__.py` — package marker
3. `strategies/bos_flip.py` — `BOSFlipStrategy` (enters on BOS, closes on opposite BOS or same-side swing)
4. Updated `backtest.py`:
   - Expanded `StrategyCallback` Protocol (adds `simulator` parameter)
   - Updated `NoopStrategy` (accepts `simulator`, ignores it)
   - Updated `BacktestResult` (adds `trades: DataFrame`, `equity_curve: Series`)
   - Restructured `BacktestHarness.run()` (three-phase: engine → batch → strategy)
   - New `compute_trade_metrics()` function
5. Integration test proving end-to-end: `BOSFlipStrategy` + `BacktestHarness` → trades, equity_curve, metrics

### Definition of Done
- [ ] `TradeSimulator` manages 0-or-1 position correctly across `enter_long`, `enter_short`, `close`
- [ ] `BOSFlipStrategy` makes trades that can be inspected in `result.trades` DataFrame
- [ ] `result.equity_curve` is a Series of cumulative realized PnL
- [ ] `compute_trade_metrics()` returns all 8 metrics: total_trades, wins, losses, win_rate, gross_profit, gross_loss, profit_factor, net_pnl, max_drawdown
- [ ] Existing `NoopStrategy` still works (backward compatible signature update)
- [ ] Existing integration tests still pass (no regression in indicator computation)
- [ ] The swing engine (`smc.py`) is **NOT modified** — zero changes
- [ ] Success criterion runs end-to-end:
  ```python
  from backtest import BacktestHarness, BacktestConfig
  from strategies.bos_flip import BOSFlipStrategy
  cfg = BacktestConfig()
  strategy = BOSFlipStrategy()
  harness = BacktestHarness(cfg, strategy_callback=strategy)
  result = harness.run("tests/test_data/EURUSD/EURUSD_15M.csv")
  result.trades   # DataFrame with trade log
  result.equity_curve  # Series with cumulative PnL
  result.metrics  # Dict including all 8 trade metrics
  ```

### Must Have
- `TradeSimulator` — single position only (0 or 1), enter/close methods, position state properties (`is_flat`, `is_long`, `is_short`)
- Trade log as DataFrame: entry_time, exit_time, side, entry_price, exit_price, pnl
- Equity curve as Series: cumulative realized PnL indexed by exit_index
- 8 trade metrics: total_trades, wins, losses, win_rate, gross_profit, gross_loss, profit_factor, net_pnl, max_drawdown
- `StrategyCallback` updated to pass `TradeSimulator` to the strategy
- `BOSFlipStrategy` — enters on BOS signal, closes on opposite BOS or same-side swing
- Three-phase harness: engine replay → batch analysis → strategy simulation
- `BacktestResult.trades` and `BacktestResult.equity_curve` populated after run
- `NoopStrategy` updated to accept new Protocol signature

### Must NOT Have (Guardrails)
- ❌ No changes to `smc.py` (zero modifications to the swing engine)
- ❌ No position sizing / risk % — V2 territory
- ❌ No ATR stops, trailing stops, or take-profit logic — V2 territory
- ❌ No slippage models or commission/fee computation
- ❌ No portfolio / multi-position management (single position only)
- ❌ No visualization / plotting
- ❌ No parameter optimization or grid search
- ❌ No `report` schema changes (existing 26 columns stay)
- ❌ No new third-party dependencies (pandas, numpy only)
- ❌ Do not change `replay_phase()` signature (preserve backward compat)
- ❌ **max_drawdown is realized PnL only** — intra-trade floating PnL (unrealized drawdown) is NOT tracked in V1. The max_drawdown metric understates true risk because it only sees closed trades. V2 will add intra-trade mark-to-market drawdown tracking.

### Known Limitations (V1)
1. **BOS look-ahead bias**: BOS signals are retroactively detected by `bos_choch()` after all 4 swings are confirmed. A BOS stamp at bar N requires future bars to establish the pattern. Entering at bar N close uses information that wasn't available in real-time. This answers *"what if I had known the BOS was there"* not *"what would I have seen at bar N close."* Acceptable for V1 historical analysis. V2 needs streaming-compatible signal detection.
2. **Realized-PnL-only drawdown**: See guardrail above. Max drawdown is computed from closed trade PnLs only. A trade that goes 10% underwater before closing at +1% shows 0 drawdown in V1.
3. **Close-of-bar execution**: Entry/exit at signal bar close assumes immediate execution. Real trading has delay, slippage, and partial fills. V1 is an approximation.

### Future Work (V2 Directions)
- **Trend filters**: Only take BOS signals in the direction of the prevailing swing trend (avoid counter-trend entries).
- **Streaming-compatible signals**: Replace retroactive BOS with a real-time swing confirmation signal that doesn't require 4 future swings.
- **Intra-trade drawdown**: Mark-to-market equity curve with floating PnL on open positions.
- **Slippage & fees**: `slippage_bps` and `fee_bps` parameters on `BacktestConfig` to model execution costs.
- **ATR-based stops**: Dynamic stop-loss based on ATR multiple.
- **Position sizing**: Risk % per trade, Kelly criterion, fixed fractional.
- **Multi-timeframe confirmation**: Higher timeframe trend filter.

## Verification Strategy

### Test Decision
Tests-after implementation. The trade simulator is validated by running against EURUSD 15M data with the BOSFlipStrategy and verifying trade counts, PnL values, and metric sanity.

### QA Policy — All agent-executed
Every task includes agent-executable QA scenarios. Scenarios use `interactive_bash` (Python scripts), file comparison, and stdout parsing. All scenarios write evidence to `.sisyphus/evidence/`.

### Key QA Patterns
1. **TradeSimulator unit tests**: Synthetic candles, verify enter/close counts, position state, PnL correctness
2. **BOSFlipStrategy on real data**: Run end-to-end, verify non-trivial trade count (not zero, not every bar)
3. **Metrics sanity check**: Verify metric math (wins + losses = total, etc.)
4. **Backward compat**: `NoopStrategy.update()` with new signature runs without error
5. **No smc.py changes**: `git diff -- smartmoneyconcepts/smc.py` is empty

### Trade Metrics Tolerance
For EURUSD 15M (24425 bars) with default parameters, expect:
- Total trades: at least 10 (non-trivial signal count)
- Win rate: between 20% and 80% (not degenerate)
- Profit factor: any non-negative value (BOS Flip is intentionally dumb)
- Net PnL: any value (strategy is intentionally naive — correctness over profitability)
- Max drawdown: >= 0

## Execution Strategy

### Parallel Execution Waves

```
Wave 1: Foundation           [T1, T2]       — parallel (independent)
              │
Wave 2: Strategy Layer       [T3, T4]       — sequential (T1 → T3; T2 → T4)
              │
Wave 3: Harness Integration  [T5] then [T6] — T5 blocks T6 (T6 can partially parallel with T3)
              │
Wave 4: Verification         [F1, F2, F3, F4] — parallel
```

### Dependency Matrix
| Task | Depends On | Blocks |
|------|-----------|--------|
| T1 | — | T3, T5 |
| T2 | — | T5 (metrics reference) |
| T3 | T1 | T5 |
| T4 | T2 | T5 |
| T5 | T1, T2, T3, T4 | T6 |
| T6 | T5 | F1 |
| F1 | T6 | — |
| F2 | T6 | — |
| F3 | T6 | — |
| F4 | T6 (git diff) | — |

### Agent Dispatch Summary

| Wave | Tasks | Agent Type | Rationale |
|------|-------|------------|-----------|
| 1 | T1, T2 | `general` | Python dataclass + simple class design. Independent of harness internals. |
| 2 | T3 | `general` | Strategy implementation — needs to know report column names and simulator interface. |
| 2 | T4 | `general` | Protocol update + BacktestResult change — needs existing backtest.py structure. |
| 3 | T5 | `unspecified-high` | Core harness restructure — three-phase flow. Deepest change to backtest.py. Must understand replay_phase, batch_analysis, report building, and strategy execution. |
| 3 | T6 | `general` | Integration test — wires everything together. |
| F | F1–F4 | `oracle` + `unspecified-high` | Final verification — needs comprehensive system understanding. |

## TODOs

### Wave 1 — Foundation (Parallel)

- [ ] T1. Create `trade_simulator.py` with `Trade` dataclass + `TradeSimulator` class
  **What to do**: Create new file `trade_simulator.py` at project root containing the `Trade` dataclass and `TradeSimulator` class. This is the core trade simulation engine — no dependency on backtest.py, smc.py, or any other project file.

  **Design**:
  ```python
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
  ```

  **Entry price logic**: Uses the `price` parameter passed by the strategy. The strategy is responsible for passing the correct price. For the BOS Flip strategy, this will be the close of the signal bar (`row["Close"]`). This gives the strategy control over pricing without coupling the simulator to any specific price model.

  **Implicit close on enter**: `_close_existing()` is called at the start of `enter_long()` and `enter_short()`. This prevents orphan positions if the strategy enters without closing. The BOS Flip strategy already checks `is_flat` before entering, so this is a safety net.

  **No-op close when flat**: `close()` returns silently if there's no position. This prevents errors when strategies call `close()` defensively.

  **Must NOT do**:
  - ❌ Do not add risk management, sizing, slippage, or commission logic
  - ❌ Do not import from `backtest` or `smc` — this file must be standalone
  - ❌ Do not add portfolio/multi-position support
  - ❌ Do not add stop-loss or take-profit logic
  - ❌ Do not hardcode any price strategy (entry_price is always caller-supplied)
  - ❌ Do not use any third-party imports beyond pandas, numpy, dataclasses

  **Recommended Agent Profile**: `general` — Python dataclass + simple class
  **Parallelization**: Wave 1, independent
  **References**: User's `Trade` and `TradeSimulator` spec in the original request

  **Acceptance Criteria**:
  - [ ] `TradeSimulator` starts flat (`is_flat == True`)
  - [ ] `enter_long(0, ts, 1.0)` sets `is_long == True`, not flat
  - [ ] `enter_short(0, ts, 1.0)` sets `is_short == True`, not flat
  - [ ] `close(5, ts, 1.05)` on a LONG at 1.0 produces pnl=0.05
  - [ ] `close(5, ts, 0.95)` on a SHORT at 1.0 produces pnl=0.05
  - [ ] `close()` on flat position does nothing (no error, no trade recorded)
  - [ ] `enter_long()` when already long closes the first trade, opens a second
  - [ ] `closed_trades` list grows correctly across multiple enter/close cycles
  - [ ] `to_dataframe()` returns correct DataFrame with all 8 columns
  - [ ] `to_dataframe()` returns empty DataFrame (0 rows, 8 columns) when no trades
  - [ ] `equity_curve()` returns Series with cumulative PnL indexed by exit_index
  - [ ] `equity_curve()` returns empty Series when no trades
  - [ ] `Trade` dataclass has exactly 8 fields with correct types
  - [ ] No imports from `backtest`, `smc`, or any third-party lib beyond pandas/numpy

  **QA Scenarios**:
  1. **Basic enter/close cycle**
     - Tool: `interactive_bash` — Python
     - Preconditions: `trade_simulator.py` exists
     - Steps:
       ```python
       from trade_simulator import TradeSimulator
       import pandas as pd
       
       sim = TradeSimulator()
       assert sim.is_flat and not sim.is_long and not sim.is_short
       
       ts = pd.Timestamp("2023-01-01")
       sim.enter_long(10, ts, 100.0)
       assert sim.is_long and not sim.is_flat
       
       sim.close(20, pd.Timestamp("2023-01-02"), 105.0)
       assert sim.is_flat
       assert len(sim.closed_trades) == 1
       assert sim.closed_trades[0].pnl == 5.0  # LONG: exit - entry
       assert sim.closed_trades[0].side == "LONG"
       assert sim.closed_trades[0].entry_index == 10
       assert sim.closed_trades[0].exit_index == 20
       print("Basic cycle: PASS")
       ```
     - Expected: All assertions pass
     - Evidence: `.sisyphus/evidence/t1-basic-cycle.txt`

  2. **Short trade with negative PnL**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       sim = TradeSimulator()
       sim.enter_short(5, pd.Timestamp("2023-01-01"), 100.0)
       sim.close(10, pd.Timestamp("2023-01-02"), 110.0)
       assert sim.closed_trades[0].pnl == -10.0  # SHORT: entry - exit
       print("Short trade: PASS")
       ```
     - Expected: pnl == -10.0 (loss)
     - Evidence: `.sisyphus/evidence/t1-short-trade.txt`

  3. **Multiple trades + implicit close**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       sim = TradeSimulator()
       sim.enter_long(0, pd.Timestamp("2023-01-01"), 100.0)
       sim.enter_long(5, pd.Timestamp("2023-01-02"), 110.0)  # implicit close at 110
       assert len(sim.closed_trades) == 1  # first trade got closed
       assert sim.closed_trades[0].exit_index == 5
       assert sim.closed_trades[0].exit_price == 110.0
       print("Implicit close: PASS")
       
       sim.close(10, pd.Timestamp("2023-01-03"), 105.0)
       assert len(sim.closed_trades) == 2
       print("Two trades: PASS")
       ```
     - Expected: First trade auto-closed when second opened
     - Evidence: `.sisyphus/evidence/t1-implicit-close.txt`

  4. **DataFrame export**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       from trade_simulator import TradeSimulator
       import pandas as pd
       
       # Empty
       sim = TradeSimulator()
       df = sim.to_dataframe()
       assert len(df) == 0
       assert list(df.columns) == [
           "side", "entry_index", "entry_time", "entry_price",
           "exit_index", "exit_time", "exit_price", "pnl",
       ]
       print("Empty DataFrame: PASS")
       
       # With trades
       sim.enter_long(0, pd.Timestamp("2023-01-01"), 100.0)
       sim.close(5, pd.Timestamp("2023-01-02"), 105.0)
       df = sim.to_dataframe()
       assert len(df) == 1
       assert df.iloc[0]["pnl"] == 5.0
       
       # Equity curve
       ec = sim.equity_curve()
       assert len(ec) == 1
       assert ec.iloc[0] == 5.0
       print("DataFrame export: PASS")
       ```
     - Expected: Correct DataFrame shape and values
     - Evidence: `.sisyphus/evidence/t1-dataframe-export.txt`


- [ ] T2. Create `compute_trade_metrics()` function (in `backtest.py`)
  **What to do**: Add a function `compute_trade_metrics(trades_df, equity_curve)` to `backtest.py` that computes the 8 V1 trade metrics from closed trades. This is SEPARATE from the existing `compute_metrics()` function which computes indicator-level stats.

  **Design**:
  ```python
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
      }
  ```

  **Placement**: Add to `backtest.py` near the existing `compute_metrics()` function (after line 674, before the export section). This keeps all metrics computation together.

  **Important**: `profit_factor` uses `1e9` sentinel when gross_loss=0 but gross_profit>0 (perfect strategy). When both are 0, profit_factor = NaN. `win_rate` and `max_drawdown` return NaN when there are no trades. These NaN values signal "undefined" rather than "zero" for aggregate statistics.

  **Must NOT do**:
  - ❌ Do not modify the existing `compute_metrics()` function
  - ❌ Do not merge trade metrics into `compute_metrics()` return dict (they're separate logic)
  - ❌ Do not compute risk-adjusted metrics (Sharpe, Sortino) — V2
  - ❌ Do not add drawdown duration or recovery metrics — V2
  - ✅ Use `1e9` sentinel for profit_factor when gross_loss == 0 (not `float('inf')`, not `None`). `1e9` is JSON-safe and trivially recognizable as a sentinel.
  - ✅ Use `float("nan")` for undefined metrics (win_rate when no trades, profit_factor when no trades, max_drawdown when no trades). NaN serializes as `null` in JSON, which is unambiguous.
  
  **Recommended Agent Profile**: `general` — numpy vectorized computation
  **Parallelization**: Wave 1, independent
  **References**: User's metrics spec (8 metrics), existing `compute_metrics()` at backtest.py:541-674

  **Acceptance Criteria**:
  - [ ] Function accepts `(trades_df, equity_curve)` and returns dict with 9 keys
  - [ ] Empty trades DataFrame: total_trades=0, win_rate=NaN, profit_factor=NaN, max_drawdown=NaN
  - [ ] Single winning trade: total_trades=1, wins=1, losses=0, win_rate=1.0
  - [ ] Single losing trade: total_trades=1, wins=0, losses=1, win_rate=0.0
  - [ ] 10 trades with 7 wins, 3 losses: win_rate=0.7
  - [ ] gross_profit = sum of positive PnLs
  - [ ] gross_loss = sum of absolute negative PnLs (positive number)
  - [ ] profit_factor = gross_profit / gross_loss
  - [ ] profit_factor = 1e9 (sentinel) when gross_loss == 0 and gross_profit > 0
  - [ ] profit_factor = NaN when gross_loss == 0 and gross_profit == 0
  - [ ] net_pnl = sum of all PnLs (positive - negative)
  - [ ] max_drawdown >= 0, computed from equity curve peak-to-trough
  - [ ] max_drawdown = NaN when no trades (no equity curve)
  - [ ] Function is pure (no side effects, no file I/O)
  - [ ] All values JSON-serializable (NaN → null in JSON, 1e9 sentinel for profit_factor)

  **QA Scenarios**:
  1. **Metrics with synthetic trade data**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       import pandas as pd
       import numpy as np
       from backtest import compute_trade_metrics
       
       # 5 trades: 3 wins (1.0, 2.0, 0.5), 2 losses (-0.5, -1.0)
       trades = pd.DataFrame({
           "pnl": [1.0, -0.5, 2.0, -1.0, 0.5],
           "exit_index": [10, 20, 30, 40, 50],
       })
       equity = pd.Series(np.cumsum([1.0, -0.5, 2.0, -1.0, 0.5]), name="equity_curve")
       metrics = compute_trade_metrics(trades, equity)
       
       assert metrics["total_trades"] == 5
       assert metrics["wins"] == 3
       assert metrics["losses"] == 2
       assert metrics["win_rate"] == 0.6
       assert metrics["gross_profit"] == 3.5  # 1.0 + 2.0 + 0.5
       assert metrics["gross_loss"] == 1.5    # 0.5 + 1.0
       assert metrics["profit_factor"] == 3.5 / 1.5
       assert metrics["net_pnl"] == 2.0       # 1.0 - 0.5 + 2.0 - 1.0 + 0.5
       assert metrics["max_drawdown"] >= 0
       print("Trade metrics: PASS")
       print(metrics)
       ```
     - Expected: All assertions pass
     - Evidence: `.sisyphus/evidence/t2-metrics.txt`

  2. **Empty trades**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       import numpy as np
       empty_trades = pd.DataFrame(columns=["pnl", "exit_index"])
       empty_equity = pd.Series(dtype=np.float64, name="equity_curve")
       m = compute_trade_metrics(empty_trades, empty_equity)
       assert m["total_trades"] == 0
       assert m["net_pnl"] == 0.0
       assert np.isnan(m["win_rate"]), f"win_rate should be NaN, got {m['win_rate']}"
       assert np.isnan(m["profit_factor"]), f"profit_factor should be NaN, got {m['profit_factor']}"
       assert np.isnan(m["max_drawdown"]), f"max_drawdown should be NaN, got {m['max_drawdown']}"
       print("Empty metrics: PASS")
       ```
     - Expected: win_rate, profit_factor, max_drawdown are NaN; total_trades=0, net_pnl=0.0
     - Evidence: `.sisyphus/evidence/t2-empty-metrics.txt`

  3. **Perfect strategy (no losses)**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       import math
       trades = pd.DataFrame({"pnl": [1.0, 2.0, 3.0], "exit_index": [1, 2, 3]})
       equity = pd.Series(np.cumsum([1.0, 2.0, 3.0]))
       m = compute_trade_metrics(trades, equity)
       assert m["profit_factor"] == 1e9, f"Expected 1e9, got {m['profit_factor']}"
       assert not math.isinf(m["profit_factor"]), "Should not be infinity"
       print("Perfect strategy: PASS")
       ```
     - Expected: profit_factor = 1e9 (sentinel, not infinity)
     - Evidence: `.sisyphus/evidence/t2-perfect.txt`


### Wave 2 — Strategy Layer (Sequential within wave)

- [ ] T3. Update `StrategyCallback` Protocol + `NoopStrategy` + `BacktestResult` in `backtest.py`
  **What to do**: Make backward-compatible changes to existing code in `backtest.py`. The critical constraint: `replay_phase()` calls `callback.update(i, row, result)` with **3 arguments** (no simulator). Phase 3 calls with **4 arguments** (with simulator). The Protocol must accept BOTH calling conventions.

  **1. Update StrategyCallback Protocol** (lines 279–300):
  ```python
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
      ) -> None:
          """Called every bar with current candle, all indicators, and the simulator.
          
          Args:
              candle_index: Position in the dataset (0-based).
              row: Full per-candle report row (OHLCV + all indicators including BOS, OB, etc.).
              engine_result: Output from _SwingEngine.update() — contains
                  "HighLow", "Level", and "PivotIndex" keys.
              simulator: TradeSimulator instance for entering/closing trades.
                  Optional — None when called from Phase 1 (engine replay without trade sim).
          """
          ...
  ```

  **2. Update NoopStrategy** (lines 303–312):
  ```python
  class NoopStrategy:
      """No-op strategy. Accepts the new parameters but ignores them."""
      
      def update(
          self,
          candle_index: int,
          row: pd.Series,
          engine_result: dict[str, float],
          simulator = None,
      ) -> None:
          pass
  ```

  **6. (Optional) Add placeholder fields to BacktestConfig** for V2 compatibility:
  ```python
  @dataclass
  class BacktestConfig:
      # ... existing fields ...
      
      # V2 placeholders (unused in V1, default 0 = no effect)
      slippage_bps: float = 0.0      # Slippage in basis points
      fee_bps: float = 0.0           # Commission/fee in basis points
  ```
  These fields exist but are not consumed by any V1 code. They reserve the API surface so V2 doesn't need to change the config schema.

  **⚠️ Migration Note**: If you have custom `StrategyCallback` implementations outside this repo, they must:
  1. Add a 4th parameter `simulator` with default `None` to their `update()` method
  2. Add `from __future__ import annotations` at the top of the file (required for `TradeSimulator | None` syntax on Python 3.9)
  3. Either import `TradeSimulator` for type hints or use string literal `"TradeSimulator"`
  The V1 Protocol change is backward-compatible at the *calling* level (3-arg calls still work) but NOT at the *implementation* level (existing implementations with 3 parameters will get a TypeError when Phase 3 calls with 4). All in-house implementations (NoopStrategy, BOSFlipStrategy) are updated in this plan.

  **3. Update BacktestResult** (lines 833–842):
  ```python
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
  ```

  **4. Add import for TradeSimulator** at the top of `backtest.py`:
  ```python
  from trade_simulator import TradeSimulator
  ```

  **5. Update BacktestHarness.__init__** to accept strategy_callback in constructor (lines 860–862):
  ```python
  def __init__(
      self, 
      config: Optional[BacktestConfig] = None,
      strategy_callback: Optional[StrategyCallback] = None,
  ) -> None:
      self.config = config or BacktestConfig()
      self._strategy_callback = strategy_callback or NoopStrategy()
  ```
  The current API (`harness = BacktestHarness(config)`) still works. The new API (`BacktestHarness(config, strategy_callback=strategy)`) is the V1 pattern.

  **Backward Compatibility Analysis**:
  - **`replay_phase()` calls `callback.update(i, row, result)` with 3 args.** This is the critical constraint. The Protocol makes `simulator` optional (default `None`), so 3-arg calls still work. `NoopStrategy` uses `simulator = None` default, so Phase 1's 3-arg call doesn't break.
  - **Phase 3 calls `strategy_callback.update(i, report.iloc[i], engine_result, simulator)` with 4 args.** This is fine — the Protocol accepts 4, and `NoopStrategy` accepts 4 via the optional default.
  - **BOSFlipStrategy (T4) uses `simulator` without a default** — it always needs it. It is ONLY called from Phase 3 (4-arg), never from Phase 1 (3-arg). This is enforced by how `BacktestHarness.run()` is restructured in T5.
  - `BacktestResult` gets 2 optional fields with `None` defaults. All existing code that destructures `BacktestResult` by field name (e.g., `result.metrics`) works unchanged. Only code accessing fields by position would break — there's no such usage.
  - `BacktestHarness.__init__` gets an optional parameter. Existing code `BacktestHarness(config)` works unchanged.
  - `StrategyCallback` Protocol change is technically a breaking change for anyone implementing the Protocol without the optional 4th parameter. See ⚠️ Migration Note above.

  **Must NOT do**:
  - ❌ Do not remove the old `BacktestHarness.__init__` signature — make `strategy_callback` optional
  - ❌ Do not change the order of existing `BacktestResult` fields — append at the end
  - ❌ Do not remove or rename existing `BacktestResult` fields
  - ❌ Do not change the `set_strategy()` method — it still works as before
  - ❌ Do not change `replay_phase()` signature (preserve backward compat)
  - ❌ Do not modify existing `compute_metrics()` function

  **Recommended Agent Profile**: `general` — Protocol, dataclass, backward compatibility
  **Parallelization**: Wave 2, blocked by T1 (imports TradeSimulator)
  **References**: backtest.py lines 279–312 (StrategyCallback, NoopStrategy), lines 833—867 (BacktestResult, BacktestHarness.__init__)

  **Acceptance Criteria**:
  - [ ] `StrategyCallback` Protocol has `simulator` as 4th parameter with `None` default (`TradeSimulator | None = None`)
  - [ ] `NoopStrategy.update()` accepts 4 parameters (last optional, defaults to None) and runs without error
  - [ ] `NoopStrategy.update(0, row, result)` with 3 args runs without error (Phase 1 compat)
  - [ ] `NoopStrategy.update(0, row, result, simulator)` with 4 args runs without error (Phase 3 compat)
  - [ ] `BacktestResult` has `trades` field (default None) and `equity_curve` field (default None)
  - [ ] `BacktestHarness(config)` still works (no positional args required)
  - [ ] `BacktestHarness(config, strategy_callback=my_strategy)` works
  - [ ] `harness.set_strategy(my_strategy)` still works
  - [ ] `import backtest` works without error (TradeSimulator import path correct)
  - [ ] Existing code that accesses `result.metrics` still works (no field renames)
  - [ ] `BacktestConfig` has `slippage_bps` and `fee_bps` fields (both default 0.0)

  **QA Scenarios**:
  1. **Protocol update verification (3-arg + 4-arg compatibility)**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       from backtest import BacktestHarness, BacktestConfig, NoopStrategy
       from trade_simulator import TradeSimulator
       import pandas as pd
       import numpy as np
       
       strat = NoopStrategy()
       row = pd.Series({"close": 1.0, "BOS": 1.0})
       engine_result = {"HighLow": 1.0, "Level": 1.0, "PivotIndex": np.nan}
       
       # Phase 1 style: 3 args (no simulator) — MUST work
       strat.update(0, row, engine_result)
       print("NoopStrategy with 3 args: PASS")
       
       # Phase 3 style: 4 args (with simulator) — MUST work
       sim = TradeSimulator()
       strat.update(0, row, engine_result, sim)
       print("NoopStrategy with 4 args: PASS")
       
       # BacktestHarness with strategy_callback kwarg
       harness = BacktestHarness(BacktestConfig(), strategy_callback=strat)
       print("BacktestHarness with strategy: PASS")
       
       # BacktestHarness without strategy_callback (default)
       harness2 = BacktestHarness(BacktestConfig())
       print("BacktestHarness default: PASS")
       ```
     - Expected: 3-arg and 4-arg calls both work. No TypeError.
     - Evidence: `.sisyphus/evidence/t3-protocol-update.txt`

  2. **BacktestResult backward compat**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       from backtest import BacktestResult, BacktestConfig
       import pandas as pd
       
       result = BacktestResult(
           config=BacktestConfig(),
           report=pd.DataFrame(),
           events=[],
           swings_df=pd.DataFrame(),
           batch_results={},
           metrics={},
       )
       assert result.trades is None
       assert result.equity_curve is None
       print("BacktestResult backward compat: PASS")
       ```
     - Expected: New fields default to None
     - Evidence: `.sisyphus/evidence/t3-backward-compat.txt`


- [ ] T4. Create `strategies/bos_flip.py` with `BOSFlipStrategy`
  **What to do**: Create `strategies/` directory at project root, add `__init__.py` and `bos_flip.py`. Implement the `BOSFlipStrategy` — the first concrete strategy for V1.

  **Strategy Logic** (from user's spec):
  ```
  If BOS == 1 (bullish):
      If currently short → close position
      If flat → enter long (at current bar's close)
  
  If BOS == -1 (bearish):
      If currently long → close position
      If flat → enter short (at current bar's close)
  ```

  **Key insight**: BOS signals are stamped at swing confirmation indices. When the engine confirms a swing, Phase 2's `bos_choch()` checks if that swing forms a BOS pattern with the previous 3 swings. If yes, BOS is stamped at that same index. The strategy receives the full report row (with BOS column) in Phase 3.

  **Design**:
  ```python
  """
  BOS Flip Strategy — V1 Trade Simulator Strategy
  
  Enters a position when a Break of Structure (BOS) is detected.
  - Bullish BOS (BOS == 1): Close short → Enter long
  - Bearish BOS (BOS == -1): Close long → Enter short
  
  This is intentionally simple. No filters, no confirmation, no risk management.
  """
  
  from __future__ import annotations
  
  from typing import TYPE_CHECKING
  
  import numpy as np
  import pandas as pd
  
  if TYPE_CHECKING:
      from trade_simulator import TradeSimulator
  
  
  class BOSFlipStrategy:
      """Strategy that flips position direction on BOS signals.
      
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
          simulator: TradeSimulator,
      ) -> None:
          """Called every bar. Checks BOS signal and acts.
          
          Args:
              candle_index: Bar index (0-based).
              row: Full per-candle report row (has "BOS" column among others).
              engine_result: Engine output (HighLow, Level, PivotIndex).
              simulator: TradeSimulator for trade execution.
          """
          bos = row.get("BOS", np.nan)
          if np.isnan(bos):
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
  ```

  **Why `row.get("BOS", np.nan)`**: In Phase 3, `row` is the full report row which includes the "BOS" column. Using `.get()` instead of `[]` handles the edge case where BOS might not be in the row dict (defensive programming). Returns NaN if missing, which causes the strategy to skip the bar.

  **Why `close_price` from `row["Close"]`**: Using the close of the signal bar. This assumes the strategy can execute at the close price of the bar where the signal appears. For V1 this is the simplest model. The report row has "Close" column (all caps, per the report schema).

  **Directory structure**:
  ```
  strategies/
      __init__.py   (empty — package marker)
      bos_flip.py   (BOSFlipStrategy)
  ```

  **Must NOT do**:
  - ❌ Do not add entry filters or confirmation logic — V1 is intentionally dumb
  - ❌ Do not add stop losses, take profits, or trailing stops — V2
  - ❌ Do not import from `backtest` or `smc` — only depends on `trade_simulator.TradeSimulator` and pandas
  - ❌ Do not use ATR, volatility, or any price-based filters
  - ❌ Do not hardcode prices or trade parameters
  - ❌ Do not modify global state or class-level variables (strategy must be re-usable)
  - ❌ Do not add complex state tracking — the TradeSimulator owns position state

  **Recommended Agent Profile**: `general` — strategy implementation
  **Parallelization**: Wave 2, blocked by T1
  **References**: User's BOS Flip strategy spec, report schema (BOS column in per_candle_report)

  **Acceptance Criteria**:
  - [ ] `strategies/__init__.py` exists and is importable
  - [ ] `from strategies.bos_flip import BOSFlipStrategy` works
  - [ ] `BOSFlipStrategy().update(0, report_row, engine_result, simulator)` runs without error
  - [ ] Strategy only acts when `BOS` is not NaN (ignores non-signal bars)
  - [ ] Bullish BOS (`BOS == 1`) on flat: enters long
  - [ ] Bullish BOS (`BOS == 1`) on short: closes short → enters long
  - [ ] Bearish BOS (`BOS == -1`) on flat: enters short
  - [ ] Bearish BOS (`BOS == -1`) on long: closes long → enters short
  - [ ] No trades opened on non-BOS bars (most bars have NaN BOS)
  - [ ] Entry price is `row["Close"]` (close of signal bar)
  - [ ] No `backtest` imports — only `trade_simulator`

  **QA Scenarios**:
  1. **Strategy unit test with synthetic data**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       from strategies.bos_flip import BOSFlipStrategy
       from trade_simulator import TradeSimulator
       import pandas as pd
       import numpy as np
       
       strategy = BOSFlipStrategy()
       sim = TradeSimulator()
       
       # Bar 0: no BOS → no action
       row0 = pd.Series({"Close": 100.0, "BOS": np.nan}, name=pd.Timestamp("2023-01-01"))
       strategy.update(0, row0, {"HighLow": np.nan}, sim)
       assert sim.is_flat, "Should be flat on no-BOS bar"
       
       # Bar 1: bullish BOS → enter long
       row1 = pd.Series({"Close": 101.0, "BOS": 1.0}, name=pd.Timestamp("2023-01-02"))
       strategy.update(1, row1, {"HighLow": 1.0}, sim)
       assert sim.is_long, "Should be long after bullish BOS"
       assert sim.position.entry_price == 101.0
       assert sim.position.entry_index == 1
       
       # Bar 2: bearish BOS → close long, enter short
       row2 = pd.Series({"Close": 99.0, "BOS": -1.0}, name=pd.Timestamp("2023-01-03"))
       strategy.update(2, row2, {"HighLow": -1.0}, sim)
       assert sim.is_short, "Should be short after bearish BOS"
       assert len(sim.closed_trades) == 1
       assert sim.closed_trades[0].side == "LONG"
       assert sim.closed_trades[0].pnl == 99.0 - 101.0  # -2.0
       
       print("BOS Flip strategy unit test: PASS")
       ```
     - Expected: Correct position flips, correct PnL
     - Evidence: `.sisyphus/evidence/t4-bos-flip-unit.txt`

  2. **BOS on non-BOS bar does nothing**
     - Tool: `interactive_bash` — Python
     - Steps: Same as above, verify NaN BOS passes through
     - Expected: No trades on NaN rows
     - Evidence: `.sisyphus/evidence/t4-bos-nan.txt`


### Wave 3 — Harness Integration (Sequential)

- [ ] T5. Restructure `BacktestHarness.run()` for three-phase execution
  **What to do**: Modify `BacktestHarness.run()` in `backtest.py` to restructure the execution flow. The current two-phase flow (engine → batch) becomes a three-phase flow (engine → batch → strategy). The strategy callback is moved from Phase 1 to Phase 3.

  **Current flow** (backtest.py lines 868–919):
  ```
  load data
  Phase 1: swings_df, events = replay_phase(data, config, strategy_callback)  
           # ^^^ strategy called inside replay_phase with engine results only
  Phase 2: batch_results = batch_analysis_phase(data, swings_df, config)
  Build report
  Compute metrics
  Return BacktestResult
  ```

  **New flow**:
  ```
  load data
  Phase 1: swings_df, events = replay_phase(data, config)  
           # ^^^ pass NoopStrategy() always — strategy runs in Phase 3
  Phase 2: batch_results = batch_analysis_phase(data, swings_df, config)
  Build report: report = build_per_candle_report(data, swings_df, batch_results)
  
  Phase 3: Strategy simulation
    Create TradeSimulator
    for i in range(len(data)):
        engine_result = {
            "HighLow": swings_df.iloc[i]["HighLow"],
            "Level": swings_df.iloc[i]["Level"],
            "PivotIndex": swings_df.iloc[i]["PivotIndex"],
        }
        strategy_callback.update(i, report.iloc[i], engine_result, simulator)
    trades_df = simulator.to_dataframe()
    equity_curve = simulator.equity_curve()
    trade_metrics = compute_trade_metrics(trades_df, equity_curve)
  
  Compute existing metrics
  Merge trade_metrics into metrics dict
  Return BacktestResult with trades, equity_curve
  ```

  **Code changes in `BacktestHarness.run()`**:

  Lines 893–895 (Phase 1 call):
  ```python
  # OLD:
  swings_df, events = replay_phase(data, self.config, self._strategy_callback)
  
  # NEW: Always pass NoopStrategy to replay_phase (strategy runs in Phase 3)
  swings_df, events = replay_phase(data, self.config)  # callback NOT passed
  ```

  After Phase 2 (after line 899, before line 902):
  ```python
  # OLD:
  # Step 4: Build report
  report = build_per_candle_report(data, swings_df, batch_results)
  
  # Step 5: Compute metrics
  elapsed = time.time() - start
  metrics = compute_metrics(...)
  
  return BacktestResult(...)
  
  # NEW:
  # Step 4: Build report (same)
  report = build_per_candle_report(data, swings_df, batch_results)
  
  # Step 5: Phase 3 — Strategy simulation
  from trade_simulator import TradeSimulator  # already imported at module top
  
  simulator = TradeSimulator()
  n = len(data)
  for i in range(n):
      engine_result: dict[str, float] = {
          "HighLow": swings_df.iloc[i]["HighLow"],
          "Level": swings_df.iloc[i]["Level"],
          "PivotIndex": swings_df.iloc[i]["PivotIndex"],
      }
      self._strategy_callback.update(
          i, report.iloc[i], engine_result, simulator
      )
  
  trades_df = simulator.to_dataframe()
  equity_curve_series = simulator.equity_curve()
  trade_metrics = compute_trade_metrics(trades_df, equity_curve_series)
  
  # Step 6: Compute existing metrics
  elapsed = time.time() - start
  metrics = compute_metrics(
      report, events, swings_df, swings_df, self.config, elapsed
  )
  metrics.update(trade_metrics)  # Merge trade metrics into the metrics dict
  
  return BacktestResult(
      config=self.config,
      report=report,
      events=events,
      swings_df=swings_df,
      batch_results=batch_results,
      metrics=metrics,
      trades=trades_df,
      equity_curve=equity_curve_series,
  )
  ```

  **Important design notes**:
  - `engine_result` dict always includes all 3 keys: `HighLow`, `Level`, `PivotIndex`. For non-confirmation bars, HighLow=NaN, Level=NaN, PivotIndex=NaN. This is fine — the strategy checks `BOS` from the report row, not from engine_result.
  - `report.iloc[i]` returns a Series with the index set to the timestamp (from the per-candle report construction). The strategy accesses row.name for the timestamp and row["Close"] for the close price. This matches the report schema.
  - `compute_trade_metrics()` is called after Phase 3, and its results are merged into the overall metrics dict with `.update()`. This adds the 9 trade metric keys to the existing metrics.
  - The Phase 3 loop is O(n) where n = number of candles. For 24k bars, this is negligible (< 1 second). No performance concern.
  - **Important (C1)**: The Phase 3 call passes 4 args to `strategy_callback.update()` including the simulator. This is compatible with the updated Protocol where `simulator` is optional (default None). The `replay_phase()` internal call still uses 3 args (no simulator) — both calling conventions work.

  **Must NOT do**:
  - ❌ Do not change the `replay_phase()` function signature (keep backward compat)
  - ❌ Do not change `batch_analysis_phase()` or `build_per_candle_report()`
  - ❌ Do not change the existing `compute_metrics()` return dict keys
  - ❌ Do not add new dependencies
  - ❌ Do not modify `smc.py`
  - ❌ Do not add try/except around strategy callback (let exceptions propagate)
  - ❌ Do not create a separate `TradeSimulator` per strategy — one per run

  **Recommended Agent Profile**: `unspecified-high` — harness integration, order of execution
  **Parallelization**: Wave 3, blocked by T1, T2, T3, T4
  **References**: backtest.py lines 868–919 (existing run() method), lines 541–674 (compute_metrics), lines 833–842 (BacktestResult)

  **Acceptance Criteria**:
  - [ ] `BacktestHarness.run()` succeeds with default config and NoopStrategy (no regressions)
  - [ ] `BacktestHarness.run()` with NoopStrategy returns `trades=None` and `equity_curve=None` (NoopStrategy does nothing, TradeSimulator has no trades)
  - [ ] `BacktestHarness.run()` with `BOSFlipStrategy` returns non-empty `trades` DataFrame
  - [ ] `result.trades` has correct columns: side, entry_index, entry_time, entry_price, exit_index, exit_time, exit_price, pnl
  - [ ] `result.equity_curve` is a Series indexed by exit_index
  - [ ] `result.metrics` includes all existing indicator metrics PLUS 9 trade metrics
  - [ ] Trade metric keys: total_trades, wins, losses, win_rate, gross_profit, gross_loss, profit_factor, net_pnl, max_drawdown
  - [ ] Existing `replay_phase()` still callable with and without strategy_callback
  - [ ] Existing tests still pass (backward compat)
  - [ ] No changes to `smc.py` (verify with `git diff -- smartmoneyconcepts/smc.py`)
  - [ ] Run 24k bars in < 60 seconds total (Phase 3 is negligible overhead)

  **QA Scenarios**:
  1. **NoopStrategy regression test**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       from backtest import BacktestHarness, BacktestConfig
       TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
       
       harness = BacktestHarness(BacktestConfig())
       result = harness.run(TEST_DATA_PATH)
       
       # Existing metrics still present
       assert "total_swings" in result.metrics
       assert "batch_diff_score" in result.metrics
       assert result.metrics["batch_diff_score"] == 0.0
       
       # No trades (NoopStrategy does nothing)
       assert result.trades is None or len(result.trades) == 0
       print(f"Metrics keys: {sorted(result.metrics.keys())}")
       print(f"Report shape: {result.report.shape}")
       print("NoopStrategy regression: PASS")
       ```
     - Expected: Existing metrics present, no trades, no errors
     - Evidence: `.sisyphus/evidence/t5-noop-regression.txt`

  2. **BOSFlipStrategy end-to-end**
     - Tool: `interactive_bash` — Python
     - Steps:
       ```python
       from backtest import BacktestHarness, BacktestConfig
       from strategies.bos_flip import BOSFlipStrategy
       TEST_DATA_PATH = "tests/test_data/EURUSD/EURUSD_15M.csv"
       
       strategy = BOSFlipStrategy()
       harness = BacktestHarness(BacktestConfig(), strategy_callback=strategy)
       result = harness.run(TEST_DATA_PATH)
       
       # Non-trivial trades
       assert result.trades is not None
       assert len(result.trades) > 0, "BOS Flip should produce at least one trade"
       print(f"Total trades: {len(result.trades)}")
       
       # Trades DataFrame structure
       expected_cols = [
           "side", "entry_index", "entry_time", "entry_price",
           "exit_index", "exit_time", "exit_price", "pnl",
       ]
       assert list(result.trades.columns) == expected_cols, \
           f"Columns: {list(result.trades.columns)}"
       
       # Equity curve
       assert result.equity_curve is not None
       assert len(result.equity_curve) == len(result.trades)
       print(f"Equity curve length: {len(result.equity_curve)}")
       
       # Trade metrics in result.metrics
       trade_keys = ["total_trades", "wins", "losses", "win_rate",
                     "gross_profit", "gross_loss", "profit_factor",
                     "net_pnl", "max_drawdown"]
       for key in trade_keys:
           assert key in result.metrics, f"Missing trade metric: {key}"
       
       print(f"Trade metrics: { {k: result.metrics[k] for k in trade_keys} }")
       print("BOSFlipStrategy E2E: PASS")
       ```
     - Expected: Strategy produces trades, metrics are populated, no errors
     - Evidence: `.sisyphus/evidence/t5-bos-flip-e2e.txt`


- [ ] T6. Full integration test
  **What to do**: Create a comprehensive integration test that validates the entire V1 trade simulator pipeline. Run end-to-end with EURUSD data and verify all outputs.

  **Test the following scenarios**:
  1. Default config + NoopStrategy (no trades, existing metrics unchanged)
  2. Default config + BOSFlipStrategy (trades produced, metrics populated)
  3. Custom config + BOSFlipStrategy (different parameters produce different trade results)
  4. Non-trivial trade count (at least 5 trades on 24k bars)
  5. Trades DataFrame shape and column correctness
  6. Equity curve sanity (cumulative, indexed by exit_index)
  7. Metric math consistency (wins + losses = total_trades, etc.)
  8. Strategy has no side effects across runs (idempotent)

  **Must NOT do**:
  - ❌ Do not assert specific numerical values for strategy profitability (BOS Flip is intentionally dumb)
  - ❌ Do not add to the existing `unit_tests.py` test suite
  - ❌ Do not skip assertions — all must be verifiable programmatically
  - ❌ Do not compare trade counts across different parameter configs (they naturally differ)

  **Recommended Agent Profile**: `general` — test infrastructure
  **Parallelization**: Wave 3, blocked by T5
  **References**: Previous integration test pattern (T11 from backtest-replay-harness plan), T5 QA scenarios

  **Acceptance Criteria**:
  - [ ] NoopStrategy test: 0 trades, existing metrics unchanged
  - [ ] BOSFlipStrategy test: > 0 trades, all 9 trade metrics present
  - [ ] Custom config produces different trade count than default config
  - [ ] wins + losses == total_trades
  - [ ] gross_profit >= 0, gross_loss >= 0
  - [ ] profit_factor == gross_profit / gross_loss (when gross_loss > 0)
  - [ ] net_pnl == sum of all PnLs
  - [ ] max_drawdown >= 0
  - [ ] equity_curve length matches total_trades
  - [ ] All tests pass under 120 seconds total
  - [ ] Test is idempotent (same result on repeat runs)

  **QA Scenarios**:
  1. **Full integration test suite**
     - Tool: `interactive_bash` — Python
     - Steps: Single comprehensive Python script (inline or temp file) that runs all scenarios
     - Expected: All assertions pass, output printed to stdout
     - Evidence: `.sisyphus/evidence/t6-integration.txt`

     The test script should be structured as:
     ```python
     # === V1 Trade Simulator Integration Test ===
     
     from backtest import BacktestHarness, BacktestConfig, NoopStrategy
     from strategies.bos_flip import BOSFlipStrategy
     import pandas as pd
     
     TEST_DATA = "tests/test_data/EURUSD/EURUSD_15M.csv"
     
     errors = []
     
     def check(condition, msg):
         if not condition:
             errors.append(msg)
             print(f"  FAIL: {msg}")
         else:
             print(f"  PASS: {msg}")
     
     # === Scenario 1: NoopStrategy (regression) ===
     print("\n=== Scenario 1: NoopStrategy ===")
     harness1 = BacktestHarness(BacktestConfig())
     result1 = harness1.run(TEST_DATA)
     check("total_swings" in result1.metrics, "Existing metrics present")
     check(result1.metrics["batch_diff_score"] == 0.0, "Batch diff == 0")
     has_trades = result1.trades is None or len(result1.trades) == 0
     check(has_trades, "NoopStrategy produces no trades")
     
     # === Scenario 2: BOSFlipStrategy (default config) ===
     print("\n=== Scenario 2: BOSFlipStrategy ===")
     strategy = BOSFlipStrategy()
     harness2 = BacktestHarness(BacktestConfig(), strategy_callback=strategy)
     result2 = harness2.run(TEST_DATA)
     trades2 = result2.trades
     check(trades2 is not None and len(trades2) > 0, f"Trades produced: {len(trades2)}")
     
     if trades2 is not None and len(trades2) > 0:
         expected_cols = [
             "side", "entry_index", "entry_time", "entry_price",
             "exit_index", "exit_time", "exit_price", "pnl",
         ]
         check(list(trades2.columns) == expected_cols, "Trade DataFrame columns correct")
         check(len(result2.equity_curve) == len(trades2), "Equity curve matches trade count")
         check(result2.metrics["total_trades"] == len(trades2), "Metrics.total_trades matches")
         check(result2.metrics["wins"] + result2.metrics["losses"] == len(trades2), 
               "wins + losses == total_trades")
         check(result2.metrics["max_drawdown"] >= 0, "max_drawdown >= 0")
     
     # === Scenario 3: BOSFlipStrategy (custom config) ===
     print("\n=== Scenario 3: Custom config ===")
     cfg3 = BacktestConfig(swing_length=10, confirmation_bars=3)
     harness3 = BacktestHarness(cfg3, strategy_callback=BOSFlipStrategy())
     result3 = harness3.run(TEST_DATA)
     trades3 = result3.trades
     check(trades3 is not None, "Custom config produces trades")
     if trades3 is not None and trades2 is not None:
         check(len(trades3) != len(trades2) or result3.metrics["net_pnl"] != result2.metrics["net_pnl"],
               "Different configs produce different results (or same by coincidence)")
     
     print(f"\n{'='*50}")
     print(f"Results: {len(errors)} failures")
     if errors:
         for e in errors:
             print(f"  - {e}")
     else:
         print("ALL INTEGRATION TESTS PASSED")
     print(f"{'='*50}")
     ```
     - Expected: All checks pass, 0 failures
     - Evidence: `.sisyphus/evidence/t6-integration.txt`


### Final Verification Wave

- [ ] F1. Plan Compliance Audit (oracle)
  **What to do**: Audit all modified and new files against this plan. Verify:
  - `trade_simulator.py` matches the T1 spec exactly
  - `strategies/bos_flip.py` matches the T4 spec exactly
  - `backtest.py` changes: Protocol updated, NoopStrategy updated, BacktestResult updated, run() restructured, compute_trade_metrics() added
  - No scope creep (no smc.py changes, no position sizing, no slippage)
  - All guardrails respected
  - All acceptance criteria from every task are met
  - Backward compatibility preserved (NoopStrategy, BacktestResult)

  **Recommended Agent Profile**: `oracle`
  **Parallelization**: After T6, parallel with F2–F4
  **References**: This entire plan document

  **Acceptance Criteria**:
  - [ ] Audit report generated listing all met/unmet criteria
  - [ ] No violations of guardrails
  - [ ] All deliverables present

  **QA Scenarios**:
  1. **Compliance check**
     - Tool: `interactive_bash` — Python
     - Steps: Script that imports all new modules, verifies all classes/methods exist, checks signatures
     - Evidence: `.sisyphus/evidence/f1-compliance.txt`


- [ ] F2. Code Quality Review (unspecified-high)
  **What to do**: Review all new and modified code for:
  - Proper separation of concerns (TradeSimulator is standalone, strategies import only trade_simulator)
  - Type hints on all public functions and methods
  - Docstrings for all public classes, methods, and functions
  - No dead code or commented-out sections
  - Error handling (what happens if strategy crashes? — let it propagate for V1)
  - PEP 8 compliance
  - No duplicate imports
  - No circular imports

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: After T6, parallel with F1, F3, F4

  **Acceptance Criteria**:
  - [ ] Review report generated with findings
  - [ ] All critical issues fixed before merge


- [ ] F3. Real Manual QA (unspecified-high)
  **What to do**: Run the BOSFlipStrategy end-to-end and manually inspect:
  - `result.trades` — trade log shows plausible entries/exits at BOS signal indices
  - `result.equity_curve` — cum PnL moves up and down with trade outcomes
  - Trade alternation: LONG → SHORT → LONG → SHORT (BOS Flip flips between long and short)
  - Trade pairs: every long has a matching close (no orphan positions)
  - Metrics sanity: win_rate, profit_factor, net_pnl, max_drawdown are all reasonable
  - No crashes or warnings during execution

  **Recommended Agent Profile**: `unspecified-high`
  **Parallelization**: After T6, parallel with F1, F2, F4

  **Acceptance Criteria**:
  - [ ] Plausible trade log with alternating directions
  - [ ] No orphan positions (every entry has a corresponding exit)
  - [ ] No warnings or errors in output
  - [ ] Metrics are internally consistent


- [ ] F4. Scope Fidelity Check (deep)
  **What to do**: Final check that ONLY the intended files were created/modified:
  - `git diff --stat` — only `backtest.py` (modified), `trade_simulator.py` (new), `strategies/__init__.py` (new), `strategies/bos_flip.py` (new)
  - `git diff -- smartmoneyconcepts/smc.py` — empty (zero changes to swing engine)
  - No changes to `pyproject.toml`, `setup.py`, `.gitignore`, or config files
  - No new third-party dependencies
  - No modification of existing test infrastructure (`test_causality.py`, `stream_compare.py`, `unit_tests.py` unchanged)
  - Evidence files NOT committed (`.sisyphus/evidence/` is ephemeral)

  **Recommended Agent Profile**: `deep`
  **Parallelization**: After T6, parallel with F1–F3

  **Acceptance Criteria**:
  - [ ] `git diff --stat` shows exactly 4 files changed (1 modified + 3 new)
  - [ ] No smc.py changes
  - [ ] No unintended modifications to unrelated files
  - [ ] Clean diff scope


## Commit Strategy

1. **Per-wave commits** during development:
   - `feat(trade-sim): add Trade dataclass and TradeSimulator class` (T1)
   - `feat(backtest): add compute_trade_metrics function` (T2)
   - `feat(backtest): update StrategyCallback Protocol, NoopStrategy, BacktestResult` (T3)
   - `feat(strategy): add BOSFlipStrategy` (T4)
   - `feat(backtest): restructure BacktestHarness.run() for three-phase execution` (T5)
   - `test(trade-sim): add integration test for V1 trade simulator` (T6)

2. **Squash to 1-2 commits** before main merge:
   - `feat(trade-sim): add V1 trade simulator with BOSFlipStrategy`

3. **Do NOT commit evidence files** (`.sisyphus/evidence/`) — ephemeral QA artifacts

## Success Criteria

- [ ] `TradeSimulator` manages single position (0 or 1) with enter_long, enter_short, close
- [ ] `TradeSimulator.is_flat`, `.is_long`, `.is_short` properties work correctly
- [ ] Implicit close on re-enter prevents orphan positions
- [ ] `Trade` dataclass captures all 8 fields with correct types
- [ ] `to_dataframe()` produces correct trade log with 8 columns
- [ ] `equity_curve()` produces cumulative PnL Series indexed by exit_index
- [ ] `StrategyCallback` Protocol expanded with `simulator` parameter
- [ ] `NoopStrategy` updated to accept new Protocol signature
- [ ] `BacktestResult` has `trades` and `equity_curve` fields (default None)
- [ ] `BacktestHarness.__init__` accepts optional `strategy_callback` kwarg
- [ ] `BacktestHarness.run()` executes three-phase flow: engine → batch → strategy
- [ ] Phase 3 calls strategy callback with full report row (all indicators)
- [ ] `compute_trade_metrics()` returns all 9 trade metrics correctly
- [ ] Trade metrics merged into `result.metrics` dict
- [ ] `BOSFlipStrategy` enters on BOS signals, flips between long/short
- [ ] `BOSFlipStrategy` uses close of signal bar as entry/exit price
- [ ] `strategies/__init__.py` + `strategies/bos_flip.py` importable
- [ ] **Zero changes to `smc.py`**
- [ ] No new third-party dependencies
- [ ] Existing NoopStrategy regression test passes
- [ ] Existing indicator metrics unchanged (batch_diff_score, swing counts, etc.)
- [ ] Integration test proves end-to-end pipeline with BOSFlipStrategy
- [ ] Trade metrics are internally consistent (wins + losses == total)
- [ ] `git diff -- smartmoneyconcepts/smc.py` is empty
- [ ] Success criterion runs without error:
  ```python
  from backtest import BacktestHarness, BacktestConfig
  from strategies.bos_flip import BOSFlipStrategy
  cfg = BacktestConfig()
  strategy = BOSFlipStrategy()
  harness = BacktestHarness(cfg, strategy_callback=strategy)
  result = harness.run("tests/test_data/EURUSD/EURUSD_15M.csv")
  result.trades       # ✅ Non-empty trade log
  result.equity_curve # ✅ Cumulative PnL Series
  result.metrics      # ✅ Includes all 9 trade metrics
  ```
