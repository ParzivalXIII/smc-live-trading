# Oracle README Review

**Date:** 2026-06-24
**Scope:** Full audit of `README.md` (315 lines) against current codebase state after the swing engine rebuild + 8 new modules + TA engine + CCXT data layer + test suite expansion.

---

## Bottom Line

The README is **structurally obsolete**. It documents only the core SMC engine (swing, structure, backtest, trade sim) and is missing **8 production modules**, an entire **7-module data pipeline**, and **16 new test files**. The test badge is off by an order of magnitude (39 → **400**), and the project structure tree is missing 15+ entries. A rewrite is required — not a patch.

**Effort estimate:** `Large(3d+)` for full rewrite; `Medium(1-2d)` if you keep the existing SMC engine docs and add new sections.

---

## 1. All Sections That Need Updating

### 1.1 Test Badge (line 3)

**Current:** `[![Tests](https://img.shields.io/badge/tests-39%2F39-passing-green)]()`

**Correct:** `[![Tests](https://img.shields.io/badge/tests-400%2F400-passing-green)]()`

Pytest collects **400 tests** across 16 test files. The old `39/39` was from 4 files (unit_tests, test_causality, test_structure_engine, test_streaming_vs_batch).

**Test file breakdown (pytest-collected = 400):**

| File | Tests |
|------|-------|
| `tests/unit_tests.py` | 16 |
| `tests/test_causality.py` | 3 |
| `tests/test_structure_engine.py` | 19 |
| `tests/test_streaming_vs_batch.py` | 1 |
| `tests/test_market_snapshot.py` | 82 |
| `tests/test_narrative.py` | 19 |
| `tests/test_decision_engine.py` | 33 |
| `tests/test_journal.py` | 27 |
| `tests/test_live_smc_buffer.py` | 13 |
| `tests/test_orchestrator.py` | 18 |
| `tests/test_analyze_ta_units.py` | 94 |
| `tests/test_analyze_ta_io.py` | 26 |
| `tests/test_analyze_ta_core.py` | 15 |
| `tests/test_analyze_ta_system.py` | 9 |
| `tests/test_ccxt_data.py` | 44 |
| `tests/test_ccxt_integration.py` | 1 |

### 1.2 Installation Section (lines 20–22)

**Current:** Only `pip install smartmoneyconcepts` — irrelevant (this project is not published on PyPI).

**Must add:**
```bash
# Local development
uv sync          # preferred — uses uv.lock
pip install -e . # alternative
poetry install   # if using poetry
```
The project uses `pyproject.toml` with pinned deps via `uv.lock`. Python 3.12 only (`requires-python = ">=3.12,<3.13"`).

### 1.3 Quick Start (lines 24–40)

The existing snippet shows only the upstream `smc` API. It should note that production workflows use `LiveSmcBuffer` + `SnapshotBuilder` + `DecisionEngine` etc. Keep the example as the "low-level API" but add a reference to the pipeline layer.

### 1.4 Project Structure (lines 267–290)

**Massively incomplete.** The current tree has 12 entries. The real tree has **30+ entries**. See Section 4 for the correct tree.

### 1.5 Contributing / Disclaimer

No structural changes needed, but the contributing section is generic boilerplate. Consider adding links to `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` (both exist in the repo root).

---

## 2. All Sections That Need Adding

### 2.1 Market Snapshot System (new section)

**Key types:** `MarketSnapshot` (dataclass, `market_snapshot.py`), `SnapshotBuilder`

MarketSnapshot is a pure-data record of market state at a single point in time:
- **Identity:** symbol, timeframe, timestamp, close
- **Trend:** trend_direction, ema21, ema21_slope
- **Momentum:** rsi14, mfi14, macd, macd_signal, macd_hist
- **Volatility:** atr14, bb_width
- **Structure:** last_swing_direction/level, last_bos_direction/index, last_choch_direction/index
- **Liquidity:** nearest_liquidity_above/below
- **OBs:** active_bullish_ob, active_bearish_ob

