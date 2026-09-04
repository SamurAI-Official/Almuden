"""Jupiter adapter - AlMuden's Solana execution backbone (WP-6 / review item 5).

Uses Jupiter Swap API V2 (Meta-Aggregator):

    GET  /swap/v2/order       -> unsigned serialized transaction
    TransactionValidator      -> PROVE it expresses the authorized swap
    wallet.sign_transaction   -> local sign (fail-closed, optional solders)
    POST /swap/v2/execute     -> Jupiter lands it, handles retries

The read-only quote path (v6 /quote) needs no taker wallet, so it backs
VenueAdapter.quote() for market discovery before any transaction is built.

FAIL-CLOSED: every precondition must hold before execute() proceeds.
Any gap raises ExecutionDisabledError. Nothing is ever sent to the chain
without (a) signing enabled, (b) solders installed, (c) a configured keypair,
and (d) the TransactionValidator approving the exact swap semantics.
"""
from __future__ import annotations

import base64
import logging
import math
import time
from typing import Any, Dict, Optional

import aiohttp

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
from trading.venues.solana.transaction_validator import (
    ExpectedSwap,
    TransactionValidator,
)
from trading.venues.solana.wallet import SolanaWallet, WalletTier, build_wallets

log = logging.getLogger(__name__)

# Rough bluechip mint set for risk classification of quote output.
_BLUECHIP_MINTS = {
    "So11111111111111111111111111111111111111112",  # SOL (wrapped)
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",  # mSOL
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",  # JitoSOL
}


