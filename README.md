# AlMuden

A continuously operating software agent that researches economic strategies,
allocates bounded capital, executes through multiple venues, measures realized
outcomes, stores experience, and progressively promotes or retires strategies.

> **Status:** Phases 0-9 implemented. Core economic kernel, risk pipeline,
> execution state machine, treasury, research layer, and market data access are live.
> The system runs in **paper mode** by default. Live trading is hard-gated behind
> explicit opt-in with persistent kill switch.

## Architecture

```
AlMuden/
??? main.py                    # CLI entrypoint
??? config.py                  # Settings loader (.env aware)
??? orchestrator/              # Engine loop, events, planner, scheduler
??? trading/
?   ??? core.py               # OrderIntent, Fill, ExecutionPermit, Opportunity
?   ??? exchange.py           # CCXT-backed venue gateway
?   ??? arbitrage/            # Scanner ? Evaluator ? Executor ? Rebalancer
?   ??? paper.py              # Paper broker (default execution path)
?   ??? live.py               # Live broker (hard-guarded)
?   ??? shadow.py             # Shadow broker (hypothetical execution)
?   ??? risk_gate.py          # Single permitted route: Intent ? Risk ? Permit ? Broker
?   ??? risk_engine.py        # Pre-trade risk gates (persistent state)
?   ??? capital_scheduler.py  # Evidence-based tier deployment (RESEARCH to MATURE)
?   ??? circuit_breaker.py    # Sliding-window breaker with aging
?   ??? execution_coordinator.py  # Multi-leg state machine with emergency hedge
?   ??? ledger.py             # Append-only confirmed-fill ledger
?   ??? treasury.py           # High-water-mark capital compounding
?   ??? strategy_lifecycle.py # 8-level promotion/demotion with event sourcing
?   ??? portfolio.py          # USDC-marked exposure tracking
?   ??? audit.py              # Audit log for compliance
?   ??? venues/
?       ??? base.py           # VenueAdapter abstraction (quote/execute/health)
?       ??? ccxt.py           # CEX venue adapter
?       ??? solana/
?           ??? jupiter.py    # Jupiter Swap V2 (quote/execute)
?           ??? pump.py       # Read-only Pump launch adapter (quarantined)
?           ??? pumpportal.py # Local tx construction (off by default)
?           ??? rpc.py        # Solana RPC client
?           ??? transaction_validator.py  # Fail-closed validator
?           ??? wallet.py     # Tiered wallets (treasury/trading/experiment)
??? strategy_lab/
?   ??? base.py               # Strategy ABC + normalized Opportunity
?   ??? registry.py           # Strategy registry
?   ??? cross_venue.py        # Cross-venue arbitrage strategy
?   ??? triangular.py         # Triangular arbitrage strategy
?   ??? momentum.py           # Momentum strategy
?   ??? backtester.py         # Event-driven backtester
?   ??? walk_forward.py       # Rolling walk-forward analysis
?   ??? simulator.py          # Monte Carlo simulation
?   ??? performance.py        # Sharpe, Sortino, drawdown, Calmar metrics
?   ??? data.py               # OHLCV DataLoader (async, Parquet caching)
??? research/
?   ??? hypothesis.py         # Immutable Hypothesis with strategy_version
?   ??? experiment.py         # Experiment with status lifecycle
?   ??? evidence.py           # Immutable Evidence (facts vs inference)
?   ??? dataset.py            # DatasetSpec with future-data-leak validation
?   ??? metrics.py            # ResearchScorecard (8 dimensions, weighted composite)
??? agents/
?   ??? researcher.py         # Research agent (observe ? hypothesize ? test)
??? memory/
?   ??? store.py              # Persistent research memory (JSONL)
??? tools/
?   ??? indicators.py         # Spread matrices, VWAP, slippage models
?   ??? news.py               # Delisting / withdrawal-suspension watch
??? database/
?   ??? redis.py              # Orderbook cache (in-memory fallback)
?   ??? postgres.py           # Trade / PnL persistence (no-DB fallback)
?   ??? qdrant.py             # Vector store (deferred)
??? api/
?   ??? routes.py             # REST endpoints (/api/status, /api/treasury, etc.)
?   ??? server.py             # Async server with WebSocket support
?   ??? static/
?       ??? index.html        # Dashboard (8 cards, WebSocket live updates)
??? scripts/
?   ??? verify_market_data.py # Market data access verification
??? tests/
    ??? test_execution.py     # Execution state-machine tests
    ??? test_shadow.py        # Shadow, lifecycle, memory, research tests
    ??? test_venues.py        # Solana venue fail-closed tests
    ??? property/
        ??? test_invariants.py  # Money-safety invariants (16 properties)
```

## Execution pipeline

```
Strategy ? TradeIntent ? RiskEngine ? CapitalScheduler ? CircuitBreaker
    ? ExecutionPermit ? Broker (Paper/Live) ? Fill ? Ledger ? Treasury
```

**The strategy/agent never reaches the broker directly.** Only an
`ExecutionPermit` can reach the signer/order router.

## Risk system

- **RiskEngine** ? max drawdown, daily loss, consecutive losses, open order limits
- **CapitalScheduler** ? evidence-based tiers (RESEARCH 0% ? CANARY 0.25-1% ?
  PROBATION 1-3% ? VERIFIED 3-10% ? PRODUCTION 10-25% ? MATURE 25-100%)
