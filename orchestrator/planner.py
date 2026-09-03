"""Cycle planner — decides *what* the engine does each tick.

The planner is intentionally simple today: poll books → scan → evaluate →
execute → rebalance. It is the seam where an LLM-driven planner can later
be injected without touching the engine loop.
"""
from __future__ import annotations

import logging
from typing import List

log = logging.getLogger(__name__)


class Planner:
    """Returns the ordered list of phase names for one engine cycle."""

    PHASES: List[str] = ["poll", "scan", "evaluate", "execute", "rebalance"]

    def plan(self) -> List[str]:
        return list(self.PHASES)
