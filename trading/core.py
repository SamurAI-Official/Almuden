"""Core trading models — the universal language of execution.

These models form the contract between strategies, risk, and execution.
Every trade flows through these types:

    Strategy → TradeIntent → RiskEngine → ExecutionPermit → Broker → Fill → Ledger
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ── Asset Classes ──────────────────────────────────────────────────────────

class AssetClass(str, Enum):
    """Classification of assets by risk profile."""
    BLUECHIP = "bluechip"
    ALT = "alt"
    MICROCAP = "microcap"
    LAUNCH_TOKEN = "launch_token"


class RiskClass(str, Enum):
    """Risk classification for strategies."""
    ARBITRAGE = "arbitrage"
    JUPITER = "jupiter"
    PUMP = "pump"
    TREND = "trend"
    MEAN_REVERSION = "mean_reversion"
    CROSS_CHAIN = "cross_chain"
    YIELD = "yield"
    REBALANCE = "rebalance"


# ── Core Models ────────────────────────────────────────────────────────────

class Fill:
    """A confirmed fill from a venue. This is the source of truth for accounting."""

    def __init__(
        self,
        venue: str,
        symbol: str,
        side: str,  # "buy" | "sell"
        size: float,
        price: float,
        fee: float = 0.0,
        cost: float = 0.0,
        proceeds: float = 0.0,
        order_id: str = "",
        timestamp: Optional[float] = None,
        status: str = "filled",  # "filled" | "partial" | "failed"
        slippage_bps: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.venue = venue
        self.symbol = symbol
        self.side = side
        self.size = size
        self.price = price
        self.fee = fee
        self.cost = cost if cost > 0 else (size * price + fee if side == "buy" else 0.0)
        self.proceeds = proceeds if proceeds > 0 else (size * price - fee if side == "sell" else 0.0)
        self.order_id = order_id
        self.timestamp = timestamp or time.time()
        self.status = status
        self.slippage_bps = slippage_bps
        self.metadata = metadata or {}

    def __repr__(self) -> str:
        return f"Fill({self.venue} {self.side} {self.size:.6f} {self.symbol} @ {self.price:.6f})"


class OrderIntent:
    """A request to execute a trade. Produced by strategies, consumed by brokers."""

    def __init__(
        self,
        venue: str,
        symbol: str,
        side: str,  # "buy" | "sell"
        size: float,
        max_price: float = 0.0,
        min_output: float = 0.0,
        ttl_ms: int = 5000,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.venue = venue
        self.symbol = symbol
        self.side = side
        self.size = size
        self.max_price = max_price
        self.min_output = min_output
        self.ttl_ms = ttl_ms
        self.metadata = metadata or {}
        self.id = str(uuid.uuid4())[:8]
        self.created_at = time.time()

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) * 1000 > self.ttl_ms

    def __repr__(self) -> str:
        return f"OrderIntent({self.venue} {self.side} {self.size:.6f} {self.symbol})"


class ExecutionPermit:
    """Produced by RiskEngine, required by Executor. Proves risk was checked."""

    def __init__(
        self,
        intent: OrderIntent,
        approved_size: float,
        approved_by: str = "RiskEngine",
        ttl_ms: int = 10000,
    ) -> None:
        self.permit_id = str(uuid.uuid4())[:12]
        self.intent = intent
        self.approved_size = approved_size
        self.approved_by = approved_by
        self.timestamp = time.time()
        self.ttl_ms = ttl_ms

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) * 1000 > self.ttl_ms

    def __repr__(self) -> str:
        return f"Permit({self.permit_id} {self.intent} size={self.approved_size:.6f})"