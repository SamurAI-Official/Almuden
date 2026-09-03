"""RAG pipeline — retrieval-augmented generation over memories.

Searches across episodic and long-term memory using embeddings
to find relevant context for LLM prompts.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config import Settings

log = logging.getLogger(__name__)


class RAGPipeline:
    """Retrieval-augmented generation over memory stores."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._embedding_service = None

    def _get_embedding_service(self):
        """Lazy-load embedding service."""
        if self._embedding_service is None:
            try:
                from agent_system.brain.embeddings import EmbeddingService
                self._embedding_service = EmbeddingService(self._settings)
            except Exception:
                self._embedding_service = None
        return self._embedding_service

    async def recall(
        self,
        query: str,
        episodic_memory=None,
        long_term_memory=None,
        k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant memories for a query.

        Returns the top-k most relevant memory items with their sources.
        """
        results = []

        # Search episodic memory
        if episodic_memory is not None:
            events = episodic_memory.get_events(limit=k * 2)
            for event in events:
                relevance = self._simple_relevance(query, event.get("description", ""))
                if relevance > 0:
                    results.append({
                        "source": "episodic",
                        "relevance": relevance,
                        "data": event,
                    })

        # Search long-term memory
        if long_term_memory is not None:
            lessons = long_term_memory.get_lessons(limit=k * 2)
            for lesson in lessons:
                relevance = self._simple_relevance(query, lesson.get("content", ""))
                if relevance > 0:
                    results.append({
                        "source": "long_term",
                        "relevance": relevance,
                        "data": lesson,
                    })

        # Sort by relevance and return top-k
        results.sort(key=lambda r: r["relevance"], reverse=True)
        return results[:k]

    @staticmethod
    def _simple_relevance(query: str, text: str) -> float:
        """Simple word-overlap relevance score."""
        if not query or not text:
            return 0.0

        query_words = set(query.lower().split())
        text_words = set(text.lower().split())

        if not query_words:
            return 0.0

        overlap = query_words & text_words
        return len(overlap) / len(query_words)

    async def build_context(
        self,
        query: str,
        episodic_memory=None,
        long_term_memory=None,
        k: int = 5,
    ) -> str:
        """Build a context string from relevant memories."""
        results = await self.recall(query, episodic_memory, long_term_memory, k)

        if not results:
            return ""

        context_parts = []
        for result in results:
            source = result["source"]
            data = result["data"]

            if source == "episodic":
                context_parts.append(
                    f"[{data.get('type', 'event')}] {data.get('description', '')}"
                )
            elif source == "long_term":
                context_parts.append(
                    f"[{data.get('category', 'lesson')}] {data.get('content', '')}"
                )

        return "\n".join(context_parts)