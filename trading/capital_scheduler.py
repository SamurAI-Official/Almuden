"""Capital scheduler — gradual capital deployment.

Starts with a tiny fraction of capital and scales up only after
a profitable track record. Prevents blowing up on day one.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from config import Settings

log = logging.getLogger(__name__)


class CapitalScheduler:
    """Manages capital allocation with gradual deployment."""

    # Deployment tiers based on profit里程碑
    TIERS = [
        {"min_profit": 0, "max_capital_pct": 5},      # Start: 5% of capital
        {"min_profit": 50, "max_capital_pct": 10},    # After $50 profit: 10%
        {"min_profit": 200, "max_capital_pct": 25},   # After $200 profit: 25%
        {"min_profit": 500, "max_capital_pct": 50},   # After $500 profit: 50%
        {"min_profit": 1000, "max_capital_pct": 100}, # After $1000 profit: 100%
    ]

    def __init__(self, settings: Settings, total_capital: float = 10_000.0) -> None:
        self._settings = settings
        self._total_capital = total_capital
        self._total_profit: float = 0.0
        self._current_tier: int = 0

    def update_profit(self, pnl: float) -> None:
        """Update profit tracking."""
        self._total_profit += pnl

        # Determine tier
        for i, tier in enumerate(self.TIERS):
            if self._total_profit >= tier["min_profit"]:
                self._current_tier = i

    def get_max_capital(self) -> float:
        """Get maximum capital allowed for current tier."""
        tier = self.TIERS[self._current_tier]
        return self._total_capital * tier["max_capital_pct"] / 100.0

    def get_max_trade_size(self) -> float:
        """Get maximum trade size based on current tier."""
        max_capital = self.get_max_capital()
        # Each trade uses at most 20% of allowed capital
        return max_capital * 0.2

    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status."""
        tier = self.TIERS[self._current_tier]
        return {
            "total_capital": self._total_capital,
            "total_profit": self._total_profit,
            "current_tier": self._current_tier,
            "tier_name": f"{tier['max_capital_pct']}% allocation",
            "max_capital": self.get_max_capital(),
            "max_trade_size": self.get_max_trade_size(),
        }