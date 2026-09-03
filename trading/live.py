"""Live broker — real order execution (Phase 7, hard-guarded).

Trading real money requires BOTH:
  1. ALMUDEN_MODE=live AND ALMUDEN_LIVE_ENABLED=true (enforced by config)
  2. ALMUDEN_LIVE_KILL_SWITCH=false

Until Phase 7, this module raises if any live method is called.
"""
from __future__ import annotations

import logging

from config import Settings

log = logging.getLogger(__name__)


class LiveBrokerError(Exception):
    pass


class LiveBroker:
    """Real order execution backend. Not yet implemented."""

    def __init__(self, settings: Settings, gateway) -> None:
        if settings.mode != "live":
            raise LiveBrokerError("LiveBroker requires mode=live")
        if not settings.live_enabled:
            raise LiveBrokerError("LiveBroker requires ALMUDEN_LIVE_ENABLED=true")
        self._settings = settings
        self._gateway = gateway
        self._kill_switch = settings.live_kill_switch
        log.warning("LIVE BROKER INITIALISED — real orders enabled")

    def _check_kill_switch(self) -> None:
        if self._kill_switch:
            raise LiveBrokerError("Kill switch is engaged")

    def buy(self, venue: str, symbol: str, size: float, price: float):
        raise LiveBrokerError("LiveBroker.buy not implemented (Phase 7)")

    def sell(self, venue: str, symbol: str, size: float, price: float):
        raise LiveBrokerError("LiveBroker.sell not implemented (Phase 7)")

    def engage_kill_switch(self, reason: str) -> None:
        self._kill_switch = True
        log.critical("LIVE KILL SWITCH ENGAGED: %s", reason)
