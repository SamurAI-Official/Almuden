"""AlMuden CLI entrypoint.

Usage:
    python main.py              # run paper engine in a loop
    python main.py --scan       # one-shot spread matrix, no execution
    python main.py --triangular # one-shot triangular arb scan
    python main.py --env        # one-shot environment snapshot
    python main.py --strategies # list available strategies
    python main.py --backtest <strategy> --start <date> --end <date>
    python main.py --simulate <strategy> --start <date> --end <date>
    python main.py --dry-run    # evaluate but do not execute
    python main.py --once       # single cycle, then exit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from config import load_settings
from orchestrator.engine import Engine
from tools.indicators import spread_matrix


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AlMuden crypto arbitrage engine")
    p.add_argument("--scan", action="store_true", help="Print spread matrix and exit")
    p.add_argument("--triangular", action="store_true", help="Print triangular arb opportunities and exit")
    p.add_argument("--env", action="store_true", help="Print environment snapshot and exit")
    p.add_argument("--strategies", action="store_true", help="List available strategies and exit")
    p.add_argument("--backtest", type=str, metavar="STRATEGY", help="Backtest a strategy (requires --start and --end)")
    p.add_argument("--simulate", type=str, metavar="STRATEGY", help="Run Monte Carlo simulation (requires --start and --end)")
    p.add_argument("--venue", type=str, default="kucoin", help="Exchange to use for backtest (default: kucoin)")
    p.add_argument("--symbol", type=str, default="ERG/USDT", help="Symbol to backtest (default: ERG/USDT)")
    p.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    p.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    p.add_argument("--draws", type=int, help="Number of Monte Carlo draws")
    p.add_argument("--serve", action="store_true", help="Start API server alongside engine")
    p.add_argument("--kill-switch", action="store_true", help="Engage kill switch and exit")
    p.add_argument("--dry-run", action="store_true", help="Evaluate but do not execute")
    p.add_argument("--once", action="store_true", help="Run one cycle and exit")
    p.add_argument("--interval", type=float, default=5.0, help="Cycle interval in seconds")
    p.add_argument("--dotenv", default=".env", help="Path to .env file")
    return p.parse_args()


async def run_scan(settings) -> None:
    """Fetch books and print the spread matrix."""
    from trading.exchange import ExchangeGateway

    gateway = ExchangeGateway(settings)
    engine = Engine(settings)
    engine._gateway = gateway  # reuse the gateway
    books = await engine._poll_books()
    matrix = spread_matrix(books)
    if not matrix:
        print("No cross-venue data available.")
        return
    print(f"\n{'Symbol':<12} {'Venue A':<10} {'Venue B':<10} {'Mid A':>12} {'Mid B':>12} {'Edge bps':>10}")
    print("-" * 70)
    for row in matrix:
        print(
            f"{row['symbol']:<12} {row['venue_a']:<10} {row['venue_b']:<10} "
            f"{row['mid_a']:>12.6f} {row['mid_b']:>12.6f} {row['edge_bps']:>10.2f}"
        )
    await gateway.close()


async def run_triangular(settings) -> None:
    """Fetch books, scan for triangular opportunities, print and exit."""
    from trading.arbitrage.triangular import TriangularScanner
    from trading.exchange import ExchangeGateway

    gateway = ExchangeGateway(settings)
    engine = Engine(settings)
    engine._gateway = gateway
    books = await engine._poll_books()

    scanner = TriangularScanner(settings)
    opportunities = scanner.scan(books)

    if not opportunities:
        print("No triangular opportunities found.")
        await gateway.close()
        return

    print(f"\n{'Venue':<10} {'Cycle':<25} {'Gross bps':>10} {'Legs'}")
    print("-" * 80)
    for opp in opportunities:
        legs_str = " -> ".join(
            f"{leg['side']} {leg['symbol']} @ {leg['price']:.6f}"
            for leg in opp["legs"]
        )
        print(
            f"{opp['venue']:<10} {opp['cycle_name']:<25} "
            f"{opp['gross_edge_bps']:>10.2f}   {legs_str}"
        )
    await gateway.close()


async def run_env(settings) -> None:
    """Poll the environment and print a snapshot."""
    from environment import Environment

    env = Environment(settings)
    try:
        state = await env.poll()

        print("\n=== Environment Snapshot ===")
        print(f"Regime: {state.regime.value}")
        print(f"Timestamp: {state.timestamp}")

        print(f"\n--- Market ({len(state.market.books)} books) ---")
        for (venue, symbol), book in sorted(state.market.books.items()):
            print(f"  {venue} {symbol}: bid={book.best_bid} ask={book.best_ask}")

        print(f"\n--- Exchange Health ---")
        for venue, health in sorted(state.exchange_health.items()):
            print(f"  {health}")

        print(f"\n--- News ({len(state.news)} items) ---")
        for item in state.news:
            print(f"  {item}")

        print(f"\n--- Sentiment ---")
        for asset, score in sorted(state.sentiment.items()):
            print(f"  {score}")

        if state.has_critical_news:
            print("\n⚠️  CRITICAL NEWS DETECTED - Trading may be restricted")
    finally:
        await env.close()


async def run_strategies(settings) -> None:
    """List available strategies."""
    from strategy_lab import create_registry

    registry = create_registry(settings)
    strategies = registry.available()
    if not strategies:
        print("No strategies registered.")
    else:
        print("\nAvailable strategies:")
        for name in strategies:
            print(f"  - {name}")


async def run_backtest(settings, strategy_name, venue, symbol, start, end) -> None:
    """Run a backtest for a strategy."""
    from strategy_lab import create_registry
    from strategy_lab.backtester import Backtester

    if not start or not end:
        print("Error: --start and --end are required for backtest")
        return

    registry = create_registry(settings)
    try:
        strategy = registry.get(strategy_name)
    except KeyError:
        print(f"Error: Unknown strategy '{strategy_name}'")
        print(f"Available: {registry.available()}")
        return

    print(f"\nBacktesting {strategy_name} on {venue} {symbol} ({start} to {end})...")

    backtester = Backtester(settings)
    result = backtester.run(strategy, venue, symbol, start, end)

    print(f"\nBacktest Results: {result.strategy}")
    print(f"Venue: {result.venue}")
    print(f"Symbol: {result.symbol}")
    print(f"Period: {result.start_date} to {result.end_date}")
    print(f"Total trades: {result.total_trades}")
    print(f"Total PnL: {result.total_pnl:.4f}")

    if result.metrics:
        print(f"\nMetrics:")
        from strategy_lab.performance import format_metrics
        print(format_metrics(result.metrics))


async def run_simulate(settings, strategy_name, venue, symbol, start, end, draws) -> None:
    """Run Monte Carlo simulation."""
    from strategy_lab import create_registry
    from strategy_lab.backtester import Backtester
    from strategy_lab.simulator import Simulator

    if not start or not end:
        print("Error: --start and --end are required for simulation")
        return

    registry = create_registry(settings)
    try:
        strategy = registry.get(strategy_name)
    except KeyError:
        print(f"Error: Unknown strategy '{strategy_name}'")
        print(f"Available: {registry.available()}")
        return

    print(f"\nRunning Monte Carlo for {strategy_name} ({draws} draws)...")

    # First run backtest to get trades
    backtester = Backtester(settings)
    result = backtester.run(strategy, venue, symbol, start, end)

    if not result.trades:
        print("No trades to simulate. Try a different period or strategy.")
        return

    # Run simulation
    simulator = Simulator(settings)
    sim_result = simulator.run(
        strategy_name=result.strategy,
        trades=result.trades,
        num_draws=draws,
    )

    print(f"\nMonte Carlo Results: {sim_result.strategy}")
    print(f"Draws: {sim_result.num_draws}")
    print(f"Initial balance: {sim_result.initial_balance:.2f}")
    print(f"Mean final balance: {sim_result.mean_final_balance:.2f}")
    print(f"Median final balance: {sim_result.median_final_balance:.2f}")
    print(f"Worst case: {sim_result.worst_case:.2f}")
    print(f"Best case: {sim_result.best_case:.2f}")
    print(f"Probability of profit: {sim_result.probability_of_profit:.1f}%")

    if sim_result.metrics:
        print(f"\nMetrics:")
        from strategy_lab.performance import format_metrics
        print(format_metrics(sim_result.metrics))

    if sim_result.percentiles:
        print(f"\nPercentiles:")
        for p, v in sorted(sim_result.percentiles.items()):
            print(f"  {p}: {v:.2f}")


async def run_dry_run(settings) -> None:
    """Run one cycle without executing trades."""
    engine = Engine(settings)
    await engine.start()
    try:
        # Monkey-patch the executor to no-op.
        original_execute = engine._executor.execute

        def dry_execute(opportunities):
            from trading.arbitrage.executor import CycleResult
            return [
                CycleResult(
                    symbol=o["symbol"], buy_venue=o["buy_venue"], sell_venue=o["sell_venue"],
                    size=0, buy_price=o.get("buy_price", 0), sell_price=o.get("sell_price", 0),
                    edge_bps=o.get("edge_bps", 0), net_edge_bps=o.get("net_edge_bps", 0),
                    pnl=0, status="dry_run",
                )
                for o in opportunities
            ]

        engine._executor.execute = dry_execute  # type: ignore[assignment]
        summary = await engine.run_once()
        print(json.dumps(summary, indent=2, default=str))
    finally:
        await engine.stop()


async def run_server(settings) -> None:
    """Start the API server with engine and agent system."""
    import asyncio
    from agent_system import AgentSystem
    from api.server import APIServer

    # Create engine and agent system
    from orchestrator.engine import Engine
    engine = Engine(settings)
    agent_system = AgentSystem(settings)

    # Create and start API server
    server = APIServer(
        settings=settings,
        engine=engine,
        agent_system=agent_system,
    )

    print(f"\n{'='*50}")
    print(f"  AlMuden API Server")
    print(f"{'='*50}")
    print(f"  Dashboard: http://localhost:{settings.api_port}?api_key={server.api_key}")
    print(f"  API Key:   {server.api_key}")
    print(f"{'='*50}\n")

    # Start engine in background
    asyncio.create_task(engine.run_forever(interval=settings.interval))

    # Run server (blocking)
    server.run()


async def main() -> int:
    args = parse_args()
    settings = load_settings(args.dotenv)
    setup_logging(settings.log_level)

    if args.scan:
        await run_scan(settings)
        return 0

    if args.triangular:
        await run_triangular(settings)
        return 0

    if args.env:
        await run_env(settings)
        return 0

    if args.strategies:
        await run_strategies(settings)
        return 0

    if args.backtest:
        await run_backtest(
            settings, args.backtest, args.venue, args.symbol, args.start, args.end
        )
        return 0

    if args.simulate:
        draws = args.draws or settings.monte_carlo_draws
        await run_simulate(
            settings, args.simulate, args.venue, args.symbol, args.start, args.end, draws
        )
        return 0

    if args.dry_run:
        await run_dry_run(settings)
        return 0

    if args.serve:
        await run_server(settings)
        return 0

    if args.kill_switch:
        print("Kill switch engaged. Set ALMUDEN_LIVE_KILL_SWITCH=true to activate.")
        return 0

    engine = Engine(settings)
    if args.once:
        await engine.start()
        try:
            summary = await engine.run_once()
            print(json.dumps(summary, indent=2, default=str))
        finally:
            await engine.stop()
        return 0

    # Default: run forever.
    await engine.run_forever(interval=args.interval)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)