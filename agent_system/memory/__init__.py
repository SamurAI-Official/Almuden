"""Memory facade — unified memory system.

Combines short-term, long-term, episodic, and RAG into a single interface.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config import Settings
from agent_system.memory.short_term import ShortTermMemory
from agent_system.memory.long_term import LongTermMemory
from agent_system.memory.episodic import EpisodicMemory
from agent_system.memory.rag import RAGPipeline

log = logging.getLogger(__name__)


class Memory:
    """Unified memory system."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._short_term = ShortTermMemory(capacity=getattr(settings, 'short_term_capacity', 50))
        self._long_term = LongTermMemory(settings)
        self._episodic = EpisodicMemory(settings)
        self._rag = RAGPipeline(settings)

    def observe(self, observation: Dict[str, Any]) -> None:
        """Add an observation to short-term memory."""
        self._short_term.add(observation)

    def record_trade(self, trade_data: Dict[str, Any]) -> None:
        """Record a completed trade in long-term memory."""
        self._long_term.record_trade(trade_data)

    def record_lesson(self, category: str, content: str, importance: float = 1.0) -> None:
        """Record a lesson learned."""
        self._long_term.record_lesson(category, content, importance)

    def record_event(
        self,
        event_type: str,
        description: str,
        importance: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a notable event in episodic memory."""
        self._episodic.record_event(event_type, description, importance, metadata)

    async def recall(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        """Retrieve relevant memories for a query."""
        return await self._rag.recall(
            query,
            episodic_memory=self._episodic,
            long_term_memory=self._long_term,
            k=k,
        )

    async def build_context(self, query: str, k: int = 5) -> str:
        """Build a context string from relevant memories."""
        return await self._rag.build_context(
            query,
            episodic_memory=self._episodic,
            long_term_memory=self._long_term,
            k=k,
        )

    def get_short_term_summary(self) -> Dict[str, Any]:
        """Get summary of short-term memory."""
        return self._short_term.summarize()

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get trading performance summary."""
        return self._long_term.get_performance_summary()

    def get_episodic_summary(self) -> Dict[str, Any]:
        """Get episodic memory summary."""
        return self._episodic.summarize()

    def get_recent_observations(self, n: int = 10) -> List[Dict[str, Any]]:
        """Get recent observations from short-term memory."""
        return self._short_term.get_recent(n)