- **CircuitBreaker** ? sliding-window aging, multiple breaker types
- **Kill switch** ? persists to disk, survives restarts
- **Strategy Lifecycle** ? 8-level promotion/demotion with JSONL event sourcing
  - Single-level promotion only (no skipping)
  - Requirements verified before granting
  - Evaluate vs. decide separated (research evaluates, lifecycle decides)

## Research loop

```
OBSERVE ? HYPOTHESIZE ? EXPERIMENT ? MEASURE ? LEARN ? MODIFY ? REPEAT
```

1. **ShadowBroker** ? records hypothetical executions without capital
2. **WalkForwardAnalyzer** ? rolling train/test validation
3. **ResearchAgent** ? observes market, generates hypotheses, tests them
4. **MemoryStore** ? persistent storage for research findings
5. **StrategyLifecycle** ? promotes strategies from RESEARCH to MATURE based on evidence

### Research domain objects

- **Hypothesis** ? immutable proposal with `strategy_version`, `market_regime`
- **Experiment** ? tracks parameters before/after, dataset, execution model
- **Evidence** ? immutable record distinguishing facts from inference
- **DatasetSpec** ? validates no future data leaks into training
- **ResearchScorecard** ? 8 dimensions: Sharpe, Sortino, drawdown, Calmar, profit factor,
  sample size, train-test degradation, fold consistency

## Market data access

Verified working (as of 2026-09-05):

| Exchange | ERG/USDT | XMR/USDT | BTC/USDT | Spread (ERG) |
|----------|----------|----------|----------|--------------|
| KuCoin   | ?        | ?        | ?        | ~4bps        |
| Gate.io  | ?        | ?        | ?        | ~43bps       |
| MEXC     | ?        | ?        | ?        | ~47bps       |
| Kraken   | ?        | ?        | ?        | -            |
| WhiteBIT | ?        | ?        | ?        | -            |

**Cross-venue arbitrage pairs:**
- ERG/USDT: KuCoin ? Gate.io ? MEXC (3 venues)
- XMR/USDT: KuCoin ? MEXC ? Kraken ? WhiteBIT (4 venues)

Verify access before testing:
```bash
python scripts/verify_market_data.py
```

## Quick start

```bash
# 1. Install
pip install -r requirements.txt          # CCXT + pyarrow; redis/asyncpg optional

# 2. Configure
cp .env.example .env                     # optional ? sensible defaults exist

# 3. Verify market data access
python scripts/verify_market_data.py

# 4. Run paper engine (live order books, simulated fills)
python main.py

# 5. One-shot scan (print spread matrix, no execution)
python main.py --scan

# 6. Dry run (evaluate but do not execute)
python main.py --dry-run

# 7. Serve API + dashboard
python main.py --serve

# 8. Run tests
python -m pytest tests/ -v
```

## Design decisions

- **Capital safety is architectural** ? the risk pipeline is the only path to execution
- **Paper mode by default** ? `LIVE_KILL_SWITCH` must be explicitly enabled
- **Inventory-based execution** ? pre-positioned balances, no on-chain transfers in hot path
- **Fee-aware evaluator** ? `net_edge = spread ? fees ? slippage ? rebalance amortization`
- **CCXT for CEX adapters** ? one venue abstraction across KuCoin, Gate.io, MEXC, Kraken, WhiteBIT
- **Solana venues** ? Jupiter Swap V2 (full execution), Pump (read-only quarantined)
- **Strategies don't own capital** ? the treasury owns capital, strategies request allocation
- **Promotion is earned** ? evidence-based, demotion is automatic
- **Lifecycle transitions are event-sourced** ? JSONL log survives restarts
- **Research evaluates, lifecycle decides** ? separated control boundary

## Testing

```bash
python -m pytest tests/ -v              # 46 tests (execution, invariants, shadow, venues)
python tests/test_execution.py          # State-machine tests
python tests/property/test_invariants.py  # Money-safety property tests
python tests/test_shadow.py             # Shadow, lifecycle, memory, research tests
```

### Invariants enforced

- A strategy cannot skip a level in the lifecycle
- A demotion cannot increase capital allocation
- Risk rejection ? zero orders
- Kill switch ? zero orders
- Future data never enters training (DatasetSpec validation)
- Promotion requires evidence that requirements are met
- Lifecycle state reconstructable from event log

## Roadmap

- **Phase 0-4** ? ? Core domain model, venue abstraction, execution state machine
- **Phase 5** ? ? Historical backtesting, walk-forward analysis
- **Phase 6** ? ? Live shadow trading, paper broker
- **Phase 7** ? ? Jupiter integration
- **Phase 8** ? ? Pump market intelligence (read-only)
- **Phase 9** ? ? Strategy laboratory, research agent, memory layer
- **Phase 9.5** ? ? Research domain objects, lifecycle safety, market data verification
- **Phase 10** ? Full agent + LLM integration (Ollama)
- **Phase 11** ? Canary capital (live trading with tight limits)
- **Phase 12** ? Autonomous capital allocation
- **Phase 13** ? Strategy evolution (automated mutation/selection)

## License

CC0-1.0 ? public domain. See [LICENSE](LICENSE).
