"""Solana venue layer: RPC, wallets, transaction validation, Jupiter, Pump.

Safety model (WP-6 of the capital-OS overhaul):

- The RPC client is read-only.
- Wallet signing requires ALL of: the optional ``solders`` dependency, an
  explicit keypair env var, and ``solana_signing_enabled=true``. Without
  every one, signing is impossible (SignerUnavailableError).
- Every externally built transaction passes TransactionValidator before
  signing; the validator is fail-closed (no parser -> no approval).
- Pump is READ-ONLY in this phase (LAUNCH_TOKEN quarantine); its adapter
  refuses execution unconditionally.
- PumpPortal is used in LOCAL mode only: it may construct unsigned
  transactions on request, but nothing in this package ever sends one.
"""
from __future__ import annotations

from trading.venues.solana.rpc import SolanaRpcClient, SolanaRpcError
from trading.venues.solana.transaction_validator import (
    SOLDERS_AVAILABLE,
    ExpectedSwap,
    TransactionValidator,
    ValidationCode,
    ValidationIssue,
    ValidationResult,
)

__all__ = [
    "SolanaRpcClient",
    "SolanaRpcError",
    "SOLDERS_AVAILABLE",
    "ExpectedSwap",
    "TransactionValidator",
    "ValidationCode",
    "ValidationIssue",
    "ValidationResult",
]