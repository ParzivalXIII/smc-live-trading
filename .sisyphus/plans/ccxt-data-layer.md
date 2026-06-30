# CCXT Data Layer — OHLCV Fetching, Processing, and Storage

## TL;DR
> **Quick Summary**: Build a 4-module CCXT OHLCV data layer under `trade_scripts/` — exchange factory (singleton, auth-optional), paginated fetcher, deduplicating processor, and CSV writer — outputting CSVs compatible with the existing `analyze_ta.py` pipeline.
> 
> **Deliverables**: `exchange.py`, `fetcher.py`, `processor.py`, `storage.py`, unit tests, integration test
> 
> **Estimated Effort**: Medium
> 
> **Parallel Execution**: YES — 3 waves + 1 final verification wave
> 
> **Critical Path**: T1 (exchange factory) → T2 (fetcher) → T3/T4 (processor + storage) → T5/T6 (tests)

## Context

### Original Request
Create a CCXT-based data fetching, processing, and deduplication layer based on a 10-step design. The layer fetches OHLCV from Bybit (public, no auth required), normalizes symbols via exchange market loading, paginates across multiple requests, deduplicates/sorts candles, and writes CSVs in the format consumed by `analyze_ta.py`.

### Interview Summary

| Topic | Decision |
|-------|----------|
| **Module location** | `trade_scripts/` — existing package, close to `analyze_ta.py` consumer |
| **Sync vs Async** | Sync for V1. Existing pipeline is sync (`analyze_ta.py`). Async can be added later. |
| **Exchange default** | Bybit (but factory supports any CCXT exchange) |
| **CSV timestamp format** | ISO datetime string (`2024-01-01 00:00:00`) — matches existing `data/ohlcv_*.csv` files |
| **CCXT instance** | Singleton cached per exchange ID — prevents rate limiter reset & repeated `load_markets()` |
| **Output dir** | `data/` — matches `analyze_ta.py --data-dir data` default |
| **Bybit-specific params** | `params.type` + `params.subType` exposed via config for swap/inverse routing |
| **Retry logic** | V1: basic no-retry. Add python-resilience later if needed. |
| **File naming** | `ohlcv_{SYMBOL}_{TIMEFRAME}.csv` matching existing convention (e.g., `ohlcv_BTCUSDT_4h.csv`) |

### Metis Review

**Gaps identified & resolved:**
- ✅ **Edge case: `since=None`** — default to `exchange.parse8601('1y ago')` or similar sensible default
- ✅ **Edge case: empty response** — return empty DataFrame gracefully
- ✅ **Edge case: partial page** — fewer than `limit` candles signals end of data, break loop
- ✅ **Symbol resolution** — try `replace('_', '/')` and `replace('-', '/')` before raising
- ✅ **Rate limiting** — `enableRateLimit: True` enforced in factory default
- ✅ **Timeout** — 30s default timeout on fetch calls
- ✅ **API key handling** — loaded from env via `python-dotenv` (already a dependency), never hardcoded
- ✅ **Memory** — large fetches aren't expected (max ~1000 candles per call), no streaming needed

## Work Objectives

### Core Objective
Build a reusable, testable CCXT data layer that fetches OHLCV candles from Bybit (or any CCXT exchange), normalizes symbols, paginates through historical data, deduplicates/sorts results, and writes CSVs compatible with `analyze_ta.py`.

### Concrete Deliverables

| File | Purpose |
|------|---------|
| `trade_scripts/exchange.py` | CCXT exchange factory — singleton pattern, auth-optional, market loading, symbol resolution |
| `trade_scripts/fetcher.py` | OHLCV fetch with paginated loop, `since` advancement, stop-on-empty |
| `trade_scripts/processor.py` | Timestamp normalization, validation, sort, dedup → pandas DataFrame |
| `trade_scripts/storage.py` | CSV writer matching `analyze_ta.py` input format |
| `tests/test_ccxt_exchange.py` | Unit tests: factory, singleton, symbol resolution |
| `tests/test_ccxt_fetcher.py` | Unit tests: single page, pagination overlap, stop conditions |
| `tests/test_ccxt_processor.py` | Unit tests: sort, dedup, NaN handling, column types |
| `tests/test_ccxt_storage.py` | Unit tests: CSV roundtrip, format compliance |
| `tests/test_ccxt_integration.py` | Integration test: real Bybit fetch (BTCUSDT 4H, 100 candles) |

