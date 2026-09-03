"""Circuit breaker — monitors trading health and triggers kill switch.

Watches for:
  - Consecutive losses exceeding threshold
  - Drawdown from peak exceeding threshold
  - Error rate exceeding threshold
  - API failures exceeding threshold
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from config import Settings

log = logging.getLogger(__name__)


class CircuitBreaker:
    """Monitors trading health and can halt all activity."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._peak_equity: float = 0.0
        self._consecutive_losses: int = 0
        self._error_count: int = 0
        self._last_error_time: float = 0.0
        self._tripped: bool = False
        self._trip_reason: str = ""
        self._trip_time: float = 0.0

        # Thresholds
        self._max_consecutive_losses = getattr(settings, 'max_consecutive_losses', 5)
        self._max_drawdown_pct = getattr(settings, 'max_drawdown_pct', 10.0) / 100.0
        self._max_errors_per_minute = getattr(settings, 'max_errors_per_minute', 10)

    def update_equity(self, equity: float) -> None:
        """Update equity tracking."""
        if equity > self._peak_equity:
            self._peak_equity = equity

        # Check drawdown
        if self._peak_equity > 0:
            drawdown = (self._peak_equity - equity) / self._peak_equity
            if drawdown > self._max_drawdown_pct:
                self._trip(f"Max drawdown exceeded: {drawdown:.2%}")

    def record_trade(self, pnl: float) -> None:
        """Record a trade result."""
        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self._max_consecutive_losses:
                self._trip(f"Consecutive losses: {self._consecutive_losses}")
        else:
            self._consecutive_losses = 0

    def record_error(self) -> None:
        """Record an error."""
        self._error_count += 1
        self._last_error_time = time.time()

        # Check error rate
        if self._error_count >= self._max_errors_per_minute:
            self._trip(f"Error rate exceeded: {self._error_count} errors")

    def _trip(self, reason: str) -> None:
        """Trip the circuit breaker."""
        if not self._tripped:
            self._tripped = True
            self._trip_reason = reason
            self._trip_time = time.time()
            log.critical("CIRCUIT BREAKER TRIPPED: %s", reason)

    def reset(self) -> None:
        """Reset the circuit breaker (manual intervention required)."""
        self._tripped = False
        self._trip_reason = ""
        self._consecutive_losses = 0
        self._error_count = 0
        log.warning("CIRCUIT BREAKER RESET")

    @property
    def is_tripped(self) -> bool:
        return self._tripped

    @property
    def trip_reason(self) -> str:
        return self._trip_reason

    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status."""
        return {
            "tripped": self._tripped,
            "reason": self._trip_reason,
            "trip_time": self._trip_time,
            "consecutive_losses": self._consecutive_losses,
            "error_count": self._error_count,
        }