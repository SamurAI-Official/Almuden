"""Pump.fun adapter - a QUARANTINED high-risk market class (WP-6 / review item 7).

Asset class: LAUNCH_TOKEN. Bonding-curve constant-product AMM pricing,
extreme impact, no durable liquidity early. Not a symbol list - a separate
risk domain with a separate capital budget. No Pump strategy may threaten
the core treasury.

This phase is READ-ONLY:
  * quote()  always returns None (bonding-curve quotes intentionally
             disabled until the transaction validator + strategy research
             phase is complete),
  * execute() ALWAYS raises ExecutionDisabledError (fail-closed),
  * balances() returns {} (no wallet interaction).

The venue still participates in the health surface so the operator can see
it is present but inert.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from config import Settings
from trading.core import ExecutionPermit, Fill, OrderIntent
from trading.venues.base import (
    ExecutionDisabledError,
    Quote,
    RiskClass,
    VenueAdapter,
    VenueType,
)
from trading.venues.solana.rpc import SolanaRpcClient

log = logging.getLogger(__name__)


class PumpAdapter(VenueAdapter):
    """Read-only Pump.fun venue. Hard no-op for any execution access."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._rpc = (
            SolanaRpcClient(settings.solana_rpc_url, settings.solana_commitment)
            if settings.solana_rpc_url
            else None
        )
        self._last_health: Dict[str, Any] = {}

    # -- Identity ----------------------------------------------------

    @property
    def name(self) -> str:
        return "pump"

    @property
    def venue_type(self) -> VenueType:
        return VenueType.SOLANA_PUMP

    # -- Read-only surface --------------------------------------------------

    async def quote(
        self, asset_in: str, asset_out: str, amount: float
    ) -> Optional[Quote]:
        """No quotes in the read-only phase.

        Returns None so the scanner treats Pump as an absent optional venue
        rather than a data source we might accidentally act on.
        """
        log.debug("pump quote requested but pump is READ-ONLY; returning None")
        return None

    async def execute(self, intent: OrderIntent, permit: ExecutionPermit) -> Fill:
        raise ExecutionDisabledError(
            "pump venue is READ-ONLY (quarantined LAUNCH_TOKEN class); "
            "execution disabled until transaction validator + strategy "
            "research completes"
        )

    async def balances(self) -> Dict[str, float]:
        return {}

    # -- Health ----------------------------------------------------------

    async def health(self) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {
            "venue": self.name,
            "ok": True,  # healthy = present and correctly inert
            "read_only": True,
            "risk_class": RiskClass.LAUNCH_TOKEN.value,
            "quarantined": True,
        }
        if self._rpc is not None:
            rpc_health = await self._rpc.health()
            snapshot["rpc"] = rpc_health
            if not rpc_health.get("ok"):
                snapshot["ok"] = False
        else:
            snapshot["rpc"] = {"ok": False, "reason": "solana_rpc_url not set"}
            snapshot["ok"] = False
        self._last_health = snapshot
        return snapshot

    async def close(self) -> None:
        if self._rpc is not None:
            await self._rpc.close()