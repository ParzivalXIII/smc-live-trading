# Draft: DataManager Work Plan

## Interview Summary
- **Intent**: Build from Scratch — new `DataManager` orchestration class
- **Module**: `trade_scripts/data_manager.py`
- **Composes**: ExchangeFactory, fetch_ohlcv, process_candles, save_candles/load_candles
- **V1 Scope**: No SQLite tracker, read last timestamp from CSV

## Key Decisions (from Oracle review)
1. ✅ CSV path uses **resolved** unified symbol (via `ExchangeFactory.resolve_symbol()`)
2. ✅ `csv_path.exists()` guard before `load_candles()` — no FileNotFoundError
3. ✅ Corrupted CSV: catch `EmptyDataError`/`ParserError`, unlink, re-fetch
4. ✅ Exchange errors propagate (no silent stale data return)
5. ✅ Merge: concat → sort → dedup(keep="last") → reset_index
6. ✅ Last timestamp: `int(df["timestamp"].iloc[-1].timestamp()) * 1000 + 1`
7. ✅ Existing CSV data always takes precedence over explicit `since`
8. ✅ Atomic save via existing `save_candles()` (tempfile + rename)

## Oracle Blocking Issues — All Resolved
| # | Issue | Fix |
|---|-------|-----|
| 1 | `load_candles()` raises FileNotFoundError | Guard with `csv_path.exists()` |
| 2 | CSV path uses raw symbol | Use `ExchangeFactory.resolve_symbol()` first |
| 3 | Corrupted CSV unhandled | Catch `EmptyDataError`/`ParserError`, unlink |
| 4 | Exchange error silent propagation | Let exceptions propagate (no try/except) |

## Scope
- **IN**: DataManager class, unit tests (mocked), integration test (real CSV)
- **OUT**: SQLite FetchTracker (V2), multi-timeframe batch update, rate limiting

## Test Strategy
- Unit tests with all 4 deps mocked (exchange, fetcher, processor, storage)
- 1 integration test with real temp CSV + mock exchange
- Coverage: first fetch, incremental fetch, corrupted CSV, empty exchange, symbol resolution variants