### Definition of Done
- All 4 modules pass unit tests with mocked CCXT
- Integration test fetches real Bybit public data and writes valid CSV
- Output CSV loads successfully via `analyze_ta.py`'s `load_csv()` function
- CCXT instance is reused across calls (singleton verified by test)
- No API keys required for OHLCV fetching

### Must Have
- ✅ Exchange factory with auth-optional constructor
- ✅ Singleton caching per exchange ID
- ✅ Symbol resolution via `exchange.markets`
- ✅ Paginated fetch loop with correct `since` advancement
- ✅ Dedup (keep last by timestamp) and sort
- ✅ CSV output with `timestamp, open, high, low, close, volume` — ISO datetime strings
- ✅ Unit tests mocking CCXT responses
- ✅ Integration test hitting real Bybit (public)

### Must NOT Have (Guardrails)
- ❌ No private endpoint logic (trading, balance, orders) in V1
- ❌ No WebSocket/ccxt.pro in V1 (REST only)
- ❌ No snapshot enrichment entangled with candle fetching
- ❌ No auto-retry with exponential backoff in V1 (add via python-resilience later)
- ❌ No async/await in V1
- ❌ No API keys stored in code (env vars only, using existing python-dotenv)
- ❌ No modification to `analyze_ta.py` — output must be forward-compatible

## Verification Strategy

### Test Decision: Unit tests (mocked) + Integration test (real API)

**Unit tests**: pytest with `unittest.mock.patch` or `pytest.monkeypatch` to mock `ccxt.bybit.fetch_ohlcv` responses.
**Integration test**: Live call to Bybit public API (no auth) for BTCUSDT 4H, 100 candles. Uses `@pytest.mark.integration` marker to allow skipping in CI.

### Agent QA Policy
All verification is agent-executed via:
1. **pytest runner** — `uv run pytest tests/test_ccxt_*.py -v` for unit tests
2. **Interactive bash** — run integration test, verify output CSV exists and is valid
3. **Python snippet** — load output CSV with `pd.read_csv(path, parse_dates=["timestamp"])` to validate format

## Execution Strategy

### Parallel Execution Waves

| Wave | Tasks | Parallelism |
|------|-------|-------------|
| **1** | T1 (exchange.py) | Single task — foundation for everything else |
| **2a** | T2 (fetcher.py) + T3 (processor.py) | Can be written in parallel — share exchange factory interface |
| **2b** | T4 (storage.py) | Independent — depends only on DataFrame format |
| **3** | T5 (unit tests) + T6 (integration test) | Depend on T1-T4 being done |
| **4** | F1-F4 (final verification) | Sequential |

### Dependency Matrix

| Task | Depends On |
|------|-----------|
| T1 — exchange.py | — |
| T2 — fetcher.py | T1 |
| T3 — processor.py | — (independent except DataFrame interface) |
| T4 — storage.py | T3 (needs processed DataFrame) |
| T5 — unit tests | T1, T2, T3, T4 |
| T6 — integration test | T1, T2, T3, T4 |

### Agent Dispatch Summary

| Agent | Tasks | Why |
|-------|-------|-----|
| **Python engineer (ccxt-skilled)** | T1, T2, T3, T4 | Core CCXT implementation, pandas processing |
| **Python test engineer** | T5, T6 | Mocking patterns, integration test with real API |
| **Oracle (deep)** | F1 | Plan compliance audit |
| **Unspecified (high)** | F2, F3 | Code quality review + real manual QA |
| **Deep** | F4 | Scope fidelity check |

---

## TODOs

### Wave 1 — Foundation

