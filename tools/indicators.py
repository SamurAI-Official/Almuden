"""Market indicators — spread matrices, VWAP, slippage models.

Pure functions over :class:`trading.exchange.Book` snapshots. No I/O.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from trading.exchange import Book

log = logging.getLogger(__name__)


def mid_price(book: Book) -> Optional[float]:
    return book.mid


def spread_bps(book: Book) -> Optional[float]:
    """Bid-ask spread in bps."""
    mid = book.mid
    spread = book.spread
    if mid and spread:
        return (spread / mid) * 10_000.0
    return None


def vwap(levels: List[Tuple[float, float]], depth: float) -> Optional[float]:
    """Volume-weighted average price for *depth* units walked through *levels*.

    *levels* must be ordered best-first (asks ascending for buys,
    bids descending for sells).
    """
    if depth <= 0 or not levels:
        return None
    remaining = depth
    total_cost = 0.0
    filled = 0.0
    for price, size in levels:
        take = min(size, remaining)
        total_cost += take * price
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if filled <= 0:
        return None
    return total_cost / filled


def slippage_bps(book: Book, side: str, size: float) -> Optional[float]:
    """Estimated slippage in bps for a market order of *size*."""
    if side == "buy":
        levels = book.asks
        top = book.best_ask[0] if book.best_ask else None
    else:
        levels = book.bids
        top = book.best_bid[0] if book.best_bid else None
    if top is None:
        return None
    avg = vwap(levels, size)
    if avg is None:
        return None
    return abs(avg - top) / top * 10_000.0


def spread_matrix(
    books: Dict[Tuple[str, str], Book]
) -> List[Dict[str, object]]:
    """Build a cross-venue spread matrix for all same-symbol pairs.

    Returns rows: {symbol, venue_a, venue_b, mid_a, mid_b, spread_bps, edge_bps}.
    """
    by_symbol: Dict[str, Dict[str, Book]] = {}
    for (venue, symbol), book in books.items():
        if book.mid is not None:
            by_symbol.setdefault(symbol, {})[venue] = book

    rows: List[Dict[str, object]] = []
    for symbol, venue_books in sorted(by_symbol.items()):
        venues = sorted(venue_books.keys())
        for i, va in enumerate(venues):
            for vb in venues[i + 1 :]:
                ba = venue_books[va]
                bb = venue_books[vb]
                ma, mb = ba.mid, bb.mid
                if ma is None or mb is None:
                    continue
                edge = (mb / ma - 1.0) * 10_000.0
                rows.append(
                    {
                        "symbol": symbol,
                        "venue_a": va,
                        "venue_b": vb,
                        "mid_a": ma,
                        "mid_b": mb,
                        "spread_a_bps": round(spread_bps(ba) or 0.0, 4),
                        "spread_b_bps": round(spread_bps(bb) or 0.0, 4),
                        "edge_bps": round(edge, 4),
                    }
                )
    rows.sort(key=lambda r: abs(r["edge_bps"]), reverse=True)
    return rows
