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

        Args:
            exchange: CCXT exchange instance (with markets loaded).
            symbol: Raw symbol string.

        Returns:
            Unified CCXT symbol string.

        Raises:
            ValueError: If the symbol cannot be resolved in exchange markets.
        """
        if symbol in exchange.markets:
            return symbol

        # Try inserting / before known quote currencies
        import re  # noqa: F811

        for quote in ["USDT", "USDC", "BUSD", "USD", "BTC", "ETH", "BNB"]:
            if symbol.endswith(quote) and len(symbol) > len(quote):
                candidate = symbol[: -len(quote)] + "/" + quote
                if candidate in exchange.markets:
                    return candidate

        # Try common separator variations
        for sep in ["_", "-"]:
            if sep in symbol:
                candidate = symbol.replace(sep, "/")
                if candidate in exchange.markets:
                    return candidate

        # Try upper-case
        upper_sym = symbol.upper()
        if upper_sym != symbol:
            return cls.resolve_symbol(exchange, upper_sym)

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
