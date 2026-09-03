"""Audit log — immutable append-only record of every decision.

Logs every signal, trade, risk check, and system event for
post-trade analysis and compliance.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from config import Settings

log = logging.getLogger(__name__)


class AuditLog:
    """Immutable audit trail."""

    def __init__(self, settings: Settings, log_path: Optional[str] = None) -> None:
        self._settings = settings
        self._log_path = log_path or os.path.join(
            getattr(settings, 'memory_dir', '.memory'), 'audit.jsonl'
        )
        os.makedirs(os.path.dirname(self._log_path), exist_ok=True)

    def record(self, event_type: str, data: Dict[str, Any]) -> None:
        """Record an immutable audit entry."""
        entry = {
            "timestamp": time.time(),
            "type": event_type,
            "data": data,
        }

        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            log.error("Failed to write audit log: %s", exc)

    def get_entries(
        self,
        event_type: Optional[str] = None,
        limit: int = 100,
        since: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Get audit entries."""
        entries = []

        if not os.path.exists(self._log_path):
            return entries

        try:
            with open(self._log_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if event_type and entry.get("type") != event_type:
                            continue
                        if since and entry.get("timestamp", 0) < since:
                            continue
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            log.warning("Failed to read audit log: %s", exc)

        entries.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
        return entries[:limit]

    def get_summary(self) -> Dict[str, Any]:
        """Get audit summary."""
        entries = self.get_entries(limit=1000)
        if not entries:
            return {"count": 0, "types": {}}

        type_counts: Dict[str, int] = {}
        for entry in entries:
            t = entry.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "count": len(entries),
            "types": type_counts,
            "first_entry": entries[-1] if entries else None,
            "latest_entry": entries[0] if entries else None,
        }