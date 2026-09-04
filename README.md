# AlMuden

A continuously operating software agent that researches economic strategies,
allocates bounded capital, executes through multiple venues, measures realized
outcomes, stores experience, and progressively promotes or retires strategies.

> **Status:** Phases 0-9 implemented. Core economic kernel, risk pipeline,
> execution state machine, treasury, and research layer are live. The system
> runs in **paper mode** by default. Live trading is hard-gated behind
> explicit opt-in with persistent kill switch.

## Architecture

```

## Execution pipeline

```
Strategy → TradeIntent → RiskEngine → CapitalScheduler → CircuitBreaker
    → ExecutionPermit → Broker (Paper/Live) → Fill → Ledger → Treasury
```

**The strategy/agent never reaches the broker directly.** Only an
`ExecutionPermit` can reach the signer/order router.

## Risk system

- **RiskEngine** — max drawdown, daily loss, consecutive losses, open order limits
- **CapitalScheduler** — evidence-based tiers (RESEARCH 0% → CANARY 0.25-1% →
  PROBATION 1-3% → VERIFIED 3-10% → PRODUCTION 10-25% → MATURE 25-100%)
- **CircuitBreaker** — sliding-window aging, multiple breaker types
- **Kill switch** — persists to disk, survives restarts
- **Strategy Lifecycle** — 8-level promotion/demotion based on evidence

## Research loop

```
OBSERVE → HYPOTHESIZE → EXPERIMENT → MEASURE → LEARN → MODIFY → REPEAT
```

1. **ShadowBroker** — records hypothetical executions without capital
2. **WalkForwardAnalyzer** — rolling train/test validation
3. **ResearchAgent** — observes market, generates hypotheses, tests them
4. **MemoryStore** — persistent storage for research findings
5. **StrategyLifecycle** — promotes strategies from RESEARCH to MATURE based on evidence

## Quick start

```bash
# 1. Install
pip install -r requirements.txt          # CCXT only; redis/asyncpg optional

# 2. Configure
cp .env.example .env                     # optional — sensible defaults exist

# 3. Run paper engine (live order books, simulated fills)
python main.py

# 4. One-shot scan (print spread matrix, no execution)
python main.py --scan

# 5. Dry run (evaluate but do not execute)
python main.py --dry-run

# 6. Serve API + dashboard
python main.py --serve

# 7. Run tests
python -m pytest tests/ -v
```

## Design decisions

- **Capital safety is architectural** — the risk pipeline is the only path to execution
- **Paper mode by default** — `LIVE_KILL_SWITCH` must be explicitly enabled
- **Inventory-based execution** — pre-positioned balances, no on-chain transfers in hot path
- **Fee-aware evaluator** — `net_edge = spread − fees − slippage − rebalance amortization`
- **CCXT for CEX adapters** — one venue abstraction across KuCoin, Gate.io, MEXC, Kraken, WhiteBIT
- **Solana venues** — Jupiter Swap V2 (full execution), Pump (read-only quarantined)
- **Strategies don't own capital** — the treasury owns capital, strategies request allocation
- **Promotion is earned** — evidence-based, demotion is automatic

## Testing

```bash
python -m pytest tests/ -v              # 43 tests (execution, invariants, shadow, venues)
python tests/test_execution.py          # State-machine tests
python tests/property/test_invariants.py  # Money-safety property tests
python tests/test_shadow.py             # Shadow, lifecycle, memory, research tests
```

## Roadmap

- **Phase 0-4** ✅ — Core domain model, venue abstraction, execution state machine
- **Phase 5** ✅ — Historical backtesting, walk-forward analysis
- **Phase 6** ✅ — Live shadow trading, paper broker
- **Phase 7** ✅ — Jupiter integration
- **Phase 8** ✅ — Pump market intelligence (read-only)
- **Phase 9** ✅ — Strategy laboratory, research agent, memory layer
- **Phase 10** — Full agent + LLM integration (Ollama)
- **Phase 11** — Canary capital (live trading with tight limits)
- **Phase 12** — Autonomous capital allocation
- **Phase 13** — Strategy evolution (automated mutation/selection)

## License

CC0-1.0 — public domain. See [LICENSE](LICENSE).
