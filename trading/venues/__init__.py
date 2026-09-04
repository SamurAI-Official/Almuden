"""Venue adapters: uniform interface across CEX and Solana execution.

WP-6 of the capital-OS overhaul. Every adapter implements VenueAdapter so
the Strategy Lab, Risk Governor and ExecutionCoordinator treat all venues
identically. Execution is fail-closed: adapters without signing capability
raise ExecutionDisabledError rather than silently no-op.
"""
from __future__ import annotations

import logging
from typing import Dict

from config import Settings
from trading.exchange import ExchangeGateway
from trading.venues.base import (
    ExecutionDisabledError,
    Quote,
    RiskClass,
    VenueAdapter,
    VenueType,
)
from trading.venues.ccxt import CCXTAdapter

log = logging.getLogger(__name__)

__all__ = [
    "ExecutionDisabledError",
    "Quote",
    "RiskClass",
    "VenueAdapter",
    "VenueType",
    "CCXTAdapter",
    "create_venues",
]


def create_venues(settings: Settings, gateway: ExchangeGateway) -> Dict[str, VenueAdapter]:
    """Build the venue registry from settings.

    CEX venues always load (backed by the shared gateway). Solana venues
    load only when explicitly enabled AND configured; a mismatch logs a
    warning and skips rather than half-initialising a venue.
    """
    venues: Dict[str, VenueAdapter] = {}
    for venue_id in settings.venues:
        venues[venue_id] = CCXTAdapter(settings, gateway, venue_id)

    if settings.solana_enabled:
        if not settings.solana_rpc_url:
            log.warning("solana_enabled=true but solana_rpc_url empty - skipping Jupiter")
        else:
            from trading.venues.solana.jupiter import JupiterAdapter

            venues["jupiter"] = JupiterAdapter(settings)
    if settings.pump_enabled:
        from trading.venues.solana.pump import PumpAdapter

        venues["pump"] = PumpAdapter(settings)
    return venues