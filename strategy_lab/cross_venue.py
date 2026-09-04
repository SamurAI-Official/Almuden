"""Cross-venue arbitrage strategy.

Buys on the cheap venue, sells on the expensive one.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from config import Settings
from environment import EnvironmentState
from strategy_lab.base import Opportunity, Strategy
from tools.indicators import spread_bps
from trading.exchange import Book

log = logging.getLogger(__name__)

DEFAULT_TAKER_FEES_BPS: Dict[str, float] = {
    "kucoin": 10.0,
    "gateio": 20.0,
    "mexc": 20.0,
    "kraken": 26.0,
    "whitebit": 20.0,
}


class CrossVenueStrategy(Strategy):
    """Cross-venue inventory arbitrage strategy."""

    name = "cross_venue"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._symbols = ["ERG/USDT", "XMR/USDT"]

        if getattr(settings, "triangular_enabled", False):
            for sym in getattr(settings, "triangular_symbols", []):
                if sym not in self._symbols:
                    self._symbols.append(sym)

    @property
    def risk_class(self) -> str:
        return "arbitrage"

    def scan(
        self,
        books: Dict[Tuple[str, str], Book],
        environment: Optional[EnvironmentState] = None,
    ) -> List[Opportunity]:
        """Scan for cross-venue opportunities."""
        by_symbol: Dict[str, Dict[str, Book]] = {}
        for (venue, symbol), book in books.items():
            if symbol in self._symbols and book.best_bid and book.best_ask:
                by_symbol.setdefault(symbol, {})[venue] = book

        opportunities: List[Opportunity] = []
        for symbol, venue_books in by_symbol.items():
            venues = sorted(venue_books.keys())
            for i, buy_venue in enumerate(venues):
                for sell_venue in venues[i + 1:]:
                    opp = self._check_direction(
                        symbol, venue_books[buy_venue], venue_books[sell_venue],
                        buy_venue, sell_venue
                    )
                    if opp:
                        opportunities.append(opp)
                    opp = self._check_direction(
                        symbol, venue_books[sell_venue], venue_books[buy_venue],
                        sell_venue, buy_venue
                    )
                    if opp:
                        opportunities.append(opp)

        if environment:
            opportunities = self._apply_environment(opportunities, environment)

        min_edge = self._settings.min_edge_bps
        return [opp for opp in opportunities if opp.expected_edge_bps >= min_edge]

    def _check_direction(
        self,
        symbol: str,
        buy_book: Book,
        sell_book: Book,
        buy_venue: str,
        sell_venue: str,
    ) -> Optional[Opportunity]:
        """Check one direction for a cross-venue opportunity."""
        ask = buy_book.best_ask
        bid = sell_book.best_bid
        if ask is None or bid is None or ask[0] <= 0 or bid[0] <= 0:
            return None

        gross_edge = (bid[0] / ask[0] - 1.0) * 10_000.0
        fee_bps = self._taker_fee_bps(buy_venue) + self._taker_fee_bps(sell_venue)
        buy_spread_bps = spread_bps(buy_book) or 0
        sell_spread_bps = spread_bps(sell_book) or 0
        slippage_bps = (buy_spread_bps + sell_spread_bps) / 2.0
        net_edge = gross_edge - fee_bps - slippage_bps - 5.0

        if net_edge < 0:
            return None

        size = min(
            ask[1] if ask else 0,
            bid[1] if bid else 0,
            self._settings.max_position / max(ask[0], 1e-12),
        )

        return Opportunity(
            strategy=self.name,
            symbol=symbol,
            venues=[buy_venue, sell_venue],
            expected_edge_bps=round(net_edge, 4),
            confidence=self._edge_confidence(net_edge),
            size=round(size, 6),
            metadata={
                "buy_venue": buy_venue,
                "sell_venue": sell_venue,
                "gross_edge_bps": round(gross_edge, 4),
                "fee_bps": round(fee_bps, 2),
                "slippage_bps": round(slippage_bps, 2),
                "buy_price": ask[0],
                "sell_price": bid[0],
            },
        )

    def _apply_environment(
        self,
        opportunities: List[Opportunity],
        environment: EnvironmentState,
    ) -> List[Opportunity]:
        """Adjust opportunities based on environment state."""
        adjusted = []
        for opp in opportunities:
            if self._has_critical_news(opp.symbol, environment):
                continue
            regime = environment.regime
            if regime.value == "volatile":
                opp.expected_edge_bps *= 0.7
                opp.confidence *= 0.8
            elif regime.value == "trending":
                opp.expected_edge_bps *= 0.9
            healthy = environment.healthy_venues
            if not all(v in healthy for v in opp.venues):
                continue
            adjusted.append(opp)
        return adjusted

    @staticmethod
    def _has_critical_news(symbol: str, environment: EnvironmentState) -> bool:
        """Check if there's critical news for a symbol."""
        base = symbol.split("/")[0]
        for item in environment.critical_news:
            if base in item.assets:
                return True
        return False

    @staticmethod
    def _taker_fee_bps(venue: str) -> float:
        return DEFAULT_TAKER_FEES_BPS.get(venue, 25.0)

    @staticmethod
    def _edge_confidence(edge_bps: float) -> float:
        return min(1.0, max(0.0, edge_bps / 100.0))