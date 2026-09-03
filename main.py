"""AlMuden CLI entrypoint.

Usage:
    python main.py              # run paper engine in a loop
    python main.py --scan       # one-shot spread matrix, no execution
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


async def main() -> int:
    args = parse_args()
    settings = load_settings(args.dotenv)
    setup_logging(settings.log_level)

    if args.scan:
        await run_scan(settings)
        return 0

    if args.dry_run:
        await run_dry_run(settings)
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
