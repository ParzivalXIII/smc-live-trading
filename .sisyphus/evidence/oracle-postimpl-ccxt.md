# Post-Implementation Review: CCXT Data Fetching Layer

**Reviewer**: Oracle (strategic technical advisor)  
**Date**: 2026-06-24  
**Scope**: `exchange.py`, `fetcher.py`, `processor.py`, `storage.py`, `__init__.py`, `tests/test_ccxt_data.py`, `tests/test_ccxt_integration.py`

---

## Bottom Line

The CCXT data layer is **well-implemented** and **substantially compliant** with the plan. All four modules are cleanly separated with proper lazy CCXT import, singleton caching, correct pagination, dedup/sort, and atomic CSV writes. Test coverage is strong (44 unit tests + 1 integration test). Two minor issues found: the gap detection in `validate_candles()` uses a hardcoded 2-hour threshold instead of computing from the actual timeframe, and a handful of trivial code-cleanliness nits. **Verdict: CONDITIONAL PASS** — resolve the gap threshold bug before closing.

---

## Plan Compliance

| Requirement | Status | Notes |
|---|---|---|
| **ExchangeFactory singleton caching** | ✅ | `_instances` dict, keyed by `exchange_id`, checks `if key not in cls._instances` before creating |
| **Symbol resolution: raw → unified** | ✅ | Quote-currency heuristic (`BTCUSDT` → `BTC/USDT`), separator replacement (`_`, `-`), uppercase fallback |
| **Symbol resolution: already unified** | ✅ | `if symbol in exchange.markets: return symbol` |
| **Symbol resolution: invalid → ValueError** | ✅ | Raises `ValueError` with helpful message + first 10 available symbols |
| **Pagination with correct since advancement** | ✅ | `current_since = candles[-1][0] + duration_ms`, break on `< limit` or empty |
| **Processor: sort, dedup (keep last), NaN/INF removal** | ✅ | All present in `process_candles()` |
| **validate_candles() function** | ✅ | Returns warning list (warnings-based, not exception-based — good design) |
| **Storage: atomic write (tempfile + rename)** | ✅ | `tempfile.mkstemp` + `shutil.move`, with cleanup on failure |
| **Storage: analyze_ta.py compatible format** | ✅ | Columns: `timestamp,open,high,low,close,volume`; ISO timestamps; no index |
| **CCXT is lazily imported** | ✅ | `import ccxt` inside `ExchangeFactory.create()` body |
| **ExchangeFactory.close_all() exists** | ✅ | Calls `.close()` on each instance, clears caches, swallows errors |
| **No private endpoint logic** | ✅ | No trading, balance, or order code |
| **No WebSocket/ccxt.pro** | ✅ | No imports or usage |
| **No async/await** | ✅ | All synchronous |
| **No modifications to analyze_ta.py** | ✅ | Not modified |
| **No auto-retry** | ✅ | No retry logic |

### Deviations from Plan

| Plan Said | Actual | Assessment |
|---|---|---|
| Symbol resolution: `symbol.replace("_", "/")`, `symbol.replace("-", "/")`, `symbol.upper()` | Quote-currency heuristic **added first** (try `endswith("USDT"/"USDC"/etc)`, then separators, then uppercase) | **Improvement** — more specific, fewer false positives |
| `validate_candles(df: pd.DataFrame) -> pd.DataFrame` (check/drop NaN/inf) | `validate_candles(df: pd.DataFrame) -> list` (returns warning list; NaN/inf handling is in `process_candles`) | **Improvement** — clearer separation of concerns |
| `normalize_timestamps()` as separate function | Normalization done inline in `process_candles()` | **Acceptable** — single-use logic doesn't warrant its own function |
| 4 separate test files (`test_ccxt_exchange.py`, `test_ccxt_fetcher.py`, `test_ccxt_processor.py`, `test_ccxt_storage.py`) | Single file `test_ccxt_data.py` with class-based grouping | **Acceptable** — functionally equivalent, pytest discovers all classes |
| Gap check: "> 2x expected interval" | Fixed `pd.Timedelta(hours=2)` | **Minor bug** — see Code Quality |

---

## Architecture Review

### Separation of Concerns ✅

| Module | Responsibility | Depends On |
|---|---|---|
| `exchange.py` | Exchange instance creation, caching, symbol resolution | `ccxt` (lazy) |
| `fetcher.py` | Paginated OHLCV fetch | Exchange instance (from factory) |
| `processor.py` | Normalize, sort, dedup, validate candles | `pandas`, `numpy` |
| `storage.py` | Atomic CSV read/write in analyze_ta.py format | `pandas`, `os`, `tempfile` |

### No Circular Imports ✅
- `exchange.py` imports only from stdlib + `ccxt` (lazy)
- `fetcher.py` imports only from stdlib
- `processor.py` imports only `numpy` + `pandas`
- `storage.py` imports only `os`, `shutil`, `tempfile`, `pathlib`, `pandas`
- `__init__.py` imports from all four, but none import from each other — no cycle possible

