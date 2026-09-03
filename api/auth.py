"""API authentication (deferred).

Auth middleware for the REST API. Implemented in a later phase.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class AuthMiddleware:
    """Stub — raises if used before implementation."""

    def __init__(self, *args, **kwargs) -> None:
        log.warning("AuthMiddleware is not yet implemented (deferred)")
