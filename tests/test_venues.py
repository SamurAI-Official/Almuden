"""WP-6 venue layer tests - the fail-closed guarantees.

Everything Solana must refuse to act unless every precondition holds:

    * create_venues must NOT register solana venues by default
    * Jupiter execute must raise without signing capability
    * Pump must NEVER execute (read-only, quarantined LAUNCH_TOKEN class)
    * TransactionValidator must reject without solders (fail-closed)
    * Pumpportal local tx construction must be off by default
    * Treasury wallet must never expose a signer

Run: python tests\\test_venues.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_settings  # noqa: E402
from trading.core import ExecutionPermit, Fill, OrderIntent  # noqa: E402
from trading.exchange import ExchangeGateway  # noqa: E402
from trading.venues import create_venues  # noqa: E402
from trading.venues.base import (  # noqa: E402
    ExecutionDisabledError,
    VenueType,
)

os.environ.setdefault("ALMUDEN_MODE", "paper")


def _solana_settings():
    s = load_settings()
    s.solana_enabled = True
    s.solana_rpc_url = "https://api.mainnet-beta.solana.com"
    s.pump_enabled = True
    # Signing deliberately left OFF - every test must fail closed.
    return s


def _intent(symbol: str = "So11111111111111111111111111111111111111112>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"):
    return OrderIntent(venue="jupiter", symbol=symbol, side="buy", size=1_000_000)


def _permit() -> ExecutionPermit:
    return ExecutionPermit(intent=_intent(), approved_size=1_000_000)


def _run(coro):
    return asyncio.run(coro)


def test_default_no_solana_venues() -> None:
    s = load_settings()
    gateway = ExchangeGateway(s)
    venues = create_venues(s, gateway)
    assert "jupiter" not in venues
    assert "pump" not in venues
    print("OK default: no solana venues registered")


def test_jupiter_execute_fails_closed_without_signing() -> None:
    from trading.venues.solana.jupiter import JupiterAdapter

    s = _solana_settings()
    adapter = JupiterAdapter(s)
    assert adapter.venue_type == VenueType.SOLANA_JUPITER
    assert adapter.trading_wallet.can_sign is False
    try:
        _run(adapter.execute(_intent(), _permit()))
        raise AssertionError("jupiter execute should have raised")
    except ExecutionDisabledError as exc:
        assert "signing disabled" in str(exc)
    _run(adapter.close())
    print("OK jupiter: execute fails closed without signing")


def test_pump_never_executes() -> None:
    from trading.venues.solana.pump import PumpAdapter

    s = _solana_settings()
    adapter = PumpAdapter(s)
    assert adapter.venue_type == VenueType.SOLANA_PUMP
    try:
        _run(adapter.execute(_intent(), _permit()))
        raise AssertionError("pump execute should have raised")
    except ExecutionDisabledError as exc:
        assert "READ-ONLY" in str(exc)
    assert _run(adapter.quote("in", "out", 1.0)) is None
    _run(adapter.close())
    print("OK pump: read-only, never executes, no quotes")


def test_transaction_validator_fails_closed_without_solders() -> None:
    from trading.venues.solana.transaction_validator import (
        SOLDERS_AVAILABLE,
        ExpectedSwap,
        TransactionValidator,
    )

    validator = TransactionValidator()
    expected = ExpectedSwap(
        taker_pubkey="11111111111111111111111111111111",
        input_mint="in",
        output_mint="out",
        in_amount_raw=1000,
        min_out_raw=900,
        max_priority_fee_lamports=1_000_000,
    )
    result = validator.validate("AAAA", expected)
    assert result.approved is False
    if not SOLDERS_AVAILABLE:
        assert any("PARSER_UNAVAILABLE" == i.code for i in result.issues)
    print(f"OK validator: fails closed (solders available: {SOLDERS_AVAILABLE})")


def test_pumpportal_tx_off_by_default() -> None:
    from trading.venues.solana.pumpportal import PumpportalClient

    s = load_settings()
    client = PumpportalClient(s)
    assert client.local_tx_enabled is False
    try:
        _run(client.build_buy_transaction("mint", 0.1))
        raise AssertionError("pumpportal tx build should have raised")
    except ExecutionDisabledError:
        pass
    _run(client.close())
    print("OK pumpportal: tx construction disabled by default")


def test_treasury_wallet_has_no_signer() -> None:
    from trading.venues.solana.wallet import (
        SignerUnavailableError,
        SolanaWallet,
        WalletTier,
    )

    s = load_settings()
    treasury = SolanaWallet(s, WalletTier.TREASURY)
    assert treasury.can_sign is False
    assert treasury.signing_blockers()["treasury_tier_no_signer"] is True
    try:
        treasury.sign_transaction(b"abc")
        raise AssertionError("treasury sign should have raised")
    except SignerUnavailableError:
        pass
    print("OK wallet: treasury tier can never sign")


def main() -> None:
    test_default_no_solana_venues()
    test_jupiter_execute_fails_closed_without_signing()
    test_pump_never_executes()
    test_transaction_validator_fails_closed_without_solders()
    test_pumpportal_tx_off_by_default()
    test_treasury_wallet_has_no_signer()
    print("\nALL WP-6 VENUE TESTS PASSED")


if __name__ == "__main__":
    main()