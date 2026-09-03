# AlMuden

An Ollama-driven multi-agent crypto trading system with ERG/XMR cross-venue
and triangular arbitrage scraping.

> **Status:** Phases 0–4 scaffolded (core engine, exchange adapters, arbitrage
> scanner/evaluator/executor). The agents/LLM layer and live trading are
> intentionally deferred — the system runs in **paper mode** by default.

## Architecture

```
AlMuden/
├── main.py                    # CLI entrypoint
├── config.py                  # Settings loader (.env aware)
├── orchestrator/              # Engine loop, events, planner, scheduler
├── trading/
│   ├── exchange.py            # CCXT-backed venue gateway
│   ├── arbitrage/             # Scanner → Evaluator → Executor → Rebalancer
│   ├── paper.py               # Paper broker (default execution path)
│   ├── live.py                # Live broker (Phase 7, hard-guarded)
│   ├── strategies.py          # Strategy registry
│   └── backtest.py            # Backtester (deferred)
├── tools/
│   ├── indicators.py          # Spread matrices, VWAP, slippage models
│   └── news.py                # Delisting / withdrawal-suspension watch
├── database/
│   ├── redis.py               # Orderbook cache (in-memory fallback)
│   ├── postgres.py            # Trade / PnL persistence (no-DB fallback)
│   └── qdrant.py              # Vector store (deferred)
├── agents/                    # LLM agents (deferred)
├── brain/                     # Ollama prompts & embeddings (deferred)
├── memory/                    # Episodic / semantic memory (deferred)
└── api/                       # REST + WebSocket API (deferred)
```

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
```

## Design decisions

- **CCXT for all adapters** — one venue abstraction across KuCoin, Gate.io,
  MEXC, Kraken, WhiteBIT.
- **Inventory-based execution** — pre-positioned balances, no on-chain
  transfers in the hot path. Two-leg cycles settle against the books.
- **Fee-aware evaluator** — `net_edge = spread − fees − slippage − rebalance
  amortization`. No trade clears the gate unless net edge is positive.
- **Agents/LLM are advisory-only** — they never touch the order router.
- **Paper mode by default** — `LIVE_KILL_SWITCH` must be explicitly enabled.

## Market landscape (ERG / XMR)

| Venue    | ERG pairs  | XMR pairs          | Notes |
|----------|-----------|--------------------|-------|
| KuCoin   | ERG/USDT  | XMR/USDT, XMR/BTC  | Only venue listing both → triangular route hub |
| Gate.io  | ERG/USDT  | —                  | Thin depth |
| MEXC     | ERG/USDT  | —                  | Deepest ERG book |
| Kraken   | —         | XMR/USD, XMR/USDT  | Largest XMR liquidity |
| WhiteBIT | —         | XMR/USDT           | Secondary XMR venue |

No ERG↔XMR pair or production atomic swap exists. KuCoin is the overlap venue;
XMR delisting/withdrawal monitoring is mandatory (Binance/OKX already delisted).

## Roadmap

- **Phase 0–4** ✅ — scaffold, engine, adapters, indicators, arbitrage engine
- **Phase 5** — triangular ERG↔XMR design
- **Phase 6** — backtest / paper gate
- **Phase 7** — live trading + agent/LLM layer

## License

CC0-1.0 — public domain. See [LICENSE](LICENSE).
