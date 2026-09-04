"""Main engine loop — ties the environment, strategy lab, broker,
and risk into a single async cycle.

One cycle:
  1. Poll environment (market data, news, exchange health, regime).
  2. Scan with all registered strategies (strategy lab).
  3. Execute viable opportunities via the unified broker (paper by default).
  4. Check inventory drift and emit rebalance actions.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from config import Settings
from database.postgres import make_store
from database.redis import make_cache
from environment import Environment, EnvironmentState
from orchestrator.events import EventBus
from orchestrator.planner import Planner
from strategy_lab import create_registry
from trading.exchange import Book, ExchangeGateway
from trading.paper import PaperBroker

log = logging.getLogger(__name__)


class Engine:
    """The core trading engine."""

    # Symbols to monitor. KuCoin is the only venue listing both ERG and XMR,
    # so triangular routes go through it. Cross-venue arb uses the overlap.
    DEFAULT_SYMBOLS = ["ERG/USDT", "XMR/USDT"]

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bus = EventBus()
        self._planner = Planner()
        self._environment = Environment(settings)
        self._gateway = self._environment._market._gateway  # Reuse gateway from environment
        self._cache = make_cache(settings.redis_url)
        self._store = make_store(settings.postgres_dsn)
        # Strategy lab replaces scanner/evaluator/executor
        self._registry = create_registry(settings)
        self._broker = PaperBroker(settings)
        # Rebalancer for inventory management
        from trading.arbitrage.rebalancer import Rebalancer
        self._rebalancer = Rebalancer(settings)
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._bus.start()
        log.info("Engine started (mode=%s, venues=%s)", self._settings.mode, self._settings.venues)

    async def stop(self) -> None:
        self._running = False
        await self._bus.stop()
        await self._environment.close()
        await self._store.close()
        log.info("Engine stopped")

    async def run_once(self) -> Dict:
        """Run a single cycle and return a summary dict."""
        phases = self._planner.plan()
        summary: Dict = {"phases": phases, "opportunities": 0, "executed": 0, "pnl": 0.0}

        # 1. Poll environment (market data, news, health, regime)
        env_state = await self._environment.poll()
        books = env_state.market.books
        summary["books"] = len(books)
        summary["regime"] = env_state.regime.value
        summary["healthy_venues"] = env_state.healthy_venues
        summary["news_alerts"] = len(env_state.news)
        summary["critical_news"] = len(env_state.critical_news)
        summary["exchange_health"] = {
            v: h.status for v, h in env_state.exchange_health.items()
        }
        await self._bus.publish("environment", env_state.summary())

        # 2. Scan with all registered strategies
        strategy_opps = self._registry.scan_all(books, env_state)
        all_opps = []
        for name, opps in strategy_opps.items():
            all_opps.extend(opps)
        summary["opportunities"] = len(all_opps)
        await self._bus.publish("scan", {"opportunities": all_opps, "by_strategy": {
            name: len(opps) for name, opps in strategy_opps.items()
        }})

        # 3. Execute via unified broker interface
        if all_opps:
            results = await self._execute_opportunities(all_opps)
            summary["executed"] = sum(1 for r in results if r.get("status") == "executed")
            summary["pnl"] = sum(r.get("pnl", 0) for r in results)
            await self._bus.publish("execute", {"results": results})

        # 4. Rebalance check
        prices = {
            sym: book.mid
            for (venue, sym), book in books.items()
            if book.mid is not None
        }
        actions = self._rebalancer.check(self._broker.all_balances(), prices)
        summary["rebalance_actions"] = len(actions)
        if actions:
            await self._bus.publish("rebalance", {"actions": actions})

        return summary

    async def _execute_opportunities(self, opportunities: List[Dict]) -> List[Dict]:
        """Execute opportunities using the unified broker interface."""
        from trading.core import OrderIntent
        
        results = []
        for opp in opportunities:
            # Build OrderIntent from opportunity metadata
            opp_dict = opp if isinstance(opp, dict) else opp.to_dict()
            metadata = opp_dict.get("metadata", {})
            size = float(opp_dict.get("size", 0) or 0)
            
            # Determine if this is a cross-venue arb opportunity
            buy_venue = metadata.get("buy_venue")
            sell_venue = metadata.get("sell_venue")
            if buy_venue and sell_venue and size > 0 and metadata.get("buy_price") and metadata.get("sell_price"):
                # Cross-venue arbitrage: buy on cheap, sell on expensive
                buy_intent = OrderIntent(
                    venue=buy_venue, symbol=opp_dict["symbol"], side="buy",
                    size=size, max_price=float(metadata.get("buy_price", 0)),
                    ttl_ms=10000,
                )
                sell_intent = OrderIntent(
                    venue=sell_venue, symbol=opp_dict["symbol"], side="sell",
                    size=size, max_price=float(metadata.get("sell_price", 0)),
                    min_output=size * float(metadata.get("sell_price", 0)) * 0.99,
                    ttl_ms=10000,
                )
                
                try:
                    buy_fill = await self._broker.execute(buy_intent)
                    sell_fill = await self._broker.execute(sell_intent)
                    
                    pnl = sell_fill.proceeds - buy_fill.cost
                    results.append({
                        "symbol": opp_dict["symbol"],
                        "strategy": opp_dict.get("strategy", "unknown"),
                        "buy_venue": buy_venue,
                        "sell_venue": sell_venue,
                        "pnl": pnl,
                        "status": "executed",
                        "buy_fill": buy_fill.__dict__,
                        "sell_fill": sell_fill.__dict__,
                    })
                except Exception as exc:
                    results.append({
                        "symbol": opp_dict["symbol"],
                        "strategy": opp_dict.get("strategy", "unknown"),
                        "status": "error",
                        "reason": str(exc),
                        "pnl": 0.0,
                    })
            else:
                # Non-arb opportunity or triangular — record but don't execute yet
                results.append({
                    "symbol": opp_dict["symbol"],
                    "strategy": opp_dict.get("strategy", "unknown"),
                    "status": "skipped",
                    "reason": "no execution path for this opportunity type",
                    "pnl": 0.0,
                })
        
        return results

    async def _poll_books(self) -> Dict[Tuple[str, str], Book]:
        """Fetch books via the environment's market feed.

        Includes both default symbols and triangular cross-pairs.
        """
        return await self._environment._market.poll_books()

    async def _fetch_safe(self, venue: str, symbol: str) -> Book:
        """Fetch a single book via the gateway."""
        return await self._environment._market._gateway.fetch_book(venue, symbol)

    async def run_forever(self, interval: float = 5.0) -> None:
        """Run cycles forever, *interval* seconds apart."""
        await self.start()
        try:
            while self._running:
                try:
                    summary = await self.run_once()
                    log.info("Cycle: %s", summary)
                except Exception:
                    log.exception("Cycle failed")
                await asyncio.sleep(interval)
        finally:
            await self.stop()
