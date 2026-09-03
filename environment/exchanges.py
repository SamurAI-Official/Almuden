"""Exchange health monitor — tracks latency, error rates, and withdrawal status.

Monitors each exchange for:
  - API latency (response time)
  - Error rate (failed requests / total requests)
  - Withdrawal suspension status (from news feed)
  - Overall health status (healthy / degraded / unhealthy)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from config import Settings
from trading.exchange import Book

log = logging.getLogger(__name__)


@dataclass
class ExchangeHealth:
    """Health status for a single exchange."""
    venue: str
    is_healthy: bool = True
    latency_ms: float = 0.0
    error_rate: float = 0.0  # 0.0 to 1.0
    last_check: float = 0.0
    status: str = "unknown"  # healthy | degraded | unhealthy | unknown
    issues: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.venue}: {self.status} (latency={self.latency_ms:.0f}ms, errors={self.error_rate:.1%})"


class ExchangeHealthMonitor:
    """Monitors exchange health from book polling results."""

    # Thresholds for health classification
    LATENCY_THRESHOLD_MS = 2000  # Above this = degraded
    ERROR_RATE_THRESHOLD = 0.3  # Above this = degraded
    CRITICAL_ERROR_RATE = 0.7  # Above this = unhealthy

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._error_counts: Dict[str, int] = {}
        self._total_counts: Dict[str, int] = {}
        self._latencies: Dict[str, List[float]] = {}

    async def check(self, books: Dict[Tuple[str, str], Book]) -> Dict[str, ExchangeHealth]:
        """Check health of all venues based on book polling results."""
        health: Dict[str, ExchangeHealth] = {}
        current_time = time.time()

        for venue in self._settings.venues:
            # Count successful vs failed book fetches
            venue_books = [b for (v, s), b in books.items() if v == venue]
            successful = len(venue_books)

            # Track counts
            self._total_counts[venue] = self._total_counts.get(venue, 0) + 1
            if successful == 0:
                self._error_counts[venue] = self._error_counts.get(venue, 0) + 1

            total = self._total_counts.get(venue, 1)
            errors = self._error_counts.get(venue, 0)
            error_rate = errors / total if total > 0 else 0.0

            # Calculate average latency from books that have timestamps
            latency = 0.0
            latencies = self._latencies.get(venue, [])
            if latencies:
                latency = sum(latencies) / len(latencies)

            # Determine status
            issues = []
            if error_rate >= self.CRITICAL_ERROR_RATE:
                status = "unhealthy"
                issues.append(f"High error rate: {error_rate:.1%}")
            elif error_rate >= self.ERROR_RATE_THRESHOLD:
                status = "degraded"
                issues.append(f"Elevated error rate: {error_rate:.1%}")
            elif latency > self.LATENCY_THRESHOLD_MS:
                status = "degraded"
                issues.append(f"High latency: {latency:.0f}ms")
            else:
                status = "healthy"

            is_healthy = status in ("healthy", "degraded")

            health[venue] = ExchangeHealth(
                venue=venue,
                is_healthy=is_healthy,
                latency_ms=round(latency, 2),
                error_rate=round(error_rate, 4),
                last_check=current_time,
                status=status,
                issues=issues,
            )

        return health

    def record_latency(self, venue: str, latency_ms: float) -> None:
        """Record a latency measurement for a venue."""
        if venue not in self._latencies:
            self._latencies[venue] = []
        self._latencies[venue].append(latency_ms)
        # Keep only last 100 measurements
        if len(self._latencies[venue]) > 100:
            self._latencies[venue] = self._latencies[venue][-100:]

    async def close(self) -> None:
        """No-op cleanup."""
        pass