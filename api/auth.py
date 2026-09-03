"""API authentication — API-key auth with rate limiting."""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Dict, Optional, Set

import secrets

log = logging.getLogger(__name__)


class AuthMiddleware:
    """API-key authentication with rate limiting."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        rate_limit: int = 60,  # Requests per minute
    ) -> None:
        self._api_key = api_key or os.environ.get("ALMUDEN_API_KEY") or self._generate_key()
        self._rate_limit = rate_limit
        self._request_counts: Dict[str, list] = defaultdict(list)

        if not api_key:
            log.info("Generated API key: %s", self._api_key)

    @staticmethod
    def _generate_key() -> str:
        """Generate a random API key."""
        return secrets.token_urlsafe(32)

    def validate_key(self, provided_key: str) -> bool:
        """Validate an API key."""
        return provided_key == self._api_key

    def check_rate_limit(self, client_ip: str) -> bool:
        """Check if a client has exceeded the rate limit."""
        now = time.time()
        minute_ago = now - 60

        # Clean old entries
        self._request_counts[client_ip] = [
            t for t in self._request_counts[client_ip] if t > minute_ago
        ]

        # Check limit
        if len(self._request_counts[client_ip]) >= self._rate_limit:
            return False

        # Record request
        self._request_counts[client_ip].append(now)
        return True

    @property
    def api_key(self) -> str:
        return self._api_key
