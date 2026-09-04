"""Experiment — a controlled test of a hypothesis.

An experiment records:
- What was tested (hypothesis, strategy version)
- How it was tested (dataset, execution model)
- What happened (metrics, robustness score)

Experiments are immutable once completed. They produce Evidence
that the lifecycle engine can use for promotion decisions.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class ExperimentStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INVALID = "invalid"  # e.g., future data leak detected


@dataclass
class Experiment:
    """A controlled test of a strategy hypothesis.

    Attributes:
        hypothesis_id: The hypothesis being tested
        strategy_id: The strategy under test
        strategy_version: The version tested (immutable snapshot)
        dataset_spec: Dataset specification (venue, symbol, timeframe, periods)
        execution_model: How fills were simulated (shadow, paper, replay)
        parameters_before: Strategy parameters before the change
        parameters_after: Strategy parameters after the change
        metrics: Computed performance metrics
        robustness_score: Composite robustness score (0-1)
        status: Current status
        created_at: When the experiment was created
        completed_at: When the experiment finished
        id: Unique identifier
    """
    hypothesis_id: str
    strategy_id: str
    strategy_version: str = "1.0.0"
    dataset_spec: Dict[str, Any] = field(default_factory=dict)
    execution_model: str = "shadow"
    parameters_before: Dict[str, Any] = field(default_factory=dict)
    parameters_after: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)
    robustness_score: float = 0.0
    status: str = ExperimentStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    id: str = field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:8]}")

    def complete(self, metrics: Dict[str, float], robustness_score: float) -> None:
        """Mark the experiment as completed with results."""
        self.metrics = metrics
        self.robustness_score = robustness_score
        self.status = ExperimentStatus.COMPLETED
        self.completed_at = time.time()

    def fail(self, reason: str = "") -> None:
        """Mark the experiment as failed."""
        self.status = ExperimentStatus.FAILED
        self.completed_at = time.time()
        if reason:
            self.metrics["failure_reason"] = reason

    def invalidate(self, reason: str) -> None:
        """Mark the experiment as invalid (e.g., future data leak)."""
        self.status = ExperimentStatus.INVALID
        self.completed_at = time.time()
        self.metrics["invalid_reason"] = reason

    @property
    def is_valid(self) -> bool:
        """True if the experiment completed without issues."""
        return self.status == ExperimentStatus.COMPLETED

    @property
    def duration_seconds(self) -> Optional[float]:
        """How long the experiment took to run."""
        if self.completed_at and self.created_at:
            return self.completed_at - self.created_at
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "hypothesis_id": self.hypothesis_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "dataset_spec": self.dataset_spec,
            "execution_model": self.execution_model,
            "parameters_before": self.parameters_before,
            "parameters_after": self.parameters_after,
            "metrics": self.metrics,
            "robustness_score": self.robustness_score,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Experiment:
        """Deserialize from dictionary."""
        exp = cls(
            hypothesis_id=data["hypothesis_id"],
            strategy_id=data["strategy_id"],
            strategy_version=data.get("strategy_version", "1.0.0"),
            dataset_spec=data.get("dataset_spec", {}),
            execution_model=data.get("execution_model", "shadow"),
            parameters_before=data.get("parameters_before", {}),
            parameters_after=data.get("parameters_after", {}),
        )
        exp.metrics = data.get("metrics", {})
        exp.robustness_score = data.get("robustness_score", 0.0)
        exp.status = data.get("status", ExperimentStatus.PENDING)
        exp.created_at = data.get("created_at", time.time())
        exp.completed_at = data.get("completed_at")
        exp.id = data.get("id", f"exp_{uuid.uuid4().hex[:8]}")
        return exp