```python
from market_snapshot import SnapshotBuilder, MarketSnapshot

builder = SnapshotBuilder()
snapshot: MarketSnapshot = builder.build(
    symbol="BTC/USDT", timeframe="1d",
    ta_row=ta_df.iloc[-1], smc_report=report_df,
)
```

### 2.2 Confluence Scoring (new section)

**Key types:** `ConfluenceResult`, `ConfluenceScorer` (`confluence.py`), `MarketContext`

Additive scoring from a single snapshot (range -4 to 10):

| Condition | Score |
|-----------|-------|
| close > ema21 | +2 |
| ema21_slope > 0 | +1 |
| macd > macd_signal | +1 |
| rsi14 > 55 | +1 |
| mfi14 > 50 | +1 |
| last_bos_direction == 1 | +3 |
| last_bos_direction == -1 | -3 |
| nearest_liquidity_above exists | +1 |
| nearest_liquidity_below exists | -1 |

**Hierarchical MTF** via `MarketContext` (daily, H4, H1):
- HTF (highest active) sets **immutable bias**
- LTFs degrade confidence **multiplicatively** (aligned=1.0, neutral=0.7, conflict=0.4)

```python
from confluence import ConfluenceScorer, MarketContext

scorer = ConfluenceScorer()
result = scorer.score(snapshot)
# result.bias, result.direction_score, result.confidence, result.reasons

context = MarketContext(daily=daily_snap, h4=h4_snap, h1=h1_snap)
composite = context.composite_score()  # hierarchical MTF
```

### 2.3 Decision Engine (new section)

**Key types:** `Decision`, `DecisionEngine` (`decision_engine.py`)

Maps snapshot + confluence to actionable decisions:

```python
from decision_engine import DecisionEngine

engine = DecisionEngine()
decision = engine.decide(snapshot, result)
# decision.action → "look_for_longs" / "avoid_shorts" / "stand_aside"
# decision.invalidation — liquidity-first fallback chain
# decision.target — nearest liquidity in bias direction
# decision.breakout_pending — set when confidence 0.3–0.7 near swing level
```

Action gating:
- Bias=bullish + confidence>0.5 → `look_for_longs`
- Bias=bearish + confidence>0.5 → `avoid_shorts`
- Bias=neutral or confidence≤0.5 → `stand_aside`

### 2.4 Narrative Builder (new section)

**Key types:** `MarketNarrative`, `MarketNarrativeSection`, `MarketNarrativeBuilder` (`narrative.py`)

Converts snapshot + confluence into structured text sections: Trend, Momentum, Structure, Liquidity, Conclusion.

```python
from narrative import MarketNarrativeBuilder

builder = MarketNarrativeBuilder()
narrative = builder.build(snapshot, result)
# narrative.bias, narrative.sections (list of NarrativeSection), narrative.conclusion
```

### 2.5 SQLite Journal (new section)

**Key types:** `JournalEntry`, `JournalWriter` (`journal.py`)

Async append-only decision journal via `aiosqlite`. Records every complete pipeline cycle:

```python
from journal import JournalWriter, JournalEntry, make_run_id

entry = JournalEntry(
    run_id=make_run_id("BTC/USDT", "1d", timestamp),
    symbol="BTC/USDT", timeframe="1d", close=65000.0,
    direction_score=7, bias="bullish", confidence=0.85,
    narrative_summary="Bullish continuation...",
    decision_action="look_for_longs", decision_invalidation=64000.0,
    decision_target=68000.0, breakout_pending=False,
    events=[...],
)

async with JournalWriter("journal.db", buffer_size=100) as writer:
    await writer.append(entry)
    # Auto-flushes when buffer reaches 100 entries
```

Schema: `journal_runs` (run-level) + `journal_events` (event-level, FK to runs).

### 2.6 Live SMC Buffer (new section)

