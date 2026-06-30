"""
data_manager.py — Incremental OHLCV data fetching, merging, and storage.

Composes ExchangeFactory, fetcher, processor, and storage into a single
update() call: determine file path → load existing → find last timestamp
→ fetch missing → merge → dedup → save atomically.
"""

from pathlib import Path
from typing import Optional

import pandas as pd

from trade_scripts.exchange import ExchangeFactory
from trade_scripts.fetcher import fetch_ohlcv
from trade_scripts.processor import process_candles
from trade_scripts.storage import load_candles, save_candles


class DataManager:
    """Manages incremental OHLCV data for a symbol/timeframe pair.

    Composes ExchangeFactory, fetch_ohlcv, process_candles, and
    save_candles into a single update() call.

    Args:
        exchange_id: CCXT exchange identifier (default "bybit").
        data_dir: Directory for CSV storage.
        config: Optional CCXT config (apiKey, secret, params.type, etc.).
    """

    def __init__(
        self,
        exchange_id: str = "bybit",
        data_dir: str = "data",
        config: Optional[dict] = None,
    ):
        self._exchange = ExchangeFactory.create(exchange_id, config)
        self._data_dir = Path(data_dir)

    def _csv_path(self, symbol: str, timeframe: str) -> Path:
        """Build the CSV file path for a resolved symbol and timeframe.

        Uses the resolved unified symbol (with "/" removed) to match
        the existing data/ohlcv_{SYMBOL}_{TF}.csv convention.
        """
        resolved = ExchangeFactory.resolve_symbol(self._exchange, symbol)
        safe = resolved.replace("/", "")
        return self._data_dir / f"ohlcv_{safe}_{timeframe}.csv"

    def update(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[int] = None,
        limit: int = 200,
        max_pages: int = 5,
    ) -> pd.DataFrame:
        """Fetch, merge, and save OHLCV data for a symbol/timeframe.

        Incremental: if a CSV already exists, only fetches candles after
        the last known timestamp. Merges new + existing, deduplicates,
        and saves atomically.

        Args:
            symbol: Raw symbol (e.g. "BTCUSDT", "BTC/USDT", "ETH_USDT").
            timeframe: CCXT unified timeframe (e.g. "4h", "1d").
            since: Optional start timestamp in ms. If None and existing
                   data exists, resumes from last known timestamp + 1ms.
            limit: Max candles per page.
            max_pages: Max pagination requests.

        Returns:
            Merged DataFrame with all candles (existing + new).

        Raises:
            ValueError: If symbol cannot be resolved.
            Exception: On exchange errors (propagated, never silent).
        """
        csv_path = self._csv_path(symbol, timeframe)

        # Load existing data (if any)
        existing: Optional[pd.DataFrame] = None
        if csv_path.exists():
            try:
                existing = load_candles(str(csv_path))
            except (pd.errors.EmptyDataError, pd.errors.ParserError, ValueError):
                csv_path.unlink(missing_ok=True)
                existing = None

        # Determine fetch start timestamp
        fetch_since = since
        if existing is not None and not existing.empty:
            last_ts = int(existing["timestamp"].iloc[-1].timestamp()) * 1000
            fetch_since = last_ts + 1  # Skip last candle (already have it)

        # Resolve symbol for exchange
        resolved = ExchangeFactory.resolve_symbol(self._exchange, symbol)

        # Fetch only missing candles
        raw = fetch_ohlcv(
            self._exchange,
            resolved,
            timeframe,
            since=fetch_since,
            limit=limit,
            max_pages=max_pages,
        )
        if not raw:
            return existing if existing is not None else pd.DataFrame()

        # Process new data
        new_df = process_candles(raw)

        # Merge with existing
        if existing is not None and not existing.empty:
            combined = pd.concat([existing, new_df], ignore_index=True)
            combined = combined.sort_values("timestamp").drop_duplicates(
                subset=["timestamp"], keep="last"
            ).reset_index(drop=True)
        else:
            combined = new_df

        # Save atomically
        save_candles(combined, str(csv_path))

        return combined
