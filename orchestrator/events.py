"""Async event bus for inter-module communication.

Modules subscribe to topics (``book``, ``signal``, ``fill``, ``error``) and
publish payloads. Decouples the scanner from the executor from the UI.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List

log = logging.getLogger(__name__)

Handler = Callable[[str, Dict[str, Any]], Awaitable[None]]


class EventBus:
    """Simple async pub/sub with fire-and-forget delivery."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue = None  # type: ignore[assignment]

    async def start(self) -> None:
        self._queue = asyncio.Queue()
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        self._running = False
        await self._queue.put(None)  # unblock the loop
        await self._task

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)

    async def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        if self._queue is None:
            await self.start()
        await self._queue.put((topic, payload))

    async def _dispatch_loop(self) -> None:
        while self._running:
            item = await self._queue.get()
            if item is None:
                continue
            topic, payload = item
            for handler in self._subscribers.get(topic, []):
                try:
                    await handler(topic, payload)
                except Exception:
                    log.exception("Event handler error for topic %r", topic)
