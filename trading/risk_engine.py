"""Risk engine — pre-trade risk gates and position limits.

Enforces:
  - Maximum position size per trade
  - Maximum drawdown from peak equity
  - Maximum daily loss
  - Maximum open orders
  - Per-venue exposure limits (marked to USD via Portfolio)

Risk state is persisted to disk so **a restart never resets risk**:
peak equity, daily P&L floor, and consecutive losses survive restarts.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from config import Settings
from trading.portfolio import Portfolio

log = logging.getLogger(__name__)


@dataclass
class RiskCheck:
    """Result of a risk check."""
    approved: bool
    reason: str = ""
    size_adjustment: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class RiskEngine:
    """Pre-trade risk management with persistent state."""

    def __init__(self, settings: Settings, state_path: Optional[str] = None) -> None:
        self._settings = settings
        self._peak_equity: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_start: float = time.time()
        self._consecutive_losses: int = 0
        self._open_orders: int = 0
        self._last_reset: float = time.time()

        # Persist risk state to survive restarts.
        self._state_path = state_path or os.path.join(
            getattr(settings, "memory_dir", ".memory"), "risk_state.json"
        )
        self._load_state()

    def _load_state(self) -> None:
        """Load persisted risk state if present."""
        try:
            if not os.path.exists(self._state_path):
                return
            with open(self._state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._peak_equity = float(data.get("peak_equity", 0.0))
            self._daily_pnl = float(data.get("daily_pnl", 0.0))
            self._daily_start = float(data.get("daily_start", time.time()))
            self._consecutive_losses = int(data.get("consecutive_losses", 0))
            self._open_orders = int(data.get("open_orders", 0))
            log.info("Loaded persisted risk state: peak_equity=%.2f daily_pnl=%.2f",
                     self._peak_equity, self._daily_pnl)
        except Exception as exc:
            log.warning("Failed to load risk state from %s: %s", self._state_path, exc)

    def _save_state(self) -> None:
        """Persist risk state to disk."""
        try:
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            with open(self._state_path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "peak_equity": self._peak_equity,
                        "daily_pnl": self._daily_pnl,
                        "daily_start": self._daily_start,
                        "consecutive_losses": self._consecutive_losses,
                        "open_orders": self._open_orders,
                        "last_reset": self._last_reset,
                    },
                    fh,
                )
        except Exception as exc:
            log.error("Failed to save risk state: %s", exc)

    def _maybe_roll_day(self) -> None:
        """Roll the daily P&L window when a new day begins."""
        now = time.time()
        if now - self._daily_start > 86400:
            self._daily_pnl = 0.0
            self._daily_start = now
            self._save_state()

    def check_trade(
        self,
        opportunity: Any,
        current_equity: float,
        current_positions: Dict[str, Dict[str, float]],
        mark_prices: Optional[Dict[str, float]] = None,
    ) -> RiskCheck:
        """Check if a trade passes all risk gates."""
        self._maybe_roll_day()

        # Update peak equity
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
            self._save_state()

        # Notional in numeraire (USD).
        size = float(getattr(opportunity, "size", 0) or 0)
        price = float(
            getattr(opportunity, "expected_price", 0)
            or (getattr(opportunity, "metadata", {}) or {}).get("buy_price", 0)
            or 0
        )
        trade_notional = size * price if price > 0 else size
        max_position = getattr(self._settings, "max_position", 100.0)
        if trade_notional > max_position:
            return RiskCheck(
                approved=False,
                reason=f"Trade notional {trade_notional:.2f} exceeds max position {max_position:.2f}",
            )

        # Check 2: Max drawdown
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - current_equity) / self._peak_equity
            max_dd = getattr(self._settings, "max_drawdown_pct", 10.0) / 100.0
            if drawdown > max_dd:
                return RiskCheck(
                    approved=False,
                    reason=f"Max drawdown exceeded: {drawdown:.2%} > {max_dd:.2%}",
                )

        # Check 3: Max daily loss
        max_daily_loss = getattr(self._settings, "max_daily_loss", 100.0)
        if self._daily_pnl < -max_daily_loss:
            return RiskCheck(
                approved=False,
                reason=f"Max daily loss reached: {self._daily_pnl:.2f}",
            )

        # Check 4: Max open orders
        max_orders = getattr(self._settings, "max_open_orders", 5)
        if self._open_orders >= max_orders:
            return RiskCheck(
                approved=False,
                reason=f"Max open orders reached: {self._open_orders}",
            )

        # Check 5: Consecutive losses circuit breaker
        max_consecutive = getattr(self._settings, "max_consecutive_losses", 5)
        if self._consecutive_losses >= max_consecutive:
            return RiskCheck(
                approved=False,
                reason=f"Consecutive losses limit: {self._consecutive_losses}",
            )

        # Check 6: Per-venue exposure, marked to USD.
        portfolio = Portfolio(current_positions, mark_prices)
        venue_exposure = portfolio.venue_exposure()
        max_venue_exposure = getattr(self._settings, "max_venue_exposure", 500.0)
        venues = getattr(opportunity, "venues", []) or []
        for venue in venues:
            exposure = venue_exposure.get(venue, 0.0)
            if exposure + trade_notional > max_venue_exposure:
                return RiskCheck(
                    approved=False,
                    reason=(
                        f"Venue {venue} exposure limit: "
                        f"{exposure:.2f} + {trade_notional:.2f} > {max_venue_exposure:.2f}"
                    ),
                    metadata={
                        "venue_exposure_usd": {
                            v: round(x, 2) for v, x in venue_exposure.items()
                        }
                    },
                )

        # All checks passed
        return RiskCheck(
            approved=True,
            reason="All risk checks passed",
            size_adjustment=1.0,
            metadata={"portfolio": portfolio.summary()},
        )

    def record_trade_result(self, pnl: float) -> None:
        """Record a trade result for tracking (persisted)."""
        self._daily_pnl += pnl
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        self._save_state()

    def record_order_opened(self) -> None:
        """Record a new open order."""
        self._open_orders += 1
        self._save_state()

    def record_order_closed(self) -> None:
        """Record a closed order."""
        self._open_orders = max(0, self._open_orders - 1)
        self._save_state()

    def get_status(self) -> Dict[str, Any]:
        """Get current risk status."""
        return {
            "peak_equity": self._peak_equity,
            "daily_pnl": self._daily_pnl,
            "consecutive_losses": self._consecutive_losses,
            "open_orders": self._open_orders,
            "state_path": self._state_path,
        }