**Key types:** `LiveSmcBuffer` (`live_smc_buffer.py`)

Streaming SMC accumulator wrapping `_SwingEngine` + `StructureEngine`. Runs batch OB/liquidity/retracements **only on new swing confirmation** (not every candle). Maintains a rolling 26-column report for `SnapshotBuilder`.

```python
from live_smc_buffer import LiveSmcBuffer

buffer = LiveSmcBuffer(swing_length=5, confirmation_bars=2)
result = buffer.update(ta_row)
# result["HighLow"], result["Level"]

report = buffer.get_smc_report()  # rolling 26-col DataFrame
events = buffer.events            # StructureEvent list for journal
```

### 2.7 Live Orchestrator (new section)

**Key types:** `LiveOrchestrator`, `OrchestratorContext`, `OrchestrationState` (`orchestrator.py`)

State machine that wires the full pipeline. States: `IDLE → LOAD → ANALYZE → DECIDE → JOURNAL → IDLE`.

```python
from orchestrator import LiveOrchestrator, OrchestratorContext

ctx = OrchestratorContext(
    symbol="BTC/USDT", timeframe="1d",
    data_dir="data", db_path="journal.db",
)
orchestrator = LiveOrchestrator(ctx)

# One full cycle:
orchestrator.step()
# ctx.snapshot, ctx.confluence, ctx.narrative, ctx.decision, ctx.entry all populated

# On error:
orchestrator.reset()  # clears context, returns to IDLE
```

### 2.8 TA Engine (new section)

**Key types:** `compute_indicators`, `load_ta_latest`, `load_ta_series` (`trade_scripts/analyze_ta.py`)

Indicators computed via `pandas-ta`:
- **EMA-21**, **MACD** (12/26/9), **RSI-14**, **BB** (20, 2σ), **MFI-14**, **OBV**, **EBSW**, **ATR-14**
- Derived: MACD cross, OBV slope, EMA21 slope, price vs BB, BB width

```python
from trade_scripts.analyze_ta import compute_indicators, load_ta_series

df = load_ta_series("BTC/USDT", "1d", tail=100)
enriched = compute_indicators(df)
# Returns df with 23+ indicator columns
```

Also provides CLI entry point:
```bash
uv run python trade_scripts/analyze_ta.py BTC/USDT --timeframe 4h
```

### 2.9 CCXT Data Pipeline (new section)

**Modules in `trade_scripts/`:**

| Module | Key exports | Purpose |
|--------|-------------|---------|
| `exchange.py` | `ExchangeFactory` | Singleton CCXT exchange factory |
| `fetcher.py` | `fetch_ohlcv` | Paginated OHLCV fetch |
| `processor.py` | `process_candles`, `validate_candles` | Normalize, sort, deduplicate |
| `storage.py` | `save_candles`, `load_candles` | Atomic CSV I/O |
| `__init__.py` | Re-exports all above | Namespace package |

```python
from trade_scripts import ExchangeFactory, fetch_ohlcv, process_candles, save_candles

exchange = ExchangeFactory.create("bybit")
symbol = ExchangeFactory.resolve_symbol(exchange, "BTCUSDT")
candles = fetch_ohlcv(exchange, symbol, timeframe="1d", limit=200)
df = process_candles(candles)
warnings = validate_candles(df, expected_interval=pd.Timedelta(days=1))
save_candles(df, "data/ohlcv_BTCUSDT_1d.csv")
```

### 2.10 Alert Layer (planned — optional future consideration)

No alert code exists yet. If there are references to an alert layer or notification system in the roadmap, mention it as "planned" rather than a missing section. Based on my review, there is no alert code in the current codebase.

---

## 3. Correct Test Count

**Pytest-collected total: 400 tests** (not 39/39).

The badge must be updated to `400/400`.

Also note: there is 1 integration test marked (`test_ccxt_integration`) that may be skipped in CI without network access. Consider whether the badge should show `399/399` (unit-only) or `400/400` (all). Recommend showing `400/400` with a note that integration tests require network.

