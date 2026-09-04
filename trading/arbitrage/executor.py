"""Inventory-based arbitrage executor.

Executes a two-leg cycle: buy on the cheap venue, sell on the expensive one.
In paper mode this is simulated against the live book; in live mode it
places real orders (Phase 7).

The executor is inventory-aware: it tracks per-venue balances and refuses
to over-extend. Rebalancing between venues is handled separately.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import Settings

log = logging.getLogger(__name__)


@dataclass
class CycleResult:
    symbol: str
    buy_venue: str
    sell_venue: str
    size: float
    buy_price: float
    sell_price: float
    edge_bps: float
    net_edge_bps: float
    pnl: float
    status: str  # "executed" | "rejected" | "error"
    reason: str = ""


@dataclass
class Balances:
    """Per-venue balances keyed by asset symbol (e.g. "ERG", "USDT")."""

    _data: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def get(self, venue: str, asset: str) -> float:
        return self._data.get(venue, {}).get(asset, 0.0)

    def set(self, venue: str, asset: str, amount: float) -> None:
        self._data.setdefault(venue, {})[asset] = amount

    def add(self, venue: str, asset: str, delta: float) -> None:
        self._data.setdefault(venue, {})[asset] = self.get(venue, asset) + delta

    def all(self) -> Dict[str, Dict[str, float]]:
        return self._data


class Executor:
    """Execute arbitrage cycles against a broker (paper or live)."""

    def __init__(self, settings: Settings, broker) -> None:
        self._settings = settings
        self._broker = broker
        self._balances = Balances()

    @property
    def balances(self) -> Balances:
        return self._balances

    def execute(self, opportunities: List[Dict]) -> List[CycleResult]:
        """Execute best opportunities up to the position cap."""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self.execute_async(opportunities))

    async def execute_async(self, opportunities: List[Dict]) -> List[CycleResult]:
        """Execute best opportunities up to the position cap (async)."""
        results: List[CycleResult] = []
        for opp in opportunities:
            result = await self._try_execute_async(opp)
            results.append(result)
            if result.status == "executed":
                log.info(
                    "Executed %s: buy %s @ %.6f, sell %s @ %.6f, net %.2f bps, pnl %.4f",
                    result.symbol, result.buy_venue, result.buy_price,
                    result.sell_venue, result.sell_price,
                    result.net_edge_bps, result.pnl,
                )
        return results

    async def _try_execute_async(self, opp: Dict) -> CycleResult:
        """Execute a single opportunity using the unified OrderIntent interface."""
        from trading.core import OrderIntent
        
        symbol = opp["symbol"]
        buy_venue = opp["buy_venue"]
        sell_venue = opp["sell_venue"]
        base, quote = symbol.split("/")

        size = self._size(opp, base, quote)
        if size <= 0:
            return CycleResult(
                symbol=symbol, buy_venue=buy_venue, sell_venue=sell_venue,
                size=0, buy_price=0, sell_price=0,
                edge_bps=opp.get("edge_bps", 0), net_edge_bps=opp.get("net_edge_bps", 0),
                pnl=0, status="rejected", reason="size zero",
            )

        # Check balance feasibility.
        cost = size * opp["buy_price"]
        if self._balances.get(buy_venue, quote) < cost:
            return CycleResult(
                symbol=symbol, buy_venue=buy_venue, sell_venue=sell_venue,
                size=size, buy_price=opp["buy_price"], sell_price=opp["sell_price"],
                edge_bps=opp.get("edge_bps", 0), net_edge_bps=opp.get("net_edge_bps", 0),
                pnl=0, status="rejected", reason=f"insufficient {quote} on {buy_venue}",
            )
        if self._balances.get(sell_venue, base) < size:
            return CycleResult(
                symbol=symbol, buy_venue=buy_venue, sell_venue=sell_venue,
                size=size, buy_price=opp["buy_price"], sell_price=opp["sell_price"],
                edge_bps=opp.get("edge_bps", 0), net_edge_bps=opp.get("net_edge_bps", 0),
                pnl=0, status="rejected", reason=f"insufficient {base} on {sell_venue}",
            )

        # Build OrderIntents for both legs
        buy_intent = OrderIntent(
            venue=buy_venue, symbol=symbol, side="buy",
            size=size, max_price=opp["buy_price"], ttl_ms=10000,
        )
        sell_intent = OrderIntent(
            venue=sell_venue, symbol=symbol, side="sell",
            size=size, max_price=opp["sell_price"], min_output=size * opp["sell_price"],
            ttl_ms=10000,
        )

        try:
            buy_fill = await self._broker.execute(buy_intent)
            sell_fill = await self._broker.execute(sell_intent)
        except Exception:
            log.exception("Execution failed for %s", symbol)
            return CycleResult(
                symbol=symbol, buy_venue=buy_venue, sell_venue=sell_venue,
                size=size, buy_price=opp["buy_price"], sell_price=opp["sell_price"],
                edge_bps=opp.get("edge_bps", 0), net_edge_bps=opp.get("net_edge_bps", 0),
                pnl=0, status="error", reason="broker error",
            )

        # Update balances from actual fills
        self._balances.add(buy_venue, quote, -buy_fill.cost)
        self._balances.add(buy_venue, base, buy_fill.size)
        self._balances.add(sell_venue, base, -sell_fill.size)
        self._balances.add(sell_venue, quote, sell_fill.proceeds)

        pnl = sell_fill.proceeds - buy_fill.cost
        return CycleResult(
            symbol=symbol, buy_venue=buy_venue, sell_venue=sell_venue,
            size=size, buy_price=buy_fill.price, sell_price=sell_fill.price,
            edge_bps=opp.get("edge_bps", 0), net_edge_bps=opp.get("net_edge_bps", 0),
            pnl=pnl, status="executed",
        )

    def _try_execute(self, opp: Dict) -> CycleResult:
        """Synchronous wrapper for backward compatibility."""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(self._try_execute_async(opp))

    def _size(self, opp: Dict, base: str, quote: str) -> float:
        """Conservative size: min(book depth, position cap / price)."""
        buy_book = opp.get("buy_book")
        sell_book = opp.get("sell_book")
        if buy_book is None or sell_book is None:
            return 0.0
        depth = min(
            buy_book.best_ask[1] if buy_book.best_ask else 0,
            sell_book.best_bid[1] if sell_book.best_bid else 0,
        )
        cap_size = self._settings.max_position / max(opp["buy_price"], 1e-12)
        return min(depth, cap_size)
