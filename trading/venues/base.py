"""Venue adapter abstraction.

A venue is anything that can quote and (potentially) execute a swap:
CEX accounts, Jupiter aggregation, Pump bonding curves. All adapters
implement the same interface so the Strategy Lab and Risk Governor can
treat them uniformly.

Execution is disabled by default: adapters that cannot safely execute
must raise ExecutionDisabledError rather than silently no-op.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from trading.core import ExecutionPermit, Fill, OrderIntent


class ExecutionDisabledError(Exception):
    """Raised when a venue cannot (or must not) execute trades."""


class VenueType(str, Enum):
    CEX = "CEX"
    SOLANA_JUPITER = "SOLANA_JUPITER"
    SOLANA_PUMP = "SOLANA_PUMP"


class RiskClass(str, Enum):
    """Market risk classes. LAUNCH_TOKEN is quarantined by the risk gate."""

    BLUECHIP = "BLUECHIP"
    ALT = "ALT"
    MICROCAP = "MICROCAP"
    LAUNCH_TOKEN = "LAUNCH_TOKEN"


@dataclass
class Quote:
    """A executable-in-principle quote. Not a commitment until filled."""

    venue: str
    venue_type: VenueType
    asset_in: str  # symbol or mint
    asset_out: str  # symbol or mint
    in_amount: float
    out_amount: float  # expected executable output, after fees
    price_impact_bps: float
    network_cost: float  # estimated fees (USD)
    risk_class: RiskClass = RiskClass.ALT
    created_at: float = field(default_factory=time.time)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def age_ms(self) -> int:
        return int((time.time() - self.created_at) * 1000)

    @property
    def effective_price(self) -> float:
        return self.out_amount / self.in_amount if self.in_amount else 0.0


class VenueAdapter(ABC):
    """Uniform interface across CEX and Solana venues."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def venue_type(self) -> VenueType: ...

    @abstractmethod
    async def quote(self, asset_in: str, asset_out: str, amount: float) -> Optional[Quote]:
        """Fetch an indicative quote. Read-only, no wallet required."""

    @abstractmethod
    async def health(self) -> Dict[str, Any]:
        """Venue health snapshot for the circuit breaker."""

    async def execute(self, intent: OrderIntent, permit: ExecutionPermit) -> Fill:
        """Execute a risk-approved order. Adapters without execution
        capability raise ExecutionDisabledError (fail-closed)."""
        raise ExecutionDisabledError(
            f"venue {self.name!r} does not support execution"
        )

    async def balances(self) -> Dict[str, float]:
        """Balances in venue-native units. Empty dict = read-only venue."""
        return {}

    async def close(self) -> None:
        """Release resources."""
