"""Paper broker — simulates fills against the live order book.

Used as the default execution backend. Tracks virtual balances per venue
and asset, fills at the quoted price plus a small simulated fee.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

from config import Settings

log = logging.getLogger(__name__)


@dataclass
class Fill:
    venue: str
    symbol: str
    side: str  # "buy" | "sell"
    size: float
    price: float
    fee: float
    cost: float       # total quote outlay (buy: size*price + fee)
    proceeds: float   # total quote received (sell: size*price - fee)


class PaperBroker:
    """Simulated broker with per-venue balances."""

    # Simulated taker fee in bps (matches conservative evaluator defaults)
    FEE_BPS = 10.0

    def __init__(self, settings: Settings, initial_balance: float = 10_000.0) -> None:
        self._settings = settings
        self._initial = initial_balance
        self._balances: Dict[str, Dict[str, float]] = {}
        self._fills: list = []

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

    def buy(self, venue: str, symbol: str, size: float, price: float) -> Fill:
        base, quote = symbol.split("/")
        fee = size * price * self.FEE_BPS / 10_000.0
        cost = size * price + fee
        bal = self._balances.setdefault(venue, {})
        bal[quote] = bal.get(quote, 0.0) - cost
        bal[base] = bal.get(base, 0.0) + size
        fill = Fill(venue, symbol, "buy", size, price, fee, cost, 0.0)
        self._fills.append(fill)
        return fill

    def sell(self, venue: str, symbol: str, size: float, price: float) -> Fill:
        base, quote = symbol.split("/")
        fee = size * price * self.FEE_BPS / 10_000.0
        proceeds = size * price - fee
        bal = self._balances.setdefault(venue, {})
        bal[base] = bal.get(base, 0.0) - size
        bal[quote] = bal.get(quote, 0.0) + proceeds
        fill = Fill(venue, symbol, "sell", size, price, fee, 0.0, proceeds)
        self._fills.append(fill)
        return fill

    def reset(self) -> None:
        self._balances.clear()
        self._fills.clear()
