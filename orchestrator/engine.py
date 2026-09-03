"""Main engine loop — ties the orchestrator, scanner, evaluator, executor,
and broker into a single async cycle.

One cycle:
  1. Poll order books from all venues for all symbols.
  2. Scan for cross-venue opportunities.
  3. Evaluate (fee-aware) and filter.
  4. Execute via the broker (paper by default).
  5. Check inventory drift and emit rebalance actions.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

from config import Settings
from database.postgres import make_store
from database.redis import make_cache
from orchestrator.events import EventBus
from orchestrator.planner import Planner
from trading.arbitrage.evaluator import Evaluator
from trading.arbitrage.executor import Executor
from trading.arbitrage.rebalancer import Rebalancer
from trading.arbitrage.scanner import Scanner
from trading.arbitrage.triangular import (
    TriangularEvaluator,
    TriangularExecutor,
    TriangularScanner,
)
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
        self._gateway = ExchangeGateway(settings)
        self._cache = make_cache(settings.redis_url)
        self._store = make_store(settings.postgres_dsn)
        self._scanner = Scanner(self.DEFAULT_SYMBOLS)
        self._evaluator = Evaluator(settings)
        self._broker = PaperBroker(settings)
        self._executor = Executor(settings, self._broker)
        self._rebalancer = Rebalancer(settings)
        # Triangular arb components
        self._tri_scanner = TriangularScanner(settings)
        self._tri_evaluator = TriangularEvaluator(settings)
        self._tri_executor = TriangularExecutor(settings, self._broker)
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._bus.start()
        log.info("Engine started (mode=%s, venues=%s)", self._settings.mode, self._settings.venues)

    async def stop(self) -> None:
        self._running = False
        await self._bus.stop()
        await self._gateway.close()
        await self._store.close()
        log.info("Engine stopped")

    async def run_once(self) -> Dict:
        """Run a single cycle and return a summary dict."""
        phases = self._planner.plan()
        summary: Dict = {"phases": phases, "opportunities": 0, "executed": 0, "pnl": 0.0}

        # 1. Poll books (including cross pairs for triangular)
        books = await self._poll_books()
        summary["books"] = len(books)

        # 2. Scan (cross-venue)
        opportunities = self._scanner.scan(books)
        summary["opportunities"] = len(opportunities)
        await self._bus.publish("scan", {"opportunities": opportunities})

        # 3. Evaluate (cross-venue)
        scored = self._evaluator.evaluate(opportunities)
        summary["scored"] = len(scored)
        await self._bus.publish("evaluate", {"scored": scored})

        # 4. Execute (cross-venue)
        if scored:
            results = self._executor.execute(scored)
            summary["executed"] = sum(1 for r in results if r.status == "executed")
            summary["pnl"] = sum(r.pnl for r in results)
            await self._bus.publish("execute", {"results": results})

        # 5. Triangular scan
        if self._settings.triangular_enabled:
            tri_opps = self._tri_scanner.scan(books)
            summary["triangular_opportunities"] = len(tri_opps)
            await self._bus.publish("triangular_scan", {"opportunities": tri_opps})

            # 6. Triangular evaluate
            tri_scored = self._tri_evaluator.evaluate(tri_opps)
            summary["triangular_scored"] = len(tri_scored)
            await self._bus.publish("triangular_evaluate", {"scored": tri_scored})

            # 7. Triangular execute
            if tri_scored:
                tri_results = self._tri_executor.execute(tri_scored)
                summary["triangular_executed"] = sum(
                    1 for r in tri_results if r.status == "executed"
                )
                summary["triangular_pnl"] = sum(r.pnl for r in tri_results)
                await self._bus.publish("triangular_execute", {"results": tri_results})

        # 8. Rebalance check
        prices = {
            sym: book.mid
            for (venue, sym), book in books.items()
            if book.mid is not None
        }
        actions = self._rebalancer.check(self._executor.balances.all(), prices)
        summary["rebalance_actions"] = len(actions)
        if actions:
            await self._bus.publish("rebalance", {"actions": actions})

        return summary

    async def _poll_books(self) -> Dict[Tuple[str, str], Book]:
        """Fetch books for all venue/symbol combinations.

        Includes both default symbols (ERG/USDT, XMR/USDT) and triangular
        cross-pair symbols (XMR/ERG, ERG/XMR) for triangular arb scanning.
        """
        books: Dict[Tuple[str, str], Book] = {}
        tasks = []
        keys = []
        # Build list of symbols: default + triangular cross-pairs
        symbols = list(self.DEFAULT_SYMBOLS)
        if self._settings.triangular_enabled:
            for sym in self._settings.triangular_symbols:
                if sym not in symbols:
                    symbols.append(sym)
        for venue in self._settings.venues:
            for symbol in symbols:
                keys.append((venue, symbol))
                tasks.append(self._fetch_safe(venue, symbol))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (venue, symbol), result in zip(keys, results):
            if isinstance(result, Exception):
                log.debug("Failed to fetch %s %s: %s", venue, symbol, result)
                continue
            books[(venue, symbol)] = result
        return books

    async def _fetch_safe(self, venue: str, symbol: str) -> Book:
        return await self._gateway.fetch_book(venue, symbol)

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
