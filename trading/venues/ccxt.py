"""CCXT venue adapter - wraps the existing ExchangeGateway."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from config import Settings
from trading.exchange import Book, ExchangeGateway
from trading.venues.base import Quote, RiskClass, VenueAdapter, VenueType

log = logging.getLogger(__name__)


class CCXTAdapter(VenueAdapter):
    """One adapter per CEX venue, backed by the shared ExchangeGateway."""

    def __init__(
        self,
        settings: Settings,
        gateway: ExchangeGateway,
        venue_id: str,
        risk_class: RiskClass = RiskClass.BLUECHIP,
    ) -> None:
        self._settings = settings
        self._gateway = gateway
        self._venue_id = venue_id
        self._risk_class = risk_class

    @property
    def name(self) -> str:
        return self._venue_id

    @property
    def venue_type(self) -> VenueType:
        return VenueType.CEX

    async def quote(self, asset_in: str, asset_out: str, amount: float) -> Optional[Quote]:
        """Build a quote from the live order book.

        Buying asset_out with asset_in (e.g. USDT -> ERG): we consume the ask.
        Selling asset_in for asset_out: we hit the bid.
        """
        symbol = f"{asset_out}/{asset_in}" if asset_in != asset_out else asset_out
        book: Optional[Book] = await self._gateway.fetch_book(self._venue_id, symbol)
        if book is None:
            return None
        # Conservative: quote against the worse side of the book.
        best_ask = book.best_ask
        best_bid = book.best_bid
        if not best_ask or not best_bid:
            return None
        mid = book.mid or (best_ask[0] + best_bid[0]) / 2.0
        spread_bps = ((best_ask[0] - best_bid[0]) / mid * 10_000.0) if mid else 0.0
        return Quote(
            venue=self._venue_id,
            venue_type=self.venue_type,
            asset_in=asset_in,
            asset_out=asset_out,
            in_amount=amount,
            out_amount=amount / best_ask[0],  # buy at ask
            price_impact_bps=spread_bps,
            network_cost=0.0,  # taker fee modelled by the evaluator, not here
            risk_class=self._risk_class,
            raw={"symbol": symbol, "bid": best_bid[0], "ask": best_ask[0]},
        )

    async def health(self) -> Dict[str, Any]:
        book = None
        try:
            book = await self._gateway.fetch_book(self._venue_id, "BTC/USDT")
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return {"venue": self._venue_id, "ok": False, "error": str(exc)}
        return {
            "venue": self._venue_id,
            "ok": book is not None,
            "has_book": book is not None,
            "book_age_s": (
                max(0.0, book.timestamp) if book and book.timestamp else None
            ),
        }

    async def close(self) -> None:
        # The gateway is shared across adapters; closing is the owner's job.
        pass
