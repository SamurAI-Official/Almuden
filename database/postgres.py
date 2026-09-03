"""Postgres persistence with graceful no-DB fallback.

If ``asyncpg`` is not installed or no DSN is configured, all write methods
become no-ops. The trading loop never crashes because the DB is down.
"""
from __future__ import annotations

import logging
from typing import List, Optional

log = logging.getLogger(__name__)


class NoopStore:
    """Drop-in store that swallows everything."""

    async def record_trade(self, *args, **kwargs) -> None:
        pass

    async def record_cycle(self, *args, **kwargs) -> None:
        pass

    async def get_trades(self, *args, **kwargs) -> List[dict]:
        return []

    async def close(self) -> None:
        pass


class PostgresStore:
    """Persists trades and arbitrative cycles to PostgreSQL."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None

    async def connect(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now(),
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price DOUBLE PRECISION NOT NULL,
                    size DOUBLE PRECISION NOT NULL,
                    fee DOUBLE PRECISION DEFAULT 0,
                    edge_bps DOUBLE PRECISION
                );
                CREATE TABLE IF NOT EXISTS cycles (
                    id SERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ DEFAULT now(),
                    symbol TEXT NOT NULL,
                    buy_venue TEXT NOT NULL,
                    sell_venue TEXT NOT NULL,
                    size DOUBLE PRECISION NOT NULL,
                    edge_bps DOUBLE PRECISION NOT NULL,
                    pnl DOUBLE PRECISION NOT NULL,
                    status TEXT NOT NULL
                );
                """
            )
        log.info("Postgres connected at %s", self._dsn)

    async def record_trade(
        self,
        venue: str,
        symbol: str,
        side: str,
        price: float,
        size: float,
        fee: float = 0.0,
        edge_bps: Optional[float] = None,
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO trades (venue, symbol, side, price, size, fee, edge_bps)"
                    " VALUES ($1,$2,$3,$4,$5,$6,$7)",
                    venue, symbol, side, price, size, fee, edge_bps,
                )
        except Exception:
            log.exception("Failed to record trade")

    async def record_cycle(
        self,
        symbol: str,
        buy_venue: str,
        sell_venue: str,
        size: float,
        edge_bps: float,
        pnl: float,
        status: str,
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO cycles (symbol, buy_venue, sell_venue, size,"
                    " edge_bps, pnl, status) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                    symbol, buy_venue, sell_venue, size, edge_bps, pnl, status,
                )
        except Exception:
            log.exception("Failed to record cycle")

    async def get_trades(self, limit: int = 100) -> List[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM trades ORDER BY id DESC LIMIT $1", limit
            )
            return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()


def make_store(dsn: str = ""):
    """Return a PostgresStore if *dsn* is set and asyncpg is installed, else NoopStore."""
    if not dsn:
        log.info("No Postgres DSN configured — persistence disabled")
        return NoopStore()
    try:
        import asyncpg  # noqa: F401

        store = PostgresStore(dsn)
        log.info("Using Postgres persistence")
        return store
    except ImportError:
        log.warning("asyncpg not installed — persistence disabled")
        return NoopStore()
