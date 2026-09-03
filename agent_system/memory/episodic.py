"""Episodic memory — event-sourced log of notable market events.

Records significant events like regime shifts, large spreads, delisting news,
and major PnL swings for later recall and reflection.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from config import Settings

log = logging.getLogger(__name__)


class EpisodicMemory:
    """Event log for notable market events."""

    def __init__(self, settings: Settings, log_path: Optional[str] = None) -> None:
        self._settings = settings
        self._log_path = log_path or os.path.join(
            getattr(settings, 'memory_dir', '.memory'), 'episodic.jsonl'
        )
        os.makedirs(os.path.dirname(self._log_path), exist_ok=True)

    def record_event(
        self,
        event_type: str,
        description: str,
        importance: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a notable event."""
        event = {
            "timestamp": time.time(),
            "type": event_type,
            "description": description,
            "importance": importance,
            "metadata": metadata or {},
        }

        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as exc:
            log.warning("Failed to record event: %s", exc)

    def get_events(
        self,
        event_type: Optional[str] = None,
        limit: int = 50,
        since: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Get events, optionally filtered by type and time."""
        events = []

        if not os.path.exists(self._log_path):
            return events

        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        if event_type and event.get("type") != event_type:
                            continue
                        if since and event.get("timestamp", 0) < since:
                            continue
                        events.append(event)
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            log.warning("Failed to read events: %s", exc)

        events.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return events[:limit]

    def summarize(self) -> Dict[str, Any]:
        """Summarize episodic memory."""
        events = self.get_events(limit=100)
        if not events:
            return {"count": 0, "events": []}

        type_counts: Dict[str, int] = {}
        for event in events:
            t = event.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "count": len(events),
            "type_counts": type_counts,
            "recent": events[:10],
        }