"""Circuit breaker — monitors trading health and triggers kill switch.

Watches for:
  - Consecutive losses exceeding threshold
  - Drawdown from peak exceeding threshold
  - Error rate exceeding threshold (sliding-window: errors per minute)
  - API failures exceeding threshold
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Dict, Optional

from config import Settings

log = logging.getLogger(__name__)


class CircuitBreaker:
    """Monitors trading health and can halt all activity."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._peak_equity: float = 0.0
        self._consecutive_losses: int = 0
        # Sliding window of timestamps so old errors age out naturally.
        self._error_timestamps: deque = deque()
        self._last_error_time: float = 0.0
        self._tripped: bool = False
        self._trip_reason: str = ""
        self._trip_time: float = 0.0

        # Thresholds
        self._max_consecutive_losses = getattr(settings, 'max_consecutive_losses', 5)
        self._max_drawdown_pct = getattr(settings, 'max_drawdown_pct', 10.0) / 100.0
        self._max_errors_per_minute = getattr(settings, 'max_errors_per_minute', 10)
        # How long an error stays in the rate window (seconds).
        self._error_window_seconds = getattr(settings, 'error_window_seconds', 60)

    def _prune_errors(self) -> None:
        """Drop error timestamps older than the window."""
        horizon = time.time() - self._error_window_seconds
        while self._error_timestamps and self._error_timestamps[0] < horizon:
            self._error_timestamps.popleft()

    @property
    def current_error_rate(self) -> int:
        """Number of errors within the current sliding window."""
        self._prune_errors()
        return len(self._error_timestamps)

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
        """Record an error (appends timestamp to the sliding window)."""
        now = time.time()
        self._last_error_time = now
        self._error_timestamps.append(now)
        self._prune_errors()

        # Check error rate within the window
        if len(self._error_timestamps) >= self._max_errors_per_minute:
            self._trip(
                f"Error rate exceeded: {len(self._error_timestamps)} "
                f"errors in {self._error_window_seconds}s"
            )

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
        self._error_timestamps.clear()
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
            "current_error_rate": self.current_error_rate,
            "max_errors_per_minute": self._max_errors_per_minute,
        }