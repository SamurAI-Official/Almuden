"""REST API routes — FastAPI endpoints for control and monitoring."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from api.auth import AuthMiddleware
from api.websocket import WebSocketFeed

log = logging.getLogger(__name__)


def create_app(
    engine=None,
    agent_system=None,
    auth: Optional[AuthMiddleware] = None,
    ws_feed: Optional[WebSocketFeed] = None,
) -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(title="AlMuden API", version="0.8.0")

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth
    if auth is None:
        auth = AuthMiddleware()

    # WebSocket feed
    if ws_feed is None:
        ws_feed = WebSocketFeed()

    # Store references
    app.state.engine = engine
    app.state.agent_system = agent_system
    app.state.auth = auth
    app.state.ws_feed = ws_feed

    # ── Auth dependency ──────────────────────────────────────────────
    async def verify_api_key(x_api_key: Optional[str] = Header(None)):
        if x_api_key is None or not auth.validate_key(x_api_key):
            raise HTTPException(status_code=401, detail="Invalid API key")
        return x_api_key

    # ── Static files (dashboard) ─────────────────────────────────────
    try:
        app.mount("/static", StaticFiles(directory="api/static"), name="static")
    except Exception:
        pass

    # ── Routes ───────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        """Serve the dashboard."""
        try:
            with open("api/static/index.html", "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "<h1>AlMuden API</h1><p>Dashboard not found.</p>"

    @app.get("/api/status")
    async def get_status(api_key: str = Depends(verify_api_key)):
        """Get system status: engine, risk gate, treasury, last cycle."""
        status = {
            "running": False,
            "mode": "paper",
        }
        if engine:
            status["running"] = getattr(engine, "_running", False)
            status["paused"] = getattr(engine, "paused", False)
            status["mode"] = getattr(engine._settings, "mode", "paper")
            # Risk gate aggregates breaker + kill switch + risk state
            if hasattr(engine, "_risk_gate"):
                status["risk"] = engine._risk_gate.get_status()
            # Treasury snapshot (allocations, tiers, net worth)
            if hasattr(engine, "_treasury"):
                try:
                    status["treasury"] = engine._treasury.summary()
                except TypeError:
                    status["treasury"] = None
            # Ledger truth: realized PnL, fees, fill count
            if hasattr(engine, "_ledger"):
                status["ledger"] = {
                    "realized_pnl": engine._ledger.realized_pnl,
                    "fees_paid": engine._ledger.fees_paid,
                    "fill_count": engine._ledger.fill_count,
                }
            # Last completed cycle summary
            if hasattr(engine, "last_summary"):
                status["last_cycle"] = engine.last_summary
        if agent_system:
            status["agent_system"] = {
                "brain_available": await agent_system.brain.is_available(),
                "last_plan": agent_system.last_plan.to_dict() if agent_system.last_plan else None,
            }
        return status

    @app.get("/api/positions")
    async def get_positions(api_key: str = Depends(verify_api_key)):
        """Get current positions: broker balances + ledger-confirmed positions."""
        out = {}
        if engine and hasattr(engine, "_broker"):
            out["broker"] = engine._broker.all_balances()
        if engine and hasattr(engine, "_ledger"):
            # Ledger positions are the venue-confirmed source of truth
            out["ledger"] = engine._ledger.positions
        return out

    @app.get("/api/trades")
    async def get_trades(limit: int = 50, api_key: str = Depends(verify_api_key)):
        """Get recent venue-confirmed fills from the ledger."""
        if engine and hasattr(engine, "_ledger"):
            fills = engine._ledger.fills[-limit:]
            return [
                {
                    "t": f.get("t"),
                    "venue": f.get("venue"),
                    "symbol": f.get("symbol"),
                    "side": f.get("side"),
                    "size": f.get("size"),
                    "price": f.get("price"),
                    "fee": f.get("fee"),
                    "cost": f.get("cost"),
                    "proceeds": f.get("proceeds"),
                    "status": f.get("status"),
                    "slippage_bps": f.get("slippage_bps"),
                    "strategy": f.get("strategy"),
                }
                for f in fills
            ]
        return []

    @app.get("/api/plan")
    async def get_plan(api_key: str = Depends(verify_api_key)):
        """Get the current agent plan."""
        if agent_system and agent_system.last_plan:
            return agent_system.last_plan.to_dict()
        return {"strategies": [], "should_trade": False, "reasoning": "No plan available"}

    @app.get("/api/memory")
    async def get_memory(api_key: str = Depends(verify_api_key)):
        """Get memory summaries."""
        if agent_system:
            return {
                "short_term": agent_system.memory.get_short_term_summary(),
                "performance": agent_system.memory.get_performance_summary(),
                "episodic": agent_system.memory.get_episodic_summary(),
            }
        return {}

    @app.post("/api/pause")
    async def pause(api_key: str = Depends(verify_api_key)):
        """Pause the engine cycle loop (state preserved, restartable)."""
        if engine:
            engine.pause(reason="api")
            return {"status": "paused"}
        return {"status": "no_engine"}

    @app.post("/api/resume")
    async def resume(api_key: str = Depends(verify_api_key)):
        """Resume the engine cycle loop after a pause."""
        if engine:
            engine.resume(reason="api")
            return {"status": "resumed"}
        return {"status": "no_engine"}

    @app.post("/api/kill-switch")
    async def kill_switch(api_key: str = Depends(verify_api_key)):
        """Engage the kill switch (persistent; blocks all new execution)."""
        if engine and hasattr(engine, "_risk_gate"):
            engine._risk_gate.engage_kill_switch(reason="api")
            engine.pause(reason="kill_switch")
            return {"status": "kill_switch_engaged", "persistent": True}
        return {"status": "no_engine"}

    @app.post("/api/kill-switch/reset")
    async def kill_switch_reset(api_key: str = Depends(verify_api_key)):
        """Disengage the kill switch (explicit operator action)."""
        if engine and hasattr(engine, "_risk_gate"):
            engine._risk_gate.disengage_kill_switch(reason="api_reset")
            return {"status": "kill_switch_disengaged"}
        return {"status": "no_engine"}

    @app.get("/api/config")
    async def get_config(api_key: str = Depends(verify_api_key)):
        """Get current configuration (safe fields only)."""
        if engine:
            settings = engine._settings
            return {
                "mode": settings.mode,
                "venues": settings.venues,
                "min_edge_bps": settings.min_edge_bps,
                "max_position": settings.max_position,
                "triangular_enabled": getattr(settings, "triangular_enabled", False),
            }
        return {}

    # ── WebSocket ────────────────────────────────────────────────────
    @app.websocket("/ws")
    async def websocket_endpoint(websocket):
        await ws_feed.handler(websocket)

    return app