class JupiterAdapter(VenueAdapter):
    """Jupiter Meta-Aggregator venue adapter."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._rpc = SolanaRpcClient(
            settings.solana_rpc_url, settings.solana_commitment
        )
        self._wallets = build_wallets(settings)
        self._validator = TransactionValidator()
        self._api_base = settings.jupiter_api_base.rstrip("/")
        self._api_key = settings.jupiter_api_key or None
        self._last_health: Dict[str, Any] = {}

    # -- Identity ----------------------------------------------------

    @property
    def name(self) -> str:
        return "jupiter"

    @property
    def venue_type(self) -> VenueType:
        return VenueType.SOLANA_JUPITER

    @property
    def trading_wallet(self) -> SolanaWallet:
        return self._wallets[WalletTier.TRADING]

    # -- HTTP helpers ------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def _get_json(self, url: str) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers()) as resp:
                if resp.status != 200:
                    raise ExecutionDisabledError(
                        f"jupiter GET {url.split('?')[0]} -> HTTP {resp.status}"
                    )
                return await resp.json()

    async def _post_json(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=body, headers=self._headers()
            ) as resp:
                if resp.status != 200:
                    raise ExecutionDisabledError(
                        f"jupiter POST {url} -> HTTP {resp.status}"
                    )
                return await resp.json()

    # -- Quote (read-only market discovery) ----------------------------

    @staticmethod
    def _risk_class_for(mint: str) -> RiskClass:
        if mint in _BLUECHIP_MINTS:
            return RiskClass.BLUECHIP
        return RiskClass.ALT

    async def quote(
        self, asset_in: str, asset_out: str, amount: float
    ) -> Optional[Quote]:
        """Best-effort indicative quote via the public v6 /quote endpoint.

        amount is in RAW units of the input mint (lamports for SOL).
        Returns None when the route is unavailable - never raises, so the
        scanner treats Jupiter as an optional venue.
        """
        amount = int(amount)
        if amount <= 0:
            return None
        url = (
            f"{self._api_base}/quote?inputMint={asset_in}"
            f"&outputMint={asset_out}&amount={amount}"
            f"&slippageBps={self._settings.solana_slippage_bps}"
        )
        try:
            payload = await self._get_json(url)
        except ExecutionDisabledError as exc:
            log.debug("jupiter quote unavailable: %s", exc)
            return None
        out_amount = float(payload.get("outAmount") or 0.0)
        if out_amount <= 0.0:
            return None
        impact_pct = float(payload.get("priceImpactPct") or 0.0)
        fees = payload.get("fees")
        network_cost = 0.0
        if isinstance(fees, list) and fees:
            network_cost = float(fees[0].get("amount") or 0.0)
        return Quote(
            venue=self.name,
            venue_type=self.venue_type,
            asset_in=asset_in,
            asset_out=asset_out,
            in_amount=float(amount),
            out_amount=out_amount,
            price_impact_bps=round(impact_pct * 100.0, 2),
            network_cost=network_cost,
            risk_class=self._risk_class_for(asset_out),
            raw=payload,
        )
# -- Execute (fail-closed) ------------------------------------------

    @staticmethod
    def _parse_swap_symbol(
        symbol: str, metadata: Dict[str, Any]
    ) -> tuple[str, str]:
        """Resolve (input_mint, output_mint) from the intent.

        Convention: symbol holds INPUT_MINT>OUTPUT_MINT, or both mints are
        provided explicitly in metadata. Missing mint info is a hard error -
        the adapter refuses to guess addresses.
        """
        if metadata.get("input_mint") and metadata.get("output_mint"):
            return metadata["input_mint"], metadata["output_mint"]
        if ">" in symbol:
            input_mint, _, output_mint = symbol.partition(">")
            if input_mint and output_mint:
                return input_mint, output_mint
        raise ValueError(
            f"jupiter execute requires mints: pass symbol='IN>OUT' or "
            f"metadata['input_mint'/'output_mint'] (got {symbol!r})"
        )

    def _signing_blockers(self) -> str:
        blockers = self.trading_wallet.signing_blockers()
        unmet = [name for name, blocked in blockers.items() if blocked]
        if not unmet:
            return ""
        return "; ".join(unmet) or "signing not available"

    async def execute(self, intent: OrderIntent, permit: ExecutionPermit) -> Fill:
        """Execute a risk-approved Jupiter swap end to end.

        Order: quote -> build -> PROVE -> sign -> submit. Any failure
        short-circuits to ExecutionDisabledError. Nothing reaches the chain
        without the TransactionValidator approving mints, amounts, priority
        fee and minimum expected output.
        """
        if not self.trading_wallet.can_sign:
            raise ExecutionDisabledError(
                "jupiter signing disabled - blockers: "
                + self._signing_blockers()
            )
        if permit.is_expired:
            raise ExecutionDisabledError("jupiter execute: permit expired")
        input_mint, output_mint = self._parse_swap_symbol(
            intent.symbol, intent.metadata
        )
        amount_raw = int(intent.size)
        if amount_raw <= 0:
            raise ExecutionDisabledError("jupiter execute: size must be > 0")

        # 1. Re-quote so validation matches the freshest order.
        url = (
            f"{self._api_base}/quote?inputMint={input_mint}"
            f"&outputMint={output_mint}&amount={amount_raw}"
            f"&slippageBps={self._settings.solana_slippage_bps}"
        )
        payload = await self._get_json(url)
        requested_out = float(payload.get("outAmount") or 0.0)
        if requested_out <= 0.0:
            raise ExecutionDisabledError(
                "jupiter execute: quote returned no output"
            )
        min_out_raw = math.floor(
            requested_out * (1.0 - self._settings.solana_slippage_bps / 10_000.0)
        )

        # 2. Build the unsigned transaction.
        order = await self._post_json(
            f"{self._api_base}/swap/v2/order",
            {
                "quoteRequest": {
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount_raw),
                    "slippageBps": self._settings.solana_slippage_bps,
                    "restrictIntermediateTokens": True,
                },
                "userPublicKey": self.trading_wallet.pubkey,
            },
        )
        swap_tx_b64 = order.get("swapTransaction")
        request_id = order.get("requestId", "")
        last_valid_height = order.get("lastValidBlockHeight", 0)
        if not swap_tx_b64:
            raise ExecutionDisabledError(
                "jupiter order returned no swapTransaction"
            )

        # 3. PROVE the transaction expresses exactly this swap.
        expected = ExpectedSwap(
            taker_pubkey=self.trading_wallet.pubkey,
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount_raw=amount_raw,
            min_out_raw=min_out_raw,
            max_priority_fee_lamports=(
                self._settings.solana_max_priority_fee_lamports
            ),
        )
        result = self._validator.validate(swap_tx_b64, expected)
        if not result.approved:
            raise ExecutionDisabledError(
                "jupiter transaction validation FAILED: "
                + "; ".join(result.reject_reasons())
            )

        # 4. Sign locally, submit to Jupiter landing.
        signed_sig = self.trading_wallet.sign_transaction(
            base64.b64decode(swap_tx_b64)
        )
        confirmation = await self._post_json(
            f"{self._api_base}/swap/v2/execute",
            {
                "swapTransaction": swap_tx_b64,
                "requestId": request_id,
                "lastValidBlockHeight": last_valid_height,
            },
        )
        tx_sig = confirmation.get("transaction", "")
        confirmed = bool(confirmation.get("confirmed", False))
        return Fill(
            venue=self.name,
            symbol=f"{input_mint}>{output_mint}",
            side=intent.side,
            size=requested_out,
            price=requested_out / amount_raw if amount_raw else 0.0,
            fee=0.0,  # Jupiter fees reflected in the route's out_amount
            cost=float(intent.size),
            proceeds=requested_out,
            order_id=request_id or tx_sig,
            timestamp=time.time(),
            status="filled" if confirmed else "partial",
            slippage_bps=self._settings.solana_slippage_bps,
            metadata={
                "signature": tx_sig,
                "signed_sig": signed_sig.hex(),
                "min_out_raw": min_out_raw,
                "confirmed": confirmed,
                "request_id": request_id,
                "block_height": last_valid_height,
            },
        )
# -- Health / balances ------------------------------------------------

    async def health(self) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {
            "venue": self.name,
            "ok": False,
            "can_sign": self.trading_wallet.can_sign,
            "signing_blockers": self._signing_blockers(),
        }
        rpc_health = await self._rpc.health()
        snapshot["rpc"] = rpc_health
        try:
            await self._get_json(f"{self._api_base}/quote")
            snapshot["api_reachable"] = True
        except ExecutionDisabledError:
            snapshot["api_reachable"] = False
        snapshot["ok"] = bool(
            rpc_health.get("ok") and snapshot.get("api_reachable")
        )
        self._last_health = snapshot
        return snapshot

    async def balances(self) -> Dict[str, float]:
        if not self.trading_wallet.is_configured:
            return {}
        sol = await self.trading_wallet.balance_sol(self._rpc)
        return {"SOL": sol}

    async def close(self) -> None:
        await self._rpc.close()