### CCXT Import is Lazy ✅
`import ccxt` appears inside `ExchangeFactory.create()` — not at module level. Confirmed in `exchange.py` line 32.

### Public Exports in `__init__.py` ✅
```
ExchangeFactory, fetch_ohlcv, process_candles, validate_candles, save_candles, load_candles
```
All 6 public symbols correctly exported. No internal-only symbols leaked.

### Architecture Verdict: **CLEAN**

---

## Code Quality

### Type Hints ✅
All 7 public functions have complete type hints on parameters and return types. Class methods on `ExchangeFactory` are fully annotated.

**Minor nits:**
- `ExchangeFactory.create()` return type is `"ccxt.Exchange"` (string forward reference) without `from __future__ import annotations` in the file. Works on Python 3.12+ but inconsistent with test files which do use `from __future__ import annotations`. Add `from __future__ import annotations` to `exchange.py` for consistency.
- `_instances: dict = {}` and `_markets_loaded: set = set()` lack type annotations. Could be `_instances: dict[str, ccxt.Exchange] = {}` and `_markets_loaded: set[str] = set()` (post-import, since `ccxt` is lazy).

### Docstrings ✅
All 7 public functions have docstrings with Args/Returns/Raises sections. Both class-level and method-level docstrings present on `ExchangeFactory`.

### Dead Code ✅
No commented-out code, no unused imports, no dead code in any module.

### Error Handling ⚠️

| Scenario | Handling | Verdict |
|---|---|---|
| Empty candle list | `fetch_ohlcv` breaks loop; `process_candles` returns empty DataFrame | ✅ |
| NaN in OHLCV | `process_candles` coerces to NaN via `pd.to_numeric(errors="coerce")`, then drops | ✅ |
| INF in OHLCV | Replaced with NaN via `df.replace([np.inf, -np.inf], np.nan)`, then dropped | ✅ |
| Symbol not found | `resolve_symbol` raises `ValueError` with available markets sample | ✅ |
| Exchange close failure | `close_all()` wraps in try/except, swallows error | ✅ |
| Atomic write failure | `save_candles()` cleans up temp file on exception, re-raises | ✅ |
| Network errors | Propagate `ccxt.NetworkError` up to caller (per plan — no retry in V1) | ✅ |
| **Gap threshold hardcoded** | `validate_candles()` uses `pd.Timedelta(hours=2)` regardless of timeframe | ❌ **Bug** |

### Bug: Gap Threshold Hardcoded

**File**: `processor.py`, line 86  
**Issue**: The gap detection compares against a fixed `pd.Timedelta(hours=2)` instead of computing `2 × expected_interval` from the candle timeframe. This will produce false warnings for:
- 4h data (expected gap 4h, threshold 2h → anything over 2h triggers)
- 1d data (expected gap 24h, threshold 2h → every gap triggers)
- Weekend gaps on crypto markets (typical 48-72h on some pairs → always triggers)

**Fix**: `validate_candles` needs a `timeframe` parameter to compute the expected interval:
```python
def validate_candles(df: pd.DataFrame, timeframe: str = "1h") -> list:
    expected_ms = exchange.parse_timeframe(timeframe) * 1000 * 2  # 2x expected
    if max_gap > pd.Timedelta(milliseconds=expected_ms):
        ...
```
Or, since `validate_candles` doesn't currently take a timeframe, the caller must pass it. Alternatively, infer from median gap: `df["timestamp"].diff().median() * 2`.

### Unnecessary `# noqa: F811` Comment

**File**: `exchange.py`, line 64  
`import re  # noqa: F811` — The `F811` (redefinition of unused) warning will never trigger for a local import inside a function body where `re` wasn't previously defined. This comment is misleading and should be removed or changed to `# noqa` (without code) if a linter complains about unused local import.

### Code Quality Verdict: **GOOD** (fix gap threshold before closing)

---

## Test Coverage

### Count: 44 Unit Tests + 1 Integration Test ✅

| Test Class | Tests | Coverage |
|---|---|---|
| `TestExchangeFactoryCreate` | 6 | Creation, singleton, different IDs, no-auth, with-auth, markets loaded |
| `TestExchangeFactoryResolveSymbol` | 6 | Unified, raw USDT, underscore, dash, invalid, empty markets |
| `TestExchangeFactoryCloseAll` | 3 | Cache cleared, close() called, error handling |
| `TestFetchSinglePage` | 3 | Returns candles, empty response, partial page |
| `TestFetchPagination` | 5 | Two pages, max_pages cap, empty second page, since advancement, params passthrough |
| `TestProcessCandles` | 9 | Empty, basic, sort, dedup, NaN removal, INF removal, timestamp dtype, numeric dtypes, value preservation |
| `TestValidateCandles` | 4 | Good data, empty, zero volume, no gap warning |
| `TestSaveCandles` | 7 | CSV format, roundtrip, directory creation, empty DF, atomic overwrite, timestamp format, parseability |
| `TestPipelineIntegration` | 1 | Full pipeline (mocked): resolve → fetch → process → save → load |
| **Integration** | 1 | Real Bybit fetch, 100 candles 4H, financial invariant checks |

