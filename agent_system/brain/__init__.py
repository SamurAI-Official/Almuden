"""Brain facade — unified LLM reasoning interface."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from config import Settings
from agent_system.brain.ollama import OllamaClient
from agent_system.brain.prompts import PromptLibrary

log = logging.getLogger(__name__)


class Brain:
    """High-level reasoning interface."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ollama = OllamaClient(settings)

    async def is_available(self) -> bool:
        """Check if LLM is available."""
        return await self._ollama.is_available()

    async def think(self, role: str, user_message: str, temperature: float = 0.7) -> Optional[str]:
        """Get a text response from the LLM for a given role."""
        system_prompt = PromptLibrary.get(role)
        return await self._ollama.chat(system_prompt, user_message, temperature=temperature)

    async def think_json(self, role: str, user_message: str, temperature: float = 0.3) -> Optional[Dict[str, Any]]:
        """Get a JSON response from the LLM."""
        system_prompt = PromptLibrary.get(role)
        response = await self._ollama.chat(system_prompt, user_message, temperature=temperature, json_mode=True)

        if response is None:
            return None

        # Try to parse JSON from response
        try:
            # Handle markdown code blocks
            if "```" in response:
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            return json.loads(response.strip())
        except json.JSONDecodeError:
            log.warning("Failed to parse JSON from LLM response: %s", response[:200])
            return None

    async def close(self) -> None:
        """Cleanup."""
        pass