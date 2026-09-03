"""Risk engine — pre-trade risk gates and position limits.

Enforces:
  - Maximum position size per trade
  - Maximum drawdown from peak equity
  - Maximum daily loss
  - Maximum open orders
  - Per-venue exposure limits
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config import Settings

log = logging.getLogger(__name__)


@dataclass
class RiskCheck:
    """Result of a risk check."""
    approved: bool
    reason: str = ""
    size_adjustment: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class RiskEngine:
    """Pre-trade risk management."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._peak_equity: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_start: float = time.time()
        self._consecutive_losses: int = 0
        self._open_orders: int = 0
        self._last_reset: float = time.time()

    def check_trade(
        self,
        opportunity: Any,
        current_equity: float,
        current_positions: Dict[str, Dict[str, float]],
    ) -> RiskCheck:
        """Check if a trade passes all risk gates."""
        # Update peak equity
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        # Reset daily PnL if new day
        if time.time() - self._daily_start > 86400:
            self._daily_pnl = 0.0
            self._daily_start = time.time()

        # Check 1: Max position size
        trade_size = getattr(opportunity, 'size', 0) * getattr(opportunity, 'expected_price', 1)
        max_position = self._settings.max_position
        if trade_size > max_position:
            return RiskCheck(
                approved=False,
                reason=f"Trade size {trade_size:.2f} exceeds max position {max_position:.2f}",
            )

        # Check 2: Max drawdown
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - current_equity) / self._peak_equity
            max_dd = getattr(self._settings, 'max_drawdown_pct', 10.0) / 100.0
            if drawdown > max_dd:
                return RiskCheck(
                    approved=False,
                    reason=f"Max drawdown exceeded: {drawdown:.2%} > {max_dd:.2%}",
                )

        # Check 3: Max daily loss
        max_daily_loss = getattr(self._settings, 'max_daily_loss', 100.0)
        if self._daily_pnl < -max_daily_loss:
            return RiskCheck(
                approved=False,
                reason=f"Max daily loss reached: {self._daily_pnl:.2f}",
            )

        # Check 4: Max open orders
        max_orders = getattr(self._settings, 'max_open_orders', 5)
        if self._open_orders >= max_orders:
            return RiskCheck(
                approved=False,
                reason=f"Max open orders reached: {self._open_orders}",
            )

        # Check 5: Consecutive losses circuit breaker
        max_consecutive = getattr(self._settings, 'max_consecutive_losses', 5)
        if self._consecutive_losses >= max_consecutive:
            return RiskCheck(
                approved=False,
                reason=f"Consecutive losses limit: {self._consecutive_losses}",
            )

        # Check 6: Per-venue exposure
        venues = getattr(opportunity, 'venues', [])
        max_venue_exposure = getattr(self._settings, 'max_venue_exposure', 500.0)
        for venue in venues:
            venue_exposure = sum(
                abs(amount)
                for asset, amount in current_positions.get(venue, {}).items()
            )
            if venue_exposure + trade_size > max_venue_exposure:
                return RiskCheck(
                    approved=False,
                    reason=f"Venue {venue} exposure limit: {venue_exposure:.2f} + {trade_size:.2f} > {max_venue_exposure:.2f}",
                )

        # All checks passed
        return RiskCheck(
            approved=True,
            reason="All risk checks passed",
            size_adjustment=1.0,
        )

    def record_trade_result(self, pnl: float) -> None:
        """Record a trade result for tracking."""
        self._daily_pnl += pnl
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def record_order_opened(self) -> None:
        """Record a new open order."""
        self._open_orders += 1

    def record_order_closed(self) -> None:
        """Record a closed order."""
        self._open_orders = max(0, self._open_orders - 1)

    def get_status(self) -> Dict[str, Any]:
        """Get current risk status."""
        return {
            "peak_equity": self._peak_equity,
            "daily_pnl": self._daily_pnl,
            "consecutive_losses": self._consecutive_losses,
            "open_orders": self._open_orders,
        }