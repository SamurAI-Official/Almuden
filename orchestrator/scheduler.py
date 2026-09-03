"""Async job scheduler — runs coroutines on fixed intervals."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Dict, List, Tuple

log = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


class Scheduler:
    """Run async jobs forever on fixed intervals."""

    def __init__(self) -> None:
        self._jobs: List[Tuple[str, Job, float]] = []
        self._running = False

    def add_job(self, name: str, job: Job, interval_seconds: float) -> None:
        self._jobs.append((name, job, interval_seconds))

    async def start(self) -> None:
        self._running = True
        tasks = [
            asyncio.create_task(self._run_loop(name, job, interval))
            for name, job, interval in self._jobs
        ]
        await asyncio.gather(*tasks)

    async def stop(self) -> None:
        self._running = False

    async def _run_loop(self, name: str, job: Job, interval: float) -> None:
        while self._running:
            start = time.monotonic()
            try:
                await job()
            except Exception:
                log.exception("Scheduler job %r failed", name)
            elapsed = time.monotonic() - start
            await asyncio.sleep(max(0.0, interval - elapsed))
