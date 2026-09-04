"""Tests for shadow broker, strategy lifecycle, memory, and research agent."""
from __future__ import annotations
import asyncio
import unittest
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from config import Settings
from trading.shadow import ShadowBroker, ShadowSnapshot
from trading.core import OrderIntent, Fill
from trading.strategy_lifecycle import StrategyLifecycle
from memory.store import MemoryStore


class TestShadowBroker(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="paper", venues=["kucoin", "gateio"])
        self.broker = ShadowBroker(self.settings)

    def test_execute_buy(self):
        intent = OrderIntent(venue="kucoin", symbol="ERG/USDT", side="buy", size=100.0, max_price=1.5)
        loop = asyncio.new_event_loop()
        fill = loop.run_until_complete(self.broker.execute(intent, strategy="cross_venue"))
        loop.close()
        self.assertEqual(fill.venue, "kucoin")
        self.assertEqual(fill.side, "buy")
        self.assertTrue(fill.metadata.get("shadow"))

    def test_execute_sell(self):
        intent = OrderIntent(venue="gateio", symbol="ERG/USDT", side="sell", size=50.0, max_price=1.6)
        loop = asyncio.new_event_loop()
        fill = loop.run_until_complete(self.broker.execute(intent, strategy="cross_venue"))
        loop.close()
        self.assertEqual(fill.side, "sell")
        self.assertGreater(fill.proceeds, 0)

    def test_trades_recorded(self):
        loop = asyncio.new_event_loop()
        for i in range(5):
            intent = OrderIntent(venue="kucoin", symbol="ERG/USDT", side="buy", size=10.0, max_price=1.0)
            loop.run_until_complete(self.broker.execute(intent, strategy="test"))
        loop.close()
        self.assertEqual(len(self.broker.trades), 5)

    def test_equity_curve(self):
        loop = asyncio.new_event_loop()
        for i in range(3):
            intent = OrderIntent(venue="kucoin", symbol="ERG/USDT", side="buy", size=10.0, max_price=1.0)
            loop.run_until_complete(self.broker.execute(intent, strategy="test"))
        loop.close()
        self.assertEqual(len(self.broker.equity_curve), 4)

    def test_snapshot(self):
        loop = asyncio.new_event_loop()
        for i in range(10):
            intent = OrderIntent(venue="kucoin", symbol="ERG/USDT", side="buy", size=10.0, max_price=1.0)
            loop.run_until_complete(self.broker.execute(intent, strategy="test"))
        loop.close()
        snapshot = self.broker.snapshot("test")
        self.assertEqual(snapshot.total_trades, 10)

    def test_compare_with_paper(self):
        loop = asyncio.new_event_loop()
        intent = OrderIntent(venue="kucoin", symbol="ERG/USDT", side="buy", size=100.0, max_price=1.5)
        loop.run_until_complete(self.broker.execute(intent, strategy="test"))
        loop.close()
        paper_fills = [Fill(venue="kucoin", symbol="ERG/USDT", side="buy", size=100.0, price=1.5, fee=0.15, cost=150.15, proceeds=0.0)]
        comparison = self.broker.compare_with_paper(paper_fills)
        self.assertIn("shadow_pnl", comparison)
        self.assertIn("slippage_estimate", comparison)

    def test_reset(self):
        loop = asyncio.new_event_loop()
        intent = OrderIntent(venue="kucoin", symbol="ERG/USDT", side="buy", size=10.0, max_price=1.0)
        loop.run_until_complete(self.broker.execute(intent, strategy="test"))
        loop.close()
        self.broker.reset()
        self.assertEqual(len(self.broker.trades), 0)


