"""Market regime detection — classifies current market conditions.

Regime types:
  - TRENDING: sustained directional movement, momentum strategies favored
  - MEAN_REVERTTING: prices oscillate around mean, arb strategies favored
  - VOLATILE: large price swings, wider stops and smaller sizes needed
  - QUIET: low activity, tight spreads, minimal opportunity
  - UNKNOWN: insufficient data to classify
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from config import Settings
from trading.exchange import Book

log = logging.getLogger(__name__)


class Regime(Enum):
    TRENDING = "trending"
    MEAN_REVERTTING = "mean_reverting"
    VOLATILE = "volatile"
    QUIET = "quiet"
    UNKNOWN = "unknown"


@dataclass
class RegimeConfig:
    """Configuration for regime detection."""
    lookback: int = 20  # Number of observations to consider
    volatile_threshold: float = 2.0  # Std devs above mean for volatile
    trending_threshold: float = 0.5  # Autocorrelation threshold for trending
    quiet_threshold: float = 0.3  # Threshold for quiet (low volatility)


class RegimeDetector:
    """Detects market regime from order book data.

    Maintains a rolling history of mid-price observations per symbol/venue
    and classifies the current regime.
    """

    def __init__(self, settings: Settings, config: Optional[RegimeConfig] = None) -> None:
        self._config = config or RegimeConfig()
        self._history: Dict[Tuple[str, str], List[float]] = defaultdict(list)

    def detect(self, books: Dict[Tuple[str, str], Book]) -> Regime:
        """Detect regime from current books."""
        # Update history with new mid prices
        self._update_history(books)

        # Need enough data to classify
        all_prices = []
        for history in self._history.values():
            if len(history) >= 3:
                all_prices.extend(history)

        if len(all_prices) < self._config.lookback:
            return Regime.UNKNOWN

        # Calculate returns
        returns = []
        for i in range(1, len(all_prices)):
            if all_prices[i - 1] > 0:
                returns.append((all_prices[i] - all_prices[i - 1]) / all_prices[i - 1])

        if len(returns) < 3:
            return Regime.UNKNOWN

        # Calculate volatility (std dev of returns)
        vol = statistics.stdev(returns) if len(returns) > 1 else 0.0

        # Calculate autocorrelation (trend strength)
        autocorr = self._autocorrelation(returns)

        # Classify
        mean_vol = self._mean_volatility()
        if mean_vol > 0 and vol > mean_vol * self._config.volatile_threshold:
            return Regime.VOLATILE
        elif abs(autocorr) > self._config.trending_threshold:
            return Regime.TRENDING
        elif vol < mean_vol * self._config.quiet_threshold:
            return Regime.QUIET
        else:
            return Regime.MEAN_REVERTTING

    def _update_history(self, books: Dict[Tuple[str, str], Book]) -> None:
        """Update price history with current books."""
        for (venue, symbol), book in books.items():
            if book.mid is not None:
                key = (venue, symbol)
                self._history[key].append(book.mid)
                # Trim to lookback window
                if len(self._history[key]) > self._config.lookback * 2:
                    self._history[key] = self._history[key][-self._config.lookback:]

    def _autocorrelation(self, returns: List[float]) -> float:
        """Calculate lag-1 autocorrelation."""
        if len(returns) < 2:
            return 0.0
        mean = statistics.mean(returns)
        numerator = sum(
            (returns[i] - mean) * (returns[i - 1] - mean)
            for i in range(1, len(returns))
        )
        denominator = sum((r - mean) ** 2 for r in returns)
        if denominator == 0:
            return 0.0
        return numerator / denominator

    def _mean_volatility(self) -> float:
        """Calculate mean volatility across all histories."""
        vols = []
        for history in self._history.values():
            if len(history) >= 3:
                returns = []
                for i in range(1, len(history)):
                    if history[i - 1] > 0:
                        returns.append((history[i] - history[i - 1]) / history[i - 1])
                if len(returns) > 1:
                    vols.append(statistics.stdev(returns))
        return statistics.mean(vols) if vols else 0.0