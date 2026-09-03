"""Triangular arbitrage engine.

Exploits three-leg cycles on a single venue:
  USDT → ERG → XMR → USDT  (or the reverse)

Requires the venue to list all three pairs: ERG/USDT, XMR/USDT, and
either ERG/XMR or XMR/ERG. KuCoin is the primary target.

Each leg is a market order, so the cycle is executed atomically on one
venue — no cross-venue transfer risk.

Math
----
For a cycle with legs [(s1, side1), (s2, side2), (s3, side3)]:

  gross_return = conversion_1 * conversion_2 * conversion_3

where for "buy"  on BASE/QUOTE: conversion = 1 / ask
      for "sell" on BASE/QUOTE: conversion = bid

A gross_return > 1.0 means the cycle is profitable before fees.

Fee model: 3 taker fees compounded  (1 - fee_rate)^3
Slippage: half the top-of-book spread per leg, summed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from config import Settings
from trading.exchange import Book

log = logging.getLogger(__name__)

# ── Cycle definitions ─────────────────────────────────────────────────────
# Each cycle starts and ends in USDT. The "alt_legs" variant handles the
# case where the cross pair is listed in the opposite direction.

CYCLE_DEFINITIONS: List[Dict[str, object]] = [
    {
        "name": "USDT-ERG-XMR-USDT",
        "legs": [
            ("ERG/USDT", "buy"),    # spend USDT, receive ERG
            ("XMR/ERG", "buy"),     # spend ERG, receive XMR
            ("XMR/USDT", "sell"),   # spend XMR, receive USDT
        ],
        "alt_legs": [
            ("ERG/USDT", "buy"),
            ("ERG/XMR", "sell"),    # spend ERG, receive XMR
            ("XMR/USDT", "sell"),
        ],
    },
    {
        "name": "USDT-XMR-ERG-USDT",
        "legs": [
            ("XMR/USDT", "buy"),    # spend USDT, receive XMR
            ("XMR/ERG", "sell"),    # spend XMR, receive ERG
            ("ERG/USDT", "sell"),   # spend ERG, receive USDT
        ],
        "alt_legs": [
            ("XMR/USDT", "buy"),
            ("ERG/XMR", "buy"),     # spend XMR, receive ERG
            ("ERG/USDT", "sell"),
        ],
    },
]

# Default taker fees in bps (must match evaluator.py)
DEFAULT_TAKER_FEES_BPS: Dict[str, float] = {
    "kucoin": 10.0,
    "gateio": 20.0,
    "mexc": 20.0,
    "kraken": 26.0,
    "whitebit": 20.0,
}


def _taker_fee_bps(venue: str) -> float:
    return DEFAULT_TAKER_FEES_BPS.get(venue, 25.0)


def _leg_price(book: Book, side: str) -> Optional[float]:
    """Get the execution price for a leg."""
    if side == "buy":
        return book.best_ask[0] if book.best_ask else None
    else:
        return book.best_bid[0] if book.best_bid else None


def _gross_return(
    books: Dict[str, Book], legs: List[Tuple[str, str]]
) -> Optional[float]:
    """Compute the gross return multiplier for a cycle.

    For "buy"  on BASE/QUOTE: 1 QUOTE -> 1/ask BASE
    For "sell" on BASE/QUOTE: 1 BASE  -> bid QUOTE
    """
    product = 1.0
    for symbol, side in legs:
        book = books.get(symbol)
        if book is None:
            return None
        price = _leg_price(book, side)
        if price is None or price <= 0:
            return None
        if side == "buy":
            product *= 1.0 / price
        else:
            product *= price
    return product


# ── Scanner ────────────────────────────────────────────────────────────────


class TriangularScanner:
    """Scan for triangular opportunities on each venue."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def scan(
        self,
        books: Dict[Tuple[str, str], Book],
    ) -> List[Dict]:
        """Find all triangular opportunities across venues.

        Returns list of opportunity dicts sorted by gross edge (descending).
        """
        # Group books by venue
        venue_books: Dict[str, Dict[str, Book]] = {}
        for (venue, symbol), book in books.items():
            venue_books.setdefault(venue, {})[symbol] = book

        opportunities: List[Dict] = []
        for venue, vb in venue_books.items():
            for cycle_def in CYCLE_DEFINITIONS:
                # Try primary legs first, then alt legs
                for legs in (cycle_def["legs"], cycle_def["alt_legs"]):  # type: ignore[assignment]
                    opp = self._check_cycle(venue, vb, str(cycle_def["name"]), legs)  # type: ignore[arg-type]
                    if opp is not None:
                        opportunities.append(opp)
                        break  # Only take the first working variant

        opportunities.sort(key=lambda o: o["gross_edge_bps"], reverse=True)
        return opportunities

    def _check_cycle(
        self,
        venue: str,
        venue_books: Dict[str, Book],
        cycle_name: str,
        legs: List[Tuple[str, str]],
    ) -> Optional[Dict]:
        """Check if a specific cycle is profitable on a venue."""
        # Check all legs are available
        for symbol, side in legs:
            book = venue_books.get(symbol)
            if book is None:
                return None
            price = _leg_price(book, side)
            if price is None or price <= 0:
                return None

        # Compute gross return
        ret = _gross_return(venue_books, legs)
        if ret is None or ret <= 1.0:
            return None

        gross_edge_bps = (ret - 1.0) * 10_000.0

        # Build leg details
        leg_details: List[Dict] = []
        for symbol, side in legs:
            book = venue_books[symbol]
            price = _leg_price(book, side)
            leg_details.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "price": price,
                    "book": book,
                }
            )

        return {
            "venue": venue,
            "cycle_name": cycle_name,
            "legs": leg_details,
            "gross_return": ret,
            "gross_edge_bps": round(gross_edge_bps, 4),
            "books": {leg["symbol"]: leg["book"] for leg in leg_details},
        }


