"""Embedding service (deferred).

Text embedding generation for the RAG memory layer. Implemented in a later phase.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class EmbeddingService:
    """Stub — raises if used before implementation."""

    def __init__(self, *args, **kwargs) -> None:
        log.warning("EmbeddingService is not yet implemented (deferred)")
