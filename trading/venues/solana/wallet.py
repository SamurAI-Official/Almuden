"""Tiered Solana wallet with a fail-closed signing gate.

Wallet architecture (WP-6 / review item 18):

    TREASURY (cold reserve)  -- keys never in process; pubkeys only
        |  limited, manual transfers
    TRADING (production hot wallet)  -- bounded working capital
        |  strict allocation
    EXPERIMENT (isolated lab wallet)  -- blast-radius containment

Signing requires ALL of:
  1. the optional ``solders`` dependency installed,
  2. ``solana_signing_enabled=true`` in settings,
  3. a keypair present in the env var named by ``solana_keypair_env``.

Missing any one => :class:`SignerUnavailableError`. There is no code path
that signs without all three preconditions; the private key is loaded
lazily, held in memory only, and never logged, serialized, or returned.
"""
from __future__ import annotations

import json
import logging
import os
from enum import Enum
from typing import Any, Dict, Optional

from config import Settings
from trading.venues.solana.rpc import SolanaRpcClient

log = logging.getLogger(__name__)

try:  # optional dependency - the signer layer fails closed without it
    from solders.keypair import Keypair as _SoldersKeypair  # type: ignore

    SOLDERS_KEYPAIR_AVAILABLE = True
except Exception:  # noqa: BLE001 - import guard must never raise
    _SoldersKeypair = None  # type: ignore[assignment,misc]
    SOLDERS_KEYPAIR_AVAILABLE = False


class SignerUnavailableError(Exception):
    """Raised when signing is attempted without every precondition met."""


class WalletTier(str, Enum):
    """Accounting identity of each wallet. Every transfer names its tier."""

    TREASURY = "TREASURY"
    TRADING = "TRADING"
    EXPERIMENT = "EXPERIMENT"


# Tier -> config setting holding the pubkey (never a private key).
_TIER_PUBKEY_SETTING: Dict[WalletTier, str] = {
    WalletTier.TREASURY: "solana_treasury_address",
    WalletTier.TRADING: "solana_trading_address",
    WalletTier.EXPERIMENT: "solana_experiment_address",
}


