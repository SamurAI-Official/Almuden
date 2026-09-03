"""Fee-aware opportunity evaluator.

Converts gross edges into net edges after subtracting:
  - taker fees on both legs (conservative — assumes market orders)
  - estimated slippage based on book depth
  - amortised rebalance cost

Only opportunities whose net edge clears the configured minimum survive.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from config import Settings

log = logging.getLogger(__name__)

# Default taker fees in bps (conservative; ccxt can override when configured)
DEFAULT_TAKER_FEES_BPS: Dict[str, float] = {
    "kucoin": 10.0,
    "gateio": 20.0,
    "mexc": 20.0,
    "kraken": 26.0,
    "whitebit": 20.0,
}


def _taker_fee_bps(venue: str) -> float:
    return DEFAULT_TAKER_FEES_BPS.get(venue, 25.0)


class Evaluator:
    """Score opportunities and drop those that don't clear the net-edge bar."""

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
        gross = opp.get("edge_bps")
        if gross is None:
            return None
        fees = self._round_trip_fee(opp)
        slippage = self._est_slippage(opp)
        rebalance = self._rebalance_cost(opp)
        return gross - fees - slippage - rebalance

    def _round_trip_fee(self, opp: Dict) -> float:
        return _taker_fee_bps(opp["buy_venue"]) + _taker_fee_bps(opp["sell_venue"])

    @staticmethod
    def _est_slippage(opp: Dict) -> float:
        """Rough slippage estimate: half the top-of-book spread per leg, in bps.

        A more precise model would walk the book to the target size. This is
        a conservative placeholder that keeps the evaluator dependency-free.
        """
        buy_book = opp.get("buy_book")
        sell_book = opp.get("sell_book")
        slip = 0.0
        for book in (buy_book, sell_book):
            if book is None:
                continue
            mid = book.mid
            spread = book.spread
            if mid and spread:
                slip += (spread / mid) * 10_000.0 / 2.0
        return slip

    @staticmethod
    def _rebalance_cost(opp: Dict) -> float:
        """Amortised cost to rebalance inventory after the trade, in bps.

        Treated as a small fixed charge per leg; tuned later from live data.
        """
        return 5.0  # placeholder