# ── Evaluator ──────────────────────────────────────────────────────────────


class TriangularEvaluator:
    """Fee-aware evaluator for triangular opportunities."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def evaluate(self, opportunities: List[Dict]) -> List[Dict]:
        """Return only opportunities whose net edge >= min_edge_bps."""
        results: List[Dict] = []
        for opp in opportunities:
            net = self._net_edge(opp)
            if net is None:
                continue
            opp["net_edge_bps"] = round(net, 4)
            opp["fee_bps"] = self._round_trip_fee(opp)
            opp["slippage_bps"] = self._est_slippage(opp)
            if net >= self._settings.min_edge_bps:
                results.append(opp)
        results.sort(key=lambda o: o["net_edge_bps"], reverse=True)
        return results

    def _net_edge(self, opp: Dict) -> Optional[float]:
        gross = opp.get("gross_edge_bps")
        if gross is None:
            return None
        fees = self._round_trip_fee(opp)
        slippage = self._est_slippage(opp)
        rebalance = self._rebalance_cost(opp)
        return gross - fees - slippage - rebalance

    def _round_trip_fee(self, opp: Dict) -> float:
        """3-leg taker fee, compounded: 1 - (1 - r)^3."""
        venue = opp["venue"]
        fee_rate = _taker_fee_bps(venue) / 10_000.0
        compounded = (1.0 - fee_rate) ** 3
        return (1.0 - compounded) * 10_000.0

    @staticmethod
    def _est_slippage(opp: Dict) -> float:
        """Estimate slippage for all three legs: half top-of-book spread each."""
        total_slip = 0.0
        for leg in opp.get("legs", []):
            book = leg.get("book")
            if book is None:
                continue
            mid = book.mid
            spread = book.spread
            if mid and spread:
                total_slip += (spread / mid) * 10_000.0 / 2.0
        return total_slip

    @staticmethod
    def _rebalance_cost(opp: Dict) -> float:
        """Triangular arb is single-venue -- no cross-venue rebalance needed."""
        return 0.0


# ── Executor ───────────────────────────────────────────────────────────────


@dataclass
class TriangularResult:
    venue: str
    cycle_name: str
    size: float
    final_amount: float
    gross_edge_bps: float
    net_edge_bps: float
    pnl: float
    status: str  # "executed" | "rejected" | "error"
    reason: str = ""


class TriangularExecutor:
    """Execute triangular arbitrage cycles on a single venue."""

    def __init__(self, settings: Settings, broker) -> None:
        self._settings = settings
        self._broker = broker

    def execute(self, opportunities: List[Dict]) -> List[TriangularResult]:
        """Execute the best opportunities."""
        results: List[TriangularResult] = []
        for opp in opportunities:
            result = self._try_execute(opp)
            results.append(result)
            if result.status == "executed":
                log.info(
                    "Triangular %s on %s: size=%.2f, final=%.2f, net=%.2f bps, pnl=%.4f",
                    result.cycle_name,
                    result.venue,
                    result.size,
                    result.final_amount,
                    result.net_edge_bps,
                    result.pnl,
                )
        return results

    def _try_execute(self, opp: Dict) -> TriangularResult:
        venue = opp["venue"]
        cycle_name = opp["cycle_name"]
        legs = opp["legs"]

        # Size the trade
        size = self._size(opp)
        if size <= 0:
            return TriangularResult(
                venue=venue,
                cycle_name=cycle_name,
                size=0,
                final_amount=0,
                gross_edge_bps=opp.get("gross_edge_bps", 0),
                net_edge_bps=opp.get("net_edge_bps", 0),
                pnl=0,
                status="rejected",
                reason="size zero",
            )

        # Check balance feasibility (need USDT on the venue)
        if hasattr(self._broker, "balance"):
            if self._broker.balance(venue, "USDT") < size:
                return TriangularResult(
                    venue=venue,
                    cycle_name=cycle_name,
                    size=size,
                    final_amount=0,
                    gross_edge_bps=opp.get("gross_edge_bps", 0),
                    net_edge_bps=opp.get("net_edge_bps", 0),
                    pnl=0,
                    status="rejected",
                    reason=f"insufficient USDT on {venue}",
                )

        # Execute the three legs, chaining amounts
        try:
            amount = size
            for leg in legs:
                symbol = leg["symbol"]
                side = leg["side"]
                price = leg["price"]

                if side == "buy":
                    # Spend `amount` of quote, receive amount/price of base
                    fill = self._broker.buy(venue, symbol, amount / price, price)
                    amount = fill.size  # base received
                else:
                    # Spend `amount` of base, receive amount*price of quote
                    fill = self._broker.sell(venue, symbol, amount, price)
                    amount = fill.proceeds  # quote received

            final_amount = amount
            pnl = final_amount - size

            return TriangularResult(
                venue=venue,
                cycle_name=cycle_name,
                size=size,
                final_amount=final_amount,
                gross_edge_bps=opp.get("gross_edge_bps", 0),
                net_edge_bps=opp.get("net_edge_bps", 0),
                pnl=pnl,
                status="executed",
            )
        except Exception:
            log.exception(
                "Triangular execution failed for %s on %s", cycle_name, venue
            )
            return TriangularResult(
                venue=venue,
                cycle_name=cycle_name,
                size=size,
                final_amount=0,
                gross_edge_bps=opp.get("gross_edge_bps", 0),
                net_edge_bps=opp.get("net_edge_bps", 0),
                pnl=0,
                status="error",
                reason="broker error",
            )

    def _size(self, opp: Dict) -> float:
        """Conservative size: min(book depth across all legs, max_position).

        Walks each leg's book to find the limiting depth, converted to USDT.
        """
        min_depth_usdt = float("inf")
        for leg in opp.get("legs", []):
            book = leg.get("book")
            if book is None:
                return 0.0
            side = leg["side"]
            price = leg["price"]
            if side == "buy":
                # depth is in base asset; USDT needed = depth * price
                depth = book.best_ask[1] if book.best_ask else 0
                depth_usdt = depth * price
            else:
                # depth is in base asset; USDT received = depth * price
                depth = book.best_bid[1] if book.best_bid else 0
                depth_usdt = depth * price
            min_depth_usdt = min(min_depth_usdt, depth_usdt)

        if min_depth_usdt == float("inf"):
            return 0.0
        return min(min_depth_usdt, self._settings.max_position)
