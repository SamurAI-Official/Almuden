"""Paper broker — simulates fills against the live order book.

Used as the default execution backend. Tracks virtual balances per venue
and asset, fills at the quoted price plus a small simulated fee.
"""
from __future__ import annotations

import logging
from typing import Dict

from config import Settings
from trading.core import Fill, OrderIntent

log = logging.getLogger(__name__)


class PaperBroker:
    """Simulated broker with per-venue balances."""

        # Simulated taker fee in bps (matches conservative evaluator defaults)
    FEE_BPS = 10.0

    def __init__(self, settings: Settings, initial_balance: float = 10_000.0) -> None:
        self._settings = settings
        self._initial = initial_balance
        self._balances: Dict[str, Dict[str, float]] = {}
        self._fills: list = []
        # Seed with initial quote currency balance on a default venue
        for v in settings.venues:
            self._balances.setdefault(v, {})[settings.default_quote] = initial_balance

    def seed_balance(self, venue: str, asset: str, amount: float) -> None:
        """Pre-fund a venue with a starting balance."""
        self._balances.setdefault(venue, {})[asset] = (
            self._balances.get(venue, {}).get(asset, 0.0) + amount
        )

    def balance(self, venue: str, asset: str) -> float:
        return self._balances.get(venue, {}).get(asset, 0.0)

    def all_balances(self) -> Dict[str, Dict[str, float]]:
        return self._balances

    @property
    def fills(self) -> list:
        return list(self._fills)

    async def execute(self, intent: OrderIntent) -> Fill:
        """Execute a trading intent against the paper book.

        Fills at the specified price (already validated by risk/engine)
        minus a simulated fee.  Balances are adjusted accordingly.
        """
        symbol = intent.symbol
        side = intent.side
        size = intent.size
        price = intent.max_price
        venue = intent.venue

        base, quote = symbol.split("/")
        fee = size * price * self.FEE_BPS / 10_000.0
        cost = size * price + fee
        proceeds = size * price - fee if side == "sell" else 0.0

        # For buys, cost = size*price + fee; for sells, proceeds = size*price - fee
        if side == "buy":
            fill_cost = cost
            fill_proceeds = 0.0
        else:
            fill_cost = 0.0
            fill_proceeds = proceeds

        bal = self._balances.setdefault(venue, {})
        if side == "buy":
            bal[quote] = bal.get(quote, 0.0) - fill_cost
            bal[base] = bal.get(base, 0.0) + size
        else:
            bal[base] = bal.get(base, 0.0) - size
            bal[quote] = bal.get(quote, 0.0) + fill_proceeds

        fill = Fill(
            venue=venue,
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            fee=fee,
            cost=fill_cost,
            proceeds=fill_proceeds,
            order_id=intent.id,
            status="filled",
        )
        self._fills.append(fill)
        log.debug("Paper fill: %s", fill)
        return fill
