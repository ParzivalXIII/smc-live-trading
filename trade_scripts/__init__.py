"""
trade_scripts — CCXT-based data fetching, processing, and storage layer.

Modules:
    exchange     : ExchangeFactory — singleton CCXT exchange factory.
    fetcher      : fetch_ohlcv — paginated OHLCV data fetch.
    processor    : process_candles, validate_candles — normalize & validate.
    storage      : save_candles, load_candles — CSV I/O for analyze_ta.py.
    data_manager : DataManager — incremental fetch + merge + save.
"""

from trade_scripts.exchange import ExchangeFactory
from trade_scripts.fetcher import fetch_ohlcv
from trade_scripts.processor import process_candles, validate_candles
from trade_scripts.storage import load_candles, save_candles
from trade_scripts.data_manager import DataManager

__all__ = [
    "ExchangeFactory",
    "fetch_ohlcv",
    "process_candles",
    "validate_candles",
    "save_candles",
    "load_candles",
    "DataManager",
]
