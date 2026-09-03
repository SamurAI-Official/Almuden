"""WebSocket feed — real-time stream of engine events."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger(__name__)


class WebSocketFeed:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self) -> None:
        self._connections: Set = set()
        self._event_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self, websocket) -> None:
        """Register a new WebSocket connection."""
        self._connections.add(websocket)
        log.info("WebSocket connected. Total: %d", len(self._connections))

    async def disconnect(self, websocket) -> None:
        """Remove a WebSocket connection."""
        self._connections.discard(websocket)
        log.info("WebSocket disconnected. Total: %d", len(self._connections))

    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast an event to all connected clients."""
        message = json.dumps({"type": event_type, "data": data}, default=str)

        disconnected = set()
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.add(ws)

        # Clean up disconnected clients
        for ws in disconnected:
            await self.disconnect(ws)

    async def handler(self, websocket) -> None:
        """Handle a WebSocket connection lifecycle."""
        await self.connect(websocket)
        try:
            async for message in websocket:
                # Handle incoming messages (e.g., ping, subscription changes)
                try:
                    data = json.loads(message)
                    if data.get("type") == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        finally:
            await self.disconnect(websocket)

    @property
    def connection_count(self) -> int:
        return len(self._connections)
