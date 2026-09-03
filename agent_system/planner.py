"""Planner — the orchestrator agent.

The planner observes the environment and decides what the system should do.
It is the only component that controls which strategies run and when.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config import Settings
from environment import EnvironmentState

log = logging.getLogger(__name__)


@dataclass
class Plan:
    """A plan produced by the planner."""
    strategies: List[str] = field(default_factory=list)
    risk_multiplier: float = 1.0
    should_trade: bool = True
    reasoning: str = ""
    urgency: str = "normal"  # low, normal, high

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategies": self.strategies,
            "risk_multiplier": self.risk_multiplier,
            "should_trade": self.should_trade,
            "reasoning": self.reasoning,
            "urgency": self.urgency,
        }


class Planner:
    """Orchestrator agent that decides what the system does each cycle."""

    def __init__(self, settings: Settings, brain=None, memory=None) -> None:
        self._settings = settings
        self._brain = brain
        self._memory = memory

    async def plan(
        self,
        environment_state: EnvironmentState,
        opportunities: Optional[Dict[str, list]] = None,
    ) -> Plan:
        """Create a plan based on the current environment."""
        # Try LLM-based planning if brain is available
        if self._brain is not None:
            try:
                llm_plan = await self._llm_plan(environment_state, opportunities)
                if llm_plan is not None:
                    return llm_plan
            except Exception as exc:
                log.debug("LLM planning failed, falling back to rules: %s", exc)

        # Fallback: rule-based planning
        return self._rule_based_plan(environment_state)

    async def _llm_plan(
        self,
        env_state: EnvironmentState,
        opportunities: Optional[Dict[str, list]] = None,
    ) -> Optional[Plan]:
        """Get a plan from the LLM."""
        if self._brain is None:
            return None

        # Build context
        recent_memory = None
        if self._memory is not None:
            recent_memory = self._memory.get_short_term_summary()

        # Format user message
        user_msg = (
            f"Regime: {env_state.regime.value}\n"
            f"Healthy venues: {env_state.healthy_venues}\n"
            f"News alerts: {len(env_state.news)}\n"
            f"Critical news: {len(env_state.critical_news)}\n"
            f"Exchange health: {env_state.exchange_health}\n"
            f"Opportunities: {opportunities}\n"
            f"Recent memory: {recent_memory}\n"
            f"\nWhat should we do?"
        )

        response = await self._brain.think_json("planner", user_msg)
        if response is None:
            return None

        return Plan(
            strategies=response.get("strategies", ["cross_venue"]),
            risk_multiplier=float(response.get("risk_multiplier", 1.0)),
            should_trade=bool(response.get("should_trade", True)),
            reasoning=str(response.get("reasoning", "")),
            urgency=str(response.get("urgency", "normal")),
        )

    def _rule_based_plan(self, env_state: EnvironmentState) -> Plan:
        """Fallback rule-based planning."""
        plan = Plan()

        # Critical news: don't trade
        if env_state.has_critical_news:
            plan.should_trade = False
            plan.reasoning = "Critical news detected - suppressing trades"
            plan.strategies = []
            return plan

        # Unhealthy venues: only use healthy ones
        healthy = env_state.healthy_venues
        if len(healthy) < 2:
            plan.should_trade = False
            plan.reasoning = f"Only {len(healthy)} healthy venue(s) - need at least 2"
            plan.strategies = []
            return plan

        # Regime-based strategy selection
        regime = env_state.regime.value
        if regime == "trending":
            plan.strategies = ["momentum"]
            plan.risk_multiplier = 0.8
            plan.reasoning = "Trending regime - momentum favored"
        elif regime == "mean_reverting":
            plan.strategies = ["cross_venue", "triangular"]
            plan.risk_multiplier = 1.0
            plan.reasoning = "Mean-reverting regime - arb strategies favored"
        elif regime == "volatile":
            plan.strategies = ["cross_venue"]
            plan.risk_multiplier = 0.5
            plan.reasoning = "Volatile regime - reduced size, higher edge threshold"
        elif regime == "quiet":
            plan.strategies = ["cross_venue"]
            plan.risk_multiplier = 0.7
            plan.reasoning = "Quiet regime - minimal activity"
        else:
            # Unknown: run cross_venue with conservative sizing
            plan.strategies = ["cross_venue"]
            plan.risk_multiplier = 0.7
            plan.reasoning = "Unknown regime - conservative default"

        return plan