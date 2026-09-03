"""Triangular arbitrage strategy.

Exploits three-leg cycles on a single venue:
  USDT → ERG → XMR → USDT  (or the reverse)
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from config import Settings
from environment import EnvironmentState
from strategy_lab.base import Opportunity, Strategy
from trading.exchange import Book

log = logging.getLogger(__name__)

CYCLE_DEFINITIONS: List[Dict[str, object]] = [
    {
        "name": "USDT-ERG-XMR-USDT",
        "legs": [
            ("ERG/USDT", "buy"),
            ("XMR/ERG", "buy"),
            ("XMR/USDT", "sell"),
        ],
        "alt_legs": [
            ("ERG/USDT", "buy"),
            ("ERG/XMR", "sell"),
            ("XMR/USDT", "sell"),
        ],
    },
    {
        "name": "USDT-XMR-ERG-USDT",
        "legs": [
            ("XMR/USDT", "buy"),
            ("XMR/ERG", "sell"),
            ("ERG/USDT", "sell"),
        ],
        "alt_legs": [
            ("XMR/USDT", "buy"),
            ("ERG/XMR", "buy"),
            ("ERG/USDT", "sell"),
        ],
    },
]

DEFAULT_TAKER_FEES_BPS: Dict[str, float] = {
    "kucoin": 10.0,
    "gateio": 20.0,
    "mexc": 20.0,
    "kraken": 26.0,
    "whitebit": 20.0,
}


class TriangularStrategy(Strategy):
    """Triangular arbitrage strategy."""

    name = "triangular"

    def scan(
        self,
        books: Dict[Tuple[str, str], Book],
        environment: Optional[EnvironmentState] = None,
    ) -> List[Opportunity]:
        """Scan for triangular opportunities."""
        opportunities: List[Opportunity] = []

        # Group books by venue
        by_venue: Dict[str, Dict[str, Book]] = {}
        for (venue, symbol), book in books.items():
            by_venue.setdefault(venue, {})[symbol] = book

        for venue, venue_books in by_venue.items():
            for cycle_def in CYCLE_DEFINITIONS:
                # Try primary legs
                result = self._check_cycle(
                    venue, venue_books, cycle_def["legs"], cycle_def["name"]
                )
                if result is None and "alt_legs" in cycle_def:
                    # Try alternate legs
                    result = self._check_cycle(
                        venue, venue_books, cycle_def["alt_legs"], cycle_def["name"]
                    )
                if result:
                    opportunities.append(result)

        if environment:
            opportunities = self._apply_environment(opportunities, environment)

        min_edge = self._settings.min_edge_bps
        return [opp for opp in opportunities if opp.expected_edge_bps >= min_edge]

    def _check_cycle(
        self,
        venue: str,
        venue_books: Dict[str, Book],
        legs: List[Tuple[str, str]],
        cycle_name: str,
    ) -> Optional[Opportunity]:
        """Check a single cycle for profitability."""
        product = 1.0
        leg_details = []

        for symbol, side in legs:
            book = venue_books.get(symbol)
            if book is None:
                return None
            if side == "buy":
                price = book.best_ask[0] if book.best_ask else None
                if price is None or price <= 0:
                    return None
                product *= 1.0 / price
                leg_details.append({"symbol": symbol, "side": side, "price": price})
            else:
                price = book.best_bid[0] if book.best_bid else None
                if price is None or price <= 0:
                    return None
                product *= price
                leg_details.append({"symbol": symbol, "side": side, "price": price})

        gross_return = product
        gross_edge_bps = (gross_return - 1.0) * 10_000.0

        if gross_edge_bps <= 0:
            return None

        # Fee: 3 legs compounded
        fee_rate = self._taker_fee_bps(venue) / 10_000.0
        fee_factor = (1.0 - fee_rate) ** 3

        # Slippage: half spread per leg
        slippage_bps = 0.0
        for leg in leg_details:
            book = venue_books[leg["symbol"]]
            mid = book.mid
            spread = book.spread
            if mid and spread:
                slippage_bps += (spread / mid) * 10_000.0 / 2.0

        net_return = gross_return * fee_factor
        net_edge_bps = (net_return - 1.0) * 10_000.0 - slippage_bps

        if net_edge_bps <= 0:
            return None

        return Opportunity(
            strategy=self.name,
            symbol="TRIANGULAR",
            venues=[venue],
            expected_edge_bps=round(net_edge_bps, 4),
            confidence=min(1.0, max(0.0, net_edge_bps / 100.0)),
            size=round(self._settings.max_position, 2),
            metadata={
                "cycle_name": cycle_name,
                "gross_edge_bps": round(gross_edge_bps, 4),
                "legs": leg_details,
            },
        )

    def _apply_environment(
        self,
        opportunities: List[Opportunity],
        environment: EnvironmentState,
    ) -> List[Opportunity]:
        """Adjust opportunities based on environment."""
        adjusted = []
        for opp in opportunities:
            healthy = environment.healthy_venues
            if opp.venues[0] not in healthy:
                continue
            regime = environment.regime
            if regime.value == "volatile":
                opp.expected_edge_bps *= 0.8
                opp.confidence *= 0.9
            adjusted.append(opp)
        return adjusted

    @staticmethod
    def _taker_fee_bps(venue: str) -> float:
        return DEFAULT_TAKER_FEES_BPS.get(venue, 25.0)