---

## 4. Correct Project Structure Tree

```
smartmoneyconcepts/
├── __init__.py
├── smc.py                  # Core indicators (Swing Engine, FVG, BOS, OB, etc.)
└── structures.py           # Streaming StructureEngine (BOS/CHOCH)

# ── Pipeline modules ──
narrative.py                # MarketNarrative, MarketNarrativeBuilder
confluence.py               # ConfluenceScorer, ConfluenceResult, MarketContext
decision_engine.py          # Decision, DecisionEngine
market_snapshot.py          # MarketSnapshot, SnapshotBuilder
journal.py                  # JournalEntry, JournalWriter (SQLite)
orchestrator.py             # LiveOrchestrator, OrchestratorContext (state machine)
live_smc_buffer.py          # LiveSmcBuffer (streaming SMC accumulator)

# ── Backtesting ──
backtest.py                 # Replay harness (3-phase: stream → batch → strategy)
trade_simulator.py          # V1 single-position trade simulator

# ── CCXT Data Pipeline ──
trade_scripts/
├── __init__.py             # Re-exports: ExchangeFactory, fetch_ohlcv, etc.
├── exchange.py             # ExchangeFactory (singleton CCXT factory)
├── fetcher.py              # fetch_ohlcv (paginated fetch)
├── processor.py            # process_candles, validate_candles
├── storage.py              # save_candles, load_candles (atomic CSV I/O)
└── analyze_ta.py           # compute_indicators, load_ta_series, load_ta_latest (CLI)

# ── Strategies ──
strategies/
├── __init__.py
└── bos_flip.py             # BOSFlipStrategy (example)

# ── Scripts ──
scripts/
└── run_bosflip_crossmarket.py

# ── Data ──
data/
├── ohlcv_BTCUSDT_1d.csv
└── ohlcv_BTCUSDT_1d_ta.csv

# ── Tests ──
tests/
├── conftest.py
├── unit_tests.py                       # 16 SMC indicator tests
├── test_causality.py                   # 3-pass causality validation
├── stream_compare.py                   # Streaming vs batch diagnostic
├── test_structure_engine.py            # 19 StructureEngine unit tests
├── test_streaming_vs_batch.py          # Streaming vs batch integration
├── test_market_snapshot.py             # 82 snapshot builder tests
├── test_narrative.py                   # 19 narrative builder tests
├── test_decision_engine.py             # 33 decision engine tests
├── test_journal.py                     # 27 journal writer tests
├── test_live_smc_buffer.py             # 13 streaming buffer tests
├── test_orchestrator.py                # 18 orchestrator pipeline tests
├── test_analyze_ta_units.py            # 94 TA indicator unit tests
├── test_analyze_ta_io.py               # 26 TA I/O tests
├── test_analyze_ta_core.py             # 15 TA core computation tests
├── test_analyze_ta_system.py           # 9 TA system-level tests
├── test_ccxt_data.py                   # 44 CCXT data pipeline tests
├── test_ccxt_integration.py            # 1 integration test (requires network)
├── generate_gif.py
├── test.gif
└── test_data/
    ├── EURUSD/
    └── cryptocurrencies/

# ── Config / Meta ──
pyproject.toml              # Dependencies, pytest config
uv.lock                     # Lockfile (uv)
.python-version             # Python version pin
setup.py                    # Legacy setup (if present)
```

---

## 5. Updated Section Ordering Recommendation

The current README sections are:
1. Badges + Fork notice
2. Title + description
3. Installation
4. Quick Start
5. Core Architecture (Swing Engine → Structure Engine → Backtest → Trade Sim → Strategy)
6. Indicators
7. Backtesting
8. Cross-Market Validation
9. Methodology
10. Project Structure
11. Contributing
12. Hide Credit Message
13. Disclaimer

**Recommended ordering** (add new sections, preserve existing SMC engine depth):

