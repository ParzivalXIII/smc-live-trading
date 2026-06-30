"""
exchange.py — CCXT exchange factory with singleton caching.

Maintains one instance per exchange ID. Config is frozen after first creation.
Public OHLCV fetching does not require API keys.
"""

from typing import Optional


class ExchangeFactory:
    """CCXT exchange factory with singleton caching.

    Maintains one instance per exchange ID. Config is frozen after first creation.
    Public OHLCV fetching does not require API keys.
    """

    _instances: dict = {}
    _markets_loaded: set = set()

    @classmethod
    def create(cls, exchange_id: str = "bybit", config: Optional[dict] = None) -> "ccxt.Exchange":
        """Create or retrieve a cached CCXT exchange instance.

        Args:
            exchange_id: CCXT exchange identifier (e.g. 'bybit', 'binance').
            config: Optional config dict (apiKey, secret, params.type, etc.).

        Returns:
            A CCXT exchange instance with markets loaded.
        """
        import ccxt

        key = exchange_id
        if key not in cls._instances:
            cfg = {"enableRateLimit": True}
            if config:
                cfg.update(config)
            exchange_class = getattr(ccxt, exchange_id)
            instance = exchange_class(cfg)
            instance.load_markets()
            cls._instances[key] = instance
            cls._markets_loaded.add(key)
        return cls._instances[key]

    @classmethod
    def resolve_symbol(cls, exchange, symbol: str) -> str:
        """Resolve a raw symbol to CCXT unified format (e.g. 'BTCUSDT' → 'BTC/USDT').

        Uses CCXT's own market metadata first, then falls back to separator
        normalization. Prefers exchange.market() which checks both
        exchange.markets and exchange.markets_by_id internally.

        Args:
            exchange: CCXT exchange instance (with markets loaded).
            symbol: Raw symbol string.

        Returns:
            Unified CCXT symbol string.

        Raises:
            ValueError: If the symbol cannot be resolved in exchange markets.
        """
        # Direct lookup (fast path)
        if symbol in exchange.markets:
            return symbol

        # Let CCXT resolve via markets / markets_by_id / market IDs
        try:
            return exchange.market(symbol)["symbol"]
        except Exception:
            pass

        # Normalize separators and try again
        normalized = symbol.replace("-", "/").replace("_", "/")
        if normalized != symbol:
            if normalized in exchange.markets:
                return normalized
            try:
                return exchange.market(normalized)["symbol"]
            except Exception:
                pass

        raise ValueError(
            f"Symbol '{symbol}' not found in exchange markets. "
            f"Available examples: {list(exchange.markets.keys())[:10]}"
        )

    @classmethod
    def close_all(cls) -> None:
        """Close all cached exchange instances (cleanup)."""
        for key, instance in list(cls._instances.items()):
            try:
                instance.close()
            except Exception:
                pass
        cls._instances.clear()
        cls._markets_loaded.clear()
