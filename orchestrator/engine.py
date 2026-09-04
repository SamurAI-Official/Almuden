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
        # RiskGate is the ONLY route to the broker (risk, capital, breaker).
        from trading.audit import AuditLog
        from trading.risk_gate import RiskGate
        self._audit = AuditLog(settings)
        self._risk_gate = RiskGate(settings, audit=self._audit)
        # Ledger: exchange-confirmed fills are the accounting source of truth.
        from trading.ledger import Ledger
        self._ledger = Ledger(settings)
        # ExecutionCoordinator: state-machine two-leg execution with
        # leg-failure recovery and restart safety (WP-3).
        from trading.execution_coordinator import ExecutionCoordinator
        self._coordinator = ExecutionCoordinator(
            settings, self._broker, self._ledger, audit=self._audit,
        )
        # Treasury: the economic core — strategies draw allocations, never
        # own capital. High-water-mark compounding (WP-7).
        from trading.treasury import Treasury
        self._treasury = Treasury(settings)
        self._last_equity: float = 0.0
        # Rebalancer for inventory management
        from trading.arbitrage.rebalancer import Rebalancer
        self._rebalancer = Rebalancer(settings)
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._bus.start()
        # Resume any executions interrupted by a restart (leg-risk safety).
        recovered = self._coordinator.recover_pending()
        if recovered:
            log.warning(
                "%d in-flight execution(s) recovered from previous session",
                len(recovered),
            )
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

        # Hard kill switch short-circuits the whole cycle (no polling, no
        # scanning, no execution). Property (review item 20): a kill switch
        # must stop the machine at the cycle boundary.
        if getattr(self._settings, "live_kill_switch", False):
            summary["status"] = "kill_switch_engaged"
            log.warning("Cycle skipped: live kill switch is engaged")
            return summary

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
        """Execute opportunities via the RiskGate — the ONLY route to the broker.

        Flow per opportunity:
              opportunity
                -> RiskGate.authorize()      (risk + capital + circuit breaker)
                -> ExecutionPermit
                -> ExecutionCoordinator      (state machine, leg-risk protected)
                     leg A: buy on buy_venue
                     leg B: sell ACTUAL bought quantity on sell_venue
                     on leg-B failure: emergency hedge on buy_venue
                -> Ledger                    (confirmed fills, round trips)
                -> Treasury                  (strategy P&L, compounding)
                -> RiskGate.release(pnl)
        """
        from trading.portfolio import Portfolio

        # Defense in depth: even a direct call cannot execute with the kill
        # switch engaged.
        if getattr(self._settings, "live_kill_switch", False):
            denied = []
            for opp in opportunities:
                opp_dict = opp if isinstance(opp, dict) else opp.to_dict()
                denied.append({
                    "symbol": opp_dict.get("symbol", "?"),
                    "strategy": opp_dict.get("strategy", "?"),
                    "status": "denied",
                    "reason": "kill_switch_engaged",
                    "pnl": 0.0,
                })
            return denied

        # Compute current equity + mark prices once per cycle.
        balances = self._broker.all_balances()
        mark_prices = {}
        last_state = getattr(self._environment, "last_state", None)
        books = (
            last_state.market.books
            if last_state is not None and hasattr(last_state, "market")
            else {}
        )
        for (_venue, _symbol), book in books.items():
            if book.mid:
                base = _symbol.split("/")[0]
                mark_prices[base] = book.mid
        portfolio = Portfolio(balances, mark_prices)
        current_equity = portfolio.total_value
        self._last_equity = current_equity
        self._risk_gate.update_equity(current_equity)
        self._risk_gate.provide_mark_prices(mark_prices)
        # Treasury seeds its buckets once (idempotent) from initial equity.
        self._treasury.initialize(current_equity)

        results = []
        for opp in opportunities:
            opp_dict = opp if isinstance(opp, dict) else opp.to_dict()
            metadata = opp_dict.get("metadata", {})
            size = float(opp_dict.get("size", 0) or 0)
            symbol = opp_dict["symbol"]
            strategy = opp_dict.get("strategy", "unknown")

            buy_venue = metadata.get("buy_venue")
            sell_venue = metadata.get("sell_venue")
            buy_price = float(metadata.get("buy_price", 0) or 0)
            sell_price = float(metadata.get("sell_price", 0) or 0)

            if not (buy_venue and sell_venue and size > 0 and buy_price and sell_price):
                results.append({
                    "symbol": symbol, "strategy": strategy,
                    "status": "skipped",
                    "reason": "no execution path for this opportunity type",
                    "pnl": 0.0,
                })
                continue

            # Single permit authorizes the whole two-leg cycle.
            permit = await self._risk_gate.authorize(
                opportunity=opp_dict,
                venue=buy_venue,
                symbol=symbol,
                side="buy",
                size=size,
                limit_price=buy_price,
                current_equity=current_equity,
                current_positions=balances,
                mark_prices=mark_prices,
            )
            if permit is None:
                results.append({
                    "symbol": symbol, "strategy": strategy,
                    "status": "denied", "reason": "risk gate",
                    "pnl": 0.0,
                })
                continue

            try:
                # State-machine round trip: leg A (buy) -> leg B (sell actual
                # bought size) -> settled, with emergency hedge on leg-B
                # failure. Partial leg-A fills cannot leave a residual.
                execution = await self._coordinator.execute_round_trip(
                    buy_permit=permit,
                    sell_venue=sell_venue,
                    sell_limit_price=sell_price,
                    strategy=strategy,
                )
                realized = execution.pnl if execution.state.value in (
                    "settled", "closed",
                ) else 0.0

                self._risk_gate.release(permit, realized)
                self._treasury.record_return(strategy, realized)
                self._audit.record("trade", {
                    "permit_id": permit.permit_id,
                    "execution_id": execution.execution_id,
                    "state": execution.state.value,
                    "symbol": symbol, "strategy": strategy,
                    "pnl": round(realized, 6),
                })
                summary = execution.summary()
                summary.update({
                    "symbol": symbol, "strategy": strategy,
                    "status": "executed" if realized or execution.settled
                        else execution.state.value,
                    "pnl": realized,
                })
                results.append(summary)
            except Exception as exc:
                self._risk_gate.record_error()
                self._audit.record("trade_error", {
                    "symbol": symbol, "strategy": strategy, "reason": str(exc),
                })
                results.append({"symbol": symbol, "strategy": strategy,
                                "status": "error", "reason": str(exc), "pnl": 0.0})

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
