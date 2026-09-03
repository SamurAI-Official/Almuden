"""Strategy registry — maps strategy names to Strategy classes."""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type

from config import Settings
from strategy_lab.base import Strategy

log = logging.getLogger(__name__)


class StrategyRegistry:
    """Registry of available strategies."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._strategies: Dict[str, Type[Strategy]] = {}
        self._instances: Dict[str, Strategy] = {}

    def register(self, name: str, strategy_cls: Type[Strategy]) -> None:
        """Register a strategy class under a name."""
        self._strategies[name] = strategy_cls
        # Clear cached instance if re-registering
        self._instances.pop(name, None)
        log.debug("Registered strategy: %s", name)

    def get(self, name: str) -> Strategy:
        """Get a strategy instance by name."""
        if name not in self._instances:
            if name not in self._strategies:
                raise KeyError(
                    f"Unknown strategy: {name!r}. "
                    f"Available: {list(self._strategies.keys())}"
                )
            self._instances[name] = self._strategies[name](self._settings)
        return self._instances[name]

    def available(self) -> List[str]:
        """List all registered strategy names."""
        return list(self._strategies.keys())

    def scan_all(
        self,
        books,
        environment=None,
        strategies: Optional[List[str]] = None,
    ):
        """Scan with all or specified strategies."""
        from strategy_lab.base import Opportunity

        results = {}
        names = strategies or self.available()
        for name in names:
            try:
                strategy = self.get(name)
                opps = strategy.scan(books, environment)
                # Ensure opportunities have the strategy name
                for opp in opps:
                    opp.strategy = name
                results[name] = opps
            except Exception as exc:
                log.warning("Strategy %s failed: %s", name, exc)
                results[name] = []
        return results