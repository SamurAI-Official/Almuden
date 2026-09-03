"""Strategy Lab — a namespace for strategies, backtesting, and simulation.

Each strategy implements the same interface:
    scan(books, environment) -> list[Opportunity]

The backtester and simulator can run any strategy without knowing its internals.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from config import Settings
from strategy_lab.base import Opportunity, Strategy
from strategy_lab.registry import StrategyRegistry

log = logging.getLogger(__name__)


def create_registry(settings: Settings) -> StrategyRegistry:
    """Create and populate the strategy registry with all known strategies."""
    registry = StrategyRegistry(settings)

    # Register built-in strategies
    try:
        from strategy_lab.cross_venue import CrossVenueStrategy
        registry.register("cross_venue", CrossVenueStrategy)
    except Exception as exc:
        log.warning("Failed to register cross_venue strategy: %s", exc)

    try:
        from strategy_lab.triangular import TriangularStrategy
        registry.register("triangular", TriangularStrategy)
    except Exception as exc:
        log.warning("Failed to register triangular strategy: %s", exc)

    try:
        from strategy_lab.momentum import MomentumStrategy
        registry.register("momentum", MomentumStrategy)
    except Exception as exc:
        log.warning("Failed to register momentum strategy: %s", exc)

    return registry


__all__ = ["create_registry", "Opportunity", "Strategy", "StrategyRegistry"]