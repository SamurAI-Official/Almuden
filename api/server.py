"""API server — Uvicorn entrypoint for the AlMuden API."""
from __future__ import annotations

import logging
from typing import Optional

import uvicorn

from config import Settings
from api.auth import AuthMiddleware
from api.websocket import WebSocketFeed
from api.routes import create_app

log = logging.getLogger(__name__)


class APIServer:
    """FastAPI server wrapper."""

    def __init__(
        self,
        settings: Settings,
        engine=None,
        agent_system=None,
        host: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        self._settings = settings
        self._host = host or getattr(settings, "api_host", "0.0.0.0")
        self._port = port or getattr(settings, "api_port", 8080)

        # Create auth and WebSocket feed
        self._auth = AuthMiddleware(api_key=getattr(settings, "api_key", None))
        self._ws_feed = WebSocketFeed()

        # Create FastAPI app
        self._app = create_app(
            engine=engine,
            agent_system=agent_system,
            auth=self._auth,
            ws_feed=self._ws_feed,
        )

    @property
    def app(self):
        return self._app

    @property
    def ws_feed(self) -> WebSocketFeed:
        return self._ws_feed

    @property
    def api_key(self) -> str:
        return self._auth.api_key

    def attach_engine_events(self, engine) -> None:
        """Forward engine EventBus events to connected WebSocket clients."""
        bus = getattr(engine, "bus", None)
        if bus is None:
            log.warning("Engine has no bus; WebSocket event forwarding disabled.")
            return

        def make_forwarder(topic: str):
            async def forwarder(event: dict) -> None:
                await self._ws_feed.broadcast(topic, event)
            return forwarder

        topics = ["environment", "scan", "execute", "rebalance"]
        for topic in topics:
            bus.subscribe(topic, make_forwarder(topic))
        log.info("WebSocket feed attached to engine events: %s", topics)

    async def serve(self) -> None:
        """Run the server inside an already-running event loop."""
        config = uvicorn.Config(
            self._app, host=self._host, port=self._port, log_level="info"
        )
        server = uvicorn.Server(config)
        await server.serve()

    def run(self) -> None:
        """Run the server (blocking; for sync entrypoints)."""
        log.info("Starting API server on %s:%d", self._host, self._port)
        log.info("API key: %s", self.api_key)
        uvicorn.run(self._app, host=self._host, port=self._port)