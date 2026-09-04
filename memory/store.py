"""Memory layer — persistent storage for agent research and market observations.

The memory layer stores:
1. Strategy research (hypotheses, backtest results, parameter changes)
2. Market observations (regime changes, anomaly detections)
3. Failure explanations (why a strategy failed, what was learned)

Uses a simple JSONL file store. Can be upgraded to Qdrant for vector search
when the observation volume warrants it.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from config import Settings

log = logging.getLogger(__name__)


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: str
    category: str  # "research", "observation", "failure", "decision"
    strategy_id: str
    content: str  # human-readable summary
    data: Dict[str, Any] = field(default_factory=dict)  # structured data
    timestamp: float = field(default_factory=time.time)
    importance: float = 1.0  # 0.0-1.0, for retrieval ranking


class MemoryStore:
    """Persistent memory storage for the research agent."""

    def __init__(self, settings: Settings, path: Optional[str] = None) -> None:
        self._settings = settings
        self._path = path or os.path.join(
            getattr(settings, "memory_dir", ".memory"), "research.jsonl"
        )
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._entries: List[MemoryEntry] = []
        self._load()

    def _load(self) -> None:
        """Load existing memory entries."""
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        self._entries.append(MemoryEntry(**data))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except Exception as e:
            log.error("Memory load failed: %s", e)

    def _append(self, entry: MemoryEntry) -> None:
        """Append a single entry to the persistent store."""
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry)) + "\n")
        except Exception as e:
            log.error("Memory append failed: %s", e)

    def store(
        self,
        category: str,
        strategy_id: str,
        content: str,
        data: Optional[Dict[str, Any]] = None,
        importance: float = 1.0,
    ) -> MemoryEntry:
        """Store a memory entry."""
        entry = MemoryEntry(
            id=f"{category}_{int(time.time() * 1000)}",
            category=category,
            strategy_id=strategy_id,
            content=content,
            data=data or {},
            importance=importance,
        )
        self._entries.append(entry)
        self._append(entry)
        return entry

    def query(
        self,
        category: Optional[str] = None,
        strategy_id: Optional[str] = None,
        limit: int = 20,
        min_importance: float = 0.0,
    ) -> List[MemoryEntry]:
        """Query memory entries with optional filters."""
        results = self._entries
        if category:
            results = [e for e in results if e.category == category]
        if strategy_id:
            results = [e for e in results if e.strategy_id == strategy_id]
        results = [e for e in results if e.importance >= min_importance]
        # Sort by timestamp descending, then importance
        results.sort(key=lambda e: (e.importance, e.timestamp), reverse=True)
        return results[:limit]

    def get_strategy_history(self, strategy_id: str) -> Dict[str, Any]:
        """Get complete research history for a strategy."""
        entries = self.query(strategy_id=strategy_id, limit=1000)
        return {
            "strategy_id": strategy_id,
            "total_entries": len(entries),
            "research": [e for e in entries if e.category == "research"],
            "observations": [e for e in entries if e.category == "observation"],
            "failures": [e for e in entries if e.category == "failure"],
            "decisions": [e for e in entries if e.category == "decision"],
        }

    def summarize_recent(self, hours: float = 24.0) -> str:
        """Generate a human-readable summary of recent memory entries."""
        cutoff = time.time() - hours * 3600
        recent = [e for e in self._entries if e.timestamp >= cutoff]
        if not recent:
            return f"No memory entries in the last {hours} hours."

        lines = [f"Memory summary (last {hours}h, {len(recent)} entries):"]
        for entry in recent[-10:]:  # last 10
            lines.append(
                f"  [{entry.category}] {entry.strategy_id}: {entry.content[:80]}"
            )
        return "\n".join(lines)

    def clear(self) -> None:
        """Clear all memory (use with caution)."""
        self._entries.clear()
        if os.path.exists(self._path):
            os.remove(self._path)

    @property
    def size(self) -> int:
        return len(self._entries)