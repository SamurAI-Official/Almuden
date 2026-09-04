"""Evidence — an immutable record of experiment outcomes.

Evidence is produced by experiments and consumed by the lifecycle engine
to make promotion/demotion decisions. Once created, evidence cannot be
modified — it is a permanent record of what was observed.

Evidence distinguishes between:
- Fact: What actually happened (metrics, trades, P&L)
- Inference: What we conclude from it (recommendation, confidence)
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class EvidenceType:
    EXPERIMENT_RESULT = "experiment_result"
    OBSERVATION = "observation"
    HEALTH_CHECK = "health_check"
    DEMOTION_TRIGGER = "demotion_trigger"


class Recommendation:
    PROMOTE = "promote"
    HOLD = "hold"
    DEMOTE = "demote"
    INVESTIGATE = "investigate"
    QUARANTINE = "quarantine"


@dataclass
class Evidence:
    """Immutable evidence record.

    Attributes:
        experiment_id: The experiment that produced this evidence (if any)
        strategy_id: The strategy this evidence concerns
        evidence_type: Type of evidence
        facts: What actually happened (metrics, trades, P&L)
        inference: What we conclude (recommendation, confidence)
        confidence: How confident we are in the inference (0-1)
        source: What produced this evidence (experiment, observation, etc.)
        market_regime: The regime during evidence collection
        strategy_version: The strategy version at time of evidence
        created_at: When the evidence was created
        id: Unique identifier
    """
    strategy_id: str
    evidence_type: str = EvidenceType.EXPERIMENT_RESULT
    experiment_id: str = ""
    facts: Dict[str, Any] = field(default_factory=dict)
    inference: str = Recommendation.HOLD
    confidence: float = 0.5
    source: str = ""
    market_regime: str = ""
    strategy_version: str = "1.0.0"
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:8]}")

    @property
    def is_positive(self) -> bool:
        """True if the evidence supports promotion."""
        return self.inference == Recommendation.PROMOTE and self.confidence > 0.7

    @property
    def is_negative(self) -> bool:
        """True if the evidence suggests demotion or investigation."""
        return self.inference in (Recommendation.DEMOTE, Recommendation.QUARANTINE)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "experiment_id": self.experiment_id,
            "strategy_id": self.strategy_id,
            "evidence_type": self.evidence_type,
            "facts": self.facts,
            "inference": self.inference,
            "confidence": self.confidence,
            "source": self.source,
            "market_regime": self.market_regime,
            "strategy_version": self.strategy_version,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Evidence:
        """Deserialize from dictionary."""
        return cls(
            strategy_id=data["strategy_id"],
            evidence_type=data.get("evidence_type", EvidenceType.EXPERIMENT_RESULT),
            experiment_id=data.get("experiment_id", ""),
            facts=data.get("facts", {}),
            inference=data.get("inference", Recommendation.HOLD),
            confidence=data.get("confidence", 0.5),
            source=data.get("source", ""),
            market_regime=data.get("market_regime", ""),
            strategy_version=data.get("strategy_version", "1.0.0"),
            created_at=data.get("created_at", time.time()),
            id=data.get("id", f"ev_{uuid.uuid4().hex[:8]}"),
        )