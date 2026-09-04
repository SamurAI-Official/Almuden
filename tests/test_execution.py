"""Execution pipeline tests — verifies the unified async broker path.

Test flow: Opportunity → engine._execute_opportunities → RiskGate →
OrderIntent → broker.execute() → Fill → balances → PnL.

Run: python tests\\test_execution.py
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


async def _run_all_cases() -> None:
    """Run every case inside ONE engine + ONE event loop.

    The engine owns aiohttp/ccxt resources bound to this loop, so all
    execution tests must share it. Creating a second asyncio.run() with a
    stopped engine would fail on closed resources.
    """
    settings = load_settings(".env.example")
    engine = Engine(settings)
    await engine.start()
    try:
        engine._broker.seed_balance("kucoin", "USDT", 10_000)
        engine._broker.seed_balance("mexc", "USDT", 10_000)
        engine._broker.seed_balance("mexc", "ERG", 500)

        # ── Case 1: normal execution ────────────────────────────────
        results = await engine._execute_opportunities([_make_opportunity()])
        assert len(results) == 1, f"expected 1 result, got {len(results)}"
        r = results[0]
        assert r["status"] == "executed", f"expected executed, got {r}"
        assert r["pnl"] > 0, f"expected positive pnl, got {r['pnl']:.4f}"
        assert r["buy_fill"]["venue"] == "kucoin"
        assert r["sell_fill"]["venue"] == "mexc"
        bal = engine._broker.all_balances()
        assert bal["kucoin"]["ERG"] > 0, "buy leg must add base asset"
        assert bal["mexc"]["USDT"] > 10_000, "sell proceeds must exceed seed"
        print(f"OK execution: pnl={r['pnl']:.4f}")

        # ── Case 2: zero-size opportunity is skipped, not executed ──
        fills_before = len(engine._broker.fills)
        opp = _make_opportunity(size=0.0)
        results2 = await engine._execute_opportunities([opp])
        assert results2[0]["status"] == "skipped", (
            f"expected skipped, got {results2[0]}"
        )
        assert len(engine._broker.fills) == fills_before, (
            "skipped opportunity must not produce fills"
        )
        print("OK zero-size skip: no fills produced")

        # ── Case 3: risk-gate state is consistent after the cycle ───
        status = engine._risk_gate.get_status()
        assert status["capital"]["trade_count"] >= 1, (
            "capital scheduler must have recorded the executed trade"
        )
        assert status["capital"]["current_tier"] >= 1, (
            "paper mode must bootstrap at CANARY or above"
        )
        print(
            f"OK risk gate: tier={status['capital']['tier_name']} "
            f"trades={status['capital']['trade_count']}"
        )

        # ── Case 4: leg-B failure → emergency hedge, no unhedged ────
        real_broker = engine._broker

        class FailingSellBroker:
            """Delegates to the real broker but rejects mexc sells."""

            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            async def execute(self, intent):
                if intent.side == "sell" and intent.venue == "mexc":
                    raise RuntimeError("simulated venue rejection")
                return await self._inner.execute(intent)

        fail_broker = FailingSellBroker(real_broker)
        engine._broker = fail_broker
        engine._coordinator._broker = fail_broker
        try:
            erg_before = real_broker.all_balances().get("kucoin", {}).get("ERG", 0.0)
            results4 = await engine._execute_opportunities([_make_opportunity()])
            r4 = results4[0]
            assert r4.get("state") == "closed", (
                f"hedge path must end closed, got {r4.get('state')} ({r4})"
            )
            assert r4.get("hedge_fill"), (
                f"emergency hedge must produce a hedge fill, got {r4}"
            )
            assert r4.get("buy_fill"), "buy leg must have filled before hedge"
            # Core safety property: NO unhedged residual from THIS execution —
            # buy +20 then hedge -20 must net to zero delta on the buy venue.
            bal4 = real_broker.all_balances()
            erg_after = bal4.get("kucoin", {}).get("ERG", 0.0)
            assert abs(erg_after - erg_before) < 1e-9, (
                f"unhedged residual delta {erg_after - erg_before:.8f} ERG on kucoin!"
            )
            # Hedge + buy must both be in the ledger (confirmed-fill accounting).
            ledger_fills = engine._ledger.fills
            hedge_legs = [
                f for f in ledger_fills
                if (f.get("metadata") or {}).get("leg") in ("B", "HEDGE")
            ]
            assert hedge_legs, "hedge/leg fills must be recorded in ledger"
            print(
                f"OK leg-failure hedge: state={r4['state']} "
                f"residual=0 pnl={r4['pnl']:.4f}"
            )
        finally:
            # Restore real broker for any further cases / teardown.
            engine._broker = real_broker
            engine._coordinator._broker = real_broker
    finally:
        await engine.stop()


def main() -> None:
    asyncio.run(_run_all_cases())
    print("\nALL EXECUTION TESTS PASSED")


if __name__ == "__main__":
    main()