"""Market data feed — order books, OHLCV bars, and derived snapshots."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from config import Settings
from trading.exchange import Book, ExchangeGateway
from tools.indicators import spread_matrix

log = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    """Snapshot of market data across all venues."""
    books: Dict[Tuple[str, str], Book] = field(default_factory=dict)
    spread_matrix: List[Dict] = field(default_factory=list)
    best_bids: Dict[str, Tuple[str, float]] = field(default_factory=dict)
    best_asks: Dict[str, Tuple[str, float]] = field(default_factory=dict)

    @property
    def venues(self) -> List[str]:
        """List of venues with data."""
        return sorted(set(v for v, _ in self.books.keys()))

    @property
    def symbols(self) -> List[str]:
        """List of symbols with data."""
        return sorted(set(s for _, s in self.books.keys()))


class MarketFeed:
    """Feeds market data from exchanges."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._gateway = ExchangeGateway(settings)

    async def poll_books(
        self,
        symbols: Optional[List[str]] = None,
    ) -> Dict[Tuple[str, str], Book]:
        """Poll order books for all venue/symbol combinations."""
        if symbols is None:
            symbols = self._get_default_symbols()

        books: Dict[Tuple[str, str], Book] = {}
        tasks = []
        keys = []

        for venue in self._settings.venues:
            for symbol in symbols:
                keys.append((venue, symbol))
                tasks.append(self._fetch_safe(venue, symbol))

        import asyncio
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (venue, symbol), result in zip(keys, results):
            if isinstance(result, Exception):
                log.debug("Failed to fetch %s %s: %s", venue, symbol, result)
                continue
            books[(venue, symbol)] = result

        return books

    def _get_default_symbols(self) -> List[str]:
        """Get default symbols to monitor."""
        symbols = ["ERG/USDT", "XMR/USDT"]
        # Add triangular cross-pairs if enabled
        if getattr(self._settings, 'triangular_enabled', False):
            for sym in getattr(self._settings, 'triangular_symbols', []):
                if sym not in symbols:
                    symbols.append(sym)
        return symbols

    def snapshot(self, books: Dict[Tuple[str, str], Book]) -> MarketSnapshot:
        """Build a market snapshot from books."""
        matrix = spread_matrix(books)

        # Find best bids and asks per symbol
        best_bids: Dict[str, Tuple[str, float]] = {}
        best_asks: Dict[str, Tuple[str, float]] = {}

        for (venue, symbol), book in books.items():
            if book.best_bid:
                price = book.best_bid[0]
                if symbol not in best_bids or price > best_bids[symbol][1]:
                    best_bids[symbol] = (venue, price)
            if book.best_ask:
                price = book.best_ask[0]
                if symbol not in best_asks or price < best_asks[symbol][1]:
                    best_asks[symbol] = (venue, price)

        return MarketSnapshot(
            books=books,
            spread_matrix=matrix,
            best_bids=best_bids,
            best_asks=best_asks,
        )

    async def _fetch_safe(self, venue: str, symbol: str) -> Book:
        return await self._gateway.fetch_book(venue, symbol)

    async def close(self) -> None:
        await self._gateway.close()