"""Execution pipeline tests — verifies the unified async broker path.

Test flow: Opportunity → engine._execute_opportunities → OrderIntent → broker.execute() → Fill → balances.

Run: python -m tests.test_execution
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_settings  # noqa: E402
from orchestrator.engine import Engine  # noqa: E402
from strategy_lab.base import Opportunity  # noqa: E402

# Force paper mode, no external dependencies for the execution path itself
os.environ.setdefault("ALMUDEN_MODE", "paper")


def _make_opportunity(side_venue: str = "kucoin", size: float = 100.0) -> Opportunity:
    return Opportunity(
        strategy="cross_venue",
        symbol="ERG/USDT",
        venues=[side_venue, "mexc"],
        expected_edge_bps=30.0,
        confidence=0.8,
        size=size,
        metadata={
            "buy_venue": side_venue,
            "sell_venue": "mexc",
            "buy_price": 0.26,
            "sell_price": 0.262,
        },
    )


async def _run_case() -> dict:
    settings = load_settings(".env.example")
    engine = Engine(settings)
    await engine.start()
    try:
        engine._broker.seed_balance("kucoin", "USDT", 10_000)
        engine._broker.seed_balance("mexc", "USDT", 10_000)
        engine._broker.seed_balance("mexc", "ERG", 500)
        results = await engine._execute_opportunities([_make_opportunity()])
        return {"engine": engine, "results": results}
    finally:
        await engine.stop()


def test_execution():
    """A cross-venue opportunity must produce two fills and positive pnl."""
    ctx = asyncio.run(_run_case())
    results, engine = ctx["results"], ctx["engine"]
    assert len(results) == 1, f"expected 1 result, got {len(results)}"
    r = results[0]
    assert r["status"] == "executed", f"expected executed, got {r}"
    assert r["pnl"] > 0, f"expected positive pnl, got {r['pnl']:.4f}"
    assert r["buy_fill"]["venue"] == "kucoin"
    assert r["sell_fill"]["venue"] == "mexc"
    # Buy consumes USDT on kucoin, adds ERG
    bal = engine._broker.all_balances()
    assert bal["kucoin"]["ERG"] > 0
    assert bal.get("mexc", {}).get("USDT", 0) > 10_000  # proceeds > seed
    print(f"✓ execution: pnl={r['pnl']:.4f}")


def test_skipped_when_size_zero():
    """An opportunity with no size must be skipped, not executed."""
    ctx = asyncio.run(_run_case())
    engine = ctx["engine"]
    opp = _make_opportunity()
    opp.size = 0.0
    results = asyncio.run(_execute_single(engine, opp))
    assert results[0]["status"] == "skipped", f"expected skipped, got {results[0]}"


async def _execute_single(engine, opp):
    return await engine._execute_opportunities([opp])


if __name__ == "__main__":
    test_execution()
    test_skipped_when_size_zero()
    print("\nALL EXECUTION TESTS PASSED")