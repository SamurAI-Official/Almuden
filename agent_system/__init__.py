"""Agent System — brain, memory, and planner for autonomous operation.

The agent system observes the environment and produces plans that
the engine executes. It is the cognitive core of AlMuden.
"""
from __future__ import annotations

import logging
from typing import Optional

from config import Settings
from agent_system.brain import Brain
from agent_system.memory import Memory
from agent_system.planner import Planner, Plan
from environment import EnvironmentState

log = logging.getLogger(__name__)


class AgentSystem:
    """Cognitive core — observes environment and produces plans."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._brain = Brain(settings)
        self._memory = Memory(settings)
        self._planner = Planner(settings, brain=self._brain, memory=self._memory)
        self._last_plan: Optional[Plan] = None

    @property
    def brain(self) -> Brain:
        return self._brain

    @property
    def memory(self) -> Memory:
        return self._memory

    @property
    def last_plan(self) -> Optional[Plan]:
        return self._last_plan

    async def perceive(self, environment_state: EnvironmentState) -> Plan:
        """Observe the environment and produce a plan."""
        # Store observation in short-term memory
        self._memory.observe({
            "regime": environment_state.regime.value,
            "books": len(environment_state.market.books),
            "news": len(environment_state.news),
            "healthy_venues": environment_state.healthy_venues,
        })

        # Record regime changes in episodic memory
        if self._last_plan:
            last_regime = self._last_plan.reasoning
            current_regime = environment_state.regime.value
            if current_regime not in last_regime:
                self._memory.record_event(
                    "regime_change",
                    f"Regime changed to {current_regime}",
                    importance=0.8,
                    metadata={"regime": current_regime},
                )

        # Get plan from planner
        plan = await self._planner.plan(environment_state)
        self._last_plan = plan

        return plan

    async def learn(self, trade_outcomes: list) -> None:
        """Learn from trade outcomes."""
        for outcome in trade_outcomes:
            self._memory.record_trade(outcome)

            # Record significant events
            pnl = outcome.get("actual_pnl", 0)
            if abs(pnl) > 10:  # Significant PnL
                self._memory.record_event(
                    "significant_pnl",
                    f"Trade PnL: {pnl:.4f}",
                    importance=min(1.0, abs(pnl) / 100),
                    metadata=outcome,
                )

    async def close(self) -> None:
        """Cleanup."""
        await self._brain.close()