class TestStrategyLifecycle(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(mode="paper", venues=["kucoin"])
        self.lifecycle = StrategyLifecycle(self.settings)

    def test_register(self):
        state = self.lifecycle.register("cross_arena_v1")
        self.assertEqual(state.deployment_level, "RESEARCH")
        self.assertTrue(state.can_trade_shadow)
        self.assertFalse(state.can_trade_live)

    def test_promote(self):
        self.lifecycle.register("test_strategy")
        result = self.lifecycle.promote("test_strategy", "SHADOW")
        self.assertTrue(result)
        state = self.lifecycle.get_state("test_strategy")
        self.assertEqual(state.deployment_level, "SHADOW")

    def test_demote(self):
        self.lifecycle.register("test_strategy")
        self.lifecycle.promote("test_strategy", "PAPER")
        result = self.lifecycle.demote("test_strategy", "poor performance")
        self.assertTrue(result)
        state = self.lifecycle.get_state("test_strategy")
        # Demote by 1 level: PAPER -> SHADOW
        self.assertEqual(state.deployment_level, "SHADOW")

    def test_cannot_demote_below_research(self):
        self.lifecycle.register("test_strategy")
        self.lifecycle.demote("test_strategy", "test", levels=5)
        state = self.lifecycle.get_state("test_strategy")
        self.assertEqual(state.deployment_level, "RESEARCH")

    def test_check_health_unhealthy(self):
        self.lifecycle.register("test_strategy")
        self.lifecycle.promote("test_strategy", "CANARY")
        self.lifecycle.update_live_metrics("test_strategy", trades=30, pnl=-10.0, drawdown_pct=15.0)
        health = self.lifecycle.check_health("test_strategy")
        self.assertFalse(health["healthy"])
        self.assertEqual(health["action"], "demote")

    def test_get_capital_allocation_research(self):
        self.lifecycle.register("test_strategy")
        allocation = self.lifecycle.get_capital_allocation("test_strategy", 10000.0)
        self.assertEqual(allocation, 0.0)

    def test_get_capital_allocation_canary(self):
        self.lifecycle.register("test_strategy")
        self.lifecycle.promote("test_strategy", "CANARY")
        allocation = self.lifecycle.get_capital_allocation("test_strategy", 10000.0)
        self.assertGreater(allocation, 0.0)

class TestMemoryStore(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.settings = Settings(mode="paper", venues=["kucoin"], memory_dir=self.tmpdir)
        self.memory = MemoryStore(self.settings)

    def test_store_and_query(self):
        self.memory.store("research", "test_strat", "Test finding", data={"key": "value"})
        entries = self.memory.query(category="research")
        self.assertEqual(len(entries), 1)

    def test_query_by_strategy(self):
        self.memory.store("research", "strat_a", "Finding A")
        self.memory.store("research", "strat_b", "Finding B")
        entries = self.memory.query(strategy_id="strat_a")
        self.assertEqual(len(entries), 1)

    def test_summarize_recent(self):
        self.memory.store("research", "test", "Recent finding")
        summary = self.memory.summarize_recent(hours=1.0)
        self.assertIn("Recent finding", summary)


class TestResearchAgent(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.settings = Settings(mode="paper", venues=["kucoin"], memory_dir=self.tmpdir)
        self.memory = MemoryStore(self.settings)
        self.lifecycle = StrategyLifecycle(self.settings)
        from agents.researcher import ResearchAgent
        self.agent = ResearchAgent(self.settings, self.memory, self.lifecycle)

    def test_observe_market_healthy(self):
        metrics = {"sharpe_ratio": 1.5, "max_drawdown_pct": 3.0, "total_pnl": 10.0, "total_trades": 50}
        result = self.agent.observe_market("test_strat", metrics, {"volatility": "low"})
        self.assertEqual(len(result["issues"]), 0)

    def test_observe_market_unhealthy(self):
        metrics = {"sharpe_ratio": 0.2, "max_drawdown_pct": 15.0, "total_pnl": -5.0, "total_trades": 50}
        result = self.agent.observe_market("test_strat", metrics, {"volatility": "high"})
        self.assertGreater(len(result["issues"]), 0)

    def test_generate_hypothesis(self):
        observation = {"issues": ["Low Sharpe ratio: 0.20"], "suggestions": []}
        hypothesis = self.agent.generate_hypothesis("test_strat", observation)
        self.assertIsNotNone(hypothesis)
        self.assertIn("min_edge_bps", hypothesis.parameter_changes)

    def test_generate_hypothesis_no_issues(self):
        observation = {"issues": [], "suggestions": []}
        hypothesis = self.agent.generate_hypothesis("test_strat", observation)
        self.assertIsNone(hypothesis)


if __name__ == "__main__":
    unittest.main()
