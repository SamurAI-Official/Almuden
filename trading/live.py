"""Live broker — real order execution via CCXT.

Trading real money requires BOTH:
  1. ALMUDEN_MODE=live AND ALMUDEN_LIVE_ENABLED=true (enforced by config)
  2. ALMUDEN_LIVE_KILL_SWITCH=false

The live broker wraps CCXT to place real orders on exchanges.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import Settings
from trading.exchange import ExchangeGateway

log = logging.getLogger(__name__)


class LiveBrokerError(Exception):
    pass


@dataclass
class LiveFill:
    """Represents a real order fill."""
    venue: str
    symbol: str
    side: str
    size: float
    price: float
    fee: float
    cost: float
    proceeds: float
    order_id: str = ""
    timestamp: float = 0.0
    status: str = "filled"


class LiveBroker:
    """Real order execution backend using CCXT."""

    def __init__(self, settings: Settings, gateway: ExchangeGateway) -> None:
        if settings.mode != "live":
            raise LiveBrokerError("LiveBroker requires mode=live")
        if not settings.live_enabled:
            raise LiveBrokerError("LiveBroker requires ALMUDEN_LIVE_ENABLED=true")
        self._settings = settings
        self._gateway = gateway
        self._kill_switch = settings.live_kill_switch
        self._balances: Dict[str, Dict[str, float]] = {}
        self._fills: List[LiveFill] = []
        self._open_orders: List[Dict] = []
        log.warning("LIVE BROKER INITIALISED — real orders enabled")

    @property
    def fills(self) -> list:
        return list(self._fills)

    def _check_kill_switch(self) -> None:
        if self._kill_switch:
            raise LiveBrokerError("Kill switch is engaged")

    async def buy(self, venue: str, symbol: str, size: float, price: float) -> LiveFill:
        """Place a market buy order."""
        self._check_kill_switch()
        try:
            order = await self._gateway.create_market_order(venue, symbol, "buy", size)
            fill = LiveFill(
                venue=venue,
                symbol=symbol,
                side="buy",
                size=size,
                price=price,
                fee=size * price * 0.001,
                cost=size * price,
                proceeds=0.0,
                order_id=order.get("id", ""),
                timestamp=time.time(),
                status=order.get("status", "filled"),
            )
            self._fills.append(fill)
            return fill
        except Exception as exc:
            raise LiveBrokerError(f"Buy failed: {exc}") from exc

    async def sell(self, venue: str, symbol: str, size: float, price: float) -> LiveFill:
        """Place a market sell order."""
        self._check_kill_switch()
        try:
            order = await self._gateway.create_market_order(venue, symbol, "sell", size)
            fill = LiveFill(
                venue=venue,
                symbol=symbol,
                side="sell",
                size=size,
                price=price,
                fee=size * price * 0.001,
                cost=0.0,
                proceeds=size * price,
                order_id=order.get("id", ""),
                timestamp=time.time(),
                status=order.get("status", "filled"),
            )
            self._fills.append(fill)
            return fill
        except Exception as exc:
            raise LiveBrokerError(f"Sell failed: {exc}") from exc

    def engage_kill_switch(self, reason: str) -> None:
        """Engage the kill switch — stops all trading."""
        self._kill_switch = True
        log.critical("LIVE KILL SWITCH ENGAGED: %s", reason)

    def disengage_kill_switch(self) -> None:
        """Disengage the kill switch (requires manual confirmation)."""
        self._kill_switch = False
        log.warning("LIVE KILL SWITCH DISENGAGED")

    @property
    def kill_switch_engaged(self) -> bool:
        return self._kill_switch

    def balance(self, venue: str, asset: str) -> float:
        """Get balance for an asset on a venue."""
        return self._balances.get(venue, {}).get(asset, 0.0)

    def all_balances(self) -> Dict[str, Dict[str, float]]:
        """Get all balances."""
        return self._balances
