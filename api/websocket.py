"""WebSocket feed (deferred).

Real-time WebSocket stream of engine events. Implemented in a later phase.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class WebSocketFeed:
    """Stub — raises if used before implementation."""

    def __init__(self, *args, **kwargs) -> None:
        log.warning("WebSocketFeed is not yet implemented (deferred)")