- [ ] T1. `trade_scripts/exchange.py` — CCXT exchange factory with singleton caching & symbol resolution

  **What to do**: Implement `ExchangeFactory` class with:
  - `create(exchange_id, config)` classmethod — singleton per exchange_id
    - Accept optional `config` dict with `apiKey`, `secret`, `params.type`, `params.subType`, etc.
    - Default config: `{'enableRateLimit': True}`
    - Instantiate via `getattr(ccxt, exchange_id)(config)`
    - Call `load_markets()` after creation
    - Cache in `_instances` dict keyed by exchange_id
    - Return cached instance on subsequent calls
  - `resolve_symbol(exchange, symbol)` classmethod — resolve raw symbol to CCXT unified format
    - Check if `symbol in exchange.markets` → return as-is
    - Try `symbol.replace("_", "/")` and `symbol.replace("-", "/")`
    - Try `symbol.upper()` variations (e.g., "btcusdt" → "BTC/USDT")
    - Raise `ValueError` with helpful message listing available symbols if not found
  - Lazy import: `import ccxt` inside the method, not at module top level

  **Module interface**:
  ```python
  exchange = ExchangeFactory.create("bybit")
  exchange = ExchangeFactory.create("bybit", config={"apiKey": ...})  # singleton
  symbol = ExchangeFactory.resolve_symbol(exchange, "BTCUSDT")  # "BTC/USDT"
  ```

  **Must NOT do**:
  - Don't export `ccxt` from the module
  - Don't create new instance on every call (singleton enforcement)
  - Don't hardcode exchange IDs or API keys

  **Recommended Agent Profile**: Python engineer with CCXT experience; skill: ccxt-python

  **Parallelization**: Wave 1, blocks T2, unblocks everything

  **References**:
  - [CCXT Manual — Exchange Instantiation](https://docs.ccxt.com/#/README?id=instantiation)
  - [CCXT Manual — Market Data](https://docs.ccxt.com/#/README?id=market-data)
  - `ccxt>=4.5.59` already in `pyproject.toml`
  - `python-dotenv` already in dependencies for env loading

  **Acceptance Criteria**:
  - [ ] `ExchangeFactory.create("bybit")` returns a `ccxt.bybit` instance
  - [ ] Second call with same exchange_id returns same object (id() or `is` check)
  - [ ] `exchange.load_markets()` has been called (verify `len(exchange.markets) > 0`)
  - [ ] `ExchangeFactory.create("bybit", config={})` with no apiKey still works
  - [ ] `resolve_symbol(exchange, "BTCUSDT")` returns `"BTC/USDT"`
  - [ ] `resolve_symbol(exchange, "BTC/USDT")` returns `"BTC/USDT"` unchanged
  - [ ] `resolve_symbol(exchange, "INVALID")` raises `ValueError`

  **QA Scenarios**:
  - **Tool**: `Bash` — `python -c "from trade_scripts.exchange import ExchangeFactory; e = ExchangeFactory.create('bybit'); print(type(e).__name__)"`
    - Expected: `bybit`
    - Evidence: `.sisyphus/evidence/task-1-factory-creation.txt`
  - **Tool**: `Bash` — identity test
    - Steps:
      1. `python -c "
  from trade_scripts.exchange import ExchangeFactory
  e1 = ExchangeFactory.create('bybit')
  e2 = ExchangeFactory.create('bybit')
  print(e1 is e2)
  "`
    - Expected: `True`
    - Evidence: `.sisyphus/evidence/task-1-singleton.txt`
  - **Tool**: `Bash` — symbol resolution
    - Steps:
      1. `python -c "
  from trade_scripts.exchange import ExchangeFactory
  e = ExchangeFactory.create('bybit')
  sym = ExchangeFactory.resolve_symbol(e, 'BTCUSDT')
  print(sym)
  "`
    - Expected: `BTC/USDT`
    - Evidence: `.sisyphus/evidence/task-1-symbol-resolution.txt`

---

### Wave 2a — Fetch & Process (parallel with Wave 2b)

- [ ] T2. `trade_scripts/fetcher.py` — OHLCV fetch with paginated loop

  **What to do**: Implement `fetch_ohlcv()` function:
  ```python
  def fetch_ohlcv(
      exchange,
      symbol: str,
      timeframe: str = "1h",
      since: int | None = None,
      limit: int = 200,
      max_pages: int = 10,
      params: dict | None = None,
  ) -> list[list]:
  ```

  **Behavior**:
  - Accept a CCXT exchange instance (already loaded with markets)
  - Resolve `params` dict for exchange-specific options (e.g., `{"type": "spot"}`)
  - Compute `duration_ms = exchange.parse_timeframe(timeframe) * 1000`
  - Pagination loop (up to `max_pages`):
    - Call `exchange.fetch_ohlcv(symbol, timeframe, current_since, limit, params or {})`
    - If result is empty → break
    - Extend `all_candles` with results
    - If `len(result) < limit` → break (end of available data)
    - Advance `current_since = candles[-1][0] + duration_ms`
  - Return raw list of CCXT candle tuples `[timestamp_ms, open, high, low, close, volume]`

  **Edge cases**:
  - `since=None`: Start from earliest available (CCXT default behavior — sends no `since` param)
  - Network error: Let `ccxt.NetworkError` propagate up (caller handles retries)
  - Single page: `max_pages=1` for quick fetches

  **Must NOT do**:
  - Don't modify the exchange instance (stateless)
  - Don't call `load_markets()` — assume already loaded
  - Don't do any processing — return raw CCXT tuples

  **Recommended Agent Profile**: Python engineer; skill: ccxt-python

  **Parallelization**: Wave 2a, blocked by T1, blocks T5

  **References**:
  - `exchange.parse_timeframe(timeframe)` — returns duration in seconds
  - `exchange.fetch_ohlcv(symbol, timeframe, since, limit, params)` — CCXT unified method
  - Bybit limit: 200 max per call

  **Acceptance Criteria**:
  - [ ] Returns list of lists `[[ts, o, h, l, c, v], ...]`
  - [ ] With `max_pages=1`, makes exactly 1 API call
  - [ ] Advances `since` correctly when `max_pages > 1`
  - [ ] Breaks when fewer than `limit` candles returned
  - [ ] Passes `params` through to CCXT

  **QA Scenarios**:
  - **Tool**: `Bash` — test with mocked exchange
    - Steps: Use python to create a mock exchange, call fetcher with controlled responses, verify pagination logic
    - This is covered in T5 unit tests. For now, verify import works:
    - `python -c "from trade_scripts.fetcher import fetch_ohlcv; print('OK')"`
    - Expected: `OK`
    - Evidence: `.sisyphus/evidence/task-2-import.txt`

- [ ] T3. `trade_scripts/processor.py` — Timestamp normalization, validation, sort, dedup

  **What to do**: Implement `process_candles()` and helper functions:
  ```python
  def process_candles(candles: list[list]) -> pd.DataFrame:
      """Convert raw CCXT candles to clean DataFrame: sort, dedup, validate."""

  def validate_candles(df: pd.DataFrame) -> pd.DataFrame:
      """Check for NaN/inf in OHLCV columns, drop invalid rows."""

  def normalize_timestamps(df: pd.DataFrame) -> pd.DataFrame:
      """Ensure timestamp column is datetime type, ms → ns conversion."""
  ```

  **Processing pipeline**:
  1. Create DataFrame from raw candles with columns `["timestamp", "open", "high", "low", "close", "volume"]`
  2. Convert `timestamp` from milliseconds to pandas datetime: `pd.to_datetime(df["timestamp"], unit="ms")`
  3. Sort by timestamp ascending
  4. Drop duplicate timestamps, keep last: `df.drop_duplicates(subset=["timestamp"], keep="last")`
  5. Reset index
  6. Validate: drop rows where any OHLCV value is NaN or inf
  7. Return DataFrame

  **Must NOT do**:
  - Don't modify the input list in-place
  - Don't convert timestamps to Unix seconds (keep as datetime — storage layer handles output format)
  - Don't add indicator columns (that's `analyze_ta.py`'s job)

  **Recommended Agent Profile**: Python engineer (pandas-skilled); skill: python-design-patterns

  **Parallelization**: Wave 2a (parallel with T2), unblocks T4, T5

  **References**:
  - `analyze_ta.py`'s `load_csv()` — reference for expected DataFrame format
  - `conftest.py` — uses `pd.Timestamp` and `pd.DatetimeIndex` throughout

  **Acceptance Criteria**:
  - [ ] Input `[[1000, 10, 11, 9, 10.5, 100], ...]` → DataFrame with proper columns
  - [ ] Timestamps are `pd.Timestamp` type (not int)
  - [ ] DataFrame is sorted ascending by timestamp
  - [ ] Duplicate timestamps are removed (keep last)
  - [ ] Rows with NaN in any OHLCV column are dropped
  - [ ] Returns empty DataFrame for empty input list

  **QA Scenarios**:
  - **Tool**: `Bash` — basic processing test
    - Steps: `python -c "
  from trade_scripts.processor import process_candles
  import pandas as pd
  candles = [[1000, 10, 11, 9, 10.5, 100], [2000, 11, 12, 10, 11.5, 200]]
  df = process_candles(candles)
  print(df.columns.tolist())
  print(df['timestamp'].dtype)
  print(len(df))
  "`
    - Expected: `['timestamp', 'open', 'high', 'low', 'close', 'volume']`, `datetime64[ns]`, `2`
    - Evidence: `.sisyphus/evidence/task-3-basic-processing.txt`
  - **Tool**: `Bash` — dedup test
    - Steps: `python -c "
  from trade_scripts.processor import process_candles
  candles = [[2000, 11, 12, 10, 11.5, 200], [1000, 10, 11, 9, 10.5, 100], [2000, 12, 13, 11, 12.5, 300]]
  df = process_candles(candles)
  print(len(df))
  print(df.iloc[1]['close'])
  "`
    - Expected: `2`, `12.5` (kept last for timestamp 2000)
    - Evidence: `.sisyphus/evidence/task-3-dedup.txt`

---

### Wave 2b — Storage (parallel with Wave 2a)

- [ ] T4. `trade_scripts/storage.py` — CSV writer matching `analyze_ta.py` input format

  **What to do**: Implement `save_candles()`:
  ```python
  def save_candles(df: pd.DataFrame, path: str | Path) -> Path:
      """Save processed candles to CSV in analyze_ta.py input format.

      Output columns: timestamp, open, high, low, close, volume
      Timestamp format: ISO datetime string (e.g., '2024-01-01 00:00:00')

      Returns the Path of the saved file.
      """
  ```

  **Format requirements** (verified against `data/ohlcv_BTCUSDT_1d.csv`):
  - Header: `timestamp,open,high,low,close,volume`
  - `timestamp`: ISO datetime string `YYYY-MM-DD HH:mm:ss` (not Unix seconds)
  - No index column
  - Float values with reasonable precision (no artificial truncation)
  - No trailing whitespace

  **Also implement**:
  ```python
  def load_candles(path: str | Path) -> pd.DataFrame:
      """Load CSV saved by save_candles() — roundtrip verification."""
  ```

  **Must NOT do**:
  - Don't modify the input DataFrame
  - Don't save index column
  - Don't save additional columns beyond OHLCV

  **Recommended Agent Profile**: Python engineer

  **Parallelization**: Wave 2b, blocked by T3, blocks T5, T6

  **References**:
  - `data/ohlcv_BTCUSDT_1d.csv` — existing file showing exact format
  - `analyze_ta.py`'s `load_csv()` — demonstrates `pd.read_csv(path, parse_dates=["timestamp"])`
  - `analyze_ta.py`'s `save_enriched_csv()` — writes CSV with `csv.DictWriter`, uses atomic rename pattern

  **Acceptance Criteria**:
  - [ ] Output CSV has exact columns: `timestamp, open, high, low, close, volume`
  - [ ] Timestamps are ISO format strings, parseable by `pd.read_csv(path, parse_dates=["timestamp"])`
  - [ ] No index column in CSV
  - [ ] `load_candles()` roundtrip returns DataFrame matching input
  - [ ] Created parent directory if it doesn't exist

  **QA Scenarios**:
  - **Tool**: `Bash` — format verification
    - Steps:
      1. `python -c "
  from trade_scripts.processor import process_candles
  from trade_scripts.storage import save_candles, load_candles
  import tempfile, pandas as pd

  candles = [[1000, 10, 11, 9, 10.5, 100], [2000, 11, 12, 10, 11.5, 200]]
  df = process_candles(candles)

  with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
      path = save_candles(df, f.name)

  # Verify roundtrip
  loaded = load_candles(path)
  print(loaded.columns.tolist())
  print(loaded['timestamp'].dtype)
  print(len(loaded))
  print(loaded.iloc[0]['timestamp'])
  "`
    - Expected: `['timestamp', 'open', 'high', 'low', 'close', 'volume']`, `datetime64[ns]`, `2`, `2024-01-01 00:00:00` (or the epoch+1s)
    - Evidence: `.sisyphus/evidence/task-4-csv-roundtrip.txt`
  - **Tool**: `Bash` — CSV file inspection
    - Steps: `head -3 /tmp/test_ohlcv.csv` (from previous step)
    - Expected: Header + 2 data rows with ISO timestamps
    - Evidence: `.sisyphus/evidence/task-4-csv-header.txt`

---

### Wave 3 — Testing

- [ ] T5. Unit tests — mocking CCXT responses

  **What to do**: Create `tests/test_ccxt_exchange.py`, `tests/test_ccxt_fetcher.py`, `tests/test_ccxt_processor.py`, `tests/test_ccxt_storage.py` with comprehensive unit tests.

  **Test modules and cases**:

  **`test_ccxt_exchange.py`**:
  - `test_create_returns_bybit_instance` — factory creates correct type
  - `test_create_singleton_same_id` — same exchange_id returns same object
  - `test_create_different_exchanges` — different IDs return different objects
  - `test_create_no_auth` — works without apiKey
  - `test_create_with_auth` — accepts apiKey in config
  - `test_resolve_symbol_raw` — "BTCUSDT" → "BTC/USDT"
  - `test_resolve_symbol_unified` — "BTC/USDT" unchanged
  - `test_resolve_symbol_invalid` — raises ValueError
  - `test_markets_loaded_after_create` — verify `load_markets()` was called

  **`test_ccxt_fetcher.py`**:
  - `test_fetch_single_page` — mock returns 200 candles, verify 1 call
  - `test_fetch_pagination_stop` — first call 200, second <200 (break)
  - `test_fetch_empty_response` — empty from exchange → empty result
  - `test_fetch_since_advancement` — verify `since` parameter advances correctly
  - `test_fetch_max_pages` — capped at max_pages even if more data available
  - `test_fetch_passes_params` — verify `params` dict forwarded to CCXT

  **`test_ccxt_processor.py`**:
  - `test_process_empty` — empty list → empty DataFrame
  - `test_process_basic` — basic candles → correct columns and types
  - `test_process_sort` — unsorted input → sorted output
  - `test_process_dedup` — duplicate timestamps → keep last
  - `test_process_nan_removal` — rows with NaN are dropped
  - `test_process_timestamp_dtype` — timestamps are `datetime64[ns]`

  **`test_ccxt_storage.py`**:
  - `test_save_csv_format` — verify CSV columns, no index
  - `test_save_and_load_roundtrip` — DataFrame → CSV → DataFrame matches
  - `test_save_creates_directory` — creates parent dir if missing
  - `test_save_empty_dataframe` — empty DataFrame → CSV with header only

  **Mocking approach**: Use `unittest.mock.patch` to mock `ccxt.bybit`:
  ```python
  @patch("ccxt.bybit")
  def test_fetch_single_page(mock_bybit):
      mock_exchange = MagicMock()
      mock_exchange.markets = {"BTC/USDT": {}}
      mock_exchange.fetch_ohlcv.return_value = [[t, 1, 2, 3, 4, 5] for t in range(1000, 1000 + 200 * 60000, 60000)]
      mock_exchange.parse_timeframe.return_value = 3600
      mock_bybit.return_value = mock_exchange

      result = fetch_ohlcv(mock_exchange, "BTC/USDT", "1h", limit=200)
      assert len(result) == 200
      mock_exchange.fetch_ohlcv.assert_called_once()
  ```

  **Must NOT do**:
  - Don't hit real API in unit tests (mock everything)
  - Don't depend on network availability

  **Recommended Agent Profile**: Python test engineer; skill: test-driven-development

  **Parallelization**: Wave 3, blocked by T1-T4

  **References**:
  - Existing tests use `pytest` with `conftest.py` fixtures
  - Test config in `pyproject.toml`: `testpaths = ["tests"]`, `python_files = ["test_*.py"]`
  - `unittest.mock` documentation for MagicMock patterns

  **Acceptance Criteria**:
  - [ ] All unit tests pass: `uv run pytest tests/test_ccxt_*.py -v`
  - [ ] At least 15 test cases across all modules
  - [ ] No real network calls in unit tests
  - [ ] Exchange singleton test verifies object identity
  - [ ] Pagination test verifies `since` advancement logic

  **QA Scenarios**:
  - **Tool**: `Bash` — run full unit test suite
    - Steps: `uv run pytest tests/test_ccxt_exchange.py tests/test_ccxt_fetcher.py tests/test_ccxt_processor.py tests/test_ccxt_storage.py -v --tb=short 2>&1`
    - Expected: All tests pass
    - Evidence: `.sisyphus/evidence/task-5-unit-tests.txt`

- [ ] T6. Integration test — real Bybit fetch

  **What to do**: Create `tests/test_ccxt_integration.py` with a test that:
  1. Creates a Bybit exchange via `ExchangeFactory.create("bybit")` (no auth)
  2. Resolves symbol `BTCUSDT` → `BTC/USDT`
  3. Fetches 100 candles of 4H timeframe via `fetch_ohlcv(exchange, "BTC/USDT", "4h", limit=100, max_pages=1)`
  4. Processes via `process_candles()`
  5. Saves to a temp file via `save_candles()`
  6. Verifies output format matches expected
  7. Loads with `load_candles()` and verifies data integrity

  **Test markers**: Use `@pytest.mark.integration` to allow skipping:
  ```python
  @pytest.mark.integration
  @pytest.mark.skipif(...)  # optional: skip in CI without network
  def test_fetch_real_bybit_ohlcv():
      ...
  ```

  **Verification checks**:
  - DataFrame has 100 rows (or less if exchange has less data)
  - All OHLCV values are positive finite numbers
  - Timestamps are properly sorted and unique
  - Close prices are within [High, Low] bounds for each row
  - Output CSV can be loaded by `analyze_ta.py`'s `load_csv()` function

  **Must NOT do**:
  - Don't hardcode expected prices (live data varies)
  - Don't require authentication
  - Don't modify any real files — use temp directory

  **Recommended Agent Profile**: Python test engineer

  **Parallelization**: Wave 3, blocked by T1-T4

  **Acceptance Criteria**:
  - [ ] Integration test can run against live Bybit API
  - [ ] Returns 100 valid candles
  - [ ] Output CSV matches `analyze_ta.py` expected format
  - [ ] Test is properly marked with `@pytest.mark.integration`

  **QA Scenarios**:
  - **Tool**: `Bash` — run integration test
    - Steps: `uv run pytest tests/test_ccxt_integration.py -v --tb=short -m integration 2>&1`
    - Expected: Test passes (may be skipped if no network)
    - Evidence: `.sisyphus/evidence/task-6-integration-test.txt`
  - **Tool**: `Bash` — verify output CSV loads in analyze_ta format
    - Steps: `python -c "
  import pandas as pd
  df = pd.read_csv('/tmp/test_output.csv', parse_dates=['timestamp'])
  print('Columns:', df.columns.tolist())
  print('Rows:', len(df))
  print('Timestamp dtype:', df['timestamp'].dtype)
  "` (using the path from the integration test)
    - Expected: Valid DataFrame with correct columns and datetime timestamps
    - Evidence: `.sisyphus/evidence/task-6-csv-format.txt`

---

### Wave 4 — Final Verification

- [ ] F1. Plan Compliance Audit (oracle)

  **What to do**: Audit the implementation against the 10-step design and this plan. Verify:
  - Singleton exchange instance (Step 1)
  - No auth required for OHLCV (Step 2)
  - `load_markets()` called during setup (Step 3)
  - Symbol resolution via exchange markets (Step 4)
  - Paginated fetch loop with correct `since` advancement (Step 5)
  - Dedup + sort after every batch (Step 6) — processor handles this
  - Four separate modules (Step 7)
  - No snapshot entanglement (Step 8)
  - Auth optional in factory (Step 9)
  - Test order preserved (Step 10)

  **Acceptance Criteria**: All 10 steps satisfied.

- [ ] F2. Code Quality Review (unspecified-high)

  **What to do**: Review all 4 modules for:
  - Consistent error handling (CCXT exceptions, ValueError for bad symbols)
  - Type hints on all public functions
  - Docstrings on all public functions
  - No hardcoded exchange-specific logic (except Bybit params in config)
  - Proper module imports (lazy ccxt import)

  **Acceptance Criteria**: All code quality checks pass.

- [ ] F3. Real Manual QA (unspecified-high + playwright if UI)

  **What to do**: Run end-to-end scenario:
  1. `python -c "from trade_scripts.exchange import ExchangeFactory; from trade_scripts.fetcher import fetch_ohlcv; from trade_scripts.processor import process_candles; from trade_scripts.storage import save_candles; e = ExchangeFactory.create('bybit'); raw = fetch_ohlcv(e, 'BTC/USDT', '4h', limit=100, max_pages=1); df = process_candles(raw); save_candles(df, '/tmp/e2e_test.csv'); print('E2E OK:', len(df), 'candles')"`
  2. Verify the CSV at `/tmp/e2e_test.csv` has correct format
  3. Verify it loads in `analyze_ta.py`'s `load_csv()` format

  **Acceptance Criteria**: E2E pipeline runs cleanly and produces valid output.

- [ ] F4. Scope Fidelity Check (deep)

  **What to do**: Verify guardrails are intact:
  - No private endpoint code in any module
  - No WebSocket/ccxt.pro imports or usage
  - No API keys required for public data
  - No modifications to `analyze_ta.py`
  - No snapshot enrichment code

  **Acceptance Criteria**: All guardrails confirmed.

---

## Commit Strategy

| After | Commit Message |
|-------|---------------|
| T1 | `feat(data-layer): add CCXT exchange factory with singleton caching` |
| T2 + T3 | `feat(data-layer): add OHLCV fetcher with pagination and candle processor` |
| T4 | `feat(data-layer): add CSV storage writer for analyze_ta.py compatibility` |
| T5 | `test(data-layer): add unit tests for exchange, fetcher, processor, storage` |
| T6 | `test(data-layer): add integration test for real Bybit OHLCV fetch` |
| F1-F4 | `chore(data-layer): final verification and compliance audit` |

## Success Criteria

1. All unit tests pass (no network required)
2. Integration test passes against live Bybit API
3. Output CSV matches `analyze_ta.py`'s expected format (`timestamp, open, high, low, close, volume` with ISO datetime strings)
4. E2E pipeline: `ExchangeFactory → fetch_ohlcv → process_candles → save_candles` runs with no errors
5. CCXT instance is reused (singleton verified)
6. No API keys required for public data fetch
7. Symbol resolution works for raw (`BTCUSDT`) and unified (`BTC/USDT`) formats
8. All guardrails confirmed intact
