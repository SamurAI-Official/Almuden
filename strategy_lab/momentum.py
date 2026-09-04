"""Momentum strategy (stub — Phase 7 agents will implement).

Placeholder for a trend-following strategy that would:
- Detect breakout moves using volume and price momentum
- Enter in the direction of the trend
- Exit when momentum reverses
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from config import Settings
from environment import EnvironmentState
from strategy_lab.base import Opportunity, Strategy
from trading.exchange import Book

log = logging.getLogger(__name__)


class MomentumStrategy(Strategy):
    """Momentum-based strategy stub."""

    name = "momentum"

    @property
    def risk_class(self) -> str:
        return "trend"

    def scan(
        self,
        books: Dict[Tuple[str, str], Book],
        environment: Optional[EnvironmentState] = None,
    ) -> List[Opportunity]:
        """Not yet implemented."""
        log.debug("MomentumStrategy.scan called (not implemented)")
        return []