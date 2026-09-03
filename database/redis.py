"""Redis cache with in-memory fallback.

If ``redis`` is not installed or no URL is configured, an in-process dict
is used transparently. This keeps the system runnable with zero infra.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger(__name__)


class MemoryCache:
    """Thread-safe-ish dict cache with TTL support (single-process)."""

    def __init__(self) -> None:
        self._data: dict = {}

    async def get(self, key: str) -> Optional[str]:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.monotonic() > expires_at:
            del self._data[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl: Optional[float] = None) -> None:
        expires = None if ttl is None else time.monotonic() + ttl
        self._data[key] = (value, expires)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class RedisCache:
    """Thin wrapper around redis.asyncio."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis

        self._client = aioredis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> Optional[str]:
        return await self._client.get(key)

    async def set(self, key: str, value: str, ttl: Optional[float] = None) -> None:
        await self._client.set(key, value, ex=int(ttl) if ttl else None)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def close(self) -> None:
        await self._client.close()


def make_cache(url: str = ""):
    """Return a RedisCache if *url* is set and redis is installed, else MemoryCache."""
    if not url:
        log.info("No Redis URL configured — using in-memory cache")
        return MemoryCache()
    try:
        import redis  # noqa: F401

        log.info("Using Redis cache at %s", url)
        return RedisCache(url)
    except ImportError:
        log.warning("redis package not installed — falling back to in-memory cache")
        return MemoryCache()
