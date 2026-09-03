"""Ollama LLM client — interface to a local Ollama instance.

Supports chat, generate, and embeddings endpoints.
Gracefully degrades when Ollama is unavailable.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiohttp

from config import Settings

log = logging.getLogger(__name__)


class OllamaClient:
    """Async client for Ollama LLM inference."""

    def __init__(
        self,
        settings: Settings,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        self._settings = settings
        self._base_url = base_url or getattr(settings, 'ollama_url', 'http://localhost:11434')
        self._model = model or getattr(settings, 'ollama_model', 'llama3')
        self._available = None  # Lazy-checked

    async def is_available(self) -> bool:
        """Check if Ollama server is reachable."""
        if self._available is not None:
            return self._available

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self._base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    self._available = resp.status == 200
        except Exception:
            self._available = False

        if not self._available:
            log.warning("Ollama not available at %s — LLM features disabled", self._base_url)

        return self._available

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> Optional[str]:
        """Send a chat message and return the response."""
        if not await self.is_available():
            return None

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {"temperature": temperature},
        }

        if json_mode:
            payload["format"] = "json"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        log.warning("Ollama chat error: %s", resp.status)
                        return None
                    data = await resp.json()
                    return data.get("message", {}).get("content", "")
        except Exception as exc:
            log.warning("Ollama chat failed: %s", exc)
            return None

    async def generate(self, prompt: str, temperature: float = 0.7) -> Optional[str]:
        """Send a single prompt (no system message) and return the response."""
        if not await self.is_available():
            return None

        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/api/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return data.get("response", "")
        except Exception as exc:
            log.warning("Ollama generate failed: %s", exc)
            return None

    async def embed(self, text: str) -> Optional[List[float]]:
        """Generate an embedding vector for text."""
        if not await self.is_available():
            return None

        payload = {
            "model": self._model,
            "prompt": text,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/api/embeddings",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return data.get("embedding")
        except Exception as exc:
            log.warning("Ollama embed failed: %s", exc)
            return None