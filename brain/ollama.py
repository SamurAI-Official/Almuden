"""Ollama client (deferred).

Interface to a local Ollama instance for LLM inference. Implemented in a later phase.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class OllamaClient:
    """Stub — raises if used before implementation."""

    def __init__(self, *args, **kwargs) -> None:
        log.warning("OllamaClient is not yet implemented (deferred)")