class SolanaWallet:
    """Tiered wallet facade: pubkeys always visible, private key never.

    The treasury tier is special: it holds NO signer at all. Treasury
    movements are manual, out-of-band operations. Only the trading and
    experiment tiers can potentially sign, and only through the gated
    :meth:`sign_transaction` method.
    """

    def __init__(self, settings: Settings, tier: WalletTier) -> None:
        self._settings = settings
        self._tier = tier
        self._keypair: Optional[Any] = None  # loaded lazily, held in memory
        self._keypair_loaded = False

    # -- Identity ----------------------------------------------------------

    @property
    def tier(self) -> WalletTier:
        return self._tier

    @property
    def pubkey(self) -> str:
        """Configured pubkey for this tier ("" when unset)."""
        return getattr(self._settings, _TIER_PUBKEY_SETTING[self._tier], "") or ""

    @property
    def is_configured(self) -> bool:
        return bool(self.pubkey)

    # -- Private key lifecycle (fail-closed, lazily loaded) -----------------

    def _load_keypair(self) -> Any:
        """Load the keypair once. Raises SignerUnavailableError on any gap."""
        if self._tier == WalletTier.TREASURY:
            raise SignerUnavailableError(
                "treasury keys never live inside AlMuden - move funds manually"
            )
        if not self._settings.solana_signing_enabled:
            raise SignerUnavailableError(
                "solana_signing_enabled=false - signing is disabled by config"
            )
        if not SOLDERS_KEYPAIR_AVAILABLE:
            raise SignerUnavailableError(
                "solders is not installed - cannot sign (fail-closed). "
                "Install with: pip install solders"
            )
        env_name = self._settings.solana_keypair_env
        if not env_name:
            raise SignerUnavailableError(
                "solana_keypair_env is empty - no env var named for the keypair"
            )
        secret = os.environ.get(env_name, "")
        if not secret:
            raise SignerUnavailableError(
                f"env var {env_name!r} is empty - keypair not provisioned"
            )
        if self._keypair_loaded:
            return self._keypair

        # Accept both JSON byte-array (solana-keygen format) and base58.
        parsed: Any = None
        try:
            payload = json.loads(secret)
            if isinstance(payload, list) and all(
                isinstance(b, int) and 0 <= b <= 255 for b in payload
            ):
                parsed = _SoldersKeypair.from_bytes(bytes(payload))  # type: ignore[union-attr]
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if parsed is None:
            try:
                parsed = _SoldersKeypair.from_base58(secret.strip())  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                raise SignerUnavailableError(
                    f"keypair in {env_name!r} is neither JSON byte-array nor "
                    f"base58 - refusing to use it"
                ) from exc
        # Sanity: the loaded keypair must match the configured pubkey.
        expected = self.pubkey
        actual = str(parsed.pubkey())
        if expected and actual != expected:
            raise SignerUnavailableError(
                f"keypair pubkey {actual[:8]}... does not match configured "
                f"{self._tier.value} address {expected[:8]}... - refusing to sign"
            )
        self._keypair = parsed
        self._keypair_loaded = True
        log.info("signer loaded for %s tier (%s...)", self._tier.value, actual[:8])
        return self._keypair

    # -- Signing / sending --------------------------------------------------

    def sign_transaction(self, tx_bytes: bytes) -> bytes:
        """Sign a fully-formed, ALREADY-VALIDATED transaction.

        Callers must run TransactionValidator BEFORE this method; the
        wallet has no authority to approve transaction semantics.
        """
        kp = self._load_keypair()  # raises SignerUnavailableError if blocked
        signed = kp.sign_message(tx_bytes)
        return bytes(signed)

    async def send_raw_transaction(
        self, rpc: SolanaRpcClient, signed_tx_b64: str
    ) -> str:
        """Submit a signed, base64 transaction. Returns the signature."""
        result = await rpc.call(
            "sendTransaction",
            [signed_tx_b64, {"encoding": "base64", "skipPreflight": False}],
        )
        return str(result or "")

    async def balance_sol(self, rpc: SolanaRpcClient) -> float:
        if not self.is_configured:
            return 0.0
        return await rpc.get_balance_sol(self.pubkey)

    # -- Signing capability gate --------------------------------------------

    @property
    def can_sign(self) -> bool:
        """True only when every signing precondition is met."""
        if self._tier == WalletTier.TREASURY:
            return False  # treasury keys never exist inside AlMuden
        if not self._settings.solana_signing_enabled:
            return False
        if not SOLDERS_KEYPAIR_AVAILABLE:
            return False
        return bool(self._settings.solana_keypair_env) and bool(
            os.environ.get(self._settings.solana_keypair_env)
        )

    def signing_blockers(self) -> Dict[str, bool]:
        """Which preconditions are unmet. For health/diagnostics output."""
        return {
            "treasury_tier_no_signer": self._tier == WalletTier.TREASURY,
            "signing_disabled": not self._settings.solana_signing_enabled,
            "solders_missing": not SOLDERS_KEYPAIR_AVAILABLE,
            "keypair_env_unset": not bool(self._settings.solana_keypair_env),
            "keypair_env_empty": (
                bool(self._settings.solana_keypair_env)
                and not os.environ.get(self._settings.solana_keypair_env)
            ),
        }

    def __repr__(self) -> str:
        return (
            f"SolanaWallet({self._tier.value} pubkey={self.pubkey[:8] or '-'} "
            f"can_sign={self.can_sign})"
        )


def build_wallets(settings: Settings) -> Dict[WalletTier, SolanaWallet]:
    """Build the three-tier wallet set from settings."""
    return {tier: SolanaWallet(settings, tier) for tier in WalletTier}
