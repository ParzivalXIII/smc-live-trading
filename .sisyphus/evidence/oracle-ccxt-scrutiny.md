# Oracle Scrutiny: CCXT Data Fetching Layer

**Date:** 2026-06-24
**Scope:** `resolve_symbol()` heuristic vs `markets_by_id`, and incremental fetch tracker design

---

## Bottom Line

**Item 1 (Symbol Resolution) is a valid concern but lower severity than described.** The heuristic works correctly for all common cases (`BTCUSDT`, `ETH_USDT`, `SOL-USDT`) and the supposed failures (`1000PEPEUSDT`, `BTCUSD`, `DOGEUSDT`) all resolve correctly with the current implementation through Bybit's specific market structure. The real gap is **exchange-native IDs** (e.g., Kraken's `XXBTZUSD`, Coinbase's `BTC-USD`). CCXT's `exchange.market()` already handles this perfectly — the fix is ~10 lines and `Quick(<1h)`. **Do it now as a robustness improvement.**

**Item 2 (Incremental Fetch) is correctly deferred to V2.** The fetcher already accepts `since` — the missing piece is a persistence layer to track the last-fetched timestamp per (symbol, timeframe). The design is clean and straightforward. **Design now, implement later.**

---

## Item 1: Symbol Resolution — Detailed Validation

### Does `markets_by_id` exist in CCXT?

**Yes.** It is a standard CCXT property, documented in the manual:

> *"The `markets_by_id` property is an associative array of arrays of markets indexed by exchange-specific identifiers."*

It is populated by `load_markets()` alongside `exchange.markets`. Since `ExchangeFactory.create()` always calls `exchange.load_markets()`, it is always available on any instance returned by the factory.

**Critical discovery:** CCXT's own `exchange.market(symbol)` method (source from `ccxt/base/exchange.py`) already implements the exact resolution order you want:

```python
def market(self, symbol):
    if symbol in self.markets:
        return self.markets[symbol]
    elif symbol in self.markets_by_id:
        markets = self.markets_by_id[symbol]
        # returns the first matching market (prefers defaultType)
        return markets[0]
    raise BadSymbol(f"{self.id} does not have market symbol {symbol}")
```

This means **CCXT already handles the `markets_by_id` lookup natively**. There is no need to manually access `markets_by_id` — just delegate to `exchange.market()`.

### Does the heuristic actually fail on test symbols?

I verified against the actual CCXT 4.x source and runtime behavior:

| Symbol | Heuristic Steps | Result | Correct? |
|--------|----------------|--------|----------|
| `BTCUSDT` | `USDT` → `BTC/USDT` ✓ | `BTC/USDT` | ✅ Correct |
| `ETH_USDT` | `_` → `ETH/USDT` ✓ | `ETH/USDT` | ✅ Correct |
| `SOL-USDT` | `-` → `SOL/USDT` ✓ | `SOL/USDT` | ✅ Correct |
| `1000PEPEUSDT` | `USDT` first → `1000PEPE/USDT` ✓ | `1000PEPE/USDT` | ✅ Correct by design (USDT is first in quote list) |
| `BTCUSDC` | `USDT`✗ → `USDC` → `BTC/USDC` | `BTC/USDC` | ✅ Correct if exchange has the pair |
| `BTCUSD` | `USDT`✗ → `USDC`✗ → `BUSD`✗ → `USD` → `BTC/USD` | `BTC/USD` | ⚠️ Correct if exchange has `BTC/USD`. If exchange only has `BTC/USDT`, raises ValueError. |
| `XXBTZUSD` (Kraken native ID) | Tries all quotes✗, separators✗, uppercase✗ | ValueError | ❌ Fails — `markets_by_id` would map it correctly |

**Conclusions about the heuristic:**

1. **The supposed `1000PEPEUSDT` fragility does not exist** — the deterministic `USDT`-first order ensures the correct resolution every time, as long as the user inputs include the correct quote currency.

2. **The `BTCUSD` → `BTC/USD` scenario works** — but it's arguably wrong if the user meant `BTC/USDT`. The heuristic **cannot** fix incorrect user input (the user typed `USD` not `USDT`), so this is not a design flaw.