### Test Quality

**Mocking approach**: Uses `unittest.mock.patch` and `MagicMock` — correct and consistent. Helper functions `make_mock_exchange()` and `make_candle()` reduce duplication.

**Key behaviors tested**:
- ✅ Singleton: `e1 is e2` — object identity, not equality
- ✅ Symbol resolution: `BTCUSDT` → `BTC/USDT`, `ETH_USDT` → `ETH/USDT`, `SOL-USDT` → `SOL/USDT`
- ✅ Pagination: `since` advancement verified by inspecting `call_args_list[1][0][2]`
- ✅ Dedup: keeps last occurrence (close=12.5 wins over close=11.5 for same timestamp)
- ✅ NaN removal: rows with NaN in any OHLCV column dropped
- ✅ INF removal: rows with INF in any OHLCV column dropped
- ✅ Atomic write: overwriting does not corrupt or leave partial files
- ✅ Integration: verifies `high >= low`, `high >= close`, `low <= close` invariants

### Coverage Gaps ⚠️

1. **Quote currency variants not fully tested** — Only `USDT` is tested for the quote heuristic. `USDC`, `BUSD`, `USD`, `BTC`, `ETH`, `BNB` quote currencies have no test coverage. Example: `ETH_BTC` → `ETH/BTC` is not tested.

2. **`since=None` behavior not explicitly tested** — No test verifies that when `since=None`, the CCXT call receives `None` as the since parameter. The `test_pagination_passes_params` test verifies `params` forwarding but `since=None` is the default.

3. **No network error propagation test** — `ccxt.NetworkError` propagation is not tested. The plan says "Let `ccxt.NetworkError` propagate up" but there's no test ensuring unhandled exceptions bubble correctly.

4. **Defensive sort in fetcher not tested** — Fetcher sorts candles before advancing (`candles.sort(key=lambda x: x[0])`), but there's no test where the exchange returns unsorted candles.

5. **Gap threshold for non-1h timeframes not tested** — The gap check is hardcoded at 2h, but no test verifies behavior with 4h or 1d data. The existing `test_validate_no_gap_warning_for_normal` uses 1h data and passes only because the gap (1h) < 2h threshold.

6. **`params.type` / `params.subType` config not tested** — The plan says these exchange-specific params should be supported, but no test exercises them through the factory config path.

### Test Coverage Verdict: **STRONG** (5 minor gaps, none critical)

---

## Guardrails

| Guardrail | Status | Evidence |
|---|---|---|
| No API keys required for OHLCV | ✅ | `test_create_no_auth()` passes; integration test runs without keys |
| No private endpoint logic | ✅ | No trade/balance/order code in any module |
| No WebSocket/ccxt.pro | ✅ | No imports, no WebSocket code |
| CCXT cached per exchange ID | ✅ | `test_create_singleton_same_id()` verifies `e1 is e2` |
| No async/await | ✅ | All functions synchronous |
| No auto-retry | ✅ | No retry decorators or loops |
| No modifications to analyze_ta.py | ✅ | File unmodified |
| No snapshot enrichment | ✅ | No snapshot code present |
| API keys never in code | ✅ | Config dict passed at runtime, no hardcoded keys |
| No index column in CSV | ✅ | `test_save_csv_format()` checks `",," not in raw` (no empty fields) |

All guardrails **INTACT**.

---

## Effort Estimate Assessment

The plan estimated **Medium** effort. The implementation (308 lines of production code + 699 lines of test code) aligns with a medium-sized deliverable. No scope creep observed.

---

## Verdict

### **CONDITIONAL PASS**

**Resolve before closing:**
1. **Gap threshold bug** (`processor.py:86`) — Replace hardcoded `pd.Timedelta(hours=2)` with a timeframe-aware computation (median-gap-based or passed as parameter). This causes false warnings on non-1h data.

**Optional improvements (defer if time-constrained):**
2. Add `from __future__ import annotations` to `exchange.py` for forward-reference consistency.
3. Add type annotations to `_instances` and `_markets_loaded` class vars.
4. Remove the misleading `# noqa: F811` comment on line 64 of `exchange.py`.
5. Add test coverage for additional quote currencies (`USDC`, `BUSD`, `ETH`, `BTC` as quote).

**Escalation trigger**: If a downstream consumer (`analyze_ta.py` or trading engine) starts receiving false gap warnings for 4h/1d data and mistaking them for real data quality issues, the gap threshold bug becomes a production blocker rather than a cosmetic issue.
