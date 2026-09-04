"""Live broker — real order execution via CCXT.

Trading real money requires BOTH:
  1. ALMUDEN_MODE=live AND ALMUDEN_LIVE_ENABLED=true (enforced by config)
  2. ALMUDEN_LIVE_KILL_SWITCH=false

The live broker wraps CCXT to place real orders on exchanges.
"""
from __future__ import annotations

import logging
import time
from typing import Dict

from config import Settings
from trading.core import Fill, OrderIntent
from trading.exchange import ExchangeGateway

log = logging.getLogger(__name__)


class LiveBrokerError(Exception):
    pass


class LiveBroker:
    """Real order execution backend using CCXT.

    Implements the unified ``async execute(intent: OrderIntent) -> Fill``
    interface so that the executor can treat paper and live identically.
    """

    # Conservative default fee — actual fees come from exchange fills
    DEFAULT_FEE_BPS = 10.0

    def __init__(self, settings: Settings, gateway: ExchangeGateway) -> None:
        if settings.mode != "live":
            raise LiveBrokerError("LiveBroker requires mode=live")
        if not settings.live_enabled:
            raise LiveBrokerError("LiveBroker requires ALMUDEN_LIVE_ENABLED=true")
        self._settings = settings
        self._gateway = gateway
        self._kill_switch = settings.live_kill_switch
        self._balances: Dict[str, Dict[str, float]] = {}
        self._fills: list = []
        self._open_orders: list = []
        log.warning("LIVE BROKER INITIALISED — real orders enabled")

    @property
    def fills(self) -> list:
        return list(self._fills)

    def _check_kill_switch(self) -> None:
        if self._kill_switch:
            raise LiveBrokerError("Kill switch is engaged")

    async def execute(self, intent: OrderIntent) -> Fill:
        """Place a real order and return the confirmed fill.

        Parses actual fills from the exchange response to determine
        real executed quantity, VWAP price, and fees — not estimated.
        """
        self._check_kill_switch()

        symbol = intent.symbol
        side = intent.side
        size = intent.size
        venue = intent.venue

        try:
            order = await self._gateway.create_market_order(
                venue, symbol, side, size
            )
        except Exception as exc:
            raise LiveBrokerError(f"Order failed: {exc}") from exc

        # Fetch the fills associated with this order to get actuals
        fills = []
        if hasattr(order, "get") and order.get("id"):
            try:
                fills = await self._gateway.fetch_order_fills(venue, order["id"], symbol)
            except Exception:
                log.debug("Could not fetch per-fill data, using order summary")

        if fills:
            # Compute VWAP from actual fills
            total_size = sum(float(f.get("amount", 0)) for f in fills)
            total_cost = sum(float(f.get("cost", 0)) for f in fills)
            total_fee = sum(
                float(f.get("fee", {}).get("cost", 0)) if isinstance(f.get("fee"), dict) else 0
                for f in fills
            )
            actual_price = total_cost / total_size if total_size > 0 else intent.max_price
            actual_fee = total_fee
            status = "filled" if total_size >= size * 0.99 else "partial"
            actual_size = total_size
        else:
            # Fallback: use order-level data
            actual_size = float(order.get("average", order.get("filled", size)))
            actual_price = float(order.get("average", order.get("price", intent.max_price)))
            # Fee: try to get from order, fall back to estimate
            fee_info = order.get("fee", {})
            if isinstance(fee_info, dict):
                actual_fee = float(fee_info.get("cost", actual_size * actual_price * self.DEFAULT_FEE_BPS / 10_000))
            else:
                actual_fee = actual_size * actual_price * self.DEFAULT_FEE_BPS / 10_000
            status = order.get("status", "closed")

        if side == "buy":
            cost = actual_size * actual_price + actual_fee
            proceeds = 0.0
        else:
            cost = 0.0
            proceeds = actual_size * actual_price - actual_fee

        fill = Fill(
            venue=venue,
            symbol=symbol,
            side=side,
            size=actual_size,
            price=actual_price,
            fee=actual_fee,
            cost=cost,
            proceeds=proceeds,
            order_id=str(order.get("id", "")),
            timestamp=time.time(),
            status=status,
        )
        self._fills.append(fill)

        slippage_bps = (actual_price - intent.max_price) / intent.max_price * 10_000 if intent.max_price > 0 else 0
        fill.slippage_bps = slippage_bps

        log.info(
            "LIVE FILL %s %s %.6f %s @ %.6f (slippage %.2f bps)",
            venue, side, actual_size, symbol, actual_price, slippage_bps,
        )
        return fill

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