3. **The critical failure mode is exchange-native IDs** like Kraken's `XXBTZUSD`, `XBT/EUR` (Coinbase's `BTC-EUR` via dashes), or any non-standard ID format. The heuristic has no logic to handle `XXBTZ` as a base currency.

4. **The heuristic fails silently in one subtle case:** if a user type `ETHBTC` (meaning `ETH/BTC`), the heuristic tries `USDT`✗, `USDC`✗, `BUSD`✗, `USD`✗, `BTC` → `ETH/BTC` ✓. This works. But `BNBBTC` → tries `USDT`✗, ..., `BTC` → `BNB/BTC` — but what if `BNB` is both a quote currency (checking for suffix) and there's also `BNB/BTC`? Actually, the algorithm checks for `BTC` at the end, so `BNBBTC` → split to `BNB/BTC`. This works.

### What is the correct resolution for `1000PEPEUSDT`?

`1000PEPE/USDT` — this is correct in both the heuristic and in `markets_by_id`. Bybit's market ID for this pair is `"1000PEPEUSDT"` and its unified symbol is `"1000PEPE/USDT"`. The `markets_by_id` lookup returns the exact market dict with `'symbol': '1000PEPE/USDT'`.

### Should `markets_by_id` be prioritized over heuristics?

**Yes — but via `exchange.market()` not direct access.** The cleanest resolution order is:

1. `exchange.market(symbol)` — handles both unified symbols and exchange-native IDs
2. Fallback: separator variations (`_`, `-`)
3. `.upper()` retry
4. `ValueError`

`exchange.market()` already does `markets` → `markets_by_id` lookup. Only if that fails do we try heuristic separator replacement. This avoids duplicating CCXT's own logic.

### Recommended fix: Use `exchange.market()` as primary path

```python
@classmethod
def resolve_symbol(cls, exchange, symbol: str) -> str:
    # Fast path: CCXT's market() handles both unified symbols
    # and exchange-native IDs via markets_by_id internally.
    try:
        return exchange.market(symbol)['symbol']
    except ccxt.BadSymbol:
        pass

    # Fallback: try common separator variations
    for sep in ["_", "-"]:
        if sep in symbol:
            candidate = symbol.replace(sep, "/")
            try:
                return exchange.market(candidate)['symbol']
            except ccxt.BadSymbol:
                continue

    # Last resort: uppercase retry
    upper_sym = symbol.upper()
    if upper_sym != symbol:
        try:
            return exchange.market(upper_sym)['symbol']
        except ccxt.BadSymbol:
            pass

    raise ValueError(
        f"Symbol '{symbol}' not found in exchange markets. "
        f"Available examples: {list(exchange.markets.keys())[:10]}"
    )
```

