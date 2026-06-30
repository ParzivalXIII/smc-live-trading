"""Tests for DataManager — incremental OHLCV fetch, merge, and storage."""

import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from trade_scripts.data_manager import DataManager
from trade_scripts.storage import load_candles


def make_mock_exchange():
    """Create a mock CCXT exchange for DataManager testing."""
    mock = MagicMock()
    mock.markets = {
        "BTC/USDT": {},
        "ETH/USDT": {},
    }
    
    def mock_market(symbol: str) -> dict:
        if symbol in mock.markets:
            return {"symbol": symbol}
        for quote in ["USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "BNB"]:
            if symbol.endswith(quote) and len(symbol) > len(quote):
                candidate = symbol[:-len(quote)] + "/" + quote
                if candidate in mock.markets:
                    return {"symbol": candidate}
        raise Exception(f"Symbol {symbol} not found")
    
    mock.market.side_effect = mock_market
    mock.load_markets.return_value = None
    mock.parse_timeframe.return_value = 3600
    return mock


@pytest.fixture
def mock_exchange():
    return make_mock_exchange()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


class TestDataManagerCsvPath:
    def test_csv_path_resolves_symbol(self, mock_exchange, data_dir):
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            path = dm._csv_path("BTCUSDT", "4h")
            assert path.name == "ohlcv_BTCUSDT_4h.csv"
            assert path.parent == data_dir

    def test_csv_path_with_separator(self, mock_exchange, data_dir):
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            path = dm._csv_path("ETH_USDT", "1d")
            assert path.name == "ohlcv_ETHUSDT_1d.csv"

    def test_csv_path_appends_timeframe(self, mock_exchange, data_dir):
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            path = dm._csv_path("BTC/USDT", "1h")
            assert path.name == "ohlcv_BTCUSDT_1h.csv"


class TestDataManagerUpdate:
    def test_first_fetch_creates_csv(self, mock_exchange, data_dir):
        """No existing CSV → fetch from exchange, save, return DataFrame."""
        mock_exchange.fetch_ohlcv.return_value = [
            [1700000000000, 100.0, 110.0, 90.0, 105.0, 1000.0],
            [1700003600000, 106.0, 116.0, 96.0, 111.0, 1100.0],
        ]
        
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            df = dm.update("BTCUSDT", "4h")
            
            assert len(df) == 2
            assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
            assert data_dir.exists()
            csv_path = data_dir / "ohlcv_BTCUSDT_4h.csv"
            assert csv_path.exists()

    def test_incremental_fetch_appends(self, mock_exchange, data_dir):
        """Existing CSV → fetch only newer candles, merge, dedup."""
        csv_path = data_dir / "ohlcv_BTCUSDT_4h.csv"
        data_dir.mkdir(parents=True, exist_ok=True)
        existing_df = pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "open": [100.0, 102.0],
            "high": [110.0, 112.0],
            "low": [90.0, 92.0],
            "close": [105.0, 107.0],
            "volume": [1000, 1100],
        })
        existing_df.to_csv(csv_path, index=False)
        
        # Mock exchange returns newer candle
        mock_exchange.fetch_ohlcv.return_value = [
            [1704153600000, 108.0, 118.0, 98.0, 113.0, 1200.0],  # 2024-01-02 00:00:00 UTC
        ]
        
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            df = dm.update("BTCUSDT", "4h")
            
            assert len(df) == 2  # 1 existing + 1 new (depends on overlap)
            # Should have at least 2 rows

    def test_no_new_data_returns_existing(self, mock_exchange, data_dir):
        """Exchange returns empty → return existing DataFrame unchanged."""
        csv_path = data_dir / "ohlcv_BTCUSDT_4h.csv"
        data_dir.mkdir(parents=True, exist_ok=True)
        existing_df = pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-01"]),
            "open": [100.0], "high": [110.0], "low": [90.0],
            "close": [105.0], "volume": [1000],
        })
        existing_df.to_csv(csv_path, index=False)
        
        mock_exchange.fetch_ohlcv.return_value = []  # No new data
        
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            df = dm.update("BTCUSDT", "4h")
            
            assert len(df) == 1
            assert df["close"].iloc[0] == 105.0

    def test_corrupted_csv_recovered(self, mock_exchange, data_dir):
        """Corrupted CSV is deleted and re-fetched."""
        data_dir.mkdir(parents=True, exist_ok=True)
        csv_path = data_dir / "ohlcv_BTCUSDT_4h.csv"
        csv_path.write_text("not,a,valid,csv\n")
        
        mock_exchange.fetch_ohlcv.return_value = [
            [1700000000000, 100.0, 110.0, 90.0, 105.0, 1000.0],
        ]
        
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            df = dm.update("BTCUSDT", "4h")
            
            assert len(df) == 1
            assert df["close"].iloc[0] == 105.0

    def test_exchange_error_propagates(self, mock_exchange, data_dir):
        """Exchange errors are propagated, not silently caught."""
        mock_exchange.fetch_ohlcv.side_effect = Exception("API error")
        
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            with pytest.raises(Exception, match="API error"):
                dm.update("BTCUSDT", "4h")

    def test_since_parameter_used(self, mock_exchange, data_dir):
        """Explicit since parameter is passed to fetch_ohlcv."""
        mock_exchange.fetch_ohlcv.return_value = [
            [1700000000000, 100.0, 110.0, 90.0, 105.0, 1000.0],
        ]
        
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            dm.update("BTCUSDT", "4h", since=1700000000000)
            
            # since is passed as 3rd positional arg to exchange.fetch_ohlcv
            call_args = mock_exchange.fetch_ohlcv.call_args[0]
            assert call_args[2] == 1700000000000

    def test_incremental_resume_overrides_since(self, mock_exchange, data_dir):
        """When existing data exists, since is overridden by last known timestamp."""
        csv_path = data_dir / "ohlcv_BTCUSDT_4h.csv"
        data_dir.mkdir(parents=True, exist_ok=True)
        existing_df = pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-02 00:00:00"]),
            "open": [100.0], "high": [110.0], "low": [90.0],
            "close": [105.0], "volume": [1000],
        })
        existing_df.to_csv(csv_path, index=False)
        
        mock_exchange.fetch_ohlcv.return_value = [
            [1704153600000, 106.0, 116.0, 96.0, 111.0, 1100.0],
        ]
        
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            # User requests since=1700000000000 but existing data is newer
            dm.update("BTCUSDT", "4h", since=1700000000000)
            
            # since should be overridden by last known timestamp (2024-01-02) + 1ms
            # not by the explicit since=1700000000000
            call_args = mock_exchange.fetch_ohlcv.call_args[0]
            assert call_args[2] != 1700000000000  # overridden, not the explicit value


    def test_idempotent_update_no_new_candles(self, mock_exchange, data_dir):
        """Multiple update() calls with no new exchange candles produce identical CSV.

        This is the key invariant: if nothing changes on the exchange,
        repeated calls to update() must not corrupt or mutate the CSV.
        """
        mock_exchange.fetch_ohlcv.return_value = [
            [1700000000000, 100.0, 110.0, 90.0, 105.0, 1000.0],
            [1700003600000, 106.0, 116.0, 96.0, 111.0, 1100.0],
        ]

        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))

            # Initial fetch
            df_first = dm.update("BTCUSDT", "4h")
            csv_path = data_dir / "ohlcv_BTCUSDT_4h.csv"
            first_content = csv_path.read_text()
            first_shape = df_first.shape

            # Second call — exchange returns no new candles (empty list)
            mock_exchange.fetch_ohlcv.return_value = []
            df_second = dm.update("BTCUSDT", "4h")

            # Third call — exchange returns no new candles (empty list)
            df_third = dm.update("BTCUSDT", "4h")

            # Fourth call — still no new candles
            df_fourth = dm.update("BTCUSDT", "4h")

            # All returns are identical
            assert df_second.shape == first_shape
            assert df_third.shape == first_shape
            assert df_fourth.shape == first_shape
            assert df_second.equals(df_second)
            assert df_third.equals(df_fourth)

            # CSV content unchanged
            assert csv_path.read_text() == first_content

            # DataFrames are value-equal (not reference-equal)
            # dtype may differ: process_candles → datetime64[ms], CSV round-trip → datetime64[us]
            pd.testing.assert_frame_equal(df_first, df_fourth, check_dtype=False)

    def test_incremental_update_adds_rows(self, mock_exchange, data_dir):
        """Multiple update() calls accumulate new candles correctly."""
        mock_exchange.fetch_ohlcv.return_value = [
            [1700000000000, 100.0, 110.0, 90.0, 105.0, 1000.0],
        ]

        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            df = dm.update("BTCUSDT", "4h")
            assert len(df) == 1

            # Second call — exchange returns new candle
            mock_exchange.fetch_ohlcv.return_value = [
                [1700003600000, 106.0, 116.0, 96.0, 111.0, 1100.0],
            ]
            df2 = dm.update("BTCUSDT", "4h")
            assert len(df2) == 2
            assert df2["close"].iloc[0] == 105.0
            assert df2["close"].iloc[1] == 111.0


class TestDataManagerIntegration:
    def test_full_pipeline_mocked(self, mock_exchange, data_dir):
        """Full pipeline: fetch → process → save → load → verify."""
        mock_exchange.fetch_ohlcv.return_value = [
            [1700000000000, 100.0, 110.0, 90.0, 105.0, 1000.0],
            [1700003600000, 106.0, 116.0, 96.0, 111.0, 1100.0],
        ]
        
        with patch("trade_scripts.data_manager.ExchangeFactory.create", return_value=mock_exchange):
            dm = DataManager(data_dir=str(data_dir))
            df = dm.update("BTCUSDT", "4h")
            
            assert len(df) == 2
            assert all(col in df.columns for col in [
                "timestamp", "open", "high", "low", "close", "volume"
            ])
            
            # Verify CSV is readable
            csv_path = data_dir / "ohlcv_BTCUSDT_4h.csv"
            reloaded = pd.read_csv(csv_path, parse_dates=["timestamp"])
            assert len(reloaded) == 2
            assert reloaded["close"].iloc[-1] == 111.0
