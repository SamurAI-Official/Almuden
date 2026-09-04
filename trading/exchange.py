"""CCXT-backed exchange gateway.

Wraps ccxt.async_support to provide a uniform interface across venues.
Public market data works without keys; trading requires credentials.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from config import Settings

log = logging.getLogger(__name__)

# Venue IDs in ccXT
VENUE_MAP = {
    "kucoin": "kucoin",
    "gateio": "gate",
    "mexc": "mexc",
    "kraken": "kraken",
    "whitebit": "whitebit",
}


class ExchangeError(Exception):
    pass


class Book:
    """Normalized order book snapshot."""

    __slots__ = ("venue", "symbol", "bids", "asks", "timestamp")

    def __init__(
        self,
        venue: str,
        symbol: str,
        bids: List[Tuple[float, float]],
        asks: List[Tuple[float, float]],
        timestamp: Optional[float] = None,
    ) -> None:
        self.venue = venue
        self.symbol = symbol
        # bids descending, asks ascending
        self.bids = sorted(bids, key=lambda x: x[0], reverse=True)
        self.asks = sorted(asks, key=lambda x: x[0])
        self.timestamp = timestamp

    @property
    def best_bid(self) -> Optional[Tuple[float, float]]:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[Tuple[float, float]]:
        return self.asks[0] if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid[0] + self.best_ask[0]) / 2.0
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask[0] - self.best_bid[0]
        return None

    def __repr__(self) -> str:
        return (
            f"Book({self.venue} {self.symbol} "
            f"bid={self.best_bid} ask={self.best_ask})"
        )


class ExchangeGateway:
    """Async CCXT gateway managing one exchange instance per venue."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._exchanges: Dict[str, Any] = {}
        self._load_markets_done = False

    async def _get_exchange(self, venue: str) -> Any:
        if venue in self._exchanges:
            return self._exchanges[venue]
        import ccxt.async_support as ccxt

        ccxt_id = VENUE_MAP.get(venue)
        if ccxt_id is None or not hasattr(ccxt, ccxt_id):
            raise ExchangeError(f"Unsupported venue: {venue}")

        keys = self._settings.keys
        creds = self._credentials_for(venue, keys)
        exchange = getattr(ccxt, ccxt_id)({
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
            **creds,
        })
        self._exchanges[venue] = exchange
        return exchange

    @staticmethod
    def _credentials_for(venue: str, keys) -> Dict[str, str]:
        """Pull the right credential fields for a venue."""
        mapping = {
            "kucoin": ("kucoin_key", "kucoin_secret", "kucoin_passphrase"),
            "gateio": ("gateio_key", "gateio_secret", ""),
            "mexc": ("mexc_key", "mexc_secret", ""),
            "kraken": ("kraken_key", "kraken_secret", ""),
            "whitebit": ("whitebit_key", "whitebit_secret", ""),
        }
        fields = mapping.get(venue, ("", "", ""))
        result = {}
        if fields[0]:
            result["apiKey"] = getattr(keys, fields[0], "") or ""
        if fields[1]:
            result["secret"] = getattr(keys, fields[1], "") or ""
        if fields[2]:
            result["password"] = getattr(keys, fields[2], "") or ""
        return result

    async def load_markets(self, venue: str) -> None:
        exchange = await self._get_exchange(venue)
        await exchange.load_markets()

    async def fetch_book(self, venue: str, symbol: str) -> Book:
        """Fetch the L2 order book for *symbol* on *venue*."""
        exchange = await self._get_exchange(venue)
        try:
            ob = await exchange.fetch_order_book(symbol, limit=20)
        except Exception as exc:
            raise ExchangeError(f"{venue} {symbol}: {exc}") from exc

        raw_bids = ob.get("bids", [])
        raw_asks = ob.get("asks", [])
        # Some venues return [price, size, timestamp]; we only need the first two.
        bids = [(float(level[0]), float(level[1])) for level in raw_bids if level[0] > 0 and level[1] > 0]
        asks = [(float(level[0]), float(level[1])) for level in raw_asks if level[0] > 0 and level[1] > 0]
        ts = ob.get("timestamp")
        timestamp = ts / 1000.0 if ts else None
        return Book(venue, symbol, bids, asks, timestamp)

    async def fetch_ticker(self, venue: str, symbol: str) -> Dict[str, Any]:
        exchange = await self._get_exchange(venue)
        return await exchange.fetch_ticker(symbol)

    async def create_market_order(
        self, venue: str, symbol: str, side: str, amount: float
    ) -> Dict[str, Any]:
        """Place a market order. Requires credentials."""
        if self._settings.mode != "live":
            raise ExchangeError("create_market_order only allowed in live mode")
        exchange = await self._get_exchange(venue)
        return await exchange.create_market_order(symbol, side, amount)

    async def fetch_order_fills(
        self, venue: str, order_id: str, symbol: str
    ) -> List[Dict[str, Any]]:
        """Fetch individual fills for a given order ID."""
        exchange = await self._get_exchange(venue)
        return await exchange.fetch_order_fills(
            symbol, since=None, limit=None, params={"orderId": order_id}
        )

    async def close(self) -> None:
        for exchange in self._exchanges.values():
            await exchange.close()
        self._exchanges.clear()
