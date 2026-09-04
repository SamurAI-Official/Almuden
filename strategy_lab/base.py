"""Strategy base class and normalized Opportunity type."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config import Settings
from environment import EnvironmentState
from trading.exchange import Book

log = logging.getLogger(__name__)


@dataclass
class Opportunity:
    """Normalized opportunity output from any strategy.

    This is the universal currency of the strategy lab — every strategy
    produces these, and the backtester/executor consume them.
    """
    strategy: str  # Name of the strategy that produced this
    symbol: str  # Primary symbol (e.g., "ERG/USDT")
    venues: List[str]  # Venues involved
    expected_edge_bps: float  # Gross expected edge in bps
    confidence: float = 0.5  # 0.0 to 1.0
    size: float = 0.0  # Recommended size in base asset
    metadata: Dict[str, Any] = field(default_factory=dict)  # Strategy-specific details

    @property
    def is_viable(self) -> bool:
        """True if this opportunity has a positive edge."""
        return self.expected_edge_bps > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "venues": self.venues,
            "expected_edge_bps": self.expected_edge_bps,
            "confidence": self.confidence,
            "size": self.size,
            "metadata": self.metadata,
        }


class Strategy(ABC):
    """Abstract base class for all strategies.

    A strategy takes market data and environment state, then returns
    a list of normalized Opportunity objects.
    """

    name: str = "base"

    @property
    @abstractmethod
    def risk_class(self) -> str:
        """Risk class for capital allocation (e.g. 'arbitrage', 'pump')."""
        ...

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @abstractmethod
    def scan(
        self,
        books: Dict[Tuple[str, str], Book],
        environment: Optional[EnvironmentState] = None,
    ) -> List[Opportunity]:
        """Scan for opportunities. Must be implemented by each strategy."""
        ...

    def evaluate(self, opportunities: List[Opportunity]) -> List[Opportunity]:
        """Filter and score opportunities. Can be overridden."""
        return [opp for opp in opportunities if opp.is_viable]