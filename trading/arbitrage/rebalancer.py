"""Inventory rebalancer.

Detects when per-venue asset balances have drifted beyond the configured
threshold and emits rebalance actions. In paper mode these are advisory;
in live mode (Phase 7) they would trigger withdrawals / deposits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from config import Settings

log = logging.getLogger(__name__)


@dataclass
class RebalanceAction:
    asset: str
    from_venue: str
    to_venue: str
    amount: float
    reason: str


class Rebalancer:
    """Check inventory drift and emit rebalance actions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def check(
        self, balances: Dict[str, Dict[str, float]], prices: Dict[str, float]
    ) -> List[RebalanceAction]:
        """Return rebalance actions needed to restore target allocation.

        *balances* is venue -> asset -> amount.
        *prices* is symbol -> price (for USD valuation).
        """
        actions: List[RebalanceAction] = []
        # Aggregate total holdings per asset across venues.
        totals: Dict[str, float] = {}
        venue_holdings: Dict[str, Dict[str, float]] = {}
        for venue, assets in balances.items():
            for asset, amount in assets.items():
                if amount <= 0:
                    continue
                totals[asset] = totals.get(asset, 0.0) + amount
                venue_holdings.setdefault(venue, {})[asset] = amount

        for asset, total in totals.items():
            if total <= 0:
                continue
            target_per_venue = total / max(len(balances), 1)
            for venue, assets in venue_holdings.items():
                held = assets.get(asset, 0.0)
                drift = abs(held - target_per_venue)
                drift_frac = drift / total if total > 0 else 0.0
                if drift_frac * 10_000 > self._settings.max_drift_bps:
                    # Find a venue that is underweight.
                    for other_venue, other_assets in venue_holdings.items():
                        if other_venue == venue:
                            continue
                        other_held = other_assets.get(asset, 0.0)
                        if other_held < target_per_venue:
                            move = min(drift, target_per_venue - other_held)
                            if move > 0:
                                actions.append(
                                    RebalanceAction(
                                        asset=asset,
                                        from_venue=venue,
                                        to_venue=other_venue,
                                        amount=move,
                                        reason=f"drift {drift_frac*10_000:.0f} bps",
                                    )
                                )
                            break
        return actions
