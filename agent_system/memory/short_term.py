"""Short-term memory — ring buffer of recent observations.

Stores the last N cycle states for immediate context.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Deque, Dict, List, Optional

log = logging.getLogger(__name__)


class ShortTermMemory:
    """Ring buffer for recent cycle observations."""

    def __init__(self, capacity: int = 50) -> None:
        self._capacity = capacity
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=capacity)

    def add(self, observation: Dict[str, Any]) -> None:
        """Add a new observation."""
        self._buffer.append(observation)

    def get_recent(self, n: int = 10) -> List[Dict[str, Any]]:
        """Get the n most recent observations."""
        return list(self._buffer)[-n:]

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all observations."""
        return list(self._buffer)

    def clear(self) -> None:
        """Clear all observations."""
        self._buffer.clear()

    @property
    def size(self) -> int:
        return len(self._buffer)

    def summarize(self) -> Dict[str, Any]:
        """Create a summary of recent memory."""
        if not self._buffer:
            return {"count": 0, "latest": None}

        recent = list(self._buffer)[-10:]
        return {
            "count": len(self._buffer),
            "latest": self._buffer[-1] if self._buffer else None,
            "recent_regimes": [r.get("regime", "unknown") for r in recent],
            "recent_books_count": [r.get("books", 0) for r in recent],
        }