Key changes:
- **Removes the quote-currency heuristic entirely** — it's a fragile approximation that `exchange.market()` supersedes.
- **`exchange.market()` is the single source of truth** — it checks `markets` then `markets_by_id` with proper handling.
- **Keeps separator fallback** for `ETH_USDT` / `SOL-USDT` style inputs (CCXT doesn't handle those).
- **Keeps `.upper()` retry** for case-insensitive inputs.
- **Removes the `import re`** that was unused.

### Why not just `markets_by_id` directly?

The `exchange.market()` approach is cleaner because:
- CCXT's implementation handles edge cases (multiple markets per ID, defaultType preference)
- It's maintained by the CCXT team — any resolution logic changes propagate automatically
- Avoids the `hasattr` check and list-unpacking boilerplate

### Effort: `Quick(<1h)`

- ~10 lines changed in `exchange.py`
- Test mock updates: add `markets_by_id` to `make_mock_exchange()` (the current mock lacks it)
- 2 new test cases: exchange-native ID resolution and separator fallback

---

## Item 2: Incremental Fetch — Design Spec

### What's missing

The current `fetch_ohlcv()` accepts a `since` parameter but there is no persistence layer to remember what was last fetched. Each invocation must explicitly pass `since`, and overlapping fetches produce duplicate data (handled by `process_candles` dedup, but wastefully).

The user wants per-(symbol, timeframe) tracking:
```
BTCUSDT_1d  → last candle timestamp
BTCUSDT_4h  → last candle timestamp
ETHUSDT_1h  → last candle timestamp
```

### Where should `last_fetched_timestamp` live?

**SQLite table in a dedicated file.** This is the V2 design.

| Location | Pros | Cons |
|----------|------|------|
| SQLite (`fetch_tracker.db`) | Atomic updates, queryable, concurrent-safe, stdlib | Slightly heavier than JSON |
| Sidecar JSON per CSV | Simple, matches data layout | Not atomic, race conditions on concurrent writes |
| Embedded in CSV filename | Trivially simple | Brittle, hard to query, breaks rename |

**Recommendation: SQLite.**

- `sqlite3` is in Python stdlib — no new dependency.
- Single file `data/fetch_tracker.db` across all symbols/timeframes.
- Atomic `UPDATE` via SQL transactions.
- Easily queryable: `"give me all tracked symbols"` or `"when was BTCUSDT_4h last updated"`.

### Schema

```sql
CREATE TABLE IF NOT EXISTS fetch_tracker (
    symbol      TEXT NOT NULL,       -- unified CCXT symbol, e.g. "BTC/USDT"
    timeframe   TEXT NOT NULL,       -- e.g. "1h", "4h", "1d"
    last_ms     INTEGER NOT NULL,    -- last candle open timestamp in ms
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (symbol, timeframe)
);
```

- `symbol` is the unified symbol (`"BTC/USDT"`) not the raw input.
- `last_ms` is the **candle open timestamp** of the most recently fetched candle, in milliseconds. This is what CCXT's `since` parameter expects.
- `updated_at` is for debugging/auditing.

### New module: `trade_scripts/tracker.py`

A new module with a single class. Not integrated into `storage.py` (different responsibility).

```python
"""tracker.py — Persist last-fetched timestamps for incremental OHLCV fetching."""

import sqlite3
from pathlib import Path


class FetchTracker:
    """Tracks last-fetched candle timestamp per (symbol, timeframe).

    Uses a SQLite database for atomic updates and easy querying.
    """

    def __init__(self, db_path: str = "data/fetch_tracker.db"):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS fetch_tracker (
                symbol     TEXT NOT NULL,
                timeframe  TEXT NOT NULL,
                last_ms    INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (symbol, timeframe)
            )
        """)
        self._conn.commit()

    def get_last_ms(self, symbol: str, timeframe: str) -> int | None:
        """Return last fetched timestamp in ms, or None if never fetched."""
        row = self._conn.execute(
            "SELECT last_ms FROM fetch_tracker WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()
        return row[0] if row else None

    def set_last_ms(self, symbol: str, timeframe: str, last_ms: int) -> None:
        """Record the last fetched timestamp."""
        self._conn.execute(
            """INSERT INTO fetch_tracker (symbol, timeframe, last_ms)
               VALUES (?, ?, ?)
               ON CONFLICT(symbol, timeframe) DO UPDATE SET
                   last_ms=excluded.last_ms,
                   updated_at=datetime('now')""",
            (symbol, timeframe, last_ms),
        )
        self._conn.commit()

    def get_all(self) -> list[tuple[str, str, int]]:
        """Return all tracked (symbol, timeframe, last_ms) entries."""
        return self._conn.execute(
            "SELECT symbol, timeframe, last_ms FROM fetch_tracker ORDER BY updated_at DESC"
        ).fetchall()

    def close(self) -> None:
        self._conn.close()
```

### Orchestrator API

A convenience function in `tracker.py` (or a thin orchestration layer):

```python
def fetch_incremental(
    exchange,
    symbol: str,
    timeframe: str,
    data_dir: str = "data",
    limit: int = 200,
    max_pages: int = 10,
) -> pd.DataFrame:
    """Fetch new candles since last tracked timestamp, append to CSV, update tracker.

    Args:
        exchange: CCXT exchange instance.
        symbol: Unified CCXT symbol (e.g. "BTC/USDT").
        timeframe: CCXT timeframe (e.g. "1h", "4h", "1d").
        data_dir: Directory for tracker DB and CSV files.
        limit: Max candles per page.
        max_pages: Max pagination requests.

    Returns:
        DataFrame of newly fetched candles (may be empty).
    """
    tracker = FetchTracker(db_path=f"{data_dir}/fetch_tracker.db")
    since = tracker.get_last_ms(symbol, timeframe)

    raw = fetch_ohlcv(exchange, symbol, timeframe, since=since,
                      limit=limit, max_pages=max_pages)
    if not raw:
        return pd.DataFrame()

    df = process_candles(raw)
    csv_path = f"{data_dir}/ohlcv_{symbol.replace('/', '')}_{timeframe}.csv"

    # Merge with existing data
    existing = load_candles(csv_path)
    if existing is not None:
        df = pd.concat([existing, df], ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp"], keep="last")
        df = df.sort_values("timestamp").reset_index(drop=True)

    save_candles(df, csv_path)

    # Update tracker with last NEW candle timestamp
    last_new = raw[-1][0]  # raw[-1] is newest candle
    tracker.set_last_ms(symbol, timeframe, last_new)
    tracker.close()

    return df
```

Key design decisions:
- `since` is the **open time** of the last candle (inclusive). CCXT's `since` parameter includes candles with `timestamp >= since`. The first page will overlap by 1 candle, which is handled by `drop_duplicates`.
- `last_new` is `raw[-1][0]` (the newest candle from this fetch), not the merged DataFrame's last timestamp. This prevents the tracker advancing past what was actually received.
- Dedup is done on the **merged** DataFrame, so repeated runs are idempotent.

### V2 Status: Confirmed Deferred

This is not a V1 blocker because:

1. **V1 already works.** Users can explicitly pass `since` and manage their own state.
2. **No data loss risk.** The fetcher + processor + storage pipeline is complete and correct without a tracker.
3. **Tracker is a convenience layer.** It adds persistence but changes nothing about the data pipeline.
4. **No breaking changes.** Adding `tracker.py` doesn't change any existing API.

**Do not implement now. Design is complete — ready for V2.**

---

## Action Plan: Item 1 Fix

Estimated effort: `Quick(<1h)`

1. **Add `markets_by_id` mock support** — In `test_ccxt_data.py`, update `make_mock_exchange()` to accept an optional `markets_by_id` dict. This ensures existing tests continue to pass while enabling new tests.

2. **Update `resolve_symbol()` in `exchange.py`** — Replace the quote-currency heuristic with `exchange.market(symbol)` as the primary resolution path. Keep separator fallback (`_`, `-`) and `.upper()` retry.

3. **Remove `import re`** — The unused regex import was a leftover from development. No longer needed.

4. **Add test cases** — Two new tests in `TestExchangeFactoryResolveSymbol`:
   - `test_resolve_via_markets_by_id` — Mock a `markets_by_id` entry and verify resolution works for exchange-native IDs.
   - `test_resolve_separator_fallback_after_market_fails` — Verify `ETH_USDT` still resolves via separator fallback when `market()` raises `BadSymbol`.

5. **Run full test suite** — `python -m pytest tests/test_ccxt_data.py -v` — confirm 45/45 (now 47/47) pass.

### Watch out for

- **`BadSymbol` exception** must be catchable without importing `ccxt` at module level. Use `except Exception` in the tight scope of `resolve_symbol`, or lazily import `ccxt` inside the method. The latter is cleaner: `from ccxt import BadSymbol; except BadSymbol:` inside the function.
- **`markets_by_id` values are lists** — `exchange.market()` already handles this, but if accessing `markets_by_id` directly, remember: `exchange.markets_by_id.get("BTCUSDT")` returns `[{"symbol": "BTC/USDT", ...}]`.
- **Mock coverage check** — The current mock has no `market()` method. If we call `exchange.market()` in the new code, the mock must have it. `MagicMock` auto-creates attributes on access (it returns a `MagicMock` for `market()`), which won't raise `BadSymbol`. We need to explicitly set `mock.market.side_effect = ccxt.BadSymbol(...)` for the fallback tests.

---

## Optional Future Considerations

1. **`exchange.market()` is now the single source of truth** — If CCXT adds more resolution logic in the future (e.g., handling more separator formats), `resolve_symbol()` automatically benefits without code changes. The quote-currency heuristic, once removed, eliminates a maintenance burden.

2. **The tracker design is ready for concurrent access** — SQLite handles multiple readers well. For write contention (multiple orchestrators fetching different symbols simultaneously), the `ON CONFLICT` upsert pattern avoids transaction conflicts. If single-process access is the only use case (likely), there is no contention concern at all.
