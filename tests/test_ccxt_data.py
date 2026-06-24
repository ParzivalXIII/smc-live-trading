"""
Unit tests for trade_scripts CCXT data layer.

Tests cover:
    - ExchangeFactory: singleton creation, symbol resolution, cleanup
    - fetch_ohlcv: single page, pagination, edge cases
    - process_candles: sort, dedup, NaN handling, empty input
    - validate_candles: data quality warnings
    - save_candles / load_candles: CSV roundtrip, atomic write
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from trade_scripts.exchange import ExchangeFactory
from trade_scripts.fetcher import fetch_ohlcv
from trade_scripts.processor import process_candles, validate_candles
from trade_scripts.storage import load_candles, save_candles

# =============================================================================
# Helpers
# =============================================================================


def _is_datetime64(dtype) -> bool:
    """Check if dtype is any datetime64 resolution."""
    return np.issubdtype(dtype, np.datetime64)


def make_mock_exchange(markets: dict | None = None) -> MagicMock:
    """Create a mock CCXT exchange instance.

    Args:
        markets: Markets dict or None for default set.
                 Pass ``{}`` explicitly for empty markets.
    """
    mock = MagicMock()
    mock.markets = markets if markets is not None else {
        "BTC/USDT": {},
        "ETH/USDT": {},
        "SOL/USDT": {},
    }
    mock.load_markets.return_value = None
    mock.parse_timeframe.return_value = 3600  # 1h in seconds
    return mock


def make_candle(ts_ms: int, open_p: float = 100.0, high_p: float = 110.0,
                low_p: float = 90.0, close_p: float = 105.0,
                vol: float = 1000.0) -> list:
    """Create a single OHLCV candle tuple."""
    return [ts_ms, open_p, high_p, low_p, close_p, vol]


# =============================================================================
# ExchangeFactory Tests
# =============================================================================


class TestExchangeFactoryCreate:
    """Singleton creation behaviour."""

    def setup_method(self) -> None:
        """Clear the singleton cache before each test."""
        ExchangeFactory.close_all()

    @patch("ccxt.bybit")
    def test_create_returns_instance(self, mock_bybit_class):
        """Factory returns a CCXT exchange instance."""
        mock_instance = make_mock_exchange()
        mock_bybit_class.return_value = mock_instance

        exchange = ExchangeFactory.create("bybit")
        assert exchange is mock_instance
        mock_bybit_class.assert_called_once()

    @patch("ccxt.bybit")
    def test_create_singleton_same_id(self, mock_bybit_class):
        """Same exchange_id returns same object."""
        mock_instance = make_mock_exchange()
        mock_bybit_class.return_value = mock_instance

        e1 = ExchangeFactory.create("bybit")
        e2 = ExchangeFactory.create("bybit")
        assert e1 is e2
        assert e1 is mock_instance
        # Instantiated only once
        mock_bybit_class.assert_called_once()

    @patch("ccxt.bybit")
    @patch("ccxt.binance")
    def test_create_different_exchanges(self, mock_binance_class,
                                        mock_bybit_class):
        """Different IDs return different objects."""
        bybit_instance = make_mock_exchange()
        binance_instance = make_mock_exchange({"BTC/USDT": {}})
        mock_bybit_class.return_value = bybit_instance
        mock_binance_class.return_value = binance_instance

        e1 = ExchangeFactory.create("bybit")
        e2 = ExchangeFactory.create("binance")
        assert e1 is not e2
        assert e1 is bybit_instance
        assert e2 is binance_instance

    @patch("ccxt.bybit")
    def test_create_no_auth(self, mock_bybit_class):
        """Works without apiKey."""
        mock_instance = make_mock_exchange()
        mock_bybit_class.return_value = mock_instance

        exchange = ExchangeFactory.create("bybit")
        assert exchange is mock_instance

    @patch("ccxt.bybit")
    def test_create_with_auth(self, mock_bybit_class):
        """Accepts apiKey in config."""
        mock_instance = make_mock_exchange()
        mock_bybit_class.return_value = mock_instance

        config = {"apiKey": "test_key", "secret": "test_secret"}
        exchange = ExchangeFactory.create("bybit", config=config)
        assert exchange is mock_instance
        call_kwargs = mock_bybit_class.call_args[0][0]
        assert call_kwargs["apiKey"] == "test_key"
        assert call_kwargs["enableRateLimit"] is True

    @patch("ccxt.bybit")
    def test_markets_loaded_after_create(self, mock_bybit_class):
        """load_markets is called after creation."""
        mock_instance = make_mock_exchange()
        mock_bybit_class.return_value = mock_instance

        ExchangeFactory.create("bybit")
        mock_instance.load_markets.assert_called_once()


class TestExchangeFactoryResolveSymbol:
    """Symbol resolution logic."""

    def test_resolve_already_unified(self):
        """Already unified symbol returns unchanged."""
        exchange = make_mock_exchange()
        assert ExchangeFactory.resolve_symbol(exchange, "BTC/USDT") == "BTC/USDT"

    def test_resolve_raw_with_usdt(self):
        """BTCUSDT → BTC/USDT."""
        exchange = make_mock_exchange()
        assert ExchangeFactory.resolve_symbol(exchange, "BTCUSDT") == "BTC/USDT"

    def test_resolve_with_underscore(self):
        """ETH_USDT → ETH/USDT."""
        exchange = make_mock_exchange({"ETH/USDT": {}})
        assert ExchangeFactory.resolve_symbol(exchange, "ETH_USDT") == "ETH/USDT"

    def test_resolve_with_dash(self):
        """SOL-USDT → SOL/USDT."""
        exchange = make_mock_exchange({"SOL/USDT": {}})
        assert ExchangeFactory.resolve_symbol(exchange, "SOL-USDT") == "SOL/USDT"

    def test_resolve_invalid_raises(self):
        """Unknown symbol raises ValueError."""
        exchange = make_mock_exchange()
        with pytest.raises(ValueError, match="'XXXXXX'"):
            ExchangeFactory.resolve_symbol(exchange, "XXXXXX")

    def test_resolve_empty_markets(self):
        """Symbol not in empty markets raises ValueError."""
        exchange = make_mock_exchange(markets={})
        with pytest.raises(ValueError):
            ExchangeFactory.resolve_symbol(exchange, "BTCUSDT")


class TestExchangeFactoryCloseAll:
    """Cleanup behaviour."""

    def setup_method(self) -> None:
        """Clear the singleton cache before each test."""
        ExchangeFactory.close_all()

    @patch("ccxt.bybit")
    def test_close_all_clears_instances(self, mock_bybit_class):
        """close_all clears the cache."""
        mock_instance = make_mock_exchange()
        mock_bybit_class.return_value = mock_instance

        ExchangeFactory.create("bybit")
        assert len(ExchangeFactory._instances) > 0
        ExchangeFactory.close_all()
        assert len(ExchangeFactory._instances) == 0

    @patch("ccxt.bybit")
    def test_close_all_calls_close(self, mock_bybit_class):
        """close_all calls close() on each cached instance."""
        mock_instance = make_mock_exchange()
        mock_bybit_class.return_value = mock_instance

        ExchangeFactory.create("bybit")
        ExchangeFactory.close_all()
        mock_instance.close.assert_called_once()

    @patch("ccxt.bybit")
    def test_close_all_handles_close_error(self, mock_bybit_class):
        """close_all handles exchange.close() raising."""
        mock_instance = make_mock_exchange()
        mock_instance.close.side_effect = RuntimeError("close failed")
        mock_bybit_class.return_value = mock_instance

        ExchangeFactory.create("bybit")
        # Should not raise
        ExchangeFactory.close_all()
        assert len(ExchangeFactory._instances) == 0


# =============================================================================
# Fetcher Tests
# =============================================================================


class TestFetchSinglePage:
    """Single-page fetch behaviour."""

    def test_single_page_returns_candles(self):
        """Single page returns mock candles."""
        exchange = make_mock_exchange()
        candles = [make_candle(t) for t in range(1000, 1000 + 200 * 60000, 60000)]
        exchange.fetch_ohlcv.return_value = candles

        result = fetch_ohlcv(exchange, "BTC/USDT", "1h", limit=200, max_pages=1)
        assert len(result) == 200
        exchange.fetch_ohlcv.assert_called_once()

    def test_single_page_empty(self):
        """Empty response from exchange returns empty list."""
        exchange = make_mock_exchange()
        exchange.fetch_ohlcv.return_value = []

        result = fetch_ohlcv(exchange, "BTC/USDT", "1h", limit=200, max_pages=1)
        assert result == []

    def test_single_page_partial(self):
        """Fewer than limit candles returns early."""
        exchange = make_mock_exchange()
        candles = [make_candle(t) for t in range(1000, 1000 + 50 * 60000, 60000)]
        exchange.fetch_ohlcv.return_value = candles

        result = fetch_ohlcv(exchange, "BTC/USDT", "1h", limit=200, max_pages=1)
        assert len(result) == 50
        exchange.fetch_ohlcv.assert_called_once()


class TestFetchPagination:
    """Multi-page pagination behaviour."""

    def test_pagination_two_pages(self):
        """Two pages: first 200, second 50 → 250 total."""
        exchange = make_mock_exchange()
        page1 = [make_candle(t) for t in range(1000, 1000 + 200 * 60000, 60000)]
        page2 = [make_candle(t) for t in range(
            1000 + 200 * 60000,
            1000 + 250 * 60000,
            60000,
        )]

        def fetch_side_effect(symbol, timeframe, since=None, limit=None, params=None):
            if since is None or since < 1000 + 200 * 60000:
                return page1
            return page2

        exchange.fetch_ohlcv.side_effect = fetch_side_effect

        result = fetch_ohlcv(exchange, "BTC/USDT", "1h", since=1000,
                             limit=200, max_pages=2)
        assert len(result) == 250

    def test_pagination_max_pages_cap(self):
        """Fetch stops at max_pages even if more data available."""
        exchange = make_mock_exchange()
        page = [make_candle(t) for t in range(1000, 1000 + 200 * 60000, 60000)]
        exchange.fetch_ohlcv.return_value = page

        result = fetch_ohlcv(exchange, "BTC/USDT", "1h", since=1000,
                             limit=200, max_pages=3)
        assert len(result) == 600
        assert exchange.fetch_ohlcv.call_count == 3

    def test_pagination_empty_second_page(self):
        """Second page returns empty → break early."""
        exchange = make_mock_exchange()
        page1 = [make_candle(t) for t in range(1000, 1000 + 200 * 60000, 60000)]
        exchange.fetch_ohlcv.side_effect = [page1, []]

        result = fetch_ohlcv(exchange, "BTC/USDT", "1h", since=1000,
                             limit=200, max_pages=5)
        assert len(result) == 200
        assert exchange.fetch_ohlcv.call_count == 2

    def test_pagination_since_advancement(self):
        """since parameter advances correctly between pages."""
        exchange = make_mock_exchange()

        # Page 1: 200 candles starting at ts=1000
        page1 = [make_candle(t) for t in range(1000, 1000 + 200 * 60000, 60000)]

        # Second page should be called with since = page1[-1][0] + 3600000
        expected_since = page1[-1][0] + 3600000
        page2 = [make_candle(t) for t in range(
            expected_since,
            expected_since + 100 * 60000,
            60000,
        )]

        exchange.fetch_ohlcv.side_effect = [page1, page2, []]

        result = fetch_ohlcv(exchange, "BTC/USDT", "1h", since=1000,
                             limit=200, max_pages=5)
        assert len(result) == 300

        # Verify the second call used advanced since
        second_call_since = exchange.fetch_ohlcv.call_args_list[1][0][2]
        assert second_call_since == expected_since, (
            f"Expected since={expected_since}, got {second_call_since}"
        )

    def test_pagination_passes_params(self):
        """params dict is forwarded to CCXT."""
        exchange = make_mock_exchange()
        exchange.fetch_ohlcv.return_value = []
        params = {"type": "spot"}

        fetch_ohlcv(exchange, "BTC/USDT", "1h", limit=200,
                    max_pages=1, params=params)
        exchange.fetch_ohlcv.assert_called_once_with(
            "BTC/USDT", "1h", None, 200, {"type": "spot"}
        )


# =============================================================================
# Processor Tests
# =============================================================================


class TestProcessCandles:
    """Candle processing pipeline."""

    def test_process_empty(self):
        """Empty list → empty DataFrame with correct columns."""
        df = process_candles([])
        assert isinstance(df, pd.DataFrame)
        assert df.empty
        assert df.columns.tolist() == ["timestamp", "open", "high", "low",
                                        "close", "volume"]

    def test_process_basic(self):
        """Basic candles produce correct columns and types."""
        candles = [
            make_candle(2000, open_p=11.0, high_p=12.0, low_p=10.0,
                        close_p=11.5, vol=200),
            make_candle(1000, open_p=10.0, high_p=11.0, low_p=9.0,
                        close_p=10.5, vol=100),
        ]
        df = process_candles(candles)
        assert len(df) == 2
        assert df.columns.tolist() == ["timestamp", "open", "high", "low",
                                        "close", "volume"]
        assert _is_datetime64(df["timestamp"].dtype), (
            f"Expected datetime64, got {df['timestamp'].dtype}"
        )
        # Sorted ascending
        assert df.iloc[0]["timestamp"] < df.iloc[1]["timestamp"]

    def test_process_sort(self):
        """Unsorted input → sorted output."""
        candles = [make_candle(3000), make_candle(1000), make_candle(2000)]
        df = process_candles(candles)
        timestamps = df["timestamp"].tolist()
        assert timestamps == sorted(timestamps)

    def test_process_dedup(self):
        """Deduplicate timestamps, keep last."""
        candles = [
            make_candle(2000, close_p=11.5),
            make_candle(1000, close_p=10.5),
            make_candle(2000, close_p=12.5),  # duplicate, should keep this
        ]
        df = process_candles(candles)
        assert len(df) == 2
        ts_2000 = pd.Timestamp("1970-01-01 00:00:02")
        last_row = df[df["timestamp"] == ts_2000]
        assert last_row["close"].iloc[0] == 12.5

    def test_process_nan_removal(self):
        """Rows with NaN in any OHLCV column are dropped."""
        candles = [
            make_candle(1000, close_p=10.5),
            [2000, float("nan"), 12.0, 10.0, 11.5, 200],
            make_candle(3000, close_p=12.5),
        ]
        df = process_candles(candles)
        assert len(df) == 2
        assert df.iloc[0]["close"] == 10.5
        assert df.iloc[1]["close"] == 12.5

    def test_process_inf_removal(self):
        """Rows with INF in any OHLCV column are dropped."""
        candles = [
            make_candle(1000, close_p=10.5),
            [2000, float("inf"), 12.0, 10.0, 11.5, 200],
            make_candle(3000, close_p=12.5),
        ]
        df = process_candles(candles)
        assert len(df) == 2

    def test_process_timestamp_dtype(self):
        """Timestamps are datetime64."""
        candles = [make_candle(1000)]
        df = process_candles(candles)
        assert _is_datetime64(df["timestamp"].dtype)

    def test_process_numeric_dtypes(self):
        """OHLCV columns are float64."""
        candles = [make_candle(1000)]
        df = process_candles(candles)
        for col in ["open", "high", "low", "close", "volume"]:
            assert df[col].dtype == np.float64, f"{col} is {df[col].dtype}"

    def test_process_preserves_all_values(self):
        """All values preserved correctly through pipeline."""
        candles = [
            [1000, 10.0, 11.0, 9.0, 10.5, 100.0],
            [2000, 11.0, 12.0, 10.0, 11.5, 200.0],
        ]
        df = process_candles(candles)
        assert df.iloc[0]["open"] == 10.0
        assert df.iloc[0]["high"] == 11.0
        assert df.iloc[0]["low"] == 9.0
        assert df.iloc[0]["close"] == 10.5
        assert df.iloc[0]["volume"] == 100.0


# =============================================================================
# Validate Candles Tests
# =============================================================================


class TestValidateCandles:
    """Candle validation logic."""

    def test_validate_good_data(self):
        """Good data → no warnings."""
        candles = [make_candle(1000), make_candle(2000), make_candle(3000)]
        df = process_candles(candles)
        warnings = validate_candles(df)
        assert len(warnings) == 0

    def test_validate_empty(self):
        """Empty DataFrame → warning."""
        df = process_candles([])
        warnings = validate_candles(df)
        assert "Empty DataFrame" in warnings

    def test_validate_zero_volume(self):
        """Zero-volume rows flagged."""
        candles = [make_candle(1000, vol=0), make_candle(2000, vol=1000)]
        df = process_candles(candles)
        warnings = validate_candles(df)
        zero_warnings = [w for w in warnings if "zero-volume" in w]
        assert len(zero_warnings) == 1

    def test_validate_no_gap_warning_for_normal(self):
        """Normal hourly data → no gap warning (gap < 2h)."""
        candles = [make_candle(1000 + i * 3600000) for i in range(5)]
        df = process_candles(candles)
        warnings = validate_candles(df)
        gap_warnings = [w for w in warnings if "gap" in w.lower()]
        assert len(gap_warnings) == 0


# =============================================================================
# Storage Tests
# =============================================================================


class TestSaveCandles:
    """CSV save/load roundtrip."""

    def test_save_csv_format(self, tmp_path: Path):
        """Saved CSV has correct columns and no index."""
        candles = [make_candle(1000), make_candle(2000)]
        df = process_candles(candles)
        path = tmp_path / "test.csv"
        save_candles(df, str(path))

        raw = path.read_text()
        header = raw.strip().split("\n")[0]
        assert header == "timestamp,open,high,low,close,volume"
        # No extra index column
        assert ",," not in raw

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        """DataFrame → CSV → DataFrame matches."""
        candles = [make_candle(1000), make_candle(2000)]
        df = process_candles(candles)
        path = tmp_path / "test.csv"
        save_candles(df, str(path))

        loaded = load_candles(str(path))
        assert loaded.columns.tolist() == [
            "timestamp", "open", "high", "low", "close", "volume"
        ]
        assert len(loaded) == 2
        assert _is_datetime64(loaded["timestamp"].dtype)
        assert loaded.iloc[0]["close"] == 105.0

    def test_save_creates_directory(self, tmp_path: Path):
        """Creates parent directory if missing."""
        candles = [make_candle(1000)]
        df = process_candles(candles)
        path = tmp_path / "subdir" / "test.csv"
        save_candles(df, str(path))
        assert path.exists()

    def test_save_empty_dataframe(self, tmp_path: Path):
        """Empty DataFrame → CSV with header only."""
        df = process_candles([])
        path = tmp_path / "empty.csv"
        save_candles(df, str(path))
        raw = path.read_text().strip()
        assert raw == "timestamp,open,high,low,close,volume"

    def test_save_atomic_no_corruption(self, tmp_path: Path):
        """Write twice, verify no corruption."""
        candles_a = [make_candle(1000, close_p=10.0)]
        candles_b = [make_candle(2000, close_p=20.0)]
        df_a = process_candles(candles_a)
        df_b = process_candles(candles_b)

        path = tmp_path / "atomic.csv"
        save_candles(df_a, str(path))
        save_candles(df_b, str(path))

        loaded = load_candles(str(path))
        assert len(loaded) == 1
        assert loaded.iloc[0]["close"] == 20.0

    def test_save_timestamp_format(self, tmp_path: Path):
        """Timestamp saved as ISO string."""
        candles = [make_candle(1704067200000)]  # 2024-01-01 00:00:00 UTC
        df = process_candles(candles)
        path = tmp_path / "ts_test.csv"
        save_candles(df, str(path))
        raw = path.read_text().strip()
        assert "2024-01-01" in raw
        assert "00:00:00" in raw

    def test_load_candles_parseable(self, tmp_path: Path):
        """CSV loaded with parse_dates works."""
        candles = [make_candle(1704067200000)]
        df = process_candles(candles)
        path = tmp_path / "parse_test.csv"
        save_candles(df, str(path))

        loaded = pd.read_csv(path, parse_dates=["timestamp"])
        assert _is_datetime64(loaded["timestamp"].dtype)


# =============================================================================
# Integration-style sanity (no network)
# =============================================================================


class TestPipelineIntegration:
    """End-to-end pipeline without network."""

    def setup_method(self) -> None:
        """Clear the singleton cache before each test."""
        ExchangeFactory.close_all()

    def test_full_pipeline_mocked(self, tmp_path: Path):
        """ExchangeFactory → fetch_ohlcv → process → save roundtrip."""
        exchange = make_mock_exchange()
        candles = [make_candle(1000 + i * 3600000) for i in range(50)]
        exchange.fetch_ohlcv.return_value = candles
        exchange.parse_timeframe.return_value = 3600

        symbol = ExchangeFactory.resolve_symbol(exchange, "BTCUSDT")
        assert symbol == "BTC/USDT"

        raw = fetch_ohlcv(exchange, symbol, "1h", limit=50, max_pages=1)
        assert len(raw) == 50

        df = process_candles(raw)
        assert len(df) == 50
        assert df["timestamp"].is_monotonic_increasing

        path = tmp_path / "pipeline.csv"
        save_candles(df, str(path))
        loaded = load_candles(str(path))
        assert len(loaded) == 50
        assert _is_datetime64(loaded["timestamp"].dtype)