```
1. Badges + Fork notice                        ← UPDATE test badge
2. Title + description                         ← unchanged
3. Installation                                ← ADD uv/poetry methods
4. Quick Start                                 ← UPDATE to show pipeline, keep smc() as low-level
5. Core Architecture (HIGH-LEVEL OVERVIEW)
   5.1 Pipeline Overview (NEW)                 ← Mermaid/ASCII diagram: Buffer → Snapshot → Score → Narrate → Decide → Journal
   5.2 LiveSMC Buffer (NEW - sec 2.6)
   5.3 Market Snapshot (NEW - sec 2.1)
   5.4 Confluence Scoring (NEW - sec 2.2)
   5.5 Decision Engine (NEW - sec 2.3)
   5.6 Narrative Builder (NEW - sec 2.4)
   5.7 Journal (NEW - sec 2.5)
   5.8 Orchestrator (NEW - sec 2.7)
6. SMC Engine (DETAILED) — move current sec 5 here
   6.1 Swing Engine (preserve current)
   6.2 Structure Engine (preserve current)
7. Technical Indicators (preserve current sec 6)
8. TA Engine (NEW - sec 2.8)                  ← computed indicators, CLI usage
9. CCXT Data Pipeline (NEW - sec 2.9)         ← fetch → process → store
10. Backtesting (preserve current sec 7)
    10.1 Running a Backtest
    10.2 CLI
    10.3 Custom Strategy
    10.4 Trade Simulator (preserve current)
11. Cross-Market Validation (preserve current)
12. Methodology (preserve current)
13. Project Structure (UPDATE - sec 4)
14. Contributing (preserve current)
15. Hide Credit Message (preserve current)
16. Disclaimer (preserve current)
```

---

## 6. Outdated / Incorrect Content Summary

| Line(s) | Current | Problem | Fix |
|---------|---------|---------|-----|
| 3 | `tests-39/39-passing-green` | Wrong count | `tests-400/400-passing-green` |
| 20–22 | `pip install smartmoneyconcepts` | Not publishable; missing uv | Add `uv sync`, `pip install -e .` |
| 42–143 | Architecture section | Only covers swing/struc/backtest/trade sim | Add pipeline modules (sections 5.1–5.8 in recommended order) |
| 267–290 | Project structure tree | 15+ entries missing | Replace with tree from Section 4 above |
| All | No mention of `data/` dir | Data files ignored | Add to tree |
| All | No mention of `trade_scripts/` | Entire data pipeline invisible | Add as section + tree entry |
| All | No mention of pipeline modules | 8 modules completely absent | Add new sections |
| All | No mention of conftest.py | Test infrastructure invisible | Add to tree |
| All | No mention of uv.lock | Lockfile exists but unmentioned | Add to tree |

---

## 7. Watch Out For

1. **Test badge maintenance**: With 400 tests across 16 files, the badge will drift quickly. Consider adding a CI step that auto-updates the badge count (e.g., `pytest --co` → parse → badge URL).

2. **Two audiences**: The README serves both (a) users of the core SMC library and (b) developers of the live trading pipeline. Keep the SMC sections intact — they differentiate this project from upstream. The pipeline sections should be additive, not replacements.

3. **narrative.py imports from analyze_ta.py**: The `MarketNarrativeBuilder._build_momentum()` calls `rsi_label()` and `mfi_signal()` from `trade_scripts.analyze_ta`. The README should make this dependency clear — the narrative module is not standalone.

---

## 8. Optional Future Considerations

1. **Add a pipeline architecture diagram** (ASCII or Mermaid) showing data flow: `LiveSmcBuffer → SnapshotBuilder → ConfluenceScorer → MarketNarrativeBuilder → DecisionEngine → JournalWriter`
2. **Cross-reference system documentation**: Consider moving the detailed API docs into docstrings (already done) and keeping the README as a high-level tour plus links to module-level `__init__` re-exports.
