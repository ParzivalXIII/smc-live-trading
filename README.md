[![Version](https://img.shields.io/badge/version-0.1.0-blue?style=flat-square)](https://github.com/ParzivalXIII/smc-live-trading)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/ambv/black)
[![Tests](https://img.shields.io/badge/tests-400%2B-passing-green)]()
[![Status](https://img.shields.io/badge/status-causal-success)]()

*Forked from [joshyattridge/smartmoneyconcepts](https://github.com/joshyattridge/smart-money-concepts) — the upstream centered-window implementation. This fork replaces the swing engine with a causal streaming state machine and adds a complete live trading pipeline.*

<p align="center">
  <img src="https://github.com/joshyattridge/smart-money-concepts/blob/f0c0fc28cc290cdd9dfcc6a6ac246ed1d59061be/tests/test.gif" alt="Candle Graph Showing Indicators"/>
</p>

# Smart Money Concepts — Live Trading Engine

A complete, **causal** (zero look-ahead) implementation of Smart Money Concepts indicators with a streaming pipeline for live trading and honest backtesting.

**Key differentiator**: Every signal is confirmed using only data available up to that bar. No centered windows, no retrospective stamping, no look-ahead bias.

## Installation

```bash
# Requires Python 3.12
uv sync
# or
pip install -e .
```

## Data Fetching

Fetch OHLCV data from cryptocurrency exchanges via CCXT. Public market data does not require authentication.

```python
from trade_scripts import ExchangeFactory, fetch_ohlcv, process_candles, save_candles

# Single exchange instance (cached, rate-limited)
exchange = ExchangeFactory.create("bybit")
symbol = ExchangeFactory.resolve_symbol(exchange, "BTCUSDT")  # → "BTC/USDT"

# Paginated fetch
candles = fetch_ohlcv(exchange, symbol, "4h", limit=200, max_pages=5)

# Process (sort, dedup, clean) and save
df = process_candles(candles)
save_candles(df, "data/ohlcv_BTCUSDT_4h.csv")
```

```
data flow: exchange.py → fetcher.py → processor.py → storage.py → CSV
```

## TA Engine

Compute technical indicators (EMA, MACD, RSI, Bollinger Bands, MFI, OBV, ATR) on OHLCV data.

```python
from trade_scripts import compute_indicators, load_ta_latest

df = pd.read_csv("data/ohlcv_BTCUSDT_4h.csv", parse_dates=["timestamp"])
df = compute_indicators(df)  # Adds 17 indicator columns

# Latest values for live use
latest = load_ta_latest("BTCUSDT", "4h")
# → {"mfi14": ..., "obv": ..., "close": ..., "timestamp": ...}
```

Full test suite: 144 tests (94 unit + 26 I/O + 15 core + 9 system).

## Core SMC Indicators

All indicators are computed via a **causal streaming state machine** — no look-ahead bias, single-pass O(n), batch-streaming equivalence proven by 3-pass causality test.

### Swing Engine (`_SwingEngine`)

A streaming state machine that detects swing highs and lows bar-by-bar.

1. **Candidate discovery**: When price makes a new N-bar high/low, a mutable candidate is established
2. **Confirmation**: After `confirmation_bars` elapse AND price retraces by `atr_multiplier × ATR`, the swing is confirmed
3. **Alternation**: Confirmed swings strictly alternate (high → low → high → ...)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `swing_length` | 50 | Lookback bars for candidate discovery |
| `confirmation_bars` | 5 | Minimum bars before confirmation |
| `atr_multiplier` | 1.5 | ATR retracement threshold multiplier |
| `atr_period` | 14 | Period for internal ATR calculation |

```python
from smartmoneyconcepts.smc import smc

swings = smc.swing_highs_lows(df, swing_length=5, confirmation_bars=2,
                               atr_multiplier=1.5, atr_period=7)
```

Note: The first `max(swing_length, atr_period)` bars return NaN while internal buffers fill.

### Structure Engine (`smartmoneyconcepts/structures.py`)

A **streaming** BOS/CHOCH detector — no batch dependency, no retroactive information.

**Two-stage design:**
1. **Provisional**: When a 4-swing pattern completes, a provisional event is emitted
2. **Confirmed/Cancelled**: Each subsequent bar checks if price breaks the swing level (confirmed) or the confirmation window expires (cancelled)

```python
from smartmoneyconcepts.structures import StructureEngine, SwingConfirmed

engine = StructureEngine(confirmation_window=10)
swing = SwingConfirmed(index=10, direction=1, level=1.05, timestamp=...)
events = engine.update(swing)  # provisional BOS/CHOCH

status_changes = engine.check_confirmations(index=15, high=1.06, low=1.04)
```

Match rate vs batch: **94.0%** (513/546 BOS events matched on EURUSD 15M). The 6% gap is structurally unavoidable (breaks before the 4th swing confirms).

### All Indicators

| Function | Returns |
|----------|---------|
| `smc.fvg(ohlc)` | FVG, Top, Bottom, MitigatedIndex |
| `smc.swing_highs_lows(ohlc, ...)` | HighLow, Level |
| `smc.bos_choch(ohlc, swings)` | BOS, CHOCH, Level, BrokenIndex |
| `smc.ob(ohlc, swings)` | OB, Top, Bottom, OBVolume, MitigatedIndex, Percentage |
| `smc.liquidity(ohlc, swings)` | Liquidity, Level, End, Swept |
| `smc.previous_high_low(ohlc, tf)` | PreviousHigh, PreviousLow, BrokenHigh, BrokenLow |
| `smc.sessions(ohlc, session)` | Active, High, Low |
| `smc.retracements(ohlc, swings)` | Direction, CurrentRetracement%, DeepestRetracement% |

## Market Snapshot

Build a unified state object from TA data + SMC report. Pure state — no calculations, no opinions.

```python
from market_snapshot import MarketSnapshot, SnapshotBuilder

builder = SnapshotBuilder()
snapshot = builder.build("BTCUSDT", "4h", ta_row, smc_report)
# → MarketSnapshot with 24 fields: close, ema21, rsi14, macd,
#   atr14, bb_width, last_swing, last_bos, liquidity, OBs, etc.
```

## Confluence Scoring

Score the market snapshot for bias, direction strength, and confidence.

```python
from confluence import ConfluenceScorer, MarketContext

result = ConfluenceScorer().score(snapshot)
# → ConfluenceResult(bias="bullish", direction_score=8.0, confidence=0.7,
#                     max_score=10, reasons=[...])
```

**Multi-timeframe**: Hierarchical model where HTF (daily) sets regime and LTFs modify confidence multiplicatively — they cannot flip the bias.

```python
ctx = MarketContext(daily=daily_snap, h4=h4_snap, h1=h1_snap)
result = ctx.composite_score()  # HTF regime lock
ctx.alignment()  # "aligned" / "mixed"
ctx.regime_alignment  # detects conflicting timeframes
```

Alignment factors: aligned=1.0, neutral LTF=0.7, conflicting=0.4.
Score range: -4 to 10. Bias mapping: <0/0-3 bearish, 4-6 neutral, 7-10 bullish.

## Narrative Generation

Convert scores into human-readable explanations.

```python
from narrative import MarketNarrativeBuilder

narrative = MarketNarrativeBuilder().build(snapshot, result)
for section in narrative.sections:
    print(f"{section.title}:")
    for bullet in section.bullets:
        print(f"  • {bullet}")
print(narrative.conclusion)
# → "Bullish continuation favored while EMA21 remains intact.
#    Liquidity target at 108900."
```

## Decision Engine

Decision support (not trading). Maps the pipeline output to actionable signals.

```python
from decision_engine import DecisionEngine

decision = DecisionEngine().decide(snapshot, result)
# → Decision(bias="bullish", confidence=0.7, action="look_for_longs",
#            invalidation=48000.0, target=52000.0, breakout_pending=False)
```

Actions: `look_for_longs`, `avoid_shorts`, `stand_aside`. Derived from HTF bias + confidence threshold (>0.5 act, ≤0.5 stand aside). `breakout_pending` flag when confidence is 0.3–0.7.

Invalidation/target: **Liquidity-first** (SMC-aligned) with 3-level fallback chain (liquidity → swing level → EMA21).

## Journal

Append-only SQLite decision journal via `aiosqlite`. Records every decision cycle.

```python
from journal import JournalEntry, JournalWriter, make_run_id

entry = JournalEntry(
    run_id=make_run_id("BTCUSDT", "1d", snapshot.timestamp),
    timestamp=snapshot.timestamp, symbol="BTCUSDT", timeframe="1d",
    close=50000.0, direction_score=8.0, bias="bullish", confidence=0.7,
    narrative_summary="Bullish continuation favored.",
    decision_action="look_for_longs", events=[...],
)

async with JournalWriter("journal.db") as writer:
    await writer.append(entry)
    await writer.flush()

# Query back
rows = await writer.query_runs(symbol="BTCUSDT", limit=10)
```

Two-table schema: `journal_runs` (run_id, timestamp, symbol, score, bias, action, etc.) + `journal_events` (linked StructureEvent IDs).

## Live Orchestrator

State machine that wires the full pipeline. Pure orchestration — no business logic.

```
IDLE → LOAD → ANALYZE → DECIDE → JOURNAL → IDLE
                          ↕
                        ERROR (on exception)
```

```python
from orchestrator import LiveOrchestrator, OrchestratorContext, sync_write_entry
from live_smc_buffer import LiveSmcBuffer
from journal import JournalWriter

ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d")
buf = LiveSmcBuffer()
orch = LiveOrchestrator(ctx, smc_buffer=buf)

async with JournalWriter("journal.db") as writer:
    while True:
        try:
            orch.step()
            sync_write_entry(writer, orch.context.entry)
        except Exception:
            if orch.state == OrchestrationState.ERROR:
                orch.reset()
```

**LiveSmcBuffer** — streaming SMC accumulator wrapping `_SwingEngine` + `StructureEngine`. Runs batch OB/liquidity/retracements on swing confirmation (not every candle). Maintains a rolling 26-column report.

**Replay mode**: Set `mode="replay"` on context and pre-populate `ta_row` per candle.

```python
ctx = OrchestratorContext(symbol="BTCUSDT", timeframe="1d", mode="replay")
for i in range(len(data)):
    ctx.ta_row = data.iloc[i]
    orch.step()
```

## Backtesting

Three-phase replay harness using the exact same code path as live trading.

```
Phase 1 (Streaming):   _SwingEngine.update() bar-by-bar
Phase 2 (Batch):       bos_choch(), ob(), liquidity(), retracements()
Phase 3 (Strategy):    StrategyCallback.update() with TradeSimulator
```

```python
from backtest import BacktestConfig, BacktestHarness
from strategies.bos_flip import BOSFlipStrategy

cfg = BacktestConfig(swing_length=5, confirmation_bars=2)
harness = BacktestHarness(cfg, strategy_callback=BOSFlipStrategy())
result = harness.run("BTCUSDT_4H.csv")

result.trades         # DataFrame: side, entry/exit, pnl
result.equity_curve   # Cumulative realized PnL
result.metrics        # 20+ metrics: win_rate, profit_factor, expectancy, avg_trade_bars
```

```bash
python backtest.py --data BTCUSDT_4H.csv \
    --swing-length 5 --confirmation-bars 2 \
    --output-dir ./results --verbose
```

### Trade Simulator

```python
from trade_simulator import TradeSimulator

sim = TradeSimulator()
sim.enter_long(index=100, time=..., price=50000.0)
sim.close(index=150, time=..., price=51000.0)
```

### Custom Strategy

```python
class MyStrategy:
    def update(self, candle_index, row, engine_result, simulator=None, structure_events=None):
        for event in (structure_events or []):
            if event.event_type == "BOS" and event.status == "confirmed":
                if event.direction == 1 and simulator and simulator.is_flat:
                    simulator.enter_long(candle_index, row.name, row["Close"])
                elif event.direction == -1 and simulator and simulator.is_flat:
                    simulator.enter_short(candle_index, row.name, row["Close"])
```

## Cross-Market Validation

The engine has been validated across 5 datasets:

| Dataset | Rows | Swings | Trades | Win Rate | Profit Factor | Avg Hold (bars) |
|---------|------|--------|--------|----------|---------------|-----------------|
| BTCUSDT 4H | 19,376 | 1,802 | 186 | 62.9% | 2.78 | 102.8 |
| SOLUSDT 4H | 12,852 | 1,255 | — | — | — | — |
| ADAUSDT 4H | 17,925 | 1,713 | — | — | — | — |
| BNBUSDT 4H | 18,891 | 1,819 | — | — | — | — |
| EURUSD 15M | 24,424 | 2,415 | 267 | 60.7% | 1.88 | 93.6 |

*Full cross-market results for SOL, ADA, BNB available in `.sisyphus/evidence/bosflip-crossmarket/`.*

Streaming vs Batch match rate: **94.0%** on EURUSD 15M. Missed events have 47% smaller break distance and reverse within 10 bars — the gap acts as a quality filter.

## Project Structure

```
.
├── smartmoneyconcepts/
│   ├── smc.py                # Core indicators (Swing Engine, FVG, BOS, OB, etc.)
│   ├── structures.py          # Streaming StructureEngine (BOS/CHOCH)
│   └── __init__.py

├── trade_scripts/
│   ├── exchange.py            # CCXT ExchangeFactory (singleton, symbol resolution)
│   ├── fetcher.py             # Paginated OHLCV fetch
│   ├── processor.py           # Candle sort/dedup/clean + validation
│   ├── storage.py             # Atomic CSV write/load
│   ├── analyze_ta.py          # TA indicators (EMA, MACD, RSI, BB, MFI, OBV, ATR)
│   └── __init__.py

├── market_snapshot.py          # MarketSnapshot dataclass + SnapshotBuilder
├── confluence.py               # ConfluenceScorer + MarketContext (hierarchical MTF)
├── narrative.py                # MarketNarrativeBuilder
├── decision_engine.py          # Decision + DecisionEngine
├── journal.py                  # JournalEntry + JournalWriter (SQLite via aiosqlite)
├── orchestrator.py             # LiveOrchestrator state machine
├── live_smc_buffer.py          # LiveSmcBuffer streaming accumulator

├── backtest.py                 # 3-phase replay harness
├── trade_simulator.py          # V1 single-position trade simulator

├── strategies/
│   ├── __init__.py
│   └── bos_flip.py             # BOSFlipStrategy (example)

├── tests/
│   ├── unit_tests.py           # 16 SMC indicator tests
│   ├── test_causality.py       # 3-pass causality validation
│   ├── stream_compare.py       # Streaming vs batch diagnostic
│   ├── test_structure_engine.py # 19 StructureEngine tests
│   ├── test_market_snapshot.py  # 82 snapshot + confluence + MTF tests
│   ├── test_narrative.py        # 19 narrative tests
│   ├── test_decision_engine.py  # 33 decision engine tests
│   ├── test_journal.py          # 27 journal tests
│   ├── test_live_smc_buffer.py  # 13 LiveSmcBuffer tests
│   ├── test_orchestrator.py     # 18 orchestrator tests
│   ├── test_analyze_ta_units.py # 94 TA unit tests
│   ├── test_analyze_ta_io.py    # 26 TA I/O tests
│   ├── test_analyze_ta_core.py  # 15 TA core tests
│   ├── test_analyze_ta_system.py # 9 TA system tests
│   ├── test_ccxt_data.py        # 44 CCXT data tests
│   ├── test_ccxt_integration.py # 1 CCXT integration test
│   ├── conftest.py              # Shared fixtures
│   └── test_data/
│       ├── EURUSD/
│       └── cryptocurrencies/

├── pyproject.toml
├── uv.lock
└── .sisyphus/                   # Plans and evidence
```

## Contributing

Please feel free to contribute. By creating your own indicators, strategies, or improving the pipeline.

1. Fork it (https://github.com/ParzivalXIII/smc-live-trading/fork).
2. Study how it's implemented.
3. Create your feature branch (`git checkout -b my-new-feature`).
4. Commit your changes (`git commit -am 'Add some feature'`).
5. Push to the branch (`git push origin my-new-feature`).
6. Create a new Pull Request.

Less is more — each pull request should be minimal, focusing on a single function or a small feature.

## Hide Credit Message

```bash
export SMC_CREDIT=0
```

Hides the credit message when importing the smartmoneyconcepts library.

## Disclaimer

This project is for educational purposes only. Do not use this as a sole decision maker for your trades. Always use proper risk management and do your own research. The author is not responsible for any losses you may incur.
