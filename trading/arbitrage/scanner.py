"""Arbitrage scanner — builds a cross-venue spread matrix from order books.

For each symbol, compares every (buy_venue, sell_venue) pair in both
directions and records the gross edge in basis points.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from trading.exchange import Book

log = logging.getLogger(__name__)

# (symbol, buy_venue, sell_venue) -> edge_bps
Opportunity = Dict[str, object]


def _gross_edge_bps(buy_book: Book, sell_book: Book) -> Optional[float]:
    """Gross edge of buying on buy_book and selling on sell_book, in bps."""
    ask = buy_book.best_ask
    bid = sell_book.best_bid
    if ask is None or bid is None:
        return None
    if ask[0] <= 0:
        return None
    return (bid[0] / ask[0] - 1.0) * 10_000.0


class Scanner:
    """Build a cross-venue spread matrix for one or more symbols."""

    def __init__(self, symbols: List[str]) -> None:
        self._symbols = symbols

    def scan(
        self,
        books: Dict[Tuple[str, str], Book],
    ) -> List[Opportunity]:
        """Return all same-symbol cross-venue opportunities, highest edge first.

        *books* is keyed by (venue, symbol).
        """
        by_symbol: Dict[str, Dict[str, Book]] = {}
        for (venue, symbol), book in books.items():
            if symbol in self._symbols and book.best_bid and book.best_ask:
                by_symbol.setdefault(symbol, {})[venue] = book

        opportunities: List[Opportunity] = []
        for symbol, venue_books in by_symbol.items():
            venues = sorted(venue_books.keys())
            for i, buy_venue in enumerate(venues):
                for sell_venue in venues[i + 1 :]:
                    self._check_pair(
                        symbol, venue_books, buy_venue, sell_venue, opportunities
                    )

        opportunities.sort(key=lambda o: o["edge_bps"], reverse=True)
        return opportunities

    @staticmethod
    def _check_pair(
        symbol: str,
        venue_books: Dict[str, Book],
        venue_a: str,
        venue_b: str,
        out: List[Opportunity],
    ) -> None:
        """Check both directions for a venue pair."""
        # Buy on A, sell on B
        edge = _gross_edge_bps(venue_books[venue_a], venue_books[venue_b])
        if edge is not None:
            out.append(
                {
                    "symbol": symbol,
                    "buy_venue": venue_a,
                    "sell_venue": venue_b,
                    "edge_bps": round(edge, 4),
                    "buy_price": venue_books[venue_a].best_ask[0],
                    "sell_price": venue_books[venue_b].best_bid[0],
                    "buy_book": venue_books[venue_a],
                    "sell_book": venue_books[venue_b],
                }
            )
        # Buy on B, sell on A
        edge = _gross_edge_bps(venue_books[venue_b], venue_books[venue_a])
        if edge is not None:
            out.append(
                {
                    "symbol": symbol,
                    "buy_venue": venue_b,
                    "sell_venue": venue_a,
                    "edge_bps": round(edge, 4),
                    "buy_price": venue_books[venue_b].best_ask[0],
                    "sell_price": venue_books[venue_a].best_bid[0],
                    "buy_book": venue_books[venue_b],
                    "sell_book": venue_books[venue_a],
                }
            )
