"""Strategy registry — maps strategy names to callables.

Today there is one strategy: cross-venue inventory arbitrage. The registry
pattern keeps the engine decoupled from specific strategies so new ones
(triangular, momentum, agent-driven) can be added without engine changes.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict

log = logging.getLogger(__name__)

# A strategy is an async callable(settings, books) -> list[dict]
StrategyFn = Callable[..., Awaitable[list]]

_REGISTRY: Dict[str, StrategyFn] = {}


def register(name: str) -> Callable[[StrategyFn], StrategyFn]:
    """Decorator to register a strategy under *name*."""

    def decorator(fn: StrategyFn) -> StrategyFn:
        _REGISTRY[name] = fn
        return fn

    return decorator


def get_strategy(name: str) -> StrategyFn:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown strategy: {name!r}. Available: {list(_REGISTRY)}")
    return _REGISTRY[name]


def available() -> list:
    return list(_REGISTRY.keys())
