"""PumpPortal local-mode transaction construction (WP-6 / review item 8, 12).

Preference over the managed Lightning endpoint: the LOCAL transaction API
returns an UNSIGNED serialized transaction for AlMuden to parse, validate,
sign locally and send through its own RPC:

    AlMuden
      -> request transaction (local)
      -> receive UNSIGNED transaction
      -> TransactionValidator  (prove semantics)
      -> RiskEngine
      -> LOCAL signer
      -> RPC

This module is READ-ONLY in the current phase: no transaction is ever
constructed for execution. The methods exist so the envelope is defined, but
every build path raises ExecutionDisabledError.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from config import Settings
from trading.venues.base import ExecutionDisabledError

log = logging.getLogger(__name__)


class PumpportalClient:
    """Local-endpoint PumpPortal client. Construction is disabled by default."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._api_base = settings.pumpportal_api_base.rstrip("/")
        # Construction is gated behind an explicit flag; off by default.
        self._allow_local_tx = bool(getattr(settings, "pumpportal_local_tx", False))

    @property
    def local_tx_enabled(self) -> bool:
        return self._allow_local_tx

    async def get_new_tokens(self, limit: int = 20) -> list[Dict[str, Any]]:
        """Read-only: poll recently minted tokens for the launch watchlist."""
        # Read-only discovery - no transaction involved.
        return []

    async def get_token_events(self, mint: str, limit: int = 20) -> list[Dict[str, Any]]:
        """Read-only: token trading events for the intelligence feed."""
        return []

    async def build_buy_transaction(self, mint: str, amount_sol: float) -> Dict[str, Any]:
        """Build an UNSIGNED buy transaction from PumpPortal's local endpoint.

        Fails closed until pumpportal_local_tx is explicitly enabled. Even
        then, callers must run TransactionValidator before any signing.
        """
        if not self._allow_local_tx:
            raise ExecutionDisabledError(
                "pumpportal local transaction construction disabled "
                "(pumpportal_local_tx=false)"
            )
        # Envelope only - the actual serialized payload is intentionally not
        # produced in this phase; execution is read-only.
        raise ExecutionDisabledError(
            "pumpportal local tx construction is not yet wired for execution"
        )

    async def health(self) -> Dict[str, Any]:
        return {
            "venue": "pumpportal",
            "ok": True,
            "local_tx_enabled": self._allow_local_tx,
            "read_only": not self._allow_local_tx,
        }

    async def close(self) -> None:
        pass