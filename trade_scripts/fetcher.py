"""
fetcher.py — OHLCV fetch with paginated loop.

Fetches paginated OHLCV data from a CCXT exchange, advancing the `since`
parameter by timeframe duration. Stops when fewer than `limit` candles
are returned or `max_pages` is reached.
"""

from typing import Optional


def fetch_ohlcv(
    exchange,
    symbol: str,
    timeframe: str = "1h",
    since: Optional[int] = None,
    limit: int = 200,
    max_pages: int = 10,
    params: Optional[dict] = None,
) -> list:
    """Fetch paginated OHLCV data from exchange.

    Advances ``since`` by last_candle_timestamp + timeframe_duration_ms.
    Stops when fewer than ``limit`` candles are returned (end of data).

    Args:
        exchange: CCXT exchange instance (from ExchangeFactory).
        symbol: Unified CCXT symbol (e.g. "BTC/USDT").
        timeframe: CCXT unified timeframe (e.g. "1h", "4h", "1d").
        since: Start timestamp in milliseconds (None = earliest available).
        limit: Max candles per page (exchange-dependent, Bybit max 200).
        max_pages: Max number of pagination requests.
        params: Additional CCXT params (e.g. {"type": "spot"}).

    Returns:
        List of CCXT OHLCV candles [[timestamp, O, H, L, C, V], ...].
    """
    duration_ms = exchange.parse_timeframe(timeframe) * 1000
    all_candles: list = []
    current_since = since
    params = params or {}

    for _page in range(max_pages):
        candles = exchange.fetch_ohlcv(symbol, timeframe, current_since, limit, params)
        if not candles:
            break
        # Sort by timestamp before advancing (defensive)
        candles.sort(key=lambda x: x[0])
        all_candles.extend(candles)
        if len(candles) < limit:
            break  # Last page — fewer candles than limit requested
        current_since = candles[-1][0] + duration_ms

    return all_candles
