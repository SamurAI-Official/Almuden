"""Hypothesis — a testable proposal for strategy improvement.

A hypothesis is immutable once created. It represents a proposed change
to a strategy's parameters or logic, with an expected outcome.

The hypothesis does not modify the strategy. It is tested through an
Experiment, which produces Evidence.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Hypothesis:
    """A testable hypothesis about strategy improvement.

    Attributes:
        strategy_id: The strategy this hypothesis targets
        strategy_version: The version the hypothesis is based on
        description: Human-readable explanation of the proposed change
        parameter_changes: Dict of parameter name → new value
        expected_improvement: What improvement is expected and why
        market_regime: The regime this hypothesis applies to (e.g., "trending", "ranging")
        created_at: Timestamp of creation
        id: Unique identifier
    """
    strategy_id: str
    description: str
    strategy_version: str = "1.0.0"
    parameter_changes: Dict[str, Any] = field(default_factory=dict)
    expected_improvement: str = ""
    market_regime: str = ""
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: f"hyp_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "description": self.description,
            "parameter_changes": self.parameter_changes,
            "expected_improvement": self.expected_improvement,
            "market_regime": self.market_regime,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Hypothesis:
        """Deserialize from dictionary."""
        return cls(
            strategy_id=data["strategy_id"],
            description=data["description"],
            strategy_version=data.get("strategy_version", "1.0.0"),
            parameter_changes=data.get("parameter_changes", {}),
            expected_improvement=data.get("expected_improvement", ""),
            market_regime=data.get("market_regime", ""),
            created_at=data.get("created_at", time.time()),
            id=data.get("id", f"hyp_{uuid.uuid4().hex[:8]